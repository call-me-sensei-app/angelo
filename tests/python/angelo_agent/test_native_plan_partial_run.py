# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An invalid lookup or job start never blocks the rest of a Claude plan; any other invalid edit still does.

A valid 8-9 kB build script was refused whole because a view beside it had a
bad argument, and the model sent the script again, a full round each time.
``plan_validation.screen_plan`` keeps every
mutating operation all-or-nothing and refuses an invalid lookup on its own,
answering it with the error a refused call gets; an ``angelo_view`` request
with exactly one meaning is normalised instead (``view_spec.normalize_view``).
The loop is driven with mocked rounds and native calls: no model, process or
Blender (doc/angelo/agent_integration.rst, "Operations the host refused alone").
"""

import copy
import itertools
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import plan_validation  # noqa: E402
from angelo_agent.plan_kinds import CLAUDE_CORE_ACTIONS  # noqa: E402
from angelo_agent.plan_schema import PlanRejected, rejection_prompt  # noqa: E402
from angelo_agent.plan_validation import plan_problems, refused_result, screen_plan  # noqa: E402
from angelo_agent.protocol import RpcError  # noqa: E402
from angelo_agent.providers import CLAUDE_PLAN_REJECTIONS  # noqa: E402
from angelo_agent.tool_specs import TOOL_MAP, validate_call  # noqa: E402
from angelo_agent.view_spec import normalize_view  # noqa: E402
from test_native_round_savings import committed, program  # noqa: E402
from test_native_turn_recovery import LoopCase, ROLLED_BACK, SCRIPT, SCRIPT_RESULT  # noqa: E402

UUID = "84dc78ab-de9c-4b72-b060-91c20c3c0e32"
HANDLE = "scn:" + "a" * 32 + ":7"


def view(views, shading="SOLID", **extra):
    return {"tool": "angelo_view", "arguments": dict({"views": list(views), "shading": shading}, **extra)}


# The refusal classes recorded in one-shot runs, and a few more of each kind.
SPLIT = view(["SCENE_CAMERA", "FRONT"], "RENDERED")
FRAME = view(["SCENE_CAMERA"], "RENDERED", frame=60)
REUSED = view([UUID], "RENDERED")
LIT_FRAME = view(["FRONT", "ISO"], "LIT", frame=60)
JOB = {"tool": "angelo_job_start", "arguments": {"kind": "RENDER_STILL", "output": "still.png", "frame_end": 60}}
GOOD_JOB = {"tool": "angelo_job_start", "arguments": {"kind": "RENDER_STILL", "output": "still.png"}}
UNEXPECTED_JOB = {"tool": "angelo_job_start", "arguments": {"kind": "RENDER_STILL", "output": "still.png", "camera": "Cam"}}
LAST_LOOK = view(["SCENE_CAMERA"], "RENDERED", image="LAST_JOB")
PICTURE = {"tool": "angelo_view", "arguments": {"views": ["FIT"], "image": "j3"}}
BAD_PROGRAM = dict(program(), arguments=dict(program()["arguments"], mode="NOPE"))
BAD_SCRIPT = {"tool": "angelo_execute_python", "arguments": {"description": "Build", "code": "def broken(:\n"}}
BAD_VERIFY = {"tool": "angelo_scene_verify", "arguments": {"read_set": [], "checks": [{"kind": "no_such_check"}]}}
BAD_CANCEL = {"tool": "angelo_job_cancel", "arguments": {"job": "not a job"}}
API = {"tool": "angelo_blender_api", "arguments": {"domain": "RNA", "name": "SubsurfModifier", "member": "",
                                                    "offset": 0, "limit": 64}}
BAD_API = {"tool": "angelo_blender_api", "arguments": {"domain": "NOPE", "name": "x", "member": "", "offset": 0,
                                                        "limit": 64}}
LOOK = view(["SCENE_CAMERA"], "RENDERED")


def message(operation):
    try:
        validate_call(operation["tool"], operation["arguments"])
    except ValueError as error:
        return str(error)
    raise AssertionError("valid: %r" % operation)


def view_result(arguments):
    shading = arguments.get("shading", "SOLID")
    return {"success": True, "shading": shading, "views": [
        {"id": "%s-%s" % (angle, shading), "name": "%s view · %s" % (angle, shading.lower()), "view": angle}
        for angle in arguments["views"]]}


class PlanCase(LoopCase):
    def provider(self, plans, results):
        provider, context = super().provider(plans, results)
        context.references.tool_images = Mock(side_effect=lambda result: list(result.get("views") or []))
        return provider, context

    def rounds(self, provider):
        return [call.args[1] for call in provider._turn.call_args_list]

    def continuation(self, provider):
        head, body = provider._turn.call_args.args[1].split("\n", 1)
        return head, json.loads(body)["result"]


class LookupRefusedAloneTests(PlanCase):
    """The plan's valid operations run; the invalid lookup is answered with its error and the turn goes on."""

    def test_a_script_runs_when_only_a_view_beside_it_is_invalid(self):
        plans = [{"reply": "", "operations": [SCRIPT, FRAME]}, {"reply": "Built.", "operations": []}]
        provider, context = self.provider(plans, [copy.deepcopy(SCRIPT_RESULT)])
        self.assertEqual(provider.run(None, "Build the bridge", observation={}), "completed")
        self.assertEqual(self.calls(provider), ["angelo_execute_python"], "The view never ran")
        head, results = self.continuation(provider)
        self.assertIn("operations[1] (angelo_view) was refused before the plan ran, so it did not run and changed "
                      "nothing: " + message(FRAME)[:80], head)
        self.assertIn("Ask again with corrected arguments", head)
        self.assertNotIn("did not run.", head.split("changed nothing:")[0])
        self.assertTrue(results["operations"][0]["success"])
        self.assertEqual(results["operations"][1], {"error": message(FRAME), "error_code": "VALIDATION"})
        rows = self.messages(context)
        self.assertTrue(any(row.startswith("Host skipped these steps") and
                            "operations[1] (angelo_view): " + message(FRAME) in row for row in rows))
        self.assertFalse(any(row.startswith("Host rejected") for row in rows))

    def test_the_plan_stops_at_a_refused_lookup(self):
        # A lookup can be a point the plan waits at, or have an effect its contract
        # does not count, so nothing the model wrote after it runs.
        plans = [{"reply": "", "operations": [SCRIPT, BAD_API, API, SCRIPT, API]}, {"reply": "Built.", "operations": []}]
        provider, _ = self.provider(plans, [copy.deepcopy(SCRIPT_RESULT)])
        self.assertEqual(provider.run(None, "Build", observation={}), "completed")
        self.assertEqual(self.calls(provider), ["angelo_execute_python"])
        head, results = self.continuation(provider)
        self.assertIn("operations[1] (angelo_blender_api) was refused", head)
        self.assertIn("operations[2..4] did not run", head)
        self.assertEqual([bool(row.get("error")) for row in results["operations"]], [False, True])
        self.assertIs(results["success"], False, "A plan with a refused lookup is not reported as a success")
        for operations in ([BAD_API, FRAME, SCRIPT, API], [FRAME, program()], [BAD_CANCEL, GOOD_JOB]):
            with self.subTest(operations=[operation["tool"] for operation in operations]):
                self.assertEqual(screen_plan(operations, None).problems, plan_problems(operations, None)[0],
                                 "Nothing would run before the held-back edit: rejected as before")

    def test_a_refused_cancel_never_lets_the_job_or_edit_after_it_run(self):
        # angelo_job_cancel is declared read-only, yet it stops a job.
        for later in (GOOD_JOB, SCRIPT):
            with self.subTest(later=later["tool"]):
                plans = [{"reply": "", "operations": [SCRIPT, BAD_CANCEL, later]}, {"reply": "", "operations": []}]
                provider, _ = self.provider(plans, [copy.deepcopy(SCRIPT_RESULT)])
                self.assertEqual(provider.run(None, "Cancel j3 and render again", observation={}), "completed")
                self.assertEqual(self.calls(provider), ["angelo_execute_python"])
                self.assertIn("operations[2] did not run", self.continuation(provider)[0])

    def test_an_edit_waiting_behind_a_refused_look_at_the_running_job_does_not_run(self):
        # A look at LAST_JOB waits for the render; scene edits are refused while it runs.
        bad_look = view(["SCENE_CAMERA"], "LIT", image="LAST_JOB")
        plans = [{"reply": "", "operations": [GOOD_JOB, bad_look, SCRIPT]}, {"reply": "", "operations": []}]
        provider, _ = self.provider(plans, [{"success": True, "job": "j1", "status": "running"}])
        self.assertEqual(provider.run(None, "Render, look, then brighten", observation={}), "completed")
        self.assertEqual(self.calls(provider), ["angelo_job_start"])
        head = self.continuation(provider)[0]
        self.assertIn("operations[1] (angelo_view) was refused", head)
        self.assertIn("operations[2] did not run", head)

    def test_the_refusal_carries_the_fix_hint_the_rejection_gave(self):
        lines = plan_problems([SCRIPT, FRAME, BAD_CANCEL], None)[0]
        screened = screen_plan([SCRIPT, FRAME, BAD_CANCEL], None)
        self.assertEqual(screened.problems, [])
        self.assertEqual([screened.refused[1], screened.refused[2]], lines, "The same lines, in the same order")
        cancel = screened.results[2]
        self.assertEqual(cancel["error"], message(BAD_CANCEL))
        self.assertIn("angelo_job_cancel", json.dumps(cancel["exact_schemas"]),
                      "A deferred lookup gets its exact schema back, as in the rejection prompt")

    def test_refusals_cost_what_rejections_did_and_one_sent_again_unchanged_runs_nothing(self):
        # A plan with operations refused alone costs one correction, as its rejection did, and
        # a refusal is deterministic, so a plan sending one again unchanged is rejected whole.
        for name, plans in (("new mistakes", [[SCRIPT, view(["SCENE_CAMERA"], "RENDERED", frame=60 + index)]
                                              for index in range(16)]),
                            ("the same mistake", [[SCRIPT, FRAME]] * 16), ("a job start", [[SCRIPT, JOB]] * 16),
                            ("behind a refusal", [[SCRIPT, view(["SCENE_CAMERA"], "RENDERED", frame=index), JOB]
                                                  for index in range(16)])):
            with self.subTest(name):
                provider, _ = self.provider([{"reply": "", "operations": plan} for plan in plans],
                                            [copy.deepcopy(SCRIPT_RESULT) for _ in range(16)])
                with self.assertRaises(PlanRejected):
                    provider.run(None, "Build", observation={})
                self.assertEqual(provider._turn.call_count, CLAUDE_PLAN_REJECTIONS + 1, "No more rounds than before")
                self.assertLessEqual(self.calls(provider).count("angelo_execute_python"), CLAUDE_PLAN_REJECTIONS)
                self.assertNotIn("angelo_job_start", self.calls(provider))
                if name != "new mistakes" and name != "behind a refusal":
                    self.assertEqual(self.calls(provider).count("angelo_execute_python"), 1,
                                     "A plan repeating a refusal runs nothing")

    def test_a_plan_with_nothing_valid_left_is_rejected_as_before(self):
        for operations in ([FRAME], [FRAME, BAD_API], [LIT_FRAME, REUSED]):
            with self.subTest(operations=operations):
                provider, context = self.provider([{"reply": "", "operations": operations}] * (CLAUDE_PLAN_REJECTIONS + 1), [])
                with self.assertRaises(PlanRejected):
                    provider.run(None, "Look", observation={})
                provider._native_call.assert_not_called()
                self.assertEqual(self.rounds(provider)[1], rejection_prompt(
                    plan_problems(operations, None)[0], operations, CLAUDE_CORE_ACTIONS))

    def test_a_refused_lookup_after_the_operation_that_stopped_the_plan_is_still_explained(self):
        # The model must not send the refused lookup again unchanged.
        plans = [{"reply": "", "operations": [program(), FRAME]}, {"reply": "", "operations": [program()]}]
        provider, _ = self.provider(plans, [copy.deepcopy(ROLLED_BACK), committed()])
        self.assertEqual(provider.run(None, "Stage 1", observation={}), "completed")
        prompt = self.rounds(provider)[1]
        self.assertTrue(prompt.startswith("The host stopped your last plan before it changed the scene"))
        self.assertIn("operations[1] (angelo_view) was refused before the plan ran", prompt)
        self.assertIn(message(FRAME)[:60], prompt)


