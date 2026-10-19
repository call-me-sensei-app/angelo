# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The pixels a model is shown (``view_picture``): bounds before decoding, the crop, the scale and transparency.

``picture_fakes`` stands in for Blender's ``imbuf``; everything else is the
real code. Each test plants pixels and reads them back from the PNG the model
would get.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import picture_fakes as fakes  # noqa: E402
from angelo_agent import view_picture, view_spec  # noqa: E402

WHITE, RED = (255, 255, 255, 255), (255, 0, 0, 255)
LIGHT, DARK, SQUARE = view_picture.CHECKER


def picture(width, height, colour=WHITE):
    pixels = numpy.empty((height, width, 4), dtype=numpy.uint8)
    pixels[...] = colour
    return pixels


class PictureCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.imbuf = fakes.imbuf_module()
        self.modules = patch.dict(sys.modules, {"imbuf": self.imbuf})
        self.modules.start()

    def tearDown(self):
        self.modules.stop()
        self.temp.cleanup()

    def file(self, name, data):
        path = self.folder / name
        path.write_bytes(data)
        return path

    def scaled(self, pixels, region=None, long_side=None):
        """``view_picture.scaled`` of a planted picture: ``(delivered RGBA rows, top first; facts)``."""
        png, facts = view_picture.scaled(self.file("planted.png", fakes.png_bytes(pixels)), region, long_side)
        return fakes.read_png(png), facts


class HeaderTests(PictureCase):
    def test_size_and_depth_are_read_from_the_first_bytes(self):
        self.assertEqual(view_picture.header(self.file("a.png", fakes.png_header(3840, 2160))), (3840, 2160, 8))
        self.assertEqual(view_picture.header(self.file("deep.png", fakes.png_header(3840, 2160, depth=16))), (3840, 2160, 16))
        self.assertEqual(view_picture.header(self.file("a.jpg", fakes.jpeg_header(1920, 1080))), (1920, 1080, 8))
        self.assertEqual(view_picture.header(self.file("a.exr", fakes.exr_header(4096, 1716))),
                         (4096, 1716, view_picture.LINEAR))
        self.assertEqual(view_picture.header(self.file("window.exr", fakes.exr_header(640, 360, origin=(-20, 15)))),
                         (640, 360, view_picture.LINEAR))

    def test_other_files_have_no_header(self):
        for name, data in (("short.jpg", b"\xff\xd8\xff"), ("text.png", b"not a picture at all, only text" * 4),
                           ("empty.exr", b"\x76\x2f\x31\x01" + bytes(64)), ("one.png", fakes.png_header(8, 8, depth=1)),
                           ("cut.exr", fakes.exr_header(640, 360)[:40]), ("nothing", b"")):
            with self.subTest(name=name):
                self.assertIsNone(view_picture.header(self.file(name, data)))

    def test_a_picture_too_large_to_decode_is_refused_before_it_is_opened(self):
        self.assertEqual(view_picture.admit(self.file("8k.png", fakes.png_header(7680, 4320)), "Job j1 image"),
                         (7680, 4320, 8))
        for name, data, pattern in (
                ("huge.png", fakes.png_header(16000, 16000), "16000x16000: more than the 68 million pixels"),
                ("huge.exr", fakes.exr_header(8192, 8192), "8192x8192: more than the 36 million pixels"),
                ("deep.png", fakes.png_header(8192, 8192, depth=16), "more than the 36 million pixels"),
                ("notes.png", b"words", "is not a PNG, JPEG or OpenEXR picture"),
                ("zero.png", fakes.png_header(0, 100), "is not a PNG, JPEG or OpenEXR picture")):
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, "Job j1 image.*" + pattern):
                view_picture.admit(self.file(name, data), "Job j1 image")
        self.assertEqual(self.imbuf.loaded, [], "nothing was decoded")
        with patch.object(view_picture, "MAX_BYTES", 16), self.assertRaisesRegex(RuntimeError, "is not a PNG"):
            view_picture.admit(self.file("big.png", fakes.png_header(64, 64)), "Job j1 image")
        with self.assertRaisesRegex(RuntimeError, "is not a PNG"):
            view_picture.admit(self.folder / "missing.png", "Job j1 image")


