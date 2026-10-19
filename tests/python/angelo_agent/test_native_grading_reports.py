# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host tests: every report that counts passes states passed, failed, inconclusive, ungraded and skipped.

Synthetic run records only; nothing starts Blender, a provider or a model. The
gate figures the benchmark has always printed (``passed``, the pass rates) are
asserted unchanged beside the contract's own totals.
"""

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import bench_mcp_gate  # noqa: E402
import bench_mcp_report  # noqa: E402
import eval_grading as grading  # noqa: E402
import eval_live_agent  # noqa: E402
import eval_live_tasks as live  # noqa: E402
import eval_survey_tasks as survey  # noqa: E402
import measured_results  # noqa: E402
import scenario_report  # noqa: E402
import survey_report  # noqa: E402


def gate_records(tasks, trials, failed=()):
    """Both sides' records of a gate run as the drivers write them; ``failed`` holds (side, trial, task)."""
    records = []
    for side in bench_mcp_report.SIDES:
        for trial in range(1, trials + 1):
            for task in tasks:
                if task["kind"] == "preview" and side == "mcp":
                    continue
                passed = (side, trial, task["id"]) not in failed
                record = {"side": side, "trial": trial, "task": task["id"], "kind": task["kind"], "status": "completed",
                          "host_check_passed": passed, "wall_ms": 1000.0, "api_calls": 2}
                if side == "angelo":   # The Angelo driver stamps the grade; the MCP driver's is derived.
                    record["grade"] = grading.derive(dict(record, host_check={"scene_unchanged": passed}), task)
                records.append(record)
    return records


def older_gate_total(summary, side):
    """passed/runs as the gate has always been totalled: the sum of each task's ``passed`` over its ``runs``."""
    rows = [row[side] for row in summary["tasks"].values() if side in row]
    return sum(row["passed"] for row in rows), sum(row["runs"] for row in rows)


