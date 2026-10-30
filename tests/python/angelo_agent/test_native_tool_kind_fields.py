# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A tool's declaration never offers a field its host refuses for some kind, mode or action.

A tool schema is one flat object, so every property in it reads as open to every value of the tool's kind, mode
or action. When the host then refuses a property for some of those values, a model following the schema loses a
correction round. On 2026-10-29 a GPT-6.1 Sol plan named a still render (``name`` is an animation's frame pattern),
and that round showed the whole job schema in the chat. A tool may limit fields by kind only when its declaration
states which fields each kind takes, made from the same table its validator reads (``job_spec.fields_by_kind``).

The sweep below calls every declared tool's host validator with each property under each value of its kind, mode
or action, and reads the refusals that name the property as one that value doesn't take. Validators that check
values before fields can hide such a refusal from the sweep, so a tool that limits fields by kind must also be
registered in ``KIND_FIELDS`` with its table, where every pair is checked exactly. No Blender, no model.
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import job_spec  # noqa: E402
from angelo_agent.tool_specs import TOOLS, validate_call  # noqa: E402

DISCRIMINATORS = ("kind", "mode", "action", "operation")
# Tools whose fields depend on their kind: (discriminator, {value: fields}, fields every value takes, statement).
KIND_FIELDS = {
    "angelo_job_start": ("kind", job_spec.FIELDS, set(job_spec.SHARED_FIELDS), job_spec.fields_by_kind()),
}
# How the host words "this value doesn't take that field" (job_spec, rig_spec, anim_spec, anim_structure_spec).
FOREIGN = re.compile(r"does not take|doesn't take|takes no|only applies|applies only")


def sample(schema):
    """A plain value of a property's declared type; validators may refuse it, but only after their field checks."""
    if not isinstance(schema, dict):
        return None
    if "enum" in schema:
        return schema["enum"][0]
    if "const" in schema:
        return schema["const"]
    kind = schema.get("type")
    kind = kind[0] if isinstance(kind, list) else kind
    if kind == "string":
        return "a"
    if kind == "integer":
        return int(schema.get("minimum", 1))
    if kind == "number":
        return float(schema.get("minimum", 1))
    if kind == "boolean":
        return False
    if kind == "array":
        return [sample(schema.get("items", {}))] * max(1, schema.get("minItems", 1))
    if kind == "object":
        return {}
    return None


def foreign_refusal(name, discriminator, value, field, schema):
    """The refusal text when ``value`` refuses ``field`` as a field it doesn't take, else None."""
    properties = schema["properties"]
    args = {key: sample(properties[key]) for key in schema.get("required", ()) if key in properties}
    args[discriminator] = value
    args[field] = sample(properties[field])
    try:
        validate_call(name, args)
    except ValueError as error:
        text = str(error)
        if FOREIGN.search(text) and re.search(r"\b%s\b" % re.escape(field), text):
            return text
    except Exception:   # Not a refusal: a validator that needs Blender for this value is outside the sweep.
        return None
    return None


class ToolKindFieldTests(unittest.TestCase):
    def test_every_field_a_tool_refuses_for_a_kind_is_stated_by_its_declaration(self):
        unstated = []
        for tool in TOOLS:
            schema = tool["inputSchema"]
            properties = schema.get("properties", {})
            for discriminator in DISCRIMINATORS:
                values = properties.get(discriminator, {}).get("enum")
                if not values:
                    continue
                for value in values:
                    for field in properties:
                        if field == discriminator:
                            continue
                        refusal = foreign_refusal(tool["name"], discriminator, value, field, schema)
                        if refusal and tool["name"] not in KIND_FIELDS:
                            unstated.append((tool["name"], value, field, refusal))
        self.assertEqual(unstated, [], "a tool refuses a declared field for some kind; state each kind's fields in "
                                       "its declaration from its validator's table and register it in KIND_FIELDS")

    def test_a_registered_tool_states_exactly_the_fields_its_host_takes(self):
        tools = {tool["name"]: tool for tool in TOOLS}
        for name, (discriminator, table, shared, statement) in KIND_FIELDS.items():
            with self.subTest(tool=name):
                tool = tools[name]
                schema = tool["inputSchema"]
                self.assertIn(statement, tool["description"], "the declaration carries the generated statement")
                self.assertEqual(set(table), set(schema["properties"][discriminator]["enum"]))
                declared = set(schema["properties"]) - {discriminator}
                taken = set().union(*table.values()) | shared
                self.assertEqual(declared, taken, "every declared field is taken by some kind, and only those")
                for value, fields in table.items():
                    for field in declared:
                        refusal = foreign_refusal(name, discriminator, value, field, schema)
                        if field in fields or field in shared:
                            self.assertIsNone(refusal, (value, field))
                        else:
                            self.assertIsNotNone(refusal, (value, field))

    def test_the_job_statement_names_each_kind_its_fields_and_what_is_required(self):
        statement = job_spec.fields_by_kind()
        for kind in job_spec.KINDS:
            self.assertRegex(statement, r"(^|[ /])%s[/:]" % kind)
        self.assertIn("RENDER_STILL: output* frame_start resolution samples format;", statement)
        self.assertIn("name", statement.split("RENDER_ANIMATION:")[1].split(";")[0])
        self.assertNotIn("name", statement.split("RENDER_STILL:")[1].split(";")[0])
        self.assertIn("EXPORT_USD/EXPORT_ALEMBIC: output* frame_start frame_end", statement)
        self.assertIn("BAKE_PHYSICS: no other field", statement)
        self.assertTrue(statement.endswith("Every kind takes minutes and queue."))

    def test_a_still_named_like_an_animation_is_still_refused_with_the_kind_that_takes_it(self):
        with self.assertRaisesRegex(ValueError, "RENDER_STILL does not take: name; kinds that take them: "
                                                "RENDER_ANIMATION"):
            job_spec.validate_job("angelo_job_start", {"kind": "RENDER_STILL", "output": "a.png", "name": "a.####"})


if __name__ == "__main__":
    unittest.main()
