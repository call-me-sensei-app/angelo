# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fail-closed admission for source links not supported by private graph cloning.

Numeric/string ID properties are copied normally. ID-valued custom properties,
including nested groups and modifier/bone properties, are not RNA pointer links:
the staged clone/remap contract cannot currently preserve them. Reject BEFORE
creating Main IDs or serializing a bundle, rather than importing an undeclared
artist object or silently clearing its pointer during cleanup.
"""

import collections

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
    # Breadth first and iterative. Depth first, a long chain of parent and child pointers (an armature's bones)
    # reached the same bones 17-21 links deep and refused every program on a humanoid rig (39 and 53 bones). A
    # holder's link distance is not bounded either: bone collections nested under each other are reached through a
    # pose bone's bone, as deep as they nest; ``seen`` ends the walk and the budget bounds it. Unlike the program
    # walks, it queues each collection element (every holder may carry ID properties).
    budget, seen, ids, queue = [MAX_FINGERPRINT_ITEMS], set(), set(), collections.deque([block])

    def enqueue(child):
        if child is None or not hasattr(child, "bl_rna"):
            return
        if isinstance(child, (bpy.types.Object, bpy.types.Scene, bpy.types.Collection)):
            return
        queue.append(child)

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
        holder = queue.popleft()
        pointer = int(holder.as_pointer())
        identity = (pointer, holder.bl_rna.identifier)   # A view over another struct's data is its own holder.
        if identity in seen:
            continue
        seen.add(identity)
        if isinstance(holder, bpy.types.ID):
            ids.add(pointer)
            if len(ids) > MAX_STAGE_IDS:
                raise ProgramError("Custom link inspection exceeds the bounded dependency ID limit", code='LIMIT_EXCEEDED')
        budget[0] -= 1  # Not nesting: the queue is iterative, ``seen`` ends it and this budget bounds it.
        if budget[0] < 0:
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
                enqueue(getattr(holder, name, None))
            elif kind in ("COLLECTION", "IDS"):  # IDS: a collection of IDs (rna_fields.link_fields).
                if mesh and name != "materials":
                    continue  # Packed mesh topology contains no custom ID links.
                for child in getattr(holder, name):
                    enqueue(child)


# Modes in which an object does not work on its data: Object Mode, and Pose Mode (the pose channels are the original
# data the depsgraph evaluates). In every other mode the object either holds its data in a buffer the original arrays
# do not have yet (Edit Mode's BMesh, dynamic topology, Multires grids) or has it open while the person changes it or
# what it uses (sculpt, paint, particle edit): a revision, a stage copy and a commit read the original data, so none
# of them can be isolated from it.
NO_BUFFER_MODES = frozenset(("OBJECT", "POSE"))


def _subject(obj, dependency):
    name = obj.name[:64]
    return "%r, which this program's objects depend on," % name if dependency else "%r" % name


def _mode_name(obj, mode=None):
    """Blender's own name of an object's mode ("Edit Mode", "Sculpt Mode", "Weight Paint"), else its identifier."""
    mode = obj.mode if mode is None else mode
    try:
        return obj.bl_rna.properties["mode"].enum_items[mode].name
    except (AttributeError, KeyError, TypeError):
        return mode.replace("_", " ").title()


def _holders():
    """{data pointer: object} for every object outside ``NO_BUFFER_MODES`` that has data, scanned once per exact
    fence. Only read for data with more than one user."""
    def build():
        found = {}
        for item in _bpy().data.objects:
            if item.mode not in NO_BUFFER_MODES and item.data is not None:
                found.setdefault(item.data.as_pointer(), item)
        return found
    return exact_fence.check("objects_holding_data", (), build)


def _holder(obj):
    """(object or None, mode) of what holds ``obj``'s data in a mode (Edit Mode through any object sharing it, a
    Sculpt, paint or other session on shared data), or None. The data's own state decides, not ``obj``'s mode:
    Blender evaluates every user of a mesh from the BMesh of the one editing it (mesh_data_update.cc), and a
    dynamic-topology stroke lives in the sculpting object's BMesh until it leaves the mode, a save or an Undo push.
    The object is None for data in Edit Mode whose editing object is not found."""
    data = obj.data
    if data is None:
        return None
    if getattr(data, "is_editmode", False):
        if obj.mode == "EDIT":
            return obj, "EDIT"
        for item in _bpy().data.objects:   # Only on the way to a refusal: name the object editing it.
            if item.data == data and item.mode == "EDIT":
                return item, "EDIT"
        return None, "EDIT"   # Refused all the same, without naming an object.
    if getattr(data, "users", 1) > 1:
        holder = _holders().get(data.as_pointer())
        if holder is not None:
            return holder, holder.mode
    return None


def mode_refusal(obj, handle=None, dependency=False):
    """The one refusal of an input whose mode, or another object's mode on its data, keeps typed programs from binding
    it, or None. The same code, text and details wherever it is found (bind, the fresh precondition before a commit,
    verify), so its words hold at each: nothing was applied. ``held_by`` names the object the person returns to
    Object Mode (the input itself, another object, or None when no editing object was found). It never suggests a
    script: a script reads the same original data, and an edit buffer written back later overwrites what it wrote."""
    if obj.mode != "OBJECT":
        holder = obj
        what = ("%s is in %s; typed programs bind objects only in Object Mode"
                % (_subject(obj, dependency), _mode_name(obj)))
    else:
        held = _holder(obj)
        if held is None:
            return None
        holder, mode = held
        if holder is None:
            what = ("%s uses data %r that is in Edit Mode, and no object editing it was found"
                    % (_subject(obj, dependency), obj.data.name[:64]))
        else:
            what = ("%s uses data %r that %r has open in %s; typed programs never bind data another object is "
                    "working on" % (_subject(obj, dependency), obj.data.name[:64], holder.name[:64],
                                    _mode_name(holder, mode)))
    remedy = ("Ask the person to return %r to Object Mode when they are ready, then observe again" % holder.name[:64]
              if holder is not None else "Ask the person to leave Edit Mode when they are ready, then observe again")
    return ProgramError("%s, so nothing was applied. %s" % (what, remedy), code="UNSUPPORTED",
                        details={"handles": [handle] if handle else [], "held_by": holder.name[:64] if holder else None})


def admit_input(obj, limits, *, dependency=False, handle=None):
    """The checks every bound input object passes before its revision is compared (BlenderProgramBackend.bind).

    ``limits``: (MAX_VERTICES, MAX_EDGES, MAX_FACES). ``dependency``: ``obj`` is an undeclared dependency, which the
    refusals say; ``handle``: its handle, which they carry. What no mode change can fix (a linked object) is refused
    first, then ``mode_refusal``. Returns the mesh vertex count it adds to the budget.
    """
    if obj.library or not obj.is_editable:
        raise ProgramError("Program write targets must be local editable objects", code='UNSUPPORTED')
    refusal = mode_refusal(obj, handle, dependency)
    if refusal is not None:
        raise refusal
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


def declared_handles(binding):
    """The handles a bound program declared in its read and write sets (the rest of its bound handles are inferred
    dependencies)."""
    program = binding.get("program") or {}
    return {entry["handle"] for key in ("read_set", "write_set") for entry in program.get(key, ())}


def assert_still_admitted(obj, handle, dependency):
    """Verify's half of ``admit_input``: a mode entered after bind, by the input or by an object holding its data, is
    ``mode_refusal`` before any revision is read. Shared data is scanned once per fence, as at bind."""
    refusal = mode_refusal(obj, handle, dependency)
    if refusal is not None:
        raise refusal


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
