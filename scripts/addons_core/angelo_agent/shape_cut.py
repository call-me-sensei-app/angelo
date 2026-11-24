# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shape Cut in Blender: a validated cut (``shape_cut_spec``) becomes live cutters and Boolean modifiers.

Every cut is non-destructive until applied. Each solid of a cut is a cutter object in the "Angelo Cutters"
collection, parented to its target (move the target and its cuts follow; move a cutter and its cut moves), drawn as
wire, never rendered, with an array of copies kept as Array modifiers so count and spacing stay editable. The target
gets one Boolean modifier per cutter, inserted before the finishing modifiers (Smooth by Angle, Bevel, Weighted
Normal) so shading always reads the cut result. The solver is Manifold when the target's result so far is closed
(cutters always are), else Exact; the report says which. ``apply_and_clean`` makes it real geometry and tidies it.
The hand tool (``shape_cut_tool``) and the agent's ``shape_cut`` operation both call ``cut``. doc/angelo/shape_cut.rst.
"""

import json
import math

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector

from . import shape_cut_geometry as geo, shape_cut_spec as spec_mod

COLLECTION = "Angelo Cutters"
TAG = "angelo_shape_cut"          # On a cutter: JSON {target, role, operation, spec}.
FINISH_TAG = "angelo_shape_finish"   # On the target: {role: persistent_uid} of its finishing modifiers.
SHARP_ANGLE = math.radians(30.0)
_OPERATION = {"DIFFERENCE": "DIFFERENCE", "UNION": "UNION", "INTERSECT": "INTERSECT", "PIECE": "INTERSECT"}


def _evaluated_mesh(obj, depsgraph):
    return obj.evaluated_get(depsgraph).data


def face_frame(obj, depsgraph, face_index, hit=None, align="EDGE"):
    """The frame on ``obj``'s evaluated face ``face_index``; without a ``hit``, the face's longest edge sets ``u``.
    Returns (frame, centre of the face in the frame)."""
    mesh = _evaluated_mesh(obj, depsgraph)
    if not 0 <= face_index < len(mesh.polygons):
        raise ValueError("face %d is not on %s (it has %d faces)" % (face_index, obj.name, len(mesh.polygons)))
    world = obj.matrix_world
    face = mesh.polygons[face_index]
    corners = [tuple(world @ mesh.vertices[i].co) for i in face.vertices]
    normal = tuple((world.to_3x3().inverted_safe().transposed() @ face.normal).normalized())
    if hit is None:
        edges = [(corners[i], corners[(i + 1) % len(corners)]) for i in range(len(corners))]
        a, b = max(edges, key=lambda e: (Vector(e[1]) - Vector(e[0])).length)
        hit = tuple((Vector(a) + Vector(b)) / 2)
    frame = geo.frame_from_face(normal, corners, hit, align)
    centre = Vector((0.0, 0.0, 0.0))
    for corner in corners:
        centre += Vector(corner)
    local = frame.to_local(tuple(centre / len(corners)))
    return frame, (local[0], local[1])


def face_towards(obj, depsgraph, direction, limit=SHARP_ANGLE):
    """The largest evaluated face whose world normal is within ``limit`` of ``direction`` (e.g. (0, 0, 1): the top)."""
    mesh = _evaluated_mesh(obj, depsgraph)
    world = obj.matrix_world.to_3x3()
    normals = world.inverted_safe().transposed()
    wanted = Vector(direction)
    if wanted.length < 1e-9:
        raise ValueError("towards needs a direction")
    wanted.normalize()
    scale = abs(world.determinant()) ** (2.0 / 3.0)
    best, best_area = None, -1.0
    for polygon in mesh.polygons:
        if (normals @ polygon.normal).normalized().angle(wanted, math.pi) <= limit and polygon.area * scale > best_area:
            best, best_area = polygon.index, polygon.area * scale
    if best is None:
        raise ValueError("%s has no face within %d degrees of %s" % (obj.name, round(math.degrees(limit)),
                                                                     tuple(round(c, 3) for c in direction)))
    return best


def world_corners(obj, depsgraph):
    evaluated = obj.evaluated_get(depsgraph)
    return [tuple(obj.matrix_world @ Vector(c)) for c in evaluated.bound_box]


def is_closed_mesh(mesh):
    """Every edge borders exactly two faces (the Manifold solver's input rule), read as arrays."""
    if not len(mesh.polygons):
        return False
    edges = np.empty(len(mesh.loops), np.int32)
    mesh.loops.foreach_get("edge_index", edges)
    counts = np.bincount(edges, minlength=len(mesh.edges))
    return bool(np.all(counts == 2))


def _collection(scene):
    found = bpy.data.collections.get(COLLECTION)
    if found is None:
        found = bpy.data.collections.new(COLLECTION)
        found.hide_render = True
    if found.name not in scene.collection.children:
        scene.collection.children.link(found)
    return found


def finishing(target):
    """{role: modifier} of the finishing modifiers Shape Cut added to ``target``. A modifier holds no custom
    properties, so the target keeps each one's ``persistent_uid``, which survives a rename; a removed one drops out."""
    kept = target.get(FINISH_TAG)
    if not kept:
        return {}
    by_uid = {m.persistent_uid: m for m in target.modifiers}
    return {role: by_uid[uid] for role, uid in kept.items() if uid in by_uid}


def _finish_index(target):
    """Where a new Boolean goes: before the first finishing modifier Shape Cut added, else at the end."""
    uids = {m.persistent_uid for m in finishing(target).values()}
    for index, modifier in enumerate(target.modifiers):
        if modifier.persistent_uid in uids:
            return index
    return len(target.modifiers)


def _add_boolean(obj, cutter, operation, solver):
    modifier = obj.modifiers.new(cutter.name, "BOOLEAN")
    modifier.operand_type = "OBJECT"
    modifier.object = cutter
    modifier.operation = _OPERATION[operation]
    modifier.solver = solver
    index = _finish_index(obj)
    current = len(obj.modifiers) - 1
    if index < current:
        obj.modifiers.move(current, index)
    return modifier


def _sharp_edges(mesh, angle=SHARP_ANGLE):
    """Edges whose faces meet at more than ``angle`` marked sharp, as Blender's own sharp_edge attribute."""
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.edges.ensure_lookup_table()
        sharp = [len(e.link_faces) == 2 and e.calc_face_angle(0.0) > angle for e in bm.edges]
    finally:
        bm.free()
    attribute = mesh.attributes.get("sharp_edge") or mesh.attributes.new("sharp_edge", "BOOLEAN", "EDGE")
    attribute.data.foreach_set("value", sharp)
    return sum(sharp)


def _cutter(name, verts, faces, frame, target, copies, spacing, collection, record):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.validate(clean_customdata=False)
    mesh.shade_smooth()
    _sharp_edges(mesh)   # Flat-sided walls stay sharp; a circle's facets (under 30 degrees) stay smooth.
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    obj.matrix_world = Matrix(frame.matrix())
    obj.parent = target
    obj.matrix_parent_inverse = target.matrix_world.inverted()
    obj.display_type = "WIRE"
    obj.hide_render = True
    obj.visible_camera = obj.visible_shadow = obj.visible_diffuse = obj.visible_glossy = False
    obj[TAG] = json.dumps(record, sort_keys=True)
    if len(copies) > 1:
        columns, rows, (du, dv) = spacing
        for count, offset, label in ((columns, (du, 0.0, 0.0), "Columns"), (rows, (0.0, dv, 0.0), "Rows")):
            if count > 1:
                array = obj.modifiers.new(label, "ARRAY")
                array.count = count
                array.use_relative_offset = False
                array.use_constant_offset = True
                array.constant_offset_displace = offset
    return obj


def cut(scene, depsgraph, target, frame, spec, name="Cut"):
    """Make ``spec`` (validated here) on ``target`` in ``frame``. Returns a report: cutters, the slice's piece,
    the solver and why. Raises ValueError for a cut that cannot be made; nothing is left behind then."""
    if target.type != "MESH":
        raise ValueError("%s is not a mesh" % target.name)
    spec = spec_mod.validate(spec)
    through = spec_mod.through_depth(frame, world_corners(target, depsgraph)) if spec["depth"] == "THROUGH" else None
    solids = spec_mod.solids(spec, through)
    closed = is_closed_mesh(_evaluated_mesh(target, depsgraph))
    solver = "MANIFOLD" if closed else "EXACT"
    why = ("the target is closed, so the fastest solver works" if closed else
           "the target has open or non-manifold edges, which only the Exact solver takes")
    collection = _collection(scene)
    array = spec["array"]
    spacing = (array["columns"], array["rows"], array["spacing"]) if array else (1, 1, (0.0, 0.0))
    made, piece = [], None
    try:
        if any(s["role"] == "PIECE" for s in solids):
            piece = target.copy()   # Shares the mesh, keeps the cuts made so far; its own Intersect follows.
            piece.name = target.name + " Slice"
            for owner in target.users_collection:
                owner.objects.link(piece)
        for solid in solids:
            record = {"target": target.name, "role": solid["role"], "operation": spec["operation"],
                      "spec": {k: v for k, v in spec.items()}}
            cutter = _cutter("%s %s" % (name, solid["role"].title()), solid["verts"], solid["faces"], frame,
                             target, solid["copies"], spacing, collection, record)
            made.append(cutter)
            owner = piece if solid["role"] == "PIECE" else target
            _add_boolean(owner, cutter, solid["role"], solver)
    except Exception:
        for cutter in made:
            remove_cutter(cutter)
        if piece is not None:
            bpy.data.objects.remove(piece)
        raise
    return {"cutters": [c.name for c in made], "piece": piece.name if piece else None, "solver": solver,
            "solver_reason": why, "copies": len(solids[0]["copies"]), "operation": spec["operation"]}


def cutters_of(target):
    """The cutters of ``target``'s cuts: those its Booleans read, and those its record names on other objects (a
    slice's piece cuts with a cutter of its own), so applying the cuts reaches the piece too."""
    out = []
    for modifier in target.modifiers:
        if modifier.type == "BOOLEAN" and modifier.object is not None and modifier.object.get(TAG):
            if modifier.object not in out:
                out.append(modifier.object)
    for obj in bpy.data.objects:
        if obj not in out and obj.get(TAG) and json.loads(obj[TAG]).get("target") == target.name:
            out.append(obj)
    return out


def remove_cutter(cutter):
    """Removes a cutter and every Boolean that reads it (the cut disappears; the target's mesh never changed)."""
    for obj in bpy.data.objects:
        for modifier in [m for m in obj.modifiers if m.type == "BOOLEAN" and m.object == cutter]:
            obj.modifiers.remove(modifier)
    mesh = cutter.data
    bpy.data.objects.remove(cutter)
    if mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def finish(target, bevel_width=0.0, segments=3, angle=SHARP_ANGLE):
    """Bevel-friendly shading after the cuts, as the last modifiers: sharp edges by angle (Blender's Smooth by
    Angle), an optional angle-limited Bevel that hardens its normals, then Weighted Normal keeping sharp edges.
    Running it again updates the same modifiers; a width of 0 removes the Bevel."""
    if not 0 <= bevel_width <= spec_mod.MAX_LENGTH or not 1 <= segments <= 64 or not 0 < angle < math.pi:
        raise ValueError("bevel width 0 to %g m, 1 to 64 segments, an angle between 0 and 180 degrees"
                         % spec_mod.MAX_LENGTH)
    mods = finishing(target)
    target.data.shade_smooth()
    sharp = mods.get("sharp") or _sharp_by_angle(target)
    _set_angle(sharp, angle)
    bevel = mods.get("bevel")
    if bevel_width > 0:
        bevel = bevel or target.modifiers.new("Bevel", "BEVEL")
        bevel.limit_method = "ANGLE"
        bevel.angle_limit = angle
        bevel.width = bevel_width
        bevel.segments = segments
        bevel.harden_normals = True
        bevel.use_clamp_overlap = True
    elif bevel is not None:
        target.modifiers.remove(bevel)
        bevel = None
    weighted = mods.get("weighted") or target.modifiers.new("Weighted Normal", "WEIGHTED_NORMAL")
    weighted.keep_sharp = True
    weighted.mode = "FACE_AREA"
    order = [m for m in (sharp, bevel, weighted) if m is not None]
    roles = zip(("sharp", "bevel", "weighted"), (sharp, bevel, weighted))
    target[FINISH_TAG] = {role: m.persistent_uid for role, m in roles if m is not None}
    for modifier in order:   # Each to the end in turn: Smooth by Angle, Bevel, Weighted Normal.
        target.modifiers.move(target.modifiers.find(modifier.name), len(target.modifiers) - 1)
    return [m.name for m in order]


def _sharp_by_angle(target):
    """Blender's own Smooth by Angle (Shade Auto Smooth's node group), unpinned so the Bevel can follow it."""
    before = set(m.name for m in target.modifiers)
    with bpy.context.temp_override(object=target, active_object=target, selected_objects=[target],
                                   selected_editable_objects=[target]):
        bpy.ops.object.shade_auto_smooth(use_auto_smooth=True, angle=SHARP_ANGLE)
    modifier = next(m for m in target.modifiers if m.name not in before and m.type == "NODES")
    modifier.use_pin_to_last = False
    return modifier


def _set_angle(modifier, angle):
    """Smooth by Angle's Angle input. A Geometry Nodes modifier's inputs are its ``properties.inputs`` (as Blender's
    own Shade Auto Smooth sets them), never ID properties of the modifier."""
    for item in modifier.node_group.interface.items_tree:
        if getattr(item, "in_out", "") == "INPUT" and item.name == "Angle":
            modifier.properties.inputs[item.identifier]["value"] = angle


def _owners(cutters):
    """Every object with a Boolean reading one of ``cutters`` (the target, and a slice's piece)."""
    return [obj for obj in bpy.data.objects
            if any(m.type == "BOOLEAN" and m.object in cutters for m in obj.modifiers)]


def _bake(obj, depsgraph, cutters):
    """``obj``'s modifiers up to its last Boolean reading ``cutters`` become its mesh; the rest stay live."""
    last = max(i for i, m in enumerate(obj.modifiers) if m.type == "BOOLEAN" and m.object in cutters)
    later = [m for m in obj.modifiers][last + 1:]
    shown = [m.show_viewport for m in later]
    for modifier in later:
        modifier.show_viewport = False
    try:
        depsgraph.update()
        result = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph), depsgraph=depsgraph)
    finally:
        for modifier, flag in zip(later, shown):
            modifier.show_viewport = flag
    for modifier in [m for m in obj.modifiers][:last + 1]:
        obj.modifiers.remove(modifier)
    return result


