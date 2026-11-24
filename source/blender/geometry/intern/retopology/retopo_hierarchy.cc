/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup geo
 *
 * The multi-resolution vertex graph, its colouring and the curvature directions at the output
 * scale.
 */

#include <algorithm>
#include <array>
#include <cmath>

#include "retopo_edges.hh"
#include "retopo_field.hh"

namespace blender::geometry::retopo {

/** Stop coarsening below this many vertices, or when a pass removes less than this share of them.
 */
static constexpr int COARSEST_SIZE = 16;
static constexpr double MIN_REDUCTION = 0.1;
static constexpr int MAX_LEVELS = 40;
/** Curvature whose turning over one lattice cell is below this many radians reads as flat (no
 * preferred direction). */
static constexpr double CURVATURE_SIGNIFICANT_TURN = 0.2;

using Tensor = std::array<double, 6>; /* symmetric 3x3: xx, xy, xz, yy, yz, zz */

static void colour_level(Level &level)
{
  const int n = level.size();
  std::vector<int> colour(size_t(n), -1);
  int colours_num = 0;
  std::vector<uint8_t> taken;
  for (int v = 0; v < n; v++) {
    taken.assign(size_t(colours_num) + 1, 0);
    for (int k = level.adjacency_offsets[size_t(v)]; k < level.adjacency_offsets[size_t(v) + 1];
         k++)
    {
      const int c = colour[size_t(level.adjacency[size_t(k)])];
      if (c >= 0) {
        taken[size_t(c)] = 1;
      }
    }
    int c = 0;
    while (taken[size_t(c)]) {
      c++;
    }
    colour[size_t(v)] = c;
    colours_num = std::max(colours_num, c + 1);
  }
  level.colours.assign(size_t(colours_num), {});
  for (int v = 0; v < n; v++) {
    level.colours[size_t(colour[size_t(v)])].push_back(v);
  }
}

static FeatureKind stronger(const FeatureKind a, const FeatureKind b)
{
  return uint8_t(a) >= uint8_t(b) ? a : b;
}

static bool coarsen(const Level &fine, Level &coarse, std::vector<int> &parent)
{
  const int n = fine.size();
  struct Candidate {
    double score;
    int i, j;
  };
  std::vector<Candidate> candidates;
  for (int i = 0; i < n; i++) {
    for (int k = fine.adjacency_offsets[size_t(i)]; k < fine.adjacency_offsets[size_t(i) + 1]; k++)
    {
      const int j = fine.adjacency[size_t(k)];
      if (j <= i) {
        continue;
      }
      const double ai = fine.areas[size_t(i)], aj = fine.areas[size_t(j)];
      const double ratio = std::min(ai, aj) / std::max(std::max(ai, aj), 1e-300);
      candidates.push_back({dot(fine.normals[size_t(i)], fine.normals[size_t(j)]) * ratio, i, j});
    }
  }
  std::sort(candidates.begin(), candidates.end(), [](const Candidate &a, const Candidate &b) {
    if (a.score != b.score) {
      return a.score > b.score;
    }
    return a.i != b.i ? a.i < b.i : a.j < b.j;
  });
  parent.assign(size_t(n), -1);
  int next = 0;
  std::vector<std::vector<int>> members;
  for (const Candidate &c : candidates) {
    if (parent[size_t(c.i)] < 0 && parent[size_t(c.j)] < 0) {
      parent[size_t(c.i)] = parent[size_t(c.j)] = next++;
      members.push_back({c.i, c.j});
    }
  }
  /* A vertex whose neighbours were all matched joins the neighbouring cluster it scores best with,
   * so every level about halves: a level left with lone vertices stops coarsening early, and a
   * coarsest level finer than the output lattice freezes position-field dislocations in (found
   * 2026-11-14: hierarchies stalled at ~3 vertices per output vertex, ~10% of extracted faces
   * triangles). Only a vertex with no neighbour at all stays alone. */
  for (int i = 0; i < n; i++) {
    if (parent[size_t(i)] >= 0) {
      continue;
    }
    int best = -1;
    double best_score = -1e300;
    for (int k = fine.adjacency_offsets[size_t(i)]; k < fine.adjacency_offsets[size_t(i) + 1]; k++)
    {
      const int j = fine.adjacency[size_t(k)];
      if (parent[size_t(j)] < 0) {
        continue;
      }
      const double ai = fine.areas[size_t(i)], aj = fine.areas[size_t(j)];
      const double score = dot(fine.normals[size_t(i)], fine.normals[size_t(j)]) *
                           std::min(ai, aj) / std::max(std::max(ai, aj), 1e-300);
      if (score > best_score || (score == best_score && j < best)) {
        best_score = score;
        best = j;
      }
    }
    if (best >= 0) {
      parent[size_t(i)] = parent[size_t(best)];
      members[size_t(parent[size_t(best)])].push_back(i);
    }
    else {
      parent[size_t(i)] = next++;
      members.push_back({i});
    }
  }
  if (next > int(n * (1.0 - MIN_REDUCTION))) {
    return false;
  }
  coarse.positions.assign(size_t(next), Vec3());
  coarse.normals.assign(size_t(next), Vec3());
  coarse.areas.assign(size_t(next), 0.0);
  coarse.feature_kind.assign(size_t(next), FeatureKind::None);
  coarse.pin_kind.assign(size_t(next), FeatureKind::None);
  coarse.pin_point.assign(size_t(next), Vec3());
  coarse.feature_dir.assign(size_t(next), Vec3());
  coarse.guided.assign(size_t(next), 0);
  for (int c = 0; c < next; c++) {
    double area = 0.0, pinned_area = 0.0;
    Vec3 p, nrm, pinned;
    for (const int v : members[size_t(c)]) {
      const double a = fine.areas[size_t(v)];
      area += a;
      p += fine.positions[size_t(v)] * a;
      if (fine.pin_kind[size_t(v)] != FeatureKind::None) {
        pinned += fine.pin_point[size_t(v)] * a;
        pinned_area += a;
      }
      nrm += fine.normals[size_t(v)] * a;
      if (uint8_t(fine.feature_kind[size_t(v)]) > uint8_t(coarse.feature_kind[size_t(c)])) {
        coarse.feature_dir[size_t(c)] = fine.feature_dir[size_t(v)];
      }
      coarse.feature_kind[size_t(c)] = stronger(coarse.feature_kind[size_t(c)],
                                                fine.feature_kind[size_t(v)]);
      coarse.pin_kind[size_t(c)] = stronger(coarse.pin_kind[size_t(c)], fine.pin_kind[size_t(v)]);
      if (fine.guided[size_t(v)] && coarse.feature_kind[size_t(c)] == FeatureKind::None &&
          !coarse.guided[size_t(c)])
      {
        coarse.guided[size_t(c)] = 1;
        coarse.feature_dir[size_t(c)] = fine.feature_dir[size_t(v)];
      }
    }
    coarse.areas[size_t(c)] = area;
    coarse.pin_point[size_t(c)] = pinned_area > 0.0 ? pinned / pinned_area : Vec3();
    coarse.positions[size_t(c)] = area > 0.0 ? p / area :
                                               fine.positions[size_t(members[size_t(c)][0])];
    Vec3 nn = normalized(nrm);
    coarse.normals[size_t(c)] = length_squared(nn) > 0.0 ?
                                    nn :
                                    fine.normals[size_t(members[size_t(c)][0])];
    const Vec3 d = normalized(
        project_tangent(coarse.feature_dir[size_t(c)], coarse.normals[size_t(c)]));
    coarse.feature_dir[size_t(c)] = length_squared(d) > 0.0 ?
                                        d :
                                        any_perpendicular(coarse.normals[size_t(c)]);
  }
  /* Coarse edges: fine edges between different parents, weights summed. */
  std::vector<std::pair<std::pair<int, int>, float>> edges;
  for (int i = 0; i < n; i++) {
    for (int k = fine.adjacency_offsets[size_t(i)]; k < fine.adjacency_offsets[size_t(i) + 1]; k++)
    {
      const int pi = parent[size_t(i)], pj = parent[size_t(fine.adjacency[size_t(k)])];
      if (pi != pj) {
        edges.push_back({{pi, pj}, fine.weights[size_t(k)]});
      }
    }
  }
  std::sort(edges.begin(), edges.end());
  coarse.adjacency_offsets.assign(size_t(next) + 1, 0);
  coarse.adjacency.clear();
  coarse.weights.clear();
  for (size_t e = 0; e < edges.size(); e++) {
    if (e > 0 && edges[e].first == edges[e - 1].first) {
      coarse.weights.back() += edges[e].second;
      continue;
    }
    coarse.adjacency_offsets[size_t(edges[e].first.first) + 1]++;
    coarse.adjacency.push_back(edges[e].first.second);
    coarse.weights.push_back(edges[e].second);
  }
  for (int c = 0; c < next; c++) {
    coarse.adjacency_offsets[size_t(c) + 1] += coarse.adjacency_offsets[size_t(c)];
  }
  return true;
}

static void tensor_add(Tensor &t, const Vec3 &e, const double w)
{
  t[0] += w * e.x * e.x;
  t[1] += w * e.x * e.y;
  t[2] += w * e.x * e.z;
  t[3] += w * e.y * e.y;
  t[4] += w * e.y * e.z;
  t[5] += w * e.z * e.z;
}

static Vec3 tensor_apply(const Tensor &t, const Vec3 &v)
{
  return {t[0] * v.x + t[1] * v.y + t[2] * v.z,
          t[1] * v.x + t[3] * v.y + t[4] * v.z,
          t[2] * v.x + t[4] * v.y + t[5] * v.z};
}

/** Principal direction and confidence of a tensor in the tangent plane of `n`, over a region of
 * `area`. */
static void tensor_direction(const Tensor &t,
                             const Vec3 &n,
                             const double area,
                             const double scale,
                             Vec3 &dir,
                             double &confidence)
{
  const Vec3 u = any_perpendicular(n), w = cross(n, u);
  const double a = dot(u, tensor_apply(t, u)), b = dot(u, tensor_apply(t, w)),
               c = dot(w, tensor_apply(t, w));
  const double mean = 0.5 * (a + c), radius = std::sqrt(0.25 * (a - c) * (a - c) + b * b);
  const double l1 = mean + radius, l2 = mean - radius;
  const double theta = 0.5 * std::atan2(2.0 * b, a - c);
  dir = u * std::cos(theta) + w * std::sin(theta);
  const double magnitude = std::fabs(l1) + std::fabs(l2);
  if (magnitude <= 0.0 || area <= 0.0) {
    confidence = 0.0;
    return;
  }
  const double anisotropy = std::fabs(l1 - l2) / magnitude;
  const double turn_per_cell = magnitude / area * scale; /* mean |curvature| x lattice spacing */
  confidence = anisotropy * std::min(1.0, turn_per_cell / CURVATURE_SIGNIFICANT_TURN);
}

/** Cohen-Steiner & Morvan edge tensors on level 0, summed up the hierarchy; directions at the
 * output scale. */
static void curvature_directions(const Surface &surface, Hierarchy &h, const double scale)
{
  std::vector<std::vector<Tensor>> tensors(h.levels.size());
  tensors[0].assign(size_t(h.levels[0].size()), Tensor{});
  EdgeTris et;
  et.build(surface.tris);
  for (int e = 0; e < et.edges_num(); e++) {
    if (et.count(e) != 2) {
      continue;
    }
    const auto &t0 = surface.tris[size_t(et.tris[size_t(et.offsets[size_t(e)])])];
    const auto &t1 = surface.tris[size_t(et.tris[size_t(et.offsets[size_t(e)] + 1)])];
    const Vec3 n0 = normalized(
        cross(surface.positions[size_t(t0[1])] - surface.positions[size_t(t0[0])],
              surface.positions[size_t(t0[2])] - surface.positions[size_t(t0[0])]));
    const Vec3 n1 = normalized(
        cross(surface.positions[size_t(t1[1])] - surface.positions[size_t(t1[0])],
              surface.positions[size_t(t1[2])] - surface.positions[size_t(t1[0])]));
    const int a = edge_key_v0(et.keys[size_t(e)]), b = edge_key_v1(et.keys[size_t(e)]);
    const Vec3 ev = surface.positions[size_t(b)] - surface.positions[size_t(a)];
    const double len = length(ev);
    if (len <= 0.0) {
      continue;
    }
    const double angle = std::acos(std::clamp(dot(n0, n1), -1.0, 1.0));
    const Vec3 dir = ev / len;
    tensor_add(tensors[0][size_t(a)], dir, 0.5 * angle * len);
    tensor_add(tensors[0][size_t(b)], dir, 0.5 * angle * len);
  }
  for (size_t l = 1; l < h.levels.size(); l++) {
    tensors[l].assign(size_t(h.levels[l].size()), Tensor{});
    const Level &fine = h.levels[l - 1];
    for (int v = 0; v < fine.size(); v++) {
      Tensor &dst = tensors[l][size_t(fine.parent[size_t(v)])];
      for (int k = 0; k < 6; k++) {
        dst[size_t(k)] += tensors[l - 1][size_t(v)][size_t(k)];
      }
    }
  }
  /* The output-scale level: the first whose mean vertex area reaches one lattice cell. */
  size_t target = h.levels.size() - 1;
  for (size_t l = 0; l < h.levels.size(); l++) {
    double area = 0.0;
    for (const double a : h.levels[l].areas) {
      area += a;
    }
    if (area / std::max(h.levels[l].size(), 1) >= scale * scale) {
      target = l;
      break;
    }
  }
  for (size_t l = 0; l < h.levels.size(); l++) {
    Level &level = h.levels[l];
    level.curvature_dir.assign(size_t(level.size()), Vec3());
    level.curvature_confidence.assign(size_t(level.size()), 0.0);
    for (int v = 0; v < level.size(); v++) {
      /* Finer than the output scale: read the ancestor's tensor at the output scale. */
      size_t at = l;
      int index = v;
      while (at < target) {
        index = h.levels[at].parent[size_t(index)];
        at++;
      }
      tensor_direction(tensors[at][size_t(index)],
                       level.normals[size_t(v)],
                       h.levels[at].areas[size_t(index)],
                       scale,
                       level.curvature_dir[size_t(v)],
                       level.curvature_confidence[size_t(v)]);
    }
  }
}

Hierarchy build_hierarchy(const Surface &surface, const double scale)
{
  Hierarchy h;
  Level base;
  base.positions = surface.positions;
  base.normals = surface.normals;
  base.areas = surface.areas;
  base.adjacency_offsets = surface.adjacency_offsets;
  base.adjacency = surface.adjacency;
  base.weights.assign(surface.adjacency.size(), 1.0f);
  base.feature_kind = surface.feature_kind;
  base.pin_kind = surface.pin_kind;
  base.pin_point = surface.positions;
  base.feature_dir = surface.feature_dir;
  base.guided = surface.guided;
  h.levels.push_back(std::move(base));
  while (int(h.levels.size()) < MAX_LEVELS && h.levels.back().size() > COARSEST_SIZE) {
    Level coarse;
    std::vector<int> parent;
    if (!coarsen(h.levels.back(), coarse, parent)) {
      break;
    }
    h.levels.back().parent = std::move(parent);
    h.levels.push_back(std::move(coarse));
  }
  for (Level &level : h.levels) {
    colour_level(level);
  }
  curvature_directions(surface, h, scale);
  return h;
}

void update_curvature(const Surface &surface, Hierarchy &h, const double scale)
{
  curvature_directions(surface, h, scale);
}

}  // namespace blender::geometry::retopo
