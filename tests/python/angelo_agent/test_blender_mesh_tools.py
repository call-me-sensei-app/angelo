# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""mesh_subdivide and mesh_delete through real staged programs, with topology steps and Undo.

Run only in a disposable instance with the exact Angelo binary:
``--background --factory-startup --disable-autoexec --python-exit-code 1``.
No provider or paid call.
"""

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-mesh-tools-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import no_stages, commit_operator  # noqa: E402

report = {"checks": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path, "version": bpy.app.version_string}
window = bpy.context.window_manager.windows[0]


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def settle(job):
    for _ in range(200):
        result = job.poll()
        if result.get("final") or result.get("ready_to_commit"):
            return result
    raise AssertionError("cooperative native program did not settle")


def run(operations, checks=()):
    cube = window.scene.objects["Cube"]
    handle = queries.object_handle(cube)
    arguments = validate_call("angelo_scene_program", {
        "mode": "APPLY", "read_set": [], "write_set": [{"handle": handle, "revision": queries.object_revision(window, cube)}],
        "operations": [dict(operation, target=handle) for operation in operations], "expected_checks": list(checks)})
    job = native.begin(window, "angelo_scene_program", arguments)
    staged = settle(job)
    if not staged.get("ready_to_commit"):
        report.setdefault("results", []).append(staged.get("error"))
        return False
    result = bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    report.setdefault("results", []).append(job.result.get("error"))
    queries.invalidate()
    return "FINISHED" in result and job.result.get("status") == "committed"


def counts():
    mesh = window.scene.objects["Cube"].data
    return len(mesh.vertices), len(mesh.polygons)


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    queries.invalidate()
    bpy.ops.ed.undo_push(message="mesh tools QA baseline")
    queries.invalidate()
    handle = queries.object_handle(window.scene.objects["Cube"])
    top = {"normal": [0, 0, 1], "normal_angle": .1}
    subdivided = run([{"kind": "mesh_subdivide", "parameters": {"face_indices": list(range(6)), "cuts": 1}},
                      {"kind": "mesh_delete", "parameters": {"face_selector": top}}],
                     checks=[{"kind": "mesh_counts", "target": handle, "vertices": {"minimum": 25, "maximum": 25},
                              "edges": {"minimum": 44, "maximum": 44}, "faces": {"minimum": 20, "maximum": 20}}])
    # 1 cut: 26 vertices and 24 faces; deleting the four top faces (selector
    # recomputed on the subdivided mesh) also removes their only-used centre vertex.
    check("subdivide_then_delete_commits", subdivided and counts() == (25, 20))
    stale = run([{"kind": "mesh_delete", "parameters": {"face_indices": [0], "index_scope": "CURRENT", "topology_step": 3}}])
    check("stale_topology_step_refused", not stale and counts() == (25, 20) and "Stale topology" in (report["results"][-1] or ""))
    bpy.ops.ed.undo()
    queries.invalidate()
    check("one_undo_restores_cube", counts() == (8, 6) and no_stages())
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "mesh-tools.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_MESH_TOOLS", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
