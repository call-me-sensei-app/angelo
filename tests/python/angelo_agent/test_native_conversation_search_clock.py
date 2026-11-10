# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual journal/search reads under the existing coarse-clock cache contract; no Blender or provider."""
import suite_isolation  # noqa: F401

import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
from angelo_agent import change_clock, conversation_search, session_journal

SECOND = 10_000_000
STAMP_NS = 1_700_000_000_000_000_000
STAMP = STAMP_NS // 100 + change_clock._EPOCH_AS_FILETIME


class SearchClockTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        (self.root / 'sessions').mkdir()
        self.path = self.root / 'sessions/chat.json'
        self.write('red brick')
        self.index = conversation_search.SearchIndex(self.root)
        self.clock = STAMP
        self.missing_identity = False
        real_stat, real_fstat = os.stat, os.fstat

        def coarse_stat(path, *args, **kwargs):
            info = real_stat(path, *args, **kwargs)
            if Path(path) != self.path:
                return info
            # Two real same-sized writes share ALL coarse timestamps; actual
            # device/inode identity still comes from the filesystem producer.
            return coarse_info(info)

        def coarse_info(info):
            return SimpleNamespace(st_dev=info.st_dev, st_ino=0 if self.missing_identity else info.st_ino,
                st_size=info.st_size, st_mtime_ns=STAMP_NS, st_ctime_ns=STAMP_NS, st_birthtime_ns=STAMP_NS,
                st_nlink=info.st_nlink, st_mode=info.st_mode)

        volume = SimpleNamespace(step=SECOND, below=SECOND, changes=True, fine=False, restamps=False)
        for context in (patch.object(conversation_search.os, 'stat', side_effect=coarse_stat),
                        patch.object(session_journal.os, 'fstat', side_effect=lambda fd: coarse_info(real_fstat(fd))),
                        patch.object(change_clock, 'volume', return_value=volume),
                        patch.object(change_clock, 'system_time', side_effect=lambda: self.clock),
                        patch.object(change_clock, 'settle', side_effect=AssertionError('UI search must not wait'))):
            context.start()
            self.addCleanup(context.stop)

    def write(self, text, path=None):
        (path or self.path).write_text(json.dumps({'messages': [{'role': 'user', 'text': text}]}))

    def matches(self, word):
        return self.index.search([{'id': 'chat', 'title': 'Project'}], word, lambda row: row['title'])

    def test_same_coarse_signature_never_keeps_the_first_write(self):
        self.index.refresh(['chat'], budget=1, now=0)
        self.assertEqual(self.matches('red'), [])
        self.write('blue tile')  # Same serialized size and witnessed timestamps.
        self.index.refresh(['chat'], budget=1, now=3)
        self.assertEqual(self.matches('red'), [])
        self.assertEqual(self.matches('blue'), [])  # Not certified yet: title-only.
        self.clock += 2 * SECOND
        self.index.refresh(['chat'], budget=1, now=6)
        self.assertEqual(self.matches('red'), [])
        self.assertEqual(len(self.matches('blue')), 1)

    def test_settled_unchanged_poll_reuses_lines_and_keypress_performs_no_io(self):
        self.clock += 2 * SECOND
        with patch.object(session_journal, 'read', wraps=session_journal.read) as reader:
            self.index.refresh(['chat'], budget=1, now=0)
            self.index.refresh(['chat'], budget=1, now=3)
            self.assertEqual(reader.call_count, 1)
        with patch.object(conversation_search.os, 'stat', side_effect=AssertionError('keypress stat')), \
                patch.object(session_journal, 'read', side_effect=AssertionError('keypress read')):
            self.assertEqual(len(self.matches('red')), 1)
            self.assertEqual(self.matches('blue'), [])

    def test_same_time_size_replacement_invalidates_cached_request_by_file_identity(self):
        self.clock += 2 * SECOND
        self.index.refresh(['chat'], budget=1, now=0)
        replacement = self.path.with_suffix('.new')
        self.write('blue tile', replacement)
        os.replace(replacement, self.path)
        self.index.refresh(['chat'], budget=1, now=3)
        self.assertEqual(self.matches('red'), [])
        self.assertEqual(len(self.matches('blue')), 1)

    def test_replacement_during_read_cannot_stamp_old_content_with_new_identity(self):
        self.clock += 2 * SECOND
        read = session_journal.read
        replacement = self.path.with_suffix('.new')
        self.write('blue tile', replacement)

        def racing_read(*args, **kwargs):
            result = read(*args, **kwargs)
            os.replace(replacement, self.path)
            return result

        with patch.object(session_journal, 'read', side_effect=racing_read):
            self.index.refresh(['chat'], budget=1, now=0)
        self.assertEqual(self.matches('red'), [])
        self.index.refresh(['chat'], budget=1, now=3)
        self.assertEqual(len(self.matches('blue')), 1)

    def test_path_aba_does_not_attribute_another_descriptors_text_to_the_original(self):
        self.clock += 2 * SECOND
        read = session_journal.read
        replacement, original = self.path.with_suffix('.new'), self.path.with_suffix('.held')
        self.write('blue tile', replacement)

        def aba_read(*args, **kwargs):
            os.replace(self.path, original)
            os.replace(replacement, self.path)
            try:
                return read(*args, **kwargs)
            finally:
                os.replace(self.path, replacement)
                os.replace(original, self.path)

        with patch.object(session_journal, 'read', side_effect=aba_read):
            self.index.refresh(['chat'], budget=1, now=0)
        self.assertEqual(self.matches('blue'), [])
        self.index.refresh(['chat'], budget=1, now=3)
        self.assertEqual(len(self.matches('red')), 1)
        self.assertEqual(self.matches('blue'), [])

    def test_transient_read_failure_does_not_certify_an_empty_transcript(self):
        self.clock += 2 * SECOND
        with patch.object(session_journal, 'read', side_effect=OSError('temporarily unavailable')):
            self.index.refresh(['chat'], budget=1, now=0)
        self.assertEqual(self.matches('red'), [])
        self.index.refresh(['chat'], budget=1, now=3)
        self.assertEqual(len(self.matches('red')), 1)

    def test_unknown_file_identity_has_no_reusable_content_key(self):
        self.clock += 2 * SECOND
        self.missing_identity = True
        with patch.object(session_journal, 'read', side_effect=AssertionError('unknown identity read')):
            self.index.refresh(['chat'], budget=1, now=0)
            self.index.refresh(['chat'], budget=1, now=3)
        self.assertEqual(self.matches('red'), [])
        self.assertEqual(len(self.matches('Project')), 1)


if __name__ == '__main__':
    unittest.main()
