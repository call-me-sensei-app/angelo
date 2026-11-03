/* SPDX-FileCopyrightText: 2026 Angelo Contributors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup bke
 *
 * Angelo: what a voxel remesh will produce and need, before it runs (Blender #77868). A voxel size
 * that is small for the object makes OpenVDB and the new mesh take more memory than the computer
 * has free, and Blender freezes or is killed. Every voxel remesh (the operator, its Sculpt Mode
 * shortcut, the Remesh modifier and Python) is checked against the same estimate. The per-vertex
 * costs were measured; see doc/angelo/voxel_remesh_guard.rst.
 */

#pragma once

#include <cstddef>
#include <cstdint>
#include <string>

namespace blender {

struct Mesh;

namespace bke {

/** Work a voxel remesh does after building the new surface, which adds to its peak memory. */
struct VoxelRemeshExtras {
  /** Preserve Volume or Preserve Attributes: the new mesh is projected onto, or sampled from, the
   * old one. */
  bool reprojects = false;
  /** Fix Poles (only applies without adaptivity). */
  bool fixes_poles = false;
};

enum class VoxelRemeshSize {
  /** Runs without asking. */
  Fits,
  /** Runs, but an interactive remesh asks first: many vertices, or half the free memory. */
  Large,
  /** Refused everywhere: more memory than is free, or more elements than a mesh can index. */
  TooLarge,
};

struct VoxelRemeshEstimate {
  /** Sum over the triangles the remesher receives of |Ax| + |Ay| + |Az| (the triangle's vector
   * area), in squared mesh units, twice that for an open or non-manifold surface (the level set
   * wraps both sides): marching cubes crosses the surface once per voxel column on each axis
   * plane, so the output has about this / size^2 vertices. Per triangle, so the parts of a
   * non-planar or crossed face never cancel. Overlapping or internal surfaces, and partly open
   * ones, are counted in full, so the estimate is high for them, never low. */
  double projected_area = 0.0;
  double vertices = 0.0;
  double peak_bytes = 0.0;
  /** Memory this remesh may use: what the system reports free, less a reserve for the system and
   * Blender, less what remeshes running at the same time reserved. */
  double free_bytes = 0.0;
  /** #free_bytes before subtracting the running remeshes' reservations (the reservation ceiling). */
  double unreserved_bytes = 0.0;
  VoxelRemeshSize size = VoxelRemeshSize::Fits;
  /** The smallest voxel size (two significant digits, rounded up) that would run without asking;
   * it follows the surface, so it scales with the object. 0 when no size fits: the source mesh
   * alone needs more than half the free memory. */
  float suggested_voxel_size = 0.0f;
};

/**
 * Estimate a voxel remesh of \a mesh (in its own coordinates) at \a voxel_size, against the memory
 * free now. One parallel pass over the triangles (#Mesh::corner_tris, which the remesh reuses)
 * and one over the corners (a byte per edge).
 */
VoxelRemeshEstimate voxel_remesh_estimate(const Mesh &mesh,
                                          float voxel_size,
                                          const VoxelRemeshExtras &extras = {});

/**
 * Memory held for a running remesh, so remeshes evaluated at the same time (modifiers of several
 * objects, the viewport and a render) cannot each count the same free memory. Released when
 * destroyed.
 */
class VoxelRemeshReservation {
  int64_t bytes_ = 0;

 public:
  VoxelRemeshReservation() = default;
  explicit VoxelRemeshReservation(int64_t bytes) : bytes_(bytes) {}
  VoxelRemeshReservation(const VoxelRemeshReservation &) = delete;
  VoxelRemeshReservation &operator=(const VoxelRemeshReservation &) = delete;
  VoxelRemeshReservation(VoxelRemeshReservation &&other) noexcept : bytes_(other.bytes_)
  {
    other.bytes_ = 0;
  }
  ~VoxelRemeshReservation();
  bool granted() const
  {
    return bytes_ > 0;
  }
};

/**
 * Reserve \a estimate's peak if it still fits beside the other running remeshes (an atomic check,
 * so two remeshes estimated at once cannot both take the same memory). Not granted for a
 * #VoxelRemeshSize::TooLarge estimate.
 */
VoxelRemeshReservation voxel_remesh_reserve(const VoxelRemeshEstimate &estimate);

/**
 * The voxel size a new Remesh modifier starts with: Blender's 0.1, unless that would make fewer
 * vertices than Blender's default cube does at 0.1 (a small object, which would vanish) or more
 * than about a million (a large object, which would stall every evaluation):
 * then the size that makes that many, from the surface.
 */
float voxel_remesh_default_size(const Mesh &mesh);

/** Translated refusal for reports and modifier errors (also when no size fits). */
std::string voxel_remesh_too_large_message(const VoxelRemeshEstimate &estimate, float voxel_size);
/** Translated sentence an interactive remesh shows before a #VoxelRemeshSize::Large run. */
std::string voxel_remesh_large_message(const VoxelRemeshEstimate &estimate, float voxel_size);
/** Voxel size as the messages print it. */
std::string voxel_remesh_size_text(float voxel_size);

}  // namespace bke
}  // namespace blender
