# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""End-of-session scoring of the scenario benchmarks on synthetic captures (host NumPy, no Blender).

``scenario_capture`` writes, per fixed view, a planar depth map (``inf`` for
sky) and a hit-object map; ``scenario_scoring`` compares a side's capture with
the ground truth's. These tests pin the metrics on hand-made maps, the capture
archive format and the ray layout of a fixed camera.
"""

import json
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

try:
    import numpy
except ImportError:  # CI installs NumPy (run_host_tests --max-skips 0); Blender bundles it.
    numpy = None

import scenario_capture as capture  # noqa: E402
from test_native_scenario_checks import FAKE_MATHUTILS  # noqa: E402

INF = float("inf")


def view(depth, ids):
    return {"depth": numpy.asarray(depth, dtype=numpy.float64), "ids": numpy.asarray(ids, dtype=numpy.int32)}


def capture_of(views, names, parents=None):
    return {"names": names, "parents": parents or {}, "resolution": [4, 2], "views": views}


def read_png(path):
    data = Path(path).read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    position, idat, size = 8, b"", None
    while position < len(data):
        length = struct.unpack(">I", data[position:position + 4])[0]
        kind, body = data[position + 4:position + 8], data[position + 8:position + 8 + length]
        if kind == b"IHDR":
            size = struct.unpack(">II", body[:8])
        elif kind == b"IDAT":
            idat += body
        position += 12 + length
    width, height = size
    raw = zlib.decompress(idat)
    rows = [raw[row * (3 * width + 1) + 1:(row + 1) * (3 * width + 1)] for row in range(height)]
    return size, [[tuple(row[3 * x:3 * x + 3]) for x in range(width)] for row in rows]


@unittest.skipIf(numpy is None, "NumPy is installed in CI and bundled with Blender")
class ScoringTests(unittest.TestCase):
    def setUp(self):
        import scenario_scoring
        self.scoring = scenario_scoring
        # Row 0 is the top. Ids: 0 Valley, 1 Bridge Deck, 2 Wheel FL (a child of Car, so part of the subject).
        self.truth = capture_of({"front": view([[INF, 10, 10, INF], [5, 5, 5, 5]], [[-1, 1, 1, -1], [0, 0, 2, 0]])},
                                ["Valley", "Bridge Deck", "Wheel FL"], {"Wheel FL": "Car"})

    def test_identical_captures_score_perfectly(self):
        result = self.scoring.score(self.truth, self.truth, [{"prefix": "Bridge"}, {"prefix": "Car"}])
        self.assertEqual(result["mean"], {"scene_iou": 1.0, "subject_iou": 1.0, "depth_agreement": 1.0,
                                          "depth_rel_median": 0.0, "depth_rel_mean": 0.0})

    def test_silhouette_subject_and_depth_metrics(self):
        # The side left a deck pixel open, put deck in the sky in one corner and built the wheel 20 % too far.
        side = capture_of({"front": view([[3, INF, 10, INF], [5, 5, 6, 5]], [[0, -1, 0, -1], [1, 1, 2, 1]])},
                          ["Bridge Deck", "Valley", "Wheel FL"], {"Wheel FL": "Car"})
        subject = [{"prefix": "Bridge"}, {"prefix": "Car"}]
        result = self.scoring.score(self.truth, side, subject)["views"]["front"]
        # Hit pixels: truth 6, side 6, both 5, union 7.
        self.assertAlmostEqual(result["scene_iou"], round(5 / 7, 4))
        # Subject pixels (by name or parent): truth {(0,1), (0,2), (1,2)}, side {(0,0), (0,2), (1,2)} -> 2 / 4.
        self.assertAlmostEqual(result["subject_iou"], 0.5)
        # Relative depth errors over the 5 shared pixels: 0, 0, 0, 0.2, 0 -> median 0, mean 0.04.
        self.assertEqual((result["depth_rel_median"], result["depth_rel_mean"]), (0.0, 0.04))
        # Within 5 %: 4 of the 7 pixels hit by either.
        self.assertAlmostEqual(result["depth_agreement"], round(4 / 7, 4))
        self.assertEqual((result["truth_coverage"], result["side_coverage"]), (0.75, 0.75))

    def test_an_empty_side_scores_zero_and_mismatched_resolutions_are_refused(self):
        empty = capture_of({"front": view([[INF] * 4, [INF] * 4], [[-1] * 4, [-1] * 4])}, [])
        result = self.scoring.score(self.truth, empty, [{"prefix": "Bridge"}])["views"]["front"]
        self.assertEqual((result["scene_iou"], result["depth_agreement"], result["depth_rel_median"]), (0.0, 0.0, None))
        with self.assertRaises(ValueError):
            self.scoring.score(self.truth, dict(empty, resolution=[8, 4]), [])

    def test_views_missing_on_a_side_are_left_out_of_the_mean(self):
        truth = dict(self.truth, views=dict(self.truth["views"], top=self.truth["views"]["front"]))
        result = self.scoring.score(truth, self.truth, [{"prefix": "Bridge"}], views=["front", "top"])
        self.assertEqual(sorted(result["views"]), ["front"])

    def test_capture_archive_round_trip(self):
        data = {"resolution": [4, 2], "names": ["Valley", "Car Wheel FL"], "parents": {"Car Wheel FL": "Car"},
                "views": {"hero": {"depth": [INF, 1, 2, 3, 4, 5, 6, 7], "ids": [-1, 0, 0, 1, 1, 0, 0, 0]}}}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "capture.npz")
            capture.save_capture(path, data)
            loaded = self.scoring.load(path)
        self.assertEqual((loaded["names"], loaded["parents"], loaded["resolution"]),
                         (data["names"], data["parents"], [4, 2]))
        self.assertEqual(loaded["views"]["hero"]["depth"].shape, (2, 4))
        self.assertTrue(numpy.isinf(loaded["views"]["hero"]["depth"][0, 0]))
        self.assertEqual(int(loaded["views"]["hero"]["ids"][0, 3]), 1)

    def test_silhouette_overlap_image(self):
        side = capture_of({"front": view([[INF, 10, INF, 4], [5, 5, 5, 5]], [[-1, 1, -1, 0], [0, 0, 2, 0]])},
                          ["Valley", "Bridge Deck", "Car Wheel FL"])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "overlap.png")
            self.scoring.silhouette_png(path, self.truth["views"]["front"], side["views"]["front"])
            size, pixels = read_png(path)
        self.assertEqual(size, (4, 2))
        both, truth_only, side_only, neither = (235, 235, 235), (240, 140, 40), (60, 140, 240), (24, 24, 28)
        self.assertEqual(pixels[0], [neither, both, truth_only, side_only])
        self.assertEqual(pixels[1], [both] * 4)


class RayLayoutTests(unittest.TestCase):
    def rays(self, spec, width=4, height=2):
        with mock.patch.dict(sys.modules, {"mathutils": FAKE_MATHUTILS}):
            return capture.rays(spec, width, height)

    def test_orthographic_rays_cover_the_scale_with_row_zero_on_top(self):
        spec = {"location": (0.0, -60.0, 0.0), "target": (0.0, 0.0, 0.0), "ortho_scale": 8.0}
        rays = self.rays(spec)
        self.assertEqual(len(rays), 8)
        top_left, bottom_right = rays[0], rays[-1]
        self.assertEqual(list(top_left[1]), [0.0, 1.0, 0.0])   # Every ray looks along +Y.
        self.assertAlmostEqual(top_left[0].x, -3.0)              # Pixel centres: (0.5 / 4 - 0.5) * 8.
        self.assertAlmostEqual(top_left[0].z, 1.0)               # Rows span the height share of the scale.
        self.assertAlmostEqual(bottom_right[0].x, 3.0)
        self.assertAlmostEqual(bottom_right[0].z, -1.0)

    def test_perspective_rays_fan_out_by_the_lens(self):
        spec = {"location": (0.0, 0.0, 0.0), "target": (0.0, 10.0, 0.0), "lens": 18.0}
        rays = self.rays(spec, width=2, height=1)
        left, right = rays[0][1], rays[1][1]
        # 18 mm on a 36 mm sensor: 90 degrees across; the two pixel centres sit at +/- half of it.
        self.assertAlmostEqual(left.x / left.y, -0.5)
        self.assertAlmostEqual(right.x / right.y, 0.5)
        self.assertAlmostEqual(left.length, 1.0)

    def test_straight_down_views_keep_plus_y_up(self):
        spec = {"location": (0.0, 0.0, 60.0), "target": (0.0, 0.0, 0.0), "ortho_scale": 4.0}
        top_left = self.rays(spec, width=2, height=2)[0][0]
        self.assertLess(top_left.x, 0.0)
        self.assertGreater(top_left.y, 0.0)


@unittest.skipIf(numpy is None, "NumPy is installed in CI and bundled with Blender")
class ManifestTests(unittest.TestCase):
    def test_meta_is_plain_json(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "c.npz")
            capture.save_capture(path, {"resolution": [1, 1], "names": [], "parents": {},
                                        "views": {"top": {"depth": [INF], "ids": [-1]}}})
            with numpy.load(path) as archive:
                self.assertEqual(json.loads(bytes(archive["meta"]).decode())["resolution"], [1, 1])


if __name__ == "__main__":
    unittest.main()
