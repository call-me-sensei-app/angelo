# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Collision for every engine: convex hulls and a convex decomposition as Game Physics colliders (game_physics.rst).

Game Physics (``io_scene_gltf2/angelo_physics``) fits a box, sphere, capsule, cylinder or one convex hull to a mesh
and writes it per engine. A concave mesh needs several hulls: one hull makes a chair a block and closes a doorway.
This module makes them, and is the one implementation the splat collision mesh (``splat_mesh``) and the game-ready
job share.

``fit(obj, method, max_hulls, max_hull_vertices)`` replaces the hulls it made below ``obj`` before and makes new
ones, each a child mesh object that is a Game Physics collider: ``angelo_physics.shape = 'CONVEX'``, Collision Only,
not rendered, drawn as wire. Every engine path then carries them: OMI for Godot (the glTF export), Godot name
suffixes, and Unreal's ``UCX_`` names (``unreal_name``) on the Unreal export's copy. The source mesh never changes.

* ``CONVEX``: one hull of the whole mesh.
* ``DECOMPOSE`` on a closed mesh: the solid is voxelised by ray parity along the columns of all three axes, a cell
  counting when the solid overlaps it at all, so a shell thinner than a voxel (a splat collision mesh) is still
  solid (one ray walk per column: the cost grows with the cross-section, not the volume). It is then split
  recursively: the part whose hull exceeds its solid by the most is cut across one of its principal axes where the
  two hulls' excess is smallest, until ``max_hulls`` parts or every part's excess is under 5% of the solid's volume.
  Each part's hull wraps the mesh's own surface in that part (its vertices, and points spread over its larger
  triangles) and, where it meets another part, the shared cells' corners: the hulls reach the real surface and
  overlap at the cuts, so nothing passes between them. (Voxel corners alone fell up to half a voxel short of a
  surface: the 2.1.0 suite covered 87% of a torus and 94% of a U channel.)
* ``DECOMPOSE`` on an open mesh: inside and outside are undefined, so each loose part gets its hull, and the
  report says the mesh is open.

