# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Scenario ``scenario-heavy``: five edits in a dense castle scene, each one needing geometry to be read.

The scene (``heavy_scene``, ``heavy_plan``): about 2,000 objects and 2.7
million vertices, made from a fixed seed by the reference job and kept as
``start.blend`` beside the references (SHA-256 in the manifest). Before the
first message both sides of a trial append that same file into their scene
(``setup``), so they start from the same bytes; the time it takes is
recorded, outside every turn's time.

The turns are one continuing chat, like the default set. Each asks for an
everyday result that can only be had by reading the scene's geometry:

1. ``flag``: the highest vertex of a 692,224-vertex terrain whose hills are
   within a few per cent of each other;
2. ``trees``: the terrain's height under each of 300 trees, 120 of which
   float or are sunk;
3. ``heaviest``: polygon counts across about 1,250 meshes, the top three
   within 2.5 % of each other, then a reduction of the winner to half;
4. ``normals``: which faces of five dense walls (27,942 to 77,002 faces)
   are wound inwards;
5. ``tower``: a plain move, the control.

Every turn also checks that everything no turn names is exactly as the
plan made it (``untouched``), which holds again at the end. The perfect
agent of the dry run and the self-check (``build_turn``) reads with
attribute arrays and ray casts; it is not a model of how either side works.

