# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A hosted worker reports its part's object count with each save, so a merge's status knows it the moment the save
is announced (``worker_part_counts.saved_facts`` / ``note``, A′). No Blender: stand-ins for both sides.

* The first merge after a save is no longer refused as "still being checked" (native r2: every hosted merge was,
  once): no lease is asked and nothing is read for it.
* The lead trusts a report only for the exact file version it names (device, inode, mtime, size from its own stat),
  from the worker that serves the file, and only a count of at least one; anything else takes the lease path.
* The worker counts what its save wrote: its own objects with a user, never a linked one or one with no user.
"""

import os
import queue
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_worker_part_counts import Fixture  # noqa: E402
from angelo_agent import (worker_chat_lifecycle as lifecycle, worker_chat_part, worker_chat_wiring,  # noqa: E402
                          worker_part_counts as counts)


def objects(*specs):
    """A worker's ``bpy`` with objects given as (library, users)."""
    return SimpleNamespace(data=SimpleNamespace(objects=[SimpleNamespace(library=lib, users=users)
                                                         for lib, users in specs]))


class Reports(Fixture):
    def setUp(self):
        super().setUp()
        self.addCleanup(self.settle)   # Runs first: no lease thread outlives the stand-ins it calls.

    def settle(self):
        self.wedge.set()
        for thread in [t for t in threading.enumerate() if t.name == "AngeloPartCount"]:
            thread.join(5)

    def report(self, worker=None, facts=None):
        """The worker's save announced (``part_saved``) through the channel's router, as the reader thread runs it."""
        facts = counts.saved_facts(objects((None, 1), (None, 2)), str(self.path)) if facts is None else facts
        worker_chat_wiring.route(worker or self.worker, {"kind": "part_saved", "session": None, "data": {
            "path": str(self.path), "revision": 1, "layout": None, "part": facts}})

    def test_the_first_merge_after_a_save_is_not_refused(self):
        refusals = 0
        for version in range(3):   # Save, then merge at once, three times: WRITER_BUSY refusals per merge.
            self.path.write_bytes(b"BLENDER part v%d" % version + b"." * version)
            self.report()
            refused = self.refusal()
            refusals += refused is not None and refused.code == "WRITER_BUSY"
        self.assertEqual(refusals, 0)
        self.assertEqual((self.sent, self.reads), ([], []))   # No lease asked, nothing read for status.
        self.assertFalse(counts.checking(str(self.path)))

    def test_a_later_version_than_the_report_takes_the_lease_path(self):
        self.report()
        os.utime(self.path, ns=(1, 10 ** 18))   # The file changed after the report (or a report of another file).
        refused = self.refusal()
        self.assertEqual(refused.code, "WRITER_BUSY")
        self.assertEqual(self.asks(1), 1)
        self.assertTrue(self.granted())   # The fallback still settles it under the lease.
        self.assertIsNone(self.refusal())

    def test_only_the_worker_that_serves_the_file_is_trusted(self):
        self.report(worker=SimpleNamespace(alive=lambda: True))   # Another worker names this file's version.
        self.assertEqual(self.refusal().code, "WRITER_BUSY")
        self.assertEqual(self.asks(1), 1)

    def test_a_zero_or_malformed_report_is_never_trusted(self):
        stat = os.stat(self.path)
        identity = [stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size]
        for facts in ({"objects": 0, "identity": identity}, {"objects": True, "identity": identity},
                      {"objects": 2, "identity": identity[:3]}, {"objects": 2, "identity": [str(v) for v in identity]},
                      "2", None):
            counts.reset()
            self.report(facts=facts if facts is not None else False)
            self.assertEqual(self.refusal().code, "WRITER_BUSY", facts)

    def test_a_report_without_facts_drops_the_older_one(self):
        self.report()
        self.report(facts=False)   # A later save the worker could not count: its old report never stands for it.
        self.assertEqual(self.refusal().code, "WRITER_BUSY")

    def test_a_trusted_count_is_still_checked_by_the_merges_own_read(self):
        # Status may call the part mergeable from the report; the merge reads the file under its gate's lease
        # (``worker_merge.merge``: a file holding no objects is refused there, whatever was reported).
        source = (Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent/worker_merge.py").read_text()
        read = source.index("names, count = set(source.materials), len(source.objects)")
        self.assertIn("if count == 0:", source[read:read + 400])


class WorkerSide(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.part = Path(folder.name) / "part.blend"
        self.part.write_bytes(b"BLENDER")

    def test_it_counts_what_the_save_wrote(self):
        bpy = objects((None, 1), (None, 0), ("lib", 1), (None, 3))   # One with no user, one linked.
        facts = counts.saved_facts(bpy, str(self.part))
        stat = os.stat(self.part)
        self.assertEqual(facts, {"objects": 2, "identity": [stat.st_dev, stat.st_ino, stat.st_mtime_ns,
                                                            stat.st_size]})

    def test_it_reports_nothing_it_cannot_say(self):
        self.assertIsNone(counts.saved_facts(objects((None, 1)), str(self.part) + ".missing"))
        self.assertIsNone(counts.saved_facts(SimpleNamespace(), str(self.part)))

    def test_each_save_announces_its_versions_facts(self):
        outbox = queue.Queue()
        host = SimpleNamespace(bpy=SimpleNamespace(data=SimpleNamespace(
            is_dirty=True, objects=[SimpleNamespace(library=None, users=1)])), revision=4, ended=False,
            save=lambda: str(self.part), layout=lambda: {"objects": []})
        chats = SimpleNamespace(host=host, save_due=False, outbox=outbox, _layout=None, _save_after=0.0)
        self.assertIsNone(worker_chat_part.save_now(chats))
        data = outbox.get_nowait()["data"]
        self.assertEqual(data["part"]["objects"], 1)
        self.assertEqual(data["part"]["identity"][2], os.stat(self.part).st_mtime_ns)


def setUpModule():   # Journal reads run inline here, as each pump asks; the reader thread has its own tests.
    from angelo_agent import worker_mirror_reader
    worker_mirror_reader.bind(inline=True)


def tearDownModule():
    from angelo_agent import worker_mirror_reader
    worker_mirror_reader.reset()


if __name__ == "__main__":
    unittest.main()
