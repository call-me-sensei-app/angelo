# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_usage``'s figures (``external_usage``) from real receipts, without Blender.

Receipts go through ``telemetry.clean_receipt`` and are priced by the real ``usage_meter`` and ``model_profiles``,
so a turn's estimate is the one the chat's usage meter shows. Contract: doc/angelo/external_mcp.rst, "Usage".
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
import uuid
from pathlib import Path
import tempfile
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import external_status as status  # noqa: E402
from angelo_agent import external_usage as usage  # noqa: E402
from angelo_agent import model_profiles, telemetry, usage_windows  # noqa: E402

CHAT, OTHER = str(uuid.uuid4()), str(uuid.uuid4())


def chat(test, **state):
    """A real Controller with ``state`` (never a stand-in: a method the code newly reads is the real one, or fails by
    name; 2.0.1, e528acda14d: route's turn_running() broke a SimpleNamespace here)."""
    from angelo_agent.controller import Controller
    folder = tempfile.TemporaryDirectory()
    test.addCleanup(folder.cleanup)
    controller = Controller(folder.name)
    test.addCleanup(controller.close)
    controller.state.update(state)
    return controller


def receipt(chat, prompt, cached, output, requests=1, model="claude-haiku-5-5", mode="claude_api", kind=None):
    raw = {"status": "completed", "runtime": "claude", "session_id": chat, "phases": {"total_ms": 4200.0},
           "counts": {"provider_turn_requests": requests},
           "usage": {"turns": 1, "requests": requests, "input_tokens": prompt, "cached_input_tokens": cached,
                     "output_tokens": output},
           "route": {"mode": mode, "requested_model": model, "cache_ttl": "5m"}}
    if kind:
        raw["kind"] = kind
    value = telemetry.clean_receipt(raw)
    assert value is not None
    return value


def list_price(model, prompt_each, tokens):
    rates = model_profiles.rates(model, "5m", prompt_tokens=prompt_each)
    return round(sum(tokens[key] * rates[key] for key in tokens) / 1e6, 6)


class TurnTests(unittest.TestCase):
    def test_a_turn_is_priced_as_the_usage_meter_prices_it(self):
        row = usage.turn_usage(receipt(CHAT, 30000, 20000, 800, requests=3))
        self.assertEqual((row["input"], row["cache_read"], row["output"], row["requests"]), (10000, 20000, 800, 3))
        self.assertEqual(row["usd"], list_price("claude-haiku-5-5", 10000,
                                                {"input": 10000, "cache_read": 20000, "output": 800}))
        self.assertEqual((row["status"], row["seconds"], row["unpriced"]), ("completed", 4.2, 0))

    def test_one_request_takes_its_own_tier_and_a_sum_without_its_split_is_a_flagged_lower_bound(self):
        # Haiku's step is 100K. One request of 120K is above it; two requests totalling 240K may be 120K + 120K or
        # 200K + 40K, so with no per-request split the sum is priced at the base rates and marked partial.
        one = usage.turn_usage(receipt(CHAT, 120000, 0, 0, requests=1))
        two = usage.turn_usage(receipt(CHAT, 240000, 0, 0, requests=2))
        self.assertAlmostEqual(one["usd"], 120000 * 0.50 / 1e6)
        self.assertEqual(one["partial"], 0)
        self.assertAlmostEqual(two["usd"], 240000 * 0.10 / 1e6)
        self.assertEqual(two["partial"], 1)

    def test_an_unknown_model_is_counted_as_unpriced_never_as_zero_dollars(self):
        row = usage.turn_usage(receipt(CHAT, 1000, 0, 10, model="house-model-9", mode="codex_api"))
        self.assertEqual((row["unpriced"], row["input"]), (1, 1000))

    def test_turns_are_this_conversations_newest_first_without_warm_ups(self):
        receipts = [receipt(CHAT, 1000, 0, 10), receipt(OTHER, 5000, 0, 10), receipt(CHAT, 2000, 0, 20),
                    receipt(CHAT, 9000, 0, 90, kind="warm_up")]
        rows = usage.turns(receipts, CHAT)
        self.assertEqual([row["input"] for row in rows], [2000, 1000])
        self.assertEqual(len(usage.turns([receipt(CHAT, 1000, 0, 1)] * 50, CHAT)), usage.MAX_TURNS)


