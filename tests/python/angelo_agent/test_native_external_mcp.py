# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's MCP server for external AI apps: the registry, keys and the launcher, end to end without Blender.

A real listener and the real ``external_mcp.Server`` (the stdio launcher's code) against the real ``ExternalHost``.
An app talks to Angelo's agent and never calls a tool; what it does there is ``test_native_external_agent``.
Contract: doc/angelo/external_mcp.rst.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).
from host_waits import CAP_S  # noqa: E402  A completion wait's cap: only a hang reaches it.

import base64
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import time
import types
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import angelo_guard_twin  # noqa: E402
from angelo_agent import chat_pending, cli_client, external_agent, external_mcp, managed_policy
from angelo_agent import external_transcript as transcript
from angelo_agent.controller import ToolDispatchBudget
from angelo_agent.controller_external import external_send
from angelo_agent.external_clients import AppRegistry
from angelo_agent.external_host import ClientRegistry, ExternalHost
from angelo_agent.external_mcp import Server
from angelo_agent.external_mcp_tools import AGENT_NAMES, app_tools
from angelo_agent.managed_policy import Policy
from angelo_agent.steering import STEER
from test_native_external_cli_host import Environment, Executor
from host_test_policy import environment_skip

SCRIPT = {"description": "Set a value", "code": "result = 1"}
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")
# On Windows connecting an app is refused (external_ui) until its named pipe, a later phase.
LISTENER = environment_skip("Unix-socket listener; Windows refuses to connect an app until its named pipe")
APP_KEYS = environment_skip("App keys are owner-only POSIX files; Windows refuses to connect an app until its "
                            "named pipe")


class TurnProvider:
    """A provider turn without a model: it asks for one script (the person decides), then waits for the test before
    replying. ``steer_ready`` says whether the turn takes a message midway (Codex's ``steer_ready``)."""

    def __init__(self, release, steer_ready=False, script=True):
        self.release, self.texts, self.steer_ready, self.script = release, [], steer_ready, script

    def connect(self):
        return "native-turn"

    def run_request(self, context, native_id, text, observation=None):
        self.texts.append(text)
        if self.script:
            done = threading.Event()
            context.request_tool("turn-script", "angelo_execute_python", SCRIPT, lambda result: done.set())
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
    """The host's view of ``ui`` without Blender: ``deliver`` is ``UiEnvironment.deliver`` with the person's send path
    (``console.submit``) reduced to binding the conversation's connection and ``Controller.send``."""

    def __init__(self, state):
        super().__init__(state)
        self.prepared, self.provider, self.stops, self.pictures = [], None, [], {}

    def prepare(self, controller):
        self.prepared.append(controller.state["id"])
        if self.provider is None:
            controller.setup_required = True
            raise RuntimeError("This chat has no connection. Connect Claude or ChatGPT in the chat.")
        controller.provider = self.provider

    def submit(self, controller, text, references=None, pending=(), **_options):
        self.prepare(controller)
        controller.send(text, references=references or [], pending=pending)

    def deliver(self, controller, text):
        if controller.turn_running():
            item = chat_pending.hold(controller, text, [])
            return "steered" if item.mode == STEER else "queued"
        with external_send(controller):
            self.submit(controller, text)
        return "started"

    def stop(self, controller):
        self.stops.append(controller.state["id"])
        controller.stop()

    def scene(self, controller):
        return {"objects": [{"name": "Cube", "type": "MESH"}], "mode": "OBJECT"}

    def viewport(self, controller):
        return self.picture(controller)

    def picture(self, controller):
        directory = controller.references.directory
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        identity = str(uuid.uuid4())
        (directory / (identity + ".png")).write_bytes(PNG)
        return identity


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="smcp-reg-"))   # No socket here: the platform's temporary folder.
        self.addCleanup(shutil.rmtree, self.root, True)
        self.registry = AppRegistry(self.root / "state")

    @unittest.skipIf(sys.platform == "win32", APP_KEYS)
    def test_connect_writes_an_owner_only_key_that_never_leaves_angelo(self):
        name = self.registry.connect("claude_code")
        self.assertEqual(name, "claude-code")
        key_file = self.registry.keys / name
        self.assertEqual(stat.S_IMODE(os.lstat(key_file).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.lstat(self.registry.keys).st_mode), 0o700)
        self.assertEqual(len(self.registry.key(name)), 32)
        record = json.loads(self.registry.path.read_text())
        self.assertNotIn(key_file.read_text().strip(), json.dumps(record), "the registry never holds a key")
        self.assertEqual(set(self.registry.load()[name]), {"app", "connected_at"})

    @unittest.skipIf(sys.platform == "win32", APP_KEYS)
    def test_a_record_2_0_0_wrote_reads_the_same(self):
        name = self.registry.connect("codex")
        raw = json.loads(self.registry.path.read_text())
        raw["apps"][name]["levels"] = ["read", "edit", "python", "tasks"]   # 2.0.0's per-app levels.
        self.registry.path.write_text(json.dumps(raw))
        self.assertEqual(self.registry.load()[name]["app"], "codex")
        self.assertIn(name, self.registry.snapshot())

    @unittest.skipIf(sys.platform == "win32", APP_KEYS)
    def test_reconnect_rotates_the_key_and_disconnect_deletes_it(self):
        name = self.registry.connect("codex")
        first = self.registry.key(name)
        self.registry.connect("codex")
        self.assertNotEqual(self.registry.key(name), first)
        self.assertTrue(self.registry.disconnect(name))
        self.assertIsNone(self.registry.key(name))
        self.assertEqual(self.registry.snapshot(), {})
        self.assertFalse(self.registry.disconnect(name))
        name = self.registry.connect("cursor")
        os.chmod(self.registry.keys / name, 0o644)
        self.assertIsNone(self.registry.key(name), "a key others can read is not a key")
        self.assertEqual(self.registry.snapshot(), {})


