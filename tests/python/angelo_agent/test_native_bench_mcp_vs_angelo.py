# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Offline checks for the billed Blender MCP vs Angelo benchmark: no process, network or account."""

import ast
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

import bench_mcp_grading as grading  # noqa: E402
import bench_mcp_report as report  # noqa: E402
import bench_mcp_trial as trial  # noqa: E402
import bench_mcp_vs_angelo as bench  # noqa: E402
import eval_live_tasks as live  # noqa: E402
import gui_launch  # noqa: E402
import native_suite_manifest as manifest  # noqa: E402

CONNECTION = "11111111-1111-4111-8111-111111111111"


class GuardTests(unittest.TestCase):
    def run_main(self, argv):
        with mock.patch("subprocess.Popen") as popen, mock.patch("subprocess.run") as run, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as errors:
            code = bench.main(argv)
        return code, errors.getvalue(), popen, run

    def test_refuses_without_authorization_before_starting_anything(self):
        with tempfile.TemporaryDirectory() as folder:
            code, errors, popen, run = self.run_main(["--connection-id", CONNECTION, "--source-connections-dir", folder,
                                                      "--output", os.path.join(folder, "out")])
        self.assertEqual(code, 2)
        self.assertIn("--authorize-billed-runs", errors)
        popen.assert_not_called()
        run.assert_not_called()

    def test_refuses_a_non_empty_output_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "existing").write_text("x")
            code, errors, popen, _ = self.run_main(["--authorize-billed-runs", "--connection-id", CONNECTION,
                                                    "--source-connections-dir", folder, "--output", folder])
        self.assertEqual(code, 2)
        self.assertIn("--output must be new or empty", errors)
        popen.assert_not_called()

    def test_install_paths_default_only_on_macos_and_the_binary_comes_from_flag_or_env(self):
        self.assertEqual(bench.macos_default("/Applications/Blender.app/Contents/MacOS/Blender", platform="darwin"),
                         "/Applications/Blender.app/Contents/MacOS/Blender")
        for platform in ("win32", "linux"):
            self.assertEqual(bench.macos_default("/Applications/Blender.app", platform=platform), "")
        with mock.patch.dict(os.environ, {"ANGELO_BINARY": "/builds/Angelo"}):
            self.assertEqual(bench.parse([]).angelo_binary, "/builds/Angelo")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(bench.parse([]).angelo_binary, "")

    def test_missing_defaults_are_refused_before_starting_anything(self):
        with tempfile.TemporaryDirectory() as folder:
            code, errors, popen, run = self.run_main([
                "--authorize-billed-runs", "--connection-id", CONNECTION, "--source-connections-dir", folder,
                "--output", os.path.join(folder, "out"), "--angelo-binary", "", "--stock-blender", "",
                "--mcp-server-dir", "", "--mcp-addon-dir", ""])
        self.assertEqual(code, 2)
        for flag in ("--angelo-binary", "--stock-blender", "--mcp-server-dir", "--mcp-addon-dir"):
            self.assertIn(flag + " is required here", errors)
        popen.assert_not_called()
        run.assert_not_called()

    def test_plan_counts_billed_prompts_and_alternates(self):
        planned = bench.plan(bench.parse(["--trials", "2"]))
        self.assertEqual(planned["order"], ["mcp-1", "angelo-1", "mcp-2", "angelo-2"])
        self.assertEqual(planned["billed_prompts"], {"mcp": 10, "angelo": 12})
        self.assertNotIn("preview_accept", planned["mcp_tasks"])

    def test_angelo_cache_ttl_is_planned_and_the_delegation_set_is_refused(self):
        self.assertEqual(bench.plan(bench.parse(["--angelo-cache-ttl", "5m"]))["angelo"]["cache_ttl"], "5m")
        self.assertEqual(bench.plan(bench.parse([]))["angelo"]["cache_ttl"], "product default")
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            bench.parse(["--task-set", "delegation"])  # No MCP equivalent: run eval_live_agent.py.

    def test_maintainer_port_is_never_used(self):
        for port in (9876, 8000, 40000):
            with self.subTest(port=port), self.assertRaises(ValueError):
                trial.AddonClient(port)
        port = trial.free_port()
        self.assertTrue(39000 <= port < 40000)

    def test_clean_environment_drops_routing_and_session_variables(self):
        inherited = {"PATH": "/usr/bin", "HOME": "/Users/x", "ANTHROPIC_BASE_URL": "http://proxy", "CLAUDECODE": "1",
                     "CLAUDE_CODE_SESSION_ID": "s", "CLAUDE_CODE_OAUTH_TOKEN": "t", "USE_STAGING_OAUTH": "1"}
        with mock.patch.dict(os.environ, inherited, clear=True):
            environment = trial.clean_environment(TMPDIR="/tmp/x")
        self.assertEqual(environment, {"PATH": "/usr/bin", "HOME": "/Users/x", "DISABLE_AUTOUPDATER": "1",
                                       "CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK": "1", "TMPDIR": "/tmp/x"})

    @unittest.skipIf(sys.platform == "win32", "The comparison bench runs Blender MCP from a POSIX virtual "
                     "environment (.venv/bin); it is a macOS benchmark")
    def test_claude_command_preallows_only_the_blender_server(self):
        command = trial.claude_command({"claude": "claude", "model": "m", "effort": "low"}, "/tmp/mcp.json")
        joined = " ".join(command)
        for fragment in ("--allowedTools mcp__blender", "--strict-mcp-config", "--model m", "--effort low",
                         "--permission-prompts none", "--input-format stream-json", '{"fastMode": false}'):
            self.assertIn(fragment, joined)
        self.assertNotIn("--dangerously-skip-permissions", joined)
        server = trial.mcp_config({"mcp_server_dir": "/ext", "stock_blender": "/b"}, 39001)["mcpServers"]["blender"]
        self.assertEqual(server["env"]["BLENDER_MCP_PORT"], "39001")
        self.assertTrue(server["command"].endswith(".venv/bin/blender-mcp"))


