# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later

import json
import math
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.controller import Controller, ToolDispatchBudget
from angelo_agent.providers import ClaudeProvider, CodexProvider, child_environment
from angelo_agent.sessions import SessionStore
from angelo_agent.tool_specs import TOOLS, validate_call, native_plan_schema


class Executor:
    def __init__(self):
        self.calls = []
        self.fingerprint = "unchanged"

    def precondition(self, name, arguments):
        return self.fingerprint

    def execute(self, name, arguments):
        self.calls.append((name, arguments))
        return {"success": True, "native_exec_ms": 0.1}


class NativeRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.controller = Controller(self.directory.name)
        self.controller.set_document(("instance", "scratch.blend", "Scene"))
        self.controller.busy = True
        self.executor = Executor()

    def tearDown(self):
        self.controller.close()
        self.directory.cleanup()

    def call(self, id, name, arguments):
        responses = []
        self.controller.request_tool(id, name, arguments, responses.append)
        self.controller.pump_tools(self.executor, self.controller.document, budget=ToolDispatchBudget(seconds=60))
        return responses

    def test_read_only_dispatch_is_direct(self):
        answers = self.call("read", "angelo_inspect_scene", {})
        self.assertTrue(answers[0]["success"])
        self.assertEqual(len(self.executor.calls), 1)

    def test_mutation_requires_explicit_approval(self):
        answers = self.call("cube", "angelo_create_cube", {"name": "Cube", "size": 1, "location": [0, 0, 0]})
        self.assertEqual(answers, [])
        self.assertEqual(self.executor.calls, [])
        pending = next(iter(self.controller.pending.values()))
        self.assertTrue(pending.approval)
        self.controller.execute_tool(pending, self.executor, self.controller.document)
        self.assertTrue(answers[0]["success"])

    def test_duplicate_id_does_not_execute_twice(self):
        self.call("same", "angelo_inspect_scene", {})
        answers = self.call("same", "angelo_inspect_scene", {})
        self.assertTrue(answers[0]["replayed"])
        self.assertEqual(len(self.executor.calls), 1)

    def test_declined_operation_is_not_applied(self):
        answers = self.call("deny", "angelo_create_cube", {"name": "Cube", "size": 1, "location": [0, 0, 0]})
        key = next(iter(self.controller.pending))
        self.controller.decline_tool(key)
        self.assertIn("declined", answers[0]["error"])
        self.assertEqual(self.executor.calls, [])

    def test_cancel_rejects_late_callbacks(self):
        self.controller.stop()
        answers = self.call("late", "angelo_create_cube", {"name": "Cube", "size": 1, "location": [0, 0, 0]})
        self.assertIn("error", answers[0])
        self.assertEqual(self.executor.calls, [])

    def test_changed_target_rejects_approval(self):
        answers = self.call("stale", "angelo_set_transform", {"object": "Cube", "location": [1, 0, 0]})
        call = next(iter(self.controller.pending.values()))
        self.executor.fingerprint = "changed"
        self.controller.execute_tool(call, self.executor, self.controller.document)
        self.assertIn("changed", answers[0]["error"])
        self.assertEqual(self.executor.calls, [])

    def test_changed_document_cancels_approval(self):
        answers = self.call("document", "angelo_create_cube", {"name": "Cube", "size": 1, "location": [0, 0, 0]})
        self.controller.set_document(("another", "other.blend", "Scene"))
        self.assertIn("error", answers[0])
        self.assertEqual(self.executor.calls, [])

    def test_invalid_arguments_rejected(self):
        for value in [math.nan, math.inf, True, -1, 1001, 1e-100]:
            with self.assertRaises(ValueError):
                validate_call("angelo_create_cube", {"name": "Cube", "size": value, "location": [0, 0, 0]})
        with self.assertRaises(ValueError):
            validate_call("execute_python", {"code": "print('no')"})

    def test_native_batch_is_bounded(self):
        op = {"tool": "angelo_create_cube", "arguments": {"name": "Cube", "size": 1, "location": [0, 0, 0]}}
        self.assertEqual(len(validate_call("angelo_apply_batch", {"operations": [op] * 64})["operations"]), 64)
        with self.assertRaises(ValueError):
            validate_call("angelo_apply_batch", {"operations": [op] * 65})
        self.assertEqual(native_plan_schema()["properties"]["operations"]["minItems"], 0)

    def test_session_resume_and_no_secret_serialization(self):
        self.controller.api_key = "secret-never-persist"
        self.controller.state["native_id"] = "native-thread-id"
        self.controller.save()
        restored = SessionStore(self.directory.name).load()
        self.assertEqual(restored["native_id"], "native-thread-id")
        self.assertNotIn("secret-never-persist", self.controller.store.path.read_text())
        if os.name != "nt":  # POSIX mode bits; on Windows the per-user profile's access list applies
            self.assertEqual(os.stat(self.controller.store.path).st_mode & 0o777, 0o600)

    def test_subscription_does_not_inherit_api_billing(self):
        old = os.environ.get("ANTHROPIC_API_KEY")
        os.environ["ANTHROPIC_API_KEY"] = "must-not-be-used"
        try:
            self.assertNotIn("ANTHROPIC_API_KEY", child_environment("claude_subscription"))
            self.assertEqual(child_environment("claude_api", "explicit-key")["ANTHROPIC_API_KEY"], "explicit-key")
        finally:
            if old is None:
                os.environ.pop("ANTHROPIC_API_KEY", None)
            else:
                os.environ["ANTHROPIC_API_KEY"] = old

    def test_qa_process_owner_reaches_both_provider_children(self):
        owner = "a" * 32
        with patch.dict(os.environ, {"ANGELO_QA_PROCESS_OWNER": owner}, clear=True):
            for mode in ("codex_subscription", "claude_subscription"):
                with self.subTest(mode=mode):
                    self.assertEqual(child_environment(mode)["ANGELO_QA_PROCESS_OWNER"], owner)

    def test_only_reviewed_native_execution_surfaces(self):
        from angelo_agent.studio_spec import STUDIO_NAMES
        from angelo_agent.flow_tool_spec import FLOW_NAMES
        from angelo_agent.team_library_spec import TEAM_NAMES
        from angelo_agent.job_spec import JOB_NAMES
        from angelo_agent.rig_spec import RIG_NAMES
        self.assertEqual({t["name"] for t in TOOLS}, {"angelo_inspect_scene", "angelo_create_cube", "angelo_set_transform", "angelo_apply_batch", "angelo_execute_python", "angelo_read_reference", "angelo_list_files", "angelo_read_file", "angelo_write_file", "angelo_delete_file", "angelo_delegate", "angelo_inspect_sculpt", "angelo_prepare_sculpt", "angelo_sculpt_batch", "angelo_sculpt_view", "angelo_sculpt_target", "angelo_sculpt_program", "angelo_sculpt_region", "angelo_sculpt_resolution", "angelo_sculpt_review"} | STUDIO_NAMES | {"angelo_set_workspace_mode", "angelo_scene_search", "angelo_scene_object", "angelo_scene_relationships", "angelo_scene_delta", "angelo_scene_settings", "angelo_agent_capabilities", "angelo_blender_api", "angelo_agent_metrics", "angelo_memory_read", "angelo_memory_update", "angelo_scene_program", "angelo_inspect_preview", "angelo_accept_preview", "angelo_discard_preview", "angelo_scene_verify", "angelo_expand_recipe", "angelo_apply_recipe", "angelo_view", "angelo_read_data", "angelo_blend_file",
                                                     # This conversation's own transcript, read-only (script_store).
                                                     "angelo_script",
                                                     # The deferred web export and preview (web_export_spec).
                                                     "angelo_web_check", "angelo_web_export", "angelo_web_preview"} | FLOW_NAMES | TEAM_NAMES | JOB_NAMES
                            | RIG_NAMES)

    def test_read_only_policy_rejects_all_mutations(self):
        self.controller.state["options"]["approval"] = "READ_ONLY"
        for name, args in [("angelo_create_cube", {"name": "Cube", "size": 1, "location": [0, 0, 0]}),
                           ("angelo_execute_python", {"description": "Create a sphere", "code": "bpy.ops.mesh.primitive_uv_sphere_add()"})]:
            answers = self.call(name, name, args)
            self.assertIn("read-only", answers[0]["error"])
        self.assertEqual(self.executor.calls, [])

    def test_auto_bounded_policy_never_autoapproves_python(self):
        self.controller.state["options"]["approval"] = "AUTO_BOUNDED"
        answers = []
        self.controller.request_tool("cube", "angelo_create_cube", {"name": "Cube", "size": 1, "location": [0, 0, 0]}, answers.append)
        native_undo_dispatch = Mock(side_effect=lambda call: self.controller.execute_tool(call, self.executor, self.controller.document))
        self.controller.pump_tools(self.executor, self.controller.document, apply_mutation=native_undo_dispatch,
                                   budget=ToolDispatchBudget(seconds=60))
        self.assertTrue(answers[0]["success"])
        native_undo_dispatch.assert_called_once()
        scripts = []
        self.controller.request_tool("script", "angelo_execute_python", {"description": "Create a sphere", "code": "bpy.ops.mesh.primitive_uv_sphere_add()"}, scripts.append)
        self.controller.pump_tools(self.executor, self.controller.document, apply_mutation=native_undo_dispatch,
                                   budget=ToolDispatchBudget(seconds=60))
        self.assertEqual(scripts, [])
        self.assertFalse(next(iter(self.controller.pending.values())).automatic)
        self.assertEqual(native_undo_dispatch.call_count, 1)

    def test_scripts_cannot_hide_in_a_bounded_batch_and_syntax_is_checked(self):
        script = {"description": "Sphere", "code": "bpy.ops.mesh.primitive_uv_sphere_add()"}
        validate_call("angelo_execute_python", script)
        with self.assertRaises(ValueError):
            validate_call("angelo_apply_batch", {"operations": [{"tool": "angelo_execute_python", "arguments": script}]})
        with self.assertRaises(ValueError):
            validate_call("angelo_execute_python", dict(script, code="for invalid"))

    def test_result_ledger_redacts_the_current_api_key(self):
        self.controller.api_key = "private-test-key"
        self.executor.execute = Mock(return_value={"success": True, "output": "private-test-key"})
        self.call("read", "angelo_inspect_scene", {})
        self.assertNotIn("private-test-key", self.controller.store.path.read_text())

    def test_stop_during_running_native_work_does_not_report_a_false_cancellation(self):
        def execute(name, arguments):
            self.controller.stop()
            return {"success": True, "note": "Already-running native work completed"}
        self.executor.execute = execute
        answers = self.call("read", "angelo_inspect_scene", {})
        self.assertTrue(answers[0]["success"])
        self.assertNotIn("error", answers[0])

    def test_named_profile_switch_does_not_resume_another_accounts_native_thread(self):
        self.controller.busy = False
        self.controller.select("claude_subscription", {"connection_id": "profile-a"})
        self.controller.state["native_id"] = "account-a-thread"
        self.controller.select("claude_subscription", {"connection_id": "profile-b"})
        self.assertIsNone(self.controller.state["native_id"])

    def test_claude_can_plan_general_blender_python_without_mcp(self):
        provider = ClaudeProvider(self.controller, {})
        operation = {"tool": "angelo_execute_python", "arguments": {"description": "Sphere", "code": "bpy.ops.mesh.primitive_uv_sphere_add()"}}
        provider._turn = Mock(return_value={"reply": "I will create a sphere.", "operations": [operation], "verify": False})
        provider._native_call = Mock(return_value={"success": True, "verified": {"object_count": 4}})
        self.assertEqual(provider.run("native-id", "prompt", observation={"object_count": 3}), "completed")
        provider._native_call.assert_called_once_with(operation["tool"], operation["arguments"],
                                                    owner=provider._turn.call_args.kwargs["owner"])

    def test_stop_during_connect_does_not_start_inference(self):
        controller = self.controller

        class CancelDuringConnect:
            def __init__(self, controller, settings):
                self.run = Mock()

            def connect(self):
                controller.stop()
                return "native-id"

            def stop(self):
                pass

            def close(self):
                pass

        with patch("angelo_agent.controller.CodexProvider", CancelDuringConnect):
            controller._run("cancelled prompt")
        controller.provider.run.assert_not_called()
        controller.pump_events()
        self.assertFalse(controller.busy)
        self.assertEqual(controller.status, "Interrupted")

    def test_stop_racing_turn_start_interrupts_the_new_native_turn(self):
        provider = CodexProvider(self.controller, {})

        class Rpc:
            closed = threading.Event()

            def request(inner, method, params, **kwargs):
                if method == "turn/start":
                    self.controller.cancelled = True
                    return {"turn": {"id": "new-turn"}}
                self.assertEqual(method, "turn/interrupt")
                self.assertEqual(params, {"threadId": "native-thread", "turnId": "new-turn"})
                provider.turn_status = "interrupted"
                provider.done.set()

        provider.rpc = Rpc()
        self.assertEqual(provider.run("native-thread", "prompt"), "interrupted")

    def test_credential_profile_switch_starts_a_new_native_conversation(self):
        self.controller.busy = False
        self.controller.select("codex_subscription", {"reuse_codex_login": True})
        self.controller.state["native_id"] = "existing-cli-thread"
        self.controller.select("codex_subscription", {"reuse_codex_login": False})
        self.assertIsNone(self.controller.state["native_id"])
        self.controller.select("codex_api", {}, "old-key")
        self.controller.state["native_id"] = "old-api-thread"
        self.controller.select("codex_api", {}, "new-key")
        self.assertIsNone(self.controller.state["native_id"])

    def test_claude_simple_plan_uses_observation_and_verified_batch_without_extra_roundtrip(self):
        provider = ClaudeProvider(self.controller, {})
        operation = {"tool": "angelo_create_cube", "arguments": {"name": "Cube", "size": 1, "location": [0, 0, 0]}}
        provider._turn = Mock(return_value={"reply": "I will create a cube.", "operations": [operation], "verify": False})
        provider._native_call = Mock(return_value={"success": True, "verified": {"object_count": 4}})
        result = provider.run("native-id", "prompt with current observation", observation={"object_count": 3})
        self.assertEqual(result, "completed")
        provider._turn.assert_called_once()
        provider._native_call.assert_called_once_with("angelo_apply_batch", {"operations": [operation]},
                                                    owner=provider._turn.call_args.kwargs["owner"])


if __name__ == "__main__":
    unittest.main()
