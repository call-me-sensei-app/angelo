# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Codex typed plans (toolset 30, codex_plan): offline, no subprocess or inference.

The strict output schema against OpenAI's documented subset and limits, every
plan kind round-tripping through it, the host rejecting whatever the ordinary
schema rejects, the streamed reply, and a scripted app-server showing that a
pure edit completes in one model round while tool calls still run in-turn.
"""

import copy
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import codex_plan, strict_schema
from angelo_agent.codex_plan import (PLAN_GUIDANCE, PLAN_TOOLS, SHAPE, SUPERSEDED, ReplyStream, ordinary_schema,
                                     output_schema, parse_plan, plan_mode)
from angelo_agent.controller import Controller
from angelo_agent.identifiers import IdentifierTable, expand_call
from angelo_agent.plan_schema import (DEFERRED_ACTIONS, DEFERRED_TOOLS, PLAN_CORE_ACTIONS, PLAN_CORE_CHECKS,
                                      PlanRejected, _operation_items)
from angelo_agent.protocol import RpcError
from angelo_agent.providers import (CODEX_REPLY_GUIDANCE, CodexProvider, _plan_problems, codex_instructions)
from angelo_agent.strict_schema import JSON_TEXT, decode, encode, inline_references, strict_problems
from angelo_agent.schema_compaction import compact_schema
from angelo_agent.team_library_spec import TEAM_NAMES
from angelo_agent.tool_specs import COLLABORATOR_TOOLS, TOOL_MAP, validate_call
from strict_plan_vectors import GATE_OUTPUTS, NAMED, union_vectors
from test_native_round_savings import committed, program

try:
    import jsonschema
except ImportError:  # CI installs it (run_host_tests --max-skips 0).
    jsonschema = None

EPOCH = "a" * 32
HANDLE, REVISION = "scn:%s:159" % EPOCH, "b" * 64


def resolver():
    """The host's resolver for a context that observed o1 at REVISION."""
    table = IdentifierTable()
    shown = {"objects": [{"handle": HANDLE, "object_revision": REVISION}]}
    table.compact(shown)
    table.observe(shown)
    return lambda name, arguments: expand_call(name, arguments, table)


def plan_of(*operations, reply=""):
    return {"reply": reply, "operations": list(operations)}


def host_problems(plan):
    """What the host says about a decoded plan before running it (as codex_plan.finish)."""
    problems, _ = _plan_problems(plan["operations"], PLAN_TOOLS, resolver())
    return problems


