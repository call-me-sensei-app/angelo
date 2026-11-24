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
#include "BKE_screen.hh"
#include "BLI_listbase.hh"

#include "BLI_time.hh"

#include <algorithm>
#include <memory>
#include <chrono>
#include <cstdio>
#include <vector>

#include "DNA_screen_types.h"
#include "DNA_userdef_types.h"
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
  const double at = ui_angelo_now(); /* After it was placed: a since never updated stays before it. */
  BPY_angelo_guard_note_input(&win, 0, 0, WINDEACTIVATE, KM_NOTHING);
  EXPECT_FALSE(ui_angelo_focus_current(but, &win)) << "armed before the window lost focus";
  EXPECT_FALSE(ui_angelo_follow_focus(but, &win));
  EXPECT_FALSE(but.angelo_armed);
  EXPECT_GE(but.angelo_since, at) << "counted again from the change";
  EXPECT_LE(but.angelo_since, ui_angelo_now());
  EXPECT_TRUE(but.angelo_placed) << "still on screen where it was, as a guard region is";
  EXPECT_TRUE(ui_angelo_follow_focus(but, &win)) << "current again";
  BPY_angelo_guard_forget_window(&win);
}

/* The window comes forward while a control is on screen (Windows brings the main window forward
 * when Preferences closes, up to a second later; build2 on 336a00f, 2026-11-23: the press on the
 * Strict popup's checkbox minted nothing). These run on the guard's own clock: ARM_WAIT is a
 * deliberate window past ARM_SECONDS (0.3 s), as a person's hand takes. */
static constexpr int ARM_WAIT_MS = 400;
static const rcti CONTROL = {10, 190, 15, 35}; /* Region pixels; the region is at the origin. */
static const rcti MENU = {20, 120, 10, 60}; /* The menu over the control (window pixels). */

/** A move the window manager delivers (real input), then the button's hover handling of it. */
static void move(
    wmWindow &win, Button &but, const ARegion &region, const int x, const int y, const rcti &at = CONTROL)
{
  BPY_angelo_guard_note_input(&win, x, y, MOUSEMOVE, KM_NOTHING);
  const int xy[2] = {x, y};
  ui_angelo_note_hover_at(but, &win, &region, at, xy);
}

/** On screen, shown, the pointer known somewhere else, the window not yet brought forward. */
static void shown_on_screen(wmWindow &win, Button &but, const ARegion &region)
{
  BPY_angelo_guard_note_presented(&win);
  ui_angelo_note_placed(but, &win, &region, CONTROL);
  BPY_angelo_guard_note_presented(&win); /* The frame that shows it. */
  BPY_angelo_guard_note_input(&win, 600, 400, MOUSEMOVE, KM_NOTHING); /* Pointer known, elsewhere. */
}

/** The operating system brings the window forward (`WM_window_activate_simulate`'s path): the
 * guard's activation, then the window manager's synthetic move, which reaches no guard. */
static void activate(wmWindow &win)
{
  BPY_angelo_guard_note_activate(&win);
}

TEST(AngeloArming, APersonsMovesArmAControlTheActivationRestartedOnScreen)
{
  /* The person's pointer rests on the control while the window comes forward: nothing about it
   * changes on screen, so nothing redraws it. Their hand keeps moving over it, as a hand does;
   * past ARM seconds from the activation it is armed, as a guard region is (`Authority::sync`
   * restarts a region from now, still on screen). */
  wmWindow win{};
  Button but;
  const ARegion region = region_at(0, 0);
  shown_on_screen(win, but, region);
  move(win, but, region, 40, 25);
  BLI_time_sleep_ms(ARM_WAIT_MS);
  move(win, but, region, 41, 25);
  EXPECT_TRUE(but.angelo_armed) << "armed before the window came forward";
  activate(win);
  move(win, but, region, 41, 25); /* The first move after it is the activation's own. */
  move(win, but, region, 42, 25);
  EXPECT_FALSE(but.angelo_armed) << "the activation restarts it: no grant from before";
  BLI_time_sleep_ms(ARM_WAIT_MS);
  move(win, but, region, 43, 25);
  EXPECT_TRUE(but.angelo_armed) << "on screen all along: the person's move ARM after the "
                                   "activation arms it, with no redraw to wait for";
  EXPECT_TRUE(ui_angelo_focus_current(but, &win));
  BPY_angelo_guard_forget_window(&win);
}

