# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Codex typed plans bind each item past the request's own commits (plan_bindings): offline.

Toolset-30 gate runs failed bevel_and_shade tasks the same way: a
final plan [transform_apply program, bevel-and-shade recipe] on one object,
bindings left to the host, both bound to the turn's revision before the first
ran. REGATE_OUTPUTS replays those plans through the scripted app-server
against an owner that compares revisions exactly, as Blender's does.
"""

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent.plan_bindings import OwnCommits
from strict_plan_vectors import REGATE_OUTPUTS
from test_native_codex_plan import EPOCH, REVISION, PlanTurnFixture, _table

TARGET = "scn:%s:153" % EPOCH  # o4 in _table(4).
AFTER_APPLY, OUTSIDE = "c" * 64, "d" * 64
STALE = "Recipe target changed since observation; inspect it again before expanding"


def committed(handle, revision, checks=1):
    """A committed program result: the written object's post-commit revision and positive checks."""
    row = {"index": 0, "kind": "dimensions", "target": handle, "status": "pass", "passed": True,
           "observed": {"dimensions": [4.0, 1.0, 3.0]}}
    return {"success": True, "final": True, "status": "committed", "scene_changed": True, "mutation_started": True,
            "partial_changes_possible": False, "program_id": "p", "preview_id": "",
            "checks": [dict(row, index=index) for index in range(checks)],
            "changes": {"added": [], "removed": [], "modified": [{"name": "Eval Cube", "fields": ["object"]}]},
            "live_revisions": {"status": "COMMITTED", "objects": [
                {"handle": handle, "name": "Eval Cube", "type": "MESH", "role": "WRITTEN", "object_revision": revision}]}}


class OwnCommitsTests(unittest.TestCase):
    def program(self, revision, handle=TARGET):
        return {"tool": "angelo_scene_program", "arguments": {
            "mode": "APPLY", "read_set": [], "write_set": [{"handle": handle, "revision": revision}], "operations": []}}

    def recipe(self, revision, handle=TARGET):
        return {"tool": "angelo_apply_recipe", "arguments": {
            "recipe": "bevel-and-shade", "target": handle, "revision": revision, "mode": "APPLY", "parameters": {}}}

    def test_a_binding_moves_along_the_requests_own_commits_only(self):
        own = OwnCommits()
        own.record(self.program(REVISION), committed(TARGET, AFTER_APPLY))
        self.assertEqual(own.rebind(self.recipe(REVISION))["arguments"]["revision"], AFTER_APPLY)
        own.record(self.program(AFTER_APPLY), committed(TARGET, OUTSIDE))
        self.assertEqual(own.rebind(self.recipe(REVISION))["arguments"]["revision"], OUTSIDE, "a chain of own commits")
        verify = {"tool": "angelo_scene_verify", "arguments": {"read_set": [{"handle": TARGET, "revision": REVISION}]}}
        self.assertEqual(own.rebind(verify)["arguments"]["read_set"][0]["revision"], OUTSIDE)
        program = own.rebind(self.program(REVISION))
        self.assertEqual(program["arguments"]["write_set"][0]["revision"], OUTSIDE)

    def test_anything_the_request_did_not_replace_is_left_for_the_owner_to_refuse(self):
        own = OwnCommits()
        own.record(self.program(REVISION), committed(TARGET, AFTER_APPLY))
        unrelated = self.recipe(REVISION, handle="scn:%s:150" % EPOCH)
        self.assertIs(own.rebind(unrelated), unrelated)
        other = self.recipe("e" * 64)  # A revision this request never replaced.
        self.assertIs(own.rebind(other), other)
        for result in ({"error": "refused", "scene_changed": False}, dict(committed(TARGET, OUTSIDE), live_revisions={
                "status": "STAGED", "objects": []})):
            own = OwnCommits()
            own.record(self.program(REVISION), result)
            self.assertEqual(own.rebind(self.recipe(REVISION))["arguments"]["revision"], REVISION)

    def test_in_turn_calls_count_as_own_commits(self):
        own = OwnCommits([(self.program(REVISION), committed(TARGET, AFTER_APPLY))])
        self.assertEqual(own.rebind(self.recipe(REVISION))["arguments"]["revision"], AFTER_APPLY)


