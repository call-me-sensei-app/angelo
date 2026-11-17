# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Agent usage reports for custom endpoints: per endpoint, format and model, tasks and tokens, never content.

Dependency-free: receipts come from the real metrics path (``RequestMetrics``), the buffer runs on a temporary
folder with a fixed clock, and the account's counting runs on ``UsageReportTasks`` with a stand-in account.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import custom_endpoints, endpoint_connections, team_usage_buffer as buffer  # noqa: E402
from angelo_agent.endpoint_connections import mode_for  # noqa: E402
from angelo_agent.telemetry import RequestMetrics  # noqa: E402
from angelo_agent.team_usage_reports import UsageReportTasks, endpoint_entry  # noqa: E402
from angelo_agent.team_view import AccountView  # noqa: E402
from fake_usage_reports import parse  # noqa: E402

NOW = 1_791_633_600.0          # 2026-10-10T12:00:00Z
TODAY = "2026-10-10"
OWNER = ("https://console.angelo.studio", "org-1", "user-1")
IDENTITY = "5d0a8a43-0b7e-4f37-9c55-6c1b5b7c2a10"
HEX = IDENTITY.replace("-", "")
USAGE = {"input_tokens": 1200, "cached_input_tokens": 1000, "output_tokens": 80, "reasoning_output_tokens": 30}
DESCRIPTION = {"id": IDENTITY, "name": "Example Proxy", "baseUrl": "https://llm.example.test",
               "formats": {"openai-responses": {"path": "/v1"}, "anthropic-messages": {"path": ""}},
               "models": [{"id": "team-gpt-sol", "format": "openai-responses", "profile": "gpt-6.1-sol"},
                          {"id": "house-model", "format": "openai-responses"},
                          {"id": "team-claude", "format": "anthropic-messages", "profile": "claude-sonnet-5-5"}]}


def receipt(mode, status="completed", requested="team-gpt-sol", seconds=12.0, usage=USAGE):
    """A receipt from the real metrics path, ``seconds`` long, with the runtime's usage for one turn (a turn is
    counted, as the chat does, or the receipt keeps no usage: ``RequestMetrics._complete``)."""
    ticks = iter([100.0, 100.0 + seconds])
    metrics = RequestMetrics("codex", clock=lambda: next(ticks))
    metrics.set_route(mode=mode, requested_model=requested)
    if usage:
        metrics.count("provider_turn_requests")
        metrics.provider_usage(dict(usage))
    return metrics.finish(status)


class Endpoints:
    def __enter__(self):
        endpoint_connections.set_current(custom_endpoints.endpoints([DESCRIPTION])[0], True)

    def __exit__(self, *exc):
        endpoint_connections.set_current(())


class EntryTests(unittest.TestCase):
    def test_a_completed_request_is_a_task_with_its_tokens_and_its_models_profile(self):
        with Endpoints():
            key, value = endpoint_entry(receipt(mode_for(HEX, "openai-responses")))
        self.assertEqual(key, (HEX, "openai-responses", "team-gpt-sol", "gpt-6.1-sol"))
        self.assertEqual(value, (1, 1200, 1000, 80, 30))

    def test_an_unmapped_model_is_untested_and_a_stopped_request_counts_tokens_only(self):
        with Endpoints():
            key, value = endpoint_entry(receipt(mode_for(HEX, "openai-responses"), "interrupted", "house-model"))
            self.assertEqual((key[3], value), ("", (0, 1200, 1000, 80, 30)))   # "" is sent as null: untested.
            self.assertIsNone(endpoint_entry(receipt(mode_for(HEX, "openai-responses"), "failed", usage=None)))
            claude = endpoint_entry(receipt(mode_for(HEX, "anthropic-messages"), requested="team-claude"))
        self.assertEqual(claude[0][1:], ("anthropic-messages", "team-claude", "claude-sonnet-5-5"))

    def test_other_connections_are_not_endpoint_entries_and_nothing_but_counts_is_read(self):
        self.assertIsNone(endpoint_entry(receipt("codex_team", requested="gpt-6.1-sol")))
        self.assertIsNone(endpoint_entry(receipt("codex_api", requested="gpt-6.1-sol")))
        with Endpoints():
            found = endpoint_entry(dict(receipt(mode_for(HEX, "openai-responses")),
                                        prompt="secret text", path="/Users/x/scene.blend"))
        self.assertNotIn("secret", json.dumps(found))
        self.assertNotIn("/Users", json.dumps(found))

    def test_a_removed_endpoint_still_reports_what_it_used(self):
        with Endpoints():
            pass   # Removed before the request's receipt was counted.
        key, _value = endpoint_entry(receipt(mode_for(HEX, "openai-responses")))
        self.assertEqual(key[3], "")


class BufferTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.buffer = buffer.UsageBuffer(self.folder.name)

    def key(self, model="team-gpt-sol", profile="gpt-6.1-sol"):
        return (TODAY, HEX, "openai-responses", model, profile)

    def test_endpoint_counts_add_up_survive_a_restart_and_form_batches_of_their_own(self):
        self.buffer.add(OWNER, {(TODAY, "anthropic", "claude-opus-5-5"): (1, 9000)}, NOW,
                        {self.key(): (1, 1200, 1000, 80, 30)})
        self.buffer.add(OWNER, {}, NOW, {self.key(): (0, 300, 0, 20, 5), self.key(profile=""): (1, 10, 0, 1, 0)})
        restarted = buffer.UsageBuffer(self.folder.name)   # Offline: kept in the shared file until a report lands.
        self.assertEqual(restarted.pending_endpoints(OWNER, NOW),
                         {self.key(): [1, 1500, 1000, 100, 35], self.key(profile=""): [1, 10, 0, 1, 0]})
        first = restarted.take(OWNER, NOW)
        self.assertEqual(set(first), {"batchId", "entries"})   # Gateway counts first, alone.
        restarted.settle(OWNER, first["batchId"], "sent", NOW)
        second = restarted.take(OWNER, NOW, scheduled=False)
        self.assertEqual((set(second), second["entries"]), ({"batchId", "entries", "customEndpoints"}, []))
        self.assertEqual(second["customEndpoints"], [   # One item per profile that ran: a remap keeps both rows.
            {"day": TODAY, "endpointId": HEX, "format": "openai-responses", "model": "team-gpt-sol",
             "profile": None, "agentTasks": 1, "inputTokens": 10, "cachedInputTokens": 0, "outputTokens": 1,
             "reasoningTokens": 0},
            {"day": TODAY, "endpointId": HEX, "format": "openai-responses", "model": "team-gpt-sol",
             "profile": "gpt-6.1-sol", "agentTasks": 1, "inputTokens": 1500, "cachedInputTokens": 1000,
             "outputTokens": 100, "reasoningTokens": 35}])
        self.assertIsInstance(parse(second), dict)   # What the console's appUsageReportBody accepts.
        retry = NOW + buffer.LEASE + 1   # After the send's lease: the same batch again, until it lands.
        self.assertEqual(restarted.take(OWNER, retry, scheduled=False)["batchId"], second["batchId"])
        self.assertFalse(restarted.settle(OWNER, second["batchId"], "drop", retry))

    def test_a_batch_holds_200_entries_and_bad_values_never_enter_it(self):
        many = {self.key("m%03d" % i, ""): (1, 10, 0, 1, 0) for i in range(250)}
        self.buffer.add(OWNER, {}, NOW, many)
        batch = self.buffer.take(OWNER, NOW)
        self.assertEqual(len(batch["customEndpoints"]), buffer.MAX_ENTRIES)
        self.assertIsInstance(parse(batch), dict)
        for bad in (("not-hex", "openai-responses", "m", "", 1, 1, 0, 0, 0), (HEX, "chat", "m", "", 1, 1, 0, 0, 0),
                    (HEX, "gemini", "two words", "", 1, 1, 0, 0, 0), (HEX, "gemini", "m", "", 1, -1, 0, 0, 0),
                    (HEX, "gemini", "m", "", 1, 5, 6, 0, 0),    # cached input above input
                    (HEX, "gemini", "m", "", 1, 5, 0, 1, 2),    # reasoning above output
                    (HEX, "gemini", "m", "", 1, 10 ** 10 + 1, 0, 0, 0)):
            with self.subTest(bad=bad):
                self.assertIsNone(buffer.endpoint_entry(TODAY, *bad))
        stored = json.loads(Path(self.folder.name, buffer.FILE).read_text())
        self.assertNotIn("prompt", json.dumps(stored))


