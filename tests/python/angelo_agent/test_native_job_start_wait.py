# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_job_start`` answers with the job as the host knows it when the model reads it (job_start_wait).

1.0.8 dogfood (B-009): a still render ended about a second after its start result said ``running``; the chat showed
the host's "Job j1 (RENDER_STILL) done" row and then the model's reply that the render "is running … when it
finishes". The start result is now held while the job runs, for a few seconds at most, so a quick job's result is its
outcome; a job its own estimate puts past the hold returns at once, as before.

Dependency-free: job records and a stand-in store, no Blender.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import job_start_wait as wait  # noqa: E402
from angelo_agent.studio_agent import StudioPending  # noqa: E402

START = {"job": "j1", "status": "running", "kind": "RENDER_STILL", "output": "renders/tree.png",
         "cancel": "angelo_job_cancel with job j1 stops it",
         "note": "Runs in the background. Its progress and result arrive in a later message's jobs block; do not sleep "
                 "or poll. Scene changes are refused until it ends."}


def record(status="running", **detail):
    return {"id": "j1", "kind": "RENDER_STILL", "status": status, "request": {"output": "renders/tree.png"},
            "detail": dict(detail), "receipt": {}, "progress": 0.0, "eta_s": None, "started": 100.0, "ended": None,
            "reason": ""}


class Store:
    def __init__(self, value):
        self.value = value

    def get(self, identity):
        return self.value if identity == self.value["id"] else None


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class HoldLength(unittest.TestCase):
    def test_a_job_estimated_past_the_hold_returns_at_once_and_others_wait_a_little(self):
        self.assertEqual(wait.hold_seconds(record()), wait.DEFAULT_HOLD, "No estimate: the short default")
        self.assertAlmostEqual(wait.hold_seconds(record(estimate_s=2)), 2 * wait.ESTIMATE_FACTOR + 1.0)
        self.assertEqual(wait.hold_seconds(record(estimate_s=7)), 0.0, "Past MAX_HOLD: no wait at all")
        self.assertEqual(wait.hold_seconds(record(estimate_s=600)), 0.0)
        self.assertLessEqual(wait.hold_seconds(record(estimate_s=5.9)), wait.MAX_HOLD)

    def test_an_ended_or_unheld_job_returns_its_result_itself(self):
        for job in (record("failed"), record("done"), record("running", estimate_s=60)):
            with self.subTest(status=job["status"], detail=job["detail"]):
                self.assertIs(wait.held(Store(job), job, START), START)
        self.assertIs(wait.held(None, record(), START), START, "No store to read the job from: no hold")


class HeldResult(unittest.TestCase):
    def setUp(self):
        self.clock, self.job = Clock(), record()
        self.store = Store(self.job)
        self.pending = wait.held(self.store, self.job, START, clock=self.clock)
        self.assertIsInstance(self.pending, StudioPending)

    def test_a_caller_that_follows_the_job_itself_reads_its_start_held_or_not(self):
        # The GUI suites drive a job step by step and need its ID at once (1.0.8 slot: 17 suites indexed the hold).
        self.assertIs(wait.started(self.pending), START)
        self.assertIs(wait.started(START), START)

    def test_a_job_that_ends_in_the_hold_answers_with_its_outcome(self):
        self.assertIsNone(self.pending.poll(), "Still running, inside the hold: no answer yet")
        self.clock.now += 1.0
        self.job.update(status="done", ended=101.2, receipt={"files": ["renders/tree.png"]})
        answer = self.pending.poll()
        self.assertEqual(answer["status"], "done")
        self.assertEqual((answer["job"], answer["kind"], answer["files"]), ("j1", "RENDER_STILL", ["renders/tree.png"]))
        self.assertEqual(answer["seconds"], 1.2)
        self.assertNotIn("cancel", answer, "Nothing left to cancel")
        self.assertIn("ended before this result returned", answer["note"])
        self.assertNotIn("later message", answer["note"], "Never told that the outcome comes later")

    def test_a_failure_in_the_hold_says_it_ended_without_finishing(self):
        self.job.update(status="failed", ended=100.5, reason="Out of memory")
        answer = self.pending.poll()
        self.assertEqual((answer["status"], answer["reason"]), ("failed", "Out of memory"))
        self.assertIn("without finishing", answer["note"])

    def test_a_job_still_running_when_the_hold_ends_answers_with_its_progress(self):
        self.clock.now += wait.DEFAULT_HOLD + 0.01
        self.job.update(progress=0.4)
        answer = self.pending.poll()
        self.assertEqual((answer["status"], answer["progress"]), ("running", 0.4))
        self.assertEqual(answer["note"], START["note"], "Still running: the start note, as before")
        self.assertIn("cancel", answer)

    def test_stop_ends_only_the_wait_and_the_call_outlives_the_hold(self):
        self.pending.cancel()   # The job is untouched: the stand-in store is only read.
        self.assertEqual(self.job["status"], "running")
        self.assertGreater(self.pending.timeout, wait.DEFAULT_HOLD)


