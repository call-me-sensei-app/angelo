# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The change-clock settle: fake clocks on every host, the real system clock on Windows, and the source rule.

NTFS stamps change and modification times from a ticking clock, and FAT and exFAT keep modification times in coarse
steps, so a stat cannot see a same-size write in the step of the file's last stamp. Evidence:
``test_native_checkpoint_capture_publication``'s RUNTIME ack test failed about one Windows run in five (a Sculpt
component rewritten right after its seal), and ``test_native_render_device_session``'s no-GPU listing test read a
cached listing back after a same-size rewrite (two runs in six). A break-it review then found a max of two times
masking one, a change time of 0 read as settled, and keys unique only within one process. The class is fixed once,
in ``angelo_change_clock``; the rule below keeps every other use of a stat time on that helper or listed, per
function, with the reason it needs none.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS / "addons_core"))
sys.path.insert(0, str(SCRIPTS / "modules"))

import angelo_change_clock as change_clock  # noqa: E402
from angelo_agent import checkpoint_capture as capture, checkpoint_filesystem as filesystem  # noqa: E402
import file_times  # noqa: E402
import machine_clock  # noqa: E402
from host_test_policy import environment_skip  # noqa: E402

EPOCH = 116444736000000000
NOW = EPOCH + 17_000_000_000_000_123   # A FILETIME whose nanoseconds have a sub-millisecond part (a fine stamp).
SECOND = 10_000_000
TIMES = {"st_mtime_ns", "st_ctime_ns", "st_mtime", "st_ctime", "st_birthtime", "st_birthtime_ns", "st_change_time"}
# Each Angelo function that reads a stat time without the helper, and why it needs none (the audit of 2026-11-04).
NEEDS_NONE = {
    "modules/angelo_start_records.py:_examine":
        "an unlocked start record's age decides only whether a record cut short (older than INCOMPLETE_AGE, 60 s) is "
        "removed; a coarse clock delays that removal by its tick, never misreads a change",
    "addons_core/angelo_agent/blender_asset_index.py:wait_for_file":
        "polling optimization only: verified rehashes the expected SHA-256 before every success and append",
    "addons_core/angelo_agent/__init__.py:_stamp_time": "reload detection for addon_utils, compared once at enable",
    "addons_core/angelo_agent/checkpoint_capture.py:_stable_file": "the comparison of witnesses that settled first",
    "addons_core/angelo_agent/checkpoint_inventory.py:_measure": "a removal plan's fingerprint of sealed snapshots",
    "addons_core/angelo_agent/checkpoint_inventory_cache.py:_folder_state": "keeps nothing within RACY_SECONDS",
    "addons_core/angelo_agent/checkpoint_inventory_cache.py:_file_state": "keeps nothing within RACY_SECONDS",
    "addons_core/angelo_agent/checkpoint_publication.py:CheckpointPublicationMixin.finish":
        ("the ordinary BASE identity seal: a documented limit (checkpoint_policy.rst); a coarse time is never "
         "identity-sealed (publish bound its content in native)"),
    "addons_core/angelo_agent/checkpoint_publication.py:CheckpointPublicationMixin._native_binding":
        "predicts the volume's clock class from the empty BASE; it trusts no bytes, and a contradiction is refused",
    "addons_core/angelo_agent/checkpoint_publication.py:CheckpointPublicationMixin._refuse_unbound_coarse":
        "an ordinary BASE stamped coarse that was not content-bound is refused, never trusted",
    "addons_core/angelo_agent/checkpoint_retention.py:CheckpointRetentionMixin._remove":
        "removal re-admits each pair by hash (_hash_descriptor settles)",
    "addons_core/angelo_agent/checkpoint_publication.py:CheckpointPublicationMixin._publish_output":
        ("compares the hashed (settled) component with its stat after the link; for a pair, publish settles both "
         "seals at once before it returns; for an ordinary BASE it is the identity-seal limit (checkpoint_policy.rst)"),
    "addons_core/angelo_agent/checkpoint_seal.py:_signature": "the ordinary BASE identity seal: a documented limit",
    "addons_core/angelo_agent/checkpoint_seal.py:copy_sealed": "the ordinary BASE identity seal: a documented limit",
    "addons_core/angelo_agent/checkpoint_timeline.py:_file_identity": "trust needs the same identity and content",
    "addons_core/angelo_agent/codex_login.py:login_stamp": "the stamp holds the file's SHA-256: its bytes decide",
    "addons_core/angelo_agent/connections.py:ConnectionStore.signature": "every writer replaces it: a new file index",
    "addons_core/angelo_agent/crash_log.py:crash_files": "orders crash logs by age",
    "addons_core/angelo_agent/document_file_identity.py:identity_of": "a creation stamp names the file, not its bytes",
    "addons_core/angelo_agent/document_file_identity.py:_stat_like":
        "the identity the save or the load took of the file it wrote or read, as given: its callers compare or "
        "settle it",
    "addons_core/angelo_agent/files.py:file_signature": "the shape of a signature whose stat settled first",
    "addons_core/angelo_agent/files.py:FileWorkspace._execute":
        "a failed delete's diagnosis, after the before-image copy settled the file (copy_stable_file)",
    "addons_core/angelo_agent/headless_working_copy.py:sweep": "an age (a day) for leftovers",
    "addons_core/angelo_agent/job_render_child.py:sweep":
        "an age (an hour) for render copies crashed apps left; a child reads its copy once, at its start",
    "addons_core/angelo_agent/job_store.py:copy_output":
        "its copy is copy_stable_file's, which settles the opened file before the first read",
    "addons_core/angelo_agent/launcher_records.py:claim_stale":
        "a marker age only permits takeover; its bytes are reread and unknown records keep the strictest policy",
    "addons_core/angelo_agent/launcher_records.py:settled":
        "a marker age only bounds waiting; only marker absence permits the launcher read",
    "addons_core/angelo_agent/library_fetch.py:trim": "orders cached downloads by age",
    "addons_core/angelo_agent/memory_projects.py:_stamp": "the shape of the receipt's stamp, settled at admission",
    "addons_core/angelo_agent/pycache_retention.py:prune": "an age for unused bytecode folders",
    "addons_core/angelo_agent/references.py:ReferenceStore.content": "attachments are immutable: a new one, a new file",
    "addons_core/angelo_agent/script_files.py:_current": "a fresh stat compared with the key _read gated",
    "addons_core/angelo_agent/session_journal.py:discard_stale": "an age for leftover temporary files",
    "addons_core/angelo_agent/session_journal.py:_identity":
        "read settles its witness; a save's own write witness cannot order another writer (documented)",
    "addons_core/angelo_agent/session_kept.py:recovered_index": "a listed time for kept chats",
    "addons_core/angelo_agent/snapshot_ingest.py:_adopt": "the identity seal of the stat _ingest settled",
    "addons_core/angelo_agent/snapshot_ingest.py:_stable": "the comparison of the stat _ingest settled",
    "addons_core/angelo_agent/start_card.py:modified_times": "a shown modification time",
    "addons_core/angelo_agent/studio_assets.py:_file_state": "generation outputs are written once; the scan settles",
    "addons_core/angelo_agent/studio_assets.py:remove_stale_temporaries": "an age for leftover temporary files",
    "addons_core/angelo_agent/update_stage_mac.py:fingerprint": "macOS only: APFS stamps fine-grained times",
    "addons_core/angelo_agent/web_tone.py:_known":
        "the read-only installed colour configuration's digest; web_tone.py is pinned to its measured table "
        "(make_web_tone_known.py, in Blender's Python, writes both)",
    "addons_core/angelo_agent/worker_part_counts.py:_key": "holds the inode: every save of a part is a new file",
    "addons_core/angelo_agent/worker_part_counts.py:_identity": "holds the inode: every save of a part is a new file",
    "angelo/load_guard.py:check": "release tooling: a lock's age",
    "angelo/msi_commit.py:choose": "release tooling: the newest proven installer",
    "angelo/release_launch_checks.py:snapshot": "release tooling: a launch check on files written minutes before",
}


