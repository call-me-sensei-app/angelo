# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The offline plan-output accounting (plan_output_accounting.py): parts, fit, and re-encodings the host accepts.

No provider, Blender or network. The recorded plans here are an Opus footbridge one-shot's plan and a
GPT-6.1-Sol fit-dimensions call, copied from their recordings.
"""

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import plan_output_accounting as accounting  # noqa: E402
from angelo_agent import batch_spec  # noqa: E402
from angelo_agent.action_catalog import validate_operation  # noqa: E402
from angelo_agent.codex_plan import PLAN_TOOLS, ordinary_schema  # noqa: E402
from angelo_agent.providers import _plan_problems  # noqa: E402
from angelo_agent.strict_schema import decode  # noqa: E402
from angelo_agent.tool_specs import native_plan_schema, validate_call  # noqa: E402
from test_native_codex_plan import _resolver, resolver  # noqa: E402

try:
    import jsonschema
except ImportError:  # CI installs it (run_host_tests --max-skips 0).
    jsonschema = None


def cube(name, alias, location, scale):
    return {"kind": "create_primitive", "parameters": {"primitive": "CUBE", "name": name, "alias": alias, "size": 1,
                                                       "location": location, "scale": scale}}


# A footbridge one-shot's plan, as Claude wrote it (8 operations, 1,091 output tokens).
FOOTBRIDGE = {"operations": [{"tool": "angelo_scene_program", "arguments": {"mode": "APPLY", "operations": [
    cube("Bridge_Deck", "deck", [0, 0, 0.5], [4, 1.2, 0.2]),
    cube("Bridge_Post_FL", "p1", [1.9, 0.525, 1.1], [0.15, 0.15, 1]),
    cube("Bridge_Post_FR", "p2", [1.9, -0.525, 1.1], [0.15, 0.15, 1]),
    cube("Bridge_Post_BL", "p3", [-1.9, 0.525, 1.1], [0.15, 0.15, 1]),
    cube("Bridge_Post_BR", "p4", [-1.9, -0.525, 1.1], [0.15, 0.15, 1]),
    cube("Bridge_Rail_L", "r1", [0, 0.525, 1.55], [3.95, 0.12, 0.1]),
    cube("Bridge_Rail_R", "r2", [0, -0.525, 1.55], [3.95, 0.12, 0.1]),
    {"kind": "material_principled", "target": "$deck", "parameters": {
        "name": "Grey_Stone", "inputs": {"Base Color": [0.42, 0.42, 0.4, 1], "Roughness": 0.9, "Metallic": 0},
        "also": ["$p1", "$p2", "$p3", "$p4", "$r1", "$r2"]}}]}}]}
# A final message (fit-dimensions), as GPT-6.1-Sol wrote it (95 output tokens).
FIT_MESSAGE = ('{"reply":"","operations":[{"tool":"angelo_apply_recipe","arguments":{"recipe":"fit-dimensions",'
               '"target":"o6","revision":"3a79e057","parameters":{"dimensions":[1,2,1],"tolerance":0.000001,'
               '"minimum_scale":0.000001,"maximum_scale":1000,"maximum_scale_ratio":100},"mode":"APPLY",'
               '"recovery_observation":null}}]}')


def program_of(plan):
    return plan["operations"][0]["arguments"]["operations"]


class PartsTests(unittest.TestCase):
    def test_parts_join_to_the_plan_exactly(self):
        script = {"tool": "angelo_execute_python", "arguments": {"description": "Arched deck", "code": "result=1"}}
        recipe = json.loads(FIT_MESSAGE)
        for plan in (FOOTBRIDGE, recipe, {"reply": "Done.", "operations": [script]}, {"operations": []},
                     {"operations": [{"arguments": {}, "tool": "x"}, 5]}):
            with self.subTest(plan=accounting.compact(plan)[:60]):
                self.assertEqual("".join(text for _, text in accounting.segments(plan)), accounting.compact(plan))
        parts = dict(accounting.segments({"reply": "Done.", "operations": [script]}))
        self.assertEqual((parts["script"], parts["script text"], parts["reply"]), ('"result=1"', '"Arched deck"', '"Done."'))
        kinds = {part for part, _ in accounting.segments(FOOTBRIDGE)}
        self.assertTrue({"op kinds", "numbers", "names", "aliases", "keys", "enums"} <= kinds)
        # A Principled input has no schema default (it writes over a held material's own: SETTINGS_NOTE): stated.
        self.assertIn(("keys", '"Metallic":'), accounting.segments(FOOTBRIDGE))
        turned = copy.deepcopy(FOOTBRIDGE)
        program_of(turned)[0]["parameters"]["rotation"] = [0, 0, 0]
        self.assertIn(("defaults", '"rotation":[0,0,0]'), accounting.segments(turned))

    def test_the_fit_recovers_known_rates(self):
        rates = [1.1, 0.35, 0.8, 0.7]
        texts = ["12 ab", '{"a":[1.25,-3]}', "word word", "0000", "x\ny", "[[1,2],[3,4]]", "abc def ghi 99"]
        samples = [(text, 50 + sum(r * c for r, c in zip(rates, accounting.features(text)))) for text in texts]
        fit = accounting.ClassFit(samples)
        self.assertAlmostEqual(fit.constant, 50, places=6)
        for found, rate in zip(fit.rates, rates):
            self.assertAlmostEqual(found, rate, places=6)
        self.assertLess(fit.rms, 1e-6)


class ClaudeReencodingTests(unittest.TestCase):
    def test_repeated_units_become_one_batch_each_with_the_same_objects(self):
        after = accounting.claude_after(FOOTBRIDGE)
        operations = program_of(after)
        self.assertEqual([item["kind"] for item in operations],
                         ["create_primitive"] * 3 + ["material_principled", "batch", "batch"])
        self.assertEqual(operations[3]["parameters"]["also"], ["$p1", "$r1"], "copies share the unit's material")
        problems, _ = _plan_problems([copy.deepcopy(after["operations"][0])], PLAN_TOOLS, resolver())
        self.assertEqual(problems, [])
        for batch, unit in ((operations[4], 1), (operations[5], 5)):
            placed = batch_spec.instances(validate_operation(dict(batch, target=None))["parameters"])
            recorded = [op["parameters"]["location"] for op in program_of(FOOTBRIDGE)[unit:unit + len(placed)]]
            self.assertEqual([item["position"] for item in placed], recorded)
        best = program_of(accounting.claude_after(FOOTBRIDGE, best=True))
        self.assertEqual(best[5]["parameters"], {"sources": ["$r1"], "mirror": "Y"})
        self.assertEqual(batch_spec.copies(validate_operation(dict(best[4], target=None))["parameters"]), 3)
        self.assertLess(len(accounting.compact(after)), len(accounting.compact(FOOTBRIDGE)) - 400)

    @unittest.skipIf(jsonschema is None, "jsonschema is not installed")
    def test_claude_codes_schema_check_accepts_the_reencoded_plan(self):
        valid = jsonschema.Draft202012Validator(native_plan_schema()).is_valid
        self.assertTrue(valid(FOOTBRIDGE))
        self.assertTrue(valid(accounting.claude_after(FOOTBRIDGE)))
        self.assertTrue(valid(accounting.claude_after(FOOTBRIDGE, best=True)))


class CodexReencodingTests(unittest.TestCase):
    def test_a_recorded_recipe_call_takes_its_short_form_and_means_the_same(self):
        after = accounting.codex_after(FIT_MESSAGE)
        self.assertEqual(after, '{"reply":"","operations":[{"tool":"angelo_apply_recipe","arguments":{"recipe":'
                                '"fit-dimensions","target":"o6","parameters":{"dimensions":[1,2,1]},"mode":"APPLY"}}]}')
        resolve, ordinary = _resolver(6), ordinary_schema()
        before_call, after_call = (validate_call("angelo_apply_recipe", resolve(
            "angelo_apply_recipe", decode(json.loads(text), ordinary)["operations"][0]["arguments"]))
            for text in (FIT_MESSAGE.replace('"3a79e057"', "null"), after))
        before_call["parameters"].pop("tolerance")
        after_call["parameters"].pop("tolerance")  # The one assumption: the forced 1e-6 was not asked for.
        self.assertEqual(before_call, after_call)


class RecordingTests(unittest.TestCase):
    def test_calls_are_read_from_a_claude_transcript_and_a_codex_rollout(self):
        with tempfile.TemporaryDirectory() as folder:
            run, sessions = Path(folder, "run"), Path(folder, "sessions")
            (run / "angelo-trial-01/trial-01/state/sessions").mkdir(parents=True)
            sessions.mkdir()
            usage = {"output_tokens": 300, "output_tokens_details": {"thinking_tokens": 100}}
            block = {"type": "tool_use", "name": "StructuredOutput", "input": FOOTBRIDGE}
            lines = [{"type": "assistant", "requestId": "r1", "message": {"usage": usage, "content": [block]}},
                     {"type": "assistant", "requestId": "r1", "message": {"usage": usage, "content": [
                         {"type": "text", "text": "ok"}]}}]
            (run / "angelo-trial-01-transcript.jsonl").write_text("\n".join(json.dumps(line) for line in lines))
            (rows,) = accounting.claude_calls(run)
            self.assertEqual((rows["output"], rows["thinking"], rows["text"]), (300, 100, "ok"))
            self.assertEqual(rows["plans"], [FOOTBRIDGE])
            thread = "01a13c10-756c-7591-aeac-9fdf38c9d295"
            (run / "angelo-trial-01/trial-01/state/sessions/s.json").write_text(json.dumps(
                {"call_id": "11111111-1111-4111-8111-111111111111/22222222-2222-4222-8222-222222222222/%s/exec-1"
                            % thread}))
            rollout = [{"type": "response_item", "payload": {"type": "message", "role": "assistant",
                                                             "content": [{"text": FIT_MESSAGE}]}},
                       {"type": "token_usage_record", "payload": {"usage": {"output_tokens": 95,
                                                                            "reasoning_output_tokens": 0}}}]
            (sessions / ("rollout-2026-10-15T00-00-00-%s.jsonl" % thread)).write_text(
                "\n".join(json.dumps(line) for line in rollout))
            (row,) = accounting.codex_calls(run, sessions)
            self.assertEqual((row["trial"], row["output"], row["writes"][0]["text"]), ("angelo-trial-01", 95, FIT_MESSAGE))


if __name__ == "__main__":
    unittest.main()
