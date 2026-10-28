# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Place Character: import each part's generated model and put the character together. Main thread.

Runs inside one undoable operator (``angelo.flow_place_character``). Each part
stays its own object, named by its slot, under one root empty at the 3D cursor,
so clothing, hair and accessories can be swapped later.

Image-to-3D providers return closed solids, not cloth: a coat can't be put on a
body. So parts are placed where the sheet drew them (``character_layout``),
clothing is pushed just outside the body (Shrinkwrap, outside only), and what a
part covers is hidden rather than physically fitted: a vertex of the body or of
an inner layer is masked (non-destructive Mask modifier, vertex group
``angelo_covered``) when no outside view sees it once the layers above are on,
or when it pokes through the layer above by up to ``POKE_SHARE`` of the height.
Openings such as an unbuttoned coat stay visible. Research numbers (stock
Blender 5.2, three characters): 0.3-6% of a garment's silhouette still shows
body after masking; 2-4 s per character.

Where each part goes comes from the fit to the character image when the
person ran it (``value["grounding"]``, ``parts_ground``), else from the sheet.
"""

import time

import bmesh
import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from . import character_layout as layout
from .flow_fanout import part_label

CLOTHING = frozenset({"INNER", "OUTER", "LOWER", "FOOTWEAR"})
FITTED = CLOTHING | {"HAIR", "HEADWEAR"}          # pushed outside the body
COVERING = CLOTHING | {"HEADWEAR"}                # hide what they cover
LAYER = {"BODY": 0, "INNER": 1, "LOWER": 2, "FOOTWEAR": 3, "OUTER": 4, "HAIR": 5, "HEADWEAR": 6, "ACCESSORY": 7}
POKE_SHARE = 0.02
OUTSIDE_OFFSET_SHARE = 0.0025
MASK_GROUP = "angelo_covered"


class ComposeError(ValueError):
    """The character can't be placed; nothing was added (the operator's Undo step is not pushed)."""


def _vertices(obj):
    count = len(obj.data.vertices)
    data = np.empty(count * 3)
    obj.data.vertices.foreach_get("co", data)
    return data.reshape(count, 3)


def _set_vertices(obj, vertices):
    obj.data.vertices.foreach_set("co", np.asarray(vertices, float).ravel())
    obj.data.update()


def _one_mesh(window, objects, name):
    """Join imported objects into one mesh object with its transform applied; delete the rest."""
    meshes = [o for o in objects if o.type == 'MESH']
    if not meshes:
        raise ComposeError(f"The model for '{name}' contains no mesh")
    for obj in meshes:
        world = obj.matrix_world.copy()
        obj.parent = None
        obj.data = obj.data.copy() if obj.data.users > 1 else obj.data
        obj.data.transform(world)
        obj.matrix_world.identity()
    for obj in objects:
        if obj.type != 'MESH':
            bpy.data.objects.remove(obj)
    target = meshes[0]
    if len(meshes) > 1:
        override = dict(active_object=target, object=target, selected_objects=meshes, selected_editable_objects=meshes)
        if isinstance(window, bpy.types.Window):
            override["window"] = window
        with bpy.context.temp_override(**override):
            bpy.ops.object.join()
    target.name = name
    return target


def _bvh(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.transform(obj.matrix_world)
    tree = BVHTree.FromBMesh(bm)
    bm.free()
    return tree


def _push_outside(obj, body, offset):
    modifier = obj.modifiers.new("angelo_outside", 'SHRINKWRAP')
    modifier.target, modifier.wrap_method, modifier.wrap_mode, modifier.offset = body, 'NEAREST_SURFACEPOINT', 'OUTSIDE', offset
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = bpy.data.meshes.new_from_object(evaluated, depsgraph=depsgraph)
    obj.modifiers.remove(modifier)
    old, obj.data = obj.data, mesh
    if old.users == 0:
        bpy.data.meshes.remove(old)


def _directions(count=40):
    golden = (1 + 5 ** 0.5) / 2
    directions = []
    for i in range(count):
        z = 1 - 2 * (i + 0.5) / count
        r = (1 - z * z) ** 0.5
        a = 2 * np.pi * i / golden
        directions.append(Vector((r * np.cos(a), r * np.sin(a), z)))
    return directions


def _mask_hidden(obj, covers, height):
    """Mask the vertices of ``obj`` that the covering trees hide from every outside view, or that poke through them."""
    directions, far, poke = _directions(), 2.0 * height, POKE_SHARE * height
    world = obj.matrix_world
    normal_matrix = world.to_3x3().inverted().transposed()
    hidden = []
    for vertex in obj.data.vertices:
        co = world @ vertex.co
        normal = (normal_matrix @ vertex.normal).normalized()
        if any(tree.ray_cast(co - normal * 1e-4, -normal, poke)[0] is not None for tree in covers):
            hidden.append(vertex.index)
            continue
        origin = co + normal * 2e-3
        seen = any(direction.dot(normal) >= 0.2 and all(tree.ray_cast(origin, direction, far)[0] is None for tree in covers)
                   for direction in directions)
        if not seen:
            hidden.append(vertex.index)
    if hidden:
        group = obj.vertex_groups.get(MASK_GROUP) or obj.vertex_groups.new(name=MASK_GROUP)
        group.add(hidden, 1.0, 'REPLACE')
        mask = obj.modifiers.new("angelo_hide_covered", 'MASK')
        mask.vertex_group, mask.invert_vertex_group = MASK_GROUP, True
    return len(hidden) / max(1, len(obj.data.vertices))


def compose(window, value, import_model, model_path, *, name="Character"):
    """Place a character from Place Character's READY value. Returns a receipt for the report and fixtures.

    ``import_model(path)`` imports one GLB and returns its new object names (Media Studio's checked import);
    ``model_path(asset_id)`` is the library file of a chosen model.
    """
    started = time.perf_counter()
    parts = [p for p in value["parts"] if p["id"] in value["models"]]
    body_part = next((p for p in parts if p["kind"] == "BODY"), None)
    if body_part is None:
        raise ComposeError("The body's model isn't ready; run its part first")
    height = value["height"]
    # Every chosen model is found before anything is added, and a failure later removes what was added: a refused
    # Place leaves the scene as it was (the operator then pushes no undo step that could record half a character).
    paths = {part["id"]: model_path(value["models"][part["id"]]) for part in parts}
    collection, added = bpy.data.collections.new(name), []
    window.scene.collection.children.link(collection)
    try:
        return _compose(window, value, parts, body_part, height, paths, import_model, collection, added, name, started)
    except Exception:
        _discard(collection, added)
        raise


def _discard(collection, names):
    """Remove a failed placement: the objects its imports added (and their now unused meshes) and its collection."""
    for object_name in names:
        obj = bpy.data.objects.get(object_name)
        if obj is None:
            continue
        data = obj.data
        bpy.data.objects.remove(obj)
        if isinstance(data, bpy.types.Mesh) and data.users == 0:
            bpy.data.meshes.remove(data)
    for obj in list(collection.objects):   # The root, if it was made.
        bpy.data.objects.remove(obj)
    bpy.data.collections.remove(collection)


def _compose(window, value, parts, body_part, height, paths, import_model, collection, added, name, started):
    objects = {}
    for part in sorted(parts, key=lambda p: LAYER.get(p["kind"], 9)):
        names = import_model(paths[part["id"]])
        added += names
        imported = [bpy.data.objects[n] for n in names if n in bpy.data.objects]
        obj = _one_mesh(window, imported, f"{name} · {part_label(part)}")
        for owner in list(obj.users_collection):
            owner.objects.unlink(obj)
        collection.objects.link(obj)
        obj["angelo_character_part"] = part["id"]
        objects[part["id"]] = obj
    body = objects[body_part["id"]]
    scale, move = layout.body_transform(_vertices(body), height)
    _set_vertices(body, _vertices(body) * np.array(scale) + np.array(move))
    sheet_frame = layout.Frame(body_part["box"], height)
    grounding = value.get("grounding") or {}
    image_frame = layout.Frame(grounding["body"], height) if grounding.get("body") else None
    boxes = grounding.get("boxes") or {}
    body_vertices = _vertices(body)
    placements = {}
    for part in parts:
        if part is body_part:
            continue
        obj = objects[part["id"]]
        vertices = _vertices(obj)
        # A part fitted to the character image (parts_ground) goes where the character wears it: the sheet draws
        # each part at its own scale (garments about 1.5x, small parts 2.4x and away from where they're worn), so its
        # box is only the fallback for a part the fit didn't find.
        grounded = image_frame is not None and boxes.get(part["id"]) is not None
        box, frame = (boxes[part["id"]], image_frame) if grounded else (part["box"], sheet_frame)
        scale, move, info = layout.place(part["kind"], vertices, box, frame, body_part.get("profile"), body_vertices,
                                         sheet_frame=sheet_frame)
        info["grounded"] = float(grounded)
        _set_vertices(obj, vertices * np.array(scale) + np.array(move))
        placements[part["id"]] = {k: round(v, 3) for k, v in info.items()}
        if part["kind"] in FITTED:
            _push_outside(obj, body, OUTSIDE_OFFSET_SHARE * height)
    trees = {pid: _bvh(objects[pid]) for pid, p in ((p["id"], p) for p in parts) if p["kind"] in COVERING}
    masked = {}
    for part in parts:
        above = [trees[q["id"]] for q in parts
                 if q["id"] in trees and q["id"] != part["id"] and LAYER.get(q["kind"], 9) > LAYER.get(part["kind"], 9)]
        if part["kind"] != "ACCESSORY" and above:
            masked[part["id"]] = round(_mask_hidden(objects[part["id"]], above, height), 3)
    root = bpy.data.objects.new(name, None)
    root.empty_display_type, root.empty_display_size = 'PLAIN_AXES', 0.2 * height
    collection.objects.link(root)
    root.location = window.scene.cursor.location
    for obj in objects.values():
        obj.parent = root
    window.view_layer.update()
    left_out = [p["id"] for p in value["parts"] if p["id"] not in value["models"]]
    return {"collection": collection.name, "root": root.name, "objects": [o.name for o in objects.values()],
            "parts": len(objects), "left_out": left_out, "placements": placements, "masked": masked,
            "seconds": round(time.perf_counter() - started, 2)}
