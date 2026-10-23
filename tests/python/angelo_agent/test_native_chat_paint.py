# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A dock is done when the chat has drawn as the chat (``chat_paint``), without Blender.

Evidence: release-105's screenshot review (2026-10-23, row 3): the dock reported success in the tick its area became
the chat, before that area drew, and a capture then showed a second Properties editor where the chat was. Covered: the
move holds until the area's own draw after the dock, or ``WAIT`` seconds; then corner Angelo settles and
``on_complete`` runs, never for an area that is gone.
"""

import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))


class PaintTests(unittest.TestCase):
    def setUp(self):
        self.bpy = types.ModuleType("bpy")
        self.bpy.types = SimpleNamespace(SpaceConsole=SimpleNamespace(draw_handler_add=mock.Mock(return_value="h"),
                                                                    draw_handler_remove=mock.Mock()))
        self.bpy.app = SimpleNamespace(timers=mock.Mock())
        self.chat = SimpleNamespace(as_pointer=lambda: 7, tag_redraw=mock.Mock())
        self.window = SimpleNamespace(as_pointer=lambda: 1, screen=SimpleNamespace(areas=[self.chat]))
        self.bpy.context = SimpleNamespace(area=self.chat, window_manager=SimpleNamespace(windows=[self.window]))
        self.ui = SimpleNamespace(_layout_timer=None)
        self.launcher = SimpleNamespace(settled=mock.Mock())
        self.console = SimpleNamespace(is_chat=lambda area: True)
        modules = {"bpy": self.bpy, "angelo_agent.ui": self.ui, "angelo_agent.chat_launcher": self.launcher,
                   "angelo_agent.console": self.console}
        patcher = mock.patch.dict(sys.modules, modules)
        patcher.start()
        self.addCleanup(patcher.stop)
        sys.modules.pop("angelo_agent.chat_paint", None)
        import angelo_agent
        for name, module in (("ui", self.ui), ("chat_launcher", self.launcher), ("console", self.console)):
            original = getattr(angelo_agent, name, None)
            setattr(angelo_agent, name, module)
            self.addCleanup(setattr, angelo_agent, name, original) if original else self.addCleanup(delattr, angelo_agent, name)
        import angelo_agent.chat_paint as paint
        self.paint = paint
        self.addCleanup(sys.modules.pop, "angelo_agent.chat_paint", None)

    def start(self, on_complete):
        self.paint.finish_dock(self.window, self.chat, on_complete)
        check = self.bpy.app.timers.register.call_args.args[0]
        self.assertIs(self.ui._layout_timer, check, "the move holds: corner Angelo, tools and docks wait")
        self.chat.tag_redraw.assert_called_once_with()
        return check

    def test_reported_only_after_the_chat_drew(self):
        done = mock.Mock()
        check = self.start(done)
        self.assertEqual(check(), 0.02)
        done.assert_not_called()
        self.launcher.settled.assert_not_called()
        self.paint._record()   # The chat's own draw.
        self.assertIsNone(check())
        done.assert_called_once_with(self.window, self.chat)
        self.launcher.settled.assert_called_once_with(1)
        self.assertIsNone(self.ui._layout_timer)
        self.bpy.types.SpaceConsole.draw_handler_remove.assert_called_once_with("h", 'WINDOW')

    def test_a_chat_that_never_draws_is_reported_after_the_wait(self):
        done = mock.Mock()
        with mock.patch.object(self.paint.time, "monotonic", side_effect=[0.0, 0.5, 5.0]):
            check = self.start(done)
            self.assertEqual(check(), 0.02)
            self.assertIsNone(check())
        done.assert_called_once_with(self.window, self.chat)

    def test_no_follow_up_for_an_area_that_is_gone(self):
        done = mock.Mock()
        check = self.start(done)
        self.window.screen.areas = []
        self.paint._record()
        self.assertIsNone(check())
        done.assert_not_called()
        self.launcher.settled.assert_called_once_with(1)
        self.assertIsNone(self.ui._layout_timer)


if __name__ == "__main__":
    unittest.main()