Axes: the courtyard is centred on the origin, the gate faces -Y, Z is up, metres.
"""

import heavy_plan as plan
import heavy_scene_blend as blend

ID = "heavy"
TITLE = "Five everyday edits in a dense castle scene"
FRAME = 1
START = "start.blend"
SETUP_TIMEOUT_S = 900.0
EVERYTHING = {"prefix": ""}
FLAG, TREES, STATUE = {"name": plan.FLAG}, {"prefix": "Tree "}, {"name": plan.STATUE}
WALLS, WATCHTOWER = {"prefix": "Castle Wall "}, {"prefix": "Watchtower"}
NAMED = [FLAG, TREES, STATUE, WALLS, WATCHTOWER]
UNTOUCHED = {"kind": "untouched", "target": EVERYTHING, "exclude": NAMED,
             "label": "everything no task names is exactly as it was"}
CAMERAS = {
    "hero": {"location": plan.CAMERA_AT, "target": plan.CAMERA_TARGET, "lens": 35.0, "role": "reference",
             "label": "Hero (the scene camera)"},
    "front": {"location": (0.0, -400.0, 20.0), "target": (0.0, 0.0, 20.0), "ortho_scale": 420.0, "role": "reference",
              "label": "South (orthographic, looking +Y)"},
    "side": {"location": (-400.0, 0.0, 20.0), "target": (0.0, 0.0, 20.0), "ortho_scale": 420.0, "role": "reference",
             "label": "West (orthographic, looking +X)"},
    "top": {"location": (0.0, 0.0, 600.0), "target": (0.0, 0.0, 0.0), "ortho_scale": 420.0, "role": "reference",
            "label": "Plan (orthographic, +Y up)"},
    "closeup_castle": {"location": (40.0, -60.0, 25.0), "target": (0.0, 0.0, 8.0), "lens": 35.0, "role": "closeup",
                       "label": "Close-up: the castle"},
}
SUBJECT = ({"prefix": "Castle"}, FLAG, TREES, WATCHTOWER, STATUE)

TURNS = [
    {"id": "flag", "title": "The flag on the highest point", "references": [],
     "prompt": "Move the 'Flag' so that its pole stands on the ground at the single highest point of 'Terrain'. "
               "Change nothing else.",
     "checks": [{"kind": "summit", "target": FLAG, "terrain": plan.TERRAIN, "radius": 3.0, "height": 0.3,
                 "label": "the flag stands on the terrain's highest point"}, UNTOUCHED]},
    {"id": "trees", "title": "Every tree on the ground", "references": [],
     "prompt": "Some of the 300 trees ('Tree 001' to 'Tree 300') float above the terrain and some are sunk into "
               "it. Set every tree on the ground, so the foot of its trunk rests on the surface of 'Terrain' right "
               "below it. Don't move any tree sideways, and change nothing else.",
     "checks": [{"kind": "grounded", "target": TREES, "ground": plan.TERRAIN, "tolerance": 0.15, "sideways": 0.05,
                 "count": 300, "label": "all 300 trees stand on the terrain"}, UNTOUCHED]},
    {"id": "heaviest", "title": "Halve the heaviest object", "references": [],
     "prompt": "Which object in the scene has the most polygons? Tell me its name and how many it has, then "
               "reduce that object to about half as many polygons while keeping its shape. Change nothing else.",
     "checks": [{"kind": "reduced", "target": STATUE, "ratio": [0.35, 0.65], "shape": 0.05,
                 "label": "the object with the most polygons has about half of them"}, UNTOUCHED]},
    {"id": "normals", "title": "Wall faces point outwards", "references": [],
     "prompt": "Some faces of the castle's curtain walls (the objects named 'Castle Wall ...') point inwards. Fix "
               "them so every face of every wall points outwards, without moving any vertex. Change nothing else.",
     "checks": [{"kind": "outward", "target": WALLS, "label": "every wall face points outwards, no vertex moved"},
                UNTOUCHED]},
    {"id": "tower", "title": "Move the watchtower", "references": [],
     "prompt": "Move the 'Watchtower' 10 m along +X, together with its roof. Change nothing else.",
     "checks": [{"kind": "moved", "target": {"name": plan.WATCHTOWER}, "with": [plan.WATCHTOWER_ROOF],
                 "offset": [10.0, 0.0, 0.0], "tolerance": 0.02, "label": "the watchtower and its roof moved 10 m +X"},
                UNTOUCHED]},
]


# ------------------------------------------------------------------ the start scene

def make_start(bpy, folder):
    """Start job (``scenario_start_job``): build the scene from the plan, save ``start.blend``, then start again
    from the factory scene and append the file, as a trial does. Returns the facts the manifest keeps."""
    import time
    from pathlib import Path
    import scenario_build_kit as kit
    kit.remove_default_objects(bpy)
    built = blend.build(bpy, bpy.context.scene)
    saved = blend.save_start(bpy, Path(folder) / START)
    bpy.ops.wm.read_factory_settings(use_empty=False)
    started = time.perf_counter()
    appended = setup(bpy, bpy.context.scene, Path(folder) / START, saved["sha256"])
    return dict(built, **saved, append=appended, append_total_s=round(time.perf_counter() - started, 3))


def setup(bpy, scene, path, sha256):
    """Both sides, before the first message: the start file appended into ``scene`` (SHA-256 checked).

    NumPy, which the graders use, is loaded here, before any turn: a turn's code that is the first to import
    it and then drops what it imported (the dry run's scripted agent does) would leave a second NumPy, whose
    arrays the first one's functions reject.
    """
    import numpy  # noqa: F401
    return blend.append_start(bpy, scene, path, sha256)


# ------------------------------------------------------------------ the perfect agent (dry run, self-check)

def build_turn(bpy, turn_id):
    import bmesh
    import numpy
    from mathutils import Vector
    scene = bpy.context.scene
    if turn_id == "flag":
        terrain = scene.objects[plan.TERRAIN]
        positions = numpy.empty(len(terrain.data.vertices) * 3, numpy.float32)
        terrain.data.attributes["position"].data.foreach_get("vector", positions)
        highest = positions.reshape(-1, 3)[int(positions[2::3].argmax())]
        scene.objects[plan.FLAG].location = terrain.matrix_world @ Vector(highest)
    elif turn_id == "trees":
        terrain = scene.objects[plan.TERRAIN]
        for obj in scene.objects:
            if obj.name.startswith("Tree "):
                ground = blend.surface_ray(bpy, terrain, obj.location.x, obj.location.y)
                obj.location.z = ground
    elif turn_id == "heaviest":
        meshes = [obj for obj in scene.objects if obj.type == "MESH"]
        heaviest = max(meshes, key=lambda obj: len(obj.data.polygons))
        reduce = heaviest.modifiers.new("Decimate", "DECIMATE")
        reduce.ratio = 0.5
    elif turn_id == "normals":
        for obj in scene.objects:
            if obj.name.startswith("Castle Wall ") and obj.type == "MESH":
                mesh = bmesh.new()
                try:
                    mesh.from_mesh(obj.data)
                    bmesh.ops.recalc_face_normals(mesh, faces=mesh.faces)
                    mesh.to_mesh(obj.data)
                finally:
                    mesh.free()
                obj.data.update()
    elif turn_id == "tower":
        scene.objects[plan.WATCHTOWER].location.x += 10.0
    else:
        raise KeyError(turn_id)
    bpy.context.view_layer.update()


def build(bpy, upto=None):
    """The ground truth from the start scene already in place: every turn up to ``upto``."""
    for turn in TURNS:
        build_turn(bpy, turn["id"])
        if turn["id"] == upto:
            break

