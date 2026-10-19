# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Real Controller/approval routes with Blender stand-ins; no provider or profile."""
import ast
import contextlib
import io
import sys
import time
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.controller import Controller, ToolDispatchBudget
from angelo_agent.managed_policy import Policy, combine, with_organization
from angelo_agent.tool_specs import validate_call, TOOL_MAP
from angelo_agent.team_view import org_policy
from test_native_dispatch_fixes import ui_class


class TrustedScriptTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.controller = Controller(self.directory.name)
        self.controller.busy = True
        self.controller.set_document(("isolated",))
        self.controller.request_checkpoint = "9cf4e420-dcdb-4f45-bc8a-d9d439ce9aac"
        self.executed = []
        def execute(name, arguments):
            self.executed.append((name, arguments.copy()))
            namespace = {"scene": SimpleNamespace(value=0), "result": None}
            exec(compile(arguments["code"], "<angelo-reviewed-script>", "exec"), namespace)
            return {"success": True, "result": namespace["result"]}
        self.executor = SimpleNamespace(precondition=Mock(return_value="current"), execute=execute)

    def tearDown(self):
        self.controller.close()
        self.directory.cleanup()

    def enqueue(self, identity="script"):
        results = []
        self.controller.request_tool(identity, "angelo_execute_python", {
            "description": "Set a scene value", "code": "scene.value = 42\nresult = scene.value"}, results.append)
        return next(c for c in self.controller.pending.values() if c.id.endswith("/" + identity)), results

    def pump(self, apply=None):
        self.controller.pump_tools(self.executor, self.controller.document,
            apply_mutation=apply or (lambda call: self.controller.execute_tool(call, self.executor, self.controller.document)),
            budget=ToolDispatchBudget(seconds=60))

    def test_integrated_call_owner_retains_delivery_and_submission_identity(self):
        from angelo_agent import controller, pending_delivery
        self.assertIs(controller.ToolCall, pending_delivery.ToolCall)
        self.controller.set_trusted_scripts(True)
        context = self.controller.runtime_context()
        results = []
        context.request_tool("native-script", "angelo_execute_python", {
            "description": "Set a scene value", "code": "scene.value = 42\nresult = scene.value"}, results.append)
        call = next(iter(self.controller.pending.values()))
        self.assertIs(call.delivery_owner, context.delivery_owner)
        self.assertIsNotNone(call.result_observer)
        self.assertTrue(call.submitted_arguments_digests)
        self.assertIs(call.script_consent, self.controller._script_consent)
        self.pump()
        self.assertEqual(results[0]["result"], 42)
        self.assertIsNone(context.program_completions.get(results[0]), "Script success is not native program proof")

    def test_trusted_script_cannot_execute_after_native_delivery_closed(self):
        from angelo_agent.request_fences import DeliveryOwner
        self.controller.set_trusted_scripts(True)
        context = self.controller.runtime_context()
        owner = DeliveryOwner(context.request_fence, context.delivery_owner)
        results = []
        self.controller.request_tool("closed-native", "angelo_execute_python", {
            "description": "Set a scene value", "code": "scene.value = 42\nresult = scene.value"},
            results.append, delivery_owner=owner)
        owner.close()
        self.pump()
        self.assertFalse(self.executed)
        self.assertIn("delivery ended", results[0]["error"])
        self.assertNotIn("execution_mode", results[0])

    def test_unconfined_authority_fields_preserve_primary_and_cleanup_recovery(self):
        self.controller.set_trusted_scripts(True)
        call, results = self.enqueue()
        self.executor.execute = lambda name, args: {
            "error": "Primary execution failure", "cleanup_error": "Restore cleanup failed",
            "rolled_back": False, "partial_changes_possible": True,
            "recovery": "Inspect the scene and request checkpoint; do not retry",
            "changes": {"modified": [{"name": "Sphere", "fields": ["geometry"]}]}}
        self.pump()
        result = results[0]
        self.assertEqual(result["error"], "Primary execution failure")
        self.assertEqual(result["cleanup_error"], "Restore cleanup failed")
        self.assertFalse(result["rolled_back"])
        self.assertTrue(result["partial_changes_possible"])
        self.assertEqual(result["recovery"], "Inspect the scene and request checkpoint; do not retry")
        self.assertEqual(result["request_checkpoint"], self.controller.request_checkpoint)
        self.assertEqual(result["execution_mode"], "trusted_unconfined")
        self.assertFalse(result["confined"])

    def test_default_off_and_old_auto_bounded_never_grants_trust(self):
        self.controller.set_options(approval="AUTO_BOUNDED", trusted_scripts=True)
        call, results = self.enqueue()
        self.pump()
        self.assertFalse(self.controller.trusted_scripts)
        self.assertTrue(call.approval)
        self.assertFalse(results)
        self.assertFalse(self.executed)

    def test_exact_manual_review_scene_only_runs_once(self):
        call, results = self.enqueue()
        self.pump()
        self.controller.review_script(call)
        self.controller.execute_tool(call, self.executor, self.controller.document)
        self.controller.execute_tool(call, self.executor, self.controller.document)
        self.assertEqual(len(self.executed), 1)
        self.assertEqual(results[0]["result"], 42)
        self.assertEqual(results[0]["execution_mode"], "trusted_unconfined")
        self.assertFalse(results[0]["confined"])
        self.assertEqual(results[0]["consent"], "exact_code_review")

    def test_trusted_auto_runs_with_truthful_fields(self):
        self.controller.set_trusted_scripts(True)
        call, results = self.enqueue()
        self.pump()
        self.assertEqual(len(self.executed), 1)
        self.assertEqual(results[0]["consent"], "chat_session")
        self.assertFalse(results[0]["external_effects_undoable"])
        self.assertFalse(results[0]["stop_interruptible"])

    def test_failure_retains_uncertainty_and_unconfined_authority(self):
        self.controller.set_trusted_scripts(True)
        call, results = self.enqueue()
        self.executor.execute = Mock(side_effect=RuntimeError("Script failed after an unknown effect"))
        self.pump()
        self.assertIn("error", results[0])
        self.assertTrue(results[0]["partial_changes_possible"])
        self.assertFalse(results[0].get("success", False))
        self.assertEqual(results[0]["execution_mode"], "trusted_unconfined")
        self.assertFalse(results[0]["external_effects_undoable"])
        self.assertIn("request_checkpoint", results[0])

    def test_exit_or_interrupt_from_a_tool_is_a_failure_never_the_end_of_angelo(self):
        for raised in (SystemExit(1), SystemExit(), KeyboardInterrupt()):
            with self.subTest(raised=type(raised).__name__):
                self.tearDown()
                self.setUp()
                self.controller.set_trusted_scripts(True)
                call, results = self.enqueue()
                self.executor.execute = Mock(side_effect=raised)
                self.pump()  # A BaseException escaping here would fail the test, as it would end Angelo.
                self.assertIn(type(raised).__name__, results[0]["error"])
                self.assertTrue(results[0]["partial_changes_possible"])
                self.assertFalse(results[0].get("success", False))
                self.assertNotIn(call.id, self.controller.pending)

    def test_opt_in_never_authorizes_old_queue(self):
        call, results = self.enqueue()
        self.controller.set_trusted_scripts(True)
        self.pump()
        self.assertFalse(self.executed)
        self.assertTrue(call.approval)

    def test_revocation_after_precondition_never_runs(self):
        self.controller.set_trusted_scripts(True)
        call, results = self.enqueue()
        self.executor.precondition.side_effect = lambda *args: (self.controller.set_trusted_scripts(False), "current")[1]
        self.pump()
        self.assertFalse(self.executed)
        self.assertTrue(call.approval)

    def test_revocation_at_journal_never_runs(self):
        self.controller.set_trusted_scripts(True)
        call, results = self.enqueue()
        journal = self.controller._journal_tool
        def revoke(call, phase):
            journal(call, phase)
            if phase == "execution_started":
                self.controller.set_trusted_scripts(False)
        self.controller._journal_tool = revoke
        self.pump()
        self.assertFalse(self.executed)
        self.assertIn("consent changed", results[0]["error"])

    def test_manual_review_after_standing_consent_revocation_runs_once(self):
        self.controller.set_trusted_scripts(True)
        call, results = self.enqueue()
        def hold(call):
            self.controller.set_trusted_scripts(False)
        self.pump(apply=hold)
        self.assertTrue(call.automatic)
        self.controller.review_script(call)
        self.controller.execute_tool(call, self.executor, self.controller.document)
        self.assertEqual(len(self.executed), 1)
        self.assertEqual(results[0]["consent"], "exact_code_review")

    def test_read_only_decline_stale_turn_document_never_run(self):
        for action in (lambda c, call: c.set_options(approval="READ_ONLY"),
                       lambda c, call: c.decline_tool(call.id),
                       lambda c, call: setattr(c, "_request_generation", c._request_generation + 1),
                       lambda c, call: c.set_document(("other",))):
            with self.subTest(action=action):
                self.controller.set_options(approval="ASK")
                self.controller.busy = True
                self.controller.cancelled = False
                self.controller.document_invalid = False
                self.controller.set_trusted_scripts(True)
                call, results = self.enqueue(str(id(action)))
                action(self.controller, call)
                self.pump()
                self.assertFalse(self.executed)

    def test_code_change_after_review_never_runs(self):
        call, results = self.enqueue()
        self.pump()
        self.controller.review_script(call)
        call.arguments["code"] = "result = 'changed'"
        self.controller.execute_tool(call, self.executor, self.controller.document)
        self.assertFalse(self.executed)
        self.assertIn("exact script", results[0]["error"])

    def test_pending_identity_replacement_cannot_run_old_call(self):
        call, results = self.enqueue()
        self.pump()
        self.controller.review_script(call)
        self.controller.pending[call.id] = SimpleNamespace()
        self.controller.execute_tool(call, self.executor, self.controller.document)
        self.assertFalse(self.executed)
        self.controller.pending.pop(call.id)

    def test_policy_blocks_opt_in_and_queued_auto(self):
        policy = combine([("test", {"AllowAutoApproval": False})])
        self.assertFalse(policy.allow_auto_approval)
        self.assertFalse(org_policy({"allowAutoApproval": "yes"})["allowAutoApproval"])
        self.assertFalse(with_organization(Policy(), org_policy({"allowAutoApproval": False})).allow_auto_approval)
        self.assertFalse(with_organization(Policy(), {"allowAutoApproval": False}).allow_auto_approval)
        self.controller.set_trusted_scripts(True)
        call, results = self.enqueue()
        with patch("angelo_agent.managed_policy.current", return_value=policy):
            self.assertFalse(self.controller.trusted_scripts)
            with self.assertRaises(RuntimeError):
                self.controller.set_trusted_scripts(True)
            self.pump()
        self.assertFalse(self.executed)

    def test_restart_and_new_chat_clear_trust(self):
        self.controller.set_trusted_scripts(True)
        self.controller.save()
        other = Controller(self.directory.name, session_id=self.controller.state["id"])
        try:
            self.assertFalse(other.trusted_scripts)
        finally:
            other.close()
        self.controller.busy = False
        self.controller.new_chat(self.controller.document)
        self.assertFalse(self.controller.trusted_scripts)

    def test_ui_requires_invoked_consent_dialog_and_current_owner(self):
        cls = ui_class("ANGELO_OT_trusted_scripts", {"get_controller": lambda: self.controller})
        op = cls()
        op.report = Mock()
        context = SimpleNamespace(window_manager=SimpleNamespace(invoke_props_dialog=Mock(return_value={'RUNNING_MODAL'})))
        self.assertEqual(op.execute(context), {'CANCELLED'})
        self.assertFalse(self.controller.trusted_scripts)
        op.invoke(context, None)
        self.assertEqual(op.execute(context), {'FINISHED'})
        op.invoke(context, None)  # Disable immediately.
        self.assertFalse(self.controller.trusted_scripts)
        op.invoke(context, None)
        self.controller.document = ("changed",)
        self.assertEqual(op.execute(context), {'CANCELLED'})

    def test_same_confirmed_dialog_instance_cannot_reenable_after_revocation(self):
        cls = ui_class("ANGELO_OT_trusted_scripts", {"get_controller": lambda: self.controller})
        op = cls()
        op.report = Mock()
        context = SimpleNamespace(window_manager=SimpleNamespace(invoke_props_dialog=Mock(return_value={'RUNNING_MODAL'})))
        op.invoke(context, None)
        self.assertEqual(op.execute(context), {'FINISHED'})
        self.controller.set_trusted_scripts(False)
        self.assertEqual(op.execute(context), {'CANCELLED'})
        self.assertFalse(self.controller.trusted_scripts)
        op.invoke(context, None)  # A new human dialog may confirm using the same native instance.
        self.assertEqual(op.execute(context), {'FINISHED'})
        self.assertTrue(self.controller.trusted_scripts)

    def test_cancel_and_disable_clear_the_same_dialog_instance(self):
        cls = ui_class("ANGELO_OT_trusted_scripts", {"get_controller": lambda: self.controller})
        op = cls()
        op.report = Mock()
        context = SimpleNamespace(window_manager=SimpleNamespace(invoke_props_dialog=Mock(return_value={'RUNNING_MODAL'})))
        op.invoke(context, None)
        op.cancel(context)
        self.assertEqual(op.execute(context), {'CANCELLED'})
        self.assertFalse(self.controller.trusted_scripts)
        op.invoke(context, None)
        self.controller.set_trusted_scripts(True)
        self.assertEqual(op.invoke(context, None), {'FINISHED'})  # The disable action consumes any old dialog.
        self.assertFalse(self.controller.trusted_scripts)
        self.assertEqual(op.execute(context), {'CANCELLED'})
        self.assertFalse(self.controller.trusted_scripts)

    def test_actual_blender_executor_python_branch_with_stand_in_bpy(self):
        path = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent/blender_tools.py"
        source = ast.parse(path.read_text())
        executor_class = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == "BlenderExecutor")
        methods = [n for n in executor_class.body if isinstance(n, ast.FunctionDef) and n.name in {"execute", "_execute"}]
        output_class = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == "LimitedOutput")
        scene = SimpleNamespace(name="Scratch", objects=[], value=0)
        window = SimpleNamespace(scene=scene, screen=SimpleNamespace(areas=[SimpleNamespace(
            type="VIEW_3D", regions=[SimpleNamespace(type="WINDOW")])]), view_layer=SimpleNamespace(update=lambda: None))
        namespace = {"__package__": "angelo_agent", "contextlib": contextlib, "io": io, "time": time,
            "validate_call": validate_call, "TOOL_MAP": TOOL_MAP,
            "bpy": SimpleNamespace(context=SimpleNamespace(mode="OBJECT", temp_override=lambda **kw: contextlib.nullcontext())),
            "job_host": SimpleNamespace(guard=lambda name, arguments: None),
            "scene_signature": lambda *args: {}, "_typed_targets": lambda *args: [], "scene_diff": lambda *args: {}}
        for name in ("SCULPT_NAMES", "JOB_NAMES", "STUDIO_NAMES", "PROGRAM_NAMES", "MEMORY_NAMES", "FLOW_NAMES",
                     "TEAM_NAMES", "SCENE_NAMES", "VALIDATION_NAMES", "RECIPE_NAMES", "VIEW_NAMES", "EXECUTION_NAMES", "READ_NAMES"):
            namespace[name] = frozenset()
        definition = ast.ClassDef(name="Executor", bases=[], keywords=[], body=methods, decorator_list=[])
        module = ast.fix_missing_locations(ast.Module(body=[output_class, definition], type_ignores=[]))
        exec(compile(module, str(path), "exec"), namespace)
        executor = namespace["Executor"]()
        executor.window, executor.precondition = window, Mock(return_value="current")
        self.controller.set_trusted_scripts(True)
        call, results = self.enqueue()
        self.controller.pump_tools(executor, self.controller.document,
            apply_mutation=lambda call: self.controller.execute_tool(call, executor, self.controller.document),
            budget=ToolDispatchBudget(seconds=60))
        self.assertEqual(scene.value, 42)
        self.assertEqual(results[0]["result"], 42)
        self.assertFalse(results[0]["undoable"])
        self.assertFalse(results[0]["main_data_undoable"])
        self.assertFalse(results[0]["image_pixels_undoable"])
        self.assertFalse(results[0]["undo_available"], "The stand-in supplies no native Undo history")
        self.assertEqual(results[0]["undo_scope"], "MAIN_DATA")
        self.assertFalse(results[0]["confined"])

    def test_real_approve_and_apply_operators_use_reviewed_route(self):
        from angelo_agent import script_errors

        call, results = self.enqueue()
        self.pump()
        self.controller.review_script(call)
        context = SimpleNamespace(preferences=SimpleNamespace(edit=SimpleNamespace(use_global_undo=True)))
        namespaces = {"_controllers": {self.controller.state["id"]: self.controller},
            "get_controller": lambda: self.controller, "BlenderExecutor": lambda window: self.executor,
            "main_window": lambda: object(), "document_key": lambda: self.controller.document,
            "STUDIO_NAMES": frozenset(), "script_errors": script_errors,
            "bpy": SimpleNamespace(context=SimpleNamespace(mode="OBJECT",
                temp_override=lambda **kw: contextlib.nullcontext()), ops=SimpleNamespace(angelo=SimpleNamespace()))}
        apply_class = ui_class("ANGELO_OT_apply_approved_tool", namespaces)
        apply = script_errors.grouped(apply_class)()
        self.assertEqual(apply.bl_options, {"UNDO_GROUPED"})
        apply.call_id, apply.session_id, apply.report = call.id, self.controller.state["id"], Mock()
        setattr(namespaces["bpy"].ops.angelo, script_errors.APPLY_SCRIPT,
                lambda *args, **kwargs: apply.execute(context))
        approve = ui_class("ANGELO_OT_approve", namespaces)()
        approve.call_id, approve.session_id, approve.report = call.id, self.controller.state["id"], Mock()
        self.assertEqual(approve.execute(context), {'FINISHED'})
        self.assertEqual(len(self.executed), 1)
        self.assertEqual(results[0]["result"], 42)


if __name__ == "__main__":
    unittest.main()
