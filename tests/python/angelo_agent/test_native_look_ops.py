# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The agent's ``camera_look`` operation as declared for the shared typed-operation pattern (``look_ops``), without
Blender: plain data, a valid schema whose defaults validate, every parameter an operator property of
``angelo.camera_look`` (read from its source) with the same default, the presets and stages are look_spec's, revision
4's fields, and the checks and receipt on snapshots. Contract: doc/angelo/camera_look.rst; Part E of the agent-verbs
design.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import json
import sys
import unittest
from pathlib import Path

try:
    import jsonschema
except ImportError:  # CI installs it (run_host_tests --max-skips 0).
    jsonschema = None
from host_test_policy import required_skip  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import look_ops, look_spec, read_only  # noqa: E402

FAMILIES = {"object", "transforms", "mesh", "modifiers", "vertex_groups", "materials", "node_groups", "node_trees",
            "world_nodes", "camera", "light", "curve", "armature", "pose", "animation", "nla_stack", "nla",
            "particles", "properties", "data", "visibility", "collections", "scene", "world", "markers"}


def _literal(node):
    try:
        return ast.literal_eval(node)
    except ValueError:
        return None


def operator_properties(module, idname):
    tree = ast.parse((ADDON / module).read_text(encoding="utf-8"))
    for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
        names = {t.id: _literal(s.value) for s in cls.body if isinstance(s, ast.Assign)
                 for t in s.targets if isinstance(t, ast.Name)}
        if names.get("bl_idname") != idname:
            continue
        props = {}
        for item in cls.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.annotation, ast.Call):
                call = item.annotation
                kind = call.func.attr if isinstance(call.func, ast.Attribute) else call.func.id
                props[item.target.id] = dict({k.arg: _literal(k.value) for k in call.keywords}, kind=kind)
        return props
    raise AssertionError("no operator %s in %s" % (idname, module))


def snapshot(stages=("vignette", "grain"), looks=1, through=True, simple=(True, True)):
    look = {"preset": "VINTAGE", "stages": list(stages), "camera": "Camera", "breathing": 0.0} if stages else None
    return {"look": look, "compositor": {"look_nodes": looks, "output_through_look": through},
            "drivers": [{"path": "p%d" % i, "simple": value} for i, value in enumerate(simple)]}


class DeclarationTests(unittest.TestCase):
    op = look_ops.WRAPPED_OPS[0]

    def test_plain_data(self):
        tree = ast.parse((ADDON / "look_ops.py").read_text(encoding="utf-8"))
        self.assertEqual([n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))], [])
        json.dumps(self.op["parameters"])

    @unittest.skipIf(jsonschema is None, required_skip("jsonschema is not installed"))
    def test_schema_and_defaults(self):
        schema = self.op["parameters"]
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate({name: spec["default"] for name, spec in schema["properties"].items()}, schema)

    def test_presets_and_stages_are_look_specs(self):
        self.assertEqual(set(look_ops.PRESETS), set(look_spec.PRESETS))
        self.assertEqual(look_ops.STAGES, look_spec.ORDER)
        props = operator_properties("look_ui.py", self.op["operator"])
        self.assertEqual(self.op["parameters"]["properties"]["preset"]["enum"], list(look_ops.PRESETS))
        self.assertIsNotNone(props["preset"])

    @unittest.skipIf(jsonschema is None, required_skip("jsonschema is not installed"))
    def test_every_preset_fits_the_schema(self):
        for name, preset in look_spec.PRESETS.items():
            preset = read_only.thawed(preset)   # As a model sends it: the spec's own tables are sealed.
            camera = {k: v for k, v in preset.get("camera", {}).items() if k != "breathing"}
            jsonschema.validate({"preset": name, "stages": preset["stages"], "camera_values": camera},
                                self.op["parameters"])

    def test_every_parameter_is_an_operator_property_with_its_default(self):
        props = operator_properties("look_ui.py", self.op["operator"])
        schema = self.op["parameters"]["properties"]
        self.assertEqual(set(self.op["map"]), set(schema))
        for name, prop in self.op["map"].items():
            self.assertIn(prop, props, name)
            if name in look_ops.JSON_PARAMETERS:
                self.assertEqual((props[prop]["kind"], props[prop]["default"]), ("StringProperty", ""), name)
                continue
            self.assertEqual(props[prop]["default"], schema[name]["default"], name)

    def test_revision_four_fields(self):
        self.assertIsNone(self.op["target"])
        self.assertIsNone(self.op["creates"])
        self.assertEqual(self.op["new_ids"], {"NODETREE"})
        self.assertEqual(self.op["mutates"], {"camera", "scene", "node_trees", "animation"})
        self.assertLessEqual(self.op["mutates"], FAMILIES)
        self.assertEqual(self.op["parameters"]["properties"]["camera"]["handle"]["role"], "write")

    def test_revision_five_shares_the_turn_budget(self):
        # Part E revision 5: every wrapped operation shares wrapped_op_budget.TURN_BUDGET_S; a declared budget_s is
        # refused. A look's cost does not grow with the scene, so it has no bound; its measured p95 must still fit.
        self.assertNotIn("budget_s", self.op)
        self.assertIsNone(self.op["input_bound"])


