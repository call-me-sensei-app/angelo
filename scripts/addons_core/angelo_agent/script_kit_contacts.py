# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Contact tests between boxes and triangulated surfaces for the kit's support check (``script_kit_support``).

NumPy only, no bpy: which boxes a surface's triangles cross or touch (the separating-axis test), bounded by
MAX_PAIRS box-triangle comparisons so a check on a huge scene stops, unmeasured, instead of running long.
"""

MAX_PAIRS = 20000000   # Box-triangle comparisons one contact test may need (about a second of NumPy).
BOX_COST = 1000        # One box's own cost in that budget: its Python round, in comparisons.


def boxes_meeting_surface(lo, hi, points, triangles, max_pairs=MAX_PAIRS):
    """Which boxes (corners ``lo`` and ``hi``, (m, 3)) a triangulated surface (``points`` (n, 3), ``triangles``
    (t, 3) indices) meets: a triangle that crosses or touches one, by the separating-axis test. None when it would
    take more than ``max_pairs`` comparisons."""
    import numpy
    if not len(lo):
        return numpy.zeros(0, dtype=bool)
    return boxes_meeting_corners(lo, hi, _near_corners(points, triangles, lo.min(axis=0), hi.max(axis=0)), max_pairs)


def boxes_meeting_corners(lo, hi, corners, max_pairs=MAX_PAIRS, first=False):
    """``boxes_meeting_surface`` for triangles given by their corners ((t, 3, 3)); ``first`` stops at the first box
    met. Triangles are sorted by their least x, so a box compares only those whose x range can reach it; triangles
    wider in x than the median box (a deck's few large faces) are compared with every box, as touching_pairs does
    with large parts."""
    import numpy
    met = numpy.zeros(len(lo), dtype=bool)
    if not len(lo) or not len(corners):
        return met
    low, high = corners.min(axis=1), corners.max(axis=1)
    width = high[:, 0] - low[:, 0]
    wide = width > float(numpy.median(hi[:, 0] - lo[:, 0]))
    big, small = numpy.flatnonzero(wide), numpy.flatnonzero(~wide)
    order = small[numpy.argsort(low[small, 0], kind="stable")]
    reach = float(width[small].max()) if len(small) else 0.0   # A narrow triangle reaching a box starts this near.
    start = numpy.searchsorted(low[order, 0], lo[:, 0] - reach, side="left")
    end = numpy.searchsorted(low[order, 0], hi[:, 0], side="right")
    if int((end - start).sum()) + (len(big) + BOX_COST) * len(lo) > max_pairs:
        return None
    for i in range(len(lo)):
        candidates = numpy.concatenate((order[start[i]:end[i]], big))
        candidates = candidates[((low[candidates] <= hi[i]) & (high[candidates] >= lo[i])).all(axis=1)]
        if len(candidates) and triangles_cross_box(corners[candidates], lo[i], hi[i]).any():
            met[i] = True
            if first:
                break
    return met


def triangles_cross_box(corners, lo, hi):
    """Separating-axis test (Akenine-Moller) of triangles (``corners`` (k, 3, 3)) against one box: True where a
    triangle crosses or touches it. Axes: the box's three, the triangle's normal and the nine cross products of its
    edges with x, y and z; a triangle is apart only when one of them separates it strictly."""
    import numpy
    centre, half = (lo + hi) / 2.0, (hi - lo) / 2.0
    v = corners - centre
    apart = ((v.min(axis=1) > half) | (v.max(axis=1) < -half)).any(axis=1)
    edges = v[:, [1, 2, 0]] - v
    normal = numpy.cross(edges[:, 0], edges[:, 1])
    apart |= numpy.abs((normal * v[:, 0]).sum(axis=1)) > (numpy.abs(normal) * half).sum(axis=1)
    for e in range(3):
        for unit in numpy.eye(3):
            axis = numpy.cross(unit, edges[:, e])
            projected = (v * axis[:, None, :]).sum(axis=2)
            radius = (numpy.abs(axis) * half).sum(axis=1)
            apart |= (projected.min(axis=1) > radius) | (projected.max(axis=1) < -radius)
    return ~apart


def _overlapping(lo, hi, box_lo, box_hi):
    """Which boxes (``lo``, ``hi``) overlap one box."""
    return (lo <= box_hi).all(axis=1) & (hi >= box_lo).all(axis=1)


def _near_corners(points, triangles, lo, hi):
    """The corners ((k, 3, 3)) of the triangles whose bounds overlap the box (``lo``, ``hi``). A triangle is apart
    only when its three corners lie beyond the same face of the box: one byte of outcodes per point, so a terrain's
    million triangles are sorted out without gathering their coordinates."""
    import numpy
    codes = numpy.zeros(len(points), dtype=numpy.uint8)
    for axis in range(3):
        codes |= (points[:, axis] < lo[axis]).astype(numpy.uint8) << axis
        codes |= (points[:, axis] > hi[axis]).astype(numpy.uint8) << (axis + 3)
    near = numpy.flatnonzero(numpy.bitwise_and.reduce(codes[triangles], axis=1) == 0)
    return points[triangles[near]]
