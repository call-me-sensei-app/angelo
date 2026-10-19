# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Cooperative controller jobs and request timing; no bpy, build or paid model."""

import sys
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from agent_runtime_fixtures import AgentRuntimeFixture, program
from angelo_agent.controller import Controller, ToolDispatchBudget, pump_controller_tools
from angelo_agent.program_jobs import ProgramError
from angelo_agent.task_memory import read_memory
from angelo_agent.checkpoint_policy import BEFORE_FIRST_EDIT, CheckpointPolicyStore, DEFAULT_POLICY
from angelo_agent.routing import shared_context


class AgentRuntimeTests(AgentRuntimeFixture, unittest.TestCase):
    def test_native_polls_yield_and_timer_never_commits_without_undo_callback(self):
        answers, call = self.enqueue()
        job = self.ready()
        self.assertTrue(job.ready_to_commit)
        self.assertEqual(job.backend.source, [0, 0, 0])
        self.assertFalse(answers)
        before = len(job.backend.calls)
        for _ in range(3):
            self.pump(commit=False)
        self.assertEqual(len(job.backend.calls), before)
        self.pump()
        self.assertEqual(job.backend.commits, 1)
        self.assertEqual(job.backend.source, [1, 2, 3])
        self.assertEqual(len(answers), 1)
        self.assertTrue(answers[0]["scene_changed"])
        self.assertNotIn(call.id, self.c.pending)

    def test_stop_disposes_uncommitted_stage_and_no_late_callback_can_commit(self):
        answers, call = self.enqueue()
        job = self.ready()
        self.c.stop()
        self.assertTrue(job.closed)
        self.assertEqual(job.backend.discards, 1)
        self.assertEqual(job.backend.source, [0, 0, 0])
        self.assertTrue(answers[0]["cancelled"])
        self.c.cancelled, self.c.busy = False, True
        self.c._request_generation += 1
        result = self.c.commit_native_job(call, self.executor, self.c.document)
        self.assertIn("error", result)
        self.assertEqual(job.backend.commits, 0)

    def test_epoch_switch_rejects_a_queued_commit(self):
        answers, call = self.enqueue()
        job = self.ready()
        self.c._epoch += 1
        self.c.commit_native_job(call, self.executor, self.c.document)
        self.assertIn("error", answers[0])
        self.assertEqual(job.backend.commits, 0)
        self.assertEqual(job.backend.discards, 1)

    def test_document_change_and_readonly_discard_staging(self):
        for index, change in enumerate((lambda: self.c.set_document(("other", 1)), lambda: self.c.set_options(approval="READ_ONLY"))):
            with self.subTest(change=change):
                answers, _ = self.enqueue(identity="discard-trial-" + str(index))
                job = self.ready()
                before_discards = job.backend.discards
                change()
                self.assertEqual(job.backend.commits, 0)
                self.assertEqual(job.backend.discards, before_discards + 1)
                self.assertFalse(answers[0]["partial_changes_possible"] if "partial_changes_possible" in answers[0] else False)
                self.c.document_invalid = False
                self.c.cancelled, self.c.busy = False, True
                self.c.document = ("document", 1)
                self.c.state["options"]["approval"] = "AUTO_BOUNDED"
                # Keep cancellation receipts/ID guards; each trial is a new invocation.

    def test_expiration_after_a_poll_cancels_the_isolated_stage(self):
        answers, call = self.enqueue()
        self.pump(commit=False)
        job = self.executor.jobs[-1]
        job.backend.on_apply = lambda: setattr(call, "expires", time.monotonic() - 1)
        for _ in range(12):
            self.pump(commit=False)
            if answers:
                break
        self.assertTrue(answers[0]["expired"])
        self.assertEqual(job.backend.source, [0, 0, 0])
        self.assertEqual(job.backend.discards, 1)

    def test_exact_fresh_precondition_immediately_before_commit(self):
        answers, call = self.enqueue()
        job = self.ready()
        self.executor.fingerprint = "changed"
        result = self.c.commit_native_job(call, self.executor, self.c.document)
        self.assertIn("changed", result["error"])
        self.assertFalse(result["partial_changes_possible"])
        self.assertEqual(job.backend.commits, 0)
        self.assertEqual(job.backend.discards, 1)
        self.assertEqual(len(answers), 1)

    def test_stop_during_revalidation_cannot_reach_commit(self):
        answers, call = self.enqueue()
        job = self.ready()
        self.executor.on_precondition = lambda *_: self.c.stop()
        self.c.commit_native_job(call, self.executor, self.c.document)
        self.assertTrue(answers[0]["cancelled"])
        self.assertEqual(job.backend.commits, 0)

    def test_cross_thread_stop_revokes_commit_now_but_defers_rna_cleanup(self):
        answers, call = self.enqueue()
        job = self.ready()
        worker = threading.Thread(target=self.c.stop)
        worker.start()
        worker.join()
        self.assertNotIn(call.id, self.c.pending)
        self.assertEqual(job.backend.discards, 0)
        self.assertEqual(len(self.c._native_cleanup), 1)
        self.assertIn("error", self.c.commit_native_job(call, self.executor, self.c.document))
        self.pump()
        self.assertEqual(job.backend.discards, 1)
        self.assertIsNone(self.writer.owner)
        self.assertTrue(answers[0]["cancelled"])

    def test_shared_writer_and_budget_do_not_starve_another_chats_reads(self):
        other = self.new_controller()
        first, _ = self.enqueue()
        second, _ = self.enqueue(other)
        read = []
        other.request_tool("read", "angelo_inspect_scene", {}, read.append)
        cursor = 0
        controllers = [self.c, other]
        for _ in range(50):
            commits_before = sum(value.commits for value in self.executor.backends.values())
            cursor = pump_controller_tools(controllers, cursor, self.executor, self.c.document,
                lambda c, call: c.execute_tool(call, self.executor, c.document),
                commit_mutation=lambda c, call: c.commit_native_job(call, self.executor, c.document),
                budget=ToolDispatchBudget(seconds=60))
            self.assertLessEqual(sum(value.commits for value in self.executor.backends.values()) - commits_before, 1)
            if read and not first:
                self.assertFalse(second)  # Read passed the waiting writer, not vice versa.
            if first and second:
                break
        self.assertTrue(read)
        self.assertTrue(first and second)
        self.assertEqual(sum(value.commits for value in self.executor.backends.values()), 2)
        self.assertIsNone(self.writer.owner)

    def test_invocation_id_cannot_change_arguments_pending_or_completed(self):
        answers, _ = self.enqueue(identity="same")
        conflict, _ = self.enqueue(identity="same", location=[2, 2, 2])
        self.assertIn("different", conflict[0]["error"])
        self.ready()
        self.pump()
        replay, _ = self.enqueue(identity="same")
        self.assertTrue(replay[0]["replayed"])
        again, _ = self.enqueue(identity="same", location=[3, 3, 3])
        self.assertIn("different", again[0]["error"])
        self.assertEqual(len(self.executor.jobs), 1)
        self.assertEqual(len(answers), 1)

    def test_restart_journal_never_reapplies_an_unfinished_commit(self):
        _answers, call = self.enqueue()
        self.c._journal_tool(call, "commit_started")
        resumed = Controller(self.directory.name, session_id=self.c.state["id"])
        self.controllers.append(resumed)
        resumed.document, resumed.busy = self.c.document, True
        replies = []
        resumed.request_tool("program", "angelo_scene_program", program(), replies.append)
        self.assertTrue(replies[0]["replayed"])
        self.assertTrue(replies[0]["partial_changes_possible"])
        self.assertEqual(replies[0]["journal_phase"], "commit_started")
        self.assertFalse(resumed.pending)
        self.assertFalse(self.executor.jobs)

    def test_retained_preview_survives_finish_and_ui_accept_uses_fresh_snapshot(self):
        old_checkpoint = str(uuid.uuid4())
        self.c.request_checkpoint = old_checkpoint
        self.c.emit("message", {"role": "user", "text": "Preview this edit", "checkpoint": old_checkpoint,
                                "checkpoint_scope": "before_request"})
        self.c.pump_events()
        answers, _ = self.enqueue(mode="PREVIEW")
        for _ in range(20):
            self.pump()
            if answers:
                break
        self.assertEqual(answers[0]["status"], "preview")
        self.c.emit("finished", {"status": "completed"})
        self.c.pump_events()
        self.assertFalse(self.c.busy)
        descriptor = self.c.native_previews()[0]
        self.assertEqual(descriptor["arguments"], program("PREVIEW"))
        self.executor.studio_context = self.c
        fresh_checkpoint = str(uuid.uuid4())
        snapshot = Mock(return_value={"id": fresh_checkpoint})
        self.c.checkpoint_factory = snapshot
        self.assertTrue(self.c.accept_native_preview(descriptor["preview_id"], self.executor, self.c.document, descriptor["preview_token"]))
        snapshot.assert_called_once()
        self.assertTrue(self.c.busy)
        self.c.pump_events()
        intents = [message for message in self.c.state["messages"] if message.get("request_kind") == "native_preview_accept"]
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0]["role"], "user")
        self.assertEqual(intents[0]["checkpoint"], fresh_checkpoint)
        self.assertEqual(intents[0]["checkpoint_scope"], "before_request")
        self.assertEqual(intents[0]["preview_id"], descriptor["preview_id"])
        intent_index = self.c.state["messages"].index(intents[0])
        activity_index = self.c._activities[intents[0]["native_call_id"]]
        self.assertLess(intent_index, activity_index)
        self.assertEqual(self.c.state["messages"][activity_index]["role"], "activity")
        self.assertNotIn("provider", intents[0])
        self.assertEqual(self.c.state["messages"][0]["checkpoint"], old_checkpoint)
        self.assertIn("human UI preview acceptance; no model request", shared_context(self.c.state["messages"]))
        self.ready()
        self.pump()
        self.assertFalse(self.c.busy)
        self.assertEqual(self.executor.backends[self.c.state["id"]].commits, 1)
        self.assertFalse(self.c.native_previews())

    def test_deferred_ui_preview_accept_attaches_its_checkpoint_once_not_to_old_message(self):
        old_checkpoint, fresh_checkpoint = str(uuid.uuid4()), str(uuid.uuid4())
        self.c.request_checkpoint = old_checkpoint
        self.c.emit("message", {"role": "user", "text": "Preview this edit", "checkpoint": old_checkpoint,
                                "checkpoint_scope": "before_request"})
        self.c.pump_events()
        answers, _ = self.enqueue(mode="PREVIEW")
        for _ in range(20):
            self.pump()
            if answers:
                break
        self.c.emit("finished", {"status": "completed"})
        self.c.pump_events()
        descriptor = self.c.native_previews()[0]
        CheckpointPolicyStore(self.directory.name).save(dict(DEFAULT_POLICY, capture_mode=BEFORE_FIRST_EDIT), user_confirmed=True)
        self.executor.studio_context = self.c
        snapshot = Mock(return_value={"id": fresh_checkpoint})
        self.c.checkpoint_factory = snapshot
        self.assertTrue(self.c.accept_native_preview(descriptor["preview_id"], self.executor, self.c.document, descriptor["preview_token"]))
        self.c.pump_events()
        self.c.pump_events()
        intents = [message for message in self.c.state["messages"] if message.get("request_kind") == "native_preview_accept"]
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0]["checkpoint"], fresh_checkpoint)
        self.assertEqual(intents[0]["checkpoint_scope"], "before_native_staging")
        self.assertEqual(self.c.state["messages"][0]["checkpoint"], old_checkpoint)
        self.assertEqual(self.c.state["messages"][0]["checkpoint_scope"], "before_request")
        self.ready()
        self.pump()
        self.c.pump_events()
        snapshot.assert_called_once()
        self.assertEqual(sum(message.get("request_kind") == "native_preview_accept" for message in self.c.state["messages"]), 1)

    def test_preview_token_and_native_binding_are_both_revalidated(self):
        answers, _ = self.enqueue(mode="PREVIEW")
        for _ in range(20):
            self.pump()
            if answers:
                break
        self.c.busy = False
        descriptor = self.c.native_previews()[0]
        self.executor.studio_context = self.c
        with self.assertRaises(ValueError):
            self.c.accept_native_preview(descriptor["preview_id"], self.executor, self.c.document, "wrong")
        self.c.pump_events()
        self.assertFalse(any(message.get("request_kind") == "native_preview_accept" for message in self.c.state["messages"]))
        self.assertTrue(self.c.native_previews())
        self.assertTrue(self.c.accept_native_preview(descriptor["preview_id"], self.executor, self.c.document, descriptor["preview_token"]))
        self.pump(commit=False)  # Consumes retained preview but does not commit.
        backend = self.executor.backends[self.c.state["id"]]
        backend.revision += 1
        self.pump()
        self.assertEqual(backend.commits, 0)
        self.assertFalse(self.c.busy)
        self.assertEqual(backend.discards, 1)

    def test_rejected_preview_precondition_or_snapshot_failure_emits_no_acceptance_intent(self):
        answers, _ = self.enqueue(mode="PREVIEW")
        for _ in range(20):
            self.pump()
            if answers:
                break
        self.c.busy = False
        descriptor = self.c.native_previews()[0]
        self.executor.studio_context = self.c
        self.c.checkpoint_factory = Mock(side_effect=RuntimeError("snapshot failed"))
        with self.assertRaises(ValueError):
            self.c.accept_native_preview(descriptor["preview_id"], self.executor, self.c.document, descriptor["preview_token"])
        self.c.pump_events()
        self.assertFalse(any(message.get("request_kind") == "native_preview_accept" for message in self.c.state["messages"]))
        self.c.checkpoint_factory = Mock(return_value={"id": str(uuid.uuid4())})
        self.executor.on_precondition = Mock(side_effect=ProgramError("stale native precondition"))
        self.assertFalse(self.c.accept_native_preview(descriptor["preview_id"], self.executor, self.c.document, descriptor["preview_token"]))
        self.c.pump_events()
        self.assertFalse(any(message.get("request_kind") == "native_preview_accept" for message in self.c.state["messages"]))
        self.assertEqual(self.executor.backends[self.c.state["id"]].commits, 0)

    def test_fused_poll_reports_verification_apart_from_staging(self):
        with patch("agent_runtime_fixtures.Backend.fuse_phases", True, create=True):
            self.c._begin_metrics()
            self.c.record_model_round()
            self.enqueue()
            self.ready()
            self.pump()
            self.c.emit("finished", {"status": "completed"})
            self.c.pump_events()
        report = self.c.metrics_report()
        # One poll began in PREPARE, yet its checks and read-back are still measured.
        self.assertEqual(report["counts"]["native_steps"], 1)
        self.assertEqual(report["counts"]["native_commits"], 1)
        self.assertEqual(report["counts"]["verification_samples"], 1)
        self.assertGreater(report["phases"]["verification_ms"]["p50"], 0)

    def test_metadata_phase_accounting_and_verified_memory_receipts(self):
        clock = [10.]
        self.executor.clock = clock
        self.c.state["provider"] = "claude_subscription"
        with patch("angelo_agent.controller.time.monotonic", side_effect=lambda: clock[0]):
            self.c._begin_metrics()
            self.c.record_model_round()
            answers, _ = self.enqueue()
            clock[0] += .02
            self.ready()
            self.pump()
            self.c.emit("delta", {"item": "reply", "text": "secret prompt marker"})
            clock[0] += .04
            self.c.emit("finished", {"status": "completed"})
            self.c.pump_events()
        report = self.c.metrics_report()
        self.assertEqual(report["completed_requests"], 1)
        self.assertEqual(report["counts"]["tool_invocations"], 1)
        self.assertEqual(report["counts"]["tool_executions"], 1)
        self.assertEqual(report["counts"]["native_commits"], 1)
        self.assertEqual(report["counts"]["native_steps"], 6)
        self.assertEqual(report["counts"]["model_rounds"], 1)
        self.assertGreater(report["phases"]["verification_ms"]["p50"], 0)
        self.assertAlmostEqual(report["phases"]["queued_wait_ms"]["p50"], 20)
        self.assertEqual(report["phases"]["approval_wait_ms"]["p50"], 0)
        self.assertGreater(self.c.timings["total_ms"], self.c.timings["first_text_ms"])
        self.assertTrue(self.c.telemetry.flush())
        self.assertNotIn("secret prompt marker", self.c.telemetry.path.read_text())
        memory = read_memory(self.c.state, self.c.document)
        self.assertEqual(memory["verified_results"][-1]["status"], "verified")
        self.c.mark_memory_stale("undo")
        memory = read_memory(self.c.state, self.c.document)
        self.assertTrue(memory["verified_results"][-1]["stale"])
        self.assertEqual(answers[0]["scene_changed"], True)

    def test_checkpoint_clock_starts_before_request_and_first_text_includes_it(self):
        self.c.busy = False
        clock = [0.]
        checkpoint_id = str(uuid.uuid4())

        def capture():
            self.assertEqual(self.c.timings["started"], 0)
            clock[0] += .1
            return {"id": checkpoint_id}

        fake_thread = SimpleNamespace(start=lambda: None, is_alive=lambda: False)
        self.c.checkpoint_factory = capture
        with patch("angelo_agent.controller.time.monotonic", side_effect=lambda: clock[0]), \
             patch("angelo_agent.controller.threading.Thread", return_value=fake_thread):
            self.c.send("private request content")
            clock[0] += .2
            self.c.emit("delta", {"item": "text", "text": "public reply"})
            self.c.emit("finished", {"status": "completed"})
            self.c.pump_events()
        self.assertAlmostEqual(self.c.timings["checkpoint_ms"], 100)
        self.assertAlmostEqual(self.c.timings["first_text_ms"], 300)
        self.assertAlmostEqual(self.c.timings["total_ms"], 300)
        self.assertEqual(self.c.state["messages"][0]["checkpoint"], checkpoint_id)

    def test_the_live_row_names_what_the_model_reasons_about(self):
        """A typed plan shows no reply until it is written: the reasoning summary's title is the first content.

        A GPT-6.1-Sol one-shot can reason for several seconds before the plan's reply, the live row saying only
        "Thinking" all the while.
        """
        from angelo_agent.chat_titles import live_label
        self.c.busy = False
        clock = [0.]
        fake_thread = SimpleNamespace(start=lambda: None, is_alive=lambda: False)
        with patch("angelo_agent.controller.time.monotonic", side_effect=lambda: clock[0]), \
             patch("angelo_agent.controller.threading.Thread", return_value=fake_thread):
            self.c.send("private request content")
            self.c.emit("status", {"text": "Thinking"})
            self.c.emit("thinking_started", {"item": "r1", "time": 1.})
            clock[0] += 1.5
            self.c.emit("thinking", {"item": "r1", "text": "**Planning the stone"})
            self.c.pump_events()
            self.assertEqual(live_label(self.c.status), "Thinking", "a title still arriving is not shown")
            self.c.emit("thinking", {"item": "r1", "text": " footbridge**\n\nI need a deck and four posts."})
            self.c.pump_events()
            self.assertEqual(live_label(self.c.status), "Thinking · Planning the stone footbridge")
            self.c.emit("thinking", {"item": "r1", "text": "**Finalizing   post positions**\n\n"})
            self.c.pump_events()
            self.assertEqual(live_label(self.c.status), "Thinking · Finalizing post positions")
            self.c.emit("status", {"text": "Preparing the next steps"})
            self.c.emit("thinking", {"item": "r1", "text": "**A late title**"})
            self.c.pump_events()
            self.assertEqual(self.c.status, "Preparing the next steps", "only the generic Thinking status is refined")
            clock[0] += 2
            self.c.emit("delta", {"item": "reply", "text": "public reply"})
            self.c.emit("finished", {"status": "completed"})
            self.c.pump_events()
        self.assertAlmostEqual(self.c.timings["first_text_ms"], 3500, "a reasoning summary is not reply text")

    def test_explicit_deferred_snapshot_precedes_stage_and_has_honest_scope(self):
        CheckpointPolicyStore(self.directory.name).save(dict(DEFAULT_POLICY, capture_mode=BEFORE_FIRST_EDIT), user_confirmed=True)
        self.c.busy = False
        captured = []
        checkpoint_id = str(uuid.uuid4())

        def capture():
            self.assertIsNone(self.writer.owner)
            self.assertFalse(self.c.native_jobs)
            captured.append(True)
            return {"id": checkpoint_id}

        self.c.checkpoint_factory = capture
        fake_thread = SimpleNamespace(start=lambda: None, is_alive=lambda: False)
        with patch("angelo_agent.controller.threading.Thread", return_value=fake_thread):
            self.c.send("Move the object")
        self.c.pump_events()
        self.assertFalse(captured)
        self.assertNotIn("checkpoint", self.c.state["messages"][0])
        self.assertEqual(self.c.state["messages"][0]["checkpoint_scope"], "before_first_approved_edit")
        self.enqueue()
        self.pump(commit=False)
        self.assertEqual(captured, [True])
        self.c.pump_events()
        self.assertEqual(self.c.state["messages"][0]["checkpoint"], checkpoint_id)
        self.assertEqual(self.c.state["messages"][0]["checkpoint_scope"], "before_native_staging")

    def test_private_metadata_failure_never_claims_scene_edits_or_captures_deferred_snapshot(self):
        self.c.checkpoint_factory = Mock(return_value={"id": str(uuid.uuid4())})
        self.c.state["options"]["approval"] = "ASK"
        self.executor.execute = Mock(return_value={"value": float("nan")})
        replies = []
        revision = read_memory(self.c.state, self.c.document)["revision"]
        self.c.request_tool("brief", "angelo_memory_update", {"expected_revision": revision, "patch": {"objective": "A sphere"}}, replies.append)
        self.pump(commit=False)
        call = next(iter(self.c.pending.values()))
        self.c.execute_tool(call, self.executor, self.c.document)
        self.assertFalse(call.mutation_started)
        self.c.checkpoint_factory.assert_not_called()
        self.assertTrue(replies[0]["serialization_failed"])
        self.assertNotIn("partial_changes_possible", replies[0])
        self.assertNotIn("changes", replies[0])

    def test_voice_snapshot_failure_is_timed_before_any_provider_or_audio_start(self):
        self.c.busy = False
        clock = [0.]

        def fail():
            self.assertEqual(self.c.timings["started"], 0)
            clock[0] += .2
            raise RuntimeError("snapshot unavailable")

        self.c.checkpoint_factory = fail
        with patch("angelo_agent.voice.availability", return_value=""), \
             patch("angelo_agent.controller.time.monotonic", side_effect=lambda: clock[0]), \
             patch("angelo_agent.controller.CodexProvider") as provider:
            with self.assertRaises(RuntimeError):
                self.c.start_voice()
            provider.assert_not_called()
        self.assertFalse(self.c.busy)
        self.assertIsNone(self.c.voice)
        self.assertAlmostEqual(self.c.timings["checkpoint_ms"], 200)
        self.assertAlmostEqual(self.c.timings["total_ms"], 200)

    def test_runtime_stays_warm_and_codex_internal_rounds_are_unavailable(self):
        self.c.busy = False
        clock = [1.]
        connections = []

        class Provider:
            def __init__(inner, context, _settings):
                inner.context = context

            def connect(inner):
                connections.append(True)
                clock[0] += .05
                inner.context.emit("native_session", {"id": "native-context"})
                return "native-context"

            def run(inner, _identity, _prompt, observation=None):
                inner.context.host.record_provider_turn()
                inner.context.host.record_model_round()  # Must not guess Codex's internal model calls.
                clock[0] += .03
                inner.context.emit("delta", {"item": str(len(connections)), "text": "Reply"})
                return "completed"

            def stop(inner):
                pass

            def close(inner):
                pass

        fake_thread = SimpleNamespace(start=lambda: None, is_alive=lambda: False)
        with patch("angelo_agent.controller.time.monotonic", side_effect=lambda: clock[0]), \
             patch("angelo_agent.controller.threading.Thread", return_value=fake_thread), \
             patch("angelo_agent.controller.CodexProvider", Provider):
            self.c.send("First request")
            self.c._run("First request")
            self.c.pump_events()
            provider = self.c.provider
            self.c.send("Second request")
            self.c._run("Second request")
            self.c.pump_events()
        self.assertIs(self.c.provider, provider)
        self.assertEqual(len(connections), 1)
        report = self.c.metrics_report()
        self.assertEqual(report["runtime_start"]["cold"]["p50"], 50)
        self.assertEqual(report["runtime_start"]["warm"]["p50"], 0)
        self.assertEqual(report["counts"]["provider_turn_requests"], 2)
        self.assertIn("model_rounds", report["unmeasured_counts"])
        self.assertNotIn("model_rounds", report["counts"])



if __name__ == "__main__":
    unittest.main()
