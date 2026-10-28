/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 *
 * The world box of what an evaluated depsgraph shows: a live part's (`_bpy._angelo_live.bounds`, framing) and a
 * worker's own size of its part (`depsgraph_size`, for the lead's `angelo_workers_status`). Geometry only, as
 * evaluated, so a curve's bevel and extrusion, text and geometry nodes count (break-it P1-5 L1, L7). Each visible
 * object and instance once, in C++.
 *
 * Each object's box is the geometry it owns, never the cached object box: that one fell back to curve data padded by
 * each point's radius (1 by default), so a planter's thin poly-curve leaf ribs (6 cm of geometry) read 2 m wide and
 * the part 2.4 m instead of 0.6 m: its tile framed far out and the lead was told a size four times too big (Sol live
 * trial, e2e 03020c0ecca, 2026-10-25; fixed at its root in BKE too).
 *
 * `instance_bounds` gives each object that makes instances the world box of what they show, read the same way: an
 * object's own facts (`Object.dimensions`, its bound box) leave instances out, so a Geometry Nodes instancer read 0
 * and a curve leaving only instances its control points padded by their radius (break-it REVIEW-BOUNDS M3).
 */

#include <cstdint>
#include <optional>

#include "BLI_bounds_types.hh"
#include "BLI_listbase_iterator.hh"
#include "BLI_map.hh"
#include "BLI_math_geom.h"
#include "BLI_set.hh"
#include "BLI_math_matrix.hh"
#include "BLI_math_vector.h"
#include "BLI_utildefines.h"
#include "BLI_vector.hh"

#include "BKE_collection.hh"
#include "BKE_duplilist.hh"
#include "BKE_geometry_set.hh"
#include "BKE_geometry_set_instances.hh"
#include "BKE_instances.hh"
#include "BKE_layer.hh"
#include "BKE_volume.hh"
#include "BKE_curves.hh"
#include "BKE_curve.hh"
#include "BKE_object.hh"
#include "BKE_object_types.hh"

#include "DEG_depsgraph.hh"
#include "DEG_depsgraph_query.hh"

#include "../generic/py_capi_utils.hh"

#include "DNA_layer_types.h"
#include "DNA_modifier_types.h"
#include "DNA_object_types.h"
#include "DNA_volume_types.h"
#include "DNA_pointcloud_types.h"
#include "DNA_mesh_types.h"
#include "DNA_grease_pencil_types.h"
#include "DNA_curve_types.h"
#include "DNA_particle_types.h"

#include "bpy_angelo_live_part.hh"