class RegionTests(PictureCase):
    def test_a_region_is_four_fractions_from_the_top_left_corner(self):
        self.assertEqual(view_spec.check_region([0.25, 0, 0.75, 1]), [0.25, 0.0, 0.75, 1.0])
        for value in ([0.5, 0.0, 0.4, 1.0], [0.0, 0.6, 1.0, 0.6], [0.0, 0.0, 1.5, 1.0], [-0.1, 0.0, 1.0, 1.0],
                      [0.0, 0.0, 1.0], "0,0,1,1", [0.0, 0.0, 0.01, 1.0], [0.0, 0.0, 1.0, 0.01], [True, 0.0, 1.0, 1.0],
                      [0.0, 0.0, None, 1.0]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                view_spec.check_region(value)
        self.assertEqual(view_picture.pixel_box([0.25, 0.5, 0.75, 1.0], 3840, 2160), (960, 1080, 2880, 2160))
        self.assertEqual(view_picture.pixel_box([0.0, 0.0, 1.0, 1.0], 7, 5), (0, 0, 7, 5))

    def test_the_crop_is_the_part_asked_for_and_not_its_mirror(self):
        pixels = picture(200, 100)
        pixels[10:20, 150:160] = RED  # Near the top-right corner: rows 10-19 from the top.
        delivered, facts = self.scaled(pixels, region=[0.7, 0.05, 0.85, 0.25])
        self.assertEqual(facts, {"source_size": [200, 100], "region_pixels": [30, 20], "size": [30, 20], "transparent": 0.0})
        red = numpy.argwhere(delivered[..., 1] == 0)
        self.assertEqual((red[:, 0].min(), red[:, 0].max(), red[:, 1].min(), red[:, 1].max()), (5, 14, 10, 19))
        other, _facts = self.scaled(pixels, region=[0.7, 0.75, 0.85, 0.95])  # The same columns at the bottom.
        self.assertEqual(int(other[..., 1].min()), 255, "the mark is at the top only")

    def test_a_small_defect_lost_in_the_downscaled_frame_is_there_in_its_region(self):
        frame = picture(3840, 2160)
        frame[1000:1003, 3000:3003] = RED  # Three pixels across in a 4K frame.
        whole, facts = self.scaled(frame, long_side=768)
        self.assertEqual((facts["size"], facts["source_size"]), ([768, 432], [3840, 2160]))
        self.assertGreater(int(whole[..., 1].min()), 150, "averaged away: nothing in the whole frame is red")
        part, facts = self.scaled(frame, region=[0.75, 0.42, 0.82, 0.5], long_side=768)
        self.assertEqual((facts["region_pixels"], facts["size"]), ([269, 173], [269, 173]), "its own pixels, not enlarged")
        marks = numpy.argwhere((part[..., 0] == 255) & (part[..., 1] == 0) & (part[..., 2] == 0))
        self.assertEqual(len(marks), 9, "all nine red pixels")

    def test_a_picture_is_scaled_down_and_never_up(self):
        delivered, facts = self.scaled(picture(1920, 1080), long_side=384)
        self.assertEqual((delivered.shape[:2], facts["size"]), ((216, 384), [384, 216]))
        delivered, facts = self.scaled(picture(320, 180), long_side=1024)
        self.assertEqual(facts["size"], [320, 180])
        delivered, facts = self.scaled(picture(1080, 1920), region=[0.0, 0.0, 1.0, 0.5], long_side=512)
        self.assertEqual((facts["region_pixels"], facts["size"]), ([1080, 960], [512, 455]))

    def test_a_region_of_a_few_pixels_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, "only 3x2 pixels of the 160x90 picture"):
            self.scaled(picture(160, 90), region=[0.0, 0.0, 0.02, 0.02])
        self.assertTrue(all(buffer.freed for buffer in self.imbuf.loaded), "the decoded picture is released")


