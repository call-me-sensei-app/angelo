# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``bone_set``: one bone of an armature, its rest geometry, pose and settings. No bpy.

The operation targets an armature object and names one existing bone exactly.
Every field is optional (at least one is set); the kernel (``action_bones``)
applies them in this order on the private staged copy:

* rest geometry, in Edit mode on the staged armature only: ``connected``
  false first, then ``parent`` or ``unparent``, ``connected`` true, ``head``
  and ``tail`` (armature space, float32-canonical) and ``roll`` (radians);
* ``settings``: the bone's rest settings (``Bone``, on the armature data) by
  RNA path;
* the pose, on the object: ``rotation_mode`` (Blender converts the stored
  rotation, keeping the orientation), ``location`` (bone space), ``rotation``
  (XYZ Euler radians, written in the bone's own rotation mode), ``scale`` and
  ``pose_settings`` (``PoseBone`` settings by RNA path).

Settings paths follow the generic rules (``rna_rules``: deny-list, RNA types
and ranges, object pointers as ``{"object": handle}``) plus the bone rules
here: selection is editor state, and the transform channels and rest geometry
have the typed fields above. A path never reaches another bone
(``rna_rules.DENIED_STRUCTS``).

Families: rest geometry and ``settings`` change the armature data, which commit
swaps whole (``armature``); pose fields change the object's pose, which commit
copies bone by bone with a journal (``pose``, ``bone_state.transfer_pose``).
"""

import copy
import math

from .action_schema_types import text, vector, number, boolean, enum, nullable, obj, float32_vector
from .action_properties import VALUE
from .rna_rules import PATH_PATTERN, PropertyPathError, object_reference

ROTATION_MODES = ("QUATERNION", "XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX", "AXIS_ANGLE")
# Nullable fields; ``unparent`` is the rest edit's boolean.
REST_FIELDS = ("head", "tail", "roll", "parent", "connected")
POSE_FIELDS = ("rotation_mode", "location", "rotation", "scale")
# Typed fields, never settings paths: the pose channels and the rest geometry.
POSE_CHANNELS = frozenset({"location", "rotation_quaternion", "rotation_euler", "rotation_axis_angle", "rotation_mode",
                           "scale", "matrix", "matrix_basis"})
REST_GEOMETRY = frozenset({"head", "tail", "roll", "head_local", "tail_local", "use_connect", "length", "matrix",
                           "matrix_local"})
# Selection is editor state, as an object's is.
SELECTION = frozenset({"select", "select_head", "select_tail"})
# Pointers to other bones: a path writes only the bone it names (also refused by RNA type).
OTHER_BONES = frozenset({"bbone_custom_handle_start", "bbone_custom_handle_end", "custom_shape_transform", "child",
                         "bone"})
MAX_SETTINGS = 64

SETTINGS = {"type": "object", "properties": {}, "additionalProperties": VALUE,
            "propertyNames": {"pattern": PATH_PATTERN}, "maxProperties": MAX_SETTINGS, "default": {}}
BONE_SET_SCHEMA = obj({
    "bone": text(),
    "head": nullable(vector()), "tail": nullable(vector()), "roll": nullable(number(-2 * math.pi, 2 * math.pi)),
    "parent": nullable(text()), "unparent": boolean(False), "connected": nullable({"type": "boolean"}),
    "settings": SETTINGS,
    "rotation_mode": nullable(enum(ROTATION_MODES)), "location": nullable(vector()),
    "rotation": nullable(vector(limit=2 * math.pi)), "scale": nullable(vector(limit=1000)),
    "pose_settings": SETTINGS}, ["bone"])


def screen_settings(settings, pose):
    """Raise for a bone path the rules refuse, before any RNA is consulted (pure)."""
    from .generic_settings import screen_settings as screen_generic
    if not isinstance(settings, dict):
        return
    for path in settings:
        text_path = str(path)
        if text_path.split(".")[0] in SELECTION:
            raise PropertyPathError("NOT_ALLOWED", path, "bone selection is editor state")
        if set(text_path.split(".")) & OTHER_BONES:
            raise PropertyPathError("NOT_ALLOWED", path, "another bone's settings are written by naming that bone")
        if pose and text_path in POSE_CHANNELS:
            raise PropertyPathError("NOT_ALLOWED", path, "pose transforms are bone_set's location, rotation, "
                                                         "scale and rotation_mode fields")
        if not pose and text_path in REST_GEOMETRY:
            raise PropertyPathError("NOT_ALLOWED", path, "rest geometry is bone_set's head, tail, roll, parent "
                                                         "and connected fields")
    screen_generic(settings)


def references(params):
    """Object handles the operation's settings point at (a pose bone's custom shape)."""
    values = list(params.get("settings", {}).values()) + list(params.get("pose_settings", {}).values())
    return [object_reference(value) for value in values if object_reference(value) is not None]


def canonical(operation, raw):
    """Cross-field rules of a schema-validated ``bone_set`` operation, in place.

    ``raw`` is the submitted operation: settings keep their JSON values
    (integers stay integers for INT properties), as the generic kinds do.
    """
    params, given = operation["parameters"], raw["parameters"]
    for key, pose in (("settings", False), ("pose_settings", True)):
        screen_settings(given.get(key, {}), pose)
        params[key] = copy.deepcopy(given.get(key, {}))
    if not (rest_edit(params) or params["settings"] or pose_edit(params)):
        raise ValueError("bone_set changes nothing: set the rest geometry, pose or settings of the bone")
    if params["parent"] is not None and params["unparent"]:
        raise ValueError("Choose a new parent or unparent, not both")
    if params["parent"] == params["bone"]:
        raise ValueError("A bone cannot be its own parent")
    if params["connected"] and params["unparent"]:
        raise ValueError("A connected bone needs a parent")
    if params["connected"] and params["head"] is not None:
        raise ValueError("A connected bone's head is its parent's tail: set the parent's tail, or connected false")
    for key in ("head", "tail"):
        if params[key] is not None:
            params[key] = float32_vector(params[key])
    if params["head"] is not None and params["tail"] is not None and \
            math.dist(params["head"], params["tail"]) <= float32_vector([.000001, 0, 0])[0]:
        raise ValueError("A bone needs a float32-representable length greater than 1e-6")
    if params["scale"] is not None and any(abs(value) < .000001 for value in params["scale"]):
        raise ValueError("Pose scale components must be nonzero")
    if operation["target"] in references(params):
        raise ValueError("A bone setting cannot point at its own armature")
    return operation


def rest_edit(params):
    """Whether the operation edits rest geometry (staged Edit mode)."""
    return any(params[key] is not None for key in REST_FIELDS) or params["unparent"]


def mode_channel(mode):
    """The rotation channel a rotation mode reads: every Euler order shares ``rotation_euler``."""
    return {"QUATERNION": "rotation_quaternion", "AXIS_ANGLE": "rotation_axis_angle"}.get(mode, "rotation_euler")


def pose_paths(params, current_mode):
    """The pose channels (paths below ``pose.bones["name"].``) a canonical ``bone_set`` writes, in kernel order.

    ``rotation_mode`` writes the mode, and a change of the rotation's family
    (Euler orders share one, ``BKE_rotMode_change_values``) touches the new
    family's channel, which the setter rewrites, and the old one, which stops
    deciding the rotation; ``rotation`` writes the channel of the mode it ends
    in; ``current_mode`` None (unknown) touches every rotation channel.
    Only these channels decide whether a key or driver would override the
    write: a bone keyed on its rotation can still be moved (otherwise any
    keyed pose channel refused every pose write).
    """
    paths, mode = [], params["rotation_mode"] or current_mode
    if params["rotation_mode"] is not None:
        paths.append("rotation_mode")
        if current_mode is None:
            paths += ["rotation_euler", "rotation_quaternion", "rotation_axis_angle"]
        elif mode_channel(current_mode) != mode_channel(params["rotation_mode"]):
            # The setter writes the new family's channel, and evaluation stops reading the old one: keys on either
            # would no longer decide the rotation (otherwise a keyed Euler bone switched to
            # quaternion silently stopped playing).
            paths += [mode_channel(params["rotation_mode"]), mode_channel(current_mode)]
    if params["location"] is not None:
        paths.append("location")
    if params["rotation"] is not None:
        paths += [mode_channel(mode)] if mode else ["rotation_euler", "rotation_quaternion", "rotation_axis_angle"]
    if params["scale"] is not None:
        paths.append("scale")
    paths += [path for path in params["pose_settings"]]
    return list(dict.fromkeys(paths))


def pose_edit(params):
    """Whether the operation writes the pose (channels or pose settings)."""
    return any(params[key] is not None for key in POSE_FIELDS) or bool(params["pose_settings"])


def families(params):
    """The families one canonical operation writes: armature data, the pose, or both."""
    rest = rest_edit(params) or bool(params["settings"])
    return ["object"] + ["armature"] * rest + ["pose"] * pose_edit(params)


def derived_check(target, params):
    """The ``bone`` check one operation states exactly: every value it set."""
    check = {"kind": "bone", "target": target, "bone": params["bone"]}
    for key in ("head", "tail", "roll", "parent", "connected", "rotation_mode", "location", "rotation", "scale"):
        if params[key] is not None:
            check[key] = copy.deepcopy(params[key])
    if params["unparent"]:
        check.update(parent=None, connected=False)  # Blender clears the connection with the parent.
    for key in ("settings", "pose_settings"):
        if params[key]:
            check[key] = copy.deepcopy(params[key])
    return check  # No tolerance: each number compares at its own float32 precision (check_defaults).


def merge_check(previous, new, params):
    """A later ``bone_set`` of the same bone: later values win; a moved head is no longer stated."""
    merged = {**previous, **new}
    for key in ("settings", "pose_settings"):
        if key in previous or key in new:
            merged[key] = {**previous.get(key, {}), **new.get(key, {})}
    # Connecting snaps the head to the parent's tail; reparenting a connected bone does too.
    if "head" not in new and (params["connected"] or params["parent"] is not None):
        merged.pop("head", None)
    if "tolerance" in previous or "tolerance" in new:  # A stated tolerance survives; none means per value.
        merged["tolerance"] = max(previous.get("tolerance", 0), new.get("tolerance", 0))
    return merged


def keeps(check, operation):
    """Whether a later ``bone_set`` on the same armature leaves an earlier bone check true.

    The same bone merges by identity. Another bone's head or tail moves connected
    neighbours (its parent's tail, its children's heads), so a check stating a
    rest endpoint is dropped then.
    """
    params = operation["parameters"]
    if params["bone"] == check["bone"]:
        return True
    moves = params["head"] is not None or params["tail"] is not None
    return not (moves and ("head" in check or "tail" in check))


def screen_check(check):
    """Semantic rules of a ``bone`` check beyond its schema (paths by the bone rules)."""
    for key, pose in (("settings", False), ("pose_settings", True)):
        if key in check:
            screen_settings(check[key], pose)
    return check
