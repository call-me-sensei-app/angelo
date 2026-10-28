# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Small rotation and distance helpers for the hand-rig modules (``hand_rig``, ``hand_skin``, ``hand_pose``).
numpy only; 3x3 rotations act on column vectors, as Blender's matrices do."""

import math

import numpy as np


class HandError(ValueError):
    """The bones or mesh are not a hand these tools can measure; the message says why and what to give instead."""


def unit(v):
    v = np.asarray(v, dtype=np.float64)
    length = float(np.linalg.norm(v))
    if not math.isfinite(length) or length < 1e-12:
        raise HandError("A direction needed for the hand's frame has no length")
    return v / length


def perpendicular(v, axis):
    """``v`` without its component along unit ``axis`` (not normalised)."""
    return v - axis * float(v @ axis)


def axis_angle(axis, angle):
    """3x3 rotation by ``angle`` radians about unit ``axis`` (Rodrigues)."""
    x, y, z = unit(axis)
    c, s = math.cos(angle), math.sin(angle)
    t = 1.0 - c
    return np.array([[t * x * x + c, t * x * y - s * z, t * x * z + s * y],
                     [t * x * y + s * z, t * y * y + c, t * y * z - s * x],
                     [t * x * z - s * y, t * y * z + s * x, t * z * z + c]])


def rotation_between(a, b):
    """Shortest 3x3 rotation turning unit ``a`` onto unit ``b``."""
    a, b = unit(a), unit(b)
    axis = np.cross(a, b)
    s, c = float(np.linalg.norm(axis)), float(a @ b)
    if s < 1e-12:
        if c > 0:
            return np.eye(3)
        other = np.array([1.0, 0.0, 0.0]) if abs(a[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
        return axis_angle(np.cross(a, other), math.pi)
    return axis_angle(axis, math.atan2(s, c))


def rotation_part(matrix):
    """The rotation of a 3x3 or 4x4 matrix with scale removed (its columns normalised, then orthogonalised)."""
    m = np.asarray(matrix, dtype=np.float64)[:3, :3]
    u, _, vt = np.linalg.svd(m)
    r = u @ vt
    if np.linalg.det(r) < 0:
        u[:, -1] = -u[:, -1]
        r = u @ vt
    return r


def quaternion(r):
    """(w, x, y, z) of a 3x3 rotation, w >= 0."""
    m = r
    trace = m[0, 0] + m[1, 1] + m[2, 2]
    if trace > 0:
        s = math.sqrt(trace + 1.0) * 2
        q = ((0.25 * s), (m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s)
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        q = ((m[2, 1] - m[1, 2]) / s, 0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s)
    elif m[1, 1] > m[2, 2]:
        s = math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        q = ((m[0, 2] - m[2, 0]) / s, (m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s)
    else:
        s = math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        q = ((m[1, 0] - m[0, 1]) / s, (m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s)
    q = np.array(q, dtype=np.float64)
    q /= np.linalg.norm(q)
    return -q if q[0] < 0 else q


def euler_xyz(r):
    """Blender's XYZ Euler angles (radians) of a 3x3 rotation, R = Rz @ Ry @ Rx (``Matrix.to_euler('XYZ')``)."""
    sy = math.hypot(r[0, 0], r[1, 0])
    if sy > 1e-9:
        return [math.atan2(r[2, 1], r[2, 2]), math.atan2(-r[2, 0], sy), math.atan2(r[1, 0], r[0, 0])]
    return [math.atan2(-r[1, 2], r[1, 1]), math.atan2(-r[2, 0], sy), 0.0]


def euler_matrix(angles):
    """3x3 rotation of Blender XYZ Euler angles (radians)."""
    x, y, z = angles
    rx = axis_angle((1.0, 0.0, 0.0), x)
    ry = axis_angle((0.0, 1.0, 0.0), y)
    rz = axis_angle((0.0, 0.0, 1.0), z)
    return rz @ ry @ rx


def segment_distance(points, a, b):
    """Distance of each point (n, 3) from segment a-b."""
    ab = b - a
    t = np.clip(((points - a) @ ab) / max(float(ab @ ab), 1e-24), 0.0, 1.0)
    return np.linalg.norm(points - (a + np.outer(t, ab)), axis=1)


class Budget:
    """One time budget for a whole tool call, its setup included: ``expired`` once ``seconds`` have passed since
    ``started`` (a ``time.perf_counter`` reading; now when None)."""

    def __init__(self, seconds, started=None):
        import time
        self.clock = time.perf_counter
        self.seconds = float(seconds)
        self.started = self.clock() if started is None else started

    def spent(self):
        return self.clock() - self.started

    def expired(self):
        return self.spent() >= self.seconds
