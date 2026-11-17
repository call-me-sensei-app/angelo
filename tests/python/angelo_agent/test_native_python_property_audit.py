# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The rule the admitted-settings audits use to find the Python a property definition runs."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from python_property_audit import python_callbacks  # noqa: E402


def definition(**keywords):
    """A stand-in for a deferred bpy.props property: only ``keywords`` is read."""
    return SimpleNamespace(keywords=keywords)


def callback(*args):
    return None


class Settings:
    """A class, as a pointer's ``type`` is."""


class PythonCallbacksTest(unittest.TestCase):
    def test_a_plain_setting_runs_no_python(self):
        self.assertEqual(python_callbacks(definition(name="Samples", description="", default=4, min=1, max=64,
                                                     options={"ANIMATABLE"}, subtype="NONE")), [])

    def test_transform_callbacks_count(self):
        # Blender 5.3's deprecated Cycles preview_pause has only set_transform; an audit that names update, get
        # and set saw no Python in it.
        self.assertEqual(python_callbacks(definition(name="Pause Preview", set_transform=callback)), ["set_transform"])
        self.assertEqual(python_callbacks(definition(get_transform=callback, default=0.0)), ["get_transform"])

    def test_every_named_callback_counts(self):
        keywords = {name: callback for name in ("update", "get", "set", "poll", "items")}
        self.assertEqual(python_callbacks(definition(**keywords)), sorted(keywords))

    def test_a_callback_of_a_later_blender_counts_by_its_value(self):
        self.assertEqual(python_callbacks(definition(on_future_event=callback)), ["on_future_event"])

    def test_constant_enum_items_and_a_pointer_type_are_not_callbacks(self):
        self.assertEqual(python_callbacks(definition(items=(("A", "A", ""), ("B", "B", "")), default="A")), [])
        self.assertEqual(python_callbacks(definition(type=Settings)), [])
        self.assertEqual(python_callbacks(definition(type=Settings, poll=callback)), ["poll"])

    def test_both_admission_audits_use_this_rule(self):
        # The Cycles and game-physics audits find callbacks with python_callbacks, not by naming keywords.
        for name in ("test_blender_render_setup.py", "test_blender_game_physics.py"):
            tree = ast.parse((HERE / name).read_text(encoding="utf-8"))
            imports = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                       and node.module == "python_property_audit" for alias in node.names}
            self.assertIn("python_callbacks", imports, name)
            named = [node.args[0].value for node in ast.walk(tree)
                     if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
                     and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "keywords"
                     and node.args and isinstance(node.args[0], ast.Constant)
                     and node.args[0].value in {"update", "get", "set", "poll", "items"}]
            self.assertEqual(named, [], name + " names callback keywords instead of using python_callbacks")


if __name__ == "__main__":
    unittest.main()
