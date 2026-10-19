# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Adversarial regressions for shared wait time and truthful terminal job results."""

import json
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch

import test_native_view_wait as waits
import test_native_view_wait_delivery as delivery
from angelo_agent import view_wait
from angelo_agent.controller import ToolDispatchBudget


class SharedBudgetTests(waits.WaitCase):
    def test_separate_controllers_with_same_session_have_separate_accounts(self):
        first = self.show()
        self.advance(200)
        second_controller = SimpleNamespace(state=dict(self.controller.state),
            _epoch=self.controller._epoch, _request_generation=self.controller._request_generation)
        second = view_wait.begin(first.manager, self.records["j3"], Mock(), second_controller)
        self.assertIsNot(first._wait_account, second._wait_account)
        self.assertEqual(view_wait.allowance(self.controller), 400)
        self.assertEqual(view_wait.allowance(second_controller), 600)
        first.cancel()
        second.cancel()

    def test_session_change_replaces_account_without_late_cleanup_debit(self):
        old = self.show()
        old_account = old._wait_account
        self.advance(200)
        self.controller.state["id"] = "replacement-session"
        self.assertEqual(view_wait.allowance(self.controller), 600)
        new_account = self.controller._render_wait_account
        self.assertIsNot(old_account, new_account)
        self.advance(50)
        old.cancel()
        self.assertFalse(old_account["active"])
        self.assertIsNone(old._wait_account)
        self.assertIsNone(old._wait_key)
        self.assertEqual(view_wait.allowance(self.controller), 600)
        self.assertIs(self.controller._render_wait_account, new_account)

    def test_completed_wait_releases_its_token_and_old_account_reference(self):
        pending = self.show()
        account, token = pending._wait_account, pending._wait_key
        self.assertIn(token, account["active"])
        self.advance(12)
        self.finish()
        pending.poll()
        self.assertNotIn(token, account["active"])
        self.assertIsNone(pending._wait_account)
        self.assertIsNone(pending._wait_key)

    def test_cancel_caches_budget_once_and_never_charges_idle_time(self):
        pending = self.show()
        self.advance(20)
        self.controller.document_invalid = True
        pending.cancel()
        first = pending.stopped_result
        self.assertEqual(first["waiting"]["waited_seconds"], 20)
        self.assertEqual(first["waiting"]["request_seconds_left"], 580)
        self.advance(800)
        pending.cancel()
        self.assertIs(pending.stopped_result, first)
        self.assertEqual(view_wait.allowance(self.controller), 580)
        self.assertIsNone(pending._wait_account)
        self.assertIsNone(pending._wait_key)

    def test_staggered_overlapping_waits_end_at_the_shared_deadline(self):
        first = self.show()
        self.advance(500)
        second = self.show()
        self.assertEqual(second.left, 100)
        self.advance(100)
        for pending in (second, first):
            self.assertEqual(pending.poll()["waiting"]["ended"], "wait_used")
        self.assertEqual(view_wait.allowance(self.controller), 0)

    def test_overlap_is_charged_once_and_idle_time_is_free(self):
        first = self.show()
        self.advance(100)
        second = self.show()
        self.advance(100)
        first.cancel()
        self.assertEqual(view_wait.allowance(self.controller), 400)
        self.advance(100)
        second.cancel()
        second.cancel()
        self.advance(200)
        self.assertEqual(view_wait.allowance(self.controller), 300)
        third = self.show()
        self.advance(300)
        self.assertEqual(third.poll()["waiting"]["ended"], "wait_used")

    def test_late_cleanup_cannot_charge_a_new_request(self):
        old = self.show()
        self.advance(200)
        self.controller._request_generation += 1
        current = self.show()
        self.advance(50)
        old.cancel()
        self.assertEqual(view_wait.allowance(self.controller), 550)
        current.cancel()
        self.advance(100)
        self.assertEqual(view_wait.allowance(self.controller), 550)


