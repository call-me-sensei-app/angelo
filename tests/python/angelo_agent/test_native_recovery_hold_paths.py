# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Paths that could get an edit past a hold, closed (request_recovery).

A hold opens when an edit that may have run is refused. Each class here is one way an attack cleared it without
the model seeing the target, or never opened it: reads of other objects (a search, a delta), an exec cell that
reads and edits in one step, a collaborator's read, a payload-bounded turn observation and a live check that
failed after writes. Real scene queries run on the fake scene of test_native_scene_queries.
"""

import unittest
from unittest.mock import patch

from agent_runtime_fixtures import AgentRuntimeFixture, Backend, HANDLE, program
from test_native_scene_queries import FakeObject, queries, window
from angelo_agent import recovery_evidence
from angelo_agent.pending_delivery import ToolCall
from angelo_agent.program_jobs import ProgramError
from angelo_agent.refusal_codes import RefusalError
from angelo_agent.request_recovery import RecoveryBarrier

A = "a" * 64


def ran(handles, code="STALE_REVISION"):
    """A refusal of an edit that may have run (its commit started)."""
    return {"error": "refused", "error_code": code, "partial_changes_possible": True, "mutation_started": True,
            "refusal_details": {"handles": list(handles)}}


def held(barrier):
    try:
        barrier.check("angelo_scene_program", program())
    except RefusalError as error:
        return error.code == "RECOVERY_REQUIRED"
    return False


class ReadsOfOtherObjects(unittest.TestCase):
    """A search or delta captures every object it scans; only the rows it lists were shown."""

    def setUp(self):
        queries.invalidate()
        self.cube, self.lamp = FakeObject(101, "Cube", mesh=True), FakeObject(102, "Lamp")
        self.window = window([self.cube, self.lamp])
        self.cube_handle = queries.object_handle(self.cube)
        self.barrier = RecoveryBarrier()
        with recovery_evidence.collect() as before:
            queries.execute(self.window, "angelo_scene_object", {"object": self.cube_handle})
        self.barrier.observe(before)
        args = {"mode": "APPLY", "read_set": [], "operations": [],
                "write_set": [{"handle": self.cube_handle, "revision": before[self.cube_handle]["revision"]}]}
        self.barrier.refused("angelo_scene_program", args, ran([self.cube_handle]))
        self.cube.data.vertices[0].co[0] = .25  # What the partial commit left behind.
        queries.invalidate(window=self.window)

    def run_read(self, name, args):
        with recovery_evidence.collect() as records:
            result = queries.execute(self.window, name, args)
        return records, self.barrier.read(name, records, result)

    def test_a_search_listing_other_objects_or_none_reads_nothing_of_the_target(self):
        for query in ("Lamp", "NoSuchObject"):
            records, result = self.run_read("angelo_scene_search", {"query": query, "limit": 1})
            self.assertNotIn(self.cube_handle, [row["object"] for row in result["objects"]])
            self.assertIn(self.cube_handle, records)  # Captured while scanning, never shown.
            self.assertTrue(held(self.barrier), query)

    def test_a_search_row_and_a_detail_read_count(self):
        _records, result = self.run_read("angelo_scene_search", {"query": "Cube", "limit": 4})
        self.assertIn(self.cube_handle, [row["object"] for row in result["objects"]])
        self.assertIn("next step", result["recovery"]["reason"])
        self.assertTrue(held(self.barrier))  # Same step: refused, with the changes.
        self.barrier.step()
        self.assertFalse(held(self.barrier))


class SameStepReadAndEdit(AgentRuntimeFixture, unittest.TestCase):
    """A Codex exec cell awaits a read and then edits: the read's result went to the cell, not the model.

    Codex reports a response's usage only after every tool call it made has resolved (the bundled runtime's
    ``try_run_sampling_request`` drains them first), so the cell's calls all share one step.
    """

    def test_every_edit_of_that_step_is_refused_and_the_next_step_runs(self):
        from angelo_agent.routing import RuntimeContext
        _answers, call = self.enqueue(identity="first")
        self.c.finish_tool(call, ran([HANDLE]))
        context = RuntimeContext(self.c, "codex_app_server", {}, "", "test-connection", self.c._epoch)
        cell = []
        with patch.object(self.executor, "execute", return_value={"object": HANDLE, "object_revision": "b" * 64}):
            context.request_tool("cell-1", "angelo_scene_object", {"object": HANDLE}, cell.append)
            for _ in range(10):
                self.pump()
                if cell:
                    break
        for identity in ("edit-a", "edit-b"):  # A refused call is a value in the cell, not an exception.
            context.request_tool(identity, "angelo_scene_program", program(), cell.append)
            self.assertEqual(cell[-1]["error_code"], "RECOVERY_REQUIRED")
            self.assertIn("changes", cell[-1]["refusal_details"]["recovery"])
        self.assertFalse(self.executor.jobs)
        context.record_model_step()  # The next response (provider_usage, on its usage report).
        answered = len(cell)
        context.request_tool("next-cell", "angelo_scene_program", program(), cell.append)
        self.assertEqual(len(cell), answered, "admitted: no immediate refusal")
        self.assertIsNone(self.c.recovery_barrier.pending)

    def test_the_codex_activity_summary_does_not_use_up_the_refusal(self):
        # codex_delivery resolves the call for its summary before the real admission.
        _answers, call = self.enqueue(identity="first")
        self.c.finish_tool(call, ran([HANDLE]))
        self.c.recovery_barrier.read("angelo_scene_object", {}, {"object": HANDLE})
        for _ in range(2):
            with self.assertRaises(RefusalError):
                self.c.resolve_call("angelo_scene_program", program())
        answers = []
        self.c.request_tool("edit", "angelo_scene_program", program(), answers.append)
        self.assertEqual(answers[0]["error_code"], "RECOVERY_REQUIRED")


class ModelSteps(AgentRuntimeFixture, unittest.TestCase):
    """Where a step ends: a Codex usage report of the lead's turn, a Claude round; never a collaborator's."""

    def test_a_codex_usage_report_of_the_lead_ends_a_step_and_a_collaborators_does_not(self):
        from types import SimpleNamespace
        from angelo_agent.provider_usage import CodexThreadUsage
        from angelo_agent.routing import RuntimeContext
        lead = RuntimeContext(self.c, "codex_app_server", {}, "", "test-connection", self.c._epoch)
        collaborator = SimpleNamespace(host=self.c, cancelled=False, voice=None)  # DelegatedContext has no step.
        scope = SimpleNamespace(matches=lambda context, params: True)
        params = {"threadId": "t", "turnId": "u", "tokenUsage": None}  # A provider that reports no counts still ends it.
        for context, expected in ((collaborator, 0), (lead, 1)):
            CodexThreadUsage().observe_event("thread/tokenUsage/updated", params, context, thread_id="t",
                                             scope=scope, accepted_input=lambda context: None)
            self.assertEqual(self.c.recovery_barrier.steps, expected)

    def test_a_codex_turn_end_ends_a_step_even_without_usage(self):
        # app-server reports usage only when the provider does; a turn end always means every call resolved.
        from types import SimpleNamespace
        from angelo_agent.provider_usage import CodexThreadUsage
        lead = self.c.runtime_context()
        scope = SimpleNamespace(matches=lambda context, params, **flags: flags == {"allow_cancelled": True,
                                                                               "allow_completed": True})
        handled = CodexThreadUsage().observe_event("turn/completed", {"threadId": "t", "turn": {"status": "completed"}},
                                                   lead, thread_id="t", scope=scope, accepted_input=lambda c: None)
        self.assertFalse(handled, "the provider still handles the turn's end itself")
        self.assertEqual(self.c.recovery_barrier.steps, 1)

    def test_later_reads_never_push_the_wait_to_a_later_step(self):
        # Regression: a lookup in each later cell re-armed the wait, so "look something up, then edit" never ran.
        barrier = RecoveryBarrier()
        barrier.refused("angelo_scene_program", program(), ran([HANDLE]))
        barrier.read("angelo_scene_object", {}, {"object": HANDLE})
        barrier.step()
        barrier.read("angelo_scene_object", {}, {"object": "scn:" + "a" * 32 + ":9"})  # A lookup in the new step.
        barrier.read("angelo_scene_object", {}, {"object": HANDLE})  # Even of the same target.
        self.assertFalse(held(barrier))

    def test_a_claude_round_ends_a_step(self):
        from angelo_agent.routing import RuntimeContext
        RuntimeContext(self.c, "claude_code", {}, "", "test-connection", self.c._epoch).record_model_round()
        self.assertEqual(self.c.recovery_barrier.steps, 1)


class CollaboratorRead(AgentRuntimeFixture, unittest.TestCase):
    def test_a_delegated_read_does_not_clear_the_lead_models_hold(self):
        _answers, call = self.enqueue(identity="first")
        self.c.finish_tool(call, ran([HANDLE]))
        parent = ToolCall(id=self.c.state["id"] + "/delegate", name="angelo_delegate",
                          arguments={"connection_id": "x", "model": "m", "task": "t"}, document=self.c.document,
                          epoch=self.c._epoch, generation=self.c._request_generation, executing=True)
        self.c.pending[parent.id] = parent
        child = []
        with patch.object(self.executor, "execute", return_value={"object": HANDLE, "object_revision": "b" * 64}):
            self.c.request_tool(parent.id + "/child/1", "angelo_scene_object", {"object": HANDLE}, child.append,
                                parent_call=parent)
            for _ in range(10):
                self.pump()
                if child:
                    break
        self.assertTrue(child and not child[0].get("error"), child)
        self.assertNotIn("recovery", child[0])
        self.assertEqual(self.c.recovery_barrier.description()["read"], [HANDLE])


class RefusalTextNamesTheTargets(unittest.TestCase):
    def test_a_refusal_read_only_as_text_still_names_what_to_read(self):
        # A plan rejection quotes the error's text alone (providers._plan_problems).
        barrier = RecoveryBarrier()
        barrier.refused("angelo_scene_program", program(), ran([HANDLE]))
        with self.assertRaises(RefusalError) as caught:
            barrier.check("angelo_scene_program", program())
        self.assertIn("Read again: " + HANDLE, str(caught.exception))


class BoundedTurnObservation(unittest.TestCase):
    def test_a_row_the_payload_bound_dropped_is_not_read(self):
        from test_native_turn_context import make_window, object_id
        from angelo_agent import change_feed, turn_context
        queries.invalidate()
        change_feed.clear()
        objects = [object_id(FakeObject(900 + i, "Prop %02d %s" % (i, "x" * 100), mesh=True)) for i in range(8)]
        with patch.object(turn_context, "MAX_PAYLOAD_CHARS", 1800):  # Stands in for a modifier-heavy scene.
            turn = turn_context.prepare(make_window(objects, objects[0], objects[1:8]), "chat", clock=lambda: 0.)
        listed = {entry["handle"] for entry in turn.observation["objects"]}
        dropped = [handle for handle in turn.recovery_evidence if handle not in listed]
        self.assertTrue(dropped, "fixture: no captured row was dropped")
        barrier = RecoveryBarrier()
        args = {"mode": "APPLY", "read_set": [], "write_set": [{"handle": dropped[0], "revision": A}], "operations": []}
        barrier.refused("angelo_scene_program", args, ran([dropped[0]]))
        barrier.read(None, turn.recovery_evidence, turn.observation)
        self.assertEqual(barrier.description()["read"], [dropped[0]])


class LiveCheckFailedAfterWrites(AgentRuntimeFixture, unittest.TestCase):
    def test_a_commit_that_wrote_then_failed_a_live_check_holds_the_next_edit(self):
        def failing_commit(backend, stage):
            backend.mutation_started = True  # program_commit sets this before its first live write.
            backend.source = list(stage["location"])
            error = ProgramError("Native commit failed; rollback requires checkpoint recovery: live check failed",
                                 code="CHECK_FAILED")
            error.partial_changes_possible, error.rollback_error = True, "could not restore"
            raise error
        with patch.object(Backend, "commit", failing_commit):
            answers, _call = self.enqueue(identity="first")
            for _ in range(40):
                self.pump()
                if answers:
                    break
        self.assertEqual((answers[0]["error_code"], answers[0]["partial_changes_possible"]), ("CHECK_FAILED", True))
        self.assertIs(answers[0]["recovery_required"], True)
        later = []
        self.c.request_tool("second", "angelo_scene_program", program(), later.append)
        self.assertEqual(later[-1]["error_code"], "RECOVERY_REQUIRED")


if __name__ == "__main__":
    unittest.main()
