/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include <array>
#include <utility>

#include "testing/testing.h"

#include "DNA_mesh_types.h"
#include "DNA_object_types.h"

#include "BLI_array.hh"
#include "BLI_index_mask.hh"
#include "BLI_vector.hh"

#include "BKE_gtest_base.hh"
#include "BKE_lib_id.hh"
#include "BKE_mesh.hh"
#include "BKE_object_types.hh"
#include "BKE_paint.hh"
#include "BKE_paint_bvh.hh"
#include "BKE_subdiv_ccg.hh"

#include "bmesh.hh"

namespace blender::bke::pbvh::tests {

class PBVHGridNormalUpdateTest : public BlenderGTestBase {
 protected:
  Vector<Mesh *> meshes_;
  Vector<BMesh *> bmeshes_;
  const std::array<int, 8> first_node_grids_ = {0, 1, 2, 3, 4, 5, 6, 7};
  const std::array<int, 4> second_node_grids_ = {8, 9, 10, 11};

  void TearDown() override
  {
    for (BMesh *bm : bmeshes_) {
      BM_mesh_free(bm);
    }
    for (Mesh *mesh : meshes_) {
      BKE_id_free(nullptr, mesh);
    }
    BlenderGTestBase::TearDown();
  }

  Mesh *mesh_create(const int faces_num)
  {
    Mesh *mesh = BKE_mesh_new_nomain(faces_num * 4, faces_num * 4, faces_num, faces_num * 4);
    meshes_.append(mesh);
    MutableSpan<float3> positions = mesh->vert_positions_for_write();
    MutableSpan<int2> edges = mesh->edges_for_write();
    MutableSpan<int> corner_verts = mesh->corner_verts_for_write();
    MutableSpan<int> corner_edges = mesh->corner_edges_for_write();
    MutableSpan<int> face_offsets = mesh->face_offsets_for_write();
    for (const int face : IndexRange(faces_num)) {
      const int start = face * 4;
      positions[start] = float3(face * 10, 0, 0);
      positions[start + 1] = float3(face * 10 + 1, 0, 0);
      positions[start + 2] = float3(face * 10 + 1, 1, 0);
      positions[start + 3] = float3(face * 10, 1, 0);
      for (const int corner : IndexRange(4)) {
        edges[start + corner] = int2(start + corner, start + (corner + 1) % 4);
        corner_verts[start + corner] = start + corner;
        corner_edges[start + corner] = start + corner;
      }
    }
    for (const int i : face_offsets.index_range()) {
      face_offsets[i] = i * 4;
    }
    return mesh;
  }

  Mesh *grids_initialize(SubdivCCG &subdiv_ccg, const bool with_normals)
  {
    Mesh *mesh = mesh_create(3);
    subdiv_ccg.level = 1;
    subdiv_ccg.grid_size = 2;
    subdiv_ccg.grid_area = 4;
    subdiv_ccg.grids_num = 12;
    subdiv_ccg.faces = mesh->faces();
    subdiv_ccg.grid_to_face_map = mesh->corner_to_face_map();
    subdiv_ccg.positions.reinitialize(48);
    subdiv_ccg.masks.reinitialize(48);
    if (with_normals) {
      subdiv_ccg.normals.reinitialize(48);
    }
    for (const int i : subdiv_ccg.positions.index_range()) {
      /* Distinct, unstitched corner samples detect accidental normalization or averaging during
       * capture. This CCG deliberately has no evaluator or OpenSubdiv topology. */
      subdiv_ccg.positions[i] = float3(i, i % 5, i * 0.125f);
      subdiv_ccg.masks[i] = (i % 8) * 0.125f;
      if (with_normals) {
        subdiv_ccg.normals[i] = float3(i + 0.25f, 0.5f, -1.0f);
      }
    }
    subdiv_ccg.grid_hidden = BitGroupVector<>(12, 4, false);
    subdiv_ccg.grid_hidden[7][2].set();
    subdiv_ccg.dirty.coords = true;
    subdiv_ccg.dirty.hidden = true;
    return mesh;
  }

  Tree controlled_tree(const SubdivCCG &subdiv_ccg)
  {
    /* Start with the real empty Grids constructor, then provide two controlled leaf partitions.
     * Faces 0 and 1 share one node; face 2 belongs to the other. No face spans multiple nodes.
     * This tests pending-work semantics, not the builder's partition algorithm. */
    Tree tree = Tree::from_grids(*mesh_create(0), subdiv_ccg);
    Vector<GridsNode> &nodes = std::get<Vector<GridsNode>>(tree.nodes_);
    nodes.resize(2);
    nodes[0].prim_indices_ = first_node_grids_;
    nodes[1].prim_indices_ = second_node_grids_;
    for (GridsNode &node : nodes) {
      node.flag_ = Node::Leaf;
    }
    return tree;
  }

