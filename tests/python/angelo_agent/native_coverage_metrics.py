# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Coverage metrics for doc/angelo/native_coverage.rst, measured in a real Blender.

Run with a disposable instance (no providers, no network, no scene changes)::

    Angelo --background --factory-startup --disable-autoexec --python-exit-code 1 \
      --python tests/python/angelo_agent/native_coverage_metrics.py -- [--json OUT]

It reports three metrics, each with its denominator:

* operators: the installed ``bpy.ops`` operators, split into editor chrome
  (``UI_MODULES`` and every ``view3d``/``outliner`` operator except
  ``VIEW3D_AGENT_OPERATORS``), out-of-scope domains (``OUT_OF_SCOPE_MODULES``)
  and the agent-relevant rest; ``COVERED_OPERATORS`` lists, by hand, each
  agent-relevant operator whose effect a native action or tool provides, and
  ``operator_run`` adds every allow-listed operator the installed-RNA rules
  admit whose poll passes on a staged sample object (``operator_run_metrics``);
* properties: editable leaf RNA settings reachable from each ID type through
  nested non-ID structs (``MAX_DEPTH`` levels, no collections), and how many of
  them the typed action kinds write (``TYPED_WRITES``) and the ``property_set``
  allow-list reaches (``action_properties.PROPERTY_PATHS`` when present);
  ParticleSettings counts the paths ``particle_set`` writes;
* bones: editable leaf settings of Bone, EditBone and PoseBone (collection
  members, so outside the property denominator) and how many ``bone_set``
  writes (``bone_metrics``);
* type counts: modifier and constraint types, and Shader and Geometry node
  types (allow-listed by the closed node sets, not generic).