class MutationsStayAllOrNothingTests(PlanCase):
    """One invalid edit, program, script or verification still rejects the whole plan, exactly as before."""

    def test_an_invalid_mutation_rejects_the_whole_plan_with_todays_prompt(self):
        for operations in ([SCRIPT, FRAME, BAD_PROGRAM], [BAD_PROGRAM, SCRIPT], [BAD_SCRIPT, view(["FRONT"])],
                           [SCRIPT, BAD_VERIFY], [SPLIT, BAD_SCRIPT], [BAD_CANCEL, BAD_PROGRAM], [SCRIPT, JOB, BAD_SCRIPT],
                           [JOB, BAD_PROGRAM], [JOB, LAST_LOOK], [JOB, SCRIPT], [FRAME, JOB]):
            with self.subTest(operations=[operation["tool"] for operation in operations]):
                provider, _ = self.provider([{"reply": "", "operations": operations},
                                             {"reply": "Asking instead.", "operations": []}], [])
                self.assertEqual(provider.run(None, "Build", observation={}), "completed")
                provider._native_call.assert_not_called()
                self.assertEqual(self.rounds(provider)[1], rejection_prompt(
                    plan_problems(operations, None)[0], operations, CLAUDE_CORE_ACTIONS))

    def test_the_screen_rejects_exactly_what_plan_problems_rejects_whenever_a_non_lookup_is_invalid(self):
        pool = [SCRIPT, BAD_SCRIPT, JOB, GOOD_JOB, BAD_VERIFY, program(), BAD_PROGRAM, FRAME, SPLIT, REUSED, BAD_API,
                API, BAD_CANCEL, LOOK, LAST_LOOK, {"tool": "angelo_no_such_tool", "arguments": {}}]
        checked = partial = 0
        for size in (1, 2, 3):
            for operations in itertools.product(pool, repeat=size):
                operations = [copy.deepcopy(operation) for operation in operations]
                problems, resolved = plan_problems(operations, None)
                screened = screen_plan(operations, None)
                invalid = [operations[int(line.split("]", 1)[0].split("[", 1)[1])]["tool"] for line in problems]
                if any(not plan_validation._alone(tool) for tool in invalid):
                    checked += 1
                    self.assertEqual(screened.problems, problems)
                elif not problems:
                    self.assertEqual((screened.problems, screened.operations, screened.refused, screened.companions,
                                      screened.stops), ([], resolved, {}, {}, {}), "A valid plan is never changed")
                elif screened.problems:
                    self.assertEqual(screened.problems, problems, "Nothing valid left: rejected exactly as before")
                    self.assertEqual(screened.dispatched(), [])
                else:
                    partial += 1
                    stop = screened.stop()
                    self.assertNotIn(None, [screened.results.get(index) for index in {**screened.refused, **screened.stops}])
                    mutating = [op for op in screened.dispatched() if not plan_validation._lookup(op["tool"])]
                    self.assertEqual(mutating, [op for index, op in enumerate(resolved[:stop])
                                                if not plan_validation._lookup(op["tool"])],
                                     "Every mutating operation before the first refused job start runs as validated; "
                                     "none after it")
        self.assertGreater(checked, 1500)
        self.assertGreater(partial, 300)

    def test_classification_follows_each_tools_declared_contract(self):
        for name, tool in TOOL_MAP.items():
            with self.subTest(tool=name):
                expected = tool["annotations"]["readOnlyHint"] and name != "angelo_scene_verify"
                self.assertEqual(plan_validation._lookup(name), expected)
        self.assertFalse(plan_validation._lookup("angelo_unknown"))

    def test_a_normaliser_is_never_applied_to_a_mutating_operation(self):
        normalisers = {"angelo_job_start": lambda arguments: ([GOOD_JOB["arguments"]], "fixed"),
                       "angelo_scene_program": lambda arguments: ([program()["arguments"]], "fixed")}
        with patch.dict(plan_validation.NORMALIZERS, normalisers):
            screened = screen_plan([SCRIPT, JOB], None)
            self.assertEqual((list(screened.stops), screened.notes, screened.dispatched()), ([1], {}, [SCRIPT]))
            screened = screen_plan([SCRIPT, BAD_PROGRAM], None)
            self.assertEqual(screened.problems, plan_problems([SCRIPT, BAD_PROGRAM], None)[0])

    def test_contract_breaches_still_fail_the_request(self):
        with self.assertRaisesRegex(RpcError, "does not allow"):
            screen_plan([SCRIPT, FRAME], frozenset({"angelo_execute_python"}))
        with self.assertRaisesRegex(RpcError, "Invalid native plan operation"):
            screen_plan([SCRIPT, {"tool": "angelo_view"}], None)

    def test_a_refused_result_mirrors_a_call_refused_at_admission(self):
        try:
            validate_call(FRAME["tool"], FRAME["arguments"])
        except ValueError as error:
            self.assertEqual(refused_result(FRAME, error), {"error": str(error), "error_code": "VALIDATION"})


