# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The engine preflight in Blender (doc/angelo/engine_preflight.rst): the facts it reads, each typed fix applied
through ``angelo_engine_fix``'s host route (the finding gone afterwards, one Undo step restoring all of them), the
stale-fix refusal, and the re-import comparison of real .glb and .fbx exports: what it finds (units, leaf bones,
dropped influences, colour spaces, local paths) and that nothing it read stays in the file, the selection, the
active object, the active collection or the frame. Background, local only; writes results.json under ANGELO_QA_DIR.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

try:
    from angelo_agent import engine_check_host, engine_preflight, program_undo
except ImportError:   # A stock Blender run from the checkout.
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
    from angelo_agent import engine_check_host, engine_preflight, program_undo
from angelo_agent.refusal_codes import RefusalError

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-engine-preflight-qa"))
OUT.mkdir(parents=True, exist_ok=True)
checks, details = {}, {}
HISTORY = program_undo.keep_background_document()   # Background Blender keeps an Undo history only with this.


def case(function):
    try:
        checks[function.__name__] = bool(function())
    except Exception:   # noqa: BLE001 - a crashed case is a failed case, with its traceback in the report.
        checks[function.__name__] = False
        details[function.__name__] = traceback.format_exc()[-1500:]
    return function


def fresh():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    program_undo.keep_background_document()
    bpy.ops.ed.undo_push(message="Original")


def cube(name, location=(0, 0, 0), size=1.0):
    mesh = bpy.data.meshes.new(name)
    half = size / 2
    mesh.from_pydata([(x * half, y * half, z * half) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], [],
                     [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)])
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = location
    return obj


def rig(name="Rig", bones=(("Root", None, (0, 0, 0), (0, 0, 1)),)):
    data = bpy.data.armatures.new(name)
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    made = {}
    for bone, parent, head, tail in bones:
        edit = data.edit_bones.new(bone)
        edit.head, edit.tail = head, tail
        if parent:
            edit.parent = made[parent]
        made[bone] = edit
    bpy.ops.object.mode_set(mode="OBJECT")
    return obj


def skin(obj, armature, weights):
    """Bind ``obj`` to ``armature`` with ``weights`` [(bone, weight)] on every vertex."""
    obj.parent = armature
    modifier = obj.modifiers.new("Armature", "ARMATURE")
    modifier.object = armature
    for bone, weight in weights:
        group = obj.vertex_groups.get(bone) or obj.vertex_groups.new(name=bone)
        group.add(range(len(obj.data.vertices)), weight, "REPLACE")


def check(target, fmt="AUTO"):
    return engine_preflight.check(None, target, fmt)


def has(report, code, subject=None):
    return any(item["code"] == code and (subject is None or item["subject"] == subject)
               for item in report["findings"])


def fix(target, *items, fmt="AUTO"):
    bpy.ops.ed.undo_push(message="Built")   # The case's own edits, as a person's would be: Undo stops here.
    result = engine_check_host.execute(None, None, "angelo_engine_fix", {
        "target": target, "format": fmt, "fixes": [{"code": code, "subject": subject} for code, subject in items]})
    details.setdefault("fixes", []).append({key: result[key] for key in ("success", "applied", "failed",
                                                                          "undo_steps")})
    return result


def undo():
    return "FINISHED" in bpy.ops.ed.undo()


