# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What an add-on module's own relative imports bind, for tests that run some of its code in a namespace they build.

A test that executes chosen functions of ``chat_view.py`` (or any add-on module) by hand gives them a namespace of
stand-ins. Listing the module's imports in that namespace by hand fails silently later: when the module adds an
import, every such test raises NameError for it (1.1, composer IME: ``composer_ime`` in chat_view's draw and
modal, unknown to four tests). ``relative_imports(path)`` reads the module's top-level ``from . import x`` and
``from .m import y`` statements and resolves each name to the real object, so a test starts from what the module
really binds and overrides only what it stands in for: ``{**relative_imports(path), **stand_ins}``.

A name whose module cannot be imported outside Blender (it needs ``gpu`` or ``blf`` at import) is left out. The
test then fails on that name, naming it, until it gives a stand-in.
"""

import ast
import importlib
import sys
from pathlib import Path


def relative_imports(path, package="angelo_agent"):
    """``{name: object}`` for the top-level relative imports of the module at ``path`` that import here."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    addons = str(Path(path).resolve().parents[1])   # scripts/addons_core, where the package imports from.
    if addons not in sys.path:
        sys.path.insert(0, addons)
    bound = {}
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or node.level != 1:
            continue
        for alias in node.names:
            try:
                if node.module is None:
                    value = importlib.import_module("%s.%s" % (package, alias.name))
                else:
                    value = getattr(importlib.import_module("%s.%s" % (package, node.module)), alias.name)
            except Exception:   # noqa: BLE001  Needs Blender's own modules to import; the test stands in for it.
                continue
            bound[alias.asname or alias.name] = value
    return bound
