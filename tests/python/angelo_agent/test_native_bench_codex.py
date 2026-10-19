# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Offline checks for the Codex side of the MCP vs Angelo benchmark: no process, network or account."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import bench_mcp_codex as codex  # noqa: E402
from angelo_agent.cli_runtime import CODEX_BUNDLE  # noqa: E402
import bench_mcp_vs_angelo as bench  # noqa: E402
import eval_live_agent  # noqa: E402
import eval_provider_limits as limits  # noqa: E402

CONNECTION = "22222222-2222-4222-8222-222222222222"
CONFIG = {"mcp_server_dir": "/srv/mcp", "stock_blender": "/srv/Blender", "model": "gpt-6-astra", "effort": "low",
          "codex": "/srv/codex", "codex_home": "/srv/home"}


def event(method, **params):
    return {"method": method, "params": params}


class CommandTests(unittest.TestCase):
    @unittest.skipIf(sys.platform == "win32", "The comparison bench runs Blender MCP from a POSIX virtual "
                     "environment (.venv/bin); it is a macOS benchmark")
    def test_the_blender_server_is_the_only_one_and_never_prompts(self):
        command = codex.codex_command(CONFIG, 39123)
        self.assertEqual(command[:4], ["/srv/codex", "app-server", "--listen", "stdio://"])
        pairs = dict(item.split("=", 1) for item in command[5::2])
        self.assertEqual(pairs["approval_policy"], '"never"')
        self.assertEqual(pairs["sandbox_mode"], '"read-only"')
        self.assertEqual(pairs["mcp_servers.blender.default_tools_approval_mode"], '"approve"')
        self.assertEqual(pairs["mcp_servers.blender.command"], '"/srv/mcp/.venv/bin/blender-mcp"')
        self.assertIn('BLENDER_MCP_PORT="39123"', pairs["mcp_servers.blender.env"])
        self.assertNotIn("model_provider", pairs, "A billed run uses the account's own OpenAI provider")
        self.assertEqual({name for name in pairs if name.startswith("mcp_servers.")},
                         {"mcp_servers.blender." + key for key in ("command", "args", "env", "default_tools_approval_mode")})

    def test_a_fake_endpoint_needs_no_sign_in(self):
        pairs = dict(item.split("=", 1) for item in codex.codex_command(CONFIG, 39123, "http://127.0.0.1:1/v1")[5::2])
        self.assertEqual(pairs["model_provider"], '"angelo_fake"')
        self.assertEqual(pairs["model_providers.angelo_fake.requires_openai_auth"], "false")

    def test_toml_values(self):
        self.assertEqual(codex._toml({"A": "1", "B": True}), '{A="1",B=true}')
        self.assertEqual(codex._toml([]), "[]")
        self.assertEqual(codex._toml('say "hi"'), '"say \\"hi\\""')

    def test_environment_keeps_state_in_the_trial(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict("os.environ", {"OPENAI_API_KEY": "x",
                                                                                      "CODEX_HOME": "/elsewhere"}):
            env = codex.environment(CONFIG, Path(folder))
        self.assertEqual(env["CODEX_HOME"], "/srv/home")
        self.assertEqual(env["CODEX_SQLITE_HOME"], str(Path(folder) / "codex-sqlite"))
        self.assertNotIn("OPENAI_API_KEY", env)


class PreflightTests(unittest.TestCase):
    def test_codex_defaults_to_the_chatgpt_app_only_on_macos(self):
        self.assertEqual(codex.default_codex("darwin"), CODEX_BUNDLE)
        self.assertEqual(codex.default_codex("win32"), "")
        problems = codex.preflight(dict(CONFIG, codex=""))
        self.assertIn("--codex is required here: it has no default on this platform", problems)

    def test_refuses_the_everyday_profile_without_reading_it(self):
        with mock.patch("pathlib.Path.read_text", side_effect=AssertionError("read")):
            problems = codex.preflight(dict(CONFIG, codex_home=str(Path.home() / ".codex")))
        self.assertTrue(any("never ~/.codex" in problem for problem in problems))

    def test_needs_a_signed_in_profile_without_its_own_servers(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            self.assertTrue(any("not signed in" in p for p in codex.preflight(dict(CONFIG, codex_home=folder))))
            (home / "auth.json").write_text("{}")
            (home / "config.toml").write_text('[mcp_servers.other]\ncommand = "x"\n')
            self.assertTrue(any("other" in p for p in codex.preflight(dict(CONFIG, codex_home=folder))))
            (home / "config.toml").write_text('model = "gpt-6-astra"\n')
            self.assertFalse([p for p in codex.preflight(dict(CONFIG, codex_home=folder)) if "home" in p])

    def test_angelo_side_profile_is_named_for_its_connection(self):
        with tempfile.TemporaryDirectory() as folder:
            wrong = Path(folder) / "codex-profile" / "other"
            right = Path(folder) / "codex-profile" / CONNECTION
            for path in (wrong, right):
                path.mkdir(parents=True)
                (path / "auth.json").write_text("{}")
            with self.assertRaisesRegex(eval_live_agent.Refusal, "codex-profile/" + CONNECTION):
                eval_live_agent.benchmark_codex_profile(str(wrong), CONNECTION, "codex_subscription")
            self.assertEqual(eval_live_agent.benchmark_codex_profile(str(right), CONNECTION, "codex_subscription"),
                             str(right.resolve()))
            with self.assertRaisesRegex(eval_live_agent.Refusal, "only for Codex"):
                eval_live_agent.benchmark_codex_profile(str(right), CONNECTION, "claude_subscription")


class AnalysisTests(unittest.TestCase):
    def test_tool_round_trips_requests_and_first_text(self):
        item = {"type": "mcpToolCall", "id": "i1", "server": "blender", "tool": "execute_blender_code",
                "arguments": {"code": "x"}}
        events = [(10.2, event("thread/tokenUsage/updated", tokenUsage={"last": {"inputTokens": 900}})),
                  (10.3, event("item/started", item=item)),
                  (10.8, event("item/completed", item=dict(item, status="completed"))),
                  (11.0, event("item/agentMessage/delta", delta="Done")),
                  (11.1, event("thread/tokenUsage/updated", tokenUsage={"last": {"inputTokens": 1000}})),
                  (11.2, event("account/rateLimits/updated", rateLimits={"planType": "pro", "primary": None}))]
        result = codex.analyze_codex_events(events, 10.0)
        self.assertEqual(result["api_calls"], 2)
        self.assertEqual(result["tool_names"], ["mcp__blender__execute_blender_code"])
        self.assertEqual(result["tool_calls"][0]["round_trip_ms"], 500.0)
        self.assertEqual(result["first_text_ms"], 1000.0)
        self.assertEqual(result["prompt_tokens_per_request"], [900, 1000])
        self.assertEqual(result["rate_limits"], [{"planType": "pro", "primary": None}])
        failed = dict(item, status="failed")
        events[2] = (10.8, event("item/completed", item=failed))
        self.assertEqual(codex.analyze_codex_events(events, 10.0)["tool_errors"], 1)

    def test_result_summary_reads_the_last_agent_message(self):
        events = [(1, event("item/completed", item={"type": "agentMessage", "text": "Made it."}))]
        summary = codex.result_summary({"turn": {"id": "t", "status": "completed"}}, events)
        self.assertEqual((summary["subtype"], summary["is_error"], summary["reply"]), ("success", False, "Made it."))
        self.assertIsNone(codex.result_summary(None, events))


class LimitTests(unittest.TestCase):
    def test_limit_errors_and_reached_windows(self):
        failed = {"turn": {"status": "failed", "error": {"message": "Try later", "codexErrorInfo": "usageLimitExceeded"}}}
        self.assertIn("usageLimitExceeded", limits.codex_limit([], failed))
        notice = event("error", error={"message": "slow down", "codexErrorInfo": "rateLimitExceeded"})
        self.assertIn("rateLimitExceeded", limits.codex_limit([notice], {"turn": {"status": "completed"}}))
        reached = event("account/rateLimits/updated", rateLimits={"rateLimitReachedType": "primary", "planType": "plus"})
        self.assertIn("plan plus", limits.codex_limit([reached], None))
        full = event("account/rateLimits/updated", rateLimits={"primary": {"usedPercent": 100}})
        self.assertIsNotNone(limits.codex_limit([full], {"turn": {"status": "failed", "error": {}}}))

    def test_object_shaped_error_info_is_read_by_its_key(self):
        retry = event("error", error={"message": "Reconnecting... 2/5", "willRetry": True,
                                      "codexErrorInfo": {"responseStreamDisconnected": {"httpStatusCode": 503}}})
        self.assertIsNone(limits.codex_limit([retry], {"turn": {"status": "completed"}}))
        limited = event("error", error={"message": "Try later", "codexErrorInfo": {"usageLimitExceeded": {}}})
        self.assertIn("usageLimitExceeded", limits.codex_limit([limited], {"turn": {"status": "failed"}}))

    def test_a_429_that_ended_the_turn_is_a_limit_as_for_claude(self):
        throttled = {"message": "exceeded retry limit, last status: 429 Too Many Requests",
                     "codexErrorInfo": {"responseTooManyFailedAttempts": {"httpStatusCode": 429}}}
        failed = {"turn": {"status": "failed", "error": throttled}}
        self.assertIn("HTTP 429", limits.codex_limit([], failed))
        notice = event("error", error=throttled, willRetry=False)
        self.assertIn("HTTP 429", limits.codex_limit([notice], None), "a turn the harness timed out")
        retrying = event("error", error={"message": "Reconnecting... 1/5", "codexErrorInfo": {
            "responseStreamDisconnected": {"httpStatusCode": 429}}}, willRetry=True)
        # A 429 Codex waited out and retried through counts too: Angelo's receipts count the same retry
        # (codex_retry.note), so the rule is the same on both sides (eval_provider_limits).
        self.assertIn("retried by Codex", limits.codex_limit([retrying], {"turn": {"status": "completed"}}))
        self.assertIsNone(limits.codex_limit([], {"turn": {"status": "failed", "error": {
            "message": "overloaded", "codexErrorInfo": {"responseTooManyFailedAttempts": {"httpStatusCode": 503}}}}}))

    def test_ordinary_failures_are_not_limits(self):
        failed = {"turn": {"status": "failed", "error": {"message": "Bad request", "codexErrorInfo": "badRequest"}}}
        self.assertIsNone(limits.codex_limit([], failed))
        busy = event("account/rateLimits/updated", rateLimits={"primary": {"usedPercent": 100}})
        self.assertIsNone(limits.codex_limit([busy], {"turn": {"status": "completed"}}), "A full window that still served")
        self.assertIsNone(limits.codex_limit([event("account/rateLimits/updated", rateLimits={"planType": None})], None))


class LauncherTests(unittest.TestCase):
    def test_codex_plan_and_default_model(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(bench.main(["--plan-only", "--runtime", "codex", "--trials", "1"]), 0)
        planned = json.loads(out.getvalue().split("PLANNED ", 1)[1])
        self.assertEqual((planned["runtime"], planned["model"]), ("codex", "gpt-6-astra"))
        self.assertIn("ChatGPT account", planned["billing"])

    def test_codex_refusals_start_nothing(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch("subprocess.Popen") as popen, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as errors:
            code = bench.main(["--runtime", "codex", "--authorize-billed-runs", "--connection-id", CONNECTION,
                               "--source-connections-dir", folder, "--codex-home", str(Path.home() / ".codex"),
                               "--output", str(Path(folder) / "out")])
        self.assertEqual(code, 2)
        self.assertIn("never ~/.codex", errors.getvalue())
        popen.assert_not_called()

    def test_an_angelo_only_codex_run_skips_the_mcp_side_profile_check(self):
        # --angelo-team-mode codex_team runs --sides angelo and never passes --codex-home to the Angelo trial.
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(bench.codex_mcp, "preflight",
                                                                        return_value=["--codex-home is not signed in"]) as check:
            args = bench.parse(["--runtime", "codex", "--sides", "angelo", "--angelo-team-mode", "codex_team",
                                "--authorize-billed-runs", "--source-connections-dir", folder,
                                "--output", str(Path(folder) / "out")])
            problems, _ = bench.preflight(args)
        check.assert_not_called()
        self.assertFalse([p for p in problems if "codex-home" in p], problems)
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(bench.codex_mcp, "preflight",
                                                                        return_value=["--codex-home is not signed in"]) as check:
            args = bench.parse(["--runtime", "codex", "--sides", "both", "--authorize-billed-runs", "--connection-id",
                                CONNECTION, "--source-connections-dir", folder, "--output", str(Path(folder) / "out")])
            problems, _ = bench.preflight(args)
        check.assert_called_once()
        self.assertIn("--codex-home is not signed in", problems)

    def test_a_recorded_codex_trial_is_reanalyzed_from_its_raw_stream(self):
        with tempfile.TemporaryDirectory() as folder:
            trial = Path(folder) / "mcp-trial-01"
            trial.mkdir()
            limited = {"turn": {"id": "t", "status": "failed", "error": {"codexErrorInfo": "usageLimitExceeded"}}}
            lines = [{"t_s": 1.5, "task": "cube_creation", "message": event("thread/tokenUsage/updated", tokenUsage={})},
                     {"t_s": 1.9, "task": "cube_creation", "message": event("turn/completed", **limited)}]
            (trial / "codex-stream.jsonl").write_text("\n".join(json.dumps(line) for line in lines))
            (trial / "trial.json").write_text(json.dumps({"runtime": "codex", "tasks": [
                {"side": "mcp", "task": "cube_creation", "sent_at_s": 1.0, "status": "failed"}]}))
            data = bench.load_mcp_trial(Path(folder), 1)
        self.assertEqual(data["tasks"][0]["api_calls"], 1)
        self.assertEqual(data["invalid"], limits.INVALID)
        self.assertEqual(data["tasks"][0]["status"], "invalid")


class McpStartupTests(unittest.TestCase):
    """Codex starts MCP servers asynchronously; the first turn waits for the Blender server."""

    def stream(self, folder):
        return codex.CodexStream([], {}, folder, Path(folder), CONFIG)

    def test_waits_for_the_blender_server_and_ignores_other_servers(self):
        import threading
        with tempfile.TemporaryDirectory() as folder:
            stream = self.stream(folder)
            stream._event("mcpServer/startupStatus/updated", {"name": "blender", "status": "starting"})
            later = threading.Timer(0.1, lambda: (
                stream._event("mcpServer/startupStatus/updated", {"name": "other", "status": "ready"}),
                stream._event("mcpServer/startupStatus/updated", {"name": "blender", "status": "ready"})))
            later.start()
            stream.wait_for_mcp_server()
            later.join()
            stream.raw.close()
        self.assertEqual(stream.mcp_status, "ready")
        self.assertGreaterEqual(stream.mcp_ready_ms, 90)
        self.assertEqual(stream.lines.qsize(), 3)   # Every notification still reaches the turn's events.

    def test_a_runtime_without_startup_status_is_not_held(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(codex, "MCP_STATUS_GRACE_S", 0.05):
            stream = self.stream(folder)
            stream.wait_for_mcp_server()
            stream.raw.close()
        self.assertIsNone(stream.mcp_status)
        self.assertLess(stream.mcp_ready_ms, 1000)

    def test_a_failed_server_ends_the_wait(self):
        with tempfile.TemporaryDirectory() as folder:
            stream = self.stream(folder)
            stream._event("mcpServer/startupStatus/updated", {"name": "blender", "status": "failed"})
            stream.wait_for_mcp_server()
            stream.raw.close()
        self.assertEqual(stream.mcp_status, "failed")


if __name__ == "__main__":
    unittest.main()
