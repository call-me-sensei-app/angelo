# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every property_set path against the installed RNA, and a staged write of each new family.

For each allow-listed path, on an owner of a fitting type: the RNA property
exists (only Cycles settings may be missing, and then they are reported as
unavailable), has the declared type and array length, is writable, its RNA
hard range contains the declared bounds and its static enum items contain the
declared ones. Then light, camera and render paths commit through a real
program with Undo. Run only in a disposable instance with the exact Angelo
binary: ``--background --factory-startup --disable-autoexec
--python-exit-code 1``. No provider or paid call.
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
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-property-paths-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries, scene_settings  # noqa: E402
from angelo_agent.action_properties import (DYNAMIC, PROPERTY_PATHS, RNA_TYPES, SCENE, holder_and_name,  # noqa: E402
                                            owner_of, value_kind)
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import no_stages, commit_operator  # noqa: E402

report = {"checks": {}, "paths": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path,
          "version": bpy.app.version_string}
window = bpy.context.window_manager.windows[0]


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def owners():
    scene = window.scene
    curve = bpy.data.objects.new("QA Curve", bpy.data.curves.new("QA Curve", "CURVE"))
    curve.data.dimensions = "3D"  # Text stands in for 2D fill modes.
    text = bpy.data.objects.new("QA Text", bpy.data.curves.new("QA Text", "FONT"))
    for obj in (curve, text):
        scene.collection.objects.link(obj)
    return {"MESH": scene.objects["Cube"], "CAMERA": scene.objects["Camera"], "LIGHT": scene.objects["Light"],
            "CURVE": curve, "FONT": text}


def rna_problem(holder, name, path):
    """None when the installed RNA definition admits the declared kind and bounds."""
    prop = holder.bl_rna.properties.get(name)
    if prop is None:
        return "missing"
    _owner, _types, kind, *arguments = PROPERTY_PATHS[path]
    if prop.type != RNA_TYPES[kind] or prop.is_readonly:
        return "type %s readonly %s" % (prop.type, prop.is_readonly)
    if prop.type in {"BOOLEAN", "INT", "FLOAT"} and getattr(prop, "array_length", 0) != (arguments[0] if kind == "floats" else 0):
        return "array length %d" % prop.array_length
    if kind in {"int", "float", "floats"}:
        low, high = arguments[-2:]
        if low < prop.hard_min - 1e-6 * max(1, abs(prop.hard_min)) or high > prop.hard_max + 1e-6 * max(1, abs(prop.hard_max)):
            return "declared %g..%g outside RNA %g..%g" % (low, high, prop.hard_min, prop.hard_max)
    return None


def refused_items(holder, name, path):
    """Declared enum items RNA refuses on this owner, found by assigning each (items can depend on context)."""
    _owner, _types, kind, *arguments = PROPERTY_PATHS[path]
    if kind != "enum" or arguments[0] is DYNAMIC:
        return set()
    original, refused = getattr(holder, name), set()
    for item in arguments[0]:
        try:
            setattr(holder, name, item)
        except TypeError:
            refused.add(item)
    setattr(holder, name, original)
    return refused


