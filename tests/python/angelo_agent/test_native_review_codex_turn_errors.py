# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Adversarial regression tests of the Codex turn-error handling (throttles, resends, Stop, exits).

Each ``Fixed*`` class failed before its fix and now asserts the fixed behaviour; each ``Held*`` test
attacks a claim that held and keeps attacking it. ``RealCodex*`` drives the real ``codex app-server``
against a local endpoint (``ANGELO_TEST_CODEX``; no account, network or inference) and is skipped without one.
"""

import http.server
import json
import math
import random
import socketserver
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from angelo_agent import codex_retry, notice_kinds, provider_errors  # noqa: E402
from angelo_agent.protocol import RpcError  # noqa: E402
import eval_provider_limits as limits  # noqa: E402
from test_native_codex_turn_errors import (  # noqa: E402
    PLAN_REPLY, THREAD, THROTTLED, ProviderFixture, _codex_binary, answered, failed)


def throttled(message=None, details=None):
    return dict(THROTTLED, message=message or THROTTLED["message"], additionalDetails=details)


class FixedRetryHints(unittest.TestCase):
    """Was: ``codex_retry._HINT`` read Go-style compound durations wrong ("12m0s" waited 1 s, "2h13m" was not
    seen) and was quadratic in whitespace under the provider's scope lock. codex 0.162 never passes the
    server's hint into a resendable error, so no hint is read: the waits are fixed."""

    def test_compound_durations_change_nothing(self):
        for text in ("Rate limit reached for requests. Please try again in 12m0s.", "try again in 10h30m",
                     "You exceeded your rate limit. Please try again in 2h13m."):
            with self.subTest(text=text):
                self.assertEqual(codex_retry.delay(throttled(text), rng=lambda: 0), codex_retry.RATE_SECONDS)

    def test_the_decision_is_constant_time_under_the_scope_lock(self):
        error = throttled("try again in" + " " * 100000 + "x")
        started = time.perf_counter()
        self.assertEqual(codex_retry.delay(error, rng=lambda: 0), codex_retry.RATE_SECONDS)
        self.assertLess(time.perf_counter() - started, 0.01)
        self.assertFalse(hasattr(codex_retry, "hinted_seconds"))


class HeldNeverRaises(unittest.TestCase):
    """Fuzz: the classifiers and the harness's limit readers never raise for any JSON value."""

    def value(self, rng, depth=0):
        choices = [None, True, False, 0, -1, 429, 503, 2 ** 70, 1.5, float("nan"), float("inf"), "", "429",
                   "usageLimitExceeded", "rateLimitExceeded", "serverOverloaded", "responseTooManyFailedAttempts",
                   "try again in 5s", "Retry-After: 99999999999999999999", "Too many requests (HTTP 429): x"]
        if depth < 4 and rng.random() < .45:
            if rng.random() < .5:
                return [self.value(rng, depth + 1) for _ in range(rng.randrange(3))]
            keys = ["message", "codexErrorInfo", "additionalDetails", "httpStatusCode", "willRetry", "error",
                    "turn", "status", "responseTooManyFailedAttempts", "x"]
            return {rng.choice(keys): self.value(rng, depth + 1) for _ in range(rng.randrange(4))}
        return rng.choice(choices)

    def test_twenty_thousand_random_shapes(self):
        rng = random.Random(1015)
        for _ in range(20000):
            error = self.value(rng)
            provider_errors.codex_error_info(error)
            provider_errors.codex_error_kind(error)
            text = provider_errors.codex_error_text(error)
            notice_kinds.kind({"role": "error", "text": text})
            seconds = codex_retry.delay(error)
            self.assertTrue(seconds is None or (math.isfinite(seconds) and codex_retry.BUSY_SECONDS <= seconds
                                                <= codex_retry.RATE_SECONDS + codex_retry.JITTER_SECONDS))
            codex_retry.kind(error)
            # The harness readers take Codex's containers (a dict ``params``, a dict ``turn``); any leaves.
            # (A non-dict ``completed`` or ``turn`` raised before this change too: harness-only, not product.)
            limits.codex_limit([{"method": "error", "params": {"error": error, "willRetry": self.value(rng)}},
                                self.value(rng)],
                               {"turn": {"status": self.value(rng), "error": self.value(rng)}})
            limits.angelo_limit([{"role": "error", "text": text}, self.value(rng)], self.value(rng), self.value(rng))


