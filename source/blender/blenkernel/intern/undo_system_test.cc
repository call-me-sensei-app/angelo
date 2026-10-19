/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include <algorithm>
#include <atomic>
#include <thread>

#include "testing/testing.h"

#include "BLI_listbase.h"
#include "BLI_string.h"
#include "BLI_vector.hh"

#include "BKE_context.hh"
#include "BKE_callbacks.hh"
#include "BKE_global.hh"
#include "BKE_gtest_base.hh"
#include "BKE_lib_id.hh"
#include "BKE_main.hh"
#include "BKE_mesh.h"
#include "BKE_scene.hh"
#include "BKE_undo_system.hh"
#include "BKE_wm_runtime.hh"

#include "DNA_mesh_types.h"
#include "DNA_scene_types.h"
#include "DNA_userdef_types.h"
#include "DNA_windowmanager_types.h"

#include "DEG_depsgraph.hh"
#include "DEG_depsgraph_build.hh"

#include "MEM_guardedalloc.h"

namespace blender::bke::tests {

struct TestUndoStep {
  UndoStep step{};
  UndoDecodeResult result = UndoDecodeResult::Success;
  Main *main_to_install = nullptr;
  bool document_flag = false;
  int decode_calls = 0;
  int id_ref_calls = 0;
  UndoRefID id_ref{};
  UndoStack *test_stack = nullptr;
  Depsgraph *test_graph = nullptr;
  void (*during_decode)(bContext *, Main *, TestUndoStep &) = nullptr;
};

/** Test decoders use one Main flag as an observable document change, without file I/O. */
static UndoDecodeResult test_step_decode_checked(
    bContext *C, Main *bmain, UndoStep *us, eUndoStepDir /*dir*/, bool /*is_final*/)
{
  TestUndoStep &step = *reinterpret_cast<TestUndoStep *>(us);
  step.decode_calls++;
  if (step.result == UndoDecodeResult::Refused) {
    return step.result;
  }
  if (step.main_to_install) {
    bmain = step.main_to_install;
    G.main = bmain;
    CTX_data_main_set(C, bmain);
  }
  if (step.during_decode) {
    step.during_decode(C, bmain, step);
  }
  bmain->is_memfile_undo_flush_needed = step.document_flag;
  return step.result;
}

static void test_step_decode(
    bContext * /*C*/, Main *bmain, UndoStep *us, eUndoStepDir /*dir*/, bool /*is_final*/)
{
  TestUndoStep &step = *reinterpret_cast<TestUndoStep *>(us);
  step.decode_calls++;
  bmain->is_memfile_undo_flush_needed = step.document_flag;
}

static void test_step_foreach_id_ref(UndoStep *us,
                                     UndoTypeForEachIDRefFn foreach_id_ref_fn,
                                     void *user_data)
{
  TestUndoStep &step = *reinterpret_cast<TestUndoStep *>(us);
  step.id_ref_calls++;
  if (step.id_ref.name[0] != '\0') {
    foreach_id_ref_fn(user_data, &step.id_ref);
  }
}

class UndoSystemTestData {
 private:
  Main *previous_main_ = G_MAIN;
  const UndoType *previous_memfile_type_ = BKE_UNDOSYS_TYPE_MEMFILE;
  const UndoType *previous_sculpt_type_ = BKE_UNDOSYS_TYPE_SCULPT;
  Vector<Main *> owned_mains_;

 public:
  UndoType memfile_type{};
  UndoType sculpt_type{};
  bContext *C;
  UndoStack *stack;

  UndoSystemTestData()
  {
    memfile_type.name = "Test MemFile";
    memfile_type.step_size = sizeof(TestUndoStep);
    memfile_type.step_encode = [](bContext *, Main *, UndoStep *) { return true; };
    memfile_type.step_decode_checked = test_step_decode_checked;
    memfile_type.step_free = [](UndoStep *) {};

    sculpt_type.name = "Test Sculpt";
    sculpt_type.step_size = sizeof(TestUndoStep);
    sculpt_type.step_decode = test_step_decode;
    sculpt_type.step_foreach_ID_ref = test_step_foreach_id_ref;
    sculpt_type.step_free = [](UndoStep *) {};
    sculpt_type.flags = UNDOTYPE_FLAG_DECODE_ACTIVE_STEP;

    BKE_UNDOSYS_TYPE_MEMFILE = &memfile_type;
    BKE_UNDOSYS_TYPE_SCULPT = &sculpt_type;
    C = CTX_create();
    stack = BKE_undosys_stack_create();
    activate_main(create_main());
  }

  ~UndoSystemTestData()
  {
    BKE_undosys_stack_destroy(stack);
    CTX_free(C);
    BKE_UNDOSYS_TYPE_MEMFILE = previous_memfile_type_;
    BKE_UNDOSYS_TYPE_SCULPT = previous_sculpt_type_;
    G.main = previous_main_;
    for (Main *bmain : owned_mains_) {
      BKE_main_free(bmain);
    }
  }

  UndoSystemTestData(const UndoSystemTestData &) = delete;
  UndoSystemTestData &operator=(const UndoSystemTestData &) = delete;

  Main *create_main()
  {
    Main *bmain = BKE_main_new();
    owned_mains_.append(bmain);
    return bmain;
  }

  void activate_main(Main *bmain)
  {
    G.main = bmain;
    CTX_data_main_set(C, bmain);
  }

  TestUndoStep &append(const UndoType &type, const char *name, const bool skip = false)
  {
    TestUndoStep *step = MEM_new<TestUndoStep>(__func__);
    STRNCPY(step->step.name, name);
    step->step.type = &type;
    step->step.skip = skip;
    BLI_addtail(&stack->steps, &step->step);
    stack->step_active = &step->step;
    if (&type == &memfile_type) {
      stack->step_active_memfile = &step->step;
    }
    return *step;
  }
};

class UndoSystemCheckedDecodeTest : public BlenderGTestBase {};

TEST_F(UndoSystemCheckedDecodeTest, RestoreLeaseRequiresOuterTraversal)
{
  UndoSystemTestData data;
  EXPECT_EQ(BKE_undosys_restore_phase(), UndoRestorePhase::Inactive);
  EXPECT_FALSE(BKE_undosys_restore_activate(data.stack, CTX_data_main(data.C)));
  EXPECT_FALSE(BKE_undosys_restore_protects_main());
}

TEST_F(UndoSystemCheckedDecodeTest, RestoreLeaseBlocksMutationAndIsVisibleToWorker)
{
  UndoSystemTestData data;
  TestUndoStep &original = data.append(data.memfile_type, "Original");
  TestUndoStep &after = data.append(data.memfile_type, "Commit");
  original.test_stack = data.stack;
  original.during_decode = [](bContext *C, Main *bmain, TestUndoStep &step) {
    UndoStack *stack = step.test_stack;
    ASSERT_TRUE(BKE_undosys_restore_activate(stack, bmain));
    EXPECT_EQ(BKE_undosys_restore_phase(), UndoRestorePhase::Preparing);
    bool worker_observed = false;
    std::thread observer([&]() {
      worker_observed = BKE_undosys_restore_phase() == UndoRestorePhase::Preparing &&
                        BKE_undosys_restore_protects_main(bmain) &&
                        BKE_undosys_restore_protects_stack(stack) &&
                        !BKE_undosys_restore_activate(stack, bmain);
    });
    observer.join();
    EXPECT_TRUE(worker_observed);

    const UndoStep *old_active = stack->step_active;
    const UndoStep *old_memfile = stack->step_active_memfile;
    EXPECT_FALSE(BKE_undosys_step_load_data_ex(stack, C, &step.step, nullptr, false));
    EXPECT_EQ(BKE_undosys_step_push_with_type(stack, C, "Nested", step.step.type),
              UNDO_PUSH_RET_FAILURE);
    EXPECT_EQ(BKE_undosys_step_push_init_with_type(stack, C, "Nested", step.step.type), nullptr);
    BKE_undosys_stack_clear(stack);
    BKE_undosys_stack_clear_active(stack);
    BKE_undosys_stack_destroy(stack);
    BKE_undosys_stack_init_from_main(stack, bmain);
    BKE_undosys_stack_init_from_context(stack, C);
    BKE_undosys_stack_limit_steps_and_memory(stack, 0, 1);
    BKE_undosys_stack_group_begin(stack);
    BKE_undosys_stack_group_end(stack);
    EXPECT_EQ(stack->steps.count(), 2);
    EXPECT_EQ(stack->step_active, old_active);
    EXPECT_EQ(stack->step_active_memfile, old_memfile);
    EXPECT_EQ(stack->step_init, nullptr);
    EXPECT_EQ(stack->group_level, 0);
    EXPECT_FALSE(stack->recovery_required);
  };
  EXPECT_TRUE(BKE_undosys_step_undo(data.stack, data.C));
  EXPECT_EQ(data.stack->step_active, &original.step);
  EXPECT_EQ(BKE_undosys_restore_phase(), UndoRestorePhase::Inactive);
  EXPECT_FALSE(BKE_undosys_restore_protects_main());
  EXPECT_FALSE(BKE_undosys_restore_protects_stack(data.stack));
  EXPECT_EQ(data.stack->steps.count(), 2);
  EXPECT_EQ(original.step.next, &after.step);
  BKE_undosys_stack_clear(data.stack);
  EXPECT_TRUE(data.stack->steps.is_empty());
}

/** Give a capture a real current Main/WM owner without opening a native window. */
class CaptureTestOwner {
  wmWindowManager *wm_;

