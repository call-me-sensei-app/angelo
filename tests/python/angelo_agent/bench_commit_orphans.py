# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What freeing replaced data costs and saves on the production commit path (``commit_orphans``).

Every edit goes through the real ``Controller`` and ``angelo.commit_native_job`` (``program_undo.commit`` with the
native bridge), as the chat and the headless host commit. Run the same command on the build under test and on the
build it is compared with (``8dd619e47f4``: no freeing), alone on a quiet machine::

    <Angelo> --background --factory-startup --disable-autoexec --python-exit-code 1 \\
        --python tests/python/angelo_agent/bench_commit_orphans.py -- --extra 10000 --edits 500 --out <folder>

``--warmup`` edits (10) run untimed first, on every arm. ``--extra`` adds that many objects, each with its own mesh and material (about three IDs each, so 10,000 is a file
of about 30,000 IDs). Edits cycle through five kinds on five spheres: a material edit, a normal recalculation and a
transform apply (each swaps data), and two transform-only kinds (no swap). Writes ``bench-commit-orphans.json``:
per-commit seconds (median, p75, max) of the swapping and the other commits, the time spent in
``commit_orphans`` (seal and free), and orphan counts and resident memory every 100 edits. No provider or paid call.
"""

import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[3]
ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def option(name, default):
    return type(default)(ARGS[ARGS.index(name) + 1]) if name in ARGS else default


EXTRA, EDITS, WARMUP = option("--extra", 10000), option("--edits", 500), option("--warmup", 10)
OUT = Path(option("--out", tempfile.mkdtemp(prefix="bench-commit-orphans-")))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path[:0] = [str(ROOT / "scripts/addons_core"), str(Path(__file__).resolve().parent)]
from angelo_agent import agent_workspace_ui, native_programs as native, program_undo  # noqa: E402
from angelo_agent import scene_queries as queries  # noqa: E402
from program_production_helpers import production_call, production_controller  # noqa: E402

try:
    from angelo_agent import commit_orphans
except ImportError:   # The build compared with has no freeing.
    commit_orphans = None
SPENT = {"seconds": 0.0, "calls": 0}


def timed(function):
    def wrapper(*args, **kwargs):
        started = time.perf_counter()
        try:
            return function(*args, **kwargs)
        finally:
            SPENT["seconds"] += time.perf_counter() - started
            SPENT["calls"] += 1
    return wrapper


if commit_orphans is not None:
    commit_orphans.seal, commit_orphans.free_replaced = timed(commit_orphans.seal), timed(commit_orphans.free_replaced)


def rss_mb():
    return int(subprocess.check_output(["ps", "-o", "rss=", "-p", str(os.getpid())]).strip()) / 1024


def orphans():
    return {name: sum(1 for block in getattr(bpy.data, name) if block.users == 0 and not block.use_fake_user)
            for name in ("meshes", "materials", "node_groups", "actions", "worlds")}


def fixture():
    for index in range(5):
        bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32, location=(index * 3, 0, 0))
        obj = bpy.context.active_object
        obj.name = "Ball %d" % index
        obj.data.materials.append(bpy.data.materials.new("Paint %d" % index))
    for index in range(EXTRA):
        data = bpy.data.meshes.new("Extra %d" % index)
        data.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        data.materials.append(bpy.data.materials.new("Extra Paint %d" % index))
        bpy.context.scene.collection.objects.link(bpy.data.objects.new("Extra %d" % index, data))
    path = OUT / "bench.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True)
    bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False, use_scripts=False)
    queries.invalidate()


def op(kind, obj, **parameters):
    return {"kind": kind, "target": queries.object_handle(obj), "parameters": parameters}


KINDS = (("swap", lambda o, i: [op("material_principled", o, name=o.data.materials[0].name,
                                   inputs={"Roughness": (i % 10) / 10})]),
         ("swap", lambda o, i: [op("mesh_recalculate_normals", o)]),
         ("other", lambda o, i: [op("transform", o, scale=[1.0 + (i % 3) / 10] * 3)]),
         ("swap", lambda o, i: [op("transform", o, scale=[1.0, 1.0, 1.0]), op("transform_apply", o, scale=True)]),
         ("other", lambda o, i: [op("transform", o, location=[o.location.x, (i % 5) * .1, 0.0])]))


def main():
    program_undo.keep_background_document()
    fixture()
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    classes = [c for c in agent_workspace_ui.workspace_operators() if getattr(c, "bl_idname", "") == "angelo.commit_native_job"]
    for cls in classes:
        bpy.utils.register_class(cls)
    window = bpy.context.window_manager.windows[0]
    seconds, rows, failures = {"swap": [], "other": []}, [{"edits": 0, "rss_mb": rss_mb(), "orphans": orphans()}], 0
    with production_controller(OUT) as controller:
        for index in range(-WARMUP, EDITS):   # The first WARMUP edits are untimed (warm caches, as on every arm).
            obj = window.scene.objects["Ball %d" % (index % 5)]
            kind, build = KINDS[(index // 5) % len(KINDS)]
            arguments = {"mode": "APPLY", "read_set": [], "expected_checks": [], "operations": build(obj, index),
                         "write_set": [{"handle": queries.object_handle(obj),
                                        "revision": queries.object_revision(window, obj)}]}
            started = time.perf_counter()
            result = production_call(controller, window, "edit %d" % index, "angelo_scene_program", arguments)
            if index < 0:
                if index == -1:   # The measured run starts here.
                    SPENT.update(seconds=0.0, calls=0)
                    rows[0] = {"edits": 0, "rss_mb": rss_mb(), "orphans": orphans()}
                continue
            seconds[kind].append(time.perf_counter() - started)
            failures += 0 if result.get("ok") else 1
            if (index + 1) % 100 == 0:
                rows.append({"edits": index + 1, "rss_mb": round(rss_mb(), 1), "orphans": orphans()})
                print("ROW", json.dumps(rows[-1]), flush=True)

    def summary(values):
        ordered = sorted(values)
        return {"count": len(ordered), "median": round(statistics.median(ordered), 4),
                "p75": round(ordered[int(len(ordered) * .75)], 4), "max": round(ordered[-1], 4)} if ordered else None
    report = {"binary": bpy.app.binary_path, "version": bpy.app.version_string, "extra_objects": EXTRA,
              "ids": sum(len(getattr(bpy.data, name)) for name in ("objects", "meshes", "materials")),
              "edits": EDITS, "warmup": WARMUP, "failures": failures, "swap": summary(seconds["swap"]), "other": summary(seconds["other"]),
              "commit_orphans": {"seconds": round(SPENT["seconds"], 3), "calls": SPENT["calls"]}
              if commit_orphans is not None else None, "rows": rows,
              "frees_replaced_data": all(callable(getattr(program_undo.native_bridge(), name, None))
                                         for name in ("seal_replaceable", "delete_replaced_data"))}
    (OUT / "bench-commit-orphans.json").write_text(json.dumps(report, indent=2))
    print("BENCH_COMMIT_ORPHANS", json.dumps({key: report[key] for key in ("swap", "other", "commit_orphans", "failures")}),
          flush=True)
    native.cleanup_all()


main()
