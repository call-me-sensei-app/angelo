# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Length limits are the host's to enforce: a near miss must not cost a whole plan.

The schema Claude Code checks a plan against carries no string lengths; the
host's validator answers an over-long text in one line. Recorded plans and the
runtime's own form of a rejection are replayed without a model or a process.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.claude_outcomes import runtime_result_error
from angelo_agent.claude_stream import RoundStream, likely_cause
from angelo_agent.providers import _plan_problems
from angelo_agent.strict_schema import inline_references
from angelo_agent.tool_specs import TOOL_MAP, dynamic_tools, native_plan_schema, validate_call

try:
    from jsonschema import Draft202012Validator
except ImportError:  # CI installs it (run_host_tests --max-skips 0).
    Draft202012Validator = None


class ScriptDescriptionTests(unittest.TestCase):
    """A model cannot count characters: a description a little over its limit must not cost a round.

    In one recorded session 18 plans, each carrying up to 25 KB of scripts,
    were refused by the runtime's schema check for a description of 241 to 286
    characters against a limit of 240, and were written again whole.
    """

    RECORDED = (241, 242, 244, 246, 248, 251, 252, 253, 254, 256, 258, 286)

    def plan(self, length):
        return {"operations": [{"tool": "angelo_execute_python", "arguments": {
            "description": "d" * length, "code": "result = 1"}}], "verify": True}

    @unittest.skipIf(Draft202012Validator is None, "jsonschema is not installed")
    def test_the_runtime_schema_admits_every_recorded_length(self):
        validator = Draft202012Validator(native_plan_schema())
        for length in self.RECORDED:
            with self.subTest(length=length):
                self.assertEqual(sum(1 for _ in validator.iter_errors(self.plan(length))), 0)

    def test_the_host_enforces_one_limit_with_room_to_miss_and_says_how_long_the_text_was(self):
        limit = TOOL_MAP["angelo_execute_python"]["inputSchema"]["properties"]["description"]["maxLength"]
        self.assertGreaterEqual(limit, 2 * 240, "Twice what a model aimed for and overshot by at most a fifth")
        arguments = self.plan(limit)["operations"][0]["arguments"]
        self.assertEqual(validate_call("angelo_execute_python", arguments), arguments)
        with self.assertRaisesRegex(ValueError, r"1-%d printable characters \(this one has %d\)" % (limit, limit + 1)):
            validate_call("angelo_execute_python", dict(arguments, description="d" * (limit + 1)))
        codex = next(tool for tool in dynamic_tools() if tool["name"] == "angelo_execute_python")
        self.assertEqual(codex["inputSchema"]["properties"]["description"]["maxLength"], limit)


def schema_nodes(node):
    """Every schema-valued dict of a schema (never the data under enum, const or default)."""
    if isinstance(node, list):
        for item in node:
            yield from schema_nodes(item)
    elif isinstance(node, dict):
        yield node
        for key, value in node.items():
            if key == "properties" and isinstance(value, dict):
                for child in value.values():
                    yield from schema_nodes(child)
            elif key not in ("enum", "const", "default", "examples", "required"):
                yield from schema_nodes(value)


