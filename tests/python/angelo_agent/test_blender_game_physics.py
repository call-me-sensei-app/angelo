# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Game physics in the compiled app: the settings, the agent's property_set, the OMI export, the import and findings.

* the installed ``Object.angelo_physics`` definitions run no Python and can't be keyed (what ``rna_rules`` admits);
* ``property_set`` on ``angelo_physics.*`` through a real staged program: commit, read-back, one Undo, refusals;
* a glTF export writes OMI_physics_body/OMI_physics_shape in the tree Godot's importer needs (doc/angelo/
  game_physics.rst): a body's mesh on a ``<name>Mesh`` child, shapes directly below their body (a grandchild hoisted
  with its composed transform, an animated one left in place), compound triggers, collision-only meshes, capsules in
  the form Godot reads, scale baked into the dimensions;
* a skinned mesh's shape fits the rest pose the file writes and is hoisted, a mesh on an animated bone keeps its
  shape in place, and ``shape_indirect`` flags exactly the latter;
* the Game Physics panel's fitted size and Edit Fitted Size equal the exported shape on both, also with a modifier
  after the Armature modifier, measured outside the draw into a cache a pose keeps and a modifier or mesh change drops;
* an export without physics has no OMI and creates no physics storage on any object;
* the import reads it all back into the same settings and objects;
* Godot name suffixes instead of OMI, the findings, and the web export's merge leaving physics alone.

Godot itself imports these files in ``test_blender_godot_physics_e2e``. Run only in a disposable instance with the
exact Angelo binary: ``--background --factory-startup --disable-autoexec --python-exit-code 1``. No provider,
account, network request or paid call is involved.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import inspect
import json
import math
import os
import struct
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-game-physics-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries, rna_rules  # noqa: E402
from angelo_agent import game_physics, web_export  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from io_scene_gltf2.angelo_physics import props  # noqa: E402
from program_qa_helpers import no_stages, commit_operator  # noqa: E402

report = {"checks": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path, "version": bpy.app.version_string}
window = bpy.context.window_manager.windows[0]


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def close(a, b, tolerance=1e-4):
    return all(abs(x - y) <= tolerance for x, y in zip(a, b)) and len(a) == len(b)


def empty_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)
    window.scene.angelo_physics_format = "OMI"
    queries.invalidate()


def box(name, location=(0, 0, 0), size=(2, 2, 2), parent=None):
    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
    obj = bpy.context.object
    obj.name = obj.data.name = name
    for vertex in obj.data.vertices:
        vertex.co = (vertex.co.x * size[0] / 2, vertex.co.y * size[1] / 2, vertex.co.z * size[2] / 2)
    obj.data.update()
    obj.parent = parent
    obj.location = location
    return obj


def mark(obj, **values):
    for key, value in values.items():
        setattr(obj.angelo_physics, key, value)
    return obj


def export(name, **options):
    path = OUT / name
    result = bpy.ops.export_scene.gltf(filepath=str(path), export_format="GLB", use_active_scene=True, **options)
    assert "FINISHED" in result, result
    data = path.read_bytes()
    length = struct.unpack("<I", data[12:16])[0]
    return path, json.loads(data[20:20 + length])


def nodes_by_name(document):
    return {node.get("name"): node for node in document.get("nodes", [])}


def holder(nodes, name, role="Collider"):
    """The node carrying ``name``'s shape: its own, when the shape sits at its origin, else ``<name><role>``."""
    return nodes.get(name + role) or nodes[name]


def scene_position(document, name):
    """Where a node's origin is in the glTF scene, composed from its ancestors' TRS."""
    from io_scene_gltf2.angelo_physics import shapes
    nodes = document["nodes"]
    parents = {child: index for index, node in enumerate(nodes) for child in node.get("children", ())}
    index = next(i for i, node in enumerate(nodes) if node.get("name") == name)
    matrix = None
    while index is not None:
        node = nodes[index]
        local = shapes.compose(node.get("translation"), node.get("rotation"), node.get("scale"))
        matrix = local if matrix is None else shapes.multiply(local, matrix)
        index = parents.get(index)
    return [matrix[row][3] for row in range(3)]


def shape_of(document, node):
    physics = node["extensions"]["OMI_physics_body"]
    role = "collider" if "collider" in physics else "trigger"
    return role, document["extensions"]["OMI_physics_shape"]["shapes"][physics[role]["shape"]]


