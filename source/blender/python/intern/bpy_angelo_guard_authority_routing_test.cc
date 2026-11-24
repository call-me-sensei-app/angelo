/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * The grant guard's consent regions under an area's overlapping regions (the review of f6b01a9..da9ba66, SHOULD 1;
 * the maintainer: "Fix properly in 2.1.0"). A sidebar (N panel), toolbar, transparent header, HUD or asset shelf drawn
 * over the region a card is drawn in took no part in the guard: a move over the open N panel aimed at the card's
 * Approve beneath, and a press there minted its ticket. A surface's regions now take only the moves and presses
 * Blender's own routing gives the region they are drawn in (`wm_event_pointer_region`, its own gtests in
 * wm_angelo_pointer_region_test.cc), and an overlapping region that hides, shrinks or moves restarts what it covered.
 */

#include "testing/testing.h"

#include <vector>

#include "bpy_angelo_guard_authority_core.hh"

namespace blender::angelo_guard::tests {

static constexpr WindowKey W = 0x4000;
static constexpr uint64_t MAIN = 0x5000;    /* The 3D view's main region, where the card is drawn. */
static constexpr uint64_t SIDEBAR = 0x5001; /* Its N panel, drawn over the card's right part. */

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

class AuthorityRoutingTest : public testing::Test {
 protected:
  Authority a;

