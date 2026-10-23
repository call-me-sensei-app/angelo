# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Images dropped into a Generation Flow become Image input steps at the drop point (1.0.5).

The node editor's ``NODE_OT_angelo_drop_assets`` (C++) takes the drop, one image or a selection, from the Asset
Browser, an Asset Shelf, Media Studio, the Outliner or files, and calls ``angelo.flow_drop_items`` with one line per
item: ``image-id:<session uid>`` (a Blender image, imported from its asset first), ``image-file:<path>``,
``studio-item:<relative asset identifier>`` (an image of Media Studio's own library, which Blender lists as a material
asset: ``media_place.from_identifiers`` names its Studio item) or ``model:<name>``. A Studio image is used as it is;
every other image goes into Media Studio's library first (a flow's Image step references a library image). Then one
Image step per image is placed in a column from the drop point and selected. A 3D model can't be a flow input yet:
it is skipped (the drop's tooltip says so, and a drop of models only is refused with that reason). The C++ operator
holds the drop's one undo step, so this one pushes none.
"""

import os
import tempfile

import bpy
from bpy.props import FloatVectorProperty, StringProperty
from bpy.types import Operator

from . import flow_host, flow_nodes
from .ui_text import rpt_

# Vertical room per placed step: an Image step with its preview is about this tall at UI scale 1.
STEP_SPACING = 200
# Where Blender puts a node it adds at the pointer (node_add.cc, position_node_based_on_mouse): its header under the
# pointer, NODE_DY (20) * 1.5 to the left and * 0.5 up, so a dropped step lands as any added node does.
POINTER_OFFSET = (-30.0, 10.0)
# Blender's own save formats and the file suffix each writes, for an image with no usable file on disk.
SUFFIXES = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}


def parse(items):
    """[(kind, value)] of the C++ operator's lines, in drop order; unknown kinds are kept for the caller to skip."""
    parsed = []
    for line in items.splitlines():
        kind, _, value = line.partition(":")
        if value:
            parsed.append((kind, value))
    return parsed


def image_file(image, directory):
    """A file holding ``image``'s pixels: its own file when that is current, else a copy written into ``directory``."""
    path = bpy.path.abspath(image.filepath, library=image.library) if image.filepath else ""
    if path and os.path.isfile(path) and not image.packed_file and not image.is_dirty and image.source == 'FILE':
        return path
    os.makedirs(directory, exist_ok=True)
    target = os.path.join(directory, "image" + SUFFIXES.get(image.file_format, ".png"))
    image.save(filepath=target, save_copy=True)
    return target


def library_images(entries, find_image, import_image, library, studio_items):
    """Media Studio library ids for the dropped images, in drop order, and the names of the models skipped.

    ``find_image(session_uid)`` returns a Blender image or None; ``import_image(path)`` adds a file to the library and
    returns its asset; ``library`` maps a library id to its record; ``studio_items(identifier)`` names the Studio
    items of a relative asset identifier (``media_place.from_identifiers``). An image that can't be used is reported,
    never silently dropped."""
    ids, models, failures = [], [], []
    with tempfile.TemporaryDirectory(prefix="angelo-flow-drop-") as directory:
        for index, (kind, value) in enumerate(entries):
            try:
                if kind == "model":
                    models.append(value)
                    continue
                if kind == "studio-item":
                    found = studio_items(value)
                    identity = found[0].id if found else ""
                    # One Studio item with several pictures is several material assets of one id: one step.
                    if identity and identity in ids:
                        continue
                    record = library.get(identity) or {}
                    if record.get("kind") != "IMAGE":
                        raise ValueError(rpt_("That Media Studio image is no longer in the library"))
                    ids.append(identity)
                    continue
                if kind == "image-file":
                    path = value
                elif kind == "image-id":
                    image = find_image(int(value))
                    if image is None:
                        continue
                    path = image_file(image, os.path.join(directory, str(index)))
                else:
                    continue
                ids.append(import_image(path)["id"])
            except (ValueError, OSError, RuntimeError) as error:
                failures.append(str(error))
    return ids, models, failures


def place(tree, location, asset_ids):
    """One Image step per asset, in a column from ``location`` (the drop point, in node space), all selected."""
    for node in tree.nodes:
        node.select = False
    placed = []
    for index, asset_id in enumerate(asset_ids):
        node = tree.nodes.new(flow_nodes.ANGELO_FlowImageNode.bl_idname)
        node.location = (location[0] + POINTER_OFFSET[0], location[1] + POINTER_OFFSET[1] - STEP_SPACING * index)
        node.asset_id = asset_id
        node.select = True
        placed.append(node)
    if placed:
        tree.nodes.active = placed[0]
    return placed


def _find_image(session_uid):
    return next((image for image in bpy.data.images if image.session_uid == session_uid), None)


class ANGELO_OT_flow_drop_items(Operator):
    """Add dropped images to a Generation Flow as Image steps"""
    bl_idname = "angelo.flow_drop_items"
    bl_label = "Drop Images into Flow"
    # No UNDO: NODE_OT_angelo_drop_assets, which calls this, is the drop's one undo step.
    bl_options = {'INTERNAL'}
    tree: StringProperty(options={'SKIP_SAVE'})
    items: StringProperty(options={'SKIP_SAVE'})
    location: FloatVectorProperty(size=2, options={'SKIP_SAVE'})

    def execute(self, context):
        tree = bpy.data.node_groups.get(self.tree)
        if tree is None or tree.bl_idname != flow_nodes.TREE:
            return {'CANCELLED'}

        from .media_place import from_identifiers
        library = flow_host.library()
        entries = parse(self.items)
        studio = {item.id for kind, value in entries if kind == "studio-item" for item in from_identifiers(value)}
        if studio - set(library.assets):
            library.reload()   # Written by another writer (a worker, the asset writer): local metadata only.
        ids, _models, failures = library_images(entries, _find_image, library.import_image, library.assets,
                                                from_identifiers)
        placed = place(tree, self.location, ids)
        for failure in failures:
            self.report({'WARNING'}, failure)
        if not placed:
            return {'CANCELLED'}
        flow_host.refresh(tree)
        return {'FINISHED'}


CLASSES = (ANGELO_OT_flow_drop_items,)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
