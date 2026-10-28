# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Main-thread scheduling, expiry, and durable failure-recovery regressions."""

import ast
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import display_text, document_scene, ui_text, worker_consent_lead
from angelo_agent import changes_popup, chat_changes, chat_guard  # noqa: E402
from angelo_agent.chat_changes import scene_change_report
from angelo_agent.controller import Controller, ToolDispatchBudget, pump_controller_tools


UI_NAMES = {"iface_": ui_text.iface_, "n_": ui_text.n_, "notice": ui_text.notice, "rpt_": ui_text.rpt_,
            "Text": display_text.Text, "shown": display_text.shown,
            "document_scene": document_scene,
            "worker_consent_lead": worker_consent_lead}  # What ui.py imports at module level.


def program():
    return {"object": "Sphere", "mesh_revision": "a" * 64, "steps": [{
        "brush": "DRAW", "radius": .3, "strength": .3, "direction": "ADD", "symmetry": "NONE",
        "normal": [0, -1, 0], "front_faces_only": True, "spacing": .2,
        "paths": [[{"position": [0, -1, 0], "pressure": 1}]],
    }]}


def ui_class(name, namespace):
    """Run the actual UI operator without loading bpy's application runtime."""
    path = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent/ui.py"
    if name in {"ANGELO_OT_trusted_scripts", "ANGELO_OT_set_approval"}:
        path = path.with_name("trusted_scripts_ui.py")
    module = ast.parse(path.read_text())
    definition = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == name)
    code = ast.Module(body=[definition], type_ignores=[])
    namespace = {"Operator": object, "StringProperty": lambda **_: None, **UI_NAMES} | namespace
    exec(compile(code, str(path), "exec"), namespace)
    return namespace[name]


class DispatchFixTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.controller = Controller(self.directory.name)
        self.controller.busy = True
        self.controller.set_document(("scratch",))
        self.controller.state["options"]["approval"] = "AUTO_BOUNDED"
        self.controller.request_checkpoint = "9cf4e420-dcdb-4f45-bc8a-d9d439ce9aac"
        self.executor = SimpleNamespace(precondition=Mock(return_value="unchanged"),
                                        execute=Mock(return_value={"success": True}))

    def tearDown(self):
        self.controller.close()
        self.directory.cleanup()

    def enqueue(self, identity="program", name="angelo_sculpt_program", arguments=None, callback=None):
        replies = []
        self.controller.request_tool(identity, name, program() if arguments is None else arguments,
                                     callback or replies.append)
        return replies, self.controller.pending.get(self.controller.state["id"] + "/" + identity)

    def pump(self, budget=None):
        self.controller.pump_tools(self.executor, self.controller.document,
            apply_mutation=lambda call: self.controller.execute_tool(call, self.executor, self.controller.document),
            budget=budget or ToolDispatchBudget(seconds=60))

    def test_32_queued_programs_execute_over_32_ui_ticks(self):
        replies = [self.enqueue(str(i))[0] for i in range(32)]
        for tick in range(32):
            self.pump()
            self.assertEqual(self.executor.execute.call_count, tick + 1)
            self.assertEqual(sum(bool(result) for result in replies), tick + 1)
        self.assertFalse(self.controller.pending)
        self.assertFalse(self.controller.has_dispatchable_tools)

    def test_stop_between_programs_leaves_later_programs_unapplied(self):
        replies = [self.enqueue(str(i))[0] for i in range(5)]
        self.pump()
        self.controller.stop()
        self.pump()
        self.assertEqual(self.executor.execute.call_count, 1)
        self.assertTrue(replies[0][0]["success"])
        self.assertTrue(all("cancelled" in result[0]["error"] for result in replies[1:]))

    def test_pending_approvals_share_the_same_one_execution_budget(self):
        self.controller.state["options"]["approval"] = "ASK"
        replies = [self.enqueue(str(i))[0] for i in range(3)]
        self.pump()
        self.assertTrue(all(call.approval for call in self.controller.pending.values()))
        self.controller.state["options"]["approval"] = "AUTO_BOUNDED"
        for count in range(1, 4):
            self.pump()
            self.assertEqual(self.executor.execute.call_count, count)
            self.assertEqual(sum(bool(result) for result in replies), count)

    def test_prepared_approval_does_not_starve_behind_new_work(self):
        self.controller.state["options"]["approval"] = "ASK"
        old, _ = self.enqueue("old")
        self.pump()
        self.controller.state["options"]["approval"] = "AUTO_BOUNDED"
        new, _ = self.enqueue("new")
        self.pump()
        self.assertTrue(old)
        self.assertFalse(new)

    def test_callback_produced_calls_wait_for_the_next_tick(self):
        results = []

        def produce(result):
            results.append(result)
            self.enqueue("new-" + str(len(results)), callback=produce)

        self.enqueue("first", callback=produce)
        self.pump()
        self.assertEqual(len(results), 1)
        self.assertEqual(self.executor.execute.call_count, 1)
        self.assertEqual(len(self.controller.pending), 1)
        self.pump()
        self.assertEqual(len(results), 2)
        # Remove the producer callback before tearDown cancels its final call.
        next(iter(self.controller.pending.values())).callbacks.clear()

    def test_preparation_time_budget_yields_before_another_precondition(self):
        self.controller.state["options"]["approval"] = "ASK"
        for i in range(4):
            self.enqueue(str(i))
        clock = [time.monotonic()]

        def slow_precondition(*_):
            clock[0] += .02
            return "unchanged"

        self.executor.precondition.side_effect = slow_precondition
        with patch("angelo_agent.controller.time.monotonic", side_effect=lambda: clock[0]):
            self.pump(ToolDispatchBudget(seconds=.008, started=clock[0]))
        self.assertEqual(self.executor.precondition.call_count, 1)
        self.assertEqual(sum(call.approval for call in self.controller.pending.values()), 1)
        self.assertEqual(self.controller.tools.qsize(), 3)

    def test_pending_set_and_per_tick_admission_are_bounded(self):
        self.controller.state["options"]["approval"] = "ASK"
        for i in range(32):
            self.enqueue(str(i))
        self.pump()
        self.assertEqual(self.executor.precondition.call_count, 8)
        self.assertEqual(len(self.controller.pending), 32)
        rejected, call = self.enqueue("overflow")
        self.assertIsNone(call)
        self.assertIn("Too many pending", rejected[0]["error"])

    def test_queued_expiry_precedes_preparation_and_is_replayed_as_expired(self):
        replies, call = self.enqueue()
        call.expires = time.monotonic() - 1
        self.pump()
        self.executor.precondition.assert_not_called()
        self.executor.execute.assert_not_called()
        self.assertTrue(replies[0]["expired"])
        replay, _ = self.enqueue()
        self.assertTrue(replay[0]["expired"])
        self.assertTrue(replay[0]["replayed"])
        self.controller.pump_events()
        message = next(m for m in self.controller.state["messages"] if m.get("call_id") == call.id)
        self.assertEqual(message["status"], "failed")
        self.assertIn("expired", message["result"]["error"])

    def test_readonly_expiry_does_not_execute_or_claim_changes(self):
        replies, call = self.enqueue(name="angelo_inspect_scene", arguments={})
        call.expires = time.monotonic() - 1
        self.pump()
        self.executor.execute.assert_not_called()
        self.assertTrue(replies[0]["expired"])
        self.assertNotIn("partial_changes_possible", replies[0])

    def test_manual_approval_expiry_precedes_revalidation(self):
        self.controller.state["options"]["approval"] = "ASK"
        replies, call = self.enqueue()
        self.pump()
        self.executor.precondition.reset_mock()
        call.expires = time.monotonic() - 1
        self.controller.execute_tool(call, self.executor, self.controller.document)
        self.executor.precondition.assert_not_called()
        self.executor.execute.assert_not_called()
        self.assertTrue(replies[0]["expired"])

    def test_pending_auto_approval_expiry_cannot_execute(self):
        self.controller.state["options"]["approval"] = "ASK"
        replies, call = self.enqueue()
        self.pump()
        call.expires = time.monotonic() - 1
        self.controller.state["options"]["approval"] = "AUTO_BOUNDED"
        self.pump()
        self.executor.execute.assert_not_called()
        self.assertTrue(replies[0]["expired"])

    def test_expiry_during_preparation_cannot_reach_automatic_execution(self):
        replies, call = self.enqueue()

        def expire(*_):
            call.expires = time.monotonic() - 1
            return "unchanged"

        self.executor.precondition.side_effect = expire
        self.pump()
        self.assertTrue(replies[0]["expired"])
        self.executor.execute.assert_not_called()

    def test_expiry_during_manual_revalidation_cannot_mutate(self):
        self.controller.state["options"]["approval"] = "ASK"
        replies, call = self.enqueue()
        self.pump()

        def expire(*_):
            call.expires = time.monotonic() - 1
            return "unchanged"

        self.executor.precondition.side_effect = expire
        self.controller.execute_tool(call, self.executor, self.controller.document)
        self.assertTrue(replies[0]["expired"])
        self.executor.execute.assert_not_called()

    def test_stop_during_precondition_cannot_reach_execution(self):
        replies, _ = self.enqueue()

        def stop(*_):
            self.controller.stop()
            return "unchanged"

        self.executor.precondition.side_effect = stop
        self.pump()
        self.executor.execute.assert_not_called()
        self.assertIn("cancelled", replies[0]["error"])

    def test_changed_document_during_precondition_cannot_reach_execution(self):
        replies, _ = self.enqueue()

        def change_document(*_):
            self.controller.set_document(("different",))
            return "unchanged"

        self.executor.precondition.side_effect = change_document
        self.pump()
        self.executor.execute.assert_not_called()
        self.assertIn("document changed", replies[0]["error"])

    def test_expired_decline_remains_an_expired_result(self):
        replies, call = self.enqueue()
        call.expires = time.monotonic() - 1
        self.controller.decline_tool(call.id)
        self.assertTrue(replies[0]["expired"])

    def test_shared_budget_yields_between_background_chats_and_is_fair(self):
        with tempfile.TemporaryDirectory() as directory:
            other = Controller(directory)
            other.busy = True
            other.set_document(self.controller.document)
            other.state["options"]["approval"] = "AUTO_BOUNDED"
            try:
                for i in range(4):
                    self.enqueue(str(i))
                    other.request_tool(str(i), "angelo_sculpt_program", program(), lambda _: None)
                # Empty sessions before/between active chats must not bias the
                # round robin toward whichever active index follows the gap.
                idle = SimpleNamespace(pump_tools=lambda *args, **kwargs: None)
                controllers = [idle] * 7 + [self.controller] + [idle] * 17 + [other]
                cursor = 0
                serviced = []

                def apply(controller, call):
                    serviced.append(controller)
                    controller.execute_tool(call, self.executor, controller.document)

                for count in range(1, 9):
                    cursor = pump_controller_tools(controllers, cursor, self.executor, self.controller.document, apply,
                                                   budget=ToolDispatchBudget(seconds=60))
                    self.assertEqual(self.executor.execute.call_count, count)
                self.assertEqual(serviced, [self.controller, other] * 4)
            finally:
                other.close()

    def test_malformed_mutating_result_keeps_snapshot_and_uncertain_changes(self):
        self.executor.execute.return_value = {"success": True, "maximum": float("nan")}
        replies, _ = self.enqueue()
        self.pump()
        result = replies[0]
        self.assertTrue(result["serialization_failed"])
        self.assertFalse(result["success"])
        self.assertTrue(result["partial_changes_possible"])
        self.assertEqual(result["request_checkpoint"], self.controller.request_checkpoint)
        self.assertIn("Do not automatically retry", result["recovery"])
        self.assertTrue(result["changes"]["uncertain"])
        self.assertEqual(result["changes"]["modified"][0]["name"], "Sphere")
        self.controller.pump_events()
        message = next(m for m in self.controller.state["messages"] if m.get("result", {}).get("changes"))
        self.assertEqual(message["result"], result)
        json.dumps(self.controller.state, allow_nan=False)

    def test_valid_recovery_and_measured_changes_survive_unrelated_bad_fields(self):
        self.controller.api_key = "private-test-token"
        self.executor.execute.return_value = {
            "error": "Read-back failed", "partial_changes_possible": True,
            "recovery": "Restore the request snapshot; private-test-token", "mutation_started": True,
            "mutation_stage": "Verify native sculpt brush", "mesh_revision_before": "b" * 64,
            "request_checkpoint": self.controller.request_checkpoint,
            "changes": {"added": [], "removed": [], "modified": [{"name": "Sphere", "fields": ["geometry"]}],
                        "max_displacement": float("inf")}, "bad": object(),
        }
        replies, _ = self.enqueue()
        self.pump()
        result = replies[0]
        self.assertEqual(result["changes"]["modified"], [{"name": "Sphere", "fields": ["geometry"]}])
        self.assertIsNone(result["changes"]["max_displacement"])
        self.assertEqual(result["mutation_stage"], "Verify native sculpt brush")
        self.assertEqual(result["request_checkpoint"], self.controller.request_checkpoint)
        self.assertIn("Restore the request snapshot", result["recovery"])
        self.assertNotIn("private-test-token", self.controller.store.path.read_text())

    def test_immutable_recovery_metadata_survives_result_encoding_failure(self):
        from types import MappingProxyType
        self.executor.execute.return_value = {
            "error": "Read-back failed", "bad": object(),
            "changes": MappingProxyType({"modified": (
                MappingProxyType({"name": "Sphere", "fields": ("geometry",)}),)}),
            "file_changes": MappingProxyType({"written": ("mesh.obj",), "deleted": ()}),
        }
        replies, _ = self.enqueue()
        self.pump()
        result = replies[0]
        self.assertTrue(result["serialization_failed"])
        self.assertTrue(result["partial_changes_possible"])
        self.assertEqual(result["execution_error"], "Read-back failed")
        self.assertEqual(result["changes"]["modified"], [{"name": "Sphere", "fields": ["geometry"]}])
        self.assertEqual(result["file_changes"]["written"], ["mesh.obj"])
        self.assertTrue(result["file_changes"]["uncertain"])

    def test_readonly_encoding_errors_never_claim_scene_edits(self):
        for identity, value in (("nan", {"result": float("nan")}), ("object", {"result": object()}),
                                ("non_object", [1, 2, 3])):
            with self.subTest(identity=identity):
                self.executor.execute.return_value = value
                replies, _ = self.enqueue(identity, "angelo_inspect_scene", {})
                self.pump()
                self.assertTrue(replies[0]["serialization_failed"])
                self.assertNotIn("partial_changes_possible", replies[0])
                self.assertNotIn("changes", replies[0])
                self.assertNotIn("request_checkpoint", replies[0])

    def test_circular_and_malformed_recovery_metadata_cannot_hide_result(self):
        circular = {}
        circular["loop"] = circular
        self.executor.execute.return_value = {
            "request_checkpoint": float("nan"), "recovery": object(), "changes": circular,
            "file_changes": {"written": ["mesh.obj", object()], "deleted": None}, "bad": 10 ** 5000,
        }
        replies, _ = self.enqueue()
        self.pump()
        self.assertEqual(replies[0]["request_checkpoint"], self.controller.request_checkpoint)
        self.assertEqual(replies[0]["file_changes"]["written"], ["mesh.obj"])
        self.assertEqual(replies[0]["file_changes"]["deleted"], [])
        self.assertTrue(replies[0]["changes"]["uncertain"])
        json.dumps(replies[0], allow_nan=False)

    def test_unexecuted_encoding_error_does_not_claim_partial_edits(self):
        replies, call = self.enqueue()
        self.controller.finish_tool(call, {"error": object()})
        self.assertTrue(replies[0]["serialization_failed"])
        self.assertNotIn("partial_changes_possible", replies[0])
        self.assertNotIn("changes", replies[0])

    def test_expired_ui_script_approval_does_not_open_review_or_request_undo(self):
        self.controller.state["options"]["approval"] = "ASK"
        replies, call = self.enqueue(name="angelo_execute_python", arguments={"description": "Fixture", "code": "result = {}"})
        self.pump()
        call.expires = time.monotonic() - 1
        bpy = SimpleNamespace(ops=SimpleNamespace(angelo=SimpleNamespace(review_tool=Mock())))
        operator_type = ui_class("ANGELO_OT_approve", {"get_controller": lambda: self.controller, "bpy": bpy})
        operator = operator_type()
        operator.call_id, operator.session_id, operator.report = call.id, "", Mock()
        context = SimpleNamespace(preferences=SimpleNamespace(edit=SimpleNamespace(use_global_undo=False)))
        self.assertEqual(operator.execute(context), {"CANCELLED"})
        self.assertTrue(replies[0]["expired"])
        bpy.ops.angelo.review_tool.assert_not_called()
        self.assertIn("expired", operator.report.call_args.args[1])

    def test_changes_ui_keeps_file_uncertainty_and_requires_journal_evidence(self):
        operator_type = ui_class("ANGELO_OT_changes", {
            "get_controller": lambda: self.controller,
            "chat_view": SimpleNamespace(tool_title=lambda _: "Write file"),
            "scene_change_report": scene_change_report, "changes_popup": changes_popup, "chat_guard": chat_guard,
            "chat_changes": chat_changes,
        })
        for journaled in (None, False, True):
            with self.subTest(journaled=journaled):
                labels = []
                layout = SimpleNamespace(label=lambda **kwargs: labels.append(kwargs["text"]), separator=lambda: None)
                layout.box = lambda: layout
                operator = operator_type()
                operator.layout = layout
                self.controller.state["messages"] = [{"role": "activity", "result": {
                    "partial_changes_possible": True, "file_changes": {"written": ["mesh.obj"], "deleted": []},
                    "journaled": journaled,
                }}]
                operator.draw(None)
                self.assertIn("Operation failed; partial changes may remain.", labels)
                self.assertIn("Written: mesh.obj", labels)
                claim = "Restoring a snapshot reverts this file; Undo doesn't."
                self.assertEqual(claim in labels, journaled is True)

    def test_changes_ui_lists_mutations_and_skips_scene_delta_observations(self):
        operator_type = ui_class("ANGELO_OT_changes", {
            "get_controller": lambda: self.controller,
            "chat_view": SimpleNamespace(tool_title=lambda message: message["name"]),
            "scene_change_report": scene_change_report, "changes_popup": changes_popup, "chat_guard": chat_guard,
            "chat_changes": chat_changes,
        })
        labels = []
        layout = SimpleNamespace(label=lambda **kwargs: labels.append(kwargs["text"]), separator=lambda: None)
        layout.box = lambda: layout
        operator = operator_type()
        operator.layout = layout
        delta_rows = [{"change": "MODIFIED", "object": {"object": "scn:1:7", "name": "Target"}, "fields": ["transform"]}]
        moved = {"added": [], "removed": [], "modified": [{"name": "Target", "fields": ["matrix"]}], "truncated": False}
        self.controller.state["messages"] = [
            {"role": "activity", "name": "angelo_scene_delta", "result": {"changes": delta_rows}},
            {"role": "activity", "name": "angelo_set_transform", "result": {"changes": moved}}]
        operator.draw(None)  # Previously raised TypeError on the delta's list-shaped "changes".
        self.assertIn("angelo_set_transform", labels)
        self.assertNotIn("angelo_scene_delta", labels)
        self.assertIn("~ Target · matrix", labels)


if __name__ == "__main__":
    unittest.main()
