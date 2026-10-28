# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The one-shot report and verdict inputs (no Blender, process or account).

The report is built from a scorecard: these tests give it one with the
shapes ``oneshot_report.collect`` writes, using the recorded-receipt fixtures
of ``test_native_oneshot_metrics``.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import oneshot_metrics as metrics  # noqa: E402
import oneshot_report as report  # noqa: E402
import scenario_oneshot_footbridge as footbridge  # noqa: E402
from test_native_oneshot_metrics import angelo_record, claude_lines, mcp_record  # noqa: E402


class ReportTests(unittest.TestCase):
    def sides(self):
        angelo = metrics.angelo_path(angelo_record(), "claude-opus-5-5")
        other = metrics.mcp_path(mcp_record(), claude_lines(), {"messages": 1}, "claude-opus-5-5", "footbridge-final.png")
        return angelo, other

    def result(self):
        angelo, other = self.sides()
        check = {"kind": "pixels", "label": "harness still: not black", "passed": True, "details": {}}
        failed = [{"label": "handrail top 1 m above the deck", "details": {"max": [3.0, 0.7, 0.9]}}]
        return {"test": "oneshot-footbridge", "title": footbridge.TITLE, "finished_at": "2026-10-11T06:00:00+0900",
                "dry_run": False, "model": "claude-opus-5-5", "runtime": "claude", "effort": "low",
                "limits": dict(footbridge.LIMITS), "final_still": footbridge.FINAL_STILL,
                "prompt": footbridge.PROMPT, "prompt_sha256": "abc",
                "references": [{"view": "hero", "file": "hero.jpg", "sha256": "f00", "bytes": 1234, "source": {
                    "kind": "render", "ground_truth": "scenario_oneshot_footbridge.py", "camera": "hero",
                    "blender": "Blender 5.2.0"}}], "unknown": metrics.UNKNOWN,
                "sides": [
                    {"side": "mcp", "trial": 1, "runtime": "claude", "label": "Blender MCP (Claude Code) 1",
                     "folder": "/nonexistent", "verdict": "failed", "reasons": ["check failed: handrail top"],
                     "checks": {"passed": 25, "total": 26, "failed": failed}, "pixels": [check], "path": other,
                     "agent_still": "/x/agent-final.png", "final_folder": None,
                     "allowances": ["every tool of the Blender MCP server"]},
                    {"side": "angelo", "trial": 1, "runtime": "claude", "label": "Angelo 1", "folder": "/nonexistent",
                     "verdict": "passed", "reasons": [], "checks": {"passed": 26, "total": 26, "failed": []},
                     "pixels": [check], "path": angelo, "agent_still": "/x/agent-final.png", "final_folder": None}]}

    def test_report_states_the_verdict_the_table_the_brief_and_the_sources(self):
        text = report.markdown(self.result())
        for expected in ("# One-shot test: Timber footbridge over a stream",
                         "| Side | Verdict | Closed checks passed / failed / inconclusive | Pixel checks "
                         "| Stop reason |",
                         "| Blender MCP (Claude Code) 1 | FAILED | 25 / 1 / 0 of 26 | 1 / 1 | finished |",
                         "| Angelo 1 | PASSED | 26 / 0 / 0 of 26 | 1 / 1 | finished |",
                         "Verdicts: Blender MCP (Claude Code) passed 0, failed 1, inconclusive 0 of 1 trials; "
                         "Angelo passed 1, failed 0, inconclusive 0 of 1 trials.",
                         "| Closed checks passed / failed / inconclusive | 25 / 1 / 0 of 26 | 26 / 0 / 0 of 26 |",
                         "No score judged by a model is counted.",
                         "| Path metric | Blender MCP (Claude Code) 1 | Angelo 1 |",
                         "Angelo 1. Intended path: one message: yes",
                         "- **Blender MCP (Claude Code) 1**: failed: check failed: handrail top",
                         "  - handrail top 1 m above the deck:", "- **Angelo 1**: passed.",
                         "> A timber footbridge over a stream",
                         "rendered from the ground truth in scenario_oneshot_footbridge.py, camera hero",
                         "## What the other side's client was allowed", "- every tool of the Blender MCP server",
                         "## Not recorded", "## Opinion (never counted)"):
            self.assertIn(expected, text)
        self.assertIn("2400 s for the turn, 900 s more for a render job it started, 25 USD at list prices", text)

    def test_a_side_that_sent_no_message_is_reported_as_failed(self):
        # side_result's shape for a trial that never started (a Windows dry run's Codex side): the report
        # used to stop at its missing final_folder, hiding that side's own error.
        result = self.result()
        result["sides"].append({"side": "mcp", "trial": 2, "runtime": "codex", "folder": "/nonexistent",
                                "side_label": "Blender MCP (Codex)", "label": "Blender MCP (Codex) 2",
                                "verdict": "failed", "reasons": ["no message was sent: the trial did not start"],
                                "checks": None, "pixels": [], "path": None})
        with tempfile.TemporaryDirectory() as folder:
            report.write(Path(folder), result)
            text = (Path(folder) / report.REPORT).read_text()
            self.assertIn("no message was sent: the trial did not start", text)
            self.assertTrue((Path(folder) / report.PAGE).is_file())

    def test_report_states_scripts_planned_n_and_each_trials_brief(self):
        result = self.result()
        result["conditions"] = {"scripts": "scripts allowed (...)", "planned_n": {"angelo": 1, "mcp": 1},
                                "recorded_n": {"angelo": {"valid": 1}, "mcp": {"valid": 1}}, "n_differs": []}
        result["sides"][0]["prompt_sha256"], result["sides"][1]["prompt_sha256"] = "abc", None
        text = report.markdown(result)
        self.assertIn("Scripts: scripts allowed (...).", text)
        self.assertIn("Planned n per side: angelo 1, mcp 1; valid trials: angelo 1, mcp 1.", text)
        self.assertIn("**These trials recorded another or no SHA-256: Angelo 1 (not recorded).**", text)
        self.assertNotIn("unchanged", self.brief(text))   # Never beside a flag (break-it E2E-18 L-4).
        result["sides"][1]["prompt_sha256"] = "abc"
        self.assertIn("Sent once to each side, unchanged (SHA-256 abc).", report.markdown(result))
        # The parallel arm's message is the brief and its request: quoted, and checked against that text (E2E-16 H-2).
        import hashlib
        import eval_parallel
        request = eval_parallel.PARALLEL_REQUEST
        sent = hashlib.sha256(eval_parallel.with_request(result["prompt"], request).encode()).hexdigest()
        result["prompt_sha256"] = hashlib.sha256(result["prompt"].encode()).hexdigest()
        for side in result["sides"]:
            side.update(prompt_sha256=sent, parallel_request=request)
        text = report.markdown(result)
        self.assertNotIn("These trials recorded another", text)
        self.assertNotIn("different messages", text)
        self.assertIn("the parallel arm's message adds the request below as its own paragraph (SHA-256 of the text "
                      "sent %s)" % sent, text)
        self.assertIn("> " + request, text)
        result["sides"][1]["prompt_sha256"] = result["prompt_sha256"]   # Recorded as the brief alone: flagged.
        text = report.markdown(result)
        self.assertIn("**These trials recorded another or no SHA-256: Angelo 1", text)
        self.assertNotIn("unchanged", self.brief(text))
        result["sides"][1].update(prompt_sha256=result["prompt_sha256"], parallel_request=None)   # One side without it.
        text = report.markdown(result)
        self.assertIn("**The sides were sent different messages: Angelo 1 sent without the parallel arm's request.**",
                      text)
        self.assertNotIn("unchanged", self.brief(text))
        # A request other than the frozen sentence, its SHA consistent with itself: Amendment A's sentence, now
        # replaced by Amendment D's, is one too.
        for changed in ("Split the work.", "Split this work across workers: give each worker its own pieces to build, "
                                           "and hand out more as they finish."):
            result["sides"][0].update(parallel_request=changed, prompt_sha256=hashlib.sha256(
                eval_parallel.with_request(result["prompt"], changed).encode()).hexdigest())
            text = report.markdown(result)
            self.assertIn("Blender MCP (Claude Code) 1 recorded a request other than the frozen sentence", text)
            self.assertNotIn("unchanged", self.brief(text))

    @staticmethod
    def brief(text):
        """The report's brief section, up to the quoted brief."""
        return text.split("## The brief", 1)[1].split("\n> ", 1)[0]

    def test_an_inconclusive_side_is_its_own_verdict_in_every_table_and_never_a_pass(self):
        result = self.result()
        undecided = {"label": "roof colour", "details": {"reason": "inconclusive: an image texture"},
                     "inconclusive": True}
        result["sides"][1].update(verdict="inconclusive", reasons=["check inconclusive: roof colour"],
                                  checks={"passed": 25, "total": 26, "failed": [undecided]})
        text = report.markdown(result)
        for expected in ("| Angelo 1 | INCONCLUSIVE | 25 / 0 / 1 of 26 | 1 / 1 | finished |",
                         "Angelo passed 0, failed 0, inconclusive 1 of 1 trials.",
                         "| Verdict (closed checks only) | failed | inconclusive |",
                         "| Closed checks passed / failed / inconclusive | 25 / 1 / 0 of 26 | 25 / 0 / 1 of 26 |",
                         "checks passed: 25 of 26 (1 inconclusive).",
                         "- **Angelo 1**: inconclusive: check inconclusive: roof colour",
                         "  - roof colour (inconclusive): "):
            self.assertIn(expected, text)
        self.assertNotIn("- **Angelo 1**: passed.", text)
        self.assertEqual(report.verdict_of(["check inconclusive: roof colour"]), "inconclusive")
        self.assertEqual(report.verdict_of(["check inconclusive: roof colour", "check failed: rails"]), "failed")
        self.assertEqual(report.verdict_of(["check inconclusive: roof", "stopped: time_cap"]), "failed")

    def test_dry_run_checks_read_the_scorecard(self):
        result = self.result()
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, report.SCORECARD).write_text(json.dumps(result))
            checks = report.dry_run_checks(folder)
        self.assertFalse(checks["mcp-01: passed (closed checks, the saved still, pixel checks)"])
        self.assertTrue(checks["mcp-01: exactly one message was typed"])
        self.assertTrue(checks["mcp-01: the client could read the image it rendered"])
        self.assertTrue(checks["angelo-01: approvals counted and timed"])
        self.assertTrue(checks["angelo-01: looked at an image of its own result"])
        self.assertTrue(checks["angelo-01: the final still was rendered and found"])
        # The fixture's sides carry no record of the files they wrote: the dry run would not let that pass.
        self.assertFalse(checks["angelo-01: the image files it wrote were recorded"])
        self.assertEqual(len(checks), 14)
        still = {"file": "footbridge-final.png", "size": [2560, 1440], "bytes": 9, "where": "own folder"}
        result["sides"][1]["produced"] = [still]
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, report.SCORECARD).write_text(json.dumps(result))
            self.assertTrue(report.dry_run_checks(folder)["angelo-01: the image files it wrote were recorded"])

    def test_pixel_checks_of_both_stills(self):
        flat = [0.5] * 576
        scene = [0.2 + 0.001 * index for index in range(576)]
        final = {"baselines": {report.OWN_CAMERA: {"thumbnail": flat}, report.BRIEF_CAMERA: {"thumbnail": flat}},
                 "harness": {"pixels": 100, "mean": 0.4, "spread": 0.2, "dark_share": 0.1, "thumbnail": scene},
                 "agent": {"pixels": 100, "mean": 0.4, "spread": 0.2, "dark_share": 0.1, "thumbnail": flat}}
        checks = report.pixels(final)
        self.assertEqual([entry["passed"] for entry in checks], [True, True, True, True, True, False])
        self.assertEqual(checks[-1]["label"], "agent's own still: not the default scene")
        missing = report.pixels({"error": "the final-still job failed"})
        self.assertEqual([entry["passed"] for entry in missing], [False, False, False])   # Nothing is assumed.

