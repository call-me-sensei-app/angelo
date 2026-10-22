# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The usage meter's canvas logic without a GPU: the cached bar, what a narrow bar keeps, the card's blocks and placement.

Drawing itself is covered by the GUI fixture (``doc/angelo/agent_chat.rst``, "Usage meter").
"""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import chat_usage, usage_meter, usage_view, usage_windows


def finished(mode, prompt=100000, cached=90000, output=1000, model="claude-opus-5-5"):
    return {"status": "completed", "phases": {"total_ms": 1}, "counts": {"provider_turn_requests": 1},
            "route": {"mode": mode, "requested_model": model, "cache_ttl": "5m"},
            "usage": {"input_tokens": prompt, "cached_input_tokens": cached, "cache_write_input_tokens": 0,
                      "output_tokens": output, "turns": 1}}


class ChatUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.controller = SimpleNamespace(telemetry=SimpleNamespace(directory=self.temp.name), busy=False,
                                          state={"id": "chat-1", "provider": "claude_api", "connection_id": "c1"})
        self.ledger = usage_meter.UsageLedger.shared(self.temp.name)
        self.addCleanup(self.ledger.drain)
        usage_windows.clear()
        self.addCleanup(usage_windows.clear)

    def test_the_bar_is_recomputed_only_when_its_inputs_change(self):
        self.assertIsNone(chat_usage.bar_parts(self.controller), "No bar before the first counted request")
        self.ledger.add("chat-1", finished("claude_api"))
        first = chat_usage.bar_parts(self.controller)
        self.assertTrue(first["money"].startswith("≈ $"))
        self.assertIs(chat_usage.bar_parts(self.controller), first, "Unchanged inputs reuse the parts")
        self.ledger.add("chat-1", finished("claude_api"))
        self.assertIsNot(chat_usage.bar_parts(self.controller), first)

    def test_the_bar_follows_a_language_change_and_the_ring_is_one_annulus(self):
        from unittest import mock
        self.ledger.add("chat-1", finished("claude_api"))
        english = chat_usage.bar_parts(self.controller)
        with mock.patch("angelo_agent.ui_text.language", return_value="ar"):
            self.assertIsNot(chat_usage.bar_parts(self.controller), english, "Translated again in another language")
        points, triangles = chat_usage.ring_triangles(0, 0, .5, steps=32)
        self.assertEqual((len(points), len(triangles)), (34, 32), "Half a ring: 16 segments, two triangles each")
        self.assertTrue(all(abs((x * x + y * y) ** .5 - 5.5) <= .91 for x, y, _ in points))

    def test_a_narrow_bar_drops_the_least_important_parts_first(self):
        parts = {"tokens": "1.8M tokens", "cached": "93% cached", "money": None,
                 "plan": "Included in your Claude plan", "window": "5-hour limit: 34% used", "ring": .34, "warn": False}
        measure = lambda text: 6 * len(text)
        kinds = lambda width, value=parts: [k for k, _ in chat_usage.bar_items(value, width, measure)]
        self.assertEqual(kinds(700), ["tokens", "cached", "window", "plan"])
        self.assertEqual(kinds(400), ["tokens", "cached", "window"])
        self.assertEqual(kinds(250), ["tokens", "cached"])
        self.assertEqual(kinds(120), ["tokens"])
        paid = dict(parts, money="≈ $0.42", plan=None, window=None)
        self.assertEqual(kinds(150, paid), ["tokens", "money"], "The dollar figure stays, with its ≈")

    def test_a_plan_chat_shows_tokens_and_its_windows_ring(self):
        self.controller.state.update(provider="claude_subscription", connection_id="sub")
        self.ledger.add("chat-1", finished("claude_subscription"))
        usage_windows.observe_claude("sub", {"status": "allowed", "rateLimitType": "five_hour", "utilization": .5})
        value = chat_usage.bar_parts(self.controller)
        self.assertEqual((value["ring"], value["money"]), (.5, None))
        content = chat_usage.card_content(self.controller, "chat")
        kinds = [kind for kind, *_ in chat_usage.blocks(content, chat_usage.WIDTH, lambda text, w, size: [text])]
        self.assertIn("badge", kinds)
        self.assertIn("window", kinds)

    def test_since_start_counts_chats_and_the_card_hangs_from_the_bar(self):
        self.ledger.add("chat-1", finished("claude_api"))
        self.ledger.add("chat-2", finished("claude_api"))
        content = chat_usage.card_content(self.controller, "since")
        self.assertIn("2 chats", content["summary"])
        items = chat_usage.blocks(content, chat_usage.WIDTH, lambda text, w, size: [text, text])
        total = sum(height for _, height, _ in items)
        x, y, w, h = chat_usage.layout(800, 900, (0, 870, 800, 30), total)
        self.assertEqual((x + w, y + h), (788, 864), "Hangs from the bar at its right end")
        x, y, w, h = chat_usage.layout(260, 200, (0, 170, 260, 30), total)
        self.assertTrue(x >= 12 and x + w <= 248 and y >= 12, "Clamped inside a small chat")


if __name__ == "__main__":
    unittest.main()