class FixedStopRaceAfterTheWait(ProviderFixture):
    """Was: Stop landing after the wait's last poll and before the resend's ``turn/start`` raised
    ``TurnStartAbandoned`` and the chat said "Your message wasn't sent", offering back a message Codex already
    held. Now the request ends as stopped and nothing more is sent."""

    def test_the_request_ends_as_stopped(self):
        self.c.provider = self.p
        rpc = self.script(lambda turn: failed(turn, THROTTLED), answered)

        def wait(seconds, stopped, emit=None, **kwargs):
            self.assertFalse(stopped())
            self.c.stop()  # The person clicks Stop just after the last poll.
            return True

        def connect(context):
            self.p.bind_request(context)
            return THREAD

        with mock.patch.object(codex_retry, "wait", side_effect=wait), \
                mock.patch.object(self.c, "_connect_runtime", side_effect=connect):
            self.c._run("Make the cube red.")
        self.c.pump_events()
        self.assertEqual(len(rpc.turn_starts()), 1, "the first attempt reached Codex; the resend was never sent")
        texts = [m.get("text") for m in self.c.state["messages"]]
        self.assertNotIn("Your message wasn't sent. Send it again when you're ready.", texts)
        self.assertFalse(any(m.get("role") == "error" for m in self.c.state["messages"]))
        self.assertEqual(self.c.status, "Interrupted")


class FixedProcessLostDuringTheWait(ProviderFixture):
    """Was: an app-server that exited during the wait still got the resend's ``turn/start``, and the request
    failed with "Agent process has disconnected" (an ordinary failure to the harness). Now nothing more is
    sent and the request fails with the throttle it met: the "Too many requests" card and the limit evidence."""

    def lose_during_the_wait(self, closes_before_turn_start=True):
        rpc = self.script(lambda turn: failed(turn, THROTTLED), answered)
        starts = []
        original = rpc.request

        def request(method, params, **kwargs):
            if method == "turn/start":
                starts.append(params)
                if rpc.closed.is_set():
                    raise RpcError("Agent process has disconnected")  # protocol.JsonRpcProcess.write
            return original(method, params, **kwargs)

        rpc.request = request

        def die():
            rpc.close()
            self.p._event("host/disconnected", {}, transport=self.p._transport_token)

        with mock.patch.multiple(codex_retry, RATE_SECONDS=.6):
            threading.Timer(.3, die).start()
            outcome, _ = self.run_request()
        return outcome, starts

    def test_the_throttle_stands_and_nothing_more_is_sent(self):
        outcome, starts = self.lose_during_the_wait()
        self.assertEqual(len(starts), 1, "no resend on the closed process")
        self.assertEqual(str(outcome), provider_errors.codex_error_text(THROTTLED))
        self.assertEqual(notice_kinds.kind({"role": "error", "text": str(outcome)}), "rate")
        self.assertIsNotNone(limits.angelo_limit([{"role": "error", "text": str(outcome)}], None))

    def test_a_process_lost_while_the_resend_is_written_keeps_the_throttle_too(self):
        rpc = self.script(lambda turn: failed(turn, THROTTLED), answered)
        original = rpc.request

        def request(method, params, **kwargs):
            if method == "turn/start" and len(rpc.turn_starts()) == 1 and not rpc.closed.is_set():
                rpc.close()  # Gone between the after-wait check and the write.
                raise RpcError("Agent process has disconnected")
            return original(method, params, **kwargs)

        rpc.request = request
        outcome, _ = self.run_request()
        self.assertEqual(str(outcome), provider_errors.codex_error_text(THROTTLED))


