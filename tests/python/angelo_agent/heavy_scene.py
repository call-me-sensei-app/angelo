# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The dense castle scene of ``scenario-heavy``: a seeded plan and its geometry as NumPy arrays (no bpy).

About 2,000 objects and 2.7 million vertices: a dense terrain with hills, a
castle with high-resolution stone walls and towers, a forest, rocks, a
village, props, a flag, a lone watchtower and two very dense carvings. It is
what a production scene looks like to an agent: big, with most of its weight
in a few meshes nobody names.

Everything is a pure function of ``SEED``: object placement uses a small
integer generator (``Stream``, never NumPy's, whose streams may change between
NumPy versions), and every coordinate is rounded to ``QUANTUM`` before it is
stored as float32, so the same arrays come out on any machine. The graders
(``heavy_checks``) rebuild them to know exactly what an untouched object must
still hold; ``heavy_scene_blend`` turns them into Blender objects.

The scene carries the five defects and facts the heavy tasks ask about:
the 'Flag' stands in the courtyard far from the terrain's highest point
(``SUMMIT`` is the hill that holds it, with lower look-alike hills elsewhere);
120 of the 300 trees float or are sunk; 'Courtyard Statue' has the most
polygons, a little more than 'Keep Relief' and 'Terrain'; patches of faces of
three curtain walls are wound inwards; the 'Watchtower' and its roof stand on
the plain.

A mesh is ``(positions float32 (n, 3), face sizes int32 (m,), corner vertices int32 (k,))`` in object space.
NumPy is imported inside the functions, so the module loads where it is absent (host tests).
"""

import math

SEED = 20261015
QUANTUM = 1.0e-4
TERRAIN_SIZE, TERRAIN_GRID = 400.0, 832          # 832 x 832 vertices: 690,561 quads.
PLAIN_RADIUS, HILLS_FROM = 80.0, 100.0           # Flat within the first, hills beyond the second.
SUMMIT = (-132.5, 121.5, 36.0, 26.0)             # x, y, height, spread of the highest hill.
DECOYS = ((146.0, -128.0, 35.1, 24.0), (-148.0, -126.0, 34.8, 30.0), (126.0, 140.0, 34.5, 22.0))
BROAD = ((0.0, 165.0, 16.0, 42.0), (172.0, 18.0, 20.0, 34.0), (-176.0, 8.0, 18.0, 30.0))
STATUE_SEGMENTS, STATUE_RINGS = 1024, 690        # 706,560 faces: the heaviest object.
RELIEF_GRID = 840                                # 703,921 faces.
WALL_STEP, TOWER_SEGMENTS, TOWER_STEP = 0.1, 192, 0.15
TREES, FLOATING, SUNK, ROCKS, HOUSES, POSTS, CRATES, BARRELS = 300, 75, 45, 900, 40, 400, 100, 80
FLAG_AT, WATCHTOWER_AT = (6.0, -6.0), (40.0, -62.0)   # The watchtower and the 10 m it moves stay on the plain.
# The curtain wall's runs: start, end (world XY, the wall's centreline) and whether a patch of faces is wound inwards.
WALLS = {"North": ((-17.5, 15.0), (17.5, 15.0), True), "East": ((20.0, -12.5), (20.0, 12.5), True),
         "West": ((-20.0, -12.5), (-20.0, 12.5), False), "South West": ((-17.5, -15.0), (-4.8, -15.0), True),
         "South East": ((4.8, -15.0), (17.5, -15.0), False)}
WALL_THICK, WALL_TOP = 2.0, 9.0


def q(values):
    """Round to ``QUANTUM`` and store as float32: identical bits on every machine."""
    import numpy
    return (numpy.round(numpy.asarray(values, dtype=numpy.float64) / QUANTUM) * QUANTUM).astype(numpy.float32)


class Stream:
    """A small seeded integer generator (64-bit LCG, high bits) for placement: the same numbers everywhere."""

    def __init__(self, seed):
        self.state = (seed * 6364136223846793005 + 1442695040888963407) % 2 ** 64

    def uniform(self, low=0.0, high=1.0):
        self.state = (self.state * 6364136223846793005 + 1442695040888963407) % 2 ** 64
        return low + (high - low) * ((self.state >> 11) / float(2 ** 53))


def mesh(positions, faces):
    """Arrays from python faces (lists of vertex indices, any size)."""
    import numpy
    sizes = numpy.array([len(face) for face in faces], dtype=numpy.int32)
    corners = numpy.array([index for face in faces for index in face], dtype=numpy.int32)
    return q(positions).reshape(-1, 3), sizes, corners


def grid_faces(columns, rows, offset=0, flip=False):
    """Quads of a row-major grid (``columns`` fastest), counter-clockwise from +Z unless ``flip``."""
    import numpy
    i = numpy.arange(columns - 1)
    j = numpy.arange(rows - 1)
    base = (j[:, None] * columns + i[None, :]).ravel() + offset
    quads = numpy.stack([base, base + 1, base + 1 + columns, base + columns], axis=1)
    if flip:
        quads = quads[:, ::-1]
    return numpy.full(len(quads), 4, numpy.int32), quads.astype(numpy.int32).ravel()


def merge(*parts):
    """One mesh of several (positions, sizes, corners)."""
    import numpy
    positions, sizes, corners, offset = [], [], [], 0
    for part_positions, part_sizes, part_corners in parts:
        positions.append(part_positions)
        sizes.append(part_sizes)
        corners.append(part_corners + offset)
        offset += len(part_positions)
    return numpy.concatenate(positions), numpy.concatenate(sizes), numpy.concatenate(corners).astype(numpy.int32)


# ---------------------------------------------------------------- terrain

def height(x, y):
    """The terrain's height (float64 arrays): a flat plain round the castle, hills beyond, low ripples."""
    import numpy
    x, y = numpy.asarray(x, dtype=numpy.float64), numpy.asarray(y, dtype=numpy.float64)
    hills = numpy.zeros(numpy.broadcast(x, y).shape)
    for cx, cy, amount, spread in (SUMMIT,) + DECOYS + BROAD:
        hills = hills + amount * numpy.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2.0 * spread ** 2))
    ripples = (0.18 * numpy.sin(0.11 * x + 0.7) * numpy.cos(0.093 * y - 0.3) + 0.1 * numpy.sin(0.23 * x + 0.41 * y)
               + 0.06 * numpy.cos(0.37 * x - 0.29 * y + 1.1))
    radius = numpy.hypot(x, y)
    ramp = numpy.clip((radius - PLAIN_RADIUS) / (HILLS_FROM - PLAIN_RADIUS), 0.0, 1.0)
    return (hills + ripples) * ramp * ramp * (3.0 - 2.0 * ramp)


