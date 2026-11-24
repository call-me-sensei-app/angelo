# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Linux updater's trust rules, one test per class of failure the break-it review of a18946a6181 found
(release-2.1.0.noindex/review/break-it-linux-signing-a18946a6181.md):

* staging never depends on the process umask (022, 002 and 077 all stage the same release);
* a ready stage is used only under this build's key list as it is now and the feed's current digest (a revoked key,
  an emptied list or a respun tarball refuses it);
* the download's minisign signature is checked against the release keys before lzma or tarfile read a byte, and
  unpacking stops at a size limit;
* support() compares kinds as well as names, owns the folder (a team-shared install is not replaced for one person),
  and offers the download at once when the build trusts no release key;
* the update feed key and the Linux release key are kept apart in both lists, revoked entries included;
* a release must carry the reviewed key list, byte for byte;
* a manifest whose links leave the release through other links is refused.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import base64
import json
import os
import shutil
import sys
import tarfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from host_test_policy import environment_skip

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from test_native_update_linux import URL, Key, LinuxCase, release_tree, pack  # noqa: E402
from test_native_update_linux import linux, release_manifest, sign_linux_release, update_feed  # noqa: E402

feed_tool = sign_linux_release.feed_tool


class UmaskTests(LinuxCase):
    def test_every_umask_stages_the_same_release(self):
        tarball = self.download()
        for mask in (0o022, 0o002, 0o077):
            with self.subTest(umask=oct(mask)):
                work = self.folder / ("work-%o" % mask)
                work.mkdir()
                previous = os.umask(mask)
                try:
                    staged, key_id = self.stage(tarball, work)
                finally:
                    os.umask(previous)
                self.assertEqual(key_id, "test-1")
                self.assertEqual(os.stat(staged / "lib").st_mode & 0o777, 0o755)
                self.assertEqual(os.stat(staged / "angelo").st_mode & 0o777, 0o755)
                self.assertEqual(os.stat(staged / "lib/marker.txt").st_mode & 0o777, 0o644)


class RecallTests(LinuxCase):
    def prepared(self):
        root = self.installed()
        tarball = self.download()

        def fetch(expected, target, reserve=0, **options):
            shutil.copyfile(tarball, target)
            return Path(target)
        patcher = mock.patch.object(linux, "fetch_signature",
                                    lambda url, stream=None: Path(str(tarball) + ".minisig").read_text())
        patcher.start()
        self.addCleanup(patcher.stop)
        preparer = update_flow_preparer(self.folder, root, fetch)
        download = update_feed.Download(URL, "a" * 64, 1000)
        return preparer, preparer.prepare(SimpleNamespace(version="1.0.1"), download), download

    def set_keys(self, entries):
        release_manifest.KEYS_FILE.write_text(json.dumps({"keys": entries}))

    def test_a_stage_is_used_only_under_the_current_keys_and_digest(self):
        preparer, ready, download = self.prepared()
        self.assertEqual(preparer.recall("1.0.1", download), ready)
        self.assertIsNone(preparer.recall("1.0.1", update_feed.Download(URL, "c" * 64, 1000)), "a respun tarball")
        encoded = base64.b64encode(self.key.public).decode()
        for label, entries in (("revoked", [{"id": "test-1", "status": "revoked", "public_key": encoded}]),
                               ("empty", []),
                               ("another key", [{"id": "test-2", "status": "active",
                                                 "public_key": base64.b64encode(Key().public).decode()}])):
            with self.subTest(label):
                self.set_keys(entries)
                self.assertIsNone(preparer.recall("1.0.1", download))
                self.assertFalse(preparer.still_valid(ready))

    def test_a_ready_file_from_before_the_new_fields_is_prepared_again(self):
        preparer, ready, download = self.prepared()
        path = preparer.work("1.0.1") / "ready.json"
        document = json.loads(path.read_text())
        del document["download_sha256"], document["key_id"]
        path.write_text(json.dumps(document))
        self.assertIsNone(preparer.recall("1.0.1", download))


