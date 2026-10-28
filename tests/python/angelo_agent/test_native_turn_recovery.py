# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A step that changed nothing never ends a turn by itself.

Each test replays a result shape recorded in a long Claude session (a
capability lookup, an API lookup the host could not answer, a program whose
commit rolled back) through the typed-plan loop with mocked rounds and native
calls: no model, process or Blender. The Codex cases use the scripted
app-server of ``test_native_codex_plan``.
"""

import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from claude_protocol_fixtures import host
from angelo_agent import plan_recovery
from angelo_agent.claude_outcomes import native_result_problem
from angelo_agent.plan_schema import lookup
from angelo_agent.plan_recovery import FAILED_EDIT_CORRECTIONS  # noqa: E402
from angelo_agent.providers import CLAUDE_PLAN_REJECTIONS, ClaudeProvider
from angelo_agent.script_errors import SCRIPT_CORRECTIONS
from test_native_codex_plan import PlanTurnFixture

from test_native_round_savings import committed, program

CAPABILITIES = {"toolset_version": 34, "blender_version": "5.2.2 LTS", "scene_changed": False,
                "permissions": "Discovery does not grant permission; the configured host approval policy still applies."}
CHECK_SCHEMAS = {"tool": "angelo_agent_capabilities", "arguments": {"family": "CHECKS", "names": ["mesh_manifold", "aim"]}}
API = {"tool": "angelo_blender_api", "arguments": {"domain": "RNA", "name": "SubsurfModifier", "member": "",
                                                    "offset": 0, "limit": 64}}
API_UNKNOWN = {"tool": "angelo_blender_api", "arguments": dict(API["arguments"], name="NoSuchSettings")}
API_RESULT = {"domain": "RNA", "blender_version": "5.2.2 LTS", "scene_changed": False, "name": "SubsurfModifier",
              "items": [{"name": "levels", "kind": "property", "type": "INT"}], "total": 1, "offset": 0,
              "next_offset": None, "truncated": False}
API_ERROR = {"error": "Unknown installed RNA type; list RNA types first", "native_dispatch_wait_ms": 20.454}
SCRIPT = {"tool": "angelo_execute_python", "arguments": {"description": "Build the island", "code": "result = 1"}}
SCRIPT_RESULT = {"success": True, "result": 1, "output": "", "undoable": True, "external_effects_undoable": False,
                 "verified": {"scene": "Scene", "object_count": 4},
                 "changes": {"added": [], "removed": [], "modified": [{"name": "Island", "fields": ["modifiers"]}]}}
VIEW = {"tool": "angelo_view", "arguments": {"targets": [], "views": ["SCENE_CAMERA"], "shading": "SOLID", "resolution": 768}}
DELEGATE = {"tool": "angelo_delegate", "arguments": {"connection_id": "11111111-1111-4111-8111-111111111111",
                                                      "model": "opus", "task": "Is the camera right?"}}
# A reviewed script that raised: the host reloaded Main; pixels and external effects remain unverified.
RAISED = {"error": "AttributeError: 'function' object has no attribute 'tau' (line 143)", "line": 143,
          "source": "angle = math.radians.tau * index / count",
          "traceback": ["line 210, in <module>: build_pines(18)", "line 143, in build_pines: angle = math.radians.tau * index / count"],
          "output": "", "script_error": True, "rolled_back": False, "main_data_restored": True,
          "rollback_scope": "MAIN_DATA", "partial_changes_possible": True, "image_pixels_restored": False,
          "external_effects_restored": False,
          "rollback": "Main data was restored. Image pixels and external effects remain unverified. Handles are stale.",
          "changes": {"added": [], "removed": [], "modified": []}}
# The same script where Undo could not prove the scene restored: what it changed before the error stays.
KEPT = {key: value for key, value in RAISED.items() if key != "scene_changed"}
KEPT.update(main_data_restored=False, request_checkpoint="b81d1b08-84d4-4fe9-952c-1683d435e6d3",
            rollback="Blender's Undo history could not take the scene back. What the script changed before the error is "
                     "still in the scene. Files, preferences and other effects outside the scene are not undone.",
            recovery="Inspect the scene and restore the pre-request snapshot if needed. Do not automatically retry this script. "
                     "External effects cannot be undone.",
            changes={"added": [{"name": "Pine 001", "type": "MESH", "vertices": 96}], "removed": [], "modified": []})
ERROR = RAISED["error"]
# A staged program whose commit failed and was rolled back (the scene's active camera cannot be deleted).
ROLLED_BACK = {"error_code": "VALIDATION", "success": False, "status": "failed", "final": True, "program_id": "p", "scene_changed": False,
               "partial_changes_possible": False, "native_exec_ms": 73.373, "operation_results": [], "checks": [],
               "error": "Native commit failed; live state rolled back: object_delete OBJECT_IN_USE: "
                        "it is a scene's active camera"}


def check_schemas():
    return dict(CAPABILITIES, **lookup("CHECKS", ["mesh_manifold", "aim"]))


class LoopCase(unittest.TestCase):
    def provider(self, plans, results):
        context = host()
        context.request_observation = Mock(side_effect=lambda callback: callback({"context_version": 2, "objects": []}))
        provider = ClaudeProvider(context, {})
        provider._turn = Mock(side_effect=[dict(plan, verify=plan.get("verify", False)) for plan in plans])
        from program_completion_fixtures import register_completion
        answers = iter(results)
        def native(name, arguments, **_kwargs):
            result = next(answers)
            register_completion(context, name, arguments, result)
            return result
        provider._native_call = Mock(side_effect=native)
        return provider, context

    def calls(self, provider):
        return [call.args[0] for call in provider._native_call.call_args_list]

    def messages(self, context):
        return [data["text"] for kind, data in context.events if kind == "message"]


class LookupsAreDataTests(LoopCase):
    """A lookup's result is data: only its ``error`` is a failure."""

    def test_check_schemas_are_not_read_as_verification_evidence(self):
        # The lookup of the CHECKS family returns a ``checks`` list of schemas.
        self.assertIn("inconclusive", native_result_problem(check_schemas()) or "",
                      "The outcome reader alone still misreads schemas; the loop must not ask it about lookups")
        provider, context = self.provider(
            [{"reply": "", "operations": [CHECK_SCHEMAS, program()]}], [check_schemas(), committed()])
        self.assertEqual(provider.run(None, "Build the blockout", observation={}), "completed")
        self.assertEqual(self.calls(provider), ["angelo_agent_capabilities", "angelo_scene_program"])
        self.assertFalse(any("inconclusive" in text for text in self.messages(context)))
        self.assertTrue(self.messages(context)[-1].startswith("Host-verified result"))

    def test_lookup_data_that_looks_like_an_outcome_is_still_data(self):
        for result in ({"success": True, "status": "failed", "receipts": []}, {"passed": False, "items": []},
                       {"operations": [{"success": False}]}, {"checks": [None]}):
            with self.subTest(result=result):
                self.assertIsNone(plan_recovery.lookup_problem(result))
        self.assertEqual(plan_recovery.lookup_problem({"error": "No such object"}), "No such object")
        self.assertIn("no usable", plan_recovery.lookup_problem(None))

    def test_a_standalone_verification_is_still_evidence_not_a_lookup(self):
        verify = {"tool": "angelo_scene_verify", "arguments": {"read_set": [], "checks": [
            {"kind": "object_count", "minimum": 0, "maximum": 0}]}}
        provider, _ = self.provider([{"reply": "", "operations": [verify]}],
                                    [{"success": False, "passed": False, "status": "fail", "checks": []}])
        self.assertEqual(provider.run(None, "Verify", observation={}), "failed")
        provider._turn.assert_called_once()