@unittest.skipIf(jsonschema is None, "jsonschema is not installed")
class StrictSchemaContractTests(unittest.TestCase):
    def test_the_output_schema_fits_openais_strict_subset_and_limits(self):
        for allowed in (None, frozenset({"angelo_scene_program", "angelo_scene_search"}), frozenset(PLAN_TOOLS)):
            with self.subTest(allowed=allowed):
                schema = output_schema(allowed)
                self.assertEqual(strict_problems(schema), [])
                jsonschema.Draft202012Validator.check_schema(schema)
        text = json.dumps(output_schema())
        for keyword in ('"oneOf"', '"allOf"', '"not"', '"pattern"', '"format"', '"minLength"', '"default"',
                        '"uniqueItems"', '"minProperties"'):
            self.assertNotIn(keyword, text)

    def test_the_platform_api_accepts_every_thread_variant(self):
        """Regression: api.openai.com refused toolset 30's schema on every API-key Codex turn.

        "Invalid schema for response_format 'codex_output_schema': In
        context=('properties', 'tool'), schema must have a 'type' key." A ChatGPT
        sign-in had accepted it. Signed in to a team the plan also carries the
        team-library tools; signed out it does not.
        """
        from angelo_agent.team_library_spec import TEAM_NAMES
        from angelo_agent.tool_specs import TOOL_MAP
        for allowed in (None, frozenset(TOOL_MAP) - TEAM_NAMES):
            with self.subTest(team=allowed is None):
                schema = output_schema(allowed)
                self.assertEqual(strict_problems(schema), [])

                def walk(node, path):
                    if isinstance(node, dict):
                        self.assertTrue({"type", "anyOf", "$ref"} & set(node), path)
                        if "const" in node or "enum" in node:
                            self.assertIn("type", node, path)
                        for key, child in node.items():
                            if key in ("properties", "$defs"):
                                for name, grandchild in child.items():
                                    walk(grandchild, path + "." + name)
                            elif key in ("items",):
                                walk(child, path + "[]")
                            elif key == "anyOf":
                                for index, member in enumerate(child):
                                    walk(member, "%s|%d" % (path, index))
                walk(schema, "root")

    def test_the_validator_names_the_platform_apis_complaint(self):
        untyped = {"type": "object", "additionalProperties": False, "required": ["tool"],
                   "properties": {"tool": {"const": "angelo_create_cube"}}}
        self.assertIn("root.tool: schema must have a 'type' key", strict_problems(untyped))
        misplaced = {"type": "object", "additionalProperties": False, "required": ["n"],
                     "properties": {"n": {"type": "string", "minimum": 1}}}
        self.assertTrue(any("do not apply to type string" in problem for problem in strict_problems(misplaced)))
        mistyped = {"type": "object", "additionalProperties": False, "required": ["n"],
                    "properties": {"n": {"type": "string", "enum": ["a", 1]}}}
        self.assertTrue(any("is not of type string" in problem for problem in strict_problems(mistyped)))

    def test_consts_and_enums_carry_their_type(self):
        def strict_of(node):
            return strict_schema.strict_schema({"type": "object", "additionalProperties": False, "required": ["x"],
                                                "properties": {"x": node}})["properties"]["x"]
        self.assertEqual(strict_of({"const": "BEVEL"}), {"type": "string", "const": "BEVEL"})
        self.assertEqual(strict_of({"const": True}), {"type": "boolean", "const": True})
        self.assertEqual(strict_of({"enum": [1, 2]}), {"type": "integer", "enum": [1, 2]})
        self.assertEqual(strict_of({"enum": [0, 0.5]}), {"type": "number", "enum": [0, 0.5]})
        self.assertEqual(strict_of({"enum": ["A", None]}), {"anyOf": [{"type": "string", "enum": ["A"]},
                                                                       {"type": "null"}]})
        self.assertEqual(strict_of({"enum": ["A", 2]}), {"anyOf": [{"type": "string", "enum": ["A"]},
                                                                    {"type": "integer", "enum": [2]}]})

    def test_bytes_are_stable_across_turns_and_rebuilds(self):
        first = json.dumps(output_schema(), separators=(",", ":"))
        codex_plan._documents.cache_clear()
        self.assertEqual(json.dumps(output_schema(), separators=(",", ":")), first)
        self.assertIs(output_schema(), output_schema(), "every turn of a thread sends the same object")

    def test_size_stays_within_its_budget(self):
        """Every request of a plan thread carries it (cached after the first): growth is a decision.

        34,900 -> 34,910 (measured 34,861 -> 34,910): bevel-and-shade's apply_scale (+49 B; its note
        travels in the recipe tool's declaration, which the strict variant leaves out).
        34,910 -> 34,860 (measured): pointers show the handle alone (stored_forms: a grammar refuses
        nothing, so {"object": ...} would cost 259 B a turn) and no check requires a tolerance (-50 B).
        34,860 -> 35,280 (measured 34,860 -> 35,277): keyframe_insert's parameters are a union by
        channel, a scale key bounded as a transform's scale (+417 B); simple-animation keys location and rotation.
        35,280 -> 36,990 (measured 35,277 -> 36,984): short forms (strict_shapes) for the three recipes
        the gates call, which may end after mode and stop their parameters where the recorded calls did
        (+1,500 B), and a transform that may stop after its scale (+207 B). Re-encoded, recorded gate
        plans write 42 fewer o200k tokens per fit-dimensions call, 22 per material-setup and 18 per
        bevel-and-shade (doc/angelo/performance.rst, "Plan output tokens").
        36,990 -> 37,000 (measured 36,984 -> 36,996): mesh_bend's name among
        the kinds the format does not spell out (+12 B); its parameters travel as JSON text, as the Codex program
        reference states them.
        """
        self.assertLessEqual(len(json.dumps(output_schema(), separators=(",", ":"))), 37000)

    def test_only_plan_tools_and_no_reads_python_or_files(self):
        tools = set()
        for member in output_schema()["properties"]["operations"]["items"]["anyOf"]:
            tool = member if "$ref" not in member else output_schema()["$defs"][member["$ref"].split("/")[-1]]
            spec = tool["properties"]["tool"]
            tools.update([spec["const"]] if "const" in spec else spec["enum"])
        self.assertEqual(tools, set(PLAN_TOOLS))
        for name in ("angelo_execute_python", "angelo_write_file", "angelo_scene_search", "angelo_view"):
            self.assertNotIn(name, tools)
        self.assertEqual(output_schema()["required"], ["reply", "operations"])

    def test_unconstrained_positions_have_no_strict_form(self):
        with self.assertRaises(ValueError):
            strict_schema.strict_schema({"type": "object", "properties": {"a": {}}, "required": ["a"],
                                         "additionalProperties": False})

    def test_open_maps_and_deep_objects_become_json_text(self):
        schema = {"type": "object", "additionalProperties": False, "required": ["map", "deep"], "properties": {
            "map": {"type": "object", "additionalProperties": {"type": "number"}},
            "deep": {"type": "object", "additionalProperties": False, "required": ["inner"],
                     "properties": {"inner": {"type": "object", "additionalProperties": False,
                                              "properties": {"x": {"type": "number"}}}}}}}
        strict = strict_schema.strict_schema(schema, max_depth=2)
        self.assertEqual(strict["properties"]["map"], JSON_TEXT)
        self.assertEqual(strict["properties"]["deep"]["properties"]["inner"], JSON_TEXT)
        value = {"map": {"a": 1}, "deep": {"inner": {"x": 2}}}
        self.assertEqual(decode({"map": '{"a":1}', "deep": {"inner": '{"x":2}'}}, schema), value)
        with self.assertRaisesRegex(strict_schema.StrictDecodeError, "map: the JSON text must hold an object"):
            decode({"map": "[1]", "deep": {"inner": "{}"}}, schema)

    def test_the_checker_names_what_leaves_the_subset(self):
        problems = strict_problems({"type": "object", "properties": {
            "a": {"oneOf": [{"type": "string"}]}, "b": {"type": "object", "properties": {}},
            "c": {"enum": list(range(1001))}}, "required": ["a", "b"], "additionalProperties": False})
        text = "\n".join(problems)
        self.assertIn("unsupported keywords ['oneOf']", text)
        self.assertIn("root: objects need additionalProperties false", text)
        self.assertIn("root.b: objects need additionalProperties false", text)
        self.assertIn("1001 enum values exceed 1000", text)


