# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo in the 3D view's corner while the chat is hidden (``chat_launcher``), without Blender.

Evidence: the maintainer (2026-10-21) asked for Angelo in the bottom corner whenever the chat is closed, about the
size of the marble, opening the chat on a click. Covered here: where the disc goes (the chat's side, clear of the
toolbar, sidebar and a bottom shelf, never outside a small view), the blink, the hit test, and the wiring: a passive
input that never makes a tool or a dock wait, the toggle's own translated label as the tooltip, Reduce Motion, a
cost guard for the blink, and no launcher in automation runs. ``test_blender_chat_launcher`` proves it in the app.
"""

import ast
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))


def load():
    bpy = types.ModuleType("bpy")
    bpy.types = SimpleNamespace(Operator=object)
    handlers = types.ModuleType("bpy.app.handlers")
    handlers.persistent = lambda function: function
    bpy.app = SimpleNamespace(handlers=handlers)
    with mock.patch.dict(sys.modules, {"bpy": bpy, "bpy.types": bpy.types, "bpy.app": bpy.app,
                                       "bpy.app.handlers": handlers}):
        sys.modules.pop("angelo_agent.chat_launcher", None)
        import angelo_agent.chat_launcher as module
    return module


launcher = load()
SOURCE = (ADDON / "chat_launcher.py").read_text()


def region(kind, x, y, width, height):
    return SimpleNamespace(type=kind, x=x, y=y, width=width, height=height)


class CornerTests(unittest.TestCase):
    def test_bottom_right_by_default_clear_of_the_edges(self):
        cx, cy, radius = launcher.corner((1200, 800), (0, 0, 0), 1.0, "RIGHT")
        self.assertEqual(radius, launcher.DISC / 2)
        self.assertEqual((cx, cy), (1200 - launcher.MARGIN - radius, launcher.MARGIN + radius))

    def test_the_chats_side(self):
        cx, _, radius = launcher.corner((1200, 800), (0, 0, 0), 1.0, "LEFT")
        self.assertEqual(cx, launcher.MARGIN + radius)

    def test_scales_with_the_interface(self):
        one, two = launcher.corner((2400, 1600), (0, 0, 0), 1.0, "RIGHT"), launcher.corner((2400, 1600), (0, 0, 0), 2.0, "RIGHT")
        self.assertEqual(two[2], 2 * one[2])
        self.assertEqual(2400 - two[0], 2 * (2400 - one[0]))

    def test_clear_of_overlapping_regions(self):
        cx, cy, radius = launcher.corner((1200, 800), (40, 300, 120), 1.0, "RIGHT")
        self.assertLessEqual(cx + radius, 1200 - 300 - launcher.MARGIN + 1e-9, "left of the sidebar")
        self.assertGreaterEqual(cy - radius, 120 + launcher.MARGIN - 1e-9, "above the asset shelf")
        cx, _, radius = launcher.corner((1200, 800), (40, 300, 120), 1.0, "LEFT")
        self.assertGreaterEqual(cx - radius, 40 + launcher.MARGIN - 1e-9, "right of the toolbar")

    def test_never_outside_a_small_view(self):
        cx, cy, radius = launcher.corner((60, 50), (40, 40, 40), 1.0, "RIGHT")
        self.assertGreaterEqual(cx - radius, -1e-9)
        self.assertGreaterEqual(cy - radius, -1e-9)


class InsetTests(unittest.TestCase):
    def test_only_regions_drawn_over_the_view_count(self):
        view = region('WINDOW', 100, 50, 1000, 700)
        regions = [view,
                   region('TOOLS', 100, 50, 44, 700),            # Toolbar over the view's left edge.
                   region('UI', 1100 - 260, 50, 260, 700),       # Sidebar over the right edge.
                   region('HEADER', 100, 726, 1000, 24),         # The top header: not at the bottom.
                   region('ASSET_SHELF', 100, 50, 1000, 90),     # A bottom shelf (sculpt, paint modes).
                   region('UI', 1200, 50, 200, 700)]             # Beside the view, not over it.
        self.assertEqual(launcher.insets(regions, view), (44, 260, 90))

    def test_hidden_regions_count_for_nothing(self):
        view = region('WINDOW', 0, 0, 800, 600)
        self.assertEqual(launcher.insets([view, region('UI', 799, 0, 1, 600)], view), (0, 0, 0))


class BlinkAndHitTests(unittest.TestCase):
    def test_a_blink_closes_then_opens(self):
        self.assertEqual(launcher.eye_open(10.0, None), 1.0)
        self.assertAlmostEqual(launcher.eye_open(10.0, 10.0), 1.0)
        self.assertAlmostEqual(launcher.eye_open(10.0 + launcher.BLINK_SECONDS / 2, 10.0), 0.1)
        self.assertEqual(launcher.eye_open(10.0 + launcher.BLINK_SECONDS, 10.0), 1.0)
        self.assertEqual(launcher.eye_open(9.0, 10.0), 1.0)

    def test_the_disc_is_the_hit_area(self):
        place = (1, 500.0, 300.0, 38.0)
        self.assertTrue(launcher.inside(place, 500, 300))
        self.assertTrue(launcher.inside(place, 538, 300))
        self.assertFalse(launcher.inside(place, 530, 330), "the square's corner is the scene's")


class WiringTests(unittest.TestCase):
    def test_marble_size_and_art(self):
        from angelo_agent import marble_wait
        self.assertEqual(launcher.MARK, marble_wait.HERO_COMPACT)
        self.assertIn("marble_draw.draw_still(cx, cy, MARK * scale, pose)", SOURCE)
        draw = (ADDON / "marble_draw.py").read_text().split("\ndef draw_still(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("_raven(pose)", draw, "the marble's own cached batches")

    def test_a_passive_input_that_never_makes_anything_wait(self):
        from angelo_agent import input_policy
        self.assertIn(launcher.IDENTITY, input_policy.CANVAS_INPUTS)
        self.assertNotIn(launcher.IDENTITY, input_policy.PASSIVE_OVERLAYS | input_policy.HOLDING_TOOLS)
        modal = SOURCE.split("    def modal(self, context, event):", 1)[1]
        self.assertIn("input_policy.canvas_blocking(window)", modal, "a real tool keeps every event")
        self.assertIn("input_policy.overlay_passes(event, context.window_manager)", modal)
        self.assertIn("_open(window)", modal)

    def test_the_click_acts_after_its_event(self):
        opener = SOURCE.split("\ndef _open(", 1)[1].split("\n# --", 1)[0]
        self.assertIn("bpy.app.timers.register(run", opener)
        self.assertIn("bpy.ops.angelo.toggle_chat()", opener)

    def test_a_failing_raven_stays_local(self):
        draw = SOURCE.split("\ndef _draw(", 1)[1].split("\n# --", 1)[0]
        self.assertIn("except Exception as error:", draw)
        self.assertNotIn("_failed(", SOURCE, "marble_draw._failed would stop the chat's wait animation")
        still = (ADDON / "marble_draw.py").read_text().split("\ndef draw_still(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("if _shader is None:", still, "the first launcher draw may come before any chat wait")

    def test_never_beside_the_chat_or_during_a_layout_change(self):
        target = SOURCE.split("\ndef target(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("moving()", target)
        draw = SOURCE.split("\ndef _draw(", 1)[1].split("\n# --", 1)[0]
        self.assertIn("moving()", draw)
        self.assertIn('chat_toggle.chat_state(window, bpy.context.window_manager.windows)[0] != "hidden"', draw)

    def test_tooltip_is_the_toggles_translated_label(self):
        self.assertIn("chat_toggle.button_text()", SOURCE.split("\ndef _draw(", 1)[1])

    def test_motion_is_guarded(self):
        idle = SOURCE.split("\ndef _idle(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("marble_host.reduced()", idle, "Reduce Motion: Blender's or the system's")
        self.assertIn("MAX_VIEW_COST", idle, "a blink redraws the whole view: only cheap views")
        self.assertNotIn("marble_host.reduced", SOURCE.split("\ndef _draw(", 1)[1], "never asked in a draw")

    def test_off_in_automation_unless_its_suite_asks(self):
        enabled = SOURCE.split("\ndef enabled(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("app_notices.automation_off()", enabled)
        self.assertIn('product_env.flag("ENABLE_CHAT_LAUNCHER")', enabled)

    def test_follows_the_chat_however_it_changes(self):
        toggle = (ADDON / "chat_toggle.py").read_text()
        self.assertGreaterEqual(toggle.count("chat_launcher.update()"), 3, "the watch, the toggle and a follow")
        init = (ADDON / "__init__.py").read_text()
        self.assertIn("chat_launcher.register()", init)
        self.assertIn("chat_launcher.unregister()", init)

    def test_no_function_rebinds_bpy(self):
        for node in ast.walk(ast.parse(SOURCE)):
            if isinstance(node, ast.FunctionDef):
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Import):
                        self.assertFalse(any(alias.name.split(".")[0] == "bpy" for alias in inner.names), node.name)


if __name__ == "__main__":
    unittest.main()
