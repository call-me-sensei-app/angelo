/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup edsculpt
 *
 * Immutable owner-specific Multires surfaces for explicit Main Undo boundaries. Encoded Main
 * remains the owner of original displacement data; these values preserve live editing surfaces.
 */

#include "sculpt_main_undo_grids.hh"
#include "sculpt_main_undo_snapshot_access.hh"

#include <algorithm>
#include <cstring>
#include <climits>
#include <limits>
#include <memory>

#include "MEM_guardedalloc.h"

#include "BLI_array.hh"
#include "BLI_bit_group_vector.hh"
#include "BLI_listbase.h"
#include "BLI_map.hh"
#include "BLI_set.hh"
#include "BLI_string.h"
#include "BLI_vector.hh"

#include "DNA_layer_types.h"
#include "DNA_mesh_types.h"
#include "DNA_modifier_types.h"
#include "DNA_object_types.h"
#include "DNA_scene_types.h"

#include "BKE_customdata.hh"
#include "BKE_global.hh"
#include "BKE_main.hh"
#include "BKE_mesh.hh"
#include "BKE_mesh_runtime.hh"
#include "BKE_modifier.hh"
#include "BKE_multires.hh"
#include "BKE_object.hh"
#include "BKE_object_types.hh"
#include "BKE_paint.hh"
#include "BKE_paint_bvh.hh"
#include "BKE_scene.hh"
#include "BKE_sculpt_undo.hh"
#include "BKE_subdiv.hh"
#include "BKE_subdiv_ccg.hh"
#include "BKE_undo_system.hh"

#include "DEG_depsgraph_query.hh"