class TransparencyTests(PictureCase):
    def test_an_opaque_picture_is_delivered_as_it_is(self):
        pixels = picture(32, 16, (10, 200, 30, 255))
        rows = numpy.ascontiguousarray(pixels)
        same, fraction = view_picture.flatten(rows)
        self.assertIs(same, rows)
        self.assertEqual(fraction, 0.0)
        delivered, facts = self.scaled(pixels)
        self.assertEqual((delivered[3, 3].tolist(), facts["transparent"]), ([10, 200, 30, 255], 0.0))

    def test_a_quarter_opaque_red_is_not_delivered_as_solid_red(self):
        pixels = picture(64, 32, (0, 255, 0, 255))
        pixels[:, :32] = (255, 0, 0, 64)  # 25% opaque red on the left half.
        delivered, facts = self.scaled(pixels)
        self.assertEqual(facts["transparent"], 0.5)
        light = [round(255 * 64 / 255 + LIGHT * (1 - 64 / 255)), round(LIGHT * (1 - 64 / 255)), round(LIGHT * (1 - 64 / 255))]
        dark = [round(255 * 64 / 255 + DARK * (1 - 64 / 255)), round(DARK * (1 - 64 / 255)), round(DARK * (1 - 64 / 255))]
        left = {tuple(pixel) for pixel in delivered[:, :32, :3].reshape(-1, 3).tolist()}
        self.assertEqual(left, {tuple(light), tuple(dark)}, "red at a quarter over both greys of the checkerboard")
        self.assertNotIn((255, 0, 0), left)
        self.assertEqual({tuple(pixel) for pixel in delivered[:, 32:, :3].reshape(-1, 3).tolist()}, {(0, 255, 0)})
        self.assertEqual(int(delivered[..., 3].min()), 255, "what the model gets is opaque")

    def test_a_hole_shows_the_checkerboard_and_not_black_or_white(self):
        pixels = picture(96, 64, (40, 40, 40, 255))
        pixels[16:48, 32:64] = (0, 0, 0, 0)  # A fully transparent hole whose stored colour is black.
        delivered, facts = self.scaled(pixels)
        hole = {tuple(pixel) for pixel in delivered[16:48, 32:64, :3].reshape(-1, 3).tolist()}
        self.assertEqual(hole, {(LIGHT, LIGHT, LIGHT), (DARK, DARK, DARK)})
        self.assertEqual(delivered[20, 34, 0] != delivered[20, 34 + SQUARE, 0], True, "squares alternate")
        self.assertEqual(facts["transparent"], round(32 * 32 / (96 * 64), 4))
        self.assertIn("17% of the picture is transparent", view_picture.transparency_note(facts["transparent"]))
        self.assertIn("grey checkerboard, which is not in the render", view_picture.transparency_note(0.4))
        self.assertIn("Under 1%", view_picture.transparency_note(0.001))
        self.assertEqual(view_picture.transparency_note(0.0), "")

    def test_premultiplied_rows_are_not_darkened_twice(self):
        rows = numpy.zeros((16, 16, 4), dtype=numpy.uint8)
        rows[...] = (64, 0, 0, 64)  # 25% red, premultiplied.
        flat, fraction = view_picture.flatten(rows, premultiplied=True)
        self.assertEqual(flat[0, 0].tolist(), [round(64 + LIGHT * (1 - 64 / 255)), round(LIGHT * (1 - 64 / 255)),
                                               round(LIGHT * (1 - 64 / 255)), 255])
        self.assertEqual(fraction, 1.0)


class ColourTests(unittest.TestCase):
    def test_the_result_names_the_view_transform_and_what_departs_from_its_defaults(self):
        scene = SimpleNamespace(view_settings=SimpleNamespace(view_transform="AgX", look="None", exposure=0.0, gamma=1.0))
        self.assertEqual(view_picture.colour(scene), {"view_transform": "AgX"})
        scene.view_settings = SimpleNamespace(view_transform="Filmic", look="High Contrast", exposure=-1.25, gamma=0.8)
        self.assertEqual(view_picture.colour(scene), {"view_transform": "Filmic", "look": "High Contrast",
                                                      "exposure": -1.25, "gamma": 0.8})


if __name__ == "__main__":
    unittest.main()
