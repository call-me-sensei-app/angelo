# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The render-device listing on volumes with coarse times: a same-size rewrite inside one stamp step is read.

Evidence: ``test_native_render_device_session``'s ``test_a_saved_no_gpu_listing_is_listed_again_after_a_day`` failed
on main's Windows shard (CI 41896684290): ``_read`` cached the listing by (path, mtime, size), and the test wrote it
again, same size, inside one NTFS clock tick, so the stale ``no_gpu`` came back. It never failed on Mac 2 because
APFS stamps nanoseconds: two writes always differ there. On a volume that keeps coarse times (HFS+ 1 s, FAT 2 s,
exFAT 10 ms) both writes share a stamp whenever they land in one step, so the failure is deterministic: this writes
until they do, then reads. The fix is the change clock (``_read`` keeps a read only under a settled stat), with its
judgement of each volume off Windows too.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts" / "modules"))

from angelo_render_device import device  # noqa: E402
from host_test_policy import environment_skip  # noqa: E402

IMAGES = {"HFS+": ("HFS+", "GPTSPUD"), "FAT32": ("MS-DOS FAT32", "MBRSPUD"), "exFAT": ("ExFAT", "MBRSPUD")}


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
                path = os.path.join(mount, device.FILE)
                for _attempt in range(50):
                    device._cache = None
                    device._write({"listed": {"state": "no_gpu"}})
                    first = os.stat(path)
                    self.assertEqual(device._read()["listed"]["state"], "no_gpu")
                    device._write({"listed": {"state": "set_up"}})   # Same length: the same size.
                    second = os.stat(path)
                    if (first.st_mtime_ns, first.st_size) == (second.st_mtime_ns, second.st_size):
                        break   # Both writes in one stamp step: the cache's key can't tell them apart.
                else:
                    self.fail("no two writes landed in one step of %s" % name)
                self.assertEqual(device._read()["listed"]["state"], "set_up", "the stale listing came back")


if __name__ == "__main__":
    unittest.main()