def setUpModule():
    # As the add-on's registration does: real volumes are known before a real settle, so none falls back to a time's
    # own trailing zeros (a host whose clock keeps a whole-second phase stamps 1 in 64 files on a whole second).
    change_clock.prime()
    change_clock.listed(30)


def filetime_ns(filetime):
    return (filetime - EPOCH) * 100


def stat(mtime, ctime=None, *, birth=None, change=None):
    """A stat: times in FILETIME steps; ``change`` a by-handle ChangeTime (a legacy descriptor witness)."""
    values = {"st_mtime_ns": filetime_ns(mtime), "st_ctime_ns": filetime_ns(mtime if ctime is None else ctime)}
    if birth is not None:
        values["st_birthtime_ns"] = filetime_ns(birth)
    if change is not None:
        values["st_change_time"] = change
    return types.SimpleNamespace(**values)


class Clock:
    """Returns the given readings in order, then repeats the last."""

    def __init__(self, *readings):
        self.readings, self.reads = readings, 0

    def __call__(self):
        self.reads += 1
        return self.readings[min(self.reads, len(self.readings)) - 1]


class ChangeClockTests(unittest.TestCase):
    def setUp(self):
        legacy = patch.object(change_clock, "_LEGACY", False)
        legacy.start()
        self.addCleanup(legacy.stop)
        self.sleeps = []
        sleep = patch.object(change_clock, "_sleep", side_effect=self.sleeps.append)   # Nothing really sleeps.
        sleep.start()
        self.addCleanup(sleep.stop)

    def test_times_already_past_cost_one_clock_reading_and_no_wait(self):
        witness, clock, waits = stat(NOW - 1, NOW - 1), Clock(NOW), change_clock.waits
        self.assertIs(change_clock.settle(witness, clock), witness)
        self.assertEqual((clock.reads, self.sleeps, change_clock.waits), (1, [], waits))

    def test_a_stamp_in_the_current_tick_waits_in_short_sleeps_until_the_clock_moves(self):
        witness, clock, waits = stat(NOW, NOW), Clock(NOW, NOW, NOW + 156_250), change_clock.waits
        change_clock.settle(witness, clock)
        self.assertEqual(clock.reads, 3)
        self.assertEqual(self.sleeps, [change_clock.STEP_S] * 2, "short sleeps between readings, never a spin")
        self.assertEqual(change_clock.waits, waits + 1)

    def test_a_change_time_ahead_of_the_clock_fails_closed_at_once(self):
        clock = Clock(NOW)
        with self.assertRaisesRegex(change_clock.Unsettled, "ahead of the system clock"):
            change_clock.settle(stat(NOW - SECOND, NOW + SECOND), clock)
        self.assertEqual((clock.reads, self.sleeps), (1, []), "decided at once: NTFS never stamps ahead of its clock")
        self.assertTrue(issubclass(change_clock.Unsettled, ValueError), "callers already treat it as a change")
        self.assertTrue(change_clock.settled(stat(NOW - SECOND, NOW + SECOND), Clock(NOW)),
                        "a cache keeps it: every write until the clock gets there stamps an earlier time")

    def test_the_limit_is_a_write_inside_the_stamp_s_own_tick_and_nothing_wider(self):
        # The helper's side of the documented limit (checkpoint_policy.rst): NTFS stamps a write with the clock's
        # reading, so a same-size, time-restored write inside the tick of the witness's own stamp carries that stamp
        # and no stat can tell it from no write; the helper waits exactly for that tick to pass, and for nothing wider.
        # test_native_checkpoint_publication_boundary pins where publication puts that wait.
        tick = 156_250
        stamped = NOW - NOW % tick   # The witness's change time: the clock's reading at the last write.
        witness = stat(stamped - SECOND, stamped)
        self.on_volume(0)   # NTFS (measured: stamps are the clock's reading, never ahead of it).
        self.assertEqual(change_clock.verdict(witness, stamped)[0], change_clock.WAIT, "inside the tick: the limit")
        for later in (stamped + 1, stamped + tick, stamped + 10 * SECOND):
            with self.subTest(later=later - stamped):
                self.assertTrue(change_clock.settled(witness, Clock(later)), "past it: nothing wider is waited for")
        clock = Clock(stamped, stamped + tick)
        change_clock.settle(witness, clock)
        self.assertEqual(clock.reads, 2, "a settle returns as soon as the clock leaves the stamp's tick")
        self.assertEqual(self.sleeps, [change_clock.STEP_S])

    def test_a_change_time_a_little_ahead_waits_for_the_clock(self):
        # A share whose server's coarse clock runs a few ms ahead stamps that same value again within its tick.
        ahead = NOW + 50_000   # 5 ms.
        self.assertFalse(change_clock.settled(stat(NOW - SECOND, ahead), Clock(NOW)))
        clock = Clock(NOW, ahead + 1)
        change_clock.settle(stat(NOW - SECOND, ahead), clock)
        self.assertEqual(clock.reads, 2)
        self.assertAlmostEqual(self.sleeps[0], 0.005, places=4)
        with self.assertRaises(change_clock.Unsettled):
            change_clock.settle(stat(NOW - SECOND, NOW + change_clock.TICK + 1), Clock(NOW))

    def test_a_modification_time_set_ahead_is_past_but_one_in_the_tick_is_not(self):
        self.assertTrue(change_clock.settled(stat(NOW + SECOND, NOW - 1), Clock(NOW)), "set, not stamped")
        self.assertFalse(change_clock.settled(stat(NOW, NOW - 1), Clock(NOW)), "always judged, whatever ctime says")

    def test_no_time_masks_another(self):
        # A creation time ahead (st_ctime equal to st_birthtime: a path's os.stat) must not hide a current mtime.
        masked = stat(NOW, NOW + SECOND, birth=NOW + SECOND)
        self.assertFalse(change_clock.settled(masked, Clock(NOW)))
        # A file just created with an older modification time (a copy keeping it) waits only for what it compares.
        copied = stat(NOW - SECOND, NOW, birth=NOW)
        self.assertTrue(change_clock.settled(copied, Clock(NOW), change=False), "an identity of mtime: no wait")
        self.assertFalse(change_clock.settled(copied, Clock(NOW)), "the change time is in the current tick")

    def test_a_change_time_of_zero_is_no_time(self):
        kept_none = stat(NOW - SECOND, change=0)
        self.assertEqual([entry[1] for entry in change_clock.times(kept_none)], ["loose"], "measured on FAT, exFAT")
        self.assertTrue(change_clock.settled(kept_none, Clock(NOW)))
        self.assertFalse(change_clock.settled(stat(NOW, change=0), Clock(NOW)), "its modification time still counts")

    def on_volume(self, step):   # A Windows volume of ``step`` (None: not listed yet, or a share), on any host.
        for target, value in (("COARSE", True), ("volume_step", lambda _device: step)):
            found = patch.object(change_clock, target, value)
            found.start()
            self.addCleanup(found.stop)

    def test_a_whole_second_stamp_on_ntfs_waits_only_its_tick(self):
        # SystemTime keeps a whole-second phase until w32time adjusts it (156,250 x 64 = 10**7), so NTFS stamps can
        # end in seven zeros: the step comes from the volume's file system, never from the value.
        whole = NOW - NOW % SECOND
        self.on_volume(0)
        self.assertTrue(change_clock.settled(stat(whole, whole, change=whole), Clock(whole + 156_250)))
        self.assertFalse(change_clock.settled(stat(whole, whole, change=whole), Clock(whole)))

    def test_a_fat_time_waits_until_the_clock_passes_its_rounded_up_stamp(self):
        # Measured (probe_volume_stamps.py): FAT32 and exFAT round LastWriteTime up to the next 2 s, so a write now
        # stamps a value in [now, now + 2 s] and a time the clock has passed can no longer be produced.
        fat = NOW - NOW % (2 * SECOND)
        self.on_volume(2 * SECOND)
        witness = stat(fat, change=fat)
        for now, past in ((fat - 3 * SECOND, True), (fat - SECOND, False), (fat, False), (fat + 1, True),
                          (fat + SECOND, True)):   # 3 s before: ahead of any write now (a cache keeps it).
            with self.subTest(now=now - fat):
                self.assertEqual(change_clock.settled(witness, Clock(now)), past)
        clock = Clock(fat - SECOND, fat + 1)
        with patch.object(change_clock, "_freezes_ui", return_value=False):   # A thread waits a step out.
            change_clock.settle(witness, clock)
        self.assertEqual(clock.reads, 2)
        self.assertEqual(self.sleeps, [change_clock.LIMIT_S], "one sleep toward the stamp, capped")

    def test_exfat_keeps_write_times_in_2_s_steps_as_fat_does(self):
        # Measured: exFAT's LastWriteTime has a 2 s step (its 10 ms is the creation time's, which no witness compares).
        for name, step in (("exFAT", 2 * SECOND), ("FAT32", 2 * SECOND), ("FAT", 2 * SECOND), ("NTFS", 0),
                           ("ReFS", 0)):
            with self.subTest(file_system=name):
                self.assertEqual(change_clock.file_system_step(name), step)

    def test_a_witness_on_an_open_writer_is_never_trusted_where_closing_restamps(self):
        # Measured: exFAT stamps LastWriteTime at every handle close, FAT32 at some; NTFS never.
        past = stat(NOW - 3 * SECOND, change=0)
        self.on_volume(2 * SECOND)
        with self.assertRaisesRegex(change_clock.Unsettled, "writer"):
            change_clock.settle(past, Clock(NOW), writing=True)
        with self.assertRaisesRegex(change_clock.Unsettled, "writer"):
            change_clock.settle_all([past], Clock(NOW), writing=True)
        self.assertIs(change_clock.settle(past, Clock(NOW)), past, "a reader's stat of the same file settles")
        self.on_volume(0)
        self.assertIs(change_clock.settle(past, Clock(NOW), writing=True), past, "NTFS: closing never restamps")
        self.on_volume(None)
        self.assertIs(change_clock.settle(past, Clock(NOW), writing=True), past, "unknown: a fine time is no FAT's")
        two = NOW - NOW % (2 * SECOND) - 2 * SECOND   # Unknown, and kept in 2 s steps: it may be FAT's.
        with self.assertRaisesRegex(change_clock.Unsettled, "writer"):
            change_clock.settle(stat(two, change=0), Clock(NOW), writing=True)

    def test_a_ui_never_waits_out_a_coarse_step(self):
        fat = NOW - NOW % (2 * SECOND)
        self.on_volume(2 * SECOND)
        clock = Clock(fat - SECOND)
        with patch.object(change_clock, "_freezes_ui", return_value=True), \
                self.assertRaisesRegex(change_clock.Unsettled, "read it again in about 1 s"):
            change_clock.settle(stat(fat, change=fat), clock)
        self.assertEqual((clock.reads, self.sleeps), (1, []), "refused at once, as changed, saying when to retry")
        short = NOW - NOW % 100_000   # A step shorter than a tick (10 ms) is waited out even there.
        self.on_volume(100_000)
        with patch.object(change_clock, "_freezes_ui", return_value=True):
            change_clock.settle(stat(short, change=short), Clock(short, short + 100_000))
        self.assertEqual(len(self.sleeps), 1)

    def test_only_a_windowed_blender_s_main_thread_counts_as_a_ui(self):
        self.assertFalse(change_clock._freezes_ui() if "bpy" not in sys.modules else False, "no bpy: no UI")
        for background, ui in ((False, True), (True, False)):
            fake = types.SimpleNamespace(app=types.SimpleNamespace(background=background))
            with self.subTest(background=background), patch.dict(sys.modules, {"bpy": fake}):
                self.assertEqual(change_clock._freezes_ui(), ui)
                seen = []
                worker = threading.Thread(target=lambda: seen.append(change_clock._freezes_ui()))
                worker.start()
                worker.join(5)
                self.assertEqual(seen, [False], "a thread never freezes the UI")

    def test_a_step_the_person_waits_for_is_waited_out_on_the_ui_thread(self):
        """``awaited()``: a snapshot capture or a Restore waits a coarse step out rather than refusing it (break-it,
        share follow-up H1); the block nests, ends with its ``with``, even by a raise, and is the thread's own."""
        fake = types.SimpleNamespace(app=types.SimpleNamespace(background=False))
        with patch.dict(sys.modules, {"bpy": fake}):
            with change_clock.awaited():
                with change_clock.awaited():
                    self.assertFalse(change_clock._freezes_ui())
                self.assertFalse(change_clock._freezes_ui(), "still inside the outer block")
                seen = []
                worker = threading.Thread(target=lambda: seen.append(getattr(change_clock._awaited, "depth", 0)))
                worker.start()
                worker.join(5)
                self.assertEqual(seen, [0], "another thread is not inside it")
                fat = NOW - NOW % (2 * SECOND)
                self.on_volume(2 * SECOND)
                change_clock.settle(stat(fat, change=fat), Clock(fat - SECOND, fat + 2 * SECOND + 1))
                self.assertEqual(len(self.sleeps), 1, "waited, not refused")
            self.assertTrue(change_clock._freezes_ui())
            with self.assertRaises(KeyError), change_clock.awaited():
                raise KeyError("a capture that failed")
            self.assertTrue(change_clock._freezes_ui(), "a raise ends the block too")

    def test_a_wait_the_person_waits_for_stays_bounded_and_fails_closed(self):
        """Inside ``awaited()`` a time that never leaves its step is still refused past the cap (the step and
        ``LIMIT_S``), and a change time ahead of the clock at once: waiting never means waiting forever."""
        fake = types.SimpleNamespace(app=types.SimpleNamespace(background=False))
        fat = NOW - NOW % (2 * SECOND)
        self.on_volume(2 * SECOND)
        ticks = iter(range(0, 10_000, 1))   # A monotonic clock a second on per read; the system clock stuck.
        with patch.dict(sys.modules, {"bpy": fake}), change_clock.awaited(), \
                patch.object(change_clock, "_monotonic", side_effect=lambda: float(next(ticks))):
            with self.assertRaisesRegex(change_clock.Unsettled, "past the wait's cap"):
                change_clock.settle(stat(fat, change=fat), lambda: fat - SECOND)
            self.assertLessEqual(len(self.sleeps), int(change_clock.LIMIT_S + 2) + 1, "bounded by the cap")
            with self.assertRaisesRegex(change_clock.Unsettled, "ahead of the system clock"):
                change_clock.settle(stat(fat, change=fat + 10 * SECOND), lambda: fat - SECOND)

    def test_a_thread_that_cannot_start_leaves_lookups_answering(self):
        listings = self.volumes({0x1234: 0})

        class Refused(threading.Thread):
            def start(self):
                raise RuntimeError("can't start new thread")
        with patch.object(change_clock.threading, "Thread", Refused):
            self.assertIsNone(change_clock.volume_step(0x1234), "the fallback answers; nothing raised")
            self.assertIsNone(change_clock._listing, "nothing recorded for a listing that never started")
            self.assertTrue(change_clock.listed(1))
        self.assertEqual(listings, [])
        self.assertIsNone(change_clock.volume_step(0x1234))   # The next sight starts one.
        self.assertTrue(change_clock.listed(5))
        self.assertEqual(change_clock.volume_step(0x1234), 0)

    def test_a_query_that_cannot_start_is_asked_again(self):
        class Refused(threading.Thread):
            def start(self):
                raise RuntimeError("can't start new thread")
        with patch.object(change_clock, "_answers", {}), \
                patch.object(change_clock, "_volume_names", return_value=["v"]), \
                patch.object(change_clock, "_volume_information", return_value=(0x9, 0)):
            with patch.object(change_clock.threading, "Thread", Refused):
                self.assertEqual(change_clock._volume_steps(), {})
            self.assertNotIn("v", change_clock._slow)
            self.assertEqual(change_clock._volume_steps(), {0x9: 0})

    def test_volumes_are_asked_together_and_only_when_new(self):
        asked, release = [], threading.Event()
        self.addCleanup(release.set)

        def information(name):
            asked.append(name)
            if name.startswith("slow"):
                release.wait(10)
            return (len(name), 0)
        with patch.object(change_clock, "_answers", {}), \
                patch.object(change_clock, "_volume_names", return_value=["slow1", "slow22", "slow333", "fast"]), \
                patch.object(change_clock, "_volume_information", side_effect=information), \
                patch.object(change_clock, "VOLUME_TIMEOUT_S", 0.2):
            window = machine_clock.start(self)
            self.assertEqual(change_clock._volume_steps(), {4: 0})
            window.assert_within(0.5, "three slow volumes share one timeout (0.2 s), not three")
            release.set()
            for _ in range(100):   # The dropped queries end now; their volumes may be asked again.
                if not change_clock._slow:
                    break
                time.sleep(0.01)
            asked.clear()
            change_clock._volume_steps()
            self.assertEqual(sorted(asked), ["slow1", "slow22", "slow333"], "only the unanswered are asked again")
            asked.clear()
            change_clock._volume_steps()
            self.assertEqual(asked, [], "the same volumes, all answered: nothing is asked")

    def test_prime_lists_once_per_process(self):
        requests = []
        with patch.object(change_clock, "COARSE", True), \
                patch.object(change_clock, "_request", side_effect=requests.append):
            with patch.object(change_clock, "_listed_once", False):
                change_clock.prime()
            with patch.object(change_clock, "_listed_once", True):
                change_clock.prime()
        self.assertEqual(requests, [None])

    def test_an_unknown_device_or_a_share_judges_each_time_by_its_own_value(self):
        whole = NOW - NOW % SECOND + SECOND   # An odd second's trailing zeros: it may have been kept in 1 s steps.
        self.on_volume(None)
        self.assertEqual(change_clock.times(stat(whole, NOW - SECOND), change=False)[0][2], SECOND)
        self.assertFalse(change_clock.settled(stat(whole, NOW - SECOND), Clock(whole + SECOND // 2), change=False))
        self.assertTrue(change_clock.settled(stat(NOW, NOW - SECOND), Clock(NOW + 156_250), change=False),
                        "a sub-millisecond time: no step")

    def test_a_change_time_rounded_up_to_its_step_waits_and_fails_only_beyond(self):
        # A change time on a 2 s volume rounded up (as FAT32 and exFAT round LastWriteTime, measured) waits; one
        # beyond the step means the clock went back.
        self.on_volume(2 * SECOND)
        rounded = NOW + 15_000_000   # 1.5 s ahead.
        self.assertFalse(change_clock.settled(stat(NOW - 3 * SECOND, change=rounded), Clock(NOW)))
        self.assertEqual(change_clock.verdict(stat(NOW - 3 * SECOND, change=rounded), NOW)[0], change_clock.WAIT)
        self.assertEqual(change_clock.verdict(stat(NOW - 3 * SECOND, change=NOW + 3 * SECOND), NOW)[0],
                         change_clock.AHEAD)

    def test_the_capture_seal_asks_the_volume(self):
        from angelo_agent import checkpoint_seal
        whole = 1_700_000_000_000_000_000
        self.on_volume(0)
        self.assertFalse(checkpoint_seal.coarse_timestamps(whole, 7), "NTFS: a whole second is no coarse step")
        self.on_volume(2 * SECOND)
        self.assertTrue(checkpoint_seal.coarse_timestamps(whole + 123_456, 7))
        self.on_volume(None)   # A share, or a volume not listed yet: the value's sub-millisecond part decides.
        self.assertTrue(checkpoint_seal.coarse_timestamps(whole, 7))
        self.assertFalse(checkpoint_seal.coarse_timestamps(whole + 123_456, 7))

    def volumes(self, steps=None, *, steps_of=None):
        """A fresh, empty volume map and a stand-in listing; the listings it made."""
        listings = []

        def listing():
            listings.append(1)
            return steps_of() if steps_of else dict(steps)
        change_clock.listed(10)   # A real listing (the module's own priming) is over before the stand-in takes over.
        for target, value in (("COARSE", True), ("_volumes", {}), ("_volume_steps", listing)):
            patcher = patch.object(change_clock, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(change_clock.listed, 5)
        return listings

    def test_volumes_are_listed_on_a_thread_once_per_new_device(self):
        listings = self.volumes({0x1234: 2 * SECOND, 0x5678: 0})
        self.assertIsNone(change_clock.volume_step(0xABCD_0000_1234), "not known yet: the fallback, no wait")
        self.assertTrue(change_clock.listed(5))
        self.assertEqual(change_clock.volume_step(0xABCD_0000_1234), 2 * SECOND, "the serial's low 32 bits")
        self.assertEqual(change_clock.volume_step(0x5678), 0)
        self.assertEqual(len(listings), 1)
        self.assertIsNone(change_clock.volume_step(0x9999))
        self.assertTrue(change_clock.listed(5))
        self.assertIsNone(change_clock.volume_step(0x9999), "no local volume: a share, kept as such")
        self.assertEqual(len(listings), 2, "one listing for the one new device")
        with patch.object(change_clock, "COARSE", False):
            self.assertIsNone(change_clock.volume_step(0x1234))

    def test_a_known_device_is_answered_without_the_lock(self):
        self.volumes({0x1234: 0})
        change_clock.volume_step(0x1234)
        self.assertTrue(change_clock.listed(5))

        class Refused:
            def __enter__(self):
                raise AssertionError("the listing lock was taken for a known device")

            def __exit__(self, *_error):
                return False
        with patch.object(change_clock, "_listing_lock", Refused()):
            self.assertEqual(change_clock.volume_step(0x1234), 0)

    def test_a_device_no_volume_has_is_looked_for_again_after_a_while(self):
        found = {}
        listings = self.volumes(steps_of=lambda: dict(found))
        self.assertIsNone(change_clock.volume_step(0x4242))
        self.assertTrue(change_clock.listed(5))
        found[0x4242] = 2 * SECOND   # A stick plugged in later, or a volume that answered slowly before.
        self.assertIsNone(change_clock.volume_step(0x4242), "within RECHECK_S: the answer stands")
        self.assertEqual(len(listings), 1)
        with patch.object(change_clock, "RECHECK_S", -1.0):
            self.assertIsNone(change_clock.volume_step(0x4242), "never waits for the new listing")
            self.assertTrue(change_clock.listed(5))
        self.assertEqual(change_clock.volume_step(0x4242), 2 * SECOND)
        self.assertEqual(len(listings), 2)

    def test_no_settle_waits_for_a_listing(self):
        release = threading.Event()
        self.volumes(steps_of=lambda: (release.wait(10), {})[1])
        self.addCleanup(release.set)
        witness = stat(NOW - SECOND, NOW - SECOND)
        witness.st_dev = 0x7777
        clock = Clock(NOW)
        change_clock.settle(witness, clock)   # The listing blocks on its own thread; the fallback answers here.
        self.assertEqual((clock.reads, self.sleeps), (1, []))

    def test_a_listing_drops_a_slow_volume_and_keeps_the_largest_step_of_a_shared_serial(self):
        answers = {"slow": (0x1, 0), "fat": (0x2, 2 * SECOND), "ntfs-twin": (0x2, 0)}
        release = threading.Event()
        self.addCleanup(release.set)

        def information(name):
            if name == "slow":
                release.wait(10)
            return answers[name]
        with patch.object(change_clock, "_answers", {}), \
                patch.object(change_clock, "_volume_names", return_value=["slow", "fat", "ntfs-twin"]), \
                patch.object(change_clock, "_volume_information", side_effect=information), \
                patch.object(change_clock, "VOLUME_TIMEOUT_S", 0.05):
            found = change_clock._volume_steps()
            self.assertEqual(found, {0x2: 2 * SECOND}, "the slow volume left out; a wrong 0 never wins")
            self.assertEqual(change_clock._volume_steps(), {0x2: 2 * SECOND}, "not asked again while it hangs")
        release.set()

    def test_now_is_read_on_the_clock_files_are_stamped_with(self):
        with patch.object(change_clock, "COARSE", True), patch.object(change_clock, "system_time", return_value=NOW):
            self.assertEqual(change_clock.now_s(), (NOW - EPOCH) / 1e7)
        with patch.object(change_clock, "COARSE", False), patch.object(change_clock.time, "time", return_value=5.0):
            self.assertEqual(change_clock.now_s(), 5.0)

    def test_a_time_that_stays_in_its_step_past_the_cap_fails_closed(self):
        clocks = iter(range(0, 10**6, 10))
        with patch.object(change_clock, "LIMIT_S", 0.0), \
                patch.object(change_clock, "_monotonic", side_effect=lambda: next(clocks)), \
                self.assertRaisesRegex(change_clock.Unsettled, "past the wait's cap"):
            change_clock.settle(stat(NOW, NOW), Clock(NOW))

    def test_settle_all_waits_once_for_every_witness(self):
        waits, clock = change_clock.waits, Clock(NOW, NOW + 156_250)
        change_clock.settle_all([stat(NOW, NOW), stat(NOW - 1, NOW), stat(NOW - 5, NOW - 5)], clock)
        self.assertEqual((clock.reads, change_clock.waits), (2, waits + 1))

    def test_a_fine_grained_volume_reads_no_clock(self):
        # APFS (test_native_change_clock_volumes judges it); before the judge, any host off Windows.
        witness = stat(NOW, NOW + SECOND * 100)
        with patch.object(change_clock, "COARSE", False), \
                patch.object(change_clock._posix, "volume", return_value=change_clock._posix.FINE), \
                patch.object(change_clock, "system_time", side_effect=AssertionError("clock read")):
            self.assertIs(change_clock.settle(witness), witness)
            change_clock.settle_all([witness])
            self.assertTrue(change_clock.settled(witness))
            self.assertEqual(change_clock.cache_key(witness, ("key",)), ("key",))
            self.assertEqual(change_clock.settled_key(witness, ("key",)), ("key",))

    def test_a_cache_never_keeps_a_key_from_the_current_tick(self):
        with patch.object(change_clock, "COARSE", True), patch.object(change_clock, "system_time", return_value=NOW):
            self.assertEqual(change_clock.cache_key(stat(NOW - 1, NOW), ("key",)), ("key",), "keys the mtime only")
            first, second = (change_clock.cache_key(stat(NOW, NOW), ("key",)) for _ in range(2))
            self.assertNotEqual(first, ("key",))
            self.assertNotEqual(first, second, "no two unsettled reads share a key either")

    def test_a_kept_key_waits_and_one_nothing_vouches_for_is_unique(self):
        with patch.object(change_clock, "COARSE", True), \
                patch.object(change_clock, "system_time", side_effect=[NOW, NOW + 156_250]):
            self.assertEqual(change_clock.settled_key(stat(NOW, NOW), ("key",)), ("key",))
        with patch.object(change_clock, "settle", side_effect=change_clock.Unsettled("ahead")):
            self.assertNotEqual(change_clock.settled_key(stat(NOW, NOW), ("key",)), ("key",))

    def test_unique_keys_differ_across_processes(self):
        code = "import sys; sys.path.insert(0, %r); import angelo_change_clock as c; print(c.unique()[1])" % (
            str(SCRIPTS / "modules"))
        seen = [subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip()
                for _ in range(2)] + [change_clock.unique()[1]]
        self.assertEqual(len(set(seen)), 3, "a fresh process never repeats another's key")
        self.assertTrue(all(len(value) == 32 for value in seen))

    def test_settle_keeps_its_own_sleep(self):
        with patch.object(change_clock.time, "sleep", side_effect=AssertionError("the global sleep")), \
                patch.object(change_clock.time, "monotonic", side_effect=AssertionError("the global monotonic")):
            change_clock.settle(stat(NOW, NOW), Clock(NOW, NOW + 156_250))
        self.assertEqual(self.sleeps, [change_clock.STEP_S])

    def test_the_agent_reaches_the_same_module(self):
        from angelo_agent import change_clock as agent
        self.assertIs(agent, change_clock)


@unittest.skipUnless(os.name == "nt", environment_skip(
    "Windows' own volumes stamp from a ticking clock; macOS's coarse ones: test_native_change_clock_mac_volumes"))
class RealClockTests(unittest.TestCase):
    def test_a_rewrite_after_settling_always_changes_the_witness(self):
        # In the tick of a file's last stamp, a same-size rewrite that restores its times keeps every stat field;
        # after settling it never does.
        with tempfile.TemporaryDirectory(prefix="angelo-change-clock-") as temp:
            fd = os.open(Path(temp) / "part", os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0))
            try:
                for attempt in range(20):
                    with self.subTest(attempt=attempt):
                        os.lseek(fd, 0, os.SEEK_SET)
                        os.write(fd, b"before")
                        before = change_clock.settle(filesystem.descriptor_witness(fd))
                        os.lseek(fd, 0, os.SEEK_SET)
                        os.write(fd, b"after!")
                        file_times.set_times(fd, ns=(before.st_atime_ns, before.st_mtime_ns))
                        after = filesystem.descriptor_witness(fd)
                        self.assertEqual((before.st_size, before.st_mtime_ns), (after.st_size, after.st_mtime_ns))
                        self.assertFalse(capture._stable_file(before, after))
            finally:
                os.close(fd)

    def test_a_settled_path_stat_always_differs_after_a_same_size_write(self):
        # The render-device listing's case: a path's os.stat, rewritten at once.
        with tempfile.TemporaryDirectory(prefix="angelo-change-clock-") as temp:
            path = Path(temp) / "listing.json"
            for attempt in range(20):
                with self.subTest(attempt=attempt):
                    path.write_bytes(b'{"at": 1}')
                    before = change_clock.settle(os.stat(path), change=False)   # What a cache may keep.
                    path.write_bytes(b'{"at": 2}')
                    after = os.stat(path)
                    self.assertNotEqual((before.st_size, before.st_mtime_ns), (after.st_size, after.st_mtime_ns))


def owned_sources():
    """Angelo's own Python files (its copyright line), wherever they sit under scripts/: Angelo's parts of another
    add-on (io_scene_gltf2/angelo_physics) too, so no folder list can leave a new one out."""
    for path in sorted(SCRIPTS.rglob("*.py")):
        if "__pycache__" not in path.parts and "Hyperbond" in path.read_text(encoding="utf-8", errors="replace")[:400]:
            yield path


STAT_CALLS = ("stat", "lstat", "fstat", "stat_of", "_info", "witness")   # What returns a stat (entry_info, ...).
INDEXES = {8: "st_mtime", 9: "st_ctime"}
NESTED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def own_nodes(function):
    """The nodes of a function's (class's, module's) own body: never a nested function's or class's (a lambda's
    are its own)."""
    pending = list(ast.iter_child_nodes(function))
    while pending:
        node = pending.pop()
        yield node
        if not isinstance(node, NESTED):
            pending.extend(ast.iter_child_nodes(node))


def helpers_of(tree):
    """The names that reach the helper: modules (``change_clock``, ``angelo_change_clock`` and any alias of them),
    functions imported by name (``from angelo_change_clock import settle``), and the names of ``change_keys``, whose
    ``key`` alone counts (``(modules, functions, keyed, keyed_functions)``: ``keyed_functions`` its names imported by
    name)."""
    modules, functions, keyed, keyed_functions = {"change_clock", "angelo_change_clock"}, set(), {"change_keys"}, set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.asname or alias.name for alias in node.names if alias.name.endswith("change_clock")}
            keyed |= {alias.asname or alias.name for alias in node.names
                      if alias.name.rsplit(".", 1)[-1] == "change_keys"}
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").endswith("change_clock"):
                functions |= {alias.asname or alias.name for alias in node.names}
            elif (node.module or "").rsplit(".", 1)[-1] == "change_keys":
                keyed_functions |= {alias.asname or alias.name for alias in node.names if alias.name == "key"}
            else:
                modules |= {alias.asname or alias.name for alias in node.names if alias.name.endswith("change_clock")}
                keyed |= {alias.asname or alias.name for alias in node.names if alias.name == "change_keys"}
    return modules, functions, keyed, keyed_functions


def is_helper(call, helpers):
    modules, functions, keyed, keyed_functions = helpers
    function = call.func
    if isinstance(function, ast.Name):
        return KEYED if function.id in keyed_functions else function.id in functions
    if not isinstance(function, ast.Attribute):
        return False
    value = ast.unparse(function.value)
    if value in keyed:   # Only the module's own names: ``self.change_keys.key`` is any object's method.
        return KEYED if function.attr == "key" else False   # ``ChangeKeys``, ``Deferred`` settle nothing.
    return value in modules or value.endswith(".change_clock")


KEYED = "keyed"   # ``is_helper`` of a ``change_keys.key`` call: it covers only its own witness's stat key.
SETTLES = {"settle", "settle_all", "settled_key"}   # The helpers that settle a witness (not a query of one).


def key_covered(call):
    """The ids of the nodes of a ``change_keys.key`` call's stat key (its third argument or ``stat_key``) whose time
    reads it covers: never one under a nested call, a lambda or an assignment expression, whose value goes on."""
    found = list(call.args[2:3]) + [keyword.value for keyword in call.keywords if keyword.arg == "stat_key"]
    covered, pending = set(), found
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.Call, ast.Lambda, ast.NamedExpr)):
            continue
        covered.add(id(node))
        pending.extend(ast.iter_child_nodes(node))
    return covered


