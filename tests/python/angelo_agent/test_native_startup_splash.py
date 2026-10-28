# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Blender's startup splash never shows Blender's Quick Setup form or New File page in Angelo, on a stand-in ``bpy``.

The splash is C (``wm_splash_screen.cc``): it draws its art, then the menu registered as ``WM_MT_splash_quick_setup``
(no saved preferences) or ``WM_MT_splash``, whichever class owns that name. Three kinds of class can: Blender's own
(``bl_operators/wm.py``, the real classes are drawn here), the startup module's stand-ins (``angelo_default_layout``)
and the add-on's (``angelo_agent.splash_setup``). Every one is drawn in every launch state with a layout that fails
on any use, so "draws nothing" is checked, not assumed:

* Angelo's startup pending (a default launch; ``--factory-startup`` and an empty profile are the same state: the
  startup module runs, no saved preferences preload the add-on);
* opted out (``ANGELO_DISABLE_DEFAULT_CHAT``), with or without the add-on loaded;
* a failed start (the scheduled start ended without the add-on);
* the add-on loaded, then disabled (Blender's own classes own the menus again, as after Reload Scripts).

Also covered: the stand-ins replace Blender's own menus once, before the splash; ``splash_setup`` takes the menus and
the draw count over; a file load that dropped Angelo's startup card hands the startup decision back. The real frames
are ``test_blender_first_launch_frames``'s.
"""

import ast
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/modules"))   # As in Blender: the startup layout imports angelo_live_windows.
LAYOUT = ROOT / "scripts/startup/angelo_default_layout.py"
ADDON = ROOT / "scripts/addons_core/angelo_agent"
BLENDER_MENUS = ROOT / "scripts/startup/bl_operators/wm.py"
MENUS = ("WM_MT_splash_quick_setup", "WM_MT_splash")


class Timers:
    def __init__(self):
        self.registered = []

    def register(self, function, first_interval=0.0, persistent=False):
        self.registered.append(function)

    def is_registered(self, function):
        return function in self.registered

    def unregister(self, function):
        self.registered.remove(function)


class Untouched:
    """A menu's layout that fails on any use: a draw that lays anything out (a form, a label) fails the test."""

    def __getattr__(self, name):
        raise AssertionError("The splash's menu laid something out: layout." + name)

    def __setattr__(self, name, value):
        raise AssertionError("The splash's menu laid something out: layout." + name)


def blenders_menus(menu_base):
    """Blender's own splash menu classes, from the real ``bl_operators/wm.py``; each counts its draws."""
    tree = ast.parse(BLENDER_MENUS.read_text())
    nodes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name in MENUS]
    namespace = {"Menu": menu_base}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(BLENDER_MENUS), "exec"), namespace)
    classes = {}
    for name in MENUS:
        cls = namespace[name]
        cls.bl_idname, cls.is_registered, cls.draws = name, True, 0   # Blender names a class's menu after it.

        def counted(self, context, _draw=cls.draw, _cls=cls):
            _cls.draws += 1
            return _draw(self, context)
        cls.draw = counted
        classes[name] = cls
    return classes