Every hull is ``convex_hull.hull``'s: it holds every point it is made from (``bmesh.ops.convex_hull`` did not on a
decomposition's dense, nearly coplanar clouds: a torus part's hull held as little as 15% of its points). A hull above
``max_hull_vertices`` (engines cap convex hulls; PhysX at 255) is rebuilt as the farthest-first hull of that many
vertices (each step adds the point farthest outside, ``convex_hull.hull(max_vertices=...)``), then scaled about its
centre just enough to hold every point again: the fewer vertices may make it a little larger, never smaller than what
it collides for. A flat part (an open sheet) gets a slab a thousandth of its size thick; a line or a spot, no hull.
"""

import math

import bmesh
import bpy
import numpy as np
from bpy.props import EnumProperty, IntProperty
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

from . import convex_hull
from .ui_text import n_, rpt_

TAG = "angelo_collision_fit"   # Custom property on the hulls this module made: a rerun replaces exactly those.
RESOLUTION = 32                # Voxels along the longest side.
TOLERANCE = 0.05               # A part is done when its hull exceeds its solid by less than this share of the solid.


def unreal_name(mesh_name, index):
    """Unreal's convex collision name for a mesh's ``index``-th hull."""
    return "UCX_%s_%02d" % (mesh_name, index)


def _world_bmesh(obj):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    bm = bmesh.new()
    bm.from_object(obj, depsgraph)
    bm.transform(obj.matrix_world)
    return bm


SLAB = 1e-3   # A flat part's collider is a slab this share of its size thick (an open sheet still collides).


def _slab(points):
    """A planar cloud as a thin slab: each point both sides of its plane; None for a line or a spot."""
    centred = points - points.mean(axis=0)
    _, sizes, axes = np.linalg.svd(centred, full_matrices=False)
    if len(sizes) < 2 or sizes[1] <= convex_hull.tolerance(points) * max(len(points), 1) ** 0.5:
        return None
    half = 0.5 * SLAB * float(np.ptp(points, axis=0).max())
    return np.concatenate([points + half * axes[2], points - half * axes[2]])


def _hull_bmesh(points, max_vertices=None):
    """The convex hull of ``points`` (at most ``max_vertices``, farthest first) as a bmesh, faces wound outward
    (caller frees). A flat cloud gets a thin slab; a line or a spot, an empty bmesh."""
    bm = bmesh.new()
    points = np.asarray(points, dtype=np.float64)
    try:
        vertices, faces = convex_hull.hull(points, max_vertices)
    except convex_hull.Flat:
        slab = _slab(points) if len(points) >= 3 else None
        if slab is None:
            return bm
        points = slab
        vertices, faces = convex_hull.hull(points, max_vertices)
    made = {int(index): bm.verts.new(points[index]) for index in vertices}
    for face in faces:
        bm.faces.new([made[int(index)] for index in face])
    bm.normal_update()
    return bm


def _contain(bm, points):
    """Scale the hull ``bm`` about its centre until every one of ``points`` is inside it (never shrinks it)."""
    centre = np.array([v.co for v in bm.verts]).mean(axis=0)
    bm.normal_update()
    normals = np.array([face.normal for face in bm.faces], dtype=np.float64)
    reach = (normals * (np.array([face.verts[0].co for face in bm.faces]) - centre)).sum(axis=1)
    held = reach > 1e-12
    scale = 1.0
    if held.any():   # Every face at once: the farthest point past each face's plane, over that face's reach.
        scale = max(1.0, float(((points - centre) @ normals[held].T / reach[held]).max()))
    if scale > 1.0:
        bmesh.ops.scale(bm, vec=(scale, scale, scale), space=Matrix.Translation(-Vector(centre)), verts=bm.verts)


def hull(points, max_vertices):
    """A hull bmesh of at most ``max_vertices`` vertices holding every one of ``points`` (empty: no volume)."""
    points = np.asarray(points, dtype=np.float64)
    bm = _hull_bmesh(points)
    if len(bm.verts) > max_vertices:
        bm.free()
        bm = _hull_bmesh(points, max_vertices)   # The farthest-first hull of that many vertices, inside the full one,
        _contain(bm, points)                      # scaled just enough to hold every point.
    return bm


def _hull_volume(points):
    try:
        _, faces = convex_hull.hull(points)
    except convex_hull.Flat:
        return 0.0
    return convex_hull.volume(points, faces)


def _voxelise(bm):
    """Cells the closed mesh's solid overlaps, on a grid: (bool grid, origin, voxel size).

    Rays run along the columns of all three axes; between an entry and the next exit every cell the span touches
    counts, so a thin shell is found by the rays crossing it, whatever its orientation."""
    coords = np.array([v.co for v in bm.verts])
    lo, hi = coords.min(axis=0), coords.max(axis=0)
    size = float((hi - lo).max()) / RESOLUTION
    shape = np.maximum(np.ceil((hi - lo) / size).astype(int), 1)
    origin = lo
    tree = BVHTree.FromBMesh(bm)
    grid = np.zeros(shape, dtype=bool)
    for axis in range(3):
        u, v = [a for a in range(3) if a != axis]
        direction = Vector(np.eye(3)[axis])
        for i in range(shape[u]):
            for j in range(shape[v]):
                start = np.empty(3)
                start[axis], start[u], start[v] = origin[axis] - size, origin[u] + (i + .5) * size, \
                    origin[v] + (j + .5) * size
                start, hits = Vector(start), []
                while True:
                    location, _, _, _ = tree.ray_cast(start, direction)
                    if location is None:
                        break
                    hits.append(location[axis])
                    start = location + direction * (size * 1e-3)
                for enter, leave in zip(hits[0::2], hits[1::2]):
                    first, last = (np.clip(np.floor((np.array((enter, leave)) - origin[axis]) / size), 0,
                                           shape[axis] - 1).astype(int))
                    index = [i, j]
                    index.insert(axis, slice(first, last + 1))
                    grid[tuple(index)] = True
    return grid, origin, size


def _surface_points(bm, size):
    """The mesh's vertices plus points spread over each triangle wider than half a voxel (about that far apart)."""
    points = [np.array([v.co for v in bm.verts])]
    step = size / 2
    for tri in bm.calc_loop_triangles():
        a, b, c = (np.array(loop.vert.co) for loop in tri)
        k = int(math.ceil(max(np.linalg.norm(b - a), np.linalg.norm(c - b), np.linalg.norm(a - c)) / step))
        if k < 2:
            continue
        i, j = np.meshgrid(np.arange(k + 1), np.arange(k + 1), indexing="ij")
        keep = (i + j) <= k
        wa, wb = i[keep] / k, j[keep] / k
        points.append(a + np.outer(wa, b - a) + np.outer(wb, c - a))
    return np.concatenate(points)