TEST(AngeloArming, AMoveTooSoonAfterTheActivationArmsNothing)
{
  /* The guard's intent stays: the activation restarts the count from now. */
  wmWindow win{};
  Button but;
  const ARegion region = region_at(0, 0);
  shown_on_screen(win, but, region);
  activate(win);
  move(win, but, region, 40, 25); /* The activation's own. */
  move(win, but, region, 41, 25);
  BPY_angelo_guard_note_presented(&win); /* A redraw, too. */
  move(win, but, region, 42, 25);
  EXPECT_FALSE(but.angelo_armed) << "less than ARM seconds since the activation";
  BLI_time_sleep_ms(200); /* Toward the edge of ARM (0.3 s) from the activation. */
  move(win, but, region, 43, 25);
  /* Judged on the time measured, not the time slept: a loaded runner's sleep can overshoot ARM. */
  if (ui_angelo_now() - BPY_angelo_guard_window_epoch_changed(&win) < 0.29) {
    EXPECT_FALSE(but.angelo_armed) << "a move inside ARM of the activation arms nothing";
  }
  BPY_angelo_guard_forget_window(&win);
}

TEST(AngeloArming, ASuitesArrivalThenPressArmsAControlShownBeforeTheActivation)
{
  /* The person-click suite's press (`qa_input.press`): the pointer arrives next to the point, and
   * DELAY (0.5 s) later moves onto it and presses. The popup opened before the window came
   * forward. With or without a redraw between, its press is armed. */
  for (const bool redraw : {false, true}) {
    wmWindow win{};
    Button but;
    const ARegion region = region_at(0, 0);
    shown_on_screen(win, but, region);
    activate(win);
    move(win, but, region, 20, 25); /* Arriving (the activation's own move). */
    if (redraw) {
      BPY_angelo_guard_note_presented(&win); /* Its highlight drawn. */
    }
    BLI_time_sleep_ms(500);
    move(win, but, region, 21, 25); /* Onto the point; the press follows at once. */
    EXPECT_TRUE(but.angelo_armed) << (redraw ? "with" : "without") << " a redraw between";
    BPY_angelo_guard_forget_window(&win);
  }
}

TEST(AngeloArming, APersonWhoReachesTheControlLaterIsArmedAtOnce)
{
  /* The review of d719ab2, SHOULD 1: the window comes forward with the pointer elsewhere; the
   * person moves about for longer than ARM, then reaches the control. It was on screen all along,
   * so their move onto it arms it at once and the press right after grants, as a consent region
   * does; counting from the first hover after the change refused it. */
  wmWindow win{};
  Button but;
  const ARegion region = region_at(0, 0);
  shown_on_screen(win, but, region);
  activate(win);
  BPY_angelo_guard_note_input(&win, 600, 401, MOUSEMOVE, KM_NOTHING); /* The activation's own. */
  BPY_angelo_guard_note_input(&win, 590, 380, MOUSEMOVE, KM_NOTHING); /* Elsewhere. */
  BLI_time_sleep_ms(ARM_WAIT_MS);
  BPY_angelo_guard_note_input(&win, 300, 200, MOUSEMOVE, KM_NOTHING); /* Still elsewhere. */
  move(win, but, region, 40, 25); /* Onto it; the press follows at once. */
  EXPECT_TRUE(but.angelo_armed) << "on screen since the activation, more than ARM ago";
  BPY_angelo_guard_forget_window(&win);
}

/** A press the window manager delivers (real input). */
static void press(wmWindow &win, const int x, const int y)
{
  BPY_angelo_guard_note_input(&win, x, y, LEFTMOUSE, KM_PRESS);
  BPY_angelo_guard_note_input(&win, x, y, LEFTMOUSE, KM_RELEASE);
}

