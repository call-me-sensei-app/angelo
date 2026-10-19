# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The feed's deltas and automatic-install switch, end to end through the release tools.

update_feed accepts and refuses delta entries and ``automatic_install``; sign_update_feed.py writes them
(``--delta``, ``--manual-install``) only for a delta named for its versions; publish_installers.py publishes a
delta in the same run as its installer, listed in SHA256SUMS, and verifies it from the public address (the
loopback stand-in for R2 of test_native_publish_installers). A feed with deltas stays readable by a build that
predates them: it ignores keys it does not know inside a download.
"""

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(ROOT / "scripts/angelo"))
import update_fixtures as ufx  # noqa: E402
from angelo_agent import update_delta, update_feed  # noqa: E402
from test_native_publish_installers import Case  # noqa: E402
from test_native_update_delta import make_app  # noqa: E402
import make_update_delta  # noqa: E402
import release_downloads as layout  # noqa: E402
import sign_update_feed  # noqa: E402

NOW = datetime(2026, 10, 25, tzinfo=timezone.utc)
DELTA_URL = "https://downloads.angelo.studio/1.1.0/Angelo-1.0.0-to-1.1.0-macos-arm64.delta.tar.xz"


def delta_entry(**changes):
    return dict({"from": "1.0.0", "url": DELTA_URL, "sha256": "c" * 64, "size": 40_000_000,
                 "format": update_delta.FORMAT}, **changes)


class FeedTests(unittest.TestCase):
    def setUp(self):
        self.seed, self.keys = ufx.new_key()

    def feed(self, deltas=None, **latest):
        document = ufx.payload()
        if deltas is not None:
            document["latest"]["downloads"]["macos-arm64"]["deltas"] = deltas
        document["latest"].update(latest)
        return update_feed.open_feed(ufx.envelope(document, self.seed), self.keys, NOW)

    def test_deltas_are_read_and_chosen_by_the_running_version_and_format(self):
        download = self.feed([delta_entry(), delta_entry(**{"from": "1.0.1"})]).latest.download_for("macos-arm64")
        self.assertEqual([d.source for d in download.deltas], ["1.0.0", "1.0.1"])
        self.assertEqual(download.delta_from("1.0.1", {update_delta.FORMAT}).source, "1.0.1")
        self.assertIsNone(download.delta_from("1.0.1", {"another-format"}))
        self.assertIsNone(download.delta_from("0.9.0", {update_delta.FORMAT}))
        self.assertEqual(self.feed().latest.download_for("macos-arm64").deltas, ())

    def test_bad_deltas_refuse_the_whole_feed(self):
        bad = {"newer source": [delta_entry(**{"from": "1.1.0"})],
               "listed twice": [delta_entry(), delta_entry()],
               "another host": [delta_entry(url="https://example.com/x.tar.xz")],
               "no format": [delta_entry(format="")],
               "odd format": [delta_entry(format="../x")],
               "not a list": {"from": "1.0.0"},
               "too many": [delta_entry(**{"from": "0.0.%d" % i}) for i in range(update_feed.MAX_DELTAS + 1)],
               "bad digest": [delta_entry(sha256="xyz")]}
        for name, deltas in bad.items():
            with self.subTest(name):
                with self.assertRaises(update_feed.FeedError):
                    self.feed(deltas)

    def test_automatic_install_defaults_on_and_can_be_switched_off(self):
        self.assertTrue(self.feed().latest.automatic_install)
        self.assertFalse(self.feed(automatic_install=False).latest.automatic_install)
        with self.assertRaises(update_feed.FeedError):
            self.feed(automatic_install="no")


class FeedToolTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)

    def test_a_delta_is_named_for_its_versions_and_measured(self):
        path = self.folder / layout.delta_name("1.0.0", "1.1.0", "macos-arm64")
        path.write_bytes(b"delta bytes")
        platform, entry = sign_update_feed.delta("macos-arm64=1.0.0=%s" % path, "1.1.0")
        self.assertEqual((platform, entry["from"], entry["format"], entry["size"], entry["url"]),
                         ("macos-arm64", "1.0.0", update_delta.FORMAT, 11, DELTA_URL))
        self.assertEqual(entry["sha256"], hashlib.sha256(b"delta bytes").hexdigest())
        misnamed = self.folder / "delta.tar.xz"
        misnamed.write_bytes(b"x")
        for spec, version in (("macos-arm64=1.0.0=%s" % misnamed, "1.1.0"), ("windows-x64=1.0.0=%s" % path, "1.1.0"),
                              ("macos-arm64=1.2.0=%s" % path, "1.1.0"), ("macos-arm64=%s" % path, "1.1.0")):
            with self.subTest(spec=spec):
                with self.assertRaises((sign_update_feed.FeedToolError, layout.LayoutError)):
                    sign_update_feed.delta(spec, version)

    def test_a_delta_needs_its_platforms_installer_in_the_feed(self):
        with self.assertRaises(sign_update_feed.FeedToolError):
            sign_update_feed.with_deltas([("windows-x64", {"url": "u"})], [("macos-arm64", delta_entry())])
        merged = sign_update_feed.with_deltas([("macos-arm64", {"url": "u"})], [("macos-arm64", delta_entry())])
        self.assertEqual(merged[0][1]["deltas"][0]["from"], "1.0.0")

    def test_the_payload_carries_the_manual_install_switch(self):
        changelog = self.folder / "CHANGELOG.md"
        changelog.write_text((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), encoding="utf-8")
        from angelo_agent import app_version
        import changelog as changelog_tool
        releases = changelog_tool.load(changelog)
        dated = next((r for r in releases if r["date"]), None)
        if dated is None:   # The source's newest entry is still Unreleased: date it in the copy, as a release does.
            text = changelog.read_text(encoding="utf-8")
            version = releases[0]["version"]
            changelog.write_text(text.replace("## %s — Unreleased" % version, "## %s — 2026-10-20" % version, 1),
                                 encoding="utf-8")
            dated = next(r for r in changelog_tool.load(changelog) if r["date"])
        self.assertIsNotNone(app_version.parse(dated["version"]))
        downloads = [("macos-arm64", {"url": layout.installer_url(dated["version"], "macos-arm64"),
                                      "sha256": "a" * 64, "size": 10})]
        payload = json.loads(sign_update_feed.build_payload(dated["version"], 5, downloads, source=changelog,
                                                            automatic_install=False, now=NOW))
        self.assertIs(payload["latest"]["automatic_install"], False)
        automatic = json.loads(sign_update_feed.build_payload(dated["version"], 5, downloads, source=changelog, now=NOW))
        self.assertNotIn("automatic_install", automatic["latest"], "the default is left out")


class PublishDeltaTests(Case):
    def setUp(self):
        super().setUp()
        old = make_app(self.folder / "old", "1.0.0")
        new = make_app(self.folder / "new", "1.0.1", {"Contents/Resources/scripts/added.py": b"print('new')\n"})
        self.delta, _ = make_update_delta.make("macos-arm64", "1.0.0", old, "1.0.1", new, self.folder / "deltas")
        self.dmg = self.dmg.rename(self.folder / "Angelo-1.0.1-macos-arm64.dmg")
        self.msi = self.msi.rename(self.folder / "Angelo-1.0.1-windows-x64.msi")
        self.write_receipt(self.msi)  # the Windows stage's builder-path receipt for this MSI

    def publish(self, *extra, **options):
        return self.run_tool("--version", "1.0.1", "--dmg", str(self.dmg), "--msi", str(self.msi), *extra, **options)

    def test_a_delta_goes_up_with_its_installers_and_is_verified(self):
        code, out = self.publish("--delta", str(self.delta))
        self.assertEqual(code, 0, self.errors)
        result = json.loads(out)
        self.assertIn("1.0.1/" + self.delta.name, self.puts())
        self.assertEqual(self.puts()[-1], "1.0.1/SHA256SUMS", "the sums stay last")
        self.assertIn(self.delta.name, self.r2.objects["1.0.1/SHA256SUMS"][0].decode())
        self.assertEqual(result["feed_deltas"], ["macos-arm64=1.0.0=%s" % self.delta])
        self.assertEqual(set(result["website_sha256"]), {"macos", "windows"}, "deltas are not website downloads")
        self.assertEqual(self.r2.objects["1.0.1/" + self.delta.name][1]["Content-Type"], "application/x-xz")

    def test_a_delta_that_is_not_this_versions_is_refused_before_anything_is_sent(self):
        other = self.folder / "Angelo-1.0.0-to-1.0.2-macos-arm64.delta.tar.xz"
        shutil.copyfile(self.delta, other)
        renamed = self.folder / "Angelo-0.9.0-to-1.0.1-macos-arm64.delta.tar.xz"
        shutil.copyfile(self.delta, renamed)   # The right name for 1.0.1, but its description says 1.0.0.
        garbage = self.folder / "Angelo-1.0.0-to-1.0.1-macos-arm64.delta.tar.xz"
        garbage.write_bytes(b"not a delta")
        for path in (other, renamed, garbage):
            with self.subTest(path=path.name):
                code, _ = self.publish("--delta", str(path))
                self.assertEqual(code, 1)
                self.assertEqual(self.puts(), [])

    def test_a_delta_without_its_platforms_installer_is_refused(self):
        code, _ = self.run_tool("--version", "1.0.1", "--msi", str(self.msi), "--delta", str(self.delta))
        self.assertEqual(code, 1)
        self.assertIn("same run", self.errors)


if __name__ == "__main__":
    unittest.main()
