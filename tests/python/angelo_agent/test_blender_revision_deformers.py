# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An exact object revision changes whenever what deforms the object changes, frame changes included.

Also recorded there: a lattice's and a curve's own revisions (and so the mesh they deform) left out their shape
keys, so editing a key's value deformed the mesh under one exact revision at a single frame.

Recorded on 1.0.9 (555b307aae8), 1.0.10 (24bdf3dff00) and main (ad7f67f8b72), 2026-11-05: ``revision_time.still``
followed five Mirror and Array attribute names, so an unparented mesh on an Armature, Lattice, Hook, Curve,
Shrinkwrap, Cast, Simple Deform, Mesh Deform, Surface Deform or Screw object was "still" and its revision left the
frame out. Keyed B-Bone pose values on its rig, or a keyed lattice shape key, then deformed it from frame to frame
under one exact revision. The objects followed now come from Blender's RNA; this suite pins that set against bpy, so
a pointer Blender adds or renames fails here until ``revision_time._read_objects`` is checked against it.

Run only in a disposable instance with the exact Angelo binary:
``--background --factory-startup --disable-autoexec --python-exit-code 1``. No provider or paid call.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-revision-deformers-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import object_dependencies, revision_time, rna_schema, scene_queries as queries  # noqa: E402
from angelo_agent.program_frames import FRAME_INDEPENDENT_MODIFIERS  # noqa: E402

report = {"checks": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path, "timings": {}}
window = bpy.context.window_manager.windows[0]
scene = window.scene

# Every Object or Collection pointer of each modifier type a mesh can hold, read from bpy's RNA (pointer
# properties, and those of nested non-ID structs and collection items, four levels deep), Blender 5.2.
RECORDED_MODIFIER_POINTERS = {
    "ARMATURE": ["object"], "ARRAY": ["curve", "end_cap", "offset_object", "start_cap"],
    "BOOLEAN": ["collection", "object"], "CAST": ["object"],
    "CLOTH": ["collision_settings.collection", "settings.effector_weights.collection"],
    "CURVE": ["object"], "DATA_TRANSFER": ["object"], "DISPLACE": ["texture_coords_object"],
    "DYNAMIC_PAINT": ["brush_settings.particle_system.active_particle_target.object",
                      "brush_settings.particle_system.cloth.collision_settings.collection",
                      "brush_settings.particle_system.parent", "brush_settings.particle_system.reactor_target_object",
                      "brush_settings.particle_system.targets[].object", "canvas_settings.canvas_surfaces[].brush_collection",
                      "canvas_settings.canvas_surfaces[].effector_weights.collection"],
    "FLUID": ["domain_settings.effector_group", "domain_settings.effector_weights.collection",
              "domain_settings.fluid_group", "domain_settings.force_collection", "domain_settings.guide_parent",
              "flow_settings.particle_system.active_particle_target.object",
              "flow_settings.particle_system.cloth.collision_settings.collection",
              "flow_settings.particle_system.parent", "flow_settings.particle_system.reactor_target_object",
              "flow_settings.particle_system.targets[].object"],
    "HOOK": ["object"], "LATTICE": ["object"], "MASK": ["armature"], "MESH_DEFORM": ["object"],
    "MIRROR": ["mirror_object"], "NORMAL_EDIT": ["target"],
    "PARTICLE_INSTANCE": ["object", "particle_system.active_particle_target.object",
                          "particle_system.cloth.collision_settings.collection",
                          "particle_system.cloth.settings.effector_weights.collection", "particle_system.parent",
                          "particle_system.reactor_target_object", "particle_system.targets[].object"],
    "PARTICLE_SYSTEM": ["particle_system.active_particle_target.object",
                        "particle_system.cloth.collision_settings.collection",
                        "particle_system.cloth.settings.effector_weights.collection", "particle_system.parent",
                        "particle_system.reactor_target_object", "particle_system.targets[].object"],
    "SCREW": ["object"], "SHRINKWRAP": ["auxiliary_target", "target"], "SIMPLE_DEFORM": ["origin"],
    "SOFT_BODY": ["settings.collision_collection", "settings.effector_weights.collection"],
    "SURFACE_DEFORM": ["target"], "UV_PROJECT": ["projectors[].object"], "UV_WARP": ["object_from", "object_to"],
    "VERTEX_WEIGHT_EDIT": ["mask_tex_map_object"], "VERTEX_WEIGHT_MIX": ["mask_tex_map_object"],
    "VERTEX_WEIGHT_PROXIMITY": ["mask_tex_map_object", "target"], "VOLUME_TO_MESH": ["object"],
    "WARP": ["object_from", "object_to", "texture_coords_object"],
    "WAVE": ["start_position_object", "texture_coords_object"],
}
# The same for the data of every object type ``revision_time.still`` can call still (read without collections).
RECORDED_DATA_POINTERS = {"MESH": [], "CAMERA": ["dof.focus_object"], "LIGHT_POINT": [], "LIGHT_SUN": [],
                          "LIGHT_SPOT": [], "LIGHT_AREA": [], "ARMATURE": [], "LATTICE": [],
                          "CURVE": ["bevel_object", "taper_object"]}
