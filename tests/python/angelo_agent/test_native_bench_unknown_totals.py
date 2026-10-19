# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Benchmark totals keep an unrecorded measurement unknown: never summed as zero.

A Codex side records no billed cost (``provider_usage.cost_usd`` is Claude
Code's estimate), only the harness's list price (``list_price_usd``); a task
that ran without its API calls, cost or time recorded makes its trial's total
unknown, and each report says what a figure covers. A skipped task sent
nothing, so it adds nothing. Synthetic records in the drivers' shapes; no
process, account or model is involved.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import bench_mcp_replay as replay  # noqa: E402
import bench_mcp_report as report  # noqa: E402
import eval_live_tasks as live  # noqa: E402
import final_runs_report  # noqa: E402
import scenario_report  # noqa: E402
import scenario_specs as specs  # noqa: E402

try:
    import scenario_scorecard as scorecard  # noqa: E402  (imports NumPy through scenario_scoring)
except ImportError:  # CI installs NumPy (run_host_tests --max-skips 0); Blender bundles it.
    scorecard = None

TASKS = live.build_tasks()
HEAD = [task for task in TASKS if task["kind"] != "preview"]


def record(side, trial, task, **values):
    """One completed task: 2 API calls, 1 s, usage with Claude Code's cost on Claude and none on Codex."""
    codex = values.pop("codex", False)
    usage = {"input_tokens": 20000, "cached_input_tokens": 18000, "output_tokens": 100, "api_ms": 400.0}
    if not codex:
        usage["cost_usd"] = values.pop("cost", 0.02)
    else:
        del usage["api_ms"]   # Codex reports no API time.
    base = {"side": side, "trial": trial, "task": task["id"], "kind": task["kind"], "status": "completed",
            "host_check_passed": True, "wall_ms": 1000.0, "api_calls": 2, "provider_usage": usage,
            "list_price_usd": 0.01}
    base.update(values)
    return base


def run(trials=3, codex_mcp=True, tasks=HEAD, change=None):
    """Both sides' head-to-head records; ``change(side, trial, task_id)`` returns updates for one record."""
    records = []
    for side in report.SIDES:
        for trial in range(1, trials + 1):
            for task in tasks:
                values = (change or (lambda *a: {}))(side, trial, task["id"])
                records.append(record(side, trial, task, codex=codex_mcp and side == "mcp", **values))
    return records


class BenchTotalsTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(report.use_tasks, live.build_tasks())

    def test_a_codex_side_is_priced_at_list_prices_with_its_comparison(self):
        summary = report.summarize(run())
        totals = summary["totals"]
        # Five tasks a trial at 0.01 list USD each on both sides: the Codex side records no estimate, so the
        # Claude side's 0.02 estimates are not compared with it (test_native_bench_cost_basis).
        self.assertEqual((totals["mcp"]["cost_usd"]["n"], totals["mcp"]["cost_usd"]["median"]), (3, 0.05))
        self.assertEqual((totals["angelo"]["cost_usd"]["n"], totals["angelo"]["cost_usd"]["median"]), (3, 0.05))
        self.assertIn("list price", summary["cost_basis"]["label"])
        text = report.markdown({"summary": summary, "meta": {"trials": 3}, "observations": [], "tool_usage": {}})
        self.assertNotIn("Claude Code cost estimate", text)
        self.assertIn("Cost basis, the same on both sides for every cost in this report: list price", text)

    def test_a_trial_without_a_tasks_api_calls_or_cost_is_unknown(self):
        def change(side, trial, task):
            if (side, trial, task) == ("angelo", 2, "transform"):
                return {"api_calls": None, "provider_usage": {"input_tokens": 1}, "list_price_usd": None}
            return {"api_calls": 5} if side == "angelo" and trial == 3 else {}
        totals = report.summarize(run(codex_mcp=False, change=change))["totals"]["angelo"]
        # Trial 2's total is unknown, never 8: the median is over trials 1 and 3, and says so.
        self.assertEqual(totals["api_calls"], {"n": 2, "median": 17.5, "p75": 21.25, "min": 10, "max": 25})
        self.assertEqual(totals["api_calls_recorded"], "2 of 3 trials")
        self.assertEqual((totals["cost_usd"]["n"], totals["cost_usd"]["median"]), (2, 0.05))   # List prices.
        self.assertEqual(totals["cost_usd_recorded"], "2 of 3 trials")

    def test_a_missing_estimate_prices_the_comparison_at_list_prices_and_a_skipped_task_adds_nothing(self):
        def change(side, trial, task):
            if (side, trial, task) == ("angelo", 1, "material"):
                return {"provider_usage": {"input_tokens": 20000, "output_tokens": 100}}   # No cost_usd here.
            if (side, trial, task) == ("mcp", 2, "material"):
                return {"status": "skipped", "api_calls": None, "provider_usage": None, "list_price_usd": None,
                        "wall_ms": None}
            return {}
        records = run(codex_mcp=False, change=change)
        summary = report.summarize(records)
        # One Angelo task lacks Claude Code's estimate: both sides at list prices, every trial priced.
        self.assertEqual(summary["cost_basis"]["basis"], "list")
        totals = summary["totals"]
        self.assertEqual((totals["angelo"]["cost_usd"]["n"], totals["angelo"]["cost_usd"]["median"]), (3, 0.05))
        observed = report.observations(records)
        self.assertIn("Model API calls per trial (5 tasks): MCP [10, 8, 10]; Angelo [10, 10, 10].", observed)

    def test_the_session_curve_prices_both_sides_on_one_basis(self):
        tasks = live.tasks_for("long", 12)
        report.use_tasks(tasks)
        head = [task for task in tasks if task["kind"] != "preview"]
        curve = report.session_curve(run(trials=2, tasks=head))
        self.assertEqual(curve["mcp"][10]["cost_usd"]["median"], 0.1)        # 10 tasks at 0.01 list USD.
        self.assertEqual(curve["angelo"][10]["cost_usd"]["median"], 0.1)     # The same basis, not 0.02 estimates.
        both = report.session_curve(run(trials=2, tasks=head, codex_mcp=False))
        self.assertEqual(both["angelo"][10]["cost_usd"]["median"], 0.2)      # Every estimate recorded: estimates.
        unknown = run(trials=2, tasks=head, change=lambda side, trial, task: {
            "api_calls": None, "provider_usage": None, "list_price_usd": None}
            if (side, trial, task) == ("angelo", 1, head[3]["id"]) else {})
        curve = report.session_curve(unknown)["angelo"]
        self.assertEqual(curve[5]["api_calls"]["n"], 1)                       # Trial 1 is unknown from task 4 on.
        self.assertEqual((curve[5]["cost_usd"]["n"], curve[5]["input_tokens_per_call"]["n"]), (1, 2))

    def test_observations_say_unknown_instead_of_zero(self):
        lines = report.observations(run(change=lambda side, trial, task: {"api_calls": None}
                                        if (side, trial, task) == ("angelo", 2, "transform") else {}))
        self.assertIn("Model API time as a share of head-to-head wall time: MCP unknown, Angelo 40%.", lines)
        self.assertIn("Model API calls per trial (5 tasks): MCP [10, 10, 10]; Angelo [10, unknown, 10].", lines)


class FinalRunsReportTests(unittest.TestCase):
    def test_an_unpriced_request_leaves_the_steps_list_price_unknown(self):
        records = [{"side": "angelo", "trial": 1, "task": "a", "status": "completed", "wall_ms": 1000.0,
                    "list_price_usd": 0.01, "uncached_input_tokens": 2000},
                   {"side": "angelo", "trial": 1, "task": "b", "status": "completed", "wall_ms": 1000.0,
                    "list_price_usd": None, "uncached_input_tokens": None},
                   {"side": "angelo", "trial": 1, "task": "c", "status": "skipped"}]
        step = {"id": "h2h-sol-default", "tool": "bench", "sides": ("angelo",)}
        with tempfile.TemporaryDirectory() as campaign:
            (Path(campaign) / step["id"]).mkdir()
            (Path(campaign) / step["id"] / "bench-mcp-vs-angelo.json").write_text(json.dumps({"records": records}))
            row, = final_runs_report.report({}, [step], campaign)["rows"]
        self.assertIsNone(row["list_price_usd"])
        self.assertEqual((row["list_price_known_usd"], row["list_price_recorded"]), (0.01, "1 of 2 requests"))
        self.assertIsNone(row["uncached_input"])
        self.assertEqual(row["uncached_input_recorded"], "1 of 2 requests")


class ReplayTests(unittest.TestCase):
    def test_a_message_without_a_recorded_cost_is_counted_not_added(self):
        output = {"cost_usd": 0.0}
        self.assertEqual(replay.add_cost(output, {"provider_usage": {"cost_usd": 0.5}}), 0.5)
        self.assertIsNone(replay.add_cost(output, {"provider_usage": None}))
        self.assertEqual((output["cost_usd"], output["cost_unknown_messages"]), (0.5, 1))


@unittest.skipIf(scorecard is None, "NumPy is installed in CI and bundled with Blender")
class ScenarioTotalsTests(unittest.TestCase):
    def test_a_turn_without_its_time_or_usage_leaves_the_sides_total_unknown(self):
        turns = [turn["id"] for turn in specs.module("bridge").TURNS]
        rows = [{"task": turn, "status": "completed", "host_check_passed": True, "wall_ms": 20000.0, "api_calls": 4,
                 "tool_call_count": 3, "tool_errors": 0, "provider_usage": {"input_tokens": 1000, "output_tokens": 200,
                                                                            "cost_usd": 0.01}} for turn in turns]
        rows[1].update(wall_ms=None, provider_usage=None, api_calls=None)
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.dict(os.environ, {specs.REFERENCES_ENV: str(Path(temporary, "references"))}):
            folder = Path(temporary, "run", "mcp-trial-01")
            folder.mkdir(parents=True)
            (folder / "trial.json").write_text(json.dumps({"runtime": "codex", "tasks": rows, "scenario": {}}))
            records = [dict(row, side="mcp", trial=1, list_price_usd=None if row["provider_usage"] is None else 0.02)
                       for row in rows]
            card = scorecard.collect(Path(temporary, "run"), "scenario-bridge", {"records": records})
        side, = card["trials"]
        self.assertIsNone(side["turns"][1]["wall_s"])
        for key in ("wall_s", "cost_usd", "list_price_usd", "api_calls", "input_tokens"):
            with self.subTest(metric=key):
                self.assertIsNone(side["metrics"][key])   # One turn unrecorded: unknown, never a partial sum.
        text = scenario_report.markdown(card, {}, {})
        self.assertIn("| %s | pass completed, time unknown |" % turns[1], text)
        self.assertIn("| Cost, USD (basis below) | – |", text)


if __name__ == "__main__":
    unittest.main()
