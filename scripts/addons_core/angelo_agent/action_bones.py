# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``bone_set`` kernel: one bone's rest geometry, settings and pose on a private staged armature.

Contract (``action_bones_spec``): the target is a staged armature object whose
data is private to the stage; the bone is named exactly. Rest geometry is
edited in Edit mode on the staged armature only (``_edit_override``), with the
armature's X-axis mirror editing aid off, so only the named bone is written;
Blender's own hierarchy rules still apply (a connected bone's head is its
parent's tail, a connected child's head follows its parent's tail) and every
other bone this moves is reported. Bone settings are written on the armature
data and pose values on the object, both by ``rna_rules`` with read-back. A
value an Action, driver or NLA strip animates is refused, as evaluation would
replace it; only the channels and settings the write touches count
(``action_bones_spec.pose_paths``). Bones change no topology, so no evaluation bound is needed.
"""

import math

from .refusal_codes import RefusalError
from .action_context import _readback
from .action_edit_context import _edit_override
from . import bone_state, generic_settings
from .action_bones_spec import pose_edit, pose_paths, rest_edit

MAX_BONES = 4096
# Blender lengthens bones at or below this length when it leaves Edit mode.
MIN_LENGTH = .000001


def apply(context, obj, operation):
    params = operation["parameters"]
    if obj.type != "ARMATURE":
        raise RefusalError("bone_set targets an armature object", code='VALIDATION')
    data = context.require_private(obj.data)
    if len(data.bones) > MAX_BONES:
        raise RefusalError("The armature has more than %d bones, outside the bounded capability" % MAX_BONES, code='LIMIT_EXCEEDED')
    bone = data.bones.get(params["bone"])
    if bone is None:
        names = [repr(item.name[:64]) for item in data.bones][:8]
        raise RefusalError("No bone of this armature has this exact name; its bones: " + (", ".join(names) or "none"),
                           code='VALIDATION')
    name = bone.name
    held = obj.pose.bones.get(name) if obj.pose else None
    touched = pose_paths(params, getattr(held, "rotation_mode", None))
    written_channels = [path for path in touched if path not in params["pose_settings"]]
    path = bone_state.animated(obj, name, touched, list(params["settings"]))  # Only what this write touches.
    if path is not None:
        raise RefusalError("BONE_ANIMATED: %s of bone %s is animated, driven or in an NLA track; key it or edit "
                         "the Action instead" % (path[:64], name[:64]), code='UNSUPPORTED')
    moved = []
    if rest_edit(params):
        moved = _edit(context, obj, data, name, params)
    if params["settings"]:
        generic_settings.apply(context, obj, data.bones[name], params["settings"], "bone")
    if pose_edit(params):
        _pose(context, obj, name, params)
        # Commit copies only the posed bones (bone_state.transfer_pose); extras persist with the stage.
        posed = context.extras.setdefault("posed_bones", {}).setdefault(operation["target"], [])
        if name not in posed:
            posed.append(name)
        # The channels it wrote, which alone commit copies (a keyed channel it left belongs to its animation);
        # None once a pose operator ran on the armature: every channel then.
        channels = context.extras.setdefault("posed_channels", {}).setdefault(operation["target"], {})
        if channels is not None:
            written = channels.setdefault(name, [])
            written += [path for path in written_channels if path not in written]
    bone, pose_bone = data.bones[name], obj.pose.bones.get(name)
    rest = {"head": list(bone.head_local), "tail": list(bone.tail_local), "roll": bone_state.rest_roll(bone),
            "parent": bone.parent.name if bone.parent else None, "connected": bool(bone.use_connect)}
    result = {"bone": name, "rest": rest}
    if pose_bone is not None:
        result["pose"] = {"rotation_mode": pose_bone.rotation_mode, "location": list(pose_bone.location),
                          "rotation": list(bone_state.rotation_matrix(pose_bone).to_euler("XYZ")),
                          "scale": list(pose_bone.scale)}
    if moved:
        result["moved_connected_bones"] = moved
    return _readback(obj, operation, **result)


def _edit(context, obj, data, name, params):
    """Rest edits in staged Edit mode; returns the other bones Blender's connections moved."""
    hidden, obj.hide_viewport = obj.hide_viewport, False  # Mode changes need a visible staged copy.
    try:
        with _edit_override(context, obj):
            mirror, data.use_mirror_x = data.use_mirror_x, False
            try:
                return _edit_bone(data.edit_bones, name, params)
            finally:
                data.use_mirror_x = mirror
    finally:
        obj.hide_viewport = hidden