class ParkTests(unittest.TestCase):
    """A console older than ``customEndpoints`` answers 400: the batch is parked and sent again for 6 hours."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.buffer = buffer.UsageBuffer(self.folder.name)
        self.buffer.add(OWNER, {}, NOW, {(TODAY, HEX, "gemini", "m", ""): (1, 10, 0, 1, 0)})

    def test_a_refused_endpoint_batch_waits_behind_gateway_counts_and_returns_on_schedule(self):
        first = self.buffer.take(OWNER, NOW)
        self.assertFalse(self.buffer.settle(OWNER, first["batchId"], "park", NOW))
        self.buffer.add(OWNER, {(TODAY, "anthropic", "claude-opus-5-5"): (1, 9000)}, NOW,
                        {(TODAY, HEX, "gemini", "n", ""): (1, 5, 0, 1, 0)})
        self.assertIsNone(self.buffer.take(OWNER, NOW + 60))   # Scheduled: waits for the next report.
        gateway = self.buffer.take(OWNER, NOW + 60, scheduled=False)
        self.assertEqual(set(gateway), {"batchId", "entries"})   # Gateway counts are never held back by it.
        self.buffer.settle(OWNER, gateway["batchId"], "sent", NOW + 60)
        later = NOW + 60 + buffer.SPACING
        again = buffer.UsageBuffer(self.folder.name).take(OWNER, later)   # Kept in the file across a restart.
        self.assertEqual(again, first)   # The same batch and id: the console adds it at most once.
        self.buffer.settle(OWNER, again["batchId"], "park", later)
        self.buffer.settle(OWNER, self.buffer.take(OWNER, later, scheduled=False)["batchId"], "sent", later)
        self.assertEqual(self.buffer.pending_endpoints(OWNER, later), {(TODAY, HEX, "gemini", "n", ""): [1, 5, 0, 1, 0]})

    def test_after_six_hours_it_is_dropped_and_newer_counts_go(self):
        first = self.buffer.take(OWNER, NOW)
        self.buffer.settle(OWNER, first["batchId"], "park", NOW)
        self.buffer.add(OWNER, {}, NOW, {(TODAY, HEX, "gemini", "n", ""): (1, 5, 0, 1, 0)})
        late = NOW + buffer.PARK + 1
        newer = self.buffer.take(OWNER, late)
        self.assertNotEqual(newer["batchId"], first["batchId"])
        self.assertEqual([i["model"] for i in newer["customEndpoints"]], ["n"])
        self.buffer.settle(OWNER, newer["batchId"], "sent", late)
        self.assertIsNone(self.buffer.take(OWNER, late + buffer.SPACING))

    def test_a_400_parks_an_endpoint_batch_and_still_drops_a_gateway_batch(self):
        tasks = UsageReportTasks()
        tasks.__dict__.update(_lock=threading.Lock(), console_url=OWNER[0], _clock=lambda: NOW,
                              _usage_buffer=self.buffer,
                              _transport=lambda *args: (400, {}, None))
        endpoint_batch = self.buffer.take(OWNER, NOW)
        self.assertEqual(tasks._send_usage(OWNER, endpoint_batch, token="fixture-token"), (False, False))
        self.assertIsNone(self.buffer.take(OWNER, NOW, scheduled=True))
        self.assertEqual(self.buffer.take(OWNER, NOW, scheduled=False)["batchId"], endpoint_batch["batchId"])
        self.buffer.add(OWNER, {(TODAY, "anthropic", "claude-opus-5-5"): (1, 9000)}, NOW)
        self.buffer.settle(OWNER, endpoint_batch["batchId"], "park", NOW)
        gateway = self.buffer.take(OWNER, NOW, scheduled=False)
        tasks._send_usage(OWNER, gateway, token="fixture-token")
        self.assertEqual(self.buffer.pending(OWNER, NOW), ({}, None))   # Dropped, as a 400 always did.


class AccountCountingTests(unittest.TestCase):
    """``UsageReportTasks`` with a stand-in account: counting is attributed to the signed-in member, no I/O."""

    def account(self, status="signed_in"):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        tasks = UsageReportTasks()
        enqueued = []
        tasks.__dict__.update(_lock=threading.Lock(), console_url=OWNER[0], _clock=lambda: NOW,
                              _view=AccountView(status=status, org_id=OWNER[1], user_id=OWNER[2], seat=True),
                              _usage_pending={}, _usage_pending_endpoints={}, _usage_saving=threading.Lock(),
                              _usage_buffer=buffer.UsageBuffer(folder.name), _enqueue=enqueued.append)
        return tasks, enqueued

    def test_endpoint_requests_are_counted_for_the_member_and_saved(self):
        tasks, enqueued = self.account()
        with Endpoints():
            self.assertTrue(tasks.count_agent_task(receipt(mode_for(HEX, "openai-responses"))))
            self.assertTrue(tasks.count_agent_task(receipt(mode_for(HEX, "openai-responses"), "failed")))
        self.assertEqual(enqueued, ["usage_save", "usage_save"])
        self.assertTrue(tasks._save_usage())
        counts = tasks._usage_buffer.pending_endpoints(OWNER, NOW)
        self.assertEqual(counts[(TODAY, HEX, "openai-responses", "team-gpt-sol", "gpt-6.1-sol")],
                         [1, 2400, 2000, 160, 60])

    def test_nothing_is_counted_without_a_team_sign_in(self):
        tasks, enqueued = self.account(status="signed_out")
        with Endpoints():
            self.assertFalse(tasks.count_agent_task(receipt(mode_for(HEX, "openai-responses"))))
        self.assertEqual(enqueued, [])


if __name__ == "__main__":
    unittest.main()
