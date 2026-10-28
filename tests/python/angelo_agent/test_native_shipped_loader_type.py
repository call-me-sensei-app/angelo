# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A module loaded from the bytecode the app ships has Python's own loader type (doc/angelo/releasing.rst,
"Angelo reads the bytecode it ships").

The shipped-bytecode path hook in ``sitecustomize.py`` once gave FileFinder a ``SourceFileLoader`` subclass. Code
that dispatches on a loader's exact type then found nothing: on Windows pip died in distlib with "Unable to locate
finder for 'pip._vendor.distlib'" (the 1.0.5 build4 seal check). The loader is now a plain ``SourceFileLoader``
with a ``get_code`` of its own, so every such dispatcher sees Python's loader while the code still comes from the
shipped file. Each case runs in an isolated interpreter (``-I -S``) against a stand-in version folder, as
``test_native_shipped_bytecode.py`` does. ``RealAppTests`` runs a real ``pip install`` with an app's bundled
Python when ``ANGELO_TEST_APP_PYTHON`` names it.
"""

import importlib.util
import json
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

from host_test_policy import environment_skip
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SITE = ROOT / "scripts/site/sitecustomize.py"

# Runs in the child: the hook from sitecustomize.py, then one named check, counting what Python had to compile.
CHILD = textwrap.dedent("""
    import ast, builtins, importlib, importlib.machinery as machinery, json, os, sys
    site_path, version, cache, check = sys.argv[1:5]
    sys.pycache_prefix = cache
    tree = ast.parse(open(site_path, encoding="utf-8").read())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "_angelo_shipped_bytecode")
    namespace = {"os": os, "sys": sys}
    exec(compile(ast.Module(body=[function], type_ignores=[]), site_path, "exec"), namespace)
    namespace["_angelo_shipped_bytecode"](version)
    sys.path[:0] = [os.path.join(version, "scripts"), os.path.join(version, "site-packages")]
    compiled = []
    real_compile = builtins.compile
    def counting_compile(source, filename, *args, **kwargs):
        # Only the stand-in app's sources: the host's own standard library compiles into the folder as usual.
        if str(filename).startswith(os.path.join(version, "")):
            compiled.append(os.path.relpath(str(filename), version))
        return real_compile(source, filename, *args, **kwargs)
    builtins.compile = counting_compile

    def describe(module):
        loader = module.__loader__
        return {"stock": type(loader) is machinery.SourceFileLoader, "shipped": "get_code" in vars(loader),
                "same": module.__spec__.loader is loader and loader == module.__spec__.loader}

    def check_types():
        report = {}
        for name in ("typed_module", "typed_package", "typed_package.inner", "typed_ns.part"):
            report[name] = describe(importlib.import_module(name))
        import typed_module, typed_package, typed_ns
        report["namespace_loader"] = type(typed_ns.__spec__.loader).__name__
        fresh = machinery.PathFinder.find_spec("typed_module")
        report["fresh_spec"] = {"stock": type(fresh.loader) is machinery.SourceFileLoader,
                                "origin": fresh.origin == typed_module.__file__,
                                "cached": fresh.cached == typed_module.__cached__ == typed_module.__spec__.cached}
        report["find_spec_is_module_spec"] = importlib.util.find_spec("typed_module") is typed_module.__spec__
        reloaded = importlib.reload(typed_module)
        report["reloaded"] = describe(reloaded)
        report["package_reloaded"] = describe(importlib.reload(typed_package))
        return report

    def check_resources():
        import inspect, linecache, pkgutil, importlib.resources
        import typed_module, typed_package
        reader = typed_package.__loader__.get_resource_reader("typed_package")
        return {"files": importlib.resources.files("typed_package").joinpath("data.txt").read_text(),
                "reader": type(reader).__name__,
                "iter_modules": sorted(info.name for info in pkgutil.iter_modules(typed_package.__path__)),
                "iter_top": "typed_module" in {info.name for info in
                                               pkgutil.iter_modules([os.path.join(version, "scripts")])},
                "get_data": pkgutil.get_data("typed_package", "data.txt").decode(),
                "getsource": inspect.getsource(typed_module.where),
                "getsourcefile": inspect.getsourcefile(typed_module) == typed_module.__file__,
                "linecache": linecache.getline(typed_module.__file__, 1)}

    def check_registry():
        # distlib's _finder_registry and pkg_resources' adapters key on type(loader).
        import typed_module, typed_package
        registry = {machinery.SourceFileLoader: "python's", machinery.SourcelessFileLoader: "sourceless"}
        report = {name: registry.get(type(module.__loader__)) for name, module in
                  (("typed_module", typed_module), ("typed_package", typed_package))}
        if os.path.isdir(os.path.join(version, "site-packages", "pip")):
            from pip._vendor.distlib import resources
            import pip._vendor.distlib as distlib
            finder = resources.finder("pip._vendor.distlib")
            report["distlib"] = {"finder": type(finder).__name__, "loader": describe(distlib),
                                 "resources_loader": describe(resources),
                                 "finds": finder.find("resources.py") is not None,
                                 "iterates": "util.py" in {r.name for r in finder.iterator("")}}
        return report

    def check_pickle():
        import copy, pickle, typed_module, typed_package
        report = {}
        for module in (typed_module, typed_package):
            for protocol in range(pickle.HIGHEST_PROTOCOL + 1):
                spec = pickle.loads(pickle.dumps(module.__spec__, protocol))
                loader = spec.loader
                report["%s-%d" % (module.__name__, protocol)] = {
                    "stock": type(loader) is machinery.SourceFileLoader,
                    "same_place": (loader.name, loader.path) == (module.__loader__.name, module.__loader__.path),
                    "code": loader.get_code(module.__name__).co_filename == module.__file__}
            copied = copy.deepcopy(module.__spec__).loader
            report["%s-deepcopy" % module.__name__] = {"stock": type(copied) is machinery.SourceFileLoader,
                                                       "code": copied.get_code(module.__name__) is not None}
        return report

    def check_threads():
        import threading
        names = ["threaded_%d" % index for index in range(8)]
        barrier, modules = threading.Barrier(len(names)), {}
        def load(name):
            barrier.wait()
            modules[name] = importlib.import_module(name)
        threads = [threading.Thread(target=load, args=(name,)) for name in names]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return {name: describe(modules[name]) for name in names}

    report = globals()["check_" + check]()
    builtins.compile = real_compile
    report["compiled"] = sorted(compiled)
    print(json.dumps(report))
