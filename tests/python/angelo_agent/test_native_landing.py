# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Outside work lands where a result applies, never on progress (doc/angelo/grant_operators.rst; the review of
a6edbf3, MUST). Each landing site of test_native_dialog_consent's LANDS, driven as its timer drives it: N progress
or status-only updates note nothing (``grant_gestures.landed`` never runs), and one result notes exactly once. A note
on progress would end every dialog's held consent and gesture's hold every few seconds of a generation, so the
person could not grant anything while it ran. The engine link's case is in test_native_engine_link_state."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).
import team_library_fixtures  # noqa: E402,F401  The stand-ins library_host and fab_host import with.

import queue
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from angelo_agent import grant_gestures  # noqa: E402

PROGRESS_TICKS = 7


class Landings(unittest.TestCase):
    def setUp(self):
        self.notes = []
        patcher = patch.object(grant_gestures, "landed", lambda: self.notes.append(True))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_generation_lands_its_files_once_and_its_progress_never(self):
        from angelo_agent import studio_commits, studio_generation
        assets = {"a1": {"id": "a1", "status": "generating", "progress": 0}}
        library = SimpleNamespace(assets=assets, update=lambda record: assets.__setitem__(record["id"], record))
        jobs = SimpleNamespace(commits=studio_commits.Commits(library), events=queue.Queue(), active={"a1": 1},
                               kinds={"a1": "MODEL"})
        for tick in range(PROGRESS_TICKS):   # Meshy/Tripo report progress every few seconds.
            jobs.events.put(("a1", {"progress": 5 + tick, "status": "generating"}))
            self.assertTrue(studio_generation.StudioJobs.pump(jobs), "progress still changes the card")
        self.assertEqual(self.notes, [])
        jobs.events.put(("a1", {"status": "ready", "progress": 100, "files": ["model.glb"]}))
        studio_generation.StudioJobs.pump(jobs)
        self.assertEqual(len(self.notes), 1)

    def test_a_flow_run_lands_a_finished_step_once_and_a_start_never(self):
        from angelo_agent.flow_runner import ACTIVE, DONE, QUEUED, advance
        steps = {"n1": {"state": QUEUED}}
        states = iter([ACTIVE] * PROGRESS_TICKS + [DONE])

        def tick():
            steps["n1"]["state"] = next(states)
            return True   # Every tick changes something (a start, a settle).
        run = SimpleNamespace(steps=steps, tick=tick)
        for _ in range(PROGRESS_TICKS):
            self.assertTrue(advance(run))
        self.assertEqual(self.notes, [])
        self.assertTrue(advance(run))
        self.assertEqual(len(self.notes), 1)

    def test_a_library_fetch_lands_once_and_its_refreshes_never(self):
        from angelo_agent import library_host
        with tempfile.TemporaryDirectory() as folder:
            library = library_host.Library(Path(folder))
            for _ in range(PROGRESS_TICKS):
                library._results.put(("refreshed", None))
                library.pump()
            self.assertEqual(self.notes, [])
            imported = []
            future = SimpleNamespace(cancelled=lambda: False, exception=lambda: None, result=lambda: "path",
                                     lease=SimpleNamespace(release=lambda: None), done=lambda: True)
            library._results.put(("fetched", (lambda value, error: imported.append(value), future)))
            library.pump()
            self.assertEqual((imported, len(self.notes)), (["path"], 1))

    def test_a_fab_import_lands_its_result_once_and_its_waiting_never(self):
        from angelo_agent import fab_host
        host = fab_host.Host(lambda **kwargs: SimpleNamespace(login_mode="managed", close=lambda **kw: None,
                                                               service_idle=lambda *a, **k: None))
        results = iter([None] * PROGRESS_TICKS + [{"success": True}])
        host.import_pending = SimpleNamespace(poll=lambda: next(results), cancel=lambda: None)
        for _ in range(PROGRESS_TICKS):
            host.pump()
        self.assertEqual(self.notes, [])
        host.pump()
        self.assertEqual(len(self.notes), 1)

    def test_a_photo_drop_lands_its_placement_once_and_its_waiting_never(self):
        import types
        from unittest import mock
        import addon_stand_in_import as stand_ins
        bpy = types.ModuleType("bpy")
        bpy.types, bpy.props = types.ModuleType("bpy.types"), types.ModuleType("bpy.props")
        bpy.types.__getattr__ = lambda name: type(name, (), {})
        handlers = types.ModuleType("bpy.app.handlers")
        handlers.persistent = lambda function: function
        bpy.app = SimpleNamespace(handlers=handlers, timers=SimpleNamespace(register=lambda *a, **k: None))
        mathutils = types.ModuleType("mathutils")
        mathutils.Vector = tuple
        with mock.patch.dict(sys.modules, {"bpy.app": bpy.app, "bpy.app.handlers": handlers, "mathutils": mathutils}):
            (jobs,) = stand_ins.import_against_stand_ins(bpy, ["photo_drop_jobs"])
        verdicts = iter(["wait"] * PROGRESS_TICKS + ["place"])
        placed = []
        with patch.object(jobs, "_window_for", lambda placement: object()), \
                patch.object(jobs, "_can_place", lambda window: True), \
                patch.object(jobs, "decide", lambda *args: next(verdicts)), \
                patch.object(jobs, "_place", lambda placement, window: placed.append(placement)):
            placement = SimpleNamespace(waiting=False)
            jobs._pending["a1"] = placement
            for _ in range(PROGRESS_TICKS):   # The asset is still generating: it waits.
                jobs._step("a1", placement, {"status": "generating"})
            self.assertEqual(self.notes, [])
            jobs._step("a1", placement, {"status": "ready"})
        self.assertEqual((len(placed), len(self.notes)), (1, 1))

    def test_a_job_lands_its_end_once_and_its_progress_never(self):
        from angelo_agent.job_manager import JobManager
        from angelo_agent.job_store import JobStore
        from test_native_job_start_wait import Runner
        with tempfile.TemporaryDirectory() as folder:
            manager = JobManager(JobStore(Path(folder) / "jobs.json"))
            runner = Runner()
            manager.submit("chat", "c1", {"kind": "RENDER_STILL", "output": "renders/a.png",
                                                  "minutes": 5.0}, runner)   # As job_spec validates it.
            for _ in range(PROGRESS_TICKS):
                manager.poll()   # Its progress, each poll.
            self.assertEqual(self.notes, [])
            runner.ended = True
            manager.poll()
            self.assertEqual(len(self.notes), 1)


if __name__ == "__main__":
    unittest.main()
