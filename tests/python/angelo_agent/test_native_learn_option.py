# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Teach me as you work is kept per chat, survives a restart and never costs the transcript's draw a disk read.

Covered (``learn_option``): off everywhere before anything is read and for a chat never set; a choice is written to
``learn.json`` and read back by a fresh process's ``load``; turning it off removes the chat and leaves the others;
a damaged or foreign file is "off everywhere" and the next choice rewrites it whole; the list keeps at most
``MAX_CHATS`` chats, dropping the oldest; a write that fails raises and leaves memory as the file is, with no
temporary file left; ``teaching`` reads no file; a chat id that is not a short string is refused. No Blender.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import learn_option  # noqa: E402


class TeachOption(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.addCleanup(learn_option.forget)
        self.folder = folder.name
        learn_option.forget()

    def saved(self):
        return json.loads((Path(self.folder) / learn_option.NAME).read_text(encoding="utf-8"))

    def test_off_until_read_and_for_a_chat_never_set(self):
        self.assertFalse(learn_option.teaching(self.folder, "chat-a"))
        learn_option.load(self.folder)   # No file yet.
        self.assertFalse(learn_option.teaching(self.folder, "chat-a"))

    def test_a_choice_is_saved_and_read_back_by_a_fresh_start(self):
        learn_option.set_teaching(self.folder, "chat-a", True)
        learn_option.set_teaching(self.folder, "chat-b", True)
        self.assertEqual(self.saved(), {"version": 1, "teach": ["chat-a", "chat-b"]})
        learn_option.forget()   # A new process.
        self.assertFalse(learn_option.teaching(self.folder, "chat-a"), "nothing is read while drawing")
        learn_option.load(self.folder)
        self.assertTrue(learn_option.teaching(self.folder, "chat-a"))
        self.assertTrue(learn_option.teaching(self.folder, "chat-b"))
        learn_option.set_teaching(self.folder, "chat-a", False)
        self.assertEqual(self.saved()["teach"], ["chat-b"])
        self.assertFalse(learn_option.teaching(self.folder, "chat-a"))
        self.assertEqual(sorted(path.name for path in Path(self.folder).iterdir()), [learn_option.NAME])

    def test_a_damaged_or_foreign_file_is_off_everywhere_and_is_rewritten_whole(self):
        for text in ("{not json", "[]", '{"teach": "chat-a"}', '{"teach": [7, "", "%s", "ok"]}' % ("x" * 500)):
            (Path(self.folder) / learn_option.NAME).write_text(text, encoding="utf-8")
            learn_option.load(self.folder)
            self.assertFalse(learn_option.teaching(self.folder, "chat-a"), text[:20])
        self.assertTrue(learn_option.teaching(self.folder, "ok"), "a readable id beside unreadable ones is kept")
        learn_option.set_teaching(self.folder, "chat-a", True)
        self.assertEqual(self.saved(), {"version": 1, "teach": ["ok", "chat-a"]})

    def test_the_list_is_bounded_oldest_first(self):
        with mock.patch.object(learn_option, "MAX_CHATS", 3):
            for name in ("a", "b", "c", "d"):
                learn_option.set_teaching(self.folder, name, True)
            learn_option.set_teaching(self.folder, "b", True)   # Set again: it becomes the newest.
            self.assertEqual(self.saved()["teach"], ["c", "d", "b"])
            self.assertFalse(learn_option.teaching(self.folder, "a"))

    def test_a_failed_write_raises_and_changes_nothing(self):
        learn_option.set_teaching(self.folder, "chat-a", True)
        with mock.patch.object(learn_option.shared_replace, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                learn_option.set_teaching(self.folder, "chat-b", True)
        self.assertFalse(learn_option.teaching(self.folder, "chat-b"))
        self.assertEqual(self.saved()["teach"], ["chat-a"])
        self.assertEqual(sorted(path.name for path in Path(self.folder).iterdir()), [learn_option.NAME])

    def test_asking_reads_no_file(self):
        learn_option.set_teaching(self.folder, "chat-a", True)
        with mock.patch.object(Path, "read_text", side_effect=AssertionError("a disk read while drawing")):
            self.assertTrue(learn_option.teaching(self.folder, "chat-a"))
            self.assertFalse(learn_option.teaching(self.folder, "chat-z"))
            self.assertFalse(learn_option.teaching(self.folder + "-other", "chat-a"))

    def test_a_chat_id_is_a_short_string(self):
        for bad in ("", None, 7, "x" * (learn_option.MAX_ID + 1)):
            with self.assertRaises(ValueError):
                learn_option.set_teaching(self.folder, bad, True)
        self.assertFalse((Path(self.folder) / learn_option.NAME).exists())


if __name__ == "__main__":
    unittest.main()