class SchemaNearMissTests(unittest.TestCase):
    """A length limit in the schema the runtime checks costs a whole plan and answers with every oneOf mismatch.

    Recorded: 22 plans refused only for a description over 240 characters.
    The runtime's answer was about 8,000 characters listing each tool's
    mismatches, the one cause among them, and each plan was written again.
    The host's validator answers the same mistake in one line, so length
    limits are the host's to enforce.
    """

    def script(self):
        from angelo_agent.tagged_unions import members
        return members(inline_references(native_plan_schema())["properties"]["operations"]["items"])[1][
            "angelo_execute_python"]

    def test_the_runtimes_schema_has_no_length_limit_left_to_miss(self):
        for allowed in (None, frozenset(TOOL_MAP) - {"angelo_team_library_search"}):
            limits = [node for node in schema_nodes(native_plan_schema(allowed)) if "maxLength" in node]
            self.assertEqual(limits, [])

    @unittest.skipIf(Draft202012Validator is None, "jsonschema is not installed")
    def test_an_overlong_text_passes_the_runtime_and_the_host_answers_in_one_line(self):
        validator = Draft202012Validator(native_plan_schema())
        for field, length, names in (("description", 5000, ("1-500 printable characters", "this one has 5000")),
                                     ("code", 40000, ("1-32768 characters", "this one has 40000"))):
            arguments = {"description": "Build the pines", "code": "result = 1"}
            arguments[field] = ("#" if field == "code" else "d") * length
            operation = {"tool": "angelo_execute_python", "arguments": arguments}
            with self.subTest(field=field):
                self.assertEqual(sum(1 for _ in validator.iter_errors({"operations": [operation]})), 0,
                                 "The runtime no longer refuses it with a list of every tool's mismatches")
                (problem,), _ = _plan_problems([operation], None)
                self.assertTrue(problem.startswith("operations[0] (angelo_execute_python): "))
                self.assertLess(len(problem), 200)
                self.assertNotIn("\n", problem)
                for name in names:
                    self.assertIn(name, problem)

    def test_other_overlong_fields_get_the_hosts_one_line_too(self):
        for operation in ({"tool": "angelo_create_cube", "arguments": {"name": "n" * 65, "size": 1.0, "location": [0, 0, 0]}},
                          {"tool": "angelo_write_file", "arguments": {"path": "notes.txt", "content": "c" * 131073}},
                          {"tool": "angelo_scene_search", "arguments": {"query": "q" * 129}}):
            with self.subTest(tool=operation["tool"]):
                (problem,), _ = _plan_problems([operation], None)
                self.assertLess(len(problem), 200)

    def dump(self, *deep):
        """A rejection in Claude Code's form: every oneOf member's mismatches, the meant member's among them."""
        shallow = []
        for tool in ("angelo_inspect_scene", "angelo_create_cube", "angelo_set_transform", "angelo_scene_search"):
            shallow += ["/operations/0/arguments: must NOT have additional properties ('description' is not allowed)",
                        "/operations/0/arguments: must have required property 'name'",
                        '/operations/0/tool: must be equal to constant: "%s"' % tool]
        return "Output does not match required schema: " + ", ".join(shallow[:6] + list(deep) + shallow[6:])

    def test_a_rejection_the_runtime_still_makes_leads_with_its_most_likely_cause(self):
        text = self.dump("/operations/0/arguments/code: must NOT have fewer than 1 characters")
        cause = "Most likely cause: operations[0].arguments.code must NOT have fewer than 1 characters"
        self.assertEqual(likely_cause(text), cause)
        nested = self.dump("/operations/0/arguments/operations/2/parameters/location/1: must be number",
                           "/operations/0/arguments/operations/2/kind: must be equal to constant: \"transform\"")
        self.assertEqual(likely_cause(nested), "Most likely cause: operations[0].arguments.operations[2].parameters.location[1] "
                                               "must be number")
        self.assertIsNone(likely_cause(self.dump()), "Every member failed alike: no line is singled out")
        self.assertIsNone(likely_cause("Rate limited; try again later"))
        self.assertEqual(likely_cause("Output does not match required schema: /verify: must be boolean"),
                         "Most likely cause: verify must be boolean", "A single mismatch is the cause")
        # The person's row of a rejected plan call, and the error of a round the runtime gave up on.
        plans = {"toolu_1": {"operations": []}}
        events = RoundStream().events({"type": "user", "session_id": "s", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "is_error": True, "content": text}]}}, plans)
        shown = next(data for kind, data in events if kind == "activity")["result"]["content"]
        self.assertEqual(shown.split("\n", 1), [cause, text])
        self.assertEqual(plans, {}, "The rejected call never counted as a plan")
        gave_up = runtime_result_error({"type": "result", "subtype": "error_max_structured_output_retries", "is_error": True,
                                        "errors": [text]})
        self.assertTrue(gave_up.startswith(cause + ". "))

    def test_the_schema_states_targets_below_what_the_host_enforces(self):
        # A model cannot count characters: it overshot a stated 240 by up to a fifth (286).
        properties = self.script()["properties"]["arguments"]["properties"]
        enforced = TOOL_MAP["angelo_execute_python"]["inputSchema"]["properties"]
        for field in ("description", "code"):
            with self.subTest(field=field):
                stated = int("".join(c for c in properties[field]["description"] if c.isdigit()))
                self.assertLessEqual(stated * 1.2, enforced[field]["maxLength"], "Room for the recorded overshoot")
                self.assertNotIn("maxLength", properties[field])


if __name__ == "__main__":
    unittest.main()
