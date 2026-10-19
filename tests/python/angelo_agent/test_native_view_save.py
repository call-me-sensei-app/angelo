# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pictures Blender saves for a view never write the scene, so they run no add-on Python in any approval mode.

The rule: the Render Result, its passes and scene-linear files must
stay visible by default (Ask), so instead of refusing them when an add-on has a
depsgraph handler or a message-bus subscriber, ``view_save`` saves them with
``_bpy._angelo_picture.save_copy``, an image format of the save's own. The
stand-in Blender (``rendered_view_fakes``) asserts that a ``save_copy`` never
changes the scene; ``test_blender_view_sources`` proves the pixels and the
silence of add-on Python in Blender itself.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import picture_fakes  # noqa: E402
from rendered_view_case import FinishedCase  # noqa: E402  (puts the add-on on the path)
from test_native_view_passes import HEIGHT, WIDTH, multilayer  # noqa: E402
from angelo_agent import view_save  # noqa: E402


def addon_function(folder, name):
    namespace = {"__name__": "community_addon"}
    exec(compile("def %s(*args):\n    return None\n" % name, str(Path(folder) / "community_addon.py"), "exec"),
         namespace)
    return namespace[name]


class SavedPictureCase(FinishedCase):
    """A build with the helper, an add-on handler and subscribers registered, and the default Ask mode."""

    def setUp(self):
        super().setUp()
        self.blender.picture_helper()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        on_update = addon_function(temp.name, "on_update")
        self.blender.bpy.app.handlers = SimpleNamespace(depsgraph_update_pre=[], depsgraph_update_post=[on_update])
        self.blender.msgbus += [("ImageFormatSettings", None, on_update), ("RenderSettings", None, on_update)]
        self.controller.state["options"] = {"approval": "ASK"}

    def unchanged(self, result):
        self.assertIs(result["scene_changed"], False)
        self.assertNotIn("extension_handlers_ran", [entry["code"] for entry in result["side_effects"]])


class RenderResultTests(SavedPictureCase):
    def test_the_render_result_is_shown_in_every_mode_and_the_scene_is_never_written(self):
        self.blender.images[0].size, self.blender.images[0].colour = (64, 36), (200, 100, 50, 255)
        before = self.scene.snapshot()
        for approval in ("READ_ONLY", "ASK", "AUTO_BOUNDED"):
            with self.subTest(approval=approval):
                self.controller.state["options"] = {"approval": approval}
                result = self.show("RENDER_RESULT")
                self.unchanged(result)
                self.assertEqual(self.delivered(result)[2, 2].tolist(), [200, 100, 50, 255])
        self.assertEqual((self.scene.snapshot(), self.blender.saved), (before, []), "no save_render, no write")
        copy = self.blender.copies[0]
        self.assertIs(copy["image"], self.blender.images[0])
        self.assertEqual((copy["settings"], copy["display"]), (view_save.PICTURE, True))
        self.assertFalse(self.show("RENDER_RESULT")["uncertain"], "the scene's own view, curves included")

    def test_no_render_yet_is_still_said(self):
        self.blender.images[0].has_pixels = False
        with self.assertRaisesRegex(RuntimeError, "Blender's Render Result holds no render yet"):
            self.show("RENDER_RESULT")


class LinearFileTests(SavedPictureCase):
    def setUp(self):
        super().setUp()
        self.path = self.file("final.exr", picture_fakes.exr_header(64, 36))
        self.blender.files[str(self.path)] = {"size": (64, 36), "colour": (237, 214, 190, 255)}
        self.job(self.path)

    def test_a_file_is_read_outside_the_document_and_shown_in_every_mode(self):
        images = list(self.blender.images)
        for approval in ("READ_ONLY", "ASK"):
            self.controller.state["options"] = {"approval": approval}
            result = self.show()
            self.unchanged(result)
            self.assertEqual(self.delivered(result)[4, 4].tolist(), [237, 214, 190, 255])
        self.assertEqual(self.blender.images, images, "no Image data-block was added, even for a moment")
        self.assertEqual(self.blender.copies[0]["image"].size, (64, 36))
        self.blender.files[str(self.path)]["kind"] = "MULTILAYER"
        self.assertTrue(self.show()["rendered"]["multilayer"])

    def test_a_file_blender_cannot_read_is_an_error_with_nothing_left(self):
        self.blender.files[str(self.path)]["has_pixels"] = False
        with self.assertRaisesRegex(RuntimeError, r"could not be read \(Image 'Image' does not have any image data"):
            self.show()
        self.assertEqual(self.reference_files(), [])


class PassTests(SavedPictureCase):
    def setUp(self):
        super().setUp()
        self.blender.window.view_layer.name = "ViewLayer"
        picture = self.blender.save_picture

        def save_picture(image, filepath, settings):  # Blender writes every layer and pass.
            if settings.get("file_format") == "OPEN_EXR_MULTILAYER":
                return Path(filepath).write_bytes(multilayer(("ViewLayer",)))
            return picture(image, filepath, settings)
        self.blender.save_picture = save_picture

    def test_a_data_pass_and_a_colour_pass_are_saved_without_writing_the_scene(self):
        before = self.scene.snapshot()
        result = self.show("RENDER_RESULT:Depth", resolution=256)
        self.unchanged(result)
        self.assertEqual(self.delivered(result).shape[:2], (HEIGHT, WIDTH))
        self.assertEqual(result["side_effects"], [], "no output colour space is named: nothing is set")
        self.assertEqual((self.blender.copies[0]["settings"], self.blender.copies[0]["display"]),
                         (view_save.MULTILAYER, False))
        colour = self.show("RENDER_RESULT:Combined")
        self.unchanged(colour)
        self.assertEqual(colour["rendered"]["preview"], "DISPLAY_REFERRED")
        self.assertEqual((self.scene.snapshot(), len(self.blender.images)), (before, 1))


class FallbackTests(FinishedCase):
    """A build without the helper keeps the settings route and its approval-mode rule (``view_policy``)."""

    def test_without_the_helper_the_scene_format_is_set_and_put_back(self):
        self.assertIsNone(view_save.native())
        self.blender.images[0].size = (64, 36)
        before = self.scene.snapshot()
        self.show("RENDER_RESULT")
        (_image, during), = self.blender.saved
        self.assertEqual(during["render"]["image_settings"]["file_format"], "PNG")
        self.assertEqual(self.scene.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
