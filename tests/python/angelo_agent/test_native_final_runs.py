# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The final-runs campaign driver: order, commands, quiet-machine checks, checkpoints. Nothing is launched."""

import contextlib
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import bench_mcp_vs_angelo as bench  # noqa: E402
import final_runs as runs  # noqa: E402
import final_runs_load as quiet  # noqa: E402
import final_runs_mcp  # noqa: E402
import final_runs_plan as plan  # noqa: E402


CONNECTIONS = {"claude": "11111111-1111-4111-8111-111111111111", "chatgpt": "22222222-2222-4222-8222-222222222222"}


def config(folder, **extra):
    candidate = Path(folder) / "candidate"
    candidate.mkdir(exist_ok=True)
    exe = candidate / "Angelo"
    exe.write_text("binary")
    (candidate / runs.RECEIPT).write_text(json.dumps({"executable": str(exe), "executable_sha256": runs.digest(exe)}))
    return dict({"platform": "macos", "output": str(folder), "candidate": str(candidate), "connections_dir": "/conn",
                 "connections": dict(CONNECTIONS), "codex_home": "/codex-profile/x", "stock_blender": "/Blender", "mcp_server_dir": "/mcp",
                 "mcp_addon_dir": "/addon", "codex": "/codex", "claude": "", "python": "/py", "load_threshold": 6.0,
                 "pilot": {"dir": "/pilot", "configs": {"sol": "/pilot/configs/sol.json",
                                                        "astra": "/pilot/configs/astra.json"}}}, **extra)


