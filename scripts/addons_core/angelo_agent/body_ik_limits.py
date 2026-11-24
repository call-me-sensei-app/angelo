# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Joint limits and balance for the full-body pose solver (``body_ik_solve``; doc/angelo/rigging.rst, "Full-body
pose"). Plain data, no Blender dependency.

**Limits**: the constrained form of FABRIK (Aristidou and Lasenby 2011, section 4, "joint restrictions"), as
swing cones, applied as each link is placed in the forward pass (``clamp_direction``, ``clamp_turn``, ``carry``) so
the rest of the chain is placed from where its link may go, and to what each link asks of its parent in the
backward pass (in the last forward pass's frames), so a link at its limit passes the rest of the bend up the body;
then checked once more after the pass (``enforce``), since a hinge kept on its side can move a link. Each link (a joint's parent to the joint) has a rest direction; carried down the
body, a link's frame is its parent link's frame turned by the smallest arc onto where the link now points (the root's
frame is the world's). A link may point at most ``limits[joint]`` from its rest direction in its parent's frame; one
that points further is turned back onto the cone, and everything below it turns with it. A joint with several
children (the chest) turns them as one bone, so that turn, measured in the joint's own frame, is held to the tightest
of its children's limits. The root's children are free: the hips have no parent to bend from.

**Balance** (``com``, ``deficit``): the centre of mass of the given segments (mass at each segment's midpoint; a
segment from a joint to itself is a point mass), seen along ``up``, must lie over the support: the convex hull of
the planted joints, widened by ``radius`` (the feet's own size). ``deficit`` is how far, and which way, it lies
beyond. The solver shifts the hips by the deficit it has added up over its iterations, so balance holds once pins
do, before the targets.
"""

import math


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _mul(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a):
    return math.sqrt(_dot(a, a))


def _unit(a):
    n = _norm(a)
    return _mul(a, 1.0 / n) if n > 1e-12 else (0.0, 0.0, 0.0)


IDENTITY = (1.0, 0.0, 0.0, 0.0)


def qmul(a, b):
    """The quaternion product ``a`` after ``b`` ((w, x, y, z))."""
    return (a[0] * b[0] - a[1] * b[1] - a[2] * b[2] - a[3] * b[3],
            a[0] * b[1] + a[1] * b[0] + a[2] * b[3] - a[3] * b[2],
            a[0] * b[2] - a[1] * b[3] + a[2] * b[0] + a[3] * b[1],
            a[0] * b[3] + a[1] * b[2] - a[2] * b[1] + a[3] * b[0])


def conj(q):
    return (q[0], -q[1], -q[2], -q[3])


def rotate(q, v):
    w, u = q[0], (q[1], q[2], q[3])
    t = _mul(_cross(u, v), 2.0)
    return _add(_add(v, _mul(t, w)), _cross(u, t))


def _perpendicular(a):
    return _unit(_cross(a, (1.0, 0.0, 0.0)) if abs(a[0]) < 0.9 else _cross(a, (0.0, 1.0, 0.0)))


def axis_angle(axis, theta):
    s = math.sin(theta / 2.0)
    return (math.cos(theta / 2.0), axis[0] * s, axis[1] * s, axis[2] * s)


def arc(a, b):
    """The smallest rotation turning direction ``a`` onto ``b``; identity when either is degenerate."""
    a, b = _unit(a), _unit(b)
    if _norm(a) == 0.0 or _norm(b) == 0.0:
        return IDENTITY
    d = _dot(a, b)
    if d < -1.0 + 1e-12:
        return axis_angle(_perpendicular(a), math.pi)
    c = _cross(a, b)
    q = (1.0 + d, c[0], c[1], c[2])
    n = math.sqrt(sum(x * x for x in q))
    return tuple(x / n for x in q)


def _angle(q):
    w = min(1.0, abs(q[0]))
    return 2.0 * math.acos(w)


def _scaled(q, theta):
    """``q``'s rotation about the same axis, by ``theta`` instead."""
    if q[0] < 0.0:
        q = tuple(-x for x in q)
    axis = _unit((q[1], q[2], q[3]))
    return axis_angle(axis, theta) if _norm(axis) > 0.0 else IDENTITY


def _subtree(children, j):
    out, stack = [], [j]
    while stack:
        k = stack.pop()
        out.append(k)
        stack.extend(children.get(k, ()))
    return out


def _turn_subtrees(pos, children, roots, pivot, q):
    for r in roots:
        for k in _subtree(children, r):
            pos[k] = _add(pivot, rotate(q, _sub(pos[k], pivot)))


def carry(base, rest, now):
    """The frame of a link that pointed along ``rest`` in its parent's frame ``base`` and now points along ``now``."""
    return qmul(arc(rotate(base, rest), now), base)


def clamp_direction(base, rest, now, limit):
    """``now`` turned back onto the cone of half-angle ``limit`` about ``rest`` carried by ``base``; and whether it
    had to be."""
    a, b = _unit(rotate(base, rest)), _unit(now)
    theta = math.acos(max(-1.0, min(1.0, _dot(a, b))))
    if theta <= limit + 1e-12:
        return now, False
    axis = _unit(_cross(a, b))
    if _norm(axis) == 0.0:
        axis = _perpendicular(a)
    return rotate(axis_angle(axis, limit - theta), now), True


def clamp_turn(turn, base, limit):
    """A branch's rotation ``turn`` held within ``limit`` of its parent's frame ``base``; and whether it had to be."""
    rel = qmul(turn, conj(base))
    if _angle(rel) <= limit + 1e-12:
        return turn, False
    return qmul(_scaled(rel, limit), base), True


def enforce(pos, order, parents, children, offset, limits, fit, keep=(), only=None):
    """Turn every link back within its limit, in place (root outward). ``fit(pairs)`` is the solver's weighted
    rotation fit; ``keep``: joints left where they are (pinned chains: pins come first); ``only``: the joints whose
    own link may be turned (the ones the solve placed). Returns the frames {joint: quaternion} and the joints whose
    limit held them."""
    frames, held = {}, []
    for j in order:
        p = parents.get(j)
        if p is None:
            frames[j] = IDENTITY
            continue
        base = frames[p]
        kids = children[p]
        if len(kids) > 1 and parents.get(p) is not None and j == kids[0]:
            limit = min((limits[k] for k in kids if k in limits), default=None)
            if limit is not None:
                turn = fit([(offset[k], _sub(pos[k], pos[p]), 1.0) for k in kids])
                rel = qmul(turn, conj(base))
                if _angle(rel) > limit + 1e-12 and not any(k in keep for k in kids):
                    _turn_subtrees(pos, children, kids, pos[p], qmul(_scaled(rel, limit), conj(rel)))
                    held.append(p)
        if j in limits and j not in keep and len(kids) == 1 and (only is None or j in only):   # Branches: above.
            now, rest = _sub(pos[j], pos[p]), rotate(base, offset[j])
            a, b = _unit(rest), _unit(now)
            theta = math.acos(max(-1.0, min(1.0, _dot(a, b))))
            if theta > limits[j] + 1e-12:
                axis = _unit(_cross(a, b))
                if _norm(axis) == 0.0:
                    axis = _perpendicular(a)
                _turn_subtrees(pos, children, [j], pos[p], axis_angle(axis, limits[j] - theta))
                held.append(j)
        frames[j] = qmul(arc(rotate(base, offset[j]), _sub(pos[j], pos[p])), base)
    return frames, held


def com(pos, segments):
    """The centre of mass of ``segments`` ((a, b, mass), mass at the midpoint of a and b)."""
    total = sum(m for _, _, m in segments)
    acc = (0.0, 0.0, 0.0)
    for a, b, m in segments:
        acc = _add(acc, _mul(_add(pos[a], pos[b]), m / 2.0))
    return _mul(acc, 1.0 / total) if total > 0.0 else acc


def _plane(up):
    up = _unit(up)
    e1 = _perpendicular(up)
    return up, e1, _cross(up, e1)


def _hull(points):
    """Andrew's monotone chain; counter-clockwise, collinear points dropped."""
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0.0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0.0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _closest_on_segment(q, a, b):
    ab = (b[0] - a[0], b[1] - a[1])
    t = ((q[0] - a[0]) * ab[0] + (q[1] - a[1]) * ab[1]) / max(ab[0] ** 2 + ab[1] ** 2, 1e-24)
    t = max(0.0, min(1.0, t))
    return (a[0] + t * ab[0], a[1] + t * ab[1])


def _inside(q, hull):
    return len(hull) >= 3 and all((b[0] - a[0]) * (q[1] - a[1]) - (b[1] - a[1]) * (q[0] - a[0]) >= 0.0
                                  for a, b in zip(hull, hull[1:] + hull[:1]))


def deficit(point, support, radius, up):
    """The vector, square to ``up``, from the widened support to ``point`` seen along ``up``; zero inside."""
    up, e1, e2 = _plane(up)
    flat = [(_dot(s, e1), _dot(s, e2)) for s in support]
    if not flat:
        return (0.0, 0.0, 0.0)
    q = (_dot(point, e1), _dot(point, e2))
    hull = _hull(flat)
    if _inside(q, hull):
        return (0.0, 0.0, 0.0)
    edges = list(zip(hull, hull[1:] + hull[:1])) if len(hull) > 1 else [(hull[0], hull[0])]
    near = min((_closest_on_segment(q, a, b) for a, b in edges), key=lambda c: math.hypot(q[0] - c[0], q[1] - c[1]))
    gap = (q[0] - near[0], q[1] - near[1])
    d = math.hypot(*gap)
    if d <= radius:
        return (0.0, 0.0, 0.0)
    s = (d - radius) / d
    return _add(_mul(e1, gap[0] * s), _mul(e2, gap[1] * s))
