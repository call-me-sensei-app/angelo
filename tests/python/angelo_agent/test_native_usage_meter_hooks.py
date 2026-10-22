# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where the usage meter reads its numbers: a finished request's receipt and the plan windows the runtimes push.

Offline fixtures: a Controller with a Codex provider over a recording RPC, and the Claude adapter over a
fake stream-json process. They prove the hooks, not a provider's reporting.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from claude_protocol_fixtures import ClaudeAdapterFixture, completed
import test_native_provider_usage as fixtures
from test_native_provider_usage import codex_breakdown
from angelo_agent import usage_meter, usage_windows


class CodexHookTests(unittest.TestCase):
    # The Codex usage suite's offline Controller and provider, without re-running its own tests.
    tearDown = fixtures.CodexProviderUsageTests.tearDown
    next_request, run_turn = fixtures.CodexProviderUsageTests.next_request, fixtures.CodexProviderUsageTests.run_turn

    def setUp(self):
        fixtures.CodexProviderUsageTests.setUp(self)
        usage_windows.clear()
        self.addCleanup(usage_windows.clear)

    def test_a_finished_request_lands_in_its_chats_meter(self):
        self.assertEqual(self.run_turn("turn-a", [codex_breakdown(12000, 4800, 260)]), "completed")
        self.c.emit("finished", {"status": "completed"})
        self.c.pump_events()
        chat = usage_meter.UsageLedger.shared(self.c.telemetry.directory).chat(self.c.state["id"])
        bucket = chat["modes"][self.c.state["provider"]]
        self.assertEqual((bucket["input"], bucket["cache_read"], bucket["output"], bucket["requests"]),
                         (7200, 4800, 260, 1))

    def test_codex_text_deltas_move_the_live_count(self):
        self.c._request_metrics.runtime = "codex"
        self.context.emit("delta", {"item": "msg", "text": "x" * 40})  # As the provider forwards a Codex delta.
        self.assertGreaterEqual(usage_meter.turn_tokens(self.c) or 0, 10)

    def test_rate_limit_updates_without_a_thread_are_kept_for_the_chats_connection(self):
        self.p._event("account/rateLimits/updated", {"rateLimits": {
            "primary": {"usedPercent": 40, "windowDurationMins": 300, "resetsAt": None}, "secondary": None}})
        state = usage_windows.current(usage_windows.connection_key(self.c))
        self.assertEqual([(w["kind"], w["used"]) for w in state["windows"]], [("primary", .4)])
        self.assertEqual(self.c._request_metrics.snapshot().get("usage"), None, "Not a usage report")


class LiveTurnTests(unittest.TestCase):
    def test_streamed_characters_estimate_between_reports_and_each_report_settles_it(self):
        from angelo_agent.telemetry import RequestMetrics
        from types import SimpleNamespace
        metrics = RequestMetrics("claude", session_id="chat")
        controller = SimpleNamespace(_request_metrics=metrics)
        self.assertIsNone(usage_meter.turn_tokens(controller), "Nothing arrived yet")
        usage_meter.streamed(metrics, 400)
        self.assertEqual(usage_meter.turn_tokens(controller), 100)
        metrics.path.running = {"input_tokens": 9000, "output_tokens": 130}  # The API's own count arrives.
        self.assertEqual(usage_meter.turn_tokens(controller), 130)
        usage_meter.streamed(metrics, 80)
        self.assertEqual(usage_meter.turn_tokens(controller), 150)
        metrics.finish("completed")
        self.assertIsNone(usage_meter.turn_tokens(controller), "The receipt is the record now")
        usage_meter.streamed(None, 10)
        usage_meter.streamed(metrics, -5)

    def test_claudes_round_stream_reports_every_kind_of_delta(self):
        from angelo_agent.claude_stream import RoundStream
        seen = []
        stream = RoundStream(on_stream=seen.append)
        for delta in ({"type": "text_delta", "text": "Hello"}, {"type": "input_json_delta", "partial_json": '{"a":'},
                      {"type": "thinking_delta", "thinking": "hmm"}, {"type": "signature_delta", "signature": "x"}):
            stream.events({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
                                                             "delta": delta}}, {})
        self.assertEqual(seen, [5, 5, 3])


class ClaudeHookTests(ClaudeAdapterFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        usage_windows.clear()
        self.addCleanup(usage_windows.clear)

    def test_a_rate_limit_event_in_a_turn_is_kept_for_the_connection(self):
        def reply(process, _):
            process.stdout.emit({"type": "rate_limit_event", "session_id": "fixture-native", "rate_limit_info": {
                "status": "allowed", "rateLimitType": "five_hour", "utilization": 0.3, "resetsAt": 4102444800,
                "unifiedWindows": {"seven_day": {"utilization": 0.1, "resetsAt": 4102444800}}}})
            process.stdout.emit(completed())
        self.process.on_user = reply
        self.turn(None, "Light it for dusk")
        state = usage_windows.current("claude_subscription")
        self.assertEqual([(w["kind"], w["used"]) for w in state["windows"]], [("five_hour", .3), ("seven_day", .1)])


if __name__ == "__main__":
    unittest.main()
