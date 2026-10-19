# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Generic modifiers, constraints and nodes through real staged programs, with read-back after commit.

Programs are normalized by ``tool_specs.validate_call`` as a model's plan is,
so each carries its derived ``modifier_values``/``constraint_values`` checks.
Covers deforming, pointer-bearing and generating modifier types, a later
configure of the same object (its revision stays exact), any constraint type
and constraint_configure, generic shader and geometry nodes, the bound and
deny refusals and the WRITABLE description. Run only in a disposable instance
with the exact Angelo binary: ``--background --factory-startup
--disable-autoexec --python-exit-code 1``. No provider or paid call.
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
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-generic-settings-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries, scene_settings  # noqa: E402
from angelo_agent import api_lookup  # noqa: E402
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


def op(kind, target, **parameters):
    return {"kind": kind, "target": handle(target), "parameters": parameters}


def program(operations, writes=(), reads=()):
    return validate_call("angelo_scene_program", {"mode": "APPLY", "write_set": [binding(obj) for obj in writes],
                                                  "read_set": [binding(obj) for obj in reads], "operations": operations})


def run(arguments):
    job = native.begin(window, "angelo_scene_program", arguments)
    return job, settle(job)


def passed(job):
    return bool(job.result.get("checks")) and all(row.get("passed") for row in job.result["checks"])


def failed_with(result, code):
    return result.get("status") == "failed" and code in (result.get("error") or "")


def cube():
    return window.scene.objects["Cube"]


def revision_exact(obj):
    queries.invalidate()
    return isinstance(queries.object_revision(window, obj), str)


def modifiers():
    lattice = bpy.data.objects.new("QA Lattice", bpy.data.lattices.new("QA Lattice"))
    window.scene.collection.objects.link(lattice)
    bpy.ops.ed.undo_push(message="generic modifiers QA")
    queries.invalidate()
    lattice = window.scene.objects["QA Lattice"]
    job, _ = run(program([op("modifier_add", cube(), type="WAVE", name="QA Wave", settings={"height": .5, "use_x": False}),
                          op("modifier_add", cube(), type="LATTICE", settings={"object": {"object": handle(lattice)}, "strength": .75}),
                          op("modifier_add", cube(), type="WIREFRAME", settings={"thickness": .05})],
                         writes=[cube()], reads=[lattice]))
    check("generic_modifiers_commit", commit(job) and passed(job)
          and [row["kind"] for row in job.result["checks"]].count("modifier_values") == 3)
    wave, lattice_modifier = cube().modifiers["QA Wave"], cube().modifiers["Angelo Lattice"]
    check("generic_modifier_values_live", abs(wave.height - .5) < 1e-6 and not wave.use_x
          and lattice_modifier.object == window.scene.objects["QA Lattice"] and abs(lattice_modifier.strength - .75) < 1e-6)
    check("generic_modifier_object_stays_bindable", revision_exact(cube()))
    job, _ = run(program([op("modifier_configure", cube(), type="WAVE", modifier="QA Wave", settings={"height": .2})],
                         writes=[cube()], reads=[window.scene.objects["QA Lattice"]]))  # The lattice is a dependency.
    check("generic_modifier_configure_commits", commit(job) and passed(job) and abs(cube().modifiers["QA Wave"].height - .2) < 1e-6)
    bpy.ops.ed.undo()
    bpy.ops.ed.undo()
    queries.invalidate()
    check("generic_modifiers_undo", not len(cube().modifiers) and no_stages())
    for parameters, code in (({"type": "OCEAN"}, "MODIFIER_TYPE_DENIED"),
                             ({"type": "WAVE", "settings": {"height": "tall"}}, "RNA_TYPE_MISMATCH"),
                             ({"type": "WAVE", "settings": {"texture_coords_object": {"object": handle(cube())}}}, "own object")):
        try:
            job, result = run(program([op("modifier_add", cube(), **parameters)], writes=[cube()]))
            refused = failed_with(result, code)
        except ValueError as error:
            refused = code in str(error)
        check("generic_modifier_refused_" + code.split()[0].lower(), refused and no_stages())


def constraints():
    bpy.ops.ed.undo_push(message="generic constraints QA")
    queries.invalidate()
    camera = window.scene.objects["Camera"]
    job, _ = run(program([op("constraint_add", cube(), type="LIMIT_DISTANCE", name="QA Distance",
                             settings={"target": {"object": handle(camera)}, "distance": 2, "limit_mode": "LIMITDIST_OUTSIDE"})],
                         writes=[cube()], reads=[camera]))
    check("generic_constraint_commits", commit(job) and passed(job))
    constraint = cube().constraints["QA Distance"]
    check("generic_constraint_values_live", constraint.target == window.scene.objects["Camera"]
          and abs(constraint.distance - 2) < 1e-6 and constraint.limit_mode == "LIMITDIST_OUTSIDE")
    check("generic_constraint_object_stays_bindable", revision_exact(cube()))
    job, _ = run(program([op("constraint_configure", cube(), constraint="QA Distance", influence=.5, settings={"distance": 3})],
                         writes=[cube()], reads=[window.scene.objects["Camera"]]))
    check("constraint_configure_commits", commit(job) and passed(job)
          and abs(cube().constraints["QA Distance"].influence - .5) < 1e-6 and abs(cube().constraints["QA Distance"].distance - 3) < 1e-6)
    bpy.ops.ed.undo()
    bpy.ops.ed.undo()
    queries.invalidate()
    check("generic_constraints_undo", not len(cube().constraints) and no_stages())


