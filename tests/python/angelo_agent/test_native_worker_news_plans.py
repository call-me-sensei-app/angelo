# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Worker news on Claude's default route, typed plans (``providers._run_plans``), and the adoption handshake off the
main thread (break-it OVH4-M-2, M-1). No Blender or provider.

The plan loop is the product's own; the runtime round is the test's ``_turn`` (it returns the model's next plan once
the prompt was written, or raises when the write fails): a continuation's worker news is told only when the next
round wrote it and the model answered. The adoption handshake runs on the real launch thread (``_launch``) with a fake
worker, and binding takes its answer (``RemoteExecutor.greet``); the Blender suites adopt real workers.
"""

import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from claude_protocol_fixtures import host  # noqa: E402
from angelo_agent import parallel_sessions, worker_news, worker_sessions  # noqa: E402
from angelo_agent.protocol import RpcError  # noqa: E402
from angelo_agent.providers import ClaudeProvider  # noqa: E402

BUILD = {"tool": "angelo_execute_python", "arguments": {"description": "Build the room.", "code": "result = 1"}}
FIELD = worker_news.FIELD


def plan(*operations, reply="", verify=False):
    return {"reply": reply, "operations": list(operations), "verify": verify}


def written(context, rounds):
    """The runtime round as ``_turn`` makes it: a write (``record_runtime_round``, as ``_turn`` calls it once the
    message was written), then the model's plan; an exception stands for a write that failed (nothing recorded)."""
    from angelo_agent.provider_usage import record_runtime_round
    rounds = list(rounds)

    def turn(*_args, **_kwargs):
        answer = rounds.pop(0)
        if isinstance(answer, Exception):
            raise answer
        record_runtime_round(context, claude=True)
        return answer
    return turn


class TypedPlanNewsTests(unittest.TestCase):
    def setUp(self):
        self.done = {"w1": True, "w2": False}   # Worker 1 finished; worker 2 still builds.
        patches = [patch.object(worker_sessions, "_started", {"lead": ["w1", "w2"]}),
                   patch.object(worker_sessions, "_parts", {"w1": (1, "Sofa", Path("/a")), "w2": (2, "Chair", Path("/b"))}),
                   patch.object(worker_sessions, "finished", lambda s: self.done[s]),
                   patch.object(parallel_sessions, "_merged", {}), patch.object(parallel_sessions, "_merging", set())]
        patches += [patch.object(worker_news, name, {}) for name in ("_told", "_told_cards", "_turn", "_staged")
                    if hasattr(worker_news, name)]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_plans(self, rounds):
        context = host()
        context.state["id"] = "lead"
        context.request_observation = Mock(side_effect=lambda callback: callback({"context_version": 2, "objects": []}))
        provider = ClaudeProvider(context, {})
        provider._turn = Mock(side_effect=written(context, rounds))
        provider._native_call = Mock(side_effect=lambda tool, arguments, owner=None: (
            {"success": True, "result": 1, "output": ""} if tool == "angelo_execute_python"
            else {"context_version": 2, "objects": []}))
        try:
            provider.run(None, "Build the room", observation={})
        except RpcError:
            pass
        return provider._turn.call_args_list

    def test_a_continuation_shows_the_news_and_tells_it_once_the_next_round_answered(self):
        calls = self.run_plans([plan(BUILD, verify=True), plan(reply="Done.")])
        self.assertIn('"%s":["1 Sofa"]' % FIELD, calls[1].args[1])   # The continuation prompt the model read.
        self.assertEqual(worker_news.fresh("lead"), [])

    def test_a_continuation_whose_write_failed_tells_nothing(self):
        self.run_plans([plan(BUILD, verify=True), RpcError("Claude runtime no longer owns this request")])
        self.assertEqual(worker_news.fresh("lead"), ["w1"])

    def test_a_status_answer_in_a_plan_is_told_by_its_continuation_s_field(self):
        context = host()
        context.state["id"] = "lead"
        context.request_observation = Mock(side_effect=lambda cb: cb({"context_version": 2, "objects": []}))
        provider = ClaudeProvider(context, {})
        provider._turn = Mock(side_effect=written(context, [plan(BUILD, verify=True), plan(reply="Done.")]))
        provider._native_call = Mock(side_effect=lambda tool, arguments, owner=None: {
            "success": True, "result": 1, "output": "", "workers": [{"worker": 1, "name": "Sofa", "state": "replied"}]})
        provider.run(None, "Build the room", observation={})
        prompt = provider._turn.call_args_list[1].args[1]
        self.assertIn('"%s":["1 Sofa"]' % FIELD, prompt)
        self.assertEqual(worker_news.fresh("lead"), [])


class RoundWrittenTests(unittest.TestCase):
    """What a model round shows is told once the round was written to the runtime, whatever its answer (break-it
    OVH5-M-2), through the real Claude ``_turn`` with the protocol fixture's process (its stdin is the write)."""

    def setUp(self):
        from claude_protocol_fixtures import ClaudeAdapterFixture
        self.fixture = type("F", (ClaudeAdapterFixture, unittest.TestCase), {"runTest": lambda self: None})()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.host.state["id"] = "lead"
        patches = [patch.object(worker_news, name, {}) for name in ("_told", "_told_cards", "_staged", "_turn")]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_written_round_whose_answer_fails_validation_still_tells_what_it_showed(self):
        self.fixture.process.on_user = lambda process, _event: process.stdout.emit(
            {"type": "result", "subtype": "success", "is_error": False, "session_id": "fixture-native"})
        worker_news.stage(self.fixture.host, ("lead", {("w1", 1)}, {("w2", frozenset({"c"}))}))
        with self.assertRaises(Exception):
            self.fixture.turn(None, "continuation")   # Delivered, answered without a structured plan.
        self.assertEqual(worker_news._told, {"lead": {("w1", 1)}})
        self.assertEqual(worker_news._told_cards, {"lead": {("w2", frozenset({"c"}))}})

    def test_a_round_never_written_tells_nothing_and_a_later_request_drops_it(self):
        stale = self.fixture.host._request_generation - 1   # Staged by a request that ended before writing it.
        worker_news._staged["lead"] = (stale, {("w1", 1)}, set())
        self.fixture.turn(None, "next request")
        self.assertEqual(worker_news._told, {})
        self.assertEqual(worker_news._staged, {})


