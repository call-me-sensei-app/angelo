/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "testing/testing.h"

#include <algorithm>
#include <memory>

#include "BLI_math_base.hh"
#include "BLI_vector.hh"

#include "GEO_uv_pack.hh"

namespace blender::geometry::tests {

/** A small deterministic generator, so islands are the same on every platform. */
struct Lcg {
  uint32_t state;
  float next()
  {
    state = state * 1664525u + 1013904223u;
    return float(state >> 8) / float(1 << 24);
  }
};

struct Triangle {
  float2 a, b, c;
};

/** Islands of mixed kinds: rectangles, L shapes and lone triangles, of spread sizes and aspects. */
static Vector<Vector<Triangle>> make_shapes(const uint32_t seed, const int count)
{
  Lcg rng{seed};
  Vector<Vector<Triangle>> shapes;
  for (int i = 0; i < count; i++) {
    const float w = 0.02f + rng.next() * rng.next() * 0.4f;
    const float h = 0.02f + rng.next() * 0.25f;
    const float x = rng.next() * 3.0f, y = rng.next() * 3.0f; /* Overlapping before packing. */
    Vector<Triangle> tris;
    switch (i % 3) {
      case 0: /* Rectangle. */
        tris.append({{x, y}, {x + w, y}, {x + w, y + h}});
        tris.append({{x, y}, {x + w, y + h}, {x, y + h}});
        break;
      case 1: /* L shape: a bar and a post. */
        tris.append({{x, y}, {x + w, y}, {x + w, y + h * 0.3f}});
        tris.append({{x, y}, {x + w, y + h * 0.3f}, {x, y + h * 0.3f}});
        tris.append({{x, y + h * 0.3f}, {x + w * 0.3f, y + h * 0.3f}, {x + w * 0.3f, y + h}});
        tris.append({{x, y + h * 0.3f}, {x + w * 0.3f, y + h}, {x, y + h}});
        break;
      default: /* Triangle. */
        tris.append({{x, y}, {x + w, y}, {x + w * rng.next(), y + h}});
        break;
    }
    shapes.append(tris);
  }
  return shapes;
}

struct Packed {
  float scale;
  Vector<Vector<Triangle>> placed;
  float used_area;
};

static float tri_area(const Triangle &t)
{
  return 0.5f * math::abs((t.b.x - t.a.x) * (t.c.y - t.a.y) - (t.c.x - t.a.x) * (t.b.y - t.a.y));
}

static Packed pack(const Vector<Vector<Triangle>> &shapes,
                   const int trials,
                   const eUVPackIsland_ShapeMethod shape,
                   const eUVPackIsland_MarginMethod margin_method,
                   const float margin)
{
  Vector<std::unique_ptr<PackIsland>> owned;
  Vector<PackIsland *> islands;
  for (const int64_t i : shapes.index_range()) {
    std::unique_ptr<PackIsland> island = std::make_unique<PackIsland>();
    island->aspect_y = 1.0f;
    island->caller_index = int(i);
    for (const Triangle &t : shapes[i]) {
      island->add_triangle(t.a, t.b, t.c);
    }
    islands.append(island.get());
    owned.append(std::move(island));
  }
  UVPackIsland_Params params;
  params.rotate_method = ED_UVPACK_ROTATION_ANY;
  params.shape_method = shape;
  params.margin_method = margin_method;
  params.margin = margin;
  params.search_trials = trials;
  Packed out;
  out.scale = pack_islands(islands, params);
  out.used_area = 0.0f;
  for (const int64_t i : shapes.index_range()) {
    float matrix[2][2];
    islands[i]->build_transformation(out.scale, islands[i]->angle, matrix);
    Vector<Triangle> placed;
    for (const Triangle &t : shapes[i]) {
      Triangle p;
      const float2 pre = islands[i]->pre_translate;
      mul_v2_m2_add_v2v2(p.a, matrix, t.a, pre);
      mul_v2_m2_add_v2v2(p.b, matrix, t.b, pre);
      mul_v2_m2_add_v2v2(p.c, matrix, t.c, pre);
      out.used_area += tri_area(p);
      placed.append(p);
    }
    out.placed.append(placed);
  }
  return out;
}

static bool inside(const Triangle &t, const float2 p, const float shrink)
{
  auto edge = [](const float2 a, const float2 b, const float2 q) {
    return (b.x - a.x) * (q.y - a.y) - (b.y - a.y) * (q.x - a.x);
  };
  const float d0 = edge(t.a, t.b, p), d1 = edge(t.b, t.c, p), d2 = edge(t.c, t.a, p);
  const float s = edge(t.a, t.b, t.c) > 0.0f ? 1.0f : -1.0f;
  return d0 * s > shrink && d1 * s > shrink && d2 * s > shrink;
}

/** Cells of a 256 x 256 raster over the unit square covered by two different islands. */
static int overlapping_cells(const Packed &packed)
{
  const int n = 256;
  Vector<int> owner(n * n, -1);
  int clashes = 0;
  for (const int64_t i : packed.placed.index_range()) {
    for (const Triangle &t : packed.placed[i]) {
      const float lo_x = std::min({t.a.x, t.b.x, t.c.x}), hi_x = std::max({t.a.x, t.b.x, t.c.x});
      const float lo_y = std::min({t.a.y, t.b.y, t.c.y}), hi_y = std::max({t.a.y, t.b.y, t.c.y});
      for (int y = std::max(0, int(lo_y * n)); y <= std::min(n - 1, int(hi_y * n)); y++) {
        for (int x = std::max(0, int(lo_x * n)); x <= std::min(n - 1, int(hi_x * n)); x++) {
          const float2 p((x + 0.5f) / n, (y + 0.5f) / n);
          if (!inside(t, p, 1e-6f)) {
            continue;
          }
          int &cell = owner[y * n + x];
          if (cell >= 0 && cell != int(i)) {
            clashes++;
          }
          cell = int(i);
        }
      }
    }
  }
  return clashes;
}

TEST(uv_pack, search_is_deterministic)
{
  const Vector<Vector<Triangle>> shapes = make_shapes(7, 60);
  const Packed a = pack(shapes, 16, ED_UVPACK_SHAPE_CONCAVE, ED_UVPACK_MARGIN_SCALED, 0.001f);
  const Packed b = pack(shapes, 16, ED_UVPACK_SHAPE_CONCAVE, ED_UVPACK_MARGIN_SCALED, 0.001f);
  EXPECT_EQ(a.scale, b.scale);
  for (const int64_t i : a.placed.index_range()) {
    for (const int64_t k : a.placed[i].index_range()) {
      EXPECT_EQ(a.placed[i][k].a, b.placed[i][k].a);
      EXPECT_EQ(a.placed[i][k].c, b.placed[i][k].c);
    }
  }
}

TEST(uv_pack, search_is_never_looser_than_one_pass)
{
  for (const uint32_t seed : {1u, 2u, 3u, 11u, 29u}) {
    for (const eUVPackIsland_ShapeMethod shape : {ED_UVPACK_SHAPE_AABB, ED_UVPACK_SHAPE_CONCAVE}) {
      const Vector<Vector<Triangle>> shapes = make_shapes(seed, 20 + int(seed) * 4);
      const Packed once = pack(shapes, 0, shape, ED_UVPACK_MARGIN_SCALED, 0.001f);
      const Packed searched = pack(shapes, 16, shape, ED_UVPACK_MARGIN_SCALED, 0.001f);
      EXPECT_GE(searched.scale, once.scale * (1.0f - 1e-6f)) << "seed " << seed;
    }
  }
}

TEST(uv_pack, searched_layouts_fit_and_never_overlap)
{
  for (const uint32_t seed : {4u, 5u}) {
    for (const eUVPackIsland_MarginMethod margin : {ED_UVPACK_MARGIN_SCALED, ED_UVPACK_MARGIN_FRACTION})
    {
      const Packed packed = pack(make_shapes(seed, 40), 16, ED_UVPACK_SHAPE_CONCAVE, margin, 0.004f);
      EXPECT_EQ(overlapping_cells(packed), 0) << "seed " << seed;
      for (const Vector<Triangle> &island : packed.placed) {
        for (const Triangle &t : island) {
          for (const float2 &p : {t.a, t.b, t.c}) {
            EXPECT_GE(p.x, -1e-4f);
            EXPECT_GE(p.y, -1e-4f);
            EXPECT_LE(p.x, 1.0f + 1e-4f);
            EXPECT_LE(p.y, 1.0f + 1e-4f);
          }
        }
      }
    }
  }
}

}  // namespace blender::geometry::tests
