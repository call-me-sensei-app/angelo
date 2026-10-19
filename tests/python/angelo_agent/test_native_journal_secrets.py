# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""No byte of the connection's key stays in a chat's journal once the chat redacts it (session_journal).

The journal appends records, so a value written before it was redacted would stay in an earlier record.
These pin the three ways a key reached the file and how each is closed, with the real controller and a
made-up key: a key streamed in pieces with a save between them (the redaction rewrites the journal), a key
bound after it was saved in any row or result record (``activity_rows.bind_key`` redacts them all and
rewrites), and a thinking summary whose key arrives split across fragments (redacted over the joined text).
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent.controller import Controller  # noqa: E402

KEY = "sk-test-fake-0123456789abcdef"  # Not a credential: a made-up key; no credential is read.


def pieces(data):
    """Every run of six characters of the key found in ``data`` (bytes)."""
    key = KEY.encode()
    return sorted({key[start:start + 6] for start in range(len(key) - 5)} & {
        data[start:start + 6] for start in range(len(data) - 5)})


class JournalSecretTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.controller = Controller(folder.name)
        self.addCleanup(self.controller.close)
        self.controller.set_document(("journal-secrets", 1))

    def pump(self):
        self.controller._last_save = 0  # Over half a second since the last save: a durable event saves.
        self.controller.pump_events()

    def on_disk(self):
        return self.controller.store.path.read_bytes()

    def test_a_key_streamed_in_pieces_with_a_save_between_leaves_no_piece_on_disk(self):
        self.controller.api_key = KEY
        for kind, item in (("delta", "reply"), ("thinking", "think")):
            with self.subTest(kind=kind):
                self.controller.emit(kind, {"item": item, "text": "The key is " + KEY[:20]})
                self.pump()
                self.controller.save()  # Any durable event's save (a thinking summary starts none itself).
                self.assertTrue(pieces(self.on_disk()), "the first piece was saved before the key was whole")
                self.controller.emit(kind, {"item": item, "text": KEY[20:] + " done."})
                self.pump()
                self.controller.save()
                self.assertEqual(pieces(self.on_disk()), [])

    def test_binding_a_key_redacts_every_row_and_result_record_on_disk(self):
        state = self.controller.state
        state["messages"] += [{"role": "assistant", "text": "Use " + KEY},
                              {"role": "thinking", "text": "It is " + KEY, "status": "completed", "duration_ms": 1},
                              {"role": "user", "text": "My key: " + KEY},
                              {"role": "activity", "call_id": "c1", "tool": "angelo_execute_python", "text": "",
                               "arguments": {"code": KEY}, "result": {KEY: "value"}, "status": "completed"}]
        state["results"]["s/c1"] = {"document": "d", "phase": "completed", "result": {"output": KEY}}
        self.controller.save()
        self.assertTrue(pieces(self.on_disk()))
        self.controller.api_key = KEY
        self.controller.save()
        self.assertEqual(self.controller.store.last_write, "rewrite")
        self.assertEqual(pieces(self.on_disk()), [])
        self.assertEqual(state["messages"][-1]["result"], {"[REDACTED]": "value"}, "mapping keys too")
        self.assertEqual(self.controller.state["results"]["s/c1"]["result"], {"output": "[REDACTED]"})

    def test_a_thinking_key_split_across_fragments_is_redacted_whole(self):
        self.controller.api_key = KEY
        self.controller.emit("thinking", {"item": "t", "text": "first " + KEY[:9]})
        self.controller.emit("thinking", {"item": "t", "text": KEY[9:] + " then"})
        self.controller.pump_events()
        row = next(m for m in self.controller.state["messages"] if m["role"] == "thinking")
        self.assertEqual(row["text"], "first [REDACTED] then")


if __name__ == "__main__":
    unittest.main()
