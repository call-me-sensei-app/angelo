# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every class whose ``bl_idname`` the add-on reads declares it. No Blender.

Blender lets a Menu, Panel or UIList leave ``bl_idname`` out and registers it under the class name, but the Python
class then has no ``bl_idname`` attribute: ``layout.menu(MY_MT_x.bl_idname)`` raises AttributeError inside a draw
callback, Blender prints the traceback and the menu is drawn without that entry. Nothing fails loudly: the entry is
simply missing. Found by the 2.1.0 Loops menu's GUI run (2026-11-15): the right-click menu drew without Loops, and the
keyboard steps landed on stock items instead.

Read from the add-on's sources: every ``Name.bl_idname`` or ``module.Name.bl_idname`` whose ``Name`` is a class the
add-on defines needs ``bl_idname`` in that class's body (or a base class the add-on defines).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import unittest
from pathlib import Path

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"


def assigned(body):
    names = set()
    for node in body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) \
            else []
        names.update(target.id for target in targets if isinstance(target, ast.Name))
    return names


def classes(trees):
    """{class name: (assigned names, base names)} of every class the add-on defines."""
    found = {}
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases = [getattr(base, "id", getattr(base, "attr", None)) for base in node.bases]
                found.setdefault(node.name, []).append((assigned(node.body), bases))
    return found


def declares(name, found, seen=()):
    """Whether every add-on class named ``name`` declares ``bl_idname`` itself or through an add-on base."""
    if name in seen:
        return False
    return all("bl_idname" in names or any(base in found and declares(base, found, seen + (name,)) for base in bases)
               for names, bases in found[name])


def reads(trees):
    """[(file, line, class name)] of each ``X.bl_idname`` / ``module.X.bl_idname`` read."""
    found = []
    for stem, tree in trees.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "bl_idname" and isinstance(node.ctx, ast.Load):
                value = node.value
                name = value.id if isinstance(value, ast.Name) else value.attr if isinstance(value, ast.Attribute) \
                    else None
                if name:
                    found.append((stem, node.lineno, name))
    return found


class Declared(unittest.TestCase):
    def test_every_bl_idname_read_is_declared(self):
        trees = {path.relative_to(ADDON).as_posix(): ast.parse(path.read_text(encoding="utf-8"))
                 for path in sorted(ADDON.rglob("*.py"))}
        found = classes(trees)
        read = [entry for entry in reads(trees) if entry[2] in found]
        self.assertTrue(read, "no bl_idname read of an add-on class: the scan reads the wrong thing")
        missing = sorted({"%s:%d %s" % entry for entry in read if not declares(entry[2], found)})
        self.assertFalse(missing, "these classes have no bl_idname attribute, so reading it raises AttributeError "
                                  "(Blender registers them under the class name; declare bl_idname): %s" % missing)


if __name__ == "__main__":
    unittest.main()
