# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's agent add-on is never started from saved preferences in a background Angelo
(``angelo_background_addons``).

Angelo starts its add-on in its window by a timer (``scripts/startup/angelo_default_layout.py``), which leaves an
entry in the preferences; once the preferences are saved, every background Angelo that reads them (a person's
command-line render, an engine's import of a ``.blend``) enabled the whole add-on as Blender enables any add-on its
preferences list. It then loaded the active chat, bound it to the opened file and wrote it: the second root of the
stuck chat (the showcase's Godot session, 2026-11-14; ``test_blender_second_process_file_load`` starts the real
binary). Pinned here:

* the rule: only Angelo's agent add-on, only in a background process; any other add-on, and the agent add-on in
  a window, start from preferences as before;
* both places where Blender enables the add-ons its preferences list ask the rule (``addon_utils._initialize_once``
  at start-up and ``reset_all`` after preferences are read again), and ``enable`` itself does not: a script, a
  suite or ``--addons`` that asks for the add-on by name still gets it;
* switching a chat editor back to the Python console, which the add-on's ``unregister`` does for every chat editor
  of the open file, never opens a chat: it remembers the draft of the chat this process already has open, or
  nothing. That call was the one that loaded the active chat in the background Angelo (traced on Linux,
  2026-11-15: ``unregister`` -> ``editor_types.select_console`` -> ``ui.get_controller()``), and the close that
  follows then wrote it.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import re
import sys
import unittest
from pathlib import Path

MODULES = Path(__file__).resolve().parents[3] / "scripts/modules"
ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(MODULES))
import angelo_background_addons  # noqa: E402


class TheRule(unittest.TestCase):
    def test_only_the_agent_addon_and_only_in_a_background_process(self):
        held = angelo_background_addons.held
        self.assertTrue(held("angelo_agent", background=True))
        self.assertFalse(held("angelo_agent", background=False))
        for other in ("io_scene_gltf2", "cycles", "angelo_agent_extras", "bl_ext.user_default.angelo_agent", "", None):
            self.assertFalse(held(other, background=True), other)


class BlenderAsksIt(unittest.TestCase):
    def setUp(self):
        self.text = (MODULES / "addon_utils.py").read_text(encoding="utf-8")

    def function(self, name):
        start = self.text.index("\ndef %s(" % name)
        return self.text[start:self.text.index("\ndef ", start + 1)]

    def test_the_addons_preferences_list_are_asked_about_at_startup_and_after_a_reload(self):
        startup = self.function("_initialize_once")
        loop = startup[startup.index("for addon in _preferences.addons:"):]
        self.assertRegex(loop, re.compile(r"_angelo_held\(module_name\):\s*\n\s*continue\s*\n\s*enable\(", re.S))
        reload = self.function("reset_all")
        self.assertRegex(reload, re.compile(
            r"elif is_enabled:\s*\n\s*if not _angelo_held\(mod_name\):[^\n]*\n\s*enable\(mod_name"))

    def test_asking_for_the_addon_by_name_still_starts_it(self):
        self.assertNotIn("_angelo_held", self.function("enable"))
        self.assertEqual(self.text.count("_angelo_held("), 3, "the rule, and the two places that ask it")


class LeavingAChatEditorOpensNoChat(unittest.TestCase):
    def test_a_draft_is_remembered_only_for_a_chat_this_process_has_open(self):
        text = (ADDON / "editor_types.py").read_text(encoding="utf-8")
        start = text.index("\ndef select_console(")
        function = text[start:text.index("\ndef ", start + 1)]
        self.assertNotIn("get_controller()", function)
        self.assertRegex(function, re.compile(
            r"controller = open_chat\(ui\)\s*\n\s*if controller is not None:\s*\n\s*ui\.remember_draft\(controller\)"))
        start = text.index("\ndef open_chat(")
        helper = text[start:text.index("\ndef ", start + 1)]
        self.assertNotIn("get_controller", helper)
        self.assertIn("ui._controllers.get(", helper)


if __name__ == "__main__":
    unittest.main()
