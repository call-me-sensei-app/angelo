# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Linux release signing: the key list, the signed file manifest (release_manifest.py) and the release tool
(scripts/angelo/sign_linux_release.py).

Every key is a throwaway made at test time, except the public half of a real minisign 0.12 signature kept as a
fixture. Covered: the shipped key list is well formed and fails closed while empty; ``add-key`` writes the entry and
the minisign public key, and refuses the update feed's key; a signed tree verifies, and each kind of change (content,
a file added or missing, an executable bit, a link's target, a link out of the tree, a writable or set-id file, a
special file, another version or folder, an untrusted or revoked key, an edited manifest, a feed-context signature)
is refused with its code; minisign signatures are minisign's default form (real minisign output verifies here, and
the KMS signer only ever signs 64-byte digests); a tarball is checked member by member without extracting it.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import base64
import hashlib
import importlib.util
import io
import json
import os
import shutil
import stat
import sys
import tarfile
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from host_test_policy import environment_skip

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(ROOT / "scripts/angelo"))
from angelo_agent import ed25519, release_manifest, update_feed  # noqa: E402

_spec = importlib.util.spec_from_file_location("sign_linux_release", ROOT / "scripts/angelo/sign_linux_release.py")
tool = importlib.util.module_from_spec(_spec)
sys.modules["sign_linux_release"] = tool
_spec.loader.exec_module(tool)

VERSION = "2.1.0"
ROOT_NAME = "Angelo-2.1.0-linux-x64"
# A real minisign 0.12 signature (default, prehashed form) of FIXTURE_TEXT, and its public key.
FIXTURE_TEXT = b"Angelo minisign fixture\n"
FIXTURE_PUBLIC = "RWSSaT3OfWyagG3ZBFzFOmTDx8deqW2nfXacqeoUCdLAmY1mgBHsmEKY"
FIXTURE_SIGNATURE = (
    "untrusted comment: signature from minisign secret key\n"
    "RUSSaT3OfWyagF2P6KXd0PZ6mLSiiduVyOEJ39py4ZpOrCXSmEcXFQiyA5ZZoLk7zF5GXP4HMGwxRZOFv+p4fCQFVeEdPGDLyQU=\n"
    "trusted comment: timestamp:1795100000\tfile:fixture.txt\thashed\n"
    "UC9duNSqUkGsZ6KFVBonJyzF+lRLcDyIZlYYvzAO0JGFmH4i88ZfgrB+WIqxZN8f8pQFk2NkQdXKoXqATcF9AA==\n")


class Signer:
    """A throwaway local key that records what it was asked to sign."""

    def __init__(self):
        self.seed, self.messages = os.urandom(32), []

    def public_key(self):
        return ed25519.public_key(self.seed)

    def sign(self, message):
        self.messages.append(message)
        return ed25519.sign(self.seed, message)


