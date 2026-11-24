# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Surface-to-surface distance between triangles, and the bounding-volume tree that finds the pairs worth measuring
(triangle_distance.py; doc/angelo/character_checks.rst), without Blender.

The exact distance is checked against a dense sampling of both triangles over many random pairs, and against cases
whose answer is known (crossing, edges meeting, coplanar overlap, parallel faces, zero-area triangles). The tree is
checked against measuring every pair, on random scenes of many small parts."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import triangle_distance as td  # noqa: E402


def sampled(tri, steps):
    """Points spread over a triangle: corners, edges and inside."""
    u, v = np.meshgrid(np.linspace(0, 1, steps), np.linspace(0, 1, steps))
    keep = u + v <= 1
    u, v = u[keep], v[keep]
    return tri[0] + np.outer(u, tri[1] - tri[0]) + np.outer(v, tri[2] - tri[0])


def sampled_distance(a, b, steps=40):
    pa, pb = sampled(a, steps), sampled(b, steps)
    return float(np.sqrt(((pa[:, None, :] - pb[None, :, :]) ** 2).sum(-1)).min())


def one(a, b):
    return float(td.triangle_pairs(np.array([a], float), np.array([b], float))[0])


class PairTests(unittest.TestCase):
    def test_random_pairs_match_a_dense_sampling(self):
        rng = np.random.default_rng(7)
        for _ in range(150):
            a = rng.uniform(-1, 1, (3, 3))
            b = rng.uniform(-1, 1, (3, 3)) + rng.uniform(-1.5, 1.5, 3)
            exact, approx = one(a, b), sampled_distance(a, b)
            longest = max(np.linalg.norm(t[i] - t[j]) for t in (a, b) for i in range(3) for j in range(3))
            self.assertGreaterEqual(exact, 0.0)
            self.assertLessEqual(exact, approx + 1e-9)                 # Never farther than points on both.
            self.assertLessEqual(approx - exact, 0.06 * longest + 1e-9)   # Within the sampling's spacing.

    def test_pairs_whose_answer_is_known(self):
        flat = [(-1, -1, 0), (1, -1, 0), (0, 1, 0)]
        cases = {
            "crossing through the interiors": ([(0, -0.2, -1), (0, 0.4, -1), (0, 0.1, 1)], 0.0),
            "edges meeting": ([(0, -0.5, 0), (0, 0.5, 0), (0, 0, 1)], 0.0),
            "parallel above": ([(-1, -1, 0.3), (1, -1, 0.3), (0, 1, 0.3)], 0.3),
            "a corner above the face": ([(0, 0, 0.25), (0.2, 0.2, 2), (-0.2, 0.2, 2)], 0.25),
            "coplanar overlap": ([(-1, 1, 0), (1, 1, 0), (0, -1.5, 0)], 0.0),
            "coplanar apart": ([(2, -1, 0), (3, -1, 0), (2.5, 1, 0)], None),
            "zero-area sliver through it": ([(0, 0, -1), (0, 0, 1), (0, 0, 0.5)], 0.0),
            "a zero-size point above": ([(0, 0, 2), (0, 0, 2), (0, 0, 2)], 2.0),
        }
        for name, (other, expected) in cases.items():
            found = one(flat, other)
            self.assertTrue(np.isfinite(found), name)
            if expected is None:
                self.assertAlmostEqual(found, sampled_distance(np.array(flat, float), np.array(other, float),
                                                               steps=80), delta=0.02, msg=name)
            else:
                self.assertAlmostEqual(found, expected, places=9, msg=name)
            self.assertAlmostEqual(found, one(other, flat), places=12, msg=name + " (either order)")

    def test_skew_edges(self):
        below = [(-1, 0, 0), (1, 0, 0), (0, 0, -1)]
        above = [(0, -1, 0.5), (0, 1, 0.5), (0, 0, 1.5)]
        self.assertAlmostEqual(one(below, above), 0.5, places=12)


def scene(rng, parts, size):
    """Random small triangles in ``parts`` labelled clusters."""
    tris, labels = [], []
    for label in range(parts):
        centre = rng.uniform(-3, 3, 3)
        for _ in range(rng.integers(1, 6)):
            tris.append(centre + rng.uniform(-size, size, (3, 3)))
            labels.append(label)
    return np.array(tris), np.array(labels)


def every_pair(tris, labels, parts):
    near = np.full((parts, parts), np.inf)
    for i in range(len(tris)):
        others = np.arange(i + 1, len(tris))
        if not len(others):
            continue
        found = td.triangle_pairs(np.repeat(tris[i:i + 1], len(others), 0), tris[others])
        for j, distance in zip(others, found):
            a, b = labels[i], labels[j]
            if a != b:
                near[a, b] = near[b, a] = min(near[a, b], distance)
    return near


class TreeTests(unittest.TestCase):
    def test_the_tree_finds_what_measuring_every_pair_finds(self):
        rng = np.random.default_rng(11)
        for trial in range(12):
            parts = int(rng.integers(2, 25))
            tris, labels = scene(rng, parts, size=float(rng.uniform(0.2, 1.2)))
            near = every_pair(tris, labels, parts)
            tree = td.Tree(tris, labels)
            for radius in (0.05, 0.4):
                touching = td.touching(tree, parts, radius)
                np.testing.assert_array_equal(touching, (near <= radius).any(axis=1), err_msg=str(trial))
            for label in range(parts):
                part = td.Tree(tris[labels == label], labels[labels == label])
                self.assertAlmostEqual(td.gap(part, tree, label), near[label].min(), places=9,
                                       msg=(trial, label))

    def test_work_is_counted(self):
        rng = np.random.default_rng(3)
        tris, labels = scene(rng, 10, 0.5)
        work = {}
        td.touching(td.Tree(tris, labels), 10, 0.1, work=work)
        self.assertGreater(work["node_pairs"], 0)
        self.assertIn("triangle_pairs", work)

    def test_one_label_has_no_gap_and_nothing_touches(self):
        tris = np.array([[(0, 0, 0), (1, 0, 0), (0, 1, 0)]], float)
        tree = td.Tree(tris, np.array([0]))
        self.assertEqual(td.touching(tree, 1, 1.0).tolist(), [False])
        self.assertEqual(td.gap(tree, tree, 0), np.inf)


if __name__ == "__main__":
    unittest.main()