@case
def armature_transform_is_applied_with_its_skin_and_undone_in_one_step():
    fresh()
    armature = rig()
    body = cube("Body")
    skin(body, armature, [("Root", 1.0)])
    armature.scale = (100.0, 100.0, 100.0)
    armature.rotation_euler = (1.5707963, 0.0, 0.0)
    bpy.context.view_layer.update()
    world = [tuple(round(v, 3) for v in body.matrix_world @ vertex.co) for vertex in body.data.vertices]
    found = has(check("UNITY"), "transform_unapplied", "Rig")
    result = fix("UNITY", ("transform_unapplied", "Rig"))
    bpy.context.view_layer.update()
    after = [tuple(round(v, 3) for v in body.matrix_world @ vertex.co) for vertex in body.data.vertices]
    gone = not has(check("UNITY"), "transform_unapplied", "Rig")
    applied = tuple(round(v, 4) for v in bpy.data.objects["Rig"].scale) == (1.0, 1.0, 1.0)
    details["transform"] = {"result": {key: result[key] for key in ("success", "undo_steps")}}
    restored = undo() and tuple(round(v, 2) for v in bpy.data.objects["Rig"].scale) == (100.0, 100.0, 100.0)
    return found and result["success"] and result["undo_steps"] == 1 and gone and applied and world == after and (
        restored or not HISTORY)


@case
def an_animated_armature_is_reported_but_never_offered_the_fix():
    fresh()
    armature = rig()
    armature.scale = (2.0, 2.0, 2.0)
    armature.keyframe_insert("location", frame=1)
    finding = next(item for item in check("GODOT")["findings"] if item["code"] == "transform_unapplied")
    try:
        fix("GODOT", ("transform_unapplied", "Rig"))
        refused = False
    except RefusalError as error:
        refused = error.code == "VALIDATION"   # The finding is there; it has no safe fix (not stale).
    return finding["fixable"] is None and refused and tuple(armature.scale) == (2.0, 2.0, 2.0)


@case
def several_fixes_are_one_undo_step():
    fresh()
    armature = rig("Skeleton")
    armature.keyframe_insert("rotation_euler", frame=1)
    walk = armature.animation_data.action
    walk.name = "Walk"
    walk.layers[0].strips[0].channelbags[0].fcurves[0].keyframe_points.insert(20, 0.0)
    image = bpy.data.images.new("NormalMap", 4, 4)
    image.colorspace_settings.name = "sRGB"
    material = bpy.data.materials.new("Mat")
    tree = material.node_tree
    texture, normal = tree.nodes.new("ShaderNodeTexImage"), tree.nodes.new("ShaderNodeNormalMap")
    texture.image = image
    tree.links.new(texture.outputs["Color"], normal.inputs["Color"])
    tree.links.new(normal.outputs["Normal"], tree.nodes["Principled BSDF"].inputs["Normal"])
    box = cube("Crate")
    box.data.materials.append(material)
    report = check("UNREAL", "FBX")
    wanted = [("armature_extra_root", "Skeleton"), ("colour_space", "NormalMap")]
    if not all(has(report, *item) for item in wanted):
        details["several"] = [(item["code"], item["subject"]) for item in report["findings"]]
        return False
    result = fix("UNREAL", *wanted, fmt="FBX")
    changed = bpy.data.objects.get("Armature") is not None and image.colorspace_settings.name == "Non-Color"
    restored = undo() and bpy.data.objects.get("Skeleton") is not None and \
        bpy.data.images["NormalMap"].colorspace_settings.name == "sRGB"
    return result["success"] and len(result["applied"]) == 2 and result["undo_steps"] == 1 and changed and (
        restored or not HISTORY)


