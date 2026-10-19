# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A motion read in the interface leaves every unkeyed edit standing after Blender's notifiers have run.

Run by ``run_native_suites.py --gui --include test_blender_motion_interface`` (a real window). Setting
``frame_current`` queues a frame-change notifier whose interface update runs after
the read and replaces every unkeyed edit of an animated channel in the scene, the untouched objects' too; the
background suites cannot see it. Two keyed empties get an unkeyed edit; then a read after an edit
(``motion_facts.after_edit``) and a read the model asks for (``angelo_scene_object`` with ``frames``) run, and
after the event loop has run the notifiers both edits must still stand, the frame and selection be unchanged, and
no message-bus subscriber to the frame be notified. A control step sets ``frame_current`` itself and shows the
same check catches the replacement. No provider, account or network.
"""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and not bpy.app.background
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-motion-interface-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import motion_facts, scene_queries as queries  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402

state = {"success": False, "checks": {}, "observed": {}, "phase": "boot", "started": time.monotonic()}
notified = []
OWNER = object()
WAIT = 1.5  # Seconds of event loop for the notifiers to run.


def write():
    (OUT / "motion-interface.json").write_text(json.dumps(state, indent=2, default=str))


def window():
    return bpy.context.window_manager.windows[0]


def xs():
    return [round(bpy.data.objects[name].location.x, 4) for name in ("QA Rig", "QA Other")]


def setup():
    win = window()
    scene = win.scene
    scene.frame_start, scene.frame_end = 1, 120
    scene.frame_set(30)
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for name in ("QA Rig", "QA Other"):
        obj = bpy.data.objects.new(name, None)
        scene.collection.objects.link(obj)
        for frame, value in ((1, (-10, 0, 0)), (120, (10, 0, 0))):
            obj.location = value
            obj.keyframe_insert("location", frame=frame)
    mesh = bpy.data.meshes.new("QA Body")
    mesh.from_pydata([(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (0, 1)], [], [(0, 1, 3, 2)])
    body = bpy.data.objects.new("QA Body", mesh)
    scene.collection.objects.link(body)
    body.parent = bpy.data.objects["QA Rig"]
    win.view_layer.update()
    bpy.msgbus.subscribe_rna(key=(bpy.types.Scene, "frame_current"), owner=OWNER, args=(),
                             notify=lambda *_args: notified.append(time.monotonic()))
    state["observed"]["keyed_x_at_30"] = xs()


def edit_and_read():
    win = window()
    rig = bpy.data.objects["QA Rig"]
    for name in ("QA Rig", "QA Other"):
        bpy.data.objects[name].location.x = -3.0  # Unkeyed edits of keyed channels.
    win.view_layer.update()
    body = bpy.data.objects["QA Body"]
    body.select_set(True)
    win.view_layer.objects.active = body
    state["before"] = {"frame": win.scene.frame_current, "active": body.name,
                       "selected": sorted(o.name for o in bpy.context.selected_objects)}
    after = motion_facts.after_edit(win, objects=[rig]) or {}
    asked = BlenderExecutor(win).execute("angelo_scene_object", {
        "object": queries.object_handle(rig), "sections": ["TRANSFORM"], "frames": [1, 120]})
    state["observed"].update(after_edit=after, at_frames=asked.get("at_frames"), x_right_after=xs())


def verify():
    win = window()
    state["observed"]["x_after_notifiers"] = xs()
    checks = state["checks"]
    checks["reads_ran"] = bool(state["observed"]["after_edit"].get("moving")) and (
        (state["observed"]["at_frames"] or {}).get("origin") == [[-10.0, 0.0, 0.0], [10.0, 0.0, 0.0]])
    checks["unkeyed_edits_outlive_the_notifiers"] = xs() == [-3.0, -3.0]
    checks["frame_and_selection_unchanged"] = state["before"] == {
        "frame": win.scene.frame_current, "active": win.view_layer.objects.active.name,
        "selected": sorted(o.name for o in bpy.context.selected_objects)}
    checks["no_frame_subscriber_notified"] = not notified


def control():
    """Blender's own frame property: the same check sees its notifier replace the edit."""
    win = window()
    bpy.data.objects["QA Other"].location.x = -3.0
    win.scene.frame_current = win.scene.frame_current


def control_seen():
    state["observed"]["control_x"] = xs()[1]
    state["checks"]["the_check_catches_a_frame_notifier"] = xs()[1] != -3.0 and bool(notified)


STEPS = [("setup", setup), ("read", edit_and_read), ("verify", verify), ("control", control),
         ("control_seen", control_seen)]


def tick():
    try:
        name, step = STEPS.pop(0)
        state["phase"] = name
        step()
        if STEPS:
            return WAIT
        bpy.msgbus.clear_by_owner(OWNER)
        state["success"] = bool(state["checks"]) and all(state["checks"].values())
        state["phase"] = "complete"
        write()
        print("ANGELO_MOTION_INTERFACE", json.dumps(state, default=str), flush=True)
    except Exception as error:  # noqa: BLE001  (recorded as the suite's failure)
        state.update(success=False, phase="failed", error=str(error), traceback=traceback.format_exc())
        write()
        print(state["traceback"], flush=True)
    if os.environ.get("ANGELO_QA_KEEP_OPEN") != "1":
        bpy.ops.wm.quit_blender()
    return None


bpy.context.preferences.view.show_splash = False
bpy.app.timers.register(tick, first_interval=1.5)
