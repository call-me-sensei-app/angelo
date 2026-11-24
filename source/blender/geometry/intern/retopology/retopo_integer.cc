/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup geo
 *
 * Integer-consistent lattice offsets. See `retopo_integer.hh`.
 */

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <functional>
#include <limits>
#include <queue>

#include "retopo_integer.hh"

namespace blender::geometry::retopo {

/** Search radii (in lattice spacings) of the successive pairing passes: every charge is first
 * paired within one cell, then two, four and eight, so near pairs are made before far ones (a
 * greedy stand-in for the minimum-cost pairing). The last radius bounds the search; a charge with
 * no partner within it is left to the strip collapse. */
static constexpr double PASS_RADII_OF_SCALE[] = {1.0, 2.0, 4.0, 8.0};
/** A changed step on a hard-feature edge may cross the feature by less than this share of a step
 * (so the lattice line on the feature stays on it). */
static constexpr double MAX_FEATURE_CROSSING = 0.5;
/** Jacobi passes of the position field over the fixed steps (each spreads a correction one edge
 * further). */
static constexpr int SMOOTHING_PASSES = 16;
/** Cost of each changed edge on top of its misfit (lattice steps): among paths of equal misfit,
 * the one changing fewest edges. */
static constexpr double EDGE_COST = 0.05;

static LatticeStep turned(const LatticeStep &v, const int quarter_turns)
{
  switch (quarter_turns & 3) {
    case 0:
      return v;
    case 1:
      return {-v[1], v[0]};
    case 2:
      return {-v[0], -v[1]};
    default:
      return {v[1], -v[0]};
  }
}

static LatticeStep plus(const LatticeStep &a, const LatticeStep &b)
{
  return {a[0] + b[0], a[1] + b[1]};
}

static LatticeStep times(const LatticeStep &a, const int sign)
{
  return {a[0] * sign, a[1] * sign};
}

static bool is_zero(const LatticeStep &a)
{
  return a[0] == 0 && a[1] == 0;
}

/** The representatives `rot(q0, i0)` and `rot(q1, i1)` (quarter turns about each normal) closest
 * to each other. */
static void match_orientation(
    const Vec3 &q0, const Vec3 &n0, const Vec3 &q1, const Vec3 &n1, int &i0, int &i1)
{
  const Vec3 a[2] = {q0, cross(n0, q0)};
  const Vec3 b[2] = {q1, cross(n1, q1)};
  double best = -1.0, best_dot = 1.0;
  i0 = i1 = 0;
  for (int i = 0; i < 2; i++) {
    for (int j = 0; j < 2; j++) {
      const double d = dot(a[i], b[j]);
      if (std::fabs(d) > best) {
        best = std::fabs(d);
        best_dot = d;
        i0 = i;
        i1 = j;
      }
    }
  }
  if (best_dot < 0.0) {
    i1 += 2;
  }
}

namespace {

/** How a change `delta` of edge `e`'s step changes a triangle's charge: `sign * turned(delta,
 * quarter_turns)`. */
struct Term {
  int8_t sign = 1;
  int8_t quarter_turns = 0;
};

struct Solver {
  const Surface &s;
  const Fields &f;
  const double scale;
  LatticeEdges &le;
  std::vector<LatticeStep> charge;
  std::vector<uint8_t> singular;
  std::vector<std::array<Term, 3>> terms;
  std::vector<Vec3> centroid;
  std::vector<uint8_t> feature;
  /** Per edge: the field's offset from v0 to v1 before rounding, in v0's frame (lattice steps). */
  std::vector<std::array<double, 2>> continuous;
  /* Search state, reused by every search. */
  std::vector<int> stamp, parent_tri, parent_edge;
  std::vector<double> dist;
  std::vector<LatticeStep> carried;
  int current = 0;

  Solver(const Surface &surface, const Fields &fields, const double spacing, LatticeEdges &out)
      : s(surface), f(fields), scale(spacing), le(out)
  {
  }

  int v0(const int e) const
  {
    return edge_key_v0(le.edges.keys[size_t(e)]);
  }
  int v1(const int e) const
  {
    return edge_key_v1(le.edges.keys[size_t(e)]);
  }

