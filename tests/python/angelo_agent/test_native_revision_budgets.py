# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Cooperative revision budgets count the main thread's own work, never its waits.

``test_blender_program_stress`` (at a machine load of about 130, the
``toolset21`` material task) failed with "o4 has no revision in your latest
observation": the send's turn context spent its 25 ms budget, then measured in
wall time, while the main thread waited after the active object's revision, so
the object the message named got ``revision_omitted: BUDGET``. The context's
identifier table then held no observed revision for it and the recipe that left
its revision to inference was refused. Two waits stand in for what the main
thread waited for: a sleep (a CPU, another process's load) and this process's
own worker burning CPU while the main thread waits (the snapshot worker, which a
process-CPU clock would still count). Real work of the main thread must still
exhaust both budgets. The real turn-context builder and scene queries over the
shared fakes, the real identifier table and post-commit read-back; no Blender.
"""

import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_turn_context import TurnContextFixture, queries  # noqa: E402
import test_native_program_commit_effects as commit_effects  # noqa: E402
from angelo_agent import context_ledger, program_commit, turn_context, work_clock  # noqa: E402
from angelo_agent.identifiers import IdentifierTable, expand_call  # noqa: E402

MESSAGE = ("Give every face of 'Eval Cube' a Principled BSDF material named 'QA Graded Material' with base colour "
           "(0.25, 0.5, 0.75, 1), roughness 0.3 and metallic 0.4.")
MATERIAL = {"name": "QA Graded Material", "base_color": [0.25, 0.5, 0.75, 1], "roughness": 0.3, "metallic": 0.4}


def busy(seconds):
    """``seconds`` of CPU work on the calling thread (the process's only running thread)."""
    started = time.process_time()
    while time.process_time() - started < seconds:
        pass


def waits_for_a_cpu(seconds):
    """The main thread waits, doing no work: wall time passes, no CPU time."""
    time.sleep(seconds)


def waits_for_a_worker(seconds):
    """This process's own worker does ``seconds`` of CPU work while the main thread waits for it."""
    worker = threading.Thread(target=busy, args=(seconds,), name="stand-in snapshot worker")
    worker.start()
    worker.join()


WAITS = (waits_for_a_cpu, waits_for_a_worker)


class TurnContextBudgetTests(TurnContextFixture):
    def setUp(self):
        super().setUp()
        self.named = self.objects[5]
        self.named.name = "Eval Cube"

    def observe(self, after_active):
        """The stress task's send: ``after_active`` runs right after the active object's revision."""
        revision = queries.object_revision

        def active_first(window, obj):
            value = revision(window, obj)
            if obj is self.active:
                after_active(2 * turn_context.REVISION_BUDGET_SECONDS)
            return value

        with patch.object(queries, "object_revision", side_effect=active_first):
            observation = turn_context.prepare(self.window, "chat-a", message=MESSAGE).observation
        self.assertIn("object_revision", observation["objects"][0], "The active object came first")
        entry = next(entry for entry in observation["objects"] if entry["name"] == "Eval Cube")
        self.assertTrue(entry["mentioned"] and not entry["active"] and not entry["selected"])
        return observation, entry

    def test_a_send_that_waited_after_the_active_object_keeps_the_named_objects_revision(self):
        for wait in WAITS:
            with self.subTest(wait=wait.__name__):
                _, entry = self.observe(wait)
                self.assertEqual(entry.get("object_revision"), queries.object_revision(self.window, self.named),
                                 entry.get("revision_omitted"))

    def test_the_stress_tasks_recipe_binds_its_inferred_revision_after_the_wait(self):
        for wait in WAITS:
            with self.subTest(wait=wait.__name__):
                observation, _ = self.observe(wait)
                table = IdentifierTable()
                context_ledger.compact_observation(observation, table)
                # The toolset21 plan of the failing task: no revision, the host infers it.
                arguments = expand_call("angelo_apply_recipe", {
                    "recipe": "material-setup", "target": queries.object_handle(self.named), "mode": "APPLY",
                    "parameters": MATERIAL}, table)
                self.assertEqual(arguments["revision"], queries.object_revision(self.window, self.named))

    @patch("angelo_agent.revision_budget.SMALL_REVISION_VERTICES", 0)  # Every fake mesh counts as large.
    def test_real_work_still_exhausts_the_budget(self):
        _, entry = self.observe(busy)
        self.assertNotIn("object_revision", entry)
        self.assertEqual(entry["revision_omitted"], {"reason": "BUDGET", "detail": "cooperative_turn_context_budget"})

    def test_real_work_leaves_a_small_revision_in_while_its_allowance_lasts(self):
        from angelo_agent import revision_budget
        with patch.object(revision_budget, "SMALL_ALLOWANCE_SECONDS", 10.0):  # The wait is 50 ms of real work.
            _, entry = self.observe(busy)
        self.assertEqual(entry.get("object_revision"), queries.object_revision(self.window, self.named))


# The clock's semantics at the earlier 50 ms: the read-back's own 0.5 s (revision_budget) would make each wait a second.
@patch.object(program_commit, "POST_COMMIT_REVISION_SECONDS", 0.05)
class PostCommitBudgetTests(unittest.TestCase):
    """The post-commit read-back's cooperative budget, between written and created objects (fakes without data are
    never small: revision_budget.small)."""

    # The read-back fixture of test_native_program_commit_effects (its tests run there).
    Backend, obj = commit_effects.LiveRevisionTests.Backend, staticmethod(commit_effects.LiveRevisionTests.obj)
    setUp = commit_effects.LiveRevisionTests.setUp

    def commit(self, after_first):
        revision = self.store.object_revision

        def first_object_waits(window, obj):
            value = revision(window, obj)
            if obj is self.written:
                after_first(2 * program_commit.POST_COMMIT_REVISION_SECONDS)
            return value

        self.store.object_revision = first_object_waits
        return self.Backend(self.store).post_commit_revisions(self.stage, [self.made])["objects"]

    def test_a_commit_that_waited_still_reports_every_revision(self):
        for wait in WAITS:
            with self.subTest(wait=wait.__name__):
                self.setUp()
                written, made = self.commit(wait)
                self.assertEqual((written["object_revision"], made.get("object_revision")), ("r1", "r2"),
                                 made.get("revision_omitted"))

    def test_real_work_still_exhausts_the_post_commit_budget(self):
        written, made = self.commit(busy)
        self.assertEqual(written["object_revision"], "r1")
        self.assertEqual(made["revision_omitted"], {"reason": "BUDGET", "detail": "cooperative_post_commit_budget"})


class WorkClockTests(unittest.TestCase):
    def test_a_per_thread_cpu_clock_is_in_use(self):
        # Blender's bundled macOS Python has no time.thread_time; the libSystem clock stands in.
        if sys.platform in ("darwin", "linux", "win32"):
            self.assertIsNotNone(work_clock.THREAD_CLOCK)

    def test_only_the_calling_threads_work_counts(self):
        clock = work_clock.WorkClock()
        waits_for_a_worker(0.05)
        waits_for_a_cpu(0.05)
        self.assertLess(clock(), turn_context.REVISION_BUDGET_SECONDS)
        busy(0.05)
        self.assertGreaterEqual(clock(), turn_context.REVISION_BUDGET_SECONDS)

    def test_without_a_thread_clock_the_smaller_of_wall_and_process_time_bounds_the_work(self):
        clock = work_clock.WorkClock(thread_clock=None)
        waits_for_a_cpu(0.05)
        self.assertLess(clock(), turn_context.REVISION_BUDGET_SECONDS, "Waiting for a CPU spends no process time")
        busy(0.05)
        self.assertGreaterEqual(clock(), 0.05)


if __name__ == "__main__":
    unittest.main()
