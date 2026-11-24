/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup geo
 *
 * Closing the holes the finished mesh has where its input has none. See `retopo_extract.hh`.
 *
 * A border of the result is either the input's own border, followed, or a hole: a lost face next
 * to a border, the notch a symmetry seam leaves when one half's border row dips, a gap where the
 * extraction refused a cycle. A border loop is the input's when most of its vertices lie within an
 * output edge of an input border loop the output can show (at least four edges around); any other
 * loop is a hole. A loop through a vertex where two border loops
 * touch is split into simple cycles first. (The earlier pass skipped every loop through such a
 * vertex, and asked instead whether a loop's centre lay on the surface, which also holds for the
 * rim of a small open patch: closed scans came out with 5 to 47 holes, 2026-11-18.)
 */

#include <algorithm>
#include <cmath>
#include <unordered_map>

#include "retopo_edges.hh"
#include "retopo_extract.hh"

namespace blender::geometry::retopo {

/** The longest hole closed (in edges). Longer border loops of a closed input outline a part
 * thinner than the output face that the lattice dropped (a strut of a CT-scanned bone); a cap that
 * large would stand far from the surface it replaces. */
static constexpr int MAX_HOLE = 48;
/** A result vertex within this share of the output edge of an input border lies on that border:
 * the result's border follows the input's to within an edge, coarser than it. */
static constexpr double BORDER_DISTANCE_OF_EDGE = 1.0;
/** An input border loop shorter than this many output edges is an opening below the output scale
 * (a quad mesh's smallest hole, one lost quad, has four edges): the result may close it, as it
 * closes any other structure smaller than a face (2026-11-18: a spinning wheel's 50 openings 1.5
 * to 3.6 edges around). */
static constexpr double MIN_BORDER_OF_EDGE = 4.0;

namespace {

/** The input's border loops of at least a given length, as edges in a uniform grid, for "is
 * there such a border near this point". */
class BorderGrid {
  double cell_;
  std::vector<std::array<Vec3, 2>> segments_;
  std::unordered_map<uint64_t, std::vector<int>> cells_;

  uint64_t key(const int x, const int y, const int z) const
  {
    return (uint64_t(uint32_t(x) & 0x1fffff) << 42) | (uint64_t(uint32_t(y) & 0x1fffff) << 21) |
           uint64_t(uint32_t(z) & 0x1fffff);
  }
  int coord(const double v) const
  {
    return int(std::floor(v / cell_));
  }

 public:
  BorderGrid(const Surface &input, const double cell, const double min_length) : cell_(cell)
  {
    EdgeTris et;
    et.build(input.tris);
    /* Border edges joined into loops (connected through their vertices), and each loop's length. */
    std::vector<int> root(input.positions.size());
    for (size_t v = 0; v < root.size(); v++) {
      root[v] = int(v);
    }
    auto find = [&](int v) {
      while (root[size_t(v)] != v) {
        root[size_t(v)] = root[size_t(root[size_t(v)])];
        v = root[size_t(v)];
      }
      return v;
    };
    std::vector<int> border;
    for (int e = 0; e < et.edges_num(); e++) {
      if (et.count(e) == 1) {
        border.push_back(e);
        root[size_t(find(edge_key_v0(et.keys[size_t(e)])))] = find(edge_key_v1(et.keys[size_t(e)]));
      }
    }
    std::unordered_map<int, double> loop_length;
    for (const int e : border) {
      const int a = edge_key_v0(et.keys[size_t(e)]), b = edge_key_v1(et.keys[size_t(e)]);
      loop_length[find(a)] += distance(input.positions[size_t(a)], input.positions[size_t(b)]);
    }
    for (const int e : border) {
      if (loop_length[find(edge_key_v0(et.keys[size_t(e)]))] < min_length) {
        continue;
      }
      const Vec3 &a = input.positions[size_t(edge_key_v0(et.keys[size_t(e)]))];
      const Vec3 &b = input.positions[size_t(edge_key_v1(et.keys[size_t(e)]))];
      const int s = int(segments_.size());
      segments_.push_back({a, b});
      /* Every cell the segment's bounding box touches. */
      const int x0 = coord(std::min(a[0], b[0])), x1 = coord(std::max(a[0], b[0]));
      const int y0 = coord(std::min(a[1], b[1])), y1 = coord(std::max(a[1], b[1]));
      const int z0 = coord(std::min(a[2], b[2])), z1 = coord(std::max(a[2], b[2]));
      for (int x = x0; x <= x1; x++) {
        for (int y = y0; y <= y1; y++) {
          for (int z = z0; z <= z1; z++) {
            cells_[key(x, y, z)].push_back(s);
          }
        }
      }
    }
  }

