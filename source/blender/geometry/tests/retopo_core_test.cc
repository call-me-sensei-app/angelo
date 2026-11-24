/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Unit tests of the retopology core (`intern/retopology`). Every input is generated in code. The
 * bars below are the quality the feature promises for any input of the kind (closed, open, sharp,
 * noisy, broken); none is tuned to one shape: each property is asserted on several unlike shapes.
 */

#include <algorithm>
#include <map>
#include <set>
#include <utility>

#include "retopo_test_meshes.hh"

#include "testing/testing.h"

namespace blender::geometry::retopo::test {

/** Sum over vertices of (4 - valence) for a closed pure-quad mesh: equals 4 x Euler
 * characteristic. */
static int valence_defect_sum(const PolyMesh &m)
{
  std::vector<int> valence(m.positions.size(), 0);
  std::set<std::pair<int, int>> edges;
  for (int f = 0; f < m.faces_num(); f++) {
    const int n = m.face_size(f);
    for (int c = 0; c < n; c++) {
      const int a = m.corner_verts[m.face_offsets[f] + c];
      const int b = m.corner_verts[m.face_offsets[f] + (c + 1) % n];
      edges.insert({std::min(a, b), std::max(a, b)});
    }
  }
  for (const auto &e : edges) {
    valence[e.first]++;
    valence[e.second]++;
  }
  int sum = 0;
  for (const int v : valence) {
    if (v > 0) {
      sum += 4 - v;
    }
  }
  return sum;
}

static int boundary_edges(const PolyMesh &m)
{
  std::map<std::pair<int, int>, int> uses;
  for (int f = 0; f < m.faces_num(); f++) {
    const int n = m.face_size(f);
    for (int c = 0; c < n; c++) {
      const int a = m.corner_verts[m.face_offsets[f] + c];
      const int b = m.corner_verts[m.face_offsets[f] + (c + 1) % n];
      uses[{std::min(a, b), std::max(a, b)}]++;
    }
  }
  int n = 0;
  for (const auto &item : uses) {
    n += item.second == 1;
  }
  return n;
}

static double point_segment_distance(const Vec3 &p, const Vec3 &a, const Vec3 &b)
{
  const Vec3 ab = b - a;
  const double t = std::clamp(dot(p - a, ab) / std::max(length_squared(ab), 1e-300), 0.0, 1.0);
  return distance(p, a + ab * t);
}

/** Distance from `p` to the nearest edge of `m`. */
static double distance_to_edges(const PolyMesh &m, const Vec3 &p)
{
  double best = 1e300;
  for (int f = 0; f < m.faces_num(); f++) {
    const int n = m.face_size(f);
    for (int c = 0; c < n; c++) {
      const Vec3 &a = m.positions[m.corner_verts[m.face_offsets[f] + c]];
      const Vec3 &b = m.positions[m.corner_verts[m.face_offsets[f] + (c + 1) % n]];
      best = std::min(best, point_segment_distance(p, a, b));
    }
  }
  return best;
}

static double nearest_vertex_distance(const PolyMesh &m, const Vec3 &p)
{
  double best = 1e300;
  for (const Vec3 &q : m.positions) {
    best = std::min(best, distance(p, q));
  }
  return best;
}

static Params params_for(const int faces)
{
  Params p;
  p.target_faces = faces;
  return p;
}

/* -------------------------------------------------------------------- */
/** \name Closed surfaces: valid pure-quad output of the right topology and size
 * \{ */

TEST(retopo_core, sphere_is_closed_pure_quad_genus_zero)
{
  const TriMesh in = icosphere(5);
  const Result r = remesh(in, params_for(1500));
  ASSERT_EQ(r.status, Status::Ok);
  EXPECT_EQ(r.facts.quad_ratio, 1.0);
  EXPECT_EQ(r.facts.non_manifold_edges, 0);
  EXPECT_EQ(boundary_edges(r.mesh), 0);
  EXPECT_EQ(valence_defect_sum(r.mesh), 8); /* 4 x chi(sphere) */
  EXPECT_LT(std::abs(r.facts.face_count_error), 0.2);
  EXPECT_LT(r.facts.hausdorff_max_of_diag, 0.01);
  /* A sphere needs 8 valence-3 poles; a clean remesher stays within a small multiple. */
  EXPECT_LE(r.facts.irregular_vertices, 32);
}

TEST(retopo_core, torus_is_closed_pure_quad_genus_one_and_nearly_regular)
{
  const TriMesh in = torus(96, 40);
  const Result r = remesh(in, params_for(2000));
  ASSERT_EQ(r.status, Status::Ok);
  EXPECT_EQ(r.facts.quad_ratio, 1.0);
  EXPECT_EQ(boundary_edges(r.mesh), 0);
  EXPECT_EQ(valence_defect_sum(r.mesh), 0); /* 4 x chi(torus) */
  EXPECT_LT(std::abs(r.facts.face_count_error), 0.2);
  EXPECT_LT(r.facts.hausdorff_max_of_diag, 0.01);
  /* A torus admits a fully regular quad mesh: irregular vertices come in +/- pairs and must stay
   * rare. */
  EXPECT_LE(r.facts.irregular_vertices, r.facts.vertices / 50);
}

TEST(retopo_core, parts_curved_tighter_than_a_face_stay_closed)
{
  /* Where the surface bends within about one output face (fingers, ears, horns, thin tubes), a
   * quad's middle lies well inside the surface; such quads were taken for borders and left holes
   * in closed scans (2026-11-15: a closed armadillo came out with 9-12 holes). */
  const std::pair<TriMesh, int> cases[] = {{icosphere(5), 96},
                                           {torus(96, 40, 1.0, 0.12), 600},
                                           {torus(64, 64, 1.0, 0.45), 120}};
  for (size_t i = 0; i < std::size(cases); i++) {
    const Result r = remesh(cases[i].first, params_for(cases[i].second));
    ASSERT_EQ(r.status, Status::Ok) << i;
    EXPECT_EQ(boundary_edges(r.mesh), 0) << i;
    EXPECT_EQ(r.facts.quad_ratio, 1.0) << i;
  }
}

TEST(retopo_core, face_count_follows_target_across_scales)
{
  for (const int target : {400, 2000, 8000}) {
    const Result r = remesh(icosphere(6), params_for(target));
    ASSERT_EQ(r.status, Status::Ok) << target;
    EXPECT_LT(std::abs(r.facts.face_count_error), 0.2) << target;
  }
}

TEST(retopo_core, clean_surfaces_meet_the_face_target_in_one_solve)
{
  /* A surface the field covers fully yields one face per lattice cell, so the first spacing should
   * be kept: each re-solve adds a whole field solve to the run. Removing the half row of every pole
   * pair cost clean surfaces about a quarter of their faces and forced a re-solve (2026-11-15). */
  const std::pair<TriMesh, int> cases[] = {{icosphere(5), 1500},
                                           {torus(96, 40), 2000},
                                           {torus(64, 32, 1.0, 0.2), 800},
                                           {box(24), 600}};
  for (size_t i = 0; i < std::size(cases); i++) {
    const Result r = remesh(cases[i].first, params_for(cases[i].second));
    ASSERT_EQ(r.status, Status::Ok) << i;
    EXPECT_EQ(r.field_solves, 1) << i;
    EXPECT_LT(std::abs(r.facts.face_count_error), 0.1) << i;
  }
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name Hard surfaces: flow and vertices follow sharp edges and corners
 * \{ */

TEST(retopo_core, box_edges_and_corners_are_kept)
{
  const TriMesh in = box(24);
  const Result r = remesh(in, params_for(600));
  ASSERT_EQ(r.status, Status::Ok);
  EXPECT_EQ(r.facts.quad_ratio, 1.0);
  EXPECT_EQ(valence_defect_sum(r.mesh), 8);
  const double diag = diagonal(in);
  /* Every corner of the box has an output vertex on it. */
  for (int i = 0; i < 8; i++) {
    const Vec3 corner(i & 1 ? 1 : -1, i & 2 ? 1 : -1, i & 4 ? 1 : -1);
    EXPECT_LT(nearest_vertex_distance(r.mesh, corner), 0.01 * diag) << i;
  }
  /* Every point of the 12 box edges lies on an output edge. */
  int missed = 0, sampled = 0;
  for (int axis = 0; axis < 3; axis++) {
    for (int k = 0; k < 4; k++) {
      for (int s = 1; s < 20; s++) {
        Vec3 p;
        p[axis] = -1.0 + 2.0 * s / 20.0;
        p[(axis + 1) % 3] = k & 1 ? 1 : -1;
        p[(axis + 2) % 3] = k & 2 ? 1 : -1;
        sampled++;
        missed += distance_to_edges(r.mesh, p) > 0.005 * diag;
      }
    }
  }
  EXPECT_EQ(missed, 0) << sampled;
  /* Flat faces of a box are best covered by a regular grid: poles only at the 8 corners. */
  EXPECT_LE(r.facts.irregular_vertices, 8);
}

TEST(retopo_core, marked_edges_are_followed_on_a_smooth_surface)
{
  /* Edges in the sphere's z = 0 plane marked by the user: no geometric feature there, only the
   * mark. (On an icosphere those edges form two arcs of the equator, not the whole circle.) */
  TriMesh in = icosphere(5);
  Constraints constraints;
  std::map<std::pair<int, int>, int> seen;
  for (const auto &t : in.tris) {
    for (int c = 0; c < 3; c++) {
      const int a = t[c], b = t[(c + 1) % 3];
      if (std::abs(in.positions[a].z) < 1e-9 && std::abs(in.positions[b].z) < 1e-9 &&
          !seen.count({std::min(a, b), std::max(a, b)}))
      {
        seen[{std::min(a, b), std::max(a, b)}] = 1;
        constraints.marked_edges.push_back({a, b});
      }
    }
  }
  ASSERT_GT(constraints.marked_edges.size(), 50u);
  const Result r = remesh(in, params_for(1500), constraints);
  ASSERT_EQ(r.status, Status::Ok);
  /* Every marked edge's midpoint lies on an output edge (chord sag of an output edge on the unit
   * sphere is ~1e-3). */
  int missed = 0;
  for (const auto &e : constraints.marked_edges) {
    const Vec3 mid = normalized((in.positions[e[0]] + in.positions[e[1]]) * 0.5);
    missed += distance_to_edges(r.mesh, mid) > 0.01;
  }
  EXPECT_EQ(missed, 0);
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name Broken input, as AI generators and scanners deliver it
 * \{ */

TEST(retopo_core, triangle_soup_with_flips_fins_and_duplicates_gives_a_clean_closed_result)
{
  const TriMesh clean = icosphere(5);
  const TriMesh in = to_soup(flip_some(add_fin_and_duplicate(clean), 7));
  const Result r = remesh(in, params_for(1500));
  ASSERT_EQ(r.status, Status::Ok);
  EXPECT_EQ(r.intake.welded_vertices, int(in.positions.size() - clean.positions.size() - 1));
  EXPECT_GE(r.intake.duplicate_triangles, 1);
  EXPECT_GE(r.intake.non_manifold_edges, 1);
  EXPECT_GT(r.intake.flipped_triangles, 0);
  EXPECT_EQ(r.facts.quad_ratio, 1.0);
  EXPECT_EQ(r.facts.non_manifold_edges, 0);
  EXPECT_LT(r.facts.hausdorff_max_of_diag, 0.02);
}

TEST(retopo_core, every_shell_is_remeshed_and_tiny_shells_are_reported)
{
  TriMesh in = join(icosphere(5, 1.0, {-2, 0, 0}), box(16, {1, -1, -1}, {3, 1, 1}));
  in = join(in, icosphere(1, 0.001, {0, 5, 0})); /* a speck far below the size threshold */
  Params p = params_for(2000);
  p.min_shell_area_fraction = 1e-4;
  const Result r = remesh(in, p);
  ASSERT_EQ(r.status, Status::Ok);
  EXPECT_EQ(r.intake.shells, 3);
  EXPECT_EQ(r.intake.shells_dropped, 1);
  EXPECT_EQ(valence_defect_sum(r.mesh), 16); /* two closed genus-0 shells */
  EXPECT_LT(r.facts.hausdorff_max_of_diag, 0.02);
}

TEST(retopo_core, scan_noise_below_the_target_scale_does_not_steer_the_result)
{
  const TriMesh in = radial_noise(icosphere(6), 0.004);
  const Result r = remesh(in, params_for(1500));
  ASSERT_EQ(r.status, Status::Ok);
  EXPECT_EQ(r.facts.quad_ratio, 1.0);
  EXPECT_EQ(valence_defect_sum(r.mesh), 8);
  EXPECT_LE(r.facts.irregular_vertices, 40);
}

TEST(retopo_core, rough_scan_noise_makes_no_feature_lines)
{
  /* Noise well below the output edge but with steep facets, as on decimated and photogrammetry
   * scans: many single edges pass the sharp-angle test, joined into one network. None of it is a
   * crease the result can follow, so it must neither pin the lattice nor cost faces (2026-11-15
   * dev scans: up to 90% of edges were marked). */
  const TriMesh in = radial_noise(icosphere(6), 0.012);
  const Result r = remesh(in, params_for(1500));
  ASSERT_EQ(r.status, Status::Ok);
  EXPECT_EQ(r.facts.quad_ratio, 1.0);
  EXPECT_LT(r.intake.feature_edges, 50);
  EXPECT_LT(std::abs(r.facts.face_count_error), 0.2);
}

TEST(retopo_core, rough_scan_noise_does_not_steer_the_flow)
{
  /* The same rough sphere: the flow should be as regular as on the gentle noise above. Open
   * (2026-11-15): the principal directions are read from noise (57 irregular with curvature
   * following, 35 without). */
  const Result r = remesh(radial_noise(icosphere(6), 0.012), params_for(1500));
  ASSERT_EQ(r.status, Status::Ok);
  EXPECT_LE(r.facts.irregular_vertices, 40);
}

TEST(retopo_core, structure_below_the_output_scale_does_not_cost_the_face_target)
{
  /* An outer surface filled with closed parts far smaller than an output face, like the pores and
   * cavities of a CT scan: about half the area cannot be represented, yet the result should have
   * the faces asked for, spent where they can be (2026-11-15, CT-scanned bones carry 3 to 4 times
   * their outer area below the output scale; 60-90% of faces were lost). */
  TriMesh in = icosphere(5);
  for (uint64_t i = 0; i < 1000; i++) {
    const Vec3 c(hash_noise(3 * i), hash_noise(3 * i + 1), hash_noise(3 * i + 2));
    in = join(in, icosphere(1, 0.03, c * 0.45));
  }
  const Result r = remesh(in, params_for(1500));
  ASSERT_EQ(r.status, Status::Ok);
  EXPECT_EQ(r.facts.quad_ratio, 1.0);
  EXPECT_LT(std::abs(r.facts.face_count_error), 0.2);
}

TEST(retopo_core, open_surface_keeps_its_boundary)
{
  /* A box without its top face: an open surface with one boundary loop. */
  TriMesh in = box(20);
  std::vector<std::array<int, 3>> kept;
  for (const auto &t : in.tris) {
    if (!(in.positions[t[0]].z > 0.999 && in.positions[t[1]].z > 0.999 &&
          in.positions[t[2]].z > 0.999))
    {
      kept.push_back(t);
    }
  }
  in.tris = kept;
  const Result r = remesh(in, params_for(500));
  ASSERT_EQ(r.status, Status::Ok);
  EXPECT_EQ(r.facts.quad_ratio, 1.0);
  EXPECT_GT(boundary_edges(r.mesh), 0);
  /* The rim stays where it was. */
  int missed = 0;
  for (int i = 0; i < 40; i++) {
    const double t = -1.0 + 2.0 * (i + 0.5) / 40;
    missed += distance_to_edges(r.mesh, {t, -1, 1}) > 0.01;
    missed += distance_to_edges(r.mesh, {1, t, 1}) > 0.01;
  }
  EXPECT_EQ(missed, 0);
  EXPECT_LT(r.facts.hausdorff_max_of_diag, 0.02);
}

TEST(retopo_core, coarse_input_is_refined_before_solving)
{
  /* 12 big triangles, many more output faces than input vertices. */
  const Result r = remesh(box(1), params_for(600));
  ASSERT_EQ(r.status, Status::Ok);
  EXPECT_GT(r.intake.refined_edges, 0);
  EXPECT_EQ(r.facts.quad_ratio, 1.0);
  EXPECT_LT(std::abs(r.facts.face_count_error), 0.25);
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name Symmetry, determinism, cancellation, empty input, measurement
 * \{ */

TEST(retopo_core, symmetry_is_exact)
{
  /* An off-centre torus is not itself symmetric in Y, so the mirror must come from the remesher.
   */
  TriMesh in = torus(80, 32);
  for (Vec3 &p : in.positions) {
    p.y += 0.05 * p.x * p.x;
  }
  for (const int axis : {0, 1}) {
    Params p = params_for(1500);
    p.symmetry_axes = 1 << axis;
    const Result r = remesh(in, p);
    ASSERT_EQ(r.status, Status::Ok) << axis;
    EXPECT_EQ(r.facts.quad_ratio, 1.0) << axis;
    EXPECT_EQ(boundary_edges(r.mesh), 0) << axis;
    for (const Vec3 &v : r.mesh.positions) {
      Vec3 mirrored = v;
      mirrored[axis] = -mirrored[axis];
      ASSERT_LT(nearest_vertex_distance(r.mesh, mirrored), 1e-6) << axis;
    }
  }
}

TEST(retopo_core, result_is_deterministic)
{
  const TriMesh in = to_soup(radial_noise(icosphere(5), 0.01));
  const Result a = remesh(in, params_for(1200));
  const Result b = remesh(in, params_for(1200));
  ASSERT_EQ(a.status, Status::Ok);
  EXPECT_EQ(a.mesh.corner_verts, b.mesh.corner_verts);
  ASSERT_EQ(a.mesh.positions.size(), b.mesh.positions.size());
  for (size_t i = 0; i < a.mesh.positions.size(); i++) {
    EXPECT_EQ(distance(a.mesh.positions[i], b.mesh.positions[i]), 0.0);
  }
}

TEST(retopo_core, cancel_stops_without_output)
{
  Callbacks callbacks;
  int calls = 0;
  callbacks.cancelled = [&]() { return ++calls > 2; };
  const Result r = remesh(icosphere(5), params_for(1500), {}, callbacks);
  EXPECT_EQ(r.status, Status::Cancelled);
  EXPECT_EQ(r.mesh.faces_num(), 0);
}

TEST(retopo_core, empty_and_degenerate_input_is_refused_with_a_reason)
{
  EXPECT_EQ(remesh(TriMesh(), params_for(100)).status, Status::EmptyInput);
  TriMesh flat;
  flat.positions = {{0, 0, 0}, {1, 0, 0}, {2, 0, 0}};
  flat.tris = {{0, 1, 2}};
  const Result r = remesh(flat, params_for(100));
  EXPECT_EQ(r.status, Status::EmptyInput);
  EXPECT_FALSE(r.notes.empty());
}

TEST(retopo_core, measure_counts_faces_and_valences)
{
  /* A 3 x 3 grid of quads plus one triangle on the side. */
  PolyMesh m;
  for (int j = 0; j < 4; j++) {
    for (int i = 0; i < 4; i++) {
      m.positions.push_back({double(i), double(j), 0.0});
    }
  }
  for (int j = 0; j < 3; j++) {
    for (int i = 0; i < 3; i++) {
      const int q[4] = {j * 4 + i, j * 4 + i + 1, (j + 1) * 4 + i + 1, (j + 1) * 4 + i};
      m.add_face(q, 4);
    }
  }
  m.positions.push_back({4.0, 0.5, 0.0});
  const int t[3] = {3, 16, 7};
  m.add_face(t, 3);
  TriMesh ref;
  ref.positions = {{0, 0, 0}, {3, 0, 0}, {3, 3, 0}, {0, 3, 0}};
  ref.tris = {{0, 1, 2}, {0, 2, 3}};
  const Facts f = measure(m, ref, 10);
  EXPECT_EQ(f.faces, 10);
  EXPECT_EQ(f.quads, 9);
  EXPECT_EQ(f.triangles, 1);
  EXPECT_NEAR(f.quad_ratio, 0.9, 1e-12);
  EXPECT_EQ(f.vertices, 17);
  EXPECT_EQ(f.interior_valence[1], 4); /* the 4 inner grid vertices */
  EXPECT_EQ(f.irregular_vertices, 0);
  EXPECT_EQ(f.boundary_vertices, 13);
  EXPECT_NEAR(f.face_count_error, 0.0, 1e-12);
}

/** \} */

}  // namespace blender::geometry::retopo::test
