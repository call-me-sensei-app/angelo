# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fail-closed admission for source links not supported by private graph cloning.

Numeric/string ID properties are copied normally. ID-valued custom properties,
including nested groups and modifier/bone properties, are not RNA pointer links:
the staged clone/remap contract cannot currently preserve them. Reject BEFORE
creating Main IDs or serializing a bundle, rather than importing an undeclared
artist object or silently clearing its pointer during cleanup.
"""

from . import exact_fence
from .program_jobs import ProgramError
from .program_dependencies import (_bpy, _embedded_links, _mutable_dependencies, link_fields, MAX_FINGERPRINT_ITEMS,
                                   MAX_STAGE_IDS)


def assert_supported_custom_links(block):
    """Inspect serializable resources and embedded RNA, no evaluated geometry.

    Read-only resources such as images can also carry custom ID references that
    libraries.write follows. Inspect those resources too, without traversing
    other scene/collection/object graphs: each declared object is its own root.
    Inside an exact fence a root passes once (exact_fence); a refusal is never
    remembered.
    """
    exact_fence.check("custom_links", (block.as_pointer(),), lambda: _inspect_custom_links(block))


def _inspect_custom_links(block):
    with exact_fence.fence():  # Reads only: one table of each type's declarations for the walk.
        _walk_custom_links(block)


def _walk_custom_links(block):
    bpy = _bpy()
    budget, seen, ids, queue = [MAX_FINGERPRINT_ITEMS], set(), set(), [(block, 0)]

    def enqueue(child, depth):
        if child is None or not hasattr(child, "bl_rna"):
            return
        if isinstance(child, (bpy.types.Object, bpy.types.Scene, bpy.types.Collection)):
            return
        queue.append((child, depth))

    def check_value(value, depth):
        budget[0] -= 1
        if budget[0] < 0 or depth > 16:
            raise ProgramError("Custom property links exceed bounded native admission limits", code='LIMIT_EXCEEDED')
        if isinstance(value, bpy.types.ID):
            raise ProgramError("ID-valued custom properties are not supported by isolated native programs; "
                               "remove the link or use a supported declared RNA dependency",
                               code='UNSUPPORTED')
        if hasattr(value, "to_dict"):
            value = value.to_dict()
        elif hasattr(value, "to_list"):
            value = value.to_list()
        if isinstance(value, dict):
            for item in value.values():
                check_value(item, depth + 1)
        elif isinstance(value, (list, tuple)):
            for item in value:
                check_value(item, depth + 1)

    while queue:
        holder, depth = queue.pop()
        pointer = int(holder.as_pointer())
        if pointer in seen:
            continue
        seen.add(pointer)
        if isinstance(holder, bpy.types.ID):
            ids.add(pointer)
            if len(ids) > MAX_STAGE_IDS:
                raise ProgramError("Custom link inspection exceeds the bounded dependency ID limit", code='LIMIT_EXCEEDED')
        budget[0] -= 1
        if budget[0] < 0 or depth > 16:
            raise ProgramError("Embedded settings exceed bounded native admission limits", code='LIMIT_EXCEEDED')
        try:
            keys = list(holder.keys())
        except (AttributeError, TypeError):
            keys = []  # Many non-ID RNA structs do not support ID properties.
        for key in keys:
            check_value(holder[key], 0)
        mesh = isinstance(holder, bpy.types.Mesh)
        for name, kind, _prop in exact_fence.table("links", holder.bl_rna, link_fields):
            if kind == "POINTER":
                enqueue(getattr(holder, name, None), depth + 1)
            elif kind in ("COLLECTION", "IDS"):  # IDS: a collection of IDs (rna_fields.link_fields).
                if mesh and name != "materials":
                    continue  # Packed mesh topology contains no custom ID links.
                for child in getattr(holder, name):
                    enqueue(child, depth + 1)


def admit_input(obj, limits):
    """The checks every bound input object passes before its revision is compared (BlenderProgramBackend.bind).

    ``limits``: (MAX_VERTICES, MAX_EDGES, MAX_FACES). Returns the mesh vertex count it adds to the budget.
    """
    if obj.mode != "OBJECT":
        raise ProgramError("Switch all declared program inputs to Object Mode before staging", code='UNSUPPORTED')
    if obj.library or not obj.is_editable:
        raise ProgramError("Program write targets must be local editable objects", code='UNSUPPORTED')
    if obj.instance_collection:
        raise ProgramError("Collection instances require an explicitly reviewed preparation; they are not staged implicitly",
                           code='UNSUPPORTED')
    vertices = 0
    if obj.type == "MESH":
        from .action_schema_types import mesh_over_limits
        over = mesh_over_limits(obj.data, limits)
        if over is not None:
            from .action_schema_types import OVER_LIMIT_NOTE
            raise ProgramError("%r is over the typed programs' mesh limits (%s); %s, its material included"
                               % (obj.name[:64], over, OVER_LIMIT_NOTE), code='LIMIT_EXCEEDED')
        vertices = len(obj.data.vertices)
    assert_supported_custom_links(obj)
    return vertices


def undeclared_dependencies(objects):
    """Objects the inputs point at (parents, constraint, modifier and driver targets, camera focus) that
    are not inputs themselves, once each in a stable order."""
    pointers = {obj.as_pointer() for obj in objects}
    found = {}
    for obj in objects:
        for block in _mutable_dependencies(obj):
            for _, _, dependency in _embedded_links(block):
                if isinstance(dependency, _bpy().types.Object) and dependency.as_pointer() not in pointers:
                    found.setdefault(dependency.as_pointer(), dependency)
    return list(found.values())


def observed_binding(controller, obj, scene=None):
    """(handle, revision) the context's latest observation showed for an undeclared dependency, or a refusal.

    The same rule as inferred bindings (identifiers): never the live revision, so a dependency that
    changed since the model looked fails closed when bind compares it. A dependency outside the bound
    ``scene`` has no revision any lookup returns (``binding_refusals.OUTSIDE_SCENE``).
    """
    from .binding_refusals import OUTSIDE_SCENE
    from .scene_queries import object_handle
    handle = object_handle(obj)
    table = getattr(controller, "_identifiers", None)
    revision = table.observed(handle) if table is not None else None
    if revision is None and scene is not None and scene not in tuple(getattr(obj, "users_scene", ()) or ()):
        raise ProgramError(OUTSIDE_SCENE % obj.name[:64], code='VALIDATION')
    if revision is None:
        why = (table.why_missing(handle) if table is not None
               else "%s has no revision in your latest observation" % handle)
        raise ProgramError("Declare every existing parent, constraint, modifier, driver and camera focus object "
                           "dependency in the read set (%r: %s)" % (obj.name[:64], why), code='VALIDATION')
    return handle, revision


def read_revision(window, obj, revision):
    """An input's exact current object revision from the store's reader (or scene_queries); only a complete
    observation authorizes a program (native_programs.BlenderProgramBackend._revision)."""
    if revision is not None:
        try:
            value = revision(window, obj)
        except TypeError:
            value = revision(obj)
    else:
        from . import scene_queries
        value = scene_queries.object_revision(window, obj)
    if isinstance(value, dict):
        if value.get("truncated") or value.get("uncertain") or value.get("complete") is False:
            raise ProgramError("An over-budget or incomplete observation cannot authorize a program", code="LIMIT_EXCEEDED")
        value = value.get("object_revision", value.get("revision"))
    if not isinstance(value, str) or not value:
        raise ProgramError("An exact current object revision is required", code="VALIDATION")
    return value