class JobStartRefusedAloneTests(PlanCase):
    """An invalid job start is a start refused at admission: what came before it runs, nothing after it does."""

    def test_a_script_runs_when_only_the_job_start_after_it_is_invalid(self):
        for job in (JOB, UNEXPECTED_JOB):
            with self.subTest(job=job["arguments"]):
                plans = [{"reply": "", "operations": [SCRIPT, job, LAST_LOOK]},
                         {"reply": "", "operations": [GOOD_JOB]}, {"reply": "Rendering.", "operations": []}]
                provider, context = self.provider(plans, [copy.deepcopy(SCRIPT_RESULT),
                                                          {"success": True, "job": "j1", "status": "running"}])
                self.assertEqual(provider.run(None, "Build and render", observation={}), "completed")
                self.assertEqual(self.calls(provider), ["angelo_execute_python", "angelo_job_start"],
                                 "The invalid start and the look at LAST_JOB after it never ran")
                self.assertEqual(provider._native_call.call_args_list[1].args[1], GOOD_JOB["arguments"])
                head, body = self.rounds(provider)[1].split("\n", 1)
                self.assertIn("operations[1] (angelo_job_start) failed and changed nothing: " + message(job)[:60], head)
                self.assertIn("operations[2] did not run", head)
                refused = json.loads(body)["result"]["operations"][1]
                self.assertEqual((refused["error"], refused["error_code"], refused["scene_changed"]),
                                 (message(job), "VALIDATION", False))
                self.assertIn("angelo_job_start", json.dumps(refused["exact_schemas"]))
                rows = self.messages(context)
                self.assertTrue(any(row.startswith("The plan stopped at operations[1]") for row in rows))
                self.assertTrue(any(row.startswith("Host skipped these steps") and "operations[1] (angelo_job_start)"
                                    in row for row in rows), "The person reads the refused start")
                self.assertFalse(any(row.startswith("Host rejected") for row in rows))

    def test_nothing_valid_before_the_job_start_is_rejected_as_before(self):
        for operations in ([JOB], [JOB, LAST_LOOK], [FRAME, JOB, SCRIPT], [JOB, LOOK]):
            with self.subTest(operations=[operation["tool"] for operation in operations]):
                screened = screen_plan(operations, None)
                self.assertEqual(screened.problems, plan_problems(operations, None)[0])

    def test_a_refused_start_is_a_correction_within_the_requests_budget(self):
        plans = [{"reply": "", "operations": [SCRIPT, JOB]}] + [
            {"reply": "", "operations": [LOOK, dict(JOB, arguments=dict(JOB["arguments"], output="v%d.png" % index))]}
            for index in range(3)]
        provider, context = self.provider(plans, [copy.deepcopy(SCRIPT_RESULT)] + [view_result(LOOK["arguments"])] * 3)
        with self.assertRaises(PlanRejected):   # Past the budget the plan is rejected whole, as before.
            provider.run(None, "Render", observation={})
        self.assertEqual(provider._turn.call_count, CLAUDE_PLAN_REJECTIONS + 1)
        self.assertEqual(self.calls(provider), ["angelo_execute_python", "angelo_view"])

    def test_a_budget_spent_on_refusals_still_escalates_on_auto(self):
        # A request that spends its correction budget on refusals, with nothing committed,
        # ends as a rejected plan, so Auto still asks a stronger model (model_routing.next_step), as before the
        # partial run. A request whose partial runs committed edits gets the suggestion every failed edit gets.
        from angelo_agent import model_routing
        plans = [{"reply": "", "operations": [LOOK, dict(JOB, arguments=dict(JOB["arguments"], output="v%d.png" % i))]}
                 for i in range(CLAUDE_PLAN_REJECTIONS + 1)]
        provider, _ = self.provider(plans, [view_result(LOOK["arguments"])] * len(plans))
        with self.assertRaises(PlanRejected):
            provider.run(None, "Render", observation={})
        self.assertEqual(set(self.calls(provider)), {"angelo_view"}, "Only lookups ran: nothing was committed")
        route = model_routing.Route("connection", "sonnet", "fast", "low", "5m", True, False, "Auto",
                                    (model_routing.Rung("opus", "strong", "low"),))
        step = model_routing.next_step(route, "plan_rejected", committed=False, rejections=CLAUDE_PLAN_REJECTIONS)
        self.assertEqual(step.action, "escalate")

    def test_a_refused_start_costs_a_correction_wherever_the_run_stops(self):
        # Charged at the screen, so a start the run never reaches (a failed lookup, a raised script, an
        # open self-check) or one past the budget takes no more rounds than its rejected plan did.
        from test_native_turn_recovery import API_ERROR, API_UNKNOWN, RAISED
        failing = dict(copy.deepcopy(SCRIPT_RESULT), self_checks=[
            {"name": "valley", "measured": 5.0, "target": 5.2, "tolerance": 0.05, "passed": False}])
        def job(index):
            return dict(JOB, arguments=dict(JOB["arguments"], output="v%d.png" % index))
        for name, first, answer in (("failed lookup", API_UNKNOWN, API_ERROR), ("raised script", SCRIPT, RAISED),
                                    ("open self-check", SCRIPT, failing)):
            with self.subTest(name):
                plans = [{"reply": "", "operations": [first, job(index)]} for index in range(16)]
                provider, _ = self.provider(plans, [copy.deepcopy(answer) for _ in range(16)])
                try:
                    provider.run(None, "Render", observation={})
                except PlanRejected:
                    pass
                self.assertLessEqual(provider._turn.call_count, CLAUDE_PLAN_REJECTIONS + 1)
                self.assertNotIn("angelo_job_start", self.calls(provider))
        plans = [{"reply": "", "operations": [BAD_PROGRAM]}] * CLAUDE_PLAN_REJECTIONS + [
            {"reply": "", "operations": [SCRIPT, JOB]}]
        provider, _ = self.provider(plans, [copy.deepcopy(SCRIPT_RESULT)])
        with self.assertRaises(PlanRejected):
            provider.run(None, "Render", observation={})
        provider._native_call.assert_not_called()   # Past the budget: nothing of the plan runs, as before.

    def test_an_invalid_start_after_a_failed_self_check_is_a_start_refused_at_admission(self):
        # A self-check never holds a job (self_checks, "tools aid, never handicap"), so an invalid start after a
        # failing one is the correctable refusal any invalid start is; the model reads the check in the script's
        # result and the person reads it, as a fact, where the turn ends.
        failing = dict(copy.deepcopy(SCRIPT_RESULT), self_checks=[
            {"name": "valley", "measured": 5.0, "target": 5.2, "tolerance": 0.05, "passed": False}])
        plans = [{"reply": "", "operations": [SCRIPT, JOB, LAST_LOOK]}, {"reply": "Done.", "operations": []}]
        provider, context = self.provider(plans, [failing])
        self.assertEqual(provider.run(None, "Build and render", observation={}), "completed")
        prompt = self.rounds(provider)[1]
        self.assertIn("operations[1] (angelo_job_start) failed and changed nothing: " + message(JOB)[:60], prompt)
        self.assertIn("operations[2] did not run", prompt)
        self.assertNotIn('"deferred"', prompt)
        self.assertIn('"valley"', prompt)
        self.assertEqual(self.calls(provider), ["angelo_execute_python"])
        rows = self.messages(context)
        self.assertTrue(any(row.startswith("The plan stopped at operations[1]") for row in rows))
        self.assertIn("The script's self-checks still fail:\nvalley: measured 5.0", rows[-1])

    def test_the_receipt_counts_the_refused_start(self):
        provider, context = self.provider([{"reply": "", "operations": [SCRIPT, JOB]}, {"reply": "", "operations": []}],
                                          [copy.deepcopy(SCRIPT_RESULT)])
        context.record_refused_plan = Mock()
        provider.run(None, "Render", observation={})
        context.record_refused_plan.assert_called_once_with([SCRIPT, JOB], [plan_problems([SCRIPT, JOB], None)[0][0]])

    def test_a_refused_lookup_beside_a_refused_start_is_reported_too(self):
        screened = screen_plan([SCRIPT, FRAME, JOB, LAST_LOOK], None)
        self.assertEqual((list(screened.refused), list(screened.stops), screened.dispatched()), ([1], [2], [SCRIPT]))
        plans = [{"reply": "", "operations": [SCRIPT, JOB, FRAME]}, {"reply": "", "operations": []}]
        provider, _ = self.provider(plans, [copy.deepcopy(SCRIPT_RESULT)])
        provider.run(None, "Render", observation={})
        head = self.continuation(provider)[0]
        self.assertIn("operations[1] (angelo_job_start) failed and changed nothing", head)
        self.assertIn("operations[2] (angelo_view) was refused before the plan ran", head)
        self.assertNotIn("operations[1] (angelo_job_start) was refused", head)


