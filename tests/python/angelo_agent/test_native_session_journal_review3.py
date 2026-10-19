# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Final adversarial review of the conversation journal: FAILING repros on the second review's fixes (69c1d8444c7).

1. ``activity_rows.bind_key`` now invalidates the journal on every new key,
   also when the file provably holds no piece of it. ``ui.get_controller``
   binds the connection's key right after ``set_document``, whose save already
   rewrote the chat (the first save after a load), so every chat opened on an
   API-key connection is rewritten whole a second time at its next save: about
   15 ms more on the main thread for a 2.5 MB chat (``journal_review/open_cost.py``),
   and a switch away from a chat only looked at rewrites it instead of writing
   nothing (``SessionStore.leave``). Before ada59924ec1 the open cost one
   rewrite. Invalidating only when memory held the key or the file is not
   exactly memory's state (``JournalWriter.current``) keeps every repro of
   ``..._review2.KeyBoundLaterTests`` passing.
"""

import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import session_journal  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.invocations import reserve_invocation  # noqa: E402
from angelo_agent.sessions import SessionStore  # noqa: E402

KEY = "sk-test-fake-0123456789abcdef"  # Not a credential: a made-up key; no credential is read.


class OpenRewriteTests(unittest.TestCase):
    """Finding 1: a chat opened on an API-key connection is rewritten twice."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = folder.name
        store = SessionStore(self.folder, str(uuid.uuid4()))
        state = store.new("claude_subscription")
        for index in range(300):  # A long chat that never held the key, closed (one base line).
            state["messages"].append({"role": "user", "text": "Request %d" % index})
            reserve_invocation(state, state["id"] + "/call-%d" % index, "%064x" % index, "('document', 1)")
        store.compact(state)
        self.chat = state["id"]
        self.rewrites = []
        rewrite = session_journal.rewrite
        patcher = patch.object(session_journal, "rewrite",
                               lambda *args, **kwargs: self.rewrites.append(args[0]) or rewrite(*args, **kwargs))
        patcher.start()
        self.addCleanup(patcher.stop)

    def open(self):
        """``ui.get_controller``'s order: construct (load), set the document (its save), bind the key."""
        controller = Controller(self.folder, session_id=self.chat)
        self.addCleanup(controller.close)
        controller.set_document(("journal-review3", 1))
        controller.api_key = KEY
        return controller

    def test_opening_a_chat_and_sending_a_message_rewrites_it_once(self):
        controller = self.open()
        controller.emit("message", {"role": "user", "text": "One more change"})
        controller._last_save = 0
        controller.pump_events()  # A durable event: the pump saves.
        self.assertEqual(controller.store.last_write, "append")
        self.assertEqual(len(self.rewrites), 1, "the first save after the load rewrote it; nothing held the key")

    def test_looking_at_a_chat_and_leaving_it_rewrites_it_once(self):
        controller = self.open()
        controller.save(leave=True)  # ui.show_session: the person switches to another chat.
        self.assertEqual(len(self.rewrites), 1)


if __name__ == "__main__":
    unittest.main()
