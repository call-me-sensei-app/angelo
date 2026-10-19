/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include <algorithm>
#include <array>
#include <cstring>
#include <limits>
#include <memory>
#include <string>
#include <utility>

#include "testing/testing.h"

#include "MEM_guardedalloc.h"

#define DNA_DEPRECATED_ALLOW
#include "DNA_customdata_types.h"
#include "DNA_meshdata_types.h"
#include "DNA_scene_types.h"

#include "BLI_array.hh"
#include "BLI_bitmap.h"
#include "BLI_math_matrix_types.hh"
#include "BLI_math_vector_types.hh"
#include "BLI_mempool.h"
#include "BLI_string.h"
#include "BLI_vector.hh"

#include "BKE_customdata.hh"
#include "BKE_gtest_base.hh"

#include "bmesh.hh"
#include "intern/bmesh_snapshot.hh"
#include "intern/bmesh_snapshot_io.hh"

namespace blender {
namespace {

/* This list is independent of the codec's dispatch table. Adding an admitted native type requires
 * a meaningful fixture here; raw padded-struct equality is deliberately not the oracle. */
enum class ValueKind {
  Floats,
  Int32,
  Int16,
  Int8,
  Boolean,
  String,
  Vertex,
  Edge,
  Face,
  Polygon,
  Loop,
  LoopUV,
  ByteColor,
  FaceColors,
  Skin,
  FreestyleEdge,
  FreestyleFace,
  Deform,
  Displacements,
  GridMask,
};

struct LayerCase {
  eCustomDataType type;
  ValueKind kind;
  size_t size;
  size_t alignment;
  int components;
};

#define LAYER_CASE(id, kind, native_type, count) \
  {id, ValueKind::kind, sizeof(native_type), alignof(native_type), count}

const std::array<LayerCase, 47> layer_cases = {{
    LAYER_CASE(CD_MVERT, Vertex, MVert, 1),
    LAYER_CASE(CD_MSTICKY, Floats, float2, 2),
    LAYER_CASE(CD_MDEFORMVERT, Deform, MDeformVert, 1),
    LAYER_CASE(CD_MEDGE, Edge, MEdge, 1),
    LAYER_CASE(CD_MFACE, Face, MFace, 1),
    LAYER_CASE(CD_MTFACE, Floats, MTFace, 8),
    LAYER_CASE(CD_MCOL, FaceColors, MCol[4], 4),
    LAYER_CASE(CD_ORIGINDEX, Int32, int, 1),
    LAYER_CASE(CD_NORMAL, Floats, float3, 3),
    LAYER_CASE(CD_FACEMAP, Int32, int, 1),
    LAYER_CASE(CD_PROP_FLOAT, Floats, MFloatProperty, 1),
    LAYER_CASE(CD_PROP_INT32, Int32, MIntProperty, 1),
    LAYER_CASE(CD_PROP_STRING, String, MStringProperty, 1),
    LAYER_CASE(CD_ORIGSPACE, Floats, OrigSpaceFace, 8),
    LAYER_CASE(CD_ORCO, Floats, float3, 3),
    LAYER_CASE(CD_MTEXPOLY, Int32, int, 1),
    LAYER_CASE(CD_MLOOPUV, LoopUV, MLoopUV, 1),
    LAYER_CASE(CD_PROP_BYTE_COLOR, ByteColor, MLoopCol, 1),
    LAYER_CASE(CD_TANGENT, Floats, float[4], 4),
    LAYER_CASE(CD_MDISPS, Displacements, MDisps, 1),
    LAYER_CASE(CD_PROP_FLOAT4X4, Floats, float4x4, 16),
    LAYER_CASE(CD_PROP_INT16_2D, Int16, short2, 2),
    LAYER_CASE(CD_CLOTH_ORCO, Floats, float[3], 3),
    LAYER_CASE(CD_PROP_FLOAT4, Floats, float4, 4),
    LAYER_CASE(CD_MPOLY, Polygon, MPoly, 1),
    LAYER_CASE(CD_MLOOP, Loop, MLoop, 1),
    LAYER_CASE(CD_SHAPE_KEYINDEX, Int32, int, 1),
    LAYER_CASE(CD_SHAPEKEY, Floats, float[3], 3),
    LAYER_CASE(CD_BWEIGHT, Floats, MFloatProperty, 1),
    LAYER_CASE(CD_CREASE, Floats, float, 1),
    LAYER_CASE(CD_ORIGSPACE_MLOOP, Floats, OrigSpaceLoop, 2),
    LAYER_CASE(CD_PAINT_MASK, Floats, float, 1),
    LAYER_CASE(CD_GRID_PAINT_MASK, GridMask, GridPaintMask, 1),
    LAYER_CASE(CD_MVERT_SKIN, Skin, MVertSkin, 1),
    LAYER_CASE(CD_FREESTYLE_EDGE, FreestyleEdge, FreestyleEdge, 1),
    LAYER_CASE(CD_FREESTYLE_FACE, FreestyleFace, FreestyleFace, 1),
    LAYER_CASE(CD_MLOOPTANGENT, Floats, float[4], 4),
    LAYER_CASE(CD_TESSLOOPNORMAL, Int16, short[4][3], 12),
    LAYER_CASE(CD_CUSTOMLOOPNORMAL, Int16, short[2], 2),
    LAYER_CASE(CD_SCULPT_FACE_SETS, Int32, int, 1),
    LAYER_CASE(CD_PROP_INT8, Int8, int8_t, 1),
    LAYER_CASE(CD_PROP_INT32_2D, Int32, int2, 2),
    LAYER_CASE(CD_PROP_COLOR, Floats, MPropCol, 4),
    LAYER_CASE(CD_PROP_FLOAT3, Floats, float3, 3),
    LAYER_CASE(CD_PROP_FLOAT2, Floats, float2, 2),
    LAYER_CASE(CD_PROP_BOOL, Boolean, bool, 1),
    LAYER_CASE(CD_PROP_QUATERNION, Floats, float[4], 4),
}};

#undef LAYER_CASE

constexpr std::array<uint32_t, 16> float_patterns = {0x80000000,
                                                     0x00000000,
                                                     0x7f800000,
                                                     0xff800000,
                                                     0x7fc12345,
                                                     0xffc54321,
                                                     0x00000001,
                                                     0x80000001,
                                                     0x3f800000,
                                                     0xc0200000,
                                                     0x7f7fffff,
                                                     0xff7fffff,
                                                     0x3eaaaaab,
                                                     0x40800000,
                                                     0xbf000000,
                                                     0x00800000};

void store_float_bits(void *value, const int component, const uint32_t bits)
{
  std::memcpy(static_cast<std::byte *>(value) + component * sizeof(float), &bits, sizeof(bits));
}

uint32_t load_float_bits(const void *value, const int component = 0)
{
  uint32_t bits;
  std::memcpy(
      &bits, static_cast<const std::byte *>(value) + component * sizeof(float), sizeof(bits));
  return bits;
}

void fill_float_components(void *value, const int count)
{
  for (int i = 0; i < count; i++) {
    store_float_bits(value, i, float_patterns[i]);
  }
}

void expect_float_components(const void *value, const int count)
{
  for (int i = 0; i < count; i++) {
    EXPECT_EQ(load_float_bits(value, i), float_patterns[i]) << "component " << i;
  }
}

void fill_layer_value(const LayerCase &test, void *value)
{
  /* Nonzero padding ensures that persistence cannot accidentally depend on native struct bytes. */
  std::memset(value, 0xa5, test.size);
  switch (test.kind) {
    case ValueKind::Floats:
      fill_float_components(value, test.components);
      break;
    case ValueKind::Int32:
      for (int i = 0; i < test.components; i++) {
        static_cast<int *>(value)[i] = -123456789 + i * 101;
      }
      break;
    case ValueKind::Int16:
      for (int i = 0; i < test.components; i++) {
        static_cast<short *>(value)[i] = short(-23456 + i * 97);
      }
      break;
    case ValueKind::Int8:
      *static_cast<int8_t *>(value) = -97;
      break;
    case ValueKind::Boolean:
      *static_cast<bool *>(value) = true;
      break;
    case ValueKind::String: {
      auto &data = *static_cast<MStringProperty *>(value);
      for (int i = 0; i < 255; i++) {
        data.s[i] = char(uint8_t(i * 73 + 19));
      }
      data.s_len = char(uint8_t(255));
      break;
    }
    case ValueKind::Vertex: {
      auto &data = *static_cast<MVert *>(value);
      fill_float_components(data.co_legacy, 3);
      data.flag_legacy = ME_HIDE;
      data.bweight_legacy = char(0xd3);
      break;
    }
    case ValueKind::Edge: {
      auto &data = *static_cast<MEdge *>(value);
      data.v1 = 0x12345678;
      data.v2 = 0x87654321;
      data.crease_legacy = char(0x93);
      data.bweight_legacy = char(0xb7);
      data.flag_legacy = ME_SEAM | ME_SHARP;
      break;
    }
    case ValueKind::Face: {
      auto &data = *static_cast<MFace *>(value);
      data.v1 = 19;
      data.v2 = 41;
      data.v3 = 103;
      data.v4 = 257;
      data.mat_nr = -1234;
      data.edcode = ME_V1V2 | ME_V4V1;
      data.flag = char(0x96);
      break;
    }
    case ValueKind::Polygon: {
      auto &data = *static_cast<MPoly *>(value);
      data.loopstart = -7654321;
      data.totloop = 1234567;
      data.mat_nr_legacy = -3210;
      data.flag_legacy = ME_SMOOTH | ME_FACE_SEL;
      break;
    }
    case ValueKind::Loop: {
      auto &data = *static_cast<MLoop *>(value);
      data.v = 0x89abcdef;
      data.e = 0x10203040;
      break;
    }
    case ValueKind::LoopUV: {
      auto &data = *static_cast<MLoopUV *>(value);
      fill_float_components(data.uv, 2);
      data.flag = MLOOPUV_EDGESEL | MLOOPUV_PINNED;
      break;
    }
    case ValueKind::ByteColor: {
      auto &data = *static_cast<MLoopCol *>(value);
      data.r = 0x12;
      data.g = 0x34;
      data.b = 0x56;
      data.a = 0x78;
      break;
    }
    case ValueKind::FaceColors:
      for (int i = 0; i < 4; i++) {
        auto &data = static_cast<MCol *>(value)[i];
        data.a = 0x80 + i;
        data.r = 0x10 + i;
        data.g = 0x20 + i;
        data.b = 0x30 + i;
      }
      break;
    case ValueKind::Skin: {
      auto &data = *static_cast<MVertSkin *>(value);
      fill_float_components(data.radius, 3);
      data.flag = MVERT_SKIN_ROOT | MVERT_SKIN_LOOSE;
      break;
    }
    case ValueKind::FreestyleEdge:
      static_cast<FreestyleEdge *>(value)->flag = FREESTYLE_EDGE_MARK;
      break;
    case ValueKind::FreestyleFace:
      static_cast<FreestyleFace *>(value)->flag = FREESTYLE_FACE_MARK;
      break;
    case ValueKind::Deform: {
      auto &data = *static_cast<MDeformVert *>(value);
      data = {};
      data.totweight = 2;
      data.flag = 0x123456;
      data.dw = MEM_new_array_zeroed<MDeformWeight>(2, __func__);
      data.dw[0].def_nr = 97;
      data.dw[1].def_nr = 3;
      store_float_bits(&data.dw[0].weight, 0, 0x80000000);
      store_float_bits(&data.dw[1].weight, 0, 0x7fc12345);
      break;
    }
    case ValueKind::Displacements: {
      auto &data = *static_cast<MDisps *>(value);
      data = {};
      data.totdisp = 3;
      data.level = 2;
      data.disps = MEM_new_array_zeroed<float[3]>(3, __func__);
      fill_float_components(data.disps, 9);
      data.hidden = BLI_BITMAP_NEW(3, __func__);
      data.hidden[0] = 0xf1234567;
      break;
    }
    case ValueKind::GridMask: {
      auto &data = *static_cast<GridPaintMask *>(value);
      data = {};
      data.level = 1;
      data.data = MEM_new_array_zeroed<float>(4, __func__);
      fill_float_components(data.data, 4);
      break;
    }
  }
}

void expect_layer_value(const LayerCase &test, const void *value)
{
  switch (test.kind) {
    case ValueKind::Floats:
      expect_float_components(value, test.components);
      break;
    case ValueKind::Int32:
      for (int i = 0; i < test.components; i++) {
        EXPECT_EQ(static_cast<const int *>(value)[i], -123456789 + i * 101);
      }
      break;
    case ValueKind::Int16:
      for (int i = 0; i < test.components; i++) {
        EXPECT_EQ(static_cast<const short *>(value)[i], -23456 + i * 97);
      }
      break;
    case ValueKind::Int8:
      EXPECT_EQ(*static_cast<const int8_t *>(value), -97);
      break;
    case ValueKind::Boolean:
      EXPECT_TRUE(*static_cast<const bool *>(value));
      break;
    case ValueKind::String: {
      const auto &data = *static_cast<const MStringProperty *>(value);
      for (int i = 0; i < 255; i++) {
        EXPECT_EQ(uint8_t(data.s[i]), uint8_t(i * 73 + 19)) << "string byte " << i;
      }
      EXPECT_EQ(uint8_t(data.s_len), 255);
      break;
    }
    case ValueKind::Vertex: {
      const auto &data = *static_cast<const MVert *>(value);
      expect_float_components(data.co_legacy, 3);
      EXPECT_EQ(data.flag_legacy, ME_HIDE);
      EXPECT_EQ(uint8_t(data.bweight_legacy), 0xd3);
      EXPECT_EQ(data._pad[0], 0);
      EXPECT_EQ(data._pad[1], 0);
      break;
    }
    case ValueKind::Edge: {
      const auto &data = *static_cast<const MEdge *>(value);
      EXPECT_EQ(data.v1, 0x12345678);
      EXPECT_EQ(data.v2, 0x87654321);
      EXPECT_EQ(uint8_t(data.crease_legacy), 0x93);
      EXPECT_EQ(uint8_t(data.bweight_legacy), 0xb7);
      EXPECT_EQ(data.flag_legacy, ME_SEAM | ME_SHARP);
      break;
    }
    case ValueKind::Face: {
      const auto &data = *static_cast<const MFace *>(value);
      EXPECT_EQ(data.v1, 19);
      EXPECT_EQ(data.v2, 41);
      EXPECT_EQ(data.v3, 103);
      EXPECT_EQ(data.v4, 257);
      EXPECT_EQ(data.mat_nr, -1234);
      EXPECT_EQ(data.edcode, ME_V1V2 | ME_V4V1);
      EXPECT_EQ(uint8_t(data.flag), 0x96);
      break;
    }
    case ValueKind::Polygon: {
      const auto &data = *static_cast<const MPoly *>(value);
      EXPECT_EQ(data.loopstart, -7654321);
      EXPECT_EQ(data.totloop, 1234567);
      EXPECT_EQ(data.mat_nr_legacy, -3210);
      EXPECT_EQ(data.flag_legacy, ME_SMOOTH | ME_FACE_SEL);
      EXPECT_EQ(data._pad, 0);
      break;
    }
    case ValueKind::Loop: {
      const auto &data = *static_cast<const MLoop *>(value);
      EXPECT_EQ(data.v, 0x89abcdef);
      EXPECT_EQ(data.e, 0x10203040);
      break;
    }
    case ValueKind::LoopUV: {
      const auto &data = *static_cast<const MLoopUV *>(value);
      expect_float_components(data.uv, 2);
      EXPECT_EQ(data.flag, MLOOPUV_EDGESEL | MLOOPUV_PINNED);
      break;
    }
    case ValueKind::ByteColor: {
      const auto &data = *static_cast<const MLoopCol *>(value);
      EXPECT_EQ(data.r, 0x12);
      EXPECT_EQ(data.g, 0x34);
      EXPECT_EQ(data.b, 0x56);
      EXPECT_EQ(data.a, 0x78);
      break;
    }
    case ValueKind::FaceColors:
      for (int i = 0; i < 4; i++) {
        const auto &data = static_cast<const MCol *>(value)[i];
        EXPECT_EQ(data.a, 0x80 + i);
        EXPECT_EQ(data.r, 0x10 + i);
        EXPECT_EQ(data.g, 0x20 + i);
        EXPECT_EQ(data.b, 0x30 + i);
      }
      break;
    case ValueKind::Skin: {
      const auto &data = *static_cast<const MVertSkin *>(value);
      expect_float_components(data.radius, 3);
      EXPECT_EQ(data.flag, MVERT_SKIN_ROOT | MVERT_SKIN_LOOSE);
      break;
    }
    case ValueKind::FreestyleEdge:
      EXPECT_EQ(static_cast<const FreestyleEdge *>(value)->flag, FREESTYLE_EDGE_MARK);
      break;
    case ValueKind::FreestyleFace:
      EXPECT_EQ(static_cast<const FreestyleFace *>(value)->flag, FREESTYLE_FACE_MARK);
      break;
    case ValueKind::Deform: {
      const auto &data = *static_cast<const MDeformVert *>(value);
      ASSERT_EQ(data.totweight, 2);
      ASSERT_NE(data.dw, nullptr);
      EXPECT_EQ(data.flag, 0x123456);
      EXPECT_EQ(data.dw[0].def_nr, 97);
      EXPECT_EQ(data.dw[1].def_nr, 3);
      EXPECT_EQ(load_float_bits(&data.dw[0].weight), 0x80000000);
      EXPECT_EQ(load_float_bits(&data.dw[1].weight), 0x7fc12345);
      break;
    }
    case ValueKind::Displacements: {
      const auto &data = *static_cast<const MDisps *>(value);
      ASSERT_EQ(data.totdisp, 3);
      EXPECT_EQ(data.level, 2);
      ASSERT_NE(data.disps, nullptr);
      ASSERT_NE(data.hidden, nullptr);
      expect_float_components(data.disps, 9);
      EXPECT_EQ(data.hidden[0], 0xf1234567);
      break;
    }
    case ValueKind::GridMask: {
      const auto &data = *static_cast<const GridPaintMask *>(value);
      EXPECT_EQ(data.level, 1);
      ASSERT_NE(data.data, nullptr);
      expect_float_components(data.data, 4);
      break;
    }
  }
}

/* Version-one field offsets are an independent wire oracle, not native struct offsets. */
constexpr size_t wire_header_size = 58;
constexpr std::array<uint16_t, 47> wire_tags = {
    0,  1,  2,  3,  4,  5,  6,  7,  8,  9,  10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 22, 23, 24,
    25, 26, 27, 28, 29, 30, 31, 34, 35, 36, 37, 38, 39, 40, 41, 42, 45, 46, 47, 48, 49, 50, 52};

uint64_t read_le(const Span<std::byte> bytes, const size_t offset, const int width)
{
  uint64_t value = 0;
  for (int i = 0; i < width; i++) {
    value |= uint64_t(std::to_integer<uint8_t>(bytes[offset + i])) << (8 * i);
  }
  return value;
}

void write_le(MutableSpan<std::byte> bytes, const size_t offset, const int width, uint64_t value)
{
  ASSERT_LE(offset + width, bytes.size());
  for (int i = 0; i < width; i++, value >>= 8) {
    bytes[offset + i] = std::byte(value & 0xff);
  }
}

void append_le(Vector<std::byte> &bytes, const uint64_t value, const int width)
{
  for (int i = 0; i < width; i++) {
    bytes.append(std::byte((value >> (8 * i)) & 0xff));
  }
}

struct WireOffsets {
  std::array<Vector<size_t>, 4> layers;
  size_t vertices;
  size_t edges;
  Vector<size_t> faces;
  Vector<size_t> loops;
  size_t history;
  size_t payload;
};

/* Only inspect output from a successful encoder. Mutated/truncated input goes directly to the
 * production prepare API; this helper never serves as a permissive replacement validator. */
WireOffsets inspect_valid_wire(const Span<std::byte> bytes)
{
  WireOffsets result;
  size_t offset = wire_header_size;
  for (int domain = 0; domain < 4; domain++) {
    const uint32_t layers = uint32_t(read_le(bytes, offset, 4));
    offset += 4;
    if (read_le(bytes, offset++, 1)) {
      const size_t path_len = read_le(bytes, offset, 2);
      offset += 2 + path_len;
    }
    for (uint32_t layer = 0; layer < layers; layer++) {
      result.layers[domain].append(offset);
      const size_t name_len = read_le(bytes, offset + 18, 2);
      offset += 20 + name_len;
    }
  }
  result.vertices = offset;
  offset += read_le(bytes, 16, 4) * 25;
  result.edges = offset;
  offset += read_le(bytes, 20, 4) * 9;
  for (uint32_t face = 0; face < read_le(bytes, 24, 4); face++) {
    result.faces.append(offset);
    const uint32_t length = uint32_t(read_le(bytes, offset, 4));
    offset += 19;
    for (uint32_t loop = 0; loop < length; loop++) {
      result.loops.append(offset);
      offset += 9;
    }
  }
  result.history = offset;
  offset += read_le(bytes, 44, 4) * 5;
  result.payload = offset;
  return result;
}

class BMeshSnapshotIOTest : public bke::BlenderGTestBase {
 protected:
  Vector<BMesh *> meshes_;
  Vector<BMGeometrySnapshot *> snapshots_;
  Vector<BMGeometryDecodePlan *> plans_;

