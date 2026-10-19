# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whole-curve key tables and the extended ``keyframe_configure`` kernel.

A ``KeyTable`` reads every keyframe attribute of one F-Curve in bulk
(``foreach_get``; enums as their RNA integer values) and writes a curve back
whole: clear, add and ``foreach_set``. Removing keys one by one is quadratic in
Blender (each removal shifts the array), so kernels that drop or reorder keys
build the kept rows and rewrite the curve once. Only staged private Actions
reach these functions (``anim_channels``).
"""

import numpy as np

from .anim_channels import (AnimationError, edit_curves, require_frames, selected_points, in_selection,
                            FRAME_EPSILON)
from .action_context import _readback

VECTORS = ("co", "handle_left", "handle_right")
INTEGERS = ("interpolation", "easing", "handle_left_type", "handle_right_type", "type",
            "select_control_point", "select_left_handle", "select_right_handle")
FLOATS = ("back", "amplitude", "period")


class KeyTable:
    """Every key of one F-Curve as numpy columns, in curve order."""

    def __init__(self, columns):
        self.columns = columns

    @classmethod
    def read(cls, curve):
        points, count = curve.keyframe_points, len(curve.keyframe_points)
        columns = {}
        for name in VECTORS:
            values = np.empty(count * 2, dtype=np.float32)
            points.foreach_get(name, values)
            columns[name] = values.reshape(count, 2).astype(np.float64)
        for name in INTEGERS:
            values = np.empty(count, dtype=np.int32)
            points.foreach_get(name, values)
            columns[name] = values
        for name in FLOATS:
            values = np.empty(count, dtype=np.float32)
            points.foreach_get(name, values)
            columns[name] = values
        return cls(columns)

    def __len__(self):
        return len(self.columns["co"])

    def take(self, indexes):
        indexes = np.asarray(indexes, dtype=np.int64)
        return KeyTable({name: values[indexes] for name, values in self.columns.items()})

    @staticmethod
    def concatenate(tables):
        tables = [table for table in tables if len(table)]
        if not tables:
            return None
        return KeyTable({name: np.concatenate([table.columns[name] for table in tables]) for name in tables[0].columns})

    def frames(self):
        return self.columns["co"][:, 0]

    def write(self, curve):
        """Replace every key of ``curve`` with these rows, sorted, then recalculate handles."""
        points, count = curve.keyframe_points, len(self)
        points.clear()
        if count:
            points.add(count)
            for name in VECTORS:
                points.foreach_set(name, self.columns[name].astype(np.float32).ravel())
            for name in INTEGERS:
                points.foreach_set(name, self.columns[name].astype(np.int32))
            for name in FLOATS:
                points.foreach_set(name, self.columns[name].astype(np.float32))
            points.sort()
            points.deduplicate()
            points.handles_recalc()
        curve.update()


def configure(context, obj, operation):
    """keyframe_configure: key settings of the selected keys, extrapolation and mute of the selected channels."""
    params = operation["parameters"]
    _owner, curves = edit_curves(context, obj, operation["target"], params)
    require_frames(curves, params["frames"], params["property"])
    keys = channels = 0
    for curve in curves:
        for point in selected_points(curve, params["frames"], params["frame_range"]):
            if params["interpolation"] is not None:
                point.interpolation = params["interpolation"]
            if params["easing"] is not None:
                point.easing = params["easing"]
            if params["handle_type"] is not None:
                point.handle_left_type = point.handle_right_type = params["handle_type"]
            if params["key_type"] is not None:
                point.type = params["key_type"]
            keys += 1
        if params["extrapolation"] is not None or params["mute"] is not None:
            if params["extrapolation"] is not None:
                curve.extrapolation = params["extrapolation"]
            if params["mute"] is not None:
                curve.mute = params["mute"]
            channels += 1
        curve.update()
    if not keys and any(params[key] is not None for key in ("interpolation", "easing", "handle_type", "key_type")):
        raise AnimationError("KEY_NOT_FOUND", "no keys of the selected channels lie in the frame range")
    return _readback(obj, operation, configured_keys=keys, configured_channels=channels,
                     channels=_channel_rows(curves))


def _channel_rows(curves, limit=16):
    return [[curve.data_path[:128], curve.array_index, len(curve.keyframe_points)] for curve in curves[:limit]]


def key_rows(table, frames, frame_range):
    """Row indexes of ``table`` selected by frames or a frame range (every row when neither)."""
    return [index for index, frame in enumerate(table.frames()) if in_selection(frame, frames, frame_range)]


def moved_merge(table, moved, unmoved):
    """Moved rows replace unmoved rows they land on (within the frame epsilon), as a transform merges keys.

    O((n + m) log m): each unmoved frame is compared with its nearest landing frame only.
    """
    if not len(moved):
        return table.take(unmoved)
    landing = np.sort(moved.frames())
    frames = table.columns["co"][np.asarray(unmoved, dtype=np.int64), 0] if len(unmoved) else np.empty(0)
    slots = np.searchsorted(landing, frames)
    nearest = np.minimum(np.abs(landing[np.clip(slots, 0, len(landing) - 1)] - frames),
                         np.abs(landing[np.clip(slots - 1, 0, len(landing) - 1)] - frames))
    keep = [index for index, distance in zip(unmoved, nearest) if distance >= FRAME_EPSILON]
    return KeyTable.concatenate([table.take(keep), moved]) or table.take([])
