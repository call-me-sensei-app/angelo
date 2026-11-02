# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An action kind's surfaces never offer a field its validator refuses for the mode written.

A kind whose parameters are one flat object for several modes offers every field to every mode, while its
validator may refuse a field another mode owns. The 1.0.7 bug sweep found keyframe_edit, action_set, nla_edit and
marker_edit built that way with nothing saying so: a model that wrote action_set ASSIGN with a name, or nla_edit
TRACK_ADD with mute, was refused and lost a correction round. Every such kind is registered in
``kind_notes.MODE_FIELDS`` with the table its validator reads, and each surface a model reads it on (an exact
lookup's note, Claude's plan for a core kind, the Codex program reference) states that table
(``mode_fields.statement``); the refusal names the mode's fields too. The job tool's fields by kind is the same rule
for a tool (test_native_tool_kind_fields).

The tests below call the real validator for every (mode, field) pair of every registered kind, so a statement can
only list what the host accepts; a sweep over every action kind fails on a mode-dependent refusal of an unregistered
kind. No Blender, no model.
"""

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import kind_notes, node_edit_spec  # noqa: E402
from angelo_agent.action_catalog import catalog, validate_operation  # noqa: E402
from angelo_agent.action_schemas import ACTION_SCHEMAS, CREATE_KINDS  # noqa: E402
from angelo_agent.codex_reference import program_reference  # noqa: E402
from angelo_agent.plan_schema import (  # noqa: E402
    CLAUDE_CORE_ACTIONS, CODEX_REFERENCE_ACTIONS, core_operation, lookup, plan_schema)
from angelo_agent.tool_specs import TOOLS  # noqa: E402
from angelo_agent.scene_binding import SCENE_KINDS  # noqa: E402

OBJ, OTHER, SCENE = ("scn:" + "a" * 32 + ":%d" % index for index in (7, 8, 9))
STEP = node_edit_spec.NODE_EDIT_SCHEMA["properties"]["edits"]["items"]  # node_edit's advertised step.
ACTION = "dat:" + "b" * 32 + ":ACTION:12"  # As sample() writes a data ID.
# How the validators word a field a mode doesn't take (mode_fields.problem and older one-off rules).
FOREIGN = re.compile(r"takes no|take none|do not apply|does not take|doesn't take|only applies|applies only")


def sample(schema):
    """A schema-valid value of a field other than its default, so the validator reads it as given."""
    if isinstance(schema, dict) and "oneOf" in schema:
        members = [member for member in schema["oneOf"] if member != {"type": "null"}]
        return sample(members[0]) if members else None
    if "enum" in schema:
        return next((value for value in schema["enum"] if value != schema.get("default")), schema["enum"][0])
    if "const" in schema:
        return schema["const"]
    kind = schema.get("type")
    if kind == "string":
        pattern = schema.get("pattern", "")
        datablock = re.search(r":([A-Z_]+):", pattern)  # A data ID: dat:<epoch>:<TYPE>:<uid>.
        return ("dat:" + "b" * 32 + ":%s:12" % datablock.group(1) if datablock and "dat:" in pattern
                else OTHER if "\\$" in pattern else "Abc")
    if kind in ("integer", "number"):
        low, high = schema.get("minimum", 0), schema.get("maximum", 100)
        value = next(v for v in (2, 3, 1, high) if low <= v <= high and v != schema.get("default"))
        return value if kind == "integer" else float(value)
    if kind == "boolean":
        return not schema.get("default", False)
    if kind == "array":
        items = schema.get("items", {})
        count = max(1, schema.get("minItems", 1))
        return [sample(items) + index if items.get("type") in ("integer", "number") else sample(items)
                for index in range(count)]
    if kind == "object":
        properties = schema.get("properties") or {}
        if properties:
            name = next(iter(properties))
            return {name: sample(properties[name])}
        return {"location": 1.0}
    return 1


def refused(error):
    """(foreign, missing) field names a mode_fields refusal states, or (None, None) for another refusal."""
    text = str(error)
    foreign = re.search(r"takes no ([\w, ]+?)(?: and needs|;|$)", text)
    missing = re.search(r"needs ([\w, ]+?)(?:;|$)", text)
    if not foreign and not missing:
        return None, None
    split = lambda match: set(match.group(1).split(", ")) if match else set()  # noqa: E731
    return split(foreign), split(missing)


def operation(kind, key, value, table, field=None, drop=None):
    """A kind's operation in one mode with its required fields (but ``drop``) and ``field`` set."""
    where, schema = kind_notes.MODE_FIELDS[kind][0], ACTION_SCHEMAS[kind]
    if where == "edits":
        variant = node_edit_spec._VARIANTS[value]
        step = {"op": value, **{name: sample(variant["properties"][name]) for name in table[value][0] if name != drop}}
        if field:
            step[field] = sample(STEP["properties"][field])
        parameters = {"material": "M", "edits": [step]}
    elif where == "settings":
        properties = schema["properties"]["settings"]["properties"]
        parameters = {"settings": {key: value, **({field: sample(properties[field])} if field else {})}}
    else:
        properties = schema["properties"]
        parameters = {key: value, **{name: sample(properties[name]) for name in table[value][0] if name != drop}}
        if field:
            parameters[field] = sample(properties[field])
    target = None if kind in CREATE_KINDS else SCENE if kind in SCENE_KINDS else OBJ
    return {"kind": kind, "target": target, "parameters": parameters}


