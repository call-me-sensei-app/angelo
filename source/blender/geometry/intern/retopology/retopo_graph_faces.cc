/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup geo
 *
 * Passes on the faces of the extracted graph. See `retopo_graph.hh`.
 */

#include <algorithm>
#include <array>
#include <cmath>

#include "retopo_edges.hh"
#include "retopo_graph.hh"

namespace blender::geometry::retopo {

/** A cycle is a face only when its centroid lies within this share of its mean radius from the
 * surface. */
static constexpr double FACE_COVERAGE = 0.25;
/** A cycle on a smooth part whose vertex normals average to less than this length (they span
 * more than about 120 degrees around it) goes around the part rather than across it: never a
 * face. */
static constexpr double MIN_NORMAL_AGREEMENT = 0.5;
/** A border loop of at most this many edges whose middle is on the surface is a lost face, closed.
 */
static constexpr int MAX_HOLE = 8;
/** A triangle pair becomes a quad when no corner of the quad is further than this from a right
 * angle (radians). */
static constexpr double MERGE_PAIR_ANGLE = 0.7853981633974483; /* 45 degrees */

/** Counter-clockwise about its vertices' normals, with its middle on the surface: a face, not a
 * border. */
bool is_face(const Graph &g,
                    const ClosestPointTree &tree,
                    const std::vector<int> &cycle,
                    const double scale,
                    const bool check_orientation)
{
  /* Orientation: each corner's turn measured against that corner's own normal, summed (robust to a
   * twisted or bent cycle, where the one plane of a Newell normal can point anywhere). */
  Vec3 centroid;
  double turn = 0.0;
  const size_t n = cycle.size();
  for (size_t i = 0; i < n; i++) {
    const Vec3 &prev = g.positions[size_t(cycle[(i + n - 1) % n])],
               &p = g.positions[size_t(cycle[i])], &next = g.positions[size_t(cycle[(i + 1) % n])];
    turn += dot(cross(p - prev, next - p), g.normals[size_t(cycle[i])]);
    centroid += p;
  }
  centroid = centroid / double(n);
  double radius = 0.0;
  for (const int v : cycle) {
    radius += distance(g.positions[size_t(v)], centroid);
  }
  radius /= double(n);
  if (check_orientation && turn <= 0.0) {
    return false;
  }
  /* Coverage: the middle within a share of the cycle's size from the surface; at least half a
   * lattice step's worth, so a sliver (radius near zero) is not called a border for lying a hair
   * off the surface. */
  double reach = FACE_COVERAGE * std::max(radius, 0.5 * scale);
  /* On a smoothly curved surface a face's middle lies inside it by the sagitta, about radius x
   * (angle between a vertex normal and the face's) / 2: that much more is allowed, so a face on a
   * part curved tighter than a lattice step is not taken for a border (2026-11-15: a closed
   * armadillo came out with 9-12 holes this way). Only where the bend is smooth: across a hard
   * feature the normals jump and the plain test is kept (allowing it there, or refusing cycles
   * whose normals spread, cost hard-surface meshes accuracy and feature recall). */
  Vec3 normal_sum;
  bool smooth = true;
  for (const int v : cycle) {
    normal_sum += g.normals[size_t(v)];
    smooth = smooth && g.kind[size_t(v)] == FeatureKind::None;
  }
  const double agreement = length(normal_sum) / double(n);
  if (smooth && agreement < MIN_NORMAL_AGREEMENT) {
    /* Around a thin smooth part (a tube narrower than a face): such a cycle, taken for a face,
     * shares its edges with the real faces there and the result opens up. */
    return false;
  }
  if (smooth) {
    const Vec3 face_normal = normal_sum / (agreement * double(n));
    double bend = 0.0;
    for (const int v : cycle) {
      bend += std::acos(std::clamp(dot(g.normals[size_t(v)], face_normal), -1.0, 1.0));
    }
    reach += 0.5 * radius * bend / double(n);
  }
  return tree.closest(centroid, reach).primitive >= 0;
}

/** Largest deviation from a right angle over the corners of quad `q` (radians). */
static double quad_badness(const Graph &g, const std::array<int, 4> &q)
{
  double worst = 0.0;
  for (int c = 0; c < 4; c++) {
    const Vec3 &p = g.positions[size_t(q[size_t(c)])];
    const Vec3 a = normalized(g.positions[size_t(q[size_t((c + 3) % 4)])] - p);
    const Vec3 b = normalized(g.positions[size_t(q[size_t((c + 1) % 4)])] - p);
    worst = std::max(worst,
                     std::fabs(std::acos(std::clamp(dot(a, b), -1.0, 1.0)) - 1.5707963267948966));
  }
  return worst;
}

/**
 * Two triangles sharing an edge become one quad when every corner of the quad is within
 * MERGE_PAIR_ANGLE of a right angle: a position-field dislocation leaves such pairs, and a pair
 * split into quads would add two poles each. Pairs are merged best first, each triangle once.
 */
int merge_triangle_pairs(const Graph &g, std::vector<std::vector<int>> &faces)
{
  std::vector<std::pair<uint64_t, int>> edges;
  for (size_t f = 0; f < faces.size(); f++) {
    if (faces[f].size() == 3) {
      for (int c = 0; c < 3; c++) {
        edges.emplace_back(edge_key(faces[f][size_t(c)], faces[f][size_t((c + 1) % 3)]), int(f));
      }
    }
  }
  std::sort(edges.begin(), edges.end());
  struct Pair {
    double badness;
    int f0, f1;
    std::array<int, 4> quad;
  };
  std::vector<Pair> pairs;
  for (size_t i = 0; i + 1 < edges.size(); i++) {
    if (edges[i].first != edges[i + 1].first ||
        (i + 2 < edges.size() && edges[i + 2].first == edges[i].first))
    {
      continue;
    }
    const int f0 = edges[i].second, f1 = edges[i + 1].second;
    if (f0 == f1) {
      continue;
    }
    const int a = edge_key_v0(edges[i].first), b = edge_key_v1(edges[i].first);
    /* Rotate f0 so it reads (x, y, c0) with {x, y} = {a, b}; f1 then reads (y, x, c1). */
    const std::vector<int> &t0 = faces[size_t(f0)], &t1 = faces[size_t(f1)];
    int r = 0;
    while (!((t0[size_t(r)] == a || t0[size_t(r)] == b) &&
             (t0[size_t((r + 1) % 3)] == a || t0[size_t((r + 1) % 3)] == b)))
    {
      r++;
    }
    const int x = t0[size_t(r)], y = t0[size_t((r + 1) % 3)], c0 = t0[size_t((r + 2) % 3)];
    int c1 = -1;
    for (const int v : t1) {
      if (v != a && v != b) {
        c1 = v;
      }
    }
    if (c1 < 0 || c1 == c0) {
      continue;
    }
    const std::array<int, 4> quad = {x, c1, y, c0};
    const double badness = quad_badness(g, quad);
    if (badness < MERGE_PAIR_ANGLE) {
      pairs.push_back({badness, f0, f1, quad});
    }
  }
  std::sort(pairs.begin(), pairs.end(), [](const Pair &p, const Pair &q) {
    return p.badness != q.badness ? p.badness < q.badness :
                                    (p.f0 != q.f0 ? p.f0 < q.f0 : p.f1 < q.f1);
  });
  std::vector<uint8_t> taken(faces.size(), 0);
  int merged = 0;
  for (const Pair &p : pairs) {
    if (taken[size_t(p.f0)] || taken[size_t(p.f1)]) {
      continue;
    }
    taken[size_t(p.f0)] = taken[size_t(p.f1)] = 1;
    faces[size_t(p.f0)] = {p.quad[0], p.quad[1], p.quad[2], p.quad[3]};
    faces[size_t(p.f1)].clear();
    merged++;
  }
  faces.erase(std::remove_if(
                  faces.begin(), faces.end(), [](const std::vector<int> &f) { return f.empty(); }),
              faces.end());
  return merged;
}

/**
 * Close small holes: a border loop of at most MAX_HOLE edges whose middle lies on the surface is a
 * face the tracing lost (two walks that met on a half-edge), not a border of the input. It is
 * added with the opposite orientation of its neighbours' border edges. Loops through a vertex with
 * more than one border edge leaving it are left alone.
 */
int fill_small_holes(const Graph &g,
                            const ClosestPointTree &tree,
                            const double scale,
                            std::vector<std::vector<int>> &faces)
{
  std::vector<std::pair<uint64_t, std::pair<int, int>>> half;
  for (const std::vector<int> &f : faces) {
    for (size_t c = 0; c < f.size(); c++) {
      const int a = f[c], b = f[(c + 1) % f.size()];
      half.push_back({edge_key(a, b), {a, b}});
    }
  }
  std::sort(half.begin(), half.end());
  /* Border edges reversed: the hole face walks b -> a. */
  std::vector<std::vector<int>> out(g.positions.size());
  for (size_t i = 0; i < half.size(); i++) {
    const bool single = (i == 0 || half[i - 1].first != half[i].first) &&
                        (i + 1 == half.size() || half[i + 1].first != half[i].first);
    if (single) {
      out[size_t(half[i].second.second)].push_back(half[i].second.first);
    }
  }
  std::vector<uint8_t> done(g.positions.size(), 0);
  int filled = 0;
  for (size_t start = 0; start < out.size(); start++) {
    if (out[start].size() != 1 || done[start]) {
      continue;
    }
    std::vector<int> loop = {int(start)};
    int v = out[start][0];
    bool ok = true;
    while (v != int(start)) {
      if (out[size_t(v)].size() != 1 || done[size_t(v)] || int(loop.size()) > MAX_HOLE) {
        ok = false;
        break;
      }
      loop.push_back(v);
      v = out[size_t(v)][0];
    }
    for (const int u : loop) {
      done[size_t(u)] = 1;
    }
    /* Its orientation is its neighbours' reversed by construction: only coverage is checked. */
    if (ok && loop.size() >= 3 && is_face(g, tree, loop, scale, false)) {
      faces.push_back(loop);
      filled++;
    }
  }
  return filled;
}

}  // namespace blender::geometry::retopo
