# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Export time with and without the engine preflight on a production-size scene (doc/angelo/engine_preflight.rst,
"Measurements"). Background Angelo, local only; no provider, account or network.

    Angelo --background --factory-startup --python bench_engine_preflight.py -- OUT_DIR [PAIRS]

The scene is built here, so any machine measures the same one: 24 × 24 street blocks of beveled buildings (separate
mesh data, as Shift+D makes them), 300 props, two skinned characters of 30,000 vertices with six influences on part
of their vertices, 40 materials with 1024² image textures, about 1.2 million triangles. Measured:

* the web export (``web_export.export``) with ``preflight`` off (A) and on (B), alternating ABAB after one untimed
  warm-up per arm, ``PAIRS`` pairs (default 5), median and range per arm: what the agent waits for; and B's parts
  (rules, and the background read-back's child after it: ``web_readback``);
* the engine link's save of a .glb asset: the write-back child alone (what the engine waits for, unchanged) and the
  preflight child that starts after it, each a fresh background Angelo, alternating, as the link runs them;
* the save's main-thread cost of starting the preflight (``engine_link._preflight``'s folder check and thread start).

Writes ``bench.json`` in OUT_DIR. Run it on a quiet machine (nothing else timed, no suites).
"""

import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

import bpy
import bmesh

from angelo_agent import engine_link_writeback, engine_preflight, engine_preflight_link, web_export, web_readback

OUT = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
PAIRS = int(sys.argv[sys.argv.index("--") + 2]) if len(sys.argv) > sys.argv.index("--") + 2 else 5
OUT.mkdir(parents=True, exist_ok=True)


def building(name, location, size, material):
    mesh = bpy.data.meshes.new(name)
    built = bmesh.new()
    bmesh.ops.create_cube(built, size=1.0)
    bmesh.ops.subdivide_edges(built, edges=built.edges[:], cuts=6, use_grid_fill=True)
    built.to_mesh(mesh)
    built.free()
    mesh.materials.append(material)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.location, obj.scale = location, size
    bevel = obj.modifiers.new("Bevel", "BEVEL")
    bevel.width, bevel.segments = 0.02, 2
    return obj


def character(name, offset, materials):
    data = bpy.data.armatures.new(name + "Rig")
    rig = bpy.data.objects.new(name + "Rig", data)
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    for index in range(8):
        bone = data.edit_bones.new("B%d" % index)
        bone.head, bone.tail = (0, 0, index * 0.25), (0, 0, (index + 1) * 0.25)
    bpy.ops.object.mode_set(mode="OBJECT")
    rig.location = offset
    mesh = bpy.data.meshes.new(name)
    built = bmesh.new()
    bmesh.ops.create_cone(built, cap_ends=True, segments=150, radius1=0.4, radius2=0.3, depth=2.0)
    bmesh.ops.subdivide_edges(built, edges=[edge for edge in built.edges if abs(edge.verts[0].co.z - edge.verts[1].co.z)
                                            > 0.5], cuts=198)
    built.to_mesh(mesh)
    built.free()
    mesh.materials.append(materials[0])
    body = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(body)
    body.parent = rig
    body.location = (0, 0, 1.0)
    modifier = body.modifiers.new("Armature", "ARMATURE")
    modifier.object = rig
    groups = [body.vertex_groups.new(name="B%d" % index) for index in range(8)]
    for vertex in mesh.vertices:
        height = (vertex.co.z + 1.0) / 2.0 * 8
        near = sorted(range(8), key=lambda index: abs(index + 0.5 - height))[:6 if vertex.index % 3 == 0 else 2]
        for rank, index in enumerate(near):
            groups[index].add([vertex.index], 1.0 / (rank + 1), "REPLACE")
    rig.keyframe_insert("location", frame=1)
    return rig, body


def build():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    materials = []
    for index in range(40):
        material = bpy.data.materials.new("Mat%02d" % index)
        image = bpy.data.images.new("Tex%02d" % index, 1024, 1024)
        image.generated_type = "COLOR_GRID"
        image.pack()
        node = material.node_tree.nodes.new("ShaderNodeTexImage")
        node.image = image
        material.node_tree.links.new(node.outputs[0], material.node_tree.nodes["Principled BSDF"].inputs[0])
        materials.append(material)
    for row in range(24):
        for column in range(24):
            index = row * 24 + column
            height = 3 + (index * 7919 % 17)
            building("Building%03d" % index, (row * 12.0, column * 12.0, height / 2), (8, 8, height),
                     materials[index % len(materials)])
    for index in range(300):   # Props that are copies (separate data) of three shapes.
        source = building("Prop%03d" % index, ((index % 30) * 9.5, (index // 30) * 28.0 + 6, 0.5), (0.8, 0.8, 1.0),
                          materials[index % 3])
        source.modifiers.clear()
    for index, offset in enumerate(((5, 5, 0), (40, 40, 0))):
        character("Hero%d" % index, offset, materials)
    path = OUT / "bench.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    depsgraph = bpy.context.evaluated_depsgraph_get()
    triangles = sum(len(obj.evaluated_get(depsgraph).data.loop_triangles) for obj in bpy.context.scene.objects
                    if obj.type == "MESH")
    return path, {"objects": len(bpy.context.scene.objects), "triangles": triangles,
                  "materials": len(bpy.data.materials), "images": len(bpy.data.images)}


def summary(values):
    return {"median_ms": round(statistics.median(values), 1), "min_ms": round(min(values), 1),
            "max_ms": round(max(values), 1), "n": len(values), "all_ms": [round(value, 1) for value in values]}


def web_arm(preflight, index):
    """The export as the agent waits for it; with the preflight, then the background read-back's child, timed on
    its own (it runs after the tool has answered)."""
    path = OUT / ("web-%s-%d.glb" % ("b" if preflight else "a", index))
    if path.exists():
        path.unlink()
    request = {"meshes": "AUTO", "textures": "WEBP", "merge": True, "selection_only": False}
    started = time.perf_counter()
    report = web_export.export(None, bpy.context.scene, bpy.context.view_layer, str(path), request, preflight=preflight)
    elapsed = (time.perf_counter() - started) * 1000.0
    parts = report.get("preflight")
    if preflight:
        readback = report.pop("_readback")
        spec = OUT / "readback-spec.json"
        spec.write_text(json.dumps({"path": str(path), "source": readback["source"], "merged": readback["merged"],
                                    "needles": {}, "result": str(OUT / "readback-result.json")}))
        parts = dict(parts, readback_child_ms=child([bpy.app.binary_path, "--background", "--factory-startup",
                                                     "--python-exit-code", "1", "--python-expr", web_readback.CHILD,
                                                     "--", str(spec)]),
                     readback=json.loads((OUT / "readback-result.json").read_text()))
    path.unlink()
    return elapsed, parts


def child(command):
    started = time.perf_counter()
    completed = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                               env=engine_link_writeback._child_environment(), timeout=900)
    if completed.returncode:
        raise RuntimeError(completed.stderr.decode("utf-8", "replace")[-500:])
    return (time.perf_counter() - started) * 1000.0


def link_arms(blend):
    from angelo_agent.godot_link_assets import godot_options
    target = OUT / "asset.glb"
    options = godot_options({})
    binary = bpy.app.binary_path
    writeback = [binary, "--background", "--factory-startup", str(blend), "--python-exit-code", "1",
                 "--python-expr", engine_link_writeback.CHILD, "--", "null", str(target), "gltf", json.dumps(options), "[]"]
    spec = {"engine": "godot", "report": str(OUT / "asset.preflight.json"), "file": str(target), "exporter": "gltf",
            "options": options}
    preflight = [binary, "--background", "--factory-startup", str(blend), "--python-exit-code", "1",
                 "--python-expr", engine_preflight_link.CHILD, "--", json.dumps(spec)]
    child(writeback)
    child(preflight)   # Untimed warm-up of each.
    timings = {"writeback": [], "preflight_after": []}
    for _ in range(PAIRS):
        timings["writeback"].append(child(writeback))
        timings["preflight_after"].append(child(preflight))
    report = json.loads((OUT / "asset.preflight.json").read_text())
    return {key: summary(values) for key, values in timings.items()}, report


def main_thread_start():
    """What a save of a linked asset spends on the main thread to start the preflight (no child is waited for)."""
    import tempfile
    from angelo_agent.engine_link_assets import Asset
    project = Path(tempfile.mkdtemp(prefix="angelo-bench-project-"))
    (project / "props").mkdir()
    (project / "props" / "crate.glb").write_bytes(b"glb")
    asset = Asset("godot", str(project), "res://props/crate.glb", str(project / "props/crate.glb"), ".glb",
                  "props/crate.glb")
    calls = []
    original = engine_preflight_link.Runner._run
    engine_preflight_link.Runner._run = lambda self, blend, spec: calls.append(spec)   # Never starts a child here.
    try:
        samples = []
        for _ in range(50):
            started = time.perf_counter()
            engine_preflight_link.after_save(bpy.app.binary_path, asset, str(project / "w.blend"), asset.path,
                                             ("gltf", {}), {})
            samples.append((time.perf_counter() - started) * 1000.0)
            engine_preflight_link._runner._running = False
    finally:
        engine_preflight_link.Runner._run = original
    return summary(samples)


blend, scene = build()
results = {"scene": scene, "build": {"binary": bpy.app.binary_path.split("/Contents/")[0].rsplit("/", 1)[-1],
                                     "version": bpy.app.version_string, "hash": bpy.app.build_hash.decode()}}
web_arm(False, -1)
web_arm(True, -1)   # Untimed warm-up of each arm.
a_times, b_times, parts = [], [], []
for index in range(PAIRS):
    a_times.append(web_arm(False, index)[0])
    elapsed, report = web_arm(True, index)
    b_times.append(elapsed)
    parts.append({"rules_ms": report["check_ms"], "readback_child_ms": round(report["readback_child_ms"], 1),
                  "readback_ms": report["readback"]["ms"], "findings": len(report["findings"]),
                  "readback_findings": [item["code"] for item in report["readback"]["findings"]]})
results["web_export"] = {"without": summary(a_times), "with": summary(b_times), "parts": parts}
results["link_save"], link_report = link_arms(blend)
results["link_report"] = {"findings": [(item["code"], item["subject"]) for item in link_report["findings"]],
                          "reimport": [(item["code"], item["subject"]) for item in link_report["reimport"]["findings"]],
                          "preflight_ms": link_report["preflight_ms"]}
results["save_main_thread"] = main_thread_start()
(OUT / "bench.json").write_text(json.dumps(results, indent=1))
print("ANGELO_ENGINE_PREFLIGHT_BENCH", json.dumps({key: results[key] for key in ("scene", "web_export", "link_save",
                                                                                 "save_main_thread")}))