class GuidanceTests(unittest.TestCase):
    """The plan guidance tells the model what this binding does, so a dependent edit costs no exec round.

    GPT-6.1-Sol applied a scaled cube's scale before beveling it in bevel_and_shade turns. Asked for "an edit a
    later step of this turn depends on" from exec, it ran transform_apply there (reasoning that the recipe's
    revision would go stale) and wrote the recipe in a third request, a slower turn than a one-request plan.
    The one-request plan is REGATE_OUTPUTS' shape, which RegateReplayTests commits whole.
    """

    def test_the_guidance_puts_dependent_edits_in_the_plan_and_says_why(self):
        from angelo_agent.codex_plan import PLAN_GUIDANCE
        from angelo_agent.codex_reference import program_reference
        text = " ".join(PLAN_GUIDANCE.split())
        self.assertNotIn("an edit a later step of this turn depends on", text)
        self.assertIn("The host binds each operation just before it runs, past what the earlier ones committed", text)
        # Facts-only text: the binding fact without "all go in operations", and
        # what an exec call does (it runs in-turn and its result comes back) without listing when to use one.
        self.assertIn("consecutive edits of one object in operations need no newer revision", text)
        self.assertIn("an edit called from exec runs during your turn and its result comes back to you", text)
        # The kind those turns looked up first is stated, so the whole request is one plan.
        self.assertIn('{kind:"transform_apply";target:Target;parameters:{location?:boolean;rotation?:boolean;'
                      'scale?:boolean;};}', program_reference(plan=True))


