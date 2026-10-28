# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A Claude correction patches the plan the host rejected or stopped instead of resending it (plan_patch).

Recorded (LANE-g2 count, 2026-10-25): 8 of the 18 rounds current parallel trials lost were Claude plans resent
whole (2.1-8.3k output tokens, about 20 s each) to change one operation, a name or a check. The loop is driven
with mocked rounds and native calls: no model, process or Blender.
"""

import copy
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import plan_patch  # noqa: E402
from angelo_agent.plan_patch import PatchError, patched  # noqa: E402
from angelo_agent.tool_specs import native_plan_schema  # noqa: E402
from test_native_round_savings import committed, program  # noqa: E402
from test_native_turn_recovery import LoopCase, ROLLED_BACK, SCRIPT, SCRIPT_RESULT  # noqa: E402

VIEW = {"tool": "angelo_view", "arguments": {"views": ["FRONT"], "resolution": 512}}


def bad_program():
    """A program the host rejects before it runs: an unknown mode (any invalid field of any operation would do)."""
    call = program()
    call["arguments"]["mode"] = "NOPE"
    return call


class PatchTests(unittest.TestCase):
    """RFC 6902 steps on the held plan, as numbered in the host's report; what ran stays out."""

    def test_a_nested_replace_keeps_every_other_operation(self):
        held = [copy.deepcopy(VIEW), bad_program()]
        plan, kept = patched(held, frozenset(), [{"op": "replace", "path": "/operations/1/arguments/mode", "value": "APPLY"}])
        self.assertEqual((plan, kept), ([VIEW, program()], [0, 1]))
        self.assertEqual(held[1]["arguments"]["mode"], "NOPE", "the held plan itself is never changed")

    def test_operations_that_ran_are_left_out_and_cannot_be_changed(self):
        held = [copy.deepcopy(SCRIPT), program()]
        name = "/operations/1/arguments/operations/0/parameters/name"
        plan, kept = patched(held, frozenset({0}), [{"op": "replace", "path": name, "value": "Cube B"}])
        self.assertEqual((plan[0]["arguments"]["operations"][0]["parameters"]["name"], len(plan), kept), ("Cube B", 1, [1]))
        for step, text in (({"op": "replace", "path": "/operations/0/arguments/code", "value": "x"},
                            r"patch\[0\] \(/operations/0/arguments/code\) changes operations\[0\], which already ran"),
                           ({"op": "remove", "path": "/operations/0"}, "changes operations\\[0\\], which already ran"),
                           ({"op": "add", "path": "/operations/0", "value": copy.deepcopy(VIEW)},
                            "adds an operation before operations\\[0\\], which already ran"),
                           ({"op": "replace", "path": "/operations", "value": []}, "no operation that already ran")):
            with self.subTest(step=step), self.assertRaisesRegex(PatchError, text):
                patched(held, frozenset({0}), [step])

    def test_remove_and_add_at_both_levels(self):
        held = [program()]
        operations = "/operations/0/arguments/operations"
        plan, _ = patched(held, frozenset(), [
            {"op": "add", "path": operations + "/-", "value": {"kind": "x"}},
            {"op": "remove", "path": operations + "/0"},
            {"op": "add", "path": "/operations/-", "value": copy.deepcopy(SCRIPT)},
            {"op": "add", "path": "/operations/0", "value": copy.deepcopy(VIEW)}])
        self.assertEqual([operation["tool"] for operation in plan],
                         ["angelo_view", "angelo_scene_program", "angelo_execute_python"])
        self.assertEqual(plan[1]["arguments"]["operations"], [{"kind": "x"}])

    def test_every_refusal_says_what_the_plan_has(self):
        held = [program()]
        for steps, text in (
                ([{"op": "replace", "path": "/operations/3", "value": {}}], "names 3, but there are 1 items there"),
                ([{"op": "replace", "path": "/operations/0/arguments/nope/x", "value": 1}], "reaches 'nope', which is not in"),
                ([{"op": "replace", "path": "/operations/0/arguments/operations/4", "value": 1}],
                 "names 4, but there are 1 items there"),
                ([{"op": "remove", "path": "/operations/0"}, {"op": "remove", "path": "/operations/0/arguments"}],
                 "patch[1] (/operations/0/arguments) names 0, but there are 0 items there"),
                ([{"op": "remove", "path": "/operations/0"}], "the patched plan has 0 operations to run; a plan runs 1 to 64"),
                ([{"op": "move", "path": "/operations/0"}], "is {op: replace|remove|add, path, value}"),
                ([{"op": "replace", "path": "/operations/0"}], "replace and add take a value"),
                ([{"op": "replace", "path": "/plan/0", "value": 1}], "starts at /operations, the plan's only member"),
                ([{"op": "replace", "path": "/operations/0/~2", "value": 1}], "is not an RFC 6901 pointer"),
                ([], "1 to 64 steps; 0 given")):
            with self.subTest(text=text), self.assertRaisesRegex(PatchError, re.escape(text)):
                patched(held, frozenset(), steps)

    def test_only_claudes_plan_schema_offers_it(self):
        schema = native_plan_schema()
        self.assertIn("patch", schema["properties"])
        self.assertEqual(schema["required"], ["operations"], "operations stay required; a patch sends them empty")


