# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's add-on stays enabled through every preference read (``prefs_reload``), without Blender.

Blender reads preferences as ``addon_utils.disable_all()``, the new preferences, then ``addon_utils.reset_all()``
(``wm_homefile_read``). The stand-in below does the same with add-on modules and a preferences list, so each read
is checked as Blender runs it: Load Factory Preferences (nothing listed, Angelo's settings back to defaults), Revert
to Saved Preferences (the saved entry and its settings kept), quitting (Angelo is disabled as before), a person who
disabled Angelo (never turned back on), an earlier Angelo's settings import (``keep_loaded=False``: it reloads) and
Reload Scripts (no second entry). The running app is ``test_blender_prefs_and_quit``.
"""

import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import prefs_reload  # noqa: E402

CORE = "angelo_agent"


class Addons(list):
    """``Preferences.addons``: entries with a module name and their own settings."""

    def new(self):
        entry = SimpleNamespace(module="", settings={})
        self.append(entry)
        return entry

    def get(self, name):
        return next((entry for entry in self if entry.module == name), None)

    def __contains__(self, name):
        return self.get(name) is not None


class Blender:
    """Add-on modules, preferences and Blender's own ``disable_all`` / ``reset_all`` / exit."""

    def __init__(self, listed=(CORE, "old_tool"), loaded=(CORE, "old_tool")):
        self.events, self.modules = [], {}
        for name in set(listed) | set(loaded) | {"new_tool"}:
            module = ModuleType(name)
            module.__addon_enabled__ = name in loaded
            if name == CORE:
                module.register, module.unregister = self.registered, self.unregistered
            self.modules[name] = module
        self.prefs = SimpleNamespace(addons=self.entries(listed))
        self.addon_utils = ModuleType("addon_utils")
        self.addon_utils.disable_all, self.addon_utils.reset_all = self.disable_all, self.reset_all
        self.blenders = (self.disable_all, self.reset_all)
        self.bpy = ModuleType("bpy")
        handlers = SimpleNamespace(exit_pre=[], persistent=lambda function: function)
        self.bpy.app = SimpleNamespace(handlers=handlers)
        self.bpy.context = SimpleNamespace(preferences=self.prefs)

    def entries(self, names, settings=None):
        addons = Addons()
        for name in names:
            addons.new().module = name
            addons[-1].settings = dict((settings or {}).get(name, {}))
        return addons

    def registered(self):
        self.events.append(("register", CORE))
        prefs_reload.install()

    def unregistered(self):
        self.events.append(("unregister", CORE))
        prefs_reload.uninstall()

    def disable_all(self):
        for name, module in sorted(self.modules.items()):
            if module.__addon_enabled__:
                self.set(name, False)

    def reset_all(self, **_options):
        for name, module in sorted(self.modules.items()):
            if (name in self.prefs.addons) != module.__addon_enabled__:
                self.set(name, name in self.prefs.addons)

    def set(self, name, enabled):
        module = self.modules[name]
        self.events.append(("enable" if enabled else "disable", name))
        module.__addon_enabled__ = enabled
        if name == CORE:
            (module.register if enabled else module.unregister)()

    def read(self, names=(), settings=None):
        """A preference read: what ``wm_homefile_read`` runs, with ``names`` listed by the new preferences."""
        self.events.clear()
        self.addon_utils.disable_all()
        self.prefs.addons = self.entries(names, settings)
        self.addon_utils.reset_all()

    def quit(self):
        """``WM_exit_ex``: the ``exit_pre`` handlers, then ``bpy.utils._on_exit`` disables every add-on."""
        self.events.clear()
        for handler in list(self.bpy.app.handlers.exit_pre):
            handler(None)
        self.addon_utils.disable_all()

    def enabled(self, name):
        return self.modules[name].__addon_enabled__

    def __enter__(self):
        self.patch = mock.patch.dict(sys.modules, dict(self.modules, bpy=self.bpy, addon_utils=self.addon_utils))
        self.patch.start()
        prefs_reload.install()   # What registering Angelo's add-on does.
        return self

    def __exit__(self, *exc):
        prefs_reload.uninstall(force=True)
        self.patch.stop()
        return False


class PreferenceReadTests(unittest.TestCase):
    def test_load_factory_preferences_keeps_angelo_loaded_with_its_default_settings(self):
        with Blender() as blender:
            blender.prefs.addons.get(CORE).settings = {"experimental_voice": True}
            blender.read(())   # Factory preferences list no add-on.
            self.assertNotIn(("unregister", CORE), blender.events, "the chat and its turns stay as they are")
            self.assertTrue(blender.enabled(CORE))
            self.assertIn(CORE, blender.prefs.addons, "listed, so Preferences and its own settings work")
            self.assertEqual(blender.prefs.addons.get(CORE).settings, {}, "a new entry: Angelo's own defaults")
            self.assertIn(("disable", "old_tool"), blender.events, "everything else follows the new preferences")
            self.assertFalse(blender.enabled("old_tool"))

    def test_revert_to_saved_preferences_keeps_the_saved_entry(self):
        with Blender() as blender:
            blender.read((CORE, "new_tool"), settings={CORE: {"experimental_voice": True}})
            self.assertEqual(blender.prefs.addons.get(CORE).settings, {"experimental_voice": True})
            self.assertEqual([e.module for e in blender.prefs.addons].count(CORE), 1)
            self.assertIn(("enable", "new_tool"), blender.events)
            self.assertNotIn(("unregister", CORE), blender.events)

    def test_every_later_read_keeps_it_too(self):
        with Blender() as blender:
            for names in ((), ("new_tool",), (), (CORE,)):
                blender.read(names)
                self.assertTrue(blender.enabled(CORE), names)
                self.assertNotIn(("unregister", CORE), blender.events, names)
            self.assertIs(blender.addon_utils.disable_all, prefs_reload._disable_all)

    def test_quitting_disables_angelo_as_before(self):
        with Blender() as blender:
            blender.quit()
            self.assertIn(("unregister", CORE), blender.events, "its chats are saved on the way out")
            self.assertFalse(blender.enabled(CORE))
            self.assertEqual((blender.addon_utils.disable_all, blender.addon_utils.reset_all), blender.blenders)
            self.assertEqual(blender.bpy.app.handlers.exit_pre, [])

    def test_a_person_who_disabled_angelo_is_never_overridden(self):
        with Blender() as blender:
            blender.set(CORE, False)   # Preferences > Add-ons: its unregister takes the wrappers out.
            self.assertEqual((blender.addon_utils.disable_all, blender.addon_utils.reset_all), blender.blenders)
            blender.read(())
            self.assertFalse(blender.enabled(CORE))
            self.assertNotIn(CORE, blender.prefs.addons)

    def test_a_stale_wrapper_still_reaches_blender(self):
        with Blender() as blender:
            disable_all, reset_all = blender.addon_utils.disable_all, blender.addon_utils.reset_all
            prefs_reload.uninstall()
            blender.events.clear()
            disable_all()   # Someone kept the wrapped function: it is Blender's own now.
            self.assertIn(("disable", CORE), blender.events)
            blender.prefs.addons = blender.entries(())
            reset_all()
            self.assertNotIn(CORE, blender.prefs.addons)


class HeldReadTests(unittest.TestCase):
    def test_an_earlier_angelos_import_reloads_it_and_keeps_it_listed(self):
        with Blender() as blender:
            with prefs_reload.angelo_stays_enabled(keep_loaded=False):
                blender.read(())
            self.assertEqual([e for e in blender.events if e[1] == CORE],
                             [("disable", CORE), ("unregister", CORE), ("enable", CORE), ("register", CORE)])
            self.assertTrue(blender.enabled(CORE))
            self.assertTrue(prefs_reload.installed(), "its unregister during the held read kept the wrappers")
            blender.read(())
            self.assertNotIn(("unregister", CORE), blender.events, "later reads keep it loaded again")

    def test_a_held_read_without_the_session_wrappers_puts_blenders_back(self):
        with Blender() as blender:
            prefs_reload.uninstall()
            with prefs_reload.angelo_stays_enabled():
                blender.read(())
            self.assertTrue(blender.enabled(CORE))
            self.assertNotIn(("unregister", CORE), blender.events)
            self.assertEqual((blender.addon_utils.disable_all, blender.addon_utils.reset_all), blender.blenders)

    def test_reload_scripts_adds_no_second_entry(self):
        with Blender() as blender:
            blender.addon_utils.reset_all(reload_scripts=True)
            self.assertEqual([e.module for e in blender.prefs.addons].count(CORE), 1)


class RegistrationTests(unittest.TestCase):
    def test_the_add_on_installs_the_wrappers_and_takes_them_out(self):
        source = (ROOT / "scripts/addons_core/angelo_agent/__init__.py").read_text(encoding="utf-8")
        register, unregister = source.split("def unregister():", 1)
        self.assertIn("prefs_reload.install()", register)
        self.assertIn("prefs_reload.uninstall()", unregister)
        self.assertLess(unregister.index("prefs_reload.uninstall()"), unregister.index("\n    ui.unregister()"))

    def test_the_settings_import_uses_the_same_guard(self):
        source = (ROOT / "scripts/addons_core/angelo_agent/settings_import_apply.py").read_text(encoding="utf-8")
        self.assertIn("from .prefs_reload import CORE_ADDONS, angelo_stays_enabled", source)
        self.assertNotIn("addon_utils.disable_all =", source)


if __name__ == "__main__":
    unittest.main()
