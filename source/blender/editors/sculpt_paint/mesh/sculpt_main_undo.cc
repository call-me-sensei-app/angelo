/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup edsculpt
 *
 * Runtime adjuncts for Angelo's typed Main Undo boundaries. Main owns serialized IDs;
 * Sculpt steps still own their mutable BMLog entries. A snapshot owns only immutable values.
 */

#include "MEM_guardedalloc.h"

#include <cstring>
#include <limits>

#include "BLI_listbase.h"
#include "BLI_map.hh"
#include "BLI_set.hh"
#include "BLI_vector.hh"

#include "DNA_mesh_types.h"
#include "DNA_object_types.h"

#include "BKE_context.hh"
#include "BKE_main.hh"
#include "BKE_mesh.hh"
#include "BKE_object.hh"
#include "BKE_object_types.hh"
#include "BKE_paint.hh"
#include "BKE_paint_bvh.hh"
#include "BKE_sculpt_undo.hh"
#include "BKE_undo_system.hh"

#include "ED_sculpt.hh"
#include "ED_undo.hh"

#include "DEG_depsgraph.hh"

#include "WM_api.hh"
#include "WM_types.hh"

#include "bmesh.hh"
#include "intern/bmesh_snapshot.hh"
#include "sculpt_main_undo_grids.hh"
#include "sculpt_main_undo_snapshot_access.hh"
#include "sculpt_undo.hh"

namespace blender::ed::sculpt_paint::undo {

struct LogSnapshotDeleter {
  void operator()(BMLogSnapshot *snapshot) const
  {
    BM_log_snapshot_free(snapshot);
  }
};

struct GeometrySnapshotDeleter {
  void operator()(BMGeometrySnapshot *snapshot) const
  {
    BM_geometry_snapshot_free(snapshot);
  }
};

struct GridStateDeleter {
  void operator()(MainUndoGridState *state) const
  {
    main_undo_grids_free(state);
  }
};

struct GridRestoreDeleter {
  void operator()(MainUndoGridRestore *restore) const
  {
    main_undo_grids_discard(restore);
  }
};

struct BMeshDeleter {
  void operator()(BMesh *bm) const
  {
    BM_mesh_free(bm);
  }
};

struct LogRestoreDeleter {
  void operator()(BMLogSnapshotRestore *restore) const
  {
    BM_log_snapshot_restore_discard(restore);
  }
};

/* Constructed only after transient admission, never during live retirement. */
struct CheckpointReplayDeformCaches {
  decltype(SculptSession::vert_normals_deform) vert_normals;
  decltype(SculptSession::face_normals_deform) face_normals;
};

struct MainUndoObject {
  uint32_t object_uid;
  uint32_t mesh_uid;
  float3 pivot_pos;
  float4 pivot_rot;
  float3 pivot_scale;
  float3 last_normal;
  bool sticky_shading_color;
  bool needs_flush_to_id;
  decltype(SculptSession::persistent) persistent;
  std::unique_ptr<SculptUndoGridVisibility> visibility;
  uint64_t observed_log_epoch = 0, observed_log_cursor = 0;
  std::unique_ptr<BMLogSnapshot, LogSnapshotDeleter> dyntopo;
  std::unique_ptr<BMGeometrySnapshot, GeometrySnapshotDeleter> geometry;
  SculptSession *observed_session = nullptr;
  const BMesh *observed_bmesh = nullptr;
  const BMLog *observed_log = nullptr;
  std::unique_ptr<BMesh, BMeshDeleter> replay_bmesh;
  std::unique_ptr<BMLogSnapshotRestore, LogRestoreDeleter> replay_log;
  std::unique_ptr<bke::pbvh::Tree> replay_tree;
  std::unique_ptr<CheckpointReplayDeformCaches> replay_deform;
};

#ifdef WITH_GTESTS
struct MainUndoTestFault {
  Main *source_main;
  UndoStack *source_stack;
  uint object_index;
  uint object_uid;
  int grid_owner_index = -1;
  uint consumed_scene_uid = 0;
  bool armed = true;
  bool decode_started = false;
  bool consumed = false;
};
#endif

struct MainUndoState {
  int references = 1;
  Vector<MainUndoObject> objects;
  std::unique_ptr<MainUndoGridState, GridStateDeleter> grids;
  bool flush_needed;
#ifdef WITH_GTESTS
  std::shared_ptr<MainUndoTestFault> test_fault;
#endif
};

#ifdef WITH_GTESTS
std::shared_ptr<MainUndoTestFault> main_undo_state_test_fault_arm(MainUndoState &state,
                                                                  Main *source_main,
                                                                  UndoStack *source_stack,
                                                                  const uint object_index,
                                                                  const char **r_error,
                                                                  const int grid_owner_index)
{
  *r_error = nullptr;
  if (!source_main || !source_stack || object_index >= state.objects.size()) {
    *r_error = "The Sculpt Undo test fault requires a valid source and saved object index";
    return nullptr;
  }
  if (grid_owner_index < -1 ||
      (grid_owner_index >= 0 &&
       (!state.grids || uint(grid_owner_index) >= main_undo_grids_test_owner_count(
                                                   *state.grids, state.objects[object_index].object_uid))))
  {
    *r_error = "The Sculpt Undo test fault requires a valid saved grid owner ordinal";
    return nullptr;
  }
  if (state.test_fault && state.test_fault->armed) {
    *r_error = "This Sculpt Undo state already has an armed test fault";
    return nullptr;
  }
  auto fault = std::make_shared<MainUndoTestFault>();
  fault->source_main = source_main;
  fault->source_stack = source_stack;
  fault->object_index = object_index;
  fault->object_uid = state.objects[object_index].object_uid;
  fault->grid_owner_index = grid_owner_index;
  state.test_fault = fault;
  return fault;
}

void main_undo_state_test_fault_disarm(MainUndoTestFault &fault)
{
  fault.armed = false;
  fault.decode_started = false;
  fault.source_main = nullptr;
  fault.source_stack = nullptr;
}

void main_undo_state_test_fault_begin_decode(const MainUndoState &state,
                                             Main *source_main,
                                             UndoStack *source_stack)
{
  MainUndoTestFault *fault = state.test_fault.get();
  if (!fault || !fault->armed) {
    return;
  }
  if (fault->source_main != source_main || fault->source_stack != source_stack) {
    main_undo_state_test_fault_disarm(*fault);
    return;
  }
  fault->decode_started = true;
  /* Consume the source binding even if the MemFile reader fails before runtime preparation.
   * A later decode attempt must not re-arm this fault against a still-live original Main. */
  fault->source_main = nullptr;
  fault->source_stack = nullptr;
}

bool main_undo_state_test_fault_consumed(const MainUndoTestFault &fault)
{
  return fault.consumed;
}

unsigned int main_undo_state_test_fault_object_uid(const MainUndoTestFault &fault)
{
  return fault.object_uid;
}

unsigned int main_undo_state_test_fault_scene_uid(const MainUndoTestFault &fault)
{
  return fault.consumed_scene_uid;
}

struct TestFaultDecodeScope {
  MainUndoTestFault *fault;

