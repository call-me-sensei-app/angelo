# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a ``batch`` costs as it grows: staging, verification, commit, Undo and Redo by copies made.

Run in an isolated process, after other benchmarks and builds have stopped::

    Angelo --background --factory-startup --disable-autoexec --python-exit-code 1 \\
        --python tests/python/angelo_agent/benchmark_blender_batch.py -- [--sizes 10,100,1000] [--samples 3]

Set ``ANGELO_QA_DIR`` to a new temporary directory; state stays inside it. No
provider, account or paid call.

Each case is one real program (``tool_specs.validate_call``, the cooperative
job polled to ready, the commit operator, then ``ed.undo`` and ``ed.redo``)
that creates its unit and one batch of ``size`` instances:

* ``linked_row``: a cube with one material, a row; copies share the mesh.
* ``full_row``: the same row with ``linked: false``; every copy owns a mesh.
* ``unit``: a post with a Bevel modifier and a lantern parented to it, a row
  mirrored across Y with a seeded rotation and scale per instance (the general
  matrix path); two objects per instance, so ``size`` objects in ``size / 2``
  instances.
* ``scatter``: an ico sphere scattered in a box with a seeded scale and
  rotation, mirrored across X.

Reported per case and size, the median of ``--samples`` runs in milliseconds:
``validate`` (the plan check, before anything is staged), ``stage_verify``
(begin to ready: copying the unit, the batch kernel, stage budgets and the
derived checks, over ``polls`` cooperative polls, the longest of which is
``longest_poll``), ``verification`` (the checks alone), ``commit`` (the
operator: transfer, rollback journal and the Undo push), ``undo`` and ``redo``,
and the size of the result a model's context is cut from. Staging and commit
also report CPU time and retired instructions (``perf_counters``): wall time
moves with everything else the machine is doing, instructions count the work.
"""

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-batch-benchmark"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import perf_counters  # noqa: E402
from angelo_agent import native_programs as native, scene_queries as queries  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import commit_operator, no_stages  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--sizes", default="10,100,1000")
parser.add_argument("--samples", type=int, default=3)
options = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
window = bpy.context.window_manager.windows[0]


def op(kind, target=None, **parameters):
    return {"kind": kind, "target": target, "parameters": parameters}


def material(target, name):
    return op("material_principled", target, name=name, inputs={"Base Color": [0.5, 0.3, 0.1, 1]})


def cases(size):
    """{case: (operations, objects per instance)} for ``size`` instances."""
    vary = {"seed": 3, "rotation": [[0, 0, 0], [0, 0, 6.28]], "scale": [[0.8, 0.8, 0.8], [1.2, 1.2, 1.2]]}
    cube = op("create_primitive", primitive="CUBE", size=0.2, name="Bench Cube 0001", alias="cube", location=[0, 0, 5])
    half = max(1, size // 2)
    return {
        "linked_row": ([cube, material("$cube", "Bench Paint"),
                        op("batch", sources=["$cube"], array=[{"step": [0.3, 0, 0], "count": size}])], 1),
        "full_row": ([cube, material("$cube", "Bench Paint"),
                      op("batch", sources=["$cube"], linked=False, array=[{"step": [0.3, 0, 0], "count": size}])], 1),
        "unit": ([op("create_primitive", primitive="CUBE", size=1, name="Bench Post 0001", alias="post",
                     location=[0, 2, 1], scale=[0.1, 0.1, 2]),
                  op("create_primitive", primitive="UV_SPHERE", name="Bench Lantern 0001", alias="lantern", radius=0.2,
                     segments=16, rings=8, location=[0, 2, 2.2]),
                  op("parent_set", "$lantern", parent="$post", keep_transform=True),
                  op("modifier_add", "$post", type="BEVEL", settings={"width": 0.01, "segments": 2}),
                  material("$post", "Bench Iron"), material("$lantern", "Bench Glow"),
                  op("batch", sources=["$post", "$lantern"], array=[{"step": [0.5, 0, 0], "count": max(1, size // 4)}],
                     mirror="Y", vary=vary)], 2),
        "scatter": ([op("create_primitive", primitive="ICO_SPHERE", name="Bench Rock 0001", alias="rock", radius=0.3,
                        subdivisions=2, location=[5, 0, 0]),
                     material("$rock", "Bench Stone"),
                     op("batch", sources=["$rock"], mirror="X", vary=vary,
                        scatter={"count": half, "min": [5, -50, 0], "max": [9, 50, 0.5]})], 1),
    }


def measure(operations):
    bpy.ops.ed.undo_push(message="batch benchmark baseline")
    queries.invalidate()
    before = len(window.scene.objects)
    clock = time.perf_counter
    start = clock()
    program = validate_call("angelo_scene_program", {"mode": "APPLY", "operations": operations})
    validated = clock()
    counters = perf_counters.read()
    job = native.begin(window, "angelo_scene_program", program)
    polls, longest = 0, 0.0
    while True:
        began = clock()
        result = job.poll()
        polls, longest = polls + 1, max(longest, clock() - began)
        if result.get("final") or result.get("ready_to_commit"):
            break
    staged = clock()
    staging = perf_counters.difference(counters, perf_counters.read())
    assert result.get("ready_to_commit"), result.get("error")
    counters = perf_counters.read()
    done = bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    committed = clock()
    committing = perf_counters.difference(counters, perf_counters.read())
    assert "FINISHED" in done and job.result.get("status") == "committed", job.result.get("error")
    assert all(row.get("passed") for row in job.result["checks"]) and no_stages()
    added = len(window.scene.objects) - before
    row = {"validate": validated - start, "stage_verify": staged - validated, "polls": polls, "longest_poll": longest,
           "verification": job.verification_ms / 1000, "commit": committed - staged,
           "result_bytes": len(json.dumps(job.result, default=str)), "added": added,
           "stage_verify_cpu": staging["cpu_ms"] / 1000, "commit_cpu": committing["cpu_ms"] / 1000,
           "stage_verify_instructions": staging["instructions"], "commit_instructions": committing["instructions"]}
    began = clock()
    bpy.ops.ed.undo()
    row["undo"] = clock() - began
    assert len(bpy.context.window_manager.windows[0].scene.objects) == before
    began = clock()
    bpy.ops.ed.redo()
    row["redo"] = clock() - began
    assert len(bpy.context.window_manager.windows[0].scene.objects) == before + added
    bpy.ops.ed.undo()  # Leave the document as it was for the next sample.
    queries.invalidate()
    return row


def main():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator({}))
    measure(cases(4)["linked_row"][0])  # Warm imports and the first stage outside the samples.
    report = {"binary": bpy.app.binary_path, "version": bpy.app.version_string, "samples": options.samples, "rows": []}
    for size in [int(value) for value in options.sizes.split(",")]:
        for name, (operations, per_instance) in cases(size).items():
            samples = [measure(operations) for _ in range(options.samples)]
            row = {"case": name, "instances": samples[0]["added"] // per_instance, "objects_added": samples[0]["added"],
                   "polls": samples[0]["polls"], "result_bytes": samples[0]["result_bytes"]}
            for key in ("validate", "stage_verify", "stage_verify_cpu", "longest_poll", "verification", "commit",
                        "commit_cpu", "undo", "redo"):
                row[key + "_ms"] = round(statistics.median(sample[key] for sample in samples) * 1000, 1)
            for key in ("stage_verify_instructions", "commit_instructions"):
                counts = [sample[key] for sample in samples if sample[key] is not None]
                row[key + "_millions"] = round(statistics.median(counts) / 1e6, 1) if counts else None
            report["rows"].append(row)
            print("BATCH_BENCHMARK", json.dumps(row), flush=True)
    (OUT / "batch-benchmark.json").write_text(json.dumps(report, indent=1))


try:
    main()
finally:
    native.cleanup_all()
