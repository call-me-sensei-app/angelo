/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 * SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <memory>
#include "BLI_array.hh"
#include "BLI_bit_group_vector.hh"
#include "BLI_function_ref.hh"
#include "BLI_math_vector_types.hh"
#include "BLI_span.hh"
#include "BLI_vector.hh"
#include "BKE_subdiv.hh"

namespace blender {
struct BMesh;
struct BMGeometryDecodePlan;
namespace ed::sculpt_paint::undo { struct MainUndoState; }
namespace ed::sculpt_paint::checkpoint {
using CaptureID = std::array<uint8_t, 16>;
/** Actual BASE writer tokens. Source UIDs are synchronous lookup keys, never file authority. */
struct ObjectBinding {
  uint32_t object_uid = 0, mesh_uid = 0, key_uid = 0;
  uint64_t object_token = 0, mesh_token = 0, key_token = 0;
  uint32_t mode = 0;
  int32_t shape_key_index = 0;
  uint32_t shape_key_flags = 0;
};
struct GridBinding {
  uint32_t object_uid = 0, mesh_uid = 0, scene_uid = 0;
  char view_layer[64] = {};
  uint64_t scene_token = 0, view_layer_token = 0;
};
/** Caller policy; no arbitrary scene-size ceiling. Native products are always checked.
 * Backing counts requested retained/input/decoded storage; scratch includes bounded staging and
 * temporary metadata. Allocator bookkeeping, OS overhead and fixed C++ control frames excluded. */
struct CodecLimits {
  uint64_t max_file_bytes;
  size_t max_backing_bytes, max_scratch_bytes;
};
struct EncodeInfo {
  CaptureID capture_id{};
  uint32_t format_version = 0, objects_num = 0, grids_num = 0, geometry_sections = 0;
  uint64_t file_bytes = 0;
  bool complete = false;
};
struct DecodeInfo {
  uint64_t file_bytes = 0;
  size_t retained_bytes = 0, decoded_geometry_bound = 0, scratch_bound = 0;
};
struct VisibilityState {
  bool present = false;
  int modifier_uid = 0, verts_num = 0, grids_num = 0, grid_size = 0;
  Array<int> face_offsets, corner_verts;
  BitVector<> face_hidden;
  BitGroupVector<> grid_hidden;
};
struct ObjectState {
  ObjectBinding binding;
  float3 pivot_pos, pivot_scale, last_normal;
  float4 pivot_rot;
  bool sticky_shading_color = false, needs_flush_to_id = false;
  int persistent_grids_num = -1, persistent_grid_size = -1;
  Array<float3> persistent_co, persistent_no;
  Array<float> persistent_disp;
  VisibilityState visibility;
  BMGeometryDecodePlan *geometry_plan = nullptr;
  BMesh *geometry = nullptr;
  ObjectState() = default;
  ObjectState(ObjectState &&other) noexcept;
  ObjectState &operator=(ObjectState &&other) noexcept;
  ~ObjectState();
  ObjectState(const ObjectState &) = delete;
};
/** Explicit profile values, serialized by field; never native struct bytes. */
struct GraphProfile {
  bool visibility_optimization, editors_update;
  int sync_writeback, debug_flags, scene_mode, simplify_subsurf;
  float frame, ctime;
  int evaluated_scene_mode, evaluated_simplify_subsurf;
  float evaluated_frame, evaluated_ctime;
};
struct ModifierProfile {
  int uid, mode, level, sculpt_level, render_level, total_level, flags;
  int quality, uv_smooth, boundary_smooth;
};
struct GridState {
  uint32_t object_index = 0;
  uint64_t scene_token = 0, view_layer_token = 0;
  bool authority = false;
  GraphProfile graph;
  ModifierProfile modifier, evaluated_modifier;
  bke::subdiv::Settings subdivision;
  int total_level = 0, verts_num = 0, level = 0, grid_size = 0, grid_area = 0, grids_num = 0;
  Array<int3> modifier_order, evaluated_modifier_order;
  Array<int2> edges;
  Array<int> face_offsets, corner_verts, corner_edges, grid_to_face;
  Array<float3> positions, normals;
  Array<float> masks;
  BitGroupVector<> hidden;
  Array<int> pending_normal_faces;
  bool dirty_coords = false, dirty_hidden = false;
};
struct DecodePlan {
  CaptureID capture_id;
  bool flush_needed = false;
  Array<ObjectState, 0> objects;
  Array<GridState, 0> grids;
  DecodeInfo info;
};
/** Single streaming encode; footer directory frames sections without second encode/full buffering. */
bool companion_encode(const undo::MainUndoState *snapshot,
                      const CaptureID &capture_id,
                      Span<ObjectBinding> objects,
                      Span<GridBinding> grids,
                      const CodecLimits &limits,
                      FunctionRef<bool(Span<std::byte>)> append,
                      const char **r_error,
                      EncodeInfo *r_info = nullptr);
/** Random access anchored input; no full companion copy. All framing and aggregate budgets admitted
 * before any BMesh construction. Each codec section owns its exact unaliased Array. */
std::unique_ptr<DecodePlan> companion_decode_prepare(
    uint64_t file_bytes,
    const CaptureID &expected_capture,
    const CodecLimits &limits,
    FunctionRef<bool(uint64_t, MutableSpan<std::byte>)> read,
    const char **r_error);
/** Bulk detached geometry construction only after aggregate admission; failure releases all
 * earlier constructed BMeshes. No Main/Key/BMLog/PBVH mutation or runtime publication. */
bool companion_decode_geometry(DecodePlan &plan, const char **r_error);
}  // namespace ed::sculpt_paint::checkpoint
}  // namespace blender
