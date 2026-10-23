# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Modules the app ships load from the bytecode the app ships (doc/angelo/releasing.rst, "Nothing is written
inside the app").

``sitecustomize.py`` installs a path hook for the app's version folder: a source there loads from the checked
hash-based ``.pyc`` the install step wrote next to it (``build_files/utils/angelo_python_bytecode.py``) when its
hash matches, and from Python's own loader and bytecode folder otherwise. Without it, every launch with an empty
or unwritable bytecode folder (a new home or sandbox, ``PYTHONDONTWRITEBYTECODE``, each new build's first launch)
compiled every module again. Each case runs in its own isolated interpreter (``-I -S``) against a stand-in
version folder, so the test process's own import system is never changed.
"""

import ast
import importlib.util
import json
import marshal
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
SITE = ROOT / "scripts/site/sitecustomize.py"

# Runs in the child: the hook from sitecustomize.py, then the imports, counting what Python had to compile.
CHILD = textwrap.dedent("""
    import ast, builtins, importlib, json, os, sys
    site_path, version, cache, modules = sys.argv[1], sys.argv[2], sys.argv[3], json.loads(sys.argv[4])
    if cache == "-":
        sys.dont_write_bytecode = True
    else:
        sys.pycache_prefix = cache
    tree = ast.parse(open(site_path, encoding="utf-8").read())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "_angelo_shipped_bytecode")
    namespace = {"os": os, "sys": sys}
    exec(compile(ast.Module(body=[function], type_ignores=[]), site_path, "exec"), namespace)
    compiled = []
    real_compile = builtins.compile
    def counting_compile(source, filename, *args, **kwargs):
        compiled.append(os.path.basename(str(filename)))
        return real_compile(source, filename, *args, **kwargs)
    namespace["_angelo_shipped_bytecode"](version)
    sys.path[:0] = [os.path.join(version, "scripts"), os.path.join(os.path.dirname(version), "outside")]
    builtins.compile = counting_compile
    report = {}
    for name in modules:
        try:
            module = importlib.import_module(name)
            report[name] = {"loader": type(module.__loader__).__name__, "value": module.VALUE,
                            "shipped": "get_code" in vars(module.__loader__),
                            "co_filename": module.where.__code__.co_filename}
        except Exception as error:
            report[name] = {"error": "%s: %s" % (type(error).__name__, error)}
    builtins.compile = real_compile
    report["compiled"] = sorted(compiled)
    print(json.dumps(report))
