# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host contract tests for angelo_view's reference comparisons (OVERLAY, OUTLINE) and MATCAP and CURVATURE looks.

Covered without Blender: the subject of a picture against its plain background or by its transparency, connected
regions and holes against a plain flood fill, outline distances against shapes whose distances are known, the
closing of strands, the encoders' pictures and records, and the contract (validation naming the field, facts
and not advice). test_blender_view_outline.py proves the targets' silhouettes and the looks in the compiled app
against Blender's own drawing and projection.
"""

import importlib.util
import json
import struct
import sys
import unittest
import uuid
import zlib
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.tool_specs import TOOL_MAP, native_plan_schema, validate_call  # noqa: E402
from angelo_agent.view_spec import COMPARES, SHADINGS, VIEWS_NOTE  # noqa: E402

try:
    import numpy
except ImportError:  # CI installs it (run_host_tests --max-skips 0).
    numpy = None

FRONT = {"location": [0.0, -2.0, 0.0], "look_at": [0.0, 0.0, 0.0], "ortho_scale": 1.0}
HANDLE = "scn:" + "b" * 32 + ":11"


def views(*items, **extra):
    return validate_call("angelo_view", dict({"views": list(items)}, **extra))


def disc(size, centre, radius):
    rows, cols = numpy.mgrid[0:size, 0:size]
    return (cols - centre[0]) ** 2 + (rows - centre[1]) ** 2 < radius ** 2


def flood_regions(mask):
    """4-connected regions by a plain breadth-first flood fill: the reference the run-based labels are tested on."""
    seen, regions = numpy.zeros_like(mask), []
    for start in zip(*numpy.nonzero(mask)):
        if seen[start]:
            continue
        region, queue = [], deque([start])
        seen[start] = True
        while queue:
            row, col = queue.popleft()
            region.append((row, col))
            for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                r, c = row + dr, col + dc
                if 0 <= r < mask.shape[0] and 0 <= c < mask.shape[1] and mask[r, c] and not seen[r, c]:
                    seen[r, c] = True
                    queue.append((r, c))
        regions.append(region)
    return regions


def png_rows(png):
    width, height = struct.unpack(">II", png[16:24])
    offset, data = 8, b""
    while offset < len(png):
        length = struct.unpack(">I", png[offset:offset + 4])[0]
        if png[offset + 4:offset + 8] == b"IDAT":
            data += png[offset + 8:offset + 8 + length]
        offset += 12 + length
    channels = len(zlib.decompress(data)) // height - 1
    raw = numpy.frombuffer(zlib.decompress(data), numpy.uint8).reshape(height, channels + 1)
    return raw[:, 1:].reshape(height, width, channels // width)


@unittest.skipUnless(numpy is not None, "NumPy is not installed")
class OutlineTests(unittest.TestCase):
    def test_regions_and_holes_equal_a_plain_flood_fill(self):
        from angelo_agent.view_outline import fill_holes, largest_region
        rng = numpy.random.default_rng(11)
        for trial in range(40):
            mask = rng.random((23, 31)) < rng.uniform(0.3, 0.7)
            with self.subTest(trial=trial):
                regions = flood_regions(mask)
                want = numpy.zeros_like(mask)
                if regions:
                    for row, col in max(regions, key=len):
                        want[row, col] = True
                got = largest_region(mask)
                self.assertEqual(got.sum(), want.sum())   # Ties may pick another region of the same size.
                self.assertTrue(numpy.array_equal(got, want) or len(
                    [r for r in regions if len(r) == len(max(regions, key=len))]) > 1)
                holes = numpy.zeros_like(mask)
                for region in flood_regions(~mask):
                    if not any(r in (0, mask.shape[0] - 1) or c in (0, mask.shape[1] - 1) for r, c in region):
                        for row, col in region:
                            holes[row, col] = True
                self.assertTrue(numpy.array_equal(fill_holes(mask), mask | holes))

    def test_distances_of_shapes_whose_distances_are_known(self):
        from angelo_agent.view_outline import compare
        found = compare(disc(400, (200, 200), 100), disc(400, (206, 200), 110), pixel_world=0.002)
        # Concentric radii 100 and 110 offset by 6: edge gaps run from 4 (left) to 16 (right).
        self.assertTrue(3.0 <= found["mean_px"] - 6.0 <= 5.0)
        self.assertAlmostEqual(found["max_px"], 16.0, delta=1.0)
        self.assertAlmostEqual(found["centre_offset_px"][0], 6.0, delta=0.05)
        self.assertAlmostEqual(found["centre_offset_px"][1], 0.0, delta=0.05)
        self.assertAlmostEqual(found["size_ratio"], 1.1, delta=0.005)
        self.assertAlmostEqual(found["max_world"], found["max_px"] * 0.002, places=6)
        same = compare(disc(300, (150, 150), 80), disc(300, (150, 150), 80))
        self.assertEqual((same["iou"], same["max_px"], same["size_ratio"]), (1.0, 0.0, 1.0))
        self.assertNotIn("mean_world", same)   # No pixel size given, no world units claimed.
        json.dumps(found)   # Plain numbers only.

    def test_an_empty_side_is_stated_not_measured(self):
        from angelo_agent.view_outline import compare
        empty, shape = numpy.zeros((50, 50), bool), disc(50, (25, 25), 10)
        self.assertEqual(compare(empty, shape)["reason"], "no subject found in the picture")
        self.assertEqual(compare(shape, empty)["reason"], "the targets are not in this view")
        self.assertFalse(compare(shape, empty)["compared"])

    def test_a_subject_on_a_plain_background_holes_filled_specks_left_out(self):
        from angelo_agent.view_outline import normalized, subject_mask
        head = disc(256, (128, 140), 80)
        picture = numpy.full((256, 256, 3), 230, numpy.int16)
        picture[head] = (190, 140, 120)
        picture[disc(256, (100, 120), 12)] = 230                  # An eye the background's colour: a hole.
        picture[disc(256, (230, 20), 3)] = (10, 10, 10)            # A speck (under 0.1% of the picture) apart.
        picture += numpy.random.default_rng(2).integers(-3, 4, picture.shape)
        mask, facts = subject_mask(numpy.clip(picture, 0, 255).astype(numpy.uint8))
        self.assertTrue(numpy.array_equal(normalized(mask), head))
        self.assertEqual(facts["background"], "PLAIN")
        busy = numpy.random.default_rng(4).integers(0, 256, (256, 256, 3)).astype(numpy.uint8)
        self.assertEqual(subject_mask(busy)[1]["background"], "BUSY")

    def test_a_second_colour_on_the_border_is_ambiguous_not_plain(self):
        """A head and shoulders whose shirt covers most of the border: the majority colour (the shirt) is taken,
        and the result says the background is ambiguous instead of calling it plain."""
        from angelo_agent.view_outline import subject_mask
        picture = numpy.full((200, 200, 3), (40, 45, 60), numpy.uint8)     # The wall.
        rows = numpy.mgrid[0:200, 0:200][0]
        picture[rows > 60] = (200, 30, 30)                                  # A shirt: bottom and most of the sides.
        picture[disc(200, (100, 60), 35)] = (210, 170, 150)
        _mask, facts = subject_mask(picture)
        self.assertEqual(facts["background"], "AMBIGUOUS")
        self.assertGreater(facts["background_share_of_border"], 0.5)

    def test_closing_joins_strands_and_reaches_no_farther_than_its_radius(self):
        from angelo_agent.view_outline import close, edges
        strands = numpy.zeros((80, 80), bool)
        strands[10:70:4, 20:60] = True                             # Lines 3 pixels apart.
        closed = close(strands, 3)
        self.assertTrue(closed[10:67, 23:57].all())
        grown = numpy.zeros_like(strands)
        for dy in range(-3, 4):
            for dx in range(-3, 4):
                grown |= numpy.roll(numpy.roll(strands, dy, 0), dx, 1)
        self.assertFalse((closed & ~grown).any())
        ring = edges(disc(40, (20, 20), 6))
        self.assertTrue(ring.any() and not ring[20, 20] and (ring <= disc(40, (20, 20), 6)).all())


@unittest.skipUnless(numpy is not None, "NumPy is not installed")
class GapTests(unittest.TestCase):
    """Where outlines differ, against an orthographic free camera's own matrices (view_framing.free_camera)."""

    def setUp(self):
        from angelo_agent.view_framing import free_camera
        self.size = 200
        self.camera = free_camera({"location": [0.0, -2.0, 0.0], "look_at": [0.0, 0.0, 0.0], "ortho_scale": 2.0},
                                  1.0, 1.0, 3.0, False, True)
        self.pixel = 2.0 / self.size
        self.depth = numpy.ones((self.size, self.size), numpy.float32)

    def test_a_shifted_shape_gives_its_shift_where_it_reaches_past(self):
        from angelo_agent.view_outline import gaps, unproject
        target, picture = disc(self.size, (100, 100), 40), disc(self.size, (88, 100), 40)  # The picture 12 px left.
        self.depth[target] = 0.5
        found = gaps(picture, target, self.depth, self.camera)
        self.assertTrue(1 <= len(found) <= 6)
        # Both sides are 12 px apart: the gaps of the largest size tie, and gaps() promises no order among ties (the
        # first one differed between x86-64 and arm64). The left one, where the picture reaches past them, is checked.
        largest = max(gap["gap_px"] for gap in found)
        tied = [gap for gap in found if gap["gap_px"] == largest]
        self.assertIn(found[0], tied)
        outward = [gap for gap in tied if gap["direction"] == "OUT"]
        self.assertTrue(outward, "the left place, where the picture reaches past them, is among the largest gaps")
        worst = outward[0]
        self.assertAlmostEqual(worst["gap_px"], 12.0, delta=1.0)
        self.assertAlmostEqual(worst["offset_world"][0], -12 * self.pixel, delta=self.pixel)   # Toward -X.
        self.assertAlmostEqual(worst["offset_world"][2], 0.0, delta=self.pixel)
        self.assertAlmostEqual(worst["gap_world"], 12 * self.pixel, delta=self.pixel)
        column, row = worst["targets_edge_px"]
        self.assertTrue(target[row, column])
        # The surface point is the pixel's own point at its depth: x and z from the pixel, y from the depth.
        expected = unproject(self.camera, numpy.array([[column, row]]), self.depth)[0]
        self.assertTrue(numpy.allclose(worst["surface_world"], expected, atol=1e-4))
        self.assertAlmostEqual(worst["surface_world"][0], (column + 0.5 - 100) * self.pixel, delta=1e-6)
        self.assertAlmostEqual(worst["surface_world"][2], (100 - row - 0.5) * self.pixel, delta=1e-6)
        inward = [gap for gap in found if gap["direction"] == "IN"]
        self.assertTrue(inward and inward[0]["offset_world"][0] < 0)    # Right: they reach past it, pulled back -X.

    def test_gaps_are_spread_bounded_and_none_where_the_outlines_agree(self):
        from angelo_agent.view_outline import MAX_GAPS, gaps
        target = disc(self.size, (100, 100), 60)
        self.depth[target] = 0.5
        self.assertEqual(gaps(target, target, self.depth, self.camera), [])
        bumpy = target | disc(self.size, (100, 36), 12) | disc(self.size, (164, 100), 12) | disc(self.size, (36, 100), 9)
        found = gaps(bumpy, target, self.depth, self.camera)
        self.assertLessEqual(len(found), MAX_GAPS)
        places = [tuple(gap["picture_edge_px"]) for gap in found]
        for a in range(len(places)):
            for b in range(a + 1, len(places)):
                self.assertGreaterEqual(max(abs(places[a][0] - places[b][0]), abs(places[a][1] - places[b][1])), 8)
        self.assertEqual({gap["direction"] for gap in found}, {"OUT"})
        self.assertEqual([gap["gap_px"] for gap in found], sorted((gap["gap_px"] for gap in found), reverse=True))

    def test_a_closed_in_edge_takes_the_nearest_drawn_depth(self):
        from angelo_agent.view_outline import gaps, unproject
        target = disc(self.size, (100, 100), 40)
        self.depth[disc(self.size, (100, 100), 37)] = 0.25          # The outer ring only closed, never drawn.
        found = gaps(disc(self.size, (85, 100), 40), target, self.depth, self.camera)
        self.assertTrue(found)
        for gap in found:   # The edge it names was drawn, and its point is at that depth, not on the far plane.
            column, row = gap["targets_edge_px"]
            self.assertEqual(self.depth[row, column], 0.25)
            self.assertTrue(numpy.allclose(gap["surface_world"],
                                           unproject(self.camera, numpy.array([[column, row]]), self.depth)[0],
                                           atol=1e-4))


