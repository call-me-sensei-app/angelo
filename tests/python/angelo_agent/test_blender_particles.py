# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Particle systems and Distribute Points on Faces through real staged programs, with Undo.

Programs are normalized by ``tool_specs.validate_call`` as a model's plan is,
so each carries its derived ``particle_values`` check. Covers a new system
(``modifier_add`` PARTICLE_SYSTEM then ``particle_set``) staged across
suspend/resume, private staging of shared ParticleSettings (preview and accept
of an existing system, a shared one that keeps its other user), per-system
settings, an instanced object, stale revisions before binding and before
commit, budget and dependency refusals, a forced commit failure's rollback,
duplicates (also one whose system the same program edits), and the Distribute
Points on Faces area rule. Run only in a disposable instance with the exact
Angelo binary: ``--background --factory-startup --disable-autoexec
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
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-particles-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, program_commit, scene_queries as queries  # noqa: E402
from angelo_agent.action_particles import SOURCE  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import no_stages, commit_operator, spent_budget  # noqa: E402

report = {"checks": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path, "version": bpy.app.version_string}
window = bpy.context.window_manager.windows[0]


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def settle(job):
    for _ in range(400):
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


def op(kind, target, **parameters):
    return {"kind": kind, "target": handle(target) if target is not None else None, "parameters": parameters}


def program(operations, writes=(), reads=(), mode="APPLY"):
    return validate_call("angelo_scene_program", {"mode": mode, "write_set": [binding(obj) for obj in writes],
                                                  "read_set": [binding(obj) for obj in reads], "operations": operations})


def run(arguments):
    job = native.begin(window, "angelo_scene_program", arguments)
    return job, settle(job)


def passed(job):
    return bool(job.result.get("checks")) and all(row.get("passed") for row in job.result["checks"])


def failed_with(result, code):
    return result.get("status") == "failed" and code in (result.get("error") or "")


def refused(operations, code, writes, reads=()):
    try:
        _job, result = run(program(operations, writes=writes, reads=reads))
        return failed_with(result, code) and no_stages()
    except ValueError as error:
        return code in str(error) and no_stages()


def obj(name):
    return window.scene.objects[name]


def revision_exact(target):
    queries.invalidate()
    return isinstance(queries.object_revision(window, target), str)


def evaluated(target):
    window.view_layer.update()
    return target.evaluated_get(bpy.context.evaluated_depsgraph_get())


def new_system():
    """modifier_add then particle_set in one program, every step suspended and resumed."""
    bpy.ops.ed.undo_push(message="particles QA new system")
    queries.invalidate()
    count = len(bpy.data.particles)
    arguments = program([op("modifier_add", obj("Cube"), type="PARTICLE_SYSTEM", name="QA Hair"),
                         op("particle_set", obj("Cube"), modifier="QA Hair",
                            settings={"type": "HAIR", "count": 200, "hair_length": .4})], writes=[obj("Cube")])
    with spent_budget():
        job, _ = run(arguments)
    check("new_system_commits_after_hydration", commit(job) and passed(job)
          and "particle_values" in [row["kind"] for row in job.result["checks"]])
    psys = obj("Cube").particle_systems["QA Hair"]
    settings = psys.settings
    check("new_system_settings_live", settings.type == "HAIR" and settings.count == 200
          and abs(settings.hair_length - .4) < 1e-6 and SOURCE not in settings.keys())
    check("new_system_leaves_one_settings_datablock", len(bpy.data.particles) == count + 1
          and settings.name == "ParticleSettings" and settings.users == 1)
    check("new_system_evaluates", len(evaluated(obj("Cube")).particle_systems["QA Hair"].particles) == 200)
    check("particle_object_stays_bindable", revision_exact(obj("Cube")))
    check("new_system_no_stage_residue", no_stages() and not native.runtime_is_staging())
    bpy.ops.ed.undo()
    queries.invalidate()
    check("new_system_one_undo", not len(obj("Cube").particle_systems) and len(bpy.data.particles) == count)
    bpy.ops.ed.redo()
    queries.invalidate()
    check("new_system_one_redo", obj("Cube").particle_systems["QA Hair"].settings.count == 200 and no_stages())


