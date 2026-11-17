# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every file or folder of this repository that Angelo's documentation names is in the tree.

The web live link's documents named ``web_link/angelo-live/README.md`` and its Node tests while only ``PROTOCOL.md``
had landed (2026-11-16; the package source came with the 2.0.0 port of #194). A cited path is a double-backquoted
literal under one of the repository's own top folders; a Python module may be named without ``.py``.
Dependency-free.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / "doc/angelo"
TOP = ("build_files/", "doc/", "locale/", "release/", "scripts/", "source/", "tests/", "web_link/")
LITERAL = re.compile(r"``([A-Za-z0-9_./-]+)``")


def cited(text):
    """The repository paths a reStructuredText document names (no patterns, no placeholders)."""
    return sorted({path.rstrip("/") for path in LITERAL.findall(text) if path.startswith(TOP)})


def exists(path):
    return (ROOT / path).exists() or (ROOT / (path + ".py")).is_file()


class DocPathTests(unittest.TestCase):
    def test_every_path_the_documentation_names_is_in_the_tree(self):
        documents = sorted(DOCS.rglob("*.rst"))
        self.assertGreater(len(documents), 20)
        missing = ["%s names %s" % (doc.relative_to(ROOT), path) for doc in documents
                   for path in cited(doc.read_text(encoding="utf-8")) if not exists(path)]
        self.assertEqual(missing, [])

    def test_the_web_links_package_source_is_in_the_tree(self):
        text = (DOCS / "web_link.rst").read_text(encoding="utf-8")
        for path in ("web_link/angelo-live/README.md", "web_link/angelo-live/PROTOCOL.md", "web_link/angelo-live/test",
                     "web_link/angelo-studio"):
            self.assertIn(path, cited(text))

    def test_the_check_finds_a_missing_path(self):
        self.assertEqual(cited("see ``web_link/nowhere/README.md`` and ``scripts/angelo/library_index``, "
                               "not ``bpy.ops`` or ``<folder>/x``"),
                         ["scripts/angelo/library_index", "web_link/nowhere/README.md"])
        self.assertFalse(exists("web_link/nowhere/README.md"))
        self.assertTrue(exists("scripts/angelo/library_index"))


if __name__ == "__main__":
    unittest.main()