""")

MODULE = "VALUE = 1\n\n\ndef where():\n    return VALUE\n"
PACKAGE_INIT = "VALUE = 2\n"
INNER = "VALUE = 3\n"


def ship(source, text=None, relative=None):
    """A source the way the install step leaves it, with its checked hash-based .pyc beside it."""
    if text is not None:
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(text)
    py_compile.compile(str(source), cfile=importlib.util.cache_from_source(str(source)),
                       dfile=relative or source.name, doraise=True,
                       invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH)


def installed_pip():
    spec = importlib.util.find_spec("pip")
    return Path(spec.origin).parent if spec and spec.origin else None


class ShippedLoaderTypeTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="angelo-shipped-loader-"))
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.version = self.folder / "5.2"
        self.scripts = self.version / "scripts"
        self.cache = self.folder / "user-cache"
        self.cache.mkdir(parents=True)
        ship(self.scripts / "typed_module.py", MODULE)
        ship(self.scripts / "typed_package/__init__.py", PACKAGE_INIT)
        ship(self.scripts / "typed_package/inner.py", INNER)
        (self.scripts / "typed_package/data.txt").write_text("shipped data")
        ship(self.scripts / "typed_ns/part.py", INNER)   # A namespace package: no __init__.
        for index in range(8):
            ship(self.scripts / ("threaded_%d.py" % index), "VALUE = %d\n" % index)
        self.shipped = self.shipped_files()

    def shipped_files(self):
        return sorted((str(path.relative_to(self.version)), path.stat().st_mtime_ns)
                      for path in self.version.rglob("*") if path.is_file())

    def run_child(self, check):
        result = subprocess.run([sys.executable, "-I", "-S", "-c", CHILD, str(SITE), str(self.version),
                                 str(self.cache), check], capture_output=True, text=True, timeout=120, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(self.shipped_files(), self.shipped, "nothing written or changed inside the app")
        return report

    def test_shipped_modules_and_packages_have_python_s_own_loader_type(self):
        report = self.run_child("types")
        expected = {"stock": True, "shipped": True, "same": True}
        for name in ("typed_module", "typed_package", "typed_package.inner", "typed_ns.part", "reloaded",
                     "package_reloaded"):
            self.assertEqual(report[name], expected, name)
        self.assertEqual(report["namespace_loader"], "NamespaceLoader")
        self.assertEqual(report["fresh_spec"], {"stock": True, "origin": True, "cached": True})
        self.assertTrue(report["find_spec_is_module_spec"])
        self.assertEqual(report["compiled"], [], "the code came from the shipped files, reload included")

    def test_resources_pkgutil_and_inspect_work_on_shipped_modules(self):
        report = self.run_child("resources")
        self.assertEqual(report["files"], "shipped data")
        self.assertEqual(report["reader"], "FileReader")
        self.assertEqual(report["iter_modules"], ["inner"])
        self.assertTrue(report["iter_top"])
        self.assertEqual(report["get_data"], "shipped data")
        self.assertEqual(report["getsource"], "def where():\n    return VALUE\n")
        self.assertTrue(report["getsourcefile"])
        self.assertEqual(report["linecache"], "VALUE = 1\n")
        self.assertEqual(report["compiled"], [])

    def test_a_registry_keyed_on_the_loader_type_finds_python_s_entry(self):
        report = self.run_child("registry")
        self.assertEqual((report["typed_module"], report["typed_package"]), ("python's", "python's"))
        self.assertEqual(report["compiled"], [])

    @unittest.skipUnless(installed_pip(), "pip is not importable in the test interpreter")
    def test_distlib_finds_its_resource_finder_for_a_shipped_pip(self):
        """The 1.0.5 build4 failure: pip's distlib looks its finder up by type(loader)."""
        pip = self.version / "site-packages/pip"
        shutil.copytree(installed_pip(), pip, ignore=shutil.ignore_patterns("__pycache__"))
        sources = [pip / "__init__.py", pip / "_vendor/__init__.py", *sorted((pip / "_vendor/distlib").glob("*.py"))]
        for source in sources:
            ship(source, relative=str(source.relative_to(self.version)))
        self.shipped = self.shipped_files()
        report = self.run_child("registry")
        distlib = report["distlib"]
        self.assertEqual(distlib["finder"], "ResourceFinder")
        self.assertEqual(distlib["loader"], {"stock": True, "shipped": True, "same": True})
        self.assertEqual(distlib["resources_loader"], {"stock": True, "shipped": True, "same": True})
        self.assertTrue(distlib["finds"] and distlib["iterates"])
        self.assertFalse(set(report["compiled"]) & {str(source.relative_to(self.version)) for source in sources},
                         "distlib and pip's packages came from the shipped files")

    def test_a_pickled_or_copied_spec_comes_back_with_a_working_python_loader(self):
        report = self.run_child("pickle")
        self.assertTrue(report.pop("compiled") is not None)
        for key, value in report.items():
            self.assertTrue(all(value.values()), (key, value))

    def test_concurrent_imports_get_python_s_loader_and_the_shipped_code(self):
        report = self.run_child("threads")
        self.assertEqual(report.pop("compiled"), [])
        for name, value in report.items():
            self.assertEqual(value, {"stock": True, "shipped": True, "same": True}, name)