class BenchReportTests(unittest.TestCase):
    """Runs of gate set version 1, as recorded before answers were read: their figure and what it leaves unread.
    ``test_native_gate_reports.py`` covers version 2 and runs of both."""

    def setUp(self):
        self.addCleanup(bench_mcp_report.use_tasks, live.build_tasks())

    def summarize(self, task_set, trials, count=40, failed=()):
        tasks = live.tasks_for(task_set, count, version=1)
        bench_mcp_report.use_tasks(tasks)
        records = gate_records(tasks, trials, failed)
        return tasks, records, bench_mcp_report.summarize(records)

    def test_the_five_task_gate_keeps_its_figure_and_states_what_no_check_read(self):
        tasks, records, summary = self.summarize("default", 5)
        for side in bench_mcp_report.SIDES:
            totals = summary["totals"][side]
            self.assertEqual([totals[key] for key in ("head_to_head_pass_rate", "trials_all_five_passed", "trials")],
                             [1.0, 5, 5])
            self.assertEqual(totals["graded"], {"passed": 20, "failed": 0, "inconclusive": 0,
                                                "ungraded": 5, "skipped": 0, "total": 25,
                                                "not_run": 0})
            self.assertEqual(totals["answer_unchecked_tasks"], ["read_only_question"])
        self.assertEqual(older_gate_total(summary, "mcp"), (25, 25))
        self.assertEqual(older_gate_total(summary, "angelo"), (30, 30), "with Angelo's staged preview")
        question = summary["tasks"]["read_only_question"]["angelo"]
        self.assertEqual((question["passed"], question["runs"], question["pass_rate"], question["answer_unchecked"]),
                         (5, 5, 1.0, True))
        self.assertEqual(question["graded"], {"passed": 0, "failed": 0, "inconclusive": 0,
                                              "ungraded": 5, "skipped": 0, "total": 5})
        self.assertFalse(summary["tasks"]["transform"]["angelo"]["answer_unchecked"])
        self.assertEqual(summary["tasks"]["transform"]["mcp"]["graded"]["passed"], 5)

    def test_the_forty_task_gate_has_eight_unread_answers_a_trial(self):
        _, _, summary = self.summarize("long", 3, failed={("angelo", 2, "move_01"), ("angelo", 3, "question_05")})
        totals = summary["totals"]["angelo"]
        self.assertEqual(totals["graded"], {"passed": 95, "failed": 2, "inconclusive": 0,
                                            "ungraded": 23, "skipped": 0, "total": 120,
                                            "not_run": 0})
        self.assertEqual(older_gate_total(summary, "angelo"), (118, 120), "the figure the gate has always printed")
        self.assertEqual(summary["totals"]["mcp"]["graded"], {"passed": 96, "failed": 0, "inconclusive": 0,
                                                              "ungraded": 24, "skipped": 0,
                                                              "total": 120, "not_run": 0})
        self.assertEqual(len(totals["answer_unchecked_tasks"]), 8)
        self.assertEqual(summary["tasks"]["question_05"]["angelo"]["graded"],
                         {"passed": 0, "failed": 1, "inconclusive": 0, "ungraded": 2, "skipped": 0, "total": 3})

    def test_the_summary_marks_unread_answers_and_prints_both_totals(self):
        _, records, summary = self.summarize("default", 5)
        text = bench_mcp_report.markdown({"summary": summary, "meta": {}, "records": records})
        self.assertRegex(text, r"\| read_only_question \|.*\| 5/5 \(failed 0, inconclusive 0\)\\\* "
                               r"\| 5/5 \(failed 0, inconclusive 0\)\\\* \|")
        self.assertRegex(text, r"\| transform \|.*\| 5/5 \(failed 0, inconclusive 0\) "
                               r"\| 5/5 \(failed 0, inconclusive 0\) \|")
        self.assertIn("| Graded (a run is passed only when a check read its result) | passed 20, failed 0, "
                      "inconclusive 0, ungraded 5, skipped 0 of 25 | passed 20, failed 0, inconclusive 0, ungraded 5, "
                      "skipped 0 of 25 |", text)
        self.assertIn(bench_mcp_gate.UNCHECKED_NOTE, text)
        self.assertIn("counts these runs as ungraded, not passed", bench_mcp_gate.UNCHECKED_NOTE)
        self.assertIn("\nGate set version 1.\n", text)
        self.assertNotIn("Answers read", text, "version 1 read none")
        self.assertIn("| Head-to-head pass rate, all runs (inconclusive runs) | 1.0 (0) | 1.0 (0) |",
                      text, "the recorded figure is still printed")

    def test_skipped_and_failed_runs_stay_in_the_denominator(self):
        tasks = live.tasks_for("default", version=1)
        bench_mcp_report.use_tasks(tasks)
        runs = (("completed", True), ("completed", False), ("skipped", None), ("timeout", None), ("completed", None))
        records = [{"side": "angelo", "trial": 1, "task": "transform", "kind": "edit", "status": status,
                    "host_check_passed": passed, "wall_ms": 1.0} for status, passed in runs]
        graded = bench_mcp_report.summarize(records)["tasks"]["transform"]["angelo"]["graded"]
        self.assertEqual(graded, {"passed": 1, "failed": 2, "inconclusive": 0, "ungraded": 1, "skipped": 1, "total": 5})

    def test_a_finished_runs_totals_keep_the_gate_figure_and_state_the_contracts(self):
        tasks, records, _ = self.summarize("long", 3, failed={("angelo", 1, "move_01")})
        records.append(dict(records[-1], trial=4, invalid="rate_limited"))
        results = {"meta": {"plan": {"task_set": "long", "angelo_tasks": [task["id"] for task in tasks]}},
                   "records": records}
        self.assertEqual(bench_mcp_gate.run_totals(results), {
            "runs": 120, "passed": 119, "unread_answers": 24, "gate_version": 1, "grading_contracts": [0],
        "approval_modes": ["ASK (legacy)"],   # Recorded before modes were stamped: every run then was in Ask.
        "scripts": "reviewed",   # The gate line names the scripts (bench_integrity.script_mode).
            "answers": {"read": 0, "parsed_leniently": 0},
            "graded": {"passed": 95, "failed": 1, "inconclusive": 0,
                       "ungraded": 24, "skipped": 0, "total": 120, "not_run": 0}})
        # A trial that stopped after its first three tasks: the 37 it never reached are stated, not missing.
        stopped = dict(results, records=[record for record in records if record["side"] == "angelo"
                                         and (record["trial"] < 3 or record["task"] in results["meta"]["plan"][
                                             "angelo_tasks"][:3]) and not record.get("invalid")])
        self.assertEqual((bench_mcp_gate.run_totals(stopped)["graded"]["total"],
                          bench_mcp_gate.run_totals(stopped)["graded"]["not_run"]), (83, 37))
        bench_mcp_report.use_tasks(tasks)
        self.assertEqual(bench_mcp_report.summarize(stopped["records"])["totals"]["angelo"]["graded"]["not_run"], 37)
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "opus-long"
            run.mkdir()
            (run / "bench-mcp-vs-angelo.json").write_text(json.dumps(results))
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(bench_mcp_gate.main([str(run)]), 0)
                with self.assertRaises(SystemExit):
                    bench_mcp_gate.main([])
        self.assertEqual(printed.getvalue(), "opus-long (gate set version 1) (scripts reviewed): host checks 119/120 (24 of them "
                         "questions whose answer no check read); graded: passed 95, failed 1, inconclusive 0, "
                         "ungraded 24, "
                         "skipped 0 of 120\n")

    def test_angelo_records_carry_the_drivers_grade(self):
        trial = {"tasks": [{"task": "read_only_question", "kind": "read_only", "status": "completed",
                            "host_check_passed": True, "grade": "ungraded"}]}
        self.assertEqual(bench_mcp_report.angelo_records(trial, 1, {})[0]["grade"], "ungraded")