  static BMGeometryCodecLimits unlimited()
  {
    return {std::numeric_limits<uint64_t>::max(),
            std::numeric_limits<size_t>::max(),
            std::numeric_limits<size_t>::max()};
  }

  void TearDown() override
  {
    for (BMGeometryDecodePlan *plan : plans_) {
      BM_geometry_snapshot_decode_plan_free(plan);
    }
    for (BMGeometrySnapshot *snapshot : snapshots_) {
      BM_geometry_snapshot_free(snapshot);
    }
    for (BMesh *bm : meshes_) {
      BM_mesh_free(bm);
    }
    bke::BlenderGTestBase::TearDown();
  }

  BMesh *mesh_create(const bool toolflags = false)
  {
    BMeshCreateParams params{};
    params.use_toolflags = toolflags;
    BMesh *bm = BM_mesh_create(&bm_mesh_allocsize_default, &params);
    meshes_.append(bm);
    return bm;
  }

  void mesh_free(BMesh *bm)
  {
    meshes_.remove_first_occurrence_and_reorder(bm);
    BM_mesh_free(bm);
  }

  BMGeometrySnapshot *snapshot_create(BMesh *bm)
  {
    const char *error = nullptr;
    BMGeometrySnapshot *snapshot = BM_geometry_snapshot_create(bm, &error);
    EXPECT_NE(snapshot, nullptr) << (error ? error : "No capture diagnostic");
    if (snapshot) {
      snapshots_.append(snapshot);
    }
    return snapshot;
  }

  void snapshot_free(BMGeometrySnapshot *snapshot)
  {
    snapshots_.remove_first_occurrence_and_reorder(snapshot);
    BM_geometry_snapshot_free(snapshot);
  }

  Array<std::byte, 0> encode(const BMGeometrySnapshot *snapshot)
  {
    Vector<std::byte> bytes;
    const char *error = nullptr;
    const bool success = BM_geometry_snapshot_encode(
        snapshot,
        unlimited(),
        [&](const Span<std::byte> chunk) {
          bytes.extend(chunk);
          return true;
        },
        &error);
    EXPECT_TRUE(success) << (error ? error : "No encode diagnostic");
    return Array<std::byte, 0>(bytes.as_span());
  }

  BMGeometryDecodePlan *prepare(Array<std::byte, 0> bytes)
  {
    const char *error = nullptr;
    BMGeometryDecodePlan *plan = BM_geometry_snapshot_decode_prepare(
        std::move(bytes), unlimited(), &error);
    EXPECT_NE(plan, nullptr) << (error ? error : "No prepare diagnostic");
    EXPECT_TRUE(bytes.is_empty());
    if (plan) {
      plans_.append(plan);
    }
    return plan;
  }

  void plan_free(BMGeometryDecodePlan *plan)
  {
    plans_.remove_first_occurrence_and_reorder(plan);
    BM_geometry_snapshot_decode_plan_free(plan);
  }

  BMesh *decode(const BMGeometryDecodePlan &plan)
  {
    size_t bytes = 0;
    const char *error = nullptr;
    BMesh *bm = BM_geometry_snapshot_decode(plan, &bytes, &error);
    EXPECT_NE(bm, nullptr) << (error ? error : "No decode diagnostic");
    if (bm) {
      meshes_.append(bm);
      EXPECT_GT(bytes, 0);
      EXPECT_LE(bytes, BM_geometry_snapshot_decode_info(plan).decoded_backing_bytes_bound);
    }
    else {
      EXPECT_EQ(bytes, 0);
    }
    return bm;
  }