class TerminalResultTests(waits.WaitCase):
    def test_cancelled_wait_never_captures_on_a_late_poll(self):
        pending = self.show()
        pending.cancel()
        stopped = pending.stopped_result
        self.finish()
        with patch.object(self.finished, "shown", side_effect=AssertionError("late decode")):
            self.assertIs(pending.poll(), stopped)
            self.assertIs(pending.poll(), stopped)
        self.assertFalse(stopped.get("views"))

    def test_completed_wait_reuses_result_without_decoding_again(self):
        pending = self.show()
        self.finish()
        first = pending.poll()
        with patch.object(self.finished, "shown", side_effect=AssertionError("repeat decode")):
            self.assertIs(pending.poll(), first)
            pending.cancel()
            self.assertIs(pending.poll(), first)
        self.assertTrue(first["views"])

    def test_already_failed_job_preserves_its_public_reason(self):
        self.records["j3"].update(status="failed", reason="Output folder has no free disk space")
        result = self.show()
        self.assertNotIn("error", result)
        self.assertEqual(result["job"]["status"], "failed")
        self.assertEqual(result["waiting"]["waited_seconds"], 0)
        self.assertIn("Output folder has no free disk space", result["note"])

    def test_failure_reason_uses_the_same_bound_in_facts_and_note(self):
        reason = "Disk write failed: " + "x" * 280 + "private-trailing-diagnostic"
        pending = self.show()
        self.advance(1, status="failed", reason=reason)
        result = pending.poll()
        self.assertLessEqual(len(result["job"]["reason"]), 300)
        self.assertIn(result["job"]["reason"], result["note"])
        self.assertNotIn("private-trailing-diagnostic", result["note"])

    def test_retained_frame_is_complete_but_cancelled_job_is_not(self):
        self.records["j3"].update(kind="RENDER_ANIMATION", detail={
            "output_path": str(self.folder / "shot"), "frames_done": 0, "frames_total": 24})
        pending = self.show()
        self.file("shot/frame_0001.png", waits.picture(64, 36))
        self.advance(20, status="cancelled", reason="user", receipt={"kept": "1/24 frames"})
        self.records["j3"]["detail"]["frames_done"] = 1
        for result in (pending.poll(), self.show()):
            self.assertTrue(result["success"] and result["rendered"]["complete"])
            self.assertFalse(result["rendered"]["job_complete"])
            self.assertEqual(result["job"]["status"], "cancelled")
            self.assertEqual(result["job"]["reason"], "user")
            self.assertEqual(result["job"]["frames_done"], 1)
            self.assertIn("the person cancelled job j3", result["note"])
            self.assertIn("Do not start it again unless they ask", result["note"])
            self.assertNotIn("No picture", result["note"])
            self.assertEqual(self.delivered(result).shape, (36, 64, 4))

    def test_stop_reports_a_job_that_already_ended(self):
        pending = self.show()
        self.advance(20, status="interrupted", reason="file_closed")
        pending.cancel()
        result = pending.stopped_result
        self.assertEqual(result["job"]["status"], "interrupted")
        self.assertNotIn("job itself goes on", result["error"])

    def test_stop_distinguishes_stopping_and_completed_jobs(self):
        for status in ("stopping", "done"):
            with self.subTest(status=status):
                self.records["j3"]["status"] = "running"
                pending = self.show()
                self.advance(1, status=status)
                pending.cancel()
                result = pending.stopped_result
                self.assertEqual(result["job"]["status"], status)
                self.assertNotIn("job itself goes on", result["error"])
                self.assertNotIn("wrote nothing", result["error"])

    def test_already_cancelled_still_returns_its_reason_without_a_picture(self):
        self.records["j3"].update(status="cancelled", reason="time_budget")
        result = self.show()
        self.assertEqual(result["views"], [])
        self.assertEqual(result["job"]["status"], "cancelled")
        self.assertIn("stopped at its own time budget", result["note"])