  ~TestFaultDecodeScope()
  {
    /* A real preparation error before the selected object must not defer the injected fault to
     * some later Undo attempt. The source binding is single-use even when never consumed. */
    if (fault && fault->decode_started) {
      main_undo_state_test_fault_disarm(*fault);
    }
  }
};
#endif

static MainUndoState *main_undo_state_capture_impl(Main &bmain, const char **r_error,
                                                  const bool capture_history,
                                                  size_t *used = nullptr,
                                                  const size_t limit = 0,
                                                  const Span<uint32_t> selected = {},
                                                  const bool transient = false)
{
  if (transient && !DEG_checkpoint_runtime_observation_allowed(&bmain)) {
    *r_error = "Checkpoint observation requires a suspended no-worker lease"; return nullptr;
  }
  if (used) { *r_error = nullptr; }
  if (used && !checkpoint_snapshot_charge(*used, limit, sizeof(MainUndoState))) {
    *r_error = "Checkpoint snapshot control exceeds preallocation admission"; return nullptr;
  }
  bool has_running_jobs = false;
  for (const wmWindowManager &wm : bmain.wm) {
    has_running_jobs |= WM_jobs_has_running(&wm);
  }
  if (has_running_jobs) {
    for (const Object &object : bmain.objects) {
      const SculptSession *ss = object.runtime->sculpt_session;
      if ((object.mode & OB_MODE_SCULPT) && ss && ss->subdiv_ccg) {
        *r_error = "Wait for background jobs to finish before capturing Multires Undo state";
        return nullptr;
      }
    }
  }
  /* Prove borrowed CCG ownership before any visibility helper may inspect that pointer. */
  std::unique_ptr<MainUndoGridState, GridStateDeleter> grids(
      transient ? main_undo_grids_observe_checkpoint(bmain, selected, limit, *used, r_error) :
      used ? main_undo_grids_capture_checkpoint(bmain, limit, *used, r_error) :
             main_undo_grids_capture(bmain, r_error));
  if (*r_error) {
    return nullptr;
  }
  std::unique_ptr<MainUndoState> state;
  for (const Object &object : bmain.objects) {
    if (transient && !selected.contains(object.id.session_uid)) { continue; }
    const SculptSession *ss = object.runtime->sculpt_session;
    if (!(object.mode & OB_MODE_SCULPT) || !ss) {
      continue;
    }
    if (ss->cache || ss->filter_cache || ss->expand_cache) {
      *r_error = "Finish the active Sculpt interaction before saving the native commit Undo state";
      return nullptr;
    }
    if (object.type != OB_MESH || !object.data) {
      *r_error = "The Sculpt object no longer owns a mesh for its Undo snapshot";
      return nullptr;
    }
    if (used) {
      const size_t bytes[] = {sizeof(MainUndoObject) * 4,
          size_t(ss->persistent.sculpt_persistent_co.as_span().size_in_bytes()),
          size_t(ss->persistent.sculpt_persistent_no.as_span().size_in_bytes()),
          size_t(ss->persistent.sculpt_persistent_disp.as_span().size_in_bytes())};
      for (const size_t size : bytes) {
        if (!checkpoint_snapshot_charge(*used, limit, size)) {
          *r_error = "Checkpoint original arrays exceed preallocation admission"; return nullptr;
        }
      }
      if (ss->angelo_undo_grid_visibility &&
          !checkpoint_snapshot_charge(*used, limit, ss->angelo_undo_grid_visibility->memory_size())) {
        *r_error = "Checkpoint pending visibility exceeds preallocation admission"; return nullptr;
      }
    }
    if (!state) {
      state = std::make_unique<MainUndoState>();
      state->grids = std::move(grids);
      state->flush_needed = bmain.is_memfile_undo_flush_needed;
    }
    MainUndoObject saved;
    saved.object_uid = object.id.session_uid;
    saved.mesh_uid = object.data->session_uid;
    saved.pivot_pos = ss->pivot_pos;
    saved.pivot_rot = ss->pivot_rot;
    saved.pivot_scale = ss->pivot_scale;
    saved.last_normal = ss->last_normal;
    saved.sticky_shading_color = ss->sticky_shading_color;
    saved.needs_flush_to_id = ss->needs_flush_to_id;
    saved.persistent = ss->persistent;
    if (transient && ss->bm_log) {
      saved.observed_log_epoch = BM_log_epoch(ss->bm_log);
      saved.observed_log_cursor = BM_log_cursor_id(ss->bm_log);
    }
    if (transient) {
      /* This owner is independent of evaluation. Never call the visibility helper that can
       * follow a borrowed ss.subdiv_ccg while its cache is being replaced. */
      if (ss->angelo_undo_grid_visibility) {
        saved.visibility = std::make_unique<SculptUndoGridVisibility>(*ss->angelo_undo_grid_visibility);
      }
    }
    else if (!state->grids || !main_undo_grids_has_object(*state->grids, saved.object_uid)) {
      saved.visibility = BKE_sculpt_undo_grid_visibility_capture(object, r_error);
      if (*r_error) {
        return nullptr;
      }
    }
    if (ss->bm) {
      if (capture_history) {
        /* Snapshot owns live vertex/triangle identity arrays and a temporary unique-ID Set,
         * not history geometry. Reserve their power-of-two backing before snapshot creation. */
        if (used) {
          const uint64_t count = uint64_t(ss->bm->totvert) + uint64_t(ss->bm->totface);
          if (ss->bm->totvert < 0 || ss->bm->totface < 0 ||
              count > (SIZE_MAX - 65536) / 512 ||
              !checkpoint_snapshot_charge(*used, limit, 65536 + size_t(count) * 512)) {
            *r_error = "Initial checkpoint BMLog identities exceed finite admission";
            return nullptr;
          }
        }
        saved.dyntopo.reset(BM_log_snapshot_create(ss->bm, ss->bm_log, r_error));
        if (!saved.dyntopo) { return nullptr; }
      }
      saved.geometry.reset(used ? BM_geometry_snapshot_create_bounded(ss->bm, limit, *used, r_error) :
                                  BM_geometry_snapshot_create(ss->bm, r_error));
      if (!saved.geometry) {
        return nullptr;
      }
    }
    if (transient) {
      if (!checkpoint_snapshot_charge(*used, limit, sizeof(CheckpointReplayDeformCaches) + 65536)) {
        *r_error = "Checkpoint deform-cache staging exceeds finite admission"; return nullptr;
      }
      saved.replay_deform = std::make_unique<CheckpointReplayDeformCaches>();
      saved.observed_session = const_cast<SculptSession *>(ss);
      saved.observed_bmesh = ss->bm; saved.observed_log = ss->bm_log;
      const Mesh &mesh = *reinterpret_cast<const Mesh *>(object.data);
      const uint64_t tree_items = ss->bm ? uint64_t(ss->bm->totvert) + uint64_t(ss->bm->totedge) +
                                             uint64_t(ss->bm->totloop) + uint64_t(ss->bm->totface) :
                                         uint64_t(mesh.verts_num) + uint64_t(mesh.faces_num) +
                                             uint64_t(mesh.corners_num);
      if (tree_items > (SIZE_MAX - 65536) / 4096 ||
          !checkpoint_snapshot_charge(*used, limit, 65536 + size_t(tree_items) * 4096)) {
        *r_error = "Checkpoint original PBVH replay exceeds finite admission"; return nullptr;
      }
      if (ss->bm) {
        saved.replay_bmesh.reset(BM_geometry_snapshot_restore_bounded(
            saved.geometry.get(), limit, *used, r_error));
        if (!saved.replay_bmesh) { return nullptr; }
        saved.replay_log.reset(BM_log_snapshot_restore_prepare_bounded(
            saved.replay_bmesh.get(), saved.dyntopo.get(), ss->bm_log, limit, *used, r_error));
        if (!saved.replay_log) { return nullptr; }
        /* As in complete Main Undo, rebuilt BMesh installs with a lazy PBVH. Building one
         * here would rewrite its derived node-ID value layers before the exact replay. */
      }
      else if (!ss->subdiv_ccg) {
        saved.replay_tree = std::make_unique<bke::pbvh::Tree>(bke::pbvh::Tree::from_mesh(mesh));
      }
    }
    state->objects.append(std::move(saved));
  }
  return state.release();
}

MainUndoState *main_undo_state_capture(Main &bmain, const char **r_error)
{
  return main_undo_state_capture_impl(bmain, r_error, true);
}

MainUndoState *main_undo_state_capture_checkpoint(Main &bmain, const size_t limit,
                                                 const char **r_error)
{
  /* Durable recovery creates a fresh log; historical incremental addresses are irrelevant.
   * Keep identical current geometry/grid/scalar/persistent/visibility/flush capture admission. */
  size_t used = 0;
  return main_undo_state_capture_impl(bmain, r_error, false, &used, limit);
}

MainUndoState *main_undo_state_capture_initial_checkpoint(Main &bmain, const size_t limit,
                                                         size_t &used, const char **r_error)
{
  return main_undo_state_capture_impl(bmain, r_error, true, &used, limit);
}

MainUndoState *main_undo_state_observe_checkpoint(Main &bmain, const Span<uint32_t> selected,
                                                 const size_t limit, size_t &used,
                                                 const char **r_error)
{
  return main_undo_state_capture_impl(bmain, r_error, true, &used, limit, selected, true);
}

bool main_undo_state_replay_checkpoint(Main &bmain, MainUndoState &state, const char **r_error)
{
  if (!DEG_checkpoint_runtime_observation_allowed(&bmain)) {
    *r_error = "Selected runtime replay requires its protected no-worker lease"; return false;
  }
  /* Validate all owners before the first publication. Resolve the actual post-callback
   * Object/Mesh relations; never restore the installer baseline's older relationships. */
  for (MainUndoObject &saved : state.objects) {
    Object *object = nullptr; Mesh *mesh = nullptr;
    for (Object &candidate : bmain.objects) {
      if (candidate.id.session_uid == saved.object_uid) { object = &candidate; break; }
    }
    for (Mesh &candidate : bmain.meshes) {
      if (candidate.id.session_uid == saved.mesh_uid) { mesh = &candidate; break; }
    }
    if (!object || !mesh || object->data != &mesh->id ||
        object->runtime->sculpt_session != saved.observed_session || !saved.observed_session ||
        !saved.replay_deform ||
        saved.observed_session->cache || saved.observed_session->filter_cache ||
        saved.observed_session->expand_cache || saved.observed_session->tex_pool) {
      *r_error = "Edited Sculpt runtime owner no longer has its captured live binding"; return false;
    }
    SculptSession &ss = *saved.observed_session;
    for (const Object &other : bmain.objects) {
      const SculptSession *other_ss = other.runtime->sculpt_session;
      if (&other != object && other_ss && (other_ss == &ss ||
          (ss.bm && ss.bm == other_ss->bm) || (ss.bm_log && ss.bm_log == other_ss->bm_log))) {
        *r_error = "Edited Sculpt replay cannot retire a shared live runtime owner"; return false;
      }
    }
    if (saved.replay_log && !BM_log_snapshot_restore_replay_validate(saved.replay_log.get(), ss.bm_log)) {
      *r_error = "Edited Sculpt history chain changed ownership during native unwind"; return false;
    }
  }
  if (state.grids && !main_undo_grids_replay_checkpoint(bmain, *state.grids, false, r_error)) { return false; }
  for (MainUndoObject &saved : state.objects) {
    SculptSession &ss = *saved.observed_session;
    Object *object = nullptr;
    for (Object &candidate : bmain.objects) {
      if (candidate.id.session_uid == saved.object_uid) { object = &candidate; break; }
    }
    BKE_sculptsession_free_pbvh(*object);
    ss.deform_cos = {}; ss.deform_imats = {};
    ss.vert_normals_deform = std::move(saved.replay_deform->vert_normals);
    ss.face_normals_deform = std::move(saved.replay_deform->face_normals);
    ss.multires_modifier = nullptr; ss.shapekey_active = nullptr;
    ss.subdiv_ccg = nullptr; /* Exact fine authority is rebound below, never an old borrow. */
    ss.deform_modifiers_active = false; ss.rv3d = nullptr; ss.v3d = nullptr;
    ss.pose_ik_chain_preview.reset(); ss.boundary_preview.reset();
    ss.cursor_sampled_normal.reset(); ss.last_paint_canvas_key.reset();
    if (ss.bm_log) { BM_log_free(ss.bm_log); ss.bm_log = nullptr; }
    if (ss.bm) { BM_mesh_free(ss.bm); ss.bm = nullptr; }
    ss.bm = saved.replay_bmesh.release();
    ss.bm_log = saved.replay_log ? BM_log_snapshot_restore_commit(saved.replay_log.release()) : nullptr;
    ss.pbvh = std::move(saved.replay_tree);
    ss.pivot_pos = saved.pivot_pos; ss.pivot_rot = saved.pivot_rot; ss.pivot_scale = saved.pivot_scale;
    ss.last_normal = saved.last_normal; ss.sticky_shading_color = saved.sticky_shading_color;
    ss.needs_flush_to_id = saved.needs_flush_to_id;
    ss.persistent = std::move(saved.persistent);
    ss.angelo_undo_grid_visibility = std::move(saved.visibility);
  }
  if (state.grids && !main_undo_grids_replay_checkpoint(bmain, *state.grids, true, r_error)) { return false; }
  bmain.is_memfile_undo_flush_needed = state.flush_needed;
  return true;
}

size_t main_undo_state_memory_size(const MainUndoState &state)
{
  size_t size = sizeof(state);
  const auto add_bytes = [&size](const size_t bytes) {
    size = bytes > std::numeric_limits<size_t>::max() - size ? std::numeric_limits<size_t>::max() :
                                                               size + bytes;
  };
  /* Conservatively include inline capacities too; no reserved heap payload may disappear from
   * the Undo budget merely because a vector has fewer live elements than allocated slots. */
  add_bytes(size_t(state.objects.capacity()) * sizeof(MainUndoObject));
  if (state.grids) {
    add_bytes(main_undo_grids_memory_size(*state.grids));
  }
  for (const MainUndoObject &object : state.objects) {
    add_bytes(size_t(object.persistent.sculpt_persistent_co.size()) * sizeof(float3));
    add_bytes(size_t(object.persistent.sculpt_persistent_no.size()) * sizeof(float3));
    add_bytes(size_t(object.persistent.sculpt_persistent_disp.size()) * sizeof(float));
    if (object.visibility) {
      add_bytes(object.visibility->memory_size());
    }
    if (object.dyntopo) {
      add_bytes(BM_log_snapshot_memory_size(object.dyntopo.get()));
    }
    if (object.geometry) {
      add_bytes(BM_geometry_snapshot_memory_size(object.geometry.get()));
    }
  }
  return size;
}

template<typename T> static bool values_equal(const Span<T> a, const Span<T> b)
{
  return a.size() == b.size() &&
         (a.is_empty() || std::memcmp(a.data(), b.data(), a.size_in_bytes()) == 0);
}

static bool visibility_equal(const SculptUndoGridVisibility &a, const SculptUndoGridVisibility &b)
{
  if (a.mesh_uid != b.mesh_uid || a.modifier_uid != b.modifier_uid || a.verts_num != b.verts_num ||
      a.grids_num != b.grids_num || a.grid_size != b.grid_size ||
      !values_equal(a.face_offsets.as_span(), b.face_offsets.as_span()) ||
      !values_equal(a.corner_verts.as_span(), b.corner_verts.as_span()) ||
      !bits::spans_equal(BoundedBitSpan(a.face_hidden), BoundedBitSpan(b.face_hidden)) ||
      a.grid_hidden.size() != b.grid_hidden.size() ||
      a.grid_hidden.group_size() != b.grid_hidden.group_size())
  {
    return false;
  }
  for (const int64_t i : a.grid_hidden.index_range()) {
    if (!bits::spans_equal(a.grid_hidden[i], b.grid_hidden[i])) {
      return false;
    }
  }
  return true;
}

bool main_undo_state_equals(const MainUndoState &a, const MainUndoState &b)
{
  if (a.objects.size() != b.objects.size() || a.flush_needed != b.flush_needed ||
      bool(a.grids) != bool(b.grids) || (a.grids && !main_undo_grids_equals(*a.grids, *b.grids)))
  {
    return false;
  }
  for (const int64_t i : a.objects.index_range()) {
    const MainUndoObject &x = a.objects[i];
    const MainUndoObject &y = b.objects[i];
    /* Compare float representations too: tolerant equality cannot justify discarding a saved
     * artist state. Object order may cause a conservative mismatch, never a false equality. */
    if (x.object_uid != y.object_uid || x.mesh_uid != y.mesh_uid ||
        x.observed_log_epoch != y.observed_log_epoch || x.observed_log_cursor != y.observed_log_cursor ||
        std::memcmp(&x.pivot_pos, &y.pivot_pos, sizeof(x.pivot_pos)) != 0 ||
        std::memcmp(&x.pivot_rot, &y.pivot_rot, sizeof(x.pivot_rot)) != 0 ||
        std::memcmp(&x.pivot_scale, &y.pivot_scale, sizeof(x.pivot_scale)) != 0 ||
        std::memcmp(&x.last_normal, &y.last_normal, sizeof(x.last_normal)) != 0 ||
        x.sticky_shading_color != y.sticky_shading_color ||
        x.needs_flush_to_id != y.needs_flush_to_id ||
        x.persistent.grids_num != y.persistent.grids_num ||
        x.persistent.grid_size != y.persistent.grid_size ||
        !values_equal(x.persistent.sculpt_persistent_co.as_span(),
                      y.persistent.sculpt_persistent_co.as_span()) ||
        !values_equal(x.persistent.sculpt_persistent_no.as_span(),
                      y.persistent.sculpt_persistent_no.as_span()) ||
        !values_equal(x.persistent.sculpt_persistent_disp.as_span(),
                      y.persistent.sculpt_persistent_disp.as_span()) ||
        bool(x.visibility) != bool(y.visibility) || bool(x.dyntopo) != bool(y.dyntopo) ||
        bool(x.geometry) != bool(y.geometry))
    {
      return false;
    }
    if ((x.visibility && !visibility_equal(*x.visibility, *y.visibility)) ||
        (x.dyntopo && !BM_log_snapshot_equals(x.dyntopo.get(), y.dyntopo.get())) ||
        (x.geometry && !BM_geometry_snapshot_equals(x.geometry.get(), y.geometry.get())) ||
        !BM_log_snapshot_restore_replay_equals(x.replay_log.get(), y.replay_log.get()))
    {
      return false;
    }
  }
  return true;
}

MainUndoState *main_undo_state_retain(MainUndoState *state)
{
  if (state) {
    BLI_assert(state->references > 0);
    state->references++;
  }
  return state;
}

bool main_undo_state_has_grids(const MainUndoState *state)
{
  return state && state->grids;
}

void main_undo_state_free(MainUndoState *state)
{
  if (state && --state->references == 0) {
    delete state;
  }
}

struct SessionDeleter {
  void operator()(SculptSession *session) const
  {
    MEM_delete(session);
  }
};

struct PreparedObject {
  Object *object;
  SculptSession *session;
  const MainUndoObject *saved;
  decltype(SculptSession::persistent) persistent;
  std::unique_ptr<SculptUndoGridVisibility> visibility;
  std::unique_ptr<BMesh, BMeshDeleter> bm;
  std::unique_ptr<BMLogSnapshotRestore, LogRestoreDeleter> log;
  /* Default construction allocates SharedCache storage. Do this before releasing any runtime,
   * rather than calling BKE_sculptsession_free_deformMats in the final phase. */
  decltype(SculptSession::vert_normals_deform) vert_normals;
  decltype(SculptSession::face_normals_deform) face_normals;
};

static bool runtime_is_idle(const SculptSession &ss, const char **r_error)
{
  if (ss.cache || ss.filter_cache || ss.expand_cache) {
    *r_error = "An active Sculpt interaction still owns the Undo runtime";
    return false;
  }
  BLI_assert(!ss.cache && !ss.filter_cache && !ss.expand_cache);
  /* Image pools retain Image pointers and their destructor dereferences those IDs. The checked
   * MemFile reader must dispose them before replacing Main, while the old Images still exist. */
  if (ss.tex_pool) {
    *r_error =
        "The previous Sculpt image pool was not released before replacing the Undo document";
    return false;
  }
  return true;
}

static bool runtime_owners_are_unique(Main &bmain, const char **r_error)
{
  Set<SculptSession *> sessions;
  Set<BMesh *> meshes;
  Set<BMLog *> logs;
  for (Object &object : bmain.objects) {
    SculptSession *ss = object.runtime->sculpt_session;
    if (!ss) {
      continue;
    }
    if (!runtime_is_idle(*ss, r_error)) {
      return false;
    }
    if (!sessions.add(ss) || (ss->bm && !meshes.add(ss->bm)) ||
        (ss->bm_log && !logs.add(ss->bm_log)))
    {
      *r_error = "Several objects share ownership of the restored Sculpt runtime";
      return false;
    }
  }
  return true;
}

static bool prepare_dyntopo(PreparedObject &prepared,
                            const UndoStack &stack,
                            Set<uint64_t> &epochs,
                            Set<BMLogEntry *> &retained_entries,
                            const char **r_error)
{
  const MainUndoObject &saved = *prepared.saved;
  const uint64_t epoch = BM_log_snapshot_epoch(saved.dyntopo.get());
  if (!epochs.add(epoch)) {
    *r_error = "Several saved Sculpt objects share one Dyntopo history epoch";
    return false;
  }
  const Vector<BMLogEntry *> entries = main_undo_log_entries(stack, saved.object_uid, epoch);
  for (BMLogEntry *entry : entries) {
    if (!retained_entries.add(entry)) {
      *r_error = "Several saved Sculpt objects share one retained Dyntopo history entry";
      return false;
    }
  }
  prepared.bm.reset(BM_geometry_snapshot_restore(saved.geometry.get(), r_error));
  if (!prepared.bm) {
    return false;
  }
  BMesh *bm = prepared.bm.get();

  prepared.log.reset(BM_log_snapshot_restore_prepare(
      bm, saved.dyntopo.get(), entries, prepared.session->bm_log, r_error));
  return bool(prepared.log);
}

static void discard_bmesh_no_flush(SculptSession &ss)
{
  if (ss.bm_log) {
    BM_log_free(ss.bm_log);
    ss.bm_log = nullptr;
  }
  if (ss.bm) {
    BM_mesh_free(ss.bm);
    ss.bm = nullptr;
  }
}

struct MainUndoBMeshRead {
  struct Saved {
    Object *object;
    uint32_t object_uid;
    bke::ObjectRuntime *runtime;
    SculptSession *session;
    BMLog *log;
    uint64_t log_epoch;
    BMesh *mesh;
  };
  Vector<Saved> objects;
  Map<SculptSession *, int64_t> sessions;