# A deformer object of the type each pointer accepts (Blender refuses other types for these).
TARGET_TYPES = {("ARMATURE", "object"): "ARMATURE", ("LATTICE", "object"): "LATTICE", ("CURVE", "object"): "CURVE",
                ("ARRAY", "curve"): "CURVE", ("MESH_DEFORM", "object"): "MESH", ("SHRINKWRAP", "target"): "MESH",
                ("SHRINKWRAP", "auxiliary_target"): "MESH", ("SURFACE_DEFORM", "target"): "MESH",
                ("ARRAY", "start_cap"): "MESH", ("ARRAY", "end_cap"): "MESH"}


def check(name, condition, detail=None):
    report["checks"][name] = bool(condition)
    assert condition, "%s: %s" % (name, detail) if detail is not None else name


def _is_id(struct):
    while struct is not None:
        if struct.identifier == "ID":
            return True
        struct = struct.base
    return False


def rna_pointers(rna, collections=True, prefix="", depth=4, ancestors=()):
    """Every Object or Collection pointer path of an RNA struct, as ``object_dependencies._structs`` reaches them:
    pointer properties, and those of nested non-ID structs (and, with ``collections``, collection items), four
    levels deep. A struct type already on the path is not entered again (a cycle)."""
    found, ancestors = [], ancestors + (rna.identifier,)
    for prop in rna.properties:
        if prop.identifier == "rna_type":
            continue
        if prop.type == "POINTER" and prop.fixed_type.identifier in ("Object", "Collection"):
            found.append(prefix + prop.identifier)
        elif prop.type in ("POINTER", "COLLECTION") and depth > 0 and not _is_id(prop.fixed_type):
            if (prop.type == "COLLECTION" and not collections) or prop.fixed_type.identifier in ancestors:
                continue
            step = prefix + prop.identifier + ("[]." if prop.type == "COLLECTION" else ".")
            found += rna_pointers(prop.fixed_type, collections, step, depth - 1, ancestors)
    return sorted(found)


def clear():
    for collection in (bpy.data.objects, bpy.data.meshes, bpy.data.armatures, bpy.data.lattices, bpy.data.curves,
                       bpy.data.actions, bpy.data.cameras, bpy.data.lights):
        for item in list(collection):
            collection.remove(item)
    scene.frame_set(1)


def link(obj):
    scene.collection.objects.link(obj)
    return obj


def mesh_object(name, size=1.0, cuts=4):
    import bmesh
    data = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=size)
    bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=cuts, use_grid_fill=True)
    bm.to_mesh(data)
    bm.free()
    return link(bpy.data.objects.new(name, data))


def new_object(kind, name):
    if kind == "EMPTY":
        return link(bpy.data.objects.new(name, None))
    if kind == "MESH":
        return mesh_object(name, cuts=1)
    if kind == "ARMATURE":
        return link(bpy.data.objects.new(name, bpy.data.armatures.new(name)))
    if kind == "LATTICE":
        return link(bpy.data.objects.new(name, bpy.data.lattices.new(name)))
    if kind == "CURVE":
        data = bpy.data.curves.new(name, "CURVE")
        data.splines.new("POLY").points.add(1)
        return link(bpy.data.objects.new(name, data))
    raise AssertionError(kind)


def evaluated(obj):
    graph = bpy.context.evaluated_depsgraph_get()
    item = obj.evaluated_get(graph)
    mesh = item.to_mesh()
    coords = [tuple(round(value, 5) for value in item.matrix_world @ vertex.co) for vertex in mesh.vertices]
    item.to_mesh_clear()
    return coords


