# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The geometry behind the kit's support check (script_kit_support), without Blender.

A part touches another object when the part's grown bounds meet one of its triangles; the separating-axis test that
decides it is pinned here against an independent reference (the triangle clipped by the box's six planes), and the
searches that pick which triangles to test (sorted windows, wide triangles kept apart, outcodes, the per-surface
index) against brute force, with triangles narrower and wider than the boxes; two surfaces meet only when each
reaches the other's triangle bounds. The union-find that joins floating parts to what they touch keeps whether a set
is grounded and the lowest point it reaches. test_blender_script_kit_support runs the whole check on real scenes.
"""

from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))

import numpy  # noqa: E402

from angelo_agent import script_kit_support as support  # noqa: E402


def clipped(triangle, lo, hi):
    """Whether any of the triangle is left after clipping it by the box's six planes (Sutherland-Hodgman)."""
    polygon = [numpy.asarray(point, dtype=float) for point in triangle]
    for axis in range(3):
        for bound, keep in ((lo[axis], lambda v, b: v >= b), (hi[axis], lambda v, b: v <= b)):
            result = []
            for i, current in enumerate(polygon):
                following = polygon[(i + 1) % len(polygon)]
                inside, next_inside = keep(current[axis], bound), keep(following[axis], bound)
                if inside:
                    result.append(current)
                if inside != next_inside:
                    t = (bound - current[axis]) / (following[axis] - current[axis])
                    result.append(current + t * (following - current))
            polygon = result
            if not polygon:
                return False
    return True


def random_triangle(rng, centre, size):
    return [[centre[a] + rng.uniform(-size, size) for a in range(3)] for _ in range(3)]


class TriangleBoxTests(unittest.TestCase):
    def test_the_separating_axis_test_agrees_with_clipping(self):
        rng = random.Random(7)
        lo, hi = numpy.array([-1.0, -0.5, -0.25]), numpy.array([1.0, 0.5, 0.25])
        agreed = tested = 0
        for _ in range(4000):
            size = rng.choice((0.1, 0.6, 2.0, 6.0))   # Smaller than the box, about its size and far larger.
            triangle = random_triangle(rng, [rng.uniform(-2.5, 2.5) for _ in range(3)], size)
            inner, outer = clipped(triangle, lo + 1e-7, hi - 1e-7), clipped(triangle, lo - 1e-7, hi + 1e-7)
            if inner != outer:   # On the boundary to within rounding: either answer is right.
                continue
            tested += 1
            got = bool(support.triangles_cross_box(numpy.array([triangle]), lo, hi)[0])
            agreed += got == inner
        self.assertGreater(tested, 3900)
        self.assertEqual(agreed, tested)

    def test_touching_counts_and_a_gap_does_not(self):
        lo, hi = numpy.array([0.0, 0.0, 0.0]), numpy.array([1.0, 1.0, 1.0])
        floor = numpy.array([[[-5.0, -5.0, 0.0], [5.0, -5.0, 0.0], [0.0, 5.0, 0.0]]])   # Through a face's plane.
        edge = numpy.array([[[2.0, 0.5, 1.5], [0.5, 0.5, 1.0 + 1e-12], [0.5, 2.0, 1.5]]])  # One corner on the top.
        below = floor - numpy.array([0.0, 0.0, 0.03])
        slant = numpy.array([[[-1.0, 0.0, 3.0], [3.0, 0.0, -1.0], [3.0, 1.0, -1.0]]])   # Past the box's corner.
        clear = slant + numpy.array([1.6, 0.0, 1.6])
        got = [bool(support.triangles_cross_box(t, lo, hi)[0]) for t in (floor, edge, below, slant, clear)]
        self.assertEqual(got, [True, False, False, True, False])

    def test_a_degenerate_triangle_is_tested_by_its_bounds(self):
        lo, hi = numpy.array([0.0, 0.0, 0.0]), numpy.array([1.0, 1.0, 1.0])
        point = numpy.array([[[0.5, 0.5, 0.5]] * 3])
        line = numpy.array([[[-1.0, 0.5, 0.5], [2.0, 0.5, 0.5], [2.0, 0.5, 0.5]]])
        away = numpy.array([[[3.0, 3.0, 3.0]] * 3])
        self.assertEqual([bool(support.triangles_cross_box(t, lo, hi)[0]) for t in (point, line, away)],
                         [True, True, False])


class SearchTests(unittest.TestCase):
    def scene(self, seed):
        """Boxes and triangles of mixed sizes: narrow ones among them and a few wider than any box."""
        rng = random.Random(seed)
        centres = [[rng.uniform(-10, 10) for _ in range(3)] for _ in range(120)]
        lo = numpy.array([[c[a] - rng.uniform(0.05, 0.6) for a in range(3)] for c in centres])
        hi = numpy.array([[c[a] + rng.uniform(0.05, 0.6) for a in range(3)] for c in centres])
        triangles = [random_triangle(rng, [rng.uniform(-10, 10) for _ in range(3)], 0.4) for _ in range(900)]
        triangles += [random_triangle(rng, [rng.uniform(-10, 10) for _ in range(3)], 12.0) for _ in range(6)]
        return lo, hi, numpy.array(triangles)

    def brute(self, lo, hi, corners):
        return numpy.array([bool(support.triangles_cross_box(corners, lo[i], hi[i]).any()) for i in range(len(lo))])

    def test_the_windowed_search_finds_what_every_comparison_finds(self):
        for seed in range(6):
            lo, hi, corners = self.scene(seed)
            met = support.boxes_meeting_corners(lo, hi, corners)
            numpy.testing.assert_array_equal(met, self.brute(lo, hi, corners))
            self.assertTrue(met.any() and not met.all(), seed)

    def test_points_and_indices_give_the_same_answer_and_first_stops_at_one(self):
        lo, hi, corners = self.scene(11)
        points, triangles = corners.reshape(-1, 3), numpy.arange(len(corners) * 3, dtype=numpy.int32).reshape(-1, 3)
        numpy.testing.assert_array_equal(support.boxes_meeting_surface(lo, hi, points, triangles),
                                         self.brute(lo, hi, corners))
        first = support.boxes_meeting_corners(lo, hi, corners, first=True)
        self.assertEqual(int(first.sum()), 1)

    def test_a_search_past_its_budget_is_refused_not_guessed(self):
        lo, hi, corners = self.scene(3)
        self.assertIsNone(support.boxes_meeting_corners(lo, hi, corners, max_pairs=10))

    def test_outcodes_keep_exactly_the_triangles_whose_bounds_overlap(self):
        lo, hi, corners = self.scene(5)
        points, triangles = corners.reshape(-1, 3), numpy.arange(len(corners) * 3).reshape(-1, 3)
        box_lo, box_hi = numpy.array([-3.0, -2.0, -1.0]), numpy.array([2.0, 4.0, 3.0])
        kept = support._near_corners(points, triangles, box_lo, box_hi)
        expected = corners[((corners.min(axis=1) <= box_hi) & (corners.max(axis=1) >= box_lo)).all(axis=1)]
        numpy.testing.assert_array_equal(kept, expected)

    def test_a_surface_index_returns_exactly_the_overlapping_triangles(self):
        lo, hi, corners = self.scene(9)
        points, triangles = corners.reshape(-1, 3), numpy.arange(len(corners) * 3).reshape(-1, 3)
        surface = support._Surface("Terrain", points, triangles)
        low, high = corners.min(axis=1), corners.max(axis=1)
        for i in range(len(lo)):
            expected = numpy.flatnonzero(((low <= hi[i]) & (high >= lo[i])).all(axis=1))
            self.assertEqual(sorted(surface.near(lo[i], hi[i]).tolist()), expected.tolist())

    def test_two_surfaces_meet_only_within_the_tolerance(self):
        def plate(name, z):
            points = numpy.array([[0, 0, z], [4, 0, z], [4, 4, z], [0, 4, z]], dtype=float)
            return support._Surface(name, points, numpy.array([[0, 1, 2], [0, 2, 3]]))
        floor = plate("Floor", 0.0)
        self.assertTrue(support.surfaces_meet(floor, plate("Rug", 0.015), 0.02))
        self.assertFalse(support.surfaces_meet(floor, plate("Shelf", 0.03), 0.02))

    def test_a_large_slanted_triangle_does_not_hold_up_what_floats_inside_its_bounds(self):
        slope = support._Surface("Slope", numpy.array([[-5, -5, -5], [5, -5, 5], [5, 5, 5]], dtype=float),
                                 numpy.array([[0, 1, 2]]))   # z = x: its bounds hold the plates below.

        def plate(z):
            points = numpy.array([[0, -1, z], [1, -1, z], [1, 0, z], [0, 0, z]], dtype=float)
            return support._Surface("Plate", points, numpy.array([[0, 1, 2], [0, 2, 3]]))
        self.assertFalse(support.surfaces_meet(slope, plate(3.0), 0.02))   # 2 m over the slope beneath it.
        self.assertFalse(support.surfaces_meet(plate(3.0), slope, 0.02))
        self.assertTrue(support.surfaces_meet(slope, plate(0.5), 0.02))    # The slope rises through it.


class BookkeepingTests(unittest.TestCase):
    def test_a_set_is_grounded_by_any_member_and_reaches_its_lowest_point(self):
        graph = support._Graph()
        graph.add(("part", 0), 1.05, False)
        graph.add(("object", 0), 1.0, False)   # The deck.
        graph.add(("object", 1), -0.2, True)   # A pier into the ground.
        graph.add(("part", 1), 4.0, False)
        self.assertTrue(graph.union(("part", 0), ("object", 0)))
        self.assertFalse(graph.union(("object", 0), ("part", 0)))
        self.assertEqual((graph.grounded(("part", 0)), graph.lowest(("part", 0))), (False, 1.0))
        graph.union(("object", 1), ("object", 0))
        self.assertEqual((graph.grounded(("part", 0)), graph.lowest(("part", 0))), (True, -0.2))
        self.assertEqual((graph.grounded(("part", 1)), graph.lowest(("part", 1))), (False, 4.0))

    def test_parts_are_reached_through_touching_parts_and_labelled_by_component(self):
        near = {0: [1], 1: [0, 2], 2: [1], 3: [4], 4: [3], 5: []}
        self.assertEqual(support.reached(near, [0], 6), [True, True, True, False, False, False])
        self.assertEqual(support.components(near, [3, 4, 5]), {3: 3, 4: 3, 5: 5})

    def test_world_bounds_of_a_turned_and_scaled_box(self):
        corners = [(-1, -2, -3), (-1, -2, 3), (-1, 2, 3), (-1, 2, -3), (1, -2, -3), (1, -2, 3), (1, 2, 3), (1, 2, -3)]
        quarter = [[0.0, -2.0, 0.0, 10.0], [1.0, 0.0, 0.0, 0.0], [0.0, 0.0, 1.0, 5.0], [0.0, 0.0, 0.0, 1.0]]
        lo, hi = support._world_box(corners, quarter)   # Turned 90 degrees about Z, Y scaled by two.
        self.assertEqual((tuple(lo), tuple(hi)), ((6.0, -1.0, 2.0), (14.0, 1.0, 8.0)))


if __name__ == "__main__":
    unittest.main()
