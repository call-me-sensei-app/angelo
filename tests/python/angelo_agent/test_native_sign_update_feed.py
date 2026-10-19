# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The update feed tool (scripts/angelo/sign_update_feed.py): build, sign, verify, keys.

Every key here is a throwaway made at test time. Covered: the payload comes from CHANGELOG.md and is
checked with the app's own parser (an Unreleased entry, an unknown version, a sequence that does not
grow and a download outside the allowed hosts are refused); a local PEM key signs, and a signature that
does not verify writes nothing; the Cloud KMS path calls ``gcloud`` with the key version's parts and reads
the public key from its PEM; ``verify`` fails closed without a trusted key; the shipped key list is
well formed; ``add-key`` turns an exported public PEM into a trusted entry (and refuses a private key,
a reused id and a duplicate key); and a rehearsal trusts a throwaway key, signs a feed announcing the
crash endpoint, and the app's own parser refuses every tampered, re-keyed, truncated or replayed copy.
"""

import base64
import hashlib
import importlib.util
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import ed25519, update_feed  # noqa: E402

_spec = importlib.util.spec_from_file_location("sign_update_feed", ROOT / "scripts/angelo/sign_update_feed.py")
tool = importlib.util.module_from_spec(_spec)
sys.modules["sign_update_feed"] = tool
_spec.loader.exec_module(tool)

NOW = datetime(2026, 10, 25, 9, 0, tzinfo=timezone.utc)
KMS = "projects/example-project/locations/global/keyRings/example-keyring/cryptoKeys/update-feed/cryptoKeyVersions/3"
CHANGELOG = """# Angelo release notes

## 1.0.1 — 2026-10-24
Based on Blender 5.2.2 LTS.

Fixes for the first week.

### Highlights
- Faster first launch
- Calmer error messages
- A fix for saving on Windows

## 1.0.0 — 2026-10-20
Based on Blender 5.2.2 LTS.

The first release.

