# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shape Cut's geometry: a surface frame, grid snapping, 2D outlines and the closed solids that cut. No Blender here.

A cut is drawn in a frame on the surface: ``u`` and ``v`` span the face's plane, ``n`` is its outward normal and the
origin sits on a corner of the face (or under the world origin), so a grid in the frame lines up with the panel's own
edges. Outlines are counter-clockwise seen from ``+n``. Solids are built closed and consistently oriented by
construction (every edge used by exactly two faces in opposite directions), which is what the Manifold solver needs.
doc/angelo/shape_cut.rst is the contract.
"""

import math

EPS = 1e-9
MAX_POINTS = 512   # An outline's corners at most (a freehand stroke is simplified first).
MIN_SEGMENTS, MAX_SEGMENTS = 3, 256


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(a, what="vector"):
    length = math.sqrt(_dot(a, a))
    if length < EPS:
        raise ValueError("%s has no length" % what)
    return (a[0] / length, a[1] / length, a[2] / length)


class Frame:
    """An orthonormal frame on a surface: world = origin + x u + y v + z n."""

    __slots__ = ("origin", "u", "v", "n")

    def __init__(self, origin, u, n):
        self.n = _unit(n, "the normal")
        u = _sub(u, tuple(c * _dot(u, self.n) for c in self.n))   # Into the plane.
        self.u = _unit(u, "the frame's first axis")
        self.v = _cross(self.n, self.u)
        self.origin = tuple(float(c) for c in origin)

    def to_world(self, x, y, z=0.0):
        o, u, v, n = self.origin, self.u, self.v, self.n
        return tuple(o[i] + x * u[i] + y * v[i] + z * n[i] for i in range(3))

    def to_local(self, point):
        d = _sub(point, self.origin)
        return (_dot(d, self.u), _dot(d, self.v), _dot(d, self.n))

    def matrix(self):
        """The frame as a 4x4 row-major matrix (columns u, v, n, origin), for an object's matrix_world."""
        o, u, v, n = self.origin, self.u, self.v, self.n
        return [[u[i], v[i], n[i], o[i]] for i in range(3)] + [[0.0, 0.0, 0.0, 1.0]]


def _project(point, origin, normal):
    return _sub(point, tuple(c * _dot(_sub(point, origin), normal) for c in normal))


def _segment_distance_sq(p, a, b):
    ab, ap = _sub(b, a), _sub(p, a)
    t = max(0.0, min(1.0, _dot(ap, ab) / max(_dot(ab, ab), EPS)))
    d = _sub(ap, tuple(c * t for c in ab))
    return _dot(d, d)


def frame_from_face(normal, corners, hit, align="EDGE"):
    """The frame a cut is drawn in on the face with these world ``corners`` (in order) hit at ``hit``.

    EDGE: ``u`` runs along the face's edge nearest the hit (so a box follows the panel) and the origin is that edge's
    first corner. WORLD: ``u`` is the world axis least aligned with the normal (X before Y before Z on a tie) and the
    origin is the world origin projected onto the face's plane, so the grid is the scene's grid.
    """
    n = _unit(normal, "the normal")
    if align == "WORLD":
        axes = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
        axis = min(axes, key=lambda a: abs(_dot(a, n)) + 1e-6 * axes.index(a))
        plane_point = corners[0] if corners else hit
        return Frame(_project((0.0, 0.0, 0.0), plane_point, n), axis, n)
    if align != "EDGE":
        raise ValueError("align must be EDGE or WORLD")
    if len(corners) < 3:
        raise ValueError("a face needs at least 3 corners")
    edges = [(corners[i], corners[(i + 1) % len(corners)]) for i in range(len(corners))]
    usable = [e for e in edges if _dot(_sub(e[1], e[0]), _sub(e[1], e[0])) > EPS]
    if not usable:
        raise ValueError("the face has no edge with length")
    a, b = min(usable, key=lambda e: _segment_distance_sq(hit, e[0], e[1]))
    plane_a = _project(a, hit, n)
    return Frame(plane_a, _sub(_project(b, hit, n), plane_a), n)


