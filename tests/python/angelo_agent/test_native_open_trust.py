# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every file Angelo opens runs its scripts only as Blender would open that file (``file_trust``).

Blender's "Auto-run scripts trusted for unintended files" fix gives Open Recent and macOS opens the file's own default
trust; ``wm.open_mainfile`` run with a path and no ``use_scripts`` still keeps the session's. The 1.0.10 break-it
review found two of Angelo's own opens doing that (the start card's recent scenes, an engine's linked .blend): after
the person trusted one file, a recent scene opened from the start card ran its scripts. Covered: every call site in the
add-on passes ``use_scripts`` (or opens Blender's own file browser); the default follows Blender's rule; an agent
script's own open takes it while the script runs, and ``bpy.ops`` is put back.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))
from angelo_agent import file_trust  # noqa: E402


class CallSiteTests(unittest.TestCase):
    def test_every_open_in_the_add_on_names_its_trust(self):
        missing = []
        for path in sorted(ADDON.rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "open_mainfile"):
                    continue
                keywords = {keyword.arg for keyword in node.keywords}
                browser = (not keywords and len(node.args) == 1 and isinstance(node.args[0], ast.Constant)
                           and node.args[0].value == "INVOKE_DEFAULT")   # Blender's file browser preselects it.
                if not browser and "use_scripts" not in keywords:
                    missing.append("%s:%d" % (path.relative_to(ADDON), node.lineno))
        self.assertEqual(missing, [])


def stub(override, preference, outside_excluded):
    return SimpleNamespace(app=SimpleNamespace(autoexec_override=override),
                           context=SimpleNamespace(preferences=SimpleNamespace(
                               filepaths=SimpleNamespace(use_scripts_auto_execute=preference))),
                           path=SimpleNamespace(is_autoexec=lambda path, **_k: outside_excluded))


class DefaultTests(unittest.TestCase):
    def test_the_command_line_then_the_preference_then_the_excluded_paths(self):
        cases = [((True, False, False), True), ((False, True, True), False), ((None, False, True), False),
                 ((None, True, True), True), ((None, True, False), False)]
        for args, expected in cases:
            with self.subTest(args=args), mock.patch.dict(sys.modules, {"bpy": stub(*args)}):
                self.assertIs(file_trust.default("/projects/a.blend"), expected)


class UnreadableTests(unittest.TestCase):
    def test_trust_that_cannot_be_read_is_none(self):
        with mock.patch.dict(sys.modules, {"bpy": SimpleNamespace(app=SimpleNamespace(), context=SimpleNamespace())}):
            self.assertIs(file_trust.default("/projects/a.blend"), False)


class ScriptTests(unittest.TestCase):
    def test_a_scripts_own_open_takes_the_files_default_and_ops_is_put_back(self):
        calls = []
        real = lambda module, function: (lambda *a, **k: calls.append((module, function, a, k)) or {"FINISHED"})
        ops = SimpleNamespace(_op_create_function=real)
        bpy = SimpleNamespace(ops=ops)
        with mock.patch.object(file_trust, "default", lambda path: path.endswith("trusted.blend")):
            with file_trust.Scripts(bpy):
                ops._op_create_function("wm", "open_mainfile")(filepath="/p/other.blend")
                ops._op_create_function("wm", "open_mainfile")(filepath="/p/trusted.blend")
                ops._op_create_function("wm", "open_mainfile")(filepath="/p/other.blend", use_scripts=True)
                ops._op_create_function("wm", "open_mainfile")("INVOKE_DEFAULT")
                ops._op_create_function("object", "select_all")(action="SELECT")
        self.assertIs(ops._op_create_function, real)
        self.assertEqual([call[3].get("use_scripts") for call in calls], [False, True, True, None, None])


if __name__ == "__main__":
    unittest.main()
