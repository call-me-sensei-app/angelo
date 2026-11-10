# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual RNA array getter must not run while staging checks a driver's path.

Source fixture only until executed by the native suite runner. This exercises real
RNA resolution: coerce=False still reads an indexed array before returning it.
"""
import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

from pathlib import Path
import json
import os
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import stage_paths  # noqa: E402
from angelo_agent.program_jobs import ProgramError  # noqa: E402


class StagePathGetterTests(unittest.TestCase):
    def setUp(self):
        self.calls = 0
        self.block = bpy.data.objects.new("stage_path_getter_fixture", None)

        def getter(_object):
            self.calls += 1
            return (1.0, 2.0, 3.0)

        self.property_name = "angelo_stage_path_getter_fixture"
        setattr(bpy.types.Object, self.property_name,
                bpy.props.FloatVectorProperty(size=3, get=getter))

    def tearDown(self):
        delattr(bpy.types.Object, self.property_name)
        bpy.data.objects.remove(self.block)

    def test_actual_index_resolution_is_a_meaningful_getter_trap(self):
        self.block.path_resolve(self.property_name + "[0]", False)
        self.assertGreater(self.calls, 0)

    def test_check_refuses_before_any_actual_array_getter(self):
        path = self.property_name + "[0]"
        router = SimpleNamespace(owned={self.block.as_pointer()}, allowed=set())
        with patch.object(stage_paths, "_animated", return_value=[self.block]), \
                patch.object(stage_paths, "_paths", return_value=[(self.block, path, False, None)]):
            with self.assertRaises(ProgramError) as caught:
                stage_paths.check(SimpleNamespace(), router, [self.block])
        self.assertEqual(caught.exception.code, "UNSUPPORTED")
        self.assertEqual(self.calls, 0)

    def test_missing_later_member_does_not_run_earlier_getter(self):
        path = self.property_name + "[0].missing"
        router = SimpleNamespace(owned={self.block.as_pointer()}, allowed=set())
        with patch.object(stage_paths, "_animated", return_value=[self.block]), \
                patch.object(stage_paths, "_paths", return_value=[(self.block, path, False, None)]):
            with self.assertRaises(ProgramError):
                stage_paths.check(SimpleNamespace(), router, [self.block])
        self.assertEqual(self.calls, 0)


class StageDeclaredPropertyCallbackTests(unittest.TestCase):
    def setUp(self):
        self.calls = 0
        self.classes = []
        self.block = bpy.data.objects.new("stage_declared_callback_fixture", None)
        self.property_name = "angelo_stage_declared_callback_fixture"

    def tearDown(self):
        if hasattr(bpy.types.Object, self.property_name):
            delattr(bpy.types.Object, self.property_name)
        bpy.data.objects.remove(self.block)
        for cls in reversed(self.classes):
            bpy.utils.unregister_class(cls)

    def group(self, name, annotations):
        cls = type(name, (bpy.types.PropertyGroup,), {"__annotations__": annotations})
        bpy.utils.register_class(cls)
        self.classes.append(cls)
        return cls

    def holder(self, cls):
        setattr(bpy.types.Object, self.property_name, bpy.props.PointerProperty(type=cls))
        return getattr(self.block, self.property_name)

    def refuse(self, start, path, write=False):
        router = SimpleNamespace(owned={self.block.as_pointer()}, allowed=set())
        with patch.object(stage_paths, "_animated", return_value=[self.block]), \
                patch.object(stage_paths, "_paths", return_value=[(start, path, write, None)]):
            with self.assertRaises(ProgramError) as caught:
                stage_paths.check(SimpleNamespace(), router, [self.block])
        self.assertEqual(caught.exception.code, "UNSUPPORTED")
        self.assertEqual(self.calls, 0)

    def test_declared_get_transform_array_refuses_before_actual_getter(self):
        def transform(_self, values, _is_set):
            self.calls += 1
            return values
        cls = self.group("AngeloStageGetterTransformFixture", {
            "vector": bpy.props.FloatVectorProperty(size=3, get_transform=transform)})
        holder = self.holder(cls)
        holder.path_resolve("vector[0]", False)
        self.assertGreater(self.calls, 0)  # Genuine native callback control.
        self.calls = 0
        self.refuse(holder, "vector[0]")

    def test_declared_set_transform_destination_is_not_treated_as_stored(self):
        def transform(_self, values, _current_values, _is_set):
            self.calls += 1
            return values
        cls = self.group("AngeloStageSetterTransformFixture", {
            "vector": bpy.props.FloatVectorProperty(size=3, set_transform=transform)})
        holder = self.holder(cls)
        holder.vector = (4.0, 5.0, 6.0)
        self.assertGreater(self.calls, 0)  # Prove the actual native transform signature runs.
        self.calls = 0
        self.assertTrue(stage_paths.python_callbacks(holder.bl_rna, "vector"))
        self.refuse(holder, "vector[0]", write=True)

    def test_string_lookup_refuses_before_implicit_item_name_getter(self):
        def name(_self):
            self.calls += 1
            return "key"
        item = self.group("AngeloStageCollectionItemFixture", {
            "name": bpy.props.StringProperty(get=name), "value": bpy.props.FloatProperty()})
        container = self.group("AngeloStageCollectionContainerFixture", {
            "items": bpy.props.CollectionProperty(type=item)})
        holder = self.holder(container)
        holder.items.add()
        holder.path_resolve('items["key"].value', False)
        self.assertGreater(self.calls, 0)  # Native lookup consults RNA's nameproperty.
        self.calls = 0
        self.refuse(holder, 'items["key"].value')


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    counts = {
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skips": len(result.skipped),
        "expected_failures": len(result.expectedFailures),
        "unexpected_successes": len(result.unexpectedSuccesses),
    }
    complete = (counts["tests_run"] == 6 and all(value == 0 for key, value in counts.items()
                                                if key != "tests_run"))
    success = result.wasSuccessful() and complete
    output = Path(os.environ["ANGELO_QA_DIR"])
    output.mkdir(parents=True, exist_ok=True)
    (output / "results.json").write_text(json.dumps({
        "checks": {"all_six_actual_rna_callback_guards": success},
        "unittest": counts,
        "was_successful": result.wasSuccessful(),
        "success": success,
    }, indent=2) + "\n")
    if not success:
        raise SystemExit(1)
