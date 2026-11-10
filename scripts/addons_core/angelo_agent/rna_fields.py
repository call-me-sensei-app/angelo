# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which RNA declarations the exact walkers read: the fields they skip, and the pointers and collections a
dependency link walk follows (``program_dependencies._walk_links``, custom-link admission).

Standard library only; the declarations come from the ``bl_rna`` passed in.
"""

_RNA_SKIP = frozenset({"rna_type", "users", "users_collection", "users_scene", "original", "id_data", "library",
                       "override_library", "asset_data", "preview", "is_evaluated", "is_runtime_data",
                       "is_updated", "is_updated_data", "is_updated_transform", "tag", "session_uid",
                       "is_embedded_data", "is_missing", "is_library_indirect", "is_editable", "is_editmode",
                       "dimensions", "bound_box", "matrix_world", "matrix_local", "dimensions_max",
                       "depsgraph", "object_instances", "evaluated_geometry", "storage_type",
                       # A point cache's status text, rewritten by playback; not a setting.
                       "info",
                       # Hashed explicitly by _fingerprint with its per-assignment charge.
                       "angelo_vertex_group_weights"})


# Per-element views whose items declare no pointer or collection (so hold no ID link and no ID property),
# by owning struct; every Attribute's ``data`` too. Iterating them visited each point of hair or a drawing.
ELEMENT_VIEWS = {"Curves": frozenset({"curves", "points", "position_data", "curve_offset_data", "normals"}),
                 "PointCloud": frozenset({"points"}), "GreasePencilDrawing": frozenset({"curve_offsets"}),
                 # An F-Curve's keys and samples (Keyframe and FCurveSample declare only scalars): a baked take
                 # holds millions, which a link walk would otherwise visit and charge one by one.
                 "FCurve": frozenset({"keyframe_points", "sampled_points"}),
                 # A shape key's points (ShapeKeyPoint, ShapeKeyBezierPoint, ShapeKeyCurvePoint: declared only as
                 # UnknownType, values only): a dense mesh's shape keys hold millions.
                 "ShapeKey": frozenset({"data"})}
_VIEW_NAMES = frozenset().union(*ELEMENT_VIEWS.values(), {"data"})


def _element_view(rna, name):
    """Whether collection ``name`` of struct ``rna`` is an element view (read only for the few view names)."""
    if name in ELEMENT_VIEWS.get(getattr(rna, "identifier", None), ()):
        return True
    base = rna if name == "data" else None
    while base is not None:  # FloatAttribute -> Attribute: an RNA struct's declared base chain.
        if getattr(base, "identifier", None) == "Attribute":
            return True
        base = getattr(base, "base", None)
    return False


def _collection_kind(prop):
    """COLLECTION, or IDS: a collection whose declared item type is an ID (a curve's materials)."""
    base = getattr(prop, "fixed_type", None)  # Every RNA collection declares one; host stand-ins may not.
    while base is not None and base.identifier != "ID":
        base = base.base
    return "COLLECTION" if base is None else "IDS"


def link_fields(rna):
    """(identifier, kind, property) of every pointer and collection a link walk reads, in RNA order.

    Only these declarations can contain dependency edges. Scalar/array values
    are checked by their existing owners; fetching them in a link walk adds
    work (and getter calls) without yielding links. Nor can element views
    (``test_blender_borrowed_materials_closure`` proves their item types are leaves).
    """
    fields = []
    for prop in rna.properties:
        name, kind = prop.identifier, prop.type
        if name in _RNA_SKIP or kind not in ("POINTER", "COLLECTION"):
            continue
        if kind == "COLLECTION":
            if name in _VIEW_NAMES and _element_view(rna, name):
                continue
            kind = _collection_kind(prop)
        fields.append((name, kind, prop))
    return tuple(fields)


_PLACEHOLDER_TYPES = frozenset({"UnknownType", "AnyType"})


def link_free_classifier(types, subtyped=None):
    """``free(prop)``: whether collection ``prop``'s items can hold no ID link at any depth, from the declarations.

    An item type is link free when no registered RNA type derives from it (a collection item is its refined type, so
    a base without pointers says nothing about its subtypes, e.g. Modifier) and every property it declares, outside
    ``_RNA_SKIP``, is a value or a collection of link-free items. A particle's hair keys are; a type in a cycle is
    not (unproven). A link walk then never iterates them; they still yield every value to the fingerprint, and
    custom-property admission still reads their elements.

    ``subtyped(identifier)``: whether any registered type derives from it; by default ``types`` (``bpy.types``) is
    scanned once for this classifier. A placeholder item type (``UnknownType``, ``AnyType``: the items' real types
    are not declared, as for a shape key's points) is never proven free.
    """
    if subtyped is None:
        bases = set()
        for name in dir(types):
            try:
                rna = getattr(getattr(types, name), "bl_rna", None)
            except Exception:  # A type that cannot be built is not a base of anything readable here.
                continue
            base = getattr(rna, "base", None)
            while base is not None:
                bases.add(base.identifier)
                base = base.base
        subtyped = bases.__contains__
    memo = {}

    def free_type(rna):
        identifier = rna.identifier
        if identifier in _PLACEHOLDER_TYPES:
            return False
        if identifier not in memo:
            memo[identifier] = False  # In progress: a cycle is not proven free.
            memo[identifier] = not subtyped(identifier) and all(
                prop.identifier in _RNA_SKIP or prop.type not in ("POINTER", "COLLECTION")
                or (prop.type == "COLLECTION" and getattr(prop, "fixed_type", None) is not None
                    and _collection_kind(prop) == "COLLECTION" and free_type(prop.fixed_type))
                for prop in rna.properties)
        return memo[identifier]

    def free(prop):
        fixed = getattr(prop, "fixed_type", None)
        return fixed is not None and free_type(fixed)

    return free


def python_subtyped(types, primed):
    """``subtyped`` for link_free_classifier from the Python classes of ``types`` (``bpy.types``): any subclass of an
    identifier's class. ``bpy.types`` builds a type's class on first access, so the first call per process (``primed``
    a one-slot list) touches every name. A class Python registers later is a subclass at once; a struct C creates at
    run time (generated node or operator property structs) has its class only once accessed, and none of those is a
    collection item type of scene data. An unknown identifier counts as subtyped (not proven free)."""
    if not primed[0]:
        for name in dir(types):
            try:
                getattr(types, name)
            except Exception:  # A type that cannot be built derives from nothing readable here.
                continue
        primed[0] = True

    def subtyped(identifier):
        cls = getattr(types, identifier, None)
        if not isinstance(cls, type):
            return True
        return bool(type.__subclasses__(cls))

    return subtyped
