# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where goals are kept (``goal_store``; doc/angelo/goal_mode.rst, "The goal", Storage): ``goals.json`` in the state
folder, never the chat file; read back with an active goal paused; damaged and later-version files handled as
``usage.json`` handles them."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))

from angelo_agent import goal_state as G, goal_store  # noqa: E402


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "goals.json"

    def store(self):
        return goal_store.GoalStore(self.folder.name)

    def goal(self, status=G.ACTIVE):
        goal = G.new("Match the hut", G.WORDS, check=G.REFERENCE)
        goal["measure"] = {"reference": "0b5e8d1c-0000-4000-8000-000000000001",
                           "camera": {"location": [0, -8, 2], "look_at": [0, 0, 1], "lens": 50},
                           "targets": "VISIBLE", "locked": True}
        G.record_round(goal, kind=G.PERSON, working_ms=90_000, measure_ms=30, tokens=1200, usd=0.0,
                       measurement={"measured": True, "iou": 0.41, "drawn": 2}, scene_changed=True)
        if status != G.ACTIVE:
            G.end(goal, status, "")
        return goal

    def test_round_trip_and_restart_pauses_an_active_goal(self):
        store = self.store()
        active, ended = self.goal(), self.goal(G.PASSED)
        store.put("chat-a", active)
        store.put("chat-b", ended)
        self.assertTrue(store.flush())
        again = self.store()
        self.assertEqual((again.get("chat-a")["status"], again.get("chat-a")["reason"]), (G.PAUSED, G.PAUSED_BY_RESTART))
        self.assertEqual(again.get("chat-a")["rounds"], active["rounds"])
        self.assertEqual(again.get("chat-a")["measure"], active["measure"])
        self.assertEqual(again.get("chat-b")["status"], G.PASSED)

    def test_the_file_holds_only_goals_and_a_version(self):
        store = self.store()
        store.put("chat-a", self.goal())
        real_open = os.open
        with mock.patch.object(goal_store.os, "open", wraps=real_open) as opened:
            self.assertTrue(store.flush())
        creates = [call for call in opened.call_args_list if call.args[1] & os.O_CREAT]
        self.assertTrue(creates, "the store never created its private temporary file")
        self.assertTrue(all(call.args[2] == 0o600 for call in creates))
        raw = json.loads(self.path.read_text())
        self.assertEqual(set(raw), {"version", "goals"})
        self.assertEqual(raw["version"], 1)
        if os.name != "nt":  # Windows mode bits do not describe its inherited per-user profile ACL.
            self.assertEqual(oct(self.path.stat().st_mode & 0o777), "0o600")

    def test_remove_forgets_a_chat(self):
        store = self.store()
        store.put("chat-a", self.goal())
        store.remove("chat-a")
        store.flush()
        self.assertIsNone(self.store().get("chat-a"))

    def test_a_damaged_file_reads_as_empty_and_is_kept_before_it_is_replaced(self):
        self.path.write_text("{not json")
        store = self.store()
        self.assertIsNone(store.get("chat-a"))
        store.put("chat-a", self.goal())
        self.assertTrue(store.flush())
        kept = [p.name for p in Path(self.folder.name).iterdir() if p.name != "goals.json"]
        self.assertTrue(kept, "the damaged file was not kept beside")
        self.assertEqual(self.store().get("chat-a")["objective"], "Match the hut")

    def test_a_later_versions_file_is_never_written_over(self):
        self.path.write_text(json.dumps({"version": 2, "goals": {}, "more": 1}))
        store = self.store()
        store.put("chat-a", self.goal())
        self.assertFalse(store.flush())
        self.assertEqual(json.loads(self.path.read_text())["version"], 2)

    def test_a_malformed_goal_is_dropped_and_the_rest_load(self):
        good = self.goal()
        self.path.write_text(json.dumps({"version": 1, "goals": {
            "ok": good, "bad-status": dict(good, status="exploded"), "bad-check": dict(good, check="VIBES"),
            "bad-rounds": dict(good, rounds="many"), "not-a-dict": 7, "x" * 100: good}}))
        store = self.store()
        self.assertIsNotNone(store.get("ok"))
        for chat in ("bad-status", "bad-check", "bad-rounds", "not-a-dict", "x" * 100):
            self.assertIsNone(store.get(chat), chat)

    def test_a_link_at_the_name_is_never_followed(self):
        target = Path(self.folder.name) / "elsewhere.json"
        target.write_text(json.dumps({"version": 1, "goals": {"chat-a": self.goal()}}))
        os.symlink(target, self.path)
        store = self.store()
        self.assertIsNone(store.get("chat-a"))
        store.put("chat-b", self.goal())
        store.flush()
        self.assertFalse(self.path.is_symlink())
        self.assertIn("chat-a", json.loads(target.read_text())["goals"])   # The link's target is untouched.

    def test_bounded_to_the_most_recent_chats(self):
        store = self.store()
        for index in range(goal_store.MAX_CHATS + 5):
            goal = self.goal(G.PASSED)
            goal["updated"] = index
            store.put("chat-%d" % index, goal)
        store.flush()
        kept = json.loads(self.path.read_text())["goals"]
        self.assertEqual(len(kept), goal_store.MAX_CHATS)
        self.assertNotIn("chat-0", kept)


if __name__ == "__main__":
    unittest.main()
