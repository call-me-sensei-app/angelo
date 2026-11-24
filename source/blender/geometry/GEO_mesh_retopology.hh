/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * \ingroup geo
 *
 * Field-aligned quad retopology of a mesh (`doc/angelo/retopology.rst`). The work is done by the
 * self-contained core in `intern/retopology`; this is its #Mesh interface for the operator, the
 * agent's job and scripts.
 */

#include <optional>
#include <string>

#include "BLI_function_ref.hh"
#include "BLI_math_vector_types.hh"
#include "BLI_span.hh"
#include "BLI_vector.hh"

#include "intern/retopology/retopo_core.hh"

struct Mesh;

namespace blender::geometry {

struct RetopologyOptions {
  /** Faces wanted in the whole output (both sides of any symmetry plane). */
  int target_faces = 5000;
  /** Mirror planes through the mesh origin: bit 0 = X, bit 1 = Y, bit 2 = Z (as #Mesh::symmetry).
   */
  int symmetry_axes = 0;
  /** Sharp-edge angle in radians; 0 turns angle-based features off (marked edges still count). */
  float sharp_angle = 0.610865f; /* 35 degrees */
  /** Follow the edges marked Sharp, Seam or Crease as hard features. */
  bool use_marked_edges = true;
  float curvature_weight = 1.0f;
  bool pure_quads = true;
};

struct RetopologyResult {
  /** The new mesh (no Main), or null when the status is not Ok. */
  Mesh *mesh = nullptr;
  retopo::Status status = retopo::Status::Failed;
  retopo::Facts facts;
  retopo::IntakeReport intake;
  retopo::StageTimes times;
  /** How many times the fields were solved (more than once when the spacing was corrected). */
  int field_solves = 0;
  double target_edge_length = 0.0;
  Vector<std::string> notes;
};

/**
 * Retopologise `mesh` (its own coordinates; the caller passes the evaluated mesh it wants
 * measured). `guides` are polylines in the mesh's space the flow follows where they touch the
 * surface. Thread-safe: reads `mesh` only.
 */
RetopologyResult mesh_retopology(const Mesh &mesh,
                                 const RetopologyOptions &options,
                                 Span<Vector<float3>> guides = {},
                                 FunctionRef<bool()> cancelled = {},
                                 FunctionRef<void(float)> progress = {});

}  // namespace blender::geometry
