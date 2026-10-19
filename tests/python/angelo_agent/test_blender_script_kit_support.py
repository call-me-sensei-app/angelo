# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The kit's support check on any surface (script_kit.check_supported, script_kit_support) in Blender.

A part counts as supported when a chain of contacts joins it to the ground, through the group's other parts or any
other visible geometry: a check of only the wheels of a car parked on a deck 1 m up read four floating wheels in a
recorded bridge build, and the model spent every correction round moving them (doc/angelo/performance.rst, "Support
through the scene"). The cases are unrelated scenes, none sized from a brief: a car on a deck on piers, a cup on a
table, a shelf on a wall with a stack of books, a tabletop on another group's legs, a box across a log, a frame on a
wall; parts that really float still fail and name what is below them and how low their contacts reach (in the air,
inside a room, over a terrain, 3 cm over a deck, a car in the air checked by its wheels, a car on a deck that floats,
over a low-poly ramp whose bounds hold it); parts on the ground pass as before without reading the scene; instances
support like objects; hidden, unrendered and frame-drawn objects never do; the limits leave a check unmeasured, never
passed. Background, local only; writes results.json under ANGELO_QA_DIR.
"""

import json
import math
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy
import bmesh
from mathutils import Matrix

try:
    from angelo_agent.script_kit import ScriptKit
    from angelo_agent import script_kit_support
except ImportError:   # A stock Blender run from the checkout.
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
    from angelo_agent.script_kit import ScriptKit
    from angelo_agent import script_kit_support

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-script-kit-support-qa"))
OUT.mkdir(parents=True, exist_ok=True)
checks, timings = {}, {}


def fresh():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for collection in list(bpy.data.collections):
        bpy.data.collections.remove(collection)
    bpy.context.scene.frame_set(1)
    return ScriptKit(bpy, bpy.context.scene)


def case(name):
    def wrap(function):
        try:
            checks[name] = bool(function())
        except Exception:
            checks[name] = False
            print(traceback.format_exc())
        return function
    return wrap


def last(kit):
    return kit.checks[-1]


def floating(kit):
    return {row["object"]: row for row in last(kit).get("floating", ())}


def mesh_object(name, build, collection=None):
    bm = bmesh.new()
    build(bm)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    (collection or bpy.context.scene.collection).objects.link(obj)
    return obj


def wheel(bm, x, y, radius, width):
    """A wheel lying on its side (axle along Y), centred on (x, y, 0) of its body."""
    bmesh.ops.create_cone(bm, cap_ends=True, segments=20, radius1=radius, radius2=radius, depth=width,
                          matrix=Matrix.Translation((x, y, 0)) @ Matrix.Rotation(math.pi / 2, 4, "X"))


def car(kit, lift=0.0):
    """A body parented over four wheels whose lowest points are 1.0 m up plus ``lift``, driven along X by keys."""
    body = kit.box("Car Body", (-2, -0.9, -0.1), (2, 0.9, 0.6))
    kit.parent(kit.box("Car Cabin", (-1.2, -0.8, 0.6), (0.9, 0.8, 1.35)), body)
    for index, (x, y) in enumerate(((-1.3, -0.78), (-1.3, 0.78), (1.3, -0.78), (1.3, 0.78))):
        part = mesh_object("Car Wheel %d" % (index + 1), lambda bm, x=x, y=y: wheel(bm, x, y, 0.35, 0.24))
        part.parent = body
    for frame, x in ((1, -10.5), (120, 10.5)):
        body.location = (x, -1.0, 1.35 + lift)
        body.keyframe_insert("location", frame=frame)
    bpy.context.scene.frame_set(60)
    return body


def deck(kit, piers=True):
    """A deck 1 m up, on two piers standing on the ground unless ``piers`` is False."""
    kit.box("Deck", (-12, -2.5, 0.4), (12, 2.5, 1.0))
    if piers:
        for x in (-10, 10):
            kit.box("Pier %+d" % x, (x - 0.5, -2, 0.0), (x + 0.5, 2, 0.4))


@case("a_car_rests_on_a_deck_on_piers")
def _():
    kit = fresh()
    deck(kit)
    car(kit)
    wheels, whole = kit.check_supported("wheels", "Car Wheel"), kit.check_supported("car", "Car")
    kit = fresh()
    deck(kit)
    car(kit, lift=0.05)
    lifted = kit.check_supported("wheels", "Car Wheel")
    rows = floating(kit)
    return (wheels and whole and lifted is False and last(kit)["measured"] == 4
            and all(row["below"] == "Deck" and abs(row["gap"] - 0.05) < 1e-3
                    and abs(row["reaches_down_to"] - 1.05) < 1e-3 for row in rows.values())
            and "ground=0.0" in last(kit)["support"] and "tolerance=0.02" in last(kit)["support"])


@case("a_car_in_the_air_checked_by_its_wheels_floats_and_so_does_one_on_a_floating_deck")
def _():
    kit = fresh()   # The wheels touch the body: one contact deep, the car in the air would pass.
    car(kit, lift=4.0)
    air = kit.check_supported("wheels", "Car Wheel")
    air_row = floating(kit)["Car Wheel 1"]
    kit = fresh()
    deck(kit, piers=False)
    car(kit)
    floating_deck = kit.check_supported("wheels", "Car Wheel")
    deck_row = floating(kit)["Car Wheel 1"]
    return (air is False and air_row["below"] == "ground z=0.0" and abs(air_row["reaches_down_to"] - 5.0) < 1e-3
            and floating_deck is False and deck_row["below"] == "Deck" and abs(deck_row["gap"]) < 1e-3
            and abs(deck_row["reaches_down_to"] - 0.4) < 1e-3)


@case("a_deck_floating_over_a_low_poly_ramp_inside_its_bounds_holds_nothing_up")
def _():
    kit = fresh()   # The ramp's two large slanted triangles have bounds that hold the whole deck.
    ramp = mesh_object("Ramp", lambda bm: bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=1))
    for vertex in ramp.data.vertices:
        x = vertex.co.x * 20.0
        vertex.co = (x, vertex.co.y * 10.0, 0.5 * (x - 12.0) - 0.5)   # Under the deck at least 0.9 m down.
    deck(kit, piers=False)
    car(kit)
    passed = kit.check_supported("wheels", "Car Wheel")
    row = floating(kit)["Car Wheel 1"]
    return passed is False and abs(row["reaches_down_to"] - 0.4) < 1e-3


@case("a_cup_rests_on_a_table_and_one_above_it_floats")
def _():
    kit = fresh()
    kit.box("Table Top", (-0.6, -0.4, 0.72), (0.6, 0.4, 0.75))
    for index, (x, y) in enumerate(((-0.55, -0.35), (0.55, -0.35), (-0.55, 0.35), (0.55, 0.35))):
        kit.box("Table Leg %d" % index, (x - 0.03, y - 0.03, 0.0), (x + 0.03, y + 0.03, 0.72))
    kit.cylinder("Cup", (0.1, 0.0), 0.04, 0.75, 0.85, segments=24)
    kit.cylinder("Mug", (-0.2, 0.1), 0.05, 0.80, 0.90, segments=24)
    table, cup, mug = (kit.check_supported("table", "Table"), kit.check_supported("cup", "Cup"),
                       kit.check_supported("mug", "Mug"))
    row = floating(kit)["Mug"]
    return table and cup and mug is False and row["below"] == "Table Top" and abs(row["gap"] - 0.05) < 1e-3


@case("a_shelf_on_a_wall_holds_a_stack_of_books_and_names_the_one_left_above_it")
def _():
    kit = fresh()
    kit.box("Wall", (-2, 0, 0), (2, 0.2, 2.5))
    kit.box("Shelf Board", (-0.5, -0.3, 1.48), (0.5, 0.0, 1.5))   # Touches the wall's face, nothing below it.
    for index in range(3):
        kit.box("Book %d" % (index + 1), (-0.1, -0.25, 1.5 + 0.04 * index), (0.1, -0.05, 1.54 + 0.04 * index))
    shelf, books = kit.check_supported("shelf", "Shelf"), kit.check_supported("books", "Book")
    kit.box("Book 4", (-0.1, -0.25, 1.82), (0.1, -0.05, 1.86))   # 0.2 m over the stack.
    loose = kit.check_supported("books again", "Book")
    row = floating(kit).get("Book 4") or {}
    return (shelf and books and loose is False and last(kit)["measured"] == 1 and row.get("below") == "Book 3"
            and abs(row.get("gap", 0) - 0.2) < 1e-3)


@case("a_tabletop_on_another_groups_legs_a_box_across_a_log_and_a_frame_on_a_wall")
def _():
    kit = fresh()   # Contacts with no vertex of one part on the other: faces over vertices, and an edge across a face.
    for index, (x, y) in enumerate(((-0.9, -0.4), (0.9, -0.4), (-0.9, 0.4), (0.9, 0.4))):
        kit.box("Legs %d" % index, (x - 0.04, y - 0.04, 0.0), (x + 0.04, y + 0.04, 0.7))
    kit.box("Slab", (-1.0, -0.5, 0.7), (1.0, 0.5, 0.74))
    log = kit.cylinder("Log", (0.0, 0.0), 0.3, -2.0, 2.0, segments=32)
    log.rotation_euler = (0.0, math.pi / 2, 0.0)   # Lying on the ground along X, its top line at z = 0.6.
    log.location = (5.0, 0.0, 0.3)
    bpy.context.view_layer.update()
    kit.box("Crate", (4.6, -0.5, 0.6), (5.4, 0.5, 1.1))   # Wider than the log: its corners hang in the air.
    kit.box("Picture Wall", (10, 0, 0), (14, 0.2, 3))
    kit.box("Frame", (11.5, -0.03, 1.6), (12.5, 0.0, 2.3))
    return all((kit.check_supported("slab", "Slab"), kit.check_supported("crate", "Crate"),
                kit.check_supported("frame", "Frame")))


@case("a_part_in_the_air_floats_over_the_ground")
def _():
    kit = fresh()
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5, location=(0, 0, 5))
    bpy.context.active_object.name = "Balloon"
    passed = kit.check_supported("balloon", "Balloon")
    row = floating(kit)["Balloon"]
    return passed is False and row["below"] == "ground z=0.0" and abs(row["gap"] - 4.5) < 1e-3


@case("a_room_or_a_terrain_around_a_part_supports_it_only_where_its_surface_reaches")
def _():
    kit = fresh()   # Their bounds would hold everything inside them.
    kit.box("Room", (-5, -5, 0.3), (5, 5, 3.0))   # Floor 0.3 m up, walls and a ceiling, on a plinth.
    kit.box("Plinth", (-5, -5, 0.0), (5, 5, 0.3))
    kit.cylinder("Lamp", (0, 0), 0.2, 1.5, 2.0)
    kit.cylinder("Stool", (2, 2), 0.2, 0.3, 0.75)
    lamp = kit.check_supported("lamp", "Lamp")
    lamp_row = floating(kit)["Lamp"]
    stool = kit.check_supported("stool", "Stool")

    def hills(bm):   # Below the ground plane in its valleys.
        bmesh.ops.create_grid(bm, x_segments=60, y_segments=60, size=30)
        for vertex in bm.verts:
            vertex.co.z = 0.5 + math.sin(vertex.co.x * 0.3) * math.cos(vertex.co.y * 0.2)
    mesh_object("Terrain", hills)
    bpy.context.view_layer.update()
    height = 0.5 + math.sin(12 * 0.3) * math.cos(9 * 0.2)   # A vertex of the terrain.
    kit.box("Rock Sunk", (11.8, 8.8, height - 0.15), (12.2, 9.2, height + 0.3))
    low = 0.5 + math.sin(12 * 0.3) * math.cos(7 * 0.2)
    kit.box("Rock Above", (11.8, 6.8, low + 0.5), (12.2, 7.2, low + 0.9))   # Inside the terrain's bounds.
    sunk, above = kit.check_supported("sunk", "Rock Sunk"), kit.check_supported("above", "Rock Above")
    above_row = floating(kit)["Rock Above"]
    return (lamp is False and stool and lamp_row["below"] == "Room" and abs(lamp_row["gap"] - 1.2) < 1e-3
            and sunk and above is False and above_row["below"] == "Terrain" and 0.3 < above_row["gap"] < 0.7
            and low + 0.9 < 1.5)


@case("parts_on_the_ground_pass_as_before_without_reading_the_scene")
def _():
    kit = fresh()
    kit.box("Plinth", (0, 0, 0), (1, 1, 1))
    kit.box("Plinth Statue", (0.2, 0.2, 1.0), (0.8, 0.8, 2.5))   # On the plinth: the chain, as before.
    kit.box("Post", (3, 0, -0.5), (3.2, 0.2, 2))                   # Sunk below the ground plane.
    search = script_kit_support.scene_support

    def never(*_args, **_kwargs):
        raise AssertionError("the scene was searched")
    script_kit_support.scene_support = never
    try:
        plinth, post = kit.check_supported("plinth", "Plinth"), kit.check_supported("post", "Post")
    finally:
        script_kit_support.scene_support = search
    return plinth and post and last(kit) == {"name": "post", "measured": 0, "target": 0, "tolerance": 0,
                                             "passed": True}


def scatter_tree(name, points_size):
    group = bpy.data.node_groups.new(name, "GeometryNodeTree")
    group.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    group.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    entry, exit_ = group.nodes.new("NodeGroupInput"), group.nodes.new("NodeGroupOutput")
    instance, cube = group.nodes.new("GeometryNodeInstanceOnPoints"), group.nodes.new("GeometryNodeMeshCube")
    cube.inputs["Size"].default_value = points_size
    transform = group.nodes.new("GeometryNodeTransform")   # Each post stands on its point.
    transform.inputs["Translation"].default_value = (0, 0, points_size[2] / 2)
    group.links.new(cube.outputs[0], transform.inputs[0])
    group.links.new(entry.outputs[0], instance.inputs["Points"])
    group.links.new(transform.outputs[0], instance.inputs["Instance"])
    group.links.new(instance.outputs[0], exit_.inputs[0])
    return group


def post_points(name, xs, z):
    def points(bm):
        for x in xs:
            bm.verts.new((x, 0.0, z))
    obj = mesh_object(name, points)
    obj.modifiers.new("Posts", "NODES").node_group = scatter_tree(name, (0.1, 0.1, 1.0))
    return obj


@case("instances_support_like_objects_but_never_the_group_that_makes_them")
def _():
    kit = fresh()
    asset = bpy.data.collections.new("Table Asset")   # A collection instance, as an asset drop makes one.
    for part in [kit.box("Asset Top", (-0.5, -0.5, 0.7), (0.5, 0.5, 0.75))] + [
            kit.box("Asset Leg %d" % i, (x - 0.04, y - 0.04, 0.0), (x + 0.04, y + 0.04, 0.7))
            for i, (x, y) in enumerate(((-0.45, -0.45), (0.45, -0.45), (-0.45, 0.45), (0.45, 0.45)))]:
        bpy.context.scene.collection.objects.unlink(part)   # The top reaches the ground through the legs.
        asset.objects.link(part)
    holder = bpy.data.objects.new("Placed Table", None)
    holder.instance_type, holder.instance_collection = "COLLECTION", asset
    bpy.context.scene.collection.objects.link(holder)
    holder.location = (4, 0, 0)
    kit.cylinder("Vase", (4, 0), 0.1, 0.75, 1.1)
    post_points("Fence Posts", (0, 1, 2, 3), 0.0)   # Geometry Nodes posts, 1 m tall, on the ground.
    kit.box("Fence Rail", (-0.2, -0.05, 1.0), (3.2, 0.05, 1.1))
    post_points("Pier Posts", (10, 11), 0.0)
    kit.box("Pier Board", (9.8, -0.3, 1.0), (11.2, 0.3, 1.1))   # On its own group's instances: not read either way.
    bpy.context.view_layer.update()
    vase, rail, pier = (kit.check_supported("vase", "Vase"), kit.check_supported("rail", "Fence Rail"),
                        kit.check_supported("pier", "Pier"))
    return vase and rail and pier is False


@case("hidden_unrendered_and_frame_drawn_objects_never_support")
def _():
    kit = fresh()
    kit.box("Hidden Block", (0, 0, 0), (1, 1, 1)).hide_set(True)
    kit.box("Unrendered Block", (3, 0, 0), (4, 1, 1)).hide_render = True
    kit.box("Wire Block", (6, 0, 0), (7, 1, 1)).display_type = "WIRE"
    kit.box("Cut Block", (9, 0, 0), (10, 1, 1))
    kit.cut("Cut Block", kit.box("Cut Cutter", (9.4, -1, 0.9), (9.6, 2, 1.2)))   # The cutter is hidden after it.
    kit.box("Solid Block", (12, 0, 0), (13, 1, 1))
    for index, (x0, x1, z) in enumerate(((0.1, 0.4, 1.0), (3.1, 3.4, 1.0), (6.1, 6.4, 1.0), (9.42, 9.58, 1.2),
                                          (12.1, 12.4, 1.0))):
        kit.box("Load %d" % index, (x0, 0.1, z), (x1, 0.4, z + 0.3))   # Load 3 on the spent cutter's top.
    results, below = [], []
    for index in range(5):
        results.append(kit.check_supported("load %d" % index, "Load %d" % index))
        below.append(floating(kit).get("Load %d" % index, {}).get("below"))
    return (results == [False, False, False, False, True]
            and below == ["ground z=0.0", "ground z=0.0", "ground z=0.0", "Cut Block", None])


@case("the_tolerance_is_the_line_and_its_value_is_stated")
def _():
    kit = fresh()
    kit.box("Base", (0, 0, 0), (2, 2, 1))
    kit.box("Near", (0.2, 0.2, 1.015), (0.6, 0.6, 1.3))
    kit.box("Off", (1.2, 1.2, 1.03), (1.6, 1.6, 1.3))
    near, off = kit.check_supported("near", "Near"), kit.check_supported("off", "Off")
    gap = floating(kit)["Off"]["gap"]
    wide = kit.check_supported("off wide", "Off", tolerance=0.05)
    raised = kit.check_supported("raised ground", "Off", ground=1.02)   # A ground plane stated just under it.
    return near and off is False and abs(gap - 0.03) < 1e-4 and wide and raised


@case("limits_leave_the_check_unmeasured_and_bad_values_fail_without_raising")
def _():
    kit = fresh()
    kit.box("Deck", (-5, -5, 0.5), (5, 5, 1))
    kit.box("Crate", (0, 0, 1), (1, 1, 2))
    kit.box("Kite", (0, 0, 4), (1, 1, 4.2))   # Floating: the search looks at every object.
    few = kit.check_supported("few objects", "Kite", max_objects=1)
    few_entry = last(kit)
    tight = kit.check_supported("few vertices", "Crate", max_vertices=10)   # The crate's 8 fit, the deck's do not.
    tight_entry = last(kit)
    bad = kit.check_supported("bad ground", "Crate", ground="floor")
    bad_entry = last(kit)
    negative = kit.check_supported("negative", "Crate", tolerance=-1)
    return (few is None and few_entry["passed"] is None and "not measured" in few_entry["reason"]
            and tight is None and "vertex limit" in tight_entry["reason"]
            and bad is False and "finite" in bad_entry["reason"] and negative is False)


@case("support_is_searched_fast_in_a_scattered_scene")
def _():
    kit = fresh()

    def ground_grid(bm):
        bmesh.ops.create_grid(bm, x_segments=300, y_segments=300, size=50)
    grass = mesh_object("Grass", ground_grid)   # 90,601 Geometry Nodes instances.
    grass.modifiers.new("Blades", "NODES").node_group = scatter_tree("Blades", (0.02, 0.02, 0.2))

    def lift(x, y):   # Rolling ground about the ground plane.
        return 0.3 * math.sin(x * 0.2) * math.cos(y * 0.15)

    def terrain(bm):
        bmesh.ops.create_grid(bm, x_segments=350, y_segments=350, size=50)
        for vertex in bm.verts:
            vertex.co.z = lift(vertex.co.x, vertex.co.y)
    mesh_object("Field", terrain)   # 123,201 vertices and 245,000 triangles.
    deck(kit)
    car(kit)
    start = time.perf_counter()
    parked = kit.check_supported("parked", "Car")
    timings["car_on_deck_s"] = round(time.perf_counter() - start, 3)
    def stones(bm):   # 2,000 stones 0.1 m over the field, among its triangles: every one is compared.
        for x in range(-25, 25):
            for y in range(-45, -5):   # Clear of the deck.
                for vertex in bmesh.ops.create_cube(bm, size=0.2)["verts"]:
                    vertex.co.x += x * 0.9
                    vertex.co.y += y * 0.9
                    vertex.co.z += max(lift(x * 0.9, y * 0.9), 0.0) + 0.4   # Over the field and the ground.
    mesh_object("Stones", stones)
    start = time.perf_counter()
    lifted = kit.check_supported("stones", "Stones")
    timings["2000_floating_stones_s"] = round(time.perf_counter() - start, 3)
    measured = last(kit)["measured"]
    print("ANGELO_SCRIPT_KIT_SUPPORT_TIMINGS", json.dumps(timings))
    return (parked and lifted is False and measured == 2000 and timings["car_on_deck_s"] < 1.5
            and timings["2000_floating_stones_s"] < 5.0)


report = {"success": bool(checks) and all(checks.values()), "checks": checks, "timings": timings}
(OUT / "results.json").write_text(json.dumps(report, indent=2))
print("ANGELO_SCRIPT_KIT_SUPPORT", json.dumps(report))