class NormalisedResultTests(unittest.TestCase):
    def test_the_model_reads_each_part_without_host_bookkeeping(self):
        # Timing is left out of a normalised slot's parts as of any result.
        from angelo_agent.model_results import model_result
        part = {"success": True, "views": [], "native_exec_ms": 3.2, "native_dispatch_wait_ms": 1.0}
        shown = model_result("angelo_view", {"success": True, "normalized": "note", "results": [part, dict(part)]})
        self.assertEqual(shown["results"], [{"success": True, "views": []}] * 2)


class ViewNormalisationTests(PlanCase):
    """RENDERED with the scene camera and other cameras has one meaning: render the camera, LIT the others."""

    def test_rendered_with_other_cameras_becomes_a_render_and_a_lit_look(self):
        for views, others in ((["SCENE_CAMERA", "FRONT"], ["FRONT"]), (["FRONT", "SCENE_CAMERA"], ["FRONT"]),
                              (["SCENE_CAMERA", "FRONT", "TOP"], ["FRONT", "TOP"]), (["ISO", "SCENE_CAMERA", "FIT"], ["ISO", "FIT"])):
            with self.subTest(views=views):
                arguments = {"targets": [HANDLE], "views": views, "shading": "RENDERED", "resolution": 768}
                calls, note = normalize_view(arguments)
                self.assertEqual(calls, [dict(arguments, views=["SCENE_CAMERA"]),
                                         dict(arguments, views=others, shading="LIT")])
                self.assertIn("drew %s with LIT" % ", ".join(others), note)
                for call in calls:
                    validate_call("angelo_view", call)

    def test_an_existing_picture_is_shown_whatever_camera_or_default_shading_was_written(self):
        for arguments in ({"views": ["FIT"], "image": "j3", "resolution": 768},
                          {"views": ["FRONT"], "shading": "RENDERED", "image": "LAST_JOB", "region": [0, 0, 0.5, 0.5]},
                          {"views": ["SCENE_CAMERA"], "image": "RENDER_RESULT"},
                          {"views": [UUID], "image": "j2", "frame": 4, "file": "frame_0004.png"}):
            with self.subTest(arguments=arguments):
                calls, note = normalize_view(arguments)
                self.assertEqual(calls, [dict(arguments, shading="RENDERED", views=["SCENE_CAMERA"])])
                self.assertIn("existing picture", note)
                validate_call("angelo_view", calls[0])

    def test_requests_with_more_than_one_meaning_are_left_alone(self):
        for arguments in (REUSED["arguments"], view(["FRONT"], "RENDERED")["arguments"], FRAME["arguments"],
                          view(["SCENE_CAMERA", UUID], "RENDERED")["arguments"],
                          LIT_FRAME["arguments"], dict(SPLIT["arguments"], frame=60),
                          dict(SPLIT["arguments"], region=[0.0, 0.0, 0.5, 0.5]),
                          dict(SPLIT["arguments"], image="LAST_JOB"), view(["FIT"], "LIT", image="j3")["arguments"],
                          {"views": ["FIT", "FRONT"], "image": "j3"}, {"views": ["FIT"], "image": "j3", "frames": [1, 2]},
                          {"views": ["FIT"], "image": "j3", "group": "Bones"}, {"views": ["FIT"], "image": "nope"},
                          {"views": ["FIT"], "image": "UV"},
                          view(["SCENE_CAMERA", "SCENE_CAMERA"], "RENDERED")["arguments"],
                          view(["SCENE_CAMERA", "FRONT", "FRONT"], "RENDERED")["arguments"],
                          view(["SCENE_CAMERA", "FRONT", "TOP", "ISO"], "RENDERED")["arguments"],
                          view(["SCENE_CAMERA", "front"], "RENDERED")["arguments"],
                          dict(SPLIT["arguments"], resolution=640), dict(SPLIT["arguments"], targets=["Cube"]),
                          view(["SCENE_CAMERA", "FRONT"], "SOLID")["arguments"], LOOK["arguments"], None, [], "RENDERED"):
            with self.subTest(arguments=arguments):
                self.assertIsNone(normalize_view(arguments))

    def test_the_plan_runs_both_looks_in_the_views_slot_and_the_result_says_so(self):
        plans = [{"reply": "", "operations": [SCRIPT, SPLIT]}, {"reply": "It reads well.", "operations": []}]

        def native(name, arguments, **_kwargs):
            return copy.deepcopy(SCRIPT_RESULT) if name == "angelo_execute_python" else view_result(arguments)
        provider, context = self.provider(plans, [])
        provider._native_call = Mock(side_effect=native)
        self.assertEqual(provider.run(None, "Build and look", observation={}), "completed")
        self.assertEqual([(call.args[0], call.args[1].get("shading"), call.args[1].get("views"))
                          for call in provider._native_call.call_args_list],
                         [("angelo_execute_python", None, None), ("angelo_view", "RENDERED", ["SCENE_CAMERA"]),
                          ("angelo_view", "LIT", ["FRONT"])])
        head, results = self.continuation(provider)
        self.assertNotIn("refused", head)
        slot = results["operations"][1]
        self.assertIn("drew FRONT with LIT", slot["normalized"])
        self.assertEqual([part["shading"] for part in slot["results"]], ["RENDERED", "LIT"])
        self.assertEqual(len(results["operations"]), 2, "Results stay aligned with the model's operations")
        self.assertFalse(any(row.startswith(("Host skipped", "Host rejected")) for row in self.messages(context)))

    def test_a_failed_part_is_the_lookups_failure_and_a_decided_one_ends_the_turn(self):
        def native(failure):
            def call(name, arguments, **_kwargs):
                if name == "angelo_execute_python":
                    return copy.deepcopy(SCRIPT_RESULT)
                return dict(failure) if arguments.get("shading") == "RENDERED" else view_result(arguments)
            return call
        provider, _ = self.provider([{"reply": "", "operations": [SCRIPT, SPLIT]}, {"reply": "", "operations": []}], [])
        provider._native_call = Mock(side_effect=native({"error": "The scene has no camera"}))
        self.assertEqual(provider.run(None, "Look", observation={}), "completed")
        self.assertEqual(len(provider._native_call.call_args_list), 3, "The LIT look is independent of the render")
        head, results = self.continuation(provider)
        self.assertIn("operations[1] (angelo_view) failed: The scene has no camera", head)
        slot = results["operations"][1]
        self.assertEqual([part.get("shading") for part in slot["results"]], [None, "LIT"])
        self.assertNotIn("Only", slot["normalized"])
        # The note never claims a look that was not drawn.
        provider, _ = self.provider([{"reply": "", "operations": [SCRIPT, SPLIT]}], [])
        provider._native_call = Mock(side_effect=native({"error": "Document changed or turn stopped"}))
        self.assertEqual(provider.run(None, "Look", observation={}), "interrupted")
        self.assertEqual(len(provider._native_call.call_args_list), 2, "No further look after a decided part")

    def test_a_decided_second_part_outranks_a_failed_first(self):
        # A stop on any part is read as the slot's decision.
        def call(name, arguments, **_kwargs):
            if name == "angelo_execute_python":
                return copy.deepcopy(SCRIPT_RESULT)
            if arguments.get("shading") == "RENDERED":
                return {"error": "The scene has no camera"}
            return {"error": "Document changed or turn stopped"}
        provider, _ = self.provider([{"reply": "", "operations": [SCRIPT, SPLIT, API]}, {"reply": "", "operations": []}], [])
        provider._native_call = Mock(side_effect=call)
        self.assertEqual(provider.run(None, "Look", observation={}), "interrupted")
        self.assertNotIn("angelo_blender_api", self.calls(provider))

    def test_a_normalised_view_over_the_view_budget_is_refused_instead_and_the_script_still_runs(self):
        budget = Mock(side_effect=lambda calls: "View budget: over" if any(
            call["arguments"].get("shading") == "LIT" for call in calls) else None)
        screened = screen_plan([SCRIPT, PICTURE, SPLIT], None, None, budget)
        self.assertEqual((screened.problems, list(screened.notes)), ([], [1]), "A picture adds no kept view: kept")
        self.assertEqual(screened.refused, {2: plan_problems([SCRIPT, PICTURE, SPLIT], None)[0][1]
                                            + " (its normalised form would exceed the view budget)"})
        self.assertEqual(screened.results[2]["view_budget"], plan_validation.OVER_BUDGET,
                         "The model is told why LIT is no way out either")
        self.assertEqual([call["tool"] for call in screened.dispatched()], ["angelo_execute_python", "angelo_view"])
        from angelo_agent.plan_recovery import run_operations, stopped_note
        evidence = Mock(feedback={})
        run = run_operations(screened.operations, lambda operation: (copy.deepcopy(SCRIPT_RESULT) if operation["tool"]
                             == "angelo_execute_python" else view_result(operation["arguments"])), lambda: True,
                             evidence, Mock(tool_images=Mock(return_value=[])), screened=screened)
        note = stopped_note(screened.operations, run)
        self.assertIn(plan_validation.OVER_BUDGET, note)
        for words in ("None of this plan's operations were run", "before running any"):   # It ran.
            self.assertNotIn(words, note + json.dumps(run.results))

    def test_a_plan_over_the_budget_without_its_normalised_views_is_rejected_whole(self):
        # Reverting a view never lets an over-budget plan run part of itself.
        def kept(calls):
            return sum(len(call["arguments"]["views"]) for call in calls
                       if call["tool"] == "angelo_view" and call["arguments"].get("shading") != "RENDERED")
        budget = Mock(side_effect=lambda calls: "View budget: over" if kept(calls) > 3 else None)
        operations = [SCRIPT, SPLIT, SCRIPT, view(["FRONT", "TOP", "ISO"]), view(["BACK", "LEFT", "RIGHT"])]
        self.assertEqual(screen_plan(operations, None, None, budget).problems, ["View budget: over"],
                         "Its later views alone exceed the budget: rejected whole, as a valid look's plan is")
        operations = [SCRIPT, SPLIT, SCRIPT, view(["FRONT", "TOP", "ISO"])]
        self.assertEqual(screen_plan(operations, None, None, budget).problems, ["View budget: over"],
                         "The overflow is the later valid view's: rejected whole, as with SPLIT written validly")
        # Only the normalised view that takes the plan past the budget is refused; one that fits runs.
        operations = [SCRIPT, view(["SCENE_CAMERA", "FRONT", "TOP"], "RENDERED"), SPLIT,
                      view(["SCENE_CAMERA", "ISO"], "RENDERED")]
        screened = screen_plan(operations, None, None, budget)
        self.assertEqual((screened.problems, list(screened.notes), list(screened.refused)), ([], [1, 2], [3]),
                         "2 + 1 kept views fit the 3; the third normalised view's one more does not")
        self.assertEqual(kept(screened.dispatched()), 3)
        always = Mock(return_value="View budget: over")
        self.assertEqual(screen_plan([SCRIPT, FRAME], None, None, always).problems,
                         [plan_problems([SCRIPT, FRAME], None)[0][0], "View budget: over"])

    def test_normalisation_keeps_a_plan_within_the_operation_limit(self):
        screened = screen_plan([SPLIT, SPLIT] + [SCRIPT] * 61, None)
        self.assertEqual((list(screened.notes), list(screened.refused)), ([0], [1]))
        self.assertEqual(len(screened.operations) + sum(map(len, screened.companions.values())), 64)

if __name__ == "__main__":
    unittest.main()
