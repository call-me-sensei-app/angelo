# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A throttle Codex retried without telling its client is limit evidence, read from Codex's own log.

Regression for the 2.1.0 Sol gate on 93d6a3d3336: Codex hid its first websocket retry of a 429
(``codex_core::responses_retry``: "stream disconnected - retrying sampling request (1/5 in 576s…) … 429 Too Many
Requests"), so neither Angelo's receipt (``provider_retries_rate``) nor any stream carried it and the pre-set rule
(``eval_provider_limits``) never fired. ``eval_codex_logs`` reads the copied log by the same rule on every Codex route.
The fixture database has the real schema and row shape of that gate's ``logs_2.sqlite``. The routes (Angelo's
subscription, API-key and team connections, the MCP side) are in ``test_native_eval_codex_log_routes.py``.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sqlite3
import tempfile
from pathlib import Path
import unittest
from unittest import mock

from codex_log_fixtures import Log, THREAD, THROTTLED, retry_body
import eval_codex_logs as codex_logs
import eval_provider_limits as limits
import bench_mcp_report


class ReadTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.home = self.root / "accounts" / "codex-state" / "connection-1"
        self.log = Log(self.home)
        self.addCleanup(self.folder.cleanup)
        self.addCleanup(self.log.close)
        self.start = 1795488500.25
        # An earlier run's thread on the same connection (an API-key log every run shares), and the trial's own.
        self.log.add(self.start - 30, "earlier turn", level="TRACE", target="codex_core::session", thread="earlier")
        self.log.add(self.start + 2, "turn started", level="TRACE", target="codex_core::session")

    def read(self, start=None, end=None, threads=None):
        return codex_logs.trial_logs([self.home], self.start if start is None else start,
                                     self.start + 300 if end is None else end, self.root / "trial", threads)

    def test_the_hidden_retry_is_missed_by_the_streams_and_found_in_the_log(self):
        self.log.add(self.start + 39.4, THROTTLED)
        # Red: what the streams and the receipt showed of that turn names no limit.
        self.assertIsNone(limits.angelo_limit([{"role": "assistant", "text": "Done."}], {"output_tokens": 40},
                                              {"provider_retries_rate": 0}))
        self.assertIsNone(limits.codex_limit([], {"turn": {"status": "completed"}}))
        evidence, record = self.read()
        self.assertIn("Codex retried a throttled request it did not report", evidence)
        self.assertIn("429 Too Many Requests", evidence)
        [log] = record["logs"]
        self.assertEqual(len(log["limit_rows"]), 1)
        self.assertEqual(log["limit_rows"][0]["thread"], THREAD)
        self.assertTrue(log["limit_rows"][0]["retry"].startswith("stream disconnected - retrying sampling request"))
        self.assertEqual(record["unverifiable"], [])
        # The rows were only in the live database's -wal: the copy holds them, self-contained once read.
        copy = self.root / "trial" / log["copy"]
        self.assertFalse((copy.parent / "logs_2.sqlite-wal").exists())
        connection = sqlite3.connect(copy)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM logs WHERE target = ?",
                                                (codex_logs.RETRY_TARGET,)).fetchone()[0], 1)
        finally:
            connection.close()

    def test_a_retry_for_another_reason_or_outside_the_window_never_counts(self):
        self.log.add(self.start + 10, retry_body("unexpected status 503 Service Unavailable: overloaded"))
        self.log.add(self.start + 11, retry_body("exceeded retry limit, last status: 503 Service Unavailable"))
        self.log.add(self.start + 12, retry_body("stream disconnected before completion: connection reset"))
        self.log.add(self.start - 5, THROTTLED, thread="earlier")   # Before the trial: another run's.
        self.log.add(self.start + 301, THROTTLED)          # After it.
        self.log.add(self.start + 13, THROTTLED, level="INFO")
        self.log.add(self.start + 14, THROTTLED, target="codex_core::other")
        self.log.add(self.start + 15, "a model reply that mentions 429 Too Many Requests", target="codex_core::model")
        evidence, record = self.read()
        self.assertIsNone(evidence)
        self.assertEqual(record["logs"][0]["limit_rows"], [])

    def test_another_runs_thread_in_the_window_is_not_the_trials(self):
        self.log.add(self.start + 20, THROTTLED, thread="earlier")   # First seen before the trial began.
        self.assertIsNone(self.read()[0])
        self.log.add(self.start + 21, THROTTLED, thread="named")     # The trial's client named only THREAD.
        self.assertIsNone(self.read(threads={THREAD})[0])
        self.assertIsNotNone(self.read(threads={"named"})[0])

    def test_a_throttled_compaction_retry_and_a_usage_limit_count_alike(self):
        self.log.add(self.start + 50, retry_body("429 Too Many Requests: rate limit reached", field="compact_error",
                                                 what="remote compaction v2 stream failed; retrying request after delay"))
        self.assertIsNotNone(self.read()[0])
        self.log.add(self.start + 60, retry_body("You've hit your usage limit. Try again later."))
        self.assertEqual(len(self.read()[1]["logs"][0]["limit_rows"]), 2)

    def test_a_thread_at_codexs_retention_is_unverifiable_and_a_fresh_one_is_not(self):
        self.assertEqual(self.read()[1]["unverifiable"], [])   # A fresh thread with few rows: nothing to flag.
        self.log.many(self.start + 100, codex_logs.KEEP_ROWS, thread="long")
        evidence, record = self.read()
        self.assertIsNone(evidence)
        [item] = record["logs"][0]["possibly_pruned"]
        self.assertEqual((item["thread"], item["rows"]), ("long", codex_logs.KEEP_ROWS))
        self.assertTrue(any("thread long at Codex's retention" in reason for reason in record["unverifiable"]))
        self.assertTrue(codex_logs.unverifiable({"codex_log": record}))

    def test_a_thread_over_the_byte_retention_is_unverifiable(self):
        self.log.add(self.start + 100, "big", level="TRACE", target="codex_core::session", thread="big",
                     size=codex_logs.KEEP_BYTES)
        self.assertEqual(self.read()[1]["logs"][0]["possibly_pruned"][0]["thread"], "big")

    def test_no_log_an_empty_log_and_a_log_without_its_table_are_unverifiable_not_evidence(self):
        evidence, record = codex_logs.trial_logs([self.root / "missing"], self.start, self.start + 1, self.root / "t1")
        self.assertIsNone(evidence)
        self.assertEqual(record["unverifiable"], ["no Codex runtime log found"])
        for name, make in (("empty", lambda c: c.execute(Log.SCHEMA)),
                           ("tableless", lambda c: c.execute("CREATE TABLE x (y)"))):
            with self.subTest(name=name):
                home = self.root / name
                home.mkdir()
                connection = sqlite3.connect(home / "logs_2.sqlite")
                make(connection)
                connection.commit()
                connection.close()
                evidence, record = codex_logs.trial_logs([home], self.start, self.start + 1, self.root / ("t-" + name))
                self.assertIsNone(evidence)
                self.assertEqual(record["logs"][0]["notes"][0], "empty" if name == "empty" else "no logs table")
                self.assertTrue(record["unverifiable"])

    def test_two_logs_of_one_home_are_copied_apart(self):
        older = Log(self.home, name="logs_2.sqlite", folder="")   # The sqlite home's own, beside log/logs_2.sqlite.
        self.addCleanup(older.close)
        older.add(self.start + 5, THROTTLED)
        self.log.add(self.start + 6, retry_body("exceeded retry limit, last status: 429 Too Many Requests"))
        evidence, record = self.read()
        copies = sorted(entry["copy"] for entry in record["logs"])
        self.assertEqual(copies, ["codex-logs/home-00/log/logs_2.sqlite", "codex-logs/home-00/logs_2.sqlite"])
        self.assertEqual(sum(len(entry["limit_rows"]) for entry in record["logs"]), 2)

    def test_mark_invalid_records_the_log_and_never_rewrites_an_earlier_verdict(self):
        report = codex_logs.mark_invalid({"tasks": []}, "evidence", {"logs": []})
        self.assertEqual((report["invalid"], report["codex_log"]), (limits.INVALID, {"logs": []}))
        self.assertIn("evidence", report["aborted"])
        earlier = {"invalid": limits.INVALID, "aborted": "first"}
        self.assertEqual(codex_logs.mark_invalid(earlier, "second", {})["aborted"], "first")
        self.assertNotIn("invalid", codex_logs.mark_invalid({}, None, {}))