class LiveEvalAggregateTests(unittest.TestCase):
    def test_the_aggregate_states_every_state(self):
        tasks = live.tasks_for("default", version=1)
        records = [{"task": "read_only_question", "kind": "read_only", "status": "completed", "host_check_passed": True,
                    "grade": "ungraded", "receipt": {"status": "completed"}},
                   {"task": "cube_creation", "kind": "edit", "status": "completed", "host_check_passed": True,
                    "grade": "passed", "receipt": {"status": "completed"}},
                   {"task": "transform", "kind": "edit", "status": "completed", "host_check_passed": False,
                    "grade": "failed", "receipt": {"status": "completed"}},
                   {"task": "material", "kind": "edit", "status": "skipped", "receipt": None}]
        report = eval_live_agent.aggregate(live.plan(1, tasks), {"id": "c"}, [{"tasks": records}, {
            "invalid": "rate_limited", "tasks": [dict(records[1])]}])
        self.assertEqual(report["grading"], {"passed": 1, "failed": 1, "inconclusive": 0,
                                             "ungraded": 1, "skipped": 1, "total": 4,
                                             "not_run": 2, "contract": 0,
                                             "answers": {"read": 0, "parsed_leniently": 0}})
        self.assertEqual(report["tasks"]["read_only_question"]["ungraded"], 1)
        self.assertIn("an unchecked answer or edit is ungraded", report["method"])


class MeasuredResultsTests(unittest.TestCase):
    def measured(self, records):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run"
            run.mkdir()
            (run / "bench-mcp-vs-angelo.json").write_text(json.dumps({
                "meta": {"plan": {"model": "claude-opus-5-5", "effort": "low"}}, "records": records}))
            return measured_results.measured([run])["claude-opus-5-5"]["low"]

    def record(self, kind, passed=True, **more):
        return dict({"side": "angelo", "task": "t", "kind": kind, "status": "completed", "host_check_passed": passed,
                     "wall_ms": 1000.0, "provider_usage": {"cost_usd": 0.01}, "models": ["claude-opus-5-5"]}, **more)

    def counted(self, entry):
        return entry["tasks"], entry["ungraded"], entry["pass_rate"]

    def test_unread_answers_cannot_admit_a_model(self):
        entries = self.measured([self.record("read_only") for _ in range(24)] + [self.record("edit")])
        self.assertEqual(self.counted(entries["scene_read"]), (0, 24, 0.0))
        self.assertEqual(entries["scene_read"]["p75_ms"], 1000.0, "their latency is still a measurement")
        self.assertEqual(self.counted(entries["scene_edit"]), (1, 0, 1.0))

    def test_checked_answers_count(self):
        entries = self.measured([self.record("read_only", grade="passed"),
                                 self.record("read_only", False, grade="failed"),
                                 self.record("read_only", None, grade="ungraded")])
        self.assertEqual(self.counted(entries["scene_read"]), (2, 1, 0.5))


