# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A call asked for again is answered with exactly what its first delivery was sent, error included.

The launcher asks Angelo again under the same call ID every ``ROUND_WAIT`` (25 s) while a call runs, so for any call
longer than that the app only ever sees a re-ask's answer. The call never runs twice: the re-ask gets the first answer.
Without the window that answer is made after the call ran (its save or reload adds a note, or answers with the error
when it raised: ``headless_answers``). These tests drive the launcher's own re-asks through the pipes with a shortened
``ROUND_WAIT`` and assert on what the app receives. They also cover the host's admission checks (a re-ask never runs,
so no check may answer in its place), forgetting calls past the bound (never an unanswered one, and a forgotten one is
said to be not kept), and ``finish`` alone (one deadline per call; one failing delivery never drops the rest).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json  # noqa: E402
import os  # noqa: E402
import queue  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest.mock import Mock, patch  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from host_test_policy import environment_skip  # noqa: E402
from angelo_agent import claude_direct, external_apps, external_mcp, headless_answers  # noqa: E402
import test_native_headless_script_files as script_harness  # noqa: E402  (a module: its tests are not collected here)

CUBE = {"name": "Box", "size": 1, "location": [0, 0, 0]}
SAVE_FAILED = "could not save or reload after this call"
RAW = {"content": [{"type": "text", "text": "{\"ok\": true}"}], "isError": False}


@unittest.skipIf(sys.platform == "win32", environment_skip("Headless sessions are macOS and Linux for now"))
class PipeReplayTests(unittest.TestCase):
    """The headless host end to end through the launcher's pipes: the launcher itself asks again while the call runs."""
    tick, call, open = (script_harness.HeadlessScriptFileTests.tick, script_harness.HeadlessScriptFileTests.call,
                        script_harness.HeadlessScriptFileTests.open)
    setUp = script_harness.HeadlessScriptFileTests.setUp
    start_child = script_harness.HeadlessScriptFileTests.start_child

    def slow_calls(self, during=None):
        """The edit runs on the main thread for longer than the launcher's (shortened) round, so the launcher asks
        again while it runs; ``during`` happens inside it. Returns the patches and the launcher's request count."""
        original, rounds = self.executor.execute, []

        def slow(name, arguments):
            if name == "angelo_create_cube":
                time.sleep(4 * external_mcp.ROUND_WAIT)
                if during is not None:
                    during()
            return original(name, arguments)
        asking = self.server.headless_request

        def counted(*args, **kwargs):
            rounds.append(1)
            return asking(*args, **kwargs)
        return (patch.object(external_mcp, "ROUND_WAIT", 0.25), patch.object(self.executor, "execute", slow),
                patch.object(self.server, "headless_request", counted)), rounds

    def test_a_call_whose_save_and_reload_raised_is_reported_failed_when_asked_again(self):
        """The audit's case on the path apps use: the answer the app receives is the save's error, not a success."""
        self.open()
        cubes = self.executor.calls.count("angelo_create_cube")
        patches, rounds = self.slow_calls()
        with patches[0], patches[1], patches[2], \
                patch.object(self.host, "save", side_effect=OSError("the disk is full, on purpose")), \
                patch.object(self.host, "reload", side_effect=RuntimeError("the reload failed, on purpose")):
            result = self.call("angelo_create_cube", CUBE)
        self.assertGreaterEqual(len(rounds), 2, "the launcher asked again while the call ran")
        self.assertTrue(result["isError"], result)
        self.assertIn(SAVE_FAILED, json.dumps(result))
        self.assertEqual(self.executor.calls.count("angelo_create_cube"), cubes + 1, "the call ran once")

    def test_a_reask_after_the_source_changed_gets_the_calls_answer_not_stale(self):
        """Break-it 2026-11-13 (must-fix 1): the host's admission checks guard running a call; a re-ask never runs, so
        a source changed on disk meanwhile must not answer "stale" in place of what the call did."""
        self.open()
        cubes = self.executor.calls.count("angelo_create_cube")
        source = self.source
        patches, rounds = self.slow_calls(during=lambda: source.write_bytes(source.read_bytes() + b" saved meanwhile"))
        with patches[0], patches[1], patches[2]:
            result = self.call("angelo_create_cube", CUBE)
        self.assertGreaterEqual(len(rounds), 2, "the launcher asked again while the call ran")
        self.assertFalse(result.get("isError"), result)   # The call's own answer: it ran and was saved.
        self.assertIn("changed on disk during this call", json.dumps(result), result)   # SOURCE_WRITTEN, not stale.
        self.assertEqual(self.executor.calls.count("angelo_create_cube"), cubes + 1, "the call ran once")


