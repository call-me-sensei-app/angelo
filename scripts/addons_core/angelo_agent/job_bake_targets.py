# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a texture bake writes into: the target's bake images, and the objects a high-to-low bake reads.

Blender bakes into the image of each material's active, selected Image Texture
node. A request either leaves that choice to the materials (``active_images``)
or names the image by its resource ID; then ``TargetNodes`` points every
material of the target at it for the job and puts the node trees back exactly:
the active node, its selection, the node shown in textured views, and no
temporary node left. What it changed is recorded on the scene with the job's
settings (``job_settings``), so a file saved while the bake ran is put back
when it is next opened.

A UDIM image is baked tile by tile by Blender. ``covered_tiles`` says which
tiles the target's faces lie in (the bake writes those and leaves the others'
pixels as they are); ``restorable_tiles`` whether a stopped bake can be undone:
Python reaches only an image's first tile, so a tile's backup is its own saved
file or packed data, valid while the image has no unsaved changes
(``reload_tiles``).

``check_target`` and ``check_sources`` refuse what Blender's bake would refuse
only once its job runs (a target disabled in renders, a source outside the
view layer, a cage with other faces), so the approval never offers such a
bake. ``written`` reads from the images whether a bake that ended wrote them,
which Blender's own completion handler does not say.

Model-facing refusals are English. Main thread only.
"""

import os
from array import array

import bpy

from .job_spec import DATA_MAPS, JobRequestError, check_resolution

IMAGE_SUFFIXES = {"PNG": ".png", "JPEG": ".jpg", "OPEN_EXR": ".exr", "TARGA": ".tga", "TIFF": ".tif", "BMP": ".bmp",
                  "WEBP": ".webp"}
# The float copy costs 16 bytes a pixel: 256 MiB at this bound.
MAX_RESTORE_PIXELS = 4096 * 4096
# What Blender's bake accepts as a source (it converts these to meshes).
SOURCE_TYPES = frozenset({'MESH', 'FONT', 'CURVE', 'SURFACE', 'META'})
TEMPORARY_NODE = "Angelo Bake Target"
RESTORE_KEY, CREATED_KEY = "bake_nodes", "bake_image"
# A new bake image: Non-Color for the maps that hold data (job_spec.DATA_MAPS); float where 8 bits can't hold
# the values.
FLOAT_MAPS = frozenset({"POSITION"})


def active_images(target):
    """The images the target's own materials bake into: each one's active, selected Image Texture node."""
    images = []
    for slot in target.material_slots:
        node = slot.material.node_tree.nodes.active if slot.material and slot.material.node_tree else None
        if node is None or node.bl_idname != "ShaderNodeTexImage" or node.image is None or not node.select:
            raise JobRequestError("Every material of %s needs an active, selected Image Texture node with an image to "
                                  "bake into; or pass image (the resource ID of a loaded image) or resolution (a new "
                                  "image) and the job points the materials at it" % target.name)
        images.append(node.image)
    if not images:
        raise JobRequestError("%s has no material to bake into; give it a material first" % target.name)
    return list(dict.fromkeys(images))


def check_image(image):
    """Refuse an image a bake cannot write into as a still picture of this file."""
    if image.library is not None:
        raise JobRequestError("The image %s is linked from another file; bake into an image of this file" % image.name)
    if image.type not in {'IMAGE', 'UV_TEST'} or image.source not in {'FILE', 'GENERATED', 'TILED'}:
        raise JobRequestError("%s is not a still image a bake can write into" % image.name)


def new_image_name(target, bake_type):
    """The name a new bake image will really get: ``<target> <Map>``, numbered if that is taken."""
    base = "%s %s" % (target.name, bake_type.title())
    name, number = base[:60], 1
    while bpy.data.images.get(name) is not None:
        number += 1
        name = "%s %d" % (base[:56], number)
    return name


def create_image(name, size, bake_type):
    """A new image for one bake, kept by a fake user until a material uses it."""
    image = bpy.data.images.new(name, size[0], size[1], alpha=False, float_buffer=bake_type in FLOAT_MAPS,
                                is_data=bake_type in DATA_MAPS)
    image.use_fake_user = True
    if bake_type in FLOAT_MAPS:
        image.file_format = 'OPEN_EXR'
    return image


def remove_created(name):
    """Remove the image a job created and did not finish (also from a file saved while the bake ran)."""
    image = bpy.data.images.get(name) if name else None
    if image is not None and image.library is None:
        bpy.data.images.remove(image)


def _in_view_layer(view_layer, obj):
    return view_layer.objects.get(obj.name) == obj


def check_target(window, target):
    """What Blender requires of the object baked onto, checked before its job starts."""
    try:
        if target.type != 'MESH' or not target.data.uv_layers or not target.data.polygons:
            raise JobRequestError("Bake onto a mesh with faces and a UV map")
    except ReferenceError:
        raise JobRequestError("The object to bake onto was removed") from None
    active = window.view_layer.objects.active
    if target.mode != 'OBJECT' or (active is not None and active.mode != 'OBJECT'):
        raise JobRequestError("Switch Blender to Object Mode before baking")
    if not _in_view_layer(window.view_layer, target) or target.hide_render:
        raise JobRequestError("%s is excluded from the view layer or disabled in renders; a bake needs it enabled"
                              % target.name)


def check_sources(window, target, sources, cage):
    """A high-to-low bake's source objects and cage, as Blender's bake needs them."""
    try:
        for source in sources:
            if source.type not in SOURCE_TYPES:
                raise JobRequestError("%s is not a mesh, curve, text, surface or metaball, so it can't be baked from"
                                      % source.name)
            if not _in_view_layer(window.view_layer, source):  # Hidden is fine: Blender shows a source to its bake.
                raise JobRequestError("%s is excluded from the view layer; include its collection to bake from it"
                                      % source.name)
        if cage is None:
            return
        if cage.type != 'MESH' or not _in_view_layer(window.view_layer, cage):
            raise JobRequestError("The cage %s must be a mesh in the view layer" % cage.name)
        if sum(obj.name == cage.name for obj in bpy.data.objects) != 1:
            raise JobRequestError("Blender finds a cage by its name, and %s names more than one object; rename it"
                                  % cage.name)
        # Modifiers can change either mesh's faces; then only Blender's own check, in its job, can tell.
        if not cage.modifiers and not target.modifiers and _faces(cage) != _faces(target):
            raise JobRequestError("The cage %s must have the same faces as %s (a copy of it, moved or scaled to "
                                  "enclose the sources)" % (cage.name, target.name))
    except ReferenceError:
        raise JobRequestError("An object of this bake was removed") from None


def _faces(obj):
    return len(obj.data.polygons), len(obj.data.loops)


class TargetNodes:
    """One image as the bake target of every material of ``target``, for one job."""

    def __init__(self, target, image):
        self.target, self.image, self.records = target, image, []

    def materials(self):
        """The target's materials, each once (raises JobRequestError when the job could not point them at the image)."""
        found = list(dict.fromkeys(slot.material for slot in self.target.material_slots if slot.material is not None))
        if not found:
            raise JobRequestError("Give %s a material first; a bake writes through its materials" % self.target.name)
        for material in found:
            if material.library is not None or material.node_tree is None or not material.is_editable:
                raise JobRequestError("The material %s is linked or has no nodes, so a job can't point it at the image; "
                                      "leave image out to bake into its own active image" % material.name)
        return found

    def apply(self):
        """Point every material at the image; returns what was changed, as plain data for ``restore``."""
        for material in self.materials():
            nodes = material.node_tree.nodes
            record = {"material": material.name, "active": nodes.active.name if nodes.active else None,
                      "textured": [node.name for node in nodes if node.show_texture]}
            node = next((node for node in nodes if node.bl_idname == "ShaderNodeTexImage" and node.image == self.image),
                        None)
            record["created"] = node is None
            if node is None:
                node = nodes.new("ShaderNodeTexImage")
                node.name = node.label = TEMPORARY_NODE
                node.image = self.image
            record.update(node=node.name, select=bool(node.select) and not record["created"])
            self.records.append(record)  # Recorded before the tree changes: a failure below is still undone.
            nodes.active, node.select = node, True
        return self.records

    def restore(self):
        restore(self.records)
        self.records = []