# Runs in an app's bundled Python, whose own sitecustomize installs the hook.
APP_CHILD = textwrap.dedent("""
    import importlib.machinery as machinery, json, multiprocessing, sys
    def loader_of(name):
        import importlib
        module = importlib.import_module(name)
        return [type(module.__loader__) is machinery.SourceFileLoader, "get_code" in vars(module.__loader__)]
    if __name__ == "__main__":
        from pip._vendor.distlib.resources import finder
        resources = finder("pip._vendor.distlib").iterator("")
        report = {"distlib": sorted(r.name for r in resources if r.name.endswith(".py"))[:3],
                  "json": loader_of("json"), "pip": loader_of("pip._vendor.distlib.resources")}
        with multiprocessing.get_context("spawn").Pool(1) as pool:
            report["spawned"] = pool.apply(loader_of, ("email.mime.text",))
        print(json.dumps(report))
""")

BACKEND = textwrap.dedent('''
    import base64, hashlib, os, zipfile
    NAME = "angelo_loader_pkg"
    FILES = {NAME + "/__init__.py": b"VALUE = 1\\n",
             NAME + "-0.1.dist-info/METADATA": b"Metadata-Version: 2.1\\nName: angelo-loader-pkg\\nVersion: 0.1\\n",
             NAME + "-0.1.dist-info/WHEEL": (b"Wheel-Version: 1.0\\nGenerator: t\\nRoot-Is-Purelib: true\\n"
                                             b"Tag: py3-none-any\\n")}
    def get_requires_for_build_wheel(config_settings=None):
        return []
    def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
        wheel_name, record = NAME + "-0.1-py3-none-any.whl", []
        with zipfile.ZipFile(os.path.join(wheel_directory, wheel_name), "w") as wheel:
            for path, data in FILES.items():
                wheel.writestr(path, data)
                digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
                record.append("%s,sha256=%s,%d" % (path, digest, len(data)))
            record.append(NAME + "-0.1.dist-info/RECORD,,")
            wheel.writestr(NAME + "-0.1.dist-info/RECORD", "\\n".join(record) + "\\n")
        return wheel_name
''')
PYPROJECT = ('[build-system]\nrequires = []\nbuild-backend = "backend"\nbackend-path = ["."]\n\n'
             '[project]\nname = "angelo-loader-pkg"\nversion = "0.1"\n')
