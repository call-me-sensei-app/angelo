# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Animation edit kinds: schemas, derived key/shape-key/NLA checks and their predicates.

Dependency-free; Actions are small channelbag stand-ins. Real Actions, shape
keys, NLA, commit, rollback and Undo are covered by test_blender_animation_edits.py.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.action_catalog import resource_dependencies, validate_operation  # noqa: E402
from angelo_agent.derived_checks import derive_checks  # noqa: E402
from angelo_agent.plan_schema import DEFERRED_ACTIONS, DEFERRED_CHECKS  # noqa: E402
from angelo_agent.validation_animation import evaluate_animation  # noqa: E402
from angelo_agent.validation_reader import Inconclusive  # noqa: E402
from angelo_agent.validation_spec import validate_check  # noqa: E402

OBJ = "scn:" + "a" * 32 + ":7"
KINDS = ("keyframe_delete", "keyframe_configure", "shape_key_add", "shape_key_value", "nla_push_down")


def op(kind, **parameters):
    return {"kind": kind, "target": OBJ, "parameters": parameters}


def point(frame, interpolation="BEZIER", easing="AUTO", handle="AUTO_CLAMPED"):
    return SimpleNamespace(co=SimpleNamespace(x=float(frame)), interpolation=interpolation, easing=easing,
                           handle_left_type=handle, handle_right_type=handle)


class Curves:
    def __init__(self, curves):
        self.curves = curves

    def find(self, path, index=0):
        return self.curves.get((path, index))


def animated(curves, nla=0, action=True):
    bag = SimpleNamespace(fcurves=Curves(curves))
    strip = SimpleNamespace(channelbag=lambda slot: bag)
    animation = SimpleNamespace(action=SimpleNamespace(layers=[SimpleNamespace(strips=[strip])]) if action else None,
                                action_slot=object(), nla_tracks=[object()] * nla)
    return SimpleNamespace(animation_data=animation)


READER = SimpleNamespace(spend=lambda amount: None)


class SchemaTests(unittest.TestCase):
    def test_kinds_are_deferred_with_their_checks(self):
        for kind in KINDS:
            self.assertIn(kind, DEFERRED_ACTIONS)
        for check in ("keyframes", "shape_key", "nla"):
            self.assertIn(check, DEFERRED_CHECKS)
        self.assertEqual(resource_dependencies(op("nla_push_down"))["mutates"], ["object", "animation", "nla"])
        self.assertEqual(resource_dependencies(op("shape_key_add", name="Smile"))["mutates"], ["object", "mesh"])

    def test_refusals(self):
        for operation in (op("keyframe_delete", property="location"), op("keyframe_delete", property="color", frame=1),
                          op("keyframe_configure", property="location", frames=[1]),
                          op("keyframe_configure", property="location", frames=[1, 1], interpolation="LINEAR"),
                          op("keyframe_configure", property="location", frames=[1], interpolation="SMOOTH"),
                          op("shape_key_add", name="Smile", slider_min=1, slider_max=0),
                          op("shape_key_add", name="Smile", value=2), op("shape_key_value", name="Smile"),
                          op("nla_push_down", track="")):
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                validate_operation(operation)


class DerivedCheckTests(unittest.TestCase):
    def derived(self, *operations):
        return derive_checks([validate_operation(operation) for operation in operations])

    def test_key_checks_merge_deletes_and_configures(self):
        checks = self.derived(op("keyframe_configure", property="location", frames=[1, 10, 20], interpolation="LINEAR"),
                              op("keyframe_delete", property="location", frame=20))
        self.assertEqual(checks, [{"kind": "keyframes", "target": OBJ, "property": "location", "index": -1,
                                   "frames": [1, 10], "interpolation": "LINEAR", "absent": [20]}])
        self.assertEqual(validate_check(checks[0]), checks[0])
        later_key = self.derived(op("keyframe_delete", property="location", frame=20),
                                 {"kind": "keyframe_insert", "target": OBJ, "parameters": {"property": "location",
                                                                                          "value": [0, 0, 0], "frame": 20}})
        self.assertEqual(later_key, [])

    def test_shape_key_and_nla_checks(self):
        self.assertEqual(self.derived(op("shape_key_add", name="Smile", value=.3), op("shape_key_value", name="Smile", value=.6)),
                         [{"kind": "shape_key", "target": OBJ, "name": "Smile", "exists": True, "value": .6}])
        keyed = self.derived(op("shape_key_value", name="Smile", value=.6, frame=4))
        self.assertNotIn("value", keyed[0])
        self.assertEqual(self.derived(op("nla_push_down")),
                         [{"kind": "nla", "target": OBJ, "minimum_tracks": 1, "active_action": False}])