def definitions():
    """What rna_rules admits is what the glTF add-on defines without callbacks, and none of it can be keyed."""
    annotations = inspect.get_annotations(props.AngeloPhysicsSettings, eval_str=True)
    admitted = {path.split(".", 1)[1] for path in rna_rules.GAME_PHYSICS}
    check("admitted_paths_are_the_definitions", admitted == set(annotations))
    problems = [name for name, value in annotations.items()
                if value.keywords.get("get") or value.keywords.get("set") or value.keywords.get("update")
                or callable(value.keywords.get("items"))]
    check("definitions_run_no_python", not problems)
    rna = props.AngeloPhysicsSettings.bl_rna.properties
    check("definitions_are_not_keyable", all(not rna[name].is_animatable for name in admitted))
    check("every_session_has_them", "angelo_physics" in bpy.types.Object.bl_rna.properties
          and "angelo_physics_format" in bpy.types.Scene.bl_rna.properties)


def program(operations, writes=()):
    return validate_call("angelo_scene_program", {"mode": "APPLY", "read_set": [], "write_set": list(writes),
                                                  "operations": operations})


def settle(job):
    for _ in range(200):
        result = job.poll()
        if result.get("final") or result.get("ready_to_commit"):
            return result
    raise AssertionError("cooperative native program did not settle")


def agent_property_set():
    empty_scene()
    crate = box("AgentCrate")
    bpy.ops.ed.undo_push(message="game physics QA baseline")
    queries.invalidate()
    target = queries.object_handle(crate)
    binding = {"handle": target, "revision": queries.object_revision(window, crate)}
    values = {"angelo_physics.body": "RIGID", "angelo_physics.mass": 3.5, "angelo_physics.shape": "CAPSULE",
              "angelo_physics.axis": "X"}
    arguments = program([{"kind": "property_set", "target": target, "parameters": {"values": values}}], [binding])
    job = native.begin(window, "angelo_scene_program", arguments)
    staged = settle(job)
    check("agent_stages_without_a_live_edit", staged.get("ready_to_commit") and not props.marked(crate))
    result = bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    crate = window.scene.objects["AgentCrate"]
    settings = crate.angelo_physics
    check("agent_commits_physics", "FINISHED" in result and job.result.get("status") == "committed"
          and settings.body == "RIGID" and abs(settings.mass - 3.5) < 1e-6 and settings.shape == "CAPSULE"
          and settings.axis == "X")
    bpy.ops.ed.undo()
    queries.invalidate()
    crate = window.scene.objects["AgentCrate"]
    check("agent_one_undo_restores", not props.marked(crate) and no_stages())
    refused = ""
    try:   # The common path's enum items refuse it before anything is staged.
        program([{"kind": "property_set", "target": queries.object_handle(crate),
                  "parameters": {"values": {"angelo_physics.body": "FLYING"}}}],
                [{"handle": queries.object_handle(crate), "revision": queries.object_revision(window, crate)}])
    except ValueError as error:
        refused = str(error)
    check("agent_unknown_body_refused", bool(refused))
    report["refusal"] = refused[:300]
    native.cleanup_all()


