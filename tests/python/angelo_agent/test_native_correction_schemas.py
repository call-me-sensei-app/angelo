# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exact correction feedback without expanding static schemas or admitting invalid edits."""

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import plan_patch, plan_schema
from angelo_agent.action_schema_types import _validate
from angelo_agent.action_schemas import ACTION_SCHEMAS
from angelo_agent.batch_spec import MAX_BATCH_COPIES
from angelo_agent.controller import Controller
from angelo_agent.plan_schema import (CODEX_REFERENCE_ACTIONS, CODEX_REFERENCE_MODIFIERS, CLAUDE_CORE_ACTIONS,
                                      deferred_schemas, lookup, rejection_prompt)
from angelo_agent.tool_specs import validate_call
from test_native_codex_plan import PlanTurnFixture
from test_native_everyday_turns import op, program, certified
import test_native_plan_schema as schema_tests
from test_native_schema_compaction import expand_generated


def subdivision(kind="modifier_add", levels=2):
    operations = [op("create_primitive", primitive="CUBE", alias="cube")]
    if kind == "modifier_configure":
        operations.append(op("modifier_add", "$cube", type="SUBSURF", name="Subdivision", settings={"levels": 1}))
    parameters = {"type": "SUBSURF", "settings": {"levels": levels}}
    parameters["modifier" if kind == "modifier_configure" else "name"] = "Subdivision"
    return program(*operations, op(kind, "$cube", **parameters))


def correction(call, core=CODEX_REFERENCE_ACTIONS):
    prompt = rejection_prompt(["Invalid submitted parameters"], [call], core, CODEX_REFERENCE_MODIFIERS)
    return json.loads(prompt.rsplit("\n", 1)[1])


def parameters(schemas, kind):
    return expand_generated(next(entry["parameters"] for entry in schemas["ACTIONS"]["actions"] if entry["kind"] == kind))


def assert_subdivision(test, schemas, kind):
    schema = parameters(schemas, kind)
    test.assertEqual(schema["properties"]["type"], {"const": "SUBSURF"})
    settings = schema["properties"]["settings"]["properties"]
    for key, upper in (("levels", 3), ("render_levels", 6)):
        test.assertEqual(settings[key]["type"], "integer")
        test.assertEqual((settings[key]["minimum"], settings[key]["maximum"]), (0, upper))
    test.assertNotIn("BEVEL", json.dumps(schema))
    test.assertLess(len(json.dumps(schemas, separators=(",", ":")).encode()), 2000)