 public:
  explicit CaptureTestOwner(UndoSystemTestData &data)
  {
    wm_ = BKE_id_new<wmWindowManager>(CTX_data_main(data.C), "Capture owner");
    wm_->runtime = MEM_new<bke::WindowManagerRuntime>(__func__);
    wm_->runtime->undo_stack = data.stack;
  }
  ~CaptureTestOwner()
  {
    /* UndoSystemTestData owns and frees this stack before Main's WM destructor. */
    wm_->runtime->undo_stack = nullptr;
  }
};

TEST_F(UndoSystemCheckedDecodeTest, CaptureRequiresItsActualCurrentMainOwner)
{
  UndoSystemTestData data;
  data.append(data.memfile_type, "Original");
  EXPECT_EQ(BKE_undosys_capture_begin(CTX_data_main(data.C), data.stack), nullptr);
  CaptureTestOwner owner(data);
  UndoCaptureScope *scope = BKE_undosys_capture_begin(CTX_data_main(data.C), data.stack);
  ASSERT_NE(scope, nullptr);
  EXPECT_TRUE(BKE_undosys_capture_validate(scope));
  EXPECT_EQ(BKE_undosys_capture_begin(CTX_data_main(data.C), data.stack), nullptr);
  BKE_undosys_capture_end(scope);
  EXPECT_EQ(BKE_undosys_restore_phase(), UndoRestorePhase::Inactive);
}

TEST_F(UndoSystemCheckedDecodeTest, CaptureRefusesDuringUnmarkedDecodeWithoutActivatingLease)
{
  UndoSystemTestData data;
  TestUndoStep &original = data.append(data.memfile_type, "Original");
  data.append(data.memfile_type, "Later");
  CaptureTestOwner owner(data);
  original.test_stack = data.stack;
  original.during_decode = [](bContext *, Main *bmain, TestUndoStep &step) {
    EXPECT_EQ(BKE_undosys_restore_phase(), UndoRestorePhase::Inactive);
    EXPECT_EQ(BKE_undosys_capture_begin(bmain, step.test_stack), nullptr);
    EXPECT_EQ(BKE_undosys_restore_phase(), UndoRestorePhase::Inactive);
    EXPECT_EQ(step.test_stack->steps.count(), 2);
  };
  EXPECT_TRUE(BKE_undosys_step_undo(data.stack, data.C));
  EXPECT_EQ(original.decode_calls, 1);
  EXPECT_EQ(data.stack->step_active, &original.step);
}

TEST_F(UndoSystemCheckedDecodeTest, CaptureProtectsHistoryOnMainAndEvaluationWorker)
{
  UndoSystemTestData data;
  TestUndoStep &original = data.append(data.memfile_type, "Original");
  TestUndoStep &after = data.append(data.memfile_type, "Existing redo");
  data.stack->step_active = data.stack->step_active_memfile = &original.step;
  CaptureTestOwner owner(data);
  Main *bmain = CTX_data_main(data.C);
  UndoCaptureScope *scope = BKE_undosys_capture_begin(bmain, data.stack);
  ASSERT_NE(scope, nullptr);
  std::thread worker([&]() {
    EXPECT_TRUE(BKE_undosys_restore_protects_main(bmain));
    EXPECT_TRUE(BKE_undosys_restore_protects_stack(data.stack));
  });
  worker.join();
  BKE_undosys_stack_clear(data.stack);
  BKE_undosys_stack_clear_active(data.stack);
  BKE_undosys_stack_limit_steps_and_memory(data.stack, 0, 1);
  EXPECT_FALSE(BKE_undosys_step_redo(data.stack, data.C));
  EXPECT_EQ(data.stack->steps.count(), 2);
  EXPECT_EQ(data.stack->step_active, &original.step);
  EXPECT_EQ(original.step.next, &after.step);
  EXPECT_TRUE(BKE_undosys_capture_validate(scope));
  BKE_undosys_capture_end(scope);
  EXPECT_TRUE(BKE_undosys_step_redo(data.stack, data.C));
}

TEST_F(UndoSystemCheckedDecodeTest, CaptureReleaseDoesNotRunEditorDelivery)
{
  UndoSystemTestData data;
  data.append(data.memfile_type, "Original");
  CaptureTestOwner owner(data);
  UndoCaptureScope *scope = BKE_undosys_capture_begin(CTX_data_main(data.C), data.stack);
  ASSERT_NE(scope, nullptr);
  /* With no requested finalization, release must only drop owner protection. */
  BKE_undosys_capture_end(scope);
  EXPECT_EQ(data.stack->steps.count(), 1);
  EXPECT_FALSE(BKE_undosys_restore_protects_main());
}

static int restore_editor_calls = 0;
static UndoStack *restore_editor_stack = nullptr;
static UndoStep *restore_editor_final_step = nullptr;
static Depsgraph *restore_editor_graph = nullptr;
static void restore_test_editor_update(const DEGEditorUpdateContext *update, bool updated)
{
  restore_editor_calls++;
  EXPECT_EQ(BKE_undosys_restore_phase(), UndoRestorePhase::Finalizing);
  EXPECT_TRUE(BKE_undosys_restore_protects_main(update->bmain));
  EXPECT_EQ(restore_editor_stack->step_active, restore_editor_final_step);
  EXPECT_EQ(update->depsgraph, restore_editor_graph);
  EXPECT_TRUE(updated);
  BKE_undosys_stack_clear(restore_editor_stack);
  EXPECT_EQ(restore_editor_stack->steps.count(), 2);
}

TEST_F(UndoSystemCheckedDecodeTest, CapturePublishesOnceBeforeOneShotEditorDelivery)
{
  UndoSystemTestData data;
  Main *bmain = CTX_data_main(data.C);
  Scene *scene = BKE_id_new<Scene>(bmain, "Scene");
  Depsgraph *graph = BKE_scene_ensure_depsgraph(
      bmain, scene, static_cast<ViewLayer *>(scene->view_layers.first));
  data.append(data.memfile_type, "Original");
  CaptureTestOwner owner(data);
  UndoCaptureScope *scope = BKE_undosys_capture_begin(bmain, data.stack);
  ASSERT_NE(scope, nullptr);
  TestUndoStep *after = MEM_new<TestUndoStep>(__func__);
  after->step.type = &data.memfile_type;
  STRNCPY(after->step.name, "Captured commit");
  restore_editor_calls = 0;
  restore_editor_stack = data.stack;
  restore_editor_final_step = &after->step;
  restore_editor_graph = graph;
  DEG_editors_set_update_cb(nullptr, restore_test_editor_update);
  EXPECT_TRUE(BKE_undosys_restore_defer_editor_update(bmain, graph, false, true));
  EXPECT_TRUE(BKE_undosys_restore_defer_editor_update(bmain, graph, true, false));
  EXPECT_EQ(restore_editor_calls, 0);
  EXPECT_TRUE(BKE_undosys_capture_publish(scope, nullptr, &after->step));
  EXPECT_FALSE(BKE_undosys_capture_publish(scope, nullptr, &after->step));
  EXPECT_EQ(restore_editor_calls, 0);
  EXPECT_TRUE(BKE_undosys_capture_finalize(scope, data.C));
  EXPECT_EQ(restore_editor_calls, 1);
  EXPECT_TRUE(BKE_undosys_capture_finalize(scope, data.C));
  EXPECT_EQ(restore_editor_calls, 1);
  BKE_undosys_capture_end(scope);
  EXPECT_EQ(restore_editor_calls, 1);
  EXPECT_EQ(BKE_undosys_restore_phase(), UndoRestorePhase::Inactive);
  DEG_editors_set_update_cb(nullptr, nullptr);
}

TEST_F(UndoSystemCheckedDecodeTest, EditorDeliveryWaitsForFinalHistoryAndCombinesFrameUpdate)
{
  UndoSystemTestData data;
  Main *bmain = CTX_data_main(data.C);
  Scene *scene = BKE_id_new<Scene>(bmain, "Scene");
  Depsgraph *graph = BKE_scene_ensure_depsgraph(
      bmain, scene, static_cast<ViewLayer *>(scene->view_layers.first));
  TestUndoStep &original = data.append(data.memfile_type, "Original");
  data.append(data.memfile_type, "Commit");
  original.test_stack = data.stack;
  original.test_graph = graph;
  original.during_decode = [](bContext *, Main *main, TestUndoStep &step) {
    EXPECT_TRUE(BKE_undosys_restore_activate(step.test_stack, main));
    EXPECT_TRUE(BKE_undosys_restore_defer_editor_update(main, step.test_graph, false, true));
    EXPECT_TRUE(BKE_undosys_restore_defer_editor_update(main, step.test_graph, true, false));
    EXPECT_EQ(restore_editor_calls, 0);
    EXPECT_NE(step.test_stack->step_active, &step.step);
  };
  restore_editor_calls = 0;
  restore_editor_stack = data.stack;
  restore_editor_final_step = &original.step;
  restore_editor_graph = graph;
  DEG_editors_set_update_cb(nullptr, restore_test_editor_update);
  EXPECT_TRUE(BKE_undosys_step_undo(data.stack, data.C));
  DEG_editors_set_update_cb(nullptr, nullptr);
  EXPECT_EQ(restore_editor_calls, 1);
  EXPECT_EQ(BKE_undosys_restore_phase(), UndoRestorePhase::Inactive);
  EXPECT_FALSE(BKE_undosys_restore_defer_editor_update(bmain, graph, false, true));
}

TEST_F(UndoSystemCheckedDecodeTest, DeferredGraphAddressDoesNotReplaceItsNewOwnerIdentity)
{
  UndoSystemTestData data;
  Main *bmain = CTX_data_main(data.C);
  Scene *old_scene = BKE_id_new<Scene>(bmain, "Rebound");
  Scene *new_scene = BKE_id_new<Scene>(bmain, "Other");
  ViewLayer *old_layer = static_cast<ViewLayer *>(old_scene->view_layers.first);
  ViewLayer *new_layer = static_cast<ViewLayer *>(new_scene->view_layers.first);
  Depsgraph *graph = BKE_scene_ensure_depsgraph(bmain, old_scene, old_layer);
  data.append(data.memfile_type, "Original");
  TestUndoStep &active = data.append(data.memfile_type, "Current");
  CaptureTestOwner owner(data);
  UndoCaptureScope *scope = BKE_undosys_capture_begin(bmain, data.stack);
  ASSERT_NE(scope, nullptr);
  EXPECT_TRUE(BKE_undosys_restore_defer_editor_update(bmain, graph, false, true));
  /* Deterministically reuse the exact graph address for another live canonical owner, without
   * depending on allocator reuse. This models the identity hazard of disposed stage graphs. */
  GHash *extracted = BKE_scene_undo_depsgraphs_extract(bmain);
  BKE_id_rename(*bmain, old_scene->id, "Retired");
  BKE_id_rename(*bmain, new_scene->id, "Rebound");
  BKE_scene_undo_depsgraphs_restore(bmain, extracted);
  EXPECT_EQ(BKE_scene_get_depsgraph(old_scene, old_layer), nullptr);
  ASSERT_EQ(BKE_scene_get_depsgraph(new_scene, new_layer), graph);
  EXPECT_TRUE(BKE_undosys_restore_defer_editor_update(bmain, graph, true, false));
  restore_editor_calls = 0;
  restore_editor_stack = data.stack;
  restore_editor_final_step = &active.step;
  restore_editor_graph = graph;
  DEG_editors_set_update_cb(nullptr, restore_test_editor_update);
  EXPECT_TRUE(BKE_undosys_capture_finalize(scope, data.C));
  EXPECT_EQ(restore_editor_calls, 1);
  DEG_editors_set_update_cb(nullptr, nullptr);
  BKE_undosys_capture_end(scope);
}

TEST_F(UndoSystemCheckedDecodeTest, NonEditorGraphsKeepTheirImmediateFinalizationPolicy)
{
  UndoSystemTestData data;
  Main *bmain = CTX_data_main(data.C);
  Scene *scene = BKE_id_new<Scene>(bmain, "Scene");
  Depsgraph *graph = DEG_graph_new(
      bmain, scene, static_cast<ViewLayer *>(scene->view_layers.first), DAG_EVAL_RENDER);
  data.append(data.memfile_type, "Original");
  CaptureTestOwner owner(data);
  UndoCaptureScope *scope = BKE_undosys_capture_begin(bmain, data.stack);
  ASSERT_NE(scope, nullptr);
  EXPECT_FALSE(DEG_editors_update_is_enabled(graph));
  std::thread worker([&]() {
    EXPECT_FALSE(BKE_undosys_restore_defer_editor_update(bmain, graph, false, true));
  });
  worker.join();
  EXPECT_FALSE(BKE_undosys_restore_defer_editor_update(bmain, graph, true, false));
  EXPECT_FALSE(BKE_undosys_restore_defer_editor_update(bmain, graph, false, true));
  EXPECT_TRUE(BKE_undosys_capture_validate(scope));
  EXPECT_TRUE(BKE_undosys_capture_finalize(scope, data.C));
  BKE_undosys_capture_end(scope);
  DEG_graph_free(graph);
}

TEST_F(UndoSystemCheckedDecodeTest, UnexpectedNoncanonicalEditorOwnerFailsWithoutDelivery)
{
  UndoSystemTestData data;
  Main *bmain = CTX_data_main(data.C);
  Scene *scene = BKE_id_new<Scene>(bmain, "Scene");
  Depsgraph *graph = DEG_graph_new(
      bmain, scene, static_cast<ViewLayer *>(scene->view_layers.first), DAG_EVAL_VIEWPORT);
  DEG_enable_editors_update(graph); /* No production caller enables this noncanonical graph. */
  data.append(data.memfile_type, "Original");
  CaptureTestOwner owner(data);
  UndoCaptureScope *scope = BKE_undosys_capture_begin(bmain, data.stack);
  ASSERT_NE(scope, nullptr);
  EXPECT_TRUE(BKE_undosys_restore_defer_editor_update(bmain, graph, true, true));
  EXPECT_FALSE(BKE_undosys_capture_finalize(scope, data.C));
  BKE_undosys_capture_end(scope);
  EXPECT_EQ(BKE_undosys_restore_phase(), UndoRestorePhase::Inactive);
  DEG_graph_free(graph);
}

static int restore_cancel_count = 0;
static Main *restore_cancel_main = nullptr;
static void restore_test_cancel(Main *bmain)
{
  restore_cancel_count++;
  restore_cancel_main = bmain;
  EXPECT_EQ(BKE_undosys_restore_phase(), UndoRestorePhase::Preparing);
}

TEST_F(UndoSystemCheckedDecodeTest, InitialLeaseRefusalDoesNotCancelExistingWork)
{
  UndoSystemTestData data;
  TestUndoStep &original = data.append(data.memfile_type, "Original");
  TestUndoStep &after = data.append(data.memfile_type, "Commit");
  original.test_stack = data.stack;
  original.during_decode = [](bContext *, Main *bmain, TestUndoStep &step) {
    EXPECT_TRUE(BKE_undosys_restore_activate(step.test_stack, bmain, restore_test_cancel));
    step.result = UndoDecodeResult::Refused;
  };
  restore_cancel_count = 0;
  EXPECT_FALSE(BKE_undosys_step_undo(data.stack, data.C));
  EXPECT_EQ(restore_cancel_count, 0);
  EXPECT_EQ(BKE_undosys_restore_phase(), UndoRestorePhase::Inactive);
  EXPECT_FALSE(data.stack->recovery_required);
  EXPECT_EQ(data.stack->step_active, &after.step);
}

TEST_F(UndoSystemCheckedDecodeTest, FailedLeaseCancelsIntermediateWorkAndReleasesAdmission)
{
  UndoSystemTestData data;
  TestUndoStep &original = data.append(data.memfile_type, "Original");
  data.append(data.memfile_type, "Commit");
  original.test_stack = data.stack;
  original.result = UndoDecodeResult::FailedStateInstalled;
  original.during_decode = [](bContext *, Main *bmain, TestUndoStep &step) {
    EXPECT_TRUE(BKE_undosys_restore_activate(step.test_stack, bmain, restore_test_cancel));
    BKE_undosys_restore_before_memfile_read(step.test_stack);
  };
  restore_cancel_count = 0;
  restore_cancel_main = nullptr;
  EXPECT_FALSE(BKE_undosys_step_undo(data.stack, data.C));
  EXPECT_EQ(restore_cancel_count, 1);
  EXPECT_EQ(restore_cancel_main, CTX_data_main(data.C));
  EXPECT_EQ(BKE_undosys_restore_phase(), UndoRestorePhase::Inactive);
  EXPECT_TRUE(data.stack->recovery_required);
  EXPECT_EQ(data.stack->step_active, &original.step);
}

TEST_F(UndoSystemCheckedDecodeTest, LeasePersistsAcrossIntermediateMainReplacement)
{
  UndoSystemTestData data;
  Main *initial = CTX_data_main(data.C);
  Main *replacement = data.create_main();
  TestUndoStep &original = data.append(data.memfile_type, "Original");
  TestUndoStep &middle = data.append(data.memfile_type, "Middle");
  TestUndoStep &after = data.append(data.memfile_type, "Commit");
  original.test_stack = middle.test_stack = after.test_stack = data.stack;
  middle.during_decode = [](bContext *, Main *bmain, TestUndoStep &step) {
    EXPECT_TRUE(BKE_undosys_restore_activate(step.test_stack, bmain, restore_test_cancel));
  };
  after.main_to_install = replacement;
  after.during_decode = [](bContext *, Main *bmain, TestUndoStep &step) {
    EXPECT_EQ(BKE_undosys_restore_phase(), UndoRestorePhase::Preparing);
    EXPECT_FALSE(BKE_undosys_restore_protects_main(bmain));
    BKE_undosys_restore_before_memfile_read(step.test_stack);
    EXPECT_TRUE(BKE_undosys_restore_rebind_main(step.test_stack, bmain));
    EXPECT_TRUE(BKE_undosys_restore_protects_main(bmain));
  };
  data.stack->step_active = &original.step;
  data.stack->step_active_memfile = &original.step;
  restore_cancel_count = 0;
  EXPECT_TRUE(BKE_undosys_step_load_data(data.stack, data.C, &after.step));
  EXPECT_EQ(CTX_data_main(data.C), replacement);
  EXPECT_NE(initial, replacement);
  EXPECT_EQ(restore_cancel_count, 0);
  EXPECT_EQ(BKE_undosys_restore_phase(), UndoRestorePhase::Inactive);
}

TEST_F(UndoSystemCheckedDecodeTest, CheckedRefusalPreservesHistoryWithoutQuarantine)
{
  UndoSystemTestData data;
  Main *bmain = CTX_data_main(data.C);
  TestUndoStep &original = data.append(data.memfile_type, "Original");
  TestUndoStep &after = data.append(data.memfile_type, "Commit");
  original.result = UndoDecodeResult::Refused;
  original.document_flag = true;
  ASSERT_FALSE(bmain->is_memfile_undo_flush_needed);

  EXPECT_FALSE(BKE_undosys_step_undo(data.stack, data.C));
  EXPECT_EQ(CTX_data_main(data.C), bmain);
  EXPECT_FALSE(bmain->is_memfile_undo_flush_needed);
  EXPECT_FALSE(bmain->is_undo_recovery_required);
  EXPECT_FALSE(data.stack->recovery_required);
  EXPECT_EQ(data.stack->step_active, &after.step);
  EXPECT_EQ(data.stack->step_active_memfile, &after.step);
  EXPECT_EQ(data.stack->steps.count(), 2);
  EXPECT_EQ(original.step.next, &after.step);
  EXPECT_EQ(after.step.prev, &original.step);
  EXPECT_EQ(original.decode_calls, 1);
  EXPECT_EQ(after.decode_calls, 0);

  original.result = UndoDecodeResult::Success;
  EXPECT_TRUE(BKE_undosys_step_undo(data.stack, data.C));
  EXPECT_EQ(data.stack->step_active, &original.step);
  EXPECT_TRUE(bmain->is_memfile_undo_flush_needed);
  EXPECT_FALSE(BKE_undosys_recovery_required(data.stack, bmain));
}

TEST_F(UndoSystemCheckedDecodeTest, LaterRefusalKeepsLastSuccessfullyRestoredBoundary)
{
  UndoSystemTestData data;
  Main *bmain = CTX_data_main(data.C);
  TestUndoStep &original = data.append(data.memfile_type, "Original");
  TestUndoStep &restored = data.append(data.memfile_type, "Intermediate state");
  TestUndoStep &refused = data.append(data.memfile_type, "Refused boundary", true);
  TestUndoStep &target = data.append(data.memfile_type, "History target");
  restored.document_flag = true;
  refused.result = UndoDecodeResult::Refused;
  data.stack->step_active = &original.step;
  data.stack->step_active_memfile = &original.step;

  EXPECT_FALSE(BKE_undosys_step_load_from_index(data.stack, data.C, 3));
  EXPECT_EQ(restored.decode_calls, 1);
  EXPECT_EQ(refused.decode_calls, 1);
  EXPECT_EQ(target.decode_calls, 0);
  EXPECT_EQ(data.stack->step_active, &restored.step);
  EXPECT_EQ(data.stack->step_active_memfile, &restored.step);
  EXPECT_TRUE(bmain->is_memfile_undo_flush_needed);
  EXPECT_FALSE(bmain->is_undo_recovery_required);
  EXPECT_FALSE(data.stack->recovery_required);
  EXPECT_EQ(data.stack->steps.count(), 4);
  EXPECT_EQ(restored.step.next, &refused.step);
  EXPECT_EQ(refused.step.next, &target.step);
}

TEST_F(UndoSystemCheckedDecodeTest, PendingStepRefusesBeforeDecodeAndRetainsOwnership)
{
  UndoSystemTestData data;
  Main *bmain = CTX_data_main(data.C);
  TestUndoStep &original = data.append(data.memfile_type, "Original");
  TestUndoStep &after = data.append(data.memfile_type, "Commit");
  data.sculpt_type.step_encode_init = [](bContext *, UndoStep *) {};
  UndoStep *pending = BKE_undosys_step_push_init_with_type(
      data.stack, data.C, "Unfinished stroke", &data.sculpt_type);
  ASSERT_NE(pending, nullptr);

  EXPECT_FALSE(BKE_undosys_step_undo(data.stack, data.C));
  EXPECT_FALSE(BKE_undosys_step_load_from_index(data.stack, data.C, 0));
  EXPECT_EQ(original.decode_calls, 0);
  EXPECT_EQ(after.decode_calls, 0);
  EXPECT_EQ(data.stack->step_init, pending);
  EXPECT_EQ(pending->type, &data.sculpt_type);
  EXPECT_EQ(data.stack->step_active, &after.step);
  EXPECT_EQ(data.stack->step_active_memfile, &after.step);
  EXPECT_EQ(data.stack->steps.count(), 2);
  EXPECT_FALSE(bmain->is_undo_recovery_required);
  EXPECT_FALSE(data.stack->recovery_required);

  /* The unfinished operation still owns its detached step and may cancel it normally. */
  data.stack->step_init = nullptr;
  pending->type->step_free(pending);
  MEM_delete(pending);
  EXPECT_TRUE(BKE_undosys_step_undo(data.stack, data.C));
  EXPECT_EQ(original.decode_calls, 1);
  EXPECT_FALSE(BKE_undosys_recovery_required(data.stack, bmain));
}

TEST_F(UndoSystemCheckedDecodeTest, OpenGroupRefusesBeforeDecodeWithoutPoisoningHistory)
{
  UndoSystemTestData data;
  Main *bmain = CTX_data_main(data.C);
  TestUndoStep &original = data.append(data.memfile_type, "Original");
  TestUndoStep &after = data.append(data.memfile_type, "Commit");
  BKE_undosys_stack_group_begin(data.stack);
  BKE_undosys_stack_group_begin(data.stack);

  EXPECT_FALSE(BKE_undosys_step_undo(data.stack, data.C));
  EXPECT_FALSE(BKE_undosys_step_load_from_index(data.stack, data.C, 0));
  EXPECT_EQ(original.decode_calls, 0);
  EXPECT_EQ(after.decode_calls, 0);
  EXPECT_EQ(data.stack->group_level, 2);
  EXPECT_EQ(data.stack->step_active, &after.step);
  EXPECT_EQ(data.stack->step_active_memfile, &after.step);
  EXPECT_EQ(data.stack->steps.count(), 2);
  EXPECT_FALSE(bmain->is_undo_recovery_required);
  EXPECT_FALSE(data.stack->recovery_required);

  BKE_undosys_stack_group_end(data.stack);
  BKE_undosys_stack_group_end(data.stack);
  EXPECT_TRUE(BKE_undosys_step_undo(data.stack, data.C));
  EXPECT_EQ(original.decode_calls, 1);
  EXPECT_FALSE(BKE_undosys_recovery_required(data.stack, bmain));
}

TEST_F(UndoSystemCheckedDecodeTest, HiddenInstalledFailureStopsUndoBeforeSculpt)
{
  UndoSystemTestData data;
  Main *old_main = CTX_data_main(data.C);
  Main *installed_main = data.create_main();
  TestUndoStep &original = data.append(data.memfile_type, "Original");
  TestUndoStep &sculpt = data.append(data.sculpt_type, "Artist stroke");
  TestUndoStep &before = data.append(data.memfile_type, "Commit baseline", true);
  TestUndoStep &after = data.append(data.memfile_type, "Commit");
  before.result = UndoDecodeResult::FailedStateInstalled;
  before.main_to_install = installed_main;
  before.document_flag = true;

  EXPECT_FALSE(BKE_undosys_step_undo(data.stack, data.C));
  EXPECT_EQ(CTX_data_main(data.C), installed_main);
  EXPECT_EQ(G_MAIN, installed_main);
  EXPECT_TRUE(installed_main->is_memfile_undo_flush_needed);
  EXPECT_TRUE(installed_main->is_undo_recovery_required);
  EXPECT_FALSE(old_main->is_undo_recovery_required);
  EXPECT_TRUE(data.stack->recovery_required);
  EXPECT_EQ(data.stack->step_active, &before.step);
  EXPECT_EQ(data.stack->step_active_memfile, &before.step);
  EXPECT_TRUE(before.step.skip);
  EXPECT_EQ(before.decode_calls, 1);
  EXPECT_EQ(sculpt.decode_calls, 0);
  EXPECT_EQ(sculpt.id_ref_calls, 0);
  EXPECT_EQ(original.decode_calls, 0);
  EXPECT_EQ(after.decode_calls, 0);

  /* Failure keeps every historical and redo step; it neither rolls back nor discards a branch. */
  EXPECT_EQ(data.stack->steps.count(), 4);
  EXPECT_EQ(original.step.next, &sculpt.step);
  EXPECT_EQ(sculpt.step.prev, &original.step);
  EXPECT_EQ(sculpt.step.next, &before.step);
  EXPECT_EQ(before.step.prev, &sculpt.step);
  EXPECT_EQ(before.step.next, &after.step);
  EXPECT_EQ(after.step.prev, &before.step);
  EXPECT_FALSE(BKE_undosys_step_undo(data.stack, data.C));
  EXPECT_FALSE(BKE_undosys_step_redo(data.stack, data.C));
  EXPECT_FALSE(BKE_undosys_step_load_from_index(data.stack, data.C, 0));
  EXPECT_EQ(before.decode_calls, 1);
  EXPECT_EQ(sculpt.decode_calls, 0);
  EXPECT_EQ(after.decode_calls, 0);
}

TEST_F(UndoSystemCheckedDecodeTest, ForwardHistoryJumpStopsAtInstalledFailure)
{
  UndoSystemTestData data;
  TestUndoStep &original = data.append(data.memfile_type, "Original");
  TestUndoStep &sculpt = data.append(data.sculpt_type, "Artist stroke");
  TestUndoStep &before = data.append(data.memfile_type, "Commit baseline", true);
  TestUndoStep &after = data.append(data.memfile_type, "Commit");
  before.result = UndoDecodeResult::FailedStateInstalled;
  before.document_flag = true;
  data.stack->step_active = &original.step;
  data.stack->step_active_memfile = &original.step;

  EXPECT_FALSE(BKE_undosys_step_load_from_index(data.stack, data.C, 3));
  EXPECT_EQ(sculpt.decode_calls, 1);
  EXPECT_EQ(before.decode_calls, 1);
  EXPECT_EQ(after.decode_calls, 0);
  EXPECT_EQ(data.stack->step_active, &before.step);
  EXPECT_EQ(data.stack->step_active_memfile, &before.step);
  EXPECT_EQ(data.stack->steps.count(), 4);
  EXPECT_TRUE(CTX_data_main(data.C)->is_memfile_undo_flush_needed);
  EXPECT_TRUE(BKE_undosys_recovery_required(data.stack, CTX_data_main(data.C)));
}

TEST_F(UndoSystemCheckedDecodeTest, RecursiveMemFileFailurePreventsIdResolutionAndSculptDecode)
{
  UndoSystemTestData data;
  Mesh *mesh = BKE_mesh_add(CTX_data_main(data.C), "Referenced mesh");
  TestUndoStep &original = data.append(data.memfile_type, "Original");
  TestUndoStep &sculpt = data.append(data.sculpt_type, "Artist stroke");
  TestUndoStep &after = data.append(data.memfile_type, "Later Main");
  original.result = UndoDecodeResult::FailedStateInstalled;
  STRNCPY(sculpt.id_ref.name, mesh->id.name);

  EXPECT_FALSE(BKE_undosys_step_load_data(data.stack, data.C, &sculpt.step));
  EXPECT_EQ(original.decode_calls, 1);
  EXPECT_EQ(sculpt.id_ref_calls, 0);
  EXPECT_EQ(sculpt.id_ref.ptr, nullptr);
  EXPECT_EQ(sculpt.decode_calls, 0);
  EXPECT_EQ(after.decode_calls, 0);
  EXPECT_EQ(data.stack->step_active, &original.step);
  EXPECT_EQ(data.stack->step_active_memfile, &original.step);
  EXPECT_TRUE(BKE_undosys_recovery_required(data.stack, CTX_data_main(data.C)));
}

TEST_F(UndoSystemCheckedDecodeTest, SuccessfulRecursiveMemFilePreservesLegacyDecode)
{
  UndoSystemTestData data;
  Mesh *mesh = BKE_mesh_add(CTX_data_main(data.C), "Referenced mesh");
  TestUndoStep &original = data.append(data.memfile_type, "Original");
  TestUndoStep &sculpt = data.append(data.sculpt_type, "Artist stroke");
  TestUndoStep &after = data.append(data.memfile_type, "Later Main");
  STRNCPY(sculpt.id_ref.name, mesh->id.name);
  sculpt.document_flag = true;

  EXPECT_TRUE(BKE_undosys_step_load_data(data.stack, data.C, &sculpt.step));
  EXPECT_EQ(original.decode_calls, 1);
  EXPECT_EQ(sculpt.id_ref_calls, 1);
  EXPECT_EQ(sculpt.id_ref.ptr, &mesh->id);
  EXPECT_EQ(sculpt.decode_calls, 1);
  EXPECT_EQ(after.decode_calls, 0);
  EXPECT_EQ(data.stack->step_active, &sculpt.step);
  EXPECT_EQ(data.stack->step_active_memfile, &original.step);
  EXPECT_TRUE(CTX_data_main(data.C)->is_memfile_undo_flush_needed);
  EXPECT_FALSE(BKE_undosys_recovery_required(data.stack, CTX_data_main(data.C)));
}

TEST_F(UndoSystemCheckedDecodeTest, FailureWithoutMainInstallationStillQuarantinesPartialChanges)
{
  UndoSystemTestData data;
  Main *bmain = CTX_data_main(data.C);
  TestUndoStep &original = data.append(data.memfile_type, "Original");
  TestUndoStep &after = data.append(data.memfile_type, "Commit");
  original.result = UndoDecodeResult::FailedNoMainInstalled;
  original.document_flag = true;
  ASSERT_FALSE(bmain->is_memfile_undo_flush_needed);

  EXPECT_FALSE(BKE_undosys_step_undo(data.stack, data.C));
  EXPECT_EQ(CTX_data_main(data.C), bmain);
  EXPECT_TRUE(bmain->is_memfile_undo_flush_needed);
  EXPECT_TRUE(bmain->is_undo_recovery_required);
  EXPECT_TRUE(data.stack->recovery_required);
  EXPECT_EQ(data.stack->step_active, &after.step);
  EXPECT_EQ(data.stack->step_active_memfile, &after.step);
  EXPECT_EQ(original.decode_calls, 1);
  EXPECT_FALSE(BKE_undosys_step_load_from_index(data.stack, data.C, 1));
  EXPECT_EQ(after.decode_calls, 0);
}

TEST_F(UndoSystemCheckedDecodeTest, QuarantineSurvivesHistoryResetUntilFreshMain)
{
  UndoSystemTestData data;
  Main *failed_main = CTX_data_main(data.C);
  TestUndoStep &original = data.append(data.memfile_type, "Original");
  data.append(data.memfile_type, "Commit");
  original.result = UndoDecodeResult::FailedNoMainInstalled;
  ASSERT_FALSE(BKE_undosys_step_undo(data.stack, data.C));
  ASSERT_TRUE(failed_main->is_undo_recovery_required);

  BKE_undosys_stack_clear(data.stack);
  EXPECT_TRUE(data.stack->steps.is_empty());
  EXPECT_TRUE(BKE_undosys_recovery_required(data.stack, failed_main));
  BKE_undosys_stack_init_from_main(data.stack, failed_main);
  EXPECT_TRUE(data.stack->recovery_required);
  EXPECT_TRUE(BKE_undosys_recovery_required(data.stack, failed_main));

  BKE_undosys_stack_destroy(data.stack);
  data.stack = BKE_undosys_stack_create();
  EXPECT_FALSE(data.stack->recovery_required);
  EXPECT_TRUE(BKE_undosys_recovery_required(data.stack, failed_main));
  BKE_undosys_stack_init_from_main(data.stack, failed_main);
  EXPECT_TRUE(data.stack->recovery_required);

  Main *fresh_main = data.create_main();
  data.activate_main(fresh_main);
  BKE_undosys_stack_clear(data.stack);
  BKE_undosys_stack_init_from_main(data.stack, fresh_main);
  EXPECT_FALSE(data.stack->recovery_required);
  EXPECT_FALSE(BKE_undosys_recovery_required(data.stack, fresh_main));
  EXPECT_TRUE(failed_main->is_undo_recovery_required);
  data.append(data.memfile_type, "New document edit");
  EXPECT_TRUE(BKE_undosys_step_undo(data.stack, data.C));
  EXPECT_FALSE(BKE_undosys_recovery_required(data.stack, fresh_main));
}

/** Own only test metadata and extra stacks; Main still owns each actual WindowManager ID. */
class CheckpointTestOwners {
 private:
  UndoSystemTestData &data_;
  Vector<wmWindowManager *> managers_;
  Vector<UndoStack *> extra_stacks_;
  decltype(U.uiflag) saved_uiflag_ = U.uiflag;

