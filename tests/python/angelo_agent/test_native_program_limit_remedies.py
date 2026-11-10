# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Program cap refusals report their actual counts without changing admission."""
import suite_isolation  # noqa: F401
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import program_spec


def binding(number):
    return {"handle": "fixture-object-%d" % number, "revision": "observed-revision"}


def arguments(**fields):
    return {"mode": "APPLY", "operations": [{"kind": "create_primitive", "target": None,
            "parameters": {"primitive": "CUBE", "name": "Fixture cube", "alias": "fixture",
                           "location": [0, 0, 0]}}], **fields}


class ProgramLimitRemedies(unittest.TestCase):
    def refusal(self, **fields):
        with self.assertRaises(ValueError) as raised:
            program_spec.validate_program("angelo_scene_program", arguments(**fields))
        return str(raised.exception)

    def test_malformed_binding_list_is_distinct_from_a_cap(self):
        text = self.refusal(read_set={"handle": "fixture"})
        self.assertIn("lists of {handle, revision}", text)
        self.assertNotIn("limited", text)

    def test_each_set_names_its_own_count_and_shared_bound(self):
        for key in ("read_set", "write_set"):
            with self.subTest(key=key):
                text = self.refusal(**{key: [binding(i) for i in range(65)]})
                self.assertIn(key + " lists 65", text)
                self.assertIn("at most 64", text)
                self.assertIn("read_set and write_set together", text)
                self.assertIn("split", text)

    def test_combined_count_names_scene_settings_truthfully(self):
        text = self.refusal(read_set=[binding(i) for i in range(40)],
                            write_set=[binding(i) for i in range(40, 80)])
        self.assertIn("binds 80 existing objects and scene settings", text)
        self.assertIn("at most 64", text)
        self.assertIn("split", text)

    def test_shared_entries_still_count_once_and_exact_boundary_is_admitted(self):
        entries = [binding(i) for i in range(64)]
        result = program_spec.validate_program("angelo_scene_program", arguments(read_set=entries, write_set=entries))
        self.assertEqual(result["read_set"], entries)
        self.assertEqual(result["write_set"], entries)

    def test_operation_overflow_names_count_and_existing_bound(self):
        text = self.refusal(operations=[{}] * 33)
        self.assertIn("1-32 bounded operations", text)
        self.assertIn("this one has 33", text)
        self.assertIn("split", text)

    def test_numbers_follow_enforcing_constants(self):
        with patch.object(program_spec, "MAX_INPUT_OBJECTS", 3), patch.object(program_spec, "MAX_OPERATIONS", 2):
            self.assertIn("at most 3", self.refusal(read_set=[binding(i) for i in range(4)]))
            self.assertIn("1-2 bounded operations", self.refusal(operations=[{}] * 3))

    def test_empty_or_nonlist_operations_keep_their_existing_refusal(self):
        for operations in ([], {}, None):
            with self.subTest(operations=operations):
                self.assertEqual(self.refusal(operations=operations), "A native program requires 1-32 bounded operations")


if __name__ == "__main__":
    unittest.main()
