# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A merge's precondition reads a worker part's object count without the window waiting on its worker (A′
integration, FF's remaining main-thread wait). No Blender: ``bpy`` is a stand-in that counts the file's objects.

* A live worker's changed part: the lease is asked on a thread; until it is granted the count is UNKNOWN, the
  precondition refuses the part as not ready (WRITER_BUSY) and returns at once, even with the worker wedged.
* Once granted, the next pump reads the count under the lease and releases it; the part is mergeable as before.
* A refused lease stays not mergeable, and is asked again later; a file no worker serves is read at once.
"""

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import (parallel_sessions, worker_chat_lifecycle as lifecycle, worker_merge,  # noqa: E402
                          worker_merge_gate, worker_part_counts as counts)


class _Load:
    def __init__(self, objects):
        self.objects = objects

    def __enter__(self):
        return SimpleNamespace(objects=list(range(self.objects))), None

    def __exit__(self, *exc):
        return False


class Fixture(unittest.TestCase):
    def setUp(self):
        counts.reset()
        self.addCleanup(counts.reset)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "part.blend"
        self.path.write_bytes(b"BLENDER part")
        self.objects, self.reads, self.sent = 3, [], []
        self.wedge = threading.Event()   # Cleared: the worker answers nothing until set.
        self.grant = {"ok": True, "lease": "token-1"}
        bpy = SimpleNamespace(data=SimpleNamespace(libraries=SimpleNamespace(load=self._load)))
        self.worker = SimpleNamespace(alive=lambda: True)
        for patcher in (mock.patch.dict(sys.modules, {"bpy": bpy}),
                        mock.patch.object(lifecycle, "enabled", lambda environ=None: True),
                        mock.patch.object(lifecycle, "_serving", lambda path: self.worker),
                        mock.patch.object(lifecycle, "_send", self._send),
                        mock.patch.object(worker_merge_gate, "leased", lambda path: False),
                        mock.patch.object(parallel_sessions, "refuse_merged_again", lambda *args: None)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self._settle)   # Runs first: before the stand-ins above are taken away.

    def _settle(self):
        """Every lease and release thread this test started has ended while its stand-ins still answer: none is left
        to call the real worker channel after the test, or to print while the interpreter exits (an abort at exit)."""
        self.wedge.set()   # Never leave a lease thread waiting.
        counts.reset()     # Releases what was granted, on threads of its own.
        deadline = time.monotonic() + 10   # A lease thread that ends may start a release thread: until none is left.
        while time.monotonic() < deadline:
            left = [t for t in threading.enumerate() if t.name == "AngeloPartCount" and t.is_alive()]
            if not left:
                return
            left[0].join(max(0.0, deadline - time.monotonic()))
        self.fail("a part-count thread outlived its test")

    def _load(self, path):
        self.reads.append(path)
        return _Load(self.objects)

    def _send(self, session, op, worker, timeout, **fields):
        self.sent.append(op)
        if op == "part.lease":
            if not self.wedge.wait(timeout):
                raise lifecycle.ChatOpFailed("part.lease", None, "timed out")
            if isinstance(self.grant, Exception):
                raise self.grant
            return self.grant
        return {"ok": True}

    def refusal(self):
        return worker_merge.part_refusal(1, None, str(self.path), None)

    def asks(self, at_least):
        """The leases asked once ``at_least`` have been: each is sent from a lease thread of its own."""
        deadline = time.monotonic() + 10
        while self.sent.count("part.lease") < at_least and time.monotonic() < deadline:
            time.sleep(0.005)
        return self.sent.count("part.lease")

    def granted(self):
        """The worker answered and the pump ran: True once the count is known."""
        self.wedge.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            counts.pump(now=0.0)
            if counts.count(str(self.path)) is not counts.UNKNOWN:
                return True
            time.sleep(0.005)
        return False


class WedgedWorker(Fixture):
    def test_the_precondition_never_waits_on_a_wedged_worker_and_fails_closed(self):
        started = time.perf_counter()
        refused = self.refusal()
        for _ in range(5):
            counts.pump(now=0.0)
            refused = self.refusal()
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 0.05, "a merge's precondition waited on a wedged worker")
        self.assertIsNotNone(refused)
        self.assertEqual(refused.code, "WRITER_BUSY")
        self.assertIn("still being checked", str(refused))
        self.assertEqual(self.reads, [])   # Nothing read without the lease.
        self.assertTrue(counts.checking(str(self.path)))   # Its tile says the part is being checked.
        self.assertEqual(self.asks(1), 1)  # One ask, not one per call.

    def test_a_granted_lease_reads_on_the_pump_releases_it_and_the_part_is_mergeable(self):
        self.assertIsNotNone(self.refusal())
        self.assertTrue(self.granted())
        self.assertIsNone(self.refusal())
        self.assertEqual(len(self.reads), 1)
        deadline = time.monotonic() + 5
        while "part.unlease" not in self.sent and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertIn("part.unlease", self.sent)
        self.assertFalse(counts.checking(str(self.path)))

    def test_an_empty_part_is_still_refused_once_known(self):
        self.objects = 0
        self.assertTrue(self.granted())
        refused = self.refusal()
        self.assertEqual(refused.code, "VALIDATION")
        self.assertIn("holds no objects", str(refused))

    def test_a_new_version_of_the_file_is_asked_again(self):
        self.assertTrue(self.granted())
        self.assertIsNone(self.refusal())
        self.wedge.clear()
        self.path.write_bytes(b"BLENDER part, saved again")   # A later save: a new size, a new version.
        refused = self.refusal()
        self.assertEqual(refused.code, "WRITER_BUSY")
        self.assertEqual(self.asks(2), 2)

    def test_a_refused_lease_stays_not_mergeable_and_is_asked_again_later(self):
        self.grant = lifecycle.ChatOpFailed("part.lease", None, "busy saving")
        self.wedge.set()
        self.assertIsNotNone(self.refusal())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with counts._lock:
                if counts._asks[str(self.path)].state == "refused":
                    break
            time.sleep(0.005)
        self.assertEqual(self.refusal().code, "WRITER_BUSY")
        self.assertEqual(self.sent.count("part.lease"), 1)   # Not before RETRY_SECONDS.
        with counts._lock:
            counts._asks[str(self.path)].retry = 0.0
        self.grant = {"ok": True, "lease": "token-2"}
        self.assertIsNotNone(self.refusal())
        self.assertTrue(self.granted())
        self.assertIsNone(self.refusal())


class NothingToWaitOn(Fixture):
    def test_a_file_no_worker_serves_is_read_at_once(self):
        with mock.patch.object(lifecycle, "_serving", lambda path: None):
            self.assertIsNone(self.refusal())
        self.assertEqual(self.sent, [])
        self.assertEqual(len(self.reads), 1)

    def test_a_merges_own_read_uses_its_gates_lease(self):
        with mock.patch.object(worker_merge_gate, "leased", lambda path: True):
            self.assertIsNone(self.refusal())
        self.assertEqual(self.sent, [])

    def test_with_the_switch_off_it_reads_as_before(self):
        with mock.patch.object(lifecycle, "enabled", lambda environ=None: False):
            self.assertIsNone(self.refusal())
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
