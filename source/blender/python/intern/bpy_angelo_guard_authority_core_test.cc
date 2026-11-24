/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * The authority's tickets and ledger (`bpy_angelo_guard_authority_core.hh`), with the clock in the
 * test's hands. The window manager's calls (`note_*`) and the Python module are covered by the
 * background and GUI suites (`test_blender_guard_authority*`); here is everything they rely on.
 */

#include "testing/testing.h"

#include <limits>
#include <thread>

#include "bpy_angelo_guard_authority_core.hh"

namespace blender::angelo_guard::tests {

static constexpr WindowKey W = 0x1000;
static constexpr WindowKey OTHER = 0x2000;

static RegionSpec approve(int x = 100, int y = 100, const char *digest = "d1")
{
  RegionSpec region;
  region.x = x;
  region.y = y;
  region.width = 80;
  region.height = 24;
  region.idname = "angelo.approve";
  region.digest = digest;
  return region;
}

class AuthorityTest : public testing::Test {
 protected:
  Authority a;

  /** Drawn at t=0 under nothing; the pointer below it, then into it once the shield passed. */
  void arm(WindowKey window = W, double at = SHIELD_SECONDS + 0.1)
  {
    a.set_regions(window, "chat", {approve()}, 0.0);
    a.note_move(window, 140, 90, at - 0.05);
    a.note_move(window, 140, 110, at);
  }
};

TEST_F(AuthorityTest, AnAimedPressMintsOneTicketForItsOwnIdnameAndDigest)
{
  arm();
  EXPECT_TRUE(a.note_press(W, 140, 110, 1.2));
  EXPECT_FALSE(a.take("angelo.approve", "other", 1.3));
  EXPECT_FALSE(a.take("angelo.external_app_connect", "d1", 1.3));
  EXPECT_TRUE(a.take("angelo.approve", "d1", 1.3));
  EXPECT_FALSE(a.take("angelo.approve", "d1", 1.3)) << "single use";
}

/* The 2.0.0 break-it review's MUST: a pointer code put on a control (Window.cursor_warp, any warp)
 * never aims the person's next click there, shielded or not, until it moves out and back in. */
TEST_F(AuthorityTest, APointerCodePutOnAControlClicksNothingUntilItLeaves)
{
  RegionSpec plain = approve(300, 300, "d2");
  plain.idname = "studio.generate"; /* Unshielded: a plain press would mint. */
  a.set_regions(W, "chat", {approve()}, 0.0);
  a.set_regions(W, "studio", {plain}, 0.0);
  a.note_move(W, 10, 10, 0.5);
  a.note_warp(W, 140, 110, SHIELD_SECONDS + 0.5);
  a.note_move(W, 141, 111, SHIELD_SECONDS + 0.6); /* The person's nudge, still inside. */
  EXPECT_FALSE(a.note_press(W, 141, 111, SHIELD_SECONDS + 0.7)) << "shielded: warped into";
  a.note_warp(W, 340, 310, SHIELD_SECONDS + 0.8);
  EXPECT_FALSE(a.note_press(W, 340, 310, SHIELD_SECONDS + 0.9)) << "unshielded: warped into";
  a.note_move(W, 341, 311, SHIELD_SECONDS + 1.0);
  EXPECT_FALSE(a.note_press(W, 341, 311, SHIELD_SECONDS + 1.1)) << "a nudge inside is no way in";
  EXPECT_FALSE(a.take("studio.generate", "d2", SHIELD_SECONDS + 1.2));
  a.note_move(W, 500, 500, SHIELD_SECONDS + 1.3); /* Out, then in by the person's own move. */
  a.note_move(W, 340, 310, SHIELD_SECONDS + 1.4);
  EXPECT_TRUE(a.note_press(W, 340, 310, SHIELD_SECONDS + 1.5));
  a.note_move(W, 140, 90, SHIELD_SECONDS + 1.6);
  a.note_move(W, 140, 110, SHIELD_SECONDS + 1.7);
  EXPECT_TRUE(a.note_press(W, 140, 110, SHIELD_SECONDS + 1.8));
}

TEST_F(AuthorityTest, AControlDrawnUnderAPointerCodePutThereIsWarpedInto)
{
  a.note_move(W, 10, 10, 0.1);
  a.note_warp(W, 340, 310, 0.2);
  RegionSpec plain = approve(300, 300, "d2");
  plain.idname = "studio.generate";
  a.set_regions(W, "studio", {plain}, 0.3); /* Opened under it after the warp. */
  EXPECT_FALSE(a.note_press(W, 340, 310, SHIELD_SECONDS + 0.5));
  a.note_move(W, 341, 310, SHIELD_SECONDS + 0.6); /* A twitch inside it. */
  plain.digest = "d3";
  a.set_regions(W, "studio", {plain}, SHIELD_SECONDS + 0.7); /* Redrawn there with new values. */
  EXPECT_FALSE(a.note_press(W, 341, 310, 2 * SHIELD_SECONDS + 1.0)) << "the same trap, redrawn";
  a.note_move(W, 10, 10, 2 * SHIELD_SECONDS + 1.1);
  a.note_move(W, 341, 310, 2 * SHIELD_SECONDS + 1.2); /* Out and back in by the person. */
  EXPECT_TRUE(a.note_press(W, 341, 310, 2 * SHIELD_SECONDS + 1.3));
}

TEST_F(AuthorityTest, AGestureHoldLastsSecondsAndGoesOnFocusLossOrAnAgentRun)
{
  a.hold_gesture("canvas.popup_slider", "k1", 1.0);
  EXPECT_FALSE(a.peek("canvas.popup_slider", "k1", 1.0 + TICKET_SECONDS + 0.1)) << "seconds";
  a.hold_gesture("canvas.popup_slider", "k2", 2.0);
  a.note_deactivate(OTHER, 2.1);
  EXPECT_FALSE(a.peek("canvas.popup_slider", "k2", 2.2)) << "any window losing focus ends it";
  a.hold_gesture("canvas.popup_slider", "k3", 3.0);
  a.note_agent_run();
  EXPECT_FALSE(a.peek("canvas.popup_slider", "k3", 3.1)) << "an agent run ends it";
  a.mint("angelo.trusted_scripts", "dialog", 4.0, DIALOG_SECONDS);
  a.note_deactivate(W, 4.1);
  EXPECT_TRUE(a.peek("angelo.trusted_scripts", "dialog", 4.2)) << "a dialog's is no gesture's";
}

/** SHOULD 3 of the review of d3024be: a drag's hold lasts while the person's button is down. The
 * release's own step still takes it (C notes the release before the canvas handles it), and any
 * pointer move after with no button down ends it, so moves after a release the canvas missed grant
 * nothing. */
TEST_F(AuthorityTest, AGestureHoldEndsWithThePersonsButton)
{
  a.note_press(W, 10, 10, 1.0); /* The person's press (on a slider): the button is down. */
  a.hold_gesture("canvas.popup_slider", "k1", 1.0);
  a.note_move(W, 20, 10, 1.1);
  EXPECT_TRUE(a.peek("canvas.popup_slider", "k1", 1.1)) << "dragging: it stands";
  a.note_release(W, 1.2);
  EXPECT_TRUE(a.peek("canvas.popup_slider", "k1", 1.2)) << "the release's own step";
  a.note_move(W, 30, 10, 1.3);
  EXPECT_FALSE(a.peek("canvas.popup_slider", "k1", 1.3)) << "a move with no button down";
  a.hold_gesture("canvas.popup_slider", "k2", 2.0); /* Held with no button down at all. */
  a.note_move(W, 40, 10, 2.1);
  EXPECT_FALSE(a.peek("canvas.popup_slider", "k2", 2.1));
  a.mint("angelo.trusted_scripts", "dialog", 3.0, DIALOG_SECONDS);
  a.note_move(W, 50, 10, 3.1);
  EXPECT_TRUE(a.peek("angelo.trusted_scripts", "dialog", 3.2)) << "only a gesture's ends";
}

/** The reviewer's read of f5604750e18, (e): a dialog's held consent is the person's press for that
 * dialog, not for whatever an agent turn makes of it: any agent run ends it (a dialog left open
 * across a turn needs a fresh press), and it lasts what a confirm needs, a slow reader's too (the
 * read of 927cd1a81a4: agent changes end it, so the time only bounds an idle dialog), a file
 * browser's pick a few minutes. */
TEST_F(AuthorityTest, ADialogsHeldConsentEndsWithAnAgentRunAndLastsWhatAConfirmNeeds)
{
  EXPECT_GE(DIALOG_SECONDS, 120.0) << "a slow reader still confirms";
  EXPECT_LE(DIALOG_SECONDS, 180.0);
  EXPECT_LE(BROWSE_SECONDS, 300.0);
  a.mint("angelo.trusted_scripts", "ui", 0.0);
  ASSERT_TRUE(a.carry("angelo.trusted_scripts", "ui", "dialog:1", 0.1, false));
  a.note_agent_run();
  EXPECT_FALSE(a.peek("angelo.trusted_scripts", "dialog:1", 0.2)) << "an agent turn ended it";

  a.mint("angelo.trusted_scripts", "ui", 1.0);
  ASSERT_TRUE(a.carry("angelo.trusted_scripts", "ui", "dialog:2", 1.0, false));
  EXPECT_TRUE(a.peek("angelo.trusted_scripts", "dialog:2", 1.0 + DIALOG_SECONDS - 1.0));
  EXPECT_FALSE(a.peek("angelo.trusted_scripts", "dialog:2", 1.0 + DIALOG_SECONDS + 0.1));

  a.mint("angelo.godot_link_install", "ui", 2.0);
  ASSERT_TRUE(a.carry("angelo.godot_link_install", "ui", "dialog:3", 2.0, true)); /* A browser. */
  EXPECT_TRUE(a.peek("angelo.godot_link_install", "dialog:3", 2.0 + DIALOG_SECONDS + 1.0));
  EXPECT_FALSE(a.peek("angelo.godot_link_install", "dialog:3", 2.0 + BROWSE_SECONDS + 0.1));

  a.hold_dialog("angelo.flow_run", "dialog:4", 3.0, false); /* Held in the person's scope. */
  EXPECT_TRUE(a.peek("angelo.flow_run", "dialog:4", 3.1));
  a.note_agent_run();
  EXPECT_FALSE(a.peek("angelo.flow_run", "dialog:4", 3.2));
}

/** The review of a6edbf3, MUST: outside work landing (a generation's result, a job's end) ends a
 * dialog's held consent and a gesture's hold, and nothing else. The person's own move into a
 * control, and a press ticket they already minted, stay theirs: outside work synthesizes no input,
 * so a generation's results never keep the person from granting while it runs. */
TEST_F(AuthorityTest, OutsideWorkEndsHeldConsentButLeavesThePersonsInput)
{
  RegionSpec plain = approve(300, 300, "d2");
  plain.idname = "studio.generate";
  a.set_regions(W, "studio", {plain}, 0.0);
  a.note_move(W, 10, 10, 0.5);
  a.note_move(W, 340, 310, SHIELD_SECONDS + 1.0); /* The person's own move in. */
  a.hold_gesture("canvas.popup_slider", "k1", SHIELD_SECONDS + 1.0);
  a.mint("angelo.trusted_scripts", "ui", SHIELD_SECONDS + 1.0);
  ASSERT_TRUE(a.carry("angelo.trusted_scripts", "ui", "dialog:1", SHIELD_SECONDS + 1.0, false));
  a.mint("angelo.custom_model", "ui", SHIELD_SECONDS + 1.0);
  a.note_outside_work();
  EXPECT_FALSE(a.peek("canvas.popup_slider", "k1", SHIELD_SECONDS + 1.1)) << "a gesture's hold ends";
  EXPECT_FALSE(a.peek("angelo.trusted_scripts", "dialog:1", SHIELD_SECONDS + 1.1)) << "a dialog's";
  EXPECT_TRUE(a.peek("angelo.custom_model", "ui", SHIELD_SECONDS + 1.1)) << "a press stays theirs";
  EXPECT_TRUE(a.note_press(W, 340, 310, SHIELD_SECONDS + 1.2)) << "their move still counts";
  a.open_surface(W, "chat:1");
  a.note_outside_work();
  EXPECT_TRUE(a.focus(W, "chat:1", "angelo.pick", "x1", SHIELD_SECONDS + 1.3))
      << "a popover the person opened stays theirs";
  a.note_agent_run(); /* Agent code may have synthesized input: its run resets the person's state. */
  EXPECT_FALSE(a.focus(W, "chat:1", "angelo.pick", "x2", SHIELD_SECONDS + 1.4))
      << "after an agent run, it is no longer theirs";
}

TEST_F(AuthorityTest, NoTicketWithoutARegionAngeloDrew)
{
  /* A keymap item or a button a script drew: no region, so no press there mints anything. */
  a.note_move(W, 10, 10, 5.0);
  a.note_move(W, 20, 20, 5.1);
  EXPECT_FALSE(a.note_press(W, 20, 20, 5.2));
  EXPECT_FALSE(a.take("angelo.approve", "d1", 5.3));
}

TEST_F(AuthorityTest, APressWithinTheShieldMintsNothing)
{
  a.set_regions(W, "chat", {approve()}, 0.0);
  a.note_move(W, 140, 90, 0.2);
  a.note_move(W, 140, 110, 0.3); /* Into it during its shield: it aims, the press still waits. */
  EXPECT_FALSE(a.note_press(W, 140, 110, 0.8));
  EXPECT_FALSE(a.take("angelo.approve", "d1", 0.8));
}

/* 2.0.1 (the maintainer had to click Approve 2, then 5, then 6-8 times while an app drove the chat):
 * a person who moves onto Approve as soon as it shows and clicks once after its shield approves. */
TEST_F(AuthorityTest, AMoveOntoItDuringItsShieldThenOnePressAfterItMints)
{
  a.set_regions(W, "chat", {approve()}, 0.0);
  a.note_move(W, 140, 90, 0.2);
  a.note_move(W, 140, 110, 0.3);
  EXPECT_TRUE(a.note_press(W, 140, 110, 1.5));
  EXPECT_TRUE(a.take("angelo.approve", "d1", 1.5));
}

/* The card re-laid out on every streamed row, under the pointer that aimed at it: one press mints. */
TEST_F(AuthorityTest, AShieldedControlReLaidOutUnderItsAimedPointerKeepsItsShieldAndAim)
{
  arm();
  for (int step = 1; step <= 20; step++) { /* A row streams in every 50 ms; the card shifts 2 px. */
    const double at = 1.1 + 0.05 * step;
    a.set_regions(W, "chat", {approve(100, 100 + (step % 2) * 2)}, at);
  }
  EXPECT_TRUE(a.note_press(W, 140, 110, 2.2));
  EXPECT_TRUE(a.take("angelo.approve", "d1", 2.2));
}

TEST_F(AuthorityTest, AShieldedControlReLaidOutAwayFromThePointerNeedsAnAimAgain)
{
  arm();
  a.set_regions(W, "chat", {approve(100, 300)}, 1.15); /* Moved away: the pointer is not on it. */
  a.note_move(W, 140, 290, 1.2);
  a.note_move(W, 140, 310, 1.25); /* Into it: its shield stood all along, so this press mints. */
  EXPECT_TRUE(a.note_press(W, 140, 310, 1.3));
}

/* Two regions of one control (duplicates): the one drawn where it was keeps its shield and aim. */
TEST_F(AuthorityTest, DuplicateRegionsPreferTheOneDrawnWhereItWas)
{
  arm();
  a.set_regions(W, "chat", {approve(100, 300), approve()}, 1.15); /* A second copy elsewhere, listed first. */
  EXPECT_TRUE(a.note_press(W, 140, 110, 1.2)) << "the one where it was keeps its aim";
}

TEST_F(AuthorityTest, TheClickThatBringsTheWindowForwardAimsSoTheNextPressMints)
{
  arm();
  a.note_deactivate(W, 1.15);
  a.note_activate(W, 2.99);        /* The window manager's activation, then its synthetic move. */
  a.note_move(W, 140, 110, 2.99);
  EXPECT_FALSE(a.note_press(W, 140, 110, 3.0)) << "the click that brings the window forward";
  EXPECT_TRUE(a.press_activating(W));
  EXPECT_FALSE(a.note_press(W, 140, 110, 3.5)) << "its shield starts again at that click";
  EXPECT_FALSE(a.press_activating(W));
  EXPECT_TRUE(a.note_press(W, 140, 110, 4.1)) << "the next deliberate press, without a move";
}

/* The re-review of acd0d465ee9, SHOULD-1: Cmd-Tab, a Dock click or `open -a` brings the window
 * forward with no press. The person's first press later is an ordinary press: a resting pointer
 * never aims with it, however many presses follow (S1). */
TEST_F(AuthorityTest, AnActivationWithoutAPressLeavesNoWindowForwardClick)
{
  arm();
  a.note_deactivate(W, 1.15);
  a.note_activate(W, 5.0);
  a.note_move(W, 140, 110, 5.0); /* Its synthetic move. */
  EXPECT_FALSE(a.note_press(W, 140, 110, 7.0)) << "a press long after the activation";
  EXPECT_FALSE(a.press_activating(W));
  EXPECT_FALSE(a.note_press(W, 140, 110, 8.5)) << "never aimed by that press";
  a.note_move(W, 140, 90, 8.6);
  a.note_move(W, 140, 110, 8.7);
  EXPECT_TRUE(a.note_press(W, 140, 110, 8.8)) << "the person's move onto it, then a press";
}

TEST_F(AuthorityTest, AnyEventBeforeThePressMeansTheActivationHadNoPress)
{
  arm();
  a.note_deactivate(W, 1.15);
  a.note_activate(W, 5.0);
  a.note_move(W, 140, 110, 5.0);
  a.note_key(W, 65, 5.01); /* A key the person typed before clicking. */
  EXPECT_FALSE(a.note_press(W, 140, 110, 5.02));
  EXPECT_FALSE(a.press_activating(W)) << "a key came between: not the window-forward click";
  a.note_deactivate(W, 6.0);
  a.note_activate(W, 9.0);
  a.note_move(W, 140, 110, 9.0);
  a.note_move(W, 141, 110, 9.01); /* A real move came between. */
  a.note_press(W, 141, 110, 9.02);
  EXPECT_FALSE(a.press_activating(W)) << "a move came between";
}

/* Windows delivers pointer moves to a background window: they never take the window-forward click
 * away, so the click that brings it forward still never grants, aimed or not. */
TEST_F(AuthorityTest, MovesOverTheBackgroundWindowKeepTheWindowForwardClick)
{
  arm();
  a.note_deactivate(W, 1.15);
  a.note_move(W, 140, 90, 5.0); /* Over the background window, then onto the button. */
  a.note_move(W, 140, 110, 5.1);
  a.note_activate(W, 9.0);
  a.note_move(W, 140, 110, 9.0);
  EXPECT_FALSE(a.note_press(W, 140, 110, 9.01)) << "the click that brings it forward never grants";
  EXPECT_TRUE(a.press_activating(W));
}

TEST_F(AuthorityTest, ALatePressIsNotTheWindowForwardClick)
{
  arm();
  a.note_deactivate(W, 1.15);
  a.note_activate(W, 5.0);
  a.note_move(W, 140, 110, 5.0);
  a.note_press(W, 140, 110, 5.0 + ACTIVATION_PRESS_SECONDS + 0.1);
  EXPECT_FALSE(a.press_activating(W));
}

/* The activation's synthetic move lands where the pointer is now; if it moved while the window was
 * in the background, that is never the person's move into a control here. */
TEST_F(AuthorityTest, TheActivationsOwnMoveNeverAims)
{
  a.note_move(W, 10, 10, 0.0);
  a.set_regions(W, "chat", {approve()}, 0.0);
  a.note_deactivate(W, 0.5);
  a.note_activate(W, 3.0);
  a.note_move(W, 140, 110, 3.0); /* Onto the button, by the activation, not the person. */
  EXPECT_FALSE(a.note_press(W, 140, 110, 5.0));
  EXPECT_FALSE(a.note_press(W, 140, 110, 6.5));
}

/* SHOULD-3: a hand's jitter inside a shielded control, once its shield has passed, aims at it; a
 * pointer that never moved still never does (S1), and jitter during the shield does not. */
TEST_F(AuthorityTest, AMoveInsideAShieldedControlAfterItsShieldAimsAtIt)
{
  a.note_move(W, 140, 110, 0.0);
  a.set_regions(W, "chat", {approve()}, 0.0); /* Appears under the resting pointer. */
  a.note_move(W, 141, 110, 0.5);              /* Jitter within its shield. */
  EXPECT_FALSE(a.note_press(W, 141, 110, 1.5)) << "jitter during the shield does not aim";
  a.note_release(W, 1.55);
  EXPECT_FALSE(a.note_press(W, 141, 110, 2.6)) << "and a resting pointer still never grants";
  a.note_release(W, 2.65);
  a.note_move(W, 142, 111, 2.7); /* A real move inside it, after the shield. */
  EXPECT_TRUE(a.note_press(W, 142, 111, 2.8));
}

TEST_F(AuthorityTest, PressesFromAPointerRestingWhereTheRegionAppearedNeverMintUntilAMoveIntoIt)
{
  a.note_move(W, 140, 110, 0.0);
  a.set_regions(W, "chat", {approve()}, 0.0);
  a.note_move(W, 140, 110, 2.0); /* A refresh at the same place is no move. */
  EXPECT_FALSE(a.note_press(W, 140, 110, 2.3)) << "no press from a pointer resting where it appeared";
  EXPECT_FALSE(a.note_press(W, 140, 110, 2.4)) << "a double-click's second press never grants";
  /* A press never aims (the break-it review of ca6f5ff137d, S1): presses a second or more apart never mint. */
  EXPECT_FALSE(a.note_press(W, 140, 110, 3.4));
  EXPECT_FALSE(a.note_press(W, 140, 110, 4.6));
  a.note_move(W, 140, 160, 4.8); /* The person moves off it and back onto it. */
  a.note_move(W, 140, 110, 4.9);
  EXPECT_TRUE(a.note_press(W, 140, 110, 5.0)) << "one press after the move into it";
}

/* The review's S1: the person approves card A; card B appears in the same place under the resting pointer; two
 * presses a second or more apart, no move, never grant B. */
TEST_F(AuthorityTest, ASecondCardInTheFirstOnesPlaceTakesNoPressesWithoutAMove)
{
  arm();
  EXPECT_TRUE(a.note_press(W, 140, 110, 1.2)) << "card A";
  a.note_release(W, 1.25);
  const RegionSpec b = approve(100, 100, "card-b");
  a.set_regions(W, "chat", {b}, 1.3); /* Card B, same place: a new control under the resting pointer. */
  EXPECT_FALSE(a.note_press(W, 140, 110, 2.5));
  a.note_release(W, 2.55);
  EXPECT_FALSE(a.note_press(W, 140, 110, 3.7));
  a.note_release(W, 3.75);
  EXPECT_FALSE(a.take("angelo.approve", "card-b", 3.8));
  a.note_move(W, 140, 160, 3.9);
  a.note_move(W, 140, 110, 4.0);
  EXPECT_TRUE(a.note_press(W, 140, 110, 4.1)) << "card B after the person's move onto it";
}

/* The break-it review's M1, scenario 1: the person's pointer crossed Approve once and rests elsewhere;
 * streamed rows shift Approve under it. That is a control arriving under the pointer: no press grants. */
TEST_F(AuthorityTest, AnAimedControlShiftedUnderAPointerThatLeftItGrantsNothing)
{
  arm();                                      /* Aimed at 1.1. */
  a.note_move(W, 140, 160, 1.3);              /* The pointer leaves it and rests below. */
  a.set_regions(W, "chat", {approve(100, 150)}, 1.5); /* Rows push the card under the pointer. */
  EXPECT_FALSE(a.note_press(W, 140, 160, 1.6)) << "arrived under the pointer: a new control";
  EXPECT_FALSE(a.note_press(W, 140, 160, 1.69)) << "nor a double-click";
  EXPECT_FALSE(a.take("angelo.approve", "d1", 1.7));
}

/* Scenario 2: a never-aimed control jumps under the resting pointer; a double-click never grants. */
TEST_F(AuthorityTest, ANeverAimedControlJumpingUnderThePointerTakesNoPressUntilAMoveIntoIt)
{
  a.set_regions(W, "chat", {approve(100, 300)}, 0.0);
  a.note_move(W, 10, 10, 0.1);
  a.note_move(W, 140, 110, 0.2);              /* Rests here; the control is elsewhere. */
  a.set_regions(W, "chat", {approve(100, 100)}, 2.0); /* Jumps under it. */
  EXPECT_FALSE(a.note_press(W, 140, 110, 2.1)) << "within its new shield";
  EXPECT_FALSE(a.note_press(W, 140, 110, 3.2)) << "after its shield, no move into it";
  EXPECT_FALSE(a.note_press(W, 140, 110, 3.29)) << "a double-click's second press";
  EXPECT_FALSE(a.note_press(W, 140, 110, 4.3)) << "a press never aims (review S1)";
  a.note_move(W, 140, 160, 4.4);
  a.note_move(W, 140, 110, 4.5);
  EXPECT_TRUE(a.note_press(W, 140, 110, 4.6)) << "one press after the move into it";
}

TEST_F(AuthorityTest, TheFirstMoveAWindowReportsIsNeverAMoveIntoARegion)
{
  a.set_regions(W, "chat", {approve()}, 0.0);
  a.note_move(W, 140, 110, 2.0);
  EXPECT_FALSE(a.note_press(W, 140, 110, 2.1));
}

TEST_F(AuthorityTest, ARegionDrawnElsewhereKeepsItsShieldButNotItsAim)
{
  arm();
  a.set_regions(W, "chat", {approve(300, 300)}, 1.15);
  EXPECT_FALSE(a.note_press(W, 340, 310, 1.2));
  a.set_regions(W, "chat", {approve(300, 300)}, 1.3); /* Unchanged: keeps its shield's start. */
  a.note_move(W, 340, 290, 2.2);
  a.note_move(W, 340, 310, 2.25);
  EXPECT_TRUE(a.note_press(W, 340, 310, 2.3));
}

TEST_F(AuthorityTest, ARegionNoLongerDrawnMintsNothing)
{
  arm();
  a.set_regions(W, "chat", {}, 1.15);
  EXPECT_FALSE(a.note_press(W, 140, 110, 1.2));
}

TEST_F(AuthorityTest, LosingFocusDisarmsAndTheReturningPressMintsNothing)
{
  arm();
  a.note_deactivate(W, 1.15);
  a.note_activate(W, 1.19);
  a.note_move(W, 140, 110, 1.19);
  EXPECT_FALSE(a.note_press(W, 140, 110, 1.2)) << "the click that brings the window forward";
  a.note_move(W, 140, 90, 2.5);
  a.note_move(W, 140, 110, 2.6);
  EXPECT_TRUE(a.note_press(W, 140, 110, 2.7)) << "aimed again after its shield";
}

static RegionSpec choice(const char *digest = "d1")
{
  RegionSpec region = approve(100, 100, digest);
  region.idname = "canvas.select_model";
  region.shield = false;
  return region;
}

TEST_F(AuthorityTest, AnUnshieldedRegionTakesAPressAfterAMoveButNeverTheReturningOne)
{
  a.note_move(W, 10, 10, 0.0);
  a.set_regions(W, "chat", {choice()}, 0.0);
  a.note_move(W, 140, 110, ARM_SECONDS + 0.05); /* Onto it once it has been there ARM_SECONDS. */
  EXPECT_TRUE(a.note_press(W, 140, 110, 0.5)) << "a menu's choice needs no 1 s shield";
  EXPECT_TRUE(a.take("canvas.select_model", "d1", 0.6));
  a.note_deactivate(W, 0.7);
  a.note_activate(W, 0.79);
  a.note_move(W, 140, 110, 0.79);
  EXPECT_FALSE(a.note_press(W, 140, 110, 0.8)) << "the click that brings the window forward";
  EXPECT_FALSE(a.note_press(W, 140, 110, 0.9)) << "back in focus, it starts again";
  a.note_move(W, 141, 110, 0.8 + ARM_SECONDS + 0.05);
  EXPECT_TRUE(a.note_press(W, 141, 110, 1.2));
}

/** SHOULD 2 of the reviewer's read of d3024be (the coordinator's ruling: ARM_SECONDS, approvals
 * keep the 1 s shield): any grant control mints only for a press after a real move inside it at
 * least ARM_SECONDS after it was drawn there, so content that re-lays a control under a resting
 * pointer, or under a moving one, grants nothing on the person's next click. */
TEST_F(AuthorityTest, ARestingPointerUnderASwappedInControlNeverMints)
{
  a.note_move(W, 10, 10, 0.0);
  a.note_move(W, 140, 110, 0.1); /* The pointer rests here; nothing is drawn under it yet. */
  a.set_regions(W, "studio", {choice()}, 1.0); /* Streamed content puts a control under it. */
  EXPECT_FALSE(a.note_press(W, 140, 110, 3.0)) << "no move since it was drawn";
}

TEST_F(AuthorityTest, AMoveWithinArmOfTheSwapMintsNothing)
{
  a.note_move(W, 10, 10, 0.0);
  a.set_regions(W, "studio", {choice()}, 1.0);
  a.note_move(W, 140, 110, 1.0 + ARM_SECONDS - 0.1); /* Onto it before it had been there ARM. */
  EXPECT_FALSE(a.note_press(W, 140, 110, 1.0 + ARM_SECONDS + 0.5)) << "a move too soon";
  a.note_move(W, 141, 110, 1.0 + ARM_SECONDS + 0.6); /* The person's next move, inside it. */
  EXPECT_TRUE(a.note_press(W, 141, 110, 1.0 + ARM_SECONDS + 0.7));
  a.set_regions(W, "studio", {choice("d2")}, 3.0); /* Another under the moving pointer. */
  a.note_move(W, 142, 110, 3.1);
  EXPECT_FALSE(a.note_press(W, 142, 110, 3.2)) << "the new one needs its own ARM";
}

/** Blender's own buttons have no region here: they ask, as they arm, whether the pointer is in
 * them only because code put it there (the warp, and every position since, inside them). */
TEST_F(AuthorityTest, WarpedIntoFollowsThePointersPathSinceTheWarp)
{
  a.note_move(W, 10, 10, 0.0);
  EXPECT_FALSE(a.warped_into(W, 100, 100, 80, 24)) << "no warp";
  a.note_warp(W, 140, 110, 1.0);
  EXPECT_TRUE(a.warped_into(W, 100, 100, 80, 24));
  a.note_move(W, 141, 111, 1.1); /* A twitch inside it. */
  EXPECT_TRUE(a.warped_into(W, 100, 100, 80, 24)) << "it never left";
  EXPECT_FALSE(a.warped_into(W, 300, 300, 80, 24)) << "another button";
  a.note_move(W, 60, 110, 1.2); /* Out of it, */
  a.note_move(W, 140, 110, 1.3); /* and back in by the person. */
  EXPECT_FALSE(a.warped_into(W, 100, 100, 80, 24));
  EXPECT_TRUE(a.moved_here_since(W, 140, 110, 1.3)) << "the person's move ended here";
  EXPECT_FALSE(a.moved_here_since(W, 140, 110, 1.4)) << "not since then";
  EXPECT_FALSE(a.moved_here_since(W, 141, 111, 1.0)) << "it ended elsewhere";
  a.note_move(W, 140, 110, 1.5); /* A refresh: no move. */
  EXPECT_FALSE(a.moved_here_since(W, 140, 110, 1.4));
  a.note_warp(W, 200, 200, 1.6);
  EXPECT_FALSE(a.moved_here_since(W, 200, 200, 1.0)) << "a warp is not the person's move";
}

TEST_F(AuthorityTest, TheOrdinaryOpenMoveClickFlowMints)
{
  a.note_move(W, 10, 10, 0.0);
  a.set_regions(W, "chat", {choice()}, 1.0); /* The person opened the picker. */
  a.note_move(W, 60, 90, 1.15);
  a.note_move(W, 140, 110, 1.0 + ARM_SECONDS + 0.05); /* Their hand reaches the row. */
  EXPECT_TRUE(a.note_press(W, 140, 110, 1.0 + ARM_SECONDS + 0.2));
}

TEST_F(AuthorityTest, WindowsAreIndependent)
{
  arm(W);
  a.set_regions(OTHER, "chat", {approve()}, 0.0);
  a.note_deactivate(OTHER, 1.0);
  EXPECT_FALSE(a.note_press(OTHER, 140, 110, 1.2));
  EXPECT_TRUE(a.note_press(W, 140, 110, 1.2));
}

TEST_F(AuthorityTest, ABlenderButtonsTicketIsItsOperatorsAndSingleUse)
{
  a.mint("angelo.set_approval", "ui", 1.0);
  EXPECT_FALSE(a.take("angelo.trusted_scripts", "ui", 1.1));
  EXPECT_TRUE(a.take("angelo.set_approval", "ui", 1.1));
  EXPECT_FALSE(a.take("angelo.set_approval", "ui", 1.1));
}

TEST_F(AuthorityTest, ATicketExpires)
{
  arm();
  ASSERT_TRUE(a.note_press(W, 140, 110, 1.2));
  EXPECT_FALSE(a.take("angelo.approve", "d1", 1.2 + TICKET_SECONDS + 0.01));
}

TEST_F(AuthorityTest, TicketsAreBoundedOldestFirst)
{
  arm();
  for (int i = 0; i < MAX_TICKETS + 1; i++) {
    a.note_move(W, 140, 90, 1.2 + i * 0.01);
    a.note_move(W, 140, 110, 1.2 + i * 0.01 + 0.001);
    ASSERT_TRUE(a.note_press(W, 140, 110, 1.2 + i * 0.01 + 0.002));
  }
  int taken = 0;
  while (a.take("angelo.approve", "d1", 1.5)) {
    taken++;
  }
  EXPECT_EQ(taken, MAX_TICKETS);
}

TEST_F(AuthorityTest, RegistrationIsBounded)
{
  std::vector<RegionSpec> many(MAX_REGIONS_PER_SURFACE + 1, approve());
  EXPECT_FALSE(a.set_regions(W, "chat", many, 0.0));
  RegionSpec long_name = approve();
  long_name.idname = std::string(MAX_TEXT + 1, 'x');
  EXPECT_FALSE(a.set_regions(W, "chat", {long_name}, 0.0));
  for (int i = 0; i < MAX_SURFACES; i++) {
    EXPECT_TRUE(a.set_regions(W, "s" + std::to_string(i), {approve()}, 0.0));
  }
  EXPECT_FALSE(a.set_regions(W, "one too many", {approve()}, 0.0));
  EXPECT_TRUE(a.set_regions(W, "s0", {}, 0.0)) << "a surface already there may always update";
}

TEST_F(AuthorityTest, AForgottenWindowKeepsNothing)
{
  arm();
  a.forget_window(W);
  EXPECT_FALSE(a.note_press(W, 140, 110, 1.2));
}

TEST_F(AuthorityTest, TheLedgerWidensOnlyInsideAGrantScopeAndAlwaysNarrows)
{
  EXPECT_TRUE(a.define("always_allow", Value(false)));
  EXPECT_FALSE(a.define("always_allow", Value(true))) << "defined once; its narrow value is fixed";
  EXPECT_EQ(a.set("always_allow", "on", Value(true), false), SetResult::Refused);
  EXPECT_FALSE(a.get("always_allow", "on").has_value());
  EXPECT_EQ(a.set("always_allow", "on", Value(true), true), SetResult::Set);
  EXPECT_EQ(std::get<bool>(*a.get("always_allow", "on")), true);
  EXPECT_EQ(a.set("always_allow", "on", Value(true), false), SetResult::Unchanged);
  EXPECT_EQ(a.set("always_allow", "on", Value(false), false), SetResult::Set) << "narrowing";
  EXPECT_EQ(a.set("missing", "on", Value(true), true), SetResult::UnknownNamespace);
}

TEST_F(AuthorityTest, RemovingAKeyAlwaysNarrows)
{
  a.define("approved_calls", Value());
  ASSERT_EQ(a.set("approved_calls", "chat/1", Value(std::string("digest")), true), SetResult::Set);
  EXPECT_TRUE(a.remove("approved_calls", "chat/1"));
  EXPECT_FALSE(a.get("approved_calls", "chat/1").has_value());
  EXPECT_FALSE(a.remove("approved_calls", "chat/1"));
}

TEST_F(AuthorityTest, AValueOfAnotherTypeThanTheNarrowOneIsWidening)
{
  a.define("approval", Value(std::string("ASK")));
  EXPECT_EQ(a.set("approval", "chat", Value(std::string("ASK")), false), SetResult::Set);
  EXPECT_EQ(a.set("approval", "chat", Value(std::string("AUTO_BOUNDED")), false),
            SetResult::Refused);
  EXPECT_EQ(a.set("approval", "chat", Value(int64_t(0)), false), SetResult::Refused);
}

TEST_F(AuthorityTest, TheLedgerIsBounded)
{
  a.define("many", Value(false));
  for (int i = 0; i < MAX_LEDGER_KEYS; i++) {
    ASSERT_EQ(a.set("many", std::to_string(i), Value(false), false), SetResult::Set);
  }
  EXPECT_EQ(a.set("many", "one more", Value(false), false), SetResult::Full);
  EXPECT_EQ(a.set("many", "0", Value(true), true), SetResult::Set) << "an existing key changes";
  EXPECT_EQ(a.set("many", std::string(MAX_TEXT + 1, 'k'), Value(false), false), SetResult::Full);
}

TEST_F(AuthorityTest, ItemsAreSortedByKey)
{
  a.define("trust", Value(false));
  a.set("trust", "b", Value(true), true);
  a.set("trust", "a", Value(true), true);
  const auto items = a.items("trust");
  ASSERT_EQ(items.size(), 2);
  EXPECT_EQ(items[0].first, "a");
  EXPECT_EQ(items[1].first, "b");
}

class AdmitTest : public AuthorityTest {
 protected:
  void SetUp() override
  {
    a.define_standard();
  }
};

TEST_F(AdmitTest, NothingIsAdmittedByDefault)
{
  EXPECT_FALSE(a.admits("chat1/call1", "aa"));
  EXPECT_FALSE(a.admits("", "aa"));
  EXPECT_FALSE(a.admits("chat1/call1", ""));
}

TEST_F(AdmitTest, AnApprovedScriptRunsOnceAndOnlyItsExactText)
{
  a.set(APPROVED_SCRIPTS, "chat1/call1", Value(std::string("aa")), true);
  EXPECT_FALSE(a.admits("chat1/call1", "bb")) << "another text";
  EXPECT_FALSE(a.admits("chat1/call2", "aa")) << "another call";
  EXPECT_TRUE(a.admits("chat1/call1", "aa"));
  EXPECT_FALSE(a.admits("chat1/call1", "aa")) << "consumed";
}

TEST_F(AdmitTest, AStandingConsentAloneAdmitsNothing)
{
  /* Angelo reads them and writes the approval when they cover a script; a script one must not
   * cover (it touches preferences or Angelo itself) still asks. */
  a.set(TRUSTED_CHATS, "chat1", Value(true), true);
  a.set(ALWAYS_ALLOW, "on", Value(true), true);
  a.set(CHAT_CLIENTS, "chat1", Value(std::string("cursor")), true);
  a.set(REVIEWED_SCRIPTS, "project/aa", Value(true), true);
  EXPECT_FALSE(a.admits("chat1/call1", "aa"));
}

TEST_F(AdmitTest, AContextFolderIsAttachedOnlyInAScope)
{
  /* Folder as context (2.1.0): chat id -> the digest of the folder's real path, narrowing to none. */
  EXPECT_FALSE(a.define(CONTEXT_FOLDERS, Value(std::string("aa")))) << "defined once, by C";
  EXPECT_EQ(a.set(CONTEXT_FOLDERS, "chat1", Value(std::string("aa")), false), SetResult::Refused);
  EXPECT_EQ(a.set(CONTEXT_FOLDERS, "chat1", Value(std::string("aa")), true), SetResult::Set);
  EXPECT_FALSE(a.admits("chat1/call1", "aa"));
  EXPECT_TRUE(a.remove(CONTEXT_FOLDERS, "chat1")) << "narrowing";
}

TEST_F(AdmitTest, AReviewedScriptIsRememberedOnlyInAScope)
{
  /* Always run this exact script in this project (2.1.0): a standard namespace, so it narrows to
   * false from start-up, before any add-on or script could define it otherwise. */
  EXPECT_FALSE(a.define(REVIEWED_SCRIPTS, Value(true))) << "defined once, by C";
  EXPECT_EQ(a.set(REVIEWED_SCRIPTS, "project/aa", Value(true), false), SetResult::Refused);
  EXPECT_EQ(a.set(REVIEWED_SCRIPTS, "project/aa", Value(true), true), SetResult::Set);
  EXPECT_EQ(a.set(REVIEWED_SCRIPTS, "project/aa", Value(false), false), SetResult::Set) << "narrowing";
}

TEST_F(AdmitTest, TheRulesAreDecidedOnlyInAScope)
{
  /* Standing rules (2.1.0): a text's digest -> Use (true) or Don't use (false), and the All projects
   * text's digest; Use and a text are set only in a scope, while Don't use and clearing narrow. */
  EXPECT_FALSE(a.define(RULE_DECISIONS, Value(true))) << "defined once, by C";
  EXPECT_FALSE(a.define(GLOBAL_RULES, Value(std::string("aa")))) << "defined once, by C";
  EXPECT_EQ(a.set(RULE_DECISIONS, "aa", Value(true), false), SetResult::Refused);
  EXPECT_EQ(a.set(RULE_DECISIONS, "aa", Value(true), true), SetResult::Set);
  EXPECT_EQ(a.set(RULE_DECISIONS, "aa", Value(false), false), SetResult::Set) << "narrowing";
  EXPECT_EQ(a.set(GLOBAL_RULES, "text", Value(std::string("aa")), false), SetResult::Refused);
  EXPECT_EQ(a.set(GLOBAL_RULES, "text", Value(std::string("aa")), true), SetResult::Set);
  EXPECT_FALSE(a.admits("chat1/call1", "aa"));
  EXPECT_TRUE(a.remove(GLOBAL_RULES, "text")) << "narrowing";
}

TEST_F(AdmitTest, NoneOfItIsWrittenWithoutAScope)
{
  EXPECT_EQ(a.set(TRUSTED_CHATS, "chat1", Value(true), false), SetResult::Refused);
  EXPECT_EQ(a.set(APPROVED_SCRIPTS, "chat1/c", Value(std::string("aa")), false), SetResult::Refused);
  EXPECT_EQ(a.set(ALWAYS_ALLOW, "on", Value(true), false), SetResult::Refused);
  EXPECT_FALSE(a.admits("chat1/c", "aa"));
}

class KeyTest : public AuthorityTest {
 protected:
  static constexpr int TAB = 100, RET = 101, SPACE = 102, PADENTER = 103, OTHER_KEY = 104,
                       RIGHT = 105, END = 106;
  void SetUp() override
  {
    KeyTypes types;
    types.tab = TAB;
    types.activate[0] = RET;
    types.activate[1] = SPACE;
    types.activate[2] = PADENTER;
    types.move[0] = RIGHT;
    types.move[1] = END;
    a.set_key_types(types);
  }
};

/* The coordinator's keyboard ruling (2026-11-16): keyboard grants keep what 1.x could do, inside a
 * popover or list the person opened. Its focus is theirs; an arrow or End on it (the effort
 * slider) and Return on its cursor row mint the focus's ticket after the shield. */
TEST_F(KeyTest, AMovementKeyInAPopoverThePersonOpenedMintsItsFocusTicket)
{
  a.open_surface(W, "chat:1");
  /* A slider: moving is its action (SHOULD 4 of the review of 9c8b4e57077). */
  EXPECT_TRUE(a.focus(W, "chat:1", "canvas.popup_slider", "s", 1.0, true)) << "the person's focus";
  a.note_key(W, RIGHT, 1.5);
  EXPECT_FALSE(a.take("canvas.popup_slider", "s", 1.5)) << "within the shield since focus arrived";
  a.note_key(W, END, 1.0 + SHIELD_SECONDS + 0.05);
  EXPECT_TRUE(a.take("canvas.popup_slider", "s", 2.1));
  EXPECT_TRUE(a.focus(W, "chat:1", "canvas.popup_select_model", "row2", 3.0)) << "cursor moved";
  a.note_key(W, RET, 3.0 + SHIELD_SECONDS + 0.05);
  EXPECT_TRUE(a.take("canvas.popup_select_model", "row2", 4.1));
}

TEST_F(KeyTest, APopoverThePersonDidNotOpenCountsNothing)
{
  EXPECT_FALSE(a.focus(W, "chat:1", "canvas.popup_slider", "s", 1.0)) << "code opened it";
  a.note_key(W, RIGHT, 3.0);
  EXPECT_FALSE(a.take("canvas.popup_slider", "s", 3.0));
  a.open_surface(W, "chat:2");
  EXPECT_FALSE(a.focus(W, "chat:1", "canvas.popup_slider", "s", 4.0)) << "another popover";
  a.note_key(W, TAB, 5.0);
  ASSERT_TRUE(a.focus(W, "chat:1", "canvas.approve", "d1", 5.1)) << "Tab-moved focus elsewhere";
  a.note_key(W, RIGHT, 5.1 + SHIELD_SECONDS + 0.05);
  EXPECT_FALSE(a.take("canvas.approve", "d1", 6.2)) << "arrows act only in the opened popover";
}

TEST_F(KeyTest, ClosingThePopoverOrAnAgentRunEndsIt)
{
  a.open_surface(W, "chat:1");
  ASSERT_TRUE(a.focus(W, "chat:1", "canvas.popup_slider", "s", 1.0, true));
  a.close_surface(W, "chat:2");
  a.note_key(W, RIGHT, 1.0 + SHIELD_SECONDS + 0.05);
  EXPECT_TRUE(a.take("canvas.popup_slider", "s", 2.1)) << "another surface's close: nothing";
  a.close_surface(W, "chat:1");
  a.note_key(W, RIGHT, 3.0);
  EXPECT_FALSE(a.take("canvas.popup_slider", "s", 3.0)) << "closed: its focus went with it";
  EXPECT_FALSE(a.focus(W, "chat:1", "canvas.popup_slider", "s", 4.0));

  a.open_surface(W, "chat:1");
  ASSERT_TRUE(a.focus(W, "chat:1", "canvas.popup_slider", "s", 5.0, true));
  a.note_agent_run();
  a.note_key(W, RIGHT, 5.0 + SHIELD_SECONDS + 0.05);
  EXPECT_FALSE(a.take("canvas.popup_slider", "s", 6.1)) << "opened before the script";
  EXPECT_FALSE(a.focus(W, "chat:1", "canvas.popup_slider", "s", 7.0));
}

/** Each surface reports its own focus as it draws: another surface's redraw with nothing focused
 * leaves the chat's Tab focus the person's (test_blender_connect_hero on Linux: the showcase drawn
 * beside the chat cleared it, and the person's Return was refused). Only the surface that holds the
 * focus clears it. */
TEST_F(KeyTest, AnotherSurfaceDrawnWithNoFocusLeavesTheFocusAlone)
{
  a.note_key(W, TAB, 1.0);
  ASSERT_TRUE(a.focus(W, "chat:1", "canvas.welcome_connect", "c", 1.1));
  EXPECT_FALSE(a.focus(W, "hero", "", "", 1.5)) << "nothing is focused there";
  EXPECT_TRUE(a.focus(W, "chat:1", "canvas.welcome_connect", "c", 1.6)) << "a redraw: it stands";
  a.note_key(W, RET, 1.1 + SHIELD_SECONDS + 0.05);
  EXPECT_TRUE(a.take("canvas.welcome_connect", "c", 2.2));
  EXPECT_FALSE(a.focus(W, "chat:1", "", "", 3.0)) << "the chat's own report of no focus";
  EXPECT_FALSE(a.focus(W, "chat:1", "canvas.welcome_connect", "c", 3.1)) << "cleared: code can't bring it back";
}

TEST_F(KeyTest, TabThenReturnAfterTheShieldMintsItsTicket)
{
  a.note_key(W, TAB, 1.0);
  EXPECT_TRUE(a.focus(W, "chat", "canvas.approve", "d1", 1.1));
  a.note_key(W, RET, 1.5);
  EXPECT_FALSE(a.take("canvas.approve", "d1", 1.5)) << "Return before the shield since focus arrived";
  a.note_key(W, RET, 1.1 + SHIELD_SECONDS + 0.05);
  EXPECT_TRUE(a.take("canvas.approve", "d1", 2.2));
  for (const int key : {SPACE, PADENTER}) {
    a.note_key(W, key, 3.0);
    EXPECT_TRUE(a.take("canvas.approve", "d1", 3.0)) << key;
  }
  a.note_key(W, OTHER_KEY, 3.1);
  EXPECT_FALSE(a.take("canvas.approve", "d1", 3.1)) << "only an activation key";
}

TEST_F(KeyTest, FocusSetByCodeIsNotThePersonsAndClearsFocus)
{
  EXPECT_FALSE(a.focus(W, "chat", "canvas.approve", "d1", 1.0)) << "no Tab, no press: code moved it";
  a.note_key(W, RET, 3.0);
  EXPECT_FALSE(a.take("canvas.approve", "d1", 3.0));
  a.note_key(W, TAB, 4.0);
  EXPECT_FALSE(a.focus(W, "chat", "canvas.approve", "d1", 4.0 + FOCUS_SECONDS + 0.1))
      << "a report long after the Tab is code's";
}

/* The review's SHOULD (4): an arrow mints only where moving is the action (a slider), never for a
 * list's cursor row (Return acts there). SHOULD (3): focus the person's own key just moved in
 * their popover is theirs at once, so Down, Down, Return within a second grants; focus that
 * arrived any other way waits the shield. NIT: another surface's control of the same digest is a
 * new focus. */
TEST_F(KeyTest, AnArrowMintsOnlyForAMovingControl)
{
  a.open_surface(W, "chat:1");
  ASSERT_TRUE(a.focus(W, "chat:1", "canvas.popup_select_model", "row1", 1.0));
  a.note_key(W, RIGHT, 1.0 + SHIELD_SECONDS + 0.05);
  EXPECT_FALSE(a.take("canvas.popup_select_model", "row1", 2.1)) << "a row: Return acts";
  ASSERT_TRUE(a.focus(W, "chat:1", "canvas.popup_slider", "s", 3.0, true));
  a.note_key(W, RIGHT, 3.0 + SHIELD_SECONDS + 0.05);
  EXPECT_TRUE(a.take("canvas.popup_slider", "s", 4.1)) << "a slider: the arrow is the action";
}

TEST_F(KeyTest, FocusThePersonsKeyMovedInTheirPopoverIsTheirsAtOnce)
{
  a.open_surface(W, "chat:1");
  ASSERT_TRUE(a.focus(W, "chat:1", "canvas.popup_select_model", "row1", 1.0));
  a.note_key(W, END, 1.1); /* Down… */
  ASSERT_TRUE(a.focus(W, "chat:1", "canvas.popup_select_model", "row2", 1.15));
  a.note_key(W, END, 1.2); /* …Down… */
  ASSERT_TRUE(a.focus(W, "chat:1", "canvas.popup_select_model", "row3", 1.25));
  a.note_key(W, RET, 1.3); /* …Return, well within a second. */
  EXPECT_TRUE(a.take("canvas.popup_select_model", "row3", 1.3));
  ASSERT_TRUE(a.focus(W, "chat:1", "canvas.popup_select_model", "row4", 3.0)) << "moved, no key";
  a.note_key(W, RET, 3.1);
  EXPECT_FALSE(a.take("canvas.popup_select_model", "row4", 3.1)) << "not moved by a key";
}

TEST_F(KeyTest, TheSameDigestOnAnotherSurfaceIsANewFocus)
{
  a.note_key(W, TAB, 1.0);
  ASSERT_TRUE(a.focus(W, "chat:1", "canvas.approve", "d1", 1.1));
  EXPECT_FALSE(a.focus(W, "chat:2", "canvas.approve", "d1", 5.0)) << "not the same focus";
}

/* The Fusion break-it review (2026-11-15, N4): an agent script that runs right after the person's Tab or press can
 * change the draw state a surface reports focus from; the next report, inside FOCUS_SECONDS, would be taken as the
 * person's. A script's run voids every Tab, press and focus before it. */
TEST_F(KeyTest, AnAgentRunVoidsTheTabAndPressBeforeIt)
{
  a.note_key(W, TAB, 1.0);
  a.note_agent_run();
  EXPECT_FALSE(a.focus(W, "chat", "canvas.approve", "d1", 1.1)) << "reported after a script, not the person's";
  a.note_key(W, RET, 1.1 + SHIELD_SECONDS + 0.05);
  EXPECT_FALSE(a.take("canvas.approve", "d1", 2.2));

  std::vector<RegionSpec> regions(1);
  regions[0] = {0, 0, 100, 40, "canvas.approve", "d1", false};
  ASSERT_TRUE(a.set_regions(W, "chat", regions, 3.0));
  a.note_move(W, 50, 60, 3.0);
  a.note_move(W, 10, 10, 3.0 + ARM_SECONDS + 0.05); /* The person's hand reaches it. */
  a.note_press(W, 10, 10, 3.4);
  ASSERT_TRUE(a.take("canvas.approve", "d1", 3.4)) << "the press's own ticket, bound to its control";
  a.note_agent_run();
  EXPECT_FALSE(a.focus(W, "chat", "canvas.approve", "d1", 3.5)) << "the press came before the script";

  a.note_key(W, TAB, 5.0);
  ASSERT_TRUE(a.focus(W, "chat", "canvas.approve", "d1", 5.1));
  a.note_agent_run();
  a.note_key(W, RET, 5.1 + SHIELD_SECONDS + 0.05);
  EXPECT_FALSE(a.take("canvas.approve", "d1", 6.2)) << "a focus the person had before the script is gone";

  a.note_key(W, TAB, 7.0);
  EXPECT_TRUE(a.focus(W, "chat", "canvas.approve", "d1", 7.1)) << "a Tab after the script is the person's";
}

TEST_F(KeyTest, TheSameFocusReportedAgainStands)
{
  a.note_key(W, TAB, 1.0);
  ASSERT_TRUE(a.focus(W, "chat", "canvas.approve", "d1", 1.1));
  EXPECT_TRUE(a.focus(W, "chat", "canvas.approve", "d1", 5.0)) << "a redraw reports it again";
  a.note_key(W, RET, 5.1);
  EXPECT_TRUE(a.take("canvas.approve", "d1", 5.1));
  EXPECT_FALSE(a.focus(W, "chat", "canvas.decline", "d1", 6.0)) << "moved by code to another control";
}

TEST_F(KeyTest, APressInsideTheControlGivesItFocus)
{
  a.set_regions(W, "chat", {approve()}, 0.0);
  a.note_press(W, 140, 110, 0.5); /* Within its shield: mints nothing, but the person pressed there. */
  EXPECT_TRUE(a.focus(W, "chat", "angelo.approve", "d1", 0.6));
  a.note_press(W, 10, 10, 2.0); /* Elsewhere: the surface's click ends keyboard focus (canvas_controls). */
  a.focus(W, "chat", "", "", 2.05);
  EXPECT_FALSE(a.focus(W, "chat", "angelo.approve", "d1", 2.1)) << "the press was not on it";
}

TEST_F(KeyTest, LosingFocusOrAnotherReportEndsIt)
{
  a.note_key(W, TAB, 1.0);
  ASSERT_TRUE(a.focus(W, "chat", "canvas.approve", "d1", 1.1));
  a.note_deactivate(W, 1.2);
  a.note_key(W, RET, 3.0);
  EXPECT_FALSE(a.take("canvas.approve", "d1", 3.0)) << "the window lost focus";
  a.note_key(W, TAB, 4.0);
  ASSERT_TRUE(a.focus(W, "chat", "canvas.approve", "d1", 4.1));
  a.focus(W, "chat", "", "", 4.2); /* Code clears it. */
  a.note_key(W, RET, 6.0);
  EXPECT_FALSE(a.take("canvas.approve", "d1", 6.0));
}

TEST_F(AuthorityTest, ADialogCarriesThePressToItsConfirmAndOnlyThatInstance)
{
  EXPECT_FALSE(a.carry("angelo.connection_signin", "ui", "dialog:1", 1.0)) << "no press";
  a.mint("angelo.connection_signin", "ui", 1.0);
  EXPECT_TRUE(a.carry("angelo.connection_signin", "ui", "dialog:1", 1.1));
  EXPECT_FALSE(a.take("angelo.connection_signin", "ui", 1.2)) << "the press ticket moved";
  const double later = 1.1 + DIALOG_SECONDS - 1.0; /* The person reads it, then confirms. */
  EXPECT_FALSE(a.take("angelo.connection_signin", "dialog:2", later)) << "another instance";
  EXPECT_TRUE(a.take("angelo.connection_signin", "dialog:1", later)) << "the person confirms later";
  a.mint("angelo.connection_signin", "ui", 300.0);
  a.carry("angelo.connection_signin", "ui", "dialog:3", 300.0);
  EXPECT_FALSE(a.take("angelo.connection_signin", "dialog:3", 300.0 + DIALOG_SECONDS + 1.0));
}

TEST_F(AuthorityTest, PeekLeavesTheTicket)
{
  a.mint("angelo.approve", "ui", 1.0);
  EXPECT_TRUE(a.peek("angelo.approve", "ui", 1.1));
  EXPECT_FALSE(a.peek("angelo.approve", "x", 1.1));
  EXPECT_TRUE(a.take("angelo.approve", "ui", 1.1));
  EXPECT_FALSE(a.peek("angelo.approve", "ui", 1.1));
}

TEST(AuthorityScope, AnAutomaticScopeAdmitsOnlyItsListedOperators)
{
  Authority a;
  a.allow_automatic("angelo.approve");
  EXPECT_FALSE(Scope::admits_operator(a, "angelo.approve")) << "no scope";
  {
    Scope automatic(Scope::AUTOMATIC);
    EXPECT_TRUE(Scope::admits_operator(a, "angelo.approve"));
    EXPECT_FALSE(Scope::admits_operator(a, "angelo.external_app_connect"));
    {
      Scope person(Scope::PERSON);
      EXPECT_TRUE(Scope::admits_operator(a, "angelo.external_app_connect"));
    }
    EXPECT_FALSE(Scope::admits_operator(a, "angelo.external_app_connect"));
  }
  {
    Scope harness(Scope::HARNESS);
    EXPECT_TRUE(Scope::admits_operator(a, "angelo.external_app_connect"));
  }
}

TEST(AuthorityScope, AScopeIsThisThreadsAndNests)
{
  EXPECT_FALSE(Scope::active());
  {
    Scope outer;
    EXPECT_TRUE(Scope::active());
    {
      Scope inner;
      EXPECT_TRUE(Scope::active());
    }
    EXPECT_TRUE(Scope::active());
    bool other = true;
    std::thread([&]() { other = Scope::active(); }).join();
    EXPECT_FALSE(other) << "a thread a scope starts is not inside it";
  }
  EXPECT_FALSE(Scope::active());
}

TEST(AuthorityDigest, HexIsLowerCaseAndComplete)
{
  uint8_t digest[32];
  for (int i = 0; i < 32; i++) {
    digest[i] = uint8_t(i * 8 + 7);
  }
  EXPECT_EQ(to_hex(digest), "070f171f272f373f474f575f676f777f878f979fa7afb7bfc7cfd7dfe7eff7ff");
}

TEST(AuthoritySingleton, OneAuthorityPerProcess)
{
  EXPECT_EQ(&authority(), &authority());
}

/** The coordinator's ruling on the 0.3 s arm, (b): a control's ARM_SECONDS count from the first
 * frame that showed it, not from when it was made, so a slow first draw takes none of the
 * person's time; and (a): a move inside it once that time has passed arms it, a hand's jitter
 * included, with no need to leave and come back. */
TEST_F(AuthorityTest, AControlArmsArmSecondsAfterTheFrameThatFirstShowedIt)
{
  RegionSpec plain = approve(300, 300, "d2");
  plain.idname = "studio.generate";
  plain.shield = false;
  a.note_presented(W, 0.5); /* The window shows frames. */
  a.note_move(W, 10, 10, 0.6);
  a.set_regions(W, "studio", {plain}, 1.0); /* Drawn in the frame on screen at 1.5. */
  a.note_presented(W, 1.5);
  a.note_move(W, 340, 310, 1.6);
  EXPECT_FALSE(a.note_press(W, 340, 310, 1.65)) << "on screen 0.15 s: not yet";
  a.note_move(W, 341, 310, 1.5 + ARM_SECONDS + 0.05); /* The hand's jitter, still inside. */
  EXPECT_TRUE(a.note_press(W, 341, 310, 1.5 + ARM_SECONDS + 0.1));
}

TEST_F(AuthorityTest, AControlDrawnInAFrameNotYetShownArmsNothing)
{
  RegionSpec plain = approve(300, 300, "d2");
  plain.idname = "studio.generate";
  plain.shield = false;
  a.note_presented(W, 0.5);
  a.note_move(W, 10, 10, 0.6);
  a.set_regions(W, "studio", {plain}, 1.0);
  a.note_move(W, 340, 310, 3.0); /* Seconds later, the frame still not presented. */
  EXPECT_FALSE(a.note_press(W, 340, 310, 3.1));
  EXPECT_EQ(a.shown(W, a.drawing_frame(W), 1.0), std::numeric_limits<double>::infinity());
  a.note_presented(W, 3.2);
  EXPECT_DOUBLE_EQ(a.shown(W, 1, 1.0), 3.2) << "frame 1 is the one presented at 3.2";
}

TEST_F(AuthorityTest, AFrameLongShownCountsAsShownLongAgoAndAWindowWithoutFramesUsesTheDrawTime)
{
  const uint64_t first = a.drawing_frame(W);
  for (int i = 0; i < 100; i++) {
    a.note_presented(W, 1.0 + i * 0.01);
  }
  EXPECT_LE(a.shown(W, first, 1.0), 1.0) << "older than the frames kept: shown long ago";
  EXPECT_DOUBLE_EQ(a.shown(OTHER, a.drawing_frame(OTHER), 2.0), 2.0) << "no frame presented yet";
}

/** (a): the person's press on a control not yet armed is recorded with the control, once, so the
 * guard refuses quietly and hints on the control instead of reporting an error. */
TEST_F(AuthorityTest, APressOnAControlNotYetArmedIsRecordedOnceWithTheControl)
{
  RegionSpec plain = approve(300, 300, "d2");
  plain.idname = "studio.generate";
  plain.shield = false;
  a.set_regions(W, "studio", {plain}, 0.0);
  a.note_move(W, 10, 10, 0.1);
  a.note_move(W, 340, 310, 0.15); /* Too soon after it was drawn. */
  EXPECT_FALSE(a.note_press(W, 340, 310, 0.2));
  EXPECT_FALSE(a.take_unarmed("studio.other", "d2", 0.3).has_value());
  EXPECT_FALSE(a.take_unarmed("studio.generate", "d3", 0.3).has_value());
  const std::optional<Authority::Unarmed> press = a.take_unarmed("studio.generate", "d2", 0.3);
  ASSERT_TRUE(press.has_value());
  EXPECT_EQ(press->window, W);
  EXPECT_EQ(press->x, 300);
  EXPECT_EQ(press->width, 80);
  EXPECT_FALSE(a.take_unarmed("studio.generate", "d2", 0.3).has_value()) << "once";
  EXPECT_FALSE(a.note_press(W, 340, 310, 0.35)); /* Still no move onto it since it armed. */
  EXPECT_FALSE(a.take_unarmed("studio.generate", "d2", 0.35 + UNARMED_SECONDS + 0.1).has_value())
      << "stale";
  EXPECT_FALSE(a.note_press(W, 340, 310, 1.6));
  a.note_move(W, 341, 310, 1.7); /* The jitter inside it arms it. */
  EXPECT_TRUE(a.note_press(W, 341, 310, 1.8));
  EXPECT_FALSE(a.take_unarmed("studio.generate", "d2", 1.9).has_value())
      << "an armed press supersedes the one before it";
}

TEST_F(AuthorityTest, ABlenderButtonsPressNotYetArmedIsRecordedForItsOperator)
{
  a.note_unarmed_button(W, "angelo.trusted_scripts", 10, 20, 200, 22, 5.0);
  EXPECT_FALSE(a.take_unarmed("angelo.approve", "", 5.1).has_value());
  const std::optional<Authority::Unarmed> press = a.take_unarmed(
      "angelo.trusted_scripts", "", 5.1);
  ASSERT_TRUE(press.has_value());
  EXPECT_EQ(press->y, 20);
  EXPECT_EQ(press->height, 22);
  EXPECT_FALSE(a.take_unarmed("angelo.trusted_scripts", "", 5.1).has_value());
}

}  // namespace blender::angelo_guard::tests
