# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A throttled live display never ends on a stale value (live_throttle, claude_stream, codex_plan).

The plan's status and steps, a script's newest lines and a thinking summary redraw at most twice a second as a
model streams. A change inside that window used to be dropped when nothing followed it, so the live row kept the
last value shown before the window: the 1.0.8 timing sweep's batched footbridge at Opus pace ended on "Ar Rail"
instead of "Ar Rail ×2". Every case here runs on a fake clock, at several paces and on plans unrelated to the one
that exposed it. Dependency-free: no Claude or Codex process, Blender or model.
"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
import plan_progress_fixtures as fixtures  # noqa: E402
from angelo_agent import codex_plan, live_throttle, plan_progress, plan_steps  # noqa: E402
from angelo_agent.claude_stream import STATUS_PLAN, STATUS_THINKING, THINKING_SECONDS, RoundStream  # noqa: E402
from angelo_agent.live_throttle import Latest  # noqa: E402
from angelo_agent.plan_progress import PlanProgress  # noqa: E402
from angelo_agent.script_stream import ScriptText  # noqa: E402

# Characters per fragment and seconds between fragments: Opus 5.5's recorded plan pace (16 per 60 ms) and paces
# on either side of it, from one character at a time to fragments slower than the window.
PACES = ((16, .06), (5, .02), (1, .004), (40, .15), (64, .45), (200, .8))


def lamps():
    """A street of lamps and benches: a batched unit last, so its count completes in the plan's final characters."""
    lamp = fixtures.cube("Lamp", "l", [0, 0, 1.5], [.1, .1, 3])
    bench = fixtures.cube("Bench", "b", [1, 0, .3], [1.5, .4, .3])
    return {"operations": [fixtures.base.operation("angelo_scene_program", mode="APPLY", operations=[
        fixtures.cube("Street", "s", [0, 0, 0], [20, 4, .1]), lamp, bench,
        {"kind": "batch", "parameters": {"sources": ["$b"], "at": [[4, 0, .3], [8, 0, .3]]}},
        {"kind": "batch", "parameters": {"sources": ["$l"], "at": [[4, 0, 1.5], [8, 0, 1.5], [12, 0, 1.5]]}}])]}


def script_plan():
    """A plan whose script is its last step, its description named before its code."""
    return {"operations": [{"tool": "angelo_execute_python", "arguments": {
        "description": "Scatter rocks", "code": "\n".join("rock_%d = %d" % (n, n) for n in range(30))}}]}


PLANS = {"footbridge": fixtures.footbridge(), "batched footbridge": fixtures.batched_footbridge(),
         "Arabic batched footbridge": fixtures.batched_footbridge("Ar "), "lamps": lamps(), "script": script_plan()}


def whole(text):
    """The status that names the complete plan: what the row must end on."""
    progress = PlanProgress()
    progress.feed(text)
    return plan_progress.status(STATUS_PLAN, progress.detail(), plan_steps.encode(progress))


def fragments(text, chars):
    return [text[start:start + chars] for start in range(0, len(text), chars)]


def clocked(clock):
    return patch.object(live_throttle.time, "monotonic", side_effect=lambda: clock[0])


def statuses(emits):
    return [data["text"] for kind, data in emits if kind == "status"]


class LatestTests(unittest.TestCase):
    def test_the_first_change_shows_at_once_and_later_ones_wait_for_the_window(self):
        clock, display = [10.], Latest(.5, "idle")
        with clocked(clock):
            self.assertIsNone(display.offer("idle"), "No change, nothing to show")
            self.assertEqual(display.offer("a"), "a")
            clock[0] += .2
            self.assertIsNone(display.offer("b"))
            clock[0] += .3
            self.assertEqual(display.offer("c"), "c", "After the window the newest shows")
            self.assertIsNone(display.flush(), "Nothing held")

    def test_a_change_inside_the_window_is_held_never_dropped(self):
        clock, display = [10.], Latest(.5)
        with clocked(clock):
            display.offer("a")
            clock[0] += .1
            self.assertIsNone(display.offer("b"))
            self.assertIsNone(display.offer("c"))
            self.assertEqual(display.flush(), "c", "The newest held value")
            self.assertEqual(display.shown, "c")
            self.assertIsNone(display.flush(), "Once")

    def test_returning_to_what_shows_holds_nothing(self):
        clock, display = [10.], Latest(.5)
        with clocked(clock):
            display.offer("a")
            display.offer("b")
            self.assertIsNone(display.offer("a"))
            self.assertIsNone(display.flush())
            self.assertFalse(Latest(.5, "x").started)
            self.assertTrue(display.started)


