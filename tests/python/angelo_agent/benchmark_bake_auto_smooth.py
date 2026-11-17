# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bake Auto Smooth's return (Blender #153195): objects sharing a mesh, before and after the bake. Run alone.

    <app> -b --factory-startup --python tests/python/angelo_agent/benchmark_bake_auto_smooth.py -- \
        [--objects 10000] [--reps 5] [--out OUT.json]

Builds N objects sharing one mesh (a capped 24-sided cylinder with a 16 x 8 sphere; its face count is
reported), gives each a Smooth by Angle modifier with Shade Auto Smooth, then
measures in the same process before and after object.shade_auto_smooth_bake:

* reevaluate_objects_s: median depsgraph update after tagging every object's geometry;
* reevaluate_mesh_s: median update after tagging the shared mesh (an edit to it);
* transform_s: median update after moving every object (no geometry work either way);
* evaluated_meshes: distinct evaluated meshes; rss_mb: resident memory after evaluation;
* bake_s: the operator itself.

Both sides run in the same build, scene and settings; only the bake differs. Timings are only
comparable on a quiet machine; state the machine, build and load with every number.
"""

import argparse
import json
import math
import os
import statistics
import subprocess
import sys
import time

import bmesh
import bpy


def arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--objects", type=int, default=10000)
    parser.add_argument("--reps", type=int, default=5)
    parser.add_argument("--out")
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])


def rss_mb():
    output = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True).stdout
    return int(output.strip()) / 1024.0 if output.strip() else None


def template():
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=24, radius1=1.0, radius2=1.0, depth=2.0)
    sphere = bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=8, radius=0.7)
    bmesh.ops.translate(bm, vec=(0.0, 0.0, 1.6), verts=sphere["verts"])
    mesh = bpy.data.meshes.new("Shared")
    bm.to_mesh(mesh)
    bm.free()
    return mesh


def median_update(depsgraph, tag, reps):
    samples = []
    for _ in range(reps):
        tag()
        start = time.perf_counter()
        depsgraph.update()
        samples.append(time.perf_counter() - start)
    return statistics.median(samples)


def measure(objects, mesh, reps):
    depsgraph = bpy.context.evaluated_depsgraph_get()

    def tag_objects():
        for ob in objects:
            ob.update_tag(refresh={'DATA'})

    def tag_transforms():
        for ob in objects:
            ob.update_tag(refresh={'OBJECT'})

    return {"reevaluate_objects_s": median_update(depsgraph, tag_objects, reps),
            "reevaluate_mesh_s": median_update(depsgraph, lambda: mesh.update_tag(), reps),
            "transform_s": median_update(depsgraph, tag_transforms, reps),
            "evaluated_meshes": len({ob.evaluated_get(depsgraph).data.as_pointer() for ob in objects}),
            "rss_mb": rss_mb()}


def main():
    args = arguments()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    mesh = template()
    objects = []
    for index in range(args.objects):
        ob = bpy.data.objects.new(f"Ob{index:06d}", mesh)
        ob.location = (index % 100 * 3.0, index // 100 * 3.0, 0.0)
        bpy.context.scene.collection.objects.link(ob)
        ob.select_set(True)
        objects.append(ob)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.object.shade_auto_smooth(angle=math.radians(30.0))
    result = {"objects": args.objects, "faces": len(mesh.polygons), "reps": args.reps,
              "binary": bpy.app.binary_path, "version": bpy.app.version_string,
              "build_hash": bpy.app.build_hash.decode(), "threads": bpy.context.scene.render.threads}
    result["before"] = measure(objects, mesh, args.reps)
    start = time.perf_counter()
    result["bake_result"] = sorted(bpy.ops.object.shade_auto_smooth_bake())
    result["bake_s"] = time.perf_counter() - start
    result["after"] = measure(objects, mesh, args.reps)
    before, after = result["before"], result["after"]
    result["ratios"] = {key: before[key] / after[key] for key in ("reevaluate_objects_s", "reevaluate_mesh_s")
                        if after[key]}
    text = json.dumps(result, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as out:
            out.write(text)
    print("BAKE_AUTO_SMOOTH_BENCH_JSON: " + json.dumps(result), flush=True)


main()
