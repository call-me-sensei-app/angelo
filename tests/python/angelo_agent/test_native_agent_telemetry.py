# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Private bounded telemetry receipts and statistics; no Blender or network."""

import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.provider_usage import TOKENS as USAGE_TOKENS
from angelo_agent.telemetry import MAX_FILE_BYTES, MAX_REQUESTS, PHASES, RequestMetrics, TelemetryStore, clean_receipt


class TelemetryTests(unittest.TestCase):
    def test_whitelist_finite_data_private_atomic_restart_and_no_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TelemetryStore(directory)
            receipt = {"status": "completed", "runtime": "codex", "runtime_start": "cold",
                "session_id": str(uuid.uuid4()), "prompt": "PRIVATE", "code": "PASSWORD", "api_key": "SECRET",
                "phases": {"total_ms": 12, "first_text_ms": 1, "secret": "PRIVATE", "commit_ms": float("nan")},
                "counts": {"tool_invocations": 2, "fake_retries": 100, "replays": True}}
            self.assertTrue(store.record(receipt))
            self.assertTrue(store.flush())
            text = store.path.read_text()
            self.assertNotIn("PRIVATE", text)
            self.assertNotIn("PASSWORD", text)
            self.assertNotIn("SECRET", text)
            self.assertNotIn("NaN", text)
            if os.name != "nt":  # POSIX mode bits; on Windows the per-user profile's access list applies
                self.assertEqual(os.stat(store.path).st_mode & 0o777, 0o600)
            restored = TelemetryStore(directory)
            self.assertEqual(restored.report()["completed_requests"], 1)
            self.assertEqual(restored.report()["counts"]["tool_invocations"], 2)
            before = restored.path.stat().st_mtime_ns
            restored.report()
            self.assertEqual(restored.path.stat().st_mtime_ns, before)
            self.assertIn("model_rounds", restored.report()["unmeasured_counts"])
            self.assertNotIn("model_rounds", restored.report()["counts"])

    def test_completed_p50_p95_cold_warm_and_session_filters_are_truthful(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = str(uuid.uuid4()), str(uuid.uuid4())
            store = TelemetryStore(directory)
            for value, temperature, status, session in [(10, "cold", "completed", first),
                    (30, "warm", "completed", first), (9000, "cold", "failed", first), (20, "warm", "completed", second)]:
                store.record({"status": status, "runtime": "codex", "runtime_start": temperature,
                    "session_id": session, "phases": {"total_ms": value, "runtime_start_ms": value},
                    "counts": {"tool_invocations": 1}})
            self.assertTrue(store.flush())
            report = store.report(first)
            self.assertEqual(report["completed_requests"], 2)
            self.assertEqual(report["phases"]["total_ms"], {"samples": 2, "p50": 20, "p95": 29})
            self.assertEqual(report["counts"]["tool_invocations"], 3)
            self.assertEqual(report["runtime_start"]["cold"]["samples"], 1)
            self.assertEqual(store.report()["completed_requests"], 3)
            self.assertEqual(store.report(first, 1)["completed_requests"], 0)

    def test_persistence_is_bounded_and_uses_the_shared_serialized_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = []
            store = TelemetryStore(directory, submit=jobs.append)
            for i in range(MAX_REQUESTS + 40):
                store.record({"status": "completed", "phases": {"total_ms": i}, "counts": {}})
            self.assertEqual(len(jobs), 1)
            self.assertEqual(len(store.receipts), MAX_REQUESTS)
            jobs.pop()()
            self.assertTrue(store.flush())
            self.assertEqual(len(json.loads(store.path.read_text())["requests"]), MAX_REQUESTS)
            self.assertLess(store.path.stat().st_size, 512 * 1024)

    def test_request_clock_is_monotonic_and_no_late_update_or_second_finish(self):
        clock = [4.]
        metrics = RequestMetrics(clock=lambda: clock[0])
        metrics.count("tool_invocations")
        clock[0] += .02
        metrics.mark("first_text_ms")
        clock[0] += .04
        receipt = metrics.finish("completed")
        metrics.count("tool_invocations")
        metrics.add("total_ms", 999)
        self.assertEqual(receipt["phases"]["total_ms"], 60)
        self.assertEqual(receipt["counts"]["tool_invocations"], 1)
        self.assertIsNone(metrics.finish("completed"))
        json.dumps(receipt, allow_nan=False)

    def test_corrupt_symlink_and_nonfinite_receipts_do_not_become_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "private.json"
            source.write_text('{"api_key":"SECRET"}')
            (root / "performance.json").symlink_to(source)
            store = TelemetryStore(directory)
            self.assertEqual(store.report()["retained_requests"], 0)
            self.assertIsNone(clean_receipt({"phases": {"total_ms": float("inf")}}))
            self.assertIsNone(clean_receipt({"phases": {"total_ms": 10 ** 1000}}))
            self.assertIsNone(clean_receipt({"status": [], "runtime": {}, "phases": {}}))
            self.assertEqual(source.read_text(), '{"api_key":"SECRET"}')


class ReceiptExtensionTests(unittest.TestCase):
    """Provider usage, main-thread peaks, fallback categories and cold/warm totals."""

    def test_main_thread_block_is_the_longest_single_section_never_a_sum(self):
        metrics = RequestMetrics()
        for milliseconds in (12, 48, 30):
            metrics.peak("main_thread_block_ms", milliseconds)
        metrics.add("main_thread_block_ms", 1000)
        metrics.peak("execution_ms", 1000)
        metrics.peak("main_thread_block_ms", float("nan"))
        receipt = metrics.finish("completed")
        self.assertEqual(receipt["phases"]["main_thread_block_ms"], 48)
        self.assertNotIn("execution_ms", receipt["phases"])
        metrics.peak("main_thread_block_ms", 5000)
        self.assertEqual(metrics.phases["main_thread_block_ms"], 48)

    def test_usage_fields_survive_only_when_every_provider_turn_reported_them(self):
        metrics = RequestMetrics(runtime="claude")
        metrics.count("provider_turn_requests", 2)
        metrics.provider_usage({"input_tokens": 3010, "output_tokens": 200, "api_ms": 2500, "cost_usd": .04})
        metrics.provider_usage({"input_tokens": 3110, "output_tokens": 60, "cost_usd": .01, "unknown": 7,
                                "turns": 99, "output_tokens_bad": -1})
        metrics.provider_usage({"input_tokens": True})
        receipt = metrics.finish("completed")
        self.assertEqual(receipt["usage"], {"input_tokens": 6120, "output_tokens": 260, "cost_usd": .05, "turns": 2})
        incomplete = RequestMetrics()
        incomplete.count("provider_turn_requests", 2)
        incomplete.provider_usage({"input_tokens": 10})
        incomplete.provider_usage(None)
        self.assertNotIn("usage", incomplete.finish("completed"))
        no_turns = RequestMetrics()
        no_turns.provider_usage({"input_tokens": 10})
        self.assertNotIn("usage", no_turns.finish("completed"))

    def test_receipt_usage_is_an_allowlist_of_bounded_numbers(self):
        base = {"status": "completed", "phases": {"total_ms": 1}, "counts": {}}
        cleaned = clean_receipt(dict(base, usage={"turns": 2, "input_tokens": 10 ** 12, "cached_input_tokens": 3,
            "output_tokens": -1, "reasoning_output_tokens": 1.5, "turn_ms": float("inf"), "api_ms": 12.34567,
            "cost_usd": 1e7, "prompt": "PRIVATE"}))
        self.assertEqual(cleaned["usage"], {"turns": 2, "input_tokens": 10 ** 12, "cached_input_tokens": 3,
                                            "api_ms": 12.346})
        self.assertNotIn("usage", clean_receipt(dict(base, usage={"input_tokens": 5})))
        self.assertNotIn("usage", clean_receipt(dict(base, usage={"turns": 1})))
        self.assertNotIn("usage", clean_receipt(dict(base, usage=["not", "usage"])))

    def test_fallback_categories_are_counted_once_per_script_and_measured_per_receipt(self):
        metrics = RequestMetrics()
        metrics.count_fallback(("geometry", "materials", "geometry", "invented"))
        metrics.count_fallback(("geometry",))
        counts = metrics.finish("completed")["counts"]
        self.assertEqual(counts["python_fallbacks"], 2)
        self.assertEqual(counts["python_fallback_geometry"], 2)
        self.assertEqual(counts["python_fallback_materials"], 1)
        self.assertNotIn("python_fallback_invented", counts)
        self.assertEqual(RequestMetrics().finish("completed")["counts"]["python_fallbacks"], 0)
        with tempfile.TemporaryDirectory() as directory:
            store = TelemetryStore(directory)
            store.record({"status": "completed", "phases": {"total_ms": 5}, "counts": {"tool_invocations": 1}})
            store.record({"status": "completed", "phases": {"total_ms": 5}, "counts": counts})
            self.assertTrue(store.flush())
            report = store.report()
            self.assertEqual(report["python_fallbacks"], {"samples": 1, "scripts": 2,
                                                         "categories": {"geometry": 2, "materials": 1}})
            self.assertFalse(any(name.startswith("python_fallback") for name in report["counts"]))

    def test_report_aggregates_complete_usage_and_cold_warm_totals(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TelemetryStore(directory)
            for total, temperature, usage in [(4000, "cold", {"turns": 1, "input_tokens": 10000, "cached_input_tokens": 0}),
                                              (1000, "warm", {"turns": 2, "input_tokens": 30000, "cached_input_tokens": 24000,
                                                              "api_ms": 800}),
                                              (2000, "warm", None)]:
                receipt = {"status": "completed", "runtime_start": temperature, "phases": {"total_ms": total},
                           "counts": {"provider_turn_requests": 1}}
                if usage:
                    receipt["usage"] = usage
                store.record(receipt)
            store.record({"status": "failed", "phases": {"total_ms": 9}, "counts": {},
                          "usage": {"turns": 1, "input_tokens": 999999}})
            self.assertTrue(store.flush())
            report = store.report()
            usage = report["provider_usage"]
            self.assertEqual(usage["fields"]["input_tokens"], {"samples": 2, "p50": 20000, "p95": 29000, "total": 40000})
            self.assertEqual(usage["fields"]["api_ms"]["samples"], 1)
            self.assertEqual(usage["cached_input_share"], .6)
            self.assertIn("cost_usd", usage["unmeasured_fields"])
            self.assertEqual(usage["requests_with_provider_turns"], 3)
            self.assertEqual(report["total_by_runtime_start"]["cold"], {"samples": 1, "p50": 4000, "p95": 4000})
            self.assertEqual(report["total_by_runtime_start"]["warm"]["samples"], 2)
            self.assertIn("main_thread_block_ms", report["phase_scopes"])

    def receipt(self, **extra):
        """A receipt as long sessions record them, with every section present (about 1.9 KB; recorded ones are 1.0-1.5)."""
        counts = ("python_fallbacks", "provider_turn_requests", "model_rounds", "tool_invocations", "tool_executions",
                  "tool_completions", "tool_failures", "precondition_checks", "native_steps", "native_commits",
                  "verification_samples", "python_fallback_geometry", "python_fallback_materials", "rejections",
                  "failure_script", "failure_other")
        return dict({
            "status": "completed", "runtime": "claude", "runtime_start": "warm", "session_id": str(uuid.uuid4()),
            "phases": {name: 3599999.123 for name in PHASES}, "counts": {name: 123 for name in counts},
            "usage": dict({name: 1234567 for name in USAGE_TOKENS}, turns=12, turn_ms=3599999.123, api_ms=3599999.123,
                          cost_usd=12.345678, requests=14),
            "route": {"mode": "claude_subscription", "requested_model": "claude-opus-5-5", "effort": "medium",
                      "fast": False, "cache_ttl": "1h", "cache_gap": "5m_to_1h", "auto": False, "single_model": True,
                      "escalation": 0, "served_models": ["claude-opus-5-5"]},
            "path": {"edits": 14, "image_delivery": "confirmed", "images_returned": 9, "images_delivered": 9,
                     "images": [[1, 0, {"attachment": 2}], [4, 6, {"view:SOLID:AXIS": 2, "view:MATERIAL:SCENE_CAMERA": 1}],
                                [9, 14, {"view:MATERIAL:SCENE_CAMERA": 2, "preview": 2}]],
                     "steps": [["lookup", "ok", "ACTIONS", ["mesh_bevel", "modifier_add"], 1],
                               ["typed", "check", "angelo_scene_program", ["mesh_bevel"], 1],
                               ["api", "ok", "RNA", [], 2], ["script", "raised", "", [], 1], ["script", "ok", "", [], 9]]}},
            **extra)

    def test_a_full_store_of_receipts_as_long_sessions_record_them_fits_the_file_budget(self):
        cleaned = clean_receipt(self.receipt())
        self.assertEqual(set(cleaned), {"status", "runtime", "runtime_start", "session_id", "phases", "counts", "usage",
                                        "route", "path"})
        self.assertEqual(len(cleaned["usage"]), len(USAGE_TOKENS) + 5)
        size = len(json.dumps(cleaned, ensure_ascii=False).encode("utf-8"))
        self.assertLess(size, MAX_FILE_BYTES // MAX_REQUESTS, "Under the average the budget allows every receipt")
        encoded = json.dumps({"version": 1, "requests": [cleaned] * MAX_REQUESTS}, ensure_ascii=False, allow_nan=False)
        self.assertLess(len(encoded.encode("utf-8")), MAX_FILE_BYTES)

    def test_the_file_never_exceeds_its_budget_and_keeps_the_newest_receipts(self):
        # Receipts are not all one size: long model names and four consultations make one about 10 KB.
        long = dict(self.receipt()["route"], requested_model="m" * 128, served_models=["m" * 127 + str(n) for n in range(8)])
        with tempfile.TemporaryDirectory() as directory:
            store = TelemetryStore(directory)
            for index in range(MAX_REQUESTS):
                usage = dict(self.receipt()["usage"])
                self.assertTrue(store.record(self.receipt(route=long, phases={"total_ms": index + 1},
                                                          consultations=[dict(long, usage=usage)] * 4)))
            self.assertTrue(store.flush())
            self.assertLessEqual(store.path.stat().st_size, MAX_FILE_BYTES)
            self.assertEqual(len(store.receipts), MAX_REQUESTS, "This run still has them all")
            kept = [value["phases"]["total_ms"] for value in TelemetryStore(directory).receipts]
            self.assertGreater(len(kept), MAX_REQUESTS // 8)
            self.assertLess(len(kept), MAX_REQUESTS)
            self.assertEqual(kept, list(range(MAX_REQUESTS - len(kept) + 1, MAX_REQUESTS + 1)), "The newest, in order")

    def test_a_model_reads_the_same_report_as_before(self):
        # angelo_agent_metrics returns report(): the receipt's new sections and counts are not in it.
        with tempfile.TemporaryDirectory() as directory:
            store = TelemetryStore(directory)
            store.record(self.receipt())
            self.assertTrue(store.flush())
            report = store.report()
        self.assertEqual(sorted(report["counts"]), [
            "duplicate_pending", "failure_binding", "failure_check", "failure_expired", "failure_frame",
            "failure_modifier", "failure_other", "frame_rebinds", "invalid_calls", "model_rounds", "native_commits",
            "native_steps", "playback_waits", "precondition_checks", "provider_turn_requests", "rejections", "replays",
            "staging_restarts", "tool_completions", "tool_executions", "tool_failures", "tool_invocations",
            "verification_samples"])
        usage = report["provider_usage"]
        self.assertEqual(sorted(usage["fields"]) + usage["unmeasured_fields"],
                         sorted(USAGE_TOKENS + ("turn_ms", "api_ms", "cost_usd")))
        self.assertEqual(sorted(report), [
            "available_requests", "cache_gaps", "completed_requests", "consultations", "count_samples", "count_scopes",
            "counts", "limit", "local_only", "max_retained_requests", "notes", "phase_scopes", "phases",
            "provider_usage", "python_fallbacks", "retained_requests", "routes", "runtime_start",
            "total_by_runtime_start", "unmeasured_counts", "usage_scopes", "version"])
        self.assertNotIn("path", json.dumps(report))

    def test_a_script_that_raised_has_its_own_count(self):
        from angelo_agent.failure_codes import failure_code
        raised = {"error": "KeyError: 'Scale' (line 2)", "script_error": True, "rolled_back": True}
        self.assertEqual(failure_code(raised), "script")
        self.assertEqual(failure_code({"error": "KeyError: 'Scale' (line 2)"}), "other", "Only the executor's own account")
        metrics = RequestMetrics("claude")
        for _ in range(3):
            metrics.count("failure_" + failure_code(raised))
        receipt = metrics.finish("completed")
        self.assertEqual(receipt["counts"]["failure_script"], 3)
        self.assertNotIn("Scale", json.dumps(receipt))

    def test_provider_resends_are_receipt_only_counts(self):
        """codex_retry: a turn resent after HTTP 429 or an overloaded provider; never in the model-read report."""
        with tempfile.TemporaryDirectory() as directory:
            store = TelemetryStore(directory)
            metrics = store.begin(RequestMetrics("codex", session_id="chat"))
            metrics.count("provider_retries_rate")
            receipt = metrics.finish("failed")
            self.assertEqual(receipt["counts"]["provider_retries_rate"], 1)
            report = store.report()
            self.assertNotIn("provider_retries_rate", report["counts"])
            self.assertNotIn("provider_retries_busy", json.dumps(report))

    def test_a_request_in_progress_can_be_read_and_a_finished_one_cannot(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TelemetryStore(directory)
            metrics = store.begin(RequestMetrics("codex", session_id="chat"))
            self.assertIsNone(store.running_usage("chat"), "Nothing known yet")
            metrics.count("provider_turn_requests")
            metrics.path.running = {"input_tokens": 9000, "cached_input_tokens": 8000, "output_tokens": 40, "requests": 1,
                                    "prompt": "PRIVATE"}
            self.assertEqual(store.running_usage("chat"), {"input_tokens": 9000, "cached_input_tokens": 8000,
                                                           "output_tokens": 40, "requests": 1, "turns": 1, "running": True})
            metrics.path.running = None
            metrics.provider_usage({"input_tokens": 9500, "cached_input_tokens": 8000, "output_tokens": 90, "requests": 2})
            self.assertEqual(store.running_usage("chat"), {"input_tokens": 9500, "cached_input_tokens": 8000,
                                                           "output_tokens": 90, "requests": 2, "turns": 1, "running": False})
            self.assertIsNone(store.running_usage("other"))
            metrics.finish("completed")
            self.assertIsNone(store.running_usage("chat"))


if __name__ == "__main__":
    unittest.main()
