# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Workspace metadata yields, cancels and bounds worker count without moving live bpy access off-thread."""

import os
import threading
import time
import unittest
from unittest.mock import patch

import test_native_blend_file as fixtures
from angelo_agent import blend_file_pending as pending
from angelo_agent.read_spec import validate_read
from angelo_agent.studio_agent import StudioPending


@unittest.skipUnless(os.name == "posix", "anchored POSIX fixture")
class PendingMetadataTests(unittest.TestCase):
    def setUp(self):
        fixtures.WorkspaceFileTests.setUp(self)
        self.args = validate_read("angelo_blend_file", {"file": "stages/s8.blend"})

    def finished(self, result):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            value = result.poll()
            if value is not None:
                return value
            time.sleep(.002)
        self.fail("metadata worker did not finish")

    def test_the_controller_gets_pending_before_file_work_finishes(self):
        entered, release = threading.Event(), threading.Event()
        workers = []
        def blocked(_descriptor, _token):
            workers.append(threading.get_ident())
            entered.set()
            release.wait(2)
        self.metadata.before_scan = blocked
        result = pending.begin(self.args, self.workspace)
        self.assertIsInstance(result, StudioPending)
        self.assertTrue(entered.wait(1))
        self.assertIsNone(result.poll())
        self.assertNotEqual(workers, [threading.get_ident()])
        release.set()
        value = self.finished(result)
        self.assertEqual((value["readable"], value["read_only"], value["scene_changed"]), (True, True, False))
        self.assertIn("native_exec_ms", value)
        self.assertEqual(self.libraries.calls, [])
        self.assertFalse(pending._active)

    def test_stop_waits_for_worker_cleanup_before_releasing_its_slot(self):
        entered = threading.Event()
        def blocked(_descriptor, token):
            entered.set()
            token.wait(2)
        self.metadata.before_scan = blocked
        result = pending.begin(self.args, self.workspace)
        self.assertTrue(entered.wait(1))
        descriptor = self.metadata.calls[0][0]
        result.cancel()
        value = self.finished(result)
        self.assertEqual(value["code"], "CANCELLED")
        with self.assertRaises(OSError):
            os.fstat(descriptor)
        self.assertFalse(pending._active)

    def test_simultaneous_reads_have_a_fixed_admission_limit(self):
        both_entered, lock, entered = threading.Event(), threading.Lock(), []
        def blocked(descriptor, token):
            with lock:
                entered.append(descriptor)
                if len(entered) == pending.MAX_ACTIVE:
                    both_entered.set()
            token.wait(2)
        self.metadata.before_scan = blocked
        active = [pending.begin(self.args, self.workspace) for _ in range(pending.MAX_ACTIVE)]
        try:
            self.assertTrue(both_entered.wait(1))
            self.assertEqual(pending.begin(self.args, self.workspace)["code"], "BUSY")
            self.assertEqual(len(self.metadata.calls), pending.MAX_ACTIVE)
        finally:
            for result in active:
                result.cancel()
            for result in active:
                self.finished(result)
        self.assertFalse(pending._active)

    def test_fifo_refusals_do_not_consume_worker_slots(self):
        for index in range(pending.MAX_ACTIVE):
            file = "pipe%d.blend" % index
            os.mkfifo(self.root / file)
            args = validate_read("angelo_blend_file", {"file": file})
            self.assertEqual(self.finished(pending.begin(args, self.workspace))["code"], "NOT_A_FILE")
        self.assertFalse(pending._active)
        self.assertEqual(self.metadata.calls, [])
        self.assertTrue(self.finished(pending.begin(self.args, self.workspace))["readable"])

    def test_regular_entry_swapped_to_fifo_cannot_block_a_cancelled_worker(self):
        entered, release = threading.Event(), threading.Event()
        path = self.root / self.args["file"]
        original = pending.listing._open_file

        def swapped(folder, name, flags, mode=0o600):
            path.unlink()
            os.mkfifo(path)
            entered.set()
            release.wait(2)
            return original(folder, name, flags, mode)

        with patch.object(pending.listing, "_open_file", swapped):
            result = pending.begin(self.args, self.workspace)
            try:
                self.assertTrue(entered.wait(1))
                result.cancel()
                release.set()
                value = self.finished(result)
            finally:
                release.set()
                # Keep a future regression from leaking a worker: a temporary writer unblocks
                # even an incorrectly blocking read-open. The assertions above still fail.
                writer = os.open(path, os.O_RDWR | os.O_NONBLOCK)
                try:
                    self.finished(result)
                finally:
                    os.close(writer)
        self.assertEqual(value["code"], "NOT_A_FILE")
        self.assertFalse(pending._active)
        self.assertEqual(self.metadata.calls, [])
        path.unlink()
        path.write_bytes(b"BLENDER-v502" + b"\0" * 64)
        self.assertTrue(self.finished(pending.begin(self.args, self.workspace))["readable"])


if __name__ == "__main__":
    unittest.main()
