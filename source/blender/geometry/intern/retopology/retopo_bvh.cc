/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup geo
 */

#include <algorithm>

#include "retopo_bvh.hh"

namespace blender::geometry::retopo {

static constexpr int LEAF_SIZE = 4;

Vec3 closest_point_on_triangle(const Vec3 &p, const Vec3 &a, const Vec3 &b, const Vec3 &c)
{
  const Vec3 ab = b - a, ac = c - a, ap = p - a;
  const double d1 = dot(ab, ap), d2 = dot(ac, ap);
  if (d1 <= 0.0 && d2 <= 0.0) {
    return a;
  }
  const Vec3 bp = p - b;
  const double d3 = dot(ab, bp), d4 = dot(ac, bp);
  if (d3 >= 0.0 && d4 <= d3) {
    return b;
  }
  const double vc = d1 * d4 - d3 * d2;
  if (vc <= 0.0 && d1 >= 0.0 && d3 <= 0.0) {
    return a + ab * (d1 / (d1 - d3));
  }
  const Vec3 cp = p - c;
  const double d5 = dot(ab, cp), d6 = dot(ac, cp);
  if (d6 >= 0.0 && d5 <= d6) {
    return c;
  }
  const double vb = d5 * d2 - d1 * d6;
  if (vb <= 0.0 && d2 >= 0.0 && d6 <= 0.0) {
    return a + ac * (d2 / (d2 - d6));
  }
  const double va = d3 * d6 - d5 * d4;
  if (va <= 0.0 && (d4 - d3) >= 0.0 && (d5 - d6) >= 0.0) {
    return b + (c - b) * ((d4 - d3) / ((d4 - d3) + (d5 - d6)));
  }
  const double denom = 1.0 / (va + vb + vc);
  return a + ab * (vb * denom) + ac * (vc * denom);
}

static Vec3 closest_point_on_segment(const Vec3 &p, const Vec3 &a, const Vec3 &b)
{
  const Vec3 ab = b - a;
  const double l2 = length_squared(ab);
  if (l2 <= 1e-300) {
    return a;
  }
  const double t = std::clamp(dot(p - a, ab) / l2, 0.0, 1.0);
  return a + ab * t;
}

void ClosestPointTree::build_triangles(const std::vector<Vec3> &positions,
                                       const std::vector<std::array<int, 3>> &tris)
{
  positions_ = positions;
  prims_ = tris;
  segments_ = false;
  primitives_num_ = int(tris.size());
  std::vector<Vec3> centers(tris.size());
  for (size_t i = 0; i < tris.size(); i++) {
    centers[i] = (positions[tris[i][0]] + positions[tris[i][1]] + positions[tris[i][2]]) / 3.0;
  }
  order_.resize(tris.size());
  for (size_t i = 0; i < tris.size(); i++) {
    order_[i] = int(i);
  }
  nodes_.clear();
  if (primitives_num_ > 0) {
    nodes_.reserve(size_t(2 * primitives_num_ / LEAF_SIZE + 2));
    build_node(0, primitives_num_, centers);
  }
}

void ClosestPointTree::build_segments(const std::vector<Vec3> &positions,
                                      const std::vector<std::array<int, 2>> &segments)
{
  positions_ = positions;
  prims_.resize(segments.size());
  for (size_t i = 0; i < segments.size(); i++) {
    prims_[i] = {segments[i][0], segments[i][1], segments[i][1]};
  }
  segments_ = true;
  primitives_num_ = int(segments.size());
  std::vector<Vec3> centers(segments.size());
  for (size_t i = 0; i < segments.size(); i++) {
    centers[i] = (positions[segments[i][0]] + positions[segments[i][1]]) * 0.5;
  }
  order_.resize(segments.size());
  for (size_t i = 0; i < segments.size(); i++) {
    order_[i] = int(i);
  }
  nodes_.clear();
  if (primitives_num_ > 0) {
    build_node(0, primitives_num_, centers);
  }
}

void ClosestPointTree::primitive_bounds(const int prim, Vec3 &lo, Vec3 &hi) const
{
  const auto &t = prims_[prim];
  lo = min_v(positions_[t[0]], min_v(positions_[t[1]], positions_[t[2]]));
  hi = max_v(positions_[t[0]], max_v(positions_[t[1]], positions_[t[2]]));
}

int ClosestPointTree::build_node(const int begin, const int end, std::vector<Vec3> &centers)
{
  const int index = int(nodes_.size());
  nodes_.emplace_back();
  Vec3 lo(1e300, 1e300, 1e300), hi(-1e300, -1e300, -1e300);
  Vec3 clo = lo, chi = hi;
  for (int i = begin; i < end; i++) {
    Vec3 plo, phi;
    primitive_bounds(order_[i], plo, phi);
    lo = min_v(lo, plo);
    hi = max_v(hi, phi);
    clo = min_v(clo, centers[order_[i]]);
    chi = max_v(chi, centers[order_[i]]);
  }
  nodes_[index].lo = lo;
  nodes_[index].hi = hi;
  if (end - begin <= LEAF_SIZE) {
    nodes_[index].begin = begin;
    nodes_[index].end = end;
    return index;
  }
  const Vec3 extent = chi - clo;
  const int axis = extent.x > extent.y ? (extent.x > extent.z ? 0 : 2) :
                                         (extent.y > extent.z ? 1 : 2);
  const int mid = (begin + end) / 2;
  std::nth_element(
      order_.begin() + begin, order_.begin() + mid, order_.begin() + end, [&](int a, int b) {
        return centers[a][axis] < centers[b][axis] ||
               (centers[a][axis] == centers[b][axis] && a < b);
      });
  const int left = build_node(begin, mid, centers);
  const int right = build_node(mid, end, centers);
  nodes_[index].left = left;
  nodes_[index].right = right;
  return index;
}

Vec3 ClosestPointTree::closest_on_primitive(const int prim, const Vec3 &p) const
{
  const auto &t = prims_[prim];
  if (segments_) {
    return closest_point_on_segment(p, positions_[t[0]], positions_[t[1]]);
  }
  return closest_point_on_triangle(p, positions_[t[0]], positions_[t[1]], positions_[t[2]]);
}

static double box_distance_squared(const Vec3 &p, const Vec3 &lo, const Vec3 &hi)
{
  double d = 0.0;
  for (int i = 0; i < 3; i++) {
    const double v = p[i] < lo[i] ? lo[i] - p[i] : (p[i] > hi[i] ? p[i] - hi[i] : 0.0);
    d += v * v;
  }
  return d;
}

ClosestPointTree::Hit ClosestPointTree::closest(const Vec3 &p, const double max_distance) const
{
  Hit hit;
  if (nodes_.empty()) {
    return hit;
  }
  if (max_distance >= 0.0) {
    hit.distance_squared = max_distance * max_distance;
  }
  int stack[128];
  int top = 0;
  stack[top++] = 0;
  while (top > 0) {
    const Node &node = nodes_[stack[--top]];
    if (box_distance_squared(p, node.lo, node.hi) > hit.distance_squared) {
      continue;
    }
    if (node.left < 0) {
      for (int i = node.begin; i < node.end; i++) {
        const int prim = order_[i];
        const Vec3 q = closest_on_primitive(prim, p);
        const double d = distance_squared(p, q);
        if (d < hit.distance_squared ||
            (d == hit.distance_squared && hit.primitive >= 0 && prim < hit.primitive))
        {
          hit.distance_squared = d;
          hit.point = q;
          hit.primitive = prim;
        }
      }
      continue;
    }
    /* Visit the nearer child first. */
    const Node &l = nodes_[node.left], &r = nodes_[node.right];
    const double dl = box_distance_squared(p, l.lo, l.hi),
                 dr = box_distance_squared(p, r.lo, r.hi);
    if (top + 2 > 128) {
      continue; /* Depth is log2(n / 4); never reached for any mesh that fits in memory. */
    }
    if (dl < dr) {
      stack[top++] = node.right;
      stack[top++] = node.left;
    }
    else {
      stack[top++] = node.left;
      stack[top++] = node.right;
    }
  }
  return hit;
}

}  // namespace blender::geometry::retopo
