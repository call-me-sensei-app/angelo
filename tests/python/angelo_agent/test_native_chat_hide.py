# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Hide undoes Show (``chat_hide``), without Blender.

Evidence: on shipped 1.0.4 each Show took the chat's width from the 3D view beside Properties and each Hide (Blender's
Close Area) gave it to Properties: the Layout tab's 3D view went 1881, 1286, 686, 356 px over three cycles (Mac 1
diagnostic, release-105's screenshot review, 2026-10-23). Covered: a chat Show widened that way runs the dock's own
inverse as a timed move and ends it like a dock; any other chat, and an inverse that fails, closes with Close Area.
"""

import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))


def load():
    bpy = types.ModuleType("bpy")
    bpy.types = SimpleNamespace(Operator=object)
    bpy.app = SimpleNamespace(timers=mock.Mock())
    bpy.context = SimpleNamespace(window_manager=SimpleNamespace(windows=[]), temp_override=mock.MagicMock())
    bpy.ops = SimpleNamespace(screen=SimpleNamespace(area_close=mock.Mock()))
    with mock.patch.dict(sys.modules, {"bpy": bpy}):
        for name in ("angelo_agent.chat_hide", "angelo_agent.docking", "angelo_agent.layout"):
            sys.modules.pop(name, None)
        import angelo_agent.chat_hide as module
    return module, bpy


def area(pointer=7):
    return SimpleNamespace(as_pointer=lambda: pointer, regions=[SimpleNamespace(type='WINDOW')])


def window(pointer=1):
    return SimpleNamespace(as_pointer=lambda: pointer)


class HideTests(unittest.TestCase):
    def setUp(self):
        self.hide, self.bpy = load()
        self.ui = SimpleNamespace(_dock_metadata={}, _layout_timer=None)
        self.launcher = SimpleNamespace(settled=mock.Mock())
        self.policy = SimpleNamespace(layout_blocking=lambda window: False)
        modules = {"angelo_agent.ui": self.ui, "angelo_agent.chat_launcher": self.launcher,
                   "angelo_agent.input_policy": self.policy}
        patcher = mock.patch.dict(sys.modules, modules)
        patcher.start()
        self.addCleanup(patcher.stop)
        import angelo_agent
        for name, module in (("ui", self.ui), ("chat_launcher", self.launcher), ("input_policy", self.policy)):
            original = getattr(angelo_agent, name, None)
            setattr(angelo_agent, name, module)
            self.addCleanup(setattr, angelo_agent, name, original) if original else self.addCleanup(delattr, angelo_agent, name)

    def test_a_chat_show_did_not_widen_closes_at_once(self):
        self.hide.request_hide(window(), area())
        self.bpy.ops.screen.area_close.assert_called_once_with()
        self.assertIsNone(self.ui._layout_timer)
        self.bpy.app.timers.register.assert_not_called()

    def run_inverse(self, steps):
        self.ui._dock_metadata[7] = True
        target = window()
        self.bpy.context.window_manager.windows = [target]
        with mock.patch.object(self.hide.docking, "collapse_chat", return_value=iter(steps)) as collapse:
            self.hide.request_hide(target, area())
            collapse.assert_called_once_with(target, 7, True)
            self.assertNotIn(7, self.ui._dock_metadata)
            advance = self.bpy.app.timers.register.call_args.args[0]
            self.assertIs(self.ui._layout_timer, advance, "a move: tools, docks and mode switches wait for it")
            intervals = []
            while (interval := advance()) is not None:
                intervals.append(interval)
        return intervals

    def test_a_widened_chat_gives_its_strip_back(self):
        self.assertEqual(self.run_inverse([0.05, 0.05]), [0.05, 0.05])
        self.bpy.ops.screen.area_close.assert_not_called()
        self.assertIsNone(self.ui._layout_timer)
        self.launcher.settled.assert_called_once_with(1)

    def test_an_inverse_that_no_longer_fits_still_hides(self):
        def steps():
            yield 0.05
            raise RuntimeError("The adjacent editor boundaries no longer match chat")
        chat = area()
        chat.as_pointer = lambda: 7
        target = window()
        target.screen = SimpleNamespace(areas=[chat])
        self.ui._dock_metadata[7] = True
        self.bpy.context.window_manager.windows = [target]
        with mock.patch.object(self.hide.docking, "collapse_chat", return_value=steps()), mock.patch("builtins.print"):
            self.hide.request_hide(target, chat)
            advance = self.bpy.app.timers.register.call_args.args[0]
            while advance() is not None:
                pass
        self.bpy.ops.screen.area_close.assert_called_once_with()
        self.assertIsNone(self.ui._layout_timer)
        self.launcher.settled.assert_called_once_with(1)

    def test_a_closed_window_ends_the_move(self):
        self.ui._dock_metadata[7] = True
        with mock.patch.object(self.hide.docking, "collapse_chat", return_value=iter([0.05])):
            self.hide.request_hide(window(), area())
            advance = self.bpy.app.timers.register.call_args.args[0]
            self.assertIsNone(advance())   # No window in the window manager any more.
        self.assertIsNone(self.ui._layout_timer)
        self.launcher.settled.assert_called_once_with(1)


    def presses(self, count):
        self.director = SimpleNamespace(_timer=None)
        sys.modules["angelo_agent.director"] = self.director
        import angelo_agent
        angelo_agent.director = self.director
        self.addCleanup(sys.modules.pop, "angelo_agent.director", None)
        target = window()
        self.bpy.context.window_manager.windows = [target]
        self.bpy.app.timers.is_registered = mock.Mock(return_value=False)
        self.bpy.ops.angelo = SimpleNamespace(toggle_chat=mock.Mock())
        self.ui._layout_timer = object()   # A move is under way.
        for _ in range(count):
            self.hide.after_move(target)
        apply = self.bpy.app.timers.register.call_args.args[0]
        self.assertEqual(apply(), 0.05, "waits for the move to end")
        self.bpy.ops.angelo.toggle_chat.assert_not_called()
        self.ui._layout_timer = None
        self.assertIsNone(apply())
        return self.bpy.ops.angelo.toggle_chat

    def test_a_press_during_a_move_is_applied_after_it(self):
        self.presses(1).assert_called_once_with()

    def test_two_quick_presses_leave_the_chat_as_it_was(self):
        self.presses(2).assert_not_called()

    def test_a_toggle_never_runs_inside_a_walk_over_the_windows(self):
        """A toggle can open or close a window; the 6ceaef365ff full line crashed in WindowManager_windows_next when
        the walk over the windows went on past one. Each pressed window is looked up afresh by pointer, and a window
        the first toggle closed is skipped."""
        self.director = SimpleNamespace(_timer=None)
        sys.modules["angelo_agent.director"] = self.director
        import angelo_agent
        angelo_agent.director = self.director
        self.addCleanup(sys.modules.pop, "angelo_agent.director", None)
        first, second = window(1), window(2)
        self.bpy.context.window_manager.windows = [first, second]
        self.bpy.app.timers.is_registered = mock.Mock(return_value=False)

        def toggle():   # The first toggle closes the second window.
            self.bpy.context.window_manager.windows = [first]
        self.bpy.ops.angelo = SimpleNamespace(toggle_chat=mock.Mock(side_effect=toggle))
        self.hide.after_move(first)
        self.hide.after_move(second)
        apply = self.bpy.app.timers.register.call_args.args[0]
        self.assertIsNone(apply())
        self.assertEqual(self.bpy.ops.angelo.toggle_chat.call_count, 1)
        source = (ROOT / "scripts/addons_core/angelo_agent/chat_hide.py").read_text()
        body = source.split("\ndef _apply_presses(", 1)[1].split("\ndef ", 1)[0]
        self.assertNotIn("for window in bpy.context.window_manager.windows", body)
        self.assertIn("for pointer in pending:", body)

    def test_the_toggle_defers_rather_than_refuses(self):
        toggle = (ROOT / "scripts/addons_core/angelo_agent/chat_toggle.py").read_text()
        execute = toggle.split("    def execute(", 1)[1].split("\ndef ", 1)[0]
        self.assertLess(execute.index("chat_hide.after_move(here)"), execute.index("show(here)"))


if __name__ == "__main__":
    unittest.main()
