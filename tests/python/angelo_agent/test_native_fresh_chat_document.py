# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A chat with nothing of its document follows a new document quietly; one with work still says it changed.

Dependency-free: the controller's own document binding; no Blender, provider process or model. 1.0.5 screenshot
review row 22: a fresh, unconnected chat showed "Document changed. Start New Chat before issuing scene commands."
on first launch when a file opened after startup (Finder), and its connect cards collapsed to the reconnect row.
The in-app check is ``test_blender_fresh_chat_document``.
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent.controller import Controller  # noqa: E402

FIRST, SECOND = ("first-document", 1), ("second-document", 2)


class DocumentBindingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.controller = Controller(self.directory.name)
        self.controller.set_document(FIRST)

    def tearDown(self):
        self.controller.close()
        self.directory.cleanup()

    def notices(self):
        self.controller.pump_events()
        return [str(m.get("text")) for m in self.controller.state["messages"] if "Document changed" in str(m.get("text"))]

    def test_a_fresh_chat_follows_the_new_document_without_a_notice(self):
        self.controller.set_document(SECOND)
        self.assertEqual(self.controller.document, SECOND)
        self.assertFalse(self.controller.document_invalid)
        self.assertEqual(self.notices(), [])
        self.assertEqual(self.controller.state["messages"], [], "The chat stays empty: its welcome shows")

    def test_host_notices_alone_do_not_bind_a_chat_to_its_document(self):
        self.controller.emit("message", {"role": "system", "text": "Added Head – Stylized to start from (GEO-head)"})
        self.controller.pump_events()
        self.controller.set_document(SECOND)
        self.assertFalse(self.controller.document_invalid)
        self.assertEqual(self.notices(), [])

    def test_a_chat_with_a_message_still_says_the_document_changed(self):
        self.controller.state["messages"].append({"role": "user", "text": "Make a chair"})
        self.controller.set_document(SECOND)
        self.assertTrue(self.controller.document_invalid)
        self.assertEqual(self.controller.document, FIRST, "Its turns stay bound to the document they were about")
        self.assertEqual(len(self.notices()), 1)

    def test_a_running_turn_or_a_native_thread_still_binds_the_chat(self):
        for bind in (lambda c: setattr(c, "busy", True), lambda c: c.state.__setitem__("native_id", "thread-1")):
            with self.subTest(bind=bind):
                controller = Controller(tempfile.mkdtemp(dir=self.directory.name))
                try:
                    controller.set_document(FIRST)
                    bind(controller)
                    controller.set_document(SECOND)
                    self.assertTrue(controller.document_invalid)
                finally:
                    controller.busy = False
                    controller.close()


if __name__ == "__main__":
    unittest.main()
