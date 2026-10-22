# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A long round is neither cut while it streams nor silent, and a turn that was cut still shows what it used.

The stream-json shapes are the Messages API's stream events as Claude Code
forwards them (``--include-partial-messages``). Unbilled fixtures only: they
prove what the host does with these events, not that a live runtime sends
them. ``test_native_turn_activity_codex`` holds the Codex turns.
"""

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from claude_protocol_fixtures import ClaudeAdapterFixture, NativeInitializeFixture, SUPPORTED, completed
from angelo_agent import turn_activity
from angelo_agent.chat_settings import normalize_models
from angelo_agent.chat_titles import live_label
from angelo_agent.claude_stream import STATUS_PLAN, STATUS_THINKING, RoundStream
from angelo_agent.controller import Controller, ToolDispatchBudget
from angelo_agent.protocol import RpcError
from angelo_agent.provider_usage import ClaudeProcessUsage
from angelo_agent.providers import ClaudeProvider
from angelo_agent.telemetry import RequestMetrics, clean_receipt
from angelo_agent.turn_context import TurnContext
from test_native_provider_usage import claude_result
from test_native_round_savings import committed, program


def stream(event):
    return {"type": "stream_event", "event": event, "session_id": "fixture-native"}


def message_start(identity="msg_1", uncached=120, read=90000, write=4000):
    return stream({"type": "message_start", "message": {"id": identity, "model": "claude-opus-5-5", "usage": {
        "input_tokens": uncached, "cache_read_input_tokens": read, "cache_creation_input_tokens": write, "output_tokens": 2}}})


def message_delta(output=5000):
    return stream({"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": output}})


def thinking(index, text):
    return stream({"type": "content_block_delta", "index": index, "delta": {"type": "thinking_delta", "thinking": text}})


THINKING_START = stream({"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}})
PLAN_START = stream({"type": "content_block_start", "index": 1, "content_block": {
    "type": "tool_use", "id": "toolu_1", "name": "StructuredOutput", "input": {}}})


def block_stop(index):
    return stream({"type": "content_block_stop", "index": index})


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class ActivityTests(unittest.TestCase):
    def test_silence_and_one_long_stretch_are_the_only_reasons_to_give_up(self):
        clock = Clock()
        activity = turn_activity.TurnActivity(clock)
        clock.now += 599
        self.assertIsNone(activity.overdue())
        clock.now += 1
        self.assertEqual(activity.overdue(), turn_activity.SILENT)
        activity.touch()  # The runtime is streaming again: silence starts over, the stretch does not.
        self.assertIsNone(activity.overdue())
        for _ in range(11):
            clock.now += 100
            activity.touch()
        self.assertIsNone(activity.overdue(), "1,700 s of steady streaming is not a hung round")
        clock.now += 100
        activity.touch()
        self.assertEqual(activity.overdue(), turn_activity.LONG)
        activity.rest()  # The host handed back a tool result: a new stretch.
        self.assertIsNone(activity.overdue())

    def test_waiting_ends_at_once_when_the_turn_does_and_never_while_the_host_is_working(self):
        clock, done = Clock(), threading.Event()
        activity = turn_activity.TurnActivity(clock)
        done.set()
        self.assertIsNone(turn_activity.wait(done, activity))
        done.clear()
        polls = []

        def busy():  # A person reads a script for an hour; then nothing comes for ten minutes.
            polls.append(clock.now)
            clock.now += 600
            return len(polls) <= 6

        with patch.object(turn_activity, "POLL_SECONDS", 0.001):
            self.assertEqual(turn_activity.wait(done, activity, busy=busy), turn_activity.SILENT)
        self.assertEqual(len(polls), 7)

    def test_the_messages_say_what_happened(self):
        self.assertEqual(turn_activity.problem("Claude", turn_activity.SILENT, "Inspect before retrying"),
                         "Claude turn timed out: nothing came from the model for 10 minutes. Inspect before retrying")
        self.assertIn("ran for 30 minutes without a result", turn_activity.problem("Agent", turn_activity.LONG, ""))


class ClaudeRoundTests(ClaudeAdapterFixture, unittest.TestCase):
    """The real transport reader and waiter over the stdio fixture, with the limits scaled down."""

    def play(self, events, pause=0.02):
        def on_user(process, _event):
            def run():
                for event in events:
                    if event is None:
                        return  # The runtime goes silent.
                    process.stdout.emit(event)
                    time.sleep(pause)
            threading.Thread(target=run, daemon=True).start()
        self.process.on_user = on_user

    def limits(self, idle, stretch):
        for name, value in (("IDLE_SECONDS", idle), ("STRETCH_SECONDS", stretch), ("POLL_SECONDS", 0.01)):
            patcher = patch.object(turn_activity, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def kinds(self):
        return [kind for kind, _ in self.host.events]

    def test_a_round_that_keeps_streaming_outlives_the_silence_limit_and_shows_its_progress(self):
        self.limits(idle=0.6, stretch=30)  # Events 20 ms apart: a loaded machine has room, a flat cap would not.
        events = [message_start(), THINKING_START] + [thinking(0, "step %d. " % index) for index in range(45)]
        events += [block_stop(0), PLAN_START, block_stop(1), message_delta(), completed("Done thinking")]
        self.play(events)
        self.start(None)
        plan = self.turn(None, "A long build")
        self.assertEqual(plan["reply"], "Done thinking", "0.9 s of streaming against a 0.6 s silence limit")
        rows = [(kind, data) for kind, data in self.host.events if kind.startswith("thinking")]
        kinds = [kind for kind, _ in rows]
        self.assertEqual((kinds[0], kinds[-1]), ("thinking_started", "thinking_completed"))
        self.assertEqual(set(kinds[1:-1]), {"thinking"})
        self.assertLessEqual(len(kinds) - 2, 4, "45 deltas in 0.9 s reach the chat at most twice a second")
        self.assertEqual(len({data["item"] for _, data in rows}), 1)
        self.assertEqual("".join(data.get("text", "") for _, data in rows),
                         "".join("step %d. " % index for index in range(45)), "Nothing of the summary is lost")
        statuses = [data["text"] for kind, data in self.host.events if kind == "status"]
        self.assertEqual(statuses, [STATUS_THINKING, STATUS_PLAN, STATUS_THINKING])
        self.assertLess(self.kinds().index("thinking_started"), self.kinds().index("message_complete"))

    def test_a_silent_round_is_given_up_and_its_known_usage_is_reported(self):
        self.limits(idle=0.15, stretch=30)
        self.host.record_interrupted_usage = Mock()
        self.play([message_start(), THINKING_START, None])
        self.start(None)
        with self.assertRaisesRegex(RpcError, "Claude turn timed out: nothing came from the model for 0 minutes"):
            self.turn(None, "A long build")
        self.assertEqual(self.process.stdin.messages[-1]["request"]["subtype"], "interrupt")
        self.host.record_interrupted_usage.assert_called_once_with(
            {"input_tokens": 94120, "cached_input_tokens": 90000, "cache_write_input_tokens": 4000,
             "requests": 1, "models": ["claude-opus-5-5"]})
        self.host.record_provider_usage.assert_not_called()

    def quiet(self, event):
        self.limits(idle=0.3, stretch=30)
        self.play([message_start()] + [event] * 150)  # 3 s of notices and nothing from the model.
        self.start(None)
        started = time.monotonic()
        with self.assertRaisesRegex(RpcError, "nothing came from the model"):
            self.turn(None, "A long build")
        self.assertLess(time.monotonic() - started, 2.0, "The notices kept the turn alive")

    def test_a_retry_notice_is_not_the_model_answering(self):
        self.quiet({"type": "system", "subtype": "api_retry", "attempt": 1, "session_id": "fixture-native"})

    def test_a_keep_alive_is_not_the_model_answering(self):
        self.quiet({"type": "keep_alive"})

    def test_a_ping_in_a_quiet_stream_is_not_the_model_answering(self):
        self.quiet(stream({"type": "ping"}))

    def test_one_endless_stretch_is_given_up_even_while_it_streams(self):
        self.limits(idle=5, stretch=0.2)
        self.play([message_start(), THINKING_START] + [thinking(0, "again ")] * 200)
        self.start(None)
        with self.assertRaisesRegex(RpcError, "one stretch of model work ran for 0 minutes without a result"):
            self.turn(None, "A long build")

    def test_a_resumed_process_reports_its_first_round_from_the_api_counts(self):
        # Claude Code restores the session's totals on --resume, so the first result has no baseline.
        self.play([message_start(), message_delta(5000),
                   dict(claude_result(500, 990000, 44000, 77000, api_ms=60000, cost=2.5), session_id="fixture-native")])
        self.start("fixture-native")
        self.turn("fixture-native", "After a restart")
        self.host.record_provider_usage.assert_called_once_with(
            {"input_tokens": 94120, "cached_input_tokens": 90000, "cache_write_input_tokens": 4000,
             "requests": 1, "output_tokens": 5000, "models": ["claude-opus-5-5"], "turn_ms": 900})

    def test_stop_during_a_round_reports_what_the_round_had_already_used(self):
        self.limits(idle=30, stretch=30)
        self.host.record_interrupted_usage = Mock()
        started = threading.Event()

        def on_user(process, _event):
            process.stdout.emit(message_start())
            started.set()
        self.process.on_user = on_user
        self.start(None)
        result = {}
        worker = threading.Thread(target=lambda: result.update(plan=self.turn(None, "Stop me")), daemon=True)
        worker.start()
        self.assertTrue(started.wait(5))
        deadline = time.monotonic() + 5
        while self.host.record_provider_turn.call_count == 0 and time.monotonic() < deadline:
            time.sleep(0.005)
        time.sleep(0.05)  # The reader has delivered message_start.
        self.host.cancelled = True
        self.provider.stop()
        worker.join(5)
        self.assertIsNone(result["plan"])
        self.host.record_interrupted_usage.assert_called_once_with(
            {"input_tokens": 94120, "cached_input_tokens": 90000, "cache_write_input_tokens": 4000,
             "requests": 1, "models": ["claude-opus-5-5"]})


class DisplayEventTests(unittest.TestCase):
    """A status or a thinking summary is drawn; it never starts a save of the whole conversation."""

    def test_statuses_and_thinking_summaries_redraw_without_saving(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        controller = Controller(directory.name)
        self.addCleanup(controller.close)
        controller._last_save = time.monotonic() - 60
        with patch.object(controller.store, "save") as save:
            version = controller.version
            controller.emit("status", {"text": STATUS_PLAN})
            controller.emit("thinking_started", {"item": "t1", "time": time.perf_counter()})
            controller.emit("thinking", {"item": "t1", "text": "Weighing the camera angle. "})
            controller.emit("thinking_completed", {"item": "t1", "time": time.perf_counter()})
            self.assertTrue(controller.pump_events())
            save.assert_not_called()
            self.assertGreater(controller.version, version, "Still redrawn")
            row = controller.state["messages"][-1]
            self.assertEqual((row["role"], row["text"], row["status"]),
                             ("thinking", "Weighing the camera angle. ", "completed"))
            controller.emit("delta", {"item": "a1", "text": "Done."})
            controller.pump_events()
            save.assert_called_once()


class StreamUsageTests(unittest.TestCase):
    def feed(self, *events):
        round_stream = RoundStream()
        for event in events:
            round_stream.events(event, {})
        return round_stream.observed_usage()

    def test_a_finished_round_sums_the_api_counts_of_every_message(self):
        usage, complete = self.feed(message_start("msg_1"), message_delta(40), message_start("msg_2", 10, 100, 0),
                                    message_delta(60))
        self.assertTrue(complete)
        self.assertEqual(usage, {"input_tokens": 94230, "cached_input_tokens": 90100, "cache_write_input_tokens": 4000,
                                 "requests": 2, "output_tokens": 100, "models": ["claude-opus-5-5"]})

    def test_an_unfinished_message_has_no_output_count_and_nothing_is_estimated(self):
        usage, complete = self.feed(message_start("msg_1"), message_delta(40), message_start("msg_2", 10, 100, 0),
                                    thinking(0, "x" * 5000))
        self.assertFalse(complete)
        self.assertNotIn("output_tokens", usage)
        self.assertEqual(usage["input_tokens"], 94230)

    def test_no_counts_is_unmeasured(self):
        self.assertEqual(self.feed(thinking(0, "x")), (None, False))
        broken = stream({"type": "message_start", "message": {"id": "msg_1", "usage": {"input_tokens": -1}}})
        self.assertEqual(self.feed(broken), (None, False))
        self.assertEqual(self.feed(stream({"type": "message_start", "message": {"id": "msg_1"}})), (None, False))

    def test_running_totals_are_preferred_and_the_api_counts_fill_the_gaps(self):
        observed = ({"input_tokens": 3110, "cached_input_tokens": 3000, "cache_write_input_tokens": 100,
                     "output_tokens": 60}, True)
        usage = ClaudeProcessUsage()
        first = usage.turn(claude_result(10, 0, 3000, 200, api_ms=2500, cost=.04), observed)
        self.assertEqual((first["input_tokens"], first["cost_usd"]), (3010, .04), "The runtime's totals, with their cost")
        self.assertNotIn("requests", first, "Not reported for this round, so not invented")
        counted = ClaudeProcessUsage().turn(claude_result(10, 0, 3000, 200, api_ms=2500, cost=.04),
                                            (dict(observed[0], requests=3), True))
        self.assertEqual((counted["requests"], counted["cost_usd"]), (3, .04), "The stream's count beside the runtime's totals")
        resumed = ClaudeProcessUsage(resumed=True)
        first = resumed.turn(claude_result(500, 90000, 4000, 7000, api_ms=60000, cost=2.5, duration_ms=1300), observed)
        self.assertEqual(first, dict(observed[0], turn_ms=1300))
        self.assertNotIn("cost_usd", first, "No baseline: tokens only, never an invented cost")
        second = resumed.turn(claude_result(510, 93000, 4100, 7100, api_ms=61500, cost=2.6), observed)
        self.assertAlmostEqual(second["cost_usd"], .1)
        self.assertIsNone(ClaudeProcessUsage(resumed=True).turn(claude_result(1, 0, 0, 1, api_ms=1, cost=0),
                                                                (observed[0], False)), "An incomplete count is not a total")


class ReceiptTests(unittest.TestCase):
    ROUND = {"input_tokens": 1000, "cached_input_tokens": 900, "cache_write_input_tokens": 0, "output_tokens": 50,
             "cost_usd": 0.01, "turn_ms": 400}

    def receipt(self, *reports):
        metrics = RequestMetrics("claude")
        for usage, interrupted in reports:
            metrics.count("provider_turn_requests")
            metrics.provider_usage(usage, interrupted=interrupted)
        return metrics.finish("interrupted")

    def test_an_interrupted_turn_no_longer_hides_what_the_other_turns_used(self):
        cut = {"input_tokens": 1200, "cached_input_tokens": 1000, "cache_write_input_tokens": 0}
        usage = self.receipt((self.ROUND, False), (self.ROUND, False), (cut, True))["usage"]
        self.assertEqual(usage, {"input_tokens": 3200, "cached_input_tokens": 2800, "cache_write_input_tokens": 0,
                                 "output_tokens": 100, "cost_usd": 0.02, "turn_ms": 800, "turns": 3,
                                 "interrupted_turns": 1})

    def test_a_cut_turn_with_nothing_measured_is_still_marked(self):
        self.assertEqual(self.receipt((None, True))["usage"], {"turns": 1, "interrupted_turns": 1})

    def test_an_unmeasured_completed_turn_still_withholds_its_fields(self):
        receipt = self.receipt((self.ROUND, False), (None, False))
        self.assertNotIn("usage", receipt, "Unmeasured is not interrupted: partial sums would pass as totals")
        usage = self.receipt((self.ROUND, False), ({"input_tokens": 5}, False), (None, True))["usage"]
        self.assertEqual(usage, {"input_tokens": 1005, "turns": 3, "interrupted_turns": 1})

    def test_the_receipt_allowlist_keeps_the_mark(self):
        base = {"status": "interrupted", "runtime": "claude", "phases": {"total_ms": 5}, "counts": {}}
        cleaned = clean_receipt(dict(base, usage={"turns": 2, "interrupted_turns": 1, "input_tokens": 9}))
        self.assertEqual(cleaned["usage"], {"turns": 2, "interrupted_turns": 1, "input_tokens": 9})
        self.assertNotIn("interrupted_turns", clean_receipt(dict(base, usage={"turns": 2, "interrupted_turns": "1",
                                                                               "input_tokens": 9}))["usage"])


CLAUDE = normalize_models([{"value": "sonnet", "displayName": "Claude Sonnet", "isDefault": True,
                            "supportedEffortLevels": ["low", "medium", "high"]}], "claude")


class OfflineClaude(ClaudeProvider):
    def connect(self):
        self.executable, self.env = "claude", {"FIXTURE": "only"}
        return self.controller.state["native_id"]


class StoppedTurnReceiptTests(unittest.TestCase):
    """Real Controller and provider: Stop during the second round of a request."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.c = Controller(self.directory.name)
        self.c.set_document(("scratch", 1))
        self.c.choose_connection("claude_subscription", {"model": ""}, "", None, CLAUDE, "sonnet")
        self.c.state["options"]["approval"] = "AUTO_BOUNDED"
        self.rounds, self.second_started = 0, threading.Event()
        self.receipts = []
        patches = [patch("angelo_agent.controller.ClaudeProvider", OfflineClaude),
                   patch("angelo_agent.child_process.popen",
                         side_effect=lambda *args, **kwargs: NativeInitializeFixture(on_user=self.reply)),
                   patch("angelo_agent.child_process.run",
                         return_value=SimpleNamespace(returncode=0, stdout=SUPPORTED)),
                   patch.object(self.c.telemetry, "record", side_effect=self.receipts.append),
                   # The second round's observation, without Blender.
                   patch("angelo_agent.turn_context.prepare", return_value=TurnContext(
                       {"context_version": 2, "objects": []}, stats={}))]
        for value in patches:
            value.start()
            self.addCleanup(value.stop)

    def tearDown(self):
        self.c.close()
        self.directory.cleanup()

    def reply(self, process, event):
        self.rounds += 1
        if self.rounds == 1:
            process.stdout.emit(message_start("msg_1", 100, 0, 3000))
            process.stdout.emit(message_delta(200))
            result = claude_result(100, 0, 3000, 200, api_ms=2500, cost=.04)
            result["structured_output"] = {"reply": "", "operations": [program()], "verify": True}
            process.stdout.emit(result)
        else:
            process.stdout.emit(message_start("msg_2", 150, 3000, 500))
            self.second_started.set()  # ... and thinks until the person presses Stop.

    def test_the_receipt_of_a_stopped_request_shows_its_completed_round_and_the_cut_one(self):
        executor = SimpleNamespace(calls=[], window="window", supports_native_job=lambda name: False,
                                   precondition=lambda name, arguments: "unchanged",
                                   execute=lambda name, arguments: committed())
        self.c.send("Create a cube and look at it", {"objects": []})
        apply = lambda call: self.c.execute_tool(call, executor, self.c.document)
        deadline, stopped = time.monotonic() + 10, False
        while time.monotonic() < deadline:
            self.c.pump_events()
            self.c.pump_tools(executor, self.c.document, apply_mutation=apply, budget=ToolDispatchBudget(seconds=60))
            if self.second_started.is_set() and not stopped:
                time.sleep(0.05)  # The reader delivers message_start.
                self.c.stop()
                stopped = True
            if stopped and not self.c.busy and not (self.c._worker and self.c._worker.is_alive()):
                self.c.pump_events()
                break
            time.sleep(.002)
        (receipt,) = self.receipts
        self.assertEqual(receipt["status"], "interrupted")
        self.assertEqual(receipt["usage"], {
            "input_tokens": 3100 + 3650, "cached_input_tokens": 3000, "cache_write_input_tokens": 3000 + 500,
            "output_tokens": 200, "api_ms": 2500, "cost_usd": .04, "turn_ms": 900, "requests": 2, "turns": 2,
            "interrupted_turns": 1})


class LiveLabelTests(unittest.TestCase):
    def test_the_live_row_names_the_phase_the_runtime_reported(self):
        self.assertEqual(live_label(STATUS_PLAN), "Preparing the next steps")
        for status in (STATUS_THINKING, "Completed", ""):
            self.assertEqual(live_label(status), "Thinking")
        self.assertEqual(live_label("Approval required"), "Waiting for your approval")  # chat_titles.LIVE (1.0.4)
        self.assertEqual(live_label("Connecting"), "Connecting")


if __name__ == "__main__":
    unittest.main()