def settling(call, helpers):
    """Whether a helper call settles its witness for good: ``settle`` and kin (never ``change_keys.key``)."""
    function = call.func
    name = function.attr if isinstance(function, ast.Attribute) else getattr(function, "id", None)
    return is_helper(call, helpers) is True and name in SETTLES


def time_read(node, stats):
    """The witness a stat-time read reads (its expression), or None for no read: an attribute, a field name anywhere
    (``fields = ("st_mtime_ns", ...)``, ``getattr``: ``<field names>``), ``getmtime``/``getctime`` by attribute or by
    name, ``stat.ST_MTIME``/``ST_CTIME``, or a stat indexed at 8 or 9 (a stat call, or a name ``stats`` holds)."""
    if isinstance(node, ast.Attribute):
        if node.attr in TIMES:
            return ast.unparse(node.value)
        return "<stat index>" if node.attr in {"ST_MTIME", "ST_CTIME"} else None
    if isinstance(node, ast.Name) and node.id in {"ST_MTIME", "ST_CTIME"}:
        return "<stat index>"
    if isinstance(node, ast.Constant):
        return "<field names>" if node.value in TIMES else None
    if isinstance(node, ast.Call):
        function = node.func
        if ((isinstance(function, ast.Attribute) and function.attr in {"getmtime", "getctime"})
                or (isinstance(function, ast.Name) and function.id in {"getmtime", "getctime"})):
            return "<getmtime>"
        return None
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and node.slice.value in INDEXES:
        value = node.value
        if ((isinstance(value, ast.Call) and ast.unparse(value.func).endswith(STAT_CALLS))
                or (isinstance(value, ast.Name) and value.id in stats)):
            return ast.unparse(value)
    return None


