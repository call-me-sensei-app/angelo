# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A Media Studio model's geometry as its asset holds it (``studio_assets_convert``, a background Blender).

One object is baked to identity with its origin at the base centre of its bounds, so a drop stands it on the surface
under the pointer at the GLB's own size; several keep their places and the collection's instance offset is that base
centre. Bounds are the baked vertices' own, so a rotated model's are tight. An import that fails removes everything it
made. A plain module (no package-relative imports): the writer imports it beside itself.
"""

import bpy
from mathutils import Matrix, Vector

try:
    from .gltf_morph import import_options
    from . import material_display_sync
except ImportError:   # A plain sibling module in studio_assets_convert's child Blender.
    from gltf_morph import import_options
    import material_display_sync

KINDS = ("objects", "meshes", "armatures", "materials", "images", "actions", "collections", "textures", "node_groups")


class Made:
    """What one import made, found against a snapshot of the file's datablocks taken before it: ``objects`` and
    ``collections``, and ``discard`` removes every datablock it made, whatever stage it failed at."""

    def __init__(self):
        self.before = {kind: {i.as_pointer() for i in getattr(bpy.data, kind)} for kind in KINDS}
        self.objects, self.collections = [], []

    def new(self, kind):
        return [i for i in getattr(bpy.data, kind) if i.as_pointer() not in self.before[kind]]

    def collect(self):
        self.objects, self.collections = self.new("objects"), self.new("collections")
        return self

    def discard(self):
        for kind in KINDS:   # Objects first: they use the rest.
            made = self.new(kind)
            if made:
                bpy.data.batch_remove(made)


def import_glb(path):
    """Import a GLB (textures packed, no bone-display shapes); a ``Made``. On any failure, everything the import made
    is removed and the error raised, so a retry never leaves copies behind."""
    made = Made()
    # No scene extras: they would be written onto whichever scene is current, not the person's model.
    options = {"filepath": str(path), "import_pack_images": True, "import_scene_extras": False,
               **import_options(path)}
    try:
        try:
            result = bpy.ops.import_scene.gltf(disable_bone_shape=True, **options)
        except TypeError:   # An importer without the option.
            result = bpy.ops.import_scene.gltf(**options)
        if result != {'FINISHED'}:
            raise RuntimeError("the GLB did not import")
        for material in made.new("materials"):   # Solid view shows what the importer's graph shows.
            material_display_sync.sync(material, material_display_sync.ADDED, images=material_display_sync.LOAD)
    except Exception:
        made.discard()
        raise
    return made.collect()


def bounds(objects):
    """The exact world bounds of the objects' mesh vertices (numpy: a provider model has hundreds of thousands)."""
    import numpy
    lows, highs = [], []
    for obj in objects:
        if obj.type != 'MESH' or not obj.data.vertices:
            continue
        co = numpy.empty(len(obj.data.vertices) * 3, dtype=numpy.float64)
        obj.data.vertices.foreach_get("co", co)
        matrix = numpy.array(obj.matrix_world)
        world = co.reshape(-1, 3) @ matrix[:3, :3].T + matrix[:3, 3]
        lows.append(world.min(axis=0))
        highs.append(world.max(axis=0))
    if not lows:
        raise RuntimeError("the GLB holds no mesh")
    return Vector(numpy.min(lows, axis=0).tolist()), Vector(numpy.max(highs, axis=0).tolist())


def single(objects):
    """The one object a model asset is, or None when it is a collection of several."""
    meshes = [o for o in objects if o.type == 'MESH']
    if len(objects) == 1 and len(meshes) == 1 and meshes[0].data.users == 1 and not meshes[0].modifiers:
        return meshes[0]
    return None


def shape(objects):
    """Shape an import as an asset: ``(single object or None, base, low, high)``. A single object is baked to identity
    with its origin at the base centre; several keep their places (their collection's instance offset is ``base``)."""
    bpy.context.view_layer.update()
    one = single(objects)
    if one is not None:
        one.data.transform(one.matrix_world, shape_keys=True)
        one.matrix_world = Matrix.Identity(4)
    low, high = bounds(objects)
    base = Vector(((low.x + high.x) / 2, (low.y + high.y) / 2, low.z))
    if one is not None:
        one.data.transform(Matrix.Translation(-base), shape_keys=True)
    return one, base, low, high