class OrderTests(unittest.TestCase):
    def test_gates_come_first_and_cover_the_three_models_at_their_bars(self):
        steps = plan.campaign("macos")
        gates = [s for s in steps if s["phase"] == "gate"]
        self.assertEqual(steps[:len(gates)], gates)
        self.assertEqual(sorted({s["model"] for s in gates}), ["claude-opus-5-5", "gpt-6-astra", "gpt-6.1-sol"])
        prompts = {s["id"]: plan.estimate(s)["prompts"] for s in gates}
        for model in ("sol", "opus", "astra"):
            self.assertEqual(prompts["gate-%s-default" % model], 30)
            self.assertEqual(prompts["gate-%s-long" % model], 120)
            self.assertEqual(prompts["smoke-%s-gate7" % model], 7)
            self.assertEqual(prompts["smoke-%s-survey" % model], 8)
            # The gate runs in Auto; one trial of the default set checks Ask.
            ask = next(s for s in gates if s["id"] == "smoke-%s-ask" % model)
            self.assertEqual((prompts[ask["id"]], ask["tool"], ask["task_set"]), (6, "bench", "default"))
            self.assertEqual(ask["args"][ask["args"].index("--angelo-approval") + 1], "ask")
            self.assertFalse(any("--angelo-approval" in s["args"] for s in gates if not s["id"].endswith("-ask")),
                             "the rest in Auto")

    def test_models_cover_their_task_sets(self):
        steps = plan.campaign("macos")
        ids = {s["id"] for s in steps}
        for model in ("sol", "astra"):
            for name in ("default", "long", *plan.MINIS, *plan.SCENES, *plan.HEAVY):
                self.assertIn("h2h-%s-%s" % (model, name), ids)
            for case in plan.PILOT_CASES:
                self.assertIn("pilot-%s-%s" % (model, case), ids)
        opus = {s["id"] for s in steps if s["model_key"] == "opus" and s["phase"] == "h2h"}
        self.assertEqual(opus, {"h2h-opus-default", "h2h-opus-long"}
                         | {"h2h-opus-" + m for m in plan.MINIS + plan.HEAVY})
        self.assertEqual(len(steps), 60)
        for model in ("sol", "astra"):
            self.assertIn("--returning-user", next(s for s in steps if s["id"] == "returning-%s-default" % model)["args"])
        windows = plan.campaign("windows")
        self.assertFalse([s for s in windows if s["tool"] == "pilot"], "The pilot adapter is macOS only")
        self.assertEqual(len(windows), 52)

    def test_accounts_alternate_and_the_heaviest_runs_come_last(self):
        h2h = [s for s in plan.campaign("macos") if s["phase"] == "h2h"]
        blocks = [h2h[0]["model_key"]]
        for step in h2h[1:]:
            if step["model_key"] != blocks[-1]:
                blocks.append(step["model_key"])
        self.assertEqual(blocks, ["sol", "opus", "astra", "opus", "sol", "astra"])
        self.assertTrue(all(s["kind"] in ("long", "scene", "heavy") for s in h2h[-10:]))

    def test_estimates_are_priced_at_list_prices(self):
        step = next(s for s in plan.campaign("macos") if s["id"] == "gate-sol-default")
        value = plan.estimate(step)
        expected = ((value["input"] - value["cached"]) * 2.0 + value["cached"] * 0.10 + value["output"] * 10.0) / 1e6
        self.assertAlmostEqual(value["usd"], expected, places=2)
        totals = plan.summary(plan.campaign("macos"))
        self.assertEqual(set(totals["by_account"]), {"claude", "chatgpt"})


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.config = config(self.folder.name)
        self.steps = {s["id"]: s for s in plan.campaign("macos")}

    def line(self, step_id, billed=True):
        return runs.command(self.steps[step_id], self.config, Path(self.folder.name) / step_id, billed=billed)

    def test_billed_bench_runs_one_trial_per_call_and_resumes(self):
        line = self.line("h2h-sol-default")
        self.assertIn("--authorize-billed-runs", line)
        self.assertEqual(line[-3:], ["--resume", "--max-new-trials", "1"])
        # The product's own setting (scripts allowed), never the chat-level test switch (bench_integrity).
        self.assertIn("--angelo-always-allow-scripts", line)
        self.assertNotIn("--angelo-trusted-scripts", line)
        for flag, value in (("--runtime", "codex"), ("--sides", "both"), ("--trials", "5"),
                            ("--codex-home", "/codex-profile/x"), ("--mcp-addon-dir", "/addon")):
            self.assertEqual(line[line.index(flag) + 1], value)

    def test_the_gate_keeps_its_own_conditions(self):
        line = self.line("gate-opus-long")
        self.assertNotIn("--angelo-trusted-scripts", line)
        self.assertNotIn("--angelo-always-allow-scripts", line, "the gate stays in Auto with no script opt-in")
        self.assertNotIn("--mcp-addon-dir", line)
        self.assertEqual(line[line.index("--sides") + 1], "angelo")
        self.assertEqual(line[line.index("--long-tasks") + 1], "40")
        self.assertEqual(line[line.index("--trials") + 1], "3")
        self.assertNotIn("--codex-home", line)

    def test_every_gate_runs_parallel_edit_as_shipped_and_none_can_set_it(self):
        """The gates of record test the product as shipped (release-106): each gate step's own tool gets its as-shipped
        flag from the step builder, so none can be built forced on, forced off, or (without it) off by default."""
        for platform in ("macos", "windows"):
            for step in plan.campaign(platform):
                switches = [arg for arg in step["args"] if arg in plan.PARALLEL_SWITCHES]
                with self.subTest(platform=platform, step=step["id"]):
                    if step["phase"] == "gate":
                        self.assertEqual(switches, [plan.AS_SHIPPED[step["tool"]]])
                    else:
                        self.assertEqual(switches, [], "the head-to-heads stay as they are (a separate question)")
        for switch in plan.PARALLEL_SWITCHES:
            with self.assertRaises(ValueError):
                plan._step("gate-x", "gate", "sol", "bench", "default", 1, ("angelo",), {"angelo": 6}, "default",
                           [switch])
        with self.assertRaises(KeyError):   # A new gate tool must choose its as-shipped form first.
            plan._step("gate-x", "gate", "sol", "pilot", "default", 1, ("angelo",), {"angelo": 6}, "default")
        for step_id, flag in (("gate-sol-default", "--angelo-parallel-as-shipped"), ("gate-opus-long",
                                                                                    "--angelo-parallel-as-shipped"),
                              ("smoke-astra-gate7", "--parallel-as-shipped"), ("smoke-sol-survey",
                                                                                 "--parallel-as-shipped"),
                              ("smoke-opus-ask", "--angelo-parallel-as-shipped")):
            for billed in (True, False):
                with self.subTest(step=step_id, billed=billed):
                    self.assertEqual(self.line(step_id, billed).count(flag), 1)

    def test_the_bench_forwards_as_shipped_to_each_angelo_trial(self):
        base = ["--sides", "angelo", "--connection-id", CONNECTIONS["claude"]]
        folder = Path(self.folder.name)
        for extra, forwarded in ((["--angelo-parallel-as-shipped"], {"--parallel-as-shipped"}),
                                 (["--angelo-parallel-agents"], {"--parallel-agents"}), ([], set())):
            with self.subTest(extra=extra):
                command, _workspace = bench.angelo_command(bench.parse(base + extra), folder, folder / "t")
                self.assertEqual(set(command) & {"--parallel-as-shipped", "--parallel-agents"}, forwarded)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            bench.parse(base + ["--angelo-parallel-agents", "--angelo-parallel-as-shipped"])

    def test_a_run_that_forces_parallel_edit_says_so(self):
        """The head-to-heads force it off (only the gates run as shipped): every h2h bench step's run plan records it and
        its threats say so, and no gate step's do (break-it E2E-7 M-2, E2E-8 M-4: from the recorded plan)."""
        base = ["--sides", "both", "--connection-id", CONNECTIONS["claude"]]
        for platform in ("macos", "windows"):
            for step in plan.campaign(platform):
                if step["tool"] != "bench":
                    continue
                with self.subTest(platform=platform, step=step["id"]):
                    args = bench.parse(base + [a for a in step["args"] if a != "--returning-user"])
                    setting = bench.plan(args)["angelo"]["parallel_edit"]
                    self.assertEqual(setting, "as shipped" if step["phase"] == "gate" else "forced off")
                    line = bench.parallel_disclosure(setting)
                    if step["phase"] == "gate":
                        self.assertEqual(line, "")
                    else:
                        self.assertIn("forced off (ANGELO_ENABLE_PARALLEL_AGENTS=0), not as shipped", line)
        self.assertIn("forced on (ANGELO_ENABLE_PARALLEL_AGENTS=1)", bench.parallel_disclosure(
            bench.parallel_setting(bench.parse(base + ["--angelo-parallel-agents"]))))
        self.assertEqual(bench.parallel_disclosure(None), "", "a run recorded before the setting says nothing")
        self.assertNotEqual(bench.plan(bench.parse(base)), bench.plan(bench.parse(base + ["--angelo-parallel-agents"])),
                            "so --resume refuses a run whose switch changed (its RUN-PLAN.json differs)")

    def test_a_trials_report_carries_angelos_own_launch_line(self):
        import eval_parallel as eval_live_agent   # Where the trial driver reads it from.
        log = Path(self.folder.name) / "run.log"
        log.write_text("Blender 5.2\nAngelo: parallel edit on: Apple M4 Pro GPU, 48 GB, 14 cores\nquit\n")
        self.assertEqual(eval_live_agent.launch_line(log), "Angelo: parallel edit on: Apple M4 Pro GPU, 48 GB, 14 cores")
        log.write_text("Angelo: parallel edit on (forced at launch): Apple M4 Pro GPU\n")
        self.assertIn("(forced at launch)", eval_live_agent.launch_line(log))
        self.assertIsNone(eval_live_agent.launch_line(Path(self.folder.name) / "missing.log"))

    def test_unbilled_forms_never_authorize(self):
        for step_id in ("gate-sol-default", "smoke-astra-survey", "h2h-opus-oneshot-mini-cube"):
            line = self.line(step_id, billed=False)
            self.assertIn("--plan-only", line)
            self.assertNotIn("--authorize-billed-runs", line)
        pilot = self.line("pilot-astra-duplication", billed=False)
        self.assertIn("smoke", pilot)
        self.assertNotIn("--authorize-measured-trials", pilot)
        self.assertIn("--authorize-measured-trials", self.line("pilot-astra-duplication"))

    def test_the_pilot_runs_on_its_own_interpreter_and_stops_with_the_campaign(self):
        self.config["pilot"]["python"] = "/opt/python3.14"
        line = self.line("pilot-sol-node_editing")
        self.assertEqual(line[:5], ["nice", "-n", "10", "/opt/python3.14", "-B"])
        self.assertEqual(line[line.index("--stop-file") + 1], str(Path(self.folder.name) / "STOP"))
        self.assertNotIn("--stop-file", self.line("pilot-sol-node_editing", billed=False))

    def test_the_pilot_must_run_the_frozen_candidate(self):
        folder = Path(self.folder.name)
        (folder / "stock").write_text("stock")
        self.config["stock_blender"] = str(folder / "stock")
        receipt = json.loads((Path(self.config["candidate"]) / runs.RECEIPT).read_text())
        good = {"angelo_binary": receipt["executable"], "stock_blender": str(folder / "stock")}
        (folder / "other").write_text("another build")
        configs = {"sol": dict(good), "astra": dict(good, angelo_binary=str(folder / "other"), runtime_rehearsal=True)}
        for model, value in configs.items():
            (folder / (model + ".json")).write_text(json.dumps(value))
        self.config["pilot"]["configs"] = {model: str(folder / (model + ".json")) for model in configs}
        problems = runs.pilot_mismatches(self.config, receipt)
        self.assertEqual(len(problems), 2)
        self.assertTrue(all(problem.startswith("astra") for problem in problems))

    def test_pilot_entries_follow_the_pilots_counterbalanced_order(self):
        entries = runs.pilot_entries(self.steps["pilot-sol-duplication"])
        self.assertEqual(entries[:4], ["duplication-01-mcp", "duplication-01-angelo", "duplication-02-angelo",
                                       "duplication-02-mcp"])
        self.assertEqual(len(entries), 10)


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.steps = {s["id"]: s for s in plan.campaign("macos")}

    def test_finished_trials_are_counted_and_rate_limited_ones_are_not(self):
        step = self.steps["smoke-sol-gate7"]
        folder = self.folder / step["id"]
        folder.mkdir()
        (folder / "eval-live-agent.json").write_text(json.dumps({"trials": [{"invalid": "rate_limited"}]}))
        self.assertEqual(runs.completed(step, folder), set())
        (folder / "eval-live-agent.json").write_text(json.dumps({"trials": [{"success": True}]}))
        self.assertEqual(runs.completed(step, folder), {"angelo-1"})
        pilot = self.steps["pilot-sol-batch_edits"]
        entry = self.folder / pilot["id"] / "batch_edits-01-angelo"
        entry.mkdir(parents=True)
        (entry / "SUPERVISION.json").write_text(json.dumps({"completed_entries": 1,
                                                            "stop_reason": "provider_rate_or_usage_limit"}))
        self.assertEqual(runs.completed(pilot, self.folder / pilot["id"]), set())

    def test_a_rate_limited_account_waits_while_the_other_continues(self):
        steps = [self.steps["gate-sol-default"], self.steps["gate-opus-default"]]
        calls, clock = [], [1000.0]

        def fake_step(step, config, campaign_dir, state, deadline=None):
            calls.append(step["id"])
            state["steps"].setdefault(step["id"], {"trials": {}})
            return "blocked" if calls.count(step["id"]) == 1 and step["account"] == "chatgpt" else "done"
        with mock.patch.object(runs, "run_step", side_effect=fake_step), \
                mock.patch.object(runs.time, "time", side_effect=lambda: clock[0]), \
                mock.patch.object(runs.quiet, "load", return_value=[1, 1, 1]):
            code = runs.run({}, self.folder, steps, sleep=lambda s: clock.__setitem__(0, clock[0] + s))
        self.assertEqual(code, 0)
        self.assertEqual(calls, ["gate-sol-default", "gate-opus-default", "gate-sol-default"])
        events = [json.loads(line)["event"] for line in (self.folder / runs.JOURNAL).read_text().splitlines()]
        self.assertIn("rate_limited", events)
        self.assertIn("waiting_for_limits", events)

    def test_a_nearly_used_chatgpt_window_waits_for_its_reset(self):
        steps = [self.steps["gate-sol-default"], self.steps["gate-opus-default"]]
        calls, clock = [], [1000.0]
        windows = iter([(96.0, 5000.0), (2.0, 9000.0)])

        def fake_step(step, config, campaign_dir, state, deadline=None):
            calls.append(step["id"])
            state["steps"].setdefault(step["id"], {"trials": {}})
            return "done"
        with mock.patch.object(runs, "run_step", side_effect=fake_step), \
                mock.patch.object(runs, "chatgpt_window", side_effect=lambda config: next(windows)), \
                mock.patch.object(runs.time, "time", side_effect=lambda: clock[0]), \
                mock.patch.object(runs.quiet, "load", return_value=[1, 1, 1]):
            self.assertEqual(runs.run({}, self.folder, steps, sleep=lambda s: clock.__setitem__(0, clock[0] + s)), 0)
        self.assertEqual(calls, ["gate-opus-default", "gate-sol-default"], "Claude ran while ChatGPT's window reset")
        self.assertGreaterEqual(clock[0], 5000.0)

    def test_the_head_to_head_waits_for_every_gate_at_its_bar(self):
        gate, h2h = self.steps["gate-sol-default"], self.steps["h2h-sol-default"]
        state = {"steps": {gate["id"]: {"status": "done"}}}
        with mock.patch.object(runs, "gate_passes", return_value=(29, 30)):
            self.assertFalse(runs.gates_clear(self.folder, [gate, h2h], state))
        with mock.patch.object(runs, "gate_passes", return_value=(30, 30)):
            self.assertTrue(runs.gates_clear(self.folder, [gate, h2h], state))
        with mock.patch.object(runs, "run_step") as step, mock.patch.object(runs, "gate_passes", return_value=(29, 30)), \
                mock.patch.object(runs.quiet, "load", return_value=[1, 1, 1]):
            self.assertEqual(runs.run({}, self.folder, [gate, h2h], sleep=lambda s: None), 1)
        step.assert_not_called()


