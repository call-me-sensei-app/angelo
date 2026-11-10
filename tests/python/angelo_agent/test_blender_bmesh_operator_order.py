# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""BMesh operators give the same element order in every process, and so the Float boolean the same result.

A BMesh operator's mapping slot (``BMOpSlot``) is a hash keyed by element pointers, and iterating it followed heap
addresses, which differ between processes (ASLR and the allocator's own randomisation). Extrude made its side faces
by iterating ``boundary_map.out``, so ``extrude_face_region``, ``extrude_edge_only``, ``spin``, ``solidify``, the
UV sphere primitive (an edge extrude per segment) and Python's ``bmesh.ops`` map results came out in another order
in every run of stock Blender 5.2 (5.3, main). An order-sensitive tool then gave another result: the Float (Fast)
boolean of a cube with a UV sphere cutter had 262 to 1,519 corners from one run to the next (the evaluation suite's
cube on the 1.1 rehearsal, ``d192309e195``); with an icosphere cutter it repeated. Angelo iterates a mapping slot in
insertion order (``bmesh_operators.cc``, "Mapping Slot Order").

The same holds for the order elements are freed and split in: freeing decides where the next elements are
allocated, so Bevel freeing its rebuilt faces from a pointer-hashed set made a later operation's faces (a
triangulate of a 40 by 40 grid's beveled vertices) come out in another order every run. Bevel, Grid Fill's split
edges, the knife and Weld Edges into Faces now walk insertion-ordered containers.

Checks, in the real binary (background; writes results.json under ANGELO_QA_DIR): ``PROCESSES`` child processes of
this binary each build every case below from scratch and print a digest of its positions and vertex, edge and face
order; every case must give one digest across the processes. The UV sphere, its boolean and Bevel then
Triangulate exposed this; extrude region, spin, solidify, the edge extrude of a circle, the Python map order, Bevel
edges then Subdivide, an uneven Grid Fill and Weld Edges into Faces are held out.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import bmesh
import bpy

PROCESSES = 4
CHILD_FLAG = "--angelo-bmesh-order-child"


def _bmesh_digest(bm):
    bm.verts.index_update()
    hasher = hashlib.blake2b(digest_size=8)
    for vert in bm.verts:
        hasher.update(repr(tuple(round(value, 5) for value in vert.co)).encode())
    for edge in bm.edges:
        hasher.update(repr((edge.verts[0].index, edge.verts[1].index)).encode())
    for face in bm.faces:
        hasher.update(repr([vert.index for vert in face.verts]).encode())
    return hasher.hexdigest(), len(bm.faces)


def _mesh_digest(mesh):
    bm = bmesh.new()
    bm.from_mesh(mesh)
    try:
        return _bmesh_digest(bm)
    finally:
        bm.free()


def _grid():
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=5, y_segments=5, size=1.0)
    return bm


def _op_case(build):
    bm = _grid()
    try:
        build(bm)
        return _bmesh_digest(bm)
    finally:
        bm.free()


def case_extrude_face_region():
    return _op_case(lambda bm: bmesh.ops.extrude_face_region(bm, geom=bm.faces[:]))


def case_extrude_edge_only_circle():
    bm = bmesh.new()
    try:
        bmesh.ops.create_circle(bm, segments=32, radius=1.0)
        bmesh.ops.extrude_edge_only(bm, edges=bm.edges[:])
        return _bmesh_digest(bm)
    finally:
        bm.free()


def case_spin():
    return _op_case(lambda bm: bmesh.ops.spin(bm, geom=bm.verts[:] + bm.edges[:], cent=(2.0, 0.0, 0.0),
                                              axis=(0.0, 1.0, 0.0), angle=3.0, steps=6))


def case_solidify():
    return _op_case(lambda bm: bmesh.ops.solidify(bm, geom=bm.faces[:], thickness=0.1))


def case_create_uvsphere():
    bm = bmesh.new()
    try:
        bmesh.ops.create_uvsphere(bm, u_segments=24, v_segments=12, radius=1.0)
        return _bmesh_digest(bm)
    finally:
        bm.free()


def case_primitive_uv_sphere_add():
    bpy.ops.mesh.primitive_uv_sphere_add()
    obj = bpy.context.object
    try:
        return _mesh_digest(obj.data)
    finally:
        bpy.data.objects.remove(obj)


def case_python_map_order():
    """``bmesh.ops`` returns a mapping slot as a dictionary: its keys come in the operator's order."""
    bm = _grid()
    try:
        bm.verts.index_update()
        result = bmesh.ops.duplicate(bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:])
        order = [vert.index for vert in result["vert_map"].keys()]
        return hashlib.blake2b(repr(order).encode(), digest_size=8).hexdigest(), len(order)
    finally:
        bm.free()


def case_float_boolean_uv_sphere_cutter():
    """The evaluation suite's cube under a Float boolean with a UV sphere cutter (the exposing case)."""
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=2, use_grid_fill=True)
    cube = bpy.data.meshes.new("Cube")
    bm.to_mesh(cube)
    bm.free()
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=24, v_segments=12, radius=1.0)
    sphere = bpy.data.meshes.new("Sphere")
    bm.to_mesh(sphere)
    bm.free()
    cutter = bpy.data.objects.new("Cutter", sphere)
    obj = bpy.data.objects.new("Cut", cube)
    for item in (cutter, obj):
        bpy.context.scene.collection.objects.link(item)
    cutter.location, cutter.scale = (0.8, 0.6, 0.7), (0.6, 0.6, 0.6)
    modifier = obj.modifiers.new("Boolean", "BOOLEAN")
    modifier.solver, modifier.object = "FLOAT", cutter
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        return _mesh_digest(mesh)
    finally:
        evaluated.to_mesh_clear()


