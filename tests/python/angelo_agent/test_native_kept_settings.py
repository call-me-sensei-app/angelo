# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Settings a call leaves out stay as they are (action_schemas.SETTINGS_NOTE): the host's side.

A canonical program carries only what a call states for a typed modifier, constraint and Principled material
(no schema default is filled in), the model reads that fact where defaults were shown, checks state only what was
written, and the bound obligations model a held material's in-place edit and refuse it as the kernel does.
``test_blender_kept_settings`` measures the kernels against Blender.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.action_catalog import validate_operation  # noqa: E402
from angelo_agent.action_schemas import (CONSTRAINT_SETTINGS, MODIFIER_SETTINGS, PARAMETER_SCHEMAS,  # noqa: E402
                                         SETTINGS_NOTE)
from angelo_agent.derived_checks import derive_checks  # noqa: E402
from angelo_agent.refusal_codes import RefusalError  # noqa: E402
from test_native_material_obligations import A, facts, op, program, required_checks  # noqa: E402

OBJ = "scn:" + "a" * 32 + ":7"


def defaults(schema, path=""):
    """Every default a schema still fills, by path (the settings objects' own ``{}`` left out)."""
    found = []
    if isinstance(schema, dict):
        if "default" in schema and path.count(".") > 0:
            found.append(path)
        for key, child in (schema.get("properties") or {}).items():
            found += defaults(child, path + "." + key)
        for index, child in enumerate(schema.get("oneOf") or ()):
            found += defaults(child, path + "[%d]" % index)
        if schema.get("type") == "array":
            found += defaults(schema["items"], path + "[]")
    return found


class CanonicalTests(unittest.TestCase):
    def test_no_typed_setting_or_input_has_a_default(self):
        for kind, schema in MODIFIER_SETTINGS.items():
            self.assertEqual(defaults(schema, kind), [], kind)
        for kind, schema in CONSTRAINT_SETTINGS.items():
            self.assertEqual(defaults(schema, kind), [kind + ".set_inverse"] if kind == "CHILD_OF" else [], kind)
        self.assertEqual(defaults(PARAMETER_SCHEMAS["material_principled"]["properties"]["inputs"], "inputs"), [])
        for variant in PARAMETER_SCHEMAS["constraint_add"]["oneOf"]:
            self.assertTrue({"influence", "owner_space", "target_space"}.isdisjoint(
                name for name, field in variant["properties"].items() if "default" in field))

    def test_a_canonical_program_carries_only_what_the_call_states(self):
        for kind, settings in (("modifier_configure", {"segments": 3}), ("modifier_add", {"width": .2})):
            parameters = {"type": "BEVEL", "settings": settings, **({"modifier": "Bevel"} if kind == "modifier_configure"
                                                                    else {})}
            self.assertEqual(validate_operation(op(kind, OBJ, **parameters))["parameters"]["settings"], settings)
        added = validate_operation({"kind": "constraint_add", "target": OBJ,
                                    "parameters": {"type": "DAMPED_TRACK", "target": A}})["parameters"]
        self.assertEqual((added["settings"], set(added) & {"influence", "owner_space", "target_space"}), ({}, set()))
        inputs = validate_operation(op("material_principled", OBJ, name="Red", inputs={"Roughness": .3}))
        self.assertEqual(inputs["parameters"]["inputs"], {"Roughness": .3})
        keyed = validate_operation(op("keyframe_insert", OBJ, property="location", value=[0, 0, 1]))
        self.assertNotIn("interpolation", keyed["parameters"])
        # A typed null keeps a setting as leaving it out does: one canonical program either way.
        for settings in ({"mirror_object": None}, {}):
            self.assertEqual(validate_operation(op("modifier_add", OBJ, type="MIRROR", settings=settings))
                             ["parameters"]["settings"], {})

    def test_the_model_reads_the_fact_where_defaults_were(self):
        from angelo_agent import action_catalog, tool_specs
        program_tool = json.dumps(tool_specs.TOOL_MAP["angelo_scene_program"])
        self.assertEqual(program_tool.count(SETTINGS_NOTE), 4)  # modifier_add/configure, constraint_add, material.
        self.assertEqual(json.dumps(tool_specs.native_plan_schema()).count(SETTINGS_NOTE), 3)  # The core kinds.
        for kind in ("modifier_add", "modifier_configure", "constraint_add", "material_principled"):
            self.assertIn(SETTINGS_NOTE, json.dumps(action_catalog.catalog(kind)), kind)


class CheckTests(unittest.TestCase):
    def test_checks_state_only_what_was_written_and_keep_what_a_partial_configure_does_not(self):
        added = validate_operation(op("modifier_add", OBJ, name="Bevel", type="BEVEL", settings={"width": .2}))
        configured = validate_operation(op("modifier_configure", OBJ, modifier="Bevel", type="BEVEL",
                                           settings={"segments": 3}))
        (check,) = derive_checks([added, configured])
        self.assertEqual(check["values"], {"width": .2, "segments": 3})
        (bare,) = derive_checks([validate_operation(op("modifier_add", OBJ, name="Smooth", type="SUBSURF"))])
        self.assertEqual(bare["values"], {}, "The modifier, of its type: nothing else was written")
        other = validate_operation(op("modifier_configure", OBJ, modifier="Other", type="BEVEL", settings={"width": .1}))
        self.assertEqual(len(derive_checks([added, other])), 2, "Another modifier's configure keeps this check")


