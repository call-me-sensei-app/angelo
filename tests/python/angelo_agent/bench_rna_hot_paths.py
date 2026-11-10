# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Instructions per operation on the Python API's hot paths, for wrapper-lifetime changes (``bpy_rna.cc``).

Each operation runs ``count`` times inside one measured call; a sample is the calling thread's retired
instructions (``perf_counters.read_thread_counts``) divided by ``count``. Process-wide instructions are reported too
for the operations whose work runs partly on Blender's worker threads (evaluation). Instruction counts do not depend
on what else the machine runs, so they compare builds on a busy machine; wall times are reported and are only
meaningful on a quiet one.

Two scene states are measured for the allocator-heavy operations: no sub-data wrapper alive, and one kept alive
(``held``), since a kept sub-data wrapper is what turns on freed-memory watching.

Run: ``Angelo --background --factory-startup --python bench_rna_hot_paths.py -- --out report.json``.
"""

import json
import os
import statistics
import sys
import time

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import perf_counters  # noqa: E402

SAMPLES = 9
WARMUP = 2


def _mesh_object(name, verts):
    """A grid mesh with about ``verts`` vertices and a Subdivision modifier, linked to the scene."""
    side = max(2, int(verts ** 0.5))
    me = bpy.data.meshes.new(name)
    coords = [(x, y, 0.0) for y in range(side) for x in range(side)]
    faces = [(y * side + x, y * side + x + 1, (y + 1) * side + x + 1, (y + 1) * side + x)
             for y in range(side - 1) for x in range(side - 1)]
    me.from_pydata(coords, [], faces)
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _sample(function, count):
    """One sample: per-operation thread and process instructions, and wall nanoseconds."""
    t0 = perf_counters.read_thread_counts()
    p0 = perf_counters.read_rusage()
    w0 = time.perf_counter_ns()
    function()
    w1 = time.perf_counter_ns()
    p1 = perf_counters.read_rusage()
    t1 = perf_counters.read_thread_counts()
    return {"thread": (t1["instructions"] - t0["instructions"]) / count,
            "process": (p1["instructions"] - p0["instructions"]) / count,
            "wall_ns": (w1 - w0) / count}


def _measure(function, count):
    samples = [_sample(function, count) for _ in range(WARMUP + SAMPLES)][WARMUP:]
    result = {}
    for key in ("thread", "process", "wall_ns"):
        values = [s[key] for s in samples]
        result[key] = {"median": round(statistics.median(values), 1), "min": round(min(values), 1),
                       "max": round(max(values), 1)}
    return result


def operations():
    scene = bpy.context.scene
    big = _mesh_object("Big", 250_000)
    small = _mesh_object("Small", 400)
    obj = small
    mod = obj.modifiers.new("Sub", 'SUBSURF')
    mod.levels = 1
    obj["x"] = 1.5
    obj["g"] = {"a": 1, "b": [1.0, 2.0, 3.0]}
    # Evaluation shares mesh storage. Settle it before capturing live element
    # wrappers, so their initial writable lookup detaches during setup.
    depsgraph = bpy.context.evaluated_depsgraph_get()
    me = big.data
    verts = me.vertices
    held = [verts[i] for i in range(1000)]
    n_co = len(verts) * 3
    flat = [0.0] * n_co
    try:
        import numpy as np
        buffer = np.zeros(n_co, dtype=np.float32)
    except ImportError:
        buffer = None
    churn_mesh = bpy.data.meshes.new("Churn")
    churn_mesh.vertices.add(8)

    def loop(count, body):
        def run():
            for _ in range(count):
                body()
        return run

    def id_vector_get():
        return obj.location

    def id_int_get():
        return obj.pass_index

    def embedded_int_get():
        return scene.render.resolution_x

    def sub_int_get():
        return mod.levels

    def sub_int_set():
        mod.levels = 1

    def vertex_item():
        return verts[7]

    def held_vertex_co():
        for v in held:
            v.co

    def held_vertex_co_x():
        for v in held:
            v.co.x

    def iterate_vertices():
        for _v in small.data.vertices:
            pass

    def iterate_vertices_co():
        for v in small.data.vertices:
            v.co.x

    def idprop_get():
        return obj["x"]

    def idprop_nested_get():
        return obj["g"]["b"][1]

    def foreach_get_list():
        verts.foreach_get("co", flat)

    def foreach_get_buffer():
        verts.foreach_get("co", buffer)

    def foreach_set_buffer():
        verts.foreach_set("co", buffer)

    def vertices_add():
        churn_mesh.vertices.add(1)

    def mesh_new_remove():
        m = bpy.data.meshes.new("Tmp")
        bpy.data.meshes.remove(m)

    def evaluate():
        obj.update_tag()
        depsgraph.update()

    ops = [
        ("id_vector_get", 2000, loop(2000, id_vector_get)),
        ("id_int_get", 2000, loop(2000, id_int_get)),
        ("embedded_int_get", 2000, loop(2000, embedded_int_get)),
        ("sub_int_get", 2000, loop(2000, sub_int_get)),
        ("sub_int_set", 2000, loop(2000, sub_int_set)),
        ("vertex_item", 2000, loop(2000, vertex_item)),
        ("held_vertex_co", 1000, held_vertex_co),
        ("held_vertex_co_x", 1000, held_vertex_co_x),
        ("iterate_vertices", len(small.data.vertices), iterate_vertices),
        ("iterate_vertices_co_x", len(small.data.vertices), iterate_vertices_co),
        ("idprop_get", 2000, loop(2000, idprop_get)),
        ("idprop_nested_get", 2000, loop(2000, idprop_nested_get)),
        ("foreach_get_list_750k", 1, foreach_get_list),
        ("vertices_add_1", 200, loop(200, vertices_add)),
        ("mesh_new_remove", 200, loop(200, mesh_new_remove)),
        ("evaluate_subsurf", 5, loop(5, evaluate)),
    ]
    if buffer is not None:
        ops += [("foreach_get_buffer_750k", 1, foreach_get_buffer), ("foreach_set_buffer_750k", 1, foreach_set_buffer)]
    return ops, held


def allocator_operations():
    """Allocator-heavy operations measured while no sub-data wrapper is alive (only ID-level wrappers)."""
    obj = _mesh_object("Alloc", 400)
    obj.modifiers.new("Sub", 'SUBSURF').levels = 1  # The returned wrapper is dropped at once.
    churn_mesh = bpy.data.meshes.new("Churn")
    churn_mesh.vertices.add(8)
    depsgraph = bpy.context.evaluated_depsgraph_get()

    def vertices_add():
        for _ in range(200):
            churn_mesh.vertices.add(1)

    def mesh_new_remove():
        for _ in range(200):
            bpy.data.meshes.remove(bpy.data.meshes.new("Tmp"))

    def evaluate():
        for _ in range(5):
            obj.update_tag()
            depsgraph.update()

    return [("vertices_add_1_none_held", 200, vertices_add), ("mesh_new_remove_none_held", 200, mesh_new_remove),
            ("evaluate_subsurf_none_held", 5, evaluate)]


def main():
    out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None
    report = {"binary": bpy.app.binary_path, "version": bpy.app.version_string, "build_hash": bpy.app.build_hash.decode(),
              "load_1m": round(os.getloadavg()[0], 2), "samples": SAMPLES, "operations": {}}
    for name, count, function in allocator_operations():
        report["operations"][name] = _measure(function, count)
    ops, _held = operations()
    for name, count, function in ops:
        report["operations"][name] = _measure(function, count)
    text = json.dumps(report, indent=1)
    if out:
        with open(out, "w") as handle:
            handle.write(text)
    print("ANGELO_RNA_HOT_PATHS " + json.dumps({k: v["thread"]["median"] for k, v in report["operations"].items()}))


main()
