# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Retopology in Director, the view people start in (doc/angelo/retopology.rst).

Real default startup (Director layout and chat), real window events through ``Window.event_simulate`` wherever a
person would use them. The scene: one scan-like mesh (a dense sphere with seeded noise, about 20,000 triangles). The
scenario:

* The chat bar's + menu lists Retopology in its Model group (``chat_destinations``), running ``angelo.retopology``.
* With no mesh selected, the row (``angelo.retopology``, as the chat bar runs it) says to select one and changes
  nothing.
* With the scan selected, the row opens Retopology's options; Return retopologizes it as a background job (the
  interface stays live), and the result is a new object, all quads, near the default 5,000 faces, carrying its
  facts (``_angelo_retopology``), the only selected and the active object; the scan itself is unchanged.
* Ctrl+Z removes the result in one step, the scan untouched; Ctrl+Shift+Z brings it back.

Stand-in provider CLIs (``fake_provider_cli.py``) answer the start-up sign-in checks; no account or inference. Run
through ``run_native_suites.py --gui --include test_blender_retopology_director_gui``; check
``retopology-director.json`` for ``success`` and ``phase == "complete"``. Screenshots after each visible step.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import hashlib
import json
import math
import random
import tempfile
import time
import traceback
from pathlib import Path

import bmesh
import bpy
from mathutils import Euler

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-retopology-director-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_provider_cli  # noqa: E402
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

product_env.clear('DISABLE_DEFAULT_CHAT')  # This suite needs the default Director startup.

os.environ["ANGELO_FAKE_CLI_STATE"] = str(OUT / "fake-cli")
os.environ["PATH"] = str(fake_provider_cli.install(OUT / "bin")) + os.pathsep + os.environ.get("PATH", "")
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
REPORT = OUT / "retopology-director.json"
SOURCE = "Scan"
RESULT = SOURCE + "_retopo"
DEFAULT_FACES = 5000
state = {"phase": "boot", "success": False, "checks": {}, "steps": {}, "screenshots": {},
         "fixture": "Default Director startup, stand-in Claude connected, one scan-like mesh (seeded noise), "
                    "synthetic window events; no provider, account or inference"}
started = time.monotonic()


def window():
    return bpy.context.window_manager.windows[0]


