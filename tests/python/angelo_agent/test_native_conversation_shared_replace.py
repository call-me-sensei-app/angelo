# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Conversation persistence under the existing NTFS sharing fixture; not Windows platform acceptance."""
import suite_isolation  # noqa: F401

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
from angelo_agent import conversation_search, conversations, shared_replace
from angelo_agent.sessions import SessionCollection
import ntfs_sharing
from test_native_conversation_recovery import add_chat

PAUSES = [shared_replace.PAUSE * i for i in range(1, shared_replace.ATTEMPTS)]


class CacheReplaceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.index = conversation_search.SearchIndex(self.root)
        self.old = json.dumps({'version': conversation_search.VERSION, 'chats': {}}).encode()
        self.index.path.write_bytes(self.old)
        self.index._entries = {'chat': {'signature': [1, 2, 3, 4, 5, 6], 'lines': ['new request']}}
        self.index._dirty = True
        self.sharing = ntfs_sharing.Sharing(self)

    def test_a_shared_reader_keeps_old_bytes_while_new_cache_is_published(self):
        fd = shared_replace.open_shared(self.index.path)
        try:
            self.index.save()
            with open(fd, 'rb', closefd=False) as stream:
                self.assertEqual(stream.read(), self.old)
        finally:
            os.close(fd)
        self.assertFalse(self.index._dirty)
        self.assertEqual(json.loads(self.index.path.read_text())['chats']['chat']['lines'], ['new request'])
        self.assertEqual(self.sharing.pauses, [])

    def test_cache_load_shares_delete_and_reads_the_original_opened_file(self):
        reader = conversation_search.SearchIndex(self.root)
        self.sharing.while_open(self.index.path, self.index.save)
        reader._load()
        self.assertEqual(self.sharing.action_errors, [])
        self.assertEqual(reader._entries, {})  # The reader's old file, not the newly published one.
        self.assertEqual(json.loads(self.index.path.read_text())['chats'], self.index._entries)
        self.assertEqual(self.sharing.pauses, [])

    def test_a_transient_foreign_reader_uses_only_the_existing_bounded_retries(self):
        self.sharing.hold(self.index.path, refusals=2)
        self.index.save()
        self.assertFalse(self.index._dirty)
        self.assertEqual(self.sharing.pauses, PAUSES[:2])
        self.assertEqual(json.loads(self.index.path.read_text())['chats'], self.index._entries)

    def test_permanent_refusal_preserves_old_cache_dirty_retry_and_no_document_save_notice(self):
        held = self.sharing.hold(self.index.path)
        notices = []
        with patch.object(shared_replace, 'on_refused', notices.append), \
                patch.object(conversation_search.time, 'monotonic', return_value=100) as clock:
            self.index.save()  # A best-effort cache remains dirty, not a successful document save.
            self.assertTrue(self.index._dirty)
            self.assertEqual(self.index.path.read_bytes(), self.old)
            self.assertEqual(self.sharing.pauses, PAUSES)
            self.index.save()
            self.assertEqual(self.sharing.pauses, PAUSES, 'No extra Windows retry pauses on the next UI tick')
            self.sharing.let_go(held)
            clock.return_value = 100 + shared_replace.RETRY_SECONDS
            self.index.save()
        self.assertFalse(self.index._dirty)
        self.assertIsNone(self.index._refused_at)
        self.assertEqual(json.loads(self.index.path.read_text())['chats'], self.index._entries)
        self.assertEqual(notices, [])


class ConversationReplaceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.collection = SessionCollection(self.root)
        self.identity = add_chat(self.root, self.collection, 'Keep this private conversation')
        self.record = self.collection.records[self.identity]
        self.live = self.root / 'sessions' / (self.identity + '.json')
        self.original = self.live.read_bytes()
        self.refs = self.root / 'references' / self.identity
        self.refs.mkdir(parents=True)
        self.picture = self.refs / 'picture.png'
        self.picture.write_bytes(b'private attachment')
        self.sharing = ntfs_sharing.Sharing(self)

    def test_entry_reader_shares_delete_with_atomic_metadata_publication(self):
        entry = self.root / 'entry.json'
        old, new = {'version': 1, 'recovery': True}, {'version': 1, 'recovery': False}
        conversations._write_entry(entry, old)
        self.sharing.while_open(entry, lambda: conversations._write_entry(entry, new))
        self.assertEqual(conversations._read_entry(entry), old)
        self.assertEqual(self.sharing.action_errors, [])
        self.assertEqual(json.loads(entry.read_text()), new)
        self.assertEqual(self.sharing.pauses, [])

    def test_delete_restore_moves_real_transcript_and_attachment_folder_with_shared_reader(self):
        fd = shared_replace.open_shared(self.live)
        self.sharing.hold(self.picture, refusals=2)
        try:
            conversations.delete(self.root, self.record, now=1)
            self.assertFalse(self.live.exists())
            kept = self.root / conversations.TRASH / self.identity / 'sessions' / self.live.name
            self.assertEqual(kept.read_bytes(), self.original)
            conversations.restore(self.root, self.identity)
            with open(fd, 'rb', closefd=False) as stream:
                self.assertEqual(stream.read(), self.original)
        finally:
            os.close(fd)
        self.assertEqual(self.live.read_bytes(), self.original)
        self.assertEqual(self.picture.read_bytes(), b'private attachment')
        self.assertEqual(self.sharing.pauses, PAUSES[:2])
        self.assertTrue(conversations.forget(self.root, self.identity))

    def test_refused_attachment_move_rolls_back_the_actual_transcript_without_losing_either(self):
        self.sharing.hold(self.picture)
        with self.assertRaises(shared_replace.Refused):
            conversations.delete(self.root, self.record, now=1)
        self.assertEqual(self.live.read_bytes(), self.original)
        self.assertEqual(self.picture.read_bytes(), b'private attachment')
        self.assertEqual(self.sharing.pauses, PAUSES)
        self.assertEqual(conversations.purge(self.root, now=10**9), 0)


if __name__ == '__main__':
    unittest.main()
