# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The one-shot tests in the launchers and drivers: task sets, time and cost caps, plan, commands.

Pins what the one-shot format must leave alone: the billed gates' and the
scenario sets' time cap, launch commands and Claude Code command are what
they were, and nothing of a one-shot test reaches them.
"""

import contextlib
import io
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

import bench_mcp_scenario as mcp_scenario  # noqa: E402
import bench_mcp_trial as mcp_trial  # noqa: E402
import bench_mcp_vs_angelo as bench  # noqa: E402
import eval_live_agent  # noqa: E402
import eval_live_tasks as live  # noqa: E402
import oneshot_mcp  # noqa: E402
import scenario_oneshot_footbridge as footbridge  # noqa: E402
import scenario_session  # noqa: E402
import scenario_specs as specs  # noqa: E402

CONNECTION = "11111111-1111-4111-8111-111111111111"


class HarnessWiringTests(unittest.TestCase):
    def test_live_task_sets_include_the_one_shot_tests(self):
        for task_set in specs.ONESHOT_TASK_SETS:
            self.assertIn(task_set, live.TASK_SETS)
            self.assertEqual(live.tasks_for(task_set), specs.tasks(specs.scenario_id(task_set)))
            self.assertEqual(len(live.tasks_for(task_set)), 1)
        self.assertEqual(live.TASK_SETS[:5], ("default", "long", "delegation", "coverage", "survey"))

    def test_gate_sets_keep_their_time_cap_and_commands(self):
        default = bench.parse(["--connection-id", CONNECTION])
        self.assertEqual((default.task_timeout, eval_live_agent.parse([]).task_timeout), (300.0, 300.0))
        for parse, flag in ((bench.parse, "--task-timeout"), (eval_live_agent.parse, "--task-timeout")):
            for task_set in ("default", "scenario-bridge"):
                with self.subTest(task_set=task_set), contextlib.redirect_stderr(io.StringIO()), \
                        self.assertRaises(SystemExit):
                    parse(["--task-set", task_set, flag, "1801"])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            bench.parse(["--cost-cap", "5"])                     # One-shot tests only.
        command = mcp_trial.claude_command({"claude": "claude", "model": "m", "effort": "low"}, "/c.json", ["a"])
        self.assertEqual(command[-3:], ["--permission-prompts", "none", "--no-session-persistence"])
        self.assertEqual(command[command.index("--allowedTools") + 1], "mcp__blender,mcp__blender__a")
        for task_set in ("default", "long", "scenario-bridge", "scenario-island"):
            self.assertIs(mcp_scenario.client_command(command, {"task_set": task_set}, Path("/tmp/x")), command)
        stream, output = object(), {}
        mcp_scenario.guard(stream, {"task_set": "long"}, Path("/tmp/x"), output)   # Untouched: no wrapper, no state.
        self.assertEqual(output, {})

    def test_a_one_shot_test_takes_its_own_time_cap(self):
        for task_set in specs.ONESHOT_TASK_SETS:
            wall = specs.limits(specs.scenario_id(task_set))["wall_s"]
            self.assertEqual(bench.parse(["--task-set", task_set]).task_timeout, wall)
            self.assertEqual(eval_live_agent.parse(["--task-set", task_set]).task_timeout, wall)
            self.assertEqual(specs.wall_cap(task_set), wall)
        self.assertEqual(specs.wall_cap("default"), 0)
        self.assertEqual(bench.parse(["--task-set", "oneshot-bridge", "--task-timeout", "7200"]).task_timeout, 7200)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            bench.parse(["--task-set", "oneshot-bridge", "--task-timeout", str(specs.ONESHOT_MAX_WALL_S + 1)])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            bench.parse(["--task-set", "oneshot-bridge", "--after-failure", "reset"])

    def test_plan_is_one_message_per_side_with_limits_and_allowances(self):
        args = bench.parse(["--task-set", "oneshot-footbridge", "--connection-id", CONNECTION, "--runtime", "codex",
                            "--model", "gpt-6-astra", "--trials", "1"])
        plan = bench.plan(args)
        self.assertEqual(plan["billed_prompts"], {"mcp": 1, "angelo": 1})
        self.assertEqual((plan["mcp_tasks"], plan["angelo_tasks"]), (["brief"], ["brief"]))
        oneshot = plan["oneshot"]
        self.assertEqual(oneshot["messages_per_side"], 1)
        self.assertEqual(oneshot["limits"], {"wall_s": footbridge.LIMITS["wall_s"],
                                             "job_wait_s": footbridge.LIMITS["job_wait_s"],
                                             "cost_usd": footbridge.LIMITS["cost_usd"]})
        self.assertEqual(oneshot["other_side_client"], oneshot_mcp.ALLOWANCES["codex"])
        self.assertEqual(plan["scenario"]["oneshot"]["final_still"], footbridge.FINAL_STILL)
        capped = bench.plan(bench.parse(["--task-set", "oneshot-footbridge", "--cost-cap", "3.5", "--task-timeout", "900"]))
        self.assertEqual(capped["oneshot"]["limits"], {"wall_s": 900.0, "job_wait_s": 900.0, "cost_usd": 3.5})
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(bench.main(["--task-set", "oneshot-bridge", "--plan-only"]), 0)
        self.assertIn('"messages_per_side": 1', printed.getvalue())
        self.assertNotIn("oneshot", bench.plan(bench.parse(["--task-set", "scenario-bridge"])))

    def angelo_command(self, argv):
        args = bench.parse(["--authorize-billed-runs", "--connection-id", CONNECTION, *argv])
        args.source_connections_dir = "/nonexistent"
        with tempfile.TemporaryDirectory() as folder, mock.patch("subprocess.run") as run:
            run.return_value.returncode = 0
            bench.run_angelo_trial(args, Path(folder), 1)
        return run.call_args.args[0], run.call_args.kwargs["timeout"]

    def test_angelo_launch_command_and_environment(self):
        default, _ = self.angelo_command([])
        self.assertNotIn("--cost-cap", default)                  # The billed gates' command is unchanged.
        scenario, _ = self.angelo_command(["--task-set", "scenario-bridge"])
        self.assertNotIn("--cost-cap", scenario)
        command, timeout = self.angelo_command(["--task-set", "oneshot-footbridge", "--cost-cap", "4"])
        self.assertEqual(command[command.index("--cost-cap") + 1], "4")
        self.assertEqual(command[command.index("--task-timeout") + 1], "%g" % footbridge.LIMITS["wall_s"])
        self.assertEqual(command[command.index("--python-approvals") + 1], "approve")
        wall, wait = footbridge.LIMITS["wall_s"], footbridge.LIMITS["job_wait_s"]
        self.assertEqual(timeout, 1 * (2 * wall + 60) + 300 + wait)
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            args = eval_live_agent.parse(["--task-set", "oneshot-footbridge", "--cost-cap", "4"])
            environment = eval_live_agent.trial_environment(folder, folder, args, 1)
            plain = eval_live_agent.trial_environment(folder, folder, eval_live_agent.parse([]), 1)
        self.assertEqual(environment["ANGELO_EVAL_COST_CAP"], "4")
        self.assertEqual(environment["ANGELO_EVAL_TASK_SET"], "oneshot-footbridge")
        self.assertNotIn("ANGELO_EVAL_COST_CAP", {name for name in plain if name not in os.environ})
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            eval_live_agent.parse(["--cost-cap", "4"])

    def test_the_trial_session_and_the_mcp_steps_are_the_one_shot_ones(self):
        import oneshot_session
        session = scenario_session.for_trial("oneshot-footbridge", Path("/tmp"))
        self.assertIsInstance(session, oneshot_session.OneShotSession)
        self.assertEqual(session.job_wait_s, footbridge.LIMITS["job_wait_s"])
        self.assertNotIsInstance(scenario_session.for_trial("scenario-bridge", Path("/tmp")),
                                 oneshot_session.OneShotSession)
        self.assertIsNone(scenario_session.for_trial("default", Path("/tmp")))
        folder = Path("/tmp/run/mcp-trial-01")
        plain = mcp_trial.claude_command({"claude": "claude", "model": "m", "effort": "low"}, "/c.json", ["a"])
        command = mcp_scenario.client_command(plain, {"task_set": "oneshot-bridge"}, folder)
        self.assertEqual(command[-2:], ["--add-dir", str(folder)])
        allowed = command[command.index("--allowedTools") + 1].split(",")
        self.assertEqual(allowed[:2], ["mcp__blender", "mcp__blender__a"])
        self.assertIn("Read(//**/*.png)", allowed)                # Image files anywhere; "//" is the filesystem root.
        self.assertTrue(all(rule.startswith("Read(//**/*.") for rule in allowed[2:]))
        self.assertFalse({"Bash", "Write", "Edit", "WebFetch", "Read"} & set(allowed))   # Nothing wider is granted.
        self.assertEqual(len(plain), len(command) - 2)

    def test_mcp_grading_code_compiles_for_the_one_shot_names(self):
        import ast
        import bench_mcp_grading as grading
        for task_set in specs.ONESHOT_TASK_SETS:
            code = grading.scenario_grade_code("brief", task_set)
            ast.parse(code)
            self.assertIn("scenario_specs.module(scenario_specs.scenario_id(%r))" % task_set, code)
        ast.parse(oneshot_mcp.RENDER_PATH_CODE)
        self.assertIsNotNone(json.dumps(oneshot_mcp.ALLOWANCES))


if __name__ == "__main__":
    unittest.main()
