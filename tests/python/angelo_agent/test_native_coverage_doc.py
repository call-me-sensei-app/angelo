# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The native coverage checklist stays consistent with the registries it describes.

Dependency-free: parses doc/angelo/native_coverage.rst and compares it with the
action, modifier and check registries and the measured always-sent prefix. It
does not prove that a listed capability works; the named suites do that.
"""

import json
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent.action_schemas import MODIFIER_SETTINGS, OPERATION_SCHEMAS  # noqa: E402
from angelo_agent.plan_schema import DEFERRED_ACTIONS, PLAN_CORE_ACTIONS  # noqa: E402
from angelo_agent.tool_specs import dynamic_tools, native_plan_schema  # noqa: E402
from angelo_agent.validation_spec import CHECK_KINDS, MODIFIER_PROPERTIES  # noqa: E402

DOC = ROOT / "doc/angelo/native_coverage.rst"
STATUSES = ("Native typed", "Native partial", "Reviewed Python", "Not covered")


def encoded(value):
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode())


def tables(text):
    """Each list-table as a list of rows; a row is a list of cell strings."""
    result = []
    for block in re.split(r"^\.\. list-table::.*$", text, flags=re.M)[1:]:
        rows = []
        for line in block.splitlines():
            if re.match(r"   \* -( |$)", line):
                rows.append([line[len("   * -"):].strip()])
            elif re.match(r"     -( |$)", line) and rows:
                rows[-1].append(line[len("     -"):].strip())
            elif line.startswith("       ") and rows:
                rows[-1][-1] = (rows[-1][-1] + " " + line.strip()).strip()
            elif line and not line.startswith(" ") and rows:
                break
        result.append(rows)
    return result


class CoverageDocTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DOC.read_text(encoding="utf-8")
        cls.tables = tables(cls.text)

    def capability_rows(self):
        return [row for table in self.tables if table and table[0][0] == "Capability" for row in table[1:]]

    def table_titled(self, first_header):
        return next(table for table in self.tables if table and table[0][0] == first_header)

    def test_rows_have_five_cells_and_a_closed_status(self):
        rows = self.capability_rows()
        self.assertGreater(len(rows), 50)
        for row in rows:
            with self.subTest(row=row[0]):
                self.assertEqual(len(row), 5, row)
                self.assertIn(row[1], STATUSES)

    def test_summary_counts_match_the_rows(self):
        counts = {status: 0 for status in STATUSES}
        for row in self.capability_rows():
            counts[row[1]] += 1
        summary = {row[0]: row for row in self.table_titled("Status")[1:]}
        for status in STATUSES:
            with self.subTest(status=status):
                self.assertEqual(int(summary[status][2]), counts[status])

    def test_every_action_kind_modifier_and_check_is_listed(self):
        for kind in OPERATION_SCHEMAS:
            self.assertIn("``" + kind + "``", self.text, kind)
        for modifier in set(MODIFIER_SETTINGS) | set(MODIFIER_PROPERTIES):
            self.assertIn("``" + modifier + "``", self.text, modifier)
        for check in CHECK_KINDS:
            self.assertIn("``" + check + "``", self.text, check)

    def test_prefix_figures_are_the_current_measurements(self):
        rows = {row[0]: row for row in self.table_titled("Part")[1:]}

        def current(name):
            return int(rows[name][-1].split()[0].replace(",", ""))

        self.assertEqual(current("Claude plan schema (core plus deferred names)"), encoded(native_plan_schema()))
        self.assertEqual(current("Codex dynamic tools"), encoded(dynamic_tools()))
        from angelo_agent.providers import INSTRUCTIONS
        self.assertEqual(current("Codex instructions"), len(INSTRUCTIONS.encode()))
        self.assertEqual(rows["Action kinds: core, deferred"][-1],
                         "%d, %d" % (len(PLAN_CORE_ACTIONS), len(DEFERRED_ACTIONS)))


if __name__ == "__main__":
    unittest.main()