@unittest.skipIf(jsonschema is None, "jsonschema is not installed")
class RoundTripTests(unittest.TestCase):
    """Standards vectors: a JSON Schema 2020-12 validator, not our own code, judges both schemas."""

    @classmethod
    def setUpClass(cls):
        cls.ordinary = ordinary_schema()
        cls.ordinary_validator = jsonschema.Draft202012Validator(cls.ordinary)
        cls.strict_validator = jsonschema.Draft202012Validator(output_schema())

    def round_trip(self, plan):
        self.assertEqual(list(self.ordinary_validator.iter_errors(plan)), [], "a valid ordinary plan")
        written = encode(plan, self.ordinary)
        errors = [error.message[:300] for error in self.strict_validator.iter_errors(written)]
        self.assertEqual(errors, [], "the strict variant accepts it")
        # Omitted and null optional fields are the same plan (test_optional_nulls_mean_omitted).
        self.assertEqual(decode(written, self.ordinary), decode(plan, self.ordinary))
        return written

    def test_every_union_member_round_trips(self):
        vectors = union_vectors(self.ordinary)
        text = json.dumps(vectors)
        for label, plan in vectors:
            with self.subTest(vector=label):
                self.round_trip(plan)
        for name in PLAN_TOOLS - DEFERRED_TOOLS:
            self.assertIn('"%s"' % name, text)
        self.assertTrue(any('"%s"' % name in text for name in PLAN_TOOLS & DEFERRED_TOOLS), "the deferred tools' variant")
        for kind in PLAN_CORE_ACTIONS + PLAN_CORE_CHECKS + (DEFERRED_ACTIONS[0],):
            self.assertIn('"kind": "%s"' % kind, text)
        for recipe in ("bevel-and-shade", "fit-dimensions", "clean-mesh", "material-setup", "simple-animation",
                       "uv-prep"):
            self.assertIn('"recipe": "%s"' % recipe, text)
        for primitive in ("CUBE", "UV_SPHERE", "CYLINDER", "CIRCLE"):
            self.assertIn('"primitive": "%s"' % primitive, text)

    def test_named_standards_vectors(self):
        for name, operation in NAMED.items():
            with self.subTest(vector=name):
                written = self.round_trip(plan_of(operation))
                self.assertEqual(host_problems(decode(written, self.ordinary)), [], "the host accepts it")

    def test_a_typed_bevel_is_not_the_generic_modifier_variant(self):
        """Regression: inside a oneOf a typed BEVEL configure matched the generic stub too, so Claude Code refused it."""
        operation = NAMED["typed BEVEL configure (the 98/120 overlap)"]["arguments"]["operations"][0]
        ordinary_items = inline_references({"oneOf": _operation_items()["oneOf"]})
        matches = [index for index, variant in enumerate(ordinary_items["oneOf"])
                   if jsonschema.Draft202012Validator(variant).is_valid(operation)]
        self.assertEqual(len(matches), 1, "exactly one ordinary variant")
        written = encode(plan_of(NAMED["typed BEVEL configure (the 98/120 overlap)"]), self.ordinary)
        settings = written["operations"][0]["arguments"]["operations"][0]["parameters"]["settings"]
        # The typed variant's own short form (strict_shapes): the settings the vector does not state are left out.
        self.assertEqual(settings, {"width": 0.05, "segments": 3})

    def test_json_text_carries_deferred_items(self):
        written = encode(plan_of(NAMED["deferred kind with parameters"], NAMED["deferred check"],
                                 NAMED["deferred tool"]), self.ordinary)
        self.assertEqual(written["operations"][0]["arguments"]["operations"][0]["parameters"], '{"name":"Box"}')
        self.assertEqual(json.loads(written["operations"][1]["arguments"]["expected_checks"][0]),
                         {"kind": "object_exists", "target": "o1", "expected": True})
        self.assertEqual(json.loads(written["operations"][2]["arguments"]),
                         {"expected_revision": 0, "patch": {"objective": "Keep the cube centred."}})

    def test_optional_nulls_mean_omitted(self):
        """Decoding drops an optional property's null: the host must read both forms as the same call.

        Every union vector the host accepts is validated twice: with the
        model's explicit nulls kept (the strict output, JSON text parsed) and
        with them omitted (decode). The runtime validators must agree exactly.
        """
        resolve, compared = resolver(), 0
        for label, plan in union_vectors(self.ordinary):
            written = encode(plan, self.ordinary)
            for index, operation in enumerate(decode(written, self.ordinary)["operations"]):
                try:
                    omitted = validate_call(operation["tool"], resolve(operation["tool"], operation["arguments"]))
                except (ValueError, RpcError):
                    continue  # A generated value the host refuses for its own reasons (tested elsewhere).
                explicit = _parse_json_text(written["operations"][index], operation)
                with self.subTest(vector=label, operation=index):
                    self.assertEqual(validate_call(explicit["tool"], resolve(explicit["tool"], explicit["arguments"])),
                                     omitted)
                compared += 1
        self.assertGreater(compared, 40)


def _parse_json_text(written, decoded):
    """The model's strict output with its nulls kept, and JSON text where the decoded form has an object.

    A property that only null fits is not written at all (a creating operation's target): the decoded form's null
    is restored here, as ``decode`` restores it.
    """
    if isinstance(written, str) and isinstance(decoded, dict):
        return json.loads(written)
    if isinstance(written, dict) and isinstance(decoded, dict):
        kept = {key: _parse_json_text(value, decoded.get(key)) for key, value in written.items()}
        return dict(kept, **{key: None for key, value in decoded.items() if value is None and key not in written})
    if isinstance(written, list) and isinstance(decoded, list):
        return [_parse_json_text(value, other) for value, other in zip(written, decoded)]
    return written


@unittest.skipIf(jsonschema is None, "jsonschema is not installed")
def _resolved(schema, node):
    while isinstance(node, dict) and "$ref" in node:
        node = schema["$defs"][node["$ref"].split("/")[-1]]
    return node


def _members(schema, node):
    """The object members of a strict union (or the object itself)."""
    node = _resolved(schema, node)
    return [_resolved(schema, member) for member in node.get("anyOf", [node])
            if _resolved(schema, member).get("type") == "object"]


def _admitting(members, keys):
    """Members whose property order begins with ``keys``: what a strict decoder lets a model continue."""
    return [member for member in members if list(member["properties"])[:len(keys)] == list(keys)]


