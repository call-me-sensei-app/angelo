# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Generic modifier and constraint types: their deny-list, evaluation bounds and settings.

A modifier or constraint type without a typed schema is written through its
installed RNA (``rna_rules``): settings are paths on the modifier or
constraint struct, object pointers ``{"object": handle}`` resolved through the
staged context (never the edited object itself), and every value is read back.
Nothing here reaches a datablock other than an object: textures, images, node
groups and caches stay reviewed Python.

Modifier types that read or write files, run simulations into caches, depend
on particle systems or volumes, or would need a geometry-nodes graph
(``geometry_nodes`` owns those) are denied. Every other type has an evaluated
topology bound: deforming types keep the vertex count, a few reduce it, and
the generating ones scale it by a factor read from their settings. A particle
system passes its mesh through; ``action_budget`` bounds its particles from its
settings (``particle_rules``), which ``particle_set`` writes.
"""

from .refusal_codes import RefusalError

from .rna_rules import (PropertyPathError, DENIED_STRUCTS, object_reference, plain, pointer_matches, same, resolve,
                        write, read)

TYPE_PATTERN = r"^[A-Z][A-Z_]{1,31}$"
# Files, caches, simulations, particles, volumes and graphs: never added or configured generically.
DENIED_MODIFIERS = frozenset({
    "NODES", "MESH_CACHE", "MESH_SEQUENCE_CACHE", "FLUID", "OCEAN", "PARTICLE_INSTANCE",
    "EXPLODE", "DYNAMIC_PAINT", "SURFACE", "MESH_TO_VOLUME", "VOLUME_TO_MESH", "VOLUME_DISPLACE"})
# Script constraints run Python (removed from current Blender; refused by name in any build).
DENIED_CONSTRAINTS = frozenset({"PYTHON", "SCRIPT"})
# Deforming and data-only types: the evaluated vertex count is the input's.
DEFORM_MODIFIERS = frozenset({
    "CAST", "HOOK", "LAPLACIANDEFORM", "LATTICE", "MESH_DEFORM", "SIMPLE_DEFORM", "SMOOTH", "CORRECTIVE_SMOOTH",
    "LAPLACIANSMOOTH", "SURFACE_DEFORM", "WARP", "WAVE", "ARMATURE", "NORMAL_EDIT", "WEIGHTED_NORMAL", "UV_PROJECT",
    "UV_WARP", "VERTEX_WEIGHT_EDIT", "VERTEX_WEIGHT_MIX", "VERTEX_WEIGHT_PROXIMITY", "DATA_TRANSFER", "DISPLACE",
    "CLOTH", "SOFT_BODY", "COLLISION", "DECIMATE", "WELD", "MASK", "BUILD", "PARTICLE_SYSTEM"})
# Generating types: a factor on the input bound from their own settings.
FACTOR_MODIFIERS = {
    "TRIANGULATE": lambda modifier: 2, "EDGE_SPLIT": lambda modifier: 2, "WIREFRAME": lambda modifier: 8,
    "SKIN": lambda modifier: 16,
    "MULTIRES": lambda modifier: 4 ** max(getattr(modifier, "levels", 0), getattr(modifier, "sculpt_levels", 0),
                                          getattr(modifier, "render_levels", 0), getattr(modifier, "total_levels", 0)),
}
# Types whose nested settings structs commit copies too.
NESTED_SETTINGS_TYPES = frozenset({"CLOTH", "SOFT_BODY", "COLLISION"})
MAX_SETTINGS = 64
TOO_MANY = "%d settings in one operation; at most %d: split them across operations on the same target"
MAX_CACHED_TYPES = 4096
_PATHS = {}  # (RNA type identifier, property count, limit) -> writable settings paths


def screen_settings(settings):
    """Raise for a denied settings path or a non-JSON value, before any RNA is consulted (pure)."""
    from .rna_rules import json_value, screen_path
    if not isinstance(settings, dict):
        return
    if len(settings) > MAX_SETTINGS:
        raise RefusalError(TOO_MANY % (len(settings), MAX_SETTINGS), code="LIMIT_EXCEEDED")
    for path, value in settings.items():
        screen_path(path, struct=True)  # A modifier's or constraint's own settings.
        json_value(value, path)


def check_type(kind, typed):
    """Refuse a modifier type the generic path never handles."""
    if kind in DENIED_MODIFIERS:
        raise RefusalError("MODIFIER_TYPE_DENIED: %s modifiers read or write files, caches, particles, volumes or "
                         "graphs; use geometry_nodes or reviewed Python" % kind, code="UNSUPPORTED")
    if kind in typed:
        raise RefusalError("%s has a typed schema; use its documented settings" % kind, code="VALIDATION")


def bound_factor(kind, modifier):
    """The evaluated-topology factor of a generic type, or raise when it has none."""
    if kind in DEFORM_MODIFIERS:
        return 1
    if kind in FACTOR_MODIFIERS:
        return FACTOR_MODIFIERS[kind](modifier)
    raise RefusalError("Existing modifier topology is outside the bounded native capability: " + kind, code="UNSUPPORTED")


def object_pointers(struct, depth=0):
    """Object pointers set on a modifier or constraint (and its nested settings structs)."""
    found = []
    for prop in getattr(getattr(struct, "bl_rna", None), "properties", ()):
        if prop.type != "POINTER" or prop.identifier in {"rna_type", "id_data"}:
            continue
        value = getattr(struct, prop.identifier, None)
        if value is None:
            continue
        if getattr(prop.fixed_type, "identifier", None) == "Object":
            found.append(value)
        elif prop.is_readonly and depth < 2 and not hasattr(value, "users") and value.bl_rna.identifier not in DENIED_STRUCTS:
            found.extend(object_pointers(value, depth + 1))
    return found


def foreign_ids(struct):
    """Names of datablock pointers other than objects that are set (textures, images, node groups...)."""
    names = []
    for prop in getattr(getattr(struct, "bl_rna", None), "properties", ()):
        if prop.type == "POINTER" and prop.identifier not in {"rna_type", "id_data"}:
            value = getattr(struct, prop.identifier, None)
            if value is not None and hasattr(value, "users") and getattr(prop.fixed_type, "identifier", None) != "Object":
                names.append(prop.identifier)
    return names


def apply(context, owner_obj, struct, settings, kind):
    """Write generic settings on a staged modifier or constraint; read every one back."""
    if len(settings) > MAX_SETTINGS:
        raise RefusalError(TOO_MANY % (len(settings), MAX_SETTINGS), code="LIMIT_EXCEEDED")

    def resolve_object(reference):
        target = context.resolve(reference)
        if target == owner_obj:
            raise PropertyPathError("OBJECT_TYPE_MISMATCH", kind, "a %s cannot point at its own object" % kind)
        return target

    resolved = {}
    for path, value in settings.items():
        holder, prop = resolve(struct, path, owner_kind="STRUCT")
        write(holder, prop, path, value, resolve_object)
        resolved[path] = (holder, prop)
    for path, value in settings.items():
        holder, prop = resolved[path]
        actual = read(holder, prop, lambda obj: _reference_of(context, obj))
        if not (same(value, actual) or (object_reference(value) is not None and actual == value)):
            raise PropertyPathError("VALUE_NOT_RETAINED", path, "read back %r after writing %r" % (actual, value))


def _reference_of(context, obj):
    for reference, staged in list(context.objects.items()) + list(context.aliases.items()):
        if staged == obj:
            return reference
    return None


def evaluate(struct, expected, tolerance, resolve_target):
    """(passed, observed) for generic settings values on a live or staged modifier or constraint."""
    observed, failed, unresolved = {}, [], []
    for path, value in expected.items():
        try:
            holder, prop = resolve(struct, path, owner_kind="STRUCT")
        except (PropertyPathError, AttributeError):
            failed.append(path)
            observed[path] = None
            continue
        actual = getattr(holder, prop.identifier)
        if prop.type == "POINTER":
            matched, missing = pointer_matches(actual, value, resolve_target)
            observed[path] = actual.name[:64] if actual is not None else None
            if missing:
                unresolved.append(path)
        else:
            actual = plain(actual)
            observed[path] = actual
            numeric = isinstance(value, (int, float)) and type(value) is not bool or (
                isinstance(value, list) and all(isinstance(item, (int, float)) and type(item) is not bool for item in value))
            if tolerance is not None and numeric:
                items = value if isinstance(value, list) else [value]
                got = actual if isinstance(actual, list) else [actual]
                matched = len(items) == len(got) and all(abs(a - b) <= tolerance for a, b in zip(got, items))
            else:
                matched = same(value, actual)
        if not matched:
            failed.append(path)
    result = {"values": observed}
    if failed:
        result["mismatched"] = failed
    if unresolved:
        result["unresolved"] = unresolved  # The expected value names no object (a name, or an undeclared alias).
    return not failed, result


def writable_paths(rna, limit):
    """``writable_settings`` paths of an RNA type, cached per type as in bone_state.

    The walk reads only the static definition (flags, fixed types), never an
    instance, so one walk serves every struct of the type. Revisions read every
    generic node, modifier and constraint once per object and pass, so without
    the cache a material shared by 2,000 objects repeated the same walk 2,000 times.
    """
    key = (rna.identifier, len(rna.properties), limit)
    paths = _PATHS.get(key)
    if paths is None:
        from .rna_rules import writable_settings
        if len(_PATHS) >= MAX_CACHED_TYPES:
            _PATHS.clear()
        paths = _PATHS[key] = tuple(entry["path"] for entry in writable_settings(rna, limit=limit))
    return paths


def values(struct, reference_of, limit=MAX_SETTINGS * 4):
    """Every natively writable setting's value (bounded), for revisions and checks."""
    result = {}
    if getattr(struct, "bl_rna", None) is None:
        return result  # A plain stand-in has no RNA definition.
    for path in writable_paths(struct.bl_rna, limit):
        try:
            holder, prop = resolve(struct, path, owner_kind="STRUCT")
        except (PropertyPathError, AttributeError):
            continue
        result[path] = plain(getattr(holder, prop.identifier), reference_of)
    return result
