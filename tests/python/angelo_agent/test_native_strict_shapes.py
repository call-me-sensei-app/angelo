# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shorter strict forms of the Codex typed plan (strict_shapes): offline, no subprocess or inference.

A shaped object may be closed after a cut instead of padded with nulls, and a
property only null fits is not written. A JSON Schema validator judges what
the strict schema admits; the host's own validator judges that a short form,
the padded form and the ordinary plan are one and the same call.
"""

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import codex_plan, strict_shapes  # noqa: E402
from angelo_agent.action_schemas import OPERATION_SCHEMAS  # noqa: E402
from angelo_agent.codex_plan import ordinary_schema, output_schema, parse_plan  # noqa: E402
from angelo_agent.plan_schema import PLAN_CORE_ACTIONS, plan_document  # noqa: E402
from angelo_agent.strict_schema import decode, encode, strict_problems, strict_schema  # noqa: E402
from angelo_agent.strict_shapes import SHAPE, annotate, forms  # noqa: E402
from angelo_agent.tool_specs import TOOLS, validate_call  # noqa: E402
from test_native_codex_plan import _resolver  # noqa: E402

try:
    import jsonschema
except ImportError:  # CI installs it (run_host_tests --max-skips 0).
    jsonschema = None


def plan_of(*operations, checks=None):
    program = {"mode": "APPLY", "operations": list(operations)}
    if checks is not None:
        program["expected_checks"] = checks
    return {"reply": "", "operations": [{"tool": "angelo_scene_program", "arguments": program}]}


def create(primitive="CUBE", **parameters):
    return {"kind": "create_primitive", "target": None, "parameters": dict({"primitive": primitive}, **parameters)}


def material(target="$post", **parameters):
    return {"kind": "material_principled", "target": target, "parameters": parameters}


IRON = {"Base Color": [.018, .021, .024, 1.], "Roughness": .36, "Metallic": .8}
# The first operations of a recorded railings plan, as an ordinary plan.
RAILINGS = plan_of(
    create(name="Railing Posts South", alias="post", location=[-12., -2.4, 1.5], scale=[.05, .05, .5]),
    material(name="Railing Dark Iron", inputs=IRON),
    {"kind": "transform", "target": "o4", "parameters": {"location": [0., 0., 1.]}},
    checks=[])


def written(plan):
    return encode(plan, ordinary_schema())


def text(value):
    return json.dumps(value, separators=(",", ":"))


class FormsTests(unittest.TestCase):
    def test_forms_are_prefixes_of_one_order_and_the_whole_object_is_last(self):
        names = ["primitive", "name", "alias", "location", "rotation", "scale", "size"]
        shape = {"order": ("name", "alias", "location", "scale"), "cuts": ("scale",)}
        self.assertEqual(forms(names, ["primitive"], ["primitive"], shape),
                         [["primitive", "name", "alias", "location", "scale"],
                          ["primitive", "name", "alias", "location", "scale", "rotation", "size"]])
        self.assertEqual(forms(names, [], [], None), [names], "No shape: one complete form in document order")

    def test_a_form_never_leaves_out_a_required_property(self):
        shape = {"order": ("a", "b", "c"), "cuts": ("a", "b")}
        self.assertEqual(forms(["a", "b", "c"], [], ["b"], shape), [["a", "b"], ["a", "b", "c"]])
        self.assertEqual(forms(["a", "b", "c"], [], ["c"], shape), [["a", "b", "c"]])

    def test_names_a_schema_no_longer_has_are_ignored(self):
        shape = {"order": ("gone", "a"), "cuts": ("gone", "a", "missing")}
        self.assertEqual(forms(["b", "a"], [], [], shape), [["a"], ["a", "b"]])
        self.assertEqual(forms(["a"], [], [], {"order": ("a",), "cuts": ("a",)}), [["a"]], "A cut at the end adds nothing")


class ShapeTableTests(unittest.TestCase):
    """Every shape names properties its object really has: a renamed field is noticed here, not in a model run."""

    def test_every_shape_matches_its_live_schema(self):
        def properties_of(kind, discriminator=None, value=None):
            parameters = OPERATION_SCHEMAS[kind]["properties"]["parameters"]
            members = parameters.get("oneOf", [parameters])
            return [member["properties"] for member in members
                    if discriminator is None or member["properties"][discriminator].get("const") == value]

        cases = [(shape, properties_of("create_primitive", "primitive", primitive))
                 for primitive, shape in strict_shapes.PRIMITIVES.items()]
        cases += [(shape, [member["settings"]["properties"] for member in properties_of("modifier_add", "type", kind)])
                  for kind, shape in strict_shapes.MODIFIERS.items()]
        # A kind or a field another build adds (material_assign, also) is shaped for that build and ignored here.
        cases += [(shape, properties_of(kind)) for kind, shape in strict_shapes.PARAMETERS.items()
                  if kind in OPERATION_SCHEMAS]
        cases.append((strict_shapes.PRINCIPLED_INPUTS,
                      [OPERATION_SCHEMAS["material_principled"]["properties"]["parameters"]["properties"]["inputs"]["properties"]]))
        for shape, found in cases:
            self.assertEqual(len(found), 1)
            for name in shape["order"] + shape["cuts"]:
                if name != "also":
                    self.assertIn(name, found[0])
            self.assertTrue(set(shape["cuts"]) <= set(shape["order"]))
        self.assertEqual(strict_shapes.forms(["name", "inputs", "slot"], [], [], strict_shapes.PARAMETERS[
            "material_principled"]), [["name"], ["name", "inputs"], ["name", "inputs", "slot"]], "A missing name is skipped")
        from angelo_agent.action_recipes import RECIPE_SCHEMAS
        from angelo_agent.tool_specs import TOOL_MAP
        for recipe, shape in strict_shapes.RECIPES.items():
            schema = RECIPE_SCHEMAS[recipe]
            self.assertTrue(set(shape["order"]) <= set(schema["properties"]), recipe)
            self.assertTrue(set(schema["required"]) <= set(shape["order"][:shape["order"].index(shape["cuts"][0]) + 1]),
                            "%s: a cut never leaves out a required parameter" % recipe)
        arguments = strict_shapes.ARGUMENTS["angelo_apply_recipe"]
        self.assertTrue(set(arguments["order"]) <= set(TOOL_MAP["angelo_apply_recipe"]["inputSchema"]["properties"]))

    def test_annotate_marks_exactly_the_shaped_objects(self):
        document = annotate(plan_document(TOOLS, codex_plan.PLAN_TOOLS))
        marked = json.dumps(document).count('"%s"' % SHAPE)
        # Program arguments, five primitives, BEVEL in add and configure, transform, a material and its inputs, and
        # each shaped recipe's arguments and parameters. A shaped kind the strict format does not spell out (its
        # parameters travel as JSON text) has nothing to mark.
        self.assertEqual(marked, 1 + len(strict_shapes.PRIMITIVES) + 2 * len(strict_shapes.MODIFIERS)
                         + len([kind for kind in strict_shapes.PARAMETERS if kind in PLAN_CORE_ACTIONS]) + 1
                         + 2 * len(strict_shapes.RECIPES))
        self.assertNotIn(SHAPE, json.dumps(output_schema()), "The mark never reaches the model")


@unittest.skipIf(jsonschema is None, "jsonschema is not installed")
class ShortFormTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.strict = jsonschema.Draft202012Validator(output_schema())

    def accepted(self, value):
        return not list(self.strict.iter_errors(value))

    def test_the_recorded_railings_operations_are_written_without_padding(self):
        value = written(RAILINGS)
        self.assertEqual(text(value["operations"][0]["arguments"]["operations"]), text([
            {"kind": "create_primitive", "parameters": {"primitive": "CUBE", "name": "Railing Posts South",
                                                        "alias": "post", "location": [-12., -2.4, 1.5],
                                                        "scale": [.05, .05, .5]}},
            {"kind": "material_principled", "target": "$post", "parameters": {
                "name": "Railing Dark Iron", "inputs": {"Base Color": [.018, .021, .024, 1.], "Roughness": .36,
                                                        "Metallic": .8}}},
            {"kind": "transform", "target": "o4", "parameters": {"location": [0., 0., 1.]}}]))
        self.assertEqual(list(value["operations"][0]["arguments"]), ["mode", "operations", "expected_checks"])
        self.assertTrue(self.accepted(value))
        self.assertNotIn("null", text(value))

    def test_a_short_form_the_padded_form_and_the_ordinary_plan_are_one_call(self):
        ordinary = ordinary_schema()
        short = written(RAILINGS)
        saved = codex_plan.SHORT_FORMS
        try:
            codex_plan.SHORT_FORMS = False
            codex_plan._documents.cache_clear()
            padded = encode(RAILINGS, ordinary_schema())
            self.assertGreater(len(text(padded)), len(text(short)) + 250)
            self.assertIn('"rotation":null', text(padded))
        finally:
            codex_plan.SHORT_FORMS = saved
            codex_plan._documents.cache_clear()
        self.assertTrue(self.accepted(padded), "A model may still write every field")
        self.assertEqual(decode(short, ordinary), decode(padded, ordinary))
        resolve = _resolver(4)  # A context that observed o1..o4: the host binds the transform's target.
        canonical = [validate_call(item["tool"], resolve(item["tool"], item["arguments"]))
                     for item in decode(short, ordinary)["operations"]]
        expected = copy.deepcopy(RAILINGS["operations"][0]["arguments"])
        self.assertEqual(canonical, [validate_call("angelo_scene_program", resolve("angelo_scene_program", expected))])
        self.assertEqual(len(canonical[0]["operations"]), 3)

    def test_only_trailing_fields_may_be_left_out(self):
        value = written(RAILINGS)
        cube = value["operations"][0]["arguments"]["operations"][0]["parameters"]
        del cube["alias"]  # A field before the cut: still required.
        self.assertFalse(self.accepted(value))
        value = written(RAILINGS)
        inputs = value["operations"][0]["arguments"]["operations"][1]["parameters"]["inputs"]
        del inputs["Roughness"]
        self.assertFalse(self.accepted(value))
        value = written(RAILINGS)
        value["operations"][0]["arguments"]["operations"][0]["parameters"]["size"] = 2  # Past a cut: all or none.
        self.assertFalse(self.accepted(value))

    def test_a_field_past_the_cut_takes_the_whole_object(self):
        plan = plan_of(create(name="Tilted", alias="t", location=[0, 0, 1], rotation=[0, .5, 0]))
        cube = written(plan)["operations"][0]["arguments"]["operations"][0]["parameters"]
        self.assertEqual(list(cube), ["primitive", "name", "alias", "location", "scale", "rotation", "size"])
        self.assertEqual((cube["scale"], cube["size"]), (None, None))
        self.assertTrue(self.accepted(written(plan)))
        self.assertEqual(parse_plan(text(written(plan)))[0], plan)

    def test_material_inputs_stop_where_the_request_stops(self):
        for inputs, keys in (({"Base Color": [1, 0, 0, 1]}, ["Base Color", "Roughness"]),
                             (IRON, ["Base Color", "Roughness", "Metallic"]),
                             (dict(IRON, **{"Emission Strength": 4.}), ["Base Color", "Roughness", "Metallic",
                                                                         "Emission Color", "Emission Strength", "IOR",
                                                                         "Alpha"])):
            with self.subTest(keys=keys):
                value = written(plan_of(material(target="o1", name="Glow", inputs=inputs)))
                parameters = value["operations"][0]["arguments"]["operations"][0]["parameters"]
                self.assertEqual(list(parameters), ["name", "inputs"])
                self.assertEqual(list(parameters["inputs"]), keys)
                self.assertTrue(self.accepted(value))

    def test_a_creating_operations_target_is_not_written_and_is_restored(self):
        value = written(RAILINGS)
        operation = value["operations"][0]["arguments"]["operations"][0]
        self.assertNotIn("target", operation)
        self.assertIsNone(parse_plan(text(value))[0]["operations"][0]["arguments"]["operations"][0]["target"])
        operation["target"] = None
        self.assertFalse(self.accepted(value), "Not a field of the strict form any more")
        # Where a target can be an object it is always written.
        self.assertIn("target", value["operations"][0]["arguments"]["operations"][1])

    def test_a_program_stops_after_its_operations_or_its_checks(self):
        bare = plan_of(create(name="Box", alias="box", location=[0, 0, 1]))
        self.assertEqual(list(written(bare)["operations"][0]["arguments"]), ["mode", "operations"])
        bound = copy.deepcopy(bare)
        bound["operations"][0]["arguments"]["read_set"] = [{"handle": "o2", "revision": "bbbbbbbb"}]
        self.assertEqual(list(written(bound)["operations"][0]["arguments"]),
                         ["mode", "operations", "expected_checks", "recovery_observation", "read_set", "write_set"])
        self.assertIsNone(written(bound)["operations"][0]["arguments"]["recovery_observation"])
        cited = copy.deepcopy(bound)
        cited["operations"][0]["arguments"]["recovery_observation"] = "recovery:" + "a" * 32
        for plan in (bare, bound, cited):
            self.assertTrue(self.accepted(written(plan)))
            self.assertEqual(parse_plan(text(written(plan)))[0], plan)


    def test_a_transform_may_stop_after_its_scale(self):
        plan = plan_of({"kind": "transform", "target": "o4", "parameters": {"location": [0, 0, 0], "scale": [2, .6, .15]}})
        parameters = written(plan)["operations"][0]["arguments"]["operations"][0]["parameters"]
        self.assertEqual(text(parameters), '{"location":[0,0,0],"rotation":null,"scale":[2,0.6,0.15]}')
        self.assertTrue(self.accepted(written(plan)))
        self.assertEqual(parse_plan(text(written(plan)))[0], plan)

    def test_a_kind_the_format_does_not_spell_out_travels_as_json_text_beside_shaped_ones(self):
        """mesh_bend: its parameters are one JSON string, decoded and
        validated exactly by the host, while the shaped create before it stops at its cut."""
        deck = create(name="Deck", alias="deck", location=[0, 0, 1], scale=[2, .6, .1])
        plan = plan_of(deck, {"kind": "mesh_bend", "target": "$deck", "parameters": {"rise": .3, "segments": 8}})
        value = written(plan)
        operations = value["operations"][0]["arguments"]["operations"]
        self.assertEqual(list(operations[0]["parameters"]), ["primitive", "name", "alias", "location", "scale"])
        self.assertEqual(operations[1]["parameters"], '{"rise":0.3,"segments":8}')
        self.assertTrue(self.accepted(value))
        self.assertEqual(parse_plan(text(value))[0], plan)
        arguments = decode(value, ordinary_schema())["operations"][0]["arguments"]
        call = validate_call("angelo_scene_program", _resolver(4)("angelo_scene_program", arguments))
        self.assertEqual(call["operations"][1]["parameters"], {"rise": .3, "axis": None, "up": "Z", "segments": 8})

    def test_a_recipe_call_stops_after_its_mode_and_its_parameters_where_the_recorded_calls_did(self):
        """Recorded GPT fit-dimensions calls wrote the schema's widest scale bounds, a tolerance and nulls they
        did not need (86.7 o200k tokens on average; this form 44.8)."""
        from angelo_agent.action_recipes import FIT_BOUNDS
        recorded = json.loads(
            '{"reply":"","operations":[{"tool":"angelo_apply_recipe","arguments":{"recipe":"fit-dimensions",'
            '"target":"o4","revision":null,"parameters":{"dimensions":[1,2,1],"tolerance":0.000001,'
            '"minimum_scale":0.000001,"maximum_scale":1000,"maximum_scale_ratio":100},"mode":"APPLY",'
            '"recovery_observation":null}}]}')
        short = {"reply": "", "operations": [{"tool": "angelo_apply_recipe", "arguments": {
            "recipe": "fit-dimensions", "target": "o4", "parameters": {"dimensions": [1, 2, 1]}, "mode": "APPLY"}}]}
        self.assertEqual(text(written(short)), text(short), "nothing padded")
        self.assertTrue(self.accepted(written(short)))
        self.assertTrue(self.accepted(recorded), "the recorded long form stays valid")
        resolve, ordinary = _resolver(4), ordinary_schema()
        calls = [validate_call(item["tool"], resolve(item["tool"], item["arguments"]))
                 for value in (short, recorded) for item in decode(value, ordinary)["operations"]]
        for key, value in FIT_BOUNDS.items():
            self.assertEqual(calls[0]["parameters"][key], value)
            self.assertEqual(calls[1]["parameters"][key], value, "what the recorded call stated")
        self.assertEqual({key: value for key, value in calls[0].items() if key != "parameters"},
                         {key: value for key, value in calls[1].items() if key != "parameters"},
                         "the host binds the observed revision either way")
        for recipe, parameters, keys in (
                ("material-setup", {"name": "Long Material 04", "base_color": [.2, .8, .3, 1], "roughness": .2,
                                    "metallic": 0.}, ["name", "base_color", "roughness", "metallic"]),
                ("bevel-and-shade", {"width": .05, "segments": 2, "apply_scale": True}, ["width", "segments",
                                                                                         "apply_scale"])):
            with self.subTest(recipe=recipe):
                plan = {"reply": "", "operations": [{"tool": "angelo_apply_recipe", "arguments": {
                    "recipe": recipe, "target": "o4", "parameters": parameters, "mode": "APPLY"}}]}
                value = written(plan)
                self.assertEqual(list(value["operations"][0]["arguments"]), ["recipe", "target", "parameters", "mode"])
                self.assertEqual(list(value["operations"][0]["arguments"]["parameters"]), keys)
                self.assertTrue(self.accepted(value))
                validate_call("angelo_apply_recipe", resolve("angelo_apply_recipe",
                                                             decode(value, ordinary)["operations"][0]["arguments"]))
        # A recipe no recorded plan used keeps one complete form; a stated revision takes the whole call.
        uv = {"reply": "", "operations": [{"tool": "angelo_apply_recipe", "arguments": {
            "recipe": "uv-prep", "target": "o4", "parameters": {"method": "CUBE"}, "mode": "APPLY"}}]}
        self.assertEqual(list(written(uv)["operations"][0]["arguments"]),
                         ["recipe", "target", "revision", "parameters", "mode", "recovery_observation"])
        bound = copy.deepcopy(short)
        bound["operations"][0]["arguments"]["revision"] = "bbbbbbbb"
        self.assertEqual(list(written(bound)["operations"][0]["arguments"]),
                         ["recipe", "target", "parameters", "mode", "revision", "recovery_observation"])
        for plan in (uv, bound):
            self.assertTrue(self.accepted(written(plan)))
            self.assertEqual(parse_plan(text(written(plan)))[0], plan)


class SchemaTests(unittest.TestCase):
    def test_the_shaped_schema_stays_in_the_strict_subset_and_its_limits(self):
        schema = output_schema()
        self.assertEqual(strict_problems(schema), [])
        properties = json.dumps(schema).count('"required"')
        self.assertLess(properties, 400, "Objects, not properties: far below every documented limit")

    def test_the_switch_restores_one_form_per_object(self):
        saved = codex_plan.SHORT_FORMS
        try:
            codex_plan.SHORT_FORMS = False
            codex_plan._documents.cache_clear()
            plain = output_schema()
            self.assertEqual(strict_problems(plain), [])
            size = len(text(plain))
        finally:
            codex_plan.SHORT_FORMS = saved
            codex_plan._documents.cache_clear()
        shaped = len(text(output_schema()))
        self.assertLess(size, shaped)
        # 3,324 B before the recipe forms and the transform's scale cut (+1,707 B: the
        # transform cut 207, three recipes' parameters about 640 and their arguments about 860), measured.
        self.assertLessEqual(shaped - size, 5100, "What the shorter forms cost in schema bytes")

    def test_shapes_change_nothing_for_an_unshaped_schema(self):
        schema = {"type": "object", "additionalProperties": False, "required": ["kind"], "properties": {
            "note": {"type": "string"}, "kind": {"const": "x"}, "target": {"type": "null"}}}
        strict = strict_schema(schema)
        self.assertEqual(list(strict["properties"]), ["kind", "note"], "The discriminator first; a null-only field gone")
        self.assertEqual(decode({"kind": "x", "note": None}, schema), {"kind": "x", "target": None},
                         "A null-only field has one value: decoded plans read alike whether or not it is required")
        required = dict(schema, required=["kind", "target"])
        self.assertEqual(decode({"kind": "x", "note": "n"}, required), {"kind": "x", "note": "n", "target": None})
        self.assertEqual(encode({"kind": "x", "target": None}, required), {"kind": "x", "note": None})


if __name__ == "__main__":
    unittest.main()