@unittest.skipIf(sys.platform == "win32", environment_skip("Headless sessions are macOS and Linux for now"))
class ForgettingTests(unittest.TestCase):
    """``ExternalApps`` past CALLS_KEPT: an unanswered call is never forgotten; a forgotten one asked again is told its
    answer is not kept, through ``handle_app_call``, and nothing runs."""
    tick, call, open = PipeReplayTests.tick, PipeReplayTests.call, PipeReplayTests.open
    setUp, start_child = PipeReplayTests.setUp, PipeReplayTests.start_child

    def test_unanswered_calls_are_never_forgotten_and_a_forgotten_one_is_not_kept(self):
        self.open()
        host, label = self.host, self.host.client.label
        running = (label, "still-running")
        host._calls[running] = {"work": Mock(), "fields": {}, "note": None}
        for index in range(external_apps.CALLS_KEPT + 5):
            host._calls[(label, "done-%d" % index)] = {"work": Mock(), "fields": {}, "note": None, "answer": RAW}
        host._forget_answered()
        self.assertIn(running, host._calls, "a call still unanswered is never forgotten")
        self.assertLessEqual(len(host._calls), external_apps.CALLS_KEPT)
        self.assertIn((label, "done-0"), host._evicted)
        replies, cubes = [], self.executor.calls.count("angelo_create_cube")
        work = SimpleNamespace(client=host.client, admitted=False, complete=replies.append,
                               payload={"id": "done-0", "tool": "angelo_create_cube", "arguments": CUBE})
        host.handle_app_call(work)
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0]["code"], "tool_failed")
        self.assertIn("did not run again", json.dumps(replies[0]["result"]))
        self.tick()
        self.assertEqual(self.executor.calls.count("angelo_create_cube"), cubes, "nothing ran")


@unittest.skipIf(sys.platform == "win32", environment_skip("Headless sessions are macOS and Linux for now"))
class RunLoopTests(unittest.TestCase):
    """``HeadlessHost.run`` itself: a held answer that raises is logged, the session keeps serving, and the deliveries
    after it are answered on the next tick (re-review S1: the raise used to end the child)."""
    tick, call, open = PipeReplayTests.tick, PipeReplayTests.call, PipeReplayTests.open
    setUp, start_child = PipeReplayTests.setUp, PipeReplayTests.start_child

    def test_a_raising_delivery_never_ends_the_session(self):
        self.open()
        host, sent, times = self.host, [], {"start": time.monotonic()}
        broken = SimpleNamespace(count_out=Mock(side_effect=TypeError("on purpose")))

        def answered(value):   # The delivery after the raising one: the session is still serving; end the loop.
            sent.append(value)
            times["answered"] = time.monotonic()
            host.end()
        host._answers.put(((host.client.label, "c1"), "angelo_inspect_scene", False, broken, RAW, sent.append))
        host._answers.put(((host.client.label, "c2"), "angelo_inspect_scene", False, None, RAW, answered))
        ui = SimpleNamespace(main_window=lambda: None, document_key=lambda: self.child_env.document)
        executor = self.executor
        # run imports BlenderExecutor from blender_tools, which needs Blender: a stand-in module gives the test's own.
        stand_in = SimpleNamespace(BlenderExecutor=lambda window: executor)
        with patch.dict(sys.modules, {"angelo_agent.blender_tools": stand_in}), \
                patch("sys.stderr", new_callable=__import__("io").StringIO) as errors:
            host.run(ui)
        times["returned"] = time.monotonic()
        took = {key: round(value - times["start"], 3) for key, value in times.items()}
        self.assertEqual(sent, [RAW], "the delivery after the raising one was answered on a later tick")
        self.assertLess(took["answered"], 5, took)
        self.assertLess(took["returned"], 30, took)   # Ending the session never waits on the raised delivery.
        self.assertIn("a held answer could not be sent", errors.getvalue())
        self.assertFalse(host._unsent)


class WindowForgettingTests(unittest.TestCase):
    """The window's answered mark (``_marked``): a call is forgotten past CALLS_KEPT only once its answer went out."""

    def test_marked_calls_are_forgotten_and_unmarked_ones_kept(self):
        host = SimpleNamespace(_calls={}, _evicted={})
        host._calls[("app", "running")] = {"work": None}
        sent = []
        for index in range(external_apps.CALLS_KEPT + 3):
            record = {"work": None}
            host._calls[("app", "done-%d" % index)] = record
            external_apps._marked(record, sent.append)(RAW)   # Its answer went out in the window.
        external_apps.ExternalApps._forget_answered(host)
        self.assertEqual(len(sent), external_apps.CALLS_KEPT + 3, "the marked responder answered every call")
        self.assertIn(("app", "running"), host._calls)
        self.assertIn(("app", "done-0"), host._evicted)
        self.assertLessEqual(len(host._calls), external_apps.CALLS_KEPT)


