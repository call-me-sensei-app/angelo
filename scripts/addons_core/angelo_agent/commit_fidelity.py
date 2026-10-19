# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Commit fidelity: the live result of a commit is the staged result its checks measured.

Declared and derived checks run on the stage (``program_jobs`` VALIDATE, and
again immediately before commit), and commit re-runs every one of them on the
live objects after the writes (``live_checks``), so "passed on the live result"
is literal. This module adds the structural part, inside the same rollback
scope, for every object the program changed (written objects with a change,
and new objects):

* data: the live object holds the very data block the stage measured (a
  swapped written object, a new object), or one of the same type and element
  counts (vertices, edges, faces and corners; splines and their points; lattice
  points; bones; text): a linked duplicate pointed at its link root's live
  data, a written object whose data commit does not swap. Bounds are never
  compared: an object's cached bounds are stale or a fallback for curves and
  lattices, objects hidden or disabled in viewports and data after an Undo
  (otherwise curve and lattice moves rolled back falsely);
* transforms: a written object's local matrix equals its staged copy's
  (unless animation drives it: the stage keeps the values it was given);
* linked copies: commit points a linked duplicate or linked batch copy at its
  link root's data, so the copy shows the root's materials and face material
  indices, not its own staged ones. Before any write, each copy a material
  step gave a material must have the staged material list (by the material
  each slot commits as) and per-face (per-spline) material indices of its root
  (``linked_layouts``). Without it, copies
  whose own checks had passed committed on another
  material: one resolved through ``source`` from an isolated copy, a library
  material sharing a stated name, two unnamed statements (each a new material)
  and faces a Geometry Nodes material index left on the root.

A mismatch raises ``COMMIT_FIDELITY`` before the commit returns, so the journal
rolls the whole commit back and the result is a failure, never a certificate.
It reads no coordinates, so its cost does not grow with mesh size.

