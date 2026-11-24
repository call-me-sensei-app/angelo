/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup geo
 *
 * Mesh extraction from the position field (Jakob et al. 2015, section 5) and the finishing passes.
 *
 * Extraction: every surface edge carries the integer lattice step between its two lattice points,
 * made consistent around every triangle first (`retopo_integer.hh`). Edges with no step are
 * collapsed; edges of one step become output edges. Faces are the cycles of the
 * output graph traced around each vertex in angular order in its tangent plane: counter-clockwise
 * cycles are faces, clockwise ones are borders.
 *
 * Finishing: optionally split every face once into quads (pure-quad output), project onto the
 * input surface, keep feature vertices on their feature lines and corners, and relax tangentially
 * on the surface.
 */

#pragma once

#include "retopo_bvh.hh"
#include "retopo_field.hh"

namespace blender::geometry::retopo {

struct Extracted {
  PolyMesh mesh;
  std::vector<FeatureKind> feature_kind; /* per output vertex */
  int holes_filled = 0;
  int triangle_pairs_merged = 0;
  int borders = 0;
  /** The integer step (`retopo_integer.hh`): position-singular triangles, pairs cancelled, and
   * charges moved out through a border or into an orientation singularity. */
  int position_singularities = 0;
  int singularity_pairs_cancelled = 0;
  int singularities_moved_out = 0;
};

/** `tree` holds `surface`'s triangles: a cycle whose middle is off the surface is a border, never
 * a face. */
Extracted extract(const Surface &surface,
                  const ClosestPointTree &tree,
                  const Fields &fields,
                  double scale);

struct DislocationCleanup {
  /** Pole pairs cancelled by removing the half row between them... */
  int rows_removed = 0;
  /** ...and by inserting the missing half row the other way round. */
  int rows_inserted = 0;
};

/**
 * Cancel dislocations (a non-quad face beside an irregular vertex, where a half row ends) in
 * pairs, or one against a border: by collapsing the quad strip that joins them, or by inserting
 * the half row that completes it, shortest first but nearer the `wanted_faces` the finished mesh
 * should have (its corners when `count_corners`, as the quad split makes one quad per corner;
 * else its faces), when that lowers the irregularity. See `retopo_cleanup.cc` and
 * `retopo_cleanup_split.cc`.
 */
DislocationCleanup cancel_dislocations(
    Extracted &x, double scale, int max_strip_quads, double wanted_faces, bool count_corners);

struct FinishSettings {
  bool pure_quads = true;
  int relax_iterations = 4;
  /** The output edge length (after the quad split, when there is one). */
  double edge_length = 1.0;
};

/** Close the finished mesh's holes: border loops of at most 48 edges with no border of `input` at
 * least four output edges long within an edge of most of their vertices (`edge`: the output edge
 * length; `surface`: `input`'s triangles). Even loops are filled with quads by chords, odd ones
 * with one face; a seam hole's fill stays mirrored. Returns the holes closed. See
 * `retopo_holes.cc`. */
int close_holes(PolyMesh &m,
                const Surface &input,
                const ClosestPointTree &surface,
                double edge,
                int symmetry_axes);

/** Split, project, snap and relax `extracted` on `surface`. */
PolyMesh finish(const Surface &surface,
                const ClosestPointTree &tree,
                Extracted &extracted,
                const FinishSettings &settings);

}  // namespace blender::geometry::retopo
