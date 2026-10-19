# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""operator_run safety rules on real operators: admission and accounting refusals, bounds, commit fidelity.

* Every installed-RNA and accounting refusal on a real operator that breaks
  it (the allow-list is patched for the case): PYTHON_OPERATOR, INTERACTIVE,
  NO_UNDO, DATA_PROPERTY, FILE_PROPERTY, FREED_DATA, NEW_DATABLOCKS,
  DATA_REPLACED and UNCOMMITTED_STATE, each leaving no Main residue.
* Bounds before work: bone growth (superlinear naming) and the work bound of
  superlinear mesh operators, refused at once; a small selection still runs.
* Commit fidelity through real programs: a stale shape-key display flag on a
  curve, pose data an armature operator copies (refused when commit cannot
  carry it, committed through the pose family when it can) and live children
  or bones a reindexing operator would detach.

Run only in a disposable instance with the exact Angelo binary: ``--background
--factory-startup --disable-autoexec --python-exit-code 1``. No provider or
paid call.
"""

import json
import os
import random
import sys
import tempfile
import time
import traceback
from pathlib import Path
from unittest.mock import patch

import bmesh
import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-operator-run-safety"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import action_operator_spec as spec, action_operators, native_programs as native  # noqa: E402
from angelo_agent import scene_queries as queries  # noqa: E402
from angelo_agent.action_kernels import StagedActionContext, apply_operation  # noqa: E402
from angelo_agent.program_dependencies import _fingerprint  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import commit_operator, no_stages  # noqa: E402

report = {"checks": {}, "timings": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path}
window = bpy.context.window_manager.windows[0]


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def id_count():
    return sum(len(getattr(bpy.data, prop.identifier)) for prop in bpy.data.bl_rna.properties
               if prop.type == "COLLECTION" and prop.identifier != "all_ids")


def scratch():
    scene = bpy.data.scenes.new("QA Safety Stage")
    collection = bpy.data.collections.new("QA Safety Stage Objects")
    scene.collection.children.link(collection)
    return StagedActionContext(scene, collection)


def staged(context, kind, params):
    apply_operation(context, {"kind": kind, "target": None, "parameters": params})
    return context.resolve("$" + params["alias"])


def refusal(context, target, operator, label, **parameters):
    """Run one operator on a staged target; return its refusal code (or OK) and record the time."""
    started = time.perf_counter()
    try:
        apply_operation(context, {"kind": "operator_run", "target": target, "parameters": dict(operator=operator, **parameters)})
        code = "OK"
    except ValueError as error:
        code = str(error).split(":")[0].replace("operator_run ", "")
    report["timings"][label] = round(time.perf_counter() - started, 4)
    report.setdefault("codes", {})[label] = code
    return code


def discard(context):
    assert all(obj.mode == "OBJECT" for obj in context.collection.objects)
    bpy.data.batch_remove(ids=list(set(context.owned_ids) | set(context.created_ids)))


class QAPythonOperator(bpy.types.Operator):
    bl_idname = "mesh.angelo_qa_python"
    bl_label = "Angelo QA Python operator"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, _context):
        return {"FINISHED"}


def admitted(*names, targets=None, undeny=()):
    """Patch the allow-list (and object targets) for one case; the RNA rules still run."""
    patches = [patch.dict(spec.ALLOWED, {name: (1, {}) for name in names}),
               patch.dict(spec.OBJECT_TARGETS, {name: targets for name in names if targets}),
               patch.dict(spec.DENIED, {key: value for key, value in spec.DENIED.items() if key not in undeny}, clear=True)]
    for item in patches:
        item.start()
    return patches


def rule_refusals():
    """Each runtime refusal on a real operator that breaks its rule, without residue."""
    ids, live = id_count(), {obj.name: _fingerprint(obj) for obj in bpy.data.objects}
    bpy.utils.register_class(QAPythonOperator)
    cases = {"PYTHON_OPERATOR": ("mesh.angelo_qa_python", None), "INTERACTIVE": ("mesh.bevel", None),
             "NO_UNDO": ("mesh.duplicate", None), "DATA_PROPERTY": ("uv.stitch", None),
             "FILE_PROPERTY": ("object.empty_image_add", ("MESH",))}
    for code, (operator, targets) in cases.items():
        context = scratch()
        staged(context, "create_primitive", {"primitive": "CUBE", "alias": "mesh"})
        patches = admitted(operator, targets=targets)
        try:
            check("refused_" + code, refusal(context, "$mesh", operator, code) == code)
        finally:
            for item in patches:
                item.stop()
            discard(context)
    bpy.utils.unregister_class(QAPythonOperator)
    # FREED_DATA: removing every shape key frees the Key; the known-path guard is lifted to reach the accounting.
    context = scratch()
    mesh = staged(context, "create_primitive", {"primitive": "CUBE", "alias": "mesh"})
    mesh.shape_key_add(name="Basis")
    context.track_id(mesh.data.shape_keys)
    with patch.object(action_operators, "_guard_frees", lambda *_args: None):
        check("refused_FREED_DATA", refusal(context, "$mesh", "object.shape_key_remove", "FREED_DATA",
                                            properties={"all": True}) == "FREED_DATA")
    discard(context)
    # NEW_DATABLOCKS: a particle system adds ParticleSettings (typed kinds own that commit).
    context = scratch()
    staged(context, "create_primitive", {"primitive": "CUBE", "alias": "mesh"})
    patches = admitted("object.particle_system_add", targets=("MESH",), undeny=("object.particle_system_add",))
    try:
        check("refused_NEW_DATABLOCKS", refusal(context, "$mesh", "object.particle_system_add", "NEW_DATABLOCKS")
              == "NEW_DATABLOCKS")
    finally:
        for item in patches:
            item.stop()
        discard(context)
    # DATA_REPLACED: an operator that leaves the object on other (existing, stage-owned) data.
    context = scratch()
    mesh = staged(context, "create_primitive", {"primitive": "CUBE", "alias": "mesh"})
    spare = context.track_id(bpy.data.meshes.new("QA Spare"))
    find = action_operators.operator

    def replacing(idname):
        call, rna = find(idname)

        class Replacing:
            def __getattr__(self, name):
                return getattr(call, name)

            def __call__(self, *args, **kwargs):
                result = call(*args, **kwargs)
                mesh.data = spare
                return result
        return Replacing(), rna

    with patch.object(action_operators, "operator", replacing):
        check("refused_DATA_REPLACED", refusal(context, "$mesh", "object.shade_smooth", "DATA_REPLACED") == "DATA_REPLACED")
    discard(context)
    # UNCOMMITTED_STATE: Shade Flat/Smooth remove a "Smooth by Angle" modifier on the stage only. Objects using that
    # asset have no exact revision today, so the operator is wrapped to remove a modifier the same way.
    context = scratch()
    mesh = staged(context, "create_primitive", {"primitive": "CUBE", "alias": "mesh"})
    mesh.modifiers.new("QA Weighted", "WEIGHTED_NORMAL")

    def removing(idname):
        call, rna = find(idname)

        class Removing:
            def __getattr__(self, name):
                return getattr(call, name)

            def __call__(self, *args, **kwargs):
                result = call(*args, **kwargs)
                mesh.modifiers.remove(mesh.modifiers[0])
                return result
        return Removing(), rna

    with patch.object(action_operators, "operator", removing):
        check("refused_UNCOMMITTED_STATE", refusal(context, "$mesh", "object.shade_flat", "UNCOMMITTED_STATE")
              == "UNCOMMITTED_STATE")
    discard(context)
    check("rule_refusals_leave_no_ids_or_live_change", id_count() == ids and
          {obj.name: _fingerprint(obj) for obj in bpy.data.objects} == live)


def bounds():
    """Superlinear growth and work are refused before the operator runs."""
    context = scratch()
    rig = staged(context, "armature_create", {"alias": "rig", "bones": [
        {"name": "B%02d" % index, "head": [index, 0, 0], "tail": [index, 0, 1]} for index in range(32)]})
    check("bone_growth_refused_before_running", refusal(context, "$rig", "armature.subdivide", "subdivide_32_bones_32_cuts",
                                                        properties={"number_cuts": 32}) == "GROWTH_BOUND"
          and report["timings"]["subdivide_32_bones_32_cuts"] < .5 and len(rig.data.bones) == 32)
    check("bone_growth_within_bound_runs", refusal(context, "$rig", "armature.subdivide", "subdivide_32_bones_3_cuts",
                                                   properties={"number_cuts": 3}) == "OK" and len(rig.data.bones) == 128)
    discard(context)
    context = scratch()
    mesh = staged(context, "create_primitive", {"primitive": "PLANE", "alias": "wire"})
    tree, rng = bmesh.new(), random.Random(7)
    verts = [tree.verts.new((rng.random() * 10, rng.random() * 10, rng.random())) for _ in range(20000)]
    for index in range(1, len(verts)):
        tree.edges.new((verts[index], verts[rng.randrange(index)]))
    tree.to_mesh(mesh.data)
    tree.free()
    check("superlinear_work_refused_before_running", refusal(context, "$wire", "mesh.edge_face_add", "edge_face_add_wire_all")
          == "WORK_BOUND" and report["timings"]["edge_face_add_wire_all"] < .5)
    # Two unconnected vertices of the same 40,000-element tree: an edge, well within the bound.
    neighbours = {index for edge in mesh.data.edges if 0 in edge.vertices for index in edge.vertices}
    other = next(index for index in range(1, 20000) if index not in neighbours)
    check("small_selection_on_large_mesh_runs", refusal(context, "$wire", "mesh.edge_face_add", "edge_face_add_wire_pair",
                                                        select="INDICES", vertex_indices=[0, other]) == "OK"
          and len(mesh.data.edges) == 20000)
    discard(context)


def objects():
    return window.scene.objects


def binding(obj):
    return {"handle": queries.object_handle(obj), "revision": queries.object_revision(window, obj)}


def program(target, operator, mode="APPLY", reads=(), **parameters):
    return validate_call("angelo_scene_program", {
        "mode": mode, "read_set": [binding(obj) for obj in reads], "write_set": [binding(target)],
        "operations": [{"kind": "operator_run", "target": queries.object_handle(target),
                        "parameters": dict(operator=operator, **parameters)}]})


def settle(job):
    for _ in range(400):
        result = job.poll()
        if result.get("final") or result.get("ready_to_commit") or result.get("status") == "preview":
            return result
    raise AssertionError("cooperative native program did not settle")


def run(arguments):
    job = native.begin(window, "angelo_scene_program", arguments)
    result = settle(job)
    if result.get("ready_to_commit"):
        committed = "FINISHED" in bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
        return job, committed and job.result.get("status") == "committed", None
    return job, False, result.get("error") or job.result.get("error") or ""


def undo():
    bpy.ops.ed.undo()
    queries.invalidate()


def stale_curve_flag():
    """A manual shape-key pin on a curve between preview and accept makes the plan stale, never reverted."""
    curve = bpy.data.objects.new("QA Safety Curve", bpy.data.curves.new("QA Safety Curve", "CURVE"))
    curve.data.splines.new("BEZIER").bezier_points.add(1)
    curve.data.splines[0].bezier_points[1].co = (2, 0, 0)
    window.scene.collection.objects.link(curve)
    bpy.ops.ed.undo_push(message="operator_run safety curve")
    queries.invalidate()
    result = settle(native.begin(window, "angelo_scene_program", program(curve, "curve.subdivide", mode="PREVIEW")))
    check("curve_preview_retained", result.get("status") == "preview" and no_stages())
    curve.show_only_shape_key = True  # The artist's change while the preview is shown.
    accepted = native.begin(window, "angelo_accept_preview", {"preview_id": result["preview_id"]})
    outcome = settle(accepted)
    if outcome.get("ready_to_commit"):
        bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=accepted.id)
    curve = objects()["QA Safety Curve"]
    check("manual_curve_shape_key_pin_makes_accept_stale", not outcome.get("ready_to_commit")
          and curve.show_only_shape_key and len(curve.data.splines[0].bezier_points) == 2 and no_stages())
    curve.show_only_shape_key = False


def armature_pose_data():
    """Pose data an armature operator copies commits through the pose family, or is refused."""
    rig = bpy.data.objects.new("QA Safety Rig", bpy.data.armatures.new("QA Safety Rig"))
    window.scene.collection.objects.link(rig)
    window.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    for name, head in (("Arm.L", (1, 0, 0)), ("Hand.L", (2, 0, 0))):
        bone = rig.data.edit_bones.new(name)
        bone.head, bone.tail = head, (head[0] + 1, 0, 0)
    rig.data.edit_bones["Hand.L"].parent = rig.data.edit_bones["Arm.L"]
    bpy.ops.object.mode_set(mode="OBJECT")
    rig.pose.bones["Arm.L"].rotation_mode = "XYZ"
    bpy.ops.ed.undo_push(message="operator_run safety rig")
    queries.invalidate()
    job, committed, error = run(program(rig, "armature.symmetrize"))
    mirrored = objects()["QA Safety Rig"].pose.bones.get("Arm.R")
    check("mirrored_bone_commits_its_copied_pose_settings", committed and mirrored is not None
          and mirrored.rotation_mode == "XYZ")
    undo()
    rig = objects()["QA Safety Rig"]
    rig.pose.bones["Arm.L"].constraints.new("LIMIT_ROTATION")
    rig.pose.bones["Arm.L"]["qa_weight"] = 2
    queries.invalidate()
    before = _fingerprint(rig)
    for operator in ("armature.duplicate", "armature.symmetrize"):
        job, committed, error = run(program(objects()["QA Safety Rig"], operator))
        check(operator.split(".")[1] + "_of_constrained_bones_refused", not committed and "POSE_DATA" in error
              and _fingerprint(objects()["QA Safety Rig"]) == before and no_stages())


def reindexed_dependents():
    """Live children and bones that a reindexing commit would detach make the program fail before any edit."""
    cube = objects()["Cube"]
    child = bpy.data.objects.new("QA Vertex Child", None)
    window.scene.collection.objects.link(child)
    child.parent, child.parent_type, child.parent_vertices = cube, "VERTEX", (0, 0, 0)
    window.view_layer.update()
    where = tuple(child.matrix_world.translation)
    bpy.ops.ed.undo_push(message="operator_run safety vertex child")
    queries.invalidate()
    job, committed, error = run(program(cube, "mesh.sort_elements", properties={"type": "REVERSE", "elements": ["VERT"]}))
    window.view_layer.update()
    check("vertex_parent_reindex_refused", not committed and "REINDEXED_DEPENDENTS" in error
          and tuple(objects()["QA Vertex Child"].matrix_world.translation) == where and no_stages())
    rig = objects()["QA Safety Rig"]
    prop = bpy.data.objects.new("QA Bone Child", None)
    window.scene.collection.objects.link(prop)
    prop.parent, prop.parent_type, prop.parent_bone = rig, "BONE", "Hand.L"
    bpy.ops.ed.undo_push(message="operator_run safety bone child")
    queries.invalidate()
    job, committed, error = run(program(rig, "armature.delete"))  # Every bone, Hand.L with it.
    check("bone_parent_delete_refused", not committed and "REINDEXED_DEPENDENTS" in error
          and "Hand.L" in objects()["QA Safety Rig"].data.bones and no_stages())


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    queries.invalidate()
    rule_refusals()
    bounds()
    stale_curve_flag()
    armature_pose_data()
    reindexed_dependents()
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "operator-run-safety.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_OPERATOR_RUN_SAFETY", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
