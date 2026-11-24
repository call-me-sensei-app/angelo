# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A high-to-low bake's extrusion and ray distance, measured from the meshes. No Blender dependency.

Contract: doc/angelo/long_jobs.rst, "High to low". Blender casts each texel's ray from the target's surface pushed
out by the extrusion, back along its normal, at most the maximum ray distance, and bakes the first source surface the
ray meets. Left at Blender's defaults (0 and 0) a source surface outside the target is never baked, and a person
otherwise guesses both numbers. ``separation`` measures them at area-weighted points of the target
(``game_ready_metrics.Samples``, fixed seed), along each point's face normal:

* **Inside the sources** (more exits than entries on the ray outward): the point's surface is where the ray has left
  every source volume it was in, so parts that overlap or sink into each other count as one, and a separate part
  beyond open space (the other side of a crevice) is never reached. That distance is the point's *outward* need.
* **Outside them**: the first source surface inward is its *inward* need.
* Neither (not inside, and no source surface within ``REACH`` of the target's size inward): the point is not
  covered. ``REACH`` bounds only that search for a surface outside the point; a point inside the sources is covered
  however far their outer surface lies (a thick shell, a part rising from the surface), and the gap rule, not a
  distance, keeps a separate part from being reached.

The extrusion is the ``QUANTILE`` of the outward needs times ``MARGIN``; the ray distance adds the inward needs'
quantile times ``MARGIN``. A quantile rather than the largest need keeps one stray part from pushing every ray past
the surfaces it should meet; the points it leaves out are reported (``missed_share``) with the share not covered, so
the agent and the person can see when a cage is needed. ``hits(origin, direction, reach)`` is the host's ray query:
every source hit within ``reach`` (``None``: to the end of the sources), sorted, as ``(distance, exits)``
(``exits``: the ray leaves the surface's front side); host tests pass a NumPy caster. The cast outward runs to the
end, because whether a point is inside the sources depends on every surface ahead of it.
"""

import numpy as np

SAMPLES = 4096       # Area-weighted points (plus every vertex of the target).
QUANTILE = 0.995     # Of the points' needs: a need rarer than half a percent of the surface is reported, not followed.
MARGIN = 1.1         # On each distance: rays start clear of the surface they should meet.
REACH = 0.25         # Of the target's bounding-box diagonal: a source farther inward from a point outside every
                     # source is not this surface. Never bounds the walk out of a point inside them.
FLOOR = 1e-3         # Of the diagonal: the least ray distance, since Blender reads 0 as no limit.
DIGITS = 6


def wanted(request, cage, scene_extrusion, scene_ray):
    """Whether a high-to-low bake measures its distances: none in the request, no cage, and the scene's bake settings
    still at Blender's defaults (0, 0). Settings a person changed stay theirs."""
    return ("extrusion" not in request and "ray_distance" not in request and cage is None
            and scene_extrusion == 0.0 and scene_ray == 0.0)


def _normals(geometry, tri):
    corners = geometry.corners().astype(np.float64)[tri]
    normal = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    length = np.linalg.norm(normal, axis=1)
    return normal / np.where(length > 0.0, length, 1.0)[:, None], length > 0.0


def _need(hits, point, normal, reach):
    """``(outward, inward)`` for one point, or None when it is outside every source and none lies within ``reach``
    inward. Every hit ahead is counted: a truncated list would misjudge whether the point is inside."""
    ahead = hits(point, normal, None)
    depth = sum(1 if exits else -1 for _distance, exits in ahead)
    if depth > 0:   # Inside: walk out until no source volume holds the point.
        for distance, exits in ahead:
            depth += -1 if exits else 1
            if depth <= 0:
                return distance, 0.0
        return None
    for distance, exits in hits(point, -normal, reach):
        if not exits:   # The first surface met from outside.
            return 0.0, distance
    return None


def _round(value):
    return round(float(value), DIGITS)


def separation(target, hits, samples=SAMPLES):
    """The measured distances for baking onto ``target`` (``game_ready_metrics.Geometry``, world space, scene units)
    from the sources ``hits`` casts against: ``{covered_share}`` alone when no point is covered, else also
    ``extrusion``, ``ray_distance``, the quantiles they came from (``outward``, ``inward``) and ``missed_share``.
    A point is covered when it is inside the sources or has one within ``REACH`` inward; ``missed_share`` is the
    share of covered points the distances leave out."""
    low, high = target.bounds()
    diagonal = float(np.linalg.norm(high - low))
    reach = REACH * diagonal
    from .game_ready_metrics import Samples   # Loaded with the first bake that measures (nothing at load).
    points = Samples(target, count=samples)
    normals, valid = _normals(target, points.tri)
    outward, inward = [], []
    total = 0
    for point, normal, ok in zip(points.points.astype(np.float64), normals, valid):
        if not ok:
            continue
        total += 1
        need = _need(hits, point, normal, reach)
        if need is not None:
            outward.append(need[0])
            inward.append(need[1])
    covered = len(outward)
    result = {"covered_share": _round(covered / total if total else 0.0)}
    if not covered:
        return result
    outward, inward = np.asarray(outward), np.asarray(inward)
    out_q = float(np.quantile(outward, QUANTILE))
    in_q = float(np.quantile(inward, QUANTILE))
    extrusion = out_q * MARGIN
    ray = max(extrusion + in_q * MARGIN, extrusion + FLOOR * diagonal)
    missed = int(np.count_nonzero((outward > extrusion) | (inward > ray - extrusion)))
    result.update(extrusion=_round(extrusion), ray_distance=_round(ray), outward=_round(out_q), inward=_round(in_q),
                  missed_share=_round(missed / covered))
    return result
