# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Values behind the reviewed-script kit (script_kit): axes, vectors, finite numbers, JSON-safe and capped values, and
live data-blocks (a removed one is refused in Python, before Blender's own calls can crash on it)."""

import math

MAX_VALUES = 12         # Values listed of a measured or target list.


def _axis(axis, allowed):
    name = str(axis).upper()
    if name not in allowed:
        raise ValueError("kit: axis must be one of %s, not %r" % (", ".join(allowed), axis))
    return name


def _vector(values):
    from mathutils import Vector
    return Vector(tuple(float(v) for v in values))


def _live(item, kind, what):
    """``item`` when it is a data-block of ``kind`` still in the file; else ValueError. Blender's own calls
    (``user_map``, ``evaluated_get``, ``materials.append``) can crash on a removed one, where Python raises first."""
    if not isinstance(item, kind):
        raise ValueError("kit: %r is not a%s %s" % (item, "n" if what[0] in "aeiou" else "", what))
    try:
        item.name
    except ReferenceError:
        raise ValueError("kit: that %s was removed from the file" % what) from None
    return item


def _hide(cutter, hidden=True):
    """Hidden in the view layer and in renders (or shown in the view layer again): still evaluated, so a later move
    reaches its matrix."""
    try:
        cutter.hide_set(hidden)
    except RuntimeError:   # Not in the view layer: nothing shows it there.
        pass
    if hidden:
        cutter.hide_render = True


def _spent_still(bpy, cutter):
    """A spent cutter the script left alone: hidden, not rendered, and used by nothing but its collections, scene
    and children."""
    try:
        hidden = cutter.hide_get()
    except RuntimeError:
        hidden = True
    from .material_sharing import kept  # Data nothing keeps uses no cutter.
    users = [user for user in kept(bpy.data.user_map(subset=[cutter]).get(cutter, ()))
             if user != cutter and not isinstance(user, (bpy.types.Collection, bpy.types.Scene))
             and not (isinstance(user, bpy.types.Object) and user.parent == cutter)]
    return hidden and cutter.hide_render and not users


def _visible(obj):
    try:
        return obj.visible_get()   # Hidden itself, or in a hidden or excluded collection: not seen.
    except RuntimeError:   # Not in the view layer.
        return False


def _capped(value):
    """A long list of values: its first MAX_VALUES and how many more."""
    if isinstance(value, list) and len(value) > MAX_VALUES:
        return value[:MAX_VALUES] + ["+%d more" % (len(value) - MAX_VALUES)]
    return value


def _sequence(value):
    return not isinstance(value, (str, bytes)) and hasattr(value, "__len__") and hasattr(value, "__getitem__")


def _number(value):
    """A finite float, or None (missing, not a number, NaN or infinite)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _as_list(value):
    return [None if v is None else _number(v) for v in value] if _sequence(value) else [
        None if value is None else _number(value)]


def _rounded(value):
    """JSON-safe: numbers (numpy's included) rounded, NaN and infinity as None, text kept."""
    if _sequence(value):
        return [_rounded(v) for v in value]
    if value is None or isinstance(value, str):
        return value
    number = _number(value)
    return None if number is None else round(number, 4)
