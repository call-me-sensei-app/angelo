# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Dry run of the live-eval "coverage" task set's grading, with no model and no billing.

Builds each coverage task's end state with plain ``bpy`` and grades it with the
trial driver's own helpers (``grading_targets``, ``bind_checks``) extracted by
``bench_mcp_grading``, as both benchmark sides do: every task must fail before
its edit and pass after it. Tasks on harness-made fixtures (strokes, hair
curves) get them from ``eval_live_tasks.prepare_fixture`` first. Run only in a disposable instance with the exact
Angelo binary: ``--background --factory-startup --disable-autoexec --python-exit-code 1``.
"""

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-coverage-tasks-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(HERE))
import bench_mcp_grading as grading  # noqa: E402
import eval_live_tasks as live  # noqa: E402
from angelo_agent.scene_validation import VerificationContext, verify  # noqa: E402

namespace = {"live": live, "copy": __import__("copy"), "bpy": bpy, "VerificationContext": VerificationContext,
             "verify": verify}
exec(grading.session_helpers_source(), namespace)  # The trial driver's helpers, verbatim.
report = {"checks": {}, "paid_requests": 0, "tasks": {}}
scene = bpy.context.window_manager.windows[0].scene


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def grade(task):
    obj = scene.objects.get(live.EVAL_OBJECT)
    result = verify(VerificationContext(namespace["grading_targets"](scene, obj), list(scene.objects)),
                    namespace["bind_checks"](task["checks"], obj))
    report["tasks"].setdefault(task["id"], []).append(result["status"])
    return result["success"]


def build(task_id):
    if task_id == "render_settings":
        scene.render.resolution_x, scene.render.resolution_y = 1280, 720
        scene.render.resolution_percentage, scene.render.film_transparent = 50, True
    elif task_id == "world_background":
        background = scene.world.node_tree.nodes["Background"]
        background.inputs["Color"].default_value = (0.02, 0.03, 0.1, 1)
        background.inputs["Strength"].default_value = 0.5
    elif task_id == "frame_range":
        scene.render.fps, scene.frame_start, scene.frame_end = 30, 1, 120
    elif task_id == "duplicate_and_collect":
        original = scene.objects[live.EVAL_OBJECT]
        duplicate = original.copy()
        duplicate.data = original.data.copy()
        duplicate.name, duplicate.location = live.COPY_OBJECT, (3, 0, 0)
        props = bpy.data.collections.new("Eval Props")
        scene.collection.children.link(props)
        for obj in (original, duplicate):
            for collection in list(obj.users_collection):
                collection.objects.unlink(obj)
            props.objects.link(obj)
    elif task_id == "render_visibility":
        scene.objects[live.COPY_OBJECT].hide_render = True
    elif task_id == "rig_pose":
        data = bpy.data.armatures.new(live.RIG_OBJECT)
        rig = bpy.data.objects.new(live.RIG_OBJECT, data)
        scene.collection.objects.link(rig)
        bpy.context.view_layer.objects.active = rig
        bpy.ops.object.mode_set(mode="EDIT")
        upper, lower = data.edit_bones.new("Upper"), data.edit_bones.new("Lower")
        upper.head, upper.tail, lower.head, lower.tail = (0, 0, 0), (0, 0, 1), (0, 0, 1), (0, 0, 2)
        lower.parent, lower.use_connect = upper, True
        bpy.ops.object.mode_set(mode="OBJECT")
        rig.pose.bones["Lower"].rotation_quaternion = (0.9689124, 0.247404, 0, 0)  # 0.5 rad about X.
        data.bones["Upper"].use_deform = False
    elif task_id == "particle_hair":
        scene.objects[live.EVAL_OBJECT].modifiers.new("Eval Hair", "PARTICLE_SYSTEM")
        settings = scene.objects[live.EVAL_OBJECT].particle_systems["Eval Hair"].settings
        settings.type, settings.count, settings.hair_length = "HAIR", 300, 0.2
    elif task_id == "scatter_points":
        tree = bpy.data.node_groups.new("Eval Scatter", "GeometryNodeTree")
        tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        group_in, group_out = tree.nodes.new("NodeGroupInput"), tree.nodes.new("NodeGroupOutput")
        scatter = tree.nodes.new("GeometryNodeDistributePointsOnFaces")
        scatter.inputs["Density"].default_value = 5
        tree.links.new(group_in.outputs["Geometry"], scatter.inputs["Mesh"])
        tree.links.new(scatter.outputs["Points"], group_out.inputs["Geometry"])
        scene.objects[live.COPY_OBJECT].modifiers.new("Eval Scatter", "NODES").node_group = tree
    elif task_id == "scatter_edit":
        tree = scene.objects[live.COPY_OBJECT].modifiers["Eval Scatter"].node_group
        scatter, output = tree.nodes["Distribute Points on Faces"], tree.nodes["Group Output"]
        scatter.inputs["Density"].default_value = 2
        lift = tree.nodes.new("GeometryNodeSetPosition")
        lift.inputs["Offset"].default_value = (0, 0, 0.1)
        tree.links.new(scatter.outputs["Points"], lift.inputs["Geometry"])
        tree.links.new(lift.outputs["Geometry"], output.inputs[0])
    elif task_id == "world_nodes":
        tree = scene.world.node_tree
        gain = tree.nodes.new("ShaderNodeValue")
        gain.name, gain.outputs[0].default_value = "Eval Gain", 1.5
        tree.links.new(gain.outputs[0], tree.nodes["Background"].inputs["Strength"])
    elif task_id == "procedural_material":
        paint = bpy.data.materials.new("Eval Paint")
        noise = paint.node_tree.nodes.new("ShaderNodeTexNoise")
        paint.node_tree.links.new(noise.outputs["Color"], paint.node_tree.nodes["Principled BSDF"].inputs["Base Color"])
        mesh = scene.objects[live.EVAL_OBJECT].data
        mesh.materials.append(paint)
        for face in mesh.polygons:
            face.material_index = len(mesh.materials) - 1
    elif task_id == "material_node_edit":
        tree = bpy.data.materials["Eval Paint"].node_tree
        noise, bsdf = tree.nodes["Noise Texture"], tree.nodes["Principled BSDF"]
        ramp, frame = tree.nodes.new("ShaderNodeValToRGB"), tree.nodes.new("NodeFrame")
        frame.name = "Pattern"
        tree.links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
        tree.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
        noise.parent = ramp.parent = frame
    elif task_id == "grease_pencil_sketch":
        sketch = bpy.data.objects.new(live.SKETCH_OBJECT, bpy.data.grease_pencils.new(live.SKETCH_OBJECT))
        sketch.data.layers.new("Layer")
        sketch.data.layers.new("Ink")
        scene.collection.objects.link(sketch)
    elif task_id in ANIMATION_BUILDS:
        ANIMATION_BUILDS[task_id](scene.objects[live.EVAL_OBJECT], scene.objects.get(live.COPY_OBJECT))
    elif task_id == "edit_mode_transform":
        for vertex in scene.objects[live.EVAL_OBJECT].data.vertices:
            if vertex.co.z > 0:
                vertex.co.z += 0.5
    elif task_id == "grease_pencil_strokes":
        drawing = scene.objects[live.STROKES_OBJECT].data.layers["Ink"].frames[0].drawing
        drawing.remove_strokes()
        drawing.add_strokes([7, 7])
    elif task_id == "hair_curves":
        hair = scene.objects[live.HAIR_OBJECT].data
        hair.remove_curves()
        hair.add_curves([7, 7, 7])
    elif task_id == "two_line_text":
        text = bpy.data.curves.new(live.TEXT_OBJECT, "FONT")
        text.body = "Angelo\nStudio"
        scene.collection.objects.link(bpy.data.objects.new(live.TEXT_OBJECT, text))
    elif task_id == "constraint_apply":
        scene.objects[live.COPY_OBJECT].location = (1.5, 0, 0)
    elif task_id == "triangulate_mesh":
        import bmesh
        mesh = scene.objects[live.EVAL_OBJECT].data
        geometry = bmesh.new()
        geometry.from_mesh(mesh)
        bmesh.ops.triangulate(geometry, faces=geometry.faces[:])
        geometry.to_mesh(mesh)
        geometry.free()
    bpy.context.view_layer.update()


def _curves(obj):
    return [curve for layer in obj.animation_data.action.layers for strip in layer.strips
            for bag in strip.channelbags for curve in bag.fcurves]


def _key_linear(cube, _copy):
    for frame, x in ((1, 0), (30, 4)):
        cube.location = (x, 0, 0)
        cube.keyframe_insert("location", frame=frame)
    for curve in _curves(cube):
        curve.extrapolation = "LINEAR"
        for point in curve.keyframe_points:
            point.interpolation = "LINEAR"


def _key_retime(cube, _copy):
    for curve in _curves(cube):
        for point in curve.keyframe_points:
            for vector in (point.co, point.handle_left, point.handle_right):
                vector.x += 10


def _key_noise(cube, _copy):
    curve = next(curve for curve in _curves(cube) if curve.data_path == "location" and curve.array_index == 0)
    noise = curve.modifiers.new("NOISE")
    noise.name, noise.strength = "Eval Shake", 0.5


def _copy_keys(_cube, copy):
    for frame in (16, 45):
        copy.keyframe_insert("location", index=0, frame=frame)


def _stash_action(_cube, copy):
    animation = copy.animation_data
    track = animation.nla_tracks.new()
    track.name, track.mute = "[Action Stash]", True
    track.strips.new(animation.action.name, 16, animation.action)
    animation.action = bpy.data.actions.new("Eval Idle")


def _nla_strip(cube, copy):
    track = copy.animation_data.nla_tracks.new()
    track.name = "Eval Layer"
    strip = track.strips.new("Eval Walk", 50, cube.animation_data.action)
    strip.repeat, strip.blend_type = 2, "ADD"


def _timeline_markers(_cube, _copy):
    scene.timeline_markers.new("Eval Start", frame=1)
    scene.timeline_markers.new("Eval Hit", frame=24)


ANIMATION_BUILDS = {"key_linear": _key_linear, "key_retime": _key_retime, "key_noise": _key_noise,
                    "copy_keys": _copy_keys, "stash_action": _stash_action, "nla_strip": _nla_strip,
                    "timeline_markers": _timeline_markers}


def run_checks():
    bpy.ops.mesh.primitive_cube_add(size=2)
    bpy.context.object.name = live.EVAL_OBJECT
    tasks = [task for task in live.tasks_for("coverage") if task["kind"] == "edit" and task["id"] != "cube_creation"]
    for task in tasks:
        if task.get("fixture"):  # The harness-made object the task edits, as both trial drivers make it.
            check(task["id"] + "_fixture_made", live.prepare_fixture(bpy, scene, task) == ["fixture:" + task["fixture"]])
        check(task["id"] + "_fails_before_its_edit", not grade(task))
        build(task["id"])
        check(task["id"] + "_passes_after_its_edit", grade(task))
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
(OUT / "coverage-tasks.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_COVERAGE_TASKS", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