def held(owner=A, bsdf=None):
    """Binding facts: ``owner`` holds "Paint" (live:10) whose Principled facts are ``bsdf``."""
    bound = facts([owner], {owner: [("Paint", 10)]})
    bound["resources"]["live:10"]["bsdf"] = bsdf or {"count": 1, "name": "Principled BSDF", "linked": []}
    return bound


class ShownTests(unittest.TestCase):
    def test_a_shader_is_shown_through_links_and_muted_nodes_internal_links_only(self):
        """Second round: a muted node passes its internal links, and each engine renders its own output."""
        from angelo_agent.material_rules import reaches_surface
        direct = [("P", "BSDF", "Out", "Surface")]
        self.assertTrue(reaches_surface("P", direct, {}, {"Out"}))
        self.assertFalse(reaches_surface("P", direct, {}, {"Other"}))
        self.assertFalse(reaches_surface("P", [("P", "BSDF", "Out", "Volume")], {}, {"Out"}))
        mixed = [("P", "BSDF", "Mix", "Shader"), ("E", "Emission", "Mix", "Shader_001"), ("Mix", "Shader", "Out", "Surface")]
        self.assertTrue(reaches_surface("P", mixed, {}, {"Out"}))
        passes = {"Mix": {("Shader", "Shader")}}  # A muted Mix Shader passes its first shader through.
        self.assertTrue(reaches_surface("P", mixed, passes, {"Out"}))
        self.assertFalse(reaches_surface("E", mixed, passes, {"Out"}))
        self.assertFalse(reaches_surface("P", direct, {"P": set()}, {"Out"}), "A muted shader passes nothing")


class InPlaceObligationTests(unittest.TestCase):
    def test_a_held_material_is_edited_in_place_on_its_bound_shader(self):
        request = program([op("material_principled", A, name="Paint", inputs={"Roughness": .7})], [A])
        bound = held(bsdf={"count": 1, "name": "Shader", "linked": ["Base Color"]})
        (graph,) = [check for check in required_checks(request, material_facts=bound) if check["kind"] == "node_graph"]
        self.assertEqual(graph["nodes"], [{"name": "Shader", "inputs": {"Roughness": .7}}])

    def test_refused_as_the_kernel_refuses_it(self):
        for bsdf, inputs, code in (({"count": 0}, {"Roughness": .7}, "PRINCIPLED_NOT_SINGLE"),
                                   ({"count": 2}, {"Roughness": .7}, "PRINCIPLED_NOT_SINGLE"),
                                   ({"count": 1, "name": "P", "linked": ["Metallic"]}, {"Metallic": .2}, "INPUT_LINKED"),
                                   ({"count": 1, "name": "P", "linked": [], "shown": False}, {"Roughness": .7},
                                    "PRINCIPLED_NOT_SHOWN")):
            request = program([op("material_principled", A, name="Paint", inputs=inputs)], [A])
            with self.subTest(code=code, bsdf=bsdf), self.assertRaisesRegex(RefusalError, code):
                required_checks(request, material_facts=held(bsdf=bsdf))
        linked_elsewhere = held(bsdf={"count": 1, "name": "P", "linked": ["Metallic"]})
        request = program([op("material_principled", A, name="Paint", inputs={"Roughness": .7})], [A])
        required_checks(request, material_facts=linked_elsewhere)  # A link into another input does not matter.

    def test_an_earlier_link_leaves_the_wiring_to_the_kernel_and_restating_a_new_material_is_one_material(self):
        """Tenth review: a link into Base Color refused a later Roughness (the kernel accepts it), and the obligations'
        own comparator refused a restatement program_rules and the kernel accepted."""
        link = op("node_edit", A, material="Paint", edits=[{"op": "link", "from_node": "Noise", "from_socket": "Color",
                                                             "to_node": "Principled BSDF", "to_socket": "Base Color"}])
        request = program([link, op("material_principled", A, name="Paint", inputs={"Roughness": .7})], [A])
        bound = held()
        bound["resources"]["live:10"]["inputs"] = {"Principled BSDF": {"Base Color": False}}
        required_checks(request, material_facts=bound)
        for first, second in (({"Base Color": [1, 0, 0, 1], "Roughness": .5}, {"Base Color": [1, 0, 0, 1]}),
                              ({}, {"Roughness": .5})):
            restated = program([op("create_primitive", None, primitive="CUBE", alias="a"),
                                op("create_primitive", None, primitive="CUBE", alias="b"),
                                op("material_principled", "$a", name="Fresh", inputs=first),
                                op("material_principled", "$b", name="Fresh", inputs=second)], [A])
            required_checks(restated, material_facts=facts([A]))  # Not MATERIAL_REDEFINED: Blender's defaults.

    def test_an_earlier_node_edit_that_may_change_the_shader_refuses_and_a_creation_does_not(self):
        swap = op("node_edit", A, material="Paint", edits=[{"op": "swap", "node": "Principled BSDF",
                                                             "type": "ShaderNodeBsdfDiffuse"}])
        request = program([swap, op("material_principled", A, name="Paint", inputs={"Roughness": .7})], [A])
        with self.assertRaisesRegex(RefusalError, "PRINCIPLED_UNKNOWN"):
            required_checks(request, material_facts=held())
        restated = program([op("create_primitive", None, primitive="CUBE", alias="a"),
                            op("material_principled", "$a", name="Fresh", inputs={"Roughness": .2}),
                            op("material_principled", "$a", name="Fresh", inputs={"Metallic": .9})])
        values = [check for check in required_checks(restated) if check["kind"] == "material_values"]
        self.assertTrue(values)


if __name__ == "__main__":
    unittest.main()
