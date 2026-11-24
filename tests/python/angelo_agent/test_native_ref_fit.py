# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The reference board and its measured fit without Blender (doc/angelo/reference_fit.rst): views with their solved
cameras, the drawing as a mask and as strokes, and per-view scores of a silhouette against the drawing, in pixels
and millimetres, with a pass/fail only where a threshold is set.

Shapes are drawn here (ellipses, boxes, an L), several unlike ones, so no assertion depends on one character. The
outline maths is ``view_outline``'s (shared with angelo_view's compare); this pins the board's contract that hair
fitting (v210-hair-toon) and the character checks build on."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import ref_board, ref_fit  # noqa: E402

W, H = 240, 320
SCALE = 2.4   # Orthographic width in world units: one pixel is 0.01 units.


def ortho_camera(scale=SCALE, width=W, height=H):
    projection = np.eye(4)
    projection[0, 0] = 2.0 / scale
    projection[1, 1] = 2.0 / (scale * height / width)
    projection[2, 2] = -2.0 / 100.0
    projection[2, 3] = -1.0
    view = np.eye(4)
    view[2, 3] = -10.0
    return {"view_matrix": view.tolist(), "projection_matrix": projection.tolist(),
            "details": {"ortho_scale": scale}}


def ellipse(cx=120, cy=160, rx=60, ry=110, shape=(H, W)):
    rows, columns = np.mgrid[:shape[0], :shape[1]]
    return ((columns - cx) / rx) ** 2 + ((rows - cy) / ry) ** 2 <= 1.0


def box(top, left, bottom, right, shape=(H, W)):
    mask = np.zeros(shape, bool)
    mask[top:bottom, left:right] = True
    return mask


def picture(mask, alpha=False, line=None):
    """An RGB(A) drawing: a dark grey subject on white, or a cut-out; ``line`` draws black outline pixels."""
    rgb = np.full(mask.shape + (3,), 255, np.uint8)
    rgb[mask] = (90, 90, 90)
    if line is not None:
        rgb[line] = (0, 0, 0)
    if not alpha:
        return rgb
    return np.dstack((rgb, np.where(mask, 255, 0).astype(np.uint8)))


def board(views, defaults=None, unit_mm=1000.0):
    data = {"schema": 1, "unit_mm": unit_mm, "defaults": defaults or {}, "views": []}
    images = {}
    for view_id, image, extra in views:
        entry = {"id": view_id, "size": [W, H], "camera": ortho_camera(),
                 "image": {"path": "refs/%s.png" % view_id, "sha256": "0" * 64}}
        entry.update(extra)
        data["views"].append(entry)
        images[view_id] = image
    return ref_board.Board.from_dict(data, images)


class BoardTests(unittest.TestCase):
    def test_views_cameras_and_sizes(self):
        shape = ellipse()
        made = board([("front", picture(shape), {}), ("side", picture(shape), {})])
        self.assertEqual(made.view_ids(), ["front", "side"])
        self.assertEqual(made.size("side"), (W, H))
        camera = made.camera("front")
        self.assertEqual(set(camera), {"view_matrix", "projection_matrix", "details"})
        camera["details"]["ortho_scale"] = 99   # A copy: the board is never changed through it.
        self.assertEqual(made.camera("front")["details"]["ortho_scale"], SCALE)
        self.assertEqual(made.unit_mm, 1000.0)
        with self.assertRaises(KeyError):
            made.camera("back")

    def test_the_mask_comes_from_alpha_or_the_plain_background(self):
        for shape in (ellipse(), box(40, 30, 300, 200)):
            for alpha in (False, True):
                made = board([("front", picture(shape, alpha=alpha), {})])
                found = made.mask("front")
                self.assertEqual(found.shape, (H, W))
                self.assertGreater(np.count_nonzero(found & shape) / np.count_nonzero(found | shape), 0.98)

    def test_strokes_are_the_drawing_lines_before_the_fill(self):
        shape = box(40, 30, 300, 200)
        line = shape & ~box(42, 32, 298, 198)   # A 2-pixel outline drawn in black.
        for alpha in (False, True):
            strokes = board([("front", picture(shape, alpha=alpha, line=line), {})]).strokes("front")
            self.assertEqual(strokes.dtype, np.float32)
            self.assertGreater(float(strokes[line].mean()), 0.9)
            self.assertLess(float(strokes[~shape].max()), 0.05)   # The ground is no line.
            self.assertLess(float(strokes[shape & ~line].mean()), 0.7)   # The fill is lighter than a line.

    def test_thresholds_merge_view_over_board_defaults(self):
        made = board([("front", picture(ellipse()), {"thresholds": {"p95_mm_max": 5.0}}),
                      ("side", picture(ellipse()), {})], defaults={"iou_min": 0.9})
        self.assertEqual(made.thresholds("front"), {"iou_min": 0.9, "p95_mm_max": 5.0})
        self.assertEqual(made.thresholds("side"), {"iou_min": 0.9, "p95_mm_max": None})

    def test_the_dict_round_trips_without_pixels(self):
        made = board([("front", picture(ellipse()), {})], defaults={"iou_min": 0.8})
        data = made.to_dict()
        self.assertEqual(data["views"][0]["image"]["path"], "refs/front.png")
        self.assertNotIn("pixels", repr(data))
        again = ref_board.Board.from_dict(data, {"front": picture(ellipse())})
        self.assertEqual(again.to_dict(), data)

    def test_landmarks_are_named_pixels_per_view(self):
        made = board([("front", picture(ellipse()), {"landmarks": {"crown": [120, 50], "chin": [120.5, 260]}}),
                      ("side", picture(ellipse()), {})])
        self.assertEqual(made.landmarks("front"), {"crown": (120.0, 50.0), "chin": (120.5, 260.0)})
        self.assertEqual(made.landmarks("side"), {})
        for bad in ({"Crown!": [1, 2]}, {"crown": [1]}, {"crown": [W + 1, 2]}, {"crown": "top"}):
            with self.assertRaisesRegex(ref_board.BoardError, "landmarks"):
                board([("front", picture(ellipse()), {"landmarks": bad})])

    def test_the_reference_outline_is_made_once_per_view(self):
        # Hair fitting scores a board 50-200 times per fit; the drawing's side never changes, so it is kept.
        made = board([("front", picture(ellipse()), {})])
        first = made.reference_outline("front")
        self.assertIs(made.reference_outline("front"), first)
        self.assertFalse(first.flags.writeable)   # Shared between threads: nobody may change it.
        calls = []
        real = ref_fit.view_outline.normalized
        try:
            ref_fit.view_outline.normalized = lambda mask, holes=False: calls.append(1) or real(mask, holes)
            for _ in range(3):
                ref_fit.score_views(made, {"front": (ellipse(cx=125), None)})
        finally:
            ref_fit.view_outline.normalized = real
        self.assertEqual(len(calls), 3)   # The silhouette's side only, once a call.

    def test_bad_boards_are_refused_by_field(self):
        cases = [({"schema": 2}, "schema"), ({"views": [{"id": "front"}]}, "size"),
                 ({"views": [{"id": "a b", "size": [W, H], "camera": ortho_camera()}]}, "id"),
                 ({"unit_mm": -1}, "unit_mm")]
        for change, field in cases:
            data = {"schema": 1, "unit_mm": 1000.0, "defaults": {}, "views": []}
            data.update(change)
            with self.assertRaisesRegex(ref_board.BoardError, field):
                ref_board.Board.from_dict(data, {})


