# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The change clock judges each volume off Windows too: stand-in mount tables on every host.

Evidence (audit 1.0.10, re-check 3): ``COARSE = os.name == "nt"`` made every settle return at once on macOS, so on
HFS+ (1 s), FAT (2 s), exFAT and network shares a same-size rewrite in the second of a file's last stamp kept
(inode, size, mtime, ctime) and read as unchanged (measured on Mac 2: 30 of 30 rewrites on HFS+ and FAT, 18 of 30 on
exFAT, none on APFS). ``test_native_change_clock_mac_volumes`` proves it on real disk images; this pins the judge,
its cache and what each answer makes a settle do, on any host.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

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
sys.path.insert(0, str(ROOT / "scripts" / "addons_core"))
sys.path.insert(0, str(ROOT / "scripts" / "modules"))

import angelo_change_clock as change_clock  # noqa: E402
import angelo_change_clock_volumes as volumes  # noqa: E402
from host_test_policy import environment_skip  # noqa: E402

EPOCH = 116444736000000000
SECOND = 10_000_000
WHOLE_NS = 1_780_000_001 * 10**9          # An odd whole second, in ns: an HFS+ stamp.
FINE_NS = WHOLE_NS + 123_456_789          # A nanosecond stamp: APFS's.
HFS = volumes.MAC_TYPES["hfs"]


def filetime(time_ns):
    return time_ns // 100 + EPOCH


def stat(device, mtime_ns, ctime_ns=None):
    return types.SimpleNamespace(st_dev=device, st_mtime_ns=mtime_ns,
                                 st_ctime_ns=mtime_ns if ctime_ns is None else ctime_ns)


class Clock:
    def __init__(self, *readings):
        self.readings, self.reads = readings, 0

    def __call__(self):
        self.reads += 1
        return self.readings[min(self.reads, len(self.readings)) - 1]