namespace blender {

bool live_is_geometry(const Object *ob)
{
  /* Geometry only, as evaluated: a curve's bevel and extrusion, text, geometry nodes (break-it P1-5 L1, L7). */
  return ELEM(ob->type, OB_MESH, OB_CURVES_LEGACY, OB_SURF, OB_FONT, OB_MBALL, OB_CURVES, OB_POINTCLOUD, OB_VOLUME,
              OB_GREASE_PENCIL);
}

/** The world box of the geometry \a ob owns, merged into \a r_min / \a r_max; false when it owns none. */
bool live_merge_owned_bounds(const Object *ob, float r_min[3], float r_max[3])
{
  /* The geometry an object owns: its evaluated geometry set's bounds, none when that holds only instances (this
   * iteration visits them as objects of their own; its data, a curve's control points padded by their radius,
   * would add a cage nothing draws). An object with no geometry set (an instance's temporary object, whose data
   * was replaced) reads its data. */
  const std::optional<Bounds<float3>> bounds = ob->runtime->geometry_set_eval ?
                                                   BKE_object_evaluated_geometry_bounds(ob) :
                                                   BKE_object_boundbox_get(ob);
  if (!bounds) {
    return false;
  }
  const float4x4 &matrix = ob->object_to_world();
  for (int corner = 0; corner < 8; corner++) {
    const float3 local((corner & 1) ? bounds->max.x : bounds->min.x,
                       (corner & 2) ? bounds->max.y : bounds->min.y,
                       (corner & 4) ? bounds->max.z : bounds->min.z);
    minmax_v3v3_v3(r_min, r_max, math::transform_point(matrix, local));
  }
  return true;
}

/** Whether a Boolean with an operand collection runs at all (MOD_boolean.cc ``BMD_error_messages``): never with a
 * non-mesh object in the collection, nor an Intersect without the Exact or Manifold solver; a modifier that refuses
 * cuts nothing, so its collection's objects are no operands (REVIEW-OVERLAP-A-2 L3). */
static bool runs_with_its_collection(const BooleanModifierData &bmd)
{
  const bool intersect_unsolved = bmd.operation == eBooleanModifierOp_Intersect &&
                                  !ELEM(bmd.solver, eBooleanModifierSolver_Mesh_Arr, eBooleanModifierSolver_Manifold);
  if (intersect_unsolved) {
    return false;
  }
  FOREACH_COLLECTION_OBJECT_RECURSIVE_BEGIN (bmd.collection, operand) {
    if (operand->type != OB_MESH) {
      return false;
    }
  }
  FOREACH_COLLECTION_OBJECT_RECURSIVE_END;
  return true;
}

/** The objects every enabled Boolean modifier of \a depsgraph cuts with, by original session uid: helpers, never part
 * of what a part shows (the maintainer, overlap scope a, 2026-10-25). Read as MOD_boolean reads them: an Object
 * operand that is a mesh, or each mesh of the operand collection other than the object cut (MOD_boolean.cc
 * `is_disabled`, `ob != ctx->object`); a modifier off in both the viewport and renders cuts nothing. An instanced
 * collection's objects count too, so a kit's cutter is a helper where it is instanced (REVIEW-OVERLAP-A M1, L1, L2). */
static Set<uint32_t> boolean_operands(Depsgraph *depsgraph)
{
  Set<uint32_t> operands;
  DEGObjectIterSettings settings = {nullptr};
  settings.depsgraph = depsgraph;
  /* A cutter is usually hidden: every object counts. */
  settings.flags = DEG_ITER_OBJECT_FLAG_LINKED_DIRECTLY | DEG_ITER_OBJECT_FLAG_LINKED_INDIRECTLY;
  DEG_OBJECT_ITER_BEGIN (&settings, ob) {
    const Object *cut = DEG_get_original(ob);
    for (ModifierData &md : ob->modifiers) {
      if (md.type != eModifierType_Boolean || !(md.mode & (eModifierMode_Realtime | eModifierMode_Render))) {
        continue;
      }
      const BooleanModifierData &bmd = reinterpret_cast<const BooleanModifierData &>(md);
      if (bmd.flag & eBooleanModifierFlag_Object) {
        if (bmd.object != nullptr && bmd.object->type == OB_MESH) {
          operands.add(DEG_get_original(bmd.object)->id.session_uid);
        }
      }
      else if ((bmd.flag & eBooleanModifierFlag_Collection) && bmd.collection != nullptr &&
               runs_with_its_collection(bmd))
      {
        FOREACH_COLLECTION_OBJECT_RECURSIVE_BEGIN (bmd.collection, operand) {
          const Object *original = DEG_get_original(operand);
          if (original != cut) {
            operands.add(original->id.session_uid);
          }
        }
        FOREACH_COLLECTION_OBJECT_RECURSIVE_END;
      }
    }
  }
  DEG_OBJECT_ITER_END;
  return operands;
}

/** Whether \a depsgraph's view layer shows \a original: a base enabled for its mode as the depsgraph's builder pulls
 * bases in (deg_builder.cc, its collections' flags and the object's own restriction, as #BKE_base_eval_flags applies
 * them). An object in the depsgraph only as a dependency (an excluded collection's member a collection instance uses,
 * an object disabled in viewports kept as an Object Info source) has no flags set there and shows nothing of its own
 * (REVIEW-INSTANCES-LATENCY M1, round 2 M1-r). */
bool live_shown_here(ViewLayer *view_layer, Object *original, const int mode)
{
  if (!BKE_view_layer_is_synced(*view_layer)) {
    return true; /* Never resynchronized by a read: shown, as before its last change (REVIEW-INSTANCES-LATENCY). */
  }
  const Base *base = BKE_view_layer_base_find(view_layer, original);
  const bool render = mode == DAG_EVAL_RENDER;
  const int enabled = render ? BASE_ENABLED_RENDER : BASE_ENABLED_VIEWPORT;
  return base != nullptr && (base->flag_from_collection & enabled) != 0 &&
         !(original->visibility_flag & (render ? OB_HIDE_RENDER : OB_HIDE_VIEWPORT));
}

/** A collection's members by original session uid: all of them, and those Blender's render picks (not hidden from
 * renders by themselves or by a child collection: FOREACH_COLLECTION_VISIBLE_OBJECT_RECURSIVE in render mode). */
struct Members {
  Set<uint32_t> all, rendered;
};

static const Members &members_of(Collection *collection, Map<const Collection *, Members> &known)
{
  return known.lookup_or_add_cb(collection, [&]() {
    Members members;
    FOREACH_COLLECTION_OBJECT_RECURSIVE_BEGIN (collection, ob) {
      members.all.add(DEG_get_original(ob)->id.session_uid);
    }
    FOREACH_COLLECTION_OBJECT_RECURSIVE_END;
    FOREACH_COLLECTION_VISIBLE_OBJECT_RECURSIVE_BEGIN (collection, ob, DAG_EVAL_RENDER) {
      members.rendered.add(DEG_get_original(ob)->id.session_uid);
    }
    FOREACH_COLLECTION_VISIBLE_OBJECT_RECURSIVE_END;
    return members;
  });
}

/** Whether Blender's render leaves out the object the instance \a dob shows (\a parent made it), its instancer aside.
 * A collection's members are picked by their render visibility (a collection instance, a Geometry Nodes Collection
 * reference, a particle collection, an instancer's children: object_dupli.cc), while an object reference (Object
 * Info, a particle object) renders its object whatever that object's own setting: hiding the prototype is how such
 * instancing is set up (REVIEW-OVERLAP-A H1). A member whose collection cannot be told (a deeper level, an instancer's
 * children) is decided by its own setting. */
static bool render_leaves_out(const DupliObject &dob,
                              const Object *instancer,
                              Collection *instancer_picked,
                              const bool top,
                              ViewLayer *view_layer,
                              Map<const Collection *, Members> &known,
                              Collection **r_picked)
{
  const uint32_t uid = DEG_get_original(dob.ob)->id.session_uid;
  Collection *referenced = nullptr;
  if (const bke::GeometrySet *geometry = dob.instance_data[0]) {
    const bke::Instances *instances = geometry->get_instances();
    const int index = dob.instance_idx[0];
    if (instances != nullptr && index >= 0 && index < instances->instances_num()) {
      const bke::InstanceReference &reference = instances->references()[instances->reference_handles()[index]];
      if (reference.type() == bke::InstanceReference::Type::Object &&
          DEG_get_original(&reference.object())->id.session_uid == uid)
      {
        return false;
      }
      if (reference.type() == bke::InstanceReference::Type::Collection) {
        referenced = &reference.collection();
      }
    }
  }
  const bool particle_collection = dob.type == OB_DUPLIPARTS && dob.particle_system != nullptr &&
                                   dob.particle_system->part != nullptr &&
                                   dob.particle_system->part->ren_as == PART_DRAW_GR;
  /* The collections that may have picked it, innermost first: its instancer's own (a collection instance), a nodes
   * Collection reference (whose geometry may be a level further out), a particle system's. It is decided by the first
   * that holds it (REVIEW-OVERLAP-A-2 round 2: a kit placed by Collection Info read the outer reference only). */
  /* An instancer's own children (vertex or face instancing) inside an instanced collection come from that
   * collection, by its render members (object_dupli.cc ``make_child_duplis``, REVIEW-OVERLAP-A-2 R2-L2). The type is
   * the last generator on Blender's stack, so the instancer's own instancing is read too (round 3 L3). */
  const bool children = ELEM(dob.type, OB_DUPLIVERTS, OB_DUPLIFACES);
  Collection *const candidates[] = {
      dob.type == OB_DUPLICOLLECTION && (instancer->transflag & OB_DUPLICOLLECTION) ? instancer->instance_collection :
                                                                                      nullptr,
      referenced,
      particle_collection ? dob.particle_system->part->instance_collection : nullptr,
      children ? instancer_picked : nullptr};
  Collection *collection = nullptr;
  for (Collection *candidate : candidates) {
    if (candidate == nullptr) {
      continue;
    }
    collection = candidate;
    const Members &members = members_of(candidate, known);
    if (members.all.contains(uid)) {
      *r_picked = candidate;
      return !members.rendered.contains(uid);
    }
  }
  if (top && collection == nullptr && ELEM(dob.type, OB_DUPLIVERTS, OB_DUPLIFACES)) {
    /* An instancer's children in the scene: render's depsgraph holds only those its view layer renders, by their
     * base (REVIEW-OVERLAP-A-2 L2). */
    return !live_shown_here(view_layer, DEG_get_original(dob.ob), DAG_EVAL_RENDER);
  }
  const bool picked = collection != nullptr || particle_collection ||
                      ELEM(dob.type, OB_DUPLICOLLECTION, OB_DUPLIVERTS, OB_DUPLIFACES);
  return picked && (dob.ob->visibility_flag & OB_HIDE_RENDER);
}

/** Whether \a dob is a member a Geometry Nodes Collection reference picked: object_dupli.cc gives such a member the
 * index it is made with and its own instances the next one (``make_dupli(.., object_id++)`` then
 * ``make_recursive_duplis(.., object_id++)``), where every other generator gives both the same. */
static bool collection_reference_member(const DupliObject &dob)
{
  const bke::GeometrySet *geometry = dob.instance_data[0];
  const bke::Instances *instances = geometry != nullptr ? geometry->get_instances() : nullptr;
  const int index = dob.instance_idx[0];
  return instances != nullptr && index >= 0 && index < instances->instances_num() &&
         instances->references()[instances->reference_handles()[index]].type() ==
             bke::InstanceReference::Type::Collection;
}

/** Whether \a above is an instance that made \a dob, any number of levels up: its persistent id path is the rest of
 * \a dob's (object_dupli.cc ``make_dupli``: an instance's path is its own index, then each level above's), the
 * level it made one past its own index for a Collection reference's member (REVIEW-OVERLAP-A-2 round 2 M1). */
static bool made_by(const DupliObject &dob, const DupliObject &above)
{
  const int offset = dob.level - above.level;
  if (offset < 1 || above.level < 0 || above.level + offset >= MAX_DUPLI_RECUR) {
    return false;
  }
  for (int i = 1; i <= above.level; i++) {
    if (above.persistent_id[i] != dob.persistent_id[i + offset]) {
      return false;
    }
  }
  const int pushed = dob.persistent_id[offset];
  return pushed == above.persistent_id[0] ||
         (pushed == above.persistent_id[0] + 1 && collection_reference_member(above));
}

/** The instances in \a parent's dupli list that Blender's render never makes (REVIEW-OVERLAP-A-2 M1). The list is
 * depth first: an instancing object's entry, then the instances it makes, deeper (object_dupli.cc). An instance is
 * left out when the nearest instance above that made it is left out or hidden from renders (render runs no generator
 * for an object hidden from renders, ``get_dupli_generator``), or when render picks it from a collection that does not
 * render it (#render_leaves_out, its instancer the one above, else \a parent). One walk, a stack of open levels. */
static Set<const DupliObject *> render_left_out(const Object *parent,
                                                const DupliList &duplis,
                                                ViewLayer *view_layer,
                                                Map<const Collection *, Members> &known)
{
  Set<const DupliObject *> out;
  Map<const DupliObject *, Collection *> picked;   /* The collection that picked each instance, when one did. */
  Vector<const DupliObject *> open;
  for (const DupliObject &dob : duplis) {
    while (!open.is_empty() && open.last()->level >= dob.level) {
      open.remove_last();
    }
    const DupliObject *above = nullptr;
    for (int index = int(open.size()) - 1; index >= 0 && above == nullptr; index--) {
      above = made_by(dob, *open[index]) ? open[index] : nullptr;
    }
    Collection *from = nullptr;
    if ((above != nullptr && (out.contains(above) || (above->ob->visibility_flag & OB_HIDE_RENDER))) ||
        render_leaves_out(dob,
                          above != nullptr ? above->ob : parent,
                          above != nullptr ? picked.lookup_default(above, nullptr) : nullptr,
                          above == nullptr,
                          view_layer,
                          known,
                          &from))
    {
      out.add(&dob);
    }
    if (from != nullptr && (dob.ob->transflag & (OB_DUPLIVERTS | OB_DUPLIFACES))) {
      picked.add(&dob, from);   /* Only an instancer of children needs it (R2-L2): never a scatter's every tile. */
    }
    open.append(&dob);
  }
  return out;
}

/** Helper geometry, one rule for a part's size and its world bounds: geometry hidden from renders, as Blender's render
 * decides it (the object, its collection or an instance's instancer by the base flags an instance's temporary object
 * takes from its instancer, depsgraph_query_iter.cc; the object an instance shows by #render_leaves_out), geometry
 * displayed as wire or bounds (a cutter, a guide), or a Boolean operand. \a dob is the instance \a ob is, if any. */
static bool is_helper(const Object *ob, const bool left_out, const Set<uint32_t> &operands)
{
  /* Its own geometry hidden from renders: Show Instancer off for renders on an instancer (REVIEW-OVERLAP-A-2 L1). */
  if (!(ob->base_flag & BASE_ENABLED_RENDER) || left_out ||
      !(BKE_object_visibility(ob, DAG_EVAL_RENDER) & OB_VISIBLE_SELF))
  {
    return true;
  }
  return ob->dt <= OB_WIRE || operands.contains(DEG_get_original(ob)->id.session_uid);
}

static DEGObjectIterSettings visible_with_instances(Depsgraph *depsgraph)
{
  DEGObjectIterSettings settings = {nullptr};
  settings.depsgraph = depsgraph;
  settings.flags = DEG_ITER_OBJECT_FLAG_LINKED_DIRECTLY | DEG_ITER_OBJECT_FLAG_VISIBLE |
                   DEG_ITER_OBJECT_FLAG_DUPLI;
  return settings;
}

/** What a pass leaves out: helper geometry, nothing, or only geometry a Solid draw shows no surface of (wire or
 * bounds display: the Main picture's fallback, REVIEW-OVERLAP-A-2 round 4 L1). */
enum class Skip { Helpers, Nothing, Undrawn };

/** The world box of the geometry \a depsgraph shows, what \a skip says left out. With \a planes (a view's six
 * frustum planes, inside in front): only whether one counted object's box meets them, stopping at the first; then
 * \a r_any says whether the pass counts any object at all. */
static bool merge_shown(Depsgraph *depsgraph,
                        const Skip skip,
                        float r_min[3],
                        float r_max[3],
                        const float (*planes)[4] = nullptr,
                        bool *r_any = nullptr)
{
  const bool skip_helpers = skip == Skip::Helpers;
  INIT_MINMAX(r_min, r_max);
  bool found = false;
  const Set<uint32_t> operands = skip_helpers ? boolean_operands(depsgraph) : Set<uint32_t>();
  Map<const Collection *, Members> known;
  ViewLayer *view_layer = DEG_get_input_view_layer(depsgraph);
  const eEvaluationMode mode = DEG_get_mode(depsgraph);
  const Object *walked = nullptr;   /* The instancer whose dupli list ``left_out`` holds. */
  Set<const DupliObject *> left_out;
  DEGObjectIterSettings settings = visible_with_instances(depsgraph);
  DEG_OBJECT_ITER_BEGIN (&settings, ob) {
    const DupliObject *dob = (ob->base_flag & BASE_FROM_DUPLI) ? data_.dupli_object_current : nullptr;
    if (skip_helpers && dob != nullptr && data_.dupli_parent != walked) {
      left_out = render_left_out(data_.dupli_parent, data_.dupli_list, view_layer, known);
      walked = data_.dupli_parent;
    }
    /* What it draws of its own: an emitter or an instancer with Show Instancer off draws none (L1). */
    if (live_is_geometry(ob) && (BKE_object_visibility(ob, mode) & OB_VISIBLE_SELF) &&
        !(skip_helpers && is_helper(ob, dob != nullptr && left_out.contains(dob), operands)) &&
        !(skip == Skip::Undrawn && ob->dt <= OB_WIRE))
    {
      if (planes == nullptr) {
        found |= live_merge_owned_bounds(ob, r_min, r_max);
        continue;
      }
      float own_min[3], own_max[3];
      INIT_MINMAX(own_min, own_max);
      if (live_merge_owned_bounds(ob, own_min, own_max)) {
        *r_any = true;
        if (isect_aabb_planes_v3(planes, 6, own_min, own_max) != ISECT_AABB_PLANE_BEHIND_ANY) {
          found = true;
          break; /* ITER_END still ends the iterator (frees its dupli list). */
        }
      }
    }
  }
  DEG_OBJECT_ITER_END;
  return found;
}

bool live_depsgraph_bounds(Depsgraph *depsgraph, float r_min[3], float r_max[3], const bool helpers_alone)
{
  /* Helpers left out unless the part shows nothing else: a part made only of guides or cutters still has a size and
   * framing (REVIEW-OVERLAP-A M3). The Main tile's picture asks without \a helpers_alone: then only what a Solid draw
   * without overlays shows counts (no wire or bounds display), so a scene of only wire guides has no picture, never an
   * empty frame, while a solid model hidden from renders still has one. */
  return merge_shown(depsgraph, Skip::Helpers, r_min, r_max) ||
         merge_shown(depsgraph, helpers_alone ? Skip::Nothing : Skip::Undrawn, r_min, r_max);
}

bool live_depsgraph_sees(Depsgraph *depsgraph, const float persmat[4][4])
{
  /* The Main picture's rule (``live_depsgraph_bounds`` without helpers alone), per object: whether a view whose
   * perspective matrix is \a persmat may see what the Main tile frames. An object's world box that meets the frustum
   * counts, so false only when no counted geometry can show: a necessary test, the picture itself decides
   * (``rail_pictures.shown_share``, REVIEW-MAIN-THUMB-2 H1). */
  float planes[6][4], min[3], max[3];
  planes_from_projmat(persmat, planes[0], planes[1], planes[2], planes[3], planes[4], planes[5]);
  bool any = false;
  if (merge_shown(depsgraph, Skip::Helpers, min, max, planes, &any)) {
    return true;
  }
  return !any && merge_shown(depsgraph, Skip::Undrawn, min, max, planes, &any);
}

}  // namespace blender