TEST(AngeloArming, AControlInAWindowInTheBackCountsFromNow)
{
  /* The re-review of cf980b8, SHOULD A, case 1: the window went to the back minutes ago; a press
   * reaches it with no activation after (macOS takes a click on a background window), then a move
   * onto a control: being in the back is not "seen since then". It counts from now. */
  wmWindow win{};
  Button but;
  const ARegion region = region_at(0, 0);
  shown_on_screen(win, but, region);
  BPY_angelo_guard_note_input(&win, 0, 0, WINDEACTIVATE, KM_NOTHING);
  BLI_time_sleep_ms(ARM_WAIT_MS); /* Long before. */
  press(win, 300, 200); /* A press elsewhere in the window, still in the back. */
  move(win, but, region, 40, 25); /* Onto the control. */
  EXPECT_FALSE(but.angelo_armed) << "in the back: counted from now";
  BPY_angelo_guard_forget_window(&win);
}

TEST(AngeloArming, TheWindowForwardPressRestartsItsControlsFromThatPress)
{
  /* The re-review of cf980b8, SHOULD A, case 2: the press that brings the window forward comes up
   * to ACTIVATION_PRESS_SECONDS after the activation; it changes the focus epoch, so its controls
   * count from that press, and a jitter ARM after the activation but not after the press arms
   * nothing (a double-click's second press granted). */
  wmWindow win{};
  Button but;
  const ARegion region = region_at(0, 0);
  shown_on_screen(win, but, region);
  BPY_angelo_guard_note_input(&win, 0, 0, WINDEACTIVATE, KM_NOTHING);
  activate(win);
  BPY_angelo_guard_note_input(&win, 600, 401, MOUSEMOVE, KM_NOTHING); /* The activation's own. */
  BLI_time_sleep_ms(150);
  press(win, 40, 25); /* The window-forward press, on the control. */
  const double pressed = ui_angelo_now();
  BLI_time_sleep_ms(200); /* Past ARM from the activation, not from the press. */
  move(win, but, region, 41, 25);
  if (ui_angelo_now() - pressed < 0.29) { /* Judged on the time measured. */
    EXPECT_FALSE(but.angelo_armed) << "counted from the window-forward press";
  }
  BLI_time_sleep_ms(ARM_WAIT_MS);
  move(win, but, region, 42, 25);
  EXPECT_TRUE(but.angelo_armed) << "ARM after that press";
  BPY_angelo_guard_forget_window(&win);
}

TEST(AngeloArming, AControlPlacedAnewAfterTheActivationWaitsForItsFrame)
{
  /* Moved after the change (a re-flow): drawn there only from the frame that shows it. */
  wmWindow win{};
  Button but;
  const ARegion region = region_at(0, 0);
  shown_on_screen(win, but, region);
  activate(win);
  BPY_angelo_guard_note_input(&win, 600, 401, MOUSEMOVE, KM_NOTHING); /* The activation's own. */
  BLI_time_sleep_ms(ARM_WAIT_MS);
  const rcti moved = {10, 190, 115, 135};
  move(win, but, region, 40, 125, moved);
  BLI_time_sleep_ms(ARM_WAIT_MS);
  move(win, but, region, 41, 125, moved);
  EXPECT_FALSE(but.angelo_armed) << "its new place has not been on screen yet";
  BPY_angelo_guard_note_presented(&win); /* The frame that shows it there. */
  BLI_time_sleep_ms(ARM_WAIT_MS);
  move(win, but, region, 42, 125, moved);
  EXPECT_TRUE(but.angelo_armed);
  BPY_angelo_guard_forget_window(&win);
}

TEST(AngeloArming, ARestingPointerWithOnlySyntheticHoversNeverArms)
{
  /* The window comes forward under a resting pointer: the window manager's own moves to where it
   * already is (the activation's, a refresh) are no person's, however long it rests. */
  wmWindow win{};
  Button but;
  const ARegion region = region_at(0, 0);
  shown_on_screen(win, but, region);
  move(win, but, region, 40, 25); /* The person's real move onto it, just before. */
  activate(win);
  BLI_time_sleep_ms(ARM_WAIT_MS);
  const int still[2] = {40, 25};
  ui_angelo_note_hover_at(but, &win, &region, CONTROL, still); /* The window manager's own move there. */
  EXPECT_FALSE(but.angelo_armed) << "their move came before the activation: none since";
  BPY_angelo_guard_forget_window(&win);
}

