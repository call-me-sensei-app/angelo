# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Linux fallback's recovery record: what Angelo deletes after an install, and only that.

The break-it re-read of 369d14b9f47 (release-2.1.0.noindex/review/break-it-linux-signing-369d14b9f47.md, SHOULD-1)
steered a recursive delete with an edited ``recovery.json``: any release beside the install, and ``<parent>/..``
when it held a manifest, because paths were compared without normalising. The class, and its fix:

* every recorded or installer-written path must be absolute and already normal (no ``.``/``..`` part), compared as
  a string, never resolved;
* the kept folder is removed only when it is the install folder Angelo recorded at hand-over, renamed: the same
  path, device, inode and version;
* the note is removed only when it is the recorded ``.txt`` beside the install, a regular file (never a link), whose
  bytes hash to the note Angelo wrote, and only while the app at the install runs.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import os
import shutil
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from host_test_policy import environment_skip

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from test_native_update_linux import LinuxCase, release_tree  # noqa: E402
from test_native_update_linux import linux, release_manifest  # noqa: E402


def flow():
    from angelo_agent import update_flow
    return update_flow


class RecoveryCase(LinuxCase):
    def setUp(self):
        super().setUp()
        self.home = self.folder / "home"
        self.root = release_tree(self.home / "Apps", "1.0.0", "old", self.key)
        self.work = self.folder / "updates" / "1.0.1"
        self.work.mkdir(parents=True)
        self.kept = self.root.parent / (self.root.name + " (kept)")
        self.note = self.root.parent / (self.root.name + " - interrupted.txt")
        # Angelo's note always names both folders (update_card.linux_recovery, every language).
        self.text = "Your previous Angelo is in: %s\nRename it back to: %s\n" % (self.kept, self.root)
        flow().record_recovery(self.work, self.root, "1.0.0", self.kept, self.note, self.text)
        self.running("1.0.1")   # Cleanup runs in the version the update installed.

    def running(self, version):
        from angelo_agent import app_version
        patch = mock.patch.object(app_version, "current", lambda: version)
        patch.start()
        self.addCleanup(patch.stop)

    def fallback(self):
        """What the script's two renames leave after a finished swap: the old folder kept, the new one in place."""
        self.root.rename(self.kept)
        release_tree(self.home / "Apps", "1.0.1", "new", self.key).rename(self.root)
        self.previous(self.kept)

    def previous(self, path):
        (self.work / "previous.txt").write_text(str(path) + "\n", encoding="utf-8")

    def edit(self, **fields):
        path = self.work / flow().RECOVERY_FILE
        document = json.loads(path.read_text(encoding="utf-8"))
        document.update(fields)
        path.write_text(json.dumps(document), encoding="utf-8")


