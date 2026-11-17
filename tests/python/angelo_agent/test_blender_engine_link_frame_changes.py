# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The engine link schedules a live export for every change an export can see, whichever way Blender evaluates it
(``scene_changes``; doc/angelo/engine_link.rst, "Live scenes").

Found by the Godot showcase (2026-11-15): a script that ended in ``scene.frame_set()`` was never sent. A frame
change is evaluated by ``BKE_scene_graph_update_for_newframe_ex``, which flushes every edit tagged before it and runs
only ``frame_change_post``, never ``depsgraph_update_post``, the one handler the link listened to. And an edit Blender
does not tag at all (a custom property set from Python, which Godot's extras read) reaches no handler. Run with the
link's own handlers (``engine_link._HANDLERS``) on a linked Godot asset, background, ``--factory-startup``:

* edits followed by a ``frame_set`` of the same frame schedule an export;
* a frame change of an animated scene schedules one;
* an untagged edit (a custom property) schedules one once Angelo's executor says a scene edit ran
  (``scene_changes.edited``); the same edit with nothing said schedules none (the class this closes);
* a selection still schedules nothing.

Writes ``results.json`` in ``ANGELO_QA_DIR``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-engine-link-frame-qa"))
OUT.mkdir(parents=True, exist_ok=True)

import addon_utils  # noqa: E402

addon_utils.enable("angelo_agent", default_set=False, persistent=False)
from angelo_agent import engine_link, scene_changes  # noqa: E402

checks = {}
report = {"success": False, "checks": checks}
scene = bpy.context.scene
layer = bpy.context.view_layer
cube = bpy.data.objects["Cube"]


def check(name, value):
    checks[name] = bool(value)
    print(("PASS " if value else "FAIL ") + name, flush=True)


def settle():
    """Evaluated, with nothing waiting: the next change is the one checked."""
    layer.update()
    engine_link._state.update(dirty_at=None, scene=engine_link.scene_key(scene, layer))


def scheduled():
    return engine_link._state["dirty_at"] is not None


def install():
    """The link's handlers as engine_link.start installs them (its ``_listen``; before it, ``_HANDLERS``)."""
    if hasattr(engine_link, "_listen"):
        engine_link._listen()
        return
    for handlers, function in engine_link._HANDLERS:
        if function not in handlers:
            handlers.append(function)


def uninstall():
    if hasattr(engine_link, "_unlisten"):
        engine_link._unlisten()
        return
    for handlers, function in engine_link._HANDLERS:
        if function in handlers:
            handlers.remove(function)


try:
    cube.keyframe_insert("rotation_euler", frame=1)
    cube.rotation_euler.z += 1.0
    cube.keyframe_insert("rotation_euler", frame=20)
    scene.frame_set(1)
    engine_link._state.update(session={"asset": type("Asset", (), {"engine": "godot"})(), "params": {}},
                              exporting=False, dirty_at=None)
    install()

    settle()
    cube.location.x += 0.5          # A script's edits...
    cube.data.vertices[0].co.z += 0.1
    cube.data.update()
    scene.frame_set(scene.frame_current)   # ...that end in a frame_set of the same frame.
    check("edits_ending_in_a_frame_set_schedule_an_export", scheduled())

    settle()
    scene.frame_set(10)             # The animated cube turns: the export at the current frame differs.
    check("a_frame_change_schedules_an_export", scheduled())

    settle()
    cube["godot_extra"] = 1         # No tag at all: no depsgraph update, no frame change.
    layer.update()
    check("an_untagged_edit_alone_reaches_no_handler", not scheduled())
    edited = getattr(scene_changes, "edited", None)
    if edited is not None:
        edited(scene)               # What Angelo's executor says after a scene-editing tool or script.
    check("an_untagged_edit_schedules_an_export_once_the_executor_says_so", edited is not None and scheduled())

    settle()
    cube.select_set(not cube.select_get())
    layer.update()
    check("a_selection_schedules_nothing", not scheduled())
    report["success"] = all(checks.values())
except Exception:
    report["error"] = traceback.format_exc()
    traceback.print_exc()
finally:
    uninstall()
    engine_link._state.update(session=None, dirty_at=None, scene=None)
    (OUT / "results.json").write_text(json.dumps(report, indent=2))
print("ENGINE_LINK_FRAME_CHANGES", json.dumps(checks))
assert report["success"], sorted(name for name, passed in checks.items() if not passed)
