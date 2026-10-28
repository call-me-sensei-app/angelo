# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A Codex turn that a provider error ends: the chat shows why at once, and a throttle is sent once more.

In a GPT-6.1 Sol gate turn (ChatGPT sign-in) codex app-server ended a turn after one request with
``error`` and ``turn/completed`` ``failed`` carrying ``"codexErrorInfo": {"responseTooManyFailedAttempts":
{"httpStatusCode": 429}}``. ``provider_errors.codex_error_text`` tested that object for membership in a
frozenset, raised ``TypeError`` on the RPC reader thread (which drops a notification handler's exception),
and the turn's ``done`` was never set: the chat said "working" until the 300 s task timeout (and would
have until the 10-minute idle watchdog).

``ScriptedRPC`` answers as codex 0.162 does (recorded against a local endpoint answering 429: the
``turn/start`` reply first, then the turn's notifications from the reader thread). The real-binary
class at the end drives an actual ``codex app-server`` against a local fake Responses endpoint when
one is available (``ANGELO_TEST_CODEX`` or the agent runtime); it is skipped otherwise. No account,
network or inference.
"""

import http.server
import json
import os
import re
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from host_test_policy import environment_skip
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from angelo_agent import codex_retry, notice_kinds, providers  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.protocol import RpcError  # noqa: E402
from angelo_agent.provider_errors import codex_error_info, codex_error_kind, codex_error_text  # noqa: E402
from angelo_agent.providers import CodexProvider  # noqa: E402
import eval_provider_limits as limits  # noqa: E402

THREAD = "native-thread"
THROTTLED = {"message": "exceeded retry limit, last status: 429 Too Many Requests",
             "codexErrorInfo": {"responseTooManyFailedAttempts": {"httpStatusCode": 429}},
             "additionalDetails": None, "misalignment": None}
USAGE = {"message": "You’ve hit your usage limit. Try again at 1:51 AM.", "codexErrorInfo": "usageLimitExceeded",
         "additionalDetails": None, "misalignment": None}
OVERLOADED = {"message": "Selected model is at capacity. Please try a different model.",
              "codexErrorInfo": "serverOverloaded", "additionalDetails": None, "misalignment": None}
# codex-cli 0.163 ends a turn whose response stopped early at once, never retried (openai/codex#53451).
INCOMPLETE = {"message": "Incomplete response returned, reason: max_output_tokens", "codexErrorInfo": "other",
              "additionalDetails": None, "misalignment": None}
RECONNECTING = {"message": "Reconnecting... 1/5", "codexErrorInfo": {"responseStreamDisconnected": {"httpStatusCode": 429}},
                "additionalDetails": "exceeded retry limit, last status: 429 Too Many Requests", "misalignment": None}
PLAN_REPLY = '{"reply":"Done.","operations":[]}'


def failed(turn, error, *, user=True, before=()):
    """A failed turn's notifications in the order codex 0.162 sends them."""
    events = [("thread/status/changed", {"threadId": THREAD, "status": {"type": "active", "activeFlags": []}}),
              ("turn/started", {"threadId": THREAD, "turn": {"id": turn, "status": "inProgress", "error": None}})]
    if user:
        item = {"type": "userMessage", "id": "user-" + turn, "content": [{"type": "text", "text": "message"}]}
        events += [(method, {"threadId": THREAD, "turnId": turn, "item": item})
                   for method in ("item/started", "item/completed")]
    events += list(before)
    events += [("thread/status/changed", {"threadId": THREAD, "status": {"type": "systemError"}}),
               ("error", {"error": error, "willRetry": False, "threadId": THREAD, "turnId": turn}),
               ("turn/completed", {"threadId": THREAD, "turn": {"id": turn, "status": "failed", "error": error,
                                                                "durationMs": 12}})]
    return events


def answered(turn, text=PLAN_REPLY):
    item = {"type": "agentMessage", "id": "msg-" + turn, "text": text}
    return [("turn/started", {"threadId": THREAD, "turn": {"id": turn, "status": "inProgress"}}),
            ("item/started", {"threadId": THREAD, "turnId": turn, "item": dict(item, text="")}),
            ("item/agentMessage/delta", {"threadId": THREAD, "turnId": turn, "itemId": item["id"], "delta": text}),
            ("item/completed", {"threadId": THREAD, "turnId": turn, "item": item}),
            ("turn/completed", {"threadId": THREAD, "turn": {"id": turn, "status": "completed", "durationMs": 40}})]


class ScriptedRPC:
    """codex app-server's answers: each ``turn/start`` replies, then its turn's notifications follow on another
    thread, as the RPC reader delivers them. ``outcomes`` is one callable per turn: ``turn ID -> events``."""

    def __init__(self, provider, outcomes, *, before_reply=False):
        self.provider, self.outcomes, self.before_reply = provider, list(outcomes), before_reply
        self.closed = threading.Event()
        self.requests, self.responses, self.delivered = [], [], []

    def request(self, method, params, **kwargs):
        self.requests.append((method, params))
        if method != "turn/start":
            return {}
        turn = "turn-%d" % sum(name == "turn/start" for name, _ in self.requests)
        events = self.outcomes.pop(0)(turn)
        if self.before_reply:  # Events the reader delivers before the turn/start reply (deferred by the scope).
            self.deliver(events)
        else:
            worker = threading.Thread(target=self.deliver, args=(events, .01), daemon=True)
            self.delivered.append(worker)
            worker.start()
        return {"turn": {"id": turn, "status": "inProgress", "error": None}}

    def deliver(self, events, pause=0):
        time.sleep(pause)
        for method, params in events:
            if callable(params):  # A host request (a tool call) the reader hands over at this point.
                params()
            else:
                self.provider._event(method, params, transport=self.provider._transport_token)

    def respond(self, identity, payload):
        self.responses.append((identity, payload))

    def write(self, message):
        self.responses.append((message.get("id"), message))

    def close(self):
        self.closed.set()

    def turn_starts(self):
        return [params for method, params in self.requests if method == "turn/start"]


class InterruptingRPC(ScriptedRPC):
    """``ScriptedRPC`` whose ``turn/interrupt`` delivers ``on_interrupt(turn ID)``'s events, as codex 0.163 sends them."""

    def __init__(self, provider, outcomes, on_interrupt):
        super().__init__(provider, outcomes)
        self.on_interrupt = on_interrupt

    def request(self, method, params, **kwargs):
        if method != "turn/interrupt":
            return super().request(method, params, **kwargs)
        self.requests.append((method, params))
        worker = threading.Thread(target=self.deliver, args=(self.on_interrupt(params["turnId"]), .01), daemon=True)
        self.delivered.append(worker)
        worker.start()
        return {}


class ErrorTextTests(unittest.TestCase):
    def test_codex_error_info_reads_names_and_objects(self):
        self.assertEqual(codex_error_info(THROTTLED), ("responseTooManyFailedAttempts", 429))
        self.assertEqual(codex_error_info(USAGE), ("usageLimitExceeded", None))
        self.assertEqual(codex_error_info({"codexErrorInfo": {"other": {}}}), ("other", None))
        for info in (None, [], ["usageLimitExceeded"], {"a": {}, "b": {}}, {"x": {"httpStatusCode": "429"}}, 7):
            with self.subTest(info=info):
                name, status = codex_error_info({"codexErrorInfo": info})
                self.assertIsNone(status)
                self.assertTrue(name is None or isinstance(name, str))

    def test_the_rc4_error_no_longer_raises_and_names_the_throttle(self):
        text = codex_error_text(THROTTLED)
        self.assertEqual(text, "Too many requests (HTTP 429): exceeded retry limit, last status: 429 Too Many Requests")
        self.assertEqual(notice_kinds.kind({"role": "error", "text": text}), "rate")

    def test_kinds_come_from_the_provider_classification(self):
        cases = [(THROTTLED, "rate", "rate"), (USAGE, "usage", "usage"), (OVERLOADED, "busy", "overloaded"),
                 ({"message": "boom", "codexErrorInfo": {"responseTooManyFailedAttempts": {"httpStatusCode": 500}}},
                  "busy", "overloaded"),
                 ({"message": "gateway", "codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": 503}}},
                  "busy", "overloaded"),
                 ({"message": "Bad request", "codexErrorInfo": "badRequest"}, None, "unknown"),
                 ({"message": "Bad request", "codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": 400}}},
                  None, "unknown")]
        for error, kind, notice in cases:
            with self.subTest(error=error):
                self.assertEqual(codex_error_kind(error), kind)
                self.assertEqual(notice_kinds.kind({"role": "error", "text": codex_error_text(error)}), notice)

    def test_a_request_id_that_looks_like_another_status_keeps_the_throttle(self):
        error = dict(THROTTLED, message=THROTTLED["message"] + ", request id: req-401-unauthorized")
        self.assertEqual(notice_kinds.kind({"role": "error", "text": codex_error_text(error)}), "rate")

    def test_odd_shapes_never_raise(self):
        for error in (None, "text", [], {}, {"message": 7}, {"codexErrorInfo": {"a": 1}}, {"codexErrorInfo": [1]}):
            with self.subTest(error=error):
                codex_error_text(error)
                codex_error_kind(error)
                codex_retry.delay(error)

    def test_the_harness_reads_the_rows_as_provider_limits(self):
        for error in (THROTTLED, USAGE):
            self.assertIsNotNone(limits.angelo_limit([{"role": "error", "text": codex_error_text(error)}], None))
        self.assertIsNone(limits.angelo_limit([{"role": "error", "text": codex_error_text(OVERLOADED)}], None),
                          "an overloaded provider is not the account's limit (as for Claude's 529)")


