# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Spend caps count what a running request has already reported (``goal_probes.usage_now``), once.

The hidden session's caps (``headless_window_app.used``) and a goal round's budget (``goal_loop``) read one meter: the
usage ledger of finished requests, then the running request's usage the ledger doesn't hold yet. A request is marked
finished before its receipt reaches the ledger (``Controller._finish_metrics``), so reading in that order never counts
one twice. Found by the 2.0.1 audit (P1): a request reporting 1,225 tokens and $0.12 left a hidden session's 1,000-token
and $0.10 caps untouched until its receipt was posted. Contract: doc/angelo/mcp_headless_window.rst, "Caps"."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import queue
import shutil
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import goal_loop, goal_probes, usage_meter
from angelo_agent import headless_window_app as app
from angelo_agent.telemetry import RequestMetrics

MODEL = "claude-opus-5-5"


class InFlightSpendTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.chat = SimpleNamespace(state={"id": "c1", "provider": "claude"}, _request_metrics=None,
                                    telemetry=SimpleNamespace(directory=str(self.root)))
        app._state.update(started=None, spent={})
        self.addCleanup(app._state.update, started=None, spent={})
        while True:
            try:
                app._inbox.get_nowait()
            except queue.Empty:
                break
        self.addCleanup(goal_loop._marks.pop, "c1", None)
        self.addCleanup(goal_loop._rounds.pop, "c1", None)

    def running(self, prompt=1200, output=25):
        """A request in progress that has reported usage, as a provider turn reports it (``provider_usage``)."""
        metrics = RequestMetrics("claude", session_id="c1")
        metrics.set_route(mode="claude", requested_model=MODEL)
        metrics.count("provider_turn_requests")
        metrics.provider_usage({"input_tokens": prompt, "output_tokens": output, "models": [MODEL]})
        self.chat._request_metrics = metrics
        return metrics

    def finish(self):
        """The request ends as ``Controller._finish_metrics`` ends it: finished first, then its receipt counted."""
        receipt = self.chat._request_metrics.finish("completed")
        usage_meter.record(self.chat.telemetry.directory, "c1", receipt, "claude")

    def test_the_hidden_cap_counts_a_running_request(self):
        self.running()
        used = app.used([self.chat])
        self.assertEqual(used["tokens"], 1225, "the running request's reported tokens count before its receipt")
        self.assertGreater(used["usd"], 0.0)
        self.assertEqual(app.cap_reached({"tokens": 1000}, used), "tokens")
        self.assertEqual(app.cap_reached({"usd": used["usd"] / 2}, used), "usd")

    def test_a_request_counts_once_after_it_finishes(self):
        self.running()
        before = app.used([self.chat])
        self.finish()
        after = app.used([self.chat])
        self.assertEqual(after["tokens"], before["tokens"], "its receipt replaces its live usage, never adds to it")
        self.assertAlmostEqual(after["usd"], before["usd"], places=6)

    def test_a_request_finishing_between_the_two_reads_is_never_counted_twice(self):
        self.running()
        ledger_read = goal_probes.spent

        def read_then_finish(controller):   # The request ends on its own thread right after the ledger is read.
            value = ledger_read(controller)
            self.finish()
            return value
        with unittest.mock.patch.object(goal_probes, "spent", read_then_finish):
            tokens, _usd = goal_probes.usage_now(self.chat)
        self.assertLessEqual(tokens, 1225, "the ledger is read first: at most one read misses it, none doubles it")
        self.assertEqual(goal_probes.usage_now(self.chat)[0], 1225, "the next read has it once")

    def test_the_goal_round_and_the_hidden_session_read_one_meter(self):
        self.running()
        goal_loop._marks["c1"] = (0, 0.0)
        with unittest.mock.patch.object(goal_probes, "paid", lambda chat: True):
            round_tokens, round_usd = goal_loop._since_mark(self.chat, live=True)
        used = app.used([self.chat])
        self.assertEqual((round_tokens, round(round_usd, 4)), (used["tokens"], used["usd"]))
        with unittest.mock.patch.object(goal_probes, "usage_now", lambda chat, settled=None: (5000, 9.5)), \
                unittest.mock.patch.object(goal_probes, "paid", lambda chat: True):
            self.assertEqual(app.used([self.chat])["tokens"], 5000)
            self.assertEqual(goal_loop._since_mark(self.chat, live=True), (5000, 9.5))

    def test_a_goal_round_over_its_token_budget_stops_on_live_usage(self):
        self.running(prompt=60_000, output=0)
        goal_loop._marks["c1"] = (0, 0.0)
        goal_loop._rounds["c1"] = {"kind": "goal", "goal": "g1", "started": time.monotonic()}
        goal = {"id": "g1", "budget": {"minutes": 60, "tokens": 50_000, "usd": None},
                "spent": {"working_ms": 0, "tokens": 0, "usd": 0.0}}
        with unittest.mock.patch.object(goal_probes, "paid", lambda chat: False):
            self.assertEqual(goal_loop._over_round_budget(self.chat, goal), "tokens")


if __name__ == "__main__":
    unittest.main()
