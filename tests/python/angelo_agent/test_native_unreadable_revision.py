# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A binding refused because its object has no exact revision is classified and holds nothing (no Blender).

Found by a face and hair one-shot: after angelo_prepare_sculpt added Multires, a program
that set the head's material was refused "Exact program revision unavailable:
multires_displacement_requires_exact_sculpt_observation" as UNKNOWN, the recovery barrier held every further
edit and the one-shot ended unrendered. The same refusal meets any object whose data the fingerprints do not
read (unfingerprinted curves, custom bevel profiles, bound deform caches) or that is over the read budget.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.refusal_codes import CORRECTABLE_CODES, error_result  # noqa: E402
from angelo_agent.request_recovery import RecoveryBarrier  # noqa: E402
from angelo_agent.scene_read_values import SceneQueryError, revision_omission, unreadable_revision  # noqa: E402

PROGRAM = {"mode": "APPLY", "operations": [{"kind": "material_principled", "target": "scn:" + "a" * 32 + ":4",
                                            "parameters": {"name": "Skin"}}]}


class UnreadableRevisionTests(unittest.TestCase):
    def test_it_is_classified_and_holds_nothing(self):
        for codes, code in ((["multires_displacement_requires_exact_sculpt_observation"], "UNSUPPORTED"),
                            (["unfingerprinted_data:CURVE", "bevel_custom_profile"], "UNSUPPORTED"),
                            (["original_state_work_budget"], "LIMIT_EXCEEDED")):
            with self.subTest(codes=codes):
                error = unreadable_revision(codes)
                self.assertIsInstance(error, SceneQueryError)
                result = error_result(error)
                self.assertEqual(result["error_code"], code)
                self.assertIn(code, CORRECTABLE_CODES)
                self.assertNotIn("recovery_required", RecoveryBarrier().refused("angelo_scene_program", PROGRAM, result))

    def test_its_codes_still_read_back(self):
        """The turn context and post-commit read-backs read the closed codes from the text, as before."""
        self.assertEqual(revision_omission(unreadable_revision(["unfingerprinted_data:CURVE"])),
                         {"reason": "UNSUPPORTED", "detail": "unfingerprinted_data:CURVE"})
        self.assertEqual(revision_omission(unreadable_revision(["original_state_work_budget"]))["reason"], "BUDGET")

    def test_an_unclassified_failure_still_holds(self):
        result = error_result(SceneQueryError("Exact program revision unavailable: x"))
        self.assertEqual(result["error_code"], "UNKNOWN")
        self.assertIs(RecoveryBarrier().refused("angelo_scene_program", PROGRAM, result).get("recovery_required"), True)

    def test_the_native_preparation_path_keeps_the_code(self):
        """Commits through _angelo_undo shape errors without exception callbacks;
        the classified refusal must not come back UNKNOWN there."""
        from angelo_agent.program_job_state import native_preparation_error_result
        result = native_preparation_error_result(unreadable_revision(["bevel_custom_profile"]), scene_changed=False)
        self.assertEqual(result["error_code"], "UNSUPPORTED")
        self.assertNotIn("recovery_required", RecoveryBarrier().refused("angelo_scene_program", PROGRAM, result))
        self.assertEqual(native_preparation_error_result(SceneQueryError("x"))["error_code"], "UNKNOWN")

    def test_unreadable_at_commit_is_a_changed_target(self):
        """Exact when bound and unreadable at commit: the object changed since (STALE_REVISION naming it)."""
        from angelo_agent.program_job_state import ProgramError, bound_revision
        handle = "scn:" + "a" * 32 + ":4"

        def unreadable():
            raise unreadable_revision(["multires_displacement_requires_exact_sculpt_observation"])
        with self.assertRaises(ProgramError) as caught:
            bound_revision(unreadable, handle)
        self.assertEqual(error_result(caught.exception)["error_code"], "STALE_REVISION")
        self.assertEqual(caught.exception.details, {"handles": [handle]})
        self.assertEqual(bound_revision(lambda: "r1", handle), "r1")

        def unclassified():
            raise SceneQueryError("Document IDs were replaced during observation; search again")
        with self.assertRaises(SceneQueryError):   # Anything else keeps its own code.
            bound_revision(unclassified, handle)

    def test_every_budget_code_is_a_limit(self):
        for codes in (["curve_data_budget"], ["bevel_custom_profile", "original_state_work_budget"]):
            self.assertEqual(error_result(unreadable_revision(codes))["error_code"], "LIMIT_EXCEEDED")


if __name__ == "__main__":
    unittest.main()
