/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 * SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once

#include "BLI_bit_span.hh"
#include "BLI_function_ref.hh"
#include "BLI_math_vector_types.hh"
#include "BLI_span.hh"
#include "BKE_subdiv.hh"
#include "ED_sculpt_checkpoint.hh"

namespace blender {
struct BMGeometrySnapshot;
struct Main;
namespace ed::sculpt_paint::undo {
struct MainUndoState;
struct MainUndoGridState;
/** Checkpoint current-content capture omits historical BMLog snapshots. All ordinary current
 * owner/canonical/runtime admission and exact geometry/arrays remain unchanged. */
MainUndoState *main_undo_state_capture_checkpoint(Main &bmain, size_t limit, const char **r_error);
/** Complete initial Undo, including fresh live BMLog provenance, with cumulative admission. */
MainUndoState *main_undo_state_capture_initial_checkpoint(Main &bmain, size_t limit,
                                                         size_t &used, const char **r_error);
/** Shared finite preallocation charge used by checkpoint copies, never by ordinary Undo. */
inline bool checkpoint_snapshot_charge(size_t &used, const size_t limit, const size_t bytes)
{
  if (bytes > limit || used > limit - bytes) { return false; }
  used += bytes;
  return true;
}
/** Selected originals and owned evaluated CCG values at a suspended single-thread callback.
 * No current/canonical/authority assumption; this is observation, never a restore destination. */
MainUndoState *main_undo_state_observe_checkpoint(Main &bmain, Span<uint32_t> selected,
                                                 size_t limit, size_t &used,
                                                 const char **r_error);
/** Publish pre-admitted exact transient runtime at the outer native unwind boundary. */
bool main_undo_state_replay_checkpoint(Main &bmain, MainUndoState &state, const char **r_error);
/** Borrowed logical bit groups. Alignment/padding is deliberately not a wire field. */
struct SnapshotBitsView {
  bits::BoundedBitSpan storage;
  int64_t groups = 0;
  int64_t group_size = 0;
  int64_t stride = 0;
};
struct SnapshotVisibilityView {
  bool present = false;
  int modifier_uid = 0, verts_num = 0, grids_num = 0, grid_size = 0;
  Span<int> face_offsets, corner_verts;
  bits::BoundedBitSpan face_hidden;
  SnapshotBitsView grid_hidden;
};
struct SnapshotObjectView {
  uint32_t object_uid, mesh_uid;
  float3 pivot_pos, pivot_scale, last_normal;
  float4 pivot_rot;
  bool sticky_shading_color, needs_flush_to_id;
  int persistent_grids_num, persistent_grid_size;
  Span<float3> persistent_co, persistent_no;
  Span<float> persistent_disp;
  SnapshotVisibilityView visibility;
  const BMGeometrySnapshot *geometry = nullptr;
};
using SnapshotGraphProfile = checkpoint::GraphProfile;
using SnapshotModifierProfile = checkpoint::ModifierProfile;
struct SnapshotGridView {
  uint32_t object_uid, mesh_uid, scene_uid;
  const char *view_layer;
  bool authority;
  SnapshotGraphProfile graph;
  SnapshotModifierProfile modifier, evaluated_modifier;
  Span<int3> modifier_order, evaluated_modifier_order;
  bke::subdiv::Settings subdivision;
  int total_level, verts_num, level, grid_size, grid_area, grids_num;
  Span<int2> edges;
  Span<int> face_offsets, corner_verts, corner_edges, grid_to_face;
  Span<float3> positions, normals;
  Span<float> masks;
  SnapshotBitsView hidden;
  Span<int> pending_normal_faces;
  bool dirty_coords, dirty_hidden;
};
/** Synchronous, non-retaining visitors only; caller retains the opaque snapshot. No Main lookup. */
bool main_undo_snapshot_objects(const MainUndoState &state,
                                FunctionRef<bool(const SnapshotObjectView &)> visit);
bool main_undo_snapshot_grids(const MainUndoState &state,
                              FunctionRef<bool(const SnapshotGridView &)> visit);
bool main_undo_grid_snapshot_visit(const MainUndoGridState &state,
                                   FunctionRef<bool(const SnapshotGridView &)> visit);
bool main_undo_snapshot_flush_needed(const MainUndoState &state);
}  // namespace ed::sculpt_paint::undo
}  // namespace blender
