# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The signed update feed (update_feed.py): signature, rollback, expiry, strict fields, the offer.

Every feed here is signed by a throwaway in-memory key (update_fixtures.py).
The cases that matter most are the refusals: a feed that is not exactly what
the release manager signed, points anywhere but Angelo's hosts, or is older
than one already accepted must never become an offer.
"""

import base64
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import update_fixtures as fx  # noqa: E402
from angelo_agent import app_version, ed25519, update_feed  # noqa: E402
from angelo_agent.update_feed import FeedError  # noqa: E402

NOW = datetime(2026, 10, 25, tzinfo=timezone.utc)


class FeedCase(unittest.TestCase):
    def setUp(self):
        self.seed, self.keys = fx.new_key()

    def open(self, document=None, **kwargs):
        raw = kwargs.pop("raw", None) or fx.envelope(document or fx.payload(), self.seed)
        return update_feed.open_feed(raw, kwargs.pop("keys", self.keys), NOW, **kwargs)

    def refused(self, code, **kwargs):
        with self.assertRaises(FeedError) as caught:
            self.open(**kwargs)
        self.assertEqual(caught.exception.code, code, caught.exception)

    def bad_payload(self, code, **changes):
        self.refused(code, document=fx.payload(**changes))

    def bad_latest(self, code, **changes):
        document = fx.payload()
        document["latest"].update(changes)
        self.refused(code, document=document)


class SignatureTests(FeedCase):
    def test_a_correctly_signed_feed_opens(self):
        feed = self.open()
        self.assertEqual((feed.latest.version, feed.sequence, feed.channel), ("1.1.0", 3, "stable"))
        self.assertEqual(feed.latest.highlights[0], "Edits finish in fewer steps")
        self.assertEqual(feed.latest.download_for("macos-arm64").size, 412_000_000)
        self.assertIsNone(feed.latest.download_for("linux-x64"))

    def test_no_configured_key_fails_closed(self):
        self.refused("no_keys", keys={})

    def test_an_unknown_key_id_bad_signature_or_wrong_signer_is_refused(self):
        self.refused("unknown_key", raw=fx.envelope(fx.payload(), self.seed, key_id="someone-else"))
        other_seed, _ = fx.new_key()
        self.refused("bad_signature", raw=fx.envelope(fx.payload(), other_seed))

    def test_a_changed_payload_or_signature_is_refused(self):
        raw = json.loads(fx.envelope(fx.payload(), self.seed))
        tampered = json.dumps(fx.payload("9.9.9")).encode()
        raw["payload"] = base64.b64encode(tampered).decode()
        self.refused("bad_signature", raw=json.dumps(raw).encode())
        raw = json.loads(fx.envelope(fx.payload(), self.seed))
        signature = bytearray(base64.b64decode(raw["signature"]))
        signature[5] ^= 1
        raw["signature"] = base64.b64encode(bytes(signature)).decode()
        self.refused("bad_signature", raw=json.dumps(raw).encode())

    def test_the_signing_context_separates_this_key_from_other_uses(self):
        self.refused("bad_signature", raw=fx.envelope(fx.payload(), self.seed, context=b""))
        self.refused("bad_signature", raw=fx.envelope(fx.payload(), self.seed, context=b"angelo-crash-report-v1\n"))

    def test_malformed_envelopes_are_refused(self):
        good = json.loads(fx.envelope(fx.payload(), self.seed))
        for raw in (b"", b"not json", b"[]", b'{"format": "other"}', b"x" * (update_feed.MAX_ENVELOPE + 1)):
            with self.subTest(raw=raw[:20]):
                with self.assertRaises(FeedError):
                    update_feed.open_feed(raw, self.keys, NOW)
        for field, value in (("version", 2), ("format", "feed"), ("payload", "***"), ("signature", 5), ("payload", None)):
            with self.subTest(field=field):
                with self.assertRaises(FeedError):
                    update_feed.open_feed(json.dumps(dict(good, **{field: value})).encode(), self.keys, NOW)
        big = fx.envelope(fx.payload(), self.seed, payload_bytes=b'{"x": "' + b"a" * 40_000 + b'"}')
        self.refused("too_large", raw=big)

    def test_the_shipped_key_file_parses_and_revoked_or_broken_entries_are_dropped(self):
        shipped = update_feed.load_keys()
        self.assertIsInstance(shipped, dict)
        public = base64.b64encode(ed25519.public_key(bytes(32))).decode()
        document = {"keys": [{"id": "ok", "public_key": public}, {"id": "gone", "public_key": public, "status": "revoked"},
                             {"id": "short", "public_key": "AAAA"}, {"id": "junk", "public_key": "***"}, 5, {}]}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "keys.json"
            path.write_text(json.dumps(document))
            self.assertEqual(list(update_feed.load_keys(path)), ["ok"])
            path.write_text("not json")
            self.assertEqual(update_feed.load_keys(path), {})
        self.assertEqual(update_feed.load_keys(Path(folder) / "missing.json"), {})


class PayloadTests(FeedCase):
    def test_rollback_and_expiry(self):
        self.refused("rollback", last_sequence=4)
        self.assertEqual(self.open(last_sequence=3).sequence, 3)
        self.bad_payload("expired", expires="2026-10-24T23:59:59Z")
        self.assertEqual(self.open(document=fx.payload(expires="")).expires, "")

    def test_schema_channel_sequence_and_stamps(self):
        self.bad_payload("malformed", schema=2)
        self.bad_payload("invalid", channel="beta")
        for sequence in (0, -1, "3", True, 1.5, None):
            with self.subTest(sequence=sequence):
                self.bad_payload("invalid", sequence=sequence)
        self.bad_payload("invalid", issued="yesterday")
        self.bad_payload("invalid", issued="2026-10-20 09:00:00")
        self.bad_payload("invalid", minimum_supported="1.0")

    def test_every_link_is_https_on_an_angelo_or_github_host(self):
        for url in ("http://angelo.studio/releases", "https://evil.example/releases", "https://angelo.studio.evil.example/x",
                    "https://angelo.studio@evil.example/x", "https://evil.example/angelo.studio", "javascript:alert(1)",
                    "https://angelo.studio:8443/x", "https://angelo.studio/a b", "//angelo.studio/x", "",
                    "https://notangelo.studio/x"):
            with self.subTest(url=url):
                self.bad_latest("invalid", notes_url=url)
        document = fx.payload()
        document["latest"]["downloads"]["macos-arm64"]["url"] = "https://downloads.evil.example/Angelo.dmg"
        self.refused("invalid", document=document)
        self.assertTrue(self.open(document=fx.payload(
            latest=dict(fx.payload()["latest"], notes_url="https://www.angelo.studio/releases#1.1.0"))).latest.notes_url)
        self.assertTrue(self.open(document=fx.payload(
            latest=dict(fx.payload()["latest"], notes_url="https://github.com/org/angelo/releases/tag/v1.1.0"))).latest.notes_url)
        for url in ("https://gist.github.com/x", "https://evil.github.com/x", "https://github.com.evil.example/x"):
            with self.subTest(url=url):   # github.com itself only, unlike angelo.studio and its subdomains.
                self.bad_latest("invalid", notes_url=url)

    def test_the_crash_report_service_must_be_an_angelo_service(self):
        self.assertEqual(self.open(document=fx.payload(services={"crash_reports": "https://api.angelo.studio/v1/crash-reports"})
                                   ).crash_reports_url, "https://api.angelo.studio/v1/crash-reports")
        self.assertEqual(self.open().crash_reports_url, "")
        for url in ("https://github.com/x/y", "http://api.angelo.studio/v1", "https://evil.example/v1"):
            with self.subTest(url=url):
                self.bad_payload("invalid", services={"crash_reports": url})
        self.bad_payload("invalid", services=[])

    def test_text_and_list_bounds(self):
        self.bad_latest("invalid", version="1.1")
        self.bad_latest("invalid", version="v1.1.0")
        self.bad_latest("invalid", title="")
        self.bad_latest("invalid", title="x" * 81)
        self.bad_latest("invalid", summary="x" * 301)
        self.bad_latest("invalid", highlights=["fine"] * 6)
        self.bad_latest("invalid", highlights=["bell \x07"])
        self.bad_latest("invalid", highlights=["right-to-left ‮ override"])
        self.bad_latest("invalid", highlights="a string")
        self.bad_latest("invalid", critical="yes")
        self.bad_latest("invalid", released="soon")
        self.bad_payload("invalid", latest=[])

    def test_downloads_are_strictly_described(self):
        for platform, change in (("macos-arm64", {"sha256": "A" * 64}), ("macos-arm64", {"sha256": "a" * 63}),
                                 ("macos-arm64", {"size": 0}), ("macos-arm64", {"size": True}),
                                 ("macos-arm64", {"size": 9 * 1024 ** 3}), ("macos-arm64", {"url": None})):
            with self.subTest(change=change):
                document = fx.payload()
                document["latest"]["downloads"][platform].update(change)
                self.refused("invalid", document=document)
        document = fx.payload()
        document["latest"]["downloads"]["amiga-m68k"] = document["latest"]["downloads"]["macos-arm64"]
        self.refused("invalid", document=document)
        document = fx.payload()
        document["latest"]["downloads"] = []
        self.refused("invalid", document=document)


class OfferTests(FeedCase):
    def offer(self, current, platform="macos-arm64", **changes):
        document = fx.payload(**changes)
        return update_feed.offer_for(self.open(document=document), current, platform)

    def test_a_newer_release_is_offered_with_this_platforms_download(self):
        offer = self.offer("1.0.0")
        self.assertEqual(offer.release.version, "1.1.0")
        self.assertIn("macos-arm64.dmg", offer.download.url)
        self.assertFalse(offer.critical or offer.unsupported)
        self.assertIn("windows-x64.msi", self.offer("1.0.0", "windows-x64").download.url)

    def test_nothing_is_offered_when_current_or_newer(self):
        self.assertIsNone(self.offer("1.1.0"))
        self.assertIsNone(self.offer("1.2.0"))
        self.assertIsNone(self.offer("2.0.0"))
        self.assertIsNone(update_feed.offer_for(self.open(), "not-a-version", "macos-arm64"))

    def test_a_platform_without_a_build_is_still_told_and_sent_to_the_notes(self):
        offer = self.offer("1.0.0", "linux-x64")
        self.assertIsNone(offer.download)
        self.assertTrue(offer.release.notes_url.startswith("https://angelo.studio/releases"))

    def test_critical_and_unsupported_versions_are_marked(self):
        document = fx.payload()
        document["latest"]["critical"] = True
        self.assertTrue(update_feed.offer_for(self.open(document=document), "1.0.0", "macos-arm64").critical)
        offer = self.offer("1.0.0", minimum_supported="1.0.1", sequence=4)
        self.assertTrue(offer.unsupported and offer.critical)

    def test_platform_keys(self):
        for system, machine, key in (("darwin", "arm64", "macos-arm64"), ("darwin", "x86_64", "macos-x64"),
                                     ("win32", "AMD64", "windows-x64"), ("win32", "ARM64", "windows-arm64"),
                                     ("linux", "x86_64", "linux-x64"), ("linux", "armv7l", ""), ("darwin", "ppc", "")):
            with self.subTest(system=system, machine=machine):
                self.assertEqual(update_feed.platform_key(system, machine), key)


class VersionTests(unittest.TestCase):
    def test_parse_and_display(self):
        self.assertEqual(app_version.parse("1.10.2"), (1, 10, 2))
        for bad in ("1.0", "1.0.0.0", "v1.0.0", "1.0.0-rc1", "", None, 5, "1.a.0", "1000.0.0"):
            self.assertIsNone(app_version.parse(bad), bad)
        self.assertEqual((app_version.display("1.0.0"), app_version.display("1.1.0"), app_version.display("1.1.2")),
                         ("1.0", "1.1", "1.1.2"))
        self.assertLess(app_version.parse("1.9.0"), app_version.parse("1.10.0"))

    def test_the_fallback_version_equals_the_identitys_when_that_module_exists(self):
        root = Path(__file__).resolve().parents[3]
        identity = root / "scripts/modules/angelo_identity.json"
        if identity.exists():  # On branches that carry the application identity.
            self.assertEqual(json.loads(identity.read_text())["version"]["label"], app_version.FALLBACK_VERSION)
            for key, url in app_version.FALLBACK_LINKS.items():
                self.assertEqual(json.loads(identity.read_text())["urls"][key], url)
        self.assertIsNotNone(app_version.parse(app_version.FALLBACK_VERSION))


if __name__ == "__main__":
    unittest.main()
