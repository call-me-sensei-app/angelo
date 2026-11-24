# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""UV Layout's arithmetic: padding that survives mip levels, texel density, stacking matches, tiles. No Blender.

``uv_layout`` (Blender side) and the agent's ``kit.uv_layout`` use these; doc/angelo/uv_layout.rst is the contract.
NumPy only where arrays help (matching island shapes); everything else is plain arithmetic.
"""

import math
from types import MappingProxyType

import numpy as np

MAX_TILES = 100          # UDIM 1001-1100: a 10 x 10 grid.
MIN_TEXTURE, MAX_TEXTURE = 16, 32768
FILL = 0.72              # A tile is planned to this share of island area before its own pack decides.
MAX_MATCH = 4096         # Islands with more vertices are not stacked (the match compares every vertex pair).
MATCH_BLOCK = 1 << 20    # Coordinates compared at once (8 MiB of float64 per temporary): bounds the match's memory.
SAME_SIDE = 0.5          # Mean cosine between matched normals for the surfaces to face the same way.
MAX_TURNS = 256          # A round part is turned at most this many ways per flip; past it, it is not stacked.
PROBE = 16               # Points checked first on each try, so a wrong one costs a little, not every vertex pair.
# Pack Islands' shape per packer choice. Quality is Blender's Concave shape (0.71 against AABB's 0.47 on 60 plates);
# Fast is the shape with the better efficiency per second of Convex and AABB on the development probe
# (doc/angelo/uv_layout.rst). The agent's operation keeps Quality unless the person asks for speed.
PACKERS = MappingProxyType({"QUALITY": "CONCAVE", "FAST": "AABB"})   # Read-only: a layout admitted as Fast stays Fast.
DEFAULT_PACKER = "QUALITY"


def _texture(size):
    if isinstance(size, bool) or not isinstance(size, int) or not MIN_TEXTURE <= size <= MAX_TEXTURE:
        raise ValueError("texture size must be a whole number of pixels from %d to %d" % (MIN_TEXTURE, MAX_TEXTURE))
    return size


def mip_margin(texture_size, mips):
    """Pack Islands' fraction margin that keeps islands apart down to mip level ``mips``.

    Blender adds the margin on every side of an island, so two islands end 2 x margin apart. A texel of mip level m
    covers 2^m texels of the full image, and filtering that level reads one more texel, so islands stay apart at level
    m when the gap is at least 2^(m + 1) texels: a margin of 2^m texels, 2^m / size of the square.
    """
    size = _texture(texture_size)
    if isinstance(mips, bool) or not isinstance(mips, int) or not 0 <= mips <= int(math.log2(size)):
        raise ValueError("mips must be a whole number from 0 to %d for a %d px texture" % (int(math.log2(size)), size))
    return (2 ** mips) / size


# Below this share of the tile, padding leaves the islands less than a texture half the size on each side would give
# them whole: the layout says so, with fixes (doc/angelo/uv_layout.rst, "Too little room").
ROOM_FLOOR = 1 / 16


def _ceiling(boxes, areas, margin):
    """The most of one tile islands of these boxes (w, h) and true areas cover when each gets ``margin`` on every side:
    the scale s at which the padded boxes fill the tile (sum (s w + 2m)(s h + 2m) = 1), then their area at s."""
    count, box, half = len(boxes), sum(w * h for w, h in boxes), sum(w + h for w, h in boxes)
    rest = 4.0 * margin * margin * count - 1.0
    if not count or box <= 0.0 or rest >= 0.0:   # Nothing to lay out, or the padding alone fills the tile.
        return 0.0
    scale = (-2.0 * margin * half + math.sqrt(4.0 * margin * margin * half * half - 4.0 * box * rest)) / (2.0 * box)
    return min(1.0, scale * scale * sum(areas))


def room(islands, texture_size, mips):
    """Before packing: the share of its tile the most crowded group's islands can get with mip-safe padding (a ceiling a
    real pack stays under), whether that is under ``ROOM_FLOOR``, and the concrete fixes that clear it: the most mip
    levels, or the smallest texture (doubling, up to ``MAX_TEXTURE``), that would. ``islands`` is ``[(uv points,
    true uv area, group), ...]``; each group gets its own tile. None of a fix means none clears the floor."""
    groups = {}
    for points, area, group in islands:
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        boxes, areas = groups.setdefault(group, ([], []))
        boxes.append((max(xs) - min(xs), max(ys) - min(ys)))
        areas.append(area)

    def share(size, kept):
        margin = mip_margin(size, kept)
        return min((_ceiling(boxes, areas, margin) for boxes, areas in groups.values()), default=1.0)
    current = share(texture_size, mips)
    too_little = bool(groups) and current < ROOM_FLOOR
    fix_mips = next((m for m in range(mips - 1, -1, -1) if share(texture_size, m) >= ROOM_FLOOR), None)
    larger = [texture_size * 2 ** k for k in range(1, 16) if texture_size * 2 ** k <= MAX_TEXTURE]
    fix_size = next((s for s in larger if share(s, mips) >= ROOM_FLOOR), None)
    return {"share": round(current, 4), "floor": ROOM_FLOOR, "too_little": too_little,
            "fits_with_mips": fix_mips if too_little else None,
            "fits_with_texture_size": fix_size if too_little else None}


def fit_margin(islands, texture_size, mips, automatic):
    """``room`` for ``mips``, and the margin a layout uses. A margin someone set (``automatic`` False) is kept: too
    little room is then only said. One nobody set keeps ``mips`` where the islands have room and otherwise drops to the
    most mip levels that leave them ``ROOM_FLOOR`` (coordinator's decision, 2026-11-18), returned as ``lowered``
    ``{"from", "to", "keep_with_texture_size"}`` for the receipt and the person; None when it kept ``mips``, including
    when no margin leaves room."""
    first = room(islands, texture_size, mips)
    if not automatic or not first["too_little"] or first["fits_with_mips"] is None:
        return first, None
    fitted = first["fits_with_mips"]
    return room(islands, texture_size, fitted), {"from": mips, "to": fitted,
                                                 "keep_with_texture_size": first["fits_with_texture_size"]}


def mips_kept(gap_texels):
    """The deepest mip level a gap of ``gap_texels`` (full-size texels) keeps islands apart at; -1 for none."""
    if gap_texels < 2:
        return -1
    return int(math.floor(math.log2(gap_texels))) - 1


def polygon_area(uvs):
    """Unsigned area of a polygon given as (u, v) corners."""
    total = 0.0
    for i in range(len(uvs)):
        (x0, y0), (x1, y1) = uvs[i], uvs[(i + 1) % len(uvs)]
        total += x0 * y1 - x1 * y0
    return abs(total) / 2.0


def texel_density(uv_area, world_area, texture_size):
    """Pixels per metre: the texture's side times the square root of UV area (0-1 square) over surface area."""
    if world_area <= 0 or uv_area <= 0:
        return 0.0
    return _texture(texture_size) * math.sqrt(uv_area / world_area)


def density_scale(current, target):
    """The factor that brings an island at ``current`` px/m to ``target``."""
    if current <= 0 or target <= 0:
        raise ValueError("densities must be above 0 px/m")
    return target / current


def spread(densities, weights=None):
    """How even a set of densities is: (low, high, weighted mean), ignoring empty islands."""
    pairs = [(d, w) for d, w in zip(densities, weights or [1.0] * len(densities)) if d > 0 and w > 0]
    if not pairs:
        return 0.0, 0.0, 0.0
    total = sum(w for _, w in pairs)
    return min(d for d, _ in pairs), max(d for d, _ in pairs), sum(d * w for d, w in pairs) / total


def tiles_needed(uv_areas):
    """Tiles a set of islands needs at its current scale, planned at ``FILL`` of each tile."""
    return max(1, math.ceil(sum(uv_areas) / FILL))


def assign_tiles(uv_areas, tiles):
    """Islands to ``tiles`` tiles, largest first, each to the tile with the most room (balanced first fit).
    Returns a tile index per island."""
    if not 1 <= tiles <= MAX_TILES:
        raise ValueError("tiles must be 1 to %d" % MAX_TILES)
    used = [0.0] * tiles
    out = [0] * len(uv_areas)
    for index in sorted(range(len(uv_areas)), key=lambda i: (-uv_areas[i], i)):
        tile = min(range(tiles), key=lambda t: (used[t], t))
        out[index] = tile
        used[tile] += uv_areas[index]
    return out


def spill_tile(used, index, groups):
    """Where an island leaving tile ``used[index]`` goes: the next tile in ``used`` (planned, not yet packed) of the
    same group (``groups``: tile -> group), else a new tile after every tile so far. Spilling forward keeps the tile
    count near what the area needs; a new tile per spilled island multiplied it."""
    group = groups[used[index]]
    for tile in used[index + 1:]:
        if groups.get(tile) == group:
            return tile
    return max(max(used), max(groups)) + 1


def over_fill(areas, movable):
    """Which islands of a tile move on before it is packed (indices): largest first, an island stays while the tile's
    area is within ``FILL``, counting the immovable ones (others are stacked on them) first; the rest move on. The
    tile always keeps at least one island. Packing everything spilled onto a tile spread the layout far past it."""
    used = sum(a for a, m in zip(areas, movable) if not m)
    kept_any = any(not m for m in movable)
    leaving = []
    for k in sorted((k for k in range(len(areas)) if movable[k]), key=lambda k: (-areas[k], k)):
        if not kept_any or used + areas[k] <= FILL:
            used += areas[k]
            kept_any = True
        else:
            leaving.append(k)
    return sorted(leaving)


def overflowing(inside, movable, areas):
    """Which islands of a packed tile move on (indices): every movable one the pack left outside the tile, all at
    once, so a tile is packed once rather than once per spilled island. When every island is outside, the largest
    stays, so the tile keeps one and the loop moves forward. A tile of one island keeps it."""
    if len(inside) <= 1:
        return []
    out = [k for k in range(len(inside)) if movable[k] and not inside[k]]
    if len(out) == len(inside):
        out.remove(max(out, key=lambda k: (areas[k], -k)))
    return out


def _hull(points):
    """Convex hull (counter-clockwise) of 2D points, by the monotone chain."""
    pts = sorted(set(map(tuple, points)))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def tile_span(points):
    """The long side of the smallest-area rectangle around UV ``points`` at any turn (rotating calipers): the side
    a tile must hold, since Pack Islands turns each island to its smallest rectangle."""
    hull = _hull(points)
    if len(hull) < 2:
        return 0.0
    if len(hull) == 2:
        return math.dist(*hull)
    best = None
    for i, a in enumerate(hull):
        b = hull[(i + 1) % len(hull)]
        length = math.dist(a, b)
        if length == 0:
            continue
        ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
        along = [(p[0] - a[0]) * ux + (p[1] - a[1]) * uy for p in hull]
        across = [(p[1] - a[1]) * ux - (p[0] - a[0]) * uy for p in hull]
        w, h = max(along) - min(along), max(across) - min(across)
        if best is None or w * h < best[0]:
            best = (w * h, max(w, h))
    return best[1]


def fits_tile(points, margin):
    """Whether an island with these UV points fits one tile at its scale, keeping ``margin`` on both sides."""
    return tile_span(points) <= 1.0 - 2.0 * margin + 1e-9


def udim_offset(tile):
    """The (u, v) offset of tile ``tile`` (0 = UDIM 1001), ten tiles to a row."""
    return (tile % 10, tile // 10)


def efficiency(uv_areas, tiles):
    """Share of the tiles' area the islands cover (1.0 = no gaps)."""
    return sum(uv_areas) / max(tiles, 1)


def _points(values, name="points"):
    """An (N, 3) float array of finite points from any sequence of (x, y, z): the caller's tuples or an array."""
    try:
        array = np.asarray(values, dtype=float)
    except (TypeError, ValueError):
        array = None
    if array is None or array.ndim != 2 or array.shape[1] != 3 or not np.all(np.isfinite(array)):
        raise ValueError("%s must be a sequence of finite (x, y, z) points" % name)
    return array


def _frame(points):
    """Centroid and principal axes (rows, right-handed) of an (N, 3) array."""
    centre = points.mean(axis=0)
    values, vectors = np.linalg.eigh(np.cov((points - centre).T) + np.eye(3) * 1e-18)
    axes = vectors[:, ::-1].T
    if np.linalg.det(axes) < 0:
        axes[2] = -axes[2]
    return centre, axes, values[::-1]


def signature(points, faces, tolerance):
    """A cheap key that congruent islands share: vertex and face counts and the rounded principal spreads."""
    points = _points(points)
    if len(points) < 3:
        return (len(points), faces, ())
    _centre, _axes, values = _frame(points)
    step = max(tolerance, 1e-9)
    return (len(points), faces, tuple(int(round(math.sqrt(max(v, 0.0)) / step)) for v in values))


def _nearest(points, others, tolerance):
    """Each point's nearest other (one block of rows at a time, so memory stays bounded however many points), or
    None as soon as some point has no other within ``tolerance``."""
    rows = max(1, MATCH_BLOCK // (3 * len(others)))
    nearest = np.empty(len(points), dtype=np.intp)
    for start in range(0, len(points), rows):
        block = points[start:start + rows]
        difference = block[:, None, :] - others[None, :, :]
        squared = np.einsum("ijk,ijk->ij", difference, difference)
        best = squared.argmin(axis=1)
        if squared[np.arange(len(block)), best].max() > tolerance * tolerance:
            return None
        nearest[start:start + rows] = best
    return nearest


def _turns(local_a, local_b, plane, tolerance):
    """Turns about the remaining axis (row-vector matrices) that carry some point of B onto A's point farthest from
    that axis: the candidates for a part whose two spreads across the axis are equal (a round part)."""
    p, q = plane
    radius_a = np.hypot(local_a[:, p], local_a[:, q])
    reference = int(radius_a.argmax())
    radius_b = np.hypot(local_b[:, p], local_b[:, q])
    along = 3 - p - q
    same = np.flatnonzero((np.abs(radius_b - radius_a[reference]) <= tolerance)
                          & (np.abs(local_b[:, along] - local_a[reference, along]) <= tolerance))
    if len(same) > MAX_TURNS:
        return   # Too many ways to try within a turn's time: not stacked rather than slow.
    target = math.atan2(local_a[reference, q], local_a[reference, p])
    for j in same:
        angle = target - math.atan2(local_b[j, q], local_b[j, p])
        turn = np.eye(3)
        turn[p, p] = turn[q, q] = math.cos(angle)
        turn[p, q], turn[q, p] = math.sin(angle), -math.sin(angle)
        yield turn


def match(points_a, normals_a, points_b, normals_b, tolerance, mirrored=False):
    """A vertex map making part B congruent to part A (rotation and translation, optionally a mirror), every vertex
    within ``tolerance``; None when there is none. Points and normals are sequences of (x, y, z).

    Congruent points are not enough: a mirror image of a flat or mirror-symmetric part also matches the part turned
    over, with its surface facing the other way, and its shared UVs would show the texture mirrored. So the matched
    normals must face the same way, and a mirror counts only with ``mirrored``. A round part (two equal spreads
    across its axis) is turned about that axis until a point lands; a shape with no distinct axis at all (a cube's
    corners, a sphere) is never matched, so a stack is never placed wrong."""
    a, b = _points(points_a, "points_a"), _points(points_b, "points_b")
    na, nb = _points(normals_a, "normals_a"), _points(normals_b, "normals_b")
    if a.shape != b.shape or na.shape != a.shape or nb.shape != b.shape or not 3 <= len(a) <= MAX_MATCH:
        return None
    ca, axes_a, values_a = _frame(a)
    cb, axes_b, values_b = _frame(b)
    if np.max(np.abs(np.sqrt(np.maximum(values_a, 0)) - np.sqrt(np.maximum(values_b, 0)))) > tolerance:
        return None
    root = np.sqrt(np.maximum(values_a, 0))
    equal = (abs(root[0] - root[1]) < 10 * tolerance, abs(root[1] - root[2]) < 10 * tolerance)
    if all(equal):
        return None
    local_a = (a - ca) @ axes_a.T
    local_b = (b - cb) @ axes_b.T
    probe = np.unique(np.linspace(0, len(a) - 1, min(len(a), PROBE)).astype(int))
    signs = [(1, 1, 1), (1, -1, -1), (-1, 1, -1), (-1, -1, 1)]
    if mirrored:
        signs += [(-x, y, z) for x, y, z in signs]
    for sign in signs:
        flip = np.diag(np.asarray(sign, float))
        flipped = local_b @ flip
        turns = _turns(local_a, flipped, (0, 1) if equal[0] else (1, 2), tolerance) if any(equal) else [np.eye(3)]
        for turn in turns:
            moved = flipped @ turn
            if _nearest(local_a[probe], moved, tolerance) is None:
                continue
            mapping = _nearest(local_a, moved, tolerance)
            if mapping is None or len(np.unique(mapping)) != len(a):
                continue
            carry = axes_b.T @ flip @ turn @ axes_a   # Row vectors: (b - cb) @ carry lies on a - ca.
            if np.mean(np.sum((nb[mapping] @ carry) * na, axis=1)) < SAME_SIDE:
                continue   # The points match, but the surface faces the other way: a mirror image, turned over.
            return mapping.tolist()   # mapping[i]: B's vertex that lies on A's vertex i.
    return None


def corner_sources(faces_a, faces_b, a_of_b):
    """For each face of part B (a tuple of vertex ids in corner order), A's face on the vertices B's face maps to
    (``a_of_b``) and, per corner, A's corner on that vertex: ``[(face_a, [corner_a, ...]), ...]``. UVs then copy
    corner to corner, so a vertex on a seam keeps a different UV in each face. None when some face of B has no such
    face in A (the parts' topology differs)."""
    by_vertices = {frozenset(face): (index, {v: k for k, v in enumerate(face)}) for index, face in enumerate(faces_a)}
    sources = []
    for face in faces_b:
        if any(v not in a_of_b for v in face):
            return None
        mapped = [a_of_b[v] for v in face]
        found = by_vertices.get(frozenset(mapped))
        if found is None or len(found[1]) != len(mapped):
            return None
        sources.append((found[0], [found[1][v] for v in mapped]))
    return sources