OFFSETS = np.array([[x, y, z] for x in (-1, 0, 1) for y in (-1, 0, 1) for z in (-1, 0, 1)])
FACE_OFFSETS = np.array([[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]])


def _hull_points(parts, shape, origin, size, surface):
    """Per part: the surface points nearest its cells, and the corners of its cells that touch another part."""
    label = np.full(shape, -1, dtype=np.int64)
    for index, cells in enumerate(parts):
        label[tuple(cells.T)] = index
    base = np.floor((surface - origin) / size).astype(np.int64)
    owner, best = np.full(len(surface), -1), np.full(len(surface), np.inf)
    for offset in OFFSETS:   # The nearest part cell among the 27 around each point.
        cell = base + offset
        valid = np.all((cell >= 0) & (cell < shape), axis=1)
        here = np.full(len(surface), -1)
        here[valid] = label[tuple(cell[valid].T)]
        distance = np.linalg.norm(surface - (origin + (cell + 0.5) * size), axis=1)
        better = (here >= 0) & (distance < best)
        owner[better], best[better] = here[better], distance[better]
    result = []
    for index, cells in enumerate(parts):
        seam = np.zeros(len(cells), dtype=bool)
        for offset in FACE_OFFSETS:
            cell = cells + offset
            valid = np.all((cell >= 0) & (cell < shape), axis=1)
            other = np.full(len(cells), -1)
            other[valid] = label[tuple(cell[valid].T)]
            seam |= (other >= 0) & (other != index)
        points = np.concatenate([surface[owner == index], _corners(cells[seam], origin, size)])
        result.append(points if len(points) >= 4 else _corners(_boundary(cells), origin, size))
    return result


def _corners(cells, origin, size):
    """Corner points of the given voxel cells (n, 3 integer indices)."""
    offsets = np.array([[x, y, z] for x in (0, 1) for y in (0, 1) for z in (0, 1)])
    return (origin + (cells[:, None, :] + offsets[None, :, :]) * size).reshape(-1, 3)


def _boundary(cells):
    """The cells of a part with a face neighbour outside the part (its hull is the hull of these), vectorised."""
    lo = cells.min(axis=0)
    local = cells - lo + 1
    occupied = np.zeros(local.max(axis=0) + 2, dtype=bool)
    occupied[tuple(local.T)] = True
    interior = occupied.copy()
    for axis in range(3):
        interior[1:-1, 1:-1, 1:-1] &= np.roll(occupied, 1, axis)[1:-1, 1:-1, 1:-1]
        interior[1:-1, 1:-1, 1:-1] &= np.roll(occupied, -1, axis)[1:-1, 1:-1, 1:-1]
    keep = ~interior[tuple(local.T)]
    return cells[keep] if keep.any() else cells


def _excess(cells, origin, size):
    """How much a part's hull exceeds its solid (volume)."""
    return _hull_volume(_corners(_boundary(cells), origin, size)) - len(cells) * size ** 3


def _split(cells, origin, size):
    """The best cut of a part across one of its principal axes, as two cell arrays, or None."""
    centres = cells.astype(np.float64)
    centred = centres - centres.mean(axis=0)
    _, _, axes = np.linalg.svd(centred, full_matrices=False)
    best = None
    for axis in axes:
        along = centred @ axis
        for quantile in (0.25, 0.4, 0.5, 0.6, 0.75):
            cut = np.quantile(along, quantile)
            left, right = cells[along <= cut], cells[along > cut]
            if len(left) == 0 or len(right) == 0:
                continue
            cost = _excess(left, origin, size) + _excess(right, origin, size)
            if best is None or cost < best[0]:
                best = (cost, left, right)
    return None if best is None else (best[1], best[2])


def _decompose(bm, max_hulls):
    grid, origin, size = _voxelise(bm)
    shape = grid.shape
    cells = np.argwhere(grid)
    if len(cells) == 0:
        return []
    solid = len(cells) * size ** 3
    parts = [(cells, _excess(cells, origin, size))]
    while len(parts) < max_hulls:
        index = max(range(len(parts)), key=lambda i: parts[i][1])
        part, excess = parts[index]
        if excess < TOLERANCE * solid or len(part) < 2:
            break
        split = _split(part, origin, size)
        if split is None:
            break
        parts.pop(index)
        parts += [(piece, _excess(piece, origin, size)) for piece in split]
    return _hull_points([part for part, _ in parts], shape, origin, size, _surface_points(bm, size))