def update_flow_preparer(folder, root, fetch):
    from angelo_agent import update_flow
    return update_flow.Preparer(folder / "updates", "1.0.0", "linux-x64", str(root / "angelo"), fetch=fetch,
                                log=lambda *a: None, mirror="")


class SignatureFirstTests(LinuxCase):
    def work(self):
        work = self.folder / "work"
        work.mkdir(exist_ok=True)
        return work

    def test_nothing_is_decompressed_before_the_release_key_signed_the_bytes(self):
        tarball = self.download()
        other = Key()
        cases = {"no signature": None, "another key": sign_linux_release.minisign_signature(tarball, "test-1", other),
                 "legacy form": "untrusted comment: x\n%s\ntrusted comment: file:%s\n%s\n" % (
                     base64.b64encode(b"Ed" + release_manifest.keynum(self.key.public) + b"0" * 64).decode(),
                     tarball.name, base64.b64encode(b"0" * 64).decode())}
        for label, signature in cases.items():
            with self.subTest(label), mock.patch.object(tarfile, "open", side_effect=AssertionError("parsed")):
                with self.assertRaises(linux.StageError):
                    linux.stage(tarball, "1.0.1", self.work(), self.keys, signature=signature)
                self.assertFalse(linux.staged_path(self.work(), "1.0.1").parent.exists())

    def test_unpacking_stops_at_its_limit(self):
        with self.assertRaises(linux.StageError) as caught:
            self.stage(self.download(), self.work(), max_bytes=10)
        self.assertEqual(caught.exception.code, "too_large")
        self.assertFalse(linux.staged_path(self.work(), "1.0.1").parent.exists())

    def test_the_signature_is_fetched_small_and_strictly(self):
        class Body:
            def __init__(self, data):
                self.data = data

            def read(self, n):
                return self.data[:n]

            def close(self):
                pass
        self.assertEqual(linux.fetch_signature("u", stream=lambda *a, **k: (200, {}, Body(b"text"))), "text")
        for status, data, code in ((404, b"", "signature_http_404"),
                                   (200, b"x" * (release_manifest.MAX_MINISIG + 1), "bad_minisig")):
            with self.subTest(status=status):
                with self.assertRaises(linux.StageError) as caught:
                    linux.fetch_signature("u", stream=lambda *a, **k: (status, {}, Body(data)))
                self.assertEqual(caught.exception.code, code)


class SupportTests(LinuxCase):
    def test_a_released_file_or_link_replaced_by_a_folder_is_the_persons(self):
        for listed in ("lib/marker.txt", "lib/marker.link"):
            with self.subTest(listed):
                shutil.rmtree(self.folder / "Apps", ignore_errors=True)
                root = self.installed()
                (root / listed).unlink()
                (root / listed).mkdir()
                (root / listed / "my-work.blend").write_text("mine")
                self.assertEqual(linux.support(root / "angelo", "1.0.0")[0], "modified")
                self.assertIn(listed, linux.unlisted(root, "1.0.0"))

    def test_a_folder_opened_to_others_is_shared_and_someone_elses_is_permission(self):
        root = self.installed()
        os.chmod(root, 0o757)
        self.assertEqual(linux.support(root / "angelo", "1.0.0")[0], "shared", "writable by everyone")
        os.chmod(root, 0o775)
        with mock.patch.object(linux, "private_group", return_value=False):
            self.assertEqual(linux.support(root / "angelo", "1.0.0")[0], "shared", "a group of others")
        with mock.patch.object(linux, "private_group", return_value=True):
            self.assertEqual(linux.support(root / "angelo", "1.0.0")[0], "", "the person's own private group")
        os.chmod(root, 0o755)
        self.assertEqual(linux.support(root / "angelo", "1.0.0")[0], "")
        with mock.patch.object(linux.os, "geteuid", return_value=os.geteuid() + 1):
            self.assertEqual(linux.support(root / "angelo", "1.0.0")[0], "permission", "another person's folder")

    def test_a_private_group_is_proven_or_it_is_shared(self):
        user = SimpleNamespace(pw_name="ana", pw_uid=1000, pw_gid=1000)
        cases = {
            "private": (SimpleNamespace(gr_name="ana", gr_mem=[]), [user], True),
            "the user listed as its member": (SimpleNamespace(gr_name="ana", gr_mem=["ana"]), [user], True),
            "another account's primary group too": (SimpleNamespace(gr_name="ana", gr_mem=[]),
                                                    [user, SimpleNamespace(pw_name="bo", pw_uid=1001, pw_gid=1000)],
                                                    False),
            "named for a team": (SimpleNamespace(gr_name="artists", gr_mem=[]), [user], False),
            "a supplementary member": (SimpleNamespace(gr_name="ana", gr_mem=["ana", "bo"]), [user], False),
        }
        for label, (group, accounts, expected) in cases.items():
            with self.subTest(label):
                pwd = SimpleNamespace(getpwuid=lambda uid: user, getpwall=lambda accounts=accounts: accounts)
                grp = SimpleNamespace(getgrgid=lambda gid, group=group: group)
                self.assertEqual(linux.private_group(1000, 1000, grp, pwd), expected)
        failing = SimpleNamespace(getgrgid=mock.Mock(side_effect=KeyError(1000)))
        self.assertFalse(linux.private_group(1000, 1000, failing, SimpleNamespace(getpwuid=lambda uid: user,
                                                                                getpwall=lambda: [user])))

    def test_a_build_without_a_key_offers_the_download_at_once(self):
        root = self.installed()
        release_manifest.KEYS_FILE.write_text(json.dumps({"keys": []}))
        self.assertEqual(linux.support(root / "angelo", "1.0.0")[0], "untrusted")


