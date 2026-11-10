# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Make a newly added model belong in the scene: sized like the scene's objects and standing beside them.

Fab's converted GLB files carry whatever units their publisher used (chairs measured 0.17-0.54 m tall, 2026-11-09),
so a stock import can arrive far too small or large. ``fit`` scales such a model to the typical height of the
scene's own objects when it is more than twice off (the maintainer's choice, 2026-11-09: fit to the scene; one Undo
removes the whole import), and ``beside`` moves a model added at the 3D cursor out of an object it would stand
inside (the default cube sits on the cursor). A drop point is the person's own choice and is never moved. Flat
objects (floors, planes) and the new model itself never set the scale.
"""
from statistics import median

from mathutils import Vector

FIT_RATIO = 2.0   # Within this factor of the scene's typical height a model keeps its own size.


def _span(points):
    """The axis-aligned box of ``points`` (one or more vectors)."""
    points = list(points)
    return (Vector([min(p[i] for p in points) for i in range(3)]), Vector([max(p[i] for p in points) for i in range(3)]))


def _box(obj):
    return _span(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)


def _others(scene, objects):
    mine = {o.as_pointer() for o in objects}
    return [_box(o) for o in scene.objects if o.type == 'MESH' and o.as_pointer() not in mine and o.visible_get()
            and o.data and len(o.data.vertices)]


def _bounds(objects):
    boxes = [_box(o) for o in objects if o.type == 'MESH' and o.data and len(o.data.vertices)]
    if not boxes:
        return None
    return _span([b[0] for b in boxes] + [b[1] for b in boxes])


def _move(objects, offset):
    for top in (o for o in objects if o.parent is None):
        top.location += offset


def fit(objects, scene, point):
    """Scale the model about its base ``point`` to the scene's typical object height when it is more than
    ``FIT_RATIO`` off; the factor applied (1.0 when kept)."""
    import bpy
    bpy.context.view_layer.update()
    own = _bounds(objects)
    heights = [hi.z - lo.z for lo, hi in _others(scene, objects)
               if hi.z - lo.z > 0.05 and hi.z - lo.z >= 0.1 * max(hi.x - lo.x, hi.y - lo.y)]
    if own is None or not heights or own[1].z - own[0].z <= 1e-6:
        return 1.0
    ratio = median(heights) / (own[1].z - own[0].z)
    if 1 / FIT_RATIO <= ratio <= FIT_RATIO:
        return 1.0
    point = Vector(point)
    for top in (o for o in objects if o.parent is None):
        top.scale *= ratio
        top.location = point + (top.location - point) * ratio
    bpy.context.view_layer.update()
    return ratio


def beside(objects, scene):
    """Move a model added at the 3D cursor clear of the objects it would stand inside: to their right, on the ground
    they stand on; ``True`` when moved."""
    import bpy
    bpy.context.view_layer.update()
    own = _bounds(objects)
    others = _others(scene, objects)
    if own is None or not others:
        return False
    lo, hi = own

    def overlaps(box):
        return lo.x < box[1].x and box[0].x < hi.x and lo.y < box[1].y and box[0].y < hi.y and lo.z < box[1].z and box[0].z < hi.z
    hits = [box for box in others if overlaps(box)]
    if not hits:
        return False
    gap = max(0.1, 0.25 * (hi.x - lo.x))
    # To the right of what it was inside, standing on that object's ground.
    _move(objects, Vector((max(box[1].x for box in hits) + gap - lo.x, 0.0, min(box[0].z for box in hits) - lo.z)))
    bpy.context.view_layer.update()
    return True
