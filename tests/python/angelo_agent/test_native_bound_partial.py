# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native lifecycle keeps partial verification distinct from execution success."""

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.program_checks import resolve_obligations
from angelo_agent.program_jobs import ProgramJob, PreviewStore, WriterLease
from angelo_agent.program_spec import validate_program
from angelo_agent.program_completion import capture_program
from angelo_agent.claude_outcomes import certified_completion
from test_native_program_jobs import Backend, HANDLE, program, finish


def partial_program(mode="APPLY", authored=False):
    request = program(mode)
    request["operations"].append({"kind": "node_edit", "target": HANDLE, "parameters": {
        "modifier": "Geometry", "edits": [
            {"op": "set", "node": "Math", "inputs": {"Value": 1}},
            {"op": "set", "node": "Math", "settings": {"operation": "MULTIPLY"}}]}})
    if authored:
        request["expected_checks"] = [{"kind": "transform", "target": HANDLE,
                                        "property": "location", "expected": [9, 9, 9], "tolerance": .00001}]
    return validate_program(request)


class PartialBackend(Backend):
    def apply(self, stage, operation):
        if operation["kind"] == "transform":
            return super().apply(stage, operation)
        self._record("apply")
        return {"kind": operation["kind"]}

    def validate(self, stage, check):
        if check["kind"] == "transform":
            result = super().validate(stage, check)
        else:
            self._record("check")
            result = {"passed": True, "kind": check["kind"]}
        result["status"] = "pass" if result["passed"] else "fail"
        return result


class PartialLifecycleTests(unittest.TestCase):
    def test_success_keeps_unknowns_and_never_certifies_or_summarizes_as_complete(self):
        arguments = partial_program()
        backend = PartialBackend()
        job = ProgramJob(backend, arguments, writer=WriterLease())
        finish(job)
        result = job.commit()
        self.assertEqual(result["status"], "committed")
        self.assertEqual(result["verification"]["status"], "partial")
        self.assertEqual(result["verification"]["unknowns"], job.obligations.unknowns())
        self.assertEqual(result["verification"]["known_checked"], len(job.checks))
        self.assertEqual(backend.calls.count("check"), 2 * len(job.checks))
        self.assertNotIn("check_summary", result)
        self.assertIs(capture_program(job).obligations, job.obligations)
        operation = {"tool": "angelo_scene_program", "arguments": arguments}
        self.assertIsNone(certified_completion([operation], [result], read_only=lambda tool: False))
        forged = copy.deepcopy(result)
        del forged["verification"]
        self.assertIsNone(certified_completion([operation], [forged], read_only=lambda tool: False))

    def test_authored_contradiction_still_fails_and_leaves_live_source_unchanged(self):
        backend = PartialBackend()
        before = copy.deepcopy(backend.source)
        job = ProgramJob(backend, partial_program(authored=True), writer=WriterLease())
        result = finish(job)
        self.assertFalse(result["success"])
        self.assertEqual(backend.source, before)
        self.assertNotIn("verification", result)
        self.assertIsNone(job.completion_obligations())

    def test_preview_accept_keeps_unknown_bundle_and_rechecks_every_known_check(self):
        backend, previews, writer = PartialBackend(), PreviewStore(), WriterLease()
        job = ProgramJob(backend, partial_program("PREVIEW"), owner="owner", previews=previews, writer=writer)
        preview = finish(job)
        retained = previews.get(preview["preview_id"], "owner")
        self.assertEqual(preview["verification"]["unknowns"], job.obligations.unknowns())
        accepted = ProgramJob.accept(preview["preview_id"], "owner", previews=previews, writer=writer)
        self.assertIs(accepted.obligations, retained["obligations"])
        finish(accepted)
        result = accepted.commit()
        self.assertEqual(result["verification"], preview["verification"])
        self.assertEqual(backend.calls.count("check"), 3 * len(accepted.checks))
        self.assertFalse(previews.records)

    def test_stale_and_cancelled_partial_work_cannot_be_reported_as_success(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                backend, writer = PartialBackend(), WriterLease()
                before = copy.deepcopy(backend.source)
                job = ProgramJob(backend, partial_program(), writer=writer)
                finish(job)
                if cancel:
                    result = job.cancel()
                else:
                    backend.revision += 1
                    result = job.commit()
                self.assertFalse(result["success"])
                self.assertEqual(backend.source, before)
                self.assertNotIn("verification", result)
                self.assertIsNone(writer.owner)
                self.assertIsNone(job.completion_obligations())


if __name__ == "__main__":
    unittest.main()
