/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include <cstring>
#include <climits>
#include <limits>
#include "testing/testing.h"
#include "BKE_gtest_base.hh"
#include "DNA_object_types.h"
#include "bmesh.hh"
#include "intern/bmesh_snapshot.hh"
#include "sculpt_checkpoint_io.hh"

namespace blender::ed::sculpt_paint::checkpoint::tests {
namespace {
CodecLimits unlimited()
{
  return {std::numeric_limits<uint64_t>::max(), std::numeric_limits<size_t>::max(),
          std::numeric_limits<size_t>::max()};
}
uint64_t value64(const Span<std::byte> bytes, const int64_t at)
{
  uint64_t value = 0;
  for (int i = 0; i < 8; i++) { value |= uint64_t(std::to_integer<uint8_t>(bytes[at + i])) << (8 * i); }
  return value;
}
void put64(MutableSpan<std::byte> bytes, const int64_t at, const uint64_t value)
{
  for (int i = 0; i < 8; i++) { bytes[at + i] = std::byte(uint8_t(value >> (8 * i))); }
}
void put32(MutableSpan<std::byte> bytes, const int64_t at, const uint32_t value)
{
  for (int i = 0; i < 4; i++) { bytes[at + i] = std::byte(uint8_t(value >> (8 * i))); }
}
uint32_t float_bits(const float value)
{
  uint32_t bits; std::memcpy(&bits, &value, sizeof(bits)); return bits;
}
float bits_float(const uint32_t bits)
{
  float value; std::memcpy(&value, &bits, sizeof(value)); return value;
}
}

class SculptCompanionTest : public bke::BlenderGTestBase {
 protected:
  CaptureID capture{1, 2, 3, 4};
  Array<undo::SnapshotObjectView, 0> objects = Array<undo::SnapshotObjectView, 0>(2);
  Array<ObjectBinding, 0> bindings = Array<ObjectBinding, 0>(2);
  Array<undo::SnapshotGridView, 0> grids = Array<undo::SnapshotGridView, 0>(3);
  Array<GridBinding, 0> graph_bindings = Array<GridBinding, 0>(3);
  Array<int2, 0> edges = Array<int2, 0>(4);
  Array<int, 0> offsets = Array<int, 0>(2), corners = Array<int, 0>(4);
  Array<int, 0> corner_edges = Array<int, 0>(4), grid_faces = Array<int, 0>(4);
  Array<float3, 0> first_positions = Array<float3, 0>(16), later_positions = Array<float3, 0>(16);
  Array<float3, 0> normals = Array<float3, 0>(16);
  Array<float, 0> masks = Array<float, 0>(16);
  BitGroupVector<0> hidden{4, 4};
  Array<int, 0> pending = Array<int, 0>(1);
  BMGeometrySnapshot *geometry = nullptr;

