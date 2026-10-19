/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include <array>

#include "testing/testing.h"

#include "BLI_math_vector_types.hh"
#include "BLI_vector.hh"

#include "BKE_customdata.hh"
#include "BKE_gtest_base.hh"
#include "BKE_lib_id.hh"
#include "BKE_mesh.hh"

#include "DNA_object_types.h"

#include "bmesh.hh"
#include "intern/bmesh_log.hh"
#include "intern/bmesh_mesh_convert.hh"

#ifndef NDEBUG
#  include "range_tree.h"
#endif

namespace blender {

class BMeshLogSnapshotTest : public bke::BlenderGTestBase {
 protected:
  Vector<BMesh *> meshes_;
  Vector<BMLog *> logs_;
  Vector<BMLogEntry *> entries_;
  Vector<BMLogSnapshot *> snapshots_;
  Vector<BMLogSnapshotRestore *> restores_;

  void TearDown() override
  {
    for (BMLogSnapshot *snapshot : snapshots_) {
      BM_log_snapshot_free(snapshot);
    }
    for (BMLogSnapshotRestore *restore : restores_) {
      BM_log_snapshot_restore_discard(restore);
    }
    for (BMLog *log : logs_) {
      BM_log_free(log);
    }
    for (auto entry = entries_.rbegin(); entry != entries_.rend(); ++entry) {
      BM_log_entry_drop(*entry);
    }
    for (BMesh *bm : meshes_) {
      BM_mesh_free(bm);
    }
    bke::BlenderGTestBase::TearDown();
  }

  BMesh *mesh_create()
  {
    BMeshCreateParams params{};
    BMesh *bm = BM_mesh_create(&bm_mesh_allocsize_default, &params);
    meshes_.append(bm);
    return bm;
  }

  BMesh *mesh_copy(BMesh *bm)
  {
    BMesh *copy = BM_mesh_copy(bm);
    meshes_.append(copy);
    return copy;
  }

  BMesh *mesh_conversion_roundtrip(BMesh *bm)
  {
    Mesh *mesh = BKE_mesh_new_nomain(0, 0, 0, 0);
    /* Match BKE_sculptsession_bm_to_me's data conversion. A temporary Object only supplies the
     * same active-key index as the restoration caller; this fixture has no shape keys. */
    Object object{};
    object.shapenr = 1;
    BMeshToMeshParams to_mesh_params{};
    to_mesh_params.calc_object_remap = false;
    BM_mesh_bm_to_me(nullptr, bm, mesh, &to_mesh_params);

    BMesh *restored = mesh_create();
    BMeshFromMeshParams from_mesh_params{};
    from_mesh_params.calc_face_normal = true;
    from_mesh_params.calc_vert_normal = true;
    from_mesh_params.use_shapekey = true;
    from_mesh_params.active_shapekey = object.shapenr;
    BM_mesh_bm_from_me(restored, mesh, &from_mesh_params);
    BKE_id_free(nullptr, mesh);
    return restored;
  }

  BMLog *log_create(BMesh *bm)
  {
    BMLog *log = BM_log_create(bm);
    logs_.append(log);
    return log;
  }

  void log_free(BMLog *log)
  {
    logs_.remove_first_occurrence_and_reorder(log);
    BM_log_free(log);
  }

  BMLogEntry *entry_add(BMLog *log)
  {
    BMLogEntry *entry = BM_log_entry_add(log);
    entries_.append(entry);
    return entry;
  }

  void entry_drop(BMLogEntry *entry)
  {
    entries_.remove(entries_.first_index_of(entry));
    BM_log_entry_drop(entry);
  }

  BMLogSnapshot *snapshot_create(BMesh *bm, const BMLog *log)
  {
    BMLogSnapshot *snapshot = BM_log_snapshot_create(bm, log);
    if (snapshot) {
      snapshots_.append(snapshot);
    }
    return snapshot;
  }