def existing_system():
    """An existing system's settings: previewed on a private copy, accepted, then a per-system setting."""
    queries.invalidate()
    live = obj("Cube").particle_systems["QA Hair"].settings
    arguments = program([op("particle_set", obj("Cube"), modifier="QA Hair", settings={"count": 300})],
                        writes=[obj("Cube")], mode="PREVIEW")
    result = settle(native.begin(window, "angelo_scene_program", arguments))
    check("preview_keeps_live_settings", result.get("status") == "preview" and live.count == 200 and no_stages()
          and any(row.get("changes", {}).get("particles") for row in result["delta"]["details"]))
    accepted = native.begin(window, "angelo_accept_preview", {"preview_id": result["preview_id"]})
    check("preview_accept_ready", settle(accepted).get("ready_to_commit"))
    check("preview_accept_commits", commit(accepted))
    settings = obj("Cube").particle_systems["QA Hair"].settings
    replaced = [block for block in bpy.data.particles if block != settings]
    check("accept_gives_the_system_its_edited_copy", settings.count == 300 and settings.name == "ParticleSettings"
          and SOURCE not in settings.keys() and len(replaced) == 1 and replaced[0].users == 0)
    job, _ = run(program([op("modifier_configure", obj("Cube"), type="PARTICLE_SYSTEM", modifier="QA Hair",
                             settings={"particle_system.seed": 7})], writes=[obj("Cube")]))
    check("per_system_setting_commits", commit(job) and passed(job)
          and obj("Cube").particle_systems["QA Hair"].seed == 7)
    check("existing_system_stays_bindable", revision_exact(obj("Cube")))


def stale_and_rollback():
    """A manual edit after observing or staging refuses the program; a failed commit restores everything."""
    queries.invalidate()
    settings = obj("Cube").particle_systems["QA Hair"].settings
    observed = binding(obj("Cube"))
    settings.hair_length = .5
    queries.invalidate(window)  # An unknown edit: handles stay, revisions are recomputed.
    check("settings_edit_changes_revision", queries.object_revision(window, obj("Cube")) != observed["revision"])
    try:
        stale = validate_call("angelo_scene_program", {"mode": "APPLY", "write_set": [observed], "read_set": [],
                                                       "operations": [op("particle_set", obj("Cube"), modifier="QA Hair",
                                                                         settings={"count": 250})]})
        _job, result = run(stale)
        report["stale_result"] = {key: result.get(key) for key in ("status", "error")}
        check("stale_revision_refused", result.get("status") == "failed" and settings.count == 300 and no_stages())
    except Exception as error:  # A precondition refusal before staging is also a refusal.
        report["stale_result"] = repr(error)[:400]
        check("stale_revision_refused", "revision" in str(error) and settings.count == 300 and no_stages())
    arguments = program([op("particle_set", obj("Cube"), modifier="QA Hair", settings={"count": 250})],
                        writes=[obj("Cube")], mode="PREVIEW")
    result = settle(native.begin(window, "angelo_scene_program", arguments))
    settings.hair_length = .6  # A manual edit while the preview waits.
    try:
        accepted = native.begin(window, "angelo_accept_preview", {"preview_id": result["preview_id"]})
        outcome = settle(accepted)
        stale = outcome.get("status") == "failed" or not commit(accepted)
    except Exception:
        stale = True
    check("edit_during_preview_refused", stale and settings.count == 300 and abs(settings.hair_length - .6) < 1e-6
          and no_stages())
    before = native._fingerprint(obj("Cube"))
    job, _ = run(program([op("particle_set", obj("Cube"), modifier="QA Hair", settings={"count": 150}),
                          op("modifier_configure", obj("Cube"), type="PARTICLE_SYSTEM", modifier="QA Hair",
                             settings={"particle_system.seed": 9})], writes=[obj("Cube")]))
    with patch.object(program_commit, "restore_sources", side_effect=RuntimeError("injected after the transfer")):
        result = job.commit()
    psys = obj("Cube").particle_systems["QA Hair"]
    check("failed_commit_rolls_back_particles", not result["success"] and not result["partial_changes_possible"]
          and psys.settings == settings and settings.count == 300 and psys.seed == 7
          and native._fingerprint(obj("Cube")) == before and no_stages())


