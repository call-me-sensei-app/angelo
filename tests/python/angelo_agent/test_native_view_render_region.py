# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A RENDERED look's picture on the host: a region at full detail, transparency, and the colours of the render window.

``rendered_view_case`` runs the real ``view_render`` and ``view_picture`` over
the stand-in Blender, which writes its still at the size and with the alpha
its settings give. The compiled suite ``test_blender_rendered_view_pixels``
proves the same on real renders.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from rendered_view_case import LookCase  # noqa: E402
from angelo_agent import view_spec  # noqa: E402
from angelo_agent.view_picture import CHECKER  # noqa: E402

BORDER = ("use_border", "use_crop_to_border", "border_min_x", "border_min_y", "border_max_x", "border_max_y")


class RegionTests(LookCase):
    def test_a_region_is_rendered_alone_at_the_detail_of_a_larger_frame(self):
        scene = self.blender.scene  # 1920x1080 at 50%.
        before = scene.snapshot()
        pending, result = self.run_look(region=[0.25, 0.0, 0.75, 0.5])
        during = self.blender.during["scene"]["render"]
        self.assertEqual((during["resolution_x"], during["resolution_y"], during["resolution_percentage"]),
                         (1536, 864, 100), "a frame large enough for the part to be 768 pixels wide")
        self.assertEqual([during[name] for name in BORDER], [True, True, 0.25, 0.5, 0.75, 1.0],
                         "Blender's render region counts from the bottom-left corner")
        self.assertEqual(scene.snapshot(), before, "the person's resolution and render region are back")
        view = result["views"][0]
        self.assertEqual((view["width"], view["height"], view["region"]), (768, 432, [0.25, 0.0, 0.75, 0.5]))
        self.assertEqual(view["camera"], {"source": "SCENE_CAMERA"}, "a part of the frame is no camera to reuse")
        self.assertTrue(view["name"].endswith("· region"))
        self.assertEqual((result["rendered"]["region"], result["rendered"]["frame_size"]),
                         ([0.25, 0.0, 0.75, 0.5], [1536, 864]))
        self.assertIn("one part of the scene camera's frame (region, of a 1536x864 frame)", result["note"])
        self.assertIn("judge lighting, atmosphere and the detail of that part", result["note"])
        self.assertEqual(self.delivered(result).shape[:2], (432, 768))

    def test_the_job_is_admitted_and_recorded_for_the_pixels_it_renders(self):
        pending = self.begin(region=[0.25, 0.0, 0.75, 0.5])
        record = self.manager.active()
        self.assertEqual(record["detail"]["resolution"], [768, 432], "the part, not the frame around it")
        self.assertEqual((record["request"]["frame_size"], record["request"]["border"]),
                         ([1536, 864], [0.25, 0.5, 0.75, 1.0]))
        pending.cancel()

    def test_a_region_never_asks_for_more_detail_than_the_scenes_full_resolution(self):
        _pending, result = self.run_look(region=[0.4, 0.4, 0.6, 0.6], resolution=1024)
        during = self.blender.during["scene"]["render"]
        self.assertEqual((during["resolution_x"], during["resolution_y"], during["resolution_percentage"]),
                         (1920, 1080, 100), "the scene's own resolution at 100%, not more")
        self.assertEqual((result["views"][0]["width"], result["views"][0]["height"]), (384, 216))
        self.assertEqual(result["rendered"]["frame_size"], [1920, 1080])

    def test_a_portrait_part_keeps_its_own_ratio(self):
        _pending, result = self.run_look(region=[0.0, 0.0, 0.25, 1.0], resolution=512)
        self.assertEqual((result["views"][0]["width"], result["views"][0]["height"]), (228, 512))

    def test_a_region_of_a_few_pixels_is_refused_before_anything_starts(self):
        scene = self.blender.scene
        scene.render.resolution_x, scene.render.resolution_y = 320, 180
        before = scene.snapshot()
        with self.assertRaisesRegex(RuntimeError, "only 6x4 pixels of this scene's 320x180 render; choose a larger one"):
            self.begin(region=[0.0, 0.0, 0.02, 0.02])
        self.assertEqual((self.blender.invoked, scene.snapshot(), self.reference_files()), ([], before, []))
        self.assertIsNone(self.manager.active())

    def test_region_and_frame_are_part_of_the_contract(self):
        base = {"views": ["SCENE_CAMERA"], "shading": "RENDERED"}
        checked = view_spec.validate_view("angelo_view", dict(base, region=[0, 0, 0.5, 1]))
        self.assertEqual(checked["region"], [0.0, 0.0, 0.5, 1.0])
        self.assertEqual(view_spec.validate_view("angelo_view", dict(base, image="j4", frame=120))["frame"], 120)
        self.assertEqual(view_spec.validate_view("angelo_view", dict(base, image="LAST_JOB", frame=7)),
                         dict(view_spec.validate_view("angelo_view", base), image="LAST_JOB", frame=7))
        self.assertEqual(view_spec.validate_view("angelo_view", dict(base, image="RENDER_RESULT", region=[0, 0, 1, 0.5]))["region"],
                         [0.0, 0.0, 1.0, 0.5])
        for arguments, pattern in ((dict(base, shading="LIT", region=[0, 0, 0.5, 1]), "use it with RENDERED"),
                                   (dict(base, region=[0.5, 0, 0.5, 1]), "left < right"),
                                   (dict(base, region=[0, 0, 0.5]), "four fractions"),
                                   (dict(base, frame=3), "pass that job's ID or LAST_JOB as image"),
                                   (dict(base, image="RENDER_RESULT", frame=3), "pass that job's ID or LAST_JOB as image"),
                                   (dict(base, image="LAST"), "RENDER_RESULT, LAST_JOB or the ID of a render job"),
                                   (dict(base, image="j4", frame=1.5), "the number of one frame"),
                                   (dict(base, image="j4", frame=True), "the number of one frame")):
            with self.subTest(arguments=arguments), self.assertRaisesRegex(ValueError, pattern):
                view_spec.validate_view("angelo_view", arguments)
        schema = view_spec.VIEW_TOOLS[0]["inputSchema"]["properties"]
        self.assertEqual(schema["region"], {"type": "array", "items": {"type": "number", "minimum": 0, "maximum": 1},
                                            "minItems": 4, "maxItems": 4})
        self.assertEqual(schema["frame"]["type"], "integer")

    def test_the_persons_own_render_region_is_rendered_as_f12_would_and_named(self):
        render = self.blender.scene.render
        render.use_border, render.border_max_x = True, 0.5
        before = self.blender.scene.snapshot()
        _pending, result = self.run_look()
        during = self.blender.during["scene"]["render"]
        self.assertEqual([during[name] for name in BORDER], [before["render"][name] for name in BORDER], "left as it is")
        self.assertIn("The scene's own render region is on, so only that part of the frame is rendered", result["note"])
        self.assertNotIn("region", result["rendered"])


