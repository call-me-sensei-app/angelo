# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Writing a pose by armature-space matrices without side effects (doc/angelo/rigging.rst, "How poses are written").

Retargeting, the game rig's bake, root motion and the IK/FK snap all know where a bone should end up in the armature's
space and need the local values that put it there. ``write`` works parents first from the rest pose and the bones'
current local values, so no view-layer update runs between bones, and:

* writes and keys only the bones it is told to (``keyed``); every other bone keeps its own values and curves;
* writes each bone in its own rotation mode (quaternion, axis-angle or any Euler order, continuous with the value it
  had), so a rig animated with Euler curves keeps playing them;
* keeps a quaternion on the same side as the previous one, so baked keys never flip the long way round.

Constraints are not evaluated: the chain is the bones' own transforms, which is what a key sets.

A bake passes ``keys`` (a ``Keys``): the values are recorded, and ``Keys.flush`` writes each curve once, through the
action slot's channelbag. Keying frame by frame re-sorted and re-handled a whole curve on every key, so a bake grew with
the square of its frames (a 1000-frame retarget of a 25-bone rig took 10.5 s, 12.6 times its 250-frame time).
"""

import bpy
from mathutils import Matrix

ROTATION_PATH = {"QUATERNION": "rotation_quaternion", "AXIS_ANGLE": "rotation_axis_angle"}


def ordered(obj):
    """The armature's bones, parents first."""
    return sorted(obj.data.bones, key=lambda b: len(b.parent_recursive))


def rest_in_parent(bone):
    return bone.parent.matrix_local.inverted() @ bone.matrix_local if bone.parent else bone.matrix_local.copy()


def _set_rotation(pb, quaternion):
    mode = pb.rotation_mode
    if mode == 'QUATERNION':
        if pb.rotation_quaternion.dot(quaternion) < 0.0:
            quaternion.negate()
        pb.rotation_quaternion = quaternion
    elif mode == 'AXIS_ANGLE':
        axis, angle = quaternion.to_axis_angle()
        pb.rotation_axis_angle = (angle, axis.x, axis.y, axis.z)
    else:
        pb.rotation_euler = quaternion.to_euler(mode, pb.rotation_euler)


def chain(obj, desired):
    """{bone: armature-space matrix} for every bone once ``desired`` is applied (see ``write``), without writing."""
    pose = {}
    for bone in ordered(obj):
        parent = pose[bone.parent.name] if bone.parent else Matrix.Identity(4)
        natural = parent @ rest_in_parent(bone)
        entry = desired.get(bone.name)
        if entry is None:
            pose[bone.name] = natural @ obj.pose.bones[bone.name].matrix_basis
        elif isinstance(entry, tuple):   # ("rotation", m): m's rotation, the position its parent gives it.
            target = entry[1].to_3x3().normalized().to_4x4()
            target.translation = (natural @ obj.pose.bones[bone.name].matrix_basis).translation
            pose[bone.name] = target
        else:
            pose[bone.name] = entry
    return pose


class Keys:
    """A bake's keys for one object and its current action slot, held until ``flush``."""

    def __init__(self, obj):
        self.obj, self.points = obj, {}

    def record(self, pb, frame):
        for path in ("location", ROTATION_PATH.get(pb.rotation_mode, "rotation_euler"), "scale"):
            data_path = 'pose.bones["%s"].%s' % (bpy.utils.escape_identifier(pb.name), path)
            for index, value in enumerate(getattr(pb, path)):
                self.points.setdefault((data_path, index, pb.name), []).append((float(frame), float(value)))

    def flush(self):
        """Write every recorded curve at once: a new curve filled in one call, an existing one with each key put in
        without re-handling, then each curve's handles computed once."""
        from . import anim_channels
        if not self.points:
            return
        bag = anim_channels.channelbag(self.obj, ensure=True)
        for (data_path, index, group), points in self.points.items():
            curve = bag.fcurves.find(data_path, index=index)
            if curve is None:
                curve = bag.fcurves.new(data_path, index=index)
                found = bag.groups.get(group) if hasattr(bag.groups, "get") else None
                curve.group = found or bag.groups.new(group)
            keys = curve.keyframe_points
            if not len(keys):
                keys.add(len(points))
                keys.foreach_set("co", [value for point in points for value in point])
            else:
                for frame, value in points:
                    keys.insert(frame, value, options={'FAST'})
            curve.update()
        self.points.clear()


def write(obj, desired, frame=None, keyed=None, keys=None):
    """Pose ``obj`` so each bone in ``desired`` ({bone: armature-space Matrix, or ("rotation", Matrix)}) gets it.

    Writes (and, with ``frame``, keys) only the bones in ``keyed`` (default: those in ``desired``); with ``keys`` the keys
    are recorded there for one write per curve. Returns the resulting armature-space matrices of every bone."""
    keyed = set(desired) if keyed is None else set(keyed)
    pose = chain(obj, desired)
    for bone in ordered(obj):
        if bone.name not in keyed:
            continue
        parent = pose[bone.parent.name] if bone.parent else Matrix.Identity(4)
        basis = (parent @ rest_in_parent(bone)).inverted() @ pose[bone.name]
        location, rotation, scale = basis.decompose()
        pb = obj.pose.bones[bone.name]
        pb.location, pb.scale = location, scale
        _set_rotation(pb, rotation)
        if frame is not None and keys is not None:
            keys.record(pb, frame)
        elif frame is not None:
            for path in ("location", ROTATION_PATH.get(pb.rotation_mode, "rotation_euler"), "scale"):
                pb.keyframe_insert(path, frame=frame, group=bone.name)
    return pose