``dependents`` names live objects outside the program whose evaluated shape or
placement may follow an object the program changed (in its data or its
transform): every object that reaches one through Blender's reference graph
(``bpy.data.user_map``), directly or through node groups, collections,
particle settings, data blocks and other IDs. No check of this program read
them.
"""

from .program_jobs import ProgramError

_TOLERANCE = 1e-5  # Relative to the value's magnitude (at least absolute): float32 bounds and matrices.
MAX_DEPENDENTS = 16


def _counts(data):
    """The type and element counts of a data block (O(1); O(splines) for a curve)."""
    if data is None:
        return None
    kind = data.bl_rna.identifier
    if kind == "Mesh":
        return kind, len(data.vertices), len(data.edges), len(data.polygons), len(data.loops)
    if kind in {"Curve", "SurfaceCurve", "TextCurve"}:
        return (kind, data.dimensions, getattr(data, "body", None),
                tuple((spline.type, len(spline.points), len(spline.bezier_points)) for spline in data.splines))
    if kind == "Lattice":
        return kind, data.points_u, data.points_v, data.points_w
    if kind == "Armature":
        return kind, len(data.bones)
    return (kind,)


# Data whose object bounds without modifiers are its own (a metaball's join its family, so they are not).
def _near(first, second):
    return len(first) == len(second) and all(
        abs(a - b) <= _TOLERANCE * max(1.0, abs(a), abs(b)) for a, b in zip(first, second))


def _matrix(obj):
    """The local matrix of an object no animation drives (the stage does not write evaluated channels back)."""
    animation = obj.animation_data
    if animation is not None and (animation.action is not None or len(animation.drivers) or len(animation.nla_tracks)):
        return None
    return tuple(float(value) for row in obj.matrix_basis for value in row)


def capture(stage, new_objects):
    """Before any live write: what each changed object's committed state must equal.

    Rows are (name, live object, staged data, staged object, staged matrix);
    a new object is its own staged object, and one commit may re-point (a
    linked duplicate) keeps its staged counts too.
    """
    deleted = set(stage.extras.get("deleted", ()))
    rows = [(stage.logical_names[handle], stage.originals[handle], stage.objects[handle].data, stage.objects[handle],
             _matrix(stage.objects[handle]))
            for handle in stage.binding["writes"] if handle in stage.changes and handle not in deleted]
    linked = {obj.as_pointer() for alias, obj in stage.context.aliases.items()
              if "$" + alias.lstrip("$") in stage.extras.get("linked_duplicates", {})}
    staged = {}
    for obj in new_objects:
        if obj.as_pointer() in linked and obj.data is not None and obj.data.as_pointer() not in staged:
            staged[obj.data.as_pointer()] = _counts(obj.data)
        rows.append((obj.name, obj, obj.data, None, None))
    linked_layouts(stage)
    return rows, staged


def _material_key(block):
    """What a slot's material commits as: the live material every staged copy of it stands for (an edit for all
    its users replaces it everywhere), or, for a new material or an isolated copy, that ID itself."""
    if block is None:
        return None
    from .node_edit import source_uid
    from .node_edit_spec import MODE
    if block.get(MODE) == "ASSIGN":  # node_edit's isolated copy commits under a name of its own.
        return ("id", block.as_pointer())
    uid = source_uid(block)
    return ("uid", uid if uid is not None else block.session_uid)


def _material_layout(data):
    """The data's slot materials (``_material_key``) and each face's (a curve's splines') material index."""
    from array import array
    materials = tuple(_material_key(block) for block in getattr(data, "materials", ()))
    kind = data.bl_rna.identifier
    if kind == "Mesh":
        indices = array("i", bytes(4 * len(data.polygons)))
        data.polygons.foreach_get("material_index", indices)
    elif kind in {"Curve", "SurfaceCurve", "TextCurve"}:
        indices = array("i", [spline.material_index for spline in data.splines])
    else:
        indices = array("i")
    return materials, indices


def linked_layouts(stage):
    """Raise unless every linked copy a material step gave a material ends with its link root's materials and faces.

    That copy's readback and checks measured its own staged data, and commit
    gives it the root's (``program_memberships.link_new_objects``); copies
    that already share the root's staged data (a linked batch) are equal by
    construction. A copy no material step named follows its root, as
    Blender's linked duplicate does: an isolated edit of the root's material
    shows on it too (such a probe commits as before).
    """
    from .material_rules import KINDS, also
    linked = stage.extras.get("linked_duplicates", {})
    created = {"$" + alias.lstrip("$"): obj for alias, obj in stage.context.aliases.items()}
    given = {reference for operation in stage.binding["program"]["operations"] if operation["kind"] in KINDS
             for reference in [operation["target"], *also(operation)]}
    for alias in linked:
        if alias not in given:
            continue
        reference, seen = alias, set()
        while reference in linked and reference not in seen:
            seen.add(reference)
            reference = linked[reference]
        root = stage.objects.get(reference) or stage.originals.get(reference) or created.get(reference)
        copy = created.get(alias)
        if copy is None or root is None or copy.data is None or root.data is None or copy.data == root.data:
            continue  # verify_links refuses a relation that cannot hold; the same data has the same materials.
        mine, theirs = _material_layout(copy.data), _material_layout(root.data)
        if mine != theirs:
            what = "material slots" if mine[0] != theirs[0] else "face materials"
            raise ProgramError("COMMIT_FIDELITY: the linked copy %r shares %r's data after commit, but its staged %s "
                               "differ from that object's, so it would not show what its checks measured; nothing was "
                               "changed. Give every object of the linked group the same material in one statement "
                               "(also lists the others), or duplicate with linked false"
                               % (copy.name[:64], str(stage.logical_names.get(reference, root.name))[:64], what),
                               code="UNSUPPORTED")
    return len(linked)


def verify(captured):
    """After the writes and a view-layer update: raise unless every changed object holds its staged result."""
    rows, staged = captured
    checked = {}
    for name, live, data, copy, matrix in rows:
        live_matrix = _matrix(live) if matrix is not None else None
        if live_matrix is not None and not _near(live_matrix, matrix):
            _mismatch(name, "transform")
        if live.data == data:
            continue
        if data is None or live.data is None:
            _mismatch(name, "data")
        pair = (data.as_pointer(), live.data.as_pointer())
        if pair in checked:
            continue
        counts = _counts(data) if copy is not None else staged.get(data.as_pointer())
        if counts is None or _counts(live.data) != counts:
            _mismatch(name, "geometry")
        checked[pair] = True
    return len(rows)


def verify_links(stage):
    """Raise unless every linked duplicate and linked copy shares its link root's live data (after linking)."""
    linked = stage.extras.get("linked_duplicates", {})
    created = {"$" + alias.lstrip("$"): obj for alias, obj in stage.context.aliases.items()}
    for alias in linked:
        reference, seen = alias, set()
        while reference in linked and reference not in seen:
            seen.add(reference)
            reference = linked[reference]
        root = stage.originals.get(reference) or created.get(reference)
        obj = created.get(alias)
        if obj is None or root is None or obj.data is None or obj.data != root.data:
            _mismatch(obj.name if obj is not None else alias, "linked data")
    return len(linked)


