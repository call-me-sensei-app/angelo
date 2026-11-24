# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The macOS/Windows-only features' Linux answers (doc/angelo/engine_link.rst, "Engine detection"; doc/angelo/
library.rst, Fab).

Covered: Fab's sign-in browser is a registered platform feature (macOS and Windows), so on Linux Fab says it is not
available on this platform before any click and nothing of the browser is imported or started, while macOS and
Windows keep the connector's own state; engine detection reads Linux's process table (``/proc/<pid>/comm``, this
user's processes only, a vanished process skipped) and finds Godot, Unity and Unreal where Linux installs put them.
No Blender.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import team_library_fixtures  # noqa: F401,E402  The add-on's stand-in modules, as the other Fab host tests use.
from angelo_agent import engine_detect, fab_host, fab_ui, platform_features  # noqa: E402
from host_test_policy import environment_skip  # noqa: E402


class FabTests(unittest.TestCase):
    def test_fab_is_registered_for_macos_and_windows_only(self):
        self.assertTrue(platform_features.supported("fab_browser", "darwin"))
        self.assertTrue(platform_features.supported("fab_browser", "win32"))
        self.assertFalse(platform_features.supported("fab_browser", "linux"))

    def test_linux_says_why_before_any_click_and_starts_nothing(self):
        tripwire = types.ModuleType("angelo_agent.fab_browser_connection")
        tripwire.__getattr__ = lambda name: self.fail("Fab's browser owner was reached on Linux: " + name)
        with mock.patch.object(fab_host, "_host", None), mock.patch.object(sys, "platform", "linux"), \
                mock.patch.dict(sys.modules, {"angelo_agent.fab_browser_connection": tripwire}):
            self.assertFalse(fab_host.register())
            view = fab_host.snapshot()
        self.assertEqual((view["status"], view["can_connect"], view["reason_code"]),
                         ("unavailable", False, "PLATFORM_UNAVAILABLE"))
        self.assertEqual(fab_ui.REASONS[view["reason_code"]], "Fab is not available on this platform.")
        with mock.patch.object(fab_host, "_host", None), mock.patch.object(sys, "platform", "darwin"):
            self.assertEqual(fab_host.snapshot()["reason_code"], "LIVE_CONNECTOR_UNAVAILABLE",
                             "where Fab is built, the connector's own state")


class EngineTests(unittest.TestCase):
    def proc(self, rows):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        for pid, name in rows:
            (Path(folder.name) / str(pid)).mkdir()
            if name is not None:
                (Path(folder.name) / str(pid) / "comm").write_bytes(name.encode() + b"\n")
        (Path(folder.name) / "self").mkdir()
        return folder.name

    def test_linux_reads_this_users_process_names(self):
        proc = self.proc([(10, "UnrealEditor"), (11, "Godot_v4.3-stab"), (12, "bash"), (13, None)])
        names = engine_detect._linux_names(proc)
        self.assertEqual(sorted(names), ["Godot_v4.3-stab", "UnrealEditor", "bash"], "a vanished process is skipped")
        self.assertEqual(engine_detect.running(names), {"godot", "unreal"})
        self.assertEqual(engine_detect._linux_names(proc, lambda path, info: False), [], "only this user's processes")
        with mock.patch.object(engine_detect, "_linux_names", return_value=["Unity"]):
            self.assertEqual(engine_detect.running(platform="linux"), {"unity"})
        with mock.patch.object(engine_detect, "_linux_names", side_effect=OSError("no /proc")):
            self.assertEqual(engine_detect.running(platform="linux"), frozenset())

    def test_linux_install_places(self):
        def among(*present):
            return lambda pattern: pattern in present
        home = {"HOME": "/home/a"}
        self.assertEqual(engine_detect.installed("linux", home, among(
            "/home/a/Unity/Hub/Editor/*/Editor/Unity")), {"unity"})
        self.assertEqual(engine_detect.installed("linux", home, among(
            "/home/a/Linux_Unreal_Engine_*/Engine/Binaries/Linux/UnrealEditor",
            "/var/lib/flatpak/app/org.godotengine.Godot")), {"unreal", "godot"})
        self.assertEqual(engine_detect.installed("linux", home, among()), frozenset())
        seen = []
        engine_detect.installed("linux", {}, lambda pattern: seen.append(pattern) or False)
        self.assertTrue(seen and not any(pattern.startswith("~") for pattern in seen),
                        "without HOME only the system places are looked at")

    @unittest.skipUnless(sys.platform.startswith("linux"), environment_skip("a real /proc (Linux)"))
    def test_the_real_proc_lists_this_process(self):
        self.assertTrue(engine_detect.process_names("linux"))


if __name__ == "__main__":
    unittest.main()
