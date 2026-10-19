# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fused polls share one hydration across bounded units; no bpy needed."""

import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent.program_jobs import (MAX_AMORTIZED_POLL_SECONDS, MAX_FUSED_POLL_SECONDS, ProgramJob, PreviewStore,
                                       WriterLease, fused_work_budget)
from test_native_program_jobs import Backend, HANDLE, finish, program

CHECK = {"kind": "transform", "target": HANDLE, "property": "location", "expected": [2, 3, 4], "tolerance": .00001}


class FusedBackend(Backend):
    """The native protocol: prepared stages, coalesced quanta, fusion and residency."""

    prepared_stage_ready = coalesce_operations = fuse_phases = True

    def __init__(self, costs=None, residency=1.):
        super().__init__()
        self.now, self.costs, self.residency = 0., dict(costs or {}), residency
        self.resident, self.hydrations, self.suspensions = False, 0, 0

    def operation_quantum_clock(self):
        return self.now

    def _record(self, name):
        super()._record(name)
        self.now += self.costs.get(name, 0.)

    def create_stage(self, binding):
        stage = super().create_stage(binding)
        self.copy_object(stage, HANDLE)
        self.finish_stage(stage)
        self.resident = True
        return stage

    def resume_stage(self, stage):
        if "suspended" not in stage:
            return stage
        self.now += self.residency  # Hydration never spends the work budget (fused_work_budget).
        self.hydrations += 1
        self.resident = True
        return copy.deepcopy(stage["suspended"])

    def suspend_stage(self, stage):
        if "suspended" in stage:
            return stage
        self.now += self.residency  # Nor does serialization.
        self.suspensions += 1
        self.resident = False
        return {"suspended": copy.deepcopy(stage)}

    def discard(self, stage):
        self.resident = False
        super().discard(stage)


