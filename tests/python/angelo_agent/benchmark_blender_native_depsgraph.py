# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native graph update costs for unchanged, sparse and dense edits.

Run in isolated --background --factory-startup with --python-exit-code 1.
ANGELO_QA_DIR selects the report directory. Compare the same build configuration
before and after; interleave binary runs after compilation has stopped.
No provider, GPU, Undo, or timer polling is included in these timings.
"""

import json
import os
from pathlib import Path
import sys

import bpy

assert bpy.app.background and "--factory-startup" in sys.argv
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import perf_counters

OUT = Path(os.environ["ANGELO_QA_DIR"])
OUT.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.filepaths.temporary_directory = str(OUT)
report = {"binary": bpy.app.binary_path, "build_hash": bpy.app.build_hash.decode(),
          "method": "3 warmups + 21 samples; wall, CPU and instructions; generated empty objects",
          "load_average": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
          "cohorts": {}}


def measure(function):
    summary, samples, _ = perf_counters.repeat(function, samples=21, warmup=3)
    return {"metrics": summary, "samples": samples}


for size in (100, 1000, 10000):
    bpy.data.batch_remove(tuple(bpy.data.objects))
    objects = []
    for index in range(size):
        obj = bpy.data.objects.new("NativeBench_%05d" % index, None)
        bpy.context.scene.collection.objects.link(obj)
        objects.append(obj)
    bpy.context.view_layer.update()
    target = objects[-1]

    def sparse():
        target.location.x += .125
        bpy.context.view_layer.update()

    def dense():
        for obj in objects:
            obj.location.x += .125
        bpy.context.view_layer.update()

    values = {"unchanged": measure(bpy.context.view_layer.update),
              "sparse_transform": measure(sparse), "all_transforms": measure(dense)}
    graph = bpy.context.evaluated_depsgraph_get()
    for obj in (objects[0], target):
        assert tuple(obj.evaluated_get(graph).matrix_world.translation) == tuple(obj.location)
    report["cohorts"][str(size)] = values
    print("NATIVE_DEPSGRAPH_BENCHMARK", size, json.dumps(values), flush=True)

report["success"] = True
(OUT / "results.json").write_text(json.dumps(report, indent=2))