def force_field():
    bpy.ops.ed.undo_push(message="force field QA")
    queries.invalidate()
    empty = bpy.data.objects.new("QA Wind", None)
    window.scene.collection.objects.link(empty)
    window.view_layer.objects.active = empty
    with bpy.context.temp_override(window=window, object=empty, active_object=empty):
        bpy.ops.object.forcefield_toggle()  # Adding a field is reviewed Python; its settings are generic paths.
    queries.invalidate()
    job, _ = run(program([op("property_set", empty, values={"field.type": "WIND", "field.strength": 3})], writes=[empty]))
    wind = window.scene.objects["QA Wind"]
    check("force_field_commits", commit(job) and passed(job) and wind.field.type == "WIND" and abs(wind.field.strength - 3) < 1e-6)
    check("force_field_object_stays_bindable", revision_exact(wind))
    bpy.ops.ed.undo()
    queries.invalidate()


def nodes():
    bpy.ops.ed.undo_push(message="generic nodes QA")
    queries.invalidate()
    job, _ = run(program([op("shader_nodes", cube(), name="QA Voronoi", nodes=[
        {"id": "tex", "type": "ShaderNodeTexVoronoi", "inputs": {"Scale": 4}, "settings": {"feature": "F2"}},
        {"id": "bsdf", "type": "BSDF_PRINCIPLED"}, {"id": "out", "type": "OUTPUT_MATERIAL"}], links=[
        {"from_node": "tex", "from_socket": "Color", "to_node": "bsdf", "to_socket": "Base Color"},
        {"from_node": "bsdf", "from_socket": "BSDF", "to_node": "out", "to_socket": "Surface"}])], writes=[cube()]))
    check("generic_shader_nodes_commit", commit(job) and passed(job))
    material = next(slot.material for slot in cube().material_slots if slot.material and slot.material.name == "QA Voronoi")
    voronoi = material.node_tree.nodes["tex"]
    check("generic_shader_node_values_live", voronoi.bl_idname == "ShaderNodeTexVoronoi" and voronoi.feature == "F2"
          and abs(voronoi.inputs["Scale"].default_value - 4) < 1e-6 and voronoi.outputs["Color"].is_linked)
    job, _ = run(program([op("geometry_nodes", cube(), name="QA Triangulate", nodes=[
        {"id": "in", "type": "NodeGroupInput"}, {"id": "tri", "type": "GeometryNodeTriangulate"},
        {"id": "out", "type": "NodeGroupOutput"}], links=[
        {"from_node": "in", "from_socket": "Geometry", "to_node": "tri", "to_socket": "Mesh"},
        {"from_node": "tri", "from_socket": "Mesh", "to_node": "out", "to_socket": "Geometry"}])], writes=[cube()]))
    check("generic_geometry_nodes_commit", commit(job))
    evaluated = cube().evaluated_get(bpy.context.evaluated_depsgraph_get())
    check("generic_geometry_nodes_evaluate", len(evaluated.data.polygons) == 12)
    check("generic_geometry_object_stays_bindable", revision_exact(cube()))
    bpy.ops.ed.undo()
    bpy.ops.ed.undo()
    queries.invalidate()
    job, result = run(program([op("geometry_nodes", cube(), name="QA Huge", nodes=[
        {"id": "grid", "type": "GeometryNodeMeshGrid", "inputs": {"Vertices X": 4000, "Vertices Y": 4000}},
        {"id": "out", "type": "NodeGroupOutput"}], links=[
        {"from_node": "grid", "from_socket": "Mesh", "to_node": "out", "to_socket": "Geometry"}])], writes=[cube()]))
    check("generic_geometry_over_budget_refused", failed_with(result, "budget") and no_stages())
    described = api_lookup.execute({"domain": "WRITABLE", "name": "WaveModifier", "member": "", "offset": 0, "limit": 64})
    check("writable_describes_settings", any(item["path"] == "height" for item in described["items"])
          and not any("texture" == item["path"] for item in described["items"]))


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    queries.invalidate()
    modifiers()
    constraints()
    force_field()
    nodes()
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "generic-settings.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_GENERIC_SETTINGS", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
