# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every Codex route reads its trial's Codex log by one rule (``eval_codex_logs``), wired through the real harness.

Angelo's side (``eval_live_agent.run_trial``) on a subscription, an API-key connection run in place and a team
connection (``team:<mode>``, whose Codex state is kept under its mode, never a folder named with a ':'); a Claude
connection is never read. The MCP side (``bench_mcp_codex.run_trial``) reads its own sqlite home only, never
``CODEX_HOME``'s log. Rows are the fixture of ``test_native_eval_codex_logs.py``.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from codex_log_fixtures import Log, THROTTLED
import bench_mcp_codex as codex_mcp
import eval_codex_logs as codex_logs
import eval_live_agent
import eval_live_tasks as live
import eval_provider_limits as limits


class AngeloTrialTests(unittest.TestCase):
    def run_trial(self, root, accounts, connection_id, log):
        def run(command, cwd, env, **kwargs):
            log.add(time.time(), THROTTLED)   # The trial's runtime retried a 429 during the trial.
            (Path(env["ANGELO_QA_DIR"]) / "trial.json").write_text(json.dumps(
                {"tasks": [{"task": "t", "status": "completed"}], "success": True}))
            return SimpleNamespace(returncode=0)

        args = SimpleNamespace(stable_state_dir="", task_timeout=60, task_set="default", demo_record=False,
                               input_isolation="on", connection_id=connection_id, model="", effort="low")
        environment = lambda folder, accounts, args, trial, state=None, **k: {   # noqa: E731
            "ANGELO_STATE_DIR": str(state or folder / "state"), "ANGELO_CONNECTIONS_DIR": str(accounts),
            "ANGELO_QA_DIR": str(folder / "qa")}
        try:   # Closed before its folder goes: Windows cannot remove an open database.
            with mock.patch.object(eval_live_agent.subprocess, "run", run), \
                    mock.patch.object(eval_live_agent, "trial_environment", environment), \
                    mock.patch.object(eval_live_agent.addon_pin, "trial_stamp", return_value={}), \
                    mock.patch.object(eval_live_agent.trace_export, "after_trial", lambda folder, report, *a, **k: report):
                return eval_live_agent.run_trial(Path("/bin/true"), root / "out", accounts, args, 1, 1)
        finally:
            log.close()

    def test_a_codex_connection_is_read_and_a_claude_one_is_not(self):
        from angelo_agent.connections import ConnectionStore
        for codex in (True, False):
            with self.subTest(codex=codex), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                accounts = root / "source-connections"   # An API-key connection in place: outside the trial.
                record = ConnectionStore(accounts).add("OPENAI" if codex else "ANTHROPIC", "SUBSCRIPTION")
                # The same throttled row in both: the connection's runtime, from the trial's store, decides.
                report = self.run_trial(root, accounts, record["id"], Log(accounts / "codex-state" / record["id"]))
                if codex:
                    self.assertEqual(report["invalid"], limits.INVALID)
                    self.assertIn("did not report", report["aborted"])
                    self.assertTrue((root / "out" / "trial-01" / report["codex_log"]["logs"][0]["copy"]).is_file())
                else:
                    self.assertNotIn("invalid", report)
                    self.assertNotIn("codex_log", report)

    def test_a_team_connection_is_read_under_its_mode(self):
        self.assertEqual(codex_logs.connection_folders(None, live.TEAM_PREFIX + "codex_team"),
                         ("codex_team", ["codex_team"]))
        self.assertTrue(codex_logs.codex_runtime("codex_team"))
        self.assertFalse(codex_logs.trial_runs_codex(None, live.TEAM_PREFIX + "claude_team"))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            accounts = root / "out" / "accounts"   # A team's state, in the trial's own connections folder.
            report = self.run_trial(root, accounts, live.TEAM_PREFIX + "codex_team",
                                    Log(accounts / "codex-state" / "codex_team"))
            self.assertEqual(report["invalid"], limits.INVALID)
            self.assertFalse(any(":" in path.name for path in root.rglob("*")))

    def test_a_connection_without_an_id_of_its_own_is_read_under_its_mode(self):
        from angelo_agent.connections import ConnectionStore
        with tempfile.TemporaryDirectory() as folder:
            record = ConnectionStore(folder).add("OPENAI", "SUBSCRIPTION")
            self.assertEqual(codex_logs.connection_folders(folder, record["id"]),
                             ("codex_subscription", [record["id"], "codex_subscription"]))
            self.assertEqual(codex_logs.connection_folders(folder, "missing"), (None, []))
            self.assertEqual(codex_logs.connection_folders(None, "missing"), (None, []))


class McpTrialTests(unittest.TestCase):
    def test_the_mcp_side_reads_its_own_sqlite_home_never_codex_home(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            home = Log(root / "codex-home", folder="log")   # CODEX_HOME's log: other processes' rows only.
            home.add(time.time() + 1, THROTTLED)
            trial = root / "mcp-trial-01"
            logs = {}

            def launch(config, folder, port):   # The trial's Codex writes to its own sqlite home, then a 429 retry.
                logs["own"] = Log(folder / "codex-sqlite", folder="")
                logs["own"].add(time.time(), THROTTLED)
                raise RuntimeError("no Blender in a host test")

            config = {"claude_cwd": str(root / "cwd"), "task_set": "default", "claude_config_dir": "",
                      "codex_home": str(root / "codex-home")}
            try:
                with mock.patch.object(codex_mcp.workdirs, "fresh", return_value=str(root / "cwd")), \
                        mock.patch.object(codex_mcp.trial_base, "launch_blender", launch):
                    output = codex_mcp.run_trial(config, 1, trial)
            finally:
                home.close()
                if "own" in logs:
                    logs["own"].close()
            self.assertIn("no Blender in a host test", output["error"])
            self.assertEqual(output["invalid"], limits.INVALID)
            [entry] = output["codex_log"]["logs"]
            self.assertEqual(entry["copy"], "codex-logs/home-00/logs_2.sqlite")
            self.assertEqual(json.loads((trial / "trial.json").read_text())["invalid"], limits.INVALID)

    def test_the_mcp_side_counts_only_the_thread_its_stream_named(self):
        with tempfile.TemporaryDirectory() as folder:
            trial = Path(folder)
            log = Log(trial / "codex-sqlite", folder="")
            try:
                log.add(time.time(), THROTTLED, thread="another")
                since = time.time() - 5
                output = codex_logs.after_mcp_trial({}, trial, ["own-thread"], since)
                self.assertNotIn("invalid", output)
                output = codex_logs.after_mcp_trial({}, trial, [None], since)   # Not named: first seen in the window.
                self.assertEqual(output["invalid"], limits.INVALID)
            finally:
                log.close()


if __name__ == "__main__":
    unittest.main()