class StandIn(unittest.TestCase):
    """A fresh cache on a stand-in host whose mount table the test writes; ``self.listed`` counts its listings,
    ``self.mount()`` stands for a volume mounted or unmounted (``counted``: the watch counted it, else not yet), and
    ``self.unwatched()`` for no watch. A real watch's thread (another test's, or a real lookup's) counts nothing
    here: ``_bump`` is stood in too."""

    platform = "darwin"

    def setUp(self):
        self.table, self.listed = [], 0

        def table():
            self.listed += 1
            if isinstance(self.table, Exception):
                raise self.table
            return list(self.table)
        def arm():
            watched = volumes._watched
            return volumes._generation if watched is not None and watched[0] == os.getpid() else None
        for target, value in (("PLATFORM", self.platform), ("_known", {}), ("_mac_mounts", table),
                              ("_linux_mounts", table), ("_arm", arm), ("_generation", 0), ("_bump", lambda: None),
                              ("_begin", lambda *_watch: None), ("_end", lambda *_watch: None),
                              ("_watched", (os.getpid(), "stand-in"))):
            patcher = patch.object(volumes, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        coarse = patch.object(change_clock, "COARSE", False)
        coarse.start()
        self.addCleanup(coarse.stop)
        self.sleeps = []
        sleep = patch.object(change_clock, "_sleep", side_effect=self.sleeps.append)
        sleep.start()
        self.addCleanup(sleep.stop)

    def mount(self, table, counted=True):
        self.table = table
        if counted:
            volumes._generation += 1

    def unwatched(self):
        volumes._watched = None


class MacJudgeTests(StandIn):
    def test_each_file_system_is_judged_by_its_type_and_unknown_ones_as_the_coarsest(self):
        expected = {("apfs", True): volumes.FINE, ("apfs", False): volumes.UNKNOWN,
                    ("hfs", True): volumes.Volume(SECOND, SECOND, False, False, True),
                    ("msdos", True): volumes.Volume(2 * SECOND, 2 * SECOND, False, False, False),
                    ("exfat", True): volumes.Volume(100_000, 100_000, False, False, False)}
        for share in ("smbfs", "nfs", "afpfs", "webdav", "macfuse", "lifs", "ntfs", "cd9660", "something-new"):
            expected[(share, False)] = expected[(share, True)] = volumes.UNKNOWN
        for (name, local), volume in expected.items():
            with self.subTest(file_system=name, local=local):
                self.assertEqual(volumes.judge(name, local), volume)
        self.assertEqual(volumes.judge("APFS"), volumes.FINE, "type names compare without case")

    def test_a_device_is_listed_once_and_answered_from_the_cache(self):
        self.table = [(7, "apfs", True), (9, "hfs", True)]
        self.assertIs(volumes.volume(7, FINE_NS), volumes.FINE)
        self.assertEqual(volumes.volume(9, WHOLE_NS), HFS)
        self.assertEqual(self.listed, 1, "one listing answered both devices")
        for _ in range(3):
            volumes.volume(7, FINE_NS + 1)
        self.assertEqual(self.listed, 1)
        self.assertIsNone(volumes.volume(None, FINE_NS), "no device: nothing says")

    def test_a_reused_device_number_is_listed_again_when_the_change_time_contradicts_the_answer(self):
        # Measured: hdiutil gave each image the same device once the last was detached.
        self.table = [(7, "apfs", True)]
        self.assertIs(volumes.volume(7, FINE_NS), volumes.FINE)
        self.mount([(7, "hfs", True)])   # The APFS disk ejected, an HFS+ one mounted under its number.
        self.assertEqual(volumes.volume(7, WHOLE_NS), HFS, "a whole-second change time on APFS: listed again")
        self.assertEqual(self.listed, 2)
        self.mount([(7, "msdos", True)])
        self.assertEqual(volumes.volume(7, WHOLE_NS + 10**9).step, 2 * SECOND, "an even second is no HFS+ proof")
        self.assertEqual(self.listed, 3)
        self.mount([(7, "exfat", True)])
        self.assertEqual(volumes.volume(7, WHOLE_NS + 120_000_000).step, 100_000, "a counted mount: listed again")
        self.assertEqual(self.listed, 4)

    def test_a_reused_device_whose_stamps_fit_the_old_answer_is_listed_again(self):
        # Break-it review M1: an unknown type stamping nanoseconds (a third-party NTFS) under APFS's old number.
        self.table = [(8, "apfs", True)]
        self.assertIs(volumes.volume(8, FINE_NS), volumes.FINE)
        self.mount([(8, "ufsd_NTFS", True)])
        self.assertIs(volumes.volume(8, FINE_NS + 777), volumes.UNKNOWN)
        stamped = filetime(FINE_NS + 777)
        self.assertFalse(change_clock.settled(stat(8, FINE_NS + 777), Clock(stamped)))
        self.table = [(9, "msdos", True)]   # Review L1: FAT is no coarsest answer; a volume after it is judged.
        volumes.volume(9, WHOLE_NS + 10**9)
        self.mount([(9, "udf", True)])
        # Judged again as unknown (msdos's answer would trust it): a writer's even-second stamp may be FAT's; an
        # odd-second one cannot be (FAT keeps 2 s steps) and is trusted.
        with self.assertRaisesRegex(change_clock.ClosingRestamps, "writer"):
            change_clock.settle(stat(9, WHOLE_NS + 10**9 - 10**10), Clock(stamped), writing=True)
        odd = stat(9, WHOLE_NS - 10**10)
        self.assertIs(change_clock.settle(odd, Clock(stamped), writing=True), odd)

    def test_a_contradicting_stamp_lists_again_even_before_the_watch_counts_and_a_confirmed_shape_does_not(self):
        self.table = [(7, "apfs", True)]
        volumes.volume(7, FINE_NS)
        self.mount([(7, "hfs", True)], counted=False)   # Mounted; the watch's thread has not woken yet.
        self.assertEqual(volumes.volume(7, WHOLE_NS), HFS)
        self.assertEqual(self.listed, 2)
        for _ in range(3):   # An even second on HFS+ is listed once, and the listing confirms that shape.
            self.assertEqual(volumes.volume(7, WHOLE_NS + 10**9), HFS)
        self.assertEqual(self.listed, 3)

    def test_a_finer_answer_never_stands_for_a_coarser_volume(self):
        swaps = (("exfat", "hfs", WHOLE_NS), ("hfs", "msdos", WHOLE_NS + 10**9), ("apfs", "hfs", WHOLE_NS),
                 ("apfs", "exfat", WHOLE_NS + 120_000_000), ("hfs", "exfat", FINE_NS - 3_456_789),
                 ("apfs", "msdos", WHOLE_NS + 10**9))
        for cached, replaced, stamp in swaps:
            with self.subTest(cached=cached, replaced=replaced), patch.object(volumes, "_known", {}):
                self.table = [(5, cached, True)]
                volumes.volume(5, None)
                self.mount([(5, replaced, True)])
                self.assertEqual(volumes.volume(5, stamp), volumes.judge(replaced))

    def test_a_contradicting_stamp_lists_again_only_when_a_volume_came_or_went(self):
        self.table = [(2, "hfs", True)]
        even_ns = WHOLE_NS + 10**9   # Half of HFS+'s stamps: trailing zeros of 2 s.
        self.assertEqual(volumes.volume(2, even_ns), HFS)
        for _ in range(3):
            self.assertEqual(volumes.volume(2, even_ns), HFS)
        self.assertEqual(self.listed, 2, "listed once more, which confirmed it: the same HFS+ volume")
        self.mount([(2, "msdos", True)])
        self.assertEqual(volumes.volume(2, even_ns).step, 2 * SECOND)
        self.assertEqual(self.listed, 3)
        self.unwatched()   # No watch (its thread could not start): every contradiction lists again.
        with patch.object(volumes, "_known", {}):
            self.table = [(2, "hfs", True)]
            for _ in range(2):
                volumes.volume(2, even_ns)
            self.assertEqual(self.listed, 5)

    def test_a_device_no_mount_has_is_the_coarsest_until_a_volume_comes(self):
        self.assertIs(volumes.volume(4, FINE_NS), volumes.UNKNOWN)
        self.assertIs(volumes.volume(4, FINE_NS), volumes.UNKNOWN)
        self.assertEqual(self.listed, 1, "the watch counted nothing: the table is as it was")
        self.mount([(4, "apfs", True)])
        self.assertIs(volumes.volume(4, FINE_NS), volumes.FINE)
        self.assertEqual(self.listed, 2)

    def test_without_a_watch_every_answer_is_looked_for_again_after_a_while(self):
        self.unwatched()
        self.assertIs(volumes.volume(4, FINE_NS), volumes.UNKNOWN)
        self.table = [(4, "apfs", True)]
        self.assertIs(volumes.volume(4, FINE_NS), volumes.UNKNOWN, "within RECHECK_S the answer stands")
        self.assertEqual(self.listed, 1)
        with patch.object(volumes, "RECHECK_S", -1.0):
            self.assertIs(volumes.volume(4, FINE_NS), volumes.FINE)
        self.assertEqual(self.listed, 2)
        self.table = [(4, "ufsd_NTFS", True)]   # Re-review L-2: nanosecond stamps that fit APFS's answer.
        self.assertIs(volumes.volume(4, FINE_NS), volumes.FINE, "within RECHECK_S")
        with patch.object(volumes, "RECHECK_S", -1.0):
            self.assertIs(volumes.volume(4, FINE_NS), volumes.UNKNOWN, "a FINE answer expires without a watch too")

    def test_answers_listed_before_a_watch_counted_or_in_another_process_are_listed_again(self):
        self.unwatched()
        self.table = [(7, "apfs", True)]
        volumes.volume(7, FINE_NS)
        volumes._watched = (os.getpid(), "late")   # The watch's thread began to wait after that listing.
        volumes.volume(7, FINE_NS)
        self.assertEqual(self.listed, 2)
        volumes.volume(7, FINE_NS)
        self.assertEqual(self.listed, 2)
        with patch.object(volumes.os, "getpid", return_value=os.getpid() + 1):   # A forked child: no watch yet.
            volumes.volume(7, FINE_NS)
        self.assertEqual(self.listed, 3)

    def test_a_darwin_device_is_its_32_bit_dev_t(self):
        self.table = [(0xEDF5F389, "devfs", True), (7, "apfs", True)]
        self.assertIs(volumes.volume(0xFFFFFFFF_EDF5F389, FINE_NS), volumes.UNKNOWN)
        self.assertIs(volumes.volume(7 | (0xFFFFFFFF << 32), FINE_NS), volumes.FINE, "sign-extended st_dev")

    def test_a_table_that_cannot_be_read_answers_the_coarsest_and_is_read_again_after_a_while(self):
        self.table = OSError("no table")
        self.assertIs(volumes.volume(3, FINE_NS), volumes.UNKNOWN)
        self.table = [(3, "apfs", True)]   # Round-3 review: never pinned until a mount, under a live watch.
        self.assertIs(volumes.volume(3, FINE_NS), volumes.UNKNOWN, "within RECHECK_S")
        with patch.object(volumes, "RECHECK_S", -1.0):
            self.assertIs(volumes.volume(3, FINE_NS), volumes.FINE)
        with patch.object(volumes, "_list", side_effect=RuntimeError("anything")):
            self.assertIs(volumes.volume(8, FINE_NS), volumes.UNKNOWN, "never raises into a settle")

    def test_two_mounts_of_one_device_keep_the_coarser_answer(self):
        self.table = [(6, "apfs", True), (6, "hfs", True), (6, "exfat", True)]
        self.assertEqual(volumes.mounts()[6], HFS, "HFS+'s step, and its change time that no program sets")
        self.table = [(6, "exfat", True), (6, "smbfs", False)]
        self.assertEqual(volumes.mounts()[6], volumes.UNKNOWN)


class SettleOnEachVolumeTests(StandIn):
    def test_a_fine_volume_reads_no_clock(self):
        self.table = [(1, "apfs", True)]
        witness = stat(1, FINE_NS)
        with patch.object(change_clock, "system_time", side_effect=AssertionError("clock read")):
            self.assertIs(change_clock.settle(witness), witness)
            change_clock.settle_all([witness, witness])
            self.assertTrue(change_clock.settled(witness))
            self.assertEqual(change_clock.settled_key(witness, ("key",)), ("key",))
            self.assertIs(change_clock.settle(witness, writing=True), witness)
        self.assertFalse(change_clock.coarse_times(1, FINE_NS))

    def test_hfs_waits_until_the_clock_leaves_the_stamp_s_second(self):
        # Measured: HFS+ rounds down to the second, so a write now stamps a time in [now - 1 s, now].
        self.table = [(2, "hfs", True)]
        witness = stat(2, WHOLE_NS)
        stamped = filetime(WHOLE_NS)
        for now, past in ((stamped - SECOND, False), (stamped, False), (stamped + SECOND // 2, False),
                          (stamped + SECOND - 1, False), (stamped + SECOND, True), (stamped + 5 * SECOND, True)):
            with self.subTest(after=(now - stamped) / SECOND):
                self.assertEqual(change_clock.settled(witness, Clock(now)), past)
        clock = Clock(stamped + 3 * SECOND // 10, stamped + SECOND + 1)
        change_clock.settle(witness, clock)
        self.assertEqual(clock.reads, 2)
        self.assertAlmostEqual(self.sleeps[0], 0.7, places=3)
        self.assertTrue(change_clock.coarse_times(2, WHOLE_NS))

    def test_a_ui_refuses_a_coarse_wait_as_changed_but_waits_out_exfat_s_10_ms(self):
        self.table = [(2, "hfs", True), (3, "exfat", True)]
        stamped = filetime(WHOLE_NS)
        with patch.object(change_clock, "_freezes_ui", return_value=True):
            with self.assertRaisesRegex(change_clock.Unsettled, r"HFS\+.*read it again in about 1 s"):
                change_clock.settle(stat(2, WHOLE_NS), Clock(stamped + SECOND // 10))
            with patch.object(change_clock, "system_time", return_value=stamped + SECOND // 10):
                key = change_clock.settled_key(stat(2, WHOLE_NS), ("key",))
            self.assertNotEqual(key, ("key",), "a kept key never stands for an unsettled stat")
            exfat_ns = WHOLE_NS + 120_000_000
            change_clock.settle(stat(3, exfat_ns), Clock(filetime(exfat_ns), filetime(exfat_ns) + 100_001))
        self.assertEqual(len(self.sleeps), 1, "the 10 ms step is waited out, even on the UI thread")

    def test_an_unknown_volume_waits_two_seconds_either_side_and_judges_a_writer_by_its_stamp(self):
        self.table = [(4, "smbfs", False)]
        witness = stat(4, FINE_NS)
        stamped = filetime(FINE_NS)
        self.assertFalse(change_clock.settled(witness, Clock(stamped + 19 * SECOND // 10)), "a fine time too")
        self.assertTrue(change_clock.settled(witness, Clock(stamped + 2 * SECOND + 1)))
        self.assertFalse(change_clock.settled(stat(4, FINE_NS, FINE_NS + 10**9), Clock(stamped)),
                         "a server's clock a second ahead: waits")
        # Whether a share restamps at close is not measured: as Windows judges a share, a writer's time kept in 2 s
        # steps may be FAT's and is refused; a finer one is trusted (a restamp then only fails a later check).
        fine = stat(4, FINE_NS - 10 * 10**9)
        self.assertIs(change_clock.settle(fine, Clock(stamped), writing=True), fine, "an NFS home keeps snapshots")
        with self.assertRaises(change_clock.ClosingRestamps):
            change_clock.settle(stat(4, WHOLE_NS + 10**9 - 10 * 10**9), Clock(stamped), writing=True)   # 2 s steps.
        odd = stat(4, WHOLE_NS - 10 * 10**9)   # A whole odd second: 1 s steps at most, never FAT's.
        self.assertIs(change_clock.settle(odd, Clock(stamped), writing=True), odd, "an odd second is trusted")
        self.assertTrue(issubclass(change_clock.ClosingRestamps, change_clock.Unsettled), "callers treat it as changed")
        self.table = [(2, "hfs", True)]
        old = stat(2, WHOLE_NS - 10 * 10**9)
        self.assertIs(change_clock.settle(old, Clock(stamped), writing=True), old, "HFS+ never restamps at close")

    def test_fat_s_change_time_is_its_modification_time_so_one_set_ahead_is_past(self):
        # Measured: FAT and exFAT on macOS report st_ctime equal to st_mtime, and os.utime moves both.
        self.table = [(2, "msdos", True), (3, "hfs", True)]
        ahead_ns = WHOLE_NS + 3600 * 10**9 + 10**9   # A time a program set an hour ahead.
        now = filetime(WHOLE_NS) + 5 * SECOND
        self.assertEqual([entry[1] for entry in change_clock.times(stat(2, ahead_ns))], ["loose", "loose"])
        self.assertTrue(change_clock.settled(stat(2, ahead_ns), Clock(now)))
        with self.assertRaisesRegex(change_clock.Unsettled, "ahead of the system clock"):
            change_clock.settle(stat(3, WHOLE_NS, ahead_ns), Clock(now))   # HFS+ stamps it: the clock went back.

    def test_a_cache_never_keeps_a_key_from_the_stamp_s_second(self):
        self.table = [(2, "msdos", True)]
        even_ns = WHOLE_NS + 10**9
        with patch.object(change_clock, "system_time", return_value=filetime(even_ns) + SECOND):
            first = change_clock.cache_key(stat(2, even_ns), ("key",))
            self.assertNotEqual(first, ("key",))
            self.assertNotEqual(first, change_clock.cache_key(stat(2, even_ns), ("key",)))
        with patch.object(change_clock, "system_time", return_value=filetime(even_ns) + 2 * SECOND + 1):
            self.assertEqual(change_clock.cache_key(stat(2, even_ns), ("key",)), ("key",))


class LinuxJudgeTests(StandIn):
    platform = "linux"

    def test_a_disk_file_system_stamping_whole_seconds_is_judged_by_its_stamps(self):
        # Re-review L-4: ext2-4 with 128-byte inodes keep seconds (s_time_gran 1 s), rounded down.
        self.table = [(volumes.linux_device(8, 1), "ext4", True)]
        device = volumes.linux_device(8, 1)
        self.assertIs(volumes.volume(device, FINE_NS), volumes.LINUX_DISK)
        self.assertEqual(volumes.volume(device, WHOLE_NS), volumes.Volume(SECOND, SECOND, False, False, True))
        self.assertEqual(volumes.volume(device, WHOLE_NS + 10**9).step, 2 * SECOND)
        self.assertFalse(change_clock.settled(stat(device, WHOLE_NS), Clock(filetime(WHOLE_NS) + SECOND // 2)))

    def test_disk_and_memory_file_systems_tick_and_the_rest_are_the_coarsest(self):
        for name in ("ext4", "xfs", "btrfs", "tmpfs", "overlay", "zfs"):
            self.assertIs(volumes.judge(name), volumes.LINUX_DISK, name)
        for name in ("vfat", "msdos", "exfat", "nfs4", "cifs", "smb3", "fuse.sshfs", "9p", "virtiofs", "new"):
            self.assertIs(volumes.judge(name), volumes.UNKNOWN, name)

    def test_mountinfo_names_each_device_and_type(self):
        text = ("22 1 8:1 / / rw,relatime shared:1 - ext4 /dev/sda1 rw\n"
                "30 22 0:26 / /mnt/my\\040share rw master:4 unbindable - cifs //nas/share rw,vers=3.0\n"
                "31 22 8:17 / /media/stick rw - vfat /dev/sdb1 rw\n"
                "garbage line\n")
        with tempfile.TemporaryDirectory(prefix="angelo-mountinfo-") as folder:
            path = Path(folder) / "mountinfo"
            path.write_text(text, encoding="utf-8")
            listed = _real_linux_mounts(path)
        device = volumes.linux_device
        self.assertEqual(listed, [(device(8, 1), "ext4", True), (device(0, 26), "cifs", True),
                                  (device(8, 17), "vfat", True)])
        self.table = listed
        self.assertEqual([volumes.volume(entry[0], FINE_NS) for entry in listed],
                         [volumes.LINUX_DISK, volumes.UNKNOWN, volumes.UNKNOWN])
        if sys.platform.startswith("linux"):   # Where os.makedev is Linux's own encoding.
            for major, minor in ((8, 1), (259, 3), (0, 26), (4095, 1048575)):
                self.assertEqual(device(major, minor), os.makedev(major, minor))

    @unittest.skipUnless(sys.platform.startswith("linux") and os.path.exists("/proc/self/mountinfo"),
                         environment_skip("Linux's own mount table"))
    def test_this_host_s_temporary_folder_is_judged(self):
        with patch.object(volumes, "_linux_mounts", _real_linux_mounts), tempfile.TemporaryDirectory() as folder:
            found = volumes.volume(os.stat(folder).st_dev, os.stat(folder).st_ctime_ns)
        self.assertIs(found, volumes.LINUX_DISK, "a test run's temporary folder is on a disk or memory file system")


_real_linux_mounts = volumes._linux_mounts


class HostTableTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "darwin", environment_skip("macOS's own mount table"))
    def test_this_mac_s_mount_table_names_every_file_s_device(self):
        with tempfile.TemporaryDirectory(prefix="angelo-volumes-") as folder:
            path = Path(folder) / "witness"
            path.write_bytes(b"x")
            witness = os.stat(path)
            table = {device: (name, local) for device, name, local in volumes._mac_mounts()}
            self.assertIn(witness.st_dev & 0xFFFFFFFF, table, "a file's st_dev is its mount's f_fsid.val[0]")
            # The struct's offsets, checked against the system's own account of the mount (mount(8)).
            point = subprocess.run(["df", "-P", folder], capture_output=True, text=True, check=True)
            point = point.stdout.splitlines()[-1].split(None, 5)[5]
            listed = subprocess.run(["mount"], capture_output=True, text=True, check=True).stdout
            line = next(line for line in listed.splitlines() if " on %s (" % point in line)
            options = line.rsplit(" (", 1)[1].rstrip(")").split(", ")
            self.assertEqual(table[witness.st_dev & 0xFFFFFFFF], (options[0], "local" in options))
            self.assertEqual(change_clock.volume(witness), volumes.judge(options[0], "local" in options))

    @unittest.skipUnless(sys.platform == "darwin" or sys.platform.startswith("linux"),
                         environment_skip("a mount watch: macOS's kqueue or Linux's mountinfo"))
    def test_the_real_watch_waits_and_survives_lookups_from_many_threads(self):
        # Re-review M-1: on Linux a second poll() on the watch's poll object killed the watch for good.
        with tempfile.TemporaryDirectory(prefix="angelo-volumes-") as folder:
            witness = os.stat(folder)
            change_clock.volume(witness)
            for _attempt in range(200):
                watched = volumes._watched
                if watched is not None and watched[0] == os.getpid():
                    break
                time.sleep(0.05)
            self.assertEqual(volumes._watched[0], os.getpid(), "the watch waits once a listing started it")
            watched = volumes._watched
            second = witness.st_ctime_ns - witness.st_ctime_ns % 10**9
            stamps = [second + k * 10**9 for k in range(4)] + [witness.st_ctime_ns]   # Shapes that contradict.

            def lookups():
                for index in range(300):
                    volumes.volume(witness.st_dev, stamps[index % len(stamps)])
            threads = [threading.Thread(target=lookups) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(60)
            self.assertIs(volumes._watched, watched, "the same watch still counts")

    def test_a_listing_never_waits_for_another_thread(self):
        done = []
        worker = threading.Thread(target=lambda: done.append(volumes.mounts()))
        worker.start()
        worker.join(10)
        self.assertEqual(len(done), 1)

    def test_the_agent_and_the_clock_reach_the_same_judge(self):
        self.assertIs(change_clock._posix, volumes)
        self.assertIs(change_clock.Volume, volumes.Volume)


if __name__ == "__main__":
    unittest.main()
