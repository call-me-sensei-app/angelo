# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Subdivision render levels up to 6 and adaptive settings through real staged programs.

Typed ``modifier_add`` and ``modifier_configure`` write render levels up to 6
and the adaptive settings; an object whose stack a script or the artist set to
such values is no longer refused by every later typed action. The suite also
shows what still bounds the work: a stage evaluates viewport levels only, the
render-time ceiling refuses a typed write above it (while an object already
above it can be edited and lowered), and every setting is part of the
object's revision. Commit, Undo, Redo, a forced commit failure and a stale
binding are checked on the live scene. Run only in a disposable instance with
the exact Angelo binary: ``--background --factory-startup --disable-autoexec
--python-exit-code 1``. No provider or paid call.
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
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-subdivision-levels-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries  # noqa: E402
from angelo_agent.action_schema_types import MAX_RENDER_VERTICES  # noqa: E402
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


def op(kind, target=None, **parameters):
    reference = target if isinstance(target, str) or target is None else handle(target)
    return {"kind": kind, "target": reference, "parameters": parameters}


def run(canonical):
    job = native.begin(window, "angelo_scene_program", canonical)
    return job, settle(job)


def committed(name, operations, writes=(), reads=()):
    job, staged = run(program(operations, writes, reads))
    report["last_error"] = staged.get("error")
    check(name + "_stages", staged.get("ready_to_commit"))
    done = commit(job)
    report["last_error"] = job.result.get("error")
    check(name + "_commits", done and bool(job.result.get("checks"))
          and all(row.get("passed") for row in job.result["checks"]))
    return job


def refused(name, operations, text, writes=(), reads=()):
    job, result = run(program(operations, writes, reads))
    failed = result.get("status") == "failed" or not commit(job)
    error = (job.result or result).get("error", "")
    report["last_error"] = error
    check(name, failed and text in error and no_stages())
    return error


def undo():
    bpy.ops.ed.undo()
    queries.invalidate()


def redo():
    bpy.ops.ed.redo()
    queries.invalidate()


def baseline(message):
    bpy.ops.ed.undo_push(message=message)
    queries.invalidate()


def settings(modifier):
    return (modifier.levels, modifier.render_levels, modifier.subdivision_type, bool(modifier.use_adaptive_subdivision),
            modifier.adaptive_space, round(modifier.adaptive_pixel_size, 4))


def subsurf(target, **values):
    return op("modifier_add", target, type="SUBSURF", name="Subdivision", settings=values)


def configure(target, **values):
    return op("modifier_configure", target, type="SUBSURF", modifier="Subdivision", settings=values)


def typed_fields():
    """Render levels above 3 and the adaptive settings are typed, checked, undoable and editable again."""
    baseline("subdivision QA typed")
    cube = objects()["Cube"]
    job = committed("render_five_adaptive", [subsurf(cube, levels=2, render_levels=5, adaptive={
        "enabled": True, "space": "PIXEL", "pixel_size": 1.5})], writes=[cube])
    modifier = cube.modifiers["Subdivision"]
    check("typed_values_are_live", settings(modifier) == (2, 5, "CATMULL_CLARK", True, "PIXEL", 1.5))
    kinds = {row["checks"][0]["kind"]: row["checks"][0]["observed"] for row in job.result["checks"]}
    check("both_derived_checks_read_the_live_modifier", kinds["modifier"]["values"]["render_levels"] == 5
          and kinds["modifier_values"]["values"] == {"use_adaptive_subdivision": True, "adaptive_space": "PIXEL",
                                                    "adaptive_pixel_size": 1.5})
    result = job.result["operation_results"][0]
    # A cube: 24 loops, 16 times at viewport level 2, 64 times more at render level 5.
    check("the_result_states_both_bounds", result["evaluated_vertex_bound"] == 24 * 16
          and result["render_vertex_bound"] == 24 * 16 * 64 and result["adaptive"] is True)
    undo()
    check("typed_one_undo", "Subdivision" not in objects()["Cube"].modifiers and no_stages())
    redo()
    cube = objects()["Cube"]
    check("typed_redo", settings(cube.modifiers["Subdivision"]) == (2, 5, "CATMULL_CLARK", True, "PIXEL", 1.5))
    # The second edit: raise the level again. A null adaptive field keeps the modifier's adaptive settings.
    job = committed("raise_the_level_again", [configure(cube, levels=2, render_levels=6)], writes=[cube])
    check("raised_again", settings(cube.modifiers["Subdivision"]) == (2, 6, "CATMULL_CLARK", True, "PIXEL", 1.5)
          and job.result["checks"][0]["checks"][0]["observed"]["values"]["render_levels"] == 6)
    committed("adaptive_object_space", [configure(cube, levels=1, render_levels=2, adaptive={
        "enabled": True, "space": "OBJECT", "object_edge_length": 0.05})], writes=[cube])
    modifier = cube.modifiers["Subdivision"]
    check("object_space_dicing", modifier.adaptive_space == "OBJECT" and abs(modifier.adaptive_object_edge_length - .05) < 1e-6
          and modifier.use_adaptive_subdivision)
    undo()
    undo()
    check("two_more_undos", settings(objects()["Cube"].modifiers["Subdivision"]) == (2, 5, "CATMULL_CLARK", True, "PIXEL", 1.5))


