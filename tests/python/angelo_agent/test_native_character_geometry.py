# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The pre-review checks' geometry without Blender (doc/angelo/character_checks.rst): shells, faces wound against
their neighbours, closed shells turned inside out, parts floating free of everything else, parts no bone moves and
see-through holes in a view's silhouette, as facts with where they are.

Meshes are built here (cubes, a plane, a ring), several unlike ones; nothing depends on one character. A stylised
character's hair lock, a prop's handle and a building's window frame fail these checks in the same ways."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import character_geometry as geo  # noqa: E402

CUBE_FACES = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
              (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]


def cube(centre=(0.0, 0.0, 0.0), size=1.0):
    """A closed cube wound outward (counter-clockwise seen from outside)."""
    h = size / 2.0
    c = np.array(centre, float)
    corners = np.array([(-h, -h, -h), (h, -h, -h), (h, h, -h), (-h, h, -h),
                        (-h, -h, h), (h, -h, h), (h, h, h), (-h, h, h)]) + c
    return corners, np.array(CUBE_FACES)


def box(low, high):
    """A closed box between two corners, wound outward."""
    (x0, y0, z0), (x1, y1, z1) = low, high
    corners = np.array([(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
                        (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)], float)
    return corners, np.array(CUBE_FACES)


def sphere(centre=(0.0, 0.0, 0.0), radius=1.0, rings=8, segments=16):
    """A closed UV sphere wound outward; ``rings`` x ``segments`` sets how fine it is."""
    points = [(0.0, 0.0, -radius)]
    for ring in range(1, rings):
        theta = np.pi * ring / rings - np.pi / 2
        for segment in range(segments):
            phi = 2 * np.pi * segment / segments
            points.append((radius * np.cos(theta) * np.cos(phi), radius * np.cos(theta) * np.sin(phi),
                           radius * np.sin(theta)))
    points.append((0.0, 0.0, radius))
    top = len(points) - 1
    faces = []
    at = lambda ring, segment: 1 + (ring - 1) * segments + segment % segments  # noqa: E731
    for segment in range(segments):
        faces.append((0, at(1, segment + 1), at(1, segment)))
        faces.append((top, at(rings - 1, segment), at(rings - 1, segment + 1)))
    for ring in range(1, rings - 1):
        for segment in range(segments):
            a, b = at(ring, segment), at(ring, segment + 1)
            c, d = at(ring + 1, segment + 1), at(ring + 1, segment)
            faces += [(a, b, c), (a, c, d)]
    return np.array(points) + np.array(centre, float), np.array(faces)


def triangle(*corners):
    return np.array(corners, float), np.array([(0, 1, 2)])


def codes(found):
    return [fact["code"] for fact in found["facts"]]


def combine(*parts):
    positions, triangles, offset = [], [], 0
    for points, faces in parts:
        positions.append(points)
        triangles.append(faces + offset)
        offset += len(points)
    return np.vstack(positions), np.vstack(triangles)


class ShellTests(unittest.TestCase):
    def test_an_outward_cube_has_nothing_to_report(self):
        points, faces = cube()
        self.assertGreater(geo.signed_volume(points, faces), 0)
        self.assertEqual(geo.check(points, faces)["facts"], [])

    def test_shells_are_found_by_shared_corners(self):
        points, faces = combine(cube(), cube((5, 0, 0)), cube((0.5, 0, 0)))
        shells = geo.shells(faces, len(points))
        self.assertEqual(len(set(shells.tolist())), 3)
        welded = geo.weld(points, faces, 1e-6)   # Split copies of one corner are one corner.
        self.assertEqual(welded.shape, faces.shape)

    def test_a_cube_turned_inside_out_is_an_inward_shell(self):
        points, faces = cube()
        found = geo.check(points, faces[:, ::-1])["facts"]
        self.assertEqual([fact["code"] for fact in found], ["inward_shell"])
        self.assertEqual(found[0]["count"], 12)

    def test_one_face_wound_against_its_neighbours(self):
        points, faces = cube()
        faces = faces.copy()
        faces[3] = faces[3][::-1]
        found = geo.check(points, faces)["facts"]
        self.assertEqual([fact["code"] for fact in found], ["flipped_faces"])
        self.assertEqual(found[0]["count"], 1)
        self.assertEqual(found[0]["faces"], [3])
        self.assertEqual(len(found[0]["where"][0]), 3)   # A world point to look at.

    def test_an_open_surface_is_never_called_inside_out(self):
        points = np.array([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], float)
        for faces in (np.array([(0, 1, 2), (0, 2, 3)]), np.array([(0, 2, 1), (0, 3, 2)])):
            self.assertEqual(geo.check(points, faces)["facts"], [])


class PartTests(unittest.TestCase):
    def test_a_part_far_from_everything_floats_and_a_touching_one_does_not(self):
        body = cube(size=2.0)
        touching = cube((1.25, 0, 0), size=0.5)   # Its face meets the body's.
        floating = cube((0, 0, 3.0), size=0.3)
        points, faces = combine(body, touching, floating)
        found = geo.check(points, faces, height=2.0)["facts"]
        self.assertEqual([fact["code"] for fact in found], ["floating_part"])
        self.assertAlmostEqual(found[0]["measured"]["gap"], 3.0 - 0.15 - 1.0, places=6)
        self.assertEqual(found[0]["count"], 12)

    def test_the_gap_tolerance_scales_with_the_figure(self):
        points, faces = combine(cube(size=2.0), cube((0, 0, 1.0 + 0.01 + 0.05), size=0.1))
        self.assertEqual(geo.check(points, faces, height=2.0)["facts"], [])   # 1 cm on a 2 m figure: touching.
        self.assertEqual([f["code"] for f in geo.check(points, faces, height=0.2)["facts"]], ["floating_part"])

    def test_a_part_no_bone_moves(self):
        points, faces = combine(cube(size=2.0), cube((1.25, 0, 0), size=0.5))
        weights = np.ones(len(points))
        weights[8:] = 0.0
        found = geo.check(points, faces, weights=weights, height=2.0)["facts"]
        self.assertEqual([fact["code"] for fact in found], ["unweighted_part"])
        self.assertEqual(found[0]["count"], 12)



class SurfaceDistanceTests(unittest.TestCase):
    """Audit F7 (2026-11-14): shells were measured corner to surface only, so two parts crossing through each other
    with no corner near the other's surface were called floating. Shells are measured surface to surface: crossing
    faces, edges that meet and coplanar overlaps touch; the gap of a part that floats is its true nearest distance."""

    def test_two_triangles_crossing_through_their_interiors_touch(self):
        flat = triangle((-1, -1, 0), (1, -1, 0), (0, 1, 0))
        upright = triangle((0, -0.2, -1), (0, 0.4, -1), (0, 0.1, 1))   # Pierces the flat one; corners 1 away.
        points, faces = combine(flat, upright)
        self.assertEqual(codes(geo.check(points, faces, height=2.0)), [])

    def test_closed_bars_crossing_like_a_plus_sign_touch(self):
        # No corner of either bar is near the other's surface; only their faces and edges cross.
        points, faces = combine(box((-2, -0.2, -0.2), (2, 0.2, 0.2)), box((-0.3, -2, -0.1), (0.3, 2, 0.1)))
        self.assertEqual(codes(geo.check(points, faces, height=4.0)), [])

    def test_edges_that_meet_touch_and_a_lifted_edge_floats_by_the_edge_gap(self):
        below = triangle((-1, 0, 0), (1, 0, 0), (0, 0, -1))   # Its top edge runs along x.
        for lift, expected in ((0.0, []), (0.5, ["floating_part"])):
            above = triangle((0, -1, lift), (0, 1, lift), (0, 0, 1 + lift))   # Its bottom edge runs along y.
            points, faces = combine(below, above)
            found = geo.check(points, faces, height=2.0)
            self.assertEqual(codes(found), expected, lift)
            if expected:
                self.assertAlmostEqual(found["facts"][0]["measured"]["gap"], lift, places=6)   # Edge to edge.

    def test_coplanar_triangles_that_overlap_touch(self):
        # Two triangles of a six-pointed star: every corner outside the other, only their edges cross.
        up = triangle(*[(np.cos(a), np.sin(a), 0.0) for a in np.radians([90, 210, 330])])
        down = triangle(*[(np.cos(a), np.sin(a), 0.0) for a in np.radians([270, 30, 150])])
        points, faces = combine(up, down)
        self.assertEqual(codes(geo.check(points, faces, height=2.0)), [])

    def test_a_sliver_through_a_face_touches_and_degenerate_faces_never_break_the_measure(self):
        flat = triangle((-1, -1, 0), (1, -1, 0), (0, 1, 0))
        sliver = triangle((0, 0, -1), (0, 0, 1), (0, 0, 0.5))   # Zero area: a needle through the face.
        point = triangle((3, 3, 3), (3, 3, 3), (3, 3, 3))       # Zero size, far away.
        points, faces = combine(flat, sliver, point)
        found = geo.check(points, faces, height=2.0)
        self.assertEqual(codes(found), ["floating_part"])
        self.assertEqual(found["facts"][0]["shell"], 2)
        self.assertAlmostEqual(found["facts"][0]["measured"]["gap"], float(np.linalg.norm([3, 3, 2])), places=6)

    def test_a_sphere_with_a_bar_through_it_and_a_moon_beside_it(self):
        for rings in (6, 12):
            points, faces = combine(sphere(rings=rings, segments=2 * rings), box((-2, -0.05, -0.05), (2, 0.05, 0.05)),
                                    sphere((0, 0, 1.6), radius=0.3, rings=rings, segments=2 * rings))
            found = geo.check(points, faces, height=2.0)
            self.assertEqual(codes(found), ["floating_part"], rings)
            self.assertEqual(found["facts"][0]["shell"], 2)
            self.assertAlmostEqual(found["facts"][0]["measured"]["gap"], 0.3, delta=0.02)   # Faceted spheres.

    def test_the_work_grows_about_linearly_with_the_faces(self):
        """The audit timed the old point-by-triangle scan at about 4x per doubling. Pairs measured, counted rather
        than timed: four times the faces may cost a little over four times the work, never sixteen."""
        work = []
        for rings in (16, 32):
            points, faces = combine(sphere(rings=rings, segments=2 * rings),
                                    sphere((0.0, 0.0, 1.5), radius=0.4, rings=rings, segments=2 * rings),
                                    box((-1.5, -0.05, -0.05), (1.5, 0.05, 0.05)))
            found = geo.check(points, faces, height=2.0)
            work.append(found["work"]["node_pairs"] + found["work"]["triangle_pairs"])
        self.assertLess(work[1], 8 * work[0], work)


class SeeThroughTests(unittest.TestCase):
    def test_holes_enclosed_by_the_silhouette_are_listed(self):
        rows, columns = np.mgrid[:100, :100]
        ring = ((rows - 50) ** 2 + (columns - 50) ** 2 <= 40 ** 2) & ((rows - 50) ** 2 + (columns - 50) ** 2 > 10 ** 2)
        solid = (rows - 50) ** 2 + (columns - 50) ** 2 <= 40 ** 2
        holes = geo.see_through(ring)
        self.assertEqual(len(holes), 1)
        self.assertEqual(holes[0]["area_px"], int(((rows - 50) ** 2 + (columns - 50) ** 2 <= 10 ** 2).sum()))
        self.assertEqual(holes[0]["bbox_px"], [40, 40, 61, 61])
        self.assertEqual(geo.see_through(solid), [])
        self.assertEqual(geo.see_through(ring, min_area_px=1000), [])   # Specks below the floor are left out.


if __name__ == "__main__":
    unittest.main()
