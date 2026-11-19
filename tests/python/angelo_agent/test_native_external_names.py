# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every name the MCP visibility modules take from another Angelo module exists there, read from the source.

The host tests substitute the window's side (``VisibilityUi``) and so never call into ``bpy``-bound modules such as
``scene_queries``; a name that exists only in another branch (``scene_queries.handle`` for this tree's
``object_handle``) passed them all and failed only in Blender (found by test_blender_external_window_gui on the
2.0.1 Linux build). This reads each module's source: every ``from .module import name`` and every ``module.name`` of a
sibling module it imports must be a top-level name of that module (an alias module that puts another in its place,
``sys.modules[__name__] = _module``, is followed to that one). An import guarded by ``except ImportError`` is
optional by design and skipped. Contract: doc/angelo/external_mcp.rst.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
MODULES = ROOT / "scripts/modules"
CHECKED = [ADDON / name for name in (
    "external_visibility.py", "external_window.py", "external_look_args.py", "external_state.py", "external_usage.py",
    "external_result.py", "external_mcp_visibility.py", "external_look.py", "view_warm.py", "view_warm_facts.py")] + [
    Path(__file__).resolve().parent / name for name in (
        "test_blender_external_window_gui.py", "test_blender_external_visibility.py", "test_blender_external_state.py",
        "test_native_external_look_warm.py")]
ALIAS = re.compile(r"import (angelo_\w+) as _module[\s\S]*sys\.modules\[__name__\] = _module")


def source_of(module):
    """The file whose top level a sibling ``module`` is (an alias module followed), or None."""
    path = ADDON / (module + ".py")
    if not path.exists():
        return None
    found = ALIAS.search(path.read_text(encoding="utf-8"))
    return MODULES / (found.group(1) + ".py") if found else path


def top_level(path):
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.col_offset == 0:
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and node.col_offset == 0:
            for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                names |= {name.id for name in ast.walk(target) if isinstance(name, ast.Name)}
        elif isinstance(node, (ast.Import, ast.ImportFrom)) and node.col_offset == 0:
            names |= {alias.asname or alias.name.split(".")[0] for alias in node.names}
    return names


def optional_imports(tree):
    """Import nodes inside a ``try`` whose handlers catch ImportError: optional by design."""
    guarded = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try) and any("ImportError" in ast.unparse(handler.type or ast.Name("BaseException"))
                                             for handler in node.handlers):
            guarded |= {id(sub) for stmt in node.body for sub in ast.walk(stmt)
                        if isinstance(sub, (ast.Import, ast.ImportFrom))}
    return guarded


def missing(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    guarded, siblings, found = optional_imports(tree), {}, []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or id(node) in guarded:
            continue
        relative = node.level == 1
        package = node.module == "angelo_agent" or (relative and node.module is None)
        if package:   # from . import a, b / from angelo_agent import a, b: sibling modules.
            for alias in node.names:
                target = source_of(alias.name)
                if target is None:
                    found.append("no module %s (line %d)" % (alias.name, node.lineno))
                else:
                    siblings[alias.asname or alias.name] = target
        elif relative or (node.module or "").startswith("angelo_agent."):
            module = node.module.split(".")[-1]
            target = source_of(module)
            if target is None:
                found.append("no module %s (line %d)" % (module, node.lineno))
                continue
            names = top_level(target)
            found += ["%s.%s (line %d)" % (module, alias.name, node.lineno) for alias in node.names
                      if alias.name not in names]
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in siblings \
                and not node.attr.startswith("__"):
            if node.attr not in top_level(siblings[node.value.id]):
                found.append("%s.%s (line %d)" % (node.value.id, node.attr, node.lineno))
    return sorted(set(found))


class NameTests(unittest.TestCase):
    def test_every_name_the_visibility_modules_use_exists_in_this_tree(self):
        for path in CHECKED:
            with self.subTest(module=path.name):
                self.assertEqual(missing(path), [])

    def test_the_check_finds_a_name_another_branch_has(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            probe = Path(folder) / "probe.py"
            probe.write_text("from . import scene_queries\nscene_queries.handle(None)\n"
                             "from .live_windows import areas\nfrom .view_warm import no_such_name\n")
            self.assertEqual(missing(probe), ["scene_queries.handle (line 2)", "view_warm.no_such_name (line 4)"])


if __name__ == "__main__":
    unittest.main()