class FinishTests(unittest.TestCase):
    """``headless_answers.finish`` alone."""

    def host(self, after_call=lambda *args: None):
        host = SimpleNamespace(_answers=queue.Queue(), _calls={}, _unsent=[], after_call=after_call)
        host._calls[("app", "c1")] = {"work": None, "fields": {}, "note": None}
        host._calls[("app", "c2")] = {"work": None, "fields": {}, "note": None}
        return host

    def test_a_replay_queued_before_its_first_answer_gets_that_answer(self):
        def failing(tool, current, value):
            raise OSError("on purpose")
        host, sent = self.host(failing), []
        host._answers.put((("app", "c1"), "angelo_create_cube", True, None, RAW, lambda v: sent.append(("again", v))))
        host._answers.put((("app", "c1"), "angelo_create_cube", False, None, RAW, lambda v: sent.append(("first", v))))
        headless_answers.finish(host)
        headless_answers.finish(host)   # The next tick: the waiting replay now has the first answer.
        self.assertEqual([who for who, _value in sent], ["first", "again"])
        self.assertEqual(sent[0][1], sent[1][1], "the replay is the first answer")
        self.assertTrue(sent[1][1]["isError"], "the save's error, never the raw success")

    def test_every_replay_of_a_call_shares_one_deadline(self):
        """Break-it (should 3): the launcher asks every round; the wait must not start again with each re-ask."""
        host, sent, now = self.host(), [], [0.0]
        clock = lambda: now[0]  # noqa: E731
        host._answers.put((("app", "c1"), "angelo_create_cube", True, None, RAW, sent.append))
        headless_answers.finish(host, clock=clock)
        now[0] = headless_answers.REPLAY_WAIT_S - 1
        host._answers.put((("app", "c1"), "angelo_create_cube", True, None, RAW, sent.append))   # A later round.
        headless_answers.finish(host, clock=clock)
        self.assertEqual(sent, [], "both wait while the call's first answer may still come")
        now[0] = headless_answers.REPLAY_WAIT_S + 1
        headless_answers.finish(host, clock=clock)
        self.assertEqual(len(sent), 2, "both are told at the call's one deadline")
        self.assertTrue(all(value["isError"] and "did not run again" in json.dumps(value) for value in sent))

    def test_a_replay_of_an_unknown_call_never_claims_success(self):
        host, sent = self.host(), []
        host._answers.put((("app", "gone"), "angelo_create_cube", True, None, RAW, sent.append))
        headless_answers.finish(host)
        self.assertTrue(sent and sent[0]["isError"])

    def test_one_failing_delivery_never_drops_the_ones_after_it(self):
        """Break-it (should 4): the batch is answered in order; an error in one leaves the rest for the next tick."""
        host, sent = self.host(), []
        broken = SimpleNamespace(count_out=Mock(side_effect=TypeError("on purpose")))
        host._answers.put((("app", "c1"), "angelo_create_cube", False, broken, RAW, sent.append))
        host._answers.put((("app", "c2"), "angelo_create_cube", False, None, RAW, sent.append))
        with self.assertRaises(TypeError):
            headless_answers.finish(host)
        headless_answers.finish(host)
        self.assertEqual(len(sent), 1, "the delivery after the failing one was answered on the next tick")
        self.assertEqual(sent[0], RAW)


class RequestCacheTests(unittest.TestCase):
    """``DirectRequest``'s own answers past their bound: asked again, a call is told its answer is not kept, never that
    it did not run (it did), and the IDs it keeps for that are bounded too."""

    def request(self):
        return claude_direct.DirectRequest(SimpleNamespace(_current=lambda owner: True),
                                           SimpleNamespace(references=Mock(), allowed_tools=None), Mock(), Mock())

    def test_a_replay_past_the_bound_is_told_not_kept_and_never_runs(self):
        request = self.request()
        for index in range(4 * claude_direct.MAX_CALLS):
            request._answers[("call", "old-%d" % index)] = RAW
        self.assertTrue(request.admit(("call", "late"), lambda value: None), "a new call runs")
        request.answered(("call", "late"), RAW)
        replies = []
        self.assertFalse(request.admit(("call", "late"), replies.append), "asked again: it never runs twice")
        self.assertEqual(len(replies), 1)
        self.assertTrue(replies[0]["isError"])
        self.assertIn("did not run again", json.dumps(replies[0]))

    def test_the_not_kept_ids_are_bounded(self):
        request = self.request()
        request._answers = {("call", "old-%d" % i): RAW for i in range(4 * claude_direct.MAX_CALLS)}
        request._not_kept = {("call", "nk-%d" % i) for i in range(64 * claude_direct.MAX_CALLS)}
        request.answered(("call", "one-more"), RAW)
        self.assertEqual(len(request._not_kept), 64 * claude_direct.MAX_CALLS)


if __name__ == "__main__":
    unittest.main()
