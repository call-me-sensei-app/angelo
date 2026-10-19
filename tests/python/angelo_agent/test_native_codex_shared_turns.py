# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Turns, accounts and retirement on a shared Codex app-server (codex_runtime, codex_connect, codex_turns).

``FakeCodex`` behaves as codex-cli 0.162 did in ``codex_stale_call_probe.py``:
a ``turn/start`` on a thread whose turn is still open is steered into that
turn (its ID is returned again), and ``turn/interrupt`` ends a turn unless the
test marks it stuck.
"""

import itertools
import os
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import codex_connect, codex_runtime, providers  # noqa: E402
from angelo_agent.codex_connect import ContextLost  # noqa: E402
from angelo_agent.codex_runtime import Retired, Runtime  # noqa: E402
from angelo_agent.codex_turns import STUCK, STUCK_SHARED, MessageNotSent  # noqa: E402
from angelo_agent.protocol import RpcError  # noqa: E402

KEY = ("connection-a", "codex_subscription", None, ("codex", "app-server"))


def wait_for(condition, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(.005)
    return False


class FakeCodex:
    def __init__(self, on_event, on_request, email="person@example.invalid", interrupt_delay=0.0):
        self.on_event, self.on_request = on_event, on_request
        self.closed, self.stderr_tail = threading.Event(), []
        self.open, self.stuck, self.inputs, self.calls, self.responses = {}, set(), {}, [], []
        self.email, self.interrupt_delay, self.started_events = email, interrupt_delay, True
        self.before_turn_reply = None
        self.ids = itertools.count(1)

    def request(self, method, params=None, timeout=60):
        params = params or {}
        self.calls.append((method, params))
        if self.closed.is_set():
            raise RpcError("Agent process exited")
        if method == "initialize":
            return {"userAgent": "angelo_blender/0.162.0 (Mac OS; arm64)"}
        if method == "account/read":
            return {"account": {"type": "chatgpt", "email": self.email, "planType": "plus"}}
        if method == "config/read":
            return {"config": {}}
        if method == "model/list":
            return {"data": []}
        if method == "thread/start":
            return {"thread": {"id": "thread-%d" % next(self.ids)}}
        if method == "thread/resume":
            return {"thread": {"id": params["threadId"]}}
        if method == "turn/start":
            thread = params["threadId"]
            if thread in self.open:  # Steered into the open turn.
                self.inputs[self.open[thread]].append(params["input"])
                return {"turn": {"id": self.open[thread]}}
            turn = "turn-%d" % next(self.ids)
            self.open[thread], self.inputs[turn] = turn, [params["input"]]
            if self.started_events:
                self.on_event("turn/started", {"threadId": thread, "turn": {"id": turn}})
            if self.before_turn_reply:
                self.before_turn_reply()
            return {"turn": {"id": turn}}
        if method == "turn/interrupt":
            if params["turnId"] not in self.stuck:
                timer = threading.Timer(self.interrupt_delay, self.complete,
                                        (params["threadId"], params["turnId"], "interrupted"))
                timer.daemon = True
                timer.start()
            return {}
        return {}

    def complete(self, thread, turn, status="completed"):
        if self.open.get(thread) == turn:
            del self.open[thread]
            self.on_event("turn/completed", {"threadId": thread, "turn": {"id": turn, "status": status}})

    def respond(self, request_id, result):
        self.responses.append((request_id, result))

    def write(self, message):
        pass

    def notify(self, method, params=None):
        pass

    def close(self):
        self.closed.set()

    def interrupts(self):
        return [params for method, params in self.calls if method == "turn/interrupt"]


class SharedCase(unittest.TestCase):
    def setUp(self):
        codex_runtime.close_all()
        self.addCleanup(codex_runtime.close_all)
        self.processes, self.process_options = [], {}

    def spawn(self, on_event, on_request):
        self.processes.append(FakeCodex(on_event, on_request, **self.process_options))
        return self.processes[-1]

    login = None  # A login file for the shared runtime (LoginFileTests).

    def provider(self, state=None, key=KEY, connection="connection-a"):
        """A real CodexProvider connected as ``CodexProvider.connect`` connects (transport, checks, open_thread)."""
        controller = SimpleNamespace(state=state if state is not None else {"native_id": None}, voice=None,
                                     cancelled=False, api_key="")
        provider = providers.CodexProvider(controller, {})
        self.addCleanup(provider.close)
        self.connect(provider, key, connection)
        return provider

    def connect(self, provider, key=KEY, connection="connection-a"):
        """As ``CodexProvider.connect``: a reconnect lets go of its lease; a failed connect closes the provider."""
        scope = provider._scope
        scope.new_transport()
        if isinstance(provider.rpc, codex_runtime.Lease):
            provider.rpc.close()
        try:
            return self._connect(provider, scope, key, connection)
        except Exception:
            provider.close()
            raise

    def _connect(self, provider, scope, key, connection):
        provider.rpc, _models = codex_runtime.transport(
            self.spawn, lambda method, params: scope.turns.observe(method, params), lambda *args: None,
            lambda rpc: codex_connect.initialize(rpc, provider.controller),
            lambda rpc: codex_connect.checks(rpc, provider.controller, "codex_subscription", False),
            key, connection, None, "codex_subscription", self.login)
        result = codex_connect.open_thread(provider, {"cwd": "/w"}, lambda: [], fork=False)
        provider.thread_id = provider.controller.state["native_id"] = result["thread"]["id"]
        return provider.thread_id

    def send(self, provider, text):
        return provider._scope.turns.start(provider, provider.rpc, {"threadId": provider.thread_id, "input": text},
                                           provider.controller)["turn"]["id"]


class StuckTurnTests(SharedCase):
    """F1: a turn that never ends is never steered into, and the chat moves to a new context."""

    def test_a_stuck_turn_abandons_its_thread_and_the_chat_continues_in_a_new_one(self):
        state = {"native_id": None}
        first = self.provider(state)
        stuck = self.send(first, "message 1")
        process = self.processes[0]
        process.stuck.add(stuck)
        first._scope.turns.settle_seconds = 0.3
        with self.assertRaises(MessageNotSent) as refused:
            self.send(first, "message 2")
        self.assertEqual(str(refused.exception), STUCK_SHARED)
        self.assertNotIn("restarted", STUCK_SHARED)
        runtime, stuck_thread = next(iter(codex_runtime._POOL.values())), state["native_id"]
        self.assertTrue(runtime.is_abandoned(stuck_thread))
        self.assertIsNone(first.rpc, "The provider let go of its lease")
        second = providers.CodexProvider(SimpleNamespace(state=state, voice=None, cancelled=False, api_key=""), {})
        self.addCleanup(second.close)
        with self.assertRaises(ContextLost):  # The controller then opens a new context with the handoff.
            self.connect(second)
        state["native_id"] = None
        self.assertNotEqual(self.connect(second), stuck_thread)
        resent = self.send(second, "message 2 (sent again)")
        self.assertNotEqual(resent, stuck)
        self.assertEqual(process.inputs[stuck], ["message 1"], "Nothing was steered into the stuck turn")
        self.assertEqual(len(self.processes), 1, "Other chats keep the shared process")

    def test_a_reconnecting_chat_waits_for_its_threads_open_turn_before_sending(self):
        self.process_options = {"interrupt_delay": 0.2}
        state = {"native_id": None}
        first = self.provider(state)
        older = self.send(first, "message 1")
        first.close()  # An error closed the provider; Codex still has the turn open for a moment.
        second = providers.CodexProvider(SimpleNamespace(state=state, voice=None, cancelled=False, api_key=""), {})
        self.addCleanup(second.close)
        self.connect(second)
        self.assertIn(older, second._scope.turns.open_on(state["native_id"]), "The process's record, not the chat's")
        newer = self.send(second, "message 2")
        self.assertNotEqual(newer, older)
        self.assertEqual(self.processes[0].inputs[older], ["message 1"])

    def test_a_providers_own_process_still_closes_with_the_original_text(self):
        state = {"native_id": None}
        own = self.provider(state, key=None)
        stuck = self.send(own, "message 1")
        self.processes[0].stuck.add(stuck)
        own._scope.turns.settle_seconds = 0.3
        with self.assertRaises(MessageNotSent) as refused:
            self.send(own, "message 2")
        self.assertEqual(str(refused.exception), STUCK)
        self.assertTrue(self.processes[0].closed.is_set())


class OrphanTurnTests(SharedCase):
    """F4: a turn whose chat let go is interrupted, however Codex reports it."""

    def setUp(self):
        super().setUp()
        self.runtime = codex_runtime.runtime_for(KEY, "connection-a", self.spawn)
        self.process = self.processes[0]
        self.lease = self.runtime.lease(lambda *args: None, lambda *args: None)
        self.lease.own("thread-a")

    def test_a_turn_reported_after_its_chat_let_go_is_interrupted(self):
        self.lease.close()
        self.process.on_event("turn/started", {"threadId": "thread-a", "turn": {"id": "turn-a"}})
        self.assertTrue(wait_for(lambda: self.process.interrupts() == [{"threadId": "thread-a", "turnId": "turn-a"}]))

    def test_a_turn_known_only_from_its_turn_start_reply_is_interrupted_on_release(self):
        self.process.started_events = False
        turn = self.lease.request("turn/start", {"threadId": "thread-a", "input": "hi"})["turn"]["id"]
        self.lease.close()
        self.assertTrue(wait_for(lambda: self.process.interrupts() == [{"threadId": "thread-a", "turnId": turn}]))

    def test_a_turn_start_answered_after_release_is_interrupted(self):
        self.process.started_events = False
        self.process.before_turn_reply = self.lease.close  # Stop closed the provider while turn/start was in flight.
        turn = self.lease.request("turn/start", {"threadId": "thread-a", "input": "hi"})["turn"]["id"]
        self.assertTrue(wait_for(lambda: self.process.interrupts() == [{"threadId": "thread-a", "turnId": turn}]))

    def test_an_unowned_tool_call_is_refused_and_its_turn_interrupted(self):
        self.lease.close()
        self.process.on_request(7, "item/tool/call", {"threadId": "thread-a", "turnId": "turn-x",
                                                      "tool": "angelo_scene_search"})
        self.assertFalse(self.process.responses[0][1]["success"])
        self.assertTrue(wait_for(lambda: self.process.interrupts() == [{"threadId": "thread-a", "turnId": "turn-x"}]))
        self.process.on_request(8, "item/tool/call", {"threadId": "thread-a", "turnId": "turn-x", "tool": "x"})
        time.sleep(.05)
        self.assertEqual(len(self.process.interrupts()), 1, "Interrupted once")

    def test_a_live_chats_turn_is_never_an_orphan(self):
        self.process.on_event("turn/started", {"threadId": "thread-a", "turn": {"id": "turn-a"}})
        self.lease.request("turn/start", {"threadId": "thread-a", "input": "x"})
        time.sleep(.05)
        self.assertNotIn({"threadId": "thread-a", "turnId": "turn-a"}, self.process.interrupts())

    def test_voice_turns_are_left_to_the_voice_session(self):
        self.lease.own("voice", voice=True)
        self.process.on_event("turn/started", {"threadId": "voice", "turn": {"id": "turn-v"}})
        self.lease.close()
        time.sleep(.05)
        self.assertEqual(self.process.interrupts(), [])
        self.assertEqual(self.runtime.turns.open_on("voice"), [])


class RetiredTests(SharedCase):
    """F5: a retired process admits no new chat, even when it is retired mid-connect."""

    def test_a_retired_runtime_admits_no_lease(self):
        runtime = codex_runtime.runtime_for(KEY, "connection-a", self.spawn)
        busy = runtime.lease(lambda *args: None, lambda *args: None)
        codex_runtime.retire_connection("connection-a")
        with self.assertRaises(Retired):
            runtime.lease(lambda *args: None, lambda *args: None)
        self.assertEqual(runtime.leases, [busy])
        self.assertIsNot(codex_runtime.runtime_for(KEY, "connection-a", self.spawn), runtime)

    def test_a_connect_passes_over_a_process_retired_before_its_lease(self):
        older = codex_runtime.runtime_for(KEY, "connection-a", self.spawn)
        older.lease(lambda *args: None, lambda *args: None)  # A chat keeps the older process alive.
        retired = []

        def start(rpc):
            if not retired:  # A sign-in lands while this chat connects.
                retired.append(True)
                codex_runtime.retire_connection("connection-a")
        lease, _models = codex_runtime.transport(self.spawn, lambda *args: None, lambda *args: None, start,
                                                 lambda rpc: [], KEY, "connection-a")
        self.assertIsNot(lease.runtime, older)
        self.assertTrue(older.retired)
        self.assertNotIn(lease, older.leases)


class AccountScopeTests(SharedCase):
    """F2: connections never share a process, and a changed sign-in never serves a new chat."""

    def test_connections_never_share_a_process_and_retire_on_their_own(self):
        command, env = ["codex", "app-server"], {}
        key_a = codex_runtime.pool_key("connection-a", "codex_subscription", command, env)
        key_b = codex_runtime.pool_key("connection-b", "codex_subscription", command, env)
        self.assertNotEqual(key_a, key_b, "Two reused-login connections with the same command")
        a = codex_runtime.runtime_for(key_a, "connection-a", self.spawn)
        b = codex_runtime.runtime_for(key_b, "connection-b", self.spawn)
        self.assertIsNot(a, b)
        codex_runtime.retire_connection("connection-b")
        self.assertTrue(b.retired)
        self.assertFalse(a.retired)

    def test_a_changed_account_never_serves_the_next_chat(self):
        first = self.provider()
        self.processes[0].email = "someone-else@example.invalid"  # Signed in again outside Angelo.
        second = self.provider()
        self.assertEqual(len(self.processes), 2, "The next chat checks a new process")
        self.assertIsNot(second.rpc.runtime, first.rpc.runtime)
        self.assertTrue(first.rpc.runtime.retired)
        self.assertFalse(self.processes[0].closed.is_set(), "The first chat keeps its process until it lets go")
        first.close()
        self.assertTrue(self.processes[0].closed.is_set())

    def test_the_same_account_keeps_one_process(self):
        self.provider()
        self.provider()
        self.assertEqual(len(self.processes), 1)

    def test_a_default_sign_in_retires_every_process_of_its_mode(self):
        subscription = codex_runtime.runtime_for(KEY, "connection-a", self.spawn, mode="codex_subscription")
        other = codex_runtime.runtime_for(("x",), "connection-x", self.spawn, mode="codex_api")
        codex_runtime.retire_mode("codex_subscription")
        self.assertTrue(subscription.retired)
        self.assertFalse(other.retired)

    def test_the_sign_in_pump_retires_on_a_default_login(self):
        source = (Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent/ui.py").read_text()
        start = source.index("            if logged_in:\n")
        branch = source[start:source.index("_authentication.start(mode, connection_settings", start)]
        self.assertIn("codex_runtime.retire_mode(mode)", branch)
        self.assertIn("controller.invalidate_runtime(record[\"id\"])", branch, "A connection's sign-in retires its own")


class LoginFileTests(SharedCase):
    """P1: a sign-out or account switch outside Angelo (the profile's login file) never serves a new chat.

    A running codex app-server keeps the login it started with (``auth_reload_apikey.py``): only the file changes.
    """

    def setUp(self):
        super().setUp()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.login = Path(folder.name) / "auth.json"
        self.login.write_text('{"tokens": "first"}')

    def test_a_signed_out_login_retires_the_process_for_the_next_chat(self):
        first = self.provider()
        self.login.unlink()  # The person signs out in the Codex CLI.
        second = self.provider()
        self.assertEqual(len(self.processes), 2)
        self.assertIsNot(second.rpc.runtime, first.rpc.runtime)
        self.assertTrue(first.rpc.runtime.retired)
        self.assertFalse(self.processes[0].closed.is_set(), "The first chat keeps its process until it lets go")

    def test_another_account_or_a_rewritten_file_retires_it_and_an_unchanged_one_does_not(self):
        self.provider()
        self.provider()
        self.assertEqual(len(self.processes), 1, "An unchanged login: one process")
        os.utime(self.login, ns=(1, 1))  # Rewritten in place (same size): another account, or a token refresh.
        self.provider()
        self.assertEqual(len(self.processes), 2)
        replaced = self.login.with_name("auth.json.new")
        replaced.write_text('{"tokens": "secnd"}')
        os.replace(replaced, self.login)  # Atomically replaced, as Codex writes it.
        self.provider()
        self.assertEqual(len(self.processes), 3)

    def test_a_sign_in_after_the_process_started_retires_it(self):
        self.login.unlink()
        self.provider()
        self.login.write_text('{"tokens": "first"}')
        self.provider()
        self.assertEqual(len(self.processes), 2)

    def test_each_connect_costs_one_stat_and_reads_nothing(self):
        self.provider()
        real_stat, stats = os.stat, []

        def counted(path, *args, **kwargs):
            stats.append(str(path))
            return real_stat(path, *args, **kwargs)
        with unittest.mock.patch.object(codex_runtime.os, "stat", counted), \
                unittest.mock.patch("builtins.open", side_effect=AssertionError("The login file is never read")):
            self.provider()
        self.assertEqual(stats, [str(self.login)])
        started = time.perf_counter()
        for _ in range(2000):
            codex_runtime.login_stamp(self.login)
        self.assertLess((time.perf_counter() - started) / 2000, 0.001, "About a stat")

    def test_the_login_file_is_the_profiles(self):
        self.assertEqual(codex_runtime.login_file({"CODEX_HOME": "/profiles/a"}), Path("/profiles/a/auth.json"))
        self.assertEqual(codex_runtime.login_file({}), Path.home() / ".codex" / "auth.json")

    def test_a_status_check_without_an_account_retires_that_profiles_processes(self):
        default = codex_runtime.runtime_for(KEY, "connection-a", self.spawn, login=self.login)
        other = codex_runtime.runtime_for(("other",), "connection-b", self.spawn, login=Path("/elsewhere/auth.json"))
        codex_runtime.retire_login(self.login)
        self.assertTrue(default.retired)
        self.assertFalse(other.retired)
        source = (Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent/ui.py").read_text()
        start = source.index("    changes = _authentication.pump()")
        pump = source[start:source.index("            if logged_in:\n", start)]
        self.assertIn('status == "Not connected"', pump)
        self.assertIn("codex_runtime.retire_login(", pump)
        self.assertIn('codex_runtime.retire_connection(record["id"])', pump)


class StartRaceTests(SharedCase):
    """P3: a sign-in during a new process's first ``initialize`` moves the connect on to the next process."""

    def test_a_process_retired_and_closed_during_its_start_is_passed_over(self):
        retired = []

        def start(rpc):
            if not retired:
                retired.append(True)
                codex_runtime.retire_mode("codex_subscription")  # A default-login sign-in completes right now.
            return rpc.request("initialize")
        lease, _models = codex_runtime.transport(self.spawn, lambda *args: None, lambda *args: None, start,
                                                 lambda rpc: [], KEY, "connection-a", None, "codex_subscription")
        self.assertEqual(len(self.processes), 2)
        self.assertTrue(self.processes[0].closed.is_set())
        self.assertIs(lease.runtime.rpc, self.processes[1])

    def test_a_real_start_failure_still_fails_the_connect(self):
        def start(rpc):
            raise RpcError("Angelo requires Codex 0.160.0 or newer")
        with self.assertRaisesRegex(RpcError, "0.160.0"):
            codex_runtime.transport(self.spawn, lambda *args: None, lambda *args: None, start, lambda rpc: [], KEY,
                                    "connection-a", None, "codex_subscription")
        self.assertEqual(len(self.processes), 1)


if __name__ == "__main__":
    unittest.main()
