/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup geo
 *
 * The retopology pipeline: intake, symmetry cut, features, refinement, fields, extraction,
 * dislocation cleanup, finishing, mirror, facts.
 */

#include <algorithm>
#include <chrono>
#include <cmath>
#include <string>

#include "retopo_extract.hh"

namespace blender::geometry::retopo {

/** Refine the input until every edge is at most this share of the lattice spacing. */
static constexpr double MAX_EDGE_OF_SCALE = 0.35;
/** Refinement never grows the working surface past this many triangles (the result then notes it).
 */
static constexpr int MAX_WORKING_TRIANGLES = 24'000'000;
/** Longest row of coarse quads removed or inserted to cancel a pole pair: past it, the row has
 * left its patch. */
static constexpr int MAX_STRIP_QUADS = 48;
/** When the finished mesh would miss the face target by more than this share, the hierarchy and
 * fields are built again at a corrected spacing, at most MAX_RESOLVES times... */
static constexpr double FACES_TOLERANCE = 0.1;
static constexpr int MAX_RESOLVES = 2;
/** ...within these shares of the first spacing (at most about eight times, at least about half the
 * faces per unit of area). */
static constexpr double MIN_SPACING_OF_SCALE = 0.35;
static constexpr double MAX_SPACING_OF_SCALE = 1.4;

namespace {

class Clock {
  std::chrono::steady_clock::time_point start_ = std::chrono::steady_clock::now();

