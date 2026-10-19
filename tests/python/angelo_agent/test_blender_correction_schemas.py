# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Rejected parameter feedback, followed by a corrected native action and one Undo.

Disposable --background --factory-startup --disable-autoexec only. No model/provider.
This proves corrected action execution; scripted host tests own the correction-round claim.
"""

import copy
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

assert bpy.app.background and "--factory-startup" in sys.argv, "Use an isolated headless factory startup"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-correction-schema-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries  # noqa: E402
from angelo_agent.action_schemas import ACTION_SCHEMAS  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.plan_schema import model_schema  # noqa: E402
from angelo_agent.strict_schema import inline_references  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import commit_operator, no_stages  # noqa: E402

report = {"checks": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path, "version": bpy.app.version_string}
window = bpy.context.window_manager.windows[0]


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def arguments():
    return {"mode": "APPLY", "read_set": [], "write_set": [], "operations": [
        {"kind": "create_primitive", "target": None,
         "parameters": {"primitive": "CUBE", "name": "Correction Cube", "alias": "cube"}},
        {"kind": "modifier_add", "target": "$cube",
         "parameters": {"type": "SUBSURF", "name": "Subdivision", "settings": {"levels": 2}}},
        {"kind": "batch", "target": None,
         "parameters": {"sources": ["$cube"], "array": [{"step": [3, 0, 0], "count": 2}]}}]}


def main_state():
    ids = {name: sorted(block.as_pointer() for block in getattr(bpy.data, name)) for name in
           ("objects", "meshes", "materials", "node_groups", "scenes", "collections", "worlds", "curves",
            "armatures", "cameras", "lights")}
    return ids, {obj.name: native._fingerprint(obj) for obj in window.scene.objects}


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    queries.invalidate()
    # Seed host handle metadata before the invalid-input baseline; rejection itself must allocate nothing.
    before = main_state()
    with tempfile.TemporaryDirectory(dir=OUT) as directory:
        controller = Controller(directory)
        try:
            controller.set_document(("correction-native-fixture", 1))
            controller._begin_metrics()
            controller.busy = True
            for label, index, field, value in (("batch_count", 2, "count", 1),
                                              ("subsurf_range", 1, "levels", 4),
                                              ("subsurf_integer", 1, "levels", 2.5)):
                bad, rows = arguments(), []
                parameters = bad["operations"][index]["parameters"]
                (parameters["array"][0] if field == "count" else parameters["settings"])[field] = value
                controller.request_tool(label, "angelo_scene_program", bad, rows.append)
                check(label + "_rejected_before_dispatch", len(rows) == 1 and bool(rows[0].get("error")) and
                      controller.tools.empty() and not controller.pending and not controller.native_jobs)
                check(label + "_main_unchanged", main_state() == before and no_stages() and native.WRITER.owner is None)
                schemas = json.loads(json.dumps(rows[0]["exact_schemas"]))
                kind = bad["operations"][index]["kind"]
                selected = next(entry for entry in schemas["ACTIONS"]["actions"] if entry["kind"] == kind)
                exact = inline_references(selected["parameters"])
                source = ACTION_SCHEMAS[kind]
                if kind == "modifier_add":
                    source = next(v for v in source["oneOf"] if v["properties"]["type"].get("const") == "SUBSURF")
                check(label + "_exact_feedback", exact == model_schema(source))
                # Public validation rechecks the corrected values; schema feedback never grants permission.
                corrected = validate_call("angelo_scene_program", copy.deepcopy(arguments()))
                check(label + "_corrected_validates", corrected["operations"][index]["kind"] == kind)
                report.setdefault("rejections", {})[label] = rows[0]
        finally:
            controller.close()
    original_names = set(window.scene.objects.keys())
    bpy.ops.ed.undo_push(message="Correction schema fixture baseline")
    job = native.begin(window, "angelo_scene_program", validate_call("angelo_scene_program", arguments()))
    try:
        for _ in range(200):
            staged = job.poll()
            if staged.get("final") or staged.get("ready_to_commit"):
                break
        check("corrected_stage_ready", bool(staged.get("ready_to_commit")))
        check("staging_leaves_main_unchanged", set(window.scene.objects.keys()) == original_names and no_stages())
        committed = bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
        report["result"] = job.result
        check("corrected_committed", "FINISHED" in committed and job.result.get("status") == "committed")
        obligations = job.completion_obligations()
        check("all_known_checks_passed", obligations is not None and not obligations.unknowns() and
              len(job.check_results) == len(obligations.checks()) and bool(job.check_results) and
              all(row.get("passed") is True for row in job.check_results))
        created = [obj for obj in window.scene.objects if obj.name not in original_names]
        check("count_two_includes_source", len(created) == 2 and sorted(round(obj.location.x, 5) for obj in created) == [0, 3])
        check("subdivision_two_on_source_and_copy", all(obj.modifiers.get("Subdivision") is not None and
              obj.modifiers["Subdivision"].type == "SUBSURF" and obj.modifiers["Subdivision"].levels == 2 for obj in created))
        check("commit_releases_stage_and_writer", no_stages() and native.WRITER.owner is None)
        bpy.ops.ed.undo()
        queries.invalidate()
        check("one_undo_restores_original_objects", set(window.scene.objects.keys()) == original_names and no_stages())
        bpy.ops.ed.redo()
        queries.invalidate()
        restored = [obj for obj in window.scene.objects if obj.name not in original_names]
        check("redo_restores_corrected_objects", len(restored) == 2 and
              all(obj.modifiers["Subdivision"].levels == 2 for obj in restored) and no_stages())
        report["success"] = True
    finally:
        if not job.closed:
            job.cancel()


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "correction-schemas.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_CORRECTION_SCHEMAS", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
