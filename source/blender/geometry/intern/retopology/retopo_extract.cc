/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup geo
 *
 * Extraction of the output graph and its faces from the position field. See `retopo_extract.hh`.
 */

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <unordered_map>

#include "retopo_edges.hh"
#include "retopo_extract.hh"
#include "retopo_graph.hh"
#include "retopo_integer.hh"

namespace blender::geometry::retopo {

/** Clusters closer than this share of the lattice spacing, facing the same way, are one output
 * vertex. */
static constexpr double MERGE_DISTANCE = 0.3;
static constexpr double MERGE_NORMAL_DOT = 0.5;

void PolyMesh::add_face(const int *verts, const int size)
{
  corner_verts.insert(corner_verts.end(), verts, verts + size);
  face_offsets.push_back(int(corner_verts.size()));
}


static void sort_ring(Graph &g, const int v)
{
  const Vec3 &n = g.normals[size_t(v)];
  const Vec3 u = any_perpendicular(n), w = cross(n, u);
  std::vector<std::pair<double, int>> items;
  for (const int j : g.ring[size_t(v)]) {
    const Vec3 d = g.positions[size_t(j)] - g.positions[size_t(v)];
    items.emplace_back(std::atan2(dot(d, w), dot(d, u)), j);
  }
  std::sort(items.begin(), items.end());
  for (size_t i = 0; i < items.size(); i++) {
    g.ring[size_t(v)][i] = items[i].second;
  }
}

static int slot_of(const Graph &g, const int v, const int neighbour)
{
  const std::vector<int> &r = g.ring[size_t(v)];
  for (size_t i = 0; i < r.size(); i++) {
    if (r[i] == neighbour) {
      return int(i);
    }
  }
  return -1;
}

/**
 * Map each cluster to the first earlier cluster within `MERGE_DISTANCE * scale` whose normal
 * agrees, folding its kind in (a corner keeps its position). Returns the map; merged-away clusters
 * keep their slot, unused.
 */
static std::vector<int> merge_coincident(Graph &g, const double scale)
{
  const size_t n = g.positions.size();
  std::vector<int> map(n);
  const double cell = MERGE_DISTANCE * scale;
  const double limit2 = cell * cell;
  std::unordered_map<uint64_t, std::vector<int>> grid;
  auto key = [](int64_t x, int64_t y, int64_t z) {
    return (uint64_t(x & 0x1fffff) << 42) | (uint64_t(y & 0x1fffff) << 21) |
           uint64_t(z & 0x1fffff);
  };
  for (size_t c = 0; c < n; c++) {
    const Vec3 &p = g.positions[c];
    const int64_t cx = int64_t(std::floor(p.x / cell)), cy = int64_t(std::floor(p.y / cell)),
                  cz = int64_t(std::floor(p.z / cell));
    int found = -1;
    for (int64_t dx = -1; dx <= 1 && found < 0; dx++) {
      for (int64_t dy = -1; dy <= 1 && found < 0; dy++) {
        for (int64_t dz = -1; dz <= 1 && found < 0; dz++) {
          const auto it = grid.find(key(cx + dx, cy + dy, cz + dz));
          if (it == grid.end()) {
            continue;
          }
          for (const int other : it->second) {
            if (distance_squared(g.positions[size_t(other)], p) <= limit2 &&
                dot(g.normals[size_t(other)], g.normals[c]) > MERGE_NORMAL_DOT)
            {
              found = other;
              break;
            }
          }
        }
      }
    }
    if (found < 0) {
      map[c] = int(c);
      grid[key(cx, cy, cz)].push_back(int(c));
      continue;
    }
    map[c] = found;
    if (g.kind[c] == FeatureKind::Corner && g.kind[size_t(found)] != FeatureKind::Corner) {
      g.positions[size_t(found)] = p;
    }
    if (uint8_t(g.kind[c]) > uint8_t(g.kind[size_t(found)])) {
      g.kind[size_t(found)] = g.kind[c];
    }
  }
  return map;
}

/** Cluster the surface vertices by zero-step edges and collect the unit-step edges between
 * clusters (the steps made integer-consistent first, `retopo_integer.hh`). */
static Graph build_graph(const Surface &surface,
                         const Fields &fields,
                         const double scale,
                         LatticeEdges &lattice)
{
  const int nv = surface.verts_num();
  lattice = lattice_edges(surface, fields, scale);
  const std::vector<Vec3> o = positions_with_steps(surface, fields, lattice, scale);
  UnionFind uf(nv);
  std::vector<std::array<int, 2>> unit_edges;
  for (int e = 0; e < lattice.edges.edges_num(); e++) {
    const uint64_t key = lattice.edges.keys[size_t(e)];
    const LatticeStep &step = lattice.step[size_t(e)];
    if (step[0] == 0 && step[1] == 0) {
      uf.join(edge_key_v0(key), edge_key_v1(key));
    }
    else if (std::abs(step[0]) + std::abs(step[1]) == 1) {
      unit_edges.push_back({edge_key_v0(key), edge_key_v1(key)});
    }
  }
  Graph g;
  std::vector<int> cluster(size_t(nv), -1);
  std::vector<double> weight;
  std::vector<int> corner_of;
  for (int v = 0; v < nv; v++) {
    const int r = uf.find(v);
    if (cluster[size_t(r)] < 0) {
      cluster[size_t(r)] = int(g.positions.size());
      g.positions.emplace_back();
      g.normals.emplace_back();
      g.kind.push_back(FeatureKind::None);
      weight.push_back(0.0);
      corner_of.push_back(-1);
    }
    const int c = cluster[size_t(r)];
    cluster[size_t(v)] = c;
    const double a = surface.areas[size_t(v)];
    g.positions[size_t(c)] += o[size_t(v)] * a;
    g.normals[size_t(c)] += surface.normals[size_t(v)] * a;
    weight[size_t(c)] += a;
    if (surface.feature_kind[size_t(v)] == FeatureKind::Corner && corner_of[size_t(c)] < 0) {
      corner_of[size_t(c)] = v;
    }
    if (uint8_t(surface.feature_kind[size_t(v)]) > uint8_t(g.kind[size_t(c)])) {
      g.kind[size_t(c)] = surface.feature_kind[size_t(v)];
    }
  }
  for (size_t c = 0; c < g.positions.size(); c++) {
    g.positions[c] = corner_of[c] >= 0 ? surface.positions[size_t(corner_of[c])] :
                                         g.positions[c] / std::max(weight[c], 1e-300);
    const Vec3 n = normalized(g.normals[c]);
    g.normals[c] = length_squared(n) > 0.0 ? n : Vec3(0, 0, 1);
  }
  /* Clusters that reached the same lattice point without a zero-offset edge between them are one
   * vertex: merge those closer than MERGE_DISTANCE x scale facing the same way (so the two sides
   * of a thin part stay apart). */
  const std::vector<int> merged = merge_coincident(g, scale);
  for (int &c : cluster) {
    c = merged[size_t(c)];
  }
  std::vector<uint64_t> keys;
  for (const auto &e : unit_edges) {
    const int a = cluster[size_t(e[0])], b = cluster[size_t(e[1])];
    if (a != b) {
      keys.push_back(edge_key(a, b));
    }
  }
  std::sort(keys.begin(), keys.end());
  keys.erase(std::unique(keys.begin(), keys.end()), keys.end());
  g.ring.assign(g.positions.size(), {});
  for (const uint64_t k : keys) {
    g.ring[size_t(edge_key_v0(k))].push_back(edge_key_v1(k));
    g.ring[size_t(edge_key_v1(k))].push_back(edge_key_v0(k));
  }
  /* Dangling vertices (fewer than two edges) bound no face. */
  bool changed = true;
  while (changed) {
    changed = false;
    for (size_t v = 0; v < g.ring.size(); v++) {
      if (g.ring[v].size() == 1) {
        const int j = g.ring[v][0];
        g.ring[v].clear();
        auto &rj = g.ring[size_t(j)];
        rj.erase(std::remove(rj.begin(), rj.end(), int(v)), rj.end());
        changed = true;
      }
    }
  }
  for (size_t v = 0; v < g.ring.size(); v++) {
    sort_ring(g, int(v));
  }
  return g;
}

/** Split a closed walk at every vertex it visits twice into simple cycles. */
static std::vector<std::vector<int>> split_at_repeats(const std::vector<int> &cycle)
{
  std::vector<std::vector<int>> out;
  std::vector<std::vector<int>> stack = {cycle};
  while (!stack.empty()) {
    std::vector<int> c = std::move(stack.back());
    stack.pop_back();
    bool split = false;
    for (size_t i = 0; i < c.size() && !split; i++) {
      for (size_t j = i + 1; j < c.size(); j++) {
        if (c[i] == c[j]) {
          std::vector<int> inner(c.begin() + long(i), c.begin() + long(j));
          std::vector<int> outer(c.begin(), c.begin() + long(i));
          outer.insert(outer.end(), c.begin() + long(j), c.end());
          stack.push_back(std::move(inner));
          stack.push_back(std::move(outer));
          split = true;
          break;
        }
      }
    }
    if (!split) {
      out.push_back(std::move(c));
    }
  }
  return out;
}

Extracted extract(const Surface &surface,
                  const ClosestPointTree &tree,
                  const Fields &fields,
                  const double scale)
{
  LatticeEdges lattice;
  Graph g = build_graph(surface, fields, scale, lattice);
  Extracted result;
  result.position_singularities = lattice.charges;
  result.singularity_pairs_cancelled = lattice.pairs_cancelled;
  result.singularities_moved_out = lattice.charges_moved_out;
  std::vector<std::vector<uint8_t>> used(g.ring.size());
  for (size_t v = 0; v < g.ring.size(); v++) {
    used[v].assign(g.ring[v].size(), 0);
  }
  std::vector<int> cycle;
  std::vector<std::vector<int>> faces;
  for (size_t start_v = 0; start_v < g.ring.size(); start_v++) {
    for (size_t start_slot = 0; start_slot < g.ring[start_v].size(); start_slot++) {
      if (used[start_v][start_slot]) {
        continue;
      }
      /* Every half-edge lies on exactly one orbit of "next around the far end" (a permutation of
       * the half-edges), so an orbit is always traced whole and never meets another; long orbits
       * are borders, judged below. */
      cycle.clear();
      int a = int(start_v), slot = int(start_slot);
      do {
        used[size_t(a)][size_t(slot)] = 1;
        cycle.push_back(a);
        const int b = g.ring[size_t(a)][size_t(slot)];
        const int back = slot_of(g, b, a);
        const int deg = int(g.ring[size_t(b)].size());
        a = b;
        slot = (back - 1 + deg) % deg;
      } while (!(a == int(start_v) && slot == int(start_slot)));
      if (cycle.size() < 3) {
        continue;
      }
      /* A cycle through a vertex twice (a pinch) is several faces or borders: split it at each
       * repeat. */
      for (const std::vector<int> &simple : split_at_repeats(cycle)) {
        if (simple.size() < 3) {
          continue; /* there and back along one edge */
        }
        if (!is_face(g, tree, simple, scale)) {
          result.borders++;
          continue;
        }
        faces.push_back(simple);
      }
    }
  }
  result.holes_filled += fill_small_holes(g, tree, scale, faces);
  result.triangle_pairs_merged = merge_triangle_pairs(g, faces);
  for (const std::vector<int> &face : faces) {
    if (face.size() > 6) {
      result.holes_filled++;
    }
  }
  /* Keep the vertices faces use, in order of first use. */
  std::vector<int> remap(g.positions.size(), -1);
  for (const std::vector<int> &cycle_face : faces) {
    const int size = int(cycle_face.size());
    std::vector<int> face(static_cast<size_t>(size));
    for (int c = 0; c < size; c++) {
      const int v = cycle_face[size_t(c)];
      if (remap[size_t(v)] < 0) {
        remap[size_t(v)] = int(result.mesh.positions.size());
        result.mesh.positions.push_back(g.positions[size_t(v)]);
        result.feature_kind.push_back(g.kind[size_t(v)]);
      }
      face[size_t(c)] = remap[size_t(v)];
    }
    result.mesh.add_face(face.data(), size);
  }
  return result;
}

}  // namespace blender::geometry::retopo
