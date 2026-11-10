# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A held goal's host measurement in Blender (``goal_measure``; doc/angelo/goal_mode.rst, "Checks").

Run by ``run_native_suites.py --gui --include test_blender_goal_measure`` (the silhouette is drawn offscreen, which
needs a window). The reference is a disc drawn here, 128 pixels in radius on 512, seen by an orthographic camera
whose 512 pixels span 4 units, so a unit sphere at the origin fills it exactly and a cube of side 2 overlaps it by
pi / 4 (0.785):

* an empty scene is unmeasured, with why; a cube measures pi / 4; the sphere that replaces it measures 0.95 or more:
  an edit toward the shape raises the overlap;
* targets are a rule resolved at every measurement: an object added later is drawn (VISIBLE), a collection measures
  only its objects, an object off camera is never drawn, a floor that covers the picture is noted, and Undo between
  rounds (new object handles) leaves the goal measurable;
* SCENE checks bind each named object's current revision; a deleted object is an unmeasured round;
* a measurement leaves the document, viewports and data-blocks unchanged and writes no file; its time at 512 px.
Inspect results.json; a zero exit status alone is not proof.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback
from types import SimpleNamespace

import bpy
import numpy as np

assert "--factory-startup" in sys.argv, "Use an isolated factory-startup instance"
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-goal-measure-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path[:0] = [str(ROOT / "scripts/addons_core"), str(HERE)]
from angelo_agent import goal_measure, goal_state as G, scene_queries  # noqa: E402
from angelo_agent.references import ReferenceStore  # noqa: E402
from view_outline_kit import attach, snapshot  # noqa: E402

report = {"checks": {}, "measurements": {}, "binary": bpy.app.binary_path}
CAMERA = {"location": [0.0, -6.0, 0.0], "look_at": [0.0, 0.0, 0.0], "ortho_scale": 4.0}
SIZE, RADIUS = 512, 128.0


def check(name, condition):
    report["checks"][name] = bool(condition)


def disc():
    """White with a black disc of RADIUS pixels at the centre: a reference on a plain background."""
    yy, xx = np.mgrid[0:SIZE, 0:SIZE] + 0.5
    inside = (xx - SIZE / 2) ** 2 + (yy - SIZE / 2) ** 2 <= RADIUS ** 2
    rows = np.full((SIZE, SIZE, 4), 255, np.uint8)
    rows[inside, :3] = 0
    return rows


def measure(ctx, label, targets="VISIBLE"):
    ctx["goal"]["measure"]["targets"] = targets
    started = time.perf_counter()
    with bpy.context.temp_override(window=ctx["window"]):
        found = goal_measure.measure(ctx["chat"], ctx["goal"], ctx["window"])
    found = dict(found, ms=round((time.perf_counter() - started) * 1000, 1))
    report["measurements"][label] = {key: found.get(key) for key in (
        "measured", "reason", "iou", "drawn", "objects", "notes", "ms", "passed_count", "total", "all_passed")}
    return found


