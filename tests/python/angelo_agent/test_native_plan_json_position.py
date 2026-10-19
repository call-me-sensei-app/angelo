# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where a plan stopped being JSON, said to the model once Claude Code gives up on it (json_position).

Recorded (Opus 5.5): a StructuredOutput plan with one closing brace missing; Claude
Code 2.1.301 answers each attempt without a position (its JSON_PARSE branch returns before any hook) and fails the
turn after five. The host reads the last unparsed call's raw text from the stream and asks for the plan once more
with the parser's position, the open brackets and their path, and a caret. Facts only: nothing is repaired, and a
second failure in the same request is the runtime's own error. No Claude process, no Blender.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from claude_protocol_fixtures import ClaudeAdapterFixture, NativeInitializeFixture, completed  # noqa: E402
from angelo_agent import json_position  # noqa: E402
from angelo_agent.claude_transport import ClaudeRequest  # noqa: E402
from angelo_agent.protocol import RpcError  # noqa: E402

RECORDED = ('{"operations": [{"tool": "angelo_apply_recipe", "arguments": {"recipe": "bevel-and-shade", "target": '
            '"o6", "revision": "d17fab92", "mode": "APPLY", "parameters": {"width": 0.125, "segments": 3, '
            '"apply_scale": true}}], "reply": "Adding a bevel modifier."}')


class Facts(unittest.TestCase):
    def test_the_recorded_plan_names_the_missing_brace_and_its_object(self):
        found = json_position.facts(RECORDED)
        self.assertEqual(found["error"], "'}' expected before ']'")
        self.assertEqual((found["open"], found["path"], found["closes"]), ("{[{", "operations[0]", "}]}"))
        self.assertEqual(RECORDED[found["byte"]], "]")
        self.assertTrue(found["excerpt"].endswith("^"))

    def test_unrelated_malformed_shapes_are_located(self):
        cases = {  # text -> (parser message start, innermost open path)
            '{"operations": [{"a": 1}, {"b": 2}': ("Expecting ',' delimiter", "operations"),          # Missing ]}.
            '{"operations": [1, 2,], "reply": "x"}': ("Illegal trailing comma", "operations"),
            '{"reply": "said "hi" there", "operations": []}': ("Expecting ',' delimiter", "(top)"),   # Bare quote.
            '{"operations": [{"tool": "x", "arguments": {"a": [1, 2}}]}': ("']' expected before '}'",
                                                                           "operations[0].arguments.a"),
            '{"operations": [{"tool": "angelo_view", "argum': ("Unterminated string", "operations[0]"),  # Truncated.
            '{\n  "operations": [\n    {"a": 1,}\n  ]\n}': ("Illegal trailing comma", "operations[0]"),
        }
        for raw, (message, path) in cases.items():
            with self.subTest(raw):
                found = json_position.facts(raw)
                self.assertTrue(found["error"].startswith(message), found)
                self.assertEqual(found["path"], path)
                caret = found["excerpt"].split("\n")[-1]
                self.assertEqual(caret.strip(), "^")

    def test_escaped_quotes_and_brackets_inside_strings_are_text(self):
        found = json_position.facts('{"reply": "a \\" [ { quote", "operations": [{"a": "]}"}')
        self.assertEqual((found["open"], found["path"]), ("{[", "operations"))

    def test_a_cut_input_states_no_error_the_cut_may_have_caused(self):
        """Claude Code keeps the first 2,048 characters of an unparsed input beside its whole length."""
        long = '{"operations": [' + ", ".join('{"tool": "t", "arguments": {"n": %d}}' % n for n in range(120)) + \
            '], "reply": "' + "x" * 400 + '"}'
        for end in (2048, 2001, 1500):
            with self.subTest(end):
                found = json_position.facts(long[:end], len(long) + 1)  # The whole input had one more character.
                self.assertNotIn("error", found)
                self.assertEqual(found["kept"], end)
                self.assertNotIn("Still open", json_position.text(long[:end], len(long) + 1))
        inner = long.index("}}", 300) + 1  # Between an operation's arguments and its own closing brace.
        early = long[:inner] + "]" + long[inner:2048]  # A wrong closer well inside the kept part is a fact.
        self.assertEqual(json_position.facts(early, len(long) + 1)["error"], "'}' expected before ']'")
        self.assertIn("of your %d characters" % (len(long) + 1), json_position.text(long[:2048], len(long) + 1))
        self.assertIn("hold no JSON error the cut did not cause", json_position.text(long[:2048], len(long) + 1))

    def test_a_cut_through_a_character_or_near_a_real_error(self):
        """A UTF-16 cut can split an emoji (a lone surrogate); a real error near the cut with text after it stays."""
        body = '{"operations": [{"tool": "t", "arguments": {"note": "' + "a" * 2030 + '\U0001F600 end"}}]}'
        raw = body[:2040] + "\ud83d"  # Claude Code's 2,048-unit slice ends between the emoji's two halves.
        for cut in (raw, '{"k": "\ud83d', '{"\ud83d": [1,}'):
            with self.subTest(cut=cut[-12:]):
                message = json_position.text(cut, 5000)
                self.assertTrue(message.startswith("Nothing of this plan ran."))
        filler = '{"operations": [' + ", ".join('{"tool": "t", "arguments": {"n": %d}}' % n for n in range(60))
        for gap in (5, 20, 40):
            at = filler.rindex(", ", 0, 2048 - gap) + 2  # Just after a comma, inside the kept part.
            broken = filler[:at] + '"x" ' + filler[at:]   # A definite wrong token with text after it.
            with self.subTest(gap=gap):
                found = json_position.facts(broken[:2048], 4000)
                self.assertIn("error", found)
        self.assertEqual(json_position.facts('{"a": [1, 2, tru', 4000).get("error"), None)  # The cut's own token.
        self.assertEqual(json_position.facts('{"a": "xy\\u12', 4000).get("error"), None)   # An escape cut short.
        self.assertTrue(json_position.facts('{"a": "xy\\qyyyy', 4000)["error"].startswith("Invalid"))  # Wrong.

    def test_nan_and_infinity_are_not_json(self):
        found = json_position.facts('{"operations": [{"a": NaN}], "reply": ""}')
        self.assertEqual((found["error"], found["path"]), ("NaN is not a JSON value", "operations[0]"))
        self.assertTrue(json_position.facts('{"x": -Infinity}')["error"].startswith("-Infinity"))

    def test_extra_data_after_a_whole_value_has_nothing_open(self):
        found = json_position.facts('{"a": 1} {"b": 2}')
        self.assertTrue(found["error"].startswith("Extra data"))
        self.assertNotIn("open", found)

    def test_deep_and_long_inputs_stay_bounded_and_linear(self):
        import time
        for raw in ('{"operations":' + "[" * 16000, '{"k":' * 8000, '{"' + "k" * 5000 + '": [1,}'):
            started = time.perf_counter()
            message = json_position.text(raw)
            self.assertLess(time.perf_counter() - started, 1.0)
            self.assertLessEqual(len(message), json_position.MAX_MESSAGE)

    def test_valid_json_and_non_text_have_no_facts(self):
        for value in ('{"operations": [], "reply": ""}', None, 3, {"raw": "x"}):
            self.assertIsNone(json_position.facts(value))
            self.assertIsNone(json_position.text(value))

    def test_a_message_states_facts_and_asks_for_the_whole_plan(self):
        message = json_position.text(RECORDED, 495)
        self.assertIn("(495 characters): '}' expected before ']' (line 1, column", message)
        self.assertTrue(message.startswith("Nothing of this plan ran."))
        self.assertIn('as {"raw": ...} only because it could not parse it; you sent no "raw" field', message)
        self.assertIn("the innermost at operations[0]; closing them all takes }]}", message)
        self.assertTrue(message.endswith("Send the whole plan again as valid JSON."))


