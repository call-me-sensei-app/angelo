# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Mocked operation admission, not a wall-clock or filesystem latency benchmark."""
import suite_isolation  # noqa: F401
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
from angelo_agent.conversation_search import SearchIndex


class SearchDeadline(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.index = SearchIndex(self.directory.name)
        self.index._entries = {}  # No persisted-cache IO in this operation-admission fixture.
        self.clock, self.operations = 0.0, []
        self.versions, self.payloads = {}, {}

        def signature(identity):
            self.operations.append(('stat', identity))
            self.clock += 1
            return [self.versions.get(identity, 1), 10]

        def read(identity):
            self.operations.append(('read', identity))
            self.clock += 1
            self.index._entries[identity] = {'signature': [self.versions.get(identity, 1), 10],
                                             'lines': [self.payloads.get(identity, 'request ' + identity)]}
            self.index._folded.pop(identity, None)
            self.index._dirty = True

        for patch in (mock.patch('angelo_agent.conversation_search.time.perf_counter', lambda: self.clock),
                      mock.patch.object(self.index, '_signature', signature),
                      mock.patch.object(self.index, '_read', read), mock.patch.object(self.index, 'save')):
            patch.start()
            self.addCleanup(patch.stop)

    def finish(self, identities, now=0):
        for _ in range(2 * len(identities) + 2):
            before = len(self.operations)
            done = self.index.refresh(identities, budget=1, now=now)
            self.assertLessEqual(len(self.operations) - before, 1)
            if done:
                return
        self.fail('The finite signature/read queues must progress to completion')

    def test_zero_budget_starts_no_stat_or_read_and_next_draw_progresses(self):
        ids = ['a', 'b', 'c']
        self.assertFalse(self.index.refresh(ids, budget=0, now=0))
        self.assertEqual(self.operations, [])
        self.assertFalse(self.index.complete)
        self.assertFalse(self.index.refresh(ids, budget=1, now=0))
        self.assertEqual(self.operations, [('stat', 'a')])

    def test_signature_pass_resumes_without_restarting_or_scanning_entire_list(self):
        ids = list('abcdefgh')
        self.assertFalse(self.index.refresh(ids, budget=2, now=0))
        self.assertEqual(self.operations, [('stat', 'a'), ('stat', 'b')])
        self.assertFalse(self.index.refresh(ids, budget=2, now=0))
        self.assertEqual(self.operations, [('stat', 'a'), ('stat', 'b'), ('stat', 'c'), ('stat', 'd')])

    def test_stat_and_read_share_one_deadline(self):
        self.assertFalse(self.index.refresh(['a', 'b'], budget=3, now=0))
        self.assertEqual(self.operations, [('stat', 'a'), ('stat', 'b'), ('read', 'a')])
        self.assertFalse(self.index.complete)
        self.assertTrue(self.index.refresh(['a', 'b'], budget=1, now=0))
        self.assertEqual(self.operations[-1], ('read', 'b'))

    def test_every_step_is_bounded_and_completion_waits_for_both_phases(self):
        self.finish(['a', 'b', 'c'])
        self.assertTrue(self.index.complete)
        self.assertEqual(self.operations, [('stat', value) for value in 'abc'] + [('read', value) for value in 'abc'])

    def test_query_change_recomputes_matches_while_index_progresses(self):
        records = [{'id': 'a', 'title': 'red'}, {'id': 'b', 'title': 'green'}]
        title = lambda record: record['title']
        self.payloads['a'] = 'blue shape'
        self.index.refresh(['a', 'b'], budget=1, now=0)
        self.assertEqual([r['id'] for r, _ in self.index.search(records, 'red', title)], ['a'])
        self.assertEqual(self.index.search(records, 'blue', title), [])
        self.finish(['a', 'b'])
        self.assertEqual([r['id'] for r, _ in self.index.search(records, 'blue', title)], ['a'])
        self.assertEqual([r['id'] for r, _ in self.index.search(records, 'green', title)], ['b'])

    def test_changed_revision_before_pending_read_uses_current_payload(self):
        self.payloads['a'] = 'old text'
        self.index.refresh(['a', 'b'], budget=1, now=0)
        self.versions['a'], self.payloads['a'] = 2, 'new text'
        self.finish(['a', 'b'])
        records = [{'id': 'a', 'title': ''}]
        self.assertEqual(self.index.search(records, 'old', lambda row: row['title']), [])
        self.assertEqual(len(self.index.search(records, 'new', lambda row: row['title'])), 1)
        self.assertEqual(self.index._entries['a']['signature'][0], 2)

    def test_deleted_and_new_chats_do_not_reset_or_corrupt_pending_progress(self):
        self.index.refresh(['a', 'b', 'c'], budget=1, now=0)
        self.index.forget('a')
        self.finish(['b', 'c', 'd'])
        self.assertNotIn(('read', 'a'), self.operations)
        self.assertEqual([identity for action, identity in self.operations if action == 'read'], ['b', 'c', 'd'])
        records = [{'id': identity, 'title': 'match'} for identity in ('d', 'b', 'c')]
        self.assertEqual([row['id'] for row, _ in self.index.search(records, 'match', lambda row: row['title'])],
                         ['d', 'b', 'c'])

    def test_due_recheck_resumes_and_only_reads_changed_signatures(self):
        self.finish(['a', 'b'])
        self.operations.clear()
        self.versions['b'] = 2
        self.finish(['a', 'b'], now=3)
        self.assertEqual(self.operations, [('stat', 'a'), ('stat', 'b'), ('read', 'b')])

    def test_missing_file_is_not_requeued_while_the_pass_progresses(self):
        original = self.index._signature
        def signature(identity):
            value = original(identity)
            return None if identity == 'a' else value
        with mock.patch.object(self.index, '_signature', signature):
            self.finish(['a', 'b', 'c'])
        self.assertEqual(self.operations, [('stat', value) for value in 'abc']
                         + [('read', value) for value in 'bc'])
        self.assertTrue(self.index.complete)


if __name__ == '__main__':
    unittest.main()
