# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a motion read finds to read and what it states past its caps, in the compiled app (motion_facts).

Run by ``run_native_suites.py --include test_blender_motion_candidates`` (BACKGROUND). Earlier versions missed
edits the read never saw and stated a box from part of the instances; these cases are built apart from the
reproductions (other sizes, rigs, counts and kinds of edit). It proves:

* a script's edits are read from what Blender's depsgraph updates show changed, not only the change list: an object
  past the change list's 1,000, a rig's bone past its first 64 curves, a driver whose expression changes, an NLA strip
  added to a track; a static edit of a large scene still reads nothing, and a relations rebuild (which lists every
  object) adds nothing;
* particle hair instancing an animated object, and a vertex instancer whose child is keyed, animate, and their boxes
  equal Blender's instance list placed corner by corner;
* past ``MAX_INSTANCES`` the group is listed with a null box and the reason, never a box of some instances;
* Pose mode is measured;
* a frame's cost is its evaluation and its read together, and a held scene is refused before the scene-wide scans.

No provider, account or network.
"""

import json
import os
from pathlib import Path
import sys
import time
import traceback

import bpy
from mathutils import Vector

assert bpy.app.background and "--factory-startup" in sys.argv
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ["ANGELO_QA_DIR"])
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import motion_facts, motion_frames, native_programs as native, object_bounds  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402

report = {"checks": {}, "observed": {}, "binary": bpy.app.binary_path, "success": False}
W = bpy.context.window
S = W.scene


def check(name, condition, **observed):
    report["checks"][name] = bool(condition)
    if observed:
        report["observed"][name] = observed


def clear():
    W.view_layer.update()
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    S.frame_start, S.frame_end, S.frame_current = 1, 96, 12
    motion_frames.ESTIMATES.clear()
    W.view_layer.update()


def cube(name, size=1.0, location=(0, 0, 0), link=True):
    mesh = bpy.data.meshes.new(name)
    h = size / 2
    mesh.from_pydata([(x, y, z) for x in (-h, h) for y in (-h, h) for z in (-h, h)], [],
                     [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)])
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    if link:
        S.collection.objects.link(obj)
    return obj


def keyed(owner, path, values):
    for frame, value in values:
        setattr(owner, path, value)
        owner.keyframe_insert(path, frame=frame)


def script(code):
    return BlenderExecutor(W).execute("angelo_execute_python", {"description": "QA motion candidates", "code": code})


def names(read):
    return [group["name"] for group in (read or {}).get("moving", ())]


def instance_truth(instancer, frames):
    """Each frame's box of what ``instancer`` makes, corner by corner from Blender's instance list."""
    found, original = [], S.frame_current
    for frame in frames:
        S.frame_set(frame)
        low, high = [float("inf")] * 3, [float("-inf")] * 3
        for instance in W.view_layer.depsgraph.object_instances:
            if instance.is_instance and instance.parent is not None and instance.parent.original == instancer:
                matrix = instance.matrix_world.copy()
                for corner in instance.object.bound_box:
                    point = matrix @ Vector(corner)
                    low, high = [min(a, b) for a, b in zip(low, point)], [max(a, b) for a, b in zip(high, point)]
        found.append(object_bounds.rounded([low, high]))
    S.frame_set(original)
    return found


def near(a, b, tolerance=0.002):
    return a is not None and b is not None and all(abs(x - y) <= tolerance for p, q in zip(a, b) for x, y in zip(p, q))


def past_the_change_list():
    clear()
    for index in range(1100):  # Past scene_signature's 1,000 objects: the change list cannot see the last ones.
        cube("QA Still %04d" % index, 0.5, (index % 40, index // 40, 0))
    W.view_layer.update()
    keyed_late = script("import bpy\nobj = bpy.data.objects['QA Still 1090']\n"
                        "obj.keyframe_insert('location', frame=1)\nobj.location.z = 6\n"
                        "obj.keyframe_insert('location', frame=96)\n")
    check("an_object_past_the_change_list_is_read", names(keyed_late.get("motion")) == ["QA Still 1090"],
          motion=keyed_late.get("motion"), modified=keyed_late["changes"]["modified"][:3])
    added = script("import bpy\ncart = bpy.data.objects.new('QA Cart', None)\nbpy.context.scene.collection.objects.link(cart)\n"
                   "cart.keyframe_insert('location', frame=1)\ncart.location.y = -9\ncart.keyframe_insert('location', frame=96)\n"
                   "box = bpy.data.objects['QA Still 0003'].copy(); box.name = 'QA Cart Box'; box.parent = cart\n"
                   "bpy.context.scene.collection.objects.link(box)\n")
    check("an_added_rig_past_the_change_list_is_read", names(added.get("motion")) == ["QA Cart"],
          motion=added.get("motion"))
    still = script("import bpy\nbpy.data.objects['QA Still 1095'].location.x += 2\n"
                   "extra = bpy.data.objects.new('QA Marker', None)\nbpy.context.scene.collection.objects.link(extra)\n")
    check("a_static_edit_of_a_large_scene_reads_nothing", "motion" not in still, motion=still.get("motion"))
    clear()
    for index in range(3):
        keyed(bpy.data.objects.new("QA Swing %d" % index, None), "rotation_euler", ((1, (0, 0, 0)), (96, (0, 0, 2))))
        S.collection.objects.link(bpy.data.objects["QA Swing %d" % index])
    cube("QA Crate", 1.0, (5, 0, 0))
    W.view_layer.update()
    scrub = script("import bpy\nscene = bpy.context.scene\nfor frame in (10, 50, 90):\n"
                   "    scene.frame_current = frame\n    bpy.context.view_layer.update()\n"
                   "scene.frame_current = 12\nbpy.context.view_layer.update()\n"
                   "bpy.data.objects['QA Crate'].location.y = 3\n")
    check("a_script_scrubbing_frames_reports_no_untouched_rig", "motion" not in scrub, motion=scrub.get("motion"))


def rig_curves_drivers_and_strips():
    clear()
    arm = bpy.data.objects.new("QA Spine", bpy.data.armatures.new("QA Spine"))
    S.collection.objects.link(arm)
    W.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    for index in range(20):
        bone = arm.data.edit_bones.new("Seg%02d" % index)
        bone.head, bone.tail = (0, 0, index * 0.5), (0, 0, index * 0.5 + 0.5)
    bpy.ops.object.mode_set(mode="OBJECT")
    for bone in arm.pose.bones:  # 10 curves a bone: 200, past motion_signature's first 64.
        for frame in (1, 96):
            bone.keyframe_insert("location", frame=frame)
            bone.keyframe_insert("rotation_quaternion", frame=frame)
            bone.keyframe_insert("scale", frame=frame)
    held = cube("QA Spine Tip", 0.4, (0, 0, 9.75))
    held.parent, held.parent_type, held.parent_bone = arm, "BONE", "Seg19"
    W.view_layer.update()
    read = script("import bpy\nbone = bpy.data.objects['QA Spine'].pose.bones['Seg19']\n"
                  "bone.location = (3, 0, 0)\nbone.keyframe_insert('location', frame=48)\n")
    check("a_bone_past_the_first_64_curves_is_read", names(read.get("motion")) == ["QA Spine"],
          motion=read.get("motion"), modified=read["changes"]["modified"])
    clear()
    lead = bpy.data.objects.new("QA Lead", None)
    S.collection.objects.link(lead)
    keyed(lead, "location", ((1, (0, 0, 0)), (96, (0, 12, 0))))
    follower = cube("QA Follower", 1.0)
    driver = follower.driver_add("location", 1).driver
    driver.type = "AVERAGE"
    variable = driver.variables.new()
    variable.type = "TRANSFORMS"
    variable.targets[0].id, variable.targets[0].transform_type = lead, "LOC_Z"  # The lead's still channel.
    track_owner = cube("QA Strip Owner", 1.0, (8, 0, 0))
    track_owner.animation_data_create()
    track = track_owner.animation_data.nla_tracks.new()
    lift = bpy.data.objects.new("QA Lift Source", None)
    S.collection.objects.link(lift)
    keyed(lift, "location", ((1, (8, 0, 0)), (40, (8, 0, 5))))
    lift.animation_data.action.use_fake_user = True
    lift.animation_data.action = None
    W.view_layer.update()
    swapped = script("import bpy\nd = bpy.data.objects['QA Follower'].animation_data.drivers[0].driver\n"
                     "d.variables[0].targets[0].transform_type = 'LOC_Y'\n")  # Now it follows the lead's motion.
    check("a_changed_driver_is_read", "QA Follower" in names(swapped.get("motion")), motion=swapped.get("motion"),
          modified=swapped["changes"]["modified"])
    strip = script("import bpy\nowner = bpy.data.objects['QA Strip Owner']\n"
                   "owner.animation_data.nla_tracks[0].strips.new('QA Lift', 10, bpy.data.actions['QA Lift SourceAction'])\n")
    check("an_added_nla_strip_is_read", "QA Strip Owner" in names(strip.get("motion")), motion=strip.get("motion"))


def instancers():
    clear()
    source = cube("QA Pebble", 0.3, (0, 0, -20))
    keyed(source, "scale", ((1, (1, 1, 1)), (96, (2.5, 2.5, 2.5))))
    field = cube("QA Field", 4.0, (0, 30, 0))
    field.modifiers.new("Hair", "PARTICLE_SYSTEM")
    settings = field.particle_systems[0].settings
    settings.type, settings.count, settings.hair_length = "HAIR", 40, 1.0
    settings.render_type, settings.instance_object = "OBJECT", source
    W.view_layer.update()
    read = motion_facts.after_edit(W, objects=[field]) or {}
    group = next((g for g in read.get("moving", ()) if g["name"] == "QA Field"), {})
    truth = instance_truth(field, read.get("frames") or [1])
    own = object_bounds.world_bounds(field)
    merged = [[[min(a, b) for a, b in zip(box[0], own[0])], [max(a, b) for a, b in zip(box[1], own[1])]]
              for box in truth]
    check("particle_instances_of_an_animated_object_animate_and_match",
          len(group.get("bounds") or []) == len(truth) and all(near(a, b) for a, b in zip(group["bounds"], merged)),
          read=read, truth=merged)
    clear()
    pin = cube("QA Pin", 0.2)
    keyed(pin, "rotation_euler", ((1, (0, 0, 0)), (96, (0, 1.2, 0))))
    plate = cube("QA Plate", 3.0, (0, -30, 0))
    plate.instance_type = "VERTS"
    pin.parent = plate
    W.view_layer.update()
    edited = script("import bpy\nbpy.data.objects['QA Plate'].location.x = 4\n")
    group = next((g for g in (edited.get("motion") or {}).get("moving", ()) if g["name"] == "QA Plate"), {})
    truth = instance_truth(plate, (edited.get("motion") or {}).get("frames") or [1])
    check("editing_a_vertex_instancer_of_a_keyed_child_reads_it", len(group.get("bounds") or []) == len(truth)
          and all(near(a, b) for a, b in zip(group["bounds"], truth)), motion=edited.get("motion"), truth=truth)
    clear()
    grain = cube("QA Grain", 0.02, (0, 0, -20))
    keyed(grain, "location", ((1, (0, 0, -20)), (96, (0, 0, -15))))
    tree = bpy.data.node_groups.new("QA Grains", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    grid, info, place, out = (tree.nodes.new("GeometryNodeMeshGrid"), tree.nodes.new("GeometryNodeObjectInfo"),
                              tree.nodes.new("GeometryNodeInstanceOnPoints"), tree.nodes.new("NodeGroupOutput"))
    grid.inputs["Vertices X"].default_value, grid.inputs["Vertices Y"].default_value = 110, 100  # 11,000 points.
    info.inputs["Object"].default_value, info.inputs["As Instance"].default_value = grain, True
    tree.links.new(grid.outputs["Mesh"], place.inputs["Points"])
    tree.links.new(info.outputs["Geometry"], place.inputs["Instance"])
    tree.links.new(place.outputs["Instances"], out.inputs[0])
    sand = cube("QA Sand", 0.1, (0, 60, 0))
    sand.modifiers.new("Grains", "NODES").node_group = tree
    W.view_layer.update()
    read = motion_facts.after_edit(W, objects=[sand]) or {}
    group = next((g for g in read.get("moving", ()) if g["name"] == "QA Sand"), {})
    check("past_the_instance_cap_no_box_is_stated",  # Or, on a loaded machine, nothing stated in the time.
          (group and all(box is None for box in group["bounds"]) and "not measured" in (group.get("instances") or ""))
          or (not read.get("moving") and "not_measured" in read), read=read)


def mixed_instances():
    """Different geometry instanced from one set (Pick Instance), and a curve inside a moving collection instance."""
    clear()
    shapes = bpy.data.collections.new("QA Shapes")
    for index, size in enumerate((0.2, 1.4, 3.0)):
        shapes.objects.link(cube("QA Shape %d" % index, size, link=False))
    tree = bpy.data.node_groups.new("QA Picks", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    line, info, index_node, place, spin, out = (
        tree.nodes.new("GeometryNodeMeshLine"), tree.nodes.new("GeometryNodeCollectionInfo"),
        tree.nodes.new("GeometryNodeInputIndex"), tree.nodes.new("GeometryNodeInstanceOnPoints"),
        tree.nodes.new("GeometryNodeRotateInstances"), tree.nodes.new("NodeGroupOutput"))
    line.inputs["Count"].default_value = 9
    info.inputs["Collection"].default_value = shapes
    info.inputs["Separate Children"].default_value = info.inputs["Reset Children"].default_value = True
    place.inputs["Pick Instance"].default_value = True
    tree.links.new(line.outputs["Mesh"], place.inputs["Points"])
    tree.links.new(info.outputs["Instances"], place.inputs["Instance"])
    tree.links.new(index_node.outputs["Index"], place.inputs["Instance Index"])
    tree.links.new(place.outputs["Instances"], spin.inputs["Instances"])
    tree.links.new(spin.outputs["Instances"], out.inputs[0])
    spinner = cube("QA Spinner", 0.1, (0, -50, 0))
    spinner.modifiers.new("Picks", "NODES").node_group = tree
    keyed(spinner, "location", ((1, (0, -50, 0)), (96, (6, -50, 0))))
    W.view_layer.update()
    read = motion_facts.after_edit(W, objects=[spinner]) or {}
    group = next((g for g in read.get("moving", ()) if g["name"] == "QA Spinner"), {})
    truth = instance_truth(spinner, read.get("frames") or [1])
    check("mixed_geometry_instances_each_keep_their_own_box", len(group.get("bounds") or []) == len(truth)
          and all(near(a, b) for a, b in zip(group["bounds"], truth)), read=read, truth=truth)
    clear()
    rails = bpy.data.collections.new("QA Rails")
    curve = bpy.data.curves.new("QA Rail", "CURVE")
    spline = curve.splines.new("POLY")
    spline.points.add(1)
    spline.points[0].co, spline.points[1].co = (0, 0, 0, 1), (2.5, 0, 0, 1)
    curve.dimensions, curve.bevel_depth = "3D", 0.15
    rails.objects.link(bpy.data.objects.new("QA Rail", curve))
    holder = bpy.data.objects.new("QA Rail Holder", None)
    holder.instance_type, holder.instance_collection = "COLLECTION", rails
    S.collection.objects.link(holder)
    keyed(holder, "location", ((1, (0, 0, 40)), (96, (0, 0, 44))))
    W.view_layer.update()
    read = motion_facts.after_edit(W, objects=[holder]) or {}
    group = next((g for g in read.get("moving", ()) if g["name"] == "QA Rail Holder"), {})
    first = (group.get("bounds") or [None])[0]
    check("a_curve_in_a_collection_instance_has_its_true_box",
          near(first, [[0, -0.15, 39.85], [2.5, 0.15, 40.15]], 0.01), read=read)


def deferred_meshes():
    """A script's result boxes never evaluate a deferred subdivided mesh in full (reading the
    evaluated mesh's vertices cost 0.26 s an object), and a Geometry Nodes output of instances only
    still has no box of its own base."""
    clear()
    code = ("import bpy\nfor i in range(6):\n"
            "    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=4, radius=0.4, location=(i * 1.5, 8, 0))\n"
            "    bpy.context.object.name = 'QA Pebble %d' % i\n"
            "    bpy.context.object.modifiers.new('Smooth', 'SUBSURF').levels = 3\n")
    exec(code, {"bpy": bpy})
    pebbles = [bpy.data.objects["QA Pebble %d" % i] for i in range(6)]
    W.view_layer.update()  # Evaluated as after any edit; Blender defers the subdivision until its data is read.
    started = time.perf_counter()
    boxes = [object_bounds.world_bounds(obj, depsgraph=W.view_layer.depsgraph) for obj in pebbles]
    spent = time.perf_counter() - started
    check("boxes_never_evaluate_a_subdivided_mesh", all(boxes) and spent < 0.2, ms=round(spent * 1000, 1))
    for pebble in pebbles:  # The same meshes keyed: a frames read evaluates them as a frame change does, no more.
        keyed(pebble, "location", ((1, tuple(pebble.location)), (96, tuple(pebble.location + Vector((0, 0, 2))))))
    W.view_layer.update()
    started = time.perf_counter()
    read = motion_facts.after_edit(W, objects=pebbles) or {}
    spent = time.perf_counter() - started
    check("a_frames_read_never_evaluates_a_subdivided_mesh", len(read.get("moving", ())) == 6 and spent < 1.0,
          ms=round(spent * 1000, 1), frames=read.get("frames"), not_measured=read.get("not_measured"))


def pose_mode_and_budgets():
    clear()
    arm = bpy.data.objects.new("QA Arm", bpy.data.armatures.new("QA Arm"))
    S.collection.objects.link(arm)
    W.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    arm.data.edit_bones.new("Reach").tail = (0, 0, 1)
    bpy.ops.object.mode_set(mode="POSE")
    keyed(arm.pose.bones["Reach"], "location", ((1, (0, 0, 0)), (96, (0, 4, 0))))
    hand = cube("QA Hand", 0.3, (0, 0, 1))
    hand.parent, hand.parent_type, hand.parent_bone = arm, "BONE", "Reach"
    read = motion_facts.after_edit(W, objects=[arm]) or {}
    check("pose_mode_is_measured", names(read) == ["QA Arm"] and arm.mode == "POSE", read=read)
    bpy.ops.object.mode_set(mode="OBJECT")
    now = [0.0]
    sampler = motion_frames.Sampler(W, clock=lambda: now[0])
    with sampler:
        opened = sampler.open()
        first = sampler.at(1, lambda: now.__setitem__(0, now[0] + 0.12))  # A read slower than its frame.
        second = sampler.at(96)
    check("a_frames_read_counts_toward_its_cost", opened is None and first and not second
          and sampler.slowest >= 0.12, slowest=sampler.slowest)
    motion_frames.ESTIMATES.note(S, 0.9, motion_frames.MAX_SECONDS)
    started = time.perf_counter()
    held = motion_facts.after_edit(W, objects=[arm]) or {}
    spent = time.perf_counter() - started
    check("a_held_scene_is_refused_before_its_scans", "takes about 0.90 s" in (held.get("not_measured") or "")
          and spent < 0.05, held=held, ms=round(spent * 1000, 2))
    motion_frames.ESTIMATES.clear()


try:
    past_the_change_list()
    rig_curves_drivers_and_strips()
    instancers()
    mixed_instances()
    deferred_meshes()
    pose_mode_and_budgets()
    report["success"] = all(report["checks"].values()) and bool(report["checks"])
except Exception:
    report["error"] = traceback.format_exc()
    traceback.print_exc()
finally:
    native.cleanup_all()
    (OUT / "motion-candidates.json").write_text(json.dumps(report, indent=2, default=str))
failed = sorted(name for name, passed in report["checks"].items() if not passed)
print("MOTION_CANDIDATES failed checks:", json.dumps(failed))
assert report["success"], failed or report.get("error")
