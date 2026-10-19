# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Conversation-journal edge-case fixes, each pinned here (``..._review3.py`` holds the reproductions).

Each test here failed before its fix:

* a load removes an empty kept name a copy reserved and a crash never filled
  (``session_kept._copy_new``), once it is as stale as a leftover temporary
  file, and nothing else (``session_journal.discard_stale``);
* the redaction's guard checks every line, also past a damaged one
  (``session_kept._json_values``);
* a redaction that was refused is reported on standard error by the copy's
  name, never the key (``SessionStore.redact_kept``).
"""

import contextlib
import io
import os
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import session_kept  # noqa: E402
from angelo_agent.sessions import SessionStore  # noqa: E402

KEY = "sk-test-fake-0123456789abcdef"  # Not a credential: a made-up key; no credential is read.


def made(path, data=b"", seconds=120):
    """``path`` holding ``data``, last written ``seconds`` ago."""
    path.write_bytes(data)
    past = time.time() - seconds
    os.utime(path, (past, past))
    return path


class PlaceholderTests(unittest.TestCase):
    def test_a_load_removes_a_stale_empty_kept_name_of_its_chat_only(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SessionStore(folder, str(uuid.uuid4()))
            store.save(store.new("claude_subscription"))
            other = SessionStore(folder, str(uuid.uuid4()))
            beside = store.path.parent
            stale = made(beside / (store.path.name + ".20261015T120000Z.unreadable"))
            fresh = made(beside / (store.path.name + ".20261015T120001Z.unreadable"), seconds=0)  # Being filled now.
            whole = made(beside / (store.path.name + ".20261015T120002Z.unreadable"), b'{"version": 1}\n')
            theirs = made(beside / (other.path.name + ".20261015T120000Z.unreadable"))
            store.load()
            self.assertFalse(stale.exists(), "an empty kept name a crash left is removed")
            self.assertTrue(fresh.exists() and whole.exists() and theirs.exists())


class GuardTests(unittest.TestCase):
    def test_the_guard_checks_lines_past_a_damaged_one(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SessionStore(folder, str(uuid.uuid4()))
            key = "1234567890123456"
            copy = store.path.parent / (store.path.name + ".20261015T000000Z.unreadable")
            data = b'{"a": 1}\n{"seq": 2, damaged\n{"seq": 3, "n": %s, "t": "x%s"}\n' % (key.encode(), key.encode())
            copy.write_bytes(data)
            self.assertEqual(session_kept.redact_kept(store.path, key), [copy.name])
            self.assertEqual(copy.read_bytes(), data, "the line after the damage still parses")


class RefusedRedactionTests(unittest.TestCase):
    def test_a_refused_redaction_is_reported_by_name_without_the_key(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SessionStore(folder, str(uuid.uuid4()))
            copy = store.path.parent / (store.path.name + ".20261015T000000Z.unreadable")
            copy.write_bytes(b'{"version": 2, "n": 7, "t": "%s"}\n' % KEY.encode())
            errors = io.StringIO()
            with patch_refusal(), contextlib.redirect_stderr(errors):
                self.assertEqual(store.redact_kept(KEY), [copy.name])
            self.assertIn(copy.name, errors.getvalue())
            self.assertNotIn(KEY, errors.getvalue())
            self.assertNotIn(folder, errors.getvalue())


@contextlib.contextmanager
def patch_refusal():
    """A copy the redaction cannot write back (a read-only copy's replace failing, as on a locked volume)."""
    from unittest.mock import patch
    from angelo_agent import session_journal
    with patch.object(session_journal, "rewrite", side_effect=PermissionError(13, "Permission denied")):
        yield


if __name__ == "__main__":
    unittest.main()