@case
def godot_loop_name_stash_and_collision_mesh():
    fresh()
    armature = rig()
    armature.keyframe_insert("location", frame=1)
    armature.location.x = 1.0
    armature.keyframe_insert("location", frame=10)
    armature.location.x = 0.0
    armature.keyframe_insert("location", frame=20)
    cycle = armature.animation_data.action
    cycle.name = "Idle"
    spare = bpy.data.actions.new("Wave")
    armature.animation_data.action = spare   # Gives Wave a slot for Rig, then leaves it without a user.
    armature.keyframe_insert("rotation_euler", frame=1)
    armature.animation_data.action = cycle
    empty = bpy.data.objects.new("Wall-colonly", None)
    empty.empty_display_type = "CUBE"
    empty.empty_display_size = 2.0
    empty.location = (3, 0, 1)
    bpy.context.scene.collection.objects.link(empty)
    child = cube("Sign", (3, 0, 4), 0.5)
    child.parent = empty
    child.matrix_parent_inverse = empty.matrix_world.inverted()
    bpy.context.view_layer.update()
    sign_at = tuple(round(v, 4) for v in child.matrix_world.translation)
    report = check("GODOT")
    wanted = [("loop_name", "Idle"), ("action_unsaved", "Wave"), ("collision_empty", "Wall-colonly")]
    details["godot_found"] = [(item["code"], item["subject"], item["fixable"]) for item in report["findings"]]
    if not all(has(report, *item) for item in wanted):
        return False
    result = fix("GODOT", *wanted)
    bpy.context.view_layer.update()
    wall = bpy.data.objects.get("Wall-colonly")
    remaining = {rule["code"] for rule in result["remaining"]["rules"]}   # Grouped (engine_findings).
    details["godot_after"] = {"names": [cycle.name, spare.users], "wall": wall and (wall.type, len(wall.data.vertices)),
                              "sign": [tuple(round(v, 4) for v in child.matrix_world.translation), sign_at],
                              "parent": child.parent and child.parent.name, "remaining": sorted(remaining)}
    return (result["success"] and cycle.name == "Idle-loop" and spare.users >= 1
            and wall is not None and wall.type == "MESH" and len(wall.data.vertices) == 8
            and tuple(round(v, 4) for v in child.matrix_world.translation) == sign_at
            and child.parent == wall and not remaining & {"loop_name", "action_unsaved", "collision_empty"})


@case
def instancing_influences_and_web_collider():
    fresh()
    for index in range(3):
        cube("Post.%03d" % index, (index * 2.0, 0, 0))
    cube("UCX_Barrier", (0, 5, 0))
    armature = rig("Rig", [("B%d" % index, None, (index, 0, 0), (index, 0, 1)) for index in range(6)])
    body = cube("Body", (0, -5, 0))
    skin(body, armature, [("B%d" % index, weight) for index, weight in enumerate((.4, .2, .15, .1, .1, .05))])
    report = check("WEB")
    posts = next(item for item in report["findings"] if item["code"] == "instancing_lost")
    wanted = [("instancing_lost", posts["subject"]), ("influences", "Body"), ("collision_foreign", "UCX_Barrier")]
    if not all(has(report, *item) for item in wanted):
        details["web_found"] = [(item["code"], item["subject"]) for item in report["findings"]]
        return False
    result = fix("WEB", *wanted)
    shared = len({bpy.data.objects["Post.%03d" % index].data.name for index in range(3)}) == 1
    details["web_after"] = {"shared": shared, "barrier": bpy.data.objects["UCX_Barrier"].data.name}
    most = max(len([g for g in vertex.groups if g.weight > 0]) for vertex in body.data.vertices)
    return result["success"] and shared and most == 4 and bpy.data.objects["UCX_Barrier"].hide_get()


@case
def a_finding_gone_since_the_check_is_refused_and_nothing_changes():
    fresh()
    cube("Lamp").constraints.new("TRACK_TO")
    try:
        fix("GODOT", ("loop_name", "Nope"))
        return False
    except RefusalError as error:
        return error.code == "STALE_REVISION" and bpy.data.objects["Lamp"].constraints
    return False


