# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Comparability of benchmark trials (``bench_integrity``) and the symmetric throttle rule.

What each trial records it was sent and graded with, when earlier MCP trials may be reused, the scripts label
every report states, the planned n a run and a campaign fix in advance, and the throttle evidence both sides
share. Unbilled fixtures with the drivers' shapes; Claude Code's ``api_retry`` line is the 2.1.301 stream-json
shape. No process, account or model is involved.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import bench_integrity as integrity  # noqa: E402
import bench_mcp_report as report  # noqa: E402
import bench_mcp_vs_angelo as bench  # noqa: E402
import eval_live_tasks as live  # noqa: E402
import eval_provider_limits as limits  # noqa: E402
import eval_receipts  # noqa: E402
import final_runs  # noqa: E402
import final_runs_plan as plan  # noqa: E402
import final_runs_report  # noqa: E402
from angelo_agent import codex_retry  # noqa: E402
from angelo_agent.claude_stream import RoundStream, retry_kind  # noqa: E402
from angelo_agent.claude_transport import ClaudeTurn  # noqa: E402

CONNECTION = "22222222-2222-4222-8222-222222222222"
THROTTLE = {"type": "system", "subtype": "api_retry", "attempt": 1, "max_retries": 10, "retry_delay_ms": 5000,
            "error_status": 429, "error": "rate_limit"}
OVERLOADED = dict(THROTTLE, error_status=529, error="overloaded")