class ScrubTests(unittest.TestCase):
    SECRETS = ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0LXVzZXIifQ.c2lnbmF0dXJl", "sk-proj-AbCdEfGhIjKlMnOpQrStUv",
               "AIzaSyD-fixture-not-a-real-key-0123456789", "tok_live_fixture_value", "person@example.com",
               "cookie-fixture-value")

    def test_a_kept_copy_holds_no_credential_shaped_value(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder) / "home"
            log = Log(home)
            try:
                log.add(1000.5, 'response headers: {"set-cookie": "__oailb=%s; Path=/", "x": "%s"}'
                        % (self.SECRETS[5], self.SECRETS[0]), level="TRACE", target="codex_api::transport")
                log.add(1001, "Authorization: Bearer %s api_key=%s key %s" % (self.SECRETS[3], self.SECRETS[1],
                                                                             self.SECRETS[2]), level="TRACE")
                log.add(1002, "signed in as %s" % self.SECRETS[4], level="INFO")
                log.add(1003, THROTTLED)
                evidence, record = codex_logs.trial_logs([home], 1000, 1100, Path(folder) / "trial")
            finally:
                log.close()
            self.assertIsNotNone(evidence)   # Read before the scrub: the evidence is the same.
            entry = record["logs"][0]
            self.assertEqual(entry["redacted_rows"], 3)
            data = (Path(folder) / "trial" / entry["copy"]).read_bytes()
            for secret in self.SECRETS:
                self.assertNotIn(secret.encode(), data)
            self.assertIn(b"429 Too Many Requests", data)

    def test_a_copy_that_cannot_be_scrubbed_is_deleted(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder) / "home"
            log = Log(home)
            try:
                log.add(1001, THROTTLED)
                with mock.patch.object(codex_logs, "scrub", return_value=None):
                    evidence, record = codex_logs.trial_logs([home], 1000, 1100, Path(folder) / "trial")
            finally:
                log.close()
            self.assertIsNotNone(evidence)
            self.assertNotIn("copy", record["logs"][0])
            self.assertEqual(list((Path(folder) / "trial").rglob("logs_*")), [])

    def test_redaction_is_stable(self):
        text = '"set-cookie": "a=b", Authorization: Bearer abcdefghij, api_key=xyz'
        once = codex_logs.redact(text)
        self.assertEqual(codex_logs.redact(once), once)
        self.assertNotIn("abcdefghij", once)


class ReportTests(unittest.TestCase):
    def test_unverifiable_trials_are_counted_per_side_and_shown(self):
        meta = [{"side": "angelo", "codex_log_unverifiable": True}, {"side": "angelo", "codex_log_unverifiable": False},
                {"side": "mcp", "codex_log_unverifiable": True}, {"side": "mcp"}]
        counts = codex_logs.unverifiable_counts(meta)
        self.assertEqual(counts, {"angelo": 1, "mcp": 1})
        [line, blank] = bench_mcp_report._unverifiable_lines(counts)
        self.assertIn("angelo 1 trial(s); mcp 1 trial(s)", line)
        self.assertEqual(bench_mcp_report._unverifiable_lines({}), [])


if __name__ == "__main__":
    unittest.main()