def own_settings(target, psys):
    """Make a system's settings single-user the way Blender's New button does."""
    with bpy.context.temp_override(object=target, active_object=target, particle_system=psys):
        return "FINISHED" in bpy.ops.particle.new()


def shared_and_instanced():
    """Shared ParticleSettings are refused; single-user ones take an instanced object, a declared dependency."""
    plane_mesh = bpy.data.meshes.new("QA Ground")
    plane_mesh.from_pydata([(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)], [], [(0, 1, 2, 3)])
    ground = bpy.data.objects.new("QA Ground", plane_mesh)
    window.scene.collection.objects.link(ground)
    ground.modifiers.new("QA Shared", "PARTICLE_SYSTEM")
    ground.particle_systems[0].settings.user_remap(obj("Cube").particle_systems["QA Hair"].settings)
    rock = bpy.data.objects.new("QA Rock", bpy.data.meshes.new("QA Rock"))
    rock.data.from_pydata([(0, 0, 0), (.1, 0, 0), (0, .1, 0)], [], [(0, 1, 2)])
    window.scene.collection.objects.link(rock)
    for block in [block for block in bpy.data.particles if not block.users]:
        bpy.data.particles.remove(block)
    queries.invalidate()
    shared = obj("Cube").particle_systems["QA Hair"].settings
    check("shared_settings_refused", refused([op("particle_set", obj("QA Ground"), modifier="QA Shared",
                                                 settings={"count": 50})], "PARTICLE_SETTINGS_SHARED",
                                             [obj("QA Ground")]) and shared.count == 300 and shared.users == 2)
    check("own_settings_made", own_settings(obj("QA Ground"), obj("QA Ground").particle_systems[0])
          and obj("QA Ground").particle_systems[0].settings != shared)
    bpy.ops.ed.undo_push(message="particles QA instancing baseline")
    queries.invalidate()
    job, _ = run(program([op("particle_set", obj("QA Ground"), modifier="QA Shared",
                             settings={"render_type": "OBJECT", "instance_object": {"object": handle(obj("QA Rock"))},
                                       "count": 120})], writes=[obj("QA Ground")], reads=[obj("QA Rock")]))
    check("instancing_commits", commit(job) and passed(job))
    ground_settings, cube_settings = obj("QA Ground").particle_systems[0].settings, obj("Cube").particle_systems[0].settings
    check("instancing_settings_live", ground_settings != cube_settings and cube_settings.count == 300
          and cube_settings.render_type == "PATH" and ground_settings.count == 120
          and ground_settings.instance_object == obj("QA Rock") and cube_settings.users == 1)
    check("instancing_object_stays_bindable", revision_exact(obj("QA Ground")))
    queries.invalidate()
    job, _ = run(program([op("object_duplicate", None, source=handle(obj("QA Ground")), alias="twin", name="QA Twin")],
                         reads=[obj("QA Ground"), obj("QA Rock")]))
    check("duplicate_commits", commit(job))
    twin = obj("QA Twin")
    check("duplicate_shares_the_live_settings", twin.particle_systems[0].settings == obj("QA Ground").particle_systems[0].settings
          and not any(SOURCE in block.keys() for block in bpy.data.particles))
    bpy.ops.ed.undo()
    bpy.ops.ed.undo()
    queries.invalidate()
    check("instancing_two_undos_restore", obj("QA Ground").particle_systems[0].settings.instance_object is None
          and "QA Twin" not in window.scene.objects and no_stages())


def duplicate_then_edit():
    """A duplicate shares its source's staged settings; editing its system in the same program copies them first.

    The copy is Blender's ``particle.new``, called inside the program's stage override: its view-layer update
    needs the stage's scene and view layer as one pair (``program_objects._override``). With the artist's scene
    and the stage's view layer, the depsgraph update crashed the app.
    """
    queries.invalidate()
    live = obj("QA Ground").particle_systems[0].settings
    count = live.count
    edit = {"kind": "particle_set", "target": "$own", "parameters": {"modifier": "QA Shared", "settings": {"count": 64}}}
    job, _ = run(program([op("object_duplicate", None, source=handle(obj("QA Ground")), alias="own", name="QA Own Twin"),
                          edit], reads=[obj("QA Ground")]))
    check("duplicate_then_edit_commits", commit(job) and passed(job))
    twin = obj("QA Own Twin").particle_systems[0].settings
    check("duplicate_then_edit_gets_its_own_settings", twin != live and twin.count == 64 and live.count == count
          and live.users == 1 and twin.users == 1 and not any(SOURCE in block.keys() for block in bpy.data.particles))
    check("duplicate_then_edit_no_stage_residue", no_stages() and not native.runtime_is_staging())
    bpy.ops.ed.undo()
    queries.invalidate()
    check("duplicate_then_edit_one_undo", "QA Own Twin" not in window.scene.objects
          and obj("QA Ground").particle_systems[0].settings.count == count and no_stages())


