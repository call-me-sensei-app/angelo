# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Opaque typed effects remain explicit partial evidence, never complete authority.

Real pure compilation, ProgramJob lifecycle, controller delivery and presentation;
recording backends do not prove Blender RNA semantics or authorize callbacks.
"""

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import approval_review, approvals, codex_plan, program_completion
from angelo_agent.claude_outcomes import (certified_completion, compact_receipt, present_plan_checks,
                                          present_program_checks, terse_receipt)
from angelo_agent.controller import Controller
from angelo_agent.program_checks import resolve_obligations
from angelo_agent.program_jobs import PreviewStore, ProgramJob, WriterLease
from angelo_agent.providers import ClaudeProvider, CodexProvider, tool_content
from angelo_agent.routing import RuntimeContext, handoff
from test_native_bound_approval_review import call_for
from test_native_complete_checks import RecordingBackend, settle
from test_native_material_obligations import A, cube, op, program


def partial_request(mode="APPLY", *, large=True):
    authored = {"kind": "node_graph", "target": A, "modifier": "Geometry",
                "nodes": [{"name": "Math", "inputs": {"Value": 1}}]}
    operations = [cube("cube%d" % index) for index in range(11)] if large else []
    operations.append(op("node_edit", A, modifier="Geometry", edits=[
        {"op": "set", "node": "Other", "inputs": {"Scale": 2}},
        {"op": "set", "node": "Math", "inputs": {"Value": 1}},
        {"op": "set", "node": "Math", "settings": {"operation": "MULTIPLY"}}]))
    return dict(program(operations, [A], [authored]), mode=mode)


class PartialCompletionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.host = Controller(self.directory.name)
        self.host.set_document(("partial-fixture", 1))
        self.host._begin_metrics()
        self.host.busy = True
        self.context = self.host.runtime_context()
        self.previews, self.writer = PreviewStore(), WriterLease()

    def tearDown(self):
        self.previews.clear()
        self.host.close()
        self.directory.cleanup()

    def stage(self, request=None):
        request = request or partial_request()
        backend = RecordingBackend()
        backend.fail_target = "$never"
        job = ProgramJob(backend, request, owner="partial", previews=self.previews, writer=self.writer)
        result = settle(job)
        if request["mode"] == "APPLY":
            self.assertTrue(result["ready_to_commit"])
            result = job.commit()
        self.assertTrue(result["success"])
        self.assertTrue(job.obligations.unknowns())
        return job, result

    def deliver(self, job, result, *, tool="angelo_scene_program", arguments=None, local=False):
        arguments = arguments or job.program
        replies = []
        self.context.request_tool(job.id, tool, arguments, replies.append)
        call = next(iter(self.host.pending.values()))
        call.local = local
        self.host.native_jobs[call.id] = job
        self.host.finish_tool(call, result)
        return {"tool": tool, "arguments": arguments}, replies[0], program_completion.completions_for(self.context, replies)[0]

    def assert_uncertified(self, operation, result, completion):
        self.assertIsNone(certified_completion([operation], [result], read_only=lambda _: False,
                                              completions=[completion]))

    def test_partial_execution_checks_known_and_authored_but_cannot_certify_or_collapse(self):
        job, result = self.stage()
        self.assertGreater(len(result["checks"]), 32)
        self.assertNotIn("check_summary", result)
        self.assertEqual(result["verification"], {"status": "partial", "unknowns": job.obligations.unknowns(),
                         "known_required": len(job.checks), "known_checked": len(job.check_results)})
        authored = job.program["expected_checks"][0]
        self.assertIn(authored, job.backend.checked)
        self.assertGreaterEqual(len(job.backend.checked), 2 * len(job.checks))
        self.assertTrue(any(node.get("inputs") == {"Scale": 2} for check in job.checks
                            for node in check.get("nodes", ())))
        operation, result, completion = self.deliver(job, result)
        self.assertIsNotNone(completion, "The receipt records an actual partial execution, never complete proof")
        self.assert_uncertified(operation, result, completion)
        self.assertIsNone(certified_completion([operation], [result], read_only=lambda _: False))
        before = json.dumps(result)
        self.assertIs(present_program_checks(operation["tool"], result, completion=completion), result)
        self.assertIsNone(compact_receipt(operation["tool"], result))
        self.assertIsNone(terse_receipt(operation["tool"], result))
        shown = json.loads(tool_content(self.context, operation["tool"], result)[0]["text"])
        self.assertEqual(len(shown["checks"]), len(result["checks"]))
        self.assertEqual(shown["verification"], result["verification"])
        self.assertEqual(json.dumps(result), before)

    def test_removed_or_forged_public_metadata_cannot_restore_private_complete_authority(self):
        job, result = self.stage()
        operation, result, completion = self.deliver(job, result)
        for forged in (None, {"status": "complete", "unknowns": []}):
            with self.subTest(forged=forged):
                result.pop("verification", None)
                if forged is not None:
                    result["verification"] = forged
                count = len(result["checks"])
                result["check_summary"] = {"required": count, "checked": count, "passed": count,
                                           "checks_digest": job.obligations.checks_digest}
                self.assert_uncertified(operation, result, completion)
                self.assertIsNone(certified_completion([operation], [result], read_only=lambda _: False))
                self.assertIs(present_program_checks(operation["tool"], result, completion=completion), result)
                plan = {"success": True, "operations": [result]}
                self.assertIs(present_plan_checks([operation], plan, completions=[completion]), plan)
                shown = json.loads(tool_content(self.context, operation["tool"], result)[0]["text"])
                self.assertEqual(len(shown["checks"]), count)
                self.assertNotIn("check_presentation", shown)

    def test_codex_in_turn_keeps_full_partial_evidence_without_host_summary(self):
        job, result = self.stage()
        provider = CodexProvider(self.context, {})
        provider._scope.begin(self.context, "thread", starting=False)
        provider._scope.bind_turn("turn")
        provider.rpc = SimpleNamespace(respond=Mock())
        original = self.host.request_tool
        def dispatch(*args, **kwargs):
            original(*args, **kwargs)
            call = next(iter(self.host.pending.values()))
            self.host.native_jobs[call.id] = job
            self.host.finish_tool(call, result)
        with patch.object(self.host, "request_tool", side_effect=dispatch):
            provider._request("rpc", "item/tool/call", {"threadId": "thread", "turnId": "turn",
                "callId": "partial", "tool": "angelo_scene_program", "arguments": job.program})
        provider._emit_host_summary(self.context, provider._scope.fence)
        payload = provider.rpc.respond.call_args.args[1]
        delivered = json.loads(payload["contentItems"][0]["text"])
        self.assertEqual(delivered["verification"]["status"], "partial")
        self.assertEqual(len(delivered["checks"]), len(job.checks))
        self.assertNotIn("check_presentation", delivered)
        self.host.pump_events()
        self.assertFalse(any(row.get("host_summary") for row in self.host.state["messages"]))

    def test_claude_continuation_keeps_full_partial_evidence_without_host_summary(self):
        job, result = self.stage()
        operation, result, completion = self.deliver(job, result)
        provider = ClaudeProvider(self.context, {})
        provider._native_call = Mock(return_value=result)
        provider._continuation_observation = Mock(return_value={})
        provider._turn = Mock(side_effect=[{"reply": "", "operations": [operation], "verify": True},
                                           {"reply": "The effects remain partly unverified.", "operations": [], "verify": False}])
        self.assertEqual(provider.run(None, "Change the nodes", observation={}), "completed")
        continuation = provider._turn.call_args_list[1].args[1]
        delivered = json.loads(continuation.split("\n", 1)[1])["result"]["operations"][0]
        self.assertEqual(delivered["verification"]["status"], "partial")
        self.assertEqual(len(delivered["checks"]), len(job.checks))
        self.assertNotIn("check_presentation", delivered)
        self.host.pump_events()
        self.assertFalse(any(row.get("host_summary") for row in self.host.state["messages"]))

    def test_preview_accept_keeps_partial_bundle_without_host_summary(self):
        job, preview = self.stage(partial_request("PREVIEW"))
        original_unknowns = job.obligations.unknowns()
        operation, preview, completion = self.deliver(job, preview)
        self.assert_uncertified(operation, preview, completion)
        accepted = ProgramJob.accept(preview["preview_id"], "partial", previews=self.previews, writer=self.writer)
        self.assertTrue(settle(accepted)["ready_to_commit"])
        result = accepted.commit()
        self.assertTrue(result["success"])
        self.assertEqual(result["verification"]["unknowns"], original_unknowns)
        operation, result, completion = self.deliver(accepted, result, tool="angelo_accept_preview",
            arguments={"preview_id": preview["preview_id"]}, local=True)
        self.assert_uncertified(operation, result, completion)
        self.host.pump_events()
        self.assertFalse(any(row.get("host_summary") for row in self.host.state["messages"]))
        text, _ = handoff(self.host.state["messages"])
        self.assertIn("partial", text)
        self.assertIn("opaque_node_settings", text)
        self.assertNotIn('"receipt"', text)

    def test_cancelled_and_stale_partial_outcomes_never_supply_complete_authority(self):
        job, result = self.stage()
        replies = []
        self.context.request_tool("stale", "angelo_scene_program", job.program, replies.append)
        call = next(iter(self.host.pending.values()))
        self.host.native_jobs[call.id] = job
        self.host._begin_metrics()
        self.host.finish_tool(call, result)
        self.assertTrue(replies[0]["success"])
        self.assertEqual(program_completion.completions_for(self.context, replies), [None])
        self.context = self.host.runtime_context()
        request = partial_request()
        cancelled = ProgramJob(RecordingBackend(), request, previews=self.previews, writer=self.writer)
        result = cancelled.cancel()
        operation, result, completion = self.deliver(cancelled, result)
        self.assertTrue(result["cancelled"])
        self.assertIsNone(completion)
        self.assert_uncertified(operation, result, completion)

    def test_authored_failure_still_blocks_the_partial_program_before_commit(self):
        request = partial_request(large=False)
        backend = RecordingBackend()
        backend.fail_target = A
        job = ProgramJob(backend, request, previews=self.previews, writer=self.writer)
        result = settle(job)
        self.assertFalse(result["success"])
        self.assertEqual(backend.checked[0], request["expected_checks"][0])
        self.assertEqual(backend.commits, 0)
        operation, result, completion = self.deliver(job, result)
        self.assertIsNone(completion)
        self.assert_uncertified(operation, result, completion)

    def test_manual_approval_shows_immutable_partial_effects_and_all_known_checks(self):
        request = partial_request(large=False)
        bundle = resolve_obligations(request)
        call = call_for(request, bundle)
        before = copy.deepcopy(request)
        approval_review.prepare_review(call, call.precondition)
        shown = approval_review.review_arguments(call)
        self.assertEqual(shown["expected_checks"], bundle.checks())
        self.assertEqual(shown["unverified_effects"][0], {"step": 1, "root": {"target": A, "modifier": "Geometry"},
            "reason": "opaque node settings", "affected": bundle.unknowns()[0]["affected"]})
        self.assertIn("partial", approvals.approval_message(call.name, shown)["warning"])
        self.assertIn("Opaque node settings".lower(), approvals.approval_details(call.name, shown).lower())
        shown["unverified_effects"].clear()
        self.assertTrue(approval_review.review_arguments(call)["unverified_effects"])
        self.assertEqual(request, before)
        self.assertTrue(bundle.unknowns())

    def test_large_partial_history_prefix_preserves_status_count_and_first_reason(self):
        job, result = self.stage()
        operation, result, completion = self.deliver(job, result)
        # Historical evidence may be longer than the established 5,000-character excerpt.
        unknowns = copy.deepcopy(result["verification"]["unknowns"]) * 3
        unknowns[0]["affected"] = [{"nodes": [{"name": "Math", "inputs": {"Value": "x" * 6000}}]}]
        result["verification"]["unknowns"] = unknowns
        result["verification"]["unknown_count"] = 999  # A historical display count never overrides the actual list.
        before = copy.deepcopy(result)
        messages = [{"role": "activity", "status": "completed", "call_id": "partial", "tool": operation["tool"],
                     "result": result}]
        text, _ = handoff(messages)
        entry = json.loads(text.split(":\n", 1)[1])
        excerpt = entry["result"]
        self.assertEqual(len(excerpt), 5000)
        for text in ('"status": "partial"', '"unknown_count": 3', '"excerpt": true',
                     '"operation": 11', '"modifier": "Geometry"', '"reason": "opaque_node_settings"'):
            self.assertIn(text, excerpt)
        self.assertEqual(result, before)
        self.assertEqual(len(result["verification"]["unknowns"]), 3)
        failing = {"tool": "angelo_scene_program", "arguments": job.program}
        correction = codex_plan.continuation_prompt(self.context, [operation, failing],
            [result, {"error": "Stopped", "scene_changed": False}], None)
        self.assertIn('"verification":{"status":"partial"', correction)
        self.assertIn('"opaque_node_settings"', correction)
        self.assertIn('"checks":', correction)


if __name__ == "__main__":
    unittest.main()
