# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A worker chat's usage and telemetry: forwarded once by the worker, written by Angelo (design A', C8).

A real ``Controller`` (no provider, no Blender) finishes a request in the "worker" folder; its events are then applied
in the "lead" folder, as Angelo's process would. Lead and worker share this process here, in two folders.
"""

import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import team_usage_reports, telemetry, usage_meter, usage_windows  # noqa: E402
from angelo_agent import worker_usage  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402

FILES = ("performance.json", "usage.json", "team-usage.json")


class Account:
    def __init__(self):
        self.tasks = []

    def count_agent_task(self, receipt):
        self.tasks.append(receipt)


class ForwardingTests(unittest.TestCase):
    def setUp(self):
        self.worker_dir = tempfile.TemporaryDirectory()
        self.lead_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.worker_dir.cleanup)
        self.addCleanup(self.lead_dir.cleanup)
        self.addCleanup(self._forget)
        self.events = []
        worker_usage.install(self.events.append)
        worker_usage.cover(self.worker_dir.name)
        self.dropped = worker_usage.dropped

    def _forget(self):
        worker_usage.uninstall()
        usage_windows.clear()
        team_usage_reports.attach(None)
        for folder in (self.worker_dir.name, self.lead_dir.name):
            resolved = str(Path(folder).resolve())
            if resolved in telemetry._STORES:   # Its background writes end before the folder is removed.
                telemetry._STORES[resolved].flush()
            usage_meter.drain(folder, timeout=2.0)
            telemetry._STORES.pop(resolved, None)
            for key in (folder, resolved):
                usage_meter._LEDGERS.pop(key, None)

    def _finish_request(self):
        controller = Controller(self.worker_dir.name, session_id=str(uuid.uuid4()))
        self.addCleanup(controller.close)
        self.assertTrue(worker_usage.covered(controller))
        controller._begin_metrics()
        metrics = controller._request_metrics
        metrics.set_route(mode="claude_team", requested_model="claude-sonnet-4-6")
        metrics.count("provider_turn_requests")
        metrics.provider_usage({"input_tokens": 120, "output_tokens": 30})
        controller._finish_metrics("completed")
        return controller

    def _worker_wrote_nothing(self):
        telemetry.TelemetryStore.shared(self.worker_dir.name).flush(timeout=.2)
        usage_meter.drain(self.worker_dir.name)
        for name in FILES:
            self.assertFalse((Path(self.worker_dir.name) / name).exists(), name)

    def test_a_finished_request_is_forwarded_once_and_never_written_in_the_worker(self):
        controller = self._finish_request()
        kinds = sorted((event["kind"], event["data"]["record"]) for event in self.events)
        self.assertEqual(kinds, [("telemetry", "receipt"), ("usage", "meter"), ("usage", "team_task")])
        self.assertTrue(all(event["session"] == controller.state["id"] for event in self.events))
        self.assertEqual(len({event["data"]["id"] for event in self.events}), 3)
        self._worker_wrote_nothing()
        # The worker's own reads still see it (the agent's metrics tool).
        self.assertEqual(len(controller.telemetry.receipts), 1)
        self.assertIsNotNone(usage_meter.UsageLedger.shared(self.worker_dir.name).chat(controller.state["id"]))

    def test_the_lead_writes_each_record_once_through_its_own_stores(self):
        controller = self._finish_request()
        events = list(self.events)
        worker_usage.uninstall()   # Now "Angelo's process".
        account = Account()
        team_usage_reports.attach(account)
        lead = self.lead_dir.name
        self.assertEqual([worker_usage.apply(event, lead) for event in events], [True, True, True])
        self.assertEqual([worker_usage.apply(event, lead) for event in events], [False, False, False])   # Once.
        store = telemetry.TelemetryStore.shared(lead)
        self.assertTrue(store.flush())
        self.assertEqual(len(telemetry.TelemetryStore(lead).receipts), 1)
        ledger = usage_meter.UsageLedger.shared(lead)
        self.assertTrue(ledger.flush())
        self.assertEqual(usage_meter.UsageLedger(lead).chat(controller.state["id"])["modes"]["claude_team"]["output"],
                         30)
        self.assertEqual(len(account.tasks), 1)
        self._worker_wrote_nothing()

    def test_a_warm_up_receipt_is_forwarded_too(self):
        receipt = {"kind": "warm_up", "status": "completed", "runtime": "codex", "phases": {"total_ms": 5.0},
                   "route": {"mode": "codex_subscription"}, "usage": {"input_tokens": 9, "turns": 1}}
        from angelo_agent.codex_bases import BaseStore
        BaseStore(self.worker_dir.name).record_warm_up(receipt)
        kinds = sorted(event["data"]["record"] for event in self.events)
        self.assertEqual(kinds, ["meter", "receipt"])
        meter = next(event for event in self.events if event["data"]["record"] == "meter")
        self.assertIsNone(meter["data"]["chat"])
        self._worker_wrote_nothing()
        worker_usage.uninstall()
        for event in self.events:
            self.assertTrue(worker_usage.apply(event, self.lead_dir.name))
        self.assertEqual(usage_meter.UsageLedger.shared(self.lead_dir.name).since_started()["chats"], 0)

    def test_rate_windows_are_forwarded_and_kept_by_the_lead(self):
        context = SimpleNamespace(state={"connection_id": "conn-1", "provider": "codex_subscription"})
        params = {"rateLimits": {"primary": {"usedPercent": 40, "windowDurationMins": 300, "resetsAt": 4102444800}}}
        self.assertTrue(usage_windows.observe_codex_event(context, "account/rateLimits/updated", params))
        usage_windows.observe_claude_event(context, {"type": "rate_limit_event", "rate_limit_info": {
            "status": "allowed", "rateLimitType": "five_hour", "utilization": 0.5, "resetsAt": 4102444800}})
        self.assertIsNone(usage_windows.current("conn-1"))   # Not kept in the worker.
        self.assertEqual([event["data"]["runtime"] for event in self.events], ["codex", "claude"])
        worker_usage.uninstall()
        for event in self.events:
            self.assertTrue(worker_usage.apply(event, self.lead_dir.name))
        kinds = {window["kind"] for window in usage_windows.current("conn-1")["windows"]}
        self.assertEqual(kinds, {"primary", "five_hour"})

    def test_a_record_that_cannot_be_sent_is_dropped_never_written(self):
        worker_usage.uninstall()
        worker_usage.install(lambda event: (_ for _ in ()).throw(OSError("channel closed")))
        self._finish_request()
        self.assertEqual(worker_usage.dropped - self.dropped, 3)
        self._worker_wrote_nothing()

    def test_a_chat_is_not_covered_until_its_folder_is(self):
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        self.addCleanup(telemetry._STORES.pop, str(Path(other.name).resolve()), None)
        controller = Controller(other.name, session_id=str(uuid.uuid4()))
        self.addCleanup(controller.close)
        self.assertFalse(worker_usage.covered(controller))
        worker_usage.uninstall()
        with self.assertRaises(RuntimeError):
            worker_usage.cover(other.name)

    def test_apply_refuses_unknown_and_malformed_records_and_never_in_a_worker(self):
        lead = self.lead_dir.name
        good = {"kind": "telemetry", "data": {"id": uuid.uuid4().hex, "record": "receipt",
                                              "receipt": {"status": "completed", "phases": {"total_ms": 1.0}}}}
        self.assertFalse(worker_usage.apply(good, lead))   # Forwarding is installed: this is a worker.
        worker_usage.uninstall()
        for event in ({"kind": "other", "data": {"id": "a"}}, {"kind": "usage", "data": {"record": "meter"}},
                      {"kind": "usage", "data": {"id": "b", "record": "meter", "chat": 5}},
                      {"kind": "usage", "data": {"id": "c", "record": "windows", "runtime": "x", "key": "k"}},
                      {"kind": "usage", "data": {"id": "d", "record": "unknown"}}, None):
            self.assertFalse(worker_usage.apply(event, lead))
        self.assertTrue(worker_usage.apply(good, lead))


if __name__ == "__main__":
    unittest.main()
