# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The reviewed-script kit (script_kit) in Blender: the adversarial reviews' cases.

Each case is a finding of the kit's adversarial reviews (2 to 4, and the castle runs' crash on a removed cutter)
or its guard: targets the cut cannot prove are refused and left alone; a cut reaches only the parts it touches,
keeps every face on its slot and the target's own stack; checks reach the host whole and stay small; a cutter can
cut several targets and a removed object is refused, never handed to Blender. Background, local only; writes
results.json under ANGELO_QA_DIR.
"""

import json
import math
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy
import bmesh
from mathutils import Vector

try:
    from angelo_agent.script_kit import ScriptKit
except ImportError:   # A stock Blender run from the checkout.
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
    from angelo_agent.script_kit import ScriptKit

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-script-kit-reviews-qa"))
OUT.mkdir(parents=True, exist_ok=True)
checks = {}


def fresh():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    return ScriptKit(bpy, bpy.context.scene)


def overlapping_wall(name):
    bm = bmesh.new()
    for centre, size in [((0, -15, 4.5), (42, 2, 9)), ((0, 15, 4.5), (42, 2, 9)), ((-20, 0, 4.5), (2, 28, 9)),
                         ((20, 0, 4.5), (2, 28, 9))]:
        for vertex in bmesh.ops.create_cube(bm, size=1)["verts"]:
            vertex.co = Vector(tuple(v * s for v, s in zip(vertex.co, size))) + Vector(centre)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def case(name):
    def wrap(function):
        try:
            checks[name] = bool(function())
        except Exception:
            checks[name] = False
            print(traceback.format_exc())
        return function
    return wrap




def refused(function, words):
    try:
        function()
    except ValueError as error:
        return words in str(error)
    return False


def volume(obj):
    bm = bmesh.new()
    bm.from_object(obj, bpy.context.evaluated_depsgraph_get())
    bm.transform(obj.matrix_world)
    value = abs(bm.calc_volume(signed=True))
    bm.free()
    return value


@case("targets_it_cannot_prove_are_refused_and_left_alone")
def _():
    kit = fresh()   # Review 2 B2-1, B2-2, F2-1, F2-8, N2-6: open, shaped, edited, inward and shared cases.
    wall = kit.box("Open Wall", (-6, -0.5, 0), (6, 0.5, 8))
    bm = bmesh.new()
    bm.from_mesh(wall.data)
    bm.faces.ensure_lookup_table()
    bm.faces.remove(min(bm.faces, key=lambda face: face.calc_center_median().z))   # No bottom face.
    bm.to_mesh(wall.data)
    bm.free()
    vertices = len(wall.data.vertices)
    open_wall = refused(lambda: kit.arch(wall, 0, 2, 3, (-1, 1)), "not a closed solid")
    fence = kit.box("Fence", (0, 0, 0), (1.5, 0.1, 1))
    fence.modifiers.new("Array", "ARRAY")
    shaped = refused(lambda: kit.cut(fence, kit.box("Hole", (0.5, -1, 0.3), (1, 1, 0.6))), "makes or moves geometry")
    room = kit.box("Room", (-3, -3, 0), (3, 3, 3))
    room.data.flip_normals()
    inward = refused(lambda: kit.cut(room, kit.box("Room Door", (-0.5, -4, 0.2), (0.5, -2, 2))), "wound inward")
    block, door = kit.box("Leaf", (0, 0, 0), (4, 0.2, 3)), kit.box("Shared Door", (1, -1, 0.2), (2, 1, 2))
    other = kit.box("Other Leaf", (0, 2, 0), (4, 2.2, 3))
    other.modifiers.new("Door", "BOOLEAN").object = door
    shared = refused(lambda: kit.cut(block, door), "also used by 'Other Leaf'")
    bpy.context.view_layer.objects.active = block
    bpy.ops.object.mode_set(mode="EDIT")
    edited = refused(lambda: kit.cut(block, kit.box("Edit Door", (1, -1, 0.2), (2, 1, 2))), "Object Mode")
    bpy.ops.object.mode_set(mode="OBJECT")
    return (open_wall and shaped and inward and shared and edited and len(wall.data.vertices) == vertices
            and "Open Wall arch cutter" not in bpy.data.objects and "Shared Door" in bpy.data.objects
            and len(block.data.vertices) == 8)


@case("a_cut_reaches_only_the_parts_it_touches_and_removes_the_overlap")
def _():
    kit = fresh()   # Review 2 F2-6 and N7: a far part made the whole mesh take the slow path, and was re-cut.
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=6, radius=2.0)
    for vertex in bmesh.ops.create_cube(bm, size=1.0)["verts"]:
        vertex.co.z -= 2.4   # A base touching the sphere.
    mesh = bpy.data.meshes.new("Statue")
    bm.to_mesh(mesh)
    bm.free()
    statue = bpy.data.objects.new("Statue", mesh)
    bpy.context.scene.collection.objects.link(statue)
    import time
    start = time.perf_counter()
    kit.cut(statue, kit.box("Slot", (-0.4, -3, -0.5), (0.4, 3, 0.5)))
    seconds = time.perf_counter() - start
    row = kit.row("Posts", 0.0, 10.0, "X", 0.0, 0.0, unit=(1.0, 0.5, 1.0), gap=0.5)   # Seven posts filling 0-10 m.
    before = volume(row)
    kit.cut(row, kit.box("Notch", (0.0, -1, 0.6), (0.6, 1, 2)))
    removed = before - volume(row)
    print("ANGELO_SCRIPT_KIT_CUT_S", round(seconds, 3))
    return seconds < 2.0 and abs(removed - 0.6 * 0.5 * 0.4) < 1e-4


@case("shared_faces_and_generous_cutters_are_cut")
def _():
    kit = fresh()   # Review 2 F2-9 and F2-10: a merged L of abutting walls and a 1 m trim on a 2 cm part.
    bm = bmesh.new()
    for lo, hi in (((0, 0, 0), (6, 1, 3)), ((5, 1, 0), (6, 6, 3))):
        for vertex in bmesh.ops.create_cube(bm, size=1)["verts"]:
            vertex.co = Vector(tuple((a + b) / 2 + v * (b - a) for v, a, b in zip(vertex.co, lo, hi)))
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-4)
    mesh = bpy.data.meshes.new("L Wall")
    bm.to_mesh(mesh)
    bm.free()
    wall = bpy.data.objects.new("L Wall", mesh)
    bpy.context.scene.collection.objects.link(wall)
    kit.cut(wall, kit.box("L Door", (1, -1, -1), (2, 2, 2)))
    clear = kit.check_ray("door clear", (1.5, -2, 1), (0, 1, 0), 4.0, tolerance=0.01)   # Through, to nothing.
    bracket = kit.box("Bracket", (0, 0, 0), (0.02, 0.02, 0.005))
    kit.cut(bracket, kit.box("Trim", (0.015, -0.5, -0.5), (1.0, 0.5, 0.5)))
    return not clear and kit.checks[-1]["measured"] is None and abs(volume(bracket) - 1.5e-6) < 1e-8


@case("cuts_keep_the_target_slots_and_mesh_name")
def _():
    kit = fresh()   # Review 2 N2-4 and N2-5.
    stone, glass = bpy.data.materials.new("Stone"), bpy.data.materials.new("Glass")
    wall = kit.box("Wall", (0, 0, 0), (4, 0.5, 3), stone)
    wall.data.name = "Wall Mesh LOD0"
    kit.cut(wall, kit.box("Window", (1, -1, 1), (2, 2, 2), glass))
    return list(wall.data.materials) == [stone] and wall.data.name == "Wall Mesh LOD0"


@case("bounds_are_world_vertices_and_rays_are_never_vacuous")
def _():
    import numpy
    kit = fresh()   # Review 2 F2-11, F2-3, F2-4.
    tower = kit.cylinder("Tower", (0, 0), 5.0, 0, 10)
    tower.rotation_euler.z = math.pi / 4
    footprint = kit.check_bounds("footprint", "Tower", (-5, -5, 0), (5, 5, 10), tolerance=0.05)
    kit.box("Castle Wall", (-1, 2, 0), (1, 3, 3))
    misspelt = kit.check_ray("clear", (0, 0, 1), (0, 1, 0), None, prefix="castle Wall")
    nan = kit.check_ray("clear nan", (0, 0, 1), (0, float("nan"), 0), None)
    bad = [kit.check("tol none", 1.0, 1.0, tolerance=None), kit.check("tol nan", 1.0, 1.0, tolerance=float("nan"))]
    good = kit.check("tol float32", numpy.float32(1.0), 1.0, tolerance=numpy.float32(0.05))
    json.dumps(kit.outcome(), allow_nan=False)
    return footprint and not misspelt and not nan and not any(bad) and good


@case("every_pass_reaches_the_host_and_lists_stay_small")
def _():
    kit = fresh()   # Review 2 F2-2, F2-5, N2-8: passes past the list, long value lists, more failures than listed.
    for index in range(70):
        kit.check("arch %d" % index, 1.0, 1.0)
    kit.check("heights", list(range(5000)), list(range(5000)))
    for index in range(300):
        kit.check("bad %d" % index, 1.0, 2.0)
    outcome = kit.outcome()
    names = outcome["self_checks_passed"]
    return ("arch 69" in names and "heights" in names and len(json.dumps(outcome)) < 40000
            and "bad 299" in outcome["self_checks_failed"] and outcome["self_checks"][-1]["passed"] is None
            and outcome["self_checks"][-1]["measured"] == 300 - (160 - 32))


@case("an_odd_arch_and_a_tall_stack_measure_right_and_fast")
def _():
    import time
    kit = fresh()   # Review 2 N2-3 and F2-7.
    nave = kit.box("Nave", (-40, -1, 0), (40, 1, 50))
    kit.arch(nave, 0.0, 60.0, 10.0, (-2, 2), segments=23)
    crown = kit.checks[-1]["passed"]
    bm = bmesh.new()
    for level in range(4000):
        for vertex in bmesh.ops.create_cube(bm, size=1.0)["verts"]:
            vertex.co.z += level + 0.5
    mesh = bpy.data.meshes.new("Pier")
    bm.to_mesh(mesh)
    bm.free()
    bpy.context.scene.collection.objects.link(bpy.data.objects.new("Pier", mesh))
    start = time.perf_counter()
    stands = kit.check_supported("pier stands", "Pier")
    return crown and stands and time.perf_counter() - start < 1.0


@case("a_cut_keeps_every_face_on_its_own_slot")
def _():
    kit = fresh()   # Review 3 B3-1: clearing the slots moved every face of the object to the first.
    plaster, brick = bpy.data.materials.new("Plaster"), bpy.data.materials.new("Brick")
    wall = kit.box("Slotted Wall", (0, 0, 0), (4, 0.5, 3), plaster)
    wall.data.materials.append(brick)
    top = max(wall.data.polygons, key=lambda polygon: polygon.center.z)
    top.material_index = 1
    plinth = kit.box("Slotted Plinth", (10, 0, 0), (11, 1, 1))
    plinth.data.materials.append(plaster)
    plinth.data.materials.append(brick)
    for polygon in plinth.data.polygons:
        polygon.material_index = 1
    bm = bmesh.new()
    for mesh in (wall.data, plinth.data):
        bm.from_mesh(mesh)
    joined = wall.data.copy()
    bm.to_mesh(joined)
    bm.free()
    wall.data = joined
    bpy.data.objects.remove(plinth, do_unlink=True)
    kit.cut(wall, kit.box("Slotted Door", (1, -1, -1), (2, 2, 2)))
    used = [polygon.material_index for polygon in wall.data.polygons]
    return used.count(1) == 1 + 6 and len(wall.data.materials) == 2


@case("shape_keeping_stacks_and_subtracting_booleans_are_cut")
def _():
    kit = fresh()   # Review 3 F3-2: Smooth by Angle (geometry nodes), a level-0 Subdivision, the person's own door.
    group = bpy.data.node_groups.new("Smooth by Angle", "GeometryNodeTree")   # Shade Auto Smooth's group's name.
    group.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    group.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    entry, exit_ = group.nodes.new("NodeGroupInput"), group.nodes.new("NodeGroupOutput")
    group.links.new(entry.outputs[0], exit_.inputs[0])
    wall = kit.box("Stacked Wall", (0, 0, 0), (6, 0.5, 3))
    wall.modifiers.new("Smooth", "NODES").node_group = group
    subdivision = wall.modifiers.new("Subdivision", "SUBSURF")
    subdivision.levels = subdivision.render_levels = 0
    wall.modifiers.new("Their Door", "BOOLEAN").object = kit.box("Their Door Cutter", (4, -1, -1), (5, 2, 2))
    kit.cut(wall, kit.box("Our Door", (1, -1, -1), (2, 2, 2)))
    array = kit.box("Render Fence", (0, 5, 0), (1, 5.1, 1)).modifiers.new("Array", "ARRAY")
    array.show_viewport = False
    render_only = refused(lambda: kit.cut("Render Fence", kit.box("Gap", (0.3, 4, 0.3), (0.6, 6, 0.6))), "Array")
    # Review 4 B4-1 and F4-1: at rest a Mask or a render-level Subdivision moves nothing, yet the cut breaks them;
    # a vertex parent moves when a cut renumbers the vertices.
    masked = kit.box("Masked Wall", (0, 8, 0), (4, 8.5, 3))
    masked.vertex_groups.new(name="Keep").add(list(range(8)), 1.0, "REPLACE")
    masked.modifiers.new("Mask", "MASK").vertex_group = "Keep"
    mask = refused(lambda: kit.cut(masked, kit.box("Mask Door", (1, 7, -1), (2, 9, 2))), "Mask")
    smooth = kit.box("Render Smooth", (0, 12, 0), (4, 12.5, 3))
    levels = smooth.modifiers.new("Subdivision", "SUBSURF")
    levels.levels, levels.render_levels = 0, 2
    render_levels = refused(lambda: kit.cut(smooth, kit.box("Smooth Door", (1, 11, -1), (2, 13, 2))), "Subdivision")
    lamp_wall = kit.box("Lamp Wall", (0, 16, 0), (4, 16.5, 3))
    lamp = bpy.data.objects.new("Lamp", None)
    bpy.context.scene.collection.objects.link(lamp)
    lamp.parent, lamp.parent_type, lamp.parent_vertices = lamp_wall, "VERTEX", (7, 0, 0)
    vertex_parent = refused(lambda: kit.cut(lamp_wall, kit.box("Lamp Door", (1, 15, -1), (2, 17, 2))), "Lamp")
    return (len(wall.data.vertices) > 8 and len(wall.modifiers) == 3 and render_only and mask and render_levels
            and vertex_parent)


@case("a_miss_is_refused_fast_and_a_block_of_parts_is_swept_fast")
def _():
    import time
    kit = fresh()   # Review 3 F3-3 and the sweep note: a miss paid the self-intersection retry; blocks were quadratic.
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=6, radius=2.0)
    mesh = bpy.data.meshes.new("Ball")
    bm.to_mesh(mesh)
    bm.free()
    ball = bpy.data.objects.new("Ball", mesh)
    bpy.context.scene.collection.objects.link(ball)
    start = time.perf_counter()
    missed = refused(lambda: kit.cut(ball, kit.box("Corner", (1.7, 1.7, 1.7), (1.95, 1.95, 1.95))), "does not overlap")
    miss_seconds = time.perf_counter() - start
    bm = bmesh.new()
    for x in range(30):
        for y in range(30):
            for z in range(10):
                for vertex in bmesh.ops.create_cube(bm, size=1.0)["verts"]:
                    vertex.co += Vector((x, y, z + 0.5))
    mesh = bpy.data.meshes.new("Block")
    bm.to_mesh(mesh)
    bm.free()
    bpy.context.scene.collection.objects.link(bpy.data.objects.new("Block", mesh))
    start = time.perf_counter()
    stands = kit.check_supported("block stands", "Block")
    sweep_seconds = time.perf_counter() - start
    print("ANGELO_SCRIPT_KIT_MISS_S", round(miss_seconds, 3), "SWEEP_S", round(sweep_seconds, 3))
    return missed and miss_seconds < 1.0 and stands and sweep_seconds < 3.0


@case("one_cutter_cuts_several_targets_and_a_removed_object_is_refused")
def _():
    kit = fresh()   # The castle runs (fix17 trial 4): kit.cut(gate, c); kit.cut(wall, c) crashed the app in user_map.
    gate, wall = kit.box("Gate Block", (-3, -1, 0), (3, 1, 6)), kit.box("Wall Run", (-12, -0.5, 0), (12, 0.5, 5))
    cutter = kit.box("Through Cutter", (-1, -3, -1), (1, 3, 3))
    kit.cut(gate, cutter)
    kit.cut(wall, cutter)
    clear = kit.check_ray("both open", (0, -5, 1), (0, 1, 0), None)
    lamp = bpy.data.objects.new("Gate Lamp", None)
    bpy.context.scene.collection.objects.link(lamp)
    lamp.parent, lamp.location = cutter, (0, 0, 2)   # Parented to the cutter after its cuts: stays where it is.
    bpy.context.view_layer.update()
    kit.finish()
    gone = "Through Cutter" not in bpy.data.objects and abs(lamp.matrix_world.translation.z - 2.0) < 1e-6
    other, glass = kit.box("Spare", (0, 0, 10), (1, 1, 11)), bpy.data.materials.new("Spare Glass")
    bpy.data.objects.remove(other, do_unlink=True)
    bpy.data.materials.remove(glass)
    stale = refused(lambda: kit.cut(wall, other), "removed") and refused(
        lambda: kit.box("Pane", (0, 0, 0), (1, 1, 1), glass), "removed") and not kit.check_bounds(
        "removed", [other], (0, 0, 0), None)
    return clear and gone and stale and len(gate.data.polygons) > 6 and len(wall.data.polygons) > 6


@case("a_cut_lands_where_the_objects_are_now_and_a_stepped_cutter_cuts_each_time")
def _():
    kit = fresh()   # Review 8 B8-1 and F8-1: matrices read before the depsgraph caught up; a spent cutter froze.
    wall = kit.box("Moved Wall", (-6, -0.25, 0), (6, 0.25, 3))
    cutter = kit.box("Moved Cutter", (-0.5, -1, 1), (0.5, 1, 2))
    cutter.location.x = 3.0
    kit.cut(wall, cutter)
    moved = (kit.check_ray("at 3", (3, -2, 1.5), (0, 1, 0), None)
             and not kit.check_ray("at 0", (0, -2, 1.5), (0, 1, 0), None))
    row = kit.box("Window Wall", (-6, 2.75, 0), (6, 3.25, 3))
    step = kit.box("Window Cutter", (-0.4, 2, 1), (0.4, 4, 2))
    for x in (-4.0, 0.0, 4.0):
        step.location.x = x
        kit.cut(row, step)
    stepped = all(kit.check_ray("window %g" % x, (x, 1, 1.5), (0, 1, 0), None) for x in (-4.0, 0.0, 4.0))
    kit.finish()
    return moved and stepped and "Window Cutter" not in bpy.data.objects


@case("a_cutter_the_script_takes_back_stays")
def _():
    kit = fresh()   # Review 8 F8-2: finish deleted a cutter the script reused as glass, or another object used.
    wall = kit.box("Glass Wall", (-3, -0.25, 0), (3, 0.25, 3))
    pane = kit.box("Pane Cutter", (-0.5, -1, 1), (0.5, 1, 2))
    kit.cut(wall, pane)
    pane.hide_render = False   # Taken back the way a script that knows hide_viewport would (review 9 N9-2).
    pane.name = "Window Glass"
    other, door = kit.box("Other Wall", (-3, 5, 0), (3, 5.5, 3)), kit.box("Door Cutter", (-0.5, 4, 0.2), (0.5, 10, 2))
    kit.cut(kit.box("Door Wall", (-3, 9, 0), (3, 9.5, 3)), door)
    other.modifiers.new("Their Door", "BOOLEAN").object = door
    kit.finish()
    excluded = bpy.data.collections.new("Cutters")
    bpy.context.scene.collection.children.link(excluded)
    stash = kit.box("Stashed Cutter", (-0.5, -1, 1), (0.5, 1, 2))
    bpy.context.scene.collection.objects.unlink(stash)
    excluded.objects.link(stash)
    bpy.context.view_layer.layer_collection.children["Cutters"].exclude = True
    outside = refused(lambda: kit.cut(wall, stash), "not evaluated")   # Review 9 N9-1: cut where it was.
    disabled = kit.box("Disabled Cutter", (-0.5, -1, 1), (0.5, 1, 2))
    disabled.hide_viewport = True   # Review 10 F10-1: in the layer, never evaluated.
    outside = outside and refused(lambda: kit.cut(wall, disabled), "not evaluated")
    return (outside and "Window Glass" in bpy.data.objects and bpy.data.objects["Window Glass"].visible_get()
            and "Door Cutter" in bpy.data.objects
            and sorted(kit.outcome().get("kit_kept_cutters", [])) == ["Door Cutter", "Window Glass"])


report = {"success": bool(checks) and all(checks.values()), "checks": checks}
(OUT / "results.json").write_text(json.dumps(report, indent=2))
print("ANGELO_SCRIPT_KIT", json.dumps(report))
