# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The motion read's guards and edge cases, in the compiled app (motion_facts, motion_frames, motion_scope).

Run by ``run_native_suites.py --include test_blender_motion_guards`` (BACKGROUND). Every case is one that failed
on the first version, rebuilt here from general setups (none is a bridge
scene): negative frames, a Geometry Nodes simulation zone, static hair and an Ocean beside a moving rig, a mesh on
a keyed bone and a skin its parent rig deforms, a keyed car on a static road curve, an object the view layer does
not evaluate, an instancer whose base mesh never renders, a budget that fits one frame, Python handlers a frame
change would run, the scene revision an approved script is checked against, static edits, a script that only keys
a shape key, a millimetre model, and a heavy multi-threaded scene against the wall-time budget. Values are checked
against ``scene.frame_set`` as Blender evaluates it.

No provider, account or network.
"""

import json
import os
from pathlib import Path
import sys
import time
import traceback

import bpy

assert bpy.app.background and "--factory-startup" in sys.argv
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ["ANGELO_QA_DIR"])
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import blender_tools, motion_facts, motion_frames, native_programs as native  # noqa: E402
from angelo_agent import object_bounds, scene_queries as queries  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402

report = {"checks": {}, "observed": {}, "binary": bpy.app.binary_path, "success": False}
W = bpy.context.window
S = W.scene


def check(name, condition, **observed):
    report["checks"][name] = bool(condition)
    if observed:
        report["observed"][name] = observed


def clear():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    motion_frames.ESTIMATES.clear()
    S.frame_start, S.frame_end = 1, 120
    S.frame_set(30)


def cube(name, size=1.0, location=(0, 0, 0), collection=None):
    mesh = bpy.data.meshes.new(name)
    h = size / 2
    mesh.from_pydata([(x, y, z) for x in (-h, h) for y in (-h, h) for z in (-h, h)], [],
                     [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)])
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    (collection or S.collection).objects.link(obj)
    return obj


def keyed(owner, path, values):
    for frame, value in values:
        setattr(owner, path, value)
        owner.keyframe_insert(path, frame=frame)


def rig(name="QA Rig", start=(-10, 0, 0), end=(10, 0, 0)):
    obj = bpy.data.objects.new(name, None)
    S.collection.objects.link(obj)
    keyed(obj, "location", ((1, start), (120, end)))
    return obj


def geometry_nodes(name, build):
    tree = bpy.data.node_groups.new(name, "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nodes = tree.nodes
    build(tree, nodes.new("NodeGroupInput"), nodes.new("NodeGroupOutput"))
    return tree


def truth(objects, frames, top=None):
    """Blender's own evaluation (``scene.frame_set``): the top's origin and the objects' union box per frame."""
    original = S.frame_current
    found = []
    for frame in frames:
        S.frame_set(frame)
        boxes = [box for box in (object_bounds.world_bounds(obj) for obj in objects) if box]
        found.append({"origin": [round(v, 3) + 0.0 for v in (top or objects[0]).matrix_world.translation],
                      "bounds": object_bounds.rounded([[min(b[0][i] for b in boxes) for i in range(3)],
                                                       [max(b[1][i] for b in boxes) for i in range(3)]])})
    S.frame_set(original)
    return found


def group_named(read, name):
    return next((group for group in (read or {}).get("moving", ()) if group["name"] == name), None)


