# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where animated objects are at a few frames, in the compiled app (motion_facts, motion_frames).

Run by ``run_native_suites.py --include test_blender_motion_readback`` (BACKGROUND). Rigs held out from the
recorded bridge cars: a keyed rig with a body and four wheels parented to it, a mesh parented to a keyed bone, a
Child Of constraint to a keyed target, a Follow Path constraint on an animated curve, a driver, a time-reading
Geometry Nodes object and an object whose parent moves it while its own keys hold it. It proves:

* every value equals Blender's own evaluation at that frame (``scene.frame_set``, read, measured independently);
* the scene ends as it began: frame and subframe, selection, active object, scene camera, an unkeyed edit of an
  animated channel, a baked physics cache;
* a script's result (``motion``), a committed program's result and ``angelo_scene_object`` with ``frames``
  (``at_frames``) carry the facts; a part that does not follow its parent is named (``apart``);
* unbaked physics is not measured (the reason is stated) and nothing is evaluated;
* the cost on a large animated scene stays within the read's budget (recorded).

The guards and the review's edge cases are ``test_blender_motion_guards``; the interface's notifiers are
``test_blender_motion_interface`` (a window).

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
from angelo_agent import motion_facts, motion_frames, native_programs as native, object_bounds  # noqa: E402
from angelo_agent import scene_queries as queries  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402

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
    S.frame_start, S.frame_end, S.frame_current = 1, 120, 30


def cube(name, size=1.0, location=(0, 0, 0)):
    mesh = bpy.data.meshes.new(name)
    h = size / 2
    mesh.from_pydata([(x, y, z) for x in (-h, h) for y in (-h, h) for z in (-h, h)], [],
                     [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)])
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    S.collection.objects.link(obj)
    return obj


def keyed(obj, path, values):
    for frame, value in values:
        setattr(obj, path, value)
        obj.keyframe_insert(path, frame=frame)


def parent_keep(child, parent):
    W.view_layer.update()
    world = child.matrix_world.copy()
    child.parent = parent
    child.matrix_parent_inverse = parent.matrix_world.inverted()
    child.matrix_world = world


def truth(objects, frames):
    """Blender's own evaluation: ``scene.frame_set`` per frame, each object's world box and origin."""
    original = S.frame_current
    found = []
    for frame in frames:
        S.frame_set(frame)
        boxes = [object_bounds.world_bounds(obj) for obj in objects]
        boxes = [box for box in boxes if box]
        found.append({"origin": [round(v, 3) + 0.0 for v in objects[0].matrix_world.translation],
                      "bounds": object_bounds.rounded([[min(b[0][i] for b in boxes) for i in range(3)],
                                                       [max(b[1][i] for b in boxes) for i in range(3)]])})
    S.frame_set(original)
    return found


def same(read, expected):
    return (read["origin"] == [row["origin"] for row in expected]
            and read["bounds"] == [row["bounds"] for row in expected])


def state():
    return (S.frame_current, S.frame_subframe, S.camera, tuple(o.name for o in bpy.context.selected_objects),
            W.view_layer.objects.active)


def car(stale=False):
    """A keyed rig with a body and four wheels; ``stale``: the wheels' parent inverse taken before the rig moved."""
    rig = bpy.data.objects.new("QA Rig", None)
    S.collection.objects.link(rig)
    body = cube("QA Body", 2.0, (0, 0, 1))
    corners = ((-.8, -1), (.8, -1), (-.8, 1), (.8, 1))
    wheels = [cube("QA Wheel %d" % i, 0.6, (x, y, 0.3)) for i, (x, y) in enumerate(corners)]
    rig.location = (-10, -1, 0)
    if stale:
        for part in [body] + wheels:  # The recorded idiom: an inverse from a matrix never updated (identity here).
            part.parent = rig
            part.matrix_parent_inverse = rig.matrix_world.inverted()
            part.location = (part.location.x + 10, part.location.y + 1, part.location.z)
    else:
        for part in [body] + wheels:
            part.location = (part.location.x - 10, part.location.y - 1, part.location.z)
            parent_keep(part, rig)
    keyed(rig, "location", ((1, (-10, -1, 0)), (120, (10, -1, 0))))
    return rig, [rig, body] + wheels