class KeySeparationTests(LinuxCase):
    def lists(self):
        feed, linux_list = self.folder / "update_keys.json", self.folder / "linux_keys.json"
        for path in (feed, linux_list):
            path.write_text(json.dumps({"keys": []}))
        return feed, linux_list

    def test_neither_list_takes_a_key_the_other_ever_named(self):
        key = Key().public
        for status in ("active", "revoked"):
            with self.subTest(status):
                feed, linux_list = self.lists()
                feed.write_text(json.dumps({"keys": [{"id": "f1", "status": status,
                                                      "public_key": base64.b64encode(key).decode()}]}))
                with self.assertRaises(sign_linux_release.ReleaseToolError):
                    sign_linux_release.add_key("linux-1", key, linux_list, self.folder / "pub", feed)
                feed, linux_list = self.lists()
                linux_list.write_text(json.dumps({"keys": [{"id": "l1", "status": status,
                                                            "public_key": base64.b64encode(key).decode()}]}))
                with self.assertRaises(feed_tool.FeedToolError):
                    feed_tool.add_key(feed, "2026-12", key, separate_from=linux_list)

    def test_the_shipped_lists_are_disjoint_revoked_entries_included(self):
        feed = update_feed.listed_keys(update_feed.KEYS_FILE)
        shipped = update_feed.listed_keys(Path(linux.__file__).with_name("linux_release_keys.json"))
        self.assertIsNotNone(feed)
        self.assertIsNotNone(shipped)
        self.assertFalse(feed & shipped)

    def test_an_unreadable_other_list_refuses_the_key(self):
        feed, linux_list = self.lists()
        linux_list.write_text("not json")
        with self.assertRaises(feed_tool.FeedToolError):
            feed_tool.add_key(feed, "2026-12", Key().public, separate_from=linux_list)
        with self.assertRaises(sign_linux_release.ReleaseToolError):
            sign_linux_release.add_key("linux-1", Key().public, self.folder / "linux_keys2.json", self.folder / "pub",
                                       self.folder / "missing.json")


