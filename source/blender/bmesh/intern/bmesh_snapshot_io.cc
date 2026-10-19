/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup bmesh
 *
 * Private checkpoint geometry. Wire fields are independent of native padding and pointers.
 */

#include <algorithm>
#include <array>
#include <climits>
#include <cstring>
#include <limits>
#include <memory>

#include "MEM_guardedalloc.h"

#define DNA_DEPRECATED_ALLOW
#include "DNA_customdata_types.h"
#include "DNA_meshdata_types.h"

#include "BLI_bitmap.h"
#include "BLI_listbase.h"
#include "BLI_map.hh"
#include "BLI_math_matrix_types.hh"
#include "BLI_math_vector_types.hh"
#include "BLI_mempool.h"

#include "BKE_customdata.hh"

#include "bmesh.hh"
#include "bmesh_snapshot_io.hh"

namespace blender {
namespace {

constexpr uint32_t no_element = UINT32_MAX;
constexpr std::array<char, 8> magic = {'B', 'M', 'S', 'N', 'A', 'P', '0', '1'};
constexpr size_t output_chunk_size = 4096;

bool fail(const char **error, const char *message)
{
  if (error && !*error) {
    *error = message;
  }
  return false;
}

bool add(size_t &a, const size_t b)
{
  if (b > SIZE_MAX - a) {
    return false;
  }
  a += b;
  return true;
}

bool product(const size_t a, const size_t b, size_t &result)
{
  if (b && a > SIZE_MAX / b) {
    return false;
  }
  result = a * b;
  return true;
}

bool rounded4(const size_t value, size_t &result)
{
  result = value;
  if (!add(result, 3)) {
    return false;
  }
  result &= ~size_t(3);
  return true;
}

bool add_allocation(size_t &total, const size_t bytes)
{
  size_t rounded;
  return rounded4(bytes, rounded) && add(total, rounded);
}

struct Field {
  uint16_t offset;
  uint8_t width;
  uint8_t count;
};

enum class Payload : uint8_t { Fixed, Deform, Displacements, GridMask };

struct TypeInfo {
  uint16_t tag;
  eCustomDataType type;
  uint16_t size;
  uint16_t alignment;
  Span<Field> fields;
  uint8_t repeats = 1;
  uint16_t stride = 0;
  Payload payload = Payload::Fixed;
};

Span<TypeInfo> type_infos()
{
  static const Field u8[] = {{0, 1, 1}};
  static const Field u32[] = {{0, 4, 1}};
  static const Field u32_2[] = {{0, 4, 2}};
  static const Field u32_3[] = {{0, 4, 3}};
  static const Field u32_4[] = {{0, 4, 4}};
  static const Field u32_8[] = {{0, 4, 8}};
  static const Field u32_16[] = {{0, 4, 16}};
  static const Field u16_2[] = {{0, 2, 2}};
  static const Field u16_12[] = {{0, 2, 12}};
  static const Field string[] = {{offsetof(MStringProperty, s), 1, 255},
                                 {offsetof(MStringProperty, s_len), 1, 1}};
  static const Field vert[] = {{offsetof(MVert, co_legacy), 4, 3},
                               {offsetof(MVert, flag_legacy), 1, 1},
                               {offsetof(MVert, bweight_legacy), 1, 1}};
  static const Field edge[] = {{offsetof(MEdge, v1), 4, 2},
                               {offsetof(MEdge, crease_legacy), 1, 1},
                               {offsetof(MEdge, bweight_legacy), 1, 1},
                               {offsetof(MEdge, flag_legacy), 2, 1}};
  static const Field face[] = {{offsetof(MFace, v1), 4, 4},
                               {offsetof(MFace, mat_nr), 2, 1},
                               {offsetof(MFace, edcode), 1, 1},
                               {offsetof(MFace, flag), 1, 1}};
  static const Field poly[] = {{offsetof(MPoly, loopstart), 4, 1},
                               {offsetof(MPoly, totloop), 4, 1},
                               {offsetof(MPoly, mat_nr_legacy), 2, 1},
                               {offsetof(MPoly, flag_legacy), 1, 1}};
  static const Field loop_uv[] = {{offsetof(MLoopUV, uv), 4, 2}, {offsetof(MLoopUV, flag), 4, 1}};
  static const Field skin[] = {{offsetof(MVertSkin, radius), 4, 3},
                               {offsetof(MVertSkin, flag), 4, 1}};
  /* These channel orders intentionally differ. Never reinterpret legacy MCol as MLoopCol. */
  static const Field legacy_color[] = {{offsetof(MCol, a), 1, 1},
                                       {offsetof(MCol, r), 1, 1},
                                       {offsetof(MCol, g), 1, 1},
                                       {offsetof(MCol, b), 1, 1}};
  static const Field byte_color[] = {{offsetof(MLoopCol, r), 1, 1},
                                     {offsetof(MLoopCol, g), 1, 1},
                                     {offsetof(MLoopCol, b), 1, 1},
                                     {offsetof(MLoopCol, a), 1, 1}};

#define VALUE(TAG, TYPE, NATIVE, FIELDS) \
  { \
    TAG, TYPE, sizeof(NATIVE), alignof(NATIVE), Span<Field>(FIELDS, ARRAY_SIZE(FIELDS)) \
  }
  /* Version-one tags are explicit literals. They must not follow future enum renumbering. */
  static const TypeInfo types[] = {
      VALUE(0, CD_MVERT, MVert, vert),
      VALUE(1, CD_MSTICKY, float2, u32_2),
      {2, CD_MDEFORMVERT, sizeof(MDeformVert), alignof(MDeformVert), {}, 1, 0, Payload::Deform},
      VALUE(3, CD_MEDGE, MEdge, edge),
      VALUE(4, CD_MFACE, MFace, face),
      VALUE(5, CD_MTFACE, MTFace, u32_8),
      {6,
       CD_MCOL,
       sizeof(MCol[4]),
       alignof(MCol),
       Span<Field>(legacy_color, ARRAY_SIZE(legacy_color)),
       4,
       sizeof(MCol)},
      VALUE(7, CD_ORIGINDEX, int, u32),
      VALUE(8, CD_NORMAL, float3, u32_3),
      VALUE(9, CD_FACEMAP, int, u32),
      VALUE(10, CD_PROP_FLOAT, MFloatProperty, u32),
      VALUE(11, CD_PROP_INT32, MIntProperty, u32),
      VALUE(12, CD_PROP_STRING, MStringProperty, string),
      VALUE(13, CD_ORIGSPACE, OrigSpaceFace, u32_8),
      VALUE(14, CD_ORCO, float3, u32_3),
      VALUE(15, CD_MTEXPOLY, int, u32),
      VALUE(16, CD_MLOOPUV, MLoopUV, loop_uv),
      VALUE(17, CD_PROP_BYTE_COLOR, MLoopCol, byte_color),
      VALUE(18, CD_TANGENT, float4, u32_4),
      {19, CD_MDISPS, sizeof(MDisps), alignof(MDisps), {}, 1, 0, Payload::Displacements},
      VALUE(20, CD_PROP_FLOAT4X4, float4x4, u32_16),
      VALUE(22, CD_PROP_INT16_2D, short2, u16_2),
      VALUE(23, CD_CLOTH_ORCO, float3, u32_3),
      VALUE(24, CD_PROP_FLOAT4, float4, u32_4),
      VALUE(25, CD_MPOLY, MPoly, poly),
      VALUE(26, CD_MLOOP, MLoop, u32_2),
      VALUE(27, CD_SHAPE_KEYINDEX, int, u32),
      VALUE(28, CD_SHAPEKEY, float3, u32_3),
      VALUE(29, CD_BWEIGHT, MFloatProperty, u32),
      VALUE(30, CD_CREASE, float, u32),
      VALUE(31, CD_ORIGSPACE_MLOOP, OrigSpaceLoop, u32_2),
      VALUE(34, CD_PAINT_MASK, float, u32),
      {35,
       CD_GRID_PAINT_MASK,
       sizeof(GridPaintMask),
       alignof(GridPaintMask),
       {},
       1,
       0,
       Payload::GridMask},
      VALUE(36, CD_MVERT_SKIN, MVertSkin, skin),
      VALUE(37, CD_FREESTYLE_EDGE, FreestyleEdge, u8),
      VALUE(38, CD_FREESTYLE_FACE, FreestyleFace, u8),
      VALUE(39, CD_MLOOPTANGENT, float4, u32_4),
      VALUE(40, CD_TESSLOOPNORMAL, short[4][3], u16_12),
      VALUE(41, CD_CUSTOMLOOPNORMAL, short2, u16_2),
      VALUE(42, CD_SCULPT_FACE_SETS, int, u32),
      VALUE(45, CD_PROP_INT8, int8_t, u8),
      VALUE(46, CD_PROP_INT32_2D, int2, u32_2),
      VALUE(47, CD_PROP_COLOR, MPropCol, u32_4),
      VALUE(48, CD_PROP_FLOAT3, float3, u32_3),
      VALUE(49, CD_PROP_FLOAT2, float2, u32_2),
      VALUE(50, CD_PROP_BOOL, bool, u8),
      VALUE(52, CD_PROP_QUATERNION, float4, u32_4),
  };
#undef VALUE
  static_assert(sizeof(types) / sizeof(TypeInfo) == 47);
  return Span<TypeInfo>(types, ARRAY_SIZE(types));
}

const TypeInfo *type_info_from_tag(const uint16_t tag)
{
  for (const TypeInfo &info : type_infos()) {
    if (info.tag == tag) {
      return &info;
    }
  }
  return nullptr;
}

const TypeInfo *type_info(const eCustomDataType type)
{
  for (const TypeInfo &info : type_infos()) {
    if (info.type == type) {
      return &info;
    }
  }
  return nullptr;
}

class Reader {
 public:
  Span<std::byte> bytes;
  size_t position = 0;
  bool ok = true;

