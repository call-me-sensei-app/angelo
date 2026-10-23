# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Make the Human Base Meshes Angelo bundles, and their facts, from Blender's own asset bundle.

Blender publishes the Human Base Meshes (CC0, by the Blender community and the
Blender Studio) as one 49 MB file. Angelo ships the assets one file each, so a
script appends one head without reading the rest, and
``angelo_blender_api`` domain BASE_MESHES answers from
``human_base_meshes.json`` (``human_base_meshes.py``): each asset's objects,
sizes, topology, modifiers, a few landmarks and the face-set regions.

Which assets: every asset whose Multires carries at most one level. The four
with more (the realistic bodies and skull at three levels, the planar head at
five) are 4 to 13 MB each and together 85 % of the bundle; their shapes are
the same bodies the stylised and primitive assets give a start for.

Each asset is moved so the bounding box of its meshes is centred on the origin
(the bundle lays them out side by side), keeping rotation and scale; nothing
else changes. The facts are read from the evaluated meshes in that place, so
they are what a script that appends the file finds.

Run in a stock background Blender with the official zip (checked against its
pinned SHA-256)::

    Blender -b --factory-startup --disable-autoexec --python make_human_base_meshes.py -- ZIP [--check]

``--check`` compares the facts with the committed table and the committed
files' digests without writing (a Blender upgrade that reads them otherwise).
"""

import hashlib
import json
import math
import os
import re
import sys
import tempfile
import zipfile
from pathlib import Path

import bpy

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
TABLE = ADDON / "human_base_meshes.json"
FILES = ADDON / "human_base_meshes"
SOURCE = {
    "name": "Human Base Meshes: Asset Bundle",
    "version": "1.4.1",
    "url": "https://download.blender.org/demo/asset-bundles/human-base-meshes/human-base-meshes-bundle-v1.4.1.zip",
    "sha256": "811f43accbb31a88266d932f8f5563b2d13586fca0ba2693aad1f5fe582b3515",
    "bytes": 50643039,
    "license": "CC0-1.0",
    "authors": "the Blender community and the Blender Studio",
}
BLEND = "human-base-meshes-bundle-v1.4.1/human_base_meshes_bundle.blend"
MAX_MULTIRES_LEVELS = 1
# Face-set regions of the three head meshes that show on the surface, named from a front render of each head coloured
# by face set. Sets hidden inside (the lids behind the eyes, the mouth's inside) keep their place only.
# +X is the head's left: it faces -Y.
REGIONS = {
    "GEO-head_sculpting_realistic": {4: "ear R", 5: "ear L", 7: "jaw, mouth and chin", 8: "eye sockets", 9: "nose",
                                     10: "shoulders", 11: "cranium, face and neck", 12: "neck cut"},
    "GEO-head_animation_realistic": {3: "ear R", 4: "ear L", 9: "eye socket R", 10: "eye socket L", 11: "nose",
                                     24: "jaw, mouth and chin", 33: "cranium and face", 35: "neck and shoulders",
                                     36: "neck cut"},
    "GEO-head_stylized": {6: "neck", 27: "eye sockets", 28: "eyelids", 29: "ears", 44: "cranium and face", 45: "nose",
                          46: "jaw, mouth and cheeks", 359: "neck cut"},
}


# Misspellings in the bundle's asset names, corrected in the name people read ("name"); "datablock_name" keeps the
# upstream spelling, since appending looks the data-block up by it, and file names stay as built (1.0.5 review).
UPSTREAM_SPELLING = {"Primitve": "Primitive"}


def display_name(name):
    name = name.strip()
    for wrong, right in UPSTREAM_SPELLING.items():
        name = name.replace(wrong, right)
    return name


def _slug(name):
    slug = "".join(ch if ch.isalnum() else "_" for ch in name.strip().lower()).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug


def _r(values):
    return [round(float(v), 4) for v in values]


def _evaluated_points(obj, depsgraph):
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        matrix = obj.matrix_world
        return [matrix @ v.co for v in mesh.vertices]
    finally:
        evaluated.to_mesh_clear()


def _box(points):
    low = [min(p[i] for p in points) for i in range(3)]
    high = [max(p[i] for p in points) for i in range(3)]
    return low, high


def _multires_levels(obj):
    return max((m.total_levels for m in obj.modifiers if m.type == "MULTIRES"), default=0)


def _main_mesh(objects):
    meshes = [o for o in objects if o.type == "MESH"]
    return max(meshes, key=lambda o: len(o.data.vertices) * max(o.dimensions)) if meshes else None


def _role(obj, main):
    """main, an eye part (eyelid, iris, sclera, eye) with its side, or part. Whole name tokens only: a substring
    test named an eyelid an eye."""
    if obj is main:
        return "main"
    tokens = [token for token in re.split(r"[^a-z]+", obj.name.lower()) if token]
    side = {"l": "L", "r": "R"}.get(tokens[-1], "") if tokens else ""
    for known in ("eyelid", "iris", "sclera", "eye"):
        if known in tokens:
            return ("%s %s" % (known, side)).strip()
    return "part"


def _regions(obj):
    names = REGIONS.get(obj.name)
    attribute = obj.data.attributes.get(".sculpt_face_set")
    if attribute is None:
        return None
    by_set = {}
    matrix, vertices = obj.matrix_world, obj.data.vertices
    for polygon in obj.data.polygons:
        by_set.setdefault(attribute.data[polygon.index].value, []).extend(polygon.vertices)
    regions = []
    for face_set, indices in sorted(by_set.items()):
        low, high = _box([matrix @ vertices[i].co for i in set(indices)])
        region = {"face_set": face_set, "box_min": _r(low), "box_max": _r(high)}
        if names and face_set in names:
            region["name"] = names[face_set]
        regions.append(region)
    return regions


def _landmarks(kind, main_points, every_point, eyes):
    """The asset's highest point and eye centres; a head's nose tip (its most forward middle point) and neck cut."""
    low, high = _box(main_points)
    marks = {"top": _r(max(every_point, key=lambda p: p.z))}
    for side in ("L", "R"):
        if side in eyes:
            marks["eye_" + side] = _r(eyes[side])
    if kind == "head":
        width = high[0] - low[0]
        middle = [p for p in every_point if abs(p.x) <= 0.02 * width]   # A primitive head's nose is its own mesh.
        marks["nose_tip"] = _r(min(middle, key=lambda p: p.y))
        marks["neck_bottom_z"] = round(low[2], 4)
    return marks


def _refuse_builder_identity(data, file_name):
    """The release scan reads files as they ship and cannot see inside a compressed .blend: read it uncompressed here."""
    import getpass
    import socket
    names = {getpass.getuser(), socket.gethostname().split(".")[0], str(Path.home()), tempfile.gettempdir()}
    lowered = data.lower()
    for name in names:
        for encoded in (name.lower().encode(), name.lower().encode("utf-16-le")):
            if len(name) >= 4 and encoded in lowered:
                raise SystemExit("%s holds this machine's %r" % (file_name, name))
    for pattern in ("/users/", "/home/", "/var/folders/", ":\\users\\", ":/users/"):
        for encoded in (pattern.encode(), pattern.encode("utf-16-le")):
            if encoded in lowered:
                raise SystemExit("%s holds a home or temporary path (%r)" % (file_name, pattern))


def _kind(name):
    lowered = name.lower()
    for kind in ("head", "body", "skull", "skeleton", "eye", "hand", "foot", "jaw"):
        if lowered.startswith(kind):
            return kind
    return "other"


def _assets():
    found = [(c, list(c.all_objects)) for c in bpy.data.collections if c.asset_data]
    found += [(o, [o]) for o in bpy.data.objects if o.asset_data]
    return sorted(found, key=lambda item: item[0].name.strip().lower())


def _all_points(objects, depsgraph):
    return [p for o in objects if o.type == "MESH" for p in _evaluated_points(o, depsgraph)]


def _place(objects, depsgraph):
    """Centre the asset's bounding box (every mesh of it) on the origin by moving its root objects."""
    low, high = _box(_all_points(objects, depsgraph))
    offset = [-(low[i] + high[i]) / 2 for i in range(3)]
    for obj in objects:
        if obj.parent is None or obj.parent not in objects:
            obj.location = [obj.location[i] + offset[i] for i in range(3)]
    bpy.context.view_layer.update()