 public:
  CheckpointCaptureOwner *capture = nullptr;

  explicit CheckpointTestOwners(UndoSystemTestData &data) : data_(data) {}

  ~CheckpointTestOwners()
  {
    /* Release even after a fatal assertion or deliberately failed document validation. */
    EXPECT_TRUE(BKE_undosys_checkpoint_capture_end(capture));
    for (wmWindowManager *wm : managers_) {
      /* Tests restore temporarily changed runtime bindings before leaving their body. */
      wm->runtime->undo_stack = nullptr;
    }
    for (UndoStack *stack : extra_stacks_) {
      BKE_undosys_stack_destroy(stack);
    }
    U.uiflag = saved_uiflag_;
  }

  wmWindowManager *add_manager(const char *name, UndoStack *stack = nullptr)
  {
    wmWindowManager *wm = BKE_id_new<wmWindowManager>(CTX_data_main(data_.C), name);
    wm->runtime = MEM_new<bke::WindowManagerRuntime>(__func__);
    wm->runtime->undo_stack = stack;
    managers_.append(wm);
    return wm;
  }

  UndoStack *add_stack()
  {
    UndoStack *stack = BKE_undosys_stack_create();
    extra_stacks_.append(stack);
    return stack;
  }

  TestUndoStep &append(UndoStack *stack, const char *name)
  {
    TestUndoStep *step = MEM_new<TestUndoStep>(__func__);
    STRNCPY(step->step.name, name);
    step->step.type = &data_.memfile_type;
    BLI_addtail(&stack->steps, &step->step);
    stack->step_active = stack->step_active_memfile = &step->step;
    return *step;
  }

