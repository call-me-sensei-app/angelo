# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio's asset library deletes a copy only when its record is proven gone: unknown is never absent.

Regression (release audit of 1.0.4/1.0.5, P1): ``removable`` asked ``os.path.lexists``, which answers False for a
name it can't stat (EACCES), and ``unclean_record`` listed with ``Path.glob``, which in Python 3.13 swallows the
listing's error. A records folder with mode 000 therefore read as "every record gone and the folder clean", and the
next scan deleted the generated .blend copies that linked scenes use (since 1.0.5 a copy holds the only Blender data
of its model). Developed on EACCES (a real ``chmod 000``) and EIO; EPERM, ESTALE and ETIMEDOUT, the per-record stat
and the listing alone are held out.
"""

import errno
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_studio_assets import MODULES, Library  # noqa: E402

with mock.patch.dict(sys.modules, MODULES):
    from angelo_agent import studio_assets  # noqa: E402

NUMBERS = (errno.EIO, errno.EACCES, errno.EPERM, errno.ESTALE, errno.ETIMEDOUT)


class UnknownRecords(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.library = Library(temporary.name)
        self.records = self.library.root / "assets"
        kept = self.library.model("kept")
        self.gone = self.library.model("gone")
        self.library.studio.reload()
        self.manifest = {}
        for record in (kept, self.gone):
            found = studio_assets.item(self.library.root, record, {})
            Path(found["blend"]).parent.mkdir(parents=True, exist_ok=True)
            Path(found["blend"]).write_bytes(b"BLENDER")
            self.manifest[record["id"]] = {"fingerprint": found["fingerprint"], "blend": Path(found["blend"]).name}
        studio_assets._blocking_logged.clear()

    def removable(self):
        with mock.patch("builtins.print"):
            return studio_assets.removable(self.library.root, [self.gone["id"], "not-an-id"])

    def test_a_record_deleted_in_media_studio_still_removes_its_copy(self):
        (self.records / (self.gone["id"] + ".json")).unlink()
        self.assertEqual(self.removable(), ["not-an-id", self.gone["id"]])

    @unittest.skipIf(os.name == "nt" or os.geteuid() == 0, "POSIX permissions as an ordinary user")
    def test_a_records_folder_that_cant_be_read_deletes_no_copy(self):
        self.records.chmod(0)
        try:
            self.assertEqual(self.removable(), ["not-an-id"], "only a key that is no item id is forgotten")
            self.library.studio.reload()   # What the scan sees: no records at all.
            with mock.patch("builtins.print"):
                _convert, remove = self.library.plan(self.manifest)
            self.assertNotIn(self.gone["id"], remove)
        finally:
            self.records.chmod(0o700)
        for found in self.manifest.values():
            self.assertTrue((self.library.root / "blender-assets" / "models" / found["blend"]).exists())

    def test_a_record_whose_presence_cant_be_told_is_not_gone(self):
        name = self.gone["id"] + ".json"
        real = os.lstat
        for number in NUMBERS:
            with self.subTest(errno=errno.errorcode[number]):
                def lstat(path, *args, **kwargs):
                    if os.fspath(path).endswith(name):
                        raise OSError(number, os.strerror(number))
                    return real(path, *args, **kwargs)
                with mock.patch("angelo_agent.studio_assets.os.lstat", lstat):
                    (self.records / name).rename(self.records / (name + ".moved"))   # Even when it really is gone.
                    try:
                        self.assertEqual(self.removable(), ["not-an-id"])
                    finally:
                        (self.records / (name + ".moved")).rename(self.records / name)

    def test_a_listing_that_fails_blocks_every_removal(self):
        (self.records / (self.gone["id"] + ".json")).unlink()
        for number in NUMBERS:
            with self.subTest(errno=errno.errorcode[number]):
                def scandir(path, *args, **kwargs):
                    raise OSError(number, os.strerror(number))
                with mock.patch("angelo_agent.studio_assets.os.scandir", scandir):
                    self.assertEqual(self.removable(), ["not-an-id"])
                    self.assertEqual(studio_assets.unclean_record(self.records), str(self.records))

    def test_the_listing_matches_what_the_reader_reads(self):
        for name in (".hidden.json", "UPPER.JSON", "folder.json", "plain.txt"):
            target = self.records / name
            target.mkdir() if name == "folder.json" else target.write_text("{}", encoding="utf-8")
        expected = sorted(path.name for path in self.records.glob("*.json"))
        self.assertEqual(studio_assets._json_names(self.records), expected)


if __name__ == "__main__":
    unittest.main()
