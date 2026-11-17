# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A part's lease asked of a worker Angelo is stopping (``worker_merge_gate.stopped_within``). No Blender.

A merge stops the workers it merged; a merge right after it (after its Undo, say) found a worker whose connection had
closed and whose process had not yet exited, asked it for a lease, and was refused WRITER_BUSY ("Could not reach the
worker (I/O operation on closed file.)", "The worker has ended": ``test_blender_parallel_merge``'s
``a_part_merges_again_after_undo`` on a loaded machine). Pinned: such a worker's process is waited for, bounded by the
lease's own time, and its file then read without a lease, in a merge's gate and in any other read; one that does not
end in that time still refuses; a worker still connected is never waited for. Fake processes, no clock.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import subprocess
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import worker_chat_lifecycle as lifecycle, worker_merge_gate as gate  # noqa: E402

PART = "/parts/worker-1/part.blend"


class Process:
    """A worker's process: running until ``wait`` (it ends then, or not, as ``ends`` says)."""

    def __init__(self, ends):
        self.ends, self.waits, self.code = ends, [], None

    def poll(self):
        return self.code

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if not self.ends:
            raise subprocess.TimeoutExpired("worker", timeout)
        self.code = 0
        return 0


def worker(connected, ends):
    ended = threading.Event()
    if not connected:
        ended.set()
    return SimpleNamespace(process=Process(ends), client=SimpleNamespace(closed=not connected, ended=ended),
                           alive=lambda: connected)


def unreachable(*_args, **_kwargs):
    raise lifecycle.ChatOpFailed("part.lease", None, "Could not reach the worker (I/O operation on closed file.)")


class ReadLease(unittest.TestCase):
    def lease(self, target):
        with mock.patch.object(lifecycle, "_serving", return_value=target), \
                mock.patch.object(lifecycle, "_send", side_effect=unreachable):
            return lifecycle._lease(PART, 1.0)

    def test_a_stopping_workers_file_is_read_once_its_process_ended(self):
        target = worker(connected=False, ends=True)
        self.assertIsNone(self.lease(target))
        self.assertEqual(target.process.waits, [1.0])   # Bounded by the lease's own wait.

    def test_one_that_does_not_end_in_time_still_refuses(self):
        with self.assertRaises(lifecycle.ChatOpFailed):
            self.lease(worker(connected=False, ends=False))

    def test_a_connected_worker_is_never_waited_for(self):
        target = worker(connected=True, ends=True)
        with self.assertRaises(lifecycle.ChatOpFailed):
            self.lease(target)
        self.assertEqual(target.process.waits, [])


class MergeGate(unittest.TestCase):
    def gate(self, target):
        def ask(_session, _op, _worker, _timeout, _fields, then, after=None):
            then(lifecycle.ChatOpFailed("part.lease", None, "The worker has ended", sent=False))
        with mock.patch.object(lifecycle, "_serving", return_value=target), mock.patch.object(gate, "_ask", ask):
            return gate.Gate([], [PART]).start()

    def test_a_merge_after_a_stop_reads_the_stopped_workers_part(self):
        target = worker(connected=False, ends=True)
        merge = self.gate(target)
        self.assertTrue(merge.done.is_set())
        self.assertIsNone(merge.error)
        self.assertEqual(len(target.process.waits), 1)
        self.assertLessEqual(target.process.waits[0], lifecycle.LEASE_WAIT)

    def test_a_merge_still_refuses_a_worker_that_does_not_end(self):
        merge = self.gate(worker(connected=False, ends=False))
        self.assertIsNotNone(merge.error)
        self.assertEqual(merge.error.code, "WRITER_BUSY")

    def test_a_merge_never_waits_on_a_connected_worker(self):
        target = worker(connected=True, ends=True)
        merge = self.gate(target)
        self.assertIsNotNone(merge.error)
        self.assertEqual(target.process.waits, [])


if __name__ == "__main__":
    unittest.main()
