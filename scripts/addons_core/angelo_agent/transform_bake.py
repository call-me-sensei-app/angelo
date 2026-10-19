# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The matrix ``transform_apply`` bakes into a mesh, as Blender's Apply builds it (pure Python, no bpy).

Blender's Object > Apply (``apply_objects_internal`` in editors/object/object_transform.cc) builds the matrix
it bakes from the object's channels, never from a decomposition: the scale matrix for scale, rotation times
scale for both, the rotation conjugated by the scale (S^-1 R S) for rotation alone, and the location carried
through what stays on the object. It resets only the applied channels and flips the faces when that matrix
mirrors (Corrective Flip Normals, on by default). Rebuilding the basis from ``Matrix.decompose`` instead, as
the kernel once did, turns a mirrored scale (-2, 1, 1) into a 180-degree rotation about X and the scale
(2, -1, -1): the world shape held, but the rotation channels changed, and a keyed rotation flipped the object
on the next frame.

Matrices are lists of rows. Delta transforms are refused before this runs, so the basis's linear part is the
rotation times ``scale`` exactly.
"""

SINGULAR = 1e-12
from .refusal_codes import RefusalError


def multiply(a, b):
    return [[sum(a[row][k] * b[k][column] for k in range(3)) for column in range(3)] for row in range(3)]


def _apply(matrix, vector):
    return [sum(matrix[row][k] * vector[k] for k in range(3)) for row in range(3)]


def is_rotation(m, tolerance=0.00001):
    """Whether the 3x3 ``m`` neither scales nor shears: its columns are unit length and at right angles."""
    columns = [[m[row][column] for row in range(3)] for column in range(3)]
    return all(abs(sum(columns[a][k] * columns[b][k] for k in range(3)) - (1.0 if a == b else 0.0)) <= tolerance
               for a in range(3) for b in range(a, 3))


def determinant(m):
    return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
            + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))


def inverse(m):
    det = determinant(m)
    if abs(det) < SINGULAR:
        raise RefusalError("cannot apply this with a singular basis", code="VALIDATION")
    cofactor = [[(m[(r + 1) % 3][(c + 1) % 3] * m[(r + 2) % 3][(c + 2) % 3]
                  - m[(r + 1) % 3][(c + 2) % 3] * m[(r + 2) % 3][(c + 1) % 3]) for c in range(3)] for r in range(3)]
    return [[cofactor[c][r] / det for c in range(3)] for r in range(3)]


def bake(linear, scale, location, *, apply_location, apply_rotation, apply_scale):
    """``(applied, translation, mirrors)``: the 3x3 linear part and translation to bake, and whether it mirrors.

    ``linear`` is the basis's 3x3 (rows), ``scale`` the object's scale channel and ``location`` its location.
    The object then keeps its channels except the applied ones, which become identity. Raises ValueError when a
    needed inverse does not exist (rotation alone with a zero scale axis, location without both rotation and
    scale over a singular basis), as Blender declines a non-invertible transform.
    """
    scale_matrix = [[float(scale[row]) if row == column else 0.0 for column in range(3)] for row in range(3)]
    if apply_rotation and apply_scale:
        applied = [list(map(float, row)) for row in linear]
    elif apply_scale:
        applied = scale_matrix
    elif apply_rotation:  # S^-1 R S, with linear = R S: the scale that stays then maps it back.
        applied = multiply(inverse(scale_matrix), [list(map(float, row)) for row in linear])
    else:
        applied = [[1.0 if row == column else 0.0 for column in range(3)] for row in range(3)]
    translation = [0.0, 0.0, 0.0]
    if apply_location:
        translation = [float(value) for value in location]
        if not (apply_rotation and apply_scale):
            translation = _apply(multiply(applied, inverse(linear)), translation)
    return applied, translation, determinant(applied) < 0
