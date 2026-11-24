/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Unit tests of the finished mesh's hole closing (`intern/retopology/retopo_holes.cc`): a border
 * of the result is a hole exactly when the input has no border beside it. Every mesh is generated
 * in code.
 */

#include <cmath>
#include <map>
#include <set>
#include <utility>

#include "retopo_test_meshes.hh"

#include "../intern/retopology/retopo_extract.hh"

#include "testing/testing.h"

namespace blender::geometry::retopo::test {

static int open_edges(const PolyMesh &m)
{
  std::map<std::pair<int, int>, int> uses;
  for (int f = 0; f < m.faces_num(); f++) {
    const int n = m.face_size(f);
    for (int c = 0; c < n; c++) {
      const int a = m.corner_verts[m.face_offsets[f] + c];
      const int b = m.corner_verts[m.face_offsets[f] + (c + 1) % n];
      uses[{std::min(a, b), std::max(a, b)}]++;
    }
  }
  int n = 0;
  for (const auto &item : uses) {
    n += item.second == 1;
  }
  return n;
}

static bool all_quads(const PolyMesh &m)
{
  for (int f = 0; f < m.faces_num(); f++) {
    if (m.face_size(f) != 4) {
      return false;
    }
  }
  return true;
}

static Surface surface_of(const TriMesh &t)
{
  Surface s;
  s.positions = t.positions;
  s.tris = t.tris;
  return s;
}

/** The quads of `torus(nu, nv, ...)` (same vertices, same winding), less the quads in `skip`. */
static PolyMesh torus_quads(const int nu,
                            const int nv,
                            const double R,
                            const double r,
                            const std::set<std::pair<int, int>> &skip)
{
  PolyMesh m;
  m.positions = torus(nu, nv, R, r).positions;
  auto id = [&](int i, int j) { return ((i + nu) % nu) * nv + (j + nv) % nv; };
  for (int i = 0; i < nu; i++) {
    for (int j = 0; j < nv; j++) {
      if (skip.count({i, j})) {
        continue;
      }
      const int quad[4] = {id(i, j), id(i + 1, j), id(i + 1, j + 1), id(i, j + 1)};
      m.add_face(quad, 4);
    }
  }
  return m;
}

/** An n x n grid of quads over [-1, 1]^2 at z = 0, and its triangles, less the quads in `skip`. */
static std::pair<PolyMesh, TriMesh> plane(const int n, const std::set<std::pair<int, int>> &skip)
{
  PolyMesh m;
  TriMesh t;
  for (int i = 0; i <= n; i++) {
    for (int j = 0; j <= n; j++) {
      m.positions.push_back({-1.0 + 2.0 * i / n, -1.0 + 2.0 * j / n, 0.0});
    }
  }
  t.positions = m.positions;
  auto id = [&](int i, int j) { return i * (n + 1) + j; };
  for (int i = 0; i < n; i++) {
    for (int j = 0; j < n; j++) {
      t.tris.push_back({id(i, j), id(i + 1, j), id(i + 1, j + 1)});
      t.tris.push_back({id(i, j), id(i + 1, j + 1), id(i, j + 1)});
      if (!skip.count({i, j})) {
        const int quad[4] = {id(i, j), id(i + 1, j), id(i + 1, j + 1), id(i, j + 1)};
        m.add_face(quad, 4);
      }
    }
  }
  return {m, t};
}

TEST(retopo_holes, holes_touching_at_a_corner_are_both_closed)
{
  /* Two lost quads sharing one corner: the two border loops meet at that vertex. The earlier pass
   * skipped every loop through such a vertex (closed scans kept 3 to 56 of them, 2026-11-18). */
  const TriMesh input = torus(24, 12);
  PolyMesh m = torus_quads(24, 12, 1.0, 0.35, {{3, 3}, {4, 4}});
  ASSERT_EQ(open_edges(m), 8);
  ClosestPointTree tree;
  tree.build_triangles(input.positions, input.tris);
  const size_t before = m.positions.size();
  EXPECT_EQ(close_holes(m, surface_of(input), tree, 0.25, 0), 2);
  EXPECT_EQ(open_edges(m), 0);
  EXPECT_TRUE(all_quads(m));
  EXPECT_EQ(m.positions.size(), before); /* a four-edge hole is one quad, with no pole */
}

TEST(retopo_holes, a_hole_on_a_part_curved_tighter_than_a_face_is_closed)
{
  /* A thin tube with four faces around, four of them lost (an eight-edge hole around half the
   * tube): the chord's middle vertex goes on the surface, not across the inside of the tube. */
  const TriMesh input = torus(32, 4, 1.0, 0.05);
  PolyMesh m = torus_quads(32, 4, 1.0, 0.05, {{5, 0}, {5, 1}, {6, 0}, {6, 1}});
  ASSERT_EQ(open_edges(m), 8);
  ClosestPointTree tree;
  tree.build_triangles(input.positions, input.tris);
  const size_t before = m.positions.size();
  EXPECT_EQ(close_holes(m, surface_of(input), tree, 0.07, 0), 1);
  EXPECT_EQ(open_edges(m), 0);
  EXPECT_TRUE(all_quads(m));
  ASSERT_EQ(m.positions.size(), before + 1);
  EXPECT_LT(tree.closest(m.positions.back()).distance_squared, 1e-18);
}

TEST(retopo_holes, the_inputs_own_border_stays_open)
{
  /* A small open patch: its rim is short and its centre lies on the surface, yet it is the
   * input's border, followed by the result, never a hole. */
  auto [m, input] = plane(2, {});
  ClosestPointTree tree;
  tree.build_triangles(input.positions, input.tris);
  EXPECT_EQ(close_holes(m, surface_of(input), tree, 1.0, 0), 0);
  EXPECT_EQ(m.faces_num(), 4);
  EXPECT_EQ(open_edges(m), 8);
}

TEST(retopo_holes, a_hole_inside_an_open_surface_is_closed_and_a_centre_on_a_mirror_plane_stays_on_it)
{
  /* A 6 x 6 patch with its middle 2 x 2 quads lost: the hole is two faces from the rim, and its
   * loop is mirrored across x = 0. */
  auto [m, input] = plane(6, {{2, 2}, {2, 3}, {3, 2}, {3, 3}});
  const size_t before = m.positions.size();
  ASSERT_EQ(open_edges(m), 24 + 8);
  ClosestPointTree tree;
  tree.build_triangles(input.positions, input.tris);
  EXPECT_EQ(close_holes(m, surface_of(input), tree, 1.0 / 3.0, 1), 1);
  EXPECT_EQ(open_edges(m), 24); /* the rim only */
  EXPECT_TRUE(all_quads(m));
  ASSERT_EQ(m.positions.size(), before + 1);
  EXPECT_EQ(m.positions.back()[0], 0.0);
}

}  // namespace blender::geometry::retopo::test
