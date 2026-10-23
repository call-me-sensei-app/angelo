# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A Codex plan message that keeps streaming without finishing is cut (``plan_runaway``).

The recorded runaway streamed 64-80 deltas a second for 297 s inside one never-finished plan message
(Sol, early Windows 1.0.5 gate). Its text was not recorded, so the replays here cover the readings it
allows: whitespace without end, or text that keeps growing. The two healthy messages in
``plan_runaway_corpus.json`` are real: the largest plan of the saved runs and the one whose stream held a
3,015-character run of tabs and still finished. Both pass untouched at the shipped ceilings.
"""

import json
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
sys.path.insert(0, str(HERE))
from angelo_agent import codex_attempts, plan_runaway, turn_activity
from angelo_agent.plan_runaway import DURATION, SIZE, WHITESPACE, MessageWatch, TurnWatch
from angelo_agent.protocol import RpcError
from angelo_agent.ui_text import stored_recipe
from test_native_codex_plan import PlanTurnFixture, ScriptedAppServer

CORPUS = json.loads((HERE / "plan_runaway_corpus.json").read_text(encoding="utf-8"))
RATE = 70  # Deltas a second, as recorded (64-80).
PREFIX = '{"reply":"I\'ll scale \\u201cEval Cube\\u201d to exactly 4 \\u00d7 1 \\u00d7 3 m.","operations":['


def replay(text, chunk=4, rate=RATE):
    """Stream ``text`` through a TurnWatch as deltas of ``chunk`` characters at ``rate`` a second (simulated
    clock): ``(reason, deltas fed, seconds)``."""
    clock = [0.0]
    watch = TurnWatch(clock=lambda: clock[0])
    count = 0
    for start in range(0, len(text), chunk):
        watch.feed("msg-1", text[start:start + chunk])
        count += 1
        if watch.reason:
            break
        clock[0] += 1.0 / rate
    return watch.reason, count, clock[0]


def endless(filler, chunk, seconds):
    """A runaway after the reply: ``filler`` repeated for ``seconds`` at RATE deltas of ``chunk`` characters."""
    return PREFIX + filler * (int(seconds * RATE * chunk) // len(filler) + 1)


class CeilingTests(unittest.TestCase):
    def test_the_largest_healthy_plan_passes_untouched(self):
        text = CORPUS["largest_healthy_plan"]
        self.assertEqual(len(text.encode()), 9572)
        for chunk in (1, 4, 7, 64):
            self.assertIsNone(replay(text, chunk)[0])

    def test_the_plan_whose_stream_held_3015_tabs_and_finished_passes_untouched(self):
        text = CORPUS["whitespace_run_plan"]
        self.assertIn("\t" * 3000, text)
        self.assertIsNone(replay(text, 1)[0])  # Even one character a delta: 3,015 blank deltas (the real run took <= 671).
        self.assertIsNone(replay(text, 16)[0])

    def test_every_ceiling_is_at_least_ten_times_its_measured_maximum(self):
        self.assertGreaterEqual(plan_runaway.MESSAGE_BYTES, 10 * 9572)
        self.assertGreaterEqual(plan_runaway.WHITESPACE_CHARS, 10 * 3015)
        self.assertGreaterEqual(plan_runaway.WHITESPACE_DELTAS, 10 * 671)  # That run's whole message: 671 tokens.
        self.assertGreaterEqual(plan_runaway.MESSAGE_SECONDS, 10 * 41)

    def test_a_whitespace_runaway_is_cut_within_two_minutes_whatever_its_token_width(self):
        for filler, chunk in ((" ", 1), ("\n", 1), ("  ", 2), ("\t" * 16, 16), ("\n  ", 3), (" " * 64, 64)):
            reason, _, seconds = replay(endless(filler, chunk, 900), chunk)
            self.assertEqual(reason, WHITESPACE, repr(filler))
            self.assertLessEqual(seconds, plan_runaway.WHITESPACE_DELTAS / RATE + 3, repr(filler))  # + the reply

    def test_one_non_blank_delta_restarts_the_delta_count(self):
        with patch.object(plan_runaway, "WHITESPACE_CHARS", 10 ** 9):
            watch = MessageWatch(0.0)
            watch.feed("{", 0.0)
            for text in [" "] * (plan_runaway.WHITESPACE_DELTAS - 1) + ['"a":1,'] + [" "] * (plan_runaway.WHITESPACE_DELTAS - 1):
                self.assertIsNone(watch.feed(text, 0.0))
            self.assertEqual(watch.feed(" ", 0.0), WHITESPACE)

    def test_a_runaway_that_keeps_writing_text_is_cut_by_size_or_time(self):
        reason, _, seconds = replay(endless('{"tool":"angelo_set_transform","arguments":{}},', 4, 900), 4)
        self.assertEqual(reason, DURATION)
        self.assertAlmostEqual(seconds, plan_runaway.MESSAGE_SECONDS, delta=1)
        reason, _, seconds = replay(endless("x" * 64, 64, 900), 64)  # Fast enough to pass the size first.
        self.assertEqual(reason, SIZE)
        self.assertLess(seconds, plan_runaway.MESSAGE_SECONDS)

    def test_the_recorded_runaway_would_have_ended_within_ten_minutes_whatever_it_wrote(self):
        # 20,790 deltas = 297 s at 70 a second, the recorded stream; it could not finish inside the ceiling.
        for filler in (" ", "0", "a", '\\"', "é"):
            reason, deltas, seconds = replay(endless(filler, 1, 900), 1)
            self.assertIsNotNone(reason, repr(filler))
            self.assertLessEqual(seconds, plan_runaway.MESSAGE_SECONDS + 1)  # Simulated clock sums floats.

    def test_whitespace_inside_strings_and_in_a_plain_answer_is_text(self):
        long_string = '{"reply":"' + " " * (plan_runaway.WHITESPACE_CHARS + 10) + '","operations":[]}'
        self.assertIsNone(replay(long_string, 64)[0])
        escaped = '{"reply":"a \\" ' + " " * (plan_runaway.WHITESPACE_CHARS + 10) + '","operations":[]}'
        self.assertIsNone(replay(escaped, 64)[0])
        plain = "Here is the layout:\n" + " " * (plan_runaway.WHITESPACE_CHARS + 10) + "done."
        self.assertIsNone(replay(plain, 64)[0])

    def test_leading_whitespace_counts_until_the_message_shows_what_it_is(self):
        watch = MessageWatch(0.0)
        self.assertEqual(watch.feed(" " * plan_runaway.WHITESPACE_CHARS, 1.0), WHITESPACE)

    def test_each_message_has_its_own_clock_and_the_first_ceiling_stands(self):
        clock = [0.0]
        watch = TurnWatch(clock=lambda: clock[0])
        watch.feed("a", "{")
        clock[0] = plan_runaway.MESSAGE_SECONDS - 1
        watch.feed("b", "{")  # A new message starts its own clock.
        clock[0] = plan_runaway.MESSAGE_SECONDS + 1
        watch.feed("b", '"')
        self.assertIsNone(watch.reason)
        watch.feed("a", "1")
        self.assertEqual(watch.reason, DURATION)
        watch.feed("b", " " * plan_runaway.WHITESPACE_CHARS)
        self.assertEqual(watch.reason, DURATION)

    def test_the_resend_note_states_facts_only(self):
        for reason in (WHITESPACE, SIZE, DURATION):
            text = plan_runaway.note(reason)
            self.assertTrue(text.startswith("[Host note]"))
            self.assertIn("nothing ran", text)


class RunawayAppServer(ScriptedAppServer):
    """A step ("runaway", filler) streams ``filler`` after a plan's reply until the host interrupts the turn."""

    def __init__(self, provider, turns):
        super().__init__(provider, turns)
        self.interrupted = threading.Event()

    def request(self, method, parameters, **kwargs):
        if method == "turn/interrupt":
            self.requests.append((method, parameters))
            self.interrupted.set()
            return {}
        if method == "turn/start":
            self.interrupted.clear()
        return super().request(method, parameters, **kwargs)

    def play(self, thread, turn, steps):
        if not steps or steps[0][0] not in ("runaway", "recorded"):
            return super().play(thread, turn, steps)
        deadline = time.monotonic() + 10
        while self.provider.turn_id != turn and time.monotonic() < deadline:
            time.sleep(0.001)
        ids = {"threadId": thread, "turnId": turn}
        self.provider._event("item/completed", dict(ids, item={"type": "userMessage", "id": "user-" + turn}))
        if steps[0][0] == "recorded":
            return super().play(thread, turn, steps[1:])
        for step in steps[1:]:
            if step[0] == "tool":
                self.provider._request("call-x", "item/tool/call", dict(ids, callId="call-x", tool=step[1],
                                                                        arguments=step[2]))
        item = "msg-%s" % turn
        self.provider._event("item/agentMessage/delta", dict(ids, itemId=item, delta=PREFIX))
        while not self.interrupted.wait(0.001) and time.monotonic() < deadline + 20:
            self.provider._event("item/agentMessage/delta", dict(ids, itemId=item, delta=steps[0][1]))
        self.provider._event("turn/completed", {"threadId": thread, "turn": {"id": turn, "status": "interrupted"}})