  bool empty() const
  {
    return segments_.empty();
  }

  /** Whether a border segment passes within `radius` (at most one cell) of `p`. */
  bool near(const Vec3 &p, const double radius) const
  {
    const int cx = coord(p[0]), cy = coord(p[1]), cz = coord(p[2]);
    for (int x = cx - 1; x <= cx + 1; x++) {
      for (int y = cy - 1; y <= cy + 1; y++) {
        for (int z = cz - 1; z <= cz + 1; z++) {
          const auto it = cells_.find(key(x, y, z));
          if (it == cells_.end()) {
            continue;
          }
          for (const int s : it->second) {
            const Vec3 &a = segments_[size_t(s)][0], &b = segments_[size_t(s)][1];
            const Vec3 ab = b - a;
            const double t = std::clamp(
                dot(p - a, ab) / std::max(length_squared(ab), 1e-300), 0.0, 1.0);
            if (distance_squared(p, a + ab * t) <= radius * radius) {
              return true;
            }
          }
        }
      }
    }
    return false;
  }
};

/** The border loops of `m` as simple cycles, each walked the way a face filling it would run. */
std::vector<std::vector<int>> border_cycles(const PolyMesh &m)
{
  std::vector<std::pair<uint64_t, std::array<int, 2>>> half;
  for (int f = 0; f < m.faces_num(); f++) {
    const int n = m.face_size(f);
    for (int c = 0; c < n; c++) {
      const int a = m.corner_verts[size_t(m.face_offsets[size_t(f)] + c)];
      const int b = m.corner_verts[size_t(m.face_offsets[size_t(f)] + (c + 1) % n)];
      half.push_back({edge_key(a, b), {a, b}});
    }
  }
  std::sort(half.begin(), half.end());
  /* A filling face runs each border edge the other way round: b -> a. */
  std::vector<std::vector<int>> out(m.positions.size());
  for (size_t i = 0; i < half.size(); i++) {
    const bool single = (i == 0 || half[i - 1].first != half[i].first) &&
                        (i + 1 == half.size() || half[i + 1].first != half[i].first);
    if (single) {
      out[size_t(half[i].second[1])].push_back(half[i].second[0]);
    }
  }
  std::vector<std::vector<int>> cycles;
  std::vector<int> place(m.positions.size(), -1); /* index on the current path, or -1 */
  for (size_t start = 0; start < out.size(); start++) {
    while (!out[start].empty()) {
      std::vector<int> path = {int(start)};
      place[start] = 0;
      int v = int(start);
      while (!out[size_t(v)].empty()) {
        const int w = out[size_t(v)].back();
        out[size_t(v)].pop_back();
        if (place[size_t(w)] >= 0) {
          /* Back at a vertex of the path: the part since it is a simple cycle. */
          const int from = place[size_t(w)];
          cycles.emplace_back(path.begin() + from, path.end());
          for (size_t i = size_t(from) + 1; i < path.size(); i++) {
            place[size_t(path[i])] = -1;
          }
          path.resize(size_t(from) + 1);
          v = w;
          continue;
        }
        place[size_t(w)] = int(path.size());
        path.push_back(w);
        v = w;
      }
      /* A chain that never closed (a border through a non-manifold edge): nothing to fill. */
      for (const int u : path) {
        place[size_t(u)] = -1;
      }
    }
  }
  return cycles;
}

}  // namespace

/** Fill the even `loop` (in filling order) with quads: split by the chord between two opposite
 * vertices (through a new vertex in its middle when that keeps both halves even) until each part
 * is one quad. The chord is the one through the loop's vertices on a symmetry plane when it has
 * them (it maps to itself, so a seam hole's fill stays mirrored); else the one with the fewest ends
 * already at valence four or more (an end below four lacks the edge the chord gives it), the
 * shortest of those. New vertices go on the surface. */
static void fill_even(PolyMesh &m,
                      std::vector<int> &valence,
                      const std::vector<int> &loop,
                      const ClosestPointTree &surface,
                      const double edge,
                      const int symmetry_axes)
{
  const int n = int(loop.size());
  if (n == 4) {
    m.add_face(loop.data(), 4);
    return;
  }
  const int half = n / 2;
  auto on_plane = [&](const Vec3 &p, const int axis) {
    return (symmetry_axes & (1 << axis)) && p[axis] == 0.0;
  };
  int best = 0, best_axis = -1, best_full = 3;
  double best_length = 1e300;
  for (int i = 0; i < half; i++) {
    const int va = loop[size_t(i)], vb = loop[size_t(i + half)];
    const Vec3 &a = m.positions[size_t(va)];
    const Vec3 &b = m.positions[size_t(vb)];
    for (int axis = 0; axis < 3; axis++) {
      if (on_plane(a, axis) && on_plane(b, axis) && best_axis < 0) {
        best = i;
        best_axis = axis;
      }
    }
    const int full = (valence[size_t(va)] >= 4) + (valence[size_t(vb)] >= 4);
    if (best_axis < 0 &&
        (full < best_full || (full == best_full && distance(a, b) < best_length)))
    {
      best_full = full;
      best_length = distance(a, b);
      best = i;
    }
  }
  const int i = best, j = best + half;
  /* Each part has half + 1 loop vertices and the chord's middle vertices: one when half is even. */
  std::vector<int> chord; /* from loop[i] towards loop[j] */
  if ((half + 1) % 2 == 1) {
    const Vec3 &a = m.positions[size_t(loop[size_t(i)])];
    const Vec3 &b = m.positions[size_t(loop[size_t(j)])];
    Vec3 mid = (a + b) * 0.5;
    const ClosestPointTree::Hit hit = surface.closest(mid, 2.0 * std::max(distance(a, b), edge));
    if (hit.primitive >= 0) {
      mid = hit.point;
    }
    if (best_axis >= 0) {
      mid[best_axis] = 0.0;
    }
    chord.push_back(int(m.positions.size()));
    m.positions.push_back(mid);
    valence.push_back(2);
  }
  valence[size_t(loop[size_t(i)])]++;
  valence[size_t(loop[size_t(j)])]++;
  std::vector<int> first, second;
  for (int k = i; k <= j; k++) {
    first.push_back(loop[size_t(k)]);
  }
  first.insert(first.end(), chord.rbegin(), chord.rend());
  for (int k = j; k <= i + n; k++) {
    second.push_back(loop[size_t(k % n)]);
  }
  second.insert(second.end(), chord.begin(), chord.end());
  fill_even(m, valence, first, surface, edge, symmetry_axes);
  fill_even(m, valence, second, surface, edge, symmetry_axes);
}

int close_holes(PolyMesh &m,
                const Surface &input,
                const ClosestPointTree &surface,
                const double edge,
                const int symmetry_axes)
{
  const double near = BORDER_DISTANCE_OF_EDGE * edge;
  const BorderGrid borders(input, std::max(near, 1e-12), MIN_BORDER_OF_EDGE * edge);
  /* Each vertex's edge count, kept up to date by the fills. */
  std::vector<int> valence(m.positions.size(), 0);
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
    keys.erase(std::unique(keys.begin(), keys.end()), keys.end());
    for (const uint64_t key : keys) {
      valence[size_t(edge_key_v0(key))]++;
      valence[size_t(edge_key_v1(key))]++;
    }
  }
  int closed = 0;
  for (std::vector<int> &loop : border_cycles(m)) {
    if (loop.size() < 3 || int(loop.size()) > MAX_HOLE) {
      continue;
    }
    if (!borders.empty()) {
      int beside = 0;
      for (const int v : loop) {
        beside += borders.near(m.positions[size_t(v)], near);
      }
      if (2 * beside >= int(loop.size())) {
        continue; /* the input's own border */
      }
    }
    if (loop.size() % 2 == 1) {
      /* An odd loop has no quad filling. */
      m.add_face(loop.data(), int(loop.size()));
    }
    else {
      fill_even(m, valence, loop, surface, edge, symmetry_axes);
    }
    closed++;
  }
  return closed;
}

}  // namespace blender::geometry::retopo
