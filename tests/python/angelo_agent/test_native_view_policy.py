# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every view that sets settings for a moment follows the mode-aware rule for the add-on Python they run.

The rule: a viewport capture, a Render Result or render-pass
picture and a finished-image picture are refused in the Read-only and Ask
approval modes when their own temporary writes would run Python from add-ons
(depsgraph-update handlers when the writes update the depsgraph, message-bus
subscribers to the settings written); the Auto modes make them, name that
Python and never say the scene is unchanged. ``view_policy`` has the scope.
"""

import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rendered_view_case import FinishedCase  # noqa: E402  (puts the add-on on the path)
from test_native_view_capture import HANDLE, FakeOffScreen, environment  # noqa: E402
from angelo_agent import view_cache, view_policy  # noqa: E402
from angelo_agent.references import ReferenceStore  # noqa: E402


class CaptureCase(unittest.TestCase):
    """``view_capture`` over the stand-in viewport of ``test_native_view_capture``."""

    def setUp(self):
        self.env = environment()
        modules = patch.dict(sys.modules, {"bpy": self.env.bpy, "gpu": self.env.gpu, "mathutils": self.env.mathutils,
                                           "_bpy": self.env.native})
        modules.start()
        self.addCleanup(modules.stop)
        sys.modules.pop("angelo_agent.view_capture", None)
        self.capture = importlib.import_module("angelo_agent.view_capture")
        queries = patch.object(self.capture, "scene_queries", self.env.queries)
        queries.start()
        self.addCleanup(queries.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.references = ReferenceStore(directory.name, "4c0a43a6-8f2a-4b8e-9b1f-1b7b0d1b3c55")
        FakeOffScreen.draws, FakeOffScreen.fail = [], False
        view_cache.clear()
        self.addCleanup(view_cache.clear)


def addon_function(folder, name):
    """A function whose code lives outside the shipped scripts, as an add-on's does."""
    namespace = {"__name__": "community_addon"}
    exec(compile("def %s(*args):\n    return None\n" % name, str(Path(folder) / "community_addon.py"), "exec"),
         namespace)
    return namespace[name]


