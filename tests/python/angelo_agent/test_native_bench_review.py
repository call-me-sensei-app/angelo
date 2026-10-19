# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Debug mode and the benchmark fairness rules: one regression test per defect.

Each test is a reproduction turned around: it fails without its fix and passes with it.
Unbilled fixtures with the drivers' shapes; no process, account or model is involved.
"""

import contextlib
import io
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
import bench_mcp_gate  # noqa: E402
import bench_mcp_scenario  # noqa: E402
import bench_mcp_vs_angelo as bench  # noqa: E402
import eval_ask_why as ask_why  # noqa: E402
import eval_diagnostics as diagnostics  # noqa: E402
import eval_live_agent  # noqa: E402
import eval_provider_limits as limits  # noqa: E402
import oneshot_group  # noqa: E402

CONN = "22222222-2222-4222-8222-222222222222"
THROTTLE = {"type": "system", "subtype": "api_retry", "attempt": 1, "max_retries": 10, "retry_delay_ms": 5000,
            "error_status": 429, "error": "rate_limit"}


def quiet(function, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as errors:
        return function(*args, **kwargs), errors.getvalue()


class Folder(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def mcp_run(self, name, plan, tasks, code, sha=None):
        folder = self.root / name
        (folder / "mcp-trial-01").mkdir(parents=True)
        (folder / "RUN-PLAN.json").write_text(json.dumps(plan))
        records = [{"task": task["id"], "prompt_sha256": sha or integrity.prompt_sha256(task["prompt"])}
                   for task in tasks if task["kind"] != "preview"]
        (folder / "mcp-trial-01" / "trial.json").write_text(json.dumps({"test_code": code, "tasks": records}))
        return folder


class StampCarryTests(Folder):
    """B1: a diagnostic run's MCP trials are never reused, and every rebuild or aggregate keeps the stamp."""

    def test_reuse_refuses_a_diagnostic_mcp_run(self):
        args = bench.parse(["--sides", "angelo", "--connection-id", CONN])
        tasks, code = bench.tasks(args), integrity.test_code()
        old = self.mcp_run("diag", bench.plan(bench.parse(["--connection-id", CONN, "--debug-thinking"])), tasks, code)
        problems = integrity.reuse_problems(old, bench.plan(args), tasks, code)
        self.assertTrue(any("diagnostic run" in problem for problem in problems), problems)

    def test_a_plain_re_summary_keeps_the_stamp_in_the_results_json(self):
        (self.root / "RUN-PLAN.json").write_text(json.dumps(bench.plan(bench.parse(
            ["--connection-id", CONN, "--debug-thinking"]))))
        args = bench.parse(["--connection-id", CONN])
        with mock.patch.object(bench, "versions", return_value={}):
            results = bench.finish(args, self.root, [], [])
        self.assertTrue(results["meta"]["plan"]["diagnostics"]["debug_thinking"])
        alone = self.root / "copied"
        alone.mkdir()
        (alone / bench.RESULTS).write_text((self.root / bench.RESULTS).read_text())
        self.assertIn("diagnostic run", diagnostics.claim_refusal(alone), "the JSON alone still refuses")
        self.assertTrue((self.root / bench.SUMMARY).read_text().startswith("> **Diagnostic run"))


