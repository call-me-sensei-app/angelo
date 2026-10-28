# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Background processes that apply native commits keep Undo history as a window does, from before their first load.

Background Blender builds no Undo history when it loads a file, and a native commit refuses without an active step
("Enable Global Undo with at least one Undo step before applying a native commit"), so every staged program and
recipe APPLY refused in the headless MCP child (found while building parallel-agent workers, which share its
start-up). The binary now keeps history in background once asked (``_angelo_undo.keep_background_document``): every
load starts the loaded document's own history, first step "Original", as a window's load does. These checks pin the
Python side, source-level where bpy is needed: the shared start-up asks before any load, an older binary is never
assumed to keep history, and no background start-up pushes its own step (``ed.undo_push`` marks the just-opened file
modified, which ``angelo_blend_file`` would then report). The real child runs in
test_blender_headless_native_commits.py.
"""

import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))
from angelo_agent import program_undo  # noqa: E402

# The modules that start or run a background document: the headless MCP child and parallel-agent workers.
BACKGROUND_PREFIXES = ("headless_", "worker_")
LOADS = {"open_mainfile", "open_copy", "read_homefile", "read_factory_settings", "revert_mainfile"}


def tree(name):
    return ast.parse((ADDON / name).read_text(encoding="utf-8"))


def function(module, name):
    return next(node for node in ast.walk(module) if isinstance(node, ast.FunctionDef) and node.name == name)


def called(node):
    """(line, name) for every call below ``node``, named by its last attribute or its function name."""
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            target = child.func
            name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", None)
            if name:
                yield child.lineno, name


class KeepBackgroundHistoryTests(unittest.TestCase):
    def test_the_binary_is_asked_and_its_answer_returned(self):
        asked = []
        bridge = SimpleNamespace(keep_background_document=lambda: asked.append(True) or True)
        with patch.dict(sys.modules, {"_bpy": SimpleNamespace(_angelo_undo=bridge)}):
            self.assertIs(program_undo.keep_background_document(), True)
        self.assertEqual(asked, [True])
        bridge = SimpleNamespace(keep_background_document=lambda: False)
        with patch.dict(sys.modules, {"_bpy": SimpleNamespace(_angelo_undo=bridge)}):
            self.assertIs(program_undo.keep_background_document(), False)

    def test_a_save_through_a_copy_is_marked_by_the_binary(self):
        marked = []
        bridge = SimpleNamespace(background_document_saved=lambda: marked.append(True) or True)
        with patch.dict(sys.modules, {"_bpy": SimpleNamespace(_angelo_undo=bridge)}):
            self.assertIs(program_undo.background_document_saved(), True)
        self.assertEqual(marked, [True])
        for module in (None, SimpleNamespace(), SimpleNamespace(_angelo_undo=SimpleNamespace())):
            with patch.dict(sys.modules, {"_bpy": module}):
                self.assertIs(program_undo.background_document_saved(), False)

    def test_an_older_binary_is_never_assumed_to_keep_history(self):
        for module in (None, SimpleNamespace(), SimpleNamespace(_angelo_undo=SimpleNamespace()),
                       SimpleNamespace(_angelo_undo=SimpleNamespace(keep_background_document=None))):
            with patch.dict(sys.modules, {"_bpy": module}):
                self.assertIs(program_undo.keep_background_document(), False)


class SharedStartUpTests(unittest.TestCase):
    def test_start_up_keeps_history_before_any_load(self):
        main = tree("headless_main.py")
        prepare = function(main, "_prepare_preferences")
        self.assertIn("keep_background_document", {name for _, name in called(prepare)})
        # Global Undo and at least one step are forced: the person's 0 would refuse every native commit.
        assigned = {}
        for node in ast.walk(prepare):
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Tuple) and isinstance(node.value,
                                                                                                    ast.Tuple):
                pairs = zip(node.targets[0].elts, node.value.elts)
            elif isinstance(node, ast.Assign):
                pairs = ((node.targets[0], node.value),)
            else:
                continue
            assigned.update({target.attr: ast.unparse(value) for target, value in pairs
                             if isinstance(target, ast.Attribute)})
        self.assertEqual((assigned.get("use_global_undo"), assigned.get("undo_steps")), ("True", "UNDO_STEPS"))
        from angelo_agent import headless_main
        self.assertGreaterEqual(headless_main.UNDO_STEPS, 1)
        serve = function(main, "_serve")
        calls = sorted(called(serve))
        first_prepare = min(line for line, name in calls if name == "_prepare_preferences")
        loads = [line for line, name in calls if name in LOADS]
        self.assertTrue(loads, "the start-up loads its document")
        self.assertLess(first_prepare, min(loads), "history is kept before the first load, so every load starts one")

    def test_the_headless_save_marks_the_document_saved_after_placing_it(self):
        """Blender's save there is a copy (never the open file), which leaves the document reading as unsaved."""
        save = next(node for node in ast.walk(tree("headless_host.py")) if isinstance(node, ast.FunctionDef)
                    and node.name == "save")
        calls = sorted(called(save))
        placed = [line for line, name in calls if name == "place"]
        marked = [line for line, name in calls if name == "background_document_saved"]
        self.assertTrue(placed and marked, calls)
        self.assertGreater(min(marked), max(placed), "marked saved only once the output is in place")

    def test_no_background_module_pushes_its_own_first_step(self):
        """The binary starts each loaded document's history; an extra push would add a step and mark it modified."""
        found = []
        for path in sorted(ADDON.glob("*.py")):
            if path.name.startswith(BACKGROUND_PREFIXES):
                found += ["%s:%d" % (path.name, line) for line, name in called(tree(path.name)) if name == "undo_push"]
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
