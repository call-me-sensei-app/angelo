# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""UV Layout's job driver (uv_layout_job.Layout) without Blender: uv_layout and _bpy._angelo_jobs stood in.

Covered: a pack starts, waits while its job runs, and the layout finishes when the steps end; a stop asks the job
to stop and puts the meshes back once it has ended; a fault ends it failed the same way, and a layout whose
meshes are gone ends at once; and nothing touches mesh data while any job holds the
interface lock (a render started mid-layout): no next step, no finish and no restore until the lock is gone
(break-it brief, 2026-11-15).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import importlib
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))


class FakeLayout(types.ModuleType):
    """uv_layout's surface the job driver uses, recording each call."""

    def __init__(self, packs):
        super().__init__("angelo_agent.uv_layout")
        self.packs, self.calls = packs, []

    def begin(self, objects, spec):
        self.calls.append("begin")
        return dict(spec), list(objects), "state"

    def steps(self, objects, spec):
        for k in range(self.packs):
            self.calls.append("step")
            yield {"scale": True, "merge": False, "done": k, "tiles": self.packs}
        self.calls.append("receipt")
        return {"tiles": [1001]}

    def pack(self, spec, step, job=False):
        self.calls.append("pack-job" if job else "pack")

    def finish(self, state, restore):
        self.calls.append("finish-restore" if restore else "finish")


class FakeJobs:
    def __init__(self):
        self.running, self.stops = False, 0

    def progress(self, scene, job):
        return 0.5 if self.running else None

    def stop(self, scene, job):
        self.stops += 1
        return self.running


def forget():
    """Drop the stand-in and the driver bound to it: from sys.modules and from the package's attributes."""
    import angelo_agent
    for name in ("uv_layout", "uv_layout_job"):
        sys.modules.pop("angelo_agent." + name, None)
        angelo_agent.__dict__.pop(name, None)


def driver(packs):
    forget()
    fake = FakeLayout(packs)
    sys.modules["angelo_agent.uv_layout"] = fake
    uv_layout_job = importlib.import_module("angelo_agent.uv_layout_job")
    jobs = FakeJobs()
    uv_layout_job._jobs = lambda: jobs
    return uv_layout_job.Layout(["Plates"], {"packer": "QUALITY"}), fake, jobs


class Driving(unittest.TestCase):
    def tearDown(self):
        forget()

    def test_a_pack_runs_as_a_job_and_the_layout_finishes(self):
        layout, fake, jobs = driver(1)
        self.assertEqual(layout.advance("scene", False)[0], "PACKING")
        self.assertEqual(fake.calls, ["begin", "step", "pack-job"])
        jobs.running = True
        self.assertEqual(layout.advance("scene", False), ("PACKING", 0.5))
        jobs.running = False
        self.assertEqual(layout.advance("scene", False), ("DONE", 1.0))
        self.assertEqual(fake.calls[-2:], ["receipt", "finish"])
        self.assertEqual(layout.receipt, {"tiles": [1001]})

    def test_nothing_touches_the_meshes_while_any_job_locks_the_interface(self):
        layout, fake, jobs = driver(2)
        layout.advance("scene", False)   # The first pack starts.
        before = list(fake.calls)
        for _ in range(3):   # Its job has ended, but a render now holds the lock.
            self.assertEqual(layout.advance("scene", True)[0], "PACKING")
        self.assertEqual(fake.calls, before, "no next step while another job holds the interface lock")
        self.assertEqual(layout.advance("scene", False)[0], "PACKING")
        self.assertEqual(fake.calls[-2:], ["step", "pack-job"])

    def test_a_stop_restores_only_after_the_job_and_any_lock_are_gone(self):
        layout, fake, jobs = driver(2)
        layout.advance("scene", False)
        jobs.running = True
        layout.stop("scene")
        self.assertEqual(jobs.stops, 1)
        self.assertEqual(layout.advance("scene", False)[0], "PACKING")   # The job is still ending.
        jobs.running = False
        self.assertEqual(layout.advance("scene", True)[0], "PACKING")    # Ended, but the interface is locked.
        self.assertNotIn("finish-restore", fake.calls)
        self.assertEqual(layout.advance("scene", False)[0], "STOPPED")
        self.assertEqual(fake.calls[-1], "finish-restore")
        self.assertNotIn("receipt", fake.calls)

    def test_a_fault_ends_failed_and_restores_once_the_pack_has_ended(self):
        layout, fake, jobs = driver(2)
        layout.advance("scene", False)
        jobs.running = True
        layout.fail("scene", "the pack could not start")
        self.assertEqual(jobs.stops, 1, "the running pack is asked to stop")
        self.assertEqual(layout.advance("scene", False)[0], "PACKING")
        jobs.running = False
        self.assertEqual(layout.advance("scene", False)[0], "FAILED")
        self.assertEqual(fake.calls[-1], "finish-restore")
        self.assertEqual(layout.error, "the pack could not start")
        self.assertNotIn("receipt", fake.calls)

    def test_abandon_ends_at_once_with_or_without_putting_back(self):
        layout, fake, jobs = driver(2)
        layout.advance("scene", False)
        layout.abandon("the object was removed")
        self.assertEqual(fake.calls[-1], "finish")
        self.assertEqual(layout.advance("scene", False)[0], "FAILED")
        layout, fake, jobs = driver(2)
        layout.advance("scene", False)
        layout.abandon("a second fault", restore=True)
        self.assertEqual(fake.calls[-1], "finish-restore")


if __name__ == "__main__":
    unittest.main()