class FailedLookupTests(LoopCase):
    """A failed lookup goes back to the model in the same turn."""

    def test_a_failed_lookup_returns_every_result_to_the_model(self):
        plans = [{"reply": "Looking up the API.", "operations": [API, API, API_UNKNOWN]},
                 {"reply": "Continuing without it.", "operations": []}]
        provider, context = self.provider(plans, [API_RESULT, API_RESULT, API_ERROR])
        self.assertEqual(provider.run(None, "Build the material", observation={}), "completed")
        self.assertEqual(provider._turn.call_count, 2)
        prompt = provider._turn.call_args.args[1]
        head, body = prompt.split("\n", 1)
        self.assertIn("operations[2] (angelo_blender_api) failed", head)
        self.assertIn("Unknown installed RNA type", head)
        envelope = json.loads(body)["result"]
        self.assertIs(envelope["success"], False, "A plan with a failed lookup is not reported as a success")
        results = envelope["operations"]
        self.assertEqual([bool(result.get("error")) for result in results], [False, False, True])
        self.assertFalse(any(text.startswith("Unknown installed RNA type") for text in self.messages(context)),
                         "No terminal system row: the turn went on")

    def test_later_lookups_still_run_and_later_edits_do_not(self):
        plans = [{"reply": "", "operations": [API_UNKNOWN, API, program(), VIEW]}, {"reply": "Asking instead.", "operations": []}]
        provider, _ = self.provider(plans, [API_ERROR, API_RESULT])
        self.assertEqual(provider.run(None, "Build", observation={}), "completed")
        self.assertEqual(self.calls(provider), ["angelo_blender_api", "angelo_blender_api"])
        head = provider._turn.call_args.args[1].split("\n", 1)[0]
        self.assertIn("operations[0] (angelo_blender_api) failed", head)
        self.assertIn("operations[2..3] did not run", head)

    def test_a_failed_lookup_after_a_committed_edit_keeps_the_edit_and_gets_its_round(self):
        plans = [{"reply": "", "operations": [program(), API_UNKNOWN]}, {"reply": "", "operations": []}]
        provider, context = self.provider(plans, [committed(), API_ERROR])
        self.assertEqual(provider.run(None, "Build", observation={}), "completed")
        self.assertEqual(provider._turn.call_count, 2, "No host summary may hide the failed lookup")
        results = json.loads(provider._turn.call_args.args[1].split("\n", 1)[1])["result"]["operations"]
        self.assertEqual(results[0]["status"], "committed")

    def test_failed_lookups_do_not_spend_the_correction_budget_and_the_round_limit_still_ends_the_turn(self):
        plans = [{"reply": "", "operations": [API_UNKNOWN]}] * 16
        provider, _ = self.provider(plans, [API_ERROR] * 16)
        with self.assertRaisesRegex(Exception, "iteration limit"):
            provider.run(None, "Build", observation={})
        self.assertEqual(provider._turn.call_count, 16)

    def test_a_decision_by_the_person_or_the_host_still_ends_the_turn(self):
        for result in ({"error": "The user declined this operation. Do not repeat it."},
                       {"error": "Blender operation/approval expired", "expired": True},
                       {"error": "The agent turn ended before this was approved; nothing was run. Ask again if it is still needed.",
                        "revoked": True},
                       {"error": "Document changed or turn stopped"}):
            with self.subTest(result=result):
                provider, context = self.provider([{"reply": "", "operations": [DELEGATE, API]}], [result])
                self.assertEqual(provider.run(None, "Ask a collaborator", observation={}), "interrupted")
                provider._turn.assert_called_once()
                self.assertEqual(self.messages(context)[-1], result["error"])


