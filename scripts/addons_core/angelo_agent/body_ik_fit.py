# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The rotation fits of the full-body pose solver (``body_ik_solve``; doc/angelo/rigging.rst, "Full-body pose"): a
branching joint's children are placed by one rotation of their offsets. Plain data, no Blender dependency.

* ``_fit_rotation``: the unit quaternion that best turns each offset toward where its child asks to be, weighted
  (Horn's closed-form quaternion fit, 1987; the top eigenvector by cyclic Jacobi, deterministic).
* ``_fit_branch``: fitted to the children leading to pins first; when their offsets lie on one line (two planted
  legs), the turn about that line is then chosen in closed form for the other children (the spine decides the
  pelvis's pitch).
"""

import math

from .body_ik_limits import _cross, _dot, _mul, _norm, _sub, rotate as _rotate


def _unit(a, fallback=(0.0, 0.0, 1.0)):   # The solver's: a degenerate vector points up.
    n = _norm(a)
    return _mul(a, 1.0 / n) if n > 1e-12 else fallback


def _jacobi_top(n):
    """The eigenvector of the symmetric 4x4 ``n`` with the largest eigenvalue (cyclic Jacobi; deterministic)."""
    a = [row[:] for row in n]
    v = [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]
    for _ in range(50):
        off = sum(a[i][j] ** 2 for i in range(4) for j in range(4) if i != j)
        if off < 1e-30:
            break
        for p in range(3):
            for q in range(p + 1, 4):
                if abs(a[p][q]) < 1e-300:
                    continue
                theta = (a[q][q] - a[p][p]) / (2.0 * a[p][q])
                t = (1.0 if theta >= 0 else -1.0) / (abs(theta) + math.sqrt(theta * theta + 1.0))
                c = 1.0 / math.sqrt(t * t + 1.0)
                s = t * c
                for k in range(4):
                    akp, akq = a[k][p], a[k][q]
                    a[k][p], a[k][q] = c * akp - s * akq, s * akp + c * akq
                for k in range(4):
                    apk, aqk = a[p][k], a[q][k]
                    a[p][k], a[q][k] = c * apk - s * aqk, s * apk + c * aqk
                for k in range(4):
                    vkp, vkq = v[k][p], v[k][q]
                    v[k][p], v[k][q] = c * vkp - s * vkq, s * vkp + c * vkq
    top = max(range(4), key=lambda i: a[i][i])
    q = tuple(v[k][top] for k in range(4))
    return q if q[0] >= 0.0 else tuple(-x for x in q)


def _fit_rotation(pairs):
    """The unit quaternion that best turns each ``a`` toward its ``b`` (directions only; Horn 1987), each pair
    weighted by its ``w``. One pair: the smallest such turn; none: no turn."""
    pairs = [(a, b, w) for a, b, w in pairs if _norm(a) > 1e-12 and _norm(b) > 1e-12]
    if not pairs:
        return (1.0, 0.0, 0.0, 0.0)
    if len(pairs) == 1:
        a, b = _unit(pairs[0][0]), _unit(pairs[0][1])
        d = _dot(a, b)
        if d < -1.0 + 1e-12:   # Opposite: half a turn about any axis square to ``a``.
            axis = _unit(_cross(a, (1.0, 0.0, 0.0)) if abs(a[0]) < 0.9 else _cross(a, (0.0, 1.0, 0.0)))
            return (0.0, axis[0], axis[1], axis[2])
        c = _cross(a, b)
        q = (1.0 + d, c[0], c[1], c[2])
        n = math.sqrt(sum(x * x for x in q))
        return tuple(x / n for x in q)
    m = [[0.0] * 3 for _ in range(3)]
    for a, b, w in pairs:
        b = _mul(_unit(b), _norm(a))   # Directions matter, not how far a child's wish lies.
        for i in range(3):
            for j in range(3):
                m[i][j] += w * a[i] * b[j]
    (sxx, sxy, sxz), (syx, syy, syz), (szx, szy, szz) = m
    n = [[sxx + syy + szz, syz - szy, szx - sxz, sxy - syx],
         [syz - szy, sxx - syy - szz, sxy + syx, szx + sxz],
         [szx - sxz, sxy + syx, -sxx + syy - szz, syz + szy],
         [sxy - syx, szx + sxz, syz + szy, -sxx - syy + szz]]
    q = _jacobi_top(n)
    norm = math.sqrt(sum(x * x for x in q))
    return tuple(x / norm for x in q)


def _fit_branch(pinned_pairs, free_pairs):
    """The turn of a branching joint: fitted to the children leading to pins first; when their offsets lie on one
    line (the hips' two legs), the turn about that line is then chosen, in closed form, to point the other children
    where they ask (the spine decides the pelvis's pitch); with none pinned, one fit over all."""
    if not pinned_pairs:
        return _fit_rotation(free_pairs)
    turn = _fit_rotation(pinned_pairs)
    if not free_pairs:
        return turn
    dirs = [_unit(_rotate(turn, a)) for a, _, _ in pinned_pairs if _norm(a) > 1e-12]
    if not dirs or any(_norm(_cross(dirs[0], d)) > 1e-6 for d in dirs[1:]):
        return turn   # Pins on more than a line fix the turn.
    axis = dirs[0]
    num = den = 0.0
    for a, b, w in free_pairs:
        v, t = _rotate(turn, a), b
        v, t = _sub(v, _mul(axis, _dot(v, axis))), _sub(t, _mul(axis, _dot(t, axis)))
        num += w * _dot(axis, _cross(v, t))
        den += w * _dot(v, t)
    phi = math.atan2(num, den)
    spin = (math.cos(phi / 2.0), axis[0] * math.sin(phi / 2.0), axis[1] * math.sin(phi / 2.0),
            axis[2] * math.sin(phi / 2.0))
    w1, x1, y1, z1 = spin
    w2, x2, y2, z2 = turn
    return (w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2, w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
            w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2, w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2)