class ReviewedKeyListTests(LinuxCase):
    def test_a_release_must_carry_the_reviewed_list(self):
        reviewed = self.folder / "reviewed.json"
        reviewed.write_text(release_manifest.KEYS_FILE.read_text())
        root = release_tree(self.folder / "dist", "1.0.1", "new")
        addon = root / "5.3" / sign_linux_release.KEYS_IN_TREE
        addon.parent.mkdir(parents=True)
        rehearsal = json.loads(reviewed.read_text())
        rehearsal["keys"].append({"id": "rehearsal", "status": "active",
                                  "public_key": base64.b64encode(Key().public).decode()})
        addon.write_text(json.dumps(rehearsal))   # A list that also trusts a rehearsal key.
        for path in [addon.parent, *addon.parents]:
            if path == root.parent:
                break
            os.chmod(path, 0o755)
        os.chmod(addon, 0o644)
        with self.assertRaises(sign_linux_release.ReleaseToolError) as caught:
            sign_linux_release.write_manifest(root, "1.0.1", "test-1", self.key, reviewed)
        self.assertIn("not the reviewed list", str(caught.exception))
        shutil.copyfile(reviewed, addon)
        sign_linux_release.write_manifest(root, "1.0.1", "test-1", self.key, reviewed)
        self.assertEqual(sign_linux_release.check_tree(root, "1.0.1", reviewed), "test-1")


class ListedFixTests(LinuxCase):
    """Each smaller fix with a test that fails when it is removed."""

    def test_remove_previous_sweeps_only_the_installers_own_leftovers(self):
        root = self.installed()
        work = self.folder / "updates" / "1.0.1"
        work.mkdir(parents=True)
        keep = []
        for name in (".%s.previous-123" % root.name, ".%s.update-45" % root.name):
            (root.parent / name).mkdir()
        for name in (".%s.previous-x" % root.name, ".%s.update-" % root.name, "%s (previous)" % root.name,
                     "Other.previous-1"):
            (root.parent / name).mkdir()
            keep.append(root.parent / name)
        target = self.folder / "outside"
        target.mkdir()
        os.symlink(target, root.parent / (".%s.previous-9" % root.name))   # A link is never followed or removed.
        note = root.parent / (root.name + " - update interrupted.txt")
        kept = root.parent / (root.name + " (kept)")
        text = "Your previous Angelo is in: %s\nRename it back to: %s\n" % (kept, root)
        note.write_bytes(os.fsencode(text))
        update_flow_module().record_recovery(work, root, "1.0.0", kept, note, text)
        (work / "previous.txt").write_text(str(work / "staged" / "Angelo-1.0.0-linux-x64") + "\n")
        (work / "staged" / "Angelo-1.0.0-linux-x64").mkdir(parents=True)
        self.assertTrue(update_flow_module().remove_previous(work, root))
        self.assertEqual(sorted(p.name for p in root.parent.iterdir()),
                         sorted([root.name, ".%s.previous-9" % root.name] + [p.name for p in keep]))
        self.assertTrue(target.is_dir())
        self.assertFalse(note.exists(), "the recovery note goes once the app it names runs again")
        self.assertFalse((work / "staged" / "Angelo-1.0.0-linux-x64").exists())

    def test_the_download_is_read_from_one_handle(self):
        tarball = self.download()
        seen = {}
        real_verify, real_open = release_manifest.verify_minisig, tarfile.open

        def verify(path, text, keys, name=None, handle=None):
            seen["verified"] = handle
            return real_verify(path, text, keys, name, handle)

        def opened(*args, **kwargs):
            seen["unpacked"] = kwargs.get("fileobj")
            return real_open(*args, **kwargs)
        work = self.folder / "work"
        work.mkdir()
        with mock.patch.object(release_manifest, "verify_minisig", verify), \
                mock.patch.object(tarfile, "open", opened):
            self.stage(tarball, work)
        self.assertIsNotNone(seen["verified"])
        self.assertIs(seen["verified"], seen["unpacked"])

    def test_the_unpack_stop_is_the_reserved_space(self):
        from angelo_agent import update_flow
        self.assertEqual(update_flow.UNPACKED_FACTOR["linux"], linux.UNPACK_FACTOR)
        tarball, limits = self.download(), []
        real = release_manifest.extraction_filter
        work = self.folder / "work"
        work.mkdir()
        with mock.patch.object(release_manifest, "extraction_filter",
                               lambda limit, *a: limits.append(limit) or real(limit, *a)):
            self.stage(tarball, work)
        self.assertEqual(limits, [linux.UNPACK_FACTOR * tarball.stat().st_size])

    def test_the_fingerprint_sees_a_file_put_back_with_its_size_and_time(self):
        root = self.installed()
        path = root / "lib" / "marker.txt"
        stamp = 1_700_000_000_000_000   # Whole microseconds: some file systems (APFS) keep no finer through utime.
        os.utime(path, ns=(stamp, stamp))
        os.utime(path.parent, ns=(stamp, stamp))
        before, info, folder = linux.fingerprint(root), os.stat(path), os.stat(path.parent)
        replacement = root / "lib" / "marker.new"
        replacement.write_text(path.read_text())
        os.utime(replacement, ns=(info.st_atime_ns, info.st_mtime_ns))
        os.replace(replacement, path)   # Same name, size, content and mtime: another inode and ctime.
        os.utime(path.parent, ns=(folder.st_atime_ns, folder.st_mtime_ns))   # Only ctime and the inode can tell.
        self.assertNotEqual(linux.fingerprint(root), before)


