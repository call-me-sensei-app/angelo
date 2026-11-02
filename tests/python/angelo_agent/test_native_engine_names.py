# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A render engine's name is a product name: never translated anywhere in Angelo (doc/angelo/render_engines.rst,
"Engine names"). No Blender.

Every UI locale, through the real catalogues looked up as the app looks them up (Blender's first, then Angelo's): the
switcher's rows as drawn (Director's dropdown and Render > Render Engine), the button's label and the labels its width
is fitted to keep each engine's name exactly as the engine registers it, while the words around it ("Preview",
"Final") are translated. Before this rule, Blender's catalogue gave "Cycles" its F-curve modifier sense ("Döngüler",
"حلقات", "循環"). Also covered: the fallback names match the names Blender registers in C++, Blender's own engine
dropdown is marked untranslated, and the catalogue validator refuses a translation that drops an engine's name.
"""

import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Importing the stand-in modules sets them as attributes of an ``angelo_agent`` package another test file already
# imported; put its attributes back after, or later files' ``from angelo_agent import x`` get the stand-ins.
_PACKAGE = sys.modules.get("angelo_agent")
_ATTRIBUTES = dict(vars(_PACKAGE)) if _PACKAGE is not None else None
from host_test_policy import environment_skip  # noqa: E402
from test_native_render_engines import BPY, LOADED, loaded, render_engines, scene  # noqa: E402
if _PACKAGE is not None:
    for _name in set(vars(_PACKAGE)) - set(_ATTRIBUTES):
        delattr(_PACKAGE, _name)
    for _name, _value in _ATTRIBUTES.items():
        setattr(_PACKAGE, _name, _value)

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/angelo"))
from i18n import languages, po, validate  # noqa: E402

UI_TEXT = LOADED["angelo_agent.ui_text"]
REGISTERED = {"BLENDER_EEVEE": "EEVEE", "CYCLES": "Cycles", "BLENDER_WORKBENCH": "Workbench", "VRAY_RENDER_RT": "V-Ray"}


def catalogue(path):
    """``{(context, msgid): msgstr}`` of one .po file; no context is Blender's default one."""
    if not path.exists() or path.read_bytes().startswith(b"version https://git-lfs"):
        return None
    _header, entries = po.read(str(path))
    return {(entry.ctxt or None, entry.id): entry.str for entry in entries if entry.str and not entry.obsolete}


def translations(locale_po):
    """``bpy.app.translations`` for one locale: Blender's catalogue asked first, then Angelo's, as in the app."""
    blender = catalogue(ROOT / "locale/po" / (locale_po + ".po"))
    angelo = catalogue(ROOT / "locale/angelo/po" / (locale_po + ".po"))
    if blender is None or angelo is None:
        return None

    def lookup(message, context=None):
        for found in (blender, angelo):
            if (context, message) in found:
                return found[(context, message)]
        return message
    return SimpleNamespace(pgettext_iface=lookup, pgettext_tip=lookup, pgettext_rpt=lookup, locale=locale_po)


class EveryLocaleTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(loaded())
        self.enterContext(mock.patch.object(render_engines, "device_line", return_value=None))

    def shown(self, locale_po):
        """Every engine label the switcher draws in ``locale_po``: ``{row value: text}``, plus the button's."""
        found = translations(locale_po)
        if found is None:
            self.skipTest(environment_skip("a catalogue is an LFS pointer in this checkout"))
        with mock.patch.object(BPY.app, "translations", found), mock.patch.dict(UI_TEXT._pgettext, clear=True):
            final = scene("CYCLES", **dict(render_engines.COMMON, **render_engines.PRESETS["FINAL"]))
            drawn = {row["value"]: row["label"] if row.get("translate") is False else UI_TEXT.iface_(row["label"])
                     for row in render_engines.menu_rows(SimpleNamespace(scene=final)) if row["kind"] == "row"}
            return (drawn, render_engines.current_label(final), render_engines.current_label(scene()),
                    render_engines.every_label(), UI_TEXT.iface_(render_engines.PRESET_LABELS["FINAL"], "Angelo"))

    def test_every_locale_shows_each_engine_as_it_registers_itself(self):
        problems = []
        for language in languages.LANGUAGES:
            with self.subTest(language=language.code):
                drawn, cycles_button, eevee_button, widths, final = self.shown(language.po)
                for identifier, name in REGISTERED.items():
                    if identifier == "CYCLES":
                        continue
                    if drawn.get("engine:" + identifier) != name:
                        problems.append((language.code, identifier, drawn.get("engine:" + identifier)))
                for value in ("engine:CYCLES:PREVIEW", "engine:CYCLES:FINAL"):
                    if not re.search(r"(^|\W)Cycles(\W|$)", drawn[value]):
                        problems.append((language.code, value, drawn[value]))
                if not re.search(r"(^|\W)Cycles(\W|$)", cycles_button) or final not in cycles_button:
                    problems.append((language.code, "button", cycles_button))
                if eevee_button != "EEVEE":
                    problems.append((language.code, "button", eevee_button))
                if not {"EEVEE", "Cycles", "Workbench", "V-Ray"} <= set(widths):
                    problems.append((language.code, "widths", widths))
        self.assertEqual(problems, [], "an engine's name is never translated; its preset is")

    def test_the_render_menu_draws_the_same_labels_untranslated(self):
        source = (ROOT / "scripts/addons_core/angelo_agent/render_engines.py").read_text(encoding="utf-8")
        draw = source[source.index("class ANGELO_MT_render_engine"):]
        self.assertIn('row["label"] if row.get("translate") is False else iface_(row["label"])', draw)
        rows = [row for row in render_engines.choices(scene()) if row["value"].startswith("engine:")]
        self.assertTrue(rows and all(row.get("translate") is False for row in rows), "every engine row")


class SourceTests(unittest.TestCase):
    def test_the_fallback_names_are_the_names_blender_registers(self):
        for identifier, path in (("BLENDER_EEVEE", "source/blender/draw/engines/eevee/eevee_engine.cc"),
                                 ("BLENDER_WORKBENCH", "source/blender/draw/engines/workbench/workbench_engine.cc")):
            text = (ROOT / path).read_text(encoding="utf-8")
            self.assertRegex(text, r'/\*idname\*/ "%s",\s*/\*name\*/ N_\("%s"\)' % (
                identifier, re.escape(render_engines.NAMES[identifier])))
        cycles = (ROOT / "intern/cycles/blender/addon/__init__.py").read_text(encoding="utf-8")
        self.assertIn('bl_label = "%s"' % render_engines.NAMES["CYCLES"], cycles)

    def test_blenders_own_engine_dropdown_is_untranslated(self):
        text = (ROOT / "source/blender/makesrna/intern/rna_scene.cc").read_text(encoding="utf-8")
        start = text.index('prop = RNA_def_property(srna, "engine", PROP_ENUM, PROP_NONE);')
        self.assertIn("RNA_def_property_flag(prop, PROP_ENUM_NO_TRANSLATE);", text[start:text.index(";", text.index(
            'RNA_def_property_update(prop, NC_WINDOW, "rna_RenderSettings_engine_update")', start)) + 1])

    def test_the_validator_refuses_a_translation_that_drops_an_engines_name(self):
        language = languages.BY_CODE["tr"]
        for name in ("Cycles", "EEVEE", "Workbench", "V-Ray"):
            english = "%s is not enabled" % name
            kept = validate.check_entry(language, po.Entry(english), po.Entry(english, "%s etkin değil" % name))
            self.assertFalse([issue for issue in kept if "product name" in str(issue)], name)
            dropped = validate.check_entry(language, po.Entry(english), po.Entry(english, "Döngüler etkin değil"))
            self.assertTrue([issue for issue in dropped if "product name %s" % name in str(issue)], name)


if __name__ == "__main__":
    unittest.main()
