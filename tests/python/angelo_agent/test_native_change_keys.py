# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``change_keys``: a change detector's key of a file is one key, in one form, while the file is unchanged, and
another after any write, in the file system clock's tick too.

Covered: a file first seen settled is keyed by its stat and never read; one first seen in its clock's tick is keyed
by its bytes, read again while unsettled and once more when settled, then never (round 13's follow-up: a digest
while unsettled and a stat once settled read as one change with no write); a same-size write in the tick that no stat
shows is seen when the clock passes; a rewrite first seen settled is a change; a change time ahead of the clock is
settled; a stat once judged settled keeps its form when the clock reaches a time it carries (a time set ahead of the
clock: break-it review, M1); ``wait=False`` never waits; the bound on files kept and on bytes read per call; detectors
never share a form; and with the real clock, a file written just now keeps its key once the clock passes it.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
from angelo_agent import change_keys  # noqa: E402

CLOCK = change_keys.change_clock


def unsettled():
    """The clock hasn't passed any stat yet (a FAT, exFAT or network volume just after a write)."""
    return mock.patch.multiple(CLOCK, settle=mock.Mock(side_effect=CLOCK.Unsettled("tick")),
                               settled=mock.Mock(return_value=False))


class ChangeKeyTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="angelo-change-keys-"))
        self.addCleanup(lambda: [path.unlink() for path in self.folder.iterdir()] and self.folder.rmdir())
        self.keys = change_keys.ChangeKeys()
        self.path = str(self.folder / "plate.exr")
        self.write(b"first bytes")
        self.reads = []
        digest = change_keys._digest
        patch = mock.patch.object(change_keys, "_digest", lambda *args: self.reads.append(args[0]) or digest(*args))
        patch.start()
        self.addCleanup(patch.stop)

    def write(self, data):
        Path(self.path).write_bytes(data)

    def key(self, **options):
        return change_keys.key(os.stat(self.path), self.path, keys=self.keys, **options)

    def test_a_file_first_seen_settled_is_keyed_by_its_stat_and_never_read(self):
        info = os.stat(self.path)
        first = self.key()
        self.assertEqual(first, (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
        self.assertEqual(self.key(), first)
        self.assertEqual(self.reads, [])
        self.write(b"other bytes")
        self.assertNotEqual(self.key(), first)

    def test_the_site_key_is_used_as_given(self):
        self.assertEqual(self.key(stat_key=("site", 1)), ("site", 1))

    def test_a_file_first_seen_unsettled_keeps_its_key_when_the_clock_passes_it(self):
        with unsettled():
            first = self.key()
            self.assertEqual(first[0], "sha256")
            self.assertEqual(self.key(), first, "unchanged while unsettled")
        for _ in range(3):
            self.assertEqual(self.key(), first, "the clock passed it: still its bytes")
        self.assertEqual(len(self.reads), 3, "twice while unsettled, once when settled, never again")
        self.write(b"later bytes, a new size")
        self.assertNotEqual(self.key(), first)

    def test_a_write_in_the_tick_that_no_stat_shows_is_seen_when_the_clock_passes(self):
        info = os.stat(self.path)
        with unsettled():
            first = change_keys.key(info, self.path, keys=self.keys)
        self.write(b"FIRST BYTES")   # Same size; the same stat stands for it.
        second = change_keys.key(info, self.path, keys=self.keys)
        self.assertNotEqual(second, first)
        self.assertEqual(change_keys.key(info, self.path, keys=self.keys), second)

    def test_a_rewrite_first_seen_settled_is_a_change_in_the_stat_form(self):
        with unsettled():
            first = self.key()
        self.write(b"rewritten later, longer")
        self.assertNotEqual(self.key(), first)
        self.assertNotIn(self.path, self.keys.hashed())

    def test_a_change_time_ahead_of_the_clock_counts_as_settled(self):
        with mock.patch.multiple(CLOCK, settle=mock.Mock(side_effect=CLOCK.Unsettled("ahead")),
                                 settled=mock.Mock(return_value=True)):
            self.assertNotEqual(self.key()[0], "sha256")
        self.assertEqual(self.reads, [])

    def test_a_stat_judged_settled_keeps_its_form_when_the_clock_reaches_its_times(self):
        """Break-it review, M1: a modification time set ahead of the clock is past when first seen; when the clock
        reaches it the stat is in its step, and the key went from the stat to the bytes with no write."""
        first = self.key()
        with unsettled():
            self.assertEqual(self.key(), first, "the same stat: the same form")
        self.assertEqual(self.key(), first)
        self.assertEqual(self.reads, [])

    def test_a_changed_stat_is_judged_again(self):
        first = self.key()
        self.write(b"second bytes, longer")
        with unsettled():
            second = self.key()
        self.assertNotEqual(second, first)
        self.assertEqual(second[0], "sha256", "a new stat in its step: its bytes")

    def test_a_read_error_is_the_callers(self):
        with unsettled(), mock.patch.object(change_keys, "_digest", side_effect=PermissionError("denied")):
            with self.assertRaises(OSError):
                self.key()

    def test_without_waiting_the_clock_is_only_asked(self):
        with mock.patch.object(CLOCK, "settle", side_effect=AssertionError("waited")):
            self.key(wait=False)

    def test_a_detector_keeps_digests_for_at_most_its_limit(self):
        self.keys.limit = 2
        with unsettled():
            for name in ("a", "b", "c"):
                path = str(self.folder / name)
                Path(path).write_bytes(name.encode())
                change_keys.key(os.stat(path), path, keys=self.keys)
        self.assertEqual([Path(path).name for path in self.keys.forms], ["b", "c"])

    def test_bytes_read_stop_at_the_budget(self):
        budget = [5]
        with unsettled():
            self.key(budget=budget)   # Crosses it: read whole.
            with self.assertRaises(change_keys.Deferred):
                self.key(budget=budget)
        self.assertEqual(budget, [5 - len(b"first bytes")])

    def test_a_growing_file_is_read_to_its_stat_and_charged_what_was_read(self):
        """Round 2, L-D and round 3, M1: a file still being written is longer than its stat said; it is read to its
        stat's size and one byte more (never after its writer), and charged that; one that can't be read costs
        nothing."""
        info = os.stat(self.path)
        grown = SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino, st_size=1, st_mtime_ns=info.st_mtime_ns,
                                st_ctime_ns=info.st_ctime_ns)
        budget = [100]
        with unsettled():
            grown_key = change_keys.key(grown, self.path, keys=self.keys, budget=budget)
            self.assertEqual(budget, [98], "its stat's 1 byte and 1 more")
            with self.assertRaises(OSError):
                change_keys.key(grown, self.path + ".gone", keys=self.keys, budget=budget)
        self.assertEqual(budget, [98])
        self.assertNotEqual(grown_key, change_keys.key(os.stat(self.path), self.path, keys=change_keys.ChangeKeys()))

    def test_a_file_read_past_its_stat_never_keys_as_the_longer_file(self):
        """Break-it round 4, L2: read at a stat of 10 bytes while 15 were there (the first 11 hashed), then
        rewritten to exactly those 11: the same digest, so the rewrite went unseen."""
        def witness(size):
            info = os.stat(self.path)
            return SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino, st_size=size,
                                   st_mtime_ns=info.st_mtime_ns, st_ctime_ns=info.st_ctime_ns)
        with unsettled():
            self.write(b"0123456789AJUNK")
            first = change_keys.key(witness(10), self.path, keys=self.keys)
            self.write(b"0123456789A")
            self.assertNotEqual(change_keys.key(witness(11), self.path, keys=self.keys), first)

    def test_a_form_is_kept_under_its_name(self):
        with unsettled():
            first = change_keys.key(os.stat(self.path), self.path, keys=self.keys, name="the file")
        other = os.path.join(os.path.dirname(self.path), ".", os.path.basename(self.path))   # Another spelling.
        self.assertEqual(change_keys.key(os.stat(other), other, keys=self.keys, name="the file"), first,
                         "one name, one form")

    def test_each_detector_keeps_its_own_forms(self):
        other = change_keys.ChangeKeys()
        settled_first = change_keys.key(os.stat(self.path), self.path, keys=other)
        with unsettled():
            self.key()
        self.assertEqual(change_keys.key(os.stat(self.path), self.path, keys=other), settled_first)

    def test_detector_sites_count_a_file_they_cant_read_as_missing(self):
        """Break-it review, M2: reading an unsettled file can fail (a writer that denies reading, a folder), where a
        stat-only key never read it; the World key let that escape into the export."""
        from angelo_agent import web_world_key
        with unsettled(), mock.patch.object(change_keys, "_digest", side_effect=PermissionError("denied")):
            self.assertEqual(web_world_key._file(self.path), (self.path, None))

    def test_on_a_coarse_volume_the_clocks_own_judgement_decides_each_form(self):
        """``change_clock``'s real verdict, on a volume it judges to keep 2 s steps (FAT: no change time), on Blender's
        main thread: a file first seen inside its step is keyed by its bytes and keeps them once the step passes; one
        first seen past it, by its stat. Only the clock, the volume's step and the thread are stood in for."""
        mtime = 1_800_000_000 * 10**9
        witness = SimpleNamespace(st_dev=7, st_ino=11, st_size=len(b"first bytes"), st_mtime_ns=mtime, st_ctime_ns=0)
        now = [mtime - 500_000_000]   # FAT rounds a write's time up: ahead of the clock, inside the step.

        def coarse():
            return mock.patch.multiple(CLOCK, COARSE=True, _freezes_ui=lambda: True,
                                       volume_step=lambda device: CLOCK.FILE_SYSTEM_STEPS["FAT"],
                                       system_time=lambda: now[0] // 100 + CLOCK._EPOCH_AS_FILETIME)
        with coarse():
            first = change_keys.key(witness, self.path, keys=self.keys)
            self.assertEqual(first[0], "sha256", "inside its 2 s step")
            now[0] = mtime + 3 * 10**9
            for _ in range(2):
                self.assertEqual(change_keys.key(witness, self.path, keys=self.keys), first, "the step passed")
            self.assertEqual(len(self.reads), 2)
            other = SimpleNamespace(st_dev=7, st_ino=12, st_size=1, st_mtime_ns=mtime, st_ctime_ns=0)
            self.assertEqual(change_keys.key(other, self.path + ".b", keys=self.keys)[0], 7, "first seen past it")

    def test_with_the_real_clock_a_file_written_just_now_keeps_its_key(self):
        """Windows: the write is in the clock's current tick; off Windows every stat is settled at once."""
        self.write(b"written just now")
        first = self.key()
        time.sleep(0.05)   # More than the clock's tick (16 ms).
        self.assertEqual(self.key(), first)


if __name__ == "__main__":
    unittest.main()
