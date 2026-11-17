# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every glTF file Angelo writes holds the scene its window shows, never the file's other scenes.

Blender's glTF exporter writes every scene of the open file unless ``use_active_scene`` is on (its default is off:
``io_scene_gltf2`` ``gather.py``), and it makes each scene the window's own while it reads it (``tree.py``). With
``use_selection`` it then asks each object whether it is selected in that scene's view layer, so an object selected
in another scene is written too, and every scene's name is written whatever is selected.

Checked in the source, since each route needs a window or the exporter itself: every call of the operator names
``use_active_scene=True`` where it is called, or forwards settings that a caller of ``web_export.run_exporter``
builds with it. ``test_blender_team_library`` proves the file a save writes, in the app.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
from pathlib import Path
import unittest

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
FORWARDER = "run_exporter"   # web_export's one function that passes a caller's settings on unchanged.


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _names_the_scene(call):
    return any(keyword.arg == "use_active_scene" and isinstance(keyword.value, ast.Constant)
               and keyword.value.value is True for keyword in call.keywords)


def _functions(tree):
    """Each function with the calls inside it (a nested function's calls count for both)."""
    for function in ast.walk(tree):
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield function, [node for node in ast.walk(function) if isinstance(node, ast.Call)]


class GltfExportScope(unittest.TestCase):
    def setUp(self):
        self.modules = {path.name: ast.parse(path.read_text(encoding="utf-8")) for path in sorted(ADDON.glob("*.py"))}

    def _exports(self):
        """``(module, function name, call)`` of every call of the glTF export operator."""
        for name, tree in self.modules.items():
            for function, calls in _functions(tree):
                for call in calls:
                    if _dotted(call.func).endswith("export_scene.gltf"):
                        yield name, function.name, call

    def test_the_operator_is_called_somewhere(self):
        self.assertGreaterEqual(len({(name, call.lineno) for name, _function, call in self._exports()}), 3)

    def test_every_call_names_the_active_scene_or_forwards_settings_that_do(self):
        missing = sorted({"%s:%d" % (name, call.lineno) for name, function, call in self._exports()
                          if not _names_the_scene(call)
                          and not (function == FORWARDER and any(k.arg is None for k in call.keywords))})
        self.assertEqual(missing, [], "glTF exports that write every scene of the file")

    def test_every_caller_of_the_forwarder_builds_settings_that_name_the_active_scene(self):
        callers = 0
        for name, tree in self.modules.items():
            for function, calls in _functions(tree):
                if function.name == FORWARDER or not any(_dotted(call.func).split(".")[-1] == FORWARDER
                                                         for call in calls):
                    continue
                callers += 1
                built = [call for _function, inner in _functions(tree) for call in inner
                         if _dotted(call.func) == "dict" and _names_the_scene(call)]
                self.assertTrue(built, "%s: %s forwards settings no dict() of its module builds with "
                                       "use_active_scene=True" % (name, function.name))
        self.assertGreaterEqual(callers, 2)


if __name__ == "__main__":
    unittest.main()
