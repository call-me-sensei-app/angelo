# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""mesh_bend through real staged programs: the bow, its refusals, Undo, and the arched footbridge in one program.

The arched-footbridge one-shot (``scenario_oneshot_mini_footbridge_arched``) is
built here as one typed program, the plan a model can now write for it, and
graded with that test's own closed checks: the arch needs no script, so a Codex
plan can end with it (doc/angelo/performance.rst, "One-shot rounds").

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
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-mesh-bend-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import args_for, no_stages, commit_operator  # noqa: E402

report = {"checks": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path, "version": bpy.app.version_string}
window = bpy.context.window_manager.windows[0]


def check(name, condition, detail=None):
    report["checks"][name] = bool(condition)
    if detail is not None:
        report.setdefault("details", {})[name] = detail
    assert condition, (name, detail)


def settle(job):
    for _ in range(400):
        result = job.poll()
        if result.get("final") or result.get("ready_to_commit"):
            return result
    raise AssertionError("cooperative native program did not settle")


def apply(operations, writes=()):
    """Stage and commit one program; (committed, staged result, final result)."""
    job = native.begin(window, "angelo_scene_program",
                       validate_call("angelo_scene_program", args_for(window, operations, writes=writes)))
    staged = settle(job)
    if not staged.get("ready_to_commit"):
        return False, staged, staged
    result = bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    queries.invalidate()
    return "FINISHED" in result and job.result.get("status") == "committed", staged, job.result


def op(kind, target=None, **parameters):
    return {"kind": kind, "target": target, "parameters": parameters}


def cube(name, alias, location, scale):
    return op("create_primitive", primitive="CUBE", size=1, name=name, alias=alias, location=location, scale=scale)


def world(obj):
    return [obj.matrix_world @ vertex.co for vertex in obj.data.vertices]


def plank():
    """A 4 x 1.2 x 0.2 m plank made by scaling a 1 m cube, bowed by 0.3 m in the same program."""
    committed, staged, result = apply([cube("QA Plank", "plank", [0, 4, 0], [4, 1.2, 0.2]),
                                       op("mesh_bend", "$plank", rise=0.3)])
    check("plank_commits", committed and no_stages(), result.get("error"))
    obj = window.scene.objects["QA Plank"]
    mesh = obj.data
    # 15 cuts across four long faces: 8 + 4 * 15 vertices, the two end caps plus 4 * 16 faces.
    check("plank_cut_into_sixteen_slices", (len(mesh.vertices), len(mesh.polygons)) == (68, 66),
          [len(mesh.vertices), len(mesh.polygons)])
    points = world(obj)
    span = max(point.x for point in points) - min(point.x for point in points)
    top, low = max(point.z for point in points), min(point.z for point in points)
    # The middle's top rises by 0.3 m; an end's bottom stays at -0.1 m; the length stays 4 m.
    check("plank_middle_rises_ends_and_length_stay", abs(top - 0.4) < 1e-5 and abs(low + 0.1) < 1e-5
          and abs(span - 4) < 1e-5, {"top": top, "low": low, "span": span})
    # Each vertex moved straight up by 0.3 * (1 - t^2): a quarter of the way in, by 0.225 m.
    quarter = [point.z for point in points if abs(point.x - 1) < 1e-4]
    check("plank_follows_the_bow", quarter and abs(max(quarter) - (0.1 + 0.225)) < 1e-5
          and abs(min(quarter) - (-0.1 + 0.225)) < 1e-5, quarter)
    facts = json.dumps(result, default=str)
    check("result_states_the_bend", '"span": 4.0' in facts and '"axis": "X"' in facts and '"up": "Z"' in facts,
          facts[:400])
    check("plank_scale_kept", tuple(round(value, 6) for value in obj.scale) == (4, 1.2, 0.2))
    return obj


