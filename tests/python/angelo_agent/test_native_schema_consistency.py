# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Models refer to objects, data and values the same way in every schema they write against.

The lint behind doc/angelo/agent_execution.rst, "Referring to objects, data and
values". It walks every generated schema a model reads, or a validator checks a
model's output against:

* Claude's typed-plan schema, which Claude Code validates every plan against
  before the host sees it (a mismatch costs a round and an error of over 20,000
  characters whose cause the model often never sees);
* a Codex plan thread's strict response format and the schema its output is
  decoded with;
* the tool declarations of Codex chat threads (both connection kinds) and of
  voice threads;
* every deferred schema a lookup returns (action kinds, check kinds, tools);
* the raw tool schemas the host validates calls against;

and asserts one convention for each concept: object pointers, an operation's
own target, open settings maps, data-block names and IDs, colours and vectors,
enum spelling, optional check tolerances and the deferred variants. It caught
an earlier build's scene camera (property_set's ``camera`` took only the bare
handle while every other pointer of its map took ``{"object": handle}``);
``test_the_pre_fix_camera_is_caught`` keeps that schema as a fixture.

Needs jsonschema (CI installs it); dependency-free otherwise. Whether a model
writes these forms is a live question these tests do not answer.
"""

import copy
import math
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import codex_plan, plan_schema  # noqa: E402
from angelo_agent.action_catalog import validate_operation  # noqa: E402
from angelo_agent.action_schema_types import HANDLE, _validate  # noqa: E402
from angelo_agent.action_schemas import ACTION_SCHEMAS  # noqa: E402
from angelo_agent.anim_structure_spec import ACTION_ID_PATTERN  # noqa: E402
from angelo_agent.action_world_spec import IMAGE_ID_PATTERN  # noqa: E402
from angelo_agent.identifiers import SHORT_HANDLE_SCHEMA  # noqa: E402
from angelo_agent.program_spec import validate_program  # noqa: E402
from angelo_agent.tool_specs import TOOLS, codex_tools, dynamic_tools, native_plan_schema  # noqa: E402
from angelo_agent.validation_spec import CHECK_VARIANTS, TARGET, validate_checks, validate_value  # noqa: E402
from angelo_agent.execution_spec import validate_execution  # noqa: E402
from test_native_coverage_identifiers import OBJ, OTHER, OTHER_REV, REV, SCENE, SCENE_REV  # noqa: E402

try:
    from jsonschema import Draft202012Validator
except ImportError:
    Draft202012Validator = None

BARE = {"short": "o5", "long": "scn:%s:5" % ("a" * 32), "alias": "$cam"}
WRAPPED = {name + "{}": {"object": value} for name, value in BARE.items()}
FORMS = {**BARE, **WRAPPED, "null": None}
POINTER_FORMS = frozenset(BARE) | frozenset(WRAPPED)
# The program fields that name another object (action and check schemas); the walk must find each.
POINTER_FIELDS = frozenset({"parent", "mirror_object", "operand", "object", "target", "source", "sources", "also",
                            "armature", "camera", "expected"})
# Enums spelled other than as Blender identifiers (UPPER_CASE), each for its own reason.
SPELLINGS = {
    "property": "RNA property names (location, rotation_euler, scale)",
    "interpolation": "a node's own RNA items where Blender spells them so (TEX_IMAGE: Linear, Closest...)",
    "op": "node_edit's step verbs (add, link...), also accepted in upper case",
    "recipe": "recipe names (bevel-and-shade...)",
    "unit": "length units as people write them (m, cm, in...)",
    **{name: "the backend's own vocabulary (Media Studio, team library)"
       for name in ("aspect", "background", "quality", "kind", "purposes", "space")}}
# Colours Blender stores without alpha: Light.color and World.color (and the checks of those settings).
RGB_OWNERS = frozenset({"light_create", "light_configure", "light_settings", "world_configure", "world_settings"})


def resolve(node, root):
    while isinstance(node, dict) and isinstance(node.get("$ref"), str):
        node = root["$defs"][node["$ref"].rsplit("/", 1)[1]]
    return node


def members(node, root):
    node = resolve(node, root)
    union = node.get("oneOf") or node.get("anyOf") if isinstance(node, dict) else None
    return [resolve(member, root) for member in union] if union else [node]


def is_wrapper(node, root):
    node = resolve(node, root)
    return (isinstance(node, dict) and node.get("type") == "object"
            and set(node.get("properties") or ()) == {"object"})


def is_handle_string(node, root):
    """A string type of an object reference: a handle pattern (the long form matches it) or the short one."""
    node = resolve(node, root)
    pattern = node.get("pattern") if isinstance(node, dict) and node.get("type") == "string" else None
    return isinstance(pattern, str) and (re.search(pattern, BARE["long"]) is not None
                                         or pattern == SHORT_HANDLE_SCHEMA["pattern"])


def references_an_object(node, root):
    return any(is_handle_string(member, root) for member in members(node, root))


def names_objects_as_values(node, root):
    """An open value (property_set's values, a node socket's default): {"object": ...} among JSON values."""
    found = members(node, root)
    return any(is_wrapper(member, root) for member in found) and not references_an_object(node, root)


def sites(document, root=None, *, check=False, operation=False, tool_root=True, path=""):
    """``(path, class, schema, owner kind)`` of every object reference in a schema.

    Classes: ``subject`` (an operation's or check's own target, a binding's
    handle, a tool argument), ``pointer`` (a field naming another object),
    ``map`` (the value schema of an open settings map that names objects) and
    ``check_target_type`` (a check field other than target typed TARGET: a
    pointer must use POINTER instead). Walks ``$ref`` from the root.
    """
    root = document if root is None else root
    node = resolve(document, root)
    if not isinstance(node, dict):
        return
    for key in ("oneOf", "anyOf", "allOf"):
        for index, member in enumerate(node.get(key) or ()):
            yield from sites(member, root, check=check, operation=operation, tool_root=tool_root,
                             path="%s/%s%d" % (path, key, index))
    if isinstance(node.get("items"), dict):
        yield from sites(node["items"], root, check=check, operation=operation, tool_root=False, path=path + "[]")
    properties = node.get("properties") if isinstance(node.get("properties"), dict) else {}
    extra = node.get("additionalProperties")
    if isinstance(extra, dict) and names_objects_as_values(extra, root):
        yield path + "/*", "map", extra, None
    kind = resolve(properties.get("kind"), root) if "kind" in properties else None
    owner = kind.get("const") if isinstance(kind, dict) else None
    for name, child in properties.items():
        child_path = "%s/%s" % (path, name)
        if name == "object" and set(properties) == {"object"}:
            continue  # The inside of a pointer's {"object": ...} form.
        listed = resolve(child, root)
        listed = listed.get("items") if isinstance(listed, dict) and listed.get("type") == "array" else None
        if isinstance(listed, dict) and references_an_object(listed, root):  # A list of objects (sources, also).
            child, child_path = listed, child_path + "[]"
        if references_an_object(child, root):
            subject = (tool_root or (name in ("target", "targets") and "kind" in properties)
                       or (name == "handle" and "revision" in properties))
            yield child_path, "subject" if subject else "pointer", child, owner
        elif names_objects_as_values(child, root):
            yield child_path, "map", child, owner
        elif check and name != "target" and any(member == TARGET for member in members(child, root)):
            yield child_path, "check_target_type", child, owner
        if name in ("expected_checks", "checks"):
            yield from sites(child, root, check=True, tool_root=False, path=child_path)
        elif name == "operations" and not operation:
            yield from sites(child, root, operation=True, tool_root=False, path=child_path)
        elif name == "arguments" and "tool" in properties:
            yield from sites(child, root, tool_root=True, path=child_path)
        else:
            yield from sites(child, root, check=check, operation=operation, tool_root=False, path=child_path)


def accepted(schema, root):
    """The names of the FORMS a schema accepts (jsonschema, with the document's $defs)."""
    probe = dict(schema, **{"$defs": root.get("$defs", {})}) if "$defs" in root else schema
    validator = Draft202012Validator(probe)
    return frozenset(name for name, value in FORMS.items() if validator.is_valid(value))


def documents():
    """Every model-facing and host-validated schema document, by name."""
    yield "claude plan", native_plan_schema(), {}
    yield "codex response format (decoded)", codex_plan.ordinary_schema(), {"stored": True}
    for label, tools in (("voice tools", dynamic_tools()),
                         ("codex tools (function calling)", codex_tools(short_identifiers=False, defer=False)),
                         ("codex tools (OpenAI)", codex_tools(short_identifiers=True, defer=True))):
        for tool in tools:
            for declared in tool.get("tools", [tool]):  # The deferred namespace holds its tools.
                yield "%s: %s" % (label, declared["name"]), declared["inputSchema"], {}
    names = list(ACTION_SCHEMAS)
    for start in range(0, len(names), plan_schema.MAX_LOOKUP_NAMES):
        for action in plan_schema.lookup("ACTIONS", names[start:start + plan_schema.MAX_LOOKUP_NAMES])["actions"]:
            yield "lookup ACTIONS %s" % action["kind"], action["parameters"], {"tool_root": False}
    for variant in plan_schema.lookup("CHECKS", sorted({v["properties"]["kind"]["const"] for v in CHECK_VARIANTS}))["checks"]:
        yield "lookup CHECKS %s" % variant["properties"]["kind"]["const"], variant, {"tool_root": False, "check": True}
    for tool in plan_schema.lookup("TOOLS", sorted(plan_schema.DEFERRED_TOOLS))["tools"]:
        yield "lookup TOOLS %s" % tool["name"], tool["input_schema"], {}
    for tool in TOOLS:
        yield "host: %s" % tool["name"], tool["inputSchema"], {"host": True}


def pointer_problems(name, document, *, stored=False, tool_root=True, check=False, host=False):
    """Every object-reference site of ``document`` that breaks the convention, as text."""
    problems = []
    for path, kind, schema, _owner in sites(document, tool_root=tool_root, check=check):
        forms = accepted(schema, document) - {"null"}
        where = "%s %s" % (name, path)
        if kind == "check_target_type":
            problems.append("%s: a check field naming an object is a POINTER, not TARGET" % where)
        elif kind == "map":
            if not WRAPPED.keys() <= forms:
                problems.append("%s: an open map's values name objects as {\"object\": handle}" % where)
        elif kind == "subject" and forms & WRAPPED.keys():
            problems.append("%s: a target takes the handle, never {\"object\": ...}" % where)
        elif kind == "pointer" and forms != (frozenset(BARE) if stored else POINTER_FORMS):
            wanted = "the handle only (a strict grammar)" if stored else "the handle, $alias or {\"object\": handle}"
            problems.append("%s: a pointer takes %s; this takes %s" % (where, wanted, sorted(forms)))
    problems += typed_key_problems(name, document)
    return problems


def typed_key_problems(name, document, root=None, path=""):
    """A typed key of an open map (property_set's camera) accepts every object form its map's values accept."""
    root = document if root is None else root
    node, problems = resolve(document, root), []
    if not isinstance(node, dict):
        return problems
    extra, properties = node.get("additionalProperties"), node.get("properties")
    if isinstance(extra, dict) and isinstance(properties, dict):
        wanted = accepted(extra, root) & WRAPPED.keys()
        for key, child in properties.items():
            if references_an_object(child, root) and not wanted <= accepted(child, root):
                problems.append("%s %s/%s: takes %s, but every other object in its map takes %s" % (
                    name, path, key, sorted(accepted(child, root) - {"null"}), sorted(wanted)))
    for key, value in node.items():
        if key in ("properties", "$defs") and isinstance(value, dict):
            for child_name, child in value.items():
                if key == "properties":
                    problems += typed_key_problems(name, child, root, "%s/%s" % (path, child_name))
        elif key in ("items", "additionalProperties") and isinstance(value, dict):
            problems += typed_key_problems(name, value, root, path + ("[]" if key == "items" else "/*"))
        elif key in ("oneOf", "anyOf", "allOf") and isinstance(value, list):
            for index, member in enumerate(value):
                problems += typed_key_problems(name, member, root, "%s/%s%d" % (path, key, index))
    return problems


# One quantity, one bound, wherever a model writes it: a key's or an animation's value is bounded as its channel is
# written by a transform. Rotation keys are the exception: a spin of several turns keys past 2 pi (action_schemas).
CHANNEL_BOUNDS = {"location": 100000, "rotation_euler": 100000, "scale": 1000}


def channel_bound_problems(name, node, root=None, path=""):
    """Every value vector beside a ``property`` naming channels whose bound is not each channel's own."""
    root = node if root is None else root
    node, problems = resolve(node, root), []
    if not isinstance(node, dict):
        return problems
    properties = node.get("properties") if isinstance(node.get("properties"), dict) else {}
    channel = resolve(properties.get("property"), root) if "property" in properties else None
    named = (([channel["const"]] if "const" in channel else channel.get("enum") or [])
             if isinstance(channel, dict) else [])
    if named and set(named) <= set(CHANNEL_BOUNDS):
        for field, child in properties.items():
            if field == "expected":
                continue  # A check states an expected value; operations bound what they write.
            for member in members(child, root):
                items = resolve(member.get("items"), root) if member.get("type") == "array" else None
                if isinstance(items, dict) and items.get("type") == "number" and member.get("maxItems") == 3:
                    for channel_name in named:
                        if (items.get("minimum"), items.get("maximum")) != (-CHANNEL_BOUNDS[channel_name],
                                                                            CHANNEL_BOUNDS[channel_name]):
                            problems.append("%s %s/%s: a %s value takes %s..%s as its channel does, not %s..%s" % (
                                name, path, field, channel_name, -CHANNEL_BOUNDS[channel_name],
                                CHANNEL_BOUNDS[channel_name], items.get("minimum"), items.get("maximum")))
    for key, value in node.items():
        if key == "properties" and isinstance(value, dict):
            for child_name, child in value.items():
                problems += channel_bound_problems(name, child, root, "%s/%s" % (path, child_name))
        elif key in ("items", "additionalProperties") and isinstance(value, dict):
            problems += channel_bound_problems(name, value, root, path + ("[]" if key == "items" else "/*"))
        elif key in ("oneOf", "anyOf", "allOf") and isinstance(value, list):
            for index, member in enumerate(value):
                problems += channel_bound_problems(name, member, root, "%s/%s%d" % (path, key, index))
    return problems


def raw_program():
    return next(tool for tool in TOOLS if tool["name"] == "angelo_scene_program")["inputSchema"]


def named(document, pattern, root=None, path="", owner=None):
    """``(path, schema, owner kind)`` of every property whose name matches ``pattern``."""
    root = document if root is None else root
    node = resolve(document, root)
    if not isinstance(node, dict):
        return
    properties = node.get("properties") if isinstance(node.get("properties"), dict) else {}
    kind = resolve(properties.get("kind"), root) if "kind" in properties else None
    owner = kind.get("const", owner) if isinstance(kind, dict) else owner
    for name, child in properties.items():
        if re.fullmatch(pattern, name):
            yield "%s/%s" % (path, name), child, owner
        yield from named(child, pattern, root, "%s/%s" % (path, name), owner)
    for key in ("oneOf", "anyOf", "allOf"):
        for index, member in enumerate(node.get(key) or ()):
            yield from named(member, pattern, root, "%s/%s%d" % (path, key, index), owner)
    for key in ("items", "additionalProperties"):
        if isinstance(node.get(key), dict):
            yield from named(node[key], pattern, root, path + "/" + key, owner)


def strings(schema):
    """The non-null members of a field's schema, without annotations."""
    return [{key: value for key, value in member.items() if key not in ("default", "description")}
            for member in (schema.get("oneOf") or schema.get("anyOf") or [schema]) if member != {"type": "null"}]


@unittest.skipIf(Draft202012Validator is None, "jsonschema is not installed")
class ObjectReferenceTests(unittest.TestCase):
    def test_every_generated_schema_refers_to_objects_one_way(self):
        problems, documents_seen, pointers_seen = [], 0, 0
        for name, document, options in documents():
            documents_seen += 1
            pointers_seen += sum(kind == "pointer" for _p, kind, _s, _o in sites(
                document, tool_root=options.get("tool_root", True), check=options.get("check", False)))
            problems += pointer_problems(name, document, stored=options.get("stored", False),
                                         tool_root=options.get("tool_root", True), check=options.get("check", False))
        self.assertEqual(problems, [])
        self.assertGreater(documents_seen, 150)
        found = {path.rsplit("/", 1)[1].replace("[]", "") for path, kind, _s, _o in sites(raw_program(), tool_root=False)
                 if kind == "pointer"}
        self.assertEqual(found, POINTER_FIELDS, "the walk finds every pointer field it checks")

    def test_the_pre_fix_camera_is_caught(self):
        """An earlier build: the camera's value schema took only the bare handle."""
        before = copy.deepcopy(raw_program())
        values = before["properties"]["operations"]["items"]["oneOf"]
        values = next(v for v in values if v["properties"]["kind"]["const"] == "property_set")
        values = values["properties"]["parameters"]["properties"]["values"]
        values["properties"]["camera"] = {"oneOf": [HANDLE, {"type": "null"}]}
        problems = pointer_problems("fixture", before)
        self.assertTrue(any("/values/camera" in problem and "a pointer takes" in problem for problem in problems))
        self.assertTrue(any("/values/camera" in problem and "every other object in its map" in problem
                            for problem in problems), problems)
        self.assertEqual(pointer_problems("current", raw_program()), [])

    def test_a_strict_grammar_shows_only_the_handle(self):
        """Constrained decoding writes nothing its grammar lacks, so no form is refused there (stored_forms)."""
        strict = codex_plan.output_schema()
        wrappers = [path for path, schema, _owner in named(strict, r".+") if any(
            is_wrapper(member, strict) for member in members(schema, strict))]
        self.assertEqual(wrappers, [])
        decoded = codex_plan.ordinary_schema()
        self.assertFalse([path for path, kind, schema, _o in sites(decoded) if kind == "pointer"
                          and accepted(schema, decoded) & WRAPPED.keys()])

    def test_the_host_accepts_exactly_what_the_schema_does_and_stores_the_handle(self):
        """Forward and reverse: _validate/validate_value against jsonschema on each raw pointer site."""
        program = raw_program()
        checked = 0
        for path, kind, schema, _owner in sites(program, tool_root=False):
            if kind != "pointer":
                continue
            validator = validate_value if "/expected_checks" in path else _validate
            for name, value in FORMS.items():
                expected = name in accepted(schema, program)
                try:
                    stored, host = validator(copy.deepcopy(value), schema, "x"), True
                except ValueError:
                    stored, host = None, False
                with self.subTest(path=path, form=name):
                    self.assertEqual(host, expected)
                    if host and value is not None:
                        self.assertEqual(stored, value["object"] if isinstance(value, dict) else value)
                checked += 1
        self.assertGreater(checked, 150)

    def test_pointer_schemas_are_one_structural_kind(self):
        from angelo_agent.action_schema_types import NULLABLE_POINTER, POINTER, pointer_union
        self.assertTrue(pointer_union(POINTER["oneOf"]))
        self.assertTrue(pointer_union(NULLABLE_POINTER["oneOf"]))
        from angelo_agent.action_properties import VALUE
        self.assertFalse(pointer_union(VALUE["oneOf"]), "an open map keeps {\"object\": ...}: RNA types its values")


class AdvertisedFormTests(unittest.TestCase):
    """What a schema offers, its validator takes: a capability lookup's schema offered names with every family
    while the host took them with three, so valid-looking lookups were refused."""

    def test_capability_lookups_take_names_in_every_family(self):
        from angelo_agent.execution_spec import CAPABILITY_FAMILIES, EXECUTION_TOOLS
        from angelo_agent.action_recipes import RECIPE_SCHEMAS
        schema = next(tool for tool in EXECUTION_TOOLS if tool["name"] == "angelo_agent_capabilities")["inputSchema"]
        self.assertEqual(schema["properties"]["family"]["enum"], list(CAPABILITY_FAMILIES))
        samples = {"SUMMARY": "angelo_view", "ACTIONS": "light_create", "QUERIES": "angelo_scene_search",
                   "CHECKS": "modifier:ARRAY", "RECIPES": "bevel-and-shade", "TOOLS": "angelo_view"}
        for family in CAPABILITY_FAMILIES:
            with self.subTest(family=family):
                arguments = validate_execution("angelo_agent_capabilities", {"family": family, "names": [samples[family]]})
                self.assertNotIn("unknown_names", plan_schema.lookup(family, arguments["names"]))
        for recipe in RECIPE_SCHEMAS:  # Every recipe id is a name the schema's pattern takes.
            self.assertRegex(recipe, schema["properties"]["names"]["items"]["pattern"])

    def test_maps_that_never_hold_objects_do_not_offer_them(self):
        """Node sockets and settings and F-Curve modifier settings refuse objects; their schema says so."""
        from angelo_agent.action_properties import SCALAR_VALUE, VALUE
        for kind, path in (("node_edit", ("edits", "items", "properties", "settings")),
                           ("fcurve_modifier", ("settings",))):
            node = ACTION_SCHEMAS[kind]["properties"]
            for key in path:
                node = node[key]
            self.assertEqual(node["additionalProperties"], SCALAR_VALUE, kind)
        self.assertNotEqual(SCALAR_VALUE, VALUE)
        with self.assertRaisesRegex(ValueError, "NO_POINTERS"):
            validate_operation({"kind": "fcurve_modifier", "target": OBJ, "parameters": {
                "property": "location", "type": "NOISE", "settings": {"strength": {"object": OTHER}}}})

    def test_a_recipe_expands_whatever_its_schema_states(self):
        """simple-animation's values took 100,000 while the key it expands to stopped rotations at 2 pi."""
        from angelo_agent.action_recipes import expand_recipe
        expanded = expand_recipe("simple-animation", OBJ, {"property": "rotation_euler", "start_frame": 1,
                                                          "end_frame": 48, "start_value": [0, 0, 0],
                                                          "end_value": [0, 0, 6 * math.pi]})
        self.assertEqual(expanded["operations"][-1]["parameters"]["value"][2], 6 * math.pi)


class ProgramEquivalenceTests(unittest.TestCase):
    """Both pointer forms make one canonical program: references, derived checks and approval alike."""

    def program(self, operations, checks=(), write=OBJ):
        written = operations[0]["kind"] in ("object_join", "material_principled")  # OTHER is changed too.
        other = [{"handle": OTHER, "revision": OTHER_REV}]
        return validate_program("angelo_scene_program", {"mode": "APPLY", "operations": operations,
            "write_set": [{"handle": write, "revision": REV if write == OBJ else SCENE_REV}] + (other if written else []),
            "read_set": [] if written else other, "expected_checks": list(checks)})

    def both(self, build):
        bare, wrapped = self.program(*build(OTHER)), self.program(*build({"object": OTHER}))
        self.assertEqual(bare, wrapped)
        self.assertIn(OTHER, repr(bare))
        self.assertNotIn("'object'", repr(bare))

    def test_every_typed_pointer(self):
        cases = {
            "parent": lambda o: ([{"kind": "parent_set", "target": OBJ, "parameters": {"parent": o}}],),
            "mirror": lambda o: ([{"kind": "modifier_add", "target": OBJ, "parameters": {
                "type": "MIRROR", "settings": {"mirror_object": o}}}],),
            "boolean": lambda o: ([{"kind": "modifier_add", "target": OBJ, "parameters": {
                "type": "BOOLEAN", "settings": {"operand": o}}}],),
            "constraint": lambda o: ([{"kind": "constraint_add", "target": OBJ, "parameters": {
                "type": "TRACK_TO", "target": o}}],),
            "join": lambda o: ([{"kind": "object_join", "target": OBJ, "parameters": {"sources": [o]}}],),
            "material also": lambda o: ([{"kind": "material_principled", "target": OBJ, "parameters": {
                "name": "Red", "also": [o]}}],),
            "parent check": lambda o: ([{"kind": "transform", "target": OBJ, "parameters": {"location": [0, 0, 1]}}],
                                       [{"kind": "parent", "target": OBJ, "expected": o}]),
        }
        for name, build in cases.items():
            with self.subTest(name):
                self.both(build)

    def test_scene_pointers(self):
        def camera(o):
            return ([{"kind": "property_set", "target": SCENE, "parameters": {"values": {"camera": o}}}],
                    [{"kind": "property", "target": SCENE, "values": {"camera": o}}])

        def marker(o):
            return ([{"kind": "marker_edit", "target": SCENE, "parameters": {
                "operation": "ADD", "name": "Shot", "frame": 1, "camera": o}}],
                    [{"kind": "markers", "target": SCENE, "present": [{"name": "Shot", "camera": o}]}])
        for build in (camera, marker):
            bare = self.program(*build(OTHER), write=SCENE)
            self.assertEqual(bare, self.program(*build({"object": OTHER}), write=SCENE))

    def test_malformed_wrappers_are_refused(self):
        for value in ({"object": OTHER, "extra": 1}, {"object": None}, {"objects": OTHER}, {"object": "two words"}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_operation({"kind": "parent_set", "target": OBJ, "parameters": {"parent": value}})


class ValueConventionTests(unittest.TestCase):
    def test_data_blocks_are_named_alike(self):
        program = raw_program()
        for field in ("material", "collection"):
            shapes = {repr(strings(schema)) for path, schema, _owner in named(program, field)}
            with self.subTest(field=field):
                self.assertEqual(shapes, {repr([{"type": "string", "minLength": 1, "maxLength": 64}])})
        for field, pattern in (("image", IMAGE_ID_PATTERN), ("action", ACTION_ID_PATTERN)):
            for path, schema, owner in named(program, field):
                if owner == "nla" or owner == "node_edit" and field == "action":
                    continue  # The nla check states the expected Action's name; node_edit's action is a verb.
                with self.subTest(path=path):
                    self.assertEqual([member.get("pattern") for member in strings(schema)], [pattern])

    def test_colours_and_vectors(self):
        program = raw_program()
        for path, schema, owner in named(program, r"(?i).*colou?r.*"):
            for member in strings(schema):
                if member.get("type") != "array":
                    continue
                with self.subTest(path=path):
                    self.assertEqual((member["items"]["minimum"], member["items"]["maximum"]), (0, 1))
                    self.assertEqual(member["minItems"], 3 if owner in RGB_OWNERS else 4)
                    self.assertEqual(member["maxItems"], member["minItems"])
        bounds = {"location": 100000, "scale": 1000, "rotation": 2 * math.pi}
        for field, limit in bounds.items():
            for path, schema, owner in named(program, field):
                if "/expected_checks" in path:
                    continue  # A check states an expected value; operations bound what they write.
                for member in strings(schema):
                    if owner == "node_edit":
                        continue  # A node's location on the editor's 2D canvas.
                    if member.get("type") == "array" and member["items"].get("type") == "number":
                        with self.subTest(path=path):
                            self.assertEqual((member["minItems"], member["maxItems"]), (3, 3))
                            self.assertAlmostEqual(member["items"]["maximum"], limit, delta=limit * 1e-4)
                            self.assertEqual(member["items"]["minimum"], -member["items"]["maximum"])

    def test_a_channel_value_is_bounded_as_its_channel(self):
        """A scale key took 100,000 while a transform's scale took 1,000: a quantity's bound
        is the same in every schema; a key's or recipe's value follows the channel its ``property`` names."""
        checked = []
        for name, document, _options in documents():
            with self.subTest(document=name):
                self.assertEqual(channel_bound_problems(name, document), [])
            checked.append(name)
        self.assertIn("host: angelo_scene_program", checked)
        before = {"type": "object", "properties": {  # keyframe_insert's parameters before the fix.
            "property": {"enum": ["location", "rotation_euler", "scale"]},
            "value": {"type": "array", "items": {"type": "number", "minimum": -100000, "maximum": 100000},
                      "minItems": 3, "maxItems": 3}}}
        self.assertEqual(len(channel_bound_problems("fixture", before)), 1, "The scale key is caught")
        for kind, parameters in (("keyframe_insert", {"property": "scale", "value": [1000, 1, 1]}),
                                 ("transform", {"scale": [1000, 1, 1]})):
            accepted_at = validate_operation({"kind": kind, "target": OBJ, "parameters": parameters})["parameters"]
            self.assertEqual(accepted_at.get("value", accepted_at.get("scale"))[0], 1000)
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                validate_operation({"kind": kind, "target": OBJ, "parameters": {
                    key: [1000.5, 1, 1] if key in ("value", "scale") else value for key, value in parameters.items()}})

    def test_enums_are_blender_identifiers_or_listed(self):
        unlisted = set()
        for tool in TOOLS:
            for path, schema, _owner in named(tool["inputSchema"], r".+"):
                for member in strings(schema):
                    values = member.get("enum") or ([] if member.get("type") != "array" else
                                                    member.get("items", {}).get("enum") or [])
                    if values and all(isinstance(value, str) for value in values) and not all(
                            re.fullmatch(r"[A-Z0-9][A-Z0-9_]*", value) for value in values):
                        if path.rsplit("/", 1)[1] not in SPELLINGS:
                            unlisted.add("%s %s" % (tool["name"], path))
        self.assertEqual(unlisted, set())

    def test_numbers_are_json_numbers_and_integers_json_integers(self):
        """Uniform in both validators: a string, a boolean or 2.0 where an integer belongs is refused, by name."""
        for value, reason in (("2", "requires an integer$"), (True, "requires an integer$"),
                              (2.0, r"requires an integer \(2, not 2.0\)")):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, reason):
                _validate({"segments": value}, ACTION_SCHEMAS["modifier_add"]["oneOf"][0]["properties"]["settings"],
                          "settings")
        for value in ("2", True, 2.0):
            with self.subTest(check=value), self.assertRaises(ValueError):
                validate_checks([{"kind": "modifier", "target": "$a", "name": "Bevel", "type": "BEVEL",
                                  "values": {"segments": value}}])

    def test_no_check_requires_a_tolerance(self):
        self.assertEqual([v["properties"]["kind"]["const"] for v in CHECK_VARIANTS if "tolerance" in v["required"]], [])
        omitted = validate_checks([
            {"kind": "dimensions", "target": "$a", "expected": [2, 2, 2]},
            {"kind": "transform", "target": "$a", "property": "location", "expected": [0, 0, 2500]},
            {"kind": "material_values", "target": "$a", "material": "Red", "roughness": 0.5},
            {"kind": "aim", "target": "$a", "point": [0, 0, 0]}])
        # Left out, each number compares at its own float32 precision (check_defaults); an angle keeps the aim's.
        self.assertEqual([check.get("tolerance") for check in omitted], [None, None, None, 0.0001])

    def test_deferred_variants_are_never_stricter_than_exact_ones(self):
        """The generic variant of deferred kinds requires no field an exact variant may leave out (target)."""
        items = plan_schema._operation_items(plan_schema.CLAUDE_CORE_ACTIONS)["oneOf"]
        generic = next(item for item in items if "enum" in item["properties"]["kind"])
        for item in items:
            self.assertLessEqual(set(generic["required"]), set(item["required"]), item["properties"]["kind"])
        minimal = {"create_object": {"type": "EMPTY"}, "armature_create": {
            "bones": [{"name": "Bone", "head": [0, 0, 0], "tail": [0, 0, 1]}]},
            "batch": {"sources": [OTHER], "at": [[0, 0, 0], [1, 0, 0]]}, "object_convert": {"source": OTHER, "alias": "mesh"}}
        for kind, parameters in minimal.items():  # Deferred creating kinds: the target can only be null.
            with self.subTest(kind=kind):
                self.assertIsNone(validate_operation({"kind": kind, "parameters": parameters})["target"])


if __name__ == "__main__":
    unittest.main()