class TransparencyTests(LookCase):
    def test_a_transparent_film_is_shown_as_a_checkerboard_and_said(self):
        self.blender.scene.pixels = (0, 0, 0, 0)
        _pending, result = self.run_look()
        self.assertEqual(self.blender.during["scene"]["render"]["image_settings"]["color_mode"], "RGBA")
        pixels = self.delivered(result)
        self.assertEqual({tuple(pixel) for pixel in pixels[::9, ::7].reshape(-1, 4).tolist()},
                         {(CHECKER[0],) * 3 + (255,), (CHECKER[1],) * 3 + (255,)})
        self.assertEqual(result["rendered"]["transparent"], 1.0)
        self.assertIn("100% of the picture is transparent or half transparent; those pixels are shown over a grey "
                      "checkerboard, which is not in the render", result["note"])
        self.assertEqual(result["views"][0]["bytes"], Path(result["views"][0]["path"]).stat().st_size)

    def test_an_opaque_render_says_nothing_about_transparency(self):
        _pending, result = self.run_look()
        self.assertEqual(self.delivered(result)[10, 10].tolist(), [120, 130, 140, 255])
        self.assertNotIn("transparent", result["rendered"])
        self.assertNotIn("checkerboard", result["note"])

    def test_a_picture_that_cannot_be_read_is_an_error_and_is_not_kept(self):
        pending = self.begin()
        self.blender.progress = 1.0
        self.manager.poll()
        self.blender.write(b"\x89PNG\r\n\x1a\n" + b"not a picture")
        self.blender.progress = None
        self.manager.poll()
        result = pending.poll()
        self.assertIn("The rendered image could not be read", result["error"])
        self.assertEqual((result["read_only"], result["scene_changed"], self.reference_files()), (True, False, []))


class ColourTests(LookCase):
    def test_the_result_names_the_view_transform_the_picture_is_in(self):
        self.blender.scene.view_settings.values.update(view_transform="Filmic", look="High Contrast", exposure=0.75)
        _pending, result = self.run_look()
        self.assertEqual(result["rendered"]["color"], {"view_transform": "Filmic", "look": "High Contrast", "exposure": 0.75})
        self.assertIn("the scene's Filmic view transform", result["note"])

    def test_an_output_that_overrides_the_colour_management_still_renders_what_the_render_window_shows(self):
        scene = self.blender.scene
        settings = scene.render.image_settings
        settings.color_management = "OVERRIDE"
        settings.view_settings.values.update(view_transform="Standard", look="None", exposure=2.0)
        scene.view_settings.values.update(view_transform="AgX", look="AgX - Punchy", exposure=-1.0)
        before = scene.snapshot()
        _pending, result = self.run_look()
        during = self.blender.during["scene"]["render"]["image_settings"]
        self.assertEqual(during["color_management"], "OVERRIDE", "never switched off: switching it on again resets it")
        self.assertEqual({name: during["view_settings"][name] for name in ("view_transform", "look", "exposure")},
                         {"view_transform": "AgX", "look": "AgX - Punchy", "exposure": -1.0})
        self.assertEqual(during["display_settings"]["display_device"], "sRGB")
        self.assertEqual(scene.snapshot(), before, "the output keeps its own view transform")
        self.assertFalse(result["uncertain"])
        self.assertEqual(result["side_effects"], [])

    def test_scene_curves_an_overriding_output_cannot_take_are_said(self):
        scene = self.blender.scene
        scene.render.image_settings.color_management = "OVERRIDE"
        scene.view_settings.values["use_curve_mapping"] = True
        _pending, result = self.run_look()
        self.assertEqual([entry["code"] for entry in result["uncertainty"]], ["render_color_approximate"])
        self.assertTrue(result["uncertain"])


if __name__ == "__main__":
    unittest.main()
