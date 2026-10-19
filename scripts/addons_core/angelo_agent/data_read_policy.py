# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where ``angelo_read_data`` may go: the open document's own data, and nothing else.

A path is walked one RNA property at a time. Every struct it reaches, through
a pointer or as a collection item, is admitted here first:

* it belongs to a datablock (``id_data``) of the open document, so RNA's own
  type descriptions, the preferences and anything without an owner are out;
* that datablock is original data, never an evaluated copy;
* its type is not interface or script data (screens, window managers,
  workspaces, text blocks) and not a library, which ``angelo_blend_file`` reports;
* a scene is the bound scene: other scenes are not read (the agent edits one
  scene, and the scene reads never report another one);
* the struct is not a dependency graph, a preview image or packed file bytes;
* a datablock whose values come from an external file that a read could load
  (movie clips, sounds, volumes, cache files) shows its identity only;
* the elements of a geometry attribute (``Attribute.data``) are not entered:
  fetching one makes Blender rewrite an attribute stored as a single value
  into an array (measured: ``storage_type`` SINGLE becomes ARRAY), which is a
  change to the mesh. Its length, type, domain and storage stay readable.

``refusal`` returns a stable code and one sentence over detached Node/owner
facts. It never reads a wrapper, compares RNA values or reaches ``bpy``.
"""

from .data_read_values import struct_names

# ``bpy.data`` collections a path may not enter, and what to use instead.
DATA_DENIED = {
    "scenes": "Other scenes are not read; the bound scene is the root scene.",
    "screens": "Interface layout is not scene data.",
    "window_managers": "Interface state is not scene data.",
    "workspaces": "Interface layout is not scene data.",
    "texts": "Text blocks hold scripts, which are never read here.",
    "libraries": "Linked libraries are reported by angelo_blend_file.",
    "all_ids": "Read one datablock type, for example data.objects or data.node_groups.",
}
DENIED_ID_TYPES = {
    "SCREEN": "Interface layout is not scene data.",
    "WINDOWMANAGER": "Interface state is not scene data.",
    "WORKSPACE": "Interface layout is not scene data.",
    "TEXT": "Text blocks hold scripts, which are never read here.",
    "LIBRARY": "Linked libraries are reported by angelo_blend_file.",
}
# Structs that are not document data, by RNA identifier (a struct deriving from one is refused too).
DENIED_STRUCTS = {
    "Depsgraph": "The dependency graph holds evaluated copies, not the document's data.",
    "DepsgraphObjectInstance": "The dependency graph holds evaluated copies, not the document's data.",
    "DepsgraphUpdate": "The dependency graph holds evaluated copies, not the document's data.",
    "Struct": "RNA type descriptions are documentation; use angelo_blender_api.",
    "Property": "RNA type descriptions are documentation; use angelo_blender_api.",
    "Function": "RNA type descriptions are documentation; use angelo_blender_api.",
    "ImagePreview": "Preview images are pixels, not settings.",
    "PackedFile": "Packed file bytes are not read.",
    "TextLine": "Text blocks hold scripts, which are never read here.",
}
# Properties never followed or listed on any struct: RNA's description of the type itself.
DENIED_PROPERTIES = frozenset({"rna_type", "bl_rna"})
# Datablocks read by identity only: more could load their external file.
FILE_BACKED = frozenset({"MOVIECLIP", "SOUND", "VOLUME", "CACHEFILE"})
FILE_BACKED_REASON = "This datablock's values come from an external file; only its identity is read."
IDENTITY_PROPERTIES = frozenset({"name", "name_full", "id_type", "users", "use_fake_user", "is_missing",
                                 "is_library_indirect", "is_embedded_data", "library"})
# Collections whose items are never fetched, as {struct or base: {property: why}}: the fetch itself changes the data.
REWRITING_COLLECTIONS = {"Attribute": {"data": (
    "An attribute's elements are not read: fetching one makes Blender rewrite a single-value attribute as an array. "
    "Its length, data_type, domain and storage_type are readable; element values are on vertices, edges, polygons "
    "and loops.")}}


def owner_of(value):
    """Copied owner facts, never an RNA owner."""
    return value.owner if value.owner.exists else None


def is_id(value):
    return value.is_id


_NODE = []  # data_read_access.Node, imported on first use (it imports this module).


def refusal(value, scene=None):
    """Policy over detached node facts. Native owns bound-scene comparison."""
    if not _NODE:
        from .data_read_access import Node
        _NODE.append(Node)
    if type(value) is not _NODE[0]:
        return "NOT_DATA", "This is not a data struct of the document."
    if value.is_data_root:
        return None
    for name in struct_names(value.type):
        if name in DENIED_STRUCTS:
            return "DENIED", DENIED_STRUCTS[name]
    owner = value.owner
    if not owner.exists:
        return "OUT_OF_DOCUMENT", "This belongs to no datablock of the open document, so it is not read."
    kind = owner.id_type
    if kind in DENIED_ID_TYPES:
        return "DENIED", DENIED_ID_TYPES[kind]
    if not owner.is_original:
        return "DENIED", "Evaluated copies are not read; read the original datablock."
    if kind == "SCENE" and not owner.is_bound_scene:
        return "OTHER_SCENE", "Other scenes are not read; the bound scene is the root scene."
    if kind in FILE_BACKED and not owner.is_self:
        return "DENIED", FILE_BACKED_REASON
    return None


def collection_refusal(names, identifier):
    """Why the items of collection ``identifier`` on a struct with RNA ``names`` are not fetched, or None."""
    for name in names:
        reason = REWRITING_COLLECTIONS.get(name, {}).get(identifier)
        if reason:
            return reason
    return None


def identity_only(value):
    """Whether only the identity properties of this datablock are read (its file could be loaded)."""
    return is_id(value) and value.owner.id_type in FILE_BACKED
