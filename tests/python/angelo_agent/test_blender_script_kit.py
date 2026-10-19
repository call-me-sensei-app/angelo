# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The reviewed-script kit (script_kit) in Blender: exact solids, safe cuts, rows and self-checks.

Each case is a recorded castle miss (doc/angelo/performance.rst, "Castle one-shot misses") or its guard:
a cutter wound inward (a mirrored cylinder, a profile listed the wrong way round) still cuts; an arched opening
through a wall mesh of four overlapping boxes keeps every wall (the EXACT solver without self-intersection
handling deleted three of them); a cutter that misses is refused; a merlon row stays inside its span; the
self-checks measure an arch's crown, bounds, a clear passage and floating parts. Background, local only; writes
results.json under ANGELO_QA_DIR. The second review's cases follow: targets the cut cannot prove are refused and left
alone; a cut reaches only the parts it touches; checks reach the host whole and stay small.
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

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-script-kit-qa"))
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


@case("arch_through_a_block_has_its_crown")
def _():
    kit = fresh()
    stone = bpy.data.materials.new("Stone")
    gate = kit.box("Castle Gatehouse", (-5, -18.5, 0), (5, -11.5, 13), stone)
    kit.arch(gate, 0.0, 4.0, 3.5, (-19.5, -10.5))
    kit.finish()   # The cutters go when the script ends.
    crown = kit.checks[-1]
    return (crown["passed"] and abs(crown["measured"] - 5.5) < 0.01 and [m.name for m in gate.data.materials] == ["Stone"]
            and "Castle Gatehouse arch cutter" not in bpy.data.objects)


@case("arch_through_overlapping_boxes_keeps_every_wall")
def _():
    kit = fresh()
    wall = overlapping_wall("Castle Wall")
    kit.arch(wall, 0.0, 4.0, 3.5, (-19.5, -10.5))
    lo, hi = kit.bounds(wall)
    return kit.checks[-1]["passed"] and all(abs(a - b) < 1e-3 for a, b in zip(lo + hi, (-21, -16, 0, 21, 16, 9)))


@case("a_cutter_wound_inward_still_cuts")
def _():
    kit = fresh()
    block = kit.box("Block", (10, 10, 0), (14, 14, 4))
    n = 24
    vertices = [(2 * math.cos(2 * math.pi * i / n), 2 * math.sin(2 * math.pi * i / n), z) for z in (-1, 5) for i in range(n)]
    faces = [(i, (i + 1) % n, n + (i + 1) % n, n + i) for i in range(n)] + [tuple(range(n - 1, -1, -1)),
                                                                          tuple(range(n, 2 * n))]
    mirrored = [(x + 12, z + 10, y + 2) for x, y, z in vertices]   # Two axes swapped: wound inward.
    mesh = bpy.data.meshes.new("cutter")
    mesh.from_pydata(mirrored, [], faces)
    cutter = bpy.data.objects.new("cutter", mesh)
    bpy.context.scene.collection.objects.link(cutter)
    before = len(block.data.polygons)
    kit.cut(block, cutter)
    return len(block.data.polygons) > before


@case("a_cutter_that_misses_is_refused")
def _():
    kit = fresh()
    target, cutter = kit.box("Far", (30, 30, 0), (31, 31, 1)), kit.box("Miss", (40, 40, 0), (41, 41, 1))
    try:
        kit.cut(target, cutter)
    except ValueError as error:
        return "does not overlap" in str(error)
    return False


@case("a_row_stays_inside_its_span")
def _():
    kit = fresh()   # 33 m of 0.5 m units 1 m apart: 22 units, centred in the span.
    row = kit.row("Row", -16.5, 16.5, "X", 15.7, 9.0, unit=(0.5, 0.5, 2.0), gap=1.0)
    lo, hi = kit.bounds(row)
    return len(row.data.polygons) == 22 * 6 and lo[0] >= -16.5 and hi[0] <= 16.5 and abs(lo[2] - 9.0) < 1e-4


@case("a_row_takes_its_unit_and_gap_from_the_script")
def _():
    kit = fresh()   # No default sizes: a script that leaves them out is told so, never given another request's sizes.
    try:
        kit.row("Row", 0.0, 10.0, "X", 0.0, 0.0)
    except TypeError as error:
        return "unit" in str(error) and "gap" in str(error) and not bpy.data.objects
    return False


@case("self_checks_measure_bounds_paths_and_floating_parts")
def _():
    kit = fresh()
    gate = kit.box("Castle Gatehouse", (-5, -18.5, 0), (5, -11.5, 13))
    kit.arch(gate, 0.0, 4.0, 3.5, (-19.5, -10.5))
    kit.box("Castle Float", (0, 0, 20), (1, 1, 21))
    good = kit.check_bounds("gatehouse", "Castle Gatehouse", (-5, -18.5, 0), (5, -11.5, 13))
    bad = kit.check_bounds("gatehouse moved", "Castle Gatehouse", (-5, -16, 0), None)
    clear = kit.check_ray("gateway clear", (0, -40, 1.5), (0, 1, 0), None, prefix="Castle Gatehouse")
    floating = kit.check_supported("nothing floats", "Castle")
    return good and not bad and clear and not floating and kit.checks[-1]["measured"] == 1


