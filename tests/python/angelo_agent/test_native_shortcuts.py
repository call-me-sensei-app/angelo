# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every window-wide Angelo shortcut is free in every Blender editor (``shortcuts.py``), without Blender.

Evidence: Ctrl+Option+D, the Director toggle, worked only over the 3D view. Blender consults region and area keymaps
before the Window keymap, and its "User Interface" keymap (on almost every region) binds Ctrl+Alt+D to
``anim.driver_button_remove``, which has no poll and consumes the key even with no driver under the pointer; the
Outliner binds it to ``outliner.drivers_delete_selected``. The
class: a Window-keymap shortcut whose chord any editor's keymap also binds. Checked here against the keymaps Blender
generates (default with right- and left-click select, legacy, a set of non-default preferences, industry
compatible); ``test_blender_director_shortcut`` proves the Director chord in each editor of the running app.
The contract: Ctrl+Option+M shown everywhere; 1.0's Ctrl+Option+D kept only over the 3D view
(``shortcuts.DIRECTOR_VIEWPORT``), which takes nothing that acts there.
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
ADDON = ROOT / "scripts/addons_core/angelo_agent"
KEYMAP_DATA = ROOT / "scripts/presets/keyconfig/keymap_data"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))

import angelo_agent.shortcuts as shortcuts  # noqa: E402  (imports no bpy at module level)

MODIFIERS = ("ctrl", "alt", "shift", "oskey")
# Window-keymap items registered outside shortcuts.py, each a deliberate override of a Window item of Blender's own:
# an add-on item is put first in the keymap it joins (wm_keymap_addon_add), so it wins there and nowhere else.
WINDOW_OVERRIDES = {
    # Shift+F4 opens the Python Console, never the cached chat surface (ui.register; Blender: wm.context_set_enum).
    ("angelo.editor_type", ("F4", False, False, True, False)): "ui.py",
}


def _module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def blender_keymaps():
    """``{variant: generated keymaps}``: what Blender's keyconfig presets build, read without Blender."""
    default = _module(KEYMAP_DATA / "blender_default.py", "angelo_test_blender_default")
    bpy = types.ModuleType("bpy")
    bpy.utils = SimpleNamespace(execfile=lambda path: _module(path, "angelo_test_blender_default_copy"))
    with mock.patch.dict(sys.modules, {"bpy": bpy, "bpy.utils": bpy.utils}):
        industry = _module(KEYMAP_DATA / "industry_compatible_data.py", "angelo_test_industry")
        industry_keymaps = industry.generate_keymaps(industry.Params())
    return {
        "default": default.generate_keymaps(default.Params()),
        "default, left-click select": default.generate_keymaps(default.Params(select_mouse='LEFT')),
        "legacy": default.generate_keymaps(default.Params(legacy=True)),
        "default, non-default preferences": default.generate_keymaps(default.Params(
            use_alt_click_leader=True, use_v3d_tab_menu=True, use_region_toggle_pie=True,
            use_key_activate_tools=True, use_select_all_toggle=True, spacebar_action='SEARCH')),
        "industry compatible": industry_keymaps,
    }


def chord_of(event):
    """``(type, ctrl, alt, shift, oskey)`` of a keymap item's event, or None for one no key press can match."""
    if event.get("key_modifier") or event.get("value") not in ("PRESS", "CLICK", "ANY", None):
        return None
    return (event["type"], *(bool(event.get(key)) for key in MODIFIERS))


def takers(keymaps, chord):
    """Every non-modal ``keymap:operator`` a press of ``chord`` reaches, ``any``-modifier items included."""
    found = []
    for name, space, content in keymaps:
        if space.get("modal"):
            continue   # A modal map only acts while its own tool runs.
        for idname, event, _properties in content["items"]:
            if event.get("any") and event["type"] == chord[0] and not event.get("key_modifier"):
                found.append(f"{name}:{idname}")
            elif chord_of(event) == chord:
                found.append(f"{name}:{idname}")
    return found


def angelo_chord(spec):
    return (spec["type"], *(bool(spec.get(key)) for key in MODIFIERS))


class FreeInEveryEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.variants = blender_keymaps()

    def test_every_window_shortcut_is_free_in_every_non_modal_keymap(self):
        for shortcut in shortcuts.WINDOW_SHORTCUTS:
            self.assertEqual((shortcut.keymap, shortcut.space_type), ("Window", 'EMPTY'))
            for variant, keymaps in self.variants.items():
                with self.subTest(shortcut=shortcut.idname, keymap=variant):
                    self.assertEqual(takers(keymaps, angelo_chord(shortcut.chord)), [])

    def test_the_viewport_binding_takes_nothing_that_acts_over_the_3d_view(self):
        """1.0's Ctrl+Option+D, kept over the 3D view's main region: every other keymap binding it acts only in
        other regions, except the legacy keymap's Window debug menu, which it hides over the 3D view (accepted:
        a developer menu, still in F3 search)."""
        elsewhere = {"User Interface", "Outliner", "NLA Editor", "Sequencer"}
        accepted = {"legacy": {"Window:wm.debug_menu"}}
        viewport = shortcuts.DIRECTOR_VIEWPORT
        self.assertEqual((viewport.keymap, viewport.space_type), ("3D View", 'VIEW_3D'))
        for variant, keymaps in self.variants.items():
            with self.subTest(keymap=variant):
                found = takers(keymaps, angelo_chord(viewport.chord))
                left = [t for t in found if t.split(":")[0] not in elsewhere and t not in accepted.get(variant, set())]
                self.assertEqual(left, [])
                self.assertNotIn("3D View", {t.split(":")[0] for t in found})

    def test_overrides_replace_a_window_item_of_blenders(self):
        """An override reaches exactly the editors Blender's own item reached: it replaces a binding. (Industry
        compatible has no Window Shift+F4, so there the item is new, and its Image editor's zoom keeps Shift+F4: an
        add-on Window item never takes an editor's chord.)"""
        for (idname, chord), _ in WINDOW_OVERRIDES.items():
            for variant, keymaps in self.variants.items():
                if variant == "industry compatible":
                    continue
                with self.subTest(shortcut=idname, keymap=variant):
                    self.assertTrue(any(t.startswith("Window:") for t in takers(keymaps, chord)))

    def test_the_check_finds_the_old_director_chord(self):
        """The control: the chord that was shadowed is reported, in the editors where it was."""
        found = takers(self.variants["default"], ("D", True, True, False, False))
        self.assertIn("User Interface:anim.driver_button_remove", found)
        self.assertIn("Outliner:outliner.drivers_delete_selected", found)

    def test_shortcuts_are_distinct(self):
        chords = [angelo_chord(s.chord) for s in shortcuts.WINDOW_SHORTCUTS + (shortcuts.DIRECTOR_VIEWPORT,)]
        chords += [c for _, c in WINDOW_OVERRIDES]
        self.assertEqual(len(chords), len(set(chords)))

    def test_add_on_items_win_inside_their_own_keymap(self):
        """Why a Window override works and nothing more: add-on items are put first in the keymap they join."""
        source = (ROOT / "source/blender/windowmanager/intern/wm_keymap.cc").read_text()
        body = source.split("static void wm_keymap_addon_add(", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("BLI_addhead(&keymap->items", body)


class RegistrationTests(unittest.TestCase):
    def window_registrations(self):
        """``(file, line)`` of each add-on keymap item created on a Window keymap outside shortcuts.py."""
        found = []
        for path in sorted(ADDON.rglob("*.py")):
            if path.name == "shortcuts.py" or "tests" in path.parts:
                continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "new"
                        and any(k.arg == "name" and isinstance(k.value, ast.Constant) and k.value.value == "Window"
                                for k in node.keywords)):
                    found.append(path.name)
        return found

    def test_window_shortcuts_are_registered_from_shortcuts_py(self):
        self.assertEqual(self.window_registrations(), sorted(set(WINDOW_OVERRIDES.values())))

    def test_each_shortcut_is_registered_by_its_module(self):
        self.assertIn("shortcuts.add(keyconfig, shortcuts.DIRECTOR)", (ADDON / "director.py").read_text())
        self.assertIn("shortcuts.add(keyconfig, shortcuts.DIRECTOR_VIEWPORT)", (ADDON / "director.py").read_text())
        self.assertIn("shortcuts.add(keyconfig, shortcuts.CHAT)", (ADDON / "chat_toggle.py").read_text())

    def test_add_creates_the_item_on_the_window_keymap(self):
        created = []
        keymap = SimpleNamespace(keymap_items=SimpleNamespace(new=lambda *a, **k: created.append((a, k)) or "item"))
        keyconfig = SimpleNamespace(keymaps=SimpleNamespace(new=lambda **k: created.append(k) or keymap))
        self.assertEqual(shortcuts.add(keyconfig, shortcuts.DIRECTOR), (keymap, "item"))
        self.assertEqual(created, [{"name": "Window", "space_type": 'EMPTY'},
                                   (("angelo.director_toggle", 'M', 'PRESS'), {"ctrl": True, "alt": True})])
        created.clear()
        shortcuts.add(keyconfig, shortcuts.DIRECTOR_VIEWPORT)
        self.assertEqual(created, [{"name": "3D View", "space_type": 'VIEW_3D'},
                                   (("angelo.director_toggle_viewport", 'D', 'PRESS'), {"ctrl": True, "alt": True})])

    def test_the_chords_the_maintainer_chose(self):
        """Ctrl+Option+M shown everywhere; Ctrl+Option+D kept only over the 3D view, under an operator of its own."""
        self.assertEqual(shortcuts.DIRECTOR.idname, "angelo.director_toggle")
        self.assertEqual(angelo_chord(shortcuts.DIRECTOR.chord), ("M", True, True, False, False))
        self.assertEqual(angelo_chord(shortcuts.DIRECTOR_VIEWPORT.chord), ("D", True, True, False, False))
        self.assertNotEqual(shortcuts.DIRECTOR_VIEWPORT.idname, shortcuts.DIRECTOR.idname)
        self.assertEqual(shortcuts.CHAT.idname, "angelo.toggle_chat")

    def test_the_viewport_operator_is_never_shown(self):
        source = (ADDON / "director.py").read_text()
        body = source.split("class ANGELO_OT_director_toggle_viewport(", 1)[1].split("\nclass ", 1)[0]
        self.assertIn("bl_options = {'INTERNAL'}", body)
        self.assertNotIn("angelo.director_toggle_viewport", source.replace(body, ""))

    def test_the_mode_menu_offers_the_toggle_with_its_shortcut(self):
        """The item for the other mode is the toggle, so Blender draws its shortcut beside it."""
        source = (ADDON / "director.py").read_text()
        menu = source.split("class ANGELO_MT_workspace_mode(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('operator("angelo.director_toggle", text="Manual Mode"', menu)
        self.assertIn('operator("angelo.director_toggle", text="Director Mode"', menu)


class NamedAsBoundTests(unittest.TestCase):
    def welcome(self):
        bpy = types.ModuleType("bpy")
        with mock.patch.dict(sys.modules, {"bpy": bpy, "blf": types.ModuleType("blf")}):
            sys.modules.pop("angelo_agent.welcome_screen", None)
            try:
                import angelo_agent.welcome_screen as module
            except ImportError as error:   # Other drawing modules this one imports need Blender.
                self.skipTest(f"welcome_screen needs Blender here: {error}")
        return module

    def test_welcome_key_caps(self):
        welcome = self.welcome()
        chord = {**shortcuts.DIRECTOR.chord, "name": "M"}
        self.assertEqual(welcome.key_caps(chord, True), ("⌃", "⌥", "M"))
        self.assertEqual(welcome.key_caps(chord, False), ("Ctrl", "Alt", "M"))
        self.assertEqual(welcome.key_caps({"ctrl": True, "shift": True, "type": "D", "name": "D"}, True), ("⌃", "⇧", "D"))
        self.assertEqual(welcome.key_caps({"oskey": True, "type": "F5", "name": "F5"}, False), ("Win", "F5"))

    def test_the_welcome_and_the_chat_hint_read_the_keymap(self):
        welcome = (ADDON / "welcome_screen.py").read_text().split("\ndef shortcut_keys(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("shortcuts.bound(shortcuts.DIRECTOR[0])", welcome)
        hint = (ADDON / "chat_hint.py").read_text().split("\ndef shortcut(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("shortcuts.bound(shortcuts.CHAT[0])", hint)

    def test_no_shown_text_names_the_old_director_chord(self):
        """Every string the add-on can show (docstrings, which only explain, aside) is free of the 1.0 chord."""
        stale = ("Ctrl+Alt+D", "Ctrl+Option+D", "Control+Option+D", "⌃⌥D")
        for path in ADDON.rglob("*.py"):
            if "tests" in path.parts:
                continue
            tree = ast.parse(path.read_text())
            docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                          if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) and node.body
                          and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)}
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                    with self.subTest(file=path.name, line=node.lineno):
                        self.assertFalse([s for s in stale if s in node.value.replace(" ", "")])
        self.assertNotIn('("⌃", "⌥", "D")', (ADDON / "welcome_screen.py").read_text())


if __name__ == "__main__":
    unittest.main()
