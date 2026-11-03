# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Web export time with and without the look, and what a World picture costs (doc/angelo/performance.rst).

Runs inside a background Angelo; the same file runs in an app with the look (1.0.9) and one without (1.0.8), so
an ABAB of processes compares the two on the same scenes::

    Angelo -b --factory-startup --python tests/python/angelo_agent/bench_web_look.py -- <out.json> [repeats]

Every process builds the same scene: 120 objects (cubes, spheres, a monkey per row) on two materials, 1,000
triangles each on average, a sun and two point lights, a camera. It is exported twice untimed (warm-up: the glTF
add-on, the colour configuration's first measurement, file caches), then ``repeats`` times timed, for each World:

* ``color``: Blender's default World (a plain colour), the everyday case: the look must cost nothing measurable;
* ``none``: no World.

An app with the look also times, untimed first, the World forms that make a picture: Blender's bundled
``forest.exr`` studio HDRI, a synthetic 8192 x 4096 EXR (the size of a downloaded 8K HDRI), and a Sky texture
(a background Blender bake): the first export of each (the picture made) and the next (the picture cached).
Wall-clock milliseconds of ``web_export.export``; the JSON names the app's add-on and Blender's build hash.
"""

import json
import os
import statistics
import sys
import tempfile
import time

import addon_utils
import bpy

addon_utils.enable("angelo_agent", default_set=False, persistent=False)
from angelo_agent import web_export  # noqa: E402

REQUEST = {"meshes": "AUTO", "textures": "AUTO", "merge": True, "selection_only": False}


def build():
    scene = bpy.context.scene
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    stone, paint = bpy.data.materials.new("Stone"), bpy.data.materials.new("Paint")
    for row in range(10):
        for column in range(12):
            location = (column * 2.2 - 12, row * 2.2 - 10, 1)
            kind = column % 3
            if kind == 0:
                bpy.ops.mesh.primitive_cube_add(size=1.4, location=location)
            elif kind == 1:
                bpy.ops.mesh.primitive_uv_sphere_add(radius=0.8, location=location, segments=32, ring_count=16)
            else:
                bpy.ops.mesh.primitive_monkey_add(size=1.2, location=location)
            bpy.context.active_object.data.materials.append(stone if (row + column) % 2 else paint)
    bpy.ops.object.light_add(type="SUN", rotation=(0.8, 0.1, 0.5))
    for x in (-6, 6):
        bpy.ops.object.light_add(type="POINT", location=(x, 0, 5))
    bpy.ops.object.camera_add(location=(20, -20, 14), rotation=(1.1, 0, 0.8))
    scene.camera = bpy.context.active_object
    return scene


def world_of(kind, folder):
    if kind == "color":
        made = bpy.data.worlds.new("Plain")
        made.color = (0.05, 0.05, 0.05)
        return made
    if kind == "none":
        return None
    made = bpy.data.worlds.new(kind)
    tree = made.node_tree
    for node in list(tree.nodes):
        tree.nodes.remove(node)
    background = tree.nodes.new("ShaderNodeBackground")
    tree.links.new(background.outputs[0], tree.nodes.new("ShaderNodeOutputWorld").inputs[0])
    if kind == "sky":
        tree.links.new(tree.nodes.new("ShaderNodeTexSky").outputs[0], background.inputs[0])
        return made
    texture = tree.nodes.new("ShaderNodeTexEnvironment")
    if kind == "hdri":
        path = os.path.join(bpy.utils.system_resource("DATAFILES", path="studiolights"), "world", "forest.exr")
    else:   # hdri8k: an 8192 x 4096 half-float EXR with a bright sun, written once per process.
        import numpy as np
        import OpenImageIO as oiio
        path = os.path.join(folder, "big.exr")
        height, width = 4096, 8192
        sky = np.linspace(0.2, 1.5, height, dtype=np.float32)[:, None, None] * np.ones((1, width, 3), np.float32)
        sky[400:440, 3000:3040] = 5000.0
        image = oiio.ImageBuf(oiio.ImageSpec(width, height, 3, oiio.HALF))
        image.set_pixels(oiio.ROI(), sky)
        image.write(path)
    texture.image = bpy.data.images.load(path)
    tree.links.new(texture.outputs[0], background.inputs[0])
    return made


def export_ms(scene, folder):
    path = os.path.join(folder, "scene-%d.glb" % time.perf_counter_ns())
    started = time.perf_counter()
    if hasattr(web_export, "pending_export"):   # The export waits for the World's picture off the main thread.
        pending = web_export.pending_export(None, scene, bpy.context.view_layer, path, dict(REQUEST))
        while (report := pending.step()) is None:
            pending.job.done.wait(1.0)   # Returns as the picture lands; a background process has no UI to keep.
    else:   # An app before it (1.0.8) waits inside export.
        report = web_export.export(None, scene, bpy.context.view_layer, path, dict(REQUEST))
    elapsed = (time.perf_counter() - started) * 1000.0
    size = os.path.getsize(path)
    os.remove(path)
    return elapsed, size, report


def summary(values):
    ordered = sorted(values)
    return {"median": round(statistics.median(ordered), 2), "p75": round(ordered[int(0.75 * (len(ordered) - 1))], 2),
            "min": round(ordered[0], 2), "max": round(ordered[-1], 2), "n": len(ordered),
            "all": [round(value, 2) for value in values]}


def main():
    arguments = sys.argv[sys.argv.index("--") + 1:]
    out, repeats = arguments[0], int(arguments[1]) if len(arguments) > 1 else 10
    folder = tempfile.mkdtemp(prefix="angelo-bench-web-look-")
    scene = build()
    looks = hasattr(web_export, "_inspect")
    result = {"app_has_look": looks, "blender": bpy.app.build_hash.decode(),
              "addon": os.path.dirname(web_export.__file__), "triangles": None, "timed": {}, "pictures": {}}
    for kind in ("color", "none"):
        scene.world = world_of(kind, folder)
        for warm in range(2):
            elapsed, size, report = export_ms(scene, folder)
            if warm == 0:
                result.setdefault("first_export_ms", {})[kind] = round(elapsed, 1)   # Each process's cold export.
        result["triangles"] = report["budget"]["triangles"]
        values = []
        for _ in range(repeats):
            values.append(export_ms(scene, folder)[0])
        result["timed"][kind] = dict(summary(values), bytes=size)
    if looks:
        for kind in ("hdri", "hdri8k", "sky"):
            scene.world = world_of(kind, folder)
            first, size, report = export_ms(scene, folder)
            cached, _size, _report = export_ms(scene, folder)
            environment = report["settings"]["environment"]
            result["pictures"][kind] = {"first_ms": round(first, 1), "cached_ms": round(cached, 1), "bytes": size,
                                        "picture_bytes": environment.get("bytes"), "made_ms": environment.get("made_ms"),
                                        "status": environment.get("status")}
    with open(out, "w") as handle:
        json.dump(result, handle, indent=2)
    print("BENCH_WEB_LOOK " + json.dumps({key: result[key] for key in ("app_has_look", "first_export_ms", "pictures")}))


main()
