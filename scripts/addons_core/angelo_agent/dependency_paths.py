# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a driver variable's data path reads, step by step, for ``object_dependencies.walk`` (no bpy import).

A path is read in Blender's grammar: an identifier, then ``.identifier``,
``["key"]`` (a quoted key, backslash escapes included: ``["Shared [K5b]"]``)
or ``[n]`` steps. Every ID the path crosses is followed in order, a return to
an ID it already crossed included: ``["self"].rotation_euler`` on a camera
whose ``self`` property points back at it reads the camera's rotation (without
this, orbits were committed measured from cameras whose drivers read their
rotation that way and through keys holding ``]``).
A path that does not parse, or that resolves while a step of it cannot be
read, cannot be walked: the walk is then incomplete, never complete and
short.
"""

import re

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_INDEX = re.compile(r"\[-?\d+\]")
# A read that cannot feed an object's own rotation back into its position: its location (TRANSFORMS LOC_*,
# LOC_DIFF), or a value of its own that is no rotation and no matrix (SINGLE_PROP, never leaving it).
_ROTATION_STEPS = ("rotation_", "delta_rotation_", "matrix_")
INCOMPLETE = object()


def path_steps(path):
    """``[(step as written, is an index)]`` of a data path, or None when it does not parse."""
    steps, index, size = [], 0, len(path)
    while index < size:
        char = path[index]
        if char == "[" and path[index + 1:index + 2] in ('"', "'"):
            quote, end = path[index + 1], index + 2
            while end < size and path[end] != quote:
                end += 2 if path[end] == "\\" else 1
            if end + 1 >= size or path[end + 1] != "]":
                return None
            steps.append((path[index:end + 2], False))
            index = end + 2
        elif char == "[":
            match = _INDEX.match(path, index)
            if match is None:
                return None
            steps.append((match.group(0), True))
            index = match.end()
        else:
            dotted = char == "." and bool(steps)
            match = _IDENTIFIER.match(path, index + 1 if dotted else index)
            if match is None or not dotted and steps:
                return None
            steps.append((path[index:match.end()], False))
            index = match.end()
    return steps


def path_prefixes(path):
    """Every prefix of a data path that ends a step that is no index, the path itself last; None if it does not
    parse."""
    steps = path_steps(path or "")
    if steps is None:
        return None
    prefixes, built = [], ""
    for text, is_index in steps:
        built += text
        if not is_index:
            prefixes.append(built)
    return prefixes


def path_root(path):
    """A data path's first step (``render`` of ``render.resolution_x``, ``["dolly"]`` of ``["dolly"]``); ``*``
    when it has none or does not parse."""
    steps = path_steps(path or "")
    return steps[0][0] if steps else "*"


def time_setting(path):
    """Whether a scene-relative path reads the frame (or reads what cannot be named: ``*``)."""
    root = path_root(path)
    return root == "*" or root.startswith("frame_")


def overlaps(first, second):
    """Whether two data paths read or write the same value (one is the other or a step-prefix of it)."""
    if "*" in (first, second) or first == second:
        return True
    short, long = sorted((first, second), key=len)
    return long.startswith(short) and long[len(short)] in ".["


class Read:
    """The IDs a path crosses from its base, in order (repeats included); ``own``: the steps it reads in the
    first before it leaves it (all of them when it never does)."""

    __slots__ = ("blocks", "own")

    def __init__(self, blocks, own):
        self.blocks, self.own = blocks, own

    @property
    def within(self):
        return len(self.blocks) == 1


def _key(value):
    pointer = getattr(value, "as_pointer", None)
    return pointer() if callable(pointer) else id(value)


def read_ids(base, path):
    """What a driver path reads from ``base`` (an ID, or a struct of one: a view layer): a ``Read``; None when the
    path does not resolve (the driver reads nothing now); INCOMPLETE when it does not parse, or resolves while a
    step of it cannot be read.

    A step crosses into another ID when its value is an ID (a pointer, the base's own ID included) or belongs to
    another ID (``id_data``)."""
    first = getattr(base, "id_data", None) or base
    resolve = getattr(base, "path_resolve", None)
    steps = path_steps(path or "")
    if steps is None:
        return INCOMPLETE
    if not path or not callable(resolve):
        return Read([first], [text for text, _ in steps])
    try:
        resolve(path, False)
    except (ValueError, TypeError, AttributeError, KeyError, IndexError):
        return None
    blocks, current, own, built = [first], first, [], ""
    for text, is_index in steps:
        built += text
        if len(blocks) == 1:
            own.append(text)
        if is_index:
            continue
        try:
            value = resolve(built, False)
        except (ValueError, TypeError, AttributeError, KeyError, IndexError):
            return INCOMPLETE
        held = getattr(value, "id_data", None)
        if held is not None and (_key(held) == _key(value) or _key(held) != _key(current)):
            blocks.append(held)
            current = held
    return Read(blocks, own)


def reads_matrix(variable, target, read):
    """Whether a driver variable can read its target object's rotation or matrix (what can feed an object's
    rotation back into its position). Only these cannot: TRANSFORMS of a location channel, LOC_DIFF, and a
    SINGLE_PROP path whose steps in the target (``read.own``, up to where it leaves it) read no rotation and no
    matrix. Everything else does, and so does every object the path crosses after its target: a path that
    leaves and comes back (``["self"].rotation_euler``, ``data["owner"].matrix_world``) reads it again."""
    if variable.type == "LOC_DIFF":
        return False
    if variable.type == "TRANSFORMS":
        return not str(getattr(target, "transform_type", "")).startswith("LOC_")
    if variable.type == "SINGLE_PROP":
        return not read.own or any(text.lstrip(".").startswith(_ROTATION_STEPS) for text in read.own)
    return True


_PLACING_ROOTS = ("location", "rotation_euler", "rotation_quaternion", "rotation_axis_angle", "rotation_mode", "scale",
                  "delta_location", "delta_rotation_euler", "delta_rotation_quaternion", "delta_scale", "matrix_world",
                  "matrix_basis", "matrix_local", "matrix_parent_inverse", "parent")


def variable_roles(variable, target, path):
    """The roles a driver variable reads its target Object in: its placement, its pose or shape, or only its own
    properties (a custom property: its keys and drivers, never what instances or deforms it)."""
    from .object_dependencies import PLACEMENT, SHAPE
    if variable.type == "SINGLE_PROP":
        root = path_root(path)
        if root.startswith("["):
            return ()
        return (PLACEMENT,) if root in _PLACING_ROOTS else (PLACEMENT, SHAPE)
    return (PLACEMENT, SHAPE) if getattr(target, "bone_target", "") else (PLACEMENT,)
