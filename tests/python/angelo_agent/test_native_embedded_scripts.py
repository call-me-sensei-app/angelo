# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Scripts the add-on runs as code strings (a child Blender's ``--python-expr``, ``exec``) bind every name they use.

Break-it r2m4c R10-M-5: the worker's offscreen-draw PROBE called ``private_roots.mkdtemp`` without importing it (an
edit of the module's imports never reaches a string), so the child raised NameError and every worker refused
pictures; a test that substituted the child's result hid it. Here every module-level script string of the add-on is
checked for names it uses but never binds, and the PROBE itself runs in a child interpreter with stand-in bpy/gpu.
"""

import ast
import builtins
from pathlib import Path
import subprocess
import symtable
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"


def script_strings():
    """(module, name, code) of each module-level ``NAME = "..."`` string that is a Python script with an import."""
    for path in sorted(ADDON.glob("*.py")):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)):
                try:
                    tree = ast.parse(node.value.value)
                except SyntaxError:
                    continue
                if any(isinstance(item, (ast.Import, ast.ImportFrom)) for item in ast.walk(tree)):
                    yield path.name, node.targets[0].id, node.value.value


def unbound(code):
    table = symtable.symtable(code, "<script>", "exec")
    names = set()

    def visit(scope):
        for symbol in scope.get_symbols():
            if symbol.is_referenced() and symbol.is_global() and not symbol.is_assigned() \
                    and not symbol.is_imported() and not hasattr(builtins, symbol.get_name()):
                names.add(symbol.get_name())
        for child in scope.get_children():
            visit(child)
    visit(table)
    top = {symbol.get_name() for symbol in table.get_symbols() if symbol.is_assigned() or symbol.is_imported()}
    return sorted(names - top)


CHILD = r"""
import json, sys, types
from unittest.mock import MagicMock
probe, addons = sys.argv[1], sys.argv[2]
class Executor:
    def __init__(self, window):
        self.observation_store = None
    def execute(self, name, args):
        assert self.observation_store is not None
modules = {"gpu": MagicMock(), "bpy": MagicMock(),
           "angelo_agent.blender_tools": types.SimpleNamespace(BlenderExecutor=Executor),
           "angelo_agent.live_windows": types.SimpleNamespace(first=lambda window_manager: object()),
           "angelo_agent.references": types.SimpleNamespace(ReferenceStore=lambda folder, session: (folder, session))}
