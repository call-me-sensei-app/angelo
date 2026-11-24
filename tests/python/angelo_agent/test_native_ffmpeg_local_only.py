# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every FFmpeg open in Blender and audaspace goes through the `file` protocol only (BREAKIT reread-r210-1c2c4edaf48
M-1).

The bundled FFmpeg is built with network protocols, and a path is whatever a document or a script set: a URL made a
sound, a movie or an encoder connect out as native traffic. So every `avformat_open_input` and every `avio_open*`
passes `protocol_whitelist=file` (imbuf's `ffmpeg_open_input_local` / `ffmpeg_open_output_local`, audaspace's reader
and writer), and a new call site that doesn't fails here. Whether it holds at run time is
`test_blender_script_confinement`'s (`network_refused_*`, a localhost listener). Reads sources; dependency-free.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OPENS = re.compile(r"\b(avformat_open_input|avio_open2?)\s*\(")
FOLDERS = ("source", "extern/audaspace", "intern")
SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".m", ".mm"}


def calls():
    """``[(path, line number, the line)]`` of every FFmpeg open in the sources (comments skipped)."""
    found = []
    for folder in FOLDERS:
        for path in sorted((ROOT / folder).rglob("*")):
            if path.suffix not in SUFFIXES or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for number, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith(("//", "*", "/*")) or not OPENS.search(line):
                    continue
                found.append((path.relative_to(ROOT).as_posix(), number, line))
    return found


class FFmpegLocalOnly(unittest.TestCase):
    def test_every_open_passes_the_file_only_whitelist(self):
        found = calls()
        self.assertGreaterEqual(len(found), 5, found)   # The reader finds the opens it guards.
        for path, number, line in found:
            text = (ROOT / path).read_text(encoding="utf-8").splitlines()
            before = "\n".join(text[max(0, number - 6):number])
            with self.subTest(path=path, line=number):
                self.assertRegex(before, r'av_dict_set\(&(options|local_only), "protocol_whitelist", '
                                         r'(LOCAL_PROTOCOLS|"file"), 0\)')
                self.assertRegex(line, r"&(options|local_only)\)")

    def test_the_whitelist_is_files_only(self):
        util = (ROOT / "source/blender/imbuf/movie/intern/movie_util.cc").read_text(encoding="utf-8")
        self.assertIn('static constexpr const char *LOCAL_PROTOCOLS = "file";', util)

    def test_imbuf_reads_only_through_the_helper(self):
        for path, number, line in calls():
            if (path.startswith("source/") and "avformat_open_input" in line and
                    path != "source/blender/imbuf/movie/intern/movie_util.cc"):
                self.fail("%s:%d opens FFmpeg input directly: use ffmpeg_open_input_local" % (path, number))


if __name__ == "__main__":
    unittest.main()