def omi_export():
    empty_scene()
    crate = mark(box("Crate", (0, 0, 1), size=(1, 2, 0.5)), body="RIGID", mass=4, shape="BOX")
    crate.scale = (2, 2, 2)
    wall = mark(box("Wall", (6, 0, 0), size=(4, 0.2, 3)), shape="TRIMESH")
    pill = mark(box("Pill", (0, 6, 0), size=(0.8, 0.8, 3)), body="KINEMATIC", shape="CAPSULE")
    zone = mark(box("Zone", (-6, 0, 0)), body="TRIGGER")
    mark(box("ZonePart", (0, 0, 2), parent=zone), shape="SPHERE")
    cart = mark(bpy.data.objects.new("Cart", None), body="RIGID")
    window.scene.collection.objects.link(cart)
    frame = bpy.data.objects.new("Frame", None)
    window.scene.collection.objects.link(frame)
    frame.parent, frame.location = cart, (0, 0, 1)
    frame.rotation_euler = (0, 0, math.pi / 2)
    mark(box("Wheel", (1, 0, 0), size=(1, 1, 1), parent=frame), shape="CYLINDER", axis="Y")
    spinner = mark(box("Spinner", (0, 3, 0), parent=frame), shape="BOX")
    spinner.keyframe_insert("rotation_euler", frame=1)
    hull = mark(box("CrateHull", (0, 0, 0), size=(1, 2, 0.5), parent=crate), shape="CONVEX", collision_only=True)
    box("Plain", (0, -6, 0))
    path, document = export("physics.glb")
    report["omi_nodes"] = document["nodes"]
    nodes = nodes_by_name(document)
    check("omi_declared", {"OMI_physics_body", "OMI_physics_shape"} <= set(document.get("extensionsUsed", ()))
          and "OMI_physics_body" not in document.get("extensionsRequired", ()))
    body = nodes["Crate"]
    check("body_keeps_name_and_moves_mesh_to_child", "mesh" not in body
          and body["extensions"]["OMI_physics_body"]["motion"] == {"type": "dynamic", "mass": 4}
          and document["nodes"][body["children"][0]]["name"] == "CrateMesh"
          and "mesh" in document["nodes"][body["children"][0]])
    role, entry = shape_of(document, holder(nodes, "Crate"))
    check("box_fitted_in_gltf_axes", role == "collider" and entry["type"] == "box"
          and close(entry["box"]["size"], (1, 0.5, 2)) and "CrateCollider" not in nodes)
    check("body_scale_stays_on_body", close(body.get("scale", (1, 1, 1)), (2, 2, 2)))
    role, entry = shape_of(document, holder(nodes, "CrateHull"))
    hull_mesh = entry["convex"]["mesh"]
    check("collision_only_mesh_drawn_by_no_node", entry["type"] == "convex"
          and all(node.get("mesh") != hull_mesh for node in document["nodes"]))
    check("collision_only_child_carries_its_own_shape", "CrateHullCollider" not in nodes
          and nodes["CrateHull"] in [document["nodes"][index] for index in body["children"]])
    check("static_mesh_keeps_mesh_with_shape_child", "mesh" in nodes["Wall"] and "extensions" not in nodes["Wall"]
          and shape_of(document, nodes["WallCollider"])[1]["trimesh"]["mesh"] == nodes["Wall"]["mesh"])
    role, entry = shape_of(document, holder(nodes, "Pill"))
    check("capsule_in_godot_form", entry["type"] == "capsule" and close(
        [entry["capsule"][key] for key in ("radius", "radiusTop", "radiusBottom", "height")], (0.4, 0.4, 0.4, 3.0)))
    trigger = nodes["Zone"]["extensions"]["OMI_physics_body"]["trigger"]
    members = [document["nodes"][index]["name"] for index in trigger.get("nodes", [])]
    check("compound_trigger_lists_its_shapes", members == ["ZonePartTrigger"]
          and shape_of(document, nodes["ZonePartTrigger"])[0] == "trigger"
          and nodes["ZonePartTrigger"] in [document["nodes"][i] for i in nodes["Zone"]["children"]])
    cart_children = [document["nodes"][index]["name"] for index in nodes["Cart"].get("children", [])]
    check("grandchild_shape_hoisted_under_body", "WheelCollider" in cart_children)
    wheel = nodes["WheelCollider"]
    # Frame (1 up, turned 90 degrees about Z) then Wheel (1 along X): Blender (0, 1, 1), glTF (0, 1, -1).
    check("hoisted_shape_composes_its_transforms", close(wheel["translation"], (0, 1, -1), 1e-3))
    check("animated_shape_stays_on_its_node", "SpinnerCollider" in [
        document["nodes"][index]["name"] for index in nodes["Spinner"].get("children", [])])
    check("plain_object_untouched", "extensions" not in nodes["Plain"] and "mesh" in nodes["Plain"])
    report["omi_document_extensions"] = document.get("extensions")
    return path


def unchanged_without_physics():
    empty_scene()
    for index in range(5):
        box("Plain%d" % index, (index * 3, 0, 0))
    _path, document = export("plain.glb")
    text = json.dumps(document)
    check("no_physics_no_omi", "OMI_" not in text)
    check("export_creates_no_physics_storage", all(
        obj.bl_system_properties_get() is None or "angelo_physics" not in obj.bl_system_properties_get()
        for obj in window.scene.objects))


