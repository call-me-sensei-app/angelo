# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""External conversations through the real Controller.request_tool path.

A real listener and client run against a bpy-free host environment: real
``Controller`` and ``SessionCollection`` in a fresh /tmp profile, a fake
executor, and the test thread acting as Blender's main-thread pump. Never the
real ``$TMPDIR/angelo-<uid>`` directory or a real profile.
"""

import itertools
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
from angelo_agent.invocations import document_digest
from angelo_agent.sessions import SessionCollection


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


@unittest.skipIf(sys.platform == "win32", "Needs the Unix-socket listener, which Windows does not open yet "
                                         "(doc/angelo/external_cli.rst)")
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
        deadline = time.monotonic() + 15
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

    def test_read_runs_through_request_tool_and_is_recorded_in_the_external_transcript(self):
        status = self.cli("status")
        self.assertEqual((status["code"], status["result"]["conversation"]), ("ok", None))
        envelope = self.cli("call", "angelo_scene_search", '{"limit": 5}', "--id", "read-1")
        self.assertEqual((envelope["code"], envelope["exit"]), ("ok", 0))
        self.assertEqual(envelope["document"], document_digest(repr(("doc-a", 1))))
        self.assertEqual(envelope["result"]["path"], "<withheld>")
        self.assertEqual(envelope["result"]["note"], "profile at <angelo-state>")
        self.assertNotIn(self.state, str(envelope))
        controller = self.conversation()
        self.assertEqual(envelope["conversation"], controller.state["id"])
        self.assertEqual(controller.external_client, "agent")
        self.assertEqual(self.env._sessions.records[controller.state["id"]]["title"], "External: agent")
        self.assertFalse(controller.busy)
        self.assertEqual(controller.state["invocations"]["entries"], {})  # Reads never reserve mutation IDs.
        activity = [m for m in controller.state["messages"] if m["role"] == "activity"]
        self.assertEqual((activity[-1]["tool"], activity[-1]["status"]), ("angelo_scene_search", "completed"))
        self.assertEqual(self.executor.calls, ["angelo_scene_search"])
        self.assertEqual(external_label(self.state, controller.state["id"]), "agent")
        with self.assertRaises(RuntimeError):
            controller.send("hello")
        replay = self.cli("call", "angelo_scene_search", '{"limit": 5}', "--id", "read-1")
        self.assertTrue(replay["call"]["replayed"])
        self.assertEqual(self.executor.calls, ["angelo_scene_search"])
        changed = self.cli("call", "angelo_scene_search", '{"limit": 6}', "--id", "read-1")
        self.assertEqual(changed["code"], "refused")
        receipt = self.cli("status", "--call", "read-1")["result"]["call"]
        self.assertEqual((receipt["state"], receipt["receipt_phase"]), ("finished", "completed"))
        self.assertEqual(self.cli("status", "--call", "never")["result"]["call"]["state"], "unknown")

    def test_mutations_never_reach_the_controller(self):
        self.cli("call", "angelo_inspect_scene")
        controller = self.conversation()
        before = dict(controller.state["results"])
        with patch.object(Controller, "request_tool", side_effect=AssertionError("reached the controller")):
            for tool, arguments in (("angelo_create_cube", '{"name":"X","size":1,"location":[0,0,0]}'),
                                    ("angelo_execute_python", '{"description":"x","code":"pass"}'),
                                    ("angelo_write_file", '{"path":"a.txt","content":"x"}'),
                                    ("angelo_memory_update", '{}')):
                self.assertEqual(self.cli("call", tool, arguments, "--id", "m-" + tool)["code"], "refused")
        self.assertEqual(controller.state["results"], before)
        self.assertEqual(self.executor.calls, ["angelo_inspect_scene"])

    def test_document_change_is_stale_until_explicit_session_new(self):
        first = self.cli("call", "angelo_inspect_scene")
        old = self.conversation()
        self.env.document = ("doc-b", 1)
        old.set_document(self.env.document)  # What ui.document_load_pre does for every chat.
        stale = self.cli("call", "angelo_inspect_scene")
        self.assertEqual((stale["code"], stale["exit"], stale["retry"]), ("stale", 5, "after_observe"))
        self.assertFalse(self.cli("status")["result"]["conversation"]["document_valid"])
        rotated = self.cli("session", "new")
        self.assertEqual(rotated["result"]["previous"], old.state["id"])
        self.assertNotEqual(rotated["conversation"], old.state["id"])
        self.assertIn(old.state["id"], self.env._sessions.records)  # The old transcript stays readable.
        fresh = self.cli("call", "angelo_inspect_scene")
        self.assertEqual((fresh["code"], fresh["conversation"]), ("ok", rotated["conversation"]))
        guard = self.cli("call", "angelo_inspect_scene", "--conversation", old.state["id"])
        self.assertEqual(guard["code"], "stale")
        expected = self.cli("call", "angelo_inspect_scene", "--expect-document", first["document"])
        self.assertEqual(expected["code"], "stale")

    def test_concurrent_reads_share_one_fenced_request(self):
        self.cli("call", "angelo_inspect_scene")
        controller = self.conversation()
        results, threads = [], []
        for index in range(3):
            def run(index=index):
                options = cli_client.parser().parse_args(["call", "angelo_inspect_scene", "--id", f"c-{index}"])
                results.append(cli_client.run(cli_client.build_request(options), self.state, "agent", 20))
            threads.append(threading.Thread(target=run, daemon=True))
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + 10
        while len(controller.pending) < 3 and time.monotonic() < deadline:
            self.host.pump()  # Admit all three before dispatching any.
            time.sleep(0.005)
        self.assertEqual(len(controller.pending), 3)
        self.assertEqual({call.generation for call in controller.pending.values()}, {controller._request_generation})
        while any(t.is_alive() for t in threads) and time.monotonic() < deadline + 10:
            self.tick()
            time.sleep(0.005)
        self.assertEqual([r["code"] for r in results], ["ok", "ok", "ok"])
        self.tick()
        self.assertFalse(controller.busy)

    def test_turning_access_off_ends_queued_reads_as_unavailable(self):
        self.cli("call", "angelo_inspect_scene")
        controller = self.conversation()
        result, done = {}, threading.Event()

        def run():
            options = cli_client.parser().parse_args(["call", "angelo_inspect_scene", "--id", "late"])
            result["value"] = cli_client.run(cli_client.build_request(options), self.state, "agent", 20)
            done.set()
        threading.Thread(target=run, daemon=True).start()
        deadline = time.monotonic() + 10
        while not controller.pending and time.monotonic() < deadline:
            self.host.pump()  # Admit, but never dispatch the queued read.
            time.sleep(0.005)
        self.assertTrue(controller.pending)
        self.env.requested = False
        self.host.pump()
        self.assertTrue(done.wait(10))
        self.assertEqual(result["value"]["code"], "unavailable")
        self.assertFalse(controller.busy or controller.pending)
        self.assertEqual(self.executor.calls, ["angelo_inspect_scene"])

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
