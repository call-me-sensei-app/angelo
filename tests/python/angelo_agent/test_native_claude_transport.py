# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native Claude adapter protocol/Windows-budget tests; zero real provider runs."""

import copy
import subprocess
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from claude_protocol_fixtures import ClaudeAdapterFixture, NativeInitializeFixture, REQUIRES, SUPPORTED, TOO_OLD, completed, host
from angelo_agent.claude_transport import (ClaudeRequest, ClaudeTransport, COMMAND_BUDGET,
    check_command_budget, initialization_request, require_supported_runtime, windows_command_units)
from angelo_agent.protocol import RpcError
from angelo_agent.request_fences import RequestFence
from angelo_agent.runtime_environment import SESSION_CONTEXT
from angelo_agent.plan_schema import CLAUDE_CORE_ACTIONS, DEFERRED_TOOLS, SCRIPT_TARGETS, plan_arguments, without_lengths
from angelo_agent.providers import CLAUDE_INSTRUCTIONS, CLAUDE_PLAN_INSTRUCTIONS
from angelo_agent.tool_specs import TOOLS, native_plan_schema
from test_native_schema_compaction import expand_generated
from angelo_agent.tagged_unions import discriminated, members
from test_native_plan_schema import assert_standards_vectors

try:
    from jsonschema import Draft202012Validator
except ImportError:
    Draft202012Validator = None


