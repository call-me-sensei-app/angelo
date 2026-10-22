# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The transcript scrollbar's geometry and input (chat_scrollbar), without Blender.

The GUI suite test_blender_chat_replies drags the real bar; this pins the arithmetic: the thumb's size and
place, a proportional drag, a track click, wheel and trackpad steps (in the system's scroll direction, as
Blender's own views: ``scroll_input``), and nothing when everything fits.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import chat_scrollbar  # noqa: E402

FAKE_BPY = SimpleNamespace(app=SimpleNamespace(timers=SimpleNamespace(is_registered=lambda f: True, register=None)))


def event(kind, value="NOTHING", y=0, prev_y=0):
    return SimpleNamespace(type=kind, value=value, mouse_y=y, mouse_prev_y=prev_y)


class ScrollbarTests(unittest.TestCase):
    def setUp(self):
        self.state = {"message_viewport": (0, 100, 400, 500), "max_scroll": 1500}
        self.scrolls = {"chat": 0}
        self.area = SimpleNamespace(tag_redraw=lambda: None)
        patcher = mock.patch.dict(sys.modules, {"bpy": FAKE_BPY})
        patcher.start()
        self.addCleanup(patcher.stop)

    def handle(self, e, x=395, y=300):
        return chat_scrollbar.handle(e, self.state, self.area, x, y, "chat", self.scrolls)

    def test_thumb_is_proportional_and_sits_at_the_bottom_when_at_the_newest_row(self):
        geometry = chat_scrollbar._geometry(self.state)
        track, thumb, max_scroll = geometry
        self.assertAlmostEqual(thumb, 492 * 500 / 2000)
        self.assertEqual(chat_scrollbar._thumb_rect(geometry, 0)[1], track[1])
        self.assertAlmostEqual(chat_scrollbar._thumb_rect(geometry, 1500)[1], track[1] + track[3] - thumb)

    def test_nothing_when_everything_fits(self):
        self.state["max_scroll"] = 0
        self.assertIsNone(chat_scrollbar._geometry(self.state))
        self.assertFalse(self.handle(event('LEFTMOUSE', 'PRESS')))

    def test_drag_scrolls_proportionally_and_release_ends_it(self):
        track, thumb, _ = chat_scrollbar._geometry(self.state)
        self.assertTrue(self.handle(event('MOUSEMOVE'), x=395, y=track[1] + 10))
        self.assertEqual(self.state["scroll_hover"], "bar")
        self.assertTrue(self.handle(event('LEFTMOUSE', 'PRESS'), y=track[1] + 10))
        self.assertEqual(self.scrolls["chat"], 0)  # Pressed on the thumb: no jump.
        self.handle(event('MOUSEMOVE'), y=track[1] + 10 + (track[3] - thumb) / 2)
        self.assertAlmostEqual(self.scrolls["chat"], 750, delta=1)
        self.assertTrue(self.handle(event('LEFTMOUSE', 'RELEASE')))
        self.assertNotIn("scroll_drag", self.state)

    def test_track_click_jumps_and_clamps(self):
        track, thumb, _ = chat_scrollbar._geometry(self.state)
        self.handle(event('MOUSEMOVE'), x=395, y=track[1] + track[3] - 2)
        self.handle(event('LEFTMOUSE', 'PRESS'), y=track[1] + track[3] - 2)
        self.assertEqual(self.scrolls["chat"], 1500)

    def test_wheel_and_trackpad_scroll_within_bounds(self):
        self.assertTrue(self.handle(event('WHEELUPMOUSE', 'PRESS'), x=50))
        self.assertEqual(self.scrolls["chat"], 66)
        # A pan Blender's own views follow up by 40 pixels (view2d: deltay = prev_y - y) shows earlier rows.
        self.assertTrue(self.handle(event('TRACKPADPAN', y=300, prev_y=340), x=50))
        self.assertEqual(self.scrolls["chat"], 106)
        self.assertTrue(self.handle(event('TRACKPADPAN', y=340, prev_y=300), x=50))
        self.assertEqual(self.scrolls["chat"], 66)
        # The transcript lays out in unscaled units: on a 2x display the content still moves with the fingers.
        chat_scrollbar.handle(
            event('TRACKPADPAN', y=300, prev_y=340), self.state, self.area, 50, 300, "chat", self.scrolls, 2.0)
        self.assertEqual(self.scrolls["chat"], 86)
        for _ in range(40):
            self.handle(event('WHEELDOWNMOUSE', 'PRESS'), x=50)
        self.assertEqual(self.scrolls["chat"], 0)

    def test_trackpad_outside_the_transcript_passes_through(self):
        self.assertFalse(self.handle(event('TRACKPADPAN', y=340, prev_y=300), x=50, y=20))

    def test_hit_geometry_matches_the_kit_bar(self):
        from angelo_agent import ui_kit_rows
        for scroll in (0, 400, 1500):
            kit = ui_kit_rows.scrollbar_rects((0, 100, 400, 500), 2000, 500, 1500 - scroll, 1.0)[1]
            ours = chat_scrollbar._thumb_rect(chat_scrollbar._geometry(self.state), scroll)
            self.assertAlmostEqual(kit[1], ours[1])
            self.assertAlmostEqual(kit[3], ours[3])


if __name__ == "__main__":
    unittest.main()
