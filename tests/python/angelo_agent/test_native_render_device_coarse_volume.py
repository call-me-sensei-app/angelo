# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The render-device listing on volumes with coarse times: a same-size rewrite inside one stamp step is read.

Evidence: ``test_native_render_device_session``'s ``test_a_saved_no_gpu_listing_is_listed_again_after_a_day`` failed
on main's Windows shard (CI 41896684290): ``_read`` cached the listing by (path, mtime, size), and the test wrote it
again, same size, inside one NTFS clock tick, so the stale ``no_gpu`` came back. It never failed on Mac 2 because
APFS stamps nanoseconds: two writes always differ there. On a volume that keeps coarse times (HFS+ 1 s, FAT 2 s,
exFAT 10 ms) both writes share a stamp whenever they land in one step. The fix is the change clock (``_read`` keeps a
read only under a settled stat), with its judgement of each volume off Windows too.

Both writes are real, on each real volume, and the step is forced, never waited for: the change clock is held in the
first write's step and the second write carries the first's stamp, which is what any write landing in that step
carries. An earlier form wrote until two writes happened to share a stamp, and a late second write (a loaded machine;
exFAT's step is 10 ms) failed it 1 time in 4 on the 1.1 tree on 2026-11-11 with "no two writes landed in one step of
exFAT"; delaying each write by one step reproduced that every time. What the forcing assumes is the second test: on
each volume a write's stamp lies within the step the change clock judges it by.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts" / "modules"))

import angelo_change_clock as change_clock  # noqa: E402
from angelo_render_device import device  # noqa: E402
from host_test_policy import environment_skip  # noqa: E402

IMAGES = {"HFS+": ("HFS+", "GPTSPUD"), "FAT32": ("MS-DOS FAT32", "MBRSPUD"), "exFAT": ("ExFAT", "MBRSPUD")}
WRITES = 100   # Per volume, for the stamps' window.


def filetime(time_ns):
    return change_clock._filetime(time_ns)


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("hdiutil"), environment_skip(
    "Disk images of each coarse file system need macOS's hdiutil"))
class CoarseVolumeListingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder, cls.mounts = tempfile.mkdtemp(prefix="angelo-render-device-"), {}
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

    def tearDown(self):
        device.attach(None)
        device._cache = None

    def test_a_same_size_rewrite_in_one_stamp_step_is_read(self):
        for name, mount in self.mounts.items():
            with self.subTest(file_system=name):
                device.attach(mount)
                device._cache = None
                path = os.path.join(mount, device.FILE)
                device._write({"listed": {"state": "no_gpu"}})
                first = os.stat(path)
                self.assertGreater(change_clock.volume(first).step, 0, "a coarse volume")
                in_step = filetime(first.st_mtime_ns)   # The clock still in the first write's step.
                with mock.patch.object(change_clock, "system_time", return_value=in_step):
                    self.assertEqual(device._read()["listed"]["state"], "no_gpu")
                    device._write({"listed": {"state": "set_up"}})   # Same length: the same size.
                    os.utime(path, ns=(first.st_atime_ns, first.st_mtime_ns))   # What a write in that step carries.
                    second = os.stat(path)
                    self.assertEqual((first.st_mtime_ns, first.st_size), (second.st_mtime_ns, second.st_size))
                    self.assertEqual(device._read()["listed"]["state"], "set_up", "the stale listing came back")

    def test_every_write_is_stamped_within_its_volumes_step_of_the_clock(self):
        # What the forcing above stands for: a write's stamp is never older than the clock's reading before it by
        # more than the step the change clock judges the volume by (``below``), and never ahead of the reading after
        # it, so a read the clock has carried past the step is never followed by a write in that step.
        for name, mount in self.mounts.items():
            with self.subTest(file_system=name):
                path = os.path.join(mount, "stamp.bin")
                for index in range(WRITES):
                    before = change_clock.system_time()
                    with open(path, "wb") as handle:
                        handle.write(b"%08d" % index)
                    after = change_clock.system_time()
                    found = os.stat(path)
                    stamp, below = filetime(found.st_mtime_ns), change_clock.volume(found).below
                    self.assertGreaterEqual(stamp, before - below, "stamped older than its volume's step")
                    self.assertLessEqual(stamp, after, "stamped ahead of the clock")
                    time.sleep(0.001 * (index % 5))   # Writes at every offset within a step.


if __name__ == "__main__":
    unittest.main()