@unittest.skipUnless(numpy is not None, "NumPy is not installed")
class EncoderTests(unittest.TestCase):
    def setUp(self):
        self.size = 64
        self.reference = numpy.full((self.size, self.size, 4), 240, numpy.uint8)
        self.reference[disc(self.size, (30, 32), 18)] = (120, 90, 80, 255)
        self.drawn = numpy.full((self.size, self.size, 4), 13, numpy.uint8)   # Bottom row first.
        self.drawn[..., 3] = 255

    def test_overlay_is_the_image_and_the_view_averaged(self):
        from angelo_agent.view_compare import encoder, pair_record, picture_size
        rows = png_rows(encoder(self.reference, "OVERLAY")(self.drawn.tobytes(), self.size, self.size))
        want = (self.reference[..., :3].astype(int) + 13) // 2
        self.assertTrue(numpy.array_equal(rows[..., :3], want))
        self.assertEqual(picture_size(self.size, self.size, "OVERLAY"), (self.size, self.size))
        record = pair_record({"id": "x", "source_size": [64, 64]}, self.size, self.size, "OVERLAY")
        self.assertEqual((record["layout"], record["width"]), ("REFERENCE_OVER_VIEW", self.size))

    def test_outline_paints_both_outlines_and_states_the_comparison(self):
        from angelo_agent.view_compare import REFERENCE_RGB, TARGET_RGB, encoder
        target, facts = disc(self.size, (34, 32), 18), {"pixel_world": 0.01}
        rows = png_rows(encoder(self.reference, "OUTLINE", None, target, facts)(
            self.drawn.tobytes(), self.size, self.size))
        colours = {tuple(pixel) for pixel in rows[..., :3].reshape(-1, 3).tolist()}
        self.assertIn(REFERENCE_RGB, colours)
        self.assertIn(TARGET_RGB, colours)
        self.assertEqual((facts["reference"]["subject_from"], facts["pixel_world_at"]), ("PLAIN_BACKGROUND", "LOOK_AT"))
        self.assertAlmostEqual(facts["centre_offset_px"][0], 4.0, delta=0.1)
        self.assertAlmostEqual(facts["mean_world"], facts["mean_px"] * 0.01, places=5)

    def test_a_cut_out_is_outlined_by_its_transparency_whatever_its_colours(self):
        from angelo_agent.view_compare import encoder
        noise = numpy.random.default_rng(5).integers(0, 256, (self.size, self.size, 4)).astype(numpy.uint8)
        alpha = numpy.where(disc(self.size, (32, 32), 20), 255, 0).astype(numpy.uint8)
        facts = {}
        encoder(noise, "OUTLINE", alpha, disc(self.size, (32, 32), 20), facts)(self.drawn.tobytes(), 64, 64)
        self.assertEqual((facts["reference"]["subject_from"], facts["iou"]), ("TRANSPARENCY", 1.0))

    def test_records_notes_and_uncertainty(self):
        from angelo_agent.view_compare import note, notes, picture_size
        self.assertEqual(picture_size(10, 6), (22, 6))
        plans = [{"pair": {}, "item": dict(FRONT, compare=compare)} for compare in ("OVERLAY", "OUTLINE")]
        text = note(plans)
        self.assertIn("half opacity", text)
        self.assertIn("outlined orange", text)
        self.assertNotIn("attached image on the left", text)
        plans[1]["outline"] = {"not_drawn": 2, "particle_hair_cut_at_points": 5,
                               "reference": {"subject_from": "BUSY_BACKGROUND", "touches_border": True}}
        self.assertEqual([found["code"] for found in notes(plans)],
                         ["reference_background_busy", "outline_particle_hair_cut", "reference_subject_cropped",
                          "outline_targets_not_drawn"])
        plans[1]["outline"]["reference"] = {"subject_from": "AMBIGUOUS_BACKGROUND", "background_share_of_border": 0.62}
        ambiguous = notes(plans)[0]
        self.assertEqual(ambiguous["code"], "reference_background_ambiguous")
        self.assertIn("38%", ambiguous["detail"])

    def test_a_pair_is_fitted_to_a_deliverable_picture(self):
        """At 1024 a side-by-side pair was 2,050 px wide and could pass the 5 MiB image limit."""
        from angelo_agent.view_compare import GAP, MAX_PICTURE_PIXELS, MAX_PICTURE_SIDE, fitted, picture_size
        from angelo_agent.view_png import encode_png
        for size in ((1024, 1024), (1024, 576), (576, 1024), (512, 512)):
            width, height = fitted(*size, dict(FRONT))
            picture = picture_size(width, height)
            with self.subTest(size=size):
                self.assertLessEqual(picture[0], MAX_PICTURE_SIDE)
                self.assertLessEqual(picture[0] * picture[1], MAX_PICTURE_PIXELS + 2 * GAP * height)
                self.assertAlmostEqual(width / height, size[0] / size[1], delta=0.01)
        self.assertEqual(fitted(512, 512, dict(FRONT)), (512, 512))           # Already deliverable: unchanged.
        self.assertEqual(fitted(1024, 1024, dict(FRONT, compare="OUTLINE")), (1024, 1024))   # One view's size.
        width, height = picture_size(*fitted(1024, 1024, dict(FRONT)))
        noise = numpy.random.default_rng(0).integers(0, 256, (height, width, 4)).astype(numpy.uint8)
        self.assertLess(len(encode_png(noise.tobytes(), width, height)), 5 * 1024 * 1024)   # Even incompressible.


