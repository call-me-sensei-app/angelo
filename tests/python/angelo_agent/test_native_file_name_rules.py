# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``file_name_rules``: the spellings of a name that are one file, read from the volume, never assumed per platform.

``test_blender_restore_keeps_file`` assumed macOS's rule (NFC and NFD name one file) and failed on Windows, where NTFS
folds case but not normalization (the 1.0.7 candidate on build4). These tests check the probes against the volume
they run on (each spelling's answer is what ``os.path.samefile`` finds) and each known volume's rule as a model.
"""

import os
import shutil
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import file_name_rules  # noqa: E402


class VolumeTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)

    def test_each_spelling_answers_as_the_volume_resolves_it(self):
        document = os.path.join(self.folder, unicodedata.normalize("NFC", "café.blend"))
        Path(document).write_bytes(b"x")
        found = file_name_rules.spellings(document)
        self.assertEqual(set(found), {"nfd", "case"})
        for label, (target, same) in found.items():
            actual = os.path.exists(target) and os.path.samefile(target, document)
            self.assertEqual(same, actual, label)

    def test_probes_leave_nothing_behind(self):
        file_name_rules.folds_case(self.folder)
        file_name_rules.folds_normalization(self.folder)
        self.assertEqual(os.listdir(self.folder), [])


class ModelTests(unittest.TestCase):
    """Each volume's rule, whatever volume the test runs on."""

    def spell(self, case, normalization):
        with mock.patch.object(file_name_rules, "folds_case", return_value=case), \
                mock.patch.object(file_name_rules, "folds_normalization", return_value=normalization):
            return {label: same for label, (_target, same) in
                    file_name_rules.spellings(os.path.join("folder", "café.blend")).items()}

    def test_ntfs_folds_case_but_not_normalization(self):
        self.assertEqual(self.spell(True, False), {"nfd": False, "case": True})

    def test_apfs_by_default_folds_both(self):
        self.assertEqual(self.spell(True, True), {"nfd": True, "case": True})

    def test_a_case_sensitive_volume_folds_neither(self):
        self.assertEqual(self.spell(False, False), {"nfd": False, "case": False})


if __name__ == "__main__":
    unittest.main()