class RetryPolicyTests(unittest.TestCase):
    def test_only_transient_failures_wait(self):
        # A throttle waits past the account's one-minute window; an overloaded provider a few seconds.
        self.assertEqual(codex_retry.delay(THROTTLED, rng=lambda: 0), codex_retry.RATE_SECONDS)
        self.assertGreaterEqual(codex_retry.RATE_SECONDS, 60)
        self.assertEqual(codex_retry.delay(THROTTLED, rng=lambda: 1), codex_retry.RATE_SECONDS + codex_retry.JITTER_SECONDS)
        self.assertEqual(codex_retry.delay(OVERLOADED, rng=lambda: 0), codex_retry.BUSY_SECONDS)
        self.assertEqual((codex_retry.kind(THROTTLED), codex_retry.kind(OVERLOADED)), ("rate", "busy"))
        for error in (USAGE, {"message": "x", "codexErrorInfo": "rateLimitExceeded"}, {"message": "bad"}, None,
                      {"message": "x", "codexErrorInfo": "contextWindowExceeded"}):
            self.assertIsNone(codex_retry.delay(error))

    def test_the_wait_is_fixed_whatever_the_text_says(self):
        """No retry hint is read (codex 0.162 never passes one to a resendable error): fixed waits only."""
        for text in ("Please try again in 2.5s.", "retry after 1500 ms", "Retry-After: 0", "try again in 12m0s",
                     "Try again in 3 hours", "try again in" + " " * 50000 + "x"):
            with self.subTest(text=text[:40]):
                self.assertEqual(codex_retry.delay(dict(THROTTLED, message=text), rng=lambda: 0), codex_retry.RATE_SECONDS)
                self.assertEqual(codex_retry.delay(dict(OVERLOADED, additionalDetails=text), rng=lambda: 0),
                                 codex_retry.BUSY_SECONDS)
        self.assertFalse(hasattr(codex_retry, "hinted_seconds"))

    def test_the_wait_counts_down_in_whole_seconds(self):
        now, shown = [0.0], []

        def sleep(seconds):
            now[0] += seconds

        self.assertTrue(codex_retry.wait(2.5, lambda: False, shown.append, clock=lambda: now[0], sleep=sleep))
        self.assertEqual(shown, [codex_retry.waiting(3), codex_retry.waiting(2), codex_retry.waiting(1)])
        from angelo_agent.chat_titles import live_label
        self.assertEqual(live_label(codex_retry.waiting(42.2)), "Provider busy — retrying in 43 s")
        self.assertEqual(live_label(codex_retry.RETRYING), "Provider busy, retrying…")

    def test_wait_ends_at_once_on_stop(self):
        stopped, started = [False], time.monotonic()
        threading.Timer(.1, lambda: stopped.__setitem__(0, True)).start()
        self.assertFalse(codex_retry.wait(5, lambda: stopped[0]))
        self.assertLess(time.monotonic() - started, 1)
        self.assertTrue(codex_retry.wait(.05, lambda: False))


class ProviderFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.c = Controller(self.temp.name)
        self.addCleanup(self.c.close)
        self.c.set_document(("turn-errors", 3))
        self.c.state["native_id"] = THREAD
        self.c._begin_metrics()
        self.c.busy, self.c.cancelled = True, False
        self.context = self.c.runtime_context()
        self.p = CodexProvider(self.context, {})
        self.addCleanup(self.p.close)
        self.p._transport_token = object()
        self.p.thread_id = THREAD
        self.p.bind_request(self.context)
        self.statuses = []
        emit = self.context.emit

        def recording(kind, data=None):
            if kind == "status":
                self.statuses.append(data["text"])
            return emit(kind, data)

        self.context.emit = recording
        fast = mock.patch.multiple(codex_retry, RATE_SECONDS=.05, BUSY_SECONDS=.05, JITTER_SECONDS=0)
        fast.start()
        self.addCleanup(fast.stop)

    def script(self, *outcomes, **options):
        self.p.rpc = ScriptedRPC(self.p, outcomes, **options)
        return self.p.rpc

    def run_request(self, text="Stage a PREVIEW only that moves 'Eval Cube' to (3, 0, 0)."):
        started = time.monotonic()
        try:
            return self.p.run(THREAD, text), time.monotonic() - started
        except RpcError as error:
            return error, time.monotonic() - started