def frames_and_simulation():
    clear()
    box = cube("QA Swing")
    keyed(box, "location", ((-40, (-40, 0, 0)), (40, (40, 0, 0))))
    S.frame_set(-5)
    at = motion_facts.at_frames(W, box, [-20, 10])
    expected = truth([box], [-20, 10])
    check("negative_frames_are_read_and_put_back", at.get("origin") == [row["origin"] for row in expected]
          and S.frame_current == -5, at=at, expected=expected, frame_after=S.frame_current)
    clear()
    moving = rig()

    def simulate(tree, group_in, group_out):
        nodes, links = tree.nodes, tree.links
        sim_in, sim_out = nodes.new("GeometryNodeSimulationInput"), nodes.new("GeometryNodeSimulationOutput")
        sim_in.pair_with_output(sim_out)
        rise = nodes.new("GeometryNodeSetPosition")
        rise.inputs["Offset"].default_value = (0, 0, 0.1)
        links.new(group_in.outputs[0], sim_in.inputs[0])
        links.new(sim_in.outputs[1], rise.inputs[0])
        links.new(rise.outputs[0], sim_out.inputs[1])
        links.new(sim_out.outputs[0], group_out.inputs[0])
    cube("QA Simulated", 1.0, (0, 20, 0)).modifiers.new("Sim", "NODES").node_group = geometry_nodes("QA Sim", simulate)
    for frame in range(1, 31):
        S.frame_set(frame)
    read = motion_facts.after_edit(W, objects=[moving]) or {}
    check("a_simulation_zone_is_not_measured", "simulation" in (read.get("not_measured") or "")
          and S.frame_current == 30, read=read)
    clear()
    moving = rig()
    grass = cube("QA Grass", 4.0, (30, 30, 0))
    grass.modifiers.new("Hair", "PARTICLE_SYSTEM")
    grass.particle_systems[0].settings.type = "HAIR"
    cube("QA Sea", 4.0, (-30, 30, 0)).modifiers.new("Ocean", "OCEAN")
    read = motion_facts.after_edit(W, objects=[moving]) or {}
    static = bool(group_named(read, "QA Rig"))
    grass.particle_systems[0].settings.type = "EMITTER"
    emitted = motion_facts.after_edit(W, objects=[moving]) or {}
    check("static_hair_and_an_ocean_are_measured_and_emitters_are_not", static
          and "particles" in (emitted.get("not_measured") or ""), read=read, emitter=emitted)


def rigs_and_scope():
    clear()
    arm = bpy.data.objects.new("QA Arm", bpy.data.armatures.new("QA Arm"))
    S.collection.objects.link(arm)
    W.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    bone = arm.data.edit_bones.new("Lift")
    bone.head, bone.tail = (0, 0, 0), (0, 0, 2)
    bpy.ops.object.mode_set(mode="OBJECT")
    pose = arm.pose.bones["Lift"]
    for frame, y in ((1, 0.0), (120, 4.0)):
        pose.location = (0, y, 0)
        pose.keyframe_insert("location", frame=frame)
    held = cube("QA Held", 0.5, (0, 0, 1))
    held.parent, held.parent_type, held.parent_bone = arm, "BONE", "Lift"
    skin = cube("QA Skin", 1.0, (3, 0, 1))
    skin.parent = arm
    skin.vertex_groups.new(name="Lift").add(list(range(8)), 1.0, "REPLACE")
    skin.modifiers.new("Rig", "ARMATURE").object = arm
    read = motion_facts.after_edit(W, objects=[held, skin]) or {}
    group = group_named(read, "QA Arm") or {}
    expected = truth([held, skin], read.get("frames") or [1], top=arm)
    check("bone_parented_and_deformed_parts_are_not_apart", "apart" not in read and group
          and group["bounds"] == [row["bounds"] for row in expected], read=read, expected=expected)
    clear()
    road = bpy.data.objects.new("QA Road", bpy.data.curves.new("QA Road", "CURVE"))
    spline = road.data.splines.new("POLY")
    spline.points.add(1)
    spline.points[1].co = (200, 0, 0, 1)
    S.collection.objects.link(road)
    car = rig("QA Car")
    car.parent = road
    body = cube("QA Body", 2.0)
    body.parent = car
    read = motion_facts.after_edit(W, objects=[body]) or {}
    check("a_static_road_curve_is_not_the_top", [g["name"] for g in read.get("moving", ())] == ["QA Car"]
          and read["moving"][0]["bounds"][0][1][0] - read["moving"][0]["bounds"][0][0][0] < 3
          and "apart" not in read, read=read)  # The top's own keys against its still parent are no news.
    clear()
    hidden = bpy.data.collections.new("QA Hidden")
    S.collection.children.link(hidden)
    away = cube("QA Away", 1.0, (0, 0, 0), collection=hidden)
    keyed(away, "location", ((1, (0, 0, 0)), (120, (30, 0, 0))))
    W.view_layer.layer_collection.children["QA Hidden"].exclude = True
    at = motion_facts.at_frames(W, away, [1, 60, 120])
    check("an_object_the_view_layer_does_not_evaluate_is_not_measured",
          "not evaluated" in (at.get("not_measured") or "") and "origin" not in at, at=at)
    clear()
    holder = rig("QA Holder", (0, 0, 0), (0, 0, 10))

    def far(tree, _group_in, group_out):
        nodes, links = tree.nodes, tree.links
        point, unit = nodes.new("GeometryNodePoints"), nodes.new("GeometryNodeMeshCube")
        place, move = nodes.new("GeometryNodeInstanceOnPoints"), nodes.new("GeometryNodeTransform")
        move.inputs["Translation"].default_value = (0, 2.5, 0)
        links.new(point.outputs[0], place.inputs["Points"])
        links.new(unit.outputs[0], place.inputs["Instance"])
        links.new(place.outputs[0], move.inputs["Geometry"])
        links.new(move.outputs[0], group_out.inputs[0])
    base = cube("QA Base", 0.1)
    base.parent = holder
    base.modifiers.new("Far", "NODES").node_group = geometry_nodes("QA Far", far)
    read = motion_facts.after_edit(W, objects=[base]) or {}
    first = (group_named(read, "QA Holder") or {}).get("bounds", [None])[0]
    check("an_instancer_box_leaves_out_base_geometry_that_does_not_render",
          first == [[-0.5, 2.0, -0.5], [0.5, 3.0, 0.5]], first=first, read=read)
    clear()
    tiny = rig("QA Tiny", (0, 0, 0), (0.0005, 0, 0))
    cube("QA Tiny Part", 0.004).parent = tiny
    read = motion_facts.after_edit(W, objects=[tiny]) or {}
    group = group_named(read, "QA Tiny") or {}
    check("a_millimetre_model_moves", group.get("origin", [[0]])[-1][0] == 0.0005, read=read)


