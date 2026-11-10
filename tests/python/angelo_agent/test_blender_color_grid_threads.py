# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Color grids made on several threads at once come out exactly as one made alone (doc/angelo/uv_checker.rst).

Audit P1 (2026-11-01): the generated Color Grid image draws its labels with BLF, and drew them with the render
stamp's font object (``blf_mono_font_render``), whose buffer, size, color and pen are unlocked shared state. The
viewport UV checker's worker makes one off the main thread, and Cycles loads image textures on its task threads, so
grids made at once raced each other (and a stamping render). ``image_gen.cc`` now gives the labels a font of their own
and draws one grid at a time.

The proof: rounds of twelve generated Color Grid images, never read before the render, on twelve planes in a Cycles
render with stamping on, so Cycles generates them on its loading threads at once; then each is compared byte for
byte with a grid generated alone on the main thread. Run with ``--background --factory-startup``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import os
from pathlib import Path
import sys
import time
import traceback

import bpy

assert bpy.app.background and "--factory-startup" in sys.argv
OUT = Path(os.environ.get("ANGELO_QA_DIR") or "/tmp/angelo-color-grid-threads")
OUT.mkdir(parents=True, exist_ok=True)
ROUNDS, IMAGES, SIZE = 3, 12, 1024
report = {"checks": {}, "observed": {"rounds": []}, "success": False}


def grid_image(name):
    image = bpy.data.images.new(name, SIZE, SIZE)
    image.generated_type = 'COLOR_GRID'
    return image


def textured_plane(index, image):
    bpy.ops.mesh.primitive_plane_add(size=0.9, location=((index % 4) - 1.5, (index // 4) - 1.0, 0))
    plane = bpy.context.active_object
    material = bpy.data.materials.new(f"Grid{index}")
    material.use_nodes = True
    texture = material.node_tree.nodes.new("ShaderNodeTexImage")
    texture.image = image
    material.node_tree.links.new(texture.outputs["Color"],
                                 material.node_tree.nodes["Principled BSDF"].inputs["Base Color"])
    plane.data.materials.append(material)
    return plane


try:
    scene = bpy.context.scene
    bpy.data.batch_remove(tuple(bpy.data.objects))
    bpy.ops.object.camera_add(location=(0, 0, 6))
    scene.camera = bpy.context.active_object
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 1
    scene.render.resolution_x = scene.render.resolution_y = 64
    scene.render.use_stamp = True              # The stamp draws with blf_mono_font_render too.
    scene.render.use_stamp_note = True
    scene.render.stamp_note_text = "color grid threads"
    reference = list(grid_image("Reference").pixels)   # Made alone, on the main thread.
    for round_index in range(ROUNDS):
        for obj in [o for o in bpy.data.objects if o.type == 'MESH']:
            bpy.data.objects.remove(obj)
        images = [grid_image(f"Round{round_index}_{index}") for index in range(IMAGES)]
        for index, image in enumerate(images):
            textured_plane(index, image)
        started = time.perf_counter()
        bpy.ops.render.render(write_still=False)
        elapsed = round(time.perf_counter() - started, 2)
        differing = [image.name for image in images if list(image.pixels) != reference]
        report["observed"]["rounds"].append({"render_seconds": elapsed, "differing": differing})
        for image in images:
            bpy.data.images.remove(image)
    every = [name for entry in report["observed"]["rounds"] for name in entry["differing"]]
    report["checks"]["every_grid_made_at_once_equals_one_made_alone"] = not every
    report["success"] = all(report["checks"].values())
except Exception:
    report["error"] = traceback.format_exc()
    traceback.print_exc()
finally:
    (OUT / "results.json").write_text(json.dumps(report, indent=2))
print("COLOR_GRID_THREADS", json.dumps(report["observed"]["rounds"]))
assert report["success"], report.get("error") or report["observed"]
