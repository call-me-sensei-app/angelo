# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``shared_replace``: a replace that lands while a reader of ours holds the file, retried while another holds it.

Windows refuses ``os.replace`` onto a name any handle holds open (``[WinError 5]``). On a Windows host these tests
hold real handles (``ntfs_sharing.REAL``); elsewhere ``ntfs_sharing`` applies NTFS's sharing rules to the host's
real files. ``test_native_shared_replace_sites`` pins where the add-on calls it; ``..._callers`` its callers.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))

from angelo_agent import session_journal, shared_replace  # noqa: E402
import ntfs_sharing  # noqa: E402
from host_test_policy import environment_skip  # noqa: E402
import thread_work  # noqa: E402

ALL_PAUSES = [shared_replace.PAUSE * attempt for attempt in range(1, shared_replace.ATTEMPTS)]


class Case(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.target = self.folder / "state.json"
        self.target.write_bytes(b"old")
        self.sharing = ntfs_sharing.Sharing(self)

    def new(self, data=b"new"):
        source = self.folder / ("state.%d.tmp" % len(list(self.folder.iterdir())))
        source.write_bytes(data)
        return source


class WindowsRulesTests(Case):
    """The stand-in is faithful: what the old code did (a bare ``os.replace``) is refused as on NTFS."""

    def test_a_bare_replace_is_refused_while_any_reader_holds_the_file(self):
        for reader in (self.sharing.hold, lambda path: session_journal.open_shared(path)):
            with self.subTest(reader=reader):
                fd = reader(self.target)
                try:
                    with self.assertRaises(PermissionError) as refused:
                        os.replace(self.new(), self.target)
                    self.assertIn(getattr(refused.exception, "winerror", None), (5, 32))
                finally:
                    self.sharing.let_go(fd)
        self.assertEqual(self.target.read_bytes(), b"old")


class ReplaceTests(Case):
    def test_a_reader_of_ours_never_refuses_it_and_keeps_the_file_it_opened(self):
        fd = shared_replace.open_shared(self.target)
        try:
            shared_replace.replace(self.new(), self.target)
            with open(fd, "rb", closefd=False) as handle:
                self.assertEqual(handle.read(), b"old")   # POSIX semantics: the reader's file, unchanged.
        finally:
            os.close(fd)
        self.assertEqual((self.target.read_bytes(), self.sharing.pauses), (b"new", []))

    def test_a_moment_held_by_another_program_is_waited_out(self):
        self.sharing.hold(self.target, refusals=2)
        shared_replace.replace(self.new(), self.target)
        self.assertEqual(self.target.read_bytes(), b"new")
        self.assertEqual(self.sharing.pauses, ALL_PAUSES[:2])

    def test_one_held_for_good_is_raised_after_bounded_tries_and_the_old_file_stays(self):
        self.sharing.hold(self.target)
        source = self.new()
        with self.assertRaises(PermissionError):
            shared_replace.replace(source, self.target)
        self.assertEqual(self.sharing.pauses, ALL_PAUSES)   # 4 pauses, 100 ms in all: bounded.
        self.assertEqual((self.target.read_bytes(), source.read_bytes()), (b"old", b"new"))

    def test_every_save_gets_its_retries_and_a_refusal_is_told_apart(self):
        """No memory of earlier refusals: a save that must land (a finished job's) gets its retries even right after
        one was refused; ``Refused`` (a ``PermissionError``) tells a holder's refusal from any other error, so a
        caller that tries again from a tick can wait ``RETRY_SECONDS`` first."""
        held = self.sharing.hold(self.target)
        for _ in range(2):
            del self.sharing.pauses[:]
            with self.assertRaises(shared_replace.Refused) as refused:
                shared_replace.replace(self.new(), self.target)
            self.assertIsInstance(refused.exception, PermissionError)
            self.assertEqual(self.sharing.pauses, ALL_PAUSES)
        self.sharing.let_go(held)
        self.sharing.hold(self.target, refusals=1)
        shared_replace.replace(self.new(), self.target)
        self.assertEqual(self.target.read_bytes(), b"new")

    def test_a_folder_never_moves_onto_a_name_in_use(self):
        """Windows' ``MoveFileExW`` replaces files only: a folder onto any existing name is refused for good."""
        for existing in ("file", "folder"):
            with self.subTest(onto=existing):
                folder = self.folder / ("plugin.%s.new" % existing)
                folder.mkdir()
                taken = self.folder / ("taken.%s" % existing)
                taken.mkdir() if existing == "folder" else taken.write_bytes(b"x")
                del self.sharing.pauses[:]
                with self.assertRaises(FileExistsError):
                    shared_replace.replace(folder, taken)
                self.assertEqual((self.sharing.pauses, folder.is_dir()), ([], True))

    def test_a_folder_moves_and_a_folder_in_a_files_place_is_never_waited_for(self):
        folder = self.folder / "plugin.new"
        folder.mkdir()
        (folder / "a.txt").write_text("a")
        shared_replace.replace(folder, self.folder / "plugin")
        self.assertEqual((self.folder / "plugin" / "a.txt").read_text(), "a")
        in_the_way = self.folder / "taken.json"
        in_the_way.mkdir()
        with self.assertRaises(IsADirectoryError):   # Windows answers access denied: never waited for.
            shared_replace.replace(self.new(), in_the_way)
        self.assertEqual(self.sharing.pauses, [])

    def test_a_folder_with_a_file_held_inside_is_waited_out(self):
        folder = self.folder / "plugin.new"
        folder.mkdir()
        (folder / "a.txt").write_text("a")
        self.sharing.hold(folder / "a.txt", refusals=1)
        shared_replace.replace(folder, self.folder / "plugin")
        self.assertTrue((self.folder / "plugin" / "a.txt").is_file())
        self.assertEqual(self.sharing.pauses, ALL_PAUSES[:1])

    def test_retried_gives_another_rename_the_same_bounded_tries(self):
        calls = []

        def anchored(source, target):
            calls.append((source, target))
            if len(calls) < 3:
                raise PermissionError(13, "[WinError 32] in use")
        shared_replace.retried(anchored, "a", "b")
        self.assertEqual((len(calls), self.sharing.pauses), (3, ALL_PAUSES[:2]))

    def test_without_posix_semantics_even_a_shared_reader_is_waited_for_then_raised(self):
        """FAT, exFAT, some shares: ``win32_replace`` falls back to ``MoveFileExW``, which any handle refuses."""
        if ntfs_sharing.REAL:
            self.skipTest(environment_skip("the stand-in's fallback; a Windows runner's NTFS has POSIX semantics"))
        fd = shared_replace.open_shared(self.target)
        with patch.object(shared_replace.win32_replace, "_posix",
                          side_effect=shared_replace.win32_replace.Unsupported("Windows error 87")):
            with self.assertRaises(PermissionError):
                shared_replace.replace(self.new(), self.target)
        self.sharing.let_go(fd)
        self.assertEqual((self.target.read_bytes(), self.sharing.pauses), (b"old", ALL_PAUSES))

    @unittest.skipUnless(hasattr(os, "symlink"), "no symbolic links on this host")
    def test_a_link_moved_into_place_is_the_link_never_the_file_it_names(self):
        """Meaningful on a Windows host (``win32_replace`` opens the link itself); elsewhere ``os.replace``'s own."""
        named = self.folder / "named.txt"
        named.write_text("kept")
        link = self.folder / "link.tmp"
        try:
            os.symlink(named, link)
        except OSError as error:   # Windows without the privilege to make links.
            self.skipTest("links cannot be made here: %s" % error)
        shared_replace.replace(link, self.target)
        self.assertTrue(self.target.is_symlink())
        self.assertEqual((named.read_text(), self.target.read_text()), ("kept", "kept"))

    def test_the_readers_read_through_the_shared_handle(self):
        with patch.object(session_journal, "open_shared", wraps=session_journal.open_shared) as shared:
            self.assertEqual(shared_replace.read_bytes(self.target), b"old")
            self.assertEqual(shared_replace.read_text(self.target), "old")
            with shared_replace.open_file(self.target) as handle:
                self.assertEqual(handle.read(), b"old")
        self.assertEqual(shared.call_count, 3)


class FolderAtTheNameTests(unittest.TestCase):
    @unittest.skipUnless(os.path.isdir("/dev/fd"), environment_skip("descriptors are counted through /dev/fd"))
    def test_a_read_of_a_folder_names_it_and_leaks_no_descriptor(self):
        with tempfile.TemporaryDirectory() as folder:
            before = len(os.listdir("/dev/fd"))
            for _ in range(20):
                with self.assertRaises(OSError) as raised:
                    shared_replace.read_bytes(folder)
                self.assertEqual(raised.exception.filename, folder)
            self.assertLessEqual(len(os.listdir("/dev/fd")), before)


class SuccessPathCostTests(unittest.TestCase):
    """The success path's cost, counted (the helper is on save paths): POSIX, one Python call over ``os.replace``."""

    @unittest.skipIf(os.name == "nt", environment_skip("Windows renames through win32_replace: its cost is its own module's"))
    def test_posix_adds_one_call_and_no_system_call(self):
        with tempfile.TemporaryDirectory() as folder:
            target, sources = os.path.join(folder, "t"), [os.path.join(folder, "s%d" % index) for index in range(4)]
            for source in sources:
                Path(source).write_bytes(b"x")
            os.replace(sources[2], target)   # Warm: both measured calls find everything as it will be.
            shared_replace.replace(sources[3], target)
            _value, bare = thread_work.count(lambda: os.replace(sources[0], target))
            _value, helper = thread_work.count(lambda: shared_replace.replace(sources[1], target))
        self.assertEqual((helper.calls - bare.calls, helper.c_calls - bare.c_calls), (1, 0))
        self.assertLessEqual(helper.instructions - bare.instructions, 12)


class StandaloneTests(unittest.TestCase):
    def test_a_background_angelos_plain_script_imports_it_as_a_sibling(self):
        """``studio_assets_convert`` runs outside the add-on package, with its folder on ``sys.path``."""
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import shared_replace; "
                "print(shared_replace.win32_replace.__name__)")
        result = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(ADDON)], capture_output=True, text=True,
                                timeout=60)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "win32_replace"), result.stderr)


