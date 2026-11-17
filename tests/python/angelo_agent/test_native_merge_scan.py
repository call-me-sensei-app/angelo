# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The merge check reads written bytes of any size, streamed (``worker_merge_scan``; break-it E2E-20 F-L-2,
BREAKIT-C7A-7 H1 and L2), without Blender.

Covered: a private path in every spelling a text transport makes (as written, form- and percent-encoded, JSON- and
HTML-escaped, a ``file://`` URL, backslashes, doubled separators, UTF-16 either way round at either alignment) is
found after any number of bytes and wherever it falls against the chunks it is read in (the chunk made small here, so
every alignment is tried); an own folder's name anywhere; a last name in another script; a path through the default
workspace and back. Nothing is found in random bytes, in text that names the folders without a path, or in the
default workspace's and the person's own paths. Only the bytes around places found are read as text, the file is never
read whole, a separator run longer than the context is read through, and one longer than ``MARGIN`` fails closed.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import os
import random
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import quote, urlencode

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import private_roots, worker_merge_paths, worker_merge_scan  # noqa: E402
from angelo_agent.worker_merge_scan import BytesReader, Scanner, Unreadable  # noqa: E402
import machine_clock  # noqa: E402


class Counted(BytesReader):
    """A reader that records the largest read."""

    largest = 0

    def read(self, start, end):
        self.largest = max(self.largest, end - start)
        return super().read(start, end)


class MergeScanTest(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="angelo-scan-"))
        self.home = self.base / "Angelo Test Ä"   # A space and a letter JSON escapes.
        self.state = self.home / "state"
        (self.state / "sessions").mkdir(parents=True)
        (self.state / "workspace" / "My Renders").mkdir(parents=True)
        (self.home / "state backup").mkdir()   # The person's, beside the state folder: its file need not exist.
        (self.home / "state+backup").mkdir()   # And one whose name has a "+", which is not a space there.
        self.script = self.home / "データ"   # A folder whose name has no Latin letter.
        self.script.mkdir()
        self.part = self.home / "parts" / "angelo-part-q7w1e9"
        self.part.mkdir(parents=True)
        self.secret = str(self.state / "sessions" / "chat.json")
        patches = [mock.patch.object(private_roots, "registered", return_value=[]),
                   mock.patch.object(private_roots, "families", return_value=[]),
                   mock.patch.object(worker_merge_paths, "_workspace", return_value=self.state / "workspace")]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(shutil.rmtree, self.base, True)
        self.private = self.folders(self.state, self.script)

    def folders(self, *roots):
        with mock.patch.object(worker_merge_paths, "_registry", return_value=list(roots)):
            return worker_merge_paths.PrivateFolders([self.part / "part.blend"])

    def found(self, data, private=None):
        return Scanner(private or self.private).hit(BytesReader(data), 0, len(data))

    def spellings(self):
        path = self.secret
        forward = path.replace(os.sep, "/")
        return {"plain": path.encode(), "form": urlencode({"file": path}).encode(),
                "percent": quote(forward, safe="").encode(), "json": json.dumps({"src": path}).encode(),
                "html": forward.replace("/", "&#47;").encode(), "url": ("file://" + quote(forward)).encode(),
                "backslash": forward.replace("/", "\\").encode(), "doubled": forward.replace("/", "//").encode(),
                "utf16le": path.encode("utf-16-le"), "utf16be": path.encode("utf-16-be"),
                "script": str(self.script / "x.png").encode(),
                "script_wide": str(self.script / "x.png").encode("utf-16-le"),
                "own_name": b"note: " + self.part.name.upper().encode() + b" done",
                "detour": (str(self.state / "workspace" / "My Renders") + "/../../sessions/chat.json").encode(),
                # Held out (the review of this check, BREAKIT-C7A-7): a shell's escaped spaces, a form or HTML spelling
                # kept as UTF-16, the bytes of its letters as Python and git escape them.
                "shell": b"cp " + path.replace(" ", "\\ ").encode() + b" /tmp/x",
                "wide_form": urlencode({"file": path}).encode("utf-16-le"),
                "wide_html": forward.replace("/", "&#47;").encode("utf-16-be"),
                "python_bytes": repr(path.encode())[2:-1].encode(),
                "workspace_sibling": ("see " + str(self.state / "workspace backup" / "s.json") + " now").encode(),
                "git_octal": "".join(chr(byte) if byte < 128 else "\\%03o" % byte for byte in path.encode()).encode()}

    def test_every_spelling_found_after_any_size(self):
        filler = random.Random(7)
        for size in (0, 1, (1 << 20) + 3, 9 << 20):
            pad = bytes(filler.getrandbits(8) for _ in range(min(size, 4096))) * (size // 4096 + 1)
            for name, spelled in self.spellings().items():
                with self.subTest(size=size, spelling=name):
                    self.assertIsNotNone(self.found(pad[:size] + b" " + spelled + b" " + pad[:333]))

    def test_found_wherever_it_falls_against_the_chunks(self):
        with mock.patch.object(worker_merge_scan, "CHUNK", 4096), mock.patch.object(worker_merge_scan, "PIECE", 2048):
            for name, spelled in self.spellings().items():
                for offset in range(4096 - len(spelled) - 40, 4096 + 40, 3):
                    with self.subTest(spelling=name, offset=offset):
                        data = b"\0" * offset + spelled + b"\n" + b"z" * 9000
                        self.assertIsNotNone(self.found(data))

    def test_nothing_found_in_bytes_without_a_private_path(self):
        noise = random.Random(11)
        random_bytes = bytes(noise.getrandbits(8) for _ in range(1 << 20))
        words = "".join("frame %d: angelo state of the scene, angelo-part workspace ok\n" % index
                        for index in range(20000)).encode()
        own = (str(self.state / "workspace" / "My Renders" / "a.png") + "\n" + str(self.base / "elsewhere" / "state")
               + "/b.png\nsee " + str(self.home / "state backup" / "x.png") + " today\nbackup at "
               + str(self.home / "state+backup" / "x.png") + "\n").encode()
        for name, data in (("random", random_bytes), ("words", words), ("own", own * 50),
                           ("wide", own.decode().encode("utf-16-le"))):
            with self.subTest(data=name):
                self.assertIsNone(self.found(data))

    def test_reads_text_only_around_places_and_never_the_whole_file(self):
        read_as_text, analysed = [0], [0]
        decoding, analyse = worker_merge_scan.decoding, Scanner._analyse

        def counted_decoding(chunk, family, plan=None, at=0):   # Every text read goes through it.
            read_as_text[0] += len(chunk)
            return decoding(chunk, family, plan, at)

        def counted_analyse(self, *args):
            analysed[0] += 1
            return analyse(self, *args)
        noise = random.Random(5)
        data = bytes(noise.getrandbits(8) for _ in range(1 << 20)) * 24
        latin = self.folders(self.state)   # A folder with no Latin letter is read in every chunk's text instead.
        with mock.patch.object(worker_merge_scan, "decoding", counted_decoding), \
                mock.patch.object(Scanner, "_analyse", counted_analyse):
            reader = Counted(data)
            self.assertIsNone(Scanner(latin).hit(reader, 0, len(data)))
            self.assertLess(read_as_text[0], len(data) // 100)
            self.assertLessEqual(reader.largest, worker_merge_scan.CHUNK + 4096)
            read_as_text[0] = 0
            words = "".join("frame %d: angelo state of the scene\n" % index for index in range(100000)).encode()
            self.assertIsNone(self.found(words, latin))
            self.assertEqual(analysed[0], 0)   # A name with no separator before it is never read as a path.

    def test_separator_run_read_through_and_too_long_one_fails_closed(self):
        run = str(self.home) + "/" * (3 * worker_merge_paths.DETOUR) + "state/sessions/chat.json"
        self.assertIsNotNone(self.found(b"x" * 50000 + run.encode() + b"\n"))
        with mock.patch.object(worker_merge_scan, "MARGIN", 1 << 16):
            longer = str(self.home) + "/" * (1 << 18) + "state/sessions/chat.json"
            with self.assertRaises(Unreadable):
                self.found(b"x" * 50000 + longer.encode() + b"\n")

    def test_found_among_many_mentions_of_the_folders_names(self):
        lines = ["frame %d: angelo state of the scene, workspace ok" % index for index in range(30000)]
        for name, spelled in self.spellings().items():
            for at in (0, 15000, 29999):
                with self.subTest(spelling=name, line=at):
                    text = "\n".join(lines[:at]).encode() + b"\n" + spelled + b"\n" + "\n".join(lines[at:]).encode()
                    self.assertIsNotNone(self.found(text))
        with mock.patch.object(worker_merge_scan, "CHUNK", 1 << 16):
            self.assertIsNotNone(self.found("\n".join(lines).encode() + self.secret.encode("utf-16-le")))

    def test_home_spelled_as_a_script_does_found(self):
        home_here = {"HOME": str(self.base), "USERPROFILE": str(self.base)}   # Windows reads USERPROFILE.
        with mock.patch.dict(os.environ, home_here):   # The state folder is now in the home folder.
            private = self.folders(self.state)
            rest = os.path.relpath(self.secret, self.base)
            for home in ("~", "$HOME", "${HOME}", "%USERPROFILE%"):
                with self.subTest(home=home):
                    self.assertIsNotNone(self.found(('cat "%s/%s"\n' % (home, rest)).encode(), private))

    def test_a_line_of_workspace_paths_costs_its_length(self):
        """Paths in the default workspace, many to a line, with ".." later on it: each place a path could end is
        tried, and each folder is decided once: 80 lines (390 KB) take about 0.2 s on the nominal machine; the review
        found 37 s for 0.2 MB, growing as its square."""
        workspace = self.state / "workspace" / "My Renders"
        line = " ".join(str(workspace / ("take %d.png" % index)) for index in range(60)) + " ...\n"
        data, privates = (line * 80).encode(), []

        def work():   # Fresh folders each round: nothing decided before.
            privates.append(self.folders(self.state))
            return self.found(data, privates[-1])
        self.assertIsNone(machine_clock.assert_cost(self, work, 1.0, "80 lines of workspace paths"))
        private = privates[-1]
        self.assertLessEqual(max(map(len, (private._identities, private._decided, private._exists))),
                             worker_merge_paths.CACHED)

    def test_own_names_are_the_part_folders_never_a_path_component(self):
        self.assertEqual(self.private.names, [self.part.name])   # Not the temporary folder's nor the home's.


if __name__ == "__main__":
    unittest.main()