class RolledBackEditTests(LoopCase):
    """An edit that failed without changing anything is corrected wherever it sits in the plan."""

    def test_a_rolled_back_commit_before_other_operations_gets_a_corrected_plan(self):
        plans = [{"reply": "", "operations": [program(), SCRIPT, VIEW], "verify": True},
                 {"reply": "", "operations": [program()]}]
        provider, context = self.provider(plans, [copy.deepcopy(ROLLED_BACK), committed()])
        self.assertEqual(provider.run(None, "Stage 1", observation={}), "completed")
        self.assertEqual(self.calls(provider), ["angelo_scene_program", "angelo_scene_program"],
                         "The script and the view after the failed program never ran")
        prompt = provider._turn.call_args.args[1]
        self.assertTrue(prompt.startswith("The host stopped your last plan before it changed the scene"))
        self.assertIn("OBJECT_IN_USE", prompt)
        self.assertTrue(any(text.startswith("The plan stopped before changing the scene") for text in self.messages(context)))

    def test_after_earlier_operations_ran_the_model_is_told_what_ran_and_only_the_rest_is_asked_for(self):
        plans = [{"reply": "", "operations": [SCRIPT, program(), VIEW]}, {"reply": "", "operations": [program()]}]
        provider, context = self.provider(plans, [copy.deepcopy(SCRIPT_RESULT), copy.deepcopy(ROLLED_BACK), committed()])
        self.assertEqual(provider.run(None, "Stage 1", observation={}), "completed")
        self.assertEqual(self.calls(provider), ["angelo_execute_python", "angelo_scene_program", "angelo_scene_program"])
        prompt = provider._turn.call_args.args[1]
        head, body = prompt.split("\n", 1)
        self.assertNotIn("before it changed the scene", prompt, "A script ran: the scene did change")
        self.assertIn("operations[1] (angelo_scene_program) failed and changed nothing", head)
        self.assertIn("operations[2] did not run", head)
        self.assertIn("do not repeat", head)
        envelope = json.loads(body)["result"]
        self.assertEqual((envelope["success"], envelope["operations"][0]["success"]), (False, True))

    def test_failed_edit_corrections_have_one_bounded_budget(self):
        plans = [{"reply": "", "operations": [program(), SCRIPT]}] * (FAILED_EDIT_CORRECTIONS + 1)
        provider, context = self.provider(plans, [copy.deepcopy(ROLLED_BACK)] * (FAILED_EDIT_CORRECTIONS + 1))
        self.assertEqual(provider.run(None, "Stage 1", observation={}), "interrupted")
        self.assertEqual(provider._turn.call_count, FAILED_EDIT_CORRECTIONS + 1)
        self.assertEqual(self.messages(context)[-1], ROLLED_BACK["error"])

    def test_an_edit_that_may_have_changed_the_scene_never_comes_back_as_unchanged(self):
        for results in ([dict(RAISED)], [dict(ROLLED_BACK, partial_changes_possible=True)],
                        [dict(ROLLED_BACK, scene_changed=None)], [dict(ROLLED_BACK, cleanup_pending=True)]):
            with self.subTest(results=results):
                self.assertIsNone(plan_recovery.unchanged_failure([SCRIPT, VIEW], results))



