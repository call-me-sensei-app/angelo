# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The missing ``<installer>.sha256`` files of a version published before every installer had one
(publish_installers.py --add-checksum-files).

Dependency-free and offline, on the loopback stand-in for R2 of test_native_publish_installers (never a real
bucket). Covered: a version exactly as the earlier tool wrote it (installers, a delta, ``SHA256SUMS``) gains one
checksum file per installer, each line copied from ``SHA256SUMS``, and nothing else is written; a version shaped
like 1.0.4 (the disk image in ``SHA256SUMS``, the MSI added later with its own file) gains only the disk image's;
nothing to add, no ``SHA256SUMS`` and a state the verification rejects are refused before any write; a
``SHA256SUMS`` line that is not the served installer fails the verification; the dry run is offline; and the
option takes nothing but the version.
"""

import hashlib
import io
import json
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_publish_add_installer import (DMG, MSI, VERSION, AddCase, key, layout, make_delta,  # noqa: E402
                                               tool)
from test_native_publish_installers import ENVIRON  # noqa: E402


class BackfillTests(AddCase):
    def publish_the_old_way(self, platforms, delta=None):
        """The objects exactly as the tool before 1.0.6 wrote a first publish: installers, deltas, SHA256SUMS."""
        entries = {}
        for platform in platforms:
            self.put_object(layout.installer_name(VERSION, platform), self.path(platform).read_bytes())
            entries[layout.installer_name(VERSION, platform)] = self.digest(platform)
        if delta is not None:
            self.put_object(delta.name, delta.read_bytes())
            entries[delta.name] = self.sha(delta.read_bytes())
        self.put_object(layout.SUMS, layout.sums_text(entries).encode())
        return entries

    @staticmethod
    def sha(data):
        return hashlib.sha256(data).hexdigest()

    def backfill(self, *extra, **options):
        return self.run_tool("--version", VERSION, "--add-checksum-files", *extra, **options)

    def test_a_version_published_the_old_way_gains_each_installer_s_file_and_nothing_else(self):
        delta = make_delta(self.folder, VERSION)
        entries = self.publish_the_old_way(["macos-arm64", "windows-x64"], delta)
        sums = self.r2.objects[key(layout.SUMS)]
        self.r2.log.clear()
        code, out = self.backfill()
        self.assertEqual(code, 0, self.errors)
        self.assertEqual(self.puts(), [key(DMG + ".sha256"), key(MSI + ".sha256")], "only checksum files")
        self.assertEqual(self.r2.objects[key(layout.SUMS)], sums, "SHA256SUMS is never rewritten")
        self.assertEqual(self.r2.objects[key(DMG + ".sha256")],
                         (layout.sums_text({DMG: entries[DMG], delta.name: entries[delta.name]}).encode(),
                          layout.headers(layout.SUMS)))
        self.assertEqual(self.r2.objects[key(MSI + ".sha256")][0].decode(), layout.sums_text({MSI: entries[MSI]}))
        result = json.loads(out)
        self.assertEqual(sorted(result["checksum_files"]), sorted(self.served(n + ".sha256") for n in (DMG, MSI)))
        self.assertEqual(len(result["verified"]), 6, "SHA256SUMS, two checksum files, two installers, the delta")
        self.assertEqual(self.verify_only(["macos-arm64", "windows-x64"]), 0, self.errors)

    def test_a_version_shaped_like_1_0_4_gains_only_the_missing_file(self):
        self.publish_the_old_way(["macos-arm64"])
        self.put_object(MSI, self.msi.read_bytes())
        self.put_object(MSI + ".sha256", layout.sums_text({MSI: self.digest("windows-x64")}).encode())
        before = self.r2.objects[key(MSI + ".sha256")]
        self.r2.log.clear()
        self.assertEqual(self.backfill()[0], 0, self.errors)
        self.assertEqual(self.puts(), [key(DMG + ".sha256")])
        self.assertEqual(self.r2.objects[key(MSI + ".sha256")], before)
        self.assertEqual(self.verify_only(["macos-arm64", "windows-x64"]), 0, self.errors)

    def test_nothing_to_add_no_sha256sums_or_a_rejected_state_writes_nothing(self):
        self.refused_without_writes("is not published (no SHA256SUMS)", [], "--add-checksum-files")
        self.assertEqual(self.run_platforms(["macos-arm64"])[0], 0, self.errors)   # 1.0.6 on: it has its file.
        self.refused_without_writes("has its own checksum file already", [], "--add-checksum-files")
        self.r2.objects.clear()
        self.publish_the_old_way(["macos-arm64"])
        self.put_object(layout.SUMS, layout.sums_text({DMG: self.digest("macos-arm64"), MSI: "1" * 64}).encode())
        self.refused_without_writes("SHA256SUMS lists %s, which is not published" % MSI, [], "--add-checksum-files")
        self.assertIn("so no checksum file is added", self.errors)

    def test_a_sha256sums_line_that_is_not_the_served_installer_fails_the_verification(self):
        self.publish_the_old_way(["macos-arm64"])
        self.put_object(DMG, b"another disk image")
        self.assertEqual(self.backfill()[0], 1)
        self.assertIn("is not the file published", self.errors)

    def test_the_dry_run_is_offline_and_the_option_takes_only_the_version(self):
        code, out = self.backfill("--dry-run", environ={})
        self.assertEqual(code, 0, self.errors)
        self.assertEqual(self.r2.log, [])
        self.assertEqual([item["key"] for item in json.loads(out)["objects"]],
                         [key(DMG + ".sha256"), key(MSI + ".sha256")])
        for extra in (["--dmg", str(self.dmg)], ["--add-to-version"], ["--verify-only"], ["--msi-signing", "signed"]):
            with self.subTest(extra=extra), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                tool.main(["--version", VERSION, "--add-checksum-files", *extra], target=self.r2.target,
                          environ=ENVIRON)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            tool.main(["--check", "--add-checksum-files"], target=self.r2.target, environ=ENVIRON)


if __name__ == "__main__":
    unittest.main()