  void SetUp() override
  {
    bke::BlenderGTestBase::SetUp();
    offsets[0] = 0; offsets[1] = 4; pending[0] = 0;
    for (int i = 0; i < 4; i++) {
      edges[i] = int2(i, (i + 1) % 4); corners[i] = corner_edges[i] = i; grid_faces[i] = 0;
    }
    for (int i = 0; i < 16; i++) {
      first_positions[i] = float3(float(i), 0, bits_float(0x80000000));
      later_positions[i] = float3(float(i) + 100, 2, 3);
      normals[i] = float3(0, 0, 1); masks[i] = float(i) / 16;
    }
    hidden[2][3].set(true);
    for (int i = 0; i < 2; i++) {
      objects[i] = {};
      objects[i].object_uid = uint32_t(i + 1); objects[i].mesh_uid = 50;
      objects[i].pivot_pos = float3(bits_float(0x80000000), bits_float(0x7fc12345), float(i));
      objects[i].pivot_rot = float4(1, 0, 0, 0); objects[i].pivot_scale = float3(1);
      objects[i].last_normal = float3(0, 1, 0);
      objects[i].persistent_grids_num = -1; objects[i].persistent_grid_size = -1;
      objects[i].needs_flush_to_id = true;
      bindings[i].object_uid = uint32_t(i + 1); bindings[i].mesh_uid = 50;
      bindings[i].object_token = uint64_t(100 + i); bindings[i].mesh_token = 200;
      bindings[i].mode = OB_MODE_SCULPT;
    }
    for (int i = 0; i < 3; i++) {
      grids[i] = {};
      const int object = i == 2 ? 1 : 0;
      grids[i].object_uid = objects[object].object_uid; grids[i].mesh_uid = 50;
      grids[i].scene_uid = uint32_t(10 + i); grids[i].view_layer = "Layer";
      grids[i].authority = i != 1;
      grids[i].graph = {true, true, 1, 9, 0, 0, bits_float(0x80000000), 10, 0, 0, 10, 10};
      grids[i].modifier = {5, 3, 1, 1, 1, 1, 0, 3, 0, 0};
      grids[i].evaluated_modifier = grids[i].modifier;
      grids[i].subdivision = {false, false, 1, false,
          bke::subdiv::SUBDIV_VTX_BOUNDARY_EDGE_ONLY,
          bke::subdiv::SUBDIV_FVAR_LINEAR_INTERPOLATION_NONE};
      grids[i].total_level = grids[i].level = 1; grids[i].verts_num = 4;
      grids[i].grid_size = 2; grids[i].grid_area = 4; grids[i].grids_num = 4;
      grids[i].edges = edges.as_span(); grids[i].face_offsets = offsets.as_span();
      grids[i].corner_verts = corners.as_span(); grids[i].corner_edges = corner_edges.as_span();
      grids[i].grid_to_face = grid_faces.as_span();
      grids[i].positions = i == 0 ? first_positions.as_span() : later_positions.as_span();
      grids[i].normals = normals.as_span(); grids[i].masks = masks.as_span();
      grids[i].hidden = {hidden.all_bits(), hidden.size(), hidden.group_size(),
                        hidden.all_bits().size() / hidden.size()};
      if (grids[i].authority) { grids[i].pending_normal_faces = pending.as_span(); }
      grids[i].dirty_coords = true;
      graph_bindings[i].object_uid = objects[object].object_uid;
      graph_bindings[i].mesh_uid = 50; graph_bindings[i].scene_uid = grids[i].scene_uid;
      std::memcpy(graph_bindings[i].view_layer, "Layer", 6);
      graph_bindings[i].scene_token = uint64_t(300 + i);
      graph_bindings[i].view_layer_token = uint64_t(400 + i);
    }
  }
  void TearDown() override
  {
    BM_geometry_snapshot_free(geometry);
    bke::BlenderGTestBase::TearDown();
  }
  void add_geometry()
  {
    BMeshCreateParams params{};
    BMesh *bm = BM_mesh_create(&bm_mesh_allocsize_default, &params);
    const float coordinates[3][3] = {{0, 0, 0}, {1, 0, 0}, {0, 1, 0}};
    BMVert *vertices[3];
    for (int i = 0; i < 3; i++) { vertices[i] = BM_vert_create(bm, coordinates[i], nullptr, BM_CREATE_NOP); }
    BM_face_create_verts(bm, vertices, 3, nullptr, BM_CREATE_NOP, true);
    const char *error = nullptr;
    geometry = BM_geometry_snapshot_create(bm, &error);
    ASSERT_NE(geometry, nullptr) << (error ? error : "No geometry capture diagnostic");
    BM_mesh_free(bm);
    objects[0].geometry = objects[1].geometry = geometry;
    grids = Array<undo::SnapshotGridView, 0>(0);
    graph_bindings = Array<GridBinding, 0>(0);
  }
  Array<std::byte, 0> encode()
  {
    Vector<std::byte> bytes; const char *error = nullptr;
    const bool success = companion_encode_views_for_test(objects.as_span(), grids.as_span(), true,
        capture, bindings.as_span(), graph_bindings.as_span(), unlimited(),
        [&](Span<std::byte> chunk) { bytes.extend(chunk); return true; }, &error);
    EXPECT_TRUE(success) << (error ? error : "No companion encode diagnostic");
    return Array<std::byte, 0>(bytes.as_span());
  }
  std::unique_ptr<DecodePlan> prepare(const Span<std::byte> bytes,
                                     const CodecLimits &limits = unlimited(),
                                     const CaptureID *expected = nullptr)
  {
    const char *error = nullptr;
    return companion_decode_prepare(bytes.size(), expected ? *expected : capture, limits,
        [&](uint64_t at, MutableSpan<std::byte> out) {
          if (at > uint64_t(bytes.size()) || uint64_t(out.size()) > uint64_t(bytes.size()) - at) { return false; }
          std::memcpy(out.data(), bytes.data() + size_t(at), out.size()); return true;
        }, &error);
  }
};

TEST_F(SculptCompanionTest, DistinctSharedMeshAndSameObjectGraphOwners)
{
  const auto bytes = encode(); auto plan = prepare(bytes.as_span());
  ASSERT_NE(plan, nullptr);
  ASSERT_EQ(plan->objects.size(), 2); ASSERT_EQ(plan->grids.size(), 3);
  EXPECT_EQ(plan->objects[0].binding.mesh_token, plan->objects[1].binding.mesh_token);
  EXPECT_NE(plan->objects[0].binding.object_token, plan->objects[1].binding.object_token);
  EXPECT_EQ(plan->grids[0].object_index, plan->grids[1].object_index);
  EXPECT_NE(plan->grids[0].scene_token, plan->grids[1].scene_token);
  EXPECT_TRUE(plan->grids[0].authority); EXPECT_FALSE(plan->grids[1].authority);
  EXPECT_TRUE(plan->grids[2].authority); EXPECT_TRUE(plan->flush_needed);
  EXPECT_EQ(plan->grids[0].positions[0].x, first_positions[0].x);
  EXPECT_EQ(plan->grids[1].positions[0].x, later_positions[0].x);
  EXPECT_TRUE(plan->grids[0].hidden[2][3]);
  EXPECT_EQ(plan->grids[0].pending_normal_faces[0], 0);
  EXPECT_EQ(plan->grids[0].graph.debug_flags, 9);
}

TEST_F(SculptCompanionTest, ExactFloatBitsAndProfiles)
{
  auto plan = prepare(encode().as_span()); ASSERT_NE(plan, nullptr);
  EXPECT_EQ(float_bits(plan->objects[0].pivot_pos.x), 0x80000000u);
  EXPECT_EQ(float_bits(plan->objects[0].pivot_pos.y), 0x7fc12345u);
  EXPECT_EQ(float_bits(plan->grids[0].graph.frame), 0x80000000u);
  EXPECT_EQ(float_bits(plan->grids[0].positions[0].z), 0x80000000u);
  EXPECT_EQ(plan->grids[0].modifier.quality, 3);
  EXPECT_EQ(plan->grids[0].subdivision.vtx_boundary_interpolation, bke::subdiv::SUBDIV_VTX_BOUNDARY_EDGE_ONLY);
}

TEST_F(SculptCompanionTest, FooterDirectoryRejectsEmptyOverlapDuplicateAndTruncation)
{
  const auto original = encode();
  const uint64_t directory = value64(original.as_span(), original.size() - 24);
  for (int attack = 0; attack < 5; attack++) {
    Array<std::byte, 0> bytes(original.as_span());
    if (attack == 0) { put64(bytes.as_mutable_span(), directory + 16, 0); }
    if (attack == 1) { put64(bytes.as_mutable_span(), directory + 24 + 8, 48); }
    if (attack == 2) { put32(bytes.as_mutable_span(), directory + 24 + 4, 0); }
    if (attack == 3) { put64(bytes.as_mutable_span(), bytes.size() - 24, UINT64_MAX); }
    if (attack == 4) { put64(bytes.as_mutable_span(), directory + 16, UINT64_MAX); }
    EXPECT_EQ(prepare(bytes.as_span()), nullptr) << attack;
  }
  EXPECT_EQ(prepare(original.as_span().drop_back(1)), nullptr);
  EXPECT_EQ(prepare(original.as_span().take_front(47)), nullptr);
}

TEST_F(SculptCompanionTest, CaptureVersionTokensAndAggregateLimits)
{
  const auto original = encode();
  CaptureID other = capture; other[0] ^= 1;
  EXPECT_EQ(prepare(original.as_span(), unlimited(), &other), nullptr);
  Array<std::byte, 0> bytes(original.as_span()); put32(bytes.as_mutable_span(), 8, 2);
  EXPECT_EQ(prepare(bytes.as_span()), nullptr);
  bytes = Array<std::byte, 0>(original.as_span()); put64(bytes.as_mutable_span(), 48, 0);
  EXPECT_EQ(prepare(bytes.as_span()), nullptr);
  CodecLimits limits = unlimited(); limits.max_backing_bytes = sizeof(DecodePlan);
  EXPECT_EQ(prepare(original.as_span(), limits), nullptr);
  limits = unlimited(); limits.max_scratch_bytes = 4095;
  EXPECT_EQ(prepare(original.as_span(), limits), nullptr);
  bytes = Array<std::byte, 0>(original.as_span()); put32(bytes.as_mutable_span(), 32, UINT32_MAX);
  EXPECT_EQ(prepare(bytes.as_span()), nullptr);
}

TEST_F(SculptCompanionTest, WriterRequiresActualCompleteTokenBindings)
{
  Vector<std::byte> bytes; const char *error = nullptr;
  graph_bindings[1].view_layer_token = graph_bindings[0].view_layer_token;
  graph_bindings[1].scene_token = graph_bindings[0].scene_token;
  EXPECT_FALSE(companion_encode_views_for_test(objects.as_span(), grids.as_span(), false,
      capture, bindings.as_span(), graph_bindings.as_span(), unlimited(),
      [&](Span<std::byte> chunk) { bytes.extend(chunk); return true; }, &error));
  EXPECT_TRUE(bytes.is_empty());
}

TEST_F(SculptCompanionTest, GeometryUsesExistingCodecAndFreshOwnedMeshes)
{
  add_geometry(); auto plan = prepare(encode().as_span()); ASSERT_NE(plan, nullptr);
  ASSERT_NE(plan->objects[0].geometry_plan, nullptr); ASSERT_NE(plan->objects[1].geometry_plan, nullptr);
  ASSERT_TRUE(companion_decode_geometry(*plan, nullptr));
  EXPECT_NE(plan->objects[0].geometry, plan->objects[1].geometry);
  EXPECT_TRUE(BM_geometry_snapshot_equals(geometry, plan->objects[0].geometry));
  EXPECT_TRUE(BM_geometry_snapshot_equals(geometry, plan->objects[1].geometry));
  EXPECT_FALSE(companion_decode_geometry(*plan, nullptr));
}

TEST_F(SculptCompanionTest, SecondOwnerGeometryFailureReleasesEarlierMeshes)
{
  add_geometry(); auto plan = prepare(encode().as_span()); ASSERT_NE(plan, nullptr);
  const char *error = nullptr;
  EXPECT_FALSE(companion_decode_geometry_with_test_fault(*plan, 1, BMGeometryDecodeTestPhase::Vertex, 0, &error));
  EXPECT_EQ(plan->objects[0].geometry, nullptr); EXPECT_EQ(plan->objects[1].geometry, nullptr);
  EXPECT_NE(error, nullptr);
  EXPECT_TRUE(companion_decode_geometry(*plan, nullptr));
}

TEST_F(SculptCompanionTest, SecondOwnerSectionFailureRejectsWholeDetachedPlan)
{
  add_geometry(); auto bytes = encode();
  const uint64_t directory = value64(bytes.as_span(), bytes.size() - 24);
  /* Object0,Geometry0,Object1,Geometry1; corrupt the second existing-codec header only. */
  const uint64_t second = value64(bytes.as_span(), directory + 3 * 24 + 8);
  bytes[int64_t(second)] ^= std::byte(0xff);
  EXPECT_EQ(prepare(bytes.as_span()), nullptr);
}

TEST_F(SculptCompanionTest, EmptyNoRuntimeCompanionAndHonestEncodeFacts)
{
  Vector<std::byte> bytes; EncodeInfo info; const char *error = nullptr;
  ASSERT_TRUE(companion_encode(nullptr, capture, {}, {}, unlimited(),
      [&](Span<std::byte> chunk) { bytes.extend(chunk); return true; }, &error, &info));
  EXPECT_TRUE(info.complete); EXPECT_EQ(info.file_bytes, bytes.size()); EXPECT_EQ(info.capture_id, capture);
  EXPECT_EQ(info.objects_num, 0); EXPECT_EQ(info.geometry_sections, 0);
  auto plan = prepare(bytes.as_span()); ASSERT_NE(plan, nullptr);
  EXPECT_TRUE(plan->objects.is_empty()); EXPECT_TRUE(plan->grids.is_empty());
  EXPECT_TRUE(companion_decode_geometry(*plan, nullptr));
  EXPECT_FALSE(companion_encode(nullptr, capture, {}, {}, unlimited(),
      [](Span<std::byte>) { return false; }, &error, &info));
  EXPECT_FALSE(info.complete); EXPECT_EQ(info.file_bytes, 0);
}
TEST_F(SculptCompanionTest, PendingVisibilityExactLogicalBitsAndPersistentArrays)
{
  grids = Array<undo::SnapshotGridView, 0>(0);
  graph_bindings = Array<GridBinding, 0>(0);
  BitVector<> face(1, true);
  auto &view = objects[0].visibility;
  view.present = true; view.modifier_uid = 5; view.verts_num = 4; view.grids_num = 4; view.grid_size = 2;
  view.face_offsets = offsets.as_span(); view.corner_verts = corners.as_span();
  view.face_hidden = BoundedBitSpan(face);
  view.grid_hidden = {hidden.all_bits(), hidden.size(), hidden.group_size(),
                      hidden.all_bits().size() / hidden.size()};
  objects[0].persistent_co = first_positions.as_span(); objects[0].persistent_no = normals.as_span();
  objects[0].persistent_disp = masks.as_span(); objects[0].persistent_grids_num = 4;
  objects[0].persistent_grid_size = 2;
  auto plan = prepare(encode().as_span()); ASSERT_NE(plan, nullptr);
  const auto &object = plan->objects[0];
  EXPECT_TRUE(object.visibility.present); EXPECT_TRUE(object.visibility.face_hidden[0]);
  EXPECT_TRUE(object.visibility.grid_hidden[2][3]);
  EXPECT_EQ(object.persistent_grids_num, 4); EXPECT_EQ(object.persistent_grid_size, 2);
  EXPECT_EQ(float_bits(object.persistent_co[0].z), 0x80000000u);
  EXPECT_EQ(object.persistent_disp.size(), masks.size());
  EXPECT_EQ(object.visibility.face_offsets.last(), 4);
}

TEST_F(SculptCompanionTest, BoundedReaderFailureAndNoncanonicalBooleanRefused)
{
  const auto bytes = encode(); const char *error = nullptr;
  auto plan = companion_decode_prepare(bytes.size(), capture, unlimited(),
      [](uint64_t, MutableSpan<std::byte>) { return false; }, &error);
  EXPECT_EQ(plan, nullptr); EXPECT_NE(error, nullptr);
  Array<std::byte, 0> altered(bytes.as_span());
  put32(altered.as_mutable_span(), 40, 2); /* Header flush boolean must be canonical. */
  EXPECT_EQ(prepare(altered.as_span()), nullptr);
}

TEST_F(SculptCompanionTest, SparseBitCapacityRefusedBeforeConstruction)
{
  /* Native BitVector::reserve takes int. Virtual zero payloads permit the file/backing
   * policy while proving that admission refuses before allocating or reading them. */
  for (const bool face : {false, true}) {
    for (const bool scan : {false, true}) {
      for (const uint64_t size : {uint64_t(INT_MAX) + 1, uint64_t(INT_MAX) + 64}) {
        std::array<std::byte, 16> header{};
        put64(MutableSpan(header), 0, face ? 1 : 2);
        put64(MutableSpan(header), 8, face ? size : size / 2);
        const uint64_t logical_bits = face ? size : 2 * (size / 2);
        uint64_t read_bytes = 0;
        EXPECT_FALSE(companion_decode_bits_for_test(face, scan, 16 + (logical_bits + 7) / 8,
            face ? 1 : 2, face ? size : size / 2,
            [&](uint64_t at, MutableSpan<std::byte> out) {
              read_bytes += out.size();
              for (const int64_t i : out.index_range()) {
                out[i] = at + i < header.size() ? header[size_t(at + i)] : std::byte(0);
              }
              return true;
            }));
        EXPECT_LE(read_bytes, 4096);
      }
    }
  }
  /* Group padding makes the largest two-group native endpoint a multiple of 64. */
  std::array<std::byte, 16> grouped{};
  const uint64_t group_size = (uint64_t(INT_MAX) / 2) & ~uint64_t(63);
  put64(MutableSpan(grouped), 0, 2); put64(MutableSpan(grouped), 8, group_size);
  EXPECT_TRUE(companion_decode_bits_for_test(false, true, 16 + (2 * group_size + 7) / 8,
      2, group_size, [&](uint64_t at, MutableSpan<std::byte> out) {
        for (const int64_t i : out.index_range()) {
          out[i] = at + i < grouped.size() ? grouped[size_t(at + i)] : std::byte(0);
        }
        return true;
      }));
  /* Exact representable face endpoint: scan only, so no huge native allocation. */
  std::array<std::byte, 16> header{};
  put64(MutableSpan(header), 0, 1); put64(MutableSpan(header), 8, INT_MAX);
  EXPECT_TRUE(companion_decode_bits_for_test(true, true, 16 + (uint64_t(INT_MAX) + 7) / 8,
      1, INT_MAX, [&](uint64_t at, MutableSpan<std::byte> out) {
        for (const int64_t i : out.index_range()) {
          out[i] = at + i < header.size() ? header[size_t(at + i)] : std::byte(0);
        }
        return true;
      }));
}

TEST_F(SculptCompanionTest, OwnerBitDimensionsRefusedInCompanionScan)
{
  auto bytes = encode();
  const uint64_t directory = value64(bytes.as_span(), bytes.size() - 24);
  const uint64_t grid = value64(bytes.as_span(), directory + 2 * 24 + 8);
  uint64_t at = grid + 200;
  for (const int components : {3, 3, 2, 1, 1, 1, 1, 3, 3, 1}) {
    at += 8 + value64(bytes.as_span(), at) * components * 4;
  }
  put64(bytes.as_mutable_span(), at, 2);
  put64(bytes.as_mutable_span(), at + 8, 8); /* Same 16 logical bits, wrong owner shape. */
  EXPECT_EQ(prepare(bytes.as_span()), nullptr);

  grids = Array<undo::SnapshotGridView, 0>(0);
  graph_bindings = Array<GridBinding, 0>(0);
  BitVector<> face(1, true);
  auto &v = objects[0].visibility;
  v.present = true; v.modifier_uid = 5; v.verts_num = 4; v.grids_num = 4; v.grid_size = 2;
  v.face_offsets = offsets.as_span(); v.corner_verts = corners.as_span();
  v.face_hidden = BoundedBitSpan(face);
  v.grid_hidden = {hidden.all_bits(), 4, 4, 4};
  bytes = encode();
  at = 48 + 108;
  for (const int components : {3, 3, 1}) { at += 8 + value64(bytes.as_span(), at) * components * 4; }
  at += 4 + 16;
  for (int i = 0; i < 2; i++) { at += 8 + value64(bytes.as_span(), at) * 4; }
  put64(bytes.as_mutable_span(), at + 8, 2); /* One byte remains but owner has one face. */
  EXPECT_EQ(prepare(bytes.as_span()), nullptr);
  bytes = encode();
  at += 16 + 1;
  put64(bytes.as_mutable_span(), at, 2); put64(bytes.as_mutable_span(), at + 8, 8);
  EXPECT_EQ(prepare(bytes.as_span()), nullptr);
}

TEST_F(SculptCompanionTest, PersistentCacheAbsentStaleAndInvalidTuples)
{
  auto plan = prepare(encode().as_span()); ASSERT_NE(plan, nullptr);
  EXPECT_EQ(plan->objects[0].persistent_grids_num, -1);
  EXPECT_TRUE(plan->objects[0].persistent_co.is_empty());
  /* Valid stale cache describes one older 2x2 grid while current owners have four. */
  objects[0].persistent_grids_num = 1; objects[0].persistent_grid_size = 2;
  objects[0].persistent_co = first_positions.as_span().take_front(4);
  objects[0].persistent_no = normals.as_span().take_front(4);
  objects[0].persistent_disp = masks.as_span().take_front(4);
  plan = prepare(encode().as_span()); ASSERT_NE(plan, nullptr);
  EXPECT_EQ(plan->objects[0].persistent_co.size(), 4);
  EXPECT_EQ(plan->objects[0].persistent_grids_num, 1);
  for (int i = 0; i < 4; i++) {
    EXPECT_EQ(float_bits(plan->objects[0].persistent_co[i].z), float_bits(first_positions[i].z));
    EXPECT_EQ(plan->objects[0].persistent_no[i], normals[i]);
    EXPECT_EQ(float_bits(plan->objects[0].persistent_disp[i]), float_bits(masks[i]));
  }
  const auto valid = encode();
  for (const int count_offset : {108, 164, 220}) {
    Array<std::byte, 0> bytes(valid.as_span());
    put64(bytes.as_mutable_span(), 48 + count_offset, 3);
    EXPECT_EQ(prepare(bytes.as_span()), nullptr);
  }
  for (const int2 dims : {int2(-1, 2), int2(1, -1), int2(-2, -2), int2(1, 0),
                          int2(INT_MAX, INT_MAX), int2(-1, -1)}) {
    Array<std::byte, 0> bytes(valid.as_span());
    put32(bytes.as_mutable_span(), 48 + 96, uint32_t(dims.x));
    put32(bytes.as_mutable_span(), 48 + 100, uint32_t(dims.y));
    EXPECT_EQ(prepare(bytes.as_span()), nullptr);
  }
  objects[0].persistent_grids_num = 0;
  objects[0].persistent_co = {}; objects[0].persistent_no = {}; objects[0].persistent_disp = {};
  plan = prepare(encode().as_span()); ASSERT_NE(plan, nullptr);
  EXPECT_EQ(plan->objects[0].persistent_grid_size, 2);
}

TEST_F(SculptCompanionTest, SubdivisionRawEnumsAdmittedBeforeCast)
{
  const auto original = encode();
  const uint64_t directory = value64(original.as_span(), original.size() - 24);
  const uint64_t grid = value64(original.as_span(), directory + 2 * 24 + 8);
  for (const int field : {0, 1}) {
    const int maximum = field == 0 ? int(bke::subdiv::SUBDIV_VTX_BOUNDARY_EDGE_AND_CORNER) :
                                   int(bke::subdiv::SUBDIV_FVAR_LINEAR_INTERPOLATION_ALL);
    for (const int raw : {-1, INT_MAX, maximum + 1}) {
      Array<std::byte, 0> bytes(original.as_span());
      put32(bytes.as_mutable_span(), grid + 168 + field * 4, uint32_t(raw));
      EXPECT_EQ(prepare(bytes.as_span()), nullptr);
    }
    for (const int raw : {0, maximum}) {
      Array<std::byte, 0> bytes(original.as_span());
      put32(bytes.as_mutable_span(), grid + 168 + field * 4, uint32_t(raw));
      auto plan = prepare(bytes.as_span()); ASSERT_NE(plan, nullptr);
      EXPECT_EQ(field == 0 ? int(plan->grids[0].subdivision.vtx_boundary_interpolation) :
                            int(plan->grids[0].subdivision.fvar_linear_interpolation), raw);
    }
  }
}

}  // namespace blender::ed::sculpt_paint::checkpoint::tests
