# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A program card's review rows are prepared and looked up at one geometry (``approval_review.canvas_geometry``).

The canvas timer prepares them for the chat's width in unscaled pixels (13eff464199); ``review_rows`` returns
nothing for any other width. Three GUI suites asked at ``region.width - 64``, which matches only at UI scale 1, so on
a scaled display their bounded review "never finished" (test_blender_complete_review_ui on the Linux Xvfb run of
9d7a54eab7f: Details complete, 35 rows, yet no rows at the suite's width for 180 s).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
import angelo_agent  # noqa: E402
from angelo_agent import approval_review  # noqa: E402


def constant(path, name):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return next(node.value.value for node in tree.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == name for target in node.targets))


class CanvasGeometryTests(unittest.TestCase):
    def geometry(self, scale, width, font_size):
        fake = SimpleNamespace(ui_scale=lambda: scale)
        area = SimpleNamespace(spaces=SimpleNamespace(active=SimpleNamespace(font_size=font_size)))
        with patch.object(angelo_agent, "chat_view", fake, create=True), \
                patch.dict(sys.modules, {"angelo_agent.chat_view": fake}):
            return approval_review.canvas_geometry(area, SimpleNamespace(width=width))

    def test_the_width_is_the_chats_unscaled_width_less_the_cards_inset_at_every_scale(self):
        inset = constant(ADDON / "chat_card_draw.py", "REVIEW_INSET")
        self.assertEqual(approval_review.CANVAS_INSET, inset, "The draw's lookup and the timer use one inset")
        for scale in (0.5, 1.0, 1.25, 2.0, 4.0):
            for width in (80, 340, 841, 2560):
                for font_size in (10, 15, 24):
                    got = self.geometry(scale, width, font_size)
                    # chat_view.draw: width = region.width / scale; size as below; chat_card_draw: width - REVIEW_INSET.
                    self.assertEqual(got, (max(40, width / scale - inset), max(12, int(font_size * 0.8))),
                                     (scale, width, font_size))

    def test_the_timer_prepares_at_this_geometry(self):
        tree = ast.parse((ADDON / "approval_review.py").read_text(encoding="utf-8"))
        prepare = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                       and node.name == "prepare_canvas_reviews")
        calls = {node.func.id for node in ast.walk(prepare) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name)}
        self.assertIn("canvas_geometry", calls)

    def test_no_suite_asks_for_review_rows_at_a_width_of_its_own(self):
        """Every suite that looks a program card's rows up takes the geometry from the product, never its own sum."""
        askers = []
        for path in sorted(Path(__file__).resolve().parent.glob("test_blender_*.py")):
            source = path.read_text(encoding="utf-8")
            if not any(isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "review_rows"
                       for node in ast.walk(ast.parse(source))):
                continue
            askers.append(path.name)
            self.assertIn("canvas_geometry(", source, path.name)
            self.assertNotRegex(source, r"\.width\s*-\s*64\b", path.name)
        self.assertGreaterEqual(len(askers), 3, askers)

if __name__ == "__main__":
    unittest.main()
