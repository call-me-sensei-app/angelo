# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Armature bone state ``bone_set`` writes: revisions, the pose commit, the ``bone`` check and the delta.

One reader serves four owners, so they agree on what a bone's writable state
is: every generic setting of a ``Bone`` and ``PoseBone`` that ``rna_rules``
admits (one cached path list per RNA type, never another bone's), the pose
channels, and the B-Bone custom handles by name.

* ``armature_state`` is the observed revision's ``bones`` field
  (``scene_fingerprints``): a manual edit of any of it makes a plan stale.
* ``transfer_pose`` is the commit's ``pose`` family: each pose bone's staged
  channels and settings, copied to the live pose bone of the same name with a
  rollback journal. Rotation channels are journaled before the rotation mode,
  so a rollback restores the mode first and then the raw channels (setting a
  mode converts the stored rotation).
* ``evaluate`` is the ``bone`` predicate: rest geometry from the bone's
  armature-space matrix, the pose rotation as an orientation in any mode, and
  settings through ``generic_settings.evaluate``. An animated value is
  inconclusive, as the ``property`` check's is.
* ``changes`` is the bounded preview delta.

The bpy-dependent math (``mathutils``) is imported where it is used, so host
tests import this module with RNA stand-ins.
"""

from .scene_read_values import label
from .rna_rules import plain

CHANNELS = ("location", "rotation_quaternion", "rotation_euler", "rotation_axis_angle", "scale")
MAX_PATHS = 600
MAX_CHANGED = 16
_PATHS = {}


def writable_paths(struct):
    """The generic settings paths ``bone_set`` writes on this struct's RNA type (cached per type)."""
    from .action_bones_spec import POSE_CHANNELS, SELECTION
    rna = getattr(struct, "bl_rna", None)
    if rna is None:
        return ()  # A plain stand-in has no RNA definition.
    key = (rna.identifier, len(rna.properties))
    if key not in _PATHS:
        from .rna_rules import writable_settings
        _PATHS[key] = tuple(entry["path"] for entry in writable_settings(rna, limit=MAX_PATHS)
                            if entry["path"].split(".")[0] not in SELECTION and entry["path"] not in POSE_CHANNELS)
    return _PATHS[key]


def _walk(struct, path):
    *parents, name = path.split(".")
    holder = struct
    for segment in parents:
        holder = getattr(holder, segment)
    return holder, name


def read(struct, path, reference_of):
    holder, name = _walk(struct, path)
    return plain(getattr(holder, name), reference_of)


def settings_values(struct, reference_of):
    return [read(struct, path, reference_of) for path in writable_paths(struct)]


def channels(pose_bone):
    return [getattr(pose_bone, "rotation_mode", None)] + [[float(value) for value in getattr(pose_bone, name, ())]
                                                          for name in CHANNELS]


def _layout(struct):
    """(column paths with widths, row paths) of a type: top-level numbers and booleans are columns."""
    rna = getattr(struct, "bl_rna", None)
    key = ("layout", getattr(rna, "identifier", None), len(getattr(rna, "properties", ())))
    if key not in _PATHS:
        columns, rows = [], []
        for path in writable_paths(struct):
            prop = rna.properties.get(path) if "." not in path else None
            if prop is not None and prop.type in {"BOOLEAN", "INT", "FLOAT"} and not getattr(prop, "is_runtime", False):
                columns.append((path, max(1, int(getattr(prop, "array_length", 0) or 0))))
            else:
                rows.append(path)
        _PATHS[key] = (tuple(columns), tuple(rows))
    return _PATHS[key]


def _columns(collection, columns):
    """Every item's values of each column path, one C copy per path (``foreach_get``)."""
    count = len(collection)
    values = {}
    for path, width in columns:
        buffer = [0] * (count * width)
        collection.foreach_get(path, buffer)
        values[path] = buffer
    return values


def _rows(collection, work, extra, describe):
    """Per-item reads of the paths ``foreach_get`` cannot copy (enums, nested structs, pointers)."""
    items = list(collection)
    if not items:
        return {}, []
    columns, rows = _layout(items[0]) if hasattr(collection, "foreach_get") else ((), writable_paths(items[0]))
    work.take(items=len(items), elements=len(items) * (sum(width for _, width in columns) + len(rows) + extra))
    return _columns(collection, columns), [describe(item, rows) for item in items]


def armature_state(obj, work, reference_of):
    """Every bone_set-writable value of an armature object, bounded by ``work``.

    Top-level numbers and booleans are copied a column at a time, so a large
    rig costs a few C copies per setting instead of a Python read per bone.
    """
    def bone_row(bone, rows):
        handles = [getattr(bone, "bbone_custom_handle_" + end, None) for end in ("start", "end")]
        return [label(bone.name), [read(bone, path, reference_of) for path in rows],
                [label(handle.name) if handle is not None else None for handle in handles]]

    def pose_row(pose_bone, rows):
        transform = getattr(pose_bone, "custom_shape_transform", None)
        return [label(pose_bone.name), getattr(pose_bone, "rotation_mode", None),
                [read(pose_bone, path, reference_of) for path in rows],
                label(transform.name) if transform is not None else None]

    data = obj.data
    bone_columns, bones = _rows(data.bones, work, 2, bone_row)
    pose = obj.pose.bones if obj.pose else ()
    pose_columns, pose_bones = _rows(pose, work, 18, pose_row)
    if pose_bones and hasattr(pose, "foreach_get"):
        pose_columns.update(_columns(pose, [(name, 4 if name in ("rotation_quaternion", "rotation_axis_angle") else 3)
                                            for name in CHANNELS]))
    elif pose_bones:
        pose_columns["channels"] = [channels(pose_bone) for pose_bone in pose]
    return {"data": settings_values(data, reference_of), "bones": bones, "bone_columns": bone_columns,
            "pose": pose_bones, "pose_columns": pose_columns}


def quoted(name):
    """A bone name inside an RNA data path (``bpy.utils.escape_identifier``)."""
    return '"' + str(name).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _action_paths(action):
    paths = set()
    for layer in getattr(action, "layers", ()):
        for strip in layer.strips:
            for channelbag in getattr(strip, "channelbags", ()):
                paths.update(curve.data_path for curve in channelbag.fcurves)
    return paths


def _strip_paths(strips, depth=0):
    """The paths the Actions of NLA strips animate (a meta strip's inner strips too); None past the depth bound."""
    paths = set()
    for strip in strips:
        if getattr(strip, "action", None) is not None:
            paths |= _action_paths(strip.action)
        inner = list(getattr(strip, "strips", ()) or ())
        if inner:
            found = _strip_paths(inner, depth + 1) if depth < 8 else None
            if found is None:
                return None
            paths |= found
    return paths


def animated_paths(block):
    """Data paths of one ID's Action curves, NLA strips' Action curves and drivers (Blender 5 channelbags).

    Every slot's curves count, as do muted tracks and strips (a superset).
    ``*`` only when the strips cannot be read through (deeper than eight meta
    levels, or no strips to read). NLA used to count as every path, which refused writes of
    channels no strip animates.
    """
    animation = getattr(block, "animation_data", None)
    if animation is None:
        return set()
    paths = {driver.data_path for driver in animation.drivers}
    if animation.action is not None:
        paths |= _action_paths(animation.action)
    for track in animation.nla_tracks:
        strips = getattr(track, "strips", None)
        found = _strip_paths(strips) if strips is not None else None
        if found is None:
            paths.add("*")
            break
        paths |= found
    return paths


def animated(obj, bone_name, pose_paths=(), bone_paths=()):
    """The first written path an Action, driver or NLA track animates, else None."""
    if pose_paths:
        curves = animated_paths(obj)
        prefix = "pose.bones[%s]." % quoted(bone_name)
        for path in pose_paths:
            if "*" in curves or prefix + path in curves:
                return path
    if bone_paths:
        curves = animated_paths(obj.data)
        prefix = "bones[%s]." % quoted(bone_name)
        for path in bone_paths:
            if "*" in curves or prefix + path in curves:
                return path
    return None


def transfer_pose(live, staged, mapping, journal, names=None, channels=None):
    """Copy the staged pose bones' channels and writable settings to the live pose, journaled.

    ``names`` are the bones the program posed (``stage.extras["posed_bones"]``);
    None copies every bone, which writes the same values at a higher cost.
    ``channels`` maps a bone to the channels ``bone_set`` wrote
    (``stage.extras["posed_channels"]``): only those are copied, so a keyed
    channel the write left keeps what its animation gives it; None (a pose
    operator ran) copies every channel.
    """
    from .program_jobs import ProgramError
    from .program_transfers import _live_id
    if live.pose is None or staged.pose is None:
        raise ProgramError("An armature's pose is missing at commit", code='INTERNAL_ERROR')
    sources = staged.pose.bones if names is None else [staged.pose.bones.get(name) for name in names]
    for source in sources:
        if source is None:
            raise ProgramError("A posed bone is no longer in the staged armature", code='INTERNAL_ERROR')
        target = live.pose.bones.get(source.name)
        if target is None:
            raise ProgramError("A posed bone is no longer in the live armature: " + label(source.name), code='STALE_DEPENDENCY')
        _transfer_channels(target, source, journal, lambda message: ProgramError(message, code='INTERNAL_ERROR'),
                           None if channels is None else channels.get(source.name, []))
        for path in writable_paths(source):
            holder, name = _walk(target, path)
            source_holder, _ = _walk(source, path)
            pointer = holder.bl_rna.properties[name].type == "POINTER"
            value = getattr(source_holder, name)
            value = _live_id(value, mapping) if pointer else plain(value)
            current = getattr(holder, name)
            if (current == value) if pointer else (plain(current) == value):
                continue
            if isinstance(current, (set, frozenset)):
                value = set(value)
            journal.append((holder, name, current if pointer or isinstance(current, (set, frozenset)) else plain(current)))
            setattr(holder, name, value)
            actual = getattr(holder, name)
            if (actual != value) if pointer or isinstance(value, set) else (plain(actual) != value):
                raise ProgramError("A committed pose setting did not retain its staged value: " + path, code='INTERNAL_ERROR')


def _transfer_channels(target, source, journal, error, only=None):
    """The staged channels onto the live bone: all of them, or ``only`` those named (and the mode if named)."""
    names = CHANNELS if only is None else tuple(name for name in CHANNELS if name in only)
    mode = only is None or "rotation_mode" in only
    wanted = {name: tuple(getattr(source, name)) for name in names}
    current = {name: tuple(getattr(target, name)) for name in names}
    if wanted == current and (not mode or source.rotation_mode == target.rotation_mode):
        return
    # A mode change converts channels, and so does its rollback: then every channel is journaled and restored.
    for name in (CHANNELS if mode else names):
        journal.append((target, name, tuple(getattr(target, name))))
    if mode:
        # Journaled last, so a rollback restores it first: setting a mode converts the channels.
        journal.append((target, "rotation_mode", target.rotation_mode))
        target.rotation_mode = source.rotation_mode
    for name in names:
        setattr(target, name, wanted[name])
    if (mode and target.rotation_mode != source.rotation_mode
            or any(tuple(getattr(target, name)) != wanted[name] for name in names)):
        raise error("A committed pose channel did not retain its staged value: " + label(source.name))


def rotation_matrix(pose_bone):
    """A pose bone's rotation as a 3x3 matrix, whatever its rotation mode."""
    from mathutils import Euler, Matrix, Quaternion, Vector
    mode = pose_bone.rotation_mode
    if mode == "QUATERNION":
        quaternion = Quaternion(pose_bone.rotation_quaternion)
        return quaternion.normalized().to_matrix() if quaternion.magnitude > 1e-12 else Matrix.Identity(3)
    if mode == "AXIS_ANGLE":
        angle, *axis = pose_bone.rotation_axis_angle
        axis = Vector(axis)
        return Matrix.Rotation(angle, 3, axis.normalized()) if axis.length > 1e-12 else Matrix.Identity(3)
    return Euler(pose_bone.rotation_euler, mode).to_matrix()


def rest_roll(bone):
    """The bone's rest roll (radians) from its armature-space matrix, as Edit mode shows it."""
    return float(bone.AxisRollFromMatrix(bone.matrix_local.to_3x3())[1])


def _angle_near(actual, expected, tolerance):
    difference = (actual - expected + 3.141592653589793) % 6.283185307179586 - 3.141592653589793
    return abs(difference) <= tolerance


def evaluate(reader, obj, check):
    """(passed, bounded metrics) for one canonical ``bone`` check on a staged or live armature."""
    from .validation_reader import Inconclusive, number_vector, resolve_target, values_near
    from .generic_settings import evaluate as evaluate_settings
    if obj.type != "ARMATURE":
        return False, {"type": obj.type}
    bone = obj.data.bones.get(check["bone"])
    pose_bone = obj.pose.bones.get(check["bone"]) if obj.pose else None
    if bone is None:
        return False, {"present": False}
    reader.spend(4 + len(check.get("settings", {})) + len(check.get("pose_settings", {})))
    tolerance = check.get("tolerance")  # None: each number at its own float32 precision (check_defaults).
    pose_fields = [key for key in ("location", "rotation", "scale", "rotation_mode") if key in check]
    from .action_bones_spec import mode_channel
    # The channels the check reads: its rotation is the one the bone's mode stores now.
    channel_paths = [mode_channel(pose_bone.rotation_mode) if key == "rotation" else key for key in pose_fields
                     if pose_bone is not None or key != "rotation"]
    path = animated(obj, bone.name, channel_paths + list(check.get("pose_settings", {})),
                    list(check.get("settings", {})))
    if path is not None:
        raise Inconclusive("An animated or driven bone value can differ from its stored value: " + path)
    observed, failed = {}, []

    def compare(key, actual, passed):
        observed[key] = actual
        if not passed:
            failed.append(key)

    for key, value in (("head", bone.head_local), ("tail", bone.tail_local)):
        if key in check:
            actual = number_vector(value, 3)
            compare(key, actual, values_near(actual, check[key], tolerance))
    if "roll" in check:
        actual = rest_roll(bone)
        compare("roll", actual, _angle_near(actual, check["roll"], max(tolerance or 0, 0.00005)))
    if "parent" in check:
        actual = bone.parent.name[:64] if bone.parent else None
        compare("parent", actual, actual == check["parent"])
    if "connected" in check:
        compare("connected", bool(bone.use_connect), bool(bone.use_connect) == check["connected"])
    if pose_fields and pose_bone is None:
        return False, {"present": True, "pose_bone": False}
    if "rotation_mode" in check:
        compare("rotation_mode", pose_bone.rotation_mode, pose_bone.rotation_mode == check["rotation_mode"])
    for key in ("location", "scale"):
        if key in check:
            actual = number_vector(getattr(pose_bone, key), 3)
            compare(key, actual, values_near(actual, check[key], tolerance))
    if "rotation" in check:
        from mathutils import Euler
        matrix, expected = rotation_matrix(pose_bone), Euler(check["rotation"], "XYZ").to_matrix()
        difference = max(abs(matrix[row][column] - expected[row][column]) for row in range(3) for column in range(3))
        compare("rotation", [float(value) for value in matrix.to_euler("XYZ")],
                difference <= max(tolerance or 0, 0.00001))
    resolve = lambda reference: resolve_target(reader.context, reference)  # noqa: E731
    for key, struct in (("settings", bone), ("pose_settings", pose_bone)):
        if key in check:
            if struct is None:
                return False, {"present": True, "pose_bone": False}
            passed, result = evaluate_settings(struct, check[key], tolerance, resolve)
            compare(key, result["values"], passed)
    result = {"bone": bone.name[:64], "values": observed, "scope": "REST_BONE_AND_STORED_POSE_NOT_EVALUATED"}
    if failed:
        result["mismatched"] = failed
    return not failed, result


def changes(before, after, names=None):
    """Bounded before/after of the bones whose rest geometry, pose channels or settings differ.

    ``names`` maps a staged object's pointer to its live name, so a custom shape
    that is the staged copy of the same object is not reported as changed.
    """
    from .scene_read_values import vector
    names = names or {}

    def reference_of(value):
        return label(names.get(value.as_pointer(), value.name))

    rows, total = [], 0
    for bone in after.data.bones:
        old = before.data.bones.get(bone.name)
        old_pose = before.pose.bones.get(bone.name) if before.pose else None
        new_pose = after.pose.bones.get(bone.name) if after.pose else None
        row = {}
        rest = [[vector(item.head_local), vector(item.tail_local), round(rest_roll(item), 6)] if item is not None else None
                for item in (old, bone)]
        if rest[0] is None or rest[0][:2] != rest[1][:2] or not _angle_near(rest[0][2], rest[1][2], .000002):
            row["rest"] = {"before": rest[0], "after": rest[1]}  # Head, tail and roll.
        parents = [item.parent.name if item is not None and item.parent else None for item in (old, bone)]
        if parents[0] != parents[1]:
            row["parent"] = {"before": parents[0], "after": parents[1]}
        if old_pose is not None and new_pose is not None and channels(old_pose) != channels(new_pose):
            row["pose"] = {"before": channels(old_pose), "after": channels(new_pose)}
        settings = [settings_values(item, reference_of) if item is not None else None
                    for item in (old, bone, old_pose, new_pose)]
        if settings[0] != settings[1] or settings[2] != settings[3]:
            row["settings_changed"] = True
        if row:
            total += 1
            if len(rows) < MAX_CHANGED:
                rows.append(dict(name=label(bone.name), **row))
    return {"bones": rows, "changed": total, "truncated": total > len(rows)}