class StampTests(unittest.TestCase):
    """I7: every task record of either side keeps its prompt's SHA-256 and the test code's version."""

    def test_the_test_code_version_covers_the_harness_not_its_tests(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / "tests/python/angelo_agent"
            folder.mkdir(parents=True)
            (folder / "eval_live_tasks.py").write_text("TASKS = 1\n")
            (folder / "test_native_x.py").write_text("x = 1\n")
            first = integrity.test_code(root)
            (folder / "test_native_x.py").write_text("x = 2\n")
            integrity.test_code.cache_clear()
            self.assertEqual(integrity.test_code(root)["sha256"], first["sha256"], "a test file is not test code")
            (folder / "eval_live_tasks.py").write_text("TASKS = 2\n")
            integrity.test_code.cache_clear()
            self.assertNotEqual(integrity.test_code(root)["sha256"], first["sha256"])
            self.assertIsNone(first["commit"] if not (Path(root) / ".git").exists() else None)
        integrity.test_code.cache_clear()

    def test_angelo_task_records_carry_the_stamp_into_the_bench(self):
        task = {"id": "t", "prompt": "Make a cube"}
        trial = SimpleNamespace(index=0, report={}, tasks=[task], warm_ups={"marker": None},
                                controller=SimpleNamespace(telemetry=SimpleNamespace(receipts=[])))
        extras = eval_receipts.task_extras(trial)
        self.assertEqual(extras["prompt_sha256"], integrity.prompt_sha256("Make a cube"))
        self.assertEqual(set(extras["test_code"]), {"commit", "dirty", "sha256"})
        row = dict(extras, task="t", kind="edit", status="completed")
        record = report.angelo_records({"tasks": [row]}, 1, {})[0]
        self.assertEqual((record["prompt_sha256"], record["test_code"]), (extras["prompt_sha256"], extras["test_code"]))

    def test_both_mcp_runners_stamp_each_task_and_trial(self):
        for name in ("bench_mcp_trial.py", "bench_mcp_codex.py"):
            source = (HERE / name).read_text()
            run_task = source[source.index("def run_task"):source.index("def run_task") + 400]
            with self.subTest(runner=name):
                self.assertIn("**bench_integrity.trial_stamp(task)", run_task, "in the task's first record fields")
                self.assertIn('"test_code": bench_integrity.test_code()', source[source.index("def run_trial"):])


class ReuseTests(unittest.TestCase):
    """I5: earlier MCP trials are reused only when they were sent and graded exactly as this run's trials."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.args = bench.parse(["--sides", "angelo", "--connection-id", CONNECTION])
        self.tasks = bench.tasks(self.args)
        self.code = {"commit": "abc", "dirty": False, "sha256": "f" * 64}

    def reused(self, **changes):
        folder = Path(self.folder.name) / ("reused-%d" % len(list(Path(self.folder.name).iterdir())))
        (folder / "mcp-trial-01").mkdir(parents=True)
        run_plan = dict(bench.plan(self.args), **changes.pop("plan", {}))
        (folder / "RUN-PLAN.json").write_text(json.dumps(run_plan))
        records = [{"task": task["id"], "prompt_sha256": integrity.prompt_sha256(task["prompt"])}
                   for task in self.tasks if task["kind"] != "preview"]
        for record in records:
            record.update(changes.get("record", {}))
        trial = {"test_code": changes.get("code", self.code), "tasks": records}
        (folder / "mcp-trial-01" / "trial.json").write_text(json.dumps(trial))
        return folder

    def problems(self, folder):
        return integrity.reuse_problems(folder, bench.plan(self.args), self.tasks, self.code)

    def test_identical_trials_may_be_reused_and_another_model_only_in_the_tier_mode(self):
        self.assertEqual(self.problems(self.reused()), [])
        other = self.reused(plan={"model": "claude-sonnet-5-5", "effort": "medium"})
        self.assertEqual(len(self.problems(other)), 2, "another model and effort are refused by default")
        self.assertEqual(integrity.reuse_problems(other, bench.plan(self.args), self.tasks, self.code,
                                                  cross_model=True), [],
                         "a tier measurement against recorded MCP results (model_routing.rst)")

    def test_a_different_prompt_task_set_reference_or_test_code_is_refused(self):
        cases = {"another prompt": (dict(record={"prompt_sha256": "0" * 64}), "was sent another prompt"),
                 "an unstamped trial": (dict(record={"prompt_sha256": None}), "before trials kept their prompt"),
                 "other test code": (dict(code=dict(self.code, sha256="e" * 64, commit="old")), "ran other test code"),
                 "an older trial": (dict(code={}), "before trials kept their test code"),
                 "another task set": (dict(plan={"task_set": "long"}), "its task_set was 'long'"),
                 "other references": (dict(plan={"scenario": {"reference_digest": "abcd"}}),
                                      "its reference digest was 'abcd'")}
        for name, (changes, expected) in cases.items():
            with self.subTest(case=name):
                self.assertTrue(any(expected in problem for problem in self.problems(self.reused(**changes))),
                                self.problems(self.reused(**changes)))
        self.assertIn("no readable RUN-PLAN.json", self.problems(Path(self.folder.name) / "missing")[0])

    def test_the_bench_refuses_before_any_trial(self):
        folder = self.reused(record={"prompt_sha256": "0" * 64})
        args = bench.parse(["--sides", "angelo", "--connection-id", CONNECTION, "--authorize-billed-runs",
                            "--reuse-mcp-results", str(folder), "--output", str(Path(self.folder.name) / "new")])
        problems, _output = bench.preflight(args)
        self.assertTrue(any(problem.startswith("--reuse-mcp-results: mcp-trial-01 task") for problem in problems),
                        problems)


class ScriptsAndPlannedNTests(unittest.TestCase):
    """I1 and I10: head-to-heads run with Always allow scripts and say so; n is fixed in advance and checked."""

    def test_every_head_to_head_step_uses_the_product_setting_and_gates_none(self):
        for step in plan.campaign("macos"):
            with self.subTest(step=step["id"]):
                self.assertNotIn("--angelo-trusted-scripts", step["args"])
                if step["phase"] == "h2h" and step["tool"] == "bench":
                    self.assertIn("--angelo-always-allow-scripts", step["args"])
                if step["phase"] == "gate":
                    self.assertNotIn("--angelo-always-allow-scripts", step["args"])

    def test_the_scripts_label(self):
        self.assertTrue(integrity.scripts_label({"always_allow_scripts": True}).startswith("scripts allowed"))
        self.assertTrue(integrity.scripts_label({"trusted_scripts": True}).startswith("scripts allowed"))
        self.assertIn("Auto", integrity.scripts_label({"approval": "default"}))
        self.assertIn("Ask", integrity.scripts_label({"approval": "ask"}))

    def test_a_run_states_its_scripts_and_flags_a_side_short_of_its_planned_n(self):
        args = bench.parse(["--angelo-always-allow-scripts", "--trials", "3", "--connection-id", CONNECTION])
        records = ([{"side": "mcp", "trial": trial, "task": "a"} for trial in (1, 2, 3)]
                   + [{"side": "angelo", "trial": trial, "task": "a"} for trial in (1, 2)]
                   + [{"side": "angelo", "trial": 3, "task": "a", "invalid": "rate_limited"}])
        found = integrity.conditions(bench.plan(args), records)
        self.assertEqual((found["planned_n"], found["script_mode"]), ({"mcp": 3, "angelo": 3}, "allowed"))
        self.assertEqual(found["n_differs"], ["angelo: 2 valid trial(s), 3 planned (1 invalid, rate-limited)"])
        text = report.markdown({"summary": report.summarize([]), "meta": {"conditions": found}, "records": []})
        self.assertIn("Scripts: scripts allowed", text)
        self.assertIn("**n differs from the plan:** angelo: 2 valid trial(s), 3 planned", text)

    def test_a_campaign_records_its_planned_n_at_init_and_its_report_flags_any_arm_that_differs(self):
        steps = [step for step in plan.campaign("macos") if step["id"] in ("h2h-opus-default", "gate-opus-default")]
        config = {"planned_n": final_runs.planned_n(plan.campaign("macos"))}
        self.assertEqual(config["planned_n"]["h2h-opus-default"], {"mcp": 5, "angelo": 5})
        records = [{"side": side, "trial": trial, "task": "a", "status": "completed", "wall_ms": 1000.0}
                   for side in ("mcp", "angelo") for trial in range(1, 6 if side == "mcp" else 5)]
        meta = {"plan": {"angelo": {"always_allow_scripts": True, "approval": "default"}}}
        with tempfile.TemporaryDirectory() as campaign:
            (Path(campaign) / "h2h-opus-default").mkdir()
            (Path(campaign) / "h2h-opus-default" / "bench-mcp-vs-angelo.json").write_text(json.dumps(
                {"meta": meta, "records": records, "summary": {"cold_warm": {"trials": []}}}))
            result = final_runs_report.report(config, steps, campaign)
            older = final_runs_report.report({}, steps, campaign)   # A campaign initialised before planned n.
        self.assertIn("h2h-opus-default angelo: 4 valid trial(s), 5 planned", result["n_differs"])
        self.assertIn("gate-opus-default: not run (planned angelo 5)", result["n_differs"])
        row = next(row for row in result["rows"] if row["step"] == "h2h-opus-default" and row["side"] == "angelo")
        self.assertEqual((row["planned_n"], row["n"]), (5, 4))
        self.assertTrue(row["scripts"].startswith("scripts allowed"))
        self.assertIn("h2h-opus-default: no planned n was recorded at init", older["n_differs"])


class ThrottleRuleTests(unittest.TestCase):
    """N5/I8: a throttle a runtime waited out invalidates the trial on either side, by the same rule."""

    def test_the_mcp_side_counts_claude_codes_own_throttled_retries(self):
        self.assertIn("HTTP 429", limits.mcp_limit([(1.0, THROTTLE)], {"type": "result", "subtype": "success"}, {}))
        self.assertIn("HTTP 429", limits.mcp_limit([dict(THROTTLE, error_status=None)], None, None))
        self.assertIsNone(limits.mcp_limit([OVERLOADED], {"type": "result", "subtype": "success"}, {}),
                          "a busy provider's retry is not a limit, on either side")

    def test_angelos_receipt_counts_the_same_retries(self):
        self.assertEqual((retry_kind(THROTTLE), retry_kind(OVERLOADED), retry_kind({"type": "system"})),
                         ("rate", "busy", None))
        seen = []
        RoundStream(on_retry=seen.append).events(THROTTLE, {})
        self.assertEqual(seen, ["rate"])
        # The real constructor, as the provider builds each turn (an end-to-end check).
        from angelo_agent.claude_transport import ClaudeRequest
        from angelo_agent.request_fences import RequestFence
        from angelo_agent.telemetry import RequestMetrics
        host = SimpleNamespace(_epoch=1, _request_generation=1, _closing=False)
        host._request_metrics = metrics = RequestMetrics("claude")
        metrics.counts = {}
        context = SimpleNamespace(host=host, request_fence=RequestFence(1, 1), cancelled=False,
                                  record_running_usage=None)
        turn = ClaudeTurn(ClaudeRequest(context))
        for event in (THROTTLE, OVERLOADED, {"type": "system", "subtype": "init"}):
            turn.stream.events(event, {})
        self.assertEqual(metrics.counts, {"provider_retries_rate": 1, "provider_retries_busy": 1})
        self.assertIn("throttled", limits.angelo_limit([], {"input_tokens": 9}, {"provider_retries_rate": 1}))

    def test_codex_own_throttled_retries_count_on_both_sides(self):
        error = {"message": "Reconnecting... 1/5", "codexErrorInfo": {"responseStreamDisconnected": {"httpStatusCode": 429}}}
        metrics, host = mock.Mock(), SimpleNamespace()
        host._request_metrics = metrics
        context = SimpleNamespace(host=host, request_fence=SimpleNamespace(matches=lambda _host: True),
                                  cancelled=False, voice=True)
        provider = SimpleNamespace(_codex_retried=False, _retrying_shown=False)
        codex_retry.note(provider, context, "error", {"error": error, "willRetry": True})
        metrics.count.assert_called_once_with("provider_retries_rate")
        busy = {"message": "overloaded", "codexErrorInfo": {"responseStreamDisconnected": {"httpStatusCode": 503}}}
        codex_retry.note(provider, context, "error", {"error": busy, "willRetry": True})
        metrics.count.assert_called_once()
        self.assertIn("retried by Codex", limits.codex_limit([{"method": "error", "params": {
            "error": error, "willRetry": True}}], {"turn": {"status": "completed"}}))


if __name__ == "__main__":
    unittest.main()
