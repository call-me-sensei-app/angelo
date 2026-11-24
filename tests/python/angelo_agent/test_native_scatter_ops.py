# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The agent's ``scatter`` operation as declared for the shared typed-operation pattern (``scatter_ops``), without
Blender: plain data, a valid schema whose defaults validate, every parameter an operator property of
``angelo.scatter`` (read from its source) with a default the schema accepts, revision 4's fields (nothing existing is
mutated, one new object and node group, levers), the biome presets, and the checks and receipt on snapshots.
Contract: doc/angelo/scatter.rst; Part E of the agent-verbs design.
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
from angelo_agent import scatter_ops, scatter_spec  # noqa: E402

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


def snapshot(digest="d", created=({"name": "Scatter", "type": "MESH", "modifiers": ["NODES"]},),
             layers=(("Trees", 12), ("Rocks", 40)), spacing=1.02):
    return {"target": {"digest": digest}, "created": list(created),
            "scatter": {"instances": sum(count for _name, count in layers),
                        "layers": [{"name": name, "instances": count} for name, count in layers],
                        "min_spacing": spacing}}


class DeclarationTests(unittest.TestCase):
    op = scatter_ops.WRAPPED_OPS[0]

    def test_plain_data(self):
        tree = ast.parse((ADDON / "scatter_ops.py").read_text(encoding="utf-8"))
        self.assertEqual([n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))], [],
                         "scatter_ops stays plain data until wrapped_ops lands")
        json.dumps(self.op["parameters"])

    @unittest.skipIf(jsonschema is None, required_skip("jsonschema is not installed"))
    def test_schema_and_defaults(self):
        schema = self.op["parameters"]
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate({name: spec["default"] for name, spec in schema["properties"].items()}, schema)

    def test_every_parameter_is_an_operator_property_with_its_default(self):
        props = operator_properties("scatter_ui.py", self.op["operator"])
        schema = self.op["parameters"]["properties"]
        self.assertEqual(set(self.op["map"]), set(schema))
        for name, prop in self.op["map"].items():
            self.assertIn(prop, props, name)
            default = schema[name]["default"]
            if name in scatter_ops.JSON_PARAMETERS:   # Structured: canonical JSON in one StringProperty.
                self.assertEqual(props[prop]["kind"], "StringProperty", name)
                self.assertEqual(props[prop]["default"], "", name)
                continue
            self.assertEqual(props[prop]["default"], default, name)
            for bound, check in (("min", "minimum"), ("max", "maximum")):
                if props[prop].get(bound) is not None and check in schema[name]:
                    inside = schema[name][check] >= props[prop][bound] if bound == "min" \
                        else schema[name][check] <= props[prop][bound]
                    self.assertTrue(inside, "%s: the schema's %s is inside the property's" % (name, check))

    def test_revision_four_fields(self):
        self.assertEqual(self.op["creates"], {"object"})
        self.assertEqual(self.op["new_ids"], {"NODETREE"})
        self.assertEqual(set(self.op["mutates"]), set(), "the surface and assets are only read")
        self.assertLessEqual(set(self.op["mutates"]), FAMILIES)
        self.assertIs(self.op["reads_only"], False)
        self.assertLessEqual(set(self.op["levers"]), set(self.op["parameters"]["properties"]))
        self.assertEqual((self.op["target"], self.op["mode"], self.op["source"]), ("MESH", "OBJECT", "angelo"))

    def test_revision_five_shares_the_turn_budget(self):
        # Part E revision 5: every wrapped operation shares wrapped_op_budget.TURN_BUDGET_S; a declared budget_s is
        # refused. The input_bound stays None until bound_from_timings sets it from timed runs on both machines.
        self.assertNotIn("budget_s", self.op)
        bound = self.op["input_bound"]
        self.assertTrue(bound is None or (isinstance(bound, dict) and bound
                                          and all(type(count) is int and count > 0 for count in bound.values())))

    @unittest.skipIf(jsonschema is None, required_skip("jsonschema is not installed"))
    def test_biomes_are_the_presets_and_their_layers_fit_the_schema(self):
        names = sorted(path.name.split(".")[0] for path in scatter_spec.PRESET_DIR.glob("*.angelo-biome.json"))
        self.assertEqual(sorted(scatter_ops.BIOMES), names)
        layer_schema = self.op["parameters"]["properties"]["layers"]["items"]
        for path in scatter_spec.PRESET_DIR.glob("*.angelo-biome.json"):
            biome = scatter_spec.load_biome(path.read_text(encoding="utf-8"))
            for layer in biome["layers"]:
                layer = dict(layer, assets=[{"object_name": "Stand-in"}])
                jsonschema.validate(layer, layer_schema)

    @unittest.skipIf(jsonschema is None, required_skip("jsonschema is not installed"))
    def test_checked_layers_validate_against_the_schema(self):
        # What scatter_spec accepts, the declaration accepts: a layer with every rule.
        layers = scatter_spec.check_layers([{
            "name": "All", "assets": [{"object": "Tree", "weight": 2}], "density": 0.5, "spacing": 1.5, "seed": 3,
            "rules": {"slope": {"min": 0, "max": 30, "soft": 5}, "height": {"min": 0, "max": 0.6, "relative": True},
                      "mask": {"name": "paint", "invert": True},
                      "distance": {"objects": ["Path"], "keep_away": 2, "falloff": 1},
                      "noise": {"scale": 8, "amount": 0.4}},
            "transform": {"scale": [0.7, 1.3], "align": 0.5, "yaw": True, "tilt": 5}}])
        jsonschema.validate({"layers": layers}, self.op["parameters"])


class CheckTests(unittest.TestCase):
    checks = staticmethod(scatter_ops.scatter_checks)

    def by_name(self, before, after):
        return {item["name"]: item for item in self.checks({}, before, after)}

    def test_a_good_system_passes_every_check(self):
        result = self.by_name(snapshot(), snapshot())
        self.assertTrue(all(item["passed"] for item in result.values()), result)

    def test_a_changed_surface_fails(self):
        self.assertFalse(self.by_name(snapshot("a"), snapshot("b"))["surface_unchanged"]["passed"])

    def test_an_empty_layer_fails_and_says_what_to_change(self):
        item = self.by_name(snapshot(), snapshot(layers=(("Trees", 0), ("Rocks", 5))))["every_layer_placed"]
        self.assertFalse(item["passed"])
        self.assertIn("Trees", item["detail"])
        self.assertIn("density", item["detail"])

    def test_overlap_fails_and_float_noise_does_not(self):
        self.assertFalse(self.by_name(snapshot(), snapshot(spacing=0.9))["spacing_kept"]["passed"])
        self.assertTrue(self.by_name(snapshot(), snapshot(spacing=0.9995))["spacing_kept"]["passed"])

    def test_one_new_object_with_one_nodes_modifier(self):
        two = snapshot(created=({"name": "A", "type": "MESH", "modifiers": ["NODES"]},) * 2)
        self.assertFalse(self.by_name(snapshot(), two)["one_system"]["passed"])

    def test_receipt(self):
        self.assertEqual(scatter_ops.scatter_receipt(snapshot()),
                         {"object": "Scatter", "instances": 52,
                          "layers": [{"name": "Trees", "instances": 12}, {"name": "Rocks", "instances": 40}]})


if __name__ == "__main__":
    unittest.main()
