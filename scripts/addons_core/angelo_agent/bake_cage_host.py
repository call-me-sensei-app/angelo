# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A high-to-low bake's measured distances (``bake_cage``) on Blender's main thread.

The target and the sources are read as Blender renders them (the evaluated mesh, every enabled modifier, in world
space) and the sources become one BVH (``mathutils.bvhtree``), which ``hits`` walks: each ray is cast again from just
past its last hit until it leaves the reach (outward: the end of the sources), so every surface on the way is
counted (overlapping parts included).
Nothing in the scene is written. A job's admission runs twice (its approval, then its start), so the last result is
kept under a digest of the very arrays it measured: the same geometry is measured once, and any change measures again.

Contract: doc/angelo/long_jobs.rst, "High to low".
"""

import hashlib

import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from . import bake_cage

MAX_HITS = 32       # Surfaces one ray may pass through before the walk stops (a deeper stack is not a bake surface).
STEP = 1e-6         # Of the reach: how far past a hit the next cast starts.
_last = (None, None)   # (digest, result)


def _world(obj, depsgraph):
    """The object's evaluated triangles in world space: (positions (V, 3) float64, tris (T, 3) int32)."""
    owner = obj.evaluated_get(depsgraph)
    mesh = owner.to_mesh()
    try:
        mesh.calc_loop_triangles()
        tris = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int32)
        mesh.loop_triangles.foreach_get("vertices", tris)
        positions = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
        mesh.vertices.foreach_get("co", positions)
    finally:
        owner.to_mesh_clear()
    m = np.asarray(obj.matrix_world, dtype=np.float64)
    world = positions.reshape(-1, 3).astype(np.float64) @ m[:3, :3].T + m[:3, 3]
    return world, tris.reshape(-1, 3)


def _caster(tree, extent):
    """``bake_cage``'s ``hits`` over ``tree``. ``extent`` is the diagonal of a box holding the target and every source:
    a ray from a point in it meets no surface farther than that, so ``reach=None`` (to the end of the sources) casts
    that far."""
    def hits(origin, direction, reach):
        reach = extent if reach is None else reach
        out, travelled = [], 0.0
        start, way = Vector(origin), Vector(direction)
        step = STEP * max(reach, 1e-9)
        while len(out) < MAX_HITS:
            location, normal, _index, distance = tree.ray_cast(start, way, reach - travelled)
            if location is None:
                break
            travelled += distance
            out.append((travelled, normal.dot(way) > 0.0))
            start = location + way * step
            travelled += step
            if travelled >= reach:
                break
        return out
    return hits


def measure(depsgraph, target, sources):
    """``bake_cage.separation`` for baking ``sources`` onto ``target``, read from ``depsgraph``."""
    global _last
    target_arrays = _world(target, depsgraph)
    source_arrays = [_world(source, depsgraph) for source in sources]
    digest = hashlib.blake2b(digest_size=16)
    for positions, tris in [target_arrays, *source_arrays]:
        digest.update(positions.tobytes())
        digest.update(tris.tobytes())
        digest.update(b"|")
    key = digest.hexdigest()
    if _last[0] == key:
        return dict(_last[1])
    offset, positions, tris = 0, [], []
    for source_positions, source_tris in source_arrays:
        positions.append(source_positions)
        tris.append(source_tris + offset)
        offset += len(source_positions)
    every = np.concatenate([target_arrays[0], *positions])
    extent = float(np.linalg.norm(every.max(axis=0) - every.min(axis=0))) if len(every) else 0.0
    tree = BVHTree.FromPolygons(np.concatenate(positions).tolist(), np.concatenate(tris).tolist(), all_triangles=True)
    from .game_ready_metrics import Geometry   # Loaded with the first bake that measures (nothing at load).
    result = bake_cage.separation(Geometry(*target_arrays), _caster(tree, extent))
    _last = (key, result)
    return dict(result)
