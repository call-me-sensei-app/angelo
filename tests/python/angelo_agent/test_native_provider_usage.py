# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Per-turn provider usage from runtime running totals; no process or inference.

The notification and result shapes follow the installed Codex 0.160.0
app-server schema and Claude Code 2.1.258 result contract. These fixtures prove
the host's differencing and fencing, not a live provider's reporting.
"""

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import call

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from claude_protocol_fixtures import ClaudeAdapterFixture, NativeInitializeFixture
from angelo_agent.controller import Controller
from angelo_agent.provider_usage import (ClaudeProcessUsage, CodexThreadUsage, claude_total, codex_total,
                                          difference)
from angelo_agent.providers import CodexProvider


class RecordingRPC:
    def __init__(self):
        self.closed = threading.Event()
        self.responses, self.requests = [], []

    def respond(self, identity, payload):
        self.responses.append((identity, payload))

    def request(self, method, parameters, **kwargs):
        self.requests.append((method, parameters))
        return {}

    def close(self):
        self.closed.set()


def codex_breakdown(prompt, cached, output, reasoning=0, write=None):
    value = {"totalTokens": prompt + output, "inputTokens": prompt, "cachedInputTokens": cached,
             "outputTokens": output, "reasoningOutputTokens": reasoning}
    if write is not None:
        value["cacheWriteInputTokens"] = write
    return value


def token_update(thread, turn, total, last=None):
    return {"threadId": thread, "turnId": turn, "tokenUsage": {
        "total": total, "last": last or total, "modelContextWindow": 400000}}


def claude_result(uncached, read, write, output, *, api_ms, cost, duration_ms=900, thinking=None, subtype="success"):
    entry = {"inputTokens": uncached, "outputTokens": output, "cacheReadInputTokens": read,
             "cacheCreationInputTokens": write, "webSearchRequests": 0, "costUSD": cost,
             "contextWindow": 200000, "maxOutputTokens": 32000}
    if thinking is not None:
        entry["thinkingTokens"] = thinking
    return {"type": "result", "subtype": subtype, "is_error": subtype != "success", "session_id": "fixture-native",
            "duration_ms": duration_ms, "duration_api_ms": api_ms, "num_turns": 1, "total_cost_usd": cost,
            "modelUsage": {"claude-sonnet": entry},
            "structured_output": {"reply": "Verified", "operations": [], "verify": False}}


class UsageNormalizationTests(unittest.TestCase):
    def test_codex_cached_tokens_are_part_of_input_and_cache_write_defaults_to_zero(self):
        self.assertEqual(codex_total(codex_breakdown(1000, 800, 50, 20)), {
            "input_tokens": 1000, "cached_input_tokens": 800, "cache_write_input_tokens": 0,
            "output_tokens": 50, "reasoning_output_tokens": 20})
        self.assertEqual(codex_total(codex_breakdown(1000, 800, 50, write=30))["cache_write_input_tokens"], 30)
        for broken in (None, {}, dict(codex_breakdown(1, 0, 1), outputTokens=-1),
                       dict(codex_breakdown(1, 0, 1), inputTokens=True), dict(codex_breakdown(1, 0, 1), inputTokens=1.5),
                       dict(codex_breakdown(1, 0, 1), inputTokens=10 ** 13)):
            self.assertIsNone(codex_total(broken))

    def test_claude_input_includes_cache_reads_and_writes_across_models(self):
        result = claude_result(100, 2000, 300, 40, api_ms=1200, cost=.25, thinking=10)
        result["modelUsage"]["claude-haiku"] = {"inputTokens": 5, "outputTokens": 7, "cacheReadInputTokens": 0,
                                                "cacheCreationInputTokens": 0, "thinkingTokens": 0}
        self.assertEqual(claude_total(result), {
            "input_tokens": 2405, "cached_input_tokens": 2000, "cache_write_input_tokens": 300,
            "output_tokens": 47, "reasoning_output_tokens": 10, "api_ms": 1200, "cost_usd": .25})

    def test_claude_optional_thinking_and_bad_totals_are_not_invented(self):
        self.assertNotIn("reasoning_output_tokens", claude_total(claude_result(1, 0, 0, 1, api_ms=1, cost=0)))
        no_cost = claude_result(1, 0, 0, 1, api_ms=1, cost=0)
        no_cost["total_cost_usd"] = float("nan")
        self.assertNotIn("cost_usd", claude_total(no_cost))
        for broken in (None, {"modelUsage": []}, {"modelUsage": {"m": {"inputTokens": 1}}},
                       {"modelUsage": {"m": "not usage"}}):
            self.assertIsNone(claude_total(broken))

    def test_a_decreasing_total_cannot_be_differenced(self):
        self.assertEqual(difference({"input_tokens": 9, "api_ms": 5}, {"input_tokens": 4, "api_ms": 1}),
                         {"input_tokens": 5, "api_ms": 4})
        self.assertIsNone(difference({"input_tokens": 3}, {"input_tokens": 4}))
        self.assertIsNone(difference(None, {"input_tokens": 4}))
        self.assertEqual(difference({"input_tokens": 9, "cost_usd": 1}, {"input_tokens": 4}), {"input_tokens": 5})


class CodexThreadUsageTests(unittest.TestCase):
    def test_new_thread_turns_are_differences_of_the_thread_total(self):
        usage = CodexThreadUsage()
        usage.thread_started("thread")
        usage.begin_turn("thread")
        usage.observe("thread", token_update("thread", "t1", codex_breakdown(1000, 0, 40))["tokenUsage"])
        usage.observe("thread", token_update("thread", "t1", codex_breakdown(2500, 900, 90, 30))["tokenUsage"])
        self.assertEqual(usage.finish_turn(4100), {"input_tokens": 2500, "cached_input_tokens": 900,
            "cache_write_input_tokens": 0, "output_tokens": 90, "reasoning_output_tokens": 30, "requests": 2,
            "turn_ms": 4100})
        usage.begin_turn("thread")
        usage.observe("thread", token_update("thread", "t2", codex_breakdown(4000, 2400, 100, 30))["tokenUsage"])
        self.assertEqual(usage.finish_turn(None), {"input_tokens": 1500, "cached_input_tokens": 1500,
            "cache_write_input_tokens": 0, "output_tokens": 10, "reasoning_output_tokens": 0, "requests": 1})

    def test_unknown_baseline_missing_update_and_decrease_are_unmeasured(self):
        usage = CodexThreadUsage()
        usage.begin_turn("resumed")  # Resumed thread: its earlier total is unknown.
        usage.observe("resumed", token_update("resumed", "t1", codex_breakdown(9000, 0, 10))["tokenUsage"])
        self.assertIsNone(usage.finish_turn(100))
        usage.begin_turn("resumed")  # Now the reported total is a known baseline.
        usage.observe("resumed", token_update("resumed", "t2", codex_breakdown(9500, 400, 20))["tokenUsage"])
        self.assertEqual(usage.finish_turn(100)["input_tokens"], 500)
        usage.begin_turn("resumed")
        self.assertIsNone(usage.finish_turn(100))  # No update arrived; never zero.
        usage.begin_turn("resumed")
        usage.observe("resumed", token_update("resumed", "t4", codex_breakdown(100, 0, 1))["tokenUsage"])
        usage.observe("resumed", token_update("resumed", "t4", codex_breakdown(9900, 400, 30))["tokenUsage"])
        self.assertIsNone(usage.finish_turn(100))  # A decrease taints the whole turn.
        usage.observe("other", token_update("other", "x", codex_breakdown(1, 0, 1))["tokenUsage"])
        usage.begin_turn("resumed")  # Switching threads discards the other baseline.
        usage.observe("resumed", token_update("resumed", "t5", codex_breakdown(9999, 400, 31))["tokenUsage"])
        self.assertIsNone(usage.finish_turn(100))
        usage.thread_resumed("resumed")  # A reconnect never reuses the stale total.
        usage.begin_turn("resumed")
        usage.observe("resumed", token_update("resumed", "t6", codex_breakdown(99999, 400, 40))["tokenUsage"])
        self.assertIsNone(usage.finish_turn(100))


class ClaudeProcessUsageTests(unittest.TestCase):
    def test_claude_process_differences_consecutive_results_from_zero(self):
        usage = ClaudeProcessUsage()
        first = usage.turn(claude_result(10, 0, 3000, 200, api_ms=2500, cost=.04, duration_ms=3100, thinking=50))
        self.assertEqual(first, {"input_tokens": 3010, "cached_input_tokens": 0, "cache_write_input_tokens": 3000,
                                 "output_tokens": 200, "reasoning_output_tokens": 50, "api_ms": 2500,
                                 "cost_usd": .04, "turn_ms": 3100, "models": ["claude-sonnet"]})
        second = usage.turn(claude_result(20, 3000, 3100, 260, api_ms=3700, cost=.05, duration_ms=1300, thinking=60))
        self.assertEqual(second["models"], ["claude-sonnet"])
        self.assertEqual(second["input_tokens"], 3110)
        self.assertEqual(second["cached_input_tokens"], 3000)
        self.assertEqual(second["cache_write_input_tokens"], 100)
        self.assertEqual(second["api_ms"], 1200)
        self.assertAlmostEqual(second["cost_usd"], .01)
        self.assertEqual(second["turn_ms"], 1300)
        # A zeroed crash result cannot become a baseline for later differences.
        self.assertIsNone(usage.turn(claude_result(0, 0, 0, 0, api_ms=0, cost=0)))
        self.assertIsNone(usage.turn(claude_result(30, 3000, 3100, 300, api_ms=4000, cost=.06)))

    def test_a_result_without_usage_is_unmeasured_until_a_new_baseline_exists(self):
        usage = ClaudeProcessUsage()
        self.assertIsNone(usage.turn({"type": "result", "duration_ms": 10}))
        self.assertIsNone(usage.turn(claude_result(1, 0, 0, 1, api_ms=1, cost=0)))
        self.assertEqual(usage.turn(claude_result(3, 0, 0, 2, api_ms=4, cost=0))["input_tokens"], 2)

    def test_resumed_process_restores_session_totals_so_its_first_turn_is_unmeasured(self):
        usage = ClaudeProcessUsage(resumed=True)
        # Claude Code reloads the resumed session's saved cost-state totals.
        self.assertIsNone(usage.turn(claude_result(500, 90000, 4000, 7000, api_ms=60000, cost=2.5)))
        second = usage.turn(claude_result(510, 93000, 4100, 7100, api_ms=61500, cost=2.6))
        self.assertEqual((second["input_tokens"], second["cached_input_tokens"], second["api_ms"]), (3110, 3000, 1500))


class CodexProviderUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.c = Controller(self.temp.name)
        self.c.set_document(("factory-runtime", 17))
        self.c.state["native_id"] = "native-thread"
        self.context = self.next_request()
        self.p = CodexProvider(self.context, {})
        self.p.rpc = RecordingRPC()
        self.p.bind_request(self.context)
        self.p._usage.thread_started("native-thread")

    def tearDown(self):
        self.p.close()
        self.c.close()
        self.temp.cleanup()

    def next_request(self):
        self.c._begin_metrics()
        self.c.busy, self.c.cancelled = True, False
        return self.c.runtime_context()

    def run_turn(self, turn, totals, duration_ms=2000, during_start=None):
        def start(method, parameters, **kwargs):
            self.assertEqual(method, "turn/start")
            for total in totals:
                self.p._event("thread/tokenUsage/updated", token_update("native-thread", turn, total))
            if during_start:
                during_start()
            self.p._event("turn/completed", {"threadId": "native-thread", "turn": {
                "id": turn, "status": "completed", "durationMs": duration_ms}})
            return {"turn": {"id": turn}}
        self.p.rpc.request = start
        return self.p.run("native-thread", "Offline fixture")

    def test_actual_turn_records_complete_request_usage(self):
        self.assertEqual(self.run_turn("turn-a", [codex_breakdown(5000, 0, 100), codex_breakdown(12000, 4800, 260, 90)]),
                         "completed")
        usage = self.c._request_metrics.snapshot()["usage"]
        self.assertEqual(usage, {"input_tokens": 12000, "cached_input_tokens": 4800, "cache_write_input_tokens": 0,
                                 "output_tokens": 260, "reasoning_output_tokens": 90, "turn_ms": 2000, "requests": 2,
                             "turns": 1})
        self.c.emit("finished", {"status": "completed"})
        self.c.pump_events()
        report = self.c.metrics_report()
        self.assertEqual(report["provider_usage"]["fields"]["input_tokens"]["total"], 12000)
        self.assertEqual(report["provider_usage"]["cached_input_share"], .4)
        self.assertIn("api_ms", report["provider_usage"]["unmeasured_fields"])

    def test_usage_updates_never_occupy_the_bounded_startup_queue(self):
        # More updates than the 64-entry queue: deferring them would drop the
        # turn's completion and hang the request, so fail inside turn/start.
        def inspect():
            deferred = [method for _, _, method, _ in self.p._scope.pending]
            self.assertNotIn("thread/tokenUsage/updated", deferred)
        self.run_turn("turn-a", [codex_breakdown(100, 0, 1)] * 70, during_start=inspect)
        self.assertEqual(self.c._request_metrics.snapshot()["usage"]["input_tokens"], 100)

    def test_turn_without_usage_or_from_a_stale_request_is_not_recorded(self):
        self.run_turn("turn-a", [], duration_ms=None)
        self.assertNotIn("usage", self.c._request_metrics.snapshot())
        old = self.context
        self.context = self.next_request()
        old.record_provider_usage({"input_tokens": 999})
        self.c.record_provider_usage({"input_tokens": 999}, request_fence=old.request_fence)
        self.assertEqual(self.c._request_metrics.usage, {})


class ClaudeProviderUsageTests(ClaudeAdapterFixture, unittest.TestCase):
    def test_each_typed_plan_round_records_its_process_difference(self):
        results = iter([claude_result(10, 0, 3000, 200, api_ms=2500, cost=.04, duration_ms=3100),
                        claude_result(20, 3000, 3100, 260, api_ms=3700, cost=.05, duration_ms=1300)])
        self.process.on_user = lambda process, _: process.stdout.emit(next(results))
        self.turn(None, "First")
        self.turn("fixture-native", "Second")
        first, second = (entry.args[0] for entry in self.host.record_provider_usage.call_args_list)
        self.assertEqual((first["input_tokens"], first["cache_write_input_tokens"], first["api_ms"]), (3010, 3000, 2500))
        self.assertEqual((second["input_tokens"], second["cached_input_tokens"], second["api_ms"]), (3110, 3000, 1200))

    def test_each_process_has_its_own_baseline_and_a_resumed_one_starts_unknown(self):
        self.process.on_user = lambda process, _: process.stdout.emit(
            claude_result(10, 0, 3000, 200, api_ms=2500, cost=.04))
        self.turn(None, "Fresh process")
        self.assertEqual(self.host.record_provider_usage.call_args.args[0]["input_tokens"], 3010)
        self.provider.stop()
        # The resumed process reports restored session totals plus its new turn.
        results = iter([claude_result(15, 2900, 3000, 230, api_ms=3400, cost=.05),
                        claude_result(20, 5800, 3000, 260, api_ms=4000, cost=.06)])
        newer = NativeInitializeFixture(on_user=lambda process, _: process.stdout.emit(next(results)))
        self.popen_mock.return_value = newer
        self.host._request_generation += 1
        self.provider.bind_request(self.host)
        self.turn("fixture-native", "Resumed process")
        self.assertIn("--resume=fixture-native", self.popen_mock.call_args.args[0])
        self.assertIsNone(self.host.record_provider_usage.call_args.args[0])
        self.turn("fixture-native", "Same resumed process")
        self.assertEqual(self.host.record_provider_usage.call_args.args[0]["input_tokens"], 2905)

    def test_result_without_usage_is_recorded_as_unmeasured(self):
        self.turn(None, "Fixture default result")
        self.assertEqual(self.host.record_provider_usage.call_args_list, [call(None)])


if __name__ == "__main__":
    unittest.main()
