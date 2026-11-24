/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup geo
 *
 * Orientation and position field smoothing (Jakob et al. 2015, "Instant Field-Aligned Meshes",
 * sections 3.1-3.3, the extrinsic formulation). Written from the paper's equations.
 */

#include <algorithm>
#include <cmath>

#include "retopo_field.hh"
#include "retopo_parallel.hh"

namespace blender::geometry::retopo {

void compat_orientation(
    const Vec3 &q0, const Vec3 &n0, const Vec3 &q1, const Vec3 &n1, Vec3 &r0, Vec3 &r1)
{
  /* The two representatives of each 4-RoSy that differ by 90 degrees; signs handled by the dot
   * product. */
  const Vec3 a[2] = {q0, cross(n0, q0)};
  const Vec3 b[2] = {q1, cross(n1, q1)};
  double best = -1.0;
  int bi = 0, bj = 0;
  for (int i = 0; i < 2; i++) {
    for (int j = 0; j < 2; j++) {
      const double score = std::fabs(dot(a[i], b[j]));
      if (score > best) {
        best = score;
        bi = i;
        bj = j;
      }
    }
  }
  r0 = a[bi];
  r1 = dot(a[bi], b[bj]) < 0.0 ? -b[bj] : b[bj];
}

/** The point nearest to both tangent planes, near the middle of the two vertices (paper, eq. 6).
 */
static Vec3 middle_point(const Vec3 &p0, const Vec3 &n0, const Vec3 &p1, const Vec3 &n1)
{
  const double n0p0 = dot(n0, p0), n0p1 = dot(n0, p1), n1p0 = dot(n1, p0), n1p1 = dot(n1, p1);
  const double n0n1 = dot(n0, n1);
  const double denom = 1.0 / (1.0 - n0n1 * n0n1 + 1e-4);
  const double lambda0 = 2.0 * (n0p1 - n0p0 - n0n1 * (n1p0 - n1p1)) * denom;
  const double lambda1 = 2.0 * (n1p0 - n1p1 - n0n1 * (n0p1 - n0p0)) * denom;
  return (p0 + p1) * 0.5 - (n0 * lambda0 + n1 * lambda1) * 0.25;
}

static Vec3 position_floor(
    const Vec3 &o, const Vec3 &q, const Vec3 &n, const Vec3 &p, const double scale)
{
  const Vec3 t = cross(n, q);
  const Vec3 d = p - o;
  return o + q * (std::floor(dot(q, d) / scale) * scale) +
         t * (std::floor(dot(t, d) / scale) * scale);
}

Vec3 position_round(const Vec3 &o, const Vec3 &q, const Vec3 &n, const Vec3 &p, const double scale)
{
  const Vec3 t = cross(n, q);
  const Vec3 d = p - o;
  return o + q * (std::round(dot(q, d) / scale) * scale) +
         t * (std::round(dot(t, d) / scale) * scale);
}

void compat_position(const Vec3 &p0,
                     const Vec3 &n0,
                     const Vec3 &q0,
                     const Vec3 &o0,
                     const Vec3 &p1,
                     const Vec3 &n1,
                     const Vec3 &q1,
                     const Vec3 &o1,
                     const double scale,
                     Vec3 &r0,
                     Vec3 &r1)
{
  const Vec3 t0 = cross(n0, q0), t1 = cross(n1, q1);
  const Vec3 middle = middle_point(p0, n0, p1, n1);
  const Vec3 base0 = position_floor(o0, q0, n0, middle, scale);
  const Vec3 base1 = position_floor(o1, q1, n1, middle, scale);
  double best = 1e300;
  for (int i = 0; i < 4; i++) {
    const Vec3 c0 = base0 + (q0 * double(i & 1) + t0 * double((i & 2) >> 1)) * scale;
    for (int j = 0; j < 4; j++) {
      const Vec3 c1 = base1 + (q1 * double(j & 1) + t1 * double((j & 2) >> 1)) * scale;
      const double cost = distance_squared(c0, c1);
      if (cost < best) {
        best = cost;
        r0 = c0;
        r1 = c1;
      }
    }
  }
}

static bool orientation_fixed(const Level &level, const int v)
{
  return level.feature_kind[size_t(v)] == FeatureKind::Line || level.guided[size_t(v)];
}

static void smooth_orientation(Level &level, std::vector<Vec3> &q, const double curvature_weight)
{
  for (const std::vector<int> &colour : level.colours) {
    parallel_for(int64_t(colour.size()), 256, [&](const int64_t k) {
      const int v = colour[size_t(k)];
      const Vec3 &n = level.normals[size_t(v)];
      if (orientation_fixed(level, v)) {
        q[size_t(v)] = level.feature_dir[size_t(v)];
        return;
      }
      Vec3 sum = q[size_t(v)];
      double weight_sum = 0.0;
      for (int e = level.adjacency_offsets[size_t(v)]; e < level.adjacency_offsets[size_t(v) + 1];
           e++)
      {
        const int j = level.adjacency[size_t(e)];
        const double w = level.weights[size_t(e)];
        Vec3 a, b;
        compat_orientation(sum, n, q[size_t(j)], level.normals[size_t(j)], a, b);
        sum = normalized(project_tangent(a * weight_sum + b * w, n));
        weight_sum += w;
      }
      const double conf = level.curvature_confidence[size_t(v)];
      if (conf > 0.0 && curvature_weight > 0.0 && weight_sum > 0.0) {
        const double w = curvature_weight * conf * weight_sum;
        Vec3 a, b;
        compat_orientation(sum, n, level.curvature_dir[size_t(v)], n, a, b);
        sum = normalized(project_tangent(a * weight_sum + b * w, n));
      }
      if (length_squared(sum) > 0.0) {
        q[size_t(v)] = sum;
      }
    });
  }
}

/**
 * Keep a lattice line on each pinned feature line (borders, marked edges). Sharp creases are
 * aligned by the extrinsic smoothing itself (`Surface::feature_pinned`). Corners are not pinned:
 * the two lines meeting there already fix the lattice point, and pinning it to the exact corner
 * fought the offset the rest of the lattice settled on, leaving a dislocation beside the corner
 * (2026-11-14, an open box's rim corners).
 */
static Vec3 constrain_position(
    const Level &level, const int v, const Vec3 &q, Vec3 o, const bool pins)
{
  if (!pins) {
    return o;
  }
  if (level.pin_kind[size_t(v)] == FeatureKind::Line) {
    const Vec3 t = cross(level.normals[size_t(v)], q);
    return o + t * dot(t, level.pin_point[size_t(v)] - o);
  }
  return o;
}

static void smooth_position(Level &level,
                            const std::vector<Vec3> &q,
                            std::vector<Vec3> &o,
                            const double scale,
                            const bool pins)
{
  for (const std::vector<int> &colour : level.colours) {
    parallel_for(int64_t(colour.size()), 256, [&](const int64_t k) {
      const int v = colour[size_t(k)];
      const Vec3 &p = level.positions[size_t(v)], &n = level.normals[size_t(v)],
                 &qv = q[size_t(v)];
      Vec3 sum = o[size_t(v)];
      double weight_sum = 0.0;
      for (int e = level.adjacency_offsets[size_t(v)]; e < level.adjacency_offsets[size_t(v) + 1];
           e++)
      {
        const int j = level.adjacency[size_t(e)];
        const double w = level.weights[size_t(e)];
        const Vec3 &nj = level.normals[size_t(j)];
        Vec3 qa, qb;
        compat_orientation(qv, n, q[size_t(j)], nj, qa, qb);
        Vec3 a, b;
        compat_position(
            p, n, qa, sum, level.positions[size_t(j)], nj, qb, o[size_t(j)], scale, a, b);
        sum = (a * weight_sum + b * w) / (weight_sum + w);
        weight_sum += w;
        sum -= n * dot(n, sum - p);
      }
      o[size_t(v)] = constrain_position(level, v, qv, position_round(sum, qv, n, p, scale), pins);
    });
  }
}

bool solve_position(Hierarchy &h,
                    const double scale,
                    const Params &params,
                    Fields &fields,
                    const std::function<bool()> &cancelled)
{
  const int levels = int(h.levels.size());
  const std::vector<std::vector<Vec3>> &q = fields.q_levels;
  std::vector<std::vector<Vec3>> o(static_cast<size_t>(levels));
  /* Coarse to fine; pins on every level, through each vertex's pin point (`Level::pin_point`). */
  for (int l = levels - 1; l >= 0; l--) {
    Level &level = h.levels[size_t(l)];
    const bool pins = true;
    o[size_t(l)].resize(size_t(level.size()));
    for (int v = 0; v < level.size(); v++) {
      const Vec3 &p = level.positions[size_t(v)], &n = level.normals[size_t(v)];
      Vec3 init = p;
      if (l < levels - 1) {
        const Vec3 &oc = o[size_t(l + 1)][size_t(level.parent[size_t(v)])];
        init = position_round(oc - n * dot(n, oc - p), q[size_t(l)][size_t(v)], n, p, scale);
      }
      o[size_t(l)][size_t(v)] = constrain_position(level, v, q[size_t(l)][size_t(v)], init, pins);
    }
    for (int it = 0; it < params.field_iterations; it++) {
      smooth_position(level, q[size_t(l)], o[size_t(l)], scale, pins);
    }
    if (cancelled && cancelled()) {
      return false;
    }
  }
  fields.o = std::move(o[0]);
  return true;
}

bool solve_fields(Hierarchy &h,
                  const double scale,
                  const Params &params,
                  Fields &fields,
                  const std::function<bool()> &cancelled,
                  const std::function<void(int)> &stage)
{
  const int levels = int(h.levels.size());
  std::vector<std::vector<Vec3>> q(static_cast<size_t>(levels));

  /* Orientation, coarse to fine. */
  for (int l = levels - 1; l >= 0; l--) {
    Level &level = h.levels[size_t(l)];
    q[size_t(l)].resize(size_t(level.size()));
    for (int v = 0; v < level.size(); v++) {
      const Vec3 &n = level.normals[size_t(v)];
      Vec3 init;
      if (l == levels - 1) {
        init = any_perpendicular(n);
      }
      else {
        init = normalized(project_tangent(q[size_t(l + 1)][size_t(level.parent[size_t(v)])], n));
        if (length_squared(init) == 0.0) {
          init = any_perpendicular(n);
        }
      }
      q[size_t(l)][size_t(v)] = orientation_fixed(level, v) ? level.feature_dir[size_t(v)] : init;
    }
    for (int it = 0; it < params.field_iterations; it++) {
      smooth_orientation(level, q[size_t(l)], params.curvature_weight);
    }
    if (cancelled && cancelled()) {
      return false;
    }
  }
  if (stage) {
    stage(0);
  }
  fields.q_levels = std::move(q);
  fields.q = fields.q_levels[0];
  if (!solve_position(h, scale, params, fields, cancelled)) {
    return false;
  }
  if (stage) {
    stage(1);
  }
  return true;
}

}  // namespace blender::geometry::retopo
