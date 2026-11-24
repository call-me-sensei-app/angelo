/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup geo
 *
 * The output graph the extraction traces faces in, and the passes on its faces (coverage test,
 * small-hole filling, triangle-pair merging). Internal to `retopo_extract.cc`.
 */

#pragma once

#include <vector>

#include "retopo_bvh.hh"
#include "retopo_mesh.hh"

namespace blender::geometry::retopo {

struct Graph {
  std::vector<Vec3> positions, normals;
  std::vector<FeatureKind> kind;
  /** Neighbours of each vertex in counter-clockwise order about its normal. */
  std::vector<std::vector<int>> ring;
};

/** Counter-clockwise about its vertices' normals (when `check_orientation`), with its middle on
 * the surface: a face, not a border. */
bool is_face(const Graph &g,
             const ClosestPointTree &tree,
             const std::vector<int> &cycle,
             double scale,
             bool check_orientation = true);

/** Close border loops of a few edges whose middle lies on the surface. Returns the loops closed. */
int fill_small_holes(const Graph &g,
                     const ClosestPointTree &tree,
                     double scale,
                     std::vector<std::vector<int>> &faces);

/** Merge triangle pairs that make a near-rectangular quad. Returns the pairs merged. */
int merge_triangle_pairs(const Graph &g, std::vector<std::vector<int>> &faces);

}  // namespace blender::geometry::retopo