def _style(name):
    """Primitive first: "Body Female - Primitve (Realistic)" (the bundle's spelling) is a primitive body."""
    lowered = name.lower()
    if "primitive" in lowered or "primitve" in lowered:
        return "Primitive"
    return next((s for s in ("Realistic", "Stylized", "Planar", "Generic") if s.lower() in lowered), "other")


def _facts(idb, objects, depsgraph, file_name, data, catalog):
    main = _main_mesh(objects)
    main_points = _evaluated_points(main, depsgraph)
    every_point = _all_points(objects, depsgraph)
    low, high = _box(every_point)
    eyes, rows, parts = {}, [], 0
    for obj in sorted(objects, key=lambda o: (o is not main, o.name)):
        role = _role(obj, main)
        if role == "part":   # Primitive bodies and the skeleton have dozens: counted, not listed.
            parts += 1
            continue
        row = {"name": obj.name, "type": obj.type, "role": role}
        if obj.type == "MESH":
            points = _evaluated_points(obj, depsgraph)
            box_low, box_high = _box(points)
            row.update(vertices=len(obj.data.vertices), faces=len(obj.data.polygons),
                       evaluated_vertices=len(points), box_min=_r(box_low), box_max=_r(box_high),
                       modifiers=[{"type": m.type, **({"levels": m.total_levels} if m.type == "MULTIRES" else
                                                      {"levels": m.levels} if m.type == "SUBSURF" else {})}
                                  for m in obj.modifiers],
                       uv_maps=[uv.name for uv in obj.data.uv_layers])
            if obj.data.shape_keys:
                row["shape_keys"] = [key.name for key in obj.data.shape_keys.key_blocks]
            if role.split()[0] in ("eye", "sclera") and role[-1] in "LR":   # The eyeball, never a lid or iris.
                eyes[role[-1]] = [(box_low[i] + box_high[i]) / 2 for i in range(3)]
        if obj.parent is not None:
            row["parent"] = obj.parent.name
        rows.append(row)
    kind = _kind(idb.name)
    asset = {"name": display_name(idb.name), "kind": kind,
             "style": _style(idb.name), "catalog": catalog, "datablock": "COLLECTION" if isinstance(
                 idb, bpy.types.Collection) else "OBJECT", "datablock_name": idb.name,
             "file": file_name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
             "size": _r([high[i] - low[i] for i in range(3)]), "main": main.name,
             "objects": rows, "other_parts": parts,
             "total_vertices": sum(len(o.data.vertices) for o in objects if o.type == "MESH"), "landmarks": _landmarks(kind, main_points, every_point, eyes)}
    regions = _regions(main) if main.name in REGIONS else None
    if regions:
        asset["regions"] = regions
    return asset