class TaskTests(unittest.TestCase):
    def test_each_task_spent_up_to_the_next_ones_start(self):
        zero = {"input": 0, "output": 0, "requests": 0, "usd": 0.0, "models": [], "live": False}
        first = dict(zero, input=1000, output=100, requests=1, usd=0.01)
        now = dict(zero, input=2500, output=300, requests=3, usd=0.04, live=True)
        rows = usage.tasks({"c:1": zero, "c:5": first}, now)
        self.assertEqual([row["task"] for row in rows], ["c:1", "c:5"])
        self.assertEqual((rows[0]["usage"]["input"], rows[0]["usage"]["usd"]), (1000, 0.01))
        self.assertEqual((rows[1]["usage"]["input"], rows[1]["usage"]["usd"], rows[1]["usage"]["live"]),
                         (1500, 0.03, True))


class PlanTests(unittest.TestCase):
    def setUp(self):
        usage_windows.clear()
        self.addCleanup(usage_windows.clear)

    def test_a_subscription_shows_the_windows_its_runtime_reported(self):
        controller = chat(self, provider="claude_subscription", id=CHAT)
        with mock.patch.object(usage_windows, "connection_key", return_value="k1"):
            self.assertIsNone(usage.plan(controller, {"billing": "subscription"}))   # Nothing reported yet.
            usage_windows.observe_claude("k1", {"status": "allowed", "rateLimitType": "five_hour",
                                                "utilization": 0.42, "resetsAt": 4102444800})
            found = usage.plan(controller, {"billing": "subscription"})
        self.assertEqual(found["windows"][0]["kind"], "five_hour")
        self.assertAlmostEqual(found["windows"][0]["used"], 0.42)
        self.assertFalse(found["limited"])

    def test_an_api_key_has_no_plan_its_spend_is_the_estimate(self):
        self.assertIsNone(usage.plan(chat(self), {"billing": "API key"}))


class SummaryTests(unittest.TestCase):
    def test_the_summary_holds_every_figure_and_only_this_conversations_tasks(self):
        controller = chat(self, id=CHAT, provider="claude_api", options={"model": "claude-haiku-5-5"})
        now = {"input": 10, "output": 1, "requests": 1, "usd": 0.001, "models": [], "live": False}
        with mock.patch.object(status, "usage", return_value=now), \
                mock.patch.object(usage, "goal", return_value=None):
            got = usage.summary(controller, {CHAT + ":3": {}, OTHER + ":1": {}}, [receipt(CHAT, 1000, 0, 10)])
        self.assertEqual(set(got), {"billing", "conversation", "turns", "tasks", "goal", "plan", "jobs", "pricing"})
        self.assertEqual([row["task"] for row in got["tasks"]], [CHAT + ":3"])
        self.assertEqual((got["billing"], got["plan"], got["jobs"]["credits"]), ("API key", None, None))
        self.assertEqual(len(got["turns"]), 1)

    def test_a_new_external_conversation_names_no_runtime_until_its_first_turn(self):
        from angelo_agent.external_host import EXTERNAL_PROVIDER
        fresh = chat(self, id=CHAT, provider=EXTERNAL_PROVIDER)
        self.assertEqual(status.route(fresh)["decided"], False)
        self.assertEqual(status.route(chat(self, id=CHAT, provider="claude_api"))["billing"], "API key",
                         "a provider the chat chose (a personal key binds no connection id) is decided")


if __name__ == "__main__":
    unittest.main()