def terrain_axis():
    import numpy
    return numpy.linspace(-TERRAIN_SIZE / 2, TERRAIN_SIZE / 2, TERRAIN_GRID)


def terrain():
    import numpy
    axis = terrain_axis()
    x, y = numpy.meshgrid(axis, axis)          # Row-major, X fastest.
    positions = q(numpy.stack([x.ravel(), y.ravel(), height(x, y).ravel()], axis=1))
    return (positions,) + grid_faces(TERRAIN_GRID, TERRAIN_GRID)


def surface(x, y):
    """The stored terrain's height at (x, y), bilinear between its quantized vertices (within millimetres of
    the triangles Blender draws on this smooth a surface)."""
    import numpy
    axis = terrain_axis()
    step = axis[1] - axis[0]
    u = min(max((x - axis[0]) / step, 0.0), TERRAIN_GRID - 1.000001)
    v = min(max((y - axis[0]) / step, 0.0), TERRAIN_GRID - 1.000001)
    i, j = int(u), int(v)
    corners = q(height(axis[[i, i + 1, i, i + 1]], axis[[j, j, j + 1, j + 1]])).astype(numpy.float64)
    fu, fv = u - i, v - j
    return float((corners[0] * (1 - fu) + corners[1] * fu) * (1 - fv) + (corners[2] * (1 - fu) + corners[3] * fu) * fv)


# ---------------------------------------------------------------- dense pieces