TEST(AngeloArming, TheActivationLeavesAnArmedControlUnarmedForItsPress)
{
  /* The click that brings the window forward never grants: an armed control is no longer current
   * the moment the window comes forward, and the activation's own hover disarms it. */
  wmWindow win{};
  Button but;
  const ARegion region = region_at(0, 0);
  shown_on_screen(win, but, region);
  move(win, but, region, 40, 25);
  BLI_time_sleep_ms(ARM_WAIT_MS);
  move(win, but, region, 41, 25);
  EXPECT_TRUE(but.angelo_armed);
  activate(win);
  EXPECT_FALSE(ui_angelo_focus_current(but, &win)) << "a press now finds it not current";
  move(win, but, region, 41, 25); /* The activation's own move. */
  EXPECT_FALSE(but.angelo_armed);
  BPY_angelo_guard_forget_window(&win);
}

/* A control a popup, menu or search box covered (the review of d719ab2's covered-control SHOULD;
 * the maintainer's "Fix in 2.1.0", 2026-11-23). block_draw draws it under the menu at the same
 * place, so it kept the frame it was first shown in, and the window manager's own move after the
 * menu closed (`addmousemove`) armed it at the point of the person's last real move, on the menu
 * item above it: a double-click's second press granted a control the person had not seen. */

TEST(AngeloArming, ACoveredControlArmsOnlyAfterItIsSeenAgain)
{
  wmWindow win{};
  Button but;
  const ARegion region = region_at(0, 0);
  shown_on_screen(win, but, region);
  move(win, but, region, 40, 25); /* The person arms it, then a menu opens over it. */
  BLI_time_sleep_ms(ARM_WAIT_MS);
  move(win, but, region, 41, 25);
  EXPECT_TRUE(but.angelo_armed);
  BLI_time_sleep_ms(ARM_WAIT_MS);
  BPY_angelo_guard_note_input(&win, 42, 25, MOUSEMOVE, KM_NOTHING); /* On the menu item, over it. */
  BPY_angelo_guard_note_presented(&win); /* The menu drawn over it; the control drawn beneath. */
  ui_angelo_restart_uncovered(but, &win, MENU); /* The menu closes on the person's click. */
  const int xy[2] = {42, 25};
  ui_angelo_note_hover_at(but, &win, &region, CONTROL, xy); /* The window manager's own move. */
  EXPECT_FALSE(but.angelo_armed) << "the double-click's second press finds it unarmed";
  EXPECT_TRUE(but.angelo_placed) << "drawn beneath the menu: on screen again from its close";
  BPY_angelo_guard_forget_window(&win);
}

TEST(AngeloArming, APersonStillOnAnUncoveredControlArmsWithTheirNextRealMove)
{
  /* The person keeps the pointer still where the menu was, then really moves 0.3 s or more after
   * it closed: armed, and their click grants. */
  wmWindow win{};
  Button but;
  const ARegion region = region_at(0, 0);
  shown_on_screen(win, but, region);
  BLI_time_sleep_ms(ARM_WAIT_MS);
  BPY_angelo_guard_note_input(&win, 42, 25, MOUSEMOVE, KM_NOTHING); /* On the menu item. */
  ui_angelo_restart_uncovered(but, &win, MENU);
  BPY_angelo_guard_note_presented(&win); /* Every close redraws its window. */
  const int still[2] = {42, 25};
  ui_angelo_note_hover_at(but, &win, &region, CONTROL, still);
  BLI_time_sleep_ms(ARM_WAIT_MS);
  ui_angelo_note_hover_at(but, &win, &region, CONTROL, still); /* Still: a refresh, no move. */
  EXPECT_FALSE(but.angelo_armed) << "the window manager's moves alone never arm it";
  move(win, but, region, 43, 25); /* Their real move, more than ARM after the close. */
  EXPECT_TRUE(but.angelo_armed);
  BPY_angelo_guard_forget_window(&win);
}

