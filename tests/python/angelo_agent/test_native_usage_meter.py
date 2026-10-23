# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chat's usage meter: list-price estimates from receipts, the per-chat ledger, plan windows and wording.

The receipts are ``telemetry.clean_receipt`` shapes; the plan-window payloads follow Claude Code's
``rate_limit_event`` (2.1.301/2.1.306 binaries) and Codex 0.163.0's ``account/rateLimits/updated``.
These fixtures prove the host's arithmetic and wording, not a provider's reporting.
"""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import model_profiles, usage_meter, usage_view, usage_windows


def receipt(mode="claude_api", model="claude-opus-5-5", ttl="1h", served=None, consultations=None, **usage):
    usage = dict({"input_tokens": 100000, "cached_input_tokens": 80000, "cache_write_input_tokens": 10000,
                  "output_tokens": 2000, "turns": 1}, **usage)
    route = {"mode": mode, "requested_model": model, "cache_ttl": ttl}
    if served is not None:
        route["served_models"] = served
    value = {"status": "completed", "runtime": "claude", "phases": {"total_ms": 1000},
             "counts": {"provider_turn_requests": 1}, "usage": usage, "route": route}
    if consultations:
        value["consultations"] = consultations
    return value


class EstimateTests(unittest.TestCase):
    def test_tokens_are_priced_at_the_served_models_list_rates_and_the_sessions_ttl(self):
        (mode, value), = usage_meter.receipt_entries(receipt(served=["claude-opus-5-5"]))
        self.assertEqual(mode, "claude_api")
        self.assertEqual((value["input"], value["cache_read"], value["cache_write"], value["output"]),
                         (10000, 80000, 10000, 2000))
        rates = model_profiles.rates("claude-opus-5-5", "1h")  # The table, not numbers copied into the test.
        expected = (10000 * rates["input"] + 80000 * rates["cache_read"] + 10000 * rates["cache_write"]
                    + 2000 * rates["output"]) / 1e6
        self.assertAlmostEqual(value["usd"], expected)
        self.assertEqual((value["unpriced"], value["partial"]), (0, 0))
        five = usage_meter.receipt_entries(receipt(ttl="5m"))[0][1]["usd"]
        self.assertLess(five, value["usd"], "5-minute writes cost less than 1-hour writes")

    def test_a_price_step_uses_the_per_request_prompt_size(self):
        small = usage_meter.entry({"requested_model": "claude-haiku-5-5"},
                                  {"input_tokens": 180000, "output_tokens": 0, "requests": 2})
        large = usage_meter.entry({"requested_model": "claude-haiku-5-5"},
                                  {"input_tokens": 180000, "output_tokens": 0, "requests": 1})
        self.assertLess(small["usd"], large["usd"])

    def test_a_mixed_model_turn_uses_the_runtimes_own_figure_and_an_unknown_model_is_not_priced(self):
        mixed = usage_meter.entry({"served_models": ["claude-opus-5-5", "claude-sonnet-5-5"]},
                                  {"input_tokens": 10, "output_tokens": 1, "cost_usd": 0.5})
        self.assertEqual((mixed["usd"], mixed["unpriced"]), (0.5, 0))
        unknown = usage_meter.entry({"requested_model": "future-model"}, {"input_tokens": 10, "output_tokens": 1})
        self.assertEqual((unknown["usd"], unknown["unpriced"], unknown["input"]), (0.0, 1, 10))
        default = usage_meter.entry({"requested_model": ""}, {"input_tokens": 10, "output_tokens": 1})
        self.assertEqual(default["unpriced"], 1, "The runtime's default model is unknown: never $0")

    def test_interrupted_or_incomplete_usage_is_a_lower_bound_and_unmeasured_requests_still_count(self):
        stopped = usage_meter.receipt_entries(receipt(interrupted_turns=1))[0][1]
        self.assertEqual(stopped["partial"], 1)
        missing = usage_meter.entry({"requested_model": "claude-opus-5-5"}, {"input_tokens": 500})
        self.assertEqual((missing["partial"], missing["unpriced"]), (1, 1))
        unmeasured = dict(receipt(), usage=None)
        (_, value), = usage_meter.receipt_entries(unmeasured)
        self.assertEqual((value["requests"], value["partial"], value["unpriced"]), (1, 1, 0))
        local = {"status": "completed", "phases": {"total_ms": 1}, "counts": {}}
        self.assertEqual(usage_meter.receipt_entries(local, "claude_api"), [], "A local preview used nothing")

    def test_consultations_count_under_their_own_connection(self):
        value = receipt(consultations=[{"mode": "codex_api", "requested_model": "gpt-6.1-sol",
                                        "usage": {"input_tokens": 1000, "cached_input_tokens": 0,
                                                  "output_tokens": 10, "turns": 1}}])
        modes = [mode for mode, _ in usage_meter.receipt_entries(value)]
        self.assertEqual(modes, ["claude_api", "codex_api"])

    def test_a_route_without_a_mode_takes_the_chats_connection(self):
        value = receipt()
        del value["route"]["mode"]
        self.assertEqual(usage_meter.receipt_entries(value, "claude_subscription")[0][0], "claude_subscription")


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledgers = []
        self.addCleanup(lambda: [ledger.drain() for ledger in self.ledgers])  # Before the folder goes.

    def ledger(self):
        self.ledgers.append(usage_meter.UsageLedger(self.temp.name))
        return self.ledgers[-1]

    def test_chat_totals_persist_and_since_start_stays_in_memory(self):
        ledger = self.ledger()
        self.assertTrue(ledger.add("chat-a", receipt()))
        ledger.add("chat-a", receipt(mode="claude_subscription"))
        ledger.add("chat-b", receipt())
        ledger.add_background(receipt(mode="codex_api", model="gpt-6.1-sol"))
        chat = ledger.chat("chat-a")
        self.assertEqual(sorted(chat["modes"]), ["claude_api", "claude_subscription"])
        self.assertEqual(chat["last"]["mode"], "claude_subscription")
        since = ledger.since_started()
        self.assertEqual((since["chats"], since["modes"]["claude_api"]["requests"]), (2, 2))
        self.assertIn("codex_api", since["modes"], "A warm-up counts since Angelo started")
        self.assertTrue(ledger.flush())
        again = self.ledger()
        self.assertEqual(again.chat("chat-a"), chat)
        self.assertEqual(again.since_started()["modes"], {}, "Totals since start are this process's only")
        self.assertIsNone(again.chat("codex_api"))

    def test_a_late_background_write_never_replaces_newer_totals_on_disk(self):
        """CI flake (runs 39628676950, 39697282161): the writer's older snapshot landed after a newer flush()."""
        import os
        import threading
        from unittest import mock
        real_replace = os.replace

        def slow_writer_replace(source, target):
            if threading.current_thread() is not threading.main_thread():
                time.sleep(.3)  # The background write took its snapshot and lands late.
            real_replace(source, target)
        ledger = self.ledger()
        with mock.patch.object(usage_meter.os, "replace", slow_writer_replace):
            ledger.add("chat-a", receipt())
            time.sleep(.05)
            ledger.add("chat-a", receipt(mode="claude_subscription"))
            self.assertTrue(ledger.flush())
            ledger.drain(3)
        self.assertEqual(self.ledger().chat("chat-a"), ledger.chat("chat-a"))

    def test_concurrent_adds_from_two_threads_leave_disk_equal_to_memory(self):
        import threading
        ledger = self.ledger()
        def adds(mode):
            for _ in range(100):
                ledger.add("chat-" + mode, receipt(mode=mode))
        threads = [threading.Thread(target=adds, args=(mode,)) for mode in ("claude_api", "codex_api")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        ledger.drain(3)
        self.assertTrue(ledger.flush())
        again = self.ledger()
        self.assertEqual({key: again.chat(key) for key in ("chat-claude_api", "chat-codex_api")},
                         {key: ledger.chat(key) for key in ("chat-claude_api", "chat-codex_api")})

    def test_the_writer_runs_off_the_callers_thread_and_coalesces(self):
        ledger = self.ledger()
        for _ in range(50):
            ledger.add("chat", receipt())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            path = Path(self.temp.name) / "usage.json"
            if path.exists() and json.loads(path.read_text())["chats"]["chat"]["modes"]["claude_api"]["requests"] == 50:
                break
            time.sleep(.02)
        else:
            self.fail("usage.json was not written by the background writer")

    def test_a_damaged_or_foreign_file_reads_as_empty_and_chats_are_bounded(self):
        path = Path(self.temp.name) / "usage.json"
        for content in ("{", "[]", json.dumps({"version": 2, "chats": {}}),
                        json.dumps({"version": 1, "chats": {"x": {"modes": {"m": {"input": -1, "usd": "1"}}}}})):
            path.write_text(content)
            ledger = self.ledger()
            chat = ledger.chat("x")
            self.assertTrue(chat is None or chat["modes"]["m"]["input"] == 0)
        ledger = self.ledger()
        for index in range(usage_meter.MAX_CHATS + 5):
            ledger.add("chat-" + str(index), receipt())
        self.assertEqual(len(ledger.chats), usage_meter.MAX_CHATS)
        self.assertIsNone(ledger.chat("chat-0"), "The least recently used chat goes first")

    def test_record_never_raises(self):
        usage_meter.record(Path(self.temp.name) / "missing" / "deeper", "chat", {"route": 5})
        usage_meter.record_background(self.temp.name, object())


class WindowTests(unittest.TestCase):
    def setUp(self):
        usage_windows.clear()
        self.addCleanup(usage_windows.clear)

    def test_claude_unified_windows_and_the_limiting_window(self):
        now = 1_800_000_000
        usage_windows.observe_claude("claude-sub", {
            "status": "allowed_warning", "rateLimitType": "five_hour", "utilization": 0.82, "resetsAt": now + 3600,
            "isUsingOverage": False, "unifiedWindows": {"five_hour": {"utilization": 0.82, "resetsAt": now + 3600},
                                                        "seven_day": {"utilization": 0.12, "resetsAt": now + 86400}}},
            now=now)
        state = usage_windows.current("claude-sub", now=now)
        self.assertEqual([window["kind"] for window in state["windows"]], ["five_hour", "seven_day"])
        self.assertFalse(state["limited"])
        usage_windows.observe_claude("claude-sub", {"status": "rejected", "isUsingOverage": True}, now=now)
        state = usage_windows.current("claude-sub", now=now)
        self.assertTrue(state["limited"] and state["overage"])
        self.assertEqual(len(state["windows"]), 2, "An update without windows keeps the ones seen")
        self.assertEqual([w["kind"] for w in usage_windows.current("claude-sub", now=now + 7200)["windows"]],
                         ["seven_day"], "A window whose reset passed is gone")
        self.assertIsNone(usage_windows.current("claude-sub", now=now + usage_windows.MAX_AGE + 1))

    def test_codex_updates_are_sparse_and_merge(self):
        usage_windows.observe_codex("chatgpt", {"primary": {"usedPercent": 21, "windowDurationMins": 300,
                                                            "resetsAt": None},
                                                "secondary": {"usedPercent": 8, "windowDurationMins": 10080}})
        usage_windows.observe_codex("chatgpt", {"primary": {"usedPercent": 25, "windowDurationMins": 300}})
        windows = usage_windows.current("chatgpt")["windows"]
        self.assertEqual([(w["kind"], w["used"]) for w in windows], [("primary", .25), ("secondary", .08)])
        self.assertIsNone(usage_windows.current("other"))
        usage_windows.observe_codex("chatgpt", "not a snapshot")
        usage_windows.observe_claude(None, {"status": "allowed"})

    def test_connection_key_prefers_the_connection_id(self):
        from types import SimpleNamespace
        controller = SimpleNamespace(state={"connection_id": "conn-1", "provider": "claude_subscription"})
        self.assertEqual(usage_windows.connection_key(SimpleNamespace(host=controller)), "conn-1")
        controller.state["connection_id"] = None
        self.assertEqual(usage_windows.connection_key(controller), "claude_subscription")
        self.assertIsNone(usage_windows.connection_key(object()))


class DrawPathGuardTests(unittest.TestCase):
    """Values the chat's draw reads can never raise there (audit at 9af9c2156f7, two reproduced paths)."""

    def setUp(self):
        usage_windows.clear()
        self.addCleanup(usage_windows.clear)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def test_a_reset_beyond_windows_localtime_is_dropped_and_a_raising_localtime_reads_as_no_time(self):
        from unittest import mock
        usage_windows.observe_claude("plan", {"rateLimitType": "five_hour", "utilization": .5, "resetsAt": 4e10})
        window, = usage_windows.current("plan")["windows"]
        self.assertIsNone(window["resets_at"], "Above year 3000 (Windows' localtime limit): no reset time")
        usage_windows.observe_claude("plan", {"rateLimitType": "five_hour", "utilization": .5,
                                              "resetsAt": usage_windows.MAX_EPOCH})
        window, = usage_windows.current("plan", now=0)["windows"]
        self.assertEqual(window["resets_at"], usage_windows.MAX_EPOCH)
        with mock.patch.object(usage_view.time, "localtime", side_effect=OSError(22, "Invalid argument")):
            self.assertEqual(usage_view.clock(window["resets_at"]), "", "As Windows raises: no time, no exception")
            (label, used, detail, warn), = usage_view.window_lines({"windows": [window]}, now=0)
        self.assertEqual(detail, "50% used", "A reset time that cannot be shown is left out")

    def test_a_malformed_models_field_loads_counts_and_keeps_the_bar(self):
        from types import SimpleNamespace
        from angelo_agent import chat_usage
        Path(self.temp.name, "usage.json").write_text(json.dumps({"version": 1, "chats": {
            "chat-1": {"modes": {"claude_api": {"input": 10, "requests": 1, "models": 5}}, "updated": 1},
            "chat-2": {"modes": 7, "last": {"mode": "claude_api", "models": {"a": 1}}, "updated": "x"}}}))
        ledger = usage_meter.UsageLedger(self.temp.name)
        self.addCleanup(ledger.drain)
        self.assertEqual(ledger.chat("chat-1")["modes"]["claude_api"]["models"], [])
        self.assertTrue(ledger.add("chat-1", receipt()))
        self.assertEqual(ledger.chat("chat-1")["modes"]["claude_api"]["requests"], 2)
        usage_meter._LEDGERS[str(Path(self.temp.name).resolve())] = usage_meter._LEDGERS[self.temp.name] = ledger
        controller = SimpleNamespace(telemetry=SimpleNamespace(directory=self.temp.name), busy=False,
                                     state={"id": "chat-1", "provider": "claude_api", "connection_id": "c1"})
        self.assertTrue(chat_usage.bar_parts(controller)["money"].startswith("≈ $"), "The bar stays")


class WordingTests(unittest.TestCase):
    def totals(self, *receipts):
        output = {}
        for value in receipts:
            for mode, item in usage_meter.receipt_entries(value):
                usage_meter.add_to(output, mode, item)
        return output

    def test_compact_numbers_and_money(self):
        self.assertEqual([usage_view.tokens(n) for n in (940, 8400, 212345, 1770000, 250000000)],
                         ["940", "8.4k", "212k", "1.8M", "250M"])
        self.assertEqual([usage_view.money(n) for n in (0, .001, .42, 1234.5)], ["$0", "≈ < $0.01", "≈ $0.42", "≈ $1,234"])
        self.assertEqual(usage_view.model_label("claude-opus-5-5"), "Claude Opus 5.5")
        self.assertEqual(usage_view.model_label("gpt-6.1-sol"), "GPT-6.1 Sol")
        self.assertEqual(usage_view.model_label("kimi-k3"), "kimi-k3")

    def test_paid_usage_shows_an_estimate_and_a_plan_never_shows_dollars(self):
        paid = usage_view.bar(self.totals(receipt()), "claude_api")
        self.assertTrue(paid["money"].startswith("≈ $"))
        self.assertEqual(paid["cached"], "80% cached", "Cache reads ÷ all input tokens: 80,000 of 100,000")
        self.assertEqual(usage_view.cached({"input": 1, "cache_read": 999, "cache_write": 0}), "99% cached",
                         "Never rounded up to 100% while anything was uncached")
        self.assertIsNone(usage_view.cached({"input": 0, "cache_read": 0, "cache_write": 0}))
        self.assertIsNone(paid["plan"])
        plan_totals = self.totals(receipt(mode="claude_subscription"))
        windows = {"windows": [{"kind": "five_hour", "used": .85, "resets_at": None},
                               {"kind": "seven_day", "used": .2, "resets_at": None}],
                   "limited": False, "overage": False, "at": time.time()}
        plan = usage_view.bar(plan_totals, "claude_subscription", windows, live=True)
        self.assertEqual((plan["ring"], plan["warn"], plan["money"]), (.85, True, None))
        self.assertEqual((plan["plan"], plan["window"]), ("Included in your Claude plan", "5-hour limit: 85% used"))
        self.assertTrue(plan["tokens"].endswith("tokens…"))
        codex_windows = {"windows": [{"kind": "primary", "used": .3, "resets_at": None, "minutes": 300}], "limited": False}
        self.assertIsNone(usage_view.bar({}, "codex_subscription", codex_windows), "Nothing counted, nothing running")
        codex = usage_view.bar({}, "codex_subscription", codex_windows, live=True)
        self.assertEqual((codex["tokens"], codex["plan"], codex["window"]),
                         ("0 tokens…", "Included in your ChatGPT plan", "5-hour limit: 30% used"))
        detail = usage_view.card(plan_totals, windows=windows)
        self.assertEqual(detail["badge_text"], "Included in your Claude plan")
        self.assertNotIn("$", detail["headline"] + "".join(value for _, value in detail["rows"]))
        self.assertEqual(usage_view.turn_label(562), "562 tokens")
        self.assertEqual(usage_view.turn_label(1), "1 token")
        self.assertEqual(usage_view.turn_label(8400), "8.4k tokens")

    def test_a_fresh_chat_shows_the_zero_bar_for_every_connection_kind(self):
        """1.0.5: the bar waited for the first request, hiding the plan's window exactly when the person decides
        whether to send. A fresh chat (nothing sent, a connection selected) shows the zero bar; any other chat with
        nothing counted still shows none (a chat from before the meter never claims a total for its requests)."""
        windows = {"windows": [{"kind": "five_hour", "used": .34, "resets_at": None}], "limited": False}
        plan = usage_view.bar({}, "claude_subscription", windows, fresh=True)
        self.assertEqual((plan["tokens"], plan["window"], plan["ring"], plan["money"], plan["warn"]),
                         ("0 tokens", "5-hour limit: 34% used", .34, None, False))
        self.assertEqual(usage_view.bar({}, "codex_subscription", None, fresh=True)["plan"], "Included in your ChatGPT plan")
        for mode in ("claude_api", "claude_team"):
            with self.subTest(mode=mode):
                paid = usage_view.bar({}, mode, None, fresh=True)
                self.assertEqual((paid["tokens"], paid["money"], paid["plan"]), ("0 tokens", "≈ $0.00", None))
        self.assertIsNone(usage_view.bar({}, "claude_api", None), "Not fresh, nothing counted: no bar")

    def test_only_a_chat_that_sent_nothing_with_a_connection_is_fresh(self):
        from types import SimpleNamespace
        from angelo_agent import chat_usage
        chat = SimpleNamespace(state={"messages": [{"role": "system", "text": "Added Head – Stylized to start from"}]})
        self.assertTrue(chat_usage.fresh(chat, True))
        self.assertFalse(chat_usage.fresh(chat, False), "No connection selected: the connect options, no bar")
        chat.state["messages"].append({"role": "user", "text": "Sculpt a head"})
        self.assertFalse(chat_usage.fresh(chat, True), "A chat that sent something is never the zero bar")

    def test_card_lists_connections_lower_bounds_and_the_estimates_basis(self):
        totals = self.totals(receipt(), receipt(mode="claude_team"), receipt(mode="claude_subscription"),
                             receipt(interrupted_turns=1), receipt(model="future-model"))
        last = usage_meter.receipt_entries(receipt())[0][1]
        detail = usage_view.card(totals, last=dict(last, mode="claude_api"), chats=2)
        self.assertEqual(detail["badge"], "estimate")
        self.assertIn("5 requests", detail["summary"])
        self.assertIn("2 chats", detail["summary"])
        self.assertIn("% cached", detail["summary"])
        self.assertRegex(detail["last"], r"tokens · \d+% cached · ≈ \$", "Per request: tokens, cached share, estimate")
        self.assertEqual(detail["rows"][-1][1].endswith("included"), True, "Plan rows go last, without a price")
        notes = " ".join(detail["notes"])
        for expected in ("organization's bill", "Claude plan covers", "Cached is the share of input tokens", "At least: 1 request",
                         "1 request used a model without a published price", model_profiles.READ_ON):
            self.assertIn(expected, notes)
        self.assertEqual(sum(share for *_, share in detail["segments"]), 1.0)

    def test_window_lines_name_the_window_and_its_reset(self):
        now = time.mktime((2026, 10, 21, 15, 0, 0, 0, 0, -1))
        lines = usage_view.window_lines({"windows": [
            {"kind": "five_hour", "used": .34, "resets_at": now + 3600},
            {"kind": "secondary", "used": .08, "resets_at": now + 3 * 86400, "minutes": 10080},
            {"kind": "primary", "used": 1.2, "resets_at": None, "minutes": 300}]}, now)
        self.assertEqual(lines[0], ("Current 5-hour window", .34, "34% used · resets 16:00", False))
        self.assertEqual(lines[1][0], "7-day window")
        self.assertIn("2026-10-24", lines[1][2])
        self.assertEqual((lines[2][0], lines[2][1], lines[2][3]), ("5-hour window", 1.0, True))


if __name__ == "__main__":
    unittest.main()
