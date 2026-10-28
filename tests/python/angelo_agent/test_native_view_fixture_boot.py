# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""GUI-test-only startup wrapper and its host tests; benchmark harness helpers remain unchanged."""

import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


def drive(scenario, state, report, marker, interval=0.1):
    """Run a GUI fixture after the common boot gate, without changing benchmark-hashed shared helpers.

    Only startup is gated: later changes made by a scenario stay visible to its assertions. Background and
    component tests that did not enable Angelo have no startup tab builder to await.
    """
    import rendered_view_fixtures

    def after_boot():
        import addon_utils
        import bpy
        import qa_boot
        import time
        since = None
        if not bpy.app.background and addon_utils.check("angelo_agent")[1]:
            while True:
                waiting = state["boot_waiting"] = qa_boot.waiting_on()
                if not waiting:
                    break
                if since is None:
                    since = time.monotonic()
                if time.monotonic() - since >= 60:
                    raise RuntimeError("View fixture startup did not settle: " + "; ".join(waiting))
                yield
        yield from scenario()

    rendered_view_fixtures.drive(after_boot, state, report, marker, interval)


class DriverTests(unittest.TestCase):
    def setUp(self):
        self.callbacks, self.actions, self.quits = [], [], []
        self.reasons = ["Manual's tab build running"]
        self.enabled, self.background, self.now = True, False, 0.0
        self.bpy = SimpleNamespace(app=SimpleNamespace(background=False, timers=SimpleNamespace(
            register=lambda callback, **kwargs: self.callbacks.append(callback))),
            ops=SimpleNamespace(wm=SimpleNamespace(quit_blender=lambda: self.quits.append(True))))
        self.modules = patch.dict(sys.modules, {
            "bpy": self.bpy,
            "addon_utils": SimpleNamespace(check=lambda name: (False, self.enabled)),
            "qa_boot": SimpleNamespace(waiting_on=lambda: list(self.reasons)),
        })
        self.modules.start()
        self.addCleanup(self.modules.stop)
        clock = patch("time.monotonic", lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        path = Path(__file__).with_name("rendered_view_fixtures.py")
        spec = importlib.util.spec_from_file_location("_view_fixtures_boot_test", path)
        self.fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.fixture)
        shared = patch.dict(sys.modules, {"rendered_view_fixtures": self.fixture})
        shared.start()
        self.addCleanup(shared.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.report = Path(folder.name) / "report.json"
        self.state = {"phase": "boot", "success": False, "checks": {}}

    def start(self, scenario):
        drive(scenario, self.state, self.report, "BOOT_TEST")
        return self.callbacks[0]

    def test_no_fixture_step_sees_a_temporary_non_viewport_screen(self):
        def scenario():
            self.actions.append("snapshot")
            # The real failure: view_area() uses next(VIEW_3D) while Manual's new tab is on screen.
            next(area for area in ([] if self.reasons else ["VIEW_3D"]))
            self.state["checks"]["snapshot"] = True
            yield
        tick = self.start(scenario)
        self.assertEqual(tick(), 0.1)
        self.assertEqual(self.actions, [])
        self.assertEqual(self.state["boot_waiting"], self.reasons)
        self.reasons[:] = ["Manual's tab build holding the layout"]
        self.assertEqual(tick(), 0.1)
        self.assertEqual(self.actions, [])
        self.reasons.clear()
        self.assertEqual(tick(), 0.1)
        self.assertEqual(self.actions, ["snapshot"])
        self.assertIsNone(tick())
        self.assertTrue(self.state["success"])

    def test_ready_boot_adds_no_fixed_delay_and_checks_remain_strict(self):
        self.reasons.clear()

        def scenario():
            self.actions.append("checked")
            self.state["phase"] = "pixels"
            self.state["checks"]["actual_pixels"] = False
            raise AssertionError("actual_pixels")
            yield
        tick = self.start(scenario)
        self.assertIsNone(tick())
        self.assertEqual(self.actions, ["checked"])
        self.assertFalse(self.state["success"])
        self.assertEqual(self.state["failed_phase"], "pixels")
        self.assertIn("actual_pixels", self.state["error"])

    def test_stuck_boot_fails_with_reason_before_any_fixture_mutation(self):
        def scenario():
            self.actions.append("mutation")
            yield
        tick = self.start(scenario)
        self.assertEqual(tick(), 0.1)
        self.now = 61.0
        self.assertIsNone(tick())
        self.assertEqual(self.actions, [])
        self.assertFalse(self.state["success"])
        self.assertIn("Manual's tab build running", self.state["error"])

    def test_a_component_without_the_addon_does_not_wait_for_unregistered_tabs(self):
        self.enabled = False

        def scenario():
            self.actions.append("component")
            self.state["checks"]["component"] = True
            yield
        tick = self.start(scenario)
        self.assertEqual(tick(), 0.1)
        self.assertEqual(self.actions, ["component"])
        self.assertIsNone(tick())
        self.assertTrue(self.state["success"])

    def test_boot_gate_does_not_hide_layout_changes_caused_by_a_test_step(self):
        self.reasons.clear()

        def scenario():
            self.actions.append("first")
            yield
            self.actions.append("second")
            self.state["checks"]["both_steps"] = True
        tick = self.start(scenario)
        self.assertEqual(tick(), 0.1)
        self.reasons.append("a layout change caused by this test")
        self.assertIsNone(tick())
        self.assertEqual(self.actions, ["first", "second"])

    def test_every_shared_driver_caller_uses_the_gui_only_startup_wrapper(self):
        import ast
        root = Path(__file__).parent
        callers = []
        for path in root.glob("test_blender_*.py"):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            aliases = {alias.asname or alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                       for alias in node.names if alias.name == "rendered_view_fixtures"}
            if not aliases:
                continue
            calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Attribute) and node.func.attr == "drive"
                     and isinstance(node.func.value, ast.Name)]
            for call in calls:
                self.assertNotIn(call.func.value.id, aliases, path.name + " bypasses the startup gate")
                if call.func.value.id == "fixture_boot":
                    callers.append(path.name)
        self.assertEqual(len(callers), 7)


if __name__ == "__main__":
    unittest.main()
