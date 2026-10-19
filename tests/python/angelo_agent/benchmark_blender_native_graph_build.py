# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native graph rebuilding after collection membership changes.

Run isolated --background --factory-startup with ANGELO_QA_DIR set. Each sample
links and unlinks an object, updating the view layer after each change. Both
rebuilds, evaluation and collection synchronization are included. No provider,
GPU or Undo cost is included. Interleave equally configured baseline/candidate
builds after other builds and tests stop.
"""

import json
import os
from pathlib import Path
import sys

import bpy

assert bpy.app.background and "--factory-startup" in sys.argv
sys.path.insert(0, str(Path(__file__).resolve().parent))
import perf_counters

OUT = Path(os.environ["ANGELO_QA_DIR"])
OUT.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.filepaths.temporary_directory = str(OUT)
report = {"binary": bpy.app.binary_path, "build_hash": bpy.app.build_hash.decode(),
          "method": "2 warmups + 9 samples; two graph rebuilds per sample; unique quad meshes",
          "cohorts": {}}

for size in (100, 1000, 5000):
    bpy.data.batch_remove(tuple(bpy.data.objects))
    bpy.data.batch_remove(tuple(bpy.data.meshes))
    objects = []
    for index in range(size):
        mesh = bpy.data.meshes.new("BuildMesh_%05d" % index)
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
        obj = bpy.data.objects.new("BuildObject_%05d" % index, mesh)
        obj.location.x = index
        bpy.context.scene.collection.objects.link(obj)
        objects.append(obj)
    bpy.context.view_layer.update()
    probe = bpy.data.objects.new("BuildProbe", None)

    def rebuild():
        bpy.context.scene.collection.objects.link(probe)
        bpy.context.view_layer.update()
        bpy.context.scene.collection.objects.unlink(probe)
        bpy.context.view_layer.update()

    summary, samples, _ = perf_counters.repeat(rebuild, samples=9, warmup=2)
    report["cohorts"][str(size)] = {"metrics": summary, "samples": samples}
    assert len(bpy.context.view_layer.objects) == size
    graph = bpy.context.evaluated_depsgraph_get()
    for obj in (objects[0], objects[-1]):
        evaluated = obj.evaluated_get(graph)
        assert tuple(evaluated.matrix_world.translation) == tuple(obj.location)
        assert len(evaluated.data.vertices) == 4 and len(evaluated.data.polygons) == 1
    print("NATIVE_GRAPH_BUILD", size, json.dumps(summary), flush=True)

report["success"] = True
(OUT / "results.json").write_text(json.dumps(report, indent=2))