  void read_steps()
  {
    const int ne = le.edges.edges_num();
    le.step.assign(size_t(ne), {0, 0});
    le.turn.assign(size_t(ne), 0);
    le.changed.assign(size_t(ne), 0);
    continuous.assign(size_t(ne), {0.0, 0.0});
    for (int e = 0; e < ne; e++) {
      const int a = v0(e), b = v1(e);
      const Vec3 &na = s.normals[size_t(a)], &nb = s.normals[size_t(b)];
      const Vec3 &qa = f.q[size_t(a)], &qb = f.q[size_t(b)];
      int ia, ib;
      match_orientation(qa, na, qb, nb, ia, ib);
      le.turn[size_t(e)] = uint8_t((ib - ia + 4) & 3);
      /* Read the step in the edge's own frame (normal halfway between the two), on a's matching
       * representative, then express it in a's frame. */
      Vec3 n = normalized(na + nb);
      if (length_squared(n) == 0.0) {
        n = na;
      }
      const Vec3 ra = ia == 0 ? qa : cross(na, qa);
      const Vec3 q = normalized(project_tangent(ra, n));
      const Vec3 t = cross(n, q);
      const Vec3 d = f.o[size_t(b)] - f.o[size_t(a)];
      const LatticeStep local = {int(std::lround(dot(d, q) / scale)),
                                 int(std::lround(dot(d, t) / scale))};
      le.step[size_t(e)] = turned(local, ia);
      const std::array<double, 2> c = {dot(d, q) / scale, dot(d, t) / scale};
      switch (ia & 3) {
        case 0:
          continuous[size_t(e)] = c;
          break;
        default:
          continuous[size_t(e)] = {-c[1], c[0]};
          break;
      }
    }
    feature.assign(size_t(ne), 0);
    for (const std::array<int, 2> &fe : s.feature_edges) {
      const int e = le.edges.find(fe[0], fe[1]);
      if (e >= 0) {
        feature[size_t(e)] = 1;
      }
    }
  }

  /** Charges, orientation singularities and the terms of every triangle. */
  void read_triangles()
  {
    const size_t nt = s.tris.size();
    charge.assign(nt, {0, 0});
    singular.assign(nt, 0);
    terms.assign(nt, {});
    centroid.resize(nt);
    for (size_t tri = 0; tri < nt; tri++) {
      const std::array<int, 3> &c = s.tris[tri];
      centroid[tri] = (s.positions[size_t(c[0])] + s.positions[size_t(c[1])] +
                       s.positions[size_t(c[2])]) /
                      3.0;
      /* `m` turns a step in the current corner's frame into corner 0's frame. */
      int m = 0;
      LatticeStep sum = {0, 0};
      for (int i = 0; i < 3; i++) {
        const int e = le.edges.tri_edges[tri][size_t(i)];
        const int k = le.turn[size_t(e)];
        LatticeStep step;
        int turn_to_next;
        if (c[size_t(i)] == v0(e)) {
          step = le.step[size_t(e)];
          turn_to_next = k;
          terms[tri][size_t(i)] = {1, int8_t(m)};
        }
        else {
          step = times(turned(le.step[size_t(e)], k), -1);
          turn_to_next = (4 - k) & 3;
          terms[tri][size_t(i)] = {-1, int8_t((m + k) & 3)};
        }
        sum = plus(sum, turned(step, m));
        m = (m - turn_to_next + 4) & 3;
      }
      singular[tri] = m != 0;
      charge[tri] = singular[tri] ? LatticeStep{0, 0} : sum;
    }
  }

  /**
   * Whether edge `e` may change to step `step`: from none or one lattice step to none or one, so
   * the change only ever joins two lattice points or splits one (removes or inserts a row). Turning
   * a unit step into a diagonal one (a path along a row, shearing it) leaves the vertices on either
   * side without the edge between them, which the extraction then loses as a hole (2026-11-15: a
   * clean torus, three holes of 12-14 edges). Never across a hard feature.
   */
  bool allowed(const int e, const LatticeStep &step) const
  {
    const LatticeStep &old = le.step[size_t(e)];
    if (std::abs(old[0]) + std::abs(old[1]) > 1 || std::abs(step[0]) + std::abs(step[1]) > 1) {
      return false;
    }
    if (!feature[size_t(e)] || is_zero(step)) {
      return true;
    }
    const int a = v0(e), b = v1(e);
    const Vec3 &n = s.normals[size_t(a)], &q = f.q[size_t(a)];
    const Vec3 across = normalized(cross(n, s.positions[size_t(b)] - s.positions[size_t(a)]));
    const double crossing = double(step[0]) * dot(q, across) +
                            double(step[1]) * dot(cross(n, q), across);
    return std::fabs(crossing) < MAX_FEATURE_CROSSING;
  }

  /** The change of edge `corner`'s step in triangle `tri` that takes `x` out of its charge. */
  LatticeStep delta_removing(const int tri, const int corner, const LatticeStep &x) const
  {
    const Term &term = terms[size_t(tri)][size_t(corner)];
    return times(turned(x, -term.quarter_turns), -term.sign);
  }