  BMLogSnapshotRestore *restore_prepare(BMesh *bm,
                                        const BMLogSnapshot *snapshot,
                                        Span<BMLogEntry *> entries,
                                        const BMLog *expected_old_log,
                                        const char **r_error = nullptr)
  {
    BMLogSnapshotRestore *restore = BM_log_snapshot_restore_prepare(
        bm, snapshot, entries, expected_old_log, r_error);
    if (restore) {
      restores_.append(restore);
    }
    return restore;
  }

  void restore_discard(BMLogSnapshotRestore *restore)
  {
    restores_.remove_first_occurrence_and_reorder(restore);
    BM_log_snapshot_restore_discard(restore);
  }

  BMLog *restore_commit(BMLogSnapshotRestore *restore)
  {
    restores_.remove_first_occurrence_and_reorder(restore);
    BMLog *log = BM_log_snapshot_restore_commit(restore);
    logs_.append(log);
    return log;
  }

  static BMVert *vert_add(BMesh *bm, const float x)
  {
    const float co[3] = {x, 0.0f, 0.0f};
    return BM_vert_create(bm, co, nullptr, BM_CREATE_NOP);
  }

  static BMVert *first_vert(BMesh *bm)
  {
    BMIter iter;
    return static_cast<BMVert *>(BM_iter_new(&iter, bm, BM_VERTS_OF_MESH, nullptr));
  }

