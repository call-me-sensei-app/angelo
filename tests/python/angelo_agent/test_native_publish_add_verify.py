# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Verifying a version a platform was added to (publish_installers.py --verify-only and after publishing).

Dependency-free and offline, on the loopback stand-in for R2 of test_native_publish_installers. Covered: both kinds
of checksum file are checked and a mismatch in either fails, against the local file and against the download;
every file a checksum file lists is checked, a delta added later and an installer not given included; a checksum
file in any other form, or one covering a file twice, fails; dry runs and ``--verify-only`` never send an S3
request; the custom domain is never asked for a file the bucket showed absent, nor for an installer no
checksum file lists, so its cache never keeps a 404 for a file a later addition writes; the checksum file served
must be the one written; and a 404 on a file the run wrote is named as a cached one.
"""

import hashlib
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_publish_add_installer import (DMG, MSI, VERSION, AddCase, key, layout, make_delta,  # noqa: E402
                                               shifted, tool)


class VerifyTests(AddCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.run_platforms(["macos-arm64"])[0], 0, self.errors)
        self.assertEqual(self.run_platforms(["windows-x64"], "--add-to-version")[0], 0, self.errors)
        self.published = dict(self.r2.objects)

    def tamper(self, name, data):
        self.r2.objects = dict(self.published)
        self.r2.objects[key(name)] = (data, self.published[key(name)][1])

    def test_both_checksum_files_are_checked_and_a_mismatch_in_either_fails(self):
        self.assertEqual(self.verify_only(["macos-arm64", "windows-x64"]), 0, self.errors)
        wrong = "0" * 64
        checksums = {   # The checksum file, what it now says, the file it covers and the other platform.
            "SHA256SUMS": (layout.SUMS, layout.sums_text({DMG: wrong}).encode(), DMG, "windows-x64"),
            "the added checksum": (MSI + ".sha256", layout.sums_text({MSI: wrong}).encode(), MSI, "macos-arm64"),
        }
        for label, (name, data, blamed, other) in checksums.items():
            with self.subTest(tampered=label):
                self.tamper(name, data)
                self.assertEqual(self.verify_only(["macos-arm64", "windows-x64"]), 1)
                self.assertIn("%s: the published checksum %s is not the local file's" % (self.served(blamed), wrong),
                              self.errors)
                self.assertEqual(self.verify_only([other]), 1, "checked against the download without the local file")
                self.assertIn("%s: the download" % self.served(blamed), self.errors)
        for label, name, path in (("the first installer", DMG, self.dmg), ("the added installer", MSI, self.msi)):
            with self.subTest(tampered=label):
                data = path.read_bytes()   # Flip the last byte: always a different file (urandom may end in "x").
                self.tamper(name, data[:-1] + bytes([data[-1] ^ 0xFF]))
                self.assertEqual(self.verify_only(["macos-arm64", "windows-x64"]), 1)
                self.assertIn("%s: the download" % self.served(name), self.errors)
                self.assertIn("is not the file published", self.errors)

    def test_every_listed_installer_is_checked_even_one_not_given(self):
        for given, other in (("macos-arm64", MSI), ("windows-x64", DMG)):
            with self.subTest(given=given):
                self.r2.objects = dict(self.published)
                self.assertEqual(self.verify_only([given]), 0, self.errors)
                self.tamper(other, b"another file")
                self.assertEqual(self.verify_only([given]), 1)
                self.assertIn("%s: " % self.served(other), self.errors)

    def test_a_checksum_file_in_another_form_or_covering_twice_fails(self):
        good = hashlib.sha256(self.msi.read_bytes()).hexdigest()
        line = "%s  %s\n" % (good, MSI)
        for label, text in (("not one", "not a checksum\n"), ("another file", layout.sums_text({DMG: good})),
                            ("CRLF", line.replace("\n", "\r\n")), ("upper case", line.upper()),
                            ("binary marker", line.replace("  ", " *")), ("no final LF", line[:-1]),
                            ("twice", line * 2), ("a stranger listed", line + "%s  %s\n" % ("0" * 64, MSI + ".x"))):
            with self.subTest(label):
                self.tamper(MSI + ".sha256", text.encode())
                self.assertEqual(self.verify_only(["macos-arm64"]), 1, label)
        self.r2.objects = dict(self.published)
        self.put_object(DMG + ".sha256", layout.sums_text({DMG: self.digest("macos-arm64")}).encode())
        self.assertEqual(self.verify_only(["macos-arm64", "windows-x64"]), 0, "SHA256SUMS and its own file agree")
        self.put_object(DMG + ".sha256", layout.sums_text({DMG: "5" * 64}).encode())
        self.assertEqual(self.verify_only(["macos-arm64", "windows-x64"]), 1)
        self.assertIn("%s is covered twice, by SHA256SUMS and by %s.sha256, with different checksums" % (DMG, DMG),
                      self.errors)

    def test_a_local_file_that_is_not_the_published_one_fails(self):
        self.msi.write_bytes(self.msi.read_bytes() + b"rebuilt")
        self.assertEqual(self.verify_only(["macos-arm64", "windows-x64"]), 1)
        self.assertIn("is not the local file's", self.errors)


class AddedDeltaTests(AddCase):
    def test_a_delta_added_later_is_verified_without_being_given(self):
        delta = make_delta(self.folder, VERSION)
        self.assertEqual(self.run_platforms(["windows-x64"])[0], 0, self.errors)
        self.assertEqual(self.run_platforms(["macos-arm64"], "--add-to-version", "--delta", str(delta))[0], 0,
                         self.errors)
        self.assertEqual(self.verify_only(["windows-x64"]), 0, self.errors)
        self.r2.objects[key(delta.name)] = (b"tampered delta", self.r2.objects[key(delta.name)][1])
        self.assertEqual(self.verify_only(["windows-x64"]), 1)
        self.assertIn("%s: " % self.served(delta.name), self.errors)
        del self.r2.objects[key(DMG + ".sha256")]   # Without its checksum file the disk image is not published.
        self.assertEqual(self.verify_only(["macos-arm64", "windows-x64"]), 1)
        self.assertIn("%s is not published (no checksum file covers it)" % self.served(DMG), self.errors)

    def test_the_checksum_file_served_must_be_the_one_written(self):
        """A stored .sha256 other than the one sent fails, even one listing only files that exist and match."""
        extra = layout.delta_name(shifted(VERSION, -1), VERSION, "macos-arm64")
        self.assertEqual(self.run_platforms(["windows-x64"])[0], 0, self.errors)
        self.put_object(extra, b"a delta published some other way")
        upload = tool.upload

        def stored_otherwise(bucket, item, data=None):
            upload(bucket, item, data)
            if item.name == DMG + ".sha256":
                listed = layout.parse_sums(data.decode())
                listed[extra] = hashlib.sha256(b"a delta published some other way").hexdigest()
                self.put_object(item.name, layout.sums_text(listed).encode())

        with mock.patch.object(tool, "upload", stored_otherwise):
            self.assertEqual(self.run_platforms(["macos-arm64"], "--add-to-version")[0], 1)
        self.assertIn("%s: " % self.served(DMG + ".sha256"), self.errors)
        self.assertIn("is not the file published", self.errors)


class CachedNotFoundTests(AddCase):
    """A custom domain that caches every 404 it serves, as Cloudflare can."""

    def setUp(self):
        super().setUp()
        self.cached, served = set(), self.r2.public

        def cdn(handler, name):
            if name in self.cached:
                return handler._answer(404, {}, b"cached 404")
            if name not in self.r2.objects:
                self.cached.add(name)
            return served(handler, name)

        self.r2.public = cdn

    def test_a_cached_404_on_the_checksum_file_the_run_wrote_is_named_as_one(self):
        self.assertEqual(self.run_platforms(["macos-arm64"])[0], 0, self.errors)
        self.assertEqual(self.cached, set(), "a first publish asks for nothing the bucket showed absent")
        self.assertEqual(self.verify_only(["macos-arm64"]), 0, self.errors)
        self.assertEqual(self.cached, {key(MSI + ".sha256")},
                         "only the absent platform's checksum file, never an installer")
        self.assertEqual(self.run_platforms(["windows-x64"], "--add-to-version")[0], 1)
        self.assertIn("%s answered 404, but it is the checksum file this run wrote: a 404 the custom domain's "
                      "cache kept? Purge that URL" % self.served(MSI + ".sha256"), self.errors)
        self.assertNotIn("not published (no checksum file covers it)", self.errors)

    def test_a_cached_404_on_an_installer_the_run_wrote_is_named_as_one(self):
        """A --verify-only run between an addition's checksum file and its installer caches the installer's 404."""
        self.assertEqual(self.run_platforms(["windows-x64"])[0], 0, self.errors)
        upload = tool.upload

        def someone_verifies_in_the_window(bucket, item, data=None):
            upload(bucket, item, data)
            if item.name == DMG + ".sha256":
                self.assertEqual(self.verify_only(["windows-x64"]), 1)

        with mock.patch.object(tool, "upload", someone_verifies_in_the_window):
            self.assertEqual(self.run_platforms(["macos-arm64"], "--add-to-version")[0], 1)
        self.assertIn(key(DMG), self.cached)
        self.assertIn("HEAD %s answered 404, but it is a file this run wrote" % self.served(DMG), self.errors)


class RequestTests(AddCase):
    def test_dry_runs_and_verify_only_never_send_an_s3_request(self):
        self.assertEqual(self.run_platforms(["macos-arm64"])[0], 0, self.errors)
        before = dict(self.r2.objects)
        for extra in (("--dry-run",), ("--dry-run", "--add-to-version"), ("--verify-only",),
                      ("--verify-only", "--add-to-version")):
            with self.subTest(extra=extra):
                self.r2.log.clear()
                self.run_platforms(["windows-x64"], *extra)       # Credentials are in the environment.
                self.assertEqual(self.r2.log, [], "no S3 request at all")
                self.assertEqual(self.r2.objects, before)

    def test_an_unpublished_platform_is_asked_for_only_through_its_checksum_file(self):
        """Publishing asks the domain for nothing the bucket showed absent; --verify-only asks for an absent
        platform's .sha256 (the one probe it needs) but never for the installer it would list; an addition's
        writes all come before the domain is asked for them."""
        timeline, s3 = self.record_public(), self.r2.s3

        def recorded(handler, body):
            timeline.append(("S3 " + handler.command, handler.path.split("/", 2)[2]))
            return s3(handler, body)

        self.r2.s3 = recorded
        self.assertEqual(self.run_platforms(["macos-arm64"])[0], 0, self.errors)
        self.assertEqual([entry for entry in timeline if MSI in entry[1] and not entry[0].startswith("S3")], [],
                         "the bucket showed them absent")
        del timeline[:]
        self.assertEqual(self.verify_only(["macos-arm64"]), 0, self.errors)
        self.assertEqual([entry for entry in timeline if MSI in entry[1]], [("GET", key(MSI + ".sha256"))])
        del timeline[:]
        self.assertEqual(self.run_platforms(["windows-x64"], "--add-to-version")[0], 0, self.errors)
        for name in (MSI + ".sha256", MSI):
            public = [index for index, (method, path) in enumerate(timeline) if path == key(name)
                      and not method.startswith("S3")]
            self.assertTrue(public, name)
            self.assertLess(timeline.index(("S3 PUT", key(name))), public[0], "written before it is asked for")




if __name__ == "__main__":
    unittest.main()
