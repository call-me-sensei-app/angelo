# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Claude's plan writes its tagged unions as if/then: the same plans, and a mismatch names its own field.

``tagged_unions`` rewrites each tagged ``oneOf`` of the plan schema. These tests
pin that both forms accept exactly the same plans (a corpus of recorded and
fixture plans and thousands of single-field mutations of them, checked with
jsonschema), and that Claude Code's answer to a mistake (approximated by
``claude_code_errors``) is short and names the field, where the ``oneOf`` form
answered with over 20,000 characters whose cause the model often never saw.
Needs jsonschema; no Claude process or inference.
"""

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import plan_schema  # noqa: E402
from angelo_agent.schema_compaction import compact_schema  # noqa: E402
from angelo_agent.tagged_unions import discriminate, members  # noqa: E402
from angelo_agent.tool_specs import TOOLS, native_plan_schema  # noqa: E402
from claude_code_errors import GRACE, KEEP, mismatch, shown  # noqa: E402
from test_native_plan_schema import EVERYDAY_OPERATIONS, PHASE5_OPERATIONS  # noqa: E402

try:
    from jsonschema import Draft202012Validator
except ImportError:
    Draft202012Validator = None

HERE = Path(__file__).resolve().parent


def one_of_plan():
    """Claude's plan as it was before tagged_unions: every tagged union a oneOf."""
    document = plan_schema.without_lengths(plan_schema.plan_document(TOOLS, None, plan_schema.CLAUDE_CORE_ACTIONS))
    for variant in document["properties"]["operations"]["items"]["oneOf"]:
        if variant["properties"]["tool"].get("const") == "angelo_execute_python":
            for field, target in plan_schema.SCRIPT_TARGETS.items():
                variant["properties"]["arguments"]["properties"][field]["description"] = target
    return compact_schema(document, min_shared_bytes=plan_schema.PLAN_MIN_SHARED_BYTES)


def program(*operations, checks=()):
    arguments = {"mode": "APPLY", "operations": list(operations)}
    if checks:
        arguments["expected_checks"] = list(checks)
    return {"operations": [{"tool": "angelo_scene_program", "arguments": arguments}]}


def corpus():
    """Plans the models wrote (the bridge scenario) and the plan fixtures of the plan-schema suite.

    The scenario names objects as ``@name:...``, which its replay resolves to handles: here o<n>.
    """
    numbers = {}

    def handles(value):
        if isinstance(value, dict):
            return {key: handles(item) for key, item in value.items()}
        if isinstance(value, list):
            return [handles(item) for item in value]
        if isinstance(value, str) and value.startswith("@name:"):
            return "o%d" % numbers.setdefault(value, len(numbers) + 1)
        return value
    recorded = handles(json.loads((HERE / "scenario_bridge_plans.json").read_text()))
    plans = []
    for turns in recorded["turns"].values():
        for turn in turns:
            if "plan" in turn:
                plans.append(turn["plan"])
            elif "call" in turn:
                plans.append({"operations": [turn["call"]], "verify": True})
            elif "python" in turn:
                plans.append({"operations": [{"tool": "angelo_execute_python", "arguments": turn["python"]}]})
    plans += [program(operation) for operation in EVERYDAY_OPERATIONS + PHASE5_OPERATIONS]
    plans += [program({"kind": "transform", "target": "o3", "parameters": {"scale": [2, 1, 1]}}, checks=[
        {"kind": "dimensions", "target": "o3", "expected": [4, 2, 2]},
        {"kind": "modifier", "target": "o3", "name": "Bevel", "type": "BEVEL", "values": {"segments": 2}},
        {"kind": "modifier", "target": "o3", "name": "Remesh", "type": "REMESH", "values": {"mode": "VOXEL"}},
        {"kind": "aim", "target": "o3", "point": [0, 0, 0]}, {"kind": "parent", "target": "o3", "expected": None}]),
              program({"kind": "modifier_configure", "target": "o4", "parameters": {
                  "type": "WELD", "modifier": "Weld", "settings": {"merge_threshold": 0.01}}}),
              program({"kind": "create_object", "parameters": {"type": "EMPTY", "alias": "e"}}),
              {"operations": [{"tool": "angelo_create_cube", "arguments": {
                  "name": "Cube", "size": 1, "location": [0, 0, 0]}}], "reply": "r"},
              {"operations": [{"tool": "angelo_sculpt_view", "arguments": {"anything": 1}}]}]
    return plans


def mutations(value, path=()):
    """Single-field mutations of a plan: each key removed, each value replaced or wrapped, a field added."""
    if isinstance(value, dict):
        yield path, {**value, "made_up": 1}
        for key, child in value.items():
            yield path, {name: item for name, item in value.items() if name != key}
            for child_path, mutated in mutations(child, path + (key,)):
                yield child_path, mutated if child_path != path + (key,) else mutated
    elif isinstance(value, list):
        for index, child in enumerate(value[:3]):
            for child_path, mutated in mutations(child, path + (index,)):
                yield child_path, mutated
        yield path, value[:1] + value[:1] if value else [1]
    if not isinstance(value, (dict, list)):
        for replacement in (None, "o9", 3, 1.5, True, {"object": "o2"}, "MADE_UP"):
            if replacement != value:
                yield path, replacement


