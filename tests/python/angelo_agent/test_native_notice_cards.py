# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Layout of the notice cards (card_kit, update_card, crash_card, whatsnew_card) on a fake view.

No Blender here: a fake view measures text at a fixed width per character so layout is
deterministic. Checked: every card fits its region at any size, every control sits inside its card and
none overlap, each control's four states reach the drawing plan, the right buttons exist in each
phase, and the dialog's actions do what they say (sending, declining, remembering). The real pixels are
checked by the GUI suite (``test_blender_update_crash``).
"""

import ast
import re
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
import crash_fixtures as cfx  # noqa: E402
import update_fixtures as ufx  # noqa: E402
from angelo_agent import (card_kit as kit, crash_card, crash_flow, crash_log, crash_report, crash_state, crash_upload,  # noqa: E402
                          release_notes, update_card, update_feed, whatsnew_card)


class FakeView:
    _font, _mono = 1, 2

    @staticmethod
    def measure(text, size, font):
        return len(text) * size * (0.60 if font == 2 else 0.52)

    def wrap(self, value, width, size, font):
        out, offset = [], 0
        for paragraph in value.split("\n"):
            line = ""
            for word in paragraph.split(" "):
                trial = (line + " " + word) if line else word
                if line and self.measure(trial, size, font) > width:
                    out.append((line + " ", offset))
                    offset += len(line) + 1
                    line = word
                else:
                    line = trial
            while self.measure(line, size, font) > width and len(line) > 1:   # A word longer than the line.
                cut = max(1, int(width / (size * (0.60 if font == 2 else 0.52))))
                out.append((line[:cut], offset))
                offset += cut
                line = line[cut:]
            out.append((line, offset))
            offset += len(line) + 1
        return tuple(out)


VIEW = FakeView()


def offer(**changes):
    seed, keys = ufx.new_key()
    document = ufx.payload(**changes)
    feed = update_feed.open_feed(ufx.envelope(document, seed), keys, datetime(2026, 10, 25, tzinfo=timezone.utc))
    return update_feed.offer_for(feed, "1.0.0", "macos-arm64")


def controls(plan):
    return [op for op in plan.ops if op[0] in {"button", "checkbox", "disclosure", "closebtn"}]


def labels(plan):
    return [op[2] for op in plan.ops if op[0] == "button"]


def texts(plan):
    return [op[1] for op in plan.ops if op[0] in {"text", "mono"}]


def overlap(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


class PlanChecks:
    def check_plan(self, plan, region):
        x, y, w, h = plan.card
        self.assertGreaterEqual(x, 0)
        self.assertLessEqual(x + w, region[0] + 1e-6)
        self.assertLessEqual(y + h, region[1] + 1e-6, "the card's top is outside the region")
        if plan.scale > 0.6001:   # At the smallest scale a card that cannot fit keeps its top in view and may run off the bottom.
            self.assertGreaterEqual(y, 0, "the card is taller than the region")
        for rect, action, _ in plan.hits:
            self.assertTrue(x - 1 <= rect[0] and rect[0] + rect[2] <= x + w + 1, (action, rect))
            self.assertTrue(y - 1 <= rect[1] and rect[1] + rect[3] <= y + h + 1, (action, rect))
        rects = [rect for rect, action, _ in plan.hits]
        for index, first in enumerate(rects):
            for second in rects[index + 1:]:
                self.assertFalse(overlap(first, second), (first, second))


def flat(plan):
    """All drawn text as one string (wrapped lines joined), for sentence checks."""
    return " ".join(line.strip() for line in texts(plan))


def state_of(op):
    return op[4] if op[0] in {"button", "disclosure"} else op[3]


class PaletteTests(unittest.TestCase):
    def test_the_palette_is_the_welcome_screens(self):
        tree = ast.parse((ROOT / "scripts/addons_core/angelo_agent/welcome_screen.py").read_text())
        welcome = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
                   if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Tuple)}
        for name in ("CARD", "PANEL", "PANEL_HOVER", "LINE", "TEXT", "MUTED", "TEAL", "TEAL_SOFT", "INK", "SCRIM"):
            self.assertEqual(getattr(kit, name), welcome[name], name)


class UpdateCardTests(unittest.TestCase, PlanChecks):
    REGION = (1147, 787)

    def card(self, **changes):
        return update_card.UpdateCard(offer(**changes), "1.0.0", lambda choice, selected: None)

    def plan(self, card=None, region=None, scale=1.0, **ui):
        region = region or self.REGION
        return (card or self.card()).layout(VIEW, region[0], region[1], scale, kit.UiState(**ui))

    def test_the_notice_says_what_is_available_and_offers_the_choices(self):
        plan = self.plan()
        self.assertIn("Angelo 1.1 is available", flat(plan))
        self.assertIn("You have Angelo 1.0.", flat(plan))
        self.assertEqual(labels(plan), ["Release notes", "Download", "Skip this version", "Later"])
        self.assertIn("Edits finish in fewer steps", flat(plan))
        self.check_plan(plan, self.REGION)
        x, y, w, h = plan.card
        self.assertAlmostEqual(x + w, self.REGION[0] - 20, delta=1)   # Bottom-right corner.
        self.assertAlmostEqual(y, 20, delta=1)

    def test_it_is_not_modal(self):
        plan = self.plan()
        self.assertFalse(update_card.UpdateCard.modal)
        self.assertNotIn("scrim", [op[0] for op in plan.ops])

    def test_a_critical_or_unsupported_update_cannot_be_skipped_and_says_so(self):
        document = ufx.payload()
        document["latest"]["critical"] = True
        seed, keys = ufx.new_key()
        feed = update_feed.open_feed(ufx.envelope(document, seed), keys, datetime(2026, 10, 25, tzinfo=timezone.utc))
        critical = update_feed.offer_for(feed, "1.0.0", "macos-arm64")
        plan = self.plan(update_card.UpdateCard(critical, "1.0.0", lambda *a: None))
        self.assertNotIn("Skip this version", labels(plan))
        self.assertIn("Angelo 1.1 is an important update", flat(plan))
        unsupported = offer(minimum_supported="1.0.1", sequence=4)
        plan = self.plan(update_card.UpdateCard(unsupported, "1.0.0", lambda *a: None))
        self.assertIn("Angelo 1.0 is no longer supported. Please update.", flat(plan))
        self.assertNotIn("Skip this version", labels(plan))

    def test_without_a_download_for_this_platform_the_notes_are_the_primary_action(self):
        selected = offer()
        selected = type(selected)(selected.release, None, False, False)
        plan = self.plan(update_card.UpdateCard(selected, "1.0.0", lambda *a: None))
        self.assertNotIn("Download", labels(plan))
        button = next(op for op in plan.ops if op[0] == "button" and op[2] == "Release notes")
        self.assertEqual(button[3], "primary")

    def test_every_control_has_four_distinct_states(self):
        base = {op[2]: op[4] for op in self.plan().ops if op[0] == "button"}
        self.assertEqual(set(base.values()), {(False, False, False, True)})
        key = ("download", None)
        states = {
            "hover": self.plan(hover=key), "pressed": self.plan(hover=key, pressed=key), "focus": self.plan(focus=key),
            "pressed_but_pointer_left": self.plan(pressed=key)}
        flags = {name: {op[2]: op[4] for op in plan.ops if op[0] == "button"}["Download"] for name, plan in states.items()}
        self.assertEqual(flags["hover"], (True, False, False, True))
        self.assertEqual(flags["pressed"], (True, True, False, True))
        self.assertEqual(flags["focus"], (False, False, True, True))
        self.assertEqual(flags["pressed_but_pointer_left"], (False, False, False, True))   # Released outside = not pressed.

    def test_it_fits_small_regions_and_large_scales(self):
        for region, scale in (((700, 520), 1.0), ((420, 300), 1.0), ((1147, 787), 2.0), ((400, 260), 2.0), ((1920, 1080), 1.0)):
            with self.subTest(region=region, scale=scale):
                plan = self.plan(region=region, scale=scale)
                self.check_plan(plan, region)
                self.assertGreaterEqual(plan.scale, 0.6)

    def test_text_never_leaves_the_card(self):
        plan = self.plan()
        x, y, w, h = plan.card
        for op in plan.ops:
            if op[0] == "text":
                self.assertLessEqual(op[2] + VIEW.measure(op[1], op[4], 1), x + w - 10, op[1])

    def test_actions(self):
        calls, opened = [], []
        selected = offer()
        card = update_card.UpdateCard(selected, "1.0.0", lambda choice, o: calls.append(choice))
        original, update_card.open_link = update_card.open_link, opened.append
        self.addCleanup(setattr, update_card, "open_link", original)
        closed = []
        host = SimpleNamespace(close=closed.append)
        card.act("notes", None, host)
        self.assertEqual((opened, closed), (["https://angelo.studio/releases#1.1.0"], []))
        card.act("download", None, host)
        self.assertEqual((opened[-1].endswith("macos-arm64.dmg"), calls, closed), (True, ["later"], ["download"]))
        card.act("skip", None, host)
        card.act("later", None, host)
        self.assertEqual((calls, closed[1:]), (["later", "skip", "later"], ["skip", "later"]))


class NoticeCardTests(unittest.TestCase, PlanChecks):
    def test_a_notice_with_two_buttons(self):
        events = []
        card = update_card.NoticeCard("alert", "Could not check for updates", "Angelo could not reach the update server.",
                                      [("close", "Close", "secondary"), ("retry", "Try Again", "primary")],
                                      on_action=lambda action, host: events.append(action), lifetime=5)
        plan = card.layout(VIEW, 1000, 700, 1.0, kit.UiState())
        self.check_plan(plan, (1000, 700))
        self.assertEqual(labels(plan), ["Close", "Try Again"])
        scheduled = []
        card.opened(SimpleNamespace(after=lambda seconds, function: scheduled.append(seconds)))
        self.assertEqual(scheduled, [5])
        closed = []
        card.act("retry", None, SimpleNamespace(close=closed.append))
        card.act("close", None, SimpleNamespace(close=closed.append))
        self.assertEqual((events, closed), (["retry", "close"], ["closed"]))


def make_flow(directory, endpoint="https://api.angelo.studio/v1/crash-reports", send=None):
    log = crash_log.parse(cfx.MAC, cfx.MAC_ROOTS)
    pending = crash_report.Pending(crash_state.fingerprint("angelo.crash.txt", 1, 1), "angelo.crash.txt", 1_760_000_000 * 10 ** 9, log)
    system = crash_report.system_info(("Apple", "Apple M4 Pro", "1.2", "METAL"), "Darwin", "arm64", "26.3")
    document = crash_report.build_document(log, system, crash_report.app_info("5.2.2 LTS", "311bbe4efa04", "2026-10-09"),
                                           pending.mtime_ns, datetime(2026, 10, 25, tzinfo=timezone.utc), "report-1")
    return crash_flow.CrashFlow(directory, pending, document, endpoint, send=send or (lambda *a: None))


class CrashCardTests(unittest.TestCase, PlanChecks):
    REGION = (1147, 787)

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.actions = SimpleNamespace(saved=0, problems=[], copied=[], finished_calls=0, stopped=0,
                                       save=lambda: setattr(self.actions, "saved", self.actions.saved + 1),
                                       open_problem=lambda url: self.actions.problems.append(url),
                                       copy=lambda text: self.actions.copied.append(text),
                                       finished=lambda: setattr(self.actions, "finished_calls", self.actions.finished_calls + 1),
                                       stop_automatic=lambda: setattr(self.actions, "stopped", self.actions.stopped + 1))

    def card(self, flow=None, **kwargs):
        return crash_card.CrashCard(flow or make_flow(self.folder.name, **kwargs), self.actions)

    def plan(self, card, scale=1.0, region=None, **ui):
        region = region or self.REGION
        return card.layout(VIEW, region[0], region[1], scale, kit.UiState(**ui))

    def host(self):
        closed = []
        return SimpleNamespace(close=closed.append, closed=closed, redraw=lambda: None, after=lambda s, f: None)

    def test_the_dialog_asks_with_the_wording_of_the_native_dialog(self):
        plan = self.plan(self.card())
        self.assertEqual(texts(plan)[0].strip(), "Angelo quit unexpectedly")
        self.assertEqual(labels(plan), ["Report on the website", "Save report…", "Don't send", "Send report"])
        self.assertTrue(plan.ops and plan.ops[0][0] == "scrim")   # Modal.
        self.assertEqual([op[2] for op in plan.ops if op[0] == "disclosure"], ["Show what will be sent"])
        self.assertEqual(len([op for op in plan.ops if op[0] == "checkbox"]), 1)
        self.check_plan(plan, self.REGION)
        self.assertEqual(plan.focus_order[0], ("toggle", None))
        self.assertEqual(plan.focus_order[-1], ("send", None))

    def test_the_card_centers_and_fits_any_region(self):
        card = self.card()
        for region, scale in (((1147, 787), 1.0), ((800, 600), 1.0), ((520, 400), 1.0), ((1147, 787), 2.0), ((360, 300), 1.5)):
            with self.subTest(region=region, scale=scale):
                plan = self.plan(card, scale, region)
                self.check_plan(plan, region)
                x, y, w, h = plan.card
                self.assertAlmostEqual(x + w / 2, region[0] / 2, delta=2)

    def test_without_an_endpoint_send_is_shown_disabled_and_nothing_can_send(self):
        sent = []
        card = self.card(endpoint="", send=lambda *a: sent.append(a))
        plan = self.plan(card)
        self.assertEqual(labels(plan), ["Report on the website", "Save report…", "Close", "Send report"])
        send = next(op for op in plan.ops if op[0] == "button" and op[2] == "Send report")
        self.assertEqual(send[4], (False, False, False, False))   # Disabled: never hover, pressed or focus.
        self.assertNotIn("send", [action for _, action, _ in plan.hits])
        self.assertNotIn(("send", None), plan.focus_order)
        self.assertEqual(self.plan(card, hover=("send", None), pressed=("send", None), focus=("send", None)).ops, plan.ops)
        self.assertEqual([op for op in plan.ops if op[0] == "checkbox"], [])
        self.assertIn("Sorry about that. Sending crash reports from Angelo is not available yet.", flat(plan))
        self.assertEqual([op[2] for op in plan.ops if op[0] == "disclosure"], ["Show the report"])
        scheduled = []
        host = SimpleNamespace(close=lambda how: None, redraw=lambda: None, after=lambda s, f: scheduled.append(f))
        card.act("send", None, host)
        self.assertEqual((card.flow.phase, sent, scheduled, card.flow.start_send()), (crash_flow.ASK, [], [], False))
        card.act("dismiss", None, self.host())
        card.closed("dismissed")
        self.assertEqual(crash_state.load(self.folder.name).status(card.flow.pending.fingerprint), "dismissed")

    def test_showing_what_will_be_sent_shows_exactly_the_sent_text(self):
        card = self.card()
        card.act("toggle", None, self.host())
        plan = self.plan(card)
        box = plan.scroll["report"]
        self.assertGreater(box["content"], box["visible"])
        shown = [op[1] for op in plan.ops if op[0] == "mono"]
        self.assertTrue(shown and all(line in card.flow.text for line in shown[:3]))
        self.assertIn("Copy report", labels(plan))
        card.act("copy", None, self.host())
        self.assertEqual(self.actions.copied, [card.flow.text])
        self.assertEqual(card.flow.text.encode("ascii"), card.flow.payload)

    def test_the_report_box_scrolls_to_its_ends_and_no_further(self):
        card = self.card()
        card.act("toggle", None, self.host())
        plan = self.plan(card)
        limit = plan.scroll["report"]["content"] - plan.scroll["report"]["visible"]
        self.assertEqual(kit.clamp_scroll(plan, "report", -50), 0.0)
        self.assertEqual(kit.clamp_scroll(plan, "report", 1e9), limit)
        end = self.plan(card, scroll={"report": 1e9})
        self.assertNotEqual([op[1] for op in end.ops if op[0] == "mono"], [op[1] for op in plan.ops if op[0] == "mono"])
        self.assertEqual(kit.scroll_box_at(plan, *[plan.scroll["report"]["rect"][0] + 20, plan.scroll["report"]["rect"][1] + 20]), "report")
        self.assertIsNone(kit.scroll_box_at(plan, 1, 1))

    def test_dont_send_with_remember_means_never_and_records_declined(self):
        card, host = self.card(), self.host()
        card.act("remember", None, host)
        card.act("decline", None, host)
        state = crash_state.load(self.folder.name)
        self.assertEqual((state.consent, state.status(card.flow.pending.fingerprint), host.closed), ("never", "declined", ["declined"]))
        card.closed("declined")
        self.assertEqual(self.actions.finished_calls, 1)

    def test_dont_send_without_remember_keeps_asking_next_time(self):
        card = self.card()
        card.act("decline", None, self.host())
        state = crash_state.load(self.folder.name)
        self.assertEqual((state.consent, state.status(card.flow.pending.fingerprint)), ("ask", "declined"))

    def test_escape_and_close_dismiss_without_sending(self):
        sent = []
        card = self.card(send=lambda *a: sent.append(a))
        host = self.host()
        card.act(card.cancel_action, None, host)
        self.assertEqual(host.closed, ["dismissed"])
        card.closed("dismissed")
        self.assertEqual((sent, crash_state.load(self.folder.name).status(card.flow.pending.fingerprint)), ([], "dismissed"))
        self.assertEqual(crash_state.load(self.folder.name).consent, "ask")

    def test_send_runs_the_upload_remembers_the_choice_and_thanks(self):
        sent = []

        def fake_send(url, payload, report_id):
            sent.append((url, payload, report_id))
            return crash_upload.Outcome(crash_upload.SENT, "http_201", report_id)

        card = self.card(send=fake_send)
        host = self.host()
        card.act("remember", None, host)
        card.act("send", None, host)
        self.assertEqual(card.flow.phase, crash_flow.SENDING)
        self.assertTrue(card.animated)
        plan = self.plan(card)
        self.assertEqual(labels(plan), [])
        self.assertIn("spinner", [op[0] for op in plan.ops])
        deadline = time.time() + 5
        while not card.flow.poll() and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(card.flow.phase, crash_flow.SENT)
        self.assertEqual(sent[0][1], card.flow.payload)
        self.assertEqual(crash_state.load(self.folder.name).consent, "always")
        plan = self.plan(card)
        self.assertIn("Thank you", flat(plan))
        self.assertIn("Report ID: report-1", [op[1] for op in plan.ops if op[0] == "mono"])
        self.assertEqual([op[2] for op in plan.ops if op[0] == "disclosure"], ["Show what was sent"])
        self.assertEqual(labels(plan), ["Report on the website", "Close"])
        self.assertIn("&report=report-1", card.flow.problem_url() + "&")
        card.closed("dismissed")
        self.assertEqual(crash_state.load(self.folder.name).status(card.flow.pending.fingerprint), "sent")
        self.assertEqual(crash_flow.last_sent(self.folder.name)["report_id"], "report-1")

    def test_a_failed_send_can_be_retried_or_saved_and_a_refusal_cannot(self):
        card = self.card(send=lambda *a: crash_upload.Outcome(crash_upload.OFFLINE, "offline"))
        card.act("send", None, self.host())
        while not card.flow.poll():
            time.sleep(0.01)
        plan = self.plan(card)
        self.assertEqual((card.flow.phase, labels(plan)), (crash_flow.FAILED, ["Close", "Save report…", "Try again"]))
        self.assertIn("The report could not be sent", flat(plan))
        refused = self.card(send=lambda *a: crash_upload.Outcome(crash_upload.REFUSED, "http_400"))
        refused.act("send", None, self.host())
        while not refused.flow.poll():
            time.sleep(0.01)
        self.assertEqual(labels(self.plan(refused)), ["Close", "Save report…"])
        card.closed("dismissed")
        self.assertEqual(crash_state.load(self.folder.name).status(card.flow.pending.fingerprint), "failed",
                         "a send that failed offline is offered again at the next launch")
        refused.closed("dismissed")
        self.assertEqual(crash_state.load(self.folder.name).status(refused.flow.pending.fingerprint), "dismissed")

    def test_a_busy_service_is_not_asked_again_at_once(self):
        clock = [1_800_000_000.0]
        flow = make_flow(self.folder.name, send=lambda *a: crash_upload.Outcome(crash_upload.RETRY, "http_429", retry_after=600))
        flow.clock = lambda: clock[0]
        card = self.card(flow)
        card.act("send", None, self.host())
        while not card.flow.poll():
            time.sleep(0.01)
        plan = self.plan(card)
        self.assertEqual(labels(plan), ["Close", "Save report…"], "no Try again while the service limits reports")
        self.assertIn("The report service is busy right now.", flat(plan))
        card.closed("dismissed")
        state = crash_state.load(self.folder.name)
        fingerprint = card.flow.pending.fingerprint
        self.assertEqual((state.status(fingerprint), state.handled[fingerprint]["not_before"]), ("failed", clock[0] + 600))
        self.assertTrue(state.held_back(fingerprint, clock[0] + 599))
        self.assertFalse(state.held_back(fingerprint, clock[0] + 601))

    def test_the_last_attempt_gives_up(self):
        flow = make_flow(self.folder.name, send=lambda *a: crash_upload.Outcome(crash_upload.OFFLINE, "offline"))
        state = crash_state.load(self.folder.name)
        for _ in range(crash_state.MAX_ATTEMPTS - 1):
            state.mark(flow.pending.fingerprint, crash_state.FAILED)
        crash_state.save(self.folder.name, state)
        flow.start_send()
        while not flow.poll():
            time.sleep(0.01)
        self.assertEqual(flow.finish(), crash_state.GAVE_UP)

    def test_save_and_report_a_problem(self):
        card, host = self.card(), self.host()
        card.act("save", None, host)
        card.act("problem", None, host)
        self.assertEqual(self.actions.saved, 1)
        url = self.actions.problems[0]
        self.assertTrue(url.startswith("https://angelo.studio/support/report-a-bug?"))
        self.assertIn("os=macOS+26.3", url)
        self.assertIn("gpu=Apple+M4+Pro", url)
        self.assertNotIn("report=", url)
        for private in cfx.CANARIES:
            self.assertNotIn(private.lower(), url.lower())
        target = Path(self.folder.name) / "report"
        written = card.flow.save_to(target)
        self.assertEqual((written.name, written.read_text().rstrip("\n")), ("report.json", card.flow.text))
        card.closed("dismissed")
        self.assertEqual(crash_state.load(self.folder.name).status(card.flow.pending.fingerprint), "saved")

    def test_every_control_has_hover_pressed_and_focus_states(self):
        card = self.card()
        for key in (("send", None), ("decline", None), ("save", None), ("problem", None), ("toggle", None),
                    ("remember", None)):
            with self.subTest(control=key):
                normal, hover = self.plan(card), self.plan(card, hover=key)
                pressed, focus = self.plan(card, hover=key, pressed=key), self.plan(card, focus=key)
                states = [[state_of(op) for op in p.ops if op[0] in {"button", "checkbox", "disclosure"}]
                          for p in (normal, hover, pressed, focus)]
                self.assertEqual(len({str(s) for s in states}), 4, key)

    def test_a_lost_view_keeps_the_crash_unrecorded_and_requeues(self):
        card = self.card()
        card.closed("lost")
        self.assertEqual(crash_state.load(self.folder.name).status(card.flow.pending.fingerprint), "")
        self.assertTrue(card.retry_when_lost)

    def test_review_shows_the_last_sent_report_and_can_stop_automatic_sending(self):
        flow = make_flow(self.folder.name, send=lambda *a, **k: crash_upload.Outcome(crash_upload.SENT, "ok", "report-1"))
        flow.start_send()
        while not flow.poll():
            time.sleep(0.01)
        review = crash_flow.CrashFlow.review(self.folder.name, crash_flow.last_sent(self.folder.name))
        card = self.card(review)
        plan = self.plan(card)
        self.assertEqual(labels(plan), ["Copy report", "Ask me each time", "Close"])
        self.assertIn("report", plan.scroll)
        self.assertIn("This is exactly what was sent", flat(plan))
        host = self.host()
        card.act("stop", None, host)
        self.assertEqual((self.actions.stopped, host.closed), (1, ["stopped"]))
        card.closed("stopped")
        self.assertEqual(crash_state.load(self.folder.name).handled, {})   # Looking at a sent report records nothing.


class WhatsNewCardTests(unittest.TestCase, PlanChecks):
    def test_the_card_shows_the_highlights_and_the_full_notes_on_request(self):
        notes = release_notes.for_version("1.0.0")
        opened, finished = [], []
        card = whatsnew_card.WhatsNewCard(notes, opened.append, lambda: finished.append(1))
        plan = card.layout(VIEW, 1147, 787, 1.0, kit.UiState())
        self.check_plan(plan, (1147, 787))
        self.assertIn("What's new in Angelo 1.0", flat(plan))
        self.assertEqual(labels(plan), ["Read the release notes online", "Got it"])
        self.assertIn(notes.highlights[0][:30], flat(plan))
        # 1.0.0 is dated (CHANGELOG.md): the card names the Blender base, never "Not released yet".
        self.assertIn("Based on Blender 5.2.2 LTS", flat(plan))
        self.assertNotIn("Not released yet", flat(plan))
        self.assertNotIn("notes", plan.scroll)
        host = SimpleNamespace(close=lambda how: None)
        card.act("toggle", None, host)
        plan = card.layout(VIEW, 1147, 787, 1.0, kit.UiState())
        self.assertIn("notes", plan.scroll)
        self.assertEqual(card.cancel_action, "close")
        self.assertEqual((card.default_action, card.outside_dismisses, card.modal), ("close", True, True))
        card.act("online", None, host)
        self.assertEqual(opened, ["https://angelo.studio/releases"])
        card.closed("closed")
        card.closed("lost")
        self.assertEqual(finished, [1])

    def test_the_full_notes_have_bright_headings_and_dotted_items_with_a_hanging_indent(self):
        notes = release_notes.for_version("1.0.0")
        blocks = whatsnew_card.full_notes(notes)
        self.assertEqual(blocks[0], ("heading", "New"))
        self.assertEqual([text for style, text in blocks if style == "heading"], [h for h, _ in notes.sections])
        card = whatsnew_card.WhatsNewCard(notes, lambda url: None, lambda: None)
        card.act("toggle", None, SimpleNamespace(close=lambda how: None))
        plan = card.layout(VIEW, 1147, 1400, 1.0, kit.UiState())
        box = plan.scroll["notes"]["rect"]
        start, end = (plan.ops.index(next(o for o in plan.ops if o[0] == kind)) for kind in ("clip", "unclip"))
        inside = [op for op in plan.ops[start:end] if op[0] in {"text", "shape"}]
        heading = next(op for op in inside if op[0] == "text" and op[1] == "New")
        item = next(op for op in inside if op[0] == "text" and op[1] != "New")
        self.assertEqual((heading[5], item[5]), (kit.TEXT, kit.MUTED))
        self.assertGreater(item[2], heading[2], "items hang inside the dot")
        dots = [op for op in inside if op[0] == "shape" and op[2] == kit.TEAL]
        self.assertTrue(dots and all(box[0] < op[1][0] < item[2] for op in dots))
        self.assertTrue(all(not text.startswith("•") for _, text in blocks), "the dot is drawn, not a glyph")

    def test_a_released_version_names_the_blender_it_is_built_on(self):
        notes = release_notes.for_version("1.0.0")
        released = type(notes)(**dict(notes.__dict__, date="2026-10-20"))
        plan = whatsnew_card.WhatsNewCard(released, lambda url: None, lambda: None).layout(VIEW, 1147, 787, 1.0, kit.UiState())
        self.assertIn("Based on Blender 5.2.2 LTS", flat(plan))
        self.assertNotIn("Not released yet", flat(plan))

    def test_it_fits_a_small_region(self):
        card = whatsnew_card.WhatsNewCard(release_notes.for_version("1.0.0"), lambda url: None, lambda: None)
        for region in ((500, 380), (1147, 787), (360, 300), (320, 200)):
            self.check_plan(card.layout(VIEW, region[0], region[1], 1.0, kit.UiState()), region)


class TranslationTests(unittest.TestCase):
    """Every interface word a card draws went through ``iface_``; only content is drawn as it is.

    Translation is replaced by a marker, every card is laid out in every phase, and what is left outside the
    markers must be content: the release notes, the report text and the feed's own highlights.
    """

    def setUp(self):
        from angelo_agent import ui_text
        marker = lambda message, context=None: "\u27e6" + message + "\u27e7"  # noqa: E731
        patcher = patch.dict(ui_text._pgettext, {"iface": marker, "tip": marker, "rpt": marker})
        patcher.start()
        self.addCleanup(patcher.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = folder.name

    def untranslated(self, plan, *content):
        drawn = " ".join([op[1] for op in plan.ops if op[0] in {"text", "mono"}] +
                         [op[2] for op in plan.ops if op[0] in {"button", "disclosure"}])
        words = set(" ".join(content).split())
        return [w for w in re.sub("\u27e6.*?\u27e7", " ", drawn, flags=re.S).split() if w not in words]

    def test_the_crash_dialog_in_every_phase(self):
        actions = SimpleNamespace(save=lambda: None, open_problem=lambda url: None, copy=lambda t: None,
                                  finished=lambda: None, stop_automatic=lambda: None)
        for endpoint in ("", "https://console.angelo.studio/api/v1/crash-reports"):
            flow = make_flow(self.folder, endpoint=endpoint)
            card = crash_card.CrashCard(flow, actions)
            card.expanded = True
            for phase in (crash_flow.ASK, crash_flow.SENDING, crash_flow.SENT, crash_flow.FAILED, crash_flow.REVIEW):
                flow.phase = phase
                with self.subTest(endpoint=endpoint, phase=phase):
                    plan = card.layout(VIEW, 1147, 1400, 1.0, kit.UiState())
                    self.assertEqual(self.untranslated(plan, flow.text), [])

    def test_the_update_notice_and_whats_new(self):
        selected = offer()
        plan = update_card.UpdateCard(selected, "1.0.0", lambda *a: None).layout(VIEW, 1147, 787, 1.0, kit.UiState())
        self.assertEqual(self.untranslated(plan, *selected.release.highlights), [])
        notes = release_notes.for_version("1.0.0")
        card = whatsnew_card.WhatsNewCard(notes, lambda url: None, lambda: None)
        card.expanded = True
        content = [notes.summary, *notes.highlights, *(h for h, _ in notes.sections), *(i for _, items in notes.sections for i in items)]
        self.assertEqual(self.untranslated(card.layout(VIEW, 1147, 1400, 1.0, kit.UiState()), *content), [])


if __name__ == "__main__":
    unittest.main()
