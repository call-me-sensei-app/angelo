/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include <array>
#include <memory>

#include "testing/testing.h"

#include "MEM_guardedalloc.h"

#include "DNA_meshdata_types.h"
#include "DNA_scene_types.h"

#include "BLI_bitmap.h"
#include "BLI_math_vector.h"
#include "BLI_math_vector_types.hh"
#include "BLI_mempool.h"
#include "BLI_string.h"
#include "BLI_vector.hh"

#include "BKE_customdata.hh"
#include "BKE_gtest_base.hh"

#include "bmesh.hh"
#include "intern/bmesh_log.hh"
#include "intern/bmesh_snapshot.hh"

namespace blender {

class BMeshGeometrySnapshotTest : public bke::BlenderGTestBase {
 protected:
  Vector<BMesh *> meshes_;
  Vector<BMGeometrySnapshot *> snapshots_;
  Vector<BMLog *> logs_;
  Vector<BMLogEntry *> entries_;
  Vector<BMLogSnapshot *> log_snapshots_;
  Vector<BMLogSnapshotRestore *> log_restores_;

  void TearDown() override
  {
    for (BMGeometrySnapshot *snapshot : snapshots_) {
      BM_geometry_snapshot_free(snapshot);
    }
    for (BMLogSnapshot *snapshot : log_snapshots_) {
      BM_log_snapshot_free(snapshot);
    }
    for (BMLogSnapshotRestore *restore : log_restores_) {
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

  BMGeometrySnapshot *snapshot_create(const BMesh *bm, const char **error = nullptr)
  {
    BMGeometrySnapshot *snapshot = BM_geometry_snapshot_create(bm, error);
    if (snapshot) {
      snapshots_.append(snapshot);
    }
    return snapshot;
  }

  BMesh *snapshot_restore(const BMGeometrySnapshot *snapshot)
  {
    BMesh *bm = BM_geometry_snapshot_restore(snapshot);
    if (bm) {
      meshes_.append(bm);
    }
    return bm;
  }

  static BMFace *triangle_add(BMesh *bm, const float offset = 0.0f)
  {
    const float coords[3][3] = {
        {offset, -1.0f, 2.0f}, {offset + 2.0f, 3.0f, -1.0f}, {offset - 1.0f, 1.0f, 4.0f}};
    BMVert *verts[3];
    for (int i = 0; i < 3; i++) {
      verts[i] = BM_vert_create(bm, coords[i], nullptr, BM_CREATE_NOP);
    }
    /* The corner order deliberately differs from vertex-pool iteration order. */
    BMVert *corners[3] = {verts[2], verts[0], verts[1]};
    return BM_face_create_verts(bm, corners, 3, nullptr, BM_CREATE_NOP, true);
  }

  static BMFace *first_face(BMesh *bm)
  {
    BMIter iter;
    return static_cast<BMFace *>(BM_iter_new(&iter, bm, BM_FACES_OF_MESH, nullptr));
  }

  template<typename T, typename Elem> static T &layer_data(Elem *elem, const int offset)
  {
    return *static_cast<T *>(BM_ELEM_CD_GET_VOID_P(elem, offset));
  }

  static size_t mesh_pool_bytes(const BMesh *bm)
  {
    return BLI_mempool_allocated_bytes(bm->vpool) + BLI_mempool_allocated_bytes(bm->epool) +
           BLI_mempool_allocated_bytes(bm->lpool) + BLI_mempool_allocated_bytes(bm->fpool) +
           BLI_mempool_allocated_bytes(bm->vdata.pool) +
           BLI_mempool_allocated_bytes(bm->edata.pool) +
           BLI_mempool_allocated_bytes(bm->ldata.pool) +
           BLI_mempool_allocated_bytes(bm->pdata.pool);
  }
};

TEST_F(BMeshGeometrySnapshotTest, CaptureEqualityAndRepeatedRestoreLeaveDirtySourceUntouched)
{
  BMesh *bm = mesh_create();
  BMFace *face = triangle_add(bm);
  BM_mesh_elem_table_ensure(bm, BM_VERT | BM_EDGE | BM_FACE);
  BMVert **vtable = bm->vtable;
  BMEdge **etable = bm->etable;
  BMFace **ftable = bm->ftable;
  const std::array<BMVert *, 3> verts = {vtable[0], vtable[1], vtable[2]};
  const std::array<BMEdge *, 3> edges = {etable[0], etable[1], etable[2]};
  const std::array<float3, 3> coordinates = {
      float3(verts[0]->co), float3(verts[1]->co), float3(verts[2]->co)};
  const std::array<int, 3> table_sizes = {bm->vtable_tot, bm->etable_tot, bm->ftable_tot};

  Vector<BMHeader *> headers;
  for (BMVert *vert : verts) {
    headers.append(&vert->head);
  }
  for (BMEdge *edge : edges) {
    headers.append(&edge->head);
  }
  BMLoop *loop = BM_FACE_FIRST_LOOP(face);
  const std::array<BMLoop *, 3> loops = {loop, loop->next, loop->prev};
  for (int i = 0; i < face->len; i++, loop = loop->next) {
    headers.append(&loop->head);
  }
  headers.append(&face->head);
  Vector<BMHeader> original_headers;
  for (const int i : headers.index_range()) {
    headers[i]->index = -100 - i * 7;
    headers[i]->hflag |= BM_ELEM_TAG;
    original_headers.append(*headers[i]);
  }
  bm->elem_index_dirty = BM_ALL;
  bm->elem_table_dirty = BM_VERT | BM_EDGE | BM_FACE;

  const auto expect_source_unchanged = [&]() {
    EXPECT_EQ(bm->totvert, 3);
    EXPECT_EQ(bm->totedge, 3);
    EXPECT_EQ(bm->totloop, 3);
    EXPECT_EQ(bm->totface, 1);
    EXPECT_EQ(bm->elem_index_dirty, BM_ALL);
    EXPECT_EQ(bm->elem_table_dirty, BM_VERT | BM_EDGE | BM_FACE);
    EXPECT_EQ(bm->vtable, vtable);
    EXPECT_EQ(bm->etable, etable);
    EXPECT_EQ(bm->ftable, ftable);
    EXPECT_EQ(bm->vtable_tot, table_sizes[0]);
    EXPECT_EQ(bm->etable_tot, table_sizes[1]);
    EXPECT_EQ(bm->ftable_tot, table_sizes[2]);
    for (int i = 0; i < 3; i++) {
      EXPECT_EQ(bm->vtable[i], verts[i]);
      EXPECT_EQ(bm->etable[i], edges[i]);
      EXPECT_EQ(float3(verts[i]->co), coordinates[i]);
      EXPECT_EQ(loops[i]->next, loops[(i + 1) % 3]);
      EXPECT_EQ(loops[i]->prev, loops[(i + 2) % 3]);
      EXPECT_EQ(loops[i]->f, face);
    }
    EXPECT_EQ(bm->ftable[0], face);
    EXPECT_EQ(BM_FACE_FIRST_LOOP(face), loops[0]);
    EXPECT_EQ(face->len, 3);
    for (const int i : headers.index_range()) {
      EXPECT_EQ(headers[i]->index, original_headers[i].index);
      EXPECT_EQ(headers[i]->hflag, original_headers[i].hflag);
      EXPECT_EQ(headers[i]->api_flag, original_headers[i].api_flag);
      EXPECT_EQ(headers[i]->data, original_headers[i].data);
    }
  };

  BMGeometrySnapshot *snapshot = snapshot_create(bm);
  ASSERT_NE(snapshot, nullptr);
  expect_source_unchanged();
  for (int i = 0; i < 3; i++) {
    EXPECT_TRUE(BM_geometry_snapshot_equals(snapshot, bm));
    BMesh *restored = snapshot_restore(snapshot);
    ASSERT_NE(restored, nullptr);
    EXPECT_NE(restored, bm);
    EXPECT_TRUE(BM_geometry_snapshot_equals(snapshot, restored));
    expect_source_unchanged();
  }
}

TEST_F(BMeshGeometrySnapshotTest, PreservesFlaggedLayersShapeKeysAndSelectionState)
{
  BMesh *bm = mesh_create();
  BM_data_layer_add_named(bm, &bm->vdata, CD_PROP_FLOAT, ".sculpt_mask");
  BM_data_layer_add_named(bm, &bm->vdata, CD_PROP_FLOAT, "temporary_value");
  BM_data_layer_add(bm, &bm->vdata, CD_ORIGINDEX);
  BM_data_layer_add(bm, &bm->vdata, CD_SHAPE_KEYINDEX);
  BM_data_layer_add_named(bm, &bm->vdata, CD_SHAPEKEY, "Basis");
  BM_data_layer_add_named(bm, &bm->vdata, CD_SHAPEKEY, "Raised");
  BM_data_layer_add_named(bm, &bm->edata, CD_PROP_INT32, "edge_value");
  BM_data_layer_add_named(bm, &bm->ldata, CD_PROP_FLOAT2, "UVMap");
  BM_data_layer_add_named(bm, &bm->pdata, CD_PROP_INT32, ".sculpt_face_set");
  BM_data_layer_add(bm, &bm->vdata, CD_BM_ELEM_PYPTR);
  BM_data_layer_add_named(bm, &bm->vdata, CD_PROP_FLOAT3, "after_python_layer");
  BMFace *face = triangle_add(bm);
  BMLoop *loop = BM_FACE_FIRST_LOOP(face);
  const int mask_offset = CustomData_get_offset_named(&bm->vdata, CD_PROP_FLOAT, ".sculpt_mask");
  const int temp_offset = CustomData_get_offset_named(
      &bm->vdata, CD_PROP_FLOAT, "temporary_value");
  const int orig_offset = CustomData_get_offset(&bm->vdata, CD_ORIGINDEX);
  const int key_index_offset = CustomData_get_offset(&bm->vdata, CD_SHAPE_KEYINDEX);
  const int vector_offset = CustomData_get_offset_named(
      &bm->vdata, CD_PROP_FLOAT3, "after_python_layer");
  const int key_first = CustomData_get_layer_index(&bm->vdata, CD_SHAPEKEY);
  bm->vdata.layers[CustomData_get_named_layer_index(&bm->vdata, CD_PROP_FLOAT, ".sculpt_mask")]
      .flag = CD_FLAG_NOCOPY;
  bm->vdata.layers[CustomData_get_named_layer_index(&bm->vdata, CD_PROP_FLOAT, "temporary_value")]
      .flag = CD_FLAG_TEMPORARY;
  bm->vdata.layers[key_first].uid = 17;
  bm->vdata.layers[key_first + 1].uid = 83;
  CustomData_set_layer_active(&bm->vdata, CD_SHAPEKEY, 1);
  CustomData_set_layer_render(&bm->vdata, CD_SHAPEKEY, 1);
  bm->shapenr = 2;
  const int edge_offset = CustomData_get_offset_named(&bm->edata, CD_PROP_INT32, "edge_value");
  const int uv_offset = CustomData_get_offset_named(&bm->ldata, CD_PROP_FLOAT2, "UVMap");
  const int face_offset = CustomData_get_offset_named(
      &bm->pdata, CD_PROP_INT32, ".sculpt_face_set");
  for (int i = 0; i < 3; i++, loop = loop->next) {
    BM_ELEM_CD_SET_FLOAT(loop->v, mask_offset, 0.125f * (i + 1));
    BM_ELEM_CD_SET_FLOAT(loop->v, temp_offset, 11.0f + i);
    BM_ELEM_CD_SET_INT(loop->v, orig_offset, 100 + i);
    BM_ELEM_CD_SET_INT(loop->v, key_index_offset, i * 3);
    layer_data<float3>(loop->v, vector_offset) = float3(i, 6, 12);
    layer_data<float3>(loop->v, bm->vdata.layers[key_first].offset) = float3(i, 2, 3);
    layer_data<float3>(loop->v, bm->vdata.layers[key_first + 1].offset) = float3(i, 4, 9);
    BM_ELEM_CD_SET_INT(loop->e, edge_offset, 31 + i);
    layer_data<float2>(loop, uv_offset) = float2(0.25f * i, 0.75f);
    copy_v3_fl3(loop->v->no, 0.0f, 1.0f, 0.0f);
    BM_elem_flag_enable(loop->e, BM_ELEM_SEAM);
    BM_elem_flag_enable(loop, BM_ELEM_SELECT_UV | BM_ELEM_SELECT_UV_EDGE);
  }
  BM_ELEM_CD_SET_INT(face, face_offset, 29);
  face->mat_nr = 7;
  copy_v3_fl3(face->no, 1.0f, 0.0f, 0.0f);
  BM_elem_flag_enable(face, BM_ELEM_SMOOTH);
  BM_face_select_set(bm, face, true);
  BM_select_history_store(bm, loop->v);
  BM_select_history_store(bm, loop->e);
  BM_select_history_store(bm, face);
  bm->act_face = face;
  bm->selectmode = SCE_SELECT_VERTEX | SCE_SELECT_EDGE | SCE_SELECT_FACE;
  bm->uv_select_sync_valid = true;

  /* Python instance identity is deliberately outside the geometry contract. Reset the sentinel
   * before any assertion can leave the fixture, since BM_mesh_free invalidates real wrappers. */
  int python_identity = 0;
  bm->py_handle = &python_identity;
  BMGeometrySnapshot *snapshot = snapshot_create(bm);
  bm->py_handle = nullptr;
  ASSERT_NE(snapshot, nullptr);
  BMesh *restored = snapshot_restore(snapshot);
  ASSERT_NE(restored, nullptr);
  EXPECT_TRUE(BM_geometry_snapshot_equals(snapshot, restored));
  EXPECT_TRUE(BM_geometry_snapshot_equals(snapshot, bm));
  EXPECT_EQ(restored->py_handle, nullptr);
  EXPECT_EQ(CustomData_get_offset(&restored->vdata, CD_BM_ELEM_PYPTR), -1);
  EXPECT_EQ(restored->shapenr, 2);
  EXPECT_EQ(restored->selectmode, bm->selectmode);
  EXPECT_TRUE(restored->uv_select_sync_valid);
  EXPECT_EQ(restored->totvertsel, bm->totvertsel);
  EXPECT_EQ(restored->totedgesel, bm->totedgesel);
  EXPECT_EQ(restored->totfacesel, bm->totfacesel);
  EXPECT_EQ(restored->vdata.totlayer, bm->vdata.totlayer - 1);
  for (int i = 0; i < bm->vdata.totlayer; i++) {
    const CustomDataLayer &source = bm->vdata.layers[i];
    if (source.type == CD_BM_ELEM_PYPTR) {
      continue;
    }
    const int index = CustomData_get_named_layer_index(&restored->vdata, source.type, source.name);
    ASSERT_GE(index, 0);
    const CustomDataLayer &copy = restored->vdata.layers[index];
    EXPECT_EQ(copy.flag, source.flag);
    EXPECT_EQ(copy.uid, source.uid);
    EXPECT_EQ(copy.active, source.active);
    EXPECT_EQ(copy.active_rnd, source.active_rnd);
  }
  BMFace *copy_face = first_face(restored);
  ASSERT_NE(copy_face, nullptr);
  EXPECT_EQ(restored->act_face, copy_face);
  EXPECT_EQ(copy_face->mat_nr, 7);
  EXPECT_EQ(float3(copy_face->no), float3(face->no));
  EXPECT_EQ(copy_face->head.hflag, face->head.hflag);
  EXPECT_EQ(BM_ELEM_CD_GET_INT(copy_face, face_offset), 29);
  BMLoop *copy_loop = BM_FACE_FIRST_LOOP(copy_face);
  const int copy_vector_offset = CustomData_get_offset_named(
      &restored->vdata, CD_PROP_FLOAT3, "after_python_layer");
  ASSERT_GE(copy_vector_offset, 0);
  loop = BM_FACE_FIRST_LOOP(face);
  for (int i = 0; i < 3; i++, loop = loop->next, copy_loop = copy_loop->next) {
    EXPECT_EQ(float3(copy_loop->v->co), float3(loop->v->co));
    EXPECT_EQ(float3(copy_loop->v->no), float3(loop->v->no));
    EXPECT_EQ(copy_loop->head.hflag, loop->head.hflag);
    EXPECT_EQ(copy_loop->e->head.hflag, loop->e->head.hflag);
    EXPECT_EQ(BM_ELEM_CD_GET_FLOAT(copy_loop->v, mask_offset), 0.125f * (i + 1));
    EXPECT_EQ(BM_ELEM_CD_GET_FLOAT(copy_loop->v, temp_offset), 11.0f + i);
    EXPECT_EQ(BM_ELEM_CD_GET_INT(copy_loop->v, orig_offset), 100 + i);
    EXPECT_EQ(BM_ELEM_CD_GET_INT(copy_loop->v, key_index_offset), i * 3);
    EXPECT_EQ(layer_data<float3>(copy_loop->v, copy_vector_offset), float3(i, 6, 12));
    EXPECT_EQ(BM_ELEM_CD_GET_INT(copy_loop->e, edge_offset), 31 + i);
    EXPECT_EQ(layer_data<float2>(copy_loop, uv_offset), float2(0.25f * i, 0.75f));
    const int copy_key_first = CustomData_get_layer_index(&restored->vdata, CD_SHAPEKEY);
    EXPECT_EQ(layer_data<float3>(copy_loop->v, restored->vdata.layers[copy_key_first].offset),
              float3(i, 2, 3));
    EXPECT_EQ(layer_data<float3>(copy_loop->v, restored->vdata.layers[copy_key_first + 1].offset),
              float3(i, 4, 9));
  }
  const std::array<BMElem *, 3> selected = {reinterpret_cast<BMElem *>(copy_loop->v),
                                            reinterpret_cast<BMElem *>(copy_loop->e),
                                            reinterpret_cast<BMElem *>(copy_face)};
  BMEditSelection *selection = static_cast<BMEditSelection *>(restored->selected.first);
  for (BMElem *element : selected) {
    ASSERT_NE(selection, nullptr);
    EXPECT_EQ(selection->ele, element);
    EXPECT_EQ(selection->htype, element->head.htype);
    selection = selection->next;
  }
  EXPECT_EQ(selection, nullptr);

  bm->uv_select_sync_valid = false;
  EXPECT_FALSE(BM_geometry_snapshot_equals(snapshot, bm));
  bm->uv_select_sync_valid = true;
  bm->act_face = nullptr;
  EXPECT_FALSE(BM_geometry_snapshot_equals(snapshot, bm));
  bm->act_face = face;
  face->mat_nr++;
  EXPECT_FALSE(BM_geometry_snapshot_equals(snapshot, bm));
  face->mat_nr--;
  bm->vdata.layers[key_first + 1].uid++;
  EXPECT_FALSE(BM_geometry_snapshot_equals(snapshot, bm));
  bm->vdata.layers[key_first + 1].uid--;
  BM_ELEM_CD_SET_FLOAT(loop->v, temp_offset, 99.0f);
  EXPECT_FALSE(BM_geometry_snapshot_equals(snapshot, bm));
  BM_ELEM_CD_SET_FLOAT(loop->v, temp_offset, 11.0f);
  EXPECT_TRUE(BM_geometry_snapshot_equals(snapshot, bm));
}

TEST_F(BMeshGeometrySnapshotTest, DynamicPayloadsAreIndependentAndEqualityDetectsTheirEdits)
{
  BMesh *bm = mesh_create();
  BM_data_layer_add(bm, &bm->vdata, CD_MDEFORMVERT);
  BM_data_layer_add(bm, &bm->ldata, CD_MDISPS);
  BM_data_layer_add(bm, &bm->ldata, CD_GRID_PAINT_MASK);
  BMLoop *loop = BM_FACE_FIRST_LOOP(triangle_add(bm));
  const int deform_offset = CustomData_get_offset(&bm->vdata, CD_MDEFORMVERT);
  const int disp_offset = CustomData_get_offset(&bm->ldata, CD_MDISPS);
  const int grid_offset = CustomData_get_offset(&bm->ldata, CD_GRID_PAINT_MASK);
  MDeformVert &deform = layer_data<MDeformVert>(loop->v, deform_offset);
  deform.totweight = 2;
  deform.flag = 37;
  deform.dw = MEM_new_array_zeroed<MDeformWeight>(2, __func__);
  deform.dw[0] = {4, 0.25f};
  deform.dw[1] = {9, 0.75f};
  MDisps &disp = layer_data<MDisps>(loop, disp_offset);
  disp.totdisp = 9;
  disp.level = 2;
  disp.disps = MEM_new_array_zeroed<float[3]>(9, __func__);
  disp.disps[8][2] = 4.5f;
  disp.hidden = BLI_BITMAP_NEW(9, __func__);
  BLI_BITMAP_ENABLE(disp.hidden, 7);
  GridPaintMask &grid = layer_data<GridPaintMask>(loop, grid_offset);
  grid.level = 2;
  grid.data = MEM_new_array_zeroed<float>(9, __func__);
  grid.data[8] = 0.625f;

  BMGeometrySnapshot *snapshot = snapshot_create(bm);
  ASSERT_NE(snapshot, nullptr);
  BMGeometrySnapshot *same = snapshot_create(bm);
  ASSERT_NE(same, nullptr);
  EXPECT_TRUE(BM_geometry_snapshot_equals(snapshot, same));
  BMesh *first = snapshot_restore(snapshot);
  BMesh *second = snapshot_restore(snapshot);
  ASSERT_NE(first, nullptr);
  ASSERT_NE(second, nullptr);
  BMLoop *first_loop = BM_FACE_FIRST_LOOP(first_face(first));
  BMLoop *second_loop = BM_FACE_FIRST_LOOP(first_face(second));
  MDeformVert &first_deform = layer_data<MDeformVert>(first_loop->v, deform_offset);
  MDisps &first_disp = layer_data<MDisps>(first_loop, disp_offset);
  GridPaintMask &first_grid = layer_data<GridPaintMask>(first_loop, grid_offset);
  ASSERT_NE(first_deform.dw, nullptr);
  ASSERT_NE(first_disp.disps, nullptr);
  ASSERT_NE(first_disp.hidden, nullptr);
  ASSERT_NE(first_grid.data, nullptr);
  EXPECT_NE(first_deform.dw, deform.dw);
  EXPECT_NE(first_deform.dw, layer_data<MDeformVert>(second_loop->v, deform_offset).dw);
  ASSERT_EQ(first_deform.totweight, 2);
  EXPECT_EQ(first_deform.flag, 37);
  EXPECT_EQ(first_deform.dw[1].def_nr, 9);
  EXPECT_EQ(first_deform.dw[1].weight, 0.75f);
  EXPECT_NE(first_disp.disps, disp.disps);
  EXPECT_NE(first_disp.hidden, disp.hidden);
  EXPECT_NE(first_disp.disps, layer_data<MDisps>(second_loop, disp_offset).disps);
  EXPECT_NE(first_disp.hidden, layer_data<MDisps>(second_loop, disp_offset).hidden);
  EXPECT_EQ(first_disp.disps[8][2], 4.5f);
  EXPECT_TRUE(BLI_BITMAP_TEST(first_disp.hidden, 7));
  EXPECT_NE(first_grid.data, grid.data);
  EXPECT_NE(first_grid.data, layer_data<GridPaintMask>(second_loop, grid_offset).data);
  EXPECT_EQ(first_grid.data[8], 0.625f);

  deform.dw[1].weight = 0.5f;
  EXPECT_FALSE(BM_geometry_snapshot_equals(snapshot, bm));
  deform.dw[1].weight = 0.75f;
  disp.disps[8][2] = 8.0f;
  EXPECT_FALSE(BM_geometry_snapshot_equals(snapshot, bm));
  disp.disps[8][2] = 4.5f;
  BLI_BITMAP_DISABLE(disp.hidden, 7);
  EXPECT_FALSE(BM_geometry_snapshot_equals(snapshot, bm));
  BLI_BITMAP_ENABLE(disp.hidden, 7);
  grid.data[8] = 0.125f;
  EXPECT_FALSE(BM_geometry_snapshot_equals(snapshot, bm));
  BMGeometrySnapshot *different = snapshot_create(bm);
  ASSERT_NE(different, nullptr);
  EXPECT_FALSE(BM_geometry_snapshot_equals(snapshot, different));
  EXPECT_TRUE(BM_geometry_snapshot_equals(snapshot, first));
  first_grid.data[8] = 0.0f;
  EXPECT_FALSE(BM_geometry_snapshot_equals(snapshot, first));
  EXPECT_TRUE(BM_geometry_snapshot_equals(snapshot, second));
  BMesh *third = snapshot_restore(snapshot);
  ASSERT_NE(third, nullptr);
  EXPECT_TRUE(BM_geometry_snapshot_equals(snapshot, third));
}

TEST_F(BMeshGeometrySnapshotTest, PreservesHiddenOnlyDisplacementsAndEmptyGridLevel)
{
  BMesh *bm = mesh_create();
  BM_data_layer_add(bm, &bm->ldata, CD_MDISPS);
  BM_data_layer_add(bm, &bm->ldata, CD_GRID_PAINT_MASK);
  BMLoop *loop = BM_FACE_FIRST_LOOP(triangle_add(bm));
  const int disp_offset = CustomData_get_offset(&bm->ldata, CD_MDISPS);
  const int grid_offset = CustomData_get_offset(&bm->ldata, CD_GRID_PAINT_MASK);
  MDisps &disp = layer_data<MDisps>(loop, disp_offset);
  disp.totdisp = 25;
  disp.level = 3;
  disp.hidden = BLI_BITMAP_NEW(25, __func__);
  BLI_BITMAP_ENABLE(disp.hidden, 24);
  GridPaintMask &grid = layer_data<GridPaintMask>(loop, grid_offset);
  grid.level = 3;
  BMGeometrySnapshot *snapshot = snapshot_create(bm);
  ASSERT_NE(snapshot, nullptr);
  BMesh *restored = snapshot_restore(snapshot);
  ASSERT_NE(restored, nullptr);
  BMLoop *copy_loop = BM_FACE_FIRST_LOOP(first_face(restored));
  const MDisps &copy_disp = layer_data<MDisps>(copy_loop, disp_offset);
  EXPECT_EQ(copy_disp.disps, nullptr);
  EXPECT_EQ(copy_disp.totdisp, 25);
  EXPECT_EQ(copy_disp.level, 3);
  ASSERT_NE(copy_disp.hidden, nullptr);
  EXPECT_NE(copy_disp.hidden, disp.hidden);
  EXPECT_TRUE(BLI_BITMAP_TEST(copy_disp.hidden, 24));
  const GridPaintMask &copy_grid = layer_data<GridPaintMask>(copy_loop, grid_offset);
  EXPECT_EQ(copy_grid.data, nullptr);
  EXPECT_EQ(copy_grid.level, 3);
  EXPECT_TRUE(BM_geometry_snapshot_equals(snapshot, restored));
  BLI_BITMAP_DISABLE(disp.hidden, 24);
  EXPECT_FALSE(BM_geometry_snapshot_equals(snapshot, bm));
  BLI_BITMAP_ENABLE(disp.hidden, 24);
  grid.level = 2;
  EXPECT_FALSE(BM_geometry_snapshot_equals(snapshot, bm));
}

TEST_F(BMeshGeometrySnapshotTest, UnknownCustomDataIsRejectedWithoutClaimingEquality)
{
  BMesh *bm = mesh_create();
  BM_data_layer_add(bm, &bm->vdata, CD_PROP_FLOAT);
  triangle_add(bm);
  BMGeometrySnapshot *snapshot = snapshot_create(bm);
  ASSERT_NE(snapshot, nullptr);
  const eCustomDataType original_type = bm->vdata.layers[0].type;
  bm->vdata.layers[0].type = eCustomDataType(CD_NUMTYPES);
  const char *error = nullptr;
  BMGeometrySnapshot *unknown = snapshot_create(bm, &error);
  const bool equal = BM_geometry_snapshot_equals(snapshot, bm);
  /* Restore the valid descriptor before assertions or fixture cleanup: an unknown type must never
   * reach the ordinary CustomData destructor, even if the snapshot API regresses. */
  bm->vdata.layers[0].type = original_type;
  EXPECT_EQ(unknown, nullptr);
  EXPECT_NE(error, nullptr);
  EXPECT_FALSE(equal);
  EXPECT_TRUE(BM_geometry_snapshot_equals(snapshot, bm));
}

TEST_F(BMeshGeometrySnapshotTest, ExternalCustomDataMetadataIsOwnedAndCompared)
{
  BMesh *bm = mesh_create();
  BM_data_layer_add(bm, &bm->ldata, CD_MDISPS);
  BMLoop *loop = BM_FACE_FIRST_LOOP(triangle_add(bm));
  MDisps &disp = layer_data<MDisps>(loop, CustomData_get_offset(&bm->ldata, CD_MDISPS));
  disp.totdisp = 9;
  disp.level = 2;
  disp.disps = MEM_new_array_zeroed<float[3]>(9, __func__);
  disp.disps[8][2] = 1.25f;
  bm->ldata.layers[0].flag = CD_FLAG_EXTERNAL | CD_FLAG_IN_MEMORY;
  bm->ldata.external = MEM_new<CustomDataExternal>(__func__);
  STRNCPY(bm->ldata.external->filepath, "//snapshot-displacements.btx");
  CustomDataExternal *source_external = bm->ldata.external;

  BMGeometrySnapshot *snapshot = snapshot_create(bm);
  ASSERT_NE(snapshot, nullptr);
  BMesh *restored = snapshot_restore(snapshot);
  ASSERT_NE(restored, nullptr);
  ASSERT_NE(restored->ldata.external, nullptr);
  EXPECT_NE(restored->ldata.external, source_external);
  EXPECT_STREQ(restored->ldata.external->filepath, "//snapshot-displacements.btx");
  EXPECT_EQ(restored->ldata.layers[0].flag, CD_FLAG_EXTERNAL | CD_FLAG_IN_MEMORY);
  EXPECT_EQ(bm->ldata.external, source_external);
  EXPECT_STREQ(source_external->filepath, "//snapshot-displacements.btx");
  EXPECT_EQ(bm->ldata.layers[0].flag, CD_FLAG_EXTERNAL | CD_FLAG_IN_MEMORY);
  EXPECT_TRUE(BM_geometry_snapshot_equals(snapshot, bm));
  EXPECT_TRUE(BM_geometry_snapshot_equals(snapshot, restored));

  source_external->filepath[2] = 'X';
  EXPECT_FALSE(BM_geometry_snapshot_equals(snapshot, bm));
  EXPECT_STREQ(restored->ldata.external->filepath, "//snapshot-displacements.btx");
  EXPECT_TRUE(BM_geometry_snapshot_equals(snapshot, restored));
  source_external->filepath[2] = 's';
  EXPECT_TRUE(BM_geometry_snapshot_equals(snapshot, bm));
}

TEST_F(BMeshGeometrySnapshotTest, ArrayOwnedCustomDataDescriptorIsRejected)
{
  BMesh *bm = mesh_create();
  BM_data_layer_add(bm, &bm->vdata, CD_PROP_FLOAT);
  triangle_add(bm);
  BMGeometrySnapshot *snapshot = snapshot_create(bm);
  ASSERT_NE(snapshot, nullptr);
  ASSERT_EQ(bm->vdata.layers[0].data, nullptr);
  float *array_data = MEM_new_array_zeroed<float>(bm->totvert, __func__);
  bm->vdata.layers[0].data = array_data;
  const char *error = nullptr;
  BMGeometrySnapshot *invalid = snapshot_create(bm, &error);
  const bool equal = BM_geometry_snapshot_equals(snapshot, bm);
  /* BMesh attributes live in per-element blocks. Undo the synthetic array ownership before any
   * assertion can leave the fixture, so ordinary CustomData cleanup sees a valid descriptor. */
  bm->vdata.layers[0].data = nullptr;
  MEM_delete(array_data);
  EXPECT_EQ(invalid, nullptr);
  EXPECT_NE(error, nullptr);
  EXPECT_FALSE(equal);
  EXPECT_TRUE(BM_geometry_snapshot_equals(snapshot, bm));
}

TEST_F(BMeshGeometrySnapshotTest, MemoryIncludesReservedPoolsAndDynamicPayloads)
{
  EXPECT_EQ(BLI_mempool_allocated_bytes(nullptr), 0);
  BMesh *bm = mesh_create();
  BM_data_layer_add(bm, &bm->vdata, CD_MDEFORMVERT);
  BM_data_layer_add(bm, &bm->ldata, CD_MDISPS);
  BM_data_layer_add(bm, &bm->ldata, CD_GRID_PAINT_MASK);
  BMLoop *loop = BM_FACE_FIRST_LOOP(triangle_add(bm));
  BMGeometrySnapshot *empty = snapshot_create(bm);
  ASSERT_NE(empty, nullptr);
  BMesh *empty_copy = snapshot_restore(empty);
  ASSERT_NE(empty_copy, nullptr);
  const size_t reserved = mesh_pool_bytes(empty_copy);
  const size_t live_elements = sizeof(BMVert) * 3 + sizeof(BMEdge) * 3 + sizeof(BMLoop) * 3 +
                               sizeof(BMFace);
  EXPECT_GT(reserved, live_elements);
  EXPECT_GE(BM_geometry_snapshot_memory_size(empty), reserved);
  /* Reading allocation size must not change pool occupancy or capacity. */
  const int verts_before = BLI_mempool_len(empty_copy->vpool);
  EXPECT_EQ(mesh_pool_bytes(empty_copy), reserved);
  EXPECT_EQ(BLI_mempool_len(empty_copy->vpool), verts_before);

  MDeformVert &deform = layer_data<MDeformVert>(loop->v,
                                                CustomData_get_offset(&bm->vdata, CD_MDEFORMVERT));
  deform.totweight = 512;
  deform.dw = MEM_new_array_zeroed<MDeformWeight>(512, __func__);
  for (int i = 0; i < deform.totweight; i++) {
    deform.dw[i] = {uint(i), 0.5f};
  }
  MDisps &disp = layer_data<MDisps>(loop, CustomData_get_offset(&bm->ldata, CD_MDISPS));
  disp.totdisp = 289;
  disp.level = 5;
  disp.disps = MEM_new_array_zeroed<float[3]>(289, __func__);
  disp.hidden = BLI_BITMAP_NEW(289, __func__);
  GridPaintMask &grid = layer_data<GridPaintMask>(
      loop, CustomData_get_offset(&bm->ldata, CD_GRID_PAINT_MASK));
  grid.level = 5;
  grid.data = MEM_new_array_zeroed<float>(289, __func__);
  BMGeometrySnapshot *populated = snapshot_create(bm);
  ASSERT_NE(populated, nullptr);
  const size_t payload_bytes = MEM_allocN_len(deform.dw) + MEM_allocN_len(disp.disps) +
                               MEM_allocN_len(disp.hidden) + MEM_allocN_len(grid.data);
  /* Both snapshots have identical geometry, layer layouts, pool capacity and metadata. The only
   * additional owned allocations are these four deep payloads, counted exactly once. */
  EXPECT_EQ(BM_geometry_snapshot_memory_size(populated),
            BM_geometry_snapshot_memory_size(empty) + payload_bytes);
}

TEST_F(BMeshGeometrySnapshotTest, NonIterablePoolAccountsForGrowthAndRetainedChunks)
{
  std::unique_ptr<BLI_mempool, decltype(&BLI_mempool_destroy)> pool(
      BLI_mempool_create(64, 0, 4, BLI_MEMPOOL_NOP), BLI_mempool_destroy);
  const size_t pool_bytes = MEM_allocN_len(pool.get());
  EXPECT_EQ(BLI_mempool_allocated_bytes(pool.get()), pool_bytes);
  Vector<void *> elements;
  elements.append(BLI_mempool_alloc(pool.get()));
  const size_t one_chunk_bytes = BLI_mempool_allocated_bytes(pool.get());
  ASSERT_GT(one_chunk_bytes, pool_bytes);

  /* Find the next chunk boundary through allocations. The allocator adjusts requested chunk
   * capacity for alignment/debugging; the test must not duplicate those private rounding rules. */
  while (elements.size() < 128 && BLI_mempool_allocated_bytes(pool.get()) == one_chunk_bytes) {
    elements.append(BLI_mempool_alloc(pool.get()));
  }
  const size_t two_chunk_bytes = BLI_mempool_allocated_bytes(pool.get());
  ASSERT_GT(two_chunk_bytes, one_chunk_bytes);
  EXPECT_EQ(two_chunk_bytes, pool_bytes + 2 * (one_chunk_bytes - pool_bytes));
  const int64_t boundary_count = elements.size();
  EXPECT_EQ(BLI_mempool_len(pool.get()), boundary_count);

  /* The second chunk is now unused, but remains reserved while the first is in use. A live-only
   * estimate would undercount here; this query also supports pools that cannot be iterated. */
  BLI_mempool_free(pool.get(), elements.pop_last());
  EXPECT_EQ(BLI_mempool_allocated_bytes(pool.get()), two_chunk_bytes);
  while (elements.size() > 1) {
    BLI_mempool_free(pool.get(), elements.pop_last());
  }
  EXPECT_EQ(BLI_mempool_len(pool.get()), 1);
  EXPECT_EQ(BLI_mempool_allocated_bytes(pool.get()), two_chunk_bytes);
  BLI_mempool_free(pool.get(), elements.pop_last());
  EXPECT_EQ(BLI_mempool_len(pool.get()), 0);
  EXPECT_EQ(BLI_mempool_allocated_bytes(pool.get()), one_chunk_bytes);

  /* Reusing and regrowing the emptied pool must count current allocations, not historical ones. */
  for (int64_t i = 0; i < boundary_count; i++) {
    elements.append(BLI_mempool_alloc(pool.get()));
  }
  EXPECT_EQ(BLI_mempool_len(pool.get()), boundary_count);
  EXPECT_EQ(BLI_mempool_allocated_bytes(pool.get()), two_chunk_bytes);
  while (!elements.is_empty()) {
    BLI_mempool_free(pool.get(), elements.pop_last());
  }
  EXPECT_EQ(BLI_mempool_allocated_bytes(pool.get()), one_chunk_bytes);
}

TEST_F(BMeshGeometrySnapshotTest, ReusedPoolSlotsAndFaceOrderRestoreWithRetainedLogHistory)
{
  BMesh *bm = mesh_create();
  BMLog *log = BM_log_create(bm);
  logs_.append(log);
  BMLogEntry *topology = BM_log_entry_add(log);
  entries_.append(topology);
  BMFace *first = triangle_add(bm);
  BMFace *removed = triangle_add(bm, 10.0f);
  triangle_add(bm, 20.0f);
  BM_log_all_added(bm, log);
  std::array<BMVert *, 3> removed_verts;
  BMLoop *loop = BM_FACE_FIRST_LOOP(removed);
  for (int i = 0; i < 3; i++, loop = loop->next) {
    removed_verts[i] = loop->v;
  }
  BM_log_face_removed(log, removed);
  BM_face_kill(bm, removed);
  for (BMVert *vert : removed_verts) {
    BM_log_vert_removed(log, vert, -1);
    BM_vert_kill(bm, vert);
  }
  BMFace *replacement = triangle_add(bm, 30.0f);
  loop = BM_FACE_FIRST_LOOP(replacement);
  for (int i = 0; i < 3; i++, loop = loop->next) {
    BM_log_vert_added(log, loop->v, -1);
  }
  BM_log_face_added(log, replacement);
  BMLogEntry *move = BM_log_entry_add(log);
  entries_.append(move);
  BMVert *moved_vert = BM_FACE_FIRST_LOOP(first)->v;
  BM_log_vert_before_modified(log, moved_vert, -1);
  const float original_x = moved_vert->co[0];
  moved_vert->co[0] += 0.5f;
  BMLogEntry *future = BM_log_entry_add(log);
  entries_.append(future);
  BM_log_vert_before_modified(log, moved_vert, -1);
  moved_vert->co[0] += 2.0f;
  BM_log_undo(bm, log);

  BMGeometrySnapshot *geometry = snapshot_create(bm);
  ASSERT_NE(geometry, nullptr);
  BMLogSnapshot *history = BM_log_snapshot_create(bm, log);
  ASSERT_NE(history, nullptr);
  log_snapshots_.append(history);
  BMesh *restored = snapshot_restore(geometry);
  ASSERT_NE(restored, nullptr);
  EXPECT_TRUE(BM_geometry_snapshot_equals(geometry, restored));
  const char *error = nullptr;
  BMLogSnapshotRestore *prepared = BM_log_snapshot_restore_prepare(
      restored, history, {topology, move, future}, log, &error);
  ASSERT_NE(prepared, nullptr) << (error ? error : "No restoration diagnostic");
  log_restores_.append(prepared);
  logs_.remove_first_occurrence_and_reorder(log);
  BM_log_free(log);
  BMLog *restored_log = BM_log_snapshot_restore_commit(prepared);
  log_restores_.remove_first_occurrence_and_reorder(prepared);
  logs_.append(restored_log);
  BMVert *restored_vert = BM_FACE_FIRST_LOOP(first_face(restored))->v;
  EXPECT_EQ(restored_vert->co[0], original_x + 0.5f);
  BM_log_redo(restored, restored_log);
  EXPECT_EQ(restored_vert->co[0], original_x + 2.5f);
  BM_log_undo(restored, restored_log);
  EXPECT_TRUE(BM_geometry_snapshot_equals(geometry, restored));
  BM_log_undo(restored, restored_log);
  EXPECT_EQ(restored_vert->co[0], original_x);
  BM_log_undo(restored, restored_log);
  EXPECT_EQ(restored->totvert, 0);
  EXPECT_EQ(restored->totface, 0);
  BM_log_redo(restored, restored_log);
  EXPECT_EQ(restored->totvert, 9);
  EXPECT_EQ(restored->totface, 3);
}

}  // namespace blender
