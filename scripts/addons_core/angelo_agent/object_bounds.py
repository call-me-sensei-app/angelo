# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An object's world-space bounds as a result states them: ``[[min x, y, z], [max x, y, z]]`` to 1 mm (finer for
a small object).

Recorded (GPT 6.1 Sol): a script's result reported the vertex count of
the ground it added but not its size, and a 200 m ground stood under a brief of 16 m until the grader measured it.
A reviewed script's ``after`` rows (``script_after``) and a program's geometry rows (``program_model_result``)
state these bounds for every object with geometry they add or change: the axis-aligned box of the object's
evaluated bounding box in world space (``Object.bound_box`` through ``matrix_world``, as program deltas read it).
Duck-typed reads; no Blender import.
"""

import math

GEOMETRY_TYPES = frozenset({"MESH", "CURVE", "SURFACE", "FONT", "META", "CURVES", "POINTCLOUD", "VOLUME",
                            "GREASEPENCIL"})
# The base data's elements: with none, and an all-zero box, the object has no geometry (``world_bounds``).
ELEMENTS = {"MESH": "vertices", "CURVE": "splines", "SURFACE": "splines", "CURVES": "curves", "POINTCLOUD": "points",
            "VOLUME": "grids"}
DIGITS = 3


def rounded(bounds, digits=DIGITS):
    """``bounds`` (two 3-vectors) rounded, or None when any value is not a finite number.

    To ``digits`` decimals (1 mm), or more for a small object, so its largest extent keeps three significant
    digits: a 0.4 mm part of a millimetre-scale model would otherwise read as a point.
    """
    try:
        values = [[float(value) for value in corner] for corner in bounds]
    except (TypeError, ValueError):
        return None
    if len(values) != 2 or any(len(corner) != 3 for corner in values):
        return None
    if not all(math.isfinite(value) for corner in values for value in corner):
        return None
    extent = max(high - low for low, high in zip(*values))
    if extent > 0.0:
        digits = max(digits, 2 - math.floor(math.log10(extent)))
    return [[round(value, digits) + 0.0 for value in corner] for corner in values]  # -0.0 reads as 0.0.


def world_bounds(obj, digits=DIGITS, depsgraph=None):
    """The world-space bounds of ``obj``'s evaluated bounding box, rounded, or None (no geometry, unreadable).

    With ``depsgraph``, a Geometry Nodes object whose output holds only instances has no geometry of its own, while
    the original's data still holds the base mesh (otherwise a 0.1 m base cube widened a box of
    instances that never render it): its evaluated box, which leaves instances out, is Blender's all-zero fallback.
    Only such an object is asked, and never through the evaluated data, whose read evaluates a deferred mesh in
    full (a 2-level Subdivision Surface 0.26 s an object). The box itself is the
    original's, which Blender keeps as the evaluated geometry's: an evaluated curve's own box adds each point's
    radius, 1 m on every side by default."""
    if getattr(obj, "type", None) not in GEOMETRY_TYPES:
        return None
    try:
        evaluated = (obj.evaluated_get(depsgraph) if depsgraph is not None
                     and any(modifier.type == "NODES" for modifier in obj.modifiers) else None)
        if evaluated is not None:
            if not any(float(value) for point in evaluated.bound_box for value in point):
                return None
        # An empty base whose box is Blender's all-zero fallback has no geometry; a box a modifier (Geometry Nodes on
        # an empty mesh, the usual procedural pattern) evaluated is its geometry's.
        elif (obj.type in ELEMENTS and not len(getattr(obj.data, ELEMENTS[obj.type]))
                and not any(float(value) for point in obj.bound_box for value in point)):
            return None
        matrix = obj.matrix_world
        rows = [[float(matrix[row][column]) for column in range(4)] for row in range(3)]
        corners = [[sum(rows[axis][index] * corner[index] for index in range(3)) + rows[axis][3] for axis in range(3)]
                   for corner in ([float(value) for value in point] for point in obj.bound_box)]
    except (AttributeError, TypeError, ValueError, IndexError, ReferenceError):
        return None
    if len(corners) != 8:
        return None
    return rounded([[min(point[axis] for point in corners) for axis in range(3)],
                    [max(point[axis] for point in corners) for axis in range(3)]], digits)
