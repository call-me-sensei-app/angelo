# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A program refused at a bound says which operation failed and what works instead; nothing commits.

Regression: a 20-operation program's material on a valley's 5,121 upward faces met "Rule-based face selection
exceeds 4096 elements", a bound meant for explicit index lists, and the result said neither which operation failed
nor what would work. Rule-based selectors now match up to the mesh limit (test_blender_rc10_gaps); these tests
pin what a failed program's result adds (``failure_facts``) on the host's real ProgramJob over the shared fake
backend. No Blender.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import generic_settings  # noqa: E402
from angelo_agent.program_job_state import LIMIT_INSTEAD  # noqa: E402
from angelo_agent.program_jobs import ProgramError, ProgramJob, PreviewStore, WriterLease  # noqa: E402
from angelo_agent.program_spec import validate_program  # noqa: E402
from angelo_agent.refusal_codes import RefusalError  # noqa: E402
from test_native_program_jobs import HANDLE, Backend, finish  # noqa: E402


def program(count):
    return validate_program({"mode": "APPLY", "read_set": [], "write_set": [{"handle": HANDLE, "revision": "a" * 64}],
                             "operations": [{"kind": "transform", "target": HANDLE,
                                             "parameters": {"location": [i, 0, 0]}} for i in range(count)],
                             "expected_checks": []})


class LimitedBackend(Backend):
    """Refuses its ``limit_at``-th operation as the selector did."""

    def __init__(self, limit_at, code="LIMIT_EXCEEDED"):
        super().__init__()
        self.limit_at, self.code, self.applied = limit_at, code, 0

    def apply(self, stage, operation):
        if self.applied == self.limit_at:
            raise ProgramError("The rule-based face selector matched more than 200,000 faces", code=self.code)
        self.applied += 1
        return super().apply(stage, operation)


class FailureFactTests(unittest.TestCase):
    def run_job(self, backend, count=5):
        job = ProgramJob(backend, program(count), owner="chat-a", writer=WriterLease(), previews=PreviewStore())
        return finish(job)

    def test_a_bound_names_the_failed_operation_and_what_works_and_commits_nothing(self):
        backend = LimitedBackend(limit_at=3)
        result = self.run_job(backend)
        self.assertEqual((result["status"], result["error_code"], result["scene_changed"]),
                         ("failed", "LIMIT_EXCEEDED", False))
        self.assertEqual(result["failed_operation"], {"index": 3, "kind": "transform"})
        self.assertEqual(result["instead"], LIMIT_INSTEAD)
        self.assertIn("angelo_execute_python", result["instead"])
        self.assertEqual(len(result["operation_results"]), 3, "The operations before it staged, and nothing committed")
        self.assertEqual(backend.commits, 0)
        self.assertEqual(backend.source[HANDLE]["location"], [0, 0, 0], "The program is refused whole")

    def test_another_failure_names_its_operation_without_a_route(self):
        result = self.run_job(LimitedBackend(limit_at=0, code="VALIDATION"))
        self.assertEqual(result["failed_operation"], {"index": 0, "kind": "transform"})
        self.assertNotIn("instead", result)

    def test_a_failure_between_operations_names_none(self):
        """Regression: a context change found at the start of a poll was named as the next operation's."""
        backend = Backend()
        job = ProgramJob(backend, program(4), owner="chat-a", writer=WriterLease(), previews=PreviewStore())
        for _ in range(50):
            job.poll()
            if len(job.operation_results) >= 2:
                break
        self.assertGreaterEqual(len(job.operation_results), 2)
        backend.epoch = 2  # The next poll's check_context refuses: STALE_CONTEXT, with no operation in flight.
        result = finish(job)
        self.assertEqual((result["status"], result["error_code"]), ("failed", "STALE_CONTEXT"))
        self.assertNotIn("failed_operation", result)

    def test_a_failure_outside_the_operations_names_none(self):
        backend = Backend()
        backend.fail = "bind"
        result = self.run_job(backend)
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("failed_operation", result)

    def test_too_many_settings_says_how_to_split_them(self):
        settings = {"setting_%d" % index: index for index in range(generic_settings.MAX_SETTINGS + 1)}
        with self.assertRaises(RefusalError) as caught:
            generic_settings.apply(None, None, None, settings, "MODIFIER")
        self.assertEqual(caught.exception.code, "LIMIT_EXCEEDED")
        self.assertEqual(str(caught.exception), "%d settings in one operation; at most %d: split them across "
                                                "operations on the same target"
                                                % (len(settings), generic_settings.MAX_SETTINGS))


if __name__ == "__main__":
    unittest.main()
