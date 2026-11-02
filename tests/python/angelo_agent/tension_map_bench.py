# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tension map benchmark: per-frame evaluation time on the same generated scene, in stock Blender and in Angelo.

    <blender> -b --factory-startup --python tension_map_bench.py -- --arm ARM --out result.json [--size 1000]

Both sides build the identical scene in the run (no file to drift): a ``size`` x ``size`` grid (1,000 x 1,000 =
1,002,001 vertices) whose shape key is keyframed across the frame range with a ripple, so every frame deforms every
vertex. Arms:

* ``none``: nothing measured (the never-slower check: stock Blender against Angelo, same scene);
* ``gn``: the Geometry Nodes workaround the Right-Click Select thread points to, built here: edge lengths against
  Blender's ``rest_position``, both averaged onto points, ``tension`` (point) and ``tension_edge`` (edge) stored.
  Runs in both apps, so its two numbers also show the apps evaluate GN alike;
* ``tension``: Angelo's Tension modifier (Angelo only), writing the same two attributes;
* ``tension_points``: the modifier writing the vertex attribute only (the edge output's cost by difference);
* ``tension_smooth``: the modifier with 4 smoothing passes.

``--topology`` measures beyond quad grids: one big n-gon, a triangle fan (one high-valence pole), a wire mesh (loose
edges only) or a real character (``--mesh-file``, its largest mesh subdivided).

Timing: one untimed pass over the range (warm caches), then ``--passes`` timed passes; each frame is
``scene.frame_set`` (animation, shape key, modifiers). Reported: median, p25 and p75 per frame, every sample kept,
and the build. ``--compare`` also reads back both measured outputs on one frame (the ``gn`` and ``tension`` values
must agree: same work on both sides).
"""

import argparse
import json
import math
import statistics
import sys
import time

import bpy
import bmesh


def arguments():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", choices=("none", "gn", "tension", "tension_points", "tension_smooth"), required=True)
    parser.add_argument("--topology", choices=("grid", "ngon", "fan", "wire", "character"), default="grid")
    parser.add_argument("--mesh-file", default="")
    parser.add_argument("--out", required=True)
    parser.add_argument("--size", type=int, default=1000)
    parser.add_argument("--frames", type=int, default=24)
    parser.add_argument("--passes", type=int, default=3)
    parser.add_argument("--compare", action="store_true")
    return parser.parse_args(argv)


def dense_mesh(topology, size, mesh_file=""):
    """The measured mesh: ``grid`` (size x size quads), ``ngon`` (one face of size * 100 vertices), ``fan`` (a
    triangle fan: one pole of valence size * 100), ``wire`` (the grid's edges only: every edge loose) or
    ``character`` (the largest mesh object of ``mesh_file``, subdivided twice). Built the same way in every app."""
    mesh = bpy.data.meshes.new("Dense")
    bm = bmesh.new()
    if topology in {"grid", "wire"}:
        bmesh.ops.create_grid(bm, x_segments=size, y_segments=size, size=1.0)
        if topology == "wire":
            bmesh.ops.delete(bm, geom=list(bm.faces), context='FACES_ONLY')
    elif topology in {"ngon", "fan"}:
        count = size * 100
        rim = [bm.verts.new((math.cos(2 * math.pi * i / count), math.sin(2 * math.pi * i / count), 0.0))
               for i in range(count)]
        if topology == "ngon":
            bm.faces.new(rim)
        else:
            pole = bm.verts.new((0.0, 0.0, 0.0))
            for i in range(count):
                bm.faces.new((pole, rim[i], rim[(i + 1) % count]))
    elif topology == "character":
        with bpy.data.libraries.load(mesh_file, link=False) as (source, target):
            target.meshes = list(source.meshes)
        source_mesh = max(target.meshes, key=lambda m: len(m.vertices))
        bm.from_mesh(source_mesh)
        bmesh.ops.subdivide_edges(bm, edges=list(bm.edges), cuts=3, use_grid_fill=True)
        size_x = max(abs(v.co.x) for v in bm.verts) or 1.0
        bmesh.ops.scale(bm, vec=(1.0 / size_x,) * 3, verts=list(bm.verts))   # The ripple below in unit space.
    bm.to_mesh(mesh)
    bm.free()
    return mesh


def scene_with_grid(size, frames, empty=True, topology="grid", mesh_file=""):
    """``empty``: start from an empty factory scene (background runs); False keeps the window's file (playback)."""
    if empty:
        bpy.ops.wm.read_factory_settings(use_empty=True)
    mesh = dense_mesh(topology, size, mesh_file)
    obj = bpy.data.objects.new("Dense", mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.shape_key_add(name="Basis")
    key = obj.shape_key_add(name="Ripple", from_mix=False)
    count = len(mesh.vertices)
    coords = [0.0] * (3 * count)
    mesh.vertices.foreach_get("co", coords)
    for index in range(count):
        x, y = coords[3 * index], coords[3 * index + 1]
        coords[3 * index] = x * (1.0 + 0.15 * math.sin(6.0 * y))
        coords[3 * index + 2] = 0.1 * math.sin(9.0 * x) * math.cos(7.0 * y)
    key.data.foreach_set("co", coords)
    key.value = 0.0
    key.keyframe_insert("value", frame=1)
    key.value = 1.0
    key.keyframe_insert("value", frame=frames)
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, frames
    return obj


def _socket(sockets, name):
    """The socket called ``name`` that the node's current type shows (typed nodes keep hidden twins)."""
    found = [s for s in sockets if s.name == name and getattr(s, "enabled", True)]
    return found[0]


def gn_workaround(obj):
    obj.add_rest_position_attribute = True
    group = bpy.data.node_groups.new("Tension Workaround", 'GeometryNodeTree')
    group.interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    group.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    nodes, links = group.nodes, group.links
    source, sink = nodes.new("NodeGroupInput"), nodes.new("NodeGroupOutput")
    ends = nodes.new("GeometryNodeInputMeshEdgeVertices")
    rest = nodes.new("GeometryNodeInputNamedAttribute")
    rest.data_type = 'FLOAT_VECTOR'
    rest.inputs["Name"].default_value = "rest_position"

    def at_index(index_socket):
        node = nodes.new("GeometryNodeFieldAtIndex")
        node.data_type, node.domain = 'FLOAT_VECTOR', 'POINT'
        links.new(rest.outputs["Attribute"], _socket(node.inputs, "Value"))
        links.new(index_socket, node.inputs["Index"])
        return _socket(node.outputs, "Value")

    def distance(a, b):
        node = nodes.new("ShaderNodeVectorMath")
        node.operation = 'DISTANCE'
        links.new(a, node.inputs[0])
        links.new(b, node.inputs[1])
        return node.outputs["Value"]

    def math_node(operation, a, b):
        node = nodes.new("ShaderNodeMath")
        node.operation = operation
        for slot, value in enumerate((a, b)):
            if isinstance(value, float):
                node.inputs[slot].default_value = value
            else:
                links.new(value, node.inputs[slot])
        return node.outputs[0]

    def on_points(value):
        """An edge value averaged onto the points (evaluated on edges, read in the point context)."""
        node = nodes.new("GeometryNodeFieldOnDomain")
        node.data_type, node.domain = 'FLOAT', 'EDGE'
        links.new(value, _socket(node.inputs, "Value"))
        return _socket(node.outputs, "Value")

    length = distance(ends.outputs["Position 1"], ends.outputs["Position 2"])
    rest_length = distance(at_index(ends.outputs["Vertex Index 1"]), at_index(ends.outputs["Vertex Index 2"]))
    vertex = math_node('SUBTRACT', math_node('DIVIDE', on_points(length), on_points(rest_length)), 1.0)
    edge = math_node('SUBTRACT', math_node('DIVIDE', length, rest_length), 1.0)

    def store(geometry, name, domain, value):
        node = nodes.new("GeometryNodeStoreNamedAttribute")
        node.data_type, node.domain = 'FLOAT', domain
        node.inputs["Name"].default_value = name
        links.new(geometry, node.inputs["Geometry"])
        links.new(value, _socket(node.inputs, "Value"))
        return node.outputs["Geometry"]

    result = store(store(source.outputs[0], "tension", 'POINT', vertex), "tension_edge", 'EDGE', edge)
    links.new(result, sink.inputs[0])
    obj.modifiers.new("Tension Workaround", 'NODES').node_group = group


def values(obj, name):
    mesh = obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data
    attribute = mesh.attributes.get(name)
    found = [0.0] * len(attribute.data)
    attribute.data.foreach_get("value", found)
    return found


def main():
    args = arguments()
    obj = scene_with_grid(args.size, args.frames, topology=args.topology, mesh_file=args.mesh_file)
    if args.arm == "gn":
        gn_workaround(obj)
    elif args.arm in {"tension", "tension_points", "tension_smooth"}:
        modifier = obj.modifiers.new("Tension", 'TENSION')
        if args.arm == "tension_smooth":
            modifier.smooth_iterations = 4
        if args.arm == "tension_points":
            modifier.edge_attribute_name = ""   # The vertex values alone: the edge output's share by difference.
    scene = bpy.context.scene
    for frame in range(1, args.frames + 1):   # Untimed warm-up pass.
        scene.frame_set(frame)
    samples = []
    for _ in range(args.passes):
        for frame in range(1, args.frames + 1):
            started = time.perf_counter()
            scene.frame_set(frame)
            samples.append(1000.0 * (time.perf_counter() - started))
    ordered = sorted(samples)
    quartiles = statistics.quantiles(ordered, n=4)
    result = {"arm": args.arm, "topology": args.topology, "vertices": len(obj.data.vertices), "edges": len(obj.data.edges),
              "frames": args.frames, "passes": args.passes, "median_ms": round(statistics.median(ordered), 3),
              "p25_ms": round(quartiles[0], 3), "p75_ms": round(quartiles[2], 3),
              "min_ms": round(ordered[0], 3), "max_ms": round(ordered[-1], 3), "samples_ms": [round(s, 3) for s in samples],
              "blender": bpy.app.version_string, "binary": bpy.app.binary_path,
              "build_hash": bpy.app.build_hash.decode() if isinstance(bpy.app.build_hash, bytes) else bpy.app.build_hash}
    if args.compare and args.arm != "none":
        scene.frame_set(args.frames // 2)
        result["midframe_point_values"] = values(obj, "tension")[::997]   # A sparse, deterministic sample.
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=1)
    print("TENSION_BENCH", json.dumps({k: v for k, v in result.items() if not isinstance(v, list)}), flush=True)


if __name__ == "__main__":
    main()
