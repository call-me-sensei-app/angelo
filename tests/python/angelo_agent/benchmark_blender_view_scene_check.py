# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What ``view_scene_check`` adds to every RENDERED look and motion strip.

Run in an isolated process, after other benchmarks and builds have stopped::

    Angelo --background --factory-startup --python-exit-code 1 \\
        --python tests/python/angelo_agent/benchmark_blender_view_scene_check.py \\
        -- [--samples 7] [--scenes small,medium,large]

A look reads the document as it starts (``SceneCheck``) and compares it when
it ends (``SceneCheck.finish``); a strip does the same around its frames. Both
readings run on the main thread. One sample is that pair on an unchanged
document; what the view itself writes and puts back in between is left out,
because that is the view's work, not the check's. So is the depsgraph update
the look's restore tags, which ``finish`` runs early when add-on handlers ran:
Blender runs it on its next event in any case, and nothing is tagged here. Two
cases per scene:

* ``no_handlers``: no Python from add-ons or scripts can run during the view
  (``SceneCheck(window, deep=False)``): every look and strip in the Read-only
  and Ask modes, and in the Auto modes without such handlers;
* ``handlers``: depsgraph-update handlers from an add-on run during the view
  (``deep=True``), which the Auto modes allow.

Scenes: ``small``, the factory startup scene (cube, camera, light);
``medium``, 50 objects with their own 64 x 64 grids (204,800 vertices) and two
1,024 x 1,024 images; ``large``, 2,000 objects with their own 23 x 23 grids
(1,058,000 vertices), one shared node material, a Bevel on every tenth object
and the same two images. Each measured case is followed by one unmeasured check
in which an object moves between the readings, which must report it, so every
sample measures a working check. The report (``view-scene-check.json``) holds
wall time, process CPU time and retired instructions per case, and a
``ratchet`` section with the median instructions of each
(``view_check/<scene>/<case>``), which ``test_blender_perf_ratchet`` keeps
from growing.

It also measures the policy check every other view that writes makes first
(``view_policy``), when no add-on Python is registered: a SOLID capture
(``display``: the message bus for its display settings), a MATERIAL capture
over a SOLID viewport (``material``: also every object's mode, read natively,
because that type change re-evaluates sculpt sessions) and a saved Render
Result picture (``saved_picture``: the depsgraph-update lists and the message
bus for its image format), as ``view_policy/<scene>/<case>``.

