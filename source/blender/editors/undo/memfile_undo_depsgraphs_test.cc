/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Memfile undo takes the depsgraphs out of Main for its read and gives them back after it. Main
 * changes made meanwhile tag no graph, so the give-back must make every evaluated copy that may
 * point where its original no longer does copy again, on success and on every failed decode.
 * A ToolSettings ID pointer stands for any of them: the 2.0.0 candidate crashed in the next
 * relations build on a stale ToolSettings pointer after a refused Dyntopo read installed Main.
 */

#include "testing/testing.h"

#include "BKE_gtest_base.hh"
#include "BKE_lib_id.hh"
#include "BKE_main.hh"
#include "BKE_scene.hh"

#include "DNA_ID.h"
#include "DNA_image_types.h"
#include "DNA_scene_types.h"

#include "DEG_depsgraph.hh"
#include "DEG_depsgraph_query.hh"

#include "undo_intern.hh"

namespace blender::ed::undo::tests {

class MemfileUndoDepsgraphsTest : public bke::BlenderGTestBase {
 protected:
  Main *bmain = nullptr;
  Scene *scene = nullptr;
  ViewLayer *view_layer = nullptr;
  Depsgraph *depsgraph = nullptr;
  Image *before = nullptr, *after = nullptr;

  void SetUp() override
  {
    bmain = BKE_main_new();
    scene = BKE_id_new<Scene>(bmain, "Scene");
    view_layer = static_cast<ViewLayer *>(scene->view_layers.first());
    before = BKE_id_new<Image>(bmain, "Before");
    after = BKE_id_new<Image>(bmain, "After");
    scene->toolsettings->imapaint.stencil = before;
    depsgraph = BKE_scene_ensure_depsgraph(bmain, scene, view_layer);
    BKE_scene_graph_evaluated_ensure(depsgraph, bmain);
  }
  void TearDown() override
  {
    BKE_main_free(bmain);
  }

  const Image *evaluated_stencil()
  {
    Depsgraph *graph = BKE_scene_get_depsgraph(scene, view_layer);
    EXPECT_EQ(graph, depsgraph);
    BKE_scene_graph_evaluated_ensure(graph, bmain);
    return DEG_get_evaluated_scene(graph)->toolsettings->imapaint.stencil;
  }

  /**
   * The next relations build walks every expanded evaluated copy not tagged for a new copy
   * (#DepsgraphNodeBuilder::update_invalid_cow_pointers), dereferencing each ID it points at.
   * Checked before that build, so a missing tag fails here instead of reading freed memory.
   */
  bool evaluated_scene_copies_again()
  {
    return (DEG_get_evaluated_scene(depsgraph)->id.recalc & ID_RECALC_SYNC_TO_EVAL) != 0;
  }

  GHash *take_out()
  {
    EXPECT_EQ(evaluated_stencil(), before);
    /* As the editors do after each update (#scene_graph_update_tagged's callers): the copies'
     * tags from their last evaluation are gone before anything changes. */
    DEG_ids_clear_recalc(depsgraph, false);
    EXPECT_FALSE(evaluated_scene_copies_again());
    GHash *taken = BKE_scene_undo_depsgraphs_extract(bmain);
    EXPECT_EQ(BKE_scene_get_depsgraph(scene, view_layer), nullptr);
    return taken;
  }
};

TEST_F(MemfileUndoDepsgraphsTest, FailedDecodeCopiesEveryEvaluatedCopyAgain)
{
  /* What the read does while the graphs are out: the scene it restores no longer points at an
   * ID it frees, and it tags nothing (the graphs, though out, stay registered with Main and would
   * take a tag: #BKE_id_delete's would hide the hazard). A failed decode has no read tags. */
  GHash *taken = take_out();
  scene->toolsettings->imapaint.stencil = nullptr;
  BKE_id_free_ex(bmain, before, LIB_ID_FREE_NO_DEG_TAG | LIB_ID_FREE_NO_USER_REFCOUNT, false);
  before = nullptr;
  memfile_undo_depsgraphs_give_back(bmain, taken, true);
  ASSERT_TRUE(evaluated_scene_copies_again());
  EXPECT_EQ(evaluated_stencil(), nullptr);
}

TEST_F(MemfileUndoDepsgraphsTest, CompleteReadCopiesWhatItReadInPlaceAgain)
{
  GHash *taken = take_out();
  scene->toolsettings->imapaint.stencil = after;
  scene->id.tag |= ID_TAG_UNDO_OLD_ID_REREAD_IN_PLACE;
  memfile_undo_depsgraphs_give_back(bmain, taken, false);
  ASSERT_TRUE(evaluated_scene_copies_again());
  EXPECT_EQ(evaluated_stencil(), after);
  EXPECT_EQ(scene->id.tag & ID_TAG_UNDO_OLD_ID_REREAD_IN_PLACE, 0);
}

TEST_F(MemfileUndoDepsgraphsTest, FailedDecodeKeepsChangesToRecordForTheNextStep)
{
  scene->id.recalc_after_undo_push = ID_RECALC_PARAMETERS;
  memfile_undo_depsgraphs_give_back(bmain, take_out(), true);
  EXPECT_EQ(scene->id.recalc_after_undo_push & ID_RECALC_PARAMETERS, ID_RECALC_PARAMETERS);
}

TEST_F(MemfileUndoDepsgraphsTest, NoGraphsTakenIsNothing)
{
  memfile_undo_depsgraphs_give_back(bmain, nullptr, true);
  EXPECT_EQ(BKE_scene_get_depsgraph(scene, view_layer), depsgraph);
}

/* A read that takes no depsgraphs (it does not reuse the old Main's data) still tags the 'never
 * undo' IDs it moves over: those tags must not outlive the decode. Seen as a stale tag on the
 * window manager in the next library read's `lib_link_all`, after Undo of a step with an asset
 * adjunct (test_blender_stock_asset_undo.py on a build with asserts). */
TEST_F(MemfileUndoDepsgraphsTest, NoGraphsTakenStillClearsTheReadsTags)
{
  scene->id.tag |= ID_TAG_UNDO_OLD_ID_REUSED_NOUNDO;
  before->id.tag |= ID_TAG_UNDO_OLD_ID_REUSED_UNCHANGED;
  after->id.tag |= ID_TAG_UNDO_OLD_ID_REREAD_IN_PLACE | ID_TAG_DOIT;
  memfile_undo_depsgraphs_give_back(bmain, nullptr, false);
  EXPECT_EQ(scene->id.tag & ID_TAG_UNDO_OLD_ID_REUSED_NOUNDO, 0);
  EXPECT_EQ(before->id.tag & ID_TAG_UNDO_OLD_ID_REUSED_UNCHANGED, 0);
  EXPECT_EQ(after->id.tag & ID_TAG_UNDO_OLD_ID_REREAD_IN_PLACE, 0);
  /* Only the read's own tags. */
  EXPECT_NE(after->id.tag & ID_TAG_DOIT, 0);
  EXPECT_EQ(BKE_scene_get_depsgraph(scene, view_layer), depsgraph);
}

}  // namespace blender::ed::undo::tests
