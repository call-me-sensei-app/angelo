# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The head-to-head bench resumes an interrupted run, prices every request and passes Trusted scripts on.

No process, network or account: every runner is replaced.
"""

import contextlib
import io
import json
import os
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

import bench_mcp_vs_angelo as bench  # noqa: E402
import eval_live_agent  # noqa: E402

CONNECTION = "22222222-2222-4222-8222-222222222222"


def finished(output, side, trial):
    if side == "mcp":
        folder = output / ("mcp-trial-%02d" % trial)
        folder.mkdir(parents=True)
        (folder / "trial.json").write_text("{}")
    else:
        folder = output / ("angelo-trial-%02d" % trial)
        folder.mkdir(parents=True)
        (folder / "eval-live-agent.json").write_text(json.dumps({"trials": [{"tasks": [{}]}]}))


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.output = Path(self.folder.name) / "out"
        self.output.mkdir()
        self.ran = []

    def argv(self, *extra):
        return ["--authorize-billed-runs", "--connection-id", CONNECTION, "--runtime", "codex", "--trials", "3",
                "--output", str(self.output), "--resume", *extra]

    def run_main(self, argv):
        def mcp_trial(config, trial, folder):
            self.ran.append(("mcp", trial))
            folder.mkdir()
            (folder / "trial.json").write_text("{}")
            return {"tasks": [{}]}

        def angelo_trial(args, output, trial):
            self.ran.append(("angelo", trial))
            finished(output, "angelo", trial)
            return {"side": "angelo", "trial": trial}
        summary = {"summary": {"gate_versions": None, "totals": {}, "invalid_rate_limited": 0}}
        with mock.patch.object(bench, "preflight", side_effect=lambda args: ([], Path(args.output).resolve())), \
                mock.patch.object(bench.codex_mcp, "run_trial", side_effect=mcp_trial), \
                mock.patch.object(bench, "run_angelo_trial", side_effect=angelo_trial), \
                mock.patch.object(bench, "gather", return_value=([], [])), \
                mock.patch.object(bench, "finish", return_value=summary), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return bench.main(argv)

    def test_completed_trials_are_skipped_and_a_cut_short_one_is_kept_and_run_again(self):
        finished(self.output, "mcp", 1)
        finished(self.output, "angelo", 1)
        (self.output / "mcp-trial-02").mkdir()  # Interrupted: no receipt.
        (self.output / "mcp-trial-02" / "claude-stream.jsonl").write_text("partial")
        self.assertEqual(self.run_main(self.argv("--max-new-trials", "1")), 0)
        self.assertEqual(self.ran, [("mcp", 2), ("angelo", 2)], "One new trial number: both its sides")
        kept = list((self.output / "interrupted").glob("mcp-trial-02-*/mcp-trial-02/claude-stream.jsonl"))
        self.assertEqual(len(kept), 1, "The cut-short trial is kept, never deleted")
        self.assertTrue(json.loads((self.output / bench.RUN_PLAN).read_text())["order"])
        self.ran.clear()
        self.assertEqual(self.run_main(self.argv()), 0)
        self.assertEqual(self.ran, [("mcp", 3), ("angelo", 3)])

    def test_a_stable_workspace_left_by_an_interruption_is_set_aside(self):
        (self.output / "angelo-state").mkdir()
        (self.output / "angelo-trial-01-tmp").mkdir()
        moved = bench.set_aside(self.output, "angelo", 1)
        self.assertEqual(sorted(moved), ["angelo-state", "angelo-trial-01-tmp"])
        self.assertFalse((self.output / "angelo-state").exists())

    def test_a_changed_plan_is_refused(self):
        args = bench.parse(self.argv())
        (self.output / bench.RUN_PLAN).write_text(json.dumps(dict(bench.plan(args), model="another-model")))
        (self.output / "mcp-trial-01").mkdir()
        problems, _output = bench.preflight(args)
        self.assertTrue(any("plan differs" in problem for problem in problems))

    def test_without_resume_a_used_folder_is_still_refused(self):
        (self.output / "mcp-trial-01").mkdir()
        args = bench.parse([item for item in self.argv() if item != "--resume"])
        problems, _output = bench.preflight(args)
        self.assertTrue(any("new or empty" in problem for problem in problems))


class PreflightOnlyTests(unittest.TestCase):
    def test_the_bench_checks_a_billed_start_and_sends_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            argv = ["--preflight-only", "--connection-id", CONNECTION, "--runtime", "codex", "--sides", "angelo",
                    "--output", str(Path(folder) / "out")]
            with mock.patch.object(bench, "preflight", return_value=([], Path(folder) / "out")) as check, \
                    mock.patch.object(bench, "run_angelo_trial") as trial, \
                    contextlib.redirect_stdout(io.StringIO()) as printed:
                self.assertEqual(bench.main(argv), 0)
            self.assertTrue(check.called)
            trial.assert_not_called()
            self.assertIn("PREFLIGHT_OK", printed.getvalue())
        args = bench.parse(["--preflight-only", "--connection-id", CONNECTION])
        with mock.patch.object(bench.live, "refusal_reasons", return_value=[]) as reasons:
            bench.preflight(args)
        self.assertTrue(reasons.call_args.args[0], "Checked as an authorized run would be")

    def test_the_live_eval_stops_after_its_checks(self):
        source = (HERE / "eval_live_agent.py").read_text()
        checks, start = source.index('print("CONNECTION"'), source.index("trials.append(run_trial(")
        self.assertLess(source.index("if args.preflight_only:", checks), start)
        self.assertIn("refusal_reasons(args.authorize_billed_runs or args.preflight_only", source)


class WindowsMcpSideTests(unittest.TestCase):
    def test_the_server_and_environment_work_on_windows(self):
        import bench_mcp_trial as trial
        self.assertTrue(trial.server_executable("/srv", "win32").replace("\\", "/").endswith(
            ".venv/Scripts/blender-mcp.exe"))
        # The host's own separator joins the path, so compare in one spelling on every host.
        self.assertTrue(trial.server_executable("/srv", "darwin").replace("\\", "/").endswith(".venv/bin/blender-mcp"))
        for name in ("SYSTEMROOT", "TEMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
            self.assertIn(name, trial.KEEP_ENV)


class PriceTests(unittest.TestCase):
    def test_every_request_gets_uncached_input_and_its_list_price(self):
        codex = {"side": "angelo", "provider_usage": {"input_tokens": 24426, "cached_input_tokens": 24064,
                                                      "cache_write_input_tokens": 0, "output_tokens": 18}}
        missing = {"side": "mcp", "provider_usage": None}
        bench.price_records([codex, missing], "gpt-6.1-sol", "codex")
        self.assertEqual(codex["uncached_input_tokens"], 362)
        self.assertAlmostEqual(codex["list_price_usd"], (362 * 2.0 + 24064 * 0.10 + 18 * 10.0) / 1e6, places=4)
        self.assertIsNone(missing["list_price_usd"], "Unknown usage is never priced as zero")
        claude = [{"side": side, "provider_usage": {"input_tokens": 1000, "cached_input_tokens": 0,
                                                     "cache_write_input_tokens": 1000, "output_tokens": 0}}
                  for side in ("angelo", "mcp")]
        bench.price_records(claude, "claude-opus-5-5", "claude")
        self.assertEqual([round(r["list_price_usd"], 4) for r in claude], [0.008, 0.005], "1-hour vs 5-minute writes")

    def test_a_billed_warm_up_is_priced_with_the_task_it_was_recorded_in(self):
        chat = {"input_tokens": 24426, "cached_input_tokens": 24064, "cache_write_input_tokens": 0,
                "output_tokens": 18}
        warm_up = {"input_tokens": 24199, "cached_input_tokens": 0, "cache_write_input_tokens": 0,
                   "output_tokens": 16, "turns": 1}
        task = {"side": "angelo", "provider_usage": chat, "warm_ups": 1, "warm_up_usage": warm_up}
        alone = {"side": "angelo", "provider_usage": dict(chat)}
        unknown = {"side": "angelo", "provider_usage": dict(chat), "warm_ups": 1, "warm_up_usage": None}
        bench.price_records([task, alone, unknown], "gpt-6.1-sol", "codex")
        self.assertEqual(task["uncached_input_tokens"], 362 + 24199)
        self.assertAlmostEqual(task["list_price_usd"] - alone["list_price_usd"], (24199 * 2.0 + 16 * 10.0) / 1e6,
                               places=4)
        self.assertIn("1 warm-up", task["list_price_basis"])
        self.assertIsNone(unknown["list_price_usd"], "A billed warm-up of unknown usage is never left out")


class WarmUpReceiptTests(unittest.TestCase):
    USAGE = {"input_tokens": 24199, "cached_input_tokens": 0, "output_tokens": 16, "turns": 1}

    def test_each_warm_up_is_counted_once_and_unknown_usage_is_none(self):
        from eval_receipts import warm_up_cursor, warm_ups_since
        usage = self.USAGE
        receipts = [{"session_id": "earlier"}]
        store = SimpleNamespace(receipts=receipts)
        controller = SimpleNamespace(telemetry=store)
        cursor = warm_up_cursor(store)
        receipts += [{"kind": "warm_up", "usage": dict(usage), "phases": {"total_ms": 6100.0}},
                     {"session_id": "chat", "usage": dict(usage)}]
        self.assertEqual(warm_ups_since(controller, cursor), {"warm_ups": 1, "warm_up_usage": usage,
                                                              "warm_up_ms": 6100.0})
        self.assertEqual(warm_ups_since(controller, cursor), {"warm_ups": 0, "warm_up_usage": None, "warm_up_ms": 0})
        receipts += [{"kind": "warm_up", "usage": dict(usage), "phases": {"total_ms": 5000.0}}, {"kind": "warm_up"}]
        self.assertEqual(warm_ups_since(controller, cursor), {"warm_ups": 2, "warm_up_usage": None, "warm_up_ms": None},
                         "a warm-up whose wall time is unknown leaves the sum unknown, never short")

    def test_an_earlier_launchs_warm_up_on_a_kept_profile_is_never_counted_again(self):
        """Regression: --returning-user trial 2 counted trial 1's persisted warm-up (performance.json) again."""
        from angelo_agent.telemetry import TelemetryStore
        from eval_receipts import warm_up_cursor, warm_ups_since
        with tempfile.TemporaryDirectory() as folder:
            first = TelemetryStore(folder)  # Trial 1's launch.
            cursor = warm_up_cursor(first)
            first.record({"kind": "warm_up", "status": "completed", "runtime": "codex", "phases": {"total_ms": 7000.0},
                          "usage": dict(self.USAGE)})
            self.assertEqual(warm_ups_since(SimpleNamespace(telemetry=first), cursor)["warm_ups"], 1)
            self.assertTrue(first.flush())
            second = TelemetryStore(folder)  # Trial 2: a new launch loads the kept performance.json.
            self.assertEqual(len(second.receipts), 1)
            cursor = warm_up_cursor(second)
            self.assertEqual(warm_ups_since(SimpleNamespace(telemetry=second), cursor),
                             {"warm_ups": 0, "warm_up_usage": None, "warm_up_ms": 0},
                             "Its remembered base was resumed: no warm-up")

    def test_an_evicted_marker_counts_every_retained_receipt_once(self):
        from eval_receipts import warm_ups_since
        marker = {"session_id": "evicted"}
        receipts = [{"kind": "warm_up", "usage": dict(self.USAGE)}]
        controller = SimpleNamespace(telemetry=SimpleNamespace(receipts=receipts))
        cursor = {"marker": marker}  # The store dropped it: everything it holds is newer.
        self.assertEqual(warm_ups_since(controller, cursor)["warm_ups"], 1)
        self.assertIs(cursor["marker"], receipts[-1])
        self.assertEqual(warm_ups_since(controller, cursor)["warm_ups"], 0)