def _roundtrip(target, fmt, build):
    fresh()
    build()
    view_layer = bpy.context.view_layer
    keep = bpy.data.objects[0]
    keep.select_set(True)
    view_layer.objects.active = keep
    bpy.context.scene.frame_set(7)
    options = engine_preflight.profile(target, fmt)
    from angelo_agent import engine_facts
    objects = engine_facts.exported_objects(bpy.context.scene, view_layer, fmt, options)
    source = engine_preflight.snapshot_for(None, objects)
    path = str(OUT / ("%s.%s" % (target.lower(), fmt.lower())))
    engine_preflight._export_to(path, "fbx" if fmt == "FBX" else "gltf", options)
    before = {name: len(getattr(bpy.data, name)) for name in ("objects", "meshes", "materials", "images", "actions",
                                                              "armatures", "collections", "node_groups")}
    report = engine_preflight.verify(path, fmt, source)
    after = {name: len(getattr(bpy.data, name)) for name in before}
    clean = (before == after and view_layer.objects.active == keep and keep.select_get()
             and bpy.context.scene.frame_current == 7)
    details["%s_%s" % (target, fmt)] = {"findings": [(item["code"], item["subject"]) for item in report["findings"]],
                                        "ms": report["ms"], "clean": clean}
    return report, clean


@case
def gltf_roundtrip_finds_dropped_weights_and_leaves_nothing():
    def build():
        armature = rig("Rig", [("B%d" % index, None, (index, 0, 0), (index, 0, 1)) for index in range(6)])
        body = cube("Body")
        skin(body, armature, [("B%d" % index, weight) for index, weight in enumerate((.3, .2, .2, .1, .1, .1))])
    report, clean = _roundtrip("UNREAL", "GLB", build)
    return clean and "reimport_weights" in {item["code"] for item in report["findings"]}


@case
def gltf_roundtrip_finds_units():
    def build():
        bpy.context.scene.unit_settings.scale_length = 0.01
        cube("Crate", (0, 0, 50), 100.0)
    report, clean = _roundtrip("GODOT", "GLB", build)
    found = next((item for item in report["findings"] if item["code"] == "reimport_units"), None)
    return clean and found is not None and "100" in found["message"]


@case
def fbx_roundtrip_finds_leaf_bones_and_paths_and_leaves_nothing():
    def build():
        bpy.ops.wm.save_as_mainfile(filepath=str(OUT / "fbx-source.blend"))
        rig("Rig", (("Root", None, (0, 0, 0), (0, 0, 1)), ("Tip", "Root", (0, 0, 1), (0, 0, 2))))
        image = bpy.data.images.new("Paint", 4, 4)
        image.filepath_raw = str(OUT / "paint.png")
        image.file_format = "PNG"
        image.save()
        material = bpy.data.materials.new("Painted")
        node = material.node_tree.nodes.new("ShaderNodeTexImage")
        node.image = image
        material.node_tree.links.new(node.outputs[0], material.node_tree.nodes["Principled BSDF"].inputs[0])
        cube("Canvas").data.materials.append(material)
    report, clean = _roundtrip("UNITY", "FBX", build)
    codes = {item["code"] for item in report["findings"]}
    return clean and "reimport_bones" in codes and "path_in_file" in codes


@case
def a_clean_scene_reads_back_clean():
    def build():
        cube("Crate", (1, 2, 0.5))
        cube("Crate Lid", (1, 2, 1.2), 0.5)
    report, clean = _roundtrip("GODOT", "GLB", build)
    return clean and report["findings"] == []


@case
def numbered_source_names_pair_with_what_arrived():
    def build():   # Blender renames every imported object while the source exists: "Post.001" arrives as "Post.004".
        for index in range(4):
            cube("Post" if index == 0 else "Post.%03d" % index, (index * 3.0, 0, 0))
        cube("Post.001.Base", (0, 5, 0))
    report, clean = _roundtrip("GODOT", "GLB", build)
    return clean and report["findings"] == []


# The independent review's cases (each a class the first version got wrong).