def mutated_plans(plan, most=30):
    """At most ``most`` mutations of ``plan``, spread evenly over all of them (deterministic)."""
    found = list(mutations(plan))
    for path, replacement in found[::max(1, -(-len(found) // most))]:
        copied = copy.deepcopy(plan)
        if not path:
            yield replacement
            continue
        node = copied
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = replacement
        yield copied


@unittest.skipIf(Draft202012Validator is None, "jsonschema is not installed")
class EquivalenceTests(unittest.TestCase):
    def test_both_forms_accept_exactly_the_same_plans(self):
        before, after = Draft202012Validator(one_of_plan()), Draft202012Validator(native_plan_schema())
        Draft202012Validator.check_schema(native_plan_schema())
        checked = differ = 0
        for plan in corpus():
            self.assertTrue(after.is_valid(plan), plan)
            for mutated in mutated_plans(plan):
                checked += 1
                if before.is_valid(mutated) != after.is_valid(mutated):
                    differ += 1
                    self.fail("The forms disagree on %s" % json.dumps(mutated)[:600])
        self.assertGreater(checked, 900)
        self.assertEqual(differ, 0)

    def test_every_union_still_has_one_member_per_tag(self):
        plan = native_plan_schema()
        tag, exact, deferred = members(plan["properties"]["operations"]["items"])
        self.assertEqual(tag, "tool")
        self.assertEqual(set(exact) | set(deferred), {tool["name"] for tool in TOOLS})
        self.assertFalse(set(exact) & set(deferred))
        small = {"oneOf": [{"type": "object", "properties": {"kind": {"const": "a"}}, "additionalProperties": False},
                           {"type": "object", "properties": {"kind": {"const": "b"}}, "additionalProperties": False}]}
        self.assertEqual(discriminate(small), small, "Two members report little; they stay a oneOf")


class ErrorTests(unittest.TestCase):
    """A mistake in one field: Claude Code's whole answer, before (oneOf) and after (if/then)."""

    MISTAKES = {
        "a field the kind does not have": (
            program({"kind": "transform", "target": "o3", "parameters": {"position": [0, 0, 1]}}),
            "/operations/0/arguments/operations/0/parameters", "'position' is not allowed"),
        "an RGB base colour": (
            program({"kind": "material_principled", "target": "o3", "parameters": {"inputs": {"Base Color": [1, 0, 0]}}}),
            "/operations/0/arguments/operations/0/parameters/inputs/Base Color", "fewer than 4 items"),
        "a check's misspelled space": (
            program({"kind": "transform", "target": "o3", "parameters": {"scale": [2, 1, 1]}}, checks=[
                {"kind": "dimensions", "target": "o3", "expected": [4, 2, 2], "space": "GLOBAL"}]),
            "/operations/0/arguments/expected_checks/0/space", "allowed values"),
        "a misspelled modifier setting": (
            program({"kind": "modifier_add", "target": "o3", "parameters": {"type": "BEVEL", "settings": {"segment": 3}}}),
            "/operations/0/arguments/operations/0/parameters/settings", "'segment' is not allowed"),
        "a camera that is not a pointer": (
            program({"kind": "property_set", "parameters": {"values": {"camera": {"object": 5}}}}),
            "/operations/0/arguments/operations/0/parameters/values/camera", "must be string"),
        "a two-number location": (
            {"operations": [{"tool": "angelo_set_transform", "arguments": {"object": "o3", "location": [0, 0]}}]},
            "/operations/0/arguments/location", "fewer than 3 items"),
    }

    def test_a_mismatch_names_its_field_in_under_a_thousand_characters(self):
        plan, before = native_plan_schema(), one_of_plan()
        for name, (value, path, reason) in self.MISTAKES.items():
            with self.subTest(name):
                text = mismatch(value, plan)
                self.assertLess(len(text), 1000, text)
                self.assertIn("%s: must" % path, text)
                self.assertIn(reason, text)
                old = mismatch(value, before)
                self.assertGreater(len(old), 2 * KEEP + GRACE, "The oneOf form's answer, cut by Claude Code")
                self.assertEqual(shown(text), text)

    def test_an_unknown_tool_or_kind_lists_the_choices(self):
        plan = native_plan_schema()
        for value, path in (({"operations": [{"tool": "angelo_move", "arguments": {}}]}, "/operations/0/tool"),
                            (program({"kind": "move", "target": "o3", "parameters": {}}),
                             "/operations/0/arguments/operations/0/kind")):
            text = mismatch(value, plan)
            self.assertLess(len(text), 1000)
            self.assertIn("%s: must be equal to one of the allowed values" % path, text)

    def test_valid_plans_have_no_answer(self):
        plan = native_plan_schema()
        for value in corpus():
            self.assertIsNone(mismatch(value, plan), value)


if __name__ == "__main__":
    unittest.main()