def snap(point, grid):
    """``point`` (frame 2D) on the nearest grid crossing; a grid of 0 (or less) leaves it as it is."""
    if grid <= 0:
        return (float(point[0]), float(point[1]))
    return (round(point[0] / grid) * grid, round(point[1] / grid) * grid)


def signed_area(poly):
    return 0.5 * sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
                     for i in range(len(poly)))


def _orient(p, q, r):
    return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])


def _segments_cross(a, b, c, d):
    """True when segments ab and cd share a point (touching counts)."""
    d1, d2, d3, d4 = _orient(c, d, a), _orient(c, d, b), _orient(a, b, c), _orient(a, b, d)
    if ((d1 > EPS and d2 < -EPS) or (d1 < -EPS and d2 > EPS)) and ((d3 > EPS and d4 < -EPS) or
                                                                  (d3 < -EPS and d4 > EPS)):
        return True

    def on(p, q, r):   # r on segment pq, given collinear.
        return min(p[0], q[0]) - EPS <= r[0] <= max(p[0], q[0]) + EPS and \
            min(p[1], q[1]) - EPS <= r[1] <= max(p[1], q[1]) + EPS
    return ((abs(d1) <= EPS and on(c, d, a)) or (abs(d2) <= EPS and on(c, d, b)) or
            (abs(d3) <= EPS and on(a, b, c)) or (abs(d4) <= EPS and on(a, b, d)))


def is_simple(poly):
    """No two non-adjacent edges of the closed outline meet."""
    count = len(poly)
    for i in range(count):
        a, b = poly[i], poly[(i + 1) % count]
        for j in range(i + 2, count):
            if i == 0 and j == count - 1:
                continue   # Adjacent through the closing edge.
            if _segments_cross(a, b, poly[j], poly[(j + 1) % count]):
                return False
    return True


def polygon(points, min_edge=1e-6):
    """A clean outline from drawn points: consecutive duplicates and a closing repeat dropped, collinear corners
    removed, counter-clockwise. ValueError for fewer than 3 corners, a self-crossing outline or one with no area."""
    pts = [(float(p[0]), float(p[1])) for p in points]
    if any(not (math.isfinite(x) and math.isfinite(y)) for x, y in pts):
        raise ValueError("outline points must be finite numbers")
    clean = []
    for p in pts:
        if not clean or math.dist(p, clean[-1]) > min_edge:
            clean.append(p)
    while len(clean) > 1 and math.dist(clean[0], clean[-1]) <= min_edge:
        clean.pop()
    changed = True
    while changed and len(clean) >= 3:   # Drop corners that lie on the line between their neighbours.
        changed = False
        for i in range(len(clean)):
            a, b, c = clean[i - 1], clean[i], clean[(i + 1) % len(clean)]
            if abs(_orient(a, b, c)) <= min_edge * max(math.dist(a, c), min_edge):
                del clean[i]
                changed = True
                break
    if len(clean) < 3:
        raise ValueError("an outline needs at least 3 corners")
    if len(clean) > MAX_POINTS:
        raise ValueError("an outline takes at most %d corners (simplify the stroke)" % MAX_POINTS)
    if not is_simple(clean):
        raise ValueError("the outline crosses itself")
    if signed_area(clean) < 0:
        clean.reverse()
    return clean