def refusals(obj):
    handle = queries.object_handle(obj)
    before = [tuple(vertex.co) for vertex in obj.data.vertices]
    count = len(window.scene.objects)
    committed, staged, _ = apply([op("create_primitive", primitive="PLANE", size=1, name="QA Sheet", alias="sheet"),
                                  op("mesh_bend", "$sheet", rise=0.2, axis="Z", up="X")])
    check("no_length_along_the_axis_refused", not committed and "no length along Z" in (staged.get("error") or "")
          and len(window.scene.objects) == count and no_stages(), staged.get("error"))
    try:
        validate_call("angelo_scene_program", args_for(window, [op("mesh_bend", handle, rise=0.3, axis="Z")],
                                                       writes=[obj]))
    except Exception as error:  # noqa: BLE001 (whatever the validator raises, it names the rule)
        check("axis_equal_to_up_refused_before_staging", "other than up" in str(error), str(error))
    else:
        check("axis_equal_to_up_refused_before_staging", False)
    check("refusals_change_nothing", [tuple(vertex.co) for vertex in obj.data.vertices] == before)


def sag_and_plan_curve():
    committed, _, result = apply([cube("QA Rope", "rope", [0, -4, 2], [6, 0.1, 0.1]),
                                  op("mesh_bend", "$rope", rise=-0.5, segments=24),
                                  cube("QA Bench", "bench", [0, -7, 0.4], [3, 0.4, 0.05]),
                                  op("mesh_bend", "$bench", rise=0.4, up="Y")])
    check("sag_and_plan_curve_commit", committed and no_stages(), result.get("error"))
    rope = world(window.scene.objects["QA Rope"])
    middle = [point.z for point in rope if abs(point.x) < 1e-4]
    check("negative_rise_sags", middle and abs(max(middle) - (2 - 0.5 + 0.05)) < 1e-4, middle)
    bench = world(window.scene.objects["QA Bench"])
    check("up_y_curves_in_plan", abs(max(point.y for point in bench) - (-7 + 0.4 + 0.2)) < 1e-4
          and abs(max(point.z for point in bench) - 0.425) < 1e-4)


def arched_footbridge():
    """The arched-footbridge one-shot as one typed program, graded by that test's closed checks."""
    import scenario_checks
    import scenario_oneshot_mini_footbridge_arched as spec
    count = len(window.scene.objects)
    stone = {"Base Color": [0.42, 0.42, 0.40, 1.0], "Roughness": 0.85}
    posts = [[-1.9, -0.55, 0.7], [-1.9, 0.55, 0.7], [1.9, -0.55, 0.7], [1.9, 0.55, 0.7]]
    committed, _, result = apply([
        cube("Bridge Deck", "deck", [0, 0, 0.35], [4, 1.2, 0.2]), op("mesh_bend", "$deck", rise=0.3),
        cube("Bridge Rail L", "rail_l", [0, -0.55, 1.25], [4, 0.08, 0.08]), op("mesh_bend", "$rail_l", rise=0.3),
        cube("Bridge Rail R", "rail_r", [0, 0.55, 1.25], [4, 0.08, 0.08]), op("mesh_bend", "$rail_r", rise=0.3),
        cube("Bridge Post", "post", posts[0], [0.12, 0.12, 1.1]),
        op("material_principled", "$deck", name="Grey Stone", inputs=stone, also=["$rail_l", "$rail_r", "$post"]),
        op("batch", sources=["$post"], at=posts, linked=True)])
    check("footbridge_one_program_commits", committed and no_stages() and len(window.scene.objects) == count + 7,
          result.get("error"))
    window.view_layer.update()
    graded = scenario_checks.evaluate(bpy, window.scene, spec.CHECKS)
    report["footbridge_grade"] = graded
    check("footbridge_passes_the_one_shot_checks", graded["success"],
          [(entry["label"], entry["passed"], entry["details"]) for entry in graded["results"]])


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    queries.invalidate()
    bpy.ops.ed.undo_push(message="mesh bend QA baseline")
    queries.invalidate()
    baseline = len(window.scene.objects)
    arched_footbridge()  # First, on the factory scene the one-shot starts from: the grader counts everything built.
    obj = plank()
    refusals(obj)
    sag_and_plan_curve()
    for _ in range(3):  # Each committed program is one Undo step.
        bpy.ops.ed.undo()
    queries.invalidate()
    check("undo_restores_the_scene", len(window.scene.objects) == baseline and no_stages(), len(window.scene.objects))
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "mesh-bend.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_MESH_BEND", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
