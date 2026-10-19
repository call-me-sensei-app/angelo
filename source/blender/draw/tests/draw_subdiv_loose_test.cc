/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: Apache-2.0 */

#include "BLI_array.hh"
#include "BLI_index_mask.hh"

#include "GPU_vertex_buffer.hh"

#include "draw_subdivision.hh"
#include "draw_testing.hh"
#include "mesh_extractors/extract_mesh.hh"
#include "mesh_extractors/extract_mesh_vbo_update.hh"

namespace blender::draw {

template<typename T> static Array<T> read_vertices(const gpu::VertBuf &vbo)
{
  /* The Metal backend's vertex read also copies its minimum 128-byte allocation. */
  Array<T> data(std::max<int>(vbo.vertex_len, (128 + sizeof(T) - 1) / sizeof(T)));
  GPU_vertbuf_use(const_cast<gpu::VertBuf *>(&vbo));
  GPU_vertbuf_read(&vbo, data.data());
  return data;
}

static void test_subdiv_loose_upload_range()
{
  const GPUVertFormat format = GPU_vertformat_from_attribute("value", gpu::VertAttrType::SINT_32);
  constexpr int size = 32781;
  constexpr int start = 7;
  constexpr int end = size - 11;
  gpu::VertBufPtr vbo(GPU_vertbuf_create_on_device(format, size));
  Array<int> original(size, -91);
  GPU_vertbuf_use(vbo.get());
  GPU_vertbuf_update_sub(vbo.get(), 0, original.as_span().size_in_bytes(), original.data());

  extract_mesh_vbo_update<int>(
      *vbo, start, end - start, [](IndexRange range, MutableSpan<int> dst) {
        for (const int i : dst.index_range()) {
          dst[i] = range.start() + i;
        }
      });
  bool called = false;
  extract_mesh_vbo_update<int>(*vbo, 0, 0, [&](IndexRange, MutableSpan<int>) { called = true; });
  EXPECT_FALSE(called);
  const Array<int> actual = read_vertices<int>(*vbo);
  for (const int i : IndexRange(size)) {
    EXPECT_EQ(actual[i], (i >= start && i < end) ? i - start : -91) << i;
  }
}
DRAW_TEST(subdiv_loose_upload_range)

static void test_subdiv_loose_upload_vector_range()
{
  const GPUVertFormat format = GPU_vertformat_from_attribute("value",
                                                             gpu::VertAttrType::SFLOAT_32_32_32);
  /* A float3 does not divide the 64 KiB chunk evenly. Exercise either side of that boundary,
   * preserving a face-like prefix and a suffix, then overwrite part of the uploaded range. */
  for (const int count : {1, 5460, 5461, 5462, 10925}) {
    constexpr int start = 5;
    const int size = start + count + 7;
    gpu::VertBufPtr vbo(GPU_vertbuf_create_on_device(format, size));
    Array<float3> original(size, float3(-91));
    GPU_vertbuf_use(vbo.get());
    GPU_vertbuf_update_sub(vbo.get(), 0, original.as_span().size_in_bytes(), original.data());
    extract_mesh_vbo_update<float3>(
        *vbo, start, count, [](IndexRange range, MutableSpan<float3> dst) {
          for (const int i : dst.index_range()) {
            const float value = range.start() + i;
            dst[i] = float3(value, -value, value + 0.5f);
          }
        });
    const int overwritten = start + count / 2;
    extract_mesh_vbo_update<float3>(*vbo, overwritten, 1, [](IndexRange, MutableSpan<float3> dst) {
      dst.fill(float3(11, 22, 33));
    });
    bool called = false;
    extract_mesh_vbo_update<float3>(
        *vbo, size, 0, [&](IndexRange, MutableSpan<float3>) { called = true; });
    EXPECT_FALSE(called);
    const Array<float3> actual = read_vertices<float3>(*vbo);
    for (const int i : IndexRange(size)) {
      const float value = i - start;
      const float3 expected = (i >= start && i < start + count) ?
                                  float3(value, -value, value + 0.5f) :
                                  float3(-91);
      EXPECT_EQ(actual[i], i == overwritten ? float3(11, 22, 33) : expected) << i;
    }
  }
}
DRAW_TEST(subdiv_loose_upload_vector_range)

static void test_subdiv_loose_empty()
{
  const MeshRenderData mr{};
  DRWSubdivCache cache{};
  cache.resolution = 3;
  gpu::VertBufPtr pos = extract_positions_subdiv(cache, mr, nullptr);
  gpu::VertBufPtr normals = extract_normals_subdiv(mr, cache, *pos);
  gpu::VertBufPtr flags = extract_paint_overlay_flags_subdiv(mr, cache);
  gpu::VertBufPtr factors = extract_edge_factor_subdiv(cache, mr, *pos);
  EXPECT_EQ(pos->vertex_len, 0);
  EXPECT_EQ(normals->vertex_len, 0);
  EXPECT_EQ(flags->vertex_len, 0);
  EXPECT_EQ(factors->vertex_len, 0);
}
DRAW_TEST(subdiv_loose_empty)

static void check_loose_geometry(const int resolution, const int edge_count, const int vert_count)
{
  MeshRenderData mr{};
  IndexMaskMemory memory;
  Array<int> loose_verts(vert_count);
  Array<float3> positions(vert_count * 2 + 1, float3(-100));
  for (const int i : loose_verts.index_range()) {
    /* Non-contiguous source vertices catch confusion between source and destination indices. */
    loose_verts[i] = i * 2 + 1;
    positions[loose_verts[i]] = float3(i, -i, 42);
  }
  mr.vert_positions = positions;
  mr.loose_verts = IndexMask::from_indices(loose_verts.as_span(), memory);
  mr.loose_edges = IndexMask(edge_count);

  DRWSubdivCache cache{};
  cache.resolution = resolution;
  cache.loose_edge_positions.reinitialize(edge_count * resolution);
  for (const int edge : IndexRange(edge_count)) {
    for (const int point : IndexRange(resolution)) {
      cache.loose_edge_positions[edge * resolution + point] = float3(edge, point, edge + point);
    }
  }

  const int edge_vertices = edge_count * (resolution - 1) * 2;
  const int size = edge_vertices + vert_count;
  gpu::VertBufPtr pos = extract_positions_subdiv(cache, mr, nullptr);
  EXPECT_EQ(pos->vertex_len, size);
  const Array<float3> actual = read_vertices<float3>(*pos);
  int cursor = 0;
  for (const int edge : IndexRange(edge_count)) {
    for (const int segment : IndexRange(resolution - 1)) {
      EXPECT_EQ(actual[cursor++], float3(edge, segment, edge + segment));
      EXPECT_EQ(actual[cursor++], float3(edge, segment + 1, edge + segment + 1));
    }
  }
  for (const int vert : IndexRange(vert_count)) {
    EXPECT_EQ(actual[cursor++], float3(vert, -vert, 42));
  }

  gpu::VertBufPtr normals = extract_normals_subdiv(mr, cache, *pos);
  EXPECT_EQ(normals->vertex_len, size);
  const Array<float3> actual_normals = read_vertices<float3>(*normals);
  gpu::VertBufPtr flags = extract_paint_overlay_flags_subdiv(mr, cache);
  EXPECT_EQ(flags->vertex_len, size);
  const Array<int> actual_flags = read_vertices<int>(*flags);
  for (const int i : IndexRange(size)) {
    EXPECT_EQ(actual_normals[i], float3(0));
    EXPECT_EQ(actual_flags[i], 0);
  }
  if (edge_count != 0) {
    gpu::VertBufPtr factors = extract_edge_factor_subdiv(cache, mr, *pos);
    EXPECT_EQ(factors->vertex_len, edge_vertices);
    const Array<float> actual_factors = read_vertices<float>(*factors);
    for (const int i : IndexRange(edge_vertices)) {
      EXPECT_EQ(actual_factors[i], 1.0f);
    }
  }
}

static void test_subdiv_loose_vertices()
{
  check_loose_geometry(3, 0, 7);
}
DRAW_TEST(subdiv_loose_vertices)

static void test_subdiv_loose_edges()
{
  for (const int resolution : {2, 3, 5, 33}) {
    check_loose_geometry(resolution, 3, 0);
  }
}
DRAW_TEST(subdiv_loose_edges)

static void test_subdiv_loose_mixed()
{
  for (const int resolution : {2, 3, 5, 33}) {
    check_loose_geometry(resolution, 2, 7);
  }
}
DRAW_TEST(subdiv_loose_mixed)

static void test_subdiv_loose_large()
{
  /* Cross several upload chunk boundaries, including the edge/vertex boundary. */
  check_loose_geometry(5, 4097, 20001);
}
DRAW_TEST(subdiv_loose_large)

}  // namespace blender::draw