def view():
    area = max((a for a in window().screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    return area, next(r for r in area.regions if r.type == 'WINDOW'), area.spaces.active


def send(kind, value='NOTHING', x=None, y=None, **extra):
    _, region, _ = view()
    window().event_simulate(type=kind, value=value, x=region.x + region.width // 2 if x is None else int(x),
                            y=region.y + region.height // 2 if y is None else int(y), **extra)


def tap(kind, **extra):
    send(kind, 'PRESS', **extra)
    send(kind, 'RELEASE', **extra)


def digest(obj):
    """The mesh's positions and faces, to show it did not change."""
    mesh = obj.data
    h = hashlib.sha256()
    for v in mesh.vertices:
        h.update(("%.6f %.6f %.6f;" % tuple(v.co)).encode())
    for p in mesh.polygons:
        h.update((",".join(map(str, p.vertices)) + ";").encode())
    return h.hexdigest()[:16]


def snapshot():
    source, result = bpy.data.objects.get(SOURCE), bpy.data.objects.get(RESULT)
    facts = dict(result["_angelo_retopology"]) if result is not None and "_angelo_retopology" in result else None
    return {"objects": sorted(o.name for o in bpy.data.objects), "meshes": len(bpy.data.meshes),
            "source": digest(source) if source else None,
            "result": None if result is None else {
                "faces": len(result.data.polygons),
                "quads": sum(1 for p in result.data.polygons if p.loop_total == 4),
                "selected": result.select_get(), "active": bpy.context.view_layer.objects.active == result,
                "facts": None if facts is None else {k: facts[k] for k in (
                    "faces", "quad_ratio", "irregular_vertices", "hausdorff_max_of_size", "field_solves")
                    if k in facts}}}


def scene_fixture():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    mesh = bpy.data.meshes.new(SOURCE)
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=5, radius=1.0)
    rng = random.Random(20261118)
    for v in bm.verts:   # A scan's noise: well below the output edge, steep facets.
        v.co *= 1.0 + 0.012 * (rng.random() * 2.0 - 1.0)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(SOURCE, mesh)
    bpy.context.scene.collection.objects.link(obj)
    _, _, space = view()
    rv3d = space.region_3d
    rv3d.view_perspective = 'ORTHO'
    rv3d.view_rotation = Euler((math.radians(70), 0, math.radians(30))).to_quaternion()
    rv3d.view_location = (0, 0, 0)
    rv3d.view_distance = 4
    space.shading.type = 'SOLID'
    space.overlay.show_wireframes = True   # The topology itself, before and after.
    state["source_digest"] = digest(obj)
    state["source_triangles"] = len(mesh.polygons)
    # A timer's change pushes no Undo step (as photo_drop_jobs does): one for the scene, so the step Ctrl+Z goes
    # back to after Retopology is this scan, not the start-up scene.
    with bpy.context.temp_override(window=window()):
        bpy.ops.ed.undo_push(message="Scan")


def deselect_all():
    for obj in bpy.context.view_layer.objects:
        obj.select_set(False)
    bpy.context.view_layer.objects.active = None


def row():
    """What the chat bar's + menu row runs (``chat_destinations`` Feature ``retopology``): its operator, invoked."""
    area, region, _ = view()
    with bpy.context.temp_override(window=window(), area=area, region=region):
        bpy.ops.angelo.retopology('INVOKE_DEFAULT')


def row_without_mesh():
    deselect_all()
    row()


def row_with_scan():
    scan = bpy.data.objects[SOURCE]
    deselect_all()
    scan.select_set(True)
    bpy.context.view_layer.objects.active = scan
    send('MOUSEMOVE')
    row()


def show_result_only():
    """The scan hidden, so the screenshot shows the result's own quads."""
    bpy.data.objects[SOURCE].hide_set(True)


def show_both():
    bpy.data.objects[SOURCE].hide_set(False)


def keyboard_to_the_view():
    """Escape, with the pointer over the view: the chat's composer has the keyboard from start-up and would take
    Ctrl+Z as text editing; Escape leaves it without touching the scene (a click on the view would select)."""
    send('MOUSEMOVE')
    tap('ESC')


def undo():
    keyboard_to_the_view()
    tap('Z', ctrl=True)


def redo():
    keyboard_to_the_view()
    tap('Z', ctrl=True, shift=True)


def done(after):
    r = after["result"]
    return r is not None and r["facts"] is not None


def result_ok(before, after):
    r = after["result"]
    if not done(after):
        return False
    return (r["quads"] == r["faces"] and abs(r["faces"] / DEFAULT_FACES - 1.0) <= 0.25 and r["selected"] and r["active"]
            and after["source"] == state["source_digest"] and set(after["objects"]) == {SOURCE, RESULT})


# (name, run, test(before, after), screenshot or None, seconds to wait). Order matters: each starts from the last.
STEPS = (
    ("row_without_a_mesh_changes_nothing", row_without_mesh,
     lambda b, a: a["objects"] == [SOURCE] and a["source"] == state["source_digest"], "retopology-no-mesh.png", 1.5),
    ("row_opens_the_options", row_with_scan, lambda b, a: a["objects"] == [SOURCE], "retopology-options.png", 1.5),
    ("return_retopologizes", lambda: tap('RET'), result_ok, None, 120.0),
    ("the_result_alone", show_result_only, lambda b, a: done(a), "retopology-result.png", 1.0),
    ("both_shown", show_both, lambda b, a: done(a), "retopology-result-and-scan.png", 1.0),
    ("undo_removes_the_result", undo,
     lambda b, a: a["objects"] == [SOURCE] and a["source"] == state["source_digest"], "retopology-undone.png", 5.0),
    ("redo_brings_it_back", redo,
     lambda b, a: RESULT in a["objects"] and a["source"] == state["source_digest"], "retopology-redone.png", 5.0),
)


def save():
    REPORT.write_text(json.dumps(state, indent=2, default=str))


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"][name] = hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        shots, checks = state["screenshots"], state["checks"]
        checks["the_options_show"] = shots.get("retopology-options.png") != shots.get("retopology-no-mesh.png")
        checks["the_result_looks_different"] = shots.get("retopology-start.png") != shots.get("retopology-result.png")
        checks["undo_looks_like_the_start"] = (shots.get("retopology-undone.png")
                                               != shots.get("retopology-result-and-scan.png"))
        state["phase"] = "complete"
        state["success"] = all(checks.values()) and all(s["passed"] is not False for s in state["steps"].values())
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    save()
    print("ANGELO_RETOPOLOGY_DIRECTOR", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    try:
        assert time.monotonic() - started < 420, "Retopology Director fixture timed out in " + state["phase"]
        from angelo_agent import chat_destinations, director, ui
        if state["phase"] == "boot":
            if not director.is_director(window()) or not qa_boot.started():
                return .2
            __import__("angelo_agent.grant_guard").grant_guard.stand_in().quick_connect(mode="claude_subscription")
            state["phase"] = "connect"
            return .2
        if state["phase"] == "connect":
            if not ui.connection_available():
                return .2
            send('MOUSEMOVE')
            state["phase"], state["settled"] = "settle", time.monotonic()
            return .2
        if state["phase"] == "settle":
            if time.monotonic() - state["settled"] < 1.5:
                return .2
            checks = state["checks"]
            checks["director_is_default"] = director.preferred_mode() == "DIRECTOR"
            rows = chat_destinations.menu_rows(open_groups={chat_destinations.MODEL_GROUP})
            member = next((r for r in rows if r.get("feature") == "retopology" and r.get("depth")), None)
            checks["model_group_lists_retopology"] = (member is not None and member["operator"] == "angelo.retopology"
                                                      and chat_destinations.PARENTS.get("retopology") == "model")
            checks["model_is_a_top_level_tile"] = any(r.get("value") == "model" and not r.get("depth")
                                                      for r in chat_destinations.menu_rows())
            scene_fixture()
            capture("retopology-start.png")
            state["phase"], state["index"], state["pending"] = "step", 0, None
            save()
            return .3
        if state["phase"] == "step":
            if state["pending"]:
                name, test, shot, before, sent, wait = state["pending"]
                after = snapshot()
                passed = None if test is None else bool(test(before, after))
                if passed is False and time.monotonic() - sent < wait:
                    return .1  # A dialog, a background job or an Undo step take a few event-loop passes.
                state["steps"][name] = {"passed": passed, "before": before, "after": after,
                                        "seconds": round(time.monotonic() - sent, 2)}
                state["pending"] = None
                state["index"] += 1
                if shot:
                    capture(shot)
                save()
            if state["index"] >= len(STEPS):
                finish()
                return None
            name, run, test, shot, wait = STEPS[state["index"]]
            before = snapshot()
            run()
            state["pending"] = (name, test, shot, before, time.monotonic(), wait)
            return .3
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.5)
