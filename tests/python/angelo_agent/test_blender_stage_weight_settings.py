# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A staged operator reads the person's vertex group settings, not a new scene's defaults.

``operator_run`` runs in a stage scene, and Blender 5.3 turned Auto Normalize on in a new scene's defaults: an Assign on
the stage zeroed another bone's weights that Blender itself keeps. Each row runs one operator on the stage and directly
and compares the weights. Copying those settings onto the stage scene never updates the person's objects (their RNA
updates act on the context's active object). Background, local only; writes results.json under ANGELO_QA_DIR.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import os
import sys
import tempfile
from pathlib import Path

import bpy

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
try:
    from angelo_agent import native_programs as native, scene_queries as queries
except ImportError:   # A stock Blender run from the checkout.
    sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
    from angelo_agent import native_programs as native, scene_queries as queries
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import commit_operator  # noqa: E402

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-stage-weight-settings-qa"))
OUT.mkdir(parents=True, exist_ok=True)
window = bpy.context.window_manager.windows[0]
checks, observed, staging_updates = {}, {}, {}


def check(name, direct_weights, staged_weights):
    checks[name] = direct_weights == staged_weights
    observed[name] = {"direct": direct_weights, "operator_run": staged_weights}


qa = {}
bpy.utils.register_class(commit_operator(qa))


def settle(job):
    for _ in range(400):
        result = job.poll()
        if result.get("final") or result.get("ready_to_commit"):
            return result
    raise AssertionError


def scene_with_rig(name):
    scene = window.scene
    rig = bpy.data.objects.new(name + "Rig", bpy.data.armatures.new(name + "Rig"))
    scene.collection.objects.link(rig)
    window.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    for bone_name, x in (("A", 0.0), ("B", 1.0)):
        bone = rig.data.edit_bones.new(bone_name)
        bone.head, bone.tail = (x, 0, 0), (x, 0, 1)
    bpy.ops.object.mode_set(mode='OBJECT')
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    obj = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(obj)
    a = obj.vertex_groups.new(name="A")
    b = obj.vertex_groups.new(name="B")
    a.add([0, 1, 2, 3], 1.0, 'REPLACE')
    b.add([0, 1], 0.5, 'REPLACE')   # Something for Remove from Group to take away.
    obj.vertex_groups.active_index = b.index
    modifier = obj.modifiers.new("Armature", 'ARMATURE')
    modifier.object = rig
    bpy.context.view_layer.update()
    return obj, rig


def weights(obj):
    return {group.name: [round(group.weight(i), 4) if any(g.group == group.index for g in obj.data.vertices[i].groups) else None for i in range(4)] for group in obj.vertex_groups}


def direct(obj, operator):
    window.view_layer.objects.active = obj
    obj.select_set(True)
    with bpy.context.temp_override(window=window, object=obj, active_object=obj, selected_objects=[obj]):
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        getattr(bpy.ops.object, operator)()
        bpy.ops.object.mode_set(mode='OBJECT')
    return weights(obj)


def persons_updates():
    """The person's objects the next update of the person's view layer evaluates again (geometry)."""
    scene, updated = window.scene, []
    def post(update_scene, depsgraph):
        if update_scene == scene:
            updated.extend(update.id.original.name for update in depsgraph.updates
                           if isinstance(update.id, bpy.types.Object) and update.is_updated_geometry)
    bpy.app.handlers.depsgraph_update_post.append(post)
    try:
        window.view_layer.update()
    finally:
        bpy.app.handlers.depsgraph_update_post.remove(post)
    return sorted(updated)


def staged(obj, operator):
    queries.invalidate()
    binding = {"handle": queries.object_handle(obj), "revision": queries.object_revision(window, obj)}
    rig = obj.modifiers["Armature"].object
    reads = [{"handle": queries.object_handle(rig), "revision": queries.object_revision(window, rig)}]
    program = validate_call("angelo_scene_program", {"mode": "APPLY", "read_set": reads, "write_set": [binding], "operations": [
        {"kind": "operator_run", "target": queries.object_handle(obj), "parameters": {"operator": "object." + operator, "select": "ALL"}}]})
    window.view_layer.update()
    job = native.begin(window, "angelo_scene_program", program)
    result = settle(job)
    # Copying the person's settings onto the stage scene runs their RNA updates, which tag the context's active
    # object (rna_Scene_update_active_object_data): staging itself never reaches the person's data.
    staging_updates[operator + "_" + obj.name] = persons_updates()
    if not result.get("ready_to_commit"):
        return {"refused": result.get("error")}
    status = bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    return weights(bpy.data.objects[obj.name]) if job.result.get("status") == "committed" else {"failed": job.result.get("error")}


settings = window.scene.tool_settings
for normalize in (False, True):
    settings.use_auto_normalize = normalize
    settings.vertex_group_weight = 1.0
    for operator in ("vertex_group_assign", "vertex_group_remove_from"):
        one, _rig = scene_with_rig("D%s%s" % (normalize, operator[-3:]))
        two, _rig = scene_with_rig("S%s%s" % (normalize, operator[-3:]))
        check("%s_with_auto_normalize_%s" % (operator, normalize), direct(one, operator), staged(two, operator))
settings.use_auto_normalize = False
settings.vertex_group_weight = 0.25
one, _rig = scene_with_rig("Dw")
two, _rig = scene_with_rig("Sw")
check("the_persons_assign_weight_reaches_the_stage", direct(one, "vertex_group_assign"),
      staged(two, "vertex_group_assign"))
checks["staging_never_updates_the_persons_objects"] = bool(staging_updates) and not any(staging_updates.values())
observed["staging_never_updates_the_persons_objects"] = staging_updates
report = {"success": bool(checks) and all(checks.values()), "checks": checks, "observed": observed}
(OUT / "results.json").write_text(json.dumps(report, indent=2))
print("ANGELO_STAGE_WEIGHT_SETTINGS", json.dumps(report))
