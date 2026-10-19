# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The scenario scorecard and its Markdown and HTML report, from synthetic run folders (no Blender or account).

``scenario_scorecard.collect`` reads a finished run's raw trial files; with no
saved scene it skips capture and scoring, so these tests never start Blender.
``scenario_report.write`` must build both files from the scorecard alone.
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

import scenario_report as report  # noqa: E402
import scenario_specs as specs  # noqa: E402

try:
    import scenario_scorecard as scorecard  # noqa: E402  (imports NumPy through scenario_scoring)
except ImportError:  # CI installs NumPy (run_host_tests --max-skips 0); Blender bundles it.
    scorecard = None

TURNS = [turn["id"] for turn in specs.module("bridge").TURNS]
UNDO = {"undo_steps": 9, "stopped": "reached the start", "turn_boundaries_reachable": 9, "turns": 9,
        "first_undo_returns_previous_turn": True}
HYGIENE = {"issues": 1, "new_orphan_count": 3, "unexpected_objects": ["Cube.001"]}


def failed(label, **details):
    return {"results": [{"label": label, "passed": False, "details": details}, {"label": "ok", "passed": True}]}


def write_mcp_trial(output):
    folder = output / "mcp-trial-01"
    folder.mkdir(parents=True)
    rows = [{"task": turn, "status": "completed", "host_check_passed": turn != "lamps", "wall_ms": 20000.0,
             "api_calls": 4, "tool_call_count": 3, "tool_names": ["mcp__blender__execute_blender_code"] * 2,
             "tool_errors": 1 if turn == "bridge" else 0, "references": [{"view": "front", "sha256": "x"}],
             "host_check": failed("lamp post x=0") if turn == "lamps" else
             {"error": "Add-on request failed: ModuleNotFoundError"} if turn == "valley" else {"results": []},
             "provider_usage": {"input_tokens": 1000, "output_tokens": 200, "cost_usd": 0.01}} for turn in TURNS]
    (folder / "trial.json").write_text(json.dumps({"runtime": "claude", "tasks": rows,
                                                   "scenario": {"undo": dict(UNDO, undo_steps=0), "hygiene": HYGIENE}}))


def write_angelo_trial(output):
    folder = output / "angelo-trial-01"
    (folder / "trial-01" / "qa").mkdir(parents=True)
    rows = [{"task": turn, "status": "timeout" if turn == "render_setup" else "completed",
             "host_check_passed": turn != "render_setup", "total_ms": 15000.0, "model_rounds": 2,
             "provider_turn_requests": 1, "python_fallbacks": 1 if turn == "valley" else 0,
             "approved": ["angelo_scene_program"] + (["angelo_execute_python"] if turn == "valley" else []),
             "declined": ["angelo_write_file"] if turn == "bridge" else [],
             "host_check": failed("1080p EEVEE setup", values={"render.fps": 30}) if turn == "render_setup" else {},
             "receipt": {"counts": {"failure_stale": 1}} if turn == "lamps" else {}} for turn in TURNS]
    trial = {"tasks": rows, "scenario": {"undo": UNDO, "hygiene": dict(HYGIENE, issues=0)}}
    (folder / "eval-live-agent.json").write_text(json.dumps({"trials": [trial]}))