def box(a, b, square=False, centered=False):
    """A rectangle from a drag ``a`` -> ``b``; ``square`` keeps the larger side, ``centered`` grows it from ``a``."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    if square:
        side = max(abs(dx), abs(dy))
        dx, dy = math.copysign(side, dx or 1.0), math.copysign(side, dy or 1.0)
    x0, y0 = (a[0] - dx, a[1] - dy) if centered else a
    x1, y1 = a[0] + dx, a[1] + dy
    return polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])


def circle(center, radius, segments=32):
    if not MIN_SEGMENTS <= int(segments) <= MAX_SEGMENTS:
        raise ValueError("segments must be %d to %d" % (MIN_SEGMENTS, MAX_SEGMENTS))
    if not radius > 0:
        raise ValueError("radius must be above 0")
    step = 2.0 * math.pi / int(segments)
    return [(center[0] + radius * math.cos(i * step), center[1] + radius * math.sin(i * step))
            for i in range(int(segments))]


def simplify(stroke, tolerance):
    """A freehand stroke as an outline: Ramer-Douglas-Peucker at ``tolerance``, then ``polygon``."""
    pts = [(float(p[0]), float(p[1])) for p in stroke]
    if len(pts) < 3:
        raise ValueError("a stroke needs at least 3 points")
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        first, last = stack.pop()
        a, b = pts[first], pts[last]
        best, index = -1.0, -1
        for i in range(first + 1, last):
            d = _segment_distance_sq((pts[i][0], pts[i][1], 0.0), (a[0], a[1], 0.0), (b[0], b[1], 0.0))
            if d > best:
                best, index = d, i
        if index > 0 and best > tolerance * tolerance:
            keep[index] = True
            stack.extend(((first, index), (index, last)))
    return polygon([p for p, k in zip(pts, keep) if k], min_edge=max(tolerance * 1e-3, 1e-6))


def inset(poly, distance):
    """The outline moved inward by ``distance`` (mitred corners, one corner per corner, so corner i matches corner i).
    ValueError when it would collapse, flip or cross itself: the shape is too small or too thin for that width."""
    if not distance > 0:
        raise ValueError("an inset width must be above 0")
    count, out = len(poly), []
    for i in range(count):
        p, a, b = poly[i], poly[i - 1], poly[(i + 1) % count]
        e1 = _unit2((p[0] - a[0], p[1] - a[1]))
        e2 = _unit2((b[0] - p[0], b[1] - p[1]))
        n1, n2 = (-e1[1], e1[0]), (-e2[1], e2[0])   # Inward normals of a counter-clockwise outline.
        bis = (n1[0] + n2[0], n1[1] + n2[1])
        cos_half = (bis[0] * n1[0] + bis[1] * n1[1]) / max(math.hypot(*bis), EPS)
        if math.hypot(*bis) < EPS or cos_half < 0.1:
            raise ValueError("a corner is too sharp to inset by that width")
        scale = distance / cos_half / math.hypot(*bis)
        out.append((p[0] + bis[0] * scale, p[1] + bis[1] * scale))
    for i in range(count):   # Every edge keeps its direction; a reversed edge means the offset overran it.
        a, b, c, d = poly[i], poly[(i + 1) % count], out[i], out[(i + 1) % count]
        if (b[0] - a[0]) * (d[0] - c[0]) + (b[1] - a[1]) * (d[1] - c[1]) <= EPS:
            raise ValueError("the shape is too small to inset by that width")
    if signed_area(out) <= EPS or not is_simple(out):
        raise ValueError("the shape is too small to inset by that width")
    return out


def outset(poly, distance):
    """The outline moved outward by ``distance`` (mitred, corner for corner; a mitre is capped at 10x the width so a
    sharp corner stays near its corner)."""
    if not distance > 0:
        raise ValueError("an outset width must be above 0")
    count, out = len(poly), []
    for i in range(count):
        p, a, b = poly[i], poly[i - 1], poly[(i + 1) % count]
        e1, e2 = _unit2((p[0] - a[0], p[1] - a[1])), _unit2((b[0] - p[0], b[1] - p[1]))
        n1, n2 = (e1[1], -e1[0]), (e2[1], -e2[0])   # Outward normals.
        bis = (n1[0] + n2[0], n1[1] + n2[1])
        cos_half = (bis[0] * n1[0] + bis[1] * n1[1]) / max(math.hypot(*bis), EPS)
        scale = distance / max(cos_half, 0.1) / max(math.hypot(*bis), EPS)
        out.append((p[0] + bis[0] * scale, p[1] + bis[1] * scale))
    if not is_simple(out):
        raise ValueError("the outline crosses itself when grown by that width")
    return out


def _unit2(v):
    length = math.hypot(v[0], v[1])
    if length < EPS:
        raise ValueError("an outline edge has no length")
    return (v[0] / length, v[1] / length)


def grid_offsets(columns, rows, spacing_u, spacing_v):
    """Offsets of an array's copies, centred on (0, 0): columns along u, rows along v."""
    return [((c - (columns - 1) / 2.0) * spacing_u, (r - (rows - 1) / 2.0) * spacing_v)
            for r in range(rows) for c in range(columns)]