sys.modules.update(modules)
sys.argv = ["probe", addons]
exec(compile(open(probe, encoding="utf-8").read(), "<PROBE>", "exec"), {"__name__": "__main__"})
"""


# Modules Blender provides inside its own Python (a child Blender's ``--python`` script imports them).
BLENDER_MODULES = {"bpy", "_bpy", "gpu", "gpu_extras", "mathutils", "bmesh", "bpy_extras", "blf", "bl_math", "imbuf",
                   "aud", "idprop", "freestyle", "addon_utils", "bpy_types", "bl_ui", "bl_operators", "rna_info",
                   "_angelo_checkpoint", "numpy", "OpenImageIO"}   # numpy and OpenImageIO ship in Blender's Python.
DUNDERS = {"__file__", "__name__", "__doc__", "__spec__", "__builtins__", "__loader__", "__package__", "__path__"}
SEARCH = [ROOT / "scripts/addons_core", ROOT / "scripts/modules", ROOT / "scripts/startup", ROOT / "scripts/angelo"]


def launched_files():
    """Every Python file the add-on launches as a script (a child Blender's ``--python``, the bundled interpreter):
    those its code names (``"angelo" / "X.py"``, ``with_name("X.py")``), and every launcher in scripts/angelo."""
    import re
    found = set(Path(ROOT / "scripts/angelo").glob("angelo_*.py"))
    for path in ADDON.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        found.update(ROOT / "scripts/angelo" / name for name in re.findall(r'"angelo" / "(\w+\.py)"', text))
        found.update(ADDON / name for name in re.findall(r'with_name\("(\w+\.py)"\)', text))
    return sorted(found)


def resolves(module, script):
    """Whether ``module`` imports in the script's real context: the script's folder, Angelo's script folders, the
    standard library, or Blender's own modules."""
    import importlib.util
    top = module.split(".")[0]
    if top in BLENDER_MODULES or top in sys.stdlib_module_names:
        return True
    for folder in [script.parent] + SEARCH:
        parts = module.split(".")
        candidate = folder.joinpath(*parts)
        if candidate.with_suffix(".py").is_file() or (candidate / "__init__.py").is_file():
            return True
    return importlib.util.find_spec(top) is not None


def import_problems(script):
    tree = ast.parse(script.read_text(encoding="utf-8"))
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            problems += [alias.name for alias in node.names if not resolves(alias.name, script)]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if not resolves(node.module, script):
                problems.append(node.module)
            elif node.module.split(".")[0] == "angelo_agent":
                package = ROOT / "scripts/addons_core" / Path(*node.module.split("."))
                for alias in node.names:
                    if package.is_dir() and not ((package / (alias.name + ".py")).is_file()
                                                 or (package / alias.name / "__init__.py").is_file()
                                                 or alias.name in (package / "__init__.py").read_text(encoding="utf-8")):
                        problems.append(node.module + "." + alias.name)
    return problems


class LaunchedScriptTests(unittest.TestCase):
    """Every script file Angelo launches imports only what exists in its real context and binds every name it uses
    (break-it r2m4c R10-M-5's class: a missing import silently disabled a feature in a child)."""

    def test_the_check_finds_a_missing_import_and_a_missing_module(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix="angelo-script-check-") as folder:
            script = Path(folder) / "launched.py"
            script.write_text("import os\nfrom angelo_agent import files, no_such_module\n"
                              "folder = private_roots.mkdtemp('x')\n", encoding="utf-8")
            self.assertEqual(import_problems(script), ["angelo_agent.no_such_module"])
            self.assertEqual(unbound(script.read_text(encoding="utf-8")), ["private_roots"])

    def test_the_launched_scripts_are_found(self):
        names = {path.name for path in launched_files()}
        self.assertLessEqual({"angelo_headless.py", "angelo_mcp.py", "studio_assets_convert.py"}, names)

    def test_every_launched_script_resolves_its_imports_and_binds_its_names(self):
        for script in launched_files():
            with self.subTest(script=script.name):
                self.assertTrue(script.is_file(), script)
                self.assertEqual(import_problems(script), [])
                self.assertEqual([name for name in unbound(script.read_text(encoding="utf-8")) if name not in DUNDERS],
                                 [])


class EmbeddedScriptTests(unittest.TestCase):
    def test_every_script_string_binds_every_name_it_uses(self):
        found = list(script_strings())
        self.assertIn(("worker_host.py", "PROBE"), [(module, name) for module, name, _ in found])
        for module, name, code in found:
            with self.subTest(script=module + ":" + name):
                self.assertEqual(unbound(code), [])

    def test_the_worker_probe_runs_to_its_end_in_a_child_interpreter(self):
        import tempfile
        code = next(code for module, name, code in script_strings() if (module, name) == ("worker_host.py", "PROBE"))
        with tempfile.TemporaryDirectory(prefix="angelo-probe-test-") as folder:
            probe = Path(folder) / "probe.py"
            probe.write_text(code, encoding="utf-8")
            environment = {"HOME": folder, "XDG_STATE_HOME": folder, "TMPDIR": folder, "PATH": "/usr/bin:/bin"}
            result = subprocess.run([sys.executable, "-c", CHILD, str(probe), str(ROOT / "scripts/addons_core")],
                                    capture_output=True, text=True, timeout=120, env=environment)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertIn("ANGELO-OFFSCREEN-OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
