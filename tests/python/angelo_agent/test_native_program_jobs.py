# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Staged job scheduling and exact intent/cancellation contracts, no bpy needed."""

import copy
import math
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.program_jobs import ProgramError, ProgramJob, PreviewStore, WriterLease, WriterBusyError
from angelo_agent.program_spec import validate_program, PROGRAM_TOOLS, PROGRAM_NAMES

HANDLE = "scn:" + "a" * 32 + ":1"


def program(mode="APPLY", checks=None):
    return validate_program({"mode": mode, "read_set": [], "write_set": [{"handle": HANDLE, "revision": "a" * 64}],
                             "operations": [{"kind": "transform", "target": HANDLE, "parameters": {"location": [2, 3, 4]}}],
                             "expected_checks": checks or []})


class Backend:
    def __init__(self):
        self.source = {HANDLE: {"location": [0, 0, 0], "material": {"color": [1, 0, 0]},
                                "action": {"keys": [1, 2]}, "modifier": {"width": .1}}}
        self.revision = 1
        self.epoch = 1
        self.bound_epoch = 1
        self.discards = 0
        self.commits = 0
        self.calls = []
        self.fail = ""

    def ensure_main_thread(self):
        pass

    def _record(self, name):
        self.calls.append(name)
        if self.fail == name:
            raise ProgramError("intentional " + name)

    def check_context(self, binding):
        self._record("context")
        if self.epoch != self.bound_epoch:
            raise ProgramError("document changed", code="STALE_CONTEXT")

    def bind(self, program):
        self._record("bind")
        return {"handles": (HANDLE,), "revision": self.revision}

    def create_stage(self, binding):
        self._record("stage")
        return {"copies": {}}

    def copy_object(self, stage, handle):
        self._record("copy")
        stage["copies"][handle] = copy.deepcopy(self.source[handle])

    def finish_stage(self, stage):
        self._record("remap")

    def apply(self, stage, operation):
        self._record("apply")
        stage["copies"][HANDLE]["location"] = operation["parameters"]["location"]
        return {"changed": True}

    def validate(self, stage, check):
        self._record("check")
        return {"passed": stage["copies"][HANDLE]["location"] == check["expected"], "kind": "transform"}

    def validate_stage(self, stage):
        self._record("integrity")

    def delta(self, stage):
        self._record("delta")
        return {"summary": "Moved one object", "location": stage["copies"][HANDLE]["location"]}

    def verify(self, binding):
        self._record("verify")
        if binding["revision"] != self.revision:
            raise ProgramError("stale revision", code="STALE_REVISION")

    def commit(self, stage):
        self._record("commit")
        self.commits += 1
        self.source = copy.deepcopy(stage["copies"])
        self.discard(stage)
        return {"verified": True}

    def discard(self, stage):
        self.discards += 1
        stage.clear()


def finish(job):
    for _ in range(100):
        result = job.poll()
        if result.get("final") or result.get("ready_to_commit"):
            return result
    raise AssertionError("job did not settle")


