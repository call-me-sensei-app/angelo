# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An internal or unexplained failure while a program is only staged is a correctable refusal, never a dead end.

Regression (parallel-edit live Opus run on e2e 45e28c579dc, sailboat_dock, 2026-10-25): a worker's
angelo_scene_program failed with error_code UNKNOWN ("Material operation did not produce its required slot") while
it was only staged. An unclassified failure holds every scene edit of the request until the next one
(request_recovery), so the worker's turn ended failed although nothing had changed. A failure while staging, before
any live write, whose live binding then reads back unchanged, is now an UNSUPPORTED refusal naming the operation,
the engine's reason, that nothing changed and what to change; the model corrects it as any refusal. A failure that
may have written (commit, a started mutation, cleanup pending, a binding that does not read back) stays
unclassified. Developed on ProgramError(UNKNOWN) in an operation; INTERNAL_ERROR, a plain KeyError, a failure in
validation, and every case that must stay unclassified are held out.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent.program_jobs import ProgramError, ProgramJob, WriterLease  # noqa: E402
from angelo_agent.refusal_codes import RefusalError  # noqa: E402
from angelo_agent.request_recovery import RecoveryBarrier  # noqa: E402
from test_native_program_jobs import Backend, HANDLE, finish, program  # noqa: E402


class Verifying(Backend):
    """The test backend with the native backend's rollback read-back (program_commit.verify_rollback)."""

    def __init__(self, raise_in="apply", error=None, readback_ok=True):
        super().__init__()
        self.raise_in, self.error, self.readback_ok, self.readbacks = raise_in, error, readback_ok, 0

    def apply(self, stage, operation):
        if self.raise_in == "apply":
            raise self.error
        return super().apply(stage, operation)

    def validate(self, stage, check):
        if self.raise_in == "validate":
            raise self.error
        return super().validate(stage, check)

    def verify_rollback(self, stage):
        self.readbacks += 1
        if not self.readback_ok:
            raise ProgramError("the bound object changed", code="STALE_REVISION")


def run(backend, checks=None):
    return finish(ProgramJob(backend, program(checks=checks), writer=WriterLease()))


CHECK = [{"kind": "transform", "target": HANDLE, "property": "location", "expected": [2, 3, 4], "tolerance": .0001}]


class StagedFailures(unittest.TestCase):
    def assert_correctable(self, result, engine_code, *, operation=True):
        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "UNSUPPORTED")
        self.assertIs(result["scene_changed"], False)
        self.assertIs(result["partial_changes_possible"], False)
        self.assertNotIn("recovery", result)
        self.assertEqual(result["refusal_details"]["engine_error"], engine_code)
        self.assertIn("Nothing was changed", result["error"])
        self.assertIn("could not be completed by Angelo's program engine", result["error"])
        if operation:
            self.assertIn("transform (operation 0 on %s)" % HANDLE, result["error"])

    def test_an_unexplained_failure_while_staging_is_a_correctable_refusal(self):
        backend = Verifying(error=ProgramError("Material operation did not produce its required slot"))
        result = run(backend)
        self.assert_correctable(result, "UNKNOWN")
        self.assertIn("did not produce its required slot", result["error"])
        self.assertEqual(backend.readbacks, 1, "proved by reading the binding back, not assumed")
        self.assertEqual(backend.commits, 0)

    def test_held_out_kinds_of_internal_failure(self):
        cases = {"internal": ProgramError("kernel invariant", code="INTERNAL_ERROR"),
                 "key_error": KeyError("material_slots"), "attribute_error": AttributeError("NoneType has no data")}
        for name, error in cases.items():
            with self.subTest(case=name):
                engine = "INTERNAL_ERROR" if name == "internal" else "UNKNOWN"
                self.assert_correctable(run(Verifying(error=error)), engine)

    def test_a_failure_while_validating_names_no_operation(self):
        result = run(Verifying(raise_in="validate", error=ProgramError("check reader broke")), checks=CHECK)
        self.assert_correctable(result, "UNKNOWN", operation=False)
        self.assertTrue(result["error"].startswith(
            "The staged program could not be completed by Angelo's program engine"))
        self.assertIn("angelo_execute_python", result["error"])

    def test_what_may_have_written_stays_unclassified(self):
        unreadable = Verifying(error=ProgramError("broke"), readback_ok=False)
        claims = ProgramError("broke")
        claims.partial_changes_possible = True
        started = Verifying(error=ProgramError("broke"))
        started.mutation_started = True
        pending = Verifying(error=ProgramError("broke"))
        pending.cleanup_pending = True
        rolled = ProgramError("broke")
        rolled.rollback_error = "the journal could not be restored"
        for name, backend in {"binding does not read back": unreadable, "error claims partial": Verifying(error=claims),
                              "live write started": started, "cleanup pending": pending,
                              "error carries a rollback error": Verifying(error=rolled)}.items():
            with self.subTest(case=name):
                result = run(backend)
                self.assertEqual(result["error_code"], "UNKNOWN")
                self.assertTrue(result["partial_changes_possible"])

    def test_a_backend_that_cannot_read_back_keeps_the_old_answer(self):
        backend = Backend()
        backend.fail = "apply"
        result = run(backend)
        self.assertEqual(result["error_code"], "UNKNOWN")
        self.assertTrue(result["partial_changes_possible"])
        self.assertNotIn("rollback_error", result)

    def test_a_classified_refusal_is_left_as_it_is(self):
        result = run(Verifying(error=RefusalError("bad slot", code="VALIDATION")))
        self.assertEqual((result["error_code"], result["error"]), ("VALIDATION", "bad slot"))

    def test_request_recovery_holds_nothing_after_it(self):
        result = run(Verifying(error=ProgramError("Material operation did not produce its required slot")))
        barrier = RecoveryBarrier()
        arguments = program()
        barrier.refused("angelo_scene_program", arguments, result)
        self.assertIsNone(barrier.pending, "the model corrects it as any refusal; no hold")
        old = dict(result, error_code="UNKNOWN", partial_changes_possible=True)
        old.pop("scene_changed")
        barrier.refused("angelo_scene_program", arguments, old)
        self.assertIsNotNone(barrier.pending, "the old answer held the request")


if __name__ == "__main__":
    unittest.main()
