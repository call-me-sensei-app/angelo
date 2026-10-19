# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_view`` with ``image`` on the host: which finished pictures can be shown, and what the model then sees.

The stand-in Blender (``rendered_view_fakes``) saves and loads images as
Blender does; ``picture_fakes`` stands in for ``imbuf``. Each test plants a
picture file and reads the delivered PNG back. The compiled suite
``test_blender_rendered_view_pixels`` does the same with real files, real
OpenEXR values and Blender's own view transforms.
"""

import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import numpy

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import picture_fakes  # noqa: E402
import rendered_view_fakes as fakes  # noqa: E402
from angelo_agent.references import ReferenceStore  # noqa: E402
from angelo_agent.view_picture import CHECKER  # noqa: E402

LOOK = {"targets": [], "views": ["SCENE_CAMERA"], "shading": "RENDERED", "resolution": 768}
from rendered_view_case import GREY, RED, FinishedCase, picture  # noqa: E402


class StillTests(FinishedCase):
    def test_a_finished_still_is_returned_downscaled_and_never_enlarged(self):
        self.job(self.file("final.png", picture(3840, 2160)))
        before = self.scene.snapshot()
        result = self.show()
        item = result["views"][0]
        self.assertEqual((item["width"], item["height"], item["shading"], item["framing"]),
                         (768, 432, "RENDERED", "FINISHED_IMAGE"))
        self.assertEqual(result["rendered"], {"job": "j3", "complete": True, "job_complete": True, "output": "renders/final.png",
                                              "source": "CURRENT_OUTPUT_FILE",
                                              "source_size": [3840, 2160], "engine": "CYCLES", "samples": 128})
        self.assertTrue(result["read_only"] and not result["scene_changed"] and not result["uncertain"])
        self.assertIn("The finished image of job j3 (renders/final.png), 3840x2160 downscaled to 768x432", result["note"])
        self.assertIn("current output file", result["note"])
        self.assertEqual(self.delivered(result)[5, 5].tolist(), list(GREY))
        self.assertEqual(self.scene.snapshot(), before, "an 8-bit file needs no setting changed")
        self.assertEqual(self.blender.saved, [], "and no save by Blender")
        self.job(self.file("small.png", picture(320, 180)))
        small = self.show(resolution=1024)
        self.assertEqual((small["views"][0]["width"], small["views"][0]["height"]), (320, 180))
        self.assertIn(", 320x180. It reads", small["note"])
        self.assertEqual(len(self.reference_files()), 2, "one reference per picture, nothing temporary")

    def test_a_region_shows_a_small_defect_the_downscaled_frame_loses(self):
        frame = picture(3840, 2160, (255, 255, 255, 255))
        frame[1000:1003, 3000:3003] = RED
        self.job(self.file("final.png", frame))
        whole = self.delivered(self.show())
        self.assertGreater(int(whole[..., 1].min()), 150)
        result = self.show(region=[0.75, 0.42, 0.82, 0.5])
        part = self.delivered(result)
        self.assertEqual(int(((part[..., 0] == 255) & (part[..., 1] == 0)).sum()), 9)
        item = result["views"][0]
        self.assertEqual((item["width"], item["height"], item["region"]), (269, 173, [0.75, 0.42, 0.82, 0.5]))
        self.assertEqual((result["rendered"]["region"], result["rendered"]["region_pixels"]),
                         ([0.75, 0.42, 0.82, 0.5], [269, 173]))
        self.assertIn("the part asked for (269x173 of its 3840x2160 pixels) at 269x173", result["note"])

    def test_a_half_transparent_render_is_not_delivered_as_opaque(self):
        frame = picture(256, 128, (0, 255, 0, 255))
        frame[:, :128] = (255, 0, 0, 64)  # A quarter opaque.
        self.job(self.file("cutout.png", frame))
        result = self.show()
        left = {tuple(pixel) for pixel in self.delivered(result)[:, :128, :3].reshape(-1, 3).tolist()}
        quarter = 64 / 255
        self.assertEqual(left, {tuple(round(value * quarter + grey * (1 - quarter)) for value in (255, 0, 0))
                                for grey in CHECKER[:2]})
        self.assertEqual(result["rendered"]["transparent"], 0.5)
        self.assertIn("50% of the picture is transparent or half transparent", result["note"])
        self.assertIn("grey checkerboard", result["note"])

    def test_a_sixteen_bit_png_is_shown_and_named(self):
        self.job(self.file("deep.png", picture_fakes.png_bytes(picture(640, 360), depth=16)))
        result = self.show()
        self.assertEqual(result["rendered"]["source_depth"], 16)
        self.assertEqual(self.blender.saved, [], "display values already: no view transform is applied again")

    def test_only_this_conversations_finished_render_pictures_can_be_shown(self):
        path = self.file("final.png", picture(64, 36))
        for changes, pattern in (
                ({"session": "other"}, "No job j3 in this conversation"),
                ({"kind": "EXPORT_USD"}, "only render and texture bake jobs have pictures"),
                ({"detail": {"output_path": str(self.folder / "gone.png")}}, "moved or deleted")):
            self.job(path, **changes)
            with self.subTest(pattern=pattern), self.assertRaisesRegex(RuntimeError, pattern):
                self.show()
        with self.assertRaisesRegex(RuntimeError, "No job j9 in this conversation"):
            self.show("j9")
        for name, data, pattern in (("notes.png", b"not a picture", "is not a PNG, JPEG or OpenEXR picture"),
                                    ("huge.png", picture_fakes.png_header(16000, 16000), "is 16000x16000: more than the 68 million"),
                                    ("frames.mp4", bytes(64), "is not a PNG, JPEG or OpenEXR picture")):
            self.job(self.file(name, data))
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, "Job j3 image " + pattern):
                self.show()
        self.job(path)
        with self.assertRaisesRegex(RuntimeError, "rendered one still; leave frame out"):
            self.show(frame=4)
        self.assertEqual(self.reference_files(), [], "a refusal leaves no file")


class AnimationTests(FinishedCase):
    def setUp(self):
        super().setUp()
        for number, colour in ((1, (255, 0, 0, 255)), (2, (0, 255, 0, 255)), (3, (0, 0, 255, 255))):
            self.file("shot/frame_%04d.png" % number, picture(320, 180, colour))
        self.file("shot/notes.txt", b"not a frame")
        self.job(self.folder / "shot", kind="RENDER_ANIMATION")

    def test_one_frame_is_shown_by_its_number_and_the_first_without_one(self):
        result = self.show(frame=2, resolution=256)
        self.assertEqual(self.delivered(result)[3, 3].tolist(), [0, 255, 0, 255])
        self.assertEqual({key: result["rendered"][key] for key in ("frame", "frames", "frame_count")},
                         {"frame": 2, "frames": [1, 3], "frame_count": 3})
        self.assertIn("Frame 2 of job j3 (renders/shot), 320x180 downscaled to 256x144", result["note"])
        self.assertEqual(result["views"][0]["name"], "Job j3 frame 2 · rendered")
        first = self.show(resolution=256)
        self.assertEqual((self.delivered(first)[3, 3].tolist(), first["rendered"]["frame"]), ([255, 0, 0, 255], 1))

    def test_a_frame_the_job_did_not_write_names_the_ones_it_did(self):
        with self.assertRaisesRegex(RuntimeError, "Job j3 has no frame 9: it wrote 3 frames, 1 to 3"):
            self.show(frame=9)

    def test_frames_kept_by_a_job_that_was_cancelled_can_be_shown(self):
        self.records["j3"].update(status="cancelled", receipt={"kept": "3/24 frames"})
        result = self.show(frame=3)
        self.assertEqual(result["rendered"]["frame"], 3)
        self.assertEqual(result["job"]["status"], "cancelled")
        self.assertFalse(result["rendered"]["job_complete"])

    def test_a_job_that_kept_no_frames_says_so(self):
        self.job(self.folder / "empty", kind="RENDER_ANIMATION", status="failed")
        result = self.show()
        self.assertEqual(result["job"]["status"], "failed")
        self.assertEqual(result["views"], [])
        self.assertIn("Job j3 kept no frames (failed)", result["note"])


class LinearTests(FinishedCase):
    """An OpenEXR file has no look until Blender applies the scene's view transform."""

    def setUp(self):
        super().setUp()
        self.path = self.file("final.exr", picture_fakes.exr_header(2048, 1152))
        self.blender.files[str(self.path)] = {"size": (2048, 1152), "colour": (237, 214, 190, 255)}
        self.job(self.path)

    def test_blender_saves_it_through_the_scenes_view_transform_and_everything_is_put_back(self):
        self.scene.view_settings.view_transform, self.scene.view_settings.exposure = "AgX", -0.5
        before, images = self.scene.snapshot(), list(self.blender.images)
        result = self.show()
        (image, during), = self.blender.saved
        self.assertEqual(image.size, (2048, 1152), "the loaded file, saved once by Blender itself")
        settings = during["render"]["image_settings"]
        self.assertEqual((settings["media_type"], settings["file_format"], settings["color_mode"], settings["color_depth"]),
                         ("IMAGE", "PNG", "RGBA", "8"))
        self.assertIn("_angelo_job_restore", during["custom"])
        self.assertEqual(self.scene.snapshot(), before, "the scene's image format is as the person left it")
        self.assertEqual(self.blender.images, images, "the temporary Image is removed")
        self.assertEqual(self.delivered(result)[4, 4].tolist(), [237, 214, 190, 255])
        self.assertEqual(result["rendered"], {
            "job": "j3", "output": "renders/final.exr", "engine": "CYCLES", "samples": 128, "complete": True,
            "job_complete": True,
            "source": "CURRENT_OUTPUT_FILE",
            "preview": "DISPLAY_REFERRED", "source_format": "OPEN_EXR", "source_size": [2048, 1152],
            "color": {"view_transform": "AgX", "exposure": -0.5}})
        self.assertIn("The file is OpenEXR (scene-linear values): this is an 8-bit preview through the scene's current "
                      "AgX view transform, not its exact values", result["note"])
        self.assertEqual(len(self.reference_files()), 1, "the display copy is not kept")
        self.assertEqual(result["side_effects"], [])

    def test_a_multilayer_file_says_that_only_the_combined_image_is_shown(self):
        self.blender.files[str(self.path)]["kind"] = "MULTILAYER"
        result = self.show()
        self.assertTrue(result["rendered"]["multilayer"])
        self.assertIn("Only the combined image is shown; its separate passes, layers or views are not", result["note"])

    def test_it_waits_for_a_job_that_has_its_own_settings_applied(self):
        self.active = {"id": "j7", "kind": "RENDER_ANIMATION"}
        with self.assertRaisesRegex(RuntimeError, "A job is running with its own render settings; Job j3 image is OpenEXR"):
            self.show()
        self.active, self.blender.blender_jobs["RENDER"] = None, True
        with self.assertRaisesRegex(RuntimeError, "can be shown once it ends"):
            self.show()
        self.assertEqual((self.blender.saved, self.reference_files()), ([], []))

    def test_a_file_blender_cannot_save_is_an_error_with_everything_put_back(self):
        self.blender.files[str(self.path)]["has_pixels"] = False
        before, images = self.scene.snapshot(), list(self.blender.images)
        with self.assertRaisesRegex(RuntimeError, r"Job j3 image could not be read \(Image does not have any image data\)"):
            self.show()
        self.assertEqual((self.scene.snapshot(), self.blender.images, self.reference_files()), (before, images, []))