  /** Apply the path from the search's start to `tri`, then out through edge `exit` (or -1). */
  void apply_path(int tri, const int exit_corner)
  {
    if (exit_corner >= 0) {
      const int e = le.edges.tri_edges[size_t(tri)][size_t(exit_corner)];
      const LatticeStep d = delta_removing(tri, exit_corner, carried[size_t(tri)]);
      le.step[size_t(e)] = plus(le.step[size_t(e)], d);
      le.changed[size_t(e)] = 1;
    }
    while (parent_tri[size_t(tri)] >= 0) {
      const int p = parent_tri[size_t(tri)], e = parent_edge[size_t(tri)];
      int corner = 0;
      while (le.edges.tri_edges[size_t(p)][size_t(corner)] != e) {
        corner++;
      }
      const LatticeStep d = delta_removing(p, corner, carried[size_t(p)]);
      le.step[size_t(e)] = plus(le.step[size_t(e)], d);
      le.changed[size_t(e)] = 1;
      tri = p;
    }
  }

  /** What changing edge `e`'s step to `step` costs: how much further it moves from the field's
   * continuous offset (in lattice steps), plus a little per edge so the path stays short. */
  double cost(const int e, const LatticeStep &step) const
  {
    const std::array<double, 2> &c = continuous[size_t(e)];
    const LatticeStep &old = le.step[size_t(e)];
    const double after = std::hypot(double(step[0]) - c[0], double(step[1]) - c[1]);
    const double before = std::hypot(double(old[0]) - c[0], double(old[1]) - c[1]);
    return std::max(after - before, 0.0) + EDGE_COST;
  }

  /**
   * Cancel the charge of `start` within `radius` along the cheapest path of triangles (Dijkstra on
   * #cost): to the opposite charge, out through a border, or into an orientation singularity.
   * Returns whether it was cancelled.
   */
  bool cancel(const int start, const double radius)
  {
    current++;
    const double radius2 = radius * radius;
    using Item = std::pair<double, int>;
    std::priority_queue<Item, std::vector<Item>, std::greater<>> queue;
    stamp[size_t(start)] = current;
    dist[size_t(start)] = 0.0;
    parent_tri[size_t(start)] = -1;
    parent_edge[size_t(start)] = -1;
    carried[size_t(start)] = charge[size_t(start)];
    queue.push({0.0, start});
    /* The cheapest end found so far: a triangle (pair or singularity) or a border exit. */
    double end_cost = std::numeric_limits<double>::infinity();
    int end_tri = -1, end_exit = -1, end_from = -1, end_edge = -1;
    bool end_pair = false;
    LatticeStep end_carried = {0, 0};
    while (!queue.empty()) {
      const auto [d_tri, tri] = queue.top();
      queue.pop();
      if (d_tri >= end_cost) {
        break;
      }
      if (d_tri > dist[size_t(tri)]) {
        continue;
      }
      const LatticeStep x = carried[size_t(tri)];
      for (int corner = 0; corner < 3; corner++) {
        const int e = le.edges.tri_edges[size_t(tri)][size_t(corner)];
        if (e == parent_edge[size_t(tri)]) {
          continue;
        }
        const LatticeStep d = delta_removing(tri, corner, x);
        const LatticeStep after = plus(le.step[size_t(e)], d);
        if (!allowed(e, after)) {
          continue;
        }
        const double c = d_tri + cost(e, after);
        const int users = le.edges.count(e);
        if (users == 1) {
          if (c < end_cost) {
            end_cost = c;
            end_tri = tri;
            end_exit = corner;
            end_pair = false;
          }
          continue;
        }
        if (users != 2) {
          continue;
        }
        const int first = le.edges.tris[size_t(le.edges.offsets[size_t(e)])];
        const int next = first == tri ? le.edges.tris[size_t(le.edges.offsets[size_t(e)] + 1)] :
                                        first;
        if (next == start ||
            distance_squared(centroid[size_t(next)], centroid[size_t(start)]) > radius2)
        {
          continue;
        }
        int next_corner = 0;
        while (le.edges.tri_edges[size_t(next)][size_t(next_corner)] != e) {
          next_corner++;
        }
        const Term &term = terms[size_t(next)][size_t(next_corner)];
        const LatticeStep arriving = times(turned(d, term.quarter_turns), term.sign);
        /* A triangle already on the search tree is never an end: hanging it off one of its own
         * descendants would make the path a loop. */
        const bool pair = !singular[size_t(next)] && stamp[size_t(next)] != current &&
                          is_zero(plus(charge[size_t(next)], arriving));
        if (singular[size_t(next)] || pair) {
          if (c < end_cost) {
            end_cost = c;
            end_tri = next;
            end_exit = -1;
            end_from = tri;
            end_edge = e;
            end_carried = arriving;
            end_pair = pair;
          }
          continue;
        }
        if (stamp[size_t(next)] == current && dist[size_t(next)] <= c) {
          continue;
        }
        stamp[size_t(next)] = current;
        dist[size_t(next)] = c;
        parent_tri[size_t(next)] = tri;
        parent_edge[size_t(next)] = e;
        carried[size_t(next)] = arriving;
        queue.push({c, next});
      }
    }
    if (end_tri < 0) {
      return false;
    }
    if (end_exit < 0) {
      /* The end triangle is not on the search tree: hang it off the triangle it was reached from
       * (its own search state, if any, belongs to a costlier route). */
      stamp[size_t(end_tri)] = current;
      parent_tri[size_t(end_tri)] = end_from;
      parent_edge[size_t(end_tri)] = end_edge;
      carried[size_t(end_tri)] = end_carried;
    }
    apply_path(end_tri, end_exit);
    charge[size_t(start)] = {0, 0};
    if (end_pair) {
      charge[size_t(end_tri)] = {0, 0};
      le.pairs_cancelled++;
    }
    else {
      le.charges_moved_out++;
    }
    return true;
  }

