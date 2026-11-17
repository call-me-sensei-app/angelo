# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Scripts the add-on runs outside its package import without one.

A file the add-on names by ``Path(__file__).with_name("<name>.py")`` runs as a plain script (``--python`` in a
background Angelo, a worker or hook interpreter, ``spec_from_file_location``): it has no parent package, so a
``from . import x`` at its top level raises ImportError before it does anything. Its plain sibling imports
(``sys.path`` beside it) run the same way. Found 2026-11-16 (p10c-3-library, guard piece 10): the write check added
``from . import owned_paths`` to fab_convert_child, so every Fab conversion child failed at import. A relative import
there must sit in ``try: ... except ImportError:`` with a plain fallback, as shared_replace does.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
NAMED = re.compile(r"""with_name\(\s*['"]([A-Za-z0-9_]+)\.py['"]\s*\)""")


def _guarded(tree):
    """Nodes inside a ``try`` whose handlers catch ImportError (or everything)."""
    inside = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        names = set()
        for handler in node.handlers:
            kinds = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
            names.update(getattr(kind, "id", None) for kind in kinds)
        if names & {None, "ImportError", "ModuleNotFoundError", "Exception", "BaseException"}:
            for statement in node.body:
                inside.update(id(child) for child in ast.walk(statement))
    return inside


def _top_level(tree):
    """Statements that run when the file runs: module level, and the bodies of top-level if/try/with."""
    pending, found = list(tree.body), []
    while pending:
        node = pending.pop()
        found.append(node)
        if isinstance(node, (ast.If, ast.Try, ast.With)):
            for field in ("body", "orelse", "finalbody", "handlers"):
                for child in getattr(node, field, []) or []:
                    pending.extend(child.body if isinstance(child, ast.ExceptHandler) else [child])
    return found


def scripts():
    """{name: why} of every file run without a package: those named by with_name, then their plain siblings."""
    found = {}
    for path in sorted(ADDON.glob("*.py")):
        for name in NAMED.findall(path.read_text(encoding="utf-8")):
            if (ADDON / (name + ".py")).is_file():
                found.setdefault(name, "named by %s" % path.stem)
    pending = list(found)
    while pending:
        name = pending.pop()
        tree = ast.parse((ADDON / (name + ".py")).read_text(encoding="utf-8"))
        for node in _top_level(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    sibling = alias.name.split(".")[0]
                    if sibling not in found and (ADDON / (sibling + ".py")).is_file():
                        found[sibling] = "imported plainly by %s" % name
                        pending.append(sibling)
    return found


def unguarded_relative_imports(name):
    tree = ast.parse((ADDON / (name + ".py")).read_text(encoding="utf-8"))
    guarded = _guarded(tree)
    return ["%s.py:%d" % (name, node.lineno) for node in _top_level(tree)
            if isinstance(node, ast.ImportFrom) and node.level and id(node) not in guarded]


class ChildScriptImportTests(unittest.TestCase):
    def test_the_scripts_are_found(self):
        found = scripts()
        for name in ("fab_convert_child", "studio_assets_convert", "render_child", "fab_convert_clean",
                     "shared_replace", "owned_paths"):
            self.assertIn(name, found)

    def test_no_script_run_without_a_package_imports_relative_to_one(self):
        bad = sorted((line, why) for name, why in scripts().items() for line in unguarded_relative_imports(name))
        self.assertEqual(bad, [], "these run as plain scripts (no parent package): import the sibling plainly, or "
                                  "wrap the relative import in try/except ImportError with a plain fallback")

    def test_a_planted_relative_import_is_found(self):
        tree = ast.parse("import os\nfrom . import owned_paths\ntry:\n    from . import x\nexcept ImportError:\n"
                         "    import x\n")
        guarded = _guarded(tree)
        found = [node.lineno for node in _top_level(tree)
                 if isinstance(node, ast.ImportFrom) and node.level and id(node) not in guarded]
        self.assertEqual(found, [2])


if __name__ == "__main__":
    unittest.main()
