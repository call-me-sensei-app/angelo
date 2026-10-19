# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Analytic heads for the face and hair one-shot tests: signed distance fields and a mesher (NumPy only).

A head design is a list of primitives (ellipsoids, round cones, spheres, half
spaces) joined by smooth unions and carved by smooth subtractions, the way a
sculptor blocks forms in and cuts sockets out. ``distance(design, points)``
evaluates it; ``surface_nets(design, ...)`` turns it into a closed quad mesh
(one vertex per surface cell, projected onto the surface; one quad per grid
edge the surface crosses), which ``head_kit`` makes into Blender meshes.

The designs (``head_designs``) are not a look a model must find by itself:
the graders measure a side's mesh against the ground truth's own mesh, both
read by the same ray casts (``face_grading``). They only have to be plausible
heads, built the same way every time.

Axes: metres, Z up, the face looks along -Y, the head is symmetric about x = 0.
"""

import math

import numpy

# ---------------------------------------------------------------- primitives


def rotation(degrees):
    """3x3 rotation from XYZ Euler angles in degrees (Blender's default order)."""
    x, y, z = (math.radians(value) for value in degrees)
    rx = numpy.array([[1, 0, 0], [0, math.cos(x), -math.sin(x)], [0, math.sin(x), math.cos(x)]])
    ry = numpy.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]])
    rz = numpy.array([[math.cos(z), -math.sin(z), 0], [math.sin(z), math.cos(z), 0], [0, 0, 1]])
    return rz @ ry @ rx


def _mirrored(points, primitive):
    if primitive.get("mirror"):
        return numpy.column_stack((numpy.abs(points[:, 0]), points[:, 1], points[:, 2]))
    return points


def _local(points, primitive):
    """Points in the primitive's frame: mirrored across x = 0 for a pair, then moved and rotated."""
    local = _mirrored(points, primitive) - numpy.asarray(primitive["centre"], dtype=numpy.float64)
    if primitive.get("rotation"):
        local = local @ rotation(primitive["rotation"])   # Row vectors: p @ R is R^T p, into the frame.
    return local


def _ellipsoid(local, radii):
    """Inigo Quilez's bound for an ellipsoid: exact on the surface, close to the distance near it."""
    radii = numpy.asarray(radii, dtype=numpy.float64)
    k0 = numpy.linalg.norm(local / radii, axis=1)
    k1 = numpy.linalg.norm(local / (radii * radii), axis=1)
    return k0 * (k0 - 1.0) / numpy.maximum(k1, 1e-12)


def _round_cone(points, a, b, ra, rb):
    """A capsule whose radius runs from ``ra`` at ``a`` to ``rb`` at ``b`` (exact distance, Quilez)."""
    a, b = numpy.asarray(a, dtype=numpy.float64), numpy.asarray(b, dtype=numpy.float64)
    ba = b - a
    l2 = float(ba @ ba)
    rr = ra - rb
    a2 = l2 - rr * rr
    il2 = 1.0 / l2
    pa = points - a
    y = pa @ ba
    z = y - l2
    x2 = numpy.sum((pa * l2 - numpy.outer(y, ba)) ** 2, axis=1)
    y2, z2 = y * y * l2, z * z * l2
    k = math.copysign(1.0, rr) * rr * rr * x2
    side = (numpy.sqrt(x2 * a2 * il2) + y * rr) * il2 - ra
    result = numpy.where(numpy.sign(z) * a2 * z2 > k, numpy.sqrt(x2 + z2) * il2 - rb, side)
    return numpy.where(numpy.sign(y) * a2 * y2 < k, numpy.sqrt(x2 + y2) * il2 - ra, result)


def primitive_distance(primitive, points):
    kind = primitive["kind"]
    if kind == "ellipsoid":
        return _ellipsoid(_local(points, primitive), primitive["radii"])
    if kind == "sphere":
        return numpy.linalg.norm(_local(points, primitive), axis=1) - primitive["radius"]
    if kind == "round_cone":
        return _round_cone(_mirrored(points, primitive), primitive["a"], primitive["b"], primitive["ra"],
                           primitive["rb"])
    if kind == "halfspace":   # Negative where normal . p > offset.
        return primitive["offset"] - points @ numpy.asarray(primitive["normal"], dtype=numpy.float64)
    raise ValueError("Unknown primitive %r" % kind)


def smooth_min(a, b, k):
    """Polynomial smooth minimum (a soft union) with blend width ``k``; ``k`` 0 is the plain minimum."""
    if k <= 0:
        return numpy.minimum(a, b)
    h = numpy.maximum(k - numpy.abs(a - b), 0.0) / k
    return numpy.minimum(a, b) - h * h * k * 0.25


def smooth_max(a, b, k):
    return -smooth_min(-a, -b, k)


def distance(design, points):
    """Signed distance (negative inside) of a design at ``points`` (N x 3): its steps in order."""
    points = numpy.asarray(points, dtype=numpy.float64).reshape(-1, 3)
    result = None
    for step in design["steps"]:
        value = primitive_distance(step, points)
        op = step.get("op", "add")
        if result is None:
            result = value
        elif op == "add":
            result = smooth_min(result, value, step.get("blend", 0.0))
        elif op == "cut":
            result = smooth_max(result, -value, step.get("blend", 0.0))
        elif op == "keep":   # Intersection: the flat cut at the bottom of the neck.
            result = smooth_max(result, value, step.get("blend", 0.0))
        else:
            raise ValueError("Unknown step op %r" % op)
    return result


def gradient(design, points, step=2.5e-4):
    """Central-difference gradient of the distance at ``points``."""
    columns = [(distance(design, points + offset) - distance(design, points - offset)) / (2 * step)
               for offset in numpy.eye(3) * step]
    return numpy.column_stack(columns)