class RenderResultTests(FinishedCase):
    def test_the_render_result_is_saved_as_the_render_window_shows_it(self):
        result_image = self.blender.images[0]
        result_image.size, result_image.colour = (1920, 1080), (200, 100, 50, 255)
        result_image.render_slots.active_index = 2
        before = self.scene.snapshot()
        result = self.show("RENDER_RESULT", resolution=384)
        self.assertEqual(self.scene.snapshot(), before)
        (image, during), = self.blender.saved
        self.assertIs(image, result_image)
        self.assertEqual(during["render"]["image_settings"]["color_mode"], "RGBA")
        self.assertEqual(self.delivered(result)[2, 2].tolist(), [200, 100, 50, 255])
        self.assertEqual(result["rendered"], {"image": "RENDER_RESULT", "slot": "Slot 3", "complete": True,
                                              "source_size": [1920, 1080], "color": {"view_transform": "AgX"}})
        self.assertIn("Blender's current Render Result, 1920x1080 downscaled to 384x216", result["note"])
        self.assertNotIn("Only the combined image", result["note"])
        self.assertEqual(result_image.render_slots.active_index, 2)
        self.assertEqual(len(self.reference_files()), 1)

    def test_a_render_with_further_passes_layers_or_views_says_what_is_not_shown(self):
        self.scene.view_layers[0].values.update(use_pass_normal=True, use_pass_mist=True)
        self.scene.view_layers.append(fakes.Struct(use=True, use_pass_combined=True, use_pass_z=True))
        self.scene.view_layers.append(fakes.Struct(use=False, use_pass_combined=True, use_pass_z=True))
        result = self.show("RENDER_RESULT")
        self.assertEqual((result["rendered"]["other_passes"], result["rendered"]["view_layers"]), (3, 2))
        self.assertIn("Only the combined image is shown; its separate passes, layers or views are not", result["note"])

    def test_an_output_that_overrides_the_colour_management_is_shown_in_the_scenes_view(self):
        settings = self.scene.render.image_settings
        settings.color_management = "OVERRIDE"
        settings.view_settings.values.update(view_transform="Standard", exposure=2.0)
        self.scene.view_settings.values.update(view_transform="AgX", look="AgX - Punchy", exposure=-1.0)
        before = self.scene.snapshot()
        result = self.show("RENDER_RESULT")
        during = self.blender.saved[0][1]["render"]["image_settings"]
        self.assertEqual(during["color_management"], "OVERRIDE", "never switched: switching back would reset its values")
        self.assertEqual({key: during["view_settings"][key] for key in ("view_transform", "look", "exposure")},
                         {"view_transform": "AgX", "look": "AgX - Punchy", "exposure": -1.0})
        self.assertEqual(self.scene.snapshot(), before, "the output's own view is back")
        self.assertFalse(result["uncertain"])
        self.scene.view_settings.values["use_curve_mapping"] = True
        result = self.show("RENDER_RESULT")
        self.assertEqual(result["uncertainty"][0]["code"], "render_color_approximate")
        self.assertTrue(result["uncertain"])

    def test_no_render_yet_and_a_running_render(self):
        self.blender.images[0].has_pixels = False
        before = self.scene.snapshot()
        with self.assertRaisesRegex(RuntimeError, "holds no render yet; RENDERED without image renders a look"):
            self.show("RENDER_RESULT")
        self.assertEqual((self.scene.snapshot(), self.reference_files()), (before, []))
        self.blender.images.clear()
        with self.assertRaisesRegex(RuntimeError, "holds no render yet"):
            self.show("RENDER_RESULT")
        self.blender.images.append(fakes.Image(self.blender, "RENDER_RESULT", slots=fakes.Slots([fakes.Slot("Slot 1")])))
        self.active = {"id": "j7", "kind": "RENDER_STILL"}
        with self.assertRaisesRegex(RuntimeError, "A render is running; the Render Result can be shown once it ends"):
            self.show("RENDER_RESULT")

    def test_a_region_of_the_render_result(self):
        self.blender.images[0].size = (1920, 1080)
        result = self.show("RENDER_RESULT", region=[0.5, 0.5, 1.0, 1.0], resolution=512)
        self.assertEqual((result["views"][0]["width"], result["views"][0]["height"]), (512, 288))
        self.assertEqual(result["rendered"]["region_pixels"], [960, 540])


if __name__ == "__main__":
    unittest.main()
