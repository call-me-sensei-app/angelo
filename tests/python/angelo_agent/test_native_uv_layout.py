# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""UV Layout's arithmetic (uv_layout_math, doc/angelo/uv_layout.rst), without Blender.

Covered: the margin that keeps islands apart down to a mip level, proven by rasterising two islands at that margin
and box-filtering the image level by level; texel density and its spread; tile planning and UDIM offsets; and the
3D match that stacks congruent islands (rotated, translated, reordered copies match, round parts turned about their
axis; mirrored only when asked, a flat patch turned over counting as a mirror; different shapes, and shapes with no
distinct axis, never), from the caller's own input shape and within bounded memory; and UVs copied corner to corner,
so islands with seams stack. Shapes vary on purpose so no rule fits one case.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import math
import sys
import tracemalloc
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import uv_layout_math as uvm  # noqa: E402


def _bleeds(size, mips, margin_texels):
    """Two islands margin_texels from their shared gap's middle (gap = 2 x margin), each a solid colour: does any
    texel of mip levels 1..mips (box filter), or its bilinear neighbour, mix both colours?"""
    a = np.zeros((size, size))
    b = np.zeros((size, size))
    centre = size // 2 + 3   # Off the power-of-two grid on purpose: the worst alignment.
    gap = int(round(2 * margin_texels))
    a[:, : centre - gap // 2] = 1
    b[:, centre - gap // 2 + gap:] = 1
    for _level in range(mips):
        a = (a[0::2, 0::2] + a[1::2, 0::2] + a[0::2, 1::2] + a[1::2, 1::2]) / 4
        b = (b[0::2, 0::2] + b[1::2, 0::2] + b[0::2, 1::2] + b[1::2, 1::2]) / 4
    both = (a > 0) & (b > 0)
    near = (a > 0)[:, :-1] & (b > 0)[:, 1:]   # A bilinear sample between neighbouring texels.
    return bool(both.any() or near.any())


class MipPadding(unittest.TestCase):
    def test_margin_is_two_to_the_level_over_the_size(self):
        self.assertEqual(uvm.mip_margin(2048, 0), 1 / 2048)
        self.assertEqual(uvm.mip_margin(1024, 3), 8 / 1024)
        for bad in ((1000.5, 1), (8, 1), (2048, 12), (2048, -1), (True, 1)):
            with self.assertRaises(ValueError):
                uvm.mip_margin(*bad)

    def test_islands_stay_apart_at_that_level_and_not_one_deeper(self):
        for size in (256, 512):
            for mips in (1, 2, 3, 4):
                margin = uvm.mip_margin(size, mips) * size   # In texels.
                self.assertFalse(_bleeds(size, mips, margin), (size, mips))
                self.assertTrue(_bleeds(size, mips + 2, margin), (size, mips))

    def test_mips_kept_inverts_the_margin(self):
        for mips in range(0, 6):
            self.assertEqual(uvm.mips_kept(2 * (2 ** mips)), mips)
        self.assertEqual(uvm.mips_kept(1), -1)


class Density(unittest.TestCase):
    def test_density_of_a_metre_square(self):
        # A 1 m x 1 m face on a quarter of a 2048 px texture: 1024 px across 1 m.
        self.assertAlmostEqual(uvm.texel_density(0.25, 1.0, 2048), 1024.0)
        self.assertAlmostEqual(uvm.texel_density(0.25, 4.0, 2048), 512.0)
        self.assertEqual(uvm.texel_density(0.0, 1.0, 2048), 0.0)
        self.assertAlmostEqual(uvm.density_scale(512.0, 1024.0), 2.0)
        with self.assertRaises(ValueError):
            uvm.density_scale(0.0, 10.0)

    def test_spread_is_weighted_and_skips_empty(self):
        low, high, mean = uvm.spread([100.0, 300.0, 0.0], [3.0, 1.0, 5.0])
        self.assertEqual((low, high), (100.0, 300.0))
        self.assertAlmostEqual(mean, 150.0)
        self.assertEqual(uvm.spread([], None), (0.0, 0.0, 0.0))

    def test_polygon_area(self):
        self.assertAlmostEqual(uvm.polygon_area([(0, 0), (1, 0), (1, 1), (0, 1)]), 1.0)
        self.assertAlmostEqual(uvm.polygon_area([(0, 0), (0, 1), (1, 0)]), 0.5)   # Either winding.


class Tiles(unittest.TestCase):
    def test_tiles_needed_plans_for_gaps(self):
        self.assertEqual(uvm.tiles_needed([0.1, 0.2]), 1)
        self.assertEqual(uvm.tiles_needed([0.5, 0.5]), 2)
        self.assertEqual(uvm.tiles_needed([]), 1)

    def test_assignment_balances_and_is_stable(self):
        areas = [0.5, 0.4, 0.3, 0.3, 0.2, 0.1, 0.1]
        tiles = uvm.assign_tiles(areas, 2)
        loads = [sum(a for a, t in zip(areas, tiles) if t == k) for k in range(2)]
        self.assertLessEqual(abs(loads[0] - loads[1]), 0.1 + 1e-9)
        self.assertEqual(tiles, uvm.assign_tiles(areas, 2))
        with self.assertRaises(ValueError):
            uvm.assign_tiles(areas, 0)

    def test_a_spilled_island_goes_to_the_next_unpacked_tile_of_its_group(self):
        # Tiles 0-2 hold group A, 3-4 group B; tile 0 is being packed.
        groups = {0: "A", 1: "A", 2: "A", 3: "B", 4: "B"}
        used = [0, 1, 2, 3, 4]
        self.assertEqual(uvm.spill_tile(used, 0, groups), 1)
        self.assertEqual(uvm.spill_tile(used, 2, groups), 5)   # A's last tile: a new one, never B's.
        self.assertEqual(uvm.spill_tile(used, 3, groups), 4)
        self.assertEqual(uvm.spill_tile(used, 4, groups), 5)
        groups[5] = "A"
        self.assertEqual(uvm.spill_tile(used + [5], 4, groups), 6)   # B's last: past A's new tile too.

    def test_spilling_keeps_the_tile_count_near_what_the_area_needs(self):
        # Packing fits less than planned here (0.6 of a tile against FILL's 0.72), so every planned tile overflows.
        # A tile that overflowed keeps more than capacity less its largest island, so spilling forward needs at most
        # area / (capacity - largest) + 1 tiles; a fresh tile per spilled island needed about three times as many.
        rng = np.random.default_rng(3)
        areas = list(rng.uniform(0.02, 0.08, 600))
        capacity = 0.6
        tiles = uvm.tiles_needed(areas)
        tile_of = dict(enumerate(uvm.assign_tiles(areas, tiles)))
        used, groups, index = list(range(tiles)), dict.fromkeys(range(tiles), 0), 0
        while index < len(used):
            here = sorted((i for i, t in tile_of.items() if t == used[index]), key=lambda i: areas[i])
            if len(here) > 1 and sum(areas[i] for i in here) > capacity:
                target = uvm.spill_tile(used, index, groups)
                tile_of[here[0]] = target
                if target not in groups:
                    groups[target] = groups[used[index]]
                    used.append(target)
                continue
            index += 1
        loads = [sum(areas[i] for i, t in tile_of.items() if t == tile) for tile in used]
        self.assertLessEqual(max(loads), capacity + 1e-12)
        self.assertLessEqual(len(used), math.ceil(sum(areas) / (capacity - max(areas))) + 1)

    def test_a_tile_packs_at_most_its_planned_fill(self):
        # Islands spilled from earlier tiles pile onto the next; packing them all spread the layout far past the tile
        # and kept a handful. Largest first, an island stays while the tile's area is within FILL; the rest move on.
        areas = [0.30, 0.25, 0.20, 0.10, 0.05, 0.04, 0.01]
        leaving = uvm.over_fill(areas, [True] * 7)
        kept = [k for k in range(7) if k not in leaving]
        self.assertLessEqual(sum(areas[k] for k in kept), uvm.FILL + 1e-12)
        self.assertEqual(kept, [0, 1, 3, 4, 6])   # 0.20 and then 0.04 would pass FILL (0.72); the rest fit: 0.71.
        self.assertEqual(uvm.over_fill([0.2, 0.1], [True, True]), [])
        self.assertEqual(uvm.over_fill([0.9, 0.5], [True, True]), [1])   # One island past FILL still stays.
        # Islands others are stacked on never move, and their area counts first.
        self.assertEqual(uvm.over_fill([0.1, 0.6, 0.3], [True, False, True]), [2])

    def test_every_island_a_pack_left_outside_its_tile_moves_on_at_once(self):
        # One pack per tile: the islands it placed outside go forward together, never one per pack.
        areas = [0.30, 0.05, 0.20, 0.02, 0.10]
        inside = [True, False, True, False, True]
        self.assertEqual(uvm.overflowing(inside, [True] * 5, areas), [1, 3])
        self.assertEqual(uvm.overflowing([True] * 5, [True] * 5, areas), [])
        # An island other parts are stacked on stays (its copies share its UVs).
        self.assertEqual(uvm.overflowing(inside, [True, False, True, True, True], areas), [3])
        # All outside: the largest stays, so the tile keeps an island and the loop moves forward.
        self.assertEqual(uvm.overflowing([False] * 5, [True] * 5, areas), [1, 2, 3, 4])
        self.assertEqual(uvm.overflowing([False], [True], [0.4]), [])

    def test_an_island_fits_a_tile_by_its_smallest_turned_rectangle(self):
        def rect(w, h, angle):
            c, s = math.cos(angle), math.sin(angle)
            return [(x * c - y * s + 3, x * s + y * c - 2) for x, y in ((0, 0), (w, 0), (w, h), (0, h))]
        self.assertTrue(uvm.fits_tile(rect(0.9, 0.9, math.radians(45)), 0.01))   # Its box is 1.27, turned 0.9.
        self.assertTrue(uvm.fits_tile(rect(0.98, 0.2, 0.3), 0.01))
        self.assertFalse(uvm.fits_tile(rect(0.98, 0.2, 0.3), 0.02))   # Too long once both margins are kept.
        self.assertFalse(uvm.fits_tile(rect(1.2, 0.73, 1.1), 0.0))
        self.assertTrue(uvm.fits_tile([(0.5, 0.5)] * 3, 0.0))   # Degenerate: a point fits.
        self.assertAlmostEqual(uvm.tile_span(rect(0.4, 1.3, 0.7)), 1.3)

    def test_udim_offsets(self):
        self.assertEqual(uvm.udim_offset(0), (0, 0))
        self.assertEqual(uvm.udim_offset(9), (9, 0))
        self.assertEqual(uvm.udim_offset(12), (2, 1))
        self.assertAlmostEqual(uvm.efficiency([0.3, 0.4], 1), 0.7)


def _rotation(axis, angle):
    axis = np.asarray(axis, float) / np.linalg.norm(axis)
    k = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    return np.eye(3) + math.sin(angle) * k + (1 - math.cos(angle)) * k @ k


BRACKET = np.array([[0, 0, 0], [3, 0, 0], [3, 0.4, 0], [0.5, 0.4, 0], [0.5, 1.7, 0], [0, 1.7, 0],
                    [0, 0, 0.2], [3, 0, 0.2], [3, 0.4, 0.2], [0.5, 0.4, 0.2], [0.5, 1.7, 0.2], [0, 1.7, 0.2]])
# The bracket is mirror-symmetric about z = 0.1, so its mirror image is the bracket turned over. Lifting one corner
# makes a part with no mirror symmetry: its mirror image is a different part.
CHIRAL = BRACKET + np.array([[0, 0, 0]] * 10 + [[0, 0, 0.15], [0, 0, 0]])
# A flat L-shaped patch facing +z (a decal, a panel's face): flat, so any mirror image is also the patch turned over.
FLAT = np.array([[0, 0, 0], [2, 0, 0], [2, 0.5, 0], [0.6, 0.5, 0], [0.6, 1.4, 0], [0, 1.4, 0], [0.3, 0.25, 0]])


def _outward(points):
    """Unit vectors from the centroid: they turn and mirror with the points, as a closed part's normals do."""
    d = points - points.mean(axis=0)
    return d / np.linalg.norm(d, axis=1)[:, None]


def _caller_shape(points):
    """What uv_layout hands the math: a list of (x, y, z) tuples from mathutils vectors, never an array."""
    return [tuple(float(c) for c in p) for p in points]


def _ring(segments, radius, z, twist=0.0):
    angles = [twist + 2 * math.pi * k / segments for k in range(segments)]
    return [[radius * math.cos(t), radius * math.sin(t), z] for t in angles]


# Round parts: their two spreads across the axis are equal, so the turn about the axis must be searched.
FRUSTUM = np.array(_ring(7, 0.05, 0.0) + _ring(7, 0.03, 0.2) + [[0, 0, 0], [0, 0, 0.2]])
TWISTED = np.array(_ring(6, 0.05, 0.0) + _ring(6, 0.05, 0.15, twist=0.3))   # Chiral: a left-hand twist.
DISC = np.array(_ring(8, 0.04, 0.0) + [[0, 0, 0]])                         # A flat round cap facing +z.


def _rigid(a, b):
    """The best rotation (never a mirror; row vectors) carrying points a onto b, and its largest residual (Kabsch).
    The sign is fixed to a rotation rather than read from the fit: coplanar points leave the third axis free, so the
    unconstrained fit of a flat part may come out a mirror even when a rotation fits it exactly."""
    ca, cb = a.mean(axis=0), b.mean(axis=0)
    u, _s, vt = np.linalg.svd((a - ca).T @ (b - cb))
    turn = u @ np.diag([1, 1, np.sign(np.linalg.det(u @ vt)) or 1.0]) @ vt
    return turn, np.abs((a - ca) @ turn - (b - cb)).max()


class Stacking(unittest.TestCase):
    def test_a_moved_turned_reordered_copy_matches(self):
        rng = np.random.default_rng(4)
        normals = _outward(CHIRAL)
        for axis, angle in (((0, 0, 1), 0.7), ((1, 2, 3), 2.1), ((1, 0, 0), math.pi)):
            order = rng.permutation(len(CHIRAL))
            turn = _rotation(axis, angle).T
            copy = (CHIRAL @ turn + [5, -2, 9])[order]
            mapping = uvm.match(CHIRAL, normals, copy, (normals @ turn)[order], 1e-6)
            self.assertIsNotNone(mapping, (axis, angle))
            self.assertEqual([int(order[m]) for m in mapping], list(range(len(CHIRAL))))

    def test_the_callers_shape_works(self):
        turn = _rotation((1, 2, 3), 1.1).T
        copy = CHIRAL @ turn + [1, 2, 3]
        self.assertEqual(uvm.signature(_caller_shape(CHIRAL), 10, 1e-3), uvm.signature(CHIRAL, 10, 1e-3))
        mapping = uvm.match(_caller_shape(CHIRAL), _caller_shape(_outward(CHIRAL)), _caller_shape(copy),
                            _caller_shape(_outward(CHIRAL) @ turn), 1e-6)
        self.assertEqual(mapping, list(range(len(CHIRAL))))

    def test_malformed_points_are_refused_clearly(self):
        for bad in ([(0, 0)] * 4, [(0, 0, float("nan"))] * 4, "points"):
            with self.subTest(points=bad), self.assertRaisesRegex(ValueError, "x, y, z"):
                uvm.signature(bad, 1, 1e-3)

    def test_mirror_only_when_asked(self):
        flip = np.array([-1.0, 1, 1])
        mirrored, normals = CHIRAL * flip, _outward(CHIRAL) * flip
        self.assertIsNone(uvm.match(CHIRAL, _outward(CHIRAL), mirrored, normals, 1e-6))
        self.assertIsNotNone(uvm.match(CHIRAL, _outward(CHIRAL), mirrored, normals, 1e-6, mirrored=True))

    def test_a_flat_patch_turned_over_is_a_mirror(self):
        # The points of a mirrored flat patch match the patch turned over, but its surface faces the other way, so
        # sharing UVs would show the texture mirrored: only with mirrored.
        up = np.tile([0.0, 0, 1], (len(FLAT), 1))
        mirrored = FLAT * [-1, 1, 1]
        self.assertIsNone(uvm.match(FLAT, up, mirrored, up, 1e-6))
        self.assertIsNotNone(uvm.match(FLAT, up, mirrored, up, 1e-6, mirrored=True))
        turn = _rotation((0, 0, 1), 0.9).T   # Turned in its own plane: the same patch.
        self.assertIsNotNone(uvm.match(FLAT, up, FLAT @ turn + [4, 1, 0], up, 1e-6))

    def test_a_mirror_symmetric_part_turned_over_is_itself(self):
        flip = np.array([-1.0, 1, 1])
        mapping = uvm.match(BRACKET, _outward(BRACKET), BRACKET * flip, _outward(BRACKET) * flip, 1e-6)
        self.assertIsNotNone(mapping)
        self.assertEqual(sorted(mapping), list(range(len(BRACKET))))

    def test_round_parts_match_turned_about_their_axis(self):
        rng = np.random.default_rng(7)
        for part in (FRUSTUM, TWISTED, DISC):
            normals = _outward(part) if part is not DISC else np.tile([0.0, 0, 1], (len(part), 1))
            for axis, angle in (((0, 0, 1), 0.4), ((1, 1, 0), 2.0), ((3, -1, 2), 1.2)):
                order, turn = rng.permutation(len(part)), _rotation(axis, angle).T
                copy = (part @ turn + [2, 0, -1])[order]
                mapping = uvm.match(part, normals, copy, (normals @ turn)[order], 1e-6)
                self.assertIsNotNone(mapping, (len(part), axis))
                self.assertEqual(sorted(mapping), list(range(len(part))))
                turn_found, residual = _rigid(part, copy[mapping])
                self.assertLess(residual, 1e-6)   # A rotation carries the part onto its matched copy ...
                copy_normals = (normals @ turn)[order][mapping]   # ... and its normals onto the copy's.
                self.assertLess(np.abs(normals @ turn_found - copy_normals).max(), 1e-6)

    def test_round_mirror_images_only_when_asked(self):
        flip = np.array([1.0, -1, 1])
        twisted = TWISTED * flip   # A right-hand twist: a different part.
        self.assertIsNone(uvm.match(TWISTED, _outward(TWISTED), twisted, _outward(TWISTED) * flip, 1e-6))
        self.assertIsNotNone(uvm.match(TWISTED, _outward(TWISTED), twisted, _outward(TWISTED) * flip, 1e-6,
                                       mirrored=True))
        up = np.tile([0.0, 0, 1], (len(DISC), 1))   # A mirrored cap faces the same way: the cap itself.
        self.assertIsNotNone(uvm.match(DISC, up, DISC * flip, up, 1e-6))

    def test_round_parts_with_too_many_turns_are_not_stacked(self):
        ring = np.array(_ring(uvm.MAX_TURNS + 4, 1.0, 0.0) + _ring(uvm.MAX_TURNS + 4, 0.5, 0.3))
        up = np.tile([0.0, 0, 1], (len(ring), 1))
        self.assertIsNone(uvm.match(ring, up, ring @ _rotation((0, 0, 1), 0.01).T, up, 1e-6))

    def test_corners_copy_through_seams(self):
        # Two quads and a triangle sharing vertices; B is A relabelled, each face starting on another corner. A
        # vertex on a seam has a different UV in each face, so UVs go corner to corner, never vertex to vertex.
        faces_a = [(0, 1, 4, 3), (1, 2, 5, 4), (3, 4, 6)]
        a_of_b = {10: 0, 11: 1, 12: 2, 13: 3, 14: 4, 15: 5, 16: 6}
        faces_b = [(15, 14, 11, 12), (16, 13, 14), (11, 14, 13, 10)]
        sources = uvm.corner_sources(faces_a, faces_b, a_of_b)
        self.assertEqual(sources, [(1, [2, 3, 0, 1]), (2, [2, 0, 1]), (0, [1, 2, 3, 0])])
        for (face, corners), face_b in zip(sources, faces_b):
            self.assertEqual([faces_a[face][c] for c in corners], [a_of_b[v] for v in face_b])

    def test_corners_refuse_a_different_topology(self):
        faces_a = [(0, 1, 2), (0, 2, 3)]
        self.assertIsNone(uvm.corner_sources(faces_a, [(0, 1, 3), (1, 2, 3)], {0: 0, 1: 1, 2: 2, 3: 3}))
        self.assertIsNone(uvm.corner_sources(faces_a, [(0, 1, 9)], {0: 0, 1: 1}))

    def test_different_or_symmetric_shapes_never_match(self):
        longer = CHIRAL * [1.01, 1, 1]
        self.assertIsNone(uvm.match(CHIRAL, _outward(CHIRAL), longer, _outward(longer), 1e-4))
        self.assertIsNone(uvm.match(CHIRAL, _outward(CHIRAL), CHIRAL[:-1], _outward(CHIRAL[:-1]), 1e-4))
        cube = np.array([[x, y, z] for x in (0, 1) for y in (0, 1) for z in (0, 1)], float)
        self.assertIsNone(uvm.match(cube, _outward(cube), cube + 3, _outward(cube), 1e-6))   # Never guessed.

    def test_signature_groups_copies(self):
        turned = CHIRAL @ _rotation((0, 1, 1), 1.0).T
        self.assertEqual(uvm.signature(CHIRAL, 10, 1e-3), uvm.signature(turned, 10, 1e-3))
        self.assertNotEqual(uvm.signature(CHIRAL, 10, 1e-3), uvm.signature(CHIRAL * 1.5, 10, 1e-3))

    def test_the_largest_match_keeps_memory_bounded(self):
        # At MAX_MATCH vertices every pair is compared; a whole N x N x 3 difference array would be ~384 MiB.
        rng = np.random.default_rng(11)
        n = uvm.MAX_MATCH
        cloud = rng.normal(size=(n, 3)) * [3.0, 2.0, 1.0]
        normals = rng.normal(size=(n, 3))
        normals /= np.linalg.norm(normals, axis=1)[:, None]
        turn, order = _rotation((2, -1, 0.5), 0.8).T, rng.permutation(n)
        tracemalloc.start()
        try:
            mapping = uvm.match(cloud, normals, (cloud @ turn + [1, 1, 1])[order], (normals @ turn)[order], 1e-6)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertEqual([int(order[m]) for m in mapping], list(range(n)))
        self.assertLess(peak, 64 * 2 ** 20, "peak %.0f MiB" % (peak / 2 ** 20))


class Packers(unittest.TestCase):
    """Quality (Blender's Concave shape) is the default; Fast is the shape with the better efficiency per second of
    Convex and AABB on the development probe (coordinator ruling, 2026-11-15)."""

    def test_quality_is_concave_and_fast_is_a_quicker_shape(self):
        self.assertEqual(uvm.PACKERS["QUALITY"], "CONCAVE")
        self.assertIn(uvm.PACKERS["FAST"], {"AABB", "CONVEX"})
        self.assertEqual(set(uvm.PACKERS), {"QUALITY", "FAST"})
        self.assertEqual(uvm.DEFAULT_PACKER, "QUALITY")


class PackRunsAsAStoppableJob(unittest.TestCase):
    """UV Layout's hand path runs each pack as Pack Islands' own window-manager job, so the interface keeps working,
    shows progress and stops (coordinator ruling, 2026-11-15): Angelo's job module must offer that job's progress and
    stop, and the job must check its own stop flag."""

    def test_the_job_module_offers_uv_pack(self):
        root = Path(__file__).resolve().parents[3]
        jobs = (root / "source/blender/python/intern/bpy_angelo_jobs.cc").read_text(encoding="utf-8")
        table = jobs[jobs.index("static const AngeloJobType job_types[] = {"):]
        self.assertIn('{"UV_PACK", WM_JOB_TYPE_UV_PACK, false}', table[:table.index("};")])
        pack = (root / "source/blender/editors/uvedit/uvedit_unwrap_ops.cc").read_text(encoding="utf-8")
        start = pack[pack.index("static void pack_islands_startjob("):]
        self.assertIn("pid->pack_island_params.stop = &worker_status->stop;", start[:start.index("\n}\n")])

    def test_the_runner_hears_esc_while_a_pack_locks_the_interface(self):
        # A pack job locks the interface (as a render does); a modal operator then gets no events unless its type
        # bypasses the lock, so Esc never reached the runner (GUI suite, pool VM). Python operators may now ask
        # for it; the runner only reads the job's progress and stops it while the lock holds.
        root = Path(__file__).resolve().parents[3]
        rna = (root / "source/blender/makesrna/intern/rna_wm.cc").read_text(encoding="utf-8")
        self.assertIn('{OPTYPE_LOCK_BYPASS,\n     "LOCK_BYPASS",', rna)
        ui = (root / "scripts/addons_core/angelo_agent/uv_layout_ui.py").read_text(encoding="utf-8")
        runner = ui[ui.index("class ANGELO_OT_uv_layout_run("):]
        self.assertIn('bl_options = {"UNDO", "INTERNAL", "LOCK_BYPASS"}', runner)

    def test_a_confined_script_cannot_register_a_lock_bypass_operator(self):
        # Defence in depth for the lock bypass (break-it brief, 2026-11-15): Angelo, add-ons and Blender may make an
        # operator that runs while a job locks the interface; a confined agent script may not. Both registration
        # paths (operators and macros) refuse it before an operator of the same name is replaced, so a refusal
        # changes nothing. test_blender_script_confinement proves it in the app.
        root = Path(__file__).resolve().parents[3]
        rna = (root / "source/blender/makesrna/intern/rna_wm.cc").read_text(encoding="utf-8")
        helper = rna[rna.index("static bool rna_operator_lock_bypass_refused("):]
        helper = helper[:helper.index("\n}\n")]
        self.assertIn("OPTYPE_LOCK_BYPASS", helper)
        self.assertIn("gate->confined()", helper)
        for register in ("static StructRNA *rna_Operator_register(", "static StructRNA *rna_MacroOperator_register("):
            body = rna[rna.index(register):]
            body = body[:body.index("\n}\n")]
            refusal = body.index("rna_operator_lock_bypass_refused(")
            self.assertLess(body.index("validate(&dummy_operator_ptr"), refusal, register)
            self.assertLess(refusal, body.index("WM_operatortype_find(dummy_ot.idname, true)"), register)

    def test_pack_islands_starts_its_job_without_its_dialog_when_asked(self):
        # Invoked, Pack Islands opens its confirm dialog; UV Layout's runner asks for the job alone (confirm=False),
        # as Delete does: Blender's own confirm-or-exec property, hidden and never saved.
        root = Path(__file__).resolve().parents[3]
        pack = (root / "source/blender/editors/uvedit/uvedit_unwrap_ops.cc").read_text(encoding="utf-8")
        invoke = pack[pack.index("static wmOperatorStatus uv_pack_islands_invoke("):]
        invoke = invoke[:invoke.index("\n}\n")]
        self.assertIn('if (!RNA_boolean_get(op->ptr, "confirm")) {', invoke)
        self.assertIn("return op->type->exec(C, op);", invoke)
        definition = pack[pack.index("void UV_OT_pack_islands(wmOperatorType *ot)"):]
        self.assertIn("WM_operator_properties_confirm_or_exec(ot);", definition[:definition.index("\n}\n")])
        layout = (root / "scripts/addons_core/angelo_agent/uv_layout.py").read_text(encoding="utf-8")
        self.assertIn('bpy.ops.uv.pack_islands("INVOKE_DEFAULT", False, confirm=False, **options)', layout)


class NativeKindStaysNative(unittest.TestCase):
    """Search is Angelo's field, so the agent reaches it through the uv_layout operation; the existing uv_pack kind
    keeps Blender's uv.pack_islands fields only (typed-op pattern, revision 2: existing kinds stay native)."""

    def test_uv_pack_has_only_blender_fields(self):
        from angelo_agent import action_schemas
        fields = set(action_schemas.PARAMETER_SCHEMAS['uv_pack']['properties'])
        self.assertNotIn('search', fields)
        # uv.pack_islands' own fields, plus the face-selection fields every indexed kind shares (INDEXED_KINDS).
        self.assertIn('uv_pack', action_schemas.INDEXED_KINDS)
        shared = {'index_scope', 'topology_step', 'face_selector'}
        blender = {'uv_map', 'margin', 'rotate', 'shape_method', 'margin_method', 'face_indices'}
        self.assertEqual(fields - shared, blender)
        self.assertLessEqual(shared, set(action_schemas.PARAMETER_SCHEMAS['uv_unwrap']['properties']))


if __name__ == "__main__":
    unittest.main()