@unittest.skipIf(jsonschema is None, "jsonschema is not installed")
class PropertyOrderTests(unittest.TestCase):
    """OpenAI writes a strict output's properties in schema order: a member must be named before it is filled.

    A schema sent with every object's keys sorted (compact_schema sorts by
    default) puts ``arguments`` before ``tool`` and makes a program's first key
    ``expected_checks``: every program a model began with "mode" could then only
    continue as a recipe (GATE_OUTPUTS).
    """

    @classmethod
    def setUpClass(cls):
        cls.schema = output_schema()
        cls.gate_schema = compact_schema(strict_schema.strict_schema(ordinary_schema()), min_shared_bytes=40)

    def operation_members(self, schema):
        return _members(schema, schema["properties"]["operations"]["items"])

    def arguments_of(self, schema, tool):
        member = next(m for m in self.operation_members(schema) if m["properties"]["tool"].get("const") == tool)
        return _members(schema, member["properties"]["arguments"])

    def test_the_document_order_reaches_the_model_with_discriminators_first(self):
        schema = self.schema
        self.assertEqual(list(schema["properties"]), ["reply", "operations"], "the reply streams first")
        for member in self.operation_members(schema):
            self.assertEqual(list(member["properties"])[0], "tool")
        # A program may stop after its operations or its checks; the bindings the host infers come last.
        *shorter, program = self.arguments_of(schema, "angelo_scene_program")
        self.assertEqual(list(program["properties"]),
                         ["mode", "operations", "expected_checks", "recovery_observation", "read_set", "write_set"])
        self.assertEqual([list(member["properties"]) for member in shorter],
                         [["mode", "operations"], ["mode", "operations", "expected_checks"]])
        for member in self.arguments_of(schema, "angelo_apply_recipe"):
            self.assertEqual(list(member["properties"])[0], "recipe")
        operations = _members(schema, _resolved(schema, program["properties"]["operations"])["items"])
        for operation in operations:
            self.assertEqual(list(operation["properties"])[0], "kind")
            for parameters in _members(schema, operation["properties"]["parameters"]):
                discriminators = [name for name, child in parameters["properties"].items() if "const" in child]
                self.assertEqual(list(parameters["properties"])[:len(discriminators)], discriminators,
                                 "primitive and modifier type come first")
        checks_array = next(member for member in _resolved(schema, program["properties"]["expected_checks"])["anyOf"]
                            if _resolved(schema, member).get("type") == "array")
        checks = _members(schema, _resolved(schema, checks_array)["items"])
        self.assertTrue(all(list(check["properties"])[0] == "kind" for check in checks))

    def test_every_union_member_names_itself_before_its_fields(self):
        def walk(node, seen):
            node = _resolved(self.schema, node)
            if not isinstance(node, dict) or id(node) in seen:
                return
            seen.add(id(node))
            for member in node.get("anyOf", ()):
                member = _resolved(self.schema, member)
                properties = member.get("properties") or {}
                names = list(properties)
                consts = [name for name in names if "const" in _resolved(self.schema, properties[name])]
                self.assertEqual(names[:len(consts)], consts)
                walk(member, seen)
            for child in (node.get("properties") or {}).values():
                walk(child, seen)
            walk(node.get("items"), seen)
        walk(self.schema, set())

    def test_the_gate_outputs_were_forced_by_the_sorted_order(self):
        for model, request, text in GATE_OUTPUTS:
            with self.subTest(model=model, request=request[:40]):
                written = json.loads(text)
                arguments = list(written["operations"][0]["arguments"])
                self.assertEqual(arguments[:2], ["mode", "parameters"], "the model began with mode")
                # Then: operations sorted, arguments before tool, a program's first key expected_checks.
                members = self.operation_members(self.gate_schema)
                self.assertTrue(all(list(member["properties"])[0] == "arguments" for member in members))
                admitted = {member["properties"]["tool"].get("const", "deferred") for member in members
                            if _admitting(_members(self.gate_schema, member["properties"]["arguments"]), arguments[:2])}
                self.assertEqual(admitted, {"angelo_apply_recipe"}, "only a recipe could follow mode, parameters")
                # Now: the tool comes first, and a program begins with mode.
                programs = self.arguments_of(self.schema, "angelo_scene_program")
                self.assertEqual(_admitting(programs, ["mode"]), programs)

    def test_the_decoder_and_host_run_exactly_what_the_model_wrote(self):
        """The gate's wrong recipes were the model's literal output, not a branch the decoder chose."""
        resolve = _resolver(4)
        for model, request, text in GATE_OUTPUTS:
            with self.subTest(model=model, request=request[:40]):
                plan, problem = parse_plan(text)
                self.assertIsNone(problem)
                literal = json.loads(text)["operations"][0]
                self.assertEqual(plan["operations"][0]["tool"], literal["tool"])
                self.assertEqual(plan["operations"][0]["arguments"]["recipe"], literal["arguments"]["recipe"])
                self.assertEqual(plan["operations"][0]["arguments"]["parameters"],
                                 {key: value for key, value in literal["arguments"]["parameters"].items()
                                  if value is not None})
                problems, operations = _plan_problems(plan["operations"], PLAN_TOOLS, resolve)
                self.assertEqual(problems, [])
                self.assertEqual(operations[0]["arguments"]["recipe"], literal["arguments"]["recipe"])


def _table(objects):
    """An identifier table that showed o1..o<objects> at REVISION."""
    table = IdentifierTable()
    shown = {"objects": [{"handle": "scn:%s:%d" % (EPOCH, 150 + index), "object_revision": REVISION}
                         for index in range(objects)]}
    table.compact(shown)
    table.observe(shown)
    return table