class OpeningLabelTests(unittest.TestCase):
    def test_by_default_nothing_is_readied_and_the_first_prompt_includes_the_launch(self):
        import eval_receipts
        self.assertEqual(eval_receipts.OPENING_WAIT, float(os.environ.get("ANGELO_EVAL_OPENING_WAIT", "0")))
        with mock.patch.object(eval_receipts, "OPENING_WAIT", 0.0):
            thread, _started = eval_receipts.begin_opening(object())
        self.assertIsNone(thread)

    def test_the_first_task_is_prestarted_when_a_runtime_already_ran_at_its_send(self):
        import eval_receipts
        from angelo_agent import codex_runtime
        receipts = []
        trial = SimpleNamespace(index=0, report={}, opening=(None, 0.0),
                                controller=SimpleNamespace(telemetry=SimpleNamespace(receipts=receipts)),
                                warm_ups={"marker": None}, tasks=[{"prompt": "first"}, {"prompt": "second"}])
        live = SimpleNamespace(dead=False, bases={})
        with mock.patch.dict(codex_runtime._POOL, {("key",): live}, clear=True):
            self.assertFalse(eval_receipts.opening_wait(trial, limit=0))
        self.assertEqual(trial.report["opening"]["runtime_live"], True,
                         "The app started one (a catalog load) though nothing was readied for the first message")
        self.assertFalse(trial.report["opening"]["prepared"])
        self.assertEqual(eval_receipts.task_extras(trial)["runtime_start"], "prestarted")
        trial.index = 1
        self.assertNotIn("runtime_start", eval_receipts.task_extras(trial))
        trial.index, trial.report = 0, {}
        with mock.patch.dict(codex_runtime._POOL, {}, clear=True):
            eval_receipts.opening_wait(trial, limit=0)
        self.assertFalse(trial.report["opening"]["runtime_live"])
        self.assertNotIn("runtime_start", eval_receipts.task_extras(trial))