class OwnershipTests(waits.WaitCase):
    def lost(self, change, *, cancelled=False):
        pending = self.show()
        change(pending)
        with patch.object(self.finished, "shown", side_effect=AssertionError("replacement decode")):
            if cancelled:
                pending.cancel()
                result = pending.stopped_result
            else:
                result = pending.poll()
        self.assertIn("error", result)
        self.assertEqual(result["waiting"]["ended"], "ownership_changed")
        self.assertNotIn("job", result)
        self.assertFalse(result["views"])
        self.assertNotIn("replacement-private-facts", json.dumps(result))
        self.assertIsNone(pending._wait_account)
        self.assertIsNone(pending._wait_key)
        return result

    def test_same_id_record_replacement_is_not_polled_or_decoded(self):
        def replace(pending):
            self.records["j3"] = dict(self.records["j3"], status="done", reason="replacement-private-facts")
        self.lost(replace)

    def test_in_place_record_identity_session_or_kind_change_is_refused(self):
        original = dict(self.records["j3"])
        for field, value in (("id", "j9"), ("session", "replacement-private-facts"), ("kind", "BAKE_TEXTURE")):
            with self.subTest(field=field):
                self.records["j3"] = dict(original)
                self.lost(lambda pending: self.records["j3"].update({field: value, "status": "done"}))

    def test_controller_session_change_does_not_describe_replacement_job(self):
        self.lost(lambda pending: self.controller.state.update(id="replacement-private-facts"))

    def test_cancel_after_record_replacement_never_reports_replacement_facts(self):
        self.lost(lambda pending: self.records.__setitem__("j3", dict(self.records["j3"],
            status="failed", reason="replacement-private-facts")), cancelled=True)

    def test_manager_replacement_refuses_even_if_it_returns_the_original_record(self):
        from angelo_agent import job_ui
        pending = self.show()
        replacement = SimpleNamespace(record=lambda identity: self.records.get(identity))
        with patch.object(job_ui, "manager", return_value=replacement), \
                patch.object(self.finished, "shown", side_effect=AssertionError("replacement decode")):
            result = pending.poll()
        self.assertEqual(result["waiting"]["ended"], "ownership_changed")
        self.assertNotIn("job", result)

    def test_missing_record_keeps_its_specific_result(self):
        pending = self.show()
        self.records.clear()
        result = pending.poll()
        self.assertIn("no longer on record", result["error"])
        self.assertNotIn("job", result)
        self.assertIsNone(pending._wait_account)

    def test_ownership_is_checked_again_immediately_before_decode(self):
        checks = iter((True, False))
        show = Mock(side_effect=AssertionError("decode after ownership ended"))
        record = self.records["j3"]
        manager = SimpleNamespace(record=lambda identity: record)
        pending = view_wait.JobWait(manager, record, show, self.controller, current=lambda: next(checks))
        record["status"] = "done"
        result = pending.poll()
        show.assert_not_called()
        self.assertEqual(result["waiting"]["ended"], "ownership_changed")

    def test_ownership_change_during_decode_discards_only_its_picture_and_keeps_actual_warnings(self):
        for wait in (False, True):
            with self.subTest(wait=wait):
                self.records["j3"]["status"] = "running"
                pending = self.show() if wait else None
                self.finish()
                earlier = self.references.new_observation("earlier picture")
                Path(earlier["path"]).write_bytes(b"earlier")
                warnings = [{"code": "render_settings_not_restored", "detail": "fixture actual warning"}]
                shown = self.finished.shown
                created = []
                def replaced(*args, **kwargs):
                    result = shown(*args, **kwargs)
                    created.extend(row["path"] for row in result["views"])
                    result["side_effects"] = warnings
                    self.records["j3"] = dict(self.records["j3"], reason="replacement-private-facts")
                    return result
                with patch.object(self.finished, "shown", side_effect=replaced):
                    result = pending.poll() if wait else self.show()
                self.assertEqual(result["waiting"]["ended"], "ownership_changed")
                self.assertIn("without returning a picture", result["error"])
                self.assertEqual(result["side_effects"], warnings)
                self.assertFalse(result["views"])
                self.assertNotIn("job", result)
                self.assertNotIn("rendered", result)
                self.assertNotIn("replacement-private-facts", json.dumps(result))
                self.assertTrue(created)
                self.assertTrue(all(not Path(path).exists() for path in created))
                self.assertTrue(Path(earlier["path"]).exists())

    def test_failed_ownership_before_begin_creates_no_budget_account(self):
        record = self.records["j3"]
        result = view_wait.begin(SimpleNamespace(), record, Mock(), self.controller, current=lambda: False)
        self.assertEqual(result["waiting"]["ended"], "ownership_changed")
        self.assertFalse(hasattr(self.controller, "_render_wait_account"))