@unittest.skipUnless(numpy is not None, "NumPy is not installed")
class DepthTests(unittest.TestCase):
    """DEPTH's distances against free cameras' own matrices (view_framing.free_camera), both projections."""

    def camera(self, **lens):
        from angelo_agent.view_framing import free_camera
        return free_camera(dict({"location": [0.0, -4.0, 0.0], "look_at": [0.0, 0.0, 0.0]}, **lens), 1.0, 2.0, 6.0,
                           False, True)

    def window_depth(self, camera, distance):
        """The window depth (0-1) a point straight ahead at ``distance`` gets from this camera's projection."""
        projection = numpy.array(camera["projection_matrix"], dtype=numpy.float64)
        clip = projection @ numpy.array([0.0, 0.0, -distance, 1.0])
        return (clip[2] / clip[3] + 1.0) / 2.0

    def test_distances_are_along_the_view_for_either_projection(self):
        from angelo_agent.view_depth import distances
        for lens in ({"lens": 50.0}, {"ortho_scale": 3.0}):
            with self.subTest(lens=lens):
                camera = self.camera(**lens)
                depth = numpy.ones((40, 40), numpy.float32)
                depth[10:20, 10:20] = self.window_depth(camera, 3.5)
                depth[25:30, 25:30] = self.window_depth(camera, 4.25)
                found = distances(depth, camera)
                self.assertTrue(numpy.isnan(found[0, 0]))
                # Perspective depth along the view is the same across the frame; float32 depth limits precision.
                self.assertTrue(numpy.allclose(found[10:20, 10:20], 3.5, atol=2e-3))
                self.assertTrue(numpy.allclose(found[25:30, 25:30], 4.25, atol=2e-3))

    def test_the_picture_is_white_nearest_dark_grey_farthest_and_black_where_nothing_is(self):
        from angelo_agent.view_depth import FARTHEST, picture
        camera = self.camera(ortho_scale=3.0)
        depth = numpy.ones((8, 8), numpy.float32)
        depth[0, 0], depth[1, 1] = self.window_depth(camera, 3.0), self.window_depth(camera, 5.0)
        rgba, facts = picture(depth, camera)
        rows = numpy.frombuffer(rgba, numpy.uint8).reshape(8, 8, 4)[::-1]   # Encoded bottom row first.
        self.assertEqual((rows[0, 0, 0], rows[1, 1, 0], rows[5, 5, 0]), (255, FARTHEST, 0))
        self.assertAlmostEqual(facts["near_world"], 3.0, delta=2e-3)
        self.assertAlmostEqual(facts["far_world"], 5.0, delta=2e-3)
        empty = picture(numpy.ones((4, 4), numpy.float32), camera)[1]
        self.assertEqual(empty, {"drawn_share": 0.0})

    def test_depth_needs_targets_and_is_never_a_strip(self):
        with self.assertRaises(ValueError) as caught:
            views("FRONT", shading="DEPTH")
        self.assertIn("pass their observed handles", str(caught.exception))
        self.assertEqual(views("FRONT", shading="DEPTH", targets=[HANDLE])["shading"], "DEPTH")
        with self.assertRaises(ValueError):
            views("FRONT", shading="DEPTH", targets=[HANDLE], frames=[1, 2])