def _edit_bone(edit_bones, name, params):
    bone = edit_bones[name]
    before = {item.name: (tuple(item.head), tuple(item.tail)) for item in edit_bones}
    if params["connected"] is False:
        bone.use_connect = False  # Before a new parent, so the head does not snap to it.
    if params["unparent"]:
        bone.parent = None
    elif params["parent"] is not None:
        parent = edit_bones.get(params["parent"])
        if parent is None:
            raise RefusalError("The new parent is not a bone of this armature: " + params["parent"][:64], code='VALIDATION')
        bone.parent = parent
        if bone.parent != parent:
            raise RefusalError("A bone cannot be parented to one of its own children", code='VALIDATION')
    if params["connected"]:
        if bone.parent is None:
            raise RefusalError("A connected bone needs a parent", code='VALIDATION')
        bone.use_connect = True
    for key in ("head", "tail"):
        if params[key] is not None:
            setattr(bone, key, params[key])
    if params["roll"] is not None:
        bone.roll = params["roll"]
    for key in ("head", "tail"):
        if params[key] is not None and list(getattr(bone, key)) != params[key]:
            raise RefusalError("The bone's %s did not keep the approved float32 coordinates" % key, code='INTERNAL_ERROR')
    if params["parent"] is not None and (bone.parent is None or bone.parent.name != params["parent"]):
        raise RefusalError("The bone did not keep its new parent", code='INTERNAL_ERROR')
    if params["connected"] is not None and bool(bone.use_connect) != params["connected"]:
        raise RefusalError("The bone did not keep its connection", code='INTERNAL_ERROR')
    moved = sorted(item.name for item in edit_bones
                   if item.name != name and (tuple(item.head), tuple(item.tail)) != before[item.name])
    for item in [bone] + [edit_bones[other] for other in moved]:
        length = math.dist(item.head, item.tail)
        if not math.isfinite(length) or length <= MIN_LENGTH or max(abs(v) for v in (*item.head, *item.tail)) > 100000:
            raise RefusalError("Bone %s would be degenerate or outside the finite bounds" % item.name[:64], code='VALIDATION')
    return moved[:16]


def _pose(context, obj, name, params):
    from mathutils import Euler
    pose_bone = obj.pose.bones.get(name) if obj.pose else None
    if pose_bone is None:
        raise RefusalError("The staged armature has no pose bone named " + name[:64], code='INTERNAL_ERROR')
    if params["rotation_mode"] is not None:
        pose_bone.rotation_mode = params["rotation_mode"]  # Blender keeps the orientation.
    if params["location"] is not None:
        pose_bone.location = params["location"]
    if params["rotation"] is not None:
        rotation, mode = Euler(params["rotation"], "XYZ"), pose_bone.rotation_mode
        if mode == "QUATERNION":
            pose_bone.rotation_quaternion = rotation.to_quaternion()
        elif mode == "AXIS_ANGLE":
            axis, angle = rotation.to_quaternion().to_axis_angle()
            pose_bone.rotation_axis_angle = (angle, *axis)
        else:
            pose_bone.rotation_euler = rotation.to_matrix().to_euler(mode)
    if params["scale"] is not None:
        pose_bone.scale = params["scale"]
    if params["pose_settings"]:
        generic_settings.apply(context, obj, pose_bone, params["pose_settings"], "pose bone")
    values = [value for key in ("location", "scale") for value in getattr(pose_bone, key)]
    if not all(math.isfinite(value) for value in values) or any(abs(value) < .000001 for value in pose_bone.scale):
        raise RefusalError("The pose of bone %s would be singular or not finite" % name[:64], code='VALIDATION')