class CutTurnTests(PlanTurnFixture):
    def setUp(self):
        super().setUp()
        for target, name, value in ((plan_runaway, "WHITESPACE_CHARS", 400), (turn_activity, "POLL_SECONDS", 0.01),
                                    (codex_attempts, "CUT_SETTLE_SECONDS", 3.0)):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_turns(self, *turns):
        self.p.rpc = RunawayAppServer(self.p, turns)
        status = self.p.run("native-thread", "Scale 'Eval Cube' to 4 x 1 x 3 m")
        self.c.pump_events()
        return status

    def interrupts(self):
        return [parameters for method, parameters in self.p.rpc.requests if method == "turn/interrupt"]

    def test_a_runaway_with_nothing_at_the_host_is_stopped_and_resent_once(self):
        status = self.run_turns([("runaway", "  ")], [("recorded",), ("message", '{"reply": "Done.", "operations": []}')])
        self.assertEqual(status, "completed")
        self.assertEqual(len(self.interrupts()), 1)
        first, second = self.starts()
        self.assertEqual(second["input"], [{"type": "text", "text": plan_runaway.note(WHITESPACE)}])
        self.assertIs(second["outputSchema"], first["outputSchema"])
        self.assertEqual(self.c._request_metrics.counts.get("provider_retries_runaway"), 1)
        self.assertEqual(self.c._request_metrics.snapshot()["usage"]["interrupted_turns"], 1)
        (notice,) = [row for row in self.c.state["messages"] if row.get("text") == plan_runaway.RESENT]
        self.assertEqual(stored_recipe(notice), [plan_runaway.RESENT, {}])
        self.assertFalse(self.dispatched, "nothing of the cut message ran")

    def test_a_runaway_after_a_tool_call_reached_the_host_fails_with_the_translatable_message(self):
        with self.assertRaises(RpcError) as raised:
            self.run_turns([("runaway", " "), ("tool", "angelo_scene_observe", {})])
        self.assertEqual(str(raised.exception), plan_runaway.FAILED)
        self.assertEqual(raised.exception.i18n, [plan_runaway.FAILED, {}])
        self.assertEqual(len(self.starts()), 1, "never resent")
        self.assertEqual(len(self.interrupts()), 1)

    def test_a_second_runaway_fails_after_the_one_resend(self):
        with self.assertRaisesRegex(RpcError, "kept streaming without finishing"):
            self.run_turns([("runaway", " ")], [("runaway", "\n")])
        self.assertEqual(len(self.starts()), 2)
        self.assertEqual(len(self.interrupts()), 2)

    def test_a_long_runaway_of_text_is_cut_by_time(self):
        with patch.object(plan_runaway, "MESSAGE_SECONDS", 0.3):
            status = self.run_turns([("runaway", '{"kind":"x"},')],
                                    [("recorded",), ("message", '{"reply": "Done.", "operations": []}')])
        self.assertEqual(status, "completed")
        self.assertEqual(self.starts()[1]["input"][0]["text"], plan_runaway.note(DURATION))

    def test_stop_while_a_cut_turn_ends_counts_it_once(self):
        server = RunawayAppServer(self.p, [[("runaway", " ")]])
        original = server.request

        def request(method, parameters, **kwargs):
            if method == "turn/interrupt":
                self.c.cancelled = True  # The person presses Stop as the host cuts the turn.
            return original(method, parameters, **kwargs)
        server.request = request
        self.p.rpc = server
        self.assertEqual(self.p.run("native-thread", "Scale it"), "interrupted")
        self.c.pump_events()
        self.assertEqual(self.c._request_metrics.snapshot()["usage"]["interrupted_turns"], 1)
        self.assertEqual(len(self.starts()), 1)

    def test_healthy_turns_are_never_interrupted(self):
        self.assertEqual(self.run_turns([("message", '{"reply": "Fine.", "operations": []}')]), "completed")
        self.assertEqual(self.interrupts(), [])

    def test_the_largest_healthy_plans_stream_through_the_plan_turns_untouched(self):
        from angelo_agent.codex_plan import PlanTurns
        turns = PlanTurns()
        with patch.object(plan_runaway, "WHITESPACE_CHARS", 32 * 1024):
            for text in (CORPUS["largest_healthy_plan"], CORPUS["whitespace_run_plan"]):
                turns.begin_turn()
                for start in range(0, len(text), 7):
                    turns.reply_delta("msg", text[start:start + 7])
                self.assertIsNone(turns.watch.reason)

if __name__ == "__main__":
    unittest.main()
