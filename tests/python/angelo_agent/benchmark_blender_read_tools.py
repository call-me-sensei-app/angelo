# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Measure angelo_read_data and angelo_blend_file in the compiled binary: time and answer size.

Run alone, with the isolated environment of test_blender_read_tools.py::

  ANGELO_READ_BENCH_BLEND=/path/to/a/copy.blend "$ANGELO" --background --factory-startup \\
    --disable-autoexec --python-exit-code 1 --python tests/python/angelo_agent/benchmark_blender_read_tools.py

Two cohorts. ``file``: the .blend named by ``ANGELO_READ_BENCH_BLEND`` (give it
a copy: the benchmark copies it again into its own folder, opens that copy and
never writes to the original), read as a production scene is. ``synthetic``:
20,000 objects on 2,000 meshes with 500 materials and 200 image datablocks
whose files do not exist, the worst case of the file summary's one pass and
its existence checks; skipped with ``ANGELO_READ_BENCH_SYNTHETIC=0``.

Each operation reports the median of seven samples after one warmup: process
CPU milliseconds (concurrent work on a shared machine inflates wall time, not
this), wall milliseconds, and the characters of the answer's compact JSON, the
size a model receives. Workspace file reads include worker completion and
separately record main-thread dispatch time; other reads finish in the initial
call. There is no provider latency, UI timer wait or prompt transfer. Results go to
``$ANGELO_QA_DIR/read-tools-benchmark.json``.
"""

import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-read-tools-benchmark"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
# Source add-on first, so every module measured is the checkout's code.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.files import FileWorkspace  # noqa: E402
from angelo_agent.studio_agent import StudioPending  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402

SAMPLES = 7
READ, FILE = "angelo_read_data", "angelo_blend_file"


def measure(executor, name, arguments):
    arguments = validate_call(name, arguments)
    cpu, wall, dispatch, result = [], [], [], None
    for sample in range(SAMPLES + 1):
        started_cpu, started = time.process_time(), time.perf_counter()
        result = executor.execute(name, dict(arguments))
        dispatched = (time.perf_counter() - started) * 1000
        if isinstance(result, StudioPending):
            pending = result
            deadline = time.monotonic() + pending.timeout
            try:
                while (result := pending.poll()) is None:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Workspace metadata did not complete before its deadline")
                    time.sleep(.001)
            finally:
                pending.cancel()
        if sample:  # The first call is the warmup (imports, caches).
            cpu.append((time.process_time() - started_cpu) * 1000)
            wall.append((time.perf_counter() - started) * 1000)
            dispatch.append(dispatched)
    text = json.dumps(result, separators=(",", ":"))
    return {"cpu_ms": round(statistics.median(cpu), 3), "wall_ms": round(statistics.median(wall), 3),
            "dispatch_ms": round(statistics.median(dispatch), 3),
            "characters": len(text), "answered": result.get("found", result.get("readable")),
            "truncated": bool(result.get("truncated"))}


def operations(project_file):
    """The reads a turn makes, on whatever the open document holds."""
    scene = bpy.context.scene
    mesh_object = next((obj for obj in scene.objects if obj.type == "MESH" and obj.modifiers), None) or next(
        (obj for obj in scene.objects if obj.type == "MESH"), None)
    group = max(bpy.data.node_groups, key=lambda tree: len(tree.nodes), default=None)
    material = max((m for m in bpy.data.materials if m.node_tree), key=lambda m: len(m.node_tree.nodes), default=None)
    rows = [("scene, first page", READ, {"path": "scene"}),
            ("scene.render, 64 rows", READ, {"path": "scene.render", "limit": 64}),
            ("scene.objects, 64 rows", READ, {"path": "scene.objects", "limit": 64}),
            ("one property with choices", READ, {"path": "scene.render.engine"}),
            ("unknown property (valid names)", READ, {"path": "scene.render.no_such_setting"}),
            ("unknown object name", READ, {"path": 'data.objects["No Such Object"]'}),
            ("data, the datablock collections", READ, {"path": "data", "limit": 64})]
    if hasattr(scene, "cycles"):
        rows.append(("scene.cycles, 64 rows (add-on)", READ, {"path": "scene.cycles", "limit": 64}))
    if mesh_object is not None:
        path = 'scene.objects["%s"]' % mesh_object.name.replace("\\", "\\\\").replace('"', '\\"')
        rows += [("an object, first page", READ, {"path": path}),
                 ("its modifiers with values (depth 2)", READ, {"path": path + ".modifiers", "depth": 2}),
                 ("its mesh vertices, 64 rows", READ, {"path": path + ".data.vertices", "limit": 64})]
    if group is not None:
        path = 'data.node_groups["%s"].nodes' % group.name.replace("\\", "\\\\").replace('"', '\\"')
        rows += [("largest node group, 64 nodes", READ, {"path": path, "limit": 64}),
                 ("its nodes with values (depth 2, budget-bound)", READ, {"path": path, "limit": 64, "depth": 2}),
                 ("one node's input sockets", READ, {"path": path + "[%d].inputs" % (len(group.nodes) // 2)})]
    if material is not None:
        path = 'data.materials["%s"].node_tree.nodes' % material.name.replace("\\", "\\\\").replace('"', '\\"')
        rows.append(("largest material graph, 64 nodes", READ, {"path": path, "limit": 64}))
    rows += [("file summary", FILE, {}), ("file: meshes page", FILE, {"list": "meshes", "limit": 64}),
             ("file: unused data", FILE, {"list": "UNUSED", "limit": 64}),
             ("file: external files", FILE, {"list": "FILES", "limit": 64})]
    if project_file:
        rows.append(("another .blend's datablocks", FILE, {"file": "other.blend"}))
    return rows


def cohort(label, project, has_other):
    executor = BlenderExecutor(bpy.context.window_manager.windows[0])
    executor.studio_context = SimpleNamespace(files=FileWorkspace(project))
    counts = {definition.identifier: len(getattr(bpy.data, definition.identifier)) for definition in bpy.data.bl_rna.properties
              if definition.type == "COLLECTION" and definition.identifier != "all_ids" and len(getattr(bpy.data, definition.identifier))}
    nodes = sum(len(tree.nodes) for tree in bpy.data.node_groups) + sum(
        len(m.node_tree.nodes) for m in bpy.data.materials if m.node_tree)
    entry = {"label": label, "datablocks": sum(counts.values()), "by_type": counts, "nodes": nodes, "operations": {}}
    for name, tool, arguments in operations(has_other):
        entry["operations"][name] = measure(executor, tool, arguments)
        print("%-10s %-46s %s" % (label, name, entry["operations"][name]), flush=True)
    return entry


def synthetic(project):
    """A large document: many objects, shared meshes, materials with graphs and images whose files are missing."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    meshes = []
    for index in range(2000):
        mesh = bpy.data.meshes.new("Mesh %04d" % index)
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        meshes.append(mesh)
    materials = []
    for index in range(500):
        material = bpy.data.materials.new("Material %03d" % index)
        if material.node_tree is None:
            material.use_nodes = True
        materials.append(material)
    for index in range(200):
        image = bpy.data.images.new("image_%03d.png" % index, 4, 4)
        image.source, image.filepath = "FILE", "//textures/image_%03d.png" % index
        materials[index].node_tree.nodes.new("ShaderNodeTexImage").image = image
    for index in range(20000):
        obj = bpy.data.objects.new("Object %05d" % index, meshes[index % 1500])  # 500 meshes stay without a user.
        scene.collection.objects.link(obj)
        if index < 500:
            obj.data.materials.append(materials[index]) if not obj.data.materials else None
    bpy.ops.wm.save_as_mainfile(filepath=str(project / "synthetic.blend"))