def apply_and_clean(depsgraph, target, angle=SHARP_ANGLE):
    """Makes every cut on ``target`` real geometry and tidies it. The modifiers up to the last cut are applied (as
    Blender's Apply does, in order; finishing and later modifiers stay live) on the target and on any slice piece
    sharing its cutters; then the cutters go, coincident vertices merge, degenerate faces dissolve, coplanar splits
    merge and edges sharper than ``angle`` are marked sharp. Returns facts about the target's result."""
    cutters = cutters_of(target)
    if not cutters:
        raise ValueError("%s has no Shape Cut cuts to apply" % target.name)
    owners = _owners(cutters)
    for obj in owners:
        if obj.data.shape_keys is not None:
            raise ValueError("%s has shape keys, which a Boolean cannot be applied over" % obj.name)
    facts = None
    for obj in owners:
        result = _bake(obj, depsgraph, cutters)
        cleaned = _clean(result, angle)
        old = obj.data
        obj.data = result
        if old.users == 0:
            bpy.data.meshes.remove(old)
        if obj == target:
            facts = cleaned
    for cutter in cutters:
        remove_cutter(cutter)
    facts["cutters_applied"] = len(cutters)
    facts["objects"] = sorted(obj.name for obj in owners)
    return facts


def _clean(mesh, angle):
    """Tidies a Boolean result in place and says what it left."""
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        size = max((max(v.co[i] for v in bm.verts) - min(v.co[i] for v in bm.verts) for i in range(3)),
                   default=0.0) if bm.verts else 0.0
        distance = max(size * 1e-6, 1e-7)
        before = len(bm.faces)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=distance)
        bmesh.ops.dissolve_degenerate(bm, edges=bm.edges, dist=distance)
        bmesh.ops.dissolve_limit(bm, angle_limit=math.radians(0.1), use_dissolve_boundaries=False,
                                 verts=bm.verts, edges=bm.edges, delimit={"MATERIAL", "SEAM", "SHARP", "UV"})
        open_edges = sum(1 for e in bm.edges if len(e.link_faces) != 2)
        ngons = sum(1 for f in bm.faces if len(f.verts) > 4)
        facts = {"faces_before": before, "faces": len(bm.faces), "ngons": ngons, "non_manifold_edges": open_edges}
        bm.to_mesh(mesh)
    finally:
        bm.free()
    facts["sharp_edges"] = _sharp_edges(mesh, angle)
    mesh.update()
    return facts
