# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Sculpt tools name their object by scene handle as well as by name, on every path; a refusal before an edit
holds nothing. Native isolated GUI only; no model, account or network.

Regression: a model named the head ``o4`` (a short handle the
identifier table expands to the long scene handle). ``angelo_sculpt_target`` accepted it, because
``BlenderExecutor.execute`` turns a handle into its object's name, but the approval binding of every sculpt edit
(``sculpt.precondition``) looked the handle up as a name, refused with "Choose an existing polygon mesh", and
that refusal, raised outside an edit's own preflight, read as a failure that may have changed the scene: the
request's further edits were held and the one-shot ended with the head unsculpted.

Checked here, through ``BlenderExecutor`` as the controller calls it: inspection, preparation, a brush pass and a
brush program by long handle (binding and edit), the mesh changed only by the edits; and refusals before an edit
(an unknown name, a stale handle, a stale mesh_revision) say nothing ran (``partial_changes_possible`` False).
Inspect results.json; a zero exit status alone is not proof.
"""

import json
import os
from pathlib import Path
import sys
import tempfile
import traceback

import bpy
import numpy as np

assert "--factory-startup" in sys.argv, "Use an isolated factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-sculpt-handles"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"), ANGELO_STATE_DIR=str(OUT / "state"),
                  ANGELO_DISABLE_DEFAULT_CHAT="1")
sys.path.insert(0, str(ROOT / "scripts/addons_core"))

from angelo_agent import scene_queries, sculpt  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.references import ReferenceStore  # noqa: E402

report = {"checks": {}, "measurements": {}}


def check(name, condition):
    report["checks"][name] = bool(condition)


def refusal(call):
    """``(message, partial_changes_possible)`` of a call that must be refused before any edit (None if it ran)."""
    try:
        call()
    except Exception as error:  # noqa: BLE001 - the classification is what is checked.
        return str(error), getattr(error, "partial_changes_possible", True)
    return None


def run():
    window = bpy.context.window_manager.windows[0]
    executor = BlenderExecutor(window)
    executor.observation_store = ReferenceStore(OUT, "2c6f0b1a-1d3e-4f5a-8b7c-9d0e1f2a3b4c")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32)
    head = bpy.context.active_object
    head.name = "Head"
    handle = scene_queries.object_handle(head)
    check("the_handle_is_not_a_name", handle != head.name and handle not in window.scene.objects)

    state = executor.execute("angelo_inspect_sculpt", {"object": handle, "probes": []})
    check("inspection_by_handle", state["mesh_revision"] and not state["edit_blockers"])
    prepare = {"object": handle, "mesh_revision": state["mesh_revision"], "add_levels": 0}
    check("preparation_binds_by_handle", isinstance(executor.precondition("angelo_prepare_sculpt", prepare), tuple))
    revision = executor.execute("angelo_prepare_sculpt", prepare)["verified"]["mesh_revision"]

    before = sculpt.snapshot(window, head)["world"].copy()
    common = {"brush": "GRAB", "strength": 1.0, "direction": "ADD", "symmetry": "X", "normal": [0, -1, 0],
              "front_faces_only": False, "spacing": 0.1, "falloff": "SMOOTH"}
    program = {"object": handle, "mesh_revision": revision, "steps": [
        dict(common, radius=0.3, paths=[[{"position": [0.0, -1.0, 0.0], "pressure": 1.0},
                                         {"position": [0.0, -1.15, 0.05], "pressure": 1.0}]])]}
    binding = executor.precondition("angelo_sculpt_program", program)   # The step that refused before the fix.
    check("a_program_binds_by_handle", isinstance(binding, tuple) and binding[0] == head.session_uid)
    result = executor.execute("angelo_sculpt_program", program)
    after = sculpt.snapshot(window, head)["world"]
    moved = float(np.abs(after - before).max())
    report["measurements"]["program_moved_max"] = moved
    check("a_program_by_handle_sculpts", result.get("success") and moved > 1e-3)

    revision = sculpt.snapshot(window, head)["revision"]
    batch = {"object": handle, "mesh_revision": revision, "brush": "DRAW", "radius": 0.3, "strength": 0.5,
             "direction": "ADD", "symmetry": "NONE", "normal": [1, 0, 0], "front_faces_only": True, "spacing": 0.2,
             "paths": [[{"position": [1.0, 0.0, 0.0], "pressure": 1.0}]]}
    check("a_pass_binds_by_handle", isinstance(executor.precondition("angelo_sculpt_batch", batch), tuple))
    check("a_pass_by_handle_sculpts", executor.execute("angelo_sculpt_batch", batch).get("success"))
    check("names_still_work", isinstance(executor.precondition(
        "angelo_sculpt_batch", dict(batch, object="Head", mesh_revision=sculpt.snapshot(window, head)["revision"])),
        tuple))

    # An object named like another's handle is still sculpted by its own handle (the
    # executor once turned a handle into its name, which then resolved as the other object's handle).
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.mesh.primitive_cube_add(size=0.2, location=(3.0, 0.0, 0.0))
    decoy = bpy.context.active_object
    decoy.name = handle
    decoy_state = executor.execute("angelo_inspect_sculpt", {"object": scene_queries.object_handle(decoy), "probes": []})
    report["measurements"]["decoy_inspected"] = [decoy_state.get("object"), decoy_state.get("vertices")]
    check("an_object_named_like_a_handle_is_itself", decoy_state.get("object") == decoy.name
          and decoy_state.get("vertices") == len(decoy.data.vertices))
    bpy.data.objects.remove(decoy)
    bpy.context.view_layer.objects.active = head

    found = {
        "unknown_name": refusal(lambda: executor.precondition("angelo_sculpt_batch", dict(batch, object="Nobody"))),
        "stale_revision": refusal(lambda: executor.precondition("angelo_sculpt_batch",
                                                                dict(batch, mesh_revision="0" * 64))),
    }
    bpy.data.objects.remove(head)
    window.view_layer.update()
    found["deleted_handle"] = refusal(lambda: executor.precondition("angelo_sculpt_batch", batch))
    report["measurements"]["refusals"] = found
    for name, outcome in found.items():
        check(name + "_is_refused_saying_nothing_ran", outcome is not None and outcome[1] is False)


def main():
    try:
        with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
            run()
        report["success"] = all(report["checks"].values())
    except Exception:
        report.update(success=False, error=traceback.format_exc())
    (OUT / "results.json").write_text(json.dumps(report, indent=2, default=str))
    print("ANGELO_SCULPT_HANDLES", json.dumps({"success": report["success"], "failed": sorted(
        name for name, passed in report["checks"].items() if not passed)}), flush=True)
    bpy.ops.wm.quit_blender()


bpy.app.timers.register(lambda: main() or None, first_interval=1)
