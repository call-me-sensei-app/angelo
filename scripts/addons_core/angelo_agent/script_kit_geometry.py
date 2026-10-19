# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Mesh-level work behind the reviewed-script kit (script_kit): world bounds and volumes, loose parts, and a cut that
changes nothing until it is proven.

``cut_mesh`` cuts only what it can prove. It refuses, before anything is made, a target or cutter that is not a mesh
in Object Mode, that is not a closed solid (an open edge, a wire edge or a loose vertex), a target wound inward (a
room's inside) or with shape keys, and either one with a modifier that makes or moves geometry (an Array or Mirror
would repeat the opening, a Solidify or Displace would move it away from what is seen), and a target something is
bound to by vertex index (a vertex parent, another object's Surface or Mesh Deform); a Bevel and other shape-keeping
modifiers, a subtracting Boolean, Shade Auto Smooth and a level-0 Subdivision stay on top and run once. It then
works on copies in world space, wound outward: only the target's loose parts the cutter's bounds reach are cut (the
others cannot lose anything, so a far part is never re-cut or united), with the EXACT solver's self-intersection
handling only when those parts overlap, touch or share faces, the case that needs it. A result is kept only when the
cutter overlaps the parts, something is left, the target loses extent only where the cutter reaches and never grows,
and the volume removed is the overlap's (``_removed``: what the parts lost, or for crowded parts the overlap less what
the result still holds inside the cutter); a cut that fails one of those proofs without self-intersection handling is
tried once with it (up to RETRY_VERTICES), under the same proofs. The result is written into a copy of the
target's own mesh, so its material slots, names and properties stay. Nothing of the person's data changes when it
raises.
"""

import math

MAX_CUT_VERTICES = 2000000
GEOMETRY = frozenset({"MESH", "CURVE", "SURFACE", "FONT", "META"})
KEEPS_SHAPE = frozenset({"BEVEL", "WEIGHTED_NORMAL", "EDGE_SPLIT", "TRIANGULATE", "NORMAL_EDIT", "UV_PROJECT",
                         "UV_WARP", "VERTEX_WEIGHT_EDIT", "VERTEX_WEIGHT_MIX", "VERTEX_WEIGHT_PROXIMITY"})
SMOOTHING_GROUPS = ("Smooth by Angle", "Auto Smooth")   # Shade Auto Smooth's node groups: sharp edges only.
BINDS_BY_INDEX = {"SURFACE_DEFORM": "target", "MESH_DEFORM": "object"}   # Another object's modifier bound to it.
REACH = 1e-4   # Bounds this close count as touching.
RETRY_VERTICES = 50000   # A cut that failed a proof is retried with self-intersection handling up to this size.


def outward(mesh):
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()


def world_points(mesh, matrix):
    import numpy
    count = len(mesh.vertices)
    flat = numpy.empty(count * 3, dtype=numpy.float32)   # Blender's own precision: read without a conversion.
    mesh.vertices.foreach_get("co", flat)
    points = flat.reshape(count, 3).astype(numpy.float64)
    rotation = numpy.array([[matrix[r][c] for c in range(3)] for r in range(3)])
    offset = numpy.array([matrix[r][3] for r in range(3)])
    return points @ rotation.T + offset


def world_bounds(mesh, matrix):
    points = world_points(mesh, matrix)
    if not len(points):
        return None
    return tuple(points.min(axis=0)), tuple(points.max(axis=0))


def world_volume(mesh, matrix, signed=False):
    """The enclosed volume in world space (``matrix`` None: in the mesh's own space, where its sign is its winding)."""
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(mesh)
    if matrix is not None:
        bm.transform(matrix)
    volume = bm.calc_volume(signed=True)
    bm.free()
    return volume if signed else abs(volume)


def edge_faces(mesh):
    """How many faces use each edge, and which vertices no edge uses."""
    import numpy
    loops = numpy.empty(len(mesh.loops), dtype=numpy.int64)
    mesh.loops.foreach_get("edge_index", loops)
    counts = numpy.bincount(loops, minlength=len(mesh.edges))
    ends = numpy.empty(len(mesh.edges) * 2, dtype=numpy.int64)
    mesh.edges.foreach_get("vertices", ends)
    used = numpy.zeros(len(mesh.vertices), dtype=bool)
    used[ends] = True
    return counts, int((~used).sum())


def components(mesh):
    """A label per vertex: vertices joined by edges share one."""
    import numpy
    parent = list(range(len(mesh.vertices)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    ends = numpy.empty(len(mesh.edges) * 2, dtype=numpy.int64)
    mesh.edges.foreach_get("vertices", ends)
    for a, b in ends.reshape(-1, 2).tolist():
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    return numpy.array([find(i) for i in range(len(parent))], dtype=numpy.int64)


def part_bounds(points, labels):
    """World bounds of each labelled piece: (labels in order, [((min x, y, z), (max x, y, z))])."""
    import numpy
    keys, inverse = numpy.unique(labels, return_inverse=True)
    lo = numpy.full((len(keys), 3), numpy.inf)
    hi = numpy.full((len(keys), 3), -numpy.inf)
    numpy.minimum.at(lo, inverse, points)
    numpy.maximum.at(hi, inverse, points)
    return keys, inverse, [(tuple(a), tuple(b)) for a, b in zip(lo.tolist(), hi.tolist())]


def loose_parts(mesh, matrix):
    """World bounds of each connected piece of a mesh: [((min x, y, z), (max x, y, z))]."""
    if not len(mesh.vertices):
        return []
    return part_bounds(world_points(mesh, matrix), components(mesh))[2]


def touching_pairs(parts, tolerance):
    """For each part, the indices of parts whose bounds touch it (within tolerance). Parts are hashed into a grid of
    cells the size of a typical part, and a pair is compared once, in the lowest cell both occupy; a part spanning
    more than 64 cells is compared with every other part. Near linear for rows, stacks, walls and blocks of parts."""
    near = {i: [] for i in range(len(parts))}
    if len(parts) < 2:
        return near
    import itertools
    import numpy
    lo = numpy.array([p[0] for p in parts], dtype=float) - tolerance
    hi = numpy.array([p[1] for p in parts], dtype=float) + tolerance
    cell = float(numpy.median((hi - lo).max(axis=1))) + 1e-9
    first, last = numpy.floor(lo / cell).astype(numpy.int64), numpy.floor(hi / cell).astype(numpy.int64)
    spans = (last - first + 1).prod(axis=1)
    grid, big = {}, [i for i in range(len(parts)) if spans[i] > 64]
    for i in numpy.flatnonzero(spans <= 64).tolist():
        a, b = first[i].tolist(), last[i].tolist()
        for key in itertools.product(*(range(a[k], b[k] + 1) for k in range(3))):
            grid.setdefault(key, []).append(i)

    def touch(i, j):
        if (lo[i] <= hi[j]).all() and (lo[j] <= hi[i]).all():
            near[i].append(j)
            near[j].append(i)
    for key, members in grid.items():
        for x, i in enumerate(members):
            for j in members[x + 1:]:
                if tuple(numpy.maximum(first[i], first[j]).tolist()) == key:
                    touch(i, j)
    for i in big:
        for j in range(len(parts)):
            if j != i and (spans[j] <= 64 or j > i):
                touch(i, j)
    return near


def _overlaps(box, other, tolerance=REACH):
    return all(box[0][k] <= other[1][k] + tolerance and other[0][k] <= box[1][k] + tolerance for k in range(3))


def _keeps_shape(modifier):
    """Whether a cut of the base mesh under this modifier is the opening that is seen, by an explicit list: the
    shape-keeping kinds, a Boolean that subtracts or intersects (it commutes with the cut), Shade Auto Smooth's node
    group and a Subdivision at level 0 in viewport and render. (A measured stack is no proof: a Mask, Armature or Hook
    at rest moves nothing until the cut's new vertices, in no group, are left behind.)"""
    kind = modifier.type
    if kind in KEEPS_SHAPE:
        return True
    if kind == "BOOLEAN":
        return modifier.operation in {"DIFFERENCE", "INTERSECT"}
    if kind == "NODES":
        group = modifier.node_group
        return group is not None and group.name.startswith(SMOOTHING_GROUPS)
    if kind == "SUBSURF":
        return modifier.levels == 0 and modifier.render_levels == 0
    return False


def _shaping(obj):
    """The first modifier, shown in the viewport or in renders, that makes or moves geometry, or None."""
    return next((m for m in obj.modifiers if (m.show_viewport or m.show_render) and not _keeps_shape(m)), None)


def _bound_by_index(bpy, target):
    """What would move if the target's vertices were renumbered, as a cut does: a child parented to its vertices,
    or another object's modifier bound to its mesh."""
    for child in target.children:
        if child.parent_type in {"VERTEX", "VERTEX_3"}:
            return "%r is parented to its vertices" % child.name
    for user in bpy.data.user_map(subset=[target]).get(target, ()):
        for modifier in getattr(user, "modifiers", ()) if isinstance(user, bpy.types.Object) else ():
            field = BINDS_BY_INDEX.get(modifier.type)
            if field and getattr(modifier, field, None) == target:
                return "%r's %s modifier is bound to it" % (user.name, modifier.type.replace("_", " ").title())
            if modifier.type == "DATA_TRANSFER" and modifier.object == target and "TOPOLOGY" in {
                    getattr(modifier, key, "") for key in ("vert_mapping", "edge_mapping", "loop_mapping",
                                                           "poly_mapping")}:
                return "%r's Data Transfer maps its topology" % user.name
    return None


def refusal(bpy, target, cutter):
    """Why kit.cut must not cut ``target`` with ``cutter``, or None (module docstring)."""
    for obj, role in ((target, "the target"), (cutter, "the cutter")):
        if obj.type != "MESH":
            return "%s %r is not a mesh" % (role, obj.name)
        if obj.mode != "OBJECT" or obj.data.is_editmode:
            return "%s %r is in %s mode: cut in Object Mode" % (role, obj.name, obj.mode.replace("_", " ").title())
        shaping = _shaping(obj)
        if shaping is not None:
            return ("%s %r has a modifier that makes or moves geometry (%s %r), so a cut of its mesh would not be the "
                    "opening that is seen: keep its stack and add a Boolean modifier at the end of it, or ask the "
                    "person before applying anything" % (
                        role, obj.name, shaping.type.replace("_", " ").title(), shaping.name))
        if len(obj.data.vertices) > MAX_CUT_VERTICES:
            return "%s %r has over %d vertices: cut it another way" % (role, obj.name, MAX_CUT_VERTICES)
        if not len(obj.data.polygons):
            return "%s %r has no faces" % (role, obj.name)
        counts, loose = edge_faces(obj.data)
        open_edges = int((counts < 2).sum())
        if open_edges or loose:
            return ("%s %r is not a closed solid (%d open edges, %d loose vertices), so what is inside it is not "
                    "defined: close it, or cut another way" % (role, obj.name, open_edges, loose))
    if target.data.shape_keys is not None:
        return "%r has shape keys: cut it before adding them" % target.name
    bound = _bound_by_index(bpy, target)
    if bound:
        return "a cut renumbers the vertices of %r, and %s: ask the person, or cut another way" % (target.name, bound)
    if world_volume(target.data, None, signed=True) < 0:   # A room's inside: as a solid, its cut is not a doorway.
        return ("%r is wound inward (its faces point in), so it does not bound the solid that is seen: recalculate "
                "its normals outside first, or cut another way" % target.name)
    if target == cutter:
        return "the target and the cutter are the same object"
    return None


def _subset(mesh, keep, name):
    """A copy of ``mesh`` with only the vertices ``keep`` marks (and the faces between them)."""
    import bmesh
    import numpy
    out = mesh.copy()
    out.name = name
    if keep.all():
        return out
    bm = bmesh.new()
    bm.from_mesh(out)
    bm.verts.ensure_lookup_table()
    bmesh.ops.delete(bm, geom=[bm.verts[i] for i in numpy.flatnonzero(~keep).tolist()], context="VERTS")
    bm.to_mesh(out)
    bm.free()
    return out


def _boolean(bpy, work, tool, operation, overlapping):
    modifier = work.modifiers.new("angelo kit cut", "BOOLEAN")
    modifier.operation, modifier.object, modifier.solver = operation, tool, "EXACT"
    modifier.use_self = modifier.use_hole_tolerant = overlapping
    modifier.material_mode = "INDEX"
    depsgraph = bpy.context.evaluated_depsgraph_get()
    depsgraph.update()
    mesh = bpy.data.meshes.new_from_object(work.evaluated_get(depsgraph), depsgraph=depsgraph)
    work.modifiers.remove(modifier)
    return mesh


class _Unproven(ValueError):
    """A cut that ran but failed a proof: worth one try with self-intersection handling."""


def _materials(mesh, count):
    """``mesh`` is a copy of the target's, so its slots are the target's; a face past them (a cutter's) takes the
    last. (Clearing the slots would move every face to the first.)"""
    import numpy
    if len(mesh.polygons):
        index = numpy.empty(len(mesh.polygons), dtype=numpy.int32)
        mesh.polygons.foreach_get("material_index", index)
        top = max(count - 1, 0)
        if (index > top).any():
            mesh.polygons.foreach_set("material_index", numpy.minimum(index, top))


def _removed(bpy, work, tool, result, overlapping, crowded, v_near, v_overlap, matrix, made):
    """The volume the cut removed. Closed parts that do not overlap: what the target lost. Crowded parts (overlapping,
    touching or sharing faces) have no volume of their own to compare (an overlap counts twice, an inner face is not a
    boundary), so it is the overlap less what the result still holds inside the cutter: a second intersection, of the
    result. A difference that removed nothing still holds the whole overlap there."""
    if not crowded:
        return v_near - world_volume(result, matrix)
    near, work.data = work.data, result
    try:
        left = _boolean(bpy, work, tool, "INTERSECT", overlapping)
    finally:
        work.data = near
    made.append(left)
    return v_overlap - world_volume(left, matrix)


def _attempt(bpy, name, work, tool, pieces, overlapping, crowded, made):
    """One cut of the reached parts and its proofs: the joined mesh, or ValueError."""
    import bmesh
    near, far, bounds, reach, matrix = pieces
    overlap = _boolean(bpy, work, tool, "INTERSECT", overlapping)
    made.append(overlap)
    v_near, v_tool = world_volume(near, matrix), world_volume(tool.data, tool.matrix_world)
    v_overlap = world_volume(overlap, matrix)
    if v_overlap <= 1e-6 * max(min(v_near, v_tool), 1e-15):
        raise ValueError("kit.cut: the cutter does not overlap %r" % name)
    result = _boolean(bpy, work, tool, "DIFFERENCE", overlapping)
    made.append(result)
    # The volume removed is the overlap's, with or without self-intersection handling (a retry), crowded or not.
    removed = _removed(bpy, work, tool, result, overlapping, crowded, v_near, v_overlap, matrix, made)
    if abs(removed - v_overlap) > 0.01 * v_overlap + 1e-6 * v_near + 1e-12:
        raise _Unproven("kit.cut: the cut of %r removed %.6g m³, not the overlap's %.6g" % (name, removed, v_overlap))
    bm = bmesh.new()   # Into a copy of the target's own mesh: its slots, names and properties stay.
    if far is not None:
        bm.from_mesh(far)
    bm.from_mesh(result)
    joined = (far if far is not None else near).copy()
    made.append(joined)
    bm.to_mesh(joined)
    bm.free()
    after = world_bounds(joined, matrix) if joined.polygons else None
    if after is None:
        raise ValueError("kit.cut: nothing of %r would be left" % name)
    (lo, hi), (clo, chi), (nlo, nhi) = bounds, reach, after
    for a in range(3):   # The target may shrink only where the cutter reaches its extent, and never grows.
        if nlo[a] > lo[a] + 1e-3 and clo[a] > lo[a] + 1e-3 or nhi[a] < hi[a] - 1e-3 and chi[a] < hi[a] - 1e-3:
            raise _Unproven("kit.cut: %r would lose extent outside the cutter along %s" % (name, "XYZ"[a]))
        if nlo[a] < lo[a] - 1e-3 or nhi[a] > hi[a] + 1e-3:
            raise _Unproven("kit.cut: %r would grow along %s, which a cut never does" % (name, "XYZ"[a]))
    return joined


def cut_mesh(bpy, scene, target, cutter):
    """The target's base mesh minus the cutter, as a new mesh; raises ValueError, changing nothing, when the cut
    cannot be proven (module docstring)."""
    import numpy
    problem = refusal(bpy, target, cutter)
    if problem:
        raise ValueError("kit.cut: " + problem)
    base, tool_mesh = target.data.copy(), cutter.data.copy()
    made, objects = [base, tool_mesh], []
    try:
        for mesh in (base, tool_mesh):
            outward(mesh)
        matrix = target.matrix_world.copy()
        points = world_points(base, matrix)
        bounds = (tuple(points.min(axis=0)), tuple(points.max(axis=0)))
        reach = world_bounds(tool_mesh, cutter.matrix_world)
        _keys, inverse, parts = part_bounds(points, components(base))
        reached = [i for i, box in enumerate(parts) if _overlaps(box, reach)]
        if not reached:
            raise ValueError("kit.cut: the cutter does not overlap %r" % target.name)
        keep = numpy.isin(inverse, reached)
        near = _subset(base, keep, "angelo kit near")
        made.append(near)
        far = None if keep.all() else _subset(base, ~keep, "angelo kit far")
        if far is not None:
            made.append(far)
        crowded = (bool((edge_faces(near)[0] > 2).any())   # Shared or inner faces, or parts that overlap or touch.
                   or any(touching_pairs([parts[i] for i in reached], REACH).values()))
        work, tool = bpy.data.objects.new("angelo kit cut", near), bpy.data.objects.new("angelo kit cutter", tool_mesh)
        work.matrix_world, tool.matrix_world = matrix, cutter.matrix_world.copy()
        for obj in (work, tool):
            scene.collection.objects.link(obj)
            objects.append(obj)
        joined, failure = None, None
        retry = len(near.vertices) <= RETRY_VERTICES
        for overlapping in ((True,) if crowded else (False, True) if retry else (False,)):
            try:
                joined = _attempt(bpy, target.name, work, tool, (near, far, bounds, reach, matrix), overlapping,
                                  crowded, made)
                break
            except _Unproven as error:   # A miss or an empty result is final; a failed proof may be the solver's.
                failure = failure or error
        if joined is None:
            raise ValueError(str(failure))
        made.remove(joined)
        _materials(joined, len(target.data.materials))
        return joined
    finally:
        for obj in objects:
            bpy.data.objects.remove(obj, do_unlink=True)
        for mesh in made:
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
