# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An interface font reload while the first Color Grid of a session is made never frees its labels' font.

Third audit (P2, 2026-11-01): the color grid's labels use a font of their own (``image_gen.cc``), but it was
published before it was protected, and ``BLF_unload_all`` (an interface font reload) freed unprotected fonts without
taking the load lock, so a reload while the first grid was made could free it under the drawing thread. The font is
now made private (``BLF_PRIVATE``) before it is published, under the lock the reloads take.

The proof, with nothing made beforehand (the font does not exist yet): twelve generated Color Grid images that
Cycles makes on its loading threads during a render, while another thread reloads the interface fonts again and
again (the UI font preference, ``BLF_unload_all``); then every grid equals one made afterwards, and the font still
draws after one more reload, with its labels present (a freed font draws none). Instrumented on Mac 2 (a 50 ms
pause after the font is made, never committed): the old publish-then-protect sequence lost the font to a reload in 4
of 5 runs; the private load kept it in 5 of 5. Run with ``--background --factory-startup``; one first grid per process, so a driver
repeats processes for a rate.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import os
from pathlib import Path
import sys
import threading
import traceback

import bpy

assert bpy.app.background and "--factory-startup" in sys.argv
OUT = Path(os.environ.get("ANGELO_QA_DIR") or "/tmp/angelo-color-grid-font-reload")
OUT.mkdir(parents=True, exist_ok=True)
IMAGES, SIZE = 12, 1024
report = {"checks": {}, "observed": {}, "success": False}


def label_pixels(pixels):
    """Pure black pixels: the labels' text (the grid's own colors never reach black, value 0.1 and up). A label
    font freed by a reload draws nothing, so its grids would have none (the reference made after it too)."""
    return sum(1 for index in range(0, len(pixels), 4) if pixels[index] == pixels[index + 1] == pixels[index + 2] == 0.0)


def grid_image(name):
    image = bpy.data.images.new(name, SIZE, SIZE)
    image.generated_type = 'COLOR_GRID'
    return image


try:
    scene = bpy.context.scene
    view = bpy.context.preferences.view
    bpy.data.batch_remove(tuple(bpy.data.objects))
    bpy.ops.object.camera_add(location=(0, 0, 6))
    scene.camera = bpy.context.active_object
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 1
    scene.render.resolution_x = scene.render.resolution_y = 64
    images = [grid_image(f"First{index}") for index in range(IMAGES)]   # Not read: made during the render.
    for index, image in enumerate(images):
        bpy.ops.mesh.primitive_plane_add(size=0.9, location=((index % 4) - 1.5, (index // 4) - 1.0, 0))
        material = bpy.data.materials.new(f"Grid{index}")
        material.use_nodes = True
        texture = material.node_tree.nodes.new("ShaderNodeTexImage")
        texture.image = image
        material.node_tree.links.new(texture.outputs["Color"],
                                     material.node_tree.nodes["Principled BSDF"].inputs["Base Color"])
        bpy.context.active_object.data.materials.append(material)

    stop, reloads = threading.Event(), [0]

    def reload_fonts():
        """The interface reloads its fonts (BLF_unload_all), again and again, while the render runs."""
        while not stop.is_set():
            view.font_path_ui = ""
            reloads[0] += 1

    reloader = threading.Thread(target=reload_fonts)
    reloader.start()
    try:
        bpy.ops.render.render(write_still=False)
    finally:
        stop.set()
        reloader.join()
    report["observed"]["reloads_during_render"] = reloads[0]
    reference = list(grid_image("Reference").pixels)
    differing = [image.name for image in images if list(image.pixels) != reference]
    report["observed"]["differing"] = differing
    view.font_path_ui = ""   # One more reload: the labels' font must still draw.
    after = list(grid_image("AfterReload").pixels)
    report["checks"]["reloads_overlapped_the_render"] = reloads[0] > 0
    report["checks"]["grids_made_during_reloads_equal_one_made_alone"] = not differing
    report["checks"]["labels_still_draw_after_a_reload"] = after == reference
    report["observed"]["label_pixels"] = label_pixels(reference)
    report["checks"]["grids_have_their_labels"] = report["observed"]["label_pixels"] > 2000
    report["success"] = all(report["checks"].values())
except Exception:
    report["error"] = traceback.format_exc()
    traceback.print_exc()
finally:
    (OUT / "results.json").write_text(json.dumps(report, indent=2))
print("COLOR_GRID_FONT_RELOAD", json.dumps(report["observed"]), json.dumps(report["checks"]))
assert report["success"], report.get("error") or report