def _mismatch(name, what):
    raise ProgramError("COMMIT_FIDELITY: the committed %s of %r would differ from the staged result its checks "
                       "measured; nothing was changed" % (what, str(name)[:64]), code="UNSUPPORTED")


# ID types through which an object can place or reshape another: objects (modifiers, constraints, drivers,
# parents, particle and instance settings), node groups (Object Info, Collection Info, Geometry Nodes sockets),
# collections (instancers), particle settings (instance objects), curve data (bevel and taper objects), Grease
# Pencil data (a layer's parent) and hair curves (their surface). Scenes, materials, worlds and the like hold or
# shade an object; they never move or reshape another.
_CARRIERS = {"OBJECT", "NODETREE", "COLLECTION", "PARTICLE", "CURVE", "GREASEPENCIL_V3", "GREASEPENCIL", "CURVES"}
MAX_WALK_IDS = 4096  # IDs visited per commit; beyond it the dependents are reported as not all listed.


def dependents(scene, stage, program_objects):
    """Live objects of ``scene`` outside ``program_objects`` whose evaluated shape or placement may follow a changed one.

    Changed: every object the committed program changed, in its data or its
    transform (``changed_objects``). The walk follows Blender's reference graph
    upwards: the IDs that use a changed object, the IDs that use those, and so
    on, through node groups, collections (an instancer, a Collection Info
    node), particle settings, curve data (bevel or taper objects) and any other
    ID, until it reaches objects. Read-only, after a successful commit: it
    never raises, and a walk it cannot finish is reported as such.
    """
    from .program_dependencies import _bpy
    try:
        frontier = set(changed_objects(stage))
        if not frontier:
            return []
        bpy = _bpy()
        inside = {obj.as_pointer() for obj in program_objects} | {obj.as_pointer() for obj in frontier}
        seen, found, uses = set(frontier), {}, {}
        while frontier and len(seen) <= MAX_WALK_IDS:
            users = bpy.data.user_map(subset=frontier, value_types=_CARRIERS)
            frontier = set()
            for used, holders in users.items():
                for user in holders:
                    if user in seen:
                        continue
                    seen.add(user)
                    uses[user.as_pointer()] = uses.get(used.as_pointer(), used.name)
                    if isinstance(user, bpy.types.Object):
                        if user.as_pointer() not in inside and scene.objects.get(user.name) == user:
                            found[user.as_pointer()] = {"name": user.name[:64], "uses": uses[user.as_pointer()][:64]}
                    frontier.add(user)  # An object's own users (its children, an instancing collection) follow it.
        if frontier:
            return [{"unknown": True}]
    except Exception:
        return [{"unknown": True}]
    rows = sorted(found.values(), key=lambda row: row["name"])
    return rows[:MAX_DEPENDENTS] + ([{"more": len(rows) - MAX_DEPENDENTS}] if len(rows) > MAX_DEPENDENTS else [])


def changed_objects(stage):
    """The live objects the committed program changed: written objects with a change, and the new ones."""
    deleted = set(stage.extras.get("deleted", ()))
    found = [stage.originals[handle] for handle in stage.binding["writes"]
             if handle in stage.changes and handle not in deleted]
    return found + list(stage.context.aliases.values())


def dependents_line(result):
    """The certificate's line naming ``unchecked_dependents`` (model-facing), or None."""
    rows = result.get("unchecked_dependents") if isinstance(result, dict) else None
    if not isinstance(rows, list) or not rows:
        return None
    named = ["'%s' (uses '%s')" % (str(row["name"])[:64], str(row["uses"])[:64]) for row in rows
             if isinstance(row, dict) and isinstance(row.get("name"), str) and isinstance(row.get("uses"), str)]
    more = sum(row.get("more", 0) for row in rows if isinstance(row, dict) and type(row.get("more")) is int)
    if any(isinstance(row, dict) and row.get("unknown") for row in rows) or not named:
        return "- Not checked: objects outside this program that may use an object it changed (not listed)."
    return ("- Not checked: the evaluated shape or placement of " + ", ".join(named)
            + (" and %d more" % more if more else "") + ", outside this program, which uses an object it changed.")