def budgets_and_policies():
    clear()
    moving = rig()
    ticks = iter(range(10 ** 6))
    read = motion_facts.after_edit(W, objects=[moving], clock=lambda: next(ticks) * 0.2) or {}
    motion_frames.ESTIMATES.clear()
    check("one_frame_in_the_budget_is_stated_not_measured", read.get("frames") == [1] and "not_measured" in read
          and S.frame_current == 30, read=read)
    clear()
    moving = rig()
    calls = []

    def watching(scene, depsgraph=None):
        calls.append(scene.frame_current)
    bpy.app.handlers.depsgraph_update_post.append(watching)
    try:
        handle = queries.object_handle(moving)
        asked = motion_facts.motion_at_frames(W, handle, [1, 120], mode="ASK")
        auto = motion_facts.motion_at_frames(W, handle, [1, 120], mode="AUTO_BOUNDED")
    finally:
        bpy.app.handlers.depsgraph_update_post.remove(watching)
    bpy.app.handlers.frame_change_post.append(watching)
    try:
        edited = motion_facts.after_edit(W, objects=[moving]) or {}
    finally:
        bpy.app.handlers.frame_change_post.remove(watching)
    check("add_on_python_is_run_only_where_the_mode_allows", "Python" in (asked.get("not_measured") or "")
          and auto.get("origin") == [[-10.0, 0.0, 0.0], [10.0, 0.0, 0.0]]
          and "Python" in (edited.get("not_measured") or "") and 1 not in calls and 120 not in calls,
          asked=asked, auto=auto, edited=edited, handler_frames=calls)
    owner = object()
    bpy.msgbus.subscribe_rna(key=(bpy.types.Object, "location"), owner=owner, args=(), notify=lambda *_args: None)
    try:
        subscribed = motion_facts.motion_at_frames(W, handle, [1, 120], mode="ASK")
        allowed = motion_facts.motion_at_frames(W, handle, [1, 120], mode="AUTO_BOUNDED")
    finally:
        bpy.msgbus.clear_by_owner(owner)
    check("a_subscriber_to_a_value_put_back_is_run_only_where_the_mode_allows",
          "message-bus" in (subscribed.get("not_measured") or "") and "origin" in allowed,
          subscribed=subscribed, allowed=allowed)
    driven = cube("QA Scripted", 1.0, (0, 9, 0))
    curve = driven.driver_add("location", 2)
    curve.driver.type, curve.driver.expression = "SCRIPTED", "bpy.context.scene.frame_current * 0.1"
    scripted = motion_facts.after_edit(W, objects=[moving]) or {}
    driven.driver_remove("location", 2)
    check("a_python_driver_is_not_measured", "Python" in (scripted.get("not_measured") or ""), read=scripted)
    uid = S.session_uid
    before = blender_tools._scene_revisions.get(uid, 0)
    BlenderExecutor(W).execute("angelo_scene_object", {"object": handle, "sections": ["TRANSFORM"], "frames": [1, 90]})
    check("a_frames_read_keeps_the_scene_revision", blender_tools._scene_revisions.get(uid, 0) == before,
          before=before, after=blender_tools._scene_revisions.get(uid, 0))
    clear()
    opened = []
    original_open = motion_frames.Sampler.open
    motion_frames.Sampler.open = lambda self: opened.append(1) or original_open(self)
    try:
        text = bpy.data.objects.new("QA Sign", bpy.data.curves.new("QA Sign", "FONT"))
        S.collection.objects.link(text)
        wall, cutter = cube("QA Wall", 4.0), cube("QA Cutter", 1.0, (2, 0, 0))
        wall.modifiers.new("Cut", "BOOLEAN").object = cutter
        static = motion_facts.after_edit(W, objects=[text, wall])
        cut = cube("QA Moving Cutter", 1.0, (2, 0, 0))
        keyed(cut, "location", ((1, (2, 0, 0)), (120, (2, 0, 3))))
        wall.modifiers["Cut"].object = cut
        motion_facts.after_edit(W, objects=[wall])  # A keyed cutter: the wall's geometry can change; it is read.
    finally:
        motion_frames.Sampler.open = original_open
    check("static_edits_read_nothing", static is None and opened == [1], static=static, opened=len(opened))


