/* SPDX-FileCopyrightText: 2025 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */
#include "mesh_brush_common.hh"

#include "BKE_gtest_base.hh"
#include "BKE_lib_id.hh"

#include "BLI_array_utils.hh"
#include "BLI_map.hh"

#include "DNA_mesh_types.h"

#include "GEO_mesh_primitive_cuboid.hh"
#include "GEO_mesh_primitive_grid.hh"

#include "sculpt_boundary.hh"

#include "testing/testing.h"

namespace blender::ed::sculpt_paint::tests {

class MeshBrushCommonTests : public bke::BlenderGTestBase {
 public:
  Mesh *mesh;

  void TearDown() override
  {
    if (mesh) {
      BKE_id_free(nullptr, mesh);
    }
  }
};

TEST_F(MeshBrushCommonTests, calc_vert_neighbors_interior__cube)
{
  mesh = geometry::create_cuboid_mesh(float3(1.0, 1.0, 1.0), 2, 2, 2);

  const OffsetIndices faces = mesh->faces();
  const Span<int> corner_verts = mesh->corner_verts();
  const GroupedSpan<int> vert_to_face_map = mesh->vert_to_face_map();
  const Vector<bool> hide_poly(mesh->faces_num, false);

  Array<int> verts(8);
  array_utils::fill_index_range(verts.as_mutable_span(), 0);

  SculptBoundaryInfoCache boundary_info_cache = boundary::create_boundary_info(*mesh);

  Vector<int> offset_data;
  Vector<int> data;
  const GroupedSpan<int> result = calc_vert_neighbors_interior(faces,
                                                               corner_verts,
                                                               vert_to_face_map,
                                                               boundary_info_cache.verts,
                                                               boundary_info_cache.edges,
                                                               hide_poly,
                                                               verts,
                                                               offset_data,
                                                               data);

  /* Each of the cube's 8 vertices should have 3 neighbor elements */
  Map<int, int> expected_counts = {{3, 8}};

  ASSERT_EQ(result.size(), 8);
  Map<int, int> calculated_counts;
  for (const int i : result.index_range()) {
    calculated_counts.add_or_modify(
        result[i].size(), [](int *value) { *value = 1; }, [](int *value) { (*value)++; });
  }
  for (const int key : expected_counts.keys()) {
    ASSERT_EQ(calculated_counts.lookup_default(key, -1), expected_counts.lookup_default(key, -2));
  }
}

TEST_F(MeshBrushCommonTests, calc_vert_neighbors_interior__1D_strip)
{
  mesh = geometry::create_grid_mesh(3, 2, 1.0, 1.0, {});

  const OffsetIndices faces = mesh->faces();
  const Span<int> corner_verts = mesh->corner_verts();
  const GroupedSpan<int> vert_to_face_map = mesh->vert_to_face_map();
  const Vector<bool> hide_poly(mesh->faces_num, false);

  Array<int> verts(6);
  array_utils::fill_index_range(verts.as_mutable_span(), 0);

  SculptBoundaryInfoCache boundary_info_cache = boundary::create_boundary_info(*mesh);

  Vector<int> offset_data;
  Vector<int> data;
  const GroupedSpan<int> result = calc_vert_neighbors_interior(faces,
                                                               corner_verts,
                                                               vert_to_face_map,
                                                               boundary_info_cache.verts,
                                                               boundary_info_cache.edges,
                                                               hide_poly,
                                                               verts,
                                                               offset_data,
                                                               data);
  /* The 4 corner elements should each have no neighbor. The remaining 2 elements should have 2
   * neighbors */
  Map<int, int> expected_counts = {{0, 4}, {2, 2}};

  ASSERT_EQ(result.size(), 6);
  Map<int, int> calculated_counts;
  for (const int i : result.index_range()) {
    calculated_counts.add_or_modify(
        result[i].size(), [](int *value) { *value = 1; }, [](int *value) { (*value)++; });
  }
  for (const int key : expected_counts.keys()) {
    ASSERT_EQ(calculated_counts.lookup_default(key, -1), expected_counts.lookup_default(key, -2));
  }
}

/* Three 2x2 grids; the node holds the last and the first, in that order. One vertex of each is
 * hidden. */
class ScatterVisibleGridsTest : public testing::Test {
 public:
  CCGKey key = {};
  BitGroupVector<> grid_hidden{3, 4, false};
  Array<int> grids = {2, 0};
  Array<float> node_data = {10.0f, 11.0f, 12.0f, 13.0f, 20.0f, 21.0f, 22.0f, 23.0f};

  ScatterVisibleGridsTest()
  {
    key.grid_size = 2;
    key.grid_area = 4;
  }
};

TEST_F(ScatterVisibleGridsTest, each_visible_vertex_is_written_at_its_own_index)
{
  grid_hidden[2][1].set();
  grid_hidden[0][3].set();
  Array<float> data(12, -1.0f);

  scatter_data_visible_grids<float>(key, grid_hidden, node_data, grids, data);

  /* Grid 0 takes the node's second block, grid 1 is another node's, grid 2 takes the first
   * block; the two hidden vertices and grid 1 keep what they held. */
  const Array<float> expected = {
      20.0f, 21.0f, 22.0f, -1.0f, -1.0f, -1.0f, -1.0f, -1.0f, 10.0f, -1.0f, 12.0f, 13.0f};
  EXPECT_EQ_SPAN<float>(expected, data);
}

TEST_F(ScatterVisibleGridsTest, visible_vertices_get_what_they_get_with_nothing_hidden)
{
  Array<float> all_visible(12, -1.0f);
  scatter_data_visible_grids<float>(key, grid_hidden, node_data, grids, all_visible);
  const Array<float> expected = {
      20.0f, 21.0f, 22.0f, 23.0f, -1.0f, -1.0f, -1.0f, -1.0f, 10.0f, 11.0f, 12.0f, 13.0f};
  EXPECT_EQ_SPAN<float>(expected, all_visible);

  /* Hiding a vertex changes that vertex only. */
  grid_hidden[2][1].set();
  Array<float> one_hidden(12, -1.0f);
  scatter_data_visible_grids<float>(key, grid_hidden, node_data, grids, one_hidden);
  for (const int i : all_visible.index_range()) {
    const bool hidden = i == 2 * key.grid_area + 1;
    EXPECT_EQ(one_hidden[i], hidden ? -1.0f : all_visible[i]) << "vertex " << i;
  }
}

}  // namespace blender::ed::sculpt_paint::tests
