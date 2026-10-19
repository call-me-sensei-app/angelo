/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

#include <cstddef>

namespace blender {

struct BMesh;
struct BMGeometrySnapshot;

/**
 * Capture owned geometry without writing source indices, lookup tables, tags, or CustomData.
 * The snapshot contains no BMLog, Python wrappers, or borrowed geometry storage. Every recognized
 * value layer is retained, including NOCOPY/temporary layers; Python element-pointer layers are
 * excluded. Derived caches and operator scratch state are not geometry.
 *
 * Source access must be exclusive and idle. Null plus a static diagnostic denotes unsupported or
 * inconsistent input, not a promise of graceful recovery from allocator exhaustion.
 */
BMGeometrySnapshot *BM_geometry_snapshot_create(const BMesh *bm, const char **r_error = nullptr);
/** Allocation-free conservative clone backing/scratch admission; source must be exclusive. */
bool BM_geometry_snapshot_preallocation_bound(const BMesh *bm, size_t &r_bytes,
                                               const char **r_error = nullptr);
/** Private finite capture. Cumulative charge is applied BEFORE any owned clone allocation. */
BMGeometrySnapshot *BM_geometry_snapshot_create_bounded(const BMesh *bm, size_t limit,
                                                       size_t &used,
                                                       const char **r_error = nullptr);
/** Return a fresh owned BMesh; the captured geometry is never used as the live runtime. */
BMesh *BM_geometry_snapshot_restore(const BMGeometrySnapshot *snapshot,
                                    const char **r_error = nullptr);
/** Private finite restore admission before cloning the immutable snapshot. */
BMesh *BM_geometry_snapshot_restore_bounded(const BMGeometrySnapshot *snapshot, size_t limit,
                                            size_t &used, const char **r_error = nullptr);
/**
 * Conservative, bit-exact geometry proof. False also means unsupported/unproven input. Bookkeeping
 * indices/tables, derived caches, Python wrapper pointers, and allocation addresses are excluded.
 * This does not establish document or BMLog identity, which callers must validate separately.
 */
bool BM_geometry_snapshot_equals(const BMGeometrySnapshot *snapshot, const BMesh *bm);
bool BM_geometry_snapshot_equals(const BMGeometrySnapshot *a, const BMGeometrySnapshot *b);
/** Cached owned payload bytes, including reserved pool capacity and deep attribute allocations. */
size_t BM_geometry_snapshot_memory_size(const BMGeometrySnapshot *snapshot);
void BM_geometry_snapshot_free(BMGeometrySnapshot *snapshot);

}  // namespace blender
