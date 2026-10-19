# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Append-only conversation journal: reproductions of edge cases in its key binding and kept copies.

Each test states the contract it holds the journal to and failed before its
fix. ``test_native_session_journal_pins2.py`` pins the behaviour verified
alongside.

1. ``activity_rows.bind_key`` rewrites the journal only when memory still holds
   the key it binds. A key that memory no longer holds (the head of a long
   thinking summary, an earlier version of a reply, a result record evicted
   from the 256 kept) stays whole in an earlier record after the binding, until
   the next compaction. The whole-file save the journal replaced had already
   dropped it from disk, and the binding's own save wrote none.
2. ``session_journal.preserve`` falls back to a copy when ``os.link`` fails for
   any reason, ``EEXIST`` included, and the copy ``os.replace``s whatever has
   that name. Two app instances keeping the same chat in the same second (both
   load it at start-up) can then replace the first instance's kept copy with
   the chat as that instance has already rewritten it: the only copy of the
   unreadable history is gone while both chats say it was kept unchanged.
3. A kept copy (``<id>.json.<time>.unreadable``) is never redacted: binding the
   key a chat held leaves it whole in the copy, which nothing ever removes.
"""

import glob
import os
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


def pieces(data):
    """Every run of six characters of the key found in ``data`` (bytes)."""
    key = KEY.encode()
    return sorted({key[start:start + 6] for start in range(len(key) - 5)} & {
        data[start:start + 6] for start in range(len(data) - 5)})


class KeyBoundLaterTests(unittest.TestCase):
    """Case 1: binding a key the chat saved before, when memory no longer holds it."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.controller = Controller(folder.name)
        self.addCleanup(self.controller.close)
        self.controller.set_document(("journal-review2", 1))

    def bind_and_save(self):
        self.controller.api_key = KEY  # The person binds the key they had pasted (AI Providers).
        self.controller.save()
        return pieces(self.controller.store.path.read_bytes())

    def test_the_head_of_a_long_thinking_summary(self):
        controller = self.controller
        controller.emit("thinking", {"item": "t", "text": "The person pasted " + KEY + ". "})
        controller.pump_events()
        controller.save()  # A durable event's save while the summary streams.
        for _ in range(20):  # The live row keeps the summary's last 16,000 characters.
            controller.emit("thinking", {"item": "t", "text": "x" * 1000})
            controller.pump_events()
        controller.save()
        row = next(m for m in controller.state["messages"] if m["role"] == "thinking")
        self.assertNotIn(KEY, row["text"], "precondition: memory no longer holds the key")
        self.assertEqual(self.bind_and_save(), [], "the whole-file save had already removed it")

    def test_an_earlier_version_of_a_reply(self):
        controller = self.controller
        controller.emit("delta", {"item": "r", "text": "Your key is " + KEY})
        controller._last_save = 0
        controller.pump_events()
        controller.save()
        controller.emit("message_complete", {"item": "r", "text": "Done. I will not repeat secrets."})
        controller.pump_events()
        controller.save()
        self.assertEqual(self.bind_and_save(), [])

    def test_a_result_record_evicted_from_memory(self):
        controller = self.controller
        results = controller.state["results"]
        with controller.lock:
            results["s/c0"] = {"document": "d", "phase": "completed", "result": {"output": "env: " + KEY}}
        controller.save()
        for index in range(1, 258):  # As journal_tool and complete_result evict: the oldest of 256 goes.
            with controller.lock:
                results["s/c%d" % index] = {"document": "d", "phase": "completed", "result": {"output": index}}
                while len(results) > 256:
                    results.pop(next(iter(results)))
            controller.save()
        self.assertEqual(self.bind_and_save(), [])


def unreadable_chat(folder, rows=5, text="Request %d"):
    """A chat this build cannot fully read: a later build's record part after five requests."""
    store = SessionStore(folder, str(uuid.uuid4()))
    state = store.new("claude_subscription")
    store.save(state)
    for index in range(rows):
        state["messages"].append({"role": "user", "text": text % index})
        reserve_invocation(state, state["id"] + "/call-%d" % index, "%064x" % index, "('document', 1)")
        store.save(state)
    with store.path.open("ab") as handle:
        handle.write(b'{"seq": %d, "projects": {"set": {}}}\n' % (rows + 1))
    return store, state["id"]


class KeptCopyTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = folder.name

    def kept(self, store):
        return sorted(store.path.parent.glob(glob.escape(store.path.name) + ".*.unreadable"))

    def test_a_second_instance_never_replaces_another_instances_kept_copy(self):
        """Case 2. Two instances load the chat in the same second; the first links the kept name between the
        second's check that the name is free and its own link, then rewrites the chat (its first save)."""
        store, chat = unreadable_chat(self.folder)
        original = store.path.read_bytes()
        first, second = SessionStore(self.folder, chat), SessionStore(self.folder, chat)
        real_link = os.link
        interleaved = []

        def link(source, target, *args, **kwargs):
            if not interleaved:
                interleaved.append(True)
                with patch.object(session_journal.os, "link", real_link):
                    first.save(first.load())
            return real_link(source, target, *args, **kwargs)

        with patch.object(session_journal.os, "link", link):
            second.load()
        self.assertTrue(interleaved)
        kept = [path.read_bytes() for path in self.kept(store)]
        self.assertTrue(original in kept, "the unreadable history is no longer kept anywhere: %d kept file(s), "
                        "holding %s requests" % (len(kept), [data.count(b"Request ") for data in kept]))

    def test_binding_a_key_leaves_no_piece_in_a_kept_copy(self):
        """Case 3. A key the chat held, bound after the chat was kept as unreadable."""
        store, chat = unreadable_chat(self.folder, text="My key is " + KEY + " (%d)")
        controller = Controller(self.folder, session_id=chat)
        self.addCleanup(controller.close)
        controller.set_document(("journal-review2", 1))
        self.assertEqual(len(self.kept(store)), 1, "precondition: the chat was kept")
        controller.api_key = KEY
        controller.save()
        self.assertEqual(pieces(store.path.read_bytes()), [])
        self.assertEqual([pieces(path.read_bytes()) for path in self.kept(store)], [[]],
                         "a bound key is redacted wherever this chat's history is kept")


if __name__ == "__main__":
    unittest.main()
