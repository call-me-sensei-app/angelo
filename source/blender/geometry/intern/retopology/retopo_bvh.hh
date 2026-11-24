/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup geo
 *
 * Closest-point queries on triangles and on segments (the input surface and its feature lines),
 * with a binary AABB tree. Read-only after construction, so queries run from many threads at once.
 */

#pragma once

#include <array>
#include <vector>

#include "retopo_vec.hh"

namespace blender::geometry::retopo {

class ClosestPointTree {
 public:
  struct Hit {
    Vec3 point;
    int primitive = -1;
    double distance_squared = 1e300;
  };

  /** Triangles `tris` over `positions`. */
  void build_triangles(const std::vector<Vec3> &positions,
                       const std::vector<std::array<int, 3>> &tris);
  /** Segments `segments` over `positions`. */
  void build_segments(const std::vector<Vec3> &positions,
                      const std::vector<std::array<int, 2>> &segments);

  bool empty() const
  {
    return primitives_num_ == 0;
  }
  /** The closest point within `max_distance` (or anywhere when it is negative). */
  Hit closest(const Vec3 &p, double max_distance = -1.0) const;

 private:
  struct Node {
    Vec3 lo, hi;
    int left = -1, right = -1; /* children; -1 for a leaf */
    int begin = 0, end = 0;    /* leaf range in `order_` */
  };
  int build_node(int begin, int end, std::vector<Vec3> &centers);
  void primitive_bounds(int prim, Vec3 &lo, Vec3 &hi) const;
  Vec3 closest_on_primitive(int prim, const Vec3 &p) const;

  std::vector<Vec3> positions_;
  std::vector<std::array<int, 3>> prims_; /* segments repeat their second vertex */
  bool segments_ = false;
  int primitives_num_ = 0;
  std::vector<int> order_;
  std::vector<Node> nodes_;
};

/** The closest point to `p` on triangle `a b c` (Ericson, "Real-Time Collision Detection", 5.1.5).
 */
Vec3 closest_point_on_triangle(const Vec3 &p, const Vec3 &a, const Vec3 &b, const Vec3 &c);

}  // namespace blender::geometry::retopo
