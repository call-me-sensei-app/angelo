# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The change clock on real coarse-timestamp volumes on macOS: HFS+, FAT32 and exFAT disk images.

Before the volume judge every settle returned at once off Windows, so a same-size rewrite in the second of a file's
last stamp kept (inode, size, mtime, ctime) and read as unchanged: measured on Mac 2, 30 of 30 rewrites on HFS+ and
FAT32, 18 of 30 on exFAT (audit 1.0.10, re-check 3). Each image is made by ``hdiutil`` in this run's temporary folder,
attached there without showing in the Finder (``-nobrowse``), and detached and removed when the class ends.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts" / "addons_core"))
sys.path.insert(0, str(ROOT / "scripts" / "modules"))

import angelo_change_clock as change_clock  # noqa: E402
from host_test_policy import environment_skip  # noqa: E402

IMAGES = {"HFS+": ("HFS+", "GPTSPUD"), "FAT32": ("MS-DOS FAT32", "MBRSPUD"), "exFAT": ("ExFAT", "MBRSPUD")}
ATTEMPTS = 3


def identity(witness):
    return witness.st_ino, witness.st_size, witness.st_mtime_ns, witness.st_ctime_ns


def write_in_place(path, data, times_from=None):
    """A late writer: same size, new bytes, in place; ``times_from`` a stat whose times it puts back."""
    fd = os.open(path, os.O_RDWR)
    try:
        assert len(data) == os.fstat(fd).st_size
        os.write(fd, data)
        if times_from is not None:
            os.utime(fd, ns=(times_from.st_atime_ns, times_from.st_mtime_ns))
    finally:
        os.close(fd)


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("hdiutil"), environment_skip(
    "Disk images of each coarse file system need macOS's hdiutil; test_native_change_clock_volumes covers the judge"))
class RealCoarseVolumeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.mkdtemp(prefix="angelo-clock-volumes-")
        cls.mounts = {}
        try:
            for name, (file_system, layout) in IMAGES.items():
                tag = name.replace("+", "p").lower()
                image, mount = os.path.join(cls.folder, tag + ".dmg"), os.path.join(cls.folder, "mnt-" + tag)
                os.mkdir(mount)
                subprocess.run(["hdiutil", "create", "-quiet", "-size", "64m", "-fs", file_system, "-volname",
                                tag.upper(), "-layout", layout, image], check=True, timeout=120)
                subprocess.run(["hdiutil", "attach", "-quiet", "-nobrowse", "-noverify", "-noautoopen",
                                "-mountpoint", mount, image], check=True, timeout=120)
                cls.mounts[name] = mount
        except BaseException:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            for mount in cls.mounts.values():
                subprocess.run(["hdiutil", "detach", "-quiet", "-force", mount], timeout=120)
        finally:
            cls.mounts = {}
            shutil.rmtree(cls.folder, ignore_errors=True)

    def files(self):
        for name, mount in self.mounts.items():
            path = os.path.join(mount, "witness.bin")
            with open(path, "wb") as handle:
                handle.write(b"before")
            yield name, path

    def test_a_settled_witness_always_differs_after_a_same_size_rewrite_that_puts_its_times_back(self):
        # Only where a change time is kept: FAT and exFAT report the modification time as it, so a writer that puts
        # that back is outside what any stat sees (Windows' FAT and exFAT keep none either).
        for name, path in self.files():
            if name != "HFS+":
                continue
            for attempt in range(ATTEMPTS):
                with self.subTest(file_system=name, attempt=attempt):
                    write_in_place(path, b"before")
                    before = change_clock.settle(os.stat(path))   # A thread without a UI: it waits the step out.
                    write_in_place(path, b"after!", times_from=before)
                    after = os.stat(path)
                    self.assertEqual(before.st_mtime_ns, after.st_mtime_ns, "the late writer put the time back")
                    self.assertNotEqual(identity(before), identity(after), "the change time moved past the settle")

    def test_a_kept_key_never_stands_for_a_later_rewrite(self):
        for name, path in self.files():
            for attempt in range(ATTEMPTS):
                with self.subTest(file_system=name, attempt=attempt):
                    write_in_place(path, b"first!")
                    witness = os.stat(path)
                    key = change_clock.settled_key(witness, (witness.st_size, witness.st_mtime_ns))
                    write_in_place(path, b"second")
                    now = os.stat(path)
                    self.assertNotEqual((now.st_size, now.st_mtime_ns), key, "a digest kept under it would be stale")

    def test_a_cache_reads_a_file_written_this_second_again(self):
        for name, path in self.files():
            with self.subTest(file_system=name):
                write_in_place(path, b"cached")
                witness = os.stat(path)
                key = (witness.st_size, witness.st_mtime_ns)
                with patch.object(change_clock, "system_time",
                                  return_value=witness.st_mtime_ns // 100 + 116444736000000000):
                    self.assertNotEqual(change_clock.cache_key(witness, key), key)

    def test_a_ui_refuses_a_just_written_hfs_or_fat_file_as_changed_and_waits_out_exfat(self):
        ui = types.SimpleNamespace(app=types.SimpleNamespace(background=False))
        for name, path in self.files():
            with self.subTest(file_system=name), patch.dict(sys.modules, {"bpy": ui}):
                write_in_place(path, b"in ui!")
                witness = os.stat(path)
                stamped = witness.st_mtime_ns // 100 + 116444736000000000
                if name == "exFAT":   # 10 ms steps: shorter than a tick, waited out even there.
                    with patch.object(change_clock, "system_time", side_effect=[stamped, stamped + 100_001]):
                        self.assertIs(change_clock.settle(witness), witness)
                    continue
                with patch.object(change_clock, "system_time", return_value=stamped):
                    with self.assertRaisesRegex(change_clock.Unsettled, "read it again"):
                        change_clock.settle(witness)
                    # A change detector (change_keys) then asks without waiting, and keys the file by its bytes.
                    self.assertFalse(change_clock.settled(witness))



def make_image(folder, name, file_system, layout):
    image = os.path.join(folder, name + ".dmg")
    subprocess.run(["hdiutil", "create", "-quiet", "-size", "64m", "-fs", file_system, "-volname", name.upper(),
                    "-layout", layout, image], check=True, timeout=120)
    return image


def attach(image, mount):
    os.makedirs(mount, exist_ok=True)
    subprocess.run(["hdiutil", "attach", "-quiet", "-nobrowse", "-noverify", "-noautoopen", "-mountpoint", mount,
                    image], check=True, timeout=120)


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("hdiutil"), environment_skip(
    "Disk images need macOS's hdiutil; test_native_change_clock_volumes covers a reused device on a stand-in table"))
