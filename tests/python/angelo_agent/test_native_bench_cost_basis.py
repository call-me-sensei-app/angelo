# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One cost basis per comparison, the same on both sides, stated once (``bench_totals.cost_basis``).

Claude Code's own estimate (``provider_usage.cost_usd``) when it was recorded
for everything every side of the comparison ran; otherwise the harness's
list price (``list_price_usd``) on both sides. A Codex side records no
estimate, so a comparison with one is priced at list prices on both sides.
Never one side's estimate against the other's list price. Synthetic records
in the drivers' shapes; no process, account or model is involved.
"""

import copy
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

import bench_mcp_report as report  # noqa: E402
import eval_live_tasks as live  # noqa: E402
import final_runs_report  # noqa: E402
import oneshot_group as group  # noqa: E402
import oneshot_report  # noqa: E402
import oneshot_tables as tables  # noqa: E402
import scenario_report  # noqa: E402
import scenario_specs as specs  # noqa: E402
from test_native_bench_unknown_totals import run  # noqa: E402
from test_native_oneshot_report import ReportTests  # noqa: E402
from test_native_oneshot_trials import MINIS, five  # noqa: E402

try:
    import scenario_scorecard as scorecard  # noqa: E402  (imports NumPy through scenario_scoring)
except ImportError:  # CI installs NumPy (run_host_tests --max-skips 0); Blender bundles it.
    scorecard = None

ESTIMATE, LIST = "Claude Code's own estimate", "list price"


def markdown(records):
    return report.markdown({"summary": report.summarize(records), "meta": {"trials": 3}, "observations": [],
                            "tool_usage": {}, "session_curve": report.session_curve(records)})


def basis_lines(text):
    return [line for line in text.splitlines() if line.startswith("Cost basis")]


class BenchSummaryTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(report.use_tasks, live.build_tasks())

    def test_a_codex_side_puts_both_sides_on_list_prices(self):
        summary = report.summarize(run())   # A Codex MCP side (no estimate) against a Claude Angelo side.
        self.assertEqual(summary["cost_basis"]["basis"], "list")
        for side in report.SIDES:   # Five tasks at 0.01 list USD on both sides; never Angelo's 0.02 estimates.
            self.assertEqual(summary["totals"][side]["cost_usd"]["median"], 0.05, side)
            self.assertNotIn("cost_basis", summary["totals"][side], "Stated once, for the comparison")
        lines = basis_lines(markdown(run()))
        self.assertEqual(len(lines), 1, lines)
        self.assertIn(LIST, lines[0])
        self.assertIn("missing for 15 of 30 head-to-head tasks", lines[0])

    def test_two_claude_sides_with_every_estimate_use_claude_codes_estimate(self):
        summary = report.summarize(run(codex_mcp=False))
        self.assertEqual(summary["cost_basis"]["basis"], "reported")
        for side in report.SIDES:
            self.assertEqual(summary["totals"][side]["cost_usd"]["median"], 0.1, side)
        lines = basis_lines(markdown(run(codex_mcp=False)))
        self.assertEqual(len(lines), 1)
        self.assertIn(ESTIMATE, lines[0])

    def test_one_missing_estimate_moves_both_sides_to_list_prices(self):
        def change(side, trial, task):
            if (side, trial, task) == ("angelo", 1, "material"):
                return {"provider_usage": {"input_tokens": 20000, "output_tokens": 100}}   # No cost_usd.
            return {}
        summary = report.summarize(run(codex_mcp=False, change=change))
        self.assertEqual(summary["cost_basis"]["basis"], "list")
        self.assertEqual(summary["totals"]["mcp"]["cost_usd"]["median"], 0.05, "The MCP side moves too")
        self.assertEqual(summary["totals"]["angelo"]["cost_usd"]["median"], 0.05)

    def test_the_session_curve_and_the_per_task_costs_use_the_comparisons_basis(self):
        tasks = live.tasks_for("long", 12)
        report.use_tasks(tasks)
        head = [task for task in tasks if task["kind"] != "preview"]
        curve = report.session_curve(run(trials=2, tasks=head))
        self.assertEqual(curve["angelo"][10]["cost_usd"]["median"], 0.1, "10 tasks at 0.01 list USD, not 0.02")
        self.assertNotIn("cost_basis", curve["angelo"][10])
        report.use_tasks(live.build_tasks())
        text = markdown(run())
        row = next(line for line in text.splitlines() if line.startswith("| transform | angelo | 20000 |"))
        self.assertTrue(row.endswith("| 0.0100 |"), row)   # The task's list price, as on the MCP side.
        self.assertIn("| Cost USD |", text.split("## Tokens per task")[1].splitlines()[2])


class FinalRunsTests(unittest.TestCase):
    def report(self, records):
        step = {"id": "h2h-astra-default", "tool": "bench", "sides": ("mcp", "angelo")}
        with tempfile.TemporaryDirectory() as campaign:
            (Path(campaign) / step["id"]).mkdir()
            (Path(campaign) / step["id"] / "bench-mcp-vs-angelo.json").write_text(json.dumps({"records": records}))
            return final_runs_report.report({}, [step], campaign)

    def test_a_step_is_priced_on_one_basis_for_both_sides(self):
        result = self.report(run(trials=1))   # A Codex MCP side: list prices on both sides.
        self.assertEqual(result["cost_basis"]["h2h-astra-default"]["basis"], "list")
        self.assertEqual({row["side"]: row["cost_usd"] for row in result["rows"]}, {"mcp": 0.05, "angelo": 0.05})
        claude = self.report(run(trials=1, codex_mcp=False))
        self.assertEqual(claude["cost_basis"]["h2h-astra-default"]["basis"], "reported")
        self.assertEqual({row["side"]: row["cost_usd"] for row in claude["rows"]}, {"mcp": 0.1, "angelo": 0.1})


class OneShotTests(unittest.TestCase):
    def sides(self, codex):
        angelo, other = ReportTests().sides()
        if codex:
            other = dict(other, cost_reported_usd=None)   # Codex reports no estimate.
        return angelo, other

    def cost_row(self, lines):
        return next(line for line in lines if line.startswith("| Cost"))

    def test_the_sides_table_states_one_basis_and_prices_both_columns_on_it(self):
        for codex in (True, False):
            with self.subTest(codex=codex):
                angelo, other = self.sides(codex)
                row = self.cost_row(tables.table([("MCP 1", other, {"verdict": "failed", "checks": "1 / 2"}),
                                                  ("Angelo 1", angelo, {"verdict": "passed", "checks": "2 / 2"})]))
                field = "cost_list_usd" if codex else "cost_reported_usd"
                self.assertIn(LIST if codex else ESTIMATE, row)
                self.assertEqual(row.split(" | ")[1:], [str(other[field]), str(angelo[field]) + " |"])

    def test_a_tests_report_and_scorecard_state_the_basis_once(self):
        result = ReportTests().result()
        result["sides"][0]["path"]["cost_reported_usd"] = None
        result["cost_basis"] = tables.cost_basis([side["path"] for side in result["sides"]])
        self.assertEqual(result["cost_basis"]["basis"], "list")
        text = oneshot_report.markdown(result)
        self.assertEqual(len(basis_lines(text)), 1, basis_lines(text))

    def test_the_trials_tables_use_one_basis_for_every_row(self):
        sides = five()   # Five Angelo trials (an estimate each) and five MCP (Codex) ones without.
        for item in sides:
            if "Codex" in item["label"]:
                item["path"]["cost_reported_usd"] = None
        self.assertIn("| Cost USD |", tables.trials_header()[0])
        rows = tables.trials_rows(sides)
        listed, estimated = (tables._spread([sides[0]["path"][field]] * 5, 4)
                             for field in ("cost_list_usd", "cost_reported_usd"))
        self.assertIn("| %s |" % listed, rows[0], "Angelo's list price, as the MCP side's")
        self.assertNotIn("| %s |" % estimated, rows[0])
        with tempfile.TemporaryDirectory() as base:
            for member in MINIS[:2]:
                Path(base, member).mkdir()
                Path(base, member, oneshot_report.SCORECARD).write_text(json.dumps({"sides": sides}))
            group.summarize(base, "oneshot-minis", {MINIS[0]: 0, MINIS[1]: 0})
            text = Path(base, group.SUMMARY).read_text()
            saved = json.loads(Path(base, group.RESULTS).read_text())
        self.assertEqual(saved["cost_basis"]["basis"], "list")
        self.assertEqual(len(basis_lines(text)), 1)
        self.assertNotIn("Cost, list USD", text)


@unittest.skipIf(scorecard is None, "NumPy is installed in CI and bundled with Blender")
class ScenarioTests(unittest.TestCase):
    def card(self, codex_mcp):
        turns = [turn["id"] for turn in specs.module("bridge").TURNS]
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.dict(os.environ, {specs.REFERENCES_ENV: str(Path(temporary, "references"))}):
            records = []
            for side in ("mcp", "angelo"):
                rows = [{"task": turn, "status": "completed", "host_check_passed": True, "wall_ms": 20000.0,
                         "total_ms": 20000.0, "api_calls": 4, "tool_call_count": 3, "tool_errors": 0,
                         "provider_usage": dict({"input_tokens": 1000, "output_tokens": 200},
                                                **({} if codex_mcp and side == "mcp" else {"cost_usd": 0.03}))}
                        for turn in turns]
                folder = Path(temporary, "run", "%s-trial-01" % side)
                folder.mkdir(parents=True)
                name = "trial.json" if side == "mcp" else "eval-live-agent.json"
                (folder / name).write_text(json.dumps({"runtime": "codex" if codex_mcp else "claude", "tasks": rows,
                                                       "scenario": {}}))
                records += [dict(copy.deepcopy(row), side=side, trial=1, list_price_usd=0.02) for row in rows]
            return scorecard.collect(Path(temporary, "run"), "scenario-bridge", {"records": records}), len(turns)

    def test_both_sides_cost_on_one_basis(self):
        for codex, basis, each in ((True, "list", 0.02), (False, "reported", 0.03)):
            with self.subTest(codex=codex):
                card, turns = self.card(codex)
                self.assertEqual(card["cost_basis"]["basis"], basis)
                for side in card["trials"]:
                    self.assertAlmostEqual(side["metrics"]["cost_compared_usd"], each * turns, places=4)
                text = scenario_report.markdown(card, {}, {})
                self.assertEqual(len(basis_lines(text)), 1, basis_lines(text))
                self.assertNotIn("| Cost estimate, USD |", text)


if __name__ == "__main__":
    unittest.main()