class ScoreTests(unittest.TestCase):
    def test_a_perfect_fit_and_a_shifted_one(self):
        reference = ellipse()
        made = board([("front", picture(reference), {})], defaults={"iou_min": 0.95, "p95_mm_max": 30.0})
        exact = ref_fit.score_views(made, {"front": (reference, None)})["views"]["front"]
        self.assertEqual((exact["iou"], exact["p95_px"], exact["passed"]), (1.0, 0.0, True))
        shifted = ref_fit.score_views(made, {"front": (ellipse(cx=130), None)})
        view = shifted["views"]["front"]
        self.assertEqual(view["centre_offset_px"], [10.0, 0.0])
        self.assertAlmostEqual(view["centre_offset_mm"][0], 10 * SCALE / W * 1000.0, places=3)
        self.assertAlmostEqual(view["p95_mm"], view["p95_px"] * SCALE / W * 1000.0, places=3)
        self.assertFalse(view["passed"])
        self.assertIn("iou_below", view["reasons"])
        self.assertEqual((shifted["passed"], shifted["failed"]), (False, ["front"]))

    def test_no_threshold_is_never_a_pass(self):
        made = board([("front", picture(ellipse()), {})])
        result = ref_fit.score_views(made, {"front": (ellipse(), None)})
        self.assertIsNone(result["views"]["front"]["passed"])
        self.assertIn("no_threshold", result["views"]["front"]["reasons"])
        self.assertIsNone(result["passed"])

    def test_a_view_without_a_silhouette_is_reported_not_skipped(self):
        made = board([("front", picture(ellipse()), {}), ("side", picture(box(40, 80, 300, 160)), {})],
                      defaults={"iou_min": 0.5})
        result = ref_fit.score_views(made, {"front": (ellipse(), None)})
        side = result["views"]["side"]
        self.assertFalse(side["compared"])
        self.assertIn("not_compared:no_silhouette", side["reasons"])
        self.assertIsNone(side["passed"])
        self.assertIsNone(result["passed"])   # One view unjudged: the board is not passed.

    def test_depth_gives_gaps_with_points_and_scale(self):
        # An L-shaped figure against a box: the missing corner is a gap, IN or OUT by which side reaches past.
        reference = box(40, 60, 300, 180)
        target = box(40, 60, 300, 120) | box(220, 60, 300, 180)
        depth = np.where(target, 0.5, 1.0).astype(np.float32)
        made = board([("front", picture(reference), {})], defaults={"p95_mm_max": 1.0})
        view = ref_fit.score_views(made, {"front": (target, depth)})["views"]["front"]
        self.assertTrue(view["gaps"])
        self.assertIn(view["gaps"][0]["direction"], ("IN", "OUT"))
        self.assertAlmostEqual(view["mm_per_px"], SCALE / W * 1000.0, places=2)
        self.assertFalse(view["passed"])
        self.assertIn("p95_over", view["reasons"])


if __name__ == "__main__":
    unittest.main()
