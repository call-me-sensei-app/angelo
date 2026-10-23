# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Suites that call an operator's method on its class get a ``self`` that has the class's methods (``qa_operator``).

A bare ``SimpleNamespace`` as ``self`` breaks the suite the first time the method calls another method of its class
(test_blender_input_capture on 6ceaef365ff: ``ANGELO_OT_studio_input.modal`` gained ``self.moved_out``; fourteen
more calls in ten suites built ``self`` the same way). Every call ``<...>.ANGELO_OT_<name>.<method>(self, ...)`` in
these suites passes a ``qa_operator.stand_in``, never a ``SimpleNamespace`` made in the call or bound to a name.
"""

import ast
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import qa_operator  # noqa: E402


def _bare(node):
    return isinstance(node, ast.Call) and ast.unparse(node.func).split(".")[-1] == "SimpleNamespace"


def bare_selves(source, name="<suite>"):
    """``file:line call`` for each ``ANGELO_OT_*`` method called with a bare SimpleNamespace as its ``self``."""
    tree = ast.parse(source)
    bound = {target.id for node in ast.walk(tree) if isinstance(node, ast.Assign) and _bare(node.value)
             for target in node.targets if isinstance(target, ast.Name)}
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args):
            continue
        owner = node.func.value
        cls = owner.attr if isinstance(owner, ast.Attribute) else getattr(owner, "id", "")
        first = node.args[0]
        if cls.startswith("ANGELO_OT_") and (_bare(first) or isinstance(first, ast.Name) and first.id in bound):
            found.append(f"{name}:{node.lineno} {ast.unparse(node.func)}")
    return found


class SuitesUseStandInsTests(unittest.TestCase):
    def test_no_suite_calls_an_operator_method_with_a_bare_namespace(self):
        sources = {path.name: path.read_text(encoding="utf-8") for path in sorted(HERE.glob("*.py"))
                   if path.name != Path(__file__).name}
        found = [hit for name, text in sources.items() if "ANGELO_OT_" in text and "SimpleNamespace" in text
                 for hit in bare_selves(text, name)]
        self.assertEqual(found, [], "build the self with qa_operator.stand_in(<operator class>, ...)")

    def test_the_check_sees_both_spellings(self):
        source = ("from types import SimpleNamespace\n"
                  "handler = SimpleNamespace(window_pointer=1)\n"
                  "studio.ANGELO_OT_studio_input.modal(handler, None, None)\n"
                  "ANGELO_OT_chat_input.modal(SimpleNamespace(), None, None)\n"
                  "studio.ANGELO_OT_studio_input.modal(qa_operator.stand_in(Cls), None, None)\n")
        self.assertEqual(bare_selves(source), ["<suite>:3 studio.ANGELO_OT_studio_input.modal",
                                               "<suite>:4 ANGELO_OT_chat_input.modal"])


class Blender:
    """Stands for a Blender base (``bpy.types.Operator``): left out of a stand-in."""

    def report(self, *args):
        raise AssertionError("a Blender base's method is never copied")


Blender.__module__ = "bpy_types"


class Mixin:
    BASE = "mixin"

    def helper(self):
        return ("mixin", self.window_pointer)

    def overridden(self):
        return "mixin"


class Operator(Mixin, Blender):
    dragging = False

    def modal(self, context, event):
        return self.helper(), self.overridden(), self.label, self.dragging

    def overridden(self):
        return "operator"

    @property
    def label(self):
        return f"window {self.window_pointer}"

    @staticmethod
    def kind():
        return "static"


class StandInTests(unittest.TestCase):
    def test_methods_bases_properties_and_defaults_work_as_on_an_instance(self):
        handler = qa_operator.stand_in(Operator, window_pointer=7)
        self.assertEqual(Operator.modal(handler, None, None), (("mixin", 7), "operator", "window 7", False))
        self.assertEqual((handler.kind(), handler.BASE), ("static", "mixin"))

    def test_given_state_wins_and_stays_writable(self):
        handler = qa_operator.stand_in(Operator, window_pointer=1, dragging=True)
        self.assertTrue(handler.dragging)
        handler.focus_area = 3
        self.assertEqual(handler.focus_area, 3)
        self.assertFalse(Operator.dragging, "the operator class itself is untouched")

    def test_blender_bases_are_left_out(self):
        self.assertFalse(hasattr(qa_operator.stand_in(Operator, window_pointer=1), "report"))


if __name__ == "__main__":
    unittest.main()