def import_round_trip(path):
    empty_scene()
    bpy.ops.import_scene.gltf(filepath=str(path))
    objects = window.scene.objects
    crate = objects["Crate"]
    check("import_folds_visual_child", crate.type == "MESH" and "CrateMesh" not in objects
          and crate.angelo_physics.body == "RIGID" and abs(crate.angelo_physics.mass - 4) < 1e-6)
    collider = crate.angelo_physics
    check("import_reads_box_in_blender_axes", collider.shape == "BOX" and collider.fit == "MANUAL"
          and close(collider.size, (1, 2, 0.5)))
    wall = objects["Wall"].angelo_physics
    check("import_folds_mesh_shape_into_its_mesh", wall.shape == "TRIMESH" and "WallCollider" not in objects)
    pill = objects["Pill"].angelo_physics
    check("import_reads_capsule", pill.body == "KINEMATIC" and pill.shape == "CAPSULE" and abs(pill.radius - 0.4) < 1e-4
          and abs(pill.height - 3.0) < 1e-4 and pill.axis == "Z")
    check("import_reads_trigger", objects["Zone"].angelo_physics.body == "TRIGGER"
          and objects["ZonePartTrigger"].angelo_physics.shape == "SPHERE"
          and objects["ZonePartTrigger"].angelo_physics.body == "NONE")
    hull = next(obj for obj in objects if obj.angelo_physics.collision_only)
    check("import_keeps_collision_only_mesh", hull.type == "MESH" and hull.angelo_physics.shape == "CONVEX"
          and hull.hide_render)
    _again, document = export("round-trip.glb")
    names = {node.get("name") for node in document["nodes"]}
    check("round_trip_keeps_node_names", {"Crate", "CrateMesh", "Wall", "WallCollider", "Pill", "PillMesh", "Zone",
                                          "ZoneMesh", "ZonePartTrigger", "Cart", "WheelCollider"} <= names)


def suffixes():
    empty_scene()
    window.scene.angelo_physics_format = "GODOT_SUFFIXES"
    mark(box("Floor"), shape="TRIMESH")
    mark(box("Barrel", (3, 0, 0)), body="RIGID", shape="CONVEX")
    mark(box("Door", (6, 0, 0)), body="KINEMATIC", shape="BOX")
    _path, document = export("suffixes.glb")
    names = {node.get("name") for node in document["nodes"]}
    check("suffix_format_names", {"Floor-col", "Barrel-rigid", "Door"} <= names and "OMI_" not in json.dumps(document))
    found = {item["code"] for item in game_physics.scene_findings(window.scene)}
    check("suffix_findings", "suffix_unsupported" in found)
    window.scene.angelo_physics_format = "OMI"


def findings():
    empty_scene()
    legacy = bpy.data.objects.new("Legacy-colonly", None)
    window.scene.collection.objects.link(legacy)
    mark(box("Moving", size=(1, 1, 1)), body="RIGID", shape="TRIMESH")
    mark(box("Floaty", (3, 0, 0)), body="RIGID")
    mark(box("Plane", (6, 0, 0), size=(2, 2, 0)), shape="BOX")
    mark(box("Stretched", (9, 0, 0)), body="RIGID", shape="BOX").scale = (1, 2, 1)
    mark(box("Twice-col", (12, 0, 0)), shape="BOX")
    probe = mark(bpy.data.objects.new("Probe", None), shape="CONVEX")
    window.scene.collection.objects.link(probe)
    found = {(item["code"], item["subject"]) for item in game_physics.scene_findings(window.scene)}
    report["findings"] = sorted(found)
    check("finding_colonly_empty", ("colonly_empty", "Legacy-colonly") in found)
    check("finding_trimesh_moving", ("trimesh_moving", "Moving") in found)
    check("finding_body_without_shape", ("body_without_shape", "Floaty") in found)
    check("finding_flat_shape", ("flat_shape", "Plane") in found)
    check("finding_scaled_body", ("scaled_body", "Stretched") in found)
    check("finding_suffix_and_physics", ("suffix_and_physics", "Twice-col") in found)
    check("finding_no_mesh", ("no_mesh", "Probe") in found)