def refusals():
    """Over-budget, unsupported and destructive particle edits are refused before evaluation."""
    cube = obj("Cube")
    check("budget_refused", refused([op("particle_set", cube, modifier="QA Hair", settings={"count": 5000000})],
                                    "PARTICLE_BUDGET", [cube]))
    check("render_children_budget_refused", refused([op("particle_set", cube, modifier="QA Hair", settings={
        "child_type": "INTERPOLATED", "rendered_child_count": 100000})], "PARTICLE_BUDGET", [obj("Cube")]))
    check("boids_refused", refused([op("particle_set", obj("Cube"), modifier="QA Hair", settings={
        "type": "EMITTER", "physics_type": "BOIDS"})], "PARTICLE_PHYSICS_UNSUPPORTED", [obj("Cube")]))
    check("remove_refused", refused([op("modifier_remove", obj("Cube"), modifier="QA Hair")], "discard data",
                                    [obj("Cube")]))
    check("missing_system_refused", refused([op("particle_set", obj("Cube"), modifier="QA Nothing", settings={"count": 5})],
                                            "no PARTICLE_SYSTEM modifier", [obj("Cube")]))
    check("file_path_refused", refused([op("particle_set", obj("Cube"), modifier="QA Hair",
                                           settings={"filepath": "x"})], "FILE_PATH", [obj("Cube")]))
    check("separate_refused", refused([op("mesh_separate", obj("Cube"), mode="LOOSE")], "PARTICLE_SYSTEMS",
                                      [obj("Cube")]))
    check("refusals_leave_live_settings", obj("Cube").particle_systems["QA Hair"].settings.count == 300)


def distribute_points():
    """Distribute Points on Faces bounded by the base mesh area, before evaluation."""
    ground = obj("QA Ground")
    ground.modifiers.clear()
    bpy.ops.ed.undo_push(message="particles QA distribute baseline")
    queries.invalidate()

    def graph(density, name):
        return op("geometry_nodes", obj("QA Ground"), name=name, nodes=[
            {"id": "in", "type": "NodeGroupInput"},
            {"id": "scatter", "type": "GeometryNodeDistributePointsOnFaces", "inputs": {"Density": density}},
            {"id": "out", "type": "NodeGroupOutput"}], links=[
            {"from_node": "in", "from_socket": "Geometry", "to_node": "scatter", "to_socket": "Mesh"},
            {"from_node": "scatter", "from_socket": "Points", "to_node": "out", "to_socket": "Geometry"}])

    job, _ = run(program([graph(10, "QA Scatter")], writes=[obj("QA Ground")]))
    check("distribute_commits", commit(job))
    points = evaluated(obj("QA Ground")).evaluated_geometry()
    count = len(points.pointcloud.points) if points.pointcloud is not None else 0
    check("distribute_evaluates_within_bound", 0 < count <= 10 * 4 + 2)
    check("distribute_object_stays_bindable", revision_exact(obj("QA Ground")))
    bpy.ops.ed.undo()
    queries.invalidate()
    check("distribute_over_budget_refused", refused([graph(1000000, "QA Dense")], "budget", [obj("QA Ground")]))
    obj("QA Ground").modifiers.new("QA Wave", "WAVE")
    queries.invalidate()
    check("distribute_after_modifier_refused", refused([graph(1, "QA Late")], "DISTRIBUTE_AREA_UNBOUNDED",
                                                       [obj("QA Ground")]))
    obj("QA Ground").modifiers.clear()
    queries.invalidate()


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    queries.invalidate()
    new_system()
    existing_system()
    stale_and_rollback()
    shared_and_instanced()
    duplicate_then_edit()
    refusals()
    distribute_points()
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "particles.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_PARTICLES", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