def views(*names):
    return [{"tool": "angelo_view", "arguments": {"views": [name], "resolution": 512}} for name in names]


def held_patches(operations, prompt="rejected", run=None, unchanged=True):
    """A request's patches after the host rejected (or, with ``run``, stopped) ``operations``."""
    patches = plan_patch.PlanPatches()
    patches.apply({"operations": copy.deepcopy(operations)})
    if run is None:
        patches.hold(prompt)
    else:
        patches.hold(prompt, run, unchanged)
    return patches


class BreakIt19BTests(unittest.TestCase):
    """Break-it E2E-19B on 948d877ee26: each finding's class, through the request's PlanPatches."""

    def outcome(self, patches, steps):
        plan = patches.apply({"operations": [], "patch": steps})
        return plan.get("problem") or [(operation["arguments"]["views"][0], operation["arguments"]["resolution"])
                                       for operation in plan["operations"]]

    def test_steps_apply_in_order_each_to_the_plan_the_step_before_produced(self):
        """M-2: RFC 6902 (section 3): every pair of steps whose readings by original index would differ."""
        held = views("FRONT", "BACK", "LEFT", "RIGHT")
        extra, other = views("TOP")[0], views("BOTTOM")[0]
        cases = (
            ([{"op": "add", "path": "/operations/1", "value": extra},
              {"op": "replace", "path": "/operations/2/arguments/resolution", "value": 1024}],
             [("FRONT", 512), ("TOP", 512), ("BACK", 1024), ("LEFT", 512), ("RIGHT", 512)]),
            ([{"op": "add", "path": "/operations/1", "value": extra}, {"op": "add", "path": "/operations/1", "value": other}],
             [("FRONT", 512), ("BOTTOM", 512), ("TOP", 512), ("BACK", 512), ("LEFT", 512), ("RIGHT", 512)]),
            ([{"op": "remove", "path": "/operations/1"},
              {"op": "replace", "path": "/operations/1/arguments/resolution", "value": 1024}],
             [("FRONT", 512), ("LEFT", 1024), ("RIGHT", 512)]),
            ([{"op": "add", "path": "/operations/4", "value": extra}],
             [("FRONT", 512), ("BACK", 512), ("LEFT", 512), ("RIGHT", 512), ("TOP", 512)]),
            ([{"op": "remove", "path": "/operations/0"}, {"op": "remove", "path": "/operations/0"}],
             [("LEFT", 512), ("RIGHT", 512)]))
        for steps, expected in cases:
            with self.subTest(steps=steps):
                self.assertEqual(self.outcome(held_patches(held), steps), expected)

    def test_a_patch_that_leaves_an_operation_the_schema_could_not_carry_is_a_plan_problem(self):
        """M-3: a key beside tool/arguments, no arguments, arguments that are not an object, a tool the request does
        not have, more than 64 operations: a counted problem naming the operation, never a failed request."""
        held = views("FRONT")
        for steps, text in (
                ([{"op": "add", "path": "/operations/0/note", "value": "x"}], "operations[0] of the patched plan is an "
                                                                            "object with arguments, note, tool"),
                ([{"op": "remove", "path": "/operations/0/arguments"}], "is an object with tool; an operation is exactly"),
                ([{"op": "replace", "path": "/operations/0/arguments", "value": [1]}], "operations[0] of the patched plan"),
                ([{"op": "replace", "path": "/operations/0/tool", "value": "angelo_workers_progress"}],
                 "one of this request's tools"),
                ([{"op": "add", "path": "/operations/-", "value": views("TOP")[0]}] * 64,
                 "the patched plan has 65 operations to run; a plan runs 1 to 64")):
            with self.subTest(text=text):
                problem = self.outcome(held_patches(held), steps)
                self.assertIsInstance(problem, str)
                self.assertIn(text, problem)

    def test_an_index_is_ascii_digits_without_a_leading_zero(self):
        """L-2: RFC 6901's array index; a Unicode digit or a leading zero is a plan problem, never a ValueError."""
        for index in ("\u00b2", "\u0663", "01", "-1", "1.0"):
            with self.subTest(index=index):
                problem = self.outcome(held_patches(views("FRONT", "BACK")),
                                       [{"op": "replace", "path": "/operations/%s/arguments/resolution" % index, "value": 1}])
                self.assertIn("which is not an array index", problem)

    def test_a_renumbered_plan_says_which_operations_its_numbers_are(self):
        """L-1: a stopped plan's ran operations are left out of the patched plan, so the report that numbers it says
        which operations of the model's patched plan they are."""
        stopped = NS(results=[{}, {}, {}, {"error": "x"}], refused=[])
        patches = held_patches(views("FRONT", "BACK", "LEFT", "RIGHT", "TOP", "BOTTOM"), run=stopped, unchanged="x")
        plan = patches.apply({"operations": [], "patch": [
            {"op": "replace", "path": "/operations/3/arguments/resolution", "value": 1024}]})
        self.assertEqual([operation["arguments"]["views"][0] for operation in plan["operations"]], ["RIGHT", "TOP", "BOTTOM"])
        prompt = patches.hold("rejected again")
        self.assertIn("its operations[0, 1, 2] are operations[3, 4, 5] of your patched plan", prompt)
        self.assertEqual(self.outcome(patches, [{"op": "remove", "path": "/operations/2"}]),
                         [("RIGHT", 1024), ("TOP", 512)], "the next patch uses the report's numbers")

    def test_only_an_edit_that_changed_nothing_is_held(self):
        """L-5: a raised script whose Main data was not restored left changes: no patch is offered over it."""
        for restored, held in ((False, False), (True, True)):
            with self.subTest(restored=restored):
                raised = dict(SCRIPT_RESULT, script_error=True, error="boom", main_data_restored=restored)
                patches = held_patches([SCRIPT], run=NS(results=[raised], refused=[]), unchanged=None)
                self.assertEqual(patches.held is not None, held)
                self.assertEqual(plan_patch.NOTE in (patches.note("stopped") or ""), held)


