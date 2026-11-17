# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Remote switches: the signed update feed's ``disabled_features`` (``remote_features``), and a connection mode that
names one (``remote_switch``) appearing and disappearing in AI Providers with the other modes unaffected."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
import update_fixtures as fx  # noqa: E402
from angelo_agent import agent_runtime, provider_registry, remote_features, update_feed, update_state  # noqa: E402
from angelo_agent.provider_registry import CODING, SPECS  # noqa: E402

NOW = datetime(2026, 11, 13, tzinfo=timezone.utc)
SWITCHED = dict(SPECS["claude_subscription"], remote_switch="test_switch", name="Switched")   # Any mode's shape.


class FeedFieldTests(unittest.TestCase):
    def setUp(self):
        self.seed, self.keys = fx.new_key()

    def open(self, **changes):
        return update_feed.open_feed(fx.envelope(fx.payload(**changes), self.seed), self.keys, NOW)

    def test_the_feed_carries_disabled_switches_and_an_older_feed_has_none(self):
        self.assertEqual(self.open().disabled_features, frozenset())
        self.assertEqual(self.open(disabled_features=["gemini_subscription"]).disabled_features,
                         frozenset({"gemini_subscription"}))

    def test_a_malformed_list_fails_the_feed_closed(self):
        for bad in ("gemini_subscription", ["Bad-Name"], [1], ["x" * 41], ["a"] * 33):
            with self.subTest(bad=bad), self.assertRaises(update_feed.FeedError):
                self.open(disabled_features=bad)


class SwitchTests(unittest.TestCase):
    def setUp(self):
        self.seed, self.keys = fx.new_key()
        self.directory = tempfile.TemporaryDirectory()
        remote_features.forget()

    def tearDown(self):
        remote_features.forget()
        self.directory.cleanup()

    def accept(self, **changes):
        """The update checker's state after it accepted a feed (the raw signed envelope, as it stores it)."""
        raw = fx.envelope(fx.payload(**changes), self.seed).decode("utf-8")
        update_state.save(self.directory.name, replace(update_state.load(self.directory.name), envelope=raw))
        remote_features.forget()

    def enabled(self, name="test_switch"):
        return remote_features.enabled(name, self.directory.name, keys=self.keys)

    def test_a_switch_is_on_until_a_good_feed_lists_it(self):
        self.assertTrue(self.enabled())   # No feed yet (first run, offline, or update checks off by policy).
        self.accept()
        self.assertTrue(self.enabled())
        self.accept(disabled_features=["test_switch"])
        self.assertFalse(self.enabled())
        self.assertTrue(self.enabled("another_switch"))
        self.accept()   # A later feed lifts it without a release.
        self.assertTrue(self.enabled())

    def test_a_feed_that_no_longer_verifies_lists_nothing(self):
        self.accept(disabled_features=["test_switch"])
        _seed, other_keys = fx.new_key()
        self.assertTrue(remote_features.enabled("test_switch", self.directory.name, keys=other_keys))

    def test_the_build_can_turn_a_switch_off_whatever_the_feed_says(self):
        with patch.object(remote_features, "BUILD_DISABLED", frozenset({"test_switch"})):
            self.assertFalse(self.enabled())

    def test_the_answer_is_cached_between_draws(self):
        clock = [100.0]
        ask = lambda: remote_features.enabled("test_switch", self.directory.name, keys=self.keys, clock=lambda: clock[0])
        self.assertTrue(ask())
        raw = fx.envelope(fx.payload(disabled_features=["test_switch"]), self.seed).decode("utf-8")
        update_state.save(self.directory.name, replace(update_state.load(self.directory.name), envelope=raw))
        self.assertTrue(ask())   # Within the TTL: the cached answer.
        clock[0] += remote_features.TTL + 1
        self.assertFalse(ask())

    def test_a_switched_mode_leaves_providers_and_every_other_mode_comes_back_unchanged(self):
        with patch.dict(SPECS, {"switched_mode": SWITCHED}), \
                patch.object(remote_features, "_directory", return_value=self.directory.name), \
                patch.object(update_feed, "load_keys", return_value=self.keys), \
                patch.dict(agent_runtime.PIN, {"wire_apis": list(agent_runtime.PIN.get("wire_apis", ())) + ["gemini"]}):
            self.assertTrue(provider_registry.available("switched_mode"))
            self.accept(disabled_features=["test_switch"])
            self.assertFalse(provider_registry.available("switched_mode"))
            for mode in SPECS:
                if mode != "switched_mode":
                    self.assertTrue(provider_registry.available(mode), mode)   # The Gemini API route included.
            self.assertIn(("GOOGLE", "Google Gemini", "Connect to Google Gemini"), provider_registry.provider_items(CODING))
            self.accept()
            self.assertTrue(provider_registry.available("switched_mode"))


if __name__ == "__main__":
    unittest.main()