@case("names_points_and_vectors_are_accepted")
def _():
    # The recorded misuses that cost a whole rewrite each: a row's ends as points, a cut by object names.
    kit = fresh()
    bpy.data.materials.new("Stone")
    row = kit.row("Row E", (20.7, -12.0), (20.7, 12.0), "Y", (20.7, 0.0), 9.0, (0.5, 0.4, 1.0), 0.5, mat="Stone")
    lo, hi = kit.bounds(row)
    kit.box("Block", (0, 0, 0), (4, 4, 4), "Stone")
    kit.box("Hole", (1, 1, -1), (3, 3, 5))
    kit.cut("Block", "Hole")
    measured = kit.check("vector", Vector((1.0, 2.0, 3.0)), (1.0, 2.0, 3.0))
    gate = kit.box("Gate", (-5, -18.5, 0), (5, -11.5, 13))
    kit.arch("Gate", (0.0, -15.0), 4.0, 3.5, (-19.5, -10.5))   # A point for the centre.
    measured = measured and kit.checks[-1]["passed"] and len(gate.data.polygons) > 6
    kit.box("Castle Float", (0, 0, 20), (1, 1, 21))
    kit.check_supported("floats", "Castle Float")
    measured = measured and kit.checks[-1]["floating"][0]["object"] == "Castle Float"
    return (lo[1] >= -12.0 and hi[1] <= 12.0 and abs(lo[0] - 20.5) < 1e-4 and measured
            and [m.name for m in bpy.data.objects["Block"].data.materials] == ["Stone"])


@case("a_cut_keeps_the_target_modifiers_on_top_and_runs_them_once")
def _():
    kit = fresh()   # Review B1: the whole stack was baked in and kept, so a Bevel ran twice.
    wall = kit.box("Wall", (0, 0, 0), (4, 0.5, 3))
    bevel = wall.modifiers.new("Bevel", "BEVEL")
    bevel.width, bevel.segments = 0.05, 2
    kit.cut(wall, kit.box("Door", (1.5, -1, -1), (2.5, 2, 2)))
    return len(wall.modifiers) == 1 and len(wall.data.vertices) < 40 and kit.bounds(wall)[1][2] > 2.9


@case("a_missing_cutter_is_refused_even_on_overlapping_parts_and_changes_nothing")
def _():
    kit = fresh()   # Review F3 and F4: a self-union's loss read as "removed"; a refusal had already changed things.
    bm = bmesh.new()
    for lo, hi in (((0, 0, 0), (4, 1, 2)), ((3, 0, 0), (4, 4, 2))):
        for vertex in bmesh.ops.create_cube(bm, size=1)["verts"]:
            vertex.co = Vector(tuple((a + b) / 2 + v * (b - a) for v, a, b in zip(vertex.co, lo, hi)))
    mesh = bpy.data.meshes.new("Overlap")
    bm.to_mesh(mesh)
    bm.free()
    target = bpy.data.objects.new("Overlap", mesh)
    bpy.context.scene.collection.objects.link(target)
    vertices, data = len(mesh.vertices), target.data
    cutter = kit.box("Beside", (1, 2, 0.5), (2, 3, 1.5))
    try:
        kit.cut(target, cutter)
    except ValueError as error:
        return ("does not overlap" in str(error) and target.data == data and len(mesh.vertices) == vertices
                and "Beside" in bpy.data.objects)
    return False


@case("a_small_hole_in_a_small_part_is_a_cut")
def _():
    kit = fresh()   # Review F5: an absolute volume threshold refused a 3 mm hole in a 2 cm bracket.
    bracket = kit.box("Bracket", (0, 0, 0), (0.02, 0.02, 0.004))
    kit.cut(bracket, kit.cylinder("Bolt", (0.01, 0.01), 0.0015, -0.01, 0.01, segments=16))
    return len(bracket.data.polygons) > 6


@case("checks_are_json_safe_unique_and_never_vacuous")
def _():
    import numpy
    kit = fresh()   # Review F1, F6, N2.
    kit.box("Thing", (0, 0, 0), (1, 1, 1))
    a = kit.check("height", numpy.float32(1.0), 1.0)
    b = kit.check("height", float("nan"), 1.0)
    c = kit.check("no target", 1.0, None)
    d = kit.check_bounds("misspelt", "Thnig", (0, 0, 0), None)
    e = kit.check_ray("no direction", (0, 0, 5), (0, 0, 0), None)
    names = [entry["name"] for entry in kit.checks]
    json.dumps(kit.checks, allow_nan=False)
    return a and not (b or c or d or e) and names[:2] == ["height", "height (2)"]


