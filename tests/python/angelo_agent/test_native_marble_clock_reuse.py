# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""marble_frames.reuse with the status line's clock counter: a kept picture without a key is drawn again, never a crash.

The signed 1.0.4 build raised in the chat's draw callback (``current[:-1] == entry["key"][:-1]`` with a kept picture
whose key was still None, test_blender_turn_recovery at Mac 1 baseline), so that frame was not drawn. Dependency-free:
``bpy``, ``ui`` and ``chat_view`` are stand-ins; ``key`` and ``_wait`` are patched.
"""

import sys
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))


class ReuseWithTheClock(unittest.TestCase):
    def setUp(self):
        added = "bpy" not in sys.modules
        if added:
            sys.modules["bpy"] = types.ModuleType("bpy")
            self.addCleanup(sys.modules.pop, "bpy", None)
        import angelo_agent
        from angelo_agent import marble_frames
        self.frames = marble_frames
        stubs = {"angelo_agent.ui": SimpleNamespace(_controller=object()),
                 "angelo_agent.chat_view": SimpleNamespace(_views={})}
        patcher = mock.patch.dict(sys.modules, stubs)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name, stub in (("ui", stubs["angelo_agent.ui"]), ("chat_view", stubs["angelo_agent.chat_view"])):
            attribute = mock.patch.object(angelo_agent, name, stub, create=True)
            attribute.start()
            self.addCleanup(attribute.stop)
        self.addCleanup(marble_frames._kept.clear)
        self.addCleanup(marble_frames._requested.clear)
        self.area, self.region = SimpleNamespace(as_pointer=lambda: 7), SimpleNamespace(width=10, height=10)

    def reuse(self, kept, current):
        frames = self.frames
        frames._requested.add(7)
        frames._kept[7] = {"offscreen": None, "size": (10, 10), "key": kept, "at": time.monotonic(), "places": []}
        before = frames.stats["clock"]
        with mock.patch.object(frames, "_wait", return_value=object()), \
                mock.patch.object(frames, "key", return_value=current):
            drawn = frames.reuse(self.area, self.region)
        return drawn, frames.stats["clock"] - before

    def test_a_kept_picture_without_a_key_is_drawn_again_not_a_crash(self):
        self.assertEqual(self.reuse(None, ("size", "version", "1m 9s · ")), (False, 0))
        self.assertEqual(self.reuse(None, None), (False, 0))

    def test_only_a_changed_status_line_counts_as_the_clocks_refresh(self):
        self.assertEqual(self.reuse(("size", "version", "1m 9s · "), ("size", "version", "1m 10s · ")), (False, 1))
        self.assertEqual(self.reuse(("size", "version", "1m 9s · "), ("size", "other", "1m 10s · ")), (False, 0))
        self.assertEqual(self.reuse(("size", "version", "1m 9s · "), None), (False, 0))


if __name__ == "__main__":
    unittest.main()
