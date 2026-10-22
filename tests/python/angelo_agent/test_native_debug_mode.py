# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Debug mode (doc/angelo/debug_mode.rst): the switch, its launch argument, where the summaries land, and the marks.

Unbilled fixtures only. The stream events are the Messages API's shapes as Claude Code forwards them; they prove
what the host does with summarized thinking, not that a live runtime sends it (the flag's effect on a live
Claude Code is in the document).
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

import bench_cold_warm  # noqa: E402
import bench_mcp_gate  # noqa: E402
import bench_mcp_report  # noqa: E402
import bench_mcp_trial  # noqa: E402
import bench_mcp_vs_angelo as bench  # noqa: E402
import eval_diagnostics as diagnostics  # noqa: E402
import eval_live_agent  # noqa: E402
import measured_results  # noqa: E402
from angelo_agent import debug_thinking  # noqa: E402
from angelo_agent.claude_stream import RoundStream  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.providers import ClaudeProvider, claude_session  # noqa: E402

CONNECTION = "22222222-2222-4222-8222-222222222222"
SUMMARY = "The brief says warm; the front picture is blue-grey. I will follow the picture."


def context(debug=None, provider="claude_subscription"):
    fields = {"turn_options": None, "state": {"provider": provider, "options": {}}}
    if debug is not None:
        fields["debug_thinking"] = debug
    return SimpleNamespace(**fields)


def stream(event):
    return {"type": "stream_event", "event": event, "session_id": "fixture-native"}