class ProgramTests(unittest.TestCase):
    def setUp(self):
        self.backend, self.writer, self.previews = Backend(), WriterLease(), PreviewStore()

    def job(self, mode="APPLY", checks=None):
        return ProgramJob(self.backend, program(mode, checks), owner="chat-a", writer=self.writer, previews=self.previews)

    def test_closed_schema_and_deep_copies(self):
        value = program()
        self.assertEqual(set(PROGRAM_NAMES), {tool["name"] for tool in PROGRAM_TOOLS})
        value["operations"][0]["parameters"]["location"][0] = 6
        self.assertEqual(program()["operations"][0]["parameters"]["location"], [2, 3, 4])
        for field, invalid in (("mode", "PYTHON"), ("mode", {}), ("read_set", {}), ("operations", [])):
            source = program(); source[field] = invalid
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_program(source)
        for bad in ([math.nan, 0, 0], [True, 0, 0], [100001, 0, 0]):
            source = program(); source["operations"][0]["parameters"]["location"] = bad
            with self.assertRaises(ValueError):
                validate_program(source)

    def test_aliases_order_and_dependencies_are_explicit(self):
        value = {"mode": "PREVIEW", "read_set": [], "write_set": [], "operations": [
            {"kind": "create_primitive", "target": None, "parameters": {"primitive": "CUBE", "alias": "body"}},
            {"kind": "transform", "target": "$body", "parameters": {"location": [1, 2, 3]}}]}
        validate_program(value)
        value["operations"].reverse()
        with self.assertRaisesRegex(ValueError, "forward"):
            validate_program(value)
        value = program(); value["write_set"] = []
        with self.assertRaisesRegex(ValueError, "read or write"):
            validate_program(value)
        value = program(); value["read_set"] = value["write_set"]; value["write_set"] = []
        with self.assertRaisesRegex(ValueError, "write set"):
            validate_program(value)
        value = program(); value["operations"] *= 33
        with self.assertRaisesRegex(ValueError, "32"):
            validate_program(value)

    def test_poll_never_commits_and_one_explicit_commit(self):
        job = self.job()
        source = copy.deepcopy(self.backend.source)
        intent = finish(job)
        self.assertTrue(intent["ready_to_commit"])
        self.assertFalse(intent["final"])
        self.assertEqual(self.backend.source, source)
        for _ in range(3):
            self.assertTrue(job.poll()["ready_to_commit"])
        self.assertEqual(self.backend.commits, 0)
        result = job.commit()
        self.assertEqual(result["status"], "committed")
        self.assertTrue(result["undoable"])
        self.assertEqual(self.backend.source[HANDLE]["location"], [2, 3, 4])
        job.commit(); job.cancel()
        self.assertEqual(self.backend.commits, 1)
        self.assertEqual(self.backend.source[HANDLE]["location"], [2, 3, 4])
        self.assertIsNone(self.writer.owner)

    def test_cancel_at_every_safe_boundary_preserves_original(self):
        for polls in range(8):
            with self.subTest(polls=polls):
                self.setUp()
                job = self.job()
                source = copy.deepcopy(self.backend.source)
                for _ in range(polls):
                    job.poll()
                result = job.cancel()
                self.assertTrue(result["cancelled"])
                self.assertFalse(result["scene_changed"])
                self.assertEqual(source, self.backend.source)
                self.assertEqual(self.backend.commits, 0)
                self.assertIsNone(self.writer.owner)

    def test_shared_mutable_dependencies_are_isolated(self):
        job = self.job()
        job.poll(); job.poll()
        for family, field in (("material", "color"), ("action", "keys")):
            job.stage["copies"][HANDLE][family][field][0] = 9
        job.stage["copies"][HANDLE]["modifier"]["width"] = 9
        self.assertEqual(self.backend.source[HANDLE]["material"]["color"], [1, 0, 0])
        self.assertEqual(self.backend.source[HANDLE]["action"]["keys"], [1, 2])
        self.assertEqual(self.backend.source[HANDLE]["modifier"]["width"], .1)
        job.cancel()

    def test_stale_after_staging_and_again_immediately_before_commit(self):
        job = self.job(); finish(job)
        self.backend.revision += 1
        self.backend.source[HANDLE]["location"] = [9, 9, 9]
        result = job.commit()
        self.assertFalse(result["success"])
        self.assertFalse(result["partial_changes_possible"])
        self.assertEqual(self.backend.source[HANDLE]["location"], [9, 9, 9])
        self.assertEqual(self.backend.commits, 0)
        self.assertIsNone(self.writer.owner)

    def test_scene_or_reload_change_cancels_safely(self):
        job = self.job(); job.poll()
        self.backend.epoch += 1
        result = job.poll()
        self.assertTrue(result["final"])
        self.assertIn("document changed", result["error"])
        self.assertEqual(self.backend.discards, 1)
        self.assertIsNone(self.writer.owner)

    def test_unknown_failure_each_phase_cleans_without_claiming_restoration(self):
        for phase in ("bind", "stage", "copy", "remap", "apply", "integrity", "delta", "verify", "commit"):
            with self.subTest(phase=phase):
                self.setUp()
                job = self.job()
                self.backend.fail = phase
                source = copy.deepcopy(self.backend.source)
                result = finish(job)
                if result.get("ready_to_commit"):
                    result = job.commit()
                self.assertFalse(result["success"])
                self.assertTrue(result["partial_changes_possible"])
                self.assertNotIn("scene_changed", result)
                self.assertEqual(source, self.backend.source)
                self.assertIsNone(self.writer.owner)

    def test_mandatory_integrity_and_expected_checks_cannot_be_skipped(self):
        check = {"kind": "transform", "target": HANDLE, "property": "location", "expected": [8, 8, 8], "tolerance": .0001}
        job = self.job(checks=[check]); result = finish(job)
        self.assertFalse(result["success"])
        self.assertIn("Expected check failed", result["error"])
        self.assertEqual(self.backend.commits, 0)
        self.setUp(); job = self.job(); finish(job)
        self.assertIn("integrity", self.backend.calls)
        job.cancel()

    def test_verified_rollback_keeps_failure_code_and_unchanged_scene(self):
        for code in ("UNKNOWN", "INTERNAL_ERROR"):
            for prepared in (False, True):
                with self.subTest(code=code, prepared=prepared):
                    self.setUp()
                    job = self.job(); finish(job)
                    before = copy.deepcopy(self.backend.source)
                    failure = ProgramError("transfer failed", code=code)
                    failure.rollback_restored = True
                    owner = object() if prepared else None
                    if prepared:
                        job.retain_native_preparation(owner)
                    def readback(_stage):
                        self.assertEqual(self.backend.discards, 1)
                        self.assertEqual(self.backend.source, before)
                    with patch.object(self.backend, "commit", side_effect=failure), \
                            patch.object(self.backend, "verify_rollback", side_effect=readback, create=True) as verify:
                        result = job.commit()
                    self.assertFalse(result["success"])
                    self.assertEqual(result["status"], "failed")
                    self.assertEqual(result["error_code"], code)
                    self.assertFalse(result["scene_changed"])
                    self.assertFalse(result["partial_changes_possible"])
                    self.assertTrue(result["rollback_verified"])
                    self.assertNotIn("recovery", result)
                    verify.assert_called_once()
                    if prepared:
                        self.assertIs(self.writer.owner, job)
                        job.release_native_preparation(owner)
                    self.assertIsNone(self.writer.owner)

    def test_rollback_readback_detects_a_cleanup_callback_changing_live_state(self):
        job = self.job(); finish(job)
        failure = ProgramError("transfer failed", code="INTERNAL_ERROR")
        failure.rollback_restored = True
        discard = self.backend.discard
        def changed_during_cleanup(stage):
            discard(stage)
            self.backend.source[HANDLE]["location"] = [9, 9, 9]
        def readback(_stage):
            if self.backend.source[HANDLE]["location"] != [0, 0, 0]:
                raise ProgramError("original binding no longer matches", code="STALE_REVISION")
        with patch.object(self.backend, "commit", side_effect=failure), \
                patch.object(self.backend, "discard", side_effect=changed_during_cleanup), \
                patch.object(self.backend, "verify_rollback", side_effect=readback, create=True):
            result = job.commit()
        self.assertFalse(result["success"])
        self.assertTrue(result["partial_changes_possible"])
        self.assertNotIn("scene_changed", result)
        self.assertNotIn("rollback_verified", result)
        self.assertIn("original binding", result["rollback_error"])
        self.assertEqual(self.backend.source[HANDLE]["location"], [9, 9, 9])

    def test_reverse_remap_users_are_verified_after_owned_stage_removal(self):
        job = self.job(); finish(job)
        users = ["artist object", "temporary stage copy"]
        failure = ProgramError("material transfer failed", code="UNKNOWN")
        failure.rollback_restored = True
        discard = self.backend.discard
        def remove_private_users(stage):
            discard(stage)
            users.remove("temporary stage copy")
        def readback(_stage):
            if users != ["artist object"]:
                raise ProgramError("material users differ from original binding", code="STALE_DEPENDENCY")
        with patch.object(self.backend, "commit", side_effect=failure), \
                patch.object(self.backend, "discard", side_effect=remove_private_users), \
                patch.object(self.backend, "verify_rollback", side_effect=readback, create=True):
            result = job.commit()
        self.assertFalse(result["success"])
        self.assertFalse(result["scene_changed"])
        self.assertFalse(result["partial_changes_possible"])
        self.assertTrue(result["rollback_verified"])

    def test_classified_private_teardown_keeps_no_live_apply_and_owned_residue_distinct(self):
        job = self.job()
        source = copy.deepcopy(self.backend.source)
        failure = ProgramError("private stage suspension failed", code="CLEANUP_FAILED")
        failure.cleanup_error = "owned stage removal failed"
        failure.cleanup_pending = failure.partial_changes_possible = True
        self.backend.cleanup_pending = True
        with patch.object(self.backend, "suspend_stage", side_effect=failure, create=True), \
                patch.object(self.backend, "discard", side_effect=RuntimeError("owned stage removal failed")):
            result = job.poll()
        self.assertFalse(result["success"])
        self.assertFalse(result["scene_changed"])
        self.assertTrue(result["cleanup_warning"])
        self.assertTrue(result["cleanup_pending"])
        self.assertTrue(result["partial_changes_possible"])
        self.assertEqual(self.backend.source, source)
        self.assertEqual(self.backend.commits, 0)
        self.assertIsNone(job.stage)
        self.assertIsNone(self.writer.owner)

    def _hydration_failure(self, cause):
        from angelo_agent.program_storage import _hydration_cleanup_error
        cleanup = ProgramError("Native stage teardown failed; temporary data cleanup is pending", code="CLEANUP_FAILED")
        cleanup.cleanup_error, cleanup.cleanup_pending = str(cleanup), True
        cleanup.temporary_ids_remaining, cleanup.partial_changes_possible = 2, True
        job = self.job()
        job.poll()  # PREPARE leaves a stage that the next poll hydrates.
        self.backend.cleanup_pending = True
        with patch.object(self.backend, "resume_stage", side_effect=_hydration_cleanup_error(cause, cleanup), create=True):
            return job.poll()

    def test_unclassified_hydration_cause_never_claims_an_unchanged_scene(self):
        source = copy.deepcopy(self.backend.source)
        for cause in (RuntimeError("injected native resume validation"),
                      ProgramError("internal stage invariant", code="INTERNAL_ERROR")):
            with self.subTest(cause=type(cause).__name__):
                self.setUp()
                result = self._hydration_failure(cause)
                self.assertFalse(result["success"])
                self.assertEqual(result["error_code"], "CLEANUP_FAILED")
                self.assertIn(result["refusal_details"]["cause_code"], {"UNKNOWN", "INTERNAL_ERROR"})
                self.assertNotIn("scene_changed", result)  # No live fingerprint was read on this path.
                self.assertTrue(result["partial_changes_possible"])
                self.assertTrue(result["cleanup_pending"])
                self.assertEqual(self.backend.source, source)
                self.assertEqual(self.backend.commits, 0)

    def test_classified_hydration_cause_keeps_its_class_and_private_staging_fact(self):
        result = self._hydration_failure(ProgramError("target changed", code="STALE_REVISION"))
        self.assertEqual(result["error_code"], "CLEANUP_FAILED")
        self.assertEqual(result["refusal_details"]["cause_code"], "STALE_REVISION")
        self.assertIs(result["scene_changed"], False)
        self.assertTrue(result["partial_changes_possible"])

    def test_classified_error_without_verified_rollback_cannot_assert_unchanged_scene(self):
        job = self.job(); finish(job)
        failure = ProgramError("transfer refused", code="VALIDATION")
        failure.rollback_restored = True
        with patch.object(self.backend, "commit", side_effect=failure), \
                patch.object(self.backend, "verify_rollback", side_effect=RuntimeError("restored paths differ"), create=True):
            result = job.commit()
        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "VALIDATION")
        self.assertTrue(result["partial_changes_possible"])
        self.assertNotIn("scene_changed", result)
        self.assertNotIn("rollback_verified", result)

    def test_rollback_evidence_cannot_hide_failed_cleanup_or_remaining_mutation(self):
        for issue in ("cleanup_error", "cleanup_pending", "mutation_started", "partial_changes_possible"):
            with self.subTest(issue=issue):
                self.setUp()
                job = self.job(); finish(job)
                failure = ProgramError("transfer failed", code="INTERNAL_ERROR")
                failure.rollback_restored = True
                if issue == "partial_changes_possible":
                    failure.partial_changes_possible = True
                elif issue != "cleanup_error":
                    setattr(self.backend, issue, True)
                def discard(stage):
                    if issue == "cleanup_error":
                        raise RuntimeError("owned stage removal failed")
                    stage.clear()
                with patch.object(self.backend, "commit", side_effect=failure), \
                        patch.object(self.backend, "discard", side_effect=discard), \
                        patch.object(self.backend, "verify_rollback", create=True) as verify:
                    result = job.commit()
                self.assertFalse(result["success"])
                self.assertTrue(result["partial_changes_possible"])
                self.assertNotIn("rollback_verified", result)
                self.assertIn("recovery", result)
                verify.assert_not_called()
                if issue == "cleanup_error":
                    self.assertTrue(result["cleanup_warning"])
                    self.assertNotIn("scene_changed", result)

    def test_arbitrary_failure_cannot_certify_rollback_from_text_or_attributes(self):
        for failure in (RuntimeError("live state rolled back"), ProgramError("live state rolled back", code="UNKNOWN")):
            with self.subTest(error=type(failure).__name__):
                self.setUp()
                job = self.job(); finish(job)
                if type(failure) is RuntimeError:
                    failure.rollback_restored = True
                with patch.object(self.backend, "commit", side_effect=failure), \
                        patch.object(self.backend, "verify_rollback", create=True) as verify:
                    result = job.commit()
                self.assertFalse(result["success"])
                self.assertTrue(result["partial_changes_possible"])
                self.assertNotIn("scene_changed", result)
                self.assertNotIn("rollback_verified", result)
                verify.assert_not_called()

    def test_single_writer_across_chats_but_retained_previews_release_lease(self):
        job = self.job()
        with self.assertRaises(WriterBusyError):
            self.job()
        job.cancel()
        preview = self.job("PREVIEW"); result = finish(preview)
        self.assertEqual(result["status"], "preview")
        self.assertIsNone(self.writer.owner)
        other = self.job()
        with self.assertRaises(WriterBusyError):
            ProgramJob.accept(result["preview_id"], "chat-a", previews=self.previews, writer=self.writer)
        self.assertEqual(len(self.previews.records), 1)
        other.cancel()
        accepted = ProgramJob.accept(result["preview_id"], "chat-a", previews=self.previews, writer=self.writer)
        self.assertTrue(finish(accepted)["ready_to_commit"])
        self.assertEqual(accepted.commit()["status"], "committed")

    def test_preview_owner_discard_eviction_and_expiry(self):
        self.previews.limit = 1
        result = finish(self.job("PREVIEW"))
        identity = result["preview_id"]
        with self.assertRaisesRegex(ProgramError, "conversation"):
            self.previews.get(identity, "chat-b")
        finish(self.job("PREVIEW"))
        with self.assertRaisesRegex(ProgramError, "expired"):
            self.previews.get(identity, "chat-a")
        self.assertEqual(self.backend.discards, 1)
        current = next(iter(self.previews.records))
        self.assertTrue(self.previews.discard(current, "chat-a")["success"])
        self.assertEqual(self.backend.discards, 2)
        result = finish(self.job("PREVIEW"))
        self.previews.clock = lambda: 1e20
        self.previews.expire()
        self.assertEqual(len(self.previews.records), 0)

    def test_preview_reports_its_declared_live_revisions_and_accept_returns_the_commit_outcome(self):
        class RevisionBackend(Backend):
            def bind(self, program):
                return dict(super().bind(program), revisions={HANDLE: "a" * 64})

            def commit(self, stage):
                return dict(super().commit(stage), live_revisions={"status": "COMMITTED", "objects": []})

        self.backend = RevisionBackend()
        result = finish(self.job("PREVIEW"))
        self.assertEqual(result["live_revisions"]["status"], "UNCHANGED_UNTIL_ACCEPT")
        self.assertEqual(result["live_revisions"]["objects"], [{"handle": HANDLE, "object_revision": "a" * 64, "role": "DECLARED"}])
        # Early in the result, so bounded transcript handoffs keep it.
        self.assertLess(list(result).index("live_revisions"), list(result).index("delta"))
        accepted = ProgramJob.accept(result["preview_id"], "chat-a", previews=self.previews, writer=self.writer)
        self.assertTrue(finish(accepted)["ready_to_commit"])
        self.assertEqual(accepted.commit()["live_revisions"]["status"], "COMMITTED")
        # A backend without exact bound revisions never invents them.
        self.backend = Backend()
        self.assertNotIn("live_revisions", finish(self.job("PREVIEW")))

    def test_accept_manual_edit_does_not_overwrite(self):
        result = finish(self.job("PREVIEW"))
        self.backend.revision += 1
        self.backend.source[HANDLE]["location"] = [7, 7, 7]
        job = ProgramJob.accept(result["preview_id"], "chat-a", previews=self.previews, writer=self.writer)
        final = job.poll()
        self.assertFalse(final["success"])
        self.assertEqual(len(self.previews.records), 0)
        self.assertEqual(self.backend.source[HANDLE]["location"], [7, 7, 7])
        self.assertIsNone(self.writer.owner)

    def test_global_expected_checks_revalidated_on_accept_and_commit(self):
        check = {"kind": "object_count", "minimum": 0, "maximum": 5}
        current = [True]
        self.backend.validate = lambda stage, check: {"passed": current[0], "kind": "object_count"}
        result = finish(self.job("PREVIEW", checks=[check]))
        current[0] = False
        accepted = ProgramJob.accept(result["preview_id"], "chat-a", previews=self.previews, writer=self.writer)
        self.assertFalse(finish(accepted)["success"])
        self.assertEqual(self.backend.commits, 0)
        current[0] = True
        job = self.job(checks=[check]); finish(job)
        current[0] = False
        self.assertIn("changed before commit", job.commit()["error"])
        self.assertEqual(self.backend.commits, 0)

    def test_no_stage_ids_survive_any_progress_or_intent_boundary(self):
        class SuspendingBackend(Backend):
            resident = False

            def create_stage(self, binding):
                self.resident = True
                return super().create_stage(binding)

            def suspend_stage(self, stage):
                if "suspended" in stage:
                    return stage
                self.resident = False
                return {"suspended": copy.deepcopy(stage)}

            def resume_stage(self, stage):
                if "suspended" in stage:
                    self.resident = True
                    return copy.deepcopy(stage["suspended"])
                return stage

            def discard(self, stage):
                self.resident = False
                super().discard(stage)

        self.backend = SuspendingBackend()
        job = self.job()
        for _ in range(20):
            result = job.poll()
            self.assertFalse(self.backend.resident)
            self.assertIn("suspended", job.stage)
            if result.get("ready_to_commit"):
                break
        self.assertTrue(job.commit()["success"])
        self.assertFalse(self.backend.resident)

    def test_error_after_live_mutation_never_claims_no_changes(self):
        class PartialBackend(Backend):
            def commit(self, stage):
                self.source = copy.deepcopy(stage["copies"])
                self.mutation_started = True
                raise RuntimeError("post-write cleanup fault")

        self.backend = PartialBackend()
        job = self.job(); finish(job)
        result = job.commit()
        self.assertTrue(result["scene_changed"])
        self.assertTrue(result["partial_changes_possible"])
        self.assertTrue(result["mutation_started"])
        self.assertIn("durable", result["recovery"])

    def test_wrong_thread_guard_never_drops_stage_or_writer_ownership(self):
        job = self.job(); finish(job)
        stage = job.stage
        def wrong_thread():
            raise ProgramError('main thread only')
        self.backend.ensure_main_thread = wrong_thread
        for callback in (job.poll, job.commit, job.cancel):
            with self.assertRaisesRegex(ProgramError, 'main thread'):
                callback()
            self.assertIs(job.stage, stage)
            self.assertIs(self.writer.owner, job)
            self.assertFalse(job.closed)
        self.backend.ensure_main_thread = lambda: None
        job.cancel()

    def test_ready_preparation_does_not_repeat_copy_remap_cycles(self):
        class PreparedBackend(Backend):
            prepared_stage_ready = True
            def create_stage(self, binding):
                stage = super().create_stage(binding)
                self.copy_object(stage, HANDLE)
                self.finish_stage(stage)
                return stage
        self.backend = PreparedBackend()
        job = self.job()
        result = job.poll()
        self.assertEqual(result['progress']['phase'], 'OPERATIONS')
        finish(job)
        self.assertEqual(self.backend.calls.count('copy'), 1)
        self.assertEqual(self.backend.calls.count('remap'), 1)
        job.cancel()

    def test_cleanup_ownership_survives_failure_and_never_claims_reused_ids(self):
        from angelo_agent import program_storage as storage
        block = SimpleNamespace(session_uid=91, id_type='OBJECT')
        current = {123: block}
        def failing_remove(**_):
            raise RuntimeError('native cleanup unavailable')
        data = SimpleNamespace(batch_remove=failing_remove)
        with patch.object(storage, '_main_ids', side_effect=lambda: dict(current)), patch.object(storage, '_bpy', return_value=SimpleNamespace(data=data)):
            storage.retain_cleanup({123: (91, 'OBJECT')})
            self.assertTrue(storage.cleanup_pending())
            self.assertTrue(storage.has_pending_cleanup())
            # A pointer reused by another Main ID is not owned by the residue.
            current[123] = SimpleNamespace(session_uid=92, id_type='OBJECT')
            self.assertFalse(storage.cleanup_pending())
            self.assertIn(123, current)
            storage.retain_cleanup({123: (92, 'OBJECT')})
            data.batch_remove = lambda **_: current.clear()
            self.assertFalse(storage.cleanup_pending())
            self.assertFalse(storage.has_pending_cleanup())

    def test_opt_in_quanta_bound_cheap_actions_time_and_hydration_separately(self):
        class QuantumBackend(Backend):
            coalesce_operations = True
            def __init__(self, cost=.0001):
                super().__init__()
                self.now, self.cost, self.actions, self.fail_at = 0., cost, 0, 0
            def operation_quantum_clock(self):
                return self.now
            def resume_stage(self, stage):
                self.now += 1.  # Deliberately slow hydration cannot consume action budget.
                return stage
            def suspend_stage(self, stage):
                return stage
            def apply(self, stage, operation):
                result = super().apply(stage, operation)
                self.now += self.cost
                self.actions += 1
                if self.actions == self.fail_at:
                    raise ProgramError('third staged action failed')
                return result

        def setup_backend(cost=.0001):
            backend = QuantumBackend(cost)
            value = program()
            value['operations'] *= 12
            job = ProgramJob(backend, value, owner='chat-a', writer=self.writer, previews=self.previews)
            for _ in range(3):
                job.poll()
            self.assertEqual(job.phase, 'OPERATIONS')
            return backend, job

        backend, job = setup_backend()
        result = job.poll()
        self.assertEqual(backend.actions, 8)
        self.assertEqual(result['progress']['operation_index'], 8)
        self.assertEqual(backend.source[HANDLE]['location'], [0,0,0])
        result = job.poll()
        self.assertEqual(backend.actions, 12)
        self.assertEqual(result['progress']['operation_index'], 12)
        job.cancel()
        backend, job = setup_backend(.006)
        job.poll()
        self.assertEqual(backend.actions, 1)  # Always one, even when it alone exceeds 4 ms.
        job.cancel()
        backend, job = setup_backend(.0021)
        job.poll()
        self.assertEqual(backend.actions, 2)
        job.cancel()
        backend, job = setup_backend()
        backend.fail_at = 3
        result = job.poll()
        self.assertEqual(backend.actions, 3)
        self.assertFalse(result['success'])
        self.assertEqual(len(job.operation_results), 2)
        self.assertEqual(backend.source[HANDLE]['location'], [0,0,0])
        self.assertIsNone(self.writer.owner)
        backend, job = setup_backend()
        job.poll(); result = job.cancel()
        self.assertTrue(result['cancelled'])
        self.assertEqual(backend.actions, 8)
        self.assertEqual(backend.source[HANDLE]['location'], [0,0,0])


if __name__ == "__main__":
    unittest.main()
