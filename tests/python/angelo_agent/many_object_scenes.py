# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The start scenes of the many-object scenarios (``scenario_shop``, ``scenario_workshop``).

Each is a plain, deterministic interior of 60 to 70 named objects: far more than the eight rows a turn
observation lists, so most objects a turn edits are neither listed with a revision nor named exactly in the
message. Objects are made in a fixed order (filler first), so the listing's first rows are never a turn's
targets. Each scene keeps a numbered row ('Stool 1' to 'Stool 4', 'Tool Rack 1' to 'Tool Rack 3') that a turn
continues. Neither scene, nor any name, size or count here, comes from the run that exposed the class.

``plan(scene_id)`` is the scene as data (``Item``); ``build`` makes it; ``save_start``/``append_start`` keep it as
the scenario's start file, appended by both sides of a trial before the first message (``scenario_specs``).
"""

import hashlib
import os
import time
from typing import NamedTuple

COLLECTION = "Many Object Scene"


class Item(NamedTuple):
    name: str
    kind: str  # "box" or "light" or "camera"
    location: tuple
    size: tuple = (1.0, 1.0, 1.0)
    rotation: tuple = (0.0, 0.0, 0.0)
    light: tuple = ()  # (type, energy, colour)
    data: str = ""  # Shared mesh data name: copies in a row share one mesh, as Shift+D gives.


def _shop():
    items = [Item("Floor", "box", (0, 0, -0.05), (9, 7, 0.1)), Item("Back Wall", "box", (0, 3.5, 1.5), (9, 0.2, 3)),
             Item("Left Wall", "box", (-4.5, 0, 1.5), (0.2, 7, 3)), Item("Right Wall", "box", (4.5, 0, 1.5), (0.2, 7, 3)),
             Item("Ceiling", "box", (0, 0, 3.05), (9, 7, 0.1)), Item("Front Window", "box", (4.4, 0, 1.6), (0.05, 2.4, 1.6)),
             Item("Door", "box", (-4.4, -1.5, 1.05), (0.06, 1.0, 2.1)), Item("Counter", "box", (-0.2, -1.8, 0.5), (4.2, 0.7, 1.0)),
             Item("Rug", "box", (0.6, 0.6, 0.005), (2.6, 1.8, 0.01)), Item("Desk", "box", (2.6, 2.4, 0.38), (1.4, 0.7, 0.76)),
             Item("Desk Lamp", "box", (2.95, 2.5, 0.98), (0.22, 0.22, 0.44)),
             Item("Cash Register", "box", (-1.6, -1.8, 1.12), (0.42, 0.36, 0.24))]
    items += [Item("Plant Pot %d" % n, "box", (3.9, -2.8 + 1.1 * n, 0.2), (0.4, 0.4, 0.4)) for n in (1, 2, 3)]
    for n in range(10):
        x = -3.9 + 0.88 * n
        items.append(Item("Shelf %02d" % (n + 1), "box", (x, 3.15, 1.1), (0.84, 0.36, 2.2)))
    for n in range(30):
        shelf, slot = divmod(n, 3)
        x = -3.9 + 0.88 * shelf - 0.25 + 0.25 * slot
        items.append(Item("Book %03d" % (n + 1), "box", (x, 3.05, 0.35 + 0.55 * (n % 4)), (0.06, 0.22, 0.3), data="Book"))
    items += [Item("Stool %d" % n, "box", (-2.0 + 0.6 * (n - 1), -1.15, 0.36), (0.36, 0.36, 0.72), data="Stool")
              for n in (1, 2, 3, 4)]
    items += [Item("Lantern %02d" % n, "box", (-3.3 + 1.3 * (n - 1), 0.2, 2.55), (0.24, 0.24, 0.36), data="Lantern")
              for n in range(1, 7)]
    items += [Item("Ceiling Light %d" % n, "light", (-2.5 + 2.5 * (n - 1), 0.0, 2.9), light=("POINT", 150.0, (1.0, 0.85, 0.65)))
              for n in (1, 2, 3)]
    items += [Item("Window Light", "light", (4.1, 0.0, 1.7), rotation=(0.0, -1.5708, 0.0), light=("AREA", 80.0, (1.0, 0.8, 0.55))),
              Item("Fill Light", "light", (0.0, -3.2, 2.4), rotation=(1.2, 0.0, 0.0), light=("AREA", 40.0, (1.0, 0.95, 0.9))),
              Item("Shop Camera", "camera", (0.0, -6.5, 1.7), rotation=(1.48, 0.0, 0.0))]
    return items


def _workshop():
    items = [Item("Floor", "box", (0, 0, -0.05), (10, 8, 0.1)), Item("Back Wall", "box", (0, 4, 1.75), (10, 0.2, 3.5)),
             Item("Left Wall", "box", (-5, 0, 1.75), (0.2, 8, 3.5)), Item("Right Wall", "box", (5, 0, 1.75), (0.2, 8, 3.5)),
             Item("Garage Door", "box", (0, -4, 1.4), (4.0, 0.08, 2.8)), Item("Workbench", "box", (-3.2, 2.9, 0.45), (2.4, 0.9, 0.9)),
             Item("Vise", "box", (-2.3, 2.6, 1.0), (0.2, 0.25, 0.2)), Item("Pegboard", "box", (-3.2, 3.85, 1.7), (2.4, 0.04, 1.2)),
             Item("Toolbox", "box", (-4.4, 1.0, 0.5), (0.6, 0.5, 1.0)), Item("Car Lift", "box", (1.2, 0.2, 0.08), (2.2, 4.4, 0.16)),
             Item("Car Body", "box", (1.2, 0.2, 0.85), (1.8, 4.2, 1.2))]
    items += [Item("Wheel %d" % n, "box", (1.2 + 0.95 * sx, 0.2 + 1.35 * sy, 0.4), (0.22, 0.64, 0.64), data="Wheel")
              for n, (sx, sy) in enumerate(((-1, -1), (1, -1), (-1, 1), (1, 1)), 1)]
    items += [Item("Tire Stack %d" % n, "box", (4.4, -3.0 + 0.9 * n, 0.4), (0.7, 0.7, 0.8), data="Tire Stack") for n in range(1, 6)]
    for n in range(12):
        row, column = divmod(n, 4)
        items.append(Item("Crate %02d" % (n + 1), "box", (-4.2 + 0.7 * column, -2.6 + 0.7 * row, 0.3), (0.6, 0.6, 0.6),
                          data="Crate"))
    items += [Item("Oil Can %d" % n, "box", (-1.6 + 0.18 * n, 2.75, 0.98), (0.1, 0.1, 0.16), data="Oil Can") for n in range(1, 9)]
    items += [Item("Shelf Light %d" % n, "light", (-4.0 + 2.0 * (n - 1), 3.6, 3.1), rotation=(0.6, 0.0, 0.0),
                   light=("AREA", 30.0, (0.6, 0.75, 1.0))) for n in range(1, 6)]
    items += [Item("Tool Rack %d" % n, "box", (0.5 + 1.5 * (n - 1), 3.6, 1.0), (1.2, 0.4, 2.0), data="Tool Rack")
              for n in (1, 2, 3)]
    items += [Item("Ceiling Lamp", "light", (0, 0, 3.3), light=("SPOT", 400.0, (1.0, 0.95, 0.85))),
              Item("Workshop Camera", "camera", (0.0, -7.5, 2.2), rotation=(1.4, 0.0, 0.0))]
    return items


PLANS = {"shop": _shop, "workshop": _workshop}


def plan(scene_id):
    return PLANS[scene_id]()


def entry(scene_id, name):
    return next(item for item in plan(scene_id) if item.name == name)


def camera_name(scene_id):
    return next(item.name for item in plan(scene_id) if item.kind == "camera")


def build(bpy, scene_id, scene):
    """The scene's objects in plan order, in one collection linked to ``scene``; returns counts."""
    import scenario_build_kit as kit
    collection = bpy.data.collections.new(COLLECTION)
    scene.collection.children.link(collection)
    shared = {}
    for item in plan(scene_id):
        if item.kind == "box":
            mesh = shared.get(item.data) if item.data else None
            if mesh is None:
                vertices, faces = kit.box_geometry(item.size)
                mesh = bpy.data.meshes.new(item.data or item.name)
                mesh.from_pydata(vertices, [], faces)
                if item.data:
                    shared[item.data] = mesh
            obj = bpy.data.objects.new(item.name, mesh)
        elif item.kind == "light":
            kind, energy, colour = item.light
            data = bpy.data.lights.new(item.name, kind)
            data.energy, data.color = energy, colour
            obj = bpy.data.objects.new(item.name, data)
        else:
            obj = bpy.data.objects.new(item.name, bpy.data.cameras.new(item.name))
            scene.camera = obj
        obj.location, obj.rotation_euler = item.location, item.rotation
        collection.objects.link(obj)
    bpy.context.view_layer.update()
    return {"objects": len(collection.objects)}


