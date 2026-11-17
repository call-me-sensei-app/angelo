# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's side of test_blender_godot_link_busy.py, run inside ``Angelo --background --python``
(doc/angelo/godot_link.rst, Validation).

Turns the Godot link on, accepts every request the Godot driver makes (the first asset, and the one it switches to), and
saves when the driver asks: the driver writes ``cmd-<n>.json`` (``{"save": n}``) into ``ANGELO_QA_DIR`` and this side,
taking them in order, replaces the scene's marker object with a cube named ``Save<n>``, saves (the link exports and
tells Godot "saved"), and answers ``ack-<n>.json``. Several commands written at once are saved back to back (a burst).
``cmd-done.json`` ends the run. A background session has no event loop, so this script pumps the link's timer itself.
Writes ``angelo.json``: the saves, every acknowledgement Godot sent (``reimported`` with its save number), and checks:
the asset linked at the end has its newest save acknowledged, and every acknowledgement names a save made here.
"""

import json
import os
import sys
import time
import traceback
from pathlib import Path

import addon_utils
import bpy

OUT = Path(os.environ["ANGELO_QA_DIR"])
LIMIT = float(os.environ.get("ANGELO_E2E_SECONDS", "300"))
state = {"phase": "start", "steps": [], "checks": {}, "saves": [], "assets": []}
started = time.monotonic()


def step(phase):
    state["steps"].append({"phase": phase, "at": round(time.monotonic() - started, 2)})
    state["phase"] = phase
    write()


def write():
    (OUT / "angelo.json").write_text(json.dumps(state, indent=2), encoding="utf-8")


def save(number):
    """Replace the marker with a cube named Save<number>, save, and say so."""
    for obj in [obj for obj in bpy.context.scene.objects if obj.name.startswith("Save")]:
        bpy.data.objects.remove(obj)
    bpy.ops.mesh.primitive_cube_add(size=0.4, location=(0.0, 0.0, 2.0 + 0.1 * number))
    bpy.context.object.name = "Save%d" % number
    bpy.context.view_layer.update()
    bpy.ops.wm.save_mainfile()
    entry = {"save": number, "asset": state["assets"][-1] if state["assets"] else "",
             "at": round(time.monotonic() - started, 3), "file": bpy.data.filepath}
    state["saves"].append(entry)
    (OUT / ("ack-%d.json" % number)).write_text(json.dumps(entry), encoding="utf-8")


try:
    if not addon_utils.check("angelo_agent")[1]:
        addon_utils.enable("angelo_agent", default_set=False, persistent=False)
    from angelo_agent import engine_link
    engine_link.register()
    engine_link.start()
    state["checks"]["record_written"] = os.path.isfile(os.environ["ANGELO_LINK_RECORD"])
    step("listening")
    done, following = set(), 1
    while time.monotonic() - started < LIMIT:
        engine_link._tick()
        if engine_link.pending() is not None:   # The first asset, or the one the driver switches to.
            request = engine_link.pending()
            state["assets"].append(request["asset"].ident)
            engine_link.accept(None)
            step("accepted %s" % request["asset"].ident)
        if (OUT / "cmd-done.json").exists():
            step("done")
            break
        command = OUT / ("cmd-%d.json" % following)
        if command.exists() and engine_link.session() is not None:
            save(json.loads(command.read_text(encoding="utf-8"))["save"])
            following += 1
            continue   # The next command at once: commands written together are a burst.
        time.sleep(0.02)
    for _ in range(50):   # Godot's last acknowledgements.
        engine_link._tick()
        time.sleep(0.02)
    state["reports"] = [{key: item.get(key) for key in ("code", "asset", "save", "version")}
                        for item in engine_link._state["reports"]]
    reimported = {(item.get("asset"), item.get("save")) for item in engine_link._state["reports"]
                  if item.get("code") == "reimported"}
    # Godot acknowledges a re-import for the asset linked at the time (plugin._on_reimported): the asset linked at the
    # end has its newest save acknowledged (earlier saves of a burst may fold into it). An asset the link switched away
    # from is not acknowledged once its session ended (the 2026-11-15 run: crate.glb's save 5, re-imported after the
    # switch, shows in Godot with no acknowledgement, as designed); the driver checks what Godot shows. Every
    # acknowledgement names a save this side made, of that asset.
    newest = {}
    for entry in state["saves"]:
        newest[entry["asset"]] = entry["save"]
    state["newest_saves"] = newest
    final = state["assets"][-1] if state["assets"] else ""
    made = {(entry["asset"], entry["save"]) for entry in state["saves"]}
    state["checks"]["final_asset_newest_save_acknowledged"] = final in newest and (final, newest[final]) in reimported
    state["checks"]["acknowledgements_name_real_saves"] = bool(reimported) and reimported <= made
    state["error"] = engine_link._state["error"]
    engine_link.stop()
    state["checks"]["record_removed"] = not os.path.exists(os.environ["ANGELO_LINK_RECORD"])
except Exception:
    state["traceback"] = traceback.format_exc()
finally:
    write()
sys.exit(0 if state["phase"] == "done" and all(state["checks"].values()) and "traceback" not in state else 1)
