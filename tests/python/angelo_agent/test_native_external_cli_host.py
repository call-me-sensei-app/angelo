# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The CLI host through the real listener and client: status, ``session new``, and no tool for any client (2.0.1).

A real listener and client run against a bpy-free host environment: real
``Controller`` and ``SessionCollection`` in a fresh /tmp profile, a fake
executor, and the test thread acting as Blender's main-thread pump. Never the
real ``$TMPDIR/angelo-<uid>`` directory or a real profile.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).
from host_waits import CAP_S  # noqa: E402  A completion wait's cap: only a hang reaches it.

import itertools
import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import cli_client
from angelo_agent import external_host
from angelo_agent.controller import Controller, ToolDispatchBudget
from angelo_agent.external_host import ClientRegistry, ExternalHost, external_label
from angelo_agent.sessions import SessionCollection
from host_test_policy import environment_skip


class Executor:
    native_job_tools = ()

    def __init__(self, state):
        self.state, self.calls, self.studio_context = state, [], None

    def precondition(self, name, arguments):
        return None

    def execute(self, name, arguments):
        self.calls.append(name)
        return {"objects": [{"name": "Cube"}], "path": str(Path(self.state) / "refs" / "v.png"),
                "note": "profile at " + str(self.state)}


class Environment:
    def __init__(self, state):
        self.state, self.document, self.requested = state, ("doc-a", 1), True
        self._controllers, self._sessions = {}, SessionCollection(state)

    def state_directory(self):
        return self.state

    def document_key(self):
        return self.document

    def controllers(self):
        return self._controllers

    def sessions(self):
        return self._sessions

    def access_requested(self):
        return self.requested

    def make_controller(self, session_id):
        controller = Controller(self.state, session_id=session_id)
        controller.set_document(self.document)
        return controller

    def describe(self):
        return {"blender_version": "test", "addon_version": "0.1.0"}

    def private_roots(self):
        return [(self.state, "<angelo-state>")]


class DrainPacingTests(unittest.TestCase):
    """The main-thread drain is time-sliced; no listener is needed to check its pacing (so it runs on Windows)."""

    def test_a_tick_that_starts_past_its_budget_still_serves_one_request(self):
        root = Path(tempfile.mkdtemp(prefix="scli-drain-"))
        self.addCleanup(shutil.rmtree, root, True)
        host = ExternalHost(Environment(str(root / "state")), environ={}, runtime_directory=root / "run")
        waiting = [SimpleNamespace(kind="status", claim=lambda: True, number=number) for number in range(3)]
        host.service = SimpleNamespace(work=SimpleNamespace(take=lambda: waiting.pop(0) if waiting else None))
        served = []
        host.handle_status = served.append
        # Every clock reading is a second after the last, so each tick's budget is gone before its first request.
        clock = itertools.count(0.0, 1.0)
        with patch.object(external_host.time, "monotonic", side_effect=lambda: next(clock)):
            for tick in range(1, 4):
                host.drain()
                self.assertEqual([work.number for work in served], list(range(tick)))
            host.drain()  # Nothing left: an empty queue ends the tick.
        self.assertEqual(len(served), 3)


@unittest.skipIf(sys.platform == "win32", environment_skip("Needs the Unix-socket listener, which Windows does not "
                                                           "open yet (doc/angelo/external_cli.rst)"))
class ExternalHostTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="scli-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.state = str(self.root / "state")
        self.env = Environment(self.state)
        self.executor = Executor(self.state)
        self.host = ExternalHost(self.env, environ={}, runtime_directory=self.root / "run")
        self.addCleanup(self.host.stop)
        self.addCleanup(lambda: [c.close() for c in self.env._controllers.values()])

    def tick(self):
        """One ui.pump: host drain, then each chat's events and shared tool dispatch."""
        self.host.pump()
        for controller in list(self.env._controllers.values()):
            controller.pump_events()
            controller.pump_tools(self.executor, self.env.document, budget=ToolDispatchBudget(seconds=60))

    def cli(self, *argv, label="agent"):
        self.host.pump()  # Listening before the client reads the endpoint record.
        result, done = {}, threading.Event()

        def run():
            options = cli_client.parser().parse_args(list(argv))
            result["value"] = cli_client.run(cli_client.build_request(options), self.state, label, 20)
            done.set()
        threading.Thread(target=run, daemon=True).start()
        deadline = time.monotonic() + CAP_S
        while not done.is_set() and time.monotonic() < deadline:
            self.tick()
            time.sleep(0.005)
        self.assertTrue(done.is_set(), "CLI request did not finish")
        self.tick()
        return result["value"]

    def conversation(self, label="agent"):
        return self.env._controllers[ClientRegistry(self.state).load()[label]]

    def test_toggle_and_kill_switch_control_the_listener(self):
        self.host.pump()
        endpoint = Path(self.state) / "cli" / "endpoint.json"
        self.assertTrue(endpoint.is_file())
        self.env.requested = False
        self.host.pump()
        self.assertFalse(endpoint.exists())
        self.assertEqual(self.cli("status")["code"], "unavailable")
        self.env.requested = True
        self.host.environ = {external_host.KILL_SWITCH: "1"}
        self.host.pump()
        self.assertFalse(endpoint.exists())
        self.assertIn("ANGELO_DISABLE_LOCAL_CLI", self.host.status)

    def raw(self, request, label="agent"):
        """One wire request an older client may still send (``call``, ``tools``), through the real listener."""
        self.host.pump()
        result, done = {}, threading.Event()

        def run():
            result["value"] = cli_client.run(request, self.state, label, 20)
            done.set()
        threading.Thread(target=run, daemon=True).start()
        deadline = time.monotonic() + CAP_S
        while not done.is_set() and time.monotonic() < deadline:
            self.tick()
            time.sleep(0.005)
        self.assertTrue(done.is_set(), "CLI request did not finish")
        return result["value"]

    def test_status_and_session_new_through_the_real_listener(self):
        status = self.cli("status")
        self.assertEqual((status["code"], status["result"]["conversation"]), ("ok", None))
        self.assertEqual(status["result"]["commands"], ["status", "session new"])
        rotated = self.cli("session", "new")
        self.assertEqual(rotated["code"], "ok")
        controller = self.conversation()
        self.assertEqual(rotated["conversation"], controller.state["id"])
        self.assertEqual(controller.external_client, "agent")
        self.assertEqual(self.env._sessions.records[controller.state["id"]]["title"], "External: agent")
        self.assertEqual(external_label(self.state, controller.state["id"]), "agent")
        described = self.cli("status")["result"]["conversation"]
        self.assertEqual((described["id"], described["busy"], described["pending_calls"]),
                         (controller.state["id"], False, 0))
        with self.assertRaises(RuntimeError):
            controller.send("hello")   # A CLI label's conversation never starts a model turn.

    def test_no_client_runs_a_tool(self):
        """2.0.1 (break-it review S5): ``call`` and the tool catalogue are refused for every client, before any
        controller, and the refusal is audited."""
        with patch.object(Controller, "request_tool", side_effect=AssertionError("reached the controller")):
            for request in ({"command": "call", "args": {"tool": "angelo_scene_search", "arguments": {}, "id": "r"}},
                            {"command": "call", "args": {"tool": "angelo_memory_read", "arguments": {}}},
                            {"command": "call", "args": {"tool": "angelo_execute_python",
                                                         "arguments": {"description": "x", "code": "pass"}}},
                            {"command": "tools", "args": {}}):
                answer = self.raw(request)
                self.assertEqual((answer["code"], answer["exit"]), ("refused", 4), request)
                self.assertIn("through Angelo's own agent", answer["message"])
        self.assertEqual(self.executor.calls, [])
        audit = (Path(self.state) / "cli" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertTrue(any(json.loads(line).get("command") == "call" and json.loads(line).get("code") == "refused"
                            for line in audit))

    def test_a_document_change_shows_in_status_until_session_new(self):
        self.cli("session", "new")
        old = self.conversation()
        self.env.document = ("doc-b", 1)
        old.set_document(self.env.document)  # What ui.document_load_pre does for every chat.
        self.assertFalse(self.cli("status")["result"]["conversation"]["document_valid"])
        rotated = self.cli("session", "new")
        self.assertEqual(rotated["result"]["previous"], old.state["id"])
        self.assertNotEqual(rotated["conversation"], old.state["id"])
        self.assertIn(old.state["id"], self.env._sessions.records)  # The old transcript stays readable.
        self.assertTrue(self.cli("status")["result"]["conversation"]["document_valid"])

    def test_internal_errors_latch_off_without_breaking_the_pump(self):
        class Broken(Environment):
            def document_key(self):
                raise KeyError("boom")
        ui = type("UiStub", (), {})()
        with patch.object(external_host, "UiEnvironment", lambda _: Broken(self.state)), \
                patch.object(external_host, "_host", None):
            with patch.object(ExternalHost, "start", lambda self: setattr(self, "server", object()) or True), \
                    patch.object(ExternalHost, "stop", lambda self, reason="Off": setattr(self, "server", None)):
                external_host.pump(ui)  # Must not raise into ui.pump.
                self.assertTrue(external_host._host.failed)
                self.assertIn("internal error", external_host._host.status)


if __name__ == "__main__":
    unittest.main()
