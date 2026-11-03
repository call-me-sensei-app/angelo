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
 */

#pragma once

#include <string>

#include "BLI_array.hh"
#include "BLI_math_vector_types.hh"
#include "BLI_span.hh"

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

}  // namespace blender::angelo_reduce
