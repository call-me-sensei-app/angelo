# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Local human acceptance precedes only its own activity; indexed updates remain exact."""

import unittest
from agent_runtime_fixtures import AgentRuntimeFixture


class AcceptanceEventOrderTests(AgentRuntimeFixture, unittest.TestCase):
    def test_intent_insertion_preserves_other_activity_order_and_all_indexed_updates(self):
        self.c.emit("message", {"role": "user", "text": "Earlier preview request", "checkpoint": "old-checkpoint"})
        self.c.emit("activity", {"id": "previous-preview", "tool": "angelo_scene_program", "status": "completed"})
        self.c.emit("activity", {"id": "own-accept", "tool": "angelo_accept_preview", "status": "queued"})
        self.c.emit("delta", {"item": "streaming-item", "text": "Assistant tail"})
        self.c.emit("thinking_started", {"item": "existing-thought", "time": 1.})
        self.c.emit("activity", {"id": "other-call", "tool": "angelo_inspect_scene", "status": "running"})
        self.c.emit("message", {"role": "user", "text": "Accept the reviewed preview; local human UI action.",
            "request_kind": "native_preview_accept", "call_id": "own-accept", "preview_id": "exact-preview",
            "checkpoint": "fresh-checkpoint", "checkpoint_scope": "before_native_staging"})
        self.c.pump_events()
        messages = self.c.state["messages"]
        intent_index = next(i for i, value in enumerate(messages) if value.get("request_kind") == "native_preview_accept")
        self.assertLess(self.c._activities["previous-preview"], intent_index)
        self.assertLess(intent_index, self.c._activities["own-accept"])
        self.assertLess(self.c._activities["own-accept"], self.c._activities["other-call"])
        self.assertEqual([value["call_id"] for value in messages if value["role"] == "activity"],
                         ["previous-preview", "own-accept", "other-call"])
        self.assertEqual(messages[intent_index]["native_call_id"], "own-accept")
        self.assertNotIn("call_id", messages[intent_index])  # No collision with the renderer's activity cache key.
        self.c.emit("delta", {"item": "streaming-item", "text": " updated"})
        self.c.emit("thinking_completed", {"item": "existing-thought", "time": 1.5})
        self.c.emit("activity", {"id": "other-call", "status": "completed", "result": {"objects": []}})
        self.c.emit("request_checkpoint", {"id": "fresh-checkpoint", "scope": "before_native_staging",
            "generation": self.c._request_generation, "message_index": self.c._request_message_index})
        self.c.pump_events()
        self.assertEqual(messages[self.c._items["streaming-item"]]["text"], "Assistant tail updated")
        self.assertEqual(messages[self.c._thinking_messages["existing-thought"]]["duration_ms"], 500)
        self.assertEqual(messages[self.c._activities["other-call"]]["status"], "completed")
        self.assertEqual(messages[0]["checkpoint"], "old-checkpoint")
        self.assertEqual(messages[intent_index]["checkpoint"], "fresh-checkpoint")
        self.assertEqual(messages[intent_index]["checkpoint_scope"], "before_native_staging")


if __name__ == "__main__":
    unittest.main()