def main():
    report = {"binary": bpy.app.binary_path, "blender": bpy.app.version_string, "samples": SAMPLES, "cohorts": []}
    source = os.environ.get("ANGELO_READ_BENCH_BLEND")
    if source:
        project = OUT / "file-project"
        project.mkdir(exist_ok=True)
        shutil.copy(source, project / "scene.blend")
        shutil.copy(source, project / "other.blend")
        bpy.ops.wm.open_mainfile(filepath=str(project / "scene.blend"))
        report["cohorts"].append(dict(cohort("file", project, True), source=Path(source).name,
                                      bytes=(project / "scene.blend").stat().st_size))
    if os.environ.get("ANGELO_READ_BENCH_SYNTHETIC", "1") != "0":
        project = OUT / "synthetic-project"
        project.mkdir(exist_ok=True)
        synthetic(project)
        shutil.copy(project / "synthetic.blend", project / "other.blend")
        report["cohorts"].append(dict(cohort("synthetic", project, True), bytes=(project / "synthetic.blend").stat().st_size))
    (OUT / "read-tools-benchmark.json").write_text(json.dumps(report, indent=2))
    print("ANGELO_READ_TOOLS_BENCHMARK", json.dumps({"cohorts": [entry["label"] for entry in report["cohorts"]]}), flush=True)


main()