class HandoffTests(unittest.TestCase):
    """A handoff's rows tell nothing (they may be excerpted: break-it OVH6-M-2); the opening prompt names untold
    finishes after them and is told once its round is written; a failed attempt consumes nothing (OVH6-M-1)."""

    def setUp(self):
        patches = [patch.object(worker_news, name, {}) for name in ("_told", "_told_cards", "_staged", "_turn")]
        patches += [patch.object(worker_sessions, "_started", {"lead": ["w1", "w2"]}),
                    patch.object(worker_sessions, "_parts", {"w1": (1, "Sofa", Path("/a")), "w2": (2, "Chair", Path("/b"))}),
                    patch.object(worker_sessions, "finished", lambda s: s == "w1"),
                    patch.object(parallel_sessions, "_merged", {}), patch.object(parallel_sessions, "_merging", set())]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.context = SimpleNamespace(state={"id": "lead"}, _request_generation=3, record_provider_turn=lambda: None)

    def test_rows_a_handoff_carries_tell_nothing_even_cut_by_the_excerpt(self):
        from angelo_agent.routing import handoff
        rows = [{"role": "user", "text": "Build the room."},
                {"role": "activity", "call_id": "c1", "tool": "angelo_workers_status", "status": "completed",
                 "arguments": {}, "result": {"workers": [{"worker": 1, "name": "Sofa", "state": "replied",
                                                          "reply": "x" * 9000}]}}]
        text, _ids = handoff(rows, 0, 24000, identifiers=None)
        self.assertIn("Sofa", text)
        self.assertEqual(worker_news.fresh("lead"), ["w1"])

    def test_the_opening_prompt_names_untold_finishes_and_is_told_once_written(self):
        from angelo_agent.provider_usage import record_runtime_round
        line = worker_news.prompt_news(self.context)
        self.assertIn('"%s":["1 Sofa"]' % FIELD, line)
        self.assertEqual(worker_news.fresh("lead"), ["w1"])   # Not before its round is written.
        record_runtime_round(self.context)
        self.assertEqual(worker_news.fresh("lead"), [])
        self.assertEqual(worker_news.prompt_news(self.context), "")

    def test_a_prompt_never_written_leaves_the_news_for_the_next_request(self):
        from angelo_agent.provider_usage import record_runtime_round
        worker_news.prompt_news(self.context)   # Its connect or write failed: no round recorded.
        self.context._request_generation += 1   # The next request.
        self.assertIn("1 Sofa", worker_news.prompt_news(self.context))
        record_runtime_round(self.context)
        self.assertEqual(worker_news.fresh("lead"), [])

    def test_history_text_never_carries_an_old_token(self):
        from angelo_agent.model_results import WORKER_FINISHES
        from angelo_agent.program_completion import history_result_text
        self.assertNotIn(WORKER_FINISHES, history_result_text({"workers": [], WORKER_FINISHES: "7"}))


class AdoptionHandshakeTests(unittest.TestCase):
    def test_a_part_s_launch_thread_greets_its_worker_before_adoption(self):
        """The real launch (``worker_sessions._launch``): started and greeted on its own thread."""
        asked = []
        worker = SimpleNamespace(part=Path("/p/part.blend"), start=lambda: {"ok": True},
                                 request=lambda op, timeout, **fields: asked.append((op, threading.current_thread()))
                                 or {"document": ["serial", 3]})
        entry = {"lead": SimpleNamespace(state={"id": "lead"}), "part": {"name": "Sofa", "brief": "b"}, "number": 1,
                 "error": None, "done": threading.Event(), "worker": worker}
        with patch.object(worker_sessions, "_launching", []):
            worker_sessions._launch(entry, take=False)
            self.assertTrue(entry["done"].wait(5))
        self.assertIsNone(entry["error"])
        self.assertEqual([op for op, _thread in asked], ["hello"])
        self.assertIsNot(asked[0][1], threading.main_thread())
        self.assertEqual(worker.hello, {"document": ["serial", 3]})
        source = (Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent/worker_queue.py").read_text()
        self.assertIn("ready(item[\"worker\"])", source[source.index("AngeloWorkerRelaunch") - 600:])   # A restart too.

    def test_binding_takes_that_answer_and_sends_nothing(self):
        """Break-it OVH4-M-1: a worker that stalls after its launch answered never holds the main thread."""
        from angelo_agent.remote_executor import RemoteExecutor
        worker = SimpleNamespace(hello={"document": ["serial", 3]},
                                 request=Mock(side_effect=AssertionError("no request on the main thread")))
        executor = RemoteExecutor(worker, "w-1")
        executor.greet()
        self.assertEqual(executor.document_key, ("angelo-worker", "w-1", ("serial", 3)))
        self.assertFalse(hasattr(worker, "hello"))   # Taken once.
        worker.request.assert_not_called()
        source = (Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent/worker_routing.py").read_text()
        self.assertIn("executor.greet()", source[source.index("def bind("):source.index("def capture(")])


if __name__ == "__main__":
    unittest.main()
