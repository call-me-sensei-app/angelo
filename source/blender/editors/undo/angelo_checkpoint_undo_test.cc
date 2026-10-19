/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 * SPDX-License-Identifier: GPL-2.0-or-later */
#include "testing/testing.h"
#include <iterator>
#include <memory>
#include "MEM_guardedalloc.h"
#include "BKE_context.hh"
#include "BKE_attribute_storage.hh"
#include "BLI_generic_pointer.hh"
#include "BLI_listbase.h"
#include "BLI_vector.hh"
#include "BKE_global.hh"
#include "BKE_gtest_base.hh"
#include "BKE_lib_id.hh"
#include "BKE_main.hh"
#include "BKE_mesh.hh"
#include "BKE_mesh_types.hh"
#include "BKE_object.hh"
#include "BKE_object_types.hh"
#include "BKE_paint.hh"
#include "BKE_undo_system.hh"
#include "BKE_wm_runtime.hh"
#include "DNA_mesh_types.h"
#include "DNA_object_types.h"
#include "DNA_windowmanager_types.h"
#include "ED_sculpt.hh"
#include "ED_undo.hh"
#include "DEG_depsgraph.hh"
#include "bmesh.hh"
#include "intern/bmesh_log.hh"
#include "intern/bmesh_snapshot.hh"
#include "../sculpt_paint/mesh/sculpt_main_undo_snapshot_access.hh"
#include "undo_intern.hh"

namespace blender {
void BM_log_snapshot_restore_test_fail_stage(int stage);
bool WM_checkpoint_test_coarse_replay(Main &main, Span<uint32_t> selected,
                                       FunctionRef<void()> native_unwind, size_t limit,
                                       const char **error);
}

namespace blender::ed::undo::tests {
class CheckpointInitialUndoTest : public bke::BlenderGTestBase {
 protected:
  Main *previous_main = nullptr, *source = nullptr, *target = nullptr;
  const UndoType *previous_type = nullptr;
  UndoType memfile_type{};
  wmWindowManager *source_wm = nullptr, *target_wm = nullptr;
  bContext *context = nullptr;
  UndoStack *stack = nullptr;
  CheckpointCaptureOwner *owner = nullptr;
  Object *objects[2]{};
  Vector<BMLogEntry *> fixture_entries;
  BMLogEntry *test_entry(BMLog *log)
  {
    BMLogEntry *entry = BM_log_entry_add(log);
    fixture_entries.append(entry);
    return entry;
  }