def outcome(op):
    try:
        validate_operation(op)
    except (ValueError, TypeError) as error:
        return refused(error)
    return set(), set()


def fields(kind):
    """The fields a registered kind's modes choose among (the mode key and common fields excluded)."""
    where, key, table, common = kind_notes.MODE_FIELDS[kind]
    schema = ACTION_SCHEMAS[kind]
    if where == "edits":
        return set(STEP["properties"]) - {key}
    properties = schema["properties"]["settings"]["properties"] if where == "settings" else schema["properties"]
    return set(properties) - {key}


class ModeFieldTests(unittest.TestCase):
    def test_each_mode_takes_exactly_the_fields_its_statement_lists(self):
        for kind, (where, key, table, common) in kind_notes.MODE_FIELDS.items():
            listed = {value: set(required) | set(optional) for value, (required, optional) in table.items()}
            owned = set().union(*listed.values())
            for value in table:
                for field in sorted(fields(kind)):
                    if field in table[value][0]:
                        continue
                    with self.subTest(kind=kind, mode=value, field=field):
                        foreign, _ = outcome(operation(kind, key, value, table, field))
                        if field in listed[value] or (common and field not in owned):
                            self.assertNotIn(field, foreign or set(), "the statement lists it, so it is taken")
                        else:
                            self.assertIsNotNone(foreign, "refused as a field this mode doesn't take")
                            self.assertIn(field, foreign)
                for field in table[value][0]:
                    with self.subTest(kind=kind, mode=value, required=field):
                        _, missing = outcome(operation(kind, key, value, table, drop=field))
                        self.assertIn(field, missing or set(), "a required field is refused when left out")

    def test_every_field_the_schema_offers_is_taken_by_some_mode(self):
        for kind, (where, key, table, common) in kind_notes.MODE_FIELDS.items():
            with self.subTest(kind=kind):
                owned = set().union(*(set(required) | set(optional) for required, optional in table.values()))
                self.assertLessEqual(owned, fields(kind), "the table names only fields the schema offers")
                if not common:
                    self.assertEqual(owned, fields(kind), "with no common fields, every field belongs to a mode")
                self.assertEqual(set(table), set(self.key_schema(kind)["enum"]), "the table covers every mode")

    def key_schema(self, kind):
        where, key = kind_notes.MODE_FIELDS[kind][:2]
        schema = ACTION_SCHEMAS[kind]
        if where == "edits":
            return STEP["properties"][key]
        properties = schema["properties"]["settings"]["properties"] if where == "settings" else schema["properties"]
        return properties[key]

    def test_the_statement_lists_each_mode_with_its_required_fields_marked(self):
        for kind, (where, key, table, common) in kind_notes.MODE_FIELDS.items():
            text = kind_notes.mode_statement(kind)
            body = text.split("): ", 1)[1].split(".")[0]
            stated = {}
            for entry in body.split("; "):
                modes, listed = entry.split(": ")
                for mode in modes.split("/"):
                    stated[mode] = listed.split() if listed != "none" else []
            with self.subTest(kind=kind):
                self.assertEqual(stated, {value: [name + "*" for name in required] + list(optional)
                                          for value, (required, optional) in table.items()})

    def test_every_surface_carries_the_statement(self):
        reference, plan_reference = program_reference(), program_reference(plan=True)
        lookups = json.dumps(lookup("ACTIONS", list(kind_notes.MODE_FIELDS)))
        claude_plan = json.dumps(plan_schema(TOOLS))
        for kind, (where, key, table, common) in kind_notes.MODE_FIELDS.items():
            text = kind_notes.mode_statement(kind)
            with self.subTest(kind=kind):
                if where == "edits":  # node_edit's step schema states its own: every surface reads that schema.
                    self.assertEqual(STEP["description"], text)
                    continue
                self.assertEqual(catalog(kind)["note"], text, "the exact lookup's note")
                self.assertIn(json.dumps(text), lookups)
                if kind in CLAUDE_CORE_ACTIONS:
                    self.assertIn(json.dumps(text), claude_plan)
                    parameters = core_operation(kind)["properties"]["parameters"]
                    self.assertEqual((parameters["properties"][where] if where else parameters)["description"], text)
                if kind in CODEX_REFERENCE_ACTIONS:
                    self.assertIn("\n" + text + "\n", reference)
                    self.assertIn("\n" + text + "\n", plan_reference)

    def test_the_refusal_names_what_the_mode_takes(self):
        def op(kind, target=OBJ, **parameters):
            return {"kind": kind, "target": target, "parameters": parameters}
        cases = [(op("action_set", mode="ASSIGN", action=ACTION, name="W"),
                  "action_set ASSIGN takes no name; ASSIGN takes action* slot stash_previous"),
                 (op("nla_edit", operation="TRACK_ADD", track="A", mute=True),
                  "nla_edit TRACK_ADD takes no mute; TRACK_ADD takes track* position"),
                 (op("marker_edit", SCENE, operation="ADD", name="Hit"),
                  "marker_edit ADD needs frame; ADD takes name* frame* camera"),
                 (op("keyframe_edit", operation="EULER_FILTER", frame_range=[1, 2]),
                  "keyframe_edit EULER_FILTER takes no frame_range; EULER_FILTER takes property index"),
                 (op("light_create", None, settings={"light_type": "SUN", "radius": .1}),
                  "light light_type SUN takes no radius; SUN takes angle energy color use_shadow"),
                 (op("origin_set", mode="POINT"), "origin_set POINT needs point; POINT takes point*")]
        for op, text in cases:
            with self.subTest(op=op), self.assertRaises(ValueError) as caught:
                validate_operation(op)
            self.assertEqual(str(caught.exception), text)


