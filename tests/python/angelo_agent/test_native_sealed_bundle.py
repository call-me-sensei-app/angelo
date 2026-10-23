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
import contextlib
import importlib.util
import io
import json
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
seal_probe = load_module(ROOT / "scripts/angelo/release_seal_probe.py", "release_seal_probe")


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

    def test_an_absolute_folder_that_lands_inside_the_app_writes_none(self):
        """PYTHONPYCACHEPREFIX inside the app, or "/" (or another ancestor) whose mirror of the app's sources lands
        inside it, would write Angelo's own startup bytecode into the app before sitecustomize runs."""
        source = (INTERN / "bpy_interface_pycache.cc").read_text(encoding="utf-8")
        apply = source[source.index("void bpy_pycache_config_apply(PyConfig *config)"):]
        apply = apply[:apply.index("\n}\n")]
        refuse = apply.index("pycache_prefix_inside_install(pycache_prefix)")
        # Checked for every chosen folder (explicit or default), before Python or a child is told about it.
        self.assertLess(apply.index("pycache_prefix = pycache_default_prefix();"), refuse)
        self.assertLess(refuse, apply.index("config->pycache_prefix"))
        self.assertLess(refuse, apply.index("BLI_setenv(BPY_PYCACHE_PREFIX_ENV"))
        branch = apply[refuse:apply.index("  if (!pycache_prefix.empty()) {", refuse)]
        self.assertIn("pycache_prefix.clear();", branch)
        self.assertIn("pycache_write = false;", branch)
        helper = source[source.index("static bool pycache_prefix_inside_install("):]
        helper = helper[:helper.index("\n}\n")]
        self.assertIn("BLI_path_contains(folder.c_str(), resolved_prefix.c_str())", helper)
        self.assertIn("pycache_mirror(resolved_prefix, folder)", helper)
        folders = source[source.index("static std::vector<std::string> pycache_install_folders()"):]
        folders = folders[:folders.index("\n}\n")]
        self.assertIn("BKE_appdir_program_dir()", folders)
        self.assertEqual(folders.count("BLI_path_parent_dir(program);"), 2, "macOS: the whole <name>.app bundle")
        self.assertIn("BLENDER_SYSTEM_PYTHON", folders)
        mirror = source[source.index("static std::string pycache_mirror("):]
        mirror = mirror[:mirror.index("\n}\n")]
        self.assertIn("rest[1] == ':'", mirror, "a Windows drive is dropped, as importlib's cache_from_source does")
        resolved = source[source.index("static std::string pycache_resolved("):]
        resolved = resolved[:resolved.index("\n}\n")]
        self.assertIn("realpath(head, real)", resolved)
        self.assertIn("BLI_str_tolower_ascii", resolved)

    def test_children_get_the_same_folder(self):
        app = (INTERN / "bpy_app.cc").read_text(encoding="utf-8")
        getter = app[app.index("static PyObject *bpy_app_python_args_get"):]
        self.assertIn("bpy_pycache_python_args(pycache_option, pycache_value);", getter[:getter.index("\n}\n")])
        source = (INTERN / "bpy_interface_pycache.cc").read_text(encoding="utf-8")
        self.assertIn('r_option = "-B";', source)
        self.assertIn('r_value = "pycache_prefix=" + pycache_prefix;', source)
        header = (INTERN / "bpy_interface_pycache.hh").read_text(encoding="utf-8")
        self.assertIn('#define BPY_PYCACHE_PREFIX_ENV "ANGELO_PYCACHE_PREFIX"', header)
        self.assertIn('os.environ.get("ANGELO_PYCACHE_PREFIX")', SITE.read_text(encoding="utf-8"))

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
    """The rule in sitecustomize.py, run against stand-ins for ``sys`` and ``os.environ`` and real folders: what this
    interpreter writes, and what it exports for every child before any can start."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="angelo-site-rule-"))
        self.addCleanup(shutil.rmtree, self.folder, True)
        # A macOS layout; the Windows/Linux one (<install>/5.2) is test_the_install_folder_on_other_platforms.
        self.app = self.folder / "Angelo.app"
        self.version = self.app / "Contents/Resources/5.2"
        (self.app / "Contents/MacOS").mkdir(parents=True)
        self.version.mkdir(parents=True)
        self.cache = self.folder / "home/Library/Caches/Angelo/pycache/abc"
        self.cache.mkdir(parents=True)

    def rule(self):
        tree = ast.parse(SITE.read_text(encoding="utf-8"))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == "_angelo_bytecode_folder")
        return ast.Module(body=[function], type_ignores=[])

    def run_rule(self, environ, prefix=None, dont_write=0, version=None):
        fake_sys = SimpleNamespace(pycache_prefix=prefix, flags=SimpleNamespace(dont_write_bytecode=dont_write),
                                   dont_write_bytecode=bool(dont_write), platform=sys.platform)
        fake_os = SimpleNamespace(environ=environ, path=os.path, access=os.access, W_OK=os.W_OK, X_OK=os.X_OK)
        namespace = {"sys": fake_sys, "os": fake_os}
        exec(compile(self.rule(), str(SITE), "exec"), namespace)
        namespace["_angelo_bytecode_folder"](str(version or self.version))
        return fake_sys

    def assert_writes_to(self, environ, result, folder):
        self.assertEqual((result.pycache_prefix, result.dont_write_bytecode), (folder, False))
        self.assertEqual(environ.get("PYTHONPYCACHEPREFIX"), folder, "exported for children")
        self.assertNotIn("PYTHONDONTWRITEBYTECODE", environ)

    def assert_writes_none(self, environ, result, case=None):
        self.assertEqual((result.pycache_prefix, result.dont_write_bytecode), (None, True), case)
        self.assertEqual(environ.get("PYTHONDONTWRITEBYTECODE"), "1", case)
        self.assertNotIn("PYTHONPYCACHEPREFIX", environ, case)
        self.assertNotIn("ANGELO_PYCACHE_PREFIX", environ, case)

    def test_angelo_s_folder_is_used_and_exported(self):
        environ = {"ANGELO_PYCACHE_PREFIX": str(self.cache)}
        self.assert_writes_to(environ, self.run_rule(environ, prefix=str(self.cache)), str(self.cache))

    def test_a_child_of_angelo_uses_it_too(self):
        environ = {"ANGELO_PYCACHE_PREFIX": str(self.cache), "PYTHONPYCACHEPREFIX": str(self.cache)}
        self.assert_writes_to(environ, self.run_rule(environ), str(self.cache))

    def test_angelo_s_folder_overrides_a_person_s_own(self):
        """A person's PYTHONPYCACHEPREFIX or -X pycache_prefix (absolute or relative) gives way to Angelo's."""
        for own in (str(self.folder / "elsewhere"), "pyc-rel", os.path.join("..", "cache"), "."):
            with self.subTest(own):
                environ = {"ANGELO_PYCACHE_PREFIX": str(self.cache), "PYTHONPYCACHEPREFIX": own}
                self.assert_writes_to(environ, self.run_rule(environ, prefix=own), str(self.cache))

    def test_without_angelo_a_person_s_absolute_folder_is_kept(self):
        own = str(self.folder / "elsewhere")
        environ = {"PYTHONPYCACHEPREFIX": own}
        self.assert_writes_to(environ, self.run_rule(environ, prefix=own), own)

    def test_with_no_folder_nothing_is_written_and_children_are_told(self):
        for environ, prefix in (({}, None), ({"ANGELO_PYCACHE_PREFIX": ""}, None),
                                ({"ANGELO_PYCACHE_PREFIX": "relative/folder"}, None),
                                ({"PYTHONPYCACHEPREFIX": "pyc-rel"}, "pyc-rel"),
                                ({"PYTHONPYCACHEPREFIX": "."}, ".")):
            with self.subTest(environ=environ):
                self.assert_writes_none(environ, self.run_rule(environ, prefix=prefix))

    def test_writing_turned_off_stays_off_for_children(self):
        for environ, dont_write in (({"ANGELO_PYCACHE_PREFIX": str(self.cache)}, 1),
                                    ({"ANGELO_PYCACHE_PREFIX": str(self.cache), "PYTHONDONTWRITEBYTECODE": "1"}, 0)):
            with self.subTest(environ=environ, flag=dont_write):
                self.assert_writes_none(environ, self.run_rule(environ, dont_write=dont_write))

    def test_a_folder_that_lands_inside_the_app_writes_none(self):
        """Inside the bundle (any part of it, through a link or in another case), or "/" whose mirror of the app's
        sources is the app itself."""
        link = self.folder / "link"
        link.symlink_to(self.app / "Contents")
        inside = [self.version / "cache", self.app / "Contents/MacOS/cache", self.app, link / "new/cache",
                  self.folder / "Angelo.app/../Angelo.app/Contents/x", Path(os.path.abspath(os.sep))]
        if sys.platform in ("darwin", "win32"):
            inside.append(Path(str(self.app).upper()) / "Contents/x")
        for prefix in inside:
            with self.subTest(str(prefix)):
                environ = {"PYTHONPYCACHEPREFIX": str(prefix)}
                self.assert_writes_none(environ, self.run_rule(environ, prefix=str(prefix)))
                environ = {"ANGELO_PYCACHE_PREFIX": str(prefix)}
                self.assert_writes_none(environ, self.run_rule(environ))

    def test_a_folder_beside_or_above_the_app_is_kept(self):
        """The app's parent mirrors the sources to <parent>/<app path>, outside the app; a same-prefix sibling too."""
        for prefix in (self.folder, self.folder / "Angelo.app2/cache"):
            with self.subTest(str(prefix)):
                environ = {"ANGELO_PYCACHE_PREFIX": str(prefix)}
                self.assert_writes_to(environ, self.run_rule(environ), str(prefix))

    def test_the_install_folder_on_other_platforms(self):
        """<install>/5.2: the whole install folder is the app (Windows, Linux)."""
        install = self.folder / "Angelo"
        version = install / "5.2"
        version.mkdir(parents=True)
        for prefix, writes in ((install / "cache", False), (version, False), (self.folder / "cache", True)):
            with self.subTest(str(prefix)):
                environ = {"ANGELO_PYCACHE_PREFIX": str(prefix)}
                result = self.run_rule(environ, version=version)
                if writes:
                    self.assert_writes_to(environ, result, str(prefix))
                else:
                    self.assert_writes_none(environ, result)

    def test_a_drive_root_mirroring_into_an_install_on_a_substituted_drive_writes_none(self):
        """Windows: S: substituted for C:\work. Python mirrors the path as written (S:\Angelo\...), so the prefix S:\
        lands inside the install although the resolved install is C:\work\Angelo."""
        import ntpath

        def realpath(path):
            path = ntpath.abspath(path)
            return "C:\\work" + path[2:] if path[:2].upper() == "S:" else path

        windows_path = SimpleNamespace(**{name: getattr(ntpath, name) for name in dir(ntpath) if not name.startswith("_")})
        windows_path.realpath, windows_path.isdir, windows_path.lexists = realpath, (lambda path: True), (lambda path: True)
        for prefix, writes in (("S:\\", False), ("C:\\work\\Angelo\\cache", False), ("S:\\Angelo\\cache", False),
                               ("D:\\", True), ("S:\\elsewhere", True)):
            with self.subTest(prefix):
                environ = {"ANGELO_PYCACHE_PREFIX": prefix}
                fake_sys = SimpleNamespace(pycache_prefix=None, flags=SimpleNamespace(dont_write_bytecode=0),
                                           dont_write_bytecode=False, platform="win32")
                fake_os = SimpleNamespace(environ=environ, path=windows_path, access=lambda path, mode: True,
                                          W_OK=os.W_OK, X_OK=os.X_OK)
                namespace = {"sys": fake_sys, "os": fake_os}
                exec(compile(self.rule(), str(SITE), "exec"), namespace)
                namespace["_angelo_bytecode_folder"]("S:\\Angelo\\5.2")
                self.assertEqual(fake_sys.pycache_prefix, prefix if writes else None)

    @unittest.skipIf(sys.platform == "win32" or not hasattr(os, "geteuid") or os.geteuid() == 0,
                     "needs POSIX permissions and a user they apply to")
    def test_an_unwritable_folder_writes_none(self):
        os.chmod(self.cache, 0o500)
        self.addCleanup(os.chmod, self.cache, 0o700)
        for prefix in (self.cache, self.cache / "not-yet"):
            with self.subTest(str(prefix)):
                environ = {"ANGELO_PYCACHE_PREFIX": str(prefix)}
                self.assert_writes_none(environ, self.run_rule(environ))
        blocked = self.folder / "a-file"
        blocked.write_text("")
        environ = {"ANGELO_PYCACHE_PREFIX": str(blocked / "cache")}
        self.assert_writes_none(environ, self.run_rule(environ))

    def test_a_missing_folder_below_a_writable_one_is_used(self):
        """The first launch of a build: Python creates the folder as it writes."""
        prefix = str(self.cache.parent / "new-build")
        environ = {"ANGELO_PYCACHE_PREFIX": prefix}
        self.assert_writes_to(environ, self.run_rule(environ), prefix)

    def test_it_runs_before_anything_is_imported_and_never_in_the_python_module(self):
        text = SITE.read_text(encoding="utf-8")
        call = text.index("    _angelo_bytecode_folder(os.path.dirname(sys.prefix))\n")
        self.assertIn('if os.path.basename(__file__) != "bpy_site_customize.py":', text[call - 120:call])
        tree = ast.parse(text)
        imports = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
        self.assertEqual(sorted(alias.name for node in imports for alias in node.names), ["os", "sys"])


