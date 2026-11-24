# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shape Cut's geometry and its one description of a cut (doc/angelo/shape_cut.rst), without Blender.

Covered: the surface frame (edge-aligned and world-aligned, on faces at any tilt), grid snapping, outlines (cleaned,
counter-clockwise, crossing and flat ones refused), freehand simplification, inset and outset, the closed solids
(every edge twice, outward, analytic volumes), the cut's validation and the solids each operation makes. Shapes vary
on purpose (boxes, circles, concave and rotated outlines, tilted faces) so no rule fits one case.
``test_blender_shape_cut.py`` proves the Booleans, the live cutters and apply-and-clean in Blender.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import shape_cut_geometry as geo, shape_cut_spec as spec  # noqa: E402

L_SHAPE = [(0, 0), (2, 0), (2, 1), (1, 1), (1, 2), (0, 2)]   # Concave, area 3.
TILTED = [(0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (2.0, 1.0, 1.0), (0.0, 1.0, 1.0)]   # Normal (0, -1, 1)/sqrt 2.


def close(test, a, b, places=6):
    for x, y in zip(a, b):
        test.assertAlmostEqual(x, y, places=places)


class Frames(unittest.TestCase):
    def test_edge_frame_follows_the_nearest_edge_from_its_first_corner(self):
        n = (0.0, -1.0, 1.0)
        frame = geo.frame_from_face(n, TILTED, (1.0, 0.02, 0.02))   # Next to the bottom edge.
        close(self, frame.u, (1.0, 0.0, 0.0))
        close(self, frame.origin, TILTED[0])
        frame = geo.frame_from_face(n, TILTED, (1.98, 0.5, 0.5))   # Next to the right edge.
        close(self, frame.u, (0.0, 1 / math.sqrt(2), 1 / math.sqrt(2)))
        close(self, frame.origin, TILTED[1])

    def test_frame_is_orthonormal_and_round_trips(self):
        for normal, corners, hit in (((0, -1, 1), TILTED, (0.5, 0.5, 0.5)),
                                     ((0, 0, 1), [(0, 0, 3), (4, 1, 3), (3, 5, 3)], (2, 2, 3)),
                                     ((1, 1, 1), [(1, 0, 0), (0, 1, 0), (0, 0, 1)], (0.3, 0.3, 0.4))):
            frame = geo.frame_from_face(normal, corners, hit)
            for a, b, want in ((frame.u, frame.u, 1), (frame.v, frame.v, 1), (frame.n, frame.n, 1),
                               (frame.u, frame.v, 0), (frame.u, frame.n, 0), (frame.v, frame.n, 0)):
                self.assertAlmostEqual(sum(x * y for x, y in zip(a, b)), want)
            point = (0.7, -1.3, 2.9)
            close(self, frame.to_world(*frame.to_local(point)), point)
            self.assertAlmostEqual(frame.to_local(hit)[2], 0.0)   # The hit lies on the frame's plane.

    def test_world_frame_uses_the_scene_grid(self):
        flat = geo.frame_from_face((0, 0, 1), [(5, 5, 2), (6, 5, 2), (6, 6, 2)], (5.5, 5.2, 2), align="WORLD")
        close(self, flat.u, (1, 0, 0))
        close(self, flat.origin, (0, 0, 2))
        wall = geo.frame_from_face((1, 0, 0), [(3, 0, 0), (3, 1, 0), (3, 1, 1)], (3, 0.5, 0.5), align="WORLD")
        close(self, wall.u, (0, 1, 0))
        close(self, wall.origin, (3, 0, 0))

    def test_bad_input_is_refused(self):
        with self.assertRaises(ValueError):
            geo.frame_from_face((0, 0, 0), TILTED, (0, 0, 0))
        with self.assertRaises(ValueError):
            geo.frame_from_face((0, 0, 1), [(0, 0, 0), (1, 0, 0)], (0, 0, 0))
        with self.assertRaises(ValueError):
            geo.frame_from_face((0, 0, 1), TILTED, (0, 0, 0), align="CURSOR")

    def test_matrix_columns_are_the_axes(self):
        frame = geo.frame_from_face((0, 0, 1), [(1, 2, 3), (1, 4, 3), (0, 4, 3)], (1, 3, 3))
        m = frame.matrix()
        close(self, [m[i][0] for i in range(3)], frame.u)
        close(self, [m[i][2] for i in range(3)], frame.n)
        close(self, [m[i][3] for i in range(3)], frame.origin)
        self.assertEqual(m[3], [0.0, 0.0, 0.0, 1.0])

    def test_snap(self):
        self.assertEqual(geo.snap((0.26, -0.74), 0.25), (0.25, -0.75))
        self.assertEqual(geo.snap((0.26, -0.74), 0), (0.26, -0.74))
        close(self, geo.snap((0.031, 0.049), 0.01), (0.03, 0.05))


class Outlines(unittest.TestCase):
    def test_polygon_cleans_and_orients(self):
        clockwise = [(0, 0), (0, 1), (0, 1), (1, 1), (1, 0.5), (1, 0), (0, 0)]   # Repeat, collinear, closing.
        poly = geo.polygon(clockwise)
        self.assertEqual(len(poly), 4)
        self.assertGreater(geo.signed_area(poly), 0)
        self.assertAlmostEqual(geo.signed_area(poly), 1.0)

    def test_polygon_refuses_crossing_flat_and_tiny(self):
        for bad in ([(0, 0), (1, 1), (1, 0), (0, 1)], [(0, 0), (1, 0), (2, 0)], [(0, 0), (1, 0)],
                    [(0, 0), (1, 0), (1, 1), (0, 1), (2, 0.5)], [(0, 0), (float("nan"), 0), (1, 1)]):
            with self.assertRaises(ValueError, msg=bad):
                geo.polygon(bad)

    def test_concave_outline_is_kept(self):
        poly = geo.polygon(L_SHAPE)
        self.assertEqual(len(poly), 6)
        self.assertAlmostEqual(geo.signed_area(poly), 3.0)

    def test_box_and_circle(self):
        self.assertAlmostEqual(geo.signed_area(geo.box((1, 1), (3, 2))), 2.0)
        self.assertAlmostEqual(geo.signed_area(geo.box((1, 1), (3, 2), square=True)), 4.0)
        self.assertAlmostEqual(geo.signed_area(geo.box((1, 1), (3, 2), centered=True)), 8.0)
        self.assertAlmostEqual(geo.signed_area(geo.box((0, 0), (-2, -1))), 2.0)   # Dragged any way.
        disc = geo.circle((0.5, 0.5), 2.0, 64)
        self.assertEqual(len(disc), 64)
        self.assertAlmostEqual(geo.signed_area(disc), 0.5 * 64 * 4.0 * math.sin(2 * math.pi / 64))
        for bad in ((0.0, 8), (1.0, 2), (1.0, 999)):
            with self.assertRaises(ValueError):
                geo.circle((0, 0), *bad)

    def test_freehand_stroke_simplifies_to_a_close_outline(self):
        stroke = [(math.cos(t) * (1 + 0.002 * math.sin(40 * t)), math.sin(t) * (1 + 0.002 * math.sin(40 * t)))
                  for t in (2 * math.pi * i / 600 for i in range(600))]
        poly = geo.simplify(stroke, 0.01)
        self.assertLess(len(poly), 60)
        self.assertTrue(geo.is_simple(poly))
        self.assertAlmostEqual(geo.signed_area(poly), math.pi, delta=0.05 * math.pi)

    def test_inset_and_outset(self):
        square = geo.box((0, 0), (1, 1))
        self.assertAlmostEqual(geo.signed_area(geo.inset(square, 0.1)), 0.64)
        self.assertAlmostEqual(geo.signed_area(geo.outset(square, 0.1)), 1.44)
        thin = geo.inset(geo.polygon(L_SHAPE), 0.2)
        self.assertEqual(len(thin), 6)
        self.assertTrue(geo.is_simple(thin))
        self.assertAlmostEqual(geo.signed_area(thin), 0.6 * 1.6 + 0.6 * 1.0)   # Two 0.6-wide arms.
        for poly, width in ((square, 0.5), (square, 0.7), (geo.polygon(L_SHAPE), 0.6), (square, 0)):
            with self.assertRaises(ValueError, msg=(poly, width)):
                geo.inset(poly, width)


class Solids(unittest.TestCase):
    def check(self, verts, faces, expected_volume):
        self.assertTrue(geo.is_closed(faces))
        self.assertAlmostEqual(geo.volume(verts, faces), expected_volume)

    def test_prism(self):
        for poly in (geo.box((0, 0), (2, 1)), geo.polygon(L_SHAPE), geo.circle((1, 1), 0.5, 7)):
            self.check(*geo.prism(poly, 0.25, -1.5), geo.signed_area(poly) * 1.75)
        with self.assertRaises(ValueError):
            geo.prism(L_SHAPE, 0, 0)

    def test_ring_and_cup(self):
        for outer in (geo.box((0, 0), (2, 1)), geo.polygon(L_SHAPE), geo.circle((0, 0), 1, 12)):
            inner = geo.inset(outer, 0.1)
            a_out, a_in = geo.signed_area(outer), geo.signed_area(inner)
            self.check(*geo.ring(outer, inner, 0.1, -0.4), (a_out - a_in) * 0.5)
            self.check(*geo.ring(outer, inner, 0.0, -1.0, -0.7), a_out * 1.0 - a_in * 0.7)
        with self.assertRaises(ValueError):
            geo.ring(outer, inner[:-1], 1, 0)
        with self.assertRaises(ValueError):
            geo.ring(outer, inner, 1, 0, 2)

    def test_is_closed_catches_holes_and_flips(self):
        verts, faces = geo.prism(geo.box((0, 0), (1, 1)), 1, 0)
        self.assertFalse(geo.is_closed(faces[1:]))
        self.assertFalse(geo.is_closed([faces[0][::-1]] + faces[1:]))


class Interaction(unittest.TestCase):
    """The hand tool's mouse math: where a view ray meets the surface, and the depth a drag sets."""

    frame = geo.Frame((0, 0, 1), (1, 0, 0), (0, 0, 1))   # The top of something, 1 m up.

    def test_ray_meets_the_plane(self):
        close(self, geo.ray_plane(self.frame, (2, 3, 5), (0, 0, -1)), (2, 3))
        close(self, geo.ray_plane(self.frame, (0, 0, 3), (1, 0, -1)), (2, 0))
        self.assertIsNone(geo.ray_plane(self.frame, (0, 0, 3), (1, 0, 0)))    # Along the plane.
        self.assertIsNone(geo.ray_plane(self.frame, (0, 0, 3), (0, 0, 1)))    # Behind the eye.

    def test_depth_from_a_side_view(self):
        # Looking along +y from the side, at height 0.4: the ray passes 0.6 below the surface.
        self.assertAlmostEqual(geo.depth_along_normal(self.frame, (0, 0), (0, -5, 0.4), (0, 1, 0)), 0.6)
        self.assertAlmostEqual(geo.depth_along_normal(self.frame, (0, 0), (0, -5, 2.0), (0, 1, 0)), 0.0)
        self.assertAlmostEqual(geo.depth_along_normal(self.frame, (0, 0), (0, 0, 5), (0, 0, -1)), 0.0)
        tilted = geo.Frame((0, 0, 0), (0, 1, 0), (1, 0, 0))   # A wall facing +x.
        self.assertAlmostEqual(geo.depth_along_normal(tilted, (0.5, 0), (-0.25, 0.5, 4), (0, 0, -1)), 0.25)

    def test_polygon_closes_on_its_first_corner(self):
        self.assertTrue(geo.closes([(0, 0), (1, 0), (1, 1)], (0.01, 0.0), 0.02))
        self.assertFalse(geo.closes([(0, 0), (1, 0)], (0.0, 0.0), 0.02))
        self.assertFalse(geo.closes([(0, 0), (1, 0), (1, 1)], (0.5, 0.5), 0.02))


BOX = {"shape": "BOX", "size": [0.2, 0.1], "operation": "CUT", "depth": 0.05}


class Validation(unittest.TestCase):
    def test_defaults(self):
        out = spec.validate(BOX)
        self.assertEqual((out["center"], out["rotation"], out["array"], out["width"]), ((0.0, 0.0), 0.0, None, None))
        self.assertEqual(spec.validate({"shape": "CIRCLE", "radius": 0.1, "operation": "SLICE"})["depth"],
                         spec.THROUGH)
        self.assertEqual(spec.validate({"shape": "CIRCLE", "radius": 0.1, "depth": 1})["segments"], 32)

    def test_refusals_name_the_problem(self):
        cases = [
            ({**BOX, "colour": 1}, "does not take"),
            ({**BOX, "shape": "STAR"}, "shape"),
            ({**BOX, "operation": "MELT"}, "operation"),
            ({"shape": "BOX", "size": [0.1, 0.1]}, "needs depth"),
            ({**BOX, "operation": "UNION", "depth": "THROUGH"}, "not THROUGH"),
            ({**BOX, "operation": "PANEL"}, "needs width"),
            ({**BOX, "width": 0.01}, "only INSET and PANEL"),
            ({**BOX, "radius": 1}, "does not take radius"),
            ({"shape": "CIRCLE", "depth": 1}, "needs radius"),
            ({**BOX, "size": [0.0, 1]}, "width"),
            ({**BOX, "size": [5000, 1]}, "width"),
            ({**BOX, "depth": True}, "depth"),
            ({**BOX, "operation": "PANEL", "width": 0.06}, "too small"),
            ({"shape": "POLYGON", "points": [[0, 0], [1, 1], [1, 0], [0, 1]], "depth": 1}, "crosses itself"),
            ({**BOX, "shape": "POLYGON", "points": [[0, 0], [1, 0], [0, 1]]}, "a POLYGON does not take size"),
            ({**BOX, "array": {"columns": 3, "rows": 1, "spacing": [0.2, 0]}}, "overlap along u"),
            ({**BOX, "array": {"columns": 1, "rows": 2, "spacing": [0, 0.05]}}, "overlap along v"),
            ({**BOX, "array": {"columns": 30, "rows": 30, "spacing": [1, 1]}}, "at most"),
            ({**BOX, "array": {"columns": 2}}, "array must be"),
        ]
        for case, words in cases:
            with self.assertRaises(ValueError, msg=case) as caught:
                spec.validate(case)
            self.assertIn(words, str(caught.exception), case)

    def test_rotation_turns_the_outline_about_its_centre(self):
        turned = spec.outline(spec.validate({**BOX, "center": [1, 2], "rotation": 90}))
        xs, ys = [p[0] for p in turned], [p[1] for p in turned]
        self.assertAlmostEqual(max(xs) - min(xs), 0.1)
        self.assertAlmostEqual(max(ys) - min(ys), 0.2)
        self.assertAlmostEqual(sum(xs) / 4, 1.0)
        self.assertAlmostEqual(sum(ys) / 4, 2.0)


class Layout(unittest.TestCase):
    def roles(self, **change):
        return [s["role"] for s in spec.solids(spec.validate({**BOX, **change}), through=1.0)]

    def test_each_operation_makes_its_solids(self):
        self.assertEqual(self.roles(), ["DIFFERENCE"])
        self.assertEqual(self.roles(operation="UNION"), ["UNION"])
        self.assertEqual(self.roles(operation="INTERSECT"), ["INTERSECT"])
        self.assertEqual(self.roles(operation="SLICE"), ["DIFFERENCE", "PIECE"])
        self.assertEqual(self.roles(operation="INSET", width=0.01), ["DIFFERENCE", "UNION"])
        self.assertEqual(self.roles(operation="PANEL", width=0.01), ["DIFFERENCE"])

    def test_every_solid_is_closed_and_outward(self):
        for change in ({}, {"operation": "UNION"}, {"operation": "SLICE"}, {"operation": "INSET", "width": 0.01},
                       {"operation": "PANEL", "width": 0.005}, {"shape": "CIRCLE", "radius": 0.3, "size": None},
                       {"shape": "POLYGON", "points": [[0, 0], [0.4, 0], [0.4, 0.2], [0.2, 0.2], [0.2, 0.4],
                                                       [0, 0.4]], "size": None, "operation": "PANEL", "width": 0.02}):
            case = {k: v for k, v in {**BOX, **change}.items() if v is not None}
            for solid in spec.solids(spec.validate(case), through=0.5):
                self.assertTrue(geo.is_closed(solid["faces"]), case)
                self.assertGreater(geo.volume(solid["verts"], solid["faces"]), 0, case)

    def test_a_cut_starts_above_the_surface_and_reaches_its_depth(self):
        solid = spec.solids(spec.validate(BOX))[0]
        zs = [v[2] for v in solid["verts"]]
        self.assertGreater(max(zs), 0)
        self.assertAlmostEqual(min(zs), -0.05)
        added = spec.solids(spec.validate({**BOX, "operation": "UNION"}))[0]
        zs = [v[2] for v in added["verts"]]
        self.assertAlmostEqual(max(zs), 0.05)
        self.assertLess(min(zs), 0)   # Sunk a little into the surface, so it fuses.

    def test_through_reaches_past_the_target(self):
        cut = spec.validate({**BOX, "depth": "THROUGH"})
        with self.assertRaises(ValueError):
            spec.solids(cut)
        solid = spec.solids(cut, through=0.8)[0]
        self.assertLess(min(v[2] for v in solid["verts"]), -0.8)
        frame = geo.Frame((0, 0, 1), (1, 0, 0), (0, 0, 1))
        corners = [(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-0.5, 1)]
        self.assertAlmostEqual(spec.through_depth(frame, corners), 1.5)

    def test_vent_grid_copies_are_centred_and_apart(self):
        vent = spec.validate({"shape": "BOX", "size": [0.02, 0.005], "operation": "CUT", "depth": 0.01,
                              "center": [0.3, 0.2], "array": {"columns": 3, "rows": 6, "spacing": [0.03, 0.01]}})
        solid = spec.solids(vent)[0]
        self.assertEqual(len(solid["copies"]), 18)
        first = [(x, y) for x, y, z in solid["verts"] if z > 0]
        centres = [(sum(p[0] for p in first) / len(first) + dx, sum(p[1] for p in first) / len(first) + dy)
                   for dx, dy in solid["copies"]]
        close(self, (sum(c[0] for c in centres) / 18, sum(c[1] for c in centres) / 18), (0.3, 0.2))
        self.assertEqual(len({(round(x, 9), round(y, 9)) for x, y in centres}), 18)

    def test_inset_cup_overlaps_the_opening_and_keeps_its_wall(self):
        cut = spec.validate({"shape": "CIRCLE", "radius": 0.1, "segments": 24, "operation": "INSET",
                             "depth": 0.03, "width": 0.004})
        opening, cup = spec.solids(cut)
        open_r = max(math.hypot(x, y) for x, y, z in opening["verts"])
        cup_r = max(math.hypot(x, y) for x, y, z in cup["verts"])
        self.assertGreater(cup_r, open_r)   # Grown past the opening, never coincident with it.
        self.assertAlmostEqual(min(v[2] for v in cup["verts"]), -0.034)


if __name__ == "__main__":
    unittest.main()
