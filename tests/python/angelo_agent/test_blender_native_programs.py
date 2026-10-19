# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Real RNA stage isolation, private bundles, stale gates and operator Undo/Redo.

Run only in a disposable --factory-startup instance. No providers, accounts,
user projects, network requests, native builds or paid calls are involved.
"""

import copy
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bpy

assert "--factory-startup" in sys.argv, "Use a disposable factory-startup Blender instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-native-programs-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import native_programs as native, scene_queries as queries
from angelo_agent.program_jobs import PREVIEWS, WRITER

report = {"checks": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path,
          "version": bpy.app.version_string, "opensubdiv": bool(bpy.app.build_options.opensubdiv)}

sys.path.insert(0, str(Path(__file__).resolve().parent))
from program_qa_helpers import args_for, move, no_stages, FullStore, commit_operator, partial_cleanup_regression
from program_qa_helpers import quantum_cancellation_regression, fused_poll_regression, spent_budget
from program_teardown_qa import partial_teardown_regressions, custom_property_admission_regression
ProgramQACommit = commit_operator(report)


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def settle(job):
    for _ in range(200):
        result = job.poll()
        if result.get("final") or result.get("ready_to_commit"):
            return result
    raise AssertionError("cooperative native program did not settle")


def large_mesh_program_regression(window):
    """Programs reach the designed 100,000-vertex input limit, not a hashing budget."""
    import bmesh
    mesh = bpy.data.meshes.new("Large program mesh")
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=315, y_segments=315, size=1)
    bm.to_mesh(mesh)
    bm.free()
    large = bpy.data.objects.new("Large program target", mesh)
    window.scene.collection.objects.link(large)
    window.view_layer.update()
    try:
        job = native.begin(window, 'angelo_scene_program', move(window, large, location=(5, 0, 0)))
        check('large_mesh_program_stages', settle(job).get('ready_to_commit'))
        result = bpy.ops.angelo.program_qa_commit('EXEC_DEFAULT', True, program_id=job.id)
        check('large_mesh_program_commits', 'FINISHED' in result and job.result.get('status') == 'committed' and
              tuple(large.location) == (5, 0, 0) and len(mesh.vertices) == 99856 and no_stages())
    finally:
        bpy.data.objects.remove(large)
        bpy.data.meshes.remove(mesh)


def run_checks():
    window = bpy.context.window_manager.windows[0]
    source = window.scene.objects["Cube"]
    original_scene = window.scene
    original_scene_name = original_scene.name
    original_selection = [obj.name for obj in window.view_layer.objects if obj.select_get()]
    original_active = window.view_layer.objects.active
    original_active_name = original_active.name
    bpy.context.preferences.edit.use_global_undo = True
    bpy.context.preferences.filepaths.file_preview_type = 'NONE'
    native.register()
    bpy.utils.register_class(ProgramQACommit)
    quantum_cancellation_regression(window, source, check)
    fused_poll_regression(window, source, check)
    partial_teardown_regressions(window, source, settle, check)
    custom_property_admission_regression(window, source, settle, check)
    large_mesh_program_regression(window)

    # Each safe phase is cancellable, including READY_TO_COMMIT. Poll never
    # mutates the source or queues an implicit undo of previous user work.
    # One unit per poll exposes every boundary that fusion would otherwise skip.
    for polls in range(8):
        baseline = native._fingerprint(source)
        job = native.begin(window, "angelo_scene_program", move(window, source))
        with spent_budget():
            for _ in range(polls):
                outcome = job.poll()
                check("phase_%d_does_not_mutate_original" % polls, native._fingerprint(source) == baseline)
                if outcome.get("ready_to_commit"):
                    break
        result = job.cancel()
        check("cancel_phase_%d" % polls, result["cancelled"] and no_stages() and WRITER.owner is None)

    # Shared mesh, material, action and node-group dependencies must all become
    # private; mutating a clone cannot mutate another user's linked instance.
    shared = source.copy()
    shared.data = source.data
    shared.name = "Program shared source"
    window.scene.collection.objects.link(shared)
    material = source.data.materials[0]
    source.keyframe_insert(data_path="location", frame=1)
    source.keyframe_insert(data_path="location", frame=24)
    action = source.animation_data.action
    shared.animation_data_create().action = action
    if hasattr(shared.animation_data, "action_slot"):
        shared.animation_data.action_slot = source.animation_data.action_slot
    group = bpy.data.node_groups.new("Program shared shader group", "ShaderNodeTree")
    group.interface.new_socket(name="Color", in_out="INPUT", socket_type="NodeSocketColor")
    group.interface.new_socket(name="Color", in_out="OUTPUT", socket_type="NodeSocketColor")
    input_node, output_node = group.nodes.new("NodeGroupInput"), group.nodes.new("NodeGroupOutput")
    group.links.new(input_node.outputs["Color"], output_node.inputs["Color"])
    material.use_nodes = True
    group_node = material.node_tree.nodes.new("ShaderNodeGroup")
    group_node.name = "Program shared nodes"
    group_node.node_tree = group
    before = native._fingerprint(source)
    handle = queries.object_handle(source)
    job = native.begin(window, "angelo_scene_program",
                       args_for(window, [{"kind": "transform", "target": handle, "parameters": {"location": [1, 2, 3]}}],
                                writes=[source], store=FullStore()), store=FullStore())
    job.poll()
    check("ready_preparation_skips_redundant_copy_cycles", job.phase not in {"PREPARE", "COPY", "REMAP"} and not job.closed and no_stages() and not native.runtime_is_staging() and native.has_active_programs())
    with native.job_context(job.id) as stage:
        check('precise_checkpoint_guard_sees_temporary_hydration', native.runtime_is_staging())
        staged = stage.objects[handle]
        check("shared_mesh_is_private", staged.data != source.data and staged.data.users == 1)
        check("shared_material_is_private", staged.data.materials[0] != material)
        check("shared_action_is_private", staged.animation_data.action != action)
        check("shared_nested_node_group_is_private", staged.data.materials[0].node_tree.nodes["Program shared nodes"].node_tree != group)
        staged.data.vertices[0].co.x += .75
        staged.data.materials[0].diffuse_color = (.2, .7, .1, 1)
        staged.data.materials[0].node_tree.nodes["Program shared nodes"].node_tree.nodes[0].label = "private only"
        check("shared_dependencies_originals_unchanged", native._fingerprint(source) == before and shared.data == source.data)
    job.cancel()
    check("cancel_cleans_shared_dependencies", no_stages() and source.data.users == 2 and source.animation_data.action == action)
    source.animation_data_clear()
    shared.animation_data_clear()
    material.node_tree.nodes.remove(group_node)
    bpy.data.objects.remove(shared, do_unlink=True)
    bpy.data.node_groups.remove(group)
    if action.users == 0:
        bpy.data.actions.remove(action)

    # Dependencies are explicit, not global lookup. An undeclared parent is
    # rejected before staging; a declared parent is remapped to its private copy.
    parent = bpy.data.objects.new("Program parent", None)
    window.scene.collection.objects.link(parent)
    source.parent = parent
    bad = native.begin(window, "angelo_scene_program", move(window, source))
    failure = settle(bad)
    check("undeclared_dependency_refused", not failure["success"] and "dependency" in failure["error"] and no_stages())
    handle = queries.object_handle(source)
    good = native.begin(window, "angelo_scene_program",
                        args_for(window, [{"kind": "transform", "target": handle, "parameters": {"location": [2, 3, 4]}}],
                                 writes=[source], reads=[parent]))
    intent = settle(good)
    with native.job_context(good.id) as stage:
        check("declared_parent_remapped", intent.get("ready_to_commit") and stage.objects[handle].parent == stage.objects[queries.object_handle(parent)])
    good.cancel()
    source.parent = None
    bpy.data.objects.remove(parent)
    window.view_layer.update()

    # Exact fresh checks at commit preserve manual changes, even after a job
    # has completed all staging/expected-check phases.
    job = native.begin(window, "angelo_scene_program", move(window, source))
    intent = settle(job)
    check("apply_yields_commit_intent_only", intent.get("ready_to_commit") and list(source.location) == [0, 0, 0])
    source.location.x = 9
    result = job.commit()
    check("manual_transform_invalidates_commit", not result["success"] and source.location.x == 9 and no_stages())
    source.location = (0, 0, 0)
    window.view_layer.update()

    job = native.begin(window, "angelo_scene_program", move(window, source))
    settle(job)
    source.data.materials[0].roughness = .123
    result = job.commit()
    check("manual_material_invalidates_commit", not result["success"] and abs(source.data.materials[0].roughness - .123) < 1e-6 and no_stages())

    job = native.begin(window, "angelo_scene_program", move(window, source))
    job.poll()
    switched = bpy.data.scenes.new("Program switched scene")
    window.scene = switched
    failure = job.poll()
    check("scene_switch_cancels_stage", not failure["success"] and "scene" in failure["error"] and no_stages())
    window.scene = original_scene
    bpy.data.scenes.remove(switched)

    job = native.begin(window, 'angelo_scene_program', move(window, source))
    job.poll()
    frame = window.scene.frame_current
    window.scene.frame_current = frame + 1
    failure = job.poll()
    check('frame_switch_invalidates_staged_preview_context', not failure['success'] and 'frame' in failure['error'] and no_stages())
    window.scene.frame_current = frame

    # Host assertions are mandatory and do not depend on a provider's verify
    # flag. A check failure leaves the entire artist scene untouched.
    handle = queries.object_handle(source)
    bad_check = {"kind": "transform", "target": handle, "property": "location", "expected": [8, 8, 8], "tolerance": .0001}
    before = native._fingerprint(source)
    job = native.begin(window, "angelo_scene_program", move(window, source, checks=[bad_check]))
    failed = settle(job)
    check("failed_check_discards_all_staging", not failed["success"] and not failed["partial_changes_possible"] and native._fingerprint(source) == before and no_stages())

    # Retained previews own NO Blender Main IDs. They can be accepted after
    # temporary hydration; a later manual edit makes Accept discard instead.
    baseline_ids = set(native._main_ids())
    preview = native.begin(window, "angelo_scene_program", move(window, source, mode="PREVIEW"))
    result = settle(preview)
    check("preview_result_is_retained_uuid", result.get("status") == "preview" and len(result["preview_id"]) == 36)
    check('program_delta_has_no_vertex_or_face_arrays', all('positions' not in item and not isinstance(item.get('faces'), list) for item in result['delta']['geometry']) and len(json.dumps(result)) < 20000)
    check("preview_removed_all_main_ids", no_stages() and set(native._main_ids()) == baseline_ids)
    identity = result["preview_id"]
    record = PREVIEWS.get(identity, native._owner(None, window))
    bundle = Path(record["stage"].bundle)
    # POSIX mode bits; on Windows the per-user profile's access list keeps the bundle private.
    check("private_preview_bundle", bundle.is_file() and (os.name == "nt" or bundle.stat().st_mode & 0o777 == 0o600))
    with native.preview_context(identity, window=window) as stage:
        check("preview_hydrates_real_geometry_without_switch", stage.scene != window.scene and stage.objects[handle].data != source.data and list(stage.objects[handle].location) == [2, 3, 4])
    check("preview_context_cleanup_does_not_consume", no_stages() and identity in PREVIEWS.records and bundle.exists())
    native.begin(window, "angelo_discard_preview", {"preview_id": identity})
    check("preview_discard_deletes_bundle", not bundle.exists() and no_stages())

    if not bpy.app.background:
        from angelo_agent.references import ReferenceStore
        owner_id = '2834489a-7563-471f-b78b-249eff26c6f6'
        controller = SimpleNamespace(state={'id':owner_id}, references=ReferenceStore(OUT, owner_id), closed=False)
        preview = native.begin(window, 'angelo_scene_program', move(window, source, mode='PREVIEW'), controller=controller)
        pixels = settle(preview)
        check('preview_returns_actual_staged_png', pixels['status'] == 'preview' and len(pixels['views']) == 1 and Path(pixels['views'][0]['path']).stat().st_size > 1000 and no_stages())
        inspected = native.begin(window, 'angelo_inspect_preview', {'preview_id':pixels['preview_id']}, controller=controller)
        check('preview_inspect_forwards_original_admitted_pixels', inspected['views'] == pixels['views'] and inspected['preview_note'] == pixels['preview_note'] and no_stages())
        before_location = tuple(source.location)
        bpy.ops.ed.undo_push(message='Program accepted preview baseline')
        accepted = native.begin(window, 'angelo_accept_preview', {'preview_id':pixels['preview_id']}, controller=controller)
        intent = settle(accepted)
        check('preview_accept_yields_without_live_edit', intent.get('ready_to_commit') and tuple(source.location) == before_location and no_stages())
        result = bpy.ops.angelo.program_qa_commit('EXEC_DEFAULT', True, program_id=accepted.id)
        check('preview_accept_commits_exact_preview', 'FINISHED' in result and list(source.location) == [2,3,4] and no_stages() and not PREVIEWS.records)
        bpy.ops.ed.undo()
        source = window.scene.objects['Cube']
        check('preview_accept_one_undo', tuple(source.location) == before_location and no_stages())

    preview = native.begin(window, "angelo_scene_program", move(window, source, mode="PREVIEW"))
    result = settle(preview)
    source.location.z = 7
    accepted = native.begin(window, "angelo_accept_preview", {"preview_id": result["preview_id"]})
    stale = settle(accepted)
    check("preview_accept_rejects_manual_edit", not stale["success"] and source.location.z == 7 and no_stages() and not PREVIEWS.records)
    source.location = (0, 0, 0)
    window.view_layer.update()

    # Ordinary full .blend saving during staging suspends the Main IDs before
    # serialization. A lazy poll can resume; retained previews don't block saves.
    job = native.begin(window, "angelo_scene_program", move(window, source))
    job.poll(); job.poll(); job.poll()
    saved = OUT / "stage-free-project.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(saved), copy=True)
    check("active_save_suspends_stage", isinstance(job.stage, native.SuspendedStage) and no_stages())
    outcome = job.poll()
    check("active_save_resumes_then_resuspends", outcome["status"] in {'progress', 'ready_to_commit'} and isinstance(job.stage, native.SuspendedStage) and no_stages())
    job.cancel()
    with bpy.data.libraries.load(str(saved), link=False) as (available, _destination):
        check("full_project_contains_no_ghost_stages", not any(name.startswith("__angelo_stage_") for name in available.scenes))

    # Commit rollback journal: fail after the mesh pointer swap but before all
    # modifier writes finish. The original data/settings must be restored.
    modifier = source.modifiers.new("Program rollback bevel", "BEVEL")
    modifier.width = .1
    handle = queries.object_handle(source)
    operations = [
        {"kind": "mesh_extrude", "target": handle, "parameters": {"face_indices": [0], "translation": [0, 0, .2]}},
        {"kind": "modifier_configure", "target": handle, "parameters": {"type": "BEVEL", "modifier": modifier.name, "settings": {"width": .02}}}]
    baseline = native._fingerprint(source)
    job = native.begin(window, "angelo_scene_program", args_for(window, operations, writes=[source]))
    intent = settle(job)
    check("rollback_fixture_ready", intent.get("ready_to_commit"))
    original_setter = native._set_settings
    calls = [0]

    def injected_failure(value, settings, mapping):
        calls[0] += 1
        if calls[0] == 1:
            raise RuntimeError("injected commit settings failure")
        return original_setter(value, settings, mapping)

    with patch.object(native, "_set_settings", side_effect=injected_failure):
        result = job.commit()
    check("atomic_commit_failure_rolled_back", not result["success"] and not result["partial_changes_possible"] and native._fingerprint(source) == baseline and no_stages())
    source.modifiers.remove(modifier)

    # Every imported mutable dependency must be bounded before evaluation,
    # even for a nominally cheap transform of an eight-vertex base mesh.
    for kind in ('ARRAY', 'REMESH', 'SUBSURF'):
        unsafe = source.modifiers.new('Program unsafe ' + kind, kind)
        if kind == 'ARRAY':
            unsafe.count = 100000
        elif kind == 'SUBSURF':
            unsafe.levels = 10
        else:  # A supported type: a voxel size this fine exceeds the bound.
            unsafe.mode, unsafe.voxel_size = 'VOXEL', .0001
        before = native._fingerprint(source)
        handle = queries.object_handle(source)
        value = args_for(window, [{'kind':'transform','target':handle,'parameters':{'location':[1,2,3]}}],
                         writes=[source], store=FullStore())
        with patch.object(native.BlenderProgramBackend, 'create_stage', side_effect=AssertionError('must reject before staging')):
            job = native.begin(window, 'angelo_scene_program', value, store=FullStore())
            failure = settle(job)
        check('unsafe_' + kind.lower() + '_rejected_before_evaluation', not failure['success'] and 'must reject' not in failure['error'] and native._fingerprint(source) == before and no_stages())
        source.modifiers.remove(unsafe)

    # Index authority and validated node provenance survive every serialized
    # yield, rather than being reset by a freshly constructed RNA context.
    topology_ops = [
        {'kind':'create_primitive','target':None,'parameters':{'primitive':'CUBE','alias':'shape','name':'Program Topology'}},
        {'kind':'mesh_extrude','target':'$shape','parameters':{'face_indices':[0],'translation':[0,0,.2]}},
        {'kind':'mesh_inset','target':'$shape','parameters':{'face_indices':[0],'thickness':.05}}]
    job = native.begin(window, 'angelo_scene_program', args_for(window, topology_ops))
    stale_indexes = settle(job)
    check('topology_authority_survives_suspension', not stale_indexes['success'] and 'Stale topology' in stale_indexes['error'] and no_stages())
    topology_ops[-1]['parameters'].update(index_scope='CURRENT', topology_step=1)
    job = native.begin(window, 'angelo_scene_program', args_for(window, topology_ops))
    intent = settle(job)
    with native.job_context(job.id) as stage:
        shape = stage.context.resolve('$shape')
        check('current_topology_step_preserved', intent.get('ready_to_commit') and stage.context.topology_versions[shape] == 2)
    job.cancel()
    node_ops = [
        {'kind':'create_primitive','target':None,'parameters':{'primitive':'CUBE','alias':'shape','name':'Program Nodes'}},
        {'kind':'geometry_nodes','target':'$shape','parameters':{
            'name':'Program retained geometry',
            'nodes':[{'id':'input','type':'GROUP_INPUT'}, {'id':'shift','type':'TRANSFORM_GEOMETRY','inputs':{'Translation':[.2,0,0]}}, {'id':'output','type':'GROUP_OUTPUT'}],
            'links':[{'from_node':'input','from_socket':'Geometry','to_node':'shift','to_socket':'Geometry'}, {'from_node':'shift','from_socket':'Geometry','to_node':'output','to_socket':'Geometry'}]}},
        {'kind':'uv_project','target':'$shape','parameters':{'method':'CUBE'}}]
    job = native.begin(window, 'angelo_scene_program', args_for(window, node_ops))
    intent = settle(job)
    with native.job_context(job.id) as stage:
        group = stage.context.resolve('$shape').modifiers[0].node_group
        check('node_provenance_and_bounds_survive_suspension', intent.get('ready_to_commit') and group in stage.context.node_graphs and stage.context.node_bounds[group] > 0)
    job.cancel()

    # Global counts cover the prospective final artist scene, not just the
    # two isolated aliases. They are checked again immediately before commit.
    count_ops = [
        {'kind':'create_primitive','target':None,'parameters':{'primitive':'CUBE','alias':'first','name':'Program count first'}},
        {'kind':'create_primitive','target':None,'parameters':{'primitive':'CUBE','alias':'second','name':'Program count second'}}]
    total = len(window.scene.objects) + 2
    count_check = {'kind':'object_count','minimum':total,'maximum':total}
    job = native.begin(window, 'angelo_scene_program', args_for(window, count_ops, checks=[count_check]))
    intent = settle(job)
    check('global_count_includes_unchanged_artist_objects', intent.get('ready_to_commit'))
    manual = bpy.data.objects.new('Program count manual change', None)
    window.scene.collection.objects.link(manual)
    failed = job.commit()
    check('global_count_revalidated_at_commit', not failed['success'] and 'check changed' in failed['error'] and manual.name in window.scene.objects and no_stages())
    bpy.data.objects.remove(manual, do_unlink=True)
    count_check = {'kind':'object_count','minimum':2,'maximum':2}
    job = native.begin(window, 'angelo_scene_program', args_for(window, count_ops, checks=[count_check]))
    failed = settle(job)
    check('stage_only_count_cannot_falsely_pass_full_scene_check', not failed['success'] and no_stages())

    # An existing OBJECT-linked material must be retained through the changed
    # live object's closure; retaining only data.materials would delete it.
    object_material = bpy.data.materials.new('Program OBJECT slot')
    object_material.diffuse_color = (.9,.1,.2,1)
    source.material_slots[0].link = 'OBJECT'
    source.material_slots[0].material = object_material
    bpy.ops.ed.undo_push(message='Program OBJECT material baseline')
    handle = queries.object_handle(source)
    slot_ops = [
        {'kind':'mesh_extrude','target':handle,'parameters':{'face_indices':[0],'translation':[0,0,.2]}},
        {'kind':'uv_project','target':handle,'parameters':{'method':'CUBE'}}]
    job = native.begin(window, 'angelo_scene_program', args_for(window, slot_ops, writes=[source]))
    intent = settle(job)
    check('object_linked_material_fixture_ready', intent.get('ready_to_commit'))
    result = bpy.ops.angelo.program_qa_commit('EXEC_DEFAULT', True, program_id=job.id)
    retained = source.material_slots[0].material
    # The unmodified staged clone commits as the live original: no duplicate material.
    check('object_linked_material_retained_after_commit', 'FINISHED' in result and source.material_slots[0].link == 'OBJECT' and retained is not None and retained == object_material and bpy.data.materials.get(retained.name) == retained and not any(material.name.startswith('Program OBJECT slot.') for material in bpy.data.materials) and len(source.data.vertices) > 8 and no_stages())
    bpy.ops.ed.undo()
    source = window.scene.objects['Cube']
    check('object_linked_material_commit_one_undo', len(source.data.vertices) == 8 and source.material_slots[0].material.name == 'Program OBJECT slot' and no_stages())
    source.material_slots[0].link = 'DATA'

    # A cleanup exception after live writes cannot be classified as no change.
    # The independently owned-ID batch fallback cleans Main, and the normal
    # operator still completes an Undo boundary for the committed edit.
    bpy.ops.ed.undo_push(message='Program cleanup fault baseline')
    job = native.begin(window, 'angelo_scene_program', move(window, source))
    settle(job)
    with patch.object(native.BlenderProgramBackend, '_remove_stage_ids', side_effect=RuntimeError('injected cleanup failure')):
        result = bpy.ops.angelo.program_qa_commit('EXEC_DEFAULT', True, program_id=job.id)
    check('post_write_cleanup_fault_reports_committed_change', 'FINISHED' in result and job.result['scene_changed'] and job.result['mutation_started'] and job.result['cleanup_warning'] and list(source.location) == [2,3,4] and no_stages())
    bpy.ops.ed.undo()
    source = window.scene.objects['Cube']
    check('cleanup_fault_commit_still_one_undo', list(source.location) == [0,0,0] and no_stages())
    source = partial_cleanup_regression(window, source, settle, check)

    # Snapshot the artist's own normal Undo edit BETWEEN cooperative ticks.
    # Cancel + Undo/Redo must never resurrect a ghost stage from that snapshot.
    bpy.ops.ed.undo_push(message='Program interleaved artist baseline')
    job = native.begin(window, 'angelo_scene_program', move(window, source))
    job.poll(); job.poll(); job.poll(); job.poll()
    check('every_yield_has_no_main_stage_ids', no_stages() and isinstance(job.stage, native.SuspendedStage))
    bpy.ops.mesh.primitive_cube_add('EXEC_DEFAULT', True, size=.25, location=(10,0,0))
    artist_object = window.view_layer.objects.active
    artist_name = artist_object.name  # Exact name already present in the operator's Undo snapshot.
    job.cancel()
    bpy.ops.ed.undo()
    check('manual_undo_cannot_restore_ghost_stage', no_stages() and artist_name not in window.scene.objects)
    bpy.ops.ed.redo()
    check('manual_redo_cannot_restore_ghost_stage', no_stages() and artist_name in window.scene.objects)
    artist_object = window.scene.objects[artist_name]
    mesh = artist_object.data
    bpy.data.objects.remove(artist_object, do_unlink=True)
    if mesh.users == 0:
        bpy.data.meshes.remove(mesh)
    source = window.scene.objects['Cube']
    for obj in window.view_layer.objects:
        obj.select_set(obj == source)
    window.view_layer.objects.active = source

    # Aliases, several real native actions and one ordinary UNDO operator.
    bpy.ops.ed.undo_push(message="Program QA clean baseline")
    operations = [
        {"kind": "create_primitive", "target": None, "parameters": {"primitive": "CUBE", "name": "Program Undo Cube", "alias": "body"}},
        {"kind": "transform", "target": "$body", "parameters": {"location": [4, 2, 1]}},
        {"kind": "material_principled", "target": "$body", "parameters": {"name": "Program Undo Material", "inputs": {"Base Color": [.2, .3, .8, 1]}}},
        {"kind": "modifier_add", "target": "$body", "parameters": {"type": "BEVEL", "settings": {"width": .03, "segments": 2}}},
        {"kind": "uv_project", "target": "$body", "parameters": {"method": "CUBE"}}]
    expected = [{"kind": "transform", "target": "$body", "property": "location", "expected": [4, 2, 1], "tolerance": .00001},
                {"kind": "finite_coordinates", "target": "$body"}]
    job = native.begin(window, "angelo_scene_program", args_for(window, operations, checks=expected))
    intent = settle(job)
    check("alias_program_validated_without_live_object", intent.get("ready_to_commit") and "Program Undo Cube" not in window.scene.objects)
    result = bpy.ops.angelo.program_qa_commit('EXEC_DEFAULT', True, program_id=job.id)
    new = window.scene.objects["Program Undo Cube"]
    check("ordinary_operator_commits_all_actions", "FINISHED" in result and list(new.location) == [4, 2, 1] and len(new.data.materials) == 1 and len(new.modifiers) == 1 and len(new.data.uv_layers) == 1 and no_stages())
    bpy.ops.ed.undo()
    check("one_undo_restores_entire_program", "Program Undo Cube" not in window.scene.objects and no_stages())
    bpy.ops.ed.redo()
    check("one_redo_restores_entire_program", "Program Undo Cube" in window.scene.objects and no_stages())
    check("completed_stop_does_not_auto_undo", job.cancel()["status"] == "committed" and "Program Undo Cube" in window.scene.objects)
    check("artist_window_not_switched", window.scene.name == original_scene_name)
    # Selection remains artist-owned even though private copies were created.
    check("artist_selection_preserved", [obj.name for obj in window.view_layer.objects if obj.select_get()] == original_selection and window.view_layer.objects.active.name == original_active_name)

    # Safety callbacks must survive document loads. Active jobs and retained
    # previews are cleaned before ID replacement, and stale handles cannot be
    # adopted after a load. Subsequent saves are still protected.
    saved_after_load = OUT / 'persistent-callback-project.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(saved_after_load), copy=True)
    source = window.scene.objects['Cube']
    old_handle = queries.object_handle(source)
    preview = native.begin(window, 'angelo_scene_program', move(window, source, mode='PREVIEW'))
    preview_result = settle(preview)
    preview_bundle = Path(PREVIEWS.get(preview_result['preview_id'], native._owner(None, window))['stage'].bundle)
    active = native.begin(window, 'angelo_scene_program', move(window, source))
    active.poll()
    active_bundle = Path(active.stage.bundle) if active.stage.bundle else None
    active_wrapper = next(iter(active.stage.resident.objects.values())) if active.stage.native_owner else None
    generation = native._generation
    bpy.ops.wm.open_mainfile(filepath=str(saved_after_load), load_ui=False)
    window = bpy.context.window_manager.windows[0]
    check('load_discards_active_jobs_and_retained_previews', active.closed and active.result['cancelled'] and not PREVIEWS.records and not preview_bundle.exists() and (active_bundle is None or not active_bundle.exists()) and no_stages() and native._generation > generation)
    if active_wrapper is not None:
        try:
            active_wrapper.name
        except ReferenceError:
            check('load_frees_private_native_main', True)
        else:
            check('load_frees_private_native_main', False)
    check('safety_callbacks_persist_after_load', native.cleanup_all in bpy.app.handlers.load_pre and native.cleanup_all in bpy.app.handlers.undo_pre and native.cleanup_all in bpy.app.handlers.redo_pre and native._before_save in bpy.app.handlers.save_pre)
    try:
        queries.resolve_object(window, old_handle)
    except Exception:
        check('reloaded_handles_expire', True)
    else:
        check('reloaded_handles_expire', False)
    source = window.scene.objects['Cube']
    active = native.begin(window, 'angelo_scene_program', move(window, source))
    active.poll()
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT/'post-load-stage-free.blend'), copy=True)
    check('post_load_save_still_has_no_stage_ids', no_stages() and isinstance(active.stage, native.SuspendedStage))
    active.cancel()
    report["success"] = True


def run():
    try:
        run_checks()
    except Exception:
        report.update(success=False, error=traceback.format_exc())
    finally:
        native.cleanup_all()
    (OUT / "results.json").write_text(json.dumps(report, indent=2))
    print("ANGELO_NATIVE_PROGRAMS", json.dumps(report), flush=True)
    if not bpy.app.background:
        bpy.ops.wm.quit_blender()
    return None


if bpy.app.background:
    run()
else:
    bpy.app.timers.register(run, first_interval=1)