@unittest.skipUnless(os.name == "posix", environment_skip("Linux release folders: POSIX file modes, owners and links"))
class Case(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.signer = Signer()
        self.keys_path = self.folder / "linux_release_keys.json"
        shutil.copyfile(release_manifest.KEYS_FILE, self.keys_path)
        self.feed_keys = self.folder / "update_keys.json"
        self.feed_keys.write_text(json.dumps({"keys": []}), encoding="utf-8")
        tool.add_key("rehearsal-1", self.signer.public_key(), self.keys_path, self.folder / "pub", self.feed_keys)
        self.keys = {"rehearsal-1": self.signer.public_key()}

    def tree(self, parent=None):
        root = (parent or self.folder / "dist") / ROOT_NAME
        (root / "lib").mkdir(parents=True)
        (root / "empty").mkdir()
        addon = root / "5.3/scripts/addons_core/angelo_agent"
        addon.mkdir(parents=True)
        shutil.copyfile(self.keys_path, addon / "linux_release_keys.json")
        (root / "angelo").write_bytes(b"#!/bin/sh\nexec ./angelo-bin \"$@\"\n")
        os.chmod(root / "angelo", 0o755)
        (root / "lib/libx.so.1.2").write_bytes(os.urandom(4096))
        os.symlink("libx.so.1.2", root / "lib/libx.so.1")
        for path in [root] + list(root.rglob("*")):
            if not path.is_symlink():
                os.chmod(path, (os.stat(path).st_mode & 0o7777) & ~0o022)
        return root

    def signed_tree(self):
        root = self.tree()
        tool.write_manifest(root, VERSION, "rehearsal-1", self.signer, self.keys_path)
        return root

    def refused(self, root, code, keys=None, version=VERSION):
        with self.assertRaises(release_manifest.ManifestError) as caught:
            release_manifest.check_tree(root, version, self.keys if keys is None else keys)
        self.assertEqual(caught.exception.code, code, str(caught.exception))


class KeyListTests(Case):
    def test_shipped_key_list_is_well_formed_and_never_holds_the_feed_key(self):
        document = json.loads(release_manifest.KEYS_FILE.read_text(encoding="utf-8"))
        self.assertIsInstance(document["keys"], list)
        shipped = release_manifest.load_keys()
        for entry in document["keys"]:
            self.assertIn(entry["status"], ("active", "revoked"))
            self.assertTrue(tool.feed_tool.KEY_ID.fullmatch(entry["id"]))
        self.assertFalse(set(shipped.values()) & set(update_feed.load_keys().values()))
        for key_id, public in shipped.items():   # Each trusted key has its published minisign form.
            text = (tool.PUBLIC_KEYS_DIR / ("%s.pub" % key_id)).read_text(encoding="ascii")
            self.assertEqual(tool.read_minisign_public_key(text), public)

    def test_no_key_fails_closed(self):
        empty = self.folder / "empty.json"
        empty.write_text(json.dumps({"keys": []}), encoding="utf-8")
        root = self.signed_tree()
        self.refused(root, "no_keys", keys={})
        with redirect_stderr(io.StringIO()):
            self.assertEqual(tool.main(["check-keys", "--keys", str(empty)], out=io.StringIO()), 1)
        self.assertEqual(tool.main(["check-keys", "--keys", str(self.keys_path)], out=io.StringIO()), 0)

    def test_add_key_writes_the_minisign_public_key(self):
        text = (self.folder / "pub/rehearsal-1.pub").read_text(encoding="ascii")
        self.assertEqual(tool.read_minisign_public_key(text), self.signer.public_key())
        self.assertIn(tool.keynum_text(self.signer.public_key()), text)
        self.assertFalse(tool.add_key("rehearsal-1", self.signer.public_key(), self.keys_path, self.folder / "pub",
                                      self.feed_keys))   # Idempotent.

    def test_add_key_refuses_the_feed_key_a_reused_id_and_a_private_key(self):
        other = Signer()
        self.feed_keys.write_text(json.dumps({"keys": [{"id": "2026-10", "status": "active", "public_key":
                                                        base64.b64encode(other.public_key()).decode()}]}))
        with self.assertRaises(tool.ReleaseToolError):
            tool.add_key("linux-2", other.public_key(), self.keys_path, self.folder / "pub", self.feed_keys)
        with self.assertRaises(tool.ReleaseToolError):
            tool.add_key("rehearsal-1", Signer().public_key(), self.keys_path, self.folder / "pub", self.feed_keys)
        with self.assertRaises(tool.feed_tool.FeedToolError):
            tool.feed_tool.public_from_pem(tool.feed_tool.key_file_text(other.seed))

    def test_a_revoked_key_is_not_trusted(self):
        document = json.loads(self.keys_path.read_text(encoding="utf-8"))
        document["keys"][0]["status"] = "revoked"
        self.keys_path.write_text(json.dumps(document), encoding="utf-8")
        self.assertEqual(release_manifest.load_keys(self.keys_path), {})


class ManifestTests(Case):
    def test_a_signed_tree_verifies_and_the_kms_message_is_small(self):
        root = self.signed_tree()
        self.assertEqual(release_manifest.check_tree(root, VERSION, self.keys), "rehearsal-1")
        self.assertEqual(tool.check_tree(root, VERSION, self.keys_path), "rehearsal-1")
        self.assertTrue(all(len(message) <= 64 * 1024 for message in self.signer.messages))
        self.assertEqual(self.signer.messages[-1],
                         release_manifest.SIGNING_CONTEXT
                         + hashlib.sha512((root / release_manifest.MANIFEST_NAME).read_bytes()).digest())

    def test_every_change_is_refused(self):
        changes = {
            "changed": lambda r: (r / "lib/libx.so.1.2").write_bytes(os.urandom(4096)),
            # New entries get a release's modes, never the runner's umask (002 would make them bad_mode first).
            "unexpected": lambda r: ((r / "lib/extra.so").write_bytes(b"x"), os.chmod(r / "lib/extra.so", 0o644)),
            "missing": lambda r: (r / "angelo").unlink(),
            "exec": lambda r: os.chmod(r / "angelo", 0o644),
            "link": lambda r: ((r / "lib/libx.so.1").unlink(), os.symlink("../angelo", r / "lib/libx.so.1")),
            "empty_dir": lambda r: (r / "empty").rmdir(),
        }
        codes = {"exec": "changed", "link": "changed", "empty_dir": "missing"}
        for name, change in changes.items():
            with self.subTest(name):
                shutil.rmtree(self.folder / "dist", ignore_errors=True)
                root = self.signed_tree()
                change(root)
                self.refused(root, codes.get(name, name))

    def test_unsafe_trees_are_refused(self):
        cases = {
            "bad_link": lambda r: os.symlink("../../outside", r / "lib/out"),
            "absolute_link": lambda r: os.symlink("/etc/passwd", r / "lib/out"),
            "writable": lambda r: os.chmod(r / "angelo", 0o757),
            "setuid": lambda r: os.chmod(r / "angelo", 0o4755),
            "fifo": lambda r: (os.mkfifo(r / "lib/pipe"), os.chmod(r / "lib/pipe", 0o644)),
        }
        codes = {"bad_link": "bad_link", "absolute_link": "bad_link", "writable": "bad_mode", "setuid": "bad_mode",
                 "fifo": "bad_type"}
        for name, change in cases.items():
            with self.subTest(name):
                shutil.rmtree(self.folder / "dist", ignore_errors=True)
                root = self.signed_tree()
                change(root)
                self.refused(root, codes[name])
                with self.assertRaises(release_manifest.ManifestError):   # Never signed in the first place.
                    tool.write_manifest(root, VERSION, "rehearsal-1", self.signer, self.keys_path)

    def test_signature_and_release_identity(self):
        root = self.signed_tree()
        self.refused(root, "wrong_release", version="2.1.1")
        self.refused(root, "unknown_key", keys={"other": Signer().public_key()})
        self.refused(root, "unknown_key", keys={"rehearsal-2": self.signer.public_key()})
        manifest = root / release_manifest.MANIFEST_NAME
        original = manifest.read_bytes()
        manifest.write_bytes(original.replace(b'"exec": false', b'"exec": true', 1))
        self.refused(root, "bad_signature")
        manifest.write_bytes(original)
        # A signature by the right key over the update feed's context (another use of an Ed25519 key) is refused.
        signature = self.signer.sign(update_feed.SIGNING_CONTEXT + hashlib.sha512(original).digest())
        (root / release_manifest.SIGNATURE_NAME).write_bytes(release_manifest.signature_bytes("rehearsal-1", signature))
        self.refused(root, "bad_signature")
        renamed = root.parent / "Angelo-2.1.0-linux-x64-copy"
        root.rename(renamed)
        self.refused(renamed, "wrong_release")

    def test_a_signature_file_must_say_version_1_as_a_number(self):
        root = self.signed_tree()
        document = json.loads((root / release_manifest.SIGNATURE_NAME).read_text())
        for bad in (True, "1", 1.0):
            with self.subTest(version=bad):
                (root / release_manifest.SIGNATURE_NAME).write_text(json.dumps(dict(document, version=bad)))
                self.refused(root, "malformed")

    def test_an_unsigned_tree_and_a_linked_manifest_are_refused(self):
        root = self.tree()
        self.refused(root, "unsigned")
        root = self.folder / "dist" / ROOT_NAME
        tool.write_manifest(root, VERSION, "rehearsal-1", self.signer, self.keys_path)
        moved = self.folder / "elsewhere.json"
        (root / release_manifest.MANIFEST_NAME).rename(moved)
        os.symlink(moved, root / release_manifest.MANIFEST_NAME)
        self.refused(root, "unsigned")

    def test_manifest_entries_cannot_leave_the_tree(self):
        for path in ("../x", "/etc/passwd", "a/../../x", "a//b", "a\\b", "./a", "a\nb"):
            with self.subTest(path):
                with self.assertRaises(release_manifest.ManifestError):
                    release_manifest.check_path(path)
        base = {"format": release_manifest.FORMAT, "schema": 1, "version": VERSION, "platform": "linux-x64",
                "root": ROOT_NAME}
        bad = [
            [{"path": "a", "type": "file", "size": 1, "sha256": "0" * 64, "exec": False},
             {"path": "a/b", "type": "dir"}],                                         # Below a file.
            [{"path": "a", "type": "link", "target": "../../x"}],                       # Out of the tree.
            [{"path": "a", "type": "dir"}, {"path": "a", "type": "dir"}],               # Twice.
            [{"path": "a", "type": "file", "size": True, "sha256": "0" * 64, "exec": False}],
            [{"path": "a", "type": "device"}],
        ]
        for entries in bad:
            with self.subTest(entries):
                with self.assertRaises(release_manifest.ManifestError):
                    release_manifest.parse_manifest(json.dumps(dict(base, entries=entries)).encode(), VERSION)

    def test_signing_needs_a_trusted_key_and_the_key_list_in_the_tree(self):
        root = self.tree()
        with self.assertRaises(tool.ReleaseToolError):   # Not in the key list: a release that would never install.
            tool.write_manifest(root, VERSION, "rehearsal-1", Signer(), self.keys_path)
        (root / "5.3/scripts/addons_core/angelo_agent/linux_release_keys.json").unlink()
        with self.assertRaises(tool.ReleaseToolError):
            tool.write_manifest(root, VERSION, "rehearsal-1", self.signer, self.keys_path)
        self.assertFalse((root / release_manifest.MANIFEST_NAME).exists())


class SplitSigningTests(Case):
    """The kit's split: the manifest's bytes on the build machine, the signature where the key is."""

    def run_tool(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stderr(err):
            code = tool.main(list(argv), out=out)
        return code, out.getvalue().strip().splitlines()[-1] if out.getvalue().strip() else "", err.getvalue()

    def test_unsigned_bytes_signed_elsewhere_then_placed_verify(self):
        root, key = self.tree(), self.folder / "rehearsal.pem"
        key.write_text(tool.feed_tool.key_file_text(self.signer.seed))
        manifest = self.folder / "out" / "angelo-release-manifest.json"
        manifest.parent.mkdir()
        code, line, _ = self.run_tool("manifest", "--tree", str(root), "--version", VERSION,
                                      "--unsigned-out", str(manifest), "--keys", str(self.keys_path))
        digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
        self.assertEqual((code, line.split()[:2]), (0, ["MANIFEST", digest]))
        self.assertFalse((root / release_manifest.MANIFEST_NAME).exists(), "nothing written into the tree")
        code, line, _ = self.run_tool("sign-manifest", str(manifest), "--key-id", "rehearsal-1", "--key-file",
                                      str(key), "--keys", str(self.keys_path))
        self.assertEqual((code, line), (0, "SIGNED manifest %s key rehearsal-1" % digest))
        tool.place(root, manifest.read_bytes(), (manifest.parent / release_manifest.SIGNATURE_NAME).read_bytes())
        code, line, _ = self.run_tool("check-tree", "--tree", str(root), "--version", VERSION, "--keys",
                                      str(self.keys_path))
        self.assertEqual((code, line), (0, "PASS check-tree %s version %s manifest %s key rehearsal-1 keys %s"
                                        % (ROOT_NAME, VERSION, digest, tool.file_sha256(self.keys_path))))

    def test_the_split_refuses_what_it_cannot_vouch_for(self):
        root, key = self.tree(), self.folder / "rehearsal.pem"
        key.write_text(tool.feed_tool.key_file_text(self.signer.seed))
        inside = root / "lib" / "manifest.json"
        code, _, error = self.run_tool("manifest", "--tree", str(root), "--version", VERSION, "--unsigned-out",
                                       str(inside))
        self.assertEqual(code, 1)
        self.assertIn("outside the tree", error)
        code, _, error = self.run_tool("manifest", "--tree", str(root), "--version", VERSION, "--unsigned-out",
                                       str(self.folder / "m.json"), "--key-id", "rehearsal-1")
        self.assertIn("give no key", error)
        not_a_manifest = self.folder / "other.json"
        not_a_manifest.write_text('{"format": "something else"}')
        code, _, error = self.run_tool("sign-manifest", str(not_a_manifest), "--key-id", "rehearsal-1",
                                       "--key-file", str(key), "--keys", str(self.keys_path))
        self.assertEqual(code, 1)
        self.assertFalse((self.folder / release_manifest.SIGNATURE_NAME).exists())

    def test_the_rehearsal_passes_and_records_every_step(self):
        spec = importlib.util.spec_from_file_location("rehearse_linux_release",
                                                      ROOT / "scripts/angelo/rehearse_linux_release.py")
        rehearsal = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rehearsal)
        evidence = rehearsal.rehearse(self.folder / "evidence", "2.1.0")
        steps = json.loads(evidence.read_text())["steps"]
        self.assertTrue(all(step["ok"] == step["expected_ok"] for step in steps), steps)
        names = [step["step"] for step in steps]
        for required in ("sign-manifest", "check-tree", "verify-tarball", "refused: a changed file",
                         "refused: a top folder a group may write",
                         "refused: a tarball without its top folder's entry",
                         "refused: a changed byte in the tarball", "refused: another key signs"):
            self.assertIn(required, names)
        self.assertTrue(next(s for s in steps if s["step"] == "verify-tarball")["line"].startswith(
            "PASS verify-tarball Angelo-2.1.0-linux-x64.tar.xz sha256 "))


class MinisignTests(Case):
    def test_real_minisign_output_verifies(self):
        path = self.folder / "fixture.txt"
        path.write_bytes(FIXTURE_TEXT)
        raw = base64.b64decode(FIXTURE_PUBLIC)
        public = tool.read_minisign_public_key("untrusted comment: x\n%s\n" % FIXTURE_PUBLIC)
        tool.check_minisig(path, FIXTURE_SIGNATURE, public, raw[2:10])
        path.write_bytes(FIXTURE_TEXT + b"!")
        with self.assertRaises(release_manifest.ManifestError):
            tool.check_minisig(path, FIXTURE_SIGNATURE, public, raw[2:10])

    def test_signing_sends_kms_only_a_digest_and_verifies(self):
        path = self.folder / "Angelo-2.1.0-linux-x64.tar.xz"
        path.write_bytes(os.urandom(200 * 1024))
        text = tool.minisign_signature(path, "rehearsal-1", self.signer, now=1795100000)
        self.assertEqual([len(message) for message in self.signer.messages][:1], [64])
        self.assertEqual(base64.b64decode(text.splitlines()[1])[:2], b"ED")   # minisign's default, prehashed form.
        self.assertEqual(tool.verify_minisig(path, text, self.keys), "rehearsal-1")

    def test_tampering_is_refused(self):
        path = self.folder / "Angelo-2.1.0-linux-x64.tar.xz"
        path.write_bytes(os.urandom(4096))
        text = tool.minisign_signature(path, "rehearsal-1", self.signer)
        lines = text.split("\n")
        with self.assertRaises(release_manifest.ManifestError):   # An edited trusted comment.
            tool.verify_minisig(path, "\n".join(lines[:2] + [lines[2] + "x"] + lines[3:]), self.keys)
        with self.assertRaises(release_manifest.ManifestError):
            tool.verify_minisig(path, text, {"other": Signer().public_key()})
        other = self.folder / "Angelo-2.1.1-linux-x64.tar.xz"
        shutil.copyfile(path, other)
        with self.assertRaises(release_manifest.ManifestError):   # Same bytes, another name: the trusted comment says which.
            tool.verify_minisig(other, text, self.keys)
        path.write_bytes(path.read_bytes() + b"x")
        with self.assertRaises(release_manifest.ManifestError):
            tool.verify_minisig(path, text, self.keys)

    def test_the_legacy_form_is_refused(self):
        """Only minisign's prehashed form, the one the release makes (a legacy signature would read the whole file)."""
        path = self.folder / "SHA256SUMS"
        path.write_bytes(b"0" * 64 + b"  x\n")
        signature = self.signer.sign(path.read_bytes())
        comment = "timestamp:1\tfile:SHA256SUMS"
        text = "untrusted comment: x\n%s\ntrusted comment: %s\n%s\n" % (
            base64.b64encode(b"Ed" + tool.keynum(self.signer.public_key()) + signature).decode(), comment,
            base64.b64encode(self.signer.sign(signature + comment.encode())).decode())
        with self.assertRaises(release_manifest.ManifestError):
            tool.verify_minisig(path, text, self.keys)

    def test_the_name_checked_is_the_published_one(self):
        """The updater keeps the download under its own name: the trusted comment is checked against the name the
        feed publishes, given explicitly."""
        published = self.folder / "Angelo-2.1.0-linux-x64.tar.xz"
        published.write_bytes(os.urandom(2048))
        text = tool.minisign_signature(published, "rehearsal-1", self.signer)
        cached = self.folder / "update.tar.xz"
        shutil.copyfile(published, cached)
        self.assertEqual(tool.verify_minisig(cached, text, self.keys, published.name), "rehearsal-1")
        with self.assertRaises(release_manifest.ManifestError):
            tool.verify_minisig(cached, text, self.keys)
        with self.assertRaises(release_manifest.ManifestError):
            tool.verify_minisig(cached, text, self.keys, "Angelo-2.1.1-linux-x64.tar.xz")


class FeedTests(Case):
    def test_the_feed_names_the_linux_tarball_and_the_app_can_install_it(self):
        tarball = self.folder / "Angelo-2.1.0-linux-x64.tar.xz"
        tarball.write_bytes(os.urandom(4096))
        platform, entry = tool.feed_tool.download("linux-x64=%s" % tarball, "2.1.0")
        self.assertEqual(entry["url"], "https://downloads.angelo.studio/2.1.0/Angelo-2.1.0-linux-x64.tar.xz")
        self.assertEqual((platform, entry["size"]), ("linux-x64", 4096))
        self.assertTrue(update_feed.installable(entry["url"]))
        self.assertEqual(update_feed.platform_key("linux", "x86_64"), "linux-x64")
        with self.assertRaises(tool.feed_tool.FeedToolError):   # Only the published name.
            tool.feed_tool.download("linux-x64=%s" % (self.folder / "angelo.tar.xz"), "2.1.0")


class TarballTests(Case):
    def pack(self, root, extra=None):
        tarball = root.parent / (ROOT_NAME + ".tar.xz")
        with tarfile.open(tarball, "w:xz") as archive:
            archive.add(root, arcname=ROOT_NAME)
            if extra:
                extra(archive)
        (Path(str(tarball) + ".minisig")).write_text(tool.minisign_signature(tarball, "rehearsal-1", self.signer))
        return tarball

    def test_a_signed_tarball_verifies(self):
        tarball = self.pack(self.signed_tree())
        key_id, manifest_key, _, ratio = tool.verify_tarball(tarball, VERSION, self.keys_path)
        self.assertLess(ratio, tool.MAX_RATIO)
        self.assertEqual((key_id, manifest_key), ("rehearsal-1", "rehearsal-1"))

    def test_extra_members_and_hard_links_are_refused(self):
        def outside(archive):
            info = tarfile.TarInfo("other/file")
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))

        def hard_link(archive):   # Not listed in the manifest, and to a file outside the release.
            info = tarfile.TarInfo(ROOT_NAME + "/lib/hard")
            info.type, info.linkname = tarfile.LNKTYPE, "/etc/passwd"
            archive.addfile(info)

        for extra in (outside, hard_link):
            with self.subTest(extra.__name__):
                shutil.rmtree(self.folder / "dist", ignore_errors=True)
                tarball = self.pack(self.signed_tree(), extra)
                with self.assertRaises(release_manifest.ManifestError):
                    tool.check_tarball(tarball, VERSION, self.keys)

    def test_a_file_with_two_names_packed_as_a_hard_link_verifies(self):
        root = self.tree()
        os.link(root / "angelo", root / "angelo-again")   # tar packs the second name as a hard link member.
        tool.write_manifest(root, VERSION, "rehearsal-1", self.signer, self.keys_path)
        tarball = self.pack(root)
        with tarfile.open(tarball, "r:xz") as archive:
            self.assertTrue(any(member.islnk() for member in archive.getmembers()))
        self.assertEqual(tool.check_tarball(tarball, VERSION, self.keys), "rehearsal-1")

    def test_the_publish_gate_refuses_a_tarball_without_the_reviewed_list(self):
        root = self.signed_tree()
        rehearsal = json.loads(self.keys_path.read_text())
        rehearsal["keys"].append({"id": "rehearsal-2", "status": "active",
                                  "public_key": base64.b64encode(Signer().public_key()).decode()})
        other_list = self.folder / "rehearsal-list.json"
        other_list.write_text(json.dumps(rehearsal))
        # The tree (and its signed manifest) carry other_list; the reviewed list is self.keys_path.
        shutil.copyfile(other_list, root / "5.3" / tool.KEYS_IN_TREE)
        manifest, _ = tool.unsigned_manifest(root, VERSION, other_list)
        tool.place(root, manifest, tool.sign_manifest(manifest, "rehearsal-1", self.signer, other_list))
        tarball = self.pack(root)
        with self.assertRaises(tool.ReleaseToolError) as caught:
            tool.verify_tarball(tarball, VERSION, self.keys_path)
        self.assertIn("reviewed", str(caught.exception))
        self.assertEqual(tool.verify_tarball(tarball, VERSION, other_list)[:2], ("rehearsal-1", "rehearsal-1"))

    def test_a_top_folder_others_may_write_is_refused(self):
        """Whether an install updates itself is decided by its top folder's mode: the release gates check it."""
        root = self.tree()
        os.chmod(root, 0o775)
        with self.assertRaises(release_manifest.ManifestError) as caught:
            tool.write_manifest(root, VERSION, "rehearsal-1", self.signer, self.keys_path)
        self.assertEqual(caught.exception.code, "bad_mode")
        os.chmod(root, 0o755)
        tool.write_manifest(root, VERSION, "rehearsal-1", self.signer, self.keys_path)

        def group_writable_top(info):
            if info.name == ROOT_NAME:
                info.mode = 0o775
            return info
        tarball = root.parent / (ROOT_NAME + ".tar.xz")
        with tarfile.open(tarball, "w:xz") as archive:
            archive.add(root, arcname=ROOT_NAME, filter=group_writable_top)
        with self.assertRaises(release_manifest.ManifestError) as caught:
            tool.check_tarball(tarball, VERSION, self.keys)
        self.assertEqual(caught.exception.code, "bad_mode")

    def test_a_release_that_unpacks_past_the_updaters_stop_is_refused(self):
        root = self.tree()
        (root / "lib" / "padding.bin").write_bytes(bytes(4 * 1024 * 1024))   # Compresses about 1000 to 1.
        os.chmod(root / "lib" / "padding.bin", 0o644)
        tool.write_manifest(root, VERSION, "rehearsal-1", self.signer, self.keys_path)
        with self.assertRaises(tool.ReleaseToolError) as caught:
            tool.verify_tarball(self.pack(root), VERSION, self.keys_path)
        self.assertIn("unpacks to", str(caught.exception))

    def test_the_pass_lines_name_the_key_list_they_used(self):
        tarball = self.pack(self.signed_tree())
        keys_hash = tool.file_sha256(self.keys_path)
        for argv in (["check-keys", "--keys", str(self.keys_path)],
                     ["verify-tarball", str(tarball), "--version", VERSION, "--keys", str(self.keys_path)],
                     ["check-tree", "--tree", str(self.folder / "dist" / ROOT_NAME), "--version", VERSION,
                      "--keys", str(self.keys_path)]):
            with self.subTest(argv[0]):
                out = io.StringIO()
                with redirect_stderr(io.StringIO()):
                    self.assertEqual(tool.main(argv, out=out), 0)
                line = out.getvalue().strip().splitlines()[-1]
                self.assertTrue(line.startswith("PASS %s " % argv[0]), line)
                self.assertTrue(line.endswith(" keys %s" % keys_hash), line)

    def test_a_second_manifest_member_is_refused(self):
        root = self.signed_tree()

        def again(archive):
            archive.add(root / release_manifest.MANIFEST_NAME,
                        arcname="%s/%s" % (ROOT_NAME, release_manifest.MANIFEST_NAME))
        with self.assertRaises(release_manifest.ManifestError) as caught:
            tool.check_tarball(self.pack(root, again), VERSION, self.keys)
        self.assertEqual(caught.exception.code, "malformed")

    def test_an_unsigned_tarball_is_refused(self):
        tarball = self.pack(self.tree())
        with self.assertRaises(release_manifest.ManifestError) as caught:
            tool.check_tarball(tarball, VERSION, self.keys)
        self.assertEqual(caught.exception.code, "unsigned")


if __name__ == "__main__":
    unittest.main()
