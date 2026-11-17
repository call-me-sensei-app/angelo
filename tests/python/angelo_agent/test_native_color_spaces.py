# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Colour spaces and views as the active OCIO configuration names them (``angelo_agent.color_spaces``).

Blender 5.3 lets a Project switch the configuration to ACES 1.3 or 2.0 at runtime (``ocio://`` URIs), which has no
colour space called "Non-Color" or "sRGB" and no view called "Standard": a name written as a literal raises there,
and a name compared as a literal misjudges. Covered:

* stored values (data) go through the configuration's data role, never a name;
* sRGB is found as Blender finds it (IMB_colormanagement_srgb_colorspace_name_get): interop ID
  ``srgb_rec709_scene``, then ``srgb_rec709_display``, then the common names, then the default byte role;
* an un-tone-mapped view is "Standard", else ACES's "Un-tone-mapped", else "Raw"; with none the view is left;
* a colour space's kind (``srgb``, ``data``, ``other``) for rules that judge one, older facts without it included;
* no module of the add-on writes or compares a colour space, view or display by a literal name (the source guard).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import color_spaces  # noqa: E402


class Enum:
    def __init__(self, identifiers):
        self.enum_items = [type("Item", (), {"identifier": name}) for name in identifiers]


class Settings:
    """A stand-in for ColorManagedColorspaceSettings under a configuration with these names and interop IDs."""

    def __init__(self, names, interop=None, name=None, data_name="Raw"):
        self.__dict__.update(_names=list(names), _interop=dict(interop or {}), name=name or names[0],
                             _data_name=data_name, _is_data=False)
        # As Blender's: the configuration builds these items at runtime, so none are listed outside a context.
        self.__dict__["bl_rna"] = type("RNA", (), {"properties": {"name": Enum([])}})

    def __setattr__(self, key, value):
        if key == "interop_id":
            if value not in self._interop:
                raise TypeError("enum %r not found" % value)
            self.__dict__["name"] = self._interop[value]
        elif key == "name":
            if value not in self._names:
                raise TypeError("enum %r not found" % value)
            self.__dict__["name"] = value
        elif key == "is_data":
            if value:
                self.__dict__["name"] = self._data_name
        else:
            self.__dict__[key] = value

    @property
    def interop_id(self):
        return next((key for key, name in self._interop.items() if name == self.name), "NONE")

    @property
    def is_data(self):
        return self.name == self._data_name


BLENDER = dict(names=["sRGB", "Non-Color", "Linear Rec.709"], interop={"srgb_rec709_scene": "sRGB", "data": "Non-Color"},
               data_name="Non-Color")
ACES = dict(names=["ACEScg", "sRGB - Texture", "Raw"], interop={"srgb_rec709_scene": "sRGB - Texture"},
            data_name="Raw")


class View:
    def __init__(self, views, current):
        self.__dict__.update(_views=views, view_transform=current)
        self.__dict__["bl_rna"] = type("RNA", (), {"properties": {"view_transform": Enum([])}})   # As above.

    def __setattr__(self, key, value):
        if key == "view_transform" and value not in self._views:
            raise TypeError("enum %r not found" % value)
        self.__dict__[key] = value


class ColourSpaceTests(unittest.TestCase):
    def test_stored_values_use_the_configurations_data_role(self):
        for config, expected in ((BLENDER, "Non-Color"), (ACES, "Raw")):
            settings = Settings(**config)
            color_spaces.mark_stored(settings)
            self.assertEqual(settings.name, expected)
            self.assertEqual(color_spaces.kind(settings), "data")

    def test_srgb_by_interop_id_in_any_configuration(self):
        for config, expected in ((BLENDER, "sRGB"), (ACES, "sRGB - Texture")):
            settings = Settings(**config, name="Raw" if config is ACES else "Non-Color")
            color_spaces.mark_srgb(settings)
            self.assertEqual(settings.name, expected)
            self.assertEqual(color_spaces.kind(settings), "srgb")

    def test_srgb_by_display_interop_id_then_common_names_then_the_default_byte_role(self):
        display = Settings(["ACEScg", "sRGB - Display"], interop={"srgb_rec709_display": "sRGB - Display"})
        color_spaces.mark_srgb(display)
        self.assertEqual(display.name, "sRGB - Display")
        named = Settings(["ACEScg", "Utility - sRGB - Texture"])   # An older configuration: no interop IDs.
        color_spaces.mark_srgb(named)
        self.assertEqual(named.name, "Utility - sRGB - Texture")
        bare = Settings(["ACEScg", "Output - Rec.709"])
        color_spaces.mark_srgb(bare, default_byte=lambda: "Output - Rec.709")
        self.assertEqual(bare.name, "Output - Rec.709")

    def test_kinds_for_rules_and_for_older_facts(self):
        self.assertEqual(color_spaces.kind(Settings(**ACES, name="ACEScg")), "other")
        self.assertEqual(color_spaces.fact_kind({"colorspace": "sRGB"}), "srgb")
        self.assertEqual(color_spaces.fact_kind({"colorspace": "Non-Color"}), "data")
        self.assertEqual(color_spaces.fact_kind({"colorspace": "ACEScg"}), "other")
        self.assertEqual(color_spaces.fact_kind({"colorspace": "sRGB - Texture", "colorspace_kind": "srgb"}), "srgb")

    def test_an_untonemapped_view_in_any_configuration(self):
        for views, expected in ((["Standard", "AgX", "Filmic"], "Standard"),
                                (["ACES 2.0 - SDR 100 nits (Rec.709)", "Un-tone-mapped", "Raw"], "Un-tone-mapped"),
                                (["ACES 1.0 - SDR Video", "Raw"], "Raw")):
            view = View(views, views[0] if expected != views[0] else views[-1])
            self.assertTrue(color_spaces.view_untonemapped(view))
            self.assertEqual(view.view_transform, expected)
        lone = View(["ACES 1.0 - SDR Video"], "ACES 1.0 - SDR Video")
        self.assertFalse(color_spaces.view_untonemapped(lone))
        self.assertEqual(lone.view_transform, "ACES 1.0 - SDR Video")


NAMED = {"sRGB", "Non-Color", "Standard", "Raw", "Linear Rec.709", "Filmic", "AgX", "Linear", "Linear Rec.2020",
         "sRGB IEC61966-2.1"}
SETTING = {"view_transform", "display_device"}
EXEMPT = {"color_spaces.py": "the one owner of these names",
          "web_tone.py": "picks its measured table (web_tone_known) only on Blender's own \"sRGB\" display, which the "
                         "table was measured on; WEB_TONE_SHA256 pins the file, and it only reads"}


def violations(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            literal = isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
            for target in targets:
                text = ast.unparse(target)
                if literal and (any(text.endswith("." + name) for name in SETTING)
                                or text.endswith("colorspace_settings.name")):
                    found.append("%d: %s = %r" % (node.lineno, text, node.value.value))
        elif isinstance(node, ast.Compare):
            for side in [node.left, *node.comparators]:
                values = [side] if isinstance(side, ast.Constant) else list(getattr(side, "elts", []))
                if any(isinstance(v, ast.Constant) and v.value in NAMED for v in values):
                    found.append("%d: %s" % (node.lineno, ast.unparse(node)[:80]))
    return found


class SourceTests(unittest.TestCase):
    def test_no_module_writes_or_compares_a_colour_space_view_or_display_by_name(self):
        found = {}
        for path in sorted(ADDON.rglob("*.py")):
            if path.name in EXEMPT:
                continue
            rows = violations(path)
            if rows:
                found[str(path.relative_to(ADDON))] = rows
        self.assertEqual(found, {})


if __name__ == "__main__":
    unittest.main()
