# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Claude typed-plan schema diet: exact core, deferred families, exact host validation.

Pure schema/lookup checks plus the typed-plan loop with mocked rounds; no Claude
process, credentials or inference. Natural-language effects (whether a model
looks a deferred schema up first) need a live run and are not claimed here.
"""

import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from claude_protocol_fixtures import host
from test_native_schema_compaction import expand_generated
from angelo_agent.tagged_unions import discriminated, members
from angelo_agent import plan_schema
from angelo_agent.action_schemas import OPERATION_SCHEMAS
from angelo_agent.agent_guidance import SCULPT_GUIDANCE, STUDIO_GUIDANCE, FLOW_GUIDANCE
from angelo_agent.execution_spec import validate_execution
from angelo_agent.identifiers import INFERRED_FIELDS, model_facing
from angelo_agent.plan_schema import (CLAUDE_CORE_ACTIONS, CLAUDE_DEFERRED_ACTIONS, CLAUDE_EVERYDAY_ACTIONS,
                                      DEFERRED_ACTIONS, DEFERRED_CHECKS, DEFERRED_MODIFIER_CHECKS, DEFERRED_TOOLS,
                                      PLAN_CORE_ACTIONS, deferred_index, deferred_references, lookup, model_schema,
                                      plan_arguments, rejection_prompt, staged_rejection_prompt, without_lengths)
from angelo_agent.protocol import RpcError
from angelo_agent.providers import (CLAUDE_INSTRUCTIONS, CLAUDE_PLAN_INSTRUCTIONS, CORE_INSTRUCTIONS,
                                    DIRECTOR_GUIDANCE, INSTRUCTIONS, VIEW_GUIDANCE, ClaudeProvider)
from angelo_agent.sculpt_spec import SCULPT_NAMES
from angelo_agent.studio_spec import STUDIO_NAMES
from angelo_agent.tool_specs import COLLABORATOR_TOOLS, TOOL_MAP, TOOLS, native_plan_schema, validate_call
from angelo_agent.validation_spec import CHECK_SCHEMA, CHECK_VARIANTS, validate_checks
from angelo_agent.action_catalog import validate_operation
from plan_budgets import CLAUDE_INSTRUCTION_BUDGET_BYTES, DECLARED_TOOLS_BUDGET_BYTES, PLAN_BUDGET_BYTES  # noqa: E402

HANDLE = "scn:" + "a" * 32 + ":7"
REVISION = "b" * 64
# Measured before the diet (toolset 16): a 99,770-byte plan schema and 13,921
# bytes of appended instructions. These budgets keep the diet from regressing.
# Toolset 19 added about 1 KB to the instructions: the turn-context notes
# every Claude session's first observation used to carry, and the short
# identifier and delta conventions (doc/angelo/performance.rst, "ID diet").
# Toolset 20 added about 0.5 KB: inferred bindings, observation answers and
# the optional edit reply ("Turn diet").
# Toolset 21 (doc/angelo/agent_stack_integration.rst) added about 190 bytes:
# fast-tier reliability's answer only through StructuredOutput, the {tool,
# arguments} shape and handle-only targets (each a measured cause of extra
# Haiku rounds, doc/angelo/fast_tier_reliability.rst), and named objects being
# listed whole in a delta.
# Toolset 24 (doc/angelo/gpt_launch.rst) merged adaptive routing's one-line
# "conf":"low" guidance (62 bytes), which Auto routing needs.
# Phase 5's deferred kinds and checks (animation, node_edit, the operator_run families), each valid on the host.
_P5 = "scn:" + "a" * 32 + ":%d"
PHASE5_OPERATIONS = [
    {"kind": "keyframe_configure", "target": _P5 % 7, "parameters": {"property": "location", "frames": [1],
                                                                      "key_type": "BREAKDOWN", "extrapolation": "LINEAR"}},
    {"kind": "keyframe_edit", "target": _P5 % 7, "parameters": {"operation": "OFFSET", "property": "location",
                                                                 "frame_range": [10, 20], "offset": 5}},
    {"kind": "fcurve_modifier", "target": _P5 % 7, "parameters": {"property": "location", "index": 0, "type": "NOISE",
                                                                   "name": "Shake", "settings": {"strength": 0.5}}},
    {"kind": "keyframe_copy", "target": _P5 % 7, "parameters": {"source": _P5 % 8, "property": "location", "offset": 5}},
    {"kind": "action_set", "target": _P5 % 7, "parameters": {"mode": "NEW", "name": "Idle", "stash_previous": True}},
    {"kind": "nla_edit", "target": _P5 % 7, "parameters": {"operation": "STRIP_ADD", "track": "Layer", "frame": 50,
                                                            "action": "dat:" + "b" * 32 + ":ACTION:12",
                                                            "settings": {"repeat": 2.0}}},
    {"kind": "marker_edit", "target": _P5 % 9, "parameters": {"operation": "ADD", "name": "Hit", "frame": 24}},
    {"kind": "node_edit", "target": _P5 % 7, "parameters": {"material": "Wood", "edits": [
        {"op": "add", "node": "Noise", "type": "ShaderNodeTexNoise", "inputs": {"Scale": 3}},
        {"op": "link", "from_node": "Noise", "from_socket": "Fac", "to_node": "Principled BSDF", "to_socket": "Roughness"}]}},
    {"kind": "operator_run", "target": _P5 % 7, "parameters": {"operator": "transform.translate",
                                                                "properties": {"value": [0, 0, 1]}}},
]
PHASE5_CHECKS = [
    {"kind": "keyframes", "target": _P5 % 7, "property": "location", "index": 0, "frames": [1], "key_type": "BREAKDOWN",
     "extrapolation": "LINEAR"},
    {"kind": "fcurve_modifier", "target": _P5 % 7, "property": "location", "index": 0, "name": "Shake",
     "values": {"strength": 0.5}, "tolerance": 0.0001},
    {"kind": "markers", "target": _P5 % 9, "present": [{"name": "Hit", "frame": 24}], "absent": ["Start"]},
    {"kind": "nla", "target": _P5 % 7, "action": "Idle", "track_names": ["Layer"],
     "strip": {"track": "Layer", "name": "Walk", "values": {"frame_start": 50, "repeat": 2}}},
    {"kind": "node_graph", "target": _P5 % 7, "material": "Wood", "nodes": [{"type": "ShaderNodeTexNoise",
                                                                            "inputs": {"Scale": 3}}],
     "links": [{"from_type": "ShaderNodeTexNoise", "from_socket": "Fac", "to_node": "Principled BSDF",
                "to_socket": "Roughness"}]},
    {"kind": "curve_counts", "target": _P5 % 7, "points": {"minimum": 14, "maximum": 14}},
]
# Everyday kinds exact in Claude's plan (doc/angelo/performance.rst, "Everyday kinds"): 40,463 -> 50,918 bytes,
# against one lookup round of about 3.5 s per first use.
EVERYDAY_OPERATIONS = [
    {"kind": "object_delete", "target": _P5 % 7, "parameters": {}},
    {"kind": "object_rename", "target": _P5 % 7, "parameters": {"name": "Hero"}},
    {"kind": "modifier_apply", "target": _P5 % 7, "parameters": {"modifier": "Bevel"}},
    {"kind": "collection_link", "target": _P5 % 7, "parameters": {"collection": "Lighting", "create": True,
                                                                   "mode": "MOVE"}},
    {"kind": "object_join", "target": _P5 % 7, "parameters": {"sources": [_P5 % 8]}},
    {"kind": "world_configure", "target": _P5 % 9, "parameters": {"color": [0.05, 0.05, 0.05], "strength": 1}},
    {"kind": "object_duplicate", "target": None, "parameters": {"source": _P5 % 7, "alias": "copy",
                                                                 "name": "Copy", "location": [3, 0, 0]}},
    {"kind": "marker_edit", "target": _P5 % 9, "parameters": {"operation": "ADD", "name": "Impact", "frame": 60}},
    {"kind": "camera_configure", "target": _P5 % 7, "parameters": {"settings": {"lens": 35}, "look_at": [0, 0, 0]}},
    {"kind": "property_set", "target": _P5 % 7, "parameters": {"values": {"data.dof.use_dof": True}}},
    {"kind": "light_configure", "target": _P5 % 7, "parameters": {"settings": {"energy": 2000, "radius": 0.5}}},
    {"kind": "armature_bind", "target": _P5 % 7, "parameters": {"armature": _P5 % 8, "groups": [
        {"bone": "Bone", "weights": [{"vertex": 0, "weight": 1}]}]}},
    {"kind": "mesh_extrude", "target": _P5 % 7, "parameters": {"face_indices": [5], "translation": [0, 0, 1]}},
    {"kind": "camera_create", "target": None, "parameters": {"name": "Top", "location": [0, 0, 10],
                                                              "look_at": [0, 0, 0]}},
    {"kind": "light_create", "target": None, "parameters": {"name": "Spot", "location": [0, 0, 4],
                                                             "settings": {"light_type": "SPOT"}}},
    {"kind": "uv_smart_project", "target": _P5 % 7, "parameters": {"uv_map": "Lightmap"}},
    {"kind": "material_assign", "target": _P5 % 7, "parameters": {"material": "Iron", "source": _P5 % 8}},
    {"kind": "mesh_bend", "target": _P5 % 7, "parameters": {"rise": 0.3, "axis": "X", "up": "Z", "segments": 8}},
]
# batch, exact in Claude's plan (plan_kinds.CLAUDE_REPEAT_ACTIONS): each placement.
REPEAT_OPERATIONS = [
    {"kind": "batch", "target": None, "parameters": {"sources": [_P5 % 7], "at": [[0, 0, 0], [2, 0, 0]]}},
    {"kind": "batch", "target": None, "parameters": {"sources": [_P5 % 7], "mirror": "X"}},
    {"kind": "batch", "target": None, "parameters": {"sources": [_P5 % 7, _P5 % 8], "linked": False,
                                                     "array": [{"step": [1.5, 0, 0], "count": 3}]}},
    {"kind": "batch", "target": None, "parameters": {"sources": [_P5 % 7], "along": {
        "points": [[0, 0, 0], [4, 0, 0]], "spacing": 1, "align": True}}},
    {"kind": "batch", "target": None, "parameters": {"sources": [_P5 % 7], "scatter": {
        "count": 5, "min": [0, 0, 0], "max": [4, 4, 0]}, "vary": {"seed": 3, "rotation": [[0, 0, 0], [0, 0, 1]]}}},
]


def encoded(value):
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode())


def redundancy_free(node):
    """Remove exactly what model_schema may remove, from either side."""
    if isinstance(node, list):
        return [redundancy_free(item) for item in node]
    if not isinstance(node, dict):
        return node
    result = {key: copy.deepcopy(value) if key in ("enum", "const", "default") else
              ({name: redundancy_free(child) for name, child in value.items()} if key == "properties" else redundancy_free(value))
              for key, value in node.items()}
    if result.get("required") == []:
        del result["required"]
    if result.get("type") == "string" and all(isinstance(item, str) for item in result.get("enum", [None])):
        del result["type"]
    if "default" in result and result["default"] is None:
        del result["default"]
    return result


def variants(schema):
    """``({tool: member}, deferred tool names)`` of a plan, whose tagged unions are if/then (tagged_unions)."""
    _tag, exact, deferred = members(expand_generated(schema)["properties"]["operations"]["items"])
    return exact, deferred


def arguments_of(schema, name):
    return variants(schema)[0][name]["properties"]["arguments"]


class PlanSchemaTests(unittest.TestCase):
    def test_every_tool_kind_and_check_is_exact_or_named_never_both(self):
        names = {tool["name"] for tool in TOOLS}
        self.assertTrue(DEFERRED_TOOLS < names)
        self.assertTrue(SCULPT_NAMES | STUDIO_NAMES <= DEFERRED_TOOLS)
        self.assertEqual(set(PLAN_CORE_ACTIONS) | set(DEFERRED_ACTIONS), set(OPERATION_SCHEMAS))
        self.assertFalse(set(PLAN_CORE_ACTIONS) & set(DEFERRED_ACTIONS))
        self.assertEqual(set(CLAUDE_CORE_ACTIONS) | set(CLAUDE_DEFERRED_ACTIONS), set(OPERATION_SCHEMAS))
        self.assertFalse(set(CLAUDE_CORE_ACTIONS) & set(CLAUDE_DEFERRED_ACTIONS))
        self.assertEqual([operation["kind"] for operation in EVERYDAY_OPERATIONS], list(CLAUDE_EVERYDAY_ACTIONS))
        # Large kinds stay deferred: their schemas cost more than the lookup round they save.
        for kind in ("shader_nodes", "geometry_nodes", "create_object", "node_edit", "operator_run"):
            self.assertIn(kind, CLAUDE_DEFERRED_ACTIONS)
        exact, deferred = variants(native_plan_schema())
        exact = list(exact)
        self.assertEqual(exact, [tool["name"] for tool in TOOLS if tool["name"] not in DEFERRED_TOOLS])
        self.assertEqual(deferred, [t["name"] for t in TOOLS if t["name"] in DEFERRED_TOOLS])
        for required in ("angelo_scene_program", "angelo_apply_recipe", "angelo_agent_capabilities", "angelo_scene_verify"):
            self.assertIn(required, exact)

    def test_model_projection_removes_only_validation_neutral_redundancy(self):
        schemas = [tool["inputSchema"] for tool in TOOLS] + list(OPERATION_SCHEMAS.values()) + [CHECK_SCHEMA]
        for schema in schemas:
            projected = model_schema(schema)
            self.assertEqual(redundancy_free(projected), redundancy_free(schema))
            self.assertLessEqual(encoded(projected), encoded(schema))
        self.assertEqual(model_schema({"type": "string", "enum": ["A", 1]}), {"type": "string", "enum": ["A", 1]})
        self.assertEqual(model_schema({"properties": {"type": {"const": "X"}, "default": {"type": "null", "default": None}},
                                       "required": []}),
                         {"properties": {"type": {"const": "X"}, "default": {"type": "null"}}})

    def test_core_kinds_and_checks_are_exact_and_deferred_ones_are_named(self):
        program = arguments_of(native_plan_schema(), "angelo_scene_program")
        operations = program["properties"]["operations"]["items"]
        # Without string length limits: the host enforces those (plan_schema.without_lengths). The union is
        # written as if/then (tagged_unions), which accepts the same operations.
        expected = without_lengths(model_schema(model_facing(plan_schema._operation_items(CLAUDE_CORE_ACTIONS))))
        self.assertEqual(operations, discriminated(expected))
        _tag, exact, deferred = members(operations)
        self.assertEqual(list(exact), [k for k in OPERATION_SCHEMAS if k in CLAUDE_CORE_ACTIONS])
        self.assertEqual(deferred, list(CLAUDE_DEFERRED_ACTIONS))
        self.assertEqual(operations["properties"]["parameters"], {"type": "object"})
        self.assertFalse(operations["additionalProperties"])
        checks = program["properties"]["expected_checks"]["items"]
        verify = arguments_of(native_plan_schema(), "angelo_scene_verify")["properties"]["checks"]["items"]
        self.assertEqual(checks, verify, "Programs and standalone verification share one check projection")
        core = [without_lengths(model_schema(v)) for v in CHECK_VARIANTS if plan_schema._core_check(v)]
        self.assertEqual(checks, discriminated(without_lengths(model_schema(plan_schema._checks_items()))))
        _tag, exact, deferred = members(checks)
        self.assertEqual(deferred, list(DEFERRED_CHECKS))
        self.assertEqual(members(exact["modifier"])[2], list(DEFERRED_MODIFIER_CHECKS))
        # Discriminators are disjoint, so oneOf still selects exactly one variant.
        kinds = [v["properties"]["kind"].get("const") for v in core]
        self.assertFalse(set(kinds) & set(DEFERRED_CHECKS))
        types = {v["properties"]["type"]["const"] for v in core if v["properties"]["kind"]["const"] == "modifier"}
        self.assertFalse(types & set(DEFERRED_MODIFIER_CHECKS))
        for tool in TOOLS:
            if tool["name"] not in DEFERRED_TOOLS | {"angelo_scene_program", "angelo_scene_verify"}:
                self.assertEqual(plan_arguments(tool), model_schema(model_facing(
                    tool["inputSchema"], INFERRED_FIELDS.get(tool["name"], ()))))

    def test_deferred_modifier_types_are_named_inside_the_core_modifier_kinds(self):
        from angelo_agent.action_schemas import MODIFIER_SETTINGS
        self.assertEqual(set(plan_schema.PLAN_CORE_MODIFIERS) | set(plan_schema.DEFERRED_MODIFIERS), set(MODIFIER_SETTINGS))
        for kind in plan_schema.MODIFIER_KINDS:
            variants = plan_schema.core_operation(kind)["properties"]["parameters"]["oneOf"]
            exact = [variant["properties"]["type"]["const"] for variant in variants[:-1]]
            self.assertEqual(exact, [name for name in MODIFIER_SETTINGS if name in plan_schema.PLAN_CORE_MODIFIERS])
            self.assertEqual(variants[-1]["properties"]["type"]["anyOf"][0], {"enum": list(plan_schema.DEFERRED_MODIFIERS)})
            self.assertEqual(variants[-1]["properties"]["type"]["anyOf"][1]["pattern"], "^[A-Z][A-Z_]{1,31}$")
        plan = [{"tool": "angelo_scene_program", "arguments": {"operations": [
            {"kind": "modifier_add", "parameters": {"type": "ARRAY"}}, {"kind": "modifier_add", "parameters": {"type": "BEVEL"}}]}}]
        self.assertEqual(deferred_references(plan), {"ACTIONS": ["modifier_add"]})
        exact = lookup("ACTIONS", ["modifier_add"])["actions"][0]["parameters"]
        self.assertIn("ARRAY", json.dumps(exact))
        self.assertIn("SHRINKWRAP", deferred_index()["deferred_modifier_types"]["types"])

    def test_allowed_subsets_are_never_broadened(self):
        collaborator = json.dumps(native_plan_schema(COLLABORATOR_TOOLS))
        for name in TOOL_MAP:
            if name not in COLLABORATOR_TOOLS:
                self.assertNotIn('"' + name + '"', collaborator)
        exact, deferred = variants(native_plan_schema({"angelo_sculpt_view", "angelo_scene_search"}))
        self.assertEqual(list(exact), ["angelo_scene_search"])
        self.assertEqual(deferred, ["angelo_sculpt_view"])
        self.assertEqual(list(variants(native_plan_schema({"angelo_scene_verify"}))[0]), ["angelo_scene_verify"])
        empty = native_plan_schema(frozenset())
        self.assertEqual(empty["properties"]["operations"]["maxItems"], 0)
        self.assertNotIn("$defs", empty)

    def test_plan_and_instruction_budgets(self):
        self.assertLessEqual(encoded(native_plan_schema()), PLAN_BUDGET_BYTES)
        self.assertLessEqual(len((CLAUDE_INSTRUCTIONS + CLAUDE_PLAN_INSTRUCTIONS).encode()), CLAUDE_INSTRUCTION_BUDGET_BYTES)

def assert_standards_vectors(test, validator):
    """Standards validation of the plan (run by the transport suite's jsonschema test)."""
    schema = native_plan_schema()
    validator.check_schema(schema)
    valid = validator(schema).is_valid
    cube = {"kind": "create_primitive", "target": None, "parameters": {"primitive": "CUBE", "alias": "cube", "size": 2}}
    light = {"kind": "constraint_add", "target": "o4", "parameters": {"guessed": True}}
    program = {"mode": "APPLY", "read_set": [], "write_set": [], "operations": [cube],
               "expected_checks": [{"kind": "dimensions", "target": "$cube", "expected": [2, 2, 2], "tolerance": 0.001},
                                   {"kind": "modifier", "target": "$cube", "name": "Bevel", "type": "BEVEL",
                                    "values": {"segments": 2}, "tolerance": 0}]}
    plan = lambda *operations: {"reply": "r", "verify": False, "operations": list(operations)}
    test.assertTrue(valid(plan({"tool": "angelo_scene_program", "arguments": program})))
    deferred = {"tool": "angelo_memory_update", "arguments": {"anything": 1}}
    test.assertTrue(valid(plan(deferred)), "Deferred arguments are checked by the host, not the plan schema")
    with test.assertRaises(ValueError):
        validate_call(deferred["tool"], deferred["arguments"])
    guessed = dict(program, operations=[cube, light], expected_checks=[{"kind": "aim", "made_up": 1}])
    test.assertTrue(valid(plan({"tool": "angelo_scene_program", "arguments": guessed})))
    with test.assertRaises(ValueError):
        validate_call("angelo_scene_program", guessed)
    invalid = [
        plan({"tool": "angelo_python_shell", "arguments": {}}),
        plan({"tool": "angelo_scene_program", "arguments": dict(program, operations=[dict(cube, kind="delete_all")])}),
        plan({"tool": "angelo_scene_program", "arguments": dict(program, expected_checks=[
            {"kind": "modifier", "target": "$cube", "name": "Bevel", "type": "BEVEL", "values": {"made_up": 1},
             "tolerance": 0}])}),
        plan({"tool": "angelo_scene_program", "arguments": dict(program, operations=[
            {"kind": "create_primitive", "target": None, "parameters": {"primitive": "CUBE", "size": -1}}])}),
        plan({"tool": "angelo_create_cube", "arguments": {"name": "Cube", "size": 1, "location": [0, 0, 0], "x": 1}}),
    ]
    for value in invalid:
        test.assertFalse(valid(value), value)
    # Every core modifier type, typed and generic, matches exactly one variant. A BEVEL
    # modifier_configure that matched both its typed variant and the generic stub made
    # Claude Code reject a valid plan.
    typed = {"BEVEL": {"width": 0.075, "segments": 3}, "SUBSURF": {"levels": 2}, "SOLIDIFY": {"thickness": 0.1},
             "MIRROR": {"axes": [True, False, False]}, "BOOLEAN": {"operand": "o2"}}
    for kind, settings in typed.items():
        for operation in ({"kind": "modifier_configure", "target": "o4",
                           "parameters": {"type": kind, "modifier": kind.title(), "settings": settings}},
                          {"kind": "modifier_add", "target": "o4", "parameters": {"type": kind, "settings": settings}}):
            test.assertTrue(valid(plan({"tool": "angelo_scene_program", "arguments": dict(program, operations=[operation])})),
                            operation)
    for kind in ("ARRAY", "WAVE"):  # a deferred typed type by name; any other type of the build
        operation = {"kind": "modifier_add", "target": "o4", "parameters": {"type": kind}}
        test.assertTrue(valid(plan({"tool": "angelo_scene_program", "arguments": dict(program, operations=[operation])})),
                        operation)
    # Phase 5's kinds and checks are valid on the host and match exactly one variant: of the plan (a deferred kind
    # its generic one) and of the complete check schema.
    for operation in PHASE5_OPERATIONS:
        validate_operation(copy.deepcopy(operation))
        test.assertTrue(valid(plan({"tool": "angelo_scene_program", "arguments": dict(program, operations=[operation])})),
                        operation)
    # mesh_bend is exact in Claude's plan, with its note (plan_kinds.CODEX_REFERENCE_ACTIONS says why): a guessed
    # field is refused by the plan schema itself, as by the host.
    guessed_bend = {"kind": "mesh_bend", "target": _P5 % 7, "parameters": {"rise": .3, "curve": "ARC"}}
    test.assertFalse(valid(plan({"tool": "angelo_scene_program", "arguments": dict(program, operations=[guessed_bend])})))
    with test.assertRaises(ValueError):
        validate_operation(copy.deepcopy(guessed_bend))
    # The everyday kinds and batch are exact in Claude's plan: valid on the host, one exact variant each, and
    # guessed parameters are rejected by the plan schema itself.
    for operation in EVERYDAY_OPERATIONS + REPEAT_OPERATIONS:
        validate_operation(copy.deepcopy(operation))
        test.assertTrue(valid(plan({"tool": "angelo_scene_program", "arguments": dict(program, operations=[operation])})),
                        operation)
        guessed_kind = dict(operation, parameters=dict(operation["parameters"], made_up=1))
        test.assertFalse(valid(plan({"tool": "angelo_scene_program", "arguments": dict(program, operations=[guessed_kind])})),
                         guessed_kind)
    validate_checks(copy.deepcopy(PHASE5_CHECKS))
    for check in PHASE5_CHECKS:
        test.assertTrue(valid(plan({"tool": "angelo_scene_program", "arguments": dict(program, expected_checks=[check])})),
                        check)
        test.assertTrue(validator(CHECK_SCHEMA).is_valid(check), check)
    collaborator = validator(native_plan_schema(COLLABORATOR_TOOLS)).is_valid
    test.assertFalse(collaborator(plan(deferred)), "A collaborator plan cannot name a mutation, even generically")
    test.assertTrue(collaborator(plan({"tool": "angelo_sculpt_view", "arguments": {}})))

    original = validator(CHECK_SCHEMA).is_valid
    program = arguments_of(native_plan_schema(), "angelo_scene_program")
    projected = validator(program["properties"]["expected_checks"]["items"]).is_valid
    vectors = [{"kind": "dimensions", "target": "$a", "expected": [1, 2, 3], "tolerance": 0.1},
               {"kind": "dimensions", "target": "$a", "expected": [1, 2], "tolerance": 0.1},
               {"kind": "object_type", "target": "$a", "expected": "MESH"},
               {"kind": "object_type", "target": "$a", "expected": "PYTHON"},
               {"kind": "modifier", "target": "$a", "name": "S", "type": "SUBSURF", "values": {"levels": 2}, "tolerance": 0},
               {"kind": "modifier", "target": "$a", "name": "S", "type": "SUBSURF", "values": {"levels": 9}, "tolerance": 0},
               {"kind": "modifier", "target": "$a", "name": "S", "type": "SUBSURF", "values": {}, "tolerance": 0},
               {"kind": "parent", "target": "$a", "expected": None},
               {"kind": "animation", "target": "$a", "property": "location",
                "samples": [{"frame": 1, "expected": [0, 0, 0]}], "tolerance": 0, "require_keyframes": True}]
    for vector in vectors:
        test.assertEqual(projected(vector), original(vector), vector)
    # A modifier check with no values is valid: the modifier is there, of its type, what a modifier_add stating no
    # setting wrote (kept settings).
    test.assertEqual([original(vector) for vector in vectors], [True, False, True, False, True, False, True, True, True])


class LookupTests(unittest.TestCase):
    def test_tool_lookup_returns_the_exact_schema_and_family_guidance(self):
        result = lookup("TOOLS", ["angelo_sculpt_batch", "angelo_studio_generate", "angelo_memory_update", "nope"])
        self.assertEqual(result["unknown_names"], ["nope"])
        for entry in result["tools"]:
            tool = TOOL_MAP[entry["name"]]
            self.assertEqual(expand_generated(entry["input_schema"]), model_schema(tool["inputSchema"]))
            self.assertEqual(entry["description"], tool["description"])
            self.assertEqual(entry["read_only"], tool["annotations"]["readOnlyHint"])
        self.assertEqual(result["guidance"], {"SCULPT": SCULPT_GUIDANCE, "STUDIO": STUDIO_GUIDANCE})
        self.assertNotIn("guidance", lookup("TOOLS", ["angelo_memory_update"]))

    def test_action_and_check_lookups_are_exact_and_bounded(self):
        actions = lookup("ACTIONS", ["light_create", "camera_create", "light_create"])["actions"]
        self.assertEqual([entry["kind"] for entry in actions], ["light_create", "camera_create"])
        self.assertEqual(expand_generated(actions[0]["parameters"]),
                         model_schema(OPERATION_SCHEMAS["light_create"]["properties"]["parameters"]))
        checks = lookup("CHECKS", ["modifier", "aim"])["checks"]
        self.assertEqual(len(checks), 1 + sum(v["properties"]["kind"]["const"] == "modifier" for v in CHECK_VARIANTS))
        one = lookup("CHECKS", ["modifier:ARRAY"])["checks"]
        self.assertEqual([c["properties"]["type"]["const"] for c in one], ["ARRAY"])
        self.assertEqual(lookup("CHECKS", ["modifier:NOPE"]), {"checks": [], "unknown_names": ["modifier:NOPE"]})
        # Every family takes names: SUMMARY finds a name in any family.
        summary = lookup("SUMMARY", ["angelo_view", "bevel_and_shade", "aim", "nope"])
        self.assertEqual([entry["name"] for entry in summary["tools"]], ["angelo_view"])
        self.assertEqual([entry["name"] for entry in summary["recipes"]], ["bevel-and-shade"])
        self.assertEqual(summary["unknown_names"], ["nope"])
        self.assertEqual([entry["name"] for entry in lookup("QUERIES", ["angelo_scene_search", "angelo_view"])["tools"]],
                         ["angelo_scene_search"], "QUERIES names scene query tools only")
        with self.assertRaises(ValueError):
            lookup("EVERYTHING", ["angelo_view"])

    def test_index_lists_every_deferred_item(self):
        index = deferred_index()
        self.assertEqual({entry["name"] for entry in index["deferred_tools"]}, DEFERRED_TOOLS)
        self.assertEqual(index["deferred_action_kinds"], list(DEFERRED_ACTIONS))
        self.assertEqual(len(index["deferred_check_kinds"]), len(DEFERRED_CHECKS) + len(DEFERRED_MODIFIER_CHECKS))

    def test_capabilities_names_are_closed_and_family_scoped(self):
        for family in ("SUMMARY", "ACTIONS", "QUERIES", "CHECKS", "RECIPES", "TOOLS"):
            value = {"family": family, "names": ["light_create", "modifier:ARRAY", "bevel-and-shade"]}
            self.assertEqual(validate_execution("angelo_agent_capabilities", value), value)
            self.assertEqual(validate_call("angelo_agent_capabilities", value), value)
        self.assertEqual(validate_execution("angelo_agent_capabilities", {"family": "TOOLS"}), {"family": "TOOLS"})
        for bad in ({"family": "EVERYTHING", "names": ["x"]}, {"family": "TOOLS", "names": []},
                    {"family": "TOOLS", "names": ["a"] * 2}, {"family": "TOOLS", "names": ["x"] * 33},
                    {"family": "TOOLS", "names": ["../etc"]}, {"family": "TOOLS", "names": "angelo_view"},
                    {"family": "TOOLS", "names": [True]}, {"family": "TOOLS", "names": ["a" * 49]}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_execution("angelo_agent_capabilities", bad)

    def test_references_ignore_malformed_untrusted_plans(self):
        operations = [None, "x", {"tool": 3}, {"tool": "angelo_sculpt_batch", "arguments": []},
                      {"tool": "angelo_scene_program", "arguments": {
                          "operations": [{"kind": "light_create"}, {"kind": "transform"}, {"kind": ["x"]}, 7],
                          "expected_checks": [{"kind": "aim"}, {"kind": "modifier", "type": "ARRAY"},
                                              {"kind": "modifier", "type": "BEVEL"}, {"kind": None}]}},
                      {"tool": "angelo_scene_verify", "arguments": {"checks": "all"}}]
        self.assertEqual(deferred_references(operations), {"TOOLS": ["angelo_sculpt_batch"], "ACTIONS": ["light_create"],
                                                           "CHECKS": ["aim", "modifier:ARRAY"]})
        self.assertEqual(deferred_references("not a list"), {})

    def test_rejection_prompt_names_problems_and_carries_needed_schemas(self):
        prompt = rejection_prompt(["operations[0] (angelo_memory_update): Missing tool argument"],
                                  [{"tool": "angelo_memory_update", "arguments": {}}])
        self.assertIn("nothing changed", prompt)
        self.assertIn("Missing tool argument", prompt)
        schemas = json.loads(prompt.rsplit("\n", 1)[1])
        self.assertEqual(schemas["TOOLS"]["tools"][0]["name"], "angelo_memory_update")
        self.assertNotIn("Exact schemas", rejection_prompt(["bad"], [{"tool": "angelo_view", "arguments": {}}]))
        # Invalid parameters need exact correction feedback even for a kind the static plan states.
        rejected = [{"tool": "angelo_scene_program", "arguments": {"operations": [
            {"kind": "light_create", "target": None, "parameters": {"made_up": 1}}]}}]
        self.assertEqual(deferred_references(rejected, CLAUDE_CORE_ACTIONS), {})
        self.assertEqual(deferred_references(rejected), {"ACTIONS": ["light_create"]})
        self.assertIn("Exact schemas", rejection_prompt(["bad"], rejected, CLAUDE_CORE_ACTIONS))
        self.assertNotIn("Exact schemas", staged_rejection_prompt("bad", rejected, CLAUDE_CORE_ACTIONS))
        self.assertIn("Exact schemas", staged_rejection_prompt("bad", rejected))


class CodexDeferralTests(unittest.TestCase):
    def test_codex_tools_defer_the_same_kinds_with_long_identifiers(self):
        from angelo_agent.plan_schema import codex_arguments
        from angelo_agent.tool_specs import dynamic_tools
        from angelo_agent.scene_spec import HANDLE as SCENE_HANDLE
        program = codex_arguments(TOOL_MAP["angelo_scene_program"])
        operations = program["properties"]["operations"]["items"]["oneOf"]
        self.assertEqual(operations[-1]["properties"]["kind"], {"enum": list(DEFERRED_ACTIONS)})
        self.assertEqual(len(operations) - 1, len(PLAN_CORE_ACTIONS))
        verify = codex_arguments(TOOL_MAP["angelo_scene_verify"])
        self.assertEqual(verify["properties"]["checks"]["items"], program["properties"]["expected_checks"]["items"])
        # Long identifier patterns stay: Codex has no short-identifier table.
        self.assertIn(SCENE_HANDLE["pattern"], json.dumps(verify))
        self.assertNotIn("^o[1-9]", json.dumps(dynamic_tools()))
        self.assertLess(encoded(dynamic_tools()), DECLARED_TOOLS_BUDGET_BYTES)

    def test_a_rejected_call_returns_the_deferred_schemas_it_used(self):
        from angelo_agent.plan_schema import deferred_schemas
        schemas = deferred_schemas("angelo_scene_program", {"operations": [
            {"kind": "property_set", "target": HANDLE, "parameters": {"values": {"bogus": 1}}},
            {"kind": "modifier_add", "target": HANDLE, "parameters": {"type": "ARRAY"}}],
            "expected_checks": [{"kind": "world_settings"}]})
        self.assertEqual([entry["kind"] for entry in schemas["ACTIONS"]["actions"]], ["modifier_add", "property_set"])
        self.assertEqual(schemas["CHECKS"]["checks"][0]["properties"]["kind"]["const"], "world_settings")
        self.assertEqual(deferred_schemas("angelo_scene_program", {"operations": "not a list"}), {})


class InstructionTests(unittest.TestCase):
    def test_codex_keeps_one_complete_policy_and_claude_defers_only_family_guidance(self):
        from angelo_agent import claude_text
        from angelo_agent.providers import CODEX_DEFERRED_GUIDANCE, CODEX_DEFERRED_INSTRUCTIONS
        self.assertEqual(INSTRUCTIONS, CORE_INSTRUCTIONS + SCULPT_GUIDANCE + VIEW_GUIDANCE + DIRECTOR_GUIDANCE + STUDIO_GUIDANCE
                         + FLOW_GUIDANCE + CODEX_DEFERRED_GUIDANCE)
        self.assertIn("angelo_agent_capabilities with family ACTIONS or CHECKS", CODEX_DEFERRED_GUIDANCE)
        # A Codex plan thread defers only the family guidance its deferred tools carry (codex_family_list).
        self.assertEqual(CODEX_DEFERRED_INSTRUCTIONS, CORE_INSTRUCTIONS + VIEW_GUIDANCE + DIRECTOR_GUIDANCE)
        # A Claude session reads the Claude per-model text (claude_text), its plan text included.
        self.assertEqual(CLAUDE_INSTRUCTIONS, claude_text.CLAUDE_BASE_INSTRUCTIONS)
        self.assertIn("angelo_agent_capabilities family TOOLS, ACTIONS or CHECKS", CLAUDE_PLAN_INSTRUCTIONS)
        self.assertIn("never use Python to bypass Studio approvals", CLAUDE_PLAN_INSTRUCTIONS)
        self.assertIn("never Python vertex loops", CLAUDE_PLAN_INSTRUCTIONS)


class RejectionLoopTests(unittest.TestCase):
    """The typed-plan loop with mocked rounds: rejected plans never reach Blender."""

    def provider(self, plans, results=(), allowed=None):
        context = host()
        context.allowed_tools = allowed
        context.request_observation = Mock(side_effect=lambda callback: callback({"context_version": 2, "objects": []}))
        provider = ClaudeProvider(context, {})
        provider._turn = Mock(side_effect=plans)
        provider._native_call = Mock(side_effect=list(results))
        return provider, context

    def test_a_rejected_plan_runs_nothing_and_returns_with_the_schemas_it_needed(self):
        bad = {"tool": "angelo_memory_update", "arguments": {"guessed": 1}}
        read = {"tool": "angelo_memory_read", "arguments": {}}
        plans = [{"reply": "Saving notes.", "operations": [read, bad], "verify": False},
                 {"reply": "Reading first.", "operations": [read], "verify": True},
                 {"reply": "Done.", "operations": [], "verify": False}]
        provider, context = self.provider(plans, [{"revision": 1, "memory": {}}])
        self.assertEqual(provider.run(None, "Save a note", observation={}), "completed")
        self.assertEqual(provider._native_call.call_args_list[0].args, ("angelo_memory_read", {}),
                         "The rejected plan's valid read did not run either")
        provider._native_call.assert_called_once()
        rejection = provider._turn.call_args_list[1].args[1]
        self.assertIn("operations[1] (angelo_memory_update)", rejection)
        self.assertIn('"name":"angelo_memory_update"', rejection)
        notices = [data["text"] for kind, data in context.events if kind == "message"]
        self.assertTrue(notices[0].startswith("Host rejected the plan before running any operation"))

    def test_repeated_rejections_fail_the_request_without_running_anything(self):
        bad = {"reply": "Try.", "operations": [{"tool": "angelo_studio_catalog", "arguments": {"x": 1}}], "verify": False}
        provider, _context = self.provider([copy.deepcopy(bad) for _ in range(3)])
        with self.assertRaisesRegex(ValueError, "Unexpected tool argument"):
            provider.run(None, "Generate", observation={})
        self.assertEqual(provider._turn.call_count, 3)
        provider._native_call.assert_not_called()

    def test_a_tool_outside_the_allowed_set_is_a_contract_breach_not_a_retry(self):
        plan = {"reply": "Edit.", "operations": [{"tool": "angelo_set_workspace_mode", "arguments": {"mode": "MANUAL"}}],
                "verify": False}
        provider, _context = self.provider([plan], allowed=COLLABORATOR_TOOLS)
        with self.assertRaisesRegex(RpcError, "does not allow"):
            provider.run(None, "Switch", observation={})
        provider._turn.assert_called_once()
        provider._native_call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
