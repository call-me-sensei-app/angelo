/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 */

#include <algorithm>
#include <cmath>
#include <limits>

#include "BLI_kdopbvh.hh"
#include "BLI_math_geom.h"
#include "BLI_math_vector.h"
#include "BLI_math_vector.hh"
#include "BLI_task.hh"

#include "bpy_angelo_reduce_geometry.hh"

namespace blender::angelo_reduce {

Surface::Surface(Span<float3> positions_in, Span<int3> tris_in) : positions(positions_in), tris(tris_in)
{
  if (tris.is_empty()) {
    return;
  }
  /* The tree type and axis count `mathutils.bvhtree` uses for triangles. */
  tree = BLI_bvhtree_new(int(tris.size()), 0.0f, 4, 6);
  for (const int i : tris.index_range()) {
    const float co[3][3] = {{positions[tris[i].x].x, positions[tris[i].x].y, positions[tris[i].x].z},
                            {positions[tris[i].y].x, positions[tris[i].y].y, positions[tris[i].y].z},
                            {positions[tris[i].z].x, positions[tris[i].z].y, positions[tris[i].z].z}};
    BLI_bvhtree_insert(tree, i, &co[0][0], 3);
  }
  BLI_bvhtree_balance(tree);
}

Surface::~Surface()
{
  if (tree != nullptr) {
    BLI_bvhtree_free(tree);
  }
}

static void nearest_on_triangle(void *userdata, int index, const float co[3], BVHTreeNearest *nearest)
{
  const Surface &surface = *static_cast<const Surface *>(userdata);
  const int3 tri = surface.tris[index];
  float closest[3];
  closest_on_tri_to_point_v3(
      closest, co, surface.positions[tri.x], surface.positions[tri.y], surface.positions[tri.z]);
  const float dist_sq = len_squared_v3v3(co, closest);
  if (dist_sq < nearest->dist_sq) {
    nearest->index = index;
    nearest->dist_sq = dist_sq;
    copy_v3_v3(nearest->co, closest);
  }
}

void Surface::nearest(Span<float3> points,
                      MutableSpan<float> r_distance,
                      MutableSpan<int> r_tri,
                      MutableSpan<float3> r_point) const
{
  threading::parallel_for(points.index_range(), 1024, [&](const IndexRange range) {
    for (const int i : range) {
      BVHTreeNearest nearest{};
      nearest.index = -1;
      nearest.dist_sq = std::numeric_limits<float>::max();
      if (tree != nullptr) {
        BLI_bvhtree_find_nearest(
            tree, points[i], &nearest, nearest_on_triangle, const_cast<Surface *>(this));
      }
      if (nearest.index < 0) {
        r_distance[i] = std::numeric_limits<float>::infinity();
        r_tri[i] = -1;
        r_point[i] = points[i];
        continue;
      }
      r_distance[i] = std::sqrt(nearest.dist_sq);
      r_tri[i] = nearest.index;
      r_point[i] = float3(nearest.co);
    }
  });
}

/** Twice the signed area of (a, b, p): positive when p is left of a -> b. */
static inline double edge(const double2 a, const double2 b, const double2 p)
{
  return (b.x - a.x) * (p.y - a.y) - (b.y - a.y) * (p.x - a.x);
}

/** The top-left rule for a counter-clockwise triangle (y up): a left edge goes down, a top edge left. */
static inline bool owns_boundary(const double2 a, const double2 b)
{
  const double dx = b.x - a.x, dy = b.y - a.y;
  return dy < 0.0 || (dy == 0.0 && dx < 0.0);
}

static void rasterize_view(Span<float3> positions,
                           Span<int3> tris,
                           const View &view,
                           const int resolution,
                           MutableSpan<uint8_t> counts)
{
  auto project = [&](const float3 &p) {
    return double2(double(math::dot(view.row_x, p)) + view.offset_x,
                   double(math::dot(view.row_y, p)) + view.offset_y);
  };
  for (const int3 &tri : tris) {
    double2 a = project(positions[tri.x]);
    double2 b = project(positions[tri.y]);
    double2 c = project(positions[tri.z]);
    const double area = edge(a, b, c);
    if (!(std::abs(area) > 0.0)) {
      continue; /* Degenerate (or not finite): covers nothing. */
    }
    if (area < 0.0) {
      std::swap(b, c);
    }
    const double min_x = std::min({a.x, b.x, c.x}), max_x = std::max({a.x, b.x, c.x});
    const double min_y = std::min({a.y, b.y, c.y}), max_y = std::max({a.y, b.y, c.y});
    /* Pixel centres inside the bounding box: i + 0.5 in [min, max], clamped to the image in double first
     * so a far-off triangle never converts an out-of-range value to int. */
    const double top = double(resolution - 1);
    const int x0 = int(std::clamp(std::ceil(min_x - 0.5), 0.0, top + 1.0));
    const int x1 = int(std::clamp(std::floor(max_x - 0.5), -1.0, top));
    const int y0 = int(std::clamp(std::ceil(min_y - 0.5), 0.0, top + 1.0));
    const int y1 = int(std::clamp(std::floor(max_y - 0.5), -1.0, top));
    if (x0 > x1 || y0 > y1) {
      continue;
    }
    const bool own_ab = owns_boundary(a, b), own_bc = owns_boundary(b, c), own_ca = owns_boundary(c, a);
    for (int y = y0; y <= y1; y++) {
      for (int x = x0; x <= x1; x++) {
        const double2 p(x + 0.5, y + 0.5);
        const double w0 = edge(a, b, p), w1 = edge(b, c, p), w2 = edge(c, a, p);
        if ((w0 > 0.0 || (w0 == 0.0 && own_ab)) && (w1 > 0.0 || (w1 == 0.0 && own_bc)) &&
            (w2 > 0.0 || (w2 == 0.0 && own_ca)))
        {
          uint8_t &count = counts[size_t(y) * resolution + x];
          if (count < 255) {
            count++;
          }
        }
      }
    }
  }
}

void rasterize(Span<float3> positions,
               Span<int3> tris,
               Span<View> views,
               const int resolution,
               MutableSpan<uint8_t> r_counts)
{
  const size_t pixels = size_t(resolution) * resolution;
  std::fill(r_counts.begin(), r_counts.end(), 0);
  threading::parallel_for(views.index_range(), 1, [&](const IndexRange range) {
    for (const int v : range) {
      rasterize_view(positions, tris, views[v], resolution, r_counts.slice(v * pixels, pixels));
    }
  });
}

}  // namespace blender::angelo_reduce
