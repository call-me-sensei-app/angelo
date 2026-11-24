/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup geo
 *
 * Field-aligned quad retopology: the self-contained core.
 *
 * The core depends only on the C++ standard library, so its unit tests also build and run without
 * a Blender build
 * (`tests/retopo_core_test.cc`). The Blender side (`GEO_mesh_retopology.hh`) converts a #Mesh to
 * and from these types.
 *
 * Method, from published research (no add-on code; see `doc/angelo/retopology.rst`):
 * - W. Jakob, M. Tarini, D. Panozzo, O. Sorkine-Hornung. "Instant Field-Aligned Meshes". ACM TOG
 * 34(6), 2015: extrinsic 4-RoSy orientation and 4-PoSy position fields smoothed on a
 * multi-resolution vertex graph, and quad extraction by collapsing graph edges whose lattice
 * offset is zero. Implemented here from the paper.
 * - D. Cohen-Steiner, J.-M. Morvan. "Restricted Delaunay triangulations and normal cycle". SoCG
 * 2003: the edge-based curvature tensor used for the soft alignment to principal directions.
 */

#pragma once

#include <array>
#include <cstdint>
#include <functional>
#include <string>
#include <vector>

#include "retopo_vec.hh"

namespace blender::geometry::retopo {

/** A triangle mesh as the core receives it: possibly a triangle soup, non-manifold, with several
 * shells. */
struct TriMesh {
  std::vector<Vec3> positions;
  std::vector<std::array<int, 3>> tris;
};

/** A polygon mesh as the core returns it. Face `f` has the corners
 * `face_offsets[f]..face_offsets[f + 1]`. */
struct PolyMesh {
  std::vector<Vec3> positions;
  std::vector<int> face_offsets = {0};
  std::vector<int> corner_verts;

  int faces_num() const
  {
    return int(face_offsets.size()) - 1;
  }
  int face_size(const int f) const
  {
    return face_offsets[f + 1] - face_offsets[f];
  }
  void add_face(const int *verts, int size);
};

/** What the caller asks for. Every field has a documented default; nothing is tuned to one input.
 */
struct Params {
  /** Faces wanted in the output (quads when #pure_quads). */
  int target_faces = 5000;
  /** Mirror planes through the input's origin: bit 0 = X, bit 1 = Y, bit 2 = Z. */
  int symmetry_axes = 0;
  /** Edges whose faces meet at more than this many degrees are hard features the flow follows.
   * Zero disables. */
  double sharp_angle_degrees = 35.0;
  /** Weight of the soft alignment to principal curvature directions (0 = smoothness only). */
  double curvature_weight = 1.0;
  /** Output only quads: extract at twice the edge length and split every face once. */
  bool pure_quads = true;
  /** Shells smaller than this fraction of the whole surface area are left out (reported). */
  double min_shell_area_fraction = 1e-4;
  /** Smoothing passes per hierarchy level for each field. */
  int field_iterations = 12;
  /** Tangential relaxation passes on the surface after extraction. */
  int relax_iterations = 4;
};

/** User constraints carried with the input. Indices refer to #TriMesh::positions. */
struct Constraints {
  /** Edges the user marked (sharp, seam, crease): treated as hard features, whatever their angle.
   */
  std::vector<std::array<int, 2>> marked_edges;
  /** Guide polylines in the input's space; the flow aligns to them where they lie on the surface.
   */
  std::vector<std::vector<Vec3>> guides;
};

/** Progress and cancellation. Both may be empty. Called from the calling thread only. */
struct Callbacks {
  std::function<void(float)> progress;
  std::function<bool()> cancelled;
};

/** The measured quality of a result: what the agent reads and the benchmark grades. */
struct Facts {
  int faces = 0;
  int quads = 0;
  int triangles = 0;
  int ngons = 0;
  double quad_ratio = 0.0;
  int vertices = 0;
  /** Interior vertices by valence: [0] = 3 or less, [1] = 4, [2] = 5, [3] = 6 or more. */
  std::array<int, 4> interior_valence = {0, 0, 0, 0};
  /** Interior vertices whose valence is not 4. */
  int irregular_vertices = 0;
  int boundary_vertices = 0;
  /** Edges used by more than two faces (always 0 by construction; measured, not assumed). */
  int non_manifold_edges = 0;
  /** faces / target - 1. */
  double face_count_error = 0.0;
  /** Sampled two-sided distance between output and input surfaces, divided by the input's
   * bounding-box diagonal. */
  double hausdorff_max_of_diag = 0.0;
  double hausdorff_mean_of_diag = 0.0;
  double bounds_diagonal = 0.0;
};

/** What the input intake found and did; reported with every result. */
struct IntakeReport {
  int input_vertices = 0;
  int input_triangles = 0;
  int welded_vertices = 0;
  int degenerate_triangles = 0;
  int duplicate_triangles = 0;
  /** Triangles of patches hanging off a non-manifold edge with less area than one output face. */
  int flap_triangles = 0;
  int non_manifold_edges = 0;
  int boundary_edges = 0;
  int flipped_triangles = 0;
  int shells = 0;
  int shells_dropped = 0;
  int feature_edges = 0;
  int refined_edges = 0;
};

enum class Status : uint8_t {
  Ok,
  Cancelled,
  /** No usable surface: no triangles, or all shells below the size threshold. */
  EmptyInput,
  /** The field or extraction produced no faces. */
  Failed,
};

struct StageTimes {
  double intake_ms = 0.0;
  double hierarchy_ms = 0.0;
  double orientation_ms = 0.0;
  double position_ms = 0.0;
  double extraction_ms = 0.0;
  double finish_ms = 0.0;
  double facts_ms = 0.0;
};

struct Result {
  Status status = Status::Failed;
  PolyMesh mesh;
  Facts facts;
  IntakeReport intake;
  StageTimes times;
  /** Times the fields were solved: 1 when the first lattice spacing gave about the faces asked
   * for, more when the spacing was corrected (see `retopo_remesh.cc`). */
  int field_solves = 0;
  /** The quad edge length the size field asked for, in input units. */
  double target_edge_length = 0.0;
  /** Plain-English notes (not translated; read by the agent and logs). */
  std::vector<std::string> notes;
};

/** Run the whole pipeline. Deterministic: the same input and parameters give the same output. */
Result remesh(const TriMesh &input,
              const Params &params,
              const Constraints &constraints = {},
              const Callbacks &callbacks = {});

/** Measure a polygon mesh against a reference triangle mesh (the facts of #Result). Exposed for
 * the baselines. */
Facts measure(const PolyMesh &mesh, const TriMesh &reference, int target_faces);

}  // namespace blender::geometry::retopo