def _loose_parts(bm):
    bm.verts.ensure_lookup_table()
    seen, parts = set(), []
    for start in bm.verts:
        if start.index in seen:
            continue
        stack, part = [start], []
        seen.add(start.index)
        while stack:
            vert = stack.pop()
            part.append(vert.co.copy())
            for edge in vert.link_edges:
                other = edge.other_vert(vert)
                if other.index not in seen:
                    seen.add(other.index)
                    stack.append(other)
        parts.append(np.array(part))
    return parts


def _clear(obj):
    for child in [c for c in obj.children if c.get(TAG)]:
        mesh = child.data
        bpy.data.objects.remove(child)
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def _collider(obj, bm, index):
    bm.transform(obj.matrix_world.inverted())
    mesh = bpy.data.meshes.new("%s_Hull_%02d" % (obj.name, index))
    bm.to_mesh(mesh)
    child = bpy.data.objects.new(mesh.name, mesh)
    for collection in obj.users_collection:
        collection.objects.link(child)
    child.parent = obj
    child[TAG] = True
    child.hide_render = True
    child.display_type = 'WIRE'
    physics = child.angelo_physics
    physics.shape = 'CONVEX'
    physics.collision_only = True
    return child


def fit(obj, method='DECOMPOSE', max_hulls=8, max_hull_vertices=64):
    """Replace ``obj``'s fitted hulls with new ones; return (hull objects, report)."""
    _clear(obj)
    bm = _world_bmesh(obj)
    closed = len(bm.faces) > 0 and all(edge.is_manifold for edge in bm.edges)
    try:
        if method == 'CONVEX':
            point_sets = [np.array([v.co for v in bm.verts])]
        elif closed:
            point_sets = _decompose(bm, max_hulls)
        else:
            point_sets = _loose_parts(bm)[:max_hulls]
    finally:
        bm.free()
    hulls = []
    for index, points in enumerate(p for p in point_sets if len(p) >= 4):
        hull_bm = hull(points, max_hull_vertices)
        try:
            if len(hull_bm.faces):
                hulls.append(_collider(obj, hull_bm, index))
        finally:
            hull_bm.free()
    return hulls, {"hulls": len(hulls), "closed": closed, "method": method}


class ANGELO_OT_fit_collision(bpy.types.Operator):
    bl_idname = "angelo.fit_collision"
    bl_label = "Fit Collision Hulls"
    bl_description = "Fit convex collision hulls to the active mesh, as Game Physics colliders every engine reads"
    bl_options = {'REGISTER', 'UNDO'}

    method: EnumProperty(name="Method", items=(
        ('DECOMPOSE', "Decompose", "Several convex hulls that keep the mesh's hollows and openings"),
        ('CONVEX', "Convex Hull", "One convex hull around the whole mesh"),
    ), default='DECOMPOSE')
    max_hulls: IntProperty(name="Most Hulls", default=8, min=1, max=64)
    max_hull_vertices: IntProperty(name="Most Vertices per Hull", default=64, min=8, max=255)

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and hasattr(obj, "angelo_physics")

    def execute(self, context):
        hulls, report = fit(context.active_object, self.method, self.max_hulls, self.max_hull_vertices)
        if not report["closed"] and self.method == 'DECOMPOSE':
            self.report({'WARNING'}, rpt_("The mesh is open, so each loose part got one hull ({count} in all)",
                                          count=len(hulls)))
        else:
            self.report({'INFO'}, rpt_("{count} collision hulls", count=len(hulls)))
        return {'FINISHED'}


TITLE = n_("Collision hulls")
WHY = n_("Convex collision for Unreal, Unity and Godot that keeps hollows open")


def register():
    from . import chat_destinations
    bpy.utils.register_class(ANGELO_OT_fit_collision)
    chat_destinations.register(chat_destinations.Feature(   # The chat bar's + menu: Angelo's main navigation.
        "collision_hulls", TITLE, WHY, order=52, section=n_("Share"),
        operator=ANGELO_OT_fit_collision.bl_idname, owner="collision_fit"))


def unregister():
    from . import chat_destinations
    chat_destinations.unregister("collision_hulls")
    bpy.utils.unregister_class(ANGELO_OT_fit_collision)
