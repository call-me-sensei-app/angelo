# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""sculpt.py's refusals before an edit, without Blender (bpy and mathutils stand-ins only for the import).

Every SculptPreflightError is a classified refusal, and the approval binding (sculpt.precondition) turns whatever
it meets into one that says nothing ran: a stale surface STALE_REVISION, anything else VALIDATION, a refusal with
its own code (a stale or deleted handle) kept. test_blender_sculpt_handles proves the same in the app.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.refusal_codes import CORRECTABLE_CODES, RefusalError, error_result  # noqa: E402
from angelo_agent.request_recovery import RecoveryBarrier  # noqa: E402

STUBS = {name: MagicMock() for name in ("bpy", "mathutils", "mathutils.bvhtree")}


def load():
    with patch.dict(sys.modules, STUBS):
        sys.modules.pop("angelo_agent.sculpt", None)
        import angelo_agent.sculpt as sculpt
    sys.modules.pop("angelo_agent.sculpt", None)   # Never left behind with stand-in Blender modules.
    return sculpt


ARGS = {"object": "Head", "mesh_revision": "a" * 64}


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.sculpt = load()

    def bind(self, **failures):
        sculpt = self.sculpt
        with patch.object(sculpt, "target", **failures.get("target", {"return_value": MagicMock()})), \
                patch.object(sculpt, "snapshot", return_value={"revision": failures.get("revision", "a" * 64)}), \
                patch.object(sculpt, "check_observation_scope"):
            return sculpt.precondition(MagicMock(), "angelo_sculpt_batch", ARGS)

    def test_every_preflight_refusal_is_classified(self):
        error = self.sculpt.SculptPreflightError("Prepare this object for sculpting first")
        self.assertIn(error.refusal_code, CORRECTABLE_CODES)
        self.assertFalse(error.partial_changes_possible)
        self.assertEqual(self.sculpt.SculptPreflightError("x", code="STALE_REVISION").refusal_code, "STALE_REVISION")

    def test_the_binding_says_what_was_refused_and_that_nothing_ran(self):
        cases = {"an object that is not a mesh": ({"target": {"side_effect": RuntimeError("Choose an existing "
                                                                                           "polygon mesh")}}, "VALIDATION"),
                 "a stale surface": ({"revision": "b" * 64}, "STALE_REVISION"),
                 "a deleted handle": ({"target": {"side_effect": RefusalError("deleted", code="STALE_CONTEXT")}},
                                      "STALE_CONTEXT")}
        for name, (failures, code) in cases.items():
            with self.subTest(name), self.assertRaises(Exception) as caught:
                self.bind(**failures)
            self.assertEqual(error_result(caught.exception)["error_code"], code)
            self.assertIs(caught.exception.partial_changes_possible, False)

    def test_a_refused_binding_holds_nothing(self):
        """The controller's result for a binding refused before approval: the request's other edits go on."""
        for failures in ({"target": {"side_effect": RuntimeError("Choose an existing polygon mesh")}},
                         {"revision": "b" * 64}):
            with self.subTest(failures=failures), self.assertRaises(Exception) as caught:
                self.bind(**failures)
            barrier = RecoveryBarrier()
            delivered = barrier.refused("angelo_sculpt_batch", ARGS,
                                        error_result(caught.exception, scene_changed=False, partial_changes_possible=False))
            self.assertNotIn("recovery_required", delivered)
            self.assertFalse(barrier.check("angelo_sculpt_batch", ARGS))

    def test_an_unclassified_failure_still_holds(self):
        """Why the classification matters: an UNKNOWN refusal can never say it changed nothing."""
        barrier = RecoveryBarrier()
        delivered = barrier.refused("angelo_sculpt_batch", ARGS,
                                    error_result(RuntimeError("x"), scene_changed=False, partial_changes_possible=False))
        self.assertIs(delivered["recovery_required"], True)

    def test_a_fault_is_named_and_still_holds_and_the_environment_is_unsupported(self):
        """Only a check's refusal is the model's to correct. A fault (any other
        exception) says nothing ran but stays unclassified, named by its type, so the request waits for a look."""
        for raised, code in ((AttributeError("'NoneType' object has no attribute 'vertices'"), "INTERNAL_ERROR"),
                             (MemoryError(), "INTERNAL_ERROR"), (KeyError("object"), "INTERNAL_ERROR"),
                             (ValueError("bad radius"), "VALIDATION")):
            with self.subTest(raised=type(raised).__name__), self.assertRaises(Exception) as caught:
                self.bind(target={"side_effect": raised})
            result = error_result(caught.exception)
            self.assertEqual(result["error_code"], code)
            self.assertTrue(result["error"])
            self.assertIs(caught.exception.partial_changes_possible, False)
            held = RecoveryBarrier().refused("angelo_sculpt_batch", ARGS, result)
            self.assertIs("recovery_required" in held, code == "INTERNAL_ERROR")
        self.assertIn(type(MemoryError()).__name__, error_result(self._caught(MemoryError()))["error"])
        source = (Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent/sculpt.py").read_text()
        for message in ("Enable Global Undo", "compiled Angelo sculpt engine", "lacks OpenSubdiv", "Restart the rebuilt"):
            line = next(line for line in source.splitlines() if message in line)
            self.assertIn("SculptPreflightError", line)
        self.assertEqual(source.count('code="UNSUPPORTED"'), 4)

    def _caught(self, raised):
        try:
            self.bind(target={"side_effect": raised})
        except Exception as error:  # noqa: BLE001
            return error
        self.fail("not refused")


class ApprovalLabelTests(unittest.TestCase):
    def test_a_sculpt_edit_by_handle_is_labelled_by_its_name(self):
        """An edit by handle shows the object's name on its card."""
        from angelo_agent.agent_target_labels import argument_handles
        handle = "scn:" + "a" * 32 + ":7"
        self.assertEqual(argument_handles({"object": handle}), {handle})
        self.assertEqual(argument_handles({"object": "Head"}), set())
        with patch.dict(sys.modules, STUBS):
            from angelo_agent.approvals import approval_message
            message = approval_message("angelo_prepare_sculpt", {"object": handle, "add_levels": 0},
                                       target_labels={handle: "Head"})
        self.assertIn("Head", message["title"])
        self.assertNotIn(handle, message["title"])


if __name__ == "__main__":
    unittest.main()
