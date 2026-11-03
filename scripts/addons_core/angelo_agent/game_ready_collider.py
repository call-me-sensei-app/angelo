# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The game-ready check's collider facts: how a collider mesh fits a mesh, against the mesh's convex hull, and which
of the mesh's points it leaves outside. No Blender dependency; the kernels are ``game_ready_metrics``'s.

Physics engines rebuild a convex collider's hull whatever its faces' winding, so convexity and the convex inside test
read each face either way round; a non-convex collider needs consistent winding for its volume and the winding-number
inside test, and the result says when it lacks it.
"""

import math

import numpy as np

from .game_ready_metrics import SAMPLES, Comparison, Samples, _length, _nearest, _round, topology

MAX_WINDING_WORK = 1 << 24  # Points x collider triangles, at most, for the inside and convexity tests.
MIN_WINDING_POINTS = 256    # Fewer mesh points than this and the inside test of a non-convex collider is skipped.


def _signed_volume(geometry):
    c = geometry.corners().astype(np.float64)
    return float((c[:, 0] * np.cross(c[:, 1], c[:, 2])).sum() / 6.0)


def _consistent_winding(geometry):
    """Whether every edge two triangles share is walked in opposite directions by them (outward or inward alike)."""
    ids = geometry.vertex_ids[geometry.tris]
    a, b = ids[:, [0, 1, 2]].reshape(-1), ids[:, [1, 2, 0]].reshape(-1)
    directed = np.stack([a, b], 1)
    _, counts = np.unique(directed, axis=0, return_counts=True)
    return bool((counts == 1).all())


def _convex(geometry, tolerance):
    """True, False, or None when too large to test exhaustively. Each face is tested both ways, so a convex shape
    with some faces wound inward still reads convex (engines rebuild convex hulls whatever the winding)."""
    used = geometry.positions[np.unique(geometry.tris)].astype(np.float64)   # Loose vertices are no part of it.
    if len(geometry.tris) * len(used) > MAX_WINDING_WORK:
        return None
    corners = geometry.corners().astype(np.float64)
    normals = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    keep = lengths > 0
    normals = normals[keep] / lengths[keep, None]
    offsets = (normals * corners[keep, 0]).sum(1)
    step = max(1, (1 << 20) // max(len(used), 1))   # Faces per chunk: about a million heights at a time.
    for start in range(0, len(normals), step):
        heights = used @ normals[start:start + step].T - offsets[start:start + step]
        if not ((heights <= tolerance).all(0) | (heights >= -tolerance).all(0)).all():
            return False
    return True


def _outward(geometry):
    """Face normals and offsets of a convex geometry, each turned to face away from its vertex centroid."""
    corners = geometry.corners().astype(np.float64)
    normals = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-30)
    offsets = (normals * corners[:, 0]).sum(1)
    centroid = geometry.positions[np.unique(geometry.tris)].astype(np.float64).mean(0)
    flip = np.where(normals @ centroid - offsets > 0, -1.0, 1.0)
    return normals * flip[:, None], offsets * flip


def _convex_volume(geometry):
    """Volume of a convex geometry as tetrahedra from its centroid (any winding)."""
    corners = geometry.corners().astype(np.float64)
    centroid = geometry.positions[np.unique(geometry.tris)].astype(np.float64).mean(0)
    a, b, c = (corners[:, k] - centroid for k in range(3))
    return float(np.abs((a * np.cross(b, c)).sum(1)).sum() / 6.0)


def _winding(geometry, points):
    """Generalized winding number of each point (about 1 inside a closed surface, 0 outside)."""
    c = geometry.corners().astype(np.float64)
    total = np.zeros(len(points))
    step = max(1, (1 << 18) // max(len(c), 1))   # Point-triangle pairs per chunk: tens of MB of temporaries.
    for start in range(0, len(points), step):
        p = points[start:start + step, None, :].astype(np.float64)
        a, b, d = c[None, :, 0] - p, c[None, :, 1] - p, c[None, :, 2] - p
        la, lb, ld = (np.linalg.norm(v, axis=2) for v in (a, b, d))
        numerator = (a * np.cross(b, d)).sum(2)
        denominator = la * lb * ld + (a * b).sum(2) * ld + (b * d).sum(2) * la + (d * a).sum(2) * lb
        total[start:start + step] = np.arctan2(numerator, denominator).sum(1) / (2.0 * math.pi)
    return total


def collider_fit(mesh, hull, collider, kernels, diagonal, count=SAMPLES):
    """How a collider fits a mesh: against the mesh's convex hull, and which mesh points it leaves outside."""
    shape = topology(collider)
    # A collider without faces encloses nothing: never closed, whatever its (empty) edges say.
    is_closed = collider.triangle_count > 0 and shape["open_edges"] == 0 and shape["non_manifold_edges"] == 0
    tolerance = 1e-5 * max(diagonal, 1e-9)
    convex = _convex(collider, tolerance) if is_closed else False
    wound = _consistent_winding(collider) if collider.triangle_count else True
    hull_volume = _convex_volume(hull) if hull is not None and hull.triangle_count else None
    if is_closed and convex:
        volume = _convex_volume(collider)
    elif is_closed and wound:
        volume = abs(_signed_volume(collider))
    else:
        volume = None   # Open, or faces wound both ways: no volume to state.
    out = {"tris": collider.triangle_count, "vertices": int(len(np.unique(collider.tris))), "closed": is_closed,
           "convex": convex, "consistent_winding": wound, "volume_m3": _round(volume),
           "mesh_hull_volume_m3": _round(hull_volume)}
    if hull is None:   # A build without Bullet has no convex hull operator (the development preset).
        out["hull"] = "not measured: this build has no convex hull"
    if volume is not None and hull_volume:
        out["excess_over_hull"] = _round(volume / hull_volume - 1.0)
    if collider.triangle_count and hull is not None:
        facts, _ = Comparison(hull, Samples(hull, count), collider, kernels, count).hausdorff(diagonal)
        out["hull_distance"] = facts
    samples = Samples(mesh, count=min(count, 4096))
    points = samples.points[:: max(1, len(samples.points) // 8192)]
    how = "none: the collider is open" if collider.triangle_count else "none: the collider has no faces"
    if is_closed and convex:
        normals, offsets = _outward(collider)
        outside = (points.astype(np.float64) @ normals.T - offsets > tolerance).any(1)
        how = "half-spaces of the convex collider's faces"
    elif is_closed and wound:
        affordable = MAX_WINDING_WORK // max(collider.triangle_count, 1)
        if affordable >= MIN_WINDING_POINTS:
            points = points[:: max(1, -(-len(points) // affordable))]
            distance, _, _ = _nearest(kernels, collider.surface(kernels), points)
            outside = (np.abs(_winding(collider, points)) < 0.5) & (distance > tolerance)
            how = "generalized winding number below 0.5"
        else:
            outside = None
            how = "none: the collider has too many triangles for the winding test (%d)" % collider.triangle_count
    else:
        outside = None
        if is_closed:
            how = "none: the collider's faces are wound both ways"
    if is_closed and outside is not None:
        if how.startswith("half"):
            distance, _, _ = _nearest(kernels, collider.surface(kernels), points)
        out["mesh_outside"] = _round(float(outside.mean()), 4)
        out["mesh_outside_max_m"] = _length(float(distance[outside].max()) if outside.any() else 0.0)
    out["points"] = len(points)
    out["inside_test"] = how
    return out
