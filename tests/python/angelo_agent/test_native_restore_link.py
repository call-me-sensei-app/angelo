# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chat's "Restore snapshot" and "Undo restore" links fit their translated text.

1.0.8 dogfood: "Restore snapshot" was drawn at a fixed ``width - 126`` with a fixed 126 px hit, a place that fitted the
English label only. In Japanese the label ran to 7 px from the chat's edge (English: 37 px), and the longer German
"Snapshot wiederherstellen" ran past it, clipped; the click target did not follow the text either. The link is now
right-aligned under the user's bubble by its own measured width, and both links' hits cover what is drawn.
"""

import ast
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
SOURCE = (ROOT / "scripts/addons_core/angelo_agent/chat_view.py").read_text(encoding="utf-8")


class RestoreLinkGeometry(unittest.TestCase):
    """``chat_view.restore_link`` imported with stub Blender modules for this class only (the host suite is one
    process: ``patch.dict`` drops the modules imported meanwhile, and the package's attributes for them go too)."""

    @classmethod
    def setUpClass(cls):
        stubs = {name: mock.MagicMock(name=name) for name in (
            "bpy", "bpy.props", "bpy.types", "bpy.app", "bpy.app.handlers", "blf", "gpu", "gpu_extras",
            "gpu_extras.batch", "mathutils", "bpy_extras", "bpy_extras.io_utils", "bl_math")}
        cls._before = set(sys.modules)
        cls._modules = mock.patch.dict(sys.modules, stubs)
        cls._modules.start()
        from angelo_agent import chat_view
        cls.view = chat_view

    @classmethod
    def tearDownClass(cls):
        added = [name for name in sys.modules if name not in cls._before and name.startswith("angelo_agent.")]
        cls._modules.stop()
        package = sys.modules.get("angelo_agent")
        for name in added:
            if package is not None and name.count(".") == 1:
                package.__dict__.pop(name.split(".", 1)[1], None)

    def test_the_label_ends_under_the_bubble_whatever_its_length(self):
        for chat_width in (360, 640, 990):
            for label_width in (60, 90, 140, 220):   # English to the longest catalogue's label at 11 px.
                with self.subTest(chat_width=chat_width, label_width=label_width):
                    x, (hit_x, hit_width) = self.view.restore_link(chat_width, label_width)
                    self.assertEqual(x + label_width, chat_width - 16, "Right edge with the user bubble's (16 px)")
                    self.assertLessEqual(hit_x, x)
                    self.assertGreaterEqual(hit_x + hit_width, x + label_width, "The hit covers the whole label")
                    self.assertLessEqual(hit_x + hit_width, chat_width, "The hit stays inside the chat")


class DrawnWithTheMeasuredWidth(unittest.TestCase):
    def test_no_fixed_offset_places_or_sizes_either_link(self):
        tree = ast.parse(SOURCE)
        draws = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "text"
                 and node.args and isinstance(node.args[0], ast.Call)
                 and getattr(node.args[0].func, "id", "") == "iface_"]
        labels = {node.args[0].args[0].value for node in draws if isinstance(node.args[0].args[0], ast.Constant)}
        self.assertFalse(labels & {"Restore snapshot", "Undo restore"},
                         "Each link's label is measured first, then drawn from that variable")
        self.assertNotIn("width - 126", SOURCE)
        self.assertNotIn("(32, bottom, 116, 24)", SOURCE)
        self.assertIn('restore_link(width, measure(label, 11))', SOURCE)
        self.assertIn("measure(label, 11) + 16", SOURCE, "Undo restore's hit follows its label")


if __name__ == "__main__":
    unittest.main()