def stat_time_uses(source):
    """``{function: (line, settled, read)}`` for every function reading a stat time (``<module>`` outside any):
    ``settled`` what it passes to a ``change_clock`` helper, with the names a helper's result is assigned to (sorted;
    empty: it calls none); ``read`` the witnesses its own body reads times of (sorted). A nested function counts on
    its own, never for the function around it. ``change_keys.key`` settles nothing it returns (a key in its bytes'
    form leaves the witness unsettled): a witness passed to it counts as settled only while every time read of it is
    inside that call's own arguments (its stat key)."""
    tree = ast.parse(source)
    helpers, found = helpers_of(tree), {}

    def judge(node, name):
        nodes = list(own_nodes(node))
        calls = [(child, is_helper(child, helpers)) for child in nodes if isinstance(child, ast.Call)]
        calls = [(call, kind) for call, kind in calls if kind]
        settled, stats = {ast.unparse(call.args[0]) for call, _kind in calls if call.args}, set()
        keyed = {ast.unparse(call.args[0]) for call, kind in calls if kind == KEYED and call.args}
        keyed -= {ast.unparse(call.args[0]) for call, _kind in calls if call.args and settling(call, helpers)}
        inside = {}   # Witness -> the nodes of its own key calls' stat keys.
        for call, kind in calls:
            if kind == KEYED and call.args:
                inside.setdefault(ast.unparse(call.args[0]), set()).update(key_covered(call))
        for child in nodes:
            if isinstance(child, ast.Assign) and isinstance(child.value, ast.Call):
                names = {target.id for target in child.targets if isinstance(target, ast.Name)}
                if is_helper(child.value, helpers) is True:
                    settled |= names
                if ast.unparse(child.value.func).endswith(STAT_CALLS):
                    stats |= names
        read, outside, line = set(), set(), None
        for child in nodes:
            witness = time_read(child, stats)
            if witness is not None:
                read.add(witness)
                if id(child) not in inside.get(witness, ()):
                    outside.add(witness)
                line = child.lineno if line is None else min(line, child.lineno)
        settled -= keyed & outside   # Read again beyond the key's own stat key: nothing settled it.
        if read:
            found[name] = (line, sorted(settled), sorted(read))

    def visit(node, scope):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                inner = scope + "." + child.name if scope else child.name
                judge(child, inner)
                visit(child, inner)
            else:
                visit(child, scope)
    judge(tree, "<module>")
    visit(tree, "")
    return found


