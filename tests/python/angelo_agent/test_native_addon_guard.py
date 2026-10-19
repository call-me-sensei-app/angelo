# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""run_native_suites refuses an app whose bundled add-on is not this checkout's (native_addon_guard).

Native suites test the add-on bundled in the app, never the checkout's: run against an app built before the
change under test, a suite reports another add-on's result as this checkout's.
"""

import contextlib
import io
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import native_addon_guard as guard  # noqa: E402
import run_native_suites as runner  # noqa: E402

ADDON = {"__init__.py": "VERSION = 1\n", "script_kit.py": "def row(name, unit, gap):\n    pass\n",
         "assets/release_notes.json": "{}\n"}
STARTUP = {"angelo_default_layout.py": "LAYOUT = 1\n", "bl_ui/space_topbar.py": "TOPBAR = 1\n"}


# What native_addon_guard.check returns for a matching app: harness tests whose fixture executable bundles no
# add-on patch check with it (the guard's own tests are below; bench_addon_pin's in test_native_bench_addon_pin).
MATCHING = {"matches": True, "differing": [], "source": {"folder": "checkout", "digest": "0" * 64, "files": 1},
            "app": {"folder": "app", "binary": "Angelo", "digest": "0" * 64, "files": 1}}


def matching_app(test):
    """Patch ``native_addon_guard.check`` for ``test`` (a TestCase) to report a matching app."""
    from unittest import mock
    patcher = mock.patch.object(guard, "check", return_value=MATCHING)
    patcher.start()
    test.addCleanup(patcher.stop)


def write(root, files):
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


class Fixture(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.source = self.root / "checkout"
        write(self.source / "scripts/addons_core/angelo_agent", ADDON)
        write(self.source / "scripts/startup", STARTUP)

    def app(self, layout="mac", addon=ADDON, startup=STARTUP, version="5.2"):
        """A fake installed app holding ``addon`` and ``startup``; its executable's path."""
        if layout == "mac":
            contents = self.root / ("Angelo-%s.app" % len(list(self.root.iterdir()))) / "Contents"
            binary, scripts = contents / "MacOS/Angelo", contents / "Resources" / version / "scripts"
        else:
            install = self.root / ("install-%s" % len(list(self.root.iterdir())))
            binary, scripts = install / "Angelo", install / version / "scripts"
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_text("#!/bin/sh\nexit 0\n")
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
        if addon is not None:
            write(scripts / "addons_core/angelo_agent", addon)
        write(scripts / "startup", startup)
        return binary