@unittest.skipUnless(numpy is not None, "NumPy is not installed")
class SymmetryTests(unittest.TestCase):
    """The same shape on both sides compares as the same, whatever its topology."""

    size = 256
    camera = {"view_matrix": numpy.eye(4).tolist() if numpy is not None else None,
              "projection_matrix": [[2.0, 0, 0, 0], [0, 2.0, 0, 0], [0, 0, -1.0, 0], [0, 0, 0, 1.0]]}

    def shapes(self):
        rows, columns = numpy.mgrid[0:self.size, 0:self.size]
        radius = numpy.hypot(rows - 128, columns - 128)
        comb = (numpy.abs(rows - 128) < 40) & (numpy.abs(columns - 128) < 90) & ~(
            (rows > 100) & (columns % 6 < 2))                                    # Slots 2 px wide.
        strands = numpy.zeros((self.size, self.size), bool)
        strands[60:200:4, 40:220] = True                                          # Hair-like lines, 3 px apart.
        return {"torus": (radius < 90) & (radius > 40), "two_parts": disc(self.size, (80, 128), 40)
                | disc(self.size, (200, 128), 30), "comb": comb, "strands": strands, "disk": radius < 90}

    def outline(self, shape, cut_out):
        from angelo_agent.view_compare import outlined
        rgba = numpy.zeros((self.size, self.size, 4), numpy.uint8)
        rgba[...] = 13
        rgba[shape] = 180
        rgba[..., 3] = 255
        facts = {}
        outlined(rgba.copy(), rgba, numpy.where(shape, 255, 0).astype(numpy.uint8) if cut_out else None,
                 shape.copy(), facts, numpy.where(shape, 0.5, 1.0).astype(numpy.float32), self.camera)
        return facts

    def test_identical_shapes_compare_identical_by_colour_and_by_transparency(self):
        for name, shape in self.shapes().items():
            for cut_out in (False, True):
                with self.subTest(shape=name, cut_out=cut_out):
                    facts = self.outline(shape, cut_out)
                    self.assertEqual((facts["iou"], facts["max_px"], facts["gaps"]), (1.0, 0.0, []))
                    self.assertEqual(facts["reference"]["holes"], "COMPARED" if cut_out else "FILLED")

    def test_a_cut_out_compares_holes_and_a_colour_picture_does_not(self):
        from angelo_agent.view_compare import outlined
        shapes = self.shapes()
        rgba = numpy.full((self.size, self.size, 4), 13, numpy.uint8)
        rgba[..., 3] = 255
        rgba[shapes["disk"]] = 180
        alpha = numpy.where(shapes["disk"], 255, 0).astype(numpy.uint8)
        facts = {}   # The picture is a solid disk; the targets are the torus (a hole the picture does not have).
        outlined(rgba.copy(), rgba, alpha, shapes["torus"], facts,
                 numpy.where(shapes["torus"], 0.5, 1.0).astype(numpy.float32), self.camera)
        self.assertLess(facts["iou"], 0.9)
        facts = {}
        outlined(rgba.copy(), rgba, None, shapes["torus"], facts,
                 numpy.where(shapes["torus"], 0.5, 1.0).astype(numpy.float32), self.camera)
        self.assertEqual(facts["iou"], 1.0)   # Holes filled on both: a colour picture cannot tell a hole.

    def test_gaps_are_found_from_both_outlines(self):
        from angelo_agent.view_outline import gaps
        rows, columns = numpy.mgrid[0:self.size, 0:self.size]
        body = disc(self.size, (128, 150), 60)
        horn = body | ((numpy.abs(columns - 128) < 3) & (rows > 40) & (rows < 100))   # 50 px, 5 px wide.
        prop = body | disc(self.size, (230, 40), 12)
        for name, picture, target, direction in (("horn on the targets", body, horn, "IN"),
                                                 ("horn on the picture", horn, body, "OUT"),
                                                 ("a prop only the targets have", body, prop, "IN")):
            with self.subTest(name):
                found = gaps(picture, target, numpy.where(target, 0.5, 1.0).astype(numpy.float32), self.camera)
                self.assertTrue(found and found[0]["direction"] == direction and found[0]["gap_px"] > 40)

    def test_detailed_outlines_are_compared_in_bounded_memory(self):
        import time
        import tracemalloc
        from angelo_agent.view_outline import MAX_EDGE_POINTS, compare
        size = 1024
        rows, columns = numpy.mgrid[0:size, 0:size]
        angle = numpy.arctan2(rows - 512, columns - 512)
        radius = numpy.hypot(rows - 512, columns - 512)
        furry = (radius < 380) | ((radius < 470) & (numpy.sin(angle * 720) > 0.6))   # Thousands of thin spikes.
        tracemalloc.start()
        started = time.perf_counter()
        found = compare(furry, numpy.roll(furry, 3, axis=1))
        seconds = time.perf_counter() - started
        peak = tracemalloc.get_traced_memory()[1] / 2 ** 20
        tracemalloc.stop()
        self.assertGreater(numpy.count_nonzero(furry), MAX_EDGE_POINTS)
        self.assertGreaterEqual(found["measured_every_px"], 2)   # Stated, not hidden.
        self.assertLess(peak, 200)
        self.assertLess(seconds, 5)


