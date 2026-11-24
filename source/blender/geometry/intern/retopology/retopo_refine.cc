/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup geo
 *
 * Refinement: split edges until every one is short enough for the position field to resolve the
 * output lattice. See `retopo_mesh.hh`.
 */

#include <algorithm>
#include <vector>

#include "retopo_edges.hh"
#include "retopo_mesh.hh"

namespace blender::geometry::retopo {

int refine(Surface &s, const double max_edge, const int max_triangles)
{
  int split_total = 0;
  const double max2 = max_edge * max_edge;
  for (int pass = 0; pass < 24; pass++) {
    EdgeTris et;
    et.build(s.tris);
    std::vector<int> mid(size_t(et.edges_num()), -1);
    int splits = 0;
    for (int e = 0; e < et.edges_num(); e++) {
      const int a = edge_key_v0(et.keys[size_t(e)]), b = edge_key_v1(et.keys[size_t(e)]);
      if (distance_squared(s.positions[size_t(a)], s.positions[size_t(b)]) > max2) {
        splits++;
      }
    }
    if (splits == 0 || int(s.tris.size()) + 3 * splits > max_triangles) {
      break;
    }
    for (int e = 0; e < et.edges_num(); e++) {
      const int a = edge_key_v0(et.keys[size_t(e)]), b = edge_key_v1(et.keys[size_t(e)]);
      if (distance_squared(s.positions[size_t(a)], s.positions[size_t(b)]) > max2) {
        mid[size_t(e)] = int(s.positions.size());
        s.positions.push_back((s.positions[size_t(a)] + s.positions[size_t(b)]) * 0.5);
      }
    }
    split_total += splits;
    std::vector<std::array<int, 3>> next;
    next.reserve(s.tris.size() + size_t(3 * splits));
    for (size_t t = 0; t < s.tris.size(); t++) {
      const auto &tri = s.tris[t];
      int m[3];
      int count = 0;
      for (int c = 0; c < 3; c++) {
        m[c] = mid[size_t(et.tri_edges[t][size_t(c)])];
        count += m[c] >= 0;
      }
      if (count == 0) {
        next.push_back(tri);
      }
      else if (count == 3) {
        next.push_back({tri[0], m[0], m[2]});
        next.push_back({tri[1], m[1], m[0]});
        next.push_back({tri[2], m[2], m[1]});
        next.push_back({m[0], m[1], m[2]});
      }
      else if (count == 1) {
        const int c = m[0] >= 0 ? 0 : (m[1] >= 0 ? 1 : 2);
        const int a = tri[size_t(c)], b = tri[size_t((c + 1) % 3)], o = tri[size_t((c + 2) % 3)];
        next.push_back({a, m[c], o});
        next.push_back({m[c], b, o});
      }
      else {
        /* Two split edges; rotate so the unsplit one is corner 2's edge (v2, v0). */
        const int c = m[0] < 0 ? 1 : (m[1] < 0 ? 2 : 0);
        const int v0 = tri[size_t(c)], v1 = tri[size_t((c + 1) % 3)],
                  v2 = tri[size_t((c + 2) % 3)];
        const int m01 = m[c], m12 = m[(c + 1) % 3];
        next.push_back({m01, v1, m12});
        /* Quad v0 m01 m12 v2: split along the shorter diagonal. */
        if (distance_squared(s.positions[size_t(v0)], s.positions[size_t(m12)]) <=
            distance_squared(s.positions[size_t(m01)], s.positions[size_t(v2)]))
        {
          next.push_back({v0, m01, m12});
          next.push_back({v0, m12, v2});
        }
        else {
          next.push_back({v0, m01, v2});
          next.push_back({m01, m12, v2});
        }
      }
    }
    s.tris = std::move(next);
    std::vector<std::pair<std::array<int, 2>, uint8_t>> features;
    for (size_t i = 0; i < s.feature_edges.size(); i++) {
      const auto &f = s.feature_edges[i];
      const uint8_t pinned = s.feature_pinned[i];
      const int e = et.find(f[0], f[1]);
      const int m = e >= 0 ? mid[size_t(e)] : -1;
      if (m < 0) {
        features.push_back({f, pinned});
      }
      else {
        features.push_back({{std::min(f[0], m), std::max(f[0], m)}, pinned});
        features.push_back({{std::min(f[1], m), std::max(f[1], m)}, pinned});
      }
    }
    std::sort(features.begin(), features.end());
    s.feature_edges.clear();
    s.feature_pinned.clear();
    for (const auto &f : features) {
      s.feature_edges.push_back(f.first);
      s.feature_pinned.push_back(f.second);
    }
  }
  return split_total;
}

}  // namespace blender::geometry::retopo