def _resolver(objects):
    """The host's resolver for a context that observed o1..o<objects> at REVISION."""
    table = _table(objects)
    return lambda name, arguments: expand_call(name, arguments, table)


class CompactionOrderTests(unittest.TestCase):
    def test_order_is_preserved_only_on_request_and_constraints_never_change(self):
        schema = {"type": "object", "properties": {
            "tool": {"const": "x"}, "arguments": {"type": "object", "properties": {"b": {"type": "number"},
                                                                                "a": {"type": "number"}}}},
            "required": ["tool", "arguments"]}
        sorted_ = compact_schema(schema, min_shared_bytes=32)
        kept = compact_schema(schema, min_shared_bytes=32, preserve_order=True)
        self.assertEqual(list(sorted_["properties"]), ["arguments", "tool"], "the default sorts, as before")
        self.assertEqual(list(kept["properties"]), ["tool", "arguments"])
        self.assertEqual(list(kept["properties"]["arguments"]["properties"]), ["b", "a"])
        self.assertEqual(json.dumps(kept, sort_keys=True), json.dumps(sorted_, sort_keys=True))


@unittest.skipIf(jsonschema is None, "jsonschema is not installed")
class HostRejectionTests(unittest.TestCase):
    """Whatever the ordinary plan schema rejects, the strict schema or the host rejects: nothing widens.

    Mutations apply to what the model writes (the strict form). Those the
    strict variant still accepts (it drops patterns, string lengths, large
    array bounds and oneOf exclusivity, and carries deferred items as JSON
    text) must be refused by the host's exact validation before anything runs.
    """

    @classmethod
    def setUpClass(cls):
        cls.ordinary = ordinary_schema()
        cls.ordinary_validator = jsonschema.Draft202012Validator(cls.ordinary)
        cls.strict_validator = jsonschema.Draft202012Validator(output_schema())

    def host_rejects(self, written):
        try:
            return bool(host_problems(decode(written, self.ordinary)))
        except (strict_schema.StrictDecodeError, RpcError):
            return True

    def assert_never_runs(self, written, *, strict_accepts=None, ordinary_rejects=False):
        if ordinary_rejects:
            try:
                valid = self.ordinary_validator.is_valid(decode(written, self.ordinary))
            except strict_schema.StrictDecodeError:
                valid = False
            self.assertFalse(valid, "a real negative: the ordinary schema rejects it")
        accepted = self.strict_validator.is_valid(written)
        if strict_accepts is not None:
            self.assertEqual(accepted, strict_accepts)
        if accepted:
            self.assertTrue(self.host_rejects(written), "the host must refuse what the strict schema let through")

    def written(self, operation):
        return encode(plan_of(operation), self.ordinary)

    def test_structural_mutations_are_rejected_by_the_strict_schema(self):
        for name, operation in NAMED.items():
            written = self.written(operation)
            arguments = written["operations"][0]["arguments"]
            if not isinstance(arguments, dict):
                continue
            mutations = {"extra argument": dict(arguments, surprise=1)}
            for field in list(arguments)[:3]:
                if operation["tool"] == "angelo_scene_program" and field == "expected_checks":
                    continue  # Not a mutation: a program may stop after its operations (strict_shapes).
                mutations["missing " + field] = {key: value for key, value in arguments.items() if key != field}
            if operation["tool"] == "angelo_scene_program":
                mutations["unknown kind"] = copy.deepcopy(arguments)
                mutations["unknown kind"]["operations"][0]["kind"] = "teleport"
                mutations["bad mode"] = dict(arguments, mode="MAYBE")
            for label, mutated in mutations.items():
                with self.subTest(vector=name, mutation=label):
                    variant = copy.deepcopy(written)
                    variant["operations"][0]["arguments"] = mutated
                    self.assert_never_runs(variant, strict_accepts=False)

    def test_bounds_the_strict_schema_keeps_are_rejected_by_it(self):
        recipe = self.written(NAMED["fit-dimensions recipe"])
        recipe["operations"][0]["arguments"]["parameters"]["maximum_scale_ratio"] = 1000
        material = self.written(NAMED["material"])
        material["operations"][0]["arguments"]["operations"][0]["parameters"]["inputs"]["Roughness"] = 2
        bevel = self.written(NAMED["typed BEVEL configure (the 98/120 overlap)"])
        bevel["operations"][0]["arguments"]["operations"][0]["parameters"]["settings"]["segments"] = 99
        for written in (recipe, material, bevel):
            self.assert_never_runs(written, strict_accepts=False)
            self.assertTrue(self.host_rejects(written))

    def test_what_only_the_host_checks_is_rejected_by_it(self):
        def program(edit):
            written = self.written(NAMED["create a cube and check it"])
            edit(written["operations"][0]["arguments"])
            return written
        cases = {
            "a name for a target": program(lambda a: a["expected_checks"][0].update(target="Eval Cube")),
            "an empty object name": program(lambda a: a["operations"][0]["parameters"].update(name="")),
            "an alias with spaces": program(lambda a: a["operations"][0]["parameters"].update(alias="my box")),
            "65 program operations": program(lambda a: a.update(operations=a["operations"] * 65)),
        }
        deferred = self.written(NAMED["deferred kind with parameters"])
        deferred["operations"][0]["arguments"]["operations"][0]["parameters"] = '{"name": 7}'
        cases["a deferred kind's wrong parameters"] = deferred
        broken = copy.deepcopy(deferred)
        broken["operations"][0]["arguments"]["operations"][0]["parameters"] = "not json"
        cases["JSON text that is not JSON"] = broken
        check = self.written(NAMED["deferred check"])
        check["operations"][0]["arguments"]["expected_checks"][0] = '{"kind": "object_exists", "target": "o1"}'
        cases["a deferred check missing a field"] = check
        tool = self.written(NAMED["deferred tool"])
        tool["operations"][0]["arguments"] = '{"expected_revision": 0, "patch": {"objective": 7}}'
        cases["a deferred tool's wrong arguments"] = tool
        for label, written in cases.items():
            with self.subTest(case=label):
                self.assert_never_runs(written, strict_accepts=True)

    def test_every_kind_bound_to_a_name_instead_of_a_handle_is_rejected(self):
        """A pattern the strict variant drops, across every union vector that has a target."""
        checked = 0
        for label, plan in union_vectors(self.ordinary):
            written = encode(plan, self.ordinary)
            text = json.dumps(written)
            if '"target": "o1"' not in text:
                continue
            renamed = json.loads(text.replace('"target": "o1"', '"target": "Eval Cube"'))
            with self.subTest(vector=label):
                self.assert_never_runs(renamed, strict_accepts=True, ordinary_rejects=True)
            checked += 1
        self.assertGreater(checked, 20)

    def test_tools_outside_the_plan_never_run(self):
        """Reviewed Python and file effects run from exec only."""
        for tool, arguments in (("angelo_execute_python", {"description": "x", "code": "pass"}),
                                ("angelo_write_file", {"path": "notes.txt", "content": "x"})):
            with self.subTest(tool=tool):
                plan = {"reply": "", "operations": [{"tool": tool, "arguments": arguments}]}
                self.assertFalse(self.strict_validator.is_valid(plan))
                with self.assertRaises(RpcError):
                    host_problems(decode(plan, self.ordinary))


