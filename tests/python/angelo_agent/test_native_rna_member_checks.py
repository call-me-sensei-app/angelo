# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Nothing asks a ``bpy.types`` class whether it has a built-in RNA member (1.0.5).

Evidence: on the 1.0.5 build (35f5b544698, 2026-10-23), ``hasattr(bpy.types.AssetShelf, "enable_catalog")`` and
``hasattr(bpy.types.AssetShelf, "angelo_shown_count")`` were False although both exist: properties and functions
defined in C are read through instances, never class attributes. Each such check silently took its "older binary"
branch (the shelf collapse chevron would never show; GUI suites skipped their checks). What Python adds to a class
is a class attribute, and ``hasattr`` sees it (probed on the same build: a ``bpy.props`` property assigned to a
type, and a plain Python ``property``, both True). So ``hasattr(bpy.types.X, "m")`` is flagged in the add-on and the
tests unless Python itself puts ``m`` on ``X`` somewhere there (``bpy.types.X.m = ...`` or
``setattr(bpy.types.X, "m", ...)``). For a built-in member, ask ``bpy.types.X.bl_rna.properties`` /
``.functions`` or an instance. A host stub has no RNA, so the source is read.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TREES = (ROOT / "scripts/addons_core/angelo_agent", ROOT / "tests/python/angelo_agent")
CLASS_MEMBER = re.compile(r"\bhasattr\(\s*bpy\.types\.(\w+)\s*,\s*['\"](\w+)['\"]")
ASSIGNED = re.compile(r"\bbpy\.types\.(\w+)\.(\w+)\s*=(?!=)")
SET = re.compile(r"\bsetattr\(\s*bpy\.types\.(\w+)\s*,\s*['\"](\w+)['\"]")


def sources():
    for tree in TREES:
        for path in sorted(tree.rglob("*.py")):
            yield path, path.read_text(encoding="utf-8")


def python_added(texts):
    """``(type, member)`` pairs Python adds to ``bpy.types`` classes anywhere in ``texts``."""
    return {match for text in texts for pattern in (ASSIGNED, SET) for match in pattern.findall(text)}


def class_member_checks(path, text, added):
    for number, line in enumerate(text.splitlines(), 1):
        for kind, member in CLASS_MEMBER.findall(line):
            if (kind, member) not in added and not member.startswith("bl_"):   # bl_rna & co. are class attributes.
                yield "%s:%d: %s" % (path.relative_to(ROOT), number, line.strip())


class RnaMemberCheckTests(unittest.TestCase):
    def test_no_class_level_checks_of_built_in_rna_members(self):
        files = list(sources())
        added = python_added(text for _path, text in files)
        found = [hit for path, text in files if path.name != Path(__file__).name
                 for hit in class_member_checks(path, text, added)]
        self.assertEqual(found, [], "A built-in RNA member is no class attribute: read bpy.types.X.bl_rna instead")

    def test_the_rule_sees_the_mistake_and_spares_what_python_adds(self):
        mistake = 'if hasattr(bpy.types.AssetShelf, "angelo_set_collapsed"):'
        self.assertEqual(len(list(class_member_checks(ROOT / "x.py", mistake, set()))), 1)
        added = python_added(['bpy.types.WindowManager.angelo_section = EnumProperty(items=[])',
                              'setattr(bpy.types.ShaderNodeMath, "node_tree", property(lambda node: None))'])
        self.assertEqual(added, {("WindowManager", "angelo_section"), ("ShaderNodeMath", "node_tree")})
        for line in ('not hasattr(bpy.types.WindowManager, "angelo_section")',
                     'if hasattr(bpy.types.ShaderNodeMath, "node_tree"):'):
            self.assertEqual(list(class_member_checks(ROOT / "x.py", line, added)), [], line)
        self.assertEqual(list(class_member_checks(ROOT / "x.py", 'hasattr(bpy.types, "AssetShelf")', set())), [],
                         "A type's existence is fine")
        self.assertEqual(list(class_member_checks(ROOT / "x.py", 'hasattr(bpy.types.Mesh, "bl_rna")', set())), [],
                         "bl_rna is a class attribute")


if __name__ == "__main__":
    unittest.main()