class CheckTests(Fixture):
    def test_the_same_files_match_on_either_layout(self):
        for layout in ("mac", "install"):
            result = guard.check(self.app(layout), self.source)
            self.assertTrue(result["matches"], result["differing"])
            self.assertEqual(result["source"]["digest"], result["app"]["digest"])

    def test_bytecode_caches_and_finder_files_are_ignored(self):
        binary = self.app()
        scripts = guard.bundled_scripts(binary)
        write(scripts / "addons_core/angelo_agent", {"__pycache__/script_kit.cpython-313.pyc": "x", ".DS_Store": "x",
                                                      "old.pyc": "x"})
        self.assertTrue(guard.check(binary, self.source)["matches"])

    def test_a_changed_module_an_app_only_module_and_a_missing_one_each_differ(self):
        stale = dict(ADDON, **{"script_kit.py": "def row(name, unit=(1, 0.6, 1.2), gap=0.8):\n    pass\n",
                               "job_replace.py": "OLD = 1\n"})
        del stale["__init__.py"]
        result = guard.check(self.app(addon=stale), self.source)
        self.assertFalse(result["matches"])
        self.assertEqual(result["differing"], ["addon/__init__.py: only in the checkout",
                                               "addon/job_replace.py: only in the app", "addon/script_kit.py: differs"])
        self.assertNotEqual(result["source"]["digest"], result["app"]["digest"])

    def test_startup_modules_are_compared_one_way(self):
        changed = guard.check(self.app(startup=dict(STARTUP, **{"bl_ui/space_topbar.py": "TOPBAR = 2\n"})), self.source)
        self.assertEqual(changed["differing"], ["startup/bl_ui/space_topbar.py: differs"])
        # The build adds Blender's own startup modules beside the checkout's: those are not compared.
        extra = guard.check(self.app(startup=dict(STARTUP, **{"bl_operators/wm.py": "WM = 1\n"})), self.source)
        self.assertTrue(extra["matches"], extra["differing"])

    def test_a_git_lfs_pointer_counts_as_the_file_it_points_to(self):
        # Every Angelo checkout is made with GIT_LFS_SKIP_SMUDGE=1: its binary assets are pointers, the app's real.
        import hashlib
        image = bytes(range(256)) * 8
        binary = self.app(addon=dict(ADDON, **{"assets/banner.png": ""}))
        (guard.bundled_scripts(binary) / "addons_core/angelo_agent/assets/banner.png").write_bytes(image)
        pointer = ("version https://git-lfs.github.com/spec/v1\noid sha256:%s\nsize %d\n"
                   % (hashlib.sha256(image).hexdigest(), len(image)))
        write(self.source / "scripts/addons_core/angelo_agent", {"assets/banner.png": pointer})
        self.assertTrue(guard.check(binary, self.source)["matches"])
        write(self.source / "scripts/addons_core/angelo_agent", {"assets/banner.png": pointer.replace(
            hashlib.sha256(image).hexdigest(), "0" * 64)})
        self.assertEqual(guard.check(binary, self.source)["differing"], ["addon/assets/banner.png: differs"])

    def test_a_binary_without_a_bundled_add_on_never_matches(self):
        result = guard.check(self.app(addon=None), self.source)
        self.assertFalse(result["matches"])
        self.assertIsNone(result["app"]["folder"])


class RunnerTests(Fixture):
    def run_main(self, binary, *extra):
        output, launch = io.StringIO(), Mock()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = runner.main(["--binary", str(binary), "--output", str(self.root / "out")] + list(extra),
                               suites=(), base_env={}, launch=launch,
                               addon_check=lambda binary, root: guard.check(binary, self.source))
        return code, output.getvalue(), launch

    def test_a_mismatch_is_refused_before_anything_runs_naming_both_sides(self):
        binary = self.app(addon=dict(ADDON, **{"script_kit.py": "STALE = 1\n"}))
        code, text, launch = self.run_main(binary)
        self.assertEqual(code, 2)
        launch.assert_not_called()
        self.assertFalse((self.root / "out").exists(), "No run folder: nothing ran")
        self.assertIn("Refused", text)
        self.assertIn(str(guard.bundled_scripts(binary)), text)
        self.assertIn(str(self.source), text)
        self.assertIn("addon/script_kit.py: differs", text)
        self.assertIn(guard.OVERRIDE, text)

    def test_the_override_runs_with_a_warning_line(self):
        binary = self.app(addon=dict(ADDON, **{"script_kit.py": "STALE = 1\n"}))
        code, text, _launch = self.run_main(binary, guard.OVERRIDE)
        self.assertTrue(text.startswith("WARNING: %s:" % guard.OVERRIDE), text)
        self.assertIn("No suites selected", text)   # It went on past the guard (this fixture has no suite).
        self.assertEqual(code, 2)

    def test_a_matching_app_runs_with_no_warning(self):
        code, text, _launch = self.run_main(self.app())
        self.assertNotIn("WARNING", text)
        self.assertNotIn("Refused", text)
        self.assertIn("No suites selected", text)

    def test_the_runner_checks_by_default_and_records_the_comparison(self):
        import inspect
        self.assertIs(inspect.signature(runner.main).parameters["addon_check"].default, guard.check)
        self.assertIn('"bundled_addon": bundled', inspect.getsource(runner.main))


if __name__ == "__main__":
    unittest.main()
