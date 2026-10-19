# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A Codex turn: how long the host waits for it, and what a cut or resumed turn records as used.

The scripted app-server of ``test_native_codex_plan`` with the waiting limits
scaled down: a real Controller and CodexProvider, no model or process.
``test_native_turn_activity`` holds the Claude cases and the receipt rules.
"""

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import turn_activity
from angelo_agent.controller import ToolCall
from angelo_agent.protocol import RpcError
from test_native_codex_plan import PlanTurnFixture
from test_native_provider_usage import codex_breakdown, token_update


class CodexTurnTests(PlanTurnFixture):
    def limits(self, idle, stretch):
        for name, value in (("IDLE_SECONDS", idle), ("STRETCH_SECONDS", stretch), ("POLL_SECONDS", 0.01)):
            patcher = patch.object(turn_activity, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def reasoning(self, count, pause=0.02):
        def run():
            for index in range(count):
                self.p._event("item/reasoning/summaryTextDelta", {"threadId": "native-thread", "turnId": self.p.turn_id,
                                                                  "itemId": "r1", "delta": "step %d " % index})
                time.sleep(pause)
        return run

    def test_a_turn_that_keeps_reasoning_outlives_the_silence_limit(self):
        self.limits(idle=0.6, stretch=30)
        self.assertEqual(self.run_turns([("call", self.reasoning(45)),
                                         ("message", '{"reply": "Thought it through.", "operations": []}')]), "completed")

    def test_the_time_a_person_takes_over_an_approval_is_not_model_time(self):
        self.limits(idle=0.15, stretch=0.3)
        waiting = {"approval": False}
        self.context.tools_pending = lambda: waiting["approval"]

        def read_the_script():
            waiting["approval"] = True
            time.sleep(0.6)
            waiting["approval"] = False
        self.assertEqual(self.run_turns([("call", read_the_script),
                                         ("message", '{"reply": "Ran it.", "operations": []}')]), "completed")

    def test_a_silent_turn_is_given_up_and_reports_what_it_used(self):
        self.limits(idle=0.15, stretch=30)
        self.p._usage.thread_started("native-thread")

        def usage_then_silence():
            self.p._event("thread/tokenUsage/updated", token_update("native-thread", self.p.turn_id,
                                                                    codex_breakdown(9000, 8000, 40)))
            time.sleep(1)
        with self.assertRaisesRegex(RpcError, "Agent turn timed out: nothing came from the model"):
            self.run_turns([("call", usage_then_silence)])
        usage = self.c._request_metrics.snapshot()["usage"]
        self.assertEqual((usage["input_tokens"], usage["cached_input_tokens"], usage["output_tokens"],
                          usage["interrupted_turns"]), (9000, 8000, 40, 1))

    def test_a_stopped_turn_reports_what_it_used(self):
        self.p._usage.thread_started("native-thread")

        def stop():
            self.p._event("thread/tokenUsage/updated", token_update("native-thread", self.p.turn_id,
                                                                    codex_breakdown(9000, 8000, 40)))
            self.c.cancelled = True
        self.run_turns([("call", stop)])
        usage = self.c._request_metrics.snapshot()["usage"]
        self.assertEqual((usage["input_tokens"], usage["interrupted_turns"]), (9000, 1))

    def test_the_first_turn_after_a_resume_is_measured_when_codex_reported_the_threads_total_first(self):
        self.p._usage.thread_resumed("native-thread")
        self.p._event("thread/tokenUsage/updated", token_update("native-thread", "earlier", codex_breakdown(50000, 0, 900)))

        def answer():
            self.p._event("thread/tokenUsage/updated", token_update("native-thread", self.p.turn_id,
                                                                    codex_breakdown(59000, 8000, 940)))
        self.assertEqual(self.run_turns([("call", answer), ("message", '{"reply": "Done.", "operations": []}')]), "completed")
        usage = self.c._request_metrics.snapshot()["usage"]
        self.assertEqual((usage["input_tokens"], usage["cached_input_tokens"], usage["output_tokens"]), (9000, 8000, 40))

    def test_the_first_turn_after_a_resume_without_that_total_is_unmeasured_never_zero_or_the_threads_whole(self):
        self.p._usage.thread_resumed("native-thread")

        def answer():  # The first total Codex reports arrives inside the turn: its baseline is unknown.
            self.p._event("thread/tokenUsage/updated", token_update("native-thread", self.p.turn_id,
                                                                    codex_breakdown(59000, 8000, 940)))
        self.assertEqual(self.run_turns([("call", answer), ("message", '{"reply": "Done.", "operations": []}')]), "completed")
        usage = self.c._request_metrics.snapshot().get("usage", {})
        self.assertNotIn("input_tokens", usage)
        self.assertNotIn("output_tokens", usage)

    def test_pending_host_calls_are_this_requests_own(self):
        self.assertFalse(self.context.tools_pending())
        fence = self.context.request_fence
        self.c.pending["x"] = ToolCall("x", "angelo_inspect_scene", {}, self.c.document, epoch=fence.epoch,
                                       generation=fence.generation - 1)
        self.assertFalse(self.context.tools_pending(), "An earlier request's call")
        self.c.pending["y"] = ToolCall("y", "angelo_inspect_scene", {}, self.c.document, epoch=fence.epoch,
                                       generation=fence.generation)
        self.assertTrue(self.context.tools_pending())
        self.c.pending.clear()


if __name__ == "__main__":
    unittest.main()