class FailedTurnTests(ProviderFixture):
    def test_the_rc4_turn_ends_at_once_with_its_error(self):
        with mock.patch.object(codex_retry, "PER_REQUEST", 0):  # The turn as an earlier build sent it: no resend.
            rpc = self.script(lambda turn: failed(turn, THROTTLED))
            outcome, seconds = self.run_request()
        self.assertIsInstance(outcome, RpcError)
        self.assertLess(seconds, 2, "never the 300 s task timeout or the 10-minute idle watchdog")
        self.assertEqual(str(outcome), codex_error_text(THROTTLED))
        self.assertEqual(len(rpc.turn_starts()), 1)
        self.assertTrue(self.p.done.is_set())

    def test_events_delivered_before_the_start_reply_end_the_turn_too(self):
        with mock.patch.object(codex_retry, "PER_REQUEST", 0):
            self.script(lambda turn: failed(turn, THROTTLED), before_reply=True)
            outcome, seconds = self.run_request()
        self.assertEqual(str(outcome), codex_error_text(THROTTLED))
        self.assertLess(seconds, 2)

    def test_a_usage_limit_is_never_resent(self):
        rpc = self.script(lambda turn: failed(turn, USAGE))
        outcome, seconds = self.run_request()
        self.assertEqual(str(outcome), "Provider usage limit reached (usageLimitExceeded): " + USAGE["message"])
        self.assertEqual(notice_kinds.kind({"text": str(outcome)}), "usage")
        self.assertEqual(len(rpc.turn_starts()), 1)
        self.assertEqual(self.statuses, [])
        self.assertLess(seconds, 2)

    def test_a_throttle_twice_shows_too_many_requests_after_one_resend(self):
        rpc = self.script(lambda turn: failed(turn, THROTTLED), lambda turn: failed(turn, THROTTLED))
        outcome, seconds = self.run_request()
        self.assertEqual(str(outcome), codex_error_text(THROTTLED))
        starts = rpc.turn_starts()
        self.assertEqual(len(starts), 2)
        # Codex recorded the message in the thread: the resend says so instead of repeating it.
        self.assertEqual(starts[1]["input"], [{"type": "text", "text": codex_retry.NOTE}])
        self.assertEqual(starts[1]["outputSchema"], starts[0]["outputSchema"])
        self.assertEqual(self.statuses, [codex_retry.waiting(1), "Thinking"], "counted down, then working again")
        self.assertEqual(self.c._request_metrics.counts.get("provider_retries_rate"), 1)
        self.assertLess(seconds, 2)

    def test_a_throttle_then_an_answer_completes_the_request(self):
        rpc = self.script(lambda turn: failed(turn, THROTTLED), answered)
        outcome, _ = self.run_request()
        self.assertEqual(outcome, "completed")
        self.assertEqual(len(rpc.turn_starts()), 2)
        self.assertEqual(self.p.turn_id, "turn-2")
        self.assertEqual(self.c._request_metrics.counts.get("provider_retries_rate"), 1)

    def test_an_overloaded_provider_is_resent_once(self):
        rpc = self.script(lambda turn: failed(turn, OVERLOADED), answered)
        self.assertEqual(self.run_request()[0], "completed")
        self.assertEqual(len(rpc.turn_starts()), 2)
        counts = self.c._request_metrics.counts
        self.assertEqual((counts.get("provider_retries_busy"), counts.get("provider_retries_rate")), (1, None))

    def test_a_message_codex_never_recorded_is_sent_again_whole(self):
        rpc = self.script(lambda turn: failed(turn, THROTTLED, user=False), answered)
        self.run_request("Make the cube red.")
        first, second = rpc.turn_starts()
        self.assertEqual(second["input"], first["input"])
        self.assertTrue(first["input"][0]["text"].startswith("Make the cube red."))

    def test_never_resent_after_model_output(self):
        delta = ("item/agentMessage/delta", {"threadId": THREAD, "turnId": "turn-1", "itemId": "m", "delta": "{"})
        rpc = self.script(lambda turn: failed(turn, THROTTLED, before=[delta]), answered)
        outcome, _ = self.run_request()
        self.assertIsInstance(outcome, RpcError)
        self.assertEqual(len(rpc.turn_starts()), 1)

    def test_never_resent_after_any_other_turn_item(self):
        item = ("item/started", {"threadId": THREAD, "turnId": "turn-1", "item": {"type": "reasoning", "id": "r"}})
        rpc = self.script(lambda turn: failed(turn, THROTTLED, before=[item]), answered)
        self.assertIsInstance(self.run_request()[0], RpcError)
        self.assertEqual(len(rpc.turn_starts()), 1)

    def test_never_resent_after_a_tool_call(self):
        def call(turn):
            def arrive():
                self.p._request(7, "item/tool/call", {"threadId": THREAD, "turnId": turn, "callId": "c1",
                                                      "tool": "angelo_inspect_scene", "arguments": {}},
                                transport=self.p._transport_token)
            events = failed(turn, THROTTLED)
            return events[:2] + [("item/tool/call", arrive)] + events[2:]

        rpc = self.script(call, answered)
        with mock.patch("angelo_agent.codex_delivery.dispatch_dynamic_call") as dispatch:
            outcome, _ = self.run_request()
        self.assertEqual(dispatch.call_count, 1)
        self.assertIsInstance(outcome, RpcError)
        self.assertEqual(len(rpc.turn_starts()), 1)

    def test_codex_retrying_itself_is_shown_and_never_stacked(self):
        retry = ("error", {"error": RECONNECTING, "willRetry": True, "threadId": THREAD, "turnId": "turn-1"})
        rpc = self.script(lambda turn: failed(turn, THROTTLED, before=[retry, retry]), answered)
        outcome, _ = self.run_request()
        self.assertEqual(str(outcome), codex_error_text(THROTTLED), "the final error, not 'Reconnecting'")
        self.assertEqual(len(rpc.turn_starts()), 1)
        self.assertEqual(self.statuses, [codex_retry.RETRYING])

    def test_codex_retry_that_gets_through_returns_the_live_row_to_thinking(self):
        retry = ("error", {"error": RECONNECTING, "willRetry": True, "threadId": THREAD, "turnId": "turn-1"})
        self.script(lambda turn: [retry] + answered(turn))
        self.assertEqual(self.run_request()[0], "completed")
        self.assertEqual(self.statuses, [codex_retry.RETRYING, "Thinking"])

    def test_a_retry_hint_in_the_text_changes_nothing(self):
        hinted = dict(THROTTLED, additionalDetails="Please try again in 2 minutes.")
        rpc = self.script(lambda turn: failed(turn, hinted), answered)
        self.assertEqual(self.run_request()[0], "completed")
        self.assertEqual(len(rpc.turn_starts()), 2)

    def test_stop_during_the_wait_sends_nothing_more(self):
        rpc = self.script(lambda turn: failed(turn, THROTTLED), answered)
        with mock.patch.multiple(codex_retry, RATE_SECONDS=5):
            threading.Timer(.4, self.c.stop).start()
            outcome, seconds = self.run_request()
        self.assertEqual(outcome, "interrupted")
        self.assertLess(seconds, 2)
        self.assertEqual(len(rpc.turn_starts()), 1)

    def test_voice_is_never_resent(self):
        self.context.voice = mock.Mock()
        rpc = self.script(lambda turn: failed(turn, THROTTLED), answered)
        with mock.patch.object(providers, "plan_mode", return_value=False):
            outcome, _ = self.run_request()
        self.assertIsInstance(outcome, RpcError)
        self.assertEqual(len(rpc.turn_starts()), 1)

    def test_one_resend_per_request_plan_corrections_included(self):
        rpc = self.script(lambda turn: failed(turn, THROTTLED), lambda turn: failed(turn, THROTTLED),
                          lambda turn: failed(turn, THROTTLED))
        self.assertIsInstance(self.run_request()[0], RpcError)
        with self.assertRaises(RpcError):
            self.p.plan_turn(THREAD, "A correction", self.p._plan)
        self.assertEqual(len(rpc.turn_starts()), 3, "two for the request, one for its correction: no second resend")

    def test_a_defect_in_completion_handling_never_strands_the_chat(self):
        with mock.patch.object(codex_retry, "PER_REQUEST", 0), \
             mock.patch.object(providers, "_codex_error_text", side_effect=TypeError("defect")), \
             mock.patch("sys.stderr") as stderr:
            rpc = self.script(lambda turn: failed(turn, THROTTLED))
            outcome, seconds = self.run_request()
            for worker in rpc.delivered:
                worker.join(2)  # The defect is printed after the turn has already ended.
        self.assertIn("Codex event handling failed", "".join(call.args[0] for call in stderr.write.call_args_list))
        self.assertIsInstance(outcome, RpcError)
        self.assertLess(seconds, 2)


