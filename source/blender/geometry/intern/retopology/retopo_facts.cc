/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup geo
 *
 * The quality facts of a result (#Facts). The distances are a quick, self-contained version of the
 * game-ready check's Hausdorff (sampled at vertices and face centres both ways); the benchmark
 * grades with that check itself.
 */

#include <algorithm>
#include <cmath>

#include "retopo_bvh.hh"
#include "retopo_core.hh"
#include "retopo_edges.hh"
#include "retopo_parallel.hh"

namespace blender::geometry::retopo {

/** Largest and summed nearest distances from `points` to `tree`. */
static void sweep(const std::vector<Vec3> &points,
                  const ClosestPointTree &tree,
                  double &max_d,
                  double &sum_d)
{
  const int64_t n = int64_t(points.size());
  const int64_t chunk = 4096;
  const int64_t chunks = (n + chunk - 1) / chunk;
  std::vector<double> maxes(size_t(chunks), 0.0), sums(size_t(chunks), 0.0);
  parallel_for(chunks, 1, [&](const int64_t c) {
    for (int64_t i = c * chunk; i < std::min(n, (c + 1) * chunk); i++) {
      const double d = std::sqrt(tree.closest(points[size_t(i)]).distance_squared);
      maxes[size_t(c)] = std::max(maxes[size_t(c)], d);
      sums[size_t(c)] += d;
    }
  });
  for (int64_t c = 0; c < chunks; c++) {
    max_d = std::max(max_d, maxes[size_t(c)]);
    sum_d += sums[size_t(c)];
  }
}

Facts measure(const PolyMesh &mesh, const TriMesh &reference, const int target_faces)
{
  Facts f;
  f.faces = mesh.faces_num();
  for (int i = 0; i < f.faces; i++) {
    const int n = mesh.face_size(i);
    f.quads += n == 4;
    f.triangles += n == 3;
    f.ngons += n > 4;
  }
  f.quad_ratio = f.faces > 0 ? double(f.quads) / f.faces : 0.0;
  f.face_count_error = target_faces > 0 ? double(f.faces) / target_faces - 1.0 : 0.0;

  /* Edges, valences, borders. */
  std::vector<std::pair<uint64_t, int>> uses;
  for (int i = 0; i < f.faces; i++) {
    const int n = mesh.face_size(i);
    for (int c = 0; c < n; c++) {
      uses.emplace_back(
          edge_key(mesh.corner_verts[size_t(mesh.face_offsets[size_t(i)] + c)],
                   mesh.corner_verts[size_t(mesh.face_offsets[size_t(i)] + (c + 1) % n)]),
          1);
    }
  }
  std::sort(uses.begin(), uses.end());
  const size_t nv = mesh.positions.size();
  std::vector<int> valence(nv, 0);
  std::vector<uint8_t> border(nv, 0), used(nv, 0);
  for (size_t i = 0; i < uses.size();) {
    size_t j = i;
    while (j < uses.size() && uses[j].first == uses[i].first) {
      j++;
    }
    const int count = int(j - i);
    const int a = edge_key_v0(uses[i].first), b = edge_key_v1(uses[i].first);
    valence[size_t(a)]++;
    valence[size_t(b)]++;
    used[size_t(a)] = used[size_t(b)] = 1;
    if (count == 1) {
      border[size_t(a)] = border[size_t(b)] = 1;
    }
    f.non_manifold_edges += count > 2;
    i = j;
  }
  for (size_t v = 0; v < nv; v++) {
    if (!used[v]) {
      continue;
    }
    f.vertices++;
    if (border[v]) {
      f.boundary_vertices++;
      continue;
    }
    const int k = valence[v] <= 3 ? 0 : (valence[v] == 4 ? 1 : (valence[v] == 5 ? 2 : 3));
    f.interior_valence[size_t(k)]++;
    f.irregular_vertices += valence[v] != 4;
  }

  /* Distances, both ways. */
  if (reference.tris.empty() || f.faces == 0) {
    return f;
  }
  Vec3 lo(1e300, 1e300, 1e300), hi(-1e300, -1e300, -1e300);
  for (const auto &t : reference.tris) {
    for (const int v : t) {
      lo = min_v(lo, reference.positions[size_t(v)]);
      hi = max_v(hi, reference.positions[size_t(v)]);
    }
  }
  f.bounds_diagonal = distance(lo, hi);
  ClosestPointTree ref_tree;
  ref_tree.build_triangles(reference.positions, reference.tris);
  std::vector<std::array<int, 3>> out_tris;
  std::vector<Vec3> out_points;
  for (size_t v = 0; v < nv; v++) {
    if (used[v]) {
      out_points.push_back(mesh.positions[v]);
    }
  }
  for (int i = 0; i < f.faces; i++) {
    const int n = mesh.face_size(i);
    const int *c = &mesh.corner_verts[size_t(mesh.face_offsets[size_t(i)])];
    Vec3 centre;
    for (int k = 0; k < n; k++) {
      centre += mesh.positions[size_t(c[k])];
    }
    out_points.push_back(centre / double(n));
    for (int k = 1; k + 1 < n; k++) {
      out_tris.push_back({c[0], c[k], c[k + 1]});
    }
  }
  ClosestPointTree out_tree;
  out_tree.build_triangles(mesh.positions, out_tris);
  std::vector<Vec3> ref_points;
  std::vector<uint8_t> ref_used(reference.positions.size(), 0);
  for (const auto &t : reference.tris) {
    for (const int v : t) {
      if (!ref_used[size_t(v)]) {
        ref_used[size_t(v)] = 1;
        ref_points.push_back(reference.positions[size_t(v)]);
      }
    }
    ref_points.push_back((reference.positions[size_t(t[0])] + reference.positions[size_t(t[1])] +
                          reference.positions[size_t(t[2])]) /
                         3.0);
  }
  double max_d = 0.0, sum_out = 0.0, sum_ref = 0.0;
  sweep(out_points, ref_tree, max_d, sum_out);
  sweep(ref_points, out_tree, max_d, sum_ref);
  const double diag = std::max(f.bounds_diagonal, 1e-300);
  f.hausdorff_max_of_diag = max_d / diag;
  /* Mean of the two directions' means, so neither side's sample count dominates. */
  f.hausdorff_mean_of_diag =
      0.5 * (sum_out / double(out_points.size()) + sum_ref / double(ref_points.size())) / diag;
  return f;
}

}  // namespace blender::geometry::retopo
