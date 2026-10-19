# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A provider rate or usage limit makes a harness trial invalid and stops the run; never a task failure.

Offline: the recorded shapes of limit-ended Claude runs
("You've hit your session limit", zero tokens, about 0.4 s per turn),
the MCP side's runner over a scripted stream, and both harnesses' stop and
pass-rate paths with mocked trials. No process, network or account.
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

import bench_mcp_report as report  # noqa: E402
import bench_mcp_trial as trial  # noqa: E402
import bench_mcp_vs_angelo as bench  # noqa: E402
import eval_live_agent  # noqa: E402
import eval_provider_limits as limits  # noqa: E402
import eval_live_tasks as live  # noqa: E402

CONNECTION = "11111111-1111-4111-8111-111111111111"
SESSION_LIMIT = "You've hit your session limit · resets 8:10am (Asia/Tokyo)"
ZERO = {"input_tokens": 0, "cached_input_tokens": 0, "cache_write_input_tokens": 0, "output_tokens": 0,
        "cost_usd": 0, "turns": 1}
USED = dict(ZERO, input_tokens=12, cached_input_tokens=27000, output_tokens=180)


class ClassificationTests(unittest.TestCase):
    def test_the_recorded_angelo_shape_is_a_limit(self):
        rows = [{"role": "assistant", "text": SESSION_LIMIT}, {"role": "error", "text": SESSION_LIMIT}]
        self.assertIn("session limit", limits.angelo_limit(rows, ZERO))
        self.assertTrue(limits.angelo_limit([{"role": "error", "text": "rate_limit_error: 429"}], USED),
                        "An explicit rate-limit error, even after tokens were used")

    def test_codex_rows_are_limits_as_claude_rows_are(self):
        """An Angelo Codex turn a throttle or usage limit ended is a limit, not a 300 s timeout."""
        from angelo_agent.provider_errors import codex_error_text
        throttled = codex_error_text({"message": "exceeded retry limit, last status: 429 Too Many Requests",
                                      "codexErrorInfo": {"responseTooManyFailedAttempts": {"httpStatusCode": 429}}})
        usage = codex_error_text({"message": "You've hit your usage limit. Try again at 1:51 AM.",
                                  "codexErrorInfo": "usageLimitExceeded"})
        for text in (throttled, usage, "API Error: 429 Too Many Requests"):
            with self.subTest(text=text):
                self.assertTrue(limits.angelo_limit([{"role": "user", "text": "Move it."}, {"role": "error", "text": text}],
                                                    USED))
        busy = codex_error_text({"message": "Selected model is at capacity.", "codexErrorInfo": "serverOverloaded"})
        self.assertIsNone(limits.angelo_limit([{"role": "error", "text": busy}], ZERO), "overloaded is a failure")

    def test_a_throttled_turn_angelo_resent_is_a_limit_even_when_it_then_succeeded(self):
        done = [{"role": "user", "text": "Move it."}, {"role": "assistant", "text": "Done."}]
        self.assertIn("HTTP 429", limits.angelo_limit(done, USED, {"provider_retries_rate": 1, "edits": 0}))
        self.assertIsNone(limits.angelo_limit(done, USED, {"provider_retries_busy": 1}), "an overloaded resend is not")
        self.assertIsNone(limits.angelo_limit(done, USED, {"provider_retries_rate": 0}))
        self.assertIsNone(limits.angelo_limit(done, USED, {"provider_retries_rate": "1"}))

    def test_ordinary_failures_and_replies_are_not_limits(self):
        self.assertIsNone(limits.angelo_limit([{"role": "error", "text": "The target changed after approval"}], ZERO))
        self.assertIsNone(limits.angelo_limit([{"role": "assistant", "text": "The rate limit of the API is 5/s."}],
                                              USED), "A reply that mentions a limit, with tokens used")
        self.assertIsNone(limits.angelo_limit([{"role": "assistant", "text": "Done."}], ZERO))
        self.assertFalse(limits.zero_tokens(None))
        self.assertFalse(limits.zero_tokens({"cost_usd": 0}))

    def test_mcp_stream_shapes(self):
        synthetic = {"type": "assistant", "message": {"model": "<synthetic>", "content": [
            {"type": "text", "text": SESSION_LIMIT}]}}
        result = {"type": "result", "subtype": "success", "is_error": True, "result": SESSION_LIMIT}
        self.assertTrue(limits.mcp_limit([(0.1, synthetic), (0.2, result)], result, ZERO))
        rejected = {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "rateLimitType": "five_hour"}}
        self.assertIn("five_hour", limits.mcp_limit([rejected], None, None))
        warning = {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed_warning"}}
        ok = {"type": "result", "subtype": "success", "is_error": False, "result": "There are 3 mesh objects."}
        self.assertIsNone(limits.mcp_limit([warning, ok], ok, USED))
        # A denied built-in tool is reported with ``message`` as text; it is not limit evidence and must not raise.
        denied = {"type": "system", "subtype": "permission_denied", "tool_name": "Read",
                  "message": "Permission for this tool use was denied."}
        self.assertIsNone(limits.mcp_limit([(0.1, denied), (0.2, ok)], ok, USED))
        self.assertTrue(limits.mcp_limit([(0.1, denied), (0.2, synthetic), (0.3, result)], result, ZERO))

    def test_valid_records_count_invalid_trials_per_side(self):
        records = [{"side": "angelo", "trial": 1, "task": "a", "invalid": limits.INVALID},
                   {"side": "angelo", "trial": 1, "task": "b", "invalid": limits.INVALID},
                   {"side": "angelo", "trial": 2, "task": "a"}, {"side": "mcp", "trial": 1, "task": "a"}]
        valid, invalid = limits.valid_records(records)
        self.assertEqual(len(valid), 2)
        self.assertEqual(invalid, {"angelo": {"trials": 1, "tasks": 2}})


class McpSideTests(unittest.TestCase):
    def test_a_limited_task_is_invalid_and_stops_the_trial(self):
        stream = mock.Mock(process=object(), spawned=0.)
        result = {"type": "result", "subtype": "success", "is_error": True, "result": SESSION_LIMIT}
        stream.collect.return_value = ([(0.4, result)], result, None)
        client = mock.Mock()
        client.execute.side_effect = [{"eval_object_present": True, "digest": "d"}, {"host_check_passed": False}]
        usage = mock.Mock()
        usage.turn.return_value = ZERO
        task = {"id": "transform", "kind": "edit", "source": "s", "prompt": "Move it.", "requires_object": True}
        record = trial.run_task(task, client, stream, usage, {"task_timeout": 60}, 1)
        self.assertEqual((record["status"], record["invalid"]), ("invalid", limits.INVALID))
        self.assertTrue(record["abort"].startswith("STOPPED: the provider's rate or usage limit"))


class LiveEvalTests(unittest.TestCase):
    def setUp(self):
        from test_native_addon_guard import matching_app
        matching_app(self)   # The fixture app bundles no add-on; the bench's add-on pin has its own tests.

    def test_aggregate_leaves_invalid_trials_out_and_counts_them(self):
        good = {"task": "transform", "status": "completed", "host_check_passed": True,
                "receipt": {"status": "completed"}}
        bad = {"task": "transform", "status": "invalid", "invalid": limits.INVALID, "host_check_passed": False,
               "receipt": {"status": "failed"}}
        report_ = eval_live_agent.aggregate({}, {}, [{"tasks": [good]}, {"tasks": [bad], "invalid": limits.INVALID}])
        self.assertEqual(report_["tasks"]["transform"]["runs"], 1)
        self.assertEqual(report_["invalid_trials"], {limits.INVALID: 1})

    def test_the_run_stops_after_a_rate_limited_trial(self):
        limited = {"tasks": [{"task": "read_only_question", "status": "invalid"}], "invalid": limits.INVALID,
                   "aborted": limits.STOPPED_MESSAGE % SESSION_LIMIT, "success": False}
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder) / "Angelo"
            binary.write_text("")
            binary.chmod(0o755)
            with mock.patch.object(eval_live_agent, "run_trial", return_value=limited) as run, \
                    mock.patch.object(eval_live_agent, "isolated_connection", return_value={"id": CONNECTION}), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as errors:
                code = eval_live_agent.main(["--authorize-billed-runs", "--connection-id", CONNECTION,
                                             "--blender", str(binary), "--trials", "3",
                                             "--output", str(Path(folder) / "out")])
            written = json.loads((Path(folder) / "out" / "eval-live-agent.json").read_text())
        self.assertEqual(run.call_count, 1, "No further billed trial")
        self.assertEqual(code, limits.EXIT_RATE_LIMITED)
        self.assertIn("STOPPED: the provider's rate or usage limit", errors.getvalue())
        self.assertEqual(written["invalid_trials"], {limits.INVALID: 1})

    def test_harness_runs_disable_the_refusal_fallback(self):
        args = eval_live_agent.parse(["--authorize-billed-runs", "--connection-id", CONNECTION, "--blender", "/x"])
        environment = eval_live_agent.trial_environment(Path("/private/tmp/t"), Path("/private/tmp/a"), args, 1)
        self.assertEqual(environment["CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK"], "1")
        with mock.patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=True):
            self.assertEqual(trial.clean_environment()["CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK"], "1")


