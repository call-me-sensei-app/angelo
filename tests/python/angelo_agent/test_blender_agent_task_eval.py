# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""26 real staged native programs graded by host checks, never model assertions."""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and "--background" in sys.argv, "Use an isolated headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-agent-task-eval"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ["ANGELO_DISABLE_DEFAULT_CHAT"] = "1"
os.environ["ANGELO_CONNECTIONS_DIR"] = str(OUT / "accounts")
os.environ["ANGELO_STATE_DIR"] = str(OUT / "state")

from angelo_agent.native_programs import begin, cleanup_all
from angelo_agent.action_schemas import CREATE_KINDS
from angelo_agent.scene_validation import VerificationContext, verify
from eval_agent_tasks import TASK_FIXTURES


def ids():
    return [block for collection in (bpy.data.objects, bpy.data.meshes, bpy.data.materials, bpy.data.node_groups,
                                     bpy.data.scenes, bpy.data.collections, bpy.data.actions, bpy.data.armatures,
                                     bpy.data.cameras, bpy.data.lights)
            for block in collection]


window = bpy.context.window
source_scene = window.scene
source_members = [(o.name, o.as_pointer()) for o in source_scene.objects]
report = {"tasks": [], "paid_requests": 0, "binary": bpy.app.binary_path,
          "version": bpy.app.version_string, "build_features": {"opensubdiv": bool(bpy.app.build_options.opensubdiv)},
          "grading": "real_host_checks_on_staged_and_committed_outputs", "artistic_quality_claimed": False}

for fixture in TASK_FIXTURES:
    started = time.perf_counter()
    initial_ids = {block.as_pointer() for block in ids()}
    scratch = bpy.data.scenes.new("QA Task " + fixture["id"])
    window.scene = scratch
    job = None
    entry = {"id": fixture["id"], "description": fixture["description"], "categories": fixture["categories"],
             "expected_check_status": fixture["expected_check_status"], "operation_count": len(fixture["program"]["operations"])}
    try:
        job = begin(window, "angelo_scene_program", fixture["program"])
        for tick in range(512):
            result = job.poll()
            if job.closed or job.ready_to_commit:
                break
        else:
            raise AssertionError("Bounded task did not reach a final/safe commit boundary")
        assert len(scratch.objects) == 0, "Staging/validation mutated the live scratch scene before commit"
        entry["staged_scene_untouched"] = True
        entry["host_check_results"] = result.get("checks", job.check_results)
        if fixture["expected_check_status"] == "pass":
            assert job.ready_to_commit, result
            result = job.commit()
            assert result["success"] and result["status"] == "committed", result
            # Ready-to-commit work may be privately suspended outside Blender
            # Main. Resolve NEW fixture creations only after their real commit,
            # from the exact requested names in this initially empty scene.
            aliases = {"$" + operation["parameters"]["alias"].lstrip("$"): scratch.objects.get(operation["parameters"]["name"])
                       for operation in job.program["operations"] if operation["kind"] in CREATE_KINDS
                       and operation["parameters"].get("alias")}
            assert all(obj is not None for obj in aliases.values()), "A declared creation is missing from committed output"
            committed = verify(VerificationContext(aliases, list(scratch.objects)), fixture["program"]["expected_checks"])
            entry["committed_checks"] = committed
            assert committed["success"], committed
            entry["committed"] = True
        else:
            assert job.closed and not result["success"] and result["status"] == "failed", result
            assert len(scratch.objects) == 0 and not result.get("partial_changes_possible"), result
            statuses = [check["status"] for outcome in result.get("checks", []) for check in outcome.get("checks", [])]
            assert fixture["expected_check_status"] in statuses, {"statuses": statuses, "result": result}
            entry["committed"] = False
            entry["rejected_without_live_changes"] = True
        entry["graded_success"] = True
        entry["final_status"] = result["status"]
    except Exception:
        entry.update(graded_success=False, error=traceback.format_exc())
    finally:
        if job is not None and not job.closed:
            job.cancel()
        cleanup_all()
        window.scene = source_scene
        # This test owns only IDs allocated after its factory-startup snapshot.
        allocated = [block for block in ids() if block.as_pointer() not in initial_ids]
        bpy.data.batch_remove(ids=allocated)
        entry["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        report["tasks"].append(entry)
        print("ANGELO_TASK", json.dumps({k: entry[k] for k in ("id", "graded_success", "elapsed_ms")}), flush=True)

report["source_scene_unchanged"] = window.scene == source_scene and source_members == [(o.name, o.as_pointer()) for o in source_scene.objects]
report["graded_successes"] = sum(task["graded_success"] for task in report["tasks"])
report["task_count"] = len(report["tasks"])
report["success"] = report["source_scene_unchanged"] and report["graded_successes"] == report["task_count"]
(OUT / "agent-task-eval.json").write_text(json.dumps(report, indent=2, allow_nan=False))
print("ANGELO_TASK_EVAL", json.dumps({k: report[k] for k in ("success", "task_count", "graded_successes", "source_scene_unchanged", "paid_requests")}), flush=True)
assert report["success"], [(task["id"], task.get("error", "")) for task in report["tasks"] if not task["graded_success"]]
