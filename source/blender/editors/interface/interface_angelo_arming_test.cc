/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Angelo: a Blender button's arming for a grant ticket follows the control and where it is on
 * screen (doc/angelo/grant_operators.rst; the reviewer's read of 6392a6f). A button moved on
 * screen (a scrolled sidebar, a re-flow) starts again; arming carries across redraws only to the
 * same control, so a reordered row of the same operator never inherits another row's arming.
 */

#include "BKE_idprop.hh"

#include "DNA_screen_types.h"
#include "DNA_windowmanager_types.h"

#include "WM_types.hh"

#include "MEM_guardedalloc.h"

#include "interface_intern.hh"

#ifdef WITH_PYTHON
#  include "intern/bpy_angelo_guard_authority.hh"
#endif

#include "testing/testing.h"

namespace blender::ui::tests {

static ARegion region_at(int x, int y)
{
  ARegion region{};
  region.winrct = {x, x + 400, y, y + 300};
  return region;
}

TEST(AngeloArming, AButtonMovedOnScreenStartsAgainAndIsDisarmed)
{
  Button but;
  wmWindow win{};
  const ARegion region = region_at(10, 20);
  const rcti rect = {100, 180, 50, 70};
  ui_angelo_note_placed(but, &win, &region, rect);
  EXPECT_TRUE(but.angelo_placed);
  EXPECT_EQ(but.angelo_place.xmin, 110) << "window pixels";
  but.angelo_armed = true;
  but.angelo_since = 1.0;
  ui_angelo_note_placed(but, &win, &region, rect);
  EXPECT_TRUE(but.angelo_armed) << "drawn where it was: it stays armed";
  EXPECT_EQ(but.angelo_since, 1.0);
  const rcti scrolled = {100, 180, 90, 110}; /* The same block rect, its region scrolled. */
  ui_angelo_note_placed(but, &win, &region, scrolled);
  EXPECT_FALSE(but.angelo_armed) << "moved on screen: disarmed";
  EXPECT_GT(but.angelo_since, 1.0) << "and drawn there only from now";
  but.angelo_armed = true;
  const ARegion moved = region_at(60, 20); /* Its region moved (a resized sidebar). */
  ui_angelo_note_placed(but, &win, &moved, scrolled);
  EXPECT_FALSE(but.angelo_armed);
}

TEST(AngeloArming, AButtonDrawnOffScreenIsPlacedByItsNextDrawInAWindow)
{
  Button but;
  wmWindow win{};
  const ARegion region = region_at(0, 0);
  const rcti rect = {10, 50, 10, 30};
  ui_angelo_note_placed(but, nullptr, &region, rect);
  EXPECT_FALSE(but.angelo_placed) << "off screen: not on screen anywhere yet";
  EXPECT_EQ(but.angelo_frame, UINT64_MAX);
  ui_angelo_note_placed(but, &win, &region, rect);
  EXPECT_TRUE(but.angelo_placed) << "its first draw in a window stamps it, at the same rect too";
  EXPECT_NE(but.angelo_frame, UINT64_MAX);
}

TEST(AngeloArming, AControlInAnActiveSlotCountsFromTheFrameThatShowsIt)
{
  Button but;
  wmWindow win{};
  const ARegion region = region_at(0, 0);
  const rcti rect = {10, 50, 10, 30};
  ui_angelo_note_placed(but, &win, &region, rect);
  but.angelo_armed = true;
  ui_angelo_start_again(but); /* Another control swapped into its slot (`ui_but_update_from_old_block`). */
  EXPECT_FALSE(but.angelo_armed);
  EXPECT_FALSE(but.angelo_placed);
  EXPECT_EQ(but.angelo_frame, UINT64_MAX);
  ui_angelo_note_placed(but, &win, &region, rect);
  EXPECT_TRUE(but.angelo_placed) << "the same rect is stamped again: no early return on a stale frame";
}

#ifdef WITH_PYTHON
TEST(AngeloArming, AButtonCountsFramesOfItsOwnWindow)
{
  /* Two windows (a popped-out chat and the main window): B presented frames A has not. A button placed in A by
   * an event while B was the last window drawn takes A's next frame, never B's (the review of 69d589a, SHOULD 1:
   * B's count ahead of A's left the button "not shown yet" for good, so it never armed). */
  wmWindow a{}, b{};
  for (int i = 0; i < 5; i++) {
    BPY_angelo_guard_note_presented(&b);
  }
  BPY_angelo_guard_note_presented(&a);
  Button but;
  const ARegion region = region_at(0, 0);
  ui_angelo_note_placed(but, &a, &region, {10, 50, 10, 30});
  EXPECT_EQ(but.angelo_frame, BPY_angelo_guard_drawing_frame(&a));
  EXPECT_LT(but.angelo_frame, BPY_angelo_guard_drawing_frame(&b));
  BPY_angelo_guard_note_presented(&a); /* A shows it. */
  EXPECT_LT(BPY_angelo_guard_shown(&a, but.angelo_frame, 0.0), 1.0e300) << "on screen once A swapped";
  BPY_angelo_guard_forget_window(&a);
  BPY_angelo_guard_forget_window(&b);
}

TEST(AngeloArming, AButtonArmedBeforeItsWindowLostFocusStartsAgain)
{
  /* As a consent region does on its window's epoch (review of 69d589a, SHOULD 3): armed, the window went to the
   * back, so it counts again from now and needs the person's move once more. */
  wmWindow win{};
  BPY_angelo_guard_note_presented(&win);
  Button but;
  const ARegion region = region_at(0, 0);
  ui_angelo_note_placed(but, &win, &region, {10, 50, 10, 30});
  but.angelo_armed = true;
  EXPECT_TRUE(ui_angelo_focus_current(but, &win));
  BPY_angelo_guard_note_input(&win, 0, 0, WINDEACTIVATE, KM_NOTHING);
  EXPECT_FALSE(ui_angelo_focus_current(but, &win)) << "armed before the window lost focus";
  EXPECT_FALSE(ui_angelo_follow_focus(but, &win));
  EXPECT_FALSE(but.angelo_armed);
  EXPECT_FALSE(but.angelo_placed) << "counted from the next frame that shows it";
  EXPECT_TRUE(ui_angelo_follow_focus(but, &win)) << "current again";
  BPY_angelo_guard_forget_window(&win);
}
#endif

static PointerRNA *properties(int value)
{
  PointerRNA *ptr = MEM_new<PointerRNA>(__func__);
  IDProperty *group = bke::idprop::create_group("wmOperatorProperties").release();
  IDP_AddToGroup(group, bke::idprop::create("index", value).release());
  ptr->data = group;
  return ptr;
}

static void release(Button &but)
{
  if (but.opptr) {
    IDP_FreeProperty(static_cast<IDProperty *>(but.opptr->data));
    MEM_delete(but.opptr);
    but.opptr = nullptr;
  }
}

TEST(AngeloArming, ArmingCarriesOnlyToTheSameControl)
{
  Button a, b;
  a.drawstr = b.drawstr = "Remove";
  EXPECT_TRUE(ui_angelo_same_control(a, b));
  b.drawstr = "Connect";
  EXPECT_FALSE(ui_angelo_same_control(a, b)) << "another label";
  b.drawstr = "Remove";
  a.opptr = properties(1);
  b.opptr = properties(1);
  EXPECT_TRUE(ui_angelo_same_control(a, b));
  release(b);
  b.opptr = properties(2); /* The same operator for another row, reordered into this slot. */
  EXPECT_FALSE(ui_angelo_same_control(a, b)) << "another row's values";
  release(a);
  release(b);
}

}  // namespace blender::ui::tests
