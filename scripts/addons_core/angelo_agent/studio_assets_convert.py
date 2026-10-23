# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Write Media Studio items as Blender asset files. Runs inside a background Blender, never in the app.

``Angelo -b --factory-startup --python studio_assets_convert.py -- <batch.json> <results.json>``

``studio_assets_host`` starts it (lowered priority) with a batch ``studio_assets.plan`` made: each item names its
source files, the .blend to write, the asset's name, description, tags and catalogue. Each .blend holds one asset
(a model's Object or Collection, or one Material per image) with a preview, written to a temporary name and moved
into place, so the Asset Browser never reads half a file. Only Studio's own checked files are read: a model is the
self-contained GLB Studio already validated, an image a file Studio decoded. The results file says per item whether
it was written. Standalone on purpose: no Angelo add-on runs in this process.
"""

import json
import os
import sys
import time
import uuid

import bpy
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # Its plain sibling module, not the add-on package.
import studio_assets_mesh  # noqa: E402

PREVIEW = 256
# A result's "preview" while its render runs, flushed first: a render that ends the process is known to be the render
# (studio_assets_host.RENDERING holds the same text).
RENDERING = "rendering"
STUDIO_IMAGE = "angelo_studio_asset"   # Saved custom property: studio_assets_host.STUDIO_IMAGE reads it.
DATA = ("objects", "meshes", "materials", "images", "collections", "textures", "node_groups", "armatures",
        "actions", "cameras", "lights", "curves", "worlds")


def _ids():
    return {id_.as_pointer(): id_ for name in DATA for id_ in getattr(bpy.data, name)}


def set_preview(target, path):
    """The asset's preview from a picture file, scaled to fit ``PREVIEW`` pixels."""
    image = bpy.data.images.load(path, check_existing=False)
    try:
        width, height = image.size
        if not width or not height:
            return False
        scale = min(1.0, PREVIEW / max(width, height))
        size = (max(1, round(width * scale)), max(1, round(height * scale)))
        if size != (width, height):
            image.scale(*size)
        pixels = [0.0] * (size[0] * size[1] * 4)
        image.pixels.foreach_get(pixels)
        preview = target.preview_ensure()
        preview.image_size = size
        preview.image_pixels_float.foreach_set(pixels)
        return True
    finally:
        bpy.data.images.remove(image)


def render_preview(objects, path):
    """A Workbench picture of a model that came without one: framed from the front, above."""
    scene = bpy.context.scene
    points = [o.matrix_world @ Vector(c) for o in objects if o.type == 'MESH' for c in o.bound_box]
    if not points:
        return False
    low = Vector(map(min, *(p.to_tuple() for p in points)))
    high = Vector(map(max, *(p.to_tuple() for p in points)))
    centre, radius = (low + high) / 2, max((high - low).length / 2, 1e-4)
    camera = bpy.data.objects.new("Preview", bpy.data.cameras.new("Preview"))
    scene.collection.objects.link(camera)
    direction = Vector((0.0, -1.0, 0.55)).normalized()
    camera.location = centre + direction * radius * 3.2
    camera.rotation_euler = (-direction).to_track_quat('-Z', 'Y').to_euler()
    camera.data.lens = 50
    scene.camera = camera
    render = scene.render
    render.engine = 'BLENDER_WORKBENCH'
    render.resolution_x = render.resolution_y = PREVIEW
    render.resolution_percentage = 100
    render.film_transparent = True
    render.image_settings.file_format = 'PNG'
    render.image_settings.color_mode = 'RGBA'
    render.filepath = path
    scene.display.shading.light = 'STUDIO'
    scene.display.shading.color_type = 'TEXTURE'
    try:
        bpy.ops.render.render(write_still=True)
    finally:
        bpy.data.objects.remove(camera)
    return os.path.isfile(path)


def mark(target, item):
    target.asset_mark()
    data = target.asset_data
    data.catalog_id = item["catalog_id"]
    data.description = item["description"]
    for tag in item["tags"]:
        data.tags.new(tag, skip_if_exists=True)


def model_asset(item, scratch):
    """The model itself, compressed in the asset file: one object baked to its base centre, or a collection of
    several with that base as its instance offset (``studio_assets_mesh``)."""
    objects = studio_assets_mesh.import_glb(item["files"][0]).objects   # Removed after the write, with the rest.
    one, base, _low, _high = studio_assets_mesh.shape(objects)
    if one is not None:
        target = one
    else:
        target = bpy.data.collections.new(item["name"])
        for obj in objects:
            for owner in list(obj.users_collection):
                owner.objects.unlink(obj)
            target.objects.link(obj)
        target.instance_offset = base
        bpy.context.scene.collection.children.link(target)
    target.name = item["name"]
    mark(target, item)
    if item["thumbnail"]:
        set_preview(target, item["thumbnail"])
        return {target}, None
    # No provider picture: a Workbench render, after the file is written (``write``). Rendering in a background
    # Blender can fail, or end the process, on a machine without a usable GPU context; the model is never lost to it.
    return {target}, (target, objects)


def image_assets(item):
    written = set()
    for index, path in enumerate(item["files"]):
        image = bpy.data.images.load(path, check_existing=False)
        image.name = os.path.basename(path)
        # Referenced by its absolute path, never copied into the library. Once the material lands in the person's
        # file, Angelo packs this one picture there (studio_assets_host.pack_landed_images): Blender's first save of an
        # unsaved file makes a referenced path relative, and moving the file would then lose it.
        image[STUDIO_IMAGE] = item["id"]
        material = bpy.data.materials.new(item["name"] if index == 0 else f"{item['name']} {index + 1}")
        if material.node_tree is None:   # Blender 5 gives a new material its node tree.
            material.use_nodes = True
        tree = material.node_tree
        shader = next(n for n in tree.nodes if n.type == 'BSDF_PRINCIPLED')
        texture = tree.nodes.new("ShaderNodeTexImage")
        texture.image = image
        texture.location = (shader.location.x - 340, shader.location.y)
        tree.links.new(texture.outputs["Color"], shader.inputs["Base Color"])
        tree.links.new(texture.outputs["Alpha"], shader.inputs["Alpha"])
        mark(material, item)
        set_preview(material, path)
        written.add(material)
    return written


def _save(item, targets):
    """The asset file, written whole to a temporary name and moved into place."""
    os.makedirs(os.path.dirname(item["blend"]), mode=0o700, exist_ok=True)
    temporary = item["blend"] + "." + uuid.uuid4().hex + ".tmp"
    try:
        bpy.data.libraries.write(temporary, targets, path_remap='ABSOLUTE', fake_user=True, compress=True)
        os.replace(temporary, item["blend"])
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write(item, scratch, report=lambda result: None):
    """Write one item; ``report`` gets its result as soon as the file is in place, before any preview render, so a
    render that ends the process still leaves the item written (without a preview) and reported."""
    started = time.monotonic()
    before = set(_ids())
    try:
        if item["kind"] == "3D":
            targets, render = model_asset(item, scratch)
        else:
            targets, render = image_assets(item), None
        _save(item, targets)
        result = {"ok": True, "seconds": round(time.monotonic() - started, 3)}
        report(result)
        if render is not None and item.get("render", True):   # The host turns renders off once one ended a writer.
            target, objects = render
            picture = os.path.join(scratch, item["id"] + "-preview.png")
            result["preview"] = RENDERING
            report(result)
            try:
                if render_preview(objects, picture) and set_preview(target, picture):
                    _save(item, targets)
                del result["preview"]
            except Exception as error:   # A preview is a nicety: the written model stands without one.
                result["preview"] = f"{type(error).__name__}: {error}"[:200]
        return result
    except Exception as error:   # One item's failure is its result; the batch goes on.
        return {"ok": False, "error": f"{type(error).__name__}: {error}"[:300]}
    finally:
        made = [id_ for pointer, id_ in _ids().items() if pointer not in before]
        if made:
            bpy.data.batch_remove(made)


def main(argv):
    batch_path, results_path = argv[argv.index("--") + 1:][:2]
    with open(batch_path, encoding="utf-8") as handle:
        batch = json.load(handle)
    scratch = os.path.dirname(results_path)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    results = {}

    def flush():
        # Written after every item (and before its preview render), so an interrupted batch still reports it.
        with open(results_path + ".tmp", "w", encoding="utf-8") as handle:
            json.dump(results, handle)
        os.replace(results_path + ".tmp", results_path)
    for item in batch:
        results[item["id"]] = write(item, scratch, lambda result, key=item["id"]: (results.__setitem__(key, result),
                                                                                    flush()))
        flush()


if __name__ == "__main__":
    main(sys.argv)