@unittest.skipIf(scorecard is None, "NumPy is installed in CI and bundled with Blender")
class ScorecardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.output = Path(self.temporary.name, "run")
        references = Path(self.temporary.name, "references")
        patcher = mock.patch.dict(os.environ, {specs.REFERENCES_ENV: str(references)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.temporary.cleanup)

    def test_per_side_metrics_from_raw_trials(self):
        write_mcp_trial(self.output)
        write_angelo_trial(self.output)
        card = scorecard.collect(self.output, "scenario-bridge")
        self.assertTrue((self.output / "scenario-scorecard.json").is_file())
        mcp, angelo = card["trials"]
        self.assertEqual((mcp["side"], angelo["side"]), ("mcp", "angelo"))
        self.assertEqual((mcp["metrics"]["turns_passed"], mcp["metrics"]["turns_total"]), (8, 9))
        self.assertEqual(mcp["metrics"]["python_executions"], 18)
        self.assertEqual(mcp["metrics"]["errors"]["tool_errors"], 1)
        self.assertEqual(mcp["metrics"]["cost_usd"], 0.09)
        self.assertEqual(mcp["metrics"]["wall_s"], 180.0)
        self.assertEqual(angelo["metrics"]["turns_timed_out"], 1)
        self.assertEqual(angelo["metrics"]["python_executions"], 1)
        self.assertEqual(angelo["metrics"]["reviewed_python_requests"], 1)
        self.assertEqual(angelo["metrics"]["approvals"]["declined_tools"], ["angelo_write_file"])
        self.assertEqual(angelo["metrics"]["errors"]["receipt_failures"], {"failure_stale": 1})
        self.assertEqual((mcp["undo"]["undo_steps"], angelo["undo"]["undo_steps"]), (0, 9))
        self.assertIsNone(mcp["scores"])   # No saved scene: nothing to capture.
        self.assertEqual(mcp["turns"][3]["failed_checks"][0]["label"], "lamp post x=0")
        self.assertEqual(mcp["turns"][0]["failed_checks"], [{"label": "grading error",
                                                             "details": "Add-on request failed: ModuleNotFoundError"}])

    def test_saved_scenes_are_captured_with_the_runs_own_stock_blender(self):
        # Regression: a Windows run's scorecard captured the saved scenes with the macOS default Blender path.
        write_mcp_trial(self.output)
        (self.output / "mcp-trial-01" / "final.blend").write_bytes(b"BLENDER")
        with mock.patch.object(scorecard.references, "run_job", return_value=1) as job:
            card = scorecard.collect(self.output, "scenario-bridge", blender="C:/bench/stock/blender.exe")
        self.assertEqual(job.call_args.args[0], "C:/bench/stock/blender.exe")
        self.assertIn("capture failed", card["trials"][0]["scores"]["error"])

    def test_report_files_from_the_scorecard_alone(self):
        write_mcp_trial(self.output)
        write_angelo_trial(self.output)
        card = scorecard.collect(self.output, "scenario-bridge")
        report.write(self.output, card, {"finished_at": "2026-10-10T12:00:00+0900",
                                         "plan": {"model": "claude-opus-5-5", "runtime": "claude", "effort": "low"}})
        text = (self.output / "SCENARIO-REPORT.md").read_text()
        for label, _ in report.METRIC_ROWS:
            self.assertIn("| %s |" % label, text)
        self.assertIn("| Metric | Blender MCP (Claude Code) 1 | Angelo 1 |", text)
        # Claude Code's estimate on one side only: both sides on list prices, none recorded here.
        self.assertIn("| Cost, USD (basis below) | – | – |", text)
        self.assertIn("| lamps | FAIL completed, 20 s | pass completed, 15 s |", text)
        self.assertIn("| render_setup | pass completed, 20 s | FAIL timeout, 15 s |", text)
        self.assertIn("`render_setup` / 1080p EEVEE setup", text)
        self.assertIn("model claude-opus-5-5, runtime claude, effort low", text)
        page = (self.output / "scenario-report.html").read_text()
        self.assertTrue(page.startswith("<!doctype html>"))
        self.assertIn("grid-template-columns:repeat(var(--n),minmax(0,1fr))", page)   # CSS survives intact.
        self.assertIn("&lt;", report.html_page(dict(card, plan=dict(card["plan"], title="<b>")), {}, {}, "x"))

class ReportPieceTests(unittest.TestCase):
    def test_struggles_list_failed_checks_and_timeouts(self):
        card = {"trials": [{"side": "angelo", "trial": 1, "turns": [
            {"turn": "lamps", "status": "timeout", "failed_checks": [{"label": "glow", "details": {"emission": 0}}]},
            {"turn": "valley", "status": "completed", "failed_checks": []}]},
            {"side": "mcp", "trial": 1, "turns": [{"turn": "valley", "status": "completed", "failed_checks": []}]}]}
        lines = report.struggles(card)
        self.assertEqual(lines[0], "- **Angelo trial 1**: 1 failed check(s), 0 inconclusive, timed out on lamps.")
        self.assertIn('`lamps` / glow: {"emission": 0}', lines[1])
        self.assertEqual(lines[2], "- **Blender MCP trial 1**: every turn passed its checks.")
        self.assertEqual(report.side_label({"side": "mcp", "runtime": "codex"}), "Blender MCP (Codex)")

    def test_errors_cell_lists_only_what_happened(self):
        self.assertEqual(report._errors({"tool_errors": 0, "api_retries": 2, "trial_error": False}), "api retries 2")
        self.assertEqual(report._errors({"tool_errors": None, "aborted": None}), "none")


if __name__ == "__main__":
    unittest.main()
