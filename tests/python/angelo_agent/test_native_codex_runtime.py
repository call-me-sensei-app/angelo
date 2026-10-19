# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared Codex app-servers and the warm bases new chats fork (codex_runtime)."""

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import codex_bases, codex_connect, codex_runtime, providers  # noqa: E402
from angelo_agent.codex_connect import ContextLost  # noqa: E402
from angelo_agent.codex_runtime import Lease, Runtime  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.protocol import RpcError  # noqa: E402
from angelo_agent.provider_usage import CodexThreadUsage  # noqa: E402
from angelo_agent.request_fences import CodexTurnScope  # noqa: E402
from angelo_agent.routing import TOOLSET_VERSION, route_key  # noqa: E402
from angelo_agent.telemetry import TelemetryStore  # noqa: E402

SUPPORTED = RpcError("`experimentalPredictionMode` requires `ephemeral: true`")
EMPTY_PLAN = '{"reply":"","operations":[]}'


def warm_up_reply(process, base, text=EMPTY_PLAN):
    """The warm-up turn's reply, as Codex reports a completed agent message."""
    process.on_event("item/completed", {"threadId": base, "turnId": "warm-turn",
                                        "item": {"type": "agentMessage", "id": "m", "text": text}})
LOST = RpcError("no rollout found for thread id 01a132ec")


def prediction_fork(params):
    """A supported app-server: the capability probe is refused for its missing ``ephemeral``."""
    if "ephemeral" not in params:
        raise SUPPORTED
    return {"thread": {"id": "fork-1"}}


def total(tokens):
    return {"inputTokens": tokens, "cachedInputTokens": 0, "outputTokens": 1, "reasoningOutputTokens": 0,
            "totalTokens": tokens + 1}


class FakeProcess:
    """An app-server: answers per method (a value, an exception or a function of the params)."""

    def __init__(self, on_event, on_request, answers=None):
        self.on_event, self.on_request = on_event, on_request
        self.answers = answers if answers is not None else {}
        self.calls, self.responses, self.writes, self.notes = [], [], [], []
        self.closed, self.stderr_tail = threading.Event(), ["tail"]
        self.started = 0

    def request(self, method, params=None, timeout=60):
        self.calls.append((method, params))
        answer = self.answers.get(method, {})
        if isinstance(answer, Exception):
            raise answer
        return answer(params) if callable(answer) else answer

    def respond(self, request_id, result):
        self.responses.append((request_id, result))

    def write(self, message):
        self.writes.append(message)

    def notify(self, method, params=None):
        self.notes.append(method)

    def close(self):
        self.closed.set()

    def methods(self):
        return [method for method, _ in self.calls]


def app_server(answers=None):
    """``(spawn, processes)``: a spawn function for Runtime and every process it made."""
    processes = []

    def spawn(on_event, on_request):
        processes.append(FakeProcess(on_event, on_request, answers))
        return processes[-1]
    return spawn, processes


def threads_counter(prefix):
    counter = iter(range(1, 1000))
    return lambda params: {"thread": {"id": "%s-%d" % (prefix, next(counter))}}