class HeldStopAndOwnership(ProviderFixture):
    def test_closing_the_chat_during_the_wait_sends_nothing_more(self):
        rpc = self.script(lambda turn: failed(turn, THROTTLED), answered)
        with mock.patch.multiple(codex_retry, RATE_SECONDS=5):
            threading.Timer(.3, lambda: setattr(self.c, "_closing", True)).start()
            outcome, seconds = self.run_request()
        self.assertEqual(outcome, "interrupted")
        self.assertLess(seconds, 2)
        self.assertEqual(len(rpc.turn_starts()), 1)

    def test_a_document_change_during_the_wait_sends_nothing_more(self):
        rpc = self.script(lambda turn: failed(turn, THROTTLED), answered)
        with mock.patch.multiple(codex_retry, RATE_SECONDS=5):
            threading.Timer(.3, lambda: self.c.set_document(("another.blend", 4))).start()
            outcome, seconds = self.run_request()
        self.assertEqual(outcome, "interrupted")
        self.assertLess(seconds, 2)
        self.assertEqual(len(rpc.turn_starts()), 1)

    def test_a_tool_call_replayed_after_the_turn_start_reply_still_blocks_the_resend(self):
        def call(turn):
            def arrive():
                self.p._request(9, "item/tool/call", {"threadId": THREAD, "turnId": turn, "callId": "c9",
                                                      "tool": "angelo_inspect_scene", "arguments": {}},
                                transport=self.p._transport_token)
            events = failed(turn, THROTTLED)
            return events[:2] + [("item/tool/call", arrive)] + events[2:]

        rpc = self.script(call, answered, before_reply=True)  # Deferred by the scope, replayed by send().
        with mock.patch("angelo_agent.codex_delivery.dispatch_dynamic_call") as dispatch:
            outcome, _ = self.run_request()
        self.assertEqual(dispatch.call_count, 1)
        self.assertIsInstance(outcome, RpcError)
        self.assertEqual(len(rpc.turn_starts()), 1)

    def test_context_compaction_alone_blocks_the_resend(self):
        compaction = ("item/started", {"threadId": THREAD, "turnId": "turn-1",
                                       "item": {"type": "contextCompaction", "id": "c"}})
        rpc = self.script(lambda turn: failed(turn, THROTTLED, before=[compaction]), answered)
        self.assertIsInstance(self.run_request()[0], RpcError)
        self.assertEqual(len(rpc.turn_starts()), 1)


class FixedCorrectionTurnNote(ProviderFixture):
    """Was: a plan correction turn after committed operations was resent with "... and nothing ran", though
    those operations did run. The note now says nothing ran in the failed turn and that what the message above
    reports as done still stands."""

    def test_the_correction_resend_never_reads_as_nothing_applied(self):
        rpc = self.script(lambda turn: failed(turn, THROTTLED), answered)
        self.p._retries_left = codex_retry.PER_REQUEST  # The request's first turn did not use it.
        status = self.p.plan_turn(THREAD, "operations[0] and [1] committed (receipts ...); operations[2] "
                                          "failed. Plan only the edits still needed.", None)
        self.assertEqual(status, "completed")
        self.assertEqual(rpc.turn_starts()[1]["input"], [{"type": "text", "text": codex_retry.NOTE}])
        self.assertIn("nothing ran in that turn", codex_retry.NOTE)
        self.assertIn("reports as already done still stands", codex_retry.NOTE)
        self.assertNotIn("and nothing ran.", codex_retry.NOTE)


