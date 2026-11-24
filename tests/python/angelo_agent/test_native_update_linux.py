# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Linux updates: which installs update themselves (update_stage_linux.support), unpacking and checking a downloaded
release against its signed manifest (stage), the preparation end to end (update_flow.Preparer for linux-x64), the
installer's inputs, and the installer script (update_helpers/install_update_linux.sh) run for real.

Every release here is a small stand-in tree signed with a throwaway key that the test trusts in place of the shipped
linux_release_keys.json. The script needs Linux (/proc, GNU coreutils): it runs on the Linux CI and is an
environment skip elsewhere; everything else runs everywhere.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import base64
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import host_waits  # noqa: E402
from host_test_policy import environment_skip

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts/angelo"))
import sign_linux_release  # noqa: E402  (the minisign signer the release uses)
from angelo_agent import ed25519, release_manifest, update_feed, update_flow, update_install  # noqa: E402
from angelo_agent import update_stage_linux as linux  # noqa: E402
from angelo_agent.update_install import Request  # noqa: E402

INVOCATION = "b" * 32
LINUX_SCRIPT = sys.platform.startswith("linux") and Path("/proc/self/exe").exists()
URL = "https://downloads.angelo.studio/1.0.1/Angelo-1.0.1-linux-x64.tar.xz"


class Key:
    def __init__(self):
        self.seed = os.urandom(32)
        self.public = ed25519.public_key(self.seed)

    def sign(self, message):
        return ed25519.sign(self.seed, message)

    def public_key(self):
        return self.public


def release_tree(parent, version, marker, key=None, key_id="test-1"):
    """A signed stand-in release folder (``key`` None: no manifest at all)."""
    root = Path(parent) / release_manifest.root_name(version)
    (root / "lib").mkdir(parents=True)
    (root / "angelo").write_text("#!/bin/sh\nexit 0\n")
    os.chmod(root / "angelo", 0o755)
    (root / "lib/marker.txt").write_text(marker)
    os.symlink("marker.txt", root / "lib/marker.link")
    for path in [root] + list(root.rglob("*")):
        if not path.is_symlink():
            os.chmod(path, os.stat(path).st_mode & 0o7755)
    if key is not None:
        manifest = release_manifest.manifest_bytes(release_manifest.describe_tree(root), version)
        (root / release_manifest.MANIFEST_NAME).write_bytes(manifest)
        (root / release_manifest.SIGNATURE_NAME).write_bytes(release_manifest.signature_bytes(
            key_id, key.sign(release_manifest.signed_message(manifest))))
    return root


def pack(tree, tarball, extra=None):
    with tarfile.open(tarball, "w:xz") as archive:
        archive.add(tree, arcname=tree.name)
        if extra is not None:
            extra(archive)
    return Path(tarball)


