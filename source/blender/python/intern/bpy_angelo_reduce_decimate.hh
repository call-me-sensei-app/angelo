/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 *
 * Blender's own Decimate (Collapse) on a copy of a mesh, for the game-ready check's side-by-side
 * comparison: the same `BM_mesh_decimate_collapse` call the Decimate modifier makes, on a BMesh converted
 * the way the modifier converts it, so the result is what the modifier would give for that input at a ratio
 * below 1 (the modifier alone returns its input untouched at ratio 1 or with 3 faces or fewer). The mesh
 * passed in is only read and nothing is added to any Main.
 *
 * A snapshot (`snapshot`) is a copy of a mesh in no Main, its arrays implicitly shared with the
 * original: made on the main thread at almost no cost, it is the check's own from then on (an edit
 * of the original copies what it writes, a freed original frees nothing shared), so Decimate can
 * run on it off the main thread. `convex_hull` is Blender's `convex_hull` operator (Bullet) on
 * points in a BMesh of its own.
 */

#pragma once

#include <string>

#include "BLI_array.hh"
#include "BLI_math_vector_types.hh"
#include "BLI_span.hh"
#include "BLI_vector.hh"

struct Mesh;

namespace blender::angelo_reduce {

struct DecimateResult {
  Array<float3> positions;
  /** Vertex indices of each triangle (the result's corner triangles). */
  Array<int3> tris;
  /** The active UV map at each triangle corner (3 per triangle), empty without a UV map. */
  Array<float2> corner_uvs;
  /** Blender's corner normals of the result at each triangle corner (3 per triangle). */
  Array<float3> corner_normals;
  /** Per vertex, the weight of each requested vertex group, row major; empty without groups. */
  Array<float> weights;
  std::string uv_map;
};

/**
 * Decimate (Collapse) `mesh` to `ratio` of its faces, as the modifier with the same settings does
 * (`symmetry_axis` -1 for none, 0-2 for X-Z). `groups` are vertex group indices whose weights the result
 * reports, interpolated by the collapse itself.
 */
DecimateResult decimate(const Mesh &mesh, float ratio, bool triangulate, int symmetry_axis, Span<int> groups);

/** Per vertex, the weight of each of `groups` (vertex group indices), row major; zero where unassigned. */
Array<float> vertex_weights(const Mesh &mesh, Span<int> groups);

/**
 * A copy of `mesh` in no Main, its arrays shared with it (main thread); free it with
 * `snapshot_free`.
 */
Mesh *snapshot(const Mesh &mesh);
void snapshot_free(Mesh *mesh);

#ifdef WITH_BULLET
/**
 * The convex hull of `points` as triangles over its own vertices (`r_positions`, `r_tris`), from
 * Blender's `convex_hull` operator; any thread. False (and nothing returned) when it made no
 * faces, as for fewer than four points not on one plane.
 */
bool convex_hull(Span<float3> points, Vector<float3> &r_positions, Vector<int3> &r_tris);
#endif

}  // namespace blender::angelo_reduce
