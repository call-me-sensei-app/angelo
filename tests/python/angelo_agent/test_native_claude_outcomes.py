# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Failed/inconclusive native evidence cannot become Claude completion."""

import copy
import unittest
from unittest.mock import Mock

from claude_protocol_fixtures import host
from angelo_agent.claude_outcomes import native_result_problem, runtime_result_error
from angelo_agent.protocol import RpcError
from angelo_agent.providers import ClaudeProvider


VERIFY = {"tool": "angelo_scene_verify", "arguments": {"read_set": [], "checks": [
    {"kind": "object_count", "minimum": 0, "maximum": 0}]}}
CUBE = {"tool": "angelo_create_cube", "arguments": {"name": "Cube", "size": 1, "location": [0, 0, 0]}}


class ClaudeOutcomeTests(unittest.TestCase):
    def provider(self, operations, result, *, verify=False):
        context = host()
        provider = ClaudeProvider(context, {})
        provider._turn = Mock(return_value={"reply": "Native plan", "operations": operations, "verify": verify})
        provider._native_call = Mock(return_value=result)
        return provider, context

    def test_failed_and_inconclusive_standalone_checks_fail_even_when_verify_false(self):
        for status in ("fail", "inconclusive"):
            for verify in (False, True):
                with self.subTest(status=status, verify=verify):
                    # Exact public result shape from the independent factory-scene audit.
                    result = {"success": False, "passed": False, "status": status, "checks": [
                        {"index": 0, "kind": "object_count", "status": status, "passed": False,
                         "observed": {"count": 3, "scope": "complete_bound_scene_overlay"}}],
                        "truncated": False, "read_only": True, "scope": "closed_base_data_predicates",
                        "native_exec_ms": 5.192}
                    provider, context = self.provider([VERIFY], result, verify=verify)
                    self.assertEqual(provider.run(None, "Verify empty scene", observation={}), "failed")
                    provider._native_call.assert_called_once_with(VERIFY["tool"], VERIFY["arguments"],
                        owner=provider._turn.call_args.kwargs["owner"])
                    provider._turn.assert_called_once()
                    feedback = [data["text"] for kind, data in context.events if kind == "message"]
                    self.assertTrue(any("do not prove completion" in text for text in feedback))
                    self.assertTrue(any("do not automatically retry" in text for text in feedback))
                    self.assertFalse(any("Native plan completed" in text for text in feedback))

    def test_failed_check_stops_before_any_later_mutation_in_same_plan(self):
        provider, _ = self.provider([VERIFY, CUBE], {"success": False, "passed": False, "status": "fail"})
        self.assertEqual(provider.run(None, "Verify then create", observation={}), "failed")
        provider._native_call.assert_called_once_with(VERIFY["tool"], VERIFY["arguments"],
            owner=provider._turn.call_args.kwargs["owner"])

    def test_positive_standalone_checks_can_complete_without_extra_model_turn(self):
        result = {"success": True, "passed": True, "status": "pass", "checks": [
            {"index": 0, "status": "pass", "passed": True, "observed": {"count": 0}}]}
        provider, _ = self.provider([VERIFY], result)
        self.assertEqual(provider.run(None, "Verify", observation={}), "completed")
        provider._turn.assert_called_once()

    def test_missing_or_contradictory_check_evidence_is_not_positive_verification(self):
        results = ({"success": True}, {"passed": True}, {"status": "pass"},
                   {"success": True, "passed": True, "status": "pass", "checks": [{"status": "inconclusive"}]},
                   {"success": True, "passed": True, "status": "pass", "checks": [None]})
        for result in results:
            with self.subTest(result=result):
                provider, context = self.provider([VERIFY], result)
                self.assertEqual(provider.run(None, "Verify", observation={}), "failed")
                self.assertTrue(any(kind == "message" for kind, _ in context.events))

    def test_bounded_batch_failure_and_nested_failed_outcomes_cannot_complete(self):
        for result in ({"success": False}, {"success": True, "operations": [{"success": False}]},
                       {"success": True, "checks": [{"status": "fail", "passed": False}]}):
            provider, _ = self.provider([CUBE], result)
            self.assertEqual(provider.run(None, "Create", observation={}), "failed")
            provider._native_call.assert_called_once_with("angelo_apply_batch", {"operations": [CUBE]},
                owner=provider._turn.call_args.kwargs["owner"])

    def test_existing_execution_error_remains_interrupted_not_completed(self):
        provider, context = self.provider([CUBE], {"error": "Explicit approval rejected"})
        self.assertEqual(provider.run(None, "Create", observation={}), "interrupted")
        self.assertEqual(context.events[-1], ("message", {"role": "system", "text": "Explicit approval rejected"}))

    def test_failed_initial_scene_observation_does_not_start_a_model_round(self):
        provider, _ = self.provider([], {"success": False, "status": "inconclusive"})
        with self.assertRaisesRegex(RpcError, "inconclusive"):
            provider.run(None, "Observe")
        provider._turn.assert_not_called()

    def test_generation_change_after_model_plan_prevents_native_execution(self):
        provider, context = self.provider([CUBE], {"success": True})
        def stale_plan(*_args, **_kwargs):
            context._request_generation += 1
            return {"reply": "Old plan", "operations": [CUBE], "verify": False}
        provider._turn.side_effect = stale_plan
        self.assertEqual(provider.run(None, "Original", observation={}), "interrupted")
        provider._native_call.assert_not_called()
        self.assertEqual(context.events, [])

    def test_private_or_historical_data_cannot_invent_a_failed_outcome(self):
        result = {"success": True, "memory": [{"success": False, "status": "fail"}],
                  "objects": [{"name": "failed", "status": "inconclusive"}]}
        before = copy.deepcopy(result)
        self.assertIsNone(native_result_problem(result))
        self.assertEqual(result, before)

    def test_native_progress_metadata_is_not_a_failure(self):
        self.assertIsNone(native_result_problem({"success": True, "status": "queued", "asset_id": "fixture"}))
        self.assertIsNone(native_result_problem({"success": True, "status": "preview", "scene_changed": False}))

    def test_runtime_error_precedence_includes_native_errors_not_only_result_text(self):
        self.assertEqual(runtime_result_error({"is_error": True, "errors": ["Actual native failure"],
                                              "result": "Fallback"}), "Actual native failure")
        self.assertEqual(runtime_result_error({"is_error": True, "subtype": "success", "result": "API Error"}), "API Error")
        self.assertEqual(runtime_result_error(None), "Claude Code did not complete the turn")
        self.assertIsNone(runtime_result_error({"subtype": "success", "is_error": False}))


if __name__ == "__main__":
    unittest.main()
