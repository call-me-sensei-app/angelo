# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's MCP server for external AI apps, end to end without Blender.

A real listener, the real ``external_mcp.Server`` (the stdio launcher's code),
real ``Controller`` and ``SessionCollection`` in a fresh /tmp profile and a fake
executor; the test thread acts as Blender's main-thread pump. Contract:
doc/angelo/external_mcp.rst.
"""

import io
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import cli_client, managed_policy
from angelo_agent import external_cli_protocol as protocol
from angelo_agent.controller import ToolDispatchBudget
from angelo_agent.external_clients import AppRegistry, EDIT, PYTHON, READ, TASKS, tools_for
from angelo_agent.external_host import ClientRegistry, ExternalHost
from angelo_agent.external_mcp import Server
from angelo_agent.managed_policy import Policy
from test_native_external_cli_host import Environment, Executor

CUBE = {"name": "Box", "size": 1, "location": [0, 0, 0]}
SCRIPT = {"description": "Set a value", "code": "result = 1"}


class TaskProvider:
    """A provider turn without a model: it asks for one script (the person decides) and replies."""

    def __init__(self, release):
        self.release, self.texts = release, []

    def connect(self):
        return "native-task"

    def run_request(self, context, native_id, text, observation=None):
        import threading as _threading
        self.texts.append(text)
        done, answer = _threading.Event(), []
        context.request_tool("task-script", "angelo_execute_python", SCRIPT, lambda r: (answer.append(r), done.set()))
        done.wait(20)
        self.release.wait(20)
        if context.cancelled:
            return "interrupted"
        context.emit("message_complete", {"item": "reply", "text": "Made the cube."})
        return "completed"

    def stop_request(self, fence):
        self.release.set()

    def close(self):
        pass


class AppEnvironment(Environment):
    def __init__(self, state):
        super().__init__(state)
        self.prepared, self.provider = [], None

    def prepare_task(self, controller):
        self.prepared.append(controller.state["id"])
        if self.provider is None:
            raise RuntimeError("This chat has no connection. Connect Claude or ChatGPT in the chat.")
        controller.provider = self.provider


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="smcp-reg-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.registry = AppRegistry(self.root / "state")

    def test_connect_writes_an_owner_only_key_that_never_leaves_angelo(self):
        name = self.registry.connect("claude_code")
        self.assertEqual(name, "claude-code")
        key_file = self.registry.keys / name
        self.assertEqual(stat.S_IMODE(os.lstat(key_file).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.lstat(self.registry.keys).st_mode), 0o700)
        self.assertEqual(len(self.registry.key(name)), 32)
        record = json.loads(self.registry.path.read_text())
        self.assertNotIn(key_file.read_text().strip(), json.dumps(record), "the registry never holds a key")
        self.assertEqual(self.registry.load()[name]["levels"], (READ, EDIT, PYTHON, TASKS))

    def test_reconnect_rotates_the_key_and_disconnect_deletes_it(self):
        name = self.registry.connect("codex")
        first = self.registry.key(name)
        self.registry.connect("codex")
        self.assertNotEqual(self.registry.key(name), first)
        self.assertTrue(self.registry.disconnect(name))
        self.assertIsNone(self.registry.key(name))
        self.assertEqual(self.registry.snapshot(), {})
        self.assertFalse(self.registry.disconnect(name))

    def test_reading_is_always_allowed_and_unsafe_keys_are_ignored(self):
        name = self.registry.connect("cursor", levels=())
        self.assertEqual(self.registry.load()[name]["levels"], (READ,))
        os.chmod(self.registry.keys / name, 0o644)
        self.assertIsNone(self.registry.key(name), "a key others can read is not a key")
        self.assertEqual(self.registry.snapshot(), {})

    def test_levels_map_to_disjoint_tool_families(self):
        self.assertIn("angelo_view", tools_for((READ,)))
        self.assertNotIn("angelo_scene_program", tools_for((READ,)))
        self.assertIn("angelo_scene_program", tools_for((READ, EDIT)))
        self.assertNotIn("angelo_execute_python", tools_for((READ, EDIT)))
        self.assertIn("angelo_web_check", tools_for((READ,)))
        for name in ("angelo_web_export", "angelo_web_preview"):   # A file written, a server started.
            self.assertNotIn(name, tools_for((READ,)))
            self.assertIn(name, tools_for((READ, EDIT)))
        self.assertIn("angelo_execute_python", tools_for((READ, PYTHON)))
        everything = tools_for((READ, EDIT, PYTHON, TASKS))
        for never in ("angelo_write_file", "angelo_delete_file", "angelo_read_file", "angelo_memory_update",
                      "angelo_delegate", "angelo_studio_generate", "angelo_set_workspace_mode", "angelo_flow_run"):
            self.assertNotIn(never, everything)


@unittest.skipIf(sys.platform == "win32", "Needs the Unix-socket listener (Windows named pipe: a later phase)")
class AppTests(unittest.TestCase):
    def setUp(self):
        managed_policy.reset(Policy())
        self.addCleanup(managed_policy.reset)
        self.root = Path(tempfile.mkdtemp(prefix="smcp-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.state = str(self.root / "state")
        self.env = AppEnvironment(self.state)
        self.executor = Executor(self.state)
        self.host = ExternalHost(self.env, environ={}, runtime_directory=self.root / "run")
        self.addCleanup(self.host.stop)
        self.addCleanup(lambda: [c.close() for c in self.env._controllers.values()])
        self.name = AppRegistry(self.state).connect("claude_code")
        self.approve = None   # A test's stand-in for the person at an approval card.

    def tick(self):
        self.host.pump()
        for controller in list(self.env._controllers.values()):
            controller.pump_events()
            controller.pump_tools(self.executor, self.env.document, budget=ToolDispatchBudget(seconds=60),
                                  apply_mutation=lambda call, c=controller: c.execute_tool(call, self.executor,
                                                                                            c.document))
            for call in list(controller.pending.values()):
                if call.approval and not call.executing and not call.automatic and self.approve is not None:
                    self.approve(controller, call)

    def server(self, name=None):
        out = io.StringIO()
        return Server(name or self.name, self.state, stdout=out), out

    def rpc(self, method, params=None, name=None, timeout=20):
        self.host.pump()
        server, out = self.server(name)
        server.dispatch({"jsonrpc": "2.0", "id": 7, "method": method, "params": params or {}})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            lines = [json.loads(line) for line in out.getvalue().splitlines() if line]
            answers = [line for line in lines if line.get("id") == 7]
            if answers:
                self.tick()
                return answers[0]
            self.tick()
            time.sleep(0.005)
        self.fail("no MCP answer for " + method)

    def call(self, tool, arguments=None, name=None):
        return self.rpc("tools/call", {"name": tool, "arguments": arguments or {}}, name=name)["result"]

    def conversation(self):
        return self.env._controllers[ClientRegistry(self.state).load()[self.name]]

    def test_initialize_and_tools_follow_the_apps_levels(self):
        init = self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})["result"]
        self.assertEqual(init["serverInfo"]["name"], "angelo")
        self.assertIn("not Blender MCP", init["instructions"])
        self.assertEqual(init["protocolVersion"], "2025-06-18")
        old = self.rpc("initialize", {"protocolVersion": "2099-01-01"})["result"]
        self.assertEqual(old["protocolVersion"], "2025-06-18")
        names = {tool["name"] for tool in self.rpc("tools/list")["result"]["tools"]}
        self.assertIn("angelo_task", names)
        self.assertIn("angelo_execute_python", names)
        AppRegistry(self.state).set_levels(self.name, (READ,))
        names = {tool["name"] for tool in self.rpc("tools/list")["result"]["tools"]}
        self.assertNotIn("angelo_scene_program", names)
        self.assertNotIn("angelo_task", names)
        for tool in self.rpc("tools/list")["result"]["tools"]:
            self.assertEqual(tool["inputSchema"].get("type"), "object", tool["name"])
            self.assertTrue(tool["description"])
        self.assertEqual(self.rpc("tools/list", name="unknown")["result"]["tools"], [])

    def test_a_read_runs_through_the_external_conversation(self):
        result = self.call("angelo_inspect_scene")
        self.assertFalse(result["isError"], result)
        self.assertIn("Cube", result["content"][-1]["text"])
        self.assertNotIn(self.state, json.dumps(result))
        controller = self.conversation()
        self.assertEqual(controller.external_client, self.name)
        rows = controller.state["messages"]
        self.assertTrue(any(row.get("request_kind") == "external_request" for row in rows))
        self.assertTrue(any(row.get("role") == "activity" and row.get("tool") == "angelo_inspect_scene" for row in rows))
        self.assertEqual(self.executor.calls, ["angelo_inspect_scene"])

    def test_a_bounded_edit_runs_under_approve_for_me_and_a_script_waits_for_the_person(self):
        result = self.call("angelo_create_cube", CUBE)
        self.assertFalse(result["isError"], result)
        self.assertIn("angelo_create_cube", self.executor.calls)
        controller = self.conversation()
        self.assertEqual(controller.state["options"]["approval"], "AUTO_BOUNDED")
        seen = []

        def person(controller, call):
            seen.append(call.name)
            controller.review_script(call)
            controller.execute_tool(call, self.executor, controller.document)
        self.approve = person
        result = self.call("angelo_execute_python", SCRIPT)
        self.assertEqual(seen, ["angelo_execute_python"], "every script from an app gets the person's review")
        self.assertFalse(result["isError"], result)
        self.assertFalse(controller.trusted_scripts)
        with self.assertRaises(RuntimeError):
            controller.set_trusted_scripts(True)

    def test_a_declined_call_says_so_and_ends_the_request(self):
        def person(controller, call):
            controller.decline_tool(call.id)
        self.approve = person
        result = self.call("angelo_execute_python", SCRIPT)
        self.assertTrue(result["isError"])
        self.assertIn("declined", json.dumps(result).lower())
        self.assertNotIn("angelo_execute_python", self.executor.calls)

    def test_an_app_cannot_call_beyond_its_levels(self):
        AppRegistry(self.state).set_levels(self.name, (READ,))
        for tool, arguments in (("angelo_create_cube", CUBE), ("angelo_execute_python", SCRIPT),
                                ("angelo_write_file", {"path": "a.txt", "content": "x"})):
            result = self.call(tool, arguments)
            self.assertTrue(result["isError"], tool)
        self.assertEqual(self.executor.calls, [])

    def test_a_disconnected_or_forged_app_is_refused(self):
        registry = AppRegistry(self.state)
        key = registry.key(self.name)
        registry.disconnect(self.name)
        result = self.call("angelo_inspect_scene")
        self.assertTrue(result["isError"])
        self.assertIn("not connected", result["content"][0]["text"])
        # A key proof for a name Angelo does not know is an app the person disconnected.
        self.host.pump()
        value = cli_client.run({"command": "status", "args": {}}, self.state, self.name, 10, key=key)
        self.assertEqual(value["code"], "unavailable")
        # A connected app's name needs that app's key: the launch token alone is not enough.
        registry.connect("claude_code")
        self.host.pump()
        value = cli_client.run({"command": "call", "args": {"tool": "angelo_inspect_scene", "arguments": {},
                                                              "id": "x", "presentation": "mcp"}},
                               self.state, self.name, 10)
        self.assertEqual(value["code"], "unavailable")
        self.assertEqual(self.executor.calls, [])

    def test_a_cli_label_stays_read_only(self):
        self.host.pump()
        value = cli_client.run({"command": "call", "args": {"tool": "angelo_create_cube", "arguments": CUBE, "id": "c",
                                                              "wait": 5}}, self.state, "plain-cli", 10)
        self.assertEqual(value["code"], "refused")
        value = cli_client.run({"command": "call", "args": {"tool": "angelo_inspect_scene", "arguments": {}, "id": "m",
                                                              "presentation": "mcp"}}, self.state, "plain-cli", 10)
        self.assertEqual(value["code"], "refused")

    def test_tasks_need_the_level_and_say_why_they_did_not_start(self):
        result = self.call("angelo_task", {"task": "Make a cube", "wait_seconds": 0})
        self.assertTrue(result["isError"])
        self.assertIn("no connection", json.dumps(result))
        self.assertEqual(len(self.env.prepared), 1)
        AppRegistry(self.state).set_levels(self.name, (READ, EDIT))
        result = self.call("angelo_task", {"task": "Make a cube"})
        self.assertTrue(result["isError"])
        self.assertEqual(len(self.env.prepared), 1)

    def test_a_task_runs_as_a_turn_in_the_apps_conversation_and_reports_its_state(self):
        release = threading.Event()
        self.env.provider = TaskProvider(release)
        seen = []

        def person(controller, call):
            seen.append(call.name)
            controller.review_script(call)
            controller.execute_tool(call, self.executor, controller.document)
        self.approve = None
        started = self.call("angelo_task", {"task": "Make a cube", "wait_seconds": 0})
        self.assertFalse(started["isError"], started)
        state = started["structuredContent"]
        self.assertIn(state["state"], {"running", "waiting_for_approval_in_angelo"})
        self.assertEqual(state["connection"]["billing"], "subscription")
        controller = self.conversation()
        self.assertTrue(controller.busy)
        rows = controller.state["messages"]
        self.assertTrue(any(row.get("request_kind") == "external_task" and row.get("text") == "Make a cube" for row in rows))
        busy = self.call("angelo_inspect_scene")
        self.assertTrue(busy["isError"], "tool calls wait while the app's task runs")
        for _ in range(20):
            self.tick()
        waiting = self.call("angelo_task_status", {"task_id": state["task_id"], "wait_seconds": 0})
        self.assertEqual(waiting["structuredContent"]["state"], "waiting_for_approval_in_angelo")
        self.approve = person
        release.set()
        done = self.call("angelo_task_status", {"task_id": state["task_id"], "wait_seconds": 10})
        self.assertEqual(done["structuredContent"]["state"], "done", done)
        self.assertEqual(done["structuredContent"]["reply"], "Made the cube.")
        self.assertEqual(seen, ["angelo_execute_python"], "a task's script still gets the person's review")

    def test_the_status_bar_names_an_app_waiting_in_another_conversation(self):
        from angelo_agent.external_apps import waiting
        seen = []

        def person(controller, call):
            seen.append(waiting(list(self.env._controllers.values()), active_id="another-chat"))
            seen.append(waiting(list(self.env._controllers.values()), active_id=controller.state["id"]))
            controller.decline_tool(call.id)
        self.approve = person
        self.call("angelo_execute_python", SCRIPT)
        self.assertEqual(seen[0], [(self.name, self.conversation().state["id"])])
        self.assertEqual(seen[1], [], "not while the person is looking at that conversation")
        self.assertEqual(waiting(list(self.env._controllers.values()), active_id=None), [])

    def test_stopping_a_task_from_the_app_is_angelos_stop(self):
        release = threading.Event()
        self.env.provider = TaskProvider(release)
        started = self.call("angelo_task", {"task": "Make a cube", "wait_seconds": 0})["structuredContent"]
        stopped = self.call("angelo_task_stop", {"task_id": started["task_id"]})
        self.assertFalse(stopped["isError"], stopped)
        for _ in range(20):
            self.tick()
            time.sleep(0.01)
        final = self.call("angelo_task_status", {"task_id": started["task_id"]})["structuredContent"]
        self.assertEqual(final["state"], "stopped")
        self.assertNotIn("angelo_execute_python", self.executor.calls)

    def test_the_request_closes_when_the_app_goes_quiet_and_reports_its_edits(self):
        from angelo_agent import external_work
        self.call("angelo_create_cube", CUBE)
        controller = self.conversation()
        self.assertTrue(controller.busy, "the request stays open across an app's calls")
        original = external_work.IDLE_SECONDS
        external_work.IDLE_SECONDS = 0.0
        self.addCleanup(setattr, external_work, "IDLE_SECONDS", original)
        for _ in range(5):
            self.tick()
        self.assertFalse(controller.busy)
        self.assertEqual(self.host._work, {})

    def test_stop_in_angelo_ends_the_apps_request(self):
        self.call("angelo_inspect_scene")
        controller = self.conversation()
        controller.stop()
        result = self.call("angelo_inspect_scene")
        self.assertFalse(result["isError"], "the next call opens a fresh request")
        self.assertEqual(self.executor.calls.count("angelo_inspect_scene"), 2)

    def test_a_new_file_moves_the_app_to_a_fresh_conversation(self):
        self.call("angelo_inspect_scene")
        old = self.conversation()
        self.tick()
        from angelo_agent import external_work
        original = external_work.IDLE_SECONDS
        external_work.IDLE_SECONDS = 0.0
        self.addCleanup(setattr, external_work, "IDLE_SECONDS", original)
        self.env.document = ("doc-b", 1)
        old.set_document(self.env.document)
        for _ in range(3):
            self.tick()
        result = self.call("angelo_inspect_scene")
        self.assertFalse(result["isError"], result)
        self.assertNotEqual(self.conversation().state["id"], old.state["id"])

    def test_no_network_listener_and_the_launcher_writes_only_json_rpc(self):
        server, out = self.server()
        server.serve(io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\nnot json\n'
                                 '{"jsonrpc":"2.0","method":"notifications/initialized"}\n'))
        lines = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(lines[0], {"jsonrpc": "2.0", "id": 1, "result": {}})
        self.assertEqual(lines[1]["error"]["code"], -32700)
        self.assertEqual(len(lines), 2)


class Harness(unittest.TestCase):
    """Adversarial regressions of the MCP server, on the AppTests harness."""
    setUp = AppTests.setUp
    tick = AppTests.tick
    server = AppTests.server
    rpc = AppTests.rpc
    call = AppTests.call
    conversation = AppTests.conversation

    def raw(self, request, label=None, key="default", seconds=20.0):
        """One request over the real socket in a thread while this thread pumps (the launcher's own path)."""
        label = label or self.name
        key = AppRegistry(self.state).key(label) if key == "default" else key
        self.host.pump()
        out = []
        thread = threading.Thread(target=lambda: out.append(cli_client.run(request, self.state, label, seconds, key=key)))
        thread.start()
        deadline = time.monotonic() + seconds + 5
        while thread.is_alive() and time.monotonic() < deadline:
            self.tick()
            time.sleep(0.005)
        thread.join(1)
        self.assertTrue(out, "no answer")
        return out[0]

    def app_call(self, call_id, tool, arguments, wait=5.0, **kw):
        return self.raw({"command": "call", "args": {"tool": tool, "arguments": arguments, "id": call_id,
                                                     "presentation": "mcp", "wait": wait}}, **kw)


@unittest.skipIf(sys.platform == "win32", "Unix-socket listener")
class RedeliveryTests(Harness):
    def test_a_call_delivered_again_after_stop_never_runs_twice(self):
        """Property 4: a call delivered again under its ID never runs twice."""
        first = self.app_call("mcp-same", "angelo_create_cube", CUBE)
        self.assertEqual(first["code"], "ok", first)
        self.conversation().stop()          # The person's Stop ends the request.
        for _ in range(5):
            self.tick()
        again = self.app_call("mcp-same", "angelo_create_cube", CUBE)   # The launcher's next round, same ID.
        self.assertEqual(self.executor.calls.count("angelo_create_cube"), 1,
                         "re-delivery after the request closed ran the edit again: %s" % again.get("code"))

    def test_a_call_queued_before_stop_never_runs_after_it(self):
        """Property 4: a call that waited can't run after Stop. A script waits for the person; a cube call queued behind
        it is delivered again (launcher round) after Stop closed the request: it runs, unapproved, after Stop."""
        self.host.pump()
        results = {}
        threads = [threading.Thread(target=lambda: results.__setitem__("s", cli_client.run(
            {"command": "call", "args": {"tool": "angelo_execute_python", "arguments": SCRIPT, "id": "mcp-s",
                                         "presentation": "mcp", "wait": 1.0}}, self.state, self.name, 5,
            key=AppRegistry(self.state).key(self.name))))]
        threads[0].start()
        for _ in range(40):
            self.tick()
            time.sleep(0.005)
        threads.append(threading.Thread(target=lambda: results.__setitem__("c", cli_client.run(
            {"command": "call", "args": {"tool": "angelo_create_cube", "arguments": CUBE, "id": "mcp-c",
                                         "presentation": "mcp", "wait": 0.5}}, self.state, self.name, 5,
            key=AppRegistry(self.state).key(self.name)))))
        threads[1].start()
        deadline = time.monotonic() + 3
        while any(t.is_alive() for t in threads) and time.monotonic() < deadline:
            self.tick()
            time.sleep(0.005)
        self.assertEqual(results["c"]["code"], "pending", results)   # Queued behind the script card.
        self.conversation().stop()       # The person presses Stop instead of approving.
        for _ in range(20):
            self.tick()
            time.sleep(0.005)
        self.assertNotIn("angelo_create_cube", self.executor.calls)
        self.app_call("mcp-c", "angelo_create_cube", CUBE)   # The launcher's next round for the queued call.
        self.assertNotIn("angelo_create_cube", self.executor.calls,
                         "a call queued before Stop ran after it, without the person")


@unittest.skipIf(sys.platform == "win32", "Unix-socket listener")
class LabelRaceTests(Harness):
    def test_levels_come_from_the_hello_that_proved_the_key(self):
        """Property 2: a connected app's name can't be used without its key."""
        AppRegistry(self.state).disconnect(self.name)
        self.host.pump()
        sock, failure = cli_client.connect(self.state, "cursor", None)   # Phase-1 CLI handshake, no app key.
        self.assertIsNone(failure, failure)
        AppRegistry(self.state).connect("cursor")       # The person connects Cursor meanwhile (key never used).
        self.host.pump()
        out = []

        def finish():
            protocol.send_frame(sock, {"type": "request", "command": "call", "args": {
                "tool": "angelo_create_cube", "arguments": CUBE, "id": "race", "presentation": "mcp", "wait": 5}},
                protocol.MAX_REQUEST_BYTES)
            out.append(protocol.recv_frame(sock, protocol.MAX_RESPONSE_BYTES, 10)["envelope"])
            sock.close()
        thread = threading.Thread(target=finish)
        thread.start()
        while thread.is_alive():
            self.tick()
            time.sleep(0.005)
        self.assertNotEqual(out[0]["code"], "ok", "an unkeyed phase-1 connection ran an edit as the app")
        self.assertNotIn("angelo_create_cube", self.executor.calls)


@unittest.skipIf(sys.platform == "win32", "Unix-socket listener")
class AccessOffTests(Harness):
    def test_access_off_mid_call_never_strands_the_conversation(self):
        """Property 8: close() vs in-flight calls must not strand the conversation."""
        self.host.pump()
        out = []
        thread = threading.Thread(target=lambda: out.append(cli_client.run(
            {"command": "call", "args": {"tool": "angelo_execute_python", "arguments": SCRIPT, "id": "mcp-w",
                                         "presentation": "mcp", "wait": 2.0}}, self.state, self.name, 5,
            key=AppRegistry(self.state).key(self.name))))
        thread.start()
        while thread.is_alive():
            self.tick()
            time.sleep(0.005)
        controller = self.conversation()
        self.assertTrue(controller.pending, "the script waits at its card")
        self.env.requested = False          # The person turns off "Allow apps on this computer to reach Angelo".
        for _ in range(30):
            self.tick()
            time.sleep(0.01)
        self.env.requested = True
        for _ in range(30):
            self.tick()
            time.sleep(0.01)
        self.assertFalse(controller.busy, "conversation stuck: status=%r pending=%r work=%r" % (controller.status, list(controller.pending), self.host._work))
        result = self.call("angelo_inspect_scene")
        self.assertFalse(result["isError"], result)


@unittest.skipIf(sys.platform == "win32", "Unix-socket listener")
class TaskReachTests(Harness):
    def test_a_task_never_reaches_the_families_apps_cannot_use(self):
        """Property 3: no path reaches file read/list or the team library. A task is Angelo's agent with the full
        toolset; read-only tools run without a card and their output returns to the app in the task reply."""
        ran = []

        class Provider:
            def connect(self):
                return "native"

            def run_request(self, context, native_id, text, observation=None):
                for index, (tool, args) in enumerate((("angelo_list_files", {"path": "."}),
                                                      ("angelo_team_library_search", {}),
                                                      ("angelo_studio_list", {}))):
                    done = threading.Event()
                    context.request_tool("t%d" % index, tool, args, lambda r, t=tool: (ran.append((t, r)), done.set()))
                    done.wait(5)
                context.emit("message_complete", {"item": "reply", "text": "listed"})
                return "completed"

            def stop_request(self, fence):
                pass

            def close(self):
                pass
        self.env.provider = Provider()
        self.approve = None            # Nobody approves anything.
        started = self.call("angelo_task", {"task": "List the files next to the .blend", "wait_seconds": 5})
        for _ in range(40):
            self.tick()
            time.sleep(0.005)
        never = {"angelo_list_files", "angelo_team_library_search", "angelo_studio_list"}
        self.assertFalse(never & set(self.executor.calls),
                         "a task ran %s with no approval: %s" % (sorted(never & set(self.executor.calls)),
                                                                json.dumps(started)[:200]))


class NoListenerSourceTests(unittest.TestCase):
    def test_the_launcher_and_its_client_open_no_network_socket(self):
        root = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
        for name in ("external_mcp.py", "external_mcp_tools.py", "external_clients.py", "cli_client.py"):
            text = (root / name).read_text(encoding="utf-8")
            self.assertNotIn("AF_INET", text, name)
            self.assertNotIn(".bind(", text, name)
            self.assertNotIn("http.server", text, name)


if __name__ == "__main__":
    unittest.main()
