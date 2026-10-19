/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "sculpt_checkpoint_io.hh"
#include "sculpt_main_undo_snapshot_access.hh"

#include <algorithm>
#include <cstring>
#include <limits>
#include <utility>
#include <type_traits>
#include <climits>

#include "BLI_map.hh"
#include "BLI_set.hh"
#include "BLI_string.h"
#include "DNA_object_types.h"
#include "bmesh.hh"
#include "intern/bmesh_snapshot_io.hh"

namespace blender::ed::sculpt_paint::checkpoint {
namespace {
static_assert(sizeof(float) == 4 && sizeof(int) == 4);
constexpr uint32_t version = 1;
constexpr uint64_t header_size = 48, footer_size = 32, entry_size = 24;
constexpr size_t buffer_size = 4096;
constexpr char header_magic[8] = {'A','S','C','P','T','0','0','1'};
constexpr char footer_magic[8] = {'A','S','C','E','N','D','0','1'};
enum class Section : uint32_t { Object = 1, Grid = 2, Geometry = 3 };
struct Entry { Section type; uint32_t owner; uint64_t offset, length; };

bool fail(const char **error, const char *message)
{
  if (error) { *error = message; }
  return false;
}
bool add(uint64_t &value, const uint64_t amount)
{
  if (amount > std::numeric_limits<uint64_t>::max() - value) { return false; }
  value += amount;
  return true;
}
bool product(const uint64_t a, const uint64_t b, uint64_t &result)
{
  if (a && b > std::numeric_limits<uint64_t>::max() / a) { return false; }
  result = a * b;
  return true;
}
bool charge(size_t &value, const uint64_t bytes, const size_t limit)
{
  if (bytes > limit || value > limit - size_t(bytes)) { return false; }
  value += size_t(bytes);
  return true;
}
bool capture_valid(const CaptureID &id)
{
  return std::any_of(id.begin(), id.end(), [](uint8_t byte) { return byte != 0; });
}

/** Fixed bounded output staging, not a full companion buffer. */
struct Writer {
  FunctionRef<bool(Span<std::byte>)> append;
  uint64_t limit, position = 0;
  std::array<std::byte, buffer_size> buffer;
  size_t used = 0;
  bool valid = true;
  bool flush()
  {
    if (!valid) { return false; }
    if (used && !append(Span(buffer.data(), used))) { valid = false; }
    used = 0;
    return valid;
  }
  void bytes(const void *data, const size_t length)
  {
    if (!valid || length > limit || position > limit - length) { valid = false; return; }
    const std::byte *source = static_cast<const std::byte *>(data);
    for (size_t done = 0; done < length;) {
      const size_t size = std::min(length - done, buffer.size() - used);
      std::memcpy(buffer.data() + used, source + done, size);
      used += size;
      done += size;
      if (used == buffer.size() && !flush()) { return; }
    }
    position += length;
  }
  void u32(const uint32_t value)
  {
    std::array<uint8_t, 4> bytes;
    for (int i = 0; i < 4; i++) { bytes[i] = uint8_t(value >> (8 * i)); }
    this->bytes(bytes.data(), bytes.size());
  }
  void u64(const uint64_t value)
  {
    u32(uint32_t(value)); u32(uint32_t(value >> 32));
  }
  void scalar(const int value) { u32(uint32_t(value)); }
  void scalar(const float value)
  {
    uint32_t bits;
    std::memcpy(&bits, &value, sizeof(bits));
    u32(bits);
  }
  template<typename T> void vector(const T &value, const int components)
  {
    for (int i = 0; i < components; i++) { scalar(value[i]); }
  }
  template<typename T> void array(const Span<T> values, const int components = 1)
  {
    u64(values.size());
    for (const T &value : values) {
      if constexpr (std::is_same_v<T, int> || std::is_same_v<T, float>) { scalar(value); }
      else { vector(value, components); }
    }
  }
  void bits(const undo::SnapshotBitsView &view)
  {
    u64(view.groups); u64(view.group_size);
    uint64_t count, extent;
    if (view.groups < 0 || view.group_size < 0 || view.stride < view.group_size ||
        !product(view.groups, view.group_size, count) || count > INT64_MAX ||
        !product(view.groups ? view.groups - 1 : 0, view.stride, extent) ||
        !add(extent, view.groups ? view.group_size : 0) || extent > uint64_t(view.storage.size()))
    { valid = false; return; }
    uint8_t byte = 0;
    for (uint64_t bit = 0; bit < count; bit++) {
      const int64_t index = int64_t(bit / view.group_size) * view.stride + bit % view.group_size;
      if (index < 0 || index >= view.storage.size()) { valid = false; return; }
      byte |= uint8_t(bool(view.storage[index])) << (bit % 8);
      if (bit % 8 == 7 || bit + 1 == count) { bytes(&byte, 1); byte = 0; }
    }
  }
};

/** Bounded random reader with a fixed buffer; scan pass skips arrays without retaining bytes. */
struct Reader {
  FunctionRef<bool(uint64_t, MutableSpan<std::byte>)> read;
  uint64_t begin, end, position;
  bool scan, valid = true;
  size_t &retained;
  size_t backing_limit;
  std::array<std::byte, buffer_size> buffer;
  uint64_t buffered_at = 0;
  size_t buffered_size = 0;
  uint64_t last_array_count = 0;
  Reader(FunctionRef<bool(uint64_t, MutableSpan<std::byte>)> read,
         const uint64_t begin, const uint64_t length, const bool scan,
         size_t &retained, const size_t backing_limit)
      : read(read), begin(begin), end(begin + length), position(begin), scan(scan),
        retained(retained), backing_limit(backing_limit) {}
  void bytes(void *target, const size_t length)
  {
    if (!valid || position > end || length > end - position) { valid = false; return; }
    std::byte *out = static_cast<std::byte *>(target);
    for (size_t done = 0; done < length;) {
      if (position < buffered_at || position >= buffered_at + buffered_size) {
        buffered_at = position;
        buffered_size = size_t(std::min<uint64_t>(buffer.size(), end - position));
        if (!read(position, MutableSpan(buffer.data(), buffered_size))) { valid = false; return; }
      }
      const size_t index = size_t(position - buffered_at);
      const size_t size = std::min(length - done, buffered_size - index);
      std::memcpy(out + done, buffer.data() + index, size);
      done += size;
      position += size;
    }
  }
  uint32_t u32()
  {
    std::array<uint8_t, 4> data = {};
    bytes(data.data(), data.size());
    uint32_t result = 0;
    for (int i = 0; i < 4; i++) { result |= uint32_t(data[i]) << (8 * i); }
    return result;
  }
  uint64_t u64() { const uint64_t low = u32(); return low | uint64_t(u32()) << 32; }
  int integer()
  {
    const uint32_t bits = u32(); int32_t value;
    std::memcpy(&value, &bits, sizeof(value)); return value;
  }
  bool boolean()
  {
    const uint32_t value = u32(); if (value > 1) { valid = false; } return value == 1;
  }
  float floating()
  {
    const uint32_t bits = u32(); float value;
    std::memcpy(&value, &bits, sizeof(value)); return value;
  }
  template<typename T> void vector(T &value, const int components)
  {
    for (int i = 0; i < components; i++) {
      if constexpr (std::is_same_v<T, int2> || std::is_same_v<T, int3>) { value[i] = integer(); }
      else { value[i] = floating(); }
    }
  }
  template<typename T, int64_t Capacity>
  void array(Array<T, Capacity> &values,
             const int components = 1,
             const uint64_t expected_count = UINT64_MAX)
  {
    const uint64_t count = u64(); uint64_t bytes, allocation;
    last_array_count = count;
    if (!valid || count > INT64_MAX ||
        (expected_count != UINT64_MAX && count != expected_count) ||
        !product(count, uint64_t(components) * 4, bytes) ||
        bytes > end - position || !product(count, sizeof(T), allocation) ||
        !charge(retained, allocation, backing_limit))
    { valid = false; return; }
    if (scan) { position += bytes; return; }
    values = Array<T, Capacity>(int64_t(count));
    for (T &value : values) {
      if constexpr (std::is_same_v<T, int>) { value = integer(); }
      else if constexpr (std::is_same_v<T, float>) { value = floating(); }
      else { vector(value, components); }
    }
  }
  template<int64_t Capacity> void bits(BitGroupVector<Capacity> &value,
                                     const uint64_t expected_groups,
                                     const uint64_t expected_group_size)
  {
    const uint64_t groups = u64(), group_size = u64(); uint64_t count, storage;
    if (!valid || groups > INT64_MAX || group_size > INT64_MAX ||
        (groups != 0 && (group_size == 0 || groups != expected_groups ||
                         group_size != expected_group_size)) ||
        (groups == 0 && group_size != 0 && group_size != expected_group_size) ||
        !product(groups, group_size, count) ||
        count > UINT64_MAX - 7) { valid = false; return; }
    uint64_t aligned = group_size ? 1 : 0;
    if (group_size < 64) { while (aligned < group_size) { aligned *= 2; } }
    else { aligned = group_size > uint64_t(INT64_MAX - 63) ? 0 : ((group_size + 63) & ~uint64_t(63)); }
    if ((group_size && !aligned) || !product(groups, aligned, storage) || storage > INT_MAX ||
        !charge(retained, ((storage + 63) / 64) * 8, backing_limit) || (count + 7) / 8 > end - position)
    { valid = false; return; }
    const uint64_t bytes = (count + 7) / 8;
    if (scan) {
      if (bytes && count % 8) {
        uint8_t last;
        if (!read(position + bytes - 1, MutableSpan(reinterpret_cast<std::byte *>(&last), 1)) ||
            (last >> (count % 8)) != 0) { valid = false; return; }
      }
      position += bytes; return;
    }
    /* BitVector::resize forwards the bit capacity to reserve(int). Admit that native
     * representation before construction; this is not a product geometry limit. */
    value = BitGroupVector<Capacity>(int64_t(groups), int64_t(group_size));
    uint8_t byte = 0;
    for (uint64_t bit = 0; bit < count; bit++) {
      if (bit % 8 == 0) { this->bytes(&byte, 1); }
      value[int64_t(bit / group_size)][int64_t(bit % group_size)].set((byte >> (bit % 8)) & 1);
    }
    if (count % 8 && (byte >> (count % 8)) != 0) { valid = false; }
  }
  template<int64_t Capacity> void face_bits(BitVector<Capacity> &value, const uint64_t expected_size)
  {
    const uint64_t groups = u64(), size = u64();
    if (!valid || groups > 1 || (groups == 0 && size != 0) ||
        (groups == 1 && size == 0) || size != expected_size || size > INT_MAX ||
        !charge(retained, ((size + 63) / 64) * 8, backing_limit) ||
        (size + 7) / 8 > end - position) { valid = false; return; }
    if (scan) {
      const uint64_t length = (size + 7) / 8;
      if (length && size % 8) {
        uint8_t last;
        if (!read(position + length - 1, MutableSpan(reinterpret_cast<std::byte *>(&last), 1)) ||
            (last >> (size % 8)) != 0) { valid = false; return; }
      }
      position += length;
      return;
    }
    value = BitVector<Capacity>(int64_t(size));
    uint8_t byte = 0;
    for (uint64_t i = 0; i < size; i++) {
      if (i % 8 == 0) { bytes(&byte, 1); }
      value[int64_t(i)].set((byte >> (i % 8)) & 1);
    }
    if (size % 8 && (byte >> (size % 8)) != 0) { valid = false; }
  }
  bool complete() const { return valid && position == end; }
};

void write_modifier(Writer &w, const ModifierProfile &p)
{
  for (const int value : {p.uid,p.mode,p.level,p.sculpt_level,p.render_level,p.total_level,
                         p.flags,p.quality,p.uv_smooth,p.boundary_smooth}) { w.scalar(value); }
}
void read_modifier(Reader &r, ModifierProfile &p)
{
  p.uid=r.integer(); p.mode=r.integer(); p.level=r.integer(); p.sculpt_level=r.integer();
  p.render_level=r.integer(); p.total_level=r.integer(); p.flags=r.integer();
  p.quality=r.integer(); p.uv_smooth=r.integer(); p.boundary_smooth=r.integer();
}
void write_graph(Writer &w, const GraphProfile &p)
{
  w.u32(p.visibility_optimization); w.u32(p.editors_update);
  w.scalar(p.sync_writeback); w.scalar(p.debug_flags); w.scalar(p.scene_mode);
  w.scalar(p.simplify_subsurf); w.scalar(p.frame); w.scalar(p.ctime);
  w.scalar(p.evaluated_scene_mode); w.scalar(p.evaluated_simplify_subsurf);
  w.scalar(p.evaluated_frame); w.scalar(p.evaluated_ctime);
}
void read_graph(Reader &r, GraphProfile &p)
{
  p.visibility_optimization=r.boolean(); p.editors_update=r.boolean();
  p.sync_writeback=r.integer(); p.debug_flags=r.integer(); p.scene_mode=r.integer();
  p.simplify_subsurf=r.integer(); p.frame=r.floating(); p.ctime=r.floating();
  p.evaluated_scene_mode=r.integer(); p.evaluated_simplify_subsurf=r.integer();
  p.evaluated_frame=r.floating(); p.evaluated_ctime=r.floating();
}
void write_subdivision(Writer &w, const bke::subdiv::Settings &s)
{
  w.u32(s.is_simple); w.u32(s.is_adaptive); w.scalar(s.level); w.u32(s.use_creases);
  w.scalar(int(s.vtx_boundary_interpolation)); w.scalar(int(s.fvar_linear_interpolation));
}
void read_subdivision(Reader &r, bke::subdiv::Settings &s)
{
  s.is_simple=r.boolean(); s.is_adaptive=r.boolean(); s.level=r.integer(); s.use_creases=r.boolean();
  const int vertex_boundary = r.integer();
  const int face_varying = r.integer();
  if (!r.valid || vertex_boundary < bke::subdiv::SUBDIV_VTX_BOUNDARY_NONE ||
      vertex_boundary > bke::subdiv::SUBDIV_VTX_BOUNDARY_EDGE_AND_CORNER ||
      face_varying < bke::subdiv::SUBDIV_FVAR_LINEAR_INTERPOLATION_NONE ||
      face_varying > bke::subdiv::SUBDIV_FVAR_LINEAR_INTERPOLATION_ALL)
  {
    r.valid = false;
    return;
  }
  /* These enums have no fixed underlying type. Only convert admitted raw integers. */
  s.vtx_boundary_interpolation = bke::subdiv::VtxBoundaryInterpolation(vertex_boundary);
  s.fvar_linear_interpolation = bke::subdiv::FVarLinearInterpolation(face_varying);
}
void write_object(Writer &w, const undo::SnapshotObjectView &s, const ObjectBinding &b)
{
  w.u64(b.object_token); w.u64(b.mesh_token); w.u64(b.key_token);
  w.u32(b.mode); w.scalar(b.shape_key_index); w.u32(b.shape_key_flags);
  w.vector(s.pivot_pos,3); w.vector(s.pivot_rot,4); w.vector(s.pivot_scale,3);
  w.vector(s.last_normal,3); w.u32(s.sticky_shading_color); w.u32(s.needs_flush_to_id);
  w.scalar(s.persistent_grids_num); w.scalar(s.persistent_grid_size);
  w.u32(s.geometry != nullptr);
  w.array(s.persistent_co,3); w.array(s.persistent_no,3); w.array(s.persistent_disp);
  const undo::SnapshotVisibilityView &v=s.visibility;
  w.u32(v.present);
  if (v.present) {
    w.scalar(v.modifier_uid); w.scalar(v.verts_num); w.scalar(v.grids_num); w.scalar(v.grid_size);
    w.array(v.face_offsets); w.array(v.corner_verts);
    w.bits({v.face_hidden,v.face_hidden.is_empty()?0:1,v.face_hidden.size(),v.face_hidden.size()});
    w.bits(v.grid_hidden);
  }
}
bool read_object(Reader &r, ObjectState &s, bool &geometry)
{
  s.binding.object_token=r.u64(); s.binding.mesh_token=r.u64(); s.binding.key_token=r.u64();
  s.binding.mode=r.u32(); s.binding.shape_key_index=r.integer(); s.binding.shape_key_flags=r.u32();
  r.vector(s.pivot_pos,3); r.vector(s.pivot_rot,4); r.vector(s.pivot_scale,3);
  r.vector(s.last_normal,3); s.sticky_shading_color=r.boolean(); s.needs_flush_to_id=r.boolean();
  s.persistent_grids_num=r.integer(); s.persistent_grid_size=r.integer(); geometry=r.boolean();
  uint64_t persistent_count = 0, persistent_area;
  const bool absent = s.persistent_grids_num == -1 && s.persistent_grid_size == -1;
  if (!r.valid || (!absent &&
      (s.persistent_grids_num < 0 || s.persistent_grid_size <= 0 ||
       !product(s.persistent_grid_size, s.persistent_grid_size, persistent_area) ||
       !product(s.persistent_grids_num, persistent_area, persistent_count) ||
       persistent_count > INT64_MAX)))
  {
    return false;
  }
  /* Cache dimensions describe their own generation, which may predate the current grid.
   * Default -1/-1 means absent and has no arrays. Validate each count before allocation
   * in both the scan and owned passes. */
  r.array(s.persistent_co, 3, persistent_count);
  r.array(s.persistent_no, 3, persistent_count);
  r.array(s.persistent_disp, 1, persistent_count);
  s.visibility.present=r.boolean();
  if (s.visibility.present) {
    auto &v=s.visibility;
    v.modifier_uid=r.integer(); v.verts_num=r.integer(); v.grids_num=r.integer(); v.grid_size=r.integer();
    uint64_t area;
    if (!r.valid || v.verts_num < 0 || v.grids_num < 0 || v.grid_size <= 0 ||
        !product(v.grid_size, v.grid_size, area) || area > INT64_MAX) { return false; }
    r.array(v.face_offsets);
    const uint64_t offsets_count = r.last_array_count;
    if (!r.valid || offsets_count < 1) { return false; }
    r.array(v.corner_verts);
    r.face_bits(v.face_hidden, offsets_count - 1);
    r.bits(v.grid_hidden, v.grids_num, area);
  }
  if (!r.scan && s.visibility.present) {
    const auto &v = s.visibility;
    uint64_t area;
    if (v.verts_num < 0 || v.grids_num < 0 || v.grid_size <= 0 ||
        !product(v.grid_size, v.grid_size, area) || area > INT64_MAX ||
        v.face_offsets.size() < 1 || v.face_offsets.first() != 0 ||
        v.face_offsets.last() != v.corner_verts.size() ||
        v.face_hidden.size() != v.face_offsets.size() - 1 ||
        (!v.grid_hidden.is_empty() &&
         (v.grid_hidden.size() != v.grids_num || v.grid_hidden.group_size() != int64_t(area)))) { return false; }
    int previous = 0;
    for (const int offset : v.face_offsets) {
      if (offset < previous || offset > v.corner_verts.size()) { return false; }
      previous = offset;
    }
    for (const int vertex : v.corner_verts) { if (vertex < 0 || vertex >= v.verts_num) { return false; } }
  }
  return r.complete() && s.binding.object_token && s.binding.mesh_token &&
         s.binding.object_token!=s.binding.mesh_token && (s.binding.mode & OB_MODE_SCULPT);
}
void write_grid(Writer &w, const undo::SnapshotGridView &s,
                const uint32_t object_index, const GridBinding &binding)
{
  w.u32(object_index); w.u64(binding.scene_token); w.u64(binding.view_layer_token); w.u32(s.authority);
  write_graph(w,s.graph); write_modifier(w,s.modifier); write_modifier(w,s.evaluated_modifier);
  write_subdivision(w,s.subdivision);
  for (const int v:{s.total_level,s.verts_num,s.level,s.grid_size,s.grid_area,s.grids_num}) { w.scalar(v); }
  w.array(s.modifier_order,3); w.array(s.evaluated_modifier_order,3); w.array(s.edges,2);
  w.array(s.face_offsets); w.array(s.corner_verts); w.array(s.corner_edges); w.array(s.grid_to_face);
  w.array(s.positions,3); w.array(s.normals,3); w.array(s.masks); w.bits(s.hidden);
  w.array(s.pending_normal_faces); w.u32(s.dirty_coords); w.u32(s.dirty_hidden);
}
bool read_grid(Reader &r, GridState &s)
{
  s.object_index=r.u32(); s.scene_token=r.u64(); s.view_layer_token=r.u64(); s.authority=r.boolean();
  read_graph(r,s.graph); read_modifier(r,s.modifier); read_modifier(r,s.evaluated_modifier);
  read_subdivision(r,s.subdivision);
  s.total_level=r.integer(); s.verts_num=r.integer(); s.level=r.integer();
  s.grid_size=r.integer(); s.grid_area=r.integer(); s.grids_num=r.integer();
  uint64_t area, count;
  if (!r.valid || s.verts_num < 0 || s.grids_num < 0 || s.grid_size <= 0 || s.grid_area <= 0 ||
      !product(s.grid_size, s.grid_size, area) || area != uint64_t(s.grid_area) ||
      !product(s.grids_num, area, count) || count > INT64_MAX) { return false; }
  r.array(s.modifier_order,3); r.array(s.evaluated_modifier_order,3); r.array(s.edges,2);
  r.array(s.face_offsets); r.array(s.corner_verts); r.array(s.corner_edges); r.array(s.grid_to_face);
  r.array(s.positions,3); const uint64_t positions_num = r.last_array_count;
  r.array(s.normals,3); const uint64_t normals_num = r.last_array_count;
  r.array(s.masks); const uint64_t masks_num = r.last_array_count; r.bits(s.hidden, s.grids_num, area);
  r.array(s.pending_normal_faces); s.dirty_coords=r.boolean(); s.dirty_hidden=r.boolean();
  if (!r.complete() || !s.scene_token || !s.view_layer_token || s.verts_num<0 ||
      s.grids_num<0 || s.grid_size<=0 || s.grid_area<=0) { return false; }
  if (!product(s.grid_size,s.grid_size,area) || area!=uint64_t(s.grid_area) ||
      !product(s.grids_num,area,count) || count>INT64_MAX ||
      positions_num != count || normals_num != count || (masks_num && masks_num != count)) { return false; }
  if (!r.scan && (s.positions.size()!=int64_t(count) || s.normals.size()!=int64_t(count) ||
      (!s.masks.is_empty() && s.masks.size()!=int64_t(count)) || s.grid_to_face.size()!=s.grids_num ||
      s.corner_verts.size()!=s.corner_edges.size() || s.face_offsets.size()<1 ||
      s.face_offsets.first()!=0 || s.face_offsets.last()!=s.corner_verts.size() ||
      (!s.hidden.is_empty() && (s.hidden.size()!=s.grids_num || s.hidden.group_size()!=s.grid_area)) ||
      (!s.authority && !s.pending_normal_faces.is_empty())))
  { return false; }
  if (!r.scan) {
    for (const int2 &edge : s.edges) {
      if (edge[0] < 0 || edge[1] < 0 || edge[0] >= s.verts_num || edge[1] >= s.verts_num) { return false; }
    }
    int previous = 0;
    for (const int offset : s.face_offsets) {
      if (offset < previous || offset > s.corner_verts.size()) { return false; }
      previous = offset;
    }
    for (const int corner : s.corner_verts) { if (corner < 0 || corner >= s.verts_num) { return false; } }
    for (const int edge : s.corner_edges) { if (edge < 0 || edge >= s.edges.size()) { return false; } }
    const int64_t faces = s.face_offsets.size() - 1;
    for (const int face : s.grid_to_face) { if (face < 0 || face >= faces) { return false; } }
    for (const int face : s.pending_normal_faces) { if (face < 0 || face >= faces) { return false; } }
  }
  return true;
}
}  // namespace

ObjectState::ObjectState(ObjectState &&other) noexcept { *this=std::move(other); }
ObjectState &ObjectState::operator=(ObjectState &&other) noexcept
{
  if (this==&other) { return *this; }
  if (geometry) { BM_mesh_free(geometry); }
  if (geometry_plan) { BM_geometry_snapshot_decode_plan_free(geometry_plan); }
  binding=other.binding; pivot_pos=other.pivot_pos; pivot_rot=other.pivot_rot;
  pivot_scale=other.pivot_scale; last_normal=other.last_normal;
  sticky_shading_color=other.sticky_shading_color; needs_flush_to_id=other.needs_flush_to_id;
  persistent_grids_num=other.persistent_grids_num; persistent_grid_size=other.persistent_grid_size;
  persistent_co=std::move(other.persistent_co); persistent_no=std::move(other.persistent_no);
  persistent_disp=std::move(other.persistent_disp); visibility=std::move(other.visibility);
  geometry_plan=std::exchange(other.geometry_plan,nullptr);
  geometry=std::exchange(other.geometry,nullptr);
  return *this;
}
ObjectState::~ObjectState()
{
  if (geometry) { BM_mesh_free(geometry); }
  if (geometry_plan) { BM_geometry_snapshot_decode_plan_free(geometry_plan); }
}

static bool encode_views(const Span<undo::SnapshotObjectView> objects,
                         const Span<undo::SnapshotGridView> grids,
                         const bool flush_needed,
                         const CaptureID &capture_id,
                         const Span<ObjectBinding> bindings,
                         const Span<GridBinding> grid_bindings,
                         const CodecLimits &limits,
                         FunctionRef<bool(Span<std::byte>)> append,
                         const char **r_error, EncodeInfo *r_info)
{
  if (r_error) { *r_error = nullptr; }
  if (r_info) { *r_info = {}; }
  if (!capture_valid(capture_id) || objects.size() != bindings.size() ||
      grids.size() != grid_bindings.size() || objects.size() > INT32_MAX || grids.size() > INT32_MAX) {
    return fail(r_error, "The Sculpt companion requires a capture identity and exact writer bindings");
  }
  uint32_t geometry_sections = 0;
  for (const auto &object : objects) { geometry_sections += object.geometry != nullptr; }
  const uint64_t sections = uint64_t(objects.size()) + grids.size() + geometry_sections;
  size_t metadata = buffer_size;
  uint64_t bytes;
  const auto account = [&](const uint64_t count, const uint64_t size) {
    return product(count, size, bytes) && charge(metadata, bytes, limits.max_scratch_bytes);
  };
  if (!account(objects.size(), sizeof(undo::SnapshotObjectView) + sizeof(ObjectBinding)) ||
      !account(grids.size(), sizeof(undo::SnapshotGridView) + sizeof(GridBinding) + sizeof(int)) ||
      !account(sections, sizeof(Entry)) ||
      (geometry_sections && limits.max_scratch_bytes - metadata < buffer_size)) {
    return fail(r_error, "Aggregate Sculpt encode metadata/staging exceeds the scratch policy");
  }
  for (const GridBinding &binding : grid_bindings) {
    if (!std::memchr(binding.view_layer, 0, sizeof(binding.view_layer))) {
      return fail(r_error, "Sculpt writer ViewLayer binding is not bounded");
    }
  }
  Array<ObjectBinding, 0> selected(objects.size());
  for (const int64_t i : objects.index_range()) {
    const auto &object = objects[i];
    const ObjectBinding *match=nullptr;
    for (const auto &binding:bindings) {
      if (binding.object_uid==object.object_uid && binding.mesh_uid==object.mesh_uid) {
        if (match) { return fail(r_error,"Duplicate Sculpt object writer binding"); }
        match=&binding;
      }
    }
    if (!match || !match->object_uid || !match->mesh_uid ||
        ((match->key_uid == 0) != (match->key_token == 0)) ||
        !match->object_token || !match->mesh_token ||
        match->object_token==match->mesh_token || !(match->mode & OB_MODE_SCULPT)) {
      return fail(r_error,"Missing or invalid Sculpt object writer token or context");
    }
    selected[i] = *match;
    for (int64_t previous = 0; previous < i; previous++) {
      if (selected[previous].object_token == match->object_token ||
          (selected[previous].mesh_token == match->mesh_token && selected[previous].key_token != match->key_token)) {
        return fail(r_error, "Duplicate Object token or conflicting shared-Mesh Key binding");
      }
    }
  }
  Array<GridBinding, 0> selected_grids(grids.size());
  Array<int, 0> grid_objects(grids.size());
  for (const int64_t i : grids.index_range()) {
    const auto &grid = grids[i];
    const GridBinding *match = nullptr;
    int object = -1;
    for (const int64_t j : objects.index_range()) {
      if (objects[j].object_uid == grid.object_uid && objects[j].mesh_uid == grid.mesh_uid) { object = int(j); }
    }
    for (const auto &binding : grid_bindings) {
      if (binding.object_uid == grid.object_uid && binding.mesh_uid == grid.mesh_uid &&
          binding.scene_uid == grid.scene_uid && STREQ(binding.view_layer, grid.view_layer)) {
        if (match) { return fail(r_error, "Duplicate Sculpt graph writer binding"); }
        match = &binding;
      }
    }
    if (object < 0 || !match || !match->scene_token || !match->view_layer_token) {
      return fail(r_error, "Missing late Scene/ViewLayer token for a Sculpt owner");
    }
    selected_grids[i] = *match;
    grid_objects[i] = object;
    for (int64_t previous = 0; previous < i; previous++) {
      if (grid_objects[previous] == object && selected_grids[previous].scene_token == match->scene_token &&
          selected_grids[previous].view_layer_token == match->view_layer_token) {
        return fail(r_error, "Duplicate canonical Sculpt writer owner token");
      }
    }
  }
  for (const int64_t i : objects.index_range()) {
    int owners = 0, authority = 0;
    for (const int64_t j : grids.index_range()) {
      if (grid_objects[j] == i) { owners++; authority += int(grids[j].authority); }
    }
    if (owners && (authority != 1 || objects[i].geometry || objects[i].visibility.present)) {
      return fail(r_error, "Sculpt writer owners have conflicting runtime kind or authority");
    }
  }
  Writer w{append,limits.max_file_bytes};
  w.bytes(header_magic,8); w.u32(version); w.u32(0x01020304); w.bytes(capture_id.data(),16);
  w.u32(objects.size()); w.u32(grids.size());
  w.u32(flush_needed); w.u32(0);
  Array<Entry, 0> directory{int64_t(sections)};
  int64_t directory_index = 0;
  const auto section=[&](const Section type,const uint32_t owner,auto encode) {
    const uint64_t begin=w.position;
    encode(); directory[directory_index++] = {type,owner,begin,w.position-begin};
  };
  for (const int64_t i:objects.index_range()) {
    section(Section::Object,uint32_t(i),[&]{write_object(w,objects[i],selected[i]);});
    if (objects[i].geometry) {
      if (!w.flush()) { break; }
      const uint64_t begin=w.position;
      const BMGeometryCodecLimits bm_limits{limits.max_file_bytes,
                                            limits.max_backing_bytes,
                                            limits.max_scratch_bytes - metadata};
      if (!BM_geometry_snapshot_encode(objects[i].geometry,bm_limits,[&](Span<std::byte> bytes) {
            w.bytes(bytes.data(),bytes.size()); return w.valid;
          },r_error)) { return false; }
      directory[directory_index++] = {Section::Geometry,uint32_t(i),begin,w.position-begin};
    }
  }
  for (const int64_t i : grids.index_range()) {
    section(Section::Grid, uint32_t(i), [&] {
      write_grid(w, grids[i], uint32_t(grid_objects[i]), selected_grids[i]);
    });
  }
  if (!w.valid || directory_index != directory.size()) {
    return fail(r_error, "Sculpt companion sink failed before its complete section set");
  }
  const uint64_t directory_offset=w.position;
  for (const Entry &entry:directory) {
    w.u32(uint32_t(entry.type)); w.u32(entry.owner); w.u64(entry.offset); w.u64(entry.length);
  }
  uint64_t final_size=w.position;
  if (!add(final_size,footer_size)) { return fail(r_error,"Sculpt companion extent overflow"); }
  w.bytes(footer_magic,8); w.u64(directory_offset); w.u64(directory.size()); w.u64(final_size);
  if (!w.flush()) { return fail(r_error,"Sculpt companion sink failed or exceeded its file budget"); }
  if (r_info) {
    *r_info = {capture_id, version, uint32_t(objects.size()), uint32_t(grids.size()),
               uint32_t(directory.size() - objects.size() - grids.size()), w.position, true};
  }
  return true;
}

bool companion_encode(const undo::MainUndoState *snapshot,
                      const CaptureID &capture_id, const Span<ObjectBinding> objects,
                      const Span<GridBinding> grids, const CodecLimits &limits,
                      FunctionRef<bool(Span<std::byte>)> append,
                      const char **r_error, EncodeInfo *r_info)
{
  if (r_info) { *r_info = {}; }
  uint64_t object_bytes, grid_bytes;
  size_t views_bytes = 0;
  if (objects.size() > INT32_MAX || grids.size() > INT32_MAX ||
      !product(objects.size(), sizeof(undo::SnapshotObjectView), object_bytes) ||
      !product(grids.size(), sizeof(undo::SnapshotGridView), grid_bytes) ||
      !charge(views_bytes, object_bytes, limits.max_scratch_bytes) ||
      !charge(views_bytes, grid_bytes, limits.max_scratch_bytes)) {
    return fail(r_error, "Sculpt borrowed-view metadata is not admissible");
  }
  Array<undo::SnapshotObjectView, 0> object_views(objects.size());
  Array<undo::SnapshotGridView, 0> grid_views(grids.size());
  int64_t object_index = 0, grid_index = 0;
  if (snapshot &&
      (!undo::main_undo_snapshot_objects(*snapshot, [&](const auto &view) {
         if (object_index >= object_views.size()) { return false; }
         object_views[object_index++] = view; return true;
       }) ||
       !undo::main_undo_snapshot_grids(*snapshot, [&](const auto &view) {
         if (grid_index >= grid_views.size()) { return false; }
         grid_views[grid_index++] = view; return true;
       }))) {
    return fail(r_error, "The Sculpt snapshot has an owner absent from actual writer bindings");
  }
  if (object_index != objects.size() || grid_index != grids.size()) {
    return fail(r_error, "The Sculpt writer bindings include a missing snapshot owner");
  }
  return encode_views(object_views.as_span(), grid_views.as_span(),
                      snapshot && undo::main_undo_snapshot_flush_needed(*snapshot),
                      capture_id, objects, grids, limits, append, r_error, r_info);
}

#ifdef WITH_GTESTS
bool companion_encode_views_for_test(const Span<undo::SnapshotObjectView> objects,
                                      const Span<undo::SnapshotGridView> grids,
                                      const bool flush_needed,
                                      const CaptureID &capture_id,
                                      const Span<ObjectBinding> object_bindings,
                                      const Span<GridBinding> grid_bindings,
                                      const CodecLimits &limits,
                                      FunctionRef<bool(Span<std::byte>)> append,
                                      const char **r_error)
{
  return encode_views(objects, grids, flush_needed, capture_id, object_bindings,
                      grid_bindings, limits, append, r_error, nullptr);
}
#endif

std::unique_ptr<DecodePlan> companion_decode_prepare(
    const uint64_t file_bytes,const CaptureID &expected_capture,const CodecLimits &limits,
    FunctionRef<bool(uint64_t,MutableSpan<std::byte>)> read,const char **r_error)
{
  if (r_error) { *r_error=nullptr; }
  const auto refusal=[&](const char *message)->std::unique_ptr<DecodePlan> {
    fail(r_error,message); return nullptr;
  };
  if (file_bytes<header_size+footer_size || file_bytes>limits.max_file_bytes ||
      !capture_valid(expected_capture) || limits.max_scratch_bytes<buffer_size) {
    return refusal("The Sculpt companion input extent or capture policy is invalid");
  }
  size_t retained=sizeof(DecodePlan);
  Reader header(read,0,header_size,true,retained,limits.max_backing_bytes);
  std::array<char,8> magic; CaptureID capture;
  header.bytes(magic.data(),8); const uint32_t format=header.u32(),endian=header.u32();
  header.bytes(capture.data(),16); const uint32_t objects_num=header.u32(),grids_num=header.u32();
  const bool flush_needed=header.boolean(); const uint32_t reserved=header.u32();
  if (!header.complete() || magic!=std::array<char,8>{'A','S','C','P','T','0','0','1'} ||
      format!=version || endian!=0x01020304 || capture!=expected_capture || reserved ||
      objects_num>INT32_MAX || grids_num>INT32_MAX ||
      !charge(retained,uint64_t(objects_num)*sizeof(ObjectState),limits.max_backing_bytes) ||
      !charge(retained,uint64_t(grids_num)*sizeof(GridState),limits.max_backing_bytes)) {
    return refusal("The Sculpt companion header, capture identity or aggregate owner count is invalid");
  }
  Reader footer(read,file_bytes-footer_size,footer_size,true,retained,limits.max_backing_bytes);
  footer.bytes(magic.data(),8); const uint64_t directory_offset=footer.u64(),count=footer.u64();
  const uint64_t declared_size=footer.u64(); uint64_t directory_bytes;
  if (!footer.complete() || magic!=std::array<char,8>{'A','S','C','E','N','D','0','1'} ||
      declared_size!=file_bytes || directory_offset<header_size ||
      count < uint64_t(objects_num) + grids_num || count>uint64_t(objects_num)*2+grids_num ||
      !product(count,entry_size,directory_bytes) || directory_offset>file_bytes-footer_size ||
      directory_bytes!=file_bytes-footer_size-directory_offset ||
      directory_bytes>limits.max_scratch_bytes-buffer_size) {
    return refusal("The Sculpt companion footer directory is truncated, unbounded or inconsistent");
  }
  uint64_t minimum_payload = uint64_t(objects_num) * 136;
  if (!add(minimum_payload, uint64_t(grids_num) * 312) ||
      minimum_payload > directory_offset - header_size) {
    return refusal("The Sculpt owner counts cannot fit their minimum wire payloads");
  }
  uint64_t metadata_bytes = directory_bytes;
  uint64_t part;
  if (!product(objects_num, sizeof(bool) * 3 + sizeof(int) * 2 + sizeof(uint64_t) * 3 + sizeof(ObjectBinding), part) ||
      !add(metadata_bytes, part) ||
      !product(grids_num, sizeof(bool) + sizeof(uint64_t) * 9, part) ||
      !add(metadata_bytes, part) || metadata_bytes > limits.max_scratch_bytes - buffer_size) {
    return refusal("Aggregate Sculpt directory/token scratch is not admissible");
  }
  const size_t metadata_scratch = size_t(metadata_bytes) + buffer_size;
  Array<Entry,0> entries{int64_t(count)};
  Reader directory(read,directory_offset,directory_bytes,true,retained,limits.max_backing_bytes);
  uint64_t previous=header_size;
  Array<bool,0> object_seen(objects_num,false),grid_seen(grids_num,false),geometry_seen(objects_num,false);
  for (Entry &entry:entries) {
    entry.type=Section(directory.u32()); entry.owner=directory.u32(); entry.offset=directory.u64(); entry.length=directory.u64();
    if (!directory.valid || entry.offset!=previous || !entry.length || entry.offset>directory_offset ||
        entry.length>directory_offset-entry.offset) { return refusal("Empty, overlapping or aliased Sculpt section"); }
    previous=entry.offset+entry.length;
    if (entry.type==Section::Object && entry.owner<objects_num && !object_seen[entry.owner]) { object_seen[entry.owner]=true; }
    else if (entry.type==Section::Grid && entry.owner<grids_num && !grid_seen[entry.owner]) { grid_seen[entry.owner]=true; }
    else if (entry.type==Section::Geometry && entry.owner<objects_num && !geometry_seen[entry.owner]) { geometry_seen[entry.owner]=true; }
    else { return refusal("The Sculpt companion has duplicate, foreign or missing owner sections"); }
  }
  if (!directory.complete() || previous!=directory_offset ||
      std::any_of(object_seen.begin(),object_seen.end(),[](bool v){return !v;}) ||
      std::any_of(grid_seen.begin(),grid_seen.end(),[](bool v){return !v;})) {
    return refusal("The Sculpt companion owner directory is incomplete");
  }
  /* Admit every token relationship and array/resource sum before payload allocation. */
  Array<uint64_t, 0> object_tokens(objects_num);
  Array<std::pair<uint64_t, uint64_t>, 0> mesh_keys(objects_num);
  Array<std::array<uint64_t, 3>, 0> graph_keys(grids_num);
  Array<std::pair<uint64_t, uint64_t>, 0> layer_scene_keys(grids_num);
  Array<int, 0> authority(objects_num, 0), owners(objects_num, 0);
  Array<bool, 0> visibility_seen(objects_num, false);
  Array<ObjectBinding, 0> expected_objects(objects_num);
  Array<std::array<uint64_t, 4>, 0> expected_grids(grids_num);
  for (const Entry &entry : entries) {
    if (entry.type == Section::Geometry) { continue; }
    Reader scan(read, entry.offset, entry.length, true, retained, limits.max_backing_bytes);
    bool valid;
    if (entry.type == Section::Object) {
      ObjectState object; bool geometry = false;
      valid = read_object(scan, object, geometry) && geometry == geometry_seen[entry.owner];
      object_tokens[entry.owner] = object.binding.object_token;
      mesh_keys[entry.owner] = {object.binding.mesh_token, object.binding.key_token};
      visibility_seen[entry.owner] = object.visibility.present;
      expected_objects[entry.owner] = object.binding;
    }
    else {
      GridState grid;
      valid = read_grid(scan, grid) && grid.object_index < objects_num;
      if (valid) {
        graph_keys[entry.owner] = {grid.object_index, grid.scene_token, grid.view_layer_token};
        layer_scene_keys[entry.owner] = {grid.view_layer_token, grid.scene_token};
        expected_grids[entry.owner] = {grid.object_index, grid.scene_token, grid.view_layer_token, uint64_t(grid.authority)};
        owners[grid.object_index]++; authority[grid.object_index] += int(grid.authority);
      }
    }
    if (!valid) {
      return refusal("The Sculpt companion token/profile/array extent or aggregate backing is invalid");
    }
  }
  if (objects_num > 1) {
    std::sort(object_tokens.begin(), object_tokens.end());
    std::sort(mesh_keys.begin(), mesh_keys.end());
  }
  for (int64_t i = 1; i < object_tokens.size(); i++) {
    if (object_tokens[i] == object_tokens[i - 1]) { return refusal("Duplicate BASE Object token in Sculpt companion"); }
    if (mesh_keys[i].first == mesh_keys[i - 1].first && mesh_keys[i].second != mesh_keys[i - 1].second) {
      return refusal("Shared Mesh owners disagree on actual BASE Key token");
    }
  }
  const auto mesh_token_present = [&](uint64_t token) {
    if (mesh_keys.is_empty()) { return false; }
    const auto found = std::lower_bound(mesh_keys.begin(), mesh_keys.end(), token,
        [](const auto &pair, uint64_t value) { return pair.first < value; });
    return found != mesh_keys.end() && found->first == token;
  };
  const auto object_token_present = [&](uint64_t token) {
    return !object_tokens.is_empty() && std::binary_search(object_tokens.begin(), object_tokens.end(), token);
  };
  for (const auto &pair : mesh_keys) {
    if (object_token_present(pair.first) || (pair.second &&
        (object_token_present(pair.second) || mesh_token_present(pair.second)))) {
      return refusal("The Sculpt companion aliases different BASE ID token roles");
    }
  }
  if (grids_num > 1) {
    std::sort(graph_keys.begin(), graph_keys.end());
    std::sort(layer_scene_keys.begin(), layer_scene_keys.end());
  }
  for (int64_t i = 1; i < graph_keys.size(); i++) {
    if (graph_keys[i] == graph_keys[i - 1]) { return refusal("Duplicate canonical Sculpt graph owner"); }
    if (layer_scene_keys[i].first == layer_scene_keys[i - 1].first &&
        layer_scene_keys[i].second != layer_scene_keys[i - 1].second) {
      return refusal("A Sculpt ViewLayer token is bound to multiple Scene tokens");
    }
  }
  for (const int64_t i : owners.index_range()) {
    if (owners[i] && (authority[i] != 1 || geometry_seen[i] || visibility_seen[i])) {
      return refusal("Sculpt grid owners have conflicting runtime kind or session authority");
    }
  }
  auto plan=std::make_unique<DecodePlan>();
  plan->capture_id=capture; plan->flush_needed=flush_needed;
  plan->objects=Array<ObjectState,0>(objects_num); plan->grids=Array<GridState,0>(grids_num);
  size_t actual=sizeof(DecodePlan)+size_t(objects_num)*sizeof(ObjectState)+size_t(grids_num)*sizeof(GridState);
  for (const Entry &entry:entries) {
    if (entry.type==Section::Geometry) { continue; }
    Reader payload(read,entry.offset,entry.length,false,actual,limits.max_backing_bytes);
    bool valid;
    if (entry.type==Section::Object) {
      bool geometry = false;
      valid = read_object(payload, plan->objects[entry.owner], geometry) && geometry == geometry_seen[entry.owner];
      const auto &got = plan->objects[entry.owner].binding;
      const auto &expected = expected_objects[entry.owner];
      valid &= got.object_token == expected.object_token && got.mesh_token == expected.mesh_token &&
               got.key_token == expected.key_token && got.mode == expected.mode &&
               got.shape_key_index == expected.shape_key_index && got.shape_key_flags == expected.shape_key_flags &&
               plan->objects[entry.owner].visibility.present == visibility_seen[entry.owner];
    }
    else {
      GridState &got = plan->grids[entry.owner];
      valid = read_grid(payload, got) && expected_grids[entry.owner] ==
          std::array<uint64_t, 4>{got.object_index, got.scene_token, got.view_layer_token, uint64_t(got.authority)};
    }
    if (!valid) { return refusal("The Sculpt companion payload changed or failed its owned decode"); }
  }
  plan->info={file_bytes,actual,0,metadata_scratch};
  for (const Entry &entry:entries) {
    if (entry.type!=Section::Geometry) { continue; }
    if (entry.length>INT64_MAX || !charge(plan->info.retained_bytes,entry.length,limits.max_backing_bytes) ||
        plan->info.decoded_geometry_bound > limits.max_backing_bytes - plan->info.retained_bytes) {
      return refusal("Aggregate Sculpt BMesh input exceeds backing admission"); }
    Array<std::byte,0> bytes{int64_t(entry.length)};
    if (!read(entry.offset,bytes.as_mutable_span())) { return refusal("Failed to read exact owned Sculpt BMesh section"); }
    const size_t prior_retained = plan->info.retained_bytes - size_t(entry.length);
    if (plan->info.decoded_geometry_bound > limits.max_backing_bytes - prior_retained) {
      return refusal("Aggregate Sculpt prior decode backing is not admissible");
    }
    const BMGeometryCodecLimits bm_limits{entry.length,
        limits.max_backing_bytes - prior_retained - plan->info.decoded_geometry_bound,
        limits.max_scratch_bytes - metadata_scratch};
    auto *geometry=BM_geometry_snapshot_decode_prepare(std::move(bytes),bm_limits,r_error);
    if (!geometry) { return nullptr; }
    plan->objects[entry.owner].geometry_plan=geometry;
    const auto &info=BM_geometry_snapshot_decode_info(*geometry);
    plan->info.retained_bytes-=size_t(entry.length);
    if (!charge(plan->info.retained_bytes,info.owned_input_and_plan_bytes,limits.max_backing_bytes) ||
        !charge(plan->info.decoded_geometry_bound,info.decoded_backing_bytes_bound,limits.max_backing_bytes) ||
        plan->info.retained_bytes>limits.max_backing_bytes-plan->info.decoded_geometry_bound) {
      return refusal("Aggregate Sculpt retained/decode backing exceeds admission"); }
    plan->info.scratch_bound=std::max(plan->info.scratch_bound,
        metadata_scratch + info.decode_scratch_bytes_bound);
  }
  return plan;
}

static bool decode_geometry_all(
    DecodePlan &plan,
    FunctionRef<BMesh *(const BMGeometryDecodePlan &, uint32_t, const char **)> decode,
    const char **r_error)
{
  if (r_error) { *r_error = nullptr; }
  for (const ObjectState &object : plan.objects) {
    if (object.geometry) { return fail(r_error, "The Sculpt detached geometry plan was already constructed"); }
  }
  for (const int64_t i : plan.objects.index_range()) {
    ObjectState &object = plan.objects[i];
    if (!object.geometry_plan) { continue; }
    object.geometry = decode(*object.geometry_plan, uint32_t(i), r_error);
    if (!object.geometry) {
      for (ObjectState &previous : plan.objects) {
        if (previous.geometry) { BM_mesh_free(previous.geometry); previous.geometry = nullptr; }
      }
      return false;
    }
  }
  return true;
}

bool companion_decode_geometry(DecodePlan &plan, const char **r_error)
{
  return decode_geometry_all(plan, [](const BMGeometryDecodePlan &geometry, uint32_t, const char **error) {
    return BM_geometry_snapshot_decode(geometry, nullptr, error);
  }, r_error);
}

#ifdef WITH_GTESTS
bool companion_decode_bits_for_test(const bool face, const bool scan, const uint64_t file_bytes,
                                    const uint64_t expected_groups,
                                    const uint64_t expected_group_size,
                                    FunctionRef<bool(uint64_t, MutableSpan<std::byte>)> read)
{
  size_t retained = 0;
  Reader reader(read, 0, file_bytes, scan, retained, SIZE_MAX);
  if (face) {
    BitVector<> value;
    reader.face_bits(value, expected_group_size);
  }
  else {
    BitGroupVector<> value;
    reader.bits(value, expected_groups, expected_group_size);
  }
  return reader.complete();
}

bool companion_decode_geometry_with_test_fault(DecodePlan &plan,
                                               const uint32_t owner,
                                               const BMGeometryDecodeTestPhase phase,
                                               const uint32_t element,
                                               const char **r_error)
{
  return decode_geometry_all(plan, [&](const BMGeometryDecodePlan &geometry,
                                       const uint32_t index, const char **error) {
    return index == owner ? BM_geometry_snapshot_decode_with_test_fault(geometry, phase, element, error) :
                            BM_geometry_snapshot_decode(geometry, nullptr, error);
  }, r_error);
}
#endif
}  // namespace blender::ed::sculpt_paint::checkpoint