class CheckTests(unittest.TestCase):
    def by_name(self, after):
        return {item["name"]: item for item in look_ops.camera_look_checks({}, {}, after)}

    def test_a_good_look_passes(self):
        result = self.by_name(snapshot())
        self.assertTrue(all(item["passed"] for item in result.values()), result)

    def test_a_cleared_look_passes_with_nothing_left(self):
        result = self.by_name(snapshot(stages=(), looks=0, through=False, simple=()))
        self.assertTrue(all(item["passed"] for item in result.values()), result)

    def test_two_look_nodes_or_a_bypassed_look_fail(self):
        self.assertFalse(self.by_name(snapshot(looks=2))["one_look_node"]["passed"])
        self.assertFalse(self.by_name(snapshot(through=False))["output_through_look"]["passed"])

    def test_a_driver_that_needs_python_fails(self):
        item = self.by_name(snapshot(simple=(True, False)))["drivers_need_no_python"]
        self.assertFalse(item["passed"])
        self.assertIn("p1", item["detail"])

    def test_receipt(self):
        self.assertEqual(look_ops.camera_look_receipt(snapshot()),
                         {"preset": "VINTAGE", "stages": ["vignette", "grain"], "camera": "Camera", "breathing": 0.0})
        self.assertEqual(look_ops.camera_look_receipt({"look": None})["preset"], "CLEAN")
        matched = snapshot()
        matched["look"]["exposure_stops"] = 2.0
        self.assertEqual(look_ops.camera_look_receipt(matched)["exposure_stops"], 2.0)


class ExposureTests(unittest.TestCase):
    """The stops helper (look_spec.exposure_stops) as the declaration's ``exposure`` parameter: JSON on one operator
    property, the shape look_spec accepts, nothing else."""
    op = look_ops.WRAPPED_OPS[0]

    def test_exposure_is_a_json_parameter_on_its_own_property(self):
        self.assertIn("exposure", look_ops.JSON_PARAMETERS)
        self.assertEqual(self.op["map"]["exposure"], "exposure")
        props = operator_properties("look_ui.py", self.op["operator"])
        self.assertEqual(props["exposure"]["kind"], "StringProperty")
        self.assertEqual(props["exposure"]["default"], "")

    @unittest.skipIf(jsonschema is None, required_skip("jsonschema is not installed"))
    def test_the_schema_takes_what_look_spec_takes_and_refuses_the_rest(self):
        schema = self.op["parameters"]
        good = {"reference": {"f_stop": 8.0, "shutter": 0.01, "iso": 100}, "setting": {"shutter": 0.01, "iso": 400}}
        jsonschema.validate({"exposure": good}, schema)
        self.assertAlmostEqual(look_spec.exposure_stops(good, 8.0), 2.0)
        named = {"reference": good["reference"], "setting": dict(good["setting"], f_stop=4.0)}
        jsonschema.validate({"exposure": named}, schema)
        for bad in ({"reference": good["reference"]}, {"setting": good["setting"]},
                    dict(good, extra=1), {"reference": {"f_stop": 8.0, "shutter": 0.01}, "setting": good["setting"]},
                    {"reference": good["reference"], "setting": dict(good["setting"], gain=2)},
                    {"reference": good["reference"], "setting": dict(good["setting"], shutter=0)}):
            with self.subTest(bad):
                with self.assertRaises(jsonschema.ValidationError):
                    jsonschema.validate({"exposure": bad}, schema)
                with self.assertRaises(look_spec.LookError):
                    look_spec.exposure_stops(bad, 8.0)


if __name__ == "__main__":
    unittest.main()