class GuardTests(unittest.TestCase):
    rows = [{"pid": 10, "ppid": 1, "cpu": 300.0, "name": "clang", "command": "clang -c x.cc"},
            {"pid": 11, "ppid": 1, "cpu": 2.0, "name": "Python", "command": "python3 run_host_tests.py"},
            {"pid": 12, "ppid": 1, "cpu": 80.0, "name": "Chrome Helper", "command": "Chrome Helper"},
            {"pid": 13, "ppid": 1, "cpu": 90.0, "name": "WindowServer", "command": "WindowServer"},
            {"pid": 20, "ppid": 1, "cpu": 150.0, "name": "Angelo", "command": "Angelo"},
            {"pid": 21, "ppid": 20, "cpu": 120.0, "name": "codex", "command": "codex app-server"}]

    def test_a_busy_machine_is_refused_with_its_reasons(self):
        state = quiet.check(6, own=(20,), rows=self.rows, averages=[7.5, 3.0, 2.0])
        self.assertFalse(state["quiet"])
        text = "; ".join(state["reasons"])
        self.assertIn("1-minute load 7.50", text)
        for name in ("clang", "run_host_tests.py"):
            self.assertIn(name, text)
        self.assertNotIn("Chrome Helper", text, "One resident program near a core is no contention by itself")
        self.assertNotIn("WindowServer", text)
        self.assertNotIn("codex", text, "The run's own processes never count against it")

    def test_programs_outside_the_run_together_above_the_threshold_are_refused(self):
        resident = [{"pid": 30 + i, "ppid": 1, "cpu": 90.0, "name": "Helper", "command": "Helper"} for i in range(7)]
        state = quiet.check(6, rows=resident, averages=[2.0, 2.0, 2.0])
        self.assertFalse(state["quiet"])
        self.assertIn("6.3 cores", "; ".join(state["reasons"]))
        self.assertTrue(quiet.check(6, rows=resident[:3], averages=[2.0, 2.0, 2.0])["quiet"])

    def test_process_names_with_spaces_are_read_whole(self):
        names = "  101     1  77.6 /Applications/Claude.app/Contents/MacOS/Claude Helper (Renderer)\n  102 1 0.0 /bin/zsh\n"
        commands = "  101 /Applications/Claude.app/Contents/MacOS/Claude Helper (Renderer) --type=renderer\n  102 -zsh\n"
        outputs = iter([mock.Mock(stdout=names, returncode=0), mock.Mock(stdout=commands, returncode=0)])
        with mock.patch.object(quiet.subprocess, "run", side_effect=lambda *a, **k: next(outputs)):
            rows = quiet.processes("darwin")
        self.assertEqual(rows[0]["name"], "Claude Helper (Renderer)")
        self.assertIn("--type=renderer", rows[0]["command"])
        self.assertEqual((rows[1]["name"], rows[1]["command"]), ("zsh", "-zsh"))

    def test_a_quiet_machine_passes(self):
        state = quiet.check(6, rows=[r for r in self.rows if r["pid"] in (13,)], averages=[1.2, 1.5, 2.0])
        self.assertTrue(state["quiet"], state["reasons"])

    def test_the_run_pauses_until_the_machine_is_quiet_and_says_so(self):
        states = iter([{"quiet": False, "reasons": ["busy"]}, {"quiet": False, "reasons": ["busy"]},
                       {"quiet": True, "reasons": []}])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "journal.jsonl"
            quiet.wait_quiet(6, path, checker=lambda: next(states), sleep=lambda s: None)
            events = [json.loads(line)["event"] for line in path.read_text().splitlines()]
            self.assertEqual(events, ["paused", "resumed"])
            stop = Path(folder) / "STOP"
            stop.write_text("")
            with self.assertRaises(InterruptedError):
                quiet.wait_quiet(6, path, checker=lambda: {"quiet": False, "reasons": []}, stop_file=stop,
                                 sleep=lambda s: None)

    def test_each_trial_records_its_load_and_only_other_programs_contend_it(self):
        with tempfile.TemporaryDirectory() as folder:
            sampler = quiet.Sampler(Path(folder) / "load.jsonl", threshold=6)
            # A one-shot's own final render: load 14, but nothing else ran.
            sampler.samples = [{"load": [2.0, 1, 1], "heavy": [], "other_cores": 0.4},
                               {"load": [14.0, 1, 1], "heavy": [], "other_cores": 0.6},
                               {"load": [3.0, 1, 1], "heavy": [], "other_cores": 0.5}]
            self.assertEqual(sampler.summary(), {"samples": 3, "median_1m": 3.0, "max_1m": 14.0,
                                                 "load_over_threshold_samples": 1, "max_other_cores": 0.6,
                                                 "contended_samples": 0, "unreadable_samples": 0,
                                                 "contended": False, "threshold": 6})
            sampler.samples.append({"load": [3.0, 1, 1], "heavy": [{"pid": 9, "name": "clang", "cpu": 99}],
                                    "other_cores": 1.0})
            sampler.samples.append({"load": [3.0, 1, 1], "heavy": [], "other_cores": 6.5})
            self.assertEqual(sampler.summary()["contended_samples"], 2)

    def test_the_cores_other_programs_use_exclude_the_run_and_idle_entries(self):
        rows = self.rows + [{"pid": 30, "ppid": 0, "cpu": 1500.0, "name": "Idle", "command": "Idle"}]
        self.assertEqual(quiet.other_cores(rows, own=(20,)), 3.82)

    def test_windows_load_is_busy_cores(self):
        with mock.patch.object(quiet.subprocess, "run", return_value=mock.Mock(stdout="25\n", returncode=0)), \
                mock.patch.object(quiet.os, "cpu_count", return_value=16):
            self.assertEqual(quiet.load("win32"), [4.0, 4.0, 4.0])


class RerunAndStopTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.config = config(self.folder)
        self.steps = {s["id"]: s for s in plan.campaign("macos")}

    def fake_unit(self, loads):
        """A bench step's trials finish at once; the first runs of each trial number see ``loads`` in turn."""
        loads = iter(loads)

        def unit(step, config, campaign_dir, folder, line, step_folder=None):
            trial = next(t for t in range(1, step["trials"] + 1)
                         if not all(bench.trial_done(folder, side, t) for side in step["sides"]))
            for side in step["sides"]:
                (folder / ("%s-trial-%02d" % (side, trial))).mkdir(parents=True, exist_ok=True)
                receipt = folder / ("%s-trial-%02d" % (side, trial)) / (
                    "trial.json" if side == "mcp" else "eval-live-agent.json")
                receipt.write_text(json.dumps({"trials": [{"tasks": [{}]}]}) if side == "angelo" else "{}")
            return 0, ["%s-%d" % (side, trial) for side in step["sides"]], {"contended": next(loads, False)}
        return unit

    def test_a_contended_trial_is_set_aside_and_run_again_once(self):
        step = dict(self.steps["h2h-sol-default"], trials=2)
        state = {"steps": {}}
        with mock.patch.object(runs, "run_unit", side_effect=self.fake_unit([True, True, False])):
            self.assertEqual(runs.run_step(step, self.config, self.folder, state), "done")
        kept = sorted(path.name.rsplit("-", 2)[0] for path in (self.folder / step["id"] / "contended").iterdir())
        self.assertEqual(kept, ["angelo-trial-01", "mcp-trial-01"], "Kept, never deleted; run again once only")
        record = state["steps"][step["id"]]
        self.assertTrue(record["trials"]["mcp-1"]["load"]["contended"], "The second contended run stays, marked")
        self.assertEqual(record["contended_reruns"], {"mcp-1,angelo-1": 1})

    def test_no_trial_starts_after_the_deadline_or_with_a_stop_file(self):
        step = self.steps["h2h-sol-default"]
        with mock.patch.object(runs, "run_unit") as unit:
            self.assertEqual(runs.run_step(step, self.config, self.folder, {"steps": {}}, deadline=0), "deadline")
            (self.folder / "STOP").write_text("")
            with self.assertRaises(InterruptedError):
                runs.run_step(step, self.config, self.folder, {"steps": {}})
        unit.assert_not_called()
        with mock.patch.object(runs, "gates_clear", return_value=True), \
                mock.patch.object(runs.quiet, "load", return_value=[1, 1, 1]):
            self.assertEqual(runs.run(self.config, self.folder, [step]), 5)
            (self.folder / "STOP").unlink()
            self.assertEqual(runs.run(self.config, self.folder, [step], deadline=0), 0)
        events = [json.loads(line)["event"] for line in (self.folder / runs.JOURNAL).read_text().splitlines()]
        self.assertIn("deadline", events)

    def test_a_start_refused_because_the_machine_got_busy_waits_rather_than_fails(self):
        step = self.steps["pilot-sol-batch_edits"]
        states = iter([{"quiet": False}, {"quiet": True}])
        with mock.patch.object(runs, "run_unit", return_value=(1, [], {"contended": False})) as unit, \
                mock.patch.object(runs.quiet, "check", side_effect=lambda threshold: next(states)):
            self.assertEqual(runs.run_step(step, self.config, self.folder, {"steps": {}}), "failed")
        self.assertEqual(unit.call_count, 2, "Once more after the busy refusal, then given up")

    def test_an_indicative_run_starts_on_a_busy_machine_and_records_the_load(self):
        step = self.steps["smoke-sol-gate7"]
        busy = {"quiet": False, "reasons": ["1-minute load 40.00 is above 6.0"], "load": [40, 30, 30], "heavy": []}
        process = mock.Mock(pid=4242, wait=mock.Mock(return_value=0))
        with mock.patch.object(runs.quiet, "wait_quiet") as wait, \
                mock.patch.object(runs.quiet, "check", return_value=busy), \
                mock.patch.object(runs.quiet, "Sampler") as sampler, \
                mock.patch.object(runs.subprocess, "Popen", return_value=process):
            sampler.return_value.__enter__.return_value.summary.return_value = {"contended": True}
            runs.run_unit(step, dict(self.config, ignore_load=True), self.folder, self.folder / "x", ["true"])
        wait.assert_not_called()
        entry = json.loads((self.folder / runs.JOURNAL).read_text().splitlines()[-1])
        self.assertEqual((entry["load_guard"], entry["at_start"]["load"]), ("ignored", [40, 30, 30]))

    def test_a_pilot_entry_is_counted_in_its_steps_folder(self):
        step = self.steps["pilot-sol-duplication"]
        folder, entry = self.folder / step["id"], runs.pilot_entries(step)[0]

        def finish():
            (folder / entry).mkdir(parents=True)
            (folder / entry / "SUPERVISION.json").write_text(json.dumps({"completed_entries": 1}))
            return 0
        process = mock.Mock(pid=4242, wait=mock.Mock(side_effect=finish))
        with mock.patch.object(runs.quiet, "wait_quiet", return_value=0), \
                mock.patch.object(runs.quiet, "check", return_value={"quiet": True, "load": [1, 1, 1]}), \
                mock.patch.object(runs.quiet, "Sampler") as sampler, \
                mock.patch.object(runs.subprocess, "Popen", return_value=process):
            sampler.return_value.__enter__.return_value.summary.return_value = {"contended": False}
            code, new, _load = runs.run_unit(step, self.config, self.folder, folder / entry, ["true"], step_folder=folder)
        self.assertEqual((code, new), (0, [entry]))

    def test_failed_steps_are_tried_again_only_when_asked(self):
        step = self.steps["gate-opus-default"]
        (self.folder / runs.STATE).write_text(json.dumps({"steps": {step["id"]: {"status": "failed", "trials": {}}},
                                                          "blocked": {}}))
        with mock.patch.object(runs, "run_step", return_value="done") as run_step, \
                mock.patch.object(runs.quiet, "load", return_value=[1, 1, 1]):
            runs.run(self.config, self.folder, [step])
            run_step.assert_not_called()
            runs.run(self.config, self.folder, [step], retry_failed=True)
            run_step.assert_called_once()

    def test_a_done_step_with_a_trial_set_aside_runs_again(self):
        step = self.steps["smoke-sol-gate7"]
        (self.folder / runs.STATE).write_text(json.dumps({"steps": {step["id"]: {"status": "done", "trials": {}}},
                                                          "blocked": {}}))
        with mock.patch.object(runs, "run_step", return_value="done") as run_step, \
                mock.patch.object(runs.quiet, "load", return_value=[1, 1, 1]):
            runs.run(self.config, self.folder, [step])
        run_step.assert_called_once()   # Its folder holds no finished trial.

    def test_a_trial_cap_shortens_every_step(self):
        (self.folder / runs.CONFIG).write_text(json.dumps(self.config))
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            runs.main(["plan", "--output", str(self.folder), "--only", "h2h-sol-oneshot-mini-cube", "--max-trials", "2"])
        row = json.loads(printed.getvalue())["steps"][0]
        self.assertEqual(row["trials"], 2)
        self.assertEqual(row["command"][row["command"].index("--trials") + 1], "2")

    def test_a_rehearsal_campaign_is_never_run_billed(self):
        (self.folder / runs.CONFIG).write_text(json.dumps(dict(self.config, rehearsal=True)))
        with mock.patch.object(runs.subprocess, "run", return_value=mock.Mock(stdout="")), \
                contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(runs.main(["run", "--output", str(self.folder), "--authorize-billed-runs"]), 2)
        self.assertIn("rehearsal", errors.getvalue())

    def test_a_step_without_a_connection_refuses_with_the_setting_to_give(self):
        bare = dict(self.config, connections={})
        step = dict(self.steps["gate-opus-default"], connection="")
        with self.assertRaises(SystemExit) as refused:
            runs.command(step, bare, self.folder / "x", billed=False)
        self.assertIn("ANGELO_FINAL_RUNS_CLAUDE_CONNECTION", str(refused.exception))
        for account, value in plan.TEST_CONNECTIONS.items():   # Only the environment names one: no default id.
            self.assertEqual(value, os.environ.get("ANGELO_FINAL_RUNS_%s_CONNECTION" % account.upper(), ""))

    def test_preflight_forms_and_a_new_machines_connections(self):
        windows = dict(self.config, connections={"claude": "c-win", "chatgpt": "g-win"})
        line = runs.command(self.steps["gate-opus-default"], windows, self.folder / "x", billed=False, preflight=True)
        self.assertIn("--preflight-only", line)
        self.assertEqual(line[line.index("--connection-id") + 1], "c-win")
        self.assertNotIn("--plan-only", line)


