# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The game-ready check's meshes: a Blender object read into world-space arrays, and its reductions made in memory.
Nothing here changes the scene or any data-block.

- ``read`` (main thread) takes the geometry an export would take: the evaluated mesh (modifiers applied), except for a
  mesh an Armature modifier deforms, which is read as its base mesh in rest pose with its deforming groups' weights.
  It copies the arrays out and, for Decimate, takes a snapshot of the mesh (``_angelo_reduce.snapshot``: no Main,
  arrays shared), then frees what it evaluated; ``MeshRead.finish`` makes the world-space geometry on any thread.
- ``Reducer`` welds the triangle corners into the vertex buffer an engine would get (one vertex per distinct
  position, UV, shading normal and material), then reduces it with meshoptimizer (``_bpy._angelo_reduce.simplify``)
  to index buffers over that same buffer, so every LOD of the chain shares LOD0's vertices. Seams, hard edges and
  material borders are split vertices, which the simplifier never collapses across.
- ``decimate`` runs Blender's own Decimate (Collapse) on the snapshot for comparison, on any thread.
"""

import time

import numpy as np

from .game_ready_metrics import Geometry

MAX_TRIANGLES = 2_000_000   # The measurements hold float64 copies per triangle corner in memory.


class MeshRead:
    """One object's arrays as read, how it was read, and its snapshot for Decimate (None when not asked for);
    ``geometry`` once ``finish`` made it."""

    def __init__(self, raw, note, snapshot, groups, group_names, matrix, others, unit_scale):
        self.raw, self.note, self.snapshot, self.groups, self.group_names = raw, note, snapshot, groups, group_names
        self.matrix, self.others, self.unit_scale, self.geometry = matrix, others, unit_scale, None

    def finish(self):
        """The world-space geometry from the arrays read (any thread); ``self``."""
        if self.geometry is None:
            self.geometry, self.groups, self.group_names = _geometry(self)
            self.raw = None
        return self

    def release(self):
        """Drop the snapshot (freed with its last reference)."""
        self.snapshot = None


def _armature_modifier(obj):
    return next((m for m in obj.modifiers if m.type == "ARMATURE" and m.object is not None and m.show_viewport), None)


def deforming_groups(obj, modifier):
    """Indices and names of the vertex groups the armature's deforming bones drive."""
    bones = {bone.name for bone in modifier.object.data.bones if bone.use_deform}
    found = [(group.index, group.name) for group in obj.vertex_groups if group.name in bones]
    return [index for index, _ in found], [name for _, name in found]


def _arrays(mesh):
    tris = mesh.loop_triangles
    count = len(tris)
    vertex_tris = np.empty(count * 3, dtype=np.int32)
    tris.foreach_get("vertices", vertex_tris)
    corner_tris = np.empty(count * 3, dtype=np.int32)
    tris.foreach_get("loops", corner_tris)
    materials = np.empty(count, dtype=np.int32)
    tris.foreach_get("material_index", materials)
    positions = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", positions)
    normals = np.empty(len(mesh.loops) * 3, dtype=np.float32)
    mesh.corner_normals.foreach_get("vector", normals)
    uv = None
    layer = mesh.uv_layers.active
    if layer is not None:
        uv = np.empty(len(mesh.loops) * 2, dtype=np.float32)
        layer.uv.foreach_get("vector", uv)
        uv = uv.reshape(-1, 2)
    return (positions.reshape(-1, 3), vertex_tris.reshape(-1, 3), corner_tris.reshape(-1, 3), materials,
            normals.reshape(-1, 3), uv)


def to_world(positions, normals, matrix, unit_scale):
    """Positions and normals in world space and meters."""
    m = np.asarray(matrix, dtype=np.float64)
    world = (positions.astype(np.float64) @ m[:3, :3].T + m[:3, 3]) * unit_scale
    out_normals = None
    if normals is not None:
        inverse_transpose = np.linalg.inv(m[:3, :3]).T if abs(np.linalg.det(m[:3, :3])) > 0 else m[:3, :3]
        out_normals = normals.astype(np.float64) @ inverse_transpose.T
        out_normals /= np.maximum(np.linalg.norm(out_normals, axis=-1, keepdims=True), 1e-12)
    return world.astype(np.float32), None if out_normals is None else out_normals.astype(np.float32)