def fake_modules(opted_out=False):
    record = SimpleNamespace(registered=[], unregistered=[])
    bpy = types.ModuleType("bpy")
    bpy_types = types.ModuleType("bpy.types")
    bpy_types.Menu = type("Menu", (), {"is_registered": False})
    bpy_types.SpaceConsole = SimpleNamespace(bl_rna=SimpleNamespace(properties={"is_coding_agent": None}))
    stock = blenders_menus(bpy_types.Menu)
    for name, cls in stock.items():
        setattr(bpy_types, name, cls)

    def register_class(cls):
        cls.is_registered = True
        setattr(bpy_types, cls.bl_idname, cls)
        record.registered.append(cls)

    def unregister_class(cls):
        cls.is_registered = False
        record.unregistered.append(cls)

    bpy.types = bpy_types
    bpy.utils = SimpleNamespace(register_class=register_class, unregister_class=unregister_class)
    bpy.app = types.ModuleType("bpy.app")
    bpy.app.background = False
    bpy.app.timers = Timers()
    handlers = types.ModuleType("bpy.app.handlers")
    handlers.persistent = lambda function: function
    handlers.load_post, handlers.save_post = [], []
    bpy.app.handlers = handlers
    bl_operators = types.ModuleType("bl_operators")
    wm = types.ModuleType("bl_operators.wm")
    wm.__dict__.update(stock)
    bl_operators.wm = wm
    angelo_agent = types.ModuleType("angelo_agent")
    angelo_agent.__path__ = []
    product_env = types.ModuleType("angelo_agent.product_env")
    product_env.flag = lambda name: opted_out and name == "DISABLE_DEFAULT_CHAT"
    angelo_agent.product_env = product_env
    modules = {"bpy": bpy, "bpy.types": bpy_types, "bpy.app": bpy.app, "bpy.app.handlers": handlers,
               "bl_operators": bl_operators, "bl_operators.wm": wm, "angelo_agent": angelo_agent,
               "angelo_agent.product_env": product_env}
    for name in ("card_host", "first_run"):   # splash_setup's imports; nothing here reaches them.
        module = types.ModuleType("angelo_agent." + name)
        modules[module.__name__] = module
        setattr(angelo_agent, name, module)
    spec = importlib.util.spec_from_file_location("angelo_agent.live_windows", ROOT / "scripts/modules/angelo_live_windows.py")
    live_windows = importlib.util.module_from_spec(spec)   # The real walk (no bpy): splash_setup's timers use it.
    spec.loader.exec_module(live_windows)
    modules[spec.name] = live_windows
    angelo_agent.live_windows = live_windows
    spec = importlib.util.spec_from_file_location("angelo_agent.live_target", ADDON / "live_target.py")
    live_target = importlib.util.module_from_spec(spec)   # The real holder (no bpy at import): its cards answer later.
    spec.loader.exec_module(live_target)
    modules[spec.name] = live_target
    angelo_agent.live_target = live_target
    return modules, stock, record


def starts(case):
    """Angelo's scheduled starts among the registered timers (its cover's watch is not one)."""
    return len([f for f in case.bpy.app.timers.registered if f is not case.layout._cover_watch])


class StartupSplashCase(unittest.TestCase):
    def start(self, opted_out=False, extra=None):
        modules, self.stock, self.record = fake_modules(opted_out)
        modules.update(extra or {})
        patch = mock.patch.dict(sys.modules, modules)
        patch.start()
        self.addCleanup(patch.stop)
        self.bpy = modules["bpy"]
        spec = importlib.util.spec_from_file_location("angelo_default_layout", LAYOUT)
        self.layout = importlib.util.module_from_spec(spec)
        sys.modules["angelo_default_layout"] = self.layout
        spec.loader.exec_module(self.layout)
        self.layout.register()   # A startup module registers before the startup file's load_post.
        self.layout.load_post(None)
        return self.layout

    def load_addon_menus(self):
        spec = importlib.util.spec_from_file_location("angelo_agent.splash_setup", ADDON / "splash_setup.py")
        setup = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = setup
        spec.loader.exec_module(setup)
        setup.register()
        return setup

    def draw(self, idname, window=None):
        """Draw the menu that owns ``idname`` now, as the splash does; returns the owning class."""
        menu = getattr(self.bpy.types, idname)
        instance = menu.__new__(menu)
        object.__setattr__(instance, "layout", Untouched())
        instance.draw(SimpleNamespace(window=window))
        return menu

    def assert_art_alone(self):
        for name in MENUS:
            self.draw(name)   # Untouched fails on any layout use.


class BlendersOwnMenusTests(StartupSplashCase):
    def test_blenders_own_splash_menus_lay_nothing_out(self):
        """Wherever Blender's own classes own the menus, the splash shows its art alone: no Quick Setup form."""
        self.start()
        for cls in self.stock.values():
            instance = cls.__new__(cls)
            object.__setattr__(instance, "layout", Untouched())
            instance.draw(SimpleNamespace())
            self.assertEqual(cls.draws, 1)

    def test_the_c_splash_draws_only_the_menu_registered_under_each_name(self):
        """No form of Blender's lives in C: the splash's layout is the registered menu's (and an Intel warning)."""
        source = (ROOT / "source/blender/windowmanager/intern/wm_splash_screen.cc").read_text()
        splash = source.split("static ui::Block *wm_block_splash_create(", 1)[1].split("\nstatic ", 1)[0]
        self.assertIn('mt = WM_menutype_find("WM_MT_splash_quick_setup", true);', splash)
        self.assertIn('mt = WM_menutype_find("WM_MT_splash", true);', splash)
        self.assertIn("if (mt) {\n    ui::menutype_draw(C, mt, &layout);", splash)
        self.assertNotRegex(splash, r"\.prop\(|copy_prev|save_userpref|keyconfig")


