/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 *
 * The measuring kernels of `_bpy._angelo_reduce`, free of Python: nearest points on a triangle surface for
 * many query points (Hausdorff distances and attribute transfer), and triangle coverage of pixel centres
 * (silhouettes and UV overlap). Inputs are already validated by the module.
 */

#pragma once

#include <cstdint>

#include "BLI_array.hh"
#include "BLI_math_vector_types.hh"
#include "BLI_span.hh"

struct BVHTree;

namespace blender::angelo_reduce {

/** A triangle surface and its bounding-volume tree, built once and queried many times. */
class Surface {
 public:
  Array<float3> positions;
  Array<int3> tris;
  BVHTree *tree = nullptr;

  Surface(Span<float3> positions, Span<int3> tris);
  ~Surface();
  Surface(const Surface &) = delete;
  Surface &operator=(const Surface &) = delete;

  /**
   * For each point: the distance to the nearest point of the surface, that triangle's index and the
   * nearest point itself. Runs on all cores; the surface is only read. A surface with no triangles gives
   * infinite distances and index -1.
   */
  void nearest(Span<float3> points,
               MutableSpan<float> r_distance,
               MutableSpan<int> r_tri,
               MutableSpan<float3> r_point) const;
};

/**
 * One orthographic view: a pixel's x and y are `dot(row_x, p) + offset_x` and `dot(row_y, p) + offset_y`.
 * Pixel (i, j) is covered when its centre (i + 0.5, j + 0.5) lies inside a projected triangle.
 */
struct View {
  float3 row_x;
  float offset_x;
  float3 row_y;
  float offset_y;
};

/**
 * For each view, how many triangles cover each pixel centre of a `resolution` x `resolution` image (row
 * major, y up), saturating at 255. Shared edges follow the top-left rule, so two triangles meeting at an
 * edge never both cover a pixel centre on it; degenerate triangles cover nothing. Views run in parallel.
 */
void rasterize(Span<float3> positions,
               Span<int3> tris,
               Span<View> views,
               int resolution,
               MutableSpan<uint8_t> r_counts);

}  // namespace blender::angelo_reduce
