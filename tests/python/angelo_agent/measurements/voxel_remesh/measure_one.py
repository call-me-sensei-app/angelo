# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
# Run inside Angelo/Blender --background: one voxel remesh, prints one JSON line with peak memory and timings.
# argv after "--": shape target_verts path(op|mod) options(default|all|none)
import json
import math
import resource
import sys
import time

import bpy
import bmesh

import ctypes
import os


def footprint_peak():
    """macOS lifetime peak physical footprint (counts compressed memory, unlike RSS), from rusage_info_v4."""
    try:
        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        buffer = (ctypes.c_uint64 * 64)()
        if libc.proc_pid_rusage(os.getpid(), 4, ctypes.byref(buffer)) != 0:
            return 0
        return int(buffer[2 + 28])  # 16-byte uuid, then ri_lifetime_max_phys_footprint is the 29th uint64
    except OSError:
        return 0


shape, target, path, options = sys.argv[sys.argv.index("--") + 1:][:4]
target = float(target)

bpy.ops.wm.read_factory_settings(use_empty=True)


def make(shape):
    if shape == "cube":
        bpy.ops.mesh.primitive_cube_add(size=2)
    elif shape == "sphere":
        bpy.ops.mesh.primitive_uv_sphere_add(radius=1, segments=64, ring_count=32)
    elif shape == "dense_sphere":
        bpy.ops.mesh.primitive_uv_sphere_add(radius=1, segments=720, ring_count=360)
    elif shape == "torus":
        bpy.ops.mesh.primitive_torus_add(major_radius=1, minor_radius=0.25, major_segments=96, minor_segments=24)
    elif shape == "monkey":
        bpy.ops.mesh.primitive_monkey_add(size=2)
        ob = bpy.context.object
        mod = ob.modifiers.new("s", 'SUBSURF')
        mod.levels = 2
        bpy.ops.object.modifier_apply(modifier="s")
    elif shape == "plate":
        bpy.ops.mesh.primitive_cube_add(size=2)
        bpy.context.object.data.transform(__import__("mathutils").Matrix.Diagonal((1, 1, 0.01, 1)))
    elif shape == "grid":
        # Open surface: the level set wraps both sides of the sheet.
        bpy.ops.mesh.primitive_grid_add(x_subdivisions=40, y_subdivisions=40, size=2)
    elif shape == "big_terrain":
        # 1 km open terrain with relief, the class of the #77868 reports (imported environments).
        bpy.ops.mesh.primitive_grid_add(x_subdivisions=200, y_subdivisions=200, size=1000)
        import random
        random.seed(3)
        for v in bpy.context.object.data.vertices:
            v.co.z = 20 * __import__("math").sin(v.co.x / 37) * __import__("math").cos(v.co.y / 53) + random.random()
    elif shape == "scatter":
        # Many small disconnected cubes: high surface per bounding volume.
        bm = bmesh.new()
        n = 12
        for i in range(n):
            for j in range(n):
                for k in range(n):
                    m = __import__("mathutils").Matrix.Translation(((i - n / 2) * 0.3, (j - n / 2) * 0.3, (k - n / 2) * 0.3))
                    bmesh.ops.create_cube(bm, size=0.1, matrix=m)
        me = bpy.data.meshes.new("scatter")
        bm.to_mesh(me)
        ob = bpy.data.objects.new("scatter", me)
        bpy.context.scene.collection.objects.link(ob)
        bpy.context.view_layer.objects.active = ob
        ob.select_set(True)
    return bpy.context.view_layer.objects.active


ob = make(shape)
me = ob.data

# Projected-area estimate: vertices ~ sum(area * (|nx|+|ny|+|nz|)) / h^2 (marching cubes crossings on 3 axis planes).
proj = 0.0
for p in me.polygons:
    n = p.normal
    proj += p.area * (abs(n.x) + abs(n.y) + abs(n.z))
h = math.sqrt(proj / target)

if options == "all":
    me.use_remesh_preserve_volume = True
    me.use_remesh_preserve_attributes = True
    me.use_remesh_fix_poles = True
elif options == "none":
    me.use_remesh_preserve_volume = False
    me.use_remesh_preserve_attributes = False
    me.use_remesh_fix_poles = False

src_faces = len(me.polygons)
dg = bpy.context.evaluated_depsgraph_get()
dg.update()
base_peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # bytes on macOS
base_fp = footprint_peak()
t0 = time.perf_counter()
if path == "op":
    me.remesh_voxel_size = h
    res = bpy.ops.object.voxel_remesh()
    out = ob.data
    nv, nf = len(out.vertices), len(out.polygons)
else:
    mod = ob.modifiers.new("r", 'REMESH')
    mod.mode = 'VOXEL'
    mod.voxel_size = h
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    ev = ob.evaluated_get(dg)
    out = ev.data
    nv, nf = len(out.vertices), len(out.polygons)
    res = {"MOD"}
t1 = time.perf_counter()
peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
peak_fp = footprint_peak()
print("VOXREC " + json.dumps({
    "shape": shape, "path": path, "options": options, "target": target, "voxel": h, "proj_area": proj,
    "src_faces": src_faces, "out_verts": nv, "out_faces": nf, "seconds": round(t1 - t0, 3),
    "base_peak": base_peak, "peak": peak, "delta": peak - base_peak, "fp_base": base_fp, "fp_peak": peak_fp,
    "fp_delta": peak_fp - base_fp,
    "bytes_per_vert": (peak - base_peak) / max(nv, 1), "result": sorted(res),
    "flags": [me.use_remesh_preserve_volume, me.use_remesh_preserve_attributes, me.use_remesh_fix_poles],
}), flush=True)
