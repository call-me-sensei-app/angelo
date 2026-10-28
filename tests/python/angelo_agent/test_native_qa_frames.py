# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""qa_frames tells a capture that drew content from a flat frame (35f5b544698: Media Studio's window, one colour)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy  # noqa: E402

import qa_frames  # noqa: E402


class ContentShareTests(unittest.TestCase):
    def test_a_flat_frame_and_a_noisy_flat_frame_show_nothing(self):
        flat = numpy.full((90, 120, 3), 23)
        self.assertEqual(qa_frames.content_share(flat), 0.0)
        noisy = flat + numpy.random.default_rng(1).integers(-4, 5, flat.shape)   # Under the tolerance.
        self.assertFalse(qa_frames.drawn(noisy))

    def test_tabs_a_column_and_text_count_as_drawn(self):
        frame = numpy.full((90, 120, 3), 23)
        frame[5:15, 5:40] = (45, 92, 88)    # A tab.
        frame[20:80, 5:30] = (34, 34, 34)   # A column a little lighter than the page.
        self.assertTrue(qa_frames.drawn(frame))
        self.assertGreater(qa_frames.content_share(frame), 0.1)

    def test_the_suites_that_capture_studios_window_check_it(self):
        here = Path(__file__).resolve().parent
        for suite in ("test_blender_welcome_slides.py", "test_blender_studio_palette.py"):
            self.assertIn("content_share", (here / suite).read_text(), suite)


class SameFrameRectTests(unittest.TestCase):
    """A crop uses the rectangle its row has in the frame read, never the place the row was first drawn."""

    RECORDS = [{"key": "row", "at": 1.0, "rect": "first"}, {"key": "other", "at": 2.0, "rect": "other"},
               {"key": "row", "at": 3.0, "rect": "settled"}, {"key": "other", "at": 4.0, "rect": "later"}]

    def test_the_newest_draw_of_that_row_since_the_redraw(self):
        self.assertEqual(qa_frames.drawn_in(self.RECORDS, "row", 0.5)["rect"], "settled")
        self.assertEqual(qa_frames.drawn_in(self.RECORDS, "row", 3.0)["rect"], "settled")

    def test_a_row_not_drawn_in_the_frame_read_is_none(self):
        self.assertIsNone(qa_frames.drawn_in(self.RECORDS, "row", 3.5))   # Drawn only before the redraw.
        self.assertIsNone(qa_frames.drawn_in(self.RECORDS, "missing", 0.0))
        self.assertIsNone(qa_frames.drawn_in([], "row", 0.0))

    def test_a_row_matched_by_what_it_says(self):
        records = [dict(r, title="Nothing ran" if r["key"] == "row" else "Done.") for r in self.RECORDS]
        self.assertEqual(qa_frames.drawn_in(records, "Nothing ran", 0.0, "title")["rect"], "settled")
        self.assertIsNone(qa_frames.drawn_in(records, "Nothing ran", 3.5, "title"))

    def test_the_consent_suite_crops_its_refusal_row_from_the_frame_it_reads(self):
        source = (Path(__file__).resolve().parent / "test_blender_trusted_scripts_consent.py").read_text()
        stale = source[source.index('check("stale_refusal_row_says_nothing_ran"'):]
        stale = stale[:stale.index('check("stale_approval_refused_by_the_worker"')]
        redrawn = stale.index('since = qa_frames.redraw(parts()["window"])')   # Before DRAWN's list is read.
        self.assertGreater(stale.index('qa_frames.drawn_in(DRAWN["notices"], row["title"], since, "title")'), redrawn)
        self.assertIn('to_window(now["rect"])', stale)
        self.assertNotIn('row["rect"]', stale)


if __name__ == "__main__":
    unittest.main()