class RegateReplayTests(PlanTurnFixture):
    """The four failing plans, verbatim, through the provider, the plan runner and an exact owner."""

    def setUp(self):
        super().setUp()
        self.c._identifiers = _table(4)
        self.live = {TARGET: REVISION}
        self.after_commit = []  # Changes someone else makes right after the program commits.
        self.observations = []

    def request_tool(self, call_id, name, arguments, callback, *, delivery_owner=None):
        """An owner that refuses a binding that is not the live revision, as Blender's does."""
        self.dispatched.append((call_id, name, copy.deepcopy(arguments)))
        if name == "angelo_scene_program":
            bound = {entry["handle"]: entry["revision"] for entry in arguments.get("write_set") or ()}
            if bound.get(TARGET) != self.live[TARGET]:
                return callback({"error": "The target changed since observation", "error_code": "STALE_REVISION", "scene_changed": False})
            self.live[TARGET] = AFTER_APPLY
            result = committed(TARGET, AFTER_APPLY)
            for change in self.after_commit:
                change()
            return callback(result)
        if name == "angelo_apply_recipe":
            if arguments["revision"] != self.live[TARGET]:
                return callback({"error": STALE, "error_code": "STALE_REVISION", "scene_changed": False, "partial_changes_possible": False,
                                 "mutation_started": False})
            self.live[TARGET] = "f" * 64
            return callback(committed(TARGET, "f" * 64, checks=2))
        return callback({"success": True})

    def observe(self, callback):
        self.observations.append(dict(self.live))
        callback({"objects": [{"handle": handle, "name": "Eval Cube", "type": "MESH", "object_revision": revision}
                              for handle, revision in self.live.items()]})
        return True

    def test_each_regate_plan_now_commits_both_items(self):
        for model, trial, text in REGATE_OUTPUTS:
            with self.subTest(model=model, trial=trial):
                self.dispatched.clear()
                self.c._identifiers, self.live = _table(4), {TARGET: REVISION}
                self.assertEqual(self.run_turns([("message", text)]), "completed")
                (_, program, program_arguments), (_, recipe, recipe_arguments) = self.dispatched
                self.assertEqual((program, recipe), ("angelo_scene_program", "angelo_apply_recipe"))
                self.assertEqual(program_arguments["write_set"], [{"handle": TARGET, "revision": REVISION}])
                self.assertEqual(recipe_arguments["revision"], AFTER_APPLY, "bound past the plan's own commit")
                self.assertEqual(len(self.starts()), 1, "no correction round was needed")
                self.assertEqual(self.c._identifiers.observed(TARGET), "f" * 64, "plan commits reach the table")

    # A stale refusal that changed nothing gets one corrected plan (request_recovery): the model is told
    # the target changed and is sent the current observation, and the corrected plan binds what that observation
    # showed. It never binds a revision nobody showed the model, and the exact owner refuses any older one.
    def test_a_change_after_our_commit_gets_one_correction_bound_to_what_it_showed(self):
        self.after_commit.append(lambda: self.live.update({TARGET: OUTSIDE}))
        self.context.request_observation = self.observe
        remaining = json.dumps({"reply": "", "operations": [json.loads(REGATE_OUTPUTS[0][2])["operations"][1]]})
        status = self.run_turns([("message", REGATE_OUTPUTS[0][2])], [("message", remaining)])
        self.assertEqual(status, "completed")
        self.assertEqual([(name, arguments.get("revision")) for _, name, arguments in self.dispatched][1:],
                         [("angelo_apply_recipe", AFTER_APPLY), ("angelo_apply_recipe", OUTSIDE)])
        self.assertEqual(self.observations, [{TARGET: OUTSIDE}], "the correction carried the current observation")
        prompt = json.dumps(self.starts()[1]["input"])
        self.assertIn("changed nothing: " + STALE, prompt)
        self.assertIn(OUTSIDE[:8], prompt)

    def test_a_plan_stale_before_it_ran_rebinds_only_to_what_the_correction_showed(self):
        self.live[TARGET] = OUTSIDE
        self.context.request_observation = self.observe
        text = REGATE_OUTPUTS[2][2]
        self.assertEqual(self.run_turns([("message", text)], [("message", text)]), "completed")
        bound = [arguments["write_set"][0]["revision"] for _, name, arguments in self.dispatched
                 if name == "angelo_scene_program"]
        self.assertEqual(bound, [REVISION, OUTSIDE])
        self.assertEqual(self.observations, [{TARGET: OUTSIDE}])
        self.assertEqual(len(self.starts()), 2)

    def test_without_a_new_observation_a_stale_plan_is_refused_again(self):
        self.live[TARGET] = OUTSIDE
        self.context.request_observation = lambda callback: callback({"objects": []}) or True
        text = REGATE_OUTPUTS[2][2]
        self.assertEqual(self.run_turns([("message", text)], [("message", text)], [("message", text)]), "interrupted")
        self.assertEqual({arguments["write_set"][0]["revision"] for _, name, arguments in self.dispatched
                          if name == "angelo_scene_program"}, {REVISION})

    def test_a_repeat_of_a_committed_item_is_not_run_twice_after_a_validation_correction(self):
        original = self.context.request_tool
        refused = []
        def once(call_id, name, arguments, callback):
            if name == "angelo_apply_recipe" and not refused:
                refused.append(True)
                self.dispatched.append((call_id, name, copy.deepcopy(arguments)))
                return callback({"error": "A recipe parameter needs correction", "error_code": "VALIDATION",
                                 "scene_changed": False, "partial_changes_possible": False})
            return original(call_id, name, arguments, callback)
        self.context.request_tool = once
        self.context.request_observation = self.observe
        status = self.run_turns([("message", REGATE_OUTPUTS[0][2])], [("message", REGATE_OUTPUTS[0][2])])
        self.assertEqual(status, "completed")
        self.assertEqual([name for _, name, _ in self.dispatched],
                         ["angelo_scene_program", "angelo_apply_recipe", "angelo_apply_recipe"])

    def test_a_decline_or_an_expiry_still_ends_the_request(self):
        for error in ("The user declined this native action", "This approval expired before it was used"):
            with self.subTest(error=error):
                self.dispatched.clear()
                self.c._identifiers, self.live = _table(4), {TARGET: REVISION}

                def refuse(call_id, name, arguments, callback, error=error):
                    self.dispatched.append((call_id, name, arguments))
                    callback({"error": error, "scene_changed": False, "expired": "expired" in error})
                self.context.request_tool = refuse
                self.assertEqual(self.run_turns([("message", REGATE_OUTPUTS[0][2])]), "interrupted")
                self.assertEqual(len(self.starts()), 1)


if __name__ == "__main__":
    unittest.main()
