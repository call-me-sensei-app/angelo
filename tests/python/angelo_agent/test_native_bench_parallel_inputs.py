# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The parallel verdict on malformed inputs, without a model or Blender (break-it E2E-19B L-4).

Pinned here: no input of any shape stops the verdict or its report. A reported-only input that cannot be read
(a trial's task record's workers and threads, its rollouts, its memory, the scorecard's checks, the build labels, the
cost fields) leaves every figure the verdict decides by as it was. An input the verdict needs that is there but cannot
be read (an arm's results, a record, its wall clock or main-loop figures, the grades, and since Amendment F the task
record that shows what the trial was sent) is named as missing evidence, and the verdict is incomplete and never ships.
"""
import json
import unittest

import bench_parallel_report as report
import test_native_bench_parallel as verdicts   # Its run fixture; its tests run there.

DECIDED = ("ships", "ship_rule_holds", "complete", "missing", "faster", "speedup_median", "paired", "unpaired", "lost",
           "crashed", "grades_by_class", "trials_with_a_broken_piece", "floor_holds", "quality_misses",
           "smoothness_blocker", "exercised", "treatment_applied")


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value))


def edit(path, change):
    value = json.loads(path.read_text())
    change(value)
    path.write_text(json.dumps(value))


def record(folder, arm, change):
    edit(folder / arm / report.RESULTS, lambda results: change(results["records"][0]))


class MalformedInputTests(unittest.TestCase):
    def run_folder(self):
        same = {"King": [3, 3]}
        return verdicts.VerdictTests().run_folder({"single": [600, 620], "parallel": [300, 320]},
                                                  {"single": same, "parallel": same})

    def verdict(self, change):
        folder = self.run_folder()
        change(folder)
        result = report.verdict(folder)   # Never raises ...
        report.markdown(result)            # ... nor does its report.
        return {key: result[key] for key in DECIDED}

    def test_a_reported_only_input_never_changes_what_the_verdict_decides(self):
        control = self.verdict(lambda folder: None)
        self.assertTrue(control["ships"])
        trial = lambda folder: folder / "parallel" / "angelo-trial-01"   # noqa: E731
        rollouts = lambda folder: folder / "parallel" / "angelo-trial-01-rollouts" / "rollout-x.jsonl"   # noqa: E731
        cases = {
            # The task record's own message fields are kept: since Amendment F they are a verdict input (below).
            "workers a number": lambda f: edit(trial(f) / "eval-live-agent.json", lambda r: r["trials"][0]["tasks"][
                0].update(workers=5, sent_at_unix="x")),
            "workers malformed": lambda f: edit(trial(f) / "eval-live-agent.json", lambda r: r["trials"][0]["tasks"][
                0].update(workers=[5, {"threads": 7, "worker": 1}], lead_threads=3, worker_processes="x")),
            "rollout lines of any shape": lambda f: write(rollouts(f), "\n".join(json.dumps(line) for line in (
                [1, 2], {"type": "token_usage_record", "payload": [1]},
                {"type": "token_usage_record", "payload": {"usage": "x"}},
                {"type": "event_msg", "payload": {"type": "item_completed", "item": {
                    "type": "DynamicToolCall", "tool": "angelo_workers_start", "content_items": 5}}}))),
            "memory malformed": lambda f: write(f / "parallel" / "angelo-trial-01-launch.json",
                                                {"memory": {"sampled": True, "lead": 5}}),
            "launch a list": lambda f: write(f / "parallel" / "angelo-trial-01-launch.json", [1]),
            "scorecard checks a list": lambda f: write(f / "parallel" / report.SCORECARD, {"sides": [
                {"side": "angelo", "trial": 1, "saved_scene_checks": [1]}]}),
            "scorecard sides a number": lambda f: write(f / "parallel" / report.SCORECARD, {"sides": 3}),
            "build labels a list": lambda f: edit(f / "parallel" / report.RESULTS, lambda r: r.update(meta=[1])),
            "cost fields of the wrong kind": lambda f: record(f, "parallel", lambda r: r.update(
                accounting_partial=5, approved={"a": 1}, list_price_usd="1.0")),
        }
        for case, change in cases.items():
            with self.subTest(case=case):
                self.assertEqual(self.verdict(change), control)

    def test_a_malformed_input_the_verdict_needs_is_named_missing_and_never_ships(self):
        grades = lambda f, change: edit(f / "grades.json", change)   # noqa: E731
        trial = lambda folder: folder / "parallel" / "angelo-trial-01"   # noqa: E731
        cases = {
            "results not JSON": (lambda f: write(f / "single" / report.RESULTS, "{not json"),
                                 "single results: cannot be read"),
            "results a list": (lambda f: write(f / "single" / report.RESULTS, [1]), "single results: not a JSON object"),
            "records an object": (lambda f: write(f / "single" / report.RESULTS, {"records": {"a": 1}}),
                                  "single results: its records are not a list"),
            "a trial number as text": (lambda f: record(f, "parallel", lambda r: r.update(trial="1")),
                                       "parallel results: a record whose trial number is '1'"),
            "main loop a list": (lambda f: record(f, "parallel", lambda r: r.update(main_loop=[1, 2])),
                                 "parallel trial 1: main-loop p95"),
            "wall clock as text": (lambda f: record(f, "single", lambda r: r.update(send_to_idle_ms="600000")),
                                   "single trial 1: wall clock"),
            "grades an object": (lambda f: grades(f, lambda g: g.update(grades={"a": 1})),
                                 "grades.json: its grades not in the grader's shape"),
            "a score as text": (lambda f: grades(f, lambda g: g["grades"][0].update(score="3")),
                                "grades.json: 1 item(s) not in the grader's shape"),
            "grades a list": (lambda f: write(f / "grades.json", [1]), "grades.json: not a JSON object"),
            # Amendment F: a task record that cannot show what the trial was sent is a trial not shown its message.
            "tasks an object": (lambda f: write(trial(f) / "eval-live-agent.json", {"trials": [{"tasks": {"0": {}}}]}),
                                "parallel trial 1: does not show its arm's registered message"),
            "tasks a number": (lambda f: write(trial(f) / "eval-live-agent.json", {"trials": [{"tasks": 5}]}),
                               "parallel trial 1: does not show its arm's registered message"),
            "trials an object": (lambda f: write(trial(f) / "eval-live-agent.json", {"trials": {"a": 1}}),
                                 "parallel trial 1: does not show its arm's registered message"),
        }
        for case, (change, named) in cases.items():
            with self.subTest(case=case):
                result = self.verdict(change)
                self.assertFalse(result["complete"] or result["ships"])
                self.assertTrue(any(entry.startswith(named) for entry in result["missing"]), result["missing"])


if __name__ == "__main__":
    unittest.main()