def skinned_rig():
    """Rig (kinematic body) > RigArmature, posed and keyed; Limb skinned to its bone with a box, Hat on the bone with
    a sphere. Returns (armature, limb, hat)."""
    empty_scene()
    rig = mark(bpy.data.objects.new("Rig", None), body="KINEMATIC")
    window.scene.collection.objects.link(rig)
    rig.location = (3, 0, 0)   # Off the origin: the exporter writes skinned vertices in world space.
    armature = bpy.data.objects.new("RigArmature", bpy.data.armatures.new("RigArmature"))
    window.scene.collection.objects.link(armature)
    armature.parent = rig
    bpy.context.view_layer.objects.active = armature
    bpy.ops.object.mode_set(mode="EDIT")
    bone = armature.data.edit_bones.new("Arm")
    bone.head, bone.tail = (0, 0, 0), (0, 0, 1)
    bpy.ops.object.mode_set(mode="OBJECT")
    limb = mark(box("Limb", (0, 1, 0), size=(0.2, 0.2, 2), parent=armature), shape="BOX")
    group = limb.vertex_groups.new(name="Arm")
    group.add(list(range(len(limb.data.vertices))), 1.0, "REPLACE")
    limb.modifiers.new("Armature", "ARMATURE").object = armature
    pose = armature.pose.bones["Arm"]
    pose.rotation_mode = "XYZ"
    pose.rotation_euler = (math.pi / 2, 0, 0)   # The current pose lays the limb along Y; its rest is along Z.
    pose.keyframe_insert("rotation_euler", frame=1)
    hat = mark(box("Hat", size=(0.5, 0.5, 0.5), parent=armature), shape="SPHERE")
    hat.parent_type, hat.parent_bone = "BONE", "Arm"
    bpy.context.view_layer.update()
    return armature, limb, hat


def skinned_and_bone_parented():
    """A skinned mesh's shape fits the rest pose the file writes and is hoisted under its body (only its skin moves,
    not its node); a mesh on an animated bone keeps its shape on its own node; the findings flag only the latter."""
    _armature, limb, _hat = skinned_rig()
    corners = [tuple(corner) for corner in limb.bound_box]
    check("skinned_fixture_is_posed", max(c[2] for c in corners) - min(c[2] for c in corners) < 1.0)
    _path, document = export("skinned.glb")
    nodes = nodes_by_name(document)
    below = {document["nodes"][index]["name"] for index in nodes["Rig"].get("children", [])}
    check("skinned_shape_hoisted_under_body", "LimbCollider" in below)
    role, entry = shape_of(document, nodes["LimbCollider"])
    report["skinned_collider"] = {"node": nodes["LimbCollider"], "shape": entry, "posed_bound_box": corners}
    check("skinned_shape_fits_rest_pose", entry["type"] == "box" and close(entry["box"]["size"], (0.2, 2, 0.2), 1e-3)
          and close(nodes["LimbCollider"].get("translation", (0, 0, 0)), (0, 0, -1), 1e-3))
    check("bone_parented_shape_stays_on_its_node", "HatCollider" in [
        document["nodes"][index]["name"] for index in nodes["Hat"].get("children", [])])
    flagged = {item["subject"] for item in game_physics.scene_findings(window.scene)
               if item["code"] == "shape_indirect"}
    check("finding_flags_only_what_moves_in_its_body", flagged == {"Hat"})
    # A convex hull points at the skinned mesh's vertices, which the file holds in world space and draws through the
    # skin (here its bind is the identity): its node must sit at the scene's origin, wherever the body is.
    limb.angelo_physics.shape = "CONVEX"
    _path, document = export("skinned-hull.glb")
    check("skinned_mesh_shape_sits_where_its_skin_draws", close(scene_position(document, "LimbCollider"), (0, 0, 0)))


