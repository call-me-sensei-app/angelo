# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A script's self-checks are information, never a gate (self_checks.SelfChecks in the Claude plan loop).

The script's ``kit`` records measured values beside targets; the executor returns them as ``self_checks``. Pinned
here with the real ClaudeProvider loop and mocked rounds: a plan whose own checks fail gets one result round, so a
reply written before they ran never ends the turn over a failing check the model has not seen; the round states the
failures and asks for nothing; the model may fix the scene, fix the check or reply; the turn completes either way,
and the person's notice lists the checks still open. Jobs start whatever the checks say. Most failing checks in the
recorded runs were wrong checks on a right scene (doc/angelo/agent_execution.rst, "The script kit and self-checks").
"""

from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from claude_protocol_fixtures import host  # noqa: E402
from angelo_agent import self_checks  # noqa: E402
from angelo_agent.providers import EMPTY_PLAN, ClaudeProvider  # noqa: E402
from angelo_agent.self_checks import SelfChecks  # noqa: E402

BUILD = {"tool": "angelo_execute_python", "arguments": {"description": "Build the castle.", "code": "result = 1"}}
FIX = {"tool": "angelo_execute_python", "arguments": {"description": "Fix the gateway.", "code": "result = 2"}}
LOOKUP = {"tool": "angelo_scene_search", "arguments": {"query": "Gate"}}
STILL = {"tool": "angelo_job_start", "arguments": {"kind": "RENDER_STILL", "output": "castle-final.png"}}
LOOK = {"tool": "angelo_view", "arguments": {"views": ["SCENE_CAMERA"], "shading": "RENDERED", "image": "LAST_JOB"}}
STARTED = {"success": True, "job": "j1", "status": "queued"}
ROUND = "A self-check of your script failed"   # The opening words the replay tools match (replay_turn_context).
MANDATES = ("not done", "reply only when", "Fix the cause", "every check passes")


def ran(*checks):
    return {"success": True, "result": 1, "output": "", "self_checks": list(checks)}


def check(name, passed, measured=3.5, target=5.5):
    return {"name": name, "measured": measured, "target": target, "tolerance": 0.05, "passed": passed}


def plan(*operations, verify=False, reply=""):
    return {"reply": reply, "operations": list(operations), "verify": verify}


REPLY = plan(reply="The castle is built; the crown check measured the wrong part.")


def mock_any():
    from unittest.mock import ANY
    return ANY


class ChecksTests(unittest.TestCase):
    def test_a_check_is_open_until_a_later_script_records_it_passing(self):
        checks = SelfChecks()
        failed = checks.read([BUILD], [ran(check("crown", False), check("walls", True))])
        self.assertEqual(failed, ["crown"])
        self.assertIn("crown: measured 3.5, target 5.5 (tolerance 0.05)", checks.note(failed))
        self.assertEqual(checks.read([FIX], [ran(check("walls", True))]), [])
        self.assertIn("crown", checks.open)                       # Not checked again: still open.
        checks.read([FIX], [ran(check("crown", True, 5.5))])
        self.assertEqual((checks.open, checks.row()), ({}, None))

    def test_in_one_plan_a_failure_of_a_name_outweighs_its_passes(self):
        checks = SelfChecks()   # Review F1: a later pass of the same name masked a failure.
        checks.read([BUILD, FIX], [ran(check("crown", False)), ran(check("crown", True, 5.5))])
        self.assertIn("crown", checks.open)
        checks.read([FIX], [ran(check("crown", True, 5.5))])   # A later plan's pass closes it.
        self.assertEqual(checks.open, {})

    def test_an_unmeasured_check_never_passes_never_fails_and_never_closes(self):
        checks = SelfChecks()
        unmeasured = {"name": "supported", "measured": None, "target": 0, "passed": None}
        self.assertEqual(checks.read([BUILD], [ran(unmeasured)]), [])
        self.assertEqual((checks.open, checks.note([])), ({}, None))
        checks.read([BUILD], [ran(check("span", False))])
        for kind in (None, "colour", "size"):
            unmeasured = {"name": "span", "measured": None, "passed": None, "reason": "not measured"}
            checks.read([FIX], [ran(dict(unmeasured, kind=kind) if kind else unmeasured)])
            self.assertIn("span", checks.open, kind)

    def test_the_kit_offers_no_colour_check(self):
        # kit.check_colour was offered in 24 recorded builds and used in none, while its words grew one brief's
        # exception after another (doc/angelo/performance.rst, "Concepts, not scenes").
        from angelo_agent.script_kit import ScriptKit
        self.assertFalse(hasattr(ScriptKit, "check_colour"))
        addon = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
        self.assertEqual(sorted(path.name for path in addon.glob("script_kit_colour*.py")), [])
        self.assertFalse((addon / "script_kit_sky.py").exists())

    def test_a_check_without_a_measurement_shows_its_reason(self):
        checks = SelfChecks()   # Review N1: the rows read "measured null" with no why.
        failed = checks.read([BUILD], [ran({"name": "posts", "measured": None, "target": [[0, 0, 0], [4, 1, 1]],
                                            "tolerance": 0.05, "passed": False,
                                            "reason": "kit.bounds: no object with geometry matches ('Post',)"})])
        self.assertIn("posts: kit.bounds: no object with geometry matches ('Post',) (target [[0, 0, 0], [4, 1, 1]])",
                      checks.note(failed))

    def test_a_floating_parts_check_names_the_support_it_looked_for_before_its_parts(self):
        checks = SelfChecks()   # Four wheels on a deck read "floating" with no word of what support was sought.
        support = ("a chain of contacts down to ground=0.0 through the group's parts or any other visible surface, "
                   "each within tolerance=0.02")
        rows = [{"object": "Car Wheel %d" % i, "below": "Deck", "gap": 0.05, "reaches_down_to": 1.05,
                 "bounds": [[-1.7, -1.9, 1.05], [-1.0, -1.66, 1.75]]} for i in range(8)]
        failed = checks.read([BUILD], [ran(dict(check("car", False, 4, 0), support=support, floating=rows))])
        line = checks.note(failed)
        self.assertIn("car: measured 4, target 0 (tolerance 0.05), support looked for: " + support + ", floating: [",
                      line)
        self.assertIn('"below": "Deck", "gap": 0.05, "reaches_down_to": 1.05', line)

    def test_checks_past_the_listed_ones_count_by_name(self):
        checks = SelfChecks()   # Review 2 F2-2 and 3 F3-1: overflow passes close, overflow failures stay open.
        checks.read([BUILD], [ran(check("arch 66", False))])
        checks.read([FIX], [dict(ran(check("arch 0", True, 5.5)), self_checks_passed=["arch 66"])])
        self.assertEqual(checks.open, {})
        failed = checks.read([BUILD], [dict(ran(check("bad 0", False)), self_checks_failed=["bad 199"])])
        self.assertIn("bad 199: failed (not listed)", checks.note(failed))

    def test_the_host_only_pass_names_never_reach_the_model(self):
        from angelo_agent.model_results import model_result
        shown = model_result("angelo_execute_python", dict(ran(check("x", True)), self_checks_passed=["y"],
                                                           self_checks_failed=["z"]))
        self.assertEqual(sorted(key for key in shown if key.startswith("self_checks")), ["self_checks"])

    def test_only_script_results_count_and_the_note_names_this_plan_s_failures(self):
        checks = SelfChecks()
        program = {"tool": "angelo_scene_program", "arguments": {}}
        self.assertEqual(checks.read([program], [ran(check("x", False))]), [])
        checks.read([BUILD], [ran(check("old", False))])
        note = checks.note(checks.read([FIX], [ran(check("new", False))]))
        self.assertIn("new: measured", note)
        self.assertNotIn("old: measured", note)               # Seen already; still open for the person's notice.
        self.assertIn("old: measured", checks.row()["text"])

    def test_the_note_informs_and_mandates_nothing(self):
        note = SelfChecks.note(SelfChecks(), [])
        self.assertIsNone(note)
        text = self_checks.NOTE.format(failures="crown: measured 3.5, target 5.5 (tolerance 0.05)")
        self.assertTrue(text.startswith(ROUND))
        self.assertIn("The scene or the check can be what is wrong", text)
        self.assertIn("or reply as it is", text)
        for mandate in MANDATES:
            self.assertNotIn(mandate, text)
        for gone in ("ROUNDS", "SelfCheckGate"):   # No correction rounds and no gate remain.
            self.assertFalse(hasattr(self_checks, gone), gone)


class LoopTests(unittest.TestCase):
    def run_(self, plans, results, request="Build a castle"):
        context = host()
        context.allowed_tools = None
        context.request_observation = Mock(side_effect=lambda callback: callback({"context_version": 2, "objects": []}))
        provider = ClaudeProvider(context, {})
        provider._turn = Mock(side_effect=plans)
        provider._native_call = Mock(side_effect=list(results) + [{"context_version": 2, "objects": []}] * 40)
        status = provider.run(None, request, observation={})
        prompts = [call.args[1] for call in provider._turn.call_args_list]
        texts = [data["text"] for kind, data in context.events if kind == "message" and data.get("role") == "system"]
        return status, prompts, texts, [call.args[0] for call in provider._native_call.call_args_list]

    def test_a_failing_check_comes_back_once_and_a_reply_then_completes_the_turn(self):
        status, prompts, texts, _tools = self.run_([plan(BUILD), REPLY], [ran(check("crown", False))])
        self.assertEqual((status, len(prompts)), ("completed", 2))
        self.assertTrue(prompts[1].startswith(ROUND))
        self.assertIn("crown: measured 3.5, target 5.5", prompts[1])
        for mandate in MANDATES:
            self.assertNotIn(mandate, prompts[1].split("\n", 1)[0])
        # The person reads the open check as a fact; the turn is not failed for it.
        self.assertTrue(texts[-1].startswith("The script's self-checks still fail:"))
        self.assertIn("crown: measured 3.5, target 5.5", texts[-1])
        self.assertFalse(any("not done" in text or "asking for a fix" in text for text in texts))

    def test_a_reply_written_before_the_checks_ran_never_ships_unseen(self):
        # The plan carried its "done" reply and no verify: without the round the turn would have ended on it.
        done = plan(BUILD, reply="Built the castle as asked.")
        status, prompts, _texts, _tools = self.run_([done, REPLY], [ran(check("crown", False))])
        self.assertEqual((status, len(prompts)), ("completed", 2))
        self.assertTrue(prompts[1].startswith(ROUND))
        status, prompts, _texts, _tools = self.run_([done], [ran(check("crown", True, 5.5))])
        self.assertEqual((status, len(prompts)), ("completed", 1))   # Passing checks: no extra round.

    def test_the_model_may_fix_the_check_and_a_passing_recheck_leaves_nothing_open(self):
        status, prompts, texts, _tools = self.run_([plan(BUILD), plan(FIX)],
                                                   [ran(check("crown", False)), ran(check("crown", True, 5.5))])
        self.assertEqual((status, len(prompts)), ("completed", 2))
        self.assertFalse(any(text.startswith("The script's self-checks") for text in texts))

    def test_there_is_no_round_cap_and_no_failed_ending(self):
        # Four correction rounds used to end the turn failed with no still (dt-bridge-pyall t01: wheels resting on a
        # deck read as floating). Each plan with a failure the model has not seen gets its result round, and the
        # model ends the turn.
        plans = [plan(BUILD)] + [plan(FIX) for _ in range(6)] + [REPLY]
        results = [ran(check("car", False, measured=4 + attempt)) for attempt in range(7)]
        status, prompts, texts, _tools = self.run_(plans, results)
        self.assertEqual((status, len(prompts)), ("completed", 8))
        self.assertTrue(all(prompt.startswith(ROUND) for prompt in prompts[1:]))
        self.assertTrue(texts[-1].startswith("The script's self-checks still fail:"))

    def test_a_failure_the_model_has_seen_forces_no_round(self):
        # Review 3: re-running the same failing check looped to the 16-round cap (three times the old gate's cost).
        plans = [plan(BUILD), plan(FIX), plan(FIX)]
        status, prompts, texts, _tools = self.run_(plans, [ran(check("car", False)) for _ in range(3)])
        self.assertEqual((status, len(prompts)), ("completed", 2))   # The second plan's same failure ends the turn.
        self.assertTrue(prompts[1].startswith(ROUND))
        self.assertTrue(texts[-1].startswith("The script's self-checks still fail:\ncar"))
        self.assertEqual(sum(text.startswith("The script's self-checks") for text in texts), 1)

    def test_a_fix_that_changed_nothing_is_seen_once_after_a_reply(self):
        # Review 2: "Fixed the crown." re-recorded the same failure and the turn ended on it, unseen by the model.
        fixed = plan(FIX, reply="Fixed the crown.")
        plans = [plan(BUILD), fixed, fixed, fixed]
        status, prompts, texts, _tools = self.run_(plans, [ran(check("crown", False)) for _ in range(4)])
        self.assertEqual((status, len(prompts)), ("completed", 3))   # One repeat round, never a second.
        self.assertTrue(prompts[1].startswith(ROUND) and prompts[2].startswith(ROUND))
        self.assertTrue(texts[-1].startswith("The script's self-checks still fail:\ncrown"))

    def test_a_regression_after_a_pass_is_news_again(self):
        # Review 2: a check shown failing, then passed, then failing again with "Done." ended the turn unseen.
        done = plan(FIX, reply="Done.")
        plans = [plan(BUILD), plan(FIX, verify=True), done, REPLY]
        results = [ran(check("crown", False)), ran(check("crown", True, 5.5)), ran(check("crown", False))]
        status, prompts, _texts, _tools = self.run_(plans, results)
        self.assertEqual((status, len(prompts)), ("completed", 4))
        self.assertTrue(prompts[3].startswith(ROUND))

    def test_every_other_ending_still_tells_the_person(self):
        # Review 3: the round cap, a rejected-plan cap and a failed observation ended with no word of open checks.
        def exits(plans, results, **patches):
            context = host()
            context.allowed_tools = None
            provider = ClaudeProvider(context, {})
            provider._turn = Mock(side_effect=plans)
            provider._native_call = Mock(side_effect=results)
            provider._continuation_observation = Mock(side_effect=patches.get("observation", lambda *a: {
                "context_version": 2, "objects": []}))
            try:
                status = provider.run(None, "Build a castle", observation={})
            except Exception as error:   # noqa: BLE001 - the cap and the rejection budget raise.
                status = type(error).__name__
            texts = [data["text"] for kind, data in context.events if kind == "message"]
            return status, [text for text in texts if text.startswith("The script's self-checks still fail:")]
        looping = [plan(BUILD, verify=True)] * 16   # verify=true every round: the 16-round cap.
        self.assertEqual(exits(looping, [ran(check("car", False))] * 16), ("RpcError", [mock_any()]))
        broken = exits([plan(BUILD, verify=True)], [ran(check("car", False))],
                       observation=lambda *a: {"error": "observation failed"})
        self.assertEqual((broken[0], len(broken[1])), ("interrupted", 1))

    def test_an_empty_answer_after_the_round_completes_the_turn(self):
        status, prompts, texts, _tools = self.run_([plan(BUILD), {"problem": EMPTY_PLAN}], [ran(check("crown", False))])
        self.assertEqual((status, len(prompts)), ("completed", 2))
        self.assertTrue(texts[-1].startswith("The script's self-checks still fail:"))

    def test_a_check_seen_already_holds_nothing_back(self):
        # The crown failed and was shown; a later unverified script that does not re-check it ends the turn.
        status, prompts, texts, _tools = self.run_([plan(BUILD), plan(FIX)],
                                                   [ran(check("crown", False)), ran(check("walls", True, 5.5))])
        self.assertEqual((status, len(prompts)), ("completed", 2))
        self.assertIn("crown", texts[-1])

    def test_jobs_and_their_looks_start_whatever_the_checks_say(self):
        # The job hold deferred a still while a check was open; two turns ended without their final still.
        status, _prompts, _texts, tools = self.run_(
            [plan(BUILD, STILL, LOOK, verify=True), REPLY],
            [ran(check("crown", False)), STARTED, {"success": True, "images": []}])
        self.assertEqual(status, "completed")
        self.assertEqual((tools.count("angelo_job_start"), tools.count("angelo_view")), (1, 1))

    def test_a_failed_lookup_s_note_comes_first_then_the_checks(self):
        status, prompts, _texts, _tools = self.run_([plan(BUILD, LOOKUP), REPLY],
                                                    [ran(check("crown", False)), {"error": "no such object"}])
        self.assertEqual(status, "completed")
        first, second = prompts[1].split("\n")[:2]
        self.assertTrue(first.startswith("The host ran your plan in order."))
        self.assertTrue(second.startswith(ROUND))

    def test_raised_scripts_keep_their_own_budget(self):
        # C14-1: a plan stopped by a raised script sends back its own note; the checks change nothing about it.
        raised = {"error": "NameError: x (line 3)", "line": 3, "script_error": True, "main_data_restored": True,
                  "rolled_back": False, "partial_changes_possible": True, "output": "",
                  "changes": {"added": [], "removed": [], "modified": []}}
        results = [ran(check("crown", False)), raised, ran(check("crown", True, 5.5))]
        status, prompts, _texts, _tools = self.run_([plan(BUILD), plan(FIX), plan(FIX)], results)
        self.assertEqual((status, len(prompts)), ("completed", 3))
        self.assertTrue(prompts[2].startswith("The host ran your plan in order.") and ROUND not in prompts[2])


if __name__ == "__main__":
    unittest.main()