class LaunchStateTests(StartupSplashCase):
    def test_pending_startup_draws_the_art_alone_and_counts_it(self):
        """A default launch, --factory-startup or an empty profile: the stand-ins own the menus until the add-on loads."""
        layout = self.start()
        self.assertTrue(layout.startup_pending())
        for cls in layout.STAND_INS:
            self.assertIs(self.draw(cls.bl_idname), cls)
        self.assertEqual([cls.draws for cls in self.stock.values()], [0, 0])
        self.assertEqual(layout.take_splash_draws(), 2)
        self.assertEqual(layout.take_splash_draws(), 0, "Taken over once")

    def test_opted_out_startup_draws_the_art_alone(self):
        layout = self.start(opted_out=True)
        self.assertFalse(layout.startup_pending())
        self.assert_art_alone()
        self.assertEqual([cls.draws for cls in self.stock.values()], [0, 0], "Never delegated to Blender's form")
        self.assertEqual(layout.take_splash_draws(), 0)

    def test_a_start_that_ended_without_the_add_on_draws_the_art_alone(self):
        layout = self.start()
        self.bpy.app.timers.registered.clear()   # open_default raised: Blender removed its timer.
        self.assertFalse(layout.startup_pending())
        self.assert_art_alone()
        self.assertEqual([cls.draws for cls in self.stock.values()], [0, 0])

    def test_once_booted_the_add_on_owns_the_menus(self):
        layout = self.start()
        layout._booted = True
        self.assertFalse(layout.startup_pending())

    def test_the_add_on_loaded_with_startup_opted_out_draws_the_art_alone(self):
        self.start(opted_out=True)
        setup = self.load_addon_menus()
        timers = list(self.bpy.app.timers.registered)
        for cls in setup.OVERRIDES:
            self.assertIs(self.draw(cls.bl_idname, window=SimpleNamespace(as_pointer=lambda: 1)), cls)
        self.assertEqual(self.bpy.app.timers.registered, timers, "Automation keeps the art: no card is scheduled")

    def test_the_splash_opened_after_startup_schedules_angelos_card_and_lays_nothing_out(self):
        self.start()
        setup = self.load_addon_menus()
        setup.startup_settled()
        timers = len(self.bpy.app.timers.registered)
        self.draw("WM_MT_splash_quick_setup", window=SimpleNamespace(as_pointer=lambda: 1))
        self.assertEqual(len(self.bpy.app.timers.registered), timers + 1, "Angelo's setup card is scheduled")

    def test_the_add_on_disabled_gives_the_menus_to_blenders_own_classes_which_draw_the_art_alone(self):
        layout = self.start()
        setup = self.load_addon_menus()
        layout._booted = True
        setup.unregister()   # Preferences > Add-ons, the add-on turned off (not a quit).
        for name, cls in self.stock.items():
            self.assertIs(self.draw(name), cls)
            self.assertEqual(cls.draws, 1)