def every_path_matches_rna(objects):
    problems, unavailable = {}, []
    # Build-gated items: an engine this build does not compile in.
    gated = {"render.engine": set() if hasattr(window.scene, "cycles") else {"CYCLES"}}
    for path in PROPERTY_PATHS:
        refused = None
        if owner_of(path) == SCENE:
            candidates = [window.scene]
        else:
            candidates = [objects[kind] for kind in (PROPERTY_PATHS[path][1] or ("MESH",))]
        for owner in candidates:
            try:
                holder, name = holder_and_name(owner, path)
            except (AttributeError, ValueError):  # ValueError: PropertyPathError for a missing struct.
                unavailable.append(path)
                continue
            problem = rna_problem(holder, name, path)
            if problem == "missing":
                unavailable.append(path)
            elif problem:
                problems[path] = problem
            else:  # An item counts when RNA admits it on any fitting owner (a 2D curve or text, a 3D curve).
                items = refused_items(holder, name, path)
                refused = items if refused is None else refused & items
        if refused and refused - gated.get(path, set()):
            problems[path] = "enum items refused by RNA: %s" % sorted(refused - gated.get(path, set()))
    report["paths"] = {"count": len(PROPERTY_PATHS), "problems": problems, "unavailable": sorted(set(unavailable))}
    check("every_path_matches_installed_rna", not problems)
    expected_missing = {path for path in PROPERTY_PATHS if path.startswith("cycles.")} if not hasattr(window.scene, "cycles") else set()
    check("only_build_gated_paths_are_missing", set(unavailable) == expected_missing)
    listed = scene_settings.execute(window, {})["unavailable_in_build"]
    check("scene_settings_lists_missing_paths", set(listed) == {path for path in expected_missing if owner_of(path) == SCENE})


def settle(job):
    for _ in range(200):
        result = job.poll()
        if result.get("final") or result.get("ready_to_commit"):
            return result
    raise AssertionError("cooperative native program did not settle")


def commit(job):
    result = bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    return "FINISHED" in result and job.result.get("status") == "committed"


def run(writes, operations):
    arguments = validate_call("angelo_scene_program", {"mode": "APPLY", "write_set": writes, "operations": operations})
    job = native.begin(window, "angelo_scene_program", arguments)
    settle(job)
    return job


def binding(obj):
    return {"handle": queries.object_handle(obj), "revision": queries.object_revision(window, obj)}


def new_families():
    bpy.ops.ed.undo_push(message="property paths QA")
    queries.invalidate()
    light, camera = window.scene.objects["Light"], window.scene.objects["Camera"]
    job = run([binding(light), binding(camera)], [
        {"kind": "property_set", "target": queries.object_handle(light),
         "parameters": {"values": {"data.use_temperature": True, "data.temperature": 3200.0, "data.diffuse_factor": .5}}},
        {"kind": "property_set", "target": queries.object_handle(camera),
         "parameters": {"values": {"data.lens_unit": "FOV", "data.passepartout_alpha": .25}}}])
    committed = commit(job)
    check("light_and_camera_paths_commit", committed and bool(job.result.get("checks"))
          and all(row.get("passed") for row in job.result["checks"]))
    light = window.scene.objects["Light"]
    check("light_data_swapped_with_values", light.data.use_temperature and abs(light.data.temperature - 3200) < 1e-3
          and light.data.name == "Light" and window.scene.objects["Camera"].data.lens_unit == "FOV")
    settings = scene_settings.execute(window, {})
    job = run([{"handle": settings["handle"], "revision": settings["revision"]}], [
        {"kind": "property_set", "target": settings["handle"],
         "parameters": {"values": {"eevee.volumetric_samples": 32, "render.use_border": True, "gravity": [0, 0, -3.7]}}}])
    check("render_paths_commit", commit(job) and window.scene.eevee.volumetric_samples == 32
          and window.scene.render.use_border and abs(window.scene.gravity[2] + 3.7) < 1e-6)
    bpy.ops.ed.undo()
    bpy.ops.ed.undo()
    queries.invalidate()
    check("two_undos_restore_everything", not window.scene.render.use_border
          and not window.scene.objects["Light"].data.use_temperature and no_stages())


def failed_with(job, code):
    result = job.result or {}
    return result.get("status") == "failed" and code in (result.get("error") or "")


