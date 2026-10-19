/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 * SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once

#include "BLI_function_ref.hh"
#include "BLI_math_vector_types.hh"
#include <optional>

namespace blender {
struct Depsgraph;
struct Object;
namespace bke::angelo {
struct SurfaceSample {
  float3 position;
  float3 normal;
  int face = -1;
  int vertex = -1;
  int grid = -1;
  float distance = 0;
};
/**
 * Main-thread, live PBVH queries. No mesh export, second BVH, or cached pointer.
 *
 * \a depsgraph is called only when the answer depends on the evaluated object (Multires grids,
 * Vertex and Weight Paint), so a read in Sculpt Mode needs no dependency-graph lookup.
 */
std::optional<SurfaceSample> surface_query(FunctionRef<const Depsgraph &()> depsgraph,
                                           Object &object,
                                           const float3 &origin,
                                           const float3 &direction,
                                           float max_distance);
std::optional<SurfaceSample> surface_query(const Depsgraph &depsgraph,
                                           Object &object,
                                           const float3 &origin,
                                           const float3 &direction,
                                           float max_distance);
std::optional<float3> surface_vertex(FunctionRef<const Depsgraph &()> depsgraph,
                                     Object &object,
                                     int index);
std::optional<float3> surface_vertex(const Depsgraph &depsgraph, Object &object, int index);
}  // namespace bke::angelo
}  // namespace blender