""")


def module_source(value):
    return "VALUE = %r\n\ndef where():\n    return VALUE\n" % value


class ShippedBytecodeTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="angelo-shipped-bytecode-"))
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.version = self.folder / "5.2"
        self.scripts = self.version / "scripts"
        self.outside = self.folder / "outside"
        self.cache = self.folder / "user-cache"
        for folder in (self.scripts, self.outside, self.cache):
            folder.mkdir(parents=True)

    def ship(self, name, value, folder=None, mode=py_compile.PycInvalidationMode.CHECKED_HASH):
        """A module the way the install step leaves it: its source and, unless ``mode`` is None, its .pyc."""
        source = (folder or self.scripts) / ("%s.py" % name)
        source.write_text(module_source(value))
        if mode is not None:
            # The install step records paths relative to the app (angelo_python_bytecode.display_name).
            py_compile.compile(str(source), cfile=importlib.util.cache_from_source(str(source)),
                               dfile="scripts/%s.py" % name, doraise=True, invalidation_mode=mode)
        return source

    def shipped_pyc(self, source):
        return Path(importlib.util.cache_from_source(str(source)))

    def run_child(self, modules, cache=None, flags=()):
        cache = "-" if cache == "-" else str(cache or self.cache)
        result = subprocess.run(
            [sys.executable, "-I", "-S", *flags, "-c", CHILD, str(SITE), str(self.version), cache, json.dumps(modules)],
            capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def cache_files(self):
        """Bytecode written for the stand-in modules (the host's own standard library may write its files too)."""
        return sorted(path.name for path in self.cache.rglob("*.pyc") if self.folder.name in path.relative_to(self.cache).parts)

    def test_a_shipped_module_loads_its_shipped_bytecode_and_writes_nothing(self):
        source = self.ship("shipped_one", 1)
        before = self.shipped_pyc(source).read_bytes()
        report = self.run_child(["shipped_one"])
        self.assertEqual(report["shipped_one"]["loader"], "SourceFileLoader", "Python's own loader type")
        self.assertTrue(report["shipped_one"]["shipped"])
        self.assertEqual(report["shipped_one"]["value"], 1)
        self.assertEqual(report["compiled"], [], "nothing compiled again")
        self.assertEqual(self.cache_files(), [], "nothing written in the bytecode folder")
        self.assertEqual(self.shipped_pyc(source).read_bytes(), before)

    def test_a_shipped_package_and_its_module_load_their_shipped_bytecode(self):
        package = self.scripts / "shipped_package"
        package.mkdir()
        init = self.ship("__init__", 11, folder=package)
        self.ship("inner", 12, folder=package)
        report = self.run_child(["shipped_package", "shipped_package.inner"])
        for name, value in (("shipped_package", 11), ("shipped_package.inner", 12)):
            self.assertEqual((report[name]["loader"], report[name]["shipped"], report[name]["value"]),
                             ("SourceFileLoader", True, value), name)
        self.assertEqual(report["compiled"], [])
        self.assertEqual(report["shipped_package"]["co_filename"], str(init))

    def test_an_optimizing_interpreter_never_gets_the_unoptimized_bytecode(self):
        """Under -O/-OO Python reads opt-N files; the shipped unoptimized one would keep asserts and docstrings."""
        for flag in ("-O", "-OO"):
            with self.subTest(flag):
                self.ship("optimized_%s" % flag.count("O"), 13)
                name = "optimized_%s" % flag.count("O")
                report = self.run_child([name], flags=(flag,))
                self.assertEqual((report[name]["loader"], report[name]["shipped"]), ("SourceFileLoader", False))
                self.assertEqual(report["compiled"], [name + ".py"])

    def test_tracebacks_name_the_real_source(self):
        source = self.ship("shipped_path", 1)
        report = self.run_child(["shipped_path"])
        self.assertEqual(report["shipped_path"]["co_filename"], str(source))

    def test_an_edited_source_compiles_into_the_bytecode_folder_never_beside_it(self):
        source = self.ship("edited", 1)
        shipped = self.shipped_pyc(source).read_bytes()
        source.write_text(module_source(2))
        report = self.run_child(["edited"])
        self.assertEqual(report["edited"]["value"], 2)
        self.assertEqual(report["compiled"], ["edited.py"])
        self.assertEqual(self.cache_files(), ["edited.%s.pyc" % sys.implementation.cache_tag])
        self.assertEqual(self.shipped_pyc(source).read_bytes(), shipped, "the shipped file is never rewritten")

    def test_a_module_without_shipped_bytecode_uses_the_bytecode_folder(self):
        source = self.ship("unshipped", 3, mode=None)
        report = self.run_child(["unshipped"])
        self.assertEqual(report["unshipped"]["value"], 3)
        self.assertEqual(report["compiled"], ["unshipped.py"])
        self.assertFalse(self.shipped_pyc(source).exists())
        self.assertEqual(len(self.cache_files()), 1)

    def test_only_checked_hash_bytecode_is_trusted(self):
        """A timestamp or unchecked .pyc beside a shipped source is not what the install step writes."""
        self.ship("stamped", 4, mode=py_compile.PycInvalidationMode.TIMESTAMP)
        self.ship("unchecked", 5, mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
        report = self.run_child(["stamped", "unchecked"])
        self.assertEqual((report["stamped"]["value"], report["unchecked"]["value"]), (4, 5))
        self.assertEqual(report["compiled"], ["stamped.py", "unchecked.py"])

    def test_a_damaged_shipped_file_falls_back_to_compiling(self):
        source = self.ship("damaged", 6)
        pyc = self.shipped_pyc(source)
        pyc.write_bytes(pyc.read_bytes()[:16] + b"\x00garbage")
        report = self.run_child(["damaged"])
        self.assertEqual(report["damaged"]["value"], 6)
        self.assertEqual(report["compiled"], ["damaged.py"])

    def test_a_module_outside_the_app_keeps_python_s_own_loader(self):
        self.ship("person_addon", 7, folder=self.outside, mode=None)
        report = self.run_child(["person_addon"])
        self.assertEqual((report["person_addon"]["loader"], report["person_addon"]["shipped"]),
                         ("SourceFileLoader", False))
        self.assertEqual(report["compiled"], ["person_addon.py"])
        self.assertEqual(self.cache_files(), ["person_addon.%s.pyc" % sys.implementation.cache_tag])

    def test_with_writing_off_a_shipped_module_still_loads_without_compiling(self):
        """``PYTHONDONTWRITEBYTECODE`` or no bytecode folder: the case that compiled everything at every launch."""
        self.ship("no_writes", 8)
        source = self.ship("no_writes_edited", 9)
        source.write_text(module_source(10))
        report = self.run_child(["no_writes", "no_writes_edited"], cache="-")
        self.assertEqual((report["no_writes"]["value"], report["no_writes_edited"]["value"]), (8, 10))
        self.assertEqual(report["compiled"], ["no_writes_edited.py"])
        self.assertEqual(self.cache_files(), [])
        self.assertEqual(sorted(path.name for path in self.scripts.rglob("*.pyc")),
                         sorted("%s.%s.pyc" % (n, sys.implementation.cache_tag)
                                for n in ("no_writes", "no_writes_edited")))


class StartupBytecodeTests(unittest.TestCase):
    """build_files/utils/angelo_python_bytecode.py: the modules an interpreter imports while it starts also get
    opt-1 and opt-2 bytecode, so -O and -OO runs of the bundled Python write nothing beside their sources."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location("angelo_python_bytecode",
                                                      ROOT / "build_files/utils/angelo_python_bytecode.py")
        self.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.tool)
        self.folder = Path(tempfile.mkdtemp(prefix="angelo-startup-bytecode-"))
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.lib = self.folder / "lib"
        self.lib.mkdir()

    def test_startup_sources_are_the_probed_modules_inside_the_roots(self):
        inside, outside = self.lib / "encodings_like.py", self.folder / "user_site.py"
        calls = []

        def run(command, **options):
            calls.append(command)
            return SimpleNamespace(stdout=json.dumps([str(inside), str(outside)]))
        self.assertEqual(self.tool.startup_sources([str(self.lib)], python="/app/python3.13", run=run), [str(inside)])
        self.assertEqual(calls[0][:4], ["/app/python3.13", "-I", "-B", "-c"])

    def test_paths_compare_as_the_platform_compares_them(self):
        """On Windows a probe may spell the drive or a folder in another case; normcase decides, as Windows does."""
        inside = self.lib / "encodings_like.py"

        def run(command, **options):
            return SimpleNamespace(stdout=json.dumps([str(inside).upper()]))
        folded = lambda path: path.lower()
        with unittest.mock.patch.object(self.tool.os.path, "normcase", folded), \
                unittest.mock.patch.object(self.tool.os.path, "realpath", lambda path: path):
            self.assertEqual(self.tool.startup_sources([str(self.lib)], run=run), [str(inside).upper()])

    def test_both_optimization_levels_are_checked_hash_and_optimized(self):
        source = self.lib / "startup.py"
        source.write_text('"""Docs."""\nassert False, "stripped under -O"\nVALUE = 1\n')
        report = self.tool.precompile_optimized([str(source)], str(self.folder))
        self.assertEqual(sorted(Path(path).name for path in report["compiled"]),
                         ["startup.%s.opt-%d.pyc" % (sys.implementation.cache_tag, level) for level in (1, 2)])
        for level in (1, 2):
            data = Path(self.tool.cache_file(str(source), level)).read_bytes()
            self.assertEqual(data[4:8], b"\x03\x00\x00\x00", "checked hash (PEP 552)")
            namespace = {}
            exec(marshal.loads(data[16:]), namespace)   # The assert is gone at both levels.
            self.assertEqual(namespace["VALUE"], 1)
            self.assertEqual(namespace.get("__doc__"), None if level == 2 else "Docs.")
        self.assertEqual(self.tool.precompile_optimized([str(source)], str(self.folder))["current"], 2)

    def test_an_optimizing_child_finds_them_and_writes_nothing(self):
        source = self.lib / "early.py"
        source.write_text("VALUE = 2\n")
        self.tool.precompile_optimized([str(source)], str(self.folder))
        before = sorted(p.name for p in self.lib.rglob("*.pyc"))
        for flag in ("-O", "-OO"):
            subprocess.run([sys.executable, "-I", flag, "-c", "import sys; sys.path.insert(0, sys.argv[1]); import early",
                            str(self.lib)], check=True, timeout=60)
        self.assertEqual(sorted(p.name for p in self.lib.rglob("*.pyc")), before)


class SiteCustomizeWiringTests(unittest.TestCase):
    def test_it_runs_after_the_folder_rule_with_the_version_folder_and_never_in_the_python_module(self):
        text = SITE.read_text(encoding="utf-8")
        guard = text.index('if os.path.basename(__file__) != "bpy_site_customize.py":\n    _angelo_bytecode_folder(')
        block = text[guard:text.index("\ndel _angelo_bytecode_folder", guard)]
        self.assertLess(block.index("_angelo_bytecode_folder(os.path.dirname(sys.prefix))"),
                        block.index("_angelo_shipped_bytecode("))
        self.assertIn("_angelo_shipped_bytecode(os.path.dirname(sys.prefix))", block)

    def test_it_imports_nothing_at_module_level(self):
        tree = ast.parse(SITE.read_text(encoding="utf-8"))
        imports = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
        self.assertEqual(sorted(alias.name for node in imports for alias in node.names), ["os", "sys"])


if __name__ == "__main__":
    unittest.main()