def generic_paths():
    """Settings outside the common list, through every family, with read-back after commit."""
    bpy.ops.ed.undo_push(message="generic paths QA")
    queries.invalidate()
    cube, camera = window.scene.objects["Cube"], window.scene.objects["Camera"]
    world_before = window.scene.world.mist_settings.start
    job = run([binding(cube), binding(camera)], [
        {"kind": "property_set", "target": queries.object_handle(cube), "parameters": {"values": {
            "display.show_shadows": False, "data.remesh_voxel_size": .25, "active_material.roughness": .2,
            "active_material.diffuse_color": [.1, .2, .3, 1]}}},
        {"kind": "property_set", "target": queries.object_handle(camera), "parameters": {"values": {
            "data.dof.focus_object": {"object": queries.object_handle(cube)}, "data.sensor_fit": "VERTICAL"}}}])
    committed = commit(job)
    check("generic_object_data_material_commit", committed and all(row.get("passed") for row in job.result["checks"]))
    cube, camera = window.scene.objects["Cube"], window.scene.objects["Camera"]
    check("generic_values_read_back_live", not cube.display.show_shadows and abs(cube.data.remesh_voxel_size - .25) < 1e-6
          and abs(cube.active_material.roughness - .2) < 1e-6 and cube.active_material.name == "Material"
          and camera.data.dof.focus_object == cube and camera.data.sensor_fit == "VERTICAL")
    settings = scene_settings.execute(window, {})
    job = run([{"handle": settings["handle"], "revision": settings["revision"]}], [
        {"kind": "property_set", "target": settings["handle"], "parameters": {"values": {
            "world.mist_settings.start": 3.5, "render.use_single_layer": True, "eevee.gi_diffuse_bounces": 2}}}])
    check("generic_scene_and_world_commit", commit(job) and window.scene.world.mist_settings.start == 3.5
          and window.scene.render.use_single_layer and window.scene.eevee.gi_diffuse_bounces == 2
          and window.scene.world.name == "World")
    bpy.ops.ed.undo()
    bpy.ops.ed.undo()
    queries.invalidate()
    check("generic_undo_restores", window.scene.objects["Cube"].display.show_shadows
          and window.scene.world.mist_settings.start == world_before and no_stages())
    cube = window.scene.objects["Cube"]
    bpy.types.Mesh.qa_runtime_setting = bpy.props.IntProperty(update=lambda self, context: None)  # Python-defined.
    for values, code in (({"data.qa_runtime_setting": 1}, "RUNTIME_PROPERTY"),
                         ({"data.vertices": 1}, "NOT_A_SETTING"), ({"data.no_such_setting": 1}, "PROPERTY_MISSING_IN_BUILD"),
                         ({"active_material.roughness": 7}, "RNA_RANGE"), ({"display.show_shadows": 1}, "RNA_TYPE_MISMATCH")):
        job = run([binding(cube)], [{"kind": "property_set", "target": queries.object_handle(cube),
                                     "parameters": {"values": values}}])
        check("generic_refused_" + code.lower(), failed_with(job, code) and no_stages())
    job = run([binding(cube)], [{"kind": "property_set", "target": queries.object_handle(cube),
                                 "parameters": {"values": {"active_material.metallic": .7}}}])
    cube.active_material.metallic = .3  # A manual edit while the program waits: the object revision covers it.
    check("generic_material_edit_makes_plan_stale", not commit(job) and abs(window.scene.objects["Cube"].active_material.metallic - .3) < 1e-6)
    queries.invalidate()
    settings = scene_settings.execute(window, {})
    job = run([{"handle": settings["handle"], "revision": settings["revision"]}], [
        {"kind": "property_set", "target": settings["handle"], "parameters": {"values": {"render.use_single_layer": True}}}])
    # A generic scene path is in the settings revision too: commit's verification refuses
    # it before the recorded live value (program_preimages) is even compared.
    window.scene.render.use_single_layer = True
    committed = commit(job)
    report["generic_stale_setting"] = {key: job.result.get(key) for key in ("status", "error", "error_code")}
    check("generic_stale_setting_refused", not committed and job.result.get("error_code") == "STALE_SETTING")


try:
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    queries.invalidate()
    every_path_matches_rna(owners())
    new_families()
    generic_paths()
    report["success"] = True
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "property-paths.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_PROPERTY_PATHS", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