def survey_record(task, grade, **more):
    passed = {"passed": True, "failed": False, "ungraded": None}.get(grade)
    receipt = {"status": "completed", "phases": {"total_ms": 4000.0}, "counts": {}}
    record = {"trial": 1, "task": task, "kind": "edit", "status": "skipped" if grade == "skipped" else "completed",
              "approved": [], "declined": [], "host_check_passed": passed, "grade": grade, "model_rounds": 1,
              "receipt": None if grade == "skipped" else receipt}
    return dict(record, **more)


def answered(outcome, image=False, reason=None):
    answer = {"outcome": outcome, "image_captured": image}
    if reason:
        answer["reason"] = reason
    return {"scene_unchanged": True, "answer": answer}


class SurveyReportTests(unittest.TestCase):
    def setUp(self):
        self.tasks = survey.build_survey_tasks()
        self.stamp = grading.stamp("survey", self.tasks)
        self.records = [
            survey_record("create_cube", "passed"), survey_record("extrude_top", "failed"),
            survey_record("save_blend", "ungraded", host_check={"ungraded": True, "reason": survey.FILE_RESULT}),
            survey_record("timeline", "ungraded", host_check={"success": True, "vacuous": "the checks already held"}),
            survey_record("glass_sphere", "skipped"),
            survey_record("view_check", "passed", kind="read_only", host_check=answered("right", True)),
            survey_record("view_check_cropped", "failed", kind="read_only", host_check=answered("unsupported")),
            survey_record("mesh_stats", "ungraded", kind="read_only",
                          host_check=answered("ungraded", reason="no mesh"))]

    def build(self, folder, trials):
        (Path(folder) / "eval-live-agent.json").write_text(json.dumps({"trials": trials}))
        return survey_report.build_report([folder])

    def test_the_report_states_every_state_and_why_runs_are_ungraded(self):
        with tempfile.TemporaryDirectory() as folder:
            value = self.build(folder, [{"grading": self.stamp, "tasks": self.records}])
        self.assertEqual(value["grading"], {"passed": 2, "failed": 2, "inconclusive": 0,
                                            "ungraded": 3, "skipped": 1, "total": 8,
                                            "not_run": 57, "contract": grading.CONTRACT})
        self.assertEqual(value["survey"], {"version": survey.VERSION, "tasks_digest": self.stamp["tasks_digest"],
                                           "comparable": True, "recorded_with": [{
                                               "version": survey.VERSION, "tasks_digest": self.stamp["tasks_digest"],
                                               "trials": 1}]})
        rows = {row["id"]: row for row in value["tasks"]}
        self.assertEqual((rows["save_blend"]["grades"], rows["save_blend"]["basis"], rows["save_blend"]["outcome"]),
                         ({"ungraded": 1}, "ungraded", "native"), "the routing outcome is not a grade")
        self.assertEqual(rows["glass_sphere"]["grades"], {"skipped": 1})
        self.assertEqual({row["id"]: (row["declared"], row["reasons"]) for row in value["ungraded"]}, {
            "save_blend": (True, [survey.FILE_RESULT]), "timeline": (False, ["the checks already held"]),
            "mesh_stats": (False, ["no mesh"])})
        questions = {row["id"]: row for row in value["questions"]}
        self.assertEqual(len(questions), 6)
        self.assertEqual((questions["view_check"]["answers"], questions["view_check"]["image_captured"],
                          questions["view_check"]["needs_image"]), ({"right": 1}, 1, "SCENE_CAMERA"))
        cropped = questions["view_check_cropped"]
        self.assertEqual((cropped["answers"], cropped["image_captured"]), ({"unsupported": 1}, 0))
        text = survey_report.markdown(value)
        self.assertIn("Grading, in runs: passed 2, failed 2, inconclusive 0, "
                      "ungraded 3, skipped 1 of 8; 57 planned runs never ran. "
                      "A run is passed only when a check read its result", text)
        self.assertIn("Survey version 2, task digest %s." % self.stamp["tasks_digest"], text)
        for heading in ("## Questions (answer checked against the harness's ground truth)",
                        "## Ungraded runs (never counted as passed)"):
            self.assertIn(heading, text)
        self.assertIn("| view_check_cropped | yes_no | SCENE_CAMERA | 1 | unsupported 1 | 0 | 0 |", text)
        self.assertIn("| save_blend | 1 | yes | %s |" % survey.FILE_RESULT, text)
        self.assertRegex(text, r"\| create_cube \| modeling \| 3 \| native \| native 1 \| passed 1 \|")
        self.assertNotIn("Not comparable", text)

    def test_a_trial_that_died_before_its_first_task_is_no_other_version(self):
        with tempfile.TemporaryDirectory() as folder:
            value = self.build(folder, [{"grading": self.stamp, "tasks": self.records},
                                        {"error": "the application closed before the first request"}])
        self.assertEqual((value["trials"], value["survey"]["comparable"]), (2, True))
        self.assertEqual(value["survey"]["recorded_with"], [{
            "version": survey.VERSION, "tasks_digest": self.stamp["tasks_digest"], "trials": 1}])
        self.assertEqual((value["grading"]["total"], value["grading"]["not_run"]), (8, 2 * len(self.tasks) - 8),
                         "the dead trial's tasks are stated as never run")
        self.assertNotIn("Not comparable", survey_report.markdown(value))

    def test_a_captured_image_is_counted_from_the_turns_evidence(self):
        refused = survey_record("view_check", "failed", kind="read_only", host_check=answered("no_answer"),
                                evidence={"images": 1, "current_scene_camera": 1})
        blind = survey_record("view_check_cropped", "failed", kind="read_only", host_check=answered("no_answer"),
                              evidence={"images": 0, "current_scene_camera": 0})
        with tempfile.TemporaryDirectory() as folder:
            value = self.build(folder, [{"grading": self.stamp, "tasks": [refused, blind]}])
        questions = {row["id"]: row for row in value["questions"]}
        self.assertEqual((questions["view_check"]["image_captured"], questions["view_check"]["answers"]),
                         (1, {"no_answer": 1}), "looked, then gave no answer")
        self.assertEqual(questions["view_check_cropped"]["image_captured"], 0)

    def test_answers_read_leniently_are_counted_per_question(self):
        loose = dict(answered("right", True)["answer"], parsed="leniently", lenient=["a clause after the answer"])
        exact = dict(answered("right", True)["answer"], parsed="exactly")
        records = [survey_record("view_check", "passed", kind="read_only", host_check={"answer": exact}),
                   survey_record("view_check_cropped", "passed", kind="read_only", host_check={"answer": loose}),
                   survey_record("view_check_cropped", "failed", kind="read_only", host_check=answered("no_answer"))]
        with tempfile.TemporaryDirectory() as folder:
            value = self.build(folder, [{"grading": self.stamp, "tasks": records}])
        questions = {row["id"]: row for row in value["questions"]}
        self.assertEqual([questions[name]["parsed_leniently"] for name in ("view_check", "view_check_cropped",
                                                                         "mesh_stats")], [0, 1, 0])
        text = survey_report.markdown(value)
        self.assertIn("| Task | Answer form | Needs an image | Attempted | Answers | Read leniently | Runs with an "
                      "image captured |", text)
        self.assertIn("| view_check_cropped | yes_no | SCENE_CAMERA | 2 | no_answer 1, right 1 | 1 | 1 |", text)

    def test_a_recorded_reason_cannot_break_the_table(self):
        reason = "the check | broke\nover two lines"
        record = survey_record("mesh_stats", "ungraded", kind="read_only",
                               host_check=answered("ungraded", reason=reason))
        with tempfile.TemporaryDirectory() as folder:
            text = survey_report.markdown(self.build(folder, [{"grading": self.stamp, "tasks": [record]}]))
        self.assertIn("| mesh_stats | 1 | no | the check / broke over two lines |", text)
        for line in text.splitlines():
            if line.startswith("| mesh_stats | 1 |"):
                self.assertEqual(line.count("|"), 5)

    def test_runs_of_another_survey_version_are_named_and_refused_by_default(self):
        older = [dict(survey_record("view_check", "x", kind="read_only", host_check={"scene_unchanged": True}),
                      host_check_passed=True)]
        del older[0]["grade"]
        with tempfile.TemporaryDirectory() as folder:
            value = self.build(folder, [{"tasks": older},
                                        {"grading": dict(self.stamp, tasks_digest="0" * 16), "tasks": []}])
            self.assertFalse(value["survey"]["comparable"])
            self.assertEqual([(entry["version"], entry["tasks_digest"]) for entry in value["survey"]["recorded_with"]],
                             [(1, None), (survey.VERSION, "0" * 16)])
            self.assertEqual(value["grading"]["ungraded"], 1, "an unchanged scene of the older survey read no answer")
            self.assertIn("**Not comparable: these runs were recorded with another version of the survey (version 1",
                          survey_report.markdown(value))
            errors = io.StringIO()
            with contextlib.redirect_stderr(errors), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(survey_report.main([folder]), 2)
                self.assertFalse((Path(folder) / survey_report.REPORT_MD).exists())
                self.assertEqual(survey_report.main([folder, "--mixed-versions"]), 0)
            self.assertIn("--mixed-versions", errors.getvalue())
            self.assertIn("Not comparable", (Path(folder) / survey_report.REPORT_MD).read_text())


