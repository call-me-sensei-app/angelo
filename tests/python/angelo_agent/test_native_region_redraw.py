# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A region drawn outside the window's own draw pass is laid out first, so its panels and buttons draw (B-008).

A region with a layout (panels, headers, the top bar, the Preferences) builds its UI blocks in its layout. The draw
after that layout marks them inactive and only active blocks draw, so a second draw without a new layout draws an empty
region and frees its blocks. ``live_view.window_pixels`` (the agent's screenshot while parallel-edit workers run) drew
each region alone, the way ``wm.redraw_timer``'s ``DRAW_WIN`` benchmark does: the agent saw blank Preferences in every
section, a blank Properties editor and an empty Director top bar, while the window showed them in full (1.0.8 dogfood).

This pins the class across Blender's sources: every region draw outside the window's own pass runs a layout in the same
function, the benchmark's layout-free draw stays the benchmark's, and Angelo's capture redraws through the window's
own pass. ``test_blender_window_pixels_gui`` compares the capture with the screen in many editors and regions.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "source/blender"
DRAW_CALL = re.compile(r"\b(ED_region_do_draw|wm_draw_region_test)\(")
LAYOUT = re.compile(r"\bED_region_do_layout\(|->layout\(C, ")


def body_before(text, index):
    """The text of the function ``index`` is in, up to ``index`` (Blender's style: a function opens with ``{`` alone on
    its line)."""
    return text[text.rfind("\n{\n", 0, index):index]


def calls():
    """``(path, function text up to the call, callee)`` of every region draw call in Blender's C++ sources."""
    for path in sorted(SOURCE.rglob("*.cc")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in DRAW_CALL.finditer(text):
            line = text[text.rfind("\n", 0, match.start()) + 1:match.start()]
            if line.lstrip().startswith(("void ", "static void ")):
                continue   # The definition, not a call.
            yield path.relative_to(SOURCE).as_posix(), body_before(text, match.start()), match.group(1)


class RegionRedrawTests(unittest.TestCase):
    def test_every_region_draw_outside_the_benchmark_lays_the_region_out_first(self):
        unlaid = []
        for path, body, callee in calls():
            if callee == "wm_draw_region_test":
                if path != "windowmanager/intern/wm_operators.cc":   # wm.redraw_timer's DRAW and DRAW_WIN only.
                    unlaid.append((path, callee))
                continue
            if path == "windowmanager/intern/wm_draw.cc" and "Function for redraw timer benchmark." in body:
                continue   # wm_draw_region_test itself.
            if not LAYOUT.search(body):
                unlaid.append((path, callee))
        self.assertEqual(unlaid, [], "a region drawn without its layout shows no panel or button")

    def test_the_scan_sees_the_draws_it_judges(self):
        found = {(path, callee) for path, _body, callee in calls()}
        self.assertIn(("windowmanager/intern/wm_draw.cc", "ED_region_do_draw"), found)
        self.assertIn(("editors/interface/interface_ops.cc", "ED_region_do_draw"), found)
        self.assertIn(("windowmanager/intern/wm_operators.cc", "wm_draw_region_test"), found)

    def test_the_capture_redraws_every_region_through_the_window_pass(self):
        capture = (SOURCE / "windowmanager/intern/wm_angelo_input_hold.cc").read_text(encoding="utf-8")
        body = capture[capture.index("uint8_t *WM_angelo_window_pixels_redrawn("):]
        self.assertIn("wm_draw_window_regions_again(C, win);", body)
        self.assertLess(body.index("wm_draw_window_regions_again(C, win);"),
                        body.index("WM_window_pixels_read_from_offscreen(C, win, r_size)"))
        draw = (SOURCE / "windowmanager/intern/wm_draw.cc").read_text(encoding="utf-8")
        again = draw[draw.index("void wm_draw_window_regions_again("):]
        again = again[:again.index("\n}\n")]
        self.assertIn("ED_screen_areas_iter (win, screen, area)", again)   # Global areas too (break-it P1-4 M3).
        self.assertLess(again.index("ED_region_tag_redraw(&region);"),
                        again.index("wm_draw_window_offscreen(C, win, false);"))

    def test_suites_redraw_a_window_with_its_layout(self):
        frames = (ROOT / "tests/python/angelo_agent/qa_frames.py").read_text(encoding="utf-8")
        self.assertNotIn("type='DRAW_WIN',", frames)
        self.assertIn("type='DRAW_WIN_SWAP'", frames)


if __name__ == "__main__":
    unittest.main()