class FixedHarnessGradesBeforeTheLimit(unittest.TestCase):
    """Was: the in-app driver checked ``angelo_limit`` before grading, so a throttled-then-failed request was
    never graded; and any error row naming "429" (host- or tool-authored) was a limit. Now the task is graded
    and its grade recorded before it is marked invalid (exit code 3 stays), and only rows a provider wrote
    count as limit evidence."""

    def body(self, name, following):
        source = (HERE / "eval_live_agent_session.py").read_text()
        return source[source.index("    def %s(self" % name):source.index("    def %s(self" % following)]

    def test_a_limited_task_is_graded_before_it_is_marked_invalid(self):
        running = self.body("on_running", "on_jobs")
        self.assertNotIn('self.complete("invalid"', running, "the limit no longer ends the task before grading")
        self.assertLess(running.index("limits.angelo_limit("), running.index("return self.grade()"))
        grade = self.body("grade", "workspace")
        self.assertLess(grade.index('self.record["host_check_passed"] = passed'), grade.index('self.complete("invalid"'))
        self.assertIn("grade=eval_grading_live.grading.state(dict(self.record, status=status), task)", grade)
        complete = self.body("complete", "finish")
        self.assertIn('self.index = len(self.tasks) if status == "invalid"', complete,
                      "and the run still stops: no further billed message")

    def test_the_recorded_grade_is_the_graded_one(self):
        import eval_grading
        record = {"status": "invalid", "host_check_passed": False, "invalid": limits.INVALID}
        self.assertEqual(eval_grading.state(dict(record, status="failed")), "failed")
        self.assertEqual(eval_grading.state(dict(record, grade="failed")), "failed")
        self.assertEqual(eval_grading.state(dict(record, host_check_passed=True, status="completed")), "passed")

    def test_a_resent_request_that_then_failed_is_still_invalid(self):
        rows = [{"role": "assistant", "text": "I moved the wrong cube."},
                {"role": "error", "text": "Host rejected the plan before running any operation: ..."}]
        usage = {"input_tokens": 9000, "output_tokens": 300}
        self.assertIsNone(limits.angelo_limit(rows, usage, {}))
        self.assertIsNotNone(limits.angelo_limit(rows, usage, {"provider_retries_rate": 1}))

    def test_host_plan_and_tool_text_naming_a_limit_is_never_evidence(self):
        used = {"input_tokens": 9000, "output_tokens": 300}
        for row in ({"role": "error", "text": "Host rejected the plan: object 'Cube 429' not found"},
                    {"role": "error", "text": "Native plan iteration limit reached; inspect before continuing"},
                    {"role": "error", "text": "Scene object limit reached"},
                    {"role": "activity", "text": "HTTP 429 Too Many Requests from the asset server"},
                    {"role": "assistant", "text": "The API's rate limit is 429 requests a minute."}):
            with self.subTest(row=row):
                self.assertIsNone(limits.angelo_limit([row], used))
                self.assertIsNone(limits.angelo_limit([row], {"input_tokens": 0, "output_tokens": 0}))

    def test_provider_rows_remain_evidence(self):
        used = {"input_tokens": 9000, "output_tokens": 300}
        for text in ("Too many requests (HTTP 429): exceeded retry limit, last status: 429 Too Many Requests",
                     "Provider usage limit reached (usageLimitExceeded): You've hit your usage limit.",
                     'API Error: 429 {"type":"error","error":{"type":"rate_limit_error","message":"slow"}}',
                     "rate_limit_error: 429", "You've hit your session limit · resets 8:10am (Asia/Tokyo)",
                     "Claude AI usage limit reached|1760040000"):
            with self.subTest(text=text):
                self.assertIsNotNone(limits.angelo_limit([{"role": "error", "text": text}], used))
        synthetic = [{"role": "assistant", "text": "You've hit your session limit · resets 8:10am"}]
        self.assertIsNotNone(limits.angelo_limit(synthetic, {"input_tokens": 0, "output_tokens": 0}))
        self.assertIsNone(limits.angelo_limit(synthetic, used), "a model's reply quoting a limit, tokens used")


class HeldComparatorBusyAsymmetryIsDocumented(unittest.TestCase):
    """An overloaded provider: Angelo resends once and the trial stays valid; the MCP side's Codex turn fails
    and is graded. Kept as designed (an overload is not the account's limit) and disclosed in the benchmark
    doc (gpt_launch.rst, "Benchmark harness (Codex)")."""

    def test_mcp_side_overload_is_a_failure_and_angelo_resends(self):
        error = {"message": "Selected model is at capacity. Please try a different model.",
                 "codexErrorInfo": "serverOverloaded", "additionalDetails": None}
        completed = {"turn": {"status": "failed", "error": error}}
        self.assertIsNone(limits.codex_limit([{"method": "error", "params": {"error": error, "willRetry": False}}],
                                             completed))
        self.assertEqual(codex_retry.kind(error), "busy")
        self.assertIsNone(limits.angelo_limit([], None, {"provider_retries_busy": 1}))

    def test_the_benchmark_doc_discloses_it(self):
        doc = (HERE.parents[2] / "doc/angelo/gpt_launch.rst").read_text()
        self.assertIn("Asymmetry: an overloaded provider", doc)
        self.assertIn("In practice ``busy`` means ``serverOverloaded``", doc)


