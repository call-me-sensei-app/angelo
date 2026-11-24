/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup geo
 *
 * A small double-precision vector for the retopology core. Double, because the position field
 * compares lattice points that differ by integer multiples of an edge length on meshes far from
 * the origin.
 */

#pragma once

#include <cmath>

namespace blender::geometry::retopo {

struct Vec3 {
  double x = 0.0, y = 0.0, z = 0.0;

  Vec3() = default;
  constexpr Vec3(const double x, const double y, const double z) : x(x), y(y), z(z) {}

  double &operator[](const int i)
  {
    return i == 0 ? x : (i == 1 ? y : z);
  }
  double operator[](const int i) const
  {
    return i == 0 ? x : (i == 1 ? y : z);
  }
  Vec3 operator+(const Vec3 &b) const
  {
    return {x + b.x, y + b.y, z + b.z};
  }
  Vec3 operator-(const Vec3 &b) const
  {
    return {x - b.x, y - b.y, z - b.z};
  }
  Vec3 operator-() const
  {
    return {-x, -y, -z};
  }
  Vec3 operator*(const double s) const
  {
    return {x * s, y * s, z * s};
  }
  Vec3 operator/(const double s) const
  {
    return {x / s, y / s, z / s};
  }
  Vec3 &operator+=(const Vec3 &b)
  {
    x += b.x;
    y += b.y;
    z += b.z;
    return *this;
  }
  Vec3 &operator-=(const Vec3 &b)
  {
    x -= b.x;
    y -= b.y;
    z -= b.z;
    return *this;
  }
  Vec3 &operator*=(const double s)
  {
    x *= s;
    y *= s;
    z *= s;
    return *this;
  }
};

inline Vec3 operator*(const double s, const Vec3 &v)
{
  return v * s;
}
inline double dot(const Vec3 &a, const Vec3 &b)
{
  return a.x * b.x + a.y * b.y + a.z * b.z;
}
inline Vec3 cross(const Vec3 &a, const Vec3 &b)
{
  return {a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x};
}
inline double length_squared(const Vec3 &a)
{
  return dot(a, a);
}
inline double length(const Vec3 &a)
{
  return std::sqrt(dot(a, a));
}
inline double distance(const Vec3 &a, const Vec3 &b)
{
  return length(a - b);
}
inline double distance_squared(const Vec3 &a, const Vec3 &b)
{
  return length_squared(a - b);
}
/** Unit vector, or zero for a (near) zero input. */
inline Vec3 normalized(const Vec3 &a)
{
  const double l = length(a);
  return l > 1e-300 ? a / l : Vec3();
}
inline Vec3 min_v(const Vec3 &a, const Vec3 &b)
{
  return {std::fmin(a.x, b.x), std::fmin(a.y, b.y), std::fmin(a.z, b.z)};
}
inline Vec3 max_v(const Vec3 &a, const Vec3 &b)
{
  return {std::fmax(a.x, b.x), std::fmax(a.y, b.y), std::fmax(a.z, b.z)};
}
/** `v` with its component along unit `n` removed. */
inline Vec3 project_tangent(const Vec3 &v, const Vec3 &n)
{
  return v - n * dot(n, v);
}
/** Any unit vector perpendicular to unit `n`. */
inline Vec3 any_perpendicular(const Vec3 &n)
{
  const Vec3 a = std::fabs(n.x) < 0.9 ? Vec3(1, 0, 0) : Vec3(0, 1, 0);
  return normalized(cross(n, a));
}

}  // namespace blender::geometry::retopo