class LaunchTests(unittest.TestCase):
    @mock.patch.object(debug_thinking, "SHOWN_TO_EVERYONE", True)   # The display switched on (1.0.4 ships it off).
    def test_every_chat_asks_for_the_summarized_display_and_debug_mode_adds_nothing_more(self):
        plain, _ = claude_session("claude", context())
        self.assertEqual(plain[plain.index("--thinking-display") + 1], "summarized")
        self.assertEqual(claude_session("claude", context(True))[0], plain)

    @mock.patch.object(debug_thinking, "SHOWN_TO_EVERYONE", False)   # Debug mode's own effect, with the display off.
    def test_debug_mode_adds_only_the_summarized_thinking_display(self):
        plain, _ = claude_session("claude", context())
        for debug in (False, None, "yes", 1):   # Only a real True turns it on.
            self.assertEqual(claude_session("claude", context(debug))[0], plain)
        command, _ = claude_session("claude", context(True), native_id="native-1")
        self.assertEqual(command[command.index("--thinking-display") + 1], "summarized")
        self.assertEqual([part for part in command if part not in ("--thinking-display", "summarized")],
                         plain + ["--resume=native-1"])
        self.assertEqual(command[-1], "--resume=native-1", "a resumed session still names its ID last")
        settings = json.loads(command[command.index("--settings") + 1])
        self.assertEqual(settings, {"fastMode": False, "hooks": {}}, "the settings stay the cached launch's")

    def test_a_change_of_debug_mode_relaunches_the_session(self):
        binding = lambda debug: ClaudeProvider._runtime_binding(None, SimpleNamespace(
            turn_options={"model": "", "effort": "low", "fast": False}, debug_thinking=debug))
        self.assertNotEqual(binding(False), binding(True))
        self.assertEqual(binding(False), ClaudeProvider._runtime_binding(None, SimpleNamespace(
            turn_options={"model": "", "effort": "low", "fast": False})))

    def test_the_controller_hands_it_to_each_request_and_never_saves_it(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = Controller(folder)
            self.addCleanup(controller.close)
            self.assertIs(controller.debug_thinking, False)
            self.assertFalse(debug_thinking.enabled(controller.runtime_context()))
            controller.debug_thinking = True
            self.assertTrue(debug_thinking.enabled(controller.runtime_context()))
            controller.save()
            self.assertNotIn(b"debug_thinking", controller.store.path.read_bytes())


class SummaryRecordingTests(unittest.TestCase):
    """Summarized thinking takes the existing thinking-summary path into the chat and its journal."""

    def test_a_summarized_thinking_block_becomes_a_journaled_thinking_row(self):
        events, round_stream = [], RoundStream()
        for event in (stream({"type": "message_start", "message": {"id": "msg_1", "usage": {"input_tokens": 3}}}),
                      stream({"type": "content_block_start", "index": 0,
                              "content_block": {"type": "thinking", "thinking": ""}}),
                      stream({"type": "content_block_delta", "index": 0,
                              "delta": {"type": "thinking_delta", "thinking": SUMMARY}}),
                      stream({"type": "content_block_delta", "index": 0,
                              "delta": {"type": "signature_delta", "signature": "c2ln"}}),
                      stream({"type": "content_block_stop", "index": 0})):
            events += round_stream.events(event, {})
        kinds = [kind for kind, _ in events if kind.startswith("thinking")]
        self.assertEqual(kinds, ["thinking_started", "thinking", "thinking_completed"])
        with tempfile.TemporaryDirectory() as folder:
            controller = Controller(folder)
            self.addCleanup(controller.close)
            controller.set_document(("debug-mode", 1))
            for kind, data in events:
                if kind.startswith("thinking"):
                    controller.emit(kind, data)
            controller.emit("delta", {"item": "a1", "text": "Done."})   # A durable event saves the row.
            controller._last_save = 0
            controller.pump_events()
            controller.save()
            row = next(row for row in controller.state["messages"] if row["role"] == "thinking")
            self.assertEqual((row["text"], row["status"]), (SUMMARY, "completed"))
            self.assertIn(SUMMARY.encode(), controller.store.path.read_bytes())


class HarnessFlagTests(unittest.TestCase):
    def setUp(self):
        from test_native_addon_guard import matching_app
        matching_app(self)   # The fixture app bundles no add-on; the bench's add-on pin has its own tests.

    def test_ordinary_runs_keep_their_plan_command_and_environment(self):
        args = bench.parse(["--connection-id", CONNECTION, "--task-set", "oneshot-bridge"])
        planned = bench.plan(args)
        self.assertNotIn("diagnostics", planned, "a resumed ordinary run's plan is unchanged")
        self.assertEqual(planned["oneshot"]["follow_ups"], "none: the harness never types again")
        self.assertNotIn("debug_thinking", bench.mcp_config(args, Path("/tmp/out")))
        self.assertNotIn("--thinking-display", bench_mcp_trial.claude_command(
            {"claude": "claude", "model": "m", "effort": "low"}, Path("/tmp/mcp.json")))
        live = eval_live_agent.parse(["--connection-id", CONNECTION])
        self.assertNotIn("diagnostics", json.dumps(diagnostics.plan(live)))
        environment = eval_live_agent.trial_environment(Path("/tmp/trial"), Path("/tmp/accounts"), live, 1)
        self.assertEqual((environment[diagnostics.DEBUG_ENV], environment[diagnostics.ASK_ENV]), ("", ""))
        self.assertEqual(diagnostics.extra_seconds(live), 0)

    def test_the_bench_plans_both_flags_and_hands_them_to_both_sides(self):
        args = bench.parse(["--connection-id", CONNECTION, "--task-set", "oneshot-bridge", "--debug-thinking",
                            "--ask-why-on-miss"])
        entry = bench.plan(args)["diagnostics"]
        self.assertEqual((entry["debug_thinking"], entry["ask_why_on_miss"]), (True, True))
        self.assertIn("not a timing or benchmark claim", entry["claim"])
        self.assertEqual(entry["mcp_ask_why"], "asked in the same Claude Code session")
        self.assertIn("one diagnostic question", bench.plan(args)["oneshot"]["follow_ups"])
        config = bench.mcp_config(args, Path("/tmp/out"))
        command = bench_mcp_trial.claude_command(dict(config, claude="claude"), Path("/tmp/mcp.json"))
        self.assertEqual(command[command.index("--thinking-display") + 1], "summarized")
        with tempfile.TemporaryDirectory() as folder:
            args.output, args.source_connections_dir, args.angelo_binary = folder, folder, "/app/Angelo"
            args.claude_config_dir = folder
            with mock.patch("subprocess.run", return_value=SimpleNamespace(returncode=0)) as run:
                bench.run_angelo_trial(args, Path(folder), 1)
        command = run.call_args.args[0]
        self.assertIn("--debug-thinking", command)
        self.assertIn("--ask-why-on-miss", command)
        only_angelo = bench.plan(bench.parse(["--sides", "angelo", "--ask-why-on-miss"]))["diagnostics"]
        self.assertNotIn("mcp_ask_why", only_angelo)

    def test_codex_runs_ask_both_sides_for_reasoning_summaries_and_say_the_mcp_side_is_not_asked(self):
        import bench_mcp_codex
        args = bench.parse(["--runtime", "codex", "--debug-thinking"])
        self.assertTrue(bench.plan(args)["diagnostics"]["debug_thinking"])
        config = bench.mcp_config(args, Path("/tmp/out"))
        self.assertEqual(bench_mcp_codex.reasoning_summary(config), {"summary": "auto"})
        self.assertEqual(bench_mcp_codex.reasoning_summary(bench.mcp_config(bench.parse(["--runtime", "codex"]),
                                                                            Path("/tmp/out"))), {})
        source = (HERE.parents[2] / "scripts/addons_core/angelo_agent/providers.py").read_text()
        self.assertIn('"summary": "auto"', source, "Angelo's chats ask for the same summaries on every turn")
        entry = bench.plan(bench.parse(["--runtime", "codex", "--ask-why-on-miss"]))["diagnostics"]
        self.assertTrue(entry["mcp_ask_why"].startswith("not asked"))

    def test_the_live_eval_hands_both_flags_to_the_app(self):
        args = eval_live_agent.parse(["--connection-id", CONNECTION, "--debug-thinking", "--ask-why-on-miss"])
        environment = eval_live_agent.trial_environment(Path("/tmp/trial"), Path("/tmp/accounts"), args, 1)
        self.assertEqual((environment[diagnostics.DEBUG_ENV], environment[diagnostics.ASK_ENV]), ("1", "1"))
        self.assertGreater(diagnostics.extra_seconds(args), 300)
        controller = SimpleNamespace(debug_thinking=False, state={"provider": "claude_subscription"})
        stamp = diagnostics.configure(controller, environment)
        self.assertTrue(controller.debug_thinking)
        self.assertEqual((stamp["diagnostics"]["debug_thinking"], stamp["diagnostics"]["ask_why_on_miss"]), (True, True))
        self.assertEqual(diagnostics.configure(controller, {}), {})
        self.assertFalse(controller.debug_thinking)

    def test_the_in_app_driver_sets_debug_mode_before_the_first_launch_and_asks_last(self):
        source = (HERE / "eval_live_agent_session.py").read_text()
        configure = source[source.index("    def configure(self):"):source.index("    def on_team_boot")]
        self.assertLess(configure.index("eval_diagnostics.configure(self.controller)"),
                        configure.index("begin_opening(self.controller)"))
        finishing = source[source.index("        if self.index >= len(self.tasks):"):]
        self.assertLess(finishing.index("self.scenario.finish(bpy, self.window, probe="),
                        finishing.index("eval_diagnostics.after_trial(self, bpy)"))
        self.assertIn('self.why.tick if self.phase == "why"', source)


class MarkTests(unittest.TestCase):
    FLAGS = {"debug_thinking": True, "ask_why_on_miss": True}

    def test_reports_scorecards_and_pages_say_it_is_a_diagnostic_run(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            (output / "SUMMARY.md").write_text("# Summary\n")
            (output / "oneshot-scorecard.json").write_text(json.dumps({"test": "oneshot_bridge"}))
            (output / "oneshot-report.html").write_text("<html><body class='x'><h1>Report</h1></body></html>")
            (output / "angelo-trial-06" / "trial-01" / "qa").mkdir(parents=True)
            (output / "angelo-trial-06" / "trial-01" / "qa" / "why.md").write_text("# Why\n")
            self.assertEqual(diagnostics.mark_outputs(output, {}), [])
            self.assertEqual(diagnostics.mark_outputs(output, dict(self.FLAGS, claim="...")),
                             ["SUMMARY.md", "oneshot-scorecard.json", "oneshot-report.html"])
            summary = (output / "SUMMARY.md").read_text()
            self.assertTrue(summary.startswith("> **Diagnostic run (debug thinking + ask why on miss)"))
            self.assertIn("angelo-trial-06/trial-01/qa/why.md", summary)
            card = json.loads((output / "oneshot-scorecard.json").read_text())
            self.assertEqual(card["diagnostics"]["why"], ["angelo-trial-06/trial-01/qa/why.md"])
            self.assertIn("not a timing or benchmark claim", (output / "oneshot-report.html").read_text())
            self.assertEqual(diagnostics.mark_outputs(output, self.FLAGS)[:1], ["oneshot-scorecard.json"],
                             "a report is marked once")

    def test_marked_transcripts_read_the_same(self):
        lines = [{"type": "user", "message": {"role": "user", "content": "Build a cube"}},
                 {"type": "assistant", "message": {"id": "m1", "model": "claude-opus-5-5", "usage": {
                     "input_tokens": 5, "cache_read_input_tokens": 10, "cache_creation_input_tokens": 20},
                     "content": [{"type": "tool_use", "name": "StructuredOutput",
                                  "input": {"operations": [{"tool": "angelo_scene_program"}]}}]}}]
        tasks = [{"id": "cube", "prompt": "Build a cube"}]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "angelo-trial-01-transcript.jsonl"
            path.write_text("".join(json.dumps(line) + "\n" for line in lines))
            before = (bench_mcp_report.angelo_transcript_steps(path, tasks),
                      bench_cold_warm.from_claude(bench_cold_warm._lines(path)))
            diagnostics.mark_transcript(path, self.FLAGS)
            diagnostics.mark_transcript(path, self.FLAGS)
            self.assertEqual(path.read_text().count(diagnostics.TRANSCRIPT_MARK), 1)
            self.assertEqual((bench_mcp_report.angelo_transcript_steps(path, tasks),
                              bench_cold_warm.from_claude(bench_cold_warm._lines(path))), before)
            raw = Path(folder) / "claude-stream.jsonl"
            raw.write_text("".join(json.dumps({"t_s": 1.0, "task": "cube", "message": line}) + "\n" for line in lines))
            diagnostics.mark_transcript(raw, self.FLAGS, stream=True)
            parsed = [json.loads(line) for line in raw.read_text().splitlines()]
            self.assertEqual([line["task"] for line in parsed], ["diagnostics", "cube", "cube"])
            self.assertEqual(bench_cold_warm.from_claude(bench_cold_warm._lines(raw), "cube"), before[1])


class ClaimRefusalTests(unittest.TestCase):
    def test_gate_and_measured_tools_refuse_a_diagnostic_run_whatever_is_passed(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder) / "debug-run"
            run.mkdir()
            (run / "RUN-PLAN.json").write_text(json.dumps({"diagnostics": {"debug_thinking": True}}))
            (run / "bench-mcp-vs-angelo.json").write_text(json.dumps({"meta": {"plan": {}}, "records": []}))
            self.assertIn("diagnostic run (debug thinking)", diagnostics.claim_refusal(run))
            with mock.patch("sys.stderr"):
                self.assertEqual(bench_mcp_gate.main([str(run), "--mixed-versions"]), 2)
            with self.assertRaises(diagnostics.DiagnosticRun):
                measured_results.measured([str(run)], mixed_versions=True)
            plain = Path(folder) / "plain-run"
            plain.mkdir()
            (plain / "RUN-PLAN.json").write_text(json.dumps({"order": []}))
            self.assertIsNone(diagnostics.claim_refusal(plain))


if __name__ == "__main__":
    unittest.main()