def case_bevel_vertices_then_triangulate():
    """Bevel freed its rebuilt faces in a pointer-hashed set's order; the next operation's new faces
    fill those slots, so their order followed heap addresses (the case that exposed it)."""
    bm = bmesh.new()
    try:
        bmesh.ops.create_grid(bm, x_segments=40, y_segments=40, size=1.0)
        bmesh.ops.bevel(bm, geom=bm.verts[:], offset=0.05, segments=2, affect="VERTICES")
        bmesh.ops.triangulate(bm, faces=bm.faces[:])
        return _bmesh_digest(bm)
    finally:
        bm.free()


def case_bevel_edges_then_subdivide():
    bm = bmesh.new()
    try:
        bmesh.ops.create_cube(bm, size=2.0)
        bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=8, use_grid_fill=True)
        bmesh.ops.bevel(bm, geom=bm.edges[:], offset=0.05, segments=3, affect="EDGES")
        bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=1)
        return _bmesh_digest(bm)
    finally:
        bm.free()


def case_grid_fill_uneven_then_triangulate():
    """Grid fill between edge loops of different lengths collapses its split edges from a set."""
    bm = bmesh.new()
    try:
        bottom = [bm.verts.new((x / 64.0, 0.0, 0.0)) for x in range(65)]
        top = [bm.verts.new((x / 21.0, 1.0, 0.0)) for x in range(22)]
        edges = [bm.edges.new(pair) for pair in zip(bottom, bottom[1:])]
        edges += [bm.edges.new(pair) for pair in zip(top, top[1:])]
        for start, end in ((bottom[0], top[0]), (bottom[-1], top[-1])):
            previous = start
            for step in range(1, 4):
                vert = bm.verts.new(start.co.lerp(end.co, step / 4.0))
                bm.edges.new((previous, vert))
                previous = vert
            bm.edges.new((previous, end))
        bmesh.ops.grid_fill(bm, edges=edges, use_interp_simple=True)
        bmesh.ops.triangulate(bm, faces=bm.faces[:])
        return _bmesh_digest(bm)
    finally:
        bm.free()


def case_face_split_by_edges():
    """Edit mode's Weld Edges into Faces splits each face its loose edges lie on, from a map."""
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=30, y_segments=30, size=1.0)
    for i in range(40):
        a = bm.verts.new((-0.97 + 0.048 * i, -0.99, 0.0))
        b = bm.verts.new((-0.95 + 0.048 * i, 0.99, 0.0))
        bm.edges.new((a, b))
    mesh = bpy.data.meshes.new("Split")
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("Split", mesh)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.face_split_by_edges()
    bpy.ops.object.mode_set(mode="OBJECT")
    try:
        return _mesh_digest(mesh)
    finally:
        bpy.data.objects.remove(obj)


CASES = {name[len("case_"):]: fn for name, fn in globals().items() if name.startswith("case_")}


def run_child():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    digests = {}
    for name, case in CASES.items():
        try:
            digests[name] = list(case())
        except Exception as error:
            digests[name] = ["error", "%s: %s" % (type(error).__name__, error)]
    print("ANGELO_BMESH_ORDER_CHILD " + json.dumps(digests), flush=True)


def run_parent():
    assert "--factory-startup" in sys.argv, "Use a disposable factory-startup Blender instance"
    assert bpy.app.background, "Run with --background"
    out = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-bmesh-operator-order"))
    out.mkdir(parents=True, exist_ok=True)
    runs, errors = [], []
    for index in range(PROCESSES):
        command = [bpy.app.binary_path, "--background", "--factory-startup", "--python-exit-code", "3",
                   "--python", __file__, "--", CHILD_FLAG]
        child = subprocess.run(command, capture_output=True, text=True, timeout=180)
        lines = [line for line in child.stdout.splitlines() if line.startswith("ANGELO_BMESH_ORDER_CHILD ")]
        if child.returncode != 0 or not lines:
            errors.append({"process": index, "exit": child.returncode, "stderr_tail": child.stderr[-600:]})
            continue
        runs.append(json.loads(lines[-1].split(" ", 1)[1]))
    results = []
    for name in CASES:
        seen = [run.get(name) for run in runs]
        distinct = sorted({json.dumps(item) for item in seen})
        failed = len(runs) < PROCESSES or len(distinct) != 1 or seen[0][0] == "error"
        results.append({"case": name, "distinct": len(distinct), "faces": [item[1] for item in seen],
                        "exposed_it": name in ("create_uvsphere", "float_boolean_uv_sphere_cutter",
                                               "bevel_vertices_then_triangulate"),
                        "success": not failed})
        print("ANGELO_BMESH_ORDER", name, "distinct", len(distinct), flush=True)
    unstable = [row["case"] for row in results if not row["success"]]
    state = {"success": not unstable and not errors, "processes": PROCESSES, "results": results,
             "unstable": unstable, "errors": errors, "binary": bpy.app.binary_path}
    (out / "results.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    print("ANGELO_BMESH_OPERATOR_ORDER", json.dumps({"success": state["success"], "unstable": unstable}), flush=True)


if CHILD_FLAG in sys.argv:
    run_child()
else:
    run_parent()
