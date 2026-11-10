# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Real private-state moves retain every recovery copy across injected filesystem failures."""
import suite_isolation  # noqa: F401
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
from angelo_agent import conversations
from angelo_agent.sessions import SessionCollection, SessionStore


def add_chat(root, collection, text):
    identity = collection.create()
    store = SessionStore(root, identity)
    state = store.load()
    state["messages"].append({"role": "user", "text": text})
    store.save(state)
    collection.update(state)
    collection.save()
    return identity


class RecoveryCopies(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.collection = SessionCollection(self.root)
        self.identity = add_chat(self.root, self.collection, 'A transcript worth keeping')
        self.record = self.collection.records[self.identity]
        self.live = self.root/'sessions'/(self.identity+'.json')
        self.original = self.live.read_bytes()
        self.refs = self.root/'references'/self.identity
        self.refs.mkdir(parents=True)
        (self.refs/'picture.png').write_bytes(b'original private attachment')
        self.folder = self.root/conversations.TRASH/self.identity
        self.kept = self.folder/'sessions'/self.live.name
        self.kept_refs = self.folder/'references'/self.identity

    def fail_delete_and_main_rollback(self):
        real = conversations.shared_replace.replace
        def failing(source, target):
            if Path(source) == self.refs or Path(source) == self.kept:
                raise OSError('injected forward/rollback failure')
            return real(source, target)
        with patch.object(conversations.shared_replace, 'replace', side_effect=failing):
            with self.assertRaisesRegex(OSError, 'injected'):
                conversations.delete(self.root, self.record, now=1)

    def test_failed_main_rollback_retains_original_and_can_restore(self):
        self.fail_delete_and_main_rollback()
        self.assertFalse(self.live.exists())
        self.assertEqual(self.kept.read_bytes(), self.original)
        self.assertTrue((self.folder/conversations.ENTRY).is_file())
        self.assertEqual(conversations.purge(self.root, now=10**9), 0)
        conversations.restore(self.root, self.identity)
        self.assertEqual(self.live.read_bytes(), self.original)
        self.assertEqual((self.refs/'picture.png').read_bytes(), b'original private attachment')
        self.assertTrue(conversations.forget(self.root, self.identity))

    def test_failed_attachment_rollback_retains_until_settle(self):
        write, real = conversations._write_entry, conversations.shared_replace.replace
        writes = []
        def failing_write(path, entry):
            writes.append(path)
            if len(writes) == 2:
                raise OSError('injected final metadata failure')
            return write(path, entry)
        def failing_restore(source, target):
            if Path(source) == self.kept_refs:
                raise OSError('injected attachment rollback failure')
            return real(source, target)
        with patch.object(conversations, '_write_entry', side_effect=failing_write), \
                patch.object(conversations.shared_replace, 'replace', side_effect=failing_restore):
            with self.assertRaisesRegex(OSError, 'final metadata'):
                conversations.delete(self.root, self.record, now=1)
        self.assertEqual(self.live.read_bytes(), self.original)
        self.assertEqual((self.kept_refs/'picture.png').read_bytes(), b'original private attachment')
        self.assertEqual(conversations.purge(self.root, now=10**9), 0)
        conversations.settle(self.root, self.collection.records)
        self.assertEqual((self.refs/'picture.png').read_bytes(), b'original private attachment')
        self.assertFalse(self.folder.exists())

    def test_delete_retry_cannot_remove_previous_recovery(self):
        self.fail_delete_and_main_rollback()
        with self.assertRaises(FileExistsError):
            conversations.delete(self.root, self.record)
        self.assertEqual(self.kept.read_bytes(), self.original)
        self.assertTrue((self.folder/conversations.ENTRY).is_file())

    def test_restore_preflights_conflicting_attachment_before_any_move(self):
        conversations.delete(self.root, self.record)
        self.refs.mkdir(parents=True)
        (self.refs/'picture.png').write_bytes(b'new live attachment')
        with self.assertRaises(FileExistsError):
            conversations.restore(self.root, self.identity)
        self.assertFalse(self.live.exists())
        self.assertEqual(conversations.purge(self.root, now=10**9), 0)
        stored = json.loads((self.folder/conversations.ENTRY).read_text())
        self.assertEqual(stored['version'], 1)
        self.assertIs(stored['recovery'], True)
        self.assertEqual(self.kept.read_bytes(), self.original)
        self.assertEqual((self.refs/'picture.png').read_bytes(), b'new live attachment')
        self.assertEqual((self.kept_refs/'picture.png').read_bytes(), b'original private attachment')

    def test_settle_and_forget_keep_distinct_conflicting_copies(self):
        conversations.delete(self.root, self.record, now=1)
        os.replace(self.kept, self.live)
        self.refs.mkdir(parents=True)
        (self.refs/'picture.png').write_bytes(b'new live attachment')
        conversations.settle(self.root, self.collection.records)
        self.assertFalse(conversations.forget(self.root, self.identity))
        self.assertTrue((self.folder/conversations.ENTRY).is_file())
        self.assertEqual(conversations.purge(self.root, now=10**9), 0)
        self.assertEqual((self.kept_refs/'picture.png').read_bytes(), b'original private attachment')
        self.assertEqual((self.refs/'picture.png').read_bytes(), b'new live attachment')

    def test_purge_keeps_payload_without_transcript_or_readable_entry(self):
        self.kept_refs.mkdir(parents=True)
        payload = self.kept_refs/'orphan.png'
        payload.write_bytes(b'only surviving attachment')
        (self.folder/conversations.ENTRY).write_text('{broken')
        self.assertEqual(conversations.purge(self.root, now=10**9), 0)
        self.assertEqual(payload.read_bytes(), b'only surviving attachment')

    def test_rollback_never_overwrites_new_live_transcript(self):
        real = conversations.shared_replace.replace
        def failing(source, target):
            if Path(source) == self.refs:
                self.live.write_bytes(b'new live transcript')
                raise OSError('injected later forward failure')
            return real(source, target)
        with patch.object(conversations.shared_replace, 'replace', side_effect=failing):
            with self.assertRaises(OSError):
                conversations.delete(self.root, self.record)
        self.assertEqual(self.live.read_bytes(), b'new live transcript')
        self.assertEqual(self.kept.read_bytes(), self.original)
        self.assertTrue((self.folder/conversations.ENTRY).is_file())

    def test_failed_restore_retains_recovery_past_expiry(self):
        conversations.delete(self.root, self.record, now=1)
        real = conversations.shared_replace.replace
        def failing(source, target):
            if Path(source) == self.kept:
                raise OSError('injected restore main failure')
            if Path(source) == self.refs:
                raise OSError('injected restore rollback failure')
            return real(source, target)
        with patch.object(conversations.shared_replace, 'replace', side_effect=failing):
            with self.assertRaises(OSError):
                conversations.restore(self.root, self.identity)
        self.assertEqual(self.kept.read_bytes(), self.original)
        self.assertEqual(conversations.purge(self.root, now=10**9), 0)
        self.assertTrue((self.folder/conversations.ENTRY).is_file())
        self.assertEqual((self.refs/'picture.png').read_bytes(), b'original private attachment')


if __name__ == '__main__':
    unittest.main()
