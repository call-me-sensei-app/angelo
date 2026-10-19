# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Staged ``property_set`` kernel: generic, RNA-checked writes of settings.

Runs only on the program's staged copies: an object, its private data copy and
its private material clone, or the private stage scene and its private World
copy. Each path is resolved by ``rna_rules`` (deny-list, nested settings
structs, the three datablock hops), each value is checked against the
installed RNA definition (type, hard range, array length, enum items via RNA's
own assignment check, string length, object pointer), the values are written
in canonical order and all of them are read back, so a later write cannot
silently change an earlier one. A refused value raises ``PropertyPathError``
with a stable code; the program then fails and its stage is discarded.
"""

from .action_properties import PROPERTY_PATHS, RNA_TYPES, SCENE, owner_of, family, same, value_kind
from . import rna_rules
from .rna_rules import PropertyPathError


def resolve_owner(context, reference, values, shared=None):
    """The staged scene or object a canonical value set writes, with its written datablocks private.

    A material path writes the live material in place at commit, so a material
    another user shows is refused unless ``shared`` is ALL_USERS
    (``material_sharing.check_property_edit``).
    """
    first = next(iter(values))
    scene_handles = getattr(context, "scene_handles", frozenset())
    if owner_of(first) == SCENE:
        if reference not in scene_handles:
            raise PropertyPathError("OWNER_TYPE_MISMATCH", first, "a scene setting, not an object's: leave target out "
                                    "(or pass scene_settings.handle from the observation)")
        if any(family(path) == "world" for path in values):
            if context.scene.world is None:
                raise PropertyPathError("PROPERTY_MISSING_IN_BUILD", first, "the scene has no World")
            context.require_private(context.scene.world)
        return context.scene
    if reference in scene_handles:
        raise PropertyPathError("OWNER_TYPE_MISMATCH", first, "object settings need an object handle as target")
    obj = context.resolve(reference)
    checked = False
    for path in values:
        hop = {"data": "data", "material": "active_material"}.get(family(path))
        if hop is None:
            continue
        block = getattr(obj, hop, None)
        if block is None:
            raise PropertyPathError("PROPERTY_MISSING_IN_BUILD", path, "the object has no " + hop)
        if hop == "active_material" and not checked:
            # Before the private-copy rule: a linked or shared material gets its own coded refusal.
            from .material_sharing import check_property_edit
            check_property_edit(context, reference, block, path, shared)
            checked = True
        context.require_private(block)
    return obj


def check_rna(holder, name, path, value):
    """Refuse a value the installed RNA definition would reject or clamp; return the property."""
    prop = holder.bl_rna.properties.get(name)
    if prop is None:
        raise PropertyPathError("PROPERTY_MISSING_IN_BUILD", path, "this Blender build has no such setting")
    kind = value_kind(path)
    if kind is not None and prop.type != RNA_TYPES[kind]:
        raise PropertyPathError("RNA_TYPE_MISMATCH", path, "RNA type %s, expected %s" % (prop.type, RNA_TYPES[kind]))
    if prop.type == "ENUM" and getattr(prop, "is_enum_flag", False) and kind == "enum":
        raise PropertyPathError("RNA_TYPE_MISMATCH", path, "flag enums are not supported here")
    problem = rna_rules.leaf_problem(holder, prop, path)
    if problem:
        raise PropertyPathError(*problem)
    rna_rules.check_value(prop, path, _object_value(path, value))
    return prop


def _object_value(path, value):
    """The active camera keeps its plain-handle form; every other object pointer is {"object": handle}."""
    return {"object": value} if value_kind(path) == "object" and isinstance(value, str) else value


def _reference_of(context, obj):
    """The program reference (handle or $alias) of a staged object, or None."""
    if obj is None:
        return None
    for reference, staged in list(getattr(context, "objects", {}).items()) + list(getattr(context, "aliases", {}).items()):
        if staged == obj:
            return reference
    return None


def read(context, owner, path):
    """The JSON value of one setting; object pointers as program references (the camera as a plain one)."""
    holder, prop = rna_rules.resolve(owner, path)
    value = getattr(holder, prop.identifier)
    if prop.type == "POINTER":
        reference = _reference_of(context, value) if value is not None else None
        return reference if value_kind(path) == "object" or reference is None else {"object": reference}
    return rna_rules.plain(value)


def _write(context, owner, path, value):
    holder, prop = rna_rules.resolve(owner, path)
    check_rna(holder, prop.identifier, path, value)
    required = PROPERTY_PATHS.get(path, (None, None, None, None))[3] if value_kind(path) == "object" else None

    def resolve_object(reference):
        target = context.resolve(reference)
        if required and target.type != required:
            raise PropertyPathError("OBJECT_TYPE_MISMATCH", path, "needs a %s object, not %s" % (required, target.type))
        return target

    rna_rules.write(holder, prop, path, _object_value(path, value), resolve_object)


def apply_property_set(context, operation):
    """Execute one canonical property_set on its staged owner; return before/after read-back."""
    values = operation["parameters"]["values"]
    owner = resolve_owner(context, operation["target"], values, operation["parameters"].get("shared"))
    before = {path: read(context, owner, path) for path in values}
    for path, value in values.items():  # Canonical order: common paths first.
        _write(context, owner, path, value)
    changes = []
    for path, value in values.items():
        after = read(context, owner, path)
        if not same(path, value, after):
            raise PropertyPathError("VALUE_NOT_RETAINED", path, "read back %r after writing %r" % (after, value))
        changes.append({"path": path, "before": before[path], "after": after})
    result = {"kind": "property_set", "target": operation["target"], "owner": owner_of(next(iter(values))),
              "changes": changes}
    if result["owner"] != SCENE:
        result.update(object=owner.name, object_type=owner.type)
    return result