class ScenarioReportTests(unittest.TestCase):
    def card(self, turns):
        plan = {"title": "Bridge", "turns": [turn["turn"] for turn in turns], "checks": 3, "references": "refs",
                "reference_digest": "abc"}
        passed = sum(turn["passed"] for turn in turns)
        ungraded = sum(turn.get("grade") == "ungraded" for turn in turns)
        metrics = {"turns_total": len(turns) + 1, "turns_attempted": len(turns), "turns_passed": passed,
                   "turns_ungraded": ungraded, "turns_failed": len(turns) - passed - ungraded,
                   "approvals": {}, "errors": {}}
        return {"scenario": "bridge", "plan": plan, "trials": [{"side": "angelo", "trial": 1, "turns": turns,
                                                                "metrics": metrics}]}

    def turn(self, name, grade, status="completed", failed=()):
        return {"turn": name, "status": status, "passed": grade == "passed", "grade": grade, "wall_s": 12.0,
                "failed_checks": list(failed)}

    def test_an_ungraded_turn_is_not_a_pass_and_not_a_failed_check(self):
        card = self.card([self.turn("valley", "passed"), self.turn("bridge", "ungraded"),
                          self.turn("lamps", "failed", failed=[{"label": "glow", "details": {}}]),
                          self.turn("render", "failed", status="error")])
        text = scenario_report.markdown(card, {}, {})
        self.assertIn("| Turns passed / failed / inconclusive / ungraded / not attempted (closed checks) | "
                      "1 / 2 / 0 / 1 / 1 of 5 |", text)
        self.assertIn("| bridge | UNGRADED completed, 12 s |", text)
        self.assertIn("| valley | pass completed, 12 s |", text)
        self.assertIn("| lamps | FAIL completed, 12 s |", text)
        self.assertEqual(scenario_report.struggles(card)[0],
                         "- **Angelo trial 1**: 1 failed check(s), 0 inconclusive, ungraded (no check ran) on bridge, "
                         "not passed without a recorded check on render.")

    def test_every_turn_passed_is_said_only_when_each_did(self):
        ungraded = self.card([self.turn("valley", "ungraded"), self.turn("bridge", "ungraded")])
        self.assertNotIn("every turn passed", "\n".join(scenario_report.struggles(ungraded)))
        self.assertEqual(scenario_report.struggles(self.card([self.turn("valley", "passed")])),
                         ["- **Angelo trial 1**: every turn passed its checks."])

    def test_the_scorecard_counts_ungraded_turns(self):
        import scenario_scorecard   # Imports NumPy through scenario_scoring, as CI and Blender have it.
        rows = [{"task": "valley", "status": "completed", "host_check_passed": True, "host_check": {"results": []}},
                {"task": "bridge", "status": "completed", "host_check_passed": None, "host_check": {"ungraded": True}},
                {"task": "lamps", "status": "timeout", "host_check_passed": False, "host_check": {}}]
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "angelo-trial-01"
            folder.mkdir()
            (folder / "eval-live-agent.json").write_text(json.dumps({"trials": [{"tasks": rows}]}))
            side = scenario_scorecard.score_side("bridge", scenario_scorecard.angelo_trial(folder), [])
        self.assertEqual([turn["grade"] for turn in side["turns"]], ["passed", "ungraded", "failed"])
        self.assertEqual([side["metrics"][key] for key in ("turns_passed", "turns_ungraded", "turns_failed",
                                                           "turns_attempted")], [1, 1, 1, 3])

    def test_an_inconclusive_turn_and_saved_scene_check_are_counted_apart(self):
        import scenario_scorecard
        undecided = {"label": "roof colour", "passed": False, "inconclusive": True, "details": {}}
        rows = [{"task": "valley", "status": "completed", "host_check_passed": False,
                 "host_check": {"success": False, "results": [{"label": "deck", "passed": True}, undecided]}},
                {"task": "bridge", "status": "completed", "host_check_passed": False,
                 "host_check": {"success": False, "results": [undecided, {"label": "rails", "passed": False}]}}]
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "angelo-trial-01"
            folder.mkdir()
            (folder / "eval-live-agent.json").write_text(json.dumps({"trials": [{"tasks": rows}]}))
            side = scenario_scorecard.score_side("bridge", scenario_scorecard.angelo_trial(folder), [])
        self.assertEqual([turn["grade"] for turn in side["turns"]], ["inconclusive", "failed"])
        self.assertEqual([side["metrics"][key] for key in ("turns_passed", "turns_failed", "turns_inconclusive")],
                         [0, 1, 1])
        final = {"passed": 1, "total": 3, "results": [{"label": "deck", "passed": True}, undecided,
                                                      {"label": "rails", "passed": False}]}
        checks = scenario_scorecard.end_of_session(final)
        self.assertEqual((checks["passed"], checks["failed_count"], checks["inconclusive"], checks["total"]),
                         (1, 1, 1, 3))
        self.assertEqual((checks["failed"], checks["inconclusive_checks"]), (["rails"], ["roof colour"]))
        self.assertEqual(scenario_report.compliance(checks), "1 / 1 / 1 of 3")
        self.assertEqual(scenario_report.compliance({"passed": 2, "total": 3, "failed": ["rails"]}), "2 / 1 / 0 of 3")

    def test_the_report_shows_an_inconclusive_turn_as_neither_pass_nor_fail(self):
        undecided = {"label": "roof colour", "details": {"reason": "inconclusive: image"}, "inconclusive": True}
        card = self.card([self.turn("valley", "passed"), self.turn("roof", "inconclusive", failed=[undecided])])
        card["trials"][0]["metrics"].update(turns_inconclusive=1, turns_failed=0)
        text = scenario_report.markdown(card, {}, {})
        self.assertIn("| Turns passed / failed / inconclusive / ungraded / not attempted (closed checks) | "
                      "1 / 0 / 1 / 0 / 1 of 3 |", text)
        self.assertIn("| roof | INCONCLUSIVE completed, 12 s |", text)
        self.assertEqual(scenario_report.struggles(card)[:2], [
            "- **Angelo trial 1**: 0 failed check(s), 1 inconclusive.",
            '  - `roof` / roof colour (inconclusive): {"reason": "inconclusive: image"}'])


