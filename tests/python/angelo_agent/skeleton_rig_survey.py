# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The engine-skeleton check on rig files from anywhere: the held-out survey (doc/angelo/engine_skeleton.rst).

    Angelo --background --factory-startup --python skeleton_rig_survey.py -- OUT.json FILE [FILE ...]

Each file (.glb, .gltf, .vrm, .fbx or .blend) is loaded into an empty file; every armature with 10 bones or more is
checked against Unreal, Unity and Godot with each engine's usual export. Writes, per armature: its bone count, the role
each mapped bone plays, each engine's verdict and finding statuses, the mapping's notes and the check's time. The
files are read only (a .vrm is read from a temporary .glb copy); nothing is written beside them. No provider call.
"""

import json
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import skeleton_check_host as host  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:]
OUT, FILES = Path(args[0]), [Path(a) for a in args[1:]]


def load(path, scratch):
    bpy.ops.wm.read_homefile(use_empty=True)
    suffix = path.suffix.lower()
    if suffix in (".glb", ".gltf", ".vrm"):
        source = path
        if suffix == ".vrm":
            source = Path(scratch, path.stem + ".glb")
            shutil.copy2(path, source)
        bpy.ops.import_scene.gltf(filepath=str(source))
    elif suffix == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(path))
    elif suffix == ".blend":
        with bpy.data.libraries.load(str(path), link=False) as (data_from, data_to):
            data_to.objects = list(data_from.objects)
        for obj in data_to.objects:
            if obj is not None:
                bpy.context.scene.collection.objects.link(obj)
    else:
        raise ValueError("unsupported file " + path.name)
    bpy.context.view_layer.update()


def survey(path, scratch):
    load(path, scratch)
    out = {}
    for arm in [o for o in bpy.context.scene.objects if o.type == "ARMATURE" and len(o.data.bones) >= 10]:
        entry = {"bones": len(arm.data.bones), "targets": {}}
        for target in ("UNREAL", "UNITY", "GODOT"):
            started = time.perf_counter()
            report = host.report(bpy.context.scene, arm, target)
            entry["targets"][target] = {
                "verdict": report["verdict"], "ms": round((time.perf_counter() - started) * 1000, 1),
                "statuses": {f["check"]: f["status"] for f in report["findings"]},
                "found": {f["check"]: f["found"] for f in report["findings"] if f["status"] in ("warn", "fail")}}
            entry["roles"], entry["notes"] = report["roles"], report["notes"]
        out[arm.name] = entry
    return out


def main():
    result = {"version": bpy.app.version_string, "files": {}}
    with tempfile.TemporaryDirectory() as scratch:
        for path in FILES:
            try:
                result["files"][path.name] = survey(path, scratch)
            except Exception:
                result["files"][path.name] = {"error": traceback.format_exc()[-1200:]}
    OUT.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print("ANGELO_SKELETON_SURVEY", OUT, flush=True)


main()