def unsettled_uses(source):
    return {function: line for function, (line, settled, _read) in stat_time_uses(source).items() if not settled}


# Each function that reads a stat time and calls the helper: the witness it settles (what it passes), and each other
# witness whose times it reads, with why that one needs no settle of its own (a comparison with the settled one
# after the read, the settled set's members). Every witness a function reads is the settled one or listed here, so a
# helper call on one stat never exempts another.
SETTLED_IN = {
    "addons_core/angelo_agent/chat_destinations.py:_app_states": ("stat", {}),
    "addons_core/angelo_agent/conversation_search.py:SearchIndex._signature": ("status", {}),
    "addons_core/angelo_agent/blend_file_listing.py:read":
        ("opened", {"<field names>": "held and after, compared with opened, the settled witness, after the scan"}),
    "addons_core/angelo_agent/change_keys.py:key": ("witness", {}),
    "addons_core/angelo_agent/checkpoint_filesystem.py:copy_stable_file":
        ("opened", {"<field names>": "finished, compared with opened, the settled witness, after the copy"}),
    "addons_core/angelo_agent/engine_setup.py:scan": ("info", {}),
    "addons_core/angelo_agent/engine_setup.py:_stamp": ("found", {"info": "each stat of found, settled together"}),
    "addons_core/angelo_agent/export_baked_images.py:_cached_file": ("status", {}),
    "addons_core/angelo_agent/export_baked_images.py:_file": ("status", {}),
    "addons_core/angelo_agent/export_baked_reads.py:_file": ("status", {}),
    "addons_core/angelo_agent/export_snapshot.py:settled_identity": ("info", {}),
    "addons_core/angelo_agent/external_host.py:ExternalHost.reload_apps": ("info", {}),
    "addons_core/angelo_agent/files.py:FileWorkspace.precondition": ("info", {}),
    "addons_core/angelo_agent/headless_files.py:settled_identity": ("info", {}),
    "addons_core/angelo_agent/team_library_save.py:_image":
        ("opened", {"finished": "the same handle's stat after the copy, compared with opened, the settled witness"}),
    "addons_core/angelo_agent/engine_link_writeback.py:_expected": ("saved", {}),
    "addons_core/angelo_agent/job_ui.py:resume_state": ("info", {}),
    "addons_core/angelo_agent/media_shelf.py:studio_catalogs": ("info", {}),
    "addons_core/angelo_agent/memory_host.py:_fingerprint": ("stat", {}),
    "addons_core/angelo_agent/private_roots.py:_registry_signature": ("info", {}),
    "addons_core/angelo_agent/private_roots.py:_retained_now": ("info", {}),
    "addons_core/angelo_agent/script_files.py:_read": ("info", {}),
    "addons_core/angelo_agent/snapshot_git.py:_hash_file":
        ("before", {"after": "compared with before, the settled witness, after the read"}),
    "addons_core/angelo_agent/snapshot_git.py:project_commit_time": ("info", {}),
    "addons_core/angelo_agent/studio_agent.py:fingerprint": ("path.stat()", {}),
    "addons_core/angelo_agent/studio_agent.py:StudioAgent._export":
        ("opened", {"finished": "compared with opened, the settled witness, after the copy"}),
    "addons_core/angelo_agent/studio_assets_host.py:_signature.stamp": ("info", {}),
    "addons_core/angelo_agent/team_library_save.py:check_source": ("path.stat()", {}),
    "addons_core/angelo_agent/web_shader.py:signature": ("info", {}),
    "addons_core/angelo_agent/web_world_key.py:_file": ("stat", {}),
    "addons_core/angelo_agent/worker_merge.py:precondition": ("os.stat(path)", {}),
    "modules/angelo_render_device/device.py:_read": ("found", {}),
}