def gave_up(raw, unparsed=True):
    """A Claude Code round that could not use the plan and stopped retrying."""
    def round_(process, _):
        emit = process.stdout.emit
        call = {"__unparsedToolInput": {"raw": raw, "len": len(raw.encode())}} if unparsed else json.loads(raw)
        emit({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "StructuredOutput", "id": "t0", "input": call}]}})
        emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t0", "is_error": True,
              "content": "<tool_use_error>InputValidationError: ...</tool_use_error>"}]}})
        emit({"type": "result", "subtype": "error_max_structured_output_retries", "is_error": True,
              "session_id": "fixture-native", "errors": ["Failed to provide valid structured output after 5 attempts"]})
    return round_


class Rounds(ClaudeAdapterFixture, unittest.TestCase):
    def test_a_plan_claude_code_could_not_parse_comes_back_as_its_position(self):
        self.popen_mock.return_value = NativeInitializeFixture(on_user=gave_up(RECORDED))
        plan = self.turn(None, "Bevel the cube")
        self.assertEqual(plan["operations"], [])
        self.assertIn("'}' expected before ']'", plan["problem"])
        self.assertIn("operations[0]", plan["problem"])

    def test_once_a_request_a_second_failure_is_the_runtimes_error(self):
        self.popen_mock.return_value = NativeInitializeFixture(on_user=gave_up(RECORDED))
        owner = ClaudeRequest(self.provider.controller)
        self.assertIn("problem", self.turn(None, "Bevel the cube", owner=owner))
        with self.assertRaises(RpcError):
            self.turn("fixture-native", "the facts", owner=owner)

    def test_a_parsed_last_attempt_after_an_unparsable_one_is_the_schema_error(self):
        def round_(process, _):
            emit = process.stdout.emit
            for number, call in enumerate(({"__unparsedToolInput": {"raw": RECORDED, "len": 495}},
                                           {"operations": "not a list"})):
                emit({"type": "assistant", "message": {"content": [
                    {"type": "tool_use", "name": "StructuredOutput", "id": "t%d" % number, "input": call}]}})
                emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t%d" % number,
                      "is_error": True, "content": "<tool_use_error>...</tool_use_error>"}]}})
            emit({"type": "result", "subtype": "error_max_structured_output_retries", "is_error": True,
                  "session_id": "fixture-native", "errors": ["Output does not match required schema"]})
        self.popen_mock.return_value = NativeInitializeFixture(on_user=round_)
        with self.assertRaises(RpcError):
            self.turn(None, "Bevel the cube")

    def test_a_schema_mismatch_give_up_is_not_a_parse_failure(self):
        self.popen_mock.return_value = NativeInitializeFixture(
            on_user=gave_up('{"operations": "not a list"}', unparsed=False))
        with self.assertRaises(RpcError):
            self.turn(None, "Bevel the cube")

    def test_a_valid_plan_is_untouched(self):
        plan = self.turn(None, "Say hi")
        self.assertEqual((plan["reply"], plan["operations"], "problem" in plan), ("Verified", [], False))
        self.assertIsNone(completed().get("problem"))


if __name__ == "__main__":
    unittest.main()