class ParsePlanTests(unittest.TestCase):
    def test_plain_text_is_a_plain_answer(self):
        self.assertEqual(parse_plan("The cube is 2 m wide."), ({"reply": "The cube is 2 m wide.", "operations": []}, None))
        self.assertEqual(parse_plan('"quoted"')[0]["reply"], '"quoted"')

    def test_a_json_object_must_be_a_plan(self):
        for text in ('{"reply": "x"}', '{"reply": 1, "operations": []}', '{"reply": "", "operations": {}}',
                     json.dumps({"reply": "", "operations": [{}] * 65})):
            self.assertEqual(parse_plan(text), (None, SHAPE))

    def test_bad_json_text_names_the_field(self):
        written = encode(plan_of(NAMED["deferred kind with parameters"]), ordinary_schema())
        written["operations"][0]["arguments"]["operations"][0]["parameters"] = "{oops"
        plan, problem = parse_plan(json.dumps(written))
        self.assertIsNone(plan)
        self.assertIn("operations[0].arguments.operations[0].parameters: invalid JSON text", problem)

    def test_only_the_last_message_runs(self):
        turns = codex_plan.PlanTurns()
        edit = json.dumps(encode(plan_of(NAMED["generic REMESH add"]), ordinary_schema()))
        turns.message_completed("a", edit)
        turns.message_completed("b", '{"reply": "Done.", "operations": []}')
        self.assertEqual(turns.final_plan(), ("b", None, SUPERSEDED))
        turns.begin_turn()
        turns.message_completed("a", '{"reply": "Looking.", "operations": []}')
        turns.message_completed("b", edit)
        item, plan, problem = turns.final_plan()
        self.assertEqual((item, problem, len(plan["operations"])), ("b", None, 1))


class ReplyStreamTests(unittest.TestCase):
    def feed(self, *deltas):
        stream = ReplyStream()
        return [stream.feed(delta) for delta in deltas]

    def test_the_reply_streams_decoded(self):
        parts = self.feed('{"re', 'ply":"Caf', '\\u00e9 \\"', 'ok\\"\\n', '\\ud83d', '\\ude00 done', '","operations":[{')
        self.assertEqual("".join(parts), 'Café "ok"\n😀 done')
        self.assertEqual(parts[-1], "")

    def test_plain_text_streams_as_it_is_and_json_never_does(self):
        self.assertEqual(self.feed("Hel", "lo"), ["Hel", "lo"])
        self.assertEqual(self.feed('{"operations":[', '],"reply":"late"}'), ["", ""])
        self.assertEqual(self.feed("  ", '{"reply": "', "x"), ["", "", "x"])


class PlanModeTests(unittest.TestCase):
    def context(self, **fields):
        return type("Context", (), dict({"voice": None, "allowed_tools": None}, **fields))()

    def test_openai_chat_contexts_only(self):
        self.assertTrue(plan_mode(self.context(), external=False))
        self.assertFalse(plan_mode(self.context(), external=True))
        self.assertFalse(plan_mode(self.context(voice=object()), external=False))
        self.assertFalse(plan_mode(self.context(base_instructions="Prompt editor"), external=False))
        self.assertFalse(plan_mode(self.context(allowed_tools=COLLABORATOR_TOOLS), external=False))
        self.assertFalse(plan_mode(self.context(allowed_tools=frozenset()), external=False))

    def test_recipes_are_plan_tools_and_in_turn_tools(self):
        """The first gates' recipe failures were the schema's key order, not recipes (PropertyOrderTests)."""
        from angelo_agent.tool_specs import codex_tools
        self.assertIn("angelo_apply_recipe", PLAN_TOOLS)
        self.assertIn("angelo_apply_recipe", {tool["name"] for tool in codex_tools(short_identifiers=True)})

    def test_instructions(self):
        plain = codex_instructions(short_identifiers=True, deferred=True)
        planned = codex_instructions(short_identifiers=True, deferred=True, plan=True)
        self.assertIn(CODEX_REPLY_GUIDANCE, plain)
        self.assertNotIn(PLAN_GUIDANCE, plain)
        self.assertIn(PLAN_GUIDANCE, planned)
        self.assertNotIn(CODEX_REPLY_GUIDANCE, planned)
        self.assertEqual(planned, codex_instructions(short_identifiers=True, deferred=True, plan=True))


