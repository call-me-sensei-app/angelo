# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The frame after Angelo's start-up cover lifts is owed until a region draws in it (1.0.10), on a stand-in ``bpy``.

The native cover does not render the viewports it hides (``wm_angelo_cover.cc``): they draw in the frame after it
lifts. ``angelo_default_layout`` marks that frame owed when it lifts a shown cover (``_cover["frame_owed"]``, which
``qa_boot`` waits on, so a capture in the lift's own timer pass never reads views not yet rendered), clears it at the
first region draw, and then removes its draw handlers from a timer. A second lift before that timer runs keeps the
handlers (break-it review L1: removing them left the frame owed for good and the QA gate failed at its limit). The
gate's own reading of the flag is ``test_native_qa_boot``'s; the pixels are ``test_blender_first_launch_frames``'s.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_startup_splash import LAYOUT, fake_modules  # noqa: E402  (the same stand-in bpy)


class Space:
    """A stand-in editor type: its draw handlers, live."""

    def __init__(self):
        self.live = {}
        self.bl_rna = SimpleNamespace(properties={"is_coding_agent": None})   # SpaceConsole: Angelo's binary.

    def draw_handler_add(self, function, args, region, kind):
        handle = object()
        self.live[handle] = (function, region, kind)
        return handle

    def draw_handler_remove(self, handle, region):
        self.live.pop(handle)


class LiftedFrameTests(unittest.TestCase):
    def setUp(self):
        self.wm = SimpleNamespace(angelo_startup_cover=(0.0, 0.0, 0.0, 0.0), windows=[])
        modules, _stock, _record = fake_modules(False)
        modules["bpy"].context = SimpleNamespace(window_manager=self.wm, preferences=None)
        patch = mock.patch.dict(sys.modules, modules)
        patch.start()
        self.addCleanup(patch.stop)
        self.bpy = modules["bpy"]
        spec = importlib.util.spec_from_file_location("angelo_default_layout", LAYOUT)
        self.layout = importlib.util.module_from_spec(spec)
        sys.modules["angelo_default_layout"] = self.layout
        spec.loader.exec_module(self.layout)
        self.spaces = {}
        for name in ("SpaceTopBar", "SpaceStatusBar") + self.layout.COVER_SPACES:
            self.spaces[name] = Space()
            setattr(self.bpy.types, name, self.spaces[name])
        self.layout.register()   # Covers from before the first frame.

    def live_handlers(self):
        return sum(len(space.live) for space in self.spaces.values())

    def lift(self):
        """Angelo's layout settled: the cover's watch removes it on its next poll."""
        for timer in list(self.bpy.app.timers.registered):
            if timer is self.layout._timer:
                self.bpy.app.timers.registered.remove(timer)
        self.assertIsNone(self.layout._cover_watch())
        self.assertEqual(self.wm.angelo_startup_cover[3], 0.0)

    def draw_a_region(self):
        function, _region, kind = next(iter(next(s for s in self.spaces.values() if s.live).live.values()))
        self.assertEqual(kind, 'POST_PIXEL')
        function()

    def run_timer(self, function):
        self.assertIn(function, self.bpy.app.timers.registered)
        self.bpy.app.timers.registered.remove(function)
        return function()

    def test_lifting_a_shown_cover_owes_a_frame_until_a_region_draws(self):
        self.assertEqual(self.wm.angelo_startup_cover[3], 1.0)
        self.assertFalse(self.layout._cover.get("frame_owed"))
        self.lift()
        self.assertTrue(self.layout._cover["frame_owed"], "Lifted, but the views it hid are not drawn yet")
        self.assertTrue(self.spaces["SpaceView3D"].live, "A handler on the editors the cover hid")
        # The bars draw in the lifted frame because the cover property's RNA update sends NC_WINDOW, which tags
        # every region (the lift itself tags only the editor areas, startup_cover_redraw_hidden).
        self.assertTrue(self.spaces["SpaceTopBar"].live, "And on the bars, which always draw")
        self.draw_a_region()
        self.assertFalse(self.layout._cover["frame_owed"], "Every area the lift tagged draws in that frame")
        self.assertIsNone(self.run_timer(self.layout._forget_lifted_frame))
        self.assertEqual(self.live_handlers(), 0, "Its handlers are removed after that frame")
        self.assertNotIn("frame_handlers", self.layout._cover)

    def test_a_second_lift_before_the_handlers_go_keeps_them(self):
        self.lift()
        self.draw_a_region()   # Schedules the removal...
        self.layout.start_cover()   # ...and before it runs a file load covers again, and lifts again.
        self.lift()
        self.assertTrue(self.layout._cover["frame_owed"])
        self.assertIsNone(self.run_timer(self.layout._forget_lifted_frame))
        self.assertGreater(self.live_handlers(), 0, "Still owed: the handlers that clear it stay")
        self.draw_a_region()
        self.assertFalse(self.layout._cover["frame_owed"])
        self.run_timer(self.layout._forget_lifted_frame)
        self.assertEqual(self.live_handlers(), 0)

    def shelf_cover(self, error=None):
        """A stand-in ``angelo_agent.media_shelf_cover``: its ``before_lift`` is recorded, and raises ``error``."""
        calls = []

        def before_lift():
            calls.append(self.wm.angelo_startup_cover[3])
            if error is not None:
                raise error
        stand_in = SimpleNamespace(before_lift=before_lift)
        patch = mock.patch.dict(sys.modules, {"angelo_agent.media_shelf_cover": stand_in})
        patch.start()
        self.addCleanup(patch.stop)
        return calls

    def test_the_add_on_gets_its_last_step_under_the_cover_and_a_failure_never_keeps_it_up(self):
        calls = self.shelf_cover(RuntimeError("a broken add-on"))
        with mock.patch("builtins.print"):
            self.lift()
        self.assertEqual(calls, [1.0], "Called once, while the cover still showed")
        self.assertTrue(self.layout._cover["frame_owed"])

    def test_a_check_that_raises_still_gives_the_add_on_its_last_step(self):
        calls = self.shelf_cover()
        self.layout._cover["opted_out"] = None
        with mock.patch.object(self.layout, "opted_out", side_effect=RuntimeError("a broken add-on")):
            with self.assertRaises(RuntimeError):
                self.layout._cover_watch()
        self.assertEqual(calls, [1.0])
        self.assertEqual(self.wm.angelo_startup_cover[3], 0.0, "The cover lifts all the same")

    def test_a_handler_that_fails_to_add_never_keeps_the_cover_up(self):
        """Review of 0390899dd19 (L4): an unexpected error adding the owed frame's handlers (a script reload, say) left
        the cover up for good (Blender drops the watch's timer that raised). The cover lifts; the handlers added before
        the error stay recorded, so the frame they owe is cleared and they are removed after it."""
        def broken(*args):
            raise RuntimeError("a script reload")
        self.spaces["SpaceStatusBar"].draw_handler_add = broken
        with mock.patch("builtins.print"):
            self.lift()
        self.assertTrue(self.spaces["SpaceTopBar"].live, "Added before the error")
        self.assertTrue(self.layout._cover["frame_owed"], "Owed: a handler that clears it is live")
        self.draw_a_region()
        self.assertFalse(self.layout._cover["frame_owed"])
        self.run_timer(self.layout._forget_lifted_frame)
        self.assertEqual(self.live_handlers(), 0, "None leaked")

    def test_no_frame_is_owed_when_no_handler_could_be_added(self):
        def broken(*args):
            raise RuntimeError("a script reload")
        for space in self.spaces.values():
            space.draw_handler_add = broken
        with mock.patch("builtins.print"):
            self.lift()
        self.assertFalse(self.layout._cover["frame_owed"], "Nothing would ever clear it: qa_boot would wait for good")

    def test_no_frame_is_owed_when_no_cover_was_shown(self):
        self.wm.angelo_startup_cover = (0.0, 0.0, 0.0, 0.0)   # Never painted (a cover that was already cleared).
        self.layout._remove_cover()
        self.assertFalse(self.layout._cover.get("frame_owed"))
        self.assertEqual(self.live_handlers(), 0)


if __name__ == "__main__":
    unittest.main()