def statue():
    """A lumpy figure on its pedestal: a displaced sphere of ``STATUE_SEGMENTS`` x ``STATUE_RINGS`` faces."""
    import numpy
    theta = numpy.linspace(0.0, 2 * math.pi, STATUE_SEGMENTS, endpoint=False)
    phi = numpy.linspace(-math.pi / 2, math.pi / 2, STATUE_RINGS + 1)[1:-1]
    t, p = numpy.meshgrid(theta, phi)
    lumps = (1.0 + 0.12 * numpy.sin(5 * t) * numpy.cos(3 * p) + 0.05 * numpy.sin(17 * t + 11 * p)
             + 0.02 * numpy.cos(41 * t))
    ring = numpy.stack([0.8 * lumps * numpy.cos(p) * numpy.cos(t), 0.6 * lumps * numpy.cos(p) * numpy.sin(t),
                        1.2 + 1.2 * numpy.sin(p)], axis=-1).reshape(-1, 3)
    positions = numpy.concatenate([[[0.0, 0.0, 0.0]], ring, [[0.0, 0.0, 2.4]]])
    n, rings = STATUE_SEGMENTS, STATUE_RINGS - 1
    i = numpy.arange(n)
    bottom = numpy.stack([numpy.zeros(n), 1 + (i + 1) % n, 1 + i], axis=1)
    r = numpy.arange(rings - 1)[:, None] * n + 1
    quads = numpy.stack([r + i, r + (i + 1) % n, r + n + (i + 1) % n, r + n + i], axis=-1).reshape(-1, 4)
    last, top = 1 + (rings - 1) * n, 1 + rings * n
    cap = numpy.stack([last + i, last + (i + 1) % n, numpy.full(n, top)], axis=1)
    sizes = numpy.concatenate([numpy.full(n, 3), numpy.full(len(quads), 4), numpy.full(n, 3)]).astype(numpy.int32)
    corners = numpy.concatenate([bottom.ravel(), quads.ravel(), cap.ravel()]).astype(numpy.int32)
    return q(positions), sizes, corners


def relief():
    """A carved panel facing -Y: a ``RELIEF_GRID`` square grid 2.6 m across with 3 cm of carving."""
    import numpy
    axis = numpy.linspace(-1.3, 1.3, RELIEF_GRID)
    x, z = numpy.meshgrid(axis, axis)
    depth = 0.015 * (numpy.sin(9 * x) * numpy.cos(7 * z) + numpy.sin(23 * x + 17 * z) * 0.5 + numpy.cos(31 * z) * 0.3)
    positions = q(numpy.stack([x.ravel(), -depth.ravel(), z.ravel()], axis=1))
    return (positions,) + grid_faces(RELIEF_GRID, RELIEF_GRID, flip=True)


def _jointed(offset_along, height_value, along, period=0.9, course=0.5):
    """1 where a stone joint (mortar) runs: every ``course`` metres up, staggered every ``period`` along."""
    import numpy
    row = numpy.floor(height_value / course + 1e-6)
    horizontal = numpy.abs(height_value - numpy.round(height_value / course) * course) < 0.02
    shifted = along + offset_along + (row % 2) * period / 2
    vertical = numpy.abs(shifted - numpy.round(shifted / period) * period) < 0.04
    return horizontal | vertical


def section_loop(step=WALL_STEP):
    """The wall's cross-section (y, z) every ``step`` metres round a 2 x 9 m rectangle, and each point's side."""
    import numpy
    half, top = WALL_THICK / 2, WALL_TOP
    points, sides = [], []
    for (y0, z0), (y1, z1), side in (((-half, 0.0), (half, 0.0), "bottom"), ((half, 0.0), (half, top), "outer"),
                                     ((half, top), (-half, top), "top"), ((-half, top), (-half, 0.0), "inner")):
        count = int(round(math.hypot(y1 - y0, z1 - z0) / step))
        for k in range(count):
            points.append((y0 + (y1 - y0) * k / count, z0 + (z1 - z0) * k / count))
            sides.append(side)
    return numpy.array(points), sides


def wall(length, flipped, step=WALL_STEP):
    """A closed 2 x 9 m wall run along +X from 0 to ``length``, its faces mortar-jointed, and with ``flipped``
    a patch of its outer face wound inwards (about 3 x 3 m)."""
    import numpy
    loop, sides = section_loop(step)
    count = len(loop)
    xs = numpy.linspace(0.0, length, int(round(length / step)) + 1)
    x = numpy.repeat(xs, count)
    y, z = numpy.tile(loop[:, 0], len(xs)), numpy.tile(loop[:, 1], len(xs))
    face_y = numpy.abs(numpy.abs(y) - WALL_THICK / 2) < 1e-6
    inset = face_y & _jointed(0.0, z, x)
    y = numpy.where(inset, y - numpy.sign(y) * 0.02, y)
    positions = q(numpy.stack([x, y, z], axis=1))
    rings = len(xs)
    i = numpy.arange(count)
    r = numpy.arange(rings - 1)[:, None] * count
    quads = numpy.stack([r + i, r + (i + 1) % count, r + count + (i + 1) % count, r + count + i], axis=-1)
    if flipped:
        outer = [k for k, side in enumerate(sides) if side == "outer"]
        lo, hi = int(rings * 0.4), int(rings * 0.4) + 30
        quads[lo:hi, outer[25]:outer[25] + 30] = quads[lo:hi, outer[25]:outer[25] + 30, ::-1]
    quads = quads.reshape(-1, 4)
    caps = [list(reversed(range(count))), list(range((rings - 1) * count, rings * count))]
    sizes = numpy.concatenate([numpy.full(len(quads), 4), [count, count]]).astype(numpy.int32)
    corners = numpy.concatenate([quads.ravel(), caps[0], caps[1]]).astype(numpy.int32)
    return positions, sizes, corners


