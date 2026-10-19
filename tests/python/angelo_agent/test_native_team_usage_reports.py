# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Agent usage reports: what counts as a task, the shared buffer, and the console's report shape.

Dependency-free: no account, network or Blender. ``task_entry`` reads real
receipts (``RequestMetrics.finish``); ``UsageBuffer`` runs on a temporary
folder with a fixed clock, including a real process killed mid-write; the
fake console's parser (``fake_usage_reports.parse``) is checked against the
contract's zod rules, and every body the buffer forms passes it. The account,
the console round trip, quit and several processes are in
``test_native_team_usage_flush``.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import team_lock, team_usage_buffer as buffer  # noqa: E402
from angelo_agent.telemetry import RequestMetrics  # noqa: E402
from angelo_agent.team_usage_reports import TEAM_CODING, outcome, task_entry, task_model  # noqa: E402
from fake_usage_reports import parse  # noqa: E402

NOW = 1_791_633_600.0          # 2026-10-10T12:00:00Z
TODAY = "2026-10-10"
OWNER = ("https://console.angelo.studio", "6b0f3c1e-2d4a-4c8e-9f10-1a2b3c4d5e6f",
         "f1e2d3c4-b5a6-4978-8a9b-0c1d2e3f4a5b")
DAY = 86400


def receipt(mode="claude_team", status="completed", requested="claude-opus-5-5", served=(), seconds=95.0):
    """A receipt from the real metrics path, ``seconds`` long."""
    ticks = iter([100.0, 100.0 + seconds])
    metrics = RequestMetrics("claude", clock=lambda: next(ticks))
    metrics.set_route(mode=mode, requested_model=requested)
    if served:
        metrics.provider_usage({"models": list(served)})
    return metrics.finish(status)


class TaskEntryTests(unittest.TestCase):
    def test_a_completed_team_request_is_one_task_with_its_wall_time(self):
        self.assertEqual(task_entry(receipt()), ("anthropic", "claude-opus-5-5", 95000))
        self.assertEqual(task_entry(receipt("codex_team", requested="gpt-6.1-sol", seconds=0.4)),
                         ("openai", "gpt-6.1-sol", 400))
        self.assertEqual(TEAM_CODING, {"claude_team": "anthropic", "codex_team": "openai", "grok_team": "xai",
                                       "minimax_team": "minimax", "kimi_team": "moonshot"})

    def test_personal_connections_and_unfinished_requests_are_not_tasks(self):
        for mode in ("claude_subscription", "claude_api", "codex_subscription", "codex_api", "grok_api"):
            with self.subTest(mode=mode):
                self.assertIsNone(task_entry(receipt(mode)))
        for status in ("failed", "interrupted", "cancelled", "escalated"):
            with self.subTest(status=status):
                self.assertIsNone(task_entry(receipt(status=status)))
        local = RequestMetrics("claude").finish("completed")  # A preview acceptance: no route.
        self.assertIsNone(task_entry(local))
        self.assertIsNone(task_entry(None))

    def test_the_model_is_the_one_the_runtime_served(self):
        cases = [  # (requested, served, reported)
            ("opus", ("claude-haiku-5-5", "claude-opus-5-5"), "claude-opus-5-5"),  # alias; a fast chore model too
            ("claude-opus-5-5", ("claude-opus-5-5-20261001",), "claude-opus-5-5-20261001"),
            ("claude-opus-5-5", ("claude-sonnet-5-5",), "claude-sonnet-5-5"),  # the runtime's fallback served it
            ("", ("claude-haiku-5-5", "claude-sonnet-5-5"), "claude-sonnet-5-5"),
            ("", ("gpt-6.1-sol",), "gpt-6.1-sol"),
            ("gpt-6-astra", (), "gpt-6-astra"),
            ("", (), "unknown"),
        ]
        for requested, served, reported in cases:
            with self.subTest(requested=requested, served=served):
                route = {"requested_model": requested, "served_models": list(served)}
                self.assertEqual(task_model(route), reported)

    def test_a_task_longer_than_a_day_is_capped(self):
        self.assertEqual(task_entry(receipt(seconds=2 * DAY))[2], buffer.MAX_TASK_MS)

    def test_outcomes(self):
        self.assertEqual([outcome(s) for s in (200, 201, 400, 403, 404, 401, 429, 500, 503, None)],
                         ["sent", "sent", "drop", "drop", "drop", "retry", "retry", "retry", "retry", "retry"])


