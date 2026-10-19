/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

#include "BLI_array.hh"
#include "BLI_function_ref.hh"
#include "BLI_span.hh"

namespace blender {

struct BMesh;
struct BMGeometrySnapshot;
struct BMGeometryDecodePlan;

/** Caller policy, separate from the codec's mandatory native overflow checks. No scene-size cap.
 */
struct BMGeometryCodecLimits {
  uint64_t max_section_bytes;
  /**
   * Prepare: retained input/plan plus the decoded geometry backing bound. Decode keeps that
   * reservation. Encode: the existing immutable snapshot is caller-owned and not charged again.
   * Includes requested MEM storage/pool slack; excludes allocator bookkeeping and OS overhead.
   */
  size_t max_backing_bytes;
  /**
   * Peak temporary heap backing in each call plus the encode output buffer, excluding retained
   * input/plan/geometry and fixed C++ control stack frames. No other sink storage is charged here.
   */
  size_t max_scratch_bytes;
};

struct BMGeometryDecodeInfo {
  uint64_t section_bytes;
  uint32_t verts_num, edges_num, faces_num, loops_num;
  bool all_faces_triangular;
  size_t owned_input_and_plan_bytes;
  size_t decoded_backing_bytes_bound;
  size_t decode_scratch_bytes_bound;
};

/**
 * Stream the existing immutable snapshot. The synchronous sink must not retain borrowed chunks,
 * mutate the snapshot, or invoke application callbacks. False may leave partial output;
 * publication is the caller's responsibility. Float bits and meaningful fields, not native
 * padding, are saved.
 */
bool BM_geometry_snapshot_encode(const BMGeometrySnapshot *snapshot,
                                 const BMGeometryCodecLimits &limits,
                                 FunctionRef<bool(Span<std::byte>)> append,
                                 const char **r_error = nullptr);

/**
 * Consume the owned byte array on success and failure; retain no writable aliases. Preflight all
 * extents, native products and incidences before constructing geometry. A file mapping is not an
 * immutable owner. Returns null plus a static diagnostic on invalid/unsupported input or policy
 * refusal; this is not a guarantee that core allocator exhaustion is recoverable.
 */
BMGeometryDecodePlan *BM_geometry_snapshot_decode_prepare(Array<std::byte, 0> &&owned_section,
                                                          const BMGeometryCodecLimits &limits,
                                                          const char **r_error = nullptr);

/** Parent sums all owner plans/bounds and its other runtime allocations before bulk construction.
 */
const BMGeometryDecodeInfo &BM_geometry_snapshot_decode_info(const BMGeometryDecodePlan &plan);

/**
 * Make one detached, caller-owned BMesh, freed with BM_mesh_free. The plan stays immutable and can
 * be decoded again. No Main, Mesh, Key, PBVH or BMLog is touched. Output byte count is zero on
 * failure; on success it counts actual MEM/pool backing storage, not process memory.
 */
BMesh *BM_geometry_snapshot_decode(const BMGeometryDecodePlan &plan,
                                   size_t *r_owned_backing_bytes = nullptr,
                                   const char **r_error = nullptr);

void BM_geometry_snapshot_decode_plan_free(BMGeometryDecodePlan *plan);

#ifdef WITH_GTESTS
enum class BMGeometryDecodeTestPhase { Schema, Vertex, Edge, Face, Attribute, History };
/** Local construction fault only; never changes a plan or installs persistent injection state. */
BMesh *BM_geometry_snapshot_decode_with_test_fault(const BMGeometryDecodePlan &plan,
                                                   BMGeometryDecodeTestPhase phase,
                                                   uint32_t index,
                                                   const char **r_error = nullptr);
#endif

namespace bmesh::snapshot_io {
/** Pure native count admission shared by the reader and arithmetic-boundary tests. */
bool counts_valid(const std::array<uint32_t, 4> &counts);
/** Internal bridge for the opaque snapshot owner; not a live BMesh capture API. */
bool encode_mesh(const BMesh &mesh,
                 const BMGeometryCodecLimits &limits,
                 FunctionRef<bool(Span<std::byte>)> append,
                 const char **r_error);
}  // namespace bmesh::snapshot_io

}  // namespace blender