class ExportedFolderTests(unittest.TestCase):
    """A real child of a process that ran the rule: the exported folder holds from its interpreter's start, before
    ``site`` (here ``-S``, as a child that never runs sitecustomize) and while optimizing."""

    def test_an_optimizing_child_without_site_writes_into_the_exported_folder(self):
        folder = Path(tempfile.mkdtemp(prefix="angelo-site-export-"))
        self.addCleanup(shutil.rmtree, folder, True)
        version, cache, lib = folder / "app/5.2", folder / "cache", folder / "lib"
        for path in (version, cache, lib):
            path.mkdir(parents=True)
        (lib / "early_module.py").write_text("VALUE = 1\n")
        parent = textwrap.dedent("""
            import ast, os, subprocess, sys
            site_path, version, cache, lib = sys.argv[1:5]
            os.environ["ANGELO_PYCACHE_PREFIX"] = cache
            tree = ast.parse(open(site_path, encoding="utf-8").read())
            function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                            and node.name == "_angelo_bytecode_folder")
            namespace = {"os": os, "sys": sys}
            exec(compile(ast.Module(body=[function], type_ignores=[]), site_path, "exec"), namespace)
            namespace["_angelo_bytecode_folder"](version)
            child = "import sys; sys.path.insert(0, sys.argv[1]); import early_module; print(sys.pycache_prefix)"
            print(subprocess.run([sys.executable, "-S", "-O", "-c", child, lib], capture_output=True, text=True,
                                 check=True).stdout.strip())
        """)
        environ = {key: value for key, value in os.environ.items() if not key.startswith("PYTHON")}
        result = subprocess.run([sys.executable, "-S", "-c", parent, str(SITE), str(version), str(cache), str(lib)],
                                capture_output=True, text=True, timeout=60, env=environ, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(cache))
        self.assertEqual([path.name for path in lib.rglob("*.pyc")], [], "nothing beside the source")
        self.assertEqual([path.name for path in cache.rglob("early_module*.pyc")],
                         ["early_module.%s.opt-1.pyc" % sys.implementation.cache_tag])


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


