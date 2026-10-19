# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A program's bound frame moving (playback, timeline) is waited for or rebound once, never a silent edit.

Regression for ``interrupted`` cube_creation failures: keyboard input started
animation playback between approval and staging, the approved binding's frame no longer
matched and the request ended with "The target changed after approval".
"""

import contextlib
import copy
import hashlib
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from agent_runtime_fixtures import AgentRuntimeFixture, HANDLE, program
from angelo_agent.failure_codes import failure_code
from angelo_agent.program_context import (ProgramBinding, ProgramFrameChanged, changed_message, frame_reason,
                                          precondition_change)
from angelo_agent.program_jobs import ProgramError
from angelo_agent.telemetry import clean_receipt

INPUTS = ((HANDLE, "a" * 64, "fingerprint"),)
SCENE = "scn:" + "a" * 32 + ":2"


def binding(frame=1, inputs=INPUTS, generation=0, scene=7, view=9, settings=None):
    return ProgramBinding(generation, scene, (frame, 0.0), view, inputs, settings)


class ClassificationTests(unittest.TestCase):
    def test_only_a_frame_change_with_identical_inputs_is_rebindable(self):
        change = precondition_change(binding(1), binding(37))
        self.assertEqual((change["kind"], change["rebindable"]), ("frame", True))
        self.assertIn("from 1 to 37", change["reason"])
        for after, kind in ((binding(1, generation=1), "document"), (binding(1, scene=8), "scene"),
                            (binding(1, view=10), "view_layer"),
                            (binding(1, inputs=((HANDLE, "b" * 64, "fingerprint"),)), "inputs"),
                            (binding(37, inputs=((HANDLE, "b" * 64, "fingerprint"),)), "inputs"),
                            (binding(37, settings=(SCENE, "c" * 64)), "settings")):
            with self.subTest(kind=kind):
                change = precondition_change(binding(1, settings=(SCENE, "b" * 64) if kind == "settings" else None),
                                             after)
                self.assertEqual(change["kind"], kind)
                self.assertFalse(change["rebindable"])
        self.assertIn("angelo_scene_settings", changed_message(
            precondition_change(binding(1, settings=(SCENE, "b" * 64)), binding(1, settings=(SCENE, "c" * 64))), "now"))
        self.assertIsNone(precondition_change(binding(1), binding(1)))
        self.assertIsNone(precondition_change("opaque", "other"))
        self.assertIsNone(precondition_change(tuple(binding(1))[:5], tuple(binding(37))[:5]))  # Not a program binding.
        self.assertIsNone(changed_message(None, "after approval"))

    def test_messages_name_the_actual_change_and_the_artist_action(self):
        text = changed_message(precondition_change(binding(1), binding(37)), "after approval; nothing was staged or changed")
        self.assertTrue(text.startswith("The current frame changed from 1 to 37"))
        self.assertIn("stop playback", text)
        self.assertNotIn("target", text)
        error = ProgramFrameChanged((1, 0.0), (2, 0.5))
        self.assertEqual(error.context_change, "frame")
        self.assertIn("from 1 to 2 (+0.5)", str(error))

    def test_failure_codes_are_closed_and_content_free(self):
        self.assertEqual(failure_code({"error": "x", "context_changed": "frame"}), "frame")
        self.assertEqual(failure_code({"error_code": "STALE_REVISION", "error": "The target changed after approval; staged work was not started"}),
                         "binding")
        self.assertEqual(failure_code({"error": "Choose an existing staged modifier of the exact declared type"}),
                         "other")
        self.assertEqual(failure_code({"error": "Blender operation/approval expired", "expired": True}), "expired")
        self.assertEqual(failure_code({"error": "Secret object 'Ünïcode' exploded"}), "other")
        receipt = clean_receipt({"status": "completed", "phases": {"total_ms": 1}, "counts": {
            "failure_frame": 1, "frame_rebinds": 1, "staging_restarts": 2, "playback_waits": 1,
            "tool_failure_Secret object": 1}})
        self.assertEqual(receipt["counts"], {"failure_frame": 1, "frame_rebinds": 1,
                                             "staging_restarts": 2, "playback_waits": 1})


class NativeBindingContractTests(unittest.TestCase):
    """``native_programs.precondition``'s binding is the one ``rebind`` and ``precondition_change`` read.

    Regression for the native-coverage stress failures (frame_step, playback):
    the precondition gained the scene-settings field while both consumers
    still required five, so no frame move was ever rebound and every program
    ended with "The target changed after approval". The fake backend follows
    the native contract: object revisions carry the frame, settings revisions do not.
    """

    def setUp(self):
        from angelo_agent import native_programs as native
        self.native = native
        self.state = SimpleNamespace(frame=(1, 0.0), content="same", settings="b" * 64)
        state, cube, revision = self.state, SimpleNamespace(name="Cube"), self.revision

        class Backend:
            def __init__(self, window, store=None, controller=None):
                self.generation, self.scene_uid, self.view_uid, self.frame = 0, 7, 9, state.frame
                self.scene = SimpleNamespace(objects=[cube])

            def ensure_main_thread(self):
                pass

            def bind(self, program):
                entries = {entry["handle"]: entry["revision"] for key in ("read_set", "write_set")
                           for entry in program[key]}
                if entries[HANDLE] != revision(state.frame[0]):
                    raise ProgramError("An exact object revision changed; inspect the target before retrying", code="STALE_REVISION")
                if SCENE in entries and entries[SCENE] != state.settings:
                    raise ProgramError("The scene settings changed; read angelo_scene_settings again before retrying", code="STALE_SETTING")
                return {"generation": 0, "scene_uid": 7, "frame": state.frame, "view_uid": 9, "handles": (HANDLE,),
                        "revisions": {HANDLE: entries[HANDLE]}, "fingerprints": {HANDLE: state.content},
                        "scene": {"handle": SCENE, "revision": entries[SCENE]} if SCENE in entries else None}

            def _resolve(self, handle):
                return cube if handle == HANDLE else None

            def _revision(self, obj, handle):
                return revision(state.frame[0])

            def _scene_revision(self):
                return state.settings

        queries = ModuleType("angelo_agent.scene_queries")
        queries.object_revision_at_frame = lambda window, obj, frame: revision(frame)
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        for target, value in (("BlenderProgramBackend", Backend), ("frame_independent", lambda obj: True),
                              ("_fingerprint", lambda obj: state.content), ("require_cleanup_complete", lambda: None)):
            stack.enter_context(patch.object(native, target, value))
        stack.enter_context(patch.dict(sys.modules, {"angelo_agent.scene_queries": queries}))
        self.window = SimpleNamespace(screen=SimpleNamespace(is_animation_playing=False))

    def revision(self, frame):
        return hashlib.sha256(("%s:%s" % (frame, self.state.content)).encode()).hexdigest()

    def approve(self, scene=True):
        arguments = program()
        arguments["write_set"][0]["revision"] = self.revision(self.state.frame[0])
        if scene:
            arguments["read_set"] = [{"handle": SCENE, "revision": self.state.settings}]
        return arguments, self.native.precondition(self.window, "angelo_scene_program", arguments)

    def test_a_frame_step_rebinds_the_real_precondition(self):
        for scene in (False, True):
            with self.subTest(scene=scene):
                self.state.frame = (1, 0.0)
                arguments, approved = self.approve(scene)
                self.state.frame = (2, 0.0)
                rebound = self.native.rebind(self.window, "angelo_scene_program", arguments, approved)
                self.assertIn("arguments", rebound or {})
                self.assertIsInstance(approved, ProgramBinding)
                self.assertEqual(rebound["arguments"]["write_set"][0]["revision"], self.revision(2))
                self.assertEqual(rebound["arguments"]["read_set"], arguments["read_set"])  # Settings keep their revision.
                self.assertEqual(rebound["precondition"].frame, (2, 0.0))

    def test_an_edit_before_the_frame_step_is_never_rebound(self):
        for edit, words in ((lambda: setattr(self.state, "settings", "c" * 64), "scene settings changed too"),
                            (lambda: setattr(self.state, "content", "edited"), "declared object changed too")):
            with self.subTest(words=words):
                self.state.frame, self.state.content, self.state.settings = (1, 0.0), "same", "b" * 64
                arguments, approved = self.approve()
                edit()
                self.state.frame = (2, 0.0)
                refused = self.native.rebind(self.window, "angelo_scene_program", arguments, approved)
                self.assertIn(words, refused["refused"])

    def test_a_program_that_sets_the_frame_is_never_rebound_to_another(self):
        """Regression: a timeline move after approving a frame_current write was a recoverable frame
        change, so commit overwrote the artist's move. The frame is that program's own setting: STALE_SETTING."""
        from angelo_agent.program_spec import validate_program
        self.state.frame = (1, 0.0)
        arguments = program()
        arguments["write_set"][0]["revision"] = self.revision(1)
        arguments["write_set"].append({"handle": SCENE, "revision": self.state.settings})
        arguments["operations"].append({"kind": "property_set", "target": SCENE,
                                        "parameters": {"values": {"frame_current": 40}}})
        arguments = validate_program(arguments)
        approved = self.native.precondition(self.window, "angelo_scene_program", arguments)
        self.state.frame = (30, 0.0)
        refused = self.native.rebind(self.window, "angelo_scene_program", arguments, approved)
        self.assertEqual(refused["error_code"], "STALE_SETTING")
        self.assertIn("this program sets the current frame", refused["refused"])


class ControllerRecoveryTests(AgentRuntimeFixture, unittest.TestCase):
    """The fake executor models the native contract: revisions are stamped with the frame."""

    def setUp(self):
        super().setUp()
        self.frame, self.content, self.static, self.playing = 1, "same", True, False
        self.executor.precondition = self.precondition
        self.executor.precondition_change = lambda name, before, after: precondition_change(before, after)
        self.executor.rebind_program = self.rebind
        self.executor.playback_running = lambda: self.playing
        self.hooks = []
        self.c._begin_metrics()

    def revision(self):
        return "%064x" % (hash((self.frame, self.content)) & (2 ** 256 - 1))

    def current(self):
        return binding(self.frame, ((HANDLE, self.revision(), self.content),))

    def precondition(self, name, arguments):
        if self.hooks:
            self.hooks.pop(0)()
        declared = arguments["write_set"][0]["revision"]
        if declared != self.revision():
            raise ProgramError("An exact object revision changed; inspect the target before retrying", code="STALE_REVISION")
        return self.current()

    def rebind(self, name, arguments, approved):
        current = self.current()
        if approved[:2] + approved[3:4] != current[:2] + current[3:4] or approved[2] == current[2]:
            return None
        reason = frame_reason(approved[2], current[2])
        if not self.static:
            return {"refused": reason + " and a declared object can change with time"}
        if approved[4][0][2] != self.content:
            return {"refused": reason + " and a declared object changed too"}
        program = copy.deepcopy(arguments)
        program["write_set"][0]["revision"] = self.revision()
        return {"arguments": program, "precondition": current, "reason": reason}

    def enqueue_program(self):
        answers = []
        arguments = program()
        arguments["write_set"][0]["revision"] = self.revision()
        self.c.request_tool("program", "angelo_scene_program", arguments, answers.append)
        return answers, self.c.pending.get(self.c.state["id"] + "/program")

    def run_until_answer(self, answers, limit=60):
        for _ in range(limit):
            self.pump()
            if answers:
                return answers[0]
        self.fail("No final result")

    def counts(self):
        return self.c._request_metrics.snapshot()["counts"]

    def test_frame_moved_between_approval_and_staging_rebinds_once_and_commits(self):
        self.hooks = [lambda: None, lambda: setattr(self, "frame", 37)]  # Approval at 1, staging finds 37.
        answers, call = self.enqueue_program()
        result = self.run_until_answer(answers)
        self.assertTrue(result["success"], result)
        self.assertTrue(result["scene_changed"])
        self.assertEqual(self.counts().get("frame_rebinds"), 1)
        self.assertNotIn("tool_failures", self.counts())
        staged = self.executor.jobs[0].program
        self.assertEqual(staged["write_set"][0]["revision"], self.revision())
        self.assertEqual(staged["operations"], program()["operations"])

    def test_a_second_frame_change_fails_closed_with_the_actual_reason(self):
        self.hooks = [lambda: None, lambda: setattr(self, "frame", 37), lambda: setattr(self, "frame", 40)]
        answers, _ = self.enqueue_program()
        result = self.run_until_answer(answers)
        self.assertIn("The current frame changed from 37 to 40", result["error"])
        self.assertIn("again before commit", result["error"])
        self.assertIs(result["scene_changed"], False)
        self.assertEqual(self.executor.jobs[0].backend.commits, 0)
        self.assertEqual(self.counts().get("failure_frame"), 1)

    def test_changed_inputs_are_never_rebound(self):
        self.hooks = [lambda: None, lambda: setattr(self, "content", "edited")]
        answers, _ = self.enqueue_program()
        result = self.run_until_answer(answers)
        self.assertEqual(result["error"], "An exact object revision changed; inspect the target before retrying")
        self.assertEqual(self.executor.jobs, [])
        self.assertNotIn("frame_rebinds", self.counts())
        self.assertEqual(self.counts().get("failure_binding"), 1)

    def test_time_dependent_inputs_are_not_rebound_and_the_reason_says_why(self):
        self.static = False
        self.hooks = [lambda: None, lambda: setattr(self, "frame", 2)]
        answers, _ = self.enqueue_program()
        result = self.run_until_answer(answers)
        self.assertTrue(result["error"].startswith("The current frame changed from 1 to 2"))
        self.assertIn("can change with time after approval; nothing was staged or changed", result["error"])
        self.assertEqual(self.executor.jobs, [])

    def test_playback_holds_staging_then_rebinds_at_the_stopped_frame(self):
        answers, _ = self.enqueue_program()
        self.playing = True
        for _ in range(5):
            self.pump()
        self.assertEqual(self.executor.jobs, [])
        self.assertFalse(answers)
        self.assertEqual(self.c.status, "Waiting for animation playback to stop")
        self.playing, self.frame = False, 92
        result = self.run_until_answer(answers)
        self.assertTrue(result["success"], result)
        self.assertEqual(self.counts().get("playback_waits"), 1)
        self.assertEqual(self.counts().get("frame_rebinds"), 1)

    def test_frame_moving_during_staging_restarts_the_same_program(self):
        answers, _ = self.enqueue_program()
        original_start = self.executor.start_native_job

        def start(name, arguments, *, invocation_id):
            job = original_start(name, arguments, invocation_id=invocation_id)
            if len(self.executor.jobs) == 1:
                def check_context(_binding, backend=job.backend):
                    backend.check_context = lambda _b: None
                    self.frame = 12
                    raise ProgramFrameChanged((1, 0.0), (12, 0.0))
                job.backend.check_context = check_context
            return job
        self.executor.start_native_job = start
        result = self.run_until_answer(answers)
        self.assertTrue(result["success"], result)
        self.assertEqual(len(self.executor.jobs), 2)
        self.assertEqual(self.executor.jobs[1].backend.commits, 1)
        self.assertEqual(self.counts().get("staging_restarts"), 1)
        self.assertEqual(self.counts().get("frame_rebinds"), 1)

    def test_frame_moving_before_commit_discards_the_stage_and_restages(self):
        answers, call = self.enqueue_program()
        job = self.ready()
        self.frame = 5
        result = self.c.commit_native_job(call, self.executor, self.c.document)
        self.assertEqual(result, {"restaging": True, "final": False, "scene_changed": False})
        self.assertTrue(job.closed)
        self.assertEqual(job.backend.commits, 0)
        self.assertFalse(answers)
        result = self.run_until_answer(answers)
        self.assertTrue(result["success"], result)
        self.assertEqual(self.counts().get("staging_restarts"), 1)
        self.assertEqual(self.counts().get("frame_rebinds"), 1)

    def test_deferred_frame_restage_is_not_a_terminal_completion(self):
        answers, call = self.enqueue_program()
        job = self.ready()
        self.frame = 5
        result = self.c.commit_native_job(call, self.executor, self.c.document, defer_finish=True)
        self.assertEqual(result, {"restaging": True, "final": False, "scene_changed": False})
        self.assertTrue(job.closed)
        self.assertEqual(job.backend.commits, 0)
        finished = self.c.finish_native_commit(call, {"undo_published": False})
        self.assertEqual(finished, result)
        self.assertIn(call.id, self.c.pending)
        self.assertFalse(answers)
        self.assertIsNone(call.commit_result)
        self.assertFalse(call.committing)
        result = self.run_until_answer(answers)
        self.assertTrue(result["success"], result)
        self.assertEqual(self.counts().get("staging_restarts"), 1)
        self.assertEqual(self.counts().get("frame_rebinds"), 1)

    def test_prepared_frame_restage_keeps_ownership_and_callback_uncertainty(self):
        answers, call = self.enqueue_program()
        job = self.ready()
        self.frame = 5
        preparation = self.c.enter_native_preparation(call, self.executor, self.c.document)
        self.assertTrue(preparation.restaging)
        self.assertTrue(preparation.result["restaging"])
        self.assertNotIn("scene_changed", preparation.result)
        self.assertTrue(preparation.result["partial_changes_possible"])
        self.assertFalse(job.closed)
        self.assertIs(self.writer.owner, job)
        self.c.leave_native_preparation(call, preparation)
        self.assertTrue(job.closed)
        result = self.c.finish_native_commit(call, {"undo_published": False})
        self.assertFalse(result["final"])
        self.assertFalse(answers)
        self.assertIn(call.id, self.c.pending)
        result = self.run_until_answer(answers)
        self.assertTrue(result["success"], result)
        self.assertEqual(self.counts().get("staging_restarts"), 1)
        self.assertEqual(self.counts().get("frame_rebinds"), 1)

    def test_stop_before_prepared_restage_close_cancels_instead(self):
        answers, call = self.enqueue_program()
        job = self.ready()
        self.frame = 5
        preparation = self.c.enter_native_preparation(call, self.executor, self.c.document)
        self.c.stop()
        self.assertFalse(job.closed)
        self.c.leave_native_preparation(call, preparation)
        result = self.c.finish_native_commit(call, {"undo_published": False})
        self.assertEqual(result["error_code"], "CANCELLED")
        self.assertFalse(result.get("restaging", False))
        self.assertEqual(len(answers), 1)
        self.assertEqual(job.backend.commits, 0)

    def test_frame_change_after_preparation_does_not_refresh_approval(self):
        answers, call = self.enqueue_program()
        job = self.ready()
        preparation = self.c.enter_native_preparation(call, self.executor, self.c.document)
        self.assertIsNone(preparation.result)
        approved = call.precondition
        self.frame = 5
        self.c.commit_native_job(call, self.executor, self.c.document, defer_finish=True)
        self.c.leave_native_preparation(call, preparation)
        result = self.c.finish_native_commit(call, {"undo_published": False})
        self.assertFalse(result.get("restaging", False))
        self.assertEqual(call.precondition, approved)
        self.assertEqual(job.backend.commits, 0)
        self.assertEqual(len(answers), 1)

    def test_stop_from_prepared_restage_cleanup_is_deferred_until_leave_returns(self):
        answers, call = self.enqueue_program()
        job = self.ready()
        self.frame = 5
        preparation = self.c.enter_native_preparation(call, self.executor, self.c.document)
        discard = job.backend.discard
        def stopping_discard(stage):
            self.c.stop()
            self.assertFalse(answers)
            self.assertIs(self.writer.owner, job)
            discard(stage)
        job.backend.discard = stopping_discard
        with patch.object(self.writer, "release", wraps=self.writer.release) as release:
            self.c.leave_native_preparation(call, preparation)
            result = self.c.finish_native_commit(call, {"undo_published": False})
            release.assert_called_once_with(job)
        self.assertEqual(result["error_code"], "CANCELLED")
        self.assertFalse(result.get("restaging", False))
        self.assertEqual(len(answers), 1)
        self.assertEqual(job.backend.discards, 1)

    def test_opaque_preconditions_keep_the_generic_message(self):
        del self.executor.rebind_program
        self.executor.precondition_change = lambda *args: None
        self.executor.precondition = lambda name, arguments: (self.hooks.pop(0)() if self.hooks else None, self.frame)[1]
        self.hooks = [lambda: None, lambda: setattr(self, "frame", 2)]
        answers, _ = self.enqueue_program()
        result = self.run_until_answer(answers)
        self.assertEqual(result["error"], "The target changed after approval; staged work was not started")


if __name__ == "__main__":
    unittest.main()
