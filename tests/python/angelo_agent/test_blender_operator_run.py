# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""operator_run through real staged programs, and every admitted operator on private copies.

Programs are normalized by ``tool_specs.validate_call`` as a model's plan is.
Covers Edit-mode mesh, curve, armature, lattice and UV operators and the
object subset (vertex groups, material slots, shape keys, shading, force
field), Grease Pencil objects and layers, PREVIEW and accept, stale revisions of state only the new revision
field covers, suspend and resume between operations, the refusals, one-step
Undo and Redo, and a smoke run of every admitted operator on fresh staged
copies that must leave the artist's objects and Main untouched. Run only in a
disposable instance with the exact Angelo binary: ``--background
--factory-startup --disable-autoexec --python-exit-code 1``. No provider or
paid call.
"""

import json
import os
import statistics
import sys
import tempfile
import time
import traceback
from pathlib import Path
from unittest.mock import patch

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-operator-run-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import action_operators, native_programs as native, scene_queries as queries  # noqa: E402
from angelo_agent.action_kernels import StagedActionContext, apply_operation  # noqa: E402
from angelo_agent.action_operator_spec import ALLOWED, target_types  # noqa: E402
from angelo_agent.derived_geometry import keeps_geometry  # noqa: E402
from angelo_agent.program_dependencies import _fingerprint  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import commit_operator, no_stages, spent_budget  # noqa: E402

report = {"checks": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path, "version": bpy.app.version_string}
window = bpy.context.window_manager.windows[0]


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def settle(job):
    for _ in range(400):
        result = job.poll()
        if result.get("final") or result.get("ready_to_commit") or result.get("status") == "preview":
            return result
    raise AssertionError("cooperative native program did not settle")


def commit(job):
    result = bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    report.setdefault("results", []).append({key: job.result.get(key) for key in ("status", "error")})
    return "FINISHED" in result and job.result.get("status") == "committed"


def objects():
    return window.scene.objects


def handle(obj):
    return queries.object_handle(obj)


def binding(obj):
    return {"handle": handle(obj), "revision": queries.object_revision(window, obj)}


def program(operations, writes=(), mode="APPLY"):
    return validate_call("angelo_scene_program", {"mode": mode, "read_set": [], "operations": operations,
                                                  "write_set": [binding(obj) for obj in writes]})


def run_op(target, operator, **parameters):
    return {"kind": "operator_run", "target": handle(target), "parameters": dict(operator=operator, **parameters)}


def run(arguments):
    job = native.begin(window, "angelo_scene_program", arguments)
    return job, settle(job)


def failed_with(result, code):
    return result.get("status") == "failed" and code in (result.get("error") or "")


def undo():
    bpy.ops.ed.undo()
    queries.invalidate()


def redo():
    bpy.ops.ed.redo()
    queries.invalidate()


def setup():
    cube = objects()["Cube"]
    cube.data.materials.append(bpy.data.materials.new("QA Second"))
    curve_data = bpy.data.curves.new("QA Curve", "CURVE")
    curve_data.splines.new("BEZIER").bezier_points.add(1)
    curve_data.splines[0].bezier_points[1].co = (2, 0, 0)
    window.scene.collection.objects.link(bpy.data.objects.new("QA Curve", curve_data))
    rig = bpy.data.objects.new("QA Rig", bpy.data.armatures.new("QA Rig"))
    window.scene.collection.objects.link(rig)
    window.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    bone = rig.data.edit_bones.new("Root")
    bone.head, bone.tail, bone.select, bone.select_head, bone.select_tail = (0, 0, 0), (0, 0, 1), True, True, True
    bpy.ops.object.mode_set(mode="OBJECT")
    rig.pose.bones["Root"].location = (1, 2, 3)
    rig.pose.bones["Root"].select = False
    lattice = bpy.data.objects.new("QA Lattice", bpy.data.lattices.new("QA Lattice"))
    lattice.data.points[0].co_deform = (-1, -1, -1)
    window.scene.collection.objects.link(lattice)
    window.scene.collection.objects.link(bpy.data.objects.new("QA Empty", None))
    window.view_layer.objects.active = cube
    bpy.ops.ed.undo_push(message="operator_run QA")
    queries.invalidate()


def mesh_operators():
    cube = objects()["Cube"]
    before = _fingerprint(cube)
    job, staged = run(program([run_op(cube, "mesh.subdivide", properties={"number_cuts": 1}),
                               run_op(cube, "mesh.poke", select="INDICES", face_indices=[0], index_scope="CURRENT",
                                      topology_step=1)], writes=[cube]))
    check("mesh_ready_without_live_edit", staged.get("ready_to_commit") and _fingerprint(cube) == before and no_stages())
    check("mesh_commits", commit(job))
    cube = objects()["Cube"]
    check("mesh_live", (len(cube.data.vertices), len(cube.data.polygons)) == (27, 27) and cube.data.name == "Cube")
    undo()
    check("mesh_one_undo", (len(objects()["Cube"].data.vertices), len(objects()["Cube"].data.polygons)) == (8, 6))
    redo()
    check("mesh_one_redo", len(objects()["Cube"].data.vertices) == 27)
    undo()


def object_subset():
    cube = objects()["Cube"]
    job, _ = run(program([run_op(cube, "object.vertex_group_add"), run_op(cube, "object.vertex_group_assign"),
                          run_op(cube, "object.shape_key_add"), run_op(cube, "object.shape_key_add"),
                          run_op(cube, "object.material_slot_add"), run_op(cube, "object.shade_smooth")], writes=[cube]))
    check("object_subset_commits", commit(job))
    cube = objects()["Cube"]
    keys = cube.data.shape_keys
    check("object_subset_live", [group.name for group in cube.vertex_groups] == ["Group"]
          and all(len(vertex.groups) == 1 and abs(vertex.groups[0].weight - 1) < 1e-6 for vertex in cube.data.vertices)
          and keys is not None and [block.name for block in keys.key_blocks] == ["Basis", "Key 1"]
          and len(cube.material_slots) == 3 and all(face.use_smooth for face in cube.data.polygons))
    check("object_level_state_committed", cube.active_shape_key_index == 1 and cube.active_material_index == 2)
    undo()
    cube = objects()["Cube"]
    check("object_subset_one_undo", not cube.vertex_groups and cube.data.shape_keys is None and len(cube.material_slots) == 2
          and cube.active_material_index == 0)


def force_field():
    cube = objects()["Cube"]
    job, _ = run(program([run_op(cube, "object.forcefield_toggle")], writes=[cube]))
    check("force_field_commits", commit(job) and objects()["Cube"].field.type == "FORCE")
    undo()
    check("force_field_one_undo", objects()["Cube"].field.type == "NONE")
    empty = objects()["QA Empty"]
    job, result = run(program([run_op(empty, "object.forcefield_toggle")], writes=[empty]))
    check("force_field_needs_settings", failed_with(result, "FIELD_SETTINGS_MISSING") and no_stages())


def other_types():
    curve = objects()["QA Curve"]
    job, _ = run(program([run_op(curve, "curve.subdivide")], writes=[curve]))
    check("curve_commits", commit(job) and len(objects()["QA Curve"].data.splines[0].bezier_points) == 3)
    undo()
    check("curve_one_undo", len(objects()["QA Curve"].data.splines[0].bezier_points) == 2)
    rig = objects()["QA Rig"]
    job, _ = run(program([run_op(rig, "armature.subdivide")], writes=[rig]))
    rig = objects()["QA Rig"]
    check("armature_commits_and_rebuilds_pose", commit(job) and len(objects()["QA Rig"].data.bones) == 2
          and len(objects()["QA Rig"].pose.bones) == 2)
    undo()
    check("armature_one_undo", len(objects()["QA Rig"].data.bones) == 1)
    lattice = objects()["QA Lattice"]
    job, _ = run(program([run_op(lattice, "lattice.make_regular")], writes=[lattice]))
    check("lattice_commits", commit(job) and tuple(objects()["QA Lattice"].data.points[0].co_deform) == (-.5, -.5, -.5))
    undo()
    check("lattice_one_undo", tuple(objects()["QA Lattice"].data.points[0].co_deform) == (-1, -1, -1))
    cube = objects()["Cube"]
    uv_before = [tuple(loop.uv) for loop in cube.data.uv_layers.active.data]
    job, _ = run(program([run_op(cube, "uv.cube_project", properties={"cube_size": 4})], writes=[cube]))
    check("uv_commits", commit(job) and [tuple(loop.uv) for loop in objects()["Cube"].data.uv_layers.active.data] != uv_before)
    undo()
    check("uv_one_undo", [tuple(loop.uv) for loop in objects()["Cube"].data.uv_layers.active.data] == uv_before)


def preview_accept():
    cube = objects()["Cube"]
    result = run(program([run_op(cube, "mesh.subdivide")], writes=[cube], mode="PREVIEW"))[1]
    check("preview_retained_without_live_edit", result.get("status") == "preview" and len(cube.data.vertices) == 8
          and no_stages())
    accepted = native.begin(window, "angelo_accept_preview", {"preview_id": result["preview_id"]})
    check("accepted_preview_ready", settle(accepted).get("ready_to_commit"))
    check("accepted_preview_commits", commit(accepted) and len(objects()["Cube"].data.vertices) == 26)
    undo()
    check("preview_accept_one_undo", len(objects()["Cube"].data.vertices) == 8 and no_stages())


def stale_revisions():
    cube = objects()["Cube"]
    arguments = program([run_op(cube, "mesh.subdivide")], writes=[cube])
    cube.active_material_index = 1  # A manual edit only the operator_state revision field reads.
    job, result = run(arguments)
    check("manual_active_slot_makes_plan_stale", result.get("status") == "failed" and len(cube.data.vertices) == 8)
    cube.active_material_index = 0
    rig = objects()["QA Rig"]
    queries.invalidate()
    arguments = program([run_op(rig, "armature.subdivide")], writes=[rig])
    rig.data.collections.new("QA Manual").assign(rig.data.bones[0])
    job, result = run(arguments)
    check("manual_bone_collection_makes_plan_stale", result.get("status") == "failed" and len(rig.data.bones) == 1)
    rig.data.collections.remove(rig.data.collections["QA Manual"])
    queries.invalidate()


def grease_pencil():
    def layers():
        return [layer.name for layer in objects()["QA Sketch"].data.layers]

    job, _ = run(validate_call("angelo_scene_program", {"mode": "APPLY", "read_set": [], "write_set": [], "operations": [
        {"kind": "create_object", "target": None, "parameters": {"type": "GREASEPENCIL", "alias": "sketch",
                                                                 "name": "QA Sketch"}},
        {"kind": "operator_run", "target": "$sketch", "parameters": {"operator": "grease_pencil.layer_add",
                                                                    "properties": {"new_layer_name": "Ink"}}}]}))
    check("grease_pencil_create_commits", commit(job) and all(row.get("passed") for row in job.result["checks"]))
    check("grease_pencil_live", objects()["QA Sketch"].type == "GREASEPENCIL" and layers() == ["Layer", "Ink"]
          and len(objects()["QA Sketch"].data.layers["Layer"].frames) == 1)
    queries.invalidate()
    sketch = objects()["QA Sketch"]
    job, _ = run(program([run_op(sketch, "grease_pencil.layer_move", properties={"direction": "DOWN"})], writes=[sketch]))
    check("grease_pencil_layer_edit_commits", commit(job) and sorted(layers()) == ["Ink", "Layer"] and layers() != ["Layer", "Ink"])
    undo()
    check("grease_pencil_layer_edit_one_undo", layers() == ["Layer", "Ink"])
    sketch = objects()["QA Sketch"]
    arguments = program([run_op(sketch, "grease_pencil.layer_remove")], writes=[sketch])
    sketch.data.layers["Ink"].opacity = .5  # A manual edit after the plan was made: the data revision covers layers.
    job, result = run(arguments)
    check("grease_pencil_manual_layer_edit_makes_plan_stale", result.get("status") == "failed" and len(layers()) == 2)
    undo()
    check("grease_pencil_create_one_undo", "QA Sketch" not in objects() and no_stages())


def pose_operators():
    def root():
        return objects()["QA Rig"].pose.bones["Root"]

    rig = objects()["QA Rig"]
    job, _ = run(program([run_op(rig, "pose.loc_clear"),
                          run_op(rig, "pose.rotation_mode_set", properties={"type": "XYZ"})], writes=[rig]))
    check("pose_operators_commit", commit(job) and tuple(root().location) == (0, 0, 0) and root().rotation_mode == "XYZ")
    check("pose_selection_is_not_committed", not root().select and objects()["QA Rig"].data.bones["Root"].name == "Root")
    undo()
    check("pose_operators_one_undo", tuple(root().location) == (1, 2, 3) and root().rotation_mode == "QUATERNION")
    redo()
    check("pose_operators_one_redo", tuple(root().location) == (0, 0, 0))
    undo()
    rig = objects()["QA Rig"]
    rig.keyframe_insert('pose.bones["Root"].location', frame=1)
    queries.invalidate()
    job, result = run(program([run_op(rig, "pose.loc_clear")], writes=[rig]))
    check("animated_pose_refused", failed_with(result, "ANIMATED_POSE") and tuple(root().location) == (1, 2, 3))
    objects()["QA Rig"].animation_data_clear()
    queries.invalidate()
    # The Auto-Keying preference seeds a new scene's tool settings (Blender's scene defaults); the stage scene
    # pins it off, so no operator keys the stage or follows the artist's preference.
    bpy.context.preferences.edit.use_auto_keying = True
    staged, execute = [], action_operators._execute

    def spy(context, *args):
        staged.append(context.scene.tool_settings.use_keyframe_insert_auto)
        return execute(context, *args)

    try:
        with patch.object(action_operators, "_execute", spy):
            job, result = run(program([run_op(objects()["QA Rig"], "pose.loc_clear")], writes=[objects()["QA Rig"]]))
        report["auto_keying_preference"] = [staged, result.get("error")]
        committed = commit(job)
        check("auto_keying_preference_not_staged", staged == [False] and committed
              and tuple(root().location) == (0, 0, 0) and objects()["QA Rig"].animation_data is None)
        if committed:
            undo()
            check("auto_keying_preference_one_undo", tuple(root().location) == (1, 2, 3) and no_stages())
    finally:
        bpy.context.preferences.edit.use_auto_keying = False


def suspended_between_operations():
    cube = objects()["Cube"]
    arguments = program([run_op(cube, "object.shape_key_add"), run_op(cube, "object.shape_key_add"),
                         run_op(cube, "mesh.subdivide")], writes=[cube])
    with spent_budget():
        job, _ = run(arguments)
    check("suspended_program_commits", commit(job))
    cube = objects()["Cube"]
    check("suspended_shape_keys_survive", len(cube.data.shape_keys.key_blocks) == 2 and len(cube.data.vertices) == 26)
    undo()
    check("suspended_one_undo", objects()["Cube"].data.shape_keys is None and no_stages())


def refusals():
    cube, curve = objects()["Cube"], objects()["QA Curve"]
    before = _fingerprint(cube)
    for code, operation in {"NOT_ALLOWED": run_op(cube, "mesh.primitive_torus_add"),
                            "COVERED": run_op(cube, "object.particle_system_add"), "NEW_OBJECTS": run_op(cube, "mesh.separate"),
                            "AMPLIFIER_BOUND": run_op(cube, "mesh.subdivide", properties={"number_cuts": 64})}.items():
        try:
            program([operation], writes=[cube])
            refused = False
        except ValueError as error:
            refused = code in str(error)
        check("refused_by_validation_" + code, refused)
    cases = {
        "UNKNOWN_PROPERTY": run_op(cube, "mesh.subdivide", properties={"cuts": 2}),
        "GROWTH_BOUND": run_op(cube, "mesh.primitive_grid_add", properties={"x_subdivisions": 256, "y_subdivisions": 256}),
        "NO_EFFECT": run_op(cube, "mesh.dissolve_degenerate"), "POLL_FAILED": run_op(cube, "object.shape_key_make_basis"),
        "PROPERTY_RNA_RANGE": run_op(cube, "object.vertex_group_limit_total", properties={"limit": 40}),
        "PROPERTY_NOT_SETTABLE": run_op(cube, "object.vertex_weight_paste", properties={"weight_group": 1}),
    }
    for code, operation in cases.items():
        job, result = run(program([operation], writes=[cube]))
        check("refused_" + code, failed_with(result, code) and _fingerprint(cube) == before and no_stages())
    job, result = run(program([run_op(cube, "object.shape_key_add"), run_op(cube, "object.shape_key_remove",
                                                                           properties={"all": True})], writes=[cube]))
    check("refused_SHAPE_KEY_FREE", failed_with(result, "SHAPE_KEY_FREE") and _fingerprint(cube) == before and no_stages())
    job, result = run(program([run_op(curve, "mesh.subdivide")], writes=[curve]))
    check("refused_TARGET_TYPE", failed_with(result, "TARGET_TYPE") and no_stages())
    job, result = run(program([run_op(cube, "mesh.poke", select="INDICES", face_indices=[0], index_scope="CURRENT",
                                      topology_step=1)], writes=[cube]))
    check("refused_stale_topology_step", failed_with(result, "topology") and no_stages())


def _scratch():
    scene = bpy.data.scenes.new("QA Operator Stage")
    collection = bpy.data.collections.new("QA Operator Stage Objects")
    scene.collection.children.link(collection)
    context = StagedActionContext(scene, collection)
    operation = lambda kind, params: {"kind": kind, "target": None, "parameters": params}
    apply_operation(context, operation("create_primitive", {"primitive": "CUBE", "alias": "mesh"}))
    mesh = context.resolve("$mesh")
    mesh.data.uv_layers.new(name="UVMap")
    mesh.vertex_groups.new(name="G").add([0, 1, 2, 3], .5, "REPLACE")
    mesh.shape_key_add(name="Basis")
    mesh.shape_key_add(name="K").data[0].co = (2, 2, 2)
    context.track_id(mesh.data.shape_keys)
    material = context.track_id(bpy.data.materials.new("QA Operator Material"))
    mesh.data.materials.append(material)
    mesh.active_material_index = 0
    apply_operation(context, operation("create_object", {"type": "CURVE", "alias": "curve"}))
    apply_operation(context, operation("create_object", {"type": "LATTICE", "alias": "lattice"}))
    apply_operation(context, operation("create_object", {"type": "GREASEPENCIL", "alias": "sketch"}))
    drawing = context.resolve("$sketch").data.layers[0].frames[0].drawing
    drawing.add_strokes([4, 5])
    for index, point in enumerate(drawing.attributes["position"].data):
        point.vector = (index * .1, (index % 2) * .1, 0)
    apply_operation(context, operation("create_object", {"type": "TEXT", "alias": "text", "body": "Angelo"}))
    hair = context.track_id(bpy.data.hair_curves.new("QA Operator Hair"))
    hair.add_curves([4, 4])
    for index, point in enumerate(hair.points):
        point.position = (index * .1, 0, (index % 4) * .2)
    context.add_object(context.track_id(bpy.data.objects.new("QA Operator Hair", hair)), "hair")
    apply_operation(context, operation("armature_create", {"alias": "armature", "bones": [
        {"name": "A", "head": [0, 0, 0], "tail": [0, 0, 1]},
        {"name": "B", "head": [0, 0, 1], "tail": [0, 0, 2], "parent": "A", "connected": True}]}))
    return context


def _geometry(obj):
    """Positions, elements and corner order of a mesh, curve or lattice (what derived_geometry calls geometry)."""
    data = obj.data
    if obj.type == "MESH":
        return ([tuple(v.co) for v in data.vertices], [tuple(e.vertices) for e in data.edges],
                [tuple(p.vertices) for p in data.polygons])
    if obj.type == "CURVE":
        return [([tuple(p.co) for p in s.points], [tuple(p.co) for p in s.bezier_points]) for s in data.splines]
    if obj.type == "LATTICE":
        return [tuple(p.co_deform) for p in data.points]
    return None


def smoke_every_admitted_operator():
    """Every admitted operator once, with defaults, on fresh private copies: no crash, no live or Main residue."""
    live = {obj.name: _fingerprint(obj) for obj in bpy.data.objects}
    bpy.data.curves.remove(bpy.data.curves.new("QA Font Warm-up", "FONT"))  # Loads Blender's built-in font once.
    ids_before = sum(len(getattr(bpy.data, prop.identifier)) for prop in bpy.data.bl_rna.properties
                     if prop.type == "COLLECTION" and prop.identifier != "all_ids")
    outcomes, timings, reshaped = {}, [], []
    for idname in sorted(ALLOWED):
        context = _scratch()
        alias = {"MESH": "$mesh", "CURVE": "$curve", "ARMATURE": "$armature", "LATTICE": "$lattice",
                 "GREASEPENCIL": "$sketch", "CURVES": "$hair", "FONT": "$text"}[target_types(idname)[0]]
        before = _geometry(context.resolve(alias))
        started = time.perf_counter()
        try:
            apply_operation(context, {"kind": "operator_run", "target": alias, "parameters": {"operator": idname}})
            outcome = "OK"
            if keeps_geometry(idname) and _geometry(context.resolve(alias)) != before:
                reshaped.append(idname)  # Certified without a geometry check (derived_geometry): it must keep it.
        except ValueError as error:
            outcome = str(error).split(":")[0].replace("operator_run ", "")
        except RuntimeError as error:
            outcome = "RUNTIME: " + str(error)[:80]
        timings.append(time.perf_counter() - started)
        outcomes[idname] = outcome
        staged = [obj for obj in context.collection.objects]
        assert all(obj.mode == "OBJECT" for obj in staged), idname
        bpy.data.batch_remove(ids=list(set(context.owned_ids) | set(context.created_ids)))
    counts = {}
    for outcome in outcomes.values():
        counts[outcome] = counts.get(outcome, 0) + 1
    report["smoke"] = {"outcomes": outcomes, "counts": counts, "seconds_median": statistics.median(timings),
                       "seconds_max": max(timings)}
    ids_after = sum(len(getattr(bpy.data, prop.identifier)) for prop in bpy.data.bl_rna.properties
                    if prop.type == "COLLECTION" and prop.identifier != "all_ids")
    check("smoke_leaves_live_objects_unchanged", {obj.name: _fingerprint(obj) for obj in bpy.data.objects} == live)
    check("smoke_leaves_no_ids", ids_after == ids_before)
    report["smoke"]["keepers_that_reshaped"] = reshaped
    check("geometry_keeping_operators_keep_geometry", not reshaped)
    # Most refusals here are honest NO_EFFECT or POLL_FAILED results on these small defaults, never a crash.
    check("smoke_runs_most_admitted_operators", counts.get("OK", 0) >= 120 and not any(key.startswith("RUNTIME")
                                                                                       for key in counts))


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    queries.invalidate()
    setup()
    mesh_operators()
    object_subset()
    force_field()
    other_types()
    preview_accept()
    stale_revisions()
    grease_pencil()
    pose_operators()
    suspended_between_operations()
    refusals()
    smoke_every_admitted_operator()
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "operator-run.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_OPERATOR_RUN", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
