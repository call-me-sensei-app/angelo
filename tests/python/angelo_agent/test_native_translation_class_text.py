# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every label, tooltip and property text of the add-on's classes can be translated, and no tooltip is a docstring.

Blender translates this text itself, so nothing marks it and ``test_native_translation`` (which follows the marks)
never looked: on 2026-11-12 about 80 operator tooltips and 10 labels were in no catalogue ("Delete Chat", "Move
this chat to Recently Deleted, where you can restore it"), in Angelo 1.0.11 as on the 1.1 line. The app's own
extraction cannot find a Python class's tooltip either (``i18n/class_text.py``), so the text is read here from the
sources: each must be in ``locale/angelo/angelo.pot`` or be text Blender's own catalogues translate.

Where a class sets no ``bl_description`` Blender registers its docstring as the tooltip. Nine docstrings that were
notes for developers were shown that way, and translated. A registered class now states its tooltip: a real
``bl_description``, or ``""`` for none.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/angelo"))

from i18n import class_text, po, sources  # noqa: E402

WORDS = re.compile(r"[A-Za-z]{2}")   # Text a person reads: an icon, a number or a single letter is not.


def module_paths():
    return sorted(sources.ADDON.glob("*.py"))


class ClassTextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = {entry.id for entry in po.read(str(sources.POT))[1]}
        cls.blender = set()
        for name in ("zh_HANS", "ja"):   # The two most complete Blender catalogues, as the extractor uses.
            cls.blender.update(entry.id for entry in po.read(str(ROOT / "locale/po" / f"{name}.po"))[1])

    def test_every_class_text_is_in_the_template(self):
        missing = []
        for path in module_paths():
            for kind, text, owner, line in class_text.messages(path.read_text(encoding="utf-8")):
                if WORDS.search(text) and text not in self.template and text not in self.blender:
                    missing.append(f"{path.name}:{line} {owner or 'a property'} {kind}: {text!r}")
        self.assertGreater(len(module_paths()), 100)
        self.assertEqual([], missing, "Add each to locale/angelo/angelo.pot and translate it "
                                      "(locale/angelo/README.md); the app's extraction cannot see a class's tooltip")

    def test_no_registered_class_leaves_its_tooltip_to_its_docstring(self):
        relying = []
        for path in module_paths():
            relying += [f"{path.name}:{line} {name}"
                        for name, line in class_text.docstring_tooltips(path.read_text(encoding="utf-8"))]
        self.assertEqual([], relying, "Blender registers a class docstring as its tooltip: give the class a "
                                      "bl_description (\"\" for no tooltip)")


class ReaderTests(unittest.TestCase):
    SOURCE = '''
class ANGELO_OT_sample(Operator):
    """A note for developers."""
    bl_idname = "angelo.sample"
    bl_label = "Sample Label"
    bl_description = ("A tooltip written "
                      "over two lines")
    mode: EnumProperty(name="Mode", description="How it runs", items=[
        ("A", "First Way", "Runs the first way"), ("B", "Second Way", "")])
    count: IntProperty(name=NAME)


class Helper:
    """Not registered: it has no bl_idname or bl_label."""
    bl_description = "Never read"


class ANGELO_MT_plain(Menu):
    """Shown as the menu's tooltip."""
    bl_label = "Plain"


size: FloatProperty(name="Size outside a class")
'''

    def test_it_reads_labels_tooltips_and_property_text_as_literals(self):
        found = {(kind, text, owner) for kind, text, owner, _line in class_text.messages(self.SOURCE)}
        self.assertEqual(found, {
            ("bl_label", "Sample Label", "ANGELO_OT_sample"),
            ("bl_description", "A tooltip written over two lines", "ANGELO_OT_sample"),
            ("property name", "Mode", "ANGELO_OT_sample"), ("property description", "How it runs", "ANGELO_OT_sample"),
            ("enum item name", "First Way", "ANGELO_OT_sample"),
            ("enum item description", "Runs the first way", "ANGELO_OT_sample"),
            ("enum item name", "Second Way", "ANGELO_OT_sample"),
            ("bl_label", "Plain", "ANGELO_MT_plain"), ("property name", "Size outside a class", "")})

    def test_it_names_only_registered_classes_that_rely_on_a_docstring(self):
        self.assertEqual([name for name, _line in class_text.docstring_tooltips(self.SOURCE)], ["ANGELO_MT_plain"])
        self.assertEqual(class_text.docstrings(self.SOURCE), {
            "A note for developers.", "Not registered: it has no bl_idname or bl_label.",
            "Shown as the menu's tooltip."})

    def test_a_docstring_over_several_lines_is_known_as_the_dump_writes_it(self):
        """The dump takes a docstring with its indentation removed (2.1.0: a two-line note for developers reached the
        template, so its every language, because only the indented form was known)."""
        source = 'class ANGELO_OT_two(Operator):\n    """First line for developers.\n    Second line, indented."""\n' \
                 '    bl_idname = "angelo.two"\n    bl_description = "The real tooltip"\n'
        docstrings, tooltips = class_text.docstrings(source), class_text.tooltips({"angelo_agent/two.py": source})
        self.assertTrue(class_text.dump_artifact("First line for developers.\nSecond line, indented.",
                                                 {"bpy.types.ANGELO_OT_two"}, docstrings, tooltips))

    def test_the_extraction_takes_tooltips_from_the_sources_and_drops_a_docstring_the_dump_took(self):
        tooltips = class_text.tooltips({"angelo_agent/sample.py": self.SOURCE})
        self.assertEqual(tooltips, {"A tooltip written over two lines": {"angelo_agent/sample.py"}})
        docstrings = class_text.docstrings(self.SOURCE)

        def dumped(msgid, *origins):
            return class_text.dump_artifact(msgid, set(origins), docstrings, tooltips)

        self.assertTrue(dumped("A note for developers.", "bpy.types.ANGELO_OT_sample"))
        self.assertFalse(dumped("A note for developers.", "bpy.types.ANGELO_OT_sample", "angelo_agent/sample.py"),
                         "the same words marked in a Python source are interface text")
        self.assertFalse(dumped("Sample Label", "bpy.types.ANGELO_OT_sample"))
        self.assertFalse(dumped("A tooltip written over two lines", "bpy.types.ANGELO_OT_sample"))
        self.assertFalse(dumped("A note for developers."), "a message without an origin is not the dump's")
        source = (ROOT / "scripts/angelo/i18n/extract_pot.py").read_text(encoding="utf-8")
        for needed in ("class_text.dump_artifact(msgid, origins, class_docstrings, class_tooltips)",
                       "for msgid, origins in class_tooltips.items():"):
            self.assertIn(needed, source)


if __name__ == "__main__":
    unittest.main()