class StandInTests(StartupSplashCase):
    def test_stand_ins_replace_blenders_menus_before_the_splash(self):
        layout = self.start()
        for cls in layout.STAND_INS:
            self.assertIs(getattr(self.bpy.types, cls.bl_idname), cls)
        self.assertEqual(set(self.record.unregistered), set(self.stock.values()))
        self.assertEqual(starts(self), 1, "The startup file keeps register()'s timer")

    def test_offered_once_and_only_over_blenders_own_menu(self):
        layout = self.start()
        registered = list(self.record.registered)
        layout.load_post(None)   # A later file load.
        self.assertEqual(self.record.registered, registered)
        modules, stock, _ = fake_modules()
        taken = type("Taken", (), {"bl_idname": "WM_MT_splash", "is_registered": True})
        modules["bpy.types"].WM_MT_splash = taken   # A returning launch: the add-on loaded first.
        with mock.patch.dict(sys.modules, modules):
            spec = importlib.util.spec_from_file_location("angelo_default_layout_returning", LAYOUT)
            returning = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(returning)
            returning.offer_stand_ins()
            self.assertIs(modules["bpy.types"].WM_MT_splash, taken, "Never over the add-on's own menu")
            self.assertIs(modules["bpy.types"].WM_MT_splash_quick_setup, returning.ANGELO_MT_startup_quick_setup,
                          "Blender's own menu beside it is still replaced")

    def test_unregister_puts_blenders_menus_back(self):
        layout = self.start()
        layout.unregister()
        for name, cls in self.stock.items():
            self.assertIs(getattr(self.bpy.types, name), cls)
            self.assertTrue(cls.is_registered)


class FileLoadTests(StartupSplashCase):
    def test_a_load_that_dropped_the_startup_card_hands_the_decision_back(self):
        intro = types.ModuleType("angelo_agent.intro")
        intro.file_loaded = lambda: True
        layout = self.start(extra={"angelo_agent.intro": intro})
        layout._startup = False   # open_default handed it to intro.schedule_first_launch.
        self.bpy.app.timers.registered.clear()   # Blender drops non-persistent timers on a load.
        layout.load_post(None)
        self.assertTrue(layout._startup)
        self.assertEqual(starts(self), 1, "Scheduled again")

    def test_a_load_after_the_startup_was_decided_keeps_it_decided(self):
        intro = types.ModuleType("angelo_agent.intro")
        intro.file_loaded = lambda: False
        layout = self.start(extra={"angelo_agent.intro": intro})
        layout._startup = False
        layout.load_post(None)
        self.assertFalse(layout._startup)


