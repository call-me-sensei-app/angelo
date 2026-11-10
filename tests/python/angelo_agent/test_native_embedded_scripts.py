# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Scripts the add-on runs as code strings (a child Blender's ``--python-expr``, ``exec``) bind every name they use.

Break-it r2m4c R10-M-5: the worker's offscreen-draw PROBE called ``private_roots.mkdtemp`` without importing it (an
edit of the module's imports never reaches a string), so the child raised NameError and every worker refused
pictures; a test that substituted the child's result hid it. Here every module-level script string of the add-on is
checked for names it uses but never binds, and the PROBE itself runs in a child interpreter with stand-in bpy/gpu.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import builtins
import json
import os
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
# Packages Angelo itself installs into the bundled interpreter's site-packages (certifi, the root store its TLS uses):
# a launched script reaches them from the bundled interpreter, whatever this test's interpreter has installed.
ANGELO_PACKAGES = frozenset(package["name"] for package in json.loads(
    (ROOT / "build_files/cmake/angelo_python_packages.json").read_text(encoding="utf-8"))["packages"])
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


def launched_command(script):
    """The real producer's argv, including isolated flags and its explicit reader argument."""
    from angelo_agent import pdf_text
    command = pdf_text.command("unused-import-check.pdf")
    return command if Path(command[3]).resolve() == script.resolve() else None


def archive_resolves(module, archives):
    """Find the full module through actual archive package paths without importing its code."""
    from importlib.machinery import PathFinder
    paths = archives
    for index in range(1, len(module.split(".")) + 1):
        name = ".".join(module.split(".")[:index])
        spec = PathFinder.find_spec(name, paths)
        if spec is None:
            return False
        paths = spec.submodule_search_locations
        if index < len(module.split(".")) and paths is None:
            return False
    return True


def resolves(module, script, command=None):
    """Resolve in actual launch context; -I -S cannot borrow this host's installed packages."""
    import importlib.util
    import zipfile
    top = module.split(".")[0]
    command = launched_command(script) if command is None else command
    if command is not None and "-I" in command and "-S" in command:
        archives = [str(Path(value)) for value in command[4:]
                    if Path(value).is_file() and zipfile.is_zipfile(value)]
        return top in sys.stdlib_module_names or archive_resolves(module, archives)
    if top in BLENDER_MODULES or top in ANGELO_PACKAGES or top in sys.stdlib_module_names:
        return True
    for folder in [script.parent] + SEARCH:
        parts = module.split(".")
        candidate = folder.joinpath(*parts)
        if candidate.with_suffix(".py").is_file() or (candidate / "__init__.py").is_file():
            return True
    return importlib.util.find_spec(top) is not None


def import_problems(script, command=None):
    tree = ast.parse(script.read_text(encoding="utf-8"))
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            problems += [alias.name for alias in node.names if not resolves(alias.name, script, command)]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if not resolves(node.module, script, command):
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

    def test_isolated_archive_resolution_checks_members_and_rejects_host_only_packages(self):
        import tempfile
        import zipfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(prefix="angelo-archive-check-") as folder:
            script = Path(folder) / "launched.py"
            archive = Path(folder) / "reader.pyz"
            script.write_text("import heldout_reader.errors\nimport heldout_reader.missing\nimport host_only\n")
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("heldout_reader/__init__.py", "")
                bundle.writestr("heldout_reader/errors.py", "")
            command = [sys.executable, "-I", "-S", str(script), str(archive)]
            with patch("importlib.util.find_spec", return_value=object()):
                self.assertEqual(import_problems(script, command),
                                 ["heldout_reader.missing", "host_only"])

    def test_pdf_worker_loads_actual_bundled_reader_under_its_isolated_flags(self):
        from unittest.mock import patch
        from angelo_agent import pdf_text
        # Blender's bundled Python is <prefix>/bin/python.exe on Windows; a host CPython keeps python.exe in the prefix
        # itself, where interpreter() finds none. This case checks the worker and reader, so any Python of this
        # version stands in for the bundled one there.
        with patch.object(pdf_text, "PYTHON", pdf_text.interpreter() or sys.executable):
            command = pdf_text.command("unused-import-check.pdf")
        self.assertEqual(command[1:3], ["-I", "-S"])
        self.assertEqual(Path(command[3]), pdf_text.WORKER)
        self.assertEqual(Path(command[4]), pdf_text.READER)
        # Execute the real worker loader, not a substitute import implementation. No PDF is parsed.
        code = ("import runpy, sys; worker=runpy.run_path(sys.argv[1], run_name='import_check'); "
                "reader=worker['_load'](sys.argv[2]); assert reader is not None; "
                "from pypdf.errors import DependencyError, FileNotDecryptedError, LimitReachedError; "
                "print('BUNDLED-READER-OK')")
        result = subprocess.run([command[0], *command[1:3], "-c", code, command[3], command[4]],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertEqual(result.stdout.strip(), "BUNDLED-READER-OK")

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
            if os.name == "nt":   # The same folders as Windows names them; Winsock (asyncio) needs SYSTEMROOT.
                system = os.environ["SYSTEMROOT"]
                environment = {"USERPROFILE": folder, "LOCALAPPDATA": folder, "APPDATA": folder, "TEMP": folder,
                               "TMP": folder, "SYSTEMROOT": system, "PATH": os.path.join(system, "System32")}
            else:
                environment = {"HOME": folder, "XDG_STATE_HOME": folder, "TMPDIR": folder, "PATH": "/usr/bin:/bin"}
            result = subprocess.run([sys.executable, "-c", CHILD, str(probe), str(ROOT / "scripts/addons_core")],
                                    capture_output=True, text=True, timeout=120, env=environment)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertIn("ANGELO-OFFSCREEN-OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