A check without add-on Python is about 3 million instructions, so a garbage
collection of the whole process's Python objects inside one sample doubled it
(3.6 to 7.5 million in one run). Each sample therefore starts after a
collection, with the collector off while it runs, and counts the main thread's
instructions, where the check runs (``perf_counters.read_thread_counts``).
"""

import argparse
import gc
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import bpy

assert "--background" in sys.argv and "--factory-startup" in sys.argv
sys.path.insert(0, str(Path(__file__).resolve().parent))
import benchmark_fixtures as fixtures  # noqa: E402
import perf_counters  # noqa: E402

OUT = fixtures.isolate(fixtures.default_output("angelo-view-scene-check-benchmark"))
from angelo_agent import render_extensions, view_capture, view_policy, view_render_restore  # noqa: E402
from angelo_agent import view_scene_check  # noqa: E402

SCENES = ("small", "medium", "large")
CASES = {"no_handlers": False, "handlers": True}
# The settings a look writes and puts back itself (view_render.LookRunner._values): its restore reports them.
OWN = ("render.resolution_x", "render.resolution_y", "render.resolution_percentage", "render.filepath",
       "render.use_file_extension", "render.use_render_cache", "render.use_auto_generate_texture_cache")
IMAGE_SIZE = 1024


def parse(argv):
    parser = argparse.ArgumentParser(description="View scene-check benchmark")
    parser.add_argument("--samples", type=int, default=7, help="measured checks per case after one warmup")
    parser.add_argument("--scenes", default=",".join(SCENES))
    args = parser.parse_args(argv)
    args.scenes = [value for value in args.scenes.split(",") if value]
    if not 1 <= args.samples <= 30 or not args.scenes or set(args.scenes) - set(SCENES):
        parser.error("Use 1-30 samples and scenes from " + ", ".join(SCENES))
    return args


def build(window, scene):
    """The scene's objects (``small`` keeps the factory scene); returns its counts."""
    if scene != "small":
        fixtures.clear_scene_data()
        root = bpy.data.collections.new("Benchmark view check")
        window.scene.collection.children.link(root)
        count, segments = (50, 63) if scene == "medium" else (2000, 22)
        material = fixtures.shared_node_material() if scene == "large" else None
        for index in range(count):
            obj = bpy.data.objects.new("Benchmark object %04d" % index,
                                       fixtures.new_mesh("Benchmark mesh %04d" % index, fixtures.grid(segments)))
            obj.location = (index % 50 * 3, index // 50 * 3, 0)
            if material is not None:
                obj.data.materials.append(material)
                if index % 10 == 0:
                    obj.modifiers.new("Bevel", "BEVEL")
            root.objects.link(obj)
        for name in ("Benchmark base color", "Benchmark roughness"):
            if name not in bpy.data.images:
                bpy.data.images.new(name, IMAGE_SIZE, IMAGE_SIZE)
    window.view_layer.update()
    return dict(fixtures.fixture_counts(window), images=len(bpy.data.images),
                data_blocks=sum(len(getattr(bpy.data, name, ())) for name in view_scene_check.COLLECTIONS))


def check(window, deep):
    return view_scene_check.SceneCheck(window, deep=deep).finish(OWN)


def measure(function, samples):
    """One warmup and ``samples`` calls, each after a collection and with the collector off:
    ``(stats as benchmark_fixtures.measure gives them, last answer)``."""
    raw, answer = [], None
    for index in range(samples + 1):
        gc.collect()
        gc.disable()
        try:
            start = (perf_counters.read_thread_counts() or {}).get("instructions"), time.process_time_ns()
            wall = time.perf_counter_ns()
            answer = function()
            wall = time.perf_counter_ns() - wall
            end = (perf_counters.read_thread_counts() or {}).get("instructions"), time.process_time_ns()
        finally:
            gc.enable()
        if index:
            raw.append({"wall_ms": wall / 1e6, "cpu_ms": (end[1] - start[1]) / 1e6,
                        "instructions": None if start[0] is None else end[0] - start[0]})
    walls = [sample["wall_ms"] for sample in raw]
    stats = {"samples_ms": [round(value, 3) for value in walls], "median_ms": perf_counters.describe(walls)["median"],
             "p95_ms": perf_counters.describe(walls)["p95"],
             "cpu_ms": perf_counters.describe([sample["cpu_ms"] for sample in raw]),
             "instructions": perf_counters.describe([sample["instructions"] for sample in raw]),
             "counter": "main thread"}
    return stats, answer


def finds_a_move(window, deep):
    """One unmeasured check with an object moved between the readings: it must be reported."""
    target = window.scene.objects[0]
    scene_check = view_scene_check.SceneCheck(window, deep=deep)
    saved = target.location.x
    target.location.x = saved + 1.0
    window.view_layer.update()
    try:
        return scene_check.finish(OWN)
    finally:
        target.location.x = saved
        window.view_layer.update()


def policies(window):
    """The policy check of each view kind that writes, with no add-on Python registered: ``{case: function}``."""
    space = SimpleNamespace(shading=SimpleNamespace(type="SOLID"), overlay=SimpleNamespace(show_overlays=False))
    values = view_render_restore.picture_values(window.scene)[0]  # The save's own settings: the view's work.

    def saved_picture():
        return render_extensions.view_report(view_render_restore.written_keys(window.scene, values), True).view_python

    def display(shading):
        return render_extensions.view_report(view_capture.display_keys(shading),
                                             view_policy.display_dirties(space, shading)).view_python
    return {"display": lambda: display("SOLID"), "material": lambda: display("MATERIAL"),
            "saved_picture": saved_picture}


def main():
    args = parse(fixtures.script_arguments())
    window = SimpleNamespace(scene=bpy.context.scene, view_layer=bpy.context.view_layer)
    report = {"environment": fixtures.environment(), "samples": args.samples, "warmup": 1, "scenes": {}}
    ratchet = fixtures.Ratchet()
    for scene in args.scenes:
        entry = report["scenes"][scene] = {"counts": build(window, scene), "cases": {}}
        for case, deep in CASES.items():
            stats, answer = measure(lambda: check(window, deep), args.samples)
            assert answer["changes"] == [], answer
            moved = finds_a_move(window, deep)
            assert any(change.startswith("object ") for change in moved["changes"]), moved
            entry["cases"][case] = dict(stats, complete=answer["complete"], moved=moved["changes"])
            ratchet.add("view_check/%s/%s" % (scene, case), stats)
            print("VIEW_CHECK", scene, case, json.dumps({"median_ms": stats["median_ms"], "instructions": (
                stats["instructions"] or {}).get("median"), "complete": answer["complete"]}), flush=True)
        for case, function in policies(window).items():
            stats, answer = measure(function, args.samples)
            assert answer == [], answer  # Nothing from add-ons: the fast path.
            entry.setdefault("policies", {})[case] = stats
            ratchet.add("view_policy/%s/%s" % (scene, case), stats)
            print("VIEW_POLICY", scene, case, json.dumps({"median_ms": stats["median_ms"], "instructions": (
                stats["instructions"] or {}).get("median")}), flush=True)
    report["ratchet"] = ratchet.section()
    report["success"] = True
    (OUT / "view-scene-check.json").write_text(json.dumps(report, indent=2, allow_nan=False))
    print("VIEW_CHECK_BENCHMARK_DONE", json.dumps({"success": True, "results": str(OUT / "view-scene-check.json")}),
          flush=True)


main()