def prism(poly, top, bottom):
    """A closed solid: the outline from ``bottom`` up to ``top`` along the frame's normal. (verts, faces)."""
    if not top > bottom:
        raise ValueError("a solid's top must be above its bottom")
    count = len(poly)
    verts = [(x, y, top) for x, y in poly] + [(x, y, bottom) for x, y in poly]
    faces = [tuple(range(count)), tuple(range(2 * count - 1, count - 1, -1))]
    faces += [(count + i, count + (i + 1) % count, (i + 1) % count, i) for i in range(count)]
    return verts, faces


def ring(outer, inner, top, bottom, floor=None):
    """The closed solid between ``outer`` and ``inner`` (``inner`` = ``inset(outer, w)``, corner for corner).

    Without ``floor``: a ring (a groove along an outline). With ``floor`` (between bottom and top): a cup, hollow from
    the top down to ``floor`` and closed below it.
    """
    if len(outer) != len(inner):
        raise ValueError("a ring's outlines need the same corners")
    if not top > bottom or (floor is not None and not bottom < floor < top):
        raise ValueError("a ring's levels must run bottom < floor < top")
    count = len(outer)
    level = floor if floor is not None else bottom
    verts = ([(x, y, top) for x, y in outer] + [(x, y, bottom) for x, y in outer] +
             [(x, y, top) for x, y in inner] + [(x, y, level) for x, y in inner])
    ot, ob, it, ib = 0, count, 2 * count, 3 * count
    faces = []
    for i in range(count):
        j = (i + 1) % count
        faces.append((ot + i, ot + j, it + j, it + i))       # Top annulus, facing up.
        faces.append((ob + i, ob + j, ot + j, ot + i))       # Outer wall, facing out.
        faces.append((it + i, it + j, ib + j, ib + i))       # Inner wall, facing into the hollow.
    if floor is None:
        faces += [(ob + j, ob + i, ib + i, ib + j) for i, j in ((i, (i + 1) % count) for i in range(count))]
    else:
        faces.append(tuple(ob + i for i in range(count - 1, -1, -1)))   # Outer bottom, facing down.
        faces.append(tuple(ib + i for i in range(count)))               # Hollow's floor, facing up.
    return verts, faces


def is_closed(faces):
    """Every edge used by exactly two faces, once in each direction: closed and consistently oriented."""
    seen = {}
    for face in faces:
        for i in range(len(face)):
            edge = (face[i], face[(i + 1) % len(face)])
            if edge[0] == edge[1] or edge in seen:
                return False
            seen[edge] = True
    return all((b, a) in seen for a, b in seen)


def volume(verts, faces):
    """Signed volume (positive when faces point outward), by fan triangles."""
    total = 0.0
    for face in faces:
        a = verts[face[0]]
        for k in range(1, len(face) - 1):
            total += _dot(a, _cross(verts[face[k]], verts[face[k + 1]])) / 6.0
    return total


def ray_plane(frame, origin, direction):
    """Where the view ray (world ``origin``, ``direction``) meets the frame's plane, in frame 2D; None when it runs
    along the plane or meets it behind the eye."""
    denom = _dot(direction, frame.n)
    if abs(denom) < 1e-9:
        return None
    t = _dot(_sub(frame.origin, origin), frame.n) / denom
    if t <= 0:
        return None
    local = frame.to_local(tuple(origin[i] + t * direction[i] for i in range(3)))
    return (local[0], local[1])


def depth_along_normal(frame, centre, origin, direction):
    """How far below the surface (along -n from frame-2D ``centre``) the view ray passes closest: the depth a drag
    sets. Never negative."""
    point = frame.to_world(centre[0], centre[1])
    axis = tuple(-c for c in frame.n)
    w = _sub(origin, point)
    b, d, e = _dot(axis, direction), _dot(axis, w), _dot(direction, w)
    c = _dot(direction, direction)
    denom = c - b * b
    if abs(denom) < 1e-12:   # Looking straight down the normal: depth cannot be read from this view.
        return 0.0
    s = (c * d - b * e) / denom   # Parameter along the axis of the closest point (axis is unit length).
    return max(s, 0.0)


def closes(points, point, tolerance):
    """True when a polygon being clicked has 3 or more corners and ``point`` is back on its first."""
    return len(points) >= 3 and math.dist(points[0], point) <= tolerance