SETUP_PY = ('from setuptools import setup\n'
            'setup(name="angelo-setup-pkg", version="0.1", packages=["angelo_setup_pkg"])\n')


@unittest.skipUnless(os.environ.get("ANGELO_TEST_APP_PYTHON"), environment_skip("set ANGELO_TEST_APP_PYTHON to an app's bundled Python"))
class RealAppTests(unittest.TestCase):
    """A real ``pip install`` of local source distributions by an app's bundled Python, offline, and a spawned
    worker; the app's version folder must be unchanged afterwards."""

    def setUp(self):
        self.python = os.environ["ANGELO_TEST_APP_PYTHON"]
        # <version>/python/bin/python3.13 on macOS and Linux, <version>/python/bin/python.exe on Windows.
        self.version = Path(os.path.realpath(self.python)).parents[2]
        self.work = Path(tempfile.mkdtemp(prefix="angelo-loader-pip-"))
        self.addCleanup(shutil.rmtree, self.work, True)

    def snapshot(self):
        return {str(path): (path.stat().st_size, path.stat().st_mtime_ns)
                for path in self.version.rglob("*") if path.is_file() and not path.is_symlink()}

    def run_app(self, *args):
        result = subprocess.run([self.python, *args], capture_output=True, text=True, timeout=600,
                                cwd=self.work, check=False)
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-4000:])
        return result

    def test_pip_installs_source_distributions_and_nothing_changes_inside_the_app(self):
        isolated = self.work / "isolated"
        isolated.mkdir()
        (isolated / "backend.py").write_text(BACKEND)
        (isolated / "pyproject.toml").write_text(PYPROJECT)
        legacy = self.work / "legacy"
        (legacy / "angelo_setup_pkg").mkdir(parents=True)
        (legacy / "angelo_setup_pkg/__init__.py").write_text("VALUE = 2\n")
        (legacy / "setup.py").write_text(SETUP_PY)
        before = self.snapshot()
        common = ["-m", "pip", "install", "--no-index", "--no-deps", "--no-cache-dir", "--disable-pip-version-check"]
        # Default build isolation (pip's own build environment, as the seal check runs it), then setuptools from
        # the app's own site-packages without isolation.
        self.run_app(*common, "--target", str(self.work / "target-isolated"), str(isolated))
        self.run_app(*common, "--no-build-isolation", "--target", str(self.work / "target-legacy"), str(legacy))
        self.assertTrue((self.work / "target-isolated/angelo_loader_pkg/__init__.py").is_file())
        self.assertTrue((self.work / "target-legacy/angelo_setup_pkg/__init__.py").is_file())
        child = self.work / "loader_child.py"   # A file: a spawned worker imports its __main__ again.
        child.write_text(APP_CHILD)
        report = json.loads(self.run_app(str(child)).stdout.strip().splitlines()[-1])
        self.assertTrue(report["distlib"], "distlib found its resource finder")
        self.assertEqual((report["json"], report["pip"], report["spawned"]), ([True, True],) * 3)
        after = self.snapshot()
        self.assertEqual(sorted(set(after) ^ set(before)), [], "no file added or removed inside the app")
        self.assertEqual([path for path in before if before[path] != after[path]], [], "no file changed")


if __name__ == "__main__":
    unittest.main()
