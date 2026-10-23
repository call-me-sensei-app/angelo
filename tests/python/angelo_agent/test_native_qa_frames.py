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


if __name__ == "__main__":
    unittest.main()
