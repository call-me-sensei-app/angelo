# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""A missing empty msgstr must not survive a catalog conflict resolution."""
import suite_isolation  # noqa: F401  Standalone provider isolation.

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/angelo"))
from i18n import po  # noqa: E402


class CatalogSyntaxTests(unittest.TestCase):
    def test_missing_msgstr_is_rejected_at_every_entry_boundary(self):
        for text in ('msgid "World"\n', 'msgid "World"\n\nmsgid "Steering"\nmsgstr ""\n',
                     'msgid "World"\nmsgid "Steering"\nmsgstr ""\n'):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "Missing msgstr"):
                po.parse(text)

    def test_empty_and_contextual_multiline_translations_remain_valid(self):
        text = ('msgid "World"\nmsgstr ""\n\nmsgctxt "Angelo"\nmsgid "Steering"\n'
                'msgstr "One"\n" two"\n')
        _header, entries = po.parse(text)
        self.assertEqual([(entry.key, entry.str) for entry in entries],
                         [(("", "World"), ""), (("Angelo", "Steering"), "One two")])
        for valid in ('#, fuzzy\nmsgid "One"\nmsgstr ""\n',
                      'msgid "One"\nmsgid_plural "Many"\nmsgstr[0] ""\nmsgstr[1] ""\n'):
            with self.subTest(valid=valid):
                self.assertTrue(po.parse(valid)[1])


if __name__ == "__main__":
    unittest.main()