@case
def read_back_leaves_a_mesh_in_edit_mode_untouched_and_skips_sculpt():
    fresh()
    armature = rig()
    body = cube("Body")
    skin(body, armature, [("Root", 1.0)])
    helper = bpy.data.collections.new("glTF_not_exported")   # Left by an earlier rigged import.
    bpy.context.scene.collection.children.link(helper)
    options = engine_preflight.profile("GODOT", "GLB")
    from angelo_agent import engine_facts
    path = str(OUT / "modes.glb")
    engine_preflight._export_to(path, "gltf", options)
    results = {}
    for mode in ("EDIT", "SCULPT"):
        bpy.context.view_layer.objects.active = body
        body.select_set(True)
        bpy.ops.object.mode_set(mode=mode)
        source = engine_preflight.snapshot_for(None, engine_facts.exported_objects(
            bpy.context.scene, bpy.context.view_layer, "GLB", options))
        report = engine_preflight.verify(path, "GLB", source)
        results[mode] = (body.mode, len(body.data.vertices), [c.name for c in body.users_collection],
                         [item["code"] for item in report["findings"]])
        bpy.ops.object.mode_set(mode="OBJECT")
    details["modes"] = results
    return (results["EDIT"][:3] == ("EDIT", 8, ["Scene Collection"]) and results["EDIT"][3] == []
            and results["SCULPT"][:3] == ("SCULPT", 8, ["Scene Collection"])
            and results["SCULPT"][3] == ["reimport_skipped"])


@case
def collision_mesh_keeps_what_points_at_the_empty_and_refuses_what_it_cannot_keep():
    fresh()
    empty = bpy.data.objects.new("Crate-colonly", None)
    empty.empty_display_type = "CUBE"
    bpy.context.scene.collection.objects.link(empty)
    lamp = cube("Lamp", (0, 0, 3))
    lamp.constraints.new("CHILD_OF").target = empty
    wall = cube("Wall", (5, 0, 0))
    array = wall.modifiers.new("Array", "ARRAY")
    array.use_object_offset, array.offset_object = True, empty
    fix("GODOT", ("collision_empty", "Crate-colonly"))
    mesh = bpy.data.objects["Crate-colonly"]
    remapped = (mesh.type == "MESH" and lamp.constraints[0].target == mesh and array.offset_object == mesh)
    animated = bpy.data.objects.new("Box-colonly", None)
    animated.empty_display_type = "CUBE"
    bpy.context.scene.collection.objects.link(animated)
    animated.keyframe_insert("location", frame=1)
    offered = next(item for item in check("GODOT")["findings"] if item["subject"] == "Box-colonly")["fixable"]
    return remapped and offered is None


@case
def apply_transform_is_not_offered_over_an_animated_child_and_keeps_a_scaled_child_in_place():
    fresh()
    armature = rig()
    armature.scale = (0.01, 0.01, 0.01)
    child = cube("Prop", (0, 0, 100))
    child.parent = armature
    child.scale = (2.0, 2.0, 2.0)
    bpy.context.view_layer.update()
    world = [tuple(round(v, 4) for v in child.matrix_world @ vertex.co) for vertex in child.data.vertices]
    fix("UNREAL", ("transform_unapplied", "Rig"))
    bpy.context.view_layer.update()
    kept = world == [tuple(round(v, 4) for v in child.matrix_world @ vertex.co) for vertex in child.data.vertices]
    armature = bpy.data.objects["Rig"]
    armature.scale = (0.5, 0.5, 0.5)
    child.keyframe_insert("rotation_euler", frame=1)
    finding = next(item for item in check("UNREAL")["findings"] if item["code"] == "transform_unapplied")
    return kept and finding["fixable"] is None


