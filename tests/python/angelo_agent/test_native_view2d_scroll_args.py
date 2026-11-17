# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""No suite or add-on passes a distance to ``view2d.scroll_*``: Blender replaces it with one wheel step (or a page).

The cut's GUI round on 3fc1f94df29: external_mcp asked ``scroll_down(deltay=6000)`` and then the row's whole
distance, moved 40 pixels a call and gave up with the MCP app row 1583 pixels below the view, on every Mac run.
A distance goes through ``view_scroll`` (``view2d.pan``, which applies it). Blender's own source is read, so this
fails if upstream ever starts honouring the argument and the rule is no longer needed. Runs outside Blender.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
VIEW2D = ROOT / "source/blender/editors/interface/view2d/view2d_ops.cc"
SCROLLS = {"scroll_up", "scroll_down", "scroll_left", "scroll_right"}
DELTAS = {"deltax", "deltay"}
SEARCHED = (ROOT / "tests/python", ROOT / "scripts/addons_core", ROOT / "scripts/startup")


def _names(node):
    return {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}


def ignored_distances(source, relative):
    """Every call to a ``view2d.scroll_*`` operator (a conditional choice of one included) given a delta."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not {k.arg for k in node.keywords} & DELTAS:
            continue
        names = _names(node.func)
        if "view2d" in names and names & SCROLLS:
            found.append("%s:%d" % (relative, node.lineno))
    return found


class View2DScrollArgsTest(unittest.TestCase):
    def test_blender_still_replaces_the_distance(self):
        text = VIEW2D.read_text(encoding="utf-8")
        for name, sign in (("view_scrolldown_exec", "-"), ("view_scrollup_exec", "")):
            with self.subTest(operator=name):
                body = text.split("static wmOperatorStatus %s(" % name, 1)[1].split("\n}\n", 1)[0]
                self.assertRegex(body, r'RNA_int_set\(op->ptr, "deltay", %s40 \* UI_SCALE_FAC\);' % re.escape(sign),
                                 "Blender changed how scroll_up/down move: re-check view_scroll.py and this rule")

    def test_no_caller_passes_a_distance(self):
        found = []
        for base in SEARCHED:
            for path in sorted(base.rglob("*.py")):
                text = path.read_text(encoding="utf-8", errors="replace")
                if "view2d" in text and "delta" in text:
                    found += ignored_distances(text, path.relative_to(ROOT).as_posix())
        self.assertEqual(found, [], "view2d.scroll_* ignore deltax/deltay: scroll a distance with view_scroll")

    def test_the_reader_finds_both_forms(self):
        """The forms the GUI round found: a direct call, and a conditional choice of operator."""
        direct = "bpy.ops.view2d.scroll_down(deltay=6000)\n"
        chosen = "(bpy.ops.view2d.scroll_up if up else bpy.ops.view2d.scroll_down)(deltay=d)\n"
        fine = "bpy.ops.view2d.scroll_down(page=True)\nbpy.ops.view2d.pan(deltax=0, deltay=-40)\n"
        self.assertEqual(ignored_distances(direct, "a"), ["a:1"])
        self.assertEqual(ignored_distances(chosen, "b"), ["b:1"])
        self.assertEqual(ignored_distances(fine, "c"), [])


if __name__ == "__main__":
    unittest.main()