class RealRemountTests(unittest.TestCase):
    def test_a_volume_mounted_under_an_ejected_one_s_device_is_judged_afresh(self):
        # Measured: hdiutil gives the next image the device of the one just detached. FAT first, then HFS+ with a
        # change time on an even second, which fits FAT's 2 s answer: only the mount watch can tell them apart.
        folder, attached = tempfile.mkdtemp(prefix="angelo-clock-remount-"), []
        try:
            change_clock.volume(os.stat(folder))   # The first listing starts the watch; it counts once it waits.
            for _attempt in range(100):
                watched = change_clock._posix._watched
                if watched is not None and watched[0] == os.getpid():
                    break
                time.sleep(0.05)
            self.assertEqual(change_clock._posix._watched[0], os.getpid(), "the mount watch waits")
            first = make_image(folder, "first", "MS-DOS FAT32", "MBRSPUD")
            second = make_image(folder, "second", "HFS+", "GPTSPUD")
            seen = []
            for image, mount, expected in ((first, os.path.join(folder, "mnt-first"), (20_000_000, False)),
                                           (second, os.path.join(folder, "mnt-second"), (10_000_000, True))):
                attach(image, mount)
                attached.append(mount)
                path = os.path.join(mount, "witness.bin")
                for _attempt in range(40):   # Until the change time falls on an even second (a 2 s step's).
                    with open(path, "wb") as handle:
                        handle.write(b"x")
                    witness = os.stat(path)
                    if witness.st_ctime_ns % 2_000_000_000 == 0:
                        break
                    time.sleep(0.25)
                else:
                    self.fail("no even-second change time in 10 s")
                seen.append(witness.st_dev)
                volume = change_clock.volume(witness)
                self.assertEqual((volume.step, volume.changes), expected)
                subprocess.run(["hdiutil", "detach", "-quiet", "-force", mount], check=True, timeout=120)
                attached.remove(mount)
            if seen[0] != seen[1]:   # Another disk attached in between took the number: no reuse to judge.
                self.skipTest(environment_skip("the second image got another device (%r)" % seen))
        finally:
            for mount in attached:
                subprocess.run(["hdiutil", "detach", "-quiet", "-force", mount], timeout=120)
            shutil.rmtree(folder, ignore_errors=True)

if __name__ == "__main__":
    unittest.main()