class CorrectionSelectionTests(unittest.TestCase):
    def test_batch_count_bounds_survive_correction_projection(self):
        for count in (1, MAX_BATCH_COPIES + 2, 2.5):
            call = program(op("create_primitive", primitive="CUBE", alias="cube"),
                           op("batch", sources=["$cube"], array=[{"step": [3, 0, 0], "count": count}]))
            with self.assertRaises(ValueError):
                validate_call(call["tool"], copy.deepcopy(call["arguments"]))
            schemas = correction(call)
            array = parameters(schemas, "batch")["properties"]["array"]["oneOf"][0]
            self.assertEqual(array["items"]["properties"]["count"],
                             {"type": "integer", "minimum": 2, "maximum": MAX_BATCH_COPIES + 1})
            self.assertLess(len(json.dumps(schemas, separators=(",", ":")).encode()), 4000)

    def test_subdivision_ranges_and_integer_rules_are_returned_for_both_plan_cores(self):
        for core in (CODEX_REFERENCE_ACTIONS, CLAUDE_CORE_ACTIONS):
            for kind in ("modifier_add", "modifier_configure"):
                for key, value in (("levels", 4), ("levels", -1), ("levels", 2.5), ("levels", True),
                                   ("render_levels", 7), ("render_levels", 2.5)):
                    with self.subTest(core=core, kind=kind, key=key, value=value):
                        call = subdivision(kind)
                        call["arguments"]["operations"][-1]["parameters"]["settings"] = {key: value}
                        with self.assertRaises(ValueError):
                            validate_call(call["tool"], copy.deepcopy(call["arguments"]))
                        assert_subdivision(self, correction(call, core), kind)

    def test_valid_referenced_parameters_and_nonparameter_errors_do_not_repeat_schema(self):
        for call in (subdivision(), program(op("object_rename", "not an admitted handle", name="Valid"))):
            prompt = rejection_prompt(["A target or revision is unavailable"], [call], CODEX_REFERENCE_ACTIONS,
                                      CODEX_REFERENCE_MODIFIERS)
            self.assertNotIn("Exact schemas", prompt)

    def test_modifier_types_are_deduplicated_and_unknown_type_keeps_full_fallback(self):
        call = program(op("modifier_add", "$cube", type="SUBSURF", settings={"levels": 4}),
                       op("modifier_add", "$cube", type="BEVEL", settings={"segments": 0}))
        call["arguments"]["operations"] *= 8
        schemas = correction(call)
        schema = parameters(schemas, "modifier_add")
        self.assertEqual([v["properties"]["type"]["const"] for v in schema["oneOf"]], ["BEVEL", "SUBSURF"])
        self.assertEqual(len(schemas["ACTIONS"]["actions"]), 1)
        for unknown in ("UNKNOWN_TYPE", None, [], {}):
            call["arguments"]["operations"].append(op("modifier_add", "$cube", type=unknown, settings=7))
            self.assertEqual(correction(call)["ACTIONS"], lookup("ACTIONS", ["modifier_add"]))

    def test_feedback_never_mutates_shared_schemas_or_submitted_plan(self):
        before = copy.deepcopy(ACTION_SCHEMAS)
        call = subdivision(levels=4)
        saved = copy.deepcopy(call)
        schemas = correction(call)
        schemas["ACTIONS"]["actions"][0]["parameters"].clear()
        self.assertEqual(ACTION_SCHEMAS, before)
        self.assertEqual(call, saved)
        assert_subdivision(self, correction(call), "modifier_add")

    def test_malformed_plans_are_tolerated_and_scans_use_existing_limits(self):
        malformed = [None, 3, [], {}, {"tool": []}, {"tool": "angelo_scene_program", "arguments": 3},
                     program(None, [], {}, op([], type={}))]
        self.assertNotIn("Exact schemas", rejection_prompt(["original error"], malformed))
        call = subdivision(levels=4)
        call["arguments"]["operations"] = [call["arguments"]["operations"][-1]] * 40
        with patch.object(plan_schema, "_validate", wraps=_validate) as validate:
            actual = plan_schema._correction_schemas([call] * 70)
        self.assertEqual(validate.call_count, 64 * 32)
        self.assertEqual(actual, correction(subdivision(levels=4)))
        self.assertLess(len(json.dumps(actual).encode()), 2000)
        # A malformed item beyond either existing bound never contributes an additional schema.
        hidden = program(op("object_rename", name=7))
        self.assertNotIn("object_rename", json.dumps(plan_schema._correction_schemas([call] * 64 + [hidden])))

    def test_correction_payload_respects_lookup_name_cap_and_does_not_echo_values(self):
        operations = [op(kind, parameters={"untrusted_marker": "x" * 10000}) for kind in ACTION_SCHEMAS]
        calls = [program(*operations[i:i + 32]) for i in range(0, len(operations), 32)]
        schemas = plan_schema._correction_schemas(calls, core=())
        actions = schemas["ACTIONS"]["actions"]
        self.assertEqual(len(actions), min(32, len(ACTION_SCHEMAS)))
        self.assertNotIn("untrusted_marker", json.dumps(schemas))
        fallback = lookup("ACTIONS", sorted(ACTION_SCHEMAS)[:32])
        self.assertEqual(schemas["ACTIONS"], fallback)

    def test_controller_rejects_before_queueing_and_supplies_selected_schema(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        controller = Controller(directory.name)
        self.addCleanup(controller.close)
        controller.set_document(("correction-fixture", 1))
        controller._begin_metrics()
        controller.busy = True
        for kind in ("modifier_add", "modifier_configure"):
            for value in (4, 2.5):
                call, results = subdivision(kind, value), []
                controller.request_tool(f"{kind}-{value}", call["tool"], call["arguments"], results.append)
                self.assertEqual(len(results), 1)
                self.assertIn("error", results[0])
                assert_subdivision(self, results[0]["exact_schemas"], kind)
                self.assertTrue(controller.tools.empty())
                self.assertEqual((controller.pending, controller.native_jobs), ({}, {}))


class CodexCorrectionTests(PlanTurnFixture):
    def test_repeated_invalid_parameters_do_not_gain_execution_authority(self):
        bad = subdivision(levels=4)
        with self.assertRaises(ValueError):
            self.run_turns(*[[("message", self.plan_text(bad))] for _ in range(3)])
        self.assertEqual(len(self.starts()), 3)
        self.assertEqual(self.dispatched, [])

    def corrected(self, kind, value):
        good, bad = subdivision(kind), subdivision(kind, value)
        self.results["angelo_scene_program"] = [certified(good)]
        self.assertEqual(self.run_turns([("message", self.plan_text(bad))], [("message", self.plan_text(good))]), "completed")
        first, second = self.starts()
        self.assertIs(first["outputSchema"], second["outputSchema"])
        schemas = json.loads(second["input"][0]["text"].rsplit("\n", 1)[1])
        assert_subdivision(self, schemas, kind)
        self.assertEqual(len(self.dispatched), 1, "Rejected edits never dispatch")
        actual = self.dispatched[0][2]["operations"][-1]["parameters"]
        self.assertEqual(actual["settings"]["levels"], 2)

    def test_add_range_correction(self):
        self.corrected("modifier_add", 4)

    def test_add_integer_correction(self):
        self.corrected("modifier_add", 2.5)

    def test_configure_range_correction(self):
        self.corrected("modifier_configure", 4)

    def test_configure_integer_correction(self):
        self.corrected("modifier_configure", 2.5)


class ClaudeCorrectionTests(unittest.TestCase):
    def test_invalid_subdivision_gets_one_correction_and_only_valid_program_dispatches(self):
        good, bad = subdivision(), subdivision(levels=4)
        plans = [{"operations": [call], "reply": "", "verify": False} for call in (bad, good)]
        plans.append({"operations": [], "reply": "Done.", "verify": False})
        provider, context = schema_tests.RejectionLoopTests().provider(plans)
        provider._native_call = Mock(side_effect=lambda name, args, **kwargs:
                                     certified(good) if name == "angelo_scene_program" else {"objects": []})
        self.assertEqual(provider.run(None, "Subdivide a cube", observation={}), "completed")
        prompt = provider._turn.call_args_list[1].args[1]
        self.assertTrue(prompt.endswith("\n" + plan_patch.NOTE), "Claude's correction says the plan can be patched")
        schemas = json.loads(prompt[:-len(plan_patch.NOTE) - 1].rsplit("\n", 1)[1])
        assert_subdivision(self, schemas, "modifier_add")
        edits = [call for call in provider._native_call.call_args_list if call.args[0] == "angelo_scene_program"]
        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0].args[1]["operations"][-1]["parameters"]["settings"]["levels"], 2)


if __name__ == "__main__":
    unittest.main()
