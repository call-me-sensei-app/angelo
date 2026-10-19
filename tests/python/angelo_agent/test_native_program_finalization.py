# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Do not publish commit success before its native Undo outcome is known."""

import unittest
from unittest.mock import patch

from agent_runtime_fixtures import AgentRuntimeFixture, program
from angelo_agent.program_finalization import after_native_undo
from angelo_agent.refusal_codes import RefusalError
from test_native_refusal_recovery import B, TOOL, failure, snapshot


class ResultTests(unittest.TestCase):
    def test_missing_or_failed_outcome_never_claims_undo_or_rollback(self):
        committed = {"status": "committed", "success": True, "scene_changed": True, "undoable": True}
        for outcome in (None, {}, {"undo_published": False}, {"undo_published": True, "error": "contradictory"}):
            result = after_native_undo(committed, outcome, "checkpoint")
            self.assertFalse(result["success"])
            self.assertFalse(result["undoable"])
            self.assertFalse(result["undo_published"])
            self.assertEqual(result["error_code"], "INTERNAL_ERROR")
            self.assertTrue(result["scene_changed"])
            self.assertTrue(result["partial_changes_possible"])
            self.assertNotIn("rolled_back", result)
            self.assertEqual(result["request_checkpoint"], "checkpoint")

    def test_no_write_refusal_does_not_require_an_undo_step(self):
        result = after_native_undo(failure(), {"undo_published": False})
        self.assertEqual(result["error_code"], "STALE_REVISION")
        self.assertFalse(result["scene_changed"])
        self.assertFalse(result["partial_changes_possible"])

    def test_a_partial_failure_retains_its_original_error_and_uncertainty(self):
        result = after_native_undo({"error": "Commit readback failed", "error_code": "CHECK_FAILED",
                                    "partial_changes_possible": True},
                                   {"undo_published": False, "error_code": "NATIVE_CAPTURE", "error": "allocation failed"})
        self.assertEqual(result["commit_error"], "Commit readback failed")
        self.assertEqual(result["native_undo_error_code"], "NATIVE_CAPTURE")
        self.assertNotIn("scene_changed", result)