  explicit Reader(const Span<std::byte> input) : bytes(input) {}

  const std::byte *take(const size_t count)
  {
    if (!ok || count > size_t(bytes.size()) - position) {
      ok = false;
      return nullptr;
    }
    const std::byte *result = bytes.data();
    if (position) {
      result += position;
    }
    position += count;
    return result;
  }

  uint32_t integer(const uint8_t width)
  {
    const std::byte *data = take(width);
    uint32_t result = 0;
    if (data) {
      for (uint8_t i = 0; i < width; i++) {
        result |= uint32_t(std::to_integer<uint8_t>(data[i])) << (i * 8);
      }
    }
    return result;
  }

  bool string(char *destination, const size_t capacity)
  {
    const uint32_t length = integer(2);
    if (!ok || length >= capacity) {
      return ok = false;
    }
    const std::byte *data = take(length);
    if (!ok || (length && std::memchr(data, 0, length))) {
      return ok = false;
    }
    if (length) {
      std::memcpy(destination, data, length);
    }
    destination[length] = '\0';
    return true;
  }
};

class Writer {
  FunctionRef<bool(Span<std::byte>)> append_;
  std::array<std::byte, output_chunk_size> buffer_;
  size_t used_ = 0;
  uint64_t maximum_;

 public:
  uint64_t total = 0;
  bool ok = true;

  Writer(FunctionRef<bool(Span<std::byte>)> append, const uint64_t maximum)
      : append_(append), maximum_(maximum)
  {
  }

  bool flush()
  {
    if (ok && used_) {
      ok = append_(Span(buffer_.data(), used_));
      used_ = 0;
    }
    return ok;
  }

  void raw(const void *data, size_t size)
  {
    if (!ok || size > maximum_ - total) {
      ok = false;
      return;
    }
    total += size;
    const std::byte *source = static_cast<const std::byte *>(data);
    while (ok && size) {
      const size_t part = std::min(size, buffer_.size() - used_);
      std::memcpy(buffer_.data() + used_, source, part);
      source += part;
      used_ += part;
      size -= part;
      if (used_ == buffer_.size()) {
        flush();
      }
    }
  }

  void integer(uint32_t value, const uint8_t width)
  {
    std::array<std::byte, 4> data;
    for (uint8_t i = 0; i < width; i++) {
      data[i] = std::byte(value & 0xff);
      value >>= 8;
    }
    raw(data.data(), width);
  }

  void string(const char *value)
  {
    const size_t length = std::strlen(value);
    integer(uint32_t(length), 2);
    raw(value, length);
  }
};

void write_native(Writer &writer, const void *data, const uint8_t width)
{
  uint32_t value = 0;
  /* Width-specific copies work on both native endian orders. No floating operation is used. */
  if (width == 1) {
    uint8_t scalar;
    std::memcpy(&scalar, data, 1);
    value = scalar;
  }
  else if (width == 2) {
    uint16_t scalar;
    std::memcpy(&scalar, data, 2);
    value = scalar;
  }
  else {
    std::memcpy(&value, data, 4);
  }
  writer.integer(value, width);
}

void read_native(Reader &reader, void *data, const uint8_t width)
{
  const uint32_t value = reader.integer(width);
  if (width == 1) {
    const uint8_t scalar = uint8_t(value);
    std::memcpy(data, &scalar, 1);
  }
  else if (width == 2) {
    const uint16_t scalar = uint16_t(value);
    std::memcpy(data, &scalar, 2);
  }
  else {
    std::memcpy(data, &value, 4);
  }
}

size_t fixed_wire_bytes(const TypeInfo &info)
{
  size_t result = 0;
  for (const Field field : info.fields) {
    result += size_t(field.width) * field.count;
  }
  return result * info.repeats;
}

bool grid_bytes(const uint32_t level, size_t &bytes)
{
  if (level == 0 || level >= std::numeric_limits<size_t>::digits) {
    return false;
  }
  const size_t side = (size_t(1) << (level - 1)) + 1;
  size_t count;
  return product(side, side, count) && product(count, sizeof(float), bytes);
}

bool bitmap_bytes(const uint32_t count, size_t &bytes)
{
  const size_t words = size_t(count) / 32 + (count % 32 != 0);
  return product(words, sizeof(uint32_t), bytes);
}

struct Header {
  std::array<uint32_t, 4> counts{}; /* Vertex, edge, face, loop. */
  std::array<uint32_t, 3> selected{};
  uint32_t history = 0;
  uint32_t active_face = no_element;
  uint32_t flags = 0;
  short selectmode = 0;
  int shapenr = 0;
};

struct Domain {
  Array<CustomDataLayer, 0> layers;
  CustomDataExternal *external = nullptr;
  int totsize = 0;