class UnregisteredKindSweepTests(unittest.TestCase):
    def test_no_unregistered_kind_refuses_a_field_by_mode(self):
        """Every enum field of every flat kind, with each other field set: a refusal that names that field as one
        the mode doesn't take belongs to a registered kind. (Validators that check other rules first can hide such a
        refusal, so a new mode table also registers itself in kind_notes.MODE_FIELDS.)"""
        unstated = []
        for kind, schema in ACTION_SCHEMAS.items():
            properties = schema.get("properties") or {}
            for key, spec in properties.items():
                spec = next((m for m in spec.get("oneOf", ()) if "enum" in m), spec) if "oneOf" in spec else spec
                for value in spec.get("enum", ()):
                    for field in properties:
                        if field == key or kind in kind_notes.MODE_FIELDS:
                            continue
                        params = {key: value, field: sample(properties[field])}
                        params.update({name: sample(properties[name]) for name in schema.get("required", ())
                                       if name not in params})
                        target = None if kind in CREATE_KINDS else SCENE if kind in SCENE_KINDS else OBJ
                        try:
                            validate_operation({"kind": kind, "target": target, "parameters": params})
                        except (ValueError, TypeError) as error:
                            if FOREIGN.search(str(error)) and re.search(r"\b%s\b" % re.escape(field), str(error)):
                                unstated.append((kind, key, value, field, str(error)))
        self.assertEqual(unstated, [], "a kind refuses a field by mode: register its table in kind_notes.MODE_FIELDS")


if __name__ == "__main__":
    unittest.main()