  void begin()
  {
    ASSERT_EQ(capture, nullptr);
    capture = BKE_undosys_checkpoint_capture_begin(CTX_data_main(data_.C));
    ASSERT_NE(capture, nullptr);
  }

  void expect_release()
  {
    EXPECT_TRUE(BKE_undosys_checkpoint_capture_end(capture));
    EXPECT_EQ(capture, nullptr);
    EXPECT_EQ(BKE_undosys_restore_phase(), UndoRestorePhase::Inactive);
    EXPECT_FALSE(BKE_undosys_restore_protects_main());
  }
};

struct CheckpointHistoryState {
  const UndoStep *first;
  const UndoStep *last;
  const UndoStep *active;
  const UndoStep *memfile;
  const UndoStep *init;
  int count;
  int group_level;
  bool recovery_required;

  explicit CheckpointHistoryState(const UndoStack &stack)
      : first(static_cast<const UndoStep *>(stack.steps.first)),
        last(static_cast<const UndoStep *>(stack.steps.last)),
        active(stack.step_active),
        memfile(stack.step_active_memfile),
        init(stack.step_init),
        count(stack.steps.count()),
        group_level(stack.group_level),
        recovery_required(stack.recovery_required)
  {
  }

  void expect_unchanged(const UndoStack &stack) const
  {
    EXPECT_EQ(stack.steps.first, first);
    EXPECT_EQ(stack.steps.last, last);
    EXPECT_EQ(stack.step_active, active);
    EXPECT_EQ(stack.step_active_memfile, memfile);
    EXPECT_EQ(stack.step_init, init);
    EXPECT_EQ(stack.steps.count(), count);
    EXPECT_EQ(stack.group_level, group_level);
    EXPECT_EQ(stack.recovery_required, recovery_required);
  }
};

TEST_F(UndoSystemCheckedDecodeTest, CheckpointWithoutWindowManagersNeedsNoGlobalUndoOrHistory)
{
  UndoSystemTestData data;
  CheckpointTestOwners owners(data);
  Main *bmain = CTX_data_main(data.C);
  Main *unrelated = data.create_main();
  ASSERT_TRUE(bmain->wm.is_empty());
  U.uiflag &= ~USER_GLOBALUNDO;
  const int uiflag = U.uiflag;
  const CheckpointHistoryState history(*data.stack);
  EXPECT_STREQ(BKE_undosys_restore_refusal_message(),
               "Finish the current Sculpt recovery or native commit before changing history, "
               "document owners, or starting work");
  owners.begin();
  ASSERT_NE(owners.capture, nullptr);
  EXPECT_EQ(BKE_undosys_restore_phase(), UndoRestorePhase::Checkpoint);
  EXPECT_STREQ(BKE_undosys_restore_refusal_message(),
               "Finish the current checkpoint preparation before changing history, document "
               "owners, or starting work");
  EXPECT_TRUE(BKE_undosys_checkpoint_capture_validate(owners.capture));
  EXPECT_TRUE(BKE_undosys_restore_protects_main());
  EXPECT_TRUE(BKE_undosys_restore_protects_main(bmain));
  EXPECT_FALSE(BKE_undosys_restore_protects_main(unrelated));
  EXPECT_FALSE(BKE_undosys_restore_protects_stack(nullptr));
  EXPECT_FALSE(BKE_undosys_restore_protects_stack(data.stack));
  EXPECT_TRUE(bmain->wm.is_empty());
  history.expect_unchanged(*data.stack);
  owners.expect_release();
  EXPECT_STREQ(BKE_undosys_restore_refusal_message(),
               "Finish the current Sculpt recovery or native commit before changing history, "
               "document owners, or starting work");
  EXPECT_EQ(U.uiflag, uiflag);
  EXPECT_TRUE(bmain->wm.is_empty());
  history.expect_unchanged(*data.stack);
}

TEST_F(UndoSystemCheckedDecodeTest, CheckpointStacklessWindowManagersStayStackless)
{
  UndoSystemTestData data;
  CheckpointTestOwners owners(data);
  wmWindowManager *first = owners.add_manager("A stackless");
  wmWindowManager *second = owners.add_manager("B stackless");
  auto *first_runtime = first->runtime;
  auto *second_runtime = second->runtime;
  U.uiflag &= ~USER_GLOBALUNDO;
  owners.begin();
  ASSERT_NE(owners.capture, nullptr);
  EXPECT_TRUE(BKE_undosys_checkpoint_capture_validate(owners.capture));
  EXPECT_FALSE(BKE_undosys_restore_protects_stack(nullptr));
  EXPECT_FALSE(BKE_undosys_restore_protects_stack(data.stack));
  owners.expect_release();
  EXPECT_EQ(first->runtime, first_runtime);
  EXPECT_EQ(second->runtime, second_runtime);
  EXPECT_EQ(first->runtime->undo_stack, nullptr);
  EXPECT_EQ(second->runtime->undo_stack, nullptr);
  EXPECT_TRUE(data.stack->steps.is_empty());
  EXPECT_EQ(data.stack->step_active, nullptr);
  EXPECT_FALSE(U.uiflag & USER_GLOBALUNDO);
}

TEST_F(UndoSystemCheckedDecodeTest, CheckpointProtectsExistingEmptyStackWithoutCreatingSteps)
{
  UndoSystemTestData data;
  CheckpointTestOwners owners(data);
  wmWindowManager *wm = owners.add_manager("Empty history", data.stack);
  const CheckpointHistoryState history(*data.stack);
  ASSERT_TRUE(data.stack->steps.is_empty());
  owners.begin();
  ASSERT_NE(owners.capture, nullptr);
  EXPECT_TRUE(BKE_undosys_restore_protects_stack(data.stack));
  EXPECT_TRUE(BKE_undosys_checkpoint_capture_validate(owners.capture));
  history.expect_unchanged(*data.stack);
  owners.expect_release();
  EXPECT_EQ(wm->runtime->undo_stack, data.stack);
  history.expect_unchanged(*data.stack);
}

TEST_F(UndoSystemCheckedDecodeTest, CheckpointProtectsEveryDistinctAndSharedWindowManagerStack)
{
  UndoSystemTestData data;
  CheckpointTestOwners owners(data);
  Main *bmain = CTX_data_main(data.C);
  wmWindowManager *first = owners.add_manager("A stackless");
  owners.add_manager("B primary", data.stack);
  UndoStack *secondary = owners.add_stack();
  owners.add_manager("C secondary", secondary);
  owners.add_manager("D shared primary", data.stack);
  ASSERT_EQ(bmain->wm.first, first);
  TestUndoStep &original = data.append(data.memfile_type, "Original");
  TestUndoStep &redo = data.append(data.memfile_type, "Redo");
  data.stack->step_active = data.stack->step_active_memfile = &original.step;
  TestUndoStep &secondary_original = owners.append(secondary, "Secondary original");
  TestUndoStep &secondary_redo = owners.append(secondary, "Secondary redo");
  secondary->step_active = secondary->step_active_memfile = &secondary_original.step;
  UndoStack *unrelated = owners.add_stack();
  owners.append(unrelated, "Unrelated");
  const CheckpointHistoryState primary_history(*data.stack);
  const CheckpointHistoryState secondary_history(*secondary);
  owners.begin();
  ASSERT_NE(owners.capture, nullptr);
  EXPECT_TRUE(BKE_undosys_restore_protects_stack(data.stack));
  EXPECT_TRUE(BKE_undosys_restore_protects_stack(secondary));
  EXPECT_FALSE(BKE_undosys_restore_protects_stack(unrelated));
  EXPECT_FALSE(BKE_undosys_restore_protects_stack(nullptr));
  for (UndoStack *stack : {data.stack, secondary}) {
    BKE_undosys_stack_clear(stack);
    BKE_undosys_stack_clear_active(stack);
    BKE_undosys_stack_destroy(stack);
    BKE_undosys_stack_init_from_main(stack, bmain);
    BKE_undosys_stack_init_from_context(stack, data.C);
    BKE_undosys_stack_limit_steps_and_memory(stack, 0, 1);
    BKE_undosys_stack_group_begin(stack);
    BKE_undosys_stack_group_end(stack);
    EXPECT_FALSE(BKE_undosys_step_redo(stack, data.C));
    EXPECT_FALSE(BKE_undosys_step_load_data_ex(stack, data.C, stack->step_active, nullptr, false));
    EXPECT_EQ(BKE_undosys_step_push_with_type(stack, data.C, "Blocked", &data.memfile_type),
              UNDO_PUSH_RET_FAILURE);
    EXPECT_EQ(BKE_undosys_step_push_init_with_type(stack, data.C, "Blocked", &data.memfile_type),
              nullptr);
  }
  primary_history.expect_unchanged(*data.stack);
  secondary_history.expect_unchanged(*secondary);
  EXPECT_EQ(original.step.next, &redo.step);
  EXPECT_EQ(secondary_original.step.next, &secondary_redo.step);
  EXPECT_EQ(redo.decode_calls, 0);
  EXPECT_EQ(secondary_redo.decode_calls, 0);
  BKE_undosys_stack_clear(unrelated);
  EXPECT_TRUE(unrelated->steps.is_empty());
  EXPECT_TRUE(BKE_undosys_checkpoint_capture_validate(owners.capture));
  owners.expect_release();
  EXPECT_TRUE(BKE_undosys_step_redo(data.stack, data.C));
  EXPECT_TRUE(BKE_undosys_step_redo(secondary, data.C));
  EXPECT_EQ(redo.decode_calls, 1);
  EXPECT_EQ(secondary_redo.decode_calls, 1);
}

TEST_F(UndoSystemCheckedDecodeTest, CheckpointRejectsNullWrongMainAndMalformedRuntime)
{
  UndoSystemTestData data;
  CheckpointTestOwners owners(data);
  Main *bmain = CTX_data_main(data.C);
  Main *other = data.create_main();
  EXPECT_EQ(BKE_undosys_checkpoint_capture_begin(nullptr), nullptr);
  EXPECT_EQ(BKE_undosys_checkpoint_capture_begin(other), nullptr);
  wmWindowManager *wm = owners.add_manager("Malformed");
  auto *runtime = wm->runtime;
  wm->runtime = nullptr;
  EXPECT_EQ(BKE_undosys_checkpoint_capture_begin(bmain), nullptr);
  EXPECT_EQ(BKE_undosys_restore_phase(), UndoRestorePhase::Inactive);
  EXPECT_FALSE(BKE_undosys_restore_protects_main());
  wm->runtime = runtime;
  owners.begin();
  EXPECT_TRUE(BKE_undosys_checkpoint_capture_validate(owners.capture));
}

TEST_F(UndoSystemCheckedDecodeTest, CheckpointReleasesAfterCurrentMainChanges)
{
  UndoSystemTestData data;
  CheckpointTestOwners owners(data);
  Main *original = CTX_data_main(data.C);
  owners.add_manager("Original owner", data.stack);
  Main *replacement = data.create_main();
  owners.begin();
  ASSERT_NE(owners.capture, nullptr);
  data.activate_main(replacement);
  EXPECT_FALSE(BKE_undosys_checkpoint_capture_validate(owners.capture));
  EXPECT_TRUE(BKE_undosys_restore_protects_main(original));
  EXPECT_FALSE(BKE_undosys_restore_protects_main(replacement));
  owners.expect_release();
  owners.begin();
  EXPECT_TRUE(BKE_undosys_checkpoint_capture_validate(owners.capture));
  EXPECT_TRUE(BKE_undosys_restore_protects_main(replacement));
  EXPECT_FALSE(BKE_undosys_restore_protects_stack(data.stack));
}

TEST_F(UndoSystemCheckedDecodeTest, CheckpointDetectsAddedWindowManagerAndReleases)
{
  UndoSystemTestData data;
  CheckpointTestOwners owners(data);
  owners.add_manager("A original", data.stack);
  owners.begin();
  ASSERT_NE(owners.capture, nullptr);
  wmWindowManager *added = owners.add_manager("B added");
  EXPECT_FALSE(BKE_undosys_checkpoint_capture_validate(owners.capture));
  owners.expect_release();
  EXPECT_EQ(added->runtime->undo_stack, nullptr);
  owners.begin();
  EXPECT_TRUE(BKE_undosys_checkpoint_capture_validate(owners.capture));
}

TEST_F(UndoSystemCheckedDecodeTest, CheckpointDetectsRemovedAndReplacedWindowManagerMembership)
{
  UndoSystemTestData data;
  CheckpointTestOwners owners(data);
  Main *bmain = CTX_data_main(data.C);
  wmWindowManager *original = owners.add_manager("A original", data.stack);
  wmWindowManager *replacement = owners.add_manager("B replacement", data.stack);
  BLI_remlink(&bmain->wm, replacement);
  owners.begin();
  if (!owners.capture) {
    /* Keep the deliberately detached ID owned by Main even when begin's assertion fails. */
    BLI_addtail(&bmain->wm, replacement);
    return;
  }
  BLI_remlink(&bmain->wm, original);
  EXPECT_FALSE(BKE_undosys_checkpoint_capture_validate(owners.capture));
  /* Matching cardinality and stack still cannot replace the captured WM address. */
  BLI_addtail(&bmain->wm, replacement);
  EXPECT_FALSE(BKE_undosys_checkpoint_capture_validate(owners.capture));
  owners.expect_release();
  BLI_addtail(&bmain->wm, original);
  owners.begin();
  EXPECT_TRUE(BKE_undosys_checkpoint_capture_validate(owners.capture));
}

TEST_F(UndoSystemCheckedDecodeTest, CheckpointDetectsUidRuntimeAndStackBindingChanges)
{
  UndoSystemTestData data;
  CheckpointTestOwners owners(data);
  wmWindowManager *wm = owners.add_manager("Binding", data.stack);
  auto *original_runtime = wm->runtime;
  const uint original_uid = wm->id.session_uid;
  UndoStack *other_stack = owners.add_stack();
  bke::WindowManagerRuntime replacement_runtime;
  for (int mutation = 0; mutation < 4; mutation++) {
    SCOPED_TRACE(mutation);
    owners.begin();
    ASSERT_NE(owners.capture, nullptr);
    switch (mutation) {
      case 0:
        wm->id.session_uid = original_uid ^ 1u;
        break;
      case 1:
        wm->runtime = nullptr;
        break;
      case 2:
        replacement_runtime.undo_stack = data.stack;
        wm->runtime = &replacement_runtime;
        break;
      case 3:
        original_runtime->undo_stack = other_stack;
        break;
    }
    EXPECT_FALSE(BKE_undosys_checkpoint_capture_validate(owners.capture));
    EXPECT_TRUE(BKE_undosys_restore_protects_stack(data.stack));
    EXPECT_FALSE(BKE_undosys_restore_protects_stack(other_stack));
    owners.expect_release();
    wm->id.session_uid = original_uid;
    wm->runtime = original_runtime;
    original_runtime->undo_stack = data.stack;
    replacement_runtime.undo_stack = nullptr;
  }
  owners.begin();
  EXPECT_TRUE(BKE_undosys_checkpoint_capture_validate(owners.capture));
}

TEST_F(UndoSystemCheckedDecodeTest, CheckpointEndRejectsForeignAndAliasesBeforeReacquisition)
{
  UndoSystemTestData data;
  CheckpointTestOwners owners(data);
  owners.add_manager("Owner", data.stack);
  CheckpointCaptureOwner *empty = nullptr;
  EXPECT_TRUE(BKE_undosys_checkpoint_capture_end(empty));
  EXPECT_FALSE(BKE_undosys_checkpoint_capture_validate(empty));
  owners.begin();
  ASSERT_NE(owners.capture, nullptr);
  /* Deliberately not an owner object: refusal must compare identity before dereferencing. */
  CheckpointCaptureOwner *foreign = reinterpret_cast<CheckpointCaptureOwner *>(data.stack);
  CheckpointCaptureOwner *foreign_identity = foreign;
  EXPECT_FALSE(BKE_undosys_checkpoint_capture_validate(foreign));
  EXPECT_FALSE(BKE_undosys_checkpoint_capture_end(foreign));
  EXPECT_EQ(foreign, foreign_identity);
  EXPECT_TRUE(BKE_undosys_checkpoint_capture_validate(owners.capture));
  CheckpointCaptureOwner *consumed_alias = owners.capture;
  owners.expect_release();
  /* These defensive checks cover only the no-current-owner interval before reacquisition.
   * They do not prove rejection after a later allocation reuses the address. */
  EXPECT_FALSE(BKE_undosys_checkpoint_capture_validate(consumed_alias));
  EXPECT_FALSE(BKE_undosys_checkpoint_capture_end(consumed_alias));
  EXPECT_NE(consumed_alias, nullptr);
  EXPECT_TRUE(BKE_undosys_checkpoint_capture_end(owners.capture));
  owners.begin();
  EXPECT_TRUE(BKE_undosys_checkpoint_capture_validate(owners.capture));
}

TEST_F(UndoSystemCheckedDecodeTest, CheckpointWorkerCannotBeginValidateOrConsumeOwner)
{
  UndoSystemTestData data;
  CheckpointTestOwners owners(data);
  Main *bmain = CTX_data_main(data.C);
  owners.add_manager("Owner", data.stack);
  owners.begin();
  ASSERT_NE(owners.capture, nullptr);
  CheckpointCaptureOwner *const owner_identity = owners.capture;
  bool active_observed = false;
  bool operations_refused = false;
  std::thread worker([&, owner_identity]() {
    /* This local handle is never concurrently read or written by the main thread. */
    CheckpointCaptureOwner *local = owner_identity;
    CheckpointCaptureOwner *empty = nullptr;
    active_observed = BKE_undosys_restore_phase() == UndoRestorePhase::Checkpoint &&
                      BKE_undosys_restore_protects_main(bmain) &&
                      BKE_undosys_restore_protects_stack(data.stack);
    operations_refused = !BKE_undosys_checkpoint_capture_begin(bmain) &&
                         !BKE_undosys_checkpoint_capture_validate(local) &&
                         !BKE_undosys_checkpoint_capture_end(local) && local == owner_identity &&
                         BKE_undosys_checkpoint_capture_end(empty);
  });
  worker.join();
  EXPECT_TRUE(active_observed);
  EXPECT_TRUE(operations_refused);
  EXPECT_EQ(owners.capture, owner_identity);
  EXPECT_TRUE(BKE_undosys_checkpoint_capture_validate(owners.capture));
  owners.expect_release();
  CheckpointCaptureOwner *worker_acquired = nullptr;
  std::thread inactive_worker(
      [&]() { worker_acquired = BKE_undosys_checkpoint_capture_begin(bmain); });
  inactive_worker.join();
  EXPECT_EQ(worker_acquired, nullptr);
  /* If admission regresses, let the main thread retire the unexpected owner after joining. */
  EXPECT_TRUE(BKE_undosys_checkpoint_capture_end(worker_acquired));
  EXPECT_EQ(BKE_undosys_restore_phase(), UndoRestorePhase::Inactive);
}

TEST_F(UndoSystemCheckedDecodeTest, CheckpointAndTypedCaptureExcludeEachOther)
{
  UndoSystemTestData data;
  CheckpointTestOwners owners(data);
  Main *bmain = CTX_data_main(data.C);
  owners.add_manager("Owner", data.stack);
  data.append(data.memfile_type, "Original");
  UndoCaptureScope *commit = BKE_undosys_capture_begin(bmain, data.stack);
  ASSERT_NE(commit, nullptr);
  EXPECT_EQ(BKE_undosys_checkpoint_capture_begin(bmain), nullptr);
  EXPECT_EQ(BKE_undosys_restore_phase(), UndoRestorePhase::Preparing);
  EXPECT_STREQ(BKE_undosys_restore_refusal_message(),
               "Finish the current Sculpt recovery or native commit before changing history, "
               "document owners, or starting work");
  EXPECT_TRUE(BKE_undosys_capture_validate(commit));
  BKE_undosys_capture_end(commit);
  owners.begin();
  ASSERT_NE(owners.capture, nullptr);
  EXPECT_EQ(BKE_undosys_checkpoint_capture_begin(bmain), nullptr);
  EXPECT_EQ(BKE_undosys_capture_begin(bmain, data.stack), nullptr);
  EXPECT_FALSE(BKE_undosys_restore_activate(data.stack, bmain));
  EXPECT_FALSE(BKE_undosys_step_undo(data.stack, data.C));
  EXPECT_TRUE(BKE_undosys_checkpoint_capture_validate(owners.capture));
  owners.expect_release();
  commit = BKE_undosys_capture_begin(bmain, data.stack);
  ASSERT_NE(commit, nullptr);
  EXPECT_TRUE(BKE_undosys_capture_validate(commit));
  BKE_undosys_capture_end(commit);
}

TEST_F(UndoSystemCheckedDecodeTest, CheckpointRefusesBothUnmarkedAndProtectedRestoreTraversal)
{
  for (const bool activate : {false, true}) {
    SCOPED_TRACE(activate);
    UndoSystemTestData data;
    CheckpointTestOwners owners(data);
    owners.add_manager("Owner", data.stack);
    TestUndoStep &original = data.append(data.memfile_type, "Original");
    data.append(data.memfile_type, "Current");
    original.test_stack = activate ? data.stack : nullptr;
    original.during_decode = [](bContext *, Main *bmain, TestUndoStep &step) {
      if (step.test_stack) {
        ASSERT_TRUE(BKE_undosys_restore_activate(step.test_stack, bmain));
      }
      const UndoRestorePhase expected = step.test_stack ? UndoRestorePhase::Preparing :
                                                          UndoRestorePhase::Inactive;
      EXPECT_EQ(BKE_undosys_restore_phase(), expected);
      EXPECT_EQ(BKE_undosys_checkpoint_capture_begin(bmain), nullptr);
      EXPECT_EQ(BKE_undosys_restore_phase(), expected);
    };
    EXPECT_TRUE(BKE_undosys_step_undo(data.stack, data.C));
    EXPECT_EQ(original.decode_calls, 1);
    EXPECT_EQ(BKE_undosys_restore_phase(), UndoRestorePhase::Inactive);
    owners.begin();
    EXPECT_TRUE(BKE_undosys_checkpoint_capture_validate(owners.capture));
  }
}

static int checkpoint_editor_calls = 0;
static void checkpoint_test_editor_update(const DEGEditorUpdateContext *update, bool updated)
{
  checkpoint_editor_calls++;
  EXPECT_TRUE(updated);
  EXPECT_EQ(BKE_undosys_restore_phase(), UndoRestorePhase::Checkpoint);
  EXPECT_TRUE(BKE_undosys_restore_protects_main(update->bmain));
}

TEST_F(UndoSystemCheckedDecodeTest,
       CheckpointKeepsImmediateEditorDeliveryAndReleaseLeavesStateAlone)
{
  UndoSystemTestData data;
  CheckpointTestOwners owners(data);
  Main *bmain = CTX_data_main(data.C);
  owners.add_manager("Owner", data.stack);
  data.append(data.memfile_type, "Original");
  Scene *scene = BKE_id_new<Scene>(bmain, "Scene");
  Depsgraph *graph = BKE_scene_ensure_depsgraph(
      bmain, scene, static_cast<ViewLayer *>(scene->view_layers.first));
  ASSERT_TRUE(DEG_editors_update_is_enabled(graph));
  DEG_graph_build_from_view_layer(graph);
  DEG_make_active(graph);
  const CheckpointHistoryState history(*data.stack);
  DEG_id_tag_update_ex(bmain, &scene->id, ID_RECALC_ANIMATION | ID_RECALC_PARAMETERS);
  const uint recalc = scene->id.recalc;
  ASSERT_NE(recalc, 0u);
  bmain->is_memfile_undo_flush_needed = true;
  checkpoint_editor_calls = 0;
  owners.begin();
  ASSERT_NE(owners.capture, nullptr);
  /* A real canonical editor graph must retain ordinary immediate delivery in this phase. */
  EXPECT_FALSE(BKE_undosys_restore_defer_editor_update(bmain, graph, true, true));
  EXPECT_FALSE(BKE_undosys_restore_defer_editor_update(bmain, graph, false, true));
  DEG_editors_set_update_cb(nullptr, checkpoint_test_editor_update);
  DEG_editors_update(graph, true);
  EXPECT_EQ(checkpoint_editor_calls, 1);
  owners.expect_release();
  EXPECT_EQ(checkpoint_editor_calls, 1);
  DEG_editors_set_update_cb(nullptr, nullptr);
  EXPECT_EQ(scene->id.recalc, recalc);
  EXPECT_TRUE(bmain->is_memfile_undo_flush_needed);
  EXPECT_FALSE(bmain->is_undo_recovery_required);
  history.expect_unchanged(*data.stack);
  /* Positive control: this built active graph really can consume the observed recalc flags. */
  DEG_ids_clear_recalc(graph, false);
  EXPECT_EQ(scene->id.recalc & ID_RECALC_ALL, 0u);
}

TEST_F(UndoSystemCheckedDecodeTest, CheckpointWorkerQueriesRemainBoundedToCapturedIdentities)
{
  UndoSystemTestData data;
  CheckpointTestOwners owners(data);
  Main *bmain = CTX_data_main(data.C);
  Main *unrelated_main = data.create_main();
  owners.add_manager("A stackless");
  owners.add_manager("B primary", data.stack);
  UndoStack *secondary = owners.add_stack();
  owners.add_manager("C secondary", secondary);
  UndoStack *unrelated_stack = owners.add_stack();
  std::atomic<bool> run{true};
  std::atomic<int> stage{0};
  std::atomic<int> acknowledged{0};
  std::atomic<int> queries{0};
  std::atomic<bool> bad_identity{false};
  bool stable_active = false;
  bool stable_inactive = false;
  std::thread worker([&]() {
    while (run.load(std::memory_order_acquire)) {
      const UndoRestorePhase phase = BKE_undosys_restore_phase();
      if ((phase != UndoRestorePhase::Inactive && phase != UndoRestorePhase::Checkpoint) ||
          BKE_undosys_restore_protects_main(unrelated_main) ||
          BKE_undosys_restore_protects_stack(unrelated_stack) ||
          BKE_undosys_restore_protects_stack(nullptr))
      {
        bad_identity.store(true, std::memory_order_relaxed);
      }
      /* These separate public calls may straddle release. Do not require matching answers. */
      BKE_undosys_restore_protects_main(bmain);
      BKE_undosys_restore_protects_stack(data.stack);
      BKE_undosys_restore_protects_stack(secondary);
      queries.fetch_add(1, std::memory_order_relaxed);
      const int requested = stage.load(std::memory_order_acquire);
      if (requested == 1 && acknowledged.load(std::memory_order_relaxed) == 0) {
        stable_active = BKE_undosys_restore_phase() == UndoRestorePhase::Checkpoint &&
                        BKE_undosys_restore_protects_main(bmain) &&
                        BKE_undosys_restore_protects_stack(data.stack) &&
                        BKE_undosys_restore_protects_stack(secondary);
        acknowledged.store(1, std::memory_order_release);
      }
      if (requested == 2) {
        stable_inactive = BKE_undosys_restore_phase() == UndoRestorePhase::Inactive &&
                          !BKE_undosys_restore_protects_main(bmain) &&
                          !BKE_undosys_restore_protects_stack(data.stack) &&
                          !BKE_undosys_restore_protects_stack(secondary);
        acknowledged.store(2, std::memory_order_release);
      }
    }
  });
  /* No fatal assertions while the thread is joinable. Test-owned atomics delimit stable checks. */
  owners.capture = BKE_undosys_checkpoint_capture_begin(bmain);
  EXPECT_NE(owners.capture, nullptr);
  stage.store(1, std::memory_order_release);
  while (acknowledged.load(std::memory_order_acquire) != 1) {
    std::this_thread::yield();
  }
  stage.store(0, std::memory_order_release);
  owners.expect_release();
  for (int cycle = 0; cycle < 256; cycle++) {
    owners.capture = BKE_undosys_checkpoint_capture_begin(bmain);
    EXPECT_NE(owners.capture, nullptr);
    EXPECT_TRUE(BKE_undosys_checkpoint_capture_validate(owners.capture));
    EXPECT_TRUE(BKE_undosys_checkpoint_capture_end(owners.capture));
    std::this_thread::yield();
  }
  stage.store(2, std::memory_order_release);
  while (acknowledged.load(std::memory_order_acquire) != 2) {
    std::this_thread::yield();
  }
  run.store(false, std::memory_order_release);
  worker.join();
  EXPECT_TRUE(stable_active);
  EXPECT_TRUE(stable_inactive);
  EXPECT_FALSE(bad_identity.load());
  EXPECT_GT(queries.load(), 0);
  EXPECT_EQ(owners.capture, nullptr);
  EXPECT_EQ(BKE_undosys_restore_phase(), UndoRestorePhase::Inactive);
  /* This does not pause a query with a private retained record and is not a TSAN proof. All
   * Main, WM, runtime and stack fixtures stay alive until the worker has joined above. */
}


/** A real detached target Main/WM; no native file read or dangling fixture ownership. */
class CheckpointRestoreTestTarget {
 public:
  Main *main;
  wmWindowManager *wm;
  explicit CheckpointRestoreTestTarget(UndoSystemTestData &data)
  {
    main = data.create_main();
    wm = BKE_id_new<wmWindowManager>(main, "Cold target");
    wm->runtime = MEM_new<bke::WindowManagerRuntime>(__func__);
    wm->runtime->undo_stack = data.stack;
  }
  ~CheckpointRestoreTestTarget() { wm->runtime->undo_stack = nullptr; }
};

TEST_F(UndoSystemCheckedDecodeTest, CheckpointRestoreRebaseRequiresDeclaredLiveTarget)
{
  UndoSystemTestData data;
  CheckpointTestOwners owners(data);
  owners.add_manager("Source", data.stack);
  Main *source = CTX_data_main(data.C);
  CheckpointRestoreTestTarget target(data);
  owners.capture = BKE_undosys_checkpoint_restore_begin(source, target.main);
  ASSERT_NE(owners.capture, nullptr);
  EXPECT_TRUE(BKE_undosys_checkpoint_capture_validate(owners.capture));
  EXPECT_TRUE(BKE_undosys_restore_protects_main(source));
  EXPECT_TRUE(BKE_undosys_restore_protects_main(target.main));
  EXPECT_FALSE(BKE_undosys_checkpoint_restore_rebase(owners.capture, target.main));
  ASSERT_TRUE(BKE_undosys_checkpoint_restore_retire_history(owners.capture));
  EXPECT_FALSE(BKE_undosys_checkpoint_restore_retire_history(owners.capture));
  Main *foreign = data.create_main();
  data.activate_main(foreign);
  EXPECT_FALSE(BKE_undosys_checkpoint_restore_rebase(owners.capture, foreign));
  EXPECT_EQ(BKE_undosys_restore_phase(), UndoRestorePhase::Checkpoint);
  EXPECT_FALSE(BKE_undosys_checkpoint_capture_validate(owners.capture));
  data.activate_main(target.main);
  EXPECT_TRUE(BKE_undosys_checkpoint_restore_rebase(owners.capture, target.main));
  EXPECT_TRUE(BKE_undosys_checkpoint_capture_validate(owners.capture));
  EXPECT_EQ(BKE_undosys_restore_phase(), UndoRestorePhase::Checkpoint);
  EXPECT_FALSE(BKE_undosys_checkpoint_restore_rebase(owners.capture, target.main));
  EXPECT_FALSE(BKE_undosys_checkpoint_restore_complete(owners.capture));
  owners.expect_release();
}

TEST_F(UndoSystemCheckedDecodeTest, CheckpointRestoreRefusesPendingHistoryAndNormalCaptureRebase)
{
  UndoSystemTestData data;
  CheckpointTestOwners owners(data);
  owners.add_manager("Source", data.stack);
  Main *source = CTX_data_main(data.C);
  CheckpointRestoreTestTarget target(data);
  data.stack->group_level = 1;
  EXPECT_EQ(BKE_undosys_checkpoint_restore_begin(source, target.main), nullptr);
  EXPECT_EQ(BKE_undosys_restore_phase(), UndoRestorePhase::Inactive);
  data.stack->group_level = 0;
  owners.begin();
  EXPECT_FALSE(BKE_undosys_checkpoint_restore_rebase(owners.capture, source));
  EXPECT_EQ(BKE_undosys_checkpoint_restore_begin(source, target.main), nullptr);
  EXPECT_FALSE(BKE_undosys_checkpoint_restore_complete(owners.capture));
  owners.expect_release();
}

TEST_F(UndoSystemCheckedDecodeTest, CheckpointRestorePrivateInitialPublicationKeepsGenericHistoryClosed)
{
  UndoSystemTestData data;
  CheckpointTestOwners owners(data);
  owners.add_manager("Source", data.stack);
  Main *source = CTX_data_main(data.C);
  TestUndoStep &old = owners.append(data.stack, "Old document");
  old.main_to_install = source;
  data.memfile_type.step_free = [](UndoStep *us) {
    const auto &step = *reinterpret_cast<TestUndoStep *>(us);
    if (step.main_to_install) {
      EXPECT_EQ(G_MAIN, step.main_to_install);
      EXPECT_TRUE(BKE_undosys_restore_protects_main());
    }
  };
  CheckpointRestoreTestTarget target(data);
  owners.capture = BKE_undosys_checkpoint_restore_begin(source, target.main);
  ASSERT_NE(owners.capture, nullptr);
  BKE_undosys_stack_clear(data.stack);
  EXPECT_EQ(data.stack->steps.count(), 1);
  ASSERT_TRUE(BKE_undosys_checkpoint_restore_retire_history(owners.capture));
  EXPECT_EQ(data.stack->steps.count(), 0);
  data.activate_main(target.main);
  ASSERT_TRUE(BKE_undosys_checkpoint_restore_rebase(owners.capture, target.main));
  UndoStack *foreign_stack = owners.add_stack();
  EXPECT_TRUE(BKE_undosys_restore_protects_stack(foreign_stack));
  BKE_undosys_stack_clear(data.stack);
  EXPECT_EQ(data.stack->steps.count(), 0);
  EXPECT_EQ(BKE_undosys_step_push_with_type(data.stack, data.C, "Reentry", &data.memfile_type),
            UNDO_PUSH_RET_FAILURE);
  auto *initial = MEM_new<TestUndoStep>(__func__);
  initial->step.type = &data.memfile_type;
  STRNCPY(initial->step.name, "Recovered Original");
  EXPECT_FALSE(BKE_undosys_checkpoint_restore_publish(owners.capture, foreign_stack, &initial->step));
  ASSERT_TRUE(BKE_undosys_checkpoint_restore_publish(owners.capture, data.stack, &initial->step));
  EXPECT_EQ(data.stack->steps.count(), 1);
  EXPECT_EQ(data.stack->step_active, &initial->step);
  EXPECT_EQ(data.stack->step_active_memfile, &initial->step);
  EXPECT_TRUE(BKE_undosys_checkpoint_restore_complete(owners.capture));
  EXPECT_FALSE(BKE_undosys_checkpoint_restore_publish(owners.capture, data.stack, &initial->step));
  BKE_undosys_stack_clear(data.stack);
  EXPECT_EQ(data.stack->steps.count(), 1);
  owners.expect_release();
  EXPECT_FALSE(BKE_undosys_restore_protects_stack(foreign_stack));
}

TEST_F(UndoSystemCheckedDecodeTest, CheckpointRestoreRebasedOwnerRejectsChangedWMBeforePublication)
{
  UndoSystemTestData data;
  CheckpointTestOwners owners(data);
  owners.add_manager("Source", data.stack);
  CheckpointRestoreTestTarget target(data);
  owners.capture = BKE_undosys_checkpoint_restore_begin(CTX_data_main(data.C), target.main);
  ASSERT_NE(owners.capture, nullptr);
  ASSERT_TRUE(BKE_undosys_checkpoint_restore_retire_history(owners.capture));
  data.activate_main(target.main);
  ASSERT_TRUE(BKE_undosys_checkpoint_restore_rebase(owners.capture, target.main));
  UndoStack *saved = target.wm->runtime->undo_stack;
  target.wm->runtime->undo_stack = nullptr;
  EXPECT_FALSE(BKE_undosys_checkpoint_capture_validate(owners.capture));
  EXPECT_FALSE(BKE_undosys_checkpoint_restore_complete(owners.capture));
  auto *initial = MEM_new<TestUndoStep>(__func__);
  initial->step.type = &data.memfile_type;
  EXPECT_FALSE(BKE_undosys_checkpoint_restore_publish(owners.capture, saved, &initial->step));
  MEM_delete(initial);
  target.wm->runtime->undo_stack = saved;
  EXPECT_TRUE(BKE_undosys_checkpoint_capture_validate(owners.capture));
  owners.expect_release();
}

struct CheckpointCallbackTestWitness {
  int acquired = 0, released = 0, live = 0, peak = 0;
  int value = 0, observed_value = 0;
  bool available = true;
  bool after_available = true;
  bool saw_null_main = false;
  static void *before(void *data, Main *)
  {
    auto &state = *static_cast<CheckpointCallbackTestWitness *>(data);
    state.acquired++;
    if (!state.available) { return nullptr; }
    state.live++;
    state.peak = std::max(state.peak, state.live);
    return new int(state.value);
  }
  static bool after(void *data, void *witness, Main *main)
  {
    auto &state = *static_cast<CheckpointCallbackTestWitness *>(data);
    const bool equal = *static_cast<int *>(witness) == state.value;
    delete static_cast<int *>(witness);
    state.released++;
    state.live--;
    state.saw_null_main |= main == nullptr;
    if (main && !state.after_available) {
      BKE_checkpoint_callback_observation_unavailable(main);
      return false;
    }
    if (main) { state.observed_value = state.value; }
    return main != nullptr && equal;
  }
};

TEST_F(UndoSystemCheckedDecodeTest, CheckpointCallbackNestedWitnessesAreDistinctAndOrdinaryCallsAreUntouched)
{
  UndoSystemTestData data;
  CheckpointTestOwners owners(data);
  owners.add_manager("Source", data.stack);
  Main *main = CTX_data_main(data.C);
  CheckpointCallbackTestWitness state;
  EXPECT_TRUE(BKE_checkpoint_callback_observation_safe(main));
  { ScopedCheckpointCallback ordinary(main); }
  EXPECT_EQ(state.acquired, 0);
  owners.begin();
  ScopedCheckpointCallbackObserver observer(main, &state,
      CheckpointCallbackTestWitness::before, CheckpointCallbackTestWitness::after);
  {
    ScopedCheckpointCallback outer(main);
    { ScopedCheckpointCallback inner(main); }
    EXPECT_EQ(state.live, 1);
  }
  EXPECT_TRUE(observer.valid());
  EXPECT_TRUE(BKE_checkpoint_callback_observation_safe(main));
  EXPECT_EQ(state.acquired, 2);
  EXPECT_EQ(state.released, 2);
  EXPECT_EQ(state.peak, 2);
  EXPECT_EQ(state.live, 0);
  owners.expect_release();
}

TEST_F(UndoSystemCheckedDecodeTest, CheckpointCallbackChangedMainDisposesWitnessWithoutCachedMain)
{
  UndoSystemTestData data;
  CheckpointTestOwners owners(data);
  owners.add_manager("Source", data.stack);
  Main *main = CTX_data_main(data.C);
  Main *foreign = data.create_main();
  CheckpointCallbackTestWitness state;
  owners.begin();
  ScopedCheckpointCallbackObserver observer(main, &state,
      CheckpointCallbackTestWitness::before, CheckpointCallbackTestWitness::after);
  {
    ScopedCheckpointCallback callback(main);
    data.activate_main(foreign);
  }
  EXPECT_FALSE(observer.valid());
  EXPECT_FALSE(BKE_checkpoint_callback_observation_safe(main));
  EXPECT_TRUE(BKE_checkpoint_callback_observation_safe(foreign));
  { ScopedCheckpointCallback unrelated(foreign); }
  EXPECT_TRUE(BKE_checkpoint_callback_observation_safe(foreign));
  EXPECT_TRUE(state.saw_null_main);
  EXPECT_EQ(state.released, 1);
  EXPECT_EQ(state.live, 0);
  data.activate_main(main);
  { ScopedCheckpointCallback refused(main); }
  EXPECT_EQ(state.acquired, 1);
  EXPECT_FALSE(BKE_checkpoint_callback_observation_safe(main));
  owners.expect_release();
}

TEST_F(UndoSystemCheckedDecodeTest, CheckpointWorkerCallbackNeverAcquiresMainThreadWitness)
{
  UndoSystemTestData data;
  CheckpointTestOwners owners(data);
  owners.add_manager("Source", data.stack);
  Main *main = CTX_data_main(data.C);
  CheckpointCallbackTestWitness state;
  owners.begin();
  ScopedCheckpointCallbackObserver observer(main, &state,
      CheckpointCallbackTestWitness::before, CheckpointCallbackTestWitness::after);
  int bodies = 0;
  std::thread worker([&]() {
    ScopedCheckpointCallback callback(main);
    bodies++;
  });
  worker.join();
  EXPECT_FALSE(observer.valid());
  EXPECT_FALSE(BKE_checkpoint_callback_observation_safe(main));
  {
    ScopedCheckpointCallback later(main);
    bodies++;
  }
  EXPECT_EQ(bodies, 2);
  EXPECT_FALSE(observer.valid());
  EXPECT_FALSE(BKE_checkpoint_callback_observation_safe(main));
  EXPECT_EQ(state.acquired, 0);
  EXPECT_EQ(state.released, 0);
  owners.expect_release();
}

TEST_F(UndoSystemCheckedDecodeTest, CheckpointWitnessedConflictContinuesLaterHandlerObservation)
{
  UndoSystemTestData data;
  CheckpointTestOwners owners(data);
  owners.add_manager("Source", data.stack);
  Main *main = CTX_data_main(data.C);
  CheckpointCallbackTestWitness state;
  owners.begin();
  ScopedCheckpointCallbackObserver observer(main, &state,
      CheckpointCallbackTestWitness::before, CheckpointCallbackTestWitness::after);
  {
    ScopedCheckpointCallback first(main);
    state.value = 10;
  }
  EXPECT_FALSE(observer.valid());
  EXPECT_TRUE(BKE_checkpoint_callback_observation_safe(main));
  EXPECT_EQ(state.observed_value, 10);
  {
    ScopedCheckpointCallback later(main);
    EXPECT_EQ(state.live, 1);
    state.value = 20;
  }
  /* Latest legitimate handler edits are observed rather than replaying the earlier value. */
  EXPECT_EQ(state.observed_value, 20);
  EXPECT_EQ(state.acquired, 2);
  EXPECT_EQ(state.released, 2);
  EXPECT_EQ(state.live, 0);
  EXPECT_FALSE(observer.valid());
  EXPECT_TRUE(BKE_checkpoint_callback_observation_safe(main));
  { ScopedCheckpointCallback unchanged(main); }
  EXPECT_EQ(state.acquired, 3);
  EXPECT_EQ(state.released, 3);
  EXPECT_FALSE(observer.valid());
  EXPECT_TRUE(BKE_checkpoint_callback_observation_safe(main));
  owners.expect_release();
}

TEST_F(UndoSystemCheckedDecodeTest, CheckpointUnavailableWitnessPermanentlyStopsObservation)
{
  UndoSystemTestData data;
  CheckpointTestOwners owners(data);
  owners.add_manager("Source", data.stack);
  Main *main = CTX_data_main(data.C);
  CheckpointCallbackTestWitness state;
  owners.begin();
  ScopedCheckpointCallbackObserver observer(main, &state,
      CheckpointCallbackTestWitness::before, CheckpointCallbackTestWitness::after);
  state.available = false;
  { ScopedCheckpointCallback unavailable(main); }
  EXPECT_FALSE(observer.valid());
  EXPECT_FALSE(BKE_checkpoint_callback_observation_safe(main));
  state.available = true;
  { ScopedCheckpointCallback later(main); state.value++; }
  EXPECT_EQ(state.value, 1);
  EXPECT_EQ(state.acquired, 1);
  EXPECT_EQ(state.released, 0);
  EXPECT_FALSE(BKE_checkpoint_callback_observation_safe(main));
  owners.expect_release();
}

TEST_F(UndoSystemCheckedDecodeTest, CheckpointUnavailableAfterWitnessStopsLaterAndDisposesOuterToken)
{
  UndoSystemTestData data;
  CheckpointTestOwners owners(data);
  owners.add_manager("Source", data.stack);
  Main *main = CTX_data_main(data.C);
  CheckpointCallbackTestWitness state;
  BKE_checkpoint_callback_observation_unavailable(main);
  EXPECT_TRUE(BKE_checkpoint_callback_observation_safe(main));
  owners.begin();
  {
    ScopedCheckpointCallbackObserver observer(main, &state,
        CheckpointCallbackTestWitness::before, CheckpointCallbackTestWitness::after);
    {
      ScopedCheckpointCallback outer(main);
      {
        ScopedCheckpointCallback inner(main);
        state.after_available = false;
        state.value = 10;
      }
      EXPECT_FALSE(observer.valid());
      EXPECT_FALSE(BKE_checkpoint_callback_observation_safe(main));
      EXPECT_EQ(state.live, 1);
      /* Restoring admission capacity must not clear the unavailable observation latch. */
      state.after_available = true;
      {
        ScopedCheckpointCallback later(main);
        state.value = 20;
      }
      EXPECT_EQ(state.value, 20);
      EXPECT_EQ(state.acquired, 2);
      EXPECT_EQ(state.released, 1);
    }
    EXPECT_FALSE(observer.valid());
    EXPECT_FALSE(BKE_checkpoint_callback_observation_safe(main));
    EXPECT_TRUE(state.saw_null_main);
    EXPECT_EQ(state.observed_value, 0);
    EXPECT_EQ(state.acquired, 2);
    EXPECT_EQ(state.released, 2);
    EXPECT_EQ(state.live, 0);
  }
  EXPECT_TRUE(BKE_checkpoint_callback_observation_safe(main));
  owners.expect_release();
}

TEST_F(UndoSystemCheckedDecodeTest, CheckpointUnavailableObservationIgnoresNullAndDifferentMain)
{
  UndoSystemTestData data;
  CheckpointTestOwners owners(data);
  owners.add_manager("Source", data.stack);
  Main *main = CTX_data_main(data.C);
  Main *foreign = data.create_main();
  CheckpointCallbackTestWitness state;
  owners.begin();
  ScopedCheckpointCallbackObserver observer(main, &state,
      CheckpointCallbackTestWitness::before, CheckpointCallbackTestWitness::after);
  BKE_checkpoint_callback_observation_unavailable(nullptr);
  BKE_checkpoint_callback_observation_unavailable(foreign);
  EXPECT_TRUE(observer.valid());
  EXPECT_TRUE(BKE_checkpoint_callback_observation_safe(main));
  { ScopedCheckpointCallback callback(main); }
  EXPECT_EQ(state.acquired, 1);
  EXPECT_EQ(state.released, 1);
  EXPECT_TRUE(observer.valid());
  EXPECT_TRUE(BKE_checkpoint_callback_observation_safe(main));
  owners.expect_release();
}

TEST_F(UndoSystemCheckedDecodeTest, CheckpointLostProtectionDisposesOutstandingWitnessAndStaysUnsafe)
{
  UndoSystemTestData data;
  CheckpointTestOwners owners(data);
  owners.add_manager("Source", data.stack);
  Main *main = CTX_data_main(data.C);
  CheckpointCallbackTestWitness state;
  owners.begin();
  ScopedCheckpointCallbackObserver observer(main, &state,
      CheckpointCallbackTestWitness::before, CheckpointCallbackTestWitness::after);
  {
    ScopedCheckpointCallback callback(main);
    owners.expect_release();
  }
  EXPECT_FALSE(observer.valid());
  EXPECT_FALSE(BKE_checkpoint_callback_observation_safe(main));
  EXPECT_TRUE(state.saw_null_main);
  EXPECT_EQ(state.released, 1);
  EXPECT_EQ(state.live, 0);
  owners.begin();
  { ScopedCheckpointCallback later(main); }
  EXPECT_EQ(state.acquired, 1);
  EXPECT_FALSE(BKE_checkpoint_callback_observation_safe(main));
  owners.expect_release();
}

TEST_F(UndoSystemCheckedDecodeTest, CheckpointNestedWorkerFaultDisposesOnlyExistingMainThreadWitness)
{
  UndoSystemTestData data;
  CheckpointTestOwners owners(data);
  owners.add_manager("Source", data.stack);
  Main *main = CTX_data_main(data.C);
  CheckpointCallbackTestWitness state;
  owners.begin();
  ScopedCheckpointCallbackObserver observer(main, &state,
      CheckpointCallbackTestWitness::before, CheckpointCallbackTestWitness::after);
  {
    ScopedCheckpointCallback outer(main);
    std::thread worker([&]() { ScopedCheckpointCallback callback(main); });
    worker.join();
    EXPECT_EQ(state.live, 1);
    { ScopedCheckpointCallback later(main); }
    EXPECT_EQ(state.acquired, 1);
  }
  EXPECT_FALSE(observer.valid());
  EXPECT_FALSE(BKE_checkpoint_callback_observation_safe(main));
  EXPECT_TRUE(state.saw_null_main);
  EXPECT_EQ(state.released, 1);
  EXPECT_EQ(state.live, 0);
  owners.expect_release();
}

}  // namespace blender::bke::tests