class Runner:
    """A job that ends when told (``done``): what ``JobManager`` calls on a runner, nothing else."""

    output_name = None

    def __init__(self):
        self.ended = False

    def describe(self):
        return {}

    def start(self):
        self.startup_effects_possible = False

    def poll(self):
        return None if self.ended else {"progress": 0.5}

    def finish(self, stopped):
        return "done", "", {}

    def abandon(self):
        pass


class SceneAfterTheJob(unittest.TestCase):
    """What an ended job's held result says about scene changes is read from the manager when it answers: the next
    queued job starts the moment this one ends (2nd audit of #121, P2: j1's result said "Scene changes are allowed
    again" while j2 ran)."""

    def setUp(self):
        import tempfile
        from angelo_agent.job_manager import JobManager
        from angelo_agent.job_store import JobStore
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.manager = JobManager(JobStore(Path(self.temp.name) / "jobs.json"))
        self.first, self.second = Runner(), Runner()
        request = {"kind": "RENDER_STILL", "output": "renders/a.png"}
        self.j1 = self.manager.submit("chat", "c1", dict(request), self.first)
        self.j2 = self.manager.submit("chat", "c2", dict(request, output="renders/b.png", queue=True), self.second)
        self.assertEqual((self.j1["status"], self.j2["status"]), ("running", "queued"))
        self.clock = Clock()
        self.pending = wait.held(self.manager.store, self.j1, START, clock=self.clock, active=self.manager.active)

    def answer(self):
        self.manager.poll()
        return self.pending.poll()

    def test_a_queued_job_that_starts_at_the_end_keeps_scene_changes_waiting(self):
        self.first.ended = True
        answer = self.answer()
        self.assertEqual((answer["status"], self.manager.active()["id"]), ("done", self.j2["id"]))
        self.assertNotIn("allowed again", answer["note"])
        self.assertIn("Job %s (RENDER_STILL) is running now, so scene changes still wait" % self.j2["id"],
                      answer["note"])

    def test_a_failure_with_the_next_job_running_says_so_too(self):
        self.first.finish = lambda stopped: ("failed", "Out of memory", {})
        self.first.ended = True
        answer = self.answer()
        self.assertEqual(answer["status"], "failed")
        self.assertIn("is running now", answer["note"])

    def test_the_last_job_ending_allows_scene_changes(self):
        self.manager.cancel(self.j2["id"], by="person")   # Nothing queued after it.
        self.first.ended = True
        answer = self.answer()
        self.assertIsNone(self.manager.active())
        self.assertTrue(answer["note"].endswith("Scene changes are allowed again."), answer["note"])

    def test_without_a_manager_to_read_nothing_is_claimed(self):
        job = record("done")
        self.assertNotIn("Scene changes", wait.current(START, job)["note"])


class Wiring(unittest.TestCase):
    def test_every_job_start_returns_through_the_hold(self):
        tree = ast.parse((ADDON / "job_host.py").read_text(encoding="utf-8"))
        execute = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_execute")
        last = execute.body[-1]
        self.assertIsInstance(last, ast.Return)
        self.assertEqual(ast.unparse(last.value), "held(getattr(manager, 'store', None), record, result, "
                                                   "active=getattr(manager, 'active', None))")

    def test_the_live_row_uses_labels_the_chat_already_has(self):
        from angelo_agent.chat_titles import LIVE
        render = wait.held(Store(record()), record(), START)
        bake = dict(record(), kind="BAKE_TEXTURE")
        other = wait.held(Store(bake), bake, START)
        self.assertEqual((render.status_text, other.status_text), ("Waiting for a render", "Staging native program"))
        self.assertTrue({render.status_text, other.status_text} <= set(LIVE))

    def test_the_hold_is_model_facing(self):
        source = (ROOT / "scripts/angelo/i18n/sources.py").read_text(encoding="utf-8")
        self.assertIn("job_start_wait", source.split("MODEL_FACING", 1)[1].split('"""', 2)[1])


if __name__ == "__main__":
    unittest.main()
