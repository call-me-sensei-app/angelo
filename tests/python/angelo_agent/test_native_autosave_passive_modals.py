# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Auto-save ignores passive modal operators, and every Angelo canvas and overlay is one, without Blender.

Evidence: up to 1.0.5 Angelo never auto-saved while the chat was open. ``wm_autosave_timer`` defers while any window
has a modal operator and retries every 10 ms, and ``angelo.chat_input`` runs in every window that shows the chat.
The class fix is a native operator option, ``OPTYPE_MODAL_PASSIVE`` (``MODAL_PASSIVE`` in ``bl_options``), that
auto-save ignores, carried by exactly the operators ``input_policy`` already classifies as canvases and overlays.
A real tool (any other modal, and Angelo's holding tools) still defers auto-save as in stock Blender.
``test_blender_autosave_passive_modals`` proves it in the running app.
"""

import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import input_policy  # noqa: E402

NATIVE = frozenset({input_policy.PASSIVE_MODAL_OPTION})


def _source(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


class NativeOptionTests(unittest.TestCase):

    def test_the_option_is_defined_and_registered(self):
        self.assertRegex(_source("source/blender/windowmanager/WM_types.hh"),
                         r"OPTYPE_MODAL_PASSIVE = \(1 << 14\),")
        self.assertRegex(_source("source/blender/makesrna/intern/rna_wm.cc"),
                         r'\{OPTYPE_MODAL_PASSIVE,\s*"MODAL_PASSIVE"')

    def test_auto_save_skips_only_passive_modals(self):
        text = _source("source/blender/windowmanager/intern/wm_files.cc")
        body = text[text.index("void wm_autosave_timer("):]
        body = body[:body.index("\n}\n")]
        self.assertIn("handler->op && !(handler->op->type->flag & OPTYPE_MODAL_PASSIVE)", body)
        self.assertIn("wm_autosave_timer_begin_ex(wm, 0.01);", body, "a real modal still defers, retrying in 10 ms")
        self.assertEqual(len(re.findall(r"OPTYPE_MODAL_PASSIVE", text)), 2, "one comment, one check: nothing else")

    def test_every_operator_type_flag_is_its_own_bit_and_fits_the_field(self):
        """``wmOperatorType::flag`` is a ``short``: a flag upstream adds (a Blender 5.3 merge) must not reuse a bit."""
        text = _source("source/blender/windowmanager/WM_types.hh")
        self.assertIn("short flag = 0;", text[text.index("struct wmOperatorType"):], "the field's width changed")
        block = text[text.index("/** #wmOperatorType.flag */"):]
        block = block[:block.index("};")]
        bits = [int(b) for b in re.findall(r"OPTYPE_\w+ = \(1 << (\d+)\)", block)]
        self.assertGreaterEqual(len(bits), 15)
        self.assertEqual(len(bits), len(set(bits)), "two OPTYPE flags share a bit")
        self.assertLess(max(bits), 15, "bit 15 is a short's sign bit")


class ClassificationTests(unittest.TestCase):

    def test_canvases_and_overlays_are_passive_and_holding_tools_are_not(self):
        for identity in sorted(input_policy.CANVAS_INPUTS | input_policy.PASSIVE_OVERLAYS):
            with self.subTest(identity=identity):
                self.assertEqual(input_policy.modal_options(identity, NATIVE), {"MODAL_PASSIVE"})
        for identity in sorted(input_policy.HOLDING_TOOLS | {"transform.translate", "some.addon_modal"}):
            with self.subTest(identity=identity):
                self.assertEqual(input_policy.modal_options(identity, NATIVE), set())

    def test_an_older_build_registers_without_the_option(self):
        self.assertEqual(input_policy.modal_options("angelo.chat_input", frozenset()), set(),
                         "an add-on overlaid on the 1.0.5 app must not name an option its Blender lacks")
        self.assertEqual(input_policy.modal_options("angelo.chat_input"), set(), "no bpy here: no native option")

    def test_every_angelo_modal_operator_takes_its_options_from_its_classification(self):
        found = {}
        for path in sorted(ADDON.glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not (isinstance(node, ast.ClassDef) and any(isinstance(b, ast.FunctionDef) and b.name == "modal"
                                                               for b in node.body)):
                    continue
                values = {t.id: item.value for item in node.body if isinstance(item, ast.Assign)
                          for t in item.targets if isinstance(t, ast.Name)}
                if "bl_idname" in values:
                    found[values["bl_idname"].value] = (path.name, values.get("bl_options"))
        passive = input_policy.CANVAS_INPUTS | input_policy.PASSIVE_OVERLAYS
        self.assertTrue(passive <= set(found), "every passive operator is an Angelo modal operator")
        for identity, (module, options) in sorted(found.items()):
            calls = [n for n in ast.walk(options) if isinstance(n, ast.Call)] if options is not None else []
            named = [n for n in calls if "modal_options" in (getattr(n.func, "id", ""), getattr(n.func, "attr", ""))]
            uses = any([getattr(a, "id", "") for a in n.args] == ["bl_idname"] for n in named)
            with self.subTest(operator=identity, module=module):
                if identity in passive:
                    self.assertTrue(uses, "bl_options must include modal_options(bl_idname)")
                if identity in input_policy.HOLDING_TOOLS:
                    self.assertFalse(named, "a holding tool keeps auto-save waiting: no modal_options")
                if options is not None:
                    self.assertNotIn("MODAL_PASSIVE", ast.dump(options),
                                     "never name the option by hand: modal_options follows the classification")


if __name__ == "__main__":
    unittest.main()
