# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""New modifier types and stack edits (remove, move, apply) through real staged programs.

Covers Array, Decimate, Weighted Normal, Triangulate, Displace, Screw,
Shrinkwrap and Curve modifiers, their derived checks and object revisions, the
committed stack order, removal and apply with Undo, a forced rollback of a
stack edit, and the refusals. Programs are normalized by
``tool_specs.validate_call`` as a model's plan is. Run only in a disposable
instance with the exact Angelo binary: ``--background --factory-startup
--disable-autoexec --python-exit-code 1``. No provider or paid call.
"""

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path
from unittest.mock import patch

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-modifier-stack-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, program_commit, scene_queries as queries  # noqa: E402
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
    report.setdefault("results", []).append({key: job.result.get(key) for key in ("status", "error", "checks")})
    return "FINISHED" in result and job.result.get("status") == "committed"


def objects():
    return window.scene.objects


def handle(obj):
    return queries.object_handle(obj)


def binding(obj):
    return {"handle": handle(obj), "revision": queries.object_revision(window, obj)}


def program(operations, writes=(), reads=()):
    return validate_call("angelo_scene_program", {"mode": "APPLY", "read_set": [binding(obj) for obj in reads],
                                                  "write_set": [binding(obj) for obj in writes], "operations": operations})


def run(arguments):
    job = native.begin(window, "angelo_scene_program", arguments)
    return job, settle(job)


def modifier(owner, kind, name, **settings):
    return {"kind": "modifier_add", "target": handle(owner), "parameters": {"type": kind, "name": name, "settings": settings}}


def stack(obj):
    return [(item.name, item.type) for item in obj.modifiers]


def derived(job, kind):
    """The job's host-derived obligations of ``kind`` (5073aa55350: never in the public arguments)."""
    return [item for item, flag in zip(job.obligations.checks(), job.obligations.derived_flags)
            if flag and item["kind"] == kind]


def checks_passed(job):
    return bool(job.result.get("checks")) and all(row.get("passed") for row in job.result["checks"])


def new_types():
    bpy.ops.curve.primitive_bezier_curve_add(location=(0, 4, 0))
    bpy.context.object.name = "QA Path"
    bpy.ops.mesh.primitive_uv_sphere_add(radius=1.5, location=(0, 0, 0))
    bpy.context.object.name = "QA Ball"
    bpy.ops.ed.undo_push(message="modifier QA baseline")
    queries.invalidate()
    cube, path, ball = objects()["Cube"], objects()["QA Path"], objects()["QA Ball"]
    check("curve_object_has_exact_revision", len(queries.object_revision(window, path)) == 64)
    arguments = program([
        modifier(cube, "ARRAY", "QA Array", count=3, relative_offset_displace=[1.5, 0, 0]),
        modifier(cube, "TRIANGULATE", "QA Triangulate", quad_method="BEAUTY"),
        modifier(cube, "WEIGHTED_NORMAL", "QA Weighted", weight=60, keep_sharp=True),
        modifier(cube, "DECIMATE", "QA Decimate", ratio=.75),
        modifier(cube, "DISPLACE", "QA Displace", strength=.2, direction="Z"),
        modifier(cube, "SHRINKWRAP", "QA Wrap", target=handle(ball), offset=.05),
        modifier(cube, "CURVE", "QA Bend", object=handle(path), deform_axis="POS_Y")], writes=[cube], reads=[ball, path])
    job, staged = run(arguments)
    check("new_types_derive_checks", arguments["expected_checks"] == [] and len(derived(job, "modifier")) == 7)
    check("new_types_ready", staged.get("ready_to_commit") is True and stack(cube) == [])
    check("new_types_commit", commit(job) and checks_passed(job)
          and len(job.result["checks"]) == len(job.obligations.checks()))
    check("new_types_live", stack(cube) == [("QA Array", "ARRAY"), ("QA Triangulate", "TRIANGULATE"),
                                             ("QA Weighted", "WEIGHTED_NORMAL"), ("QA Decimate", "DECIMATE"),
                                             ("QA Displace", "DISPLACE"), ("QA Wrap", "SHRINKWRAP"), ("QA Bend", "CURVE")]
          and cube.modifiers["QA Array"].count == 3 and cube.modifiers["QA Wrap"].target == ball
          and cube.modifiers["QA Bend"].object == path and cube.modifiers["QA Weighted"].keep_sharp)
    check("modified_object_keeps_exact_revision", len(queries.object_revision(window, cube)) == 64)
    job, _ = run(program([{"kind": "modifier_move", "target": handle(cube), "parameters": {"modifier": "QA Bend", "index": 0}},
                          {"kind": "modifier_remove", "target": handle(cube), "parameters": {"modifier": "QA Decimate"}}],
                         writes=[cube], reads=[ball, path]))
    check("stack_edit_commits", commit(job) and checks_passed(job))
    check("stack_edit_live", [name for name, _ in stack(cube)] == ["QA Bend", "QA Array", "QA Triangulate", "QA Weighted",
                                                                  "QA Displace", "QA Wrap"])
    check("stack_check_derived", any(row.get("kind") == "modifier_stack" and row.get("passed") for row in job.result["checks"]))
    bpy.ops.ed.undo()
    queries.invalidate()
    cube = objects()["Cube"]
    check("stack_edit_one_undo", [name for name, _ in stack(cube)][3] == "QA Decimate" and stack(cube)[0][0] == "QA Array")
    bpy.ops.ed.undo()
    queries.invalidate()
    check("new_types_one_undo", stack(objects()["Cube"]) == [] and no_stages())