def update_flow_module():
    from angelo_agent import update_flow
    return update_flow


class TopFolderTests(LinuxCase):
    def topless(self):
        """A signed release packed without a member for its top folder (tar adds one by default)."""
        tree = release_tree(self.folder / "build" / "topless", "1.0.1", "new", self.key)
        folder = self.folder / "downloads" / "topless"
        folder.mkdir(parents=True)
        tarball = folder / (tree.name + ".tar.xz")
        with tarfile.open(tarball, "w:xz") as archive:
            for path in sorted(tree.rglob("*")):
                archive.add(path, arcname="%s/%s" % (tree.name, path.relative_to(tree).as_posix()), recursive=False)
        Path(str(tarball) + ".minisig").write_text(sign_linux_release.minisign_signature(tarball, "test-1", self.key))
        return tarball

    def test_the_staged_top_folder_is_0755_whatever_the_umask(self):
        tarball = self.topless()
        work = self.folder / "work"
        work.mkdir()
        previous = os.umask(0o002)
        try:
            staged, _ = self.stage(tarball, work)
        finally:
            os.umask(previous)
        self.assertEqual(os.stat(staged).st_mode & 0o777, 0o755)

    def test_the_release_gate_refuses_a_tarball_without_its_top_folder(self):
        with self.assertRaises(release_manifest.ManifestError) as caught:
            sign_linux_release.check_tarball(self.topless(), "1.0.1", self.keys)
        self.assertEqual(caught.exception.code, "layout")


class ManifestLinkTests(unittest.TestCase):
    def test_links_that_leave_the_release_through_other_links_are_refused(self):
        base = {"format": release_manifest.FORMAT, "schema": 1, "version": "1.0.1", "platform": "linux-x64",
                "root": release_manifest.root_name("1.0.1")}
        chains = {
            "through a link to ..": [{"path": "a", "type": "dir"}, {"path": "a/up", "type": "link", "target": ".."},
                                     {"path": "a/b", "type": "link", "target": "up/../.."}],
            "a loop": [{"path": "x", "type": "link", "target": "y"}, {"path": "y", "type": "link", "target": "x/z"}],
        }
        for label, entries in chains.items():
            with self.subTest(label), self.assertRaises(release_manifest.ManifestError):
                release_manifest.parse_manifest(json.dumps(dict(base, entries=entries)).encode(), "1.0.1")
        fine = [{"path": "lib", "type": "dir"}, {"path": "lib/a.so", "type": "link", "target": "b.so"},
                {"path": "lib/b.so", "type": "link", "target": "../lib/c.so"}]
        release_manifest.parse_manifest(json.dumps(dict(base, entries=fine)).encode(), "1.0.1")
        for bad in (True, "1"):
            with self.subTest(schema=bad), self.assertRaises(release_manifest.ManifestError):
                release_manifest.parse_manifest(json.dumps(dict(base, schema=bad, entries=fine)).encode(), "1.0.1")


if __name__ == "__main__":
    unittest.main()