def read(obj, depsgraph, unit_scale, reduce, snapshot=False):
    """``obj`` as a MeshRead (main thread): its arrays, and with ``snapshot`` its mesh's snapshot for Decimate."""
    modifier = _armature_modifier(obj)
    groups, names = [], []
    owner = None
    others = 0
    if modifier is not None:
        mesh = obj.data
        groups, names = deforming_groups(obj, modifier)
        others = sum(1 for m in obj.modifiers if m.show_viewport and m != modifier)
        note = ("the base mesh in rest pose, because an Armature modifier deforms it (%d other modifiers not "
                "applied), with the weights of the deforming groups that weigh something on it" % others)
    else:
        owner = obj.evaluated_get(depsgraph)
        mesh = owner.to_mesh()
        note = "the evaluated mesh (every enabled modifier applied), as an export takes it"
    try:   # The evaluated mesh is freed however this ends; the snapshot shares its arrays and outlives it.
        if len(mesh.loop_triangles) > MAX_TRIANGLES:
            raise ValueError("%s has %d triangles; the check measures up to %d" % (
                obj.name[:64], len(mesh.loop_triangles), MAX_TRIANGLES))
        raw = _arrays(mesh)
        weights = reduce.vertex_weights(mesh, groups) if groups else None
        return MeshRead(raw + (weights,), note, reduce.snapshot(mesh) if snapshot else None, groups, names,
                        obj.matrix_world.copy(), others if modifier is not None else 0, unit_scale)
    finally:
        if owner is not None:
            owner.to_mesh_clear()


def _geometry(read_mesh):
    """(Geometry, groups, group names) of a MeshRead's arrays, in world space: NumPy only, any thread."""
    positions, vertex_tris, corner_tris, materials, corner_normals, uv, weight_bytes = read_mesh.raw
    world, normals = to_world(positions, corner_normals, read_mesh.matrix, read_mesh.unit_scale)
    groups, names, weights = read_mesh.groups, read_mesh.group_names, None
    if groups:
        weights = np.frombuffer(weight_bytes, dtype=np.float32).reshape(len(positions), -1)
        # Only groups that weigh something on this mesh move it: a rig's other bones are not its influences.
        used = np.flatnonzero((weights > 0.0).any(axis=0))
        weights = np.ascontiguousarray(weights[:, used])
        groups, names = [groups[i] for i in used], [names[i] for i in used]
    geometry = Geometry(world, vertex_tris, None if uv is None else uv[corner_tris], normals[corner_tris], weights,
                        weight_names=names if groups else None)
    geometry.modifiers_not_applied = read_mesh.others
    geometry.corner_material = np.repeat(materials, 3)   # The weld splits material borders.
    return geometry, groups, names


