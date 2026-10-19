# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which drivers Blender evaluates natively, so an object they drive can be bound, staged and committed.

A scripted driver runs a Python expression and a context property reads
whatever scene and view layer evaluate it (the stage's, while staging), so
neither may be evaluated by a stage, and a driver that is not native makes its
object unbindable (``animation_driver_expressions``). An
Average, Sum, Minimum or Maximum driver whose variables read transforms,
distances, rotation differences or a stored property evaluates natively, like
a constraint. Its definition, every variable and target, is part of the
object's revision (``describe``), so a changed driver fails the binding.
"""

import ast

NATIVE_TYPES = frozenset({"AVERAGE", "SUM", "MIN", "MAX"})
NATIVE_VARIABLES = frozenset({"TRANSFORMS", "LOC_DIFF", "ROTATION_DIFF", "SINGLE_PROP"})
_TARGET_FIELDS = ("data_path", "transform_type", "transform_space", "rotation_mode", "bone_target", "id_type")


def problem(curve):
    """None when Blender evaluates ``curve``'s driver natively, else why not (a short reason)."""
    driver = curve.driver
    if driver.type not in NATIVE_TYPES:
        return "a %s driver" % driver.type.lower()
    for variable in driver.variables:
        if variable.type not in NATIVE_VARIABLES:
            return "a %s driver variable" % variable.type.lower()
        if variable.type == "SINGLE_PROP" and _python_property(variable.targets[0]):
            return "a driver reading a Python-defined property"
    return None


def _python_property(target):
    """Whether a single-property variable reads anything a Python getter may compute (``is_runtime``) on its path, or
    a path that does not resolve (not proven native). Every step counts, so an index into a Python-defined vector
    is one (otherwise it reads as a stored custom property). A custom property
    (``["name"]`` on a struct, not a collection) is stored data: what follows it is too."""
    block, path = target.id, target.data_path
    if block is None or not path:
        return False  # Nothing to read.
    from .dependency_paths import path_steps
    steps = path_steps(path)
    if not steps:
        return True
    holder = block
    try:
        from bpy.types import bpy_prop_collection
    except ImportError:  # Off-host: nothing resolves, so nothing is proven.
        return True
    for text, numeric in steps:
        try:
            if numeric:  # An array element or a collection item by position.
                holder = holder[int(text[1:-1])]
            elif text.startswith("["):
                if not isinstance(holder, bpy_prop_collection):
                    return False  # A custom property: stored, with no getter.
                holder = holder[ast.literal_eval(text[1:-1])]
            else:
                name = text.lstrip(".")
                prop = holder.bl_rna.properties.get(name)
                if prop is None or getattr(prop, "is_runtime", True):
                    return True
                holder = getattr(holder, name)
        except (ValueError, TypeError, AttributeError, KeyError, IndexError, SyntaxError, ReferenceError):
            return True  # Unresolvable: not proven native.
    return False


def describe(curve, label):
    """The revision's account of one driver: its channel, type and every variable's targets (``label`` names)."""
    driver = curve.driver
    variables = []
    for variable in driver.variables:
        targets = []
        for target in variable.targets:
            entry = {field: str(getattr(target, field, "")) for field in _TARGET_FIELDS}
            entry["id"] = label(target.id.name) if target.id is not None else None
            targets.append(entry)
        variables.append({"name": label(variable.name), "type": variable.type, "targets": targets})
    return {"channel": label(curve.data_path, 256), "index": curve.array_index, "type": driver.type,
            "mute": bool(curve.mute), "valid": bool(curve.is_valid), "use_self": bool(getattr(driver, "use_self", False)),
            "variables": variables, "native": problem(curve) is None}
