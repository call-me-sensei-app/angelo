# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Nothing is written inside the installed app (doc/angelo/releasing.rst, "Nothing is written inside the app").

Dependency-free. The parts that cannot run outside the app are pinned by their source: Angelo's interpreter
chooses a per-user bytecode folder before Python starts (``bpy_interface_pycache.cc``) and hands it to child
interpreters (``bpy.app.python_args``, ``ANGELO_PYCACHE_PREFIX`` read by ``sitecustomize.py``); the install
step precompiles the whole bundled library and Blender's scripts (``angelo_python_bytecode.py``), for any child
that bypasses ``sitecustomize``. The rest runs here: the
``sitecustomize`` rule, the precompiled files and their recognition by the release tools, the seal check's
inventory and verdicts. The retention of old builds' caches is test_native_pycache_retention.py; the seal check
itself runs on a built app (``release_launch_checks.py seal``).
"""

import ast
import importlib.util
import json
import os
import py_compile
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/angelo"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
import release_checks  # noqa: E402
import release_launch_checks as launch  # noqa: E402

INTERN = ROOT / "source/blender/python/intern"
SITE = ROOT / "scripts/site/sitecustomize.py"


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


precompile = load_module(ROOT / "build_files/utils/angelo_python_bytecode.py", "angelo_python_bytecode")


class NativeSourceTests(unittest.TestCase):
    def test_the_bytecode_folder_is_chosen_before_python_starts(self):
        text = (INTERN / "bpy_interface.cc").read_text(encoding="utf-8")
        apply, start = text.index("bpy_pycache_config_apply(&config);"), text.index("Py_InitializeFromConfig(&config)")
        self.assertLess(apply, start)
        source = (INTERN / "bpy_interface_pycache.cc").read_text(encoding="utf-8")
        for needle in ('"PYTHONDONTWRITEBYTECODE"', '"PYTHONPYCACHEPREFIX"', "BKE_appdir_folder_caches",
                       '"pycache"', "build_hash", "config->pycache_prefix", "config->write_bytecode = 0",
                       "BLI_setenv(BPY_PYCACHE_PREFIX_ENV"):
            self.assertIn(needle, source)
        self.assertIn("bpy_interface_pycache.cc", (INTERN / "CMakeLists.txt").read_text(encoding="utf-8"))

    def test_a_relative_explicit_folder_writes_none(self):
        """PYTHONPYCACHEPREFIX=<relative> would put bytecode below the working folder, possibly inside the app."""
        source = (INTERN / "bpy_interface_pycache.cc").read_text(encoding="utf-8")
        explicit = source[source.index('explicit_value("PYTHONPYCACHEPREFIX")'):]
        explicit = explicit[:explicit.index("  if (!pycache_prefix.empty()) {")]
        self.assertIn("BLI_path_is_abs_from_cwd(prefix->c_str())", explicit)
        self.assertIn("pycache_write = false;", explicit)

    def test_children_get_the_same_folder(self):
        app = (INTERN / "bpy_app.cc").read_text(encoding="utf-8")
        getter = app[app.index("static PyObject *bpy_app_python_args_get"):]
        self.assertIn("bpy_pycache_python_args(pycache_option, pycache_value);", getter[:getter.index("\n}\n")])
        source = (INTERN / "bpy_interface_pycache.cc").read_text(encoding="utf-8")
        self.assertIn('r_option = "-B";', source)
        self.assertIn('r_value = "pycache_prefix=" + pycache_prefix;', source)
        header = (INTERN / "bpy_interface_pycache.hh").read_text(encoding="utf-8")
        self.assertIn('#define BPY_PYCACHE_PREFIX_ENV "ANGELO_PYCACHE_PREFIX"', header)
        self.assertIn('os.environ.get("ANGELO_PYCACHE_PREFIX", "")', SITE.read_text(encoding="utf-8"))

    def test_the_install_step_precompiles_with_the_installed_interpreter(self):
        root = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn("include(build_files/cmake/angelo_python_bytecode.cmake)", root)
        module = (ROOT / "build_files/cmake/angelo_python_bytecode.cmake").read_text(encoding="utf-8")
        self.assertIn("build_files/utils/angelo_python_bytecode.py", module)
        self.assertIn('-I -B \\"${ANGELO_PYTHON_BYTECODE_SCRIPT}\\"', module)
        creator = (ROOT / "source/creator/CMakeLists.txt").read_text(encoding="utf-8")
        call = creator.index("angelo_install_python_bytecode(")
        # After every install of the bundled Python and sitecustomize.py, on both platforms.
        self.assertGreater(call, creator.index("FILES ${CMAKE_SOURCE_DIR}/scripts/site/sitecustomize.py"))
        self.assertGreater(call, creator.rindex("DESTINATION ${TARGETDIR_VER}/python/bin"))
        self.assertGreater(call, creator.rindex("DESTINATION ${BLENDER_VERSION}/python/bin"))


class SiteCustomizeTests(unittest.TestCase):
    """The rule in sitecustomize.py, run against stand-ins for ``sys`` and ``os``."""

    def rule(self):
        tree = ast.parse(SITE.read_text(encoding="utf-8"))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == "_angelo_bytecode_folder")
        return ast.Module(body=[function], type_ignores=[])

    def run_rule(self, environ, prefix=None, dont_write=0):
        fake_sys = SimpleNamespace(pycache_prefix=prefix, flags=SimpleNamespace(dont_write_bytecode=dont_write),
                                   dont_write_bytecode=bool(dont_write))
        fake_os = SimpleNamespace(environ=environ, path=os.path)
        namespace = {"sys": fake_sys, "os": fake_os}
        exec(compile(self.rule(), str(SITE), "exec"), namespace)
        namespace["_angelo_bytecode_folder"]()
        return fake_sys

    def test_a_child_of_angelo_uses_its_folder(self):
        folder = os.path.abspath("angelo-cache")
        result = self.run_rule({"ANGELO_PYCACHE_PREFIX": folder})
        self.assertEqual((result.pycache_prefix, result.dont_write_bytecode), (folder, False))

    def test_any_other_run_writes_no_bytecode(self):
        for environ in ({}, {"ANGELO_PYCACHE_PREFIX": ""}, {"ANGELO_PYCACHE_PREFIX": "relative/folder"}):
            result = self.run_rule(environ)
            self.assertEqual((result.pycache_prefix, result.dont_write_bytecode), (None, True), environ)

    def test_a_relative_folder_writes_none(self):
        """A relative prefix is relative to the working folder, which can be inside the app: write nothing."""
        for prefix in ("pyc-rel", os.path.join("..", "cache"), "."):
            result = self.run_rule({"ANGELO_PYCACHE_PREFIX": os.path.abspath("x")}, prefix=prefix)
            self.assertEqual((result.pycache_prefix, result.dont_write_bytecode), (None, True), prefix)

    def test_an_explicit_choice_is_kept(self):
        folder = os.path.abspath("angelo-cache")
        chosen = os.path.abspath("chosen")
        self.assertEqual(self.run_rule({"ANGELO_PYCACHE_PREFIX": folder}, prefix=chosen).pycache_prefix, chosen)
        kept = self.run_rule({"ANGELO_PYCACHE_PREFIX": folder}, dont_write=1)
        self.assertEqual((kept.pycache_prefix, kept.dont_write_bytecode), (None, True))

    def test_it_runs_before_anything_is_imported_and_never_in_the_python_module(self):
        text = SITE.read_text(encoding="utf-8")
        call = text.index("    _angelo_bytecode_folder()\n")
        self.assertIn('if os.path.basename(__file__) != "bpy_site_customize.py":', text[call - 120:call])
        tree = ast.parse(text)
        imports = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
        self.assertEqual(sorted(alias.name for node in imports for alias in node.names), ["os", "sys"])


class PrecompileTests(unittest.TestCase):
    """build_files/utils/angelo_python_bytecode.py: every module of the bundled library and scripts, checked hash."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="angelo-precompile-"))
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.lib = self.folder / "lib"
        (self.lib / "package/sub").mkdir(parents=True)
        (self.lib / "plain.py").write_text("value = 1\n")
        (self.lib / "package/__init__.py").write_text("")
        (self.lib / "package/sub/deep.py").write_text("def f():\n    return 2\n")
        (self.lib / "broken.py").write_text("print 'Python 2'\n")   # Cannot compile, so cannot be imported.
        (self.lib / "data.txt").write_text("not a module")

    def header(self, source):
        with open(precompile.cache_file(str(source)), "rb") as handle:
            return handle.read(16)

    def test_every_module_gets_checked_hash_bytecode(self):
        report = precompile.precompile(str(self.lib))
        modules = [self.lib / "plain.py", self.lib / "package/__init__.py", self.lib / "package/sub/deep.py"]
        self.assertEqual(sorted(report["compiled"]), sorted(str(p) for p in modules))
        self.assertEqual([path for path, _error in report["failed"]], [str(self.lib / "broken.py")])
        for module in modules:
            header = self.header(module)
            self.assertEqual(header[:4], importlib.util.MAGIC_NUMBER)
            self.assertEqual(int.from_bytes(header[4:8], "little"), 0b11, "checked hash (PEP 552)")
            self.assertEqual(header[8:16], importlib.util.source_hash(module.read_bytes()))
            self.assertTrue(release_checks.is_build_bytecode(precompile.cache_file(str(module))))

    def test_an_install_again_compiles_only_what_changed(self):
        precompile.precompile(str(self.lib))
        (self.lib / "plain.py").write_text("value = 3\n")
        report = precompile.precompile(str(self.lib))
        self.assertEqual(report["compiled"], [str(self.lib / "plain.py")])
        self.assertEqual(report["current"], 2)
        self.assertEqual(self.header(self.lib / "plain.py")[8:16],
                         importlib.util.source_hash(b"value = 3\n"))

    def test_the_default_folders_are_the_library_and_blender_s_scripts(self):
        stdlib = os.path.join("x", "5.2", "python", "lib", "python3.13")
        roots = precompile.default_roots(stdlib=stdlib, prefix=os.path.join("x", "5.2", "python"))
        self.assertEqual(roots, [stdlib, os.path.join("x", "5.2", "scripts")])

    def test_cache_file(self):
        self.assertEqual(precompile.cache_file(os.path.join("x", "y.py")),
                         os.path.join("x", "__pycache__", "y.%s.pyc" % sys.implementation.cache_tag))

    def test_precompiled_files_are_build_bytecode_and_python_s_own_are_not(self):
        source = self.folder / "module.py"
        source.write_text("value = 42\n")
        built = precompile.cache_file(str(source))
        py_compile.compile(str(source), cfile=built, doraise=True,
                           invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH)
        written = str(self.folder / "written.pyc")
        py_compile.compile(str(source), cfile=written, doraise=True,
                           invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
        self.assertTrue(release_checks.is_build_bytecode(built))
        self.assertFalse(release_checks.is_build_bytecode(written))
        self.assertFalse(release_checks.is_build_bytecode(str(source)))
        self.assertFalse(release_checks.is_build_bytecode(str(self.folder / "missing.pyc")))

    def test_staging_keeps_build_bytecode_and_drops_the_rest(self):
        source = self.folder / "module.py"
        source.write_text("value = 42\n")
        built = Path(precompile.cache_file(str(source)))
        py_compile.compile(str(source), cfile=str(built), doraise=True,
                           invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH)
        (built.parent / "other.cpython-313.pyc").write_bytes(b"\0" * 16)
        only_runtime = self.folder / "package/__pycache__"
        only_runtime.mkdir(parents=True)
        (only_runtime / "x.cpython-313.pyc").write_bytes(b"\0" * 16)
        removed = []
        release_checks.drop_runtime_bytecode(str(built.parent), removed)
        release_checks.drop_runtime_bytecode(str(only_runtime), removed)
        self.assertEqual(sorted(os.listdir(built.parent)), [built.name])
        self.assertFalse(only_runtime.exists())
        self.assertEqual(sorted(removed), sorted([str(built.parent / "other.cpython-313.pyc"), str(only_runtime)]))


class SealCheckTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="angelo-seal-test-"))
        self.addCleanup(shutil.rmtree, self.folder, True)

    def headless(self, **changes):
        prefix = "/Users/a/Library/Caches/Angelo/pycache/abc"
        launch_report = {"kind": "headless", "pycache_prefix": prefix, "writes_bytecode": True, "addon": True,
                         "python_args": ["-I", "-X", "pycache_prefix=" + prefix],
                         "python_args_child": {"exit": 0, "pycache_prefix": prefix, "error": ""},
                         "multiprocessing_child": {"platform": "macOS", "bytecode_for": prefix + "/angelo-probe/m.pyc"},
                         "pip_sdist_child": {"exit": 0, "stdout": "", "error": ""},
                         "warnings_child": {"exit": 0, "stdout": "", "error": ""},
                         "bytecode": {"files": 4000, "stale": [], "missing": [], "uncompilable": 2}}
        launch_report.update(changes)
        return launch_report

    def test_snapshot_and_changes(self):
        (self.folder / "a").mkdir()
        (self.folder / "a/file.py").write_text("x")
        before = launch.snapshot(self.folder)
        (self.folder / "a/__pycache__").mkdir()
        (self.folder / "a/__pycache__/file.cpython-313.pyc").write_bytes(b"pyc")
        (self.folder / "a/file.py").write_text("changed")
        after = launch.snapshot(self.folder)
        self.assertEqual(launch.changes(before, after), {
            "added": [os.path.join("a", "__pycache__"), os.path.join("a", "__pycache__", "file.cpython-313.pyc")],
            "removed": [], "changed": [os.path.join("a", "file.py")]})
        self.assertEqual(launch.changes(before, before), {"added": [], "removed": [], "changed": []})

    def test_probe_verdicts(self):
        self.assertEqual(launch.probe_problems([self.headless()]), [])
        cases = {
            "wrote no bytecode at all": self.headless(writes_bytecode=False),
            "no cache folder": self.headless(pycache_prefix=None),
            "did not enable": self.headless(addon=False),
            "python_args failed": self.headless(python_args_child={"exit": 1, "pycache_prefix": "", "error": "boom"}),
            "python_args keeps bytecode": self.headless(python_args_child={"exit": 0, "pycache_prefix": "None",
                                                                           "error": ""}),
            "multiprocessing worker": self.headless(multiprocessing_child={
                "platform": "macOS", "bytecode_for": "/angelo-probe/__pycache__/m.cpython-313.pyc"}),
            "pip sdist child failed": self.headless(pip_sdist_child={"exit": 1, "stdout": "", "error": "no"}),
            "warnings child failed": self.headless(warnings_child={"exit": 1, "stdout": "", "error": "no"}),
            "no precompiled": self.headless(bytecode={"files": 0, "stale": [], "missing": [], "uncompilable": 0}),
            "differs from its source": self.headless(bytecode={"files": 3, "stale": ["lib/linecache.py"], "missing": [],
                                                               "uncompilable": 0}),
            "have no precompiled bytecode": self.headless(bytecode={"files": 3, "stale": [], "missing": ["lib/x.py"],
                                                                    "uncompilable": 0}),
        }
        for expected, report in cases.items():
            with self.subTest(expected):
                problems = launch.probe_problems([report])
                self.assertTrue(any(expected in problem for problem in problems), problems)

    def test_a_launch_that_writes_inside_fails_and_a_clean_one_passes(self):
        app = self.folder / "Angelo.app"
        (app / "Contents/Resources").mkdir(parents=True)
        (app / "Contents/Resources/x.py").write_text("x")
        for writes in (True, False):
            with self.subTest(writes=writes):
                calls = []

                def runner(argv, check=False, timeout=None, env=None):
                    calls.append((argv, env))
                    if argv[0] == "ditto":
                        shutil.copytree(argv[1], argv[2])
                    if argv[0] == "open":
                        clone = Path(argv[argv.index("-a") + 1])
                        output = Path(argv[argv.index("--stdout") + 1])
                        if writes:
                            (clone / "Contents/Resources/__pycache__").mkdir(exist_ok=True)
                        report = self.headless() if "--background" in argv else {"addon_at": 1.0, "quit_at": 11.0}
                        mark = launch.SEAL_MARK if "--background" in argv else launch.GUI_MARK
                        output.write_text("noise\n%s%s\n" % (mark, json.dumps(report)))
                    return SimpleNamespace(returncode=0, stdout="", stderr="valid on disk")

                keep = self.folder / ("report-%s.json" % writes)
                if writes:
                    with self.assertRaises(release_checks.CheckError) as raised:
                        launch.seal_check(app, runner, gui=True, keep=keep)
                    self.assertIn("changed 1 entries", str(raised.exception))
                else:
                    report = launch.seal_check(app, runner, gui=True, keep=keep)
                    self.assertEqual([item["kind"] for item in report["launches"]], ["headless", "gui"])
                    self.assertEqual(report["codesign_verify"]["exit"], 0)
                self.assertTrue(keep.is_file())
                opens = [(argv, env) for argv, env in calls if argv[0] == "open"]
                self.assertEqual(len(opens), 2)
                for argv, env in opens:
                    # Launch Services, a throwaway profile, and none of the caller's Python settings.
                    self.assertIn("-W", argv)
                    self.assertTrue(any(item.startswith("HOME=") for item in argv))
                    self.assertFalse([key for key in env if key.upper().startswith("PYTHON")])
                self.assertFalse((app / "Contents/Resources/__pycache__").exists(), "the input is never launched")

    def test_probes_are_valid_python(self):
        probe = ast.parse(launch.SEAL_PROBE)
        # Only the standard library at module level: a spawned worker runs the file again as __mp_main__.
        imported = {alias.name for node in probe.body if isinstance(node, ast.Import) for alias in node.names}
        self.assertFalse(imported & {"bpy", "addon_utils"}, imported)
        ast.parse(launch.GUI_PROBE.replace("SETTLE", "10.0").replace("LIMIT", "480.0"))
        ast.parse(launch.LAUNCH_CHECK)

    def test_the_signing_script_runs_both_checks(self):
        text = (ROOT / "scripts/angelo/sign_notarize_macos.py").read_text(encoding="utf-8")
        self.assertIn("release_launch_checks.launch_check(staged, runner)", text)
        self.assertIn("release_launch_checks.seal_check(staged, runner, gui=not arguments.unstyled_dmg)", text)


if __name__ == "__main__":
    unittest.main()
