# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The cold-launch rule (cold_bytecode): a first launch compiles only what the app does not ship compiled.

first_launch_frames' ``cold`` phase used to require more than 200 compiled files as its proof of a cold start;
shipped bytecode (e80af48d1e5) made 31 the right number. The phase now checks the premise directly (its folder
empty before launch, host side) and this contract: no module the shipped-bytecode loader serves is compiled into
the bytecode folder (only ones imported before it existed, and sources without a matching shipped file), and
Angelo's add-on loads through that loader. This checks the rule with real files: a shipped source, an edited one
and one that never shipped.
"""

import os
import py_compile
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cold_bytecode  # noqa: E402


class ColdBytecodeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="cold-bytecode-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.app = self.root / "app"
        self.prefix = self.root / "prefix"
        (self.app / "pkg").mkdir(parents=True)
        self.shipped = self.write("pkg/shipped.py", "VALUE = 1\n", ship=True)
        self.edited = self.write("pkg/edited.py", "VALUE = 2\n", ship=True)
        self.edited.write_text("VALUE = 3\n")   # Changed after the install step compiled it.
        self.plain = self.write("pkg/plain.py", "VALUE = 4\n", ship=False)
        self.unchecked = self.write("pkg/unchecked.py", "VALUE = 5\n", ship=False)
        py_compile.compile(str(self.unchecked), doraise=True)   # A timestamp .pyc: not what the install writes.

    def write(self, name, text, ship):
        path = self.app / name
        path.write_text(text)
        if ship:   # What build_files/utils/angelo_python_bytecode.py writes: checked hash-based, next to its source.
            py_compile.compile(str(path), doraise=True,
                               invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH)
        return path

    def compile_into_prefix(self, source):
        """What a launch with ``sys.pycache_prefix`` writes for ``source``."""
        old = sys.pycache_prefix
        sys.pycache_prefix = str(self.prefix)
        try:
            py_compile.compile(str(source), doraise=True)
        finally:
            sys.pycache_prefix = old

    def test_only_sources_that_ship_compiled_count(self):
        self.assertTrue(cold_bytecode.shipped_bytecode(str(self.shipped)))
        self.assertFalse(cold_bytecode.shipped_bytecode(str(self.edited)), "an edited source compiles, as it should")
        self.assertFalse(cold_bytecode.shipped_bytecode(str(self.plain)))
        self.assertFalse(cold_bytecode.shipped_bytecode(str(self.unchecked)), "only checked hash-based files")

    def module(self, source, shipped_loader):
        """A loaded module; the shipped loader has an instance ``get_code`` (sitecustomize.source_loader)."""
        loader = SimpleNamespace(get_code=lambda name: None) if shipped_loader else SimpleNamespace()
        return SimpleNamespace(__file__=str(source), __spec__=SimpleNamespace(loader=loader))

    def test_a_cold_launch_never_compiles_a_module_the_shipped_loader_serves(self):
        for source in (self.edited, self.plain):
            self.compile_into_prefix(source)
        clean = cold_bytecode.audit(str(self.prefix), modules={})
        self.assertEqual((clean["written"], clean["recompiled_shipped"]), (2, []))
        self.compile_into_prefix(self.shipped)
        early = cold_bytecode.audit(str(self.prefix), modules={"pkg.shipped": self.module(self.shipped, False)})
        self.assertEqual(early["recompiled_before_loader"], ["pkg.shipped"], "imported before the loader: allowed")
        self.assertEqual(early["recompiled_by_shipped_loader"] + early["recompiled_unknown"], [])
        missed = cold_bytecode.audit(str(self.prefix), modules={"pkg.shipped": self.module(self.shipped, True)})
        self.assertEqual(missed["recompiled_by_shipped_loader"], ["pkg.shipped"], "the loader should have read it")
        twice = cold_bytecode.audit(str(self.prefix), modules={"pkg.shipped": self.module(self.shipped, True),
                                                               "_by_file": self.module(self.shipped, False)})
        self.assertEqual(twice["recompiled_before_loader"], ["_by_file"],
                         "the same source also loaded straight from its file (sitecustomize's TLS check)")
        gone = cold_bytecode.audit(str(self.prefix), modules={})
        self.assertEqual(gone["recompiled_unknown"], [str(self.shipped)], "no module to explain it: a miss")
        self.assertEqual(cold_bytecode.audit(str(self.root / "missing"), modules={})["written"], 0)

    def loaded(self, source, reads_shipped):
        """A module the shipped loader served: its instance ``get_code`` reads the shipped file, or (broken)
        compiles the source as Python's own loader does."""
        import importlib.machinery
        import marshal
        import types
        loader = importlib.machinery.SourceFileLoader("pkg." + source.stem, str(source))
        if reads_shipped:
            shipped = source.parent / "__pycache__" / ("%s.%s.pyc" % (source.stem, sys.implementation.cache_tag))
            loader.get_code = types.MethodType(lambda own, name: marshal.loads(shipped.read_bytes()[16:]), loader)
        else:
            loader.get_code = types.MethodType(importlib.machinery.SourceFileLoader.get_code, loader)
        return SimpleNamespace(__file__=str(source), __spec__=SimpleNamespace(loader=loader, name=loader.name))

    def test_the_probe_names_a_shipped_loader_that_compiles_instead_of_reading(self):
        """release-105 fix round: a windowed launch compiles files loaded outside the import system (keyconfig
        presets through bpy.utils.execfile), which says nothing about the loader; asking the loader does."""
        before = (sys.pycache_prefix, sys.dont_write_bytecode)
        good = {"pkg.shipped": self.loaded(self.shipped, True)}
        self.assertEqual(cold_bytecode.probe_loader(good, app_root=str(self.app)), [])
        broken = {"pkg.shipped": self.loaded(self.shipped, False)}
        self.assertEqual(cold_bytecode.probe_loader(broken, app_root=str(self.app)), ["pkg.shipped"])
        self.assertEqual((sys.pycache_prefix, sys.dont_write_bytecode), before, "the interpreter's own settings back")
        self.assertEqual(list(self.prefix.glob("**/*.pyc")), [], "never into the launch's own folder")

    def test_loaded_modules_from_shipped_bytecode_are_inside_the_app_only(self):
        outside = self.root / "outside.py"
        outside.write_text("X = 1\n")
        py_compile.compile(str(outside), doraise=True, invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH)
        modules = {"pkg.shipped": self.module(self.shipped, True), "pkg.early": self.module(self.edited, False),
                   "pkg.plain": self.module(self.plain, True), "outside": self.module(outside, True),
                   "builtin": SimpleNamespace()}
        audit = cold_bytecode.audit(None, modules=modules, app_root=str(self.app))
        self.assertEqual(audit["loaded_shipped"], ["pkg.shipped"])


if __name__ == "__main__":
    unittest.main()