class DecoderFailureOwnershipTests(waits.WaitCase):
    """Failure after real source copying/header admission must retain the same job ownership fence."""

    STATUSES = ("done", "failed", "cancelled", "interrupted")

    def exercise(self, *, pending_first, status, change=None):
        directory = self.folder / "animation"
        self.job(directory, kind="RENDER_ANIMATION", status="running", progress=0.4, reason="",
                 detail={"output_path": str(directory), "frames_done": 1, "frames_total": 3})
        pending = self.show(frame=1) if pending_first else None
        output = self.file("animation/frame_0001.png", waits.picture(64, 36))
        output_before = output.read_bytes()
        self.records["j3"]["status"] = status
        original = self.records["j3"]
        earlier = self.references.new_observation("Earlier unrelated observation")
        Path(earlier["path"]).write_bytes(b"earlier observation")
        before_files = self.reference_files()
        failure = RuntimeError("Synthetic original decoder failure")

        def failed_decode(*args, **kwargs):
            if change == "session":
                original.update(session="replacement-session", reason="replacement-private-reason")
            elif change == "kind":
                original.update(kind="EXPORT_USD", reason="replacement-private-reason")
            elif change == "record":
                self.records["j3"] = dict(original, reason="replacement-private-reason")
            raise failure

        with patch.object(self.finished.view_picture, "scaled", side_effect=failed_decode) as decode:
            if change is None and status == "done" and not pending_first:
                with self.assertRaises(RuntimeError) as caught:
                    self.show(frame=1)
                self.assertIs(caught.exception, failure, "current ownership must re-raise the original error")
                result = None
            else:
                result = pending.poll() if pending_first else self.show(frame=1)
            decode.assert_called_once()

        self.assertEqual(output.read_bytes(), output_before, "the job's published output is unchanged")
        self.assertEqual(Path(earlier["path"]).read_bytes(), b"earlier observation")
        self.assertEqual(self.reference_files(), before_files, "only temporary source copies may be removed")
        if pending is not None:
            self.assertTrue(pending.closed)
            self.assertIsNone(pending._wait_account)
            self.assertIs(pending.poll(), result, "late polling must not repeat the failed decode")
        if change is not None:
            self.assertEqual(result["waiting"]["ended"], "ownership_changed")
            self.assertIn("error", result)
            self.assertFalse(result["views"])
            self.assertNotIn("job", result)
            self.assertNotIn("rendered", result)
            self.assertNotIn("replacement-private-reason", json.dumps(result))
            self.assertNotIn("EXPORT_USD", json.dumps(result))
        elif result is not None:
            self.assertNotIn("error", result, "unchanged ownership keeps the existing terminal result contract")
            self.assertEqual(result["waiting"]["ended"], "job_" + status)
            self.assertEqual(result["job"]["status"], status)
            self.assertEqual(result["job"]["kind"], "RENDER_ANIMATION")
            self.assertFalse(result["views"])
            if status in {"done", "failed"}:
                self.assertIn(str(failure), result["note"])
            else:
                self.assertIn(status, result["note"])

    def test_immediate_decode_failure_rechecks_mutated_session_and_kind(self):
        for status in self.STATUSES:
            for change in ("session", "kind"):
                with self.subTest(status=status, change=change):
                    self.exercise(pending_first=False, status=status, change=change)

    def test_pending_decode_failure_rechecks_mutated_session_and_kind(self):
        for status in self.STATUSES:
            for change in ("session", "kind"):
                with self.subTest(status=status, change=change):
                    self.exercise(pending_first=True, status=status, change=change)

    def test_decoder_failure_rechecks_record_replacement_for_both_routes(self):
        for status in self.STATUSES:
            for pending_first in (False, True):
                with self.subTest(status=status, pending_first=pending_first):
                    self.exercise(pending_first=pending_first, status=status, change="record")

    def test_unchanged_owner_keeps_original_error_and_terminal_outcomes(self):
        for status in self.STATUSES:
            for pending_first in (False, True):
                with self.subTest(status=status, pending_first=pending_first):
                    self.exercise(pending_first=pending_first, status=status)


class ControllerLifecycleTests(delivery.ControllerWaitTests):
    def test_controller_releases_both_waits_at_the_shared_deadline(self):
        self.start()
        self.advance(500)
        self.host.request_tool("wait-2", "angelo_view", {
            "views": ["SCENE_CAMERA"], "shading": "RENDERED", "image": "j3"}, self.replies.append)
        self.host.pump_tools(self.executor, self.host.document, budget=ToolDispatchBudget(seconds=60))
        self.assertEqual(len(self.host.media_jobs), 2)
        self.advance(100)
        self.host.pump_events()
        self.assertEqual(len(self.replies), 2)
        self.assertFalse(self.host.media_jobs or self.host.pending)
        self.assertTrue(all(row["waiting"]["ended"] == "wait_used" for row in self.replies))

    def test_document_change_does_not_claim_a_running_job_continues(self):
        self.start()
        self.host.set_document(("other-document",))
        result = self.replies[0]
        self.assertNotIn("job itself goes on", result["error"])
        self.assertIn("document", result["error"].lower())
        self.assertEqual(result["waiting"]["ended"], "document_changed")

    def test_document_change_reports_an_already_interrupted_job(self):
        self.start()
        self.records["j3"].update(status="interrupted", reason="file_closed")
        self.host.set_document(("other-document",))
        result = self.replies[0]
        self.assertEqual(result["job"]["status"], "interrupted")
        self.assertEqual(result["job"]["reason"], "file_closed")
        self.assertNotIn("job itself goes on", result["error"])


if __name__ == "__main__":
    unittest.main()
