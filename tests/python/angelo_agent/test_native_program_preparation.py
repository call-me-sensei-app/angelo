# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Same-dispatch ownership before native begin; no Blender or provider needed."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent_runtime_fixtures import AgentRuntimeFixture, program
from angelo_agent.refusal_codes import RefusalError
from angelo_agent.request_fences import DeliveryOwner, RequestFence
from angelo_agent.tool_deadlines import revoke_turn_calls


class PreparationTests(AgentRuntimeFixture, unittest.TestCase):
    def prepared(self, controller=None):
        c = controller or self.c
        answers, call = self.enqueue(c)
        job = self.ready(c)
        preparation = c.enter_native_preparation(call, self.executor, c.document)
        self.assertIsNone(preparation.result)
        self.assertIs(call.native_preparation, preparation)
        self.assertIs(self.writer.owner, job)
        return answers, call, job, preparation

    def close(self, call, preparation, *, error=None, published=False):
        self.c.leave_native_preparation(call, preparation, error=error)
        return self.c.finish_native_commit(call, {"undo_published": published})

    def prepared_delivery(self, c):
        owner = DeliveryOwner(RequestFence.capture(c))
        answers, completions = [], []
        c.request_tool("owned-program", "angelo_scene_program", program(), answers.append,
                       delivery_owner=owner, completion_sink=completions.append)
        call = c.pending[c.state["id"] + "/owned-program"]
        # This host-only backend models positive native predicate reports. The
        # controller must still wait for actual Undo finalization and delivery.
        self.executor.studio_context = c
        self.executor.backend().validate = lambda stage, check: {
            "passed": True, "status": "pass", "checks": [{
                "passed": True, "status": "pass", "kind": check["kind"],
                "target": check.get("target"), "observed": {"location": list(stage["location"])}}]}
        job = self.ready(c)
        preparation = c.enter_native_preparation(call, self.executor, c.document)
        self.assertIsNone(preparation.result)
        return answers, completions, owner, call, job, preparation

    def test_stop_expiry_invalidation_and_direct_finish_defer_stage_and_delivery(self):
        actions = {
            "stop": lambda c, call: c.stop(),
            "expiry": lambda c, call: self.expire(c, call),
            "document": lambda c, call: c.set_document(("replacement", 2)),
            "stages": lambda c, call: c.invalidate_native_stages("stage ownership invalidated"),
            "read_only": lambda c, call: c.set_options(approval="READ_ONLY"),
            "direct": lambda c, call: c.finish_tool(call, {"error": "direct refusal", "error_code": "DECLINED"}),
            "close": lambda c, call: c.close(),
        }
        for label, action in actions.items():
            with self.subTest(label=label):
                c = self.new_controller()
                answers, call, job, preparation = self.prepared(c)
                action(c, call)
                self.assertFalse(answers)
                self.assertIs(c.native_jobs[call.id], job)
                self.assertIs(self.writer.owner, job)
                self.assertFalse(job.closed)
                self.assertEqual(job.backend.discards, 0)
                c.commit_native_job(call, self.executor, c.document, defer_finish=True)
                self.assertFalse(answers)
                self.assertEqual(job.backend.commits, 0)
                c.leave_native_preparation(call, preparation)
                result = c.finish_native_commit(call, {"undo_published": False})
                self.assertEqual(len(answers), 1)
                self.assertTrue(result["partial_changes_possible"])
                self.assertNotIn("scene_changed", result)
                self.assertFalse(call.mutation_started)
                self.assertEqual(job.backend.discards, 1)
                self.assertIsNone(self.writer.owner)

    def expire(self, c, call):
        with patch("angelo_agent.controller.time.monotonic", return_value=call.expires + 1):
            self.assertTrue(c.expire_tool(call))

    def test_expiry_preserves_original_refusal_code(self):
        answers, call, job, preparation = self.prepared()
        self.expire(self.c, call)
        self.c.commit_native_job(call, self.executor, self.c.document, defer_finish=True)
        result = self.close(call, preparation)
        self.assertEqual(result["error_code"], "EXPIRED")
        self.assertTrue(result["expired"])
        self.assertEqual(len(answers), 1)

    def test_preapply_refusal_preserves_publication_and_each_native_finalization_diagnostic(self):
        for published, key in ((True, "editor_finalization_error"), (None, "finalization_transport_error"),
                               (False, "native_scope_close_error")):
            with self.subTest(published=published, diagnostic=key):
                c = self.new_controller()
                answers, call, job, preparation = self.prepared(c)
                c.stop()
                c.prepare_native_finish(call, preparation)
                c.leave_native_preparation(call, preparation)
                result = c.finish_native_commit(call, {"undo_published": published, key: "fixture native diagnostic"})
                self.assertEqual(result["error_code"], "CANCELLED")
                self.assertIs(result["undo_published"], published)
                self.assertIn(key, result["native_finalization_error"])
                self.assertTrue(result["partial_changes_possible"])
                self.assertNotIn("scene_changed", result)
                self.assertFalse(result["undoable"])
                self.assertEqual(job.backend.commits, 0)
                self.assertEqual(len(answers), 1)

    def test_restage_shortcut_rejects_editor_transport_and_close_diagnostics(self):
        for key in ("editor_finalization_error", "finalization_transport_error", "native_scope_close_error"):
            with self.subTest(diagnostic=key):
                c = self.new_controller()
                answers, call, job, preparation = self.prepared(c)
                c.leave_native_preparation(call, preparation)
                preparation.restaging = True
                call.commit_result = {"restaging": True, "final": False, "scene_changed": False}
                result = c.finish_native_commit(call, {"undo_published": False, key: "fixture finalization failure"})
                self.assertEqual(result["error_code"], "INTERNAL_ERROR")
                self.assertIn(key, result["native_finalization_error"])
                self.assertNotIn("restaging", result)
                self.assertTrue(result["partial_changes_possible"])
                self.assertEqual(len(answers), 1)
                self.assertNotIn(call.id, c.pending)

    def test_real_prepared_job_before_apply_and_cleanup_errors_never_run_exception_hooks(self):
        hooks = []
        class Failure(RuntimeError):
            def __getattribute__(self, name):
                if name in {"cleanup_error", "partial_changes_possible", "context_change", "refusal_code", "code", "details"}:
                    hooks.append(name)
                    raise AssertionError("exception property hook must not run")
                return super().__getattribute__(name)
            def __bool__(self):
                hooks.append("bool")
                raise AssertionError("exception truth hook must not run")
            def __str__(self):
                hooks.append("str")
                raise AssertionError("exception string hook must not run")
        for point in ("before_apply", "before_apply_and_cleanup", "cancel_cleanup"):
            with self.subTest(point=point):
                c = self.new_controller()
                answers, call, job, preparation = self.prepared(c)
                if "cleanup" in point:
                    discard = job.backend.discard
                    def cleanup(stage):
                        discard(stage)
                        raise Failure("prepared cleanup failed")
                    job.backend.discard = cleanup
                if point == "cancel_cleanup":
                    c.stop()
                    c.prepare_native_finish(call, preparation)
                else:
                    with patch.object(c, "_begin_prepared_apply", side_effect=Failure("before apply failed")):
                        c.commit_native_job(call, self.executor, c.document, defer_finish=True)
                    c.prepare_native_finish(call, preparation)
                self.assertTrue(job.closed)
                self.assertEqual(job.backend.commits, 0)
                self.assertIs(self.writer.owner, job)
                c.leave_native_preparation(call, preparation)
                result = c.finish_native_commit(call, {"undo_published": False})
                self.assertIsNone(self.writer.owner)
                self.assertEqual(len(answers), 1)
                self.assertTrue(result["partial_changes_possible"])
                if "cleanup" in point:
                    self.assertIn("prepared cleanup failed", result["cleanup_error"])
                self.assertEqual(hooks, [])

    def test_precondition_stop_inside_enter_is_owned_and_reports_possible_effects(self):
        answers, call = self.enqueue()
        job = self.ready()
        self.executor.on_precondition = lambda *args: self.c.stop()
        preparation = self.c.enter_native_preparation(call, self.executor, self.c.document)
        self.assertEqual(preparation.result["error_code"], "CANCELLED")
        self.assertEqual(job.backend.discards, 0)
        self.assertFalse(answers)
        result = self.close(call, preparation)
        self.assertNotIn("scene_changed", result)
        self.assertEqual(job.backend.commits, 0)

    def test_precondition_exception_and_begin_exception_never_claim_unchanged(self):
        for point in ("precondition", "begin"):
            with self.subTest(point=point):
                c = self.new_controller()
                answers, call = self.enqueue(c)
                job = self.ready(c)
                if point == "precondition":
                    self.executor.on_precondition = lambda *args: (_ for _ in ()).throw(RuntimeError("getter failed"))
                preparation = c.enter_native_preparation(call, self.executor, c.document)
                self.executor.on_precondition = None
                c.leave_native_preparation(call, preparation, error=RuntimeError("begin failed") if point == "begin" else None)
                result = c.finish_native_commit(call, {"undo_published": False})
                self.assertTrue(result["partial_changes_possible"])
                self.assertNotIn("scene_changed", result)
                self.assertFalse(result["mutation_started"])
                self.assertEqual(job.backend.commits, 0)
                self.assertEqual(len(answers), 1)
                c.recovery_barrier.clear()

    def test_exact_fence_approval_and_precondition_changes_never_apply(self):
        changes = {
            "epoch": lambda c, call: setattr(c, "_epoch", c._epoch + 1),
            "generation": lambda c, call: setattr(c, "_request_generation", c._request_generation + 1),
            "document": lambda c, call: setattr(c, "document", ("other", 2)),
            "approval": lambda c, call: setattr(call, "approved", False),
            "authorization": lambda c, call: setattr(call, "authorized", False),
            "expiry_extension": lambda c, call: setattr(call, "expires", call.expires + 10),
            "digest": lambda c, call: setattr(call, "invocation_digest", "replacement"),
            "id": lambda c, call: setattr(call, "id", "replacement"),
            "name": lambda c, call: setattr(call, "name", "not_a_tool"),
            "arguments": lambda c, call: call.arguments["operations"][0]["parameters"].update(location=[7, 8, 9]),
            "binding": lambda c, call: setattr(c.native_jobs[call.id], "binding", {"revision": 2}),
            "delivery_owner": lambda c, call: setattr(call, "delivery_owner", DeliveryOwner(RequestFence.capture(c))),
            "parent_call": lambda c, call: setattr(call, "parent_call", SimpleNamespace(id="replacement-parent")),
            "approved_fingerprint": lambda c, call: setattr(call, "precondition", "replacement"),
            "live_fingerprint": lambda c, call: setattr(self.executor, "fingerprint", "changed"),
        }
        for label, change in changes.items():
            with self.subTest(label=label):
                self.executor.fingerprint = "exact"
                c = self.new_controller()
                answers, call, job, preparation = self.prepared(c)
                original = preparation.precondition
                change(c, call)
                c.commit_native_job(call, self.executor, preparation.document, defer_finish=True)
                self.assertEqual(preparation.precondition, original)
                self.assertFalse(answers)
                c.leave_native_preparation(call, preparation)
                result = c.finish_native_commit(call, {"undo_published": False})
                self.assertEqual(result["error_code"], "STALE_CONTEXT" if label == "live_fingerprint" else "OWNERSHIP")
                self.assertEqual(job.backend.commits, 0)
                self.assertEqual(len(answers), 1)

    def test_replaced_job_is_not_cleaned_up_in_place_of_the_original(self):
        answers, call, job, preparation = self.prepared()
        replacement = SimpleNamespace(invocation_id=call.id, cancel=lambda: self.fail("replacement was cancelled"))
        self.c.native_jobs[call.id] = replacement
        self.c.commit_native_job(call, self.executor, self.c.document, defer_finish=True)
        result = self.close(call, preparation)
        self.assertEqual(result["error_code"], "OWNERSHIP")
        self.assertTrue(job.closed)
        self.assertIs(self.c.native_jobs.pop(call.id), replacement)
        self.assertEqual(len(answers), 1)

    def test_replaced_pending_call_still_releases_original_job_without_delivery(self):
        answers, call, job, preparation = self.prepared()
        self.c.pending.pop(call.id)
        self.c.commit_native_job(call, self.executor, self.c.document, defer_finish=True)
        self.close(call, preparation)
        self.assertEqual(answers, [])
        self.assertTrue(job.closed)
        self.assertIsNone(self.writer.owner)
        self.assertIsNone(call.finished_preparation)

    def test_reentrant_enter_and_wrong_leave_preserve_the_outer_owner(self):
        answers, call, job, preparation = self.prepared()
        with self.assertRaises(RefusalError):
            self.c.enter_native_preparation(call, self.executor, self.c.document)
        with self.assertRaises(RefusalError):
            self.c.leave_native_preparation(call, object())
        with self.assertRaises(RefusalError):
            self.c.finish_native_commit(call, {"undo_published": False})
        self.assertIs(call.native_preparation, preparation)
        self.assertIs(self.writer.owner, job)
        self.assertIsNone(preparation.result)
        self.assertIsNone(call.commit_result)
        self.assertFalse(answers)
        self.close(call, preparation, error=RuntimeError("fixture abandons"))

    def test_writer_is_retained_until_close_and_released_once_after_commit_or_error(self):
        for fails in (False, True):
            with self.subTest(fails=fails):
                c = self.new_controller()
                answers, call, job, preparation = self.prepared(c)
                if fails:
                    job.backend.commit = lambda stage: (_ for _ in ()).throw(RuntimeError("commit failed"))
                with patch.object(self.writer, "release", wraps=self.writer.release) as release:
                    c.commit_native_job(call, self.executor, c.document, defer_finish=True)
                    self.assertIs(self.writer.owner, job)
                    release.assert_not_called()
                    self.assertFalse(answers)
                    c.leave_native_preparation(call, preparation)
                    self.assertIsNone(self.writer.owner)
                    c.finish_native_commit(call, {"undo_published": True})
                    c.finish_native_commit(call, {"undo_published": False})
                    release.assert_called_once_with(job)
                self.assertEqual(len(answers), 1)

    def test_stop_after_typed_apply_preserves_actual_result(self):
        answers, call, job, preparation = self.prepared()
        self.c.commit_native_job(call, self.executor, self.c.document, defer_finish=True)
        self.c.stop()
        self.assertFalse(answers)
        result = self.close(call, preparation, published=True)
        self.assertEqual(result["status"], "committed")
        self.assertTrue(result["scene_changed"])
        self.assertEqual(job.backend.commits, 1)

    def test_job_verification_and_hydration_callbacks_can_still_revoke_before_apply(self):
        for phase in ("verify", "resume_stage", "prepare_commit"):
            for revoke in ("stop", "read_only", "finished"):
                with self.subTest(phase=phase, revoke=revoke):
                    c = self.new_controller()
                    answers, call, job, preparation = self.prepared(c)
                    def callback(*args):
                        if revoke == "stop":
                            c.stop()
                        elif revoke == "read_only":
                            c.set_options(approval="READ_ONLY")
                        else:
                            c.runtime_context().emit("finished", {"status": "completed"})
                        return args[0] if phase == "resume_stage" else None
                    setattr(job.backend, phase, callback)
                    c.commit_native_job(call, self.executor, c.document, defer_finish=True)
                    self.assertFalse(call.mutation_started)
                    self.assertEqual(job.backend.commits, 0)
                    self.assertIs(self.writer.owner, job)
                    self.assertFalse(answers)
                    c.leave_native_preparation(call, preparation)
                    result = c.finish_native_commit(call, {"undo_published": False})
                    self.assertEqual(result["error_code"], "READ_ONLY" if revoke == "read_only" else "CANCELLED")
                    self.assertEqual(len(answers), 1)

    def test_closed_delivery_during_precommit_preparation_refuses_before_apply(self):
        for phase in ("resume_stage", "prepare_commit"):
            with self.subTest(phase=phase):
                c = self.new_controller()
                answers, completions, owner, call, job, preparation = self.prepared_delivery(c)
                def close_delivery(stage):
                    self.assertTrue(call.committing)
                    self.assertFalse(call.mutation_started)
                    owner.close()
                    return stage if phase == "resume_stage" else None
                setattr(job.backend, phase, close_delivery)
                c.commit_native_job(call, self.executor, c.document, defer_finish=True)
                self.assertEqual(job.backend.commits, 0)
                self.assertFalse(call.mutation_started)
                self.assertEqual(answers, [])
                self.assertEqual(completions, [])
                self.assertIs(self.writer.owner, job)
                c.prepare_native_finish(call, preparation)
                c.leave_native_preparation(call, preparation)
                result = c.finish_native_commit(call, {"undo_published": False})
                self.assertIn("delivery ended", result["error"])
                self.assertTrue(result["partial_changes_possible"])
                self.assertNotIn("scene_changed", result)
                self.assertEqual(len(answers), 1)
                self.assertEqual(completions, [])
                self.assertIsNone(self.writer.owner)

    def test_prepared_completion_waits_for_native_truth_and_current_delivery(self):
        cases = (
            ("clean", {"undo_published": True}, False, True),
            ("publication_failed", {"undo_published": False}, False, False),
            ("finalization_failed", {"undo_published": True, "editor_finalization_error": "fixture failure"}, False, False),
            ("delivery_closed", {"undo_published": True}, True, False),
            ("delivery_replaced", {"undo_published": True}, True, False),
        )
        for label, outcome, close_delivery, certify in cases:
            with self.subTest(case=label):
                c = self.new_controller()
                answers, completions, owner, call, job, preparation = self.prepared_delivery(c)
                c.commit_native_job(call, self.executor, c.document, defer_finish=True)
                self.assertEqual(job.backend.commits, 1)
                self.assertEqual(answers, [])
                self.assertEqual(completions, [])
                if close_delivery:
                    owner.close()
                    self.assertFalse(c._end_closed_delivery(call))
                    if label == "delivery_replaced":
                        call.delivery_owner = DeliveryOwner(RequestFence.capture(c))
                c.prepare_native_finish(call, preparation)
                c.leave_native_preparation(call, preparation)
                self.assertIs(call.delivery_owner, owner)
                result = c.finish_native_commit(call, outcome)
                operation = {"tool": call.name, "arguments": call.arguments}
                self.assertEqual(any(value.verified(operation, result) for value in completions), certify)
                self.assertEqual(len(answers), 1)
                self.assertIs(answers[0], result)
                self.assertEqual(result["status"], "committed" if label in {"clean", "delivery_closed", "delivery_replaced"} else "failed")
                self.assertEqual(job.backend.commits, 1)
                self.assertIsNone(self.writer.owner)
                c.finish_native_commit(call, outcome)
                self.assertEqual(len(answers), 1)

    def test_writer_release_is_once_for_preapply_cancel_and_begin_failure(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                c = self.new_controller()
                answers, call, job, preparation = self.prepared(c)
                with patch.object(self.writer, "release", wraps=self.writer.release) as release:
                    if cancelled:
                        c.stop()
                    c.leave_native_preparation(call, preparation, error=None if cancelled else RuntimeError("begin failed"))
                    c.finish_native_commit(call, {"undo_published": False})
                    c.finish_native_commit(call, {"undo_published": False})
                    release.assert_called_once_with(job)
                self.assertEqual(len(answers), 1)

    def test_hydration_cannot_change_the_original_approval_fingerprint(self):
        answers, call, job, preparation = self.prepared()
        approved = call.precondition
        def hydrate(stage):
            self.executor.fingerprint = "changed by hydration callback"
            return stage
        job.backend.resume_stage = hydrate
        self.c.commit_native_job(call, self.executor, self.c.document, defer_finish=True)
        result = self.close(call, preparation)
        self.assertEqual(result["error_code"], "STALE_CONTEXT")
        self.assertEqual(call.precondition, approved)
        self.assertEqual(job.backend.commits, 0)
        self.assertFalse(call.mutation_started)

    def test_other_chat_cannot_start_writer_until_native_close(self):
        answers, call, job, preparation = self.prepared()
        self.c.commit_native_job(call, self.executor, self.c.document, defer_finish=True)
        other = self.new_controller()
        other_answers, other_call = self.enqueue(other)
        self.pump(other, commit=False)
        self.assertFalse(other.native_jobs)
        self.assertIs(self.writer.owner, job)
        self.close(call, preparation, published=True)
        other_job = self.ready(other)
        self.assertIs(self.writer.owner, other_job)
        self.assertFalse(other_answers)

    def test_provider_finished_event_revokes_preapply_without_event_pump(self):
        answers, call, job, preparation = self.prepared()
        self.c.runtime_context().emit("finished", {"status": "completed"})
        self.assertTrue(call.revoked)
        self.c.commit_native_job(call, self.executor, self.c.document, defer_finish=True)
        result = self.close(call, preparation)
        self.assertEqual(job.backend.commits, 0)
        self.assertEqual(result["error_code"], "CANCELLED")
        self.assertEqual(len(answers), 1)

    def test_turn_end_does_not_revoke_legacy_execution_or_typed_apply(self):
        answers, call, job, preparation = self.prepared()
        call.mutation_started = True
        revoke_turn_calls(self.c, RequestFence.capture(self.c))
        self.assertFalse(call.revoked)
        call.staging = False
        call.mutation_started = False
        revoke_turn_calls(self.c, RequestFence.capture(self.c))
        self.assertFalse(call.revoked)
        self.close(call, preparation, error=RuntimeError("fixture abandons"))

    def test_pure_admission_refusal_does_not_invent_callback_effects(self):
        answers, call = self.enqueue()
        job = self.ready()
        call.approved = False
        preparation = self.c.enter_native_preparation(call, self.executor, self.c.document)
        self.assertFalse(preparation.callbacks_started)
        self.assertFalse(call.preparation_effects_possible)
        result = self.close(call, preparation)
        self.assertFalse(result["scene_changed"])
        self.assertFalse(result["partial_changes_possible"])
        self.assertEqual(job.backend.commits, 0)
        self.assertEqual(len(answers), 1)

    def test_preparation_result_encoding_failure_keeps_checkpoint_uncertainty(self):
        answers, call, job, preparation = self.prepared()
        self.c.request_checkpoint = "pre-request-checkpoint"
        self.c.finish_tool(call, {"error": "refusal", "error_code": "CANCELLED", "invalid": object()})
        result = self.close(call, preparation)
        self.assertTrue(result["serialization_failed"])
        self.assertTrue(result["partial_changes_possible"])
        self.assertFalse(result["mutation_started"])
        self.assertEqual(result["request_checkpoint"], "pre-request-checkpoint")
        self.assertEqual(len(answers), 1)

    def test_reentrant_finish_during_cleanup_does_not_repeat_cleanup_or_delivery(self):
        answers, call, job, preparation = self.prepared()
        discard = job.backend.discard
        def reentrant_discard(stage):
            self.c.finish_tool(call, {"error": "nested completion", "error_code": "CANCELLED"})
            discard(stage)
        job.backend.discard = reentrant_discard
        result = self.close(call, preparation, error=RuntimeError("begin failed"))
        self.assertEqual(result["error"], "begin failed")
        self.assertEqual(len(answers), 1)
        self.assertEqual(job.backend.discards, 1)

    def test_accepted_preview_uses_owned_binding_without_reloading_consumed_record(self):
        answers, preview_call = self.enqueue(mode="PREVIEW")
        for _ in range(40):
            self.pump()
            if answers:
                break
        preview_id = answers[0]["preview_id"]
        descriptor = self.c.inspect_native_preview(preview_id)
        self.c.busy = False
        self.assertTrue(self.c.accept_native_preview(preview_id, self.executor, self.c.document, descriptor["preview_token"]))
        call = next(iter(self.c.pending.values()))
        job = self.ready()
        self.assertNotIn(preview_id, self.previews.records)
        with patch.object(self.executor, "precondition", side_effect=AssertionError("preview record was already consumed")):
            preparation = self.c.enter_native_preparation(call, self.executor, self.c.document)
            self.assertIsNone(preparation.result)
            self.c.commit_native_job(call, self.executor, self.c.document, defer_finish=True)
        result = self.close(call, preparation, published=True)
        self.assertEqual(result["status"], "committed")
        self.assertEqual(job.backend.commits, 1)


if __name__ == "__main__":
    unittest.main()