  void cancel_all()
  {
    const size_t nt = s.tris.size();
    stamp.assign(nt, 0);
    parent_tri.assign(nt, -1);
    parent_edge.assign(nt, -1);
    dist.assign(nt, 0.0);
    carried.assign(nt, {0, 0});
    for (size_t tri = 0; tri < nt; tri++) {
      le.charges += is_zero(charge[tri]) ? 0 : 1;
    }
    for (const double radius_of_scale : PASS_RADII_OF_SCALE) {
      for (size_t tri = 0; tri < nt; tri++) {
        if (!is_zero(charge[tri])) {
          cancel(int(tri), radius_of_scale * scale);
        }
      }
    }
  }
};

}  // namespace

LatticeEdges lattice_edges(const Surface &surface, const Fields &fields, const double scale)
{
  LatticeEdges le;
  le.edges.build(surface.tris);
  Solver solver(surface, fields, scale, le);
  solver.read_steps();
  solver.read_triangles();
  solver.cancel_all();
  return le;
}

std::vector<Vec3> positions_with_steps(const Surface &surface,
                                       const Fields &fields,
                                       const LatticeEdges &lattice,
                                       const double scale)
{
  std::vector<Vec3> o = fields.o;
  if (std::find(lattice.changed.begin(), lattice.changed.end(), 1) == lattice.changed.end()) {
    return o;
  }
  const int nv = surface.verts_num();
  const int ne = lattice.edges.edges_num();
  /* Each edge's step as a vector in each end's tangent frame. */
  std::vector<Vec3> forward(static_cast<size_t>(ne)), backward(static_cast<size_t>(ne));
  for (int e = 0; e < ne; e++) {
    const uint64_t key = lattice.edges.keys[size_t(e)];
    const int a = edge_key_v0(key), b = edge_key_v1(key);
    const LatticeStep &st = lattice.step[size_t(e)];
    const Vec3 &na = surface.normals[size_t(a)], &qa = fields.q[size_t(a)];
    forward[size_t(e)] = (qa * double(st[0]) + cross(na, qa) * double(st[1])) * scale;
    /* The step from b to a in b's frame. */
    const LatticeStep back = times(turned(st, lattice.turn[size_t(e)]), -1);
    const Vec3 &nb = surface.normals[size_t(b)], &qb = fields.q[size_t(b)];
    backward[size_t(e)] = (qb * double(back[0]) + cross(nb, qb) * double(back[1])) * scale;
  }
  std::vector<Vec3> sum(static_cast<size_t>(nv));
  std::vector<int> count(static_cast<size_t>(nv));
  for (int pass = 0; pass < SMOOTHING_PASSES; pass++) {
    /* Jacobi: each vertex's lattice point where its neighbours' points and the steps put it, its
     * own point counting as one neighbour (damps sliding along the surface). */
    for (int v = 0; v < nv; v++) {
      sum[size_t(v)] = o[size_t(v)];
      count[size_t(v)] = 1;
    }
    for (int e = 0; e < ne; e++) {
      const uint64_t key = lattice.edges.keys[size_t(e)];
      const int a = edge_key_v0(key), b = edge_key_v1(key);
      sum[size_t(a)] += o[size_t(b)] - forward[size_t(e)];
      sum[size_t(b)] += o[size_t(a)] - backward[size_t(e)];
      count[size_t(a)]++;
      count[size_t(b)]++;
    }
    for (int v = 0; v < nv; v++) {
      const Vec3 &n = surface.normals[size_t(v)];
      const Vec3 mean = sum[size_t(v)] / double(count[size_t(v)]);
      Vec3 point = mean - n * dot(n, mean - surface.positions[size_t(v)]);
      if (surface.pin_kind[size_t(v)] == FeatureKind::Line) {
        /* Keep the lattice line on a pinned feature line, as the field's own smoothing does. */
        const Vec3 t = cross(n, fields.q[size_t(v)]);
        point += t * dot(t, surface.positions[size_t(v)] - point);
      }
      o[size_t(v)] = point;
    }
  }
  return o;
}

}  // namespace blender::geometry::retopo
