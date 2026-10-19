# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""curve_points and object_convert through real staged programs.

Programs are normalized by ``tool_specs.validate_call`` as a model's plan is,
so each carries its derived checks. Covers explicit poly and Bezier points,
curve and text conversion to a new mesh object placed and linked like its
source, deleting the source, one-step Undo and the refusals. Run only in a
disposable instance with the exact Angelo binary: ``--background
--factory-startup --disable-autoexec --python-exit-code 1``. No provider or
paid call.
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
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-curves-qa"))
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


def commit(job):
    result = bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    report.setdefault("results", []).append({key: job.result.get(key) for key in ("status", "error")})
    return "FINISHED" in result and job.result.get("status") == "committed"


def handle(obj):
    return queries.object_handle(obj)


def binding(obj):
    return {"handle": handle(obj), "revision": queries.object_revision(window, obj)}


def program(operations, writes=(), reads=()):
    return validate_call("angelo_scene_program", {"mode": "APPLY", "read_set": [binding(obj) for obj in reads],
                                                  "write_set": [binding(obj) for obj in writes], "operations": operations})


def op(kind, target, **parameters):
    return {"kind": kind, "target": handle(target) if target is not None else None, "parameters": parameters}


def run(arguments):
    job = native.begin(window, "angelo_scene_program", arguments)
    return job, settle(job)


def passed(job):
    return bool(job.result.get("checks")) and all(row.get("passed") for row in job.result["checks"])


def failed_with(result, code):
    return result.get("status") == "failed" and code in (result.get("error") or "")


def setup():
    path_data = bpy.data.curves.new("QA Path", "CURVE")
    spline = path_data.splines.new("BEZIER")
    spline.bezier_points.add(1)
    path = bpy.data.objects.new("QA Path", path_data)
    window.scene.collection.objects.link(path)
    text_data = bpy.data.curves.new("QA Text", "FONT")
    text_data.body, text_data.extrude = "Hi", .1
    text = bpy.data.objects.new("QA Text", text_data)
    text.location, text.rotation_euler = (2, 0, 0), (0, 0, .3)
    bpy.data.collections["Collection"].objects.link(text)
    bpy.ops.ed.undo_push(message="curves QA")
    queries.invalidate()


def points():
    path = window.scene.objects["QA Path"]
    job, _ = run(program([op("curve_points", path, points=[[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 1]],
                             spline_type="POLY", cyclic=True, dimensions="3D")], writes=[path]))
    check("poly_points_commit", commit(job))
    path = window.scene.objects["QA Path"]
    spline = path.data.splines[0]
    check("poly_points_live", len(path.data.splines) == 1 and spline.type == "POLY" and len(spline.points) == 4
          and spline.use_cyclic_u and tuple(spline.points[3].co)[:3] == (0, 1, 1) and path.data.name == "QA Path")
    bpy.ops.ed.undo()
    queries.invalidate()
    path = window.scene.objects["QA Path"]
    check("points_one_undo", path.data.splines[0].type == "BEZIER" and len(path.data.splines[0].bezier_points) == 2)
    job, _ = run(program([op("curve_points", path, points=[[0, 0, 0], [2, 0, 0], [2, 2, 0]], handle_type="VECTOR")],
                         writes=[path]))
    check("bezier_points_commit", commit(job) and [point.handle_left_type for point in
                                                   window.scene.objects["QA Path"].data.splines[0].bezier_points] == ["VECTOR"] * 3)
    bpy.ops.ed.undo()
    queries.invalidate()
    text = window.scene.objects["QA Text"]
    job, result = run(program([op("curve_points", text, points=[[0, 0, 0], [1, 0, 0]])], writes=[text]))
    check("text_points_refused", failed_with(result, "curve objects"))


def conversion():
    text = window.scene.objects["QA Text"]
    objects_before = set(bpy.data.objects.keys())
    job, _ = run(program([op("object_convert", None, source=handle(text), alias="solid")], reads=[text]))
    check("convert_text_commits", commit(job) and passed(job))
    created = [bpy.data.objects[name] for name in set(bpy.data.objects.keys()) - objects_before]
    mesh = created[0] if len(created) == 1 else None
    check("convert_creates_a_mesh_object", mesh is not None and mesh.type == "MESH" and mesh.name == "QA Text.001"
          and len(mesh.data.polygons) > 0 and "QA Text" in window.scene.objects)
    check("convert_keeps_placement_and_collections", mesh.matrix_world == window.scene.objects["QA Text"].matrix_world
          and [c.name for c in mesh.users_collection] == ["Collection"])
    bpy.ops.ed.undo()
    queries.invalidate()
    check("convert_one_undo", set(bpy.data.objects.keys()) == objects_before and no_stages())
    path = window.scene.objects["QA Path"]
    path.data.bevel_depth = .05
    queries.invalidate()
    job, _ = run(program([op("object_convert", None, source=handle(path), alias="pipe", name="QA Pipe",
                             keep_original=False)], writes=[path]))
    check("convert_and_delete_commits", commit(job) and passed(job))
    check("convert_replaces_the_source", "QA Path" not in bpy.data.objects and window.scene.objects["QA Pipe"].type == "MESH"
          and len(window.scene.objects["QA Pipe"].data.vertices) > 8)
    bpy.ops.ed.undo()
    queries.invalidate()
    check("convert_and_delete_one_undo", "QA Path" in window.scene.objects and "QA Pipe" not in bpy.data.objects)
    cube = window.scene.objects["Cube"]
    job, result = run(program([op("object_convert", None, source=handle(cube), alias="again")], reads=[cube]))
    check("mesh_convert_refused", failed_with(result, "curve and text"))
    text = window.scene.objects["QA Text"]
    text.data.body = "x" * 1000
    text.data.resolution_u, text.data.bevel_depth, text.data.bevel_resolution = 64, .01, 32
    queries.invalidate()
    job, result = run(program([op("object_convert", None, source=handle(text), alias="huge")], reads=[text]))
    check("unbounded_convert_refused_before_evaluation", failed_with(result, "bounded native capability") and no_stages())


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    queries.invalidate()
    setup()
    points()
    conversion()
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "curves.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_CURVES", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
