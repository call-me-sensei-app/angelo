# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The returning-user variant: one profile across launches, a new chat per task, every request logged.

No process, network or account: the app's JSON-RPC class and the chat are stand-ins.
"""

import argparse
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

import bench_mcp_vs_angelo as bench  # noqa: E402
import eval_live_agent  # noqa: E402
import eval_request_log  # noqa: E402
import eval_returning_user as returning  # noqa: E402

CONNECTION = "22222222-2222-4222-8222-222222222222"
WARM_UP = "This message only opens the session"


class ProfileTests(unittest.TestCase):
    def test_a_profile_keeps_state_and_user_folders_across_launches(self):
        with tempfile.TemporaryDirectory() as folder:
            args = eval_live_agent.parse(["--connection-id", CONNECTION, "--profile-dir", folder + "/P",
                                          "--new-chat-per-task", "--request-log"])
            environment = eval_live_agent.trial_environment(Path(folder) / "trial-01", Path(folder) / "P/accounts",
                                                            args, 1)
            self.assertEqual(environment["ANGELO_STATE_DIR"], str(Path(folder).resolve() / "P/state"))
            self.assertEqual(environment["BLENDER_USER_SCRIPTS"], str(Path(folder).resolve() / "P/blender-user/scripts"))
            self.assertEqual((environment["ANGELO_EVAL_NEW_CHAT_PER_TASK"], environment["ANGELO_EVAL_REQUEST_LOG"]),
                             ("1", "1"))
            self.assertTrue((Path(folder) / "P/state").is_dir())
            self.assertEqual(returning.accounts(args, Path(folder) / "out"), Path(folder).resolve() / "P/accounts")
            self.assertIsNone(returning.kept_connection(args, CONNECTION), "A new profile copies the connection")
            plain = eval_live_agent.trial_environment(Path(folder) / "t", Path(folder) / "a",
                                                      eval_live_agent.parse(["--connection-id", CONNECTION]), 1)
            self.assertEqual(plain["ANGELO_STATE_DIR"], str(Path(folder) / "t/state"))
            self.assertEqual(plain["ANGELO_EVAL_NEW_CHAT_PER_TASK"], "")

    def test_a_profile_and_a_stable_state_path_exclude_each_other(self):
        with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
            eval_live_agent.parse(["--profile-dir", "/p", "--stable-state-dir", "/s"])

    def test_every_task_after_the_first_opens_a_new_chat(self):
        chats = iter(["chat-2", "chat-3"])
        ui = SimpleNamespace(create_session=lambda connection_id: SimpleNamespace(state={"id": next(chats)}))
        trial = SimpleNamespace(index=0, record={}, controller=SimpleNamespace(state={"id": "chat-1"}),
                                configure=mock.Mock())
        with mock.patch.dict("os.environ", {"ANGELO_EVAL_NEW_CHAT_PER_TASK": "1"}):
            returning.next_chat(trial, ui, CONNECTION)
            self.assertEqual(trial.record["chat"], "chat-1")
            trial.index = 1
            returning.next_chat(trial, ui, CONNECTION)
        self.assertEqual(trial.record["chat"], "chat-2")
        trial.configure.assert_called_once()


class RequestLogTests(unittest.TestCase):
    def test_threads_forks_warm_ups_and_each_requests_tokens_are_logged_without_text(self):
        calls = [("thread/start", {"model": "m"}, {"thread": {"id": "t1"}}),
                 ("turn/start", {"threadId": "t1", "input": [{"type": "text", "text": "Make a cube"}]}, {}),
                 ("thread/start", {"model": "m"}, {"thread": {"id": "base"}}),
                 ("turn/start", {"threadId": "base", "input": [{"type": "text", "text": WARM_UP}]}, {}),
                 ("thread/fork", {"threadId": "base", "experimentalPredictionMode": True, "ephemeral": True},
                  {"thread": {"id": "t2"}})]
        rows = [eval_request_log.call_row(method, params, result, WARM_UP) for method, params, result in calls]
        usage = lambda thread, i, c, o: eval_request_log.event_row("thread/tokenUsage/updated", {  # noqa: E731
            "threadId": thread, "turnId": "u", "tokenUsage": {"last": {"inputTokens": i, "cachedInputTokens": c,
                                                                       "outputTokens": o}}})
        rows += [usage("t1", 25000, 0, 50), usage("t1", 27000, 25000, 600), usage("t2", 25200, 24800, 40)]
        self.assertNotIn("Make a cube", json.dumps(rows))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "requests.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            threads = {entry["thread"]: entry for entry in eval_request_log.summary(path)}
        self.assertEqual((threads["t1"]["began"], threads["t1"]["first_cached_share"], len(threads["t1"]["requests"])),
                         ("start", 0.0, 2))
        self.assertTrue(threads["base"]["warm_up"])
        self.assertEqual((threads["t2"]["began"], threads["t2"]["source"], threads["t2"]["first_cached_share"]),
                         ("fork", "base", 0.984))

    def test_the_log_wraps_the_apps_json_rpc_class(self):
        class Process:
            def __init__(self, command, on_event=None):
                self.on_event = on_event

            def request(self, method, params=None):
                return {"thread": {"id": "t9"}}
        protocol = SimpleNamespace(JsonRpcProcess=Process)
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.dict("sys.modules", {"angelo_agent.protocol": protocol,
                                                "angelo_agent.codex_bases": SimpleNamespace(WARM_UP=WARM_UP)}), \
                mock.patch("angelo_agent.protocol", protocol, create=True), \
                mock.patch("angelo_agent.codex_bases", SimpleNamespace(WARM_UP=WARM_UP), create=True):
            eval_request_log.install(Path(folder) / "requests.jsonl")
            process = protocol.JsonRpcProcess(["codex"], on_event=lambda method, params: None)
            process.request("thread/start", {"model": "m"})
            process.on_event("thread/started", {"thread": {"id": "t9"}})
            process.request("account/read", {})
            rows = [json.loads(line) for line in (Path(folder) / "requests.jsonl").read_text().splitlines()]
        self.assertEqual([(r["method"], r["thread"]) for r in rows], [("thread/start", "t9"), ("thread/started", "t9")])


class ColumnTests(unittest.TestCase):
    def test_the_report_splits_cold_start_relaunch_and_new_chats(self):
        import final_runs_report
        def log(rows):
            return "".join(json.dumps(row) + "\n" for row in rows)

        def usage(thread, i, c):
            return {"kind": "event", "method": "thread/tokenUsage/updated", "thread": thread,
                    "request": {"input": i, "cached": c, "output": 10}}
        launches = {
            1: [{"kind": "call", "method": "thread/start", "thread": "a1"}, usage("a1", 25000, 0),
                {"kind": "call", "method": "thread/start", "thread": "w1"},
                {"kind": "call", "method": "turn/start", "thread": "w1", "warm_up": True}, usage("w1", 25000, 24000),
                {"kind": "call", "method": "thread/fork", "thread": "a2", "source": "w1"}, usage("a2", 25200, 24800)],
            2: [{"kind": "call", "method": "thread/start", "thread": "b1"}, usage("b1", 25000, 0),
                {"kind": "call", "method": "thread/resume", "thread": "w1", "source": "w1"},
                {"kind": "call", "method": "thread/fork", "thread": "b2", "source": "w1"}, usage("b2", 25200, 24900)]}
        with tempfile.TemporaryDirectory() as folder:
            for launch, rows in launches.items():
                qa = Path(folder) / ("angelo-trial-%02d/trial-01/qa" % launch)
                qa.mkdir(parents=True)
                (qa / "requests.jsonl").write_text(log(rows))
                mcp = Path(folder) / ("mcp-trial-%02d" % launch)
                mcp.mkdir()
                (mcp / "trial.json").write_text(json.dumps({"tasks": [
                    {"first_request": {"input": 17000, "cached": 12288}}, {"first_request": {"input": 17000, "cached": 0}}]}))
            result = final_runs_report.returning_columns(folder)
        columns = result["columns"]
        self.assertEqual(columns["angelo/cold start"], {"tasks": 1, "forked": 0, "first_request_cached_share_median": 0.0})
        self.assertEqual(columns["angelo/relaunch, first chat"]["forked"], 0)
        self.assertEqual(columns["angelo/new chat, running app"], {"tasks": 2, "forked": 2,
                                                                   "first_request_cached_share_median": 0.986})
        self.assertEqual(columns["mcp/relaunch, first chat"]["first_request_cached_share_median"], 0.723)
        self.assertEqual(len(result["warm_ups"]), 1)


class BenchTests(unittest.TestCase):
    def test_the_bench_runs_both_sides_as_returning_users(self):
        args = bench.parse(["--returning-user", "--runtime", "codex", "--connection-id", CONNECTION])
        self.assertTrue(bench.plan(args)["angelo"]["returning_user"])
        self.assertTrue(bench.mcp_config(args, Path("/tmp/out"))["new_thread_per_task"])
        with tempfile.TemporaryDirectory() as folder:
            args.output, args.source_connections_dir, args.angelo_binary = folder, folder, "/app/Angelo"
            args.angelo_stable_workspace, args.codex_home = True, folder
            with mock.patch("subprocess.run", return_value=SimpleNamespace(returncode=0)) as run:
                bench.run_angelo_trial(args, Path(folder), 2)
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--profile-dir") + 1], str(Path(folder) / "angelo-profile"))
        self.assertIn("--new-chat-per-task", command)
        self.assertNotIn("--stable-state-dir", command, "The profile replaces the emptied stable path")
        with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
            bench.parse(["--returning-user", "--runtime", "claude"])


if __name__ == "__main__":
    unittest.main()