class Codex163TurnEndTests(ProviderFixture):
    """Turn endings codex-cli 0.163 changed: each ends the request at once, says why, and the chat goes on."""

    TOOL_ITEM = {"type": "dynamicToolCall", "id": "c1", "namespace": None, "tool": "angelo_inspect_scene",
                 "arguments": {}, "status": "inProgress", "contentItems": None, "success": None, "durationMs": None}

    def test_an_incomplete_response_ends_the_turn_at_once_and_says_why(self):
        delta = ("item/agentMessage/delta", {"threadId": THREAD, "turnId": "turn-1", "itemId": "m", "delta": '{"reply":"Bui'})
        for before in ((), (delta,)):
            with self.subTest(output=bool(before)):
                rpc = self.script(lambda turn: failed(turn, INCOMPLETE, before=list(before)), answered)
                outcome, seconds = self.run_request()
                self.assertIsInstance(outcome, RpcError)
                self.assertEqual(str(outcome), codex_error_text(INCOMPLETE))
                self.assertTrue(str(outcome).startswith("The model stopped before finishing its reply (max_output_tokens)."))
                self.assertTrue(str(outcome).endswith(INCOMPLETE["message"]), "Codex's own words are kept")
                self.assertEqual(len(rpc.turn_starts()), 1, "Never resent: the same request would stop the same way")
                self.assertLess(seconds, 2)
                self.assertTrue(self.p.done.is_set())
        self.assertIsNone(codex_retry.delay(INCOMPLETE))
        self.assertEqual(notice_kinds.kind({"role": "error", "text": codex_error_text(INCOMPLETE)}), "unknown",
                         "Not a usage limit, throttle or overload")
        filtered = {"message": "stream disconnected before completion: Incomplete response returned, reason: content_filter"}
        self.assertEqual(codex_error_text(filtered), filtered["message"], "The content filter keeps its own text")

    def test_the_chat_shows_the_cut_off_reply_and_its_next_message_works(self):
        self.c.provider = self.p
        self.script(lambda turn: failed(turn, INCOMPLETE))

        def connect(context):
            self.p.bind_request(context)
            return THREAD

        with mock.patch.object(self.c, "_connect_runtime", side_effect=connect):
            self.c._run("Build the whole castle in one step.")
            self.c.pump_events()
            errors = [m["text"] for m in self.c.state["messages"] if m["role"] == "error"]
            self.assertEqual(len(errors), 1)
            self.assertIn("max_output_tokens", errors[0])
            self.assertEqual(self.c.status, "Failed")
            # The failed request closed its provider, as after any failure: the next message gets a working one.
            self.c.busy, self.c.cancelled = True, False
            self.c._begin_metrics()
            self.c.provider, self.p._transport_token = self.p, object()
            self.script(answered)
            self.c._run("Build the gate first.")
            self.c.pump_events()
        self.assertEqual(self.c.status, "Completed")

    def test_a_stop_during_a_dynamic_tool_call_ends_the_turn_and_the_chat_goes_on(self):
        """0.163 completes the waiting call as failed itself and drops a late answer (openai/codex#51556)."""
        def calling(turn):
            def arrive():
                self.p._request(7, "item/tool/call", {"threadId": THREAD, "turnId": turn, "callId": "c1",
                                                      "tool": "angelo_inspect_scene", "arguments": {}},
                                transport=self.p._transport_token)
            return [("turn/started", {"threadId": THREAD, "turn": {"id": turn, "status": "inProgress"}}),
                    ("item/started", {"threadId": THREAD, "turnId": turn, "item": self.TOOL_ITEM}),
                    ("item/tool/call", arrive)]

        def interrupted(turn):
            failed_call = dict(self.TOOL_ITEM, status="failed", success=False, durationMs=180)
            return [("item/completed", {"threadId": THREAD, "turnId": turn, "item": failed_call}),
                    ("serverRequest/resolved", {"threadId": THREAD, "requestId": 7}),
                    ("turn/completed", {"threadId": THREAD, "turn": {"id": turn, "status": "interrupted", "error": None,
                                                                     "durationMs": 200}})]

        self.c.provider = self.p
        rpc = self.p.rpc = InterruptingRPC(self.p, [calling, answered], interrupted)
        threading.Timer(.3, self.c.stop).start()
        outcome, seconds = self.run_request("Inspect the scene.")
        self.assertEqual(outcome, "interrupted")
        self.assertLess(seconds, 2, "Ends with Codex's turn/completed, never a watchdog")
        for worker in rpc.delivered:
            worker.join(2)
        self.assertEqual([method for method, _ in rpc.requests], ["turn/start", "turn/interrupt"])
        self.assertEqual(rpc.responses, [], "No late answer to a call Codex already ended")
        self.assertEqual(self.c.pending, {})
        self.c.cancelled = False
        self.c._begin_metrics()
        self.p.bind_request(self.c.runtime_context())
        self.assertEqual(self.p.run(THREAD, "Inspect it again."), "completed")