namespace blender::ed::sculpt_paint::undo {

namespace {

template<typename T> bool values_equal(const Span<T> a, const Span<T> b)
{
  return a.size() == b.size() &&
         (a.is_empty() || std::memcmp(a.data(), b.data(), a.size_in_bytes()) == 0);
}

template<int64_t A, int64_t B>
bool hidden_equal(const BitGroupVector<A> &a, const BitGroupVector<B> &b)
{
  if (a.size() != b.size() || a.group_size() != b.group_size()) {
    return false;
  }
  for (const int64_t i : a.index_range()) {
    if (!bits::spans_equal(a[i], b[i])) {
      return false;
    }
  }
  return true;
}

template<int64_t Capacity> BitGroupVector<Capacity> hidden_copy(const BitGroupVector<> &source)
{
  BitGroupVector<Capacity> result(source.size(), source.group_size());
  for (const int64_t i : source.index_range()) {
    result[i].copy_from(source[i]);
  }
  return result;
}

BitGroupVector<> hidden_for_restore(const BitGroupVector<0> &source)
{
  BitGroupVector<> result(source.size(), source.group_size());
  for (const int64_t i : source.index_range()) {
    result[i].copy_from(source[i]);
  }
  return result;
}

struct EvaluatedOwner {
  DEGRegisteredGraph graph;
  uint32_t object_uid;
  uint32_t mesh_uid;
  const Object *object;
  const Mesh *source_mesh;
  const Mesh *evaluated_mesh;
  const Scene *evaluated_scene;
  SubdivCCG *grids;
};

/** No original pointer stored in an evaluated ID is dereferenced by this inventory. */
bool inventory(Main &bmain, Vector<EvaluatedOwner> &r_owners, const char **r_error,
               const bool transient = false, const Span<uint32_t> selected = {},
               size_t *used = nullptr, const size_t limit = 0)
{
  return DEG_foreach_registered_graph(&bmain, [&](const DEGRegisteredGraph &graph) {
    if (graph.source_main != &bmain) {
      *r_error = "A registered Sculpt graph does not belong to the current document";
      return false;
    }
    if (graph.is_evaluating && !transient) {
      *r_error = "Finish dependency graph evaluation before replacing the Sculpt Undo state";
      return false;
    }
    Map<const ID *, DEGEvaluatedID> evaluated_ids;
    const auto visit = [&](const DEGEvaluatedID &id) {
          if (used && !checkpoint_snapshot_charge(*used, limit, sizeof(DEGEvaluatedID) * 32)) {
            *r_error = "Checkpoint evaluated-ID metadata exceeds preallocation admission";
            return false;
          }
          if (!id.original_session_uid || !evaluated_ids.add(id.evaluated_id, id)) {
            *r_error = "The evaluated Sculpt ownership inventory has an invalid identity";
            return false;
          }
          return true;
        };
    if (!(transient ? DEG_checkpoint_foreach_evaluated_ID(graph.depsgraph, visit) :
                     DEG_foreach_evaluated_ID(graph.depsgraph, visit)))
    {
      if (!*r_error) {
        *r_error = "The evaluated Sculpt ownership inventory is unavailable";
      }
      return false;
    }
    const Scene *evaluated_scene = DEG_get_evaluated_scene(graph.depsgraph);
    const DEGEvaluatedID *scene_id = evaluated_ids.lookup_ptr(
        reinterpret_cast<const ID *>(evaluated_scene));
    for (const DEGEvaluatedID &id : evaluated_ids.values()) {
      if (id.original_type != ID_OB) {
        continue;
      }
      if (transient && !selected.contains(id.original_session_uid)) { continue; }
      const Object *object = id_cast<const Object *>(id.evaluated_id);
      if (object->type != OB_MESH || !object->runtime->is_data_eval_owned) {
        continue;
      }
      /* This accessor never creates CPU subdivision data. The evaluated Object owns the result. */
      const Mesh *mesh = BKE_object_get_evaluated_mesh_no_subsurf_unchecked(object);
      if (!mesh || !mesh->runtime->subdiv_ccg) {
        continue;
      }
      const DEGEvaluatedID *data = evaluated_ids.lookup_ptr(object->runtime->data_orig);
      if (!data || data->original_type != ID_ME) {
        *r_error = "An evaluated Multires cache has no provable source Mesh identity";
        return false;
      }
      if (used && !checkpoint_snapshot_charge(*used, limit, sizeof(EvaluatedOwner) * 4)) {
        *r_error = "Checkpoint owner metadata exceeds preallocation admission";
        return false;
      }
      DEGRegisteredGraph observed_graph = graph;
      if (transient && scene_id && scene_id->original_type == ID_SCE) {
        observed_graph.scene_original_session_uid = scene_id->original_session_uid;
      }
      r_owners.append({observed_graph,
                       id.original_session_uid,
                       data->original_session_uid,
                       object,
                       id_cast<const Mesh *>(data->evaluated_id),
                       mesh,
                       scene_id && scene_id->original_type == ID_SCE &&
                               scene_id->original_session_uid == observed_graph.scene_original_session_uid ?
                           evaluated_scene :
                           nullptr,
                       mesh->runtime->subdiv_ccg.get()});
    }
    return true;
  });
}

bool canonical(const DEGRegisteredGraph &graph)
{
  return graph.scene && graph.view_layer &&
         BKE_scene_get_depsgraph(graph.scene, graph.view_layer) == graph.depsgraph;
}

bool owner_identity_valid(const DEGRegisteredGraph &graph, const char **r_error)
{
  if (!canonical(graph) || graph.mode != DAG_EVAL_VIEWPORT || graph.is_render_pipeline) {
    *r_error = "The Sculpt grids do not belong to a canonical viewport dependency graph";
    return false;
  }
  if (!graph.scene_original_session_uid ||
      graph.scene_original_session_uid != graph.scene->id.session_uid)
  {
    *r_error = "The Multires owner graph has no matching historical Scene identity";
    return false;
  }
  int matching_layers = 0;
  for (const ViewLayer &view_layer : graph.scene->view_layers) {
    matching_layers += STREQ(view_layer.name, graph.view_layer->name);
  }
  if (matching_layers != 1) {
    *r_error = "The Multires view-layer identity is ambiguous";
    return false;
  }
  return true;
}

bool current_owner(const DEGRegisteredGraph &graph, const char **r_error)
{
  if (!owner_identity_valid(graph, r_error)) {
    return false;
  }
  if (graph.is_evaluating || graph.need_update_relations || graph.need_update_nodes_visibility ||
      graph.has_pending_updates || graph.frame != BKE_scene_frame_get(graph.scene) ||
      graph.ctime != BKE_scene_ctime_get(graph.scene))
  {
    *r_error = "Finish the pending scene or frame update before saving the Multires Undo state";
    return false;
  }
  return true;
}

bool key_less(const MainUndoGridOwnerKey &a, const MainUndoGridOwnerKey &b)
{
  if (a.object_uid != b.object_uid) {
    return a.object_uid < b.object_uid;
  }
  if (a.mesh_uid != b.mesh_uid) {
    return a.mesh_uid < b.mesh_uid;
  }
  if (a.scene_uid != b.scene_uid) {
    return a.scene_uid < b.scene_uid;
  }
  return std::strcmp(a.view_layer, b.view_layer) < 0;
}

bool key_equal(const MainUndoGridOwnerKey &a, const MainUndoGridOwnerKey &b)
{
  return a.object_uid == b.object_uid && a.mesh_uid == b.mesh_uid && a.scene_uid == b.scene_uid &&
         STREQ(a.view_layer, b.view_layer);
}

struct LiveOwner {
  MainUndoGridOwnerKey key;
  EvaluatedOwner evaluated;
  const Object *original;
};

/** Inventory all registered caches before filtering: an alias outside the saved set is unsafe too.
 * Discovery can admit pending/time-stale graphs only to return identities for synchronization. */
bool collect_live_owners(Main &bmain,
                         const bool require_current,
                         Vector<LiveOwner> &r_owners,
                         const char **r_error)
{
  Map<uint32_t, const Object *> objects;
  Map<uint32_t, int> authority_counts;
  for (const Object &object : bmain.objects) {
    const SculptSession *ss = object.runtime->sculpt_session;
    if (!(object.mode & OB_MODE_SCULPT) || !ss || !ss->subdiv_ccg) {
      continue;
    }
    if (!object.id.session_uid || object.type != OB_MESH || !object.data ||
        !object.data->session_uid || !objects.add(object.id.session_uid, &object))
    {
      *r_error = "The live Multires Object or Mesh identity is invalid";
      return false;
    }
    authority_counts.add_new(object.id.session_uid, 0);
  }
  if (objects.is_empty()) {
    return true;
  }
  Vector<EvaluatedOwner> inventory_owners;
  if (!inventory(bmain, inventory_owners, r_error)) {
    if (!*r_error) {
      *r_error = "The registered Sculpt graph inventory is unavailable";
    }
    return false;
  }
  Map<const SubdivCCG *, int> cache_users;
  for (const EvaluatedOwner &owner : inventory_owners) {
    cache_users.lookup_or_add(owner.grids, 0)++;
  }
  Set<uint32_t> covered;
  for (const EvaluatedOwner &owner : inventory_owners) {
    const Object *object = objects.lookup_default(owner.object_uid, nullptr);
    if (!object) {
      continue;
    }
    if (owner.mesh_uid != object->data->session_uid || cache_users.lookup(owner.grids) != 1) {
      *r_error = "The Multires cache has a mismatched Mesh or more than one evaluated owner";
      return false;
    }
    if (!(require_current ? current_owner(owner.graph, r_error) :
                            owner_identity_valid(owner.graph, r_error)))
    {
      return false;
    }
    if (!owner.evaluated_scene) {
      *r_error = "The Multires owner has no provable evaluated Scene identity";
      return false;
    }
    LiveOwner live;
    live.key.object_uid = owner.object_uid;
    live.key.mesh_uid = owner.mesh_uid;
    live.key.scene_uid = owner.graph.scene->id.session_uid;
    static_assert(sizeof(live.key.view_layer) == sizeof(owner.graph.view_layer->name));
    STRNCPY(live.key.view_layer, owner.graph.view_layer->name);
    live.evaluated = owner;
    live.original = object;
    live.key.authority = owner.grids == object->runtime->sculpt_session->subdiv_ccg;
    authority_counts.lookup(owner.object_uid) += live.key.authority;
    covered.add(owner.object_uid);
    r_owners.append(live);
  }
  if (covered.size() != objects.size()) {
    *r_error = "The live Sculpt grids have no evaluated owner in this document";
    return false;
  }
  for (const int count : authority_counts.values()) {
    if (count != 1) {
      *r_error = "The live Sculpt grids do not have exactly one original session authority";
      return false;
    }
  }
  std::sort(r_owners.begin(), r_owners.end(), [](const LiveOwner &a, const LiveOwner &b) {
    return key_less(a.key, b.key);
  });
  for (const int64_t i : r_owners.index_range().drop_front(1)) {
    if (key_equal(r_owners[i - 1].key, r_owners[i].key)) {
      *r_error = "The Multires cache owner key is ambiguous";
      return false;
    }
  }
  return true;
}

/** Values that affect evaluation; addresses and graph activity are not saved identities. */
struct GraphProfile {
  bool visibility_optimization;
  bool editors_update;
  DepsgraphEvaluateSyncWriteback sync_writeback;
  int debug_flags;
  int scene_mode;
  short simplify_subsurf;
  float frame;
  float ctime;
  int evaluated_scene_mode;
  short evaluated_simplify_subsurf;
  float evaluated_frame;
  float evaluated_ctime;
};

GraphProfile profile(const EvaluatedOwner &owner)
{
  const DEGRegisteredGraph &graph = owner.graph;
  return {graph.use_visibility_optimization,
          graph.use_editors_update,
          graph.sync_writeback,
          graph.debug_flags,
          graph.scene->r.mode,
          graph.scene->r.simplify_subsurf,
          graph.frame,
          graph.ctime,
          owner.evaluated_scene->r.mode,
          owner.evaluated_scene->r.simplify_subsurf,
          BKE_scene_frame_get(owner.evaluated_scene),
          BKE_scene_ctime_get(owner.evaluated_scene)};
}

bool profile_equal(const GraphProfile &a, const GraphProfile &b)
{
  return a.visibility_optimization == b.visibility_optimization &&
         a.editors_update == b.editors_update && a.sync_writeback == b.sync_writeback &&
         a.debug_flags == b.debug_flags && a.scene_mode == b.scene_mode &&
         a.simplify_subsurf == b.simplify_subsurf &&
         std::memcmp(&a.frame, &b.frame, sizeof(float)) == 0 &&
         std::memcmp(&a.ctime, &b.ctime, sizeof(float)) == 0 &&
         a.evaluated_scene_mode == b.evaluated_scene_mode &&
         a.evaluated_simplify_subsurf == b.evaluated_simplify_subsurf &&
         std::memcmp(&a.evaluated_frame, &b.evaluated_frame, sizeof(float)) == 0 &&
         std::memcmp(&a.evaluated_ctime, &b.evaluated_ctime, sizeof(float)) == 0;
}

struct ModifierProfile {
  int uid;
  ModifierMode mode;
  char level;
  char sculpt_level;
  char render_level;
  char total_level;
  MultiresModifierFlag flags;
  short quality;
  short uv_smooth;
  short boundary_smooth;
};

ModifierProfile profile(const MultiresModifierData &mmd)
{
  return {mmd.modifier.persistent_uid,
          mmd.modifier.mode,
          mmd.lvl,
          mmd.sculptlvl,
          mmd.renderlvl,
          mmd.totlvl,
          mmd.flags,
          mmd.quality,
          mmd.uv_smooth,
          mmd.boundary_smooth};
}

bool profile_equal(const ModifierProfile &a, const ModifierProfile &b)
{
  return a.uid == b.uid && a.mode == b.mode && a.level == b.level &&
         a.sculpt_level == b.sculpt_level && a.render_level == b.render_level &&
         a.total_level == b.total_level && a.flags == b.flags && a.quality == b.quality &&
         a.uv_smooth == b.uv_smooth && a.boundary_smooth == b.boundary_smooth;
}

struct GridOwner : MainUndoGridOwnerKey {
  GraphProfile graph;
  ModifierProfile modifier;
  Array<int3, 0> modifier_order;
  ModifierProfile evaluated_modifier;
  Array<int3, 0> evaluated_modifier_order;
  Array<ModifierProfile, 0> observed_modifiers, observed_evaluated_modifiers;
  const SubdivCCG *observed_cache = nullptr;
  const Mesh *observed_mesh = nullptr;
  const Object *observed_object = nullptr;
  uint32_t observed_original_mesh_uid = 0;
  bke::subdiv::Settings subdivision;
  int total_level;
  int verts_num;
  Array<int2, 0> edges;
  Array<int, 0> face_offsets;
  Array<int, 0> corner_verts;
  Array<int, 0> corner_edges;
  Array<int, 0> grid_to_face;
  int level;
  int grid_size;
  int grid_area;
  int grids_num;
  Array<float3, 0> positions;
  Array<float3, 0> normals;
  Array<float, 0> masks;
  BitGroupVector<0> hidden;
  Array<int, 0> pending_normal_faces;
  bool dirty_coords;
  bool dirty_hidden;
  const Depsgraph *observed_graph = nullptr;
  std::unique_ptr<SubdivCCG> replay_arrays;
  std::unique_ptr<bke::pbvh::Tree> replay_tree;
};

Array<int3, 0> modifier_order(const Object &object)
{
  Vector<int3> order;
  for (const ModifierData &modifier : object.modifiers) {
    order.append(int3(modifier.persistent_uid, int(modifier.type), int(modifier.mode)));
  }
  return Array<int3, 0>(order.as_span());
}

bool subdivision_equal(const bke::subdiv::Settings &a, const bke::subdiv::Settings &b)
{
  return a.is_simple == b.is_simple && a.is_adaptive == b.is_adaptive && a.level == b.level &&
         a.use_creases == b.use_creases &&
         a.vtx_boundary_interpolation == b.vtx_boundary_interpolation &&
         a.fvar_linear_interpolation == b.fvar_linear_interpolation;
}

struct OwnerModifiers {
  const MultiresModifierData *original;
  const MultiresModifierData *evaluated;
};

/** Match the noncreating Multires selector without virtual modifiers (which never add Multires).
 * Read only proven owned evaluated inputs; do not follow an evaluated ID's original pointers. */
bool owner_modifiers(const LiveOwner &owner,
                     const bool require_session_binding,
                     OwnerModifiers &r_modifiers,
                     const char **r_error)
{
  const EvaluatedOwner &evaluated = owner.evaluated;
  const Object &object = *evaluated.object;
  const MultiresModifierData *mmd = nullptr;
  if ((object.mode & OB_MODE_SCULPT) &&
      CustomData_has_layer(&evaluated.source_mesh->corner_data, CD_MDISPS))
  {
    for (const ModifierData &modifier : object.modifiers) {
      if (modifier.type != eModifierType_Multires ||
          !BKE_modifier_is_enabled(
              evaluated.evaluated_scene, const_cast<ModifierData *>(&modifier), eModifierMode_Realtime))
      {
        continue;
      }
      const auto *candidate = reinterpret_cast<const MultiresModifierData *>(&modifier);
      if (candidate->sculptlvl > 0 &&
          !(candidate->flags & eMultiresModifierFlag_UseSculptBaseMesh))
      {
        mmd = candidate;
      }
      break;
    }
  }
  const MultiresModifierData *original = nullptr;
  if (mmd && mmd->modifier.persistent_uid > 0) {
    for (const ModifierData &modifier : owner.original->modifiers) {
      if (modifier.persistent_uid != mmd->modifier.persistent_uid) {
        continue;
      }
      if (original || modifier.type != eModifierType_Multires) {
        *r_error = "The evaluated Multires modifier identity is ambiguous";
        return false;
      }
      original = reinterpret_cast<const MultiresModifierData *>(&modifier);
    }
  }
  bke::subdiv::Settings subdivision{};
  if (mmd) {
    BKE_multires_subdiv_settings_init(&subdivision, mmd);
  }
  if (!mmd || !original || !evaluated.grids->subdiv ||
      multires_get_level(evaluated.evaluated_scene, &object, mmd, false, false) !=
          evaluated.grids->level ||
      evaluated.evaluated_mesh->runtime->subdiv_ccg_tot_level != mmd->totlvl ||
      !subdivision_equal(subdivision, evaluated.grids->subdiv->settings))
  {
    *r_error = "The evaluated Multires modifier, level or subdivision profile is unprovable";
    return false;
  }
  if (require_session_binding && owner.key.authority) {
    const SculptSession &ss = *owner.original->runtime->sculpt_session;
    if (ss.multires_modifier != original ||
        BKE_sculpt_multires_active(evaluated.graph.scene,
                                   const_cast<Object *>(owner.original)) != original)
    {
      *r_error = "The original Sculpt session Multires modifier binding is stale";
      return false;
    }
  }
  r_modifiers = {original, mmd};
  return true;
}

bool grid_shape_valid(const Mesh &mesh, const SubdivCCG &grids, const char **r_error)
{
  const int64_t area = int64_t(grids.grid_size) * grids.grid_size;
  const int64_t samples = int64_t(grids.grids_num) * grids.grid_area;
  if (grids.level < 1 || grids.level > 30 || grids.grid_size <= 1 || grids.grid_area <= 0 ||
      grids.grids_num <= 0 || area != grids.grid_area || samples <= 0 ||
      uint64_t(samples) > std::numeric_limits<size_t>::max() / sizeof(float3) ||
      grids.grid_size != (int64_t(1) << (grids.level - 1)) + 1 ||
      grids.grids_num != mesh.corners_num || grids.positions.size() != samples ||
      grids.normals.size() != samples ||
      (!grids.masks.is_empty() && grids.masks.size() != samples) ||
      (!grids.grid_hidden.is_empty() && (grids.grid_hidden.size() != grids.grids_num ||
                                         grids.grid_hidden.group_size() != grids.grid_area)) ||
      !values_equal(grids.faces.data(), mesh.face_offsets()) ||
      grids.grid_to_face_map.size() != grids.grids_num)
  {
    *r_error = "The Multires grids do not match their source topology and storage dimensions";
    return false;
  }
  for (const int face : mesh.faces().index_range()) {
    for (const int corner : mesh.faces()[face]) {
      if (grids.grid_to_face_map[corner] != face) {
        *r_error = "The Multires grid-to-face mapping is inconsistent";
        return false;
      }
    }
  }
  return true;
}

bool topology_matches(const GridOwner &saved, const Mesh &mesh, const SubdivCCG &grids)
{
  return saved.mesh_uid == mesh.id.session_uid && saved.verts_num == mesh.verts_num &&
         values_equal(saved.edges.as_span(), mesh.edges()) &&
         values_equal(saved.face_offsets.as_span(), mesh.face_offsets()) &&
         values_equal(saved.corner_verts.as_span(), mesh.corner_verts()) &&
         values_equal(saved.corner_edges.as_span(), mesh.corner_edges()) &&
         values_equal(saved.grid_to_face.as_span(), grids.grid_to_face_map) &&
         saved.level == grids.level && saved.grid_size == grids.grid_size &&
         saved.grid_area == grids.grid_area && saved.grids_num == grids.grids_num;
}

bool append_size(size_t &total, const size_t bytes)
{
  if (bytes > std::numeric_limits<size_t>::max() - total) {
    return false;
  }
  total += bytes;
  return true;
}

template<typename T> bool add_array_size(size_t &total, const Array<T, 0> &array)
{
  return array.is_empty() || append_size(total, MEM_allocN_len(array.data()));
}

bool add_grid_size(size_t &total, const GridOwner &grid)
{
  return add_array_size(total, grid.modifier_order) &&
         add_array_size(total, grid.evaluated_modifier_order) && add_array_size(total, grid.edges) &&
         add_array_size(total, grid.face_offsets) && add_array_size(total, grid.corner_verts) &&
         add_array_size(total, grid.corner_edges) && add_array_size(total, grid.grid_to_face) &&
         add_array_size(total, grid.positions) && add_array_size(total, grid.normals) &&
         add_array_size(total, grid.masks) && add_array_size(total, grid.pending_normal_faces) &&
         (grid.hidden.is_empty() ||
          append_size(total, MEM_allocN_len(grid.hidden.all_bits().data())));
}

}  // namespace

struct MainUndoGridState {
  Vector<GridOwner, 0> owners;
  size_t memory_size;
};

static MainUndoGridState *main_undo_grids_capture_impl(Main &bmain, const char **r_error,
                                                       size_t *used = nullptr,
                                                       const size_t limit = 0)
{
  *r_error = nullptr;
  Vector<const Object *> objects;
  for (const Object &object : bmain.objects) {
    const SculptSession *ss = object.runtime->sculpt_session;
    if (!(object.mode & OB_MODE_SCULPT) || !ss || !ss->subdiv_ccg) {
      continue;
    }
    if (ss->bm || ss->bm_log || ss->cache || ss->filter_cache || ss->expand_cache || !ss->pbvh ||
        ss->pbvh->type() != bke::pbvh::Type::Grids)
    {
      *r_error = "Finish the active or pending Sculpt surface change before capturing Multires";
      return nullptr;
    }
    if (used && !checkpoint_snapshot_charge(*used, limit, sizeof(const Object *) * 4)) {
      *r_error = "Checkpoint object inventory exceeds preallocation admission"; return nullptr;
    }
    objects.append(&object);
  }
  if (objects.is_empty()) {
    return nullptr;
  }
  if (used) {
    size_t ids = 0, graphs = 0;
    ID *id;
    FOREACH_MAIN_ID_BEGIN (&bmain, id) { ids++; } FOREACH_MAIN_ID_END;
    DEG_foreach_registered_graph(&bmain, [&](const DEGRegisteredGraph &) { graphs++; return true; });
    if (ids > (SIZE_MAX - 2048) / 2048 || graphs > SIZE_MAX / (ids * 2048 + 2048) ||
        !checkpoint_snapshot_charge(*used, limit, graphs * (ids * 2048 + 2048))) {
      *r_error = "Checkpoint strict inventory metadata exceeds preallocation admission"; return nullptr;
    }
  }
  Vector<LiveOwner> owners;
  if (!collect_live_owners(bmain, true, owners, r_error)) {
    return nullptr;
  }
  if (used && !checkpoint_snapshot_charge(*used, limit, sizeof(MainUndoGridState))) {
    *r_error = "Checkpoint strict grid control exceeds preallocation admission"; return nullptr;
  }
  auto state = std::make_unique<MainUndoGridState>();
  for (const LiveOwner &owner : owners) {
    const Object *object = owner.original;
    const Mesh &mesh = *id_cast<const Mesh *>(object->data);
    const SculptSession &ss = *object->runtime->sculpt_session;
    OwnerModifiers modifiers;
    if (!owner_modifiers(owner, true, modifiers, r_error)) {
      return nullptr;
    }
    const SubdivCCG &grids = *owner.evaluated.grids;
    if (!grid_shape_valid(mesh, grids, r_error)) {
      return nullptr;
    }
    if (used) {
      const size_t array_bytes[] = {sizeof(GridOwner) * 4, size_t(mesh.edges().size_in_bytes()),
          size_t(mesh.face_offsets().size_in_bytes()), size_t(mesh.corner_verts().size_in_bytes()),
          size_t(mesh.corner_edges().size_in_bytes()), size_t(grids.grid_to_face_map.size_in_bytes()),
          size_t(grids.positions.as_span().size_in_bytes()), size_t(grids.normals.as_span().size_in_bytes()),
          size_t(grids.masks.as_span().size_in_bytes()),
          size_t((grids.grid_hidden.all_bits().size() + 63) / 64) * 8,
          size_t(mesh.faces_num) * sizeof(int) * 4,
          size_t(object->modifiers.count() + owner.evaluated.object->modifiers.count()) * sizeof(int3)};
      if (grids.grid_hidden.all_bits().size() > INT_MAX) {
        *r_error = "Checkpoint hidden storage exceeds native copy representability"; return nullptr;
      }
      for (const size_t bytes : array_bytes) {
        if (!checkpoint_snapshot_charge(*used, limit, bytes)) {
          *r_error = "Checkpoint strict grid arrays exceed preallocation admission"; return nullptr;
        }
      }
    }
    GridOwner saved;
    static_cast<MainUndoGridOwnerKey &>(saved) = owner.key;
    saved.authority = owner.key.authority;
    saved.graph = profile(owner.evaluated);
    saved.modifier = profile(*modifiers.original);
    saved.modifier_order = modifier_order(*object);
    saved.evaluated_modifier = profile(*modifiers.evaluated);
    saved.evaluated_modifier_order = modifier_order(*owner.evaluated.object);
    saved.subdivision = grids.subdiv->settings;
    saved.total_level = owner.evaluated.evaluated_mesh->runtime->subdiv_ccg_tot_level;
    saved.verts_num = mesh.verts_num;
    saved.edges = Array<int2, 0>(mesh.edges());
    saved.face_offsets = Array<int, 0>(mesh.face_offsets());
    saved.corner_verts = Array<int, 0>(mesh.corner_verts());
    saved.corner_edges = Array<int, 0>(mesh.corner_edges());
    saved.grid_to_face = Array<int, 0>(grids.grid_to_face_map);
    saved.level = grids.level;
    saved.grid_size = grids.grid_size;
    saved.grid_area = grids.grid_area;
    saved.grids_num = grids.grids_num;
    saved.positions = Array<float3, 0>(grids.positions.as_span());
    saved.normals = Array<float3, 0>(grids.normals.as_span());
    saved.masks = Array<float, 0>(grids.masks.as_span());
    saved.hidden = hidden_copy<0>(grids.grid_hidden);
    if (owner.key.authority) {
      saved.pending_normal_faces = ss.pbvh->capture_grid_normal_update_faces(grids);
    }
    saved.dirty_coords = grids.dirty.coords;
    saved.dirty_hidden = grids.dirty.hidden;
    state->owners.append(std::move(saved));
  }
  size_t bytes = sizeof(*state);
  if (!append_size(bytes, MEM_allocN_len(state->owners.data()))) {
    *r_error = "The retained Multires Undo storage exceeds the memory accounting range";
    return nullptr;
  }
  for (const GridOwner &saved : state->owners) {
    if (!add_grid_size(bytes, saved)) {
      *r_error = "The retained Multires Undo storage exceeds the memory accounting range";
      return nullptr;
    }
  }
  state->memory_size = bytes;
  return state.release();
}

MainUndoGridState *main_undo_grids_capture(Main &bmain, const char **r_error)
{
  return main_undo_grids_capture_impl(bmain, r_error);
}

MainUndoGridState *main_undo_grids_capture_checkpoint(Main &bmain, const size_t limit,
                                                     size_t &used, const char **r_error)
{
  return main_undo_grids_capture_impl(bmain, r_error, &used, limit);
}

MainUndoGridState *main_undo_grids_observe_checkpoint(Main &bmain,
                                                     const Span<uint32_t> selected,
                                                     const size_t limit, size_t &used,
                                                     const char **r_error)
{
  if (!DEG_checkpoint_runtime_observation_allowed(&bmain)) {
    *r_error = "Checkpoint transient observation has no exclusive no-worker lease";
    return nullptr;
  }
  Vector<EvaluatedOwner> owners;
  if (!inventory(bmain, owners, r_error, true, selected, &used, limit)) { return nullptr; }
  if (owners.is_empty()) { return nullptr; }
  if (!checkpoint_snapshot_charge(used, limit, sizeof(MainUndoGridState))) {
    *r_error = "Checkpoint grid control exceeds preallocation admission"; return nullptr;
  }
  auto state = std::make_unique<MainUndoGridState>();
  for (const EvaluatedOwner &owner : owners) {
    const Object *original = nullptr;
    const Mesh *mesh = owner.source_mesh;
    for (const Object &object : bmain.objects) {
      if (object.id.session_uid == owner.object_uid) { original = &object; break; }
    }
    bool scene_live = false;
    for (const Scene &scene : bmain.scenes) {
      if (&scene == owner.graph.scene && scene.id.session_uid == owner.graph.scene_original_session_uid) {
        scene_live = BLI_findindex(&scene.view_layers, owner.graph.view_layer) >= 0; break;
      }
    }
    const Mesh *original_mesh = nullptr;
    if (original && original->type == OB_MESH) {
      for (const Mesh &candidate : bmain.meshes) {
        if (reinterpret_cast<const ID *>(&candidate) == original->data) { original_mesh = &candidate; break; }
      }
    }
    /* A suspended callback can legitimately replace Object.data while this graph still owns
     * the earlier expanded base and modifier output. Observe both proven owners independently;
     * never relabel the evaluated cache as belonging to the new original Mesh. */
    if (!original || !original_mesh || !scene_live || !owner.evaluated_scene || !owner.source_mesh) {
      *r_error = "Checkpoint observed owner has no live typed original/expanded binding"; return nullptr;
    }
    const SubdivCCG &grids = *owner.grids;
    auto charge_array = [&](const size_t bytes) { return checkpoint_snapshot_charge(used, limit, bytes); };
    const auto *ss = original->runtime->sculpt_session;
    const bool authority = ss && ss->subdiv_ccg == owner.grids;
    /* Never follow ss.subdiv_ccg: during evaluation it may still name a retired cache. */
    if (!charge_array(sizeof(GridOwner) * 4) ||
        !charge_array(mesh->edges().size_in_bytes()) ||
        !charge_array(mesh->face_offsets().size_in_bytes()) ||
        !charge_array(mesh->corner_verts().size_in_bytes()) ||
        !charge_array(mesh->corner_edges().size_in_bytes()) ||
        !charge_array(grids.grid_to_face_map.size_in_bytes()) ||
        !charge_array(grids.positions.as_span().size_in_bytes()) ||
        !charge_array(grids.normals.as_span().size_in_bytes()) ||
        !charge_array(grids.masks.as_span().size_in_bytes()) ||
        grids.grid_hidden.all_bits().size() > INT_MAX ||
        !charge_array(size_t((grids.grid_hidden.all_bits().size() + 63) / 64) * 8) ||
        !charge_array(size_t(mesh->faces_num) * sizeof(int) * 4) ||
        !charge_array(size_t(original->modifiers.count() + owner.object->modifiers.count()) *
                      (sizeof(int3) + sizeof(ModifierProfile)) * 4))
    {
      *r_error = "Checkpoint observed arrays exceed preallocation admission"; return nullptr;
    }
    GridOwner row{};
    row.observed_cache = owner.grids; row.observed_mesh = owner.evaluated_mesh;
    row.observed_object = owner.object;
    row.observed_original_mesh_uid = original_mesh->id.session_uid;
    row.object_uid = owner.object_uid; row.mesh_uid = owner.mesh_uid;
    row.scene_uid = owner.graph.scene_original_session_uid;
    STRNCPY(row.view_layer, owner.graph.view_layer->name);
    row.authority = authority; row.graph = profile(owner);
    row.modifier_order = modifier_order(*original);
    row.evaluated_modifier_order = modifier_order(*owner.object);
    auto modifiers = [](const Object &object) {
      Vector<ModifierProfile> profiles;
      for (const ModifierData &modifier : object.modifiers) {
        if (modifier.type == eModifierType_Multires) {
          profiles.append(profile(*reinterpret_cast<const MultiresModifierData *>(&modifier)));
        }
      }
      return Array<ModifierProfile, 0>(profiles.as_span());
    };
    row.observed_modifiers = modifiers(*original);
    row.observed_evaluated_modifiers = modifiers(*owner.object);
    if (grids.subdiv) { row.subdivision = grids.subdiv->settings; }
    row.total_level = owner.evaluated_mesh->runtime->subdiv_ccg_tot_level;
    row.verts_num = mesh->verts_num;
    row.edges = Array<int2, 0>(mesh->edges());
    row.face_offsets = Array<int, 0>(mesh->face_offsets());
    row.corner_verts = Array<int, 0>(mesh->corner_verts());
    row.corner_edges = Array<int, 0>(mesh->corner_edges());
    row.grid_to_face = Array<int, 0>(grids.grid_to_face_map);
    row.level = grids.level; row.grid_size = grids.grid_size;
    row.grid_area = grids.grid_area; row.grids_num = grids.grids_num;
    row.positions = Array<float3, 0>(grids.positions.as_span());
    row.normals = Array<float3, 0>(grids.normals.as_span());
    row.masks = Array<float, 0>(grids.masks.as_span());
    row.hidden = hidden_copy<0>(grids.grid_hidden);
    if (authority && ss->pbvh && ss->pbvh->type() == bke::pbvh::Type::Grids) {
      row.pending_normal_faces = ss->pbvh->capture_grid_normal_update_faces(grids);
    }
    row.dirty_coords = grids.dirty.coords; row.dirty_hidden = grids.dirty.hidden;
    row.observed_graph = owner.graph.depsgraph;
    /* Prepare native-capacity storage and the exact authority tree before the suspended
     * operation can resume. Publication later only moves these admitted buffers. */
    const uint64_t tree_items = uint64_t(grids.positions.size()) + uint64_t(mesh->verts_num) +
                                uint64_t(mesh->faces_num) + uint64_t(mesh->corners_num);
    if (tree_items > (SIZE_MAX - 65536) / 4096 ||
        !charge_array(sizeof(SubdivCCG) + grids.positions.as_span().size_in_bytes() +
                      grids.normals.as_span().size_in_bytes() + grids.masks.as_span().size_in_bytes()) ||
        !charge_array(size_t((grids.grid_hidden.all_bits().size() + 63) / 64) * 8) ||
        (authority && !charge_array(65536 + size_t(tree_items) * 4096))) {
      *r_error = "Checkpoint fine replay staging exceeds finite admission"; return nullptr;
    }
    row.replay_arrays = std::make_unique<SubdivCCG>();
    row.replay_arrays->positions = Array<float3>(row.positions.as_span());
    row.replay_arrays->normals = Array<float3>(row.normals.as_span());
    row.replay_arrays->masks = Array<float>(row.masks.as_span());
    row.replay_arrays->grid_hidden = hidden_for_restore(row.hidden);
    row.replay_arrays->level = grids.level; row.replay_arrays->grid_size = grids.grid_size;
    row.replay_arrays->grid_area = grids.grid_area; row.replay_arrays->grids_num = grids.grids_num;
    row.replay_arrays->faces = mesh->faces();
    row.replay_arrays->grid_to_face_map = grids.grid_to_face_map;
    if (authority) {
      row.replay_tree = std::make_unique<bke::pbvh::Tree>(
          bke::pbvh::Tree::from_grids(*mesh, *row.replay_arrays));
      row.replay_tree->restore_grid_normal_update_faces(Array<int, 0>(row.pending_normal_faces));
    }
    state->owners.append(std::move(row));
  }
  state->memory_size = used;
  return state.release();
}

bool main_undo_grids_replay_checkpoint(Main &bmain, MainUndoGridState &state,
                                      const bool publish, const char **r_error)
{
  if (!DEG_checkpoint_runtime_observation_allowed(&bmain)) {
    *r_error = "Fine replay requires the suspended protected no-worker lease"; return false;
  }
  /* Allocation-free two-phase caller: resolve current typed membership every time, and never
   * dereference the captured cache or evaluated-ID addresses after native work resumes. */
  for (GridOwner &saved : state.owners) {
    Object *original = nullptr; const Mesh *original_mesh = nullptr;
    for (Object &object : bmain.objects) {
      if (object.id.session_uid == saved.object_uid) { original = &object; break; }
    }
    for (const Mesh &candidate : bmain.meshes) {
      if (candidate.id.session_uid == saved.observed_original_mesh_uid) { original_mesh = &candidate; break; }
    }
    if (!original || !original_mesh || original->data != &original_mesh->id || !saved.replay_arrays) {
      *r_error = "Edited fine replay original relation no longer exists"; return false;
    }
    SubdivCCG *destination = nullptr;
    const Mesh *base = nullptr;
    const bool registered = DEG_foreach_registered_graph(&bmain, [&](const DEGRegisteredGraph &graph) {
      if (graph.depsgraph != saved.observed_graph) { return true; }
      if (!graph.scene || !graph.view_layer ||
          graph.scene->id.session_uid != saved.scene_uid ||
          std::strcmp(graph.view_layer->name, saved.view_layer) != 0) { return false; }
      return DEG_checkpoint_foreach_evaluated_ID(graph.depsgraph, [&](const DEGEvaluatedID &id) {
        if (id.original_type != ID_OB || id.original_session_uid != saved.object_uid ||
            id.evaluated_id != reinterpret_cast<const ID *>(saved.observed_object)) { return true; }
        const Object &evaluated = *reinterpret_cast<const Object *>(id.evaluated_id);
        if (!evaluated.runtime->is_data_eval_owned) { return false; }
        const bool owns_base = DEG_checkpoint_foreach_evaluated_ID(graph.depsgraph, [&](const DEGEvaluatedID &source) {
          if (source.original_type == ID_ME && source.original_session_uid == saved.mesh_uid &&
              source.evaluated_id == evaluated.runtime->data_orig) {
            base = reinterpret_cast<const Mesh *>(source.evaluated_id);
          }
          return true;
        });
        const Mesh *mesh = BKE_object_get_evaluated_mesh_no_subsurf_unchecked(&evaluated);
        if (owns_base && base && mesh) { destination = mesh->runtime->subdiv_ccg.get(); }
        return owns_base;
      });
    });
    if (!registered || !base || !destination || !destination->subdiv ||
        !values_equal(saved.edges.as_span(), base->edges()) ||
        !values_equal(saved.face_offsets.as_span(), base->face_offsets()) ||
        !values_equal(saved.corner_verts.as_span(), base->corner_verts()) ||
        !values_equal(saved.corner_edges.as_span(), base->corner_edges()) ||
        !subdivision_equal(destination->subdiv->settings, saved.subdivision) ||
        destination->level != saved.level || destination->grid_size != saved.grid_size ||
        destination->grid_area != saved.grid_area || destination->grids_num != saved.grids_num ||
        !values_equal(saved.grid_to_face.as_span(), destination->grid_to_face_map) ||
        (saved.authority && (!original->runtime->sculpt_session || !saved.replay_tree))) {
      *r_error = "Edited fine replay has no compatible live owned graph destination"; return false;
    }
    if (publish) {
      destination->positions = std::move(saved.replay_arrays->positions);
      destination->normals = std::move(saved.replay_arrays->normals);
      destination->masks = std::move(saved.replay_arrays->masks);
      destination->grid_hidden = std::move(saved.replay_arrays->grid_hidden);
      destination->dirty.coords = saved.dirty_coords; destination->dirty.hidden = saved.dirty_hidden;
      if (saved.authority) {
        SculptSession &ss = *original->runtime->sculpt_session;
        ss.subdiv_ccg = destination; ss.pbvh = std::move(saved.replay_tree);
      }
    }
  }
  return true;
}

void main_undo_grids_free(MainUndoGridState *state)
{
  delete state;
}

size_t main_undo_grids_memory_size(const MainUndoGridState &state)
{
  return state.memory_size;
}

bool main_undo_grids_has_object(const MainUndoGridState &state, const uint32_t object_uid)
{
  const auto *owner = std::lower_bound(
      state.owners.begin(), state.owners.end(), object_uid,
      [](const GridOwner &candidate, const uint32_t uid) { return candidate.object_uid < uid; });
  return owner != state.owners.end() && owner->object_uid == object_uid;
}

bool main_undo_grids_equals(const MainUndoGridState &a, const MainUndoGridState &b)
{
  if (a.owners.size() != b.owners.size()) {
    return false;
  }
  for (const int64_t i : a.owners.index_range()) {
    const GridOwner &x = a.owners[i];
    const GridOwner &y = b.owners[i];
    if (!key_equal(x, y) || x.authority != y.authority ||
        x.observed_cache != y.observed_cache || x.observed_mesh != y.observed_mesh ||
        x.observed_object != y.observed_object ||
        x.observed_original_mesh_uid != y.observed_original_mesh_uid || !profile_equal(x.graph, y.graph) ||
        !profile_equal(x.modifier, y.modifier) ||
        !values_equal(x.modifier_order.as_span(), y.modifier_order.as_span()) ||
        !values_equal(x.observed_modifiers.as_span(), y.observed_modifiers.as_span()) ||
        !values_equal(x.observed_evaluated_modifiers.as_span(), y.observed_evaluated_modifiers.as_span()) ||
        !profile_equal(x.evaluated_modifier, y.evaluated_modifier) ||
        !values_equal(x.evaluated_modifier_order.as_span(), y.evaluated_modifier_order.as_span()) ||
        !subdivision_equal(x.subdivision, y.subdivision) || x.total_level != y.total_level ||
        x.verts_num != y.verts_num || !values_equal(x.edges.as_span(), y.edges.as_span()) ||
        !values_equal(x.face_offsets.as_span(), y.face_offsets.as_span()) ||
        !values_equal(x.corner_verts.as_span(), y.corner_verts.as_span()) ||
        !values_equal(x.corner_edges.as_span(), y.corner_edges.as_span()) ||
        !values_equal(x.grid_to_face.as_span(), y.grid_to_face.as_span()) || x.level != y.level ||
        x.grid_size != y.grid_size || x.grid_area != y.grid_area || x.grids_num != y.grids_num ||
        !values_equal(x.positions.as_span(), y.positions.as_span()) ||
        !values_equal(x.normals.as_span(), y.normals.as_span()) ||
        !values_equal(x.masks.as_span(), y.masks.as_span()) || !hidden_equal(x.hidden, y.hidden) ||
        !values_equal(x.pending_normal_faces.as_span(), y.pending_normal_faces.as_span()) ||
        x.dirty_coords != y.dirty_coords || x.dirty_hidden != y.dirty_hidden)
    {
      return false;
    }
  }
  return true;
}

#ifdef WITH_GTESTS
uint32_t main_undo_grids_test_owner_count(const MainUndoGridState &state, const uint32_t object_uid)
{
  uint32_t count = 0;
  for (const GridOwner &owner : state.owners) {
    count += owner.object_uid == object_uid;
  }
  return count;
}
#endif

bool main_undo_grids_collect_owner_keys(Main &bmain,
                                      Vector<MainUndoGridOwnerKey> &r_owners,
                                      const char **r_error)
{
  *r_error = nullptr;
  r_owners.clear();
  Vector<LiveOwner> owners;
  if (!collect_live_owners(bmain, false, owners, r_error)) {
    return false;
  }
  for (const LiveOwner &owner : owners) {
    r_owners.append(owner.key);
  }
  return true;
}

bool main_undo_grids_suppress_old_writeback(Main &bmain, const char **r_error)
{
  *r_error = nullptr;
  Vector<SubdivCCG *> grids;
  /* Retirement needs owned evaluated caches, not restorable original identities. A failed reader
   * can leave data_orig absent from its graph; following it would make cleanup depend on exactly
   * the source identity that failed. Match object_update_from_subsurf_ccg's ownership checks. */
  const bool complete = DEG_foreach_registered_graph(&bmain, [&](const DEGRegisteredGraph &graph) {
    if (graph.source_main != &bmain || graph.is_evaluating) {
      *r_error = "The registered Multires caches are not idle and exclusively owned";
      return false;
    }
    return DEG_foreach_evaluated_ID(graph.depsgraph, [&](const DEGEvaluatedID &id) {
      if (id.original_type != ID_OB) {
        return true;
      }
      const Object *object = id_cast<const Object *>(id.evaluated_id);
      if (object->type != OB_MESH || !object->runtime->is_data_eval_owned) {
        return true;
      }
      const Mesh *mesh = BKE_object_get_evaluated_mesh_no_subsurf_unchecked(object);
      if (mesh && mesh->runtime->subdiv_ccg) {
        grids.append(mesh->runtime->subdiv_ccg.get());
      }
      return true;
    });
  });
  if (!complete) {
    if (!*r_error) {
      *r_error = "The registered Multires caches cannot be safely retired";
    }
    return false;
  }
  /* No failure or allocation follows the first flag change. Repeated CCG pointers are harmless. */
  for (SubdivCCG *grid : grids) {
    grid->dirty.coords = false;
    grid->dirty.hidden = false;
  }
  return true;
}

namespace {

struct PreparedGrid {
  SubdivCCG *destination;
  std::unique_ptr<SubdivCCG> arrays;
  bool dirty_coords;
  bool dirty_hidden;
};

struct PreparedAuthority {
  Object *object;
  SubdivCCG *destination;
  std::unique_ptr<bke::pbvh::Tree> tree;
};

bool empty_sessions(const Main &bmain, const char **r_error)
{
  for (const Object &object : bmain.objects) {
    const SculptSession *ss = object.runtime->sculpt_session;
    if (ss && (ss->bm || ss->bm_log || ss->pbvh || ss->subdiv_ccg || ss->multires_modifier ||
               ss->shapekey_active || ss->cache || ss->filter_cache || ss->expand_cache))
    {
      *r_error = "Multires Undo preparation requires empty temporary Sculpt sessions";
      return false;
    }
  }
  return true;
}

struct RestoredOwner {
  const GridOwner *saved;
  Object *object;
  Scene *scene;
  ViewLayer *view_layer;
};

bool resolve_owners(Main &bmain,
                    const MainUndoGridState &state,
                    Vector<RestoredOwner> &r_owners,
                    const char **r_error)
{
  Map<uint32_t, Object *> objects;
  Map<uint32_t, Scene *> scenes;
  for (Object &object : bmain.objects) {
    if (!object.id.session_uid || !objects.add(object.id.session_uid, &object)) {
      *r_error = "The restored document has duplicate Object identities";
      return false;
    }
  }
  for (Scene &scene : bmain.scenes) {
    if (!scene.id.session_uid || !scenes.add(scene.id.session_uid, &scene)) {
      *r_error = "The restored document has duplicate Scene identities";
      return false;
    }
  }
  for (const GridOwner &saved : state.owners) {
    Object *object = objects.lookup_default(saved.object_uid, nullptr);
    Scene *scene = scenes.lookup_default(saved.scene_uid, nullptr);
    ViewLayer *view_layer = nullptr;
    if (scene) {
      for (ViewLayer &candidate : scene->view_layers) {
        if (STREQ(candidate.name, saved.view_layer)) {
          if (view_layer) {
            *r_error = "The restored Multires view-layer identity is ambiguous";
            return false;
          }
          view_layer = &candidate;
        }
      }
    }
    if (!object || object->type != OB_MESH || !object->data ||
        object->data->session_uid != saved.mesh_uid || !(object->mode & OB_MODE_SCULPT) ||
        !object->runtime->sculpt_session || !scene || !view_layer)
    {
      *r_error = "The saved Multires authority is missing from the restored document";
      return false;
    }
    r_owners.append({&saved, object, scene, view_layer});
  }
  return true;
}

/** Both barriers inspect the complete saved owner set, including owners without a PBVH. */
bool restored_owners_match(Main &bmain,
                           const MainUndoGridState &state,
                           const bool require_saved_authority,
                           Vector<LiveOwner> &r_owners,
                           const char **r_error)
{
  if (!collect_live_owners(bmain, true, r_owners, r_error)) {
    return false;
  }
  if (r_owners.size() != state.owners.size()) {
    *r_error = "The restored Multires owner set changed";
    return false;
  }
  for (const int64_t i : state.owners.index_range()) {
    const GridOwner &saved = state.owners[i];
    const LiveOwner &owner = r_owners[i];
    const EvaluatedOwner &evaluated = owner.evaluated;
    const Mesh &mesh = *id_cast<const Mesh *>(owner.original->data);
    const SculptSession &ss = *owner.original->runtime->sculpt_session;
    OwnerModifiers modifiers;
    if (!key_equal(saved, owner.key) ||
        (require_saved_authority && saved.authority != owner.key.authority) ||
        !owner_modifiers(owner, require_saved_authority, modifiers, r_error) ||
        !profile_equal(saved.graph, profile(evaluated)) ||
        !profile_equal(saved.modifier, profile(*modifiers.original)) ||
        !profile_equal(saved.evaluated_modifier, profile(*modifiers.evaluated)) ||
        !values_equal(saved.modifier_order.as_span(), modifier_order(*owner.original).as_span()) ||
        !values_equal(saved.evaluated_modifier_order.as_span(),
                      modifier_order(*evaluated.object).as_span()) ||
        !subdivision_equal(saved.subdivision, evaluated.grids->subdiv->settings) ||
        saved.total_level != evaluated.evaluated_mesh->runtime->subdiv_ccg_tot_level ||
        !grid_shape_valid(mesh, *evaluated.grids, r_error) ||
        !topology_matches(saved, mesh, *evaluated.grids) || ss.bm || ss.bm_log || ss.cache ||
        ss.filter_cache || ss.expand_cache ||
        (require_saved_authority && owner.key.authority &&
         (!ss.pbvh || ss.pbvh->type() != bke::pbvh::Type::Grids)))
    {
      if (!*r_error) {
        *r_error = "The restored Multires owner, topology, modifier or evaluation profile changed";
      }
      return false;
    }
  }
  return true;
}

}  // namespace

struct MainUndoGridRestore {
  Vector<PreparedGrid> owners;
  Vector<PreparedAuthority> authorities;
};

MainUndoGridRestore *main_undo_grids_prepare(Main &bmain,
                                             const MainUndoGridState &state,
                                             const char **r_error
#ifdef WITH_GTESTS
                                             , MainUndoGridPrepareTestFault *test_fault
#endif
)
{
  *r_error = nullptr;
  if (!BKE_undosys_restore_validate(&bmain)) {
    *r_error = "Multires runtime preparation requires the protected outer Undo traversal";
    return nullptr;
  }
  if (!empty_sessions(bmain, r_error)) {
    return nullptr;
  }
  Vector<RestoredOwner> targets;
  if (!resolve_owners(bmain, state, targets, r_error)) {
    return nullptr;
  }
  Set<Depsgraph *> evaluated;
  const Main *const expected_main = &bmain;
  for (const int64_t i : state.owners.index_range()) {
    /* Each preceding evaluation can run callbacks that remove objects or whole scenes. Resolve
     * identities afresh before using the next graph, never retain an owner across a callback. */
    targets.clear();
    if (!resolve_owners(bmain, state, targets, r_error)) {
      return nullptr;
    }
    const RestoredOwner &target = targets[i];
    Depsgraph *graph = BKE_scene_ensure_depsgraph(&bmain, target.scene, target.view_layer);
    if (!graph) {
      *r_error = "The restored Multires owner graph could not be created";
      return nullptr;
    }
    targets.clear();
    if (evaluated.add(graph)) {
      /* Includes Blender's post-callback second pass. Do not retain evaluated CCG pointers here.
       */
      BKE_scene_graph_evaluated_ensure(graph, &bmain);
      if (G_MAIN != expected_main || !BKE_undosys_restore_validate(&bmain)) {
        *r_error = "A dependency graph callback invalidated the protected Multires Undo owners";
        return nullptr;
      }
      targets.clear();
      if (!resolve_owners(bmain, state, targets, r_error)) {
        return nullptr;
      }
    }
  }
  Vector<LiveOwner> owners;
  if (!restored_owners_match(bmain, state, false, owners, r_error)) {
    return nullptr;
  }
  /* The last evaluated graph can differ from the saved authority. An existing PBVH would otherwise
   * keep the wrong graph's pending normal work after sculpt_update_object changes ss.subdiv_ccg.
   * Reset only disposable G0 session data, once per Object; never free owned evaluated caches. */
  for (const int64_t i : state.owners.index_range()) {
    if (!state.owners[i].authority) {
      continue;
    }
    const LiveOwner &owner = owners[i];
    Object &object = *const_cast<Object *>(owner.original);
    BKE_sculptsession_free_pbvh(object);
    BKE_sculptsession_free_deformMats(object.runtime->sculpt_session);
    BKE_sculpt_update_object_for_edit(
        const_cast<Depsgraph *>(owner.evaluated.graph.depsgraph), &object, false);
  }
  /* This helper can rebuild PBVH, normals and visibility on G0. Requery every owner before retaining
   * final CCG pointers. There is no evaluation or callback-bearing work after this barrier. */
  owners.clear();
  if (!BKE_undosys_restore_validate(&bmain) ||
      !restored_owners_match(bmain, state, true, owners, r_error))
  {
    if (!*r_error) {
      *r_error = "The protected Multires owners changed during authority preparation";
    }
    return nullptr;
  }
  auto restore = std::make_unique<MainUndoGridRestore>();
#ifdef WITH_GTESTS
  uint32_t previous_object_uid = 0;
  uint32_t owner_index = 0;
#endif
  for (const int64_t i : state.owners.index_range()) {
    const GridOwner &saved = state.owners[i];
    const LiveOwner &owner = owners[i];
    uint32_t expected_object_uid = saved.object_uid;
#ifdef WITH_GTESTS
    owner_index = previous_object_uid == saved.object_uid ? owner_index + 1 : 0;
    previous_object_uid = saved.object_uid;
    if (test_fault && !test_fault->consumed && test_fault->object_uid == saved.object_uid &&
        test_fault->owner_index == owner_index)
    {
      test_fault->consumed = true;
      test_fault->consumed_scene_uid = saved.scene_uid;
      expected_object_uid = 0; /* Exercise the real row binding validator; mutate no saved data. */
    }
#endif
    if (owner.original->id.session_uid != expected_object_uid || !owner.original->data ||
        owner.original->data->session_uid != saved.mesh_uid)
    {
      *r_error = "A prepared Multires owner no longer matches its saved Object and Mesh";
      return nullptr;
    }
    const Mesh &mesh = *id_cast<const Mesh *>(owner.original->data);
    PreparedGrid prepared;
    prepared.destination = owner.evaluated.grids;
    prepared.arrays = std::make_unique<SubdivCCG>();
    SubdivCCG &arrays = *prepared.arrays;
    arrays.level = saved.level;
    arrays.grid_size = saved.grid_size;
    arrays.grid_area = saved.grid_area;
    arrays.grids_num = saved.grids_num;
    arrays.faces = mesh.faces();
    arrays.grid_to_face_map = saved.grid_to_face.as_span();
    arrays.positions = Array<float3>(saved.positions.as_span());
    arrays.normals = Array<float3>(saved.normals.as_span());
    arrays.masks = Array<float>(saved.masks.as_span());
    arrays.grid_hidden = hidden_for_restore(saved.hidden);
    prepared.dirty_coords = saved.dirty_coords;
    prepared.dirty_hidden = saved.dirty_hidden;
    if (saved.authority) {
      PreparedAuthority authority;
      authority.object = const_cast<Object *>(owner.original);
      authority.destination = prepared.destination;
      authority.tree = std::make_unique<bke::pbvh::Tree>(bke::pbvh::Tree::from_grids(mesh, arrays));
      authority.tree->restore_grid_normal_update_faces(Array<int, 0>(saved.pending_normal_faces));
      restore->authorities.append(std::move(authority));
    }
    restore->owners.append(std::move(prepared));
  }
  return restore.release();
}

bool main_undo_grids_restore_has_object(const MainUndoGridRestore &restore,
                                        const uint32_t object_uid)
{
  const auto *authority = std::lower_bound(
      restore.authorities.begin(), restore.authorities.end(), object_uid,
      [](const PreparedAuthority &candidate, const uint32_t uid) {
        return candidate.object->id.session_uid < uid;
      });
  return authority != restore.authorities.end() && authority->object->id.session_uid == object_uid;
}

void main_undo_grids_commit(MainUndoGridRestore *restore)
{
  BLI_assert(restore);
  /* All exact arrays and dirty flags precede the single editing authority for every Object. */
  for (PreparedGrid &prepared : restore->owners) {
    SubdivCCG &destination = *prepared.destination;
    destination.positions = std::move(prepared.arrays->positions);
    destination.normals = std::move(prepared.arrays->normals);
    destination.masks = std::move(prepared.arrays->masks);
    destination.grid_hidden = std::move(prepared.arrays->grid_hidden);
    destination.dirty.coords = prepared.dirty_coords;
    destination.dirty.hidden = prepared.dirty_hidden;
  }
  for (PreparedAuthority &authority : restore->authorities) {
    SculptSession &ss = *authority.object->runtime->sculpt_session;
    BLI_assert(ss.subdiv_ccg == authority.destination);
    ss.pbvh = std::move(authority.tree);
    /* Object-level needs_flush_to_id is restored once by the parent session owner. */
    ss.angelo_undo_grid_visibility.reset();
  }
  delete restore;
}

void main_undo_grids_discard(MainUndoGridRestore *restore)
{
  delete restore;
}

#ifdef WITH_GTESTS
bool main_undo_grids_observe(Main &bmain,
                             Vector<MainUndoGridObservation> &r_observations,
                             const char **r_error)
{
  *r_error = nullptr;
  Vector<EvaluatedOwner> owners;
  if (!inventory(bmain, owners, r_error)) {
    if (!*r_error) {
      *r_error = "The Multires observation inventory is unavailable";
    }
    return false;
  }
  Map<uint32_t, const Object *> objects;
  for (const Object &object : bmain.objects) {
    objects.add(object.id.session_uid, &object);
  }
  for (const EvaluatedOwner &owner : owners) {
    const Object *object = objects.lookup_default(owner.object_uid, nullptr);
    const SculptSession *ss = object ? object->runtime->sculpt_session : nullptr;
    const SubdivCCG &grids = *owner.grids;
    MainUndoGridObservation observation;
    observation.object_uid = owner.object_uid;
    observation.mesh_uid = owner.mesh_uid;
    observation.scene_uid = owner.graph.scene ? owner.graph.scene->id.session_uid : 0;
    observation.scene_original_session_uid = owner.graph.scene_original_session_uid;
    observation.view_layer = owner.graph.view_layer ? owner.graph.view_layer->name : "";
    observation.graph_address = uintptr_t(owner.graph.depsgraph);
    observation.ccg_address = uintptr_t(owner.grids);
    observation.frame = owner.graph.frame;
    observation.ctime = owner.graph.ctime;
    observation.canonical = canonical(owner.graph);
    observation.viewport = owner.graph.mode == DAG_EVAL_VIEWPORT;
    observation.active = owner.graph.is_active;
    observation.visibility_optimization = owner.graph.use_visibility_optimization;
    observation.render_pipeline = owner.graph.is_render_pipeline;
    observation.editors_update = owner.graph.use_editors_update;
    observation.sync_writeback = int(owner.graph.sync_writeback);
    observation.debug_flags = owner.graph.debug_flags;
    observation.pending_updates = owner.graph.has_pending_updates ||
                                  owner.graph.need_update_relations ||
                                  owner.graph.need_update_nodes_visibility;
    observation.authority = ss && ss->subdiv_ccg == &grids;
    observation.dirty_coords = grids.dirty.coords;
    observation.dirty_hidden = grids.dirty.hidden;
    observation.session_needs_flush = ss && ss->needs_flush_to_id;
    observation.level = grids.level;
    observation.grid_size = grids.grid_size;
    observation.grids_num = grids.grids_num;
    observation.face_offsets = Array<int, 0>(grids.faces.data());
    observation.grid_to_face = Array<int, 0>(grids.grid_to_face_map);
    observation.positions = Array<float3, 0>(grids.positions.as_span());
    observation.normals = Array<float3, 0>(grids.normals.as_span());
    observation.masks = Array<float, 0>(grids.masks.as_span());
    observation.hidden = hidden_copy<0>(grids.grid_hidden);
    if (observation.authority && ss->pbvh && ss->pbvh->type() == bke::pbvh::Type::Grids) {
      observation.pending_normal_faces = ss->pbvh->capture_grid_normal_update_faces(grids);
    }
    r_observations.append(std::move(observation));
  }
  return true;
}
#endif

bool main_undo_grid_snapshot_visit(const MainUndoGridState &state,
                                   FunctionRef<bool(const SnapshotGridView &)> visit)
{
  const auto modifier_view = [](const ModifierProfile &p) -> SnapshotModifierProfile {
    return {p.uid, int(p.mode), p.level, p.sculpt_level, p.render_level, p.total_level,
            int(p.flags), p.quality, p.uv_smooth, p.boundary_smooth};
  };
  for (const GridOwner &owner : state.owners) {
    SnapshotGridView view;
    view.object_uid = owner.object_uid;
    view.mesh_uid = owner.mesh_uid;
    view.scene_uid = owner.scene_uid;
    view.view_layer = owner.view_layer;
    view.authority = owner.authority;
    const GraphProfile &p = owner.graph;
    view.graph = {p.visibility_optimization, p.editors_update, int(p.sync_writeback), p.debug_flags,
                  p.scene_mode, p.simplify_subsurf, p.frame, p.ctime, p.evaluated_scene_mode,
                  p.evaluated_simplify_subsurf, p.evaluated_frame, p.evaluated_ctime};
    view.modifier = modifier_view(owner.modifier);
    view.evaluated_modifier = modifier_view(owner.evaluated_modifier);
    view.modifier_order = owner.modifier_order.as_span();
    view.evaluated_modifier_order = owner.evaluated_modifier_order.as_span();
    view.subdivision = owner.subdivision;
    view.total_level = owner.total_level;
    view.verts_num = owner.verts_num;
    view.edges = owner.edges.as_span();
    view.face_offsets = owner.face_offsets.as_span();
    view.corner_verts = owner.corner_verts.as_span();
    view.corner_edges = owner.corner_edges.as_span();
    view.grid_to_face = owner.grid_to_face.as_span();
    view.level = owner.level;
    view.grid_size = owner.grid_size;
    view.grid_area = owner.grid_area;
    view.grids_num = owner.grids_num;
    view.positions = owner.positions.as_span();
    view.normals = owner.normals.as_span();
    view.masks = owner.masks.as_span();
    view.hidden = {owner.hidden.all_bits(), owner.hidden.size(), owner.hidden.group_size(),
                   owner.hidden.is_empty() ? 0 : owner.hidden.all_bits().size() / owner.hidden.size()};
    view.pending_normal_faces = owner.pending_normal_faces.as_span();
    view.dirty_coords = owner.dirty_coords;
    view.dirty_hidden = owner.dirty_hidden;
    if (!visit(view)) {
      return false;
    }
  }
  return true;
}

}  // namespace blender::ed::sculpt_paint::undo