@unittest.skipIf(sys.platform == "win32", LISTENER)
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
        self.release = threading.Event()
        self.addCleanup(self.release.set)
        # ``console.submit`` is the person's send path (bpy in the app): reduced to the environment's, on both the
        # module table and the package attribute (the package imports its modules lazily).
        import angelo_agent
        console = types.SimpleNamespace(submit=self.env.submit)
        for patcher in (mock.patch.dict(sys.modules, {"angelo_agent.console": console}),
                        mock.patch.object(angelo_agent, "console", console, create=True)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tick(self):
        self.host.pump()
        for controller in list(self.env._controllers.values()):
            controller.pump_events()
            controller.pump_tools(self.executor, self.env.document, budget=ToolDispatchBudget(seconds=60),
                                  apply_mutation=lambda call, c=controller: c.execute_tool(call, self.executor,
                                                                                            c.document))
            chat_pending.dispatch_due(controller)   # ui.pump's when_idle: queued messages go once the turn ends.
            for call in list(controller.pending.values()):
                if call.approval and not call.executing and not call.automatic and self.approve is not None:
                    self.approve(controller, call)

    def server(self, name=None):
        out = io.StringIO()
        return Server(name or self.name, self.state, stdout=out), out

    def rpc(self, method, params=None, name=None, timeout=30):
        self.host.pump()
        server, out = self.server(name)
        server.dispatch({"jsonrpc": "2.0", "id": 7, "method": method, "params": params or {}})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            lines = [json.loads(line) for line in out.getvalue().splitlines() if line]
            answers = [line for line in lines if line.get("id") == 7]
            if answers:
                self.tick()
                self.progress = [line for line in lines if line.get("method") == "notifications/progress"]
                return answers[0]
            self.tick()
            time.sleep(0.005)
        self.fail("no MCP answer for " + method)

    def call(self, tool, arguments=None, name=None):
        return self.rpc("tools/call", {"name": tool, "arguments": arguments or {}}, name=name)["result"]

    def conversation(self):
        return self.env._controllers[ClientRegistry(self.state).load()[self.name]]

    def until(self, predicate):
        deadline = time.monotonic() + CAP_S
        while not predicate() and time.monotonic() < deadline:
            self.tick()
            time.sleep(0.005)
        self.assertTrue(predicate())

    def person_approves(self, controller, call):
        angelo_guard_twin.harness(controller.review_script, call)   # The person's Approve.
        controller.execute_tool(call, self.executor, controller.document)

    def test_initialize_and_the_tool_list(self):
        init = self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})["result"]
        self.assertEqual(init["serverInfo"]["name"], "angelo")
        self.assertNotIn("Blender MCP", init["instructions"], "Angelo never mentions another MCP server")
        names = {tool["name"] for tool in self.rpc("tools/list")["result"]["tools"]}
        self.assertEqual(names, AGENT_NAMES)
        self.assertEqual(self.rpc("tools/list", name="unknown")["result"]["tools"], [])

    def test_an_app_can_never_call_a_tool(self):
        result = self.call("angelo_execute_python", SCRIPT)
        self.assertTrue(result["isError"])
        self.host.pump()
        key = AppRegistry(self.state).key(self.name)
        for request in ({"command": "call", "args": {"tool": "angelo_inspect_scene", "arguments": {}, "id": "x"}},
                        {"command": "call", "args": {"tool": "angelo_create_cube", "arguments": {}, "id": "y",
                                                     "presentation": "mcp"}},
                        {"command": "app_call", "args": {}}, {"command": "task", "args": {"action": "start"}}):
            value = cli_client.run(request, self.state, self.name, 10, key=key)
            self.assertIn(value["code"], {"refused", "invalid"}, request)
        self.assertEqual(self.executor.calls, [])

    def test_a_cli_label_cannot_reach_the_agent(self):
        self.host.pump()
        value = cli_client.run({"command": "agent", "args": {"action": "send", "message": "hi"}}, self.state,
                               "plain-cli", 10)
        self.assertEqual(value["code"], "refused")

    def test_a_disconnected_or_forged_app_is_refused(self):
        registry = AppRegistry(self.state)
        key = registry.key(self.name)
        registry.disconnect(self.name)
        result = self.call("angelo_read")
        self.assertTrue(result["isError"])
        self.assertIn("not connected", result["content"][0]["text"])
        self.host.pump()
        value = cli_client.run({"command": "status", "args": {}}, self.state, self.name, 10, key=key)
        self.assertEqual(value["code"], "unavailable")
        registry.connect("claude_code")
        self.host.pump()
        value = cli_client.run({"command": "agent", "args": {"action": "read"}}, self.state, self.name, 10)
        self.assertEqual(value["code"], "unavailable", "a connected app's name needs that app's key")

    def test_angelo_not_open_says_what_to_do(self):
        self.env.requested = False   # Access off: the next pump keeps the listener closed.
        self.host.stop()
        result = self.call("angelo_read")
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["code"], "angelo_not_open")
        self.assertIn("open Angelo", result["content"][0]["text"])

    def test_a_call_with_no_thread_to_run_it_is_answered_and_the_launcher_keeps_serving(self):
        server, out = self.server()
        with mock.patch.object(threading.Thread, "start", side_effect=RuntimeError("can't start new thread")):
            server.serve(io.StringIO('{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"x"}}\n'
                                     '{"jsonrpc":"2.0","id":2,"method":"ping"}\n'))
        lines = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(lines[0]["id"], 1)
        self.assertTrue(lines[0]["result"]["isError"])
        self.assertEqual(lines[1], {"jsonrpc": "2.0", "id": 2, "result": {}})

    def test_no_network_listener_and_the_launcher_writes_only_json_rpc(self):
        server, out = self.server()
        server.serve(io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\nnot json\n'
                                 '{"jsonrpc":"2.0","method":"notifications/initialized"}\n'))
        lines = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(lines[0], {"jsonrpc": "2.0", "id": 1, "result": {}})
        self.assertEqual(lines[1]["error"]["code"], -32700)
        self.assertEqual(len(lines), 2)
        source = Path(external_mcp.__file__).read_text(encoding="utf-8")
        for listener in ("socket.socket", "bind(", "listen(", "http.server", "socketserver"):
            self.assertNotIn(listener, source)


if __name__ == "__main__":
    unittest.main()