Usage-weighted coverage is measured on the host from recorded transcripts; it
is not computed here. Counting is a classification, not a proof of behaviour.
"""

import argparse
import json
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))

UI_MODULES = frozenset({"wm", "screen", "ui", "view2d", "file", "text", "text_editor", "console", "info",
                        "preferences", "extensions", "buttons", "uilist", "spreadsheet", "gizmogroup",
                        "workspace", "script", "ed", "asset", "brush", "paintcurve", "palette"})
OUT_OF_SCOPE_MODULES = frozenset({"sequencer", "clip", "mask", "sound"})
# view3d and outliner are editor chrome except these scene-changing operators.
VIEW3D_AGENT_OPERATORS = frozenset({
    "view3d.object_as_camera", "view3d.camera_to_view", "view3d.camera_to_view_selected",
    "view3d.snap_selected_to_cursor", "view3d.snap_selected_to_grid", "view3d.snap_selected_to_active",
    "view3d.snap_cursor_to_selected", "view3d.snap_cursor_to_center", "view3d.snap_cursor_to_active",
    "view3d.snap_cursor_to_grid"})
CHROME_EDITORS = frozenset({"view3d", "outliner"})

# Phase 5 animation editing: graph, action, anim, nla and marker operators by effect (the
# editor operators act on a selection in an editor; the kinds name channels, frames and names).
_EDIT = "keyframe_edit (%s)"
ANIMATION_OPERATORS = {
    "graph.extrapolation_type": "keyframe_configure (partial: constant and linear; cyclic is fcurve_modifier CYCLES)",
    "action.extrapolation_type": "keyframe_configure (partial: constant and linear)",
    "action.keyframe_type": "keyframe_configure (key_type)",
    "anim.channels_setting_enable": "keyframe_configure (partial: mute)",
    "anim.channels_setting_disable": "keyframe_configure (partial: mute)",
    "anim.channels_setting_toggle": "keyframe_configure (partial: mute)",
    **{name: _EDIT % "DELETE" for name in ("graph.delete", "action.delete", "anim.keyframe_clear_v3d",
                                           "anim.keyframe_clear_button", "anim.keyframe_delete_button",
                                           "anim.channels_delete")},
    "anim.keyframe_delete_v3d": _EDIT % "partial: DELETE at explicit frames",
    "graph.clean": _EDIT % "CLEAN", "action.clean": _EDIT % "CLEAN",
    "graph.decimate": _EDIT % "partial: DECIMATE in value space, not Blender's Bezier fit",
    "graph.smooth": _EDIT % "SMOOTH", "graph.euler_filter": _EDIT % "EULER_FILTER",
    "graph.bake_keys": _EDIT % "partial: BAKE over a frame range", "action.bake_keys": _EDIT % "partial: BAKE over a range",
    "anim.channels_bake": _EDIT % "BAKE",
    "graph.snap": _EDIT % "partial: SNAP to the nearest frame or flat handles",
    "action.snap": _EDIT % "partial: SNAP to the nearest frame",
    "graph.mirror": _EDIT % "partial: SCALE by -1 about a frame or value", "action.mirror": _EDIT % "partial: SCALE by -1",
    "graph.time_offset": _EDIT % "partial: OFFSET", "graph.scale_average": _EDIT % "partial: SCALE values about a pivot",
    "graph.keyframe_insert": _EDIT % "partial: INSERT at explicit frames", "action.keyframe_insert": _EDIT % "partial: INSERT",
    "graph.click_insert": _EDIT % "INSERT with an explicit value",
    "graph.fmodifier_add": "fcurve_modifier (partial: every type but Envelope)", "graph.fmodifier_delete": "fcurve_modifier",
    **{name: "keyframe_copy (partial: an explicit source object and channels, no clipboard)" for name in (
        "graph.copy", "graph.paste", "action.copy", "action.paste")},
    **{name: "keyframe_copy (partial: onto the same object with an offset)" for name in (
        "graph.duplicate", "graph.duplicate_move", "action.duplicate", "action.duplicate_move")},
    "action.new": "action_set (NEW)", "action.unlink": "action_set (UNLINK)", "nla.action_unlink": "action_set (UNLINK)",
    "action.stash": "action_set (UNLINK with stash_previous)", "action.stash_and_create": "action_set (NEW with stash_previous)",
    "anim.slot_new_for_id": "action_set (SLOT new_slot)", "anim.slot_unassign_from_id": "action_set (SLOT, none)",
    "action.push_down": "nla_push_down",
    "nla.tracks_add": "nla_edit (TRACK_ADD)", "nla.tracks_delete": "nla_edit (TRACK_REMOVE)",
    "nla.delete": "nla_edit (STRIP_REMOVE)", "nla.actionclip_add": "nla_edit (STRIP_ADD)",
    "nla.mute_toggle": "nla_edit (STRIP_SET mute)", "nla.move_up": "nla_edit (partial: STRIP_SET move_to_track)",
    "nla.move_down": "nla_edit (partial: STRIP_SET move_to_track)", "nla.snap": "nla_edit (partial: STRIP_SET frame_start)",
    "nla.clear_scale": "nla_edit (STRIP_SET scale 1)", "nla.action_sync_length": "nla_edit (partial: STRIP_SET action range)",
    "nla.duplicate": "nla_edit (partial: STRIP_ADD of the same Action)",
    "nla.selected_objects_add": "nla_edit (partial: TRACK_ADD creates animation data)",
    "marker.add": "marker_edit (ADD)", "marker.move": "marker_edit (SET frame)", "marker.rename": "marker_edit (SET name)",
    "marker.delete": "marker_edit (REMOVE)", "marker.camera_bind": "marker_edit (BIND_CAMERA)",
    "marker.duplicate": "marker_edit (partial: ADD)",
}
# Agent-relevant operator -> the native action kind or tool that provides its
# effect. "partial" marks a subset (types, modes or options).
COVERED_OPERATORS = {
    **{"mesh.primitive_%s_add" % name: "create_primitive" for name in
       ("cube", "plane", "uv_sphere", "ico_sphere", "cylinder", "cone", "grid", "circle")},
    "transform.translate": "transform", "transform.rotate": "transform", "transform.resize": "transform",
    "object.location_clear": "transform", "object.rotation_clear": "transform", "object.scale_clear": "transform",
    "object.parent_set": "parent_set (partial: object parenting)", "object.parent_clear": "parent_set",
    "mesh.extrude_region": "mesh_extrude", "mesh.extrude_region_move": "mesh_extrude",
    "mesh.inset": "mesh_inset", "mesh.bevel": "mesh_bevel", "mesh.remove_doubles": "mesh_merge_by_distance",
    "mesh.normals_make_consistent": "mesh_recalculate_normals",
    "object.modifier_add": "modifier_add (partial: allow-listed types)",
    "material.new": "material_principled", "object.material_slot_add": "material_principled",
    "object.material_slot_assign": "material_principled",
    "node.add_node": "shader_nodes, geometry_nodes, node_edit (partial: allow-listed node types)",
    "node.link": "shader_nodes, geometry_nodes, node_edit (partial)",
    "node.new_geometry_nodes_modifier": "geometry_nodes",
    "uv.cube_project": "uv_project", "uv.sphere_project": "uv_project",
    "uv.unwrap": "uv_unwrap", "mesh.mark_seam": "uv_unwrap (partial: seams for an unwrap)",
    "object.constraint_add": "constraint_add (partial: allow-listed types)",
    "object.constraint_add_with_targets": "constraint_add (partial)",
    "anim.keyframe_insert": "keyframe_insert (partial: transform channels)",
    "anim.keyframe_insert_by_name": "keyframe_insert (partial)",
    "object.armature_add": "armature_create", "armature.bone_primitive_add": "armature_create",
    "object.vertex_group_add": "armature_bind (partial: bone groups)",
    "object.vertex_group_assign": "armature_bind (partial)",
    "object.camera_add": "camera_create", "object.light_add": "light_create",
    "sculpt.brush_stroke": "angelo_sculpt_batch, angelo_sculpt_program",
    "sculpt.sculptmode_toggle": "angelo_prepare_sculpt", "object.multires_subdivide": "angelo_prepare_sculpt",
    "import_scene.gltf": "angelo_studio_import_3d (partial: ready Studio GLBs)",
    "object.modifier_remove": "modifier_remove", "object.modifier_apply": "modifier_apply (partial: first modifier)",
    "object.modifier_move_to_index": "modifier_move", "object.modifier_move_up": "modifier_move",
    "object.modifier_move_down": "modifier_move", "world.new": "world_configure",
    "view3d.object_as_camera": "property_set (partial: scene camera, not the view)",
    "object.duplicate": "object_duplicate", "object.duplicate_move": "object_duplicate",
    "object.duplicate_move_linked": "object_duplicate (linked)", "object.delete": "object_delete",
    "object.transform_apply": "transform_apply (partial: meshes without children)",
    "object.origin_set": "origin_set (partial: bounds, median, world point)",
    "object.move_to_collection": "collection_link (MOVE)", "object.link_to_collection": "collection_link (LINK)",
    "collection.objects_remove": "collection_link (UNLINK)", "collection.create": "collection_link (create)",
    "object.hide_render_clear_all": "property_set (partial)",
    "anim.keyframe_delete": "keyframe_delete (partial: transform channels)",
    "anim.keyframe_delete_by_name": "keyframe_delete (partial)",
    "graph.interpolation_type": "keyframe_configure", "graph.easing_type": "keyframe_configure",
    "graph.handle_type": "keyframe_configure", "action.interpolation_type": "keyframe_configure",
    "action.easing_type": "keyframe_configure", "action.handle_type": "keyframe_configure",
    "object.shape_key_add": "shape_key_add", "nla.action_pushdown": "nla_push_down",
    "object.empty_add": "create_object", "object.text_add": "create_object",
    "curve.primitive_bezier_curve_add": "create_object", "curve.primitive_bezier_circle_add": "create_object",
    "uv.smart_project": "uv_smart_project", "uv.pack_islands": "uv_pack",
    "mesh.subdivide": "mesh_subdivide", "mesh.delete": "mesh_delete (partial: faces)",
    "object.join": "object_join (partial: meshes)", "mesh.separate": "mesh_separate",
    "object.hide_view_set": "visibility_set", "object.hide_view_clear": "visibility_set",
    "object.hide_collection": "visibility_set (partial: collection eye)",
    "anim.keyframe_insert_button": "property_keyframe (partial: allow-listed settings)",
    "curve.vertex_add": "curve_points (partial: whole splines)", "curve.spline_type_set": "curve_points (partial)",
    "curve.cyclic_toggle": "curve_points (partial)", "curve.handle_type_set": "curve_points (partial: auto, vector)",
    "object.convert": "object_convert (partial: curve and text to mesh)",
    "armature.parent_set": "bone_set (parent, connected)", "armature.parent_clear": "bone_set (unparent)",
    "armature.roll_clear": "bone_set (partial: an explicit roll per bone)",
    "pose.loc_clear": "bone_set (location)", "pose.rot_clear": "bone_set (rotation)",
    "pose.scale_clear": "bone_set (scale)", "pose.transforms_clear": "bone_set (location, rotation, scale)",
    "pose.rotation_mode_set": "bone_set (rotation_mode)",
    "pose.hide": "bone_set (partial: pose_settings hide)", "pose.reveal": "bone_set (partial: pose_settings hide)",
    "armature.hide": "bone_set (partial: settings hide)", "armature.reveal": "bone_set (partial: settings hide)",
    "object.particle_system_add": "modifier_add (PARTICLE_SYSTEM; settings by particle_set)",
    "object.grease_pencil_add": "create_object (partial: a blank object with one layer)",
    **ANIMATION_OPERATORS,
    # node_edit on an existing material, Geometry Nodes or World tree, by effect (frames, mute, groups and
    # interfaces not in Geometry Nodes catalog graphs). Selection, view, viewer, clipboard and modal moves stay out.
    **{"node." + name: "node_edit (%s)" % step for name, step in (
        ("link_make", "link"), ("connect_to_output", "partial: link into the output"), ("links_cut", "unlink"),
        ("links_detach", "detach with reconnect"), ("delete", "remove"), ("delete_reconnect", "remove with reconnect"),
        ("add_reroute", "add NodeReroute, insert"), ("add_color", "partial: add an RGB node and set its colour"),
        ("attach", "parent"), ("detach", "parent to no frame"), ("join", "add NodeFrame, parent"),
        ("join_named", "add a named NodeFrame, parent"), ("parent_set", "parent"),
        ("duplicate", "partial: exact copies only"), ("duplicate_move", "partial: duplicate with an offset"),
        ("duplicate_move_keep_inputs", "partial: duplicate keep_inputs"),
        ("duplicate_move_linked", "partial: a duplicated group node shares its group"),
        ("mute_toggle", "set mute; not in Geometry Nodes"), ("hide_toggle", "set hide"),
        ("options_toggle", "set show_options"), ("preview_toggle", "set show_preview"),
        ("node_copy_color", "partial: set an explicit colour per node"), ("resize", "partial: set width"),
        ("swap_node", "swap"), ("group_make", "partial: group, shader trees"),
        ("group_ungroup", "partial: ungroup, shader trees"),
        ("interface_item_new", "partial: interface ADD of a nested shader group"),
        ("interface_item_remove", "partial: interface REMOVE of a nested shader group"))},
}
# Settings written by typed action kinds, relative to each ID type.
TYPED_WRITES = {"Object": {"location", "rotation_euler", "scale"},
               "World": {"color"}}  # world_configure keeps the viewport colour with the background.
# Light settings live on its subtypes; count the union so subtype settings are not missed.
ID_TYPES = {"Object": ("Object",), "Mesh": ("Mesh",), "Curve": ("Curve",), "TextCurve": ("TextCurve",),
            "Camera": ("Camera",), "Light": ("PointLight", "SunLight", "SpotLight", "AreaLight"),
            "Material": ("Material",), "World": ("World",), "Scene": ("Scene",), "Armature": ("Armature",)}
ID_TYPES["ParticleSettings"] = ("ParticleSettings",)  # particle_set's paths, relative to the settings.
MAX_DEPTH = 3
LEAF_TYPES = frozenset({"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"})


def operator_metrics():
    modules = {}
    for module in dir(bpy.ops):
        if not module.startswith("_"):
            modules[module] = sorted(module + "." + name for name in dir(getattr(bpy.ops, module))
                                     if not name.startswith("_"))
    chrome, out_of_scope, relevant = [], [], []
    for module, operators in modules.items():
        for operator in operators:
            if module in OUT_OF_SCOPE_MODULES:
                out_of_scope.append(operator)
            elif module in UI_MODULES or (module in CHROME_EDITORS and operator not in VIEW3D_AGENT_OPERATORS):
                chrome.append(operator)
            else:
                relevant.append(operator)
    typed = sorted(operator for operator in COVERED_OPERATORS if operator in relevant)
    generic = operator_run_metrics()
    mapped = {name: COVERED_OPERATORS[name] for name in typed}
    for name in generic["poll_passes"] if generic else ():
        if name in relevant:
            mapped[name] = mapped[name] + "; operator_run" if name in mapped else "operator_run"
    by_module = {}
    for operator in relevant:
        by_module[operator.split(".")[0]] = by_module.get(operator.split(".")[0], 0) + 1
    return {"total": sum(len(items) for items in modules.values()), "modules": len(modules),
            "editor_chrome": len(chrome), "out_of_scope": len(out_of_scope), "agent_relevant": len(relevant),
            "covered": len(mapped), "covered_by_typed_kinds": len(typed), "covered_operators": dict(sorted(mapped.items())),
            "operator_run": generic, "missing_mapped_operators": sorted(set(COVERED_OPERATORS) - set(typed)),
            "agent_relevant_by_module": dict(sorted(by_module.items(), key=lambda item: -item[1]))}


def _operator_stage():
    """A private stage with one sample of each operator_run target type (mesh with UV, groups, keys, slots and a
    constraint; Grease Pencil strokes, hair curves and text)."""
    from angelo_agent.action_kernels import StagedActionContext, apply_operation
    scene = bpy.data.scenes.new("__metrics_operators")
    collection = bpy.data.collections.new("__metrics_operators")
    scene.collection.children.link(collection)
    context = StagedActionContext(scene, collection)
    create = lambda kind, params: apply_operation(context, {"kind": kind, "target": None, "parameters": params})
    create("create_primitive", {"primitive": "CUBE", "alias": "mesh"})
    mesh = context.resolve("$mesh")
    mesh.data.uv_layers.new(name="UVMap")
    mesh.vertex_groups.new(name="Group").add([0, 1, 2, 3], .5, "REPLACE")
    mesh.shape_key_add(name="Basis")
    mesh.shape_key_add(name="Key")
    context.track_id(mesh.data.shape_keys)
    for name in ("A", "B"):
        mesh.data.materials.append(context.track_id(bpy.data.materials.new("__metrics_" + name)))
    mesh.active_material_index = 1
    mesh.constraints.new("LIMIT_LOCATION")
    create("create_object", {"type": "CURVE", "alias": "curve"})
    create("create_object", {"type": "LATTICE", "alias": "lattice"})
    create("create_object", {"type": "GREASEPENCIL", "alias": "sketch"})
    context.resolve("$sketch").data.layers[0].frames[0].drawing.add_strokes([4])
    create("create_object", {"type": "TEXT", "alias": "text"})
    hair = context.track_id(bpy.data.hair_curves.new("__metrics_hair"))
    hair.add_curves([4])
    context.add_object(context.track_id(bpy.data.objects.new("__metrics_hair", hair)), "hair")
    create("armature_create", {"alias": "armature", "bones": [{"name": "A", "head": [0, 0, 0], "tail": [0, 0, 1]}]})
    return context


def operator_run_metrics():
    """Allow-listed operators the installed-RNA rules admit, and those whose poll passes on a staged sample."""
    try:
        from angelo_agent import action_operators
        from angelo_agent.action_operator_spec import ALLOWED, OperatorError, target_types
    except ImportError:
        return None
    context = _operator_stage()
    samples = {"MESH": "$mesh", "CURVE": "$curve", "ARMATURE": "$armature", "LATTICE": "$lattice", "GREASEPENCIL": "$sketch",
               "CURVES": "$hair", "FONT": "$text"}
    refused, admitted, polls = {}, [], []
    try:
        for name in sorted(ALLOWED):
            try:
                action_operators.admission(name)
            except OperatorError as error:
                refused[name] = error.code
                continue
            admitted.append(name)
            if action_operators.poll_mode(context, context.resolve(samples[target_types(name)[0]]), name):
                polls.append(name)
    finally:
        bpy.data.batch_remove(ids=list(set(context.owned_ids) | set(context.created_ids)))
    by_module = {}
    for name in polls:
        by_module[name.split(".")[0]] = by_module.get(name.split(".")[0], 0) + 1
    return {"allow_listed": len(ALLOWED), "admitted": len(admitted), "refused_by_rna_rules": refused,
            "poll_passes_count": len(polls), "poll_passes_by_module": by_module, "poll_passes": polls,
            "poll_fails": sorted(set(admitted) - set(polls))}


def _leaf_paths(struct, prefix="", depth=0, seen=None):
    seen = set() if seen is None else seen
    if struct.identifier in seen:
        return set()
    seen = seen | {struct.identifier}
    paths = set()
    for prop in struct.properties:
        if prop.identifier == "rna_type":
            continue
        path = prefix + prop.identifier
        if prop.type in LEAF_TYPES and not prop.is_readonly:
            paths.add(path)
        elif prop.type == "POINTER" and depth < MAX_DEPTH and prop.fixed_type is not None:
            if not _is_id(prop.fixed_type):  # Another ID is its own owner, not this one's setting.
                paths |= _leaf_paths(prop.fixed_type, path + ".", depth + 1, seen)
    return paths


# Where each ID type's settings sit for property_set's generic paths (rna_rules hops).
GENERIC_PREFIX = {"Object": "", "Mesh": "data.", "Curve": "data.", "TextCurve": "data.", "Camera": "data.",
                  "Light": "data.", "Material": "active_material.", "World": "world.", "Scene": "", "Armature": "data."}
GENERIC_PREFIX["ParticleSettings"] = ""  # Written by particle_set on a system's own settings.


def _generic_paths(name, structs):
    """Paths (relative to the ID) the generic rules admit, by the installed RNA definitions."""
    try:
        from angelo_agent.rna_rules import writable_settings
    except ImportError:
        return set()
    prefix = GENERIC_PREFIX[name]
    found = set()
    for struct in structs:
        for entry in writable_settings(getattr(bpy.types, struct).bl_rna, prefix=prefix, max_depth=MAX_DEPTH, limit=5000):
            path = entry["path"][len(prefix):]
            if name != "Scene" or path.split(".")[0] in _scene_roots():
                found.add(path)
    return found


def _scene_roots():
    from angelo_agent.rna_rules import SCENE_ROOTS
    return SCENE_ROOTS


def _is_id(struct):
    while struct is not None:
        if struct.identifier == "ID":
            return True
        struct = struct.base
    return False


def property_metrics():
    try:
        from angelo_agent.action_properties import PROPERTY_PATHS
    except ImportError:
        PROPERTY_PATHS = {}
    # property_set paths relative to the ID type that owns each setting.
    data_owners = {"CURVE": ("Curve",), "FONT": ("TextCurve",), "CAMERA": ("Camera",), "LIGHT": ("Light",)}
    reachable = {}
    for path, (owner, object_types, *_rest) in PROPERTY_PATHS.items():
        if owner == "SCENE":
            reachable.setdefault("Scene", set()).add(path)
        elif object_types is None:
            reachable.setdefault("Object", set()).add(path)
        else:
            for object_type in object_types:
                for id_type in data_owners[object_type]:
                    reachable.setdefault(id_type, set()).add(path[len("data."):])
    # Curve settings are shared by text data (TextCurve derives from Curve).
    reachable.setdefault("TextCurve", set()).update(reachable.get("Curve", set()) - {"dimensions", "use_fill_caps"})
    from angelo_agent.action_camera_light_spec import CAMERA_RNA, LIGHT_RNA
    typed = {key: set(value) for key, value in TYPED_WRITES.items()}
    typed["Camera"] = set(CAMERA_RNA.values())
    typed["Light"] = set(LIGHT_RNA.values())
    result, totals = {}, {"editable": 0, "typed_kinds": 0, "property_set": 0, "either": 0, "generic": 0,
                          "generic_or_typed": 0}
    for name, structs in ID_TYPES.items():
        paths = set().union(*(_leaf_paths(getattr(bpy.types, struct).bl_rna) for struct in structs))
        typed_paths = typed.get(name, set()) & paths
        allowed = reachable.get(name, set()) & paths
        generic = _generic_paths(name, structs) & paths
        row = {"editable": len(paths), "typed_kinds": len(typed_paths), "property_set": len(allowed),
               "either": len(typed_paths | allowed), "generic": len(generic | allowed),
               "generic_or_typed": len(typed_paths | allowed | generic),
               "unknown_allowlisted_paths": sorted(reachable.get(name, set()) - paths)}
        result[name] = row
        for key in totals:
            totals[key] += row[key]
    result["total"] = totals
    return result


# Bone structs live in collections (outside the ID denominator, like modifier settings).
# bone_set's typed fields per struct; its generic paths are rna_rules' less the bone rules.
BONE_TYPED = {"Bone": set(), "EditBone": {"head", "tail", "roll", "use_connect"},
              "PoseBone": {"location", "rotation_quaternion", "rotation_euler", "rotation_axis_angle", "rotation_mode",
                           "scale"}}
OTHER_BONE_HOPS = ("bone.", "parent.", "child.", "bbone_custom_handle_start.", "bbone_custom_handle_end.",
                   "custom_shape_transform.")


def bone_metrics():
    """Editable leaf settings of Bone, EditBone and PoseBone, and how many bone_set writes.

    ``editable`` follows the property rule above (nested non-ID structs, three
    levels), so it also counts paths that hop into another bone (its parent,
    child or B-Bone handle); ``own`` leaves those hops out. EditBone settings are
    the Bone settings (bone_set writes them on the Bone) plus the rest geometry.
    """
    try:
        from angelo_agent.action_bones_spec import POSE_CHANNELS, SELECTION
        from angelo_agent.rna_rules import writable_settings
    except ImportError:
        return {}
    generic = {}
    for name in ("Bone", "PoseBone"):
        generic[name] = {entry["path"] for entry in writable_settings(getattr(bpy.types, name).bl_rna, limit=5000)
                         if entry["path"].split(".")[0] not in SELECTION and entry["path"] not in POSE_CHANNELS}
    generic["EditBone"] = generic["Bone"]
    result, totals = {}, {"editable": 0, "own": 0, "bone_set": 0}
    for name in ("Bone", "EditBone", "PoseBone"):
        paths = _leaf_paths(getattr(bpy.types, name).bl_rna)
        own = {path for path in paths if not path.startswith(OTHER_BONE_HOPS)}
        reached = (generic[name] | BONE_TYPED[name]) & paths
        result[name] = {"editable": len(paths), "own": len(own), "bone_set": len(reached),
                        "own_not_reached": sorted(own - reached)}
        for key in totals:
            totals[key] += result[name][key]
    result["total"] = totals
    return result


def _subclasses(base):
    found, pending = set(), [base]
    while pending:
        for child in pending.pop().__subclasses__():
            if child not in found:
                found.add(child)
                pending.append(child)
    return found


def _generic_types():
    """Types the generic paths accept in this build: created on a scratch mesh, material or node group."""
    from angelo_agent import generic_settings, node_generic
    from angelo_agent.action_schemas import CONSTRAINT_SETTINGS, MODIFIER_SETTINGS
    mesh = bpy.data.meshes.new("__metrics")
    obj = bpy.data.objects.new("__metrics", mesh)
    result = {"modifiers": [], "constraints": [], "shader": [], "geometry": []}
    for item in bpy.types.Modifier.bl_rna.properties["type"].enum_items:
        kind = item.identifier
        if kind in MODIFIER_SETTINGS or kind in generic_settings.DENIED_MODIFIERS:
            continue
        try:
            modifier = obj.modifiers.new(kind, kind)
        except (TypeError, RuntimeError):
            modifier = None
        if modifier is None:
            continue
        try:
            generic_settings.bound_factor(kind, modifier)
            result["modifiers"].append(kind)
        except ValueError:
            pass
        obj.modifiers.remove(modifier)
    for item in bpy.types.Constraint.bl_rna.properties["type"].enum_items:
        if item.identifier not in CONSTRAINT_SETTINGS and item.identifier not in generic_settings.DENIED_CONSTRAINTS:
            try:
                obj.constraints.remove(obj.constraints.new(item.identifier))
                result["constraints"].append(item.identifier)
            except (TypeError, RuntimeError):
                pass
    material = bpy.data.materials.new("__metrics")
    group = bpy.data.node_groups.new("__metrics", "GeometryNodeTree")
    for family, base, tree in (("shader", bpy.types.ShaderNode, None), ("geometry", bpy.types.GeometryNode, group)):
        for cls in sorted(_subclasses(base), key=lambda c: c.__name__):
            name = cls.__name__
            try:
                node_generic.check_type(name, family.upper())
                target = material.node_tree if family == "shader" else tree
                node = target.nodes.new(name)
            except (ValueError, RuntimeError):
                continue
            geometry_out = any(socket.type == "GEOMETRY" for socket in node.outputs)
            if family == "shader" or not geometry_out or name in node_generic.GEOMETRY_BOUNDS \
                    or name in getattr(node_generic, "AREA_BOUNDS", {}):
                result[family].append(name)
            target.nodes.remove(node)
    for block, collection in ((material, bpy.data.materials), (group, bpy.data.node_groups), (obj, bpy.data.objects),
                              (mesh, bpy.data.meshes)):
        collection.remove(block)
    return result


def type_metrics():
    from angelo_agent.action_schemas import (CONSTRAINT_SETTINGS, GEOMETRY_NODE_TYPES, MODIFIER_SETTINGS,
                                             SHADER_NODE_TYPES)
    from angelo_agent.validation_spec import MODIFIER_PROPERTIES
    creatable = set(MODIFIER_SETTINGS) | {"NODES", "ARMATURE"}
    shader = {rna for rna, *_ in SHADER_NODE_TYPES.values()}
    geometry = {rna for rna, *_ in GEOMETRY_NODE_TYPES.values()}
    shader_types = {cls.__name__ for cls in _subclasses(bpy.types.ShaderNode)}
    geometry_types = {cls.__name__ for cls in _subclasses(bpy.types.GeometryNode)}
    generic = _generic_types()
    return {
        "modifiers": {"total": len(bpy.types.Modifier.bl_rna.properties["type"].enum_items),
                      "creatable": sorted(creatable), "verifiable": sorted(MODIFIER_PROPERTIES),
                      "generic_on_mesh": generic["modifiers"]},
        "constraints": {"total": len(bpy.types.Constraint.bl_rna.properties["type"].enum_items),
                        "creatable": sorted(CONSTRAINT_SETTINGS), "generic": generic["constraints"]},
        "shader_nodes_generic": generic["shader"], "geometry_nodes_generic": generic["geometry"],
        "shader_nodes": {"total": len(shader_types), "allow_listed": sorted(shader & shader_types),
                         "generic": False},
        "geometry_nodes": {"total": len(geometry_types), "allow_listed": sorted(geometry & geometry_types),
                           "group_io": sorted(geometry - geometry_types), "generic": False},
    }


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--json")
    args = parser.parse_args(argv)
    report = {"blender": bpy.app.version_string, "operators": operator_metrics(),
              "properties": property_metrics(), "bones": bone_metrics(), "types": type_metrics()}
    text = json.dumps(report, indent=1, sort_keys=True)
    if args.json:
        Path(args.json).write_text(text + "\n", encoding="utf-8")
    print(text)


main()