def pinned_rna():
    """The pointer set read from bpy equals the recorded one, so a new or renamed pointer is reviewed here."""
    holder = mesh_object("Holder", cuts=0)
    found = {}
    for item in bpy.types.Modifier.bl_rna.properties["type"].enum_items:
        try:
            modifier = holder.modifiers.new(item.identifier, item.identifier)
        except (RuntimeError, TypeError):
            continue  # Not a mesh modifier (Grease Pencil, Volume): ``still`` never calls those objects still.
        if modifier is None:
            continue
        paths = rna_pointers(modifier.bl_rna)
        if paths:
            found[item.identifier] = paths
        # The cached view the walk reads is exactly the live RNA's pointer list.
        cached = [prop.identifier for prop in rna_schema.pointer_properties(modifier)]
        live = [prop.identifier for prop in modifier.bl_rna.properties
                if prop.type in ("POINTER", "COLLECTION") and prop.identifier != "rna_type"]
        check("cached_view_is_live_rna:" + item.identifier, cached == live, (cached, live))
        holder.modifiers.remove(modifier)
    check("modifier_pointers_match_rna", found == RECORDED_MODIFIER_POINTERS,
          {"added": {k: v for k, v in found.items() if RECORDED_MODIFIER_POINTERS.get(k) != v},
           "removed": sorted(set(RECORDED_MODIFIER_POINTERS) - set(found))})
    data = {"MESH": bpy.data.meshes.new("PinMesh"), "CAMERA": bpy.data.cameras.new("PinCamera")}
    data.update({"LIGHT_" + kind: bpy.data.lights.new("Pin" + kind, kind) for kind in ("POINT", "SUN", "SPOT", "AREA")})
    data.update(ARMATURE=bpy.data.armatures.new("PinRig"), LATTICE=bpy.data.lattices.new("PinCage"),
                CURVE=bpy.data.curves.new("PinPath", "CURVE"))
    found_data = {name: rna_pointers(block.bl_rna, collections=False) for name, block in data.items()}
    check("data_pointers_match_rna", found_data == RECORDED_DATA_POINTERS, found_data)