def cylinder(radius, depth, segments, step, jointed=False, z0=0.0):
    """A closed vertical cylinder from z0 to z0 + depth with rings every ``step`` metres (caps are n-gons)."""
    import numpy
    zs = numpy.linspace(z0, z0 + depth, max(2, int(round(depth / step)) + 1))
    angles = numpy.linspace(0.0, 2 * math.pi, segments, endpoint=False)
    a, z = numpy.meshgrid(angles, zs)
    r = numpy.full(a.shape, radius)
    if jointed:
        r = numpy.where(_jointed(0.0, z - z0, a * radius), radius - 0.02, r)
    positions = q(numpy.stack([(r * numpy.cos(a)).ravel(), (r * numpy.sin(a)).ravel(), z.ravel()], axis=1))
    rings, i = len(zs), numpy.arange(segments)
    base = numpy.arange(rings - 1)[:, None] * segments
    quads = numpy.stack([base + i, base + (i + 1) % segments, base + segments + (i + 1) % segments,
                         base + segments + i], axis=-1).reshape(-1, 4)
    caps = numpy.concatenate([i[::-1], (rings - 1) * segments + i])
    sizes = numpy.concatenate([numpy.full(len(quads), 4), [segments, segments]]).astype(numpy.int32)
    return positions, sizes, numpy.concatenate([quads.ravel(), caps]).astype(numpy.int32)


def cone(radius, base_z, apex_z, segments, rings):
    """A closed cone with ``rings`` rings from its base to just under the apex, an apex point and a flat base."""
    import numpy
    fractions = numpy.linspace(0.0, 1.0, rings, endpoint=False)
    angles = numpy.linspace(0.0, 2 * math.pi, segments, endpoint=False)
    a, f = numpy.meshgrid(angles, fractions)
    ring = numpy.stack([(radius * (1 - f) * numpy.cos(a)).ravel(), (radius * (1 - f) * numpy.sin(a)).ravel(),
                        (base_z + (apex_z - base_z) * f).ravel()], axis=1)
    positions = q(numpy.concatenate([ring, [[0.0, 0.0, apex_z]]]))
    i, apex = numpy.arange(segments), rings * segments
    base = numpy.arange(rings - 1)[:, None] * segments
    quads = numpy.stack([base + i, base + (i + 1) % segments, base + segments + (i + 1) % segments,
                         base + segments + i], axis=-1).reshape(-1, 4)
    last = (rings - 1) * segments
    tip = numpy.stack([last + i, last + (i + 1) % segments, numpy.full(segments, apex)], axis=1)
    sizes = numpy.concatenate([numpy.full(len(quads), 4), numpy.full(segments, 3), [segments]]).astype(numpy.int32)
    return positions, sizes, numpy.concatenate([quads.ravel(), tip.ravel(), i[::-1]]).astype(numpy.int32)


def sphere(radius, segments, rings, centre=(0.0, 0.0, 0.0), scale=(1.0, 1.0, 1.0), lumps=None):
    """A UV sphere; ``lumps`` (a Stream) roughens each vertex's radius by up to 15 %."""
    import scenario_build_kit as kit
    vertices, faces = kit.uv_sphere_geometry(radius, segments, rings, centre, scale=scale)
    if lumps is not None:
        cx, cy, cz = centre
        vertices = [(cx + (x - cx) * f, cy + (y - cy) * f, cz + (z - cz) * f)
                    for (x, y, z), f in ((v, lumps.uniform(0.85, 1.15)) for v in vertices)]
    return mesh(vertices, faces)


def tree(size, stream):
    """A trunk on its own origin (its foot at z = 0) and a rounded crown of leaves: one mesh."""
    trunk = cylinder(0.22 * size, 3.0 * size, 12, 0.75 * size)
    crown = sphere(1.8 * size, 16, 10, (0.0, 0.0, 4.6 * size), (1.0, 1.0, 1.3), stream)
    return merge(trunk, crown)


def from_kit(vertices, faces):
    return mesh(vertices, faces)