  /** Approve drawn in MAIN and shown at 0; the pointer known below it, over MAIN. */
  void show()
  {
    a.note_presented(W, 0.0);
    a.set_regions(W, "chat", {approve()}, 0.0, MAIN);
    a.note_presented(W, 0.0);
    a.note_move(W, 140, 90, MAIN, SHIELD_SECONDS);
  }
};

TEST_F(AuthorityRoutingTest, MovesAndAPressOnTheOpenSidebarNeverReachTheCardBeneath)
{
  show();
  /* The person's hand on the N panel, over Approve's rect: Blender gives every event to the sidebar. */
  a.note_move(W, 160, 90, SIDEBAR, SHIELD_SECONDS + 0.05);
  a.note_move(W, 160, 110, SIDEBAR, SHIELD_SECONDS + 0.1);
  EXPECT_FALSE(a.note_press(W, 160, 110, SIDEBAR, SHIELD_SECONDS + 0.3)) << "the press is the sidebar's";
  EXPECT_FALSE(a.take("angelo.approve", "d1", SHIELD_SECONDS + 0.4));
  /* Even once aimed at from MAIN, a press the sidebar takes is never Approve's. */
  a.note_move(W, 120, 90, MAIN, SHIELD_SECONDS + 0.5);
  a.note_move(W, 120, 110, MAIN, SHIELD_SECONDS + 0.6);
  a.note_move(W, 160, 110, SIDEBAR, SHIELD_SECONDS + 0.7);
  EXPECT_FALSE(a.note_press(W, 160, 110, SIDEBAR, SHIELD_SECONDS + 0.8));
  EXPECT_FALSE(a.take("angelo.approve", "d1", SHIELD_SECONDS + 0.9));
}

TEST_F(AuthorityRoutingTest, WhereBlenderGivesTheMainRegionTheEventTheCardTakesIt)
{
  /* The sidebar's transparent part (below its panels) or Approve's part outside it: Blender gives MAIN the event, so
   * Approve arms and grants as anywhere, the sidebar's restart-only cover over it notwithstanding. */
  show();
  a.sync_covers(W, {{SIDEBAR, 150, 0, 200, 400, true}}, 0.5);
  a.note_move(W, 160, 110, MAIN, SHIELD_SECONDS + 0.1);
  EXPECT_TRUE(a.note_press(W, 160, 110, MAIN, SHIELD_SECONDS + 0.2));
  EXPECT_TRUE(a.take("angelo.approve", "d1", SHIELD_SECONDS + 0.3));
}

TEST_F(AuthorityRoutingTest, FromTheSidebarOntoTheCardsShownPartIsAMoveIntoIt)
{
  /* The pointer was inside Approve's rect but over the sidebar; it moves onto the part Blender gives MAIN: the person's
   * move into Approve, as a move from outside it. */
  show();
  a.note_move(W, 160, 110, SIDEBAR, SHIELD_SECONDS + 0.05);
  a.note_move(W, 140, 110, MAIN, SHIELD_SECONDS + 0.1);
  EXPECT_TRUE(a.note_press(W, 140, 110, MAIN, SHIELD_SECONDS + 0.2));
  EXPECT_TRUE(a.take("angelo.approve", "d1", SHIELD_SECONDS + 0.3));
}

TEST_F(AuthorityRoutingTest, ASidebarThatHidesRestartsWhatItCoveredAndSaysWhere)
{
  show();
  a.note_move(W, 140, 110, MAIN, SHIELD_SECONDS + 0.05); /* Aimed at from MAIN. */
  const Authority::DrawnCover sidebar{SIDEBAR, 120, 90, 120, 60, true};
  EXPECT_TRUE(a.sync_covers(W, {sidebar}, SHIELD_SECONDS + 0.1).empty()) << "it appears: nothing restarts yet";
  const std::vector<Authority::Rect> gone = a.sync_covers(W, {}, SHIELD_SECONDS + 0.2); /* Hidden. */
  ASSERT_EQ(gone.size(), 1u) << "the window manager restarts Blender's own buttons there too";
  EXPECT_EQ(gone[0].key, SIDEBAR);
  EXPECT_EQ(gone[0].x, 120);
  EXPECT_EQ(gone[0].width, 120);
  EXPECT_FALSE(a.note_press(W, 140, 110, MAIN, SHIELD_SECONDS + 0.3)) << "what it covered starts again";
  a.note_presented(W, SHIELD_SECONDS + 0.4); /* The frame that shows it. */
  a.note_move(W, 140, 90, MAIN, 2 * SHIELD_SECONDS + 0.5);
  a.note_move(W, 140, 110, MAIN, 2 * SHIELD_SECONDS + 0.6);
  EXPECT_TRUE(a.note_press(W, 140, 110, MAIN, 2 * SHIELD_SECONDS + 0.7)) << "the person's move and press";
}

TEST_F(AuthorityRoutingTest, ASidebarResizedRestartsWhereItWasAndATemporaryRegionsCloseIsNotReported)
{
  show();
  a.sync_covers(W, {{SIDEBAR, 120, 90, 120, 60, true}, {0x77, 500, 500, 50, 50, false}}, 0.5);
  const std::vector<Authority::Rect> moved = a.sync_covers(W, {{SIDEBAR, 160, 90, 80, 60, true}}, 0.6);
  ASSERT_EQ(moved.size(), 1u) << "only the restart-only cover; a popup's close restarts Blender's buttons itself";
  EXPECT_EQ(moved[0].x, 120) << "where it was";
}

TEST_F(AuthorityRoutingTest, AFocusAfterAPressTheSidebarTookIsNeverThePersons)
{
  show();
  a.note_press(W, 160, 110, SIDEBAR, SHIELD_SECONDS + 0.1); /* On the sidebar, over Approve. */
  EXPECT_FALSE(a.focus(W, "chat", "angelo.approve", "d1", SHIELD_SECONDS + 0.15)) << "the press was the sidebar's";
  a.note_release(W, SHIELD_SECONDS + 0.2);
  a.note_press(W, 140, 110, MAIN, SHIELD_SECONDS + 0.3);
  EXPECT_TRUE(a.focus(W, "chat", "angelo.approve", "d1", SHIELD_SECONDS + 0.35)) << "a press Blender gave MAIN";
}

TEST_F(AuthorityRoutingTest, ASurfaceDrawnInAnotherRegionNowIsLaidOutAnew)
{
  show();
  a.note_move(W, 140, 110, MAIN, SHIELD_SECONDS + 0.05); /* Aimed at. */
  a.set_regions(W, "chat", {approve()}, SHIELD_SECONDS + 0.1, SIDEBAR); /* The same rect, now in the sidebar. */
  a.note_presented(W, SHIELD_SECONDS + 0.1);
  EXPECT_FALSE(a.note_press(W, 140, 110, SIDEBAR, 2 * SHIELD_SECONDS + 0.2)) << "not aimed at where it is now";
}

TEST_F(AuthorityRoutingTest, APressNoRegionTakesIsNoSurfacesPress)
{
  show();
  a.note_move(W, 140, 110, MAIN, SHIELD_SECONDS + 0.05);
  EXPECT_FALSE(a.note_press(W, 140, 110, 0, SHIELD_SECONDS + 0.1)) << "between areas, or an area that takes none";
}

TEST_F(AuthorityRoutingTest, AnotherWindowsRoutingNeverReachesThisOne)
{
  /* The same region pointer is never shared across windows, but a target is per window anyway. */
  show();
  a.note_move(W + 1, 140, 110, MAIN, SHIELD_SECONDS + 0.05);
  EXPECT_FALSE(a.note_press(W + 1, 140, 110, MAIN, SHIELD_SECONDS + 0.1));
  EXPECT_FALSE(a.take("angelo.approve", "d1", SHIELD_SECONDS + 0.2));
}

/* The last review of da9ba66..1b4e7f0. */

TEST_F(AuthorityRoutingTest, ACanvasMenuGoesWithItsRegionWhenNoCloseWasReported)
{
  /* SHOULD 1: Flows open over the card's armed Approve, then a file load (or a close C refused from an agent's thread):
   * no close is reported, but the region it was drawn in is gone, so the next draw drops its cover and what it covered
   * starts again; while its region is shown it stays. */
  show();
  a.note_move(W, 140, 110, MAIN, SHIELD_SECONDS + 0.05);
  a.surface_cover(W, "flowmenu", 120, 90, 120, 60, SHIELD_SECONDS + 0.1, MAIN);
  a.sync_covers(W, {}, {MAIN, SIDEBAR}, SHIELD_SECONDS + 0.15);
  EXPECT_FALSE(a.note_press(W, 140, 110, MAIN, SHIELD_SECONDS + 0.2)) << "its region shown: it covers";
  a.note_release(W, SHIELD_SECONDS + 0.25);
  a.sync_covers(W, {}, {SIDEBAR}, SHIELD_SECONDS + 0.3); /* Its region is gone. */
  a.note_presented(W, SHIELD_SECONDS + 0.3);
  a.note_move(W, 140, 90, MAIN, 2 * SHIELD_SECONDS + 0.4);
  a.note_move(W, 140, 110, MAIN, 2 * SHIELD_SECONDS + 0.5);
  EXPECT_FALSE(a.note_press(W, 140, 110, MAIN, 2 * SHIELD_SECONDS + 0.6))
      << "Approve's own region went with it: its regions are forgotten too (NIT 3)";
  a.set_regions(W, "chat", {approve()}, 2 * SHIELD_SECONDS + 0.7, SIDEBAR); /* Drawn again where it is now. */
  a.note_presented(W, 2 * SHIELD_SECONDS + 0.7);
  a.note_move(W, 140, 90, SIDEBAR, 3 * SHIELD_SECONDS + 0.8);
  a.note_move(W, 140, 110, SIDEBAR, 3 * SHIELD_SECONDS + 0.9);
  EXPECT_TRUE(a.note_press(W, 140, 110, SIDEBAR, 3 * SHIELD_SECONDS + 1.0)) << "no stale cover left";
}

TEST_F(AuthorityRoutingTest, AFreedRegionsSurfacesAreForgottenAndNeverReachedThroughAReusedPointer)
{
  /* NIT 3: the region a surface was drawn in is freed and its address reused by another region: the surface's
   * regions went at the draw that no longer showed the region, so input routed to the new one reaches none. */
  show();
  a.note_move(W, 140, 110, MAIN, SHIELD_SECONDS + 0.05);
  a.sync_covers(W, {}, {SIDEBAR}, SHIELD_SECONDS + 0.1); /* MAIN is gone. */
  a.sync_covers(W, {}, {MAIN, SIDEBAR}, SHIELD_SECONDS + 0.2); /* Its address reused. */
  EXPECT_FALSE(a.note_press(W, 140, 110, MAIN, SHIELD_SECONDS + 0.3));
  EXPECT_FALSE(a.take("angelo.approve", "d1", SHIELD_SECONDS + 0.4));
}

TEST_F(AuthorityRoutingTest, ATicketGoesWhenItsControlStartsAgain)
{
  /* NIT 2: a press Blender gave a window-level modal handler (a menu eating an outside click) minted Approve's ticket;
   * once what was over Approve goes, Approve starts again and the ticket goes with its aim, so a later press takes
   * nothing. */
  show();
  a.note_move(W, 140, 110, MAIN, SHIELD_SECONDS + 0.05);
  a.sync_covers(W, {{SIDEBAR, 120, 90, 120, 60, true}}, {MAIN, SIDEBAR}, SHIELD_SECONDS + 0.1);
  EXPECT_TRUE(a.note_press(W, 140, 110, MAIN, SHIELD_SECONDS + 0.2)) << "minted (the press went elsewhere)";
  a.sync_covers(W, {}, {MAIN}, SHIELD_SECONDS + 0.3); /* The sidebar hides: Approve starts again. */
  EXPECT_FALSE(a.take("angelo.approve", "d1", SHIELD_SECONDS + 0.4)) << "the ticket went with the restart";
  /* And on a focus change (the epoch's restart). */
  a.note_presented(W, SHIELD_SECONDS + 0.4);
  a.note_move(W, 140, 90, MAIN, 2 * SHIELD_SECONDS + 0.5);
  a.note_move(W, 140, 110, MAIN, 2 * SHIELD_SECONDS + 0.6);
  EXPECT_TRUE(a.note_press(W, 140, 110, MAIN, 2 * SHIELD_SECONDS + 0.7));
  a.note_release(W, 2 * SHIELD_SECONDS + 0.75);
  a.note_deactivate(W, 2 * SHIELD_SECONDS + 0.8);
  a.note_move(W, 140, 111, MAIN, 2 * SHIELD_SECONDS + 0.9); /* Any next call syncs the window. */
  EXPECT_FALSE(a.take("angelo.approve", "d1", 2 * SHIELD_SECONDS + 1.0));
}

TEST_F(AuthorityRoutingTest, ACanvasMenuOutsideAnyDrawSyncStaysUntilClosed)
{
  /* Without the window's regions (a caller that passes none), a canvas menu's cover is left to its surface. */
  show();
  a.surface_cover(W, "flowmenu", 120, 90, 120, 60, 0.5, MAIN);
  a.sync_covers(W, {}, 0.6);
  a.note_move(W, 140, 110, MAIN, SHIELD_SECONDS + 0.05);
  EXPECT_FALSE(a.note_press(W, 140, 110, MAIN, SHIELD_SECONDS + 0.1));
}

}  // namespace blender::angelo_guard::tests