class KeptFolderTests(RecoveryCase):
    def test_the_recorded_install_folder_renamed_is_removed(self):
        self.fallback()
        self.assertTrue(flow().remove_previous(self.work, self.root))
        self.assertFalse(self.kept.exists())
        self.assertTrue(self.root.is_dir())

    def test_only_the_exact_recorded_folder_is_removed_even_when_it_holds_a_release(self):
        lookalike = release_tree(self.folder / "elsewhere", "1.0.0", "theirs", self.key)
        lookalike = lookalike.rename(self.root.parent / (self.root.name + " (previous)"))
        self.previous(lookalike)
        self.assertFalse(flow().remove_previous(self.work, self.root), "named by previous.txt, never recorded")
        self.edit(kept=str(lookalike))   # An edited record naming another release beside the install.
        self.assertFalse(flow().remove_previous(self.work, self.root), "not the folder recorded at hand-over")
        self.assertTrue((lookalike / "lib/marker.txt").exists())

    def test_a_copy_of_the_recorded_folder_is_not_it(self):
        shutil.copytree(self.root, self.kept, symlinks=True)   # Same path and version, another inode.
        self.previous(self.kept)
        self.assertFalse(flow().remove_previous(self.work, self.root))
        self.assertTrue(self.kept.is_dir())

    def test_the_recorded_folder_holding_another_version_is_kept(self):
        self.fallback()
        manifest = self.kept / release_manifest.MANIFEST_NAME
        document = json.loads(manifest.read_bytes())
        document["version"] = "0.9.0"
        os.chmod(manifest, 0o644)
        manifest.write_text(json.dumps(document), encoding="utf-8")
        self.assertFalse(flow().remove_previous(self.work, self.root))
        self.assertTrue(self.kept.is_dir())

    def test_a_path_that_is_not_normal_never_matches(self):
        """The probe that emptied the home folder: ``<Apps>/..`` holding a manifest, named by both files."""
        (self.home / release_manifest.MANIFEST_NAME).write_text("{}")
        (self.home / "my-work.blend").write_text("mine")
        for path in (str(self.root.parent) + "/..", str(self.root.parent) + "/./..",
                     str(self.root.parent) + "//" + self.kept.name, str(self.root.parent) + "/" + self.kept.name + "/"):
            with self.subTest(path=path):
                self.edit(kept=path)
                self.previous(path)
                self.assertFalse(flow().remove_previous(self.work, self.root))
                self.assertTrue((self.home / "my-work.blend").exists())
                self.assertTrue(self.root.is_dir())

    def test_installer_named_folders_need_normal_paths_too(self):
        staged = self.work / "staged"
        (staged / "Angelo-1.0.0-linux-x64").mkdir(parents=True)
        self.previous(str(staged) + "/..")   # Its parent "is" staged, read without normalising: the work folder.
        flow().remove_previous(self.work, self.root)
        self.assertTrue((self.work / "previous.txt").exists(), "the update's own folder stays")

    def test_a_record_for_another_install_is_ignored(self):
        self.fallback()
        other = self.home / "Apps" / "Other"
        other.mkdir()
        self.assertFalse(flow().remove_previous(self.work, other))
        self.assertTrue(self.kept.is_dir())

    def test_a_recorded_version_that_is_not_older_than_the_running_one_is_kept(self):
        self.fallback()
        self.running("1.0.0")   # Not a start of the version the update installed.
        self.assertFalse(flow().remove_previous(self.work, self.root))
        self.assertTrue(self.kept.is_dir())

    def test_a_folder_at_the_kept_folders_manifest_name_is_kept_without_an_exception(self):
        self.fallback()
        manifest = self.kept / release_manifest.MANIFEST_NAME
        manifest.unlink()
        manifest.mkdir()
        self.assertFalse(flow().remove_previous(self.work, self.root))
        self.assertTrue(self.kept.is_dir())

    def test_a_fifo_at_the_kept_folders_manifest_name_never_blocks(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest(environment_skip("FIFOs (os.mkfifo)"))
        self.fallback()
        manifest = self.kept / release_manifest.MANIFEST_NAME
        manifest.unlink()
        os.mkfifo(manifest)
        self.assertFalse(flow().remove_previous(self.work, self.root))   # O_NONBLOCK: returns at once.
        self.assertTrue(self.kept.is_dir())

    def test_the_record_refuses_paths_that_are_not_normal_or_not_beside_the_install(self):
        for kept, note in ((str(self.root.parent) + "/../kept", self.note), (self.kept, self.folder / "note.txt"),
                           ("kept", self.note), (self.kept, str(self.root.parent) + "/./note.txt")):
            with self.subTest(kept=kept, note=note), self.assertRaises(ValueError):
                flow().record_recovery(self.work, self.root, "1.0.0", kept, note, self.text)


class CleanupNeverRaisesTests(RecoveryCase):
    """Each step of cleanup (note, leftovers, kept folder) is independent, and none raises or blocks on what it finds:
    an exception here would abort start-up's update handling at every launch."""

    def leftover(self):
        path = self.root.parent / (".%s.previous-77" % self.root.name)
        path.mkdir()
        return path

    def test_a_note_that_cannot_be_removed_skips_nothing_else(self):
        self.fallback()
        self.note.write_bytes(os.fsencode(self.text))
        leftover = self.leftover()
        with mock.patch.object(Path, "unlink", side_effect=PermissionError(1, "sticky folder")):
            self.assertTrue(flow().remove_previous(self.work, self.root))
        self.assertTrue(self.note.exists())
        self.assertFalse(leftover.exists())
        self.assertFalse(self.kept.exists(), "the kept release is still removed")

    def test_a_previous_txt_that_is_not_utf8_skips_nothing_else(self):
        self.note.write_bytes(os.fsencode(self.text))
        leftover = self.leftover()
        (self.work / "previous.txt").write_bytes(os.fsencode(str(self.kept)) + b"\xff\n")
        self.assertTrue(flow().remove_previous(self.work, self.root))
        self.assertFalse(self.note.exists())
        self.assertFalse(leftover.exists())

    def test_a_fifo_at_the_record_or_previous_txt_never_blocks(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest(environment_skip("FIFOs (os.mkfifo)"))
        for name in (flow().RECOVERY_FILE, "previous.txt"):
            with self.subTest(name):
                (self.work / name).unlink(missing_ok=True)
                os.mkfifo(self.work / name)
                self.assertFalse(flow().remove_previous(self.work, self.root))   # O_NONBLOCK: returns at once.
                (self.work / name).unlink()
        self.assertTrue(self.root.is_dir())

    def test_a_folder_that_can_be_listed_but_not_searched_raises_nothing(self):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest(environment_skip("running as root: every folder is searchable"))
        self.leftover()
        (self.work / "previous.txt").write_text(str(self.kept) + "\n")
        os.chmod(self.root.parent, 0o644)   # Entries listed, never stat'ed: EACCES, which Path.is_dir raises.
        self.addCleanup(os.chmod, self.root.parent, 0o755)
        flow().remove_previous(self.work, self.root)

    def test_deeply_nested_json_is_no_match(self):
        nested = b"[" * 30000 + b"]" * 30000   # Under the reader's limit; past json's recursion limit.
        self.fallback()
        manifest = self.kept / release_manifest.MANIFEST_NAME
        os.chmod(manifest, 0o644)
        manifest.write_bytes(nested)
        self.assertFalse(flow().remove_previous(self.work, self.root))
        (self.work / flow().RECOVERY_FILE).write_bytes(nested)
        self.assertFalse(flow().forget_note(self.work, self.root))
        self.assertTrue(self.kept.is_dir())


class NoteTests(RecoveryCase):
    def write_note(self, path=None, text=None):
        # The bytes the script writes (printf '%s'), never newline-translated (write_text gives \r\n on Windows).
        (path or self.note).write_bytes(os.fsencode(self.text if text is None else text))
        return path or self.note

    def test_the_note_angelo_wrote_goes_while_the_app_runs(self):
        self.write_note()
        self.assertTrue(flow().forget_note(self.work, self.root))
        self.assertFalse(self.note.exists())

    def test_a_file_with_other_bytes_at_the_name_is_kept(self):
        self.write_note(text="the person's own notes\n")
        self.assertFalse(flow().forget_note(self.work, self.root))
        self.assertTrue(self.note.exists())

    def test_a_record_naming_a_file_that_is_not_a_txt_is_ignored(self):
        work = self.write_note(self.root.parent / "scene.blend")
        self.edit(note=str(work))
        self.assertFalse(flow().forget_note(self.work, self.root))
        self.assertTrue(work.exists())

    def test_a_record_naming_a_file_away_from_the_install_is_ignored(self):
        elsewhere = self.write_note(self.folder / "elsewhere.txt")
        self.edit(note=str(elsewhere))
        self.assertFalse(flow().forget_note(self.work, self.root))
        self.assertTrue(elsewhere.exists())
        self.edit(note=str(self.root.parent) + "/../" + self.note.name)
        self.write_note(self.home / self.note.name)
        self.assertFalse(flow().forget_note(self.work, self.root))
        self.assertTrue((self.home / self.note.name).exists())

    def test_a_note_that_does_not_name_both_folders_is_kept_even_when_its_hash_is_recorded(self):
        """A forged record: the hash of someone's own file beside the install, which names neither folder."""
        import hashlib
        todo = self.write_note(self.root.parent / "todo.txt", text="buy milk\n")
        self.edit(note=str(todo), note_sha256=hashlib.sha256(b"buy milk\n").hexdigest())
        self.assertFalse(flow().forget_note(self.work, self.root))
        self.assertTrue(todo.exists())

    def test_a_note_naming_only_one_of_the_folders_is_kept(self):
        """The install folder must be named on its own: its path is also the start of the kept folder's."""
        import hashlib
        for text in ("Your previous Angelo is in: %s\n" % self.kept, "Rename it back to: %s\n" % self.root):
            with self.subTest(text=text):
                self.write_note(text=text)
                self.edit(note_sha256=hashlib.sha256(os.fsencode(text)).hexdigest())
                self.assertFalse(flow().forget_note(self.work, self.root))
                self.assertTrue(self.note.exists())

    def test_a_folder_at_the_notes_name_is_kept_and_cleanup_goes_on(self):
        self.fallback()
        self.note.mkdir()   # Made after the script removed the note (a co-user, in a shared sticky folder).
        self.assertFalse(flow().forget_note(self.work, self.root))
        self.assertTrue(flow().remove_previous(self.work, self.root), "the kept release is still removed")
        self.assertFalse(self.kept.exists())
        self.assertTrue(self.note.is_dir())

    def test_the_note_stays_while_the_app_does_not_run(self):
        self.write_note()
        (self.root / release_manifest.MANIFEST_NAME).unlink()
        self.assertFalse(flow().forget_note(self.work, self.root))
        self.assertTrue(self.note.exists())

    def test_a_link_at_the_notes_name_is_never_followed_or_removed(self):
        target = self.write_note(self.folder / "target.txt")
        os.symlink(target, self.note)
        self.assertFalse(flow().forget_note(self.work, self.root))
        self.assertTrue(self.note.is_symlink())
        self.assertTrue(target.exists())

    def test_a_fifo_at_the_notes_name_is_never_opened_for_long(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest(environment_skip("FIFOs (os.mkfifo)"))
        os.mkfifo(self.note)
        self.assertFalse(flow().forget_note(self.work, self.root))   # O_NONBLOCK: returns at once, never blocks.
        self.assertTrue(self.note.exists())


@unittest.skipUnless(os.name == "posix", environment_skip("POSIX file kinds: Windows refuses to open a folder at all; "
                                                         "this reader serves Linux releases only"))
class OwnFileReaderTests(unittest.TestCase):
    """release_manifest.read_own_file, which every Linux cleanup and support reader uses: only ManifestError comes
    out, whatever is at the name, the kind is judged on the raw descriptor before a read, and the descriptor is
    always closed."""

    def setUp(self):
        import tempfile
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)

    def read(self, name):
        return release_manifest.read_own_file(str(self.folder), name, 1024)

    def test_what_is_not_a_regular_file_is_refused_as_such(self):
        (self.folder / "folder").mkdir()
        kinds = {"folder": "folder"}
        if hasattr(os, "mkfifo"):
            os.mkfifo(self.folder / "fifo")   # O_NONBLOCK: refused at once, never waits for a writer.
            kinds["fifo"] = "fifo"
        for name in kinds:
            with self.subTest(name), self.assertRaises(release_manifest.ManifestError) as caught:
                self.read(name)
            self.assertIn("not a file", str(caught.exception))

    def test_the_kind_is_judged_before_any_byte_is_read(self):
        (self.folder / "file").write_bytes(b"{}")
        real = os.fstat
        fake = lambda fd: SimpleNamespace(st_mode=0o010644, st_size=real(fd).st_size)   # A FIFO's mode.
        read = mock.Mock(side_effect=AssertionError("a byte was read before the kind was judged"))
        with mock.patch.object(release_manifest.os, "fstat", fake), \
                mock.patch.object(release_manifest.os, "read", read), \
                self.assertRaises(release_manifest.ManifestError) as caught:
            self.read("file")
        self.assertIn("not a file", str(caught.exception))
        read.assert_not_called()

    def test_the_descriptor_is_closed_on_every_path(self):
        (self.folder / "folder").mkdir()
        (self.folder / "big").write_bytes(b"x" * 2048)
        (self.folder / "file").write_bytes(b"{}")
        closed, real_close = [], os.close
        with mock.patch.object(release_manifest.os, "close", lambda fd: closed.append(fd) or real_close(fd)):
            for name in ("folder", "big", "file"):
                try:
                    self.read(name)
                except release_manifest.ManifestError:
                    pass
        self.assertEqual(len(closed), 3)


class PrivateGroupCacheTests(unittest.TestCase):
    """A proven answer is kept for the session; a failed lookup (a directory service restarting) is asked again."""

    def setUp(self):
        linux._PRIVATE_GROUPS.clear()
        self.addCleanup(linux._PRIVATE_GROUPS.clear)
        user = SimpleNamespace(pw_name="ana", pw_uid=4242, pw_gid=4242)
        self.calls = []
        self.pwd = SimpleNamespace(getpwuid=lambda uid: self.calls.append(uid) or user, getpwall=lambda: [user])
        self.grp = SimpleNamespace(getgrgid=lambda gid: SimpleNamespace(gr_name="ana", gr_mem=[]))

    def test_a_proven_answer_is_asked_once_per_session(self):
        with mock.patch.dict(sys.modules, {"grp": self.grp, "pwd": self.pwd}):
            self.assertTrue(linux.private_group(4242, 4242))
            self.assertTrue(linux.private_group(4242, 4242))
        self.assertEqual(len(self.calls), 1)

    def test_a_failed_lookup_is_never_kept_for_the_session(self):
        def down(gid):
            raise OSError("sssd restarting")
        with mock.patch.dict(sys.modules, {"grp": SimpleNamespace(getgrgid=down), "pwd": self.pwd}):
            self.assertFalse(linux.private_group(4242, 4242), "a failure counts as shared for now")
        with mock.patch.dict(sys.modules, {"grp": self.grp, "pwd": self.pwd}):
            self.assertTrue(linux.private_group(4242, 4242), "and is asked again")


class RecoveryNoteTests(LinuxCase):
    def test_the_kept_folder_and_note_are_named_now_and_never_reuse_a_name(self):
        from angelo_agent import update_card
        root = self.installed()
        kept, note, text = update_card.linux_recovery(root, "1.0.1")
        self.assertEqual((Path(kept).parent, Path(note).parent), (root.parent, root.parent))
        self.assertTrue(Path(kept).name.startswith(root.name + " "))
        self.assertTrue(note.endswith(".txt"))
        self.assertIn(kept, text)
        self.assertIn(str(root), text)
        Path(kept).mkdir()
        Path(note).write_text("an earlier stop")
        kept2, note2, _ = update_card.linux_recovery(root, "1.0.1")
        self.assertNotIn(kept2, (kept,))
        self.assertNotEqual(note2, note)
        self.assertTrue(note2.endswith(".txt"))


class EveryLanguageRecoveryTests(LinuxCase):
    """The fallback's kept folder and note, under every shipped catalogue: real file names in reading order, the note
    a .txt whose text names both paths exactly, and both found again by Angelo's record (never by their text)."""

    def catalogues(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/angelo"))
        from i18n import po
        folder = Path(__file__).resolve().parents[3] / "locale/angelo/po"
        paths = sorted(folder.glob("*.po"))
        self.assertGreaterEqual(len(paths), 20)
        for path in paths:
            yield path.stem, {entry.id: entry.str for entry in po.read(str(path))[1] if entry.str and not entry.ctxt}

    def test_every_language_names_the_files_and_cleans_them_up(self):
        from angelo_agent import app_version, ui_text, update_card, update_flow
        for language, table in self.catalogues():
            with self.subTest(language):
                shutil.rmtree(self.folder / "Apps", ignore_errors=True)
                root = self.installed()
                work = self.folder / "updates" / language
                work.mkdir(parents=True)
                lookup = lambda message, context=None, table=table: table.get(message, message)
                with mock.patch.dict(ui_text._pgettext, {"iface": lookup}):
                    kept, note, text = update_card.linux_recovery(root, "1.0.1")
                kept, note = Path(kept), Path(note)
                self.assertEqual((kept.parent, note.parent), (root.parent, root.parent))
                self.assertTrue(note.name.endswith(".txt"), note.name)
                self.assertIn(str(kept), text)
                self.assertIn(str(root), text.replace(str(kept), ""),
                              "the path to rename back to, exactly as it is on disk, on its own")
                self.assertFalse(any("\ufb50" <= ch <= "\ufeff" for ch in text + kept.name + note.name),
                                 "never Blender's pre-shaped visual form on disk")
                update_flow.record_recovery(work, root, "1.0.0", kept, note, text)
                self.assertLess(app_version.parse("1.0.0"), app_version.parse(app_version.current()))
                # The fallback, as the script does it: the old folder kept, the new one in place, the note written.
                root.rename(kept)
                release_tree(self.folder / "Apps", "1.0.0", "new", self.key)
                note.write_bytes(os.fsencode(text))
                (work / "previous.txt").write_text(str(kept) + "\n", encoding="utf-8")
                self.assertTrue(update_flow.remove_previous(work, root))
                self.assertFalse(kept.exists(), "the previous release is removed")
                self.assertFalse(note.exists(), "the note goes once the app it names runs again")

    def test_a_stopped_attempt_leaves_no_stale_note(self):
        from angelo_agent import update_flow
        root = self.installed()
        work = self.folder / "updates" / "stopped"
        work.mkdir(parents=True)
        note = root.parent / "note - interrupted.txt"
        text = "Your previous Angelo is in: %s\nRename it back to: %s\n" % (root.parent / "kept", root)
        note.write_bytes(os.fsencode(text))
        update_flow.record_recovery(work, root, "1.0.0", root.parent / "kept", note, text)
        self.assertTrue(update_flow.forget_note(work, root))
        self.assertFalse(note.exists())


if __name__ == "__main__":
    unittest.main()
