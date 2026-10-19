/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 * SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once
#include "ED_sculpt_checkpoint.hh"

#ifdef WITH_GTESTS
#include "sculpt_main_undo_snapshot_access.hh"
#include "intern/bmesh_snapshot_io.hh"
namespace blender::ed::sculpt_paint::checkpoint {
bool companion_encode_views_for_test(Span<undo::SnapshotObjectView> objects,
                                      Span<undo::SnapshotGridView> grids,
                                      bool flush_needed,
                                      const CaptureID &capture_id,
                                      Span<ObjectBinding> object_bindings,
                                      Span<GridBinding> grid_bindings,
                                      const CodecLimits &limits,
                                      FunctionRef<bool(Span<std::byte>)> append,
                                      const char **r_error);
/* Calls the production Reader admission path with sparse input; no alternate parser. */
bool companion_decode_bits_for_test(bool face, bool scan, uint64_t file_bytes,
                                    uint64_t expected_groups, uint64_t expected_group_size,
                                    FunctionRef<bool(uint64_t, MutableSpan<std::byte>)> read);
bool companion_decode_geometry_with_test_fault(DecodePlan &plan,
                                               uint32_t owner,
                                               BMGeometryDecodeTestPhase phase,
                                               uint32_t element,
                                               const char **r_error);
}
#endif
