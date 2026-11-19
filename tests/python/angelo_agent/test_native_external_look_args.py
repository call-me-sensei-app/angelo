# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An app's look request (``angelo_look``) and the ``angelo_view`` call it becomes, without Blender.

Every call ``external_look_args.build`` makes must pass ``view_spec.validate_view`` itself, the agent's own
contract, so an app's look draws exactly as the agent's views do and never makes a camera object or reads a path.
Contract: doc/angelo/external_mcp.rst, "Looks".
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import external_look_args as look  # noqa: E402
from angelo_agent import view_spec  # noqa: E402

H1 = "scn:" + "a" * 32 + ":1"
H2 = "scn:" + "a" * 32 + ":2"
FACTS = {"handles": {"Cube": H1, "Lamp post": H2}, "bounds": ((1.0, 2.0, 0.5), 2.0), "aspect": 1.5,
         "cameras": {"Cam A": {"location": [0.0, -10.0, 2.0], "look_at": [0.0, 0.0, 2.0], "lens": 35.0},
                     "Ortho": {"location": [0.0, 0.0, 20.0], "look_at": [0.0, 0.0, 0.0], "ortho_scale": 8.0}}}


def built(request, facts=FACTS):
    arguments = look.build(look.validate(request), facts)
    view_spec.validate_view(view_spec.TOOL_NAME, arguments)   # The agent's own contract, unchanged.
    return arguments


class ValidateTests(unittest.TestCase):
    def test_defaults_are_the_persons_viewport_direction_in_solid_at_512(self):
        self.assertEqual(look.validate({}), {"views": ["FIT"], "objects": [], "resolution": 512, "shading": "SOLID"})

    def test_named_angles_are_case_insensitive_and_perspective_is_a_three_quarter_orbit(self):
        checked = look.validate({"views": ["front", "Top", "perspective"]})
        self.assertEqual(checked["views"], ["FRONT", "TOP", {"orbit": [45.0, 30.0]}])

    def test_every_refusal_says_what_is_allowed(self):
        for request, words in (
                ({"views": []}, "1-3"), ({"views": ["FRONT"] * 2}, "Repeated"), ({"views": ["SIDEWAYS"]}, "FRONT"),
                ({"views": [{"orbit": [0, 91]}]}, "elevation"), ({"views": [{"orbit": [0]}]}, "orbit"),
                ({"views": [{"orbit": [0, 0], "distance": 0}]}, "distance"),
                ({"views": [{"camera": ""}]}, "camera"), ({"views": [{"location": [0, 0], "look_at": [0, 0, 0]}]},
                                                          "location"),
                ({"views": [{"location": [0, 0, 0], "look_at": [0, 0, 0]}]}, "same point"),
                ({"objects": ["x"] * 17}, "16"), ({"objects": [""]}, "name"), ({"objects": ["a", "a"]}, "Repeated"),
                ({"resolution": 2000}, "256"), ({"shading": "WEIGHT"}, "SOLID"),
                ({"shading": "RENDERED", "views": ["SCENE_CAMERA"]}, "Render Result"),
                ({"views": [{"camera": "Cam A"}], "shading": "rendered"}, "LIT"),
                ({"image": "EDITOR"}, "image"), ({"views": [{"location": [0, 0, 1], "look_at": [0, 0, 0],
                                                             "reference": "r1"}]}, "reference"),
                ([], "object")):
            with self.subTest(request=request), self.assertRaises(ValueError) as caught:
                look.validate(request)
            self.assertIn(words, str(caught.exception))

    def test_an_app_can_never_pass_a_path_a_picture_source_or_a_comparison(self):
        for key in ("image", "file", "region", "frame", "frames", "group", "targets", "path"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                look.validate({key: "x"})


class BuildTests(unittest.TestCase):
    def test_named_angles_frame_the_chosen_objects_by_their_handles(self):
        arguments = built({"views": ["FRONT", "ISO"], "objects": ["Lamp post", "Cube"], "resolution": 768})
        self.assertEqual(arguments, {"views": ["FRONT", "ISO"], "targets": [H2, H1], "resolution": 768,
                                     "shading": "SOLID"})

    def test_an_unknown_object_is_refused_by_name_with_the_ones_it_can_be(self):
        with self.assertRaises(ValueError) as caught:
            look.build(look.validate({"objects": ["Sphere"]}), FACTS)
        self.assertIn("Sphere", str(caught.exception))
        self.assertIn("Cube", str(caught.exception))

    def test_an_orbit_circles_the_bounds_centre_looking_at_it(self):
        center, radius = FACTS["bounds"]
        for azimuth, elevation, side in ((0, 0, (0, -1, 0)), (90, 0, (1, 0, 0)), (180, 0, (0, 1, 0)),
                                         (270, 0, (-1, 0, 0)), (0, 90, (0, 0, 1))):
            with self.subTest(azimuth=azimuth, elevation=elevation):
                item = built({"views": [{"orbit": [azimuth, elevation]}]})["views"][0]
                self.assertEqual(item["look_at"], list(center))
                offset = [a - b for a, b in zip(item["location"], center)]
                distance = math.dist(item["location"], center)
                # The whole bounding sphere fits the 50 mm lens's narrower half angle (3:2: 12 mm), with margin.
                self.assertGreater(distance * math.sin(math.atan(12.0 / look.LENS_MM)), radius)
                for got, want in zip(offset, side):
                    self.assertAlmostEqual(got / distance, want, places=6)
                self.assertEqual(item["lens"], look.LENS_MM)

    def test_an_orbit_distance_is_in_scene_units_from_the_centre(self):
        item = built({"views": [{"orbit": [0, 0], "distance": 7.5}]})["views"][0]
        self.assertAlmostEqual(math.dist(item["location"], FACTS["bounds"][0]), 7.5)

    def test_a_scene_camera_by_name_is_a_free_camera_from_its_own_placement(self):
        views = built({"views": [{"camera": "Cam A"}, {"camera": "Ortho"}]})["views"]
        self.assertEqual(views[0], {"location": [0.0, -10.0, 2.0], "look_at": [0.0, 0.0, 2.0], "lens": 35.0})
        self.assertEqual(views[1], {"location": [0.0, 0.0, 20.0], "look_at": [0.0, 0.0, 0.0], "ortho_scale": 8.0})

    def test_an_unknown_camera_names_the_scene_cameras(self):
        with self.assertRaises(ValueError) as caught:
            look.build(look.validate({"views": [{"camera": "Nope"}]}), FACTS)
        self.assertIn("Cam A", str(caught.exception))

    def test_the_scene_camera_lit_and_free_cameras_stay_free(self):
        self.assertEqual(built({"views": ["SCENE_CAMERA"], "shading": "lit"})["shading"], "LIT")
        item = built({"views": [{"location": [3, -3, 2], "look_at": [0, 0, 0], "lens": 85}]})["views"][0]
        self.assertEqual(item, {"location": [3.0, -3.0, 2.0], "look_at": [0.0, 0.0, 0.0], "lens": 85.0})

    def test_an_empty_scene_still_orbits_the_origin(self):
        item = built({"views": ["perspective"]}, dict(FACTS, bounds=None))["views"][0]
        self.assertEqual(item["look_at"], [0.0, 0.0, 0.0])


if __name__ == "__main__":
    unittest.main()