def stage_evaluates_viewport_levels_only():
    """What the render-level cap protected still holds: no stage evaluates render levels or adaptive dicing."""
    cube = objects()["Cube"]
    modifier = cube.modifiers["Subdivision"]
    job, staged = run(program([op("transform", cube, location=[1, 0, 0])], writes=[cube]))
    check("an_object_with_render_five_and_adaptive_stages", staged.get("ready_to_commit"))
    with native.job_context(job.id) as stage:
        depsgraph = stage.view_layer.depsgraph
        copy = stage.objects[handle(cube)]
        evaluated = copy.evaluated_get(depsgraph).data
        # 8 * 4^2 quads at viewport level 2 on a cube: 98 vertices. Render level 5 would be 6,146.
        check("the_stage_is_a_viewport_evaluation", depsgraph.mode == "VIEWPORT" and len(evaluated.vertices) == 98
              and copy.modifiers["Subdivision"].render_levels == modifier.render_levels == 5)
    check("the_transform_commits", commit(job) and tuple(cube.location) == (1, 0, 0))
    undo()


def lock_out_is_removed():
    """A stack a script left (render level 4, adaptive) no longer refuses every later typed action."""
    baseline("subdivision QA lock-out")
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3, radius=1, location=(4, 0, 0))
    island = bpy.context.object
    island.name = "QA Island"
    scripted = island.modifiers.new("Subdivision", "SUBSURF")
    scripted.levels, scripted.render_levels = 2, 4
    dice = island.modifiers.new("Dicing", "SUBSURF")
    dice.subdivision_type, dice.levels, dice.render_levels = "SIMPLE", 0, 1
    dice.use_adaptive_subdivision, dice.adaptive_space, dice.adaptive_pixel_size = True, "PIXEL", 1.0
    queries.invalidate()
    state = queries.inspect_object(window, handle(island)) if hasattr(queries, "inspect_object") else None
    report["island_state"] = state if isinstance(state, dict) and len(json.dumps(state, default=str)) < 4000 else None
    revision = queries.object_revision(window, island)
    check("the_scripted_stack_has_an_exact_revision", isinstance(revision, str) and len(revision) == 64)
    job = committed("typed_actions_on_the_scripted_stack", [
        op("transform", island, location=[4, 2, 0]),
        op("modifier_add", island, type="BEVEL", name="Soft Edges", settings={"width": 0.01, "segments": 1})],
        writes=[island])
    check("the_scripted_stack_is_intact", tuple(island.location) == (4, 2, 0)
          and [m.name for m in island.modifiers] == ["Subdivision", "Dicing", "Soft Edges"]
          and settings(island.modifiers["Subdivision"])[:2] == (2, 4) and island.modifiers["Dicing"].use_adaptive_subdivision)
    # Every adaptive setting is part of the revision: an edit the plan did not see makes it stale.
    stale = program([op("transform", island, location=[4, 3, 0])], writes=[island])
    island.modifiers["Dicing"].adaptive_pixel_size = 2.0
    queries.invalidate(window)
    job, result = run(stale)
    check("an_adaptive_setting_changes_the_revision", result.get("status") == "failed"
          and "revision changed" in result.get("error", "") and tuple(island.location) == (4, 2, 0) and no_stages())
    island.modifiers["Dicing"].adaptive_pixel_size = 1.0
    queries.invalidate()
    # Outside what the typed kinds express, a stack is still refused: fail closed, as before.
    island.modifiers["Subdivision"].render_levels = 7
    queries.invalidate()
    refused("render_levels_above_six_stay_refused", [op("transform", island, location=[4, 3, 0])],
            "Existing subdivision exceeds the bounded native capability", writes=[island])
    island.modifiers["Subdivision"].render_levels = 4
    island.modifiers["Subdivision"].levels = 4
    queries.invalidate()
    refused("viewport_levels_above_three_stay_refused", [op("transform", island, location=[4, 3, 0])],
            "Existing subdivision exceeds the bounded native capability", writes=[island])
    island.modifiers["Subdivision"].levels = 2
    queries.invalidate()