class ControllerTests(AgentRuntimeFixture, unittest.TestCase):
    def commit_deferred(self):
        answers, call = self.enqueue()
        job = self.ready()
        raw = self.c.commit_native_job(call, self.executor, self.c.document, defer_finish=True)
        self.assertEqual(raw["status"], "committed")
        self.assertEqual(job.backend.commits, 1)
        self.assertEqual(answers, [])
        self.assertIn(call.id, self.c.pending)
        self.assertTrue(call.committing)
        return answers, call, job

    def test_success_is_published_once_after_native_outcome(self):
        answers, call, job = self.commit_deferred()
        result = self.c.finish_native_commit(call, {"undo_published": True})
        self.assertEqual(result["status"], "committed")
        self.assertTrue(result["undoable"])
        self.assertTrue(result["undo_published"])
        self.assertEqual(len(answers), 1)
        self.assertNotIn(call.id, self.c.pending)
        self.assertFalse(call.committing)
        self.assertIsNone(call.commit_result)
        repeated = self.c.finish_native_commit(call, {"undo_published": False})
        self.assertEqual(repeated["status"], "committed")
        self.assertEqual(len(answers), 1)

    def test_final_editor_failure_preserves_publication_without_certifying_callbacks(self):
        answers, call, job = self.commit_deferred()
        result = self.c.finish_native_commit(call, {
            "undo_published": True, "editor_finalization_error": "callback owner mismatch"})
        self.assertTrue(result["undo_published"])
        self.assertFalse(result["success"])
        self.assertNotIn("undoable", result)
        self.assertTrue(result["partial_changes_possible"])
        self.assertEqual(job.backend.source, [1, 2, 3])
        self.assertEqual(len(answers), 1)

    def test_unknown_publication_stays_unknown_and_never_claims_unchanged(self):
        answers, call, job = self.commit_deferred()
        result = self.c.finish_native_commit(call, {"undo_published": None,
                                                  "finalization_transport_error": "missing native receipt"})
        self.assertIsNone(result["undo_published"])
        self.assertFalse(result["success"])
        self.assertIn("could not be verified", result["error"])
        self.assertEqual(job.backend.source, [1, 2, 3])
        self.assertEqual(len(answers), 1)

    def test_postcapture_failure_keeps_the_actual_edit_and_reports_recovery(self):
        answers, call, job = self.commit_deferred()
        self.c.request_checkpoint = "restore-point"
        result = self.c.finish_native_commit(call, {"undo_published": False, "error": "native capture failed"})
        self.assertEqual(job.backend.source, [1, 2, 3])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "INTERNAL_ERROR")
        self.assertFalse(result["undoable"])
        self.assertEqual(result["request_checkpoint"], "restore-point")
        self.assertEqual(len(answers), 1)
        self.assertIsNotNone(self.c.recovery_barrier.pending)

    def test_stop_at_publication_boundary_does_not_relabel_the_completed_edit(self):
        answers, call, job = self.commit_deferred()
        self.c.stop()
        self.assertEqual(answers, [])
        self.assertIn(call.id, self.c.pending)
        result = self.c.finish_native_commit(call, {"undo_published": True})
        self.assertEqual(result["status"], "committed")
        self.assertEqual(job.backend.source, [1, 2, 3])
        self.assertEqual(len(answers), 1)

    def test_unexpected_outer_commit_fault_stays_private_and_honestly_partial(self):
        for started in (False, True):
            with self.subTest(mutation_started=started):
                answers, call = self.enqueue(identity="outer-fault-" + str(started))
                job = self.ready()
                def unexpected(*args, **kwargs):
                    call.mutation_started = started
                    if started:
                        job.backend.source = [7, 8, 9]
                    raise RuntimeError("unexpected outer commit fault")
                with patch.object(self.c, "_commit_native_job", side_effect=unexpected):
                    raw = self.c.commit_native_job(call, self.executor, self.c.document, defer_finish=True)
                self.assertEqual(answers, [])
                self.assertTrue(call.committing)
                self.assertTrue(raw["partial_changes_possible"])
                self.assertNotIn("scene_changed", raw)
                self.c.finish_native_commit(call, {"undo_published": False, "error": "capture failed"})
                self.assertEqual(len(answers), 1)
                self.assertFalse(answers[0]["undoable"])
                if started:
                    self.assertEqual(job.backend.source, [7, 8, 9])
                self.c.recovery_barrier.clear()  # Independent invocation fixture, not a retry policy.

    def test_restaging_native_cleanup_failure_does_not_restart_work(self):
        for index, outcome in enumerate((None, {"undo_published": True},
                                         {"undo_published": False, "error": "cleanup failed"},
                                         {"undo_published": False, "error_code": "INTERNAL_ERROR"})):
            with self.subTest(outcome=outcome):
                answers, call = self.enqueue(identity="restaging-fault-" + str(index))
                self.ready()
                call.commit_result = {"restaging": True, "final": False, "scene_changed": False}
                result = self.c.finish_native_commit(call, outcome)
                self.assertEqual(result["error_code"], "INTERNAL_ERROR")
                self.assertTrue(result["partial_changes_possible"])
                self.assertNotIn(call.id, self.c.pending)
                self.assertEqual(len(answers), 1)
                self.c.recovery_barrier.clear()  # Independent invocation fixture, not a retry policy.

    def test_a_hold_is_not_closed_by_a_commit_and_a_failed_publication_opens_one(self):
        # Holds close only on reads of their targets (request_recovery); a commit, even one deferred to its
        # Undo publication, neither closes one nor needs a citation.
        self.c.recovery_barrier.observe(snapshot())
        self.c.recovery_barrier.refused(TOOL, program(), failure())  # Changed nothing: no hold.
        answers = []
        self.c.request_tool("deferred", TOOL, program(), answers.append)
        call = next(iter(self.c.pending.values()))
        self.pump()
        self.ready()
        self.c.commit_native_job(call, self.executor, self.c.document, defer_finish=True)
        self.assertEqual(answers, [])
        result = self.c.finish_native_commit(call, {"undo_published": False, "error_code": "INTERNAL_ERROR"})
        self.assertEqual((result["error_code"], result["partial_changes_possible"]), ("INTERNAL_ERROR", True))
        self.assertIs(answers[0]["recovery_required"], True)
        with self.assertRaises(RefusalError):  # Applied edits may remain: held until the next request.
            self.c.recovery_barrier.check(TOOL, program())
        self.c.recovery_barrier.read("angelo_scene_object", snapshot(B), {"object": program()["write_set"][0]["handle"]})
        with self.assertRaises(RefusalError):  # An unclassified failure: no read can explain it.
            self.c.recovery_barrier.check(TOOL, program())


if __name__ == "__main__":
    unittest.main()
