# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Evaluating the same object twice gives the same bits, wherever the heap puts its elements.

Anything that compares an evaluated mesh across evaluations (``export_baked``'s freshness digest, a cache key, a
re-export) needs re-evaluation to be bit-identical when nothing changed. Bevel averaged each UV vertex's corners by
iterating a pointer-hashed ``Set<BMLoop *>``, so the float sum's order followed heap addresses: on Windows, where the
heap places BMesh chunks at 16-byte granularity, a rename re-evaluated a beveled cube with UV values one ulp apart
and its baked material read as stale (the 1.0.9 cut's Windows release checks, ``baked_export_stale``). macOS places
the chunks on 512-byte boundaries, so the order happened to repeat there.

Checks, in the real binary (background; writes results.json under ANGELO_QA_DIR): two meshes (a cube with seams'
UVs and a colour attribute, a UV sphere) under each evaluator below, re-evaluated ``ROUNDS`` times with the heap
churned between rounds (Blender meshes and plain blocks of random sizes made and freed) and the object renamed
(a full re-evaluation). Every point, corner and face attribute, the corner normals and the positions must hash the
same every round. Bevel is the case that exposed this; every other evaluator is held out. One held-out evaluator
failed too, on the 1.0.9 cut 4's T4 round (Windows, GPU VM): the Fast Boolean (``boolean float``) split its edges in
a pointer-keyed GHash's order (bmesh_intersect.cc), 2 distinct results in 12 rounds; build4 and macOS had repeated.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import hashlib
import json
import os
import random
import tempfile
from pathlib import Path

import bmesh
import bpy
import numpy

ROUNDS = 12
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-evaluation-repeatable-qa"))
OUT.mkdir(parents=True, exist_ok=True)
FIELDS = {"FLOAT": ("value", 1, numpy.float32), "INT": ("value", 1, numpy.int32),
          "FLOAT_VECTOR": ("vector", 3, numpy.float32), "FLOAT_COLOR": ("color", 4, numpy.float32),
          "BYTE_COLOR": ("color", 4, numpy.float32), "FLOAT2": ("vector", 2, numpy.float32),
          "BOOLEAN": ("value", 1, bool), "INT8": ("value", 1, numpy.int32)}
# (name, modifier type, settings). Several Bevel shapes: the UV merge runs wherever corners meet.
MODIFIERS = (
    ("bevel", "BEVEL", {"width": 0.1, "segments": 3}),
    ("bevel vertices", "BEVEL", {"affect": "VERTICES", "width": 0.1, "segments": 2}),
    ("bevel harden normals", "BEVEL", {"width": 0.05, "segments": 2, "harden_normals": True}),
    ("bevel profile", "BEVEL", {"width": 0.2, "segments": 4, "profile": 0.8, "miter_outer": "MITER_ARC"}),
    ("subdivision", "SUBSURF", {"levels": 2}),
    ("subdivision uv boundaries", "SUBSURF", {"levels": 2, "uv_smooth": "SMOOTH_ALL"}),
    ("solidify", "SOLIDIFY", {"thickness": 0.05}),
    ("solidify complex", "SOLIDIFY", {"thickness": 0.05, "solidify_mode": "NON_MANIFOLD"}),
    ("weld", "WELD", {"merge_threshold": 0.01}),
    ("triangulate", "TRIANGULATE", {}),
    ("decimate", "DECIMATE", {"ratio": 0.5}),
    ("decimate planar", "DECIMATE", {"decimate_type": "DISSOLVE"}),
    ("remesh", "REMESH", {"mode": "SMOOTH", "octree_depth": 4}),
    ("voxel remesh", "REMESH", {"mode": "VOXEL", "voxel_size": 0.1}),
    ("smooth", "SMOOTH", {"iterations": 4}),
    ("laplacian smooth", "LAPLACIANSMOOTH", {"iterations": 3}),
    ("wireframe", "WIREFRAME", {"thickness": 0.02}),
    ("mirror merge", "MIRROR", {"use_mirror_merge": True}),
    ("array merge", "ARRAY", {"count": 3, "use_merge_vertices": True}),
    ("displace", "DISPLACE", {"strength": 0.1}),
    ("edge split", "EDGE_SPLIT", {}),
    ("cast", "CAST", {"factor": 0.5}),
)


def _cube():
    mesh = bpy.data.meshes.new("Cube")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=2, use_grid_fill=True)
    bm.to_mesh(mesh)
    bm.free()
    layer = mesh.uv_layers.new(name="UVMap")
    for polygon in mesh.polygons:   # One island per face: corners of a vertex sit in different islands.
        normal = polygon.normal
        axes = (1, 2) if abs(normal.x) > 0.5 else (0, 2) if abs(normal.y) > 0.5 else (0, 1)
        for index in polygon.loop_indices:
            co = mesh.vertices[mesh.loops[index].vertex_index].co
            layer.data[index].uv = (co[axes[0]] * 0.25 + 0.5, co[axes[1]] * 0.25 + 0.5)
    colour = mesh.color_attributes.new("Col", "BYTE_COLOR", "CORNER")
    for index, item in enumerate(colour.data):
        item.color = ((index % 7) / 7.0, (index % 5) / 5.0, (index % 3) / 3.0, 1.0)
    return mesh