def scripts_and_cost():
    clear()
    executor = BlenderExecutor(W)
    made = executor.execute("angelo_execute_python", {"description": "A box with a shape key", "code": (
        "import bpy\n"
        "m = bpy.data.meshes.new('QA Stretch')\n"
        "m.from_pydata([(x, y, z) for x in (0, 1) for y in (0, 1) for z in (0, 1)], [], [(0, 1, 3, 2)])\n"
        "o = bpy.data.objects.new('QA Stretch', m); bpy.context.scene.collection.objects.link(o)\n"
        "o.shape_key_add(name='Basis'); k = o.shape_key_add(name='Tall')\n"
        "for v in k.data: v.co.z *= 8\n"
        "result = {'made': True}\n")})
    keyed_only = executor.execute("angelo_execute_python", {"description": "Key the shape key", "code": (
        "import bpy\n"
        "k = bpy.data.objects['QA Stretch'].data.shape_keys.key_blocks['Tall']\n"
        "k.value = 0.0; k.keyframe_insert('value', frame=1)\n"
        "k.value = 1.0; k.keyframe_insert('value', frame=120)\n"
        "result = {'keyed': True}\n")})
    group = group_named(keyed_only.get("motion"), "QA Stretch") or {}
    heights = [box[1][2] - box[0][2] for box in group.get("bounds", ())]
    check("a_script_that_only_keys_a_shape_key_states_motion", made.get("success") and heights
          and heights[0] == 1.0 and heights[-1] == 8.0, motion=keyed_only.get("motion"), error=keyed_only.get("error"))
    clear()
    size = 700

    def heavy(tree, _group_in, group_out):
        nodes, links = tree.nodes, tree.links
        grid, move, position = (nodes.new("GeometryNodeMeshGrid"), nodes.new("GeometryNodeSetPosition"),
                                nodes.new("GeometryNodeInputPosition"))
        grid.inputs["Vertices X"].default_value = grid.inputs["Vertices Y"].default_value = size
        split, now, add, wave = (nodes.new("ShaderNodeSeparateXYZ"), nodes.new("GeometryNodeInputSceneTime"),
                                 nodes.new("ShaderNodeMath"), nodes.new("ShaderNodeMath"))
        add.operation, wave.operation = "ADD", "SINE"
        lift, smooth = nodes.new("ShaderNodeCombineXYZ"), nodes.new("GeometryNodeSubdivisionSurface")
        smooth.inputs["Level"].default_value = 1
        links.new(position.outputs[0], split.inputs[0])
        links.new(split.outputs["X"], add.inputs[0])
        links.new(now.outputs["Seconds"], add.inputs[1])
        links.new(add.outputs[0], wave.inputs[0])
        links.new(wave.outputs[0], lift.inputs["Z"])
        links.new(grid.outputs[0], move.inputs[0])
        links.new(lift.outputs[0], move.inputs["Offset"])
        links.new(move.outputs[0], smooth.inputs[0])
        links.new(smooth.outputs[0], group_out.inputs[0])
    cube("QA Heavy").modifiers.new("Heavy", "NODES").node_group = geometry_nodes("QA Heavy", heavy)
    moving = rig()
    W.view_layer.update()
    started = time.perf_counter()
    S.frame_set(31)
    one = time.perf_counter() - started
    S.frame_set(30)
    reads = []
    for _attempt in range(2):
        started = time.perf_counter()
        read = motion_facts.after_edit(W, objects=[moving]) or {}
        reads.append({"ms": round((time.perf_counter() - started) * 1000, 1), "frames": read.get("frames"),
                      "not_measured": read.get("not_measured"), "not_read": read.get("not_read")})
    bound = motion_frames.MAX_SECONDS + 3 * one + 0.25  # The budget, a frame past it, the return, the update.
    report["observed"]["heavy_one_frame_ms"] = round(one * 1000, 1)
    check("a_heavy_scene_read_keeps_to_the_wall_budget", all(row["ms"] / 1000 <= bound for row in reads)
          and S.frame_current == 30, reads=reads, one_frame_ms=round(one * 1000, 1), bound_ms=round(bound * 1000))
    motion_frames.ESTIMATES.clear()