class ScriptedAppServer:
    """codex app-server's side of a turn: each turn/start plays one scripted turn.

    A step is ("message", text), ("tool", tool, arguments) or ("call", function). Events reach the
    provider from another thread once it has bound the turn, as the RPC
    reader delivers them after the turn/start response.
    """

    def __init__(self, provider, turns):
        self.provider, self.turns = provider, list(turns)
        self.requests, self.responses = [], []
        self.closed = threading.Event()

    def request(self, method, parameters, **kwargs):
        self.requests.append((method, parameters))
        if method != "turn/start":
            return {}
        turn = "turn-%d" % len(self.requests)
        threading.Thread(target=self.play, args=(parameters["threadId"], turn, self.turns.pop(0)), daemon=True).start()
        return {"turn": {"id": turn}}

    def play(self, thread, turn, steps):
        deadline = time.monotonic() + 10
        while self.provider.turn_id != turn and time.monotonic() < deadline:
            time.sleep(0.001)
        ids = {"threadId": thread, "turnId": turn}
        for index, step in enumerate(steps):
            if step[0] == "message":
                item = "msg-%s-%d" % (turn, index)
                for start in range(0, len(step[1]), 7):
                    self.provider._event("item/agentMessage/delta", dict(ids, itemId=item, delta=step[1][start:start + 7]))
                self.provider._event("item/completed", dict(ids, item={"type": "agentMessage", "id": item,
                                                                         "text": step[1], "phase": None}))
            elif step[0] == "call":
                step[1]()
            elif step[0] == "tool":
                self.provider._request("call-%d" % index, "item/tool/call", dict(
                    ids, callId="call-%d" % index, tool=step[1], arguments=step[2]))
        self.provider._event("turn/completed", {"threadId": thread, "turn": {"id": turn, "status": "completed"}})

    def respond(self, identity, payload):
        self.responses.append((identity, payload))

    def close(self):
        pass