class OpeningReportTests(unittest.TestCase):
    def test_the_report_shows_each_trials_opening_and_the_extra_is_labelled(self):
        import bench_mcp_method
        import bench_mcp_report
        trial = {"opening": {"prepared": True, "waited_s": 4.2}, "tasks": [
            {"task": "a", "kind": "edit", "status": "completed"}, {"task": "b", "kind": "edit", "status": "completed"}]}
        records = bench_mcp_report.angelo_records(trial, 1, {})
        self.assertEqual([r["opening"] for r in records], [{"prepared": True, "waited_s": 4.2}, None])
        line = next(o for o in bench_mcp_report.observations(records) if "opening" in o)
        self.assertIn("readied, waited 4.2 s", line)
        self.assertIn("includes the runtime launch on both sides", " ".join(bench_mcp_method.METHOD))
        self.assertIn("Extra variant", bench_mcp_method.METHOD_OPENING[0])
        self.assertIn("not comparable", bench_mcp_method.THREATS_OPENING[0])


class TrustedScriptsTests(unittest.TestCase):
    def setUp(self):
        from test_native_addon_guard import matching_app
        matching_app(self)   # The fixture app bundles no add-on; the bench's add-on pin has its own tests.

    def test_the_bench_plans_and_passes_trusted_scripts_to_angelos_side(self):
        args = bench.parse(["--angelo-trusted-scripts", "--connection-id", CONNECTION])
        self.assertTrue(bench.plan(args)["angelo"]["trusted_scripts"])
        self.assertFalse(bench.plan(bench.parse([]))["angelo"]["trusted_scripts"])
        with tempfile.TemporaryDirectory() as folder:
            args.output, args.source_connections_dir, args.angelo_binary = folder, folder, "/app/Angelo"
            args.claude_config_dir = folder
            with mock.patch("subprocess.run", return_value=SimpleNamespace(returncode=0)) as run:
                bench.run_angelo_trial(args, Path(folder), 1)
        self.assertIn("--trusted-scripts", run.call_args.args[0])

    def test_the_bench_passes_the_approval_and_always_allow_scripts_to_angelos_side(self):
        args = bench.parse(["--angelo-always-allow-scripts", "--angelo-approval", "default", "--connection-id", CONNECTION])
        self.assertEqual((bench.plan(args)["angelo"]["approval"], bench.plan(args)["angelo"]["always_allow_scripts"]),
                         ("default", True))
        plain = bench.plan(bench.parse([]))["angelo"]
        self.assertEqual((plain["approval"], plain["always_allow_scripts"]), ("default", False))
        for flags, expected, absent in ((["--angelo-always-allow-scripts"], ["--always-allow-scripts"], "--approval"),
                                        (["--angelo-approval", "ask"], ["--approval", "ask"], "--always-allow-scripts")):
            with self.subTest(flags=flags), tempfile.TemporaryDirectory() as folder:
                args = bench.parse(flags + ["--connection-id", CONNECTION])
                args.output, args.source_connections_dir, args.angelo_binary = folder, folder, "/app/Angelo"
                args.claude_config_dir = folder
                with mock.patch("subprocess.run", return_value=SimpleNamespace(returncode=0)) as run:
                    bench.run_angelo_trial(args, Path(folder), 1)
                command = run.call_args.args[0]
                self.assertEqual(command[command.index(expected[0]):command.index(expected[0]) + len(expected)], expected)
                self.assertNotIn(absent, command)

    def test_the_live_eval_plans_it_and_hands_it_to_the_app(self):
        args = eval_live_agent.parse(["--trusted-scripts", "--connection-id", CONNECTION])
        environment = eval_live_agent.trial_environment(Path("/tmp/trial"), Path("/tmp/accounts"), args, 1)
        self.assertEqual(environment["ANGELO_EVAL_TRUSTED_SCRIPTS"], "1")
        plain = eval_live_agent.trial_environment(Path("/tmp/trial"), Path("/tmp/accounts"),
                                                  eval_live_agent.parse(["--connection-id", CONNECTION]), 1)
        self.assertEqual(plain["ANGELO_EVAL_TRUSTED_SCRIPTS"], "")

    def test_the_in_app_tester_leaves_trusted_scripts_to_the_chat(self):
        source = (HERE / "eval_live_agent_session.py").read_text()
        self.assertIn("self.controller.set_trusted_scripts(True)", source)
        self.assertEqual(source.count("**self.configure()"), 2, "Subscription and team boot set it alike")
        # Trusted, always-allowed or Auto-bounded alike, for the lead and (the parallel arm) each of its workers' chats:
        # the chat runs those itself, so the tester never answers them first (a worker's bounded edit was "approved"
        # by the tester between the chat's two steps, and counted as a card, in the 2026-10-25 dev check).
        self.assertIn("if chat._automatic_script(call) or chat._auto_bounded(call):", source)
        self.assertIn("for chat, call in eval_parallel.cards(ui, self.controller):", source)

    def test_the_in_app_tester_sends_as_the_chat_editor_does(self):
        source = (HERE / "eval_live_agent_session.py").read_text()
        self.assertIn("recovery_evidence=turn.recovery_evidence if turn is not None else None", source)
        product = (HERE.parents[2] / "scripts/addons_core/angelo_agent/console.py").read_text()
        self.assertIn("recovery_evidence=turn.recovery_evidence", product)


if __name__ == "__main__":
    unittest.main()