class RaisedScriptTests(LoopCase):
    """A reviewed script that raised goes back to the model with its line and the state of the scene."""

    def test_the_model_reads_the_error_its_line_and_the_partial_recovery_scope(self):
        plans = [{"reply": "", "operations": [SCRIPT, SCRIPT, VIEW], "verify": True},
                 {"reply": "Fixed the angle and built the pines.", "operations": [SCRIPT]}]
        provider, context = self.provider(plans, [copy.deepcopy(SCRIPT_RESULT), copy.deepcopy(RAISED), copy.deepcopy(SCRIPT_RESULT)])
        self.assertEqual(provider.run(None, "Stage 4", observation={}), "completed")
        self.assertEqual(self.calls(provider), ["angelo_execute_python"] * 3, "The view after the failed script never ran")
        head, body = provider._turn.call_args.args[1].split("\n", 1)
        self.assertIn("operations[1] (angelo_execute_python) raised: " + ERROR + ".", head)
        self.assertIn("The host restored Blender's Main data", head)
        self.assertIn("Image pixels and external effects remain unverified", head)
        self.assertIn("sending a corrected script is safe", head)  # Not "never blindly retry" (script_errors).
        self.assertNotIn("never blindly retry", head)
        self.assertNotIn("scene is as it was", head)
        self.assertIn("handles from before it are stale", head)
        self.assertIn("not undone", head)
        self.assertIn("operations[2] did not run", head)
        self.assertNotIn("still in the scene", head)
        results = json.loads(body)["result"]["operations"]
        self.assertEqual((results[0]["success"], results[1]["main_data_restored"], results[1]["line"]), (True, True, 143))
        self.assertIs(results[1]["rolled_back"], False)
        self.assertEqual(results[1]["traceback"][-1], "line 143, in build_pines: angle = math.radians.tau * index / count")
        self.assertFalse([text for text in self.messages(context) if "script" in text],
                         "The request context tells the person (script_errors.ScriptBudget); the loop adds nothing")

    def test_changes_that_could_not_be_undone_are_said_to_remain(self):
        plans = [{"reply": "", "operations": [SCRIPT]}, {"reply": "Removed the half-built pines and rebuilt them.", "operations": [SCRIPT]}]
        provider, context = self.provider(plans, [copy.deepcopy(KEPT), copy.deepcopy(SCRIPT_RESULT)])
        self.assertEqual(provider.run(None, "Stage 4", observation={}), "completed")
        head, body = provider._turn.call_args.args[1].split("\n", 1)
        self.assertIn("is still in the scene", head)
        self.assertIn("nothing was rolled back", head)
        self.assertIn("never assume the scene is as it was", head)
        self.assertNotIn("The host undid", head)
        self.assertIs(json.loads(body)["result"]["operations"][0]["partial_changes_possible"], True)

    def test_the_host_runs_nothing_of_its_own_between_the_failure_and_the_models_next_plan(self):
        plans = [{"reply": "", "operations": [SCRIPT]}, {"reply": "I stop here: the island mesh needs a decision.", "operations": []}]
        provider, context = self.provider(plans, [copy.deepcopy(RAISED)])
        self.assertEqual(provider.run(None, "Stage 4", observation={}), "completed")
        self.assertEqual(self.calls(provider), ["angelo_execute_python"], "No retry by the host")

    def test_a_request_corrects_a_bounded_number_of_scripts_and_then_the_turn_ends(self):
        plans = [{"reply": "", "operations": [SCRIPT]}] * (SCRIPT_CORRECTIONS + 2)
        provider, context = self.provider(plans, [copy.deepcopy(RAISED)] * (SCRIPT_CORRECTIONS + 2))
        self.assertEqual(provider.run(None, "Stage 4", observation={}), "interrupted")
        self.assertEqual(provider._turn.call_count, SCRIPT_CORRECTIONS + 1, "Five corrections, then the sixth raise ends it")
        kind, row = context.events[-1]
        self.assertEqual((row["role"], row["text"]), ("system", "This request's script corrections are used up; nothing more was tried."))

    def test_the_script_the_request_context_marked_ends_the_turn_without_a_second_row(self):
        plans = [{"reply": "", "operations": [SCRIPT]}] * 2
        provider, context = self.provider(plans, [dict(RAISED, script_corrections_used=True)])
        self.assertEqual(provider.run(None, "Stage 4", observation={}), "interrupted")
        self.assertEqual(provider._turn.call_count, 1)
        self.assertFalse([text for text in self.messages(context) if "script corrections" in text])

    def test_rejected_plans_and_raised_scripts_are_counted_apart(self):
        # Recorded: two plans the host rejected, then a script that raised. One shared budget of two ended that turn.
        rejected = {"reply": "", "operations": [{"tool": "angelo_execute_python", "arguments": {"description": "", "code": "result = 1"}}]}
        plans = [rejected] * CLAUDE_PLAN_REJECTIONS + [{"reply": "", "operations": [SCRIPT]}] * (SCRIPT_CORRECTIONS + 1)
        results = [copy.deepcopy(RAISED)] * SCRIPT_CORRECTIONS + [copy.deepcopy(SCRIPT_RESULT)]
        provider, context = self.provider(plans, results)
        self.assertEqual(provider.run(None, "Stage 4", observation={}), "completed")
        self.assertEqual(provider._turn.call_count, CLAUDE_PLAN_REJECTIONS + SCRIPT_CORRECTIONS + 1)
        # And the other way round: raised scripts leave the plan budget whole.
        plans = [{"reply": "", "operations": [SCRIPT]}] * SCRIPT_CORRECTIONS + [rejected] * CLAUDE_PLAN_REJECTIONS + [
            {"reply": "", "operations": [SCRIPT]}]
        provider, context = self.provider(plans, [copy.deepcopy(RAISED)] * SCRIPT_CORRECTIONS + [copy.deepcopy(SCRIPT_RESULT)])
        self.assertEqual(provider.run(None, "Stage 4", observation={}), "completed")

    def test_rejected_plans_and_failed_edits_are_counted_apart(self):
        # Recorded (plan-verify-facts A/B, playground message 3, 2 of 3 trials): two plans the host rejected, then a
        # staged program whose own check failed and rolled back. One shared budget of two ended that turn with nothing
        # built; the failed edit now has its own correction, as a raised script does.
        rejected = {"reply": "", "operations": [{"tool": "angelo_execute_python", "arguments": {"description": "", "code": "result = 1"}}]}
        plans = [rejected] * CLAUDE_PLAN_REJECTIONS + [{"reply": "", "operations": [program()]},
                                                       {"reply": "", "operations": [SCRIPT]}]
        provider, context = self.provider(plans, [copy.deepcopy(ROLLED_BACK), copy.deepcopy(SCRIPT_RESULT)])
        self.assertEqual(provider.run(None, "Stage 3", observation={}), "completed")
        self.assertEqual(provider._turn.call_count, CLAUDE_PLAN_REJECTIONS + 2)
        self.assertTrue(any("asking for a corrected plan" in text for text in self.messages(context)))
        # And the other way round: corrected failed edits leave the rejection budget whole.
        plans = [{"reply": "", "operations": [program()]}] * FAILED_EDIT_CORRECTIONS + [rejected] * CLAUDE_PLAN_REJECTIONS + [
            {"reply": "", "operations": [SCRIPT]}]
        provider, context = self.provider(plans, [copy.deepcopy(ROLLED_BACK)] * FAILED_EDIT_CORRECTIONS
                                          + [copy.deepcopy(SCRIPT_RESULT)])
        self.assertEqual(provider.run(None, "Stage 3", observation={}), "completed")
        self.assertEqual(provider._turn.call_count, FAILED_EDIT_CORRECTIONS + CLAUDE_PLAN_REJECTIONS + 1)

    def test_failed_edits_past_their_budget_end_the_turn_with_nothing_more_run(self):
        # Each budget still bounds the turn: a third failed edit (after two corrected ones) ends it, whatever the other.
        rejected = {"reply": "", "operations": [{"tool": "angelo_execute_python", "arguments": {"description": "", "code": "result = 1"}}]}
        plans = [rejected] * CLAUDE_PLAN_REJECTIONS + [{"reply": "", "operations": [program()]}] * (FAILED_EDIT_CORRECTIONS + 1)
        provider, context = self.provider(plans, [copy.deepcopy(ROLLED_BACK)] * (FAILED_EDIT_CORRECTIONS + 1))
        self.assertEqual(provider.run(None, "Stage 3", observation={}), "interrupted")
        self.assertEqual(provider._turn.call_count, CLAUDE_PLAN_REJECTIONS + FAILED_EDIT_CORRECTIONS + 1)
        self.assertEqual(self.messages(context)[-1], ROLLED_BACK["error"])
        self.assertEqual(self.calls(provider).count("angelo_scene_program"), FAILED_EDIT_CORRECTIONS + 1)

    def test_only_the_executors_own_account_is_a_script_error(self):
        for result in ({"error": "The user declined this operation. Do not repeat it."},
                       {"error": "Blender operation/approval expired", "expired": True},
                       {"error": "This chat is read-only; no change was applied"},
                       {"error": "This invocation ended without a durable final read-back; inspect before issuing a new "
                                 "invocation", "interrupted": True, "partial_changes_possible": True},
                       {"error": "Keep a visible 3D Viewport in the bound Blender window", "partial_changes_possible": True},
                       dict(ROLLED_BACK)):
            with self.subTest(result=result):
                self.assertIsNone(plan_recovery.raised_script([SCRIPT], [result]))
                provider, context = self.provider([{"reply": "", "operations": [SCRIPT]}] * 2, [result])
                self.assertIn(provider.run(None, "Stage 4", observation={}), ("interrupted", "failed"))
                self.assertEqual(provider._turn.call_count, 1, "An unknown state or a decision still ends the turn")
        self.assertIsNone(plan_recovery.raised_script([program()], [dict(RAISED)]), "Only a reviewed script")
        self.assertEqual(plan_recovery.raised_script([VIEW, SCRIPT], [{"views": []}, dict(KEPT)]), ERROR)