@unittest.skipUnless(os.name == "posix", environment_skip("Linux release folders: POSIX file modes, owners and links"))
class LinuxCase(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(self._cleanup)
        self.key = Key()
        self.keys = {"test-1": self.key.public}
        keys_file = self.folder / "linux_release_keys.json"
        keys_file.write_text(json.dumps({"keys": [{"id": "test-1", "status": "active", "public_key":
                                                   base64.b64encode(self.key.public).decode()}]}))
        patcher = mock.patch.object(release_manifest, "KEYS_FILE", keys_file)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _cleanup(self):
        for folder, _, _ in os.walk(self.folder):
            try:
                os.chmod(folder, 0o755)
            except OSError:
                pass
        shutil.rmtree(self.folder, ignore_errors=True)

    def installed(self, version="1.0.0", marker="old"):
        return release_tree(self.folder / "Apps", version, marker, self.key)

    def download(self, version="1.0.1", marker="new", key=None, extra=None):
        """A packed release, with its ``.minisig`` (always by the trusted key) beside it; ``key`` signs the manifest."""
        source = self.folder / "build" / marker
        tree = release_tree(source, version, marker, key or self.key)
        folder = self.folder / "downloads" / marker
        folder.mkdir(parents=True)
        # Named as the feed publishes it: the .minisig's trusted comment names that file.
        tarball = pack(tree, folder / ("%s.tar.xz" % release_manifest.root_name(version)), extra)
        Path(str(tarball) + ".minisig").write_text(sign_linux_release.minisign_signature(tarball, "test-1", self.key))
        return tarball

    def stage(self, tarball, work, version="1.0.1", keys=None, **options):
        signature = Path(str(tarball) + ".minisig").read_text()
        return linux.stage(tarball, version, work, self.keys if keys is None else keys, signature=signature, **options)


class SupportTests(LinuxCase):
    def test_a_release_folder_as_extracted_updates_itself(self):
        root = self.installed()
        self.assertEqual(linux.support(root / "angelo", "1.0.0"), ("", root.resolve()))
        self.assertEqual(linux.install_root(root / "lib/marker.txt"), root.resolve())

    def test_no_manifest_or_another_version_is_a_location(self):
        bare = release_tree(self.folder / "Dev", "1.0.0", "dev")
        self.assertEqual(linux.support(bare / "angelo", "1.0.0"), ("location", None))
        root = self.installed()
        self.assertEqual(linux.support(root / "angelo", "1.0.2")[0], "location")

    def test_files_the_person_keeps_in_the_folder_stop_automatic_updates(self):
        for extra in ("portable", "lib/my-addon.py"):
            with self.subTest(extra):
                shutil.rmtree(self.folder / "Apps", ignore_errors=True)
                root = self.installed()
                target = root / extra
                target.mkdir() if extra == "portable" else target.write_text("mine")
                self.assertEqual(linux.support(root / "angelo", "1.0.0")[0], "modified")
                self.assertEqual(linux.unlisted(root, "1.0.0"), [extra])

    def test_a_folder_the_person_cannot_write_is_notify_only(self):
        root = self.installed()
        os.chmod(root.parent, 0o555)
        if os.access(root.parent, os.W_OK):
            self.skipTest(environment_skip("running as root: every folder is writable"))
        self.assertEqual(linux.support(root / "angelo", "1.0.0")[0], "permission")


class StageTests(LinuxCase):
    def work(self):
        work = self.folder / "updates" / "1.0.1"
        work.mkdir(parents=True, exist_ok=True)
        return work

    def test_a_signed_release_is_staged_and_fingerprinted(self):
        staged, key_id = self.stage(self.download(), self.work())
        self.assertEqual((staged, key_id), (linux.staged_path(self.work(), "1.0.1"), "test-1"))
        self.assertEqual((staged / "lib/marker.txt").read_text(), "new")
        before = linux.fingerprint(staged)
        (staged / "lib/marker.txt").write_text("changed")
        self.assertNotEqual(linux.fingerprint(staged), before)

    def test_what_is_not_exactly_the_signed_release_is_refused(self):
        def outside(archive):
            info = tarfile.TarInfo("other/file")
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))

        def escaping_link(archive):
            info = tarfile.TarInfo(release_manifest.root_name("1.0.1") + "/lib/escape")
            info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
            archive.addfile(info)

        def added(archive):
            data = b"added after signing"
            info = tarfile.TarInfo(release_manifest.root_name("1.0.1") + "/lib/added")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))

        cases = {"layout": dict(extra=outside), "archive": dict(extra=escaping_link),
                 "unexpected": dict(extra=added), "bad_signature": dict(key=Key())}   # Another key, the trusted id.
        for code, options in cases.items():
            with self.subTest(code):
                marker = "new-" + code
                with self.assertRaises(linux.StageError) as caught:
                    self.stage(self.download(marker=marker, **options), self.work())
                self.assertEqual(caught.exception.code, code, str(caught.exception))
                self.assertFalse(linux.staged_path(self.work(), "1.0.1").parent.exists(), "nothing unchecked is left")

    def test_a_damaged_download_is_refused(self):
        tarball = self.download()
        tarball.write_bytes(tarball.read_bytes()[: tarball.stat().st_size // 2])
        with self.assertRaises(linux.StageError) as caught:   # The signature fails before anything is unpacked.
            self.stage(tarball, self.work())
        self.assertEqual(caught.exception.code, "bad_signature")

    def test_no_trusted_key_refuses_every_release(self):
        with self.assertRaises(linux.StageError) as caught:
            self.stage(self.download(), self.work(), keys={})
        self.assertEqual(caught.exception.code, "no_keys")

    def test_the_relaunch_path_stays_inside_the_folder(self):
        root = self.installed()
        self.assertEqual(linux.relaunch_path(root / "angelo", root), "angelo")
        (root / "lib/blender").write_text("binary")
        self.assertEqual(linux.relaunch_target(root / "lib/blender", root, "angelo"), "angelo", "the launcher first")
        os.chmod(root / "angelo", 0o644)
        self.assertEqual(linux.relaunch_target(root / "lib/blender", root, "angelo"), "lib/blender")
        self.assertEqual(linux.relaunch_target(root / "lib/blender", root, "../x"), "lib/blender")
        with self.assertRaises(ValueError):
            linux.relaunch_path(self.folder / "elsewhere", root)


class PreparerTests(LinuxCase):
    def preparer(self, root, tarball=None):
        self.tarball = tarball or self.download()
        self.signature_urls = []

        def fetch(expected, target, reserve=0, **options):
            shutil.copyfile(self.tarball, target)
            return Path(target)

        def fetch_signature(url, stream=None):
            self.signature_urls.append(url)
            return Path(str(self.tarball) + ".minisig").read_text()
        patcher = mock.patch.object(linux, "fetch_signature", fetch_signature)
        patcher.start()
        self.addCleanup(patcher.stop)
        return update_flow.Preparer(self.folder / "updates", "1.0.0", "linux-x64", str(root / "angelo"),
                                    fetch=fetch, log=lambda *a: None, mirror="")

    def test_prepare_recall_and_the_check_before_installing(self):
        root = self.installed()
        preparer = self.preparer(root)
        self.assertEqual(preparer.support(), ("", str(root.resolve())))
        ready = preparer.prepare(SimpleNamespace(version="1.0.1"), update_feed.Download(URL, "a" * 64, 1000))
        self.assertEqual((ready.family, ready.install_root, ready.method), ("linux", str(root.resolve()), "full"))
        self.assertEqual(Path(ready.payload), linux.staged_path(preparer.work("1.0.1"), "1.0.1"))
        self.assertFalse((preparer.work("1.0.1") / "update.tar.xz").exists(), "the download is removed")
        self.assertEqual(self.signature_urls, [URL + ".minisig"])
        self.assertEqual((ready.download_sha256, ready.key_id), ("a" * 64, "test-1"))
        self.assertEqual(preparer.recall("1.0.1"), ready)
        self.assertTrue(preparer.still_valid(ready))
        (Path(ready.payload) / "lib/marker.txt").write_text("changed after the check")
        self.assertFalse(preparer.still_valid(ready))
        self.assertIsNone(preparer.recall("1.0.1"))

    def test_an_install_that_cannot_update_itself_prepares_nothing(self):
        root = self.installed()
        (root / "portable").mkdir()
        with self.assertRaises(update_flow.PrepareError) as caught:
            self.preparer(root).prepare(SimpleNamespace(version="1.0.1"), update_feed.Download(URL, "a" * 64, 1000))
        self.assertEqual(caught.exception.code, "unsupported:modified")

    def test_the_previous_folder_is_removed_after_the_new_version_starts(self):
        work = self.folder / "updates" / "1.0.1"
        (work / "previous").mkdir(parents=True)
        (work / "previous.txt").write_text(str(work / "previous") + "\n")
        self.assertTrue(update_flow.remove_previous(work, self.installed()))
        self.assertFalse((work / "previous").exists())


class InstallerInputTests(unittest.TestCase):
    def test_family_inputs_and_start(self):
        self.assertEqual(update_install.family("linux-x64"), "linux")
        request = Request("linux", Path("/w"), 7, "1.0.1", "angelo", open_file="/f/a b.blend", root="/A/Angelo",
                          new="/w/staged/Angelo-1.0.1-linux-x64", launcher="angelo", program="blender",
                          kept="/A/Angelo (previous)", note="/A/Angelo - update interrupted.txt", note_text="t\n",
                          invocation=INVOCATION)
        inputs = update_install.inputs(request, {})
        self.assertEqual((inputs["ANGELO_UPDATE_ROOT"], inputs["ANGELO_UPDATE_NEW"], inputs["ANGELO_UPDATE_PROGRAM"],
                          inputs["ANGELO_UPDATE_RELAUNCH"], inputs["ANGELO_UPDATE_OPEN"]),
                         ("/A/Angelo", "/w/staged/Angelo-1.0.1-linux-x64", "blender", "angelo", "/f/a b.blend"))
        self.assertEqual((inputs["ANGELO_UPDATE_KEPT"], inputs["ANGELO_UPDATE_NOTE"], inputs["ANGELO_UPDATE_NOTE_TEXT"]),
                         ("/A/Angelo (previous)", "/A/Angelo - update interrupted.txt", "t\n"))
        env = update_install.helper_environment(request, {"PATH": "/opt/bin", "DISPLAY": ":0", "LD_PRELOAD": "x",
                                                          "PYTHONPYCACHEPREFIX": "/c", "ANGELO_UPDATE_OPEN": "old"})
        self.assertEqual((env["PATH"], env["DISPLAY"]), ("/opt/bin", ":0"), "the relaunched app is started as before")
        self.assertNotIn("LD_PRELOAD", env)
        self.assertNotIn("PYTHONPYCACHEPREFIX", env)
        with tempfile.TemporaryDirectory() as folder:
            calls = []
            update_install.start(request.__class__(**dict(request.__dict__, work=Path(folder))), environ={},
                                 popen=lambda args, **kw: calls.append((args, kw)))
            self.assertEqual(calls[0][0], ["/bin/bash", "--noprofile", "--norc",
                                           str(Path(folder) / "install_update_linux.sh")])
            self.assertTrue(calls[0][1]["start_new_session"])


@unittest.skipUnless(LINUX_SCRIPT, environment_skip("the Linux installer needs /proc and GNU coreutils"))
class ScriptTests(LinuxCase):
    def setUp(self):
        super().setUp()
        self.root = self.installed()
        self.work = self.folder / "updates" / "1.0.1"
        self.work.mkdir(parents=True)
        self.new = self.stage(self.download(), self.work)[0]

    def run_script(self, pid, new=None, locked=False, program="angelo", note=("", "", ""), **test_switches):
        request = Request("linux", self.work, pid, "1.0.1", "angelo", relaunch=False, root=str(self.root),
                          new=str(new or self.new), launcher="angelo", program=program, invocation=INVOCATION,
                          kept=note[0], note=note[1], note_text=note[2])
        script = update_install.prepare_folder(request)
        if locked:
            (self.work / update_install.LOCK_DIR).mkdir()
        env = update_install.helper_environment(request, {"HOME": str(self.folder), "PATH": os.environ["PATH"]})
        env.update({"ANGELO_UPDATE_TEST_" + name: "1" for name in test_switches})   # NO_EXCHANGE, STOP_BETWEEN.
        done = subprocess.run(["/bin/bash", "--noprofile", "--norc", str(script)], env=env, timeout=60,
                              input="ANGELO_UPDATE_COMMIT:" + INVOCATION + "\n", capture_output=True, text=True)
        if locked:
            return done
        self.assertTrue(update_install.ready(self.work, INVOCATION), done.stderr)
        return update_install.read_result(self.work)

    def marker(self):
        return (self.root / "lib/marker.txt").read_text()

    def test_it_waits_for_angelo_then_swaps_the_folders(self):
        angelo = subprocess.Popen(["sleep", "1"])
        self.addCleanup(angelo.wait)
        started = time.monotonic()
        result = self.run_script(angelo.pid)
        self.assertGreaterEqual(time.monotonic() - started, 0.9, "nothing moved while Angelo ran")
        self.assertEqual((result["status"], result["step"], result["version"]), ("installed", "swap", "1.0.1"))
        self.assertEqual(self.marker(), "new")
        # The folder keeps its path (and name), so the person's launchers still work; it now holds 1.0.1.
        release_manifest.parse_manifest((self.root / release_manifest.MANIFEST_NAME).read_bytes(), "1.0.1")
        previous = Path((self.work / "previous.txt").read_text().strip())
        self.assertEqual(previous, self.work / "previous")
        self.assertEqual((previous / "lib/marker.txt").read_text(), "old")
        self.assertFalse(self.new.exists())

    def recorded(self):
        """The kept folder, note and text Angelo chooses and records at hand-over (update_restart_handoff.launch)."""
        from angelo_agent import update_card
        kept, note, text = update_card.linux_recovery(self.root, "1.0.1")
        update_flow.record_recovery(self.work, self.root, "1.0.0", kept, note, text)
        return kept, note, text

    def test_without_an_exchange_two_renames_keep_the_previous_app_visible(self):
        kept, note, _ = recovery = self.recorded()
        result = self.run_script(999999, note=recovery, NO_EXCHANGE=True)
        self.assertEqual((result["status"], result["step"]), ("installed", "swap"))
        self.assertEqual(self.marker(), "new")
        previous = Path((self.work / "previous.txt").read_text().strip())
        self.assertEqual(previous, Path(kept))
        self.assertEqual((previous / "lib/marker.txt").read_text(), "old")
        self.assertFalse(Path(note).exists())
        self.assertTrue(update_flow.remove_previous(self.work, self.root))
        self.assertFalse(previous.exists())

    def test_a_stop_between_the_renames_leaves_the_app_findable_and_a_note(self):
        result = self.run_script(999999, NO_EXCHANGE=True, STOP_BETWEEN=True)
        self.assertEqual((result["status"], result["step"]), ("failed", "interrupted"))
        self.assertFalse(self.root.exists(), "the stand-in for a stop between the two renames")
        previous = self.root.parent / (self.root.name + " (previous)")
        self.assertEqual((previous / "lib/marker.txt").read_text(), "old")
        note = (self.root.parent / (self.root.name + " - update interrupted.txt")).read_text()
        self.assertIn(str(previous), note)
        self.assertIn(str(self.root), note)

    def test_the_fallback_uses_the_names_and_text_angelo_translated(self):
        kept = self.root.parent / (self.root.name + " (前のバージョン)")
        note = self.root.parent / (self.root.name + " - 更新が中断されました.txt")
        result = self.run_script(999999, note=(str(kept), str(note), "前の Angelo: %s\n" % kept),
                                 NO_EXCHANGE=True, STOP_BETWEEN=True)
        self.assertEqual((result["status"], result["step"]), ("failed", "interrupted"))
        self.assertEqual(note.read_text(encoding="utf-8"), "前の Angelo: %s\n" % kept)
        self.assertEqual((kept / "lib/marker.txt").read_text(), "old")

    def test_a_fifo_planted_at_the_notes_name_refuses_without_blocking(self):
        """The note is created with O_EXCL|O_NOFOLLOW: a FIFO at its name (a co-user's, in a shared sticky folder)
        is never opened, so the installer cannot hang after Angelo quit (noclobber's open would)."""
        kept, note, text = self.recorded()
        os.mkfifo(note)
        result = self.run_script(999999, note=(kept, note, text), NO_EXCHANGE=True)   # run_script's 60 s cap.
        self.assertEqual((result["status"], result["step"]), ("failed", "note"))
        self.assertEqual(self.marker(), "old")
        self.assertTrue(stat.S_ISFIFO(os.lstat(note).st_mode))

    def test_the_relaunched_app_gets_none_of_the_installers_inputs(self):
        record = self.folder / "relaunched-env.txt"
        for tree in (self.root, self.new):   # The launcher the script starts records its environment.
            # Written aside, then renamed: the test never reads a record half written.
            (tree / "angelo").write_text('#!/bin/sh\nenv > "$ANGELO_TEST_ENV_OUT.part" && '
                                         'mv "$ANGELO_TEST_ENV_OUT.part" "$ANGELO_TEST_ENV_OUT"\n')
            os.chmod(tree / "angelo", 0o755)
        request = Request("linux", self.work, 999999, "1.0.1", "angelo", relaunch=True, root=str(self.root),
                          new=str(self.new), launcher="angelo", program="blender", invocation=INVOCATION,
                          kept=str(self.root.parent / "kept"), note=str(self.root.parent / "note.txt"),
                          note_text="note\n")
        script = update_install.prepare_folder(request)
        env = update_install.helper_environment(request, {"HOME": str(self.folder), "PATH": os.environ["PATH"],
                                                          "ANGELO_TEST_ENV_OUT": str(record)})
        subprocess.run(["/bin/bash", "--noprofile", "--norc", str(script)], env=env, timeout=60,
                       input="ANGELO_UPDATE_COMMIT:" + INVOCATION + "\n", capture_output=True, text=True)
        lines = host_waits.until(lambda: record.exists() and record.read_text(),   # Complete: renamed into place.
                                 what="the relaunched launcher's environment record").splitlines()
        self.assertIn("ANGELO_TEST_ENV_OUT=%s" % record, lines, "the person's own variables are kept")
        self.assertEqual([line for line in lines if line.startswith("ANGELO_UPDATE_")], [])

    def test_a_second_installer_for_the_same_update_does_nothing(self):
        done = self.run_script(999999, locked=True)
        self.assertEqual(done.returncode, 0)
        self.assertIsNone(update_install.read_result(self.work))
        self.assertEqual(self.marker(), "old")

    def test_a_failed_second_rename_puts_the_old_folder_back(self):
        os.chmod(self.new.parent, stat.S_IRUSR | stat.S_IXUSR)
        result = self.run_script(999999)
        self.assertEqual((result["status"], result["step"]), ("failed", "move_new"))
        self.assertEqual(self.marker(), "old")

    def test_a_missing_or_unsigned_update_changes_nothing(self):
        result = self.run_script(999999, new=self.work / "nothing")
        self.assertEqual((result["status"], result["step"]), ("failed", "missing_update"))
        os.remove(self.new / release_manifest.MANIFEST_NAME)
        for name in (update_install.RESULT_FILE, update_install.READY_FILE):
            (self.work / name).unlink(missing_ok=True)
        shutil.rmtree(self.work / update_install.LOCK_DIR, ignore_errors=True)
        result = self.run_script(999999)
        self.assertEqual((result["status"], result["step"]), ("failed", "missing_update"))
        self.assertEqual(self.marker(), "old")

    def test_a_program_path_out_of_the_folder_is_refused(self):
        result = self.run_script(999999, program="../../bin/sh")
        self.assertEqual((result["status"], result["step"]), ("failed", "missing_app"))
        self.assertEqual(self.marker(), "old")

    def test_another_copy_running_from_the_folder_stops_the_swap(self):
        shutil.copyfile(shutil.which("sh"), self.root / "angelo")   # A shell: it waits in read, whatever its name.
        other = subprocess.Popen([str(self.root / "angelo"), "-c", "read _"], stdin=subprocess.PIPE)
        self.addCleanup(other.communicate)
        self.addCleanup(other.kill)
        time.sleep(0.2)
        self.assertEqual(linux.other_instances(self.root / "angelo", os.getpid()), [other.pid])
        result = self.run_script(999999)
        self.assertEqual((result["status"], result["step"]), ("aborted", "another_instance"))
        self.assertEqual(self.marker(), "old")


if __name__ == "__main__":
    unittest.main()