def restore(records):
    """Put back the node trees ``TargetNodes.apply`` changed (also from a file saved while the bake ran)."""
    for record in reversed(records):
        try:
            material = bpy.data.materials.get(record["material"])
            nodes = material.node_tree.nodes if material is not None and material.node_tree is not None else None
            if nodes is None:
                continue  # The person removed the material meanwhile.
            node = nodes.get(record["node"])
            if node is not None and record["created"]:
                nodes.remove(node)
            elif node is not None:
                node.select = record["select"]
            nodes.active = nodes.get(record["active"]) if record["active"] else None
            for other in nodes:
                shown = other.name in record["textured"]
                if other.show_texture != shown:
                    other.show_texture = shown
        except (AttributeError, KeyError, TypeError, ReferenceError, RuntimeError):
            pass  # The rest are still put back.


def _pixels(image):
    values = array('f', [0.0]) * len(image.pixels)
    image.pixels.foreach_get(values)
    return values


def snapshot(images):
    """A float copy of each image's pixels, to put back if the bake stops."""
    return [(image, _pixels(image)) for image in images]


def written(image, was_clean, kept):
    """Whether a bake that ended really wrote ``image``: True, False, or None when nothing can tell.

    Blender calls ``object_bake_complete`` for every bake job that was not stopped, also one it refused
    inside the job (an object disabled in renders, a cage with other faces), so the handler is no
    evidence. A bake marks what it writes as changed: an image without unsaved changes before the bake
    has them afterwards only if it was written. An image that already had unsaved changes is compared
    with the pixels kept at start (``kept``); without that copy there is nothing to compare.
    """
    try:
        if was_clean:
            return bool(image.is_dirty)
        if kept is None:
            return None
        return len(image.pixels) != len(kept) or _pixels(image) != kept
    except ReferenceError:
        return False  # The image was removed meanwhile.


