/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Test meshes for the retopology core, generated in code (no files, no LFS): primitives of
 * different genus and sharpness, and the defects AI-generated and scanned meshes carry (triangle
 * soup, flipped faces, non-manifold fins, several shells, noise).
 */

#pragma once

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <map>
#include <utility>
#include <vector>

#include "../intern/retopology/retopo_core.hh"

namespace blender::geometry::retopo::test {

constexpr double PI = 3.14159265358979323846;

/** Deterministic hash noise in [-1, 1]. */
inline double hash_noise(uint64_t i)
{
  i ^= i >> 33;
  i *= 0xff51afd7ed558ccdULL;
  i ^= i >> 33;
  i *= 0xc4ceb9fe1a85ec53ULL;
  i ^= i >> 33;
  return double(i % 2000001) / 1000000.0 - 1.0;
}

/** Icosphere of radius `r`, `levels` subdivisions (20 * 4^levels triangles). */
inline TriMesh icosphere(const int levels, const double r = 1.0, const Vec3 center = {})
{
  const double t = (1.0 + std::sqrt(5.0)) / 2.0;
  TriMesh m;
  const Vec3 base[12] = {{-1, t, 0},
                         {1, t, 0},
                         {-1, -t, 0},
                         {1, -t, 0},
                         {0, -1, t},
                         {0, 1, t},
                         {0, -1, -t},
                         {0, 1, -t},
                         {t, 0, -1},
                         {t, 0, 1},
                         {-t, 0, -1},
                         {-t, 0, 1}};
  for (const Vec3 &p : base) {
    m.positions.push_back(normalized(p));
  }
  m.tris = {{0, 11, 5},  {0, 5, 1},  {0, 1, 7},  {0, 7, 10}, {0, 10, 11}, {1, 5, 9}, {5, 11, 4},
            {11, 10, 2}, {10, 7, 6}, {7, 1, 8},  {3, 9, 4},  {3, 4, 2},   {3, 2, 6}, {3, 6, 8},
            {3, 8, 9},   {4, 9, 5},  {2, 4, 11}, {6, 2, 10}, {8, 6, 7},   {9, 8, 1}};
  for (int l = 0; l < levels; l++) {
    std::map<std::pair<int, int>, int> mid;
    auto midpoint = [&](int a, int b) {
      const std::pair<int, int> key(std::min(a, b), std::max(a, b));
      auto it = mid.find(key);
      if (it != mid.end()) {
        return it->second;
      }
      m.positions.push_back(normalized(m.positions[a] + m.positions[b]));
      const int i = int(m.positions.size()) - 1;
      mid[key] = i;
      return i;
    };
    std::vector<std::array<int, 3>> next;
    for (const auto &tri : m.tris) {
      const int a = midpoint(tri[0], tri[1]), b = midpoint(tri[1], tri[2]),
                c = midpoint(tri[2], tri[0]);
      next.push_back({tri[0], a, c});
      next.push_back({tri[1], b, a});
      next.push_back({tri[2], c, b});
      next.push_back({a, b, c});
    }
    m.tris = std::move(next);
  }
  for (Vec3 &p : m.positions) {
    p = center + p * r;
  }
  return m;
}

/** Torus around Z (genus 1), `nu` x `nv` quads split into triangles. */
inline TriMesh torus(const int nu, const int nv, const double R = 1.0, const double r = 0.35)
{
  TriMesh m;
  for (int i = 0; i < nu; i++) {
    const double u = 2.0 * PI * i / nu;
    for (int j = 0; j < nv; j++) {
      const double v = 2.0 * PI * j / nv;
      m.positions.push_back({(R + r * std::cos(v)) * std::cos(u),
                             (R + r * std::cos(v)) * std::sin(u),
                             r * std::sin(v)});
    }
  }
  auto id = [&](int i, int j) { return ((i + nu) % nu) * nv + (j + nv) % nv; };
  for (int i = 0; i < nu; i++) {
    for (int j = 0; j < nv; j++) {
      m.tris.push_back({id(i, j), id(i + 1, j), id(i + 1, j + 1)});
      m.tris.push_back({id(i, j), id(i + 1, j + 1), id(i, j + 1)});
    }
  }
  return m;
}

/** Axis-aligned box from `lo` to `hi`, each side an `n` x `n` grid split into triangles (diagonals
 * alternate). */
inline TriMesh box(const int n, const Vec3 lo = {-1, -1, -1}, const Vec3 hi = {1, 1, 1})
{
  TriMesh m;
  std::map<std::array<long, 3>, int> index;
  auto vert = [&](const Vec3 &p) {
    const std::array<long, 3> key = {
        std::lround(p.x * 1e6), std::lround(p.y * 1e6), std::lround(p.z * 1e6)};
    auto it = index.find(key);
    if (it != index.end()) {
      return it->second;
    }
    m.positions.push_back(p);
    index[key] = int(m.positions.size()) - 1;
    return int(m.positions.size()) - 1;
  };
  for (int axis = 0; axis < 3; axis++) {
    for (int side = 0; side < 2; side++) {
      const int a = (axis + 1) % 3, b = (axis + 2) % 3;
      auto point = [&](int i, int j) {
        Vec3 p;
        p[axis] = side ? hi[axis] : lo[axis];
        p[a] = lo[a] + (hi[a] - lo[a]) * i / n;
        p[b] = lo[b] + (hi[b] - lo[b]) * j / n;
        return vert(p);
      };
      for (int i = 0; i < n; i++) {
        for (int j = 0; j < n; j++) {
          int v00 = point(i, j), v10 = point(i + 1, j), v11 = point(i + 1, j + 1),
              v01 = point(i, j + 1);
          if (side == 0) {
            std::swap(v10, v01);
          }
          if ((i + j) % 2) {
            m.tris.push_back({v00, v10, v11});
            m.tris.push_back({v00, v11, v01});
          }
          else {
            m.tris.push_back({v00, v10, v01});
            m.tris.push_back({v10, v11, v01});
          }
        }
      }
    }
  }
  return m;
}

/** Every triangle with its own three vertices, as AI exporters and STL files deliver meshes. */
inline TriMesh to_soup(const TriMesh &m)
{
  TriMesh s;
  for (const auto &t : m.tris) {
    const int base = int(s.positions.size());
    for (int c = 0; c < 3; c++) {
      s.positions.push_back(m.positions[t[c]]);
    }
    s.tris.push_back({base, base + 1, base + 2});
  }
  return s;
}

inline TriMesh join(const TriMesh &a, const TriMesh &b)
{
  TriMesh m = a;
  const int offset = int(a.positions.size());
  m.positions.insert(m.positions.end(), b.positions.begin(), b.positions.end());
  for (const auto &t : b.tris) {
    m.tris.push_back({t[0] + offset, t[1] + offset, t[2] + offset});
  }
  return m;
}

/** Flip every `every`-th triangle's winding (inconsistent orientation). */
inline TriMesh flip_some(TriMesh m, const int every)
{
  for (size_t i = 0; i < m.tris.size(); i += size_t(every)) {
    std::swap(m.tris[i][1], m.tris[i][2]);
  }
  return m;
}

/** Move every vertex along its radial direction from `center` by up to `amount` (scan-like noise).
 */
inline TriMesh radial_noise(TriMesh m, const double amount, const Vec3 center = {})
{
  for (size_t i = 0; i < m.positions.size(); i++) {
    const Vec3 d = normalized(m.positions[i] - center);
    m.positions[i] += d * (amount * hash_noise(i + 17));
  }
  return m;
}

/** Add a fin: a triangle sharing an existing edge as a third face (non-manifold edge), plus a
 * duplicate face. */
inline TriMesh add_fin_and_duplicate(TriMesh m)
{
  const auto t = m.tris[0];
  const Vec3 a = m.positions[t[0]], b = m.positions[t[1]];
  const Vec3 out = (a + b) * 0.5 + normalized(cross(b - a, Vec3(0.3, 0.7, 0.1))) * distance(a, b);
  m.positions.push_back(out);
  m.tris.push_back({t[0], t[1], int(m.positions.size()) - 1});
  m.tris.push_back(m.tris[1]);
  return m;
}

/** Bounding-box diagonal. */
inline double diagonal(const TriMesh &m)
{
  Vec3 lo(1e300, 1e300, 1e300), hi(-1e300, -1e300, -1e300);
  for (const Vec3 &p : m.positions) {
    lo = min_v(lo, p);
    hi = max_v(hi, p);
  }
  return distance(lo, hi);
}

}  // namespace blender::geometry::retopo::test
