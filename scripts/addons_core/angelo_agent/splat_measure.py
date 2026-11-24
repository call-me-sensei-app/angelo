# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How far a splat collision surface lies off the capture, and whether one lies further off than another beyond
sampling noise: shared by ``splat_budget`` (which refuses a result that lost part of the capture) and the splat
suite (which judges results the same way). NumPy only, no ``bpy``.

Distances are sampled, so two measurements of equally good surfaces differ by noise. A difference counts only beyond
``Z`` standard errors of the two statistics combined, each error estimated from its own samples (a seeded bootstrap
for the 95th percentile, the textbook error for a mean or a proportion): no tolerance tied to a shape or a size.

**Coverage of the capture** (``exceedance``, ``exceeds_reference``). Every splat centre's sphere, widened to
r'_i = max(r_i, SHEET * voxel), lies inside the volume the surface is drawn from, and Volume to Mesh draws it half a
voxel inside that volume's boundary; so a surface that keeps every feature lies within r'_i + voxel / 2 of each centre
on a captured surface. A centre further off sits on part of the capture the surface lost (a lost wall's centres were
0.3 m off with r' about 0.1 m). Centres inside a solid (an interior splat) or floaters can be further off with
nothing lost, so the count of centres beyond their bound is judged against the same count on the hole-free surface at
the first voxel, correct by construction (``splat_mesh.SHEET``), measured on the same splats: a result counting more
than that reference beyond binomial noise has lost part of the capture. Limits: the reference is only as correct as
the SHEET rule that makes it; and centres where walls meet (or buried in thick parts) sit deeper the coarser the
voxel, so a coarse result counts a few more beyond than the finest reference with nothing lost (a floor plan's
results counted 2 to 6 of 2,000, all at interior wall junctions, 2026-11-21), which could refuse a coarse result
falsely; the gate's binomial noise absorbs that few, and the next release's wider narrow band removes the cause.
"""

import numpy as np

SEED = 20261120     # Every run draws the same samples, so a result never flips on the draw.
Z = 2.0             # Standard errors a difference must exceed to count.
ROUNDS = 200        # Bootstrap resamples for a percentile's standard error.


def area_points(coords, triangles, count=2000, seed=SEED):
    """``count`` points uniformly distributed over the triangles ``triangles`` (indices into ``coords``) by area: a
    triangle by its area, then a point uniform within it. By area, not by vertex: a thinned mesh keeps its vertices
    at corners and edges, which sit furthest off a surface, so a vertex sample overstates how far it lies off."""
    coords = np.asarray(coords, dtype=np.float64)
    corners = coords[np.asarray(triangles, dtype=np.int64)]             # (t, 3, 3)
    area = 0.5 * np.linalg.norm(np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1)
    rng = np.random.default_rng(seed)
    pick = rng.choice(len(corners), size=count, p=area / area.sum())
    a, b = rng.random(count), rng.random(count)
    flip = a + b > 1                                                    # Fold the square onto the triangle.
    a[flip], b[flip] = 1 - a[flip], 1 - b[flip]
    t = corners[pick]
    return t[:, 0] + a[:, None] * (t[:, 1] - t[:, 0]) + b[:, None] * (t[:, 2] - t[:, 0])


def p95(values):
    return float(np.percentile(np.asarray(values, dtype=np.float64), 95))


def p95_error(values, rounds=ROUNDS, seed=SEED):
    """The standard error of the 95th percentile of ``values``, by a seeded bootstrap."""
    values = np.asarray(values, dtype=np.float64)
    if len(values) < 2:
        return 0.0
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, len(values), size=(rounds, len(values)))
    return float(np.std(np.percentile(values[picks], 95, axis=1), ddof=1))


def mean_error(values):
    values = np.asarray(values, dtype=np.float64)
    return float(np.std(values, ddof=1) / np.sqrt(len(values))) if len(values) > 1 else 0.0


def within_noise(result, reference, statistic="p95"):
    """Whether ``result``'s statistic ("p95" or "mean") of its distance samples is no larger than ``reference``'s
    beyond ``Z`` combined standard errors."""
    if statistic == "p95":
        r, f, er, ef = p95(result), p95(reference), p95_error(result), p95_error(reference)
    else:
        r, f = float(np.mean(result)), float(np.mean(reference))
        er, ef = mean_error(result), mean_error(reference)
    return r <= f + Z * float(np.hypot(er, ef))


def exceedance(distances, allowed):
    """How many centres lie further from a surface (``distances``) than their own bound (``allowed``)."""
    return int(np.count_nonzero(np.asarray(distances) > np.asarray(allowed)))


def exceeds_reference(count, reference, total):
    """Whether ``count`` of ``total`` centres beyond their bound is more than the reference surface's ``reference``
    beyond ``Z`` standard errors of the two proportions (each smoothed by one success and one failure, so a reference
    with none still has a noise floor)."""
    if total <= 0:
        return False
    p, q = (count + 1) / (total + 2), (reference + 1) / (total + 2)
    return p > q + Z * float(np.sqrt(p * (1 - p) / total + q * (1 - q) / total))
