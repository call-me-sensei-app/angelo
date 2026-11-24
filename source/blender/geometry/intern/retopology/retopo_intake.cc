/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup geo
 *
 * Intake (weld, clean, orient, shells) and the symmetry cut. See `retopo_mesh.hh`.
 */

#include <algorithm>
#include <cmath>
#include <deque>
#include <unordered_map>

#include "retopo_edges.hh"
#include "retopo_mesh.hh"

namespace blender::geometry::retopo {

/** Welding tolerance and the degenerate-area threshold, relative to the bounding-box diagonal. */
static constexpr double WELD_RELATIVE = 1e-6;
static constexpr double DEGENERATE_AREA_RELATIVE = 1e-14;

static double tri_area(const Vec3 &a, const Vec3 &b, const Vec3 &c)
{
  return 0.5 * length(cross(b - a, c - a));
}

double surface_area(const std::vector<Vec3> &positions,
                    const std::vector<std::array<int, 3>> &tris)
{
  double area = 0.0;
  for (const auto &t : tris) {
    area += tri_area(positions[t[0]], positions[t[1]], positions[t[2]]);
  }
  return area;
}

/** Map every vertex to the first vertex within `tol` (index order), using a grid of cells of size
 * `tol`. */
static std::vector<int> weld_map(const std::vector<Vec3> &positions,
                                 const std::vector<uint8_t> &used,
                                 const Vec3 &lo,
                                 const double tol)
{
  std::vector<int> map(positions.size(), -1);
  std::unordered_map<uint64_t, std::vector<int>> grid;
  grid.reserve(positions.size());
  const double inv = 1.0 / tol;
  auto cell_key = [](int64_t x, int64_t y, int64_t z) {
    return (uint64_t(x & 0x1fffff) << 42) | (uint64_t(y & 0x1fffff) << 21) |
           uint64_t(z & 0x1fffff);
  };
  for (size_t v = 0; v < positions.size(); v++) {
    if (!used[v]) {
      continue;
    }
    const Vec3 rel = (positions[v] - lo) * inv;
    const int64_t cx = int64_t(std::floor(rel.x)), cy = int64_t(std::floor(rel.y)),
                  cz = int64_t(std::floor(rel.z));
    int found = -1;
    for (int64_t dx = -1; dx <= 1 && found < 0; dx++) {
      for (int64_t dy = -1; dy <= 1 && found < 0; dy++) {
        for (int64_t dz = -1; dz <= 1 && found < 0; dz++) {
          const auto it = grid.find(cell_key(cx + dx, cy + dy, cz + dz));
          if (it == grid.end()) {
            continue;
          }
          for (const int other : it->second) {
            if (distance_squared(positions[size_t(other)], positions[v]) <= tol * tol) {
              found = other;
              break;
            }
          }
        }
      }
    }
    if (found >= 0) {
      map[v] = found;
    }
    else {
      map[v] = int(v);
      grid[cell_key(cx, cy, cz)].push_back(int(v));
    }
  }
  return map;
}

/** Remove triangles where `keep` is false and vertices no triangle uses; remap `marked`. */
static void compact(Surface &s,
                    const std::vector<uint8_t> &keep,
                    std::vector<std::array<int, 2>> &marked)
{
  std::vector<std::array<int, 3>> tris;
  tris.reserve(s.tris.size());
  for (size_t t = 0; t < s.tris.size(); t++) {
    if (keep[t]) {
      tris.push_back(s.tris[t]);
    }
  }
  std::vector<int> remap(s.positions.size(), -1);
  std::vector<Vec3> positions;
  for (const auto &t : tris) {
    for (const int v : t) {
      if (remap[size_t(v)] < 0) {
        remap[size_t(v)] = int(positions.size());
        positions.push_back(s.positions[size_t(v)]);
      }
    }
  }
  for (auto &t : tris) {
    for (int &v : t) {
      v = remap[size_t(v)];
    }
  }
  std::vector<std::array<int, 2>> kept_marked;
  for (const auto &e : marked) {
    const int a = remap[size_t(e[0])], b = remap[size_t(e[1])];
    if (a >= 0 && b >= 0 && a != b) {
      kept_marked.push_back({std::min(a, b), std::max(a, b)});
    }
  }
  std::sort(kept_marked.begin(), kept_marked.end());
  kept_marked.erase(std::unique(kept_marked.begin(), kept_marked.end()), kept_marked.end());
  marked = std::move(kept_marked);
  s.positions = std::move(positions);
  s.tris = std::move(tris);
}

/** Does triangle `t` traverse the edge a->b in its own corner order? */
static bool traverses(const std::array<int, 3> &t, const int a, const int b)
{
  for (int c = 0; c < 3; c++) {
    if (t[c] == a && t[(c + 1) % 3] == b) {
      return true;
    }
  }
  return false;
}

/**
 * Manifold patches: triangles connected through edges with exactly two triangles. Returns the
 * patch of each triangle; makes the winding consistent inside each patch (breadth first from its
 * lowest triangle).
 */
static std::vector<int> orient_patches(Surface &s, const EdgeTris &et, int &patches_num)
{
  const int n = int(s.tris.size());
  std::vector<int> patch(size_t(n), -1);
  patches_num = 0;
  std::deque<int> queue;
  for (int seed = 0; seed < n; seed++) {
    if (patch[size_t(seed)] >= 0) {
      continue;
    }
    patch[size_t(seed)] = patches_num;
    queue.push_back(seed);
    while (!queue.empty()) {
      const int t = queue.front();
      queue.pop_front();
      for (int c = 0; c < 3; c++) {
        const int e = et.tri_edges[size_t(t)][size_t(c)];
        if (et.count(e) != 2) {
          continue;
        }
        const int u = et.tris[size_t(et.offsets[size_t(e)])] == t ?
                          et.tris[size_t(et.offsets[size_t(e)] + 1)] :
                          et.tris[size_t(et.offsets[size_t(e)])];
        if (u == t || patch[size_t(u)] >= 0) {
          continue;
        }
        const int a = s.tris[size_t(t)][size_t(c)], b = s.tris[size_t(t)][size_t((c + 1) % 3)];
        if (traverses(s.tris[size_t(u)], a, b)) {
          std::swap(s.tris[size_t(u)][1], s.tris[size_t(u)][2]);
        }
        patch[size_t(u)] = patches_num;
        queue.push_back(u);
      }
    }
    patches_num++;
  }
  return patch;
}

Surface intake(const TriMesh &input,
               const std::vector<std::array<int, 2>> &marked,
               const Params &params,
               IntakeReport &report,
               std::vector<std::array<int, 2>> &marked_out)
{
  report.input_vertices = int(input.positions.size());
  report.input_triangles = int(input.tris.size());
  Surface s;
  const int nv = int(input.positions.size());
  std::vector<uint8_t> used(size_t(nv), 0);
  std::vector<std::array<int, 3>> valid;
  for (const auto &t : input.tris) {
    bool ok = true;
    for (const int v : t) {
      ok = ok && v >= 0 && v < nv && std::isfinite(input.positions[size_t(v)].x) &&
           std::isfinite(input.positions[size_t(v)].y) &&
           std::isfinite(input.positions[size_t(v)].z);
    }
    if (ok) {
      valid.push_back(t);
      for (const int v : t) {
        used[size_t(v)] = 1;
      }
    }
  }
  Vec3 lo(1e300, 1e300, 1e300), hi(-1e300, -1e300, -1e300);
  for (int v = 0; v < nv; v++) {
    if (used[size_t(v)]) {
      lo = min_v(lo, input.positions[size_t(v)]);
      hi = max_v(hi, input.positions[size_t(v)]);
    }
  }
  if (valid.empty() || !(distance(lo, hi) > 0.0)) {
    report.degenerate_triangles = int(input.tris.size());
    marked_out.clear();
    return s;
  }
  const double diag = distance(lo, hi);

  /* Weld. */
  const std::vector<int> weld = weld_map(input.positions, used, lo, diag * WELD_RELATIVE);
  for (int v = 0; v < nv; v++) {
    report.welded_vertices += used[size_t(v)] && weld[size_t(v)] != v;
  }
  s.positions = input.positions;

  /* Degenerate and duplicate triangles. */
  const double min_area = DEGENERATE_AREA_RELATIVE * diag * diag;
  std::vector<std::pair<std::array<int, 3>, int>> sorted_keys;
  for (const auto &t0 : valid) {
    const std::array<int, 3> t = {weld[size_t(t0[0])], weld[size_t(t0[1])], weld[size_t(t0[2])]};
    if (t[0] == t[1] || t[1] == t[2] || t[2] == t[0] ||
        tri_area(s.positions[size_t(t[0])],
                 s.positions[size_t(t[1])],
                 s.positions[size_t(t[2])]) <= min_area)
    {
      report.degenerate_triangles++;
      continue;
    }
    std::array<int, 3> key = t;
    std::sort(key.begin(), key.end());
    sorted_keys.emplace_back(key, int(s.tris.size()));
    s.tris.push_back(t);
  }
  std::vector<uint8_t> keep(s.tris.size(), 1);
  std::sort(sorted_keys.begin(), sorted_keys.end());
  for (size_t i = 1; i < sorted_keys.size(); i++) {
    if (sorted_keys[i].first == sorted_keys[i - 1].first) {
      keep[size_t(sorted_keys[i].second)] = 0;
      report.duplicate_triangles++;
    }
  }
  std::vector<std::array<int, 2>> marked_welded;
  for (const auto &e : marked) {
    if (e[0] >= 0 && e[0] < nv && e[1] >= 0 && e[1] < nv) {
      marked_welded.push_back({weld[size_t(e[0])], weld[size_t(e[1])]});
    }
  }
  compact(s, keep, marked_welded);

  /* Orientation and non-manifold edges. */
  EdgeTris et;
  et.build(s.tris);
  for (int e = 0; e < et.edges_num(); e++) {
    report.non_manifold_edges += et.count(e) > 2;
    report.boundary_edges += et.count(e) == 1;
  }
  const std::vector<std::array<int, 3>> before = s.tris;
  int patches_num = 0;
  const std::vector<int> patch = orient_patches(s, et, patches_num);
  std::vector<double> patch_area(size_t(patches_num), 0.0), patch_volume(size_t(patches_num), 0.0);
  std::vector<uint8_t> patch_attached(size_t(patches_num), 0), patch_open(size_t(patches_num), 0);
  for (size_t t = 0; t < s.tris.size(); t++) {
    const Vec3 &a = s.positions[size_t(s.tris[t][0])], &b = s.positions[size_t(s.tris[t][1])],
               &c = s.positions[size_t(s.tris[t][2])];
    patch_area[size_t(patch[t])] += tri_area(a, b, c);
    patch_volume[size_t(patch[t])] += dot(a, cross(b, c)) / 6.0;
    for (int k = 0; k < 3; k++) {
      const int count = et.count(et.tri_edges[t][size_t(k)]);
      if (count > 2) {
        patch_attached[size_t(patch[t])] = 1;
      }
      if (count == 1) {
        patch_open[size_t(patch[t])] = 1;
      }
    }
  }
  /* Closed patches face outwards; count triangles whose winding was wrong (the minority of each
   * patch). */
  std::vector<int> patch_size(size_t(patches_num), 0), patch_diff(size_t(patches_num), 0);
  for (size_t t = 0; t < s.tris.size(); t++) {
    const int p = patch[t];
    /* Without a border the patch encloses a volume, even when a non-manifold edge joins it to
     * another. */
    if (!patch_open[size_t(p)] && patch_volume[size_t(p)] < 0.0) {
      std::swap(s.tris[t][1], s.tris[t][2]);
    }
    patch_size[size_t(p)]++;
    patch_diff[size_t(p)] += s.tris[t] != before[t];
  }
  for (int p = 0; p < patches_num; p++) {
    report.flipped_triangles += std::min(patch_diff[size_t(p)],
                                         patch_size[size_t(p)] - patch_diff[size_t(p)]);
  }

  /* Flaps: open patches attached through a non-manifold edge, smaller than one output face. */
  const double total_area = surface_area(s.positions, s.tris);
  const double face_area = total_area / std::max(params.target_faces, 1);
  keep.assign(s.tris.size(), 1);
  for (size_t t = 0; t < s.tris.size(); t++) {
    const int p = patch[t];
    if (patch_attached[size_t(p)] && patch_open[size_t(p)] && patch_area[size_t(p)] < face_area) {
      keep[t] = 0;
      report.flap_triangles++;
    }
  }
  compact(s, keep, marked_welded);

  /* Shells: triangles connected through any edge. */
  et.build(s.tris);
  UnionFind uf(int(s.tris.size()));
  for (int e = 0; e < et.edges_num(); e++) {
    for (int i = et.offsets[size_t(e)] + 1; i < et.offsets[size_t(e) + 1]; i++) {
      uf.join(et.tris[size_t(et.offsets[size_t(e)])], et.tris[size_t(i)]);
    }
  }
  std::unordered_map<int, double> shell_area;
  std::vector<int> roots;
  for (size_t t = 0; t < s.tris.size(); t++) {
    const int r = uf.find(int(t));
    const Vec3 &a = s.positions[size_t(s.tris[t][0])], &b = s.positions[size_t(s.tris[t][1])],
               &c = s.positions[size_t(s.tris[t][2])];
    if (!shell_area.count(r)) {
      roots.push_back(r);
    }
    shell_area[r] += tri_area(a, b, c);
  }
  report.shells = int(roots.size());
  const double kept_total = surface_area(s.positions, s.tris);
  keep.assign(s.tris.size(), 1);
  for (const int r : roots) {
    report.shells_dropped += shell_area[r] < params.min_shell_area_fraction * kept_total;
  }
  for (size_t t = 0; t < s.tris.size(); t++) {
    keep[t] = shell_area[uf.find(int(t))] >= params.min_shell_area_fraction * kept_total;
  }
  compact(s, keep, marked_welded);
  marked_out = marked_welded;
  s.lo = Vec3(1e300, 1e300, 1e300);
  s.hi = Vec3(-1e300, -1e300, -1e300);
  for (const Vec3 &p : s.positions) {
    s.lo = min_v(s.lo, p);
    s.hi = max_v(s.hi, p);
  }
  return s;
}

Surface clip_positive(const Surface &surface,
                      const int axis,
                      std::vector<std::array<int, 2>> &marked)
{
  Surface s;
  s.positions = surface.positions;
  const double tol = 1e-9 * std::max(surface.diagonal(), 1e-300);
  std::vector<int> sign(s.positions.size());
  for (size_t v = 0; v < s.positions.size(); v++) {
    double &c = s.positions[v][axis];
    if (std::fabs(c) <= tol) {
      c = 0.0;
    }
    sign[v] = c > 0.0 ? 1 : (c < 0.0 ? -1 : 0);
  }
  std::unordered_map<uint64_t, int> cut;
  auto cut_vertex = [&](const int a, const int b) {
    const uint64_t k = edge_key(a, b);
    const auto it = cut.find(k);
    if (it != cut.end()) {
      return it->second;
    }
    const int lo_v = std::min(a, b),
              hi_v = std::max(a, b); /* same point whichever triangle asks */
    const Vec3 &pa = s.positions[size_t(lo_v)], &pb = s.positions[size_t(hi_v)];
    const double t = pa[axis] / (pa[axis] - pb[axis]);
    Vec3 p = pa + (pb - pa) * t;
    p[axis] = 0.0;
    s.positions.push_back(p);
    sign.push_back(0);
    const int index = int(s.positions.size()) - 1;
    cut[k] = index;
    return index;
  };
  for (const auto &t : surface.tris) {
    int pos = 0, neg = 0;
    for (const int v : t) {
      pos += sign[size_t(v)] > 0;
      neg += sign[size_t(v)] < 0;
    }
    if (neg == 0 && pos > 0) {
      s.tris.push_back(t);
      continue;
    }
    if (pos == 0) {
      continue;
    }
    /* Sutherland-Hodgman against `axis >= 0`. */
    int poly[4], n = 0;
    for (int c = 0; c < 3; c++) {
      const int a = t[size_t(c)], b = t[size_t((c + 1) % 3)];
      if (sign[size_t(a)] >= 0) {
        poly[n++] = a;
      }
      if ((sign[size_t(a)] > 0 && sign[size_t(b)] < 0) ||
          (sign[size_t(a)] < 0 && sign[size_t(b)] > 0))
      {
        poly[n++] = cut_vertex(a, b);
      }
    }
    for (int i = 1; i + 1 < n; i++) {
      s.tris.push_back({poly[0], poly[i], poly[i + 1]});
    }
  }
  std::vector<uint8_t> keep(s.tris.size(), 1);
  std::vector<std::array<int, 2>> kept_marked;
  for (const auto &e : marked) {
    if (sign[size_t(e[0])] >= 0 && sign[size_t(e[1])] >= 0) {
      kept_marked.push_back(e);
    }
  }
  marked = kept_marked;
  compact(s, keep, marked);
  s.lo = Vec3(1e300, 1e300, 1e300);
  s.hi = Vec3(-1e300, -1e300, -1e300);
  for (const Vec3 &p : s.positions) {
    s.lo = min_v(s.lo, p);
    s.hi = max_v(s.hi, p);
  }
  return s;
}

}  // namespace blender::geometry::retopo
