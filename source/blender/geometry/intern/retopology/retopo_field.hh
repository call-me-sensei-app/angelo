/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup geo
 *
 * The multi-resolution vertex graph and the two fields solved on it (Jakob et al. 2015, sections
 * 3-4):
 * - orientation: a unit tangent `q` per vertex, defined up to rotations by 90 degrees (4-RoSy),
 * made as smooth as possible, held along hard features and guides, and pulled softly towards
 * principal curvature directions measured at the output scale;
 * - position: a point `o` per vertex in its tangent plane, defined up to integer steps of the edge
 * length along `q` and `n x q` (4-PoSy), made as consistent as possible, with a lattice line
 * through every feature line and a lattice point on every corner. Smoothing is Gauss-Seidel over a
 * graph colouring, so each colour runs in parallel and the result is deterministic.
 */

#pragma once

#include <functional>
#include <vector>

#include "retopo_mesh.hh"

namespace blender::geometry::retopo {

struct Level {
  std::vector<Vec3> positions, normals;
  std::vector<double> areas;
  std::vector<int> adjacency_offsets, adjacency;
  std::vector<float> weights; /* per adjacency entry */
  std::vector<FeatureKind> feature_kind;
  std::vector<FeatureKind> pin_kind;
  /** Where a pinned vertex's lattice line passes: its own position on level 0, the area-weighted
   * mean of its pinned children above (never of the unpinned ones, so a coarse vertex pins the
   * border, not a point inside it). */
  std::vector<Vec3> pin_point;
  std::vector<Vec3> feature_dir;
  std::vector<uint8_t> guided;
  /** Per vertex: the coarser level's vertex it merged into (empty on the coarsest level). */
  std::vector<int> parent;
  /** Vertices of each colour (no two neighbours share one). */
  std::vector<std::vector<int>> colours;
  /** Unit principal-direction tangent and its confidence in [0, 1] (zero when the surface is
   * isotropic). */
  std::vector<Vec3> curvature_dir;
  std::vector<double> curvature_confidence;

  int size() const
  {
    return int(positions.size());
  }
};

struct Hierarchy {
  std::vector<Level> levels; /* levels[0] is the surface itself */
};

/** Build the hierarchy by repeatedly merging matched neighbour pairs (most similar normals and
 * areas first). */
Hierarchy build_hierarchy(const Surface &surface, double scale);

/** Measure the curvature directions of `h` (built on `surface`) again at lattice spacing `scale`:
 * the only part of the hierarchy that depends on the spacing, so a hierarchy of an unchanged
 * surface is reused for another spacing with this alone. */
void update_curvature(const Surface &surface, Hierarchy &h, double scale);

struct Fields {
  std::vector<Vec3> q; /* orientation per level-0 vertex */
  std::vector<Vec3> o; /* position per level-0 vertex */
  /** The orientation on every level, kept so the position field can be solved again at another
   * spacing. */
  std::vector<std::vector<Vec3>> q_levels;
};

/**
 * Solve both fields, coarse to fine. `scale` is the lattice spacing. Returns false when
 * `cancelled` asked to stop. `stage(0)` is called after the orientation, `stage(1)` after the
 * position field.
 */
bool solve_fields(Hierarchy &hierarchy,
                  double scale,
                  const Params &params,
                  Fields &fields,
                  const std::function<bool()> &cancelled,
                  const std::function<void(int)> &stage);

/**
 * Solve the position field again at lattice spacing `scale`, over the orientation `fields` already
 * holds (the orientation doesn't depend on the spacing). Returns false when `cancelled` asked to
 * stop.
 */
bool solve_position(Hierarchy &hierarchy,
                    double scale,
                    const Params &params,
                    Fields &fields,
                    const std::function<bool()> &cancelled);

/* The extrinsic 4-RoSy / 4-PoSy helpers, shared with the extraction. */

/** Of the four rotations of `q1` about `n1`, the pair (`q0` rotated, `q1` rotated) closest in
 * angle. */
void compat_orientation(
    const Vec3 &q0, const Vec3 &n0, const Vec3 &q1, const Vec3 &n1, Vec3 &r0, Vec3 &r1);
/** The lattice points of the two vertices' position fields that are closest to each other. */
void compat_position(const Vec3 &p0,
                     const Vec3 &n0,
                     const Vec3 &q0,
                     const Vec3 &o0,
                     const Vec3 &p1,
                     const Vec3 &n1,
                     const Vec3 &q1,
                     const Vec3 &o1,
                     double scale,
                     Vec3 &r0,
                     Vec3 &r1);
/** The lattice point of (`o`, `q`, `n`) nearest to `p`. */
Vec3 position_round(const Vec3 &o, const Vec3 &q, const Vec3 &n, const Vec3 &p, double scale);

}  // namespace blender::geometry::retopo
