/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup geo
 *
 * Edge-to-triangle incidence, built by sorting (deterministic, no hash-map iteration order).
 */

#pragma once

#include <algorithm>
#include <array>
#include <cstdint>
#include <vector>

namespace blender::geometry::retopo {

inline uint64_t edge_key(const int a, const int b)
{
  const uint32_t lo = uint32_t(std::min(a, b)), hi = uint32_t(std::max(a, b));
  return (uint64_t(lo) << 32) | uint64_t(hi);
}
inline int edge_key_v0(const uint64_t key)
{
  return int(key >> 32);
}
inline int edge_key_v1(const uint64_t key)
{
  return int(key & 0xffffffffu);
}

/** Every distinct edge of a triangle set with the triangles using it. */
struct EdgeTris {
  std::vector<uint64_t> keys; /* sorted, unique */
  std::vector<int> offsets;   /* keys.size() + 1 */
  std::vector<int> tris;      /* triangles of edge e: tris[offsets[e]..offsets[e + 1]] */
  std::vector<std::array<int, 3>> tri_edges; /* edge index of corner c's edge (c, c + 1) */

  void build(const std::vector<std::array<int, 3>> &triangles)
  {
    std::vector<std::pair<uint64_t, int>> items;
    items.reserve(triangles.size() * 3);
    for (size_t t = 0; t < triangles.size(); t++) {
      for (int c = 0; c < 3; c++) {
        items.emplace_back(edge_key(triangles[t][c], triangles[t][(c + 1) % 3]), int(t * 3 + c));
      }
    }
    std::sort(items.begin(), items.end());
    keys.clear();
    offsets.clear();
    tris.clear();
    tri_edges.assign(triangles.size(), {-1, -1, -1});
    for (size_t i = 0; i < items.size(); i++) {
      if (i == 0 || items[i].first != items[i - 1].first) {
        keys.push_back(items[i].first);
        offsets.push_back(int(tris.size()));
      }
      const int corner = items[i].second;
      tris.push_back(corner / 3);
      tri_edges[size_t(corner / 3)][size_t(corner % 3)] = int(keys.size()) - 1;
    }
    offsets.push_back(int(tris.size()));
  }
  int edges_num() const
  {
    return int(keys.size());
  }
  int count(const int e) const
  {
    return offsets[e + 1] - offsets[e];
  }
  /** Index of the edge `a b`, or -1. */
  int find(const int a, const int b) const
  {
    const uint64_t k = edge_key(a, b);
    const auto it = std::lower_bound(keys.begin(), keys.end(), k);
    return (it != keys.end() && *it == k) ? int(it - keys.begin()) : -1;
  }
};

/** Union-find with path halving; the smaller index is the root, so roots are deterministic. */
struct UnionFind {
  std::vector<int> parent;
  explicit UnionFind(const int n) : parent(size_t(n))
  {
    for (int i = 0; i < n; i++) {
      parent[size_t(i)] = i;
    }
  }
  int find(int i)
  {
    while (parent[size_t(i)] != i) {
      parent[size_t(i)] = parent[size_t(parent[size_t(i)])];
      i = parent[size_t(i)];
    }
    return i;
  }
  void join(int a, int b)
  {
    a = find(a);
    b = find(b);
    if (a != b) {
      parent[size_t(std::max(a, b))] = std::min(a, b);
    }
  }
};

}  // namespace blender::geometry::retopo
