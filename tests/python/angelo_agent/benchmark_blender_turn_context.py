# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Measure turn context v2 and change-feed handler cost in the compiled binary.

Run alone, with the same isolated environment as test_blender_turn_context.py::

  "$ANGELO" --background --factory-startup --disable-autoexec --python-exit-code 1 \\
    --python tests/python/angelo_agent/benchmark_blender_turn_context.py

Reports process CPU milliseconds (medians of seven samples after one warmup),
not wall time: concurrent work on a shared machine inflates wall time but not
this process's CPU time. Cohorts put the active object and eight selected
objects at the end of scene order, the legacy observation's worst case. The
dense cohort's active mesh is a 316x316-vertex grid (99,856 vertices), just
inside the 100,000-vertex program input limit. Excludes provider latency, UI
timer waits and prompt transfer; it measures only the main-thread builder and
the depsgraph-handler recording added by the change feed.
"""

import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-turn-context-benchmark"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
# Source add-on first, so every module measured is the checkout's code.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import product_env  # noqa: E402
product_env.clear("TURN_CONTEXT")  # Every spelling the app honours, deprecated ones included.
from angelo_agent import change_feed, scene_queries as queries, turn_context

WINDOW = bpy.context.window_manager.windows[0]
CHAT = "benchmark-chat"
handler_samples = {"feed_ms": [], "invalidate_ms": [], "updates": []}


def cpu_median(function, samples=7):
    function()
    values = []
    for _ in range(samples):
        started = time.process_time()
        function()
        values.append((time.process_time() - started) * 1000)
    return round(statistics.median(values), 3)


def timed_handler(scene, depsgraph):
    """Times the existing invalidation and the added feed recording separately."""
    started = time.process_time()
    queries.invalidate(depsgraph=depsgraph)
    middle = time.process_time()
    change_feed.record_depsgraph(scene, depsgraph, queries.document_epoch())
    ended = time.process_time()
    handler_samples["invalidate_ms"].append((middle - started) * 1000)
    handler_samples["feed_ms"].append((ended - middle) * 1000)
    handler_samples["updates"].append(len(depsgraph.updates))


def build(count, dense=False):
    scene = WINDOW.scene
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)
    cube = bpy.data.meshes.new("Benchmark cube")
    cube.from_pydata([(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], [],
                     [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)])
    objects = []
    for index in range(count - (1 if dense else 0)):
        obj = bpy.data.objects.new("Object %05d" % index, cube)
        scene.collection.objects.link(obj)
        objects.append(obj)
    if dense:
        bpy.ops.mesh.primitive_grid_add(x_subdivisions=315, y_subdivisions=315, size=2)
        objects.append(WINDOW.view_layer.objects.active)
    for obj in scene.objects:
        obj.select_set(False)
    for obj in objects[-9:]:
        obj.select_set(True)
    WINDOW.view_layer.objects.active = objects[-1]
    WINDOW.view_layer.update()
    queries.invalidate()
    change_feed.clear()
    return objects


def cohort(name, count, dense=False):
    objects = build(count, dense)
    turn = turn_context.prepare(WINDOW, CHAT)
    turn.commit()
    result = {"objects": len(WINDOW.scene.objects), "active_vertices": len(objects[-1].data.vertices),
              "revisions": turn.stats["revisions"],
              "omitted": [e.get("revision_omitted") for e in turn.observation["objects"] if "revision_omitted" in e],
              "payload_chars": len(json.dumps(turn.observation, separators=(",", ":"))),
              "prepare_cpu_ms": cpu_median(lambda: turn_context.prepare(WINDOW, CHAT)),
              "legacy_cpu_ms": cpu_median(lambda: turn_context.legacy_observation(WINDOW)),
              # Name matching: a message naming no object, and one naming an
              # unselected object in the middle of scene order (one more revision).
              "prepare_unnamed_message_cpu_ms": cpu_median(lambda: turn_context.prepare(
                  WINDOW, CHAT, message="Make the selected props a little shinier and 10% smaller.")),
              "prepare_named_message_cpu_ms": cpu_median(lambda: turn_context.prepare(
                  WINDOW, CHAT, message="Scale '%s' to 2 m along X." % objects[len(objects) // 2].name)),
              "active_revision_cpu_ms": cpu_median(lambda: queries.object_revision(WINDOW, objects[-1])),
              # Context only: the default Every Request snapshot that the same send path
              # already writes (save_as_mainfile copy=True); CPU excludes disk waits.
              "request_snapshot_copy_cpu_ms": cpu_median(lambda: bpy.ops.wm.save_as_mainfile(
                  filepath=str(OUT / "snapshot_proxy.blend"), copy=True, check_existing=False), samples=3)}
    # Eight recently changed objects in the feed (the agent's or anyone's),
    # each found by name and revised; the feed is not acknowledged here.
    bpy.app.handlers.depsgraph_update_post.append(timed_handler)  # Records into the feed.
    try:
        for obj in objects[100:108] if len(objects) > 108 else objects[:8]:
            obj.location.z += 1.0
        WINDOW.view_layer.update()
    finally:
        bpy.app.handlers.depsgraph_update_post.remove(timed_handler)
    feed_turn = turn_context.prepare(WINDOW, CHAT)
    result["prepare_eight_changed_cpu_ms"] = cpu_median(lambda: turn_context.prepare(WINDOW, CHAT))
    result["changed_revisions"] = feed_turn.stats["revisions"] - turn.stats["revisions"]
    feed_turn.commit()
    # Handler cost: one transform, then a relations rebuild (a new linked ID),
    # which flags shading on every object.
    bpy.app.handlers.depsgraph_update_post.append(timed_handler)
    try:
        for kind in ("transform", "relations"):
            handler_samples.update(feed_ms=[], invalidate_ms=[], updates=[])
            for index in range(4):
                if kind == "transform":
                    objects[index].location.x += 1.0
                else:
                    extra = bpy.data.objects.new("Extra %d" % index, None)
                    WINDOW.scene.collection.objects.link(extra)
                WINDOW.view_layer.update()
            result[kind + "_handler"] = {
                "updates": max(handler_samples["updates"] or [0]),
                "feed_cpu_ms": round(statistics.median(handler_samples["feed_ms"]), 3) if handler_samples["feed_ms"] else None,
                "existing_invalidate_cpu_ms": round(statistics.median(handler_samples["invalidate_ms"]), 3) if handler_samples["invalidate_ms"] else None}
    finally:
        bpy.app.handlers.depsgraph_update_post.remove(timed_handler)
    print("ANGELO_TURN_CONTEXT_COHORT", name, json.dumps(result), flush=True)
    return result


def main():
    report = {"binary_path": bpy.app.binary_path, "version": bpy.app.version_string,
              "method": "process CPU ms, median of 7 after 1 warmup", "cohorts": {}}
    for name, count, dense in (("100_objects", 100, False), ("2000_objects", 2000, False),
                               ("20000_objects", 20000, False), ("100_objects_99856_vertex_active", 100, True)):
        report["cohorts"][name] = cohort(name, count, dense)
    (OUT / "benchmark.json").write_text(json.dumps(report, indent=2))
    print("ANGELO_TURN_CONTEXT_BENCHMARK", json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
