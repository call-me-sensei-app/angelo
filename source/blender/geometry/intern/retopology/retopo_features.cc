/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup geo
 *
 * Hard features, the vertex graph and per-vertex frames of the working surface. See
 * `retopo_mesh.hh`.
 */

#include <algorithm>
#include <cmath>
#include <unordered_set>

#include "retopo_bvh.hh"
#include "retopo_edges.hh"
#include "retopo_mesh.hh"
#include "retopo_parallel.hh"

namespace blender::geometry::retopo {

/** Bilateral face-normal filtering (Zheng et al., "Bilateral Normal Filtering for Mesh Denoising",
 * TVCG 2011): range sigma in normal-difference length, so differences of a few degrees blend and a
 * sharp edge does not. */
static constexpr double NORMAL_RANGE_SIGMA = 0.35;
static constexpr int NORMAL_FILTER_PASSES = 4;
/** A feature line turning by more than this many degrees at a vertex makes that vertex a corner.
 */
static constexpr double CORNER_TURN_DEGREES = 40.0;
/** Bound on the passes that peel short sharp segments; each pass removes one layer of a noise
 * network. */
static constexpr int MAX_SEGMENT_PASSES = 256;
/** An edge is judged sharp at the output scale over a disc of this share of the output edge
 * length... */
static constexpr double SCALE_RADIUS_OF_EDGE = 0.5;
/** ...gathering at most this many triangles (a very fine mesh under a coarse target). */
static constexpr size_t MAX_SCALE_TRIANGLES = 4096;

static std::vector<Vec3> filtered_face_normals(const Surface &s, const EdgeTris &et)
{
  const size_t n = s.tris.size();
  std::vector<Vec3> normals(n);
  std::vector<double> areas(n);
  for (size_t t = 0; t < n; t++) {
    const Vec3 c = cross(s.positions[size_t(s.tris[t][1])] - s.positions[size_t(s.tris[t][0])],
                         s.positions[size_t(s.tris[t][2])] - s.positions[size_t(s.tris[t][0])]);
    areas[t] = 0.5 * length(c);
    normals[t] = normalized(c);
  }
  const double inv = 1.0 / (2.0 * NORMAL_RANGE_SIGMA * NORMAL_RANGE_SIGMA);
  std::vector<Vec3> next(n);
  for (int pass = 0; pass < NORMAL_FILTER_PASSES; pass++) {
    parallel_for(int64_t(n), 2048, [&](const int64_t ti) {
      const size_t t = size_t(ti);
      Vec3 sum = normals[t] * areas[t];
      for (int c = 0; c < 3; c++) {
        const int e = et.tri_edges[t][size_t(c)];
        for (int i = et.offsets[size_t(e)]; i < et.offsets[size_t(e) + 1]; i++) {
          const size_t u = size_t(et.tris[size_t(i)]);
          if (u != t) {
            sum += normals[u] *
                   (areas[u] * std::exp(-distance_squared(normals[t], normals[u]) * inv));
          }
        }
      }
      next[t] = normalized(sum);
    });
    std::swap(normals, next);
  }
  return normals;
}

/**
 * The dihedral of an edge at the output scale: the area-weighted mean normals of the surface
 * within a radius of the edge's middle, reached across the surface, on either side of the plane
 * through the edge and its mean normal. A crease keeps its two planes at any scale; on scan noise
 * both sides average to the same normal, however steep each facet is (2026-11-15, rough and
 * decimated scans whose sharp edges formed one network over the whole surface).
 */
struct ScaleSides {
  std::vector<Vec3> normals, centroids;
  std::vector<double> areas;

  ScaleSides(const Surface &s, const std::vector<Vec3> &face_normals) : normals(face_normals)
  {
    centroids.resize(s.tris.size());
    areas.resize(s.tris.size());
    for (size_t t = 0; t < s.tris.size(); t++) {
      const Vec3 &a = s.positions[size_t(s.tris[t][0])], &b = s.positions[size_t(s.tris[t][1])],
                 &c = s.positions[size_t(s.tris[t][2])];
      centroids[t] = (a + b + c) / 3.0;
      areas[t] = 0.5 * length(cross(b - a, c - a));
    }
  }