@case
def a_fix_that_fails_part_way_is_reported_and_one_undo_restores_it():
    fresh()
    plate = cube("Plate")
    for name in ("A", "B"):
        modifier = plate.modifiers.new(name, "NODES")
        group = bpy.data.node_groups.new(name, "GeometryNodeTree")
        group.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        group.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        nodes, links = group.nodes, group.links
        source, smooth, out = (nodes.new("NodeGroupInput"), nodes.new("GeometryNodeSetShadeSmooth"),
                               nodes.new("NodeGroupOutput"))
        links.new(source.outputs[0], smooth.inputs["Geometry"])
        if name == "B":   # Its result is a curve: Blender can't apply it to a mesh.
            convert = nodes.new("GeometryNodeMeshToCurve")
            links.new(smooth.outputs[0], convert.inputs[0])
            links.new(convert.outputs[0], out.inputs[0])
        else:
            links.new(smooth.outputs[0], out.inputs[0])
        modifier.node_group = group
        modifier.show_viewport = False   # Off in the viewport: flagged, the export doesn't apply it.
    result = fix("WEB", ("smooth_modifier", "Plate"))
    restored = undo() and [m.name for m in bpy.data.objects["Plate"].modifiers] == ["A", "B"]
    return (not result["success"] and result["undo_steps"] == 1 and "part way" in result["failed"][0]["reason"]
            and (restored or not HISTORY))


@case
def a_mask_through_a_mix_factor_is_data_and_a_rename_never_strands_another_fix():
    fresh()
    mask = bpy.data.images.new("Mask", 4, 4)
    mask.colorspace_settings.name = "Non-Color"
    material = bpy.data.materials.new("Tiles")
    tree = material.node_tree
    texture, mix = tree.nodes.new("ShaderNodeTexImage"), tree.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    texture.image = mask
    tree.links.new(texture.outputs["Color"], mix.inputs["Factor"])
    tree.links.new(mix.outputs["Result"], tree.nodes["Principled BSDF"].inputs["Base Color"])
    cube("Floor").data.materials.append(material)
    mask_ok = not has(check("WEB"), "colour_space")
    armature = rig("Skeleton")
    armature.scale = (0.01, 0.01, 0.01)
    result = fix("UNREAL", ("armature_extra_root", "Skeleton"), ("transform_unapplied", "Skeleton"), fmt="FBX")
    return mask_ok and result["success"] and bpy.data.objects["Armature"].scale[0] == 1.0


@case
def limit_influences_follows_the_armature_the_export_uses():
    fresh()
    old = rig("OldRig", [("O%d" % index, None, (index, 0, 0), (index, 0, 1)) for index in range(6)])
    new = rig("NewRig", [("N%d" % index, None, (index, 0, 0), (index, 0, 1)) for index in range(6)])
    body = cube("Body")
    first = body.modifiers.new("Old", "ARMATURE")
    first.object, first.show_viewport = old, False
    skin(body, new, [("N%d" % index, weight) for index, weight in enumerate((.3, .2, .2, .1, .1, .1))])
    for index, weight in enumerate((.3, .2, .2, .1, .1, .1)):
        body.vertex_groups.new(name="O%d" % index).add(range(8), weight, "REPLACE")
    fix("WEB", ("influences", "Body"))
    groups = {body.vertex_groups[item.group].name for item in body.data.vertices[0].groups if item.weight > 0}
    return {name for name in groups if name.startswith("O")} == {"O%d" % i for i in range(6)} and len(
        [name for name in groups if name.startswith("N")]) == 4 and not has(check("WEB"), "influences")


@case
def any_numbered_name_and_frame_rate_survive_a_read_back():
    def build():
        for index, name in enumerate(("Wheel.1", "Frame.0001", "Prop.12", "Cube.1000")):
            cube(name, (index * 3.0, 0, 0))
        bpy.context.scene.render.fps, bpy.context.scene.render.fps_base = 24, 1.0
    report, clean = _roundtrip("UNITY", "FBX", build)
    return clean and report["findings"] == [] and bpy.context.scene.render.fps_base == 1.0


