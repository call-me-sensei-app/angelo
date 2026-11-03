# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the engine-skeleton check costs when nobody uses it, and what one check costs (doc/angelo/engine_skeleton.rst,
"Cost"). Run inside the app on a quiet machine:

    Angelo --background --factory-startup --python bench_engine_skeleton.py -- OUT.json [ROUNDS] [--enable-only]

* **Add-on start-up**: ``angelo_agent`` enabled as at launch, timed whole; with ``--enable-only`` that is all (the
  base build's comparison runs).
* **Startup**: the skeleton modules the add-on imported while registering, their import time from compiled
  bytecode (each round drops them from ``sys.modules`` and imports ``skeleton_check_ui`` again), and
  ``skeleton_check_ui``'s unregister + register.
* **Nothing per frame or per turn**: the app's handlers and timers, counted before and after that register.
* **The + menu**: ``chat_destinations.menu_rows()`` with and without the feature's row.
* **One check**: Blender's Rigify generated rig and a 1.8 m humanoid with a 60,000-vertex skinned mesh, against each
  engine (median of the rounds).
Medians and spread of every timing are written; nothing else is changed.
"""

import importlib
import json
import statistics
import sys
import time
from pathlib import Path

import bpy

args = sys.argv[sys.argv.index("--") + 1:]
OUT = Path(args[0])
ROUNDS = int(args[1]) if len(args) > 1 else 30
PREFIX = "angelo_agent.skeleton_"


def spread(samples):
    samples = sorted(samples)
    return {"median_ms": round(statistics.median(samples) * 1000, 4), "min_ms": round(samples[0] * 1000, 4),
            "max_ms": round(samples[-1] * 1000, 4), "n": len(samples)}


def hooks():
    handlers = {name: len(getattr(bpy.app.handlers, name)) for name in dir(bpy.app.handlers)
                if not name.startswith("_") and isinstance(getattr(bpy.app.handlers, name), list)}
    return handlers


def startup():
    package = sys.modules["angelo_agent"]
    loaded = sorted(name for name in sys.modules if name.startswith(PREFIX))
    ui = importlib.import_module("angelo_agent.skeleton_check_ui")
    before = hooks()
    imports, registers = [], []
    for _ in range(ROUNDS):
        ui.unregister()
        for name in loaded:
            sys.modules.pop(name, None)
            setattr(package, name.split(".")[-1], None)
        started = time.perf_counter()
        ui = importlib.import_module("angelo_agent.skeleton_check_ui")
        imports.append(time.perf_counter() - started)
        started = time.perf_counter()
        ui.register()
        registers.append(time.perf_counter() - started)
    return {"modules_imported_at_startup": loaded, "import": spread(imports), "register": spread(registers),
            "handlers_unchanged": hooks() == before}


def menu():
    from angelo_agent import chat_destinations as cd
    feature = cd.feature("engine_skeleton")
    timings = {}
    for label in ("with", "without"):
        if label == "without":
            cd.unregister("engine_skeleton")
        samples = []
        for _ in range(ROUNDS * 10):
            started = time.perf_counter()
            cd.menu_rows()
            samples.append(time.perf_counter() - started)
        timings[label] = spread(samples)
    cd.register(feature)
    return timings


def humanoid_with_mesh():
    bpy.ops.object.armature_add(location=(0, 0, 0))
    arm = bpy.context.object
    bpy.ops.object.mode_set(mode="EDIT")
    bones = arm.data.edit_bones
    bones.remove(bones[0])
    layout = [("hips", None, (0, 0, 1.0), (0, 0, 1.1)), ("spine", "hips", (0, 0, 1.1), (0, 0, 1.45)),
              ("head", "spine", (0, 0, 1.55), (0, 0, 1.8))]
    for s, x in (("L", 1), ("R", -1)):
        layout += [("arm." + s, "spine", (x * .15, 0, 1.42), (x * .43, 0, 1.42)),
                   ("fore." + s, "arm." + s, (x * .43, 0, 1.42), (x * .68, 0, 1.42)),
                   ("hand." + s, "fore." + s, (x * .68, 0, 1.42), (x * .76, 0, 1.42)),
                   ("thigh." + s, "hips", (x * .1, 0, 1.0), (x * .1, -.01, .53)),
                   ("shin." + s, "thigh." + s, (x * .1, -.01, .53), (x * .1, 0, .08)),
                   ("foot." + s, "shin." + s, (x * .1, 0, .08), (x * .1, -.15, 0))]
    for name, parent, head, tail in layout:
        bone = bones.new(name)
        bone.head, bone.tail = head, tail
        if parent:
            bone.parent = bones[parent]
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.mesh.primitive_uv_sphere_add(segments=300, ring_count=200, radius=0.9, location=(0, 0, 0.9))
    mesh = bpy.context.object
    mesh.modifiers.new("Armature", "ARMATURE").object = arm
    count = len(mesh.data.vertices)
    names = [name for name, *_ in layout]
    for k, name in enumerate(names):
        mesh.vertex_groups.new(name=name).add([v for v in range(count) if v % len(names) in (k, (k + 1) % len(names))],
                                              0.5, "REPLACE")
    return arm, count


def checks():
    from angelo_agent import skeleton_check_host as host
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.preferences.addon_enable(module="rigify")
    bpy.ops.object.armature_human_metarig_add()
    bpy.ops.pose.rigify_generate()
    rigs = {"rigify_generated": (bpy.data.objects["rig"], 0)}
    rigs["humanoid_60k_vertices"] = humanoid_with_mesh()
    out = {}
    for label, (arm, vertices) in rigs.items():
        out[label] = {"bones": len(arm.data.bones), "skinned_vertices": vertices}
        for target in ("UNREAL", "UNITY", "GODOT"):
            samples = []
            for _ in range(max(3, ROUNDS // 5)):
                started = time.perf_counter()
                host.report(bpy.context.scene, arm, target)
                samples.append(time.perf_counter() - started)
            out[label][target] = spread(samples)
    return out


def enable():
    """The add-on registered as the app does at start-up, timed whole (the ABAB comparison against the base build
    uses this number; ``startup`` isolates the skeleton modules' part)."""
    started = time.perf_counter()
    bpy.ops.preferences.addon_enable(module="angelo_agent")
    return round((time.perf_counter() - started) * 1000, 2)


result = {"version": bpy.app.version_string, "rounds": ROUNDS, "addon_enable_ms": enable()}
if "--enable-only" not in sys.argv and "angelo_agent.skeleton_check_ui" in sys.modules:
    result.update(startup=startup(), menu_rows=menu())
    result["checks"] = checks()
OUT.write_text(json.dumps(result, indent=1), encoding="utf-8")
print("ANGELO_SKELETON_BENCH", OUT, flush=True)
