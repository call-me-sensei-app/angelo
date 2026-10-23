# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""studio_views: a Media Studio window keeps its state whatever area Blender shows in it, and nothing else carries.

Switching Director and Manual mode with a Studio window open lost its tab, draft and references: the Studio window
follows its parent window's workspace and is shown a copied screen with a new area (found by the 1.0.6 Workflows
suite on Windows, 2026-10-23). A break-it review of an area-matching design found states carried by reused addresses
(freed areas, closed windows, stale Studio-window pointers, file loads); these cases pin the window-keyed design.
``bpy`` is a stand-in of windows, screens, areas and load handlers.
"""

import importlib
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
studio_views = None   # Imported per test with a stand-in bpy that never outlives the test (other suites' stubs differ).

_next = iter(range(1000, 10 ** 6))
OPENED = set()   # studio_toggle._opened


class Pointer:
    def __init__(self, pointer=None):
        self.pointer = pointer if pointer is not None else next(_next)

    def as_pointer(self):
        return self.pointer


class Area(Pointer):
    def __init__(self, studio=True, pointer=None):
        super().__init__(pointer)
        self.studio = studio


class Screen(Pointer):
    def __init__(self, *areas):
        super().__init__()
        self.areas = list(areas)


class Window(Pointer):
    def __init__(self, screen, pointer=None):
        super().__init__(pointer)
        self.screen = screen


def is_studio(area):
    return area.studio


class StudioViewTests(unittest.TestCase):
    def setUp(self):
        global studio_views
        stubs = mock.patch.dict(sys.modules, {} if "bpy" in sys.modules else {"bpy": types.ModuleType("bpy")})
        stubs.start()
        self.addCleanup(stubs.stop)
        sys.modules.pop("angelo_agent.studio_views", None)
        studio_views = importlib.import_module("angelo_agent.studio_views")
        self.windows, self.screens, self.views = [], [], {}
        self.handlers = SimpleNamespace(load_pre=[])
        studio_views.bpy = SimpleNamespace(
            context=SimpleNamespace(window_manager=SimpleNamespace(windows=self.windows)),
            data=SimpleNamespace(screens=self.screens), app=SimpleNamespace(handlers=self.handlers))
        studio_views._opened = lambda: OPENED
        self.addCleanup(studio_views.clear, self.views)
        self.addCleanup(OPENED.clear)

    def screen(self, *areas):
        screen = Screen(*areas)
        self.screens.append(screen)
        return screen

    def view(self, area):   # studio.view
        return studio_views.view(self.views, area, is_studio)

    def pump(self):   # studio.pump: a view for every shown Studio area, then track
        for window in self.windows:
            for area in window.screen.areas:
                if is_studio(area):
                    self.view(area)
        studio_views.track(self.views)

    def open_studio(self, pointer=None):   # studio_toggle.open_studio: a new window, forgotten first, then opened
        window = Window(self.screen(Area()), pointer)
        self.windows.append(window)
        studio_views.forget_window(self.views, window.as_pointer())
        OPENED.add(window.as_pointer())
        return window

    def close(self, window, toggle=True):
        if toggle:   # studio_toggle.close_studio
            studio_views.forget_window(self.views, window.as_pointer())
        self.windows.remove(window)

    def worked_on(self, window):
        state = self.view(window.screen.areas[0])
        state.update(kind="3D", prompt="a draft", references=["ref.png"])
        self.pump()
        return state

    def shows(self, window):
        state = self.view(next(a for a in window.screen.areas if is_studio(a)))
        return state["kind"], state["prompt"], state["references"]

    def test_a_mode_switch_keeps_the_state_and_switching_back_finds_it(self):
        window = self.open_studio()
        self.worked_on(window)
        old = window.screen
        window.screen = self.screen(Area())   # The copy Blender shows on a workspace change.
        self.assertEqual(self.shows(window), ("3D", "a draft", ["ref.png"]))
        self.view(window.screen.areas[0])["prompt"] = "edited after the switch"
        self.pump()
        window.screen = old
        self.assertEqual(self.shows(window)[1], "edited after the switch")

    def test_a_studio_window_keeps_its_state_on_a_layout_that_already_existed(self):
        window = self.open_studio()
        self.worked_on(window)
        window.screen = self.screen(Area())
        self.pump()
        window.screen = self.screen(Area())
        self.assertEqual(self.shows(window), ("3D", "a draft", ["ref.png"]))

    def test_a_result_sent_to_the_old_area_reaches_the_window(self):
        window = self.open_studio()
        state = self.worked_on(window)
        old = window.screen.areas[0].as_pointer()
        window.screen = self.screen(Area())
        self.pump()   # The old screen stays in its workspace: its area is alive.
        self.assertIs(self.views.get(old), state)

    def test_the_persons_own_layouts_keep_their_own_studio(self):
        mine = Area()
        window = Window(self.screen(mine))
        self.windows.append(window)
        self.view(mine).update(kind="3D", prompt="mine")
        other = Area()
        window.screen = self.screen(other)   # Another workspace's Studio, or a copy: never handed the state.
        self.assertEqual(self.shows(window), ("IMAGE", "", []))
        window.screen = self.screens[0]
        self.assertEqual(self.shows(window)[:2], ("3D", "mine"))

    def test_a_studio_window_closed_by_the_toggle_reopens_fresh_at_the_same_address(self):
        window = self.open_studio()
        self.worked_on(window)
        self.close(window)
        reopened = self.open_studio(pointer=window.as_pointer())
        self.assertEqual(self.shows(reopened), ("IMAGE", "", []))

    def test_a_studio_window_closed_otherwise_reopens_fresh_even_in_the_same_tick(self):
        window = self.open_studio()
        self.worked_on(window)
        self.close(window, toggle=False)   # The OS close button or a script: no pump in between.
        reopened = self.open_studio(pointer=window.as_pointer())
        self.assertEqual(self.shows(reopened), ("IMAGE", "", []))

    def test_a_persons_window_at_a_closed_studio_windows_address_is_not_a_studio_window(self):
        window = self.open_studio()
        self.worked_on(window)
        self.close(window, toggle=False)
        self.pump()
        self.assertNotIn(window.as_pointer(), OPENED)
        first, second = Area(), Area()
        mine = Window(self.screen(first), pointer=window.as_pointer())
        self.windows.append(mine)
        self.view(first)["prompt"] = "first layout"
        mine.screen = self.screen(second)
        self.assertEqual(self.shows(mine), ("IMAGE", "", []))

    def test_a_freed_areas_address_given_to_a_new_area_starts_fresh(self):
        mine = Area()
        window = Window(self.screen(mine))
        self.windows.append(window)
        self.view(mine)["prompt"] = "mine"
        self.screens.remove(window.screen)   # Its area is freed (area_close, a deleted workspace).
        window.screen = self.screen(Area(studio=False))
        self.pump()
        reused = Area(pointer=mine.as_pointer())
        window.screen = self.screen(reused)
        self.assertEqual(self.shows(window), ("IMAGE", "", []))

    def test_closing_several_windows_at_once_never_raises(self):
        windows = [self.open_studio() for _ in range(3)]
        for window in windows:
            self.worked_on(window)
            window.screen.areas.append(Area())   # A second Studio area in the window: its own state.
        self.pump()
        for window in windows:
            self.close(window, toggle=False)
        self.pump()   # Their screens may stay in the file: only the second areas' own states remain.
        self.assertEqual((studio_views._windows, OPENED), ({}, set()))
        self.assertTrue(all(v["prompt"] == "" for v in self.views.values()))

    def test_a_file_load_carries_nothing_over_with_or_without_its_windows(self):
        window = self.open_studio()
        self.worked_on(window)
        mine = Area()
        self.windows.append(Window(self.screen(mine)))
        self.view(mine)["prompt"] = "mine"
        self.assertEqual(len(self.handlers.load_pre), 1)
        self.assertTrue(getattr(self.handlers.load_pre[0], "_bpy_persistent", False))
        for handler in self.handlers.load_pre:
            handler(None)
        self.assertEqual(self.shows(window), ("IMAGE", "", []))   # Load UI off: the windows stay, fresh.
        self.assertEqual(self.view(mine)["prompt"], "")

    def test_a_second_studio_area_in_a_studio_window_has_its_own_state(self):
        window = self.open_studio()
        self.worked_on(window)
        second = Area()
        window.screen.areas.append(second)
        self.assertEqual(self.view(second)["prompt"], "")
        self.assertEqual(self.shows(window)[1], "a draft")

    def test_a_view_made_before_its_window_was_known_is_kept(self):
        area = Area()
        window = Window(self.screen(area))
        self.windows.append(window)
        self.view(area)["prompt"] = "early"   # The pump drew it before open_studio added the window.
        OPENED.add(window.as_pointer())
        self.assertEqual(self.shows(window)[1], "early")
        window.screen = self.screen(Area())
        self.assertEqual(self.shows(window)[1], "early")

    def test_an_area_awaiting_an_answer_keeps_its_entry_until_it_lands(self):
        window = self.open_studio()
        state = self.worked_on(window)
        old = window.screen.areas[0].as_pointer()
        self.screens.remove(window.screen)   # Its layout is freed while AI Enhance runs for it.
        window.screen = self.screen(Area())
        studio_views.track(self.views, busy={old})
        self.assertIs(self.views.get(old), state)
        studio_views.track(self.views)
        self.assertNotIn(old, self.views)

    def test_a_file_load_stops_the_requests_in_flight_first(self):
        window = self.open_studio()
        self.worked_on(window)
        seen = []
        studio_views.on_load(lambda: seen.append(len(self.views)))
        for handler in self.handlers.load_pre:
            handler(None)
        self.assertEqual(seen, [1])   # Before the states went.
        self.assertEqual(self.views, {})

    def test_studio_wires_the_pump_and_the_load(self):
        source = (Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent/studio.py").read_text()
        self.assertIn("if _enhancements.pop(pointer, None) is None:", source)   # A cancelled answer lands nowhere.
        self.assertIn("studio_views.on_load(cancel_requests)", source)
        self.assertIn("bpy.app.timers.register(pump, first_interval=.1, persistent=True)", source)

    def test_unregister_removes_the_handler_and_every_state(self):
        window = self.open_studio()
        self.worked_on(window)
        studio_views.clear(self.views)
        self.assertEqual((self.handlers.load_pre, self.views, studio_views._windows), ([], {}, {}))


if __name__ == "__main__":
    unittest.main()