class GradingTests(unittest.TestCase):
    def test_helpers_are_the_session_source_verbatim(self):
        session = grading.SESSION_SCRIPT.read_text(encoding="utf-8")
        source = grading.session_helpers_source()
        for name in grading.SESSION_HELPERS:
            self.assertIn("def %s(" % name, source)
        for block in source.split("\n\n\n"):
            self.assertIn(block, session)

    def test_missing_helper_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "session.py")
            path.write_text("def scene_digest(scene):\n    return []\n")
            with self.assertRaises(RuntimeError):
                grading.session_helpers_source(path)

    def test_grading_code_compiles_for_every_head_to_head_task(self):
        ast.parse(grading.snapshot_code())
        for task in live.build_tasks():
            if task["kind"] != "preview":
                with self.subTest(task=task["id"]):
                    code = grading.grade_code(task["id"], [["Cube", "MESH", [0, 0, 0], [2, 2, 2]]])
                    ast.parse(code)
                    self.assertIn("verify(VerificationContext", code)


def stream_events():
    return [
        (0.0, {"type": "system", "subtype": "init", "model": "claude-opus-5-5",
               "mcp_servers": [{"name": "blender", "status": "connected"}]}),
        (1.0, {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta"}}}),
        (2.0, {"type": "assistant", "message": {"id": "a", "content": [{"type": "text", "text": "Looking"}]}}),
        (2.5, {"type": "assistant", "message": {"id": "a", "content": [
            {"type": "tool_use", "id": "t1", "name": "mcp__blender__execute_blender_code", "input": {"code": "x"}}]}}),
        (4.0, {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": [
            {"type": "text", "text": json.dumps({"status": "error", "message": "boom"})}]}]}}),
        (6.0, {"type": "assistant", "message": {"id": "b", "content": [{"type": "text", "text": "Done"}]}}),
        (6.5, {"type": "result", "subtype": "success", "num_turns": 2}),
    ]