class BenchTests(unittest.TestCase):
    def setUp(self):
        from test_native_addon_guard import matching_app
        matching_app(self)   # The fixture app bundles no add-on; the bench's add-on pin has its own tests.

    def args(self, folder, *extra):
        return bench.parse(["--authorize-billed-runs", "--connection-id", CONNECTION, "--output", folder, *extra])

    def test_a_rate_limited_angelo_trial_stops_both_sides(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "out"
            argv = ["--authorize-billed-runs", "--connection-id", CONNECTION, "--output", str(output),
                    "--trials", "3", "--sides", "both"]

            def angelo(args, out, number):
                harness = out / ("angelo-trial-%02d" % number)
                harness.mkdir(parents=True)
                (harness / "eval-live-agent.json").write_text(json.dumps({"trials": [{
                    "tasks": [{"task": "read_only_question", "kind": "read_only", "status": "invalid"}],
                    "invalid": limits.INVALID, "aborted": limits.STOPPED_MESSAGE % SESSION_LIMIT}]}))
                return {"side": "angelo", "trial": number, "exit_code": limits.EXIT_RATE_LIMITED}
            mcp_trial = {"side": "mcp", "tasks": [{"task": "read_only_question", "status": "completed"}]}
            with mock.patch.object(bench, "preflight", return_value=([], output)), \
                    mock.patch.object(bench.mcp, "run_trial", return_value=mcp_trial) as mcp_run, \
                    mock.patch.object(bench, "run_angelo_trial", side_effect=angelo) as angelo_run, \
                    mock.patch.object(bench, "finish", return_value={"summary": {
                        "totals": {}, "invalid_rate_limited": {"angelo": {"trials": 1, "tasks": 1}}}}), \
                    mock.patch.object(bench, "gather", return_value=([], [])), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as errors:
                code = bench.main(argv)
        self.assertEqual((mcp_run.call_count, angelo_run.call_count), (1, 1), "Nothing after the limit")
        self.assertEqual(code, limits.EXIT_RATE_LIMITED)
        self.assertIn("STOPPED", errors.getvalue())

    def test_summaries_exclude_invalid_trials_and_say_so(self):
        records = [{"side": "angelo", "trial": n, "task": "transform", "kind": "edit", "status": status,
                    "wall_ms": 1000., "host_check_passed": passed, **({"invalid": limits.INVALID} if invalid else {})}
                   for n, status, passed, invalid in ((1, "completed", True, False), (2, "invalid", False, True))]
        with tempfile.TemporaryDirectory() as folder:
            args = self.args(folder, "--trials", "2")
            with mock.patch.object(bench, "versions", return_value={}):
                results = bench.finish(args, Path(folder), records, [])
            summary = (Path(folder) / bench.SUMMARY).read_text()
        self.assertEqual(results["summary"]["tasks"]["transform"]["angelo"]["pass_rate"], 1.0)
        self.assertEqual(results["summary"]["invalid_rate_limited"], {"angelo": {"trials": 1, "tasks": 1}})
        self.assertIn("Invalid (rate-limited), excluded from every pass rate", summary)
        self.assertEqual(len(results["records"]), 2, "Invalid records are kept, marked")

    def test_recorded_runs_are_reclassified_from_their_chats(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            sessions = output / "angelo-trial-01" / "trial-01" / "state" / "sessions"
            sessions.mkdir(parents=True)
            (sessions / "chat.json").write_text(json.dumps({"messages": [
                {"role": "user", "text": "How many mesh objects?"}, {"role": "error", "text": SESSION_LIMIT}]}))
            (output / "angelo-trial-01" / "eval-live-agent.json").write_text(json.dumps({"trials": [{
                "tasks": [{"task": "read_only_question", "kind": "read_only", "status": "failed"}]}]}))
            trial_report, records = bench.load_angelo_trial(output, 1, live.build_tasks())
        self.assertEqual(trial_report["invalid"], limits.INVALID)
        self.assertNotIn("invalid", records[0], "gather marks the records from the trial")


if __name__ == "__main__":
    unittest.main()