class ClaudeFinalRowsTests(unittest.TestCase):
    def stream(self, text, chars, seconds):
        """A plan call streamed at a pace, its block stopped: (emits while it streamed, emits at its stop)."""
        stream, clock, writing = RoundStream(), [100.], []
        with clocked(clock):
            writing += stream.events({"type": "stream_event", "event": {
                "type": "message_start", "message": {"id": "m1", "usage": {}}}}, {})
            writing += stream.events({"type": "stream_event", "event": {"type": "content_block_start", "index": 1,
                "content_block": {"type": "tool_use", "name": "StructuredOutput", "id": "t1"}}}, {})
            for piece in fragments(text, chars):
                clock[0] += seconds
                writing += stream.events({"type": "stream_event", "event": {"type": "content_block_delta", "index": 1,
                    "delta": {"type": "input_json_delta", "partial_json": piece}}}, {})
            clock[0] += seconds
            stopped = stream.events({"type": "stream_event", "event": {"type": "content_block_stop", "index": 1}}, {})
        return writing, stopped

    def test_the_row_ends_on_the_whole_plan_at_every_pace(self):
        for name, plan in PLANS.items():
            text = json.dumps(plan, ensure_ascii=False)
            for chars, seconds in PACES:
                with self.subTest(plan=name, chars=chars, seconds=seconds):
                    writing, stopped = self.stream(text, chars, seconds)
                    shown = statuses(writing + stopped)
                    self.assertEqual(shown[-1], whole(text), "The rows of the whole plan, once its call is written")
                    during = len(fragments(text, chars)) * seconds
                    self.assertLessEqual(len(shown) - 2, during / THINKING_SECONDS + 2, "Still at most twice a second")
                    self.assertEqual(len(shown), len(set(shown)), "Only when what there is to show changed")

    def test_the_stop_adds_nothing_when_the_newest_already_shows(self):
        text = json.dumps(PLANS["footbridge"])
        writing, stopped = self.stream(text, len(text), .01)
        self.assertEqual(statuses(writing)[-1], whole(text))
        self.assertEqual(stopped, [])

    def test_a_cut_off_scripts_held_lines_show_at_its_stop(self):
        text = json.dumps(script_plan())
        cut = text.index("rock_20")
        writing, stopped = self.stream(text[:cut], 4, .01)
        shown = [data for kind, data in writing + stopped if kind == "script_preview"]
        scan = ScriptText()
        scan.feed(text[:cut])
        self.assertEqual(shown[-1], scan.preview(), "The newest lines, not the ones before the window")
        self.assertIn("rock_19 = 19", shown[-1]["text"])

    def test_held_thinking_text_shows_when_the_round_ends_without_its_block_stopping(self):
        stream, clock, emits = RoundStream(), [100.], []
        with clocked(clock):
            for event in ({"type": "message_start", "message": {"id": "m1", "usage": {}}},
                          {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}},
                          {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta",
                                                                                "thinking": "Lamps first."}}):
                emits += stream.events({"type": "stream_event", "event": event}, {})
            ended = stream.events({"type": "result", "subtype": "success"}, {})
        self.assertEqual([kind for kind, _ in ended], ["thinking", "thinking_completed", "status"])
        self.assertEqual(ended[0][1]["text"], "Lamps first.")
        self.assertEqual(ended[2][1]["text"], STATUS_THINKING)

    def test_a_retried_messages_open_thinking_ends_when_the_next_message_starts(self):
        stream, clock = RoundStream(), [100.]
        with clocked(clock):
            for event in ({"type": "message_start", "message": {"id": "m1", "usage": {}}},
                          {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}},
                          {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta",
                                                                                "thinking": "Benches."}}):
                stream.events({"type": "stream_event", "event": event}, {})
            restarted = stream.events({"type": "stream_event", "event": {
                "type": "message_start", "message": {"id": "m2", "usage": {}}}}, {})
        self.assertEqual([kind for kind, _ in restarted], ["thinking", "thinking_completed", "status"])


class CodexFinalRowsTests(unittest.TestCase):
    def test_no_held_status_outlives_the_message_at_any_pace(self):
        """Codex takes the rows away when the plan message completes, so a held change never stays on screen."""
        for name, plan in PLANS.items():
            text = json.dumps(plan, ensure_ascii=False)
            for chars, seconds in PACES:
                with self.subTest(plan=name, chars=chars, seconds=seconds):
                    turns, emitted, clock = codex_plan.PlanTurns(), [], [50.]

                    def emit(kind, data):
                        emitted.append((kind, data))
                    with clocked(clock):
                        for piece in fragments(text, chars):
                            clock[0] += seconds
                            turns.reply_delta("m1", piece, emit)
                        turns.message_completed("m1", text, emit)
                    shown = statuses(emitted)
                    self.assertEqual(shown[-1], STATUS_THINKING)
                    self.assertLessEqual(len(shown) - 1, len(fragments(text, chars)) * seconds / THINKING_SECONDS + 2)


if __name__ == "__main__":
    unittest.main()