class ContractTests(unittest.TestCase):
    def test_compare_needs_a_reference_and_outline_needs_targets(self):
        reference = str(uuid.uuid4())
        for item, extra, words in (
                (dict(FRONT, compare="OVERLAY"), {}, "give reference too"),
                (dict(FRONT, reference=reference, compare="SIDE"), {}, "views[0].compare is PAIR"),
                (dict(FRONT, reference=reference, compare=["OUTLINE"]), {}, "views[0].compare is PAIR"),
                (dict(FRONT, reference=reference, compare="OUTLINE"), {}, "outlines the targets")):
            with self.subTest(item=item), self.assertRaises(ValueError) as caught:
                views(item, **extra)
            self.assertIn(words, str(caught.exception))
        with self.assertRaises(ValueError):   # -0.0 is 0.0: the same camera.
            views(dict(FRONT, reference=reference), dict(FRONT, location=[-0.0, -2.0, 0.0], reference=reference))
        checked = views(dict(FRONT, reference=reference, compare="OUTLINE"), targets=[HANDLE])
        self.assertEqual(checked["views"][0]["compare"], "OUTLINE")
        self.assertNotIn("compare", views(dict(FRONT, reference=reference, compare="PAIR"))["views"][0])
        two = views(dict(FRONT, reference=reference), dict(FRONT, reference=reference, compare="OVERLAY"))
        self.assertEqual(len(two["views"]), 2)   # The same camera and image, shown two ways: not repeated.

    def test_every_runtime_reads_compare_and_the_new_looks(self):
        schema = TOOL_MAP["angelo_view"]["inputSchema"]["properties"]
        camera = schema["views"]["items"]["anyOf"][2]["properties"]
        self.assertEqual(camera["compare"]["enum"], list(COMPARES))
        for shading in ("MATCAP", "CURVATURE"):
            self.assertIn(shading, schema["shading"]["enum"])
            self.assertIn(shading, TOOL_MAP["angelo_view"]["description"])
            self.assertEqual(views("FRONT", shading=shading)["shading"], shading)
        plan = json.dumps(native_plan_schema())
        self.assertIn('"compare"', plan)
        self.assertIn(json.dumps(VIEWS_NOTE), plan)
        with self.assertRaises(ValueError) as caught:
            views("FRONT", shading="CLAY")
        self.assertIn("MATCAP, CURVATURE", str(caught.exception))

    def test_the_note_states_facts_not_advice(self):
        for word in ("use ", "prefer", "should", "try ", "when you", "to match", "compare it"):
            self.assertNotIn(word, VIEWS_NOTE.lower())
        for fact in ("OVERLAY", "OUTLINE (targets)", "half over the view", "outlines and their distances"):
            self.assertIn(fact, VIEWS_NOTE)

    def test_looks_set_their_type_first_and_view_capture_uses_them(self):
        from angelo_agent import view_looks
        from angelo_agent.view_looks import SHADING, display_keys
        capture = (Path(view_looks.__file__).parent / "view_capture.py").read_text()
        self.assertIn("from .view_looks import", capture)   # view_capture draws these very looks (bpy: not here).
        self.assertNotIn("\nSHADING = ", capture)
        for shading in set(SHADINGS) - {"RENDERED", "WEIGHT", "DEPTH"}:   # Drawn by the host, not the viewport.
            with self.subTest(shading=shading):
                self.assertEqual(SHADING[shading][0][0], "type")
                self.assertIn(("View3DShading", "type"), display_keys(shading))
        names = [name for name, _value in SHADING["MATCAP"]]
        self.assertLess(names.index("light"), names.index("studio_light"))   # The matcap list, not the studio one.
        self.assertIn(("show_cavity", True), SHADING["CURVATURE"])

    @unittest.skipUnless(importlib.util.find_spec("jsonschema"), "jsonschema is not installed")
    def test_the_plan_schema_accepts_comparisons(self):
        import jsonschema
        jsonschema.validate({"operations": [{"tool": "angelo_view", "arguments": {
            "targets": ["o1"], "views": [dict(FRONT, reference=str(uuid.uuid4()), compare="OUTLINE")],
            "shading": "MATCAP"}}]}, native_plan_schema())


if __name__ == "__main__":
    unittest.main()
