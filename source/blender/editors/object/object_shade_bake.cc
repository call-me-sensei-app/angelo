/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup edobj
 *
 * Bake Auto Smooth: write the result of a Smooth by Angle (or legacy Auto Smooth) modifier into
 * the mesh once, and remove the modifier from every object that uses the mesh.
 *
 * Without modifiers, objects that share a mesh share one evaluated mesh. With any modifier, each
 * object evaluates its own copy, with its own sharp-edge array, normals and GPU buffers, so 10,000
 * objects sharing one mesh with one Smooth by Angle modifier each evaluate 10,000 meshes (Blender
 * #153195, #126391). Apply refuses a mesh with several users, so this is the way to apply it.
 *
 * The bake is exact or refused: the modifier must be the first of every user, enabled for the
 * viewport and render, with the same settings everywhere, unanimated (node groups too), on an
 * editable mesh without shape keys or parent deformation, and its evaluated result must differ
 * from the mesh only in edge and face sharpness. Those two attributes are then copied, so the
 * shading is unchanged.
 */

#include <algorithm>
#include <cstring>
#include <fmt/format.h>

#include "BLI_listbase_iterator.hh"
#include "BLI_map.hh"
#include "BLI_string.hh"
#include "BLI_vector.hh"
#include "BLI_vector_set.hh"

#include "BLT_translation.hh"

#include "DNA_anim_types.h"
#include "DNA_layer_types.h"
#include "DNA_mesh_types.h"
#include "DNA_modifier_types.h"
#include "DNA_node_types.h"
#include "DNA_object_types.h"

#include "BKE_anim_data.hh"
#include "BKE_anonymous_attribute_id.hh"
#include "BKE_attribute.hh"
#include "BKE_context.hh"
#include "BKE_fcurve.hh"
#include "BKE_geometry_set.hh"
#include "BKE_idprop.hh"
#include "BKE_layer.hh"
#include "BKE_lib_id.hh"
#include "BKE_main.hh"
#include "BKE_mesh.hh"
#include "BKE_mesh_legacy_convert.hh"
#include "BKE_modifier.hh"
#include "BKE_node_runtime.hh"
#include "BKE_object.hh"
#include "BKE_report.hh"

#include "NOD_dependencies.hh"

#include "DEG_depsgraph.hh"
#include "DEG_depsgraph_build.hh"
#include "DEG_depsgraph_query.hh"

#include "ED_object.hh"

#include "RNA_access.hh"

#include "WM_api.hh"
#include "WM_types.hh"

#include "object_intern.hh"

namespace blender::ed::object {

const char *AUTO_SMOOTH_MULTI_USER_APPLY_MESSAGE = N_(
    "Modifiers cannot be applied to multi-user data. Object > Bake Auto Smooth applies Smooth by "
    "Angle to every object that shares the mesh");

bool is_auto_smooth_modifier(const ModifierData &md)
{
  if (is_smooth_by_angle_modifier(md)) {
    return true;
  }
  if (md.type != eModifierType_Nodes) {
    return false;
  }
  const NodesModifierData &nmd = reinterpret_cast<const NodesModifierData &>(md);
  return nmd.node_group && !ID_MISSING(nmd.node_group) &&
         bke::is_auto_smooth_node_tree(*nmd.node_group);
}

static bool modifier_inputs_are_animated(Object &ob, const ModifierData &md)
{
  char name_esc[sizeof(md.name) * 2];
  BLI_str_escape(name_esc, md.name, sizeof(name_esc));
  const std::string prefix = fmt::format("modifiers[\"{}\"]", name_esc);
  bool animated = false;
  BKE_fcurves_id_cb(&ob.id, [&](ID * /*id*/, FCurve *fcurve) {
    if (fcurve->rna_path().startswith(prefix)) {
      animated = true;
    }
  });
  return animated;
}

/** The modifier's node group and every group nested in it, each once. */
static VectorSet<const bNodeTree *> node_trees_in(const bNodeTree &root)
{
  VectorSet<const bNodeTree *> trees = {&root};
  for (int i = 0; i < trees.size(); i++) {
    for (const bNode *node : trees[i]->all_nodes()) {
      if (node->is_group() && node->id) {
        trees.add(reinterpret_cast<const bNodeTree *>(node->id));
      }
    }
  }
  return trees;
}

/**
 * The modifier's own inputs (object animation and drivers), or anything inside its node group or
 * a nested one (the group's action, NLA or drivers can change a socket value between frames).
 */
static bool modifier_is_animated(Object &ob, const NodesModifierData &nmd)
{
  for (const bNodeTree *tree : node_trees_in(*nmd.node_group)) {
    if (BKE_animdata_id_is_animated(&tree->id)) {
      return true;
    }
  }
  return modifier_inputs_are_animated(ob, nmd.modifier);
}

/**
 * Whether the node group's result can depend only on its input mesh: no time, transform, camera,
 * render settings or other data-block, read with the dependency analysis the depsgraph uses, and
 * no evaluation-mode switch. A copy of Smooth by Angle appended before 5.0 is editable, so this is
 * what tells an edited copy apart.
 */
static bool depends_only_on_its_mesh(const NodesModifierData &nmd)
{
  const nodes::EvalDependencies deps = nodes::gather_eval_dependencies_recursive(*nmd.node_group);
  if (!deps.ids.is_empty() || deps.needs_own_transform || deps.needs_active_camera ||
      deps.needs_scene_render_params || deps.time_dependent)
  {
    return false;
  }
  bool references_id = false;
  IDP_foreach_property(nmd.modifier.system_properties, IDP_TYPE_FILTER_ID, [&](IDProperty *prop) {
    references_id |= IDP_ID_get(prop) != nullptr;
  });
  if (references_id) {
    return false;
  }
  /* Is Viewport gives the viewport and the render different results. */
  for (const bNodeTree *tree : node_trees_in(*nmd.node_group)) {
    for (const bNode *node : tree->all_nodes()) {
      if (STREQ(node->idname, "GeometryNodeIsViewport")) {
        return false;
      }
    }
  }
  return true;
}

/** The object's bakeable Auto Smooth modifier, or a report-ready reason it has none. */
static NodesModifierData *bakeable_modifier(Object &ob, std::string &r_reason)
{
  ModifierData *md = ob.modifiers.first();
  if (!md || !is_auto_smooth_modifier(*md)) {
    r_reason = fmt::format(
        fmt::runtime(RPT_("\"{object}\" does not have Smooth by Angle as its first modifier")),
        fmt::arg("object", BKE_id_name(ob.id)));
    return nullptr;
  }
  if (ob.mode != OB_MODE_OBJECT) {
    /* Leaving a paint or sculpt mode can write the object's data back over the baked mesh. */
    r_reason = fmt::format(fmt::runtime(RPT_("\"{object}\" is not in Object Mode")),
                           fmt::arg("object", BKE_id_name(ob.id)));
    return nullptr;
  }
  /* Edit Mode too: a modifier hidden there shows the mesh without it, which a bake would change.
   */
  const ModifierMode enabled = eModifierMode_Realtime | eModifierMode_Render |
                               eModifierMode_Editmode;
  if ((md->mode & enabled) != enabled) {
    r_reason = fmt::format(
        fmt::runtime(RPT_("Smooth by Angle on \"{object}\" is not enabled in the viewport, render "
                          "and Edit Mode")),
        fmt::arg("object", BKE_id_name(ob.id)));
    return nullptr;
  }
  /* Before the dependency check, which in 5.3 also counts an animated tree as time: this reason
   * is the precise one. */
  if (modifier_is_animated(ob, *reinterpret_cast<NodesModifierData *>(md))) {
    r_reason = fmt::format(fmt::runtime(RPT_("Smooth by Angle on \"{object}\" is animated")),
                           fmt::arg("object", BKE_id_name(ob.id)));
    return nullptr;
  }
  if (!depends_only_on_its_mesh(*reinterpret_cast<NodesModifierData *>(md))) {
    r_reason = fmt::format(
        fmt::runtime(RPT_("Smooth by Angle on \"{object}\" depends on more than its mesh")),
        fmt::arg("object", BKE_id_name(ob.id)));
    return nullptr;
  }
  VirtualModifierData virtual_modifier_data;
  if (BKE_modifiers_get_virtual_modifierlist(&ob, &virtual_modifier_data) != md) {
    r_reason = fmt::format(
        fmt::runtime(RPT_("\"{object}\" is deformed by its parent before Smooth by Angle")),
        fmt::arg("object", BKE_id_name(ob.id)));
    return nullptr;
  }
  return reinterpret_cast<NodesModifierData *>(md);
}

static bool same_settings(const NodesModifierData &a, const NodesModifierData &b)
{
  /* Strict: a missing input group on one side is a difference. */
  return a.node_group == b.node_group &&
         IDP_EqualsProperties_ex(a.modifier.system_properties, b.modifier.system_properties, true);
}

/** Everything but edge and face sharpness is the same, so copying those two reproduces it. */
static bool differs_only_in_sharpness(const Mesh &mesh, const Mesh &result)
{
  if (mesh.verts_num != result.verts_num || mesh.edges_num != result.edges_num ||
      mesh.faces_num != result.faces_num || mesh.corners_num != result.corners_num)
  {
    return false;
  }
  if (mesh.face_offsets() != result.face_offsets()) {
    return false;
  }
  /* Material slots live outside the attributes. */
  if (mesh.totcol != result.totcol ||
      !std::equal(mesh.mat, mesh.mat + mesh.totcol, result.mat, result.mat + result.totcol))
  {
    return false;
  }
  /* Every attribute but the two copied (positions and topology included) must be byte-identical,
   * and none may be added or removed. Anonymous attributes are evaluation intermediates. */
  const auto compared = [](const StringRef name) {
    return !ELEM(name, "sharp_edge", "sharp_face") && !bke::attribute_name_is_anonymous(name);
  };
  const bke::AttributeAccessor src = mesh.attributes();
  int src_count = 0;
  src.foreach_attribute([&](const bke::AttributeIter &iter) { src_count += compared(iter.name); });
  int result_count = 0;
  bool same = true;
  result.attributes().foreach_attribute([&](const bke::AttributeIter &iter) {
    if (!compared(iter.name)) {
      return;
    }
    result_count++;
    const std::optional<bke::AttributeMetaData> meta = src.lookup_meta_data(iter.name);
    if (!meta || meta->domain != iter.domain || meta->data_type != iter.data_type) {
      same = false;
      iter.stop();
      return;
    }
    const GVArraySpan a = *src.lookup(iter.name);
    const GVArraySpan b = *iter.get();
    const CPPType &type = a.type();
    if (!type.is_trivial || a.size() != b.size() ||
        (a.data() != b.data() && memcmp(a.data(), b.data(), type.size * a.size()) != 0))
    {
      same = false;
      iter.stop();
    }
  });
  return same && src_count == result_count;
}

/** The evaluated modifier `md_eval` of `ob_eval` run on a copy of `mesh`, as Apply runs it. */
static Mesh *evaluate_on_copy(Depsgraph &depsgraph,
                              Object &ob_eval,
                              ModifierData &md_eval,
                              const Mesh &mesh)
{
  const ModifierTypeInfo *mti = BKE_modifier_get_info(md_eval.type);
  if (!mti->modify_geometry_set) {
    return nullptr;
  }
  const ModifierEvalContext mectx = {&depsgraph, &ob_eval, MOD_APPLY_TO_ORIGINAL};
  Mesh *mesh_temp = reinterpret_cast<Mesh *>(
      BKE_id_copy_ex(nullptr, &mesh.id, nullptr, LIB_ID_COPY_LOCALIZE));
  bke::GeometrySet geometry_set = bke::GeometrySet::from_mesh(mesh_temp,
                                                              bke::GeometryOwnershipType::Owned);
  mti->modify_geometry_set(&md_eval, &mectx, &geometry_set);
  /* Anything but a mesh (instances, curves, points, volumes, Grease Pencil) cannot be baked. */
  if (!geometry_set.has_mesh() || geometry_set.has_instances() || geometry_set.has_curves() ||
      geometry_set.has_pointcloud() || geometry_set.has_volume() ||
      geometry_set.has_grease_pencil())
  {
    return nullptr;
  }
  return geometry_set.get_component_for_write<bke::MeshComponent>().release();
}

/**
 * The modifier's result on `mesh`, null when it cannot be evaluated. Uses a user the viewport
 * evaluated; when every user is hidden, a local graph that evaluates hidden objects, as Apply.
 */
static Mesh *evaluate_modifier(Main &bmain,
                               Scene &scene,
                               ViewLayer &view_layer,
                               Depsgraph &depsgraph,
                               const Span<Object *> users,
                               const Span<NodesModifierData *> modifiers,
                               const Mesh &mesh)
{
  /* Each user's modifier has its own identifier within its object. */
  for (const int i : users.index_range()) {
    Object *ob_eval = DEG_get_evaluated(&depsgraph, users[i]);
    if (ob_eval && ob_eval != users[i] && (ob_eval->base_flag & BASE_ENABLED_VIEWPORT)) {
      if (ModifierData *md_eval = BKE_modifiers_findby_persistent_uid(
              ob_eval, modifiers[i]->modifier.persistent_uid))
      {
        return evaluate_on_copy(depsgraph, *ob_eval, *md_eval, mesh);
      }
    }
  }
  const int uid = modifiers.first()->modifier.persistent_uid;
  Depsgraph *local_depsgraph = DEG_graph_new(&bmain, &scene, &view_layer, DAG_EVAL_VIEWPORT);
  DEG_disable_visibility_optimization(local_depsgraph);
  DEG_graph_build_from_ids(local_depsgraph, {&users.first()->id});
  DEG_evaluate_on_refresh(local_depsgraph);
  Mesh *result = nullptr;
  Object *ob_eval = DEG_get_evaluated(local_depsgraph, users.first());
  if (ModifierData *md_eval = ob_eval ? BKE_modifiers_findby_persistent_uid(ob_eval, uid) :
                                        nullptr)
  {
    result = evaluate_on_copy(*local_depsgraph, *ob_eval, *md_eval, mesh);
  }
  DEG_graph_free(local_depsgraph);
  return result;
}

static void copy_sharpness(const Mesh &result, Mesh &mesh)
{
  const bke::AttributeAccessor src = result.attributes();
  bke::MutableAttributeAccessor dst = mesh.attributes_for_write();
  for (const auto [name, domain] : {std::pair{"sharp_edge", bke::AttrDomain::Edge},
                                    std::pair{"sharp_face", bke::AttrDomain::Face}})
  {
    dst.remove(name);
    if (const VArray<bool> values = *src.lookup<bool>(name, domain)) {
      dst.add<bool>(name, domain, bke::AttributeInitVArray(values));
    }
  }
  mesh.tag_sharpness_changed();
}

/** Bakes `mesh` for all its `users`, or returns a report-ready reason it cannot. */
static std::string bake_mesh(Main &bmain,
                             Scene &scene,
                             ViewLayer &view_layer,
                             Depsgraph &depsgraph,
                             ReportList *reports,
                             Mesh &mesh,
                             const Span<Object *> users,
                             int &r_removed)
{
  if (!BKE_id_is_editable(&bmain, &mesh.id) || ID_IS_OVERRIDE_LIBRARY(&mesh.id)) {
    return fmt::format(fmt::runtime(RPT_("Mesh \"{mesh}\" is linked and cannot be edited")),
                       fmt::arg("mesh", BKE_id_name(mesh.id)));
  }
  if (mesh.key) {
    return fmt::format(
        fmt::runtime(RPT_("Mesh \"{mesh}\" has shape keys, which can change its angles")),
        fmt::arg("mesh", BKE_id_name(mesh.id)));
  }
  if (mesh.runtime->edit_mesh) {
    return fmt::format(fmt::runtime(RPT_("Mesh \"{mesh}\" is in Edit Mode")),
                       fmt::arg("mesh", BKE_id_name(mesh.id)));
  }
  Vector<NodesModifierData *> modifiers;
  for (Object *ob : users) {
    if (!BKE_id_is_editable(&bmain, &ob->id) || ID_IS_OVERRIDE_LIBRARY(&ob->id)) {
      return fmt::format(fmt::runtime(RPT_("\"{object}\" is linked and cannot be edited")),
                         fmt::arg("object", BKE_id_name(ob->id)));
    }
    std::string reason;
    NodesModifierData *nmd = bakeable_modifier(*ob, reason);
    if (!nmd) {
      return reason;
    }
    if (!modifiers.is_empty() && !same_settings(*modifiers.first(), *nmd)) {
      return fmt::format(
          fmt::runtime(
              RPT_("Objects using mesh \"{mesh}\" have different Smooth by Angle settings")),
          fmt::arg("mesh", BKE_id_name(mesh.id)));
    }
    modifiers.append(nmd);
  }

  Mesh *result = evaluate_modifier(bmain, scene, view_layer, depsgraph, users, modifiers, mesh);
  const bool exact = result && differs_only_in_sharpness(mesh, *result);
  if (exact) {
    copy_sharpness(*result, mesh);
  }
  if (result) {
    BKE_id_free(nullptr, result);
  }
  if (!exact) {
    return fmt::format(
        fmt::runtime(RPT_("Smooth by Angle changes more than the shading of mesh \"{mesh}\"")),
        fmt::arg("mesh", BKE_id_name(mesh.id)));
  }

  for (const int i : users.index_range()) {
    modifier_remove(reports, &bmain, &scene, users[i], &modifiers[i]->modifier);
  }
  r_removed += users.size();
  DEG_id_tag_update(&mesh.id, ID_RECALC_GEOMETRY);
  BKE_mesh_batch_cache_dirty_tag(&mesh, BKE_MESH_BATCH_DIRTY_ALL);
  WM_main_add_notifier(NC_GEOM | ND_DATA, &mesh.id);
  return {};
}

static wmOperatorStatus shade_auto_smooth_bake_exec(bContext *C, wmOperator *op)
{
  Main &bmain = *CTX_data_main(C);
  Scene &scene = *CTX_data_scene(C);
  ViewLayer &view_layer = *CTX_data_view_layer(C);
  Depsgraph &depsgraph = *CTX_data_ensure_evaluated_depsgraph(C);

  /* The meshes of the selection, in selection order, and every object that uses each of them:
   * baking writes the mesh, so its unselected users must lose their modifier too. */
  Vector<PointerRNA> selected;
  CTX_data_selected_editable_objects(C, &selected);
  VectorSet<Mesh *> meshes;
  for (const PointerRNA &ptr : selected) {
    Object *ob = static_cast<Object *>(ptr.data);
    if (ob->type == OB_MESH && ob->data) {
      meshes.add(id_cast<Mesh *>(ob->data));
    }
  }
  Map<Mesh *, Vector<Object *>> users;
  for (Object &ob : bmain.objects) {
    if (ob.type == OB_MESH && meshes.contains(id_cast<Mesh *>(ob.data))) {
      users.lookup_or_add_default(id_cast<Mesh *>(ob.data)).append(&ob);
    }
  }

  int baked = 0;
  int removed = 0;
  int skipped = 0;
  std::string first_reason;
  for (Mesh *mesh : meshes) {
    const std::string reason = bake_mesh(
        bmain, scene, view_layer, depsgraph, op->reports, *mesh, users.lookup(mesh), removed);
    if (reason.empty()) {
      baked++;
      continue;
    }
    if (skipped++ == 0) {
      first_reason = reason;
    }
  }

  /* The texts are translated here, so they are reported as they are. */
  if (baked > 0) {
    const std::string message = fmt::format(
        fmt::runtime(RPT_("Meshes baked: {baked}. Modifiers removed: {removed}")),
        fmt::arg("baked", baked),
        fmt::arg("removed", removed));
    BKE_report(op->reports, RPT_INFO, message.c_str());
  }
  if (skipped > 0) {
    const std::string message = fmt::format(
        fmt::runtime(RPT_("Meshes skipped: {skipped}. {reason}")),
        fmt::arg("skipped", skipped),
        fmt::arg("reason", first_reason));
    BKE_report(op->reports, baked > 0 ? RPT_WARNING : RPT_ERROR, message.c_str());
  }
  else if (meshes.is_empty()) {
    BKE_report(op->reports, RPT_ERROR, "No selected mesh objects");
  }
  if (baked > 0) {
    DEG_relations_tag_update(&bmain);
    WM_event_add_notifier(C, NC_OBJECT | ND_MODIFIER, nullptr);
    return OPERATOR_FINISHED;
  }
  return OPERATOR_CANCELLED;
}

static bool shade_auto_smooth_bake_poll(bContext *C)
{
  const Object *ob = CTX_data_active_object(C);
  if (ob && (ob->mode & (OB_MODE_EDIT | OB_MODE_SCULPT))) {
    CTX_wm_operator_poll_msg_set(C, "Not available in Edit Mode or Sculpt Mode");
    return false;
  }
  return true;
}

void OBJECT_OT_shade_auto_smooth_bake(wmOperatorType *ot)
{
  ot->name = "Bake Auto Smooth";
  ot->description =
      "Write the Smooth by Angle result into the selected objects' meshes and remove the modifier "
      "from every object that uses them, so a mesh shared by many objects is evaluated once. "
      "The angle no longer updates when the mesh is edited";
  ot->idname = "OBJECT_OT_shade_auto_smooth_bake";

  ot->exec = shade_auto_smooth_bake_exec;
  ot->poll = shade_auto_smooth_bake_poll;

  ot->flag = OPTYPE_REGISTER | OPTYPE_UNDO;
}

}  // namespace blender::ed::object