class PredicateTests(unittest.TestCase):
    def test_keyframes(self):
        obj = animated({("location", 0): SimpleNamespace(keyframe_points=[point(1, "LINEAR"), point(10, "LINEAR")])})
        check = validate_check({"kind": "keyframes", "target": OBJ, "property": "location", "frames": [1, 10],
                                "absent": [20], "interpolation": "LINEAR"})
        self.assertTrue(evaluate_animation(READER, obj, check)[0])
        wrong = dict(check, interpolation="CONSTANT")
        passed, observed = evaluate_animation(READER, obj, wrong)
        self.assertFalse(passed)
        self.assertEqual(observed["mismatched"][0], [1, "interpolation"])
        self.assertFalse(evaluate_animation(READER, obj, dict(check, absent=[10], frames=[1]))[0])

    def test_shape_key_and_nla(self):
        blocks = {"Smile": SimpleNamespace(name="Smile", value=.6)}
        key = SimpleNamespace(key_blocks=SimpleNamespace(get=blocks.get), animation_data=None)
        mesh_object = SimpleNamespace(data=SimpleNamespace(shape_keys=key))
        self.assertTrue(evaluate_animation(READER, mesh_object, validate_check(
            {"kind": "shape_key", "target": OBJ, "name": "Smile", "value": .6}))[0])
        self.assertTrue(evaluate_animation(READER, mesh_object, validate_check(
            {"kind": "shape_key", "target": OBJ, "name": "Frown", "exists": False}))[0])
        animated_key = animated({('key_blocks["Smile"].value', 0): SimpleNamespace(keyframe_points=[point(1)])})
        key.animation_data = animated_key.animation_data
        with self.assertRaises(Inconclusive):
            evaluate_animation(READER, mesh_object, validate_check({"kind": "shape_key", "target": OBJ, "name": "Smile",
                                                                    "value": .6}))
        pushed = animated({}, nla=1, action=False)
        self.assertTrue(evaluate_animation(READER, pushed, validate_check(
            {"kind": "nla", "target": OBJ, "minimum_tracks": 1, "active_action": False}))[0])


class PropertyKeyframeTests(unittest.TestCase):
    def test_schema_families_and_refusals(self):
        self.assertIn("property_keyframe", DEFERRED_ACTIONS)
        keyed = validate_operation(op("property_keyframe", values={"pass_index": 2, "hide_render": True}, frame=4))
        self.assertEqual(list(keyed["parameters"]["values"]), ["hide_render", "pass_index"])  # Allow-list order.
        self.assertEqual(resource_dependencies(keyed)["mutates"], ["object", "properties", "animation"])
        data = op("property_keyframe", values={"data.dof.focus_distance": 3.0}, frame=1)
        self.assertEqual(resource_dependencies(data)["mutates"], ["object", "animation", "data"])
        for values in ({"display_type": "WIRE"}, {"render.fps": 30}, {"data.body": "Hi"}, {"location": [0, 0, 0]},
                       {"active_material.roughness": .5}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_operation(op("property_keyframe", values=values, frame=1))

    def test_derived_keys_merge_and_drop_stale_values(self):
        derived = derive_checks([validate_operation(operation) for operation in (
            op("property_set", values={"pass_index": 1, "show_name": True}),
            op("property_keyframe", values={"pass_index": 2}, frame=1, interpolation="CONSTANT"),
            op("property_keyframe", values={"pass_index": 5}, frame=9, interpolation="CONSTANT"))])
        self.assertEqual(derived, [
            {"kind": "keyframes", "target": OBJ, "property": "pass_index", "index": -1, "frames": [1, 9], "absent": [],
             "interpolation": "CONSTANT"}])
        for check in derived:
            self.assertEqual(validate_check(check), check)
        # Any channel data path is checkable (keys are read, never evaluated); a malformed path is refused.
        validate_check({"kind": "keyframes", "target": OBJ, "property": 'pose.bones["Arm"].location', "frames": [1]})
        for path in ("display type", "location;import os", "data..lens"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_check({"kind": "keyframes", "target": OBJ, "property": path, "frames": [1]})

    def test_a_data_path_is_read_from_the_data(self):
        curve = SimpleNamespace(keyframe_points=[point(10)])
        camera = SimpleNamespace(animation_data=None, data=animated({("dof.focus_distance", 0): curve}))
        check = validate_check({"kind": "keyframes", "target": OBJ, "property": "data.dof.focus_distance",
                                "frames": [10]})
        self.assertTrue(evaluate_animation(READER, camera, check)[0])


if __name__ == "__main__":
    unittest.main()