def panel_matches_export():
    """The panel's fitted size and Edit Fitted Size are the export's fit (``fitting``), on a posed skinned mesh (also
    with a modifier after its Armature modifier) and on a bone-parented one. The panel never measures in its draw: a
    timer does, into a cache that a new pose keeps and a modifier or mesh change drops."""
    from types import SimpleNamespace
    from angelo_agent import game_physics_ui as ui
    from io_scene_gltf2.angelo_physics import fitting, shapes
    armature, limb, hat = skinned_rig()
    fitting.clear()
    check("panel_measures_outside_its_draw", ui.fitted(limb) is None and limb.session_uid in fitting._pending)
    fitting._measure_pending()

    def exported(name):
        _path, document = export(name, export_apply=True)
        nodes = nodes_by_name(document)
        return nodes, shape_of(document, nodes["LimbCollider"])[1], shape_of(document, holder(nodes, "Hat"))[1]

    limb_fit, hat_fit = ui.fitted(limb), ui.fitted(hat)
    nodes, limb_entry, hat_entry = exported("panel.glb")
    report["panel_fit"] = {"limb": repr(limb_fit), "hat": repr(hat_fit), "limb_entry": limb_entry, "hat_entry": hat_entry}
    check("panel_shows_exported_skinned_size", limb_fit is not None and close(
        shapes.swap_size(limb_fit.size, True), limb_entry["box"]["size"], 1e-4))
    check("panel_shows_exported_bone_parented_size", hat_fit is not None and abs(
        hat_fit.radius - hat_entry["sphere"]["radius"]) < 1e-4)
    fitting.settle()   # The timer's turn (a background Angelo runs no timers): the export's switches undone.
    check("cache_survives_an_export", limb.session_uid in fitting._cache)
    seen = []

    def record_updates(_scene, depsgraph):   # What the depsgraph says when only the pose changes.
        seen.extend((type(item.id).__name__, item.id.name, item.is_updated_geometry) for item in depsgraph.updates)

    bpy.app.handlers.depsgraph_update_post.append(record_updates)
    try:
        armature.pose.bones["Arm"].rotation_euler = (0.3, 0, 0)
        bpy.context.view_layer.update()
    finally:
        bpy.app.handlers.depsgraph_update_post.remove(record_updates)
    report["pose_updates"] = seen
    fitting.settle()
    check("cache_survives_a_pose_change", limb.session_uid in fitting._cache)
    array = limb.modifiers.new("Array", "ARRAY")   # After the Armature modifier: the rest pose, arrayed along Z.
    array.count, array.relative_offset_displace = 2, (0, 0, 1)
    bpy.context.view_layer.update()
    fitting.settle()
    check("cache_drops_on_a_modifier_change", limb.session_uid not in fitting._cache and fitting.kind(limb) == "rest"
          and limb.session_uid in fitting._pending)
    fitting._measure_pending()
    limb_fit = ui.fitted(limb)
    nodes, limb_entry, _hat = exported("panel-array.glb")
    check("panel_shows_exported_rest_with_modifiers", limb_fit is not None and close(limb_fit.size, (0.2, 0.2, 4), 1e-4)
          and close(shapes.swap_size(limb_fit.size, True), limb_entry["box"]["size"], 1e-4))
    auto_translation = nodes["LimbCollider"].get("translation", [0, 0, 0])
    ui.ANGELO_OT_game_physics_edit_fit.execute(None, SimpleNamespace(object=limb))
    nodes, manual_entry, _hat = exported("panel-manual.glb")
    check("edit_fitted_size_keeps_the_exported_shape", limb.angelo_physics.fit == "MANUAL" and close(
        manual_entry["box"]["size"], limb_entry["box"]["size"], 1e-4) and close(
        nodes["LimbCollider"].get("translation", [0, 0, 0]), auto_translation, 1e-4))
    limb.angelo_physics.fit = "AUTO"
    ui.fitted(limb)
    fitting._measure_pending()
    for vertex in limb.data.vertices:
        vertex.co.x *= 2
    limb.data.update()
    bpy.context.view_layer.update()
    fitting.settle()
    check("cache_drops_on_a_mesh_change", limb.session_uid not in fitting._cache)


def web_merge_skips_physics():
    empty_scene()
    marked = mark(box("Marked"), shape="BOX")
    child = box("Child", (0, 0, 2), parent=marked)
    plain = box("Plain", (3, 0, 0))
    names = web_export.static_names([marked, child, plain])
    check("web_merge_leaves_physics_alone", names == {"Plain"})


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    definitions()
    agent_property_set()
    path = omi_export()
    unchanged_without_physics()
    import_round_trip(path)
    suffixes()
    findings()
    skinned_and_bone_parented()
    panel_matches_export()
    web_merge_skips_physics()
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "game-physics.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_GAME_PHYSICS", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
