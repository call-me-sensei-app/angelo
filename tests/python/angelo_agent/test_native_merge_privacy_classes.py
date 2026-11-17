# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Two classes the merge check's reviews 7-10 closed (F-L-2, BREAKIT-C7A-7 L1 and C7A-9/10), judged only through what
every version of the check has (``worker_merge_paths.PrivateFolders`` and ``worker_merge_verify.holds_private``), so
this test fails on a check without the fix and passes with it (the 2.0.0 port of PR 195).

Covered: a line with many ``..`` detours is read in time that grows with its length, never by a pattern that
backtracks (14 hops took 90 s on the main thread, 20 never ended), and the path it leads to is found; a private
folder spelled through an environment variable (``$NAME``, ``${NAME}``, ``%NAME%``, ``$env:NAME``), whether the variable
names the folder, a folder above it or one inside it, is found in written bytes and in text, and nothing is found
when the variable is unset.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).
from host_waits import CAP_S  # noqa: E402  A completion wait's cap: only a hang reaches it.

import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import private_roots, worker_merge_paths, worker_merge_verify  # noqa: E402

def blender_paths():
    """``bpy`` as the cleaning uses it outside Blender (``bpy.path.abspath``): an absolute path as it is; a path relative
    to a .blend ("//") is refused, since no test here resolves one through Blender."""
    from types import SimpleNamespace

    def abspath(path, library=None):
        if path.startswith("//"):
            raise AssertionError("resolved through Blender in a host test: %r" % path)
        return path
    return SimpleNamespace(path=SimpleNamespace(abspath=abspath))


# The check of one line, in a process of its own: a pattern that backtracks holds the interpreter until it ends, so
# only a process this test can end bounds it.
DETOURS = r"""
import json, sys
from pathlib import Path
from unittest import mock
sys.path.insert(0, sys.argv[1])
from angelo_agent import private_roots, worker_merge_paths, worker_merge_verify
spec = json.loads(sys.argv[2])
with mock.patch.object(private_roots, "registered", return_value=[]), \
        mock.patch.object(private_roots, "families", return_value=[]), \
        mock.patch.object(worker_merge_paths, "_workspace", return_value=Path(spec["workspace"])), \
        mock.patch.object(worker_merge_paths, "_registry", return_value=[Path(spec["state"])]):
    private = worker_merge_paths.PrivateFolders([Path(spec["part"])])
    found = [list(item) for item in private.finds(spec["line"])]
    held = worker_merge_verify.holds_private(private, spec["line"].encode())
print(json.dumps({"found": bool(found), "held": held}))
"""


