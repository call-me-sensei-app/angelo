# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Each add-on module defines a top-level function or class once.

Re-review fix of markup 6c5a3ea96b1 (GUI, Mac 1): markup_overlay gained a second ``covers`` (window, x, y) below
the existing ``covers(area_pointer)``; Python keeps the later one, so chat_launcher's one-argument call raised on every
draw of the library and shelf chrome. No host test saw it. engine_link_server held two identical ``project_key``.
No Blender."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import unittest
from pathlib import Path

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"


class OneDefinitionTests(unittest.TestCase):
    def test_no_module_defines_a_top_level_name_twice(self):
        twice = []
        for path in sorted(ADDON.rglob("*.py")):
            seen = set()
            for node in ast.parse(path.read_text(encoding="utf-8")).body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    if node.name in seen:
                        twice.append("%s:%d %s" % (path.relative_to(ADDON), node.lineno, node.name))
                    seen.add(node.name)
        self.assertEqual(twice, [], "a second definition silently replaces the first")


if __name__ == "__main__":
    unittest.main()