def run_all():
    window = bpy.context.window_manager.windows[0]
    ctx = {"window": window}
    scene = window.scene
    scene.render.resolution_x = scene.render.resolution_y = SIZE
    bpy.data.objects.remove(scene.objects["Cube"])
    refs = ReferenceStore(OUT, "7a1d2c3b-4e5f-4a6b-8c7d-9e0f1a2b3c4d")
    ctx["chat"] = SimpleNamespace(references=refs)
    reference = attach(refs, OUT, "disc.png", disc())
    goal = G.new("Match the disc", G.WORDS, check=G.REFERENCE)
    goal["measure"] = {"reference": reference, "camera": dict(CAMERA), "targets": "VISIBLE", "locked": False}
    ctx["goal"] = goal
    check("checkout_modules_loaded", str(ROOT) in goal_measure.__file__)

    empty = measure(ctx, "empty")
    check("an_empty_scene_is_unmeasured_with_why", empty["measured"] is False and "camera's view" in empty["reason"])

    bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0, 0, 0))
    cube = measure(ctx, "cube")
    check("a_cube_measures_pi_over_four", cube["measured"] and abs(cube["iou"] - np.pi / 4) < 0.03)

    bpy.data.objects.remove(scene.objects["Cube"])
    bpy.ops.mesh.primitive_uv_sphere_add(radius=1.0, segments=96, ring_count=48, location=(0, 0, 0))
    sphere_obj = bpy.context.active_object
    sphere_obj.name = "Body"
    sphere = measure(ctx, "sphere")
    check("an_edit_toward_the_shape_raises_the_overlap", sphere["measured"] and sphere["iou"] >= 0.95
          and sphere["iou"] > cube["iou"])
    check("its_time_at_512_px_is_under_a_second", sphere["ms"] < 1000)

    before = snapshot(window)
    files_before = sorted(p.name for p in OUT.rglob("*") if p.is_file())
    measure(ctx, "read_only")
    check("a_measurement_leaves_the_document_and_viewports_unchanged", snapshot(window) == before)
    check("a_measurement_writes_no_file", sorted(p.name for p in OUT.rglob("*") if p.is_file()) == files_before)

    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.4, location=(1.4, 0, 1.4))
    bpy.context.active_object.name = "Lamp"
    added = measure(ctx, "added_later")
    check("an_object_added_later_is_drawn", added["drawn"] == 2 and {o["name"] for o in added["objects"]} == {
        "Body", "Lamp"} and added["iou"] < sphere["iou"])

    hut = bpy.data.collections.new("Hut")
    scene.collection.children.link(hut)
    hut.objects.link(sphere_obj)
    for owner in list(sphere_obj.users_collection):   # Added to the active collection, whichever the startup made.
        if owner != hut:
            owner.objects.unlink(sphere_obj)
    only = measure(ctx, "collection", "COLLECTION:Hut")
    check("a_collection_measures_only_its_objects", only["drawn"] == 1 and only["iou"] >= 0.95)
    gone = measure(ctx, "missing_collection", "COLLECTION:Barn")
    check("a_missing_collection_is_unmeasured", gone["measured"] is False and "Barn" in gone["reason"])

    bpy.ops.mesh.primitive_cube_add(size=4.0, location=(60.0, 0, 0))
    bpy.context.active_object.name = "FarAway"
    off = measure(ctx, "off_camera")
    check("an_object_off_camera_is_never_drawn", off["drawn"] == 2 and "FarAway" not in {
        o["name"] for o in off["objects"]})

    bpy.ops.mesh.primitive_plane_add(size=40.0, location=(0, 2.0, 0), rotation=(np.pi / 2, 0, 0))
    bpy.context.active_object.name = "Backdrop"
    floor = measure(ctx, "backdrop")
    check("an_object_covering_the_picture_is_noted", any(note["code"] == "fills_frame" and "Backdrop" in note["detail"]
                                                         for note in floor["notes"]))
    bpy.data.objects.remove(scene.objects["Backdrop"])

    with bpy.context.temp_override(window=window):
        bpy.ops.ed.undo_push(message="goal measure: before a move")
        scene.objects["Lamp"].location.x += 0.5
        bpy.ops.ed.undo_push(message="goal measure: moved")
        bpy.ops.ed.undo()
    undone = measure(ctx, "after_undo")
    check("undo_between_rounds_leaves_it_measurable", undone["measured"] and undone["drawn"] == 2)

    scene = bpy.context.scene   # Undo re-reads a changed scene: the reference held from before it is gone.
    body = scene.objects.get("Body")
    handle = scene_queries.object_handle(body)
    goal_checks = G.new("Keep the body", G.WORDS, check=G.SCENE)
    goal_checks["measure"] = {"checks": [{"kind": "object_exists", "target": handle, "expected": True}], "locked": False}
    ctx["goal"] = goal_checks
    with bpy.context.temp_override(window=window):
        passed = goal_measure.measure(ctx["chat"], goal_checks, window)
        body.location.z += 0.1   # An edit changes its revision: the measurement binds the current one.
        moved = goal_measure.measure(ctx["chat"], goal_checks, window)
        bpy.data.objects.remove(body)
        deleted = goal_measure.measure(ctx["chat"], goal_checks, window)
    report["measurements"]["scene_checks"] = {"passed": passed, "moved": moved, "deleted": deleted}
    check("scene_checks_bind_the_current_revision", passed.get("all_passed") and moved.get("all_passed"))
    check("a_deleted_object_is_an_unmeasured_round", deleted.get("measured") is False)


def run():
    try:
        run_all()
        report["success"] = all(report["checks"].values())
    except Exception:
        report.update(success=False, error=traceback.format_exc())
    (OUT / "results.json").write_text(json.dumps(report, indent=2, default=str))
    print("ANGELO_GOAL_MEASURE", json.dumps({"success": report["success"], "failed": sorted(
        name for name, passed in report["checks"].items() if not passed)}), flush=True)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(run, first_interval=1)
