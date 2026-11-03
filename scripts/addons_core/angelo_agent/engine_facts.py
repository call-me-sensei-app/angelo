# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The plain description of a scene that ``engine_rules`` checks: what an engine export would carry. Main thread,
read-only.

``gather(scene, view_layer, fmt, options, selection_only, depsgraph)`` describes the objects the exporter would
write with ``options`` (``exported``: glTF writes every object of the scene unless ``use_visible``; FBX every object
of the view layer, so not excluded collections), their transforms, constraints, actions, modifiers, the images
their materials use and their skin weights. Nothing is written to Blender data; costs are bounded per mesh
(``SKIN_VERTICES``) and identical-mesh hashing runs only for meshes whose counts already match.
"""

import hashlib
import json
import math
import os
import re

import bpy

SKIN_VERTICES = 250_000      # Vertices read for weights over all meshes; past it, every n-th vertex (``sampled``).
CAPS = (4, 8, 12)            # The influence limits engine_rules uses (ENGINE_INFLUENCES and glTF's default).
CURVE_TYPES = frozenset({"CURVE", "FONT", "SURFACE", "META"})
COLOUR_INPUTS = frozenset({"Base Color", "Emission Color", "Color", "Subsurface Color", "Sheen Tint", "Coat Tint"})
DATA_INPUTS = frozenset({"Roughness", "Metallic", "Normal", "Alpha", "Specular IOR Level", "Transmission Weight",
                         "Coat Weight", "Coat Roughness", "Coat Normal", "Height", "Strength", "Displacement",
                         "Anisotropic", "Sheen Weight", "IOR"})
DATA_NODES = frozenset({"ShaderNodeNormalMap", "ShaderNodeBump", "ShaderNodeDisplacement",
                        "ShaderNodeVectorDisplacement", "ShaderNodeSeparateColor", "ShaderNodeSeparateRGB",
                        "ShaderNodeSeparateXYZ"})
_ABSOLUTE = re.compile(r"^(/[^/\s]+/[^/]|[A-Za-z]:[\\/]|\\\\|~[/\\])")


GLTF_HELPERS = "glTF_not_exported"   # io_scene_gltf2's BLENDER_GLTF_SPECIAL_COLLECTION: its importer's bone shapes.


def exported_objects(scene, view_layer, fmt, options, selection_only=False):
    """The objects an export with ``options`` writes, as the exporters choose them (Blender 5.2; the glTF exporter
    skips its importer's helper collection, blender/exp/tree.py)."""
    pool = scene.objects if fmt == "GLB" else view_layer.objects
    helpers = bpy.data.collections.get(GLTF_HELPERS) if fmt == "GLB" else None
    out = []
    for obj in pool:
        if helpers is not None and obj.name in helpers.objects:
            continue
        if options.get("use_visible") and not obj.visible_get(view_layer=view_layer):
            continue
        if options.get("use_renderable") and obj.hide_render:
            continue
        if selection_only and not obj.select_get(view_layer=view_layer):
            continue
        out.append(obj)
    return sorted(out, key=lambda obj: obj.name)


def _hidden(obj, view_layer, in_layer):
    return obj.name not in in_layer or not obj.visible_get(view_layer=view_layer)


def action_curves(action):
    """Every F-curve of ``action`` across its layers, strips and slots (Blender 5 channelbags)."""
    for layer in getattr(action, "layers", ()):
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", ()):
                yield from bag.fcurves


def _cyclic(action):
    """True when every animated channel ends where it starts (or loops by a Cycles modifier) and one moves."""
    curves = list(action_curves(action))
    if not curves:
        return False
    if all(any(modifier.type == "CYCLES" for modifier in curve.modifiers) for curve in curves):
        return True
    start, end = action.frame_range
    if end - start < 1:
        return False
    moves = False
    for curve in curves:
        first, last = curve.evaluate(start), curve.evaluate(end)
        if abs(first - last) > 1e-4 * max(1.0, abs(first)):
            return False
        points = curve.keyframe_points
        moves = moves or any(abs(point.co[1] - first) > 1e-4 for point in points)
    return moves


def id_key(block):
    """A data block's name as the findings name it: its name, and its library's file name when it is linked (a local
    and a linked block may share a name)."""
    library = getattr(block, "library", None)
    return block.name if library is None else "%s [%s]" % (block.name, os.path.basename(library.filepath))


def deform_armature(obj):
    """The armature that deforms ``obj`` as the exporters evaluate it: its first viewport-enabled Armature modifier
    with an object, or None. The check, the fix and the read-back all use this one."""
    for modifier in getattr(obj, "modifiers", ()):
        if modifier.type == "ARMATURE" and modifier.object is not None and modifier.show_viewport:
            return modifier.object
    return None


def _owners(scene):
    """({action key: [object names]}, {object name: [actions]}, {action key: action}) for actions an object uses or
    whose slot names it: active, NLA strips, unused actions with its slot."""
    by_action, by_object, blocks = {}, {}, {}
    slots = {}
    for action in bpy.data.actions:
        for slot in getattr(action, "slots", ()):
            if slot.identifier.startswith("OB"):
                slots.setdefault(slot.identifier[2:], []).append(action)
    for obj in scene.objects:
        used = []
        animation = obj.animation_data
        if animation is not None:
            if animation.action is not None:
                used.append(animation.action)
            used.extend(strip.action for track in animation.nla_tracks for strip in track.strips
                        if strip.action is not None)
        used.extend(action for action in slots.get(obj.name, ()) if action.users == 0 or action.use_fake_user)
        own = []
        for action in used:
            if action not in own:
                own.append(action)
                key = id_key(action)
                blocks[key] = action
                by_action.setdefault(key, []).append(obj.name)
        by_object[obj.name] = own
    return by_action, by_object, blocks


def _transform_keyed(obj, by_object):
    """Whether moving ``obj``'s rest (Apply) would break its animation: object transform keys, or bone location
    keys, which live in the rest's scale."""
    for action in by_object.get(obj.name, ()):
        for curve in action_curves(action):
            path = curve.data_path
            if path in {"location", "rotation_euler", "rotation_quaternion", "scale", "delta_location",
                        "delta_scale"} or (path.startswith("pose.bones[") and path.endswith(".location")):
                return True
    return False


def _editable(obj):
    data = getattr(obj, "data", None)
    return obj.library is None and obj.override_library is None and (
        data is None or (data.library is None and data.users == 1))


def _smooth(modifier):
    group = getattr(modifier, "node_group", None)
    return modifier.type == "NODES" and group is not None and any(
        node.bl_idname == "GeometryNodeSetShadeSmooth" for node in group.nodes)


def _modifiers(obj):
    keys = getattr(getattr(obj, "data", None), "shape_keys", None)
    safe = obj.type == "MESH" and _editable(obj) and keys is None
    return [{"name": modifier.name, "type": modifier.type, "viewport": modifier.show_viewport,
             "smooth": _smooth(modifier), "apply_safe": safe and _smooth(modifier)} for modifier in obj.modifiers]


def _path_properties(*owners):
    found = []
    for owner in owners:
        if owner is None:
            continue
        for key in owner.keys():
            value = owner.get(key)
            if isinstance(value, str) and _ABSOLUTE.match(value):
                found.append(key)
    return sorted(set(found))


def _faces(obj, depsgraph):
    if obj.type not in CURVE_TYPES or depsgraph is None:
        return None
    evaluated = obj.evaluated_get(depsgraph)
    try:
        mesh = evaluated.to_mesh()
    except RuntimeError:
        return 0
    try:
        return len(mesh.polygons) if mesh is not None else 0
    finally:
        evaluated.to_mesh_clear()


def _materials(obj):
    """What an object's faces are drawn with, as linking its mesh to another would change it: the mesh's own
    material list, and for each slot where it is linked (object or data) and the material it resolves to. Per-face
    slot indices are the ``material_index`` attribute, which the geometry hash covers."""
    return json.dumps([[id_key(material) if material else None for material in obj.data.materials],
                       [[slot.link, id_key(slot.material) if slot.material else None] for slot in obj.material_slots]])


def geometry_keys(objects):
    """{object name: key} for plain meshes (no modifiers, shape keys or vertex groups) whose counts match another
    plain mesh in a different data block. Two objects share a key only when one's mesh can replace the other's and
    nothing it exports changes: positions, topology, every attribute (per-face material indices included), the UV
    and colour attributes in use, the mesh's custom properties, and the materials each object resolves
    (``_materials``). ``engine_fixes.link_data`` computes it again just before it links."""
    import numpy
    from .engine_rules import godot_token, unreal_prefix
    # Collision objects keep their own meshes: an engine reads them by name, as shapes, never as instances.
    plain = [obj for obj in objects if obj.type == "MESH" and not obj.modifiers and not obj.vertex_groups
             and obj.data.shape_keys is None and godot_token(obj.name) is None and unreal_prefix(obj.name) is None]
    buckets = {}
    for obj in plain:
        mesh = obj.data
        key = (len(mesh.vertices), len(mesh.loops), len(mesh.polygons), _materials(obj))
        buckets.setdefault(key, []).append(obj)
    keys, cache = {}, {}
    for members in buckets.values():
        if len(members) < 2 or len({obj.data.name for obj in members}) < 2:
            continue
        for obj in members:
            mesh = obj.data
            if mesh.as_pointer() not in cache:
                hasher = hashlib.sha1()
                for collection, field, dtype, width in ((mesh.vertices, "co", numpy.float32, 3),
                                                        (mesh.loops, "vertex_index", numpy.int32, 1),
                                                        (mesh.polygons, "loop_start", numpy.int32, 1),
                                                        (mesh.polygons, "loop_total", numpy.int32, 1)):
                    values = numpy.empty(len(collection) * width, dtype=dtype)
                    collection.foreach_get(field, values)
                    hasher.update(numpy.round(values, 6).tobytes() if dtype is numpy.float32 else values.tobytes())
                for attribute in sorted(mesh.attributes, key=lambda item: item.name):
                    if attribute.name == "position" or not len(attribute.data):
                        continue
                    hasher.update(("%s:%s:%s" % (attribute.name, attribute.domain, attribute.data_type)).encode())
                    if attribute.data_type == "STRING":
                        continue
                    item = attribute.data[0]
                    field = "vector" if hasattr(item, "vector") else "color" if hasattr(item, "color") else "value"
                    sample = getattr(item, field)
                    width = len(sample) if hasattr(sample, "__len__") else 1
                    if attribute.data_type.startswith("FLOAT") or "COLOR" in attribute.data_type:
                        values = numpy.empty(len(attribute.data) * width, dtype=numpy.float32)
                        attribute.data.foreach_get(field, values)
                        hasher.update(numpy.round(values, 6).tobytes())
                    else:   # Integers and booleans: a plain list takes any of them.
                        values = [0] * (len(attribute.data) * width)
                        attribute.data.foreach_get(field, values)
                        hasher.update(numpy.asarray(values, dtype=numpy.int64).tobytes())
                # What the exporters read beside the attributes: the UV maps in use, the colour attributes in use,
                # and the mesh's custom properties (extras).
                uvs = mesh.uv_layers
                colours = mesh.color_attributes
                hasher.update(json.dumps([
                    uvs.active.name if uvs.active else None,
                    next((layer.name for layer in uvs if layer.active_render), None),
                    colours.active_color_name if hasattr(colours, "active_color_name") else None,
                    colours.render_color_index if hasattr(colours, "render_color_index") else None,
                    {key: repr(mesh[key]) for key in mesh.keys()}], sort_keys=True, default=repr).encode())
                cache[mesh.as_pointer()] = hasher.hexdigest()
            keys[obj.name] = hashlib.sha1((cache[mesh.as_pointer()] + _materials(obj)).encode()).hexdigest()
    return keys


def _omi_physics(scene):
    """``marked(obj)`` when the glTF exporter writes Angelo's physics as OMI colliders in this build (the Godot
    physics work, ``io_scene_gltf2.angelo_physics``), else None: such an object reaches Godot with its shape."""
    try:
        from io_scene_gltf2.angelo_physics import props
    except ImportError:
        return None
    return props.marked if props.scene_format(scene) == "OMI" else None


def _replace_safe(obj):
    """Whether replacing ``obj`` with a new object keeps everything it has: no animation, constraints, bone parent,
    custom properties, linked data or linked collections. What points at it (children, constraint targets, modifier
    objects, collections) moves to the new object (``ID.user_remap`` in ``engine_fixes.collision_mesh``)."""
    from .engine_rules import godot_token
    if godot_token(obj.name) is None:
        return False
    if (obj.library is not None or obj.override_library is not None or obj.animation_data is not None
            or obj.constraints or obj.parent_type != "OBJECT" or obj.keys()
            or any(collection.library is not None or collection.override_library is not None
                   for collection in obj.users_collection)):
        return False
    return True


def _object(obj, view_layer, in_layer, by_object, depsgraph, geometry, omi=None):
    rotation = obj.matrix_basis.to_euler()
    bone_constraints = []
    if obj.type == "ARMATURE" and obj.pose is not None:
        bone_constraints = ["%s:%s" % (bone.name, constraint.type) for bone in obj.pose.bones
                            for constraint in bone.constraints if constraint.enabled and constraint.influence > 0]
    animation = obj.animation_data
    data = getattr(obj, "data", None)
    return {
        "name": obj.name, "type": obj.type, "data": data.name if data is not None else None,
        "parent": obj.parent.name if obj.parent else None, "hidden": _hidden(obj, view_layer, in_layer),
        "rotation_deg": [math.degrees(value) for value in rotation], "scale": list(obj.scale),
        # In the world: with its parents' transforms, which reach the engine above the skeleton too.
        "world_rotation_deg": [math.degrees(value) for value in obj.matrix_world.to_euler()],
        "world_scale": list(obj.matrix_world.to_scale()),
        "constraints": [constraint.type for constraint in obj.constraints
                        if constraint.enabled and constraint.influence > 0],
        "bone_constraints": bone_constraints,
        "animated": bool(animation is not None and (animation.action is not None or len(animation.nla_tracks))),
        "actions": [id_key(action) for action in by_object.get(obj.name, ())],
        "modifiers": _modifiers(obj), "faces": _faces(obj, depsgraph),
        "geometry_key": geometry.get(obj.name), "link_safe": obj.library is None and obj.override_library is None,
        "apply_scale_safe": obj.type == "MESH" and _editable(obj),
        "empty_display": obj.empty_display_type if obj.type == "EMPTY" else None,
        "replace_safe": obj.type == "EMPTY" and _replace_safe(obj),
        "path_properties": _path_properties(obj, data),
        "omi_physics": bool(omi is not None and omi(obj)),
    }


def _armature(obj, by_object, scene):
    bones = obj.data.bones
    roots = [bone.name for bone in bones if bone.parent is None]
    children = [child for child in obj.children if child.type == "MESH"]
    rename_safe = (obj.parent is None and len(roots) == 1 and _editable(obj)
                   and (obj.name == "Armature" or ("Armature", None) not in bpy.data.objects))
    # Apply moves each child's rest too: an animated or constrained child would then move twice.
    children_safe = all(_editable(child) and child.data.shape_keys is None and not child.constraints
                        and not _transform_keyed(child, by_object) for child in children)
    return {"name": obj.name, "roots": roots, "leaves": sum(1 for bone in bones if not bone.children),
            "at_root": obj.parent is None, "rename_safe": rename_safe,
            "transform_fix_safe": (_editable(obj) and not _transform_keyed(obj, by_object) and not obj.constraints
                                   and children_safe)}


def _influences(objects, budget=SKIN_VERTICES):
    skins = [(obj, arm) for obj in objects if obj.type == "MESH" and (arm := deform_armature(obj)) is not None]
    total = sum(len(obj.data.vertices) for obj, _ in skins)
    step = max(1, math.ceil(total / budget)) if total else 1
    out = []
    for obj, arm in skins:
        deform = {bone.name for bone in arm.data.bones if bone.use_deform}
        groups = {group.index for group in obj.vertex_groups if group.name in deform}
        over = {cap: 0 for cap in CAPS}
        dropped = {cap: 0.0 for cap in CAPS}
        most = 0
        vertices = obj.data.vertices
        for index in range(0, len(vertices), step):
            weights = sorted((item.weight for item in vertices[index].groups
                              if item.group in groups and item.weight > 0.0), reverse=True)
            most = max(most, len(weights))
            total_weight = sum(weights)
            for cap in CAPS:
                if len(weights) > cap:
                    over[cap] += step
                    dropped[cap] = max(dropped[cap], weights[cap] / total_weight)
        out.append({"object": obj.name, "most": most, "over": {str(cap): over[cap] for cap in CAPS},
                    "dropped": {str(cap): round(dropped[cap], 4) for cap in CAPS}, "sampled": step > 1,
                    "limit_safe": _editable(obj) and not any(group.lock_weight for group in obj.vertex_groups
                                                             if group.index in groups)})
    return out


def _roles(image_node, limit=8):
    """What ``image_node`` feeds: "colour" (base colour, emission), "data" (normal, roughness, metallic, ...)."""
    roles, stack, seen = set(), [(socket, 0) for socket in image_node.outputs], set()
    while stack:
        socket, depth = stack.pop()
        for link in socket.links:
            if not link.is_valid or link.is_muted:
                continue
            node, target = link.to_node, link.to_socket
            if node.bl_idname in DATA_NODES or target.type in {"VECTOR", "VALUE"} and not (
                    node.bl_idname == "ShaderNodeBsdfPrincipled" or node.type.startswith("BSDF")):
                roles.add("data")   # A factor, a mask or coordinates: data whatever the node feeds after.
                continue
            if node.bl_idname == "ShaderNodeBsdfPrincipled" or node.type.startswith("BSDF") or node.type == "EMISSION":
                if target.name in COLOUR_INPUTS:
                    roles.add("colour")
                elif target.name in DATA_INPUTS:
                    roles.add("data")
                continue
            if depth < limit and node.as_pointer() not in seen:
                seen.add(node.as_pointer())
                stack.extend((output, depth + 1) for output in node.outputs)
    return roles


def _image_nodes(material):
    if material is None or material.node_tree is None:
        return ()
    return [node for node in material.node_tree.nodes if node.bl_idname == "ShaderNodeTexImage" and node.image]


def _images(objects):
    """The images the exported objects' materials use, with every role each image has in any material of the file
    (a colour space serves every use, exported or not)."""
    used = {}
    for obj in objects:
        for slot in obj.material_slots:
            for node in _image_nodes(slot.material):
                used.setdefault(node.image.as_pointer(), node.image)
    roles = {pointer: set() for pointer in used}
    for material in bpy.data.materials:
        for node in _image_nodes(material):
            if node.image.as_pointer() in roles:
                roles[node.image.as_pointer()] |= _roles(node)
    out = []
    for pointer, image in sorted(used.items(), key=lambda item: id_key(item[1])):
        out.append({"name": id_key(image), "packed": image.packed_file is not None, "used_by_export": True,
                    "file": image.source in {"FILE", "SEQUENCE", "TILED"} and bool(image.filepath),
                    "roles": sorted(roles[pointer]), "colorspace": image.colorspace_settings.name,
                    "editable": image.library is None and image.override_library is None})
    return out


def gather(scene, view_layer, fmt, options, selection_only=False, depsgraph=None):
    """``engine_rules``' facts for an export of ``scene`` as ``fmt`` with exporter ``options``."""
    exported = exported_objects(scene, view_layer, fmt, options, selection_only)
    in_layer = {obj.name for obj in view_layer.objects}
    by_action, by_object, blocks = _owners(scene)
    geometry = geometry_keys(exported)
    omi = _omi_physics(scene) if fmt == "GLB" else None
    actions = []
    for name in sorted(by_action):
        action = blocks[name]
        actions.append({"name": name, "users": action.users, "fake_user": action.use_fake_user,
                        "owners": by_action[name], "cyclic": _cyclic(action),
                        "editable": action.library is None and action.override_library is None})
    return {
        "scene": {"unit_scale": scene.unit_settings.scale_length, "unit_system": scene.unit_settings.system},
        "exported": [obj.name for obj in exported],
        "objects": [_object(obj, view_layer, in_layer, by_object, depsgraph, geometry, omi) for obj in exported],
        "armatures": [_armature(obj, by_object, scene) for obj in exported if obj.type == "ARMATURE"],
        "actions": actions,
        "images": _images(exported),
        "influences": _influences(exported),
    }