def wait_for(condition, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(.005)
    return False


class Recorder:
    def __init__(self):
        self.events, self.requests = [], []

    def event(self, method, params):
        self.events.append((method, params))

    def request(self, identity, method, params):
        self.requests.append((identity, method, params))


class PoolCase(unittest.TestCase):
    def setUp(self):
        codex_runtime.close_all()
        self.addCleanup(codex_runtime.close_all)

    def settle_before_cleanup(self, directory):
        """Registered after ``directory``'s own cleanup, so it runs first: warm-ups end, receipts are written."""
        def settle():
            codex_runtime.close_all()
            for thread in threading.enumerate():
                if thread.name in ("AngeloCodexWarmUp", "AngeloCodexBase", "AngeloCodexOpening"):
                    thread.join(5)
            TelemetryStore.shared(directory).flush()
        self.addCleanup(settle)


class DispatchTests(PoolCase):
    def setUp(self):
        super().setUp()
        spawn, self.processes = app_server()
        self.runtime = Runtime(("key",), "connection", spawn)
        self.process = self.processes[0]
        self.a, self.b = Recorder(), Recorder()
        self.lease_a = self.runtime.lease(self.a.event, self.a.request)
        self.lease_b = self.runtime.lease(self.b.event, self.b.request)
        self.lease_a.own("thread-a")
        self.lease_b.own("thread-b")

    def test_events_reach_only_the_chat_that_owns_their_thread(self):
        self.process.on_event("item/agentMessage/delta", {"threadId": "thread-a", "delta": "x"})
        self.process.on_event("thread/started", {"thread": {"id": "thread-b"}})
        self.process.on_event("thread/tokenUsage/updated", {"threadId": "unowned", "tokenUsage": {"total": total(1)}})
        self.process.on_event("account/rateLimits/updated", {})
        self.assertEqual([method for method, _ in self.a.events],
                         ["item/agentMessage/delta", "account/rateLimits/updated"])
        self.assertEqual([method for method, _ in self.b.events], ["thread/started", "account/rateLimits/updated"])

    def test_host_requests_reach_their_owner_and_unowned_tool_calls_run_nothing(self):
        self.process.on_request(7, "item/tool/call",
                                {"threadId": "thread-b", "turnId": "t", "tool": "angelo_scene_search"})
        self.assertEqual(self.b.requests[0][:2], (7, "item/tool/call"))
        self.process.on_request(8, "item/tool/call", {"threadId": "base-thread", "tool": "angelo_scene_program"})
        self.process.on_request(9, "item/fileChange/requestApproval", {"threadId": "base-thread"})
        self.process.on_request(10, "item/other", {})
        self.assertEqual(self.process.responses[0][1]["success"], False)
        self.assertIn("no work was admitted", self.process.responses[0][1]["contentItems"][0]["text"])
        self.assertEqual(self.process.responses[1], (9, {"decision": "decline"}))
        self.assertEqual(self.process.writes[0]["error"]["code"], -32601)
        self.assertEqual(self.a.requests, [])

    def test_a_released_chat_leaves_the_process_and_its_loaded_thread(self):
        self.lease_a.close()
        self.assertFalse(self.process.closed.is_set())
        self.assertTrue(self.runtime.has_loaded("thread-a"))
        with self.assertRaises(RpcError):
            self.lease_a.request("turn/start", {})
        self.process.on_event("item/agentMessage/delta", {"threadId": "thread-a"})
        self.assertEqual(self.a.events, [])
        self.assertEqual(self.lease_b.stderr_tail, ["tail"])

    def test_release_interrupts_only_a_turn_its_chat_left_running(self):
        self.process.on_event("turn/started", {"threadId": "thread-a", "turn": {"id": "turn-1"}})
        self.process.on_event("turn/started", {"threadId": "thread-b", "turn": {"id": "turn-2"}})
        self.process.on_event("turn/completed",
                              {"threadId": "thread-b", "turn": {"id": "turn-2", "status": "completed"}})
        self.lease_a.close()
        self.lease_b.close()
        self.assertTrue(wait_for(lambda: "turn/interrupt" in self.process.methods()))
        time.sleep(.05)
        interrupts = [params for method, params in self.process.calls if method == "turn/interrupt"]
        self.assertEqual(interrupts, [{"threadId": "thread-a", "turnId": "turn-1"}])

    def test_a_thread_another_chat_now_owns_is_not_interrupted(self):
        self.process.on_event("turn/started", {"threadId": "thread-a", "turn": {"id": "turn-1"}})
        other = self.runtime.lease(Recorder().event, Recorder().request)
        other.own("thread-a")
        self.lease_a.close()
        time.sleep(.05)
        self.assertNotIn("turn/interrupt", self.process.methods())

    def test_a_disconnected_process_reaches_every_chat_and_leaves_the_pool(self):
        runtime = codex_runtime.runtime_for(("pooled",), "connection", app_server()[0])
        recorder = Recorder()
        lease = runtime.lease(recorder.event, recorder.request)
        runtime.rpc.on_event("host/disconnected", {})
        self.assertEqual(recorder.events, [("host/disconnected", {})])
        self.assertTrue(lease.closed.is_set())
        self.assertFalse(runtime.has_loaded("anything"))
        self.assertIsNot(codex_runtime.runtime_for(("pooled",), "connection", app_server()[0]), runtime)
        with self.assertRaises(RpcError):
            runtime.lease(recorder.event, recorder.request)


def refuse(rpc):
    raise RpcError("Codex billing mode does not match")


class PoolTests(PoolCase):
    def test_one_process_per_key_initialized_once_and_checked_every_connect(self):
        spawn, processes = app_server()
        started, verified = [], []
        for _ in range(3):
            rpc, models = codex_runtime.transport(spawn, Recorder().event, Recorder().request,
                                                  lambda rpc: started.append(rpc),
                                                  lambda rpc: verified.append(rpc) or ["m"], ("key",), "connection")
            self.assertIsInstance(rpc, Lease)
            self.assertEqual(models, ["m"])
        self.assertEqual(len(processes), 1)
        self.assertEqual(len(started), 1)
        self.assertEqual(len(verified), 3)

    def test_a_failed_check_closes_the_shared_process(self):
        spawn, processes = app_server()
        with self.assertRaises(RpcError):
            codex_runtime.transport(spawn, Recorder().event, Recorder().request, lambda rpc: None, refuse,
                                    ("key",), "connection")
        self.assertTrue(processes[0].closed.is_set())
        codex_runtime.transport(spawn, Recorder().event, Recorder().request, lambda rpc: None, lambda rpc: [],
                                ("key",), "connection")
        self.assertEqual(len(processes), 2)

    def test_without_a_key_every_connect_has_a_process_of_its_own(self):
        spawn, processes = app_server()
        for _ in range(2):
            rpc, _models = codex_runtime.transport(spawn, Recorder().event, Recorder().request, lambda rpc: None,
                                                   lambda rpc: [])
            self.assertIsInstance(rpc, FakeProcess)
        with self.assertRaises(RpcError):
            codex_runtime.transport(spawn, Recorder().event, Recorder().request, lambda rpc: None, refuse)
        self.assertEqual(len(processes), 3)
        self.assertTrue(processes[2].closed.is_set())

    def test_settings_change_closes_only_an_idle_runtime_of_that_connection(self):
        idle = codex_runtime.runtime_for(("old",), "connection", app_server()[0])
        busy = codex_runtime.runtime_for(("busy",), "other", app_server()[0])
        busy.lease(Recorder().event, Recorder().request)
        other = codex_runtime.runtime_for(("busy-new",), "other", app_server()[0])
        codex_runtime.runtime_for(("new",), "connection", app_server()[0])
        self.assertTrue(idle.dead and idle.rpc.closed.is_set())
        self.assertFalse(busy.dead, "A running chat keeps its process under older settings")
        self.assertIsNot(other, busy)
        codex_runtime.close_all()
        self.assertTrue(busy.dead and other.dead)

    def test_a_failed_check_never_cuts_short_another_chat_on_the_process(self):
        spawn, processes = app_server()
        running, _models = codex_runtime.transport(spawn, Recorder().event, Recorder().request, lambda rpc: None,
                                                   lambda rpc: [], ("key",), "connection")
        with self.assertRaises(RpcError):
            codex_runtime.transport(spawn, Recorder().event, Recorder().request, lambda rpc: None, refuse,
                                    ("key",), "connection")
        self.assertFalse(processes[0].closed.is_set())
        running.request("turn/start", {})
        fresh, _models = codex_runtime.transport(spawn, Recorder().event, Recorder().request, lambda rpc: None,
                                                 lambda rpc: [], ("key",), "connection")
        self.assertIsNot(fresh.runtime, running.runtime, "The next chat checks a new process")
        running.close()
        self.assertTrue(processes[0].closed.is_set(), "The retired process ends with its last chat")
        self.assertFalse(processes[1].closed.is_set())

    def test_reauthentication_retires_only_that_connection(self):
        old = codex_runtime.runtime_for(("a",), "connection", app_server()[0])
        lease = old.lease(Recorder().event, Recorder().request)
        idle = codex_runtime.runtime_for(("b",), "idle-connection", app_server()[0])
        kept = codex_runtime.runtime_for(("c",), "other", app_server()[0])
        codex_runtime.retire_connection("connection")
        codex_runtime.retire_connection("idle-connection")
        self.assertFalse(old.dead, "A chat still on it is stopped and closed by its controller first")
        self.assertTrue(idle.dead)
        self.assertIsNot(codex_runtime.runtime_for(("a",), "connection", app_server()[0]), old)
        lease.close()
        self.assertTrue(old.dead)
        self.assertFalse(kept.dead)
        retired = codex_runtime.runtime_for(("d",), "late", app_server()[0])
        retired.lease(Recorder().event, Recorder().request)
        codex_runtime.retire_connection("late")
        codex_runtime.close_all()
        self.assertTrue(retired.dead and kept.dead, "Unloading closes retired processes too")


class WarmForkTests(PoolCase):
    def setUp(self):
        super().setUp()
        self.answers = {"thread/fork": self.fork_answer, "thread/start": threads_counter("base"),
                        "turn/start": {"turn": {"id": "warm-turn"}}}
        self.forked = threads_counter("fork")
        spawn, processes = app_server(self.answers)
        self.runtime = Runtime(("key",), "connection", spawn)
        self.process = processes[0]
        self.chat = Recorder()
        self.lease = self.runtime.lease(self.chat.event, self.chat.request)

    def fork_answer(self, params):
        if "ephemeral" not in params:
            raise SUPPORTED
        return self.forked(params)

    def warm(self, variant="v", status="completed", reply=EMPTY_PLAN, items=()):
        self.assertTrue(self.runtime.warm(variant, {"model": "m"}, {"model": "m", "effort": "low",
                                                                   "serviceTierForTurn": "priority"}))
        self.assertTrue(wait_for(lambda: self.runtime.bases[variant]["thread"] is not None))
        base = self.runtime.bases[variant]["thread"]
        self.assertTrue(wait_for(lambda: base in self.runtime.warming))
        self.process.on_event("thread/tokenUsage/updated", {"threadId": base, "tokenUsage": {
            "total": total(23871), "last": total(23871)}})
        for item in items:
            self.process.on_event("item/completed", {"threadId": base, "turnId": "warm-turn", "item": item})
        if reply is not None:
            warm_up_reply(self.process, base, reply)
        self.process.on_event("turn/completed", {"threadId": base, "turn": {"id": "warm-turn", "status": status}})
        self.assertTrue(wait_for(lambda: self.runtime.bases[variant]["state"] != "warming"))
        return base

    def test_a_warm_up_runs_once_off_the_chat_and_its_base_is_forked(self):
        base = self.warm()
        methods = self.process.methods()
        self.assertEqual(methods, ["thread/fork", "thread/start", "turn/start"])
        probe, start, turn = (params for _, params in self.process.calls)
        self.assertNotIn("ephemeral", probe)
        self.assertEqual(start, {"model": "m"})
        self.assertEqual(turn["input"], [{"type": "text", "text": codex_bases.WARM_UP}])
        self.assertEqual(turn["serviceTierForTurn"], "default", "A Fast chat never bills its warm-up at Fast")
        self.assertEqual(self.chat.events, [], "The base's own events never reach a chat")
        self.assertFalse(self.runtime.warm("v", {}, {}), "A ready base is never warmed again")
        result, inherited = self.runtime.fork("v")
        self.assertEqual(result["thread"]["id"], "fork-1")
        self.assertEqual(self.process.calls[-1], ("thread/fork", dict(codex_bases.FORK, threadId=base)))
        self.assertEqual(inherited, total(23871))
        self.assertIsNone(self.runtime.fork("other-variant"))

    def test_a_failed_fork_allows_one_more_warm_up_then_none(self):
        self.warm()
        self.answers["thread/fork"] = RpcError("parent unavailable")
        self.assertIsNone(self.runtime.fork("v"))
        self.assertEqual(self.runtime.bases["v"]["state"], "failed")
        self.answers["thread/fork"] = self.fork_answer
        self.warm()
        self.assertEqual(self.runtime.bases["v"]["attempts"], codex_bases.MAX_WARM_UPS)
        self.answers["thread/fork"] = RpcError("parent unavailable")
        self.runtime.fork("v")
        self.assertFalse(self.runtime.warm("v", {}, {}))

    def test_a_failed_warm_up_turn_never_serves_a_fork(self):
        self.warm(status="failed")
        self.assertEqual(self.runtime.bases["v"]["state"], "failed")
        self.assertIsNone(self.runtime.fork("v"))

    def test_only_the_expected_empty_plan_and_nothing_else_makes_a_base(self):
        preamble = {"type": "agentMessage", "id": "p", "text": "SMUGGLED"}
        for name, kwargs in (("a reply", {"reply": '{"reply":"Checked the scene first.","operations":[]}'}),
                             ("an operation", {"reply": '{"reply":"","operations":[{"tool":"angelo_scene_verify"}]}'}),
                             ("a duplicate key", {"reply": '{"reply":"SMUGGLED","operations":[],"reply":""}'}),
                             ("a blank reply", {"reply": '{"reply":" ","operations":[]}'}),
                             ("another key", {"reply": '{"reply":"","operations":[],"note":null}'}),
                             ("a second message", {"items": [preamble]}),
                             ("no reply", {"reply": None}), ("not a plan", {"reply": "OK"}),
                             ("a web search", {"items": [{"type": "webSearch", "id": "w"}]}),
                             ("a tool call", {"items": [{"type": "dynamicToolCall", "id": "t"}]})):
            with self.subTest(name):
                self.runtime.bases.clear()
                self.warm(**kwargs)
                self.assertEqual(self.runtime.bases["v"]["state"], "failed")
                self.assertIsNone(self.runtime.fork("v"))
        self.runtime.bases.clear()
        self.warm(items=[{"type": "reasoning", "id": "r"}], reply='{ "operations": [], "reply": "" }')
        self.assertEqual(self.runtime.bases["v"]["state"], "ready", "Reasoning and whitespace between tokens are")

    def test_a_tool_call_from_a_warm_up_is_refused_its_turn_ended_and_no_base_kept(self):
        self.assertTrue(self.runtime.warm("v", {"model": "m"}, {"model": "m"}))
        self.assertTrue(wait_for(lambda: self.runtime.bases["v"]["thread"] in self.runtime.warming))
        base = self.runtime.bases["v"]["thread"]
        self.process.on_event("turn/started", {"threadId": base, "turn": {"id": "warm-turn"}})
        self.assertNotIn("turn/interrupt", self.process.methods(), "A warm-up's own turn is never an orphan")
        self.process.on_request(9, "item/tool/call", {"threadId": base, "turnId": "warm-turn",
                                                      "tool": "angelo_scene_search"})
        self.assertFalse(self.process.responses[-1][1]["success"])
        self.assertEqual(self.chat.requests, [])
        self.assertTrue(wait_for(lambda: ("turn/interrupt", {"threadId": base, "turnId": "warm-turn"})
                                 in self.process.calls))
        warm_up_reply(self.process, base)
        self.process.on_event("turn/completed", {"threadId": base, "turn": {"id": "warm-turn", "status": "completed"}})
        self.assertTrue(wait_for(lambda: self.runtime.bases["v"]["state"] == "failed"))

    def test_empty_plan(self):
        self.assertTrue(codex_bases.empty_plan(EMPTY_PLAN))
        for text in ('{"reply":"OK","operations":[]}', '{"reply":"","operations":[1]}', '{"reply":"",'
                     '"operations":[],"verify":true}', '{"reply":"","operations":[],"verify":null}',
                     '{"reply":"x","operations":[],"reply":""}', '{"reply":"","operations":[],"operations":[]}',
                     '{"reply":"\\n","operations":[]}', "[]", "", None, '{"operations":[]}'):
            self.assertFalse(codex_bases.empty_plan(text), text)

    def test_an_app_server_without_prediction_forks_is_never_warmed(self):
        for refusal in (RpcError("unknown method thread/fork"), None):
            with self.subTest(refusal=refusal):
                spawn, processes = app_server({"thread/fork": refusal or {"thread": {"id": "accepted"}}})
                runtime = Runtime(("old",), "connection", spawn)
                self.assertTrue(runtime.warm("v", {}, {}))
                self.assertTrue(wait_for(lambda: runtime.bases["v"]["state"] == "unsupported"))
                self.assertEqual(processes[0].methods(), ["thread/fork"])
                self.assertFalse(runtime.forks)
                self.assertFalse(runtime.warm("v", {}, {}))


class RememberedBaseTests(PoolCase):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settle_before_cleanup(self.temp.name)
        self.store = codex_bases.BaseStore(self.temp.name)
        self.answers = {"thread/fork": prediction_fork, "thread/start": threads_counter("base"),
                        "turn/start": {"turn": {"id": "warm-turn"}}, "thread/resume": {}}
        spawn, processes = app_server(self.answers)
        self.runtime = Runtime(("key",), "connection", spawn, self.store)
        self.runtime.prepare(lambda: {"userAgent": "angelo_blender/0.162.0"})
        self.process = processes[0]

    def test_a_remembered_base_is_resumed_once_and_forked(self):
        self.store.put(self.runtime.scope, "v", "old-base", total(23871))
        self.assertTrue(self.runtime.warm("v", {"model": "m", "dynamicTools": [{"name": "t"}]}, {}, stored_only=True))
        self.assertTrue(wait_for(lambda: self.runtime.bases["v"]["state"] == "ready"))
        self.assertEqual(self.process.calls[-1], ("thread/resume", {"model": "m", "threadId": "old-base",
                                                                    "excludeTurns": True}))
        self.assertEqual(self.runtime.bases["v"]["attempts"], 0, "A resume is not a warm-up")
        self.assertEqual(self.runtime.fork("v")[1], total(23871))

    def test_a_failed_fork_resumes_the_remembered_base_again_before_any_warm_up(self):
        self.store.put(self.runtime.scope, "v", "old-base", total(23871))
        self.runtime.warm("v", {}, {}, stored_only=True)
        self.assertTrue(wait_for(lambda: self.runtime.bases["v"]["state"] == "ready"))
        self.answers["thread/fork"] = RpcError("`experimentalPredictionMode` requires a loaded parent")
        self.assertIsNone(self.runtime.fork("v"))
        self.answers["thread/fork"] = prediction_fork
        self.assertTrue(self.runtime.warm("v", {}, {}, stored_only=True))
        self.assertTrue(wait_for(lambda: self.runtime.bases["v"]["state"] == "ready"))
        self.assertEqual(self.process.methods().count("thread/resume"), 2)
        self.assertNotIn("turn/start", self.process.methods())

    def test_nothing_remembered_waits_for_a_completed_turn_to_warm(self):
        self.assertTrue(self.runtime.warm("v", {}, {}, stored_only=True))
        self.assertTrue(wait_for(lambda: self.runtime.bases["v"]["state"] == "absent"))
        self.assertEqual(self.process.methods(), ["thread/fork"], "Only the capability check: no model request")
        self.assertFalse(self.runtime.warm("v", {}, {}, stored_only=True))
        self.assertTrue(self.runtime.warm("v", {}, {}))
        self.assertTrue(wait_for(lambda: "turn/start" in self.process.methods()))
        base = self.runtime.bases["v"]["thread"]
        self.process.on_event("thread/tokenUsage/updated", {"threadId": base, "tokenUsage": {"total": total(5)}})
        warm_up_reply(self.process, base)
        self.process.on_event("turn/completed", {"threadId": base, "turn": {"status": "completed"}})
        self.assertTrue(wait_for(lambda: self.runtime.bases["v"]["state"] == "ready"))
        self.assertEqual(self.store.get(self.runtime.scope, "v"), {"thread": base, "total": total(5)})

    def test_a_base_another_launch_holds_is_never_replaced(self):
        """A second Angelo on the profile: nothing warmed at connect; after a chat's turn, a base for this launch only."""
        self.store.put(self.runtime.scope, "v", "held-base", None)
        self.answers["thread/resume"] = RpcError("thread held-base already has an active writer")
        self.assertTrue(self.runtime.warm("v", {}, {}, at_connect=True))
        self.assertTrue(wait_for(lambda: self.runtime.bases["v"]["state"] == "absent"))
        self.assertEqual(self.runtime.bases["v"]["reason"], "held")
        self.assertNotIn("turn/start", self.process.methods(), "No warm-up over another instance's base at connect")
        self.assertTrue(self.runtime.warm("v", {}, {}))  # After a chat's first turn, as before this launch.
        self.assertTrue(wait_for(lambda: "turn/start" in self.process.methods()))
        self.assertEqual(self.process.methods().count("thread/resume"), 1)
        base = self.runtime.bases["v"]["thread"]
        warm_up_reply(self.process, base)
        self.process.on_event("turn/completed", {"threadId": base, "turn": {"status": "completed"}})
        self.assertTrue(wait_for(lambda: self.runtime.bases["v"]["state"] == "ready"), "It serves this launch")
        time.sleep(.05)
        self.assertEqual(self.store.get(self.runtime.scope, "v")["thread"], "held-base", "and is never remembered")

    def test_the_scope_ignores_the_terminal_and_the_os_version(self):
        agents = ["angelo_blender/0.162.0-alpha.17.2 (Mac OS 26.3.0; arm64) unknown (angelo_blender; 0.1.0)",
                  "angelo_blender/0.162.0-alpha.17.2 (Mac OS 26.4.0; arm64) iTerm.app/3.5.0 (angelo_blender; 0.1.0)",
                  "angelo_blender/0.162.0-alpha.17.2 (Mac OS 26.3.0; arm64) Apple_Terminal/455 (angelo_blender; 0.1.0)"]
        scopes = set()
        for agent in agents:
            runtime = Runtime(("key",), "connection", app_server()[0], self.store)
            runtime.prepare(lambda agent=agent: {"userAgent": agent})
            scopes.add(runtime.scope)
        self.assertEqual(len(scopes), 1, "A Dock or terminal launch and a macOS update are the same scope")
        self.assertEqual(codex_runtime.codex_build("weird agent"), "weird agent")

    def test_scope_follows_the_codex_build(self):
        spawn, _processes = app_server()
        other = Runtime(("key",), "connection", spawn, self.store)
        other.prepare(lambda: {"userAgent": "angelo_blender/0.163.0"})
        self.assertNotEqual(other.scope, self.runtime.scope)

    def test_the_store_keeps_the_newest_entries_and_survives_a_bad_file(self):
        for number in range(codex_bases.MAX_ENTRIES + 3):
            self.store.put("scope", "v%d" % number, "thread-%d" % number, None)
        self.assertIsNone(self.store.get("scope", "v0"))
        self.assertEqual(self.store.get("scope", "v18")["thread"], "thread-18")
        self.store.put("scope", "bad", "not a thread id!", None)
        self.assertIsNone(self.store.get("scope", "bad"))
        self.store.path.write_text("{not json")
        self.assertIsNone(self.store.get("scope", "v18"))
        self.store.put("scope", "v", "thread", {"inputTokens": 1, "secret": "x"})
        self.assertEqual(self.store.get("scope", "v"), {"thread": "thread", "total": {"inputTokens": 1}})


class Provider:
    """What open_thread reads from a CodexProvider."""

    def __init__(self, rpc, native=None, options=None):
        self.rpc, self._usage, self._scope = rpc, CodexThreadUsage(), CodexTurnScope()
        self.controller = SimpleNamespace(state={"native_id": native, "options": {}}, turn_options=options or {
            "model": "gpt-6.1-sol", "effort": "low", "fast": False, "approval": "AUTO_BOUNDED"}, models=[],
            settings={}, allowed_tools=None)


class OpenThreadTests(PoolCase):
    def setUp(self):
        super().setUp()
        self.answers = {"thread/start": threads_counter("chat"),
                        "thread/resume": lambda p: {"thread": {"id": p["threadId"]}},
                        "thread/fork": threads_counter("fork"), "turn/start": {"turn": {"id": "warm-turn"}}}
        spawn, processes = app_server(self.answers)
        self.runtime = Runtime(("key",), "connection", spawn)
        self.process = processes[0]
        self.prepared = []
        stub = patch.object(codex_connect, "prepare_base",
                            lambda *args, **kwargs: self.prepared.append((args, kwargs.get("stored_only", False))))
        stub.start()
        self.addCleanup(stub.stop)

    def provider(self, native=None, **kwargs):
        return Provider(self.runtime.lease(Recorder().event, Recorder().request), native, **kwargs)

    def test_the_first_chat_starts_and_warms_its_variant_after_its_first_completed_turn(self):
        provider = self.provider()
        result = codex_connect.open_thread(provider, {"model": "gpt-6.1-sol"}, lambda: [{"name": "tool"}])
        self.assertEqual(result["thread"]["id"], "chat-1")
        self.assertEqual(self.process.calls, [("thread/start", {"model": "gpt-6.1-sol",
                                                                "dynamicTools": [{"name": "tool"}]})],
                         "No base is ready: no digest and no fork on the chat's path")
        self.assertEqual(provider._usage.total["input_tokens"], 0)
        self.assertIs(self.runtime.owners["chat-1"], provider.rpc)
        (runtime, variant, parameters, turn), stored_only = self.prepared[0]
        self.assertTrue(stored_only, "A remembered base is resumed at once; nothing is warmed yet")
        self.assertIs(runtime, self.runtime)
        self.assertIsNone(variant, "Its digest is computed off the chat's path")
        self.assertEqual(parameters["dynamicTools"], [{"name": "tool"}])
        self.assertEqual((turn["model"], turn["effort"]), ("gpt-6.1-sol", "low"))
        self.assertIn("outputSchema", turn)
        codex_connect.after_turn(provider, "failed")
        codex_connect.after_turn(provider, "completed")
        self.assertEqual(len(self.prepared), 1, "Only a first turn that completed warms")
        provider = self.provider()
        codex_connect.open_thread(provider, {"model": "gpt-6.1-sol"}, lambda: [{"name": "tool"}])
        codex_connect.after_turn(provider, "completed")
        codex_connect.after_turn(provider, "completed")
        self.assertEqual([stored_only for _, stored_only in self.prepared], [True, True, False])

    def test_the_digest_and_the_base_are_prepared_off_the_chat(self):
        start, turn = {"model": "m", "dynamicTools": []}, {"model": "m", "effort": "low", "outputSchema": {}}
        self.addCleanup(patch.stopall)
        patch.stopall()  # The real prepare_base.
        with patch.object(Runtime, "warm") as warm:
            codex_connect.prepare_base(self.runtime, None, start, turn, stored_only=True)
            self.assertTrue(wait_for(lambda: warm.called))
        self.assertEqual(warm.call_args.args, (codex_connect.variant_key(start, turn), start, turn, True))

    def test_a_new_chat_forks_a_ready_base_and_starts_from_its_total(self):
        provider = self.provider()
        start = dict({"model": "gpt-6.1-sol"}, dynamicTools=[{"name": "tool"}])
        variant = codex_connect.variant_key(start, codex_connect._turn_options(provider.controller))
        self.runtime.forks = True
        self.runtime.bases[variant] = {"state": "ready", "thread": "base", "total": total(23871), "attempts": 1}
        result = codex_connect.open_thread(provider, {"model": "gpt-6.1-sol"}, lambda: [{"name": "tool"}])
        self.assertEqual(result["thread"]["id"], "fork-1")
        self.assertEqual(self.process.calls, [("thread/fork", dict(codex_bases.FORK, threadId="base"))])
        self.assertEqual(provider._usage.total["input_tokens"], 23871)
        self.assertIsNone(getattr(provider, "_warm_after_turn", None))
        provider._usage.begin_turn("fork-1")
        provider._usage.observe("fork-1", {"total": total(23871 + 24000)})
        self.assertEqual(provider._usage.finish_turn(900)["input_tokens"], 24000,
                         "The base's warm-up is not this chat's")

    def test_a_rebased_chat_starts_a_plain_thread_and_only_a_fork_is_marked(self):
        """context_rebase: a fork never compacts on codex-cli 0.163, so a rebased chat's fresh context is plain."""
        start = dict({"model": "gpt-6.1-sol"}, dynamicTools=[{"name": "tool"}])
        self.runtime.forks = True
        forked = self.provider()
        variant = codex_connect.variant_key(start, codex_connect._turn_options(forked.controller))
        self.runtime.bases[variant] = {"state": "ready", "thread": "base", "total": total(23871), "attempts": 1}
        codex_connect.open_thread(forked, {"model": "gpt-6.1-sol"}, lambda: [{"name": "tool"}])
        self.assertTrue(forked._thread_forked)
        rebased = self.provider()
        rebased.controller.host = SimpleNamespace(state={"runtime_sessions": {"chat": {"rebase_pending": True}}})
        rebased.controller.context_key = "chat"
        result = codex_connect.open_thread(rebased, {"model": "gpt-6.1-sol"}, lambda: [{"name": "tool"}])
        self.assertEqual(result["thread"]["id"], "chat-1")
        self.assertEqual(self.process.methods(), ["thread/fork", "thread/start"])
        self.assertFalse(rebased._thread_forked)
        loaded = self.provider(native="fork-1")
        codex_connect.open_thread(loaded, {"model": "gpt-6.1-sol"}, lambda: [{"name": "tool"}])
        self.assertFalse(loaded._thread_forked, "Found loaded, not forked: the chat keeps its earlier mark")

    def test_voice_and_other_variants_never_fork(self):
        provider = self.provider()
        self.runtime.forks = True
        start = dict({"model": "gpt-6.1-sol"}, dynamicTools=[])
        variant = codex_connect.variant_key(start, codex_connect._turn_options(provider.controller))
        self.runtime.bases[variant] = {"state": "ready", "thread": "base", "total": None, "attempts": 1}
        codex_connect.open_thread(provider, {"model": "gpt-6.1-sol"}, lambda: [], fork=False)
        self.assertEqual(self.process.methods(), ["thread/start"])
        self.assertIsNone(getattr(provider, "_warm_after_turn", None))
        other = self.provider()
        other.controller.allowed_tools = {"angelo_scene_search"}   # Another response format: another variant.
        codex_connect.open_thread(other, {"model": "gpt-6.1-sol"}, lambda: [])
        self.assertEqual(self.process.methods(), ["thread/start", "thread/start"])

    def test_a_warm_base_serves_every_effort_of_its_model_and_prefix(self):
        """Effort is a request parameter: a chat at another effort forks the base instead of warming its own."""
        self.runtime.forks = True
        start = dict({"model": "gpt-6.1-sol"}, dynamicTools=[])
        variant = codex_connect.variant_key(start, codex_connect._turn_options(self.provider().controller))
        self.runtime.bases[variant] = {"state": "ready", "thread": "base", "total": None, "attempts": 1}
        high = self.provider(options={"model": "gpt-6.1-sol", "effort": "high", "fast": False,
                                      "approval": "AUTO_BOUNDED"})
        codex_connect.open_thread(high, {"model": "gpt-6.1-sol"}, lambda: [])
        self.assertEqual(self.process.methods(), ["thread/fork"])
        self.assertTrue(high._thread_forked)

    def test_variant_follows_everything_that_shapes_the_cached_opening(self):
        turn = {"model": "m", "effort": "low", "outputSchema": {"type": "object"}, "serviceTierForTurn": "default"}
        key = codex_connect.variant_key({"baseInstructions": "a", "dynamicTools": []}, turn)
        self.assertEqual(key, codex_connect.variant_key({"baseInstructions": "a", "dynamicTools": [], "threadId": "x"},
                                                        dict(turn, serviceTierForTurn="priority")))
        # Effort is a request parameter, never a cache input (test_native_effort_keys).
        self.assertEqual(key, codex_connect.variant_key({"baseInstructions": "a", "dynamicTools": []},
                                                        dict(turn, effort="high")))
        for parameters, changed in (({"baseInstructions": "b", "dynamicTools": []}, turn),
                                    ({"baseInstructions": "a", "dynamicTools": [{"name": "t"}]}, turn),
                                    ({"baseInstructions": "a", "dynamicTools": []}, dict(turn, model="n")),
                                    ({"baseInstructions": "a", "dynamicTools": []}, dict(turn, outputSchema={}))):
            self.assertNotEqual(key, codex_connect.variant_key(parameters, changed))

    def test_a_still_loaded_thread_needs_no_request_and_keeps_its_total(self):
        first = self.provider()
        codex_connect.open_thread(first, {}, lambda: [])
        self.process.on_event("thread/tokenUsage/updated", {"threadId": "chat-1", "tokenUsage": {"total": total(500)}})
        first.rpc.close()
        again = self.provider(native="chat-1")
        calls = len(self.process.calls)
        self.assertEqual(codex_connect.open_thread(again, {}, lambda: [])["thread"]["id"], "chat-1")
        self.assertEqual(len(self.process.calls), calls)
        self.assertEqual(again._usage.total["input_tokens"], 500)
        self.assertIs(self.runtime.owners["chat-1"], again.rpc)

    def test_a_thread_codex_no_longer_has_is_a_lost_context_shared_or_not(self):
        self.answers["thread/resume"] = LOST
        with self.assertRaises(ContextLost):
            codex_connect.open_thread(self.provider(native="old-fork"), {}, lambda: [])
        own = FakeProcess(None, None, {"thread/resume": LOST})
        with self.assertRaises(ContextLost):
            codex_connect.open_thread(Provider(own, "old-fork"), {}, lambda: [])
        held = FakeProcess(None, None, {"thread/resume": RpcError("thread x already has an active writer")})
        with self.assertRaises(ContextLost):
            codex_connect.open_thread(Provider(held, "x"), {}, lambda: [])
        self.answers["thread/resume"] = RpcError("Agent process exited")
        with self.assertRaises(RpcError) as caught:
            codex_connect.open_thread(self.provider(native="chat-9"), {}, lambda: [])
        self.assertNotIsInstance(caught.exception, ContextLost)

    def test_a_resumed_thread_is_loaded_and_owned(self):
        provider = self.provider(native="persisted")
        codex_connect.open_thread(provider, {"model": "m"}, lambda: [])
        self.assertEqual(self.process.calls, [("thread/resume", {"model": "m", "threadId": "persisted"})])
        self.assertTrue(self.runtime.has_loaded("persisted"))
        self.assertIsNone(provider._usage.total)

    def test_an_own_process_never_forks(self):
        own = FakeProcess(None, None, {"thread/start": {"thread": {"id": "own"}}})
        provider = Provider(own)
        codex_connect.open_thread(provider, {}, lambda: [])
        self.assertEqual(own.methods(), ["thread/start"])
        self.assertIsNone(getattr(provider, "_warm_after_turn", None))


class EligibilityTests(unittest.TestCase):
    def test_only_chatgpt_subscription_text_plan_chats_share(self):
        chat = SimpleNamespace(voice=None)
        self.assertTrue(codex_runtime.eligible(chat, "codex_subscription", False, True))
        self.assertFalse(codex_runtime.eligible(SimpleNamespace(voice=object()), "codex_subscription", False, True))
        self.assertFalse(codex_runtime.eligible(chat, "codex_subscription", False, False), "Collaborators, enhancement")
        self.assertFalse(codex_runtime.eligible(chat, "codex_subscription", False, True, catalog_only=True))
        self.assertFalse(codex_runtime.eligible(chat, "codex_subscription", True, True))
        for mode in ("codex_api", "team_codex", "openrouter_api"):
            self.assertFalse(codex_runtime.eligible(chat, mode, False, True))


class ProviderTests(PoolCase):
    """CodexProvider.connect on a shared app-server, against a fake one."""

    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settle_before_cleanup(self.temp.name)
        self.processes = []
        account = {"account": {"type": "chatgpt"}}
        self.answers = {"initialize": {"userAgent": "angelo_blender/0.162.0 (Mac OS; arm64)"}, "account/read": account,
                        "config/read": {"config": {}}, "model/list": {"data": []},
                        "thread/start": threads_counter("chat"),
                        "thread/fork": prediction_fork,
                        "turn/start": {"turn": {"id": "warm-turn"}}}
        processes = self.processes

        def process(command, *, env=None, cwd=None, spawn=None, on_event=None, on_request=None):
            processes.append(FakeProcess(on_event, on_request, self.answers))
            return processes[-1]
        for value in (patch.object(providers, "find_executable", return_value="/fixture/codex"),
                      patch.object(providers, "codex_command", return_value=(["/fixture/codex", "app-server"], {})),
                      patch.object(providers, "JsonRpcProcess", process)):
            value.start()
            self.addCleanup(value.stop)

    def chat(self, native=None, voice=None):
        controller = Controller(self.temp.name)
        self.addCleanup(controller.close)
        controller.set_document(("doc", 1))
        controller.state.update(provider="codex_subscription", native_id=native, toolset_version=TOOLSET_VERSION)
        controller.settings = {"connection_id": "connection", "model": "gpt-6.1-sol"}
        controller._begin_metrics()
        context = controller.runtime_context()
        context.voice = voice
        provider = providers.CodexProvider(context, context.settings)
        self.addCleanup(provider.close)
        return provider

    def warmed(self):
        """A first chat whose completed turn warmed its variant's base: ``(chat, process, base)``."""
        first = self.chat()
        self.assertEqual(first.connect(), "chat-1")
        process, runtime = self.processes[-1], first.rpc.runtime
        self.assertTrue(wait_for(lambda: any(base["state"] == "absent" for base in runtime.bases.values())),
                        "Nothing remembered: no resume and no model request yet")
        codex_connect.after_turn(first, "completed")
        self.assertTrue(wait_for(lambda: any(r.get("thread") for r in runtime.bases.values())))
        base = next(iter(runtime.bases.values()))["thread"]
        self.assertTrue(wait_for(lambda: base in runtime.warming))
        process.on_event("thread/tokenUsage/updated", {"threadId": base, "tokenUsage": {"total": total(23871)}})
        warm_up_reply(process, base)
        process.on_event("turn/completed", {"threadId": base, "turn": {"id": "warm-turn", "status": "completed"}})
        self.assertTrue(wait_for(lambda: next(iter(runtime.bases.values()))["state"] == "ready"))
        return first, process, base

    def test_chats_of_one_connection_share_one_process_and_new_chats_fork(self):
        first, process, base = self.warmed()
        self.assertEqual(process.methods()[:5],
                         ["initialize", "account/read", "config/read", "model/list", "thread/start"])
        second = self.chat()
        self.assertEqual(second.connect(), "fork-1")
        self.assertEqual(len(self.processes), 1, "No process start, no initialize for the second chat")
        self.assertEqual(process.methods().count("initialize"), 1)
        self.assertEqual(process.methods().count("account/read"), 2, "Every connect still checks the account")
        voice = self.chat()
        voice.connect(voice=True)
        self.assertEqual(process.methods()[-1], "thread/start", "A voice thread is never a fork")

    def test_a_new_process_resumes_the_remembered_base_without_a_model_request(self):
        _first, _process, base = self.warmed()
        codex_runtime.close_all()  # The app restarts.
        self.assertEqual(self.chat().connect(), "chat-3", "The new launch's first chat starts its thread")
        process = self.processes[-1]
        runtime = next(iter(codex_runtime._POOL.values()))
        self.assertTrue(wait_for(lambda: any(base["state"] == "ready" for base in runtime.bases.values())))
        resumed = [params for method, params in process.calls if method == "thread/resume"]
        self.assertEqual([(params["threadId"], params["excludeTurns"], "dynamicTools" in params) for params in resumed],
                         [(base, True, False)])
        self.assertNotIn("turn/start", process.methods(), "Resuming the base needs no model request")
        forked = self.chat()
        self.assertEqual(forked.connect(), "fork-1")
        self.assertEqual(forked._usage.total["input_tokens"], 23871, "The remembered total is the fork's baseline")

    def test_a_reconnect_lets_go_of_its_earlier_lease(self):
        provider = self.chat()
        provider.connect()
        earlier = provider.rpc
        provider.controller.state["native_id"] = None  # A new native context on the same provider.
        provider.connect()
        self.assertTrue(earlier.closed.is_set())
        self.assertEqual(provider.rpc.runtime.leases, [provider.rpc])

    def test_a_voice_thread_started_on_a_lease_is_owned_by_its_chat(self):
        provider = self.chat()
        provider.connect()
        provider.start_voice_thread()
        voice_thread = provider.thread_id
        self.assertIs(provider.rpc.runtime.owners[voice_thread], provider.rpc)

    def test_a_billing_mismatch_still_refuses_a_shared_connect(self):
        first = self.chat()
        first.connect()
        self.answers["account/read"] = {"account": {"type": "apiKey"}}
        refused = self.chat()
        with self.assertRaises(RpcError) as caught:
            refused.connect()
        self.assertIn("billing mode", str(caught.exception))
        self.assertIsNone(refused.rpc)
        self.assertFalse(self.processes[0].closed.is_set(), "The first chat keeps its process")
        self.answers["account/read"] = {"account": {"type": "chatgpt"}}
        self.chat().connect()
        self.assertEqual(len(self.processes), 2, "The next chat checks a new process")
        first.close()
        self.assertTrue(self.processes[0].closed.is_set())


class LegacyProvider:
    def __init__(self):
        self.controller, self.texts = None, []

    def bind_request(self, context):
        self.controller = context

    def run_request(self, context, native_id, text, observation=None):
        self.texts.append((native_id, text))
        return "completed"

    def close(self):
        pass


class LostContextTests(unittest.TestCase):
    """A chat whose Codex thread is gone continues in a new one with its history and no error."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.c = Controller(self.temp.name)
        self.c.set_document(("doc", 1))
        self.c.state.update(provider="codex_subscription", native_id="lost-fork", toolset_version=TOOLSET_VERSION)
        self.c.state["messages"] = [{"role": "user", "text": "Make the cube red."},
                                    {"role": "assistant", "text": "The cube is red now."}]
        key = route_key(self.c.state.get("connection_id"), "codex_subscription")
        self.c.state["runtime_sessions"][key] = {"native_id": "lost-fork", "toolset_version": TOOLSET_VERSION,
                                                 "synced_length": 2}
        self.c.provider = self.provider = LegacyProvider()
        self.c._begin_metrics()
        self.c.busy = True

    def tearDown(self):
        self.c.close()
        self.temp.cleanup()

    def test_the_message_goes_to_a_new_context_with_the_handoff(self):
        seen = []

        def connect(context):
            seen.append(context.state["native_id"])
            if len(seen) == 1:
                raise ContextLost("The chat's Codex thread is no longer available")
            context.emit("native_session", {"id": "new-thread"})
            return "new-thread"
        with patch.object(self.c, "_connect_runtime", side_effect=connect):
            self.c._run("Now make it blue.", {"objects": []})
        self.assertEqual(seen, ["lost-fork", None])
        native_id, text = self.provider.texts[0]
        self.assertEqual(native_id, "new-thread")
        self.assertTrue(text.startswith("Now make it blue."))
        self.assertIn("Make the cube red.", text)
        self.assertIn("The cube is red now.", text)
        self.c.pump_events()
        self.assertEqual(self.c.status, "Completed")
        self.assertEqual(self.c.state["native_id"], "new-thread")
        self.assertFalse(any(m.get("role") == "error" for m in self.c.state["messages"]))

    def test_a_delta_message_to_a_live_context_has_no_handoff(self):
        with patch.object(self.c, "_connect_runtime", return_value="lost-fork"):
            self.c._run("Now make it blue.", {"objects": []})
        self.assertNotIn("Make the cube red.", self.provider.texts[0][1])

    def test_voice_on_a_new_provider_starts_its_thread_through_the_voice_entry(self):
        calls = []

        class Fresh:
            def __init__(self, context, settings):
                pass

            def start_voice_thread(self):
                calls.append("voice")
                return "voice-thread"

            def connect(self):
                calls.append("connect")

            def close(self):
                pass
        self.c.provider = None
        with patch("angelo_agent.controller.CodexProvider", Fresh):
            self.assertEqual(self.c._connect_runtime(self.c.runtime_context(), voice=True), "voice-thread")
        self.assertEqual(calls, ["voice"])

    def test_invalidating_one_connection_closes_only_its_shared_processes(self):
        with patch("angelo_agent.controller.retire_codex_runtimes") as close:
            self.c.busy = False
            self.c.invalidate_runtime()
            close.assert_not_called()
            self.c.invalidate_runtime("connection")
            close.assert_called_once_with("connection")


if __name__ == "__main__":
    unittest.main()
