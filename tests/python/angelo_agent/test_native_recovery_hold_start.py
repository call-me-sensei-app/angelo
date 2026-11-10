# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A classified refusal raised before a call's edit starts holds nothing of its request (``stale_binding``, C7b).

Recorded (C3 early town-centre diagnostic, Sol on Codex): the lead's script was refused STALE_REVISION at its run
after a merge changed the scene; the refusal did not say nothing had run, so the recovery barrier held every later
scene edit of the request, merges included, until the next request. Pinned here for the class, through the real
Controller and approval operators: any bound call whose binding changed between its approval request and its run,
whoever changed it (another chat, the person, a merge), and the other refusals that ran nothing (a headless
host's NEEDS_GUI, Global Undo off, a delivery that ended first). Unclassified failures keep holding wherever they
arise, before the start included, and a hold already open is never relaxed. No Blender.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_merge_hold import MERGE, SCRIPT, MergeHoldFixture, OperatorFixture  # noqa: E402
from angelo_agent import script_precondition, stale_binding  # noqa: E402
from angelo_agent.refusal_codes import refusal_code  # noqa: E402

TRANSFORM = {"object": "Cube", "location": [1, 0, 0]}


class StaleAtRunTests(MergeHoldFixture):
    def stale_script(self, c):
        replies, script = self.request("script", "angelo_execute_python", SCRIPT, c)
        self.pump(c)
        self.assertIsNotNone(script.precondition)
        self.scene.revision += 1   # Another chat's or the person's edit while the card waits.
        self.approve(script, c)
        return replies

    def test_a_script_refused_at_its_run_says_nothing_ran_and_holds_nothing(self):
        c = self.chat("ASK")
        replies = self.stale_script(c)
        self.assertEqual(replies[0]["error_code"], "STALE_REVISION")
        self.assertIs(replies[0]["scene_changed"], False)
        self.assertIs(replies[0]["partial_changes_possible"], False)
        self.assertNotIn("recovery_required", replies[0])
        self.assertIn("nothing of it ran", replies[0]["error"].lower())
        self.assertIsNone(c.recovery_barrier.pending)
        self.assertEqual(self.scene.ran, [])
        # The rest of the request runs: a script asked again binds the scene as it is now, and a merge runs.
        again, script = self.request("again", "angelo_execute_python", SCRIPT, c)
        self.pump(c)
        self.approve(script, c)
        merge_replies, merge = self.request("merge", MERGE, {"parts": [{"worker": 1}]}, c)
        self.pump(c)
        self.approve(merge, c)
        self.assertEqual(self.scene.ran, ["angelo_execute_python", MERGE])
        self.assertTrue(again[0].get("success") and merge_replies[0].get("success"), (again, merge_replies))

    def test_the_same_holds_for_a_typed_edit_whose_target_moved(self):
        # Held out from the recorded script case: another bound tool, refused by the same check.
        c = self.chat("ASK")
        replies, call = self.request("move", "angelo_set_transform", TRANSFORM, c)
        self.pump(c)
        self.scene.revision += 1
        self.approve(call, c)
        self.assertEqual((replies[0]["error_code"], replies[0]["scene_changed"]), ("STALE_REVISION", False))
        self.assertIsNone(c.recovery_barrier.pending)

    def test_a_binding_holding_nan_matches_its_own_approval(self):
        # break-it C7b N-1: an object with a NaN location bound a value that never compared equal to itself.
        c = self.chat("AUTO_BOUNDED")
        self.scene.precondition = lambda name, arguments: (7, (float("nan"), 0.0, 0.0))   # A fresh float each read.
        replies, _ = self.request("move", "angelo_set_transform", TRANSFORM, c)
        self.pump(c)
        self.assertTrue(replies and replies[0].get("success"), replies)
        self.assertEqual(self.scene.ran, ["angelo_set_transform"])

    def test_a_script_whose_consent_changed_before_its_run_holds_nothing(self):
        # Review L-2: "No script ran" was a plain RuntimeError, so UNKNOWN, and the request was held.
        c = self.chat("ASK")
        replies, script = self.request("script", "angelo_execute_python", SCRIPT, c)
        self.pump(c)
        c.review_script(script)
        script.reviewed_script = "another script"   # Its identity no longer matches its review.
        c.execute_tool(script, self.scene, c.document)
        self.assertEqual((replies[0]["error_code"], replies[0]["scene_changed"]), ("CANCELLED", False))
        self.assertIsNone(c.recovery_barrier.pending)
        self.assertEqual(self.scene.ran, [])

    def test_a_script_of_a_scene_holding_nan_runs_and_a_later_edit_still_goes_stale(self):
        # Review L-6: the fingerprint through a real binding, as the run's check compares it.
        c = self.chat("ASK")
        matrices = np.eye(4).reshape(1, 16).repeat(2, 0)
        matrices[1, 0] = np.nan
        self.scene.precondition = lambda name, arguments: (7, self.scene.revision, 1, "OBJECT", fingerprint(matrices))
        replies, script = self.request("script", "angelo_execute_python", SCRIPT, c)
        self.pump(c)
        self.approve(script, c)
        self.assertTrue(replies[0].get("success"), replies)
        later, again = self.request("again", "angelo_execute_python", SCRIPT, c)
        self.pump(c)
        self.scene.revision += 1
        self.approve(again, c)
        self.assertEqual(later[0]["error_code"], "STALE_REVISION")
        self.assertIn("the scene updated", later[0]["error"])

    def test_an_open_hold_is_neither_cleared_nor_widened_by_it(self):
        c = self.chat("ASK")
        c.recovery_barrier.refused("angelo_execute_python", SCRIPT, {"error": "boom", "error_code": "INTERNAL_ERROR"})
        held = c.recovery_barrier.description()
        # Under a hold the run's own check is not reached (admission refuses); the barrier sees its result as is.
        c.recovery_barrier.refused("angelo_execute_python", SCRIPT, dict(
            {"error": "changed before it ran", "error_code": "STALE_REVISION"}, **stale_binding.NOT_STARTED))
        self.assertTrue(c.recovery_barrier.pending["holding"])
        self.assertEqual(c.recovery_barrier.description(), held)

    def test_an_unclassified_failure_before_the_start_still_holds(self):
        # The documented rule (agent_execution.rst): unknown and internal failures never claim nothing changed.
        c = self.chat("ASK")
        replies, script = self.request("script", "angelo_execute_python", SCRIPT, c)
        self.pump(c)
        self.scene.precondition = lambda name, arguments: (_ for _ in ()).throw(RuntimeError("binding read failed"))
        self.approve(script, c)
        self.assertEqual(replies[0]["error_code"], "UNKNOWN")
        self.assertNotIn("scene_changed", replies[0])
        self.assertTrue(replies[0].get("recovery_required"))

    def test_an_unclassified_failure_after_the_start_still_holds(self):
        c = self.chat("ASK")
        replies, script = self.request("script", "angelo_execute_python", SCRIPT, c)
        self.pump(c)
        self.scene.execute = lambda name, arguments: (_ for _ in ()).throw(RuntimeError("half done"))
        self.approve(script, c)
        self.assertTrue(replies[0].get("recovery_required"), replies)
        self.assertIsNotNone(c.recovery_barrier.pending)


class OtherNothingRanRefusalTests(MergeHoldFixture):
    def held_after(self, c, result):
        _, call = self.request("script", "angelo_execute_python", SCRIPT, c)
        self.pump(c)
        replies = []
        call.callbacks.append(replies.append)
        c.finish_tool(call, result)
        return c.recovery_barrier.pending

    def test_a_headless_needs_gui_refusal_is_classified_and_holds_nothing(self):
        # headless_host.refuse_waiting's own result: a card no person can answer there; nothing of it ran.
        c = self.chat("ASK")
        self.assertEqual(refusal_code({"error_code": "NEEDS_GUI"}), "NEEDS_GUI")
        self.assertIsNone(self.held_after(c, {"error_code": "NEEDS_GUI", "error": "Needs Angelo's window",
                                              "scene_changed": False}))

    def test_a_delivery_that_ended_before_the_start_holds_nothing(self):
        c = self.chat("ASK")
        replies, call = self.request("script", "angelo_execute_python", SCRIPT, c)
        self.pump(c)
        call.delivery_owner = SimpleNamespace(current=lambda controller: False)
        self.assertTrue(c._end_closed_delivery(call))
        self.assertEqual((replies[0]["error_code"], replies[0]["scene_changed"]), ("CANCELLED", False))
        self.assertIsNone(c.recovery_barrier.pending)


class WorkerBindingTests(unittest.TestCase):
    """A binding holding NaN crosses the worker connection and matches its own approval (worker_values, worker_host)."""

    def test_it_crosses_and_matches_its_own_approval(self):
        from angelo_agent import worker_values
        binding = (140223, (float("nan"), 0.0, 1.0))   # angelo_set_transform's (pointer, location).
        approved = worker_values.decode(worker_values.encode(binding))   # As the lead sends it back to the worker.
        self.assertTrue(stale_binding.same(binding, approved))
        self.assertFalse(stale_binding.same((140223, (0.0, 0.0, 1.0)), approved))

    def test_a_hosted_cards_digest_exists_and_is_stable(self):
        # worker_consent.approval_digest encoded the binding: NaN made it None, so the card could never be approved.
        from angelo_agent import worker_consent
        call = SimpleNamespace(id="worker/1", name="angelo_set_transform", invocation_digest="d", arguments=TRANSFORM,
                               program_arguments=None, precondition=(1, (float("nan"), 0.0, 0.0)), document=("part", 1),
                               recovery_review=False, recovery_evidence=None)
        digest = worker_consent.approval_digest(call)
        self.assertIsNotNone(digest)
        call.precondition = (1, (float("nan"), 0.0, 0.0))   # A fresh read: another NaN object, the same place.
        self.assertEqual(worker_consent.approval_digest(call), digest)
        call.precondition = (1, (0.0, float("nan"), 0.0))   # NaN in another place: another binding.
        self.assertNotEqual(worker_consent.approval_digest(call), digest)

    def test_the_workers_approved_check_compares_in_place(self):
        source = (Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent/worker_host.py").read_text()
        self.assertIn("if not same_binding(call.precondition, approved):", source)
        self.assertNotIn("call.precondition != approved", source)


class CleanupGateTests(unittest.TestCase):
    """Review L-4: the cleanup gate refuses before its edit is applied, so its text says so beside scene_changed false."""

    def test_the_cleanup_refusal_says_nothing_of_this_edit_was_applied(self):
        from unittest.mock import patch
        from angelo_agent import program_storage
        from angelo_agent.refusal_codes import error_result
        with patch.dict(program_storage._pending_cleanup, {"stage": program_storage.CleanupRecord({})}), \
                patch.object(program_storage, "cleanup_pending", return_value=True):
            with self.assertRaises(Exception) as caught:
                program_storage.require_cleanup_complete()
        result = error_result(caught.exception, **stale_binding.NOT_STARTED)   # As _execute_tool reports it.
        self.assertEqual((result["error_code"], result["scene_changed"]), ("CLEANUP_FAILED", False))
        self.assertTrue(result["error"].startswith("Nothing of this call was applied to the scene: temporary data"))


class OperatorTests(OperatorFixture):
    def test_a_refusal_before_the_start_pushes_no_undo_step(self):
        c = self.chat("ASK")
        click, outcomes = self.operators(c)
        replies, call = self.request("move", "angelo_set_transform", TRANSFORM, c)
        self.pump(c, times=1)
        self.scene.revision += 1
        click(call)
        self.assertEqual(replies[0]["error_code"], "STALE_REVISION")
        self.assertEqual(outcomes, [{'CANCELLED'}], "an Undo step for a change that never ran")
        _, again = self.request("again", "angelo_set_transform", TRANSFORM, c)
        self.pump(c, times=1)
        click(again)
        self.assertEqual(outcomes[-1], {'FINISHED'}, "an edit that ran keeps its Undo step")
        self.assertEqual(self.scene.ran, ["angelo_set_transform"])

    def test_global_undo_off_is_classified_and_holds_nothing(self):
        c = self.chat("ASK")
        click, _ = self.operators(c)
        replies, script = self.request("script", "angelo_execute_python", SCRIPT, c)
        self.pump(c, times=1)
        c.review_script(script)
        self.context.preferences.edit.use_global_undo = False
        click(script)
        self.assertEqual((replies[0]["error_code"], replies[0]["scene_changed"]), ("UNSUPPORTED", False))
        self.assertIs(replies[0]["refusal_details"]["final"], True, "no corrected plan can change this outcome")
        self.assertIsNone(c.recovery_barrier.pending)


class RefusalTextTests(unittest.TestCase):
    """The refusal names the part of the binding that changed, binding-neutral, never as a complete account."""

    def text(self, name, before, after, change=None, playing=False):
        call = SimpleNamespace(name=name, precondition=before)
        error = stale_binding.refusal(call, SimpleNamespace(playback_running=lambda: playing), after, change)
        return str(error), error.details

    def test_a_scripts_parts(self):
        before = (7, 3, 10, "OBJECT", "a")
        cases = {(7, 4, 10, "OBJECT", "a"): "the scene updated", (7, 3, 11, "OBJECT", "a"): "the frame",
                 (7, 3, 10, "EDIT_MESH", "a"): "the mode", (7, 3, 10, "OBJECT", "b"): "objects were added, removed or moved",
                 (8, 3, 10, "OBJECT", "a"): "the active scene switched"}
        for after, part in cases.items():
            with self.subTest(part=part):
                message, details = self.text("angelo_execute_python", before, after)
                self.assertIn(part, message)
                self.assertIn("nothing of it ran", message.lower())
                self.assertEqual(len(details["binding_changed"]), 1)
                self.assertNotIn("handles", details, "a binding part is no read target")

    def test_playback_is_named_when_only_the_frame_moved(self):
        message, _ = self.text("angelo_execute_python", (7, 3, 10, "OBJECT", "a"), (7, 4, 11, "OBJECT", "a"),
                               playing=True)
        self.assertIn("the frame (animation playback was running)", message)
        self.assertNotIn("scene updated", message, "the revision moves with the frame: one cause, named once")

    def test_no_combination_of_parts_nests_parentheses_or_blurs_the_list(self):
        # The class of "(the frame (animation playback was running))" (audit-1010 Re-check 3): the parts follow a
        # colon, separated by semicolons, so each part's own parenthetical or commas stay its own.
        from itertools import combinations
        before = (7, 3, 10, "OBJECT", "a")
        changed = ((8, 3, 10, "OBJECT", "a"), (7, 4, 10, "OBJECT", "a"), (7, 3, 11, "OBJECT", "a"),
                   (7, 3, 10, "EDIT_MESH", "a"), (7, 3, 10, "OBJECT", "b"))
        for count in range(1, 6):
            for picked in combinations(range(5), count):
                after = tuple(changed[i][i] if i in picked else before[i] for i in range(5))
                for playing in (False, True):
                    with self.subTest(parts=picked, playing=playing):
                        message, details = self.text("angelo_execute_python", before, after, playing=playing)
                        depth, deepest = 0, 0
                        for character in message:
                            depth += {"(": 1, ")": -1}.get(character, 0)
                            deepest = max(deepest, depth)
                            self.assertGreaterEqual(depth, 0, message)
                        self.assertEqual((depth, deepest <= 1), (0, True), message)
                        listed = message.split("before it ran: ", 1)[1].split(". Nothing of it ran.", 1)[0]
                        self.assertEqual(len(listed.split("; ")), len(details["binding_changed"]), message)

    def test_it_asks_for_the_call_again_never_for_a_new_request(self):
        # "Request" is the person's message, which the recovery barrier holds or frees; the refusal means the call.
        message, _ = self.text("angelo_execute_python", (7, 3, 10, "OBJECT", "a"), (7, 3, 11, "OBJECT", "a"))
        self.assertNotIn("request", message.lower())
        self.assertIn("call it again", message)

    def test_other_bindings_are_named_neutrally(self):
        for name in (MERGE, "angelo_studio_generate", "angelo_set_transform"):
            with self.subTest(name=name):
                message, details = self.text(name, ("files", 1), ("files", 2))
                self.assertIn("nothing of it ran", message.lower())
                self.assertNotIn("scene updated", message)
                self.assertIsNone(details or None)

    def test_a_programs_change_uses_its_own_classification(self):
        change = {"kind": "inputs", "rebindable": False, "reason": "An input of the program changed",
                  "advice": "Read it again."}
        message, details = self.text("angelo_scene_program", object(), object(), change)
        self.assertTrue(message.startswith("An input of the program changed after it was bound, before it ran"))
        self.assertEqual(details, {"binding_changed": ["inputs"]})


class SameBindingTests(unittest.TestCase):
    def test_nan_equals_nan_in_its_place_and_nothing_else_changes(self):
        nan = float("nan")
        same = stale_binding.same
        self.assertTrue(same((1, (nan, 0.0)), (1, (float("nan"), 0.0))))
        self.assertTrue(same({"a": [nan]}, {"a": [float("nan")]}))
        self.assertFalse(same((nan, 0.0), (0.0, nan)))
        self.assertFalse(same((nan,), (0.0,)))
        self.assertFalse(same((1, 2), (1, 2, 3)))
        self.assertFalse(same((1, 2), [1, 2]), "as == does")
        self.assertTrue(same(0.0, -0.0))
        self.assertFalse(same("unmatched:a", "unmatched:b"))


class Objects:
    """``scene.objects`` as the fingerprint reads it (foreach_get, len, iteration)."""

    def __init__(self, matrices):
        self.matrices = np.asarray(matrices, np.float32)
        self.items = [SimpleNamespace(data=SimpleNamespace(session_uid=100 + i), modifiers=[]) for i in
                      range(len(self.matrices))]

    def __len__(self):
        return len(self.items)

    def __iter__(self):
        return iter(self.items)

    def foreach_get(self, name, out):
        out[:] = (self.matrices.reshape(-1) if name == "matrix_world" else np.arange(len(self.items)))


def fingerprint(matrices):
    return script_precondition.objects_fingerprint(SimpleNamespace(objects=Objects(matrices)))


class NanFingerprintTests(unittest.TestCase):
    def test_a_scene_holding_nan_binds_like_any_other(self):
        nan = np.eye(4).reshape(1, 16).repeat(2, 0)
        nan[1, 3] = np.nan
        self.assertEqual(fingerprint(nan), fingerprint(nan), "every script of such a scene was refused at its run")
        moved = nan.copy()
        moved[0, 12] = 2.0
        self.assertNotEqual(fingerprint(nan), fingerprint(moved), "a moved object still changes the binding")
        elsewhere = np.eye(4).reshape(1, 16).repeat(2, 0)
        elsewhere[1, 4] = np.nan
        self.assertNotEqual(fingerprint(nan), fingerprint(elsewhere))
        zero = nan.copy()
        zero[1, 3] = 0.0
        self.assertNotEqual(fingerprint(nan), fingerprint(zero), "NaN is not read as a real 0.0")

    def test_a_scene_without_nan_hashes_as_before(self):
        import hashlib
        matrices = np.eye(4, dtype=np.float32).reshape(1, 16).repeat(3, 0)
        matrices[2, 12] = -0.0
        digest = hashlib.blake2b(digest_size=20)
        digest.update((3).to_bytes(8, "little"))
        digest.update(np.arange(3, dtype=np.int64).tobytes())
        digest.update((matrices.reshape(-1) + np.float32(0.0)).tobytes())
        digest.update(repr(((100, 0), (101, 0), (102, 0))).encode())
        self.assertEqual(fingerprint(matrices), digest.hexdigest())


if __name__ == "__main__":
    unittest.main()
