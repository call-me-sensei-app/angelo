/* SPDX-FileCopyrightText: 2026 Angelo Contributors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup bke
 */

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdint>
#include <functional>

#include "MEM_guardedalloc.h"

#include "BLI_array.hh"
#include "BLI_math_vector.hh"
#include "BLI_math_vector_types.hh"
#include "BLI_string.h"
#include "BLI_system.h"
#include "BLI_task.hh"

#include "BLT_translation.hh"

#include "BKE_mesh.hh"
#include "BKE_mesh_remesh_voxel_estimate.hh" /* own include */

namespace blender::bke {

/* Measured on macOS arm64 (Blender 5.2.2's kernel): the peak memory a voxel remesh adds (the larger
 * of resident memory and physical footprint), 127 remeshes over nine shapes (closed, open, thin,
 * disconnected, 6 to 259,200 source faces) and 0.25 to 60 million vertices, through the operator
 * and the modifier. Each figure is the largest per-vertex cost observed plus a margin: no run
 * reached 0.79 of the model. doc/angelo/voxel_remesh_guard.rst has the table. */

/** Thread pools, OpenVDB's tree and the result's first allocations. */
static constexpr double FIXED_BYTES = 256.0 * 1024.0 * 1024.0;
/** Level set, marching-cubes buffers and the new Mesh, per output vertex. */
static constexpr double BYTES_PER_VERTEX = 520.0;
/** Preserve Volume / Attributes: the BVH and the sampled attributes, per output vertex. */
static constexpr double REPROJECT_BYTES_PER_VERTEX = 160.0;
/** Fix Poles: the BMesh round trip, per output vertex. */
static constexpr double FIX_POLES_BYTES_PER_VERTEX = 1250.0;
/** OpenVDB's copy of the source triangles and its partition, per source triangle. */
static constexpr double BYTES_PER_SOURCE_TRIANGLE = 200.0;

/** Above this many vertices (and above the current mesh's count) an interactive remesh asks. About
 * ten seconds on a fast computer; the same threshold upstream proposes (PR #165518). */
static constexpr double LARGE_VERTICES = 10'000'000.0;
/** Share of the free memory above which an interactive remesh asks. */
static constexpr double LARGE_FREE_SHARE = 0.5;
/** Kept free for the system and for Blender itself while a remesh runs: the larger of these. */
static constexpr double RESERVE_PHYSICAL_SHARE = 0.10;
static constexpr double RESERVE_MIN_BYTES = 1024.0 * 1024.0 * 1024.0;
/** A mesh indexes corners with 32-bit integers and the output has about four corners per vertex. */
static constexpr double MAX_VERTICES = double(INT32_MAX) / 4.0 * 0.75;
/** Used when the system reports no memory figures. */
static constexpr double ASSUMED_FREE_BYTES = 4.0 * 1024.0 * 1024.0 * 1024.0;
/** Fewer vertices than this is not a useful remesh: when even this does not fit, nothing does. */
static constexpr double MIN_USEFUL_VERTICES = 1000.0;

/** A new Remesh modifier's default (#voxel_remesh_default_size): Blender's size, and the vertices
 * Blender's default 2 m cube gets at it (24 m^2 / 0.1^2). */
static constexpr double BLENDER_DEFAULT_VOXEL_SIZE = 0.1;
static constexpr double DEFAULT_MIN_VERTICES = 2400.0;
/** About half a second per evaluation of a new modifier (measured: 0.5-0.7 s at one million
 * vertices on a closed surface, more with dense sources). A modifier re-evaluates on every edit. */
static constexpr double DEFAULT_MAX_VERTICES = 1'000'000.0;

/** Bytes reserved by remeshes running now (#voxel_remesh_reserve). */
static std::atomic<int64_t> reserved_bytes{0};

/**
 * True when an edge borders one face or more than two: an open or non-manifold surface. OpenVDB's
 * level set then wraps both sides of it, and the remesh makes up to twice the vertices of a closed
 * surface of the same area (measured: an open sheet 2.0x, Suzanne 1.65x, an open terrain 1.5x).
 * Loose edges (no face) do not count: the level set is built from faces only.
 */
static bool surface_is_open(const Mesh &mesh)
{
  Array<uint8_t> faces_per_edge(mesh.edges_num, 0);
  for (const int edge : mesh.corner_edges()) {
    faces_per_edge[edge] = uint8_t(std::min(faces_per_edge[edge] + 1, 3));
  }
  return std::any_of(faces_per_edge.begin(), faces_per_edge.end(), [](const uint8_t count) {
    return count == 1 || count > 2;
  });
}

/**
 * The areas of the triangles the remesher receives (#Mesh::corner_tris, cached and shared with
 * it), projected on the three axis planes, each taken as positive before summing. A face's own
 * vector area would let the parts of a non-planar, crossed or folded face cancel: a saddle quad
 * reads as its flat shadow and a crossed quad as zero, while the level set follows every triangle.
 */
static double projected_area(const Mesh &mesh)
{
  const Span<float3> positions = mesh.vert_positions();
  const Span<int> corner_verts = mesh.corner_verts();
  const Span<int3> corner_tris = mesh.corner_tris();
  return threading::parallel_reduce(
      corner_tris.index_range(),
      4096,
      0.0,
      [&](const IndexRange range, double sum) {
        for (const int tri : range) {
          /* Relative to the first corner, for precision far from the origin. */
          const double3 origin(positions[corner_verts[corner_tris[tri][0]]]);
          const double3 area = math::cross(
              double3(positions[corner_verts[corner_tris[tri][1]]]) - origin,
              double3(positions[corner_verts[corner_tris[tri][2]]]) - origin);
          sum += 0.5 * (std::abs(area.x) + std::abs(area.y) + std::abs(area.z));
        }
        return sum;
      },
      std::plus<>());
}

/** The area the remesh's output follows: the projected area, both sides of an open surface. */
static double remeshed_area(const Mesh &mesh)
{
  return projected_area(mesh) * (surface_is_open(mesh) ? 2.0 : 1.0);
}

static double per_vertex_bytes(const VoxelRemeshExtras &extras)
{
  return BYTES_PER_VERTEX + (extras.reprojects ? REPROJECT_BYTES_PER_VERTEX : 0.0) +
         (extras.fixes_poles ? FIX_POLES_BYTES_PER_VERTEX : 0.0);
}

/** Round up to two significant digits, so a size reads well and still fits. */
static float round_up_two_digits(const double value)
{
  if (!(value > 0.0) || !std::isfinite(value)) {
    return 0.0f;
  }
  const double scale = std::pow(10.0, std::floor(std::log10(value)) - 1.0);
  return float(std::ceil(value / scale - 1e-9) * scale);
}

/** Free memory less the system's reserve, before running remeshes' reservations. */
static double unreserved_bytes_now()
{
  const double physical = double(BLI_system_memory_physical_bytes());
  const double available = double(BLI_system_memory_available_bytes());
  if (!(available > 0.0)) {
    return ASSUMED_FREE_BYTES;
  }
  const double reserve = std::max(RESERVE_MIN_BYTES, physical * RESERVE_PHYSICAL_SHARE);
  return std::max(0.0, available - reserve);
}

VoxelRemeshEstimate voxel_remesh_estimate(const Mesh &mesh,
                                          const float voxel_size,
                                          const VoxelRemeshExtras &extras)
{
  VoxelRemeshEstimate estimate;
  estimate.unreserved_bytes = unreserved_bytes_now();
  estimate.free_bytes = std::max(0.0, estimate.unreserved_bytes - double(reserved_bytes.load()));
  if (mesh.faces_num == 0) {
    return estimate;
  }
  estimate.projected_area = remeshed_area(mesh);
  const double source_bytes = double(int64_t(mesh.corners_num) - 2 * int64_t(mesh.faces_num)) *
                              BYTES_PER_SOURCE_TRIANGLE;
  const double per_vertex = per_vertex_bytes(extras);
  const double size = double(voxel_size);
  const bool valid = std::isfinite(estimate.projected_area) && size > 0.0 && std::isfinite(size);
  /* A non-finite position or size cannot be remeshed safely: refuse rather than guess. */
  estimate.vertices = valid ? estimate.projected_area / (size * size) : HUGE_VAL;
  estimate.peak_bytes = FIXED_BYTES + source_bytes + estimate.vertices * per_vertex;

  if (!(estimate.vertices <= MAX_VERTICES) || !(estimate.peak_bytes <= estimate.free_bytes)) {
    estimate.size = VoxelRemeshSize::TooLarge;
  }
  else if (estimate.vertices > std::max(LARGE_VERTICES, double(mesh.verts_num)) ||
           estimate.peak_bytes > estimate.free_bytes * LARGE_FREE_SHARE)
  {
    estimate.size = VoxelRemeshSize::Large;
  }

  /* The vertex count that runs without asking: below both "large" bounds. */
  const double memory_fit = (estimate.free_bytes * LARGE_FREE_SHARE - FIXED_BYTES -
                             source_bytes) /
                            per_vertex;
  const double fitting = std::min(LARGE_VERTICES, memory_fit);
  if (std::isfinite(estimate.projected_area) && estimate.projected_area > 0.0 &&
      fitting >= MIN_USEFUL_VERTICES)
  {
    estimate.suggested_voxel_size = round_up_two_digits(
        std::sqrt(estimate.projected_area / fitting));
  }
  return estimate;
}

VoxelRemeshReservation::~VoxelRemeshReservation()
{
  if (bytes_ > 0) {
    reserved_bytes.fetch_sub(bytes_);
  }
}

VoxelRemeshReservation voxel_remesh_reserve(const VoxelRemeshEstimate &estimate)
{
  if (estimate.size == VoxelRemeshSize::TooLarge) {
    return VoxelRemeshReservation();
  }
  /* Everything reserved now, this remesh included, must fit in the free memory the estimate saw. */
  const int64_t bytes = std::max<int64_t>(1, int64_t(estimate.peak_bytes));
  const int64_t ceiling = int64_t(estimate.unreserved_bytes);
  int64_t current = reserved_bytes.load();
  while (true) {
    if (current + bytes > ceiling) {
      return VoxelRemeshReservation();
    }
    if (reserved_bytes.compare_exchange_weak(current, current + bytes)) {
      return VoxelRemeshReservation(bytes);
    }
  }
}

float voxel_remesh_default_size(const Mesh &mesh)
{
  if (mesh.faces_num == 0) {
    return float(BLENDER_DEFAULT_VOXEL_SIZE);
  }
  const double area = remeshed_area(mesh);
  if (!(area > 0.0) || !std::isfinite(area)) {
    return float(BLENDER_DEFAULT_VOXEL_SIZE);
  }
  const double blender_vertices = area / (BLENDER_DEFAULT_VOXEL_SIZE * BLENDER_DEFAULT_VOXEL_SIZE);
  if (blender_vertices >= DEFAULT_MIN_VERTICES && blender_vertices <= DEFAULT_MAX_VERTICES) {
    return float(BLENDER_DEFAULT_VOXEL_SIZE);
  }
  const double vertices = std::clamp(blender_vertices, DEFAULT_MIN_VERTICES, DEFAULT_MAX_VERTICES);
  return round_up_two_digits(std::sqrt(area / vertices));
}

std::string voxel_remesh_size_text(const float voxel_size)
{
  char text[64];
  SNPRINTF(text, "%.3g", double(voxel_size));
  return text;
}

/** Clamped and finite, so formatting never converts NaN or an out-of-range double. */
static double printable(const double value)
{
  return std::isfinite(value) ? std::clamp(value, 0.0, 1e18) : 1e18;
}

static std::string vertices_text(const double vertices)
{
  char text[BLI_STR_FORMAT_UINT64_GROUPED_SIZE];
  BLI_str_format_uint64_grouped(text, uint64_t(printable(vertices)));
  return text;
}

static std::string bytes_text(const double bytes)
{
  char text[BLI_STR_FORMAT_INT64_BYTE_UNIT_SIZE];
  BLI_str_format_byte_unit(text, int64_t(printable(bytes)), false);
  return text;
}

/** Format a translated template whose length a translation decides (no fixed buffer). */
static std::string format_message(const char *format,
                                  const std::string &a,
                                  const std::string &b,
                                  const std::string &c,
                                  const std::string &d,
                                  const std::string &e = "")
{
  char *text = BLI_sprintfN(format, a.c_str(), b.c_str(), c.c_str(), d.c_str(), e.c_str());
  std::string result = text;
  MEM_delete(text);
  return result;
}

std::string voxel_remesh_too_large_message(const VoxelRemeshEstimate &estimate,
                                           const float voxel_size)
{
  if (estimate.suggested_voxel_size <= 0.0f) {
    return format_message(RPT_("Voxel size %s would make about %s vertices and need about %s of "
                               "memory; only %s is free, and this mesh is too large to voxel "
                               "remesh at any size with that much"),
                          voxel_remesh_size_text(voxel_size),
                          vertices_text(estimate.vertices),
                          bytes_text(estimate.peak_bytes),
                          bytes_text(estimate.free_bytes));
  }
  return format_message(RPT_("Voxel size %s would make about %s vertices and need about %s of "
                             "memory; only %s is free. Use a voxel size of %s or larger"),
                        voxel_remesh_size_text(voxel_size),
                        vertices_text(estimate.vertices),
                        bytes_text(estimate.peak_bytes),
                        bytes_text(estimate.free_bytes),
                        voxel_remesh_size_text(estimate.suggested_voxel_size));
}

std::string voxel_remesh_large_message(const VoxelRemeshEstimate &estimate,
                                       const float voxel_size)
{
  if (estimate.suggested_voxel_size <= 0.0f) {
    return format_message(RPT_("Voxel size %s will make about %s vertices and need about %s of "
                               "memory (%s is free). This can take a while"),
                          voxel_remesh_size_text(voxel_size),
                          vertices_text(estimate.vertices),
                          bytes_text(estimate.peak_bytes),
                          bytes_text(estimate.free_bytes));
  }
  return format_message(RPT_("Voxel size %s will make about %s vertices and need about %s of "
                             "memory (%s is free). This can take a while; a voxel size of %s "
                             "would not"),
                        voxel_remesh_size_text(voxel_size),
                        vertices_text(estimate.vertices),
                        bytes_text(estimate.peak_bytes),
                        bytes_text(estimate.free_bytes),
                        voxel_remesh_size_text(estimate.suggested_voxel_size));
}

}  // namespace blender::bke