def every_pointer_followed():
    """Each pointer of each frame-independent modifier is followed, and a keyed target makes the mesh time-dependent."""
    for kind, paths in sorted(RECORDED_MODIFIER_POINTERS.items()):
        if kind not in FRAME_INDEPENDENT_MODIFIERS:
            continue
        for path in paths:
            check("simple_path:%s.%s" % (kind, path), "." not in path and "[" not in path)
            clear()
            target_type = TARGET_TYPES.get((kind, path), "EMPTY")
            target = new_object(target_type, "Target")
            deformed = mesh_object("Deformed")
            modifier = deformed.modifiers.new(kind, kind)
            setattr(modifier, path, target)
            check("pointer_set:%s.%s" % (kind, path), getattr(modifier, path) == target)
            check("followed:%s.%s" % (kind, path), target in revision_time._read_objects(deformed))
            # An unkeyed target of any followed type keeps the mesh still (judged by rules, not by its type).
            check("still_target_keeps_still:%s.%s" % (kind, path), revision_time.still(deformed))
            target.location = (0, 0, 0)
            target.keyframe_insert("location", frame=1)
            target.location = (0, 0, 1)
            target.keyframe_insert("location", frame=30)
            check("keyed_target_makes_dependent:%s.%s" % (kind, path), not revision_time.still(deformed))
    clear()
    camera = link(bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera")))
    focus = new_object("EMPTY", "Focus")
    camera.data.dof.focus_object = focus
    check("camera_focus_followed", focus in revision_time._read_objects(camera))


def rig_skinned(segments):
    armature = bpy.data.armatures.new("Rig")
    rig = link(bpy.data.objects.new("Rig", armature))
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    bone = armature.edit_bones.new("Bone")
    bone.head, bone.tail = (0, 0, -1), (0, 0, 1)
    bone.bbone_segments = segments
    bpy.ops.object.mode_set(mode="OBJECT")
    skin = mesh_object("Skin")
    group = skin.vertex_groups.new(name="Bone")
    group.add(list(range(len(skin.data.vertices))), 1.0, "REPLACE")
    skin.modifiers.new("Armature", "ARMATURE").object = rig
    return rig, skin


def revision_follows_frame(name, deformed):
    """Frame 1 -> 30: the evaluated mesh changes, so its exact revision must too."""
    scene.frame_set(1)
    before, shape = queries.object_revision(window, deformed), evaluated(deformed)
    scene.frame_set(30)
    after, moved = queries.object_revision(window, deformed), evaluated(deformed) != shape
    check("deforms:" + name, moved)
    check("revision_changes:" + name, before != after)
    scene.frame_set(1)
    check("revision_returns:" + name, queries.object_revision(window, deformed) == before)


def recorded_scenes():
    """The recorded holes: a keyed B-Bone curve on the rig, a lattice's keyed shape key (unparented meshes)."""
    clear()
    rig, skin = rig_skinned(segments=8)
    bone = rig.pose.bones["Bone"]
    for frame, value in ((1, 0.0), (30, 1.5)):
        bone.bbone_curveinx = bone.bbone_curveoutx = value
        bone.keyframe_insert("bbone_curveinx", frame=frame)
        bone.keyframe_insert("bbone_curveoutx", frame=frame)
    revision_follows_frame("keyed_bbone_curve", skin)
    clear()
    lattice = new_object("LATTICE", "Cage")
    lattice.scale = (1.5, 1.5, 1.5)
    deformed = mesh_object("InCage")
    deformed.modifiers.new("Lattice", "LATTICE").object = lattice
    lattice.shape_key_add(name="Basis")
    block = lattice.shape_key_add(name="Bend")
    for point in block.data:
        point.co = (point.co[0] * 2.0, point.co[1], point.co[2])
    for frame, value in ((1, 0.0), (30, 1.0)):
        block.value = value
        block.keyframe_insert("value", frame=frame)
    revision_follows_frame("keyed_lattice_shape_key", deformed)
    # A still prop on a still deformer keeps its frame-free revision (the reason ``still`` exists).
    clear()
    origin = new_object("EMPTY", "Origin")
    prop = mesh_object("Prop")
    prop.modifiers.new("SimpleDeform", "SIMPLE_DEFORM").origin = origin
    scene.frame_set(1)
    before = queries.object_revision(window, prop)
    scene.frame_set(30)
    check("still_deformer_keeps_frame_free_revision", queries.object_revision(window, prop) == before)


def revision_follows_edit(name, obj, edit, deformed=None):
    """At one frame, ``edit`` changes what ``obj`` evaluates to: its exact revision must change (and, given
    ``deformed``, the revision of the mesh it deforms must too)."""
    watched = [("own", obj)] + ([("deformed", deformed)] if deformed is not None else [])
    before = {role: queries.object_revision(window, item) for role, item in watched}
    shape = evaluated(deformed) if deformed is not None else None
    edit()
    bpy.context.view_layer.update()
    if deformed is not None:
        check("deforms:" + name, evaluated(deformed) != shape)
    for role, item in watched:
        check("revision_changes:%s:%s" % (name, role), queries.object_revision(window, item) != before[role])


def keyed_shape(owner, scale=2.0):
    """A Basis and one shape that moves every point along X; returns the shape's key block."""
    owner.shape_key_add(name="Basis")
    block = owner.shape_key_add(name="Shape")
    for point in block.data:
        point.co = (point.co[0] * scale + 0.25, point.co[1], point.co[2])
    block.value = 0.0
    return block


def shape_keys_in_revisions():
    """A lattice's and a curve's own shape keys are part of their revisions (and of the mesh they deform), as a
    mesh's are; so are the Key's own settings that change the result (absolute keys and their evaluation time)."""
    clear()
    lattice = new_object("LATTICE", "Cage")
    lattice.scale = (1.5, 1.5, 1.5)
    deformed = mesh_object("InCage")
    deformed.modifiers.new("Lattice", "LATTICE").object = lattice
    block = keyed_shape(lattice)
    bpy.context.view_layer.update()
    revision_follows_edit("lattice_shape_value", lattice, lambda: setattr(block, "value", 1.0), deformed)
    revision_follows_edit("lattice_shape_mute", lattice, lambda: setattr(block, "mute", True), deformed)
    clear()
    data = bpy.data.curves.new("Path", "CURVE")
    data.dimensions = "3D"
    spline = data.splines.new("POLY")
    spline.points.add(3)
    for index, point in enumerate(spline.points):
        point.co = (index - 1.5, 0, 0, 1)
    path = link(bpy.data.objects.new("Path", data))
    along = mesh_object("AlongPath", size=0.5)
    along.modifiers.new("Curve", "CURVE").object = path
    block = keyed_shape(path)
    for index, point in enumerate(block.data):
        point.co = (point.co[0], 0, (index % 2) * 1.0)
    bpy.context.view_layer.update()
    revision_follows_edit("curve_shape_value", path, lambda: setattr(block, "value", 1.0), along)
    clear()
    mesh = mesh_object("Absolute")
    block = keyed_shape(mesh)
    mesh.data.shape_keys.use_relative = False
    bpy.context.view_layer.update()
    revision_follows_edit("mesh_absolute_eval_time", mesh, lambda: setattr(mesh.data.shape_keys, "eval_time", 10.0))
    revision_follows_edit("mesh_use_relative", mesh, lambda: setattr(mesh.data.shape_keys, "use_relative", True))


def frames_agree(obj):
    """The evaluated state at frames 1, 30 and 30.5 (world matrix, and the evaluated mesh of a mesh)."""
    states = []
    for frame, sub in ((1, 0.0), (30, 0.0), (30, 0.5)):
        scene.frame_set(frame, subframe=sub)
        matrix = [tuple(round(value, 5) for value in row) for row in obj.evaluated_get(
            bpy.context.evaluated_depsgraph_get()).matrix_world]
        states.append((matrix, evaluated(obj) if obj.type == "MESH" else None))
    scene.frame_set(1)
    return all(state == states[0] for state in states)


def stillness_oracle():
    """Whatever ``still`` calls still is the same at every frame: across scenes built to tempt it (unanimated and
    keyed rigs, lattices and curves as deformers and parents, a path-parented child, a driven deformer), a still
    answer must match frame-invariant evaluation. Non-still answers are allowed to be conservative."""
    scenes = {}

    def rig_scene(keyed, constrained=False):
        rig, skin = rig_skinned(segments=4)
        bone = rig.pose.bones["Bone"]
        bone.rotation_mode = "XYZ"
        bone.rotation_euler = (0.4, 0, 0)
        if keyed:
            bone.keyframe_insert("rotation_euler", frame=1)
            bone.rotation_euler = (0.9, 0, 0)
            bone.keyframe_insert("rotation_euler", frame=30)
        if constrained:
            goal = new_object("EMPTY", "Goal")
            goal.keyframe_insert("location", frame=1)
            goal.location = (1, 0, 2)
            goal.keyframe_insert("location", frame=30)
            constraint = bone.constraints.new("DAMPED_TRACK")
            constraint.target = goal
        child = new_object("EMPTY", "BoneChild")
        child.parent, child.parent_type, child.parent_bone = rig, "BONE", "Bone"
        return [skin, child]

    def path_scene(animated):
        data = bpy.data.curves.new("Path", "CURVE")
        data.dimensions, data.use_path = "3D", True
        spline = data.splines.new("POLY")
        spline.points.add(3)
        for index, point in enumerate(spline.points):
            point.co = (index * 2.0, 0, 0, 1)
        path = link(bpy.data.objects.new("Path", data))
        if animated:
            data.eval_time = 0.0
            data.keyframe_insert("eval_time", frame=1)
            data.eval_time = 100.0
            data.keyframe_insert("eval_time", frame=30)
        child = new_object("EMPTY", "OnPath")
        child.parent = path
        along = mesh_object("Along", size=0.5)
        along.modifiers.new("Curve", "CURVE").object = path
        return [child, along]

    def lattice_scene(keyed):
        lattice = new_object("LATTICE", "Cage")
        lattice.scale = (1.5, 1.5, 1.5)
        inside = mesh_object("Inside")
        inside.modifiers.new("Lattice", "LATTICE").object = lattice
        if keyed:
            block = keyed_shape(lattice)
            block.keyframe_insert("value", frame=1)
            block.value = 1.0
            block.keyframe_insert("value", frame=30)
        return [inside]

    builders = {"rig_unanimated": lambda: rig_scene(False), "rig_keyed": lambda: rig_scene(True),
                "rig_pose_constraint_on_keyed_goal": lambda: rig_scene(False, constrained=True),
                "path_unanimated": lambda: path_scene(False), "path_keyed": lambda: path_scene(True),
                "lattice_unanimated": lambda: lattice_scene(False), "lattice_keyed": lambda: lattice_scene(True)}
    for name, build in builders.items():
        clear()
        for item in build():
            bpy.context.view_layer.update()
            is_still = revision_time.still(item)
            scenes["%s:%s" % (name, item.name)] = is_still
            if is_still:
                check("still_means_frame_invariant:%s:%s" % (name, item.name), frames_agree(item))
    # A Python-defined pointer (an add-on's bpy.props) to an animated datablock: Blender builds the target into the
    # graph but relates nothing to the owner (deg_builder_relations.cc build_idproperties calls build_id only), so
    # the owner's evaluation is frame-invariant and the walk may skip it. Checked against evaluation, not the walk.
    clear()
    bpy.types.Object.angelo_test_pointer = bpy.props.PointerProperty(type=bpy.types.Object)
    bpy.types.Object.angelo_test_collection = bpy.props.PointerProperty(type=bpy.types.Collection)
    try:
        keyed = new_object("EMPTY", "KeyedTarget")
        keyed.keyframe_insert("location", frame=1)
        keyed.location = (0, 0, 3)
        keyed.keyframe_insert("location", frame=30)
        group = bpy.data.collections.new("KeyedGroup")
        group.objects.link(keyed)
        holder = mesh_object("PythonPointerHolder")
        holder.angelo_test_pointer = keyed
        holder.angelo_test_collection = group
        bpy.context.view_layer.update()
        is_still = revision_time.still(holder)
        scenes["python_pointer:holder"] = is_still
        check("python_pointer_owner_frame_invariant", frames_agree(holder))
        if is_still:
            check("still_means_frame_invariant:python_pointer", frames_agree(holder))
    finally:
        del bpy.types.Object.angelo_test_pointer
        del bpy.types.Object.angelo_test_collection
    report["stillness_oracle"] = scenes
    check("unanimated_rig_keeps_skin_still", scenes["rig_unanimated:Skin"])
    check("keyed_rig_skin_not_still", not scenes["rig_keyed:Skin"])
    check("unanimated_lattice_keeps_mesh_still", scenes["lattice_unanimated:Inside"])


def geometry_group(name, node_type, *, object_input=False):
    """A Geometry Nodes group passing its geometry through, with one ``node_type`` node; with ``object_input``, an
    Object interface input feeding that node's Object socket."""
    tree = bpy.data.node_groups.new(name, "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    if object_input:
        tree.interface.new_socket("Target", in_out="INPUT", socket_type="NodeSocketObject")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    group_in, group_out = tree.nodes.new("NodeGroupInput"), tree.nodes.new("NodeGroupOutput")
    tree.links.new(group_in.outputs[0], group_out.inputs[0])
    node = tree.nodes.new(node_type)
    socket = next(item for item in node.inputs if item.type == "OBJECT")
    if object_input:
        tree.links.new(group_in.outputs["Target"], socket)
    return tree, node, socket


def keyed_empty(name):
    item = new_object("EMPTY", name)
    item.keyframe_insert("location", frame=1)
    item.location = (0, 0, 2)
    item.keyframe_insert("location", frame=30)
    return item


def breakit_regressions():
    """Findings of the 2026-11-05 break-it review of this branch, each failing on the commit before its fix.

    Runs first, in a fresh process: whether a pointer could lead to an Object was once decided from the Python
    classes Blender had created so far (F1). A Geometry Nodes modifier's inputs are structs Blender defines at run
    time (F2). A lattice's or curve's shape-key pin and Key animation, a large lattice's keys, and a mesh key's
    select and lock flags were outside the revisions (F3, F5, F6, selection review H4)."""
    clear()
    target = keyed_empty("Target")
    tree, node, socket = geometry_group("ReadsObject", "GeometryNodeObjectInfo")
    socket.default_value = target
    check("F1_node_socket_object_followed", target in object_dependencies.object_pointers(node))
    host = mesh_object("Host")
    host.modifiers.new("Nodes", "NODES").node_group = tree
    camera = link(bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera")))
    camera.parent, camera.parent_type = host, "VERTEX"
    walked = object_dependencies.walk(camera, 64, roles=(object_dependencies.PLACEMENT,))
    check("F1_vertex_parent_graph_reaches_target", target in walked.objects, [item.name for item in walked.objects])
    clear()
    rig = keyed_empty("Driver")
    tree, _node, _socket = geometry_group("InputReader", "GeometryNodeObjectInfo", object_input=True)
    reader = mesh_object("Reader")
    modifier = reader.modifiers.new("Nodes", "NODES")
    modifier.node_group = tree
    identifier = next(item.identifier for item in tree.interface.items_tree if getattr(item, "socket_type", "") == "NodeSocketObject")
    getattr(modifier.properties.inputs, identifier).value = rig  # Blender 5.2's modifier interface inputs.
    check("F2_modifier_input_object_followed", rig in object_dependencies.object_pointers(modifier))
    check("F2_modifier_input_on_keyed_object_not_still", not revision_time.still(reader))
    clear()
    bone_tree, bone_node, bone_socket = geometry_group("BoneReader", "GeometryNodeBoneInfo") if hasattr(
        bpy.types, "GeometryNodeBoneInfo") else (None, None, None)
    if bone_tree is not None:
        bone_socket.default_value = keyed_empty("BoneSource")
        mesh_object("BoneUser").modifiers.new("Nodes", "NODES").node_group = bone_tree
        check("graph_reading_another_id_not_still", not revision_time.still(scene.objects["BoneUser"]))
    # Round-2 R5: an Object input that is unlinked and empty reads nothing (an unused Switch set to Object), so it
    # leaves the graph time-free; the same Switch holding an object reads it.
    clear()
    from angelo_agent.program_frames import _graph_frame_independent, MAX_FRAME_SCAN_NODES
    switch_tree = bpy.data.node_groups.new("UnusedSwitch", "GeometryNodeTree")
    switch = switch_tree.nodes.new("GeometryNodeSwitch")
    switch.input_type = "OBJECT"
    object_inputs = [item for item in switch.inputs if item.type == "OBJECT"]
    check("R5_switch_has_object_inputs", bool(object_inputs))
    check("R5_empty_object_input_reads_nothing",
          _graph_frame_independent(switch_tree, set(), [MAX_FRAME_SCAN_NODES]))
    object_inputs[0].default_value = new_object("EMPTY", "Held")
    check("R5_object_input_holding_an_object_reads_it",
          not _graph_frame_independent(switch_tree, set(), [MAX_FRAME_SCAN_NODES]))
    # F3: pinning one key on a lattice changes the mesh it deforms.
    clear()
    lattice = new_object("LATTICE", "Cage")
    lattice.scale = (1.5, 1.5, 1.5)
    deformed = mesh_object("InCage")
    deformed.modifiers.new("Lattice", "LATTICE").object = lattice
    keyed_shape(lattice)
    lattice.active_shape_key_index = 1
    bpy.context.view_layer.update()
    revision_follows_edit("F3_lattice_pinned_key", lattice, lambda: setattr(lattice, "show_only_shape_key", True), deformed)
    # F5: the lattice's Key animation is in its revision (a key edited at another frame).
    clear()
    lattice = new_object("LATTICE", "Cage")
    block = keyed_shape(lattice)
    block.keyframe_insert("value", frame=1)
    block.keyframe_insert("value", frame=30)
    scene.frame_set(1)
    bpy.context.view_layer.update()
    before = queries.object_revision(window, lattice)
    curve = lattice.data.shape_keys.animation_data.action.layers[0].strips[0].channelbags[0].fcurves[0] if hasattr(
        lattice.data.shape_keys.animation_data.action, "layers") else lattice.data.shape_keys.animation_data.action.fcurves[0]
    curve.keyframe_points[-1].co.y = 0.8
    curve.update()
    bpy.context.view_layer.update()
    check("F5_lattice_key_animation_in_revision", queries.object_revision(window, lattice) != before)
    # F6: a 16-cubed lattice with four keys deforming ten meshes stays exact, read once a pass.
    clear()
    data = bpy.data.lattices.new("BigCage")
    data.points_u = data.points_v = data.points_w = 16
    big = link(bpy.data.objects.new("BigCage", data))
    for index in range(4):
        keyed_shape(big, scale=1.0 + 0.1 * index)
    meshes = []
    for index in range(10):
        item = mesh_object("Big%d" % index, cuts=2)
        item.modifiers.new("Lattice", "LATTICE").object = big
        meshes.append(item)
    bpy.context.view_layer.update()
    started = time.perf_counter()
    check("F6_large_lattice_exact", bool(queries.object_revision(window, big)))
    report["timings"]["large_lattice_revision_ms"] = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    check("F6_large_lattice_dependents_exact", all(queries.object_revision(window, item) for item in meshes))
    report["timings"]["large_lattice_dependent_revision_ms"] = (time.perf_counter() - started) * 1000 / len(meshes)
    # Round-2 R1: inside one pass, a dependent read second is charged what it is charged alone (a shared Key's
    # reads replay their work), so an object is exact in a pass exactly when it is alone.
    from angelo_agent.revision_pass import RevisionPass
    charged, original_budget = [], queries._read_budget

    def counting_budget():
        work = original_budget()
        start = (work.elements, work.items, work.packed)
        charged.append((work, start))
        return work

    def spent(index):
        work, start = charged[index]
        return (start[0] - work.elements, start[1] - work.items, start[2] - work.packed)
    queries._read_budget = counting_budget
    try:
        queries.object_revision(window, meshes[1])
        alone = spent(-1)
        pass_ = RevisionPass(window)
        pass_(meshes[0])
        pass_(meshes[1])
        in_pass = spent(-1)
    finally:
        queries._read_budget = original_budget
    check("R1_pass_charges_equal_standalone", alone == in_pass, (alone, in_pass))
    # Selection review H4: shape-key operators act on selected keys, so a mesh key's select and lock are bound.
    clear()
    mesh = mesh_object("Keys")
    block = keyed_shape(mesh)
    bpy.context.view_layer.update()
    for flag in ("select", "lock_shape"):
        if flag in block.bl_rna.properties:
            revision_follows_edit("H4_mesh_key_" + flag, mesh, lambda flag=flag: setattr(block, flag, not getattr(block, flag)))
    # The owner settings list is every writable Object setting about shape keys.
    try:
        from angelo_agent.shape_key_state import OWNER_SETTINGS
    except ImportError:
        OWNER_SETTINGS = ()
    writable = sorted(prop.identifier for prop in bpy.types.Object.bl_rna.properties
                      if "shape_key" in prop.identifier and not prop.is_readonly)
    check("owner_shape_key_settings_match_rna", writable == sorted(OWNER_SETTINGS), writable)


def _evaluation_properties(item):
    """Live RNA properties that can feed evaluation: on a type ``bpy.types`` names, not the Python-defined ones
    (Blender relates no ID-property pointer to its owner's evaluation); on a struct defined at run time, all."""
    named = getattr(getattr(bpy.types, item.bl_rna.identifier, None), "bl_rna", None)
    compiled = named is not None and named.as_pointer() == item.bl_rna.as_pointer()
    return [prop for prop in item.bl_rna.properties if not (compiled and prop.is_runtime)]


def reference_pointers(struct, collections=True, depth=4):
    """The Objects ``struct`` points at, read from live RNA with no cache and no pruning (the walk before
    rna_schema, over properties that can feed evaluation): the oracle the cached, pruned walk must equal."""
    found, structs = [], []

    def visit(item, level):
        structs.append(item)
        if level <= 0:
            return
        for prop in _evaluation_properties(item):
            if prop.identifier == "rna_type" or prop.type not in ("POINTER", "COLLECTION"):
                continue
            if prop.type == "COLLECTION" and not collections:
                continue
            value = getattr(item, prop.identifier, None)
            for child in (list(value or ()) if prop.type == "COLLECTION" else [value]):
                if child is not None and getattr(child, "id_data", None) is not child:
                    visit(child, level - 1)
    visit(struct, depth)
    for item in structs:
        for prop in _evaluation_properties(item):
            kind = prop.fixed_type.identifier if prop.type == "POINTER" else None
            value = getattr(item, prop.identifier, None) if kind in ("Object", "Collection") else None
            if value is not None:
                found.extend(list(value.all_objects) if kind == "Collection" else [value])
    return sorted({item.name for item in found})


def pruned_walk_matches_reference():
    """Over every mesh modifier type (each Object pointer set), every object constraint type (each target set), the
    data of every still-able and followed type, and Geometry Nodes inputs and sockets, the walk finds exactly the
    Objects the unpruned reference does."""
    clear()
    target = new_object("EMPTY", "Target")
    holder = mesh_object("Holder", cuts=0)
    compared = 0
    for item in bpy.types.Modifier.bl_rna.properties["type"].enum_items:
        try:
            modifier = holder.modifiers.new(item.identifier, item.identifier)
        except (RuntimeError, TypeError):
            continue
        if modifier is None:
            continue
        for path in RECORDED_MODIFIER_POINTERS.get(item.identifier, ()):
            if "." not in path and "[" not in path:
                try:
                    setattr(modifier, path, target)
                except (TypeError, AttributeError, ValueError):
                    pass
        check("walk_matches_reference:modifier:" + item.identifier, sorted(
            {obj.name for obj in object_dependencies.object_pointers(modifier)}) == reference_pointers(modifier))
        compared += 1
        holder.modifiers.remove(modifier)
    owner = new_object("EMPTY", "Owner")
    for item in bpy.types.Constraint.bl_rna.properties["type"].enum_items:
        try:
            constraint = owner.constraints.new(item.identifier)
        except (RuntimeError, TypeError):
            continue
        for name in ("target", "pole_target", "space_object"):
            if hasattr(constraint, name):
                try:
                    setattr(constraint, name, target)
                except (TypeError, AttributeError, ValueError):
                    pass
        if hasattr(constraint, "targets") and hasattr(constraint.targets, "new"):
            constraint.targets.new().target = target
        check("walk_matches_reference:constraint:" + item.identifier, sorted(
            {obj.name for obj in object_dependencies.object_pointers(constraint)}) == reference_pointers(constraint))
        compared += 1
        owner.constraints.remove(constraint)
    for name, data in (("camera", bpy.data.cameras.new("C")), ("curve", bpy.data.curves.new("K", "CURVE")),
                       ("light", bpy.data.lights.new("L", "SPOT")), ("lattice", bpy.data.lattices.new("T")),
                       ("armature", bpy.data.armatures.new("A")), ("mesh", bpy.data.meshes.new("M"))):
        if name == "camera":
            data.dof.focus_object = target
        if name == "curve":
            data.bevel_mode, data.bevel_object = "OBJECT", target
        check("walk_matches_reference:data:" + name, sorted({obj.name for obj in object_dependencies.object_pointers(
            data, collections=False)}) == reference_pointers(data, collections=False))
        compared += 1
    tree, node, socket = geometry_group("Compared", "GeometryNodeObjectInfo", object_input=True)
    modifier = holder.modifiers.new("Nodes", "NODES")
    modifier.node_group = tree
    identifier = next(item.identifier for item in tree.interface.items_tree if getattr(item, "socket_type", "") == "NodeSocketObject")
    getattr(modifier.properties.inputs, identifier).value = target
    for label_, struct in (("nodes_modifier", modifier), ("object_info_node", node)):
        check("walk_matches_reference:" + label_, sorted({obj.name for obj in object_dependencies.object_pointers(
            struct)}) == reference_pointers(struct))
        compared += 1
    report["walk_reference_compared"] = compared


def timings(count=1000):
    """``still`` over a pass of modified props (each with a Bevel and a Weighted Normal), and over cameras."""
    clear()
    props = []
    for index in range(count):
        prop = link(bpy.data.objects.new("Prop%d" % index, bpy.data.meshes.new("Prop%d" % index)))
        prop.modifiers.new("Bevel", "BEVEL")
        prop.modifiers.new("WeightedNormal", "WEIGHTED_NORMAL")
        props.append(prop)
    started = time.perf_counter()
    memo = {}
    for prop in props:
        revision_time.still_in_pass(prop, memo)
    report["timings"]["still_per_modified_prop_us"] = (time.perf_counter() - started) / count * 1e6


def run_checks():
    breakit_regressions()
    pruned_walk_matches_reference()
    pinned_rna()
    every_pointer_followed()
    recorded_scenes()
    shape_keys_in_revisions()
    stillness_oracle()
    timings()
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
(OUT / "revision-deformers.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_REVISION_DEFORMERS", json.dumps({key: report.get(key) for key in ("success", "paid_requests", "timings")}),
      flush=True)
assert report.get("success"), report.get("error")
