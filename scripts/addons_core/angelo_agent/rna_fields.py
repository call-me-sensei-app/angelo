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
                 "PointCloud": frozenset({"points"}), "GreasePencilDrawing": frozenset({"curve_offsets"})}
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