  static BMFace *triangle_add(BMesh *bm)
  {
    const float coords[3][3] = {{0.0f, 0.0f, 0.0f}, {1.0f, 0.0f, 0.0f}, {0.0f, 1.0f, 0.0f}};
    BMVert *verts[3];
    for (int i = 0; i < 3; i++) {
      verts[i] = BM_vert_create(bm, coords[i], nullptr, BM_CREATE_NOP);
    }
    return BM_face_create_verts(bm, verts, 3, nullptr, BM_CREATE_NOP, true);
  }
};

TEST_F(BMeshLogSnapshotTest, LiveHistoryIdentityPreservesEpochBeforeFirstEntry)
{
  BMesh *bm = mesh_create();
  BMLog *log = log_create(bm);
  const uint64_t epoch = BM_log_epoch(log);
  EXPECT_NE(epoch, 0u);
  EXPECT_EQ(BM_log_cursor_id(log), 0u);
  BMLog *other = log_create(mesh_create());
  EXPECT_NE(BM_log_epoch(other), epoch);
  EXPECT_EQ(BM_log_cursor_id(other), 0u);

  BMLogEntry *entry = entry_add(log);
  EXPECT_EQ(BM_log_epoch(log), BM_log_entry_epoch(entry));
  EXPECT_EQ(BM_log_cursor_id(log), BM_log_entry_identity(entry));
  BM_log_undo(bm, log);
  EXPECT_EQ(BM_log_epoch(log), epoch);
  EXPECT_EQ(BM_log_cursor_id(log), 0u);
  BM_log_redo(bm, log);
  EXPECT_EQ(BM_log_epoch(log), epoch);
  EXPECT_EQ(BM_log_cursor_id(log), BM_log_entry_identity(entry));
}

TEST_F(BMeshLogSnapshotTest, RestoredCursorAndSwapsPreserveUndoRedo)
{
  BMesh *bm = mesh_create();
  BMVert *vert = vert_add(bm, 0.0f);
  BMLog *log = log_create(bm);
  BMLogEntry *first = entry_add(log);
  BM_log_vert_before_modified(log, vert, -1);
  vert->co[0] = 1.0f;
  BMLogEntry *second = entry_add(log);
  BM_log_vert_before_modified(log, vert, -1);
  vert->co[0] = 2.0f;
  BM_log_undo(bm, log);
  ASSERT_EQ(vert->co[0], 1.0f);

  BMLogSnapshot *snapshot = snapshot_create(bm, log);
  ASSERT_NE(snapshot, nullptr);
  EXPECT_EQ(BM_log_snapshot_cursor_id(snapshot), BM_log_entry_identity(first));
  EXPECT_EQ(BM_log_snapshot_epoch(snapshot), BM_log_entry_epoch(first));
  EXPECT_GT(BM_log_snapshot_memory_size(snapshot), sizeof(uint));
  EXPECT_GT(BM_log_entry_identity(second), BM_log_entry_identity(first));

  BMesh *copy = mesh_copy(bm);
  const char *error = nullptr;
  EXPECT_EQ(restore_prepare(copy, snapshot, {first, second}, nullptr, &error), nullptr);
  EXPECT_NE(error, nullptr);
  /* Rejected preparation must not steal the entries from the old owner. */
  BM_log_redo(bm, log);
  EXPECT_EQ(vert->co[0], 2.0f);
  BM_log_undo(bm, log);
  EXPECT_EQ(vert->co[0], 1.0f);
  BMLogSnapshotRestore *prepared = restore_prepare(copy, snapshot, {first, second}, log);
  ASSERT_NE(prepared, nullptr);
  log_free(log);
  BMLog *restored = restore_commit(prepared);

  BMVert *copy_vert = first_vert(copy);
  BM_log_redo(copy, restored);
  EXPECT_EQ(copy_vert->co[0], 2.0f);
  BM_log_undo(copy, restored);
  EXPECT_EQ(copy_vert->co[0], 1.0f);
  BM_log_undo(copy, restored);
  EXPECT_EQ(copy_vert->co[0], 0.0f);
  BM_log_redo(copy, restored);
  EXPECT_EQ(copy_vert->co[0], 1.0f);
  BM_log_redo(copy, restored);
  EXPECT_EQ(copy_vert->co[0], 2.0f);
}

TEST_F(BMeshLogSnapshotTest, UnexpectedOwnerPreparationIsInert)
{
  BMesh *bm = mesh_create();
  BMVert *vert = vert_add(bm, 0.0f);
  BMLog *log = log_create(bm);
  BMLogEntry *entry = entry_add(log);
  BM_log_vert_before_modified(log, vert, -1);
  vert->co[0] = 2.0f;
  BMLogSnapshot *snapshot = snapshot_create(bm, log);
  ASSERT_NE(snapshot, nullptr);
  BMLog *other_log = log_create(mesh_create());

  const char *error = nullptr;
  EXPECT_EQ(restore_prepare(bm, snapshot, {entry}, other_log, &error), nullptr);
  EXPECT_NE(error, nullptr);
  BM_log_undo(bm, log);
  EXPECT_EQ(vert->co[0], 0.0f);
  BM_log_redo(bm, log);
  EXPECT_EQ(vert->co[0], 2.0f);
  EXPECT_NE(snapshot_create(bm, log), nullptr);
}

TEST_F(BMeshLogSnapshotTest, DiscardPreparedRestorePreservesOldOwner)
{
  BMesh *bm = mesh_create();
  BMVert *vert = vert_add(bm, 0.0f);
  BMLog *log = log_create(bm);
  BMLogEntry *entry = entry_add(log);
  BM_log_vert_before_modified(log, vert, -1);
  vert->co[0] = 2.0f;
  BMLogSnapshot *snapshot = snapshot_create(bm, log);
  ASSERT_NE(snapshot, nullptr);
  BMLogSnapshotRestore *prepared = restore_prepare(mesh_copy(bm), snapshot, {entry}, log);
  ASSERT_NE(prepared, nullptr);
  restore_discard(prepared);

  BM_log_undo(bm, log);
  EXPECT_EQ(vert->co[0], 0.0f);
  BM_log_redo(bm, log);
  EXPECT_EQ(vert->co[0], 2.0f);
  EXPECT_NE(snapshot_create(bm, log), nullptr);
}

TEST_F(BMeshLogSnapshotTest, OwnerlessHistoryRestoresAfterOldLogFree)
{
  BMesh *bm = mesh_create();
  BMVert *vert = vert_add(bm, 0.0f);
  BMLog *log = log_create(bm);
  BMLogEntry *entry = entry_add(log);
  BM_log_vert_before_modified(log, vert, -1);
  vert->co[0] = 2.0f;
  BMLogSnapshot *snapshot = snapshot_create(bm, log);
  ASSERT_NE(snapshot, nullptr);
  BMesh *copy = mesh_copy(bm);
  log_free(log);
  BMLogSnapshotRestore *prepared = restore_prepare(copy, snapshot, {entry}, nullptr);
  ASSERT_NE(prepared, nullptr);
  BMLog *restored = restore_commit(prepared);
  BM_log_undo(copy, restored);
  EXPECT_EQ(first_vert(copy)->co[0], 0.0f);
  BM_log_redo(copy, restored);
  EXPECT_EQ(first_vert(copy)->co[0], 2.0f);
}

TEST_F(BMeshLogSnapshotTest, LaterObjectFailureLeavesEveryOldHistoryUsable)
{
  BMesh *first_bm = mesh_create();
  BMVert *first = vert_add(first_bm, 0.0f);
  BMLog *first_log = log_create(first_bm);
  BMLogEntry *first_entry = entry_add(first_log);
  BM_log_vert_before_modified(first_log, first, -1);
  first->co[0] = 1.0f;
  BMLogSnapshot *first_snapshot = snapshot_create(first_bm, first_log);
  ASSERT_NE(first_snapshot, nullptr);

  BMesh *second_bm = mesh_create();
  BMVert *second = vert_add(second_bm, 10.0f);
  BMLog *second_log = log_create(second_bm);
  BMLogEntry *second_entry = entry_add(second_log);
  BM_log_vert_before_modified(second_log, second, -1);
  second->co[0] = 11.0f;
  BMLogSnapshot *second_snapshot = snapshot_create(second_bm, second_log);
  ASSERT_NE(second_snapshot, nullptr);

  BMLogSnapshotRestore *first_prepared = restore_prepare(
      mesh_copy(first_bm), first_snapshot, {first_entry}, first_log);
  ASSERT_NE(first_prepared, nullptr);
  const char *error = nullptr;
  EXPECT_EQ(
      restore_prepare(mesh_copy(second_bm), second_snapshot, {second_entry}, first_log, &error),
      nullptr);
  EXPECT_NE(error, nullptr);
  restore_discard(first_prepared);
  BM_log_undo(first_bm, first_log);
  BM_log_undo(second_bm, second_log);
  EXPECT_EQ(first->co[0], 0.0f);
  EXPECT_EQ(second->co[0], 10.0f);
  BM_log_redo(first_bm, first_log);
  BM_log_redo(second_bm, second_log);
  EXPECT_EQ(first->co[0], 1.0f);
  EXPECT_EQ(second->co[0], 11.0f);
}

TEST_F(BMeshLogSnapshotTest, AllPreparedLogsCommitAfterEveryOldOwnerIsFreed)
{
  BMesh *first_bm = mesh_create();
  BMVert *first = vert_add(first_bm, 0.0f);
  BMLog *first_log = log_create(first_bm);
  BMLogEntry *first_entry = entry_add(first_log);
  BM_log_vert_before_modified(first_log, first, -1);
  first->co[0] = 1.0f;
  BMLogSnapshot *first_snapshot = snapshot_create(first_bm, first_log);
  ASSERT_NE(first_snapshot, nullptr);

  BMesh *second_bm = mesh_create();
  BMVert *second = vert_add(second_bm, 10.0f);
  BMLog *second_log = log_create(second_bm);
  BMLogEntry *second_entry = entry_add(second_log);
  BM_log_vert_before_modified(second_log, second, -1);
  second->co[0] = 11.0f;
  BMLogSnapshot *second_snapshot = snapshot_create(second_bm, second_log);
  ASSERT_NE(second_snapshot, nullptr);

  BMesh *first_copy = mesh_copy(first_bm);
  BMesh *second_copy = mesh_copy(second_bm);
  BMLogSnapshotRestore *first_prepared = restore_prepare(
      first_copy, first_snapshot, {first_entry}, first_log);
  BMLogSnapshotRestore *second_prepared = restore_prepare(
      second_copy, second_snapshot, {second_entry}, second_log);
  ASSERT_NE(first_prepared, nullptr);
  ASSERT_NE(second_prepared, nullptr);
  log_free(first_log);
  log_free(second_log);
  BMLog *first_restored = restore_commit(first_prepared);
  BMLog *second_restored = restore_commit(second_prepared);
  BM_log_undo(first_copy, first_restored);
  BM_log_undo(second_copy, second_restored);
  EXPECT_EQ(first_vert(first_copy)->co[0], 0.0f);
  EXPECT_EQ(first_vert(second_copy)->co[0], 10.0f);
  BM_log_redo(first_copy, first_restored);
  BM_log_redo(second_copy, second_restored);
  EXPECT_EQ(first_vert(first_copy)->co[0], 1.0f);
  EXPECT_EQ(first_vert(second_copy)->co[0], 11.0f);
}

TEST_F(BMeshLogSnapshotTest, DeadMapPointersAreExcludedAndFutureIdsStayReserved)
{
  BMesh *bm = mesh_create();
  triangle_add(bm);
  BMLog *log = log_create(bm);
  BMLogEntry *future = entry_add(log);
  BMVert *added = vert_add(bm, 10.0f);
  BM_log_vert_added(log, added, -1);
  BM_log_undo(bm, log);
  /* Undo deliberately leaves dead pointers in the lookup maps. */
  BMLogSnapshot *snapshot = snapshot_create(bm, log);
  ASSERT_NE(snapshot, nullptr);
  EXPECT_EQ(BM_log_snapshot_cursor_id(snapshot), 0);
  BMesh *copy = mesh_copy(bm);
  BMLogSnapshotRestore *prepared = restore_prepare(copy, snapshot, {future}, log);
  ASSERT_NE(prepared, nullptr);
  log_free(log);
  BMLog *restored = restore_commit(prepared);

  BM_log_redo(copy, restored);
  ASSERT_EQ(copy->totvert, 4);
  entry_add(restored);
  BM_log_vert_added(restored, vert_add(copy, 20.0f), -1);
  /* Allocating an ID reserved by the future entry would now alias two live vertices. */
  EXPECT_NE(snapshot_create(copy, restored), nullptr);
  BM_log_undo(copy, restored);
  EXPECT_EQ(copy->totvert, 4);
  BM_log_undo(copy, restored);
  EXPECT_EQ(copy->totvert, 3);
}

TEST_F(BMeshLogSnapshotTest, TopologyAndMaskRecordsRemainReplayable)
{
  BMesh *bm = mesh_create();
  BM_data_layer_add_named(bm, &bm->vdata, CD_PROP_FLOAT, ".sculpt_mask");
  const int mask_offset = CustomData_get_offset_named(&bm->vdata, CD_PROP_FLOAT, ".sculpt_mask");
  BMLog *log = log_create(bm);
  BMLogEntry *entry = entry_add(log);
  BMFace *face = triangle_add(bm);
  face->head.hflag = BM_ELEM_SMOOTH;
  BMIter iter;
  BMVert *vert;
  BM_ITER_MESH (vert, &iter, bm, BM_VERTS_OF_MESH) {
    BM_ELEM_CD_SET_FLOAT(vert, mask_offset, 0.25f);
    vert->head.hflag = BM_ELEM_HIDDEN;
  }
  BM_log_all_added(bm, log);
  BMLogSnapshot *snapshot = snapshot_create(bm, log);
  ASSERT_NE(snapshot, nullptr);
  BMesh *copy = mesh_copy(bm);
  BMLogSnapshotRestore *prepared = restore_prepare(copy, snapshot, {entry}, log);
  ASSERT_NE(prepared, nullptr);
  log_free(log);
  BMLog *restored = restore_commit(prepared);

  BM_log_undo(copy, restored);
  EXPECT_EQ(copy->totvert, 0);
  EXPECT_EQ(copy->totface, 0);
  BM_log_redo(copy, restored);
  EXPECT_EQ(copy->totvert, 3);
  EXPECT_EQ(copy->totface, 1);
  BM_ITER_MESH (vert, &iter, copy, BM_VERTS_OF_MESH) {
    EXPECT_EQ(BM_ELEM_CD_GET_FLOAT(vert, mask_offset), 0.25f);
    EXPECT_TRUE(BM_elem_flag_test(vert, BM_ELEM_HIDDEN));
  }
  BM_ITER_MESH (face, &iter, copy, BM_FACES_OF_MESH) {
    EXPECT_TRUE(BM_elem_flag_test(face, BM_ELEM_SMOOTH));
  }
  EXPECT_NE(snapshot_create(copy, restored), nullptr);
}

TEST_F(BMeshLogSnapshotTest, MeshConversionPreservesReusedIdsAndFaceLoopOrder)
{
  BMesh *bm = mesh_create();
  BMLog *log = log_create(bm);
  BMLogEntry *topology = entry_add(log);
  const auto island_add = [&](const float offset,
                              const std::array<int, 3> corner_order,
                              std::array<BMVert *, 3> &verts) {
    const float coords[3][3] = {
        {offset + 0.0f, -1.0f, 2.0f},
        {offset + 2.0f, 3.0f, -1.0f},
        {offset - 1.0f, 1.0f, 4.0f},
    };
    for (int i = 0; i < 3; i++) {
      verts[i] = BM_vert_create(bm, coords[i], nullptr, BM_CREATE_NOP);
      BM_log_vert_added(log, verts[i], -1);
    }
    BMVert *corners[3] = {verts[corner_order[0]], verts[corner_order[1]], verts[corner_order[2]]};
    BMFace *face = BM_face_create_verts(bm, corners, 3, nullptr, BM_CREATE_NOP, true);
    BM_log_face_added(log, face);
    return face;
  };

  std::array<BMVert *, 3> first, removed, last, replacement;
  island_add(0.0f, {2, 0, 1}, first);
  BMFace *removed_face = island_add(10.0f, {0, 2, 1}, removed);
  island_add(20.0f, {1, 0, 2}, last);
  BM_log_face_removed(log, removed_face);
  BM_face_kill(bm, removed_face);
  for (BMVert *vert : removed) {
    BM_log_vert_removed(log, vert, -1);
    BM_vert_kill(bm, vert);
  }
  /* The new elements reuse released log IDs and BMesh pool slots between the surviving islands,
   * so their iteration order differs from their creation order. */
  island_add(30.0f, {1, 2, 0}, replacement);
  ASSERT_EQ(bm->totvert, 9);
  ASSERT_EQ(bm->totface, 3);

  BMLogEntry *move = entry_add(log);
  BM_log_vert_before_modified(log, first[0], -1);
  first[0]->co[0] = 0.5f;
  BMLogEntry *future = entry_add(log);
  BM_log_vert_before_modified(log, replacement[1], -1);
  replacement[1]->co[2] = 7.0f;
  BM_log_undo(bm, log);
  BMLogSnapshot *snapshot = snapshot_create(bm, log);
  ASSERT_NE(snapshot, nullptr);
  ASSERT_EQ(BM_log_snapshot_cursor_id(snapshot), BM_log_entry_identity(move));

  Vector<float3> expected_positions;
  int first_index = -1;
  int last_index = -1;
  int replacement_index = -1;
  BMIter iter;
  BMVert *vert;
  int index;
  BM_ITER_MESH_INDEX (vert, &iter, bm, BM_VERTS_OF_MESH, index) {
    expected_positions.append(float3(vert->co));
    if (vert == first[0]) {
      first_index = index;
    }
    if (vert == last[0]) {
      last_index = index;
    }
    if (vert == replacement[1]) {
      replacement_index = index;
    }
  }
  ASSERT_GE(first_index, 0);
  ASSERT_GE(last_index, 0);
  ASSERT_GE(replacement_index, 0);
  ASSERT_LT(replacement_index, last_index);

  BMesh *converted = mesh_conversion_roundtrip(bm);
  ASSERT_EQ(converted->totvert, bm->totvert);
  ASSERT_EQ(converted->totface, bm->totface);
  Vector<BMVert *> converted_verts;
  BM_ITER_MESH_INDEX (vert, &iter, converted, BM_VERTS_OF_MESH, index) {
    EXPECT_EQ(float3(vert->co), expected_positions[index]);
    converted_verts.append(vert);
  }
  const char *error = nullptr;
  /* Preparation validates the oriented face-corner ID tuples in addition to the vertex order
   * checked above. No BM_mesh_copy is involved in this path. */
  BMLogSnapshotRestore *prepared = restore_prepare(
      converted, snapshot, {topology, move, future}, log, &error);
  ASSERT_NE(prepared, nullptr) << (error ? error : "No restoration diagnostic");
  log_free(log);
  BMLog *restored = restore_commit(prepared);
  BM_log_redo(converted, restored);
  EXPECT_EQ(converted_verts[replacement_index]->co[2], 7.0f);
  BM_log_undo(converted, restored);
  EXPECT_EQ(converted_verts[replacement_index]->co[2], -1.0f);
  BM_log_undo(converted, restored);
  EXPECT_EQ(converted_verts[first_index]->co[0], 0.0f);
  BM_log_undo(converted, restored);
  EXPECT_EQ(converted->totvert, 0);
  EXPECT_EQ(converted->totface, 0);
  BM_log_redo(converted, restored);
  EXPECT_EQ(converted->totvert, 9);
  EXPECT_EQ(converted->totface, 3);
  EXPECT_NE(snapshot_create(converted, restored), nullptr);
}

TEST_F(BMeshLogSnapshotTest, WrongCountsTopologyEpochAndOrderLeaveEntriesOwned)
{
  BMesh *bm = mesh_create();
  BMFace *face = triangle_add(bm);
  BMLog *log = log_create(bm);
  BMLogEntry *first = entry_add(log);
  BMLogEntry *second = entry_add(log);
  BMLogSnapshot *snapshot = snapshot_create(bm, log);
  ASSERT_NE(snapshot, nullptr);
  const char *error = nullptr;
  BMesh *wrong_count = mesh_copy(bm);
  vert_add(wrong_count, 2.0f);
  EXPECT_EQ(restore_prepare(wrong_count, snapshot, {first, second}, log, &error), nullptr);
  EXPECT_NE(error, nullptr);

  BMesh *wrong_topology = mesh_copy(bm);
  BMIter iter;
  BMFace *copy_face = static_cast<BMFace *>(
      BM_iter_new(&iter, wrong_topology, BM_FACES_OF_MESH, nullptr));
  BM_face_normal_flip(wrong_topology, copy_face);
  EXPECT_EQ(restore_prepare(wrong_topology, snapshot, {first, second}, log, &error), nullptr);
  EXPECT_NE(error, nullptr);

  BMLog *other = log_create(bm);
  BMLogEntry *foreign = entry_add(other);
  EXPECT_EQ(restore_prepare(bm, snapshot, {foreign}, log, &error), nullptr);
  EXPECT_EQ(restore_prepare(bm, snapshot, {second, first}, log, &error), nullptr);
  EXPECT_EQ(restore_prepare(bm, snapshot, {first}, log, &error), nullptr);
  /* Successful logging through the original owner after all refusals. */
  BM_log_face_modified(log, face);
  EXPECT_NE(snapshot_create(bm, log), nullptr);
}

TEST_F(BMeshLogSnapshotTest, PrunedSavedCursorUsesGreatestSurvivingPastEntry)
{
  BMesh *bm = mesh_create();
  vert_add(bm, 0.0f);
  BMLog *log = log_create(bm);
  BMLogEntry *past = entry_add(log);
  BMLogEntry *cursor = entry_add(log);
  BMLogSnapshot *snapshot = snapshot_create(bm, log);
  ASSERT_NE(snapshot, nullptr);
  entry_drop(cursor);
  BMLogSnapshotRestore *prepared = restore_prepare(bm, snapshot, {past}, log);
  ASSERT_NE(prepared, nullptr);
  log_free(log);
  BMLog *restored = restore_commit(prepared);
  BMLogSnapshot *restored_snapshot = snapshot_create(bm, restored);
  ASSERT_NE(restored_snapshot, nullptr);
  EXPECT_EQ(BM_log_snapshot_cursor_id(restored_snapshot), BM_log_entry_identity(past));
}

TEST_F(BMeshLogSnapshotTest, PrunedCursorBeforeSurvivingFutureUsesNullCursor)
{
  BMesh *bm = mesh_create();
  vert_add(bm, 0.0f);
  BMLog *log = log_create(bm);
  BMLogEntry *cursor = entry_add(log);
  BMLogSnapshot *snapshot = snapshot_create(bm, log);
  ASSERT_NE(snapshot, nullptr);
  BMLogEntry *future = entry_add(log);
  entry_drop(cursor);
  BMLogSnapshotRestore *prepared = restore_prepare(bm, snapshot, {future}, log);
  ASSERT_NE(prepared, nullptr);
  log_free(log);
  BMLog *restored = restore_commit(prepared);
  BMLogSnapshot *restored_snapshot = snapshot_create(bm, restored);
  ASSERT_NE(restored_snapshot, nullptr);
  EXPECT_EQ(BM_log_snapshot_cursor_id(restored_snapshot), 0);
}

TEST_F(BMeshLogSnapshotTest, NoRetainedEntriesPreservesEpochAndLiveIds)
{
  BMesh *bm = mesh_create();
  triangle_add(bm);
  BMLog *log = log_create(bm);
  BMLogSnapshot *snapshot = snapshot_create(bm, log);
  ASSERT_NE(snapshot, nullptr);
  BMesh *copy = mesh_copy(bm);
  BMLogSnapshotRestore *prepared = restore_prepare(copy, snapshot, {}, log);
  ASSERT_NE(prepared, nullptr);
  log_free(log);
  BMLog *restored = restore_commit(prepared);
  BMLogEntry *entry = entry_add(restored);
  BM_log_vert_added(restored, vert_add(copy, 10.0f), -1);
  BMLogSnapshot *restored_snapshot = snapshot_create(copy, restored);
  ASSERT_NE(restored_snapshot, nullptr);
  EXPECT_EQ(BM_log_snapshot_epoch(restored_snapshot), BM_log_snapshot_epoch(snapshot));
  EXPECT_EQ(BM_log_entry_epoch(entry), BM_log_snapshot_epoch(snapshot));
  BM_log_undo(copy, restored);
  EXPECT_EQ(copy->totvert, 3);
}

TEST_F(BMeshLogSnapshotTest, ExistingEntryReconstructionPreservesEpoch)
{
  BMesh *bm = mesh_create();
  BMLog *log = log_create(bm);
  BMLogEntry *entry = entry_add(log);
  const uint64_t epoch = BM_log_entry_epoch(entry);
  log_free(log);
  BMLog *rebuilt = BM_log_from_existing_entries_create(bm, entry);
  logs_.append(rebuilt);
  BMLogSnapshot *snapshot = snapshot_create(bm, rebuilt);
  ASSERT_NE(snapshot, nullptr);
  EXPECT_EQ(BM_log_snapshot_epoch(snapshot), epoch);
  EXPECT_EQ(BM_log_entry_epoch(entry_add(rebuilt)), epoch);
}

#ifndef NDEBUG
TEST_F(BMeshLogSnapshotTest, FaceReferencesReserveIdsEvenWithoutVertexMapKeys)
{
  BMesh *bm = mesh_create();
  BMFace *face = triangle_add(bm);
  BMLog *log = log_create(bm);
  BMLogEntry *entry = entry_add(log);
  BM_log_face_removed(log, face);
  BM_face_kill(bm, face);
  /* Retain only a face record: this directly exercises its vertex-ID reservation independently
   * of any vertex-map key. The synthetic history is not replayed. */
  while (bm->totvert) {
    BM_vert_kill(bm, first_vert(bm));
  }
  BMLogSnapshot *snapshot = snapshot_create(bm, log);
  ASSERT_NE(snapshot, nullptr);
  BMLogSnapshotRestore *prepared = restore_prepare(bm, snapshot, {entry}, log);
  ASSERT_NE(prepared, nullptr);
  log_free(log);
  BMLog *restored = restore_commit(prepared);
  for (uint id = 0; id < 4; id++) {
    EXPECT_FALSE(range_tree_uint_has(BM_log_unused_ids(restored), id));
  }
}
#endif

}  // namespace blender