  static BMVert *vert_add(BMesh *bm, const float x = 0.0f)
  {
    const float co[3] = {x, 0, 0};
    return BM_vert_create(bm, co, nullptr, BM_CREATE_NOP);
  }

  static BMVert *first_vert(BMesh *bm)
  {
    BMIter iter;
    return static_cast<BMVert *>(BM_iter_new(&iter, bm, BM_VERTS_OF_MESH, nullptr));
  }

  static BMFace *first_face(BMesh *bm)
  {
    BMIter iter;
    return static_cast<BMFace *>(BM_iter_new(&iter, bm, BM_FACES_OF_MESH, nullptr));
  }

  static BMFace *triangle_add(BMesh *bm)
  {
    const float coordinates[3][3] = {{0, 0, 0}, {2, 0, 0}, {0, 3, 0}};
    BMVert *verts[3];
    for (int i = 0; i < 3; i++) {
      verts[i] = BM_vert_create(bm, coordinates[i], nullptr, BM_CREATE_NOP);
    }
    BMVert *corners[3] = {verts[2], verts[0], verts[1]};
    return BM_face_create_verts(bm, corners, 3, nullptr, BM_CREATE_NOP, true);
  }

  template<typename T, typename Elem> static T &data(Elem *elem, const int offset)
  {
    return *static_cast<T *>(BM_ELEM_CD_GET_VOID_P(elem, offset));
  }

