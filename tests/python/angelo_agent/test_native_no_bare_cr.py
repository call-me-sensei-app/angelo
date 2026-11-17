# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""No text source holds a bare carriage return (a CR byte not followed by LF).

A tool once wrote the ``\\r`` of a doc comment's ``\\return`` as a CR byte (44436dec2a0, WM_api.hh); an editor that
reads universal newlines then turns that byte into a line break, splitting the comment (merge-11's read of f560475).
Every ``.cc``, ``.hh``, ``.py`` and ``.rst`` file under source/, scripts/, tests/ and doc/ is checked. CRLF line
endings are not bare CRs. Runs outside Blender; walks the tree, as VM CI's archive has no Git.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FOLDERS = ("source", "scripts", "tests", "doc")
SUFFIXES = (".cc", ".hh", ".py", ".rst")
SKIP = {"__pycache__", ".git", "lib"}
BARE_CR = re.compile(rb"\r(?!\n)")


def bare_crs(root=ROOT):
    """``["path:line", ...]`` of every bare CR in the checked text sources under ``root`` (POSIX paths on every OS)."""
    found = []
    for folder in FOLDERS:
        for directory, names, files in os.walk(root / folder):
            names[:] = [name for name in names if name not in SKIP and not name.endswith(".noindex")]
            for name in files:
                if name.endswith(SUFFIXES):
                    path = Path(directory, name)
                    data = path.read_bytes()
                    for match in BARE_CR.finditer(data):
                        line = data.count(b"\n", 0, match.start()) + 1
                        found.append("%s:%d" % (path.relative_to(root).as_posix(), line))
    return found


class NoBareCarriageReturn(unittest.TestCase):
    def test_no_text_source_holds_a_bare_cr(self):
        self.assertEqual(bare_crs(), [], "write the character the byte stood for (``\\r`` in ``\\return``)")

    def test_the_check_finds_one(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "doc").mkdir()
            (Path(folder) / "doc" / "x.rst").write_bytes(b"ok\r\nline\rreturn\n")
            self.assertEqual(bare_crs(Path(folder)), ["doc/x.rst:2"])


if __name__ == "__main__":
    unittest.main()
