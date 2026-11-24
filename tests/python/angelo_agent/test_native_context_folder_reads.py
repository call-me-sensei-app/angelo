# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A context folder's listings and reads page and stay small (``context_folder``), split from
test_native_context_folder.py to keep that file within 500 lines (break-it of 565f5df, NIT). No Blender here."""

import suite_isolation  # noqa: F401

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import context_folder as cf  # noqa: E402
from test_native_context_folder import Tree  # noqa: E402  (its fixture: a small game project)
import angelo_guard_twin  # noqa: E402
angelo_guard_twin.stand_in_for_the_person(globals())


class PagingTests(Tree):
    """Audit 2026-11-14 F4: a listing stopped at 200 entries and search went on as if that were everything, reporting
    complete. Now a listing pages (``more``, ``next_start``, taken back as ``start``), search pages through every folder
    within its bounds, and any bound that cut the search short makes it incomplete and is named (``limits``)."""

    def setUp(self):
        super().setUp()
        many = self.root / "many"
        many.mkdir()
        for index in range(250):
            (many / ("part_%03d.txt" % index)).write_text("part %d" % index, encoding="utf-8")

    def test_a_listing_pages_past_200_and_says_so(self):
        first, _ = self.run_action(action="LIST", path="many")
        self.assertEqual((len(first["entries"]), first["more"], first["next_start"]), (200, True, 200))
        rest, _ = self.run_action(action="LIST", path="many", start=200)
        self.assertEqual((len(rest["entries"]), rest["more"]), (50, False))
        self.assertEqual(rest["entries"][-1]["name"], "part_249.txt")

    def test_search_reaches_every_entry_and_is_complete_only_when_nothing_was_cut(self):
        # Reach, not speed: the time bound is lifted here (VM CI 7ab49fd7cc0, win-4 found nothing, most likely cut by
        # the 2 s bound on a slow host); the next test cuts on time deterministically.
        with patch.object(cf, "MAX_SECONDS", 3600.0):
            found, _ = self.run_action(action="SEARCH", query="part_240")
        self.assertIn({"path": "many/part_240.txt", "match": "name"}, found["hits"])
        self.assertEqual((found["complete"], found["limits"]), (True, []))

    def test_every_bound_that_cut_a_search_is_named(self):
        for name, value, limit in (("MAX_DEPTH", 0, "depth"), ("MAX_SEARCHED_FILE", 4, "file_size"),
                                   ("MAX_VISITED", 5, "entries"), ("MAX_HITS", 1, "hits"),
                                   ("MAX_SECONDS", -1.0, "time")):   # A deadline already past: cut at the first entry.
            with self.subTest(limit=limit), patch.object(cf, name, value):
                found, _ = self.run_action(action="SEARCH", query="part")
                self.assertFalse(found["complete"])
                self.assertIn(limit, found["limits"])


class ReadWindowTests(Tree):
    """Audit efficiency: a read defaults to a small window, says where the rest starts, and widens only when asked;
    a window never splits a character."""

    def test_a_read_is_a_small_window_with_where_the_rest_starts(self):
        (self.root / "long.txt").write_text("x" * 40000, encoding="utf-8")
        first, _ = self.run_action(action="READ", path="long.txt")
        self.assertLessEqual(cf.READ_DEFAULT, 16384)
        self.assertEqual((len(first["text"]), first["next_start"]), (cf.READ_DEFAULT, cf.READ_DEFAULT))
        wide, _ = self.run_action(action="READ", path="long.txt", start=0, length=cf.READ_LIMIT)
        self.assertEqual(len(wide["text"]), 40000)
        self.assertNotIn("next_start", wide)

    def test_a_window_never_splits_a_character(self):
        (self.root / "notes.md").write_text("é" * 10000, encoding="utf-8")   # Two bytes each.
        first, _ = self.run_action(action="READ", path="notes.md", start=0, length=5)
        self.assertEqual((first["text"], first["next_start"]), ("éé", 4))
        rest, _ = self.run_action(action="READ", path="notes.md", start=first["next_start"], length=4)
        self.assertEqual(rest["text"], "éé")


if __name__ == "__main__":
    unittest.main()