def save_start(bpy, path):
    """The start file, uncompressed and without a thumbnail; returns ``{"file", "bytes", "sha256", "save_s"}``."""
    started = time.perf_counter()
    preferences = bpy.context.preferences.filepaths
    kept = preferences.file_preview_type
    preferences.file_preview_type = "NONE"
    try:
        bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True, compress=False, check_existing=False)
    finally:
        preferences.file_preview_type = kept
    return {"file": os.path.basename(str(path)), "bytes": os.path.getsize(path), "sha256": sha256(path),
            "save_s": round(time.perf_counter() - started, 2)}


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def append_start(bpy, scene_id, scene, path, expected_sha256=None):
    """Remove the factory objects and append the start file's collection into ``scene``; returns what it took."""
    import scenario_build_kit as kit
    started = time.perf_counter()
    digest = sha256(path)
    if expected_sha256 and digest != expected_sha256:
        raise ValueError("The %s start file changed on disk (%s != %s)" % (scene_id, digest, expected_sha256))
    kit.remove_default_objects(bpy)
    with bpy.data.libraries.load(str(path), link=False) as (source, into):
        into.collections = [name for name in source.collections if name == COLLECTION]
    for collection in into.collections:
        scene.collection.children.link(collection)
    scene.camera = bpy.data.objects.get(camera_name(scene_id))
    bpy.context.view_layer.update()
    return {"file": os.path.basename(str(path)), "sha256": digest, "objects": len(scene.objects),
            "append_s": round(time.perf_counter() - started, 3)}


def make_start(bpy, scene_id, folder, start):
    """Start job: build the scene, save the start file, then start again from the factory scene and append it, as a
    trial does. Returns the facts the manifest keeps."""
    from pathlib import Path
    import scenario_build_kit as kit
    kit.remove_default_objects(bpy)
    built = build(bpy, scene_id, bpy.context.scene)
    saved = save_start(bpy, Path(folder) / start)
    bpy.ops.wm.read_factory_settings(use_empty=False)
    appended = append_start(bpy, scene_id, bpy.context.scene, Path(folder) / start, saved["sha256"])
    return dict(built, **saved, append=appended)