def put_back(snapshots):
    """Write and verify the kept pixels (a stopped bake writes too); False if any could not be restored."""
    restored = bool(snapshots)
    for image, values in snapshots:
        try:
            if len(image.pixels) == len(values):
                image.pixels.foreach_set(values)
                image.update()
                if _pixels(image) != values:
                    restored = False
            else:
                restored = False
        except (ReferenceError, RuntimeError, ValueError):
            restored = False  # Continue restoring the other images even if this one no longer accepts its copy.
    return restored


def copy_name(image, taken):
    """A file name for the published copy of ``image``, unique among ``taken`` (which it joins)."""
    stem, suffix = bpy.path.clean_name(image.name) or "image", IMAGE_SUFFIXES.get(image.file_format, ".png")
    name, number = stem + suffix, 1
    while name.lower() in taken:
        number += 1
        name = "%s-%d%s" % (stem, number, suffix)
    taken.add(name.lower())
    return name


# UDIM images -------------------------------------------------------------------------------------------

def is_tiled(image):
    return image.source == 'TILED'


def check_tiles(image):
    """Every tile of a UDIM image must hold pixels within the job's bounds (Blender refuses an empty tile later)."""
    for tile in image.tiles:
        if tile.size[0] <= 0 or tile.size[1] <= 0:
            raise JobRequestError("Tile %d of the image %s has no pixels yet; fill or load it before baking"
                                  % (tile.number, image.name))
        check_resolution(tile.size[0], tile.size[1], "tile %d of the image %s" % (tile.number, image.name))


