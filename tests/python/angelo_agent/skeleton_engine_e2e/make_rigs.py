# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Angelo side of skeleton_engine_e2e/run.py: builds the test rigs, records Angelo's own verdict for each engine
BEFORE any engine sees them, and exports each the way that engine's usual import reads it.

    Angelo --background --factory-startup --python make_rigs.py -- OUT_DIR

Rigs (each a skinned mesh of one box per deforming bone, so every engine imports a skeletal mesh):

* ``ue_named``: a humanoid with UE5 Mannequin names under ``root``, T-pose, hands with metacarpals. Positive for every
  engine.
* ``rigify``: Blender's Rigify human, generated (DEF- bones under ORG/MCH and a non-deforming root). Unreal's
  auto-retarget is predicted not to recognise it.
* ``one_bone_arms``: the same humanoid with each arm a single bone from shoulder to wrist (no forearm), arbitrary
  names. Predicted to fail Unity Humanoid and Godot's profile (no LowerArm).
* ``metarig``: Rigify's human metarig itself (its armature object named ``metarig``), for Unreal's Rigify template.

Writes ``OUT/rigs/<rig>.<glb|fbx>`` and ``OUT/angelo.json`` (verdicts and roles per rig and engine).

``--extra FILE...``: held-out rig files (.glb, .fbx, .vrm), read only. Each armature with 10 or more bones and a mesh
skinned to it gets Angelo's Unreal verdict per format, then its Unreal exports (``OUT/angelo-extra.json``,
``OUT/rigs/<file>_<armature>.unreal.<glb|fbx>``).
"""

import json
import math
import re
import shutil
import sys
import tempfile
from pathlib import Path

import bpy

ARGS = sys.argv[sys.argv.index("--") + 1:]
OUT = Path(ARGS[0])
EXTRA = [Path(a) for a in ARGS[ARGS.index("--extra") + 1:]] if "--extra" in ARGS else []
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import skeleton_check_host as host  # noqa: E402
from angelo_agent.skeleton_export import exporter_options  # noqa: E402
from angelo_agent.skeleton_facts import skinned_meshes  # noqa: E402

FINGERS = (("index", -0.027), ("middle", -0.009), ("ring", 0.009), ("pinky", 0.027))


def ue_layout(one_bone_arms=False):
    """(name, parent, head, tail) of a 1.8 m humanoid facing -Y with UE5 Mannequin names."""
    b = [("root", None, (0, 0, 0), (0, -0.25, 0)), ("pelvis", "root", (0, 0, 1.0), (0, 0, 1.1))]
    z, parent = 1.1, "pelvis"
    for k in range(1, 6):
        b.append(("spine_%02d" % k, parent, (0, 0, z), (0, 0, z + 0.07)))
        parent, z = "spine_%02d" % k, z + 0.07
    b += [("neck_01", "spine_05", (0, 0, 1.45), (0, 0, 1.5)), ("neck_02", "neck_01", (0, 0, 1.5), (0, 0, 1.55)),
          ("head", "neck_02", (0, 0, 1.55), (0, 0, 1.8))]
    for s, x in (("l", 1.0), ("r", -1.0)):
        b.append(("clavicle_" + s, "spine_05", (x * 0.02, 0, 1.42), (x * 0.15, 0, 1.42)))
        if one_bone_arms:
            b.append(("upperarm_" + s, "clavicle_" + s, (x * 0.15, 0, 1.42), (x * 0.68, 0, 1.42)))
            b.append(("hand_" + s, "upperarm_" + s, (x * 0.68, 0, 1.42), (x * 0.76, 0, 1.42)))
        else:
            b += [("upperarm_" + s, "clavicle_" + s, (x * 0.15, 0, 1.42), (x * 0.43, 0, 1.42)),
                  ("lowerarm_" + s, "upperarm_" + s, (x * 0.43, 0, 1.42), (x * 0.68, 0, 1.42)),
                  ("hand_" + s, "lowerarm_" + s, (x * 0.68, 0, 1.42), (x * 0.76, 0, 1.42))]
        for finger, y in FINGERS:
            b.append(("%s_metacarpal_%s" % (finger, s), "hand_" + s, (x * 0.70, y * 0.6, 1.42), (x * 0.76, y, 1.42)))
            prev = "%s_metacarpal_%s" % (finger, s)
            for k in range(3):
                name = "%s_%02d_%s" % (finger, k + 1, s)
                b.append((name, prev, (x * (0.76 + 0.03 * k), y, 1.42), (x * (0.79 + 0.03 * k), y, 1.42)))
                prev = name
        prev = "hand_" + s
        for k in range(3):
            name = "thumb_%02d_%s" % (k + 1, s)
            hx, hy = 0.70 + 0.018 * k, -0.02 - 0.024 * k
            b.append((name, prev, (x * hx, hy, 1.41), (x * (hx + 0.018), hy - 0.024, 1.41)))
            prev = name
    for s, x in (("l", 1.0), ("r", -1.0)):
        b += [("thigh_" + s, "pelvis", (x * 0.1, 0, 1.0), (x * 0.1, -0.01, 0.53)),
              ("calf_" + s, "thigh_" + s, (x * 0.1, -0.01, 0.53), (x * 0.1, 0, 0.08)),
              ("foot_" + s, "calf_" + s, (x * 0.1, 0, 0.08), (x * 0.1, -0.12, 0.02)),
              ("ball_" + s, "foot_" + s, (x * 0.1, -0.12, 0.02), (x * 0.1, -0.2, 0.02))]
    return b


def build(name, layout, rename=None):
    data = bpy.data.armatures.new(name)
    arm = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    rename = rename or (lambda n: n)
    for bone, parent, head, tail in layout:
        edit = data.edit_bones.new(rename(bone))
        edit.head, edit.tail = head, tail
        if parent:
            edit.parent = data.edit_bones[rename(parent)]
    bpy.ops.object.mode_set(mode="OBJECT")
    return arm


def skin(arm):
    """One small box per deforming bone, joined, each box weighted fully to its bone."""
    import bmesh
    mesh = bpy.data.meshes.new(arm.name + "_body")
    bm = bmesh.new()
    groups = []
    for bone in arm.data.bones:
        if not bone.use_deform:
            continue
        head, tail = arm.matrix_world @ bone.head_local, arm.matrix_world @ bone.tail_local
        before = len(bm.verts)
        r = max(0.006, min(0.03, (tail - head).length * 0.25))
        for t in (0.15, 0.85):
            c = head.lerp(tail, t)
            for dx in (-r, r):
                for dy in (-r, r):
                    bm.verts.new((c.x + dx, c.y + dy, c.z))
        bm.verts.ensure_lookup_table()
        v = bm.verts[before:]
        for face in ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)):
            try:
                bm.faces.new([v[i] for i in face])
            except ValueError:
                pass
        groups.append((bone.name, range(before, before + 8)))
    bm.to_mesh(mesh)
    bm.free()
    body = bpy.data.objects.new(arm.name + "_body", mesh)
    bpy.context.scene.collection.objects.link(body)
    body.parent = arm
    body.modifiers.new("Armature", "ARMATURE").object = arm
    for bone, indices in groups:
        body.vertex_groups.new(name=bone).add(list(indices), 1.0, "REPLACE")
    return body


def export(arm, body, path, fmt, target):
    bpy.ops.object.select_all(action="DESELECT")
    arm.select_set(True)
    for mesh in body if isinstance(body, list) else [body]:
        mesh.select_set(True)
    bpy.context.view_layer.objects.active = arm
    options = {k: v for k, v in exporter_options(target, fmt).items()}
    if fmt == "FBX":
        options.pop("use_active_collection", None)
        options["use_selection"] = True
        if "object_types" in options:   # the operator takes a set; the engine-link profile stores a list
            options["object_types"] = set(options["object_types"])
        bpy.ops.export_scene.fbx(filepath=str(path), **options)
    else:
        allowed = set(bpy.ops.export_scene.gltf.get_rna_type().properties.keys())
        options = {k: v for k, v in options.items() if k in allowed}
        options["use_selection"] = True
        bpy.ops.export_scene.gltf(filepath=str(path), export_format="GLB", **options)


def verdict(report):
    return {"verdict": report["verdict"], "roles": report["roles"],
            "statuses": {f["check"]: f["status"] for f in report["findings"]},
            "predictions": {f["check"]: f["prediction"] for f in report["findings"] if "prediction" in f},
            "found": {f["check"]: f["found"] for f in report["findings"] if f["status"] != "pass"}}


def load(path, scratch):
    bpy.ops.wm.read_homefile(use_empty=True)
    suffix = path.suffix.lower()
    if suffix == ".vrm":   # a VRM is a glTF binary
        path = Path(shutil.copy2(path, Path(scratch, path.stem + ".glb")))
    if suffix in (".glb", ".gltf", ".vrm"):
        bpy.ops.import_scene.gltf(filepath=str(path))
    else:
        bpy.ops.import_scene.fbx(filepath=str(path))
    bpy.context.view_layer.update()


def extras(paths):
    """Held-out files: Unreal verdicts first, then the exports Unreal imports."""
    verdicts = {}
    with tempfile.TemporaryDirectory() as scratch:
        for path in paths:
            load(path, scratch)
            scene = bpy.context.scene
            for arm in [o for o in scene.objects if o.type == "ARMATURE" and len(o.data.bones) >= 10]:
                meshes = skinned_meshes(scene, arm)
                if not meshes:
                    continue
                key = re.sub(r"[^A-Za-z0-9]+", "_", "%s_%s" % (path.stem, arm.name)).strip("_")
                verdicts[key] = {"file": path.name, "armature": arm.name}
                for fmt in ("GLB", "FBX"):
                    verdicts[key]["UNREAL/" + fmt] = verdict(host.report(scene, arm, "UNREAL", fmt))
                for fmt in ("GLB", "FBX"):
                    export(arm, meshes, OUT / "rigs" / ("%s.unreal.%s" % (key, fmt.lower())), fmt, "UNREAL")
    (OUT / "angelo-extra.json").write_text(json.dumps(verdicts, indent=2), encoding="utf-8")


def main():
    bpy.ops.wm.read_homefile(use_empty=True)
    (OUT / "rigs").mkdir(parents=True, exist_ok=True)
    rigs = {}
    rigs["ue_named"] = build("Armature", ue_layout())
    names = {}
    rigs["one_bone_arms"] = build("Hero", ue_layout(one_bone_arms=True),
                                  rename=lambda n: names.setdefault(n, "Bone.%03d" % len(names)))
    bpy.ops.preferences.addon_enable(module="rigify")
    bpy.ops.object.armature_human_metarig_add()
    rigs["metarig"] = bpy.context.view_layer.objects.active
    bpy.ops.pose.rigify_generate()
    rigs["rigify"] = bpy.data.objects["rig"]
    bpy.ops.object.mode_set(mode="OBJECT")
    bodies = {name: skin(arm) for name, arm in rigs.items()}
    bpy.context.view_layer.update()
    verdicts = {}
    for name, arm in rigs.items():
        verdicts[name] = {}
        for target, fmts in (("GODOT", ("GLB",)), ("UNITY", ("FBX",)), ("UNREAL", ("GLB", "FBX"))):
            for fmt in fmts:
                verdicts[name]["%s/%s" % (target, fmt)] = verdict(host.report(bpy.context.scene, arm, target, fmt))
                export(arm, bodies[name], OUT / "rigs" / ("%s.%s.%s" % (name, target.lower(), fmt.lower())), fmt,
                       target)
    (OUT / "angelo.json").write_text(json.dumps(verdicts, indent=2), encoding="utf-8")
    extras(EXTRA)
    print("ANGELO_SKELETON_RIGS", json.dumps(sorted(p.name for p in (OUT / "rigs").iterdir())), flush=True)


main()