class SummaryInconclusiveTests(unittest.TestCase):
    """SUMMARY.md: a run whose only unmet checks were undecided is counted beside the passed and failed ones."""

    def test_pass_cells_rate_and_graded_row_show_inconclusive_runs(self):
        tasks = live.tasks_for("default")
        bench_mcp_report.use_tasks(tasks)
        records = gate_records(tasks, 2)
        undecided = {"success": False, "status": "inconclusive",
                     "checks": [{"status": "inconclusive", "passed": False}]}
        for record in records:
            if record["task"] == "transform" and record["trial"] == 1:
                record.update(host_check_passed=False, host_check=undecided)
                record.pop("grade", None)
        summary = bench_mcp_report.summarize(records)
        text = bench_mcp_report.markdown({"summary": summary, "meta": {}, "records": records})
        self.assertRegex(text, r"\| transform \|.*\| 1/2 \(failed 0, inconclusive 1\) "
                               r"\| 1/2 \(failed 0, inconclusive 1\) \|")
        self.assertIn("| Head-to-head pass rate, all runs (inconclusive runs) | 0.9 (1) | 0.9 (1) |", text)
        self.assertIn("passed 7, failed 0, inconclusive 1, ungraded 2, skipped 0 of 10", text)   # 2 v1 questions.
        self.assertEqual(summary["totals"]["angelo"]["graded"]["inconclusive"], 1)


if __name__ == "__main__":
    unittest.main()