class BufferCase(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.buffer = buffer.UsageBuffer(self.folder.name)

    def add(self, counts, now=NOW, owner=OWNER):
        self.buffer.add(owner, counts, now)

    def stored(self):
        return json.loads(Path(self.folder.name, buffer.FILE).read_text())


class BufferTests(BufferCase):
    def test_counts_become_one_batch_whose_id_stays_until_it_lands(self):
        self.add({(TODAY, "anthropic", "claude-opus-5-5"): (2, 95000), (TODAY, "openai", "gpt-6.1-sol"): (1, None)})
        self.add({(TODAY, "anthropic", "claude-opus-5-5"): (1, 20000)})
        batch = self.buffer.take(OWNER, NOW)
        self.assertEqual(str(uuid.UUID(batch["batchId"])), batch["batchId"])
        self.assertEqual(batch["entries"], [
            {"day": TODAY, "provider": "anthropic", "model": "claude-opus-5-5", "agentTasks": 3,
             "longestTaskMs": 95000},
            {"day": TODAY, "provider": "openai", "model": "gpt-6.1-sol", "agentTasks": 1, "longestTaskMs": None}])
        self.assertIsNone(self.buffer.take(OWNER, NOW + 1), "another process leaves a batch being sent alone")
        # The answer was lost: new tasks go to the next batch; the retry is the same body.
        self.add({(TODAY, "anthropic", "claude-opus-5-5"): (4, 1)}, NOW + 2)
        self.assertTrue(self.buffer.settle(OWNER, batch["batchId"], "retry", NOW + 3))
        self.assertEqual(self.buffer.take(OWNER, NOW + 4), batch)
        self.assertTrue(self.buffer.settle(OWNER, batch["batchId"], "sent", NOW + 5), "the later tasks wait")
        self.assertIsNone(self.buffer.take(OWNER, NOW + 6), "a scheduled report waits for the shared spacing")
        later = self.buffer.take(OWNER, NOW + 7, scheduled=False, lease=0)  # The report at quit doesn't.
        self.assertNotEqual(later["batchId"], batch["batchId"])
        self.assertEqual([(e["agentTasks"], e["longestTaskMs"]) for e in later["entries"]], [(4, 1)])
        self.assertFalse(self.buffer.settle(OWNER, later["batchId"], "sent", NOW + 8))
        self.add({(TODAY, "openai", "gpt-6.1-sol"): (1, 1)}, NOW + 9)
        self.assertIsNone(self.buffer.take(OWNER, NOW + 7 + buffer.SPACING))
        self.assertIsNotNone(self.buffer.take(OWNER, NOW + 8 + buffer.SPACING))

    def test_a_report_holds_200_entries_and_the_rest_follow(self):
        self.add({(TODAY, "anthropic", "model-%03d" % i): (1, i) for i in range(250)})
        first = self.buffer.take(OWNER, NOW)
        self.assertEqual(len(first["entries"]), 200)
        self.assertEqual(first["entries"][0]["model"], "model-000")
        self.assertTrue(self.buffer.settle(OWNER, first["batchId"], "sent", NOW))
        second = self.buffer.take(OWNER, NOW, scheduled=False)
        self.assertEqual(len(second["entries"]), 50)
        self.assertEqual({e["model"] for e in first["entries"] + second["entries"]},
                         {"model-%03d" % i for i in range(250)})
        for body in (first, second):
            self.assertIsInstance(parse(json.loads(json.dumps(body))), dict)

    def test_only_days_the_console_still_counts_are_kept(self):
        days = {offset: buffer.day_of(NOW + offset * DAY) for offset in (-31, -30, 0, 1, 2)}
        self.add({(days[o], "anthropic", "m"): (1, 1) for o in days})
        counts, _batch = self.buffer.pending(OWNER, NOW)
        self.assertEqual(sorted(k[0] for k in counts), [days[-30], days[0], days[1]])
        batch = self.buffer.take(OWNER, NOW)
        self.buffer.settle(OWNER, batch["batchId"], "retry", NOW)
        # Offline for a month: a batch that would add nothing is dropped, and the file goes away.
        self.assertEqual(self.buffer.pending(OWNER, NOW + 33 * DAY), ({}, None))
        self.add({(days[0], "anthropic", "m"): (1, 1)}, NOW + 33 * DAY)
        self.assertFalse(Path(self.folder.name, buffer.FILE).exists())

    def test_429_holds_every_process_until_retry_after(self):
        self.add({(TODAY, "anthropic", "m"): (1, 1)})
        batch = self.buffer.take(OWNER, NOW)
        self.buffer.settle(OWNER, batch["batchId"], "retry", NOW, hold=120)
        self.assertIsNone(self.buffer.take(OWNER, NOW + 119, scheduled=False, lease=0))
        self.assertEqual(self.buffer.take(OWNER, NOW + 120), batch)
        # A hold far in the future is a clock that moved back, not a reason to stop reporting.
        self.buffer.settle(OWNER, batch["batchId"], "retry", NOW, hold=10 ** 6)
        self.assertEqual(self.stored()["owners"][0]["hold_until"], NOW + buffer.MAX_HOLD)

    def test_counts_are_kept_per_person_and_bounded(self):
        other = (OWNER[0], OWNER[1], "0e9d8c7b-6a5f-4e3d-8c2b-1a0f9e8d7c6b")
        self.add({(TODAY, "anthropic", "m"): (buffer.MAX_TASKS - 1, buffer.MAX_TASK_MS)})
        self.add({(TODAY, "anthropic", "m"): (5, 1)})
        self.add({(TODAY, "openai", "g"): (1, 1)}, owner=other)
        self.assertEqual(self.buffer.pending(OWNER, NOW)[0],
                         {(TODAY, "anthropic", "m"): [buffer.MAX_TASKS, buffer.MAX_TASK_MS]})
        self.assertEqual(list(self.buffer.pending(other, NOW)[0]), [(TODAY, "openai", "g")])
        for index in range(buffer.MAX_OWNERS + 2):
            self.add({(TODAY, "openai", "g"): (1, 1)}, NOW + index + 1, owner=("c", "o", "u%d" % index))
        self.assertEqual(len(self.stored()["owners"]), buffer.MAX_OWNERS)
        self.add({(TODAY, "x", "m%d" % i): (1, 1) for i in range(buffer.MAX_KEYS + 10)}, NOW + 99)
        self.assertEqual(len(self.buffer.pending(OWNER, NOW + 99)[0]), buffer.MAX_KEYS)

    def test_the_file_is_private_small_and_never_trusted(self):
        self.add({(TODAY, "anthropic", "claude-opus-5-5"): (1, 5)})
        path = Path(self.folder.name, buffer.FILE)
        if os.name != "nt":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(set(self.stored()["owners"][0]), {"console", "org", "user", "updated", "hold_until",
                                                           "next_at", "batch", "counts"})
        # A hand-edited batch with an extra field (a token, say) is never sent; damaged files read as empty.
        value = self.stored()
        value["owners"][0]["batch"] = {"batchId": str(uuid.uuid4()), "entries": [
            {"day": TODAY, "provider": "anthropic", "model": "m", "agentTasks": 1, "longestTaskMs": 1,
             "inputTokens": 9}]}
        path.write_text(json.dumps(value))
        self.assertEqual(self.buffer.take(OWNER, NOW)["entries"][0]["agentTasks"], 1)
        self.assertNotIn("inputTokens", path.read_text())
        for damaged in ("{", "[]", json.dumps({"version": 2, "owners": value["owners"]}), "\xff"):
            path.write_text(damaged, encoding="latin-1")
            self.assertEqual(self.buffer.pending(OWNER, NOW), ({}, None))
        mtime = path.stat().st_mtime_ns
        self.assertIsNone(self.buffer.take(OWNER, NOW))  # Nothing pending: the damaged file is left as it is.
        self.assertEqual(path.stat().st_mtime_ns, mtime)


_STALLED_WRITER = """
import os, sys, time
sys.path.insert(0, sys.argv[1])
from angelo_agent import team_usage_buffer as buffer
folder, now = sys.argv[2], float(sys.argv[3])
def stall(source, target):  # The process dies between writing the new file and replacing the old one.
    open(os.path.join(folder, "stalled"), "w").close()
    time.sleep(60)
os.replace = stall
buffer.UsageBuffer(folder).add(tuple(sys.argv[4:7]), {("%s", "anthropic", "m"): (5, 10)}, now)
"""


class CrashTests(BufferCase):
    def test_a_failed_write_leaves_the_previous_file(self):
        self.add({(TODAY, "anthropic", "m"): (1, 1)})
        before = Path(self.folder.name, buffer.FILE).read_text()
        with patch("angelo_agent.team_usage_buffer.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.add({(TODAY, "anthropic", "m"): (5, 1)})
        self.assertEqual(Path(self.folder.name, buffer.FILE).read_text(), before)
        self.assertEqual(buffer.UsageBuffer(self.folder.name).pending(OWNER, NOW)[0],
                         {(TODAY, "anthropic", "m"): [1, 1]})

    def test_a_process_killed_mid_write_leaves_a_complete_file_and_no_lock(self):
        self.add({(TODAY, "anthropic", "m"): (1, 1)})
        before = Path(self.folder.name, buffer.FILE).read_text()
        child = subprocess.Popen([sys.executable, "-c", _STALLED_WRITER % TODAY, str(ROOT / "scripts/addons_core"),
                                  self.folder.name, str(NOW), *OWNER])
        self.addCleanup(child.wait)
        deadline = time.monotonic() + 30
        while not Path(self.folder.name, "stalled").exists():
            self.assertLess(time.monotonic(), deadline, "the child never reached its write")
            time.sleep(0.01)
        self.assertTrue(Path(self.folder.name, buffer.FILE + ".tmp").exists())
        with self.assertRaises(team_lock.LockTimeout):  # It holds the lock while it writes.
            buffer.UsageBuffer(self.folder.name, lock_timeout=0.2).pending(OWNER, NOW)
        child.kill()
        child.wait(10)
        self.assertEqual(Path(self.folder.name, buffer.FILE).read_text(), before)
        started = time.monotonic()
        self.add({(TODAY, "anthropic", "m"): (2, 3)})  # The OS released its lock; the leftover is overwritten.
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(self.buffer.pending(OWNER, NOW)[0], {(TODAY, "anthropic", "m"): [3, 3]})


class ContractShapeTests(unittest.TestCase):
    """``fake_usage_reports.parse`` reads a body as ``appUsageReportBody`` does."""

    ENTRY = {"day": TODAY, "provider": "anthropic", "model": "claude-opus-5-5", "agentTasks": 3, "longestTaskMs": 95000}

    def body(self, *entries, **fields):
        return dict({"batchId": str(uuid.uuid4()), "entries": list(entries) or [dict(self.ENTRY)]}, **fields)

    def test_valid_bodies(self):
        self.assertIsInstance(parse(self.body()), dict)
        without = {k: v for k, v in self.ENTRY.items() if k != "longestTaskMs"}
        self.assertIsNone(parse(self.body(without))["entries"][0]["longestTaskMs"], "default(null)")
        trimmed = parse(self.body(dict(self.ENTRY, provider=" anthropic ")))
        self.assertEqual(trimmed["entries"][0]["provider"], "anthropic")
        self.assertEqual(parse(self.body(dict(self.ENTRY, agentTasks=3.0)))["entries"][0]["agentTasks"], 3)

    def test_refused_bodies(self):
        refused = [
            self.body(inputTokens=1),                                            # strict body
            self.body(dict(self.ENTRY, estCost=1)),                              # strict entry: tokens and cost never
            self.body(self.ENTRY, dict(self.ENTRY, provider=" anthropic")),      # the same day, provider and model
            dict(self.body(), batchId="not-a-uuid"), {"entries": [self.ENTRY]},
            self.body(*[dict(self.ENTRY, model="m%d" % i) for i in range(201)]), dict(self.body(), entries=[]),
            self.body(dict(self.ENTRY, day="2026-10-1")), self.body(dict(self.ENTRY, provider="")),
            self.body(dict(self.ENTRY, model="m" * 201)), self.body(dict(self.ENTRY, agentTasks=True)),
            self.body(dict(self.ENTRY, agentTasks=10_001)), self.body(dict(self.ENTRY, agentTasks=1.5)),
            self.body(dict(self.ENTRY, longestTaskMs=86_400_001)), self.body(dict(self.ENTRY, longestTaskMs=-1)),
        ]
        for body in refused:
            with self.subTest(body=json.dumps(body)[:120]):
                self.assertIsInstance(parse(body), list)


if __name__ == "__main__":
    unittest.main()