def render_ceiling():
    """A typed write may not raise a stack's render-time size past the ceiling; an object above it can be lowered."""
    baseline("subdivision QA ceiling")
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=100, y_subdivisions=100, size=10, location=(0, 8, 0))
    ground = bpy.context.object
    ground.name = "QA Ground"
    queries.invalidate()
    loops = max(len(ground.data.loops), 2 * len(ground.data.edges))  # The loop-scale bound: 2E on a grid.
    check("the_fixture_is_dense", loops == 40400)
    error = refused("render_six_on_a_dense_mesh_is_refused", [subsurf(ground, levels=1, render_levels=6)],
                    "render-time topology budget", writes=[ground])
    check("the_refusal_states_the_size_and_the_remedy", str(loops * 4 * 4 ** 5) in error and str(MAX_RENDER_VERTICES) in error
          and "adaptive" in error and "Subdivision" not in ground.modifiers)
    job = committed("render_four_fits", [subsurf(ground, levels=0, render_levels=4)], writes=[ground])
    check("render_four_is_within_the_ceiling",
          job.result["operation_results"][0]["render_vertex_bound"] == loops * 256 <= MAX_RENDER_VERTICES)
    refused("raising_it_past_the_ceiling_is_refused", [configure(ground, levels=0, render_levels=5)],
            "render-time topology budget", writes=[ground])
    refused("so_is_another_modifier_that_multiplies_it", [op("modifier_add", ground, type="ARRAY", name="Row", settings={
        "count": 4})], "render-time topology budget", writes=[ground])
    # A script or the artist set it above the ceiling: typed kinds can still edit and lower it, never raise it.
    ground.modifiers["Subdivision"].render_levels = 6
    queries.invalidate()
    job = committed("an_object_above_the_ceiling_can_be_edited", [op("transform", ground, location=[0, 9, 0])],
                    writes=[ground])
    job = committed("and_lowered", [configure(ground, levels=0, render_levels=5)], writes=[ground])
    check("lowered_but_still_above_the_ceiling", ground.modifiers["Subdivision"].render_levels == 5
          and job.result["operation_results"][0]["render_vertex_bound"] > MAX_RENDER_VERTICES)
    refused("but_not_raised_again", [configure(ground, levels=0, render_levels=6)], "render-time topology budget",
            writes=[ground])


def forced_rollback():
    queries.invalidate()
    cube = objects()["Cube"]
    before = settings(cube.modifiers["Subdivision"])
    fingerprint = native._fingerprint(cube)
    original = native.BlenderProgramBackend._apply_collection_intents

    def fail_after(self, stage, journal):
        original(self, stage, journal)
        raise RuntimeError("injected failure after the modifier transfer")

    job, _ = run(program([configure(cube, levels=3, render_levels=6, adaptive={"enabled": False, "pixel_size": 4.0})],
                         writes=[cube]))
    with patch.object(native.BlenderProgramBackend, "_apply_collection_intents", fail_after):
        bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    check("rollback_reports_failure", job.result.get("status") == "failed" and not job.result.get("partial_changes_possible"))
    check("rollback_restores_every_subdivision_setting", settings(cube.modifiers["Subdivision"]) == before
          and native._fingerprint(cube) == fingerprint and no_stages())


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    queries.invalidate()
    typed_fields()
    stage_evaluates_viewport_levels_only()
    lock_out_is_removed()
    render_ceiling()
    forced_rollback()
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "subdivision-levels.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_SUBDIVISION_LEVELS", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