class SourceTests(unittest.TestCase):
    def test_intro_forgets_a_dropped_timer_and_reports_an_owed_startup(self):
        source = (ADDON / "intro.py").read_text()
        loaded = source.split("\ndef file_loaded():", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("owed = splash_setup.take_setup_lost()", loaded)
        self.assertIn("if _timer is not None and not bpy.app.timers.is_registered(_timer):", loaded)
        self.assertIn("_timer, _timer_startup = None, False", loaded)
        self.assertIn("_timer, _timer_startup = open_when_ready, startup", source)
        setup = (ADDON / "splash_setup.py").read_text()
        self.assertIn('if outcome == "lost":\n            _setup_lost = True', setup)

    def test_splash_setup_takes_the_menus_and_the_count_over(self):
        source = (ADDON / "splash_setup.py").read_text()
        register = source.split("\ndef register():", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("current = getattr(bpy.types, cls.bl_idname, None)", register)
        self.assertIn("layout.take_splash_draws()", register)
        self.assertIn("_note_drawn(count)", register)


class NativeCoverSourceTests(unittest.TestCase):
    """The window manager's cover is drawn after everything the areas draw and before popups, from one runtime
    RNA colour that is never saved (``wm_angelo_cover.cc``); the pixels are ``test_blender_first_launch_frames``."""

    def test_drawn_after_the_areas_and_their_overlays_and_before_popups(self):
        source = (ROOT / "source/blender/windowmanager/intern/wm_draw.cc").read_text()
        onscreen = source.split("static void wm_draw_window_onscreen(", 1)[1].split("\nstatic void ", 1)[0]
        order = [onscreen.index(marker) for marker in (
            "wm_region_draw_overlay(C, area, &region);",        # Region overlays: the timeline's playhead.
            "wm_draw_region_blend(&region, 0, true);",          # Overlapping regions blended in.
            "ED_screen_draw_edges(win);",
            "wm_draw_callbacks(win);",
            "wm_angelo_startup_cover_draw(win);",
            "/* Blend in floating regions (menus). */")]       # Popups: Blender's splash stays above the cover.
        self.assertEqual(order, sorted(order))

    def test_one_runtime_colour_over_the_editor_areas_only(self):
        cover = (ROOT / "source/blender/windowmanager/intern/wm_angelo_cover.cc").read_text()
        self.assertIn("WM_window_screen_rect_calc(win, &rect);", cover, "Editor areas: not the top or status bar")
        self.assertIn("namespace blender {", cover)
        self.assertLess(len(cover.splitlines()), 500)
        rna = (ROOT / "source/blender/makesrna/intern/rna_wm.cc").read_text()
        definition = rna.split('RNA_def_property(srna, "angelo_startup_cover"', 1)[1].split("\n\n", 1)[0]
        self.assertIn("rna_WindowManager_angelo_startup_cover_get", definition)
        self.assertNotIn("sdna", definition, "Runtime only: never written to a file")
        self.assertIn("intern/wm_angelo_cover.cc", (ROOT / "source/blender/windowmanager/CMakeLists.txt").read_text())


class CoverTests(StartupSplashCase):
    """Angelo's cover hides the stock startup layout from the first frame until Angelo's layout settles
    (1.0.5 screenshot review: the stock Layout showed under the splash). The pixels: ``test_blender_first_launch_frames``."""

    def test_covering_from_the_startup_file_until_the_start_has_run(self):
        layout = self.start()
        self.assertTrue(layout.covering(), "From before the first frame")
        self.assertIn(layout._cover_watch, self.bpy.app.timers.registered)
        self.bpy.app.timers.registered.remove(layout._timer)   # The start ran (Director settled, nothing moving).
        self.assertFalse(layout.covering())

    def test_covering_while_director_rebuilds_or_the_chat_dock_moves(self):
        director = types.ModuleType("angelo_agent.director")
        ui = types.ModuleType("angelo_agent.ui")
        director._timer, ui._layout_timer = None, None
        layout = self.start(extra={"angelo_agent.director": director, "angelo_agent.ui": ui})
        self.bpy.app.timers.registered.remove(layout._timer)
        director._timer = object()
        self.assertTrue(layout.covering(), "Director's rebuild shows its intermediate layouts otherwise")
        director._timer, ui._layout_timer = None, object()
        self.assertTrue(layout.covering())
        ui._layout_timer = None
        self.assertFalse(layout.covering())

    def test_never_when_angelos_start_will_not_run_and_never_for_long(self):
        self.assertFalse(self.start(opted_out=True).covering(), "Automation's opt-out shows its own layout")
        layout = self.start()
        layout._cover["since"] -= layout.COVER_LIMIT + 1
        self.assertFalse(layout.covering(), "A start that never settles stops covering")

    def test_the_window_managers_cover_is_set_and_cleared(self):
        """A binary with ``WindowManager.angelo_startup_cover`` paints it, not region handlers; settled, it clears."""
        wm = SimpleNamespace(angelo_startup_cover=(0.0, 0.0, 0.0, 0.0), windows=[])
        modules, self.stock, self.record = fake_modules(False)
        modules["bpy"].context = SimpleNamespace(window_manager=wm, preferences=None)
        patch = mock.patch.dict(sys.modules, modules)
        patch.start()
        self.addCleanup(patch.stop)
        self.bpy = modules["bpy"]
        spec = importlib.util.spec_from_file_location("angelo_default_layout", LAYOUT)
        layout = importlib.util.module_from_spec(spec)
        sys.modules["angelo_default_layout"] = layout
        spec.loader.exec_module(layout)
        layout.register()
        self.assertEqual(wm.angelo_startup_cover[3], 1.0, "Opaque from before the first frame")
        self.assertEqual(layout._cover["handlers"], [], "No per-region paint where the window manager paints")
        self.bpy.app.timers.registered.remove(layout._timer)
        self.assertIsNone(layout._cover_watch())
        self.assertEqual(wm.angelo_startup_cover, (0.0, 0.0, 0.0, 0.0))

    def test_a_file_opened_later_is_covered_again(self):
        layout = self.start()
        self.bpy.app.timers.registered.clear()
        self.assertFalse(layout.covering())
        layout.load_post(None)
        self.assertTrue(layout.covering())


if __name__ == "__main__":
    unittest.main()
