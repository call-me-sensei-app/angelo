# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host tests of what reports print for a gate run at each gate-set version, and for runs of two versions.

A version-2 run has one figure, the grading contract's: a clean five-task run
reads passed 30 of 30 and a clean forty-task run passed 120 of 120, with every
answer read. Runs of different versions are never totalled or shown together
without saying so. ``test_native_grading_reports.py`` covers version 1.
"""

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import unittest.mock

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import answer_forms_report  # noqa: E402
import bench_mcp_gate as gate  # noqa: E402
import bench_mcp_method  # noqa: E402
import bench_mcp_report  # noqa: E402
import bench_mcp_vs_angelo as bench  # noqa: E402
import eval_gate  # noqa: E402
import eval_grading as grading  # noqa: E402
import eval_live_agent  # noqa: E402
import eval_live_tasks as live  # noqa: E402
import measured_results  # noqa: E402

LINES = {"mesh_names": "Brass Marker, Cube", "box": "1 2 1; LOCATION: -2 -1 0"}


def run_records(tasks, trials, versions, failed=(), lenient=()):
    """Both sides' records of a gate run as the drivers write them at each side's gate-set version.

    ``failed`` and ``lenient`` hold (side, trial, task): a failed check or wrong answer, a leniently read one.
    """
    records = []
    for side, version in versions.items():
        for trial in range(1, trials + 1):
            for task in tasks:
                if task["kind"] == "preview" and side == "mcp":
                    continue
                key = (side, trial, task["id"])
                record = {"side": side, "trial": trial, "task": task["id"], "kind": task["kind"], "status": "completed",
                          "host_check_passed": key not in failed, "wall_ms": 1000.0, "api_calls": 2}
                answer = None
                if version >= 2:
                    record["gate_version"] = version
                    if task["kind"] == "read_only":
                        answer = {"form": task["answer"]["form"], "outcome": "wrong" if key in failed else "right",
                                  "parsed": "leniently" if key in lenient else "exactly",
                                  "stated": LINES[task["answer"]["form"]]}
                if side == "angelo":   # The Angelo driver stamps the grade; the MCP record keeps its host check.
                    host_check = {"scene_unchanged": True, **({"answer": answer} if answer else {})}
                    record["grade"] = grading.derive(dict(record, host_check=host_check), task)
                    record["answer"] = answer
                elif answer:
                    record["host_check"] = {"scene_unchanged": True, "answer": answer}
                records.append(record)
    return records


def results(task_set, count, records):
    tasks = live.tasks_for(task_set, count)
    return {"meta": {"plan": {"task_set": task_set, "angelo_tasks": [task["id"] for task in tasks],
                              "mcp_tasks": [task["id"] for task in tasks if task["kind"] != "preview"]}},
            "records": records}


class VersionTwoTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(bench_mcp_report.use_tasks, live.build_tasks())

    def run_of(self, task_set, trials, count=40, versions=None, **more):
        tasks = live.tasks_for(task_set, count)
        bench_mcp_report.use_tasks(tasks)
        records = run_records(tasks, trials, versions or {"mcp": 2, "angelo": 2}, **more)
        return tasks, records, bench_mcp_report.summarize(records)

    def test_a_clean_five_task_run_reads_thirty_of_thirty_with_every_answer_read(self):
        _, records, summary = self.run_of("default", 5, lenient={("angelo", 3, "read_only_question")})
        self.assertEqual(summary["gate_versions"], {"mcp": 2, "angelo": 2})
        for side in bench_mcp_report.SIDES:
            totals = summary["totals"][side]
            self.assertEqual(totals["graded"], {"passed": 25, "failed": 0, "inconclusive": 0, "ungraded": 0,
                                                "skipped": 0, "total": 25,
                                                "not_run": 0})
            self.assertEqual((totals["answer_unchecked_tasks"], totals["head_to_head_pass_rate"]), ([], 1.0))
            self.assertFalse(summary["tasks"]["read_only_question"][side]["answer_unchecked"])
        self.assertEqual(summary["totals"]["angelo"]["answers"], {"read": 5, "parsed_leniently": 1})
        totals = gate.run_totals(results("default", 40, records))
        self.assertEqual((totals["passed"], totals["runs"], totals["unread_answers"], totals["gate_version"]),
                         (30, 30, 0, 2))
        self.assertEqual(gate.line("opus-5task", totals), "opus-5task (gate set version 2) (scripts reviewed): passed 30, failed 0, "
                         "inconclusive 0, ungraded 0, skipped 0 of 30; answers parsed leniently: 1 of 5")
        text = bench_mcp_report.markdown({"summary": summary, "meta": {}, "records": records})
        self.assertIn("\nGate set version 2.\n", text)
        self.assertRegex(text, r"\| read_only_question \|.*\| 5/5 \(failed 0, inconclusive 0\) "
                               r"\| 5/5 \(failed 0, inconclusive 0\) \|")
        self.assertIn("| Graded (a run is passed only when a check read its result) | passed 25, failed 0, "
                      "inconclusive 0, ungraded 0, "
                      "skipped 0 of 25 | passed 25, failed 0, inconclusive 0, ungraded 0, skipped 0 of 25 |", text)
        self.assertIn("| Answers read, of them parsed leniently | 5, 0 | 5, 1 |", text)
        self.assertNotIn("\\*", text, "nothing is marked as unread")
        self.assertNotIn(gate.UNCHECKED_NOTE, text)
        self.assertNotIn("Not comparable", text)

    def test_a_clean_forty_task_run_reads_one_hundred_and_twenty_of_one_hundred_and_twenty(self):
        _, records, _ = self.run_of("long", 3)
        self.assertEqual(gate.line("opus-long", gate.run_totals(results("long", 40, records))),
                         "opus-long (gate set version 2) (scripts reviewed): passed 120, failed 0, inconclusive 0, ungraded 0, "
                         "skipped 0 of 120; "
                         "answers parsed leniently: 0 of 24")
        self.assertEqual(gate.run_totals(results("long", 40, records), "mcp")["graded"]["passed"], 120)

    def test_a_wrong_answer_is_a_failure_in_every_figure(self):
        _, records, summary = self.run_of("long", 3, failed={("angelo", 2, "question_05")})
        totals = summary["totals"]["angelo"]
        self.assertEqual((totals["graded"]["passed"], totals["graded"]["failed"], totals["head_to_head_pass_rate"],
                          totals["trials_all_five_passed"]), (119, 1, round(119 / 120, 3), 2))
        self.assertEqual(summary["tasks"]["question_05"]["angelo"]["passed"], 2)
        self.assertEqual(gate.run_totals(results("long", 40, records))["unread_answers"], 0)

    def test_sides_of_two_versions_are_named_and_only_the_older_is_marked(self):
        _, records, summary = self.run_of("default", 5, versions={"mcp": 1, "angelo": 2})
        self.assertEqual(summary["gate_versions"], {"mcp": 1, "angelo": 2})
        self.assertEqual([summary["totals"][side]["answer_unchecked_tasks"] for side in ("mcp", "angelo")],
                         [["read_only_question"], []])
        self.assertEqual(summary["totals"]["mcp"]["graded"]["ungraded"], 5)
        text = bench_mcp_report.markdown({"summary": summary, "meta": {}, "records": records})
        self.assertIn("**Not comparable: these runs were recorded with different versions of the gate set "
                      "(version 1, version 2).", text)
        self.assertIn("without reading its answer.** Here: angelo: version 2; mcp: version 1.\n", text)
        self.assertRegex(text, r"\| read_only_question \|.*\| 5/5 \(failed 0, inconclusive 0\)\\\* "
                               r"\| 5/5 \(failed 0, inconclusive 0\) \|")
        self.assertIn(gate.UNCHECKED_NOTE, text)

    def test_other_task_sets_state_no_gate_version(self):
        tasks = live.tasks_for("delegation")
        bench_mcp_report.use_tasks(tasks)
        summary = bench_mcp_report.summarize(run_records(tasks, 1, {"angelo": 1}))
        self.assertNotIn("gate_versions", summary)
        self.assertNotIn("Gate set version", bench_mcp_report.markdown({"summary": summary, "meta": {}, "records": []}))
        self.assertEqual(gate.version_lines(None), [])

    def test_angelo_records_carry_the_version_and_how_the_answer_was_read(self):
        answer = {"form": "mesh_names", "outcome": "right", "answered": ["Brass Marker", "Cube"], "expected": [["x"]],
                  "stated": "2 (Brass Marker and Cube)", "parsed": "leniently", "lenient": ["a count before the names"]}
        trial = {"grading": grading.stamp("default", live.tasks_for("default")), "tasks": [
            {"task": "read_only_question", "kind": "read_only", "status": "completed", "host_check_passed": True,
             "grade": "passed", "host_check": {"scene_unchanged": True, "answer": answer}},
            {"task": "cube_creation", "kind": "edit", "status": "completed", "host_check_passed": True}]}
        question, edit = bench_mcp_report.angelo_records(trial, 1, {})
        self.assertEqual((question["gate_version"], edit["gate_version"], edit["answer"]), (2, 2, None))
        self.assertEqual(question["answer"], {key: answer[key] for key in ("form", "outcome", "answered", "stated",
                                                                          "parsed", "lenient")})
        older = bench_mcp_report.angelo_records({"tasks": trial["tasks"]}, 1, {})[0]
        self.assertEqual((older["gate_version"], eval_gate.record_version(older)), (None, 1))


class ApprovalModeTests(unittest.TestCase):
    """Review should-fix 5: runs of different approval modes are never pooled or totalled without saying so."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.addCleanup(bench_mcp_report.use_tasks, live.build_tasks())

    def bench_run(self, name, approval=None, always=None):
        records = run_records(live.tasks_for("default"), 2, {"angelo": 2})
        for record in records:
            record.update({} if approval is None else {"approval": approval, "always_allow_scripts": always})
        run = Path(self.folder.name) / name
        run.mkdir()
        (run / "bench-mcp-vs-angelo.json").write_text(json.dumps(results("default", 40, records)))
        return str(run)

    def live_run(self, name, trial):
        task = {"task": "cube", "kind": "edit", "status": "completed", "grade": "passed", "send_to_idle_ms": 1000,
                "receipt": {"route": {"served_models": ["claude-opus-5-5"], "effort": "low"}}}
        run = Path(self.folder.name) / name
        run.mkdir()
        (run / "eval-live-agent.json").write_text(json.dumps({"plan": {}, "trials": [dict(trial, tasks=[task])]}))
        return str(run)

    def test_a_report_without_a_mode_was_recorded_in_ask(self):
        self.assertEqual(eval_gate.approval_mode({}), eval_gate.LEGACY_APPROVAL)
        self.assertEqual(eval_gate.approval_mode({"approval": "AUTO_BOUNDED"}), "AUTO_BOUNDED")
        self.assertEqual(eval_gate.approval_mode({"approval": "AUTO_BOUNDED", "always_allow_scripts": True}),
                         "AUTO_BOUNDED + always allow scripts")
        record = bench_mcp_report.angelo_records({"tasks": [{"task": "cube", "kind": "edit", "status": "completed"}],
                                                  "approval": "ASK", "always_allow_scripts": False}, 1, {})[0]
        self.assertEqual(eval_gate.approval_mode(record), "ASK")

    def test_measured_results_refuse_to_pool_modes(self):
        before = self.live_run("before", {"approval_policy": "ASK with test-only typed allowlist"})
        after = self.live_run("after", {"approval": "AUTO_BOUNDED", "always_allow_scripts": False})
        with self.assertRaisesRegex(ValueError, r"different approval modes \(ASK \(legacy\), AUTO_BOUNDED\)"):
            measured_results.measured([before, after])
        pooled = measured_results.measured([before, after], mixed_versions=True)
        self.assertEqual(pooled["claude-opus-5-5"]["low"]["scene_edit"]["tasks"], 2, "only when asked for")
        self.assertEqual(measured_results.measured([after])["claude-opus-5-5"]["low"]["scene_edit"]["tasks"], 1)

    def test_gate_totals_refuse_to_show_modes_together(self):
        auto, ask = self.bench_run("auto", "AUTO_BOUNDED", False), self.bench_run("ask", "ASK", False)
        printed, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(errors):
            self.assertEqual(gate.main([auto, ask]), 2)
        self.assertIn("different approval modes (ASK, AUTO_BOUNDED)", errors.getvalue())
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(gate.main([auto, self.bench_run("auto-2", "AUTO_BOUNDED", False)]), 0)
            self.assertEqual(gate.main([auto, ask, "--mixed-versions"]), 0)

    def test_the_bench_refuses_ask_with_always_allow_before_any_trial(self):
        """Review should-fix 4: the combination fails at parse, before an MCP trial is billed."""
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()) as errors:
            bench.parse(["--angelo-approval", "ask", "--angelo-always-allow-scripts"])
        self.assertIn("--angelo-always-allow-scripts covers Auto chats only", errors.getvalue())
        bench.parse(["--angelo-always-allow-scripts"])


