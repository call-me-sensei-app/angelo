# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``--warm-up-only`` (eval_warm_up, bench_warm_up): its flags and refusals, its record, and that no trial count,
plan or resume ever reads it. Unbilled; the sides' processes are replaced. The unbilled dry run
(``--dry-run-scenario --warm-up-only``) proves the warm-up's prefix is the trial's."""

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

import bench_mcp_vs_angelo as bench  # noqa: E402
import bench_warm_up  # noqa: E402
import eval_live_agent  # noqa: E402
import eval_warm_up  # noqa: E402
from test_native_bench_resume import finished  # noqa: E402

CONNECTION = "44444444-4444-4444-8444-444444444444"
USAGE = {"input_tokens": 12, "cached_input_tokens": 0, "output_tokens": 8, "cache_creation_input_tokens": 60000}


class WarmUpTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.output = Path(self.folder.name) / "out"
        self.output.mkdir()
        self.ran = []
        # Hermetic: a scenario run first makes (or reads) the stock-Blender references, which live in the machine's
        # own ~/.angelo-bench. These tests are about the warm-up, so references come from nowhere real: the folder is
        # this test's, and making them is recorded, not rendered (it passed only where a developer had them cached).
        import scenario_references
        import scenario_specs
        environment = mock.patch.dict(os.environ, {scenario_specs.REFERENCES_ENV: str(Path(self.folder.name) / "refs")})
        environment.start()
        self.addCleanup(environment.stop)
        self.references = []
        maker = mock.patch.object(scenario_references, "make",
                                  side_effect=lambda name, blender, *a, **k: self.references.append(name) or {})
        maker.start()
        self.addCleanup(maker.stop)

    def argv(self, *extra):
        return ["--authorize-billed-runs", "--connection-id", CONNECTION, "--task-set", "oneshot-bridge", "--trials",
                "2", "--output", str(self.output), "--debug-thinking", *extra]

    def warm_up(self, argv):
        def mcp_side(config, folder, stamp=None):
            folder.mkdir(parents=True)
            self.ran.append(("mcp warm-up", config["claude_cwd"], stamp))
            return {"side": "mcp", "warm_up": True, "status": "completed", "provider_usage": dict(USAGE)}

        def angelo_side(args, output, folder, command):
            built, _ = command(args, output, folder)
            self.ran.append(("angelo warm-up", built + ["--warm-up-only"]))
            return {"side": "angelo", "warm_up": True, "status": "completed", "provider_usage": dict(USAGE)}
        with mock.patch.object(bench, "preflight", side_effect=lambda args: ([], Path(args.output).resolve())), \
                mock.patch.object(bench_warm_up, "mcp_side", side_effect=mcp_side), \
                mock.patch.object(bench_warm_up, "angelo_side", side_effect=angelo_side), \
                contextlib.redirect_stdout(io.StringIO()):
            return bench.main(argv)

    def trials(self, argv):
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
                mock.patch.object(bench.mcp, "run_trial", side_effect=mcp_trial), \
                mock.patch.object(bench, "run_angelo_trial", side_effect=angelo_trial), \
                mock.patch.object(bench, "gather", return_value=([], [])), \
                mock.patch.object(bench, "finish", return_value=summary), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return bench.main(argv)

    def test_a_warm_up_is_recorded_apart_and_resume_then_adds_trial_one(self):
        self.assertEqual(self.warm_up(self.argv("--warm-up-only")), 0)
        record = json.loads((self.output / "warm-up" / "WARM-UP-01.json").read_text())
        self.assertTrue(record["warm_up"])
        self.assertEqual(record["prompt"], eval_warm_up.PROMPT)
        self.assertEqual(record["conditions"]["diagnostics"], {"debug_thinking": True})
        self.assertIsNotNone(record["sides"]["mcp"]["list_price_usd"], "priced as a trial's request is")
        angelo = next(entry for entry in self.ran if entry[0] == "angelo warm-up")[1]
        trial_command, _ = bench.angelo_command(bench.parse(self.argv()), self.output.resolve(), self.output / "x")
        self.assertEqual(angelo[angelo.index("--debug-thinking"):], ["--debug-thinking", "--warm-up-only"])
        self.assertEqual([part for part in angelo if part != "--warm-up-only"][:4], trial_command[:4])
        mcp = next(entry for entry in self.ran if entry[0] == "mcp warm-up")
        self.assertEqual(mcp[1], bench.mcp_config(bench.parse(self.argv()), self.output.resolve())["claude_cwd"],
                         "the trials' own working directory, which the client's prompt names")
        self.assertFalse((self.output / bench.RUN_PLAN).exists(), "the run's plan is the trials' to write")
        self.assertFalse(list(self.output.glob("*-trial-*")))
        self.assertFalse(bench.trial_done(self.output, "mcp", 1) or bench.trial_done(self.output, "angelo", 1))
        self.ran.clear()
        self.assertEqual(self.trials(self.argv("--resume", "--max-new-trials", "1")), 0)
        self.assertEqual(self.ran, [("mcp", 1), ("angelo", 1)], "the warm-up is no trial")
        self.assertEqual(self.warm_up(self.argv("--warm-up-only", "--resume")), 0)
        self.assertTrue((self.output / "warm-up" / "WARM-UP-02.json").is_file(), "each warm-up its own record")

    def test_refusals_and_the_live_eval_flag(self):
        self.assertTrue(bench.parse(["--runtime", "codex", "--warm-up-only"]).warm_up_only, "both runtimes")
        with mock.patch("sys.stderr"), self.assertRaises(SystemExit):
            bench.parse(["--runtime", "codex", "--warm-up-only", "--returning-user"])
        args = eval_live_agent.parse(["--connection-id", CONNECTION, "--warm-up-only"])
        environment = eval_live_agent.trial_environment(Path("/tmp/trial"), Path("/tmp/accounts"), args, 1)
        self.assertEqual(environment[eval_warm_up.WARM_UP_ENV], "1")
        self.assertTrue(eval_warm_up.active(environment))
        plain = eval_live_agent.parse(["--connection-id", CONNECTION])
        self.assertEqual(eval_live_agent.trial_environment(Path("/tmp/trial"), Path("/tmp/accounts"), plain, 1)
                         [eval_warm_up.WARM_UP_ENV], "")
        self.assertEqual(eval_warm_up.plan(plain), {})
        self.assertNotIn("warm_up", json.dumps(bench.plan(bench.parse(self.argv("--warm-up-only")))),
                         "a warm-up never changes the run's plan, so --resume still matches")

    def test_a_codex_run_warms_up_through_the_codex_mcp_launch(self):
        calls = []
        argv = ["--authorize-billed-runs", "--connection-id", CONNECTION, "--runtime", "codex", "--codex-home",
                str(self.output.parent / "codex-profile"), "--task-set", "oneshot-bridge", "--output", str(self.output),
                "--debug-thinking", "--warm-up-only", "--model", "gpt-6.1-sol"]

        def codex_side(config, folder, stamp=None, fake_base_url=None):
            calls.append((config["runtime"], config["model"], stamp))
            return {"side": "mcp", "runtime": "codex", "status": "completed", "provider_usage": dict(USAGE)}

        def angelo_side(args, output, folder, command):
            built, _ = command(args, output, folder)
            calls.append(("angelo", built[built.index("--codex-home") + 1]))
            return {"side": "angelo", "status": "completed", "provider_usage": dict(USAGE)}
        with mock.patch.object(bench, "preflight", side_effect=lambda args: ([], Path(args.output).resolve())), \
                mock.patch.object(bench_warm_up, "mcp_codex_side", side_effect=codex_side), \
                mock.patch.object(bench_warm_up, "angelo_side", side_effect=angelo_side), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bench.main(argv), 0)
        self.assertEqual(self.references, ["oneshot_bridge"], "the references are made before any billed message")
        self.assertEqual(calls[0], ("codex", "gpt-6.1-sol", {"debug_thinking": True}))
        self.assertEqual(calls[1], ("angelo", str(self.output.parent / "codex-profile")))

    def test_angelo_codex_rollouts_of_the_trial_are_copied_and_older_ones_left(self):
        import os
        import time
        home = Path(self.folder.name) / "codex-home"
        day = home / "sessions" / "2026" / "10" / "18"
        day.mkdir(parents=True)
        old, new = day / "rollout-old.jsonl", day / "rollout-new.jsonl"
        old.write_text('{"type": "session_meta"}\n')
        new.write_text('{"type": "session_meta"}\n')
        since = time.time()
        os.utime(old, (since - 3600, since - 3600))
        copied = bench.copy_codex_rollouts(home, since - 1, self.output / "angelo-trial-01-rollouts",
                                           {"debug_thinking": True})
        self.assertEqual(copied, ["rollout-new.jsonl"])
        first = (self.output / "angelo-trial-01-rollouts" / "rollout-new.jsonl").read_text().split("\n", 1)[0]
        self.assertIn("angelo-diagnostic-run", first, "marked as the run's transcripts are")
        self.assertFalse(bench.trial_done(self.output, "angelo", 1), "a rollouts folder is no trial")

    def test_the_codex_mcp_record_states_effort_summary_and_each_request(self):
        import mcp_request_parameters
        events = [(0.1, {"method": "item/reasoning/summaryTextDelta", "params": {"delta": "Planning"}}),
                  (0.2, {"method": "thread/tokenUsage/updated",
                         "params": {"tokenUsage": {"last": {"reasoningOutputTokens": 40}}}}),
                  (0.3, {"method": "thread/tokenUsage/updated",
                         "params": {"tokenUsage": {"last": {"reasoningOutputTokens": 0}}}})]
        record = mcp_request_parameters.codex({"effort": "low", "model": "gpt-6.1-sol", "debug_thinking": True}, events)
        self.assertEqual((record["effort"], record["reasoning_summary_requested"]), ("low", "auto"))
        self.assertEqual(record["requests"], [{"reasoning_tokens": 40, "summary": True},
                                              {"reasoning_tokens": 0, "summary": False}])
        plain = mcp_request_parameters.codex({"effort": "low", "model": "gpt-6.1-sol"}, [])
        self.assertTrue(plain["reasoning_summary_requested"].startswith("Codex default"))

    def test_the_in_app_driver_sends_the_warm_up_before_any_task(self):
        source = (HERE / "eval_live_agent_session.py").read_text()
        sending = source[source.index("    def on_send(self):"):source.index("        task = self.tasks[self.index]")]
        self.assertLess(sending.index("eval_warm_up.active()"), sending.index("if self.index >= len(self.tasks):"))


if __name__ == "__main__":
    unittest.main()
