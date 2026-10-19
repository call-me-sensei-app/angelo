# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Blender's startup splash before Angelo's add-on loads (``angelo_default_layout``), on a stand-in ``bpy``.

Blender builds its startup splash before the main loop, while ``angelo_agent`` is enabled later by the default
layout's timer. Covered: the first ``load_post`` puts stand-ins in place of Blender's two splash menus (only where
Blender's own menu is registered, once); they draw nothing while Angelo's startup is pending and Blender's own menu
when it will not run (opted out, a failed start); ``splash_setup`` takes the menus and the draw count over; a file
load that dropped Angelo's startup card hands the startup decision back. The real first frames are
``test_blender_first_launch_frames``'s.
"""

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
LAYOUT = ROOT / "scripts/startup/angelo_default_layout.py"
ADDON = ROOT / "scripts/addons_core/angelo_agent"


class Timers:
    def __init__(self):
        self.registered = []

    def register(self, function, first_interval=0.0, persistent=False):
        self.registered.append(function)

    def is_registered(self, function):
        return function in self.registered

    def unregister(self, function):
        self.registered.remove(function)


def stock_menu(name):
    """Blender's own splash menu (``bl_operators.wm``): its draw counts on the class (the form was drawn)."""
    cls = type(name, (), {"bl_idname": name, "is_registered": True, "draws": 0})

    def draw(self, context):
        cls.draws += 1   # Called with the stand-in as ``self``, as the stand-in delegates.
    cls.draw = draw
    return cls


def fake_modules(opted_out=False):
    record = SimpleNamespace(registered=[], unregistered=[])
    bpy = types.ModuleType("bpy")
    bpy_types = types.ModuleType("bpy.types")
    bpy_types.Menu = type("Menu", (), {"is_registered": False})
    bpy_types.SpaceConsole = SimpleNamespace(bl_rna=SimpleNamespace(properties={"is_coding_agent": None}))
    stock = {name: stock_menu(name) for name in ("WM_MT_splash_quick_setup", "WM_MT_splash")}
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
    handlers.load_post = []
    bpy.app.handlers = handlers
    bl_operators = types.ModuleType("bl_operators")
    wm = types.ModuleType("bl_operators.wm")
    wm.__dict__.update(stock)
    bl_operators.wm = wm
    angelo_agent = types.ModuleType("angelo_agent")
    product_env = types.ModuleType("angelo_agent.product_env")
    product_env.flag = lambda name: opted_out and name == "DISABLE_DEFAULT_CHAT"
    angelo_agent.product_env = product_env
    modules = {"bpy": bpy, "bpy.types": bpy_types, "bpy.app": bpy.app, "bpy.app.handlers": handlers,
               "bl_operators": bl_operators, "bl_operators.wm": wm, "angelo_agent": angelo_agent,
               "angelo_agent.product_env": product_env}
    return modules, stock, record


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

    def draw(self, idname):
        menu = getattr(self.bpy.types, idname)
        menu.draw(menu(), SimpleNamespace())


class StandInTests(StartupSplashCase):
    def test_stand_ins_replace_blenders_menus_before_the_splash(self):
        layout = self.start()
        for cls in layout.STAND_INS:
            self.assertIs(getattr(self.bpy.types, cls.bl_idname), cls)
        self.assertEqual(set(self.record.unregistered), set(self.stock.values()))
        self.assertEqual(len(self.bpy.app.timers.registered), 1, "The startup file keeps register()'s timer")

    def test_pending_startup_draws_the_art_alone(self):
        layout = self.start()
        self.assertTrue(layout.startup_pending())
        self.draw("WM_MT_splash_quick_setup")
        self.draw("WM_MT_splash")
        self.assertEqual([cls.draws for cls in self.stock.values()], [0, 0], "No Blender form")
        self.assertEqual(layout.take_splash_draws(), 2)
        self.assertEqual(layout.take_splash_draws(), 0, "Taken over once")

    def test_opted_out_startup_draws_blenders_own_menu(self):
        layout = self.start(opted_out=True)
        self.assertFalse(layout.startup_pending())
        self.draw("WM_MT_splash_quick_setup")
        self.assertEqual(self.stock["WM_MT_splash_quick_setup"].draws, 1)
        self.assertEqual(layout.take_splash_draws(), 0)

    def test_a_start_that_ended_without_the_add_on_draws_blenders_own_menu(self):
        layout = self.start()
        self.bpy.app.timers.registered.clear()   # open_default raised: Blender removed its timer.
        self.assertFalse(layout.startup_pending())
        self.draw("WM_MT_splash")
        self.assertEqual(self.stock["WM_MT_splash"].draws, 1)

    def test_once_booted_the_add_on_owns_the_menus(self):
        layout = self.start()
        layout._booted = True
        self.assertFalse(layout.startup_pending())

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
        self.assertEqual(len(self.bpy.app.timers.registered), 1, "Scheduled again")

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


if __name__ == "__main__":
    unittest.main()