  ~Domain()
  {
    MEM_SAFE_DELETE(external);
  }
};

bool header_read(Reader &reader, Header &header)
{
  const std::byte *identifier = reader.take(magic.size());
  if (!identifier || std::memcmp(identifier, magic.data(), magic.size()) != 0 ||
      reader.integer(4) != 1)
  {
    return false;
  }
  header.flags = reader.integer(4);
  for (uint32_t &count : header.counts) {
    count = reader.integer(4);
    if (count > INT_MAX) {
      return false;
    }
  }
  if (!bmesh::snapshot_io::counts_valid(header.counts) || (header.flags & ~3u)) {
    return false;
  }
  for (uint32_t &count : header.selected) {
    count = reader.integer(4);
  }
  header.history = reader.integer(4);
  header.active_face = reader.integer(4);
  read_native(reader, &header.selectmode, 2);
  read_native(reader, &header.shapenr, 4);
  return reader.ok && header.shapenr >= 0 && header.history <= INT_MAX &&
         header.selected[0] <= header.counts[0] && header.selected[1] <= header.counts[1] &&
         header.selected[2] <= header.counts[2] &&
         (header.active_face == no_element || header.active_face < header.counts[2]);
}

bool schema_active_indices_valid(const Span<CustomDataLayer> layers)
{
  /* Native getters add these indices to the first descriptor of each type. Validate each saved
   * value without performing that addition; preserving it must not permit an out-of-range access.
   */
  int64_t first = 0;
  while (first < layers.size()) {
    int64_t end = first + 1;
    while (end < layers.size() && layers[end].type == layers[first].type) {
      end++;
    }
    const int64_t count = end - first;
    for (int64_t i = first; i < end; i++) {
      const CustomDataLayer &layer = layers[i];
      if (layer.active < 0 || int64_t(layer.active) >= count || layer.active_rnd < 0 ||
          int64_t(layer.active_rnd) >= count)
      {
        return false;
      }
    }
    first = end;
  }
  return true;
}

bool schema_layout(Domain &domain)
{
  size_t offset = 0, maximum_alignment = 1;
  for (CustomDataLayer &layer : domain.layers) {
    const TypeInfo *info = type_info(layer.type);
    if (!info || int(info->size) != CustomData_sizeof(layer.type) ||
        !add(offset, info->alignment - 1))
    {
      return false;
    }
    offset &= ~(size_t(info->alignment) - 1);
    if (offset > INT_MAX) {
      return false;
    }
    layer.offset = int(offset);
    if (!add(offset, info->size) || offset > INT_MAX) {
      return false;
    }
    maximum_alignment = std::max(maximum_alignment, size_t(info->alignment));
  }
  if (!add(offset, maximum_alignment - 1)) {
    return false;
  }
  offset &= ~(maximum_alignment - 1);
  if (offset > INT_MAX) {
    return false;
  }
  domain.totsize = int(offset);
  return true;
}

bool schema_read(Reader &reader,
                 Domain &domain,
                 size_t &owned_bytes,
                 const size_t max_backing_bytes)
{
  const uint32_t count = reader.integer(4);
  const uint32_t external = reader.integer(1);
  size_t bytes;
  if (!reader.ok || count > INT_MAX - 5 || external > 1 ||
      !product(count, sizeof(CustomDataLayer), bytes) || !add_allocation(owned_bytes, bytes) ||
      owned_bytes > max_backing_bytes ||
      uint64_t(count) * 20 > uint64_t(reader.bytes.size()) - reader.position)
  {
    return false;
  }
  if (external) {
    if (!add_allocation(owned_bytes, sizeof(CustomDataExternal)) ||
        owned_bytes > max_backing_bytes)
    {
      return false;
    }
    domain.external = MEM_new<CustomDataExternal>(__func__);
    if (!reader.string(domain.external->filepath, sizeof(domain.external->filepath))) {
      return false;
    }
  }
  domain.layers.reinitialize(count);
  int previous_type = -1;
  for (CustomDataLayer &layer : domain.layers) {
    const TypeInfo *info = type_info_from_tag(uint16_t(reader.integer(2)));
    if (!info || int(info->type) < previous_type) {
      return false;
    }
    previous_type = int(info->type);
    layer.type = info->type;
    read_native(reader, &layer.flag, 4);
    read_native(reader, &layer.uid, 4);
    read_native(reader, &layer.active, 4);
    read_native(reader, &layer.active_rnd, 4);
    if (!reader.string(layer.name, sizeof(layer.name))) {
      return false;
    }
  }
  return schema_active_indices_valid(domain.layers) && schema_layout(domain);
}

bool skip_values(Reader &reader, const TypeInfo &info, const uint32_t count, size_t &dynamic_bytes)
{
  if (info.payload == Payload::Fixed) {
    size_t bytes;
    if (!product(count, fixed_wire_bytes(info), bytes)) {
      return false;
    }
    const std::byte *values = reader.take(bytes);
    if (!reader.ok) {
      return false;
    }
    if (info.type == CD_PROP_BOOL) {
      for (size_t i = 0; i < bytes; i++) {
        if (std::to_integer<uint8_t>(values[i]) > 1) {
          return false;
        }
      }
    }
    return true;
  }
  for (uint32_t i = 0; i < count; i++) {
    const uint32_t number = reader.integer(4);
    if (info.payload != Payload::GridMask) {
      reader.integer(4); /* Deform flag or displacement level. */
    }
    const uint32_t presence = reader.integer(1);
    size_t bytes = 0;
    if (!reader.ok) {
      return false;
    }
    if (info.payload == Payload::Deform) {
      if (number > INT_MAX || presence > 1 || (!presence && number) ||
          !product(number, sizeof(MDeformWeight), bytes))
      {
        return false;
      }
      if (presence) {
        reader.take(bytes);
        if (!add_allocation(dynamic_bytes, std::max(bytes, sizeof(MDeformWeight)))) {
          return false;
        }
      }
    }
    else if (info.payload == Payload::Displacements) {
      if (number > INT_MAX || presence > 3) {
        return false;
      }
      if (presence & 1) {
        if (!product(number, sizeof(float[3]), bytes) ||
            !add_allocation(dynamic_bytes, std::max(bytes, sizeof(float[3]))))
        {
          return false;
        }
        reader.take(bytes);
      }
      if (presence & 2) {
        if (!bitmap_bytes(number, bytes) ||
            !add_allocation(dynamic_bytes, std::max(bytes, sizeof(BLI_bitmap))))
        {
          return false;
        }
        reader.take(bytes);
      }
    }
    else {
      if (presence > 1 ||
          (presence && (!grid_bytes(number, bytes) || !add_allocation(dynamic_bytes, bytes))))
      {
        return false;
      }
      if (presence) {
        reader.take(bytes);
      }
    }
    if (!reader.ok) {
      return false;
    }
  }
  return true;
}

int history_domain(const uint32_t type)
{
  switch (type) {
    case BM_VERT:
      return 0;
    case BM_EDGE:
      return 1;
    case BM_FACE:
      return 2;
    default:
      return -1;
  }
}

bool geometry_read_validate(Reader &reader,
                            const Header &header,
                            const size_t scratch_limit,
                            uint32_t &max_face_size,
                            bool &all_triangles)
{
  size_t vertex_bytes;
  if (!product(header.counts[0], size_t(25), vertex_bytes)) {
    return false;
  }
  reader.take(vertex_bytes);
  const size_t edge_offset = reader.position;
  for (uint32_t i = 0; reader.ok && i < header.counts[1]; i++) {
    const uint32_t a = reader.integer(4), b = reader.integer(4);
    reader.integer(1);
    if (a >= header.counts[0] || b >= header.counts[0] || a == b) {
      return false;
    }
  }
  Array<uint32_t, 0> ordinals;
  size_t loop_count = 0;
  all_triangles = true;
  max_face_size = 0;
  for (uint32_t i = 0; reader.ok && i < header.counts[2]; i++) {
    const uint32_t length = reader.integer(4);
    reader.take(15); /* Normal, material, hflag. */
    size_t scratch;
    if (length < 2 || length > header.counts[3] || !add(loop_count, length) ||
        loop_count > header.counts[3] || !product(length, sizeof(uint32_t) * 2, scratch) ||
        scratch > scratch_limit)
    {
      return false;
    }
    const size_t corners_offset = reader.position;
    size_t corner_bytes;
    if (!product(length, size_t(9), corner_bytes) || !reader.take(corner_bytes)) {
      return false;
    }
    if (length > max_face_size) {
      max_face_size = length;
      ordinals.reinitialize(length);
    }
    all_triangles &= length == 3;
    for (uint32_t j = 0; j < length; j++) {
      Reader corner(reader.bytes);
      corner.position = corners_offset + size_t(j) * 9;
      const uint32_t vertex = corner.integer(4), edge = corner.integer(4);
      Reader next(reader.bytes);
      next.position = corners_offset + size_t((j + 1) % length) * 9;
      const uint32_t next_vertex = next.integer(4);
      if (vertex >= header.counts[0] || edge >= header.counts[1]) {
        return false;
      }
      Reader endpoints(reader.bytes);
      endpoints.position = edge_offset + size_t(edge) * 9;
      const uint32_t a = endpoints.integer(4), b = endpoints.integer(4);
      if (!((a == vertex && b == next_vertex) || (b == vertex && a == next_vertex))) {
        return false;
      }
      ordinals[j] = vertex;
    }
    std::sort(ordinals.begin(), ordinals.begin() + length);
    if (std::adjacent_find(ordinals.begin(), ordinals.begin() + length) !=
        ordinals.begin() + length)
    {
      return false;
    }
    for (uint32_t j = 0; j < length; j++) {
      Reader corner(reader.bytes);
      corner.position = corners_offset + size_t(j) * 9 + 4;
      ordinals[j] = corner.integer(4);
    }
    std::sort(ordinals.begin(), ordinals.begin() + length);
    if (std::adjacent_find(ordinals.begin(), ordinals.begin() + length) !=
        ordinals.begin() + length)
    {
      return false;
    }
  }
  if (!reader.ok || loop_count != header.counts[3]) {
    return false;
  }
  for (uint32_t i = 0; reader.ok && i < header.history; i++) {
    const int domain = history_domain(reader.integer(1));
    const uint32_t index = reader.integer(4);
    if (domain == -1 || index >= header.counts[domain]) {
      return false;
    }
  }
  return reader.ok;
}

struct MeshDeleter {
  void operator()(BMesh *mesh) const
  {
    BM_mesh_free(mesh);
  }
};
using OwnedMesh = std::unique_ptr<BMesh, MeshDeleter>;

}  // namespace

struct BMGeometryDecodePlan {
  Array<std::byte, 0> bytes;
  BMGeometryCodecLimits limits;
  BMGeometryDecodeInfo info{};
  Header header;
  std::array<Domain, 4> domains;
  size_t geometry_offset = 0;
  size_t dynamic_bytes = 0;
  uint32_t max_face_size = 0;
};

namespace {

bool allocation_plan(BMGeometryDecodePlan &plan)
{
  const Header &header = plan.header;
  const bool toolflags = header.flags & 1;
  const std::array<size_t, 4> element_sizes = {toolflags ? sizeof(BMVert_OFlag) : sizeof(BMVert),
                                               toolflags ? sizeof(BMEdge_OFlag) : sizeof(BMEdge),
                                               toolflags ? sizeof(BMFace_OFlag) : sizeof(BMFace),
                                               sizeof(BMLoop)};
  const std::array<uint, 4> chunks = {uint(bm_mesh_chunksize_default.totvert),
                                      uint(bm_mesh_chunksize_default.totedge),
                                      uint(bm_mesh_chunksize_default.totface),
                                      uint(bm_mesh_chunksize_default.totloop)};
  size_t backing = 0, scratch = 0, metadata_scratch = 0;
  if (!add_allocation(backing, sizeof(BMesh)) || !add(backing, plan.dynamic_bytes)) {
    return false;
  }
  for (int domain = 0; domain < 4; domain++) {
    const uint32_t count = header.counts[domain];
    const Domain &schema = plan.domains[domain];
    size_t bytes;
    if (!BLI_mempool_create_size_bound(uint(element_sizes[domain]),
                                       count,
                                       count,
                                       chunks[domain],
                                       domain == 3 ? BLI_MEMPOOL_NOP : BLI_MEMPOOL_ALLOW_ITER,
                                       &bytes) ||
        !add(backing, bytes) || !product(count, sizeof(void *), bytes) ||
        !add_allocation(scratch, bytes))
    {
      return false;
    }
    if (!schema.layers.is_empty()) {
      size_t logical_blocks;
      if (!product(count, size_t(schema.totsize), logical_blocks) ||
          !BLI_mempool_create_size_bound(
              uint(schema.totsize), count, count, chunks[domain], BLI_MEMPOOL_NOP, &bytes) ||
          !add(backing, bytes))
      {
        return false;
      }
      /* Native CustomData grows descriptors in groups of five. Include the previous buffer in
       * the scratch peak; the temporary NOCOPY-cleared view has an exact-size Array. */
      const size_t capacity = (size_t(schema.layers.size()) + 4) / 5 * 5;
      if (!product(capacity, sizeof(CustomDataLayer), bytes) || !add_allocation(backing, bytes)) {
        return false;
      }
      size_t temporary = 0;
      if (!add_allocation(temporary, bytes) || !add_allocation(temporary, bytes) ||
          !product(size_t(schema.layers.size()), sizeof(CustomDataLayer), bytes) ||
          !add_allocation(temporary, bytes) ||
          !BLI_mempool_create_size_bound(
              uint(schema.totsize), 0, 0, chunks[domain], BLI_MEMPOOL_NOP, &bytes) ||
          !add(temporary, bytes) ||
          (schema.external && !add_allocation(temporary, sizeof(CustomDataExternal))))
      {
        return false;
      }
      metadata_scratch = std::max(metadata_scratch, temporary);
    }
    if (schema.external && !add_allocation(backing, sizeof(CustomDataExternal))) {
      return false;
    }
    if (schema.external) {
      metadata_scratch = std::max(metadata_scratch, sizeof(CustomDataExternal));
    }
  }
#ifdef USE_BMESH_HOLES
  /* This codec has one boundary per face, matching the existing snapshot geometry contract. */
  size_t hole_bytes;
  if (!BLI_mempool_create_size_bound(
          sizeof(BMLoopList), 512, header.counts[2], 512, BLI_MEMPOOL_NOP, &hole_bytes) ||
      !add(backing, hole_bytes))
  {
    return false;
  }
#endif
  size_t selection_node_bytes, history_bytes, face_bytes;
  if (!rounded4(sizeof(BMEditSelection), selection_node_bytes) ||
      !product(header.history, selection_node_bytes, history_bytes) ||
      !add(backing, history_bytes) ||
      !product(plan.max_face_size, sizeof(void *) * 2, face_bytes) ||
      !add_allocation(scratch, face_bytes) || !add(scratch, metadata_scratch))
  {
    return false;
  }
  size_t total = plan.info.owned_input_and_plan_bytes;
  if (!add(total, backing) || total > plan.limits.max_backing_bytes ||
      scratch > plan.limits.max_scratch_bytes)
  {
    return false;
  }
  plan.info.decoded_backing_bytes_bound = backing;
  plan.info.decode_scratch_bytes_bound = scratch;
  return true;
}

bool schema_install(const Domain &source,
                    CustomData &destination,
                    const int count,
                    const char domain)
{
  Array<CustomDataLayer, 0> descriptors(source.layers);
  for (CustomDataLayer &layer : descriptors) {
    layer.flag &= ~CD_FLAG_NOCOPY;
  }
  CustomData view{};
  view.layers = descriptors.data();
  view.totlayer = view.maxlayer = int(descriptors.size());
  view.totsize = source.totsize;
  view.external = source.external;
  CustomData_update_typemap(&view);
  CustomData_init_layout_from(&view, &destination, CD_MASK_ALL, CD_SET_DEFAULT, 0);
  /* Nonempty layouts must have a pool even if the subsequent exact-schema check refuses. */
  CustomData_bmesh_init_pool(&destination, count, domain);
  if (destination.totlayer != source.layers.size() || destination.totsize != source.totsize) {
    return false;
  }
  for (const int64_t i : source.layers.index_range()) {
    const CustomDataLayer &a = source.layers[i];
    CustomDataLayer &b = destination.layers[i];
    if (a.type != b.type || a.offset != b.offset || std::strcmp(a.name, b.name) != 0) {
      return false;
    }
    b.flag = a.flag;
    b.uid = a.uid;
    b.active = a.active;
    b.active_rnd = a.active_rnd;
  }
  return bool(destination.external) == bool(source.external) &&
         (!source.external ||
          std::strcmp(destination.external->filepath, source.external->filepath) == 0);
}

void zero_block(CustomData &data, BMHeader &header)
{
  CustomData_bmesh_alloc_block(&data, &header.data);
  if (data.totsize) {
    std::memset(header.data, 0, size_t(data.totsize));
  }
}

template<typename T> T *owned_array(const size_t count)
{
  T *result = MEM_new_array_uninitialized<T>(std::max(size_t(1), count), __func__);
  if (result && !count) {
    std::memset(result, 0, sizeof(T));
  }
  return result;
}

bool value_read(Reader &reader, const TypeInfo &info, void *value)
{
  if (info.payload == Payload::Fixed) {
    for (int repeat = 0; repeat < info.repeats; repeat++) {
      for (const Field field : info.fields) {
        for (int i = 0; i < field.count; i++) {
          read_native(reader,
                      static_cast<char *>(value) + repeat * info.stride + field.offset +
                          i * field.width,
                      field.width);
        }
      }
    }
    return reader.ok;
  }
  if (info.payload == Payload::Deform) {
    MDeformVert &data = *static_cast<MDeformVert *>(value);
    data.totweight = int(reader.integer(4));
    read_native(reader, &data.flag, 4);
    if (reader.integer(1)) {
      data.dw = owned_array<MDeformWeight>(size_t(data.totweight));
      if (!data.dw) {
        return false;
      }
      for (int i = 0; i < data.totweight; i++) {
        read_native(reader, &data.dw[i].def_nr, 4);
        read_native(reader, &data.dw[i].weight, 4);
      }
    }
  }
  else if (info.payload == Payload::Displacements) {
    MDisps &data = *static_cast<MDisps *>(value);
    data.totdisp = int(reader.integer(4));
    read_native(reader, &data.level, 4);
    const uint32_t presence = reader.integer(1);
    if (presence & 1) {
      data.disps = owned_array<float[3]>(size_t(data.totdisp));
      if (!data.disps) {
        return false;
      }
      for (int i = 0; i < data.totdisp; i++) {
        for (int axis = 0; axis < 3; axis++) {
          read_native(reader, &data.disps[i][axis], 4);
        }
      }
    }
    if (presence & 2) {
      const size_t words = size_t(data.totdisp) / 32 + (data.totdisp % 32 != 0);
      data.hidden = owned_array<BLI_bitmap>(words);
      if (!data.hidden) {
        return false;
      }
      for (size_t i = 0; i < words; i++) {
        read_native(reader, &data.hidden[i], 4);
      }
    }
  }
  else {
    GridPaintMask &data = *static_cast<GridPaintMask *>(value);
    data.level = reader.integer(4);
    if (reader.integer(1)) {
      size_t bytes;
      if (!grid_bytes(data.level, bytes)) {
        return false;
      }
      data.data = owned_array<float>(bytes / sizeof(float));
      if (!data.data) {
        return false;
      }
      for (size_t i = 0; i < bytes / sizeof(float); i++) {
        read_native(reader, &data.data[i], 4);
      }
    }
  }
  return reader.ok;
}

template<typename Fn> void foreach_element(const BMesh &mesh, const int domain, Fn &&fn)
{
  BMIter iterator;
  BMesh *bm = const_cast<BMesh *>(&mesh); /* Native iteration only changes its local iterator. */
  if (domain == 0) {
    const BMVert *vert;
    BM_ITER_MESH (vert, &iterator, bm, BM_VERTS_OF_MESH) {
      fn(vert->head);
    }
  }
  else if (domain == 1) {
    const BMEdge *edge;
    BM_ITER_MESH (edge, &iterator, bm, BM_EDGES_OF_MESH) {
      fn(edge->head);
    }
  }
  else {
    const BMFace *face;
    BM_ITER_MESH (face, &iterator, bm, BM_FACES_OF_MESH) {
      if (domain == 2) {
        fn(face->head);
      }
      else {
        const BMLoop *loop = BM_FACE_FIRST_LOOP(face);
        for (int i = 0; i < face->len; i++, loop = loop->next) {
          fn(loop->head);
        }
      }
    }
  }
}

bool mesh_backing_bytes(const BMesh &mesh, size_t &bytes)
{
  bytes = MEM_allocN_len(&mesh);
  for (const BLI_mempool *pool : {mesh.vpool,
                                  mesh.epool,
                                  mesh.fpool,
                                  mesh.lpool,
                                  mesh.vtoolflagpool,
                                  mesh.etoolflagpool,
                                  mesh.ftoolflagpool})
  {
    if (!add(bytes, BLI_mempool_allocated_bytes(pool))) {
      return false;
    }
  }
#ifdef USE_BMESH_HOLES
  if (!add(bytes, BLI_mempool_allocated_bytes(mesh.looplistpool))) {
    return false;
  }
#endif
  const std::array<const CustomData *, 4> domains = {
      &mesh.vdata, &mesh.edata, &mesh.pdata, &mesh.ldata};
  for (int domain = 0; domain < 4; domain++) {
    const CustomData &data = *domains[domain];
    if (!add(bytes, BLI_mempool_allocated_bytes(data.pool)) ||
        (data.layers && !add(bytes, MEM_allocN_len(data.layers))) ||
        (data.external && !add(bytes, MEM_allocN_len(data.external))))
    {
      return false;
    }
    bool ok = true;
    for (const CustomDataLayer &layer : Span(data.layers, data.totlayer)) {
      const TypeInfo &info = *type_info(layer.type);
      if (info.payload == Payload::Fixed) {
        continue;
      }
      foreach_element(mesh, domain, [&](const BMHeader &header) {
        const char *value = static_cast<const char *>(header.data) + layer.offset;
        if (info.payload == Payload::Deform) {
          const auto &record = *reinterpret_cast<const MDeformVert *>(value);
          ok &= !record.dw || add(bytes, MEM_allocN_len(record.dw));
        }
        else if (info.payload == Payload::Displacements) {
          const auto &record = *reinterpret_cast<const MDisps *>(value);
          ok &= !record.disps || add(bytes, MEM_allocN_len(record.disps));
          ok &= !record.hidden || add(bytes, MEM_allocN_len(record.hidden));
        }
        else if (info.payload == Payload::GridMask) {
          const auto &record = *reinterpret_cast<const GridPaintMask *>(value);
          ok &= !record.data || add(bytes, MEM_allocN_len(record.data));
        }
      });
    }
    if (!ok) {
      return false;
    }
  }
  for (const BMEditSelection &selection : mesh.selected) {
    if (!add(bytes, MEM_allocN_len(&selection))) {
      return false;
    }
  }
  return true;
}

bool source_value_bytes(const TypeInfo &info, const void *value, size_t &wire_bytes)
{
  if (info.payload == Payload::Fixed) {
    if (info.type == CD_PROP_BOOL && *static_cast<const uint8_t *>(value) > 1) {
      return false;
    }
    return add(wire_bytes, fixed_wire_bytes(info));
  }
  const auto stored = [](const void *data, const size_t bytes) {
    return data && MEM_allocN_len(data) >= bytes;
  };
  size_t bytes;
  if (info.payload == Payload::Deform) {
    const auto &data = *static_cast<const MDeformVert *>(value);
    if (data.totweight < 0 || !add(wire_bytes, 9) ||
        !product(size_t(data.totweight), sizeof(MDeformWeight), bytes) ||
        (data.totweight && !data.dw))
    {
      return false;
    }
    return !data.dw || (stored(data.dw, bytes) && add(wire_bytes, bytes));
  }
  if (info.payload == Payload::Displacements) {
    const auto &data = *static_cast<const MDisps *>(value);
    if (data.totdisp < 0 || !add(wire_bytes, 9)) {
      return false;
    }
    if (data.disps && (!product(size_t(data.totdisp), sizeof(float[3]), bytes) ||
                       !stored(data.disps, bytes) || !add(wire_bytes, bytes)))
    {
      return false;
    }
    if (data.hidden && (!bitmap_bytes(uint32_t(data.totdisp), bytes) ||
                        !stored(data.hidden, bytes) || !add(wire_bytes, bytes)))
    {
      return false;
    }
    return true;
  }
  const auto &data = *static_cast<const GridPaintMask *>(value);
  return add(wire_bytes, 5) &&
         (!data.data ||
          (grid_bytes(data.level, bytes) && stored(data.data, bytes) && add(wire_bytes, bytes)));
}

void value_write(Writer &writer, const TypeInfo &info, const void *value)
{
  if (info.payload == Payload::Fixed) {
    for (int repeat = 0; repeat < info.repeats; repeat++) {
      for (const Field field : info.fields) {
        for (int i = 0; i < field.count; i++) {
          write_native(writer,
                       static_cast<const char *>(value) + repeat * info.stride + field.offset +
                           i * field.width,
                       field.width);
        }
      }
    }
  }
  else if (info.payload == Payload::Deform) {
    const auto &data = *static_cast<const MDeformVert *>(value);
    writer.integer(uint32_t(data.totweight), 4);
    write_native(writer, &data.flag, 4);
    writer.integer(bool(data.dw), 1);
    if (data.dw) {
      for (int i = 0; i < data.totweight; i++) {
        write_native(writer, &data.dw[i].def_nr, 4);
        write_native(writer, &data.dw[i].weight, 4);
      }
    }
  }
  else if (info.payload == Payload::Displacements) {
    const auto &data = *static_cast<const MDisps *>(value);
    writer.integer(uint32_t(data.totdisp), 4);
    write_native(writer, &data.level, 4);
    writer.integer(uint32_t(bool(data.disps)) | (uint32_t(bool(data.hidden)) << 1), 1);
    if (data.disps) {
      for (int i = 0; i < data.totdisp; i++) {
        for (int axis = 0; axis < 3; axis++) {
          write_native(writer, &data.disps[i][axis], 4);
        }
      }
    }
    if (data.hidden) {
      const size_t words = size_t(data.totdisp) / 32 + (data.totdisp % 32 != 0);
      for (size_t i = 0; i < words; i++) {
        write_native(writer, &data.hidden[i], 4);
      }
    }
  }
  else {
    const auto &data = *static_cast<const GridPaintMask *>(value);
    writer.integer(data.level, 4);
    writer.integer(bool(data.data), 1);
    if (data.data) {
      size_t bytes = 0;
      const bool valid = grid_bytes(data.level, bytes);
      BLI_assert(valid);
      UNUSED_VARS_NDEBUG(valid);
      for (size_t i = 0; i < bytes / sizeof(float); i++) {
        write_native(writer, &data.data[i], 4);
      }
    }
  }
}

using OrdinalMap = Map<const BMHeader *, uint32_t, 0>;

bool map_scratch_bytes(const uint32_t count, const OrdinalMap &map, size_t &bytes)
{
  if (!count) {
    return true;
  }
  /* Map's current default load factor is 1/2. Reserve once from an empty map, avoiding rehash
   * peaks. Check before handing a native signed slot count to the container. */
  const uint64_t required = uint64_t(count) * 2;
  uint64_t capacity = 1;
  while (capacity < required) {
    capacity <<= 1;
  }
  size_t storage;
  return capacity <= INT64_MAX && capacity <= SIZE_MAX &&
         product(size_t(capacity), size_t(map.size_per_element()), storage) &&
         add_allocation(bytes, storage);
}

bool source_header(const BMesh &mesh, Header &header)
{
  if (mesh.totvert < 0 || mesh.totedge < 0 || mesh.totface < 0 || mesh.totloop < 0 ||
      int64_t(mesh.totvert) + mesh.totface > INT_MAX || mesh.totvertsel < 0 ||
      mesh.totedgesel < 0 || mesh.totfacesel < 0 || mesh.totvertsel > mesh.totvert ||
      mesh.totedgesel > mesh.totedge || mesh.totfacesel > mesh.totface || mesh.shapenr < 0 ||
      mesh.toolflag_index || !BLI_listbase_is_empty(&mesh.errorstack))
  {
    return false;
  }
  header.counts = {uint32_t(mesh.totvert),
                   uint32_t(mesh.totedge),
                   uint32_t(mesh.totface),
                   uint32_t(mesh.totloop)};
  header.selected = {
      uint32_t(mesh.totvertsel), uint32_t(mesh.totedgesel), uint32_t(mesh.totfacesel)};
  header.flags = uint32_t(mesh.use_toolflags) | (uint32_t(mesh.uv_select_sync_valid) << 1);
  header.selectmode = mesh.selectmode;
  header.shapenr = mesh.shapenr;
  for (const BMEditSelection &selection : mesh.selected) {
    UNUSED_VARS(selection);
    if (header.history == INT_MAX) {
      return false;
    }
    header.history++;
  }
  return true;
}

}  // namespace

namespace bmesh::snapshot_io {

bool counts_valid(const std::array<uint32_t, 4> &counts)
{
  for (const uint32_t count : counts) {
    if (count > INT_MAX) {
      return false;
    }
  }
  return uint64_t(counts[0]) + counts[2] <= INT_MAX;
}

bool encode_mesh(const BMesh &mesh,
                 const BMGeometryCodecLimits &limits,
                 FunctionRef<bool(Span<std::byte>)> append,
                 const char **r_error)
{
  if (r_error) {
    *r_error = nullptr;
  }
  static_assert(sizeof(float) == 4 && sizeof(int) == 4 && sizeof(short) == 2);
  static_assert(sizeof(MDeformWeight) == 8 && sizeof(BLI_bitmap) == 4);
  Header header;
  if (!source_header(mesh, header)) {
    return fail(r_error, "The BMesh snapshot cannot supply valid native codec counts");
  }
  const std::array<const CustomData *, 4> domains = {
      &mesh.vdata, &mesh.edata, &mesh.pdata, &mesh.ldata};
  size_t wire_bytes = 58, scratch_bytes = output_chunk_size;
  const std::array<size_t, 4> geometry_record_bytes = {25, 9, 19, 9};
  for (int domain = 0; domain < 4; domain++) {
    const CustomData &data = *domains[domain];
    size_t bytes;
    if (data.totlayer < 0 || data.totsize < 0 || (data.totlayer && !data.layers) ||
        !product(header.counts[domain], geometry_record_bytes[domain], bytes) ||
        !add(wire_bytes, bytes) || !add(wire_bytes, 5))
    {
      return fail(r_error, "The BMesh snapshot codec schema size is invalid");
    }
    if (!schema_active_indices_valid(Span(data.layers, data.totlayer))) {
      return fail(r_error, "The BMesh snapshot active or render layer index is invalid");
    }
    if (data.external) {
      if (!std::memchr(data.external->filepath, 0, sizeof(data.external->filepath)) ||
          !add(wire_bytes, 2 + std::strlen(data.external->filepath)))
      {
        return fail(r_error, "The BMesh snapshot external metadata is invalid");
      }
    }
    int previous_type = -1;
    for (const CustomDataLayer &layer : Span(data.layers, data.totlayer)) {
      const TypeInfo *info = type_info(layer.type);
      if (!info || int(layer.type) < previous_type ||
          int(info->size) != CustomData_sizeof(layer.type) || layer.offset < 0 ||
          layer.offset > data.totsize || info->size > data.totsize - layer.offset || layer.data ||
          layer.sharing_info || !std::memchr(layer.name, 0, sizeof(layer.name)) ||
          !add(wire_bytes, 20 + std::strlen(layer.name)))
      {
        return fail(r_error, "The BMesh snapshot contains an unsupported codec schema");
      }
      previous_type = int(layer.type);
      bool valid = true;
      foreach_element(mesh, domain, [&](const BMHeader &element) {
        valid = valid && element.data &&
                source_value_bytes(
                    *info, static_cast<const char *>(element.data) + layer.offset, wire_bytes);
      });
      if (!valid) {
        return fail(r_error, "The BMesh snapshot contains invalid owned attribute storage");
      }
    }
  }
  size_t history_bytes;
  if (!product(header.history, size_t(5), history_bytes) || !add(wire_bytes, history_bytes) ||
      uint64_t(wire_bytes) > limits.max_section_bytes)
  {
    return fail(r_error, "The BMesh snapshot exceeds the encoded section budget");
  }
  std::array<OrdinalMap, 3> maps;
  for (int domain = 0; domain < 3; domain++) {
    if (!map_scratch_bytes(header.counts[domain], maps[domain], scratch_bytes)) {
      return fail(r_error, "The BMesh snapshot ordinal map size cannot be represented");
    }
  }
  uint32_t maximum_face = 0;
  bool valid_faces = true;
  foreach_element(mesh, 2, [&](const BMHeader &element) {
    const BMFace &face = *reinterpret_cast<const BMFace *>(&element);
    valid_faces &= face.len >= 2 && face.len <= mesh.totloop && BM_FACE_FIRST_LOOP(&face);
    maximum_face = std::max(maximum_face, uint32_t(std::max(face.len, 0)));
  });
  size_t face_scratch;
  if (!valid_faces || !product(maximum_face, sizeof(uint32_t), face_scratch) ||
      !add_allocation(scratch_bytes, face_scratch) || scratch_bytes > limits.max_scratch_bytes)
  {
    return fail(r_error, "The BMesh snapshot face or encode scratch budget is invalid");
  }
  for (int domain = 0; domain < 3; domain++) {
    maps[domain].reserve(header.counts[domain]);
    uint32_t ordinal = 0;
    foreach_element(
        mesh, domain, [&](const BMHeader &element) { maps[domain].add_new(&element, ordinal++); });
    if (ordinal != header.counts[domain]) {
      return fail(r_error, "The BMesh snapshot live element counts changed");
    }
  }
  header.active_face = mesh.act_face ? maps[2].lookup_default(&mesh.act_face->head, no_element) :
                                       no_element;
  if (mesh.act_face && header.active_face == no_element) {
    return fail(r_error, "The BMesh snapshot active face is not live");
  }
  for (const BMEditSelection &selection : mesh.selected) {
    const int domain = history_domain(selection.htype);
    if (domain < 0 || !selection.ele || !maps[domain].contains(&selection.ele->head)) {
      return fail(r_error, "The BMesh snapshot selection history is not live");
    }
  }
  bool topology_valid = true;
  foreach_element(mesh, 1, [&](const BMHeader &element) {
    const BMEdge &edge = *reinterpret_cast<const BMEdge *>(&element);
    topology_valid &= edge.v1 && edge.v2 && edge.v1 != edge.v2 &&
                      maps[0].contains(&edge.v1->head) && maps[0].contains(&edge.v2->head);
  });
  Array<uint32_t, 0> face_ordinals(maximum_face);
  size_t loops = 0;
  foreach_element(mesh, 2, [&](const BMHeader &element) {
    const BMFace &face = *reinterpret_cast<const BMFace *>(&element);
    const BMLoop *loop = BM_FACE_FIRST_LOOP(&face);
    for (int pass = 0; topology_valid && pass < 2; pass++) {
      for (int j = 0; j < face.len; j++, loop = loop->next) {
        if (!loop || !loop->v || !loop->e || !loop->next || loop->f != &face ||
            !maps[0].contains(&loop->v->head) || !maps[1].contains(&loop->e->head) ||
            !((loop->e->v1 == loop->v && loop->e->v2 == loop->next->v) ||
              (loop->e->v2 == loop->v && loop->e->v1 == loop->next->v)))
        {
          topology_valid = false;
          break;
        }
        face_ordinals[j] = pass == 0 ? maps[0].lookup(&loop->v->head) :
                                       maps[1].lookup(&loop->e->head);
      }
      if (loop != BM_FACE_FIRST_LOOP(&face)) {
        topology_valid = false;
      }
      if (topology_valid) {
        std::sort(face_ordinals.begin(), face_ordinals.begin() + face.len);
        topology_valid &= std::adjacent_find(face_ordinals.begin(),
                                             face_ordinals.begin() + face.len) ==
                          face_ordinals.begin() + face.len;
      }
    }
    topology_valid &= add(loops, size_t(face.len));
  });
  if (!topology_valid || loops != header.counts[3]) {
    return fail(r_error, "The BMesh snapshot oriented topology is inconsistent");
  }

  Writer writer(append, limits.max_section_bytes);
  writer.raw(magic.data(), magic.size());
  writer.integer(1, 4);
  writer.integer(header.flags, 4);
  for (const uint32_t count : header.counts) {
    writer.integer(count, 4);
  }
  for (const uint32_t count : header.selected) {
    writer.integer(count, 4);
  }
  writer.integer(header.history, 4);
  writer.integer(header.active_face, 4);
  write_native(writer, &header.selectmode, 2);
  write_native(writer, &header.shapenr, 4);
  for (const CustomData *data : domains) {
    writer.integer(uint32_t(data->totlayer), 4);
    writer.integer(bool(data->external), 1);
    if (data->external) {
      writer.string(data->external->filepath);
    }
    for (const CustomDataLayer &layer : Span(data->layers, data->totlayer)) {
      writer.integer(type_info(layer.type)->tag, 2);
      write_native(writer, &layer.flag, 4);
      write_native(writer, &layer.uid, 4);
      write_native(writer, &layer.active, 4);
      write_native(writer, &layer.active_rnd, 4);
      writer.string(layer.name);
    }
  }
  foreach_element(mesh, 0, [&](const BMHeader &element) {
    const BMVert &vert = *reinterpret_cast<const BMVert *>(&element);
    for (const float &value : vert.co) {
      write_native(writer, &value, 4);
    }
    for (const float &value : vert.no) {
      write_native(writer, &value, 4);
    }
    write_native(writer, &element.hflag, 1);
  });
  foreach_element(mesh, 1, [&](const BMHeader &element) {
    const BMEdge &edge = *reinterpret_cast<const BMEdge *>(&element);
    writer.integer(maps[0].lookup(&edge.v1->head), 4);
    writer.integer(maps[0].lookup(&edge.v2->head), 4);
    write_native(writer, &element.hflag, 1);
  });
  foreach_element(mesh, 2, [&](const BMHeader &element) {
    const BMFace &face = *reinterpret_cast<const BMFace *>(&element);
    writer.integer(uint32_t(face.len), 4);
    for (const float &value : face.no) {
      write_native(writer, &value, 4);
    }
    write_native(writer, &face.mat_nr, 2);
    write_native(writer, &element.hflag, 1);
    const BMLoop *loop = BM_FACE_FIRST_LOOP(&face);
    for (int j = 0; j < face.len; j++, loop = loop->next) {
      writer.integer(maps[0].lookup(&loop->v->head), 4);
      writer.integer(maps[1].lookup(&loop->e->head), 4);
      write_native(writer, &loop->head.hflag, 1);
    }
  });
  for (const BMEditSelection &selection : mesh.selected) {
    writer.integer(selection.htype, 1);
    writer.integer(maps[history_domain(selection.htype)].lookup(&selection.ele->head), 4);
  }
  for (int domain = 0; domain < 4; domain++) {
    for (const CustomDataLayer &layer : Span(domains[domain]->layers, domains[domain]->totlayer)) {
      const TypeInfo &info = *type_info(layer.type);
      foreach_element(mesh, domain, [&](const BMHeader &element) {
        value_write(writer, info, static_cast<const char *>(element.data) + layer.offset);
      });
    }
  }
  if (!writer.flush()) {
    return fail(r_error, "The BMesh section output sink failed");
  }
  if (writer.total != wire_bytes) {
    return fail(r_error, "The BMesh encoded section differs from its planned size");
  }
  return true;
}

}  // namespace bmesh::snapshot_io

BMGeometryDecodePlan *BM_geometry_snapshot_decode_prepare(Array<std::byte, 0> &&owned_section,
                                                          const BMGeometryCodecLimits &limits,
                                                          const char **r_error)
{
  if (r_error) {
    *r_error = nullptr;
  }
  /* Consume even on failure. The parent has already bounded its read allocation. */
  Array<std::byte, 0> bytes(std::move(owned_section));
  size_t owned_bytes = 0;
  if (uint64_t(bytes.size()) > limits.max_section_bytes ||
      !add_allocation(owned_bytes, sizeof(BMGeometryDecodePlan)) ||
      (bytes.size() && !add(owned_bytes, MEM_allocN_len(bytes.data()))) ||
      owned_bytes > limits.max_backing_bytes)
  {
    fail(r_error, "The BMesh section input exceeds the codec backing budget");
    return nullptr;
  }
  auto delete_plan = [](BMGeometryDecodePlan *plan) { MEM_delete(plan); };
  std::unique_ptr<BMGeometryDecodePlan, decltype(delete_plan)> plan(
      MEM_new<BMGeometryDecodePlan>(__func__), delete_plan);
  plan->bytes = std::move(bytes);
  plan->limits = limits;
  Reader reader(plan->bytes);
  if (!header_read(reader, plan->header)) {
    fail(r_error, "The BMesh section header or native element counts are invalid");
    return nullptr;
  }
  for (Domain &domain : plan->domains) {
    if (!schema_read(reader, domain, owned_bytes, limits.max_backing_bytes)) {
      fail(r_error, "The BMesh section schema or aligned native layout is invalid");
      return nullptr;
    }
  }
  plan->geometry_offset = reader.position;
  if (!geometry_read_validate(reader,
                              plan->header,
                              limits.max_scratch_bytes,
                              plan->max_face_size,
                              plan->info.all_faces_triangular))
  {
    fail(r_error, "The BMesh section topology, selection or validation budget is invalid");
    return nullptr;
  }
  for (int domain = 0; domain < 4; domain++) {
    for (const CustomDataLayer &layer : plan->domains[domain].layers) {
      if (!skip_values(
              reader, *type_info(layer.type), plan->header.counts[domain], plan->dynamic_bytes))
      {
        fail(r_error, "The BMesh section attribute payload is invalid or truncated");
        return nullptr;
      }
    }
  }
  if (!reader.ok || reader.position != size_t(plan->bytes.size())) {
    fail(r_error, "The BMesh section has missing or trailing payload bytes");
    return nullptr;
  }
  plan->info.section_bytes = uint64_t(plan->bytes.size());
  plan->info.verts_num = plan->header.counts[0];
  plan->info.edges_num = plan->header.counts[1];
  plan->info.faces_num = plan->header.counts[2];
  plan->info.loops_num = plan->header.counts[3];
  plan->info.owned_input_and_plan_bytes = owned_bytes;
  if (!allocation_plan(*plan)) {
    fail(r_error, "The BMesh native backing or scratch allocation plan is not admissible");
    return nullptr;
  }
  /* Prove native layout reconstruction before any bulk geometry allocation. This bounded
   * metadata-only pass catches duplicate/name policies that a wire-size check cannot prove. */
  constexpr std::array<char, 4> domain_types = {BM_VERT, BM_EDGE, BM_FACE, BM_LOOP};
  for (int domain = 0; domain < 4; domain++) {
    CustomData candidate{};
    const bool exact = schema_install(plan->domains[domain], candidate, 0, domain_types[domain]);
    if (candidate.pool) {
      BLI_mempool_destroy(candidate.pool);
      candidate.pool = nullptr;
    }
    CustomData_free(&candidate);
    if (!exact) {
      fail(r_error, "The BMesh section native schema would be dropped or renamed");
      return nullptr;
    }
  }
  return plan.release();
}

const BMGeometryDecodeInfo &BM_geometry_snapshot_decode_info(const BMGeometryDecodePlan &plan)
{
  return plan.info;
}

void BM_geometry_snapshot_decode_plan_free(BMGeometryDecodePlan *plan)
{
  MEM_SAFE_DELETE(plan);
}

namespace {

#ifdef WITH_GTESTS
struct DecodeTestFault {
  BMGeometryDecodeTestPhase phase;
  uint32_t index;
};

bool test_fault(const DecodeTestFault *fault,
                const BMGeometryDecodeTestPhase phase,
                const uint32_t index,
                const char **r_error)
{
  if (fault && fault->phase == phase && fault->index == index) {
    fail(r_error, "Injected local BMesh codec construction failure");
    return true;
  }
  return false;
}
#endif

BMesh *decode_mesh(const BMGeometryDecodePlan &plan,
                   size_t *r_owned_backing_bytes,
                   const char **r_error
#ifdef WITH_GTESTS
                   ,
                   const DecodeTestFault *fault = nullptr
#endif
)
{
  if (r_error) {
    *r_error = nullptr;
  }
  if (r_owned_backing_bytes) {
    *r_owned_backing_bytes = 0;
  }
  const Header &header = plan.header;
  const BMAllocTemplate allocation = {
      int(header.counts[0]), int(header.counts[1]), int(header.counts[3]), int(header.counts[2])};
  BMeshCreateParams params{};
  params.use_toolflags = header.flags & 1;
  OwnedMesh owned(BM_mesh_create(&allocation, &params));
  BMesh &mesh = *owned;
  const std::array<CustomData *, 4> domains = {&mesh.vdata, &mesh.edata, &mesh.pdata, &mesh.ldata};
  constexpr std::array<char, 4> domain_types = {BM_VERT, BM_EDGE, BM_FACE, BM_LOOP};
  for (int i = 0; i < 4; i++) {
    if (!schema_install(plan.domains[i], *domains[i], int(header.counts[i]), domain_types[i])) {
      fail(r_error, "The BMesh section schema could not be reconstructed exactly");
      return nullptr;
    }
#ifdef WITH_GTESTS
    if (test_fault(fault, BMGeometryDecodeTestPhase::Schema, uint32_t(i), r_error)) {
      return nullptr;
    }
#endif
  }
  std::array<Array<BMHeader *, 0>, 4> elements;
  for (int i = 0; i < 4; i++) {
    elements[i].reinitialize(header.counts[i]);
  }
  Reader reader(plan.bytes);
  reader.position = plan.geometry_offset;
  for (uint32_t i = 0; i < header.counts[0]; i++) {
    BMVert *vert = BM_vert_create(&mesh, nullptr, nullptr, BM_CREATE_SKIP_CD);
    zero_block(mesh.vdata, vert->head);
    for (float &value : vert->co) {
      read_native(reader, &value, 4);
    }
    for (float &value : vert->no) {
      read_native(reader, &value, 4);
    }
    read_native(reader, &vert->head.hflag, 1);
    elements[0][i] = &vert->head;
#ifdef WITH_GTESTS
    if (test_fault(fault, BMGeometryDecodeTestPhase::Vertex, i, r_error)) {
      return nullptr;
    }
#endif
  }
  for (uint32_t i = 0; i < header.counts[1]; i++) {
    const uint32_t a = reader.integer(4), b = reader.integer(4);
    BMEdge *edge = BM_edge_create(&mesh,
                                  reinterpret_cast<BMVert *>(elements[0][a]),
                                  reinterpret_cast<BMVert *>(elements[0][b]),
                                  nullptr,
                                  BM_CREATE_SKIP_CD);
    zero_block(mesh.edata, edge->head);
    read_native(reader, &edge->head.hflag, 1);
    elements[1][i] = &edge->head;
#ifdef WITH_GTESTS
    if (test_fault(fault, BMGeometryDecodeTestPhase::Edge, i, r_error)) {
      return nullptr;
    }
#endif
  }
  Array<BMVert *, 0> face_vertices(plan.max_face_size);
  Array<BMEdge *, 0> face_edges(plan.max_face_size);
  uint32_t loop_index = 0;
  for (uint32_t i = 0; i < header.counts[2]; i++) {
    const uint32_t length = reader.integer(4);
    std::array<uint32_t, 3> normal;
    for (uint32_t &value : normal) {
      value = reader.integer(4);
    }
    const uint32_t material = reader.integer(2), flag = reader.integer(1);
    const size_t corner_offset = reader.position;
    for (uint32_t j = 0; j < length; j++) {
      face_vertices[j] = reinterpret_cast<BMVert *>(elements[0][reader.integer(4)]);
      face_edges[j] = reinterpret_cast<BMEdge *>(elements[1][reader.integer(4)]);
      reader.integer(1);
    }
    BMFace *face = BM_face_create(
        &mesh, face_vertices.data(), face_edges.data(), int(length), nullptr, BM_CREATE_SKIP_CD);
    if (!face) {
      fail(r_error, "The BMesh section face could not be reconstructed");
      return nullptr;
    }
    zero_block(mesh.pdata, face->head);
    std::memcpy(face->no, normal.data(), sizeof(face->no));
    const uint16_t material_bits = uint16_t(material);
    std::memcpy(&face->mat_nr, &material_bits, 2);
    face->head.hflag = char(flag);
    elements[2][i] = &face->head;
    BMLoop *loop = BM_FACE_FIRST_LOOP(face);
    for (uint32_t j = 0; j < length; j++, loop = loop->next) {
      zero_block(mesh.ldata, loop->head);
      Reader corner(plan.bytes);
      corner.position = corner_offset + size_t(j) * 9 + 8;
      read_native(corner, &loop->head.hflag, 1);
      elements[3][loop_index++] = &loop->head;
    }
#ifdef WITH_GTESTS
    if (test_fault(fault, BMGeometryDecodeTestPhase::Face, i, r_error)) {
      return nullptr;
    }
#endif
  }
  for (uint32_t i = 0; i < header.history; i++) {
    const int domain = history_domain(reader.integer(1));
    const uint32_t index = reader.integer(4);
    _bm_select_history_store_notest(&mesh, elements[domain][index]);
#ifdef WITH_GTESTS
    if (test_fault(fault, BMGeometryDecodeTestPhase::History, i, r_error)) {
      return nullptr;
    }
#endif
  }
#ifdef WITH_GTESTS
  uint32_t attribute_index = 0;
#endif
  for (int domain = 0; domain < 4; domain++) {
    for (const CustomDataLayer &layer : plan.domains[domain].layers) {
      const TypeInfo &info = *type_info(layer.type);
      for (BMHeader *element : elements[domain]) {
        if (!value_read(reader, info, static_cast<char *>(element->data) + layer.offset)) {
          fail(r_error, "The BMesh section owned attribute storage could not be reconstructed");
          return nullptr;
        }
#ifdef WITH_GTESTS
        if (test_fault(fault, BMGeometryDecodeTestPhase::Attribute, attribute_index++, r_error)) {
          return nullptr;
        }
#endif
      }
    }
  }
  mesh.totvertsel = int(header.selected[0]);
  mesh.totedgesel = int(header.selected[1]);
  mesh.totfacesel = int(header.selected[2]);
  mesh.act_face = header.active_face == no_element ?
                      nullptr :
                      reinterpret_cast<BMFace *>(elements[2][header.active_face]);
  mesh.selectmode = header.selectmode;
  mesh.shapenr = header.shapenr;
  mesh.uv_select_sync_valid = header.flags & 2;
  mesh.elem_index_dirty = BM_ALL;
  mesh.elem_table_dirty = BM_VERT | BM_EDGE | BM_FACE;
  mesh.spacearr_dirty = BM_SPACEARR_DIRTY_ALL;
  size_t bytes;
  if (!reader.ok || reader.position != size_t(plan.bytes.size()) ||
      mesh.totvert != int(header.counts[0]) || mesh.totedge != int(header.counts[1]) ||
      mesh.totface != int(header.counts[2]) || mesh.totloop != int(header.counts[3]) ||
      !mesh_backing_bytes(mesh, bytes) || bytes > plan.info.decoded_backing_bytes_bound)
  {
    fail(r_error, "The BMesh decoded geometry or backing accounting differs from its plan");
    return nullptr;
  }
  if (r_owned_backing_bytes) {
    *r_owned_backing_bytes = bytes;
  }
  return owned.release();
}

}  // namespace

BMesh *BM_geometry_snapshot_decode(const BMGeometryDecodePlan &plan,
                                   size_t *r_owned_backing_bytes,
                                   const char **r_error)
{
  return decode_mesh(plan, r_owned_backing_bytes, r_error);
}

#ifdef WITH_GTESTS
BMesh *BM_geometry_snapshot_decode_with_test_fault(const BMGeometryDecodePlan &plan,
                                                   const BMGeometryDecodeTestPhase phase,
                                                   const uint32_t index,
                                                   const char **r_error)
{
  const DecodeTestFault fault{phase, index};
  return decode_mesh(plan, nullptr, r_error, &fault);
}
#endif

}  // namespace blender