@case("supported_sees_curves_and_array_copies")
def _():
    kit = fresh()   # Review F8: only base meshes were read, so a deck on a curve beam was "floating".
    curve = bpy.data.curves.new("Beam", "CURVE")
    curve.dimensions, curve.bevel_depth = "3D", 0.2
    spline = curve.splines.new("POLY")
    spline.points.add(1)
    spline.points[0].co, spline.points[1].co = (0, 0, 0.0, 1), (0, 0, 2.0, 1)
    beam = bpy.data.objects.new("Bridge Beam", curve)
    bpy.context.scene.collection.objects.link(beam)
    deck = kit.box("Bridge Deck", (-1, -1, 2.0), (1, 1, 2.2))
    array = deck.modifiers.new("Array", "ARRAY")
    array.count, array.relative_offset_displace = 2, (0, 0, 1)
    return kit.check_supported("bridge rests", "Bridge")


@case("a_row_refuses_a_span_shorter_than_a_unit")
def _():
    kit = fresh()   # Review N1.
    try:
        kit.row("Short", 0.0, 0.5, "x", 0.0, 0.0, unit=(1.0, 0.2, 1.0), gap=0.5)
    except ValueError as error:
        return "shorter than one unit" in str(error)
    return False


def car_body(name):
    """The recorded script's body: a box mesh around its origin, moved by its location (matrix_world not yet
    evaluated) in the same script that parents the parts."""
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(x, y, z) for z in (-0.35, 0.35) for y in (-0.9, 0.9) for x in (-2, 2)], [],
                     [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)])
    body = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(body)
    body.location = (-10.5, -1.0, 1.35)
    return body


def world_box(obj):
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    return [round(min(c[a] for c in corners), 4) for a in range(3)] + [round(max(c[a] for c in corners), 4)
                                                                        for a in range(3)]


@case("parent_keeps_world_places_where_a_stale_matrix_moved_them")
def _():
    kit = fresh()   # The bridge's car (doc/angelo/performance.rst, "Colours a request states").
    body = car_body("Car Body")
    cabin = kit.box("Car Cabin", (-11.6, -1.8, 1.7), (-9.6, -0.2, 2.4))
    wheel = kit.box("Car Wheel FL", (-9.55, -1.95, 1.0), (-8.85, -1.65, 1.7))
    before = [world_box(cabin), world_box(wheel)]
    returned = kit.parent([cabin, wheel], body)
    bpy.context.view_layer.update()
    kept = [world_box(cabin), world_box(wheel)] == before and returned == [cabin, wheel]
    body.location.x = 10.5   # Driving the body moves the parts with it.
    bpy.context.view_layer.update()
    moved = abs(world_box(cabin)[0] - (before[0][0] + 21.0)) < 1e-4
    stale_body = car_body("Old Body")   # The recorded pattern, for contrast: the part lands 10.5 m off.
    old = kit.box("Old Cabin", (-11.6, -1.8, 1.7), (-9.6, -0.2, 2.4))
    old.parent = stale_body
    old.matrix_parent_inverse = stale_body.matrix_world.inverted()
    bpy.context.view_layer.update()
    misplaced = abs(world_box(old)[0] - (-11.6 - 10.5)) < 1e-4
    single = kit.parent("Car Cabin", "Old Body") == cabin
    try:
        kit.parent(body, body)
        loop = False
    except ValueError as error:
        loop = "its own child or itself" in str(error)
    return kept and moved and misplaced and single and loop


@case("parent_refuses_a_zero_scale_parent_before_any_change_and_unparents_in_place")
def _():
    kit = fresh()   # Parent review P-N1 and P-N3.
    flat = car_body("Flat Body")
    flat.scale = (0.0, 1.0, 1.0)
    first = kit.box("First Part", (-11.0, -1.0, 1.0), (-10.0, 0.0, 2.0))
    second = kit.box("Second Part", (-9.0, -1.0, 1.0), (-8.0, 0.0, 2.0))
    before = [world_box(first), world_box(second)]
    try:
        kit.parent([first, second], flat)
        refused = False
    except ValueError as error:
        refused = "no inverse" in str(error)
    untouched = first.parent is None and second.parent is None and [world_box(first), world_box(second)] == before
    body = car_body("Car Body")
    cabin = kit.box("Car Cabin", (-11.6, -1.8, 1.7), (-9.6, -0.2, 2.4))
    kit.parent((part for part in [cabin]), body)   # A generator of children works too.
    body.location.x = 0.0
    bpy.context.view_layer.update()
    moved = world_box(cabin)
    returned = kit.parent(cabin, None)   # Unparent in place: stays where it now is.
    bpy.context.view_layer.update()
    body.location.x = 10.0
    bpy.context.view_layer.update()
    return (refused and untouched and returned == cabin and cabin.parent is None and world_box(cabin) == moved
            and abs(moved[0] - (-11.6 + 10.5)) < 1e-4)


report = {"success": bool(checks) and all(checks.values()), "checks": checks}
(OUT / "results.json").write_text(json.dumps(report, indent=2))
print("ANGELO_SCRIPT_KIT", json.dumps(report))