class SignInTests(unittest.TestCase):
    def store(self, records):
        return mock.patch("angelo_agent.connections.ConnectionStore",
                          return_value=mock.Mock(records={key: {"mode": mode} for key, mode in records.items()}))

    def test_one_subscription_of_each_kind_is_found_and_checked_by_existence(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.store({"a": "claude_subscription", "b": "codex_subscription", "c": "gemini_api"}):
                self.assertEqual(runs.discover_connections(folder), {"claude": "a", "chatgpt": "b"})
                self.assertFalse(runs.check_sign_in(folder, "darwin")["ready"])
                (Path(folder) / "claude-profile/a").mkdir(parents=True)
                (Path(folder) / "codex-profile/b").mkdir(parents=True)
                (Path(folder) / "codex-profile/b/auth.json").write_text("")
                self.assertTrue(runs.check_sign_in(folder, "darwin")["ready"], "macOS: Claude's sign-in is in the Keychain")
                self.assertFalse(runs.check_sign_in(folder, "win32")["ready"], "elsewhere an empty folder is no sign-in")
                self.assertTrue(runs.check_sign_in(folder, "win32", accounts=("chatgpt",))["ready"],
                                "a machine that runs only ChatGPT steps needs no Claude sign-in")
                (Path(folder) / "claude-profile/a/.credentials.json").write_text("")
                self.assertTrue(runs.check_sign_in(folder, "win32")["ready"])
            with self.store({"a": "claude_subscription", "d": "claude_subscription", "b": "codex_subscription"}):
                with self.assertRaises(SystemExit):
                    runs.discover_connections(folder)


class PrepareConnectionsTests(unittest.TestCase):
    """A new machine's bench store: one Claude and one ChatGPT subscription, own profiles, nothing signed in."""

    def test_two_isolated_connections_are_created_once_and_never_signed_in(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Path(folder) / "connections"
            with mock.patch.dict(os.environ, {"ANGELO_CONNECTIONS_DIR": str(store)}):
                prepared = runs.prepare_connections(store)
                again = runs.prepare_connections(store)
            self.assertEqual(prepared, again, "idempotent: the same two connections")
            from angelo_agent.connections import ConnectionStore
            records = ConnectionStore(store).records
            self.assertEqual(sorted(r["mode"] for r in records.values()), ["claude_subscription", "codex_subscription"])
            self.assertFalse(any(r["reuse_native"] for r in records.values()), "never the person's own sign-in")
            codex = Path(prepared["codex_profile"])
            self.assertEqual(codex, store / "codex-profile" / prepared["connections"]["chatgpt"])
            self.assertIn('cli_auth_credentials_store = "file"', (codex / "config.toml").read_text())
            self.assertFalse(Path(prepared["claude_profile"]).exists(), "Claude Code's sign-in creates its folder")
            self.assertFalse(runs.check_sign_in(store, "win32")["ready"])
            self.assertEqual(sorted(p.name for p in codex.iterdir()), ["config.toml"], "no credential is written")

    def test_a_profile_that_keeps_its_sign_in_elsewhere_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Path(folder) / "connections"
            prepared = runs.prepare_connections(store)
            (Path(prepared["codex_profile"]) / "config.toml").write_text('cli_auth_credentials_store = "keyring"\n')
            with self.assertRaises(SystemExit):
                runs.prepare_connections(store)


class McpRuntimeTests(unittest.TestCase):
    def runtime(self, root):
        root = Path(root)
        (root / "mcp_addon").mkdir(parents=True)
        (root / "mcp_addon/blender_manifest.toml").write_text("id = 'mcp'\n")
        (root / "mcp_server/blmcp/__pycache__").mkdir(parents=True)
        (root / "mcp_server/blmcp/__init__.py").write_text("def main(): pass\n")
        (root / "mcp_server/blmcp/__pycache__/x.pyc").write_text("")
        (root / "mcp-import-and-versions.json").write_text(json.dumps({
            "python": "3.14.0 (main) [Clang]", "dependencies": [["PyYAML", "6.0.3"], ["blender-mcp", "1.0.0"],
                                                                ["pydantic_core", "2.46.3"]]}))
        return root

    def test_the_bundle_carries_the_frozen_files_and_pins_and_installs_them_exactly(self):
        with tempfile.TemporaryDirectory() as folder:
            bundle = Path(folder) / "mcp.tar.gz"
            manifest = final_runs_mcp.bundle(self.runtime(Path(folder) / "runtime"), bundle)
            self.assertEqual(manifest["python"], "3.14.0")
            self.assertEqual(manifest["requirements"], ["PyYAML==6.0.3", "pydantic_core==2.46.3"])
            self.assertEqual(sorted(manifest["files"]), ["mcp_addon/blender_manifest.toml",
                                                         "mcp_server/blmcp/__init__.py"])
            calls = []
            run = self.uv(calls, ("PyYAML", "6.0.3"), ("pydantic_core", "2.46.3"), ("blender-mcp", "1.0.0"),
                          ("pywin32", "311"))
            installed = Path(folder) / "installed"
            facts = final_runs_mcp.install(bundle, installed, run=run)
            self.assertEqual(facts["missing"], [])
            self.assertEqual(facts["added"], ["pywin32==311"], "A platform-only dependency is recorded")
            self.assertEqual([argv[:3] for argv, _ in calls][0], ["uv", "venv", "--python"])
            self.assertEqual([argv[:3] for argv, _ in calls][-1], ["uv", "pip", "list"])
            self.assertEqual(facts["interpreter_dir"], str(installed / "python/cpython-3.14.0"))
            with self.assertRaises(FileExistsError):
                final_runs_mcp.install(bundle, installed, run=run)
            short = self.uv([], ("PyYAML", "6.0.2"))
            with self.assertRaises(RuntimeError):
                final_runs_mcp.install(bundle, Path(folder) / "other", run=short)
            self.assertFalse((Path(folder) / "other/INSTALLED.json").exists(), "a failed install is not marked done")
            self.assertTrue((Path(folder) / "other/INSTALL-FAILED.json").exists())

    def test_the_same_runtime_bundles_to_the_same_bytes(self):
        # Each send bundles again; a new digest each time meant a new install the campaign's CONFIG never named.
        with tempfile.TemporaryDirectory() as folder:
            runtime = self.runtime(Path(folder) / "runtime")
            final_runs_mcp.bundle(runtime, Path(folder) / "first.tar.gz")
            with mock.patch.object(final_runs_mcp.gzip.time, "time", return_value=2_000_000_000.0):
                final_runs_mcp.bundle(runtime, Path(folder) / "second.tar.gz")
            self.assertEqual((Path(folder) / "first.tar.gz").read_bytes(), (Path(folder) / "second.tar.gz").read_bytes())

    @staticmethod
    def uv(calls, *packages, home=None):
        """A stand-in for uv: ``venv`` writes the virtualenv's pyvenv.cfg (its interpreter in uv's install folder,
        or ``home``), and ``pip list`` names ``packages`` as uv lists them (the server by name and version)."""
        def run(argv, env=None, **kwargs):
            calls.append((argv, env))
            if argv[1] == "venv":
                venv = Path(argv[-1])
                venv.mkdir(parents=True)
                where = home or Path(env["UV_PYTHON_INSTALL_DIR"]) / "cpython-3.14.0"
                (venv / "pyvenv.cfg").write_text("home = %s\nversion_info = 3.14.0\n" % where)
            return mock.Mock(stdout=json.dumps([{"name": name, "version": version} for name, version in packages]))
        return run

    def test_the_runtime_brings_its_own_interpreter_so_any_account_that_reads_it_can_run_it(self):
        # The Windows stage installs as SYSTEM and the bench runs as its own user: uv's default interpreter folder
        # (SYSTEM's profile) was unreadable there, and the MCP server never started.
        packages = (("PyYAML", "6.0.3"), ("pydantic_core", "2.46.3"), ("blender-mcp", "1.0.0"))
        with tempfile.TemporaryDirectory() as folder:
            bundle = Path(folder) / "mcp.tar.gz"
            final_runs_mcp.bundle(self.runtime(Path(folder) / "runtime"), bundle)
            calls = []
            final_runs_mcp.install(bundle, Path(folder) / "installed", run=self.uv(calls, *packages))
            for argv, env in calls:
                with self.subTest(call=argv[:3]):
                    self.assertEqual(env["UV_PYTHON_INSTALL_DIR"], str(Path(folder) / "installed/python"))
                    self.assertEqual(env["UV_PYTHON_PREFERENCE"], "only-managed")
                    self.assertEqual(env["UV_LINK_MODE"], "copy", "no file shares the permissions of uv's cache")
            elsewhere = Path(folder) / "profile/uv/python/cpython-3.14.0"
            with self.assertRaisesRegex(RuntimeError, "outside the runtime"):
                final_runs_mcp.install(bundle, Path(folder) / "other", run=self.uv([], *packages, home=elsewhere))
            self.assertFalse((Path(folder) / "other/INSTALLED.json").exists())
            failed = json.loads((Path(folder) / "other/INSTALL-FAILED.json").read_text())
            self.assertEqual(failed["interpreter_dir"], str(elsewhere))
            self.assertEqual(final_runs_mcp.uv_environment("D", {"PATH": "p"}),
                             {"PATH": "p", "UV_PYTHON_INSTALL_DIR": str(Path("D") / "python"),
                              "UV_PYTHON_PREFERENCE": "only-managed", "UV_LINK_MODE": "copy"})


class OneshotSourcesTests(unittest.TestCase):
    """final_runs_sources: the tests' own SHA-256 pins are the manifest; a folder passes only with each picture."""

    def test_the_manifest_is_the_tests_pins(self):
        import final_runs_sources as sources
        import scenario_specs as specs
        pinned = {spec["file"]: spec["sha256"] for name in specs.ONESHOT_NAMES
                  for spec in getattr(specs.module(name), "SOURCE_IMAGES", {}).values()}
        self.assertEqual(sources.manifest(), pinned)
        self.assertIn("maintainer-reference-2026-10-11.png", pinned, "the tree and the cottage cut from it")

    def test_a_folder_passes_only_with_every_pinned_picture(self):
        import hashlib
        import final_runs_sources as sources
        pins = {"a.png": hashlib.sha256(b"a").hexdigest(), "b.png": hashlib.sha256(b"b").hexdigest()}
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / "a.png").write_bytes(b"a")
            (folder / "b.png").write_bytes(b"not b")
            (folder / "notes.txt").write_text("x")
            result = sources.check(folder, pins)
            self.assertEqual((result["ok"], result["missing"], result["mismatched"], result["unexpected"]),
                             (False, [], ["b.png"], ["notes.txt"]))
            (folder / "b.png").write_bytes(b"b")
            self.assertTrue(sources.check(folder, pins)["ok"])
            (folder / "a.png").unlink()
            self.assertEqual(sources.check(folder, pins)["missing"], ["a.png"])
        self.assertEqual(sources.check(Path(folder) / "gone", pins)["missing"], ["a.png", "b.png"])


class WindowsStageTests(unittest.TestCase):
    scripts = HERE.parents[2] / "scripts/angelo"

    def gcp(self, *argv, **env):
        import subprocess
        environment = {**os.environ, "ANGELO_GCP_PROJECT": "example-project", "ANGELO_GCP_ZONE": "example-zone-a",
                       "ANGELO_GCP_NAME": "example-vm", **env}
        return subprocess.run(["bash", str(self.scripts / "gcp_windows_validate.sh"), *argv], capture_output=True,
                              text=True, env=environment, timeout=120)

    @unittest.skipUnless(shutil.which("bash"), "The launcher is a bash script run from the workstation, not the VM")
    def test_the_project_zone_and_vm_name_are_required_settings(self):
        for name in ("ANGELO_GCP_PROJECT", "ANGELO_GCP_ZONE", "ANGELO_GCP_NAME"):
            with self.subTest(setting=name):
                refused = self.gcp("status", **{name: ""})
                self.assertNotEqual(refused.returncode, 0)
                self.assertIn("set " + name, refused.stderr)

    @unittest.skipUnless(shutil.which("bash"), "The launcher is a bash script run from the workstation, not the VM")
    def test_a_billed_session_needs_its_flag_and_a_dry_run_changes_nothing(self):
        refused = self.gcp("bench", "run")
        self.assertEqual(refused.returncode, 2)
        self.assertIn("--authorize-billed-runs", refused.stderr)
        with tempfile.TemporaryDirectory() as folder:
            runtime = McpRuntimeTests().runtime(Path(folder) / "runtime")
            shown = self.gcp("bench", "dry", ANGELO_GCP_DRY_RUN="1", ANGELO_BENCH_MCP_RUNTIME=str(runtime),
                             ANGELO_BENCH_CLAUDE_VERSION="2.1.293", ANGELO_BENCH_CODEX_VERSION="0.162.0")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn("DRY RUN: nothing is uploaded or started.", shown.stdout)
        for item in ("angelo-stages=bench", "angelo-bench-mode=dry", "angelo-bench-codex-version=0.162.0",
                     "confirm the VM is TERMINATED"):
            self.assertIn(item, shown.stdout)

    def test_the_vm_is_always_stopped_and_the_stage_never_competes_with_trials(self):
        gcp = (self.scripts / "gcp_windows_validate.sh").read_text()
        bench = gcp[gcp.index("bench() {"):gcp.index("down() {")]
        self.assertIn("wait_off || code=1", bench, "Waits for the run, then confirms the VM is off (stopping it)")
        self.assertIn("another run may be using it", bench)
        self.assertIn('source "$ROOT/scripts/angelo/gcp_windows_wait.sh"', gcp)
        waiting = (self.scripts / "gcp_windows_wait.sh").read_text()
        self.assertIn("SIGN-IN-READY", waiting[waiting.index("wait_off_run() {"):])
        boot = (self.scripts / "windows_cloud_bootstrap.ps1").read_text()
        stages = (self.scripts / "windows_bootstrap_stages.ps1").read_text()   # Dot-sourced from the sent tree.
        stage = stages[stages.index("function Invoke-BenchStage"):]
        for item in ("Stop-Job $syncer", "$benchExit -eq 194", "Stage-Mark \"bench\""):
            self.assertIn(item, stage)
        self.assertIn('(Join-Path $state "bench.done")', boot, "A new request runs the stage again")
        bench = (self.scripts / "windows_final_runs.ps1").read_text()
        self.assertIn("angelo-release-build.json", bench, "The candidate is the release stage's build of this commit")
        self.assertIn("if ($env:USERNAME -ne $benchUser -or", bench)
        self.assertIn("Remove-LocalUser -Name $benchUser", bench[bench.index("== session:"):])
        self.assertIn("--authorize-billed-runs", bench[bench.index('if ($Mode -eq "run")'):])
        self.assertNotIn("auth.json", bench, "The stage never names a credential file")


class WindowsBenchSetupTests(unittest.TestCase):
    """windows_bench_tools.ps1 and the stage's setup and sign-in, by contract (they run only on the Windows VM)."""

    scripts = HERE.parents[2] / "scripts/angelo"

    def text(self, name):
        return (self.scripts / name).read_text(encoding="utf-8")

    def test_each_tool_comes_from_its_publisher_and_is_checked(self):
        tools = self.text("windows_bench_tools.ps1")
        claude = tools[tools.index("function Install-ClaudeCode"):tools.index("function Install-StockCodex")]
        self.assertIn('"@anthropic-ai/claude-code@$Version"', claude)
        self.assertIn("reports '$reported', not $Version", claude)
        codex = tools[tools.index("function Install-StockCodex"):tools.index("function Install-StockBlender")]
        for item in ('"https://api.github.com/repos/openai/codex/releases/tags/$tag"', '"codex-package-x86_64-pc-windows-msvc.tar.gz"',
                     'if ($actual -ne $published)', '"codex-code-mode-host.exe"', '$signer -ne "OpenAI OpCo, LLC"'):
            self.assertIn(item, codex)
        self.assertLess(codex.index("if ($actual -ne $published)"), codex.index("tar.exe -xzf"), "checked before unpacking")
        msi = tools[tools.index("function Freeze-MsiCandidate"):]
        order = [msi.index(marker) for marker in ('"/i `"$Msi`"', '"tests\\python\\angelo_agent\\final_runs.py") freeze',
                                                   '"/x `"$Msi`"')]
        self.assertEqual(order, sorted(order), "installed, frozen, uninstalled again")
        self.assertIn("} finally {", msi, "uninstalled even when the freeze fails")
        for name in ("windows_bench_tools.ps1", "windows_final_runs.ps1"):
            self.assertNotIn("auth.json", self.text(name), "the scripts never name a credential file")
            self.assertNotRegex(self.text(name), r"Get-Content[^\n]*(credentials|auth)", "nor read one")

    def test_the_stage_and_setup_share_one_preparation_and_the_sign_in_uses_the_bench_profiles(self):
        stage = self.text("windows_final_runs.ps1")
        self.assertEqual(stage.count("Prepare-Bench $"), 2, "-Setup and -Stage")
        self.assertIn('$candidateMsi = Get-Meta "angelo-bench-candidate-msi" ""', stage)
        self.assertIn("    $candidateMsi = $local", stage, "an uploaded MSI is fetched before it is installed")
        sign_in = stage[stage.index("if ($SignIn) {"):stage.index("# ------------------------------------------------------------------ the measurement session")]
        order = [sign_in.index(marker) for marker in ("prepare-connections --connections-dir $connections",
                                                       "$env:CLAUDE_CONFIG_DIR = $prepared.claude_profile",
                                                       "& $facts.claude auth login",
                                                       "$env:CODEX_HOME = $prepared.codex_profile",
                                                       "& $facts.codex login --device-auth", "check-sign-in")]
        self.assertEqual(order, sorted(order))
        self.assertIn('$Accounts -contains "claude"', sign_in)
        self.assertIn('@("--account", $_)', sign_in, "a ChatGPT-only machine checks that account only")
        self.assertNotIn("Start-Process -FilePath $exe", sign_in, "no browser sign-in inside Angelo on the machine")
        for name, user in (("windows_final_runs.ps1", "$benchUser"), ("windows_bootstrap_stages.ps1", "$guiUser")):
            self.assertIn('Where-Object { $_.LocalPath -like "C:\\Users\\%s*" -and -not $_.Loaded }' % user, self.text(name),
                          "an earlier throwaway user's profile (its sign-ins) is removed")

    def test_the_vm_tree_is_its_commit_file_for_file(self):
        receive = self.text("source_send.py")
        receive = receive[receive.index("def receive("):receive.index("def confirmed(")]
        installed = receive.index("source_tree.install_commit(")
        proven = receive.index("source_tree.settle(tree, commit, real)", installed)
        receive.index('write(state, "confirmed.commit", commit)', proven)   # installed, made exact, proven, confirmed
        tool = self.text("source_tree.py")
        install = tool[tool.index('git(tree, "read-tree", commit)'):tool.index("def refusal")]
        self.assertIn('"checkout-index", "--all"', install, "files no archive created come from the commit")
        self.assertIn('{"GIT_LFS_SKIP_SMUDGE": "1"}', install)

    @unittest.skipUnless(shutil.which("bash"), "The launcher is a bash script run from the workstation, not the VM")
    def test_a_clone_gets_its_own_bucket_and_a_bench_send_carries_its_commit(self):
        shown = WindowsStageTests().gcp("clone", "bench-vm-2", ANGELO_GCP_NAME="bench-vm", ANGELO_GCP_DRY_RUN="1")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        for item in ("bucket gs://example-project-bench-vm-2", "objectAdmin for bench-vm-build@",
                     "from machine image bench-vm-bench-base", "angelo-bucket=example-project-bench-vm-2",
                     "TERMINATED"):
            self.assertIn(item, shown.stdout)
        gcp = self.text("gcp_windows_validate.sh")
        self.assertIn('send_source "$work" "$nonce" "$base"', gcp[gcp.index("redo() {"):gcp.index("status() {")],
                      "every send, a bench's included, carries its commit")
        bench = gcp[gcp.index("bench() {"):gcp.index("down() {")]
        self.assertIn("agent_runtime.json", bench, "stock Codex at the runtime's pinned version")
        self.assertIn("angelo-bench-candidate-msi=$candidate_meta", bench)


class FreezeAndRefusalTests(unittest.TestCase):
    def test_a_freeze_is_never_overwritten_and_a_changed_candidate_is_refused(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as stack:
            if os.name == "nt":
                # A Windows candidate is a build folder with angelo.exe. The stand-in cannot be a real program, so
                # only its --version run is answered here; the copy, inventory and checks are the real ones.
                app = Path(folder) / "Angelo"
                exe = app / "angelo.exe"
                exe.parent.mkdir(parents=True)
                exe.write_bytes(b"MZ stand-in")
                process = sys.modules[runs.freeze.__module__].subprocess
                real_run = process.run

                def answer(argv, *args, **kwargs):
                    if list(argv[1:]) == ["--factory-startup", "--version"]:
                        return mock.Mock(stdout="Blender 5.2.2 LTS\n")
                    return real_run(argv, *args, **kwargs)
                stack.enter_context(mock.patch.object(process, "run", side_effect=answer))
            else:
                app = Path(folder) / "Angelo.app"
                exe = app / "Contents/MacOS/Angelo"
                exe.parent.mkdir(parents=True)
                exe.write_text("#!/bin/sh\necho 'Blender 5.2.2 LTS'\n")
                exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
            receipt = runs.freeze(app, Path(folder) / "frozen")
            self.assertEqual(receipt["version"], ["Blender 5.2.2 LTS"])
            with self.assertRaises(FileExistsError):
                runs.freeze(app, Path(folder) / "frozen")
            candidate = {"candidate": str(Path(folder) / "frozen")}
            runs.verify_candidate(candidate, full=True)
            Path(receipt["executable"]).write_text("rebuilt")
            with self.assertRaises(RuntimeError):
                runs.verify_candidate(candidate)

    def test_run_refuses_without_authorization_or_from_a_dirty_checkout(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / runs.CONFIG).write_text(json.dumps(config(folder)))
            with contextlib.redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(runs.main(["run", "--output", folder]), 2)
            self.assertIn("--authorize-billed-runs", errors.getvalue())
            dirty = mock.Mock(stdout=" M tests/python/angelo_agent/final_runs.py\n")
            with mock.patch.object(runs.subprocess, "run", return_value=dirty), \
                    contextlib.redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(runs.main(["run", "--output", folder, "--authorize-billed-runs"]), 2)
            self.assertIn("uncommitted", errors.getvalue())

    def test_a_scripted_run_names_both_mcp_clients_whatever_its_steps_runtime(self):
        # It drives Claude Code and Codex; an Opus step names only claude, and on Windows the default codex is none
        # (a dry run's Codex side otherwise starts an empty program name).
        quiet = {"quiet": True, "reasons": [], "load": [0, 0, 0], "heavy": []}
        steps = [s for s in plan.campaign("windows") if s["id"] in ("h2h-opus-oneshot-mini-cube",
                                                                   "h2h-sol-oneshot-mini-cube")]
        for step in steps:
            with self.subTest(step=step["id"]), tempfile.TemporaryDirectory() as folder:
                settings = config(folder, platform="windows", codex="C:/bench/codex.exe", claude="C:/bench/claude.exe")
                with mock.patch.object(runs.quiet, "check", return_value=quiet), \
                        mock.patch.object(runs.subprocess, "run", return_value=mock.Mock(returncode=0)) as run, \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(runs.dry_run(settings, [step], Path(folder)), 0)
                scripted = [call.args[0] for call in run.call_args_list if "--dry-run-scenario" in call.args[0]]
                self.assertEqual(len(scripted), 1)
                line = scripted[0]
                for flag, value in (("--codex", "C:/bench/codex.exe"), ("--claude", "C:/bench/claude.exe")):
                    self.assertEqual(line.count(flag), 1, flag)
                    self.assertEqual(line[line.index(flag) + 1], value)
                billed = runs.command(step, settings, Path(folder) / "x")
                self.assertEqual(sum(flag in billed for flag in ("--codex", "--claude")), 1,
                                 "a billed step still names only its own client")

    def test_a_dry_run_on_a_busy_machine_is_refused_unless_asked(self):
        with tempfile.TemporaryDirectory() as folder:
            busy = {"quiet": False, "reasons": ["1-minute load 40.00 is above 6.0"], "load": [40, 40, 40], "heavy": []}
            with mock.patch.object(runs.quiet, "check", return_value=busy), mock.patch.object(runs.subprocess, "run") as run, \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(runs.dry_run(config(folder), plan.campaign("macos")[:1], Path(folder)), 2)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
