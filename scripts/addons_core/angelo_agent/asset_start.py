# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Start from a bundled asset: the one operation behind a person's choice and a model's typed create.

A person choosing "Start from a base mesh" (``chosen_tools_host``) and a model
asking for the same start through a typed create kind (planned:
``create_from_asset`` beside ``create_primitive``) must get the same result by
the same code, so both are correct by construction and read back alike.
``place`` is that code. It never decides where its objects live: the caller
passes a sink with

* ``track(id)``: every data-block the append created (the staged context's
  ``track_id``, or the host's rollback list), and
* ``link(obj)``: puts a new object in the scene (the staged context's
  ``add_object``, or the scene collection).

It appends the asset's collection or object, links every object it holds
(listed parts and the rest) and removes the appended collections, so both
callers own exactly the objects and their data; it places the asset at a point at real
size or fitted to a box's centre and height, marks each object with its origin
(``BASE_MESH_PROPERTY``) and returns the read-back facts: objects and roles,
the placement, landmarks and named face-set regions in world space, base
topology. Handles are the caller's (they depend on staging).
"""

import os

import bpy
from mathutils import Matrix, Vector

from . import human_base_meshes

BASE_MESH_PROPERTY = human_base_meshes.PROPERTY
MAX_REGIONS = 16
# Data-block collections an appended base mesh can bring (the files hold no Text, Library, Image or script data:
# make_human_base_meshes checks them); every new block in these is tracked.
TRACKED = ("objects", "meshes", "materials", "node_groups", "images", "textures", "shape_keys", "curves")


def asset(name):
    """The table row for an asset's name (spacing and case ignored) or its file stem, or None."""
    return next((row for row in human_base_meshes.table()["assets"] if human_base_meshes._matches(row, name)), None)


def asset_path(row):
    """The installed file, or ValueError when this build doesn't hold it whole (a Git LFS pointer, a lite build)."""
    folder = bpy.utils.system_resource('SCRIPTS', path=human_base_meshes.RELATIVE)
    path = os.path.join(folder, row["file"]) if folder else ""
    if not human_base_meshes.whole(path, row):
        raise ValueError("This base mesh isn't installed in this build of Angelo")
    return path


def place(name, sink, *, location=(0.0, 0.0, 0.0), fit_box=None):
    """Append and place asset ``name``; returns (objects, main object, facts).

    ``fit_box`` = (min corner, max corner) in world space: the asset is centred on the box and scaled to its
    height. Otherwise it stands at ``location`` at real size. Raises ValueError for an unknown or missing asset.
    """
    row = asset(name)
    if row is None:
        raise ValueError("Unknown base mesh; choose one of the bundled names")
    path = asset_path(row)
    kind = "collections" if row["datablock"] == "COLLECTION" else "objects"
    before = {key: set(getattr(bpy.data, key)) for key in TRACKED + ("collections",)}
    with bpy.data.libraries.load(path, link=False) as (source, target):
        if row["datablock_name"] not in getattr(source, kind):
            raise ValueError("This base mesh's file is incomplete; reinstall Angelo")   # Never the internal name.
        setattr(target, kind, [row["datablock_name"]])
    loaded = getattr(target, kind)[0]
    objects = list(loaded.all_objects) if kind == "collections" else [loaded]   # Every part, listed or not.
    for collection in [c for c in bpy.data.collections if c not in before["collections"]]:
        bpy.data.collections.remove(collection)   # The file's grouping only: the sink alone places the objects.
    for key in TRACKED:   # Data first: a staged object's data must be journaled before it is linked.
        if key != "objects":
            for block in getattr(bpy.data, key):
                if block not in before[key]:
                    sink.track(block)
    from . import material_display_sync as display
    for material in (m for m in bpy.data.materials if m not in before["materials"]):
        display.sync(material, display.ADDED)   # Solid view shows what the appended graph shows (no images read).
    for obj in objects:
        sink.track(obj)
        sink.link(obj)
    scale, offset = 1.0, Vector(location)
    if fit_box is not None:
        low, high = Vector(fit_box[0]), Vector(fit_box[1])
        scale = max(high.z - low.z, 1e-6) / max(row["size"][2], 1e-6)
        offset = (low + high) / 2
    placement = Matrix.Translation(offset) @ Matrix.Scale(scale, 4)
    for obj in objects:
        obj[BASE_MESH_PROPERTY] = row["name"]
        if obj.parent is None or obj.parent not in objects:
            obj.matrix_world = placement @ obj.matrix_world
    main = next((obj for obj in objects if _stem(obj.name, {row["main"]}) == row["main"]), objects[0])
    return objects, main, _facts(row, objects, placement, scale, offset, fit_box is not None)


def _facts(row, objects, placement, scale, offset, fitted):
    roles = {item["name"]: item["role"] for item in row["objects"]}

    def world(point):
        return [round(v, 4) for v in placement @ Vector(point)]

    return {
        "asset": row["name"], "kind": row["kind"], "style": row["style"],
        "objects": [{"role": roles.get(_stem(obj.name, roles), "part"), "name": obj.name}
                    for obj in objects if obj.type == "MESH"][:12],
        "placed": {"at": "fitted box" if fitted else "point", "location": [round(v, 4) for v in offset],
                   "scale": round(scale, 4), "height_m": round(row["size"][2] * scale, 4),
                   "facing": "-Y (front), +X is its left"},
        "landmarks": {key: (world(value) if isinstance(value, list) else round(value * scale + offset.z, 4))
                      for key, value in row.get("landmarks", {}).items()},
        "face_set_regions": [{"name": item["name"], "face_set": item["face_set"], "box_min": world(item["box_min"]),
                              "box_max": world(item["box_max"])}
                             for item in row.get("regions", ()) if item.get("name")][:MAX_REGIONS],
        "topology": {"base_vertices": row["total_vertices"],
                     "modifiers": [m["type"] + (" %d" % m["levels"] if "levels" in m else "")
                                   for item in row["objects"] if item.get("role") == "main"
                                   for m in item.get("modifiers", ())]},
    }


def _stem(name, known):
    """An appended object's table name: Blender adds .001 when the name is taken."""
    if name in known:
        return name
    base, _, suffix = name.rpartition(".")
    return base if suffix.isdigit() and base in known else name