class ClaudeTransportTests(ClaudeAdapterFixture, unittest.TestCase):

    def test_exact_full_schema_and_instructions_use_native_stdin_not_argv(self):
        self.host.instructions = "\nApplication boundary\n" + "🦊" * 100000
        expected = native_plan_schema()
        before = copy.deepcopy(expected)
        self.start("fixture-resume")
        command = self.popen_mock.call_args.args[0]
        self.assertNotIn("--json-schema", command)
        self.assertNotIn("--append-system-prompt", command)
        self.assertLess(windows_command_units(command), COMMAND_BUDGET)
        self.assertLess(windows_command_units(command), 1024)
        self.assertEqual(self.process.initialized_schema, expected)
        self.assertEqual(expected, before)
        self.assertEqual(self.process.additional_instructions,
                         CLAUDE_INSTRUCTIONS + self.host.instructions + CLAUDE_PLAN_INSTRUCTIONS)
        self.assertFalse(self.process.prompt_snapshot)
        self.assertIn("--resume=fixture-resume", command)
        self.assertEqual(command[command.index("--tools") + 1], "WebSearch,WebFetch")
        self.assertEqual(command[command.index("--allowedTools") + 1], "WebSearch,WebFetch")
        self.assertEqual(command[command.index("--permission-mode") + 1], "dontAsk")
        self.assertIn("--strict-mcp-config", command)
        self.assertEqual(command[command.index("--mcp-config") + 1], '{"mcpServers":{}}')
        # Per-machine prompt sections (working directory, date) leave the cached system prompt; every Claude Code
        # release in cli_runtime's supported range has the flag (2.1.258, 2.1.292, 2.1.293 and 2.1.301 checked).
        self.assertIn("--exclude-dynamic-system-prompt-sections", command)
        # A text-mode probe through child_process, whose text is UTF-8 on every platform (Windows would otherwise
        # decode with its ANSI code page; test_native_windows_cli pins that default).
        # The probe runs in the session's launch environment (runtime_environment.SESSION_CONTEXT included).
        self.run_mock.assert_called_once_with(["claude.exe", "--version"], env=dict(
            {"FIXTURE": "only"}, **SESSION_CONTEXT), capture_output=True, text=True, timeout=10)

    def test_wrapped_schema_projection_keeps_every_tool_exact_or_named(self):
        self.start(None)
        root = expand_generated(self.process.initialized_schema)
        _tag, variants, deferred = members(root["properties"]["operations"]["items"])
        core = [tool for tool in TOOLS if tool["name"] not in DEFERRED_TOOLS]
        self.assertEqual(list(variants), [tool["name"] for tool in core])
        self.assertEqual(deferred, [tool["name"] for tool in TOOLS if tool["name"] in DEFERRED_TOOLS])
        for variant, tool in zip(variants.values(), core):
            expected = discriminated(without_lengths(plan_arguments(tool, CLAUDE_CORE_ACTIONS)))
            if tool["name"] == "angelo_execute_python":  # Targets in place of the limits the host enforces.
                for field, target in SCRIPT_TARGETS.items():
                    expected["properties"][field]["description"] = target
            self.assertEqual(variant, {"properties": {"arguments": expected}})
        # Every other tool is named, in catalog order, with unconstrained arguments
        # that validate_call checks exactly before anything runs.
        items = root["properties"]["operations"]["items"]
        self.assertEqual({key: value for key, value in items.items() if key not in ("allOf", "properties")}, {
            "type": "object", "required": ["tool", "arguments"], "additionalProperties": False})
        self.assertEqual(items["properties"]["arguments"], {"type": "object"})
        self.assertEqual(root["properties"]["operations"]["maxItems"], 64)
        self.assertFalse(root["additionalProperties"])

    @unittest.skipIf(Draft202012Validator is None, "Optional standards validator not installed")
    def test_transport_schema_validates_plans_and_rejects_expansion_bypasses(self):
        self.start(None)
        schema = self.process.initialized_schema
        Draft202012Validator.check_schema(schema)
        validate = Draft202012Validator(schema).is_valid
        plan = {"reply": "Create and verify", "verify": False, "operations": [
            {"tool": "angelo_create_cube", "arguments": {"name": "Cube", "size": 1, "location": [0, 0, 0]}},
            {"tool": "angelo_scene_verify", "arguments": {"read_set": [], "checks": [
                {"kind": "object_count", "minimum": 1, "maximum": 4}]}}]}
        self.assertTrue(validate(plan))
        # Since toolset 20 the host infers a verify's read set from its latest observation
        # (exact revision checks unchanged), so leaving it out is valid.
        inferred = copy.deepcopy(plan); del inferred["operations"][1]["arguments"]["read_set"]
        self.assertTrue(validate(inferred))
        invalid = []
        extra = copy.deepcopy(plan); extra["extra"] = True; invalid.append(extra)
        wrong = copy.deepcopy(plan); wrong["operations"][0]["arguments"]["size"] = -1; invalid.append(wrong)
        missing = copy.deepcopy(plan); del missing["operations"][1]["arguments"]["checks"]; invalid.append(missing)
        unknown = copy.deepcopy(plan); unknown["operations"][1]["arguments"]["checks"][0]["kind"] = "python"; invalid.append(unknown)
        oversized = dict(plan, operations=plan["operations"] * 33); invalid.append(oversized)
        for value in invalid:
            self.assertFalse(validate(value))
        assert_standards_vectors(self, Draft202012Validator)

    def test_allowed_subset_and_empty_operations_schema_are_not_broadened(self):
        for allowed in ({"angelo_scene_verify"}, set()):
            with self.subTest(allowed=allowed):
                self.host.allowed_tools = allowed
                message = initialization_request(native_plan_schema(allowed), "Boundary")
                self.assertEqual(message["request"]["jsonSchema"], native_plan_schema(allowed))
                if not allowed:
                    self.assertEqual(message["request"]["jsonSchema"]["properties"]["operations"]["maxItems"], 0)

    def test_actual_windows_quoting_and_utf16_terminator_accounting(self):
        command = ['C:\\Program Files\\Claude\\claude.exe', '--resume="fixture"', "🦊"]
        quoted = subprocess.list2cmdline(command)
        self.assertEqual(windows_command_units(command), len(quoted.encode("utf-16-le")) // 2 + 1)
        with self.assertRaisesRegex(RpcError, "Angelo.*budget"):
            check_command_budget(["claude.exe", "🦊" * COMMAND_BUDGET])

    def test_oversized_non_schema_setting_fails_before_launch(self):
        self.host.turn_options["model"] = "x" * COMMAND_BUDGET
        with self.assertRaisesRegex(RpcError, "budget"):
            self.start(None)
        self.popen_mock.assert_not_called()

    def test_older_unrecognized_or_failed_version_is_actionable_without_launch(self):
        for output, returncode in ((TOO_OLD, 0), ("unknown", 0), (SUPPORTED, 1)):
            with self.subTest(output=output, returncode=returncode):
                self.run_mock.return_value = SimpleNamespace(stdout=output, returncode=returncode)
                with self.assertRaisesRegex(RpcError, REQUIRES):
                    self.start(None)
                self.popen_mock.assert_not_called()
        self.assertTrue(all(call.args[0] == ["claude.exe", "--version"] for call in self.run_mock.call_args_list))

    def test_failed_version_probe_does_not_echo_sensitive_stderr(self):
        for error in (OSError("fixture-secret"), subprocess.TimeoutExpired("fixture-secret", 10)):
            self.run_mock.side_effect = error
            with self.assertRaises(RpcError) as raised:
                require_supported_runtime("claude.exe", {})
            self.assertNotIn("fixture-secret", str(raised.exception))
            self.popen_mock.assert_not_called()

    def test_initialization_error_cleans_up_and_does_not_send_a_user_turn(self):
        self.process.initialization_error = "Native structured-output initialization rejected; update runtime"
        with self.assertRaisesRegex(RpcError, "initialization rejected"):
            self.turn(None, "Do not send")
        self.assertFalse(self.process.user_written.is_set())
        self.assertIsNone(self.provider.process)
        self.assertIsNotNone(self.process.poll())

    def test_unacknowledged_initialize_cannot_send_a_user_turn(self):
        original = self.process.receive
        def malformed(event):
            if event["type"] == "control_request" and event["request"]["subtype"] == "initialize":
                self.process.stdout.emit({"type": "control_response", "response": {
                    "subtype": "error", "request_id": "angelo-init"}})
            else:
                original(event)
        self.process.receive = malformed
        with self.assertRaisesRegex(RpcError, "not acknowledged"):
            self.turn(None, "Must not send")
        self.assertFalse(self.process.user_written.is_set())
        self.assertIsNone(self.provider.process)

    def test_successful_typed_plan_rounds_keep_one_warm_runtime_and_exact_counts(self):
        first = self.turn(None, "One")
        first_done = self.provider.done
        second = self.turn("fixture-native", "Two")
        self.assertEqual(first, second)
        self.assertIsNot(first_done, self.provider.done)
        self.popen_mock.assert_called_once()
        self.run_mock.assert_called_once()
        self.assertEqual(self.host.record_provider_turn.call_count, 2)
        self.assertEqual(self.host.record_model_round.call_count, 2)
        self.assertIs(self.provider.process, self.process)

    def test_instructions_or_tool_scope_changes_restart_instead_of_reusing_old_schema(self):
        self.turn(None, "One")
        newer = NativeInitializeFixture(on_user=lambda process, _: process.stdout.emit(completed()))
        self.popen_mock.return_value = newer
        self.host.instructions = "New boundary"
        self.host.allowed_tools = {"angelo_scene_verify"}
        try:
            self.turn("fixture-native", "Two")
            self.assertEqual(newer.initialized_schema, native_plan_schema({"angelo_scene_verify"}))
            self.assertIn("New boundary", newer.additional_instructions)
            self.assertEqual(self.popen_mock.call_count, 2)
            self.assertIsNotNone(self.process.poll())
        finally:
            self.provider.close()

    def test_runtime_error_even_with_a_structured_plan_cannot_complete(self):
        def respond(process, _):
            event = completed()
            event.update(subtype="error_max_structured_output_retries", is_error=False,
                         errors=["Native schema validation exhausted"])
            process.stdout.emit(event)
        self.process.on_user = respond
        with self.assertRaisesRegex(RpcError, "schema validation exhausted"):
            self.turn(None, "Typed plan")
        self.assertFalse(any(kind == "message_complete" for kind, _ in self.host.events))

    def test_disconnect_without_result_is_actionable_not_completed(self):
        self.process.on_user = lambda process, _: process.stdout.end()
        with self.assertRaisesRegex(RpcError, "disconnected.*reconnect"):
            self.turn(None, "Disconnected")
        self.assertFalse(any(kind == "message_complete" for kind, _ in self.host.events))

    def test_cancellation_during_initialize_retires_process_before_user_write(self):
        original = self.process.receive
        def cancel(event):
            if event["type"] == "control_request" and event["request"]["subtype"] == "initialize":
                self.host.cancelled = True
            original(event)
        self.process.receive = cancel
        self.assertIsNone(self.turn(None, "Must not send"))
        self.assertFalse(self.process.user_written.is_set())
        self.assertIsNone(self.provider.process)

    def test_generation_change_before_result_prevents_feedback_and_plan_dispatch(self):
        def respond(process, _):
            self.host._request_generation += 1
            process.stdout.emit(completed("Stale plan"))
        self.process.on_user = respond
        self.assertIsNone(self.turn(None, "Original request"))
        self.assertEqual(self.host.events, [])
        self.assertIsNone(self.provider.process)
        self.host.record_provider_turn.assert_not_called()

    def test_stop_wakes_original_waiter_and_late_old_reader_cannot_finish_new_turn(self):
        self.process.on_user = None
        replies, errors = [], []
        def turn():
            try:
                replies.append(self.turn(None, "Original"))
            except Exception as error:
                errors.append(error)
        worker = threading.Thread(target=turn)
        worker.start()
        self.assertTrue(self.process.user_written.wait(2))
        old = self.provider._transport
        self.provider.stop()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(replies, [None])
        self.assertEqual(errors, [])
        new_process = NativeInitializeFixture()
        new = ClaudeTransport(new_process, ClaudeRequest(self.host))
        self.provider._transport, self.provider.process = new, new_process
        pending = new.begin_turn(ClaudeRequest(self.host))
        old.process.stdout.emit(completed("Late original"))
        self.provider._dispatch_turn_event(old, completed("Late original"), (False, None))
        self.assertFalse(pending.done.is_set())
        self.assertEqual(self.host.events, [])
        self.assertFalse(new.retired.is_set())
        self.assertEqual(new.process.stdout.reads, 0)

    def test_retired_reader_finally_does_not_touch_replacement_initialization_or_turn(self):
        self.start(None)
        old = self.provider._transport
        new_process = NativeInitializeFixture()
        new = ClaudeTransport(new_process, ClaudeRequest(self.host))
        self.provider._transport, self.provider.process = new, new_process
        pending = new.begin_turn(ClaudeRequest(self.host))
        old.process.stdout.emit(completed("Retired"))
        old.process.wait()
        # Exercise the captured reader's finalization deterministically too.
        self.provider._read(old)
        self.assertFalse(new.initialized.is_set())
        self.assertFalse(pending.done.is_set())
        self.assertIsNone(pending.result)
        self.assertEqual(self.host.events, [])
        self.assertEqual(new_process.stdout.reads, 0)

    def test_epoch_change_blocks_native_callback_and_readback(self):
        owner = ClaudeRequest(self.host)
        callbacks, replies = [], []
        requested = threading.Event()
        def request(_id, _name, _args, callback):
            callbacks.append(callback)
            requested.set()
        self.host.request_tool = request
        worker = threading.Thread(target=lambda: replies.append(self.provider._native_call("angelo_inspect_scene", {}, owner=owner)))
        worker.start()
        self.assertTrue(requested.wait(2))
        self.host._epoch += 1
        callbacks[0]({"success": True, "object_count": 3})
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertIn("no longer owns", replies[0]["error"])
        self.assertNotIn("object_count", replies[0])

    def test_old_fenced_stop_cannot_interrupt_new_request_on_same_warm_process(self):
        self.turn(None, "Original")
        original = RequestFence.capture(self.host)
        self.host._request_generation += 1
        self.provider.bind_request(self.host)
        self.assertEqual(self.turn("fixture-native", "Next")["reply"], "Verified")
        self.provider.stop_request(original)
        self.assertIs(self.provider.process, self.process)
        self.assertIsNone(self.process.poll())
        self.assertFalse(any(event.get("request", {}).get("subtype") == "interrupt"
                             for event in self.process.stdin.messages))
        self.popen_mock.assert_called_once()

    def test_matching_fenced_stop_retires_current_request(self):
        self.turn(None, "Original")
        self.provider.stop_request(RequestFence.capture(self.host))
        self.assertIsNone(self.provider.process)
        self.assertIsNotNone(self.process.poll())
        self.assertTrue(any(event.get("request", {}).get("subtype") == "interrupt"
                            for event in self.process.stdin.messages))

    def test_replaced_executable_version_is_rechecked_on_next_actual_launch(self):
        self.turn(None, "Original")
        self.provider.stop()
        self.run_mock.return_value = SimpleNamespace(returncode=0, stdout=TOO_OLD)
        with self.assertRaisesRegex(RpcError, REQUIRES):
            self.turn("fixture-native", "New binary at same path")
        self.assertEqual(self.run_mock.call_count, 2)
        self.popen_mock.assert_called_once()

    def test_bind_refuses_active_turn_instead_of_reassigning_its_context(self):
        self.start(None)
        pending = self.provider._transport.begin_turn(ClaudeRequest(self.host))
        replacement = host()
        with self.assertRaisesRegex(RpcError, "Stop the active Claude turn"):
            self.provider.bind_request(replacement)
        self.assertIs(pending.controller, self.host)
        self.assertIs(self.provider.controller, self.host)

    def test_cancelled_before_turn_never_launches_or_writes(self):
        self.host.cancelled = True
        self.assertIsNone(self.turn(None, "Cancelled"))
        self.run_mock.assert_not_called()
        self.popen_mock.assert_not_called()

    def test_model_discovery_uses_same_bounded_supported_initialization(self):
        self.provider.connect = Mock(return_value=None)
        models = self.provider.list_models()
        self.assertTrue(models)
        self.assertLess(windows_command_units(self.popen_mock.call_args.args[0]), COMMAND_BUDGET)
        self.assertEqual(self.process.initialized_schema, native_plan_schema())
        self.assertFalse(self.process.user_written.is_set())


if __name__ == "__main__":
    unittest.main()
