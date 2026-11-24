/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup geo
 *
 * Integer-consistent lattice offsets on the surface edges, after the integer offset step of
 * J. Huang, Y. Zhou, M. Niessner, J. R. Shewchuk, L. J. Guibas, "QuadriFlow: A Scalable and
 * Robust Method for Quadrangulation", SGP 2018. Written from the paper's description.
 *
 * Every surface edge carries the integer lattice step between its two vertices' lattice points,
 * read from the position field. Around a triangle the three steps, carried into one frame, must
 * sum to zero; a triangle where they do not is a position singularity ("charge"), which the
 * extraction turns into a pole pair and a half row that ends inside the surface. Removing such a
 * half row (the strip collapse) can only ever delete rows, so it costs faces.
 *
 * Here each charge is cancelled against an opposite charge, or moved out through a border or into
 * an orientation singularity, by changing the steps of the edges crossed by the cheapest path of
 * triangles between them (cost: how far each change moves a step from the field's own continuous
 * offset). A change may only join two lattice points or split one (a step of none or one becomes
 * none or one), so it removes or inserts a row; a change that would shear a row (a unit step made
 * diagonal) is refused, and an edge on a hard feature never gets a step across the feature. The
 * position field is then smoothed again with the steps held fixed, so the lattice points either
 * side of a changed row sit one step apart on the surface.
 *
 * Unlike the strip collapse, the path may bend and may end at a border or an orientation
 * singularity, so many more pole pairs are resolved; like it, cancelling a pair usually removes the
 * half row between them, so the face count still relies on the spacing correction.
 */

#pragma once

#include <array>
#include <cstdint>
#include <vector>

#include "retopo_edges.hh"
#include "retopo_field.hh"

namespace blender::geometry::retopo {

using LatticeStep = std::array<int, 2>;

struct LatticeEdges {
  /** The surface's edges and their triangles. */
  EdgeTris edges;
  /** Per edge (`v0 < v1`): the lattice step from v0 to v1, in v0's frame (q, n x q). */
  std::vector<LatticeStep> step;
  /** Per edge: q(v0) is q(v1) turned by this many quarter turns (about the normal). */
  std::vector<uint8_t> turn;
  /** Per edge: whether the integer step changed it. */
  std::vector<uint8_t> changed;
  /** Position-singular triangles found, charges cancelled in pairs, and charges moved out through
   * a border or into an orientation singularity. */
  int charges = 0;
  int pairs_cancelled = 0;
  int charges_moved_out = 0;
};

/** Read the lattice steps of `surface`'s edges from `fields` at lattice spacing `scale`, then
 * cancel the position singularities (see the file comment). */
LatticeEdges lattice_edges(const Surface &surface, const Fields &fields, double scale);

/**
 * The position field smoothed again with the steps held fixed (the field's own smoothing, with
 * the integer step's offsets in place of the rounded ones), so the lattice points of a row the
 * step inserted or removed land one step apart, on the surface.
 */
std::vector<Vec3> positions_with_steps(const Surface &surface,
                                       const Fields &fields,
                                       const LatticeEdges &lattice,
                                       double scale);

}  // namespace blender::geometry::retopo
