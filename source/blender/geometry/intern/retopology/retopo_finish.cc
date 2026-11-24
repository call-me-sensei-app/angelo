/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup geo
 *
 * Quad split, projection, feature snapping and relaxation of the extracted mesh. See
 * `retopo_extract.hh`.
 */

#include <algorithm>

#include "retopo_edges.hh"
#include "retopo_extract.hh"
#include "retopo_parallel.hh"

namespace blender::geometry::retopo {

/** A split edge's midpoint stays on a feature line when it lands this close to one (share of the
 * edge length). */
static constexpr double FEATURE_SNAP = 0.25;
/** Share of the step towards the neighbours' average taken per relaxation pass. */
static constexpr double RELAX_STEP = 0.5;

/** Every n-gon becomes n quads around its centre, sharing one midpoint per edge. */
static void split_into_quads(Extracted &x)
{
  PolyMesh &m = x.mesh;
  PolyMesh out;
  out.positions = m.positions;
  std::vector<FeatureKind> kind = x.feature_kind;
  std::vector<std::pair<uint64_t, int>> midpoints;
  /* One midpoint per distinct edge, numbered in sorted edge order (deterministic). */
  std::vector<uint64_t> keys;
  for (int f = 0; f < m.faces_num(); f++) {
    const int n = m.face_size(f);
    for (int c = 0; c < n; c++) {
      keys.push_back(edge_key(m.corner_verts[size_t(m.face_offsets[size_t(f)] + c)],
                              m.corner_verts[size_t(m.face_offsets[size_t(f)] + (c + 1) % n)]));
    }
  }
  std::sort(keys.begin(), keys.end());
  keys.erase(std::unique(keys.begin(), keys.end()), keys.end());
  std::vector<int> mid(keys.size());
  for (size_t e = 0; e < keys.size(); e++) {
    const int a = edge_key_v0(keys[e]), b = edge_key_v1(keys[e]);
    mid[e] = int(out.positions.size());
    out.positions.push_back((m.positions[size_t(a)] + m.positions[size_t(b)]) * 0.5);
    const bool on_feature = kind[size_t(a)] != FeatureKind::None &&
                            kind[size_t(b)] != FeatureKind::None;
    kind.push_back(on_feature ? FeatureKind::Line : FeatureKind::None);
  }
  auto mid_of = [&](const int a, const int b) {
    return mid[size_t(std::lower_bound(keys.begin(), keys.end(), edge_key(a, b)) - keys.begin())];
  };
  for (int f = 0; f < m.faces_num(); f++) {
    const int n = m.face_size(f);
    const int *v = &m.corner_verts[size_t(m.face_offsets[size_t(f)])];
    Vec3 centre;
    for (int c = 0; c < n; c++) {
      centre += m.positions[size_t(v[c])];
    }
    const int centre_index = int(out.positions.size());
    out.positions.push_back(centre / double(n));
    kind.push_back(FeatureKind::None);
    for (int c = 0; c < n; c++) {
      const int prev = v[(c + n - 1) % n], cur = v[c], next = v[(c + 1) % n];
      const int quad[4] = {cur, mid_of(cur, next), centre_index, mid_of(prev, cur)};
      out.add_face(quad, 4);
    }
  }
  x.mesh = std::move(out);
  x.feature_kind = std::move(kind);
}

static std::vector<std::vector<int>> vertex_neighbours(const PolyMesh &m)
{
  std::vector<std::vector<int>> nb(m.positions.size());
  for (int f = 0; f < m.faces_num(); f++) {
    const int n = m.face_size(f);
    for (int c = 0; c < n; c++) {
      const int a = m.corner_verts[size_t(m.face_offsets[size_t(f)] + c)];
      const int b = m.corner_verts[size_t(m.face_offsets[size_t(f)] + (c + 1) % n)];
      nb[size_t(a)].push_back(b);
      nb[size_t(b)].push_back(a);
    }
  }
  for (auto &list : nb) {
    std::sort(list.begin(), list.end());
    list.erase(std::unique(list.begin(), list.end()), list.end());
  }
  return nb;
}

/** Per vertex: whether it lies on an open border of `m` (an edge only one face uses). */
static std::vector<uint8_t> border_vertices(const PolyMesh &m)
{
  std::vector<uint64_t> keys;
  for (int f = 0; f < m.faces_num(); f++) {
    const int n = m.face_size(f);
    for (int c = 0; c < n; c++) {
      keys.push_back(edge_key(m.corner_verts[size_t(m.face_offsets[size_t(f)] + c)],
                              m.corner_verts[size_t(m.face_offsets[size_t(f)] + (c + 1) % n)]));
    }
  }
  std::sort(keys.begin(), keys.end());
  std::vector<uint8_t> border(m.positions.size(), 0);
  for (size_t i = 0; i < keys.size();) {
    size_t j = i + 1;
    while (j < keys.size() && keys[j] == keys[i]) {
      j++;
    }
    if (j - i == 1) {
      border[size_t(edge_key_v0(keys[i]))] = 1;
      border[size_t(edge_key_v1(keys[i]))] = 1;
    }
    i = j;
  }
  return border;
}

static Vec3 triangle_normal(const Surface &s, const int tri)
{
  const auto &t = s.tris[size_t(tri)];
  return normalized(cross(s.positions[size_t(t[1])] - s.positions[size_t(t[0])],
                          s.positions[size_t(t[2])] - s.positions[size_t(t[0])]));
}

PolyMesh finish(const Surface &surface,
                const ClosestPointTree &tree,
                Extracted &x,
                const FinishSettings &settings)
{
  if (settings.pure_quads) {
    split_into_quads(x);
  }
  PolyMesh &m = x.mesh;
  ClosestPointTree features;
  features.build_segments(surface.positions, surface.feature_edges);

  auto project = [&](const size_t v, const Vec3 &p) -> Vec3 {
    switch (x.feature_kind[v]) {
      case FeatureKind::Corner:
        return m.positions[v]; /* a corner keeps the corner's own position */
      case FeatureKind::Line:
        if (!features.empty()) {
          return features.closest(p).point;
        }
        break;
      case FeatureKind::None:
        break;
    }
    return tree.closest(p).point;
  };

  /* A midpoint that does not land near a feature line is an ordinary vertex (it crossed a face,
   * not a line). A vertex on the result's open border is never one: no face lies beyond it, so it
   * stays on the line, which is what keeps a symmetry cut exactly on its plane for the mirror
   * (2026-11-15, a bent torus cut in X and Y). */
  if (!features.empty()) {
    const std::vector<uint8_t> border = border_vertices(m);
    parallel_for(int64_t(m.positions.size()), 512, [&](const int64_t vi) {
      const size_t v = size_t(vi);
      if (x.feature_kind[v] == FeatureKind::Line && !border[v] &&
          features.closest(m.positions[v], FEATURE_SNAP * settings.edge_length).primitive < 0)
      {
        x.feature_kind[v] = FeatureKind::None;
      }
    });
  }
  else {
    for (FeatureKind &k : x.feature_kind) {
      k = k == FeatureKind::Corner ? k : FeatureKind::None;
    }
  }
  parallel_for(int64_t(m.positions.size()), 512, [&](const int64_t vi) {
    m.positions[size_t(vi)] = project(size_t(vi), m.positions[size_t(vi)]);
  });

  const std::vector<std::vector<int>> nb = vertex_neighbours(m);
  std::vector<Vec3> next(m.positions.size());
  for (int it = 0; it < settings.relax_iterations; it++) {
    parallel_for(int64_t(m.positions.size()), 512, [&](const int64_t vi) {
      const size_t v = size_t(vi);
      const Vec3 &p = m.positions[v];
      next[v] = p;
      if (x.feature_kind[v] == FeatureKind::Corner || nb[v].empty()) {
        return;
      }
      Vec3 avg;
      int count = 0;
      for (const int j : nb[v]) {
        /* Along a feature line, only the line's own neighbours pull. */
        if (x.feature_kind[v] == FeatureKind::Line &&
            x.feature_kind[size_t(j)] == FeatureKind::None)
        {
          continue;
        }
        avg += m.positions[size_t(j)];
        count++;
      }
      if (count < 2) {
        return;
      }
      avg = avg / double(count);
      if (x.feature_kind[v] == FeatureKind::Line) {
        next[v] = project(v, p + (avg - p) * RELAX_STEP);
        return;
      }
      const ClosestPointTree::Hit hit = tree.closest(p);
      const Vec3 n = hit.primitive >= 0 ? triangle_normal(surface, hit.primitive) : Vec3();
      next[v] = project(v, p + project_tangent(avg - p, n) * RELAX_STEP);
    });
    std::swap(m.positions, next);
  }
  return std::move(m);
}

}  // namespace blender::geometry::retopo
