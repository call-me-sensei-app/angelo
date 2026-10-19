# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Render and linked-ID graph regressions in a disposable factory-startup process.

Compare the image hashes and observations between equally configured baseline
and candidate binaries. No external assets, provider, or user document is used.
"""

from array import array
import hashlib
import json
import os
from pathlib import Path
import sys
import traceback

import bpy
from mathutils import Vector

assert bpy.app.background and "--factory-startup" in sys.argv
OUT = Path(os.environ["ANGELO_QA_DIR"])
OUT.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.filepaths.temporary_directory = str(OUT)
bpy.context.preferences.filepaths.file_preview_type = "NONE"
report = {"checks": {}, "images": {}, "instances": {}, "binary": bpy.app.binary_path}


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def reset():
    bpy.data.batch_remove(tuple(bpy.data.objects))
    bpy.data.batch_remove(tuple(bpy.data.collections))
    bpy.context.view_layer.update()


def render_visibility():
    reset()
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x = scene.render.resolution_y = 128
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = True
    scene.display.render_aa = "OFF"
    scene.display.shading.light = "FLAT"
    scene.display.shading.color_type = "OBJECT"
    scene.display.shading.show_shadows = False
    scene.display.shading.show_cavity = False
    scene.view_settings.view_transform = "Standard"
    camera = bpy.data.objects.new("AuditCamera", bpy.data.cameras.new("AuditCamera"))
    scene.collection.objects.link(camera)
    camera.location = (0, -10, 5)
    camera.rotation_euler = (Vector((0, 0, 0)) - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 7
    scene.camera = camera
    objects = []
    for name, x, color in (("Red", -2, (1, 0, 0, 1)),
                            ("Green", 0, (0, 1, 0, 1)),
                            ("Blue", 2, (0, 0, 1, 1))):
        bpy.ops.mesh.primitive_cube_add(size=1.25, location=(x, 0, 0))
        obj = bpy.context.object
        obj.name, obj.color = name, color
        objects.append(obj)
    red, green, blue = objects
    red.hide_viewport = True
    green.hide_render = True

    def capture(name):
        path = OUT / (name + ".png")
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        image = bpy.data.images.load(str(path), check_existing=False)
        try:
            check(name + "_dimensions", tuple(image.size) == (128, 128))
            pixels = array("f", [0]) * len(image.pixels)
            image.pixels.foreach_get(pixels)
            report["images"][name] = hashlib.sha256(pixels.tobytes()).hexdigest()
            counts = [0, 0, 0]
            for offset in range(0, len(pixels), 4):
                if pixels[offset + 3] < .5:
                    continue
                rgb = pixels[offset:offset + 3]
                for channel in range(3):
                    if rgb[channel] > .2 and all(rgb[channel] > 1.5 * rgb[other]
                                                 for other in range(3) if other != channel):
                        counts[channel] += 1
            return counts
        finally:
            bpy.data.images.remove(image)

    counts = capture("render_visibility_initial")
    check("render_enabled_despite_viewport_hide", counts[0] > 100 and counts[2] > 100)
    check("render_hidden_green", counts[1] == 0)
    red.hide_render = True
    green.hide_render = False
    counts = capture("render_visibility_changed")
    check("render_flags_rebuilt", counts[0] == 0 and counts[1] > 100 and counts[2] > 100)
    red.hide_render = False
    green.hide_render = True
    capture("render_visibility_restored")
    check("render_restored_exactly", report["images"]["render_visibility_initial"] ==
          report["images"]["render_visibility_restored"])
    check("render_original_geometry", all(len(obj.data.vertices) == 8 for obj in objects))


def linked_instances():
    reset()
    asset = bpy.data.collections.new("AuditAsset")
    bpy.context.scene.collection.children.link(asset)
    bpy.ops.mesh.primitive_cube_add(location=(2, 0, 0))
    obj = bpy.context.object
    obj.name = "LibraryMesh"
    for collection in tuple(obj.users_collection):
        collection.objects.unlink(obj)
    asset.objects.link(obj)
    modifier = obj.modifiers.new("Array", "ARRAY")
    modifier.count = 2
    modifier.use_merge_vertices = False
    library = OUT / "asset.blend"
    bpy.data.libraries.write(str(library), {asset}, compress=True)
    reset()
    with bpy.data.libraries.load(str(library), link=True) as (_, destination):
        destination.collections = ["AuditAsset"]
    linked = destination.collections[0]
    instance = bpy.data.objects.new("LinkedInstance", None)
    instance.instance_type = "COLLECTION"
    instance.instance_collection = linked
    bpy.context.scene.collection.objects.link(instance)
    check("linked_library_preserved", linked.library is not None and
          linked.objects["LibraryMesh"].library is not None)

    def inspect(name, offset):
        graph = bpy.context.evaluated_depsgraph_get()
        values = [(tuple(item.matrix_world.translation), len(item.object.data.vertices))
                  for item in graph.object_instances
                  if item.is_instance and item.object.original.name == "LibraryMesh"]
        report["instances"][name] = values
        check(name + "_evaluated", len(values) == 1 and values[0][1] == 16 and
              all(abs(a - b) < 1e-5 for a, b in zip(values[0][0], (2 + offset, 0, 0))))
        check(name + "_source_unchanged", len(linked.objects["LibraryMesh"].data.vertices) == 8)

    for index, offset in enumerate((0, 3, -2, 0)):
        instance.location.x = offset
        probe = bpy.data.objects.new("GraphProbe", None)
        bpy.context.scene.collection.objects.link(probe)
        bpy.context.view_layer.update()
        bpy.data.objects.remove(probe, do_unlink=True)
        bpy.context.view_layer.update()
        inspect("linked_rebuild_%d" % index, offset)
    path = OUT / "linked-scene.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(path), check_existing=False)
    bpy.ops.wm.open_mainfile(filepath=str(path))
    linked = bpy.data.objects["LinkedInstance"].instance_collection
    inspect("linked_reload", 0)


try:
    render_visibility()
    linked_instances()
    report["success"] = all(report["checks"].values())
except Exception:
    report["success"] = False
    report["error"] = traceback.format_exc()
    raise
finally:
    (OUT / "results.json").write_text(json.dumps(report, indent=2))
    print("NATIVE_GRAPH_LIFECYCLE", json.dumps(report), flush=True)
