# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Observed live scene/read-back latency, not dispatch intent or historical replay."""

import unittest
from unittest.mock import Mock, patch

from agent_runtime_fixtures import AgentRuntimeFixture
from angelo_agent.telemetry import clean_receipt, scene_outcome_evidence

CUBE = {"name": "Cube", "size": 1, "location": [0, 0, 0]}
POSITIVE = {"success": True, "scene_changed": True, "verified": {"object_count": 1}, "changes": {
    "added": [{"name": "Cube", "type": "MESH"}], "removed": [], "modified": []}}


class OutcomeLatencyTests(AgentRuntimeFixture, unittest.TestCase):
    def invoke(self, name="angelo_create_cube", result=None, identity="current"):
        clock = [1.]
        def execute(*_):
            clock[0] += .025
            return POSITIVE if result is None else result
        self.executor.execute = Mock(side_effect=execute)
        with patch("angelo_agent.controller.time.monotonic", side_effect=lambda: clock[0]):
            self.c._begin_metrics()
            replies = []
            self.c.request_tool(identity, name, {} if name == "angelo_inspect_scene" else CUBE, replies.append)
            self.pump()
        return self.c._request_metrics.snapshot()["phases"], replies

    def test_positive_scene_change_and_native_readback_have_explicit_receipt_latencies(self):
        phases, replies = self.invoke()
        self.assertTrue(replies[0]["scene_changed"])
        self.assertAlmostEqual(phases["first_scene_change_ms"], 25)
        self.assertAlmostEqual(phases["first_verified_result_ms"], 25)
        self.assertLessEqual(phases["first_native_action_ms"], phases["first_scene_change_ms"])

    def test_readonly_observation_never_counts_as_scene_or_mutation_verification(self):
        phases, _ = self.invoke("angelo_inspect_scene")
        self.assertIn("first_native_action_ms", phases)
        self.assertNotIn("first_scene_change_ms", phases)
        self.assertNotIn("first_verified_result_ms", phases)

    def test_uncertain_failed_or_nonfinite_mutation_outputs_do_not_create_latencies(self):
        for index, result in enumerate((dict(POSITIVE, partial_changes_possible=True),
                dict(POSITIVE, error="Read-back failed"), dict(POSITIVE, success=False),
                dict(POSITIVE, invalid=float("nan")))):
            with self.subTest(result=index):
                phases, _ = self.invoke(result=result, identity="failed-" + str(index))
                self.assertNotIn("first_scene_change_ms", phases)
                self.assertNotIn("first_verified_result_ms", phases)

    def test_meaningful_native_readback_can_verify_a_noop_but_never_invent_a_scene_change(self):
        phases, _ = self.invoke(result={"success": True, "scene_changed": False, "verified": {"object_count": 1}})
        self.assertNotIn("first_scene_change_ms", phases)
        self.assertIn("first_verified_result_ms", phases)

    def test_missing_readback_is_unmeasured_and_replayed_history_never_populates_timings(self):
        phases, _ = self.invoke(result={"success": True}, identity="no-evidence")
        self.assertNotIn("first_scene_change_ms", phases)
        self.assertNotIn("first_verified_result_ms", phases)
        self.invoke(identity="old-positive")
        phases, replies = self.invoke(identity="old-positive")
        self.assertTrue(replies[0]["replayed"])
        self.executor.execute.assert_not_called()
        self.assertNotIn("first_scene_change_ms", phases)
        self.assertNotIn("first_verified_result_ms", phases)

    def test_staged_preview_discard_memory_and_export_are_not_native_scene_receipts(self):
        self.c._begin_metrics()
        replies, _ = self.enqueue(mode="PREVIEW")
        for _ in range(20):
            self.pump()
            if replies:
                break
        phases = self.c._request_metrics.snapshot()["phases"]
        self.assertEqual(replies[0]["status"], "preview")
        self.assertNotIn("first_scene_change_ms", phases)
        self.assertNotIn("first_verified_result_ms", phases)
        for name in ("angelo_memory_update", "angelo_discard_preview", "angelo_studio_export"):
            self.assertFalse(self.c.is_native_action(name))
        self.assertTrue(self.c.requires_mutation_checkpoint("angelo_studio_export"))
        self.assertFalse(self.c.requires_mutation_checkpoint("angelo_studio_generate"))

    def test_outcome_helpers_reject_malformed_progress_preview_and_uncertain_shapes(self):
        for result in (dict(POSITIVE, final=False), dict(POSITIVE, status="preview"),
                dict(POSITIVE, status={}), dict(POSITIVE, replayed=True),
                dict(POSITIVE, changes={"uncertain": True}), dict(POSITIVE, status="discarded")):
            self.assertEqual(scene_outcome_evidence(result), (False, False))
        self.assertEqual(scene_outcome_evidence({"success": True, "verified": True}), (False, False))
        self.assertEqual(scene_outcome_evidence({"success": True, "verified": {"note": "Not a read-back"}}), (False, False))
        receipt = clean_receipt({"status": "completed", "phases": {"total_ms": 40,
            "first_scene_change_ms": 25, "first_verified_result_ms": 30}, "counts": {}})
        self.assertEqual(receipt["phases"]["first_scene_change_ms"], 25)
        self.assertEqual(receipt["phases"]["first_verified_result_ms"], 30)


if __name__ == "__main__":
    unittest.main()
