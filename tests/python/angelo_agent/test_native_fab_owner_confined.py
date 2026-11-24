# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An agent script never starts or wedges the Fab owner worker (re-review of 1c31d63dead, the Fab owner worker).

``library_host.fab_search``/``fab_lookup``/``fab_disconnect`` are plain functions a script could import and call.
``fab_host.human``, the boundary every one of them goes through, refuses while agent code runs; and a refused
thread start (the confinement raises PermissionError, not RuntimeError) leaves the worker restartable instead of
holding an unstarted thread that every later request waited behind. No Blender, no browser.
"""
import suite_isolation  # noqa: F401
from host_waits import CAP_S  # noqa: E402  A completion wait's cap: only a hang reaches it.
import threading
import unittest
from unittest.mock import patch
import team_library_fixtures  # noqa: F401
from angelo_agent import fab_host, fab_host_jobs, grant_guard


class FabOwnerConfinedTests(unittest.TestCase):
    def test_a_script_is_refused_at_the_persons_action_boundary(self):
        owner = patch.object(fab_host, "owner", side_effect=AssertionError("reached the Fab owner"))
        with owner, patch.object(grant_guard, "inside_agent_code", return_value=True):
            with self.assertRaises(grant_guard.Refused):
                fab_host.human("search", "chair", 8)

    def test_a_refused_thread_start_leaves_the_worker_restartable(self):
        worker = fab_host_jobs.Worker()
        refused = fab_host_jobs.Job(1, "search", lambda cancel: "first")
        with patch.object(threading.Thread, "start", side_effect=PermissionError("thread refused")):
            with self.assertRaises(PermissionError):
                worker.submit(refused)
        self.assertIsNone(worker._thread, "no unstarted thread kept as the owner")
        job = worker.submit(fab_host_jobs.Job(2, "search", lambda cancel: "second"))
        self.assertTrue(job.done.wait(CAP_S), "the next request runs")
        self.assertEqual(job.take(), "second")


if __name__ == "__main__":
    unittest.main()
