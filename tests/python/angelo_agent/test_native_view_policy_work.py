# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The policy check before every viewport capture does no work its answer does not need.

Before each capture or strip, ``view_policy.admit_display`` asks which add-on Python the look's own display writes
would run: ``view_looks.display_keys`` names the settings, ``render_extensions.subscribers`` hands them to the
native message-bus listing. The settings a look writes are fixed per look, yet each check built them again (four
set comprehensions and a union) and sorted them with a key function, about half of the check's instructions
(benchmark_blender_view_scene_check, ``view_policy/<scene>/display``; 1.0.8 final cut c3e6055bb8c). The listing's
answer comes in message-bus order whatever order the keys come in, so the sort bought nothing. The same check
rebuilt two dictionaries and a set per call to read a look's shading type and overlay switch
(``view_policy.display_dirties``) and imported inside both functions; a look's keys now reach the listing as one
stored tuple, which it reads without building a list (it was handed a freshly sorted list).
"""

import ast
import inspect
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import render_extensions, view_looks, view_policy  # noqa: E402


class DisplayKeysTests(unittest.TestCase):
    def test_a_looks_settings_are_worked_out_once_and_are_its_writes(self):
        for shading in view_looks.SHADING:
            with self.subTest(shading=shading):
                keys = view_looks.display_keys(shading)
                self.assertIsInstance(keys, frozenset)   # Shared between checks: nobody can change it.
                self.assertIs(view_looks.display_keys(shading), keys)
                self.assertEqual(keys, {("View3DShading", name) for name, _value in view_looks.SHADING[shading]}
                                 | {("View3DOverlay", name) for name, _value in view_looks.OVERLAY.get(
                                     shading, (("show_overlays", False),))}
                                 | {("View3DUVChecker", name) for name, _value in view_looks.UV_CHECKER.get(
                                     shading, view_looks.UV_CHECKER_OFF)}
                                 | {("RegionView3D", "use_clip_planes")})


class DrawnWithTests(unittest.TestCase):
    def test_a_looks_type_and_overlays_are_worked_out_once(self):
        for shading in view_looks.SHADING:
            with self.subTest(shading=shading):
                found = view_looks.drawn_with(shading)
                self.assertIs(view_looks.drawn_with(shading), found)
                self.assertEqual(found, (dict(view_looks.SHADING[shading])["type"], dict(view_looks.OVERLAY.get(
                    shading, (("show_overlays", False),)))["show_overlays"]))

    def test_the_policy_check_reads_them_without_building_anything_per_check(self):
        tree = ast.parse(inspect.getsource(view_policy.display_dirties))   # Its code, never its comments.
        calls = {node.func.id for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        self.assertIn("drawn_with", calls)
        self.assertFalse(calls & {"dict", "set", "frozenset", "sorted"})
        self.assertFalse([node for node in ast.walk(tree) if isinstance(node, (
            ast.Import, ast.ImportFrom, ast.Set, ast.SetComp, ast.Dict, ast.DictComp))])


class ListingTests(unittest.TestCase):
    def setUp(self):
        self.asked = []
        native = ModuleType("_bpy")
        native._angelo_jobs = SimpleNamespace(msgbus_subscribers=lambda keys: self.asked.append(keys) or [])
        modules = patch.dict(sys.modules, {"_bpy": native})
        modules.start()
        self.addCleanup(modules.stop)

    def test_a_looks_settings_reach_the_listing_as_one_stored_tuple(self):
        keys = view_looks.display_keys("SOLID")
        self.assertEqual(render_extensions.subscribers(keys), [])
        self.assertEqual(render_extensions.subscribers(keys), [])
        self.assertIsInstance(self.asked[0], tuple)   # The listing reads a tuple without copying it.
        self.assertEqual(set(self.asked[0]), keys)
        self.assertIs(self.asked[1], self.asked[0])   # Neither sorted nor copied again on the next check.

    def test_other_written_settings_go_as_they_come(self):
        written = {("Scene", "camera")}   # A strip's put-back keys (motion_guards): built per strip, a set.
        render_extensions.subscribers(written)
        self.assertIs(self.asked[0], written)

    def test_without_the_listing_subscribers_are_unknown(self):
        del sys.modules["_bpy"]._angelo_jobs.msgbus_subscribers
        self.assertIsNone(render_extensions.subscribers(view_looks.display_keys("SOLID")))
        with patch.dict(sys.modules):
            del sys.modules["_bpy"]   # Outside Blender.
            self.assertIsNone(render_extensions.subscribers(view_looks.display_keys("SOLID")))


if __name__ == "__main__":
    unittest.main()