class PatchedCorrectionTests(LoopCase):
    """The patched plan is validated, approved and run like a written one; nothing that ran runs again."""

    def test_a_rejected_plan_is_corrected_by_a_patch(self):
        plans = [{"reply": "", "operations": [bad_program()]},
                 {"reply": "", "operations": [], "patch": [
                     {"op": "replace", "path": "/operations/0/arguments/mode", "value": "APPLY"}]}]
        provider, context = self.provider(plans, [committed()])
        self.assertEqual(provider.run(None, "Build", observation={}), "completed")
        self.assertEqual(self.calls(provider), ["angelo_scene_program"])
        self.assertEqual(provider._native_call.call_args.args[1]["mode"], "APPLY")
        self.assertEqual(provider._native_call.call_args.args[1]["operations"], program()["arguments"]["operations"])
        correction = provider._turn.call_args_list[1].args[1]
        self.assertTrue(correction.startswith("The host rejected your last plan"))
        self.assertTrue(correction.endswith(plan_patch.NOTE))
        self.assertTrue(self.messages(context)[-1].startswith("Host-verified result"))

    def test_a_plan_stopped_after_its_first_operation_ran_reruns_only_the_rest(self):
        name = "/operations/1/arguments/operations/0/parameters/name"
        plans = [{"reply": "", "operations": [copy.deepcopy(SCRIPT), program()]},
                 {"reply": "", "operations": [], "patch": [{"op": "replace", "path": name, "value": "Eval Cube"}]}]
        provider, _ = self.provider(plans, [copy.deepcopy(SCRIPT_RESULT), copy.deepcopy(ROLLED_BACK), committed()])
        self.assertEqual(provider.run(None, "Build", observation={}), "completed")
        self.assertEqual(self.calls(provider), ["angelo_execute_python", "angelo_scene_program", "angelo_scene_program"],
                         "the script that ran is not run again")
        head = provider._turn.call_args_list[1].args[1].split("\n", 1)[0]
        self.assertIn("operations[1] (angelo_scene_program) failed and changed nothing", head)
        self.assertTrue(head.endswith(plan_patch.NOTE), "the note stays on the results' header line")

    def test_a_patch_that_breaks_an_operations_shape_is_a_counted_rejection_not_a_failed_request(self):
        """Break-it E2E-19B M-3: the screen treats an operation the plan schema could not carry as a contract breach
        and fails the request; a patch that builds one is a plan problem the model corrects in the next round."""
        for steps, allowed in (([{"op": "add", "path": "/operations/0/note", "value": "x"}], None),
                               ([{"op": "remove", "path": "/operations/0/arguments"}], None),
                               ([{"op": "replace", "path": "/operations/0/tool", "value": "angelo_execute_python"}],
                                frozenset({"angelo_scene_program", "angelo_view"}))):
            with self.subTest(steps=steps):
                plans = [{"reply": "", "operations": [bad_program()]}, {"reply": "", "operations": [], "patch": steps},
                         {"reply": "", "operations": [program()]}]
                provider, context = self.provider(plans, [committed()])
                context.allowed_tools = allowed
                self.assertEqual(provider.run(None, "Build", observation={}), "completed")
                self.assertEqual(self.calls(provider), ["angelo_scene_program"])
                self.assertTrue(provider._turn.call_args_list[2].args[1].startswith(
                    "patch: operations[0] of the patched plan is"))

    def test_a_patch_that_cannot_apply_is_a_problem_and_the_plan_stays_patchable(self):
        good = {"op": "replace", "path": "/operations/0/arguments/mode", "value": "APPLY"}
        plans = [{"reply": "", "operations": [bad_program()]},
                 {"reply": "", "operations": [], "patch": [dict(good, path="/operations/2/arguments/mode")]},
                 {"reply": "", "operations": [], "patch": [good]}]
        provider, context = self.provider(plans, [committed()])
        self.assertEqual(provider.run(None, "Build", observation={}), "completed")
        self.assertEqual(self.calls(provider), ["angelo_scene_program"])
        third = provider._turn.call_args_list[2].args[1]
        self.assertTrue(third.startswith("patch[0] (/operations/2/arguments/mode) names 2, but there are 1 items"))
        self.assertTrue(third.endswith(plan_patch.NOTE))
        self.assertTrue(any(text.startswith("Nothing of the last plan ran") for text in self.messages(context)))

    def test_without_a_held_plan_a_patch_is_a_problem_and_a_written_plan_clears_it(self):
        patch = [{"op": "replace", "path": "/operations/0/arguments/mode", "value": "APPLY"}]
        plans = [{"reply": "", "operations": [], "patch": patch}, {"reply": "", "operations": [program()]}]
        provider, _ = self.provider(plans, [committed()])
        self.assertEqual(provider.run(None, "Build", observation={}), "completed")
        second = provider._turn.call_args_list[1].args[1]
        self.assertTrue(second.startswith("patch: there is no rejected or stopped plan to patch"))
        self.assertNotIn(plan_patch.NOTE, second)
        patches = plan_patch.PlanPatches()
        patches.apply({"operations": [bad_program()]})
        patches.hold("rejected")
        patches.apply({"operations": [program()]})
        self.assertIn("no rejected or stopped plan", patches.apply({"operations": [], "patch": patch})["problem"])

    def test_a_round_whose_plan_never_runs_keeps_the_held_plan(self):
        """A problem the turn itself found (an empty or superseded plan) leaves the held plan as it was."""
        patches = plan_patch.PlanPatches()
        patches.apply({"operations": [bad_program()]})
        patches.hold("rejected")
        step = {"op": "replace", "path": "/operations/0/arguments/mode", "value": "APPLY"}
        superseded = patches.apply({"operations": [], "patch": [step], "problem": "superseded"})
        self.assertEqual((superseded["problem"], "patch" in superseded), ("superseded", False))
        self.assertEqual(patches.apply({"operations": [], "patch": [step]})["operations"], [program()])

    def test_operations_beside_a_patch_are_refused(self):
        patches = plan_patch.PlanPatches()
        patches.apply({"operations": [bad_program()]})
        patches.hold("rejected")
        plan = patches.apply({"operations": [program()], "patch": [{"op": "remove", "path": "/operations/0"}]})
        self.assertEqual((plan["operations"], plan["problem"]), ([], "patch: a plan carries operations or a patch, not both"))
        self.assertNotIn("patch", plan)


if __name__ == "__main__":
    unittest.main()
