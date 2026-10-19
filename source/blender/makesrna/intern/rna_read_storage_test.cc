/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include <cstring>

#include "BLI_listbase.h"

#include "DNA_curve_types.h"
#include "DNA_material_types.h"
#include "DNA_mesh_types.h"
#include "DNA_object_types.h"
#include "DNA_scene_types.h"

#include "RNA_access.hh"
#include "RNA_prototypes.hh"

#include "rna_internal.hh"

#include "testing/testing.h"

namespace blender::rna::tests {

static int callback_calls;
static PointerRNA pointer_trap(PointerRNA *)
{
  callback_calls++;
  return {};
}
static StructRNA *refine_trap(PointerRNA *ptr)
{
  callback_calls++;
  return ptr->type;
}
static IDProperty **idproperties_trap(PointerRNA *)
{
  callback_calls++;
  return nullptr;
}
static void collection_begin_trap(CollectionPropertyIterator *, PointerRNA *)
{
  callback_calls++;
}
static void array_set_trap(PointerRNA *, const float *)
{
  callback_calls++;
}
static void int_range_trap(PointerRNA *, int *, int *, int *, int *)
{
  callback_calls++;
}
static void range_trap(PointerRNA *, float *, float *, float *, float *)
{
  callback_calls++;
}

/** Changes to builtin slots in a control are always restored, including fatal test assertions. */
template<typename T> class ScopedSlotRestore {
  T &slot_;
  T value_;

 public:
  explicit ScopedSlotRestore(T &slot) : slot_(slot), value_(slot) {}
  ~ScopedSlotRestore()
  {
    slot_ = value_;
  }
};

static PropertyRNA *builtin_property(StructRNA *type, const char *identifier)
{
  for (PropertyRNA &prop : type->cont.properties) {
    if (std::strcmp(prop.identifier, identifier) == 0) {
      return &prop;
    }
  }
  return nullptr;
}

class RNAReadStorageTest : public testing::Test {
 protected:
  static void SetUpTestSuite()
  {
    /* Initialize only generated declarations. No RNA pointer factory or path resolution. */
    RNA_blender_rna_get();
  }
  void SetUp() override
  {
    callback_calls = 0;
  }
};

TEST_F(RNAReadStorageTest, GeneratedSetterIsIndependentOfGetter)
{
  auto *prop = static_cast<FloatPropertyRNA *>(builtin_property(RNA_Object, "location"));
  ASSERT_NE(prop, nullptr);
  EXPECT_EQ(RNA_property_read_callback_source(prop, RNAReadCallbackRole::GET_ARRAY).state,
            RNAReadCallbackState::MATCHED);
  EXPECT_EQ(RNA_property_read_callback_source(prop, RNAReadCallbackRole::SET_ARRAY).state,
            RNAReadCallbackState::MATCHED);
  ScopedSlotRestore restore(prop->setarray);
  prop->setarray = array_set_trap;
  EXPECT_EQ(RNA_property_read_callback_source(prop, RNAReadCallbackRole::SET_ARRAY).state,
            RNAReadCallbackState::UNKNOWN);
  EXPECT_EQ(RNA_property_read_callback_source(prop, RNAReadCallbackRole::GET_ARRAY).state,
            RNAReadCallbackState::MATCHED);
  prop->setarray = nullptr;
  EXPECT_EQ(RNA_property_read_callback_source(prop, RNAReadCallbackRole::SET_ARRAY).state,
            RNAReadCallbackState::UNKNOWN);
  EXPECT_EQ(callback_calls, 0);
}

TEST_F(RNAReadStorageTest, ManualRangeIsObservedWithoutRunningIt)
{
  auto *prop = static_cast<IntPropertyRNA *>(builtin_property(RNA_TextCurve, "active_textbox"));
  ASSERT_NE(prop, nullptr);
  EXPECT_EQ(RNA_property_read_callback_source(prop, RNAReadCallbackRole::RANGE).state,
            RNAReadCallbackState::MATCHED);
  EXPECT_EQ(RNA_property_read_callback_source(prop, RNAReadCallbackRole::RANGE).origin,
            RNAReadCallbackOrigin::MANUAL);
  ScopedSlotRestore restore(prop->range);
  prop->range = int_range_trap;
  EXPECT_EQ(RNA_property_read_callback_source(prop, RNAReadCallbackRole::RANGE).state,
            RNAReadCallbackState::UNKNOWN);
  prop->range = nullptr;
  EXPECT_EQ(RNA_property_read_callback_source(prop, RNAReadCallbackRole::RANGE).state,
            RNAReadCallbackState::UNKNOWN);
  EXPECT_EQ(callback_calls, 0);
}

TEST_F(RNAReadStorageTest, EveryFloatSetterAndRangeRoleUsesItsOwnTypedLiteral)
{
  FloatPropertyRNA prop{};
  prop.magic = RNA_MAGIC;
  prop.type = PROP_FLOAT;
  /* Source fixtures use compiled literals, not snapshots of the mutable slots. */
  static const RNAReadCallbackRecord records[] = {
      {RNAReadCallbackRole::SET, RNAReadCallbackOrigin::MANUAL, __FILE__, "set",
       {.float_set = [](PointerRNA *, float) { callback_calls++; }}},
      {RNAReadCallbackRole::SET_ARRAY, RNAReadCallbackOrigin::MANUAL, __FILE__, "setarray",
       {.float_setarray = array_set_trap}},
      {RNAReadCallbackRole::SET_EX, RNAReadCallbackOrigin::MANUAL, __FILE__, "set_ex",
       {.float_set_ex = [](PointerRNA *, PropertyRNA *, float) { callback_calls++; }}},
      {RNAReadCallbackRole::SET_ARRAY_EX, RNAReadCallbackOrigin::MANUAL, __FILE__, "setarray_ex",
       {.float_setarray_ex = [](PointerRNA *, PropertyRNA *, const float *) { callback_calls++; }}},
      {RNAReadCallbackRole::SET_TRANSFORM, RNAReadCallbackOrigin::MANUAL, __FILE__, "transform",
       {.float_set_transform = [](PointerRNA *, PropertyRNA *, float a, float, bool) {
          callback_calls++;
          return a;
        }}},
      {RNAReadCallbackRole::SET_ARRAY_TRANSFORM,
       RNAReadCallbackOrigin::MANUAL,
       __FILE__,
       "array_transform",
       {.float_setarray_transform = [](PointerRNA *, PropertyRNA *, const float *, const float *, bool, float *) {
          callback_calls++;
        }}},
      {RNAReadCallbackRole::RANGE, RNAReadCallbackOrigin::MANUAL, __FILE__, "range",
       {.float_range = range_trap}},
      {RNAReadCallbackRole::RANGE_EX, RNAReadCallbackOrigin::MANUAL, __FILE__, "range_ex",
       {.float_range_ex = [](PointerRNA *, PropertyRNA *, float *, float *, float *, float *) {
          callback_calls++;
        }}},
  };
  const RNAPropertyReadSource source{&prop, PROP_FLOAT, records, 8};
  prop.read_source = &source;
  prop.set = records[0].expected.float_set;
  prop.setarray = records[1].expected.float_setarray;
  prop.set_ex = records[2].expected.float_set_ex;
  prop.setarray_ex = records[3].expected.float_setarray_ex;
  prop.set_transform = records[4].expected.float_set_transform;
  prop.setarray_transform = records[5].expected.float_setarray_transform;
  prop.range = records[6].expected.float_range;
  prop.range_ex = records[7].expected.float_range_ex;
  for (const RNAReadCallbackRecord &record : records) {
    EXPECT_EQ(RNA_property_read_callback_source(&prop, record.role).state,
              RNAReadCallbackState::MATCHED);
  }
  const auto reject_null = [&](auto &slot, RNAReadCallbackRole role) {
    ScopedSlotRestore restore(slot);
    slot = nullptr;
    EXPECT_EQ(RNA_property_read_callback_source(&prop, role).state,
              RNAReadCallbackState::UNKNOWN);
  };
  reject_null(prop.set, RNAReadCallbackRole::SET);
  reject_null(prop.setarray, RNAReadCallbackRole::SET_ARRAY);
  reject_null(prop.set_ex, RNAReadCallbackRole::SET_EX);
  reject_null(prop.setarray_ex, RNAReadCallbackRole::SET_ARRAY_EX);
  reject_null(prop.set_transform, RNAReadCallbackRole::SET_TRANSFORM);
  reject_null(prop.setarray_transform, RNAReadCallbackRole::SET_ARRAY_TRANSFORM);
  reject_null(prop.range, RNAReadCallbackRole::RANGE);
  reject_null(prop.range_ex, RNAReadCallbackRole::RANGE_EX);
  FloatPropertyRNA copied = prop;
  EXPECT_EQ(RNA_property_read_callback_source(&copied, RNAReadCallbackRole::SET).state,
            RNAReadCallbackState::UNKNOWN);
  prop.flag_internal |= PROP_INTERN_RUNTIME;
  EXPECT_EQ(RNA_property_read_callback_source(&prop, RNAReadCallbackRole::SET).state,
            RNAReadCallbackState::UNKNOWN);
  EXPECT_EQ(callback_calls, 0);
}

TEST_F(RNAReadStorageTest, RegularAndSystemIDPropertiesHaveSeparateSourceIdentity)
{
  EXPECT_EQ(RNA_struct_read_callback_source(RNA_Object, RNAReadCallbackRole::IDPROPERTIES).state,
            RNAReadCallbackState::MATCHED);
  EXPECT_EQ(RNA_struct_read_callback_source(RNA_Object, RNAReadCallbackRole::SYSTEM_IDPROPERTIES).state,
            RNAReadCallbackState::MATCHED);
  EXPECT_TRUE(RNA_struct_idproperties_matches_source(RNA_Object));
  StructRNA runtime;
  runtime.flag = STRUCT_RUNTIME;
  runtime.base = RNA_Object;
  runtime.idproperties = RNA_Object->idproperties;
  runtime.read_source = RNA_Object->read_source;
  EXPECT_TRUE(RNA_struct_idproperties_matches_source(&runtime));
  EXPECT_EQ(RNA_struct_read_callback_source(&runtime, RNAReadCallbackRole::IDPROPERTIES).state,
            RNAReadCallbackState::UNKNOWN);
  runtime.idproperties = idproperties_trap;
  EXPECT_FALSE(RNA_struct_idproperties_matches_source(&runtime));
  runtime.idproperties = nullptr;
  EXPECT_FALSE(RNA_struct_idproperties_matches_source(&runtime));
  StructRNA copied_owner;
  copied_owner.idproperties = RNA_Object->idproperties;
  copied_owner.read_source = RNA_Object->read_source;
  EXPECT_FALSE(RNA_struct_idproperties_matches_source(&copied_owner));
  {
    ScopedSlotRestore restore(RNA_Object->idproperties);
    RNA_Object->idproperties = idproperties_trap;
    EXPECT_FALSE(RNA_struct_idproperties_matches_source(RNA_Object));
    EXPECT_EQ(RNA_struct_read_callback_source(RNA_Object, RNAReadCallbackRole::SYSTEM_IDPROPERTIES).state,
              RNAReadCallbackState::MATCHED);
  }
  EXPECT_EQ(callback_calls, 0);
}

TEST_F(RNAReadStorageTest, RuntimeRegistrationLifetimeCannotReuseCompiledPropertyAuthority)
{
  BlenderRNA registry;
  auto *original = static_cast<PointerPropertyRNA *>(builtin_property(RNA_Scene, "render"));
  ASSERT_NE(original, nullptr);
  for (int lifetime = 0; lifetime < 2; lifetime++) {
    StructRNA *runtime = RNA_def_struct_ptr(&registry, "ReadStorageLifetimeFixture", RNA_Scene);
    ASSERT_NE(runtime, nullptr);
    auto *prop = static_cast<PointerPropertyRNA *>(
        RNA_def_property(runtime, "borrowed_render", PROP_POINTER, PROP_NONE));
    ASSERT_NE(prop, nullptr);
    prop->pointer_type = original->pointer_type;
    prop->get = original->get;
    /* Even transplanting the old immutable sidecar does not confer its declaration lifetime. */
    prop->read_source = original->read_source;
    EXPECT_EQ(RNA_property_read_callback_source(prop, RNAReadCallbackRole::GET).state,
              RNAReadCallbackState::UNKNOWN);
    EXPECT_EQ(RNA_property_raw_storage_descriptor(prop), nullptr);
    EXPECT_TRUE(RNA_struct_idproperties_matches_source(runtime));
    runtime->idproperties = idproperties_trap;
    EXPECT_FALSE(RNA_struct_idproperties_matches_source(runtime));
    RNA_struct_free(&registry, runtime);
    EXPECT_TRUE(registry.structs.is_empty());
  }
  EXPECT_EQ(callback_calls, 0);
}

TEST_F(RNAReadStorageTest, GeneratedEmbeddedPointerExtractionNeverRefines)
{
  Scene scene{};
  PointerRNA parent(&scene.id, RNA_Scene, &scene);
  auto *prop = static_cast<PointerPropertyRNA *>(builtin_property(RNA_Scene, "render"));
  ASSERT_NE(prop, nullptr);
  const RNARawStorageDescriptor *descriptor = RNA_property_raw_storage_descriptor(prop);
  ASSERT_NE(descriptor, nullptr);
  EXPECT_EQ(descriptor->kind, RNARawStorageKind::POINTER);
  EXPECT_EQ(descriptor->item_type, RNA_RenderSettings);
  EXPECT_EQ(descriptor->owner_type, RNA_Scene);
  PointerRNA unrelated_parent(&scene.id, RNA_Object, &scene);
  RNARawStorageView rejected_view;
  EXPECT_FALSE(RNA_property_raw_storage_read(&unrelated_parent, prop, &rejected_view));
  ScopedSlotRestore restore_refine(RNA_RenderSettings->refine);
  RNA_RenderSettings->refine = refine_trap;
  RNARawStorageView view;
  ASSERT_TRUE(RNA_property_raw_storage_read(&parent, prop, &view));
  EXPECT_EQ(view.data, &scene.r);
  EXPECT_EQ(view.descriptor, descriptor);
  EXPECT_EQ(callback_calls, 0);
  {
    ScopedSlotRestore restore_type(prop->pointer_type);
    prop->pointer_type = RNA_Object;
    EXPECT_FALSE(RNA_property_raw_storage_read(&parent, prop, &view));
  }
  {
    ScopedSlotRestore restore_type_fn(prop->type_fn);
    prop->type_fn = refine_trap;
    EXPECT_FALSE(RNA_property_raw_storage_read(&parent, prop, &view));
  }
  ScopedSlotRestore restore_get(prop->get);
  prop->get = pointer_trap;
  EXPECT_EQ(RNA_property_raw_storage_descriptor(prop), nullptr);
  EXPECT_FALSE(RNA_property_raw_storage_read(&parent, prop, &view));
  EXPECT_EQ(view.data, nullptr);
  prop->get = nullptr;
  EXPECT_FALSE(RNA_property_raw_storage_read(&parent, prop, &view));
  EXPECT_EQ(callback_calls, 0);
}

TEST_F(RNAReadStorageTest, GeneratedPointerRetainsBorrowAndRejectsCopiedDeclaration)
{
  Object object{};
  /* Raw extraction borrows this target; it does not need to inspect or refine its contents. */
  int target;
  object.pose = reinterpret_cast<bPose *>(&target);
  PointerRNA parent(&object.id, RNA_Object, &object);
  auto *prop = static_cast<PointerPropertyRNA *>(builtin_property(RNA_Object, "pose"));
  ASSERT_NE(prop, nullptr);
  RNARawStorageView view;
  ASSERT_TRUE(RNA_property_raw_storage_read(&parent, prop, &view));
  EXPECT_EQ(view.data, object.pose);
  PointerPropertyRNA copied = *prop;
  EXPECT_EQ(RNA_property_raw_storage_descriptor(&copied), nullptr);
  EXPECT_FALSE(RNA_property_raw_storage_read(&parent, &copied, &view));
  EXPECT_EQ(view.descriptor, nullptr);
  EXPECT_FALSE(RNA_property_raw_storage_read(nullptr, prop, &view));
  EXPECT_FALSE(RNA_property_raw_storage_read(&parent, prop, nullptr));
  EXPECT_EQ(callback_calls, 0);
}

TEST_F(RNAReadStorageTest, GeneratedListExtractionDoesNotTraverseOrRefine)
{
  Scene scene{};
  /* Deliberately invalid links demonstrate that extraction does not traverse. */
  scene.markers.first = reinterpret_cast<TimeMarker *>(uintptr_t(1));
  scene.markers.last = reinterpret_cast<TimeMarker *>(uintptr_t(2));
  PointerRNA parent(&scene.id, RNA_Scene, &scene);
  auto *prop = static_cast<CollectionPropertyRNA *>(builtin_property(RNA_Scene, "timeline_markers"));
  ASSERT_NE(prop, nullptr);
  RNARawStorageView view;
  ScopedSlotRestore restore_refine(RNA_TimelineMarker->refine);
  RNA_TimelineMarker->refine = refine_trap;
  ASSERT_TRUE(RNA_property_raw_storage_read(&parent, prop, &view));
  EXPECT_EQ(view.descriptor->kind, RNARawStorageKind::LIST);
  EXPECT_EQ(view.data, &scene.markers);
  EXPECT_EQ(view.length, -1);
  const auto reject_null = [&](auto &slot) {
    ScopedSlotRestore restore(slot);
    slot = nullptr;
    EXPECT_FALSE(RNA_property_raw_storage_read(&parent, prop, &view));
  };
  reject_null(prop->begin);
  reject_null(prop->next);
  reject_null(prop->end);
  reject_null(prop->get);
  {
    ScopedSlotRestore restore_length(prop->length);
    prop->length = [](PointerRNA *) {
      callback_calls++;
      return 0;
    };
    EXPECT_FALSE(RNA_property_raw_storage_read(&parent, prop, &view));
  }
  {
    ScopedSlotRestore restore_lookup(prop->lookupint);
    prop->lookupint = [](PointerRNA *, int, PointerRNA *) {
      callback_calls++;
      return false;
    };
    EXPECT_FALSE(RNA_property_raw_storage_read(&parent, prop, &view));
  }
  {
    ScopedSlotRestore restore_lookup(prop->lookupstring);
    prop->lookupstring = [](PointerRNA *, const char *, PointerRNA *) {
      callback_calls++;
      return false;
    };
    EXPECT_FALSE(RNA_property_raw_storage_read(&parent, prop, &view));
  }
  ScopedSlotRestore restore_begin(prop->begin);
  prop->begin = collection_begin_trap;
  EXPECT_FALSE(RNA_property_raw_storage_read(&parent, prop, &view));
  prop->begin = nullptr;
  EXPECT_FALSE(RNA_property_raw_storage_read(&parent, prop, &view));
  EXPECT_EQ(callback_calls, 0);
}

TEST_F(RNAReadStorageTest, GeneratedArrayAndPointerArrayExtractionPreserveStoredStride)
{
  Curve curve{};
  TextBox boxes[2]{};
  curve.tb = boxes;
  curve.totbox = 2;
  PointerRNA curve_parent(&curve.id, RNA_TextCurve, &curve);
  PropertyRNA *boxes_prop = builtin_property(RNA_TextCurve, "text_boxes");
  ASSERT_NE(boxes_prop, nullptr);
  RNARawStorageView view;
  ASSERT_TRUE(RNA_property_raw_storage_read(&curve_parent, boxes_prop, &view));
  EXPECT_EQ(view.descriptor->kind, RNARawStorageKind::ARRAY);
  EXPECT_EQ(view.data, boxes);
  EXPECT_EQ(view.length, 2);
  EXPECT_EQ(view.item_size, sizeof(TextBox));
  Mesh mesh{};
  Material *materials[2]{};
  mesh.mat = materials;
  mesh.totcol = 2;
  PointerRNA mesh_parent(&mesh.id, RNA_Mesh, &mesh);
  PropertyRNA *materials_prop = builtin_property(RNA_Mesh, "materials");
  ASSERT_NE(materials_prop, nullptr);
  ASSERT_TRUE(RNA_property_raw_storage_read(&mesh_parent, materials_prop, &view));
  EXPECT_EQ(view.descriptor->kind, RNARawStorageKind::POINTER_ARRAY);
  EXPECT_EQ(view.data, materials);
  EXPECT_EQ(view.length, 2);
  EXPECT_EQ(view.item_size, sizeof(Material *));
  mesh.totcol = -1;
  EXPECT_FALSE(RNA_property_raw_storage_read(&mesh_parent, materials_prop, &view));
  EXPECT_EQ(view.descriptor, nullptr);
  mesh.totcol = 2;
  mesh.mat = nullptr;
  ASSERT_TRUE(RNA_property_raw_storage_read(&mesh_parent, materials_prop, &view));
  EXPECT_EQ(view.length, 0);
  EXPECT_EQ(callback_calls, 0);
}

TEST_F(RNAReadStorageTest, ManualComputedDeclarationsNeverGainRawStorage)
{
  PropertyRNA *prop = builtin_property(RNA_Object, "matrix_world");
  ASSERT_NE(prop, nullptr);
  EXPECT_EQ(RNA_property_raw_storage_descriptor(prop), nullptr);
  prop = builtin_property(RNA_Object, "vertex_groups");
  ASSERT_NE(prop, nullptr);
  EXPECT_EQ(RNA_property_raw_storage_descriptor(prop), nullptr);
}

}  // namespace blender::rna::tests
