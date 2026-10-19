/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "testing/testing.h"
#include "MEM_guardedalloc.h"

#include <array>
#include <climits>
#include <cstring>

#include "bpy_angelo_read_scalar_const.hh"

#include "BKE_gtest_base.hh"
#include "BKE_global.hh"
#include "BKE_main.hh"
#include "BKE_pointcache.h"
#include "BKE_object_types.hh"
#include "BLI_expr_pylike_eval.h"
#include "BLI_memory_utils.hh"
#include "BLI_listbase.h"
#include "BLI_math_matrix.h"
#include "BLI_string.h"
#include "BLI_threads.h"

#include "DNA_anim_types.h"
#include "DNA_scene_types.h"
#include "DNA_sequence_types.h"
#include "DNA_object_types.h"
#include "DNA_movieclip_types.h"
#include "DNA_object_force_types.h"
#include "IMB_imbuf_types.hh"
#include "RE_pipeline.h"
#include "SEQ_sequencer.hh"
#include "RNA_access.hh"
#include "RNA_enum_types.hh"
#include "RNA_prototypes.hh"
#include "intern/rna_internal_types.hh"

namespace blender::angelo_read_scalar::tests {

class ScalarConstReadTest : public bke::BlenderGTestBase {};

TEST_F(ScalarConstReadTest, DriverColdAndWarmMatchOrdinaryReadWithoutPublishingCache)
{
  ChannelDriver source{};
  source.type = DRIVER_TYPE_PYTHON;
  STRNCPY(source.expression, "sin(frame) + 2");
  PointerRNA owner = RNA_pointer_create_discrete(nullptr, RNA_Driver, &source);
  const PropertyRNA *property = RNA_struct_find_property(&owner, "is_simple_expression");
  ASSERT_NE(property, nullptr);
  ASSERT_EQ(value_family(*owner.type, *property), DRIVER_SIMPLE_EXPRESSION);
  Scope scope;
  const Value cold = value(scope, DRIVER_SIMPLE_EXPRESSION, owner, *property);
  ASSERT_EQ(cold.status, Status::OK);
  EXPECT_TRUE(std::get<bool>(cold.data));
  EXPECT_EQ(source.expr_simple, nullptr);
  EXPECT_EQ(source.flag, 0);

  /* The ordinary RNA read on a separate driver produces the comparison result and its cache. */
  ChannelDriver ordinary{};
  ordinary.type = source.type;
  STRNCPY(ordinary.expression, source.expression);
  PointerRNA ordinary_owner = RNA_pointer_create_discrete(nullptr, RNA_Driver, &ordinary);
  EXPECT_EQ(std::get<bool>(cold.data), RNA_property_boolean_get(&ordinary_owner,
                                                              const_cast<PropertyRNA *>(property)));
  ASSERT_NE(ordinary.expr_simple, nullptr);
  source.expr_simple = ordinary.expr_simple;
  /* Existing cache wins even with a later different type/expression. */
  source.type = DRIVER_TYPE_AVERAGE;
  STRNCPY(source.expression, "unresolved_python_name");
  const Value warm = value(scope, DRIVER_SIMPLE_EXPRESSION, owner, *property);
  ASSERT_EQ(warm.status, Status::OK);
  EXPECT_TRUE(std::get<bool>(warm.data));
  EXPECT_EQ(source.expr_simple, ordinary.expr_simple);
  BLI_expr_pylike_free(ordinary.expr_simple);
}

TEST_F(ScalarConstReadTest, InvalidDriverExpressionDoesNotPublishInvalidCache)
{
  ChannelDriver driver{};
  driver.type = DRIVER_TYPE_PYTHON;
  STRNCPY(driver.expression, "arbitrary_python_callback(frame)");
  PointerRNA owner = RNA_pointer_create_discrete(nullptr, RNA_Driver, &driver);
  const PropertyRNA *property = RNA_struct_find_property(&owner, "is_simple_expression");
  ASSERT_NE(property, nullptr);
  Scope scope;
  const Value result = value(scope, DRIVER_SIMPLE_EXPRESSION, owner, *property);
  ASSERT_EQ(result.status, Status::OK);
  EXPECT_FALSE(std::get<bool>(result.data));
  EXPECT_EQ(driver.expr_simple, nullptr);
  EXPECT_EQ(driver.flag, 0);
}

static bool unregistered_getter(PointerRNA * /*owner*/)
{
  ADD_FAILURE() << "Unregistered callback was invoked";
  return false;
}

TEST_F(ScalarConstReadTest, CallbackIdentityAndRuntimePropertiesCannotAcquireFamily)
{
  ChannelDriver driver{};
  PointerRNA owner = RNA_pointer_create_discrete(nullptr, RNA_Driver, &driver);
  const PropertyRNA *property = RNA_struct_find_property(&owner, "is_simple_expression");
  ASSERT_NE(property, nullptr);
  auto replaced = *static_cast<const BoolPropertyRNA *>(property);
  replaced.get = unregistered_getter;
  EXPECT_EQ(value_family(*owner.type, replaced), NONE);
  auto runtime = *static_cast<const BoolPropertyRNA *>(property);
  runtime.flag_internal |= PROP_INTERN_RUNTIME;
  EXPECT_EQ(value_family(*owner.type, runtime), NONE);
  EXPECT_EQ(value_family(*RNA_RenderSettings, *property), NONE);
  Scope scope;
  EXPECT_EQ(value(scope, DRIVER_SIMPLE_EXPRESSION, owner, replaced).status, Status::UNSUPPORTED);
}

TEST_F(ScalarConstReadTest, RenderPassPrefixMatchesOrdinaryArrayAndOwnsItsCopy)
{
  RenderPass pass{};
  pass.rectx = 25;
  pass.recty = 1;
  pass.channels = 3;
  std::array<float, 75> buffer;
  for (size_t i = 0; i < buffer.size(); i++) {
    buffer[i] = float(i) + 0.25f;
  }
  ImBuf image{};
  image.float_buffer.data = buffer.data();
  pass.ibuf = &image;
  PointerRNA owner = RNA_pointer_create_discrete(nullptr, RNA_RenderPass, &pass);
  PropertyRNA *property = RNA_struct_find_property(&owner, "rect");
  ASSERT_NE(property, nullptr);
  ASSERT_EQ(value_family(*owner.type, *property), RENDER_PASS_RECT);
  std::array<float, 75> ordinary;
  RNA_property_float_get_array(&owner, property, ordinary.data());
  Scope scope;
  Value result = value(scope, RENDER_PASS_RECT, owner, *property);
  ASSERT_EQ(result.status, Status::OK);
  EXPECT_EQ(result.actual_length, 75);
  const auto &prefix = std::get<std::vector<float>>(result.data);
  ASSERT_EQ(prefix.size(), 64);
  for (size_t i = 0; i < prefix.size(); i++) {
    EXPECT_EQ(prefix[i], ordinary[i]);
  }
  buffer[0] = -12.0f;
  EXPECT_EQ(prefix[0], 0.25f);
  pass.ibuf = nullptr;
  const Value missing = value(scope, RENDER_PASS_RECT, owner, *property);
  ASSERT_EQ(missing.status, Status::OK);
  EXPECT_EQ(missing.actual_length, 75);
  for (const float value : std::get<std::vector<float>>(missing.data)) {
    EXPECT_EQ(value, 0.0f);
  }
}

TEST_F(ScalarConstReadTest, RenderPassInvalidLayoutIsRejectedBeforeBufferRead)
{
  RenderPass pass{};
  pass.rectx = INT_MAX;
  pass.recty = 2;
  pass.channels = 4;
  PointerRNA owner = RNA_pointer_create_discrete(nullptr, RNA_RenderPass, &pass);
  const PropertyRNA *property = RNA_struct_find_property(&owner, "rect");
  ASSERT_NE(property, nullptr);
  Scope scope;
  EXPECT_EQ(value(scope, RENDER_PASS_RECT, owner, *property).status, Status::INVALID_STORAGE);
  pass.rectx = -1;
  EXPECT_EQ(value(scope, RENDER_PASS_RECT, owner, *property).status, Status::INVALID_STORAGE);
}

TEST_F(ScalarConstReadTest, RenderThreadsPreservesFixedAndCommandLinePrecedence)
{
  const int previous_override = BLI_system_num_threads_override_get();
  BLI_SCOPED_DEFER([&]() { BLI_system_num_threads_override_set(previous_override); });
  RenderData render{};
  render.mode = R_FIXED_THREADS;
  render.threads = 17;
  PointerRNA owner = RNA_pointer_create_discrete(nullptr, RNA_RenderSettings, &render);
  const PropertyRNA *property = RNA_struct_find_property(&owner, "threads");
  ASSERT_NE(property, nullptr);
  Scope scope;
  BLI_system_num_threads_override_set(0);
  EXPECT_EQ(std::get<int>(value(scope, RENDER_THREADS, owner, *property).data), 17);
  BLI_system_num_threads_override_set(7);
  EXPECT_EQ(std::get<int>(value(scope, RENDER_THREADS, owner, *property).data), 7);
  EXPECT_EQ(BLI_system_thread_count_for_read(), 7);
  BLI_system_num_threads_override_set(0);
  /* Ordinary call seeds the sole system cache; read must observe this exact warm result. */
  const int warm = BLI_system_thread_count();
  EXPECT_EQ(BLI_system_thread_count_for_read(), warm);
}

TEST_F(ScalarConstReadTest, ConstraintTargetSpaceChoicesAreAnOrdinaryBuiltinGetter)
{
  /* Its built-in choice callback only builds temporary target descriptors; it is not
   * catalogued and runs as an ordinary getter. */
  PointerRNA owner;
  owner.type = RNA_Constraint;
  PropertyRNA *property = RNA_struct_find_property(&owner, "target_space");
  ASSERT_NE(property, nullptr);
  EXPECT_EQ(enum_family(*owner.type, *property), NONE);
  EXPECT_FALSE(catalogued(*property));
}

TEST_F(ScalarConstReadTest, NoContextActionSlotDeclarationRetainsNativeUnspecifiedName)
{
  PointerRNA owner;
  owner.type = RNA_ActionSlot;
  PropertyRNA *property = RNA_struct_find_property(&owner, "target_id_type");
  ASSERT_NE(property, nullptr);
  ASSERT_TRUE(property->flag & PROP_ENUM_NO_CONTEXT);
  ASSERT_EQ(enum_family(*owner.type, *property), ACTION_SLOT_ID_TYPE_ITEMS);
  /* The built-in callback publishes a process-lifetime item cache (why it is catalogued), so the
   * replacement is compared with the static table that callback copies, not with the callback. */
  Scope scope;
  const EnumResult result = enum_items(scope, ACTION_SLOT_ID_TYPE_ITEMS, owner, *property, nullptr, true);
  ASSERT_EQ(result.status, Status::OK);
  int count = 0;
  while (rna_enum_id_type_items[count].identifier) {
    count++;
  }
  ASSERT_EQ(result.items.size(), size_t(count + 1));
  for (int index = 0; index < count; index++) {
    EXPECT_EQ(result.items[index].value, rna_enum_id_type_items[index].value);
    EXPECT_EQ(result.items[index].identifier, rna_enum_id_type_items[index].identifier);
    EXPECT_EQ(result.items[index].name, rna_enum_id_type_items[index].name);
  }
  EXPECT_EQ(result.items.back().value, 0);
  EXPECT_EQ(result.items.back().identifier, "UNSPECIFIED");
  EXPECT_EQ(result.items.back().name, "Unspecified");
}

TEST_F(ScalarConstReadTest, PointCacheInfoIsWithheldAndActiveIndexMatchesWithoutDescriptors)
{
  Object object{};
  STRNCPY(object.id.name, "OBConstCache");
  SoftBody soft{};
  SoftBody_Shared shared{};
  PointCache previous{};
  PointCache cache{};
  object.soft = &soft;
  soft.shared = &shared;
  shared.pointcache = &cache;
  BLI_addtail(&shared.ptcaches, &previous);
  BLI_addtail(&shared.ptcaches, &cache);
  cache.flag = PTCACHE_FLAG_INFO_DIRTY | PTCACHE_EXTERNAL;
  cache.index = -1;
  STRNCPY(cache.info, "Existing metadata");
  const int flags = cache.flag;

  PointerRNA cache_owner(&object.id, RNA_PointCache, &cache);
  PropertyRNA *info = RNA_struct_find_property(&cache_owner, "info");
  ASSERT_NE(info, nullptr);
  ASSERT_EQ(value_family(*cache_owner.type, *info), POINTCACHE_INFO);
  Scope scope;
  EXPECT_EQ(value(scope, POINTCACHE_INFO, cache_owner, *info).status, Status::LOADS_FILE);
  EXPECT_EQ(cache.flag, flags);
  EXPECT_EQ(cache.index, -1);
  EXPECT_STREQ(cache.info, "Existing metadata");

  PointerRNA list_owner(&object.id, RNA_PointCaches, &cache);
  PropertyRNA *active = RNA_struct_find_property(&list_owner, "active_index");
  ASSERT_NE(active, nullptr);
  ASSERT_EQ(value_family(*list_owner.type, *active), POINTCACHE_ACTIVE_INDEX);
  const Value index = value(scope, POINTCACHE_ACTIVE_INDEX, list_owner, *active);
  ASSERT_EQ(index.status, Status::OK);
  EXPECT_EQ(std::get<int>(index.data), BLI_findindex(&shared.ptcaches, &cache));
  EXPECT_EQ(shared.pointcache, &cache);
  EXPECT_TRUE(object.pc_ids.is_empty());
}

TEST_F(ScalarConstReadTest, EmptyParentMatrixLocalMatchesOrdinaryTranslatedScaledRead)
{
  Object parent{};
  Object child{};
  bke::ObjectRuntime parent_runtime{};
  bke::ObjectRuntime child_runtime{};
  parent.type = OB_EMPTY;
  parent.data = nullptr;
  parent.runtime = &parent_runtime;
  child.runtime = &child_runtime;
  child.parent = &parent;
  child.partype = PAROBJECT;
  STRNCPY(parent.id.name, "OBEmptyParent");
  STRNCPY(child.id.name, "OBEmptyChild");
  float parent_world[4][4];
  float child_world[4][4];
  unit_m4(parent_world);
  unit_m4(child_world);
  parent_world[0][0] = 2.0f;
  parent_world[1][1] = 3.0f;
  parent_world[2][2] = 4.0f;
  parent_world[3][0] = 5.0f;
  parent_world[3][1] = -6.0f;
  parent_world[3][2] = 2.0f;
  child_world[0][0] = 4.0f;
  child_world[1][1] = 6.0f;
  child_world[2][2] = 8.0f;
  child_world[3][0] = 9.0f;
  child_world[3][1] = 0.0f;
  child_world[3][2] = 14.0f;
  parent_runtime.object_to_world = float4x4(parent_world);
  child_runtime.object_to_world = float4x4(child_world);
  PointerRNA owner;
  owner.owner_id = &child.id;
  owner.type = RNA_Object;
  owner.data = &child;
  PropertyRNA *property = RNA_struct_find_property(&owner, "matrix_local");
  ASSERT_NE(property, nullptr);
  ASSERT_EQ(value_family(*owner.type, *property), OBJECT_MATRIX_LOCAL);
  std::array<float, 16> ordinary;
  RNA_property_float_get_array(&owner, property, ordinary.data());
  Scope scope;
  const Value result = value(scope, OBJECT_MATRIX_LOCAL, owner, *property);
  ASSERT_EQ(result.status, Status::OK);
  const auto &matrix = std::get<std::vector<float>>(result.data);
  ASSERT_EQ(matrix.size(), 16u);
  for (size_t index = 0; index < matrix.size(); index++) {
    EXPECT_FLOAT_EQ(matrix[index], ordinary[index]);
    EXPECT_FLOAT_EQ(parent_runtime.object_to_world.ptr()[index / 4][index % 4], parent_world[index / 4][index % 4]);
  }
  EXPECT_FLOAT_EQ(matrix[12], 2.0f);
  EXPECT_FLOAT_EQ(matrix[13], 2.0f);
  EXPECT_FLOAT_EQ(matrix[14], 3.0f);
  EXPECT_EQ(parent.data, nullptr);
  EXPECT_EQ(child.parent, &parent);
}

static PropertyRNA *strip_fps(PointerRNA &owner)
{
  PropertyRNA *property = RNA_struct_find_property(&owner, "fps");
  EXPECT_NE(property, nullptr);
  if (property) {
    EXPECT_EQ(value_family(*owner.type, *property), STRIP_FPS);
  }
  return property;
}

TEST_F(ScalarConstReadTest, MovieStripFpsIsWithheldWithoutOpeningTheFile)
{
  Scene scene{};
  STRNCPY(scene.id.name, "SCStripOwner");
  Editing editing{};
  scene.ed = &editing;
  Strip strip{};
  StripData data{};
  StripElem element{};
  seq::StripRuntime runtime;
  strip.type = STRIP_TYPE_MOVIE;
  strip.data = &data;
  strip.runtime = &runtime;
  data.stripdata = &element;
  STRNCPY(data.dirpath, "/__angelo_read_missing_media_control__");
  STRNCPY(element.filename, "strip.mov");
  PointerRNA owner(&scene.id, RNA_MovieStrip, &strip);
  PropertyRNA *property = strip_fps(owner);
  ASSERT_NE(property, nullptr);
  Scope scope;
  EXPECT_EQ(value(scope, STRIP_FPS, owner, *property).status, Status::LOADS_FILE);
  EXPECT_TRUE(runtime.movie_readers.is_empty());
}

TEST_F(ScalarConstReadTest, ClipStripFpsNeedsAnOpenClipAndSceneStripFpsIsComputed)
{
  Scene scene{};
  STRNCPY(scene.id.name, "SCStripOwner");
  Editing editing{};
  scene.ed = &editing;
  MovieClip clip{};
  clip.source = MCLIP_SRC_MOVIE;
  Strip clip_strip{};
  seq::StripRuntime clip_runtime;
  clip_strip.type = STRIP_TYPE_MOVIECLIP;
  clip_strip.clip = &clip;
  clip_strip.runtime = &clip_runtime;
  PointerRNA clip_owner(&scene.id, RNA_MovieClipStrip, &clip_strip);
  PropertyRNA *clip_fps = strip_fps(clip_owner);
  ASSERT_NE(clip_fps, nullptr);
  Scope scope;
  EXPECT_EQ(value(scope, STRIP_FPS, clip_owner, *clip_fps).status, Status::LOADS_FILE);
  EXPECT_EQ(clip.anim, nullptr);
  clip.source = MCLIP_SRC_SEQUENCE;
  const Value sequence = value(scope, STRIP_FPS, clip_owner, *clip_fps);
  ASSERT_EQ(sequence.status, Status::OK);
  EXPECT_EQ(std::get<float>(sequence.data), 0.0f);

  Scene source{};
  source.r.frs_sec = 30;
  source.r.frs_sec_base = 1.001f;
  Strip scene_strip{};
  seq::StripRuntime scene_runtime;
  scene_strip.type = STRIP_TYPE_SCENE;
  scene_strip.scene = &source;
  scene_strip.runtime = &scene_runtime;
  PointerRNA scene_owner(&scene.id, RNA_SceneStrip, &scene_strip);
  PropertyRNA *scene_fps = strip_fps(scene_owner);
  ASSERT_NE(scene_fps, nullptr);
  const Value computed = value(scope, STRIP_FPS, scene_owner, *scene_fps);
  ASSERT_EQ(computed.status, Status::OK);
  EXPECT_FLOAT_EQ(std::get<float>(computed.data), scene_strip.media_fps(&scene));
}

}  // namespace blender::angelo_read_scalar::tests
