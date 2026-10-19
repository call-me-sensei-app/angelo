# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The heavy scene's checks against right and wrong results (stock Blender, unbilled, nothing saved).

The reference job proves each turn's checks fail before the turn and pass
after the perfect edit. This job grades more: every variant builds the start
scene in place (``heavy_scene_blend.build``), makes the five perfect edits
(``scenario_heavy.build_turn``) and then gets one thing wrong. The end-of-
session pass (every turn's checks) must then fail exactly the checks named in
``VARIANTS``; the first variant gets nothing wrong and must pass them all::

    Blender --background --factory-startup --python heavy_variants.py

It prints one ``VARIANT`` line per build and ends with ``VARIANTS n of m as
expected``; the exit code is 0 only when all are.
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import heavy_plan as plan  # noqa: E402
import scenario_heavy as heavy  # noqa: E402

FLAG, TREES = "flag: the flag stands on the terrain's highest point", "trees: all 300 trees stand on the terrain"
HALF = "heaviest: the object with the most polygons has about half of them"
NORMALS = "normals: every wall face points outwards, no vertex moved"
TOWER = "tower: the watchtower and its roof moved 10 m +X"
UNTOUCHED = tuple("%s: everything no task names is exactly as it was" % turn["id"] for turn in heavy.TURNS)


def _objects(bpy):
    return bpy.context.scene.objects


def perfect(bpy):
    pass


def flag_on_a_lower_hill(bpy):
    import heavy_scene as scene
    import numpy
    positions = plan.geometry(plan.entry(plan.TERRAIN))[0]
    cx, cy = scene.DECOYS[0][:2]
    near = numpy.hypot(positions[:, 0] - cx, positions[:, 1] - cy) < 40.0
    highest = positions[near][int(positions[near][:, 2].argmax())]
    _objects(bpy)[plan.FLAG].location = [float(v) for v in highest]


def flag_floating(bpy):
    _objects(bpy)[plan.FLAG].location.z += 1.0


def flag_sunk(bpy):
    _objects(bpy)[plan.FLAG].location.z -= 0.5


def sunk_trees_left(bpy):
    """Only the floating trees were set down: the sunk ones are where they were."""
    for item in plan.plan():
        if item.name.startswith("Tree ") and item.location[2] < _objects(bpy)[item.name].location.z - 0.4:
            _objects(bpy)[item.name].location.z = item.location[2]


def tree_moved_sideways(bpy):
    _objects(bpy)["Tree 017"].location.x += 1.0


def too_little_reduction(bpy):
    _objects(bpy)[plan.STATUE].modifiers["Decimate"].ratio = 0.9


def terrain_reduced_instead(bpy):
    _objects(bpy)[plan.STATUE].modifiers.remove(_objects(bpy)[plan.STATUE].modifiers["Decimate"])
    _objects(bpy)[plan.TERRAIN].modifiers.new("Decimate", "DECIMATE").ratio = 0.5


def walls_turned_inwards(bpy):
    import bmesh
    for obj in _objects(bpy):
        if obj.name.startswith("Castle Wall "):
            mesh = bmesh.new()
            mesh.from_mesh(obj.data)
            bmesh.ops.reverse_faces(mesh, faces=mesh.faces)
            mesh.to_mesh(obj.data)
            mesh.free()


def wall_vertex_moved(bpy):
    _objects(bpy)["Castle Wall North"].data.vertices[1000].co.y += 0.05


def terrain_flipped(bpy):
    import bmesh
    terrain = _objects(bpy)[plan.TERRAIN]
    mesh = bmesh.new()
    mesh.from_mesh(terrain.data)
    bmesh.ops.reverse_faces(mesh, faces=mesh.faces)
    mesh.to_mesh(terrain.data)
    mesh.free()


def roof_left_behind(bpy):
    """The tower was moved after its roof was unparented (keeping its place)."""
    tower, roof = _objects(bpy)[plan.WATCHTOWER], _objects(bpy)[plan.WATCHTOWER_ROOF]
    tower.location.x -= 10.0
    bpy.context.view_layer.update()
    world = roof.matrix_world.copy()
    roof.parent = None
    roof.matrix_world = world
    tower.location.x += 10.0


def tower_moved_along_y(bpy):
    tower = _objects(bpy)[plan.WATCHTOWER]
    tower.location.x -= 10.0
    tower.location.y += 10.0


def rock_nudged(bpy):
    _objects(bpy)["Rock 0001"].location.x += 0.2


def house_deleted(bpy):
    bpy.data.objects.remove(bpy.data.objects["House 01"], do_unlink=True)


VARIANTS = (
    ("the five right edits", perfect, ()),
    ("the flag on the second-highest hill", flag_on_a_lower_hill, (FLAG,)),
    ("the flag 1 m above the summit", flag_floating, (FLAG,)),
    ("the flag sunk 0.5 m into the summit", flag_sunk, (FLAG,)),
    ("only the floating trees set down", sunk_trees_left, (TREES,)),
    ("a tree moved 1 m sideways", tree_moved_sideways, (TREES,)),
    ("the heaviest object reduced by a tenth", too_little_reduction, (HALF,)),
    ("the terrain halved instead", terrain_reduced_instead, (HALF,) + UNTOUCHED),
    ("every wall face turned inwards", walls_turned_inwards, (NORMALS,)),
    ("a wall vertex moved 5 cm", wall_vertex_moved, (NORMALS,)),
    ("the terrain's faces flipped as well", terrain_flipped, UNTOUCHED),
    ("the roof left behind", roof_left_behind, (TOWER,)),
    ("the watchtower moved along +Y", tower_moved_along_y, (TOWER,)),
    ("a rock nudged 20 cm", rock_nudged, UNTOUCHED),
    ("a house deleted", house_deleted, UNTOUCHED),
)


def grade(bpy, label, breaker, expected):
    import heavy_scene_blend as blend
    import scenario_checks
    import scenario_specs
    bpy.ops.wm.read_factory_settings(use_empty=False)
    blend.remove_factory(bpy)
    blend.build(bpy, bpy.context.scene)
    for turn in heavy.TURNS:
        heavy.build_turn(bpy, turn["id"])
    breaker(bpy)
    bpy.context.view_layer.update()
    result = scenario_checks.evaluate(bpy, bpy.context.scene, scenario_specs.all_checks(heavy.ID))
    failed = sorted(entry["label"] for entry in result["results"] if not entry["passed"])
    as_expected = failed == sorted(expected)
    print("VARIANT", json.dumps({"variant": label, "as_expected": as_expected, "failed": failed}), flush=True)
    if not as_expected:
        for entry in result["results"]:
            if not entry["passed"] or entry["label"] in expected:
                print("  DETAIL", entry["label"], json.dumps(entry.get("details"), default=str)[:400], flush=True)
    return as_expected


def main():
    import bpy
    done = [grade(bpy, *variant) for variant in VARIANTS]
    print("VARIANTS %d of %d as expected" % (sum(done), len(done)), flush=True)
    return 0 if all(done) else 1


if __name__ == "__main__":
    sys.exit(main())
