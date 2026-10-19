# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The update card through its phases (downloading, preparing, ready, restarting) and the save prompt.

Same fake view and plan checks as test_native_notice_cards: every phase fits its region at any scale, no two
controls overlap, Restart to Update is disabled with the reason while work runs, and the save prompt offers
Save and Restart, Don't Save and Cancel. Real pixels: the end-to-end update (updates_and_reports.rst).
"""

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
from test_native_notice_cards import VIEW, PlanChecks, flat, labels, offer  # noqa: E402
from angelo_agent import card_kit as kit, update_card  # noqa: E402

SIZES = ((1147, 787), (520, 380), (2560, 1440))


class Live:
    """What ``update_ready`` tells the card."""

    def __init__(self, phase="ready", done=0, total=0, wait="", why=""):
        self.state, self.done, self.total, self.wait, self.why, self.restarts = phase, done, total, wait, why, []

    def phase(self, selected):
        return self.state

    def progress(self, selected):
        return self.done, self.total

    def wait_reason(self):
        return self.wait

    def unsupported_reason(self, selected):
        return self.why if self.state == "notify" else ""

    def restart(self, selected, host):
        self.restarts.append(selected)


class Host:
    def __init__(self):
        self.closed = []

    def close(self, how="done"):
        self.closed.append(how)

    def redraw(self):
        pass


class PhaseTests(unittest.TestCase, PlanChecks):
    def card(self, live, **changes):
        self.decisions = []
        return update_card.UpdateCard(offer(**changes), "1.0.0", lambda c, o: self.decisions.append(c), live=live)

    def plan(self, card, region=(1147, 787), scale=1.0):
        return card.layout(VIEW, region[0], region[1], scale, kit.UiState())

    def test_every_phase_fits_every_region(self):
        for live in (Live("downloading", 120 * 10 ** 6, 412 * 10 ** 6), Live("preparing"), Live("ready"),
                     Live("ready", wait="agent"), Live("restarting"), Live("notify", why="permission")):
            for region in SIZES:
                for scale in (1.0, 2.0):
                    with self.subTest(phase=live.state, region=region, scale=scale):
                        self.check_plan(self.plan(self.card(live), region, scale), region)

    def test_downloading_shows_its_progress_and_never_installs(self):
        card = self.card(Live("downloading", 206 * 10 ** 6, 412 * 10 ** 6))
        plan = self.plan(card)
        self.assertIn("Downloading Angelo 1.1", flat(plan))
        self.assertIn("206 MB of 412 MB", flat(plan))
        self.assertEqual(labels(plan), ["Release notes", "Hide"])
        bars = [op for op in plan.ops if op[0] == "shape" and op[2] == kit.TEAL]
        track = [op for op in plan.ops if op[0] == "shape" and op[2] == kit.PANEL]
        self.assertAlmostEqual(bars[-1][1][2] / track[-1][1][2], 0.5, places=2)
        self.assertTrue(card.animated)

    def test_ready_offers_the_restart(self):
        live = Live("ready")
        card = self.card(live)
        plan = self.plan(card)
        self.assertIn("Angelo 1.1 is ready", flat(plan))
        self.assertIn("Your file opens again afterwards", flat(plan))
        self.assertEqual(labels(plan), ["Release notes", "Restart to Update", "Skip this version", "Later"])
        restart = next(op for op in plan.ops if op[0] == "button" and op[2] == "Restart to Update")
        self.assertEqual((restart[3], restart[4][3]), ("primary", True))
        card.act("restart", None, Host())
        self.assertEqual(len(live.restarts), 1)

    def test_while_work_runs_the_restart_waits_and_says_why(self):
        for reason in ("agent", "preview", "flow", "generation", "job", "render", "interaction", "unknown"):
            with self.subTest(reason=reason):
                plan = self.plan(self.card(Live("ready", wait=reason)))
                restart = next(op for op in plan.ops if op[0] == "button" and op[2] == "Restart to Update")
                self.assertFalse(restart[4][3], "disabled")
                self.assertNotIn("restart", [action for _, action, _ in plan.hits], "and not clickable")
                self.assertIn(update_card.WHY_WAIT[reason], flat(plan))

    def test_restarting_has_no_way_to_dismiss_the_card_halfway(self):
        card = self.card(Live("restarting"))
        plan = self.plan(card)
        self.assertEqual(labels(plan), [])
        host = Host()
        card.act("later", None, host)
        self.assertEqual((host.closed, self.decisions), ([], []))

    def test_an_install_that_cannot_update_itself_says_why_and_offers_the_download(self):
        for why in update_card.WHY_MANUAL:
            with self.subTest(why=why):
                plan = self.plan(self.card(Live("notify", why=why)))
                self.assertIn(update_card.WHY_MANUAL[why], flat(plan))
                self.assertIn("Download", labels(plan))

    def test_a_critical_ready_update_cannot_be_skipped(self):
        plan = self.plan(update_card.UpdateCard(offer(), "1.0.0", lambda c, o: None, live=Live("ready")))
        self.assertIn("Skip this version", labels(plan))
        critical = offer()
        critical = type(critical)(critical.release, critical.download, True, False)
        plan = self.plan(update_card.UpdateCard(critical, "1.0.0", lambda c, o: None, live=Live("ready")))
        self.assertNotIn("Skip this version", labels(plan))
        self.assertIn("Angelo 1.1 is an important update", flat(plan))

    def test_the_layout_changes_when_the_live_state_does(self):
        live = Live("downloading", 1, 100)
        card = self.card(live)
        first = card.version()
        live.done = 50
        self.assertNotEqual(first, card.version())
        live.state, live.wait = "ready", "agent"
        self.assertNotEqual(card.version(), first)


class SaveCardTests(unittest.TestCase, PlanChecks):
    def setUp(self):
        from notices_fakes import Notices   # update_restart is Blender-side: imported against stand-in modules.
        notices = Notices()
        self.restart = notices.__enter__().update_restart
        self.addCleanup(notices.__exit__, None, None, None)

    def plan(self, card, region=(1147, 787), scale=1.0):
        return card.layout(VIEW, region[0], region[1], scale, kit.UiState())

    def test_the_prompt_names_the_file_and_offers_three_choices(self):
        chosen = []
        card = self.restart.SaveCard("castle.blend", 2, chosen.append)
        plan = self.plan(card)
        self.assertIn("Save changes before restarting?", flat(plan))
        self.assertIn("castle.blend", flat(plan))
        self.assertIn("2 images have unsaved changes too.", flat(plan))
        self.assertEqual(labels(plan), ["Cancel", "Don't Save", "Save and Restart"])
        rows = {round(op[1][1]) for op in plan.ops if op[0] == "button"}
        self.assertEqual(len(rows), 1, "the three choices share one row")
        self.assertTrue(card.modal)
        for region in SIZES:
            self.check_plan(self.plan(card, region), region)
        host = Host()
        card.act("save", None, host)
        self.assertEqual((chosen, host.closed), (["save"], ["save"]))

    def test_an_untitled_file_and_escape(self):
        card = self.restart.SaveCard("", 0, lambda choice: None)
        self.assertIn("This file has not been saved yet.", flat(self.plan(card)))
        self.assertEqual(card.cancel_action, "cancel")


if __name__ == "__main__":
    unittest.main()