# ---------------------------------------------------------------- the mesher


def _grid_values(design, low, size, shape, coarse=4):
    """Distance at every grid point, computed only in a band near the surface (a coarse pass picks it)."""
    counts = [max(2, (n - 1) // coarse + 2) for n in shape]
    axes = [numpy.arange(c) * coarse * size + low[axis] for axis, c in enumerate(counts)]
    coarse_points = numpy.stack(numpy.meshgrid(*axes, indexing="ij"), axis=-1).reshape(-1, 3)
    coarse_values = distance(design, coarse_points).reshape(counts)
    nearest = [numpy.minimum(numpy.round(numpy.arange(n) / coarse).astype(int), c - 1) for n, c in zip(shape, counts)]
    near = coarse_values[numpy.ix_(*nearest)]
    values = numpy.where(near < 0, numpy.float32(-1.0), numpy.float32(1.0)).astype(numpy.float32)
    wanted = numpy.argwhere(numpy.abs(near) < coarse * size * 1.8)
    for start in range(0, len(wanted), 500000):
        chunk = wanted[start:start + 500000]
        values[tuple(chunk.T)] = distance(design, low + chunk * size)
    values[values == 0] = numpy.float32(1e-9)   # No grid point lies exactly on the surface.
    return values


def surface_nets(design, low, high, size, project=4):
    """A closed quad mesh of the design's surface inside the box ``low``..``high``, cells of ``size``.

    Returns ``(vertices (V x 3) float32, quads (Q x 4) int64)``, quads counter-clockwise seen from outside.
    The box must hold the whole surface with a cell to spare on every side.
    """
    low = numpy.asarray(low, dtype=numpy.float64)
    shape = tuple(int(math.ceil((h - l) / size)) + 1 for l, h in zip(low, high))
    inside = _grid_values(design, low, size, shape) < 0
    nx, ny, nz = shape
    cell_shape = (nx - 1, ny - 1, nz - 1)
    count = numpy.zeros(cell_shape, dtype=numpy.uint8)
    for k in (0, 1):
        for j in (0, 1):
            for i in (0, 1):
                count += inside[i:nx - 1 + i, j:ny - 1 + j, k:nz - 1 + k]
    cells = numpy.argwhere((count > 0) & (count < 8))
    del count
    ids = numpy.ravel_multi_index(tuple(cells.T), cell_shape)   # argwhere is in C order: ``ids`` is sorted.
    values = None
    offsets = numpy.array([(i, j, k) for k in (0, 1) for j in (0, 1) for i in (0, 1)])
    edges = [(a, b) for a in range(8) for b in range(a + 1, 8) if numpy.abs(offsets[a] - offsets[b]).sum() == 1]
    # A vertex starts at the mean of its cell's edge midpoints that change sign, then moves onto the surface.
    total, hits = numpy.zeros((len(cells), 3)), numpy.zeros(len(cells))
    for a, b in edges:
        ia, ib = inside[tuple((cells + offsets[a]).T)], inside[tuple((cells + offsets[b]).T)]
        crossing = ia != ib
        total += numpy.where(crossing[:, None], (offsets[a] + offsets[b]) * 0.5, 0.0)
        hits += crossing
    vertices = low + (cells + total / numpy.maximum(hits, 1)[:, None]) * size
    for _ in range(project):
        value = distance(design, vertices)
        grad = gradient(design, vertices)
        step = (value / numpy.maximum(numpy.sum(grad * grad, axis=1), 1e-12))[:, None] * grad
        length = numpy.linalg.norm(step, axis=1)
        step *= numpy.minimum(1.0, 0.9 * size / numpy.maximum(length, 1e-12))[:, None]   # Stay near the cell.
        vertices = vertices - step
    quads = []
    for axis in range(3):
        lower, upper = [slice(None)] * 3, [slice(None)] * 3
        lower[axis], upper[axis] = slice(0, shape[axis] - 1), slice(1, shape[axis])
        inside_lower = inside[tuple(lower)]
        change = inside_lower != inside[tuple(upper)]
        u, v = [d for d in range(3) if d != axis]
        for d in (u, v):   # An edge on the box's faces has no cell on one side.
            for end in (0, change.shape[d] - 1):
                index = [slice(None)] * 3
                index[d] = end
                change[tuple(index)] = False
        edge = numpy.argwhere(change)
        if not len(edge):
            continue
        ring = []
        for du, dv in ((-1, -1), (0, -1), (0, 0), (-1, 0)):   # Counter-clockwise in (u, v): normal u x v.
            cell = edge.copy()
            cell[:, u] += du
            cell[:, v] += dv
            ring.append(numpy.searchsorted(ids, numpy.ravel_multi_index(tuple(cell.T), cell_shape)))
        ring = numpy.column_stack(ring)
        # u x v is +x, -y, +z for the three axes; the outward normal points from the inside end to the outside end.
        reverse = inside_lower[tuple(edge.T)] if axis == 1 else ~inside_lower[tuple(edge.T)]
        ring[reverse] = ring[reverse][:, ::-1]
        quads.append(ring)
    quads = numpy.concatenate(quads) if quads else numpy.zeros((0, 4), dtype=numpy.int64)
    return vertices.astype(numpy.float32), quads.astype(numpy.int64)


def signed_volume(vertices, quads):
    """The enclosed volume of a closed quad mesh: positive when its faces wind outward."""
    v = numpy.asarray(vertices, dtype=numpy.float64)
    total = 0.0
    for a, b, c in ((0, 1, 2), (0, 2, 3)):
        p, q, r = v[quads[:, a]], v[quads[:, b]], v[quads[:, c]]
        total += numpy.einsum("ij,ij->i", p, numpy.cross(q, r)).sum() / 6.0
    return total
