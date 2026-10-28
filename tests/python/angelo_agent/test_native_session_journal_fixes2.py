# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fixes for the conversation journal's kept copies, redaction, chat switches and index saves.

The repros are in ``test_native_session_journal_review2.py``.

Each test here failed before its fix:

* a kept copy's final name is created exclusively, also without hard links, so a
  second instance never replaces another instance's copy, and a copy that fails
  leaves neither its temporary file nor its name (``session_kept.preserve``);
* a key bound after a chat was kept is redacted in the kept copies, which still
  parse, and a copy the redaction would leave unparseable is left as it was
  (``session_kept.redact_kept``);
* a switch to another chat writes nothing when the file is already one base line
  holding the chat, and compacts it otherwise (``SessionStore.leave``);
* the switcher index is saved at most once a second by ``SessionCollection``
  itself (``note``, ``save_if_due``), not by ``ui.pump``.
"""

import errno
import json
import os
import stat
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import session_journal, session_kept, sessions as sessions_module  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.invocations import reserve_invocation  # noqa: E402
from angelo_agent.sessions import SessionCollection, SessionStore  # noqa: E402

KEY = "sk-test-fake-0123456789abcdef"  # Not a credential: a made-up key; no credential is read.


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


def kept(store):
    return sorted(store.path.parent.glob(store.path.name + ".*.unreadable"))


def identity(path):
    status = os.stat(path)
    return status.st_ino, status.st_size, status.st_mtime_ns


class KeptNameTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = folder.name

    def test_without_hard_links_a_copy_never_replaces_a_name_another_instance_took(self):
        store, chat = unreadable_chat(self.folder)
        original, theirs, taken = store.path.read_bytes(), b"another instance's kept copy", []

        def link(source, target, *args, **kwargs):
            if not taken:  # Another instance keeps its copy under this name after this one found it free.
                taken.append(target)
                Path(target).write_bytes(theirs)
            raise OSError(errno.EPERM, "no hard links (FAT, exFAT)")
        with patch.object(session_journal.os, "link", link):
            loaded = SessionStore(self.folder, chat).load()
        copies = {path.name: path.read_bytes() for path in kept(store)}
        self.assertEqual(copies.get(Path(taken[0]).name), theirs, "the other instance's copy is never replaced")
        self.assertEqual(sorted(copies.values()), sorted([theirs, original]))
        mine = next(name for name, data in copies.items() if data == original)
        self.assertTrue(any(mine in m.get("text", "") for m in loaded["messages"]), "the chat names its own copy")


class KeptRedactionTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = folder.name

    def test_a_bound_key_is_redacted_only_where_it_was_and_atomically(self):
        store, chat = unreadable_chat(self.folder, text="My key is " + KEY + " (%d)")
        original = store.path.read_bytes()
        controller = Controller(self.folder, session_id=chat)
        self.addCleanup(controller.close)
        [copy] = kept(store)
        self.assertEqual(copy.stat().st_ino, store.path.stat().st_ino, "precondition: a hard link to the chat file")
        controller.api_key = KEY
        redacted = original.replace(KEY.encode(), b"[REDACTED]")
        self.assertEqual(copy.read_bytes(), redacted, "only the key's bytes change")
        for line in redacted.splitlines():
            json.loads(line)
        self.assertEqual(store.path.read_bytes(), original, "never changed in place: the chat is its next save's")
        self.assertEqual(stat.S_IMODE(copy.stat().st_mode), 0o600)
        self.assertEqual(list(store.path.parent.glob("*.tmp")), [])
        controller.save()
        self.assertNotIn(KEY.encode(), store.path.read_bytes())
        self.assertEqual(copy.read_bytes(), redacted)

    def test_a_copy_the_redaction_would_leave_unparseable_is_left_as_it_was(self):
        """A key whose bytes also occur outside a JSON string (here as a number) cannot be redacted there."""
        store = SessionStore(self.folder, str(uuid.uuid4()))
        key = "1234567890123456"
        copy = store.path.parent / (store.path.name + ".20261015T000000Z.unreadable")
        data = b'{"version": 2, "messages": [{"text": "x%s"}], "n": %s}\n' % (key.encode(), key.encode())
        copy.write_bytes(data)
        self.assertEqual(session_kept.redact_kept(store.path, key), [copy.name], "reported as still holding it")
        self.assertEqual(copy.read_bytes(), data)
        self.assertEqual(session_kept.redact_kept(store.path, KEY), [], "a copy without the key is not touched")


class LeaveTests(unittest.TestCase):
    """A switch to another chat (``ui.show_session``) leaves the chat one base line, writing only when needed."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = folder.name
        self.controller = Controller(self.folder, session_id=str(uuid.uuid4()))
        self.addCleanup(self.controller.close)
        self.controller.set_document(("journal-fixes2", 1))
        self.store, self.state = self.controller.store, self.controller.state
        for index in range(5):
            self.state["messages"].append({"role": "user", "text": "Request %d" % index})
            self.controller.save()

    def one_base_line(self):
        data = self.store.path.read_bytes()
        self.assertEqual(data.count(b"\n"), 1, "one base line, readable by every build")
        return json.loads(data)

    def test_a_chat_already_one_base_line_is_left_without_a_write(self):
        self.controller.save(leave=True)
        self.one_base_line()
        before = identity(self.store.path)
        self.controller.save(leave=True)  # Back and forth with nothing new.
        self.assertEqual(identity(self.store.path), before)
        self.assertNotEqual(self.one_base_line()["updated"], self.state["updated"], "the switch's time is not written")
        self.controller.save()  # The chat's next save writes it.
        self.assertEqual(self.store.last_write, "append")

    def test_a_chat_with_records_or_a_change_is_compacted_when_left(self):
        self.controller.save(leave=True)
        for change in ("record", "change"):
            with self.subTest(change=change):
                self.state["messages"].append({"role": "user", "text": "Another " + change})
                if change == "record":
                    self.controller.save()
                self.controller.save(leave=True)
                self.assertEqual(self.one_base_line()["messages"][-1]["text"], "Another " + change)

    def test_a_chat_not_saved_since_its_load_is_rewritten_when_left(self):
        with self.store.path.open("ab") as handle:
            handle.write(b'{"seq": 1, "set": {"model": "torn')  # A crash's torn last record, there before the load.
        again = SessionStore(self.folder, self.state["id"])
        state = again.load()
        again.leave(state)
        self.assertEqual(again.last_write, "rewrite")
        self.assertEqual(self.store.path.read_bytes().count(b"\n"), 1)


class IndexSaveTests(unittest.TestCase):
    def test_a_noted_change_is_saved_at_most_once_a_second(self):
        with tempfile.TemporaryDirectory() as folder:
            switcher = SessionCollection(folder)
            state = SessionStore(folder, switcher.active).load()
            clock = [1000.0]
            with patch.object(sessions_module.time, "monotonic", lambda: clock[0]):
                switcher.save()
                state["messages"].append({"role": "user", "text": "A noted title"})
                switcher.note(state)
                switcher.save_if_due()
                self.assertNotIn("A noted title", switcher.path.read_text(encoding="utf-8"))
                clock[0] += 1.5
                switcher.save_if_due()
                self.assertIn("A noted title", switcher.path.read_text(encoding="utf-8"))
                before = identity(switcher.path)
                clock[0] += 5
                switcher.save_if_due()  # Nothing noted since.
                self.assertEqual(identity(switcher.path), before)


if __name__ == "__main__":
    unittest.main()