  void expect_prepare_refused(Array<std::byte, 0> bytes,
                              const BMGeometryCodecLimits &limits,
                              const char *diagnostic = nullptr)
  {
    const char *error = nullptr;
    BMGeometryDecodePlan *plan = BM_geometry_snapshot_decode_prepare(
        std::move(bytes), limits, &error);
    EXPECT_EQ(plan, nullptr);
    EXPECT_NE(error, nullptr);
    if (diagnostic) {
      EXPECT_STREQ(error, diagnostic);
    }
    EXPECT_TRUE(bytes.is_empty());
    if (plan) {
      plans_.append(plan);
    }
  }
};

class BMeshSnapshotIOLayerTest : public BMeshSnapshotIOTest,
                                 public testing::WithParamInterface<LayerCase> {};

TEST_P(BMeshSnapshotIOLayerTest, EveryMeaningfulFieldSurvives)
{
  const LayerCase test = GetParam();
  ASSERT_EQ(CustomData_sizeof(test.type), test.size);
  BMesh *bm = mesh_create();
  BM_data_layer_add_named(bm, &bm->vdata, test.type, "payload");
  BMVert *vert = vert_add(bm);
  const int offset = CustomData_get_offset(&bm->vdata, test.type);
  ASSERT_GE(offset, 0);
  void *value = BM_ELEM_CD_GET_VOID_P(vert, offset);
  EXPECT_EQ(reinterpret_cast<uintptr_t>(value) % test.alignment, 0);
  fill_layer_value(test, value);
  const Array<std::byte> original_value(Span(static_cast<const std::byte *>(value), test.size));
  bm->vdata.layers[0].flag = CD_FLAG_TEMPORARY;
  bm->vdata.layers[0].uid = 1701;
  BMGeometrySnapshot *snapshot = snapshot_create(bm);
  ASSERT_NE(snapshot, nullptr);
  const Array<std::byte, 0> encoded = encode(snapshot);
  ASSERT_GE(encoded.size(), wire_header_size + 7);
  const WireOffsets wire = inspect_valid_wire(encoded);
  ASSERT_EQ(wire.layers[0].size(), 1);
  for (const int i : IndexRange(layer_cases.size())) {
    if (layer_cases[i].type == test.type) {
      EXPECT_EQ(read_le(encoded, wire.layers[0][0], 2), wire_tags[i]);
    }
  }
  BMGeometryDecodePlan *plan = prepare(encoded);
  ASSERT_NE(plan, nullptr);
  BMesh *decoded = decode(*plan);
  ASSERT_NE(decoded, nullptr);
  ASSERT_EQ(decoded->totvert, 1);
  ASSERT_EQ(decoded->vdata.totlayer, 1);
  const CustomDataLayer &layer = decoded->vdata.layers[0];
  EXPECT_EQ(layer.type, test.type);
  EXPECT_EQ(layer.flag, CD_FLAG_TEMPORARY);
  EXPECT_EQ(layer.uid, 1701);
  EXPECT_STREQ(layer.name, "payload");
  const void *decoded_value = BM_ELEM_CD_GET_VOID_P(first_vert(decoded), layer.offset);
  EXPECT_EQ(reinterpret_cast<uintptr_t>(decoded_value) % test.alignment, 0);
  expect_layer_value(test, decoded_value);
  BMGeometrySnapshot *second = snapshot_create(decoded);
  ASSERT_NE(second, nullptr);
  EXPECT_EQ_SPAN<std::byte>(encoded, encode(second));
  EXPECT_EQ_SPAN<std::byte>(original_value,
                            Span(static_cast<const std::byte *>(value), test.size));
}

INSTANTIATE_TEST_SUITE_P(All47Types,
                         BMeshSnapshotIOLayerTest,
                         testing::ValuesIn(layer_cases),
                         [](const testing::TestParamInfo<LayerCase> &info) {
                           return "Type" + std::to_string(info.param.type);
                         });

TEST_F(BMeshSnapshotIOTest, EmptyWireHeaderIsExplicitLittleEndianVersionOne)
{
  BMesh *bm = mesh_create();
  BMGeometrySnapshot *snapshot = snapshot_create(bm);
  ASSERT_NE(snapshot, nullptr);
  const Array<std::byte, 0> encoded = encode(snapshot);
  /* Empty geometry has only the header and four empty domain schemas. */
  const std::array<uint8_t, 78> expected = {
      'B', 'M', 'S', 'N', 'A', 'P', '0', '1', 1, 0, 0, 0,             /* version */
      0,   0,   0,   0,                                               /* flags */
      0,   0,   0,   0,   0,   0,   0,   0,   0, 0, 0, 0, 0, 0, 0, 0, /* V/E/F/L */
      0,   0,   0,   0,   0,   0,   0,   0,   0, 0, 0, 0,             /* selected V/E/F */
      0,   0,   0,   0,                                               /* history */
      255, 255, 255, 255,                                             /* active face absent */
      0,   0,   0,   0,   0,   0,                                     /* selectmode/shapenr */
      0,   0,   0,   0,   0,                                          /* vertex schema */
      0,   0,   0,   0,   0,                                          /* edge schema */
      0,   0,   0,   0,   0,                                          /* face schema */
      0,   0,   0,   0,   0};                                         /* loop schema */
  ASSERT_EQ(encoded.size(), expected.size());
  for (const int i : encoded.index_range()) {
    EXPECT_EQ(std::to_integer<uint8_t>(encoded[i]), expected[i]) << "wire byte " << i;
  }
  BMGeometryDecodePlan *plan = prepare(encoded);
  ASSERT_NE(plan, nullptr);
  const BMGeometryDecodeInfo &info = BM_geometry_snapshot_decode_info(*plan);
  EXPECT_EQ(info.section_bytes, 78);
  EXPECT_EQ(info.verts_num, 0);
  EXPECT_EQ(info.edges_num, 0);
  EXPECT_EQ(info.faces_num, 0);
  EXPECT_EQ(info.loops_num, 0);
  BMesh *decoded = decode(*plan);
  ASSERT_NE(decoded, nullptr);
  EXPECT_EQ(decoded->totvert, 0);
  EXPECT_EQ(decoded->totface, 0);
  BM_geometry_snapshot_decode_plan_free(nullptr);
}

TEST_F(BMeshSnapshotIOTest, ScalarMatrixColorAndStringPayloadGoldens)
{
  for (const eCustomDataType type : {CD_PROP_FLOAT4X4,
                                     CD_PROP_INT32_2D,
                                     CD_PROP_BYTE_COLOR,
                                     CD_MCOL,
                                     CD_PROP_STRING,
                                     CD_MVERT,
                                     CD_MPOLY})
  {
    SCOPED_TRACE(int(type));
    const LayerCase *test = nullptr;
    for (const LayerCase &candidate : layer_cases) {
      if (candidate.type == type) {
        test = &candidate;
      }
    }
    ASSERT_NE(test, nullptr);
    BMesh *bm = mesh_create();
    BM_data_layer_add_named(bm, &bm->vdata, type, "golden");
    BMVert *vert = vert_add(bm);
    fill_layer_value(*test, BM_ELEM_CD_GET_VOID_P(vert, CustomData_get_offset(&bm->vdata, type)));
    BMGeometrySnapshot *snapshot = snapshot_create(bm);
    ASSERT_NE(snapshot, nullptr);
    const Array<std::byte, 0> bytes = encode(snapshot);
    const WireOffsets offsets = inspect_valid_wire(bytes);
    Vector<std::byte> expected;
    if (type == CD_PROP_FLOAT4X4) {
      /* Column-major native matrix storage; every component has a distinct bit pattern. */
      for (const uint32_t bits : float_patterns) {
        append_le(expected, bits, 4);
      }
    }
    else if (type == CD_PROP_INT32_2D) {
      for (const uint8_t byte : {0xeb, 0x32, 0xa4, 0xf8, 0x50, 0x33, 0xa4, 0xf8}) {
        expected.append(std::byte(byte));
      }
    }
    else if (type == CD_PROP_BYTE_COLOR) {
      for (const uint8_t byte : {0x12, 0x34, 0x56, 0x78}) {
        expected.append(std::byte(byte));
      }
    }
    else if (type == CD_MCOL) {
      for (const uint8_t byte : {0x80,
                                 0x10,
                                 0x20,
                                 0x30,
                                 0x81,
                                 0x11,
                                 0x21,
                                 0x31,
                                 0x82,
                                 0x12,
                                 0x22,
                                 0x32,
                                 0x83,
                                 0x13,
                                 0x23,
                                 0x33})
      {
        expected.append(std::byte(byte));
      }
    }
    else if (type == CD_PROP_STRING) {
      for (int i = 0; i < 255; i++) {
        expected.append(std::byte(uint8_t(i * 73 + 19)));
      }
      expected.append(std::byte(255));
    }
    else if (type == CD_MVERT) {
      append_le(expected, 0x80000000, 4);
      append_le(expected, 0x00000000, 4);
      append_le(expected, 0x7f800000, 4);
      expected.append(std::byte(0x10));
      expected.append(std::byte(0xd3));
    }
    else if (type == CD_MPOLY) {
      append_le(expected, uint32_t(-7654321), 4);
      append_le(expected, 1234567, 4);
      append_le(expected, uint16_t(-3210), 2);
      expected.append(std::byte(3));
    }
    EXPECT_EQ_SPAN<std::byte>(expected, bytes.as_span().drop_front(offsets.payload));
  }
}

TEST_F(BMeshSnapshotIOTest, DeepPayloadPresenceAndPlansHaveIndependentLifetimes)
{
  BMesh *bm = mesh_create();
  BM_data_layer_add(bm, &bm->vdata, CD_MDEFORMVERT);
  BM_data_layer_add(bm, &bm->vdata, CD_MDISPS);
  BM_data_layer_add(bm, &bm->vdata, CD_GRID_PAINT_MASK);
  const int deform_offset = CustomData_get_offset(&bm->vdata, CD_MDEFORMVERT);
  const int disp_offset = CustomData_get_offset(&bm->vdata, CD_MDISPS);
  const int mask_offset = CustomData_get_offset(&bm->vdata, CD_GRID_PAINT_MASK);
  std::array<BMVert *, 4> source;
  for (int i = 0; i < 4; i++) {
    source[i] = vert_add(bm, i);
  }
  for (const LayerCase &test : layer_cases) {
    if (ELEM(test.type, CD_MDEFORMVERT, CD_MDISPS, CD_GRID_PAINT_MASK)) {
      fill_layer_value(
          test, BM_ELEM_CD_GET_VOID_P(source[0], CustomData_get_offset(&bm->vdata, test.type)));
    }
  }
  data<MDeformVert>(source[1], deform_offset).dw = MEM_new_array_zeroed<MDeformWeight>(1,
                                                                                       __func__);
  MDisps &zero_present = data<MDisps>(source[1], disp_offset);
  zero_present.level = 7;
  zero_present.disps = MEM_new_array_zeroed<float[3]>(1, __func__);
  zero_present.hidden = MEM_new_array_zeroed<BLI_bitmap>(1, __func__);
  data<GridPaintMask>(source[1], mask_offset).level = 37;
  MDisps &hidden_only = data<MDisps>(source[2], disp_offset);
  hidden_only.totdisp = 9;
  hidden_only.level = 2;
  hidden_only.hidden = BLI_BITMAP_NEW(9, __func__);
  hidden_only.hidden[0] = 0xfedcba98;
  data<GridPaintMask>(source[2], mask_offset).level = std::numeric_limits<unsigned int>::max();
  data<MDisps>(source[3], disp_offset).totdisp = 25;
  data<MDisps>(source[3], disp_offset).level = 3;
  BMGeometrySnapshot *snapshot = snapshot_create(bm);
  ASSERT_NE(snapshot, nullptr);
  Array<std::byte, 0> bytes = encode(snapshot);
  BMGeometryDecodePlan *plan = prepare(std::move(bytes));
  ASSERT_NE(plan, nullptr);
  EXPECT_TRUE(bytes.is_empty());
  BMesh *first = decode(*plan);
  BMesh *second = decode(*plan);
  ASSERT_NE(first, nullptr);
  ASSERT_NE(second, nullptr);
  std::array<BMesh *, 2> outputs = {first, second};
  std::array<Vector<BMVert *>, 2> output_verts;
  for (int output = 0; output < 2; output++) {
    BMIter iter;
    BMVert *vert;
    BM_ITER_MESH (vert, &iter, outputs[output], BM_VERTS_OF_MESH) {
      output_verts[output].append(vert);
    }
    ASSERT_EQ(output_verts[output].size(), 4);
    const auto &verts = output_verts[output];
    for (const LayerCase &test : layer_cases) {
      if (ELEM(test.type, CD_MDEFORMVERT, CD_MDISPS, CD_GRID_PAINT_MASK)) {
        expect_layer_value(
            test,
            BM_ELEM_CD_GET_VOID_P(verts[0],
                                  CustomData_get_offset(&outputs[output]->vdata, test.type)));
      }
    }
    const MDeformVert &deform = data<MDeformVert>(verts[1], deform_offset);
    EXPECT_EQ(deform.totweight, 0);
    ASSERT_NE(deform.dw, nullptr);
    EXPECT_GE(MEM_allocN_len(deform.dw), sizeof(MDeformWeight));
    EXPECT_EQ(deform.dw[0].def_nr, 0);
    EXPECT_EQ(load_float_bits(&deform.dw[0].weight), 0);
    const MDisps &disp = data<MDisps>(verts[1], disp_offset);
    EXPECT_EQ(disp.totdisp, 0);
    EXPECT_EQ(disp.level, 7);
    ASSERT_NE(disp.disps, nullptr);
    ASSERT_NE(disp.hidden, nullptr);
    EXPECT_GE(MEM_allocN_len(disp.disps), sizeof(float[3]));
    EXPECT_GE(MEM_allocN_len(disp.hidden), sizeof(BLI_bitmap));
    EXPECT_EQ(load_float_bits(disp.disps, 0), 0);
    EXPECT_EQ(load_float_bits(disp.disps, 1), 0);
    EXPECT_EQ(load_float_bits(disp.disps, 2), 0);
    EXPECT_EQ(disp.hidden[0], 0);
    EXPECT_EQ(data<GridPaintMask>(verts[1], mask_offset).data, nullptr);
    EXPECT_EQ(data<GridPaintMask>(verts[1], mask_offset).level, 37);
    const MDisps &hidden = data<MDisps>(verts[2], disp_offset);
    EXPECT_EQ(hidden.totdisp, 9);
    EXPECT_EQ(hidden.disps, nullptr);
    ASSERT_NE(hidden.hidden, nullptr);
    EXPECT_EQ(hidden.hidden[0], 0xfedcba98);
    EXPECT_EQ(data<GridPaintMask>(verts[2], mask_offset).level,
              std::numeric_limits<unsigned int>::max());
    EXPECT_EQ(data<MDisps>(verts[3], disp_offset).totdisp, 25);
    EXPECT_EQ(data<MDisps>(verts[3], disp_offset).disps, nullptr);
    EXPECT_EQ(data<MDisps>(verts[3], disp_offset).hidden, nullptr);
  }
  for (int i = 0; i < 2; i++) {
    EXPECT_NE(data<MDeformVert>(output_verts[0][i], deform_offset).dw,
              data<MDeformVert>(output_verts[1][i], deform_offset).dw);
    EXPECT_NE(data<MDisps>(output_verts[0][i], disp_offset).disps,
              data<MDisps>(output_verts[1][i], disp_offset).disps);
    EXPECT_NE(data<MDisps>(output_verts[0][i], disp_offset).hidden,
              data<MDisps>(output_verts[1][i], disp_offset).hidden);
  }
  EXPECT_NE(data<GridPaintMask>(output_verts[0][0], mask_offset).data,
            data<GridPaintMask>(output_verts[1][0], mask_offset).data);
  data<MDisps>(source[0], disp_offset).disps[0][0] = 123;
  data<MDisps>(output_verts[0][0], disp_offset).disps[0][0] = 456;
  snapshot_free(snapshot);
  plan_free(plan);
  mesh_free(first);
  mesh_free(bm);
  EXPECT_EQ(load_float_bits(data<MDisps>(output_verts[1][0], disp_offset).disps), 0x80000000);
  EXPECT_EQ(data<MDisps>(output_verts[1][2], disp_offset).hidden[0], 0xfedcba98);
  BMGeometrySnapshot *remaining = snapshot_create(second);
  ASSERT_NE(remaining, nullptr);
  EXPECT_FALSE(encode(remaining).is_empty());
}

TEST_F(BMeshSnapshotIOTest, PreparedInputOutlivesCallerBufferSourceAndSnapshot)
{
  BMesh *bm = mesh_create();
  BM_data_layer_add_named(bm, &bm->vdata, CD_PROP_FLOAT, "bits");
  BMVert *vert = vert_add(bm, 12);
  const int offset = CustomData_get_offset(&bm->vdata, CD_PROP_FLOAT);
  store_float_bits(BM_ELEM_CD_GET_VOID_P(vert, offset), 0, 0x7fc12345);
  BMGeometrySnapshot *snapshot = snapshot_create(bm);
  ASSERT_NE(snapshot, nullptr);
  Array<std::byte, 0> caller_buffer = encode(snapshot);
  const Array<std::byte, 0> expected(caller_buffer);
  BMGeometryDecodePlan *plan = prepare(Array<std::byte, 0>(caller_buffer.as_span()));
  ASSERT_NE(plan, nullptr);
  /* The parent may reuse its read buffer after handing off an independently owned section. This
   * tests byte ownership, not the parent's separate file identity/digest protocol. */
  caller_buffer.fill(std::byte(0));
  snapshot_free(snapshot);
  mesh_free(bm);
  BMesh *decoded = decode(*plan);
  ASSERT_NE(decoded, nullptr);
  ASSERT_EQ(decoded->totvert, 1);
  EXPECT_EQ(first_vert(decoded)->co[0], 12);
  EXPECT_EQ(load_float_bits(BM_ELEM_CD_GET_VOID_P(first_vert(decoded), offset)), 0x7fc12345);
  BMGeometrySnapshot *fresh = snapshot_create(decoded);
  ASSERT_NE(fresh, nullptr);
  EXPECT_EQ_SPAN<std::byte>(expected, encode(fresh));
}

TEST_F(BMeshSnapshotIOTest, TopologySchemaSelectionAndDirtySourceStateRemainExact)
{
  BMesh *bm = mesh_create(true);
  BM_data_layer_add_named(bm, &bm->vdata, CD_PROP_FLOAT, "shared");
  BM_data_layer_add_named(bm, &bm->vdata, CD_PROP_FLOAT, "temporary");
  BM_data_layer_add_named(bm, &bm->edata, CD_PROP_FLOAT, "shared");
  BM_data_layer_add_named(bm, &bm->pdata, CD_PROP_FLOAT, "shared");
  BM_data_layer_add_named(bm, &bm->ldata, CD_PROP_FLOAT, "shared");
  BM_data_layer_add_named(bm, &bm->vdata, CD_PROP_INT32, ".sculpt_dyntopo_node_id_vertex");
  BM_data_layer_add_named(bm, &bm->pdata, CD_PROP_INT32, ".sculpt_dyntopo_node_id_face");
  BM_data_layer_add(bm, &bm->vdata, CD_ORIGINDEX);
  BM_data_layer_add_named(bm, &bm->vdata, CD_SHAPEKEY, "Basis");
  BM_data_layer_add_named(bm, &bm->vdata, CD_SHAPEKEY, "Raised");
  BM_data_layer_add(bm, &bm->vdata, CD_BM_ELEM_PYPTR);
  const int v_offset = CustomData_get_offset_named(&bm->vdata, CD_PROP_FLOAT, "shared");
  const int temp_offset = CustomData_get_offset_named(&bm->vdata, CD_PROP_FLOAT, "temporary");
  const int e_offset = CustomData_get_offset_named(&bm->edata, CD_PROP_FLOAT, "shared");
  const int f_offset = CustomData_get_offset_named(&bm->pdata, CD_PROP_FLOAT, "shared");
  const int l_offset = CustomData_get_offset_named(&bm->ldata, CD_PROP_FLOAT, "shared");
  const int orig_offset = CustomData_get_offset(&bm->vdata, CD_ORIGINDEX);
  const int vert_node_offset = CustomData_get_offset_named(
      &bm->vdata, CD_PROP_INT32, ".sculpt_dyntopo_node_id_vertex");
  const int face_node_offset = CustomData_get_offset_named(
      &bm->pdata, CD_PROP_INT32, ".sculpt_dyntopo_node_id_face");
  const int key_first = CustomData_get_layer_index(&bm->vdata, CD_SHAPEKEY);
  bm->vdata.layers[CustomData_get_named_layer_index(&bm->vdata, CD_PROP_FLOAT, "shared")].flag =
      CD_FLAG_NOCOPY;
  bm->vdata.layers[CustomData_get_named_layer_index(&bm->vdata, CD_PROP_FLOAT, "temporary")].flag =
      CD_FLAG_TEMPORARY;
  bm->vdata.layers[key_first].uid = 19;
  bm->vdata.layers[key_first + 1].uid = 103;
  CustomData_set_layer_active(&bm->vdata, CD_SHAPEKEY, 1);
  CustomData_set_layer_render(&bm->vdata, CD_SHAPEKEY, 1);
  bm->ldata.external = MEM_new<CustomDataExternal>(__func__);
  STRNCPY(bm->ldata.external->filepath, "//owned-geometry.btx");
  std::array<BMVert *, 8> verts;
  verts[0] = vert_add(bm, 0);
  BMVert *removed = vert_add(bm, -99);
  verts[2] = vert_add(bm, 2);
  BM_vert_kill(bm, removed);
  verts[1] = vert_add(bm, 1);
  for (int i = 3; i < 8; i++) {
    verts[i] = vert_add(bm, i);
  }
  /* Three triangles share an edge. A polygon fixes a distinct first-corner/cycle order. */
  const std::array<std::array<int, 3>, 3> triangle_indices = {{{0, 1, 2}, {1, 0, 3}, {0, 1, 4}}};
  Vector<BMFace *> faces;
  for (const auto &indices : triangle_indices) {
    BMVert *corners[3] = {verts[indices[0]], verts[indices[1]], verts[indices[2]]};
    faces.append(BM_face_create_verts(bm, corners, 3, nullptr, BM_CREATE_NOP, true));
  }
  BMVert *polygon[5] = {verts[4], verts[1], verts[5], verts[0], verts[3]};
  faces.append(BM_face_create_verts(bm, polygon, 5, nullptr, BM_CREATE_NOP, true));
  /* A distinct duplicate endpoint edge, a wire edge, and a loose vertex must survive too. */
  BMEdge *duplicate = BM_edge_create(bm, verts[0], verts[1], nullptr, BM_CREATE_NOP);
  BMEdge *wire_edge = BM_edge_create(bm, verts[5], verts[6], nullptr, BM_CREATE_NOP);
  ASSERT_NE(duplicate, nullptr);
  ASSERT_NE(wire_edge, nullptr);
  const BMEdge *shared_edge = BM_FACE_FIRST_LOOP(faces[0])->e;
  ASSERT_NE(duplicate, shared_edge);
  BM_mesh_elem_table_ensure(bm, BM_VERT | BM_EDGE | BM_FACE);
  BMVert **vtable = bm->vtable;
  BMEdge **etable = bm->etable;
  BMFace **ftable = bm->ftable;
  Vector<BMVert *> vert_order;
  Vector<BMEdge *> edge_order;
  Vector<BMHeader *> headers;
  BMIter iter;
  BMVert *vert;
  int index;
  BM_ITER_MESH_INDEX (vert, &iter, bm, BM_VERTS_OF_MESH, index) {
    vert_order.append(vert);
    headers.append(&vert->head);
    store_float_bits(vert->no, 0, 0x80000000);
    store_float_bits(vert->no, 1, 0x7fc12345);
    store_float_bits(vert->no, 2, 0x3f800000);
    store_float_bits(BM_ELEM_CD_GET_VOID_P(vert, v_offset), 0, float_patterns[index]);
    BM_ELEM_CD_SET_FLOAT(vert, temp_offset, index + 0.25f);
    BM_ELEM_CD_SET_INT(vert, orig_offset, 100 + index);
    BM_ELEM_CD_SET_INT(vert, vert_node_offset, 700 + index);
    data<float3>(vert, bm->vdata.layers[key_first].offset) = float3(index, 2, 3);
    data<float3>(vert, bm->vdata.layers[key_first + 1].offset) = float3(index, 4, 5);
  }
  BMEdge *edge;
  BM_ITER_MESH_INDEX (edge, &iter, bm, BM_EDGES_OF_MESH, index) {
    edge_order.append(edge);
    headers.append(&edge->head);
    BM_ELEM_CD_SET_FLOAT(edge, e_offset, index + 10.5f);
  }
  for (const int i : faces.index_range()) {
    BMFace *face = faces[i];
    headers.append(&face->head);
    face->mat_nr = short(11 + i);
    face->no[0] = 3;
    face->no[1] = 4;
    face->no[2] = 5;
    face->head.hflag = BM_ELEM_SMOOTH | BM_ELEM_TAG;
    BM_ELEM_CD_SET_FLOAT(face, f_offset, i + 20.75f);
    BM_ELEM_CD_SET_INT(face, face_node_offset, 900 + i);
    BMLoop *loop = BM_FACE_FIRST_LOOP(face);
    for (int corner = 0; corner < face->len; corner++, loop = loop->next) {
      headers.append(&loop->head);
      loop->head.hflag = BM_ELEM_SELECT_UV | BM_ELEM_SELECT_UV_EDGE | BM_ELEM_TAG;
      BM_ELEM_CD_SET_FLOAT(loop, l_offset, i * 10 + corner + 30.125f);
    }
  }
  vert_order[0]->head.hflag = BM_ELEM_SELECT | BM_ELEM_HIDDEN | BM_ELEM_TAG;
  edge_order[0]->head.hflag = BM_ELEM_SELECT | BM_ELEM_HIDDEN | BM_ELEM_SEAM;
  faces[1]->head.hflag = BM_ELEM_SELECT | BM_ELEM_HIDDEN;
  bm->totvertsel = bm->totedgesel = bm->totfacesel = 1;
  bm->act_face = faces[3];
  bm->selectmode = SCE_SELECT_VERTEX | SCE_SELECT_EDGE;
  bm->shapenr = 2;
  bm->uv_select_sync_valid = true;
  BM_select_history_store_notest(bm, vert_order[0]);
  BM_select_history_store_notest(bm, edge_order[0]);
  BM_select_history_store_notest(bm, vert_order[0]);
  BM_select_history_store_notest(bm, faces[3]);
  for (const int i : headers.index_range()) {
    headers[i]->index = -1000 - i;
  }
  bm->elem_index_dirty = BM_ALL;
  bm->elem_table_dirty = BM_VERT | BM_EDGE | BM_FACE;
  BMGeometrySnapshot *snapshot = snapshot_create(bm);
  ASSERT_NE(snapshot, nullptr);
  const Array<std::byte, 0> bytes = encode(snapshot);
  BMGeometryDecodePlan *plan = prepare(bytes);
  ASSERT_NE(plan, nullptr);
  const BMGeometryDecodeInfo &info = BM_geometry_snapshot_decode_info(*plan);
  EXPECT_EQ(info.verts_num, 8);
  EXPECT_EQ(info.faces_num, 4);
  EXPECT_EQ(info.loops_num, 14);
  EXPECT_FALSE(info.all_faces_triangular);
  BMesh *decoded = decode(*plan);
  ASSERT_NE(decoded, nullptr);
  EXPECT_TRUE(BM_geometry_snapshot_equals(snapshot, decoded));
  EXPECT_TRUE(decoded->use_toolflags);
  EXPECT_TRUE(decoded->uv_select_sync_valid);
  EXPECT_EQ(decoded->selectmode, SCE_SELECT_VERTEX | SCE_SELECT_EDGE);
  EXPECT_EQ(decoded->shapenr, 2);
  EXPECT_EQ(decoded->totvertsel, 1);
  EXPECT_EQ(decoded->totedgesel, 1);
  EXPECT_EQ(decoded->totfacesel, 1);
  EXPECT_EQ(decoded->py_handle, nullptr);
  EXPECT_EQ(CustomData_get_offset(&decoded->vdata, CD_BM_ELEM_PYPTR), -1);
  const int decoded_key = CustomData_get_layer_index(&decoded->vdata, CD_SHAPEKEY);
  ASSERT_GE(decoded_key, 0);
  ASSERT_LT(decoded_key + 1, decoded->vdata.totlayer);
  EXPECT_EQ(decoded->vdata.layers[decoded_key].uid, 19);
  EXPECT_EQ(decoded->vdata.layers[decoded_key + 1].uid, 103);
  EXPECT_EQ(decoded->vdata.layers[decoded_key].active, 1);
  EXPECT_EQ(decoded->vdata.layers[decoded_key].active_rnd, 1);
  EXPECT_EQ(decoded->vdata
                .layers[CustomData_get_named_layer_index(&decoded->vdata, CD_PROP_FLOAT, "shared")]
                .flag,
            CD_FLAG_NOCOPY);
  EXPECT_EQ(
      decoded->vdata
          .layers[CustomData_get_named_layer_index(&decoded->vdata, CD_PROP_FLOAT, "temporary")]
          .flag,
      CD_FLAG_TEMPORARY);
  ASSERT_NE(decoded->ldata.external, nullptr);
  EXPECT_NE(decoded->ldata.external, bm->ldata.external);
  EXPECT_STREQ(decoded->ldata.external->filepath, "//owned-geometry.btx");
  BM_mesh_elem_table_ensure(decoded, BM_VERT | BM_EDGE | BM_FACE);
  for (int i = 0; i < 8; i++) {
    const BMVert *copy = decoded->vtable[i];
    EXPECT_EQ(float3(copy->co), float3(vert_order[i]->co));
    EXPECT_EQ(load_float_bits(copy->no), 0x80000000);
    EXPECT_EQ(load_float_bits(copy->no, 1), 0x7fc12345);
    EXPECT_EQ(load_float_bits(BM_ELEM_CD_GET_VOID_P(copy, v_offset)), float_patterns[i]);
    EXPECT_EQ(BM_ELEM_CD_GET_FLOAT(copy, temp_offset), i + 0.25f);
    EXPECT_EQ(BM_ELEM_CD_GET_INT(copy, orig_offset), 100 + i);
    EXPECT_EQ(BM_ELEM_CD_GET_INT(copy, vert_node_offset), 700 + i);
    EXPECT_EQ(data<float3>(copy, decoded->vdata.layers[decoded_key].offset), float3(i, 2, 3));
    EXPECT_EQ(data<float3>(copy, decoded->vdata.layers[decoded_key + 1].offset), float3(i, 4, 5));
  }
  EXPECT_EQ(decoded->vtable[0]->head.hflag, BM_ELEM_SELECT | BM_ELEM_HIDDEN | BM_ELEM_TAG);
  for (const int i : edge_order.index_range()) {
    EXPECT_EQ(decoded->etable[i]->v1,
              decoded->vtable[vert_order.first_index_of(edge_order[i]->v1)]);
    EXPECT_EQ(decoded->etable[i]->v2,
              decoded->vtable[vert_order.first_index_of(edge_order[i]->v2)]);
    EXPECT_EQ(BM_ELEM_CD_GET_FLOAT(decoded->etable[i], e_offset), i + 10.5f);
  }
  for (const int i : faces.index_range()) {
    BMFace *copy = decoded->ftable[i];
    EXPECT_EQ(copy->len, i == 3 ? 5 : 3);
    EXPECT_EQ(copy->mat_nr, 11 + i);
    EXPECT_EQ(BM_ELEM_CD_GET_INT(copy, face_node_offset), 900 + i);
    EXPECT_EQ(float3(copy->no), float3(3, 4, 5));
    BMLoop *loop = BM_FACE_FIRST_LOOP(copy);
    BMLoop *original = BM_FACE_FIRST_LOOP(faces[i]);
    for (int corner = 0; corner < copy->len;
         corner++, loop = loop->next, original = original->next)
    {
      EXPECT_EQ(loop->v, decoded->vtable[vert_order.first_index_of(original->v)]);
      EXPECT_EQ(loop->e, decoded->etable[edge_order.first_index_of(original->e)]);
      EXPECT_EQ(loop->head.hflag, BM_ELEM_SELECT_UV | BM_ELEM_SELECT_UV_EDGE | BM_ELEM_TAG);
      EXPECT_EQ(BM_ELEM_CD_GET_FLOAT(loop, l_offset), i * 10 + corner + 30.125f);
    }
  }
  EXPECT_EQ(decoded->act_face, decoded->ftable[3]);
  const std::array<BMHeader *, 4> history = {&decoded->vtable[0]->head,
                                             &decoded->etable[0]->head,
                                             &decoded->vtable[0]->head,
                                             &decoded->ftable[3]->head};
  BMEditSelection *selection = static_cast<BMEditSelection *>(decoded->selected.first);
  for (BMHeader *element : history) {
    ASSERT_NE(selection, nullptr);
    EXPECT_EQ(&selection->ele->head, element);
    selection = selection->next;
  }
  EXPECT_EQ(selection, nullptr);
  EXPECT_EQ_SPAN<std::byte>(bytes, encode(snapshot));
  EXPECT_EQ(bm->vtable, vtable);
  EXPECT_EQ(bm->etable, etable);
  EXPECT_EQ(bm->ftable, ftable);
  EXPECT_EQ(bm->elem_index_dirty, BM_ALL);
  EXPECT_EQ(bm->elem_table_dirty, BM_VERT | BM_EDGE | BM_FACE);
  for (const int i : headers.index_range()) {
    EXPECT_EQ(headers[i]->index, -1000 - i);
  }
  for (const int i : vert_order.index_range()) {
    EXPECT_EQ(bm->vtable[i], vert_order[i]);
    EXPECT_EQ(load_float_bits(BM_ELEM_CD_GET_VOID_P(vert_order[i], v_offset)), float_patterns[i]);
  }
}

TEST_F(BMeshSnapshotIOTest, SinkRejectionLeavesSnapshotAndSourceReusable)
{
  BMesh *bm = mesh_create();
  BM_data_layer_add_named(bm, &bm->vdata, CD_PROP_FLOAT, "value");
  BMFace *face = triangle_add(bm);
  BMVert *vert = BM_FACE_FIRST_LOOP(face)->v;
  vert->head.index = -771;
  bm->elem_index_dirty = BM_ALL;
  BMGeometrySnapshot *snapshot = snapshot_create(bm);
  ASSERT_NE(snapshot, nullptr);
  const Array<std::byte, 0> complete = encode(snapshot);
  const WireOffsets wire = inspect_valid_wire(complete);
  for (const size_t limit : {size_t(0),
                             size_t(1),
                             wire_header_size - 1,
                             wire.vertices,
                             wire.edges + 1,
                             wire.faces[0] + 1,
                             wire.payload + 1,
                             size_t(complete.size() - 1)})
  {
    SCOPED_TRACE(limit);
    Vector<std::byte> partial;
    bool rejected = false;
    const char *error = nullptr;
    const bool success = BM_geometry_snapshot_encode(
        snapshot,
        unlimited(),
        [&](const Span<std::byte> chunk) {
          EXPECT_FALSE(rejected) << "The encoder called a failed sink again";
          const size_t count = std::min(size_t(chunk.size()), limit - size_t(partial.size()));
          partial.extend(chunk.take_front(count));
          if (count != chunk.size()) {
            rejected = true;
            return false;
          }
          return true;
        },
        &error);
    EXPECT_FALSE(success);
    EXPECT_TRUE(rejected);
    EXPECT_NE(error, nullptr);
    EXPECT_EQ(partial.size(), limit);
    EXPECT_EQ_SPAN<std::byte>(complete.as_span().take_front(limit), partial);
    EXPECT_EQ(vert->head.index, -771);
    EXPECT_EQ(bm->elem_index_dirty, BM_ALL);
    EXPECT_TRUE(BM_geometry_snapshot_equals(snapshot, bm));
    EXPECT_EQ_SPAN<std::byte>(complete, encode(snapshot));
  }
}

TEST_F(BMeshSnapshotIOTest, MalformedHeadersSchemasAndIncidencesAreRejected)
{
  BMesh *bm = mesh_create();
  BM_data_layer_add_named(bm, &bm->vdata, CD_PROP_BOOL, "valid_name");
  BMFace *face = triangle_add(bm);
  BM_select_history_store_notest(bm, BM_FACE_FIRST_LOOP(face)->v);
  bm->act_face = face;
  BMGeometrySnapshot *snapshot = snapshot_create(bm);
  ASSERT_NE(snapshot, nullptr);
  const Array<std::byte, 0> bytes = encode(snapshot);
  const WireOffsets wire = inspect_valid_wire(bytes);
  ASSERT_EQ(wire.layers[0].size(), 1);
  const size_t layer = wire.layers[0][0];
  struct Mutation {
    size_t offset;
    int width;
    uint64_t value;
    const char *reason;
  };
  const Mutation mutations[] = {
      {0, 1, 'X', "magic"},
      {8, 4, 2, "version"},
      {12, 4, 4, "unknown header flags"},
      {16, 4, UINT32_MAX, "vertex count exceeds native int"},
      {28, 4, 4, "corner sum mismatch"},
      {32, 4, 4, "selected count exceeds domain"},
      {44, 4, UINT32_MAX, "history extent overflow"},
      {48, 4, 1, "active face out of range"},
      {58, 4, UINT32_MAX, "schema extent overflow"},
      {62, 1, 2, "external presence is not boolean"},
      {layer, 2, 53, "unknown type tag"},
      {layer + 18, 2, 68, "name exceeds native capacity"},
      {layer + 20, 1, 0, "embedded NUL in layer name"},
      {wire.edges, 4, 3, "edge vertex out of range"},
      {wire.edges + 4, 4, read_le(bytes, wire.edges, 4), "self edge"},
      {wire.faces[0], 4, 1, "face too short"},
      {wire.faces[0], 4, UINT32_MAX, "face length overflow"},
      {wire.loops[0], 4, 3, "corner vertex out of range"},
      {wire.loops[1], 4, read_le(bytes, wire.loops[0], 4), "repeated face corner"},
      {wire.loops[0] + 4, 4, 3, "corner edge out of range"},
      {wire.loops[0] + 4,
       4,
       (read_le(bytes, wire.loops[0] + 4, 4) + 1) % 3,
       "corner edge has wrong endpoints"},
      {wire.history, 1, BM_LOOP, "invalid selection domain"},
      {wire.history + 1, 4, 3, "selection ordinal out of range"},
      {wire.payload, 1, 2, "noncanonical boolean value"},
  };
  for (const Mutation &mutation : mutations) {
    SCOPED_TRACE(mutation.reason);
    Array<std::byte, 0> malformed(bytes);
    write_le(malformed, mutation.offset, mutation.width, mutation.value);
    expect_prepare_refused(std::move(malformed), unlimited());
  }
  /* Every proper prefix crosses a validated header, schema, topology, history or payload extent.
   */
  for (const int64_t length : bytes.index_range()) {
    SCOPED_TRACE(length);
    expect_prepare_refused(Array<std::byte, 0>(bytes.as_span().take_front(length)), unlimited());
  }
  Vector<std::byte> trailing;
  trailing.extend(bytes);
  trailing.append(std::byte(0));
  expect_prepare_refused(Array<std::byte, 0>(trailing.as_span()), unlimited());
  Array<std::byte, 0> combined_count(bytes);
  write_le(combined_count, 16, 4, INT32_MAX);
  write_le(combined_count, 24, 4, 1);
  expect_prepare_refused(std::move(combined_count), unlimited());
  EXPECT_TRUE(BM_geometry_snapshot_equals(snapshot, bm));
}

TEST_F(BMeshSnapshotIOTest, DynamicPresenceCountsAndGridArithmeticAreValidated)
{
  for (const eCustomDataType type : {CD_MDEFORMVERT, CD_MDISPS, CD_GRID_PAINT_MASK}) {
    SCOPED_TRACE(int(type));
    BMesh *bm = mesh_create();
    BM_data_layer_add(bm, &bm->vdata, type);
    BMVert *vert = vert_add(bm);
    for (const LayerCase &test : layer_cases) {
      if (test.type == type) {
        fill_layer_value(test,
                         BM_ELEM_CD_GET_VOID_P(vert, CustomData_get_offset(&bm->vdata, type)));
      }
    }
    BMGeometrySnapshot *snapshot = snapshot_create(bm);
    ASSERT_NE(snapshot, nullptr);
    const Array<std::byte, 0> bytes = encode(snapshot);
    const size_t payload = inspect_valid_wire(bytes).payload;
    Array<std::byte, 0> invalid_presence(bytes);
    write_le(invalid_presence, payload + (type == CD_GRID_PAINT_MASK ? 4 : 8), 1, 0xff);
    expect_prepare_refused(std::move(invalid_presence), unlimited());
    Array<std::byte, 0> invalid_count(bytes);
    write_le(invalid_count, payload, 4, UINT32_MAX);
    expect_prepare_refused(std::move(invalid_count), unlimited());
    if (type == CD_MDEFORMVERT) {
      Array<std::byte, 0> missing_weights(bytes);
      write_le(missing_weights, payload + 8, 1, 0);
      expect_prepare_refused(std::move(missing_weights), unlimited());
    }
    if (type == CD_GRID_PAINT_MASK) {
      for (const uint32_t level : {0u, 33u, 64u, UINT32_MAX}) {
        Array<std::byte, 0> invalid_level(bytes);
        write_le(invalid_level, payload, 4, level);
        expect_prepare_refused(std::move(invalid_level), unlimited());
      }
    }
  }
}

TEST_F(BMeshSnapshotIOTest, ActiveAndRenderIndicesAreBoundedByEachSameTypeRun)
{
  const std::array<const char *, 3> names = {"first", "second", "third"};
  for (const int float_layers : {1, 3}) {
    BMesh *bm = mesh_create();
    for (int i = 0; i < float_layers; i++) {
      BM_data_layer_add_named(bm, &bm->vdata, CD_PROP_FLOAT, names[i]);
    }
    BM_data_layer_add_named(bm, &bm->vdata, CD_PROP_INT32, "other_type");
    vert_add(bm);
    BMGeometrySnapshot *snapshot = snapshot_create(bm);
    ASSERT_NE(snapshot, nullptr);
    const Array<std::byte, 0> bytes = encode(snapshot);
    const WireOffsets wire = inspect_valid_wire(bytes);
    ASSERT_EQ(wire.layers[0].size(), float_layers + 1);
    ASSERT_NE(prepare(bytes), nullptr);
    for (int descriptor = 0; descriptor <= float_layers; descriptor++) {
      const size_t start = wire.layers[0][descriptor];
      const int run_length = descriptor < float_layers ? float_layers : 1;
      ASSERT_EQ(read_le(bytes, start, 2), descriptor < float_layers ? 10 : 11);
      ASSERT_EQ(read_le(bytes, start + 10, 4), 0);
      ASSERT_EQ(read_le(bytes, start + 14, 4), 0);
      for (const size_t field : {size_t(10), size_t(14)}) {
        for (const int invalid : {-1,
                                  std::numeric_limits<int>::min(),
                                  run_length,
                                  run_length + 1,
                                  std::numeric_limits<int>::max()})
        {
          SCOPED_TRACE(testing::Message()
                       << float_layers << "/" << descriptor << "/" << field << "/" << invalid);
          Array<std::byte, 0> malformed(bytes);
          write_le(malformed, start + field, 4, uint32_t(invalid));
          expect_prepare_refused(std::move(malformed),
                                 unlimited(),
                                 "The BMesh section schema or aligned native layout is invalid");
        }
      }
    }
  }
}

TEST_F(BMeshSnapshotIOTest, ValidNonzeroAndDistinctPerDescriptorIndicesRemainExact)
{
  BMesh *bm = mesh_create();
  for (const char *name : {"first", "second", "third"}) {
    BM_data_layer_add_named(bm, &bm->vdata, CD_PROP_FLOAT, name);
  }
  BM_data_layer_add_named(bm, &bm->vdata, CD_PROP_INT32, "other_type");
  vert_add(bm);
  ASSERT_EQ(bm->vdata.totlayer, 4);
  CustomData_set_layer_active(&bm->vdata, CD_PROP_FLOAT, 1);
  CustomData_set_layer_render(&bm->vdata, CD_PROP_FLOAT, 2);
  for (const bool distinct_indices : {false, true}) {
    if (distinct_indices) {
      /* Bounds apply separately to each descriptor; agreement within a run is not required. */
      bm->vdata.layers[1].active = 0;
      bm->vdata.layers[1].active_rnd = 1;
    }
    BMGeometrySnapshot *snapshot = snapshot_create(bm);
    ASSERT_NE(snapshot, nullptr);
    BMGeometryDecodePlan *plan = prepare(encode(snapshot));
    ASSERT_NE(plan, nullptr);
    BMesh *decoded = decode(*plan);
    ASSERT_NE(decoded, nullptr);
    ASSERT_EQ(decoded->vdata.totlayer, 4);
    for (int i = 0; i < 3; i++) {
      EXPECT_EQ(decoded->vdata.layers[i].type, CD_PROP_FLOAT);
      EXPECT_EQ(decoded->vdata.layers[i].active, distinct_indices && i == 1 ? 0 : 1);
      EXPECT_EQ(decoded->vdata.layers[i].active_rnd, distinct_indices && i == 1 ? 1 : 2);
    }
    EXPECT_EQ(decoded->vdata.layers[3].type, CD_PROP_INT32);
    EXPECT_EQ(decoded->vdata.layers[3].active, 0);
    EXPECT_EQ(decoded->vdata.layers[3].active_rnd, 0);
    EXPECT_EQ(CustomData_get_active_layer_index(&decoded->vdata, CD_PROP_FLOAT), 1);
    EXPECT_EQ(CustomData_get_render_layer_index(&decoded->vdata, CD_PROP_FLOAT), 2);
    EXPECT_STREQ(CustomData_get_active_layer_name(&decoded->vdata, CD_PROP_FLOAT), "second");
    EXPECT_STREQ(CustomData_get_render_layer_name(&decoded->vdata, CD_PROP_FLOAT), "third");
    EXPECT_TRUE(BM_geometry_snapshot_equals(snapshot, decoded));
  }
}

TEST_F(BMeshSnapshotIOTest, SourceBridgeRejectsInvalidIndicesWithoutSinkCallsOrMutation)
{
  const std::array<const char *, 3> names = {"first", "second", "third"};
  for (const int float_layers : {1, 3}) {
    BMesh *bm = mesh_create();
    for (int i = 0; i < float_layers; i++) {
      BM_data_layer_add_named(bm, &bm->vdata, CD_PROP_FLOAT, names[i]);
    }
    BM_data_layer_add_named(bm, &bm->vdata, CD_PROP_INT32, "other_type");
    BMVert *vert = vert_add(bm, 6);
    BM_mesh_elem_table_ensure(bm, BM_VERT);
    BMVert **table = bm->vtable;
    vert->head.index = -91;
    bm->elem_index_dirty = BM_ALL;
    bm->elem_table_dirty = BM_VERT;
    CustomDataLayer *layers = bm->vdata.layers;
    BLI_mempool *pool = bm->vdata.pool;
    BMGeometrySnapshot *snapshot = snapshot_create(bm);
    ASSERT_NE(snapshot, nullptr);
    const Array<std::byte, 0> expected = encode(snapshot);
    ASSERT_EQ(bm->vdata.totlayer, float_layers + 1);
    for (int descriptor = 0; descriptor <= float_layers; descriptor++) {
      const int run_length = descriptor < float_layers ? float_layers : 1;
      for (const bool render : {false, true}) {
        int &field = render ? layers[descriptor].active_rnd : layers[descriptor].active;
        const int original = field;
        for (const int invalid : {-1,
                                  std::numeric_limits<int>::min(),
                                  run_length,
                                  run_length + 1,
                                  std::numeric_limits<int>::max()})
        {
          SCOPED_TRACE(testing::Message()
                       << float_layers << "/" << descriptor << "/" << render << "/" << invalid);
          field = invalid;
          const Array<CustomDataLayer> before(Span(layers, bm->vdata.totlayer));
          int calls = 0;
          const char *error = nullptr;
          /* Deliberately malformed metadata belongs only to this detached test BMesh. Exercise
           * the same internal bridge as the immutable wrapper without exposing a mutable snapshot.
           */
          const bool accepted = bmesh::snapshot_io::encode_mesh(
              *bm,
              unlimited(),
              [&](Span<std::byte>) {
                calls++;
                return true;
              },
              &error);
          EXPECT_FALSE(accepted);
          EXPECT_STREQ(error, "The BMesh snapshot active or render layer index is invalid");
          EXPECT_EQ(calls, 0);
          EXPECT_EQ(field, invalid);
          EXPECT_EQ(bm->vdata.layers, layers);
          EXPECT_EQ(bm->vdata.pool, pool);
          for (const int i : before.index_range()) {
            EXPECT_EQ(layers[i].type, before[i].type);
            EXPECT_EQ(layers[i].offset, before[i].offset);
            EXPECT_EQ(layers[i].flag, before[i].flag);
            EXPECT_EQ(layers[i].uid, before[i].uid);
            EXPECT_EQ(layers[i].active, before[i].active);
            EXPECT_EQ(layers[i].active_rnd, before[i].active_rnd);
            EXPECT_STREQ(layers[i].name, before[i].name);
          }
          EXPECT_EQ(bm->vtable, table);
          EXPECT_EQ(bm->vtable[0], vert);
          EXPECT_EQ(vert->head.index, -91);
          EXPECT_EQ(vert->co[0], 6);
          EXPECT_EQ(bm->elem_index_dirty, BM_ALL);
          EXPECT_EQ(bm->elem_table_dirty, BM_VERT);
          field = original;
        }
      }
    }
    Vector<std::byte> restored;
    EXPECT_TRUE(bmesh::snapshot_io::encode_mesh(
        *bm,
        unlimited(),
        [&](Span<std::byte> chunk) {
          restored.extend(chunk);
          return true;
        },
        nullptr));
    EXPECT_EQ_SPAN<std::byte>(expected, restored);
  }
}

TEST_F(BMeshSnapshotIOTest, ShapeNumberAdmissionPreservesNonnegativeValuesAndRejectsNegatives)
{
  BMesh *bm = mesh_create();
  BMVert *vert = vert_add(bm, 4);
  for (const int shape_number : {0, 1, 7, std::numeric_limits<int>::max()}) {
    bm->shapenr = shape_number;
    BMGeometrySnapshot *snapshot = snapshot_create(bm);
    ASSERT_NE(snapshot, nullptr);
    const Array<std::byte, 0> bytes = encode(snapshot);
    ASSERT_GE(bytes.size(), wire_header_size);
    EXPECT_EQ(read_le(bytes, 54, 4), uint32_t(shape_number));
    BMGeometryDecodePlan *plan = prepare(bytes);
    ASSERT_NE(plan, nullptr);
    BMesh *decoded = decode(*plan);
    ASSERT_NE(decoded, nullptr);
    EXPECT_EQ(decoded->shapenr, shape_number);
    EXPECT_EQ(CustomData_get_offset(&decoded->vdata, CD_SHAPEKEY), -1);
    /* The codec has no BASE Key binding; it must not invent a local upper bound. */
    EXPECT_EQ(bm->shapenr, shape_number);
    for (const int invalid : {-1, std::numeric_limits<int>::min()}) {
      Array<std::byte, 0> malformed(bytes);
      write_le(malformed, 54, 4, uint32_t(invalid));
      expect_prepare_refused(std::move(malformed),
                             unlimited(),
                             "The BMesh section header or native element counts are invalid");
      bm->shapenr = invalid;
      const char *error = nullptr;
      int calls = 0;
      EXPECT_FALSE(bmesh::snapshot_io::encode_mesh(
          *bm,
          unlimited(),
          [&](Span<std::byte>) {
            calls++;
            return true;
          },
          &error));
      EXPECT_STREQ(error, "The BMesh snapshot cannot supply valid native codec counts");
      EXPECT_EQ(calls, 0);
      EXPECT_EQ(bm->shapenr, invalid);
      EXPECT_EQ(vert->co[0], 4);
      bm->shapenr = shape_number;
    }
  }
}

TEST_F(BMeshSnapshotIOTest, NativeSchemaDropAndRenameRefusalsAreIsolated)
{
  enum class Collision { SameTypeName, SingletonType, CrossPropertyName };
  for (const Collision collision :
       {Collision::SameTypeName, Collision::SingletonType, Collision::CrossPropertyName})
  {
    SCOPED_TRACE(int(collision));
    BMesh *bm = mesh_create();
    BM_data_layer_add_named(bm, &bm->vdata, CD_PROP_FLOAT, "a");
    BM_data_layer_add_named(bm,
                            &bm->vdata,
                            collision == Collision::CrossPropertyName ? CD_PROP_INT32 :
                                                                        CD_PROP_FLOAT,
                            "b");
    BMGeometrySnapshot *snapshot = snapshot_create(bm);
    ASSERT_NE(snapshot, nullptr);
    const Array<std::byte, 0> bytes = encode(snapshot);
    const WireOffsets wire = inspect_valid_wire(bytes);
    ASSERT_EQ(wire.layers[0].size(), 2);
    ASSERT_EQ(wire.payload, bytes.size());
    for (const size_t count_offset : {size_t(16), size_t(20), size_t(24), size_t(28)}) {
      ASSERT_EQ(read_le(bytes, count_offset, 4), 0);
    }
    const size_t first = wire.layers[0][0], second = wire.layers[0][1];
    ASSERT_EQ(read_le(bytes, first, 2), 10);
    ASSERT_EQ(read_le(bytes, second, 2), collision == Collision::CrossPropertyName ? 11 : 10);
    for (const size_t descriptor : {first, second}) {
      ASSERT_EQ(read_le(bytes, descriptor + 10, 4), 0);
      ASSERT_EQ(read_le(bytes, descriptor + 14, 4), 0);
      ASSERT_EQ(read_le(bytes, descriptor + 18, 2), 1);
    }
    ASSERT_EQ(read_le(bytes, first + 20, 1), 'a');
    ASSERT_EQ(read_le(bytes, second + 20, 1), 'b');
    ASSERT_NE(prepare(bytes), nullptr);
    Array<std::byte, 0> malformed(bytes);
    if (collision == Collision::SingletonType) {
      /* ORIGINDEX is tag 7 and native singleton storage. Both names remain distinct, both
       * descriptors are sorted, and the zero indices are valid for the two-descriptor wire run. */
      write_le(malformed, first, 2, 7);
      write_le(malformed, second, 2, 7);
      ASSERT_EQ(read_le(malformed, first + 20, 1), 'a');
      ASSERT_EQ(read_le(malformed, second + 20, 1), 'b');
    }
    else {
      write_le(malformed, second + 20, 1, 'a');
      ASSERT_EQ(read_le(malformed, first + 20, 1), read_le(malformed, second + 20, 1));
    }
    ASSERT_LE(read_le(malformed, first, 2), read_le(malformed, second, 2));
    EXPECT_EQ(malformed.size(), bytes.size());
    expect_prepare_refused(std::move(malformed),
                           unlimited(),
                           "The BMesh section native schema would be dropped or renamed");
  }
}

TEST_F(BMeshSnapshotIOTest, NonemptySchemaAndTopologyUseExactScratchReservation)
{
  BMesh *bm = mesh_create();
  BM_data_layer_add_named(bm, &bm->vdata, CD_PROP_FLOAT, "value");
  BM_data_layer_add(bm, &bm->ldata, CD_MDISPS);
  triangle_add(bm);
  BMGeometrySnapshot *snapshot = snapshot_create(bm);
  ASSERT_NE(snapshot, nullptr);
  const Array<std::byte, 0> bytes = encode(snapshot);
  BMGeometryDecodePlan *unbounded = prepare(bytes);
  ASSERT_NE(unbounded, nullptr);
  const BMGeometryDecodeInfo &info = BM_geometry_snapshot_decode_info(*unbounded);
  ASSERT_EQ(info.verts_num, 3);
  ASSERT_EQ(info.faces_num, 1);
  ASSERT_GT(info.decode_scratch_bytes_bound, 0);
  BMGeometryCodecLimits limits = unlimited();
  limits.max_scratch_bytes = info.decode_scratch_bytes_bound;
  Array<std::byte, 0> exact_input(bytes);
  const char *error = nullptr;
  BMGeometryDecodePlan *exact = BM_geometry_snapshot_decode_prepare(
      std::move(exact_input), limits, &error);
  ASSERT_NE(exact, nullptr) << (error ? error : "No exact scratch-limit diagnostic");
  plans_.append(exact);
  EXPECT_TRUE(exact_input.is_empty());
  EXPECT_EQ(BM_geometry_snapshot_decode_info(*exact).decode_scratch_bytes_bound,
            info.decode_scratch_bytes_bound);
  BMesh *decoded = decode(*exact);
  ASSERT_NE(decoded, nullptr);
  EXPECT_TRUE(BM_geometry_snapshot_equals(snapshot, decoded));
  limits.max_scratch_bytes--;
  expect_prepare_refused(bytes, limits);
}

TEST_F(BMeshSnapshotIOTest, ExplicitSectionBackingAndScratchPoliciesAreEnforced)
{
  BMesh *bm = mesh_create();
  triangle_add(bm);
  BMGeometrySnapshot *snapshot = snapshot_create(bm);
  ASSERT_NE(snapshot, nullptr);
  const Array<std::byte, 0> bytes = encode(snapshot);
  BMGeometryDecodePlan *plan = prepare(bytes);
  ASSERT_NE(plan, nullptr);
  const BMGeometryDecodeInfo &info = BM_geometry_snapshot_decode_info(*plan);
  EXPECT_EQ(info.section_bytes, bytes.size());
  EXPECT_TRUE(info.all_faces_triangular);
  EXPECT_GE(info.owned_input_and_plan_bytes, bytes.size());
  EXPECT_GT(info.decoded_backing_bytes_bound, 0);
  BMGeometryCodecLimits limits = unlimited();
  limits.max_section_bytes = bytes.size() - 1;
  expect_prepare_refused(bytes, limits);
  limits = unlimited();
  const size_t required_backing = info.owned_input_and_plan_bytes +
                                  info.decoded_backing_bytes_bound;
  limits.max_backing_bytes = required_backing - 1;
  expect_prepare_refused(bytes, limits);
  limits.max_backing_bytes = required_backing;
  limits.max_section_bytes = bytes.size();
  Array<std::byte, 0> exact_input(bytes);
  const char *error = nullptr;
  BMGeometryDecodePlan *exact = BM_geometry_snapshot_decode_prepare(
      std::move(exact_input), limits, &error);
  ASSERT_NE(exact, nullptr) << (error ? error : "No exact-budget diagnostic");
  plans_.append(exact);
  EXPECT_TRUE(exact_input.is_empty());
  EXPECT_NE(decode(*exact), nullptr);
  /* These independently valid owners cannot both fit the one-owner backing reservation. */
  EXPECT_GT(required_backing + required_backing, limits.max_backing_bytes);
  limits = unlimited();
  limits.max_scratch_bytes = 0;
  expect_prepare_refused(bytes, limits);

  for (const bool section_limit : {false, true}) {
    limits = unlimited();
    if (section_limit) {
      limits.max_section_bytes = bytes.size() - 1;
    }
    else {
      limits.max_scratch_bytes = 0;
    }
    int calls = 0;
    error = nullptr;
    EXPECT_FALSE(BM_geometry_snapshot_encode(
        snapshot,
        limits,
        [&](Span<std::byte>) {
          calls++;
          return true;
        },
        &error));
    EXPECT_EQ(calls, 0);
    EXPECT_NE(error, nullptr);
  }
  limits = unlimited();
  limits.max_section_bytes = bytes.size();
  Vector<std::byte> exact_output;
  EXPECT_TRUE(BM_geometry_snapshot_encode(
      snapshot,
      limits,
      [&](Span<std::byte> chunk) {
        exact_output.extend(chunk);
        return true;
      },
      &error));
  EXPECT_EQ_SPAN<std::byte>(bytes, exact_output);
}

TEST_F(BMeshSnapshotIOTest, PoolSizeAdmissionBoundsCreationGrowthAndReuse)
{
  using OwnedPool = std::unique_ptr<BLI_mempool, decltype(&BLI_mempool_destroy)>;
  for (const unsigned int flags : {unsigned(BLI_MEMPOOL_NOP), unsigned(BLI_MEMPOOL_ALLOW_ITER)}) {
    for (const unsigned int element_size :
         {0u, 1u, 3u, 37u, unsigned(sizeof(BMVert)), unsigned(sizeof(BMFace))})
    {
      for (const unsigned int chunk_size : {1u, 512u, 1024u, 2048u}) {
        const unsigned int peak = chunk_size * 2 + 3;
        SCOPED_TRACE(testing::Message() << flags << "/" << element_size << "/" << chunk_size);
        size_t bound = 123;
        if (!BLI_mempool_create_size_bound(element_size, 0, peak, chunk_size, flags, &bound)) {
          /* Small chunks can have no usable capacity after private chunk overhead. */
          EXPECT_EQ(chunk_size, 1);
          EXPECT_EQ(bound, 0);
          continue;
        }
        OwnedPool pool(BLI_mempool_create(element_size, 0, chunk_size, flags),
                       BLI_mempool_destroy);
        EXPECT_LE(BLI_mempool_allocated_bytes(pool.get()), bound);
        Vector<void *> elements;
        for (unsigned int i = 0; i < peak; i++) {
          elements.append(BLI_mempool_calloc(pool.get()));
          if (i + 1 == chunk_size || i == chunk_size || i + 1 == peak) {
            EXPECT_LE(BLI_mempool_allocated_bytes(pool.get()), bound);
          }
        }
        EXPECT_EQ(BLI_mempool_len(pool.get()), peak);
        for (unsigned int i = 0; i < peak; i += 2) {
          BLI_mempool_free(pool.get(), elements[i]);
        }
        EXPECT_LE(BLI_mempool_allocated_bytes(pool.get()), bound);
        for (unsigned int i = 0; i < peak; i += 2) {
          elements[i] = BLI_mempool_calloc(pool.get());
        }
        EXPECT_EQ(BLI_mempool_len(pool.get()), peak);
        EXPECT_LE(BLI_mempool_allocated_bytes(pool.get()), bound);
      }
    }
  }
}

TEST_F(BMeshSnapshotIOTest, PoolBoundIncludesUnusedOriginalPreallocation)
{
  using OwnedPool = std::unique_ptr<BLI_mempool, decltype(&BLI_mempool_destroy)>;
  constexpr unsigned int chunk = 32;
  for (const unsigned int flags : {unsigned(BLI_MEMPOOL_NOP), unsigned(BLI_MEMPOOL_ALLOW_ITER)}) {
    for (const unsigned int preallocate : {0u, chunk, chunk + 1, chunk * 2, chunk * 2 + 1}) {
      for (const unsigned int peak : {0u, 3u}) {
        SCOPED_TRACE(testing::Message() << flags << "/" << preallocate << "/" << peak);
        size_t bound;
        ASSERT_TRUE(BLI_mempool_create_size_bound(13, preallocate, peak, chunk, flags, &bound));
        OwnedPool pool(BLI_mempool_create(13, preallocate, chunk, flags), BLI_mempool_destroy);
        EXPECT_EQ(BLI_mempool_len(pool.get()), 0);
        EXPECT_LE(BLI_mempool_allocated_bytes(pool.get()), bound);
        if (peak == 0) {
          EXPECT_EQ(BLI_mempool_allocated_bytes(pool.get()), bound);
        }
        for (unsigned int i = 0; i < peak; i++) {
          EXPECT_NE(BLI_mempool_alloc(pool.get()), nullptr);
        }
        EXPECT_LE(BLI_mempool_allocated_bytes(pool.get()), bound);
      }
    }
  }
}

TEST_F(BMeshSnapshotIOTest, PoolOverflowAndFlagRefusalsNeverConstructPools)
{
  const unsigned int max = std::numeric_limits<unsigned int>::max();
  struct Request {
    unsigned int size, preallocate, peak, chunk, flags;
  };
  const Request invalid[] = {
      {1, 0, 0, 0, BLI_MEMPOOL_NOP},
      {1, 0, 0, 512, 1u << 7},
      {max, 0, 1, max, BLI_MEMPOOL_NOP},
      {32, 0, 1, 1u << 27, BLI_MEMPOOL_ALLOW_ITER},
  };
  for (const Request &request : invalid) {
    size_t bound = 12345;
    EXPECT_FALSE(BLI_mempool_create_size_bound(
        request.size, request.preallocate, request.peak, request.chunk, request.flags, &bound));
    EXPECT_EQ(bound, 0);
  }
  size_t large_bound = 0;
  const bool representable = BLI_mempool_create_size_bound(
      sizeof(BMVert), 0, max, 512, BLI_MEMPOOL_ALLOW_ITER, &large_bound);
  if constexpr (sizeof(size_t) >= 8) {
    EXPECT_TRUE(representable);
    EXPECT_GT(large_bound, uint64_t(max));
  }
  else if (!representable) {
    EXPECT_EQ(large_bound, 0);
  }
  /* Deliberately do not allocate any pool for the arithmetic-only boundary above. */
}

TEST_F(BMeshSnapshotIOTest, NativeCountArithmeticAdmitsExactBoundaryWithoutAllocating)
{
  const uint32_t max = std::numeric_limits<int>::max();
  EXPECT_TRUE(bmesh::snapshot_io::counts_valid({0, 0, 0, 0}));
  EXPECT_TRUE(bmesh::snapshot_io::counts_valid({3, 3, 1, 3}));
  EXPECT_TRUE(bmesh::snapshot_io::counts_valid({max, 0, 0, 0}));
  EXPECT_TRUE(bmesh::snapshot_io::counts_valid({max - 1, 3, 1, 3}));
  EXPECT_FALSE(bmesh::snapshot_io::counts_valid({max, 3, 1, 3}));
  for (int domain = 0; domain < 4; domain++) {
    std::array<uint32_t, 4> counts{};
    counts[domain] = max + 1;
    EXPECT_FALSE(bmesh::snapshot_io::counts_valid(counts)) << "domain " << domain;
    counts[domain] = UINT32_MAX;
    EXPECT_FALSE(bmesh::snapshot_io::counts_valid(counts)) << "domain " << domain;
  }
}

#ifdef WITH_GTESTS
TEST_F(BMeshSnapshotIOTest, EachLocalConstructionFaultCleansUpAndLeavesPlanReusable)
{
  BMesh *bm = mesh_create();
  BM_data_layer_add(bm, &bm->vdata, CD_MDEFORMVERT);
  BM_data_layer_add(bm, &bm->ldata, CD_MDISPS);
  BM_data_layer_add(bm, &bm->ldata, CD_GRID_PAINT_MASK);
  BMFace *face = triangle_add(bm);
  const int deform_offset = CustomData_get_offset(&bm->vdata, CD_MDEFORMVERT);
  const int disp_offset = CustomData_get_offset(&bm->ldata, CD_MDISPS);
  const int grid_offset = CustomData_get_offset(&bm->ldata, CD_GRID_PAINT_MASK);
  BMLoop *loop = BM_FACE_FIRST_LOOP(face);
  for (int i = 0; i < 3; i++, loop = loop->next) {
    MDeformVert &deform = data<MDeformVert>(loop->v, deform_offset);
    deform.totweight = 2;
    deform.dw = MEM_new_array_zeroed<MDeformWeight>(2, __func__);
    deform.dw[0] = {3, 0.25f};
    deform.dw[1] = {7, 0.75f};
    MDisps &disp = data<MDisps>(loop, disp_offset);
    disp.totdisp = 9;
    disp.level = 2;
    disp.disps = MEM_new_array_zeroed<float[3]>(9, __func__);
    disp.disps[8][2] = i + 0.5f;
    disp.hidden = BLI_BITMAP_NEW(9, __func__);
    disp.hidden[0] = 0xf0000001;
    GridPaintMask &grid = data<GridPaintMask>(loop, grid_offset);
    grid.level = 2;
    grid.data = MEM_new_array_zeroed<float>(9, __func__);
    grid.data[8] = 0.625f;
  }
  BM_select_history_store_notest(bm, loop->v);
  BM_select_history_store_notest(bm, loop->e);
  BM_select_history_store_notest(bm, loop->v);
  BM_select_history_store_notest(bm, face);
  BMGeometrySnapshot *snapshot = snapshot_create(bm);
  ASSERT_NE(snapshot, nullptr);
  const Array<std::byte, 0> encoded = encode(snapshot);
  BMGeometryDecodePlan *plan = prepare(encoded);
  ASSERT_NE(plan, nullptr);
  const BMGeometryDecodeInfo original_info = BM_geometry_snapshot_decode_info(*plan);
  struct Fault {
    BMGeometryDecodeTestPhase phase;
    uint32_t index;
  };
  const Fault faults[] = {
      {BMGeometryDecodeTestPhase::Schema, 0},
      {BMGeometryDecodeTestPhase::Schema, 1},
      {BMGeometryDecodeTestPhase::Schema, 2},
      {BMGeometryDecodeTestPhase::Schema, 3},
      {BMGeometryDecodeTestPhase::Vertex, 0},
      {BMGeometryDecodeTestPhase::Vertex, 2},
      {BMGeometryDecodeTestPhase::Edge, 0},
      {BMGeometryDecodeTestPhase::Edge, 2},
      {BMGeometryDecodeTestPhase::Face, 0},
      /* Three deform records, then three displacement records, then three grid-mask records. */
      {BMGeometryDecodeTestPhase::Attribute, 0},
      {BMGeometryDecodeTestPhase::Attribute, 2},
      {BMGeometryDecodeTestPhase::Attribute, 3},
      {BMGeometryDecodeTestPhase::Attribute, 5},
      {BMGeometryDecodeTestPhase::Attribute, 6},
      {BMGeometryDecodeTestPhase::Attribute, 8},
      {BMGeometryDecodeTestPhase::History, 0},
      {BMGeometryDecodeTestPhase::History, 3},
  };
  for (const Fault &fault : faults) {
    SCOPED_TRACE(testing::Message() << int(fault.phase) << "/" << fault.index);
    const char *error = nullptr;
    BMesh *failed = BM_geometry_snapshot_decode_with_test_fault(
        *plan, fault.phase, fault.index, &error);
    if (failed) {
      meshes_.append(failed);
    }
    EXPECT_EQ(failed, nullptr);
    EXPECT_NE(error, nullptr);
    const BMGeometryDecodeInfo &after = BM_geometry_snapshot_decode_info(*plan);
    EXPECT_EQ(after.section_bytes, original_info.section_bytes);
    EXPECT_EQ(after.owned_input_and_plan_bytes, original_info.owned_input_and_plan_bytes);
    EXPECT_EQ(after.decoded_backing_bytes_bound, original_info.decoded_backing_bytes_bound);
    BMesh *decoded = decode(*plan);
    ASSERT_NE(decoded, nullptr);
    EXPECT_TRUE(BM_geometry_snapshot_equals(snapshot, decoded));
    mesh_free(decoded);
  }
  EXPECT_EQ_SPAN<std::byte>(encoded, encode(snapshot));
  EXPECT_TRUE(BM_geometry_snapshot_equals(snapshot, bm));
}
#endif

}  // namespace
}  // namespace blender
