# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A scripted driver asks for render approval only when the render would run its own Python.

Automatic looks are a latency win, and approval is for Python that would
actually run: not a driver of a data-block the render never evaluates, not an
expression Blender's restricted mode blocks while auto-execution is off, and
not plain arithmetic Blender lets every untrusted file run. Restricted mode is
trusted only while auto-execution has stayed off since the document loaded,
because Blender keeps running an expression compiled while it was on.
"""

import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from animation_fakes import Curve, driver  # noqa: E402
from angelo_agent import render_drivers  # noqa: E402


class ID:
    """A data-block: hashed by identity, as Blender's ID wrappers compare by pointer."""

    def __init__(self, name, uid, drivers=(), tree=None):
        self.name, self.session_uid, self.node_tree = name, uid, tree
        self.animation_data = SimpleNamespace(drivers=list(drivers)) if drivers else None


def python(expression="bpy.context.scene.frame_current", **options):
    return Curve("location", index=2, driver=driver(expression, **options))


class Stand(unittest.TestCase):
    """A stand-in ``bpy``/``_bpy`` with a document, its reference map and the auto-execution flag."""

    auto = True

    def setUp(self):
        self.namespace = {}
        self.data = SimpleNamespace(objects=[], materials=[], scenes=[])
        self.users = {}
        self.data.user_map = lambda: self.users
        bpy = ModuleType("bpy")
        bpy.data, bpy.app = self.data, SimpleNamespace(driver_namespace=self.namespace)
        native = ModuleType("_bpy")
        native._angelo_jobs = SimpleNamespace(scripts_auto_execute=lambda: self.auto)
        self.patches = [patch.dict(sys.modules, {"bpy": bpy, "_bpy": native}),
                        patch.object(render_drivers, "_compiled_in_full", False),
                        patch.object(render_drivers, "_watching", True)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()

    def found(self, scene, shipped=lambda value: False):
        return [label for _identity, label in render_drivers.python_drivers(scene, shipped)]

    def counts(self, expression, shipped=lambda value: False, **options):
        """Whether one driver on the rendered scene itself counts."""
        scene = ID("Main", 1, [python(expression, **options)])
        self.data.scenes[:] = [scene]
        return bool(self.found(scene, shipped))


class ScopeTests(Stand):
    def build(self):
        tree = SimpleNamespace(is_embedded_data=True, animation_data=SimpleNamespace(drivers=[python()]))
        self.main, self.other, self.shot = ID("Main", 1, [python()]), ID("Other", 2), ID("Shot", 3)
        self.cube, self.elsewhere, self.in_shot = ID("Cube", 4, [python()]), ID("Elsewhere", 5, [python()]), ID("Prop", 6, [python()])
        mesh, wood = ID("CubeMesh", 7), ID("Wood", 8, tree=tree)
        self.data.scenes[:] = [self.main, self.other, self.shot]
        self.data.objects[:] = [self.cube, self.elsewhere, self.in_shot]
        self.data.materials[:] = [wood]
        # bpy.data.user_map(): each data-block and the data-blocks that use it.
        self.users.update({self.cube: {self.main}, self.elsewhere: {self.other}, mesh: {self.cube}, wood: {mesh},
                           self.shot: {self.main}, self.in_shot: {self.shot}, self.main: set(), self.other: set()})

    def test_only_drivers_of_data_blocks_the_render_reaches_count(self):
        self.build()
        self.assertEqual(self.found(self.main), ["Cube: location[2]", "Prop: location[2]",
                                                 "Wood (node tree): location[2]", "Main: location[2]"])
        self.assertEqual(self.found(self.other), ["Elsewhere: location[2]"])

    def test_without_a_reference_map_every_data_block_counts(self):
        self.build()
        del self.data.user_map
        self.assertEqual(len(self.found(self.other)), 5)

    def test_no_driver_needs_python_and_no_map_is_read(self):
        self.data.objects[:] = [ID("Cube", 4, [Curve("location", driver=driver("var * 2", simple=True))])]
        self.data.user_map = lambda: self.fail("the reference map is read only for a driver that needs Python")
        self.assertEqual(self.found(ID("Main", 1)), [])


class AutoExecutionOnTests(Stand):
    def test_own_code_counts_and_arithmetic_or_math_does_not(self):
        self.assertTrue(self.counts("bpy.context.scene.frame_current"))
        self.assertTrue(self.counts("self.location.x", use_self=True, simple=True))
        self.assertFalse(self.counts("sin(var) * 2 + round(frame) + clamp(var, 0, 1)"))
        self.assertFalse(self.counts("max(v for v in (var, 1))"), "a comprehension of allowed names")
        self.assertFalse(self.counts("var +"), "Blender cannot compile it either")


class RestrictedTests(Stand):
    auto = False

    def test_blocked_expressions_do_not_count(self):
        self.assertTrue(render_drivers.restricted())
        self.assertFalse(self.counts("bpy.context.scene.frame_current"))
        self.namespace["_secret"] = lambda value: value
        self.assertFalse(self.counts("_secret(var)"), "Blender refuses underscore names")
        self.assertFalse(self.counts("self", use_self=True, simple=True))

    def test_a_function_an_add_on_put_in_the_namespace_still_runs(self):
        def addon_function(value):
            return value
        self.namespace.update(addon_function=addon_function, frame=1.0, bpy=object())
        self.assertTrue(self.counts("addon_function(var)"))
        self.assertFalse(self.counts("addon_function(var)", shipped=lambda value: value is addon_function))
        self.assertFalse(self.counts("sin(var) + frame"))

    def test_an_expression_compiled_while_auto_execution_was_on_keeps_counting(self):
        self.auto = True
        self.assertFalse(render_drivers.restricted())
        self.auto = False  # Switched off: Blender still runs what it compiled meanwhile.
        self.assertFalse(render_drivers.restricted())
        self.assertTrue(self.counts("bpy.context.scene.frame_current"))
        render_drivers.document_loaded()  # A load compiles every driver again, in restricted mode.
        self.assertTrue(render_drivers.restricted())
        self.assertFalse(self.counts("bpy.context.scene.frame_current"))

    def test_an_unfollowed_preference_never_trusts_restricted_mode(self):
        with patch.object(render_drivers, "_watching", False), patch.object(render_drivers, "_compiled_in_full", True):
            render_drivers.document_loaded()
            self.assertFalse(render_drivers.restricted())

    def test_a_binary_without_the_flag_counts_as_auto_execution_on(self):
        with patch.dict(sys.modules, {"_bpy": ModuleType("_bpy")}):
            self.assertTrue(render_drivers.auto_execute())
            self.assertFalse(render_drivers.restricted())


if __name__ == "__main__":
    unittest.main()