@unittest.skipUnless(ntfs_sharing.REAL, environment_skip("real NTFS handles: a Windows host"))
class RealWindowsTests(unittest.TestCase):
    """On a Windows host, outside the stand-in: Python's own ``open`` refuses a bare replace; the helper lands."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.target = Path(folder.name) / "state.json"
        self.target.write_bytes(b"old")
        self.source = Path(folder.name) / "state.tmp"
        self.source.write_bytes(b"new")

    def test_a_python_reader_refuses_os_replace_but_not_a_shared_one(self):
        with open(self.target, "rb"):
            with self.assertRaises(PermissionError):
                os.replace(self.source, self.target)
        fd = shared_replace.open_shared(self.target)
        try:
            shared_replace.replace(self.source, self.target)
        finally:
            os.close(fd)
        self.assertEqual(self.target.read_bytes(), b"new")

    def test_the_success_path_costs_about_what_os_replace_does(self):
        """A receipt in the log (no bound: a shared runner's clock): the helper's rename against ``os.replace``."""
        import statistics
        import time

        def timed(function):
            spans = []
            for index in range(200):
                self.source.write_bytes(b"%d" % index)
                start = time.perf_counter_ns()
                function(self.source, self.target)
                spans.append(time.perf_counter_ns() - start)
            return statistics.median(spans) / 1000.0
        bare, helper = timed(os.replace), timed(shared_replace.replace)
        print("shared_replace success path on this host: %.1f us median, os.replace %.1f us (200 each)"
              % (helper, bare), file=sys.stderr)
        self.assertEqual(self.target.read_bytes(), b"199")

    def test_a_python_reader_held_for_good_is_raised_after_the_retries(self):
        with patch.object(shared_replace.time, "sleep"), open(self.target, "rb"):
            with self.assertRaises(PermissionError):
                shared_replace.replace(self.source, self.target)
        self.assertEqual(self.target.read_bytes(), b"old")


class TellingTests(Case):
    """A refusal the retries could not wait out is told to the person once per file name and pause."""

    def test_a_refusal_is_told_with_its_file_and_a_card_names_it_once_per_pause(self):
        from angelo_agent import refused_saves
        told = []
        self.sharing.hold(self.target)
        with patch.object(shared_replace, "on_refused", told.append):
            with self.assertRaises(shared_replace.Refused):
                shared_replace.replace(self.new(), self.target)
        self.assertEqual([os.fspath(target) for target in told], [os.fspath(self.target)])
        aside = self.folder / "state.previous"
        with patch.object(shared_replace, "on_refused", told.append), self.assertRaises(shared_replace.Refused):
            shared_replace.replace(self.target, aside, shown=self.target)   # A move aside names the file it moves.
        self.assertEqual(os.fspath(told[-1]), os.fspath(self.target))
        source = (ADDON / "refused_saves.py").read_text(encoding="utf-8")
        self.assertIn("card.kind = KIND + name", source, "a card per file: never in place of another notice")
        told.clear()
        self.sharing.let_go_all()
        held = self.sharing.hold(self.target)
        with patch.object(shared_replace, "on_refused", told.append), self.assertRaises(shared_replace.Refused):
            shared_replace.replace(self.new(), self.target, told=False)   # Its caller tells the person itself.
        self.sharing.let_go(held)
        self.assertEqual(told, [])
        with patch.dict(refused_saves._told, clear=True), patch.object(
                refused_saves, "_heard", type(refused_saves._heard)(maxlen=refused_saves._heard.maxlen)):
            for _ in range(3):
                refused_saves.heard(self.target)
            self.assertEqual(refused_saves.due(now=1000.0), ["state.json"], "once, however often refused")
            refused_saves.heard(self.target)
            self.assertEqual(refused_saves.due(now=1001.0), [])
            refused_saves.heard(self.target)
            self.assertEqual(refused_saves.due(now=1000.0 + refused_saves.AGAIN), ["state.json"])
            for number in range(5):
                refused_saves.heard(self.folder / ("job-%d.json" % number))
            self.assertEqual(refused_saves.due(now=5000.0, limit=3), ["job-0.json", "job-1.json", "job-2.json"])
            self.assertEqual(refused_saves.due(now=5001.0), ["job-3.json", "job-4.json"], "told later, never lost")

    def test_a_save_that_lands_or_any_other_error_is_never_told(self):
        told = []
        self.sharing.hold(self.target, refusals=1)
        with patch.object(shared_replace, "on_refused", told.append):
            shared_replace.replace(self.new(), self.target)   # Waited out: landed.
            with self.assertRaises(FileNotFoundError):
                shared_replace.replace(self.folder / "missing.tmp", self.target)
        self.assertEqual(told, [])


if __name__ == "__main__":
    unittest.main()