def main(argv):
    zip_path, check = argv[0], "--check" in argv[1:]
    digest = hashlib.sha256(Path(zip_path).read_bytes()).hexdigest()
    if digest != SOURCE["sha256"]:
        raise SystemExit("%s is not the pinned bundle (sha256 %s)" % (zip_path, digest))
    with tempfile.TemporaryDirectory() as work:
        with zipfile.ZipFile(zip_path) as archive:
            source_blend = archive.extract(BLEND, work)
        blend_digest = hashlib.sha256(Path(source_blend).read_bytes()).hexdigest()
        bpy.ops.wm.open_mainfile(filepath=source_blend, load_ui=False, use_scripts=False)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        out = Path(work) / "out"
        out.mkdir()
        table = {"source": dict(SOURCE, blend=BLEND, blend_sha256=blend_digest),
                 "blender_version": ".".join(str(part) for part in bpy.app.version),
                 "selection": "every asset whose Multires has at most %d level" % MAX_MULTIRES_LEVELS,
                 "placement": "each asset is centred on the origin (the box of all its meshes); +X is its left and it faces -Y",
                 "assets": [], "left_out": []}
        for idb, objects in _assets():
            levels = max((_multires_levels(o) for o in objects), default=0)
            if levels > MAX_MULTIRES_LEVELS:
                table["left_out"].append({"name": idb.name.strip(), "multires_levels": levels})
                continue
            _place(objects, depsgraph)
            depsgraph = bpy.context.evaluated_depsgraph_get()
            file_name = _slug(idb.name) + ".blend"
            if any(asset["file"] == file_name for asset in table["assets"]):
                raise SystemExit("Two assets share the file name %s" % file_name)
            catalog = idb.asset_data.catalog_simple_name
            idb.asset_clear()   # An appended copy would otherwise mark the person's IDs as assets of no catalog.
            path = out / file_name
            bpy.data.libraries.write(str(path), {idb}, path_remap="NONE", fake_user=False, compress=True)
            plain = out / ("plain-" + file_name)
            bpy.data.libraries.write(str(plain), {idb}, path_remap="NONE", fake_user=False, compress=False)
            _refuse_builder_identity(plain.read_bytes(), file_name)
            plain.unlink()
            table["assets"].append(_facts(idb, objects, depsgraph, file_name, path.read_bytes(), catalog))
        if check:
            committed = json.loads(TABLE.read_text(encoding="utf-8"))
            ok = _same_facts(committed, table)
            for asset in committed["assets"]:
                on_disk = FILES / asset["file"]
                if hashlib.sha256(on_disk.read_bytes()).hexdigest() != asset["sha256"]:
                    raise SystemExit("%s differs from its recorded digest" % on_disk)
            if not ok:
                raise SystemExit("The facts differ from the committed table; regenerate it")
            print("human base meshes: table and files agree")
            return
        FILES.mkdir(exist_ok=True)
        for old in FILES.glob("*.blend"):
            old.unlink()
        for asset in table["assets"]:
            (FILES / asset["file"]).write_bytes((out / asset["file"]).read_bytes())
        TABLE.write_text(json.dumps(table, indent=1, sort_keys=False) + "\n", encoding="utf-8")
        print("wrote %d assets, %d bytes" % (len(table["assets"]), sum(a["bytes"] for a in table["assets"])))


def _same_facts(committed, made):
    """Facts compared without the files' bytes and digests (a .blend's bytes vary between writes)."""
    def strip(table):
        return [{k: v for k, v in asset.items() if k not in ("bytes", "sha256")} for asset in table["assets"]]
    same = all(committed[key] == made[key] for key in ("source", "blender_version", "selection", "placement", "left_out"))
    return same and strip(committed) == strip(made)


if __name__ == "__main__":
    main(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