  void SetUp() override
  {
    bke::BlenderGTestBase::SetUp();
    previous_main = G_MAIN; previous_type = BKE_UNDOSYS_TYPE_MEMFILE;
    ED_memfile_undosys_type(&memfile_type);
    BKE_UNDOSYS_TYPE_MEMFILE = &memfile_type;
    source = BKE_main_new(); target = BKE_main_new();
    source_wm = BKE_id_new<wmWindowManager>(source, "Source");
    target_wm = BKE_id_new<wmWindowManager>(target, "Target");
    source_wm->runtime = MEM_new<bke::WindowManagerRuntime>(__func__);
    target_wm->runtime = MEM_new<bke::WindowManagerRuntime>(__func__);
    stack = BKE_undosys_stack_create();
    source_wm->runtime->undo_stack = target_wm->runtime->undo_stack = stack;
    context = CTX_create();
    G.main = source; CTX_data_main_set(context, source); CTX_wm_manager_set(context, source_wm);
    Mesh *mesh = BKE_id_new<Mesh>(target, "Shared");
    for (int i = 0; i < 2; i++) {
      objects[i] = BKE_id_new<Object>(target, i ? "Hidden-unused-owner" : "Active-owner");
      objects[i]->type = OB_MESH; objects[i]->data = &mesh->id; objects[i]->mode = OB_MODE_SCULPT;
      BKE_object_sculpt_data_create(objects[i]);
      SculptSession &ss = *objects[i]->runtime->sculpt_session;
      BMeshCreateParams params{};
      ss.bm = BM_mesh_create(&bm_mesh_allocsize_default, &params);
      const float coordinates[3][3] = {{float(i * 10), 0, 0}, {float(i * 10 + 1), 0, 0},
                                      {float(i * 10), 1, 0}};
      BMVert *vertices[3];
      for (int j = 0; j < 3; j++) {
        vertices[j] = BM_vert_create(ss.bm, coordinates[j], nullptr, BM_CREATE_NOP);
      }
      BM_face_create_verts(ss.bm, vertices, 3, nullptr, BM_CREATE_NOP, true);
      ss.bm_log = BM_log_create(ss.bm);
      ss.needs_flush_to_id = true;
      ss.pivot_pos = float3(float(i), 2, 3);
    }
    target->is_memfile_undo_flush_needed = true;
    owner = BKE_undosys_checkpoint_restore_begin(source, target);
    ASSERT_NE(owner, nullptr);
    ASSERT_TRUE(BKE_undosys_checkpoint_restore_retire_history(owner));
    G.main = target; CTX_data_main_set(context, target); CTX_wm_manager_set(context, target_wm);
    ASSERT_TRUE(BKE_undosys_checkpoint_restore_rebase(owner, target));
  }
  void TearDown() override
  {
    BKE_undosys_checkpoint_capture_end(owner);
    for (int64_t i = fixture_entries.size(); i > 0; i--) { BM_log_entry_drop(fixture_entries[i - 1]); }
    source_wm->runtime->undo_stack = target_wm->runtime->undo_stack = nullptr;
    BKE_undosys_stack_destroy(stack);
    CTX_free(context);
    BKE_main_free(source); BKE_main_free(target);
    G.main = previous_main; BKE_UNDOSYS_TYPE_MEMFILE = previous_type;
    bke::BlenderGTestBase::TearDown();
  }
};

TEST_F(CheckpointInitialUndoTest, CompleteDistinctSharedOwnersAndFlushFlags)
{
  const char *error = nullptr;
  ASSERT_TRUE(ED_angelo_undo_checkpoint_initial(context, owner, &error)) << (error ? error : "");
  ASSERT_EQ(stack->steps.count(), 1);
  auto *step = reinterpret_cast<MemFileUndoStep *>(stack->step_active);
  ASSERT_NE(step->angelo_sculpt_state, nullptr);
  EXPECT_EQ(stack->step_active_memfile, &step->step);
  EXPECT_TRUE(target->is_memfile_undo_flush_needed);
  EXPECT_TRUE(ed::sculpt_paint::undo::main_undo_snapshot_flush_needed(*step->angelo_sculpt_state));
  int captured = 0;
  EXPECT_TRUE(ed::sculpt_paint::undo::main_undo_snapshot_objects(*step->angelo_sculpt_state,
      [&](const ed::sculpt_paint::undo::SnapshotObjectView &view) {
        for (Object *object : objects) {
          if (view.object_uid != object->id.session_uid) { continue; }
          const SculptSession &ss = *object->runtime->sculpt_session;
          EXPECT_TRUE(view.needs_flush_to_id); EXPECT_TRUE(ss.needs_flush_to_id);
          EXPECT_NE(view.geometry, nullptr);
          EXPECT_TRUE(BM_geometry_snapshot_equals(view.geometry, ss.bm));
          EXPECT_EQ(view.pivot_pos, ss.pivot_pos);
          captured++;
          return true;
        }
        return false;
      }));
  EXPECT_EQ(captured, 2);
  EXPECT_NE(objects[0]->runtime->sculpt_session->bm, objects[1]->runtime->sculpt_session->bm);
  EXPECT_EQ(objects[0]->data, objects[1]->data);
  EXPECT_TRUE(BKE_undosys_checkpoint_capture_validate(owner));
  EXPECT_TRUE(BKE_undosys_checkpoint_restore_complete(owner));
}

TEST_F(CheckpointInitialUndoTest, PrivateMemFileReadPreservesReusedDyntopoOwners)
{
  using namespace ed::sculpt_paint::undo;
  SculptSession *sessions[2] = {objects[0]->runtime->sculpt_session,
                                objects[1]->runtime->sculpt_session};
  BMesh *meshes[2] = {sessions[0]->bm, sessions[1]->bm};
  BMLog *logs[2] = {sessions[0]->bm_log, sessions[1]->bm_log};
  const uint64_t cursors[2] = {BM_log_entry_identity(test_entry(logs[0])),
                               BM_log_entry_identity(test_entry(logs[1]))};
  const char *error = nullptr;
  std::unique_ptr<MainUndoState, decltype(&main_undo_state_free)> before(
      main_undo_state_capture(*target, &error), main_undo_state_free);
  ASSERT_NE(before, nullptr);
  MainUndoBMeshRead *read = main_undo_bmesh_read_begin(*target, &error);
  ASSERT_NE(read, nullptr) << (error ? error : "");
  EXPECT_EQ(sessions[0]->bm, nullptr);
  EXPECT_EQ(sessions[1]->bm, nullptr);
  EXPECT_TRUE(main_undo_bmesh_read_end(*target, read, &error)) << (error ? error : "");
  for (int i = 0; i < 2; i++) {
    EXPECT_EQ(objects[i]->runtime->sculpt_session, sessions[i]);
    EXPECT_EQ(sessions[i]->bm, meshes[i]);
    EXPECT_EQ(sessions[i]->bm_log, logs[i]);
    EXPECT_EQ(BM_log_cursor_id(logs[i]), cursors[i]);
  }
  std::unique_ptr<MainUndoState, decltype(&main_undo_state_free)> after(
      main_undo_state_capture(*target, &error), main_undo_state_free);
  ASSERT_NE(after, nullptr);
  EXPECT_TRUE(main_undo_state_equals(*before, *after));
  EXPECT_EQ(id_cast<Mesh *>(objects[0]->data)->verts_num, 0);
  EXPECT_EQ(objects[0]->data, objects[1]->data);
}

TEST_F(CheckpointInitialUndoTest, PrivateMemFileRetiredDyntopoDoesNotFlushSharedCoarseMesh)
{
  using namespace ed::sculpt_paint::undo;
  SculptSession *survivor = objects[1]->runtime->sculpt_session;
  BMesh *surviving_mesh = survivor->bm;
  BMLog *surviving_log = survivor->bm_log;
  const uint64_t surviving_cursor = BM_log_entry_identity(test_entry(surviving_log));
  Mesh *coarse = id_cast<Mesh *>(objects[0]->data);
  ASSERT_EQ(coarse->verts_num, 0);
  ASSERT_EQ(objects[1]->data, &coarse->id);
  ASSERT_EQ(objects[0]->runtime->sculpt_session->bm->totvert, 3);
  /* Like the reader, allocate the replacement before retiring the old session. */
  SculptSession *replacement = MEM_new<SculptSession>(__func__);
  replacement->mode_type = OB_MODE_SCULPT;
  const char *error = nullptr;
  MainUndoBMeshRead *read = main_undo_bmesh_read_begin(*target, &error);
  if (!read) {
    MEM_delete(replacement);
    FAIL() << (error ? error : "");
  }
  /* The real Object-free operation formerly materialized the abandoned three-vertex BMesh
   * into this shared, restored zero-vertex Mesh. Use it directly to discriminate that defect. */
  BKE_sculptsession_free(objects[0]);
  objects[0]->runtime->sculpt_session = replacement;
  EXPECT_EQ(coarse->verts_num, 0);
  EXPECT_EQ(coarse->faces_num, 0);
  EXPECT_TRUE(main_undo_bmesh_read_end(*target, read, &error)) << (error ? error : "");
  EXPECT_EQ(replacement->bm, nullptr);
  EXPECT_EQ(replacement->bm_log, nullptr);
  EXPECT_EQ(objects[1]->runtime->sculpt_session, survivor);
  EXPECT_EQ(survivor->bm, surviving_mesh);
  EXPECT_EQ(survivor->bm_log, surviving_log);
  EXPECT_EQ(BM_log_cursor_id(surviving_log), surviving_cursor);
  EXPECT_EQ(coarse->verts_num, 0);
  EXPECT_EQ(objects[0]->data, objects[1]->data);
}

TEST_F(CheckpointInitialUndoTest, PrivateMemFileReadRefusesAliasedGeometryBeforeDetach)
{
  using namespace ed::sculpt_paint::undo;
  SculptSession &first = *objects[0]->runtime->sculpt_session;
  SculptSession &second = *objects[1]->runtime->sculpt_session;
  BMesh *first_mesh = first.bm;
  BMesh *second_mesh = second.bm;
  second.bm = first_mesh;
  const char *error = nullptr;
  MainUndoBMeshRead *read = main_undo_bmesh_read_begin(*target, &error);
  second.bm = second_mesh;
  EXPECT_EQ(read, nullptr);
  EXPECT_NE(error, nullptr);
  EXPECT_EQ(first.bm, first_mesh);
  EXPECT_EQ(second.bm, second_mesh);
  if (read) {
    main_undo_bmesh_read_end(*target, read, &error);
  }
}

TEST_F(CheckpointInitialUndoTest, PrivateMemFileReadRejectsChangedUidAtSameObjectAddress)
{
  using namespace ed::sculpt_paint::undo;
  SculptSession *first = objects[0]->runtime->sculpt_session;
  SculptSession *survivor = objects[1]->runtime->sculpt_session;
  BMesh *surviving_mesh = survivor->bm;
  BMLog *surviving_log = survivor->bm_log;
  const uint32_t original_uid = objects[0]->id.session_uid;
  const char *error = nullptr;
  MainUndoBMeshRead *read = main_undo_bmesh_read_begin(*target, &error);
  ASSERT_NE(read, nullptr) << (error ? error : "");
  /* Same addresses alone cannot admit a different Object incarnation. */
  objects[0]->id.session_uid = original_uid ^ uint32_t(1);
  const bool restored = main_undo_bmesh_read_end(*target, read, &error);
  objects[0]->id.session_uid = original_uid;
  EXPECT_FALSE(restored);
  EXPECT_NE(error, nullptr);
  EXPECT_EQ(objects[0]->runtime->sculpt_session, first);
  EXPECT_EQ(first->bm, nullptr);
  EXPECT_EQ(survivor->bm, surviving_mesh);
  EXPECT_EQ(survivor->bm_log, surviving_log);
  EXPECT_EQ(id_cast<Mesh *>(objects[0]->data)->verts_num, 0);
  error = nullptr;
  EXPECT_TRUE(main_undo_state_discard_runtime(*target, &error)) << (error ? error : "");
  EXPECT_EQ(id_cast<Mesh *>(objects[0]->data)->verts_num, 0);
}

TEST_F(CheckpointInitialUndoTest, PrivateMemFileReadFailurePreservesDifferentLiveBMesh)
{
  using namespace ed::sculpt_paint::undo;
  SculptSession *first = objects[0]->runtime->sculpt_session;
  SculptSession *survivor = objects[1]->runtime->sculpt_session;
  BMesh *surviving_mesh = survivor->bm;
  const char *error = nullptr;
  MainUndoBMeshRead *read = main_undo_bmesh_read_begin(*target, &error);
  ASSERT_NE(read, nullptr) << (error ? error : "");
  BMeshCreateParams params{};
  BMesh *replacement = BM_mesh_create(&bm_mesh_allocsize_default, &params);
  const float coordinate[3] = {91.0f, 92.0f, 93.0f};
  BM_vert_create(replacement, coordinate, nullptr, BM_CREATE_NOP);
  first->bm = replacement;
  EXPECT_FALSE(main_undo_bmesh_read_end(*target, read, &error));
  EXPECT_NE(error, nullptr);
  EXPECT_EQ(first->bm, replacement);
  EXPECT_EQ(replacement->totvert, 1);
  EXPECT_EQ(survivor->bm, surviving_mesh);
  EXPECT_EQ(id_cast<Mesh *>(objects[0]->data)->verts_num, 0);
  /* This must release the separate live mesh/log without flushing it or freeing the retired
   * held mesh a second time. Root's native allocator run is the acceptance for cleanup. */
  error = nullptr;
  EXPECT_TRUE(main_undo_state_discard_runtime(*target, &error)) << (error ? error : "");
  EXPECT_EQ(objects[0]->runtime->sculpt_session->bm, nullptr);
  EXPECT_EQ(objects[1]->runtime->sculpt_session->bm, nullptr);
  EXPECT_EQ(id_cast<Mesh *>(objects[0]->data)->verts_num, 0);
}

TEST_F(CheckpointInitialUndoTest, CheckpointCurrentGeometryIgnoresHistoricalLogCursor)
{
  const char *error = nullptr;
  using namespace ed::sculpt_paint::undo;
  std::unique_ptr<MainUndoState, decltype(&main_undo_state_free)> before(
      main_undo_state_capture_checkpoint(*target, SIZE_MAX, &error), main_undo_state_free);
  ASSERT_NE(before, nullptr); ASSERT_EQ(error, nullptr);
  std::unique_ptr<MainUndoState, decltype(&main_undo_state_free)> ordinary_before(
      main_undo_state_capture(*target, &error), main_undo_state_free);
  ASSERT_NE(ordinary_before, nullptr); ASSERT_EQ(error, nullptr);
  test_entry(objects[0]->runtime->sculpt_session->bm_log);
  std::unique_ptr<MainUndoState, decltype(&main_undo_state_free)> after(
      main_undo_state_capture_checkpoint(*target, SIZE_MAX, &error), main_undo_state_free);
  ASSERT_NE(after, nullptr); ASSERT_EQ(error, nullptr);
  std::unique_ptr<MainUndoState, decltype(&main_undo_state_free)> ordinary_after(
      main_undo_state_capture(*target, &error), main_undo_state_free);
  ASSERT_NE(ordinary_after, nullptr); ASSERT_EQ(error, nullptr);
  EXPECT_TRUE(main_undo_state_equals(*before, *after));
  EXPECT_FALSE(main_undo_state_equals(*ordinary_before, *ordinary_after));
  EXPECT_LT(main_undo_state_memory_size(*after), main_undo_state_memory_size(*ordinary_after));
  int geometry_owners = 0;
  EXPECT_TRUE(main_undo_snapshot_objects(*after, [&](const SnapshotObjectView &view) {
    for (Object *object : objects) {
      if (view.object_uid == object->id.session_uid) {
        EXPECT_TRUE(BM_geometry_snapshot_equals(view.geometry, object->runtime->sculpt_session->bm));
        geometry_owners++;
        return true;
      }
    }
    return false;
  }));
  EXPECT_EQ(geometry_owners, 2);
  EXPECT_TRUE(target->is_memfile_undo_flush_needed);
}

TEST_F(CheckpointInitialUndoTest, FiniteCheckpointAdmissionRefusesBeforePersistentCopy)
{
  using namespace ed::sculpt_paint::undo;
  SculptSession &ss = *objects[0]->runtime->sculpt_session;
  ss.persistent.grids_num = 10000; ss.persistent.grid_size = 2;
  ss.persistent.sculpt_persistent_co = Array<float3>(40000, float3(1, 2, 3));
  ss.persistent.sculpt_persistent_no = Array<float3>(40000, float3(0, 0, 1));
  ss.persistent.sculpt_persistent_disp = Array<float>(40000, 0.5f);
  BMesh *geometry = ss.bm;
  const char *error = nullptr;
  EXPECT_EQ(main_undo_state_capture_checkpoint(*target, 16384, &error), nullptr);
  EXPECT_NE(error, nullptr);
  EXPECT_EQ(ss.bm, geometry); EXPECT_EQ(ss.persistent.sculpt_persistent_co.size(), 40000);
  EXPECT_EQ(ss.persistent.sculpt_persistent_disp[39999], 0.5f);
  EXPECT_TRUE(target->is_memfile_undo_flush_needed); EXPECT_TRUE(ss.needs_flush_to_id);
  EXPECT_TRUE(stack->steps.is_empty());
}

TEST_F(CheckpointInitialUndoTest, SelectedTransientWitnessNeedsLeaseAndDetectsFineEdit)
{
  using namespace ed::sculpt_paint::undo;
  const uint32_t uids[2] = {objects[0]->id.session_uid, objects[1]->id.session_uid};
  size_t used = 0; const char *error = nullptr;
  EXPECT_EQ(main_undo_state_observe_checkpoint(*target, Span<uint32_t>(uids, std::size(uids)), SIZE_MAX, used, &error), nullptr);
  ASSERT_NE(error, nullptr);
  DEGCheckpointRuntimeScope *scope = DEG_checkpoint_runtime_begin(target);
  ASSERT_NE(scope, nullptr);
  struct End { DEGCheckpointRuntimeScope *scope; ~End() { DEG_checkpoint_runtime_end(scope); } } end{scope};
  error = nullptr; used = 0;
  std::unique_ptr<MainUndoState, decltype(&main_undo_state_free)> before(
      main_undo_state_observe_checkpoint(*target, Span<uint32_t>(uids, std::size(uids)), SIZE_MAX, used, &error),
      main_undo_state_free);
  ASSERT_NE(before, nullptr); ASSERT_EQ(error, nullptr);
  BMIter iter;
  BMVert *vertex = static_cast<BMVert *>(BM_iter_new(&iter, objects[0]->runtime->sculpt_session->bm,
                                                  BM_VERTS_OF_MESH, nullptr));
  ASSERT_NE(vertex, nullptr); vertex->co[0] += 0.25f;
  error = nullptr; used = 0;
  std::unique_ptr<MainUndoState, decltype(&main_undo_state_free)> after(
      main_undo_state_observe_checkpoint(*target, Span<uint32_t>(uids, std::size(uids)), SIZE_MAX, used, &error),
      main_undo_state_free);
  ASSERT_NE(after, nullptr); ASSERT_EQ(error, nullptr);
  EXPECT_FALSE(main_undo_state_equals(*before, *after));
  EXPECT_EQ(vertex->co[0], 0.25f);
  EXPECT_EQ(objects[0]->data, objects[1]->data);
  EXPECT_TRUE(target->is_memfile_undo_flush_needed);
  EXPECT_TRUE(BKE_undosys_checkpoint_capture_validate(owner));
}

TEST_F(CheckpointInitialUndoTest, BoundedGeometryAdmissionAndBitExactRoundtrip)
{
  BMesh *bm = objects[0]->runtime->sculpt_session->bm;
  size_t bound = 0, used = 7;
  const char *error = nullptr;
  ASSERT_TRUE(BM_geometry_snapshot_preallocation_bound(bm, bound, &error));
  ASSERT_GT(bound, 0u);
  BMLog *log = objects[0]->runtime->sculpt_session->bm_log;
  BMLogEntry *entry = test_entry(log);
  ASSERT_NE(entry, nullptr);
  const uint64_t cursor = BM_log_entry_identity(entry);
  EXPECT_EQ(BM_geometry_snapshot_create_bounded(bm, bound + used - 1, used, &error), nullptr);
  EXPECT_EQ(used, 7u); ASSERT_NE(error, nullptr);
  std::unique_ptr<BMGeometrySnapshot, decltype(&BM_geometry_snapshot_free)> snapshot(
      BM_geometry_snapshot_create_bounded(bm, bound + used, used, &error),
      BM_geometry_snapshot_free);
  ASSERT_NE(snapshot, nullptr); ASSERT_EQ(error, nullptr);
  EXPECT_EQ(used, bound + 7);
  EXPECT_TRUE(BM_geometry_snapshot_equals(snapshot.get(), bm));
  EXPECT_EQ(BM_log_cursor_id(log), cursor);
  EXPECT_TRUE(stack->steps.is_empty());
}

TEST_F(CheckpointInitialUndoTest, FiniteInitialSnapshotRetainsLogProvenanceWithoutPublication)
{
  using namespace ed::sculpt_paint::undo;
  const char *error = nullptr; size_t used = 0;
  EXPECT_EQ(main_undo_state_capture_initial_checkpoint(*target, 1, used, &error), nullptr);
  ASSERT_NE(error, nullptr); EXPECT_TRUE(stack->steps.is_empty());
  error = nullptr; used = 0;
  std::unique_ptr<MainUndoState, decltype(&main_undo_state_free)> bounded(
      main_undo_state_capture_initial_checkpoint(*target, SIZE_MAX, used, &error),
      main_undo_state_free);
  ASSERT_NE(bounded, nullptr); ASSERT_EQ(error, nullptr); EXPECT_GT(used, 0u);
  std::unique_ptr<MainUndoState, decltype(&main_undo_state_free)> ordinary(
      main_undo_state_capture(*target, &error), main_undo_state_free);
  ASSERT_NE(ordinary, nullptr); ASSERT_EQ(error, nullptr);
  EXPECT_TRUE(main_undo_state_equals(*bounded, *ordinary));
  EXPECT_TRUE(target->is_memfile_undo_flush_needed);
  EXPECT_TRUE(BKE_undosys_checkpoint_capture_validate(owner));
}

TEST_F(CheckpointInitialUndoTest, PreparedCallbackReplayRestoresGeometryHistoryAndFlushBits)
{
  using namespace ed::sculpt_paint::undo;
  SculptSession &ss = *objects[0]->runtime->sculpt_session;
  BMIter iter;
  BMVert *vertex = static_cast<BMVert *>(BM_iter_new(&iter, ss.bm, BM_VERTS_OF_MESH, nullptr));
  BMLogEntry *entry = test_entry(ss.bm_log);
  BM_log_vert_before_modified(ss.bm_log, vertex, -1);
  vertex->co[0] = 0.25f; ss.pivot_pos = float3(4, 5, 6);
  const uint64_t cursor = BM_log_entry_identity(entry);
  const uint32_t selected[2] = {objects[0]->id.session_uid, objects[1]->id.session_uid};
  DEGCheckpointRuntimeScope *scope = DEG_checkpoint_runtime_begin(target);
  ASSERT_NE(scope, nullptr);
  struct End { DEGCheckpointRuntimeScope *scope; ~End() { DEG_checkpoint_runtime_end(scope); } } end{scope};
  const char *error = nullptr; size_t used = 0;
  std::unique_ptr<MainUndoState, decltype(&main_undo_state_free)> edited(
      main_undo_state_observe_checkpoint(*target, Span<uint32_t>(selected, std::size(selected)), SIZE_MAX, used, &error), main_undo_state_free);
  ASSERT_NE(edited, nullptr); ASSERT_EQ(error, nullptr);
  vertex->co[0] = 7.0f;
  /* Simulate already-running native work changing both live geometry and the retained swap
   * buffer after the individual callback returned. Replay must retain the entry address. */
  BM_log_undo(ss.bm, ss.bm_log);
  ss.pivot_pos = float3(0); ss.needs_flush_to_id = false;
  target->is_memfile_undo_flush_needed = false;
  ASSERT_TRUE(main_undo_state_replay_checkpoint(*target, *edited, &error)); ASSERT_EQ(error, nullptr);
  EXPECT_EQ(BM_log_current_entry(ss.bm_log), entry);
  EXPECT_EQ(BM_log_cursor_id(ss.bm_log), cursor);
  EXPECT_EQ(ss.pivot_pos, float3(4, 5, 6)); EXPECT_TRUE(ss.needs_flush_to_id);
  EXPECT_TRUE(target->is_memfile_undo_flush_needed);
  EXPECT_TRUE(main_undo_snapshot_objects(*edited, [&](const SnapshotObjectView &view) {
    for (Object *object : objects) {
      if (object->id.session_uid == view.object_uid) {
        EXPECT_TRUE(BM_geometry_snapshot_equals(view.geometry, object->runtime->sculpt_session->bm));
      }
    }
    return true;
  }));
  BM_log_undo(ss.bm, ss.bm_log);
  vertex = static_cast<BMVert *>(BM_iter_new(&iter, ss.bm, BM_VERTS_OF_MESH, nullptr));
  EXPECT_EQ(vertex->co[0], 0.0f);
  BM_log_redo(ss.bm, ss.bm_log);
  EXPECT_EQ(vertex->co[0], 0.25f);
  EXPECT_TRUE(stack->steps.is_empty()); EXPECT_TRUE(BKE_undosys_checkpoint_capture_validate(owner));
}

TEST_F(CheckpointInitialUndoTest, ReplayRetiresActiveBorrowsAndSharedDeformCaches)
{
  using namespace ed::sculpt_paint::undo;
  SculptSession &ss = *objects[0]->runtime->sculpt_session;
  BMIter iter;
  BMVert *vertex = static_cast<BMVert *>(BM_iter_new(&iter, ss.bm, BM_VERTS_OF_MESH, nullptr));
  ss.set_active_vert(vertex); ss.clear_active_elements(true); ss.set_active_vert(vertex);
  ASSERT_TRUE(std::holds_alternative<BMVert *>(ss.active_vert()));
  ASSERT_TRUE(std::holds_alternative<BMVert *>(ss.last_active_vert()));
  ss.vert_normals_deform.ensure([](Vector<float3> &values) { values.append(float3(1, 2, 3)); });
  ss.face_normals_deform.ensure([](Vector<float3> &values) { values.append(float3(4, 5, 6)); });
  const auto borrowed_verts = ss.vert_normals_deform;
  const auto borrowed_faces = ss.face_normals_deform;
  const uint32_t selected[] = {objects[0]->id.session_uid, objects[1]->id.session_uid};
  DEGCheckpointRuntimeScope *scope = DEG_checkpoint_runtime_begin(target);
  ASSERT_NE(scope, nullptr);
  struct End { DEGCheckpointRuntimeScope *scope; ~End() { DEG_checkpoint_runtime_end(scope); } } end{scope};
  const char *error = nullptr; size_t used = 0;
  std::unique_ptr<MainUndoState, decltype(&main_undo_state_free)> edited(
      main_undo_state_observe_checkpoint(*target, Span<uint32_t>(selected, std::size(selected)), SIZE_MAX, used, &error), main_undo_state_free);
  ASSERT_NE(edited, nullptr); ASSERT_EQ(error, nullptr);
  ASSERT_TRUE(main_undo_state_replay_checkpoint(*target, *edited, &error));
  EXPECT_TRUE(std::holds_alternative<std::monostate>(ss.active_vert()));
  EXPECT_TRUE(std::holds_alternative<std::monostate>(ss.last_active_vert()));
  EXPECT_EQ(ss.active_vert_index(), -1); EXPECT_EQ(ss.last_active_vert_index(), -1);
  EXPECT_TRUE(ss.vert_normals_deform.is_dirty()); EXPECT_TRUE(ss.face_normals_deform.is_dirty());
  ASSERT_TRUE(borrowed_verts.is_cached()); ASSERT_TRUE(borrowed_faces.is_cached());
  EXPECT_EQ(borrowed_verts.data()[0], float3(1, 2, 3));
  EXPECT_EQ(borrowed_faces.data()[0], float3(4, 5, 6));
}

TEST_F(CheckpointInitialUndoTest, PreparedLogStagingRefusalReleasesDetachedOwners)
{
  SculptSession &ss = *objects[0]->runtime->sculpt_session;
  BMLogEntry *entry = test_entry(ss.bm_log);
  BMIter iter;
  BMVert *vertex = static_cast<BMVert *>(BM_iter_new(&iter, ss.bm, BM_VERTS_OF_MESH, nullptr));
  BM_log_vert_before_modified(ss.bm_log, vertex, -1);
  const char *error = nullptr;
  std::unique_ptr<BMLogSnapshot, decltype(&BM_log_snapshot_free)> log(
      BM_log_snapshot_create(ss.bm, ss.bm_log, &error), BM_log_snapshot_free);
  std::unique_ptr<BMGeometrySnapshot, decltype(&BM_geometry_snapshot_free)> geometry(
      BM_geometry_snapshot_create(ss.bm, &error), BM_geometry_snapshot_free);
  ASSERT_NE(log, nullptr); ASSERT_NE(geometry, nullptr);
  for (const int stage : {1, 2, 3}) {
    size_t used = 0;
    BMesh *clone = BM_geometry_snapshot_restore_bounded(geometry.get(), SIZE_MAX, used, &error);
    ASSERT_NE(clone, nullptr);
    const size_t baseline = MEM_get_memory_in_use();
    BM_log_snapshot_restore_test_fail_stage(stage);
    EXPECT_EQ(BM_log_snapshot_restore_prepare_bounded(clone, log.get(), ss.bm_log,
                                                       SIZE_MAX, used, &error), nullptr);
    EXPECT_NE(error, nullptr);
    EXPECT_EQ(MEM_get_memory_in_use(), baseline);
    EXPECT_EQ(BM_log_current_entry(ss.bm_log), entry);
    EXPECT_TRUE(BM_geometry_snapshot_equals(geometry.get(), ss.bm));
    BM_mesh_free(clone);
  }
  BM_log_snapshot_restore_test_fail_stage(0);
}

TEST_F(CheckpointInitialUndoTest, CoarseReplayInvalidatesSameCountConnectivityCaches)
{
  Mesh &mesh = *id_cast<Mesh *>(objects[0]->data);
  Mesh *initial = BKE_mesh_new_nomain(4, 5, 2, 6);
  initial->vert_positions_for_write().fill(float3(0));
  const int2 edges_a[] = {{0, 1}, {1, 2}, {2, 0}, {2, 3}, {3, 0}};
  const int corners_a[] = {0, 1, 2, 0, 2, 3};
  const int corner_edges_a[] = {0, 1, 2, 2, 3, 4};
  initial->edges_for_write().copy_from(Span<int2>(edges_a, std::size(edges_a)));
  initial->corner_verts_for_write().copy_from(Span<int>(corners_a, std::size(corners_a)));
  initial->corner_edges_for_write().copy_from(Span<int>(corner_edges_a, std::size(corner_edges_a)));
  initial->face_offsets_for_write()[0] = 0; initial->face_offsets_for_write()[1] = 3;
  initial->face_offsets_for_write()[2] = 6;
  BKE_mesh_nomain_to_mesh(initial, &mesh, objects[0], false);
  ASSERT_EQ(mesh.vert_to_face_map()[0].size(), 2);
  const uint32_t selected[] = {objects[0]->id.session_uid, objects[1]->id.session_uid};
  const char *error = nullptr;
  ASSERT_TRUE(WM_checkpoint_test_coarse_replay(*target, Span<uint32_t>(selected, std::size(selected)), [&]() {
    const int2 edges_b[] = {{0, 1}, {1, 3}, {3, 0}, {1, 2}, {2, 3}};
    const int corners_b[] = {0, 1, 3, 1, 2, 3};
    const int corner_edges_b[] = {0, 1, 2, 3, 4, 1};
    mesh.edges_for_write().copy_from(Span<int2>(edges_b, std::size(edges_b)));
    mesh.corner_verts_for_write().copy_from(Span<int>(corners_b, std::size(corners_b)));
    mesh.corner_edges_for_write().copy_from(Span<int>(corner_edges_b, std::size(corner_edges_b)));
    mesh.tag_topology_changed();
    EXPECT_EQ(mesh.vert_to_face_map()[0].size(), 1);
    EXPECT_EQ(mesh.vert_to_corner_map()[0].size(), 1);
    mesh.corner_to_face_map(); mesh.loose_edges(); mesh.loose_verts(); mesh.verts_no_face();
  }, SIZE_MAX, &error)) << (error ? error : "");
  EXPECT_EQ(error, nullptr);
  EXPECT_TRUE(mesh.runtime->vert_to_face_offset_cache.is_dirty());
  EXPECT_TRUE(mesh.runtime->vert_to_face_map_cache.is_dirty());
  EXPECT_TRUE(mesh.runtime->vert_to_corner_map_cache.is_dirty());
  EXPECT_TRUE(mesh.runtime->corner_to_face_map_cache.is_dirty());
  EXPECT_TRUE(mesh.runtime->loose_edges_cache.is_dirty());
  EXPECT_TRUE(mesh.runtime->loose_verts_cache.is_dirty());
  EXPECT_TRUE(mesh.runtime->verts_no_face_cache.is_dirty());
  EXPECT_EQ(mesh.vert_to_face_map()[0].size(), 2);
  EXPECT_EQ(mesh.vert_to_corner_map()[0].size(), 2);
  EXPECT_EQ(mesh.corner_verts()[3], 0);
  bool native_work = false;
  EXPECT_FALSE(WM_checkpoint_test_coarse_replay(*target, Span<uint32_t>(selected, std::size(selected)),
      [&]() { native_work = true; }, 1, &error));
  EXPECT_FALSE(native_work); EXPECT_EQ(mesh.vert_to_face_map()[0].size(), 2);
}

TEST_F(CheckpointInitialUndoTest, CoarseReplayPreservesConstantArraysAndSharedBorrowers)
{
  using bke::Attribute;
  using bke::AttrDomain;
  using bke::AttrType;
  Mesh &mesh = *id_cast<Mesh *>(objects[0]->data);
  Mesh *initial = BKE_mesh_new_nomain(3, 0, 0, 0);
  initial->vert_positions_for_write().fill(float3(1, 2, 3));
  BKE_mesh_nomain_to_mesh(initial, &mesh, objects[0], false);
  auto &attributes = mesh.attribute_storage.wrap();
  const float mask = 0.25f;
  attributes.add(".sculpt_mask", AttrDomain::Point, AttrType::Float,
                 Attribute::SingleData::from_value(GPointer(&mask)));
  auto hidden = Attribute::ArrayData::from_default_value(CPPType::get<bool>(), 3);
  static_cast<bool *>(hidden.data)[1] = true;
  attributes.add(".hide_vert", AttrDomain::Point, AttrType::Bool, std::move(hidden));
  const int marker = 17;
  attributes.add("artist_marker", AttrDomain::Point, AttrType::Int32,
                 Attribute::SingleData::from_value(GPointer(&marker)));
  const auto source_positions = attributes.lookup("position")->data();
  Attribute::DataVariant overwritten_positions = Attribute::ArrayData{};
  const uint32_t selected[] = {objects[0]->id.session_uid, objects[1]->id.session_uid};
  const char *error = nullptr;
  ASSERT_TRUE(WM_checkpoint_test_coarse_replay(
      *target, Span<uint32_t>(selected, std::size(selected)), [&]() {
        mesh.vert_positions_for_write().fill(float3(9, 8, 7));
        overwritten_positions = attributes.lookup("position")->data();
        const float changed_mask = 0.75f;
        attributes.lookup(".sculpt_mask")->assign_data(
            Attribute::ArrayData::from_value(GPointer(&changed_mask), 3));
        const bool changed_hidden = true;
        attributes.lookup(".hide_vert")->assign_data(
            Attribute::SingleData::from_value(GPointer(&changed_hidden)));
        const int changed_marker = 55;
        attributes.lookup("artist_marker")->assign_data(
            Attribute::SingleData::from_value(GPointer(&changed_marker)));
      }, SIZE_MAX, &error)) << (error ? error : "");
  EXPECT_EQ(error, nullptr);
  EXPECT_EQ(mesh.vert_positions()[0], float3(1, 2, 3));
  EXPECT_EQ(objects[0]->data, objects[1]->data);
  const auto &source_array = std::get<Attribute::ArrayData>(source_positions);
  const auto &overwritten_array = std::get<Attribute::ArrayData>(overwritten_positions);
  EXPECT_EQ(static_cast<const float3 *>(source_array.data)[0], float3(1, 2, 3));
  EXPECT_EQ(static_cast<const float3 *>(overwritten_array.data)[0], float3(9, 8, 7));
  EXPECT_NE(static_cast<const void *>(mesh.vert_positions().data()), source_array.data);
  const auto *restored_mask = std::get_if<Attribute::SingleData>(
      &attributes.lookup(".sculpt_mask")->data());
  ASSERT_NE(restored_mask, nullptr);
  EXPECT_EQ(*static_cast<const float *>(restored_mask->value), mask);
  const auto *restored_hidden = std::get_if<Attribute::ArrayData>(
      &attributes.lookup(".hide_vert")->data());
  ASSERT_NE(restored_hidden, nullptr);
  ASSERT_EQ(restored_hidden->size, 3);
  EXPECT_FALSE(static_cast<const bool *>(restored_hidden->data)[0]);
  EXPECT_TRUE(static_cast<const bool *>(restored_hidden->data)[1]);
  EXPECT_FALSE(static_cast<const bool *>(restored_hidden->data)[2]);
  const auto &retained_marker = std::get<Attribute::SingleData>(
      attributes.lookup("artist_marker")->data());
  EXPECT_EQ(*static_cast<const int *>(retained_marker.value), 55);
}

TEST_F(CheckpointInitialUndoTest, CoarseReplaySchemaRefusalPrecedesEveryOwnerPublication)
{
  using bke::Attribute;
  using bke::AttrDomain;
  using bke::AttrType;
  Mesh *second = BKE_mesh_add(target, "Second coarse owner");
  objects[1]->data = &second->id;
  for (Object *object : objects) {
    Mesh *initial = BKE_mesh_new_nomain(1, 0, 0, 0);
    initial->vert_positions_for_write().fill(float3(1, 2, 3));
    BKE_mesh_nomain_to_mesh(initial, id_cast<Mesh *>(object->data), object, false);
  }
  Mesh &first = *id_cast<Mesh *>(objects[0]->data);
  const uint32_t selected[] = {objects[0]->id.session_uid, objects[1]->id.session_uid};
  const char *error = nullptr;
  EXPECT_FALSE(WM_checkpoint_test_coarse_replay(
      *target, Span<uint32_t>(selected, std::size(selected)), [&]() {
        first.vert_positions_for_write().fill(float3(9, 8, 7));
        auto &attributes = second->attribute_storage.wrap();
        EXPECT_TRUE(attributes.remove("position"));
        const float wrong_type_value = 3.0f;
        attributes.add("position", AttrDomain::Point, AttrType::Float,
                       Attribute::ArrayData::from_value(GPointer(&wrong_type_value), 1));
      }, SIZE_MAX, &error));
  ASSERT_NE(error, nullptr);
  EXPECT_STREQ(error, "Edited coarse attribute schema changed during native unwind");
  EXPECT_EQ(first.vert_positions()[0], float3(9, 8, 7));
  EXPECT_EQ(second->attribute_storage.wrap().lookup("position")->data_type(), AttrType::Float);
}

TEST_F(CheckpointInitialUndoTest, ReplayRefusesChangedChainBeforeAnyOwnerPublication)
{
  using namespace ed::sculpt_paint::undo;
  const uint32_t selected[2] = {objects[0]->id.session_uid, objects[1]->id.session_uid};
  SculptSession &ss = *objects[0]->runtime->sculpt_session;
  test_entry(ss.bm_log);
  DEGCheckpointRuntimeScope *scope = DEG_checkpoint_runtime_begin(target);
  ASSERT_NE(scope, nullptr);
  struct End { DEGCheckpointRuntimeScope *scope; ~End() { DEG_checkpoint_runtime_end(scope); } } end{scope};
  const char *error = nullptr; size_t used = 0;
  std::unique_ptr<MainUndoState, decltype(&main_undo_state_free)> edited(
      main_undo_state_observe_checkpoint(*target, Span<uint32_t>(selected, std::size(selected)), SIZE_MAX, used, &error), main_undo_state_free);
  ASSERT_NE(edited, nullptr); ASSERT_EQ(error, nullptr);
  BMLogEntry *added = test_entry(ss.bm_log);
  BMesh *before = ss.bm; BMLog *log_before = ss.bm_log;
  EXPECT_FALSE(main_undo_state_replay_checkpoint(*target, *edited, &error)); EXPECT_NE(error, nullptr);
  EXPECT_EQ(ss.bm, before); EXPECT_EQ(ss.bm_log, log_before);
  EXPECT_EQ(BM_log_current_entry(ss.bm_log), added);
  EXPECT_TRUE(target->is_memfile_undo_flush_needed); EXPECT_TRUE(stack->steps.is_empty());
}

TEST_F(CheckpointInitialUndoTest, BoundedGeometryRestoreRefusesBeforeCloneAndPreservesBits)
{
  const char *error = nullptr;
  std::unique_ptr<BMGeometrySnapshot, decltype(&BM_geometry_snapshot_free)> snapshot(
      BM_geometry_snapshot_create(objects[0]->runtime->sculpt_session->bm, &error), BM_geometry_snapshot_free);
  ASSERT_NE(snapshot, nullptr); ASSERT_EQ(error, nullptr);
  size_t used = 0;
  EXPECT_EQ(BM_geometry_snapshot_restore_bounded(snapshot.get(), 1, used, &error), nullptr);
  EXPECT_EQ(used, 0u); EXPECT_NE(error, nullptr);
  BMesh *restored = BM_geometry_snapshot_restore_bounded(snapshot.get(), SIZE_MAX, used, &error);
  ASSERT_NE(restored, nullptr); ASSERT_EQ(error, nullptr); EXPECT_GT(used, 0u);
  EXPECT_TRUE(BM_geometry_snapshot_equals(snapshot.get(), restored));
  BM_mesh_free(restored);
}

TEST_F(CheckpointInitialUndoTest, OperationReturnHookUsesOnlyTheExactPrivateLease)
{
  struct Receipt { Main *main; int calls = 0; bool accepted = true; } receipt{target};
  auto hook = [](void *user, Main *main) {
    auto &value = *static_cast<Receipt *>(user);
    EXPECT_EQ(main, value.main); value.calls++;
    return value.accepted;
  };
  EXPECT_TRUE(DEG_checkpoint_runtime_operation_boundary(target)); EXPECT_EQ(receipt.calls, 0);
  DEGCheckpointRuntimeScope *scope = DEG_checkpoint_runtime_begin(target);
  ASSERT_NE(scope, nullptr);
  struct End { DEGCheckpointRuntimeScope *scope; ~End() { DEG_checkpoint_runtime_end(scope); } } end{scope};
  ASSERT_TRUE(DEG_checkpoint_runtime_set_operation_hook(scope, &receipt, hook));
  EXPECT_FALSE(DEG_checkpoint_runtime_set_operation_hook(scope, &receipt, hook));
  EXPECT_TRUE(DEG_checkpoint_runtime_operation_boundary(source)); EXPECT_EQ(receipt.calls, 0);
  EXPECT_TRUE(DEG_checkpoint_runtime_operation_boundary(target)); EXPECT_EQ(receipt.calls, 1);
  receipt.accepted = false;
  EXPECT_FALSE(DEG_checkpoint_runtime_operation_boundary(target)); EXPECT_EQ(receipt.calls, 2);
  EXPECT_TRUE(BKE_undosys_checkpoint_capture_validate(owner)); EXPECT_TRUE(stack->steps.is_empty());
}

TEST_F(CheckpointInitialUndoTest, RetiredOperationHookCannotOccupyTheNextLease)
{
  struct Receipt { int calls = 0; } receipt;
  auto hook = [](void *user, Main *) { static_cast<Receipt *>(user)->calls++; return true; };
  DEGCheckpointRuntimeScope *first = DEG_checkpoint_runtime_begin(target);
  ASSERT_NE(first, nullptr);
  ASSERT_TRUE(DEG_checkpoint_runtime_set_operation_hook(first, &receipt, hook));
  ASSERT_TRUE(DEG_checkpoint_runtime_operation_boundary(target));
  ASSERT_EQ(receipt.calls, 1);
  DEG_checkpoint_runtime_end(first);
  EXPECT_TRUE(DEG_checkpoint_runtime_operation_boundary(target)); EXPECT_EQ(receipt.calls, 1);
  DEGCheckpointRuntimeScope *second = DEG_checkpoint_runtime_begin(target);
  ASSERT_NE(second, nullptr);
  struct End { DEGCheckpointRuntimeScope *scope; ~End() { DEG_checkpoint_runtime_end(scope); } } end{second};
  EXPECT_TRUE(DEG_checkpoint_runtime_operation_boundary(target)); EXPECT_EQ(receipt.calls, 1);
  EXPECT_TRUE(BKE_undosys_checkpoint_capture_validate(owner));
}

TEST_F(CheckpointInitialUndoTest, WrongOwnerLeavesInitialHistoryUntouched)
{
  const char *error = nullptr;
  EXPECT_FALSE(ED_angelo_undo_checkpoint_initial(context, nullptr, &error));
  EXPECT_NE(error, nullptr);
  EXPECT_TRUE(stack->steps.is_empty()); EXPECT_EQ(stack->step_active, nullptr);
  EXPECT_TRUE(target->is_memfile_undo_flush_needed);
  EXPECT_TRUE(BKE_undosys_checkpoint_capture_validate(owner));
}

TEST_F(CheckpointInitialUndoTest, ForeignContextManagerRefusedBeforeRuntimeUse)
{
  Main *foreign_main = BKE_main_new();
  wmWindowManager *foreign = BKE_id_new<wmWindowManager>(foreign_main, "Foreign context");
  EXPECT_EQ(foreign->runtime, nullptr);
  CTX_wm_manager_set(context, foreign);
  const char *error = nullptr;
  EXPECT_FALSE(ED_angelo_undo_checkpoint_initial(context, owner, &error));
  EXPECT_NE(error, nullptr);
  EXPECT_TRUE(stack->steps.is_empty());
  EXPECT_TRUE(BKE_undosys_checkpoint_capture_validate(owner));
  CTX_wm_manager_set(context, target_wm);
  EXPECT_EQ(foreign->runtime, nullptr);
  /* Keep the null-runtime rejection oracle, then satisfy normal WM destruction's contract. */
  foreign->runtime = MEM_new<bke::WindowManagerRuntime>(__func__);
  BKE_main_free(foreign_main);
}

TEST_F(CheckpointInitialUndoTest, DuplicatePublicationRetainsExactInitialBoundary)
{
  const char *error = nullptr;
  ASSERT_TRUE(ED_angelo_undo_checkpoint_initial(context, owner, &error));
  const UndoStep *initial = stack->step_active;
  EXPECT_FALSE(ED_angelo_undo_checkpoint_initial(context, owner, &error));
  EXPECT_NE(error, nullptr);
  EXPECT_EQ(stack->steps.count(), 1); EXPECT_EQ(stack->step_active, initial);
  EXPECT_EQ(stack->step_active_memfile, initial);
  EXPECT_TRUE(target->is_memfile_undo_flush_needed);
}
}  // namespace blender::ed::undo::tests
