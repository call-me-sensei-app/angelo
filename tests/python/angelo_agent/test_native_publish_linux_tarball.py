# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Publishing the Linux tarball (publish_installers.py --tarball) to a stand-in for the R2 bucket.

The tarball goes up with its ``.minisig``; its own ``<tarball>.sha256`` lists both, so a publish cut off part way
and the public read-back cover the signature like any other file. Covered: a first publish with the macOS disk image
(SHA256SUMS lists all three files, the signature is never a feed download); Linux added to a published version with
``--add-to-version`` (SHA256SUMS untouched, the checksum file first); and the refusals before anything is sent: no
signature, a signature that does not match, a manifest signed by a key the checkout does not trust, no builder-path
receipt.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import os
import shutil
import sys
import tarfile
from pathlib import Path
from unittest import mock
import unittest

from host_test_policy import environment_skip

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/angelo"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_downloads as layout  # noqa: E402
import sign_linux_release  # noqa: E402
from sign_linux_release import release_manifest  # noqa: E402
from test_native_linux_release_signing import Signer  # noqa: E402
from test_native_publish_add_installer import DMG, MSI, VERSION, AddCase, key  # noqa: E402

TARBALL = layout.installer_name(VERSION, "linux-x64")
MINISIG = layout.signature_name(VERSION, "linux-x64")


REASONS = {"no signature": "has no %s beside it" % MINISIG,
           "another file's signature": "the signature does not match the file",
           "no receipt": "has no builder-path receipt",
           "an untrusted manifest key": "The manifest signature does not match"}


@unittest.skipUnless(os.name == "posix", environment_skip("Linux release folders: POSIX file modes, owners and links"))
class LinuxCase(AddCase):
    def setUp(self):
        super().setUp()
        self.signer = Signer()
        self.keys_path = self.folder / "linux_release_keys.json"
        self.keys_path.write_text(json.dumps({"keys": []}), encoding="utf-8")
        feed_keys = self.folder / "update_keys.json"
        feed_keys.write_text(json.dumps({"keys": []}), encoding="utf-8")
        sign_linux_release.add_key("rehearsal-1", self.signer.public_key(), self.keys_path, self.folder / "pub",
                                   feed_keys)
        patcher = mock.patch.object(release_manifest, "KEYS_FILE", self.keys_path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tarball = self.make_tarball(self.signer)

    def make_tarball(self, signer, folder_name="dist"):
        tree = self.folder / folder_name / release_manifest.root_name(VERSION)
        addon = tree / "5.3/scripts/addons_core/angelo_agent"
        addon.mkdir(parents=True)
        shutil.copyfile(self.keys_path, addon / "linux_release_keys.json")
        (tree / "angelo").write_bytes(os.urandom(50_000))
        for path in [tree] + list(tree.rglob("*")):
            os.chmod(path, 0o755 if path.is_dir() or path.name == "angelo" else 0o644)
        if signer is self.signer:
            sign_linux_release.write_manifest(tree, VERSION, "rehearsal-1", signer, self.keys_path)
        else:   # A manifest signed by a key the checkout does not trust, under the trusted key's id.
            self.sign_untrusted(tree, signer)
        tarball = self.folder / TARBALL
        with tarfile.open(tarball, "w:xz") as archive:
            archive.add(tree, arcname=tree.name)
        Path(str(tarball) + ".minisig").write_text(
            sign_linux_release.minisign_signature(tarball, "rehearsal-1", self.signer))
        self.write_receipt(tarball)
        return tarball

    def sign_untrusted(self, tree, signer):
        entries = release_manifest.describe_tree(tree)
        manifest = release_manifest.manifest_bytes(entries, VERSION)
        (tree / release_manifest.MANIFEST_NAME).write_bytes(manifest)
        (tree / release_manifest.SIGNATURE_NAME).write_bytes(release_manifest.signature_bytes(
            "rehearsal-1", signer.sign(release_manifest.signed_message(manifest))))

    def path(self, platform):
        return self.tarball if platform == "linux-x64" else super().path(platform)

    def run_platforms(self, platforms, *extra, **options):
        argv = []
        for platform in platforms:
            if platform == "linux-x64":
                argv += ["--tarball", str(self.tarball)]
        rest = [platform for platform in platforms if platform != "linux-x64"]
        if not rest:
            return self.run_tool("--version", VERSION, *argv, *extra, **options)
        return super().run_platforms(rest, *argv, *extra, **options)


class PublishTests(LinuxCase):
    def test_a_first_publish_lists_the_signature_with_its_tarball(self):
        code, out = self.run_platforms(["macos-arm64", "linux-x64"])
        self.assertEqual(code, 0, self.errors)
        puts = self.puts()
        self.assertEqual(puts[-1], key(layout.SUMS), "SHA256SUMS last: the version is complete")
        self.assertLess(puts.index(key(MINISIG)), puts.index(key(TARBALL + ".sha256")))
        own = layout.parse_sums(self.r2.objects[key(TARBALL + ".sha256")][0].decode())
        self.assertEqual(set(own), {TARBALL, MINISIG})
        self.assertEqual(set(layout.parse_sums(self.r2.objects[key(layout.SUMS)][0].decode())), {DMG, TARBALL, MINISIG})
        self.assertEqual(self.r2.objects[key(MINISIG)][1]["Content-Type"], "text/plain")
        result = json.loads(out)
        self.assertEqual(result["feed_downloads"], ["macos-arm64=%s" % self.dmg, "linux-x64=%s" % self.tarball])
        self.assertIn("linux", result["website_sha256"])
        self.assertEqual(self.verify_only(["macos-arm64", "linux-x64"]), 0, self.errors)

    def test_linux_added_to_a_published_version(self):
        self.assertEqual(self.run_platforms(["macos-arm64", "windows-x64"])[0], 0, self.errors)
        sums = self.r2.objects[key(layout.SUMS)]
        self.r2.log.clear()
        self.assertEqual(self.run_platforms(["linux-x64"], "--add-to-version")[0], 0, self.errors)
        self.assertEqual(self.puts(), [key(TARBALL + ".sha256"), key(TARBALL), key(MINISIG)])
        self.assertEqual(self.r2.objects[key(layout.SUMS)], sums, "SHA256SUMS is never rewritten")

    def test_refused_before_anything_is_sent(self):
        cases = {
            "no signature": lambda: Path(str(self.tarball) + ".minisig").unlink(),
            "another file's signature": lambda: (self.tarball.write_bytes(self.tarball.read_bytes() + b"\0"),
                                                 self.write_receipt(self.tarball)),
            "no receipt": lambda: self.tarball.with_name(TARBALL + ".builder-paths.json").unlink(),
            "an untrusted manifest key": lambda: setattr(self, "tarball", self.untrusted()),
        }
        for label, change in cases.items():
            with self.subTest(label):
                self.tarball = self.make_tarball(self.signer, "dist-" + label.replace(" ", "-")) \
                    if label != "no signature" else self.tarball
                change()
                self.r2.log.clear()
                self.assertEqual(self.run_platforms(["linux-x64"])[0], 1)
                self.assertEqual(self.puts(), [])
                self.assertIn(REASONS[label], self.errors)

    def untrusted(self):
        return self.make_tarball(Signer(), "dist-untrusted")


if __name__ == "__main__":
    unittest.main()