### Highlights
- One
- Two
- Three
"""


class ToolCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.source = self.folder / "CHANGELOG.md"
        self.source.write_text(CHANGELOG, encoding="utf-8")
        self.key = self.folder / "rehearsal.pem"
        with redirect_stderr(io.StringIO()):
            self.assertEqual(tool.main(["keygen", "--out", str(self.key)], out=io.StringIO()), 0)
        self.seed = tool.read_key_file(self.key)

    def payload(self, version="1.0.1", sequence=2, **kwargs):
        kwargs.setdefault("downloads", [("macos-arm64", {"url": "https://angelo.studio/downloads/Angelo-1.0.1-macos-arm64.dmg",
                                                         "sha256": "c" * 64, "size": 400_000_000})])
        downloads = kwargs.pop("downloads")
        return tool.build_payload(version, sequence, downloads, now=NOW, source=self.source, **kwargs)


class KeyTests(ToolCase):
    def test_keygen_writes_a_private_pem_once(self):
        if os.name != "nt":  # Windows has no mode bits.
            self.assertEqual(stat.S_IMODE(os.stat(self.key).st_mode), 0o600)
        self.assertTrue(self.key.read_text().startswith("-----BEGIN PRIVATE KEY-----"))
        self.assertEqual(len(self.seed), 32)
        with redirect_stderr(io.StringIO()):
            self.assertEqual(tool.main(["keygen", "--out", str(self.key)]), 1, "a key file is never overwritten")
        self.assertEqual(tool.read_key_file(self.key), self.seed)

    def test_public_key_prints_the_update_keys_entry(self):
        out = io.StringIO()
        self.assertEqual(tool.main(["public-key", "--key-id", "2026-10", "--key-file", str(self.key)], out=out), 0)
        entry = json.loads(out.getvalue())
        self.assertEqual(entry, {"id": "2026-10", "public_key": base64.b64encode(ed25519.public_key(self.seed)).decode(),
                                 "status": "active"})

    def test_a_key_that_is_not_ed25519_is_refused(self):
        bad = self.folder / "rsa.pem"
        bad.write_text("-----BEGIN PRIVATE KEY-----\n%s\n-----END PRIVATE KEY-----\n" % base64.b64encode(b"x" * 48).decode())
        with self.assertRaises(tool.FeedToolError):
            tool.read_key_file(bad)

    def test_the_shipped_key_list_is_well_formed(self):
        document = json.loads(update_feed.KEYS_FILE.read_text(encoding="utf-8"))
        for entry in document["keys"]:
            self.assertEqual(len(base64.b64decode(entry["public_key"])), 32)
            self.assertIn(entry.get("status", "active"), {"active", "revoked"})
        self.assertEqual(len(update_feed.load_keys()), sum(1 for e in document["keys"] if e.get("status") != "revoked"))


class BuildTests(ToolCase):
    def test_the_payload_comes_from_the_changelog_and_passes_the_apps_parser(self):
        document = json.loads(self.payload(crash_reports="https://console.angelo.studio/api/v1/crash-reports"))
        latest = document["latest"]
        self.assertEqual((latest["title"], latest["released"], latest["notes_url"]),
                         ("Angelo 1.0.1", "2026-10-24", "https://angelo.studio/releases#1.0.1"))
        self.assertEqual(latest["highlights"], ["Faster first launch", "Calmer error messages", "A fix for saving on Windows"])
        self.assertEqual((document["issued"], document["expires"]), ("2026-10-25T09:00:00Z", "2027-04-23T09:00:00Z"))
        self.assertEqual(document["services"], {"crash_reports": "https://console.angelo.studio/api/v1/crash-reports"})
        self.assertNotIn("services", json.loads(self.payload()), "no endpoint unless one is announced")

    def test_refusals(self):
        refusals = {
            "Unreleased": lambda: tool.build_payload("1.0.1", 2, (), now=NOW, source=self._unreleased()),
            "no entry": lambda: self.payload("1.0.9"),
            "greater than": lambda: self.payload(sequence=2, previous=2),
            "refuse": lambda: self.payload(downloads=[("macos-arm64", {"url": "https://example.com/a.dmg",
                                                                        "sha256": "c" * 64, "size": 1})]),
        }
        for fragment, call in refusals.items():
            with self.subTest(fragment=fragment), self.assertRaises(tool.FeedToolError) as caught:
                call()
            self.assertIn(fragment, str(caught.exception))
        with self.assertRaises(tool.FeedToolError):
            self.payload(crash_reports="http://console.angelo.studio/api/v1/crash-reports")

    def _unreleased(self):
        path = self.folder / "unreleased.md"
        path.write_text(CHANGELOG.replace("1.0.1 — 2026-10-24", "1.0.1 — Unreleased"), encoding="utf-8")
        return path

    def test_a_download_is_measured_from_the_file(self):
        installer = self.folder / "Angelo-1.0.1-windows-x64.msi"
        installer.write_bytes(b"installer bytes")
        platform, entry = tool.download("windows-x64=https://angelo.studio/downloads/x.msi=%s" % installer)
        self.assertEqual((platform, entry["size"]), ("windows-x64", 15))
        self.assertEqual(entry["sha256"], hashlib.sha256(b"installer bytes").hexdigest())
        with self.assertRaises(tool.FeedToolError):
            tool.download("windows-x64")


class SignTests(ToolCase):
    def test_a_local_key_signs_and_the_app_accepts_the_feed(self):
        raw = tool.sign_payload(self.payload(), "rehearsal", tool.LocalSigner(self.key))
        feed = update_feed.open_feed(raw, {"rehearsal": ed25519.public_key(self.seed)}, NOW)
        self.assertEqual((feed.sequence, feed.latest.version), (2, "1.0.1"))
        with self.assertRaises(update_feed.FeedError):
            update_feed.open_feed(raw, {"rehearsal": ed25519.public_key(os.urandom(32))}, NOW)

    def test_a_signature_that_does_not_verify_writes_nothing(self):
        wrong = SimpleNamespace(sign=lambda message: ed25519.sign(os.urandom(32), message),
                                public_key=lambda: ed25519.public_key(self.seed))
        with self.assertRaises(tool.FeedToolError):
            tool.sign_payload(self.payload(), "rehearsal", wrong)

    def test_cloud_kms_is_called_with_the_key_versions_parts(self):
        calls = []
        public_pem = "-----BEGIN PUBLIC KEY-----\n%s\n-----END PUBLIC KEY-----\n" % base64.b64encode(
            tool.SPKI_PREFIX + ed25519.public_key(self.seed)).decode()

        def gcloud(argv, capture_output, text):
            calls.append(argv)
            flags = dict(zip(argv, argv[1:]))
            if "asymmetric-sign" in argv:
                message = Path(flags["--input-file"]).read_bytes()
                Path(flags["--signature-file"]).write_bytes(ed25519.sign(self.seed, message))
            else:
                Path(flags["--output-file"]).write_text(public_pem)
            return SimpleNamespace(returncode=0, stderr="")

        raw = tool.sign_payload(self.payload(), "2026-10", tool.KmsSigner(KMS, run=gcloud))
        update_feed.open_feed(raw, {"2026-10": ed25519.public_key(self.seed)}, NOW)
        sign = next(argv for argv in calls if "asymmetric-sign" in argv)
        self.assertEqual(sign[:3], ["gcloud", "kms", "asymmetric-sign"])
        for flag, value in (("--version", "3"), ("--key", "update-feed"), ("--keyring", "example-keyring"),
                            ("--location", "global"), ("--project", "example-project")):
            self.assertEqual(sign[sign.index(flag) + 1], value)
        self.assertNotIn("--digest-algorithm", sign, "Ed25519 signs the message itself")
        self.assertIn(["gcloud", "kms", "keys", "versions", "get-public-key", "3"], [argv[:6] for argv in calls])

    def test_kms_refusals(self):
        with self.assertRaises(tool.FeedToolError):
            tool.KmsSigner("projects/example-project/keyRings/x")
        failing = tool.KmsSigner(KMS, run=lambda argv, **kwargs: SimpleNamespace(returncode=1, stderr="PERMISSION_DENIED"))
        with self.assertRaises(tool.FeedToolError) as caught:
            failing.sign(b"message")
        self.assertIn("PERMISSION_DENIED", str(caught.exception))
        with self.assertRaises(tool.FeedToolError):
            tool.public_from_pem("-----BEGIN PUBLIC KEY-----\n%s\n-----END PUBLIC KEY-----\n" % base64.b64encode(b"y" * 44).decode())


class CommandTests(ToolCase):
    def test_build_sign_verify_end_to_end_and_fail_closed_without_a_trusted_key(self):
        payload, feed = self.folder / "payload.json", self.folder / "stable.json"
        keys = self.folder / "keys.json"
        keys.write_text(json.dumps({"keys": [{"id": "rehearsal", "public_key": base64.b64encode(
            ed25519.public_key(self.seed)).decode()}]}))
        out = io.StringIO()
        self.assertEqual(tool.main(["sign", str(self._built(payload)), "--key-id", "rehearsal", "--key-file", str(self.key),
                                    "--out", str(feed)], out=out), 0)
        self.assertEqual(tool.main(["verify", str(feed), "--keys", str(keys)], out=out), 0)
        self.assertIn("valid: sequence 2, latest 1.0.1", out.getvalue())
        empty = self.folder / "empty.json"
        empty.write_text('{"keys": []}')
        with redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(tool.main(["verify", str(feed), "--keys", str(empty)]), 1)
        self.assertIn("No update signing key", errors.getvalue())
        with redirect_stderr(io.StringIO()):
            self.assertEqual(tool.main(["sign", str(payload), "--key-id", "x", "--out", str(feed)]), 1, "a signer is required")

    def _built(self, path):
        path.write_bytes(self.payload())
        return path


# The rehearsals add a throwaway key next to the shipped ones, under an id no release key uses.
REHEARSAL_ID = "rehearsal-01"


class AddKeyTests(ToolCase):
    """``add-key``: the reviewed change that makes builds trust the Cloud KMS key, from its exported PEM."""

    def setUp(self):
        super().setUp()
        self.keys = self.folder / "update_keys.json"
        shutil.copyfile(update_feed.KEYS_FILE, self.keys)
        self.public = ed25519.public_key(self.seed)
        self.pem = self.folder / "update-feed-public.pem"
        self.pem.write_text(tool.public_pem(self.public))

    def add(self, *extra):
        out = io.StringIO()
        with redirect_stderr(io.StringIO()) as errors:
            code = tool.main(["add-key", "--key-id", REHEARSAL_ID, "--pem", str(self.pem), "--keys", str(self.keys)]
                             + list(extra), out=out)
        return code, out.getvalue() + errors.getvalue()

    def test_the_exported_pem_becomes_an_active_key_the_app_loads(self):
        before = json.loads(self.keys.read_text())
        code, text = self.add()
        self.assertEqual(code, 0, text)
        self.assertIn("added " + REHEARSAL_ID, text)
        after = json.loads(self.keys.read_text())
        self.assertEqual(after["_about"], before["_about"], "the explanation is kept")
        self.assertEqual(after["keys"][-1], tool.key_entry(REHEARSAL_ID, self.public))
        self.assertEqual(update_feed.load_keys(self.keys),
                         {**update_feed.load_keys(update_feed.KEYS_FILE), REHEARSAL_ID: self.public})
        trusted = "already trusted %s: %s\n" % (REHEARSAL_ID, base64.b64encode(self.public).decode())
        self.assertEqual(self.add(), (0, trusted),
                         "running it again changes nothing")
        self.assertEqual(json.loads(self.keys.read_text()), after)

    def test_refusals(self):
        self.assertEqual(self.add()[0], 0)
        other = self.folder / "other.pem"
        other.write_text(tool.public_pem(ed25519.public_key(os.urandom(32))))
        with self.assertRaises(tool.FeedToolError):   # The id names another key.
            tool.add_key(self.keys, REHEARSAL_ID, tool.public_from_pem(other.read_text()))
        with self.assertRaises(tool.FeedToolError):   # The same key under a second id.
            tool.add_key(self.keys, "2026-11", self.public)
        with self.assertRaises(tool.FeedToolError):
            tool.add_key(self.keys, "bad id!", ed25519.public_key(os.urandom(32)))
        document = json.loads(self.keys.read_text())
        document["keys"][-1]["status"] = "revoked"
        self.keys.write_text(json.dumps(document))
        with self.assertRaises(tool.FeedToolError):   # A revoked id is never reused.
            tool.add_key(self.keys, REHEARSAL_ID, self.public)
        self.pem.write_text(self.key.read_text())
        code, text = self.add()
        self.assertEqual(code, 1)
        self.assertIn("private key", text, "a private key file is refused before anything is read from it")


class RehearsalTests(ToolCase):
    """The release procedure end to end with a throwaway key: trust it, build with a crash endpoint, sign,
    verify as the app does, and refuse every tampered or replayed feed."""

    def test_trust_build_sign_verify_and_refuse_tampering(self):
        keys = self.folder / "update_keys.json"
        shutil.copyfile(update_feed.KEYS_FILE, keys)
        pem = self.folder / "public.pem"
        with redirect_stderr(io.StringIO()):
            self.assertEqual(tool.main(["keygen", "--out", str(self.folder / "second.pem"), "--public-out", str(pem)],
                                       out=io.StringIO()), 0)
        seed = tool.read_key_file(self.folder / "second.pem")
        self.assertEqual(tool.public_from_pem(pem.read_text()), ed25519.public_key(seed))
        tool.add_key(keys, REHEARSAL_ID, tool.public_from_pem(pem.read_text()))
        crash = "https://console.angelo.studio/api/v1/crash-reports"
        payload = self.payload(crash_reports=crash)
        raw = tool.sign_payload(payload, REHEARSAL_ID, tool.LocalSigner(self.folder / "second.pem"))
        trusted = update_feed.load_keys(keys)
        feed = update_feed.open_feed(raw, trusted, NOW)
        self.assertEqual((feed.sequence, feed.latest.version, feed.crash_reports_url), (2, "1.0.1", crash))
        offer = update_feed.offer_for(feed, "1.0.0", "macos-arm64")
        self.assertEqual(offer.download.url, "https://angelo.studio/downloads/Angelo-1.0.1-macos-arm64.dmg")

        envelope = json.loads(raw)

        def tampered(**changes):
            copy = dict(envelope, **changes)
            return json.dumps(copy).encode()

        evil = payload.replace(b"angelo.studio/downloads", b"angelo.studio/elsewhere")
        flipped = bytearray(base64.b64decode(envelope["signature"]))
        flipped[0] ^= 1
        refusals = {
            "bad_signature": [tampered(payload=base64.b64encode(evil).decode()),
                              tampered(payload=base64.b64encode(payload.replace(crash.encode(),
                                       b"https://evil.angelo.studio/x")).decode()),
                              tampered(signature=base64.b64encode(bytes(flipped)).decode())],
            "unknown_key": [tampered(key_id="2026-11")],
            "malformed": [tampered(format="other"), raw[:-40]],
        }
        for code, feeds in refusals.items():
            for index, data in enumerate(feeds):
                with self.subTest(code=code, index=index), self.assertRaises(update_feed.FeedError) as caught:
                    update_feed.open_feed(data, trusted, NOW)
                self.assertEqual(caught.exception.code, code)
        with self.assertRaises(update_feed.FeedError) as caught:   # A replay of an older feed.
            update_feed.open_feed(raw, trusted, NOW, last_sequence=3)
        self.assertEqual(caught.exception.code, "rollback")
        with self.assertRaises(update_feed.FeedError) as caught:   # The shipped list without the key.
            update_feed.open_feed(raw, update_feed.load_keys(update_feed.KEYS_FILE), NOW)
        self.assertIn(caught.exception.code, {"no_keys", "unknown_key"})
        out = io.StringIO()
        feed_file = self.folder / "stable.json"
        feed_file.write_bytes(raw)
        self.assertEqual(tool.main(["verify", str(feed_file), "--keys", str(keys)], out=out), 0)
        self.assertIn("crash reports %s" % crash, out.getvalue())


if __name__ == "__main__":
    unittest.main()