def rigs():
    clear()
    rig, parts = car()
    read = motion_facts.after_edit(W, objects=[rig])
    group = (read or {}).get("moving", [{}])[0]
    check("parented_car_matches_blenders_evaluation",
          read and read["frames"] == [1, 60, 120] and same(group, truth(parts, read["frames"]))
          and len(group["parts"]) == 6 and "apart" not in read, read=read)
    # A bone: a mesh parented to a keyed bone.
    clear()
    arm = bpy.data.objects.new("QA Arm", bpy.data.armatures.new("QA Arm"))
    S.collection.objects.link(arm)
    W.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    bone = arm.data.edit_bones.new("Lift")
    bone.head, bone.tail = (0, 0, 0), (0, 0, 1)
    bpy.ops.object.mode_set(mode="POSE")
    pose = arm.pose.bones["Lift"]
    for frame, z in ((1, 0.0), (120, 3.0)):
        pose.location = (0, z, 0)
        pose.keyframe_insert("location", frame=frame)
    bpy.ops.object.mode_set(mode="OBJECT")
    held = cube("QA Held", 0.5, (0, 0, 1))
    held.parent, held.parent_type, held.parent_bone = arm, "BONE", "Lift"
    read = motion_facts.after_edit(W, objects=[held, arm])
    group = (read or {}).get("moving", [{}])[0]
    check("bone_parented_mesh_matches", read and same(group, truth([arm, held], read["frames"])), read=read)
    # Child Of to a keyed target; Follow Path on an animated curve; a driver; time-reading geometry nodes.
    clear()
    target = bpy.data.objects.new("QA Target", None)
    S.collection.objects.link(target)
    keyed(target, "location", ((1, (0, 0, 0)), (120, (0, 8, 0))))
    follower = cube("QA Follower", 1.0, (2, 0, 0))
    follower.constraints.new("CHILD_OF").target = target
    curve = bpy.data.curves.new("QA Path", "CURVE")
    spline = curve.splines.new("POLY")
    spline.points.add(1)
    spline.points[0].co, spline.points[1].co = (0, 0, 0, 1), (20, 0, 0, 1)
    curve.use_path, curve.path_duration = True, 120
    path = bpy.data.objects.new("QA Path", curve)
    S.collection.objects.link(path)
    rider = cube("QA Rider", 1.0, (0, 5, 0))
    follow = rider.constraints.new("FOLLOW_PATH")
    follow.target, follow.use_curve_follow = path, True
    keyed(curve, "eval_time", ((1, 0.0), (120, 120.0)))
    driven = cube("QA Driven", 1.0, (0, -5, 0))
    curve_driver = driven.driver_add("location", 2)
    curve_driver.driver.type = "AVERAGE"
    variable = curve_driver.driver.variables.new()
    variable.targets[0].id, variable.targets[0].data_path = target, "location.y"
    tree = bpy.data.node_groups.new("QA Rise", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nodes, links = tree.nodes, tree.links
    group_in, group_out = nodes.new("NodeGroupInput"), nodes.new("NodeGroupOutput")
    time_node, move = nodes.new("GeometryNodeInputSceneTime"), nodes.new("GeometryNodeSetPosition")
    combine = nodes.new("ShaderNodeCombineXYZ")
    links.new(time_node.outputs["Seconds"], combine.inputs["Z"])
    links.new(group_in.outputs[0], move.inputs["Geometry"])
    links.new(combine.outputs["Vector"], move.inputs["Offset"])
    links.new(move.outputs["Geometry"], group_out.inputs[0])
    rising = cube("QA Rising", 1.0, (0, 10, 0))
    rising.modifiers.new("Rise", "NODES").node_group = tree
    objects = [follower, rider, driven, rising]
    read = motion_facts.after_edit(W, objects=objects) or {}
    groups = {group["name"]: group for group in read.get("moving", ())}
    results = {obj.name: (obj.name in groups and same(groups[obj.name], truth([obj], read["frames"])))
               for obj in objects}
    check("child_of_follow_path_driver_and_time_nodes_match", all(results.values()), results=results, read=read)
    # A child a constraint holds in place while its parent moves: named apart.
    clear()
    rig, parts = car()
    wheel = parts[2]
    W.view_layer.update()
    anchor = bpy.data.objects.new("QA Anchor", None)
    S.collection.objects.link(anchor)
    anchor.location = wheel.matrix_world.translation
    wheel.constraints.new("COPY_LOCATION").target = anchor
    read = motion_facts.after_edit(W, objects=[rig]) or {}
    apart = [row["part"] for row in read.get("apart", ())]
    check("a_part_that_does_not_follow_its_parent_is_named_apart", apart == [wheel.name], apart=read.get("apart"))


def instances():
    """A moving rig whose child scatters cubes with Geometry Nodes, and a moving collection instance: the boxes hold
    the instances, as Blender's own instance list places them."""
    clear()
    rig = bpy.data.objects.new("QA Instance Rig", None)
    S.collection.objects.link(rig)
    keyed(rig, "location", ((1, (0, 0, 0)), (120, (30, 0, 0))))
    base = cube("QA Scatter", 0.1)
    base.parent = rig
    tree = bpy.data.node_groups.new("QA Scatter", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nodes, links = tree.nodes, tree.links
    grid, unit, place, out = (nodes.new("GeometryNodeMeshGrid"), nodes.new("GeometryNodeMeshCube"),
                              nodes.new("GeometryNodeInstanceOnPoints"), nodes.new("NodeGroupOutput"))
    grid.inputs["Size X"].default_value, grid.inputs["Size Y"].default_value = 10.0, 6.0
    links.new(grid.outputs["Mesh"], place.inputs["Points"])
    links.new(unit.outputs["Mesh"], place.inputs["Instance"])
    links.new(place.outputs["Instances"], out.inputs[0])
    base.modifiers.new("Scatter", "NODES").node_group = tree
    kit = bpy.data.collections.new("QA Kit")
    kit.objects.link(bpy.data.objects.new("QA Kit Part", cube("QA Kit Mesh", 2.0).data))
    S.collection.objects.unlink(bpy.data.objects["QA Kit Mesh"])
    holder = bpy.data.objects.new("QA Kit Instance", None)
    holder.instance_type, holder.instance_collection = "COLLECTION", kit
    S.collection.objects.link(holder)
    keyed(holder, "location", ((1, (0, 20, 0)), (120, (0, 40, 0))))
    read = motion_facts.after_edit(W, objects=[base, holder]) or {}
    groups = {group["name"]: group for group in read.get("moving", ())}
    scatter = groups.get("QA Instance Rig", {}).get("bounds") or [None]
    kit_box = groups.get("QA Kit Instance", {}).get("bounds") or [None]
    check("instance_boxes_are_in_the_group_bounds",
          scatter[0] == [[-5.5, -3.5, -0.5], [5.5, 3.5, 0.5]] and kit_box[0] == [[-1.0, 19.0, -1.0], [1.0, 21.0, 1.0]],
          scatter=scatter, kit=kit_box)


def restores():
    clear()
    rig, parts = car()
    cam = bpy.data.objects.new("QA Cam", bpy.data.cameras.new("QA Cam"))
    S.collection.objects.link(cam)
    S.camera = cam
    S.frame_subframe = 0.25
    W.view_layer.update()  # Blender's own update of that time change, as the interface runs it.
    rig.location.x = -3.0  # An unkeyed edit of a keyed channel after it: it must outlive the read.
    parts[1].select_set(True)
    W.view_layer.objects.active = parts[2]
    before = state()
    read = motion_facts.after_edit(W, objects=[rig])
    check("the_scene_ends_as_it_began", state() == before and abs(rig.location.x + 3.0) < 1e-6 and read,
          frame=S.frame_current, subframe=S.frame_subframe, rig_x=rig.location.x)
    # A baked cloth cache: still baked and readable at its frames afterwards.
    clear()
    rig, parts = car()
    cloth = cube("QA Cloth", 1.0, (0, 6, 3))
    cloth.modifiers.new("Cloth", "CLOTH")
    cache = cloth.modifiers["Cloth"].point_cache
    cache.frame_start, cache.frame_end = 1, 5
    with bpy.context.temp_override(point_cache=cache, scene=S, active_object=cloth):
        bpy.ops.ptcache.bake(bake=True)
    baked = cache.is_baked
    read = motion_facts.after_edit(W, objects=[rig])
    check("a_baked_cache_stays_baked", baked and cache.is_baked and read is not None, baked_before=baked)
    # Unbaked physics: not measured, nothing evaluated.
    with bpy.context.temp_override(point_cache=cache, scene=S, active_object=cloth):
        bpy.ops.ptcache.free_bake()
    frame = S.frame_current
    read = motion_facts.after_edit(W, objects=[rig])
    check("unbaked_physics_is_not_measured", read and "not_measured" in read and S.frame_current == frame, read=read)


def stale_depsgraph():
    """Objects removed and added with no update since (a script's edit): the read evaluates first, never reading a
    depsgraph that still lists freed objects (that crashed the app before)."""
    clear()
    rig, parts = car()
    W.view_layer.update()
    for obj in parts[2:]:
        bpy.data.objects.remove(obj, do_unlink=True)
    fresh = car()[0]
    read = motion_facts.after_edit(W, objects=[fresh])
    check("a_stale_depsgraph_is_evaluated_first", read is not None and read.get("moving"), read=read)


def through_tools():
    clear()
    code = ("import bpy\n"
            "rig = bpy.data.objects.new('QA Script Rig', None); bpy.context.scene.collection.objects.link(rig)\n"
            "m = bpy.data.meshes.new('QA Script Body')\n"
            "m.from_pydata([(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (0, 1)], [], [(0, 1, 3, 2)])\n"
            "body = bpy.data.objects.new('QA Script Body', m); bpy.context.scene.collection.objects.link(body)\n"
            "body.parent = rig\n"
            "rig.location = (-5, 0, 0); rig.keyframe_insert('location', frame=1)\n"
            "rig.location = (5, 0, 0); rig.keyframe_insert('location', frame=120)\n"
            "result = {'built': True}\n")
    result = BlenderExecutor(W).execute("angelo_execute_python", {"description": "A moving rig", "code": code})
    motion = result.get("motion") or {}
    check("a_script_result_states_motion", motion.get("frames") == [1, 60, 120]
          and [group["name"] for group in motion.get("moving", ())] == ["QA Script Rig"], motion=motion,
          error=result.get("error"))
    rig = bpy.data.objects["QA Script Rig"]
    handle = queries.object_handle(rig)
    asked = BlenderExecutor(W).execute("angelo_scene_object", {"object": handle, "sections": ["TRANSFORM"],
                                                                "frames": [1, 90]})
    at = asked.get("at_frames") or {}
    expected = truth([rig, bpy.data.objects["QA Script Body"]], [1, 90])
    check("scene_object_frames_match", at.get("frames") == [1, 90]
          and at.get("origin") == [row["origin"] for row in expected]
          and at.get("with_children") == [row["bounds"] for row in expected], at=at)
    # A typed program that keys an object: its committed result states the motion.
    box = bpy.data.objects["QA Script Body"]
    program = {"mode": "APPLY", "expected_checks": [],
               "read_set": [{"handle": handle, "revision": queries.object_revision(W, rig)}],
               "write_set": [{"handle": queries.object_handle(box), "revision": queries.object_revision(W, box)}],
               "operations": [{"kind": "keyframe_insert", "target": queries.object_handle(box),
                               "parameters": {"property": "rotation_euler", "frame": 120, "value": [0, 0, 1.5]}}]}
    try:
        native.precondition(W, "angelo_scene_program", validate_call("angelo_scene_program", program))
        job = native.begin(W, "angelo_scene_program", validate_call("angelo_scene_program", program))
        polled = job.poll()
        while not (polled.get("final") or polled.get("ready_to_commit")):
            polled = job.poll()
        committed = job.commit() if polled.get("ready_to_commit") else polled
    except Exception as error:  # noqa: BLE001  (recorded as the check's evidence)
        committed = {"error": repr(error)}
    check("a_program_result_states_motion", committed.get("status") == "committed"
          and (committed.get("motion") or {}).get("moving"), motion=committed.get("motion"),
          error=committed.get("error"))


def costs():
    """A large animated scene: 2,000 cubes under 200 keyed rigs (the heavy multi-threaded case, one frame of
    which costs more than the budget allows, is ``test_blender_motion_guards``)."""
    clear()
    rigs_, parts = [], []
    for index in range(200):
        rig = bpy.data.objects.new("QA Big Rig %d" % index, None)
        S.collection.objects.link(rig)
        keyed(rig, "location", ((1, (index, 0, 0)), (120, (index, 30, 0))))
        rigs_.append(rig)
        for part in range(10):
            obj = cube("QA Big Part %d %d" % (index, part), 0.5, (index, part, 0))
            obj.parent = rig
            parts.append(obj)
    W.view_layer.update()
    started = time.perf_counter()
    read = motion_facts.after_edit(W, objects=rigs_)
    spent = time.perf_counter() - started
    report["observed"]["large_scene_ms"] = round(spent * 1000, 1)
    check("a_large_scene_read_is_bounded", read is not None and spent < 2 * motion_frames.MAX_SECONDS + 1.0,
          ms=round(spent * 1000, 1), groups=len((read or {}).get("moving", ())), more=(read or {}).get("moving_more"),
          not_read=(read or {}).get("not_read"))


try:
    rigs()
    instances()
    restores()
    stale_depsgraph()
    through_tools()
    costs()
    report["success"] = all(report["checks"].values()) and bool(report["checks"])
except Exception:
    report["error"] = traceback.format_exc()
    traceback.print_exc()
finally:
    native.cleanup_all()
    (OUT / "motion-readback.json").write_text(json.dumps(report, indent=2, default=str))
failed = sorted(name for name, passed in report["checks"].items() if not passed)
print("MOTION_READBACK failed checks:", json.dumps(failed))
assert report["success"], failed or report.get("error")