  static void update_normals_without_evaluator(Tree &tree, SubdivCCG &subdiv_ccg)
  {
    /* The real consumer returns before stitching when normals are absent. This exercises pending
     * work consumption and subsequent invalidation, not normal values or OpenSubdiv boundaries. */
    ASSERT_TRUE(subdiv_ccg.normals.is_empty());
    SculptSession session;
    session.subdiv_ccg = &subdiv_ccg;
    ObjectRuntime runtime;
    runtime.sculpt_session = &session;
    Object object_orig;
    object_orig.runtime = &runtime;
    Object object_eval;
    tree.update_normals(object_orig, object_eval);
  }
};

TEST_F(PBVHGridNormalUpdateTest, CaptureIsReadOnlyAndDeduplicatesCornersOfDirtyFaces)
{
  SubdivCCG subdiv_ccg;
  grids_initialize(subdiv_ccg, true);
  Tree tree = controlled_tree(subdiv_ccg);
  tree.tag_positions_changed(IndexRange(0, 1));
  const Array<float3> positions(subdiv_ccg.positions);
  const Array<float3> normals(subdiv_ccg.normals);
  const Array<float> masks(subdiv_ccg.masks);
  const Array<int> grid_to_face(subdiv_ccg.grid_to_face_map);
  const float3 *positions_data = subdiv_ccg.positions.data();
  const float3 *normals_data = subdiv_ccg.normals.data();
  const float *masks_data = subdiv_ccg.masks.data();
  const Span<GridsNode> nodes = tree.nodes<GridsNode>();
  const int *first_node_data = nodes[0].grids().data();
  const int *second_node_data = nodes[1].grids().data();

  const Tree &read_only_tree = tree;
  for (int attempt = 0; attempt < 3; attempt++) {
    const Array<int, 0> faces = read_only_tree.capture_grid_normal_update_faces(subdiv_ccg);
    EXPECT_EQ_SPAN<int>({0, 1}, faces);
    EXPECT_EQ(subdiv_ccg.positions.data(), positions_data);
    EXPECT_EQ(subdiv_ccg.normals.data(), normals_data);
    EXPECT_EQ(subdiv_ccg.masks.data(), masks_data);
    EXPECT_EQ_SPAN<float3>(positions, subdiv_ccg.positions);
    EXPECT_EQ_SPAN<float3>(normals, subdiv_ccg.normals);
    EXPECT_EQ_SPAN<float>(masks, subdiv_ccg.masks);
    EXPECT_EQ_SPAN<int>(grid_to_face, subdiv_ccg.grid_to_face_map);
    for (const int grid : IndexRange(12)) {
      for (const int point : IndexRange(4)) {
        EXPECT_EQ(bool(subdiv_ccg.grid_hidden[grid][point]), grid == 7 && point == 2);
      }
    }
    EXPECT_TRUE(subdiv_ccg.dirty.coords);
    EXPECT_TRUE(subdiv_ccg.dirty.hidden);
    EXPECT_EQ(subdiv_ccg.subdiv, nullptr);
    EXPECT_EQ(nodes[0].grids().data(), first_node_data);
    EXPECT_EQ(nodes[1].grids().data(), second_node_data);
    EXPECT_EQ_SPAN<int>(first_node_grids_, nodes[0].grids());
    EXPECT_EQ_SPAN<int>(second_node_grids_, nodes[1].grids());
    EXPECT_EQ(nodes[0].flag_, Node::Leaf);
    EXPECT_EQ(nodes[1].flag_, Node::Leaf);
  }
}

TEST_F(PBVHGridNormalUpdateTest, RestoredFaceSubsetDoesNotExpandToItsCurrentNode)
{
  SubdivCCG subdiv_ccg;
  grids_initialize(subdiv_ccg, true);
  Tree tree = controlled_tree(subdiv_ccg);
  tree.tag_positions_changed(IndexRange(tree.nodes_num()));
  Array<int, 0> saved_faces = {1};
  tree.restore_grid_normal_update_faces(std::move(saved_faces));

  /* Face 0 shares the first leaf with face 1, but was not part of the captured pending work. */
  EXPECT_EQ_SPAN<int>({1}, tree.capture_grid_normal_update_faces(subdiv_ccg));
  EXPECT_EQ_SPAN<int>({1}, tree.capture_grid_normal_update_faces(subdiv_ccg));
  tree.tag_positions_changed(IndexRange(1, 1));
  EXPECT_EQ_SPAN<int>({1, 2}, tree.capture_grid_normal_update_faces(subdiv_ccg));
  /* A later artist edit adds both faces from the first leaf. Face 1 must still occur only once. */
  tree.tag_positions_changed(IndexRange(0, 1));
  EXPECT_EQ_SPAN<int>({0, 1, 2}, tree.capture_grid_normal_update_faces(subdiv_ccg));
}

TEST_F(PBVHGridNormalUpdateTest, OrdinaryUpdateConsumesPendingFacesAndLaterEditsCreateNewWork)
{
  SubdivCCG subdiv_ccg;
  grids_initialize(subdiv_ccg, false);
  Tree tree = controlled_tree(subdiv_ccg);
  tree.restore_grid_normal_update_faces(Array<int, 0>{1});
  tree.tag_positions_changed(IndexRange(0, 1));
  tree.tag_positions_changed(IndexRange(1, 1));
  EXPECT_EQ_SPAN<int>({0, 1, 2}, tree.capture_grid_normal_update_faces(subdiv_ccg));
  const Array<float3> positions(subdiv_ccg.positions);
  const Array<float> masks(subdiv_ccg.masks);

  update_normals_without_evaluator(tree, subdiv_ccg);
  EXPECT_TRUE(tree.capture_grid_normal_update_faces(subdiv_ccg).is_empty());
  update_normals_without_evaluator(tree, subdiv_ccg);
  EXPECT_TRUE(tree.capture_grid_normal_update_faces(subdiv_ccg).is_empty());

  tree.tag_positions_changed(IndexRange(1, 1));
  EXPECT_EQ_SPAN<int>({2}, tree.capture_grid_normal_update_faces(subdiv_ccg));
  update_normals_without_evaluator(tree, subdiv_ccg);
  EXPECT_TRUE(tree.capture_grid_normal_update_faces(subdiv_ccg).is_empty());
  EXPECT_EQ_SPAN<float3>(positions, subdiv_ccg.positions);
  EXPECT_EQ_SPAN<float>(masks, subdiv_ccg.masks);
  EXPECT_TRUE(subdiv_ccg.dirty.coords);
  EXPECT_TRUE(subdiv_ccg.dirty.hidden);
}

TEST_F(PBVHGridNormalUpdateTest, PendingFacesAreConsumedWithoutAnyDirtyNodes)
{
  SubdivCCG subdiv_ccg;
  grids_initialize(subdiv_ccg, false);
  Tree tree = controlled_tree(subdiv_ccg);
  tree.restore_grid_normal_update_faces(Array<int, 0>{1});
  EXPECT_EQ_SPAN<int>({1}, tree.capture_grid_normal_update_faces(subdiv_ccg));
  update_normals_without_evaluator(tree, subdiv_ccg);
  EXPECT_TRUE(tree.capture_grid_normal_update_faces(subdiv_ccg).is_empty());
  tree.tag_positions_changed(IndexRange(1, 1));
  EXPECT_EQ_SPAN<int>({2}, tree.capture_grid_normal_update_faces(subdiv_ccg));
}

TEST_F(PBVHGridNormalUpdateTest, EmptyRestoreClearsPreviouslyTaggedNormalWork)
{
  SubdivCCG subdiv_ccg;
  grids_initialize(subdiv_ccg, false);
  Tree tree = controlled_tree(subdiv_ccg);
  tree.tag_positions_changed(IndexRange(tree.nodes_num()));
  EXPECT_EQ_SPAN<int>({0, 1, 2}, tree.capture_grid_normal_update_faces(subdiv_ccg));
  tree.restore_grid_normal_update_faces(Array<int, 0>{});
  EXPECT_TRUE(tree.capture_grid_normal_update_faces(subdiv_ccg).is_empty());
  update_normals_without_evaluator(tree, subdiv_ccg);
  tree.tag_positions_changed(IndexRange(0, 1));
  EXPECT_EQ_SPAN<int>({0, 1}, tree.capture_grid_normal_update_faces(subdiv_ccg));
}

TEST_F(PBVHGridNormalUpdateTest, NonGridCaptureLeavesOrdinaryBMeshNormalWorkIntact)
{
  SubdivCCG unrelated_ccg;
  Tree mesh_tree = Tree::from_mesh(*mesh_create(3));
  EXPECT_EQ(mesh_tree.type(), Type::Mesh);
  EXPECT_TRUE(mesh_tree.capture_grid_normal_update_faces(unrelated_ccg).is_empty());

  BMeshCreateParams params{};
  BMesh *bm = BM_mesh_create(&bm_mesh_allocsize_default, &params);
  bmeshes_.append(bm);
  BM_data_layer_add_named(bm, &bm->vdata, CD_PROP_INT32, ".sculpt_dyntopo_node_id_vertex");
  BM_data_layer_add_named(bm, &bm->pdata, CD_PROP_INT32, ".sculpt_dyntopo_node_id_face");
  const float coordinates[3][3] = {{0, 0, 0}, {1, 0, 0}, {0, 1, 0}};
  BMVert *verts[3];
  for (int i = 0; i < 3; i++) {
    verts[i] = BM_vert_create(bm, coordinates[i], nullptr, BM_CREATE_NOP);
  }
  BMFace *face = BM_face_create_verts(bm, verts, 3, nullptr, BM_CREATE_NOP, true);
  Tree bmesh_tree = Tree::from_bmesh(*bm);
  ASSERT_EQ(bmesh_tree.type(), Type::BMesh);
  ASSERT_GT(bmesh_tree.nodes_num(), 0);
  face->no[0] = 7.0f;
  face->no[1] = 8.0f;
  face->no[2] = 9.0f;
  bmesh_tree.tag_positions_changed(IndexRange(bmesh_tree.nodes_num()));
  EXPECT_TRUE(bmesh_tree.capture_grid_normal_update_faces(unrelated_ccg).is_empty());
  EXPECT_EQ(float3(face->no), float3(7, 8, 9));

  Object object_orig;
  Object object_eval;
  bmesh_tree.update_normals(object_orig, object_eval);
  EXPECT_EQ(float3(face->no), float3(0, 0, 1));
  for (const BMVert *vert : verts) {
    EXPECT_EQ(float3(vert->no), float3(0, 0, 1));
  }
}

#ifdef WITH_OPENSUBDIV
TEST_F(PBVHGridNormalUpdateTest, RealBuilderRestoresExactFaceSubsetWithoutExpandingPartition)
{
  SubdivCCG subdiv_ccg;
  Mesh *mesh = grids_initialize(subdiv_ccg, true);
  const Array<float3> positions(subdiv_ccg.positions);
  const Array<float3> normals(subdiv_ccg.normals);
  const Array<float> masks(subdiv_ccg.masks);
  Tree tree = Tree::from_grids(*mesh, subdiv_ccg);
  ASSERT_EQ(tree.type(), Type::Grids);
  ASSERT_EQ(tree.nodes_num(), 1);
  ASSERT_EQ(tree.nodes<GridsNode>()[0].grids().size(), 12);
  EXPECT_EQ_SPAN<int>({0, 1, 2}, tree.capture_grid_normal_update_faces(subdiv_ccg));
  tree.restore_grid_normal_update_faces(Array<int, 0>{1});
  EXPECT_EQ_SPAN<int>({1}, tree.capture_grid_normal_update_faces(subdiv_ccg));
  EXPECT_EQ_SPAN<float3>(positions, subdiv_ccg.positions);
  EXPECT_EQ_SPAN<float3>(normals, subdiv_ccg.normals);
  EXPECT_EQ_SPAN<float>(masks, subdiv_ccg.masks);
  EXPECT_TRUE(subdiv_ccg.dirty.coords);
  EXPECT_TRUE(subdiv_ccg.dirty.hidden);
}

TEST_F(PBVHGridNormalUpdateTest, EmptyRestoreReplacesRealBuilderNormalWork)
{
  SubdivCCG subdiv_ccg;
  Mesh *mesh = grids_initialize(subdiv_ccg, false);
  Tree tree = Tree::from_grids(*mesh, subdiv_ccg);
  ASSERT_GT(tree.nodes_num(), 0);
  EXPECT_EQ_SPAN<int>({0, 1, 2}, tree.capture_grid_normal_update_faces(subdiv_ccg));
  tree.restore_grid_normal_update_faces(Array<int, 0>{});
  EXPECT_TRUE(tree.capture_grid_normal_update_faces(subdiv_ccg).is_empty());
  update_normals_without_evaluator(tree, subdiv_ccg);
  EXPECT_TRUE(tree.capture_grid_normal_update_faces(subdiv_ccg).is_empty());
}
#endif

}  // namespace blender::bke::pbvh::tests