class PlanTurnFixture(unittest.TestCase):
    """A real Controller and CodexProvider over ScriptedAppServer; host dispatch is ``request_tool``."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.c = Controller(self.temp.name)
        self.c.set_document(("factory-runtime", 17))
        self.c.state["native_id"] = "native-thread"
        self.c._begin_metrics()
        self.c.busy, self.c.cancelled = True, False
        self.context = self.c.runtime_context()
        self.p = CodexProvider(self.context, {})
        self.p.thread_id = "native-thread"
        self.p.bind_request(self.context)
        self.dispatched, self.results = [], {}
        self.context.request_tool = self.request_tool

    def tearDown(self):
        self.p.close()
        self.c.close()
        self.temp.cleanup()

    def request_tool(self, call_id, name, arguments, callback, *, delivery_owner=None):
        self.dispatched.append((call_id, name, arguments))
        results = self.results.get(name, [{"success": True, "objects": []}])
        from program_completion_fixtures import register_completion
        result = results.pop(0) if len(results) > 1 else results[0]
        register_completion(self.context, name, arguments, result)
        callback(result)

    def run_turns(self, *turns):
        self.p.rpc = ScriptedAppServer(self.p, turns)
        status = self.p.run("native-thread", "Make a 2 m cube")
        self.c.pump_events()
        return status

    def plan_text(self, *operations, reply=""):
        return json.dumps(encode(plan_of(*operations, reply=reply), ordinary_schema()))

    def starts(self):
        return [parameters for method, parameters in self.p.rpc.requests if method == "turn/start"]


class CodexPlanTurnTests(PlanTurnFixture):
    def test_a_pure_edit_completes_in_one_model_round(self):
        self.results["angelo_scene_program"] = [committed()]
        self.assertEqual(self.run_turns([("message", self.plan_text(program()))]), "completed")
        (start,) = self.starts()
        # Signed out (no team account in host tests): every tool but the team family.
        self.assertIs(start["outputSchema"], output_schema(frozenset(TOOL_MAP) - TEAM_NAMES))
        self.assertEqual([name for _, name, _ in self.dispatched], ["angelo_scene_program"])
        self.assertTrue(self.dispatched[0][0].startswith("codex-plan/"))
        rows = self.c.state["messages"]
        self.assertFalse([row for row in rows if row["role"] == "assistant"], "no reply row, and no raw JSON")
        (summary,) = [row for row in rows if row.get("host_summary")]
        self.assertTrue(summary["text"].startswith("Host-verified result (no further model round was needed)"))

    def test_a_reply_streams_and_shows_once(self):
        self.results["angelo_scene_program"] = [committed()]
        self.run_turns([("message", self.plan_text(program(), reply="I kept it centred."))])
        rows = [row for row in self.c.state["messages"] if row["role"] == "assistant" or row.get("host_summary")]
        self.assertEqual(rows[0]["text"], "I kept it centred.")
        self.assertTrue(rows[1]["host_summary"], "as after a Claude plan, the host's account follows the reply")
        self.assertEqual(len(rows), 2)

    def test_tool_calls_still_run_within_the_turn(self):
        self.results["angelo_scene_program"] = [committed()]
        self.run_turns([("tool", "angelo_scene_search", {"query": "Cube"}),
                        ("tool", "angelo_agent_capabilities", {"family": "ACTIONS", "names": ["mesh_subdivide"]}),
                        ("message", self.plan_text(program()))])
        self.assertEqual([name for _, name, _ in self.dispatched],
                         ["angelo_scene_search", "angelo_agent_capabilities", "angelo_scene_program"])
        self.assertEqual(len(self.p.rpc.responses), 2, "both in-turn calls were answered")
        self.assertEqual(len(self.starts()), 1)

    def test_an_edit_already_run_in_turn_is_not_run_again(self):
        self.results["angelo_scene_program"] = [committed()]
        self.run_turns([("tool", "angelo_scene_program", program()["arguments"]),
                        ("message", self.plan_text(program()))])
        self.assertEqual([name for _, name, _ in self.dispatched], ["angelo_scene_program"])
        self.assertTrue([row for row in self.c.state["messages"] if row.get("host_summary")])

    def test_a_repeat_is_found_whatever_revision_the_host_bound(self):
        """An in-turn commit moves the observed revision; the repeat would bind the new one and apply twice."""
        first = copy.deepcopy(program())
        first["arguments"].update(read_set=[{"handle": HANDLE, "revision": REVISION}], write_set=[])
        again = copy.deepcopy(program())
        again["arguments"].update(read_set=[{"handle": HANDLE, "revision": "c" * 64}], write_set=[])
        self.assertTrue(codex_plan._repeats(again, [(first, committed())]))
        self.assertFalse(codex_plan._repeats(again, [(first, {"error": "stage failed"})]), "a failed run may be retried")
        moved = copy.deepcopy(again)
        moved["arguments"]["operations"][0]["parameters"]["location"] = [1, 0, 0]
        self.assertFalse(codex_plan._repeats(moved, [(first, committed())]))

    def test_a_rejected_plan_comes_back_once_with_the_same_schema(self):
        bad = copy.deepcopy(NAMED["deferred kind with parameters"])
        bad["arguments"]["operations"][0].update(target=HANDLE, parameters={"name": 7})
        self.results["angelo_scene_program"] = [committed()]
        status = self.run_turns([("message", self.plan_text(bad))], [("message", self.plan_text(program()))])
        self.assertEqual(status, "completed")
        first, second = self.starts()
        self.assertIs(second["outputSchema"], first["outputSchema"])
        self.assertIn("The host rejected your last plan", second["input"][0]["text"])
        # Invalid parameters get exact feedback even when their kind appears in the static reference.
        self.assertIn("Exact schemas", second["input"][0]["text"])
        self.assertIn('"object_rename"', second["input"][0]["text"])
        self.assertEqual(len(self.dispatched), 1)

    def test_a_rejected_kind_outside_the_reference_brings_its_exact_schema(self):
        bad = copy.deepcopy(NAMED["deferred kind with parameters"])
        bad["arguments"]["operations"][0].update(kind="shape_key_add", target=HANDLE, parameters={"name": 7})
        self.results["angelo_scene_program"] = [committed()]
        self.assertEqual(self.run_turns([("message", self.plan_text(bad))], [("message", self.plan_text(program()))]),
                         "completed")
        self.assertIn('"shape_key_add"', self.starts()[1]["input"][0]["text"], "a kind read on demand: its exact schema")

    def test_a_program_that_failed_on_its_stage_comes_back_for_a_fix(self):
        failed = {"error_code": "VALIDATION", "error": "Modifier 'Bevel' not found", "scene_changed": False, "partial_changes_possible": False,
                  "mutation_started": False}
        self.results["angelo_scene_program"] = [failed, committed()]
        status = self.run_turns([("message", self.plan_text(program()))], [("message", self.plan_text(program()))])
        self.assertEqual(status, "completed")
        self.assertIn("The host stopped your last plan", self.starts()[1]["input"][0]["text"])
        self.assertEqual(len(self.dispatched), 2)

    def test_the_gate_outputs_replay_to_exactly_their_literal_calls(self):
        """Executor replay of GATE_OUTPUTS: one turn, one dispatch, the recipe the model named, nothing else."""
        self.c._identifiers = _table(4)
        for model, request, text in GATE_OUTPUTS:
            with self.subTest(model=model, request=request[:40]):
                self.dispatched.clear()
                self.results["angelo_apply_recipe"] = [committed()]
                self.assertEqual(self.run_turns([("message", text)]), "completed")
                literal = json.loads(text)["operations"][0]["arguments"]
                ((call_id, tool, arguments),) = self.dispatched
                self.assertEqual((tool, arguments["recipe"], arguments["mode"]),
                                 ("angelo_apply_recipe", literal["recipe"], literal["mode"]))
                self.assertEqual(arguments["target"], "scn:%s:%d" % (EPOCH, 149 + int(literal["target"][1:])))
                self.assertEqual(len(self.starts()), 1)

    def test_a_stop_as_the_turn_ends_runs_nothing(self):
        def stop():
            self.c.cancelled = True
        status = self.run_turns([("message", self.plan_text(program())), ("call", stop)])
        self.assertEqual(status, "interrupted")
        self.assertFalse(self.dispatched)

    def test_three_rejections_fail_the_request(self):
        bad = json.dumps({"reply": "", "operations": {}})
        with self.assertRaisesRegex(PlanRejected, "Host rejected the plan"):
            self.run_turns([("message", bad)], [("message", bad)], [("message", bad)])
        self.assertEqual(len(self.starts()), 3)
        self.assertFalse(self.dispatched)

    def test_a_plain_answer_ends_the_turn(self):
        self.assertEqual(self.run_turns([("message", '{"reply": "It is 2 m wide.", "operations": []}')]), "completed")
        self.assertFalse(self.dispatched)
        self.assertEqual([row["text"] for row in self.c.state["messages"] if row["role"] == "assistant"],
                         ["It is 2 m wide."])

    def test_non_plan_threads_send_no_schema(self):
        self.context.allowed_tools = COLLABORATOR_TOOLS
        self.run_turns([("message", "Advice only.")])
        (start,) = self.starts()
        self.assertNotIn("outputSchema", start)
        self.assertEqual([row["text"] for row in self.c.state["messages"] if row["role"] == "assistant"], ["Advice only."])


if __name__ == "__main__":
    unittest.main()