class _Endpoint:
    """A local Responses endpoint; each POST takes the next named answer."""

    @staticmethod
    def sse(events):
        return 200, "text/event-stream", "".join("event: %s\ndata: %s\n\n" % (e["type"], json.dumps(e))
                                                 for e in events).encode()

    def answer(self, name):
        created = {"type": "response.created", "response": {"id": "r"}}
        if name == "429":
            return 429, "application/json", b'{"error":{"type":"rate_limit_exceeded","message":"slow"}}'
        if name == "overloaded":
            return self.sse([created, {"type": "response.failed", "response": {"id": "r", "error": {
                "code": "server_is_overloaded", "message": "The server is overloaded."}}}])
        tokens = 90000 if name == "big" else 10
        item = {"type": "message", "role": "assistant", "id": "msg", "content": [
            {"type": "output_text", "text": PLAN_REPLY, "annotations": []}]}
        return self.sse([created, {"type": "response.output_item.done", "output_index": 0, "item": item},
                         {"type": "response.completed", "response": {"id": "r", "usage": {
                             "input_tokens": tokens, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 5,
                             "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": tokens + 5}}}])

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
                self._send(*endpoint.answer(endpoint.answers.pop(0) if endpoint.answers else "ok"))

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


@unittest.skipUnless(_codex_binary(), "no codex executable (set ANGELO_TEST_CODEX to the agent runtime's codex)")
class RealCodexReview(ProviderFixture):
    def connect(self, answers, overrides=()):
        # A silent app-server fails the test in half a minute with the host's own idle error, never after the
        # product's ten minutes (a Windows environment without SystemRoot was one: codex.exe never finished a turn).
        from angelo_agent import turn_activity
        idle = mock.patch.object(turn_activity, "IDLE_SECONDS", 30)
        idle.start()
        self.addCleanup(idle.stop)
        from angelo_agent.protocol import JsonRpcProcess
        from codex_prompt_probe import fake_command
        self.endpoint = _Endpoint(answers)
        self.addCleanup(self.endpoint.server.shutdown)
        out = Path(self.temp.name) / "codex"
        for name in ("state", "home", "workspace"):
            (out / name).mkdir(parents=True, exist_ok=True)
        command, env = fake_command(_codex_binary(), out, self.endpoint.server.server_address[1], overrides)
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

    def send(self, thread, text):
        try:
            return self.p.run(thread, text)
        except RpcError as error:
            return error

    def users(self, body):
        return [json.dumps(item.get("content")) for item in body["input"] if item.get("role") == "user"]

    def test_held_an_overloaded_turn_is_resent_once_and_the_model_sees_the_message_once(self):
        thread = self.connect(["overloaded", "ok"])
        self.assertEqual(self.send(thread, "Move the cube."), "completed")
        self.assertEqual(len(self.endpoint.bodies), 2)
        users = self.users(self.endpoint.bodies[1])
        self.assertEqual(sum("Move the cube." in text for text in users), 1)
        self.assertTrue(any("[Host note]" in text for text in users))
        self.assertEqual(self.c._request_metrics.counts.get("provider_retries_busy"), 1)

    def test_held_an_automatic_compaction_before_a_429_blocks_the_resend(self):
        thread = self.connect(["big", "ok", "429"], overrides=("model_auto_compact_token_limit=1000",
                                                               "model_context_window=100000"))
        self.assertEqual(self.send(thread, "First."), "completed")
        self.c._begin_metrics()
        self.p.bind_request(self.c.runtime_context())
        outcome = self.send(thread, "Second.")
        self.assertIsInstance(outcome, RpcError)
        self.assertEqual(len(self.endpoint.bodies), 3, "warm-up, compaction, the 429: no resend")


if __name__ == "__main__":
    unittest.main()
