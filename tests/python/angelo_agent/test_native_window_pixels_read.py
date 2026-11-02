# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A screenshot on Metal draws the window offscreen, never reads the front buffer (wm_draw.cc). No Blender.

On Metal, activating a window's context to read it advances its 3-texture swap chain, so a front-buffer read after a
redraw returned the frame drawn before it, and a new window's creation clear (1.0.8 slot: test_blender_window_pixels_gui
took a blank Preferences reference; test_blender_studio_assets_gui's dialog frame equalled the row's before it).
``WM_window_pixels_read`` takes the offscreen path there; the eyedropper's sample read is unchanged.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "source/blender/windowmanager/intern/wm_draw.cc"


def body(name):
    text = SOURCE.read_text(encoding="utf-8")
    start = text.index(name)
    return text[start:text.index("\n}\n", start)]


class WindowPixelsReadTests(unittest.TestCase):
    def test_a_screenshot_on_metal_never_reads_the_front_buffer(self):
        read = body("uint8_t *WM_window_pixels_read(bContext *C, wmWindow *win, int r_size[2])")
        guard = read.index("GPU_backend_get_type() != GPU_BACKEND_METAL")
        self.assertLess(guard, read.index("WM_window_pixels_read_from_frontbuffer"))
        self.assertIn("return WM_window_pixels_read_from_offscreen(C, win, r_size);", read)

    def test_the_eyedropper_sample_read_is_unchanged(self):
        self.assertNotIn("GPU_BACKEND_METAL", body("bool WM_window_pixels_read_sample(bContext *C"))


if __name__ == "__main__":
    unittest.main()
