# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``keyframe_copy``: keys of a declared source object's channels pasted onto the staged target.

The explicit form of the Graph Editor's copy and paste: no clipboard, no
selection. The source is a declared read dependency whose staged Action is
only read; the target's private Action is written (``anim_channels`` guard),
created and named after the live object when it has none. Each source
channel goes to the target channel with the same data path and index, created
in the target's slot (and its group) when absent; a path the target cannot
animate (a bone it lacks) is refused. Keys keep every setting, shifted by
``offset`` frames; pasted keys are unselected. ``MERGE`` replaces target keys
on the pasted frames, ``REPLACE_RANGE`` every target key inside the pasted
span, ``REPLACE`` every key of each target channel. Source and target may be
the same object (a duplicate in time): the source keys are read first.
"""

import numpy as np

from .anim_channels import (AnimationError, MAX_KEYS, FRAME_EPSILON, channelbag, guard, select_curves)
from .anim_keys import KeyTable, key_rows, moved_merge
from .action_context import _readback
from .program_names import name_new_slot


def _target_action(context, obj, target):
    import bpy
    animation = obj.animation_data or obj.animation_data_create()
    if len(animation.drivers) or animation.use_tweak_mode:
        raise AnimationError("DRIVEN" if len(animation.drivers) else "TWEAK_MODE",
                             "animation with drivers or in NLA tweak mode needs reviewed Python")
    if animation.action is None:
        live = context.extras.get("live_names", {}).get(target, obj.name)
        action = context.track_id(bpy.data.actions.new(live + "Action"))
        name_new_slot(context.extras, obj, target)  # The slot the first channel creates takes the live name.
        animation.action = action
        return action
    guard(context, target, "object")
    return context.require_private(animation.action)


def _animatable(obj, path, index):
    head, _, name = path.rpartition(".")
    try:
        holder = obj.path_resolve(head) if head else obj
        if name.startswith('["'):  # A custom property: numeric, and present on the target.
            value = holder[name[2:-2]]
            return isinstance(value, (int, float)) and not isinstance(value, bool) and index == 0
        prop = holder.bl_rna.properties[name]
    except (ValueError, KeyError, AttributeError, TypeError):
        return False
    length = int(getattr(prop, "array_length", 0) or 0)
    return bool(prop.is_animatable) and prop.type in {"BOOLEAN", "INT", "FLOAT", "ENUM"} and (
        index == 0 if not length else index < length)


def apply(context, obj, operation):
    """keyframe_copy onto the staged target ``obj``."""
    params, target = operation["parameters"], operation["target"]
    source = context.resolve(params["source"])
    source_animation = source.animation_data
    if source_animation is not None and len(source_animation.drivers):
        raise AnimationError("DRIVEN", "the source's animation has drivers; copy it with reviewed Python")
    curves = select_curves(channelbag(source), params["property"], params["index"])
    if not curves:
        raise AnimationError("CHANNEL_NOT_FOUND", "the source has no animated %s channel" % (params["property"] or "object"))
    pasted = []
    for curve in curves:  # Read every source key before the target (possibly the same Action) changes.
        table = KeyTable.read(curve)
        rows = key_rows(table, (), params["frame_range"])
        if rows:
            pasted.append((curve.data_path, curve.array_index, curve.group.name if curve.group else None,
                           table.take(rows)))
    if not pasted:
        raise AnimationError("KEY_NOT_FOUND", "the source channels have no keys in the frame range")
    if sum(len(rows) for *_, rows in pasted) > MAX_KEYS:
        raise AnimationError("TOO_MANY_KEYS", "at most %d keys per copy" % MAX_KEYS)
    for path, index, _group, _rows in pasted:
        if not _animatable(obj, path, index):
            raise AnimationError("CHANNEL_MISMATCH", "the target cannot animate %s[%d]" % (path[:128], index))
    action = _target_action(context, obj, target)
    bag = channelbag(obj, ensure=False)
    written = 0
    for path, index, group, rows in pasted:
        curve = bag.fcurves.find(path, index=index) if bag is not None else None
        if curve is None:
            curve = action.fcurve_ensure_for_datablock(obj, path, index=index, **({"group_name": group} if group else {}))
            bag = channelbag(obj)
        columns = rows.columns
        for name in ("co", "handle_left", "handle_right"):
            columns[name][:, 0] += params["offset"]
        for name in ("select_control_point", "select_left_handle", "select_right_handle"):
            columns[name][:] = 0
        existing = KeyTable.read(curve)
        frames = existing.frames()
        if params["mode"] == "REPLACE":
            kept = []
        elif params["mode"] == "REPLACE_RANGE":
            low, high = rows.frames().min(), rows.frames().max()
            kept = [i for i, frame in enumerate(frames) if not low - FRAME_EPSILON <= frame <= high + FRAME_EPSILON]
        else:
            kept = list(range(len(existing)))
        if len(existing) + len(rows) > MAX_KEYS:
            raise AnimationError("TOO_MANY_KEYS", "the target channel would hold more than %d keys" % MAX_KEYS)
        moved_merge(existing, rows, kept).write(curve)
        written += len(rows)
    return _readback(obj, operation, source=params["source"], pasted_keys=written, channels=len(pasted),
                     frames=[float(np.min([rows.frames().min() for *_, rows in pasted])),
                             float(np.max([rows.frames().max() for *_, rows in pasted]))])