def _sphere():
    mesh = bpy.data.meshes.new("Sphere")
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=24, v_segments=12, radius=1.0, calc_uvs=True)
    bm.to_mesh(mesh)
    bm.free()
    return mesh


def _geometry_nodes(obj):
    """A held-out node evaluator: subdivide, displace by noise, merge, extrude and triangulate."""
    group = bpy.data.node_groups.new("Repeat", "GeometryNodeTree")
    group.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    group.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nodes, links = group.nodes, group.links
    chain = [nodes.new("NodeGroupInput")]
    for kind in ("GeometryNodeSubdivideMesh", "GeometryNodeSetPosition", "GeometryNodeMergeByDistance",
                 "GeometryNodeExtrudeMesh", "GeometryNodeTriangulate"):
        chain.append(nodes.new(kind))
    chain.append(nodes.new("NodeGroupOutput"))
    for left, right in zip(chain, chain[1:]):
        links.new(left.outputs[0], right.inputs[0])
    noise, scale = nodes.new("ShaderNodeTexNoise"), nodes.new("ShaderNodeVectorMath")
    scale.operation = "SCALE"
    scale.inputs["Scale"].default_value = 0.05
    links.new(noise.outputs["Color"], scale.inputs[0])
    links.new(scale.outputs["Vector"], chain[2].inputs["Offset"])
    chain[3].inputs["Distance"].default_value = 0.02
    chain[4].inputs["Offset Scale"].default_value = 0.05
    obj.modifiers.new("Nodes", "NODES").node_group = group


def digest(obj):
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        hasher = hashlib.blake2b(digest_size=16)
        for attribute in sorted(mesh.attributes, key=lambda item: item.name):
            if attribute.name.startswith("."):
                continue
            field = FIELDS.get(attribute.data_type)
            if field is None:
                continue
            name, size, kind = field
            values = numpy.empty(len(attribute.data) * size, dtype=kind)
            attribute.data.foreach_get(name, values)
            hasher.update(attribute.name.encode() + values.tobytes())
        normals = numpy.empty(len(mesh.loops) * 3, dtype=numpy.float32)
        mesh.corner_normals.foreach_get("vector", normals)
        hasher.update(normals.tobytes())
        return hasher.hexdigest(), len(mesh.loops)
    finally:
        evaluated.to_mesh_clear()


def churn(rng, kept):
    """Move the heap on: make and free Blender meshes and plain blocks of random sizes, keep some alive."""
    made = []
    for _ in range(rng.randint(1, 6)):
        mesh = bpy.data.meshes.new("Churn")
        bm = bmesh.new()
        bmesh.ops.create_icosphere(bm, subdivisions=rng.randint(1, 3), radius=1.0)
        bm.to_mesh(mesh)
        bm.free()
        made.append(mesh)
    kept.append(bytearray(rng.randint(16, 1 << 18)))
    kept.extend(bytes(rng.randint(16, 4096)) for _ in range(rng.randint(0, 40)))
    while len(kept) > 64:
        kept.pop(rng.randrange(len(kept)))
    for mesh in made[::2]:
        bpy.data.meshes.remove(mesh)


def repeatable(obj, rng, kept):
    seen = []
    for round_ in range(ROUNDS):
        churn(rng, kept)
        obj.name = "%s %d" % (obj.name.split(" #")[0], round_) + " #"   # A rename re-evaluates it in full.
        obj.data.update()
        bpy.context.view_layer.update()
        seen.append(digest(obj))
    return seen


def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    rng, kept, results = random.Random(109), [], []
    cutter = bpy.data.objects.new("Cutter", _sphere())
    bpy.context.scene.collection.objects.link(cutter)
    cutter.location, cutter.scale = (0.8, 0.6, 0.7), (0.6, 0.6, 0.6)
    cutter.hide_viewport = cutter.hide_render = True
    evaluators = list(MODIFIERS) + [("boolean exact", "BOOLEAN", {"solver": "EXACT"}),
                                    ("boolean float", "BOOLEAN", {"solver": "FLOAT"}), ("geometry nodes", None, {})]
    for shape, make in (("cube", _cube), ("sphere", _sphere)):
        for name, kind, settings in evaluators:
            obj = bpy.data.objects.new(shape, make())
            bpy.context.scene.collection.objects.link(obj)
            if kind is None:
                _geometry_nodes(obj)
            else:
                modifier = obj.modifiers.new(name, kind)
                for key, value in settings.items():
                    setattr(modifier, key, value)
                if kind == "BOOLEAN":
                    modifier.object = cutter
            seen = repeatable(obj, rng, kept)
            distinct = sorted({item[0] for item in seen})
            results.append({"mesh": shape, "evaluator": name, "corners": seen[0][1], "distinct": len(distinct),
                            "exposed_it": name == "bevel" and shape == "cube"})
            print("ANGELO_EVAL_REPEAT", shape, name, "corners", seen[0][1], "distinct", len(distinct), flush=True)
            bpy.data.objects.remove(obj)
    unstable = [row for row in results if row["distinct"] != 1]
    state = {"success": not unstable, "rounds": ROUNDS, "results": results,
             "unstable": ["%s / %s" % (row["mesh"], row["evaluator"]) for row in unstable]}
    (OUT / "results.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    print("ANGELO_EVALUATION_REPEATABLE", json.dumps({"success": state["success"], "unstable": state["unstable"]}),
          flush=True)


main()
