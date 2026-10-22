# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A message that never reached the agent: a translatable notice, and the text offered back.

``codex_turns`` abandons a request before ``turn/start`` when Stop arrives during
the open-turn guard, or when an older turn never ends. The real ``Controller``
runs with a stand-in provider (no subprocess or inference) and ``drafts`` with
stand-in chat inputs (no Blender).
"""

import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.codex_turns import ABANDONED, STUCK, MessageNotSent, TurnStartAbandoned  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.drafts import restore_unsent  # noqa: E402

NOTICE = "Your message wasn't sent. Send it again when you're ready."
TEXT = "Make the bridge longer"


class Provider:
    """Connects warm, then fails before turn/start the way codex_turns does."""

    def __init__(self, failure, *, stop_first=False):
        self.failure, self.stop_first, self.closed = failure, stop_first, False

    def bind_request(self, context):
        self.context = context

    def run(self, native_id, text, observation=None):
        if self.stop_first:
            self.context.host.stop()
        raise self.failure

    def stop_request(self, fence):
        pass

    def close(self):
        self.closed = True


class MessageNotSentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.c = Controller(self.directory.name, session_id=str(uuid.uuid4()))
        self.addCleanup(self.c.close)
        self.c.set_document(("document", 1))
        self.c.state["native_id"] = "warm-thread"
        # Stand-in references: no files are read (ReferenceStore owns those checks).
        for name, value in (("checked_path", Path(self.directory.name)), ("prompt_context", [])):
            stand_in = patch.object(self.c.references, name, return_value=value)
            stand_in.start()
            self.addCleanup(stand_in.stop)

    def request(self, provider, reference=None):
        self.c.provider = provider
        if reference:
            self.c.attachments = [reference]
        worker = SimpleNamespace(start=lambda: None, is_alive=lambda: False)
        with patch("angelo_agent.controller.threading.Thread", return_value=worker):
            self.c.send(TEXT)
            self.assertEqual(self.c.attachments, [])  # Sending consumes the composer's references.
            self.c._run(TEXT)
        self.c.pump_events()

    def notices(self):
        return [m for m in self.c.state["messages"] if m.get("text") == NOTICE]

    def test_stop_during_the_guard_shows_the_notice_and_offers_the_text_back(self):
        reference = {"name": "ref.png", "kind": "image", "bytes": 10, "path": "/nowhere/ref.png"}
        provider = Provider(TurnStartAbandoned(ABANDONED), stop_first=True)
        self.request(provider, reference)
        notices = self.notices()
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["role"], "system")
        self.assertEqual(notices[0]["i18n"], [NOTICE, {}])  # Re-rendered in the interface language.
        self.assertFalse(any(m.get("role") == "error" for m in self.c.state["messages"]))
        self.assertEqual(self.c.status, "Interrupted")
        self.assertFalse(self.c.busy)
        self.assertEqual(self.c.unsent_draft, TEXT)
        self.assertEqual(self.c.attachments, [reference])
        self.assertFalse(provider.closed, "Stop leaves the warm connection open")
        self.assertIs(self.c.provider, provider)

    def test_a_dead_older_turn_shows_the_notice_instead_of_an_english_error(self):
        provider = Provider(MessageNotSent(STUCK))
        self.request(provider)
        self.assertEqual(len(self.notices()), 1)
        self.assertFalse(any(m.get("role") == "error" for m in self.c.state["messages"]))
        self.assertFalse(any(STUCK in m.get("text", "") for m in self.c.state["messages"]))
        self.assertEqual(self.c.status, "Failed")
        self.assertEqual(self.c.unsent_draft, TEXT)
        self.assertTrue(provider.closed)  # The guard already restarted it; the request drops the provider.
        self.assertIsNone(self.c.provider)

    def test_other_failures_keep_their_error_and_offer_nothing_back(self):
        from angelo_agent.protocol import RpcError
        self.request(Provider(RpcError("Codex turn failed")))
        self.assertEqual(self.notices(), [])
        self.assertTrue(any(m.get("role") == "error" and "Codex turn failed" in m["text"] for m in self.c.state["messages"]))
        self.assertIsNone(self.c.unsent_draft)
        self.assertEqual(self.c.attachments, [])

    def test_a_reference_added_since_is_not_replaced(self):
        newer = {"name": "new.png", "kind": "image", "bytes": 5, "path": "/nowhere/new.png"}
        self.c.provider = Provider(TurnStartAbandoned(ABANDONED), stop_first=True)
        self.c.attachments = [{"name": "old.png", "kind": "image", "bytes": 5, "path": "/nowhere/old.png"}]
        worker = SimpleNamespace(start=lambda: None, is_alive=lambda: False)
        with patch("angelo_agent.controller.threading.Thread", return_value=worker):
            self.c.send(TEXT)
            self.c._run(TEXT)
        self.c.attachments = [newer]  # The person attached something before the UI caught up.
        self.c.pump_events()
        self.assertEqual(self.c.attachments, [newer])


class Line:
    def __init__(self, body=""):
        self.body, self.current_character = body, 0


def read(space):
    return (space.body, space.current_character) if space is not None else None


def write(space, draft):
    space.body, space.current_character = draft


class RestoreUnsentTests(unittest.TestCase):
    def controller(self, text=TEXT):
        return SimpleNamespace(unsent_draft=text, state={"id": "session-1"})

    def test_an_empty_input_gets_the_text_back_with_the_caret_after_it(self):
        empty, other = Line(), Line()
        controller = self.controller("Café ✓")
        self.assertTrue(restore_unsent(controller, active=True, spaces=[empty, other, None], saved={},
                                       read=read, write=write))
        # ConsoleLine.current_character is a character index: the caret after all six characters, not nine bytes.
        self.assertEqual((empty.body, empty.current_character), ("Café ✓", len("Café ✓")))
        self.assertEqual(other.body, "Café ✓")
        self.assertIsNone(controller.unsent_draft, "offered back once")

    def test_a_draft_the_person_started_is_never_overwritten(self):
        typing = Line("Something new")
        self.assertFalse(restore_unsent(self.controller(), active=True, spaces=[typing], saved={},
                                        read=read, write=write))
        self.assertEqual(typing.body, "Something new")

    def test_a_chat_not_on_screen_gets_it_in_its_saved_draft(self):
        saved = {}
        self.assertTrue(restore_unsent(self.controller(), active=False, spaces=[], saved=saved, read=read, write=write))
        self.assertEqual(saved["session-1"], (TEXT, len(TEXT)))
        saved["session-1"] = ("typed later", 3)
        self.assertFalse(restore_unsent(self.controller(), active=False, spaces=[], saved=saved, read=read, write=write))
        self.assertEqual(saved["session-1"], ("typed later", 3))

    def test_nothing_to_offer(self):
        line = Line()
        self.assertFalse(restore_unsent(self.controller(""), active=True, spaces=[line], saved={}, read=read, write=write))
        self.assertEqual(line.body, "")


if __name__ == "__main__":
    unittest.main()