class ThrottleResummaryTests(Folder):
    """F1: a re-summary of a trial recorded before both sides counted waited-out 429s applies that rule to neither."""

    def trial(self, stamped):
        output = self.root / ("stamped" if stamped else "older")
        folder = output / "mcp-trial-01"
        folder.mkdir(parents=True)
        usage = {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 0,
                 "cache_creation_input_tokens": 0}
        trial = {"side": "mcp", "trial": 1, "tasks": [{"task": "cube_creation", "kind": "edit", "sent_at_s": 0.0,
                                                       "status": "completed", "provider_usage": usage}]}
        if stamped:
            trial["test_code"] = {"sha256": "f" * 64}
        (folder / "trial.json").write_text(json.dumps(trial))
        result = {"type": "result", "subtype": "success", "is_error": False, "usage": usage, "result": "Done"}
        lines = [{"t_s": 0.5, "task": "cube_creation", "message": THROTTLE},
                 {"t_s": 9.0, "task": "cube_creation", "message": result}]
        (folder / "claude-stream.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines))
        return bench.load_mcp_trial(output, 1)

    def test_only_a_stamped_trial_is_made_invalid_by_a_waited_out_429(self):
        self.assertIsNone(self.trial(stamped=False).get("invalid"), "never only one side's trials")
        self.assertEqual(self.trial(stamped=True).get("invalid"), limits.INVALID)
        self.assertIsNone(limits.mcp_limit([THROTTLE], {"subtype": "success"}, {}, waited_out=False))
        retried = {"method": "error", "params": {"willRetry": True, "error": {"message": "x", "codexErrorInfo": {
            "responseStreamDisconnected": {"httpStatusCode": 429}}}}}
        self.assertIsNone(limits.codex_limit([retried], {"turn": {"status": "completed"}}, waited_out=False))

    def test_the_report_says_which_trials_the_rule_could_not_reach(self):
        found = integrity.conditions(bench.plan(bench.parse(["--connection-id", CONN, "--trials", "1"])),
                                     [{"side": "mcp", "trial": 1}, {"side": "angelo", "trial": 1,
                                                                     "test_code": {"sha256": "a"}}])
        self.assertEqual(found["throttle_rule_not_applied"], ["mcp 1"])
        self.assertIn("applied to neither side of them: mcp 1", "\n".join(integrity.markdown_lines(found)))


class ResummaryTests(Folder):
    """F2 and F7: --summarize-only runs every reuse check, re-plans nothing and drops no trial."""

    def test_a_re_summary_with_reuse_runs_every_reuse_check(self):
        args = bench.parse(["--sides", "angelo", "--connection-id", CONN])
        bad = self.mcp_run("other-prompts", bench.plan(args), bench.tasks(args), integrity.test_code(), sha="0" * 64)
        new = self.root / "angelo-only"
        new.mkdir()
        with mock.patch.object(bench, "gather") as gather, mock.patch.object(bench.gate, "recorded_version",
                                                                             return_value=None):
            code, errors = quiet(bench.main, ["--summarize-only", str(new), "--reuse-mcp-results", str(bad),
                                              "--sides", "angelo", "--connection-id", CONN])
        self.assertEqual(code, 2)
        self.assertIn("was sent another prompt", errors)
        gather.assert_not_called()

    def test_a_re_summary_checks_reuse_against_the_angelo_trials_as_recorded(self):
        args = bench.parse(["--sides", "angelo", "--connection-id", CONN])
        reused = self.mcp_run("mcp", bench.plan(args), bench.tasks(args), {"sha256": "c" * 64})
        new = self.root / "new"
        (new / "angelo-trial-01").mkdir(parents=True)
        tasks = [{"task": task["id"], "prompt_sha256": integrity.prompt_sha256(task["prompt"]),
                  "test_code": {"sha256": "d" * 64}} for task in bench.tasks(args)]
        (new / "angelo-trial-01" / "eval-live-agent.json").write_text(json.dumps({"trials": [{"tasks": tasks}]}))
        problems = integrity.summary_problems(new, bench.plan(args), None, bench.tasks(args), reused)
        self.assertTrue(any("ran other test code" in problem for problem in problems), problems)
        tasks[0]["test_code"] = None
        (new / "angelo-trial-01" / "eval-live-agent.json").write_text(json.dumps({"trials": [{"tasks": tasks}]}))
        self.assertIn("recorded before trials kept their prompt and test code",
                      " ".join(integrity.summary_problems(new, bench.plan(args), None, bench.tasks(args), reused)))

    def test_planned_n_comes_from_the_recorded_plan_and_a_changed_plan_is_refused(self):
        (self.root / "RUN-PLAN.json").write_text(json.dumps(bench.plan(bench.parse(
            ["--connection-id", CONN, "--trials", "5"]))))
        records = [{"side": side, "trial": trial, "task": "a", "test_code": {"sha256": "a"}}
                   for side in ("mcp", "angelo") for trial in (1, 2, 3)]
        args = bench.parse(["--connection-id", CONN, "--trials", "3"])
        with mock.patch.object(bench, "versions", return_value={}):
            found = bench.finish(args, self.root, records, [])["meta"]["conditions"]
        self.assertEqual(found["planned_n"], {"mcp": 5, "angelo": 5})
        self.assertEqual(found["n_differs"], ["angelo: 3 valid trial(s), 5 planned", "mcp: 3 valid trial(s), 5 planned"])
        with mock.patch.object(bench, "gather") as gather, mock.patch.object(bench.gate, "recorded_version",
                                                                             return_value=None):
            code, errors = quiet(bench.main, ["--summarize-only", str(self.root), "--trials", "3", "--connection-id",
                                              CONN])
        self.assertEqual(code, 2)
        self.assertIn("order: recorded", errors)
        gather.assert_not_called()

    def test_no_trial_folder_beyond_the_plan_is_dropped(self):
        plan = bench.plan(bench.parse(["--connection-id", CONN, "--trials", "2"]))
        (self.root / "angelo-trial-04").mkdir()
        self.assertEqual(integrity.trial_count(self.root, plan), 4)


class CrossModelTests(Folder):
    """F3: another runtime is never reused; another model or effort only with the labelled tier mode."""

    def test_runtime_model_and_effort_must_match_unless_the_tier_mode_is_named(self):
        args = bench.parse(["--sides", "angelo", "--connection-id", CONN])
        tasks, code = bench.tasks(args), integrity.test_code()
        other = self.mcp_run("other", dict(bench.plan(args), runtime="codex", model="gpt-6-astra", effort="high"),
                             tasks, code)
        strict = integrity.reuse_problems(other, bench.plan(args), tasks, code)
        self.assertEqual(sum(key in " ".join(strict) for key in ("runtime", "model", "effort")), 3)
        loose = integrity.reuse_problems(other, bench.plan(args), tasks, code, cross_model=True)
        self.assertEqual(loose, ["its runtime was 'codex', this run's is 'claude'"], "never another runtime")
        model = self.mcp_run("model", dict(bench.plan(args), model="claude-sonnet-5-5"), tasks, code)
        self.assertEqual(integrity.reuse_problems(model, bench.plan(args), tasks, code, cross_model=True), [])

    def test_a_cross_model_run_is_labelled_refused_as_a_claim_and_priced_by_its_own_model(self):
        args = bench.parse(["--sides", "angelo", "--connection-id", CONN])
        reused = self.mcp_run("sonnet", dict(bench.plan(args), model="claude-sonnet-5-5"), bench.tasks(args),
                              integrity.test_code())
        tier = bench.parse(["--sides", "angelo", "--connection-id", CONN, "--reuse-mcp-results", str(reused),
                            "--cross-model-reuse"])
        planned = bench.plan(tier)
        self.assertEqual(planned["cross_model_reuse"]["mcp"]["model"], "claude-sonnet-5-5")
        run = self.root / "tier"
        run.mkdir()
        (run / "RUN-PLAN.json").write_text(json.dumps(planned))
        self.assertIn("--cross-model-reuse", diagnostics.claim_refusal(run))
        self.assertIsNone(diagnostics.claim_refusal(run, angelo_only=True), "measured_results reads Angelo only")
        lines = integrity.markdown_lines(integrity.conditions(planned, []))
        self.assertTrue(any(line.startswith("**Cross-model reuse:**") for line in lines))
        with mock.patch("sys.stderr"), self.assertRaises(SystemExit):
            bench.parse(["--connection-id", CONN, "--cross-model-reuse"])
        usage = {"input_tokens": 1000, "cached_input_tokens": 0, "output_tokens": 100}
        records = [{"side": "mcp", "provider_usage": dict(usage), "runtime": "codex", "model": "gpt-6-astra"},
                   {"side": "mcp", "provider_usage": dict(usage)}]
        bench.price_records(records, "claude-opus-5-5", "claude")
        import oneshot_metrics
        self.assertEqual(records[0]["list_price_usd"], oneshot_metrics.list_cost(usage, "gpt-6-astra", "5m"))
        self.assertEqual(records[0]["list_price_basis"], oneshot_metrics.list_price_basis(
            [usage], "gpt-6-astra", "5m", oneshot_metrics.CODEX_UNSPLIT), "its own runtime's basis")
        self.assertEqual(records[1]["list_price_usd"], oneshot_metrics.list_cost(usage, "claude-opus-5-5", "5m"))

    def test_the_help_text_says_what_the_code_does(self):
        captured, real = {}, bench.argparse.ArgumentParser.parse_args

        def grab(parser, argv=None, namespace=None):
            captured["parser"] = parser
            return real(parser, argv, namespace)
        with mock.patch.object(bench.argparse.ArgumentParser, "parse_args", grab):
            bench.parse(["--connection-id", CONN])
        helps = {action.option_strings[0]: action.help for action in captured["parser"]._actions if action.option_strings}
        self.assertIn("runtime, model, effort, prompt SHA-256s, references and test code", helps["--reuse-mcp-results"])
        self.assertIn("never another runtime", helps["--cross-model-reuse"])


class MeasuredCrossModelTests(Folder):
    """F3, cross-model: measured_results reads a cross-model run's Angelo side only and never compares it with MCP."""

    def test_only_angelo_samples_are_read_and_no_mcp_figure_is_emitted(self):
        import measured_results
        run = self.root / "tier"
        run.mkdir()
        plan = {"model": "claude-opus-5-5", "effort": "low", "cross_model_reuse": {
            "mcp": {"model": "claude-sonnet-5-5", "effort": "low"}, "angelo": {"model": "claude-opus-5-5"}}}
        records = [{"side": side, "trial": 1, "task": "t%d" % index, "kind": "edit", "status": "completed",
                    "host_check_passed": side == "angelo", "grade": "passed" if side == "angelo" else "failed",
                    "wall_ms": 1000.0 if side == "angelo" else 99000.0,
                    "provider_usage": {"cost_usd": 0.01 if side == "angelo" else 9.0}}
                   for side in ("angelo", "mcp") for index in range(3)]
        (run / "bench-mcp-vs-angelo.json").write_text(json.dumps({"meta": {"plan": plan}, "records": records}))
        output, errors = quiet(measured_results.main, [str(run)])
        self.assertEqual(output, 0)
        self.assertIn("only Angelo's samples are read", errors)
        entry = measured_results.measured([str(run)])["claude-opus-5-5"]["low"]["scene_edit"]
        self.assertEqual((entry["tasks"], entry["pass_rate"], entry["p75_ms"], entry["cost_usd"]), (3, 1.0, 1000.0, 0.01))
        self.assertNotIn("mcp", json.dumps(measured_results.measured([str(run)])).lower(), "no MCP figure is emitted")
        self.assertIn("--cross-model-reuse", diagnostics.claim_refusal(run), "the gate and claim tools still refuse it")


class LiveEvalCodexTests(unittest.TestCase):
    """F4, revised for Sol runs: debug thinking works on Codex (Angelo's chats already ask for reasoning summaries,
    the MCP side asks for the same), at parse, from the connection and in the app."""

    def test_debug_thinking_runs_on_codex(self):
        args = eval_live_agent.parse(["--connection-id", CONN, "--codex-home", "/x", "--debug-thinking"])
        self.assertTrue(args.debug_thinking)
        controller = SimpleNamespace(state={"provider": "codex_subscription"}, debug_thinking=False)
        self.assertTrue(diagnostics.configure(controller, {diagnostics.DEBUG_ENV: "1"})["diagnostics"]["debug_thinking"])
        self.assertTrue(controller.debug_thinking)


class GroupTests(unittest.TestCase):
    """F5: a one-shot group's files keep the members' diagnostic stamp and scripts label; gate lines name scripts."""

    def test_group_outputs_carry_the_stamp_and_scripts(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            for member in oneshot_group.scenario_specs.ONESHOT_GROUPS["oneshot-minis"][:2]:
                (base / member).mkdir(parents=True)
                card = {"sides": [], "conditions": {"scripts": integrity.SCRIPT_LABELS["allowed"]},
                        "diagnostics": {"debug_thinking": True}}
                (base / member / "oneshot-scorecard.json").write_text(json.dumps(card))
            quiet(oneshot_group.summarize, base, "oneshot-minis", {})
            text = (base / oneshot_group.SUMMARY).read_text()
            self.assertTrue(text.split("\n")[2].startswith("> **Diagnostic run (debug thinking)"))
            self.assertIn("Scripts: scripts allowed", text)
            data = json.loads((base / oneshot_group.RESULTS).read_text())
            self.assertTrue(data["diagnostics"]["debug_thinking"])
            self.assertIn("diagnostic run", diagnostics.claim_refusal(base))

    def test_gate_lines_name_the_scripts(self):
        totals = {"gate_version": 2, "grading_contracts": [], "answers": {"read": 0}, "scripts": "allowed",
                  "graded": {"passed": 1, "failed": 0, "ungraded": 0, "skipped": 0, "total": 1, "not_run": 0}}
        with mock.patch.object(bench_mcp_gate.grading, "describe", return_value="passed 1 of 1"):
            self.assertIn("(scripts allowed)", bench_mcp_gate.line("run", totals))


class GraderCodeTests(unittest.TestCase):
    """F6: the reuse test-code digest covers the product graders and everything they import."""

    def test_a_product_grader_change_changes_the_digest(self):
        with tempfile.TemporaryDirectory() as root:
            harness, addon = Path(root) / "tests/python/angelo_agent", Path(root) / "scripts/addons_core/angelo_agent"
            harness.mkdir(parents=True)
            addon.mkdir(parents=True)
            (harness / "bench_mcp_grading.py").write_text("x = 1\n")
            (addon / "scene_validation.py").write_text("from .validation_reader import read\n")
            (addon / "validation_reader.py").write_text("def read():\n    return 1\n")
            (addon / "providers.py").write_text("y = 1\n")
            names = [path.name for path in integrity.test_code_sources(root)]
            self.assertEqual(names, ["bench_mcp_grading.py", "scene_validation.py", "validation_reader.py"])
            before = integrity.test_code(root)["sha256"]
            (addon / "providers.py").write_text("y = 2\n")
            integrity.test_code.cache_clear()
            self.assertEqual(integrity.test_code(root)["sha256"], before, "a product change outside the graders")
            (addon / "validation_reader.py").write_text("def read():\n    return 2\n")
            integrity.test_code.cache_clear()
            self.assertNotEqual(integrity.test_code(root)["sha256"], before)
        integrity.test_code.cache_clear()
        self.assertIn("scene_validation.py", [path.name for path in integrity.test_code_sources()])


class AskBeforeUndoTests(unittest.TestCase):
    """F8: the follow-up sees the graded scene: the Undo probe runs after it, on both sides; the dry run asks."""

    def test_in_app_the_probe_waits_for_the_follow_up(self):
        events = []
        scenario = SimpleNamespace(undo_probe=lambda bpy, window: events.append("undo probe"))
        failing = {"task": "brief", "status": "completed", "host_check_passed": False,
                   "host_check": {"results": [{"label": "sky", "passed": False, "details": {}}]}}
        trial = SimpleNamespace(report={"tasks": [failing]}, controller=object(), scenario=scenario, window=None,
                                finish=lambda: events.append("finish"))
        environ = {diagnostics.ASK_ENV: "1"}
        self.assertTrue(diagnostics.asks_why(trial, environ))
        self.assertTrue(trial.deferred_probe)
        ask_why.finish(trial, None)
        self.assertEqual(events, ["undo probe", "finish"])
        source = (HERE / "eval_live_agent_session.py").read_text()
        self.assertIn("self.scenario.finish(bpy, self.window, probe=not eval_diagnostics.asks_why(self))", source)

    def test_the_mcp_side_asks_between_the_saved_copy_and_the_probe(self):
        events = []
        config = {"task_set": "oneshot-bridge", "ask_why_on_miss": True}
        output = {"tasks": [{"host_check_passed": False, "host_check": {"results": [
            {"label": "sky", "passed": False, "details": {}}]}}]}
        stream = SimpleNamespace(process=SimpleNamespace(poll=lambda: None))
        with mock.patch.object(bench_mcp_scenario, "finish",
                               side_effect=lambda *a, **k: events.append(("finish", k.get("probe")))), \
                mock.patch.object(bench_mcp_scenario, "undo_probe", side_effect=lambda *a: events.append("probe")), \
                mock.patch.object(ask_why, "ask_mcp", side_effect=lambda *a: events.append("ask")):
            bench_mcp_scenario.end_of_trial(object(), stream, None, Path("/tmp"), config, output)
        self.assertEqual(events, [("finish", False), "ask", "probe"])

    def test_the_dry_run_passes_the_flags_and_forces_the_question(self):
        with mock.patch("scenario_dry_run.run", return_value={"success": True, "checks": {}, "elapsed_s": 0}) as run, \
                mock.patch("sys.stdout"):
            bench.main(["--task-set", "oneshot-bridge", "--dry-run-scenario", "--output", "/tmp/x", "--debug-thinking",
                        "--ask-why-on-miss"])
        self.assertEqual(run.call_args.kwargs["diagnostics"], {"debug_thinking": True, "ask_why_on_miss": True})
        failures, reason = ask_why.due({"tasks": []}, object(), {ask_why.FORCE_ENV: "1", ask_why.DRY_RUN_ENV: "1"})
        self.assertEqual((failures, reason), ([ask_why.FORCED], None))
        self.assertEqual(ask_why.due({"tasks": []}, object(), {ask_why.FORCE_ENV: "1"})[1], "no closed check failed",
                         "forced only inside the dry run")
        self.assertIn("eval_ask_why.HEADER in text", (HERE / "scenario_dry_run_session.py").read_text())


class DocumentedCommandTests(unittest.TestCase):
    """F9: the documented one-shot command runs with the product's Always allow scripts setting."""

    def test_the_one_shot_command_allows_scripts(self):
        doc = (HERE.parents[2] / "doc/angelo/oneshot_tests.rst").read_text()
        block = doc[doc.index("python3 tests/python/angelo_agent/bench_mcp_vs_angelo.py --authorize-billed-runs"):]
        self.assertIn("--angelo-always-allow-scripts", block[:block.index("--output <new folder>")])


if __name__ == "__main__":
    unittest.main()