 public:
  double lap()
  {
    const auto now = std::chrono::steady_clock::now();
    const double ms = std::chrono::duration<double, std::milli>(now - start_).count();
    start_ = now;
    return ms;
  }
};

}  // namespace

/** Mirror `m` across `axis = 0`. Border vertices exactly on the plane are shared; faces are
 * reversed. */
static PolyMesh mirror(const PolyMesh &m, const int axis)
{
  std::vector<std::pair<uint64_t, int>> uses;
  for (int f = 0; f < m.faces_num(); f++) {
    const int n = m.face_size(f);
    for (int c = 0; c < n; c++) {
      const int a = m.corner_verts[size_t(m.face_offsets[size_t(f)] + c)];
      const int b = m.corner_verts[size_t(m.face_offsets[size_t(f)] + (c + 1) % n)];
      uses.emplace_back((uint64_t(std::min(a, b)) << 32) | uint64_t(std::max(a, b)), f);
    }
  }
  std::sort(uses.begin(), uses.end());
  std::vector<uint8_t> border(m.positions.size(), 0);
  for (size_t i = 0; i < uses.size(); i++) {
    const bool single = (i == 0 || uses[i - 1].first != uses[i].first) &&
                        (i + 1 == uses.size() || uses[i + 1].first != uses[i].first);
    if (single) {
      border[size_t(uses[i].first >> 32)] = 1;
      border[size_t(uses[i].first & 0xffffffffu)] = 1;
    }
  }
  PolyMesh out = m;
  std::vector<int> twin(m.positions.size());
  for (size_t v = 0; v < m.positions.size(); v++) {
    if (border[v] && m.positions[v][axis] == 0.0) {
      twin[v] = int(v);
      continue;
    }
    Vec3 p = m.positions[v];
    p[axis] = -p[axis];
    twin[v] = int(out.positions.size());
    out.positions.push_back(p);
  }
  std::vector<int> face;
  for (int f = 0; f < m.faces_num(); f++) {
    const int n = m.face_size(f);
    face.resize(size_t(n));
    for (int c = 0; c < n; c++) {
      face[size_t(c)] =
          twin[size_t(m.corner_verts[size_t(m.face_offsets[size_t(f)] + n - 1 - c)])];
    }
    out.add_face(face.data(), n);
  }
  return out;
}

Result remesh(const TriMesh &input,
              const Params &params,
              const Constraints &constraints,
              const Callbacks &callbacks)
{
  Result r;
  Clock clock;
  auto cancelled = [&]() { return callbacks.cancelled && callbacks.cancelled(); };
  auto progress = [&](const float f) {
    if (callbacks.progress) {
      callbacks.progress(f);
    }
  };
  if (cancelled()) {
    r.status = Status::Cancelled;
    return r;
  }

  /* Intake. */
  std::vector<std::array<int, 2>> marked;
  Surface base = intake(input, constraints.marked_edges, params, r.intake, marked);
  if (base.tris.empty()) {
    r.status = Status::EmptyInput;
    r.notes.push_back(input.tris.empty() ?
                          "The mesh has no faces." :
                          "No face of the mesh has an area, or every part is below the size "
                          "threshold.");
    r.times.intake_ms = clock.lap();
    return r;
  }
  const double area = surface_area(base.positions, base.tris);
  const int target = std::max(params.target_faces, 1);
  const double edge = std::sqrt(area / target);
  const double scale = params.pure_quads ? 2.0 * edge : edge;
  r.target_edge_length = edge;

  Surface work = base;
  for (int axis = 0; axis < 3; axis++) {
    if (params.symmetry_axes & (1 << axis)) {
      work = clip_positive(work, axis, marked);
    }
  }
  if (work.tris.empty()) {
    r.status = Status::EmptyInput;
    r.notes.push_back("Nothing of the mesh lies on the kept side of the symmetry plane.");
    return r;
  }
  detect_features(work, marked, params.sharp_angle_degrees, edge);
  r.intake.refined_edges = refine(work, MAX_EDGE_OF_SCALE * scale, MAX_WORKING_TRIANGLES);
  if (int(work.tris.size()) * 4 > MAX_WORKING_TRIANGLES * 3) {
    r.notes.push_back(
        "Refinement stopped at the working-size limit: the face target is very high for this "
        "mesh.");
  }
  work.finalize();
  apply_guides(work, constraints.guides, 0.5 * edge);
  r.intake.feature_edges = int(work.feature_edges.size());
  if (r.intake.flap_triangles > 0) {
    r.notes.push_back(
        std::to_string(r.intake.flap_triangles) +
        " triangles hanging off non-manifold edges, smaller than one output face, were left out.");
  }
  if (r.intake.shells_dropped > 0) {
    r.notes.push_back(std::to_string(r.intake.shells_dropped) +
                      " separate parts below the size threshold were left out.");
  }
  r.times.intake_ms = clock.lap();
  progress(0.1f);
  if (cancelled()) {
    r.status = Status::Cancelled;
    return r;
  }

  /* Fields. */
  Hierarchy hierarchy;
  Fields fields;
  /* `same_surface`: the surface is the one the hierarchy was built on, which is then reused (only
   * its curvature depends on the spacing). */
  auto solve_all = [&](const double s, const bool same_surface) {
    r.field_solves++;
    if (same_surface) {
      update_curvature(work, hierarchy, s);
    }
    else {
      hierarchy = build_hierarchy(work, s);
    }
    r.times.hierarchy_ms += clock.lap();
    progress(0.2f);
    if (cancelled()) {
      return false;
    }
    return solve_fields(hierarchy, s, params, fields, cancelled, [&](const int stage) {
      if (stage == 0) {
        r.times.orientation_ms += clock.lap();
        progress(0.45f);
      }
      else {
        r.times.position_ms += clock.lap();
        progress(0.7f);
      }
    });
  };
  if (!solve_all(scale, false)) {
    r.status = Status::Cancelled;
    return r;
  }

  /* Extraction and dislocation cleanup, at a spacing corrected until the result has about the
   * faces asked for. The field lays out one face per lattice cell of area, but area it cannot
   * represent yields none: the half row between an annihilated pole pair, closed parts and pores
   * smaller than a cell, parts thinner than one (2026-11-15: CT-scanned bones lost 60-90% of their
   * faces, clean surfaces about a quarter). The faces the extraction would finish with are counted
   * and the hierarchy and fields rebuilt at a spacing that makes up for the difference. */
  const double work_area = surface_area(work.positions, work.tris);
  ClosestPointTree tree;
  tree.build_triangles(work.positions, work.tris);
  /* The faces the finished mesh will have: the quad split turns each n-gon into n quads. */
  auto finished_faces = [&](const PolyMesh &m) {
    return params.pure_quads ? int(m.corner_verts.size()) : m.faces_num();
  };
  const double wanted = work_area / (edge * edge);
  double spacing = scale, previous_spacing = 0.0, previous_ratio = 0.0;
  Extracted extracted;
  DislocationCleanup dislocations;
  for (int round = 0;; round++) {
    extracted = extract(work, tree, fields, spacing);
    dislocations = cancel_dislocations(
        extracted, spacing, MAX_STRIP_QUADS, wanted, params.pure_quads);
    r.times.extraction_ms += clock.lap();
    /* Count what the finished mesh will have: its holes are closed at the end (`close_holes`),
     * so a spacing corrected for the area they lack would overshoot once they are filled. The
     * clipped surface is the input here, so a symmetry cut counts as a border, never a hole. */
    PolyMesh closed = extracted.mesh;
    close_holes(closed, work, tree, spacing, 0);
    const double ratio = double(finished_faces(closed)) / wanted;
    if (round == MAX_RESOLVES || ratio <= 0.0 || std::fabs(ratio - 1.0) <= FACES_TOLERANCE) {
      break;
    }
    /* Faces go as spacing^-k: k = 2 on a surface the field covers fully, steeper where finer
     * spacing reaches structure the coarser one could not; measured from the previous round when
     * there is one. */
    double k = 2.0;
    if (previous_spacing > 0.0) {
      const double measured = std::log(ratio / previous_ratio) /
                              std::log(previous_spacing / spacing);
      if (measured > 0.5 && measured < 8.0) {
        k = measured;
      }
    }
    const double next = std::clamp(spacing * std::pow(ratio, 1.0 / k),
                                   MIN_SPACING_OF_SCALE * scale,
                                   MAX_SPACING_OF_SCALE * scale);
    if (next == spacing) {
      break;
    }
    previous_spacing = spacing;
    previous_ratio = ratio;
    spacing = next;
    const int split = refine(work, MAX_EDGE_OF_SCALE * spacing, MAX_WORKING_TRIANGLES);
    r.intake.refined_edges += split;
    if (split > 0) {
      work.finalize();
      apply_guides(work, constraints.guides, 0.5 * edge);
    }
    if (!solve_all(spacing, split == 0)) {
      r.status = Status::Cancelled;
      return r;
    }
    if (split > 0) {
      tree = ClosestPointTree();
      tree.build_triangles(work.positions, work.tris);
    }
    if (cancelled()) {
      r.status = Status::Cancelled;
      return r;
    }
  }
  hierarchy = Hierarchy();
  if (dislocations.rows_removed + dislocations.rows_inserted > 0) {
    r.notes.push_back(std::to_string(dislocations.rows_removed + dislocations.rows_inserted) +
                      " pole pairs were cancelled by removing or inserting the row between them.");
  }
  if (extracted.holes_filled > 0) {
    r.notes.push_back(std::to_string(extracted.holes_filled) +
                      " gaps in the extracted mesh were closed with n-gons.");
  }
  if (extracted.mesh.faces_num() == 0) {
    r.status = Status::Failed;
    r.notes.push_back(
        "The field produced no faces: the face target may be too low for this mesh.");
    return r;
  }
  progress(0.85f);
  if (cancelled()) {
    r.status = Status::Cancelled;
    return r;
  }
  FinishSettings finish_settings;
  finish_settings.pure_quads = params.pure_quads;
  finish_settings.relax_iterations = params.relax_iterations;
  finish_settings.edge_length = edge * spacing / scale;
  PolyMesh mesh = finish(work, tree, extracted, finish_settings);
  for (int axis = 2; axis >= 0; axis--) {
    if (params.symmetry_axes & (1 << axis)) {
      mesh = mirror(mesh, axis);
    }
  }
  {
    ClosestPointTree full;
    full.build_triangles(base.positions, base.tris);
    const int closed = close_holes(mesh, base, full, edge * spacing / scale, params.symmetry_axes);
    if (closed > 0) {
      r.notes.push_back(std::to_string(closed) +
                        " holes in the result, where the input has no border, were closed.");
    }
  }
  r.mesh = std::move(mesh);
  r.times.finish_ms += clock.lap();

  /* Facts, against the cleaned input (both sides of any symmetry plane). */
  TriMesh reference;
  reference.positions = base.positions;
  reference.tris = base.tris;
  r.facts = measure(r.mesh, reference, params.target_faces);
  r.times.facts_ms = clock.lap();
  progress(1.0f);
  r.status = Status::Ok;
  return r;
}

}  // namespace blender::geometry::retopo