class SealProbeTests(unittest.TestCase):
    """release_seal_probe.py, run inside the app: the children it starts."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="angelo-seal-probe-test-"))
        self.addCleanup(shutil.rmtree, self.folder, True)

    def test_it_starts_optimizing_children_that_only_inherit_the_exported_folder(self):
        """-O, -OO and PYTHONOPTIMIZE=1 pip without python_args (whose -I and -X would hide a missing export)."""
        calls = []

        def run(argv, cwd, env=None):
            calls.append((argv, env))
            return {"exit": 0, "stdout": "/c", "error": ""}

        class Pool:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def apply(self, function, args=()):
                return "/c/angelo-probe/module.pyc"

        app = SimpleNamespace(python_args=("-I", "-X", "pycache_prefix=/c"))
        fakes = {"bpy": SimpleNamespace(app=app),
                 "addon_utils": SimpleNamespace(enable=lambda *a, **k: None, check=lambda name: (True, True))}
        output = io.StringIO()
        with unittest.mock.patch.dict(sys.modules, fakes), \
                unittest.mock.patch.dict(os.environ, {"PYTHONPYCACHEPREFIX": "/c"}), \
                unittest.mock.patch.object(seal_probe, "run", run), \
                unittest.mock.patch.object(seal_probe, "bytecode", lambda roots: {}), \
                unittest.mock.patch.object(seal_probe.tempfile, "mkdtemp", lambda prefix: str(self.folder / "work")), \
                unittest.mock.patch.object(seal_probe.multiprocessing, "get_context",
                                           lambda method: SimpleNamespace(Pool=lambda size: Pool())), \
                contextlib.redirect_stdout(output):
            (self.folder / "work").mkdir()
            seal_probe.probe()
        report = json.loads(output.getvalue().split("ANGELO_SEAL_PROBE ", 1)[1])
        children = report["optimize_children"]
        self.assertEqual(sorted(children), ["-O", "-O, replaced environment", "-OO", "PYTHONOPTIMIZE=1 pip"])
        self.assertEqual((children["-O"]["pycache_prefix"], children["-OO"]["pycache_prefix"]), ("/c", "/c"))
        optimizing = [(argv, env) for argv, env in calls
                      if (argv[1] in ("-O", "-OO") and env is None) or "target-optimized" in argv[-2]]
        self.assertEqual([argv[1] for argv, _env in optimizing], ["-O", "-OO", "-m"])
        for argv, _env in optimizing:
            self.assertEqual(argv[0], sys.executable)
            self.assertFalse({"-I", "-E", "-X", "-B"} & set(argv), argv)
        # This process's own environment (whatever sitecustomize exported), plus PYTHONOPTIMIZE for pip.
        pip_argv, pip_env = optimizing[2]
        self.assertEqual(pip_argv[1:4], ["-m", "pip", "install"])
        self.assertEqual((pip_env["PYTHONOPTIMIZE"], pip_env["PYTHONPYCACHEPREFIX"]), ("1", "/c"))
        self.assertEqual([env for _argv, env in optimizing[:2]], [None, None])
        # A replaced environment: nothing exported reaches it, only what a process needs to start.
        replaced = [(argv, env) for argv, env in calls if argv[1] == "-O" and env is not None]
        self.assertEqual(len(replaced), 1)
        self.assertFalse([key for key in replaced[0][1] if key.startswith(("PYTHON", "ANGELO_"))], replaced[0][1])
        self.assertEqual(children["-O, replaced environment"]["stdout"], "/c")   # Kept as the child printed it.


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
                         "optimize_children": {"-O": {"exit": 0, "pycache_prefix": prefix, "error": ""},
                                               "-OO": {"exit": 0, "pycache_prefix": prefix, "error": ""},
                                               "PYTHONOPTIMIZE=1 pip": {"exit": 0, "stdout": "", "error": ""},
                                               "-O, replaced environment": {"exit": 0, "stdout": "None 1 0",
                                                                            "error": ""}},
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
            "optimizing child (PYTHONOPTIMIZE=1 pip) failed": self.headless(optimize_children=dict(
                self.headless()["optimize_children"], **{"PYTHONOPTIMIZE=1 pip": {"exit": 1, "stdout": "",
                                                                                  "error": "no"}})),
            "optimizing child (-O, replaced environment) failed": self.headless(optimize_children=dict(
                self.headless()["optimize_children"], **{"-O, replaced environment": {"exit": 1, "stdout": "",
                                                                                      "error": "no"}})),
            "optimizing child (-OO) keeps bytecode in None": self.headless(optimize_children=dict(
                self.headless()["optimize_children"], **{"-OO": {"exit": 0, "pycache_prefix": "None", "error": ""}})),
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