class Reducer:
    """LOD0's vertex buffer, welded once, and meshoptimizer reductions over it."""

    def __init__(self, read_mesh, reduce, uv_weight, normal_weight, skin_weight):
        geometry = read_mesh.geometry
        started = time.perf_counter()
        corners = geometry.tris.reshape(-1)
        streams = [corners.astype(np.uint32), geometry.corner_normal.reshape(-1, 3),
                   geometry.corner_material.astype(np.int32)]
        if geometry.corner_uv is not None:
            streams.append(geometry.corner_uv.reshape(-1, 2))
        remap_bytes, unique = reduce.weld(len(corners), [np.ascontiguousarray(s) for s in streams])
        remap = np.frombuffer(remap_bytes, dtype=np.uint32)
        first = np.empty(unique, dtype=np.int64)
        first[remap[::-1]] = np.arange(len(remap) - 1, -1, -1)   # The first corner of each welded vertex.
        self.vertex_ids = corners[first].astype(np.int64)
        self.positions = np.ascontiguousarray(geometry.positions[self.vertex_ids])
        self.normals = geometry.corner_normal.reshape(-1, 3)[first]
        self.uvs = None if geometry.corner_uv is None else geometry.corner_uv.reshape(-1, 2)[first]
        self.weights = None if geometry.weights is None else geometry.weights[self.vertex_ids]
        self.weight_names = geometry.weight_names
        self.indices = remap
        self.weld_ms = (time.perf_counter() - started) * 1000.0
        parts, weights = [], []
        for values, weight in ((self.uvs, uv_weight), (self.normals, normal_weight), (self.weights, skin_weight)):
            if values is not None and weight > 0 and values.shape[1]:
                parts.append(values)
                weights += [weight] * values.shape[1]
        self.attribute_count = len(weights)
        self.attributes = np.ascontiguousarray(np.concatenate(parts, axis=1), dtype=np.float32) if parts else None
        self.attribute_weights = np.asarray(weights, dtype=np.float32) if weights else None
        self.reduce = reduce

    def refusal(self):
        """Why meshoptimizer can't keep this mesh's attributes, or None."""
        if self.attribute_count > self.reduce.MAX_ATTRIBUTES:
            groups = 0 if self.weights is None else self.weights.shape[1]
            return ("%d deforming vertex groups weigh something on this mesh; with its UVs and normals that is %d attributes, over "
                    "meshoptimizer's %d, so its skin weights could not be kept by the reduction's error measure. "
                    "Blender's Decimate (DECIMATE) interpolates them instead" % (groups, self.attribute_count,
                                                                                 self.reduce.MAX_ATTRIBUTES))
        return None

    def lod0(self):
        """The welded vertex buffer and its full index buffer, as an engine would receive LOD0."""
        return self.geometry(self.indices)

    def geometry(self, indices):
        tris = np.asarray(indices, dtype=np.uint32).reshape(-1, 3)
        return Geometry(self.positions, tris,
                        None if self.uvs is None else self.uvs[tris],
                        self.normals[tris],
                        self.weights, vertex_ids=self.vertex_ids, weight_names=self.weight_names)

    def simplify(self, ratio, max_error, lock_border):
        """Indices over LOD0's vertices at ``ratio`` of its triangles, its relative error and the time taken."""
        target = int(len(self.indices) // 3 * ratio) * 3
        options = self.reduce.OPTION_LOCK_BORDER if lock_border else 0
        started = time.perf_counter()
        indices, error = self.reduce.simplify(
            self.positions, self.indices, target, 1.0 if max_error is None else max_error, mode="ATTRIBUTES",
            attributes=self.attributes, weights=self.attribute_weights, options=options)
        return np.frombuffer(indices, dtype=np.uint32), error, (time.perf_counter() - started) * 1000.0


def decimate(read_mesh, ratio, reduce, unit_scale):
    """Blender's Decimate (Collapse, triangulated) of the read mesh's snapshot as a Geometry, and the time it took
    (any thread: the snapshot is the check's own)."""
    started = time.perf_counter()
    result = reduce.decimate(read_mesh.snapshot, ratio, triangulate=True, groups=read_mesh.groups or None)
    elapsed = (time.perf_counter() - started) * 1000.0
    positions = np.frombuffer(result["positions"], dtype=np.float32).reshape(-1, 3)
    tris = np.frombuffer(result["tris"], dtype=np.int32).reshape(-1, 3)
    normals = np.frombuffer(result["corner_normals"], dtype=np.float32).reshape(-1, 3)
    world, world_normals = to_world(positions, normals, read_mesh.matrix, unit_scale)
    uv = None if result["corner_uvs"] is None else np.frombuffer(result["corner_uvs"], dtype=np.float32)
    weights = None
    if result["weights"] is not None:
        weights = np.frombuffer(result["weights"], dtype=np.float32).reshape(len(positions), -1)
    return Geometry(world, tris, uv, world_normals, weights,
                    weight_names=read_mesh.group_names if weights is not None else None), elapsed