@case
def the_web_export_returns_before_its_read_back_which_runs_from_json():
    fresh()
    armature = rig("Rig", [("B%d" % index, None, (index, 0, 0), (index, 0, 1)) for index in range(6)])
    body = cube("Body")
    skin(body, armature, [("B%d" % index, weight) for index, weight in enumerate((.3, .2, .2, .1, .1, .1))])
    from angelo_agent import web_export
    path = str(OUT / "web-readback.glb")
    if os.path.exists(path):
        os.remove(path)
    report = web_export.export(None, bpy.context.scene, bpy.context.view_layer, path,
                               {"meshes": "AUTO", "textures": "WEBP", "merge": True, "selection_only": False},
                               preflight=True)
    readback = report.pop("_readback")
    spec = json.loads(json.dumps({"path": path, "source": readback["source"], "merged": readback["merged"],
                                  "needles": {}, "result": str(OUT / "web-readback.json")}))   # As the child reads it.
    objects = len(bpy.data.objects)
    result = engine_preflight.readback_child(spec)   # The child's work, here in-process.
    details["web_readback"] = [item["code"] for item in result["findings"]]
    return ("reimport" not in report["preflight"] and "findings" in report["preflight"]
            and json.load(open(spec["result"]))["findings"] == result["findings"]
            and "reimport_weights" in details["web_readback"] and len(bpy.data.objects) <= objects)


def _faces(obj):
    """The material each face of ``obj`` is drawn with, as the object resolves its slots."""
    slots = obj.material_slots
    return [slots[face.material_index].material.name if face.material_index < len(slots)
            and slots[face.material_index].material else None for face in obj.data.polygons]


@case
def link_data_never_changes_what_any_object_is_drawn_with():
    """Twins differ only by materials: the mesh's own list, an object-linked slot, or per-face indices. Each set of
    objects that really match is linked; no object's faces change material (audit P1)."""
    fresh()
    materials = [bpy.data.materials.new("Mat%d" % index) for index in range(3)]
    built = {}

    def twin(name, x, data, slot=None, faces=None):
        obj = cube(name, (x, 0, 0))
        for material in data:
            obj.data.materials.append(material)
        if slot is not None:
            obj.material_slots[0].link = "OBJECT"
            obj.material_slots[0].material = slot
        for index in faces or ():
            obj.data.polygons[index].material_index = 1
        built[name] = obj

    for index in range(2):
        twin("DataA.%d" % index, index, [materials[0]])
        twin("DataB.%d" % index, 2 + index, [materials[1]])
        twin("Slot.%d" % index, 4 + index, [materials[0]], slot=materials[2])
        twin("Faces.%d" % index, 6 + index, [materials[0], materials[1]], faces=(0, 3))
    twin("FacesOther", 8, [materials[0], materials[1]], faces=(1,))
    twin("Plain", 9, [materials[0]])
    before = {name: _faces(obj) for name, obj in built.items()}
    report = check("WEB")
    offered = [item for item in report["findings"] if item["code"] == "instancing_lost"]
    details["link_groups"] = [(item["subject"], item["message"]) for item in offered]
    result = fix("WEB", *[("instancing_lost", item["subject"]) for item in offered])
    after = {name: _faces(obj) for name, obj in built.items()}
    shared = {name: obj.data.name for name, obj in built.items()}
    details["link_after"] = shared
    pairs = [("DataA.0", "DataA.1"), ("DataB.0", "DataB.1"), ("Slot.0", "Slot.1"), ("Faces.0", "Faces.1")]
    return (result["success"] and before == after and all(shared[a] == shared[b] for a, b in pairs)
            and len({shared[a] for a, _ in pairs} | {shared["FacesOther"]}) == 5 and len(offered) == 4)


@case
def link_data_checks_its_group_again_just_before_linking():
    """A twin changed after the check (another material) is not linked, though the check's key says it matched."""
    fresh()
    from angelo_agent import engine_fixes
    red, blue = bpy.data.materials.new("First"), bpy.data.materials.new("Second")
    objects = [cube("Twin.%d" % index, (index * 2.0, 0, 0)) for index in range(3)]
    for obj in objects:
        obj.data.materials.append(red)
    report, facts, options = engine_preflight.run(None, "WEB", "GLB")
    subject = next(item["subject"] for item in report["findings"] if item["code"] == "instancing_lost")
    objects[2].data.materials[0] = blue   # After the check, before the fix.
    changed = engine_fixes.link_data(subject, facts, {})
    details["relinked"] = changed
    return (objects[0].data == objects[1].data and objects[2].data != objects[0].data
            and _faces(objects[2]) == ["Second"] * 6 and len(changed) == 1)


