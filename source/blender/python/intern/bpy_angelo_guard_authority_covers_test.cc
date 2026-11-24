/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * The grant guard's consent regions under a Blender popup (the review of cf980b8..f6b01a9, SHOULD 3;
 * the maintainer: "Prove + fix in 2.1.0"). A menu, popover, dialog, pie, search box or tooltip drawn
 * over an armed Approve took no part in it: a move over the menu aimed at Approve beneath, and a click
 * on the menu's item minted Approve's ticket, so the double-click's second press granted it. While a
 * temporary region covers a consent region, that region takes no move and no press there, and when it
 * goes or moves the region starts again from then and from the window's next frame.
 */

#include "testing/testing.h"

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <vector>

#include "bpy_angelo_guard_authority_core.hh"

namespace blender::angelo_guard::tests {

static constexpr WindowKey W = 0x3000;
static constexpr uint64_t MENU = 0x77;

static RegionSpec approve()
{
  RegionSpec region; /* x 100..179, y 100..123 */
  region.x = 100;
  region.y = 100;
  region.width = 80;
  region.height = 24;
  region.idname = "angelo.approve";
  region.digest = "d1";
  return region;
}

class AuthorityCoverTest : public testing::Test {
 protected:
  Authority a;

  /** Shown at 0 (frames presented), the pointer below it, then into it once the shield passed. */
  void arm(const double at = SHIELD_SECONDS + 0.1)
  {
    a.note_presented(W, 0.0);
    a.set_regions(W, "chat", {approve()}, 0.0);
    a.note_presented(W, 0.0);
    a.note_move(W, 140, 90, at - 0.05);
    a.note_move(W, 140, 110, at);
  }
};

TEST_F(AuthorityCoverTest, APressOnAMenuOverAnArmedApproveMintsNothing)
{
  arm();
  a.set_cover(W, MENU, 120, 90, 120, 60, 1.15); /* A menu opens over the armed Approve. */
  EXPECT_FALSE(a.note_press(W, 140, 110, 1.2)) << "the press is on the menu's item";
  EXPECT_FALSE(a.take("angelo.approve", "d1", 1.3));
}

TEST_F(AuthorityCoverTest, MovesOverAMenuNeverAimAtWhatItCovers)
{
  /* The menu covers Approve's left half; the person's moves are over the menu. A press on the half
   * still shown finds Approve never aimed at. */
  a.note_presented(W, 0.0);
  a.set_regions(W, "chat", {approve()}, 0.0);
  a.note_presented(W, 0.0);
  a.set_cover(W, MENU, 100, 100, 40, 24, 0.1);
  a.note_move(W, 120, 90, SHIELD_SECONDS + 0.05);
  a.note_move(W, 120, 110, SHIELD_SECONDS + 0.1); /* Over the menu, over Approve's left half. */
  EXPECT_FALSE(a.note_press(W, 160, 110, SHIELD_SECONDS + 0.3)) << "no aim from moves over the menu";
  EXPECT_FALSE(a.take("angelo.approve", "d1", SHIELD_SECONDS + 0.4));
}

TEST_F(AuthorityCoverTest, AnUncoveredRegionStartsAgainFromTheFrameThatShowsIt)
{
  arm();
  a.set_cover(W, MENU, 120, 90, 120, 60, 1.15);
  a.clear_cover(W, MENU, 1.5); /* The menu closes on the double-click's first press. */
  EXPECT_FALSE(a.note_press(W, 140, 110, 1.6)) << "its second press, without moving";
  a.note_move(W, 140, 90, 1.7);
  a.note_move(W, 140, 110, 1.5 + SHIELD_SECONDS + 0.2); /* Into it, before the close's frame. */
  EXPECT_FALSE(a.note_press(W, 140, 110, 1.5 + SHIELD_SECONDS + 0.3)) << "not shown again yet";
  a.note_presented(W, 2.5); /* The frame that shows it again. */
  a.note_move(W, 140, 90, 2.5 + SHIELD_SECONDS);
  a.note_move(W, 140, 110, 2.5 + SHIELD_SECONDS + 0.05);
  EXPECT_TRUE(a.note_press(W, 140, 110, 2.5 + SHIELD_SECONDS + 0.1)) << "the person's move and press";
  EXPECT_TRUE(a.take("angelo.approve", "d1", 2.5 + SHIELD_SECONDS + 0.2));
}

TEST_F(AuthorityCoverTest, AMenuMovedOffRestartsWhatItCoveredAndCoversWhereItIs)
{
  arm();
  a.set_cover(W, MENU, 120, 90, 120, 60, 1.15);
  a.set_cover(W, MENU, 400, 300, 120, 60, 1.2); /* Dragged away. */
  EXPECT_FALSE(a.note_press(W, 140, 110, 1.3)) << "what it covered starts again";
}

TEST_F(AuthorityCoverTest, ACoverNoDrawListsGoes)
{
  /* A temporary region freed without its close (a file load, a screen change): the window's next
   * draw lists only what it draws, so the stale cover goes and what it covered starts again. */
  arm();
  a.set_cover(W, MENU, 120, 90, 120, 60, 1.15);
  a.sync_covers(W, {}, 1.5);
  a.note_presented(W, 2.0);
  a.note_move(W, 140, 90, 2.0 + SHIELD_SECONDS);
  a.note_move(W, 140, 110, 2.0 + SHIELD_SECONDS + 0.05);
  EXPECT_TRUE(a.note_press(W, 140, 110, 2.0 + SHIELD_SECONDS + 0.1)) << "no stale cover left";
}

TEST_F(AuthorityCoverTest, ACanvasMenuCoversOtherSurfacesButNeverItsOwn)
{
  /* The review of f6b01a9..da9ba66, SHOULD 2: Angelo's own Flows popup (surface "flowmenu") drawn
   * over a card's armed Approve: a press there is the popup's row, never Approve's; the popup's own
   * rows still take their presses; and when it closes, Approve starts again. */
  arm();
  RegionSpec row = approve();
  row.idname = "flowmenu.row";
  row.digest = "r1";
  row.shield = false;
  a.set_regions(W, "flowmenu", {row}, 1.0);
  a.note_presented(W, 1.0); /* The frame that shows the popup. */
  a.note_move(W, 140, 130, 1.5);
  a.note_move(W, 141, 110, 1.5 + ARM_SECONDS + 0.01); /* Onto the row, over Approve. */
  a.surface_cover(W, "flowmenu", 100, 100, 80, 24, 1.15);
  EXPECT_TRUE(a.note_press(W, 141, 110, 1.9)) << "the popup's own row takes its press";
  EXPECT_TRUE(a.take("flowmenu.row", "r1", 1.95));
  EXPECT_FALSE(a.take("angelo.approve", "d1", 1.95)) << "never the Approve beneath";
  a.surface_cover(W, "flowmenu", 0, 0, 0, 0, 2.0); /* The popup closes. */
  a.set_regions(W, "flowmenu", {}, 2.0);
  EXPECT_FALSE(a.note_press(W, 141, 110, 2.1)) << "Approve starts again: not aimed at";
}

TEST_F(AuthorityCoverTest, ADrawSyncNeverDropsACanvasMenusCover)
{
  arm();
  a.surface_cover(W, "flowmenu", 120, 90, 120, 60, 1.15);
  a.sync_covers(W, {}, 1.2); /* No Blender popup drawn: the canvas menu's cover stays. */
  EXPECT_FALSE(a.note_press(W, 140, 110, 1.3));
}

TEST_F(AuthorityCoverTest, ACoverElsewhereChangesNothing)
{
  arm();
  a.set_cover(W, MENU, 500, 500, 100, 100, 1.15);
  EXPECT_TRUE(a.note_press(W, 140, 110, 1.2));
  a.clear_cover(W, MENU, 1.3);
  EXPECT_TRUE(a.take("angelo.approve", "d1", 1.4));
}

/** p50 and p75 nanoseconds per call of `call`, over `batches` batches of `per_batch` calls. */
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

TEST_F(AuthorityCoverTest, CostOfAMoveAndAPressWithCovers)
{
  /* The latency rule: a move and a press with no cover and with three (a menu, its submenu, a
   * tooltip), printed for the evidence; no timing asserted. */
  arm();
  double t = 10.0;
  const auto move = [&](int i) { a.note_move(W, 300 + (i & 7), 300, t += 1e-6); };
  const auto press = [&](int) { a.note_press(W, 600, 600, t += 1e-6); };
  const auto moves0 = ns_per_call(move, 31, 20000);
  const auto presses0 = ns_per_call(press, 31, 20000);
  for (uint64_t key = 1; key <= 3; key++) {
    a.set_cover(W, key, 200 + int(key) * 10, 200, 100, 100, t);
  }
  const auto moves3 = ns_per_call(move, 31, 20000);
  const auto presses3 = ns_per_call(press, 31, 20000);
  printf("ANGELO_COVER_COST move p50 %.1f ns p75 %.1f ns (3 covers: %.1f / %.1f); press p50 %.1f ns "
         "p75 %.1f ns (3 covers: %.1f / %.1f)\n",
         moves0.first, moves0.second, moves3.first, moves3.second, presses0.first, presses0.second,
         presses3.first, presses3.second);
}

}  // namespace blender::angelo_guard::tests
