# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The guard's cost-bisect switch (CMake ``ANGELO_GUARD_DIAG_NO_INSTALL``) builds the app with no agent-script
confinement at all: it must never be set by a build configuration, preset or release script in the repository, only
by hand on a diagnostic build. Such a build fails closed (guard piece 7b, 2026-11-16): it refuses every agent script,
a release configure refuses the switch, and every packaging step refuses a binary that carries its marker. Runs
outside Blender."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]
NAME = "ANGELO_GUARD_DIAG_NO_INSTALL"
# Where it may be named: its definition, its uses in C and Python, the packaging steps that refuse it, and this test.
ALLOWED = {"source/blender/python/intern/CMakeLists.txt", "source/blender/python/intern/bpy_angelo_guard_confine.cc",
           "source/blender/python/intern/bpy_angelo_guard_confine_run.cc",
           "scripts/addons_core/angelo_agent/script_confinement_blender.py",
           "scripts/angelo/builder_paths.py", "build_files/package_spec/build_archive.py",
           "tests/python/angelo_agent/test_native_guard_diag_define.py",
           "tests/python/angelo_agent/test_blender_guard_diag.py"}
INTERN = ROOT / "source/blender/python/intern"
sys.path.insert(0, str(ROOT / "scripts/angelo"))
import builder_paths  # noqa: E402  (standard library only)
MARKER = builder_paths.DIAGNOSTIC_MARKER
CONFIG_SUFFIXES = (".cmake", ".txt", ".json", ".py", ".sh", ".bat", ".ps1", ".yml", ".yaml", ".toml", ".cfg", ".ini",
                   ".zsh", "Makefile", "GNUmakefile")


class DiagDefineTests(unittest.TestCase):
    def test_no_configuration_sets_it(self):
        found = []
        for folder in ("build_files", "release", "scripts", "source", "tests", "tools", ".github"):
            base = ROOT / folder
            if not base.is_dir():
                continue
            for path in base.rglob("*"):
                if not path.is_file() or not path.name.endswith(CONFIG_SUFFIXES):
                    continue
                relative = path.relative_to(ROOT).as_posix()
                if relative in ALLOWED:
                    continue
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if NAME in text:
                    found.append(relative)
        for name in ("GNUmakefile", "make.bat", "CMakeLists.txt"):
            if (ROOT / name).is_file() and NAME in (ROOT / name).read_text(encoding="utf-8", errors="ignore"):
                found.append(name)
        self.assertEqual(found, [], "the diagnostic switch is set outside its definition")

    def test_off_by_default(self):
        text = (ROOT / "source/blender/python/intern/CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"option\(\s*%s[^)]*\bON\b" % NAME, text))
        self.assertIsNone(re.search(r"set\(\s*%s\s+ON" % NAME, text))


def _diag_block():
    """The CMake block that handles the switch, as CMakeLists.txt holds it."""
    text = (INTERN / "CMakeLists.txt").read_text(encoding="utf-8")
    found = re.search(r"^if\(%s\)\n.*?^endif\(\)" % NAME, text, re.S | re.M)
    return found.group(0) if found else ""


class FailsClosedTests(unittest.TestCase):
    def test_every_agent_script_is_refused_under_it(self):
        text = (INTERN / "bpy_angelo_guard_confine_run.cc").read_text(encoding="utf-8")
        body = text[text.index("PyObject *guard_run_confined("):]
        body = body[body.index("{") + 1:]
        first = body.lstrip()
        self.assertTrue(first.startswith("#ifdef %s" % NAME), "run_confined must refuse before anything else")
        block = first[:first.index("#endif")]
        self.assertIn("return nullptr;", block)
        self.assertIn("DIAG_REFUSAL", block)

    def test_the_marker_is_embedded_only_under_it(self):
        found = []
        for path in sorted(INTERN.glob("*.cc")):
            text = path.read_text(encoding="utf-8", errors="ignore")
            for match in re.finditer(re.escape(MARKER), text):
                before = text[:match.start()]
                opened = before.rfind("#ifdef %s" % NAME)
                closed = before.rfind("#endif")
                found.append((path.name, opened != -1 and opened > closed))
        self.assertTrue(found, "the diagnostic build embeds its marker")
        self.assertTrue(all(inside for _name, inside in found), found)
        refusal = (INTERN / "bpy_angelo_guard_confine_run.cc").read_text(encoding="utf-8")
        self.assertRegex(refusal, r"static const char DIAG_REFUSAL\[\] =[^;]*%s" % re.escape(MARKER))

    @unittest.skipUnless(shutil.which("cmake"), "cmake is not installed")
    def test_a_release_configure_refuses_it(self):
        block = _diag_block()
        self.assertIn("FATAL_ERROR", block)
        with tempfile.TemporaryDirectory() as folder:
            script = pathlib.Path(folder, "diag.cmake")
            script.write_text(block.replace("add_definitions(", "message(STATUS ") + "\n", encoding="utf-8")

            def configure(*defines):
                return subprocess.run(["cmake", *("-D" + d for d in defines), "-P", str(script)],
                                      capture_output=True, text=True).returncode

            self.assertNotEqual(configure(NAME + "=ON", "ANGELO_RELEASE_CONFIGURATION=ON"), 0)
            self.assertNotEqual(configure(NAME + "=ON", "ANGELO_REQUIRE_AGENT_RUNTIME=ON"), 0)
            self.assertEqual(configure(NAME + "=ON"), 0)                       # A diagnostic build folder.
            self.assertEqual(configure("ANGELO_RELEASE_CONFIGURATION=ON"), 0)  # A release without it.
        release = (ROOT / "build_files/cmake/config/blender_release.cmake").read_text(encoding="utf-8")
        self.assertRegex(release, r'set\(ANGELO_RELEASE_CONFIGURATION ON CACHE BOOL "[^"]*" FORCE\)')

    def test_packaging_refuses_a_binary_with_its_marker(self):
        with tempfile.TemporaryDirectory() as folder:
            install = pathlib.Path(folder, "install")
            (install / "bin").mkdir(parents=True)
            payload = b"\0" * ((1 << 20) - 10) + MARKER.encode() + b"\0" * 64   # Across a read block's edge.
            (install / "bin" / "angelo").write_bytes(payload)
            (install / "readme.txt").write_text("clean")
            self.assertEqual(builder_paths.diagnostic_files(install), ["bin/angelo"])
            allow = [(["*"], re.compile(".*"), "everything")]   # Even an allow list that admits all admits it not.
            report = builder_paths.scan(install, identity=[], allow=allow, folders=[])
            self.assertIn(builder_paths.DIAGNOSTIC_KIND, report["hits"].get("bin/angelo", {}))
            archive = subprocess.run([sys.executable, str(ROOT / "build_files/package_spec/build_archive.py"),
                                      "angelo-test", "tar.xz", str(install), os.path.join(folder, "out")],
                                     capture_output=True, text=True, cwd=folder)
            self.assertEqual(archive.returncode, 1, archive.stderr)
            self.assertIn(NAME, archive.stderr)
            self.assertFalse(pathlib.Path(folder, "out", "angelo-test.tar.xz").exists())
        for script in ("scripts/angelo/build_release_macos.sh", "scripts/angelo/build_release_windows.ps1"):
            self.assertIn("builder-paths", (ROOT / script).read_text(encoding="utf-8"), script)

    def test_this_test_never_holds_the_marker(self):
        for path in (pathlib.Path(__file__), ROOT / "scripts/angelo/builder_paths.py"):
            self.assertNotIn(MARKER, path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
