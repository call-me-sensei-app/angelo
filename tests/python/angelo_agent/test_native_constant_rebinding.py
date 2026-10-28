# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""No add-on module replaces one of its own constants further down (a refinement that builds on it is fine).

The session rail named its failed tiles ``FAILED = "failed"`` and, two hundred lines later, the lead's reply template
``FAILED = "(Its turn failed: %s)"``: every failed tile's kind and the status the lead read became the template, while
the kinds list kept "failed" (the live trial on e2e 45e28c579dc: a worker's state read "(Its turn failed: %s)"). A
module-level UPPER_CASE name bound again by a value that does not read it is that slip, in any module. No Blender.
"""

import ast
import sys
import unittest
from pathlib import Path

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))


def replaced_constants(source):
    """``[(name, line)]`` of module-level UPPER_CASE names bound again by a value that does not read them."""
    bound, found = set(), []
    for node in ast.parse(source).body:   # Module level, as written.
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        names = [n.id for target in targets for n in ast.walk(target)
                 if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store) and n.id.isupper()]
        reads = {n.id for n in ast.walk(value) if isinstance(n, ast.Name)}
        found += [(name, node.lineno) for name in names if name in bound and name not in reads]
        bound.update(names)
    return found


class ConstantRebindingTests(unittest.TestCase):
    def test_no_module_replaces_its_own_constant(self):
        found = {path.name: replaced for path in sorted(ADDON.glob("*.py"))
                 if (replaced := replaced_constants(path.read_text(encoding="utf-8")))}
        self.assertEqual(found, {})

    def test_the_check_tells_a_replacement_from_a_refinement(self):
        self.assertEqual(replaced_constants('FAILED = "failed"\nX = 1\nFAILED = "(Its turn failed: %s)"\n'),
                         [("FAILED", 3)])
        self.assertEqual(replaced_constants("COUNTS = {1}\nCOUNTS = COUNTS | {2}\nTABLE = {}\nTABLE['a'] = 1\n"), [])
        self.assertEqual(replaced_constants("A, B = 1, 2\nB = 3\n"), [("B", 2)])

    def test_a_failed_tile_is_a_kind_of_the_rail(self):
        from angelo_agent import session_rail as rail
        self.assertEqual(rail.FAILED, "failed")
        self.assertIn(rail.FAILED, rail.KINDS)
        self.assertIn("%s", rail.TURN_FAILED)


if __name__ == "__main__":
    unittest.main()