  ~MainUndoBMeshRead()
  {
    for (Saved &saved : objects) {
      if (saved.mesh) {
        BM_mesh_free(saved.mesh);
      }
    }
  }
};

MainUndoBMeshRead *main_undo_bmesh_read_begin(Main &bmain, const char **r_error)
{
  if (!runtime_owners_are_unique(bmain, r_error)) {
    return nullptr;
  }
  auto read = std::make_unique<MainUndoBMeshRead>();
  for (Object &object : bmain.objects) {
    SculptSession *ss = object.runtime->sculpt_session;
    if (!ss || !ss->bm) {
      continue;
    }
    read->sessions.add_new(ss, read->objects.size());
    read->objects.append({&object,
                          object.id.session_uid,
                          object.runtime,
                          ss,
                          ss->bm_log,
                          ss->bm_log ? BM_log_epoch(ss->bm_log) : 0,
                          ss->bm});
  }
  /* Finish all allocation and admission before taking ownership. The reader may swap a restored
   * Mesh into its old address before freeing the old Object. Its normal session destructor would
   * then flush the abandoned BMesh into that restored Mesh. Keep the log/session/PBVH intact:
   * unchanged Objects can be reused, and their Sculpt history must retain its original owner. */
  for (MainUndoBMeshRead::Saved &saved : read->objects) {
    saved.session->bm = nullptr;
  }
  return read.release();
}

bool main_undo_bmesh_read_end(Main &bmain, MainUndoBMeshRead *read_ptr, const char **r_error)
{
  std::unique_ptr<MainUndoBMeshRead> read(read_ptr);
  bool valid = true;
  /* Only dereference owners in the current Main. Saved Object/runtime/session pointers may have
   * been freed; pointer, UID and log-epoch checks prevent transferring a mesh to a new owner. */
  for (Object &object : bmain.objects) {
    SculptSession *ss = object.runtime->sculpt_session;
    const int64_t *index = read->sessions.lookup_ptr(ss);
    if (!index) {
      continue;
    }
    MainUndoBMeshRead::Saved &saved = read->objects[*index];
    if (&object != saved.object || object.id.session_uid != saved.object_uid ||
        object.runtime != saved.runtime || ss->bm || ss->bm_log != saved.log ||
        (ss->bm_log && BM_log_epoch(ss->bm_log) != saved.log_epoch))
    {
      /* A surviving cache must not retain pointers into the mesh about to be retired. The
       * caller quarantines the failed decode; do not overwrite a different live BMesh. */
      BKE_sculptsession_free_pbvh(object);
      if (ss->bm == saved.mesh) {
        ss->bm = nullptr;
      }
      *r_error = "Dyntopo runtime ownership changed during the protected Undo read";
      valid = false;
      continue;
    }
    ss->bm = saved.mesh;
    saved.mesh = nullptr;
  }
  return valid;
}

bool main_undo_state_restore(bContext *C, const MainUndoState &state, const char **r_error)
{
#ifdef WITH_GTESTS
  TestFaultDecodeScope test_fault_scope{state.test_fault.get()};
#endif
  Main *bmain = CTX_data_main(C);
  wmWindowManager *wm = CTX_wm_manager(C);
  UndoStack *stack = wm ? wm->runtime->undo_stack : nullptr;
  if (!bmain || !stack || stack->step_init) {
    *r_error = "The Sculpt Undo runtime cannot be restored during an unfinished Undo step";
    return false;
  }
  std::unique_ptr<MainUndoGridRestore, GridRestoreDeleter> grids;
  if (state.grids) {
    /* Main is already installed. Establish safe empty temporary sessions before evaluating any
     * owner graph, so abandoned BMeshes and borrowed grid pointers cannot enter that evaluation.
     * This is preparation, not a promise to retain the state being replaced on restore failure. */
    if (!main_undo_state_discard_runtime(*bmain, r_error)) {
      return false;
    }
#ifdef WITH_GTESTS
    MainUndoGridPrepareTestFault grid_fault{};
    MainUndoTestFault *fault = test_fault_scope.fault;
    const bool inject_grid = fault && fault->armed && fault->decode_started &&
                             fault->grid_owner_index >= 0;
    if (inject_grid) {
      grid_fault.object_uid = fault->object_uid;
      grid_fault.owner_index = uint(fault->grid_owner_index);
    }
    grids.reset(main_undo_grids_prepare(
        *bmain, *state.grids, r_error, inject_grid ? &grid_fault : nullptr));
    if (inject_grid && grid_fault.consumed) {
      fault->consumed = true;
      fault->consumed_scene_uid = grid_fault.consumed_scene_uid;
      main_undo_state_test_fault_disarm(*fault);
    }
#else
    grids.reset(main_undo_grids_prepare(*bmain, *state.grids, r_error));
#endif
    if (!grids) {
      return false;
    }
  }
  if (!runtime_owners_are_unique(*bmain, r_error)) {
    return false;
  }
  Map<uint32_t, Object *> objects;
  for (Object &object : bmain->objects) {
    if (!object.id.session_uid || !objects.add(object.id.session_uid, &object)) {
      *r_error = "The restored Undo document contains duplicate object identities";
      return false;
    }
  }
  Vector<PreparedObject> prepared_objects;
  prepared_objects.reserve(state.objects.size());
  Set<uint32_t> object_uids;
  Set<uint64_t> epochs;
  Set<BMLogEntry *> retained_entries;
  for (const int64_t object_index : state.objects.index_range()) {
    const MainUndoObject &saved = state.objects[object_index];
    uint32_t lookup_uid = saved.object_uid;
#ifdef WITH_GTESTS
    MainUndoTestFault *fault = test_fault_scope.fault;
    if (fault && fault->armed && fault->decode_started && fault->grid_owner_index == -1 &&
        fault->object_index == object_index)
    {
      /* Exercise the real missing-object validator after all preceding preparations. Only this
       * local lookup changes; the saved snapshot remains immutable and reusable. */
      lookup_uid = 0;
      fault->consumed = true;
      main_undo_state_test_fault_disarm(*fault);
    }
#endif
    Object *object = objects.lookup_default(lookup_uid, nullptr);
    if (!object || object->type != OB_MESH || !object->data ||
        object->data->session_uid != saved.mesh_uid || !(object->mode & OB_MODE_SCULPT) ||
        !object->runtime->sculpt_session ||
        !(object->runtime->sculpt_session->mode_type & OB_MODE_SCULPT) ||
        !object_uids.add(saved.object_uid))
    {
      *r_error = "The saved Sculpt runtime does not match the restored Undo document";
      return false;
    }
    const Mesh &mesh = *id_cast<Mesh *>(object->data);
    if (bool(saved.dyntopo) != bool(saved.geometry)) {
      *r_error = "The saved Dyntopo geometry and history are incomplete";
      return false;
    }
    if (saved.visibility &&
        (!saved.visibility->matches_mesh(mesh) || !saved.visibility->matches_modifier(*object)))
    {
      *r_error =
          "The saved Multires visibility does not match the restored Undo mesh and modifier";
      return false;
    }
    PreparedObject prepared;
    prepared.object = object;
    prepared.session = object->runtime->sculpt_session;
    prepared.saved = &saved;
    prepared.persistent = saved.persistent;
    if (saved.visibility) {
      prepared.visibility = std::make_unique<SculptUndoGridVisibility>(*saved.visibility);
    }
    if (saved.dyntopo && !prepare_dyntopo(prepared, *stack, epochs, retained_entries, r_error)) {
      return false;
    }
    prepared_objects.append(std::move(prepared));
  }

  /* Without grids, keep the existing lazy rebuild after runtime binding. Grid preparation has
   * already tagged and evaluated every required owner; tagging again would invalidate its exact
   * prepared destinations after publication. */
  if (!grids) {
    for (PreparedObject &prepared : prepared_objects) {
      DEG_id_tag_update_ex(bmain, &prepared.object->id, ID_RECALC_GEOMETRY);
      DEG_id_tag_update_ex(bmain, prepared.object->data, ID_RECALC_GEOMETRY);
    }
  }

  /* No validation, allocation, evaluation, or callbacks may follow the first runtime release.
   * Preserve the sessions themselves, but invalidate every borrowed geometry/evaluation pointer.
   * An old Dyntopo runtime must also be discarded when the target snapshot is ordinary Sculpt. */
  for (PreparedObject &prepared : prepared_objects) {
    Object &object = *prepared.object;
    SculptSession &ss = *prepared.session;
    BLI_assert(object.runtime->sculpt_session == &ss);
    BKE_sculptsession_free_pbvh(object);
    const bool prepared_grid_owner = grids && main_undo_grids_restore_has_object(
                                                  *grids, object.id.session_uid);
    if (!prepared_grid_owner) {
      ss.deform_cos = {};
      ss.deform_imats = {};
      ss.vert_normals_deform = std::move(prepared.vert_normals);
      ss.face_normals_deform = std::move(prepared.face_normals);
      ss.shapekey_active = nullptr;
      ss.multires_modifier = nullptr;
      ss.subdiv_ccg = nullptr;
      ss.deform_modifiers_active = false;
    }
    ss.rv3d = nullptr;
    ss.v3d = nullptr;
    ss.needs_flush_to_id = prepared.saved->needs_flush_to_id;
    ss.pose_ik_chain_preview.reset();
    ss.boundary_preview.reset();
    ss.cursor_sampled_normal.reset();
    ss.last_paint_canvas_key.reset();
    discard_bmesh_no_flush(ss);
  }
  bmain->is_memfile_undo_flush_needed = state.flush_needed;
  for (PreparedObject &prepared : prepared_objects) {
    SculptSession &ss = *prepared.session;
    const MainUndoObject &saved = *prepared.saved;
    ss.bm = prepared.bm.release();
    ss.bm_log = prepared.log ? BM_log_snapshot_restore_commit(prepared.log.release()) : nullptr;
    if (ss.bm) {
      /* Main deliberately retains its original storage during capture. A later ordinary save
       * or MemFile encode must materialize this restored authoritative surface. */
      ss.needs_flush_to_id = true;
      bmain->is_memfile_undo_flush_needed = true;
    }
    ss.pivot_pos = saved.pivot_pos;
    ss.pivot_rot = saved.pivot_rot;
    ss.pivot_scale = saved.pivot_scale;
    ss.last_normal = saved.last_normal;
    ss.sticky_shading_color = saved.sticky_shading_color;
    ss.persistent = std::move(prepared.persistent);
    /* PBVH is gone. Retain exact fine-grid visibility for the existing lazy evaluation hook,
     * rather than allocating another copy or touching a stale SubdivCCG here. */
    ss.angelo_undo_grid_visibility = std::move(prepared.visibility);
  }
  if (grids) {
    main_undo_grids_commit(grids.release());
  }
  return true;
}

bool main_undo_state_suppress_old_grid_writeback(Main &bmain, const char **r_error)
{
  for (const wmWindowManager &wm : bmain.wm) {
    if (WM_jobs_has_running(&wm)) {
      *r_error = "Wait for background jobs to finish before restoring Sculpt Undo state";
      return false;
    }
  }
  return main_undo_grids_suppress_old_writeback(bmain, r_error);
}

bool main_undo_state_discard_runtime(Main &bmain, const char **r_error)
{
  if (!runtime_owners_are_unique(bmain, r_error)) {
    return false;
  }
  struct Replacement {
    Object *object;
    std::unique_ptr<SculptSession, SessionDeleter> session;
  };
  Vector<Replacement> replacements;
  for (Object &object : bmain.objects) {
    if (!object.runtime->sculpt_session) {
      continue;
    }
    Replacement replacement;
    replacement.object = &object;
    replacement.session.reset(MEM_new<SculptSession>(__func__));
    replacement.session->mode_type = object.mode;
    replacements.append(std::move(replacement));
  }
  for (Replacement &replacement : replacements) {
    Object &object = *replacement.object;
    DEG_id_tag_update_ex(&bmain, &object.id, ID_RECALC_GEOMETRY);
    if (object.data) {
      DEG_id_tag_update_ex(&bmain, object.data, ID_RECALC_GEOMETRY);
    }
  }
  /* Every replacement and its internal caches exist before destroying any old session. Normal
   * BKE_sculptsession_free would flush an old BMesh into the newly restored Main; never call it.
   */
  for (Replacement &replacement : replacements) {
    Object &object = *replacement.object;
    SculptSession *old = object.runtime->sculpt_session;
    BKE_sculptsession_free_pbvh(object);
    discard_bmesh_no_flush(*old);
    MEM_delete(old);
    object.runtime->sculpt_session = replacement.session.release();
  }
  return true;
}

bool main_undo_snapshot_objects(const MainUndoState &state,
                                FunctionRef<bool(const SnapshotObjectView &)> visit)
{
  for (const MainUndoObject &object : state.objects) {
    SnapshotObjectView view;
    view.object_uid = object.object_uid;
    view.mesh_uid = object.mesh_uid;
    view.pivot_pos = object.pivot_pos;
    view.pivot_rot = object.pivot_rot;
    view.pivot_scale = object.pivot_scale;
    view.last_normal = object.last_normal;
    view.sticky_shading_color = object.sticky_shading_color;
    view.needs_flush_to_id = object.needs_flush_to_id;
    view.persistent_grids_num = object.persistent.grids_num;
    view.persistent_grid_size = object.persistent.grid_size;
    view.persistent_co = object.persistent.sculpt_persistent_co.as_span();
    view.persistent_no = object.persistent.sculpt_persistent_no.as_span();
    view.persistent_disp = object.persistent.sculpt_persistent_disp.as_span();
    view.geometry = object.geometry.get();
    if (object.visibility) {
      const SculptUndoGridVisibility &visibility = *object.visibility;
      view.visibility.present = true;
      view.visibility.modifier_uid = visibility.modifier_uid;
      view.visibility.verts_num = visibility.verts_num;
      view.visibility.grids_num = visibility.grids_num;
      view.visibility.grid_size = visibility.grid_size;
      view.visibility.face_offsets = visibility.face_offsets.as_span();
      view.visibility.corner_verts = visibility.corner_verts.as_span();
      view.visibility.face_hidden = BoundedBitSpan(visibility.face_hidden);
      const auto &hidden = visibility.grid_hidden;
      view.visibility.grid_hidden = {hidden.all_bits(), hidden.size(), hidden.group_size(),
                                     hidden.is_empty() ? 0 : hidden.all_bits().size() / hidden.size()};
    }
    if (!visit(view)) {
      return false;
    }
  }
  return true;
}

bool main_undo_snapshot_grids(const MainUndoState &state,
                              FunctionRef<bool(const SnapshotGridView &)> visit)
{
  return !state.grids || main_undo_grid_snapshot_visit(*state.grids, visit);
}

bool main_undo_snapshot_flush_needed(const MainUndoState &state)
{
  return state.flush_needed;
}

}  // namespace blender::ed::sculpt_paint::undo