class Python:
    """A temporary folder for add-on code, and the approval mode of a stand-in conversation."""

    def plant(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.on_update = addon_function(temp.name, "on_update")
        self.on_message = addon_function(temp.name, "on_message")

    def mode(self, approval):
        return SimpleNamespace(state={"id": "chat", "options": {"approval": approval}})


class DisplayDirtiesTests(CaptureCase, Python):
    """Only two display writes make Blender update the depsgraph (rna_space.cc)."""

    def test_only_a_preview_type_change_with_a_sculpt_session_or_overlays_under_retopology_count(self):
        space, target = self.env.space, self.env.target
        object.__setattr__(space.shading, "type", "SOLID")
        space.overlay.show_overlays = False  # What every capture but WIREFRAME's sets: not written.
        self.assertFalse(view_policy.display_dirties(space, "MATERIAL"), "no sculpt session")
        for mode, dirties in (("SCULPT", True), ("WEIGHT_PAINT", True), ("VERTEX_PAINT", True), ("EDIT", False)):
            with self.subTest(mode=mode):
                target.mode = mode
                self.assertEqual(view_policy.display_dirties(space, "MATERIAL"), dirties)
                self.assertFalse(view_policy.display_dirties(space, "SOLID"), "the type stays SOLID")
        target.mode = "SCULPT"
        object.__setattr__(space.shading, "type", "MATERIAL")
        self.assertTrue(view_policy.display_dirties(space, "SOLID"), "putting Material Preview back counts too")
        self.assertFalse(view_policy.display_dirties(space, "LIT"), "LIT is the same type: not written")
        target.mode = "OBJECT"
        space.overlay.show_overlays, space.overlay.show_retopology = True, True
        self.assertTrue(view_policy.display_dirties(space, "SOLID"))
        space.overlay.show_retopology = False
        self.assertFalse(view_policy.display_dirties(space, "SOLID"))


class CapturePolicyTests(CaptureCase, Python):
    def setUp(self):
        super().setUp()
        self.plant()

    def capture_in(self, approval, **args):
        return self.capture.execute(self.env.window, dict({"targets": [HANDLE], "views": ["FRONT"]}, **args),
                                    self.references, self.mode(approval))

    def test_a_subscriber_to_a_display_setting_refuses_in_read_only_and_ask_and_is_named_in_auto(self):
        self.env.msgbus.append(("View3DShading", "light", self.on_message))
        before = self.env.space.shading.snapshot()
        for approval in ("READ_ONLY", "ASK"):
            with self.subTest(approval=approval), self.assertRaisesRegex(
                    RuntimeError, r"community_addon.on_message \(message bus: View3DShading.light\)"):
                self.capture_in(approval)
        self.assertEqual((FakeOffScreen.draws, self.env.space.shading.snapshot()), ([], before))
        result = self.capture_in("AUTO_BOUNDED")
        self.assertEqual(len(FakeOffScreen.draws), 1)
        self.assertIsNone(result["scene_changed"], "it runs after the view")
        self.assertIn("community_addon.on_message", result["side_effects"][-1]["detail"])
        self.assertIn("scene_change_unverified", [entry["code"] for entry in result["uncertainty"]])
        cached = self.capture_in("READ_ONLY")  # A cached view writes nothing.
        self.assertTrue(cached["views"][0]["cache_hit"])
        self.assertIs(cached["scene_changed"], False)

    def test_depsgraph_handlers_count_only_when_the_display_writes_update_the_depsgraph(self):
        self.env.bpy.app.handlers = SimpleNamespace(depsgraph_update_pre=[], depsgraph_update_post=[self.on_update])
        self.assertIs(self.capture_in("READ_ONLY", shading="MATERIAL")["scene_changed"], False)
        view_cache.clear()
        self.env.target.mode = "SCULPT"
        with self.assertRaisesRegex(RuntimeError, r"community_addon.on_update \(depsgraph_update_post\)"):
            self.capture_in("ASK", shading="MATERIAL")
        self.assertEqual(len(FakeOffScreen.draws), 1, "the refused capture drew nothing")
        self.assertIs(self.capture_in("ASK", shading="SOLID")["scene_changed"], False, "SOLID keeps the type")
        self.assertIsNone(self.capture_in("AUTO_BOUNDED", shading="MATERIAL")["scene_changed"])

    def test_without_add_on_python_nothing_changes(self):
        self.env.msgbus.append(("RenderSettings", "resolution_x", self.on_message))  # Not a display setting.
        result = self.capture_in("READ_ONLY")
        self.assertIs(result["scene_changed"], False)
        self.assertNotIn("extension_handlers_ran", [entry["code"] for entry in result["side_effects"]])


class SavedPicturePolicyTests(FinishedCase, Python):
    """The Render Result and its passes are saved with the scene's image format set for the save."""

    def setUp(self):
        super().setUp()
        self.plant()
        self.blender.images[0].size = (64, 36)

    def show_in(self, approval, image="RENDER_RESULT"):
        self.controller.state["options"] = {"approval": approval}
        return self.show(image)

    def test_a_subscriber_to_the_image_format_refuses_in_read_only_and_ask_and_is_named_in_auto(self):
        self.blender.msgbus.append(("ImageFormatSettings", "file_format", self.on_message))
        before = self.scene.snapshot()
        for approval in ("READ_ONLY", "ASK"):
            for image in ("RENDER_RESULT", "RENDER_RESULT:Depth"):
                with self.subTest(approval=approval, image=image), self.assertRaisesRegex(
                        RuntimeError, r"community_addon.on_message \(message bus: ImageFormatSettings.file_format\)"):
                    self.show_in(approval, image)
        self.assertEqual((self.blender.saved, self.scene.snapshot(), self.reference_files()), ([], before, []))
        result = self.show_in("AUTO_BOUNDED")
        self.assertEqual(len(self.blender.saved), 1)
        self.assertIsNone(result["scene_changed"])
        self.assertIn("community_addon.on_message", result["side_effects"][-1]["detail"])

    def test_any_depsgraph_handler_counts_because_scene_settings_always_update_the_depsgraph(self):
        self.blender.bpy.app.handlers = SimpleNamespace(depsgraph_update_pre=[], depsgraph_update_post=[self.on_update])
        with self.assertRaisesRegex(RuntimeError, r"community_addon.on_update \(depsgraph_update_post\)"):
            self.show_in("ASK")
        self.assertIsNone(self.show_in("AUTO_BOUNDED")["scene_changed"])

    def test_without_add_on_python_the_picture_says_unchanged(self):
        self.blender.msgbus.append(("View3DShading", "light", self.on_message))  # Not written by a save.
        self.assertIs(self.show_in("READ_ONLY")["scene_changed"], False)


if __name__ == "__main__":
    unittest.main()