class ControllerTests(ProviderFixture):
    def run_through_controller(self, *outcomes):
        self.c.provider = self.p
        self.script(*outcomes)

        def connect(context):
            self.p.bind_request(context)
            return THREAD

        with mock.patch.object(self.c, "_connect_runtime", side_effect=connect):
            self.c._run("Stage a PREVIEW only that moves 'Eval Cube' to (3, 0, 0).")
        self.c.pump_events()

    def test_the_chat_shows_the_notice_and_the_request_finishes_failed(self):
        with mock.patch.object(codex_retry, "PER_REQUEST", 0):
            self.run_through_controller(lambda turn: failed(turn, THROTTLED))
        errors = [m for m in self.c.state["messages"] if m["role"] == "error"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(notice_kinds.kind(errors[0]), "rate")
        self.assertEqual(self.c.status, "Failed")
        self.assertIsNotNone(limits.angelo_limit(self.c.state["messages"], None), "the harness's rate-limited trial")

    def test_a_usage_limit_gets_its_own_notice(self):
        self.run_through_controller(lambda turn: failed(turn, USAGE))
        errors = [m for m in self.c.state["messages"] if m["role"] == "error"]
        self.assertEqual([notice_kinds.kind(m) for m in errors], ["usage"])


def _codex_binary():
    """A codex executable to drive for real: ``ANGELO_TEST_CODEX``, else the agent runtime when installed."""
    candidate = os.environ.get("ANGELO_TEST_CODEX")
    if not candidate:
        try:
            from angelo_agent import agent_runtime
            candidate = agent_runtime.bundled()
        except Exception:  # noqa: BLE001 - no runtime: the class is skipped.
            candidate = None
    return candidate if candidate and os.access(candidate, os.X_OK) else None


def _codex_version(binary):
    """``(major, minor)`` that ``binary --version`` names, or ``(0, 0)``."""
    try:
        said = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return (0, 0)
    found = re.search(r"(\d+)\.(\d+)\.\d+", said or "")
    return (int(found.group(1)), int(found.group(2))) if found else (0, 0)


class _Responses:
    """A local Responses endpoint: each POST takes the next answer (``"429"``, ``"usage"``, ``"incomplete"``
    or ``"ok"``)."""

    def __init__(self, answers):
        self.answers, self.bodies = list(answers), []
        endpoint = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self._send(200, "application/json", b'{"models":[]}')

            def do_POST(self):
                endpoint.bodies.append(json.loads(self.rfile.read(int(self.headers.get("content-length", 0)))))
                answer = endpoint.answers.pop(0) if endpoint.answers else "ok"
                if answer == "429":
                    self._send(429, "application/json", b'{"error":{"type":"rate_limit_exceeded","message":"slow"}}')
                elif answer == "incomplete":  # The output limit reached mid-reply.
                    events = [{"type": "response.created", "response": {"id": "r"}},
                              {"type": "response.incomplete", "response": {
                                  "id": "r", "incomplete_details": {"reason": "max_output_tokens"},
                                  "usage": {"input_tokens": 10, "input_tokens_details": {"cached_tokens": 0},
                                            "output_tokens": 5, "output_tokens_details": {"reasoning_tokens": 0},
                                            "total_tokens": 15}}}]
                    self._send(200, "text/event-stream", "".join(
                        "event: %s\ndata: %s\n\n" % (e["type"], json.dumps(e)) for e in events).encode())
                elif answer == "usage":
                    self._send(429, "application/json", json.dumps({"error": {
                        "type": "usage_limit_reached", "message": "limit", "resets_at": int(time.time()) + 3600}}).encode())
                else:
                    item = {"type": "message", "role": "assistant", "id": "msg", "content": [
                        {"type": "output_text", "text": PLAN_REPLY, "annotations": []}]}
                    events = [{"type": "response.created", "response": {"id": "r"}},
                              {"type": "response.output_item.done", "output_index": 0, "item": item},
                              {"type": "response.completed", "response": {"id": "r", "usage": {
                                  "input_tokens": 10, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 5,
                                  "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 15}}}]
                    self._send(200, "text/event-stream", "".join(
                        "event: %s\ndata: %s\n\n" % (e["type"], json.dumps(e)) for e in events).encode())

            def _send(self, status, kind, body):
                self.send_response(status)
                self.send_header("content-type", kind)
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True

        self.server = Server(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


class FakeAppServerEnvironmentTests(unittest.TestCase):
    """The real app-server below runs in ``codex_prompt_probe.fake_command``'s environment, on both platforms.

    On Windows it keeps the system variables (without SystemRoot codex.exe never finished a turn and the tests
    waited ten minutes each) and points the per-user folders at the probe's own; nowhere does it keep a key or a
    person's folders.
    """

    SYSTEM = {"SYSTEMROOT": r"C:\Windows", "WINDIR": r"C:\Windows", "SYSTEMDRIVE": "C:",
              "COMSPEC": r"C:\Windows\system32\cmd.exe", "PATHEXT": ".COM;.EXE;.BAT", "PROGRAMDATA": r"C:\ProgramData",
              "TEMP": r"C:\Temp", "TMP": r"C:\Temp"}
    PERSON = {"USERPROFILE": r"C:\Users\artist", "APPDATA": r"C:\Users\artist\AppData\Roaming",
              "LOCALAPPDATA": r"C:\Users\artist\AppData\Local", "OPENAI_API_KEY": "not-a-real-key"}

    def environment(self, platform):
        import codex_prompt_probe
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        out = Path(folder.name)
        launched = dict(self.SYSTEM, PATH="bin", CODEX_HOME=str(out / "state"), **self.PERSON)
        with mock.patch.object(codex_prompt_probe, "codex_command", return_value=(["codex", "app-server"], launched)), \
                mock.patch.object(codex_prompt_probe.os, "name", platform):
            return out, codex_prompt_probe.fake_command("codex", out, 4321)[1]

    def test_windows_keeps_the_system_and_gets_its_own_profile_folders(self):
        out, env = self.environment("nt")
        self.assertEqual({key: env[key] for key in self.SYSTEM}, self.SYSTEM)
        self.assertEqual(env["USERPROFILE"], str(out / "home"))
        for key in ("APPDATA", "LOCALAPPDATA"):
            self.assertTrue(Path(env[key]).is_dir() and Path(env[key]).is_relative_to(out / "home"), key)
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertEqual((env["PATH"], env["HOME"]), ("bin", str(out / "home")))

    def test_posix_keeps_only_what_it_always_kept(self):
        out, env = self.environment("posix")
        self.assertEqual(sorted(env), ["ANGELO_PROBE_KEY", "CODEX_HOME", "HOME", "PATH"])


@unittest.skipUnless(_codex_binary(), environment_skip("no codex executable (set ANGELO_TEST_CODEX to the agent runtime's codex)"))
class RealAppServerTests(ProviderFixture):
    """The real codex app-server, answered 429 by a local endpoint: the gate turn above, end to end."""

    def connect(self, answers):
        # A silent app-server fails the test in half a minute with the host's own idle error, never after the
        # product's ten minutes (a Windows environment without SystemRoot was one: codex.exe never finished a turn).
        from angelo_agent import turn_activity
        idle = mock.patch.object(turn_activity, "IDLE_SECONDS", 30)
        idle.start()
        self.addCleanup(idle.stop)
        from angelo_agent.protocol import JsonRpcProcess
        from codex_prompt_probe import fake_command
        self.endpoint = _Responses(answers)
        self.addCleanup(self.endpoint.server.shutdown)
        out = Path(self.temp.name) / "codex"
        for name in ("state", "home", "workspace"):
            (out / name).mkdir(parents=True, exist_ok=True)
        command, env = fake_command(_codex_binary(), out, self.endpoint.server.server_address[1])
        token = self.p._transport_token
        rpc = JsonRpcProcess(command, env=env, cwd=str(out / "workspace"),
                             on_event=lambda method, params: self.p._event(method, params, transport=token),
                             on_request=lambda identity, method, params: self.p._request(identity, method, params,
                                                                                         transport=token))
        self.addCleanup(rpc.close)
        rpc.request("initialize", {"clientInfo": {"name": "angelo_blender", "title": "Angelo", "version": "0.1.0"},
                                   "capabilities": {"experimentalApi": True}})
        rpc.notify("initialized")
        thread = rpc.request("thread/start", {"cwd": str(out / "workspace"), "modelProvider": "angelo_probe",
                                              "sandbox": "read-only", "approvalPolicy": "on-request",
                                              "model": "gpt-6-astra", "baseInstructions": "Answer."})["thread"]["id"]
        self.p.rpc, self.p.thread_id = rpc, thread
        self.p.bind_request(self.context)
        return thread

    def send(self, thread):
        started = time.monotonic()
        try:
            return self.p.run(thread, "Move the cube."), time.monotonic() - started
        except RpcError as error:
            return error, time.monotonic() - started

    def test_a_429_twice_ends_the_turn_with_too_many_requests(self):
        outcome, seconds = self.send(self.connect(["429", "429"]))
        self.assertIsInstance(outcome, RpcError)
        self.assertEqual(notice_kinds.kind({"text": str(outcome)}), "rate")
        self.assertEqual(len(self.endpoint.bodies), 2)
        self.assertLess(seconds, 3)

    def test_a_usage_limit_ends_the_turn_at_once(self):
        outcome, seconds = self.send(self.connect(["usage"]))
        self.assertEqual(notice_kinds.kind({"text": str(outcome)}), "usage")
        self.assertEqual(len(self.endpoint.bodies), 1)
        self.assertLess(seconds, 2)

    def test_an_incomplete_response_ends_the_turn_at_once(self):
        if _codex_version(_codex_binary()) < (0, 163):
            self.skipTest(environment_skip("codex-cli 0.163 or later ends a turn on an incomplete response (openai/codex#53451)"))
        outcome, seconds = self.send(self.connect(["incomplete", "ok"]))
        self.assertIsInstance(outcome, RpcError)
        self.assertTrue(str(outcome).startswith("The model stopped before finishing its reply (max_output_tokens)."))
        self.assertEqual(len(self.endpoint.bodies), 1, "Never retried by Codex or resent by Angelo")
        self.assertLess(seconds, 3)

    def test_a_429_then_an_answer_completes_and_the_model_sees_the_message_once(self):
        outcome, _ = self.send(self.connect(["429", "ok"]))
        self.assertEqual(outcome, "completed")
        self.assertEqual(len(self.endpoint.bodies), 2)
        users = [json.dumps(item.get("content")) for item in self.endpoint.bodies[1]["input"] if item.get("role") == "user"]
        self.assertEqual(sum("Move the cube." in text for text in users), 1)
        self.assertTrue(any("[Host note]" in text for text in users))


if __name__ == "__main__":
    unittest.main()
