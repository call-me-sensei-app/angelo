/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup geo
 *
 * The input intake and the working surface. Robustness to AI-generated and scanned meshes is
 * decided here, from each mesh's own measured topology (never from names or sizes of particular
 * inputs):
 * - weld coincident vertices (a tolerance relative to the bounding-box diagonal), drop degenerate
 * and duplicate triangles, drop flaps (triangles hanging off a non-manifold edge by an otherwise
 * open border);
 * - make the winding consistent across manifold edges, and closed shells face outwards;
 * - find shells and leave out those below a fraction of the total area;
 * - find hard features: user-marked edges, boundaries, non-manifold edges and sharp edges (on
 * bilaterally filtered face normals, keeping only feature chains at least one output edge long, so
 * scan noise does not become a feature);
 * - refine until every edge is short enough for the position field to resolve the output lattice.
 */

#pragma once

#include <array>
#include <cstdint>
#include <vector>

#include "retopo_core.hh"

namespace blender::geometry::retopo {

enum class FeatureKind : uint8_t {
  None = 0,
  /** On a feature line: the output keeps a lattice line along it. */
  Line = 1,
  /** Where feature lines meet or turn: the output keeps a vertex on it. */
  Corner = 2,
};

/** The cleaned triangle surface the fields are solved on. */
struct Surface {
  std::vector<Vec3> positions;
  std::vector<std::array<int, 3>> tris;
  /** Hard feature edges (vertex pairs, smaller index first). */
  std::vector<std::array<int, 2>> feature_edges;
  /**
   * Per feature edge: whether the position field must lay a lattice line on it. Borders, marked
   * and non-manifold edges are pinned; a sharp crease is not: the extrinsic smoothing aligns the
   * lattice to a crease by itself, and pinning creases froze dislocations next to them
   * (2026-11-14: a 2 x 2 x 2 box gave 20 of 228 edge samples off the result and 16 irregular
   * vertices pinned, none and the ideal 8 unpinned).
   */
  std::vector<uint8_t> feature_pinned;

  /* Filled by #Surface::finalize. */
  std::vector<Vec3> normals;
  std::vector<double> areas;
  std::vector<int> adjacency_offsets;
  std::vector<int> adjacency;
  std::vector<FeatureKind> feature_kind;
  /** The same over pinned feature edges only: where the position field keeps a lattice line or
   * point. */
  std::vector<FeatureKind> pin_kind;
  /** Unit direction along the feature at a #FeatureKind::Line vertex, or along a guide (#guided).
   */
  std::vector<Vec3> feature_dir;
  std::vector<uint8_t> guided;
  double total_area = 0.0;
  Vec3 lo, hi;

  int verts_num() const
  {
    return int(positions.size());
  }
  /** Normals, dual areas, the vertex graph, feature kinds and directions. */
  void finalize();
  double diagonal() const
  {
    return distance(lo, hi);
  }
};

/** Weld, clean, orient, split into shells and drop the small ones. `marked` is remapped to the
 * welded indices. */
Surface intake(const TriMesh &input,
               const std::vector<std::array<int, 2>> &marked,
               const Params &params,
               IntakeReport &report,
               std::vector<std::array<int, 2>> &marked_out);

/** Keep the part of `surface` on the positive side of the plane `axis = 0`; the cut lies exactly
 * on the plane. */
Surface clip_positive(const Surface &surface, int axis, std::vector<std::array<int, 2>> &marked);

/** Hard features: marked edges, boundaries, non-manifold edges and sharp edges (see the file
 * comment). */
void detect_features(Surface &surface,
                     const std::vector<std::array<int, 2>> &marked,
                     double sharp_angle_degrees,
                     double min_chain_length);

/** Split edges longer than `max_edge` until none is left (bounded passes); features split with
 * them. */
int refine(Surface &surface, double max_edge, int max_triangles);

/** Mark vertices near `guides` (within `radius`) with the guide's direction. Call after
 * #Surface::finalize. */
void apply_guides(Surface &surface, const std::vector<std::vector<Vec3>> &guides, double radius);

/** Sum of triangle areas. */
double surface_area(const std::vector<Vec3> &positions,
                    const std::vector<std::array<int, 3>> &tris);

}  // namespace blender::geometry::retopo
