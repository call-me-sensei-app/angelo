/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 * SPDX-License-Identifier: GPL-2.0-or-later */
/** Selected cold Sculpt installation; no source-UID recovery or unrelated runtime disposal. */
#include <algorithm>
#include <string>

#include <climits>
#include <cstring>
#include <memory>
#include <utility>
#include "MEM_guardedalloc.h"
#include "BLI_listbase.h"
#include "BLI_generic_pointer.hh"
#include "BLI_set.hh"
#include "BLI_threads.h"
#include "BKE_customdata.hh"
#include "BKE_asset_edit.hh"
#include "BKE_collection.hh"
#include "BKE_callbacks.hh"
#include "BKE_global.hh"
#include "BKE_attribute.hh"
#include "BKE_attribute_storage.hh"
#include "BKE_main.hh"
#include "BKE_mesh.hh"
#include "BKE_mesh_runtime.hh"
#include "BKE_mesh_types.hh"
#include "BKE_object.hh"
#include "BKE_object_types.hh"
#include "BKE_paint.hh"
#include "BKE_customdata.hh"
#include "BLI_implicit_sharing.hh"
#include "BKE_paint_bvh.hh"
#include "BKE_report.hh"
#include "BKE_scene.hh"
#include "BKE_sculpt_undo.hh"
#include "BKE_subdiv_ccg.hh"
#include "BKE_undo_system.hh"
#include "BLO_readfile.hh"
#include "DNA_asset_types.h"
#include "DNA_brush_types.h"
#include "DNA_key_types.h"
#include "DNA_layer_types.h"
#include "DNA_mesh_types.h"
#include "DNA_modifier_types.h"
#include "DNA_object_types.h"
#include "DNA_scene_types.h"
#include "DEG_depsgraph.hh"
#include "DEG_depsgraph_debug.hh"
#include "DEG_depsgraph_query.hh"
#include "bmesh.hh"
#include "intern/bmesh_log.hh"
#include "ED_sculpt.hh"
#include "../../editors/sculpt_paint/mesh/sculpt_main_undo_snapshot_access.hh"
#include "ED_undo.hh"
#include "wm_checkpoint.hh"
namespace blender {
/* Joined C metadata-only safety gate; it remains false after worker/unwitnessed callbacks. */
bool BKE_checkpoint_callback_observation_safe(const Main *main);
struct WMCheckpointColdCallback;
/* Detached target bindings and coarse coordinate/Key fingerprints are retained before LOAD_POST.
 * Fine arrays remain per graph owner; no mesh-level collapse or source-UID recovery lookup. */
struct WMCheckpointColdInstall {
  struct CoarseWitness {
    Array<float3> positions;
    Array<int2> edges;
    Array<int> face_offsets, corner_verts, corner_edges;
    Array<float> mask;
    Array<bool> hide_vert, hide_edge, hide_poly;
    std::array<bool, 4> attribute_present{};
    Vector<Array<std::byte>> key_data;
    Vector<int> key_elements;
    /** The Mesh attribute layout before LOAD_POST. A Dyntopo install replaces Mesh attributes
     * from its BMesh at the next flush, so a callback's layout change there is an edit. */
    Vector<std::string> attribute_layout;
  };
  struct Target {
    Object *object;
    Mesh *mesh;
    Key *key;
    uint32_t object_uid, mesh_uid, key_uid;
    SculptSession *installer_session = nullptr;
    std::shared_ptr<CoarseWitness> coarse;
    bool dyntopo = false;
  };
  struct EditedRelation {
    uint32_t object_uid, mesh_uid, key_uid;
    int mode, shape_index, shape_flags;
  };
  struct EditedKeyBlock { KeyBlock *block; int elements; void *data; };
  struct EditedAttribute {
    const char *name;
    bke::AttrDomain domain;
    bke::AttrType type;
    int64_t elements;
    bool array_only;
    bke::Attribute::DataVariant data;
  };
  struct EditedMesh {
    uint32_t mesh_uid, key_uid;
    int verts, edges, faces, corners, key_element_size;
    Vector<EditedAttribute, 8> attributes;
    int *face_offsets = nullptr;
    const ImplicitSharingInfo *face_sharing = nullptr;
    Vector<EditedKeyBlock, 0> key_blocks;
    /* Every affected SharedCache is constructed under finite admission, not while
     * publishing. This runtime owns no geometry/CCG/evaluated mesh; it is cache staging. */
    std::unique_ptr<bke::MeshRuntime> empty_caches;
    void publish_cache_invalidation(Mesh &mesh)
    {
      auto &live = *mesh.runtime;
      auto &empty = *empty_caches;
      live.bounds_cache = std::move(empty.bounds_cache);
      live.bvh_cache_verts = std::move(empty.bvh_cache_verts);
      live.bvh_cache_edges = std::move(empty.bvh_cache_edges);
      live.bvh_cache_faces = std::move(empty.bvh_cache_faces);
      live.bvh_cache_corner_tris = std::move(empty.bvh_cache_corner_tris);
      live.bvh_cache_corner_tris_no_hidden = std::move(empty.bvh_cache_corner_tris_no_hidden);
      live.bvh_cache_loose_verts = std::move(empty.bvh_cache_loose_verts);
      live.bvh_cache_loose_verts_no_hidden = std::move(empty.bvh_cache_loose_verts_no_hidden);
      live.bvh_cache_loose_edges = std::move(empty.bvh_cache_loose_edges);
      live.bvh_cache_loose_edges_no_hidden = std::move(empty.bvh_cache_loose_edges_no_hidden);
      live.vert_to_face_offset_cache = std::move(empty.vert_to_face_offset_cache);
      live.vert_to_face_map_cache = std::move(empty.vert_to_face_map_cache);
      live.vert_to_corner_map_cache = std::move(empty.vert_to_corner_map_cache);
      live.corner_to_face_map_cache = std::move(empty.corner_to_face_map_cache);
      live.vert_normals_cache = std::move(empty.vert_normals_cache);
      live.vert_normals_true_cache = std::move(empty.vert_normals_true_cache);
      live.face_normals_cache = std::move(empty.face_normals_cache);
      live.face_normals_true_cache = std::move(empty.face_normals_true_cache);
      live.corner_normals_cache = std::move(empty.corner_normals_cache);
      live.loose_edges_cache = std::move(empty.loose_edges_cache);
      live.loose_verts_cache = std::move(empty.loose_verts_cache);
      live.verts_no_face_cache = std::move(empty.verts_no_face_cache);
      live.corner_tri_faces_cache = std::move(empty.corner_tri_faces_cache);
      live.shrinkwrap_boundary_cache = std::move(empty.shrinkwrap_boundary_cache);
      live.max_material_index = std::move(empty.max_material_index);
      /* Preserve triangulation freeze metadata, while its old connectivity is dirty. */
      live.corner_tris_cache.data = std::move(empty.corner_tris_cache.data);
      live.corner_tris_cache.dirty_while_frozen = live.corner_tris_cache.frozen;
      live.subsurf_face_dot_tags.clear_and_shrink();
      live.subsurf_optimal_display_edges.clear_and_shrink();
      live.spatial_groups.reset();
      mesh.flag &= ~(ME_NO_OVERLAPPING_TOPOLOGY | ME_FLAG_UV_SELECT_SYNC_VALID);
      /* Unlike generic topology dirtying, do not reset subdiv_ccg: fine replay just
       * independently validated and published its exact owned CCG authority. */
    }
    ~EditedMesh() {
      if (face_sharing) { face_sharing->remove_user_and_delete_if_last(); }
      for (auto &block : key_blocks) { if (block.data) { MEM_delete_void(block.data); } }
    }
  };
  struct EditedCoarse {
    Vector<EditedRelation, 0> relations;
    Vector<std::unique_ptr<EditedMesh>, 0> meshes;
  };
  std::unique_ptr<EditedCoarse> conflict_coarse;
  ed::sculpt_paint::checkpoint::DecodePlan *plan = nullptr;
  Main *main = nullptr;
  Vector<Target> targets;
  Vector<Scene *> scenes;
  Vector<ViewLayer *> layers;
  Vector<uint32_t> scene_uids;
  CheckpointCaptureOwner *owner = nullptr; /* Borrowed; outer WM transaction owns its lifetime. */
  using RuntimeState = ed::sculpt_paint::undo::MainUndoState;
  struct RuntimeIdentity {
    const SculptSession *session;
    const bke::pbvh::Tree *pbvh;
    const BMesh *mesh;
    const BMLog *log;
    const SubdivCCG *ccg;
  };
  DEGCheckpointRuntimeScope *evaluation_scope = nullptr;
  Vector<uint32_t> selected;
  Set<WMCheckpointColdCallback *> callback_tokens;
  size_t coarse_limit = 0;
  size_t coarse_charge = 0;
  size_t callback_budget = 0;
  size_t callback_reserved = 0;
  std::unique_ptr<RuntimeState, decltype(&ed::sculpt_paint::undo::main_undo_state_free)>
      conflict_state{nullptr, ed::sculpt_paint::undo::main_undo_state_free};
  size_t conflict_reserved = 0;
  bool callback_observation_failed = false;
  ReportList *runtime_reports = nullptr; /* Borrowed by the synchronous Restore lease. */
  bool preservation_busy = false;
  bool preservation_attempted = false;
  bool preservation_complete = false;
  bool runtime_ready = false;
  bool callback_conflict = false;
  bool installed = false;
};

template<typename T> static bool checkpoint_values_equal(Span<T> a, Span<T> b)
{
  return a.size() == b.size() &&
         (a.is_empty() || std::memcmp(a.data(), b.data(), a.size_in_bytes()) == 0);
}

static bool checkpoint_float_equal(const float a, const float b)
{
  return std::memcmp(&a, &b, sizeof(float)) == 0;
}

static ID *checkpoint_resolved(const BLOCheckpointReadTokens &tokens,
                               const uint64_t token, const short code)
{
  for (const BLOCheckpointIDToken &row : tokens.ids) {
    if (row.token == token && row.id_code == code) {
      return row.resolved;
    }
  }
  return nullptr;
}

/** Sorted ``domain:type:name`` of every Mesh attribute. */
static Vector<std::string> checkpoint_attribute_layout(const Mesh &mesh)
{
  Vector<std::string> layout;
  mesh.attributes().foreach_attribute([&](const bke::AttributeIter &iter) {
    layout.append(std::to_string(int(iter.domain)) + ":" + std::to_string(int(iter.data_type)) + ":" +
                  std::string(iter.name));
  });
  std::sort(layout.begin(), layout.end());
  return layout;
}

WMCheckpointColdInstall *WM_checkpoint_cold_prepare(
    Main &main, ed::sculpt_paint::checkpoint::DecodePlan &plan,
    const BLOCheckpointReadTokens &tokens, ReportList *reports)
{
  if (!tokens.complete || plan.objects.is_empty()) {
    return nullptr;
  }
  ed::sculpt_paint::checkpoint::CodecLimits limits;
  if (!WM_checkpoint_memory_limits(limits, 0, reports)) { return nullptr; }
  size_t charged = 0;
  auto charge = [&](const size_t bytes) {
    return ed::sculpt_paint::undo::checkpoint_snapshot_charge(charged, limits.max_backing_bytes, bytes);
  };
  if (!charge(sizeof(WMCheckpointColdInstall)) ||
      plan.objects.size() > INT64_MAX / 4 || plan.grids.size() > INT64_MAX / 4 ||
      !charge(size_t(plan.objects.size()) * (sizeof(WMCheckpointColdInstall::Target) + sizeof(uint32_t)) * 4) ||
      !charge(size_t(plan.grids.size()) * (sizeof(Scene *) + sizeof(ViewLayer *) + sizeof(uint32_t)) * 4)) {
    BKE_report(reports, RPT_ERROR, "Checkpoint coarse owner controls exceed finite admission"); return nullptr;
  }
  auto cold = std::make_unique<WMCheckpointColdInstall>();
  cold->coarse_limit = limits.max_backing_bytes;
  cold->main = &main;
  cold->plan = &plan;
  for (const auto &saved : plan.objects) {
    Object *object = id_cast<Object *>(checkpoint_resolved(tokens, saved.binding.object_token, ID_OB));
    Mesh *mesh = id_cast<Mesh *>(checkpoint_resolved(tokens, saved.binding.mesh_token, ID_ME));
    Key *key = saved.binding.key_token ?
                   id_cast<Key *>(checkpoint_resolved(tokens, saved.binding.key_token, ID_KE)) : nullptr;
    if (!object || !mesh || object->type != OB_MESH || object->data != &mesh->id ||
        mesh->key != key || uint32_t(object->mode) != saved.binding.mode ||
        !(object->mode & OB_MODE_SCULPT) || object->shapenr != saved.binding.shape_key_index ||
        uint32_t(object->shapeflag) != saved.binding.shape_key_flags ||
        (key && key->from != &mesh->id))
    {
      BKE_report(reports, RPT_ERROR, "The selected checkpoint Object/Mesh/Key relations do not match BASE");
      return nullptr;
    }
    WMCheckpointColdInstall::Target target;
    target.object = object;
    target.mesh = mesh;
    target.key = key;
    target.object_uid = object->id.session_uid;
    target.mesh_uid = mesh->id.session_uid;
    target.key_uid = key ? key->id.session_uid : 0;
    target.dyntopo = saved.geometry != nullptr;
    for (const auto &previous : cold->targets) {
      if (previous.mesh == mesh && previous.key == key) { target.coarse = previous.coarse; break; }
    }
    if (!target.coarse) {
      /* One immutable coarse witness per actual Mesh/Key pair. Fourfold count-based backing
       * also covers virtual attribute materialization and vector growth before any lookup. */
      const uint64_t verts = uint64_t(mesh->verts_num), edges = uint64_t(mesh->edges_num);
      const uint64_t faces = uint64_t(mesh->faces_num), corners = uint64_t(mesh->corners_num);
      if (mesh->verts_num < 0 || mesh->edges_num < 0 || mesh->faces_num < 0 || mesh->corners_num < 0 ||
          !charge(sizeof(WMCheckpointColdInstall::CoarseWitness) + 64) ||
          !charge(size_t(verts) * (sizeof(float3) + sizeof(float) + sizeof(bool)) * 4) ||
          !charge(size_t(edges) * (sizeof(int2) + sizeof(bool)) * 4) ||
          !charge(size_t(faces + 1) * (sizeof(int) + sizeof(bool)) * 4) ||
          !charge(size_t(corners) * sizeof(int) * 8)) {
        BKE_report(reports, RPT_ERROR, "Checkpoint shared coarse arrays exceed finite admission"); return nullptr;
      }
      if (key) {
        for (const KeyBlock &block : key->block) {
          if (key->elemsize <= 0 || block.totelem < 0 ||
              uint64_t(block.totelem) > uint64_t(INT64_MAX) / uint64_t(key->elemsize) ||
              (block.totelem && !block.data) ||
              !charge(size_t(block.totelem) * size_t(key->elemsize)) ||
              !charge((sizeof(Array<std::byte>) + sizeof(int)) * 4)) {
            BKE_report(reports, RPT_ERROR, "Checkpoint Key witness exceeds finite admission"); return nullptr;
          }
        }
      }
      target.coarse = std::make_shared<WMCheckpointColdInstall::CoarseWitness>();
    target.coarse->positions = Array<float3>(mesh->vert_positions());
    target.coarse->edges = Array<int2>(mesh->edges());
    target.coarse->face_offsets = Array<int>(mesh->face_offsets());
    target.coarse->corner_verts = Array<int>(mesh->corner_verts());
    target.coarse->corner_edges = Array<int>(mesh->corner_edges());
    const auto attributes = mesh->attributes();
    target.coarse->attribute_present = {attributes.contains(".sculpt_mask"), attributes.contains(".hide_vert"),
                                attributes.contains(".hide_edge"), attributes.contains(".hide_poly")};
    target.coarse->mask = Array<float>(VArraySpan<float>(*attributes.lookup<float>(".sculpt_mask", bke::AttrDomain::Point)));
    target.coarse->hide_vert = Array<bool>(VArraySpan<bool>(*attributes.lookup<bool>(".hide_vert", bke::AttrDomain::Point)));
    target.coarse->hide_edge = Array<bool>(VArraySpan<bool>(*attributes.lookup<bool>(".hide_edge", bke::AttrDomain::Edge)));
    target.coarse->hide_poly = Array<bool>(VArraySpan<bool>(*attributes.lookup<bool>(".hide_poly", bke::AttrDomain::Face)));
    target.coarse->attribute_layout = checkpoint_attribute_layout(*mesh);
    if (key) {
      for (const KeyBlock &block : key->block) {
        if (key->elemsize <= 0 || block.totelem < 0 ||
            uint64_t(block.totelem) > uint64_t(INT64_MAX) / uint64_t(key->elemsize) ||
            (block.totelem && !block.data))
        {
          return nullptr;
        }
        target.coarse->key_elements.append(block.totelem);
        target.coarse->key_data.append(Array<std::byte>(
            Span(static_cast<const std::byte *>(block.data), int64_t(block.totelem) * key->elemsize)));
      }
    }
    }
    cold->selected.append(target.object_uid);
    cold->targets.append(std::move(target));
  }
  for (const auto &saved : plan.grids) {
    bool found = false;
    for (const BLOCheckpointLayerToken &row : tokens.layers) {
      if (row.scene_token == saved.scene_token && row.layer_token == saved.view_layer_token &&
          row.scene && row.layer)
      {
        cold->scenes.append(row.scene);
        cold->layers.append(row.layer);
        cold->scene_uids.append(row.scene->id.session_uid);
        found = true;
        break;
      }
    }
    if (!found) {
      BKE_report(reports, RPT_ERROR, "A saved Sculpt graph's Scene/ViewLayer is missing from BASE");
      return nullptr;
    }
  }
  cold->coarse_charge = charged;
  return cold.release();
}

bool WM_checkpoint_cold_validate(Main &main, const WMCheckpointColdInstall &cold,
                                 const bool before_editors, ReportList *reports)
{
  /* Do not dereference any cached document/ID after a callback until global/live owner
   * identity has been checked. This also requires the outer replacement guard to remain held. */
  if (!BLI_thread_is_main() || G_MAIN != &main || &main != cold.main || cold.callback_conflict ||
      (cold.runtime_ready && !BKE_undosys_checkpoint_capture_validate(cold.owner))) {
    BKE_report(reports, RPT_ERROR, "The live document, its Restore owner or a callback changed during Restore");
    return false;
  }
  size_t ids = 0;
  ID *counted;
  FOREACH_MAIN_ID_BEGIN (&main, counted) {
    if (ids == SIZE_MAX) { return false; }
    ids++;
  }
  FOREACH_MAIN_ID_END;
  if (cold.coarse_charge > cold.coarse_limit ||
      ids > (cold.coarse_limit - cold.coarse_charge) / (sizeof(ID *) * 32) || ids > INT64_MAX) {
    BKE_report(reports, RPT_ERROR, "Checkpoint owner validation scratch exceeds finite admission"); return false;
  }
  Set<ID *> current;
  current.reserve(int64_t(ids));
  ID *id;
  FOREACH_MAIN_ID_BEGIN (&main, id) { current.add(id); }
  FOREACH_MAIN_ID_END;
  for (const int64_t i : cold.targets.index_range()) {
    const auto &target = cold.targets[i];
    const auto &binding = cold.plan->objects[i].binding;
    if (!current.contains(reinterpret_cast<ID *>(target.object)) || !current.contains(reinterpret_cast<ID *>(target.mesh)) ||
        (target.key && !current.contains(reinterpret_cast<ID *>(target.key))) ||
        target.object->id.session_uid != target.object_uid || target.mesh->id.session_uid != target.mesh_uid ||
        (target.key && target.key->id.session_uid != target.key_uid) ||
        target.object->data != &target.mesh->id || target.mesh->key != target.key ||
        uint32_t(target.object->mode) != binding.mode ||
        target.object->shapenr != binding.shape_key_index ||
        uint32_t(target.object->shapeflag) != binding.shape_key_flags ||
        (before_editors && !cold.runtime_ready && target.object->runtime->sculpt_session) ||
        (cold.runtime_ready && target.object->runtime->sculpt_session != target.installer_session) ||
        !checkpoint_values_equal<float3>(target.coarse->positions, target.mesh->vert_positions()) ||
        !checkpoint_values_equal<int2>(target.coarse->edges, target.mesh->edges()) ||
        !checkpoint_values_equal<int>(target.coarse->face_offsets, target.mesh->face_offsets()) ||
        !checkpoint_values_equal<int>(target.coarse->corner_verts, target.mesh->corner_verts()) ||
        !checkpoint_values_equal<int>(target.coarse->corner_edges, target.mesh->corner_edges()))
    {
      BKE_reportf(reports, RPT_ERROR, "A callback changed the selected Sculpt object \"%s\" (its owners, mode or "
                  "coarse Mesh coordinates)",
                  current.contains(reinterpret_cast<ID *>(target.object)) ? target.object->id.name + 2 : "?");
      return false;
    }
    if (target.dyntopo && checkpoint_attribute_layout(*target.mesh) != target.coarse->attribute_layout) {
      BKE_reportf(reports, RPT_ERROR, "A callback added or removed attributes of the Dyntopo mesh of \"%s\"",
                  target.object->id.name + 2);
      return false;
    }
    if (target.key) {
      int index = 0;
      for (const KeyBlock &block : target.key->block) {
        if (index >= target.coarse->key_data.size() || block.totelem != target.coarse->key_elements[index] ||
            target.key->elemsize <= 0 || (block.totelem && !block.data) ||
            !checkpoint_values_equal<std::byte>(target.coarse->key_data[index],
                 Span(static_cast<const std::byte *>(block.data), int64_t(block.totelem) * target.key->elemsize)))
        {
          BKE_report(reports, RPT_ERROR, "A callback changed selected checkpoint Key coordinates");
          return false;
        }
        index++;
      }
      if (index != target.coarse->key_data.size()) { return false; }
    }
    const auto attributes = target.mesh->attributes();
    if (target.coarse->attribute_present != std::array<bool, 4>{attributes.contains(".sculpt_mask"), attributes.contains(".hide_vert"),
                                                       attributes.contains(".hide_edge"), attributes.contains(".hide_poly")} ||
        !checkpoint_values_equal<float>(target.coarse->mask, VArraySpan<float>(*attributes.lookup<float>(".sculpt_mask", bke::AttrDomain::Point))) ||
        !checkpoint_values_equal<bool>(target.coarse->hide_vert, VArraySpan<bool>(*attributes.lookup<bool>(".hide_vert", bke::AttrDomain::Point))) ||
        !checkpoint_values_equal<bool>(target.coarse->hide_edge, VArraySpan<bool>(*attributes.lookup<bool>(".hide_edge", bke::AttrDomain::Edge))) ||
        !checkpoint_values_equal<bool>(target.coarse->hide_poly, VArraySpan<bool>(*attributes.lookup<bool>(".hide_poly", bke::AttrDomain::Face))))
    {
      BKE_report(reports, RPT_ERROR, "A callback changed selected checkpoint mask or hiding attributes");
      return false;
    }
  }
  for (const int64_t i : cold.scenes.index_range()) {
    const Scene *scene = cold.scenes[i];
    const auto &saved = cold.plan->grids[i];
    if (!current.contains(reinterpret_cast<ID *>(const_cast<Scene *>(scene))) || scene->id.session_uid != cold.scene_uids[i] ||
        BLI_findindex(&scene->view_layers, cold.layers[i]) < 0 ||
        scene->r.mode != saved.graph.scene_mode || scene->r.simplify_subsurf != saved.graph.simplify_subsurf ||
        !checkpoint_float_equal(BKE_scene_frame_get(scene), saved.graph.frame) ||
        !checkpoint_float_equal(BKE_scene_ctime_get(scene), saved.graph.ctime))
    {
      BKE_report(reports, RPT_ERROR, "A callback changed selected checkpoint Scene/ViewLayer owners");
      return false;
    }
  }
  return true;
}

static bool checkpoint_cold_preserve_impl(Main &main, WMCheckpointColdInstall &cold,
                                           CheckpointCaptureOwner *owner, ReportList *reports,
                                           bool suspended_operation);
static bool checkpoint_cold_operation_boundary(void *user, Main *main)
{
  auto &cold = *static_cast<WMCheckpointColdInstall *>(user);
  return main && checkpoint_cold_preserve_impl(*main, cold, cold.owner, cold.runtime_reports, true);
}

static bool checkpoint_cold_restore_brushes(Main &main, WMCheckpointColdInstall &cold,
                                            const size_t limit, ReportList *reports)
{
  /* Sessions are reconstructed without mode entry, so ED_editors_init deliberately skips the
   * ordinary Sculpt entry path that resolves saved brush assets. Restore only that missing
   * binding, before LOAD_POST, without rebuilding or flushing any selected surface. */
  struct SceneOwner { Scene *scene; uint uid; };
  Vector<SceneOwner> scenes;
  const int64_t count = main.scenes.count();
  size_t charged = 0;
  if (count < 0 || uint64_t(count) > limit / (2 * sizeof(SceneOwner)) ||
      !ed::sculpt_paint::undo::checkpoint_snapshot_charge(
          charged, limit, size_t(count) * 2 * sizeof(SceneOwner))) { return false; }
  scenes.reserve(count);
  for (Scene &scene : main.scenes) {
    if (!scene.toolsettings || !scene.toolsettings->sculpt) { continue; }
    for (const auto &target : cold.targets) {
      if (BKE_collection_has_object_recursive(scene.master_collection, target.object)) {
        scenes.append({&scene, scene.id.session_uid});
        break;
      }
    }
  }
  auto valid = [&]() {
    return BKE_undosys_checkpoint_capture_validate(cold.owner) &&
           BKE_checkpoint_callback_observation_valid() &&
           WM_checkpoint_cold_validate(main, cold, false, reports);
  };
  for (const SceneOwner &saved : scenes) {
    if (!valid() || BLI_findindex(&main.scenes, saved.scene) < 0 ||
        saved.scene->id.session_uid != saved.uid || !saved.scene->toolsettings ||
        !saved.scene->toolsettings->sculpt) { return false; }
    Paint *paint = &saved.scene->toolsettings->sculpt->paint;
    if (paint->brush || !paint->brush_asset_reference) { continue; }
    const AssetWeakReference &source = *paint->brush_asset_reference;
    if (!ed::sculpt_paint::undo::checkpoint_snapshot_charge(charged, limit, sizeof(AssetWeakReference)) ||
        !ed::sculpt_paint::undo::checkpoint_snapshot_charge(charged, limit,
            source.asset_library_identifier ? strlen(source.asset_library_identifier) + 1 : 0) ||
        !ed::sculpt_paint::undo::checkpoint_snapshot_charge(charged, limit,
            source.relative_asset_identifier ? strlen(source.relative_asset_identifier) + 1 : 0)) {
      return false;
    }
    const AssetWeakReference reference(source);
    ID *resolved = bke::asset_edit_id_from_weak_reference(main, ID_BR, reference);
    /* Import can run callbacks. Resolve membership before dereferencing cached owners and do
     * not overwrite an actual callback change to the selected brush/reference. */
    if (!valid() || BLI_findindex(&main.scenes, saved.scene) < 0 ||
        saved.scene->id.session_uid != saved.uid || !saved.scene->toolsettings ||
        !saved.scene->toolsettings->sculpt || &saved.scene->toolsettings->sculpt->paint != paint ||
        paint->brush || !paint->brush_asset_reference ||
        *paint->brush_asset_reference != reference) { return false; }
    if (!resolved || BLI_findindex(&main.brushes, resolved) < 0 || !paint->runtime ||
        !BKE_paint_can_use_brush(paint, id_cast<Brush *>(resolved))) {
      BKE_report(reports, RPT_ERROR, "The saved Sculpt brush asset could not be restored");
      return false;
    }
    /* Same pointer assignment as the ordinary asset-reference resolver. Keep the exact saved
     * weak reference; no default brush or replacement reference is synthesized. */
    paint->brush = id_cast<Brush *>(resolved);
  }
  return valid();
}

bool WM_checkpoint_cold_runtime_begin(Main &main, WMCheckpointColdInstall &cold,
                                       CheckpointCaptureOwner *owner, ReportList *reports)
{
  if (cold.runtime_ready || !BKE_undosys_checkpoint_capture_validate(owner) ||
      !WM_checkpoint_cold_validate(main, cold, true, reports)) { return false; }
  /* Ordinary ED_editors_init is active/visibility dependent. Reconstruct every selected
   * session explicitly first, including unused/hidden/other-Scene owners. It will then
   * recognize their actual mode data and leave them intact. No evaluation or mode operator. */
  for (const auto &target : cold.targets) {
    if (target.object->runtime->sculpt_session) {
      BKE_report(reports, RPT_ERROR, "A selected object already had a Sculpt session before Restore installed one");
      return false;
    }
  }
  ed::sculpt_paint::checkpoint::CodecLimits limits;
  if (!WM_checkpoint_memory_limits(limits, 0, reports) ||
      uint64_t(cold.targets.size()) > limits.max_backing_bytes / sizeof(SculptSession)) {
    BKE_report(reports, RPT_ERROR, "Checkpoint Sculpt sessions exceed finite admission");
    return false;
  }
  for (auto &target : cold.targets) {
    BKE_object_sculpt_data_create(target.object);
    target.installer_session = target.object->runtime->sculpt_session;
  }
  cold.evaluation_scope = DEG_checkpoint_runtime_begin(&main);
  if (!cold.evaluation_scope) {
    BKE_report(reports, RPT_ERROR, "The checkpoint evaluation scope could not begin");
    return false;
  }
  cold.callback_budget = limits.max_backing_bytes;
  cold.owner = owner;
  cold.runtime_reports = reports;
  cold.runtime_ready = true;
  if (!DEG_checkpoint_runtime_set_operation_hook(cold.evaluation_scope, &cold,
                                                 checkpoint_cold_operation_boundary)) {
    BKE_report(reports, RPT_ERROR, "The checkpoint evaluation hook could not be installed");
    return false;
  }
  return checkpoint_cold_restore_brushes(main, cold, limits.max_backing_bytes, reports);
}

static bool checkpoint_attribute_matches(const bke::Attribute &attribute,
                                         const bke::AttrDomain domain,
                                         const bke::AttrType type,
                                         const int64_t elements,
                                         const bool array_only)
{
  if (attribute.domain() != domain || attribute.data_type() != type) { return false; }
  if (const auto *array = std::get_if<bke::Attribute::ArrayData>(&attribute.data())) {
    return array->size == elements &&
           ((array->data && array->sharing_info) || (!array->data && !elements));
  }
  const auto *single = std::get_if<bke::Attribute::SingleData>(&attribute.data());
  return !array_only && single && single->value && single->sharing_info;
}

static std::unique_ptr<WMCheckpointColdInstall::EditedCoarse> checkpoint_edited_coarse_prepare(
    Main &main, Span<uint32_t> selected, const size_t limit, size_t &used, const char **error)
{
  using Cold = WMCheckpointColdInstall;
  auto charge = [&](const size_t bytes) {
    return ed::sculpt_paint::undo::checkpoint_snapshot_charge(used, limit, bytes);
  };
  if (!charge(sizeof(Cold::EditedCoarse) + selected.size() *
              (sizeof(Cold::EditedRelation) + sizeof(std::unique_ptr<Cold::EditedMesh>)) * 4)) {
    *error = "Edited coarse replay controls exceed finite admission"; return nullptr;
  }
  auto result = std::make_unique<Cold::EditedCoarse>();
  for (const uint32_t uid : selected) {
    Object *object = nullptr; Mesh *mesh = nullptr; Key *key = nullptr;
    for (Object &candidate : main.objects) {
      if (candidate.id.session_uid == uid) { object = &candidate; break; }
    }
    if (!object) { continue; } /* Legitimate deletion remains deleted. */
    if (object->type != OB_MESH) { continue; }
    for (Mesh &candidate : main.meshes) {
      if (reinterpret_cast<ID *>(&candidate) == object->data) { mesh = &candidate; break; }
    }
    if (!mesh) { *error = "Edited coarse Object has no live typed Mesh"; return nullptr; }
    if (mesh->key) {
      for (Key &candidate : main.shapekeys) {
        if (&candidate == mesh->key) { key = &candidate; break; }
      }
      if (!key || key->from != &mesh->id) {
        *error = "Edited coarse Key has no live typed Mesh binding"; return nullptr;
      }
    }
    result->relations.append({uid, mesh->id.session_uid, key ? key->id.session_uid : 0,
                              object->mode, object->shapenr, object->shapeflag});
    bool exists = false;
    for (const auto &old : result->meshes) { exists |= old->mesh_uid == mesh->id.session_uid; }
    if (exists) { continue; }
    if (mesh->verts_num < 0 || mesh->edges_num < 0 || mesh->faces_num < 0 || mesh->corners_num < 0 ||
        !charge(sizeof(Cold::EditedMesh) + sizeof(bke::MeshRuntime) + 131072) ||
        !charge(size_t(mesh->verts_num) * (sizeof(float3) + sizeof(float) + sizeof(bool)) * 4) ||
        !charge(size_t(mesh->edges_num) * (sizeof(int2) + sizeof(bool)) * 4) ||
        !charge((size_t(mesh->faces_num) + 1) * (sizeof(int) + sizeof(bool)) * 4) ||
        !charge(size_t(mesh->corners_num) * sizeof(int) * 8)) {
      *error = "Edited coarse geometry staging exceeds finite admission"; return nullptr;
    }
    if (key) {
      for (const KeyBlock &block : key->block) {
        if (key->elemsize <= 0 || block.totelem < 0 ||
            size_t(block.totelem) > size_t(INT64_MAX) / size_t(key->elemsize) ||
            (block.totelem && !block.data) ||
            !charge(size_t(block.totelem) * size_t(key->elemsize)) ||
            !charge(sizeof(Cold::EditedKeyBlock) * 4)) {
          *error = "Edited Key staging exceeds finite admission"; return nullptr;
        }
      }
    }
    auto copy = std::make_unique<Cold::EditedMesh>();
    copy->empty_caches = std::make_unique<bke::MeshRuntime>();
    copy->mesh_uid = mesh->id.session_uid; copy->key_uid = key ? key->id.session_uid : 0;
    copy->verts = mesh->verts_num; copy->edges = mesh->edges_num;
    copy->faces = mesh->faces_num; copy->corners = mesh->corners_num;
    copy->key_element_size = key ? key->elemsize : 0;
    auto attribute = [&](const char *name, const bke::AttrDomain domain,
                          const bke::AttrType type, const int64_t elements,
                          const bool array_only) {
      const bke::Attribute *source = mesh->attribute_storage.wrap().lookup(name);
      if (!source) {
        if (array_only && elements) {
          *error = "Edited coarse geometry attribute is missing"; return false;
        }
        return true;
      }
      if (!checkpoint_attribute_matches(*source, domain, type, elements, array_only)) {
        *error = "Edited coarse attribute has invalid storage or dimensions"; return false;
      }
      /* These eight fixed attributes fit inline. Their full arrays and sharing controls were
       * charged above; a constant value uses no more backing than that admission. */
      const CPPType &cpp_type = bke::attribute_type_to_cpp_type(type);
      bke::Attribute::DataVariant data = bke::Attribute::ArrayData{};
      if (const auto *array = std::get_if<bke::Attribute::ArrayData>(&source->data())) {
        bke::Attribute::ArrayData copied{};
        if (elements) {
          copied = bke::Attribute::ArrayData::from_uninitialized(cpp_type, elements);
          std::memcpy(copied.data, array->data, size_t(elements) * cpp_type.size);
        }
        data = std::move(copied);
      }
      else {
        const auto &single = std::get<bke::Attribute::SingleData>(source->data());
        auto copied = bke::Attribute::SingleData::from_default_value(cpp_type);
        std::memcpy(copied.value, single.value, cpp_type.size);
        data = std::move(copied);
      }
      copy->attributes.append({name, domain, type, elements, array_only, std::move(data)});
      return true;
    };
    using bke::AttrDomain;
    using bke::AttrType;
    if (!attribute("position", AttrDomain::Point, AttrType::Float3, copy->verts, true) ||
        !attribute(".edge_verts", AttrDomain::Edge, AttrType::Int32_2D, copy->edges, true) ||
        !attribute(".corner_vert", AttrDomain::Corner, AttrType::Int32, copy->corners, true) ||
        !attribute(".corner_edge", AttrDomain::Corner, AttrType::Int32, copy->corners, true) ||
        !attribute(".sculpt_mask", AttrDomain::Point, AttrType::Float, copy->verts, false) ||
        !attribute(".hide_vert", AttrDomain::Point, AttrType::Bool, copy->verts, false) ||
        !attribute(".hide_edge", AttrDomain::Edge, AttrType::Bool, copy->edges, false) ||
        !attribute(".hide_poly", AttrDomain::Face, AttrType::Bool, copy->faces, false)) {
      return nullptr;
    }
    if (copy->faces) {
      const auto offsets = mesh->face_offsets();
      copy->face_offsets = MEM_new_array_uninitialized<int>(offsets.size(), __func__);
      std::memcpy(copy->face_offsets, offsets.data(), offsets.size_in_bytes());
      copy->face_sharing = implicit_sharing::info_for_mem_free(copy->face_offsets);
    }
    if (key) {
      for (KeyBlock &block : key->block) {
        const size_t bytes = size_t(block.totelem) * size_t(key->elemsize);
        void *data = bytes ? MEM_new_uninitialized(bytes, __func__) : nullptr;
        if (bytes) { std::memcpy(data, block.data, bytes); }
        copy->key_blocks.append({&block, block.totelem, data});
      }
    }
    result->meshes.append(std::move(copy));
  }
  return result;
}

static bool checkpoint_edited_coarse_replay(Main &main, WMCheckpointColdInstall::EditedCoarse &state,
                                             const bool publish, const char **error)
{
  for (const auto &relation : state.relations) {
    Object *object = nullptr;
    for (Object &candidate : main.objects) {
      if (candidate.id.session_uid == relation.object_uid) { object = &candidate; break; }
    }
    Mesh *mesh = nullptr;
    for (Mesh &candidate : main.meshes) {
      if (candidate.id.session_uid == relation.mesh_uid) { mesh = &candidate; break; }
    }
    if (!object || !mesh || object->data != &mesh->id || object->type != OB_MESH ||
        int(object->mode) != relation.mode || object->shapenr != relation.shape_index ||
        int(object->shapeflag) != relation.shape_flags) {
      *error = "Edited Object relations changed again during native unwind"; return false;
    }
  }
  for (auto &owned : state.meshes) {
    auto &saved = *owned; Mesh *mesh = nullptr; Key *key = nullptr;
    for (Mesh &candidate : main.meshes) {
      if (candidate.id.session_uid == saved.mesh_uid) { mesh = &candidate; break; }
    }
    if (saved.key_uid) {
      for (Key &candidate : main.shapekeys) {
        if (candidate.id.session_uid == saved.key_uid) { key = &candidate; break; }
      }
    }
    if (!mesh || !saved.empty_caches || mesh->key != key || (saved.key_uid && !key) ||
        mesh->verts_num != saved.verts || mesh->edges_num != saved.edges ||
        mesh->faces_num != saved.faces || mesh->corners_num != saved.corners ||
        (key && (key->elemsize != saved.key_element_size || key->from != &mesh->id)) ||
        (saved.faces && (!mesh->face_offset_indices || !mesh->runtime->face_offsets_sharing_info))) {
      *error = "Edited coarse destination dimensions or Key binding changed during native unwind"; return false;
    }
    for (auto &saved_attribute : saved.attributes) {
      bke::Attribute *attribute = mesh->attribute_storage.wrap().lookup(saved_attribute.name);
      if (!attribute || !checkpoint_attribute_matches(*attribute, saved_attribute.domain,
          saved_attribute.type, saved_attribute.elements, saved_attribute.array_only)) {
        *error = "Edited coarse attribute schema changed during native unwind"; return false;
      }
      if (publish) {
        /* Retain the displaced array or constant until this saved owner is destroyed. Neither
         * reading data nor assigning a prepared variant calls data_for_write or allocates. */
        auto displaced = attribute->data();
        attribute->assign_data(std::move(saved_attribute.data));
        saved_attribute.data = std::move(displaced);
      }
    }
    if (key) {
      int64_t i = 0;
      for (KeyBlock &block : key->block) {
        if (i >= saved.key_blocks.size() || &block != saved.key_blocks[i].block ||
            block.totelem != saved.key_blocks[i].elements) {
          *error = "Edited Key block ownership changed during native unwind"; return false;
        }
        if (publish) { std::swap(block.data, saved.key_blocks[i].data); }
        i++;
      }
      if (i != saved.key_blocks.size()) {
        *error = "Edited Key block count changed during native unwind"; return false;
      }
    }
    if (publish) {
      if (saved.faces) {
        std::swap(mesh->face_offset_indices, saved.face_offsets);
        std::swap(mesh->runtime->face_offsets_sharing_info, saved.face_sharing);
      }
      saved.publish_cache_invalidation(*mesh);
    }
  }
  return true;
}

#ifdef WITH_GTESTS
/* Drives the real coarse preparation/validation/publication paths, with native work
 * deliberately inserted between capture and replay. No alternate cache implementation. */
bool WM_checkpoint_test_coarse_replay(Main &main, const Span<uint32_t> selected,
                                       FunctionRef<void()> native_unwind, const size_t limit,
                                       const char **error)
{
  size_t used = 0;
  auto state = checkpoint_edited_coarse_prepare(main, selected, limit, used, error);
  if (!state) { return false; }
  native_unwind();
  return checkpoint_edited_coarse_replay(main, *state, false, error) &&
         checkpoint_edited_coarse_replay(main, *state, true, error);
}
#endif

struct WMCheckpointColdCallback {
  using State = ed::sculpt_paint::undo::MainUndoState;
  std::unique_ptr<State, decltype(&ed::sculpt_paint::undo::main_undo_state_free)>
      before{nullptr, ed::sculpt_paint::undo::main_undo_state_free};
  Vector<WMCheckpointColdInstall::RuntimeIdentity> identities;
  size_t reservation = 0;
};

WMCheckpointColdCallback *WM_checkpoint_cold_callback_begin(Main &main,
                                                            WMCheckpointColdInstall &cold,
                                                            ReportList *reports)
{
  if (!BLI_thread_is_main()) { return nullptr; }
  if (!cold.runtime_ready || cold.callback_observation_failed || cold.preservation_busy ||
      G_MAIN != &main || cold.main != &main ||
      !BKE_undosys_checkpoint_capture_validate(cold.owner) ||
      !DEG_checkpoint_runtime_observation_allowed(&main) ||
      cold.callback_reserved > cold.callback_budget)
  {
    cold.callback_conflict = true; cold.callback_observation_failed = true; return nullptr;
  }
  size_t used = 0;
  const size_t limit = cold.callback_budget - cold.callback_reserved;
  const size_t control = sizeof(WMCheckpointColdCallback) +
                         cold.targets.size() * sizeof(WMCheckpointColdInstall::RuntimeIdentity) * 4;
  if (!ed::sculpt_paint::undo::checkpoint_snapshot_charge(used, limit, control)) {
    cold.callback_conflict = true; cold.callback_observation_failed = true; return nullptr;
  }
  auto token = std::make_unique<WMCheckpointColdCallback>();
  const char *error = nullptr;
  token->before.reset(ed::sculpt_paint::undo::main_undo_state_observe_checkpoint(
      main, cold.selected.as_span(), limit, used, &error));
  if (error) { cold.callback_conflict = true; cold.callback_observation_failed = true; BKE_report(reports, RPT_ERROR, error); return nullptr; }
  for (const uint32_t uid : cold.selected) {
    const SculptSession *ss = nullptr;
    for (const Object &object : main.objects) {
      if (object.id.session_uid == uid) { ss = object.runtime->sculpt_session; break; }
    }
    token->identities.append({ss, ss ? ss->pbvh.get() : nullptr, ss ? ss->bm : nullptr,
                             ss ? ss->bm_log : nullptr, ss ? ss->subdiv_ccg : nullptr});
  }
  token->reservation = used;
  cold.callback_reserved += used;
  cold.callback_tokens.add(token.get());
  return token.release();
}

bool WM_checkpoint_cold_callback_end(Main *live_main, WMCheckpointColdInstall &cold,
                                      WMCheckpointColdCallback *token, ReportList *reports)
{
  /* Membership proof precedes any token dereference. Disposal is unconditional for the exact
   * token even if callbacks replaced Main; never bind/dereference a null/stale Main reference. */
  if (!BLI_thread_is_main()) { return false; }
  if (!token || !cold.callback_tokens.remove(token)) {
    cold.callback_conflict = true; cold.callback_observation_failed = true;
    BKE_checkpoint_callback_observation_unavailable(cold.main); return false;
  }
  std::unique_ptr<WMCheckpointColdCallback> owned(token);
  auto release = [&]() { cold.callback_reserved -= token->reservation; };
  if (cold.callback_observation_failed || !live_main || live_main != G_MAIN || live_main != cold.main ||
      !BKE_undosys_checkpoint_capture_validate(cold.owner) ||
      !DEG_checkpoint_runtime_observation_allowed(live_main))
  {
    release(); cold.callback_conflict = true; cold.callback_observation_failed = true;
    BKE_checkpoint_callback_observation_unavailable(cold.main); return false;
  }
  const char *error = nullptr;
  size_t used = 0;
  const size_t limit = cold.callback_budget - cold.callback_reserved;
  decltype(token->before) after(ed::sculpt_paint::undo::main_undo_state_observe_checkpoint(
      *live_main, cold.selected.as_span(), limit, used, &error),
      ed::sculpt_paint::undo::main_undo_state_free);
  auto after_coarse = error ? nullptr : checkpoint_edited_coarse_prepare(
      *live_main, cold.selected.as_span(), limit, used, &error);
  bool equal = !error && bool(after) == bool(token->before);
  if (equal && after) {
    equal = ed::sculpt_paint::undo::main_undo_state_equals(*after, *token->before);
  }
  for (const int64_t i : cold.targets.index_range()) {
    const SculptSession *ss = nullptr;
    for (const Object &object : live_main->objects) {
      if (object.id.session_uid == cold.selected[i]) { ss = object.runtime->sculpt_session; break; }
    }
    const auto &before = token->identities[i];
    equal &= ss && ss == before.session && ss->pbvh.get() == before.pbvh &&
             ss->bm == before.mesh && ss->bm_log == before.log && ss->subdiv_ccg == before.ccg;
  }
  /* This admission is deliberately after the actual edited witness. Changed Main relations
   * cause refusal but must not prevent retaining the runtime bound to those new relations. */
  equal &= WM_checkpoint_cold_validate(*live_main, cold, false, reports);
  release();
  if (!equal) {
    cold.callback_conflict = true;
    /* Keep the exact after-callback state alive on refusal. Evaluation's receipt cancels all
     * subsequent operations; preservation/rebinding of the current operation is checked below. */
    if (error) {
      cold.callback_observation_failed = true;
      BKE_checkpoint_callback_observation_unavailable(cold.main);
    }
    else {
      cold.callback_reserved -= cold.conflict_reserved;
      cold.preservation_attempted = false; cold.preservation_complete = false;
      cold.conflict_state = std::move(after);
      cold.conflict_coarse = std::move(after_coarse);
      cold.conflict_reserved = used;
      cold.callback_reserved += used;
    }
    BKE_report(reports, RPT_ERROR, error ? error : "A checkpoint callback changed selected Sculpt runtime");
  }
  return equal;
}

static bool checkpoint_cold_preserve_impl(Main &main, WMCheckpointColdInstall &cold,
                                           CheckpointCaptureOwner *owner, ReportList *reports,
                                           const bool suspended_operation)
{
  /* Validate the actual joined process/document owner before any captured ID/runtime access. */
  if (!BLI_thread_is_main() || G_MAIN != &main || cold.main != &main || (cold.runtime_ready && owner != cold.owner) ||
      !BKE_undosys_checkpoint_capture_validate(owner)) {
    BKE_report(reports, RPT_ERROR, "Checkpoint edited-runtime preservation lost its live owner");
    return false;
  }
  if (!cold.runtime_ready) { return true; }
  if (!BKE_checkpoint_callback_observation_safe(&main)) {
    cold.callback_observation_failed = true;
    BKE_report(reports, RPT_ERROR, "Checkpoint callback concurrency or witness admission is unverified"); return false;
  }
  if (!cold.callback_conflict) { return true; }
  if (cold.preservation_busy || (!suspended_operation && !cold.callback_tokens.is_empty()) ||
      cold.callback_observation_failed || !DEG_checkpoint_runtime_observation_allowed(&main)) {
    BKE_report(reports, RPT_ERROR, "Checkpoint runtime preservation is partial: an edited callback could not be captured");
    return false;
  }
  if (cold.preservation_attempted) { return cold.preservation_complete; }
  cold.preservation_attempted = true;
  struct Busy {
    bool &flag; explicit Busy(bool &value) : flag(value) { flag = true; }
    ~Busy() { flag = false; }
  } busy(cold.preservation_busy);
  /* Keep the exact replay owner local through publication. Native BMesh Python invalidation
   * only clears wrapper pointers; no callbacks are executed by these retire/move paths. */
  struct Reservation {
    WMCheckpointColdInstall &cold; size_t bytes;
    ~Reservation() { cold.callback_reserved -= bytes; }
  } reservation{cold, cold.conflict_reserved};
  cold.conflict_reserved = 0;
  auto coarse = std::move(cold.conflict_coarse);
  auto runtime = std::move(cold.conflict_state);
  const char *error = nullptr;
  if (!coarse || !checkpoint_edited_coarse_replay(main, *coarse, false, &error)) {
    BKE_report(reports, RPT_ERROR, error ? error : "Checkpoint coarse callback preservation is partial"); return false;
  }
  if (runtime && !ed::sculpt_paint::undo::main_undo_state_replay_checkpoint(
          main, *runtime, &error)) {
    BKE_report(reports, RPT_ERROR, error ? error : "Checkpoint edited-runtime preservation is partial");
    return false;
  }
  if (!checkpoint_edited_coarse_replay(main, *coarse, true, &error)) {
    BKE_report(reports, RPT_ERROR, error ? error : "Checkpoint coarse publication is partial"); return false;
  }
  runtime.reset(); coarse.reset();
  cold.preservation_complete = true;
  return true;
}

void WM_checkpoint_cold_runtime_end(WMCheckpointColdInstall &cold)
{
  if (!BLI_thread_is_main()) { return; }
  DEG_checkpoint_runtime_end(cold.evaluation_scope);
  cold.evaluation_scope = nullptr;
  cold.runtime_ready = false;
  cold.owner = nullptr;
  cold.runtime_reports = nullptr;
}

bool WM_checkpoint_cold_preserve_callback_edits(Main &main, WMCheckpointColdInstall &cold,
                                                 CheckpointCaptureOwner *owner, ReportList *reports)
{
  struct Retire {
    WMCheckpointColdInstall &cold;
    ~Retire() { WM_checkpoint_cold_runtime_end(cold); }
  } retire{cold};
  return checkpoint_cold_preserve_impl(main, cold, owner, reports, false);
}

static bool checkpoint_modifier_matches(const MultiresModifierData &m,
                                        const ed::sculpt_paint::checkpoint::ModifierProfile &p)
{
  return m.modifier.persistent_uid == p.uid && int(m.modifier.mode) == p.mode &&
         m.lvl == p.level && m.sculptlvl == p.sculpt_level && m.renderlvl == p.render_level &&
         m.totlvl == p.total_level && int(m.flags) == p.flags && m.quality == p.quality &&
         m.uv_smooth == p.uv_smooth && m.boundary_smooth == p.boundary_smooth;
}
static bool checkpoint_modifier_order_matches(const Object &object, Span<int3> expected)
{
  int index = 0;
  for (const ModifierData &modifier : object.modifiers) {
    if (index >= expected.size() || expected[index] !=
        int3(modifier.persistent_uid, int(modifier.type), int(modifier.mode))) {
      return false;
    }
    index++;
  }
  return index == expected.size();
}
static const MultiresModifierData *checkpoint_multires(const Object &object, const int uid)
{
  for (const ModifierData &modifier : object.modifiers) {
    if (modifier.type == eModifierType_Multires && modifier.persistent_uid == uid) {
      return reinterpret_cast<const MultiresModifierData *>(&modifier);
    }
  }
  return nullptr;
}

#ifdef WITH_GTESTS
static bool checkpoint_test_fail_next_install = false;
void WM_checkpoint_test_fail_next_install()
{
  checkpoint_test_fail_next_install = true;
}
#endif

bool WM_checkpoint_cold_install(bContext *C, Main &main, WMCheckpointColdInstall &cold,
                                 CheckpointCaptureOwner *owner, ReportList *reports)
{
  using namespace ed::sculpt_paint::checkpoint;
  /* Every refusal names its check: Restore surfaces these reasons, never a generic failure. */
  auto refuse = [&](const char *reason) {
    BKE_report(reports, RPT_ERROR, reason);
    return false;
  };
#ifdef WITH_GTESTS
  if (std::exchange(checkpoint_test_fail_next_install, false)) {
    return refuse("Test fault: the Sculpt install was refused after replacement");
  }
#endif
  if (cold.installed || owner != cold.owner) { return refuse("Checkpoint install was already attempted or lost its owner"); }
  if (!WM_checkpoint_cold_validate(main, cold, false, reports)) {
    return refuse("Selected checkpoint owners changed before Sculpt install");
  }
  if (!cold.runtime_ready || !cold.callback_tokens.is_empty() ||
      !BKE_checkpoint_callback_observation_valid() ||
      !BKE_undosys_checkpoint_capture_validate(cold.owner)) {
    return refuse("Checkpoint install began without its runtime owner or with an open callback");
  }
  auto valid = [&]() {
    return BKE_undosys_checkpoint_capture_validate(cold.owner) &&
           BKE_checkpoint_callback_observation_valid() &&
           WM_checkpoint_cold_validate(main, cold, false, reports);
  };
  CodecLimits install_limits;
  if (!WM_checkpoint_memory_limits(install_limits, 0, reports)) { return false; }
  size_t install_charge = 0;
  auto admit_install = [&](const uint64_t count) {
    return count <= (SIZE_MAX - 65536) / 4096 &&
           ed::sculpt_paint::undo::checkpoint_snapshot_charge(
               install_charge, install_limits.max_backing_bytes, 65536 + size_t(count) * 4096);
  };
  for (const int64_t i : cold.targets.index_range()) {
    const auto &target = cold.targets[i];
    const auto &saved = cold.plan->objects[i];
    const uint64_t items = saved.geometry ? uint64_t(saved.geometry->totvert) + saved.geometry->totedge +
                                               saved.geometry->totloop + saved.geometry->totface :
                                           uint64_t(target.mesh->verts_num) + target.mesh->faces_num +
                                               target.mesh->corners_num;
    if (!admit_install(items)) {
      BKE_report(reports, RPT_ERROR, "Checkpoint per-owner PBVH/log construction exceeds finite admission"); return false;
    }
  }
  for (const auto &saved : cold.plan->grids) {
    if (!admit_install(uint64_t(saved.positions.size()) + uint64_t(saved.grids_num) +
                       uint64_t(saved.face_offsets.size()) + uint64_t(saved.corner_verts.size()))) {
      BKE_report(reports, RPT_ERROR, "Checkpoint fine PBVH staging exceeds finite admission"); return false;
    }
  }
  /* Retire only selected fresh-file runtime. Never visit/discard unrelated handler-created sessions. */
  for (auto &target : cold.targets) {
    SculptSession *ss = target.object->runtime->sculpt_session;
    if (!ss || ss->cache || ss->filter_cache || ss->expand_cache || ss->tex_pool) {
      return refuse("A selected Sculpt session was missing or busy before install");
    }
    BKE_sculptsession_free_pbvh(*target.object);
    BKE_sculptsession_free_deformMats(ss);
    if (ss->bm_log) { BM_log_free(ss->bm_log); ss->bm_log = nullptr; }
    if (ss->bm) { BM_mesh_free(ss->bm); ss->bm = nullptr; }
    ss->subdiv_ccg = nullptr;
    ss->multires_modifier = nullptr;
    ss->shapekey_active = nullptr;
    ss->deform_modifiers_active = false;
    ss->rv3d = nullptr;
    ss->v3d = nullptr;
  }
  /* Admit every graph's selected objects before evaluating any graph. Admission tags each
   * selected Object for every graph that contains it, so an Object shown by two graphs (the
   * same Multires Object in two Scenes) would otherwise leave the earlier-evaluated graph
   * stale. Each graph is then evaluated once and its scope always ends, also on refusal. */
  struct Materializing {
    Depsgraph *graph;
    DEGCheckpointMaterializeScope *scope;
  };
  Vector<Materializing> materializing;
  auto end_materializing = [&]() {
    bool restored = true;
    for (const Materializing &row : materializing) {
      restored &= DEG_checkpoint_materialize_end(row.scope);
    }
    materializing.clear();
    return restored;
  };
  auto refuse_materializing = [&](const char *reason) {
    end_materializing();
    return refuse(reason);
  };
  for (const int64_t i : cold.scenes.index_range()) {
    if (!valid()) { return refuse_materializing("Selected checkpoint owners changed while preparing Sculpt graphs"); }
    Depsgraph *graph = BKE_scene_ensure_depsgraph(&main, cold.scenes[i], cold.layers[i]);
    if (!graph) { return refuse_materializing("A selected Sculpt graph could not be created"); }
    const auto &profile = cold.plan->grids[i].graph;
    DEG_debug_flags_set(graph, profile.debug_flags);
    if (!profile.visibility_optimization) { DEG_disable_visibility_optimization(graph); }
    if (profile.editors_update) { DEG_enable_editors_update(graph); }
    bool admitted = false;
    for (const Materializing &row : materializing) { admitted |= row.graph == graph; }
    if (!admitted) {
      Vector<uint32_t> selected;
      for (const int64_t j : cold.scenes.index_range()) {
        if (cold.scenes[j] == cold.scenes[i] && cold.layers[j] == cold.layers[i]) {
          selected.append(cold.targets[cold.plan->grids[j].object_index].object_uid);
        }
      }
      DEGCheckpointMaterializeScope *scope = DEG_checkpoint_materialize_begin(graph, selected.as_span());
      if (!scope) { return refuse_materializing("A selected Sculpt graph could not be materialized"); }
      materializing.append({graph, scope});
    }
  }
  for (const Materializing &row : materializing) {
    BKE_scene_graph_evaluated_ensure(row.graph, &main);
    if (!valid()) { return refuse_materializing("Selected checkpoint owners changed during Sculpt graph evaluation"); }
  }
  if (!end_materializing()) { return refuse("A selected Sculpt graph profile was not restored after evaluation"); }
  if (!valid()) { return refuse("Selected checkpoint owners changed during Sculpt graph evaluation"); }
  /* Select each saved editing authority before collecting final evaluated pointers. */
  for (const int64_t i : cold.plan->grids.index_range()) {
    const GridState &grid = cold.plan->grids[i];
    if (grid.authority) {
      Object &object = *cold.targets[grid.object_index].object;
      BKE_sculptsession_free_pbvh(object);
      BKE_sculpt_update_object_for_edit(
          BKE_scene_get_depsgraph(cold.scenes[i], cold.layers[i]), &object, false);
      if (!valid()) { return refuse("Selected checkpoint owners changed while selecting the Sculpt authority"); }
    }
  }
  struct PreparedGrid {
    SubdivCCG *destination;
    GridState *saved;
    std::unique_ptr<SubdivCCG> arrays;
    std::unique_ptr<bke::pbvh::Tree> authority;
  };
  Vector<PreparedGrid> grids;
  Set<SubdivCCG *> destinations;
  for (const int64_t i : cold.plan->grids.index_range()) {
    GridState &saved = cold.plan->grids[i];
    Object &object = *cold.targets[saved.object_index].object;
    Depsgraph *graph = BKE_scene_get_depsgraph(cold.scenes[i], cold.layers[i]);
    Object *evaluated_object = graph ? DEG_get_evaluated(graph, &object) : nullptr;
    const Mesh *evaluated_mesh = evaluated_object ? BKE_object_get_evaluated_mesh_no_subsurf_unchecked(evaluated_object) : nullptr;
    SubdivCCG *destination = evaluated_mesh ? evaluated_mesh->runtime->subdiv_ccg.get() : nullptr;
    const auto *original_modifier = checkpoint_multires(object, saved.modifier.uid);
    const auto *evaluated_modifier = evaluated_object ? checkpoint_multires(*evaluated_object, saved.evaluated_modifier.uid) : nullptr;
    bool graph_matches = false;
    const char *graph_mismatch = "the graph is not registered";
    DEG_foreach_registered_graph(&main, [&](const DEGRegisteredGraph &observed) {
      if (observed.depsgraph == graph) {
        const Scene *evaluated_scene = DEG_get_evaluated_scene(graph);
        const std::pair<bool, const char *> checks[] = {
            {observed.scene == cold.scenes[i] && observed.view_layer == cold.layers[i] &&
                 observed.source_main == &main, "its Scene/ViewLayer/Main"},
            {observed.mode == DAG_EVAL_VIEWPORT && !observed.is_render_pipeline, "its evaluation mode"},
            {!observed.is_evaluating, "it is evaluating"},
            {!observed.need_update_relations, "it needs a relations update"},
            {!observed.need_update_nodes_visibility, "it needs a visibility update"},
            {!observed.has_pending_updates, "it has pending updates"},
            {observed.use_visibility_optimization == saved.graph.visibility_optimization &&
                 observed.use_editors_update == saved.graph.editors_update &&
                 int(observed.sync_writeback) == saved.graph.sync_writeback &&
                 observed.debug_flags == saved.graph.debug_flags, "its options"},
            {checkpoint_float_equal(observed.frame, saved.graph.frame) &&
                 checkpoint_float_equal(observed.ctime, saved.graph.ctime), "its frame"},
            {evaluated_scene && evaluated_scene->r.mode == saved.graph.evaluated_scene_mode &&
                 evaluated_scene->r.simplify_subsurf == saved.graph.evaluated_simplify_subsurf &&
                 checkpoint_float_equal(BKE_scene_frame_get(evaluated_scene), saved.graph.evaluated_frame) &&
                 checkpoint_float_equal(BKE_scene_ctime_get(evaluated_scene), saved.graph.evaluated_ctime),
             "its evaluated Scene"},
        };
        graph_matches = true;
        for (const auto &[ok, what] : checks) {
          if (!ok) { graph_matches = false; graph_mismatch = what; break; }
        }
      }
      return true;
    });
    const Mesh &mesh = *cold.targets[saved.object_index].mesh;
    if (!graph_matches) {
      BKE_reportf(reports, RPT_ERROR, "A cold Sculpt graph's evaluation profile changed: %s", graph_mismatch);
      return false;
    }
    if (!destination || !destination->subdiv) { return refuse("A cold Sculpt graph has no Multires grid destination"); }
    if (!destinations.add(destination)) {
      return refuse("Two selected Sculpt graphs resolved the same Multires grid destination");
    }
    if (mesh.verts_num != saved.verts_num || !original_modifier || !evaluated_modifier ||
        !checkpoint_modifier_matches(*original_modifier, saved.modifier) ||
        !checkpoint_modifier_matches(*evaluated_modifier, saved.evaluated_modifier) ||
        !checkpoint_modifier_order_matches(object, saved.modifier_order) ||
        !checkpoint_modifier_order_matches(*evaluated_object, saved.evaluated_modifier_order))
    {
      return refuse("A cold Sculpt Multires modifier or modifier stack changed");
    }
    if (destination->level != saved.level || destination->grid_size != saved.grid_size ||
        destination->grid_area != saved.grid_area || destination->grids_num != saved.grids_num ||
        evaluated_mesh->runtime->subdiv_ccg_tot_level != saved.total_level) {
      return refuse("A cold Sculpt Multires grid destination has another level or size");
    }
    if (!checkpoint_values_equal<int2>(saved.edges, mesh.edges()) ||
        !checkpoint_values_equal<int>(saved.face_offsets, mesh.face_offsets()) ||
        !checkpoint_values_equal<int>(saved.corner_verts, mesh.corner_verts()) ||
        !checkpoint_values_equal<int>(saved.corner_edges, mesh.corner_edges()) ||
        !checkpoint_values_equal<int>(saved.grid_to_face, destination->grid_to_face_map))
    {
      return refuse("A cold Sculpt Multires topology or grid-to-face map changed");
    }
    const auto &a = destination->subdiv->settings;
    const auto &b = saved.subdivision;
    if (a.is_simple != b.is_simple || a.is_adaptive != b.is_adaptive || a.level != b.level ||
        a.use_creases != b.use_creases || a.vtx_boundary_interpolation != b.vtx_boundary_interpolation ||
        a.fvar_linear_interpolation != b.fvar_linear_interpolation) {
      return refuse("A cold Sculpt Multires subdivision setting changed");
    }
    PreparedGrid row{destination, &saved, std::make_unique<SubdivCCG>(), nullptr};
    row.arrays->positions = std::move(saved.positions);
    row.arrays->normals = std::move(saved.normals);
    row.arrays->masks = std::move(saved.masks);
    row.arrays->grid_hidden = std::move(saved.hidden);
    row.arrays->level = saved.level;
    row.arrays->grid_size = saved.grid_size;
    row.arrays->grid_area = saved.grid_area;
    row.arrays->grids_num = saved.grids_num;
    row.arrays->faces = mesh.faces();
    row.arrays->grid_to_face_map = destination->grid_to_face_map;
    if (saved.authority) {
      if (object.runtime->sculpt_session->subdiv_ccg != destination) {
        return refuse("The saved Sculpt editing authority does not own its graph's Multires grids");
      }
      row.authority = std::make_unique<bke::pbvh::Tree>(bke::pbvh::Tree::from_grids(mesh, *row.arrays));
      row.authority->restore_grid_normal_update_faces(Array<int, 0>(saved.pending_normal_faces.as_span()));
    }
    grids.append(std::move(row));
  }
  struct PreparedObject {
    SculptSession *session;
    decltype(SculptSession::persistent) persistent;
    std::unique_ptr<SculptUndoGridVisibility> visibility;
    BMLog *log = nullptr;
    std::unique_ptr<bke::pbvh::Tree> tree;
    ~PreparedObject() { if (log) { BM_log_free(log); } }
    PreparedObject() = default;
    PreparedObject(PreparedObject &&other) noexcept
        : session(other.session), persistent(std::move(other.persistent)), visibility(std::move(other.visibility)),
          log(std::exchange(other.log, nullptr)), tree(std::move(other.tree)) {}
  };
  Vector<PreparedObject> prepared;
  for (const int64_t i : cold.targets.index_range()) {
    ObjectState &saved = cold.plan->objects[i];
    PreparedObject row;
    row.session = cold.targets[i].object->runtime->sculpt_session;
    if (!row.session || row.session->cache || row.session->filter_cache || row.session->expand_cache) {
      return refuse("A selected Sculpt session was missing or busy while staging install");
    }
    row.persistent.sculpt_persistent_co = std::move(saved.persistent_co);
    row.persistent.sculpt_persistent_no = std::move(saved.persistent_no);
    row.persistent.sculpt_persistent_disp = std::move(saved.persistent_disp);
    row.persistent.grids_num = saved.persistent_grids_num;
    row.persistent.grid_size = saved.persistent_grid_size;
    if (saved.visibility.present) {
      row.visibility = std::make_unique<SculptUndoGridVisibility>();
      row.visibility->mesh_uid = cold.targets[i].mesh_uid;
      row.visibility->modifier_uid = saved.visibility.modifier_uid;
      row.visibility->verts_num = saved.visibility.verts_num;
      row.visibility->grids_num = saved.visibility.grids_num;
      row.visibility->grid_size = saved.visibility.grid_size;
      row.visibility->face_offsets = std::move(saved.visibility.face_offsets);
      row.visibility->corner_verts = std::move(saved.visibility.corner_verts);
      row.visibility->face_hidden = std::move(saved.visibility.face_hidden);
      row.visibility->grid_hidden = std::move(saved.visibility.grid_hidden);
      if (!row.visibility->matches_mesh(*cold.targets[i].mesh) ||
          !row.visibility->matches_modifier(*cold.targets[i].object)) {
        return refuse("Saved Multires grid visibility does not match its Mesh or modifier");
      }
    }
    if (saved.geometry) {
      row.log = BM_log_create(saved.geometry);
      if (!row.log) { return refuse("A dynamic-topology Sculpt log could not be created"); }
      /* PBVH's BMesh builder requires these derived runtime layers, even when the captured
       * session had no tree. Add them only to the already owned decoded installation mesh. */
      if (CustomData_get_offset_named(&saved.geometry->vdata, CD_PROP_INT32,
                                     ".sculpt_dyntopo_node_id_vertex") < 0) {
        BM_data_layer_add_named(saved.geometry, &saved.geometry->vdata, CD_PROP_INT32,
                                ".sculpt_dyntopo_node_id_vertex");
      }
      if (CustomData_get_offset_named(&saved.geometry->pdata, CD_PROP_INT32,
                                     ".sculpt_dyntopo_node_id_face") < 0) {
        BM_data_layer_add_named(saved.geometry, &saved.geometry->pdata, CD_PROP_INT32,
                                ".sculpt_dyntopo_node_id_face");
      }
      row.tree = std::make_unique<bke::pbvh::Tree>(bke::pbvh::Tree::from_bmesh(*saved.geometry));
    }
    prepared.append(std::move(row));
  }
  if (!valid()) { return refuse("Selected checkpoint owners changed while staging Sculpt install"); }
  /* Every allocation/evaluation/validation precedes publication. All per-owner arrays first. */
  for (PreparedGrid &row : grids) {
    row.destination->positions = std::move(row.arrays->positions);
    row.destination->normals = std::move(row.arrays->normals);
    row.destination->masks = std::move(row.arrays->masks);
    row.destination->grid_hidden = std::move(row.arrays->grid_hidden);
    row.destination->dirty.coords = row.saved->dirty_coords;
    row.destination->dirty.hidden = row.saved->dirty_hidden;
  }
  for (const int64_t i : prepared.index_range()) {
    PreparedObject &row = prepared[i];
    SculptSession &ss = *row.session;
    ObjectState &saved = cold.plan->objects[i];
    ss.bm = std::exchange(saved.geometry, nullptr);
    ss.bm_log = std::exchange(row.log, nullptr);
    if (row.tree) { ss.pbvh = std::move(row.tree); }
    ss.pivot_pos = saved.pivot_pos;
    ss.pivot_rot = saved.pivot_rot;
    ss.pivot_scale = saved.pivot_scale;
    ss.last_normal = saved.last_normal;
    ss.sticky_shading_color = saved.sticky_shading_color;
    ss.needs_flush_to_id = saved.needs_flush_to_id;
    ss.persistent = std::move(row.persistent);
    ss.angelo_undo_grid_visibility = std::move(row.visibility);
  }
  for (PreparedGrid &row : grids) {
    if (row.authority) {
      cold.targets[row.saved->object_index].object->runtime->sculpt_session->pbvh = std::move(row.authority);
    }
  }
  main.is_memfile_undo_flush_needed = cold.plan->flush_needed;
  const char *undo_error = nullptr;
  if (!ED_angelo_undo_checkpoint_initial(C, owner, &undo_error)) {
    BKE_report(reports, RPT_ERROR, undo_error ? undo_error : "Checkpoint initial Undo was not published");
    return false;
  }
  cold.installed = true;
  return true;
}

void WM_checkpoint_cold_free(WMCheckpointColdInstall *cold)
{
  if (!cold) { return; }
  for (WMCheckpointColdCallback *token : cold->callback_tokens) { delete token; }
  WM_checkpoint_cold_runtime_end(*cold);
  delete cold;
}

}  // namespace blender
