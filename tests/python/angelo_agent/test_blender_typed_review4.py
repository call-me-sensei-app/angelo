# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Typed-refusal edge cases: what an orbit's camera follows, stated defaults and shared node groups.

* An orbit's camera follows what its drivers read and the Objects in its
  vertex parent's nested Geometry Nodes groups; its aim (a Track To) does not
  move it, so moving the aimed object beside an orbit is admitted.
* A ``material_principled`` that states a default value states it; a name
  alone keeps a held material, and a face selector assigns its faces.
* A Geometry Nodes group two modifiers share is scanned once (the object
  still rebinds after a frame step); a ``rotation_mode`` change names the
  channel it rewrites; a camera's unkeyed setting beside a keyed lens commits;
  a failure resuming a stage while polling reports the verified unchanged scene.

Run in a disposable instance with the exact Angelo binary: ``--background
--factory-startup --disable-autoexec --python-exit-code 1``. No provider or
paid call.
"""

import json
import math
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-typed-review4-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, program_camera, program_frames, program_orbits  # noqa: E402
from angelo_agent import scene_queries as queries  # noqa: E402
from angelo_agent.refusal_codes import RefusalError  # noqa: E402
from angelo_agent.scene_settings import scene_handle, settings_revision  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import no_stages, commit_operator  # noqa: E402

report = {"checks": {}, "cases": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path,
          "version": bpy.app.version_string}
window = bpy.context.window_manager.windows[0]
NAME = "angelo_scene_program"
ORBIT = {"azimuth": math.pi, "elevation": .3}


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def handle(obj):
    return queries.object_handle(obj)


def op(kind, target=None, **parameters):
    reference = target if isinstance(target, str) or target is None else handle(target)
    return {"kind": kind, "target": reference, "parameters": parameters}


def entry(obj):
    return {"handle": handle(obj), "revision": queries.object_revision(window, obj)}


def scene_entry():
    return {"handle": scene_handle(window.scene), "revision": settings_revision(window.scene)}


def settled():
    bpy.context.view_layer.update()
    queries.invalidate()


def new_scene(label):
    scene = bpy.data.scenes.new("Review3 " + label)
    window.scene = scene
    cam = bpy.data.objects.new("Cam " + label, bpy.data.cameras.new("Cam " + label))
    cam.location = (0, -9, 3)
    scene.collection.objects.link(cam)
    scene.camera = cam
    return scene, cam


def empty(scene, name, moving):
    obj = bpy.data.objects.new(name, None)
    scene.collection.objects.link(obj)
    if moving:
        for frame, x in ((1, 0.0), (21, 4.0)):
            obj.location.x = x
            obj.keyframe_insert("location", index=0, frame=frame)
    return obj


def host(scene, name):
    import bmesh
    data = bpy.data.meshes.new(name)
    shape = bmesh.new()
    bmesh.ops.create_grid(shape, x_segments=3, y_segments=3, size=1.0)
    shape.to_mesh(data)
    shape.free()
    obj = bpy.data.objects.new(name, data)
    scene.collection.objects.link(obj)
    return obj


def run(operations, writes=()):
    try:
        arguments = validate_call(NAME, {"mode": "APPLY", "read_set": [], "write_set": list(writes),
                                         "operations": operations, "expected_checks": []})
        approved = native.precondition(window, NAME, arguments)
    except ValueError as error:
        return {"status": "refused before approval", "error": str(error)}, None
    job = native.begin(window, NAME, arguments, approved_obligations=approved.obligations)
    for _ in range(4096):
        staged = job.poll()
        if staged.get("final") or staged.get("ready_to_commit"):
            break
    return staged, job


def committed(operations, writes=()):
    staged, job = run(operations, writes)
    if job is not None and staged.get("ready_to_commit"):
        bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
        staged = job.result or staged
    return {"status": staged.get("status"), "error": str(staged.get("error") or "")}


def orbit_beside(mover, build_label):
    light = {"kind": "light_create", "target": None, "parameters": {"name": "QA Rim", "look_at": [0, 0, 0],
                                                                      "orbit": dict(ORBIT)}}
    try:
        arguments = validate_call(NAME, {"mode": "APPLY", "read_set": [], "write_set": [entry(mover)],
                                         "operations": [op("transform", mover, location=[3, 1, 0]), light]})
        resolved = program_camera.host_program(window, arguments)
    except ValueError as error:
        return str(error), None
    return "", resolved


def follows():
    scene, cam = new_scene("driver")
    ctrl = empty(scene, "Ctrl", False)
    curve = cam.driver_add("location", 0)
    variable = curve.driver.variables.new()
    variable.type = "TRANSFORMS"
    variable.targets[0].id, variable.targets[0].transform_type = ctrl, "LOC_X"
    curve.driver.expression = "var"
    settled()
    refused, _ = orbit_beside(ctrl, "driver")
    report["cases"]["orbit_beside_a_driver_target_move"] = refused
    check("an_orbit_beside_a_move_of_what_a_camera_driver_reads_is_refused",
          "moves an object the scene camera follows" in refused)
    scene, cam = new_scene("aim")
    subject = empty(scene, "Subject", False)
    cam.constraints.new("TRACK_TO").target = subject
    settled()
    before = [round(value, 4) for value in cam.matrix_world.translation]
    refused, resolved = orbit_beside(subject, "aim")
    location = resolved["operations"][1]["parameters"]["location"] if resolved else None
    from angelo_agent.action_camera_light_spec import orbit_location
    report["cases"]["orbit_beside_an_aim_move"] = {"refused": refused, "location": location}
    check("an_orbit_beside_a_move_of_the_cameras_aim_is_measured_from_its_unchanged_position",
          not refused and location == orbit_location(before, [0, 0, 0], ORBIT))


def materials():
    scene, cam = new_scene("materials")
    red = bpy.data.materials.new("QA Red")
    red.use_nodes = True
    red.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (.8, .1, .1, 1)
    box = host(scene, "QA Box")
    box.data.materials.append(bpy.data.materials.new("QA Blue"))
    box.data.materials.append(red)
    settled()
    def colour():  # The material the box's second slot holds now (an edit for all users commits its copy).
        material = box.material_slots[1].material
        node = material.node_tree.nodes.get("Principled BSDF") if material and material.node_tree else None
        return [round(value, 3) for value in node.inputs["Base Color"].default_value] if node else None
    outcome = committed([op("material_principled", box, name="QA Red", face_selector={"normal": [0, 0, 1],
                                                                                     "normal_angle": .1})], [entry(box)])
    report["cases"]["name_only_face_selector"] = dict(outcome, colour=colour(),
                                                      faces=sorted({p.material_index for p in box.data.polygons}))
    check("a_name_only_selector_keeps_the_material_and_assigns_its_faces", outcome["status"] == "committed"
          and colour() == [.8, .1, .1, 1.0] and {p.material_index for p in box.data.polygons} == {1})
    settled()
    outcome = committed([op("material_principled", box, name="QA Red", shared="ALL_USERS",
                            inputs={"Base Color": [.8, .8, .8, 1]})], [entry(box)])
    report["cases"]["stated_default_colour"] = dict(outcome, colour=colour())
    check("a_stated_default_colour_is_applied", outcome["status"] == "committed" and colour() == [.8, .8, .8, 1.0])


def shared_graph_and_paths():
    scene, cam = new_scene("shared graph")
    tree = bpy.data.node_groups.new("QA Big", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    inlet, outlet = tree.nodes.new("NodeGroupInput"), tree.nodes.new("NodeGroupOutput")
    tree.links.new(inlet.outputs[0], outlet.inputs[0])
    for _ in range(298):
        tree.nodes.new("ShaderNodeMath")
    box = host(scene, "QA Shared")
    for index in range(2):
        box.modifiers.new("GN %d" % index, "NODES").node_group = tree
    check("a_group_two_modifiers_share_leaves_the_object_frame_independent", program_frames.frame_independent(box))
    arm = bpy.data.objects.new("QA Rig", bpy.data.armatures.new("QA Rig"))
    scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    bone = arm.data.edit_bones.new("Bone")
    bone.head, bone.tail = (0, 0, 0), (0, 0, 1)
    bpy.ops.object.mode_set(mode="OBJECT")
    pose = arm.pose.bones["Bone"]
    pose.rotation_mode = "QUATERNION"
    for frame, w in ((1, 1.0), (21, .7)):
        pose.rotation_quaternion[0] = w
        pose.keyframe_insert("rotation_quaternion", index=0, frame=frame)
    pose.rotation_mode = "XYZ"
    lens = bpy.data.objects.new("QA Lens", bpy.data.cameras.new("QA Lens"))
    scene.collection.objects.link(lens)
    for frame, value in ((1, 35.0), (21, 85.0)):
        lens.data.lens = value
        lens.data.keyframe_insert("lens", frame=frame)
    scene.frame_set(1)
    settled()
    move = {"kind": "property_set", "target": scene_handle(scene), "parameters": {"values": {"frame_current": 11}}}
    outcome = committed([op("bone_set", arm, bone="Bone", rotation_mode="QUATERNION"), move], [entry(arm), scene_entry()])
    report["cases"]["rotation_mode_beside_a_frame_move"] = outcome
    check("a_rotation_mode_change_onto_a_keyed_channel_is_refused_before_approval",
          outcome["status"] == "refused before approval" and "rotation_quaternion" in outcome["error"])
    outcome = committed([op("camera_configure", lens, settings={"clip_end": 500.0})], [entry(lens)])
    report["cases"]["clip_end_beside_a_keyed_lens"] = outcome
    check("a_camera_setting_no_channel_holds_commits_beside_a_keyed_lens",
          outcome["status"] == "committed" and abs(lens.data.clip_end - 500.0) < 1e-3)


def poll_resume():
    scene, cam = new_scene("poll resume")
    box = host(scene, "QA Moved")
    settled()
    staged, job = None, None
    arguments = validate_call(NAME, {"mode": "APPLY", "read_set": [], "write_set": [entry(box)],
                                     "operations": [op("transform", box, location=[1, 2, 3]),
                                                    op("transform", box, scale=[2, 2, 2])]})
    approved = native.precondition(window, NAME, arguments)
    job = native.begin(window, NAME, arguments, approved_obligations=approved.obligations)

    def fail(stage):
        raise RefusalError("Node metadata refers outside the staged IDs", code="INTERNAL_ERROR")
    job.backend.fuse_phases = False  # One phase a poll, so the next poll resumes the suspended stage.
    first = job.poll()
    job.backend.resume_stage = fail
    result = job.poll()
    report["cases"]["poll_resume_first"] = first.get("status")
    for _ in range(64):
        if result.get("final"):
            break
        result = job.poll()
    report["cases"]["poll_resume_failure"] = {key: result.get(key) for key in (
        "status", "error", "scene_changed", "partial_changes_possible", "rollback_verified")}
    check("a_failure_resuming_a_stage_while_polling_reports_the_verified_unchanged_scene",
          result.get("status") == "failed" and result.get("partial_changes_possible") is False
          and result.get("rollback_verified") is True and tuple(box.location) == (0, 0, 0) and no_stages())


def light_orbit():
    return {"kind": "light_create", "target": None, "parameters": {"name": "QA Rim", "look_at": [0, 0, 0],
                                                                    "orbit": dict(ORBIT)}}


def resolved(operations, writes):
    try:
        arguments = validate_call(NAME, {"mode": "APPLY", "read_set": [], "write_set": writes, "operations": operations})
        return "", program_camera.host_program(window, arguments)
    except ValueError as error:
        return str(error), None


def fifth_review():
    """What a driver reads beyond its target, instanced collections, Euler orders, scene settings."""
    scene, cam = new_scene("material tree")
    holder = host(scene, "QA Holder")
    material = bpy.data.materials.new("QA Driven")
    material.use_nodes = True
    holder.data.materials.append(material)
    socket = material.node_tree.nodes["Principled BSDF"].inputs["Roughness"]
    for frame, value in ((1, 0.0), (21, 1.0)):
        socket.default_value = value
        socket.keyframe_insert("default_value", frame=frame)
    driver = cam.driver_add("location", 0).driver
    variable = driver.variables.new()
    variable.type = "SINGLE_PROP"
    variable.targets[0].id_type, variable.targets[0].id = "MATERIAL", material
    variable.targets[0].data_path = 'node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value'
    driver.expression = "var * 4"
    scene.frame_set(1)
    settled()
    move = {"kind": "property_set", "target": scene_handle(scene), "parameters": {"values": {"frame_current": 21}}}
    refused, _ = resolved([light_orbit(), move], [scene_entry()])
    report["cases"]["driver_reads_a_keyed_embedded_tree"] = refused
    check("a_driver_reading_keys_in_a_material_tree_moves_the_camera_with_time", "moves with time" in refused)
    scene, cam = new_scene("instancer")
    member = host(scene, "QA Member")
    collection = bpy.data.collections.new("QA Instanced")
    collection.objects.link(member)
    instancer = bpy.data.objects.new("QA Instancer", None)
    scene.collection.objects.link(instancer)
    instancer.instance_type, instancer.instance_collection = "COLLECTION", collection
    tree = bpy.data.node_groups.new("QA Instances", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nodes, links = tree.nodes, tree.links
    inlet, outlet, info = nodes.new("NodeGroupInput"), nodes.new("NodeGroupOutput"), nodes.new("GeometryNodeObjectInfo")
    info.inputs["Object"].default_value, info.inputs["As Instance"].default_value = instancer, True
    realize, bounds, move_to = (nodes.new("GeometryNodeRealizeInstances"), nodes.new("GeometryNodeBoundBox"),
                                nodes.new("GeometryNodeSetPosition"))
    links.new(inlet.outputs[0], move_to.inputs["Geometry"])
    links.new(info.outputs["Geometry"], realize.inputs[0])
    links.new(realize.outputs[0], bounds.inputs[0])
    links.new(bounds.outputs["Min"], move_to.inputs["Offset"])
    links.new(move_to.outputs[0], outlet.inputs[0])
    carrier = host(scene, "QA Carrier")
    carrier.modifiers.new("GN", "NODES").node_group = tree
    cam.parent, cam.parent_type, cam.parent_vertices = carrier, "VERTEX", (0, 0, 0)
    settled()
    refused, _ = resolved([op("transform", member, location=[2, 1, .5]), light_orbit()], [entry(member)])
    report["cases"]["instanced_collection_member_moved"] = refused
    check("an_orbit_beside_a_move_of_an_instanced_collection_member_is_refused",
          "moves an object the scene camera follows" in refused)
    scene, cam = new_scene("scene setting")
    scene["dolly"] = 2.0
    driver = cam.driver_add("location", 0).driver
    variable = driver.variables.new()
    variable.type = "SINGLE_PROP"
    variable.targets[0].id_type, variable.targets[0].id, variable.targets[0].data_path = "SCENE", scene, '["dolly"]'
    driver.expression = "var"
    scene.world = bpy.data.worlds.new("QA World")
    settled()
    world = {"kind": "world_configure", "target": scene_handle(scene), "parameters": {"strength": 3.0}}
    refused, program = resolved([world, light_orbit()], [scene_entry()])
    report["cases"]["unrelated_scene_setting_beside_an_orbit"] = refused
    check("a_scene_setting_no_camera_driver_reads_leaves_the_orbit_admitted", not refused and program is not None)
    dolly = {"kind": "property_set", "target": scene_handle(scene), "parameters": {"values": {'["dolly"]': 3.0}}}
    refused, _ = resolved([dolly, light_orbit()], [scene_entry()])
    report["cases"]["read_scene_setting_beside_an_orbit"] = refused
    check("the_scene_setting_a_camera_driver_reads_refuses_the_orbit",
          "edits scene settings a driver of the scene camera reads" in refused or "CUSTOM_PROPERTY" in refused)


def rigged(scene, label):
    arm = bpy.data.objects.new("QA %s Rig" % label, bpy.data.armatures.new("QA %s Rig" % label))
    scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    bone = arm.data.edit_bones.new("Bone")
    bone.head, bone.tail = (0, 0, 0), (0, 0, 1)
    bpy.ops.object.mode_set(mode="OBJECT")
    pose = arm.pose.bones["Bone"]
    pose.rotation_mode = "XYZ"
    for frame, z in ((1, 0.0), (21, 1.0)):
        pose.rotation_euler[2] = z
        pose.keyframe_insert("rotation_euler", index=2, frame=frame)
    return arm, pose


def bone_channels():
    """Fifth review, finding 7: a pose write is refused only for the channels it touches that are keyed or driven.

    Blender's own truth first: after a frame change an unkeyed channel keeps a value set by hand, and a keyed one
    is overridden, so writing a keyed channel is refused and writing an unkeyed one commits and holds.
    """
    scene, cam = new_scene("bone truth")
    arm, pose = rigged(scene, "Truth")
    scene.frame_set(1)
    pose.location = (1, 2, 3)
    pose.rotation_euler[2] = 5.0
    scene.frame_set(11)
    truth = {"unkeyed_kept": tuple(round(v, 3) for v in pose.location) == (1, 2, 3),
             "keyed_overridden": round(pose.rotation_euler[2], 3) != 5.0}
    report["cases"]["blender_truth"] = truth
    check("blender_keeps_an_unkeyed_channel_and_overrides_a_keyed_one", all(truth.values()))
    # A driver on one channel: programs never bind a driven object (no exact revision, scene_fingerprints), so the
    # kernel's rule is read directly, beside Blender's truth for that channel and an undriven one.
    scene, cam = new_scene("bone driver")
    arm, pose = rigged(scene, "Driven")
    arm.animation_data_clear()
    source = bpy.data.objects.new("QA Driver Source", None)
    scene.collection.objects.link(source)
    source.location.x = .25
    driver = pose.driver_add("location", 0).driver
    driver.type = "AVERAGE"
    variable = driver.variables.new()
    variable.type = "TRANSFORMS"
    variable.targets[0].id, variable.targets[0].transform_type = source, "LOC_X"
    from angelo_agent import bone_state
    from angelo_agent.action_bones_spec import pose_paths
    scene.frame_set(1)
    pose.location, pose.scale = (3, 3, 3), (2, 2, 2)
    scene.frame_set(11)
    truth = {"driven_overridden": round(pose.location[0], 3) == .25, "undriven_kept": tuple(pose.scale) == (2, 2, 2),
             "location_refused": bone_state.animated(arm, "Bone", pose_paths({"rotation_mode": None, "location": [1, 2, 3],
                 "rotation": None, "scale": None, "pose_settings": {}}, pose.rotation_mode)) == "location",
             "scale_admitted": bone_state.animated(arm, "Bone", pose_paths({"rotation_mode": None, "location": None,
                 "rotation": None, "scale": [2, 2, 2], "pose_settings": {}}, pose.rotation_mode)) is None}
    report["cases"]["bone_driver"] = truth
    check("a_driven_channel_is_refused_and_an_undriven_one_admitted", all(truth.values()))
    rigs = {}
    for label in ("action", "nla"):
        scene, cam = new_scene("bone " + label)
        arm, pose = rigged(scene, label.title())
        if label == "nla":  # The keys pushed down into an NLA strip: no active Action.
            animation = arm.animation_data
            track = animation.nla_tracks.new()
            track.strips.new("Turn", 1, animation.action)
            animation.action = None
        scene.frame_set(1)
        settled()
        rigs[label] = (scene, arm)
        move = {"kind": "property_set", "target": scene_handle(scene), "parameters": {"values": {"frame_current": 11}}}
        free, held = ("location", [1, 2, 3]), ("rotation", [0, 0, .5])
        outcome = committed([op("bone_set", arm, bone="Bone", **{free[0]: free[1]}), move], [entry(arm), scene_entry()])
        scene.frame_set(scene.frame_current)
        bone = arm.pose.bones["Bone"]
        kept = tuple(round(v, 3) for v in getattr(bone, free[0])) == tuple(float(v) for v in free[1])
        report["cases"]["bone_%s_unkeyed" % label] = dict(outcome, kept=kept)
        check("an_unkeyed_channel_of_a_bone_with_%s_commits_and_holds" % label,
              outcome["status"] == "committed" and kept and scene.frame_current == 11)
        settled()
        outcome = committed([op("bone_set", arm, bone="Bone", **{held[0]: held[1]}), dict(move, target=scene_handle(
            scene))], [entry(arm), scene_entry()])
        report["cases"]["bone_%s_keyed" % label] = outcome
        check("a_keyed_channel_of_a_bone_with_%s_is_refused_before_approval" % label,
              outcome["status"] == "refused before approval" and "FRAME_ANIMATED" in outcome["error"])
        outcome = committed([op("bone_set", arm, bone="Bone", **{held[0]: held[1]})], [entry(arm)])
        report["cases"]["bone_%s_keyed_no_frame_move" % label] = outcome
        check("a_keyed_channel_of_a_bone_with_%s_is_refused_at_staging" % label,
              outcome["status"] == "failed" and "BONE_ANIMATED" in outcome["error"])
    scene, cam = new_scene("euler order")
    arm, pose = rigged(scene, "Order")
    scene.frame_set(1)
    settled()
    move = {"kind": "property_set", "target": scene_handle(scene), "parameters": {"values": {"frame_current": 11}}}
    outcome = committed([op("bone_set", arm, bone="Bone", rotation_mode="ZXY"), move], [entry(arm), scene_entry()])
    scene.frame_set(scene.frame_current)
    report["cases"]["euler_order_beside_a_frame_move"] = dict(outcome, mode=arm.pose.bones["Bone"].rotation_mode)
    check("an_euler_order_change_on_a_bone_with_keyed_rotation_commits_and_holds", outcome["status"] == "committed"
          and arm.pose.bones["Bone"].rotation_mode == "ZXY")


def single_prop(owner, index, kind, target, path, expression="var"):
    driver = owner.driver_add("location", index).driver
    driver.type = "SCRIPTED"
    variable = driver.variables.new()
    variable.name, variable.type = "var", "SINGLE_PROP"
    variable.targets[0].id_type, variable.targets[0].id, variable.targets[0].data_path = kind, target, path
    driver.expression = expression


def sixth_review():
    """Every ID a driver path crosses, context scenes, self reads that are not rotations, light linking, families."""
    scene, cam = new_scene("path hop")
    holder, victim = host(scene, "QA Holder Hop"), host(scene, "QA Victim Hop")
    shared = bpy.data.materials.new("QA Shared Hop")
    shared.use_nodes = True
    holder.data.materials.append(shared)
    victim.data.materials.append(shared)
    single_prop(cam, 0, "OBJECT", holder,
                'active_material.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value', "var * 4")
    settled()
    write = op("material_principled", victim, name="QA Shared Hop", inputs={"Roughness": 1.0}, shared="ALL_USERS")
    refused, _ = resolved([write, light_orbit()], [entry(victim)])
    report["cases"]["path_hop_shared_material"] = refused
    check("an_orbit_beside_an_edit_of_a_material_a_driver_reads_through_another_object_is_refused",
          "moves an object the scene camera follows" in refused)
    scene, cam = new_scene("context")
    scene.render.resolution_x = 1000
    driver = cam.driver_add("location", 0).driver
    driver.type = "SCRIPTED"
    variable = driver.variables.new()
    variable.name, variable.type = "var", "CONTEXT_PROP"
    variable.targets[0].context_property, variable.targets[0].data_path = "ACTIVE_SCENE", "render.resolution_x"
    driver.expression = "var / 500"
    settled()
    resolution = {"kind": "property_set", "target": scene_handle(scene), "parameters": {"values": {
        "render.resolution_x": 3000}}}
    refused, _ = resolved([resolution, light_orbit()], [scene_entry()])
    report["cases"]["context_scene_setting"] = refused
    check("an_orbit_beside_an_edit_of_a_scene_setting_a_context_variable_reads_is_refused",
          "edits scene settings a driver of the scene camera reads" in refused)
    scene, cam = new_scene("dolly")
    target = host(scene, "QA Dolly Target")
    cam.constraints.new("TRACK_TO").target = target
    single_prop(cam, 1, "OBJECT", cam, "data.lens", "-var / 5")
    settled()
    refused, _ = resolved([op("transform", target, location=[5, 1, 0]), light_orbit()], [entry(target)])
    report["cases"]["dolly_zoom_aim_moved"] = refused
    check("a_dolly_zoom_reading_its_own_lens_keeps_its_aim_unfollowed", not refused)
    scene, cam = new_scene("world read")
    world = bpy.data.worlds.new("QA World Read")
    world.use_nodes = True
    scene.world = world
    single_prop(cam, 0, "WORLD", world, 'node_tree.nodes["Background"].inputs[1].default_value')
    settled()
    resolution = {"kind": "property_set", "target": scene_handle(scene), "parameters": {"values": {
        "render.resolution_x": 1234}}}
    refused, _ = resolved([resolution, light_orbit()], [scene_entry()])
    report["cases"]["world_driver_unrelated_setting"] = refused
    check("an_unrelated_scene_setting_beside_a_world_driver_leaves_the_orbit_admitted", not refused)
    scene, cam = new_scene("light linking")
    mover = host(scene, "QA Linked Mover")
    for frame, x in ((1, 0.0), (21, 5.0)):
        mover.location.x = x
        mover.keyframe_insert("location", index=0, frame=frame)
    receivers = bpy.data.collections.new("QA Receivers")
    receivers.objects.link(mover)
    lamp = bpy.data.objects.new("QA Linked Lamp", bpy.data.lights.new("QA Linked Lamp", "POINT"))
    scene.collection.objects.link(lamp)
    lamp.light_linking.receiver_collection = receivers
    check("light_linking_leaves_a_light_frame_independent", program_frames.frame_independent(lamp))
    scene, cam = new_scene("family")
    arm, pose = rigged(scene, "Family")
    scene.frame_set(1)
    settled()
    outcome = committed([op("bone_set", arm, bone="Bone", rotation_mode="QUATERNION")], [entry(arm)])
    report["cases"]["family_switch_on_keyed_euler"] = outcome
    check("a_rotation_family_switch_that_would_stop_keyed_rotation_is_refused",
          outcome["status"] == "failed" and "BONE_ANIMATED" in outcome["error"])


try:
    bpy.data.batch_remove(ids=list(bpy.data.objects) + list(bpy.data.meshes) + list(bpy.data.materials))
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    follows()
    materials()
    shared_graph_and_paths()
    poll_resume()
    fifth_review()
    bone_channels()
    sixth_review()
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
report.pop("last_commit", None)
(OUT / "typed-review4.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_TYPED_REVIEW4", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
