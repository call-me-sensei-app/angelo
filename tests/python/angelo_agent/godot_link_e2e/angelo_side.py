# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's side of test_blender_godot_link_e2e.py, run inside ``Angelo --background --python``
(doc/angelo/godot_link.rst, Validation).

Turns the Godot link on, then answers Godot's request as the person's Open would (``engine_link.accept``), edits the
linked scene once (a cube named AngeloAdded), saves, and waits for the Godot driver to report the re-import. A
background session has no event loop, so this script pumps the link's timer itself. Writes ``angelo.json`` to
``ANGELO_QA_DIR``. With ``ANGELO_E2E_DURABLE=1`` (an editor that shows no live scenes, such as Unreal's Python
plugin) it edits once the asset is open instead of after the first live scene, and after the save waits for the
editor to close the link instead of for a "reimported" report. With ``ANGELO_E2E_WAIT_PREVIEW=1`` (Unreal's live
layer) it saves only once the editor has reported showing the edit live (status ``previewed``), so the editor's
driver can check the live edit arrived before any save.
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
LIMIT = float(os.environ.get("ANGELO_E2E_SECONDS", "150"))
DURABLE = os.environ.get("ANGELO_E2E_DURABLE") == "1"
WAIT_PREVIEW = os.environ.get("ANGELO_E2E_WAIT_PREVIEW") == "1"
state = {"phase": "start", "steps": [], "checks": {}, "sent": []}


def step(phase):
    state["steps"].append({"phase": phase, "at": round(time.monotonic() - started, 2)})
    state["phase"] = phase
    write()   # A run stopped from outside still says how far it got.


def write():
    (OUT / "angelo.json").write_text(json.dumps(state, indent=2), encoding="utf-8")


def export_facts():
    """What the save exports, for the engine side to check the re-imported asset against: every mesh object's
    world-space bounds (Blender metres), its triangles and the objects' names. Also in ``export.txt``, plain text for
    an engine's own test (C#): the six bounds, the triangle count, the names."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    points, triangles, names = [], 0, []
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        mesh.calc_loop_triangles()
        triangles += len(mesh.loop_triangles)
        points += [evaluated.matrix_world @ vertex.co for vertex in mesh.vertices]
        evaluated.to_mesh_clear()
        names.append(obj.name)
    low = [round(min(point[i] for point in points), 5) for i in range(3)]
    high = [round(max(point[i] for point in points), 5) for i in range(3)]
    (OUT / "export.txt").write_text(" ".join(str(value) for value in low + high) + "\n%d\n%s\n"
                                    % (triangles, ",".join(sorted(names))), encoding="utf-8")
    return {"min": low, "max": high, "triangles": triangles, "objects": sorted(names)}


started = time.monotonic()
try:
    if not addon_utils.check("angelo_agent")[1]:
        addon_utils.enable("angelo_agent", default_set=False, persistent=False)
    from angelo_agent import engine_link
    engine_link.register()
    engine_link.start()
    state["checks"]["record_written"] = os.path.isfile(os.environ["ANGELO_LINK_RECORD"])
    step("listening")
    write()
    marker = None
    noted = 0.0
    while time.monotonic() - started < LIMIT:
        engine_link._tick()
        if time.monotonic() - noted > 2.0:
            noted = time.monotonic()
            state["now"] = {"session": bool(engine_link.session()), "pending": engine_link.pending() is not None,
                            "clients": len(engine_link.clients()), "error": engine_link._state["error"],
                            "reports": [item.get("code") for item in engine_link._state["reports"]][-5:]}
            write()
        phase = state["phase"]
        if phase == "listening" and engine_link.pending() is not None:
            request = engine_link.pending()
            state["request"] = {"asset": request["asset"].ident, "node": request["node"],
                                "project_name": request["project_name"]}
            engine_link.accept(None)
            state["checks"]["opened_editable"] = bpy.data.filepath == request["asset"].editable
            step("accepted")
        elif phase == "accepted" and DURABLE and time.monotonic() - started > state["steps"][-1]["at"] + 1.0:
            bpy.ops.mesh.primitive_cube_add(size=0.5, location=(0.0, 0.0, 2.0))
            bpy.context.object.name = "AngeloAdded"
            bpy.context.view_layer.update()
            state["export"] = export_facts()
            bpy.ops.wm.save_mainfile()
            state["checks"]["no_live_scene_sent"] = engine_link._state["version"] == 0
            step("saved")
        elif phase == "saved" and DURABLE and engine_link.session() is None:
            state["checks"]["editor_closed_link"] = True
            step("closed")
            break
        elif phase == "accepted" and not DURABLE and engine_link._state["version"] >= 1:
            bpy.ops.mesh.primitive_cube_add(size=0.5, location=(0.0, 0.0, 2.0))
            bpy.context.object.name = "AngeloAdded"
            bpy.context.view_layer.update()
            marker = engine_link._state["version"]
            step("edited")
        elif phase == "edited" and engine_link._state["version"] > marker and (not WAIT_PREVIEW or any(
                item.get("code") == "previewed" and int(item.get("version") or 0) > marker
                and int(item.get("instances") or 0) >= 1 for item in engine_link._state["reports"])):
            if WAIT_PREVIEW:
                state["checks"]["editor_previewed_before_save"] = True
            state["export"] = export_facts()
            bpy.ops.wm.save_mainfile()
            step("saved")
        elif phase == "saved" and not DURABLE and any(item.get("code") == "reimported"
                                                      for item in engine_link._state["reports"]):
            state["checks"]["godot_reported_reimport"] = True
            step("reimported")
        elif phase == "reimported" and engine_link.session() is None:
            state["checks"]["godot_closed_link"] = True
            step("closed")
            break
        time.sleep(0.03)
    state["versions_sent"] = engine_link._state["version"]
    state["last_export_ms"] = round(engine_link._state["last_ms"], 1)
    state["error"] = engine_link._state["error"]
    engine_link.stop()
    state["checks"]["record_removed"] = not os.path.exists(os.environ["ANGELO_LINK_RECORD"])
except Exception:
    state["traceback"] = traceback.format_exc()
finally:
    write()
sys.exit(0 if state["phase"] == "closed" and all(state["checks"].values()) and "traceback" not in state else 1)
