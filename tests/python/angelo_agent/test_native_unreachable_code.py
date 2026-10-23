# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""No statement after a return, raise, continue or break in the same block, in any GUI suite or add-on module.

test_blender_studio_tabs lost its "strip" phase this way (1.0.5): an inserted phase left the strip's captures after
another phase's return, unreachable, so the suite looped until its 300 s cap and the strip was never checked.
"""

import ast
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ADDON = HERE.parents[2] / "scripts/addons_core/angelo_agent"


def unreachable(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if isinstance(block, list):
                found += ["%s:%d" % (path.name, block[i + 1].lineno) for i, statement in enumerate(block[:-1])
                          if isinstance(statement, (ast.Return, ast.Raise, ast.Continue, ast.Break))]
    return found


class UnreachableCodeTests(unittest.TestCase):
    def test_no_dead_statements_in_gui_suites_or_the_add_on(self):
        paths = sorted(HERE.glob("test_blender_*.py")) + sorted(ADDON.rglob("*.py"))
        self.assertEqual([], [hit for path in paths for hit in unreachable(path)])


if __name__ == "__main__":
    unittest.main()