@case
def a_read_back_compares_the_export_it_was_taken_from():
    """Exports 1 and 2 go to the same name; the read-back of 1, run after 2, still compares 1's file with 1's
    scene (audit P2: a pathname alone would compare 2's file)."""
    fresh()
    from angelo_agent import export_snapshot, web_export
    settings = {"meshes": "AUTO", "textures": "WEBP", "merge": False, "selection_only": False}
    path = str(OUT / "web-snapshot.glb")
    copy = str(OUT / "web-snapshot-copy.glb")
    for leftover in (path, copy):
        if os.path.exists(leftover):
            os.remove(leftover)
    cube("First", size=1.0)
    first = web_export.export(None, bpy.context.scene, bpy.context.view_layer, path, settings, preflight=True)
    identity = export_snapshot.take(path, copy)
    bpy.data.objects["First"].scale = (4.0, 4.0, 4.0)
    cube("Second", (6, 0, 0))
    os.remove(path)
    web_export.export(None, bpy.context.scene, bpy.context.view_layer, path, settings, preflight=True)
    spec = {"source": first["_readback"]["source"], "merged": False, "needles": {}}
    from_copy = engine_preflight.readback_child(dict(spec, path=copy, identity=identity,
                                                     result=str(OUT / "snapshot-copy.json")))
    from_path = engine_preflight.readback_child(dict(spec, path=path, result=str(OUT / "snapshot-path.json")))
    with open(copy, "ab") as handle:
        handle.write(b"later")
    changed = engine_preflight.readback_child(dict(spec, path=copy, identity=identity,
                                                   result=str(OUT / "snapshot-changed.json")))
    details["snapshot"] = {"copy": [item["code"] for item in from_copy["findings"]],
                           "path": [item["code"] for item in from_path["findings"]], "changed": changed}
    return not from_copy["findings"] and from_path["findings"] and "error" in changed


@case
def a_link_report_names_its_save_and_a_resaved_blend_writes_none():
    fresh()
    cube("Crate")
    blend = str(OUT / "link-save.blend")
    report_file = str(OUT / "link-save.preflight.json")
    for leftover in (blend, report_file):
        if os.path.exists(leftover):
            os.remove(leftover)
    bpy.ops.wm.save_as_mainfile(filepath=blend)
    # As the background Angelo engine_preflight_link starts: it opens the saved file, and preflight_child vouches for
    # the scene only through the identity of the file that load read (document_file_identity.loaded).
    bpy.ops.wm.open_mainfile(filepath=blend)
    from angelo_agent import export_snapshot
    spec = {"engine": "godot", "report": report_file, "file": None, "exporter": "gltf",
            "options": engine_preflight.blend_import_profile("godot", {})[1], "scene_version": 7,
            "blend_identity": export_snapshot.identity(blend), "save_key": "token:7"}
    done = engine_preflight.preflight_child(spec)
    written = json.load(open(report_file))
    os.remove(report_file)
    bpy.ops.wm.save_as_mainfile(filepath=blend)   # Saved again: the first request is no longer this file's.
    stale = engine_preflight.preflight_child(spec)
    return (done is not None and written["status"] == "done" and written["scene_version"] == 7
            and written["blend_identity"] == spec["blend_identity"] and written["save_key"] == "token:7"
            and written["saved_at"] > 0
            and stale is None and not os.path.exists(report_file))


report = {"success": bool(checks) and all(checks.values()), "checks": checks, "details": details,
          "undo_history": HISTORY}
(OUT / "results.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_ENGINE_PREFLIGHT", json.dumps({"success": report["success"], "checks": checks}))