if __name__ == "__main__":
    unittest.main()


class InconclusiveVerdictTests(unittest.TestCase):
    """A check the grader could not decide is reported apart: never a pass, and not a measured fail."""

    def test_a_side_is_inconclusive_only_when_every_reason_is_an_undecided_check(self):
        self.assertEqual(report.verdict_of([]), "passed")
        self.assertEqual(report.verdict_of(["check inconclusive: green leaves"]), "inconclusive")
        self.assertEqual(report.verdict_of(["check inconclusive: green leaves", "check failed: grass"]), "failed")
        self.assertEqual(report.verdict_of(["check inconclusive: green leaves", "stopped: provider_limit"]), "failed")

    def test_evaluate_marks_an_undecided_check(self):
        import scenario_checks
        fake = {"undecided": lambda *args: (False, {"inconclusive": True}),
                "unread": lambda *args: (False, {"reason": "inconclusive: more than 8000000 vertices to count"}),
                "failed": lambda *args: (False, {"problems": ["x"]})}
        original = dict(scenario_checks.CHECKS)
        try:
            scenario_checks.CHECKS.update(fake)
            bpy = type("Bpy", (), {"context": type("Context", (), {"evaluated_depsgraph_get": lambda: None})})
            result = scenario_checks.evaluate(bpy, None, [{"kind": kind} for kind in fake])
        finally:
            scenario_checks.CHECKS.clear()
            scenario_checks.CHECKS.update(original)
        self.assertEqual([entry.get("inconclusive", False) for entry in result["results"]], [True, True, False])
        self.assertFalse(result["success"])