def layers_and_scale():
    """Held-out cases: other view layers, curve boxes, large edits, bake nodes."""
    clear()
    moving = rig()
    side = bpy.data.collections.new("QA Side")
    S.collection.children.link(side)
    other = cube("QA Other", 1.0, (0, 30, 0), collection=side)
    keyed(other, "location", ((1, (0, 30, 0)), (120, (8, 30, 0))))
    W.view_layer.layer_collection.children["QA Side"].exclude = True  # Only the second layer evaluates it.
    second = S.view_layers.new("QA Second")
    fresh = motion_facts.after_edit(W, objects=[moving]) or {}  # The second layer has no depsgraph yet.
    second.update()
    S.frame_set(30)
    second.update()
    other.location.y = 33.0  # An unkeyed edit seen only in the second layer.
    read = motion_facts.after_edit(W, objects=[moving]) or {}
    check("a_layer_never_evaluated_is_not_built_and_another_layers_edit_survives",
          "view layer" in (fresh.get("not_measured") or "") and group_named(read, "QA Rig")
          and abs(other.location.y - 33.0) < 1e-6, fresh=fresh, read=read, other_y=other.location.y)

    def simulate(tree, group_in, group_out):
        sim_in, sim_out = tree.nodes.new("GeometryNodeSimulationInput"), tree.nodes.new("GeometryNodeSimulationOutput")
        sim_in.pair_with_output(sim_out)
        tree.links.new(group_in.outputs[0], sim_in.inputs[0])
        tree.links.new(sim_in.outputs[1], sim_out.inputs[1])
        tree.links.new(sim_out.outputs[0], group_out.inputs[0])
    hidden_sim = cube("QA Side Sim", 1.0, (0, 40, 0), collection=side)
    hidden_sim.modifiers.new("Sim", "NODES").node_group = geometry_nodes("QA Side Sim", simulate)
    second.update()
    stepped = motion_facts.after_edit(W, objects=[moving]) or {}
    check("a_simulation_in_another_layer_is_not_stepped", "simulation" in (stepped.get("not_measured") or ""),
          read=stepped)
    hidden_sim.modifiers.clear()
    props = [cube("QA Prop %d" % index, 0.5, (index, -5, 0)) for index in range(4)]
    second.update()
    W.view_layer.update()  # Both layers evaluated, as when each is shown in a window.
    removed = BlenderExecutor(W).execute("angelo_execute_python", {"description": "Remove props, key the rig", "code": (
        "import bpy\n"
        "for name in %r: bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True)\n"
        "rig = bpy.data.objects['QA Rig']; rig.location.z = 2.0; rig.keyframe_insert('location', frame=60)\n"
        "result = {'removed': True}\n") % [prop.name for prop in props]})
    check("a_script_that_removes_objects_with_two_layers_evaluated_is_read", removed.get("success")
          and group_named(removed.get("motion"), "QA Rig"), motion=removed.get("motion"), error=removed.get("error"))
    S.view_layers.remove(second)
    clear()
    moving = rig()

    def passing(tree, group_in, group_out):
        bake = tree.nodes.new("GeometryNodeBake")
        tree.links.new(group_in.outputs[0], bake.inputs[0])
        tree.links.new(bake.outputs[0], group_out.inputs[0])
    cube("QA Pass", 1.0, (0, 50, 0)).modifiers.new("Pass", "NODES").node_group = geometry_nodes("QA Pass", passing)
    read = motion_facts.after_edit(W, objects=[moving]) or {}
    check("a_bake_node_outside_a_simulation_does_not_refuse", group_named(read, "QA Rig"), read=read)
    clear()
    path = bpy.data.objects.new("QA Cable", bpy.data.curves.new("QA Cable", "CURVE"))
    path.data.bevel_depth = 0.5
    spline = path.data.splines.new("POLY")
    spline.points.add(1)
    spline.points[1].co = (4, 0, 0, 1)
    S.collection.objects.link(path)
    keyed(path, "location", ((1, (0, 10, 0)), (120, (0, 20, 0))))
    read = motion_facts.after_edit(W, objects=[path]) or {}
    first = (group_named(read, "QA Cable") or {}).get("bounds", [None])[0]
    check("a_curve_box_is_its_geometry", first == [[0.0, 9.5, -0.5], [4.0, 10.5, 0.5]], first=first)
    clear()
    road, root = cube("QA Long Road", 1.0), bpy.data.objects.new("QA Bridge Root", None)
    S.collection.objects.link(root)
    planks = []
    for index in range(300):
        plank = cube("QA Plank %03d" % index, 0.5, (index * 0.5, 0, 0))
        plank.parent = root
        planks.append(plank)
    car = rig("QA Long Car")
    car.parent = road
    body = cube("QA Long Body", 2.0)
    body.parent = car
    read = motion_facts.after_edit(W, objects=planks + [body]) or {}
    opened = []
    original_open = motion_frames.Sampler.open
    motion_frames.Sampler.open = lambda self: opened.append(1) or original_open(self)
    try:
        static = motion_facts.after_edit(W, objects=planks)
    finally:
        motion_frames.Sampler.open = original_open
    check("a_large_edit_keeps_its_top_and_reads_nothing_static",
          [g["name"] for g in read.get("moving", ())] == ["QA Long Car"] and static is None and not opened,
          groups=[g["name"] for g in read.get("moving", ())], static=static, opened=len(opened))


try:
    layers_and_scale()
    frames_and_simulation()
    rigs_and_scope()
    budgets_and_policies()
    scripts_and_cost()
    report["success"] = all(report["checks"].values()) and bool(report["checks"])
except Exception:
    report["error"] = traceback.format_exc()
    traceback.print_exc()
finally:
    native.cleanup_all()
    (OUT / "motion-guards.json").write_text(json.dumps(report, indent=2, default=str))
failed = sorted(name for name, passed in report["checks"].items() if not passed)
print("MOTION_GUARDS failed checks:", json.dumps(failed))
assert report["success"], failed or report.get("error")
