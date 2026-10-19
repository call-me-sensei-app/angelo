# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Smart UV Project and Pack Islands through real staged programs, with derived checks and Undo.

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
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-uv-tools-qa"))
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


def apply(kind, **parameters):
    cube = window.scene.objects["Cube"]
    handle = queries.object_handle(cube)
    arguments = validate_call("angelo_scene_program", {
        "mode": "APPLY", "read_set": [], "write_set": [{"handle": handle, "revision": queries.object_revision(window, cube)}],
        "operations": [{"kind": kind, "target": handle, "parameters": parameters}]})
    job = native.begin(window, "angelo_scene_program", arguments)
    settle(job)
    result = bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    report.setdefault("results", []).append({key: job.result.get(key) for key in ("status", "error")})
    queries.invalidate()
    return ("FINISHED" in result and job.result.get("status") == "committed"
            and all(row.get("passed") for row in job.result.get("checks", [])) and bool(job.result.get("checks")))


def uv_bounds(name):
    layer = window.scene.objects["Cube"].data.uv_layers[name]
    values = [value for item in layer.uv for value in item.vector]
    return min(values), max(values), len(values)


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    queries.invalidate()
    bpy.ops.ed.undo_push(message="uv QA baseline")
    queries.invalidate()
    check("smart_project_commits", apply("uv_smart_project", uv_map="QA Smart", island_margin=.02))
    low, high, count = uv_bounds("QA Smart")
    check("smart_project_fills_unit_square", count == 48 and 0 <= low and high <= 1 and high - low > .3)
    check("pack_commits", apply("uv_pack", uv_map="QA Smart", margin=.05, rotate=False))
    low, high, _ = uv_bounds("QA Smart")
    check("pack_stays_in_unit_square", 0 <= low and high <= 1)
    check("pack_needs_an_existing_map", not apply("uv_pack", uv_map="Missing"))
    bpy.ops.ed.undo()
    bpy.ops.ed.undo()
    queries.invalidate()
    check("uv_two_undos", "QA Smart" not in window.scene.objects["Cube"].data.uv_layers and no_stages())
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "uv-tools.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_UV_TOOLS", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
