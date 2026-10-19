# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent.editor_registry import EDITOR_GROUPS, EDITORS, CODING_AGENT, editor_identity


class EditorRegistryTests(unittest.TestCase):
    def test_coding_agent_has_a_separate_ai_column(self):
        groups = dict(EDITOR_GROUPS)
        self.assertEqual([v[0] for v in groups["AI"] if v[0] == CODING_AGENT], [CODING_AGENT])
        self.assertIn(("CONSOLE", "Python Console", "CONSOLE"), groups["Scripting"])
        self.assertNotIn(CODING_AGENT, [v[0] for v in groups["Scripting"]])
        self.assertEqual(list(groups), ["General", "Animation", "Scripting", "AI", "Data"])

    def test_editor_choices_are_unique_and_console_is_not_renamed(self):
        identities = [v[0] for _, entries in EDITOR_GROUPS for v in entries]
        self.assertEqual(len(identities), len(set(identities)))
        self.assertEqual(EDITORS["CONSOLE"][0], "Python Console")
        self.assertEqual(EDITORS[CODING_AGENT][0], "Coding Agent")

    def test_editor_identity_distinguishes_the_agent_from_native_python(self):
        for language, identity in (("python", "CONSOLE"), ("", "CONSOLE"), ("shell", "CONSOLE"), ("angelo", CODING_AGENT)):
            area = SimpleNamespace(type="CONSOLE", spaces=SimpleNamespace(active=SimpleNamespace(language=language)))
            self.assertEqual(editor_identity(area), identity)
        self.assertEqual(editor_identity(SimpleNamespace(type="VIEW_3D", ui_type="VIEW_3D")), "VIEW_3D")

    def test_compiled_editor_enum_has_ai_heading_and_separate_subtype(self):
        source = (ROOT / "source/blender/makesrna/intern/rna_screen.cc").read_text()
        self.assertIn('(SPACE_CONSOLE << 16) | 1', source)
        self.assertIn('"CODING_AGENT"', source)
        self.assertIn('RNA_ENUM_ITEM_HEADING(N_("AI"), nullptr)', source)  # Marked for the catalogues.
        original = (ROOT / "source/blender/makesrna/intern/rna_space.cc").read_text()
        self.assertIn('"Python Console"', original)
        self.assertIn('"is_coding_agent"', original)
        surface = (ROOT / "source/blender/editors/space_console/space_console.cc").read_text()
        self.assertIn('value == 1 ? "angelo" : "python"', surface)
        self.assertIn('st->space_subtype_set = console_space_subtype_set', surface)

    def test_coding_agent_lazy_bootstrap_never_evaluates_python(self):
        source = (ROOT / "scripts/modules/_console_angelo.py").read_text()
        self.assertIn('addon_utils.enable("angelo_agent"', source)
        self.assertIn('return {\'CANCELLED\'}  # Wait for the host', source)
        self.assertNotIn('bpy.ops.console.execute', source)
        self.assertNotIn('_console_python', source)


if __name__ == "__main__":
    unittest.main()