TEST(AngeloArming, AnUncoveredControlCountsFromTheFrameThatShowsItAgain)
{
  /* The review of cf980b8..f6b01a9, SHOULD 1: the close's redraw is slow (a heavy viewport, a first
   * EEVEE draw), and input queued meanwhile is timed after it: it must not arm a control that first
   * reached the screen again in that frame, until ARM after the frame's swap. */
  wmWindow win{};
  Button but;
  const ARegion region = region_at(0, 0);
  shown_on_screen(win, but, region);
  BLI_time_sleep_ms(ARM_WAIT_MS);
  ui_angelo_restart_uncovered(but, &win, MENU); /* The menu closes. */
  BLI_time_sleep_ms(ARM_WAIT_MS);                /* Its window's redraw takes longer than ARM. */
  BPY_angelo_guard_note_presented(&win);         /* The frame that shows the control again. */
  const double presented = ui_angelo_now();
  move(win, but, region, 40, 25); /* Queued during that draw: right after its swap. */
  if (ui_angelo_now() - presented < 0.29) {
    EXPECT_FALSE(but.angelo_armed) << "on screen again only from that frame";
  }
  BLI_time_sleep_ms(ARM_WAIT_MS);
  move(win, but, region, 41, 25);
  EXPECT_TRUE(but.angelo_armed) << "ARM after that frame";
  BPY_angelo_guard_forget_window(&win);
}

TEST(AngeloArming, OnlyControlsTheClosedMenuCoveredStartAgain)
{
  /* A submenu closes over its parent menu: the parent's items it covered start again, the rest
   * keep their arming. */
  wmWindow win{};
  Button covered, beside;
  const ARegion region = region_at(0, 0);
  const rcti far = {300, 480, 15, 35};
  shown_on_screen(win, covered, region);
  ui_angelo_note_placed(beside, &win, &region, far);
  BPY_angelo_guard_note_presented(&win);
  covered.angelo_armed = beside.angelo_armed = true;
  ui_angelo_restart_uncovered(covered, &win, MENU);
  ui_angelo_restart_uncovered(beside, &win, MENU);
  EXPECT_FALSE(covered.angelo_armed);
  EXPECT_TRUE(beside.angelo_armed) << "never under the submenu";
  BPY_angelo_guard_forget_window(&win);
}

TEST(AngeloArming, AControlScrolledAwayOrHiddenCountsFromTheFrameThatShowsItAgain)
{
  /* A menu scrolled a notch and back, or a control hidden and shown again, at the same place:
   * on screen again only from the frame that shows it. */
  for (const int flag : {int(UI_SCROLLED), int(UI_HIDDEN)}) {
    wmWindow win{};
    Button but;
    const ARegion region = region_at(0, 0);
    shown_on_screen(win, but, region);
    BLI_time_sleep_ms(ARM_WAIT_MS);
    but.flag |= flag;
    ui_angelo_note_hidden(but); /* block_draw skips it. */
    but.flag &= ~flag;
    ui_angelo_note_placed(but, &win, &region, CONTROL); /* Drawn there again. */
    move(win, but, region, 40, 25);
    BLI_time_sleep_ms(ARM_WAIT_MS);
    move(win, but, region, 41, 25);
    EXPECT_FALSE(but.angelo_armed) << "not shown again yet (flag " << flag << ")";
    BPY_angelo_guard_note_presented(&win); /* The frame that shows it again. */
    BLI_time_sleep_ms(ARM_WAIT_MS);
    move(win, but, region, 42, 25);
    EXPECT_TRUE(but.angelo_armed) << "flag " << flag;
    BPY_angelo_guard_forget_window(&win);
  }
}

/** p50 and p75 nanoseconds per call of `call` over `batches` batches of `per_batch` calls. */
template<typename Call> static std::pair<double, double> ns_per_call(Call call, int batches, int per_batch)
{
  std::vector<double> samples;
  for (int batch = 0; batch < batches; batch++) {
    const auto start = std::chrono::steady_clock::now();
    for (int i = 0; i < per_batch; i++) {
      call(i);
    }
    const std::chrono::duration<double, std::nano> took = std::chrono::steady_clock::now() - start;
    samples.push_back(took.count() / per_batch);
  }
  std::sort(samples.begin(), samples.end());
  return {samples[samples.size() / 2], samples[samples.size() * 3 / 4]};
}