class MergePrivacyClassesTest(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="angelo-classes-"))
        self.home = self.base / "Angelo Test"
        self.state = self.home / "state"
        (self.state / "sessions").mkdir(parents=True)
        (self.state / "workspace").mkdir()
        self.secret = self.state / "sessions" / "chat.json"
        self.part = self.base / "angelo-part-r7c2x5" / "part.blend"
        patches = [mock.patch.object(private_roots, "registered", return_value=[]),
                   mock.patch.object(private_roots, "families", return_value=[]),
                   mock.patch.object(worker_merge_paths, "_workspace", return_value=self.state / "workspace")]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(shutil.rmtree, self.base, True)

    def folders(self, *roots):
        with mock.patch.object(worker_merge_paths, "_registry", return_value=list(roots or [self.state])):
            return worker_merge_paths.PrivateFolders([self.part])

    def test_a_line_of_many_detours_is_read_in_time_and_its_path_found(self):
        hops = 200   # Linear: well under a millisecond each. Backtracking: never ends (20 hops never did).
        line = "see %s%s" % (str(self.home) + os.sep, "a/" * hops + "../" * hops + "state/sessions/h.json")
        spec = {"workspace": str(self.state / "workspace"), "state": str(self.state), "part": str(self.part),
                "line": line}
        try:
            done = subprocess.run([sys.executable, "-c", DETOURS, str(ROOT / "scripts/addons_core"), json.dumps(spec)],
                                  capture_output=True, text=True, timeout=CAP_S)
        except subprocess.TimeoutExpired:
            self.fail("a line of %d detours was still being read after %.0f s" % (hops, CAP_S))
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])
        self.assertEqual(json.loads(done.stdout.strip().splitlines()[-1]), {"found": True, "held": True})

    def test_a_folder_spelled_through_an_environment_variable_is_found(self):
        for name, value in (("ANGELO_CLASSES_TEST_ABOVE", self.home), ("ANGELO_CLASSES_TEST_EXACT", self.state),
                            ("ANGELO_CLASSES_TEST_INSIDE", self.state / "sessions")):
            tail = os.path.relpath(self.secret, value)
            with mock.patch.dict(os.environ, {name: str(value)}):
                private = self.folders()
                for spelled in ("$%s/%s", "${%s}/%s", "%%%s%%\\%s", "$env:%s/%s"):
                    text = spelled % (name, tail.replace(os.sep, "\\" if "%%" in spelled else "/"))
                    with self.subTest(variable=name, spelled=text):
                        self.assertTrue(worker_merge_verify.holds_private(private, ('cat "%s" now\n' % text).encode()))
                        self.assertTrue(private.holds(text))
            unset = self.folders()
            self.assertFalse(worker_merge_verify.holds_private(unset, ('cat "$%s/%s"\n' % (name, tail)).encode()))

    def test_a_detour_after_a_spelling_that_starts_no_separator_is_found_after_a_word(self):
        """A private folder spelled through a variable (or, on Windows, from a drive: ``C:\\...``), then through a
        folder and back, after a word on the same line ("see $HOME/a/../state/x"): its outermost name shares its run of
        text with the word, since only separators divide runs (VM CI on Windows, 4d5d7168d10: every drive path with a
        detour missed). Found and held, in every spelling."""
        name = "ANGELO_CLASSES_TEST_DETOUR"
        tail = os.path.relpath(self.secret, self.home).replace(os.sep, "/")
        with mock.patch.dict(os.environ, {name: str(self.home)}):
            private = self.folders()
            for spelled in ("$%s/a/../%s", "${%s}/a/../%s", "%%%s%%\\a\\..\\%s", "$env:%s/a/../%s"):
                path = spelled % (name, tail.replace("/", "\\") if "%%" in spelled else tail)
                for text in ("see " + path, 'cat "%s" now' % path, "x=" + path):
                    with self.subTest(text=text):
                        self.assertTrue(private.finds(text))
                        self.assertTrue(worker_merge_verify.holds_private(private, (text + "\n").encode()))

    def test_a_detour_spelling_is_found_wherever_its_run_starts(self):
        """Read as permissively as the plain backwards reader (a false find refuses, a miss leaks; re-read of
        ef03d0e1f83, MUST): the outermost name right after a word character ("-I$HOME/x/../"), and a run of
        separator-free text longer than a detour's reach before it on the same line. In UTF-8 and UTF-16LE."""
        name = "ANGELO_CLASSES_TEST_REACH"
        tail = os.path.relpath(self.secret, self.home).replace(os.sep, "/")
        with mock.patch.dict(os.environ, {name: str(self.home)}):
            private = self.folders()
            path = "$%s/x/../%s" % (name, tail)
            for text in ("-I" + path, "..." + path, "x" * 5000 + path, "see " + "y" * 5000 + path):
                with self.subTest(text=text[:40]):
                    self.assertTrue(private.finds(text))
                    for encoding in ("utf-8", "utf-16-le"):
                        self.assertTrue(worker_merge_verify.holds_private(private, (text + "\n").encode(encoding)),
                                        encoding)

    def test_an_encoded_backslash_spelling_keeps_a_folder_that_starts_with_a_mark(self):
        """A path spelled with backslashes and then encoded (``&#92;``, ``%5C``), through folders whose names start
        with a mark a backslash escapes (".config", "_cache", "-x", "~y"): undoing the encoding and the backslash
        escapes in one step read "\\.config" as ".config", joined to the folder before it, so no view held the path
        (the cut's macOS run: ".angelo-bench" under the home folder; Linux's "~/.local/share/angelo" alike). Each
        transport undone is a view of its own. In UTF-8 and UTF-16LE."""
        for lead in (".config", "_cache", "-x", "~y"):
            state = self.base / lead / "Angelo Test" / "state"
            (state / "sessions").mkdir(parents=True)
            private = self.folders(state)
            backslashed = (state / "sessions" / "out.obj").as_posix().replace("/", "\\")
            for text in (backslashed, "see " + backslashed + " now", backslashed.replace("\\", "&#92;"),
                         backslashed.replace("\\", "%5C"), "see " + backslashed.replace("\\", "&#x5C;") + " now"):
                for encoding in ("utf-8", "utf-16-le"):
                    with self.subTest(text=text[:60], encoding=encoding):
                        self.assertTrue(worker_merge_verify.holds_private(private, text.encode(encoding)))

    def test_a_path_relative_to_the_part_into_a_private_folder_is_found_where_it_resolves(self):
        # Blender's "//" (the part file's folder) in text no path field holds, into the state folder past a name
        # with a space (port review NIT: decided by a name's length, never by where it resolves).
        private = self.folders()
        relative = "//../%s/state/sessions/chat.json" % self.home.name
        text = "note: %s now" % relative
        self.assertTrue(private.finds(text))
        self.assertTrue(private.holds(relative))
        self.assertTrue(worker_merge_verify.holds_private(private, text.encode()))
        elsewhere = "note: //../elsewhere/x.png now"   # Relative too, and nowhere private: kept.
        self.assertFalse(private.finds(elsewhere))
        self.assertFalse(worker_merge_verify.holds_private(private, elsewhere.encode()))
        with mock.patch.dict(sys.modules, {"bpy": blender_paths()}):
            self.assertEqual(private.clean(text, None, False), "note: chat.json now")
            self.assertIsNone(private.clean(elsewhere, None, False))


