# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Plan mode at the real sites that change a scene (doc/angelo/plan_mode.rst, "Safety"; break-it review S4, S1): a
planning chat's typed edit is refused at dispatch, at approval, at execution and at commit, a staged edit is discarded
when planning starts, a retained preview cannot be accepted, and the chat's queued background jobs that change things
are cancelled as planning starts (never held for a press to start). Through the real ``Controller`` and ``JobManager`` with the shared stand-in executor (no bpy)."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).
import angelo_guard_twin  # noqa: E402  C's authority, as the app's (strict: no scope but the one a test opens).

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from agent_runtime_fixtures import AgentRuntimeFixture  # noqa: E402
from angelo_agent import controller_entries, planning_controls as PC  # noqa: E402,F401
from angelo_agent.chat_settings import planning  # noqa: E402


PLAN = {"summary": "Two ways.", "recommended": 0, "questions": [],
        "approaches": [{"title": "Booleans", "steps": ["Cut"], "risks": [], "estimate": "10 min"}]}


def person(function, *args, **kwargs):
    return angelo_guard_twin.harness(function, *args, **kwargs)


class PlanningAtTheRealSites(AgentRuntimeFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.c._persons_send = True

    def plan(self):
        PC.start(self.c)
        self.assertTrue(planning(self.c.state["options"]))

    def test_a_typed_edit_is_refused_at_dispatch(self):
        self.plan()
        answers, _call = self.enqueue()
        for _ in range(5):
            person(self.pump)
        self.assertEqual([answer.get("error_code") for answer in answers], ["READ_ONLY"])
        self.assertEqual(self.executor.backend().commits if self.executor.backends else 0, 0)

    def test_approve_and_execute_refuse_a_call_queued_before_planning(self):
        self.c.state["options"]["approval"] = "ASK"   # Waits for the person's approval, as a card does.
        answers, call = self.enqueue()
        self.pump()
        self.plan()   # Refuses what waits (Controller.set_options), as a switch to Read only does.
        self.assertEqual([answer.get("error_code") for answer in answers], ["READ_ONLY"])
        for site in (self.c.approve_tool, self.c.execute_tool):
            with self.subTest(site.__name__):
                person(site, call, self.executor, self.c.document)
        self.assertEqual(len(answers), 1)
        self.assertFalse(self.executor.backends and self.executor.backend().commits)

    def test_a_staged_edit_is_discarded_and_never_committed(self):
        answers, call = self.enqueue()
        job = self.ready()
        self.plan()
        self.assertEqual(job.backend.commits, 0)
        self.assertEqual(job.backend.discards, 1)
        person(self.c.commit_native_job, call, self.executor, self.c.document)
        self.assertEqual(job.backend.commits, 0)

    def test_a_retained_preview_cannot_be_accepted(self):
        self.plan()
        self.c.busy = False
        with patch.object(type(self.c), "inspect_native_preview", lambda c, identity: {"preview_token": "t"}), \
                self.assertRaises(ValueError) as caught:
            person(self.c.accept_native_preview, "preview", self.executor, self.c.document, "t")
        self.assertIn("read-only", str(caught.exception))


class WaitingJobs(unittest.TestCase):
    """Break-it reviews S1 (round 1) and S1, S2, S3 (e327003b8c2): work admitted before planning never runs while the
    chat plans, and no press that ends planning starts anything. Starting planning cancels the chat's queued jobs that
    the agent's calls admitted and that change things (each job's own row says why), so nothing waits on the chat's
    Python state: no hold to leak when the chat is deleted, and no press that quietly starts work admitted under an
    earlier setting. The person's own queued presses (a card's Bake, Make Video's renders, the missing frames: no call),
    a look the agent asked for while planning (``RENDER_PREVIEW``, read only) and a job already running go on, the
    running one named. Re-read of 55525904e80: S1 (the person's jobs), S2 (the running job named), S3 (a failed save),
    NIT 2 (the agent told which). Through the real ``Controller`` (its ``set_options`` as ``controller_entries`` wraps
    it) and a real ``JobManager`` and ``JobStore``."""

    def setUp(self):
        import tempfile
        from angelo_agent.controller import Controller
        from angelo_agent.job_manager import JobManager
        from angelo_agent.job_store import JobStore
        angelo_guard_twin.reset()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.c = Controller(self.directory.name)
        self.addCleanup(self.c.close)
        self.c._persons_send = True
        self.said = []   # The chat's rows as the main thread would add them (``Controller.emit`` queues them).
        self.c.emit = lambda kind, data=None: self.said.append((data or {}).get("text")) if kind == "message" else None
        self.jobs = JobManager(JobStore(self.directory.name))
        self.started = []
        self.jobs._start = lambda record: self.started.append(record["id"])
        for patcher in (patch("angelo_agent.job_manager._shared", self.jobs),
                        patch("angelo_agent.edit_tool_hold.running", lambda: False)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def queue(self, kind, session=None, call_id="call"):
        from angelo_agent.job_store import new_record
        record = new_record(self.jobs.store.allocate(), session or self.c.state["id"], call_id, {"kind": kind}, now=0)
        self.jobs.store.add(record)
        self.jobs.queue.append(record["id"])
        return record["id"]

    def status(self, identity):
        record = self.jobs.store.get(identity)
        return record["status"], record["reason"]

    def test_starting_planning_cancels_the_waiting_changes_and_says_so(self):
        from angelo_agent import job_chat, planning_text as T
        person(self.c.set_options, approval="AUTO_BOUNDED")
        bake, export = self.queue("BAKE_TEXTURE"), self.queue("EXPORT_USD")
        look, other = self.queue("RENDER_PREVIEW"), self.queue("BAKE_TEXTURE", session="another-chat")
        PC.start(self.c)
        for identity in (bake, export):
            self.assertEqual(self.status(identity), ("cancelled", PC.JOBS_CANCELLED_BY))
            self.assertIn("Cancelled: its chat started planning",
                          job_chat.progress_text(self.jobs.store.get(identity)))
        self.assertEqual(self.status(look), ("queued", ""))   # A look the agent waits for (read only).
        self.assertEqual(self.status(other), ("queued", ""))   # Another chat's work is its own.
        self.assertEqual(self.said, [T.STARTED])   # Each job's own row says why (job_ui): no second notice.
        self.jobs.poll()
        self.assertEqual(self.started, [look, other])   # Planning holds nothing back: there is nothing left to hold.

    def test_a_running_job_goes_on_and_is_named(self):
        """S2: the notice says what still changes the scene: the chat's job already running, by its title."""
        from angelo_agent import planning_text as T
        running = self.queue("BAKE_TEXTURE")
        self.jobs.queue.remove(running)
        self.jobs.store.get(running)["status"] = "running"
        self.jobs.running = running
        PC.start(self.c)
        self.assertEqual(self.status(running), ("running", ""))   # Planning stops new changes, not what runs.
        self.assertEqual(self.said, [T.JOB_STILL_RUNS.format(job="Baking textures"), T.STARTED])

    def test_the_persons_own_queued_jobs_survive_planning(self):
        """S1: a job no agent call admitted (the Bake Maps card's Bake, Make Video's renders, Render the missing
        frames: ``call_id`` empty) is the person's press; planning restricts the agent, so it stays queued and runs."""
        bake, agents = self.queue("BAKE_MAPS", call_id=""), self.queue("BAKE_MAPS")
        PC.start(self.c)
        self.assertEqual(self.status(bake), ("queued", ""))
        self.assertEqual(self.status(agents), ("cancelled", PC.JOBS_CANCELLED_BY))
        self.jobs.poll()
        self.assertEqual(self.started, [bake])

    def test_a_failed_save_still_cancels(self):
        """S3: the chat's save raises (another Angelo holds it, a full disk) after planning is set: the cancel is keyed
        on the planning state, never on a normal return."""
        bake = self.queue("BAKE_TEXTURE")
        with patch.object(type(self.c), "save", side_effect=OSError("disk full")), self.assertRaises(OSError):
            PC.start(self.c)
        self.assertTrue(planning(self.c.state["options"]))
        self.assertEqual(self.status(bake), ("cancelled", PC.JOBS_CANCELLED_BY))
        self.jobs.poll()
        self.assertEqual(self.started, [])

    def test_the_agent_is_told_which_jobs_planning_cancelled(self):
        """NIT 2: START's result and Go's request name them, so the agent can queue them again after Go."""
        from angelo_agent import planning_state as S
        bake, export = self.queue("BAKE_TEXTURE"), self.queue("EXPORT_USD")
        result = PC.execute(self.c, None, {"action": "START"})
        self.assertIn("%s, %s" % (bake, export), result["note"])
        self.assertIn("%s, %s" % (bake, export), S.go_text("call-1", 0, PLAN, "ASK", cancelled=self.c._plan_cancelled))
        self.assertNotIn("cancelled", S.go_text("call-1", 0, PLAN, "ASK"))
        result = PC.execute(self.c, None, dict(PLAN, action="PROPOSE"))   # Go itself carries them, once.
        self.c.state["messages"].append({"role": "activity", "call_id": "call-1", "tool": "angelo_plan",
                                         "arguments": dict(PLAN, action="PROPOSE"), "result": result})
        sent = []
        with patch.object(PC, "_send", lambda controller, text: sent.append(text)):
            self.assertTrue(person(PC.go, self.c, "call-1", 0, "ASK"))
        self.assertIn("%s, %s" % (bake, export), sent[0])
        self.assertEqual(self.c._plan_cancelled, [])

    def test_every_route_into_planning_cancels_them(self):
        """Not only ``start``: agent code may narrow the chat into planning through ``set_options`` itself."""
        bake = self.queue("BAKE_MAPS")
        self.c.set_options(approval="READ_ONLY", planning=True, plan_return="ASK")
        self.assertEqual(self.status(bake), ("cancelled", PC.JOBS_CANCELLED_BY))
        self.assertTrue(planning(self.c.state["options"]))

    def test_no_press_that_ends_planning_starts_earlier_work(self):
        """S2: Stop planning (and Go, and the approval control) never start a job admitted under an earlier setting:
        the label says the chat asks before changes, and that is all the press does."""
        for end in ("stop", "approval control"):
            with self.subTest(end):
                person(self.c.set_options, approval="AUTO_BOUNDED")
                bake = self.queue("BAKE_TEXTURE")
                PC.start(self.c)
                if end == "stop":
                    self.assertTrue(person(PC.stop, self.c))
                else:
                    person(self.c.set_options, approval="ASK")
                self.assertFalse(planning(self.c.state["options"]))
                self.jobs.poll()
                self.assertNotIn(bake, self.started)
                self.assertEqual(self.status(bake), ("cancelled", PC.JOBS_CANCELLED_BY))

    def test_a_deleted_chat_that_was_not_planning_keeps_its_queued_job_running(self):
        """S1 (regression in e327003b8c2): a deleted chat in Auto with a bake queued behind a render. Its job runs as
        before plan mode, never held until a restart: nothing asks a chat's file whether it plans any more."""
        import os
        from angelo_agent.sessions import SessionStore
        person(self.c.set_options, approval="AUTO_BOUNDED")
        self.c.save()
        bake = self.queue("BAKE_TEXTURE")
        path = SessionStore(self.directory.name, self.c.state["id"]).path
        ui = SimpleNamespace(_controllers={}, _sessions=SimpleNamespace(records={}),
                             state_directory=lambda: self.directory.name)
        if path.exists():
            os.remove(path)   # Moved to Recently Deleted, as ``conversations_delete.delete`` does.
        with patch.dict(sys.modules, {"angelo_agent.ui": ui}):
            self.jobs.poll()
        self.assertEqual(self.started, [bake])
        self.assertFalse(hasattr(PC, "holds_work"))   # No job waits on a chat's Python state or its file.
        from angelo_agent import conversations_delete
        self.assertFalse(hasattr(conversations_delete, "_cancel_held_jobs"))


if __name__ == "__main__":
    unittest.main()
