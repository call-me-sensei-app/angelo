# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``keyframe_edit``: one structural edit of the selected keys on a private staged Action.

Operations (``anim_spec``): DELETE, OFFSET and SCALE (moved keys replace the
unmoved keys they land on, as a transform merges keys), INSERT (the curve's
own value, so its shape holds, or an explicit one), CLEAN, DECIMATE and SMOOTH
(``anim_filters``), BAKE (Blender's own ``FCurve.bake``), EULER_FILTER and SNAP.
Curves are rewritten whole from key tables (``anim_keys``), never key by key,
and a channel left without keys or modifiers is removed, as Delete Keyframes
does. Work is bounded by ``anim_channels.MAX_KEYS`` before anything is written.
"""

import math

import numpy as np

from . import anim_filters as filters
from .anim_channels import AnimationError, MAX_KEYS, edit_curves, remove_empty, in_selection
from .anim_keys import KeyTable, key_rows, moved_merge
from .action_context import _readback

AUTOMATIC_HANDLES = {"AUTO", "AUTO_CLAMPED", "VECTOR"}


def _enum_value(name):
    import bpy
    return {item.identifier: item.value for item in bpy.types.Keyframe.bl_rna.properties[name].enum_items}


def _rewrite(curve, table, rows, transform):
    """Apply ``transform`` to the selected rows; moved rows win where they land on unmoved ones."""
    rows = list(rows)
    moved = table.take(rows)
    transform(moved.columns)
    chosen = set(rows)
    unmoved = [index for index in range(len(table)) if index not in chosen]
    moved_merge(table, moved, unmoved).write(curve)


def _offset(params):
    def apply(columns):
        for name in ("co", "handle_left", "handle_right"):
            columns[name][:, 0] += params["offset"]
            columns[name][:, 1] += params["value_offset"]
    return apply


def _scale(params):
    def apply(columns):
        for name in ("co", "handle_left", "handle_right"):
            columns[name][:, 0] = params["pivot"] + (columns[name][:, 0] - params["pivot"]) * params["factor"]
            columns[name][:, 1] = (params["value_pivot"]
                                   + (columns[name][:, 1] - params["value_pivot"]) * params["value_factor"])
        if params["factor"] < 0:  # Mirrored in time: the left handle is now on the right.
            for left, right in (("handle_left", "handle_right"), ("handle_left_type", "handle_right_type"),
                                ("select_left_handle", "select_right_handle")):
                columns[left], columns[right] = columns[right].copy(), columns[left].copy()
    return apply


def _snap(params):
    types = _enum_value("handle_left_type")
    automatic = [types[name] for name in AUTOMATIC_HANDLES]

    def apply(columns):
        if params["mode"] == "NEAREST_FRAME":
            delta = np.round(columns["co"][:, 0]) - columns["co"][:, 0]
            for name in ("co", "handle_left", "handle_right"):
                columns[name][:, 0] += delta
            return
        for name in ("handle_left", "handle_right"):
            columns[name][:, 1] = columns["co"][:, 1]
        for name in ("handle_left_type", "handle_right_type"):
            columns[name] = np.where(np.isin(columns[name], automatic), types["ALIGNED"], columns[name])
    return apply


def _insert(curve, table, params, interpolation, key_type):
    """New rows at the frames (the curve's value before any insert), replacing keys already there."""
    frames = params["frames"]
    values = [params["value"] if params["value"] is not None else curve.evaluate(frame) for frame in frames]
    template = table.take([0] * len(frames)) if len(table) else None
    if template is None:  # An empty channel: Blender's defaults for the remaining fields.
        point = curve.keyframe_points.insert(frames[0], values[0])
        template = KeyTable.read(curve).take([0] * len(frames))
        curve.keyframe_points.remove(point)
    columns = template.columns
    for index, (frame, value) in enumerate(zip(frames, values)):
        for name in ("co", "handle_left", "handle_right"):
            columns[name][index] = (frame, value)
    columns["easing"][:] = 0  # AUTO
    for name in ("select_control_point", "select_left_handle", "select_right_handle"):
        columns[name][:] = 0
    types = _enum_value("handle_left_type")
    columns["handle_left_type"][:] = columns["handle_right_type"][:] = types["AUTO_CLAMPED"]
    if interpolation is not None:
        columns["interpolation"][:] = interpolation
    elif len(table):  # The segment a new key splits keeps its interpolation.
        previous = np.searchsorted(table.frames(), np.asarray(frames, dtype=np.float64) + 1e-4, side="right") - 1
        columns["interpolation"][:] = table.columns["interpolation"][np.clip(previous, 0, len(table) - 1)]
    columns["type"][:] = key_type if key_type is not None else 0
    moved_merge(table, template, list(range(len(table)))).write(curve)


def _clean(owner, curve, table, params):
    selected = [in_selection(frame, (), params["frame_range"]) for frame in table.frames()]
    points = list(map(tuple, table.columns["co"]))
    kept = filters.clean(points, params["threshold"], selected)
    table.take(kept).write(curve)
    if params["channels"] and len(curve.keyframe_points) == 1 and not len(curve.modifiers):
        if abs(curve.keyframe_points[0].co.y - _default(owner, curve)) <= 1e-7:
            curve.keyframe_points.clear()


def _default(owner, curve):
    """The animated property's RNA default at the curve's index (0 when it cannot be read)."""
    path = curve.data_path
    head, _, name = path.rpartition(".")
    try:
        holder = owner.path_resolve(head) if head else owner
        prop = holder.bl_rna.properties[name]
        return float(prop.default_array[curve.array_index] if getattr(prop, "is_array", False) else prop.default)
    except (ValueError, KeyError, AttributeError, IndexError, TypeError):
        return 0.0


def _decimate(curve, table, params):
    rows = key_rows(table, (), params["frame_range"])
    points = [tuple(table.columns["co"][index]) for index in rows]
    kept = set(rows[index] for index in filters.decimate(points, params["ratio"], params["error"]))
    chosen = set(rows)
    table.take([index for index in range(len(table)) if index not in chosen or index in kept]).write(curve)


def _smooth(curve, table, params):
    rows = key_rows(table, (), params["frame_range"])
    columns = table.columns
    values, left, right = (list(columns[name][rows, 1]) for name in ("co", "handle_left", "handle_right"))
    for _ in range(params["passes"]):
        values, left, right = filters.smooth(values, left, right)
    columns["co"][rows, 1], columns["handle_left"][rows, 1], columns["handle_right"][rows, 1] = values, left, right
    table.write(curve)


def _bake(curve, params):
    start, end = (int(math.floor(params["frame_range"][0])), int(math.ceil(params["frame_range"][1])))
    muted = [modifier.mute for modifier in curve.modifiers]
    if not params["bake_modifiers"]:
        for modifier in curve.modifiers:
            modifier.mute = True
    curve.bake(start, end, step=params["step"], remove=params["remove"])
    if params["bake_modifiers"]:
        while len(curve.modifiers):
            curve.modifiers.remove(curve.modifiers[0])
    else:
        for modifier, mute in zip(curve.modifiers, muted):
            modifier.mute = mute
    if params["interpolation"] is not None:
        for point in curve.keyframe_points:
            if start <= point.co.x < end:
                point.interpolation = params["interpolation"]
    curve.update()


def _euler(curves):
    """Blender's Euler filter: compatible X/Y/Z rotations where keyed together, then whole turns per channel."""
    from mathutils import Euler
    groups = {}
    for curve in curves:
        groups.setdefault(curve.data_path, {})[curve.array_index] = curve
    for group in groups.values():
        if set(group) >= {0, 1, 2} and len({len(group[axis].keyframe_points) for axis in range(3)}) == 1:
            tables = [KeyTable.read(group[axis]) for axis in range(3)]
            frames = [table.frames() for table in tables]
            same = np.all(np.abs(frames[0] - frames[1]) < 1e-4) and np.all(np.abs(frames[0] - frames[2]) < 1e-4)
            if same and len(frames[0]) >= 2:
                rotations = list(zip(*(table.columns["co"][:, 1] for table in tables)))
                filtered = filters.euler_multi(rotations, lambda euler, previous: Euler(euler, "XYZ").to_matrix()
                                               .to_euler("XYZ", Euler(previous, "XYZ")))
                for axis, table in enumerate(tables):
                    delta = np.array([row[axis] for row in filtered]) - table.columns["co"][:, 1]
                    for name in ("co", "handle_left", "handle_right"):
                        table.columns[name][:, 1] += delta
                    table.write(group[axis])
    for curve in curves:
        table = KeyTable.read(curve)
        offsets = np.array(filters.euler_single(list(table.columns["co"][:, 1])))
        if len(offsets) and np.any(offsets):
            for name in ("co", "handle_left", "handle_right"):
                table.columns[name][:, 1] += offsets
            table.write(curve)


def edit(context, obj, operation):
    """keyframe_edit: one operation on the selected keys of the selected channels."""
    params = operation["parameters"]
    kind = params["operation"]
    owner, curves = edit_curves(context, obj, operation["target"], params)
    if kind == "EULER_FILTER":
        curves = [curve for curve in curves if curve.data_path.rsplit(".", 1)[-1] == "rotation_euler"]
        if not curves:
            raise AnimationError("CHANNEL_NOT_FOUND", "no rotation_euler channels are animated")
    keys_before = sum(len(curve.keyframe_points) for curve in curves)
    _bound(kind, params, curves, keys_before)
    if kind == "INSERT":
        interpolation = _enum_value("interpolation")[params["interpolation"]] if params["interpolation"] else None
        key_type = _enum_value("type")[params["key_type"]] if params["key_type"] else None
    selected = 0
    for curve in curves:
        table = KeyTable.read(curve)
        rows = key_rows(table, params.get("frames", ()) if kind in {"DELETE"} else (), params.get("frame_range"))
        if kind == "DELETE":
            selected += len(rows)
            chosen = set(rows)
            table.take([index for index in range(len(table)) if index not in chosen]).write(curve)
        elif kind == "OFFSET":
            selected += len(rows)
            _rewrite(curve, table, rows, _offset(params))
        elif kind == "SCALE":
            selected += len(rows)
            _rewrite(curve, table, rows, _scale(params))
        elif kind == "SNAP":
            selected += len(rows)
            _rewrite(curve, table, rows, _snap(params))
        elif kind == "INSERT":
            selected += len(params["frames"])
            _insert(curve, table, params, interpolation, key_type)
        elif kind == "CLEAN":
            selected += len(rows)
            _clean(owner, curve, table, params)
        elif kind == "DECIMATE":
            selected += len(rows)
            _decimate(curve, table, params)
        elif kind == "SMOOTH":
            selected += len(rows)
            _smooth(curve, table, params)
        elif kind == "BAKE":
            _bake(curve, params)
            selected += 1
    if kind == "EULER_FILTER":
        _euler(curves)
        selected = keys_before
    if not selected and kind not in {"BAKE"}:
        raise AnimationError("KEY_NOT_FOUND", "no keys of the selected channels are at those frames or in the range")
    keys_after = sum(len(curve.keyframe_points) for curve in curves)
    removed = remove_empty(owner, curves)
    return _readback(obj, operation, operation_name=kind, keys_before=keys_before, keys_after=keys_after,
                     selected_keys=selected, removed_channels=removed)


def _bound(kind, params, curves, keys):
    """Refuse work beyond the key budget before writing anything."""
    if kind == "INSERT":
        added = len(params["frames"]) * len(curves)
    elif kind == "BAKE":
        start, end = params["frame_range"]
        added = (int((end - start) / params["step"]) + 2) * len(curves)
    else:
        added = 0
    if keys + added > MAX_KEYS:
        raise AnimationError("TOO_MANY_KEYS", "the result would hold more than %d keys" % MAX_KEYS)
