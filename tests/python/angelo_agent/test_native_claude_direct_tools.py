# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Claude's opt-in direct-tools route (claude_direct.py): the switch, the launch, the tool channel and each call.

Unbilled stdio fixtures only: the ``mcp_message`` control requests are the shapes Claude Code 2.1.301 sends for a
host-served (SDK) MCP server, recorded against a fake Messages endpoint (doc/angelo/agent_integration.rst, "Direct
tools"). They prove what the host answers, not what a live model does with it.
"""

import json
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from claude_protocol_fixtures import ClaudeAdapterFixture, NativeInitializeFixture  # noqa: E402
from angelo_agent import claude_direct, claude_direct_text, claude_plan_guidance, claude_text  # noqa: E402
from angelo_agent.claude_stream import RoundStream  # noqa: E402
from angelo_agent.claude_transport import ClaudeRequest  # noqa: E402
from angelo_agent.plan_kinds import DEFERRED_TOOLS  # noqa: E402
from angelo_agent.providers import ClaudeProvider, claude_session  # noqa: E402
from angelo_agent.tool_specs import TOOLS  # noqa: E402


def context(direct=None):
    fields = {"turn_options": None, "state": {"provider": "claude_subscription", "options": {}}}
    if direct is not None:
        fields["claude_direct_tools"] = direct
    return SimpleNamespace(**fields)


def mcp(identity, method, params=None, server="angelo"):
    message = {"jsonrpc": "2.0", "method": method}
    if identity is not None:
        message["id"] = identity
    if params is not None:
        message["params"] = params
    return {"type": "control_request", "request_id": "cc-%s" % identity,
            "request": {"subtype": "mcp_message", "server_name": server, "message": message}}


def finished(reply=""):
    return {"type": "result", "subtype": "success", "is_error": False, "session_id": "fixture-native", "result": reply}


class DirectFixture(NativeInitializeFixture):
    """A Claude Code stand-in that makes ``calls`` (name, arguments) one at a time over the tool channel."""

    def __init__(self, calls=(), reply=""):
        super().__init__(on_user=lambda process, _event: process.next())
        self.calls, self.reply, self.answers, self.initialize = list(calls), reply, [], None
        self.answered = threading.Event()

    def receive(self, event):
        if event["type"] == "control_request" and event["request"]["subtype"] == "initialize":
            self.initialize = event["request"]
            event = {"type": "control_request", "request_id": event["request_id"],
                     "request": dict(event["request"], jsonSchema=None)}
        if event["type"] == "control_response" and "mcp_response" in (event["response"].get("response") or {}):
            self.answers.append(event["response"]["response"]["mcp_response"])
            self.answered.set()
            return self.next()
        return super().receive(event)

    def next(self):
        if self.calls:
            name, arguments = self.calls.pop(0)
            self.stdout.emit(mcp(len(self.answers) + 1, "tools/call", {"name": name, "arguments": arguments}))
        else:
            self.stdout.emit(finished(self.reply))


def answering(results):
    """A ``request_tool`` that answers each call at once with the next of ``results``."""
    def request_tool(call_id, name, arguments, receive):
        receive(results.pop(0))
    return Mock(side_effect=request_tool)


class SwitchTests(unittest.TestCase):
    def test_1_turns_it_on_0_off_and_anything_else_keeps_the_default(self):
        from unittest import mock
        for default in (False, True):
            with mock.patch.object(claude_direct, "DEFAULT", default):
                self.assertIs(claude_direct.requested({}), default)
                self.assertIs(claude_direct.requested({"ANGELO_CLAUDE_DIRECT_TOOLS": "yes"}), default)
                self.assertIs(claude_direct.requested({"ANGELO_CLAUDE_DIRECT_TOOLS": "0"}), False)
                self.assertIs(claude_direct.requested({"ANGELO_CLAUDE_DIRECT_TOOLS": "1"}), True)
        self.assertIs(claude_direct.DEFAULT, False, "off until the A/B decides")
        for value in (None, False, 1, "1"):   # A context turns it on only with a real True.
            self.assertFalse(claude_direct.enabled(context(value)))
        self.assertTrue(claude_direct.enabled(context(True)))

    def test_off_the_launch_and_initialization_are_the_shipped_routes(self):
        plain = claude_session("claude", context())
        self.assertEqual(claude_session("claude", context(False)), plain)
        self.assertNotIn("mcp__angelo", " ".join(plain[0]))
        self.assertIn("jsonSchema", plain[1]["request"])
        self.assertTrue("".join(plain[1]["request"]["systemPrompt"]).endswith(claude_plan_guidance.CLAUDE_PLAN_INSTRUCTIONS))

    def test_on_only_the_allowed_tools_and_the_initialization_change(self):
        (plain, plain_init), (command, initialize) = claude_session("claude", context()), claude_session("claude", context(True))
        index = plain.index("--allowedTools") + 1
        self.assertEqual(command[:index] + command[index + 1:], plain[:index] + plain[index + 1:])
        self.assertEqual(command[index], plain[index] + ",mcp__angelo")
        self.assertEqual(json.loads(command[command.index("--mcp-config") + 1]), {"mcpServers": {}},
                         "no server process: the tools come over the session's own stdin")
        request = initialize["request"]
        self.assertNotIn("jsonSchema", request)
        self.assertEqual(request["sdkMcpServers"], ["angelo"])
        self.assertEqual(request["sdkMcpServerConfigs"], {"angelo": {"disableAutoBackground": True}})
        base = "".join(plain_init["request"]["systemPrompt"])[:-len(claude_plan_guidance.CLAUDE_PLAN_INSTRUCTIONS)]
        self.assertTrue(base.startswith(claude_text.CLAUDE_BASE_INSTRUCTIONS), "the same RC10 base text")
        self.assertEqual(request["systemPrompt"], [base + claude_direct_text.CLAUDE_TOOLS_INSTRUCTIONS])

    def test_a_direct_session_fixes_the_settings_that_would_change_the_route(self):
        from angelo_agent import runtime_environment
        inherited = {"MAX_MCP_OUTPUT_TOKENS": "100", "MCP_TOOL_TIMEOUT": "5000", "CLAUDE_CODE_MCP_AUTO_BACKGROUND_MS": "9"}
        plain = runtime_environment.session_environment(dict(inherited), context())
        self.assertEqual({name: plain[name] for name in inherited}, inherited, "the plan route is unchanged")
        direct = runtime_environment.session_environment(dict(inherited), context(True))
        self.assertEqual({name: direct[name] for name in inherited}, claude_direct.SESSION_ENVIRONMENT)

    def test_a_change_relaunches_the_session(self):
        binding = lambda direct: ClaudeProvider._runtime_binding(None, SimpleNamespace(
            turn_options={"model": "", "effort": "low", "fast": False}, claude_direct_tools=direct))
        self.assertNotEqual(binding(False), binding(True))

    def test_the_controller_hands_the_apps_switch_to_each_request_and_never_saves_it(self):
        import tempfile
        from unittest import mock
        from angelo_agent.controller import Controller
        with tempfile.TemporaryDirectory() as folder:
            controller = Controller(folder)
            self.addCleanup(controller.close)
            with mock.patch.dict("os.environ", {"ANGELO_CLAUDE_DIRECT_TOOLS": ""}):
                self.assertFalse(claude_direct.enabled(controller.runtime_context()))
            with mock.patch.dict("os.environ", {"ANGELO_CLAUDE_DIRECT_TOOLS": "1"}):
                self.assertTrue(claude_direct.enabled(controller.runtime_context()))
                controller.save()
            self.assertNotIn(b"claude_direct", controller.store.path.read_bytes())


class TextTests(unittest.TestCase):
    def test_no_one_plan_format_is_left(self):
        text = claude_direct_text.CLAUDE_TOOLS_INSTRUCTIONS
        for plan_only in ("StructuredOutput", "verify=true", '"conf"', "operations []", "reply holds",
                          "All steps are validated", "no model round", "plan"):
            self.assertNotIn(plan_only, text)

    def test_the_plan_texts_other_lines_are_kept(self):
        text = " ".join(claude_direct_text.CLAUDE_TOOLS_INSTRUCTIONS.split())
        for line in ("Targets are o<n> handles or $aliases, never names.", claude_plan_guidance.SCENE_ENTRY,
                     "Sculpt with native angelo_*sculpt* tools, never Python vertex loops or UI gestures.",
                     "Views refresh/attach after every edit; ask again only for a view the result lists in stale_views.",
                     "A request keeps at most twelve", "Edit new objects in their creating program via $aliases.",
                     "declare only the expected_checks its operations do not imply"):
            self.assertIn(" ".join(line.split()), text)


class ToolListTests(unittest.TestCase):
    def test_every_allowed_tool_in_a_fixed_order_and_byte_stable(self):
        tools = claude_direct.tool_list()
        self.assertEqual([tool["name"] for tool in tools], [tool["name"] for tool in TOOLS])
        self.assertEqual(json.dumps(tools), json.dumps(claude_direct.tool_list()))
        allowed = {"angelo_scene_search", "angelo_execute_python", "angelo_scene_program"}
        self.assertEqual([tool["name"] for tool in claude_direct.tool_list(allowed)],
                         [tool["name"] for tool in TOOLS if tool["name"] in allowed])

    def test_deferred_tools_are_named_as_the_plan_names_them(self):
        for tool in claude_direct.tool_list():
            if tool["name"] in DEFERRED_TOOLS:
                self.assertEqual(tool["inputSchema"], {"type": "object"})
                self.assertIn("angelo_agent_capabilities", tool["description"])
            else:
                self.assertNotIn("description", tool, "core tools show their schema only, as the plan does")

    def test_core_schemas_are_the_plans(self):
        tools = {tool["name"]: tool["inputSchema"] for tool in claude_direct.tool_list()}
        program = json.dumps(tools["angelo_scene_program"])
        self.assertIn('"if"', program)
        self.assertNotIn("maxLength", program)
        self.assertEqual(tools["angelo_execute_python"]["properties"]["code"]["description"], "Under 27000 characters")


class ChannelTests(unittest.TestCase):
    def answer(self, event, *, turn=None, direct=None, allowed=None):
        written = []
        controller = SimpleNamespace(allowed_tools=allowed)
        owner = SimpleNamespace(controller=controller, same_request=lambda other: other is owner)
        transport = SimpleNamespace(lock=threading.RLock(), turn=turn, owner=owner)
        provider = SimpleNamespace(_direct=direct, _write=lambda message, *_a, **_k: written.append(message),
                                   _current=lambda *_a: True)
        claude_direct.answer(provider, transport, event)
        return [message["response"]["response"]["mcp_response"] for message in written]

    def test_the_handshake(self):
        [reply] = self.answer(mcp(0, "initialize", {"protocolVersion": "2025-06-18"}))
        self.assertEqual(reply["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(reply["result"]["capabilities"], {"tools": {}})
        [reply] = self.answer(mcp(None, "notifications/initialized"))
        self.assertEqual(reply["result"], {})
        [reply] = self.answer(mcp(1, "tools/list"), allowed={"angelo_scene_search"})
        self.assertEqual([tool["name"] for tool in reply["result"]["tools"]], ["angelo_scene_search"])

    def test_anything_else_is_refused(self):
        [reply] = self.answer(mcp(2, "resources/list"))
        self.assertEqual(reply["error"]["code"], -32601)
        [reply] = self.answer(mcp(3, "tools/list", server="other"))
        self.assertEqual(reply["error"]["code"], -32601)
        [reply] = self.answer(mcp(4, "tools/call", {"name": "angelo_scene_search", "arguments": {}}))
        self.assertTrue(reply["result"]["isError"], "no request owns a call made outside a turn")
        self.assertIn("did not run", reply["result"]["content"][0]["text"])


class DirectRunTests(ClaudeAdapterFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.host.claude_direct_tools = True

    def run_calls(self, calls, results, reply=""):
        self.process = DirectFixture(calls, reply)
        self.popen_mock.return_value = self.process
        self.host.request_tool = answering(list(results))
        return self.provider.run_request(self.host, None, "Make a bridge", observation={})

    def answers(self):
        return [(answer["result"]["isError"], [item for item in answer["result"]["content"]])
                for answer in self.process.answers]

    def test_a_lookup_runs_and_its_result_returns_to_the_model(self):
        rows = {"success": True, "objects": [], "total": 0}
        status = self.run_calls([("angelo_scene_search", {"query": "Bridge"})], [rows], reply="No bridge yet.")
        self.assertEqual(status, "completed")
        self.host.request_tool.assert_called_once()
        name, arguments = self.host.request_tool.call_args[0][1:3]
        self.assertEqual((name, arguments["query"]), ("angelo_scene_search", "Bridge"))
        [(error, content)] = self.answers()
        self.assertFalse(error)
        self.assertEqual(json.loads(content[-1]["text"]), rows)
        self.assertIn(("message_complete", {"item": "claude-assistant", "text": "No bridge yet."}), self.host.events)
        self.assertIsNone(self.process.initialize.get("jsonSchema"))

    def test_a_rejected_call_runs_nothing_and_says_why(self):
        status = self.run_calls([("angelo_create_cube", {"name": "A", "size": -1, "location": [0, 0, 0]})], [])
        self.assertEqual(status, "completed")
        self.host.request_tool.assert_not_called()
        [(error, content)] = self.answers()
        value = json.loads(content[-1]["text"])
        self.assertTrue(error)
        self.assertEqual((value["error_code"], value["scene_changed"]), ("VALIDATION", False))
        self.assertTrue(value["error"].startswith("This call:"), value["error"])

    def test_a_decline_ends_the_request_and_refuses_later_calls(self):
        cube = ("angelo_create_cube", {"name": "A", "size": 1, "location": [0, 0, 0]})
        status = self.run_calls([cube, cube], [{"error": "The user declined this change."}])
        self.assertEqual(status, "interrupted")
        self.host.request_tool.assert_called_once()   # The second call never reached the host.
        (first_error, first), (second_error, second) = self.answers()
        self.assertTrue(first_error and second_error)
        self.assertIn("The request ended with this call", first[0]["text"])
        self.assertIn("make no further tool calls", first[0]["text"])
        self.assertNotIn("may be in the scene", first[0]["text"], "a decline states no change")
        self.assertIn("already ended", second[-1]["text"])

    def test_a_host_error_fails_the_request_as_a_plans_does(self):
        from angelo_agent.protocol import RpcError
        search = ("angelo_scene_search", {"query": "Bridge"})
        self.process = DirectFixture([search, search])
        self.popen_mock.return_value = self.process
        self.host.request_tool = Mock(side_effect=RpcError("Native Blender approval/dispatch timed out"))
        with self.assertRaisesRegex(RpcError, "dispatch timed out"):
            self.provider.run_request(self.host, None, "Make a bridge", observation={})
        self.host.request_tool.assert_called_once()   # The second call was refused, never dispatched.
        (first_error, first), (second_error, second) = self.answers()
        self.assertTrue(first_error and second_error)
        self.assertIn("the host stopped it", first[-1]["text"])
        self.assertIn("already ended", second[-1]["text"])

    def test_calls_rest_the_turns_clocks(self):
        state = claude_direct.DirectRequest(SimpleNamespace(_current=lambda owner: True), self.host,
                                            ClaudeRequest(self.host), Mock())
        self.assertFalse(state.busy())
        state.arrived()
        self.assertTrue(state.busy())
        state.left()
        self.assertFalse(state.busy())


class StreamTests(unittest.TestCase):
    def test_direct_calls_draw_no_runtime_row(self):
        stream, plans = RoundStream(), {}
        events = stream.events({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "mcp__angelo__angelo_scene_search", "input": {}},
            {"type": "tool_use", "id": "t2", "name": "WebSearch", "input": {"query": "bridges"}}]}}, plans)
        self.assertEqual([data["id"] for kind, data in events if kind == "activity"], ["claude-web/t2"])
        events = stream.events({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "{}"},
            {"type": "tool_result", "tool_use_id": "t2", "content": "results"}]}}, plans)
        self.assertEqual([data["id"] for kind, data in events if kind == "activity"], ["claude-web/t2"])


if __name__ == "__main__":
    unittest.main()