class MergeBlockWalkTest(unittest.TestCase):
    """The merge check's walk over a written file's blocks (``worker_merge_verify``): a block after a kept datablock is
    its own data (``DATA``), a file-level block Blender writes, or the file cannot be checked (port review NIT: an
    unknown block code ended the datablock silently, so what it held was read as no one's)."""

    @staticmethod
    def written(after_code):
        def block(code, body, index=0):
            return struct.pack("<4siQqq", code, index, 0x1000, len(body), 1) + body
        identity = bytearray(400)   # ID: five pointers, then its name; ID.lib (the fourth pointer) left empty.
        identity[40:40 + 8] = b"OBKept\0\0"
        return (b"BLENDER17-01v0503" + block(b"OB\0\0", bytes(identity), 1)
                + block(after_code, b"/private/state/sessions/chat.json".ljust(64, b"\0"))
                + block(b"ENDB", b""))

    def test_an_unknown_block_after_a_kept_datablock_fails_closed(self):
        from angelo_agent.worker_merge_scan import BytesReader
        data = self.written(b"XYZW")
        with self.assertRaises(worker_merge_verify.Unchecked):
            worker_merge_verify._blocks(BytesReader(data))

    def test_its_own_data_block_is_read_as_its_bytes(self):
        from angelo_agent.worker_merge_scan import BytesReader
        data = self.written(b"DATA")
        found, _kinds = worker_merge_verify._blocks(BytesReader(data))
        self.assertEqual([(key, end) for key, _start, end, _structs in found], [((b"OB", b"Kept"), len(data) - 32)])


class ClearOverridesTest(unittest.TestCase):
    """A worker's library overrides made plain local data (``worker_merge_privacy._clear_overrides``): a hierarchy is
    cleared whole once any member of it is, so no override is left whose hierarchy root is now local data (port review
    NIT: a member whose own reference stays linked kept its override, its root cleared under it)."""

    def test_a_partly_cleared_hierarchy_is_cleared_whole_its_root_last(self):
        from types import SimpleNamespace
        from angelo_agent import worker_merge_privacy
        made, persons, cleared = SimpleNamespace(library=object()), SimpleNamespace(library=object()), []

        def override(name, reference, root=None):
            item = SimpleNamespace(name=name, library=None)
            item.override_library = SimpleNamespace(reference=reference, hierarchy_root=root or item)
            item.make_local = lambda clear_liboverride: cleared.append(name)
            return item
        root = override("Root", made)   # Its reference is made local: it is cleared.
        member = override("Member", persons, root)   # Its own reference stays linked (the person's library).
        other = override("Other", persons)   # Another hierarchy, nothing of it cleared: kept.
        worker_merge_privacy._clear_overrides([member, root, other], [made])
        self.assertEqual(cleared, ["Member", "Root"])


class SoundFileNameTest(unittest.TestCase):
    """A sound packed again as FLAC is written under a name taken from its own path, which is the part's (a script's)
    data, into a scratch folder C checked: the name must stay one plain file of that folder on every platform (port
    review of cb7eb030718: ``COM1.wav`` named the Windows serial device, ``ab:cd.wav`` a stream of another file)."""

    STEERED = ["COM1.wav", "C:\\x\\CON.wav", "//nul.mp3", "aux", "LPT9.flac", "ab:cd.wav", "..", "../../x.wav",
               "\\\\server\\share\\conin$.wav", "a/b\\c..wav", "", ".wav", "x" * 400 + ".wav", "\u97f3.wav",
               "\U00020000" * 100 + ".wav"]   # A letter UTF-8 spells in 4 bytes: the name's length is in bytes.
    DEVICES = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {"%s%d" % (k, n) for k in ("COM", "LPT")
                                                                    for n in range(10)}

    def test_a_sound_s_path_names_one_plain_file_of_the_folder(self):
        import ntpath
        import posixpath
        from angelo_agent import worker_merge_media
        for path in self.STEERED:
            name = "sound-%s.flac" % worker_merge_media._flac_stem(path)   # As ``_sound`` joins it.
            for folder, module in (("C:\\scratch\\3", ntpath), ("/scratch/3", posixpath)):
                target = module.join(folder, name)
                self.assertEqual(module.dirname(target), folder, (path, name))
                self.assertEqual(module.basename(target), name, (path, name))
            self.assertNotIn(":", name, path)
            self.assertFalse(set(name) & set("/\\"), path)
            self.assertNotIn(name.split(".")[0].upper().rstrip(" "), self.DEVICES, (path, name))
            self.assertTrue(name.endswith(".flac") and len(name.encode("utf-8")) <= 80, (path, name))


if __name__ == "__main__":
    unittest.main()
