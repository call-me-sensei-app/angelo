# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reading an exported clip set back, for test_blender_clip_export and test_blender_clip_export_edges: a glTF file's
animations as JSON, a clip FBX through Blender's importer (what an engine sees), with what it brought in removed."""

import json
import struct

import bpy


def glb_document(path):
    data = path.read_bytes()
    length = struct.unpack_from("<I", data, 12)[0]
    return json.loads(data[20:20 + length])


def glb(path):
    """{animation name: {"frames": its length in frames from time 0, "paths": the channels' target paths}}."""
    doc = glb_document(path)
    out = {}
    for anim in doc.get("animations", []):
        times = [doc["accessors"][s["input"]]["max"][0] for s in anim["samplers"]]
        paths = {c["target"]["path"] for c in anim["channels"]}
        out[anim["name"]] = {"frames": round(max(times) * bpy.context.scene.render.fps) + 1, "paths": sorted(paths)}
    return out


def curves(action):
    out = []
    for layer in action.layers:
        for strip in layer.strips:
            for bag in strip.channelbags:
                out += list(bag.fcurves)
    return out


def fbx_clip(path):
    """The clip file read back with Blender's importer: its take's frames, its objects, shape-key animation (and
    whether a shape key's value changes over the take)."""
    before_objects, before_actions = set(bpy.data.objects), set(bpy.data.actions)
    bpy.ops.import_scene.fbx(filepath=str(path))
    new_objects = [o for o in bpy.data.objects if o not in before_objects]
    new_actions = [a for a in bpy.data.actions if a not in before_actions]
    arm_actions = [a for a in new_actions if any(fc.data_path.startswith("pose.bones") for fc in curves(a))]
    frames = max((round(a.frame_range[1] - a.frame_range[0]) + 1 for a in arm_actions), default=0)
    key_curves = [fc for a in new_actions for fc in curves(a) if "key_blocks" in fc.data_path]
    moving = any(len({round(k.co[1], 3) for k in fc.keyframe_points}) > 1 for fc in key_curves)
    out = {"frames": frames, "types": sorted({o.type for o in new_objects}), "shape_keys": bool(key_curves),
           "shape_keys_move": moving}
    for obj in new_objects:
        bpy.data.objects.remove(obj)
    for action in new_actions:
        bpy.data.actions.remove(action)
    return out