class CodexTurnTests(PlanTurnFixture):
    """The Codex runtime, for the same cases: its lookups and reviewed Python are in-turn calls."""

    def answers(self):
        return [(payload["success"], payload["contentItems"][0]["text"]) for _, payload in self.p.rpc.responses]

    def test_a_failed_lookup_and_check_schemas_are_tool_results_within_the_turn(self):
        self.results.update(angelo_blender_api=[API_ERROR], angelo_agent_capabilities=[check_schemas()],
                            angelo_scene_program=[committed()])
        status = self.run_turns([("tool", "angelo_blender_api", API_UNKNOWN["arguments"]),
                                 ("tool", "angelo_agent_capabilities", CHECK_SCHEMAS["arguments"]),
                                 ("message", self.plan_text(program()))])
        self.assertEqual(status, "completed")
        (failed, text), (found, schemas) = self.answers()
        self.assertFalse(failed)
        self.assertIn("Unknown installed RNA type", text)
        self.assertTrue(found)
        self.assertIn("mesh_manifold", schemas)
        self.assertEqual(len(self.starts()), 1, "One turn: the model read the error and went on")

    def test_a_script_that_raised_is_a_tool_result_within_the_turn(self):
        self.results.update(angelo_execute_python=[dict(RAISED), copy.deepcopy(SCRIPT_RESULT)])
        status = self.run_turns([("tool", "angelo_execute_python", SCRIPT["arguments"]),
                                 ("tool", "angelo_execute_python", SCRIPT["arguments"]),
                                 ("message", '{"reply": "Fixed the helper and ran it again.", "operations": []}')])
        self.assertEqual(status, "completed")
        (failed, text), (succeeded, _) = self.answers()
        self.assertFalse(failed)
        answer = json.loads(text)
        self.assertEqual((answer["line"], answer["main_data_restored"], answer["traceback"]), (143, True, RAISED["traceback"]))
        self.assertIs(answer["rolled_back"], False)
        self.assertTrue(succeeded)

    def test_a_request_corrects_a_bounded_number_of_scripts_and_then_runs_no_more(self):
        # Through the real request context: the host's dispatch is the stand-in, not the context's.
        del self.context.request_tool
        def dispatch(key, name, arguments, callback, request_fence=None, delivery_owner=None, result_observer=None,
                     completion_sink=None):
            def complete(result):
                if result_observer is not None:
                    result_observer(result)
                callback(result)
            self.request_tool(key, name, arguments, complete)
        self.c.request_tool = dispatch
        self.results["angelo_execute_python"] = [copy.deepcopy(RAISED)]
        scripts = [("tool", "angelo_execute_python", SCRIPT["arguments"])] * (SCRIPT_CORRECTIONS + 3)
        status = self.run_turns(scripts + [("message", '{"reply": "The pines script keeps failing.", "operations": []}')])
        self.assertEqual(status, "completed")
        self.assertEqual(len(self.dispatched), SCRIPT_CORRECTIONS + 1, "No script runs after the corrections are used")
        answers = [json.loads(text) for _, text in self.answers()]
        self.assertFalse(any("script_corrections_used" in answer for answer in answers[:SCRIPT_CORRECTIONS]))
        self.assertTrue(all(answer["script_corrections_used"] for answer in answers[SCRIPT_CORRECTIONS:]))
        self.assertIn("no further script runs", answers[-1]["error"])
        rows = [row["text"] for row in self.c.state["messages"] if row["role"] == "system"]
        self.assertEqual(rows.count("The script stopped with an error. Scene data was restored, but image pixels and "
                                    "external effects were not verified:\n" + ERROR), SCRIPT_CORRECTIONS + 1)
        self.assertEqual(rows.count("This request's script corrections are used up; nothing more was tried."), 1)

    def test_a_rolled_back_commit_before_other_plan_items_comes_back_for_the_rest(self):
        self.results["angelo_scene_program"] = [copy.deepcopy(ROLLED_BACK), committed()]
        # The fixture has no UI pump to build the correction's observation.
        self.context.request_observation = lambda receive: receive({"context_version": 2, "objects": []})
        second = copy.deepcopy(program())
        second["arguments"]["operations"][0]["parameters"].update(name="Second Cube", alias="second")
        second["arguments"]["expected_checks"] = []
        status = self.run_turns([("message", self.plan_text(program(), second))], [("message", self.plan_text(program()))])
        self.assertEqual(status, "completed")
        prompt = self.starts()[1]["input"][0]["text"]
        self.assertIn("stopped at operations[0] (angelo_scene_program), which changed nothing", prompt)
        self.assertIn("OBJECT_IN_USE", prompt)
        self.assertEqual(len(self.dispatched), 2, "The item after the failed one never ran")


if __name__ == "__main__":
    unittest.main()