class ReportTests(unittest.TestCase):
    def test_stream_analysis_counts_distinct_api_calls_and_tool_round_trips(self):
        analysis = report.analyze_mcp_events(stream_events(), start=0.0)
        self.assertEqual(analysis["api_calls"], 2)
        self.assertEqual(analysis["first_text_ms"], 1000.0)
        self.assertEqual(analysis["first_tool_call_ms"], 2500.0)
        self.assertEqual(analysis["tool_call_count"], 1)
        self.assertEqual(analysis["tool_calls"][0]["round_trip_ms"], 1500.0)
        self.assertEqual(analysis["tool_errors"], 1)
        self.assertEqual(analysis["init"]["mcp_servers"][0]["status"], "connected")

    def test_angelo_transcript_steps_follow_each_prompt(self):
        tasks = live.build_tasks()
        lines = [{"type": "user", "message": {"content": tasks[1]["prompt"] + "\n\nCurrent native scene observation"}},
                 {"type": "assistant", "message": {"id": "m1", "model": "claude-opus-5-5", "content": [
                     {"type": "tool_use", "name": "StructuredOutput",
                      "input": {"operations": [{"tool": "angelo_scene_program"}]}}]}},
                 {"type": "user", "message": {"content": "Host confirms these native operations completed."}},
                 {"type": "assistant", "message": {"id": "m2", "model": "claude-opus-5-5", "content": [
                     {"type": "tool_use", "name": "StructuredOutput", "input": {"operations": []}}]}}]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "t.jsonl")
            path.write_text("\n".join(json.dumps(line) for line in lines))
            steps = report.angelo_transcript_steps(path, tasks)
        self.assertEqual(steps["cube_creation"], {"api_calls": 2, "plans": [["angelo_scene_program"], []],
                                                  "models": ["claude-opus-5-5"],
                                                  "tool_names": ["angelo_scene_program"]})

    def test_repeated_prompts_are_attributed_in_task_order(self):
        tasks = live.tasks_for("long", 12)
        questions = [task for task in tasks if task["id"].startswith("question_")]
        self.assertEqual(len({task["prompt"] for task in questions}), 1)
        lines = []
        for index, task in enumerate(tasks):
            lines.append({"type": "user", "message": {"content": task["prompt"] + "\n\nObservation delta"}})
            lines.append({"type": "assistant", "message": {"id": "m%d" % index, "model": "m", "content": []}})
        lines.append({"type": "assistant", "message": {"id": "extra", "model": "m", "content": []}})
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "t.jsonl")
            path.write_text("\n".join(json.dumps(line) for line in lines))
            steps = report.angelo_transcript_steps(path, tasks)
        self.assertEqual(sorted(steps), sorted(task["id"] for task in tasks))
        self.assertEqual([steps[task["id"]]["api_calls"] for task in questions], [1, 2])

    def test_isolation_and_after_failure_reach_both_sides(self):
        args = bench.parse(["--authorize-billed-runs", "--connection-id", CONNECTION, "--output", "/nonexistent/x",
                            "--after-failure", "reset"])
        self.assertEqual((bench.plan(args)["input_isolation"], bench.plan(args)["after_failure"]), ("on", "reset"))
        config = bench.mcp_config(args, Path("/private/tmp/out"))
        self.assertEqual((config["input_isolation"], config["after_failure"]), ("on", "reset"))
        compile(grading.reset_code("transform"), "<reset>", "exec")

    def test_window_states_are_recorded_and_differences_never_silent(self):
        darwin = mock.patch.object(gui_launch.sys, "platform", "darwin")
        with darwin:
            behind = bench.parse(["--output", "/nonexistent/x"])
            front = bench.parse(["--output", "/nonexistent/x", "--window-state", "front"])
            self.assertTrue(bench.plan(behind)["window_state"]["mcp"].startswith("front"))
            self.assertIn("not comparable", bench.window_asymmetry(behind))
            self.assertEqual(bench.window_asymmetry(front), "")
            self.assertEqual(bench.mcp_config(front, Path("/private/tmp/out"))["window_state"], "front")
            self.assertEqual(bench.window_asymmetry(bench.parse(["--sides", "angelo"])), "")
            with tempfile.TemporaryDirectory() as folder:
                # Reused MCP results keep their run's recorded state; a run recorded before it was planned says so.
                plan_file = Path(folder, bench.RUN_PLAN)
                plan_file.write_text(json.dumps({"window_state": {"mcp": "behind: recorded", "angelo": "x"}}))
                reuse = bench.parse(["--sides", "angelo", "--window-state", "front", "--reuse-mcp-results", folder])
                self.assertEqual(bench.plan(reuse)["window_state"]["mcp"], "behind: recorded")
                self.assertIn("not comparable", bench.window_asymmetry(reuse))
                plan_file.write_text(json.dumps({}))
                again = bench.parse(["--summarize-only", folder])
                self.assertEqual(bench.plan(again)["window_state"], "not recorded")

    def test_summary_keeps_failed_runs_in_timing_and_counts_them_as_failures(self):
        records = [{"side": "mcp", "trial": n, "task": "transform", "status": "completed", "wall_ms": ms,
                    "host_check_passed": passed, "api_calls": 3}
                   for n, ms, passed in ((1, 1000., True), (2, 3000., False), (3, 2000., True))]
        records.append({"side": "mcp", "trial": 4, "task": "transform", "status": "skipped"})
        stats = report.summarize(records)["tasks"]["transform"]["mcp"]
        self.assertEqual((stats["runs"], stats["passed"], stats["skipped"], stats["pass_rate"]), (4, 2, 1, .5))
        self.assertEqual((stats["wall_ms"]["n"], stats["wall_ms"]["median"]), (3, 2000.))
        self.assertEqual(stats["wall_ms_passed_only"]["median"], 1500.)

    def test_manifest_never_runs_the_benchmark_by_default(self):
        self.assertIn("bench_mcp_vs_angelo.py", manifest.EXCLUDED)
        self.assertIn("bench_mcp_blender_startup.py", manifest.EXCLUDED)
        for helper in ("bench_mcp_trial.py", "bench_mcp_grading.py", "bench_mcp_report.py"):
            self.assertIn(helper, manifest.HELPERS)


if __name__ == "__main__":
    unittest.main()