class ComparisonTests(unittest.TestCase):
    """Runs of different gate-set versions are never totalled together without saying so."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.addCleanup(bench_mcp_report.use_tasks, live.build_tasks())
        self.old = self.write("opus-5task-old", run_records(live.tasks_for("default", version=1), 5,
                                                              {"angelo": 1}))
        self.new = self.write("opus-5task-new", run_records(live.tasks_for("default"), 5, {"angelo": 2}))

    def write(self, name, records, task_set="default"):
        run = Path(self.folder.name) / name
        run.mkdir()
        (run / "bench-mcp-vs-angelo.json").write_text(json.dumps(results(task_set, 40, records)))
        return str(run)

    def totals(self, *arguments):
        printed, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(errors):
            code = gate.main(list(arguments))
        return code, printed.getvalue(), errors.getvalue()

    def test_totals_of_one_version_print_and_of_two_are_refused(self):
        self.assertEqual(self.totals(self.new), (0, "opus-5task-new (gate set version 2) (scripts reviewed): passed 30, failed 0, "
                                                 "inconclusive 0, ungraded 0, skipped 0 of 30; "
                                                 "answers parsed leniently: 0 of 5\n", ""))
        code, printed, errors = self.totals(self.old, self.new)
        self.assertEqual((code, printed), (2, ""))
        self.assertIn("Not comparable: these runs were recorded with different versions of the gate set "
                      "(version 1, version 2).", errors)
        self.assertIn("--mixed-versions", errors)
        code, printed, errors = self.totals(self.old, self.new, "--mixed-versions")
        self.assertEqual((code, errors), (0, ""))
        lines = printed.splitlines()
        self.assertTrue(lines[0].startswith("Not comparable"))
        self.assertEqual(lines[1], "opus-5task-old (gate set version 1) (scripts reviewed): host checks 30/30 (5 of them questions whose "
                         "answer no check read); graded: passed 25, failed 0, inconclusive 0, ungraded 5, "
                         "skipped 0 of 30")
        self.assertTrue(lines[2].startswith("opus-5task-new (gate set version 2) (scripts reviewed): passed 30,"))

    def test_the_older_spelling_of_the_totals_command_still_works(self):
        source = (HERE / "bench_mcp_report.py").read_text(encoding="utf-8")
        self.assertIn('if __name__ == "__main__":', source)
        self.assertIn("sys.exit(gate.main())", source)

    def test_same_gate_set_with_different_grading_contracts_is_not_silently_pooled(self):
        tasks = live.tasks_for("default")
        runs = []
        for contract in (3, 4):
            records = run_records(tasks, 1, {"angelo": 2})
            for record in records:
                record["grading_contract"] = contract
            runs.append(self.write("contract-%d" % contract, records))
        self.assertEqual([measured_results.gate_version(run) for run in runs], [2, 2])
        code, printed, errors = self.totals(*runs)
        self.assertEqual((code, printed), (2, ""))
        self.assertIn("different grading contracts (3, 4)", errors)
        code, printed, errors = self.totals(*runs, "--mixed-versions")
        self.assertEqual((code, errors), (0, ""))
        self.assertIn("different grading contracts (3, 4)", printed)
        with self.assertRaisesRegex(ValueError, "different grading contracts"):
            measured_results.measured(runs)
        errors = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(errors):
            self.assertEqual(measured_results.main(runs + ["--mixed-versions"]), 0)
        self.assertIn("different grading contracts (3, 4)", errors.getvalue())
        with self.assertRaisesRegex(ValueError, "unrecorded"):
            measured_results.measured([self.new, runs[1]])

    def test_live_reports_and_normalized_records_keep_the_original_contract(self):
        tasks = live.tasks_for("default")
        trial = {"grading": dict(grading.stamp("default", tasks), contract=3),
                 "tasks": [{"task": "read_only_question", "kind": "read_only", "status": "completed"}]}
        record = bench_mcp_report.angelo_records(trial, 1, {})[0]
        self.assertEqual(record["grading_contract"], 3)
        self.assertEqual(grading.stamp("default", tasks)["contract"], 4)
        self.assertEqual(grading.record_contract({}), 0)
        runs = []
        for contract in (3, 4):
            root = Path(self.folder.name) / ("live-contract-%d" % contract)
            root.mkdir()
            trial["grading"]["contract"] = contract
            (root / "eval-live-agent.json").write_text(json.dumps({"trials": [trial]}))
            runs.append(str(root))
        with self.assertRaisesRegex(ValueError, "different grading contracts"):
            measured_results.measured(runs)

    def test_a_benchmark_report_labels_mixed_contracts_within_one_run(self):
        tasks = live.tasks_for("default")
        bench_mcp_report.use_tasks(tasks)
        records = run_records(tasks, 1, {"angelo": 2, "mcp": 2})
        for record in records:
            record["grading_contract"] = 4 if record["side"] == "angelo" else 3
        text = bench_mcp_report.markdown({"summary": bench_mcp_report.summarize(records),
                                          "meta": {}, "records": records})
        self.assertIn("different grading contracts (3, 4)", text)

    def test_measured_results_refuse_two_versions(self):
        self.assertEqual([measured_results.gate_version(run) for run in (self.old, self.new)], [1, 2])
        with self.assertRaisesRegex(ValueError, "Not comparable"):
            measured_results.measured([self.old, self.new])
        self.assertIsInstance(measured_results.measured([self.old, self.new], mixed_versions=True), dict)
        self.assertIsInstance(measured_results.measured([self.new]), dict)
        errors = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(errors):
            self.assertEqual(measured_results.main([self.old, self.new]), 2)
            self.assertEqual(measured_results.main([self.old, self.new, "--mixed-versions"]), 0)
        self.assertEqual(errors.getvalue().count("Not comparable"), 2)
        other = Path(self.folder.name) / "delegation"
        other.mkdir()
        (other / "eval-live-agent.json").write_text(json.dumps({"trials": [{"tasks": [
            {"task": "audit_question", "kind": "read_only", "status": "completed"}]}]}))
        self.assertIsNone(measured_results.gate_version(other), "a run that asked no gate question has no version")
        self.assertIsInstance(measured_results.measured([self.new, str(other)]), dict)
        coverage = Path(self.folder.name) / "coverage"
        coverage.mkdir()
        (coverage / "eval-live-agent.json").write_text(json.dumps({"trials": [{
            "grading": {"task_set": "coverage"}, "tasks": [{"task": "read_only_question", "kind": "read_only"}]}]}))
        self.assertIsNone(measured_results.gate_version(coverage), "the coverage set is not a versioned gate set")
        recorded_before_stamps = Path(self.folder.name) / "older"
        recorded_before_stamps.mkdir()
        (recorded_before_stamps / "eval-live-agent.json").write_text(json.dumps({"trials": [{"tasks": [
            {"task": "read_only_question", "kind": "read_only"}, {"task": "question_05", "kind": "read_only"}]}]}))
        self.assertEqual(measured_results.gate_version(recorded_before_stamps), 1)

    def test_a_run_directory_s_own_version_is_read_for_a_new_summary(self):
        root = Path(self.folder.name)
        for name, trial in (("new", {"grading": {"gate_version": 2}}), ("old", {}), ("stamped-old", {"grading": {}})):
            (root / name / "angelo-trial-01").mkdir(parents=True)
            (root / name / "angelo-trial-01" / "eval-live-agent.json").write_text(json.dumps({"trials": [trial]}))
        (root / "mcp-only" / "mcp-trial-01").mkdir(parents=True)
        (root / "mcp-only" / "mcp-trial-01" / "trial.json").write_text(json.dumps({"gate_version": 2}))
        (root / "empty").mkdir()
        # A first trial that never started states nothing: the run's plan, a later trial or the MCP side does.
        never_started = {"tasks": [], "error": "No trial report"}
        for name, files in (("plan-says", [{"plan": {"grading": {"gate_version": 2}}, "trials": [never_started]}]),
                            ("later-trial-says", [{"trials": [never_started]},
                                                  {"trials": [{"grading": {"gate_version": 2}}]}])):
            for number, data in enumerate(files, 1):
                (root / name / ("angelo-trial-%02d" % number)).mkdir(parents=True)
                (root / name / ("angelo-trial-%02d" % number) / "eval-live-agent.json").write_text(json.dumps(data))
        (root / "mcp-says" / "angelo-trial-01").mkdir(parents=True)
        (root / "mcp-says" / "angelo-trial-01" / "eval-live-agent.json").write_text(json.dumps(
            {"trials": [never_started]}))
        (root / "mcp-says" / "mcp-trial-01").mkdir()
        (root / "mcp-says" / "mcp-trial-01" / "trial.json").write_text(json.dumps({"gate_version": 2}))
        self.assertEqual([gate.recorded_version(root / name) for name in (
            "new", "old", "stamped-old", "mcp-only", "empty", "plan-says", "later-trial-says", "mcp-says")],
            [2, 1, 1, 2, None, 2, 2, 2])
        args = bench.parse(["--task-set", "long", "--long-tasks", "7"])
        self.assertEqual(eval_gate.tasks_version(bench.tasks(args)), 2)
        args.gate_version = 1
        self.assertEqual(bench.tasks(args), live.tasks_for("long", 7, 1), "an older run is summarized as it was asked")

    def test_a_side_without_valid_records_keeps_the_run_s_version(self):
        tasks = live.tasks_for("default")
        records = [dict(record, invalid="rate_limited") for record in run_records(tasks, 1, {"angelo": 2})]
        run = results("default", 40, records)
        self.assertEqual(gate.run_totals(run)["gate_version"], 2, "rate-limited records still state their version")
        run["records"] = []
        run["meta"]["plan"]["gate_version"] = 2
        self.assertEqual((gate.run_totals(run)["gate_version"], gate.run_totals(run, "mcp")["gate_version"]), (2, 2))
        del run["meta"]["plan"]["gate_version"]
        self.assertEqual(gate.run_totals(run)["gate_version"], 1, "recorded before versions")
        empty = self.write("opus-5task-limited", [], "default")
        data = json.loads((Path(empty) / "bench-mcp-vs-angelo.json").read_text())
        data["meta"]["plan"]["gate_version"] = 2
        (Path(empty) / "bench-mcp-vs-angelo.json").write_text(json.dumps(data))
        code, printed, _ = self.totals(empty, self.new)
        self.assertEqual(code, 0, "a version-2 run with no valid records is not another version")
        self.assertIn("opus-5task-limited (gate set version 2) (scripts reviewed): passed 0, failed 0, inconclusive 0, ungraded 0, "
                      "skipped 0 of 0",
                      printed)

    def test_the_plan_and_the_method_state_the_version(self):
        plan = bench.plan(bench.parse([]))
        self.assertEqual((plan["task_set"], plan["gate_version"]), ("default", 2))
        self.assertEqual(bench.plan(bench.parse(["--task-set", "long"]))["gate_version"], 2)
        self.assertIn("computes the truth from the scene before it sends the prompt",
                      bench_mcp_method.METHOD_QUESTIONS[0])
        self.assertEqual((bench.METHOD, bench.THREATS), (bench_mcp_method.METHOD, bench_mcp_method.THREATS))
        self.assertNotIn(bench_mcp_method.METHOD_QUESTIONS[0], bench.METHOD, "added only to a version-2 run")

    def test_the_live_eval_aggregate_states_what_it_was_graded_as(self):
        tasks = live.tasks_for("long", 7)
        plan = dict(live.plan(1, tasks), grading=grading.stamp("long", tasks))
        report = eval_live_agent.aggregate(plan, {"id": "c"}, [{"tasks": []}])
        self.assertEqual({key: report["grading"][key] for key in ("task_set", "gate_version", "tasks_digest",
                                                                  "contract", "not_run", "answers")},
                         {"task_set": "long", "gate_version": 2, "tasks_digest": grading.tasks_digest(tasks),
                          "contract": grading.CONTRACT, "not_run": 7, "answers": {"read": 0, "parsed_leniently": 0}})
        self.assertNotIn("tasks", report["grading"])


class RebuiltSummaryTests(unittest.TestCase):
    """``--summarize-only`` of a recorded run: its own gate-set version decides the task list and the text."""

    def setUp(self):
        self.addCleanup(bench_mcp_report.use_tasks, live.build_tasks())

    def rebuild(self, version):
        tasks = live.tasks_for("default", version=version)
        trial = {"success": True, "tasks": [
            {"task": task["id"], "kind": task["kind"], "status": "completed", "host_check_passed": True,
             "grade": "passed" if version == 2 or task["kind"] != "read_only" else "ungraded", "total_ms": 1000.0,
             "receipt": {"status": "completed"}} for task in tasks]}
        if version == 2:
            trial["grading"] = grading.stamp("default", tasks)
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            (output / "angelo-trial-01").mkdir()
            (output / "angelo-trial-01" / "eval-live-agent.json").write_text(json.dumps({"trials": [trial]}))
            lines = []   # The session transcript: each prompt as it was sent, then one model call.
            for number, task in enumerate(tasks):
                lines += [{"type": "user", "message": {"content": task["prompt"]}},
                          {"type": "assistant", "message": {"id": "call-%d" % number, "model": "m", "content": []}}]
            (output / "angelo-trial-01-transcript.jsonl").write_text("\n".join(map(json.dumps, lines)))
            printed = io.StringIO()
            with unittest.mock.patch.object(bench, "versions", lambda *args: {}), \
                    contextlib.redirect_stdout(printed):
                self.assertEqual(bench.main(["--summarize-only", folder, "--trials", "1", "--sides", "angelo"]), 0)
            written = json.loads((output / bench.RESULTS).read_text())
            return written, (output / bench.SUMMARY).read_text(), printed.getvalue()

    def test_a_run_of_each_version_is_rebuilt_as_it_was_asked(self):
        for version in (1, 2):
            with self.subTest(version=version):
                written, summary, printed = self.rebuild(version)
                plan = json.loads(printed.splitlines()[0].split(" ", 1)[1])
                self.assertEqual((plan["gate_version"], written["meta"]["plan"]["gate_version"]), (version, version),
                                 "the run's own version, also in the plan that is printed first")
                self.assertIn("\nGate set version %d.\n" % version, summary)
                self.assertEqual(bench_mcp_method.METHOD_QUESTIONS[0] in written["meta"]["method"], version == 2)
                question = next(record for record in written["records"] if record["task"] == "read_only_question")
                self.assertEqual(question["api_calls"], 1, "its model call is credited to it, by its own prompt")
                self.assertEqual(written["summary"]["totals"]["angelo"]["graded"]["ungraded"], 2 - version)
                self.assertEqual(written["summary"]["gate_versions"], {"angelo": version})


class AnswerFormsReportTests(unittest.TestCase):
    def test_it_prints_each_question_run_and_a_total_per_form(self):
        loose = {"form": "box", "outcome": "right", "parsed": "leniently", "lenient": ["the location on its own line"],
                 "stated": "1 2 1\nLOCATION: -2 -1 0"}
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder) / "sol-forms"
            run.mkdir()
            (run / "eval-live-agent.json").write_text(json.dumps({"trials": [{"tasks": [
                {"task": "read_only_question", "kind": "read_only", "host_check": {"answer": {
                    "form": "mesh_names", "outcome": "right", "parsed": "exactly", "stated": "Brass Marker, Cube"}}},
                {"task": "cube_creation", "kind": "edit", "host_check": {"success": True}},
                {"task": "question_05", "kind": "read_only", "host_check": {"answer": loose}},
                {"task": "question_10", "kind": "read_only", "host_check": {"answer": {
                    "form": "box", "outcome": "no_answer"}}}]}]}))
            bench_run = Path(folder) / "sol-5task"
            bench_run.mkdir()
            (bench_run / "bench-mcp-vs-angelo.json").write_text(json.dumps({"records": [
                {"side": "angelo", "trial": 2, "task": "read_only_question", "kind": "read_only", "answer": {
                    "form": "mesh_names", "outcome": "wrong", "parsed": "exactly", "stated": "Cube"}},
                {"side": "mcp", "trial": 2, "task": "read_only_question", "kind": "read_only", "host_check": {
                    "answer": {"form": "mesh_names", "outcome": "right", "parsed": "exactly", "stated": "A, B"}}}]}))
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual((answer_forms_report.main([str(run), str(bench_run)]), answer_forms_report.main([])),
                                 (0, 2))
            with self.assertRaises(ValueError):
                answer_forms_report.question_runs(folder)
        self.assertEqual(printed.getvalue().splitlines(), [
            'Grading contract: unrecorded.',
            'sol-forms angelo trial 1 read_only_question [mesh_names]: right, read exactly: "Brass Marker, Cube"',
            'sol-forms angelo trial 1 question_05 [box]: right, read leniently (the location on its own line): '
            '"1 2 1\\nLOCATION: -2 -1 0"',
            "sol-forms angelo trial 1 question_10 [box]: no_answer, read -: no answer line",
            'sol-5task angelo trial 2 read_only_question [mesh_names]: wrong, read exactly: "Cube"',
            'sol-5task mcp trial 2 read_only_question [mesh_names]: right, read exactly: "A, B"',
            "form box: right 1 of 2 runs; answer line read in 1, leniently in 1",
            "form mesh_names: right 2 of 3 runs; answer line read in 3, leniently in 0"])
        # A benchmark record made from a trial report keeps the form, so its runs are totalled per form too.
        trial = {"tasks": [{"task": "question_05", "kind": "read_only", "status": "completed",
                            "host_check": {"answer": loose}}]}
        self.assertEqual(bench_mcp_report.angelo_records(trial, 1, {})[0]["answer"]["form"], "box")


if __name__ == "__main__":
    unittest.main()
