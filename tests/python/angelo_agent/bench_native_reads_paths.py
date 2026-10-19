# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every native read Angelo adds that stock Blender can also do, as pairs of routes (``bench_native_reads``).

Each ``Path`` names what is read, the unit its time is given in, the stock
Python routes (all of them are timed; the fastest supported one is the
comparison) and Angelo's route (None in a stock Blender, where it does not
exist). Every route of a path returns the same data, so ``parity`` can prove
that the comparison is like for like before anything is timed.

The routes take a ``Setup`` (the scene's meshes and objects, prepared once)
and do the whole unit of work in one call. A route that needs Sculpt Mode
gets the statue in Sculpt Mode; nothing here writes the scene.
"""

import ctypes
import os
import struct
from collections import namedtuple

Route = namedtuple("Route", "label call supported")
Path = namedtuple("Path", "id label unit per_vertex stock angelo note")


def _numpy():
    import numpy
    return numpy


# ---------------------------------------------------------------- positions

def positions_vertices(setup):
    numpy, out = _numpy(), []
    for mesh in setup.meshes:
        buffer = numpy.empty(len(mesh.vertices) * 3, numpy.float32)
        mesh.vertices.foreach_get("co", buffer)
        out.append(buffer)
    return out


def positions_attribute(setup):
    numpy, out = _numpy(), []
    for mesh in setup.meshes:
        buffer = numpy.empty(len(mesh.vertices) * 3, numpy.float32)
        mesh.attributes["position"].data.foreach_get("vector", buffer)
        out.append(buffer)
    return out


def positions_pointer(setup):
    """Unsupported: one raw memory copy from the attribute's first element (``as_pointer``)."""
    numpy, out = _numpy(), []
    for mesh in setup.meshes:
        buffer = numpy.empty(len(mesh.vertices) * 3, numpy.float32)
        if len(buffer):
            ctypes.memmove(buffer.ctypes.data, mesh.attributes["position"].data[0].as_pointer(), buffer.nbytes)
        out.append(buffer)
    return out


def positions_angelo(setup):
    numpy = _numpy()
    return [numpy.frombuffer(mesh.angelo_sculpt_positions, numpy.float32) for mesh in setup.meshes]


# ---------------------------------------------------------------- vertex-group weights

def weights_walk(setup):
    """The per-vertex walk, packed as Angelo's buffer is: (vertex int32, group int32, weight float32) records."""
    pack, out = struct.Struct("<iif").pack, []
    for mesh in setup.weighted:
        out.append(b"".join(pack(vertex.index, group.group, group.weight) for vertex in mesh.vertices
                            for group in vertex.groups))
    return out


def weights_bmesh(setup):
    """bmesh's deform layer, the fastest stock read found; the same records."""
    import bmesh
    pack, out = struct.Struct("<iif").pack, []
    for mesh in setup.weighted:
        data = bmesh.new()
        try:
            data.from_mesh(mesh)
            layer = data.verts.layers.deform.active
            out.append(b"".join(pack(vertex.index, group, weight) for vertex in data.verts
                                for group, weight in vertex[layer].items()))
        finally:
            data.free()
    return out


def weights_angelo(setup):
    return [mesh.angelo_vertex_group_weights for mesh in setup.weighted]


# ---------------------------------------------------------------- topology

def _offsets(numpy, sizes):
    offsets = numpy.zeros(len(sizes) + 1, numpy.int32)
    numpy.cumsum(sizes, out=offsets[1:])
    return offsets


def topology_elements(setup):
    numpy, out = _numpy(), []
    for mesh in setup.meshes:
        corners = numpy.empty(len(mesh.loops), numpy.int32)
        mesh.loops.foreach_get("vertex_index", corners)
        sizes = numpy.empty(len(mesh.polygons), numpy.int32)
        mesh.polygons.foreach_get("loop_total", sizes)
        out.append(corners.tobytes() + _offsets(numpy, sizes).tobytes())
    return out


def topology_attribute(setup):
    numpy, out = _numpy(), []
    for mesh in setup.meshes:
        corners = numpy.empty(len(mesh.loops), numpy.int32)
        mesh.attributes[".corner_vert"].data.foreach_get("value", corners)
        offsets = numpy.empty(len(mesh.polygons) + 1, numpy.int32)
        mesh.polygons.foreach_get("loop_start", offsets[:-1])
        offsets[-1] = len(mesh.loops)
        out.append(corners.tobytes() + offsets.tobytes())
    return out


def topology_angelo(setup):
    return [mesh.angelo_sculpt_topology for mesh in setup.meshes]


# ---------------------------------------------------------------- sculpt-session reads (the statue in Sculpt Mode)

def sculpt_mesh_evaluated(setup):
    obj = setup.sculpt
    mesh = obj.evaluated_get(setup.depsgraph()).to_mesh()
    try:
        return len(mesh.vertices)
    finally:
        obj.evaluated_get(setup.depsgraph()).to_mesh_clear()


def sculpt_mesh_angelo(setup):
    obj = setup.sculpt
    mesh = obj.angelo_sculpt_mesh()
    try:
        return len(mesh.vertices)
    finally:
        obj.to_mesh_clear()


def _rays(setup):
    return setup.rays


def query_ray_cast(setup):
    obj, graph = setup.sculpt, setup.depsgraph()
    return [tuple(round(v, 4) for v in obj.ray_cast(origin, direction, distance=50.0, depsgraph=graph)[1])
            for origin, direction in _rays(setup)]


def query_angelo(setup):
    obj = setup.sculpt
    return [tuple(round(v, 4) for v in obj.angelo_sculpt_query(origin, direction=direction, distance=50.0)[1])
            for origin, direction in _rays(setup)]


def vertex_rna(setup):
    vertices = setup.sculpt.data.vertices
    return [tuple(round(v, 5) for v in vertices[index].co) for index in setup.indices]


def vertex_angelo(setup):
    read = setup.sculpt.angelo_sculpt_vertex   # Looked up once, as vertex_rna looks up .data.vertices once.
    return [tuple(round(v, 5) for v in read(index)) for index in setup.indices]


# ---------------------------------------------------------------- BVH from a mesh (the terrain)

def bvh_from_object(setup):
    from mathutils.bvhtree import BVHTree
    return BVHTree.FromObject(setup.terrain, setup.depsgraph()).find_nearest((0.0, 0.0, 50.0))[2]


def bvh_from_mesh(setup):
    from mathutils.bvhtree import BVHTree
    return BVHTree.FromMesh(setup.terrain.data).find_nearest((0.0, 0.0, 50.0))[2]


# ---------------------------------------------------------------- small native helpers

def groups_per_vertex(setup):
    vertices = setup.weighted[0].vertices
    return [tuple((group.group, round(group.weight, 5)) for group in vertices[index % len(vertices)].groups)
            for index in setup.indices]


def groups_helper(setup):
    from _bpy import _angelo_read
    mesh = setup.weighted[0]
    read, count = _angelo_read.vertex_groups, len(mesh.vertices)
    return [tuple((group, round(weight, 5)) for group, weight in read(mesh, index % count, 0, 64)["items"])
            for index in setup.indices]


def image_rna(setup):
    image = setup.image
    return [(tuple(image.size), image.channels, image.depth, image.is_float) for _ in setup.indices]


def image_angelo(setup):
    from _bpy import _angelo_read
    read, image, out = _angelo_read.image_metadata, setup.image, []
    for _ in setup.indices:
        facts = read(image)
        out.append((tuple(facts["size"]), facts["channels"], facts["depth"], facts["is_float"]))
    return out


def listing_library(setup):
    import bpy
    with bpy.data.libraries.load(str(setup.listing), link=False) as (source, _into):
        return sorted(source.objects)


def listing_angelo(setup):
    """The scan ``angelo_blend_file`` makes (``blend_file_listing``), with its limits and deadline. Blender
    writes one block per weighted vertex, so this file has 1.1 million blocks per pass; with a system call per
    block the scan once took 6.5 times the stock listing and its 2,000,000-block limit refused the file."""
    from _bpy import _angelo_read
    from angelo_agent.blend_file_listing import DEADLINE_SECONDS, SCAN_LIMITS
    descriptor = os.open(str(setup.listing), os.O_RDONLY | getattr(os, "O_BINARY", 0))
    try:
        token = _angelo_read.metadata_cancel_token(DEADLINE_SECONDS)
        scanned = _angelo_read.metadata_scan(descriptor, {"list": "objects", "offset": 0, "limit": 4000,
                                                          "summary_names": 12, "libraries_limit": 32},
                                             dict(SCAN_LIMITS), token)
    finally:
        os.close(descriptor)
    if scanned.get("status") != "OK":
        raise RuntimeError("metadata_scan: %s (blocks read %s)" % (scanned.get("status"), scanned.get("blocks")))
    return sorted(scanned["types"]["objects"]["names"])


listing_product = listing_angelo


PATHS = (
    Path("positions", "Vertex positions", "per million vertices", True,
         (Route("MeshVertices.foreach_get('co', float32 array)", positions_vertices, True),
          Route("Mesh.attributes['position'].data.foreach_get('vector', preallocated float32 array)",
                positions_attribute, True),
          Route("ctypes.memmove from Mesh.attributes['position'].data[0].as_pointer() (unsupported)",
                positions_pointer, False)),
         Route("Mesh.angelo_sculpt_positions (bytes, one copy)", positions_angelo, True), ""),
    Path("vertex_group_weights", "Vertex-group weights", "per million vertices", True,
         (Route("for vertex in mesh.vertices: for group in vertex.groups (packed records)", weights_walk, True),
          Route("bmesh deform layer: vertex[layer].items() (packed records)", weights_bmesh, True)),
         Route("Mesh.angelo_vertex_group_weights (bytes of records)", weights_angelo, True),
         "Stock Python has no bulk vertex-group read."),
    Path("topology", "Face corners and offsets", "per million vertices", False,
         (Route("MeshLoops.foreach_get('vertex_index') + MeshPolygons.foreach_get('loop_total')", topology_elements,
                True),
          Route("Mesh.attributes['.corner_vert'].data.foreach_get('value') + MeshPolygons.foreach_get('loop_start')",
                topology_attribute, True)),
         Route("Mesh.angelo_sculpt_topology (bytes)", topology_angelo, True), "Reads every face corner."),
    Path("sculpt_mesh", "The mesh being sculpted", "per check", False,
         (Route("Object.evaluated_get(depsgraph).to_mesh()", sculpt_mesh_evaluated, True),),
         Route("Object.angelo_sculpt_mesh()", sculpt_mesh_angelo, True),
         "The statue in Sculpt Mode; the stock route re-evaluates the object."),
    Path("sculpt_query", "1,000 ray hits on the mesh being sculpted", "per check", False,
         (Route("Object.ray_cast(origin, direction, depsgraph=...)", query_ray_cast, True),),
         Route("Object.angelo_sculpt_query(origin, direction=..., distance=...)", query_angelo, True),
         "Not the same work: Angelo's query reads the live sculpt surface (Multires grids, current deformation, "
         "hidden faces skipped) without evaluating; ray_cast reads the evaluated mesh, which a stroke does not "
         "update, which is the Multires cage in Sculpt Mode and which includes hidden faces. 1.0 answers as RC7 "
         "does, testing every triangle of each PBVH leaf the ray reaches; a faster per-leaf index waits until it "
         "is proven exact."),
    Path("sculpt_vertex", "1,000 vertex positions while sculpting", "per check", False,
         (Route("Mesh.vertices[i].co", vertex_rna, True),),
         Route("Object.angelo_sculpt_vertex(i)", vertex_angelo, True), ""),
    Path("bvh_from_mesh", "A ray-cast tree of the 692,224-vertex terrain", "per check", False,
         (Route("mathutils.bvhtree.BVHTree.FromObject(object, depsgraph)", bvh_from_object, True),),
         Route("mathutils.bvhtree.BVHTree.FromMesh(mesh)", bvh_from_mesh, True), ""),
    Path("vertex_groups_one", "1,000 single-vertex group reads", "per check", False,
         (Route("Mesh.vertices[i].groups", groups_per_vertex, True),),
         Route("_bpy._angelo_read.vertex_groups(mesh, i, 0, 64)", groups_helper, True), ""),
    Path("image_metadata", "1,000 image size and format reads", "per check", False,
         (Route("Image.size, .channels, .depth, .is_float", image_rna, True),),
         Route("_bpy._angelo_read.image_metadata(image)", image_angelo, True),
         "A loaded image; the stock getters would also load an unloaded one."),
    Path("blend_listing", "List another .blend's objects (a copy of the scene's file)", "per check", False,
         (Route("bpy.data.libraries.load(path) as (source, into): source.objects", listing_library, True),),
         Route("_bpy._angelo_read.metadata_scan(descriptor, ...) with the product's own limits", listing_angelo,
               True), "angelo_blend_file's scan, its limits included (blend_file_listing.SCAN_LIMITS)."),
)

# Reads compared here once and retired since, with their last numbers, so the table hides nothing.
RETIRED = (
    {"id": "stored_property", "label": "1,000 registered-property reads", "unit": "per check",
     "retired": "retired in 1.0: replaced by the address-ABI reads",
     "stock_route": "Scene.angelo_bench_value (a bpy.props IntProperty, no getter)",
     "angelo_route": "_bpy._angelo_read.stored_property(scene, ('angelo_bench_value',))",
     "measurements": [
         {"angelo_commit": "a7fa84aeaa2", "stock_ms": 0.057, "angelo_ms": 0.275,
          "instructions": {"stock": 1097333, "angelo": 5865433}, "note": "the first comparison"},
         {"angelo_commit": "e07c7e1b208", "stock_ms": 0.055, "angelo_ms": 0.116,
          "instructions": {"stock": 1097369, "angelo": 2468623},
          "note": "its record built from interned words; the last build that had it"}],
     "note": "It read raw system storage (no getter, transform or item callback, nothing created or repaired) "
             "and answered with a record (state, type, value), which alone costs about as much as stock's whole "
             "read. Nothing in the product called it after automatic reads moved to the address-read ABI "
             "(angelo_read_data), which reads stored members with the same guarantees."},
)
