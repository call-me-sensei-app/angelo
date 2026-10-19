# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Regressions for the avoidable rounds and wrong results of fast model tiers.

Each test reproduces a recorded billed-run pattern (Sonnet 5.5 and Haiku 5.5;
doc/angelo/fast_tier_reliability.rst) without a model: the typed-plan
loop with mocked rounds and native calls, and pure program validation.
"""

import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from claude_protocol_fixtures import host
from angelo_agent import context_ledger
from angelo_agent.claude_outcomes import correctable_failure
from angelo_agent.identifiers import IdentifierError, IdentifierTable, expand_call
from angelo_agent.program_spec import validate_program
from angelo_agent.providers import CLAUDE_PLAN_INSTRUCTIONS, ClaudeProvider
from test_native_round_savings import HANDLE, REVISION, committed, program
from test_native_turn_diet import entry, observation

CUBE = {"kind": "create_primitive", "target": None,
        "parameters": {"primitive": "CUBE", "name": "Eval Cube", "size": 2, "location": [0, 0, 0]}}
DIMENSIONS = {"kind": "dimensions", "target": "Eval Cube", "expected": [2, 2, 2], "tolerance": 0.001, "space": "WORLD"}
LOOKUP = {"tool": "angelo_scene_object", "arguments": {"object": HANDLE, "sections": ["TRANSFORM"], "limit": 10}}
MOVE = {"tool": "angelo_scene_program", "arguments": {
    "mode": "APPLY", "write_set": [{"handle": HANDLE, "revision": REVISION}],
    "operations": [{"kind": "transform", "target": HANDLE, "parameters": {"mode": "SET", "location": [-1, -1, 0]}}]}}


def stage_failure(error, **extra):
    """A program that stopped on its isolated stage (program_jobs.ProgramJob._error)."""
    return dict({"success": False, "error": error, "error_code": "VALIDATION", "status": "failed", "final": True, "program_id": "p",
                 "scene_changed": False, "partial_changes_possible": False, "operation_results": [], "checks": []}, **extra)


class LoopTests(unittest.TestCase):
    def provider(self, plans, results):
        context = host()
        context.request_observation = Mock(side_effect=lambda callback: callback({"context_version": 2, "objects": []}))
        provider = ClaudeProvider(context, {})
        provider._turn = Mock(side_effect=plans)
        provider._native_call = Mock(side_effect=results)
        return provider, context

    def test_a_lookup_without_verify_still_gets_the_round_that_does_the_edit(self):
        """Regression: the move never ran, yet the request said completed."""
        plans = [{"reply": "Checking the cube before moving it.", "operations": [LOOKUP]},
                 {"reply": "Moving it.", "operations": [MOVE], "verify": False}]
        provider, _ = self.provider([dict(plan, verify=plan.get("verify", False)) for plan in plans],
                                    [{"object": HANDLE, "object_revision": REVISION}, committed()])
        self.assertEqual(provider.run(None, "Move 'Eval Cube' to (-1, -1, 0)", observation={}), "completed")
        self.assertEqual(provider._turn.call_count, 2)
        self.assertEqual([call.args[0] for call in provider._native_call.call_args_list],
                         ["angelo_scene_object", "angelo_scene_program"])

    def test_a_standalone_verification_without_verify_still_ends_the_turn(self):
        verify = {"tool": "angelo_scene_verify", "arguments": {"read_set": [{"handle": HANDLE, "revision": REVISION}],
                  "checks": [{"kind": "dimensions", "target": HANDLE, "expected": [2, 2, 2], "tolerance": 0.001}]}}
        provider, _ = self.provider([{"reply": "Checking.", "operations": [verify], "verify": False}],
                                    [{"success": True, "passed": True, "status": "pass", "checks": []}])
        self.assertEqual(provider.run(None, "Check it", observation={}), "completed")
        provider._turn.assert_called_once()

    def test_a_program_that_failed_on_its_stage_gets_one_corrected_plan(self):
        """Regression: a guessed modifier name failed the whole request."""
        wrong = copy.deepcopy(MOVE)
        plans = [{"reply": "Configuring.", "operations": [wrong], "verify": False},
                 {"reply": "Configuring the right one.", "operations": [MOVE], "verify": False}]
        error = ("Choose an existing staged modifier of the exact declared type: no BEVEL modifier is named "
                 "'Angelo BEVEL' on this object; its BEVEL modifiers: 'Bevel'")
        provider, context = self.provider(plans, [stage_failure(error), committed()])
        self.assertEqual(provider.run(None, "Change the bevel", observation={}), "completed")
        self.assertEqual(provider._turn.call_count, 2)
        prompt = provider._turn.call_args.args[1]
        self.assertTrue(prompt.startswith("The host stopped your last plan before it changed the scene"))
        self.assertIn("its BEVEL modifiers: 'Bevel'", prompt)

    def test_corrected_plans_are_bounded(self):
        plans = [{"reply": "Try %d." % index, "operations": [MOVE], "verify": False} for index in range(3)]
        provider, _ = self.provider(plans, [stage_failure("Expected check failed: dimensions")] * 3)
        self.assertEqual(provider.run(None, "Scale it", observation={}), "interrupted")
        self.assertEqual(provider._turn.call_count, 3)

    def test_failures_that_may_have_changed_or_need_observation_never_come_back(self):
        operations = [MOVE]
        for result in (stage_failure("x", scene_changed=True), stage_failure("x", partial_changes_possible=True),
                       stage_failure("x", cleanup_pending=True), stage_failure("x", context_changed="frame"),
                       {"error": "The user declined this operation. Do not repeat it."},
                       {"error": "Blender operation/approval expired", "expired": True}):
            with self.subTest(result=result):
                self.assertIsNone(correctable_failure(operations, [result]))
        # A stale binding that changed nothing comes back (request_recovery): its result names what to read,
        # and the correction binds only what a later read showed; the owner refuses an older binding again.
        for message in ("An exact object revision changed; inspect the target before retrying",
                        "The target changed after approval; staged work was not started"):
            self.assertEqual(correctable_failure(operations, [stage_failure(message, error_code="STALE_REVISION")]), message)
        self.assertIsNone(correctable_failure([LOOKUP, MOVE], [{"scene_changed": True}, stage_failure("x")]))
        self.assertEqual(correctable_failure([LOOKUP, MOVE], [{"object": HANDLE}, stage_failure("x")]), "x")
        self.assertIsNone(correctable_failure([{"tool": "angelo_accept_preview", "arguments": {}}], [stage_failure("x")]))


class ProgramValidationTests(unittest.TestCase):
    def test_a_check_naming_the_created_object_binds_to_its_alias(self):
        """Regression: a check naming the created object by its name cost an extra round."""
        result = validate_program("angelo_scene_program", {"mode": "APPLY", "read_set": [], "write_set": [],
                                  "operations": [CUBE], "expected_checks": [DIMENSIONS]}, derive_checks=True)
        self.assertEqual(result["operations"][0]["parameters"]["alias"], "created_1")
        self.assertEqual({check["target"] for check in result["expected_checks"]}, {"$created_1"})
        self.assertEqual(validate_program("angelo_scene_program", result), result, "Re-validation is a no-op")
        aliased = copy.deepcopy(CUBE)
        aliased["parameters"]["alias"] = "cube"
        result = validate_program("angelo_scene_program", {"mode": "APPLY", "operations": [aliased],
                                                           "expected_checks": [DIMENSIONS]})
        self.assertEqual(result["expected_checks"][0]["target"], "$cube")
        self.assertIn({"kind": "object_name", "target": "$cube", "expected": "Eval Cube"}, result["expected_checks"],
                      "A binding by name also requires that name (Blender suffixes a taken one)")

    def test_a_creation_the_program_renames_keeps_no_check_of_its_first_name(self):
        aliased = copy.deepcopy(CUBE)
        aliased["parameters"]["alias"] = "cube"
        rename = {"kind": "object_rename", "target": "$cube", "parameters": {"name": "Final Cube"}}
        result = validate_program("angelo_scene_program", {"mode": "APPLY", "operations": [aliased, rename],
                                                           "expected_checks": [DIMENSIONS]})
        self.assertEqual([check["kind"] for check in result["expected_checks"]], ["dimensions"])

    def test_a_binding_by_name_needs_room_for_its_name_check(self):
        full = [dict(DIMENSIONS, target="$cube", expected=[2, 2, size]) for size in range(1, 32)]
        aliased = copy.deepcopy(CUBE)
        aliased["parameters"]["alias"] = "cube"
        with self.assertRaisesRegex(ValueError, "no room for the check that it gets that name: target it by its \\$alias"):
            validate_program("angelo_scene_program", {"mode": "APPLY", "operations": [aliased],
                                                      "expected_checks": full + [DIMENSIONS]})

    def test_a_claude_context_binds_the_created_name_too(self):
        """Regression: a Claude context's check of a created object by its name cost an extra round.

        A Claude context's short-identifier inference tried to bind the check's
        target as an observed object and refused the name before validation could
        bind it to the creation.
        """
        table = IdentifierTable()
        context_ledger.compact_observation(observation(entry(HANDLE, REVISION, name="Cube")), table)
        arguments = expand_call("angelo_scene_program", {"mode": "APPLY", "operations": [CUBE],
                                                         "expected_checks": [DIMENSIONS]}, table)
        self.assertFalse(arguments.get("read_set"), "The created object is not an observed one")
        result = validate_program("angelo_scene_program", arguments)
        self.assertEqual(result["operations"][0]["parameters"]["alias"], "created_1")
        self.assertEqual(result["expected_checks"][0]["target"], "$created_1")
        # A name no single creation gives is still refused with the fix, before anything runs.
        for operations, target in (([CUBE], "Cube"), ([CUBE, copy.deepcopy(CUBE)], "Eval Cube")):
            with self.subTest(target=target, creations=len(operations)):
                with self.assertRaisesRegex(IdentifierError, "'%s' is not an object handle" % target):
                    expand_call("angelo_scene_program", {"mode": "APPLY", "operations": operations,
                                "expected_checks": [dict(DIMENSIONS, target=target)]}, table)

    def test_a_material_a_script_makes_is_never_found_and_the_error_names_the_plan_that_works(self):
        """Regression: a program assigning a material a script of the same plan made cost an extra round.

        A script made the deck and its 'Grey Stone' material; a program in the same plan assigned that
        material to new posts by name. Validation refuses it before anything runs, as it must (no lookup
        in the file), and the correction the model made is what the refusal now names.
        """
        posts = [{"kind": "create_primitive", "target": None, "parameters": {
            "primitive": "CUBE", "name": "Post_%d" % index, "alias": "p%d" % index, "size": 1,
            "location": [index, 0, 0]}} for index in range(2)]
        assign = {"kind": "material_assign", "target": "$p0", "parameters": {"material": "Grey Stone", "also": ["$p1"]}}
        with self.assertRaisesRegex(ValueError, r"^material_assign MATERIAL_NOT_FOUND: .* never looks one up in the "
                                    r"file, so one a script makes is not found: state it in this program "
                                    r"\(material_principled; a script after this program can reuse it by name\) "
                                    r"or name an object that has it in source. A file material no object uses is "
                                    r"assigned with a reviewed script$"):
            validate_program("angelo_scene_program", {"mode": "APPLY", "operations": posts + [assign]})
        stated = {"kind": "material_principled", "target": "$p0", "parameters": {
            "name": "Grey Stone", "inputs": {"Roughness": 0.85}, "also": ["$p1"]}}
        validate_program("angelo_scene_program", {"mode": "APPLY", "operations": posts + [stated]})

    def test_ambiguous_or_unknown_names_are_still_rejected_with_the_fix(self):
        twice = {"mode": "APPLY", "operations": [CUBE, copy.deepcopy(CUBE)], "expected_checks": [DIMENSIONS]}
        with self.assertRaisesRegex(ValueError, "'Eval Cube' is neither.*through its \\$alias"):
            validate_program("angelo_scene_program", twice)
        with self.assertRaisesRegex(ValueError, r"\('\$eval_cube'\).*handle and declare it in write_set"):
            validate_program("angelo_scene_program", {"mode": "APPLY", "write_set": [{"handle": HANDLE, "revision": REVISION}],
                "operations": [{"kind": "transform", "target": "$eval_cube", "parameters": {"location": [1, 0, 0]}}]})
        with self.assertRaisesRegex(ValueError, "'Cube' is\\s+not declared.*never its name"):
            validate_program("angelo_scene_program", {"mode": "APPLY", "write_set": [{"handle": HANDLE, "revision": REVISION}],
                "operations": [{"kind": "transform", "target": "Cube", "parameters": {"location": [1, 0, 0]}}]})


class GuidanceTests(unittest.TestCase):
    def test_the_plan_contract_names_the_three_shapes_fast_models_got_wrong(self):
        text = " ".join(CLAUDE_PLAN_INSTRUCTIONS.split())
        # A Claude plan reads the Claude per-model text (claude_text).
        self.assertIn("Answer only via one StructuredOutput call, never plain text", text)
        self.assertIn("An operation is {tool, arguments}; program steps go in its arguments.operations", text)
        self.assertIn("Targets are o<n> handles or $aliases, never names", text)

    def test_the_plan_contract_says_a_program_never_sees_what_a_script_of_its_plan_makes(self):
        """The plan shape behind extra rounds: a program assigning a material a script of the same plan makes."""
        text = " ".join(CLAUDE_PLAN_INSTRUCTIONS.split())
        # A Claude plan reads the Claude per-model text (claude_text).
        self.assertIn("All steps are validated before the first runs: a program never sees what a script in the plan "
                      "makes, so state shared materials in the program, before the script.", text)


if __name__ == "__main__":
    unittest.main()
