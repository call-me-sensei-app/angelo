# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Regression: a committed edit keeps the names and identities an artist sees.

Staging copies data, Actions, materials, node groups and Worlds, which Blender
names with a suffix. Before the fix, a committed mesh edit renamed the
object's mesh to ``Cube.001`` and its material to ``Material.001``, a first
keyframe named its Action after the private staged copy, and a modifier edit
moved an existing Geometry Nodes modifier onto a ``.001`` group copy. Run only
in a disposable instance with the exact Angelo binary: ``--background
--factory-startup --disable-autoexec --python-exit-code 1``. No provider or paid call.
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
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-commit-names-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries  # noqa: E402
from angelo_agent import scene_settings  # noqa: E402
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


def objects():
    return window.scene.objects


def run(operations, writes=(), scene=False):
    write_set = [{"handle": queries.object_handle(obj), "revision": queries.object_revision(window, obj)} for obj in writes]
    if scene:
        settings = scene_settings.execute(window, {})
        write_set.append({"handle": settings["handle"], "revision": settings["revision"]})
    arguments = validate_call("angelo_scene_program", {"mode": "APPLY", "read_set": [], "write_set": write_set,
                                                       "operations": operations})
    job = native.begin(window, "angelo_scene_program", arguments)
    settle(job)
    result = bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    queries.invalidate()
    report.setdefault("results", []).append(job.result.get("error"))
    return "FINISHED" in result and job.result.get("status") == "committed"


def handle(name):
    return queries.object_handle(objects()[name])


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    queries.invalidate()
    bpy.ops.ed.undo_push(message="names QA baseline")
    queries.invalidate()
    cube = objects()["Cube"]
    check("mesh_edit_commits", run([{"kind": "mesh_extrude", "target": handle("Cube"),
                                     "parameters": {"face_indices": [0], "translation": [0, 0, .5]}}], [cube]))
    cube = objects()["Cube"]
    check("mesh_keeps_its_name", cube.data.name == "Cube" and len(cube.data.vertices) == 12)
    check("material_keeps_its_identity", cube.material_slots[0].material.name == "Material"
          and "Material.001" not in bpy.data.materials)
    check("first_key_action_named_after_the_object", run([{"kind": "keyframe_insert", "target": handle("Cube"), "parameters": {
        "property": "location", "value": [0, 0, 0], "frame": 1}}], [cube]) and cube.animation_data.action.name == "CubeAction")
    check("second_key_keeps_the_action_name", run([{"kind": "keyframe_insert", "target": handle("Cube"), "parameters": {
        "property": "location", "value": [1, 0, 0], "frame": 10}}], [cube]) and cube.animation_data.action.name == "CubeAction")
    camera = objects()["Camera"]
    check("camera_data_keeps_its_name", run([{"kind": "property_set", "target": handle("Camera"), "parameters": {
        "values": {"data.dof.use_dof": True}}}], [camera]) and camera.data.name == "Camera" and camera.data.dof.use_dof)
    scene_handle = scene_settings.execute(window, {})["handle"]
    check("world_keeps_its_name", run([{"kind": "world_configure", "target": scene_handle, "parameters": {"strength": 2}}],
                                      scene=True) and window.scene.world.name == "World")
    # Shared data: the edited object gets a suffixed copy; the other user keeps the original and its name.
    twin = cube.copy()
    twin.name = "Cube Twin"
    window.scene.collection.objects.link(twin)
    queries.invalidate()
    check("shared_edit_commits", run([{"kind": "mesh_inset", "target": handle("Cube Twin"),
                                       "parameters": {"face_indices": [1]}}], [objects()["Cube Twin"]]))
    check("shared_data_keeps_the_original_name", objects()["Cube"].data.name == "Cube"
          and objects()["Cube Twin"].data.name.startswith("Cube.") and objects()["Cube Twin"].data != objects()["Cube"].data)
    # An existing (catalog) Geometry Nodes modifier keeps its node group through a modifier edit.
    graph = {"name": "QA Nodes", "nodes": [{"id": "input", "type": "GROUP_INPUT"},
             {"id": "move", "type": "TRANSFORM_GEOMETRY", "inputs": {"Translation": [0, 0, .1]}},
             {"id": "output", "type": "GROUP_OUTPUT"}],
             "links": [{"from_node": "input", "from_socket": "Geometry", "to_node": "move", "to_socket": "Geometry"},
                       {"from_node": "move", "from_socket": "Geometry", "to_node": "output", "to_socket": "Geometry"}]}
    check("node_modifier_commits", run([{"kind": "geometry_nodes", "target": handle("Cube Twin"), "parameters": graph}],
                                       [objects()["Cube Twin"]]))
    group = objects()["Cube Twin"].modifiers["QA Nodes"].node_group
    groups_before = sorted(item.name for item in bpy.data.node_groups)
    edited = run([{"kind": "modifier_add", "target": handle("Cube Twin"), "parameters": {"type": "BEVEL"}}],
                 [objects()["Cube Twin"]])
    check("node_group_identity_kept", edited and objects()["Cube Twin"].modifiers["QA Nodes"].node_group == group
          and sorted(item.name for item in bpy.data.node_groups) == groups_before)
    for _ in range(8):
        bpy.ops.ed.undo()
    queries.invalidate()
    cube = objects()["Cube"]
    check("undo_restores_names", cube.data.name == "Cube" and len(cube.data.vertices) == 8 and not cube.animation_data
          and objects()["Camera"].data.name == "Camera" and not objects()["Camera"].data.dof.use_dof and no_stages())
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "commit-names.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_COMMIT_NAMES", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