def covered_tiles(target, image, explicit):
    """The tile numbers of ``image`` that ``target``'s faces lie in, sorted: the tiles a bake writes.

    Blender bakes with the mesh's active UV map and writes a face into every
    tile its UVs overlap. ``explicit`` is True when the request named the image
    (every face with a material bakes into it); otherwise only the faces of
    materials whose active node holds this image count.
    """
    import numpy
    mesh = target.data
    layer = mesh.uv_layers.active
    if layer is None or not mesh.polygons:
        return []
    uvs = numpy.empty(len(mesh.loops) * 2, dtype=numpy.float32)
    layer.uv.foreach_get("vector", uvs)
    starts = numpy.empty(len(mesh.polygons), dtype=numpy.int32)
    mesh.polygons.foreach_get("loop_start", starts)
    slots = numpy.empty(len(mesh.polygons), dtype=numpy.int32)
    mesh.polygons.foreach_get("material_index", slots)
    bakes_here = []
    for slot in target.material_slots:
        material = slot.material
        node = material.node_tree.nodes.active if material is not None and material.node_tree is not None else None
        bakes_here.append(material is not None and (explicit or (node is not None and getattr(node, "image", None) == image)))
    if not any(bakes_here):
        return []
    used = numpy.array(bakes_here, dtype=bool)[numpy.clip(slots, 0, len(bakes_here) - 1)]
    u, v = uvs[0::2], uvs[1::2]
    low_u, high_u = numpy.minimum.reduceat(u, starts)[used], numpy.maximum.reduceat(u, starts)[used]
    low_v, high_v = numpy.minimum.reduceat(v, starts)[used], numpy.maximum.reduceat(v, starts)[used]
    # A face touching a tile's edge without area in it does not count: the upper bound is exclusive.
    first_u, first_v = numpy.floor(low_u).astype(numpy.int64), numpy.floor(low_v).astype(numpy.int64)
    last_u = numpy.maximum(first_u, numpy.ceil(high_u - 1e-6).astype(numpy.int64) - 1)
    last_v = numpy.maximum(first_v, numpy.ceil(high_v - 1e-6).astype(numpy.int64) - 1)
    spans = numpy.unique(numpy.stack([first_u, last_u, first_v, last_v], axis=1), axis=0)
    numbers = set()
    for u0, u1, v0, v1 in spans.tolist():
        for column in range(max(u0, 0), min(u1, 9) + 1):
            for row in range(max(v0, 0), min(v1, 99) + 1):
                numbers.add(1001 + column + 10 * row)
    return sorted(numbers & {tile.number for tile in image.tiles})


def _tile_path(image, number):
    """The file one tile of a UDIM image is stored in, or None when its path names no tile."""
    path = image.filepath
    if "<UDIM>" not in path and "<UVTILE>" not in path:
        return None
    column, row = (number - 1001) % 10 + 1, (number - 1001) // 10 + 1
    path = path.replace("<UDIM>", str(number)).replace("<UVTILE>", "u%d_v%d" % (column, row))
    return bpy.path.abspath(path, library=image.library)


def restorable_tiles(image):
    """Whether a stopped bake's tiles can be put back: no unsaved changes, and a saved source for every tile."""
    if image.is_dirty:
        return False
    if len(image.packed_files) >= len(image.tiles):
        return True
    paths = [_tile_path(image, tile.number) for tile in image.tiles]
    return all(path is not None and os.path.isfile(path) for path in paths)


def reload_tiles(image):
    """Put a UDIM image back as it was saved: Blender reads every tile again from its file or packed data."""
    try:
        image.buffers_free()
    except ReferenceError:
        return False  # The image was removed meanwhile.
    return True


def tile_copy_names(image, taken):
    """``(path with Blender's <UDIM> mark, {tile number: file name})`` for the published copies of a UDIM image."""
    stem, suffix = bpy.path.clean_name(image.name) or "image", IMAGE_SUFFIXES.get(image.file_format, ".png")
    number = 1
    while any(("%s.%d%s" % (stem, tile.number, suffix)).lower() in taken for tile in image.tiles):
        number += 1
        stem = "%s-%d" % (bpy.path.clean_name(image.name) or "image", number)
    names = {tile.number: "%s.%d%s" % (stem, tile.number, suffix) for tile in image.tiles}
    taken.update(name.lower() for name in names.values())
    return "%s.<UDIM>%s" % (stem, suffix), names