TEST(AngeloArming, CostOfAHoverAndOfAnActivationsRestart)
{
  /* The latency rule: what a pointer move over a button costs, steady and right after its window
   * came forward (each call restarts it), printed for the evidence (build it at the base and at the
   * change; no timing is asserted, a loaded machine only slows both). A draw runs none of this. */
  wmWindow win{};
  Button but;
  const ARegion region = region_at(0, 0);
  shown_on_screen(win, but, region);
  const int xy[2] = {40, 25};
  BPY_angelo_guard_note_input(&win, 40, 25, MOUSEMOVE, KM_NOTHING);
  const auto hover = ns_per_call(
      [&](int) { ui_angelo_note_hover_at(but, &win, &region, CONTROL, xy); }, 31, 20000);
  const auto restart = ns_per_call(
      [&](int) {
        BPY_angelo_guard_note_activate(&win);
        ui_angelo_note_hover_at(but, &win, &region, CONTROL, xy);
      },
      31,
      20000);
  const auto activation = ns_per_call([&](int) { BPY_angelo_guard_note_activate(&win); }, 31, 20000);
  /* A draw: every hidden or scrolled-away button (`block_draw`), and a popup's close: every button
   * of the window (`ui_angelo_note_uncovered`), here one at a time, half of them under it. */
  Button hidden;
  hidden.flag |= UI_HIDDEN;
  const auto drawn_hidden = ns_per_call([&](int) { ui_angelo_note_hidden(hidden); }, 31, 20000);
  const rcti away = {300, 400, 300, 400};
  const auto uncovered = ns_per_call(
      [&](int i) { ui_angelo_restart_uncovered(but, &win, (i & 1) ? MENU : away); }, 31, 20000);
  printf("ANGELO_ARMING_COST hover p50 %.1f ns p75 %.1f ns; activation+hover p50 %.1f ns p75 %.1f ns; "
         "activation alone p50 %.1f ns p75 %.1f ns; hidden button per draw p50 %.1f ns p75 %.1f ns; "
         "uncover per button p50 %.1f ns p75 %.1f ns\n",
         hover.first, hover.second, restart.first, restart.second, activation.first, activation.second,
         drawn_hidden.first, drawn_hidden.second, uncovered.first, uncovered.second);
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

TEST(AngeloArming, APopupsCloseRestartsOnlyThePopupsDrawnBeneathIt)
{
  /* The review of c0b0845..17a0f0b's NIT: a lower popup (or tooltip) closing while an upper one stays open restarted
   * the upper one's buttons too, so the person's next press there was answered with a hint. Popups are drawn in
   * screen->regionbase order: the parent (before it) was under it and starts again; the upper one (after it) never
   * was and keeps its arming. */
  wmWindow win{};
  bScreen screen{};
  ARegion parent{}, lower{}, upper{};
  Block blocks[3];
  Button *buttons[3];
  ARegion *regions[3] = {&parent, &lower, &upper};
  for (int i = 0; i < 3; i++) {
    regions[i]->runtime = MEM_new<bke::ARegionRuntime>(__func__);
    regions[i]->winrct = {100, 300, 100, 300};
    BLI_addtail(&screen.regionbase, regions[i]);
    blocks[i].buttons_ptrs.append(std::make_unique<Button>());
    buttons[i] = blocks[i].buttons_ptrs.last().get();
    buttons[i]->angelo_placed = true;
    buttons[i]->angelo_place = {150, 250, 150, 170}; /* Window pixels: under the closing popup's rect. */
    buttons[i]->angelo_armed = true;
    buttons[i]->angelo_since = 1.0;
    BLI_addtail(&regions[i]->runtime->uiblocks, &blocks[i]);
  }
  ui_angelo_note_uncovered_in(&win, &screen, lower.winrct, &lower, true);
  EXPECT_FALSE(buttons[0]->angelo_armed) << "the parent beneath it starts again";
  EXPECT_TRUE(buttons[2]->angelo_armed) << "the popup above it was never under it";
  EXPECT_EQ(buttons[2]->angelo_since, 1.0);
  EXPECT_TRUE(buttons[1]->angelo_armed) << "the one going is never walked";
  ui_angelo_note_uncovered_in(&win, &screen, lower.winrct, nullptr, false);
  EXPECT_TRUE(buttons[2]->angelo_armed) << "an area's overlapping region or the startup cover: never a popup";
  for (ARegion *region : regions) {
    BLI_listbase_clear(&region->runtime->uiblocks);
    MEM_delete(region->runtime);
  }
}

TEST(AngeloArming, ATemporaryRegionCoversWhatItDrawsNeverItsShadow)
{
  /* Person-click's covered case (the debug run of 44fabbb): the checkbox's own tooltip, beside it with only its shadow
   * margin over it, closed as the person pressed the checkbox and restarted it, so the press was refused. A temporary
   * region covers its opaque part: a tooltip's box (its rect less the popup margin), a popup's block. */
  const float scale = U.scale_factor;
  U.scale_factor = 1.0f; /* The popup margin scales with the interface (none at 0). */
  const int margin = int(UI_POPUP_MARGIN);
  ASSERT_GT(margin, 2);
  ARegion tooltip{};
  tooltip.runtime = MEM_new<bke::ARegionRuntime>(__func__);
  tooltip.winrct = {100, 400, 100, 160};
  const rcti tip = UI_angelo_temporary_cover(&tooltip);
  EXPECT_EQ(tip.xmin, 100 + margin);
  EXPECT_EQ(tip.ymax, 160 - margin) << "its shadow left out";
  wmWindow win{};
  bScreen screen{};
  ARegion area_region{};
  area_region.runtime = MEM_new<bke::ARegionRuntime>(__func__);
  ScrArea area{};
  BLI_addtail(&area.regionbase, &area_region);
  BLI_addtail(&screen.areabase, &area);
  Block block;
  block.buttons_ptrs.append(std::make_unique<Button>());
  block.buttons_ptrs.append(std::make_unique<Button>());
  Button &pressed = *block.buttons_ptrs[0]; /* Its button, above it: only the shadow over it. */
  Button &beneath = *block.buttons_ptrs[1]; /* A neighbour under its box. */
  pressed.angelo_place = {120, 300, 160 - margin / 2, 180};
  beneath.angelo_place = {120, 300, 120, 140};
  for (Button *but : {&pressed, &beneath}) {
    but->angelo_placed = true;
    but->angelo_armed = true;
  }
  BLI_addtail(&area_region.runtime->uiblocks, &block);
  ui_angelo_note_uncovered_in(&win, &screen, tip, &tooltip, true);
  EXPECT_TRUE(pressed.angelo_armed) << "the button the person pressed stays armed as its tooltip closes";
  EXPECT_FALSE(beneath.angelo_armed) << "what its box hid starts again";
  /* A popup's block: the block's rect in the region, its margin left out. */
  ARegion popup{};
  popup.runtime = MEM_new<bke::ARegionRuntime>(__func__);
  popup.winrct = {200, 500, 200, 400};
  Block menu;
  menu.flag = BLOCK_LOOP;
  menu.rect = {float(margin), float(300 - margin), float(margin), float(200 - margin)};
  BLI_addtail(&popup.runtime->uiblocks, &menu);
  const rcti drawn = UI_angelo_temporary_cover(&popup);
  EXPECT_EQ(drawn.xmin, 200 + margin);
  EXPECT_EQ(drawn.ymax, 400 - margin);
  for (ARegion *region : {&tooltip, &area_region, &popup}) {
    BLI_listbase_clear(&region->runtime->uiblocks);
    MEM_delete(region->runtime);
  }
  U.scale_factor = scale;
}


TEST(AngeloArming, AnActivationWithNoMoveOfItsOwnArmsOnlyOnThePersonsNextRealMove)
{
  /* Windows build2 on afb48bd, person-click run-2: Preferences closed and the main window came forward with no
   * synthetic move, so the person's arrival on the popup's checkbox was taken as the activation's own. Their next
   * real move onto it arms it; a pointer that only rests there (refreshes at the same point) never does. */
  wmWindow win{};
  Button but;
  const ARegion region = region_at(0, 0);
  shown_on_screen(win, but, region);
  BLI_time_sleep_ms(ARM_WAIT_MS);
  activate(win);
  BLI_time_sleep_ms(ARM_WAIT_MS);
  move(win, but, region, 20, 25); /* The person's arrival, taken as the activation's own move. */
  EXPECT_FALSE(but.angelo_armed) << "a move whose origin is unknown arms nothing";
  move(win, but, region, 20, 25); /* At rest: a refresh, not a move. */
  BLI_time_sleep_ms(ARM_WAIT_MS);
  move(win, but, region, 20, 25);
  EXPECT_FALSE(but.angelo_armed) << "an activation and a resting pointer never arm";
  move(win, but, region, 21, 25); /* The press's own move, one pixel. */
  EXPECT_TRUE(but.angelo_armed) << "the person's next real move onto it arms it";
  BPY_angelo_guard_forget_window(&win);
}

TEST(AngeloArming, ATooltipsCloseNeverRestartsTheControlItDescribes)
{
  /* Windows build2 on afb48bd, person-click run-2, and its Mac reproduction: near the window's bottom Blender draws a
   * checkbox's tooltip over the checkbox itself; the person's press closed it, its close restarted the checkbox, and
   * the press was answered with a hint. The tooltip's own control (still where it was when the tooltip opened) keeps
   * its arming; every other control its box hid starts again, and so does its own one if it moved since. */
  wmWindow win{};
  bScreen screen{};
  ARegion area_region{};
  area_region.runtime = MEM_new<bke::ARegionRuntime>(__func__);
  ScrArea area{};
  BLI_addtail(&area.regionbase, &area_region);
  BLI_addtail(&screen.areabase, &area);
  Block block;
  block.buttons_ptrs.append(std::make_unique<Button>());
  block.buttons_ptrs.append(std::make_unique<Button>());
  Button &own = *block.buttons_ptrs[0];
  Button &neighbour = *block.buttons_ptrs[1];
  own.angelo_place = {11, 191, 15, 35};
  neighbour.angelo_place = {11, 191, 42, 62};
  for (Button *but : {&own, &neighbour}) {
    but->angelo_placed = true;
    but->angelo_armed = true;
  }
  BLI_addtail(&area_region.runtime->uiblocks, &block);
  const rcti box = {0, 400, 10, 70}; /* The tooltip's box over both. */
  const AngeloTooltipOwn opened = ui_angelo_tooltip_own(own);
  ui_angelo_note_uncovered_in(&win, &screen, box, nullptr, true, &opened);
  EXPECT_TRUE(own.angelo_armed) << "the control it describes keeps its arming";
  EXPECT_FALSE(neighbour.angelo_armed) << "what it hid starts again";
  own.angelo_place = {11, 191, 20, 40}; /* Moved since the tooltip opened. */
  ui_angelo_note_uncovered_in(&win, &screen, box, nullptr, true, &opened);
  EXPECT_FALSE(own.angelo_armed) << "moved since: placed anew, it starts again";
  BLI_listbase_clear(&area_region.runtime->uiblocks);
  MEM_delete(area_region.runtime);
}


TEST(AngeloArming, ATooltipsCloseRestartsItsOwnControlThatStartedAgainUnderIt)
{
  /* The review of 3cfdc5f, MAJOR: a tooltip exists for the active button, and a rebuild keeps the active button as the
   * same object even when its label or operator properties changed (another row of a per-item operator, a data
   * change), so `ui_angelo_same_control` starts it again and its next draw re-places it at the same rect, under the
   * tooltip. The person never saw it in that form: the tooltip's close must restart it, whatever its address and
   * place say. */
  wmWindow win{};
  bScreen screen{};
  ARegion area_region = region_at(0, 0);
  area_region.runtime = MEM_new<bke::ARegionRuntime>(__func__);
  ScrArea area{};
  BLI_addtail(&area.regionbase, &area_region);
  BLI_addtail(&screen.areabase, &area);
  Block block;
  block.buttons_ptrs.append(std::make_unique<Button>());
  Button &own = *block.buttons_ptrs[0];
  ui_angelo_note_placed(own, &win, &area_region, CONTROL);
  own.angelo_armed = true;
  BLI_addtail(&area_region.runtime->uiblocks, &block);
  const AngeloTooltipOwn opened = ui_angelo_tooltip_own(own); /* Its tooltip opens over it. */
  ui_angelo_start_again(own); /* Another control in its slot, the same Button kept active. */
  ui_angelo_note_placed(own, &win, &area_region, CONTROL); /* Drawn again at the same rect. */
  own.angelo_armed = true; /* A real move armed what the person could not see under the tooltip. */
  const rcti box = {0, 400, 0, 70};
  ui_angelo_note_uncovered_in(&win, &screen, box, nullptr, true, &opened);
  EXPECT_FALSE(own.angelo_armed) << "started again under its tooltip: the close restarts it";
  BLI_listbase_clear(&area_region.runtime->uiblocks);
  MEM_delete(area_region.runtime);
  BPY_angelo_guard_forget_window(&win);
}

}  // namespace blender::ui::tests