class SourceRuleTests(unittest.TestCase):
    def uses(self):
        for path in owned_sources():
            if path.name != "angelo_change_clock.py":
                relative = path.relative_to(SCRIPTS).as_posix()
                for function, (line, settled, read) in stat_time_uses(path.read_text(encoding="utf-8")).items():
                    yield relative + ":" + function, line, settled, read

    def test_every_stat_time_use_settles_or_says_why_not(self):
        unlisted = ["%s (line %d)" % (name, line) for name, line, settled, _read in self.uses()
                    if (name not in SETTLED_IN if settled else name not in NEEDS_NONE)]
        self.assertEqual(unlisted, [], "settle a witness or gate a cache with change_clock, and list the function: "
                                       "SETTLED_IN with the witness it settles, or NEEDS_NONE with the reason")

    def test_each_listed_function_settles_every_witness_it_reads(self):
        found = {name: (settled, read) for name, _line, settled, read in self.uses()}
        for name, (witness, others) in SETTLED_IN.items():
            with self.subTest(function=name):
                settled, read = found.get(name, ([], []))
                self.assertIn(witness, settled, "passes that witness to a change_clock helper")
                self.assertEqual(sorted(set(read) - set(settled)), sorted(others),
                                 "every other witness it reads has its reason, and every reason a witness")
        for name in NEEDS_NONE:
            with self.subTest(function=name):
                self.assertEqual(found.get(name, (None,))[0], [], "still reads a stat time, and calls no helper")

    def test_the_rule_catches_each_kind_of_read(self):
        for source in ("def f(p):\n    return os.stat(p).st_mtime_ns\n",
                       "def f(p):\n    return os.path.getctime(p)\n",
                       "def f(p):\n    return getmtime(p)\n",
                       "def f(s):\n    return getattr(s, 'st_birthtime_ns', 0)\n",
                       "def f(e):\n    return e.stat().st_mtime\n",
                       "x = os.stat('a').st_ctime\n",
                       "def f(a):\n    names = ('st_size', 'st_mtime_ns')\n    return [getattr(a, n) for n in names]\n",
                       "def f(p):\n    return os.stat(p)[8]\n",
                       "def f(p):\n    return os.stat(p)[stat.ST_MTIME]\n",
                       "def f(p):\n    s = os.stat(p)\n    return s[9]\n",
                       "def f(p):\n    def g(q):\n        return change_clock.settle(q)\n"
                       "    return os.stat(p).st_mtime_ns\n"):
            with self.subTest(source=source):
                self.assertTrue(unsettled_uses(source), "a nested function's helper call never counts for f")
        two = stat_time_uses("def f(p, q):\n    a = change_clock.settle(os.stat(p))\n    b = os.stat(q)\n"
                             "    return a.st_mtime_ns == b.st_mtime_ns\n")
        self.assertEqual(two["f"][1:], (["a", "os.stat(p)"], ["a", "b"]), "b is read and not settled")
        for source in ("from . import change_keys\ndef f(p):\n    info = os.stat(p)\n"
                       "    change_keys.ChangeKeys(info)\n    return info.st_mtime_ns\n",
                       "import exchange_keys\ndef f(p):\n    info = os.stat(p)\n"
                       "    exchange_keys.key(info)\n    return info.st_mtime_ns\n"):
            with self.subTest(source=source):
                self.assertTrue(unsettled_uses(source), "only change_keys.key settles, and only change_keys'")
        keyed = stat_time_uses("from . import change_keys\ndef f(p):\n    info = os.stat(p)\n"
                               "    return change_keys.key(info, p, (info.st_mtime_ns,), keys=K)\n")
        self.assertEqual(keyed["f"][1], ["info"], "change_keys.key covers the times of its own stat key")
        later = stat_time_uses("from . import change_keys\ndef f(p):\n    info = os.stat(p)\n"
                               "    k = change_keys.key(info, p, keys=K)\n    SEEN[p] = info.st_mtime_ns\n"
                               "    return k\n")
        self.assertEqual(later["f"][1], [], "a time read beyond the key call: nothing settled it (round 2, M-A)")
        named = stat_time_uses("from .change_keys import key\ndef f(p):\n    info = os.stat(p)\n"
                               "    k = key(info, p, keys=K)\n    SEEN[p] = info.st_mtime_ns\n    return k\n")
        self.assertEqual(named["f"][1], [], "imported by name, the same")
        for source in ("    ka = change_keys.key(a, p, (a.st_size,), keys=K)\n"
                       "    kb = change_keys.key(b, q, (b.st_size, a.st_mtime_ns), keys=K)\n",
                       "    k = change_keys.key(a, p, (m := a.st_mtime_ns,), keys=K)\n    SEEN[p] = m\n",
                       "    k = change_keys.key(a, remember(p, a.st_mtime_ns), keys=K)\n",
                       "    k = change_keys.key(a, p, keys=K)\n    if change_clock.settled(a):\n"
                       "        SEEN[p] = a.st_mtime_ns\n",
                       "    k = self.change_keys.key(a, p, (a.st_mtime_ns,))\n"):
            with self.subTest(source=source):   # Break-it round 3, L1 to L3.
                uses = stat_time_uses("from . import change_keys, change_clock\ndef f(p, q):\n"
                                      "    a, b = os.stat(p), os.stat(q)\n" + source + "    return 1\n")
                self.assertNotIn("a", uses["f"][1], "a's time is read where no key call covers it")
        imported = stat_time_uses("from angelo_change_clock import settle as hold\n"
                                  "def f(p):\n    info = hold(os.stat(p))\n    return info.st_mtime_ns\n")
        self.assertEqual(imported["f"][1:], (["info", "os.stat(p)"], ["info"]), "a helper imported by name counts")


if __name__ == "__main__":
    unittest.main()