class FusedPollTests(unittest.TestCase):
    def setUp(self):
        self.backend, self.writer, self.previews = FusedBackend(), WriterLease(), PreviewStore()

    def job(self, mode="APPLY", checks=None, operations=1):
        value = program(mode, checks)
        value["operations"] *= operations
        return ProgramJob(self.backend, value, owner="chat-a", writer=self.writer, previews=self.previews)

    def polls(self, job, limit=20):
        progress = []
        for _ in range(limit):
            result = job.poll()
            self.assertFalse(self.backend.resident)  # No staged ID survives any yield.
            progress.append(result["progress"]["operation_index"])
            if result.get("ready_to_commit") or result.get("final"):
                return progress
        self.fail("job did not settle")

    def test_cheap_units_share_one_poll_and_one_suspension(self):
        job = self.job(checks=[CHECK, CHECK])
        self.assertTrue(job.poll()["ready_to_commit"])
        self.assertEqual((self.backend.hydrations, self.backend.suspensions), (0, 1))
        self.assertFalse(self.backend.resident)
        self.assertEqual(self.backend.calls, ["context", "bind", "stage", "copy", "remap", "apply",
                                              "check", "check", "integrity", "delta", "verify"])
        self.assertGreater(job.verification_ms, 0)
        self.assertEqual(self.backend.source[HANDLE]["location"], [0, 0, 0])
        self.assertEqual(job.commit()["status"], "committed")
        self.assertEqual(self.backend.hydrations, 1)  # Commit still re-hydrates and re-checks.
        self.assertEqual(self.backend.source[HANDLE]["location"], [2, 3, 4])

    def test_spent_work_budget_yields_and_the_next_poll_continues(self):
        # 5 ms actions exceed the 4 ms quantum alone; four quanta spend a
        # poll's 16 ms. Cheap hydration and serialization keep that budget
        # and never count against it.
        self.backend = FusedBackend({"apply": .005}, residency=.002)
        job = self.job(operations=12)
        self.assertEqual(self.polls(job), [4, 8, 12, 12])
        self.assertEqual((self.backend.hydrations, self.backend.suspensions), (3, 4))
        self.assertEqual(self.backend.calls.count("apply"), 12)
        self.assertEqual(job.commit()["status"], "committed")

    def test_budget_is_the_polls_own_residency_cost_within_its_bounds(self):
        self.assertEqual(fused_work_budget(0.), MAX_FUSED_POLL_SECONDS)
        self.assertEqual(fused_work_budget(.004), MAX_FUSED_POLL_SECONDS)
        self.assertEqual(fused_work_budget(.03125), .03125)
        self.assertEqual(fused_work_budget(5.), MAX_AMORTIZED_POLL_SECONDS)

    def test_costly_residency_raises_the_work_budget_to_its_own_cost(self):
        # 1/128 s actions: three spend the first poll's 16 ms (it does not
        # hydrate). Hydration plus the last suspension cost 1/32 s, so each
        # later poll works that long: four actions per hydration, not three.
        self.backend = FusedBackend({"apply": 1 / 128}, residency=1 / 64)
        job = self.job(operations=12)
        self.assertEqual(self.polls(job), [3, 7, 11, 12])
        self.assertEqual((self.backend.hydrations, self.backend.suspensions), (3, 4))
        self.assertEqual(job.commit()["status"], "committed")
        self.setUp()
        self.backend = FusedBackend({"apply": 1 / 128}, residency=.001)
        self.assertEqual(self.polls(self.job(operations=12)), [3, 6, 9, 12, 12])

    def test_residency_beyond_the_bound_still_yields_after_its_bounded_work(self):
        # One-second residencies: a poll works at most 100 ms (13 actions of 1/128 s), never a second.
        self.backend = FusedBackend({"apply": 1 / 128})
        job = self.job(operations=30)
        self.assertEqual(self.polls(job), [3, 16, 29, 30])
        self.assertEqual(self.backend.calls.count("apply"), 30)
        self.assertEqual(job.commit()["status"], "committed")

    def test_native_spent_budget_clock_still_runs_one_unit_per_poll(self):
        # program_qa_helpers.spent_budget: every reading of the budget clock
        # advances past the largest budget a measured residency can set.
        backend = FusedBackend()
        backend.operation_quantum_clock = lambda: setattr(backend, "now", backend.now + 2 * MAX_AMORTIZED_POLL_SECONDS) or backend.now
        self.backend = backend
        self.assertEqual(self.polls(self.job(checks=[CHECK], operations=3)), [0, 1, 2, 3, 3, 3])

    def test_every_poll_completes_one_unit_even_when_it_alone_exceeds_the_budget(self):
        self.backend = FusedBackend({"bind": .05, "apply": .05, "check": .05}, residency=0.)
        job = self.job(checks=[CHECK], operations=3)
        # Preparation, three actions, the check (with stage validation), then read-back.
        self.assertEqual(self.polls(job), [0, 1, 2, 3, 3, 3])
        self.assertEqual(job.phase, "READY_TO_COMMIT")

    def test_verify_only_and_ready_polls_never_hydrate(self):
        for fused in (False, True):
            with self.subTest(fused=fused):
                self.setUp()
                self.backend.fuse_phases = fused
                preview = finish(self.job("PREVIEW", checks=[CHECK]))
                hydrations = self.backend.hydrations
                accepted = ProgramJob.accept(preview["preview_id"], "chat-a", previews=self.previews, writer=self.writer)
                result = accepted.poll()
                if fused:  # The exact verify and every check share one hydration.
                    self.assertTrue(result["ready_to_commit"])
                    self.assertEqual(self.backend.hydrations, hydrations + 1)
                else:
                    self.assertEqual((accepted.phase, self.backend.hydrations), ("VALIDATE", hydrations))
                    self.assertTrue(finish(accepted)["ready_to_commit"])
                hydrations = self.backend.hydrations
                for _ in range(3):
                    self.assertTrue(accepted.poll()["ready_to_commit"])
                self.assertEqual(self.backend.hydrations, hydrations)
                self.assertFalse(self.backend.resident)
                self.assertEqual(accepted.commit()["status"], "committed")
                self.assertEqual(self.backend.source[HANDLE]["location"], [2, 3, 4])

    def test_stop_between_fused_polls_preserves_the_original(self):
        for polls in range(5):
            with self.subTest(polls=polls):
                self.setUp()
                self.backend = FusedBackend({"apply": .005})
                job = self.job(operations=12)
                source = copy.deepcopy(self.backend.source)
                for _ in range(polls):
                    job.poll()
                result = job.cancel()
                self.assertTrue(result["cancelled"])
                self.assertFalse(result["scene_changed"])
                self.assertEqual(self.backend.source, source)
                self.assertFalse(self.backend.resident)
                self.assertEqual(self.backend.commits, 0)
                self.assertIsNone(self.writer.owner)

    def test_failure_inside_a_fused_poll_discards_the_resident_stage(self):
        for phase in ("apply", "check", "integrity", "delta", "verify"):
            with self.subTest(phase=phase):
                self.setUp()
                self.backend.fail = phase
                result = self.job(checks=[CHECK]).poll()
                self.assertFalse(result["success"])
                self.assertTrue(result["final"])
                self.assertEqual(result["error_code"], "UNKNOWN")
                self.assertTrue(result["partial_changes_possible"])
                self.assertNotIn("scene_changed", result)
                self.assertEqual(self.backend.discards, 1)
                self.assertFalse(self.backend.resident)
                self.assertEqual(self.backend.source[HANDLE]["location"], [0, 0, 0])
                self.assertIsNone(self.writer.owner)

    def test_failed_expected_check_ends_the_fused_poll(self):
        bad = dict(CHECK, expected=[8, 8, 8])
        job = self.job(checks=[bad, CHECK])
        result = job.poll()
        self.assertIn("Expected check failed", result["error"])
        self.assertEqual(self.backend.calls.count("check"), 1)
        self.assertNotIn("delta", self.backend.calls)
        self.assertFalse(self.backend.resident)

    def test_synchronous_stop_inside_a_unit_ends_the_poll_without_suspending(self):
        job = self.job(operations=3)
        apply = self.backend.apply

        def apply_then_stop(stage, operation):
            outcome = apply(stage, operation)
            job.cancel()
            return outcome

        self.backend.apply = apply_then_stop
        result = job.poll()
        self.assertTrue(result["cancelled"])
        self.assertEqual(self.backend.calls.count("apply"), 1)
        self.assertEqual(self.backend.suspensions, 0)
        self.assertFalse(self.backend.resident)
        self.assertIsNone(self.writer.owner)

    def test_verification_time_is_checks_stage_validation_and_read_back_only(self):
        self.backend = FusedBackend({"bind": 2., "apply": 1., "check": .25, "integrity": .125,
                                     "delta": .0625, "verify": .03125})
        job = self.job(checks=[CHECK, CHECK])
        with patch("angelo_agent.program_jobs.time.perf_counter", lambda: self.backend.now):
            self.polls(job)
            self.assertEqual(job.verification_ms, 718.75)  # Neither preparation, actions nor residency.
            self.assertEqual(job.commit()["status"], "committed")
        self.assertEqual(job.verification_ms, 718.75)  # Commit-time checks are commit time.

    def test_preview_is_retained_from_the_poll_that_reads_it_back(self):
        result = self.job("PREVIEW", checks=[CHECK]).poll()
        self.assertEqual(result["status"], "preview")
        self.assertEqual(self.backend.suspensions, 1)  # Retention suspends; the closed job does not again.
        self.assertFalse(self.backend.resident)
        self.assertEqual(len(self.previews.records), 1)
        self.assertIsNone(self.writer.owner)


if __name__ == "__main__":
    unittest.main()