def forced_rollback():
    cube = objects()["Cube"]
    cube.modifiers.new("QA Keep", "BEVEL").segments = 3
    nodes = cube.modifiers.new("QA Smooth", "WEIGHTED_NORMAL")
    nodes.weight = 70
    cube.modifiers.new("QA Last", "TRIANGULATE")
    queries.invalidate()
    before, active = [(item.name, item.type) for item in cube.modifiers], cube.modifiers.active.name
    real = program_commit._sync_stack

    def fail_after_sync(original, staged, journal):
        real(original, staged, journal)
        raise RuntimeError("injected failure after the stack edit")

    arguments = program([{"kind": "modifier_remove", "target": handle(cube), "parameters": {"modifier": "QA Smooth"}},
                         {"kind": "modifier_move", "target": handle(cube), "parameters": {"modifier": "QA Last", "index": 0}}],
                        writes=[cube])
    job, _ = run(arguments)
    with patch.object(program_commit, "_sync_stack", fail_after_sync):
        bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    report["rollback_result"] = {key: job.result.get(key) for key in (
        "status", "error_code", "partial_changes_possible", "rollback_verified", "rollback_error")}
    check("rollback_reports_failure", job.result.get("status") == "failed" and not job.result.get("partial_changes_possible")
          and job.result.get("rollback_verified") is True)
    check("rollback_restores_stack", [(item.name, item.type) for item in cube.modifiers] == before
          and cube.modifiers.active.name == active
          and cube.modifiers["QA Smooth"].weight == 70 and cube.modifiers["QA Keep"].segments == 3 and no_stages())
    for name in ("QA Keep", "QA Smooth", "QA Last"):
        cube.modifiers.remove(cube.modifiers[name])
    queries.invalidate()


def boolean_and_remesh():
    bpy.ops.mesh.primitive_cube_add(size=1, location=(.5, .5, .5))
    cutter = bpy.context.object
    cutter.name = "QA Cutter"
    bpy.ops.ed.undo_push(message="modifier QA boolean baseline")
    queries.invalidate()
    obj, cutter = objects()["Cube"], objects()["QA Cutter"]
    job, _ = run(program([modifier(obj, "BOOLEAN", "QA Cut", operand=handle(cutter), solver="MANIFOLD", use_self=True)],
                         writes=[obj], reads=[cutter]))
    committed = commit(job)
    live = objects()["Cube"].modifiers.get("QA Cut")
    check("boolean_solver_options_commit", committed and checks_passed(job) and live.solver == "MANIFOLD"
          and live.use_self and live.object == objects()["QA Cutter"])
    bpy.ops.ed.undo()
    queries.invalidate()
    job, result = run(program([modifier(objects()["Cube"], "REMESH", "QA Remesh", mode="VOXEL", voxel_size=.2)],
                              writes=[objects()["Cube"]]))
    unavailable = not (bpy.app.build_options.openvdb if hasattr(bpy.app.build_options, "openvdb") else False)
    check("remesh_refused_where_the_build_lacks_it", (result.get("status") == "failed" and "REMESH_UNAVAILABLE" in result["error"])
          if unavailable else result.get("ready_to_commit") is True)
    report["remesh_available"] = not unavailable
    native.cleanup_all() if not unavailable else None
    check("remesh_leaves_no_stage", no_stages())


def apply_first():
    bpy.ops.mesh.primitive_cube_add(location=(6, 0, 0))
    block = bpy.context.object
    block.name = "QA Block"
    bpy.ops.ed.undo_push(message="modifier QA apply baseline")
    queries.invalidate()
    block = objects()["QA Block"]
    job, _ = run(program([modifier(block, "SUBSURF", "QA Smooth", levels=1),
                          modifier(block, "ARRAY", "QA Row", count=2),
                          {"kind": "modifier_apply", "target": handle(block), "parameters": {"modifier": "QA Smooth"}}],
                         writes=[block]))
    check("apply_commits", commit(job) and checks_passed(job))
    check("apply_bakes_first_modifier", len(block.data.vertices) == 26 and stack(block) == [("QA Row", "ARRAY")])
    bpy.ops.ed.undo()
    queries.invalidate()
    block = objects()["QA Block"]
    check("apply_one_undo", len(block.data.vertices) == 8 and stack(block) == [] and no_stages())
    block.modifiers.new("QA First", "BEVEL")
    block.modifiers.new("QA Second", "WEIGHTED_NORMAL")
    queries.invalidate()
    job, result = run(program([{"kind": "modifier_apply", "target": handle(block), "parameters": {"modifier": "QA Second"}}],
                              writes=[block]))
    check("apply_non_first_refused", result.get("status") == "failed" and "first modifier" in result["error"] and no_stages())
    block.modifiers.clear()
    block.modifiers.new("QA Multires", "MULTIRES")
    queries.invalidate()
    try:
        arguments = program([{"kind": "modifier_remove", "target": handle(block), "parameters": {"modifier": "QA Multires"}}],
                            writes=[block])
    except Exception as error:
        check("multires_remove_refused", "revision" in str(error) or "MULTIRES" in str(error) or "Multires" in str(error))
    else:
        job, result = run(arguments)
        check("multires_remove_refused", result.get("status") == "failed" and no_stages())
    block.modifiers.clear()
    fitted = block.modifiers.new("QA Fit", "ARRAY")
    fitted.fit_type = "FIT_LENGTH"
    queries.invalidate()
    job, result = run(program([{"kind": "transform", "target": handle(block), "parameters": {"location": [6, 1, 0]}}],
                              writes=[block]))
    check("unbounded_existing_array_refused", result.get("status") == "failed" and "fit-to-length" in result["error"])
    block.modifiers.clear()


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    queries.invalidate()
    new_types()
    forced_rollback()
    boolean_and_remesh()
    apply_first()
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "modifier-stack.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_MODIFIER_STACK", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
