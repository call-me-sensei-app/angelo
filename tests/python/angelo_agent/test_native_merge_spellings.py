# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How a merge's privacy rule spells Angelo's folders and what its reading costs (``worker_merge_spellings``,
``worker_merge_strings``, ``worker_merge_verify``; break-it E2E-20 F-L-2, BREAKIT-C7A-9), without Blender.

Covered: a folder spelled through any environment variable whose value holds it (``$V``, ``${V}``, ``%V%``,
``$env:V``, the variable's name set here, never one the code knows), from the home folder as ``~user``, and by its
8.3 short name on Windows, is found by the cleaning and the check; text that names a folder densely with ".." and
near-matches costs its length (the review's "/state/" and "../../angelo/store" lines took 33 s a megabyte); a value
larger than a window is cleaned a window at a time, every place cut wherever it falls, in memory a small multiple of
the value, never the value decoded and folded whole (a 1 GB property peaked at 12.9 GB); and every DNA field that can
hold packed bytes is one the check finds by the file's own DNA, which this test reads, so a new one fails here.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import os
import re
import shutil
import struct
import sys
import tempfile
import tracemalloc
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import (private_roots, worker_merge_paths, worker_merge_spellings, worker_merge_strings,  # noqa: E402
                          worker_merge_verify)
from angelo_agent.worker_merge_scan import BytesReader, Scanner  # noqa: E402
import machine_clock  # noqa: E402


def blender_paths():
    """``bpy`` as the cleaning uses it outside Blender (``bpy.path.abspath``): an absolute path as it is; a path relative
    to a .blend ("//") is refused, since no test here resolves one through Blender."""
    from types import SimpleNamespace

    def abspath(path, library=None):
        if path.startswith("//"):
            raise AssertionError("resolved through Blender in a host test: %r" % path)
        return path
    return SimpleNamespace(path=SimpleNamespace(abspath=abspath))


# Every DNA field that holds packed bytes, by header: the check finds each by its ``PackedFile`` struct in the written
# file (``worker_merge_verify``), whatever field holds it. A new one is listed here once its owner's merge is decided.
PACKED_FIELDS = {
    "DNA_ID.h": {"packedfile"},   # A Library's packed .blend: a worker's library is made local, never kept.
    "DNA_image_types.h": {"packedfile", "packedfiles", "autosave_packedfiles"},   # Written again, or refused.
    "DNA_modifier_types.h": {"packed_file"},   # A geometry-nodes bake's file: refused (BREAKIT-C7A-9 H2).
    "DNA_sound_types.h": {"packedfile", "newpackedfile"},   # Written again, or refused.
    "DNA_vfont_types.h": {"packedfile", "temp_pf"},   # Never kept.
    "DNA_volume_types.h": {"packedfile"},   # Refused.
}


class MergeSpellingsTest(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="angelo-spell-"))
        self.home = self.base / "Angelo Test Ä"
        self.state = self.home / "state"
        (self.state / "sessions").mkdir(parents=True)
        (self.state / "workspace").mkdir()
        self.secret = self.state / "sessions" / "chat.json"
        patches = [mock.patch.object(private_roots, "registered", return_value=[]),
                   mock.patch.object(private_roots, "families", return_value=[]),
                   mock.patch.object(worker_merge_paths, "_workspace", return_value=self.state / "workspace")]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(shutil.rmtree, self.base, True)

    def folders(self, *roots):
        with mock.patch.object(worker_merge_paths, "_registry", return_value=list(roots or [self.state])):
            return worker_merge_paths.PrivateFolders([self.base / "angelo-part-k4m2z8" / "part.blend"])

    def found(self, data, private):
        return Scanner(private).hit(BytesReader(data), 0, len(data))

    def test_a_folder_spelled_through_an_environment_variable_is_found(self):
        # A folder the private one is in, two up; the private folder itself; a folder inside it (BREAKIT-C7A-10 M1).
        for name, value in (("ANGELO_SPELL_TEST_ROOT", self.home), ("ANGELO_SPELL_TEST_PARENT", self.base),
                            ("ANGELO_SPELL_TEST_EXACT", self.state),
                            ("ANGELO_SPELL_TEST_INSIDE", self.state / "sessions")):
            tail = os.path.relpath(self.secret, value)
            with mock.patch.dict(os.environ, {name: str(value)}):
                private = self.folders()
                for spelled in ("$%s/%s", "${%s}/%s", "%%%s%%\\%s", "$env:%s/%s"):
                    text = spelled % (name, tail.replace(os.sep, "\\" if "%%" in spelled else "/"))
                    with self.subTest(variable=name, spelled=text):
                        self.assertIsNotNone(self.found(('cat "%s" now\n' % text).encode(), private))
                        self.assertTrue(private.holds(text))
            self.assertIsNone(self.found(('cat "$%s/%s"\n' % (name, tail)).encode(), self.folders()))   # Unset.

    def test_the_home_folder_spelled_with_its_user_is_a_spelling(self):
        home = os.path.expanduser("~")
        user = os.path.basename(home)
        found = worker_merge_spellings.spellings(Path(home) / "angelo-spell" / "state")
        if user and os.path.expanduser("~" + user) == home:
            self.assertIn("~%s%sangelo-spell%sstate" % (user, os.sep, os.sep), found)
        self.assertIn("~%sangelo-spell%sstate" % (os.sep, os.sep), found)

    def test_a_short_name_is_a_spelling(self):
        folder = self.base / "angelo long folder name"
        folder.mkdir()
        short = worker_merge_spellings._short(folder)   # Windows: its 8.3 name where the volume keeps one.
        self.assertIn(str(short), worker_merge_spellings.spellings(folder))
        if str(short) != str(folder):
            private = self.folders(folder)
            self.assertIsNotNone(self.found(("see %s\\x.png\n" % short).encode(), private))

    def test_dense_near_matches_cost_their_length(self):
        """Lines of near-matches, each place read back without reading the text again: "/state/" forty times then
        "../" forty times, and imports through "../../angelo/store"; 1 MB of each takes about a tenth of a second on
        the nominal machine (the review measured 33 s and 5.4 s a megabyte)."""
        (self.base / "angelo").mkdir()
        private = self.folders(self.state, self.base / "angelo")
        lines = {"state_detours": "/state/" * 40 + "../" * 40 + "\n",
                 "imports": "".join("import ../../angelo/store%d\n" % index for index in range(50))}
        for label, line in lines.items():
            text = line * (1_000_000 // len(line))
            with self.subTest(text=label):
                self.assertEqual(machine_clock.assert_cost(self, lambda: private.finds(text), 1.0, label), [])
                self.assertIsNone(self.found(text.encode(), private))

    def test_a_newline_free_line_of_alternating_detours_costs_its_length(self):
        """One line, no newline, of "/state/.." again and again (every "state" a place, every ".." taking it back, so a
        read back never mismatches), alone and after the private folder's own spelling (every place then a private
        path): each place reads back only its spelling's folders and each cut is read once, in the finds, the
        cleaning of a short and a long value and the check (port review SHOULD 1: about 10^9 steps a megabyte, on
        the main thread). A quarter of a megabyte takes a few hundredths of a second on the nominal machine."""
        private = self.folders(self.state)
        size = 250_000
        alternating = "/state/.." * (size // 9)
        after = str(self.state) + "/../state" * (size // 9)
        long_after = after * (worker_merge_strings.LARGE // len(after) + 2)
        # In turn, never as subtests: the first cost past its budget ends the test (a backtracking read takes minutes).
        blender = mock.patch.dict(sys.modules, {"bpy": blender_paths()})
        blender.start()
        self.addCleanup(blender.stop)
        for label, text, private_path in (("alternating", alternating, False), ("after_a_private_folder", after, True)):
            found = machine_clock.assert_cost(self, lambda: private.finds(text), 0.5, label + " finds")
            self.assertEqual(bool(found), private_path, label)
            hit = machine_clock.assert_cost(self, lambda: self.found(text.encode(), private), 0.5, label + " check")
            self.assertEqual(hit is not None, private_path, label)
            cleaned = machine_clock.assert_cost(self, lambda: private.clean(text, None, False), 0.5, label + " cleaning")
            self.assertEqual(cleaned is not None, private_path, label)
        cleaned = machine_clock.assert_cost(self, lambda: worker_merge_strings._clean_text(long_after, None, private),
                                            2.0, "a long value of detours after a private folder")
        self.assertNotIn(str(self.state), cleaned)

    def test_a_place_costs_its_own_folders_however_many_spellings_end_there(self):
        """Two hundred state folders in the registry (every state folder ever used is: BREAKIT-C7A-3 H3), so at least
        400 spellings end in "state" on every platform (Linux spells a folder twice, macOS a dozen ways, Windows more).
        The newline-free alternating line costs what it does with one folder: each place reads its own folders once
        and its spellings' shared pieces, never each spelling (VM CI on Windows, 4d5d7168d10: 0.57 s against 0.5;
        here 2.2 s before the fix)."""
        states = [self.base / ("Angelo Test %d" % number) / "state" for number in range(200)]
        for state in states:
            (state / "sessions").mkdir(parents=True)
        private = self.folders(*states)
        self.assertGreaterEqual(len(private.needles), 400)
        size = 250_000
        for label, text, private_path in (("alternating", "/state/.." * (size // 9), False),
                                          ("after_a_private_folder", str(states[7]) + "/../state" * (size // 9), True)):
            found = machine_clock.assert_cost(self, lambda: private.finds(text), 0.5, label + " finds, 200 folders")
            self.assertEqual(bool(found), private_path, label)

    def test_a_large_value_dense_with_paths_costs_its_length(self):
        """A path every 200 bytes in an 8 MB bytes value with letters UTF-8 spells in two bytes: each window's places
        located in one pass (BREAKIT-C7A-10 M3: 108 s, each place encoding its window's prefix again); about a quarter
        of a second on the nominal machine."""
        private = self.folders()
        unit = ("café note " * 15 + str(self.secret) + "\n").encode()
        value = unit * (8_000_000 // len(unit))
        cleaned = machine_clock.assert_cost(self, lambda: worker_merge_strings._clean_text(value, None, private), 2.0,
                                            "a dense 8 MB bytes value")
        self.assertEqual(cleaned, value.replace(str(self.secret).encode(), b"chat.json"))

    def test_a_large_value_is_cleaned_a_window_at_a_time(self):
        """Paths placed across the windows' edges, in a value three windows and more long: each cut, nothing else
        changed, in memory a small multiple of the value."""
        large = worker_merge_strings.LARGE
        private = self.folders()
        path = str(self.secret)
        filler = b"x" * 97 + b"\n"
        value, expected = bytearray(), bytearray()
        for edge in (large - 40, 2 * large - 10, 3 * large + 3, 4 * large - len(path) // 2):
            gap = filler * ((edge - len(value)) // len(filler)) + b"y" * ((edge - len(value)) % len(filler))
            value += gap + path.encode() + b" "
            expected += gap + b"chat.json "
        value += filler * 1000
        expected += filler * 1000
        tracemalloc.start()
        try:
            cleaned = worker_merge_strings._clean_text(bytes(value), None, private)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertEqual(cleaned, bytes(expected))
        self.assertLess(peak, 3 * len(value) + 16 * large, peak)
        text = bytes(value).decode()
        self.assertEqual(worker_merge_strings._clean_text(text, None, private), bytes(expected).decode())

    def test_every_dna_field_that_holds_packed_bytes_is_known(self):
        folder = ROOT / "source" / "blender" / "makesdna"
        found = {}
        for header in sorted(folder.glob("DNA_*.h")):
            text = header.read_text(encoding="utf-8", errors="replace")
            for match in re.finditer(r"^\s*(?:DNA_DEPRECATED\s+)?(?:struct\s+)?(?:PackedFile\s*\*|"
                                     r"ListBaseT<ImagePackedFile>)\s*(\w+)", text, re.MULTILINE):
                found.setdefault(header.name, set()).add(match.group(1))
        self.assertEqual(found, PACKED_FIELDS, "A DNA field that holds packed bytes was added or renamed: decide "
                                               "how a merge keeps its owner (worker_merge_media), then list it here")

    def test_the_check_reads_struct_names_from_a_files_dna(self):
        def strings(items):
            data = b"".join(item + b"\0" for item in items)
            return data + b"\0" * (-len(data) % 4)
        types = [b"char", b"int", b"Link", b"PackedFile"]
        body = (b"SDNANAME" + struct.pack("<i", 2) + strings([b"*next", b"size"]) + b"TYPE" + struct.pack("<i", 4)
                + strings(types) + b"TLEN" + struct.pack("<4h", 1, 4, 16, 24) + b"STRC" + struct.pack("<i", 2)
                + struct.pack("<hh", 2, 1) + struct.pack("<hh", 2, 0) + struct.pack("<hh", 3, 1)
                + struct.pack("<hh", 1, 1))
        self.assertEqual(worker_merge_verify._struct_names(body), {0: b"Link", 1: b"PackedFile"})
        self.assertEqual(worker_merge_verify._struct_names(b"not dna"), {})


if __name__ == "__main__":
    unittest.main()