  bool sharp(const EdgeTris &et, const int e, const double radius, const double cos_limit) const
  {
    const int t0 = et.tris[size_t(et.offsets[size_t(e)])],
              t1 = et.tris[size_t(et.offsets[size_t(e)] + 1)];
    const Vec3 middle = (centroids[size_t(t0)] + centroids[size_t(t1)]) * 0.5;
    const Vec3 mean = normals[size_t(t0)] + normals[size_t(t1)];
    Vec3 side = cross(mean, centroids[size_t(t1)] - centroids[size_t(t0)]);
    side = normalized(
        cross(side, mean)); /* in the mean tangent plane, pointing from t0's side to t1's */
    if (length_squared(side) == 0.0) {
      return true;
    }
    Vec3 sums[2] = {Vec3(), Vec3()};
    const double r2 = radius * radius;
    std::vector<int> stack = {t0, t1};
    std::unordered_set<int> seen = {t0, t1};
    while (!stack.empty() && seen.size() < MAX_SCALE_TRIANGLES) {
      const int t = stack.back();
      stack.pop_back();
      sums[dot(centroids[size_t(t)] - middle, side) < 0.0 ? 0 : 1] += normals[size_t(t)] *
                                                                      areas[size_t(t)];
      for (const int f : et.tri_edges[size_t(t)]) {
        for (int i = et.offsets[size_t(f)]; i < et.offsets[size_t(f) + 1]; i++) {
          const int u = et.tris[size_t(i)];
          if (distance_squared(centroids[size_t(u)], middle) <= r2 && seen.insert(u).second) {
            stack.push_back(u);
          }
        }
      }
    }
    if (length_squared(sums[0]) == 0.0 || length_squared(sums[1]) == 0.0) {
      return true;
    }
    return dot(normalized(sums[0]), normalized(sums[1])) < cos_limit;
  }
};

/**
 * Sharp lines shorter than one output edge cannot be represented: drop them (noise, small dents).
 * Judged per segment between ends and junctions, never per connected network: scan noise joins
 * single sharp edges into one network that is long in total but made of segments shorter than an
 * output edge (2026-11-15, decimated and photogrammetry scans with up to 90% of their edges
 * sharp). Short dangling segments go first, repeatedly, so the spurs noise leaves on a real crease
 * are cleared and the crease's pieces join again before short segments between junctions are
 * judged.
 */
static void drop_short_sharp_segments(const Surface &s,
                                      const EdgeTris &et,
                                      std::vector<uint8_t> &hard,
                                      const double min_length)
{
  const int nv = s.verts_num(), ne = et.edges_num();
  std::vector<int> offsets(size_t(nv) + 1), incident;
  std::vector<uint8_t> seen(static_cast<size_t>(ne));
  std::vector<int> segment;
  auto ends = [&](const int e) {
    return std::array<int, 2>{edge_key_v0(et.keys[size_t(e)]), edge_key_v1(et.keys[size_t(e)])};
  };
  std::vector<int> sharp;
  for (int e = 0; e < ne; e++) {
    if (hard[size_t(e)] == 2) {
      sharp.push_back(e);
    }
  }
  for (int pass = 0; pass < MAX_SEGMENT_PASSES && !sharp.empty(); pass++) {
    std::fill(offsets.begin(), offsets.end(), 0);
    for (const int e : sharp) {
      for (const int v : ends(e)) {
        offsets[size_t(v) + 1]++;
      }
    }
    for (int v = 0; v < nv; v++) {
      offsets[size_t(v) + 1] += offsets[size_t(v)];
    }
    incident.assign(size_t(offsets[size_t(nv)]), 0);
    {
      std::vector<int> fill(offsets.begin(), offsets.end() - 1);
      for (const int e : sharp) {
        for (const int v : ends(e)) {
          incident[size_t(fill[size_t(v)]++)] = e;
        }
      }
    }
    auto degree = [&](const int v) { return offsets[size_t(v) + 1] - offsets[size_t(v)]; };
    for (const int e : sharp) {
      seen[size_t(e)] = 0;
    }
    std::vector<int> short_dangling, short_inner;
    for (const int e0 : sharp) {
      if (seen[size_t(e0)]) {
        continue;
      }
      segment.assign(1, e0);
      seen[size_t(e0)] = 1;
      const std::array<int, 2> e0_ends = ends(e0);
      double length_sum = distance(s.positions[size_t(e0_ends[0])],
                                   s.positions[size_t(e0_ends[1])]);
      bool dangling = false;
      for (const int start : e0_ends) {
        int v = start, previous = e0;
        while (degree(v) == 2) {
          const int a = incident[size_t(offsets[size_t(v)])],
                    b = incident[size_t(offsets[size_t(v)] + 1)];
          const int e = a == previous ? b : a;
          if (seen[size_t(e)]) {
            break; /* a closed loop */
          }
          seen[size_t(e)] = 1;
          segment.push_back(e);
          const std::array<int, 2> ve = ends(e);
          const int next = ve[0] == v ? ve[1] : ve[0];
          length_sum += distance(s.positions[size_t(v)], s.positions[size_t(next)]);
          v = next;
          previous = e;
        }
        dangling |= degree(v) == 1;
      }
      if (length_sum < min_length) {
        std::vector<int> &into = dangling ? short_dangling : short_inner;
        into.insert(into.end(), segment.begin(), segment.end());
      }
    }
    const std::vector<int> &drop = short_dangling.empty() ? short_inner : short_dangling;
    if (drop.empty()) {
      break;
    }
    for (const int e : drop) {
      hard[size_t(e)] = 0;
    }
    sharp.erase(std::remove_if(
                    sharp.begin(), sharp.end(), [&](const int e) { return hard[size_t(e)] != 2; }),
                sharp.end());
  }
}

void detect_features(Surface &s,
                     const std::vector<std::array<int, 2>> &marked,
                     const double sharp_angle_degrees,
                     const double min_chain_length)
{
  EdgeTris et;
  et.build(s.tris);
  std::vector<uint8_t> hard(size_t(et.edges_num()),
                            0); /* 1 = kept whatever its length, 2 = sharp */
  for (int e = 0; e < et.edges_num(); e++) {
    if (et.count(e) != 2) {
      hard[size_t(e)] = 1; /* border or non-manifold junction */
    }
  }
  for (const auto &m : marked) {
    const int e = et.find(m[0], m[1]);
    if (e >= 0) {
      hard[size_t(e)] = 1;
    }
  }
  if (sharp_angle_degrees > 0.0) {
    const std::vector<Vec3> normals = filtered_face_normals(s, et);
    const double cos_limit = std::cos(sharp_angle_degrees * 3.14159265358979323846 / 180.0);
    std::vector<int> candidates;
    for (int e = 0; e < et.edges_num(); e++) {
      if (hard[size_t(e)] || et.count(e) != 2) {
        continue;
      }
      const int t0 = et.tris[size_t(et.offsets[size_t(e)])],
                t1 = et.tris[size_t(et.offsets[size_t(e)] + 1)];
      if (dot(normals[size_t(t0)], normals[size_t(t1)]) < cos_limit) {
        candidates.push_back(e);
      }
    }
    const ScaleSides sides(s, normals);
    std::vector<uint8_t> sharp(candidates.size(), 0);
    parallel_for(int64_t(candidates.size()), 64, [&](const int64_t i) {
      sharp[size_t(i)] = sides.sharp(
          et, candidates[size_t(i)], SCALE_RADIUS_OF_EDGE * min_chain_length, cos_limit);
    });
    for (size_t i = 0; i < candidates.size(); i++) {
      if (sharp[i]) {
        hard[size_t(candidates[i])] = 2;
      }
    }
    drop_short_sharp_segments(s, et, hard, min_chain_length);
  }
  s.feature_edges.clear();
  s.feature_pinned.clear();
  for (int e = 0; e < et.edges_num(); e++) {
    if (hard[size_t(e)]) {
      s.feature_edges.push_back(
          {edge_key_v0(et.keys[size_t(e)]), edge_key_v1(et.keys[size_t(e)])});
      s.feature_pinned.push_back(hard[size_t(e)] == 1);
    }
  }
}

/**
 * Each vertex's place on the feature lines (all of them, or the pinned ones only): on a line where
 * exactly two meet nearly straight, a corner where they end, branch or turn by more than
 * CORNER_TURN_DEGREES. Writes the line's direction into `dirs` when given.
 */
static std::vector<FeatureKind> classify_vertices(const Surface &s,
                                                  const bool pinned_only,
                                                  std::vector<Vec3> *dirs_out)
{
  const int nv = s.verts_num();
  std::vector<FeatureKind> kind(static_cast<size_t>(nv), FeatureKind::None);
  std::vector<int> count(static_cast<size_t>(nv), 0);
  std::vector<std::array<Vec3, 2>> dirs(static_cast<size_t>(nv));
  for (size_t i = 0; i < s.feature_edges.size(); i++) {
    if (pinned_only && !s.feature_pinned[i]) {
      continue;
    }
    const auto &f = s.feature_edges[i];
    const Vec3 d = normalized(s.positions[size_t(f[1])] - s.positions[size_t(f[0])]);
    for (int k = 0; k < 2; k++) {
      const int v = f[size_t(k)];
      if (count[size_t(v)] < 2) {
        dirs[size_t(v)][size_t(count[size_t(v)])] = k == 0 ? d : -d; /* outgoing */
      }
      count[size_t(v)]++;
    }
  }
  const double straight = -std::cos(CORNER_TURN_DEGREES * 3.14159265358979323846 / 180.0);
  for (int v = 0; v < nv; v++) {
    if (count[size_t(v)] == 0) {
      continue;
    }
    if (count[size_t(v)] == 2 && dot(dirs[size_t(v)][0], dirs[size_t(v)][1]) < straight) {
      kind[size_t(v)] = FeatureKind::Line;
      if (dirs_out) {
        const Vec3 d = normalized(
            project_tangent(dirs[size_t(v)][0] - dirs[size_t(v)][1], s.normals[size_t(v)]));
        (*dirs_out)[size_t(v)] = length_squared(d) > 0.0 ? d :
                                                           any_perpendicular(s.normals[size_t(v)]);
      }
    }
    else {
      kind[size_t(v)] = FeatureKind::Corner;
    }
  }
  return kind;
}

void Surface::finalize()
{
  const int nv = verts_num();
  normals.assign(size_t(nv), Vec3());
  areas.assign(size_t(nv), 0.0);
  total_area = 0.0;
  lo = Vec3(1e300, 1e300, 1e300);
  hi = Vec3(-1e300, -1e300, -1e300);
  for (const Vec3 &p : positions) {
    lo = min_v(lo, p);
    hi = max_v(hi, p);
  }
  for (const auto &t : tris) {
    const Vec3 c = cross(positions[size_t(t[1])] - positions[size_t(t[0])],
                         positions[size_t(t[2])] - positions[size_t(t[0])]);
    const double area = 0.5 * length(c);
    total_area += area;
    for (const int v : t) {
      normals[size_t(v)] += c; /* area weighted */
      areas[size_t(v)] += area / 3.0;
    }
  }
  for (Vec3 &n : normals) {
    n = normalized(n);
    if (length_squared(n) == 0.0) {
      n = Vec3(0, 0, 1);
    }
  }
  /* Vertex graph (CSR), sorted neighbours. */
  std::vector<std::pair<int, int>> pairs;
  pairs.reserve(tris.size() * 6);
  for (const auto &t : tris) {
    for (int c = 0; c < 3; c++) {
      pairs.emplace_back(t[size_t(c)], t[size_t((c + 1) % 3)]);
      pairs.emplace_back(t[size_t((c + 1) % 3)], t[size_t(c)]);
    }
  }
  std::sort(pairs.begin(), pairs.end());
  pairs.erase(std::unique(pairs.begin(), pairs.end()), pairs.end());
  adjacency_offsets.assign(size_t(nv) + 1, 0);
  adjacency.resize(pairs.size());
  for (size_t i = 0; i < pairs.size(); i++) {
    adjacency_offsets[size_t(pairs[i].first) + 1]++;
    adjacency[i] = pairs[i].second;
  }
  for (int v = 0; v < nv; v++) {
    adjacency_offsets[size_t(v) + 1] += adjacency_offsets[size_t(v)];
  }
  /* Feature kinds and directions. */
  feature_dir.assign(size_t(nv), Vec3());
  guided.assign(size_t(nv), 0);
  feature_kind = classify_vertices(*this, false, &feature_dir);
  pin_kind = classify_vertices(*this, true, nullptr);
}

void apply_guides(Surface &s, const std::vector<std::vector<Vec3>> &guides, const double radius)
{
  std::vector<Vec3> points;
  std::vector<std::array<int, 2>> segments;
  for (const auto &guide : guides) {
    for (size_t i = 0; i + 1 < guide.size(); i++) {
      if (distance_squared(guide[i], guide[i + 1]) > 0.0) {
        points.push_back(guide[i]);
        points.push_back(guide[i + 1]);
        segments.push_back({int(points.size()) - 2, int(points.size()) - 1});
      }
    }
  }
  if (segments.empty()) {
    return;
  }
  ClosestPointTree tree;
  tree.build_segments(points, segments);
  parallel_for(s.verts_num(), 1024, [&](const int64_t vi) {
    const size_t v = size_t(vi);
    if (s.feature_kind[v] != FeatureKind::None) {
      return;
    }
    const ClosestPointTree::Hit hit = tree.closest(s.positions[v], radius);
    if (hit.primitive < 0) {
      return;
    }
    const auto &seg = segments[size_t(hit.primitive)];
    const Vec3 d = normalized(
        project_tangent(points[size_t(seg[1])] - points[size_t(seg[0])], s.normals[v]));
    if (length_squared(d) > 0.0) {
      s.feature_dir[v] = d;
      s.guided[v] = 1;
    }
  });
}

}  // namespace blender::geometry::retopo
