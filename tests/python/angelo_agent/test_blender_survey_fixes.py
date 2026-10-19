# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The request survey's host defects, reproduced through real staging, commit and Undo.

Use the exact Angelo binary with --factory-startup (GUI, not --background). No
provider process, account or paid request. Each case is a request from the
survey (eval_survey_tasks) as the plan Claude sent, or its minimal core; the
host-only halves are in test_native_survey_fixes.py. Every case fails on a
build without the fixes.
"""

import json
import math
import os
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import bpy

assert '--factory-startup' in sys.argv and not bpy.app.background
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get('ANGELO_QA_DIR', '/private/tmp/angelo-survey-fixes'))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT='1', ANGELO_CONNECTIONS_DIR=str(OUT/'accounts'),
                  ANGELO_STATE_DIR=str(OUT/'state'))
sys.path.insert(0, str(ROOT/'scripts/addons_core'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import action_budget, native_programs as native, scene_queries as queries  # noqa: E402
from angelo_agent.identifiers import IdentifierTable  # noqa: E402
from angelo_agent.scene_validation import VerificationContext, verify  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import args_for, FullStore, no_stages  # noqa: E402

report = {'binary': bpy.app.binary_path, 'checks': {}, 'cases': {}, 'paid_requests': 0}
BLUE = [0.05, 0.1, 0.4, 1.0]


def check(name, value):
    report['checks'][name] = bool(value)
    assert value, name


def window():
    return bpy.context.window_manager.windows[0]


def handle(obj):
    return queries.object_handle(obj)


def op(kind, target, parameters):
    return {'kind': kind, 'target': target, 'parameters': parameters}


class Commit(bpy.types.Operator):
    bl_idname = 'angelo.survey_fixes_commit'
    bl_label = 'Commit survey fixes QA'
    bl_options = {'REGISTER', 'UNDO'}
    program_id: bpy.props.StringProperty()

    def execute(self, context):
        result = native.commit_job(self.program_id, context.window)
        report['last_result'] = {key: value for key, value in result.items() if key not in ('delta', 'views')}
        return {'FINISHED'} if result.get('success') else {'CANCELLED'}


def run_program(operations, writes=(), reads=(), checks=(), controller=None):
    """(result, job): staged, then committed through the Undo operator when it gets ready."""
    # validate_call normalizes like a model-submitted plan, so derived checks run too.
    value = validate_call('angelo_scene_program', args_for(window(), operations, writes=writes, reads=reads,
                                                           checks=checks, store=FullStore()))
    job = native.begin(window(), 'angelo_scene_program', value, controller=controller, store=FullStore())
    for _ in range(400):
        result = job.poll()
        if result.get('final') or result.get('ready_to_commit'):
            break
    if result.get('ready_to_commit'):
        bpy.ops.angelo.survey_fixes_commit('EXEC_DEFAULT', True, program_id=job.id)
        result = job.result
    queries.invalidate()
    return result, job


def undo():
    bpy.ops.ed.undo()
    queries.invalidate()


def principled(material):
    node = next(node for node in material.node_tree.nodes if node.bl_idname == 'ShaderNodeBsdfPrincipled')
    return ([round(value, 5) for value in node.inputs['Base Color'].default_value],
            round(node.inputs['Roughness'].default_value, 5), round(node.inputs['Metallic'].default_value, 5))


def red_plastic(obj, name='Red Plastic'):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    node = material.node_tree.nodes['Principled BSDF']
    node.inputs['Base Color'].default_value = (0.8, 0.02, 0.02, 1)
    node.inputs['Roughness'].default_value = 0.3
    obj.data.materials.append(material)
    return material


def material_edits_in_place():
    """recolor_material: "Change 'Red Plastic' to a deep blue" left 'Red Plastic.002' on the cube."""
    cube = window().scene.objects['Cube']
    cube.data.materials.clear()  # Eval Cube had one material, 'Red Plastic'.
    red_plastic(cube)
    bpy.ops.ed.undo_push(message='Survey material baseline')
    blue = {'name': 'Red Plastic', 'inputs': {'Base Color': BLUE, 'Roughness': 0.2, 'Metallic': 0.0}}
    for slot in (-1, 0):  # The survey's first plan (default slot), then its retry (slot 0).
        result, _job = run_program([op('material_principled', handle(cube), dict(blue, slot=slot))], writes=[cube])
        cube = window().scene.objects['Cube']
        names = [slot.material.name for slot in cube.material_slots]
        report['cases']['material_slot_%d' % slot] = {'status': result.get('status'), 'error': result.get('error'),
                                                      'slots': names}
        # An edit of a held material is a set on its Principled BSDF, checked by node_graph; a new
        # material keeps material_values. The values themselves are read from Blender below (is_edited).
        checks = result.get('checks') or ()
        derived = [item.get('kind') for item in checks if item.get('passed')]
        check('material_slot_%d_commits' % slot, result.get('success') and checks
              and all(item.get('passed') for item in checks) and {'material_values', 'node_graph'} & set(derived))
        check('material_slot_%d_keeps_its_name_and_slot' % slot, names == ['Red Plastic'])
        check('material_slot_%d_is_edited' % slot, principled(cube.material_slots[0].material) == (BLUE, 0.2, 0.0))
        check('material_slot_%d_leaves_no_suffixed_copy_in_use' % slot,
              not any(m.name.startswith('Red Plastic.') and m.users for m in bpy.data.materials))
        undo()
        cube = window().scene.objects['Cube']
        check('material_slot_%d_undo_restores_red' % slot,
              [s.material.name for s in cube.material_slots] == ['Red Plastic']
              and principled(cube.material_slots[0].material)[0][0] == 0.8 and no_stages())
    # The schema's default name never edits: a second default-named material is a new slot, as before.
    for _ in range(2):
        result, _job = run_program([op('material_principled', handle(window().scene.objects['Cube']), {
            'inputs': {'Base Color': BLUE}})], writes=[window().scene.objects['Cube']])
    check('default_names_still_add_materials', result.get('success') and len(
        window().scene.objects['Cube'].material_slots) == 3)
    undo()
    undo()


def material_names_are_exact():
    """The check said 'Red Plastic' passed while the slot held 'Red Plastic.002': names matched by slot."""
    cube = window().scene.objects['Cube']
    result, _job = run_program([op('material_principled', handle(cube), {
        'name': 'Navy', 'slot': 0, 'inputs': {'Base Color': BLUE, 'Roughness': 0.2, 'Metallic': 0.0}})],
        writes=[cube], checks=[{'kind': 'material_values', 'target': handle(cube), 'material': 'Red Plastic',
                                'base_color': BLUE, 'roughness': 0.2, 'metallic': 0, 'tolerance': 0.0001}])
    report['cases']['material_exact_names'] = {'error': result.get('error')}
    check('a_replaced_slot_never_passes_for_the_old_name', not result.get('success') and
          'material_values' in (result.get('error') or '') and window().scene.objects['Cube'].material_slots[0].material.name
          == 'Red Plastic')
    other = bpy.data.objects.new('Shared User', cube.data.copy())
    window().scene.collection.objects.link(other)
    other.data.materials[0] = cube.material_slots[0].material
    bpy.ops.ed.undo_push(message='Shared material')
    result, _job = run_program([op('material_principled', handle(cube), {'name': 'Red Plastic', 'inputs': {
        'Base Color': BLUE}})], writes=[cube])
    report['cases']['material_shared'] = {'error': result.get('error')}
    check('a_shared_material_is_not_edited_for_its_other_users',
          not result.get('success') and 'also used elsewhere' in (result.get('error') or ''))
    mesh = other.data
    bpy.data.objects.remove(other)
    bpy.data.meshes.remove(mesh)
    bpy.ops.ed.undo_push(message='Shared material removed')


def linear_spin_samples_between_keys():
    """spin_turntable: frame 60 of a LINEAR 1-120 turn failed twice under require_keyframes."""
    cube = window().scene.objects['Cube']
    turn = lambda frame, z: op('keyframe_insert', handle(cube), {'property': 'rotation_euler', 'value': [0, 0, z],
                                                                 'frame': frame, 'interpolation': 'LINEAR'})
    expected = 2 * math.pi * 59 / 119  # Frame 60 of 1..120 is not the midpoint.
    sample = lambda value, tolerance: {'kind': 'animation', 'target': handle(cube), 'property': 'rotation_euler',
                                       'samples': [{'frame': 60, 'expected': [0, 0, value]}], 'tolerance': tolerance,
                                       'require_keyframes': True}
    result, _job = run_program([turn(1, 0), turn(120, 2 * math.pi)], writes=[cube], checks=[sample(expected, 1e-4)])
    staged = next((item for item in result.get('checks', ()) if item.get('kind') == 'animation'), {})
    cube = window().scene.objects['Cube']
    live = verify(VerificationContext({'$cube': cube}, list(window().scene.objects)),
                  [dict(sample(expected, 1e-4), target='$cube')])
    report['cases']['linear_spin'] = {'status': result.get('status'), 'error': result.get('error'),
                                      'staged': staged.get('checks'), 'live': live['checks']}
    check('linear_spin_commits', result.get('success'))
    check('linear_spin_passes_live', live['passed'])
    staged_actual = staged['checks'][0]['observed']['samples'][0]['actual']
    live_actual = live['checks'][0]['observed']['samples'][0]['actual']
    check('staged_evaluation_equals_live', staged_actual == live_actual and abs(live_actual[2] - expected) < 1e-5)
    undo()
    cube = window().scene.objects['Cube']
    result, _job = run_program([turn(1, 0), turn(120, 2 * math.pi)], writes=[cube], checks=[sample(math.pi, 1e-3)])
    check('a_wrong_value_fails_and_says_what_it_read', not result.get('success')
          and 'observed' in (result.get('error') or '') and '3.115' in result['error'])


def array_row_dimensions():
    """array_row: five 1 m spheres 1.5 m apart are 7 m long, as observations say; the check read 1 m."""
    result, _job = run_program([
        op('create_primitive', None, {'primitive': 'UV_SPHERE', 'alias': 'ball', 'radius': 0.5, 'name': 'Ball'}),
        op('modifier_add', '$ball', {'type': 'ARRAY', 'name': 'Row', 'settings': {
            'count': 5, 'relative_offset_displace': [1.5, 0, 0]}})],
        checks=[{'kind': 'dimensions', 'target': '$ball', 'expected': [7, 1, 1], 'tolerance': 0.001}])
    ball = window().scene.objects.get('Ball')
    report['cases']['array_row'] = {'status': result.get('status'), 'error': result.get('error'),
                                    'dimensions': list(ball.dimensions) if ball else None}
    check('array_row_commits', result.get('success') and ball is not None
          and all(abs(a - b) < 1e-4 for a, b in zip(ball.dimensions, (7, 1, 1))))
    undo()


def typed_torus():
    """donut: a typed torus instead of operator_run (mesh.primitive_torus_add is a Python operator)."""
    result, _job = run_program([op('create_primitive', None, {
        'primitive': 'TORUS', 'alias': 'donut', 'name': 'Donut', 'major_radius': 0.3, 'minor_radius': 0.1,
        'minor_segments': 16, 'location': [4.2, 0, -0.9]})])
    donut = window().scene.objects.get('Donut')
    check('torus_commits', result.get('success') and donut is not None and len(donut.data.vertices) == 768
          and all(abs(a - b) < 1e-4 for a, b in zip(donut.dimensions, (0.8, 0.8, 0.2))))
    undo()


def observed_dependencies_bind():
    """donut turn: unparenting the table legs was refused until their observed parent was "declared"."""
    scene = window().scene
    top = bpy.data.objects.new('Table Top', bpy.data.meshes.new('Table Top Mesh'))
    leg = bpy.data.objects.new('Table Leg', bpy.data.meshes.new('Table Leg Mesh'))
    for obj in (top, leg):
        scene.collection.objects.link(obj)
    leg.parent = top
    bpy.ops.ed.undo_push(message='Survey table baseline')
    queries.invalidate()
    unparent = lambda: [op('parent_set', handle(scene.objects['Table Leg']), {'parent': None, 'keep_transform': False})]

    def context_seeing(revision):
        table = IdentifierTable()
        table.short_handle(handle(scene.objects['Table Top']))
        table.observe({'objects': [{'handle': handle(scene.objects['Table Top']), 'object_revision': revision}]})
        return NS(_identifiers=table)

    result, _job = run_program(unparent(), writes=[scene.objects['Table Leg']])
    check('an_unobserved_parent_is_still_refused', not result.get('success')
          and 'Declare every existing parent' in (result.get('error') or ''))
    result, _job = run_program(unparent(), writes=[scene.objects['Table Leg']], controller=context_seeing('0' * 64))
    check('a_parent_changed_since_the_observation_fails_closed', not result.get('success')
          and 'changed since your latest observation' in (result.get('error') or ''))
    seen = context_seeing(FullStore.object_revision(window(), scene.objects['Table Top']))
    result, _job = run_program(unparent(), writes=[scene.objects['Table Leg']], controller=seen)
    report['cases']['observed_parent'] = {'status': result.get('status'), 'error': result.get('error')}
    check('an_observed_parent_binds_and_the_leg_unparents', result.get('success')
          and window().scene.objects['Table Leg'].parent is None and window().scene.objects['Table Top'] is not None)
    undo()
    for name in ('Table Leg', 'Table Top'):
        bpy.data.objects.remove(window().scene.objects[name])
    bpy.ops.ed.undo_push(message='Survey table removed')


def evaluated(obj):
    mesh = obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data
    return max(len(mesh.vertices), 2 * len(mesh.edges), 4 * len(mesh.polygons), len(mesh.loops))


def budget_is_sound_and_lets_the_survey_stack_through():
    """subdivision / boolean_hole: refused as over budget; the estimate must still bound real evaluation."""
    context = NS(assert_staged=lambda obj: obj, require_private=lambda block: block, owned_ids=[], node_bounds={},
                 resolve=lambda value: value)
    adders = {'cube': lambda: bpy.ops.mesh.primitive_cube_add(), 'ico': lambda: bpy.ops.mesh.primitive_ico_sphere_add(),
              'grid': lambda: bpy.ops.mesh.primitive_grid_add(), 'monkey': lambda: bpy.ops.mesh.primitive_monkey_add(),
              'cone': lambda: bpy.ops.mesh.primitive_cone_add()}
    stacks = {'bevel%d_%s' % (s, miter): [('BEVEL', {'segments': s, 'limit_method': 'NONE', 'width': 0.02,
                                                     'miter_inner': miter})]
              for s in (1, 3, 5, 8) for miter in ('MITER_SHARP', 'MITER_ARC')}
    stacks.update(subsurf=[('SUBSURF', {'levels': 2, 'render_levels': 3})],
                  survey=[('BEVEL', {'segments': 3, 'limit_method': 'NONE', 'width': 0.05}),
                          ('SUBSURF', {'levels': 2, 'render_levels': 3})],
                  mirror=[('MIRROR', {'use_axis': (True, True, True)})], solidify=[('SOLIDIFY', {})])
    worst = {}
    with bpy.context.temp_override(window=window()):
        for mesh_name, add in adders.items():
            for stack_name, stack in stacks.items():
                add()
                obj = bpy.context.active_object
                for kind, settings in stack:
                    modifier = obj.modifiers.new(kind, kind)
                    for key, value in settings.items():
                        setattr(modifier, key, value)
                try:
                    estimate = action_budget._modifier_bound(context, obj)
                except ValueError:
                    estimate = None  # Refused before evaluation: nothing to compare.
                worst[mesh_name + '/' + stack_name] = [evaluated(obj) if estimate else None, estimate]
                bpy.data.objects.remove(obj)
    report['cases']['budget'] = worst
    admitted = [pair for pair in worst.values() if pair[1] is not None]
    check('budget_bounds_real_evaluation', len(admitted) > 40 and all(actual <= estimate for actual, estimate in admitted))
    check('budget_admits_the_survey_stack_on_a_cube', worst['cube/survey'][1] is not None)
    # The survey's hole through the bevelled, subdivided cube now stages and commits. Eval Cube then had 224
    # vertices and 228 faces; the factory cube cut 5 times per edge has 218 and 216.
    cube = window().scene.objects['Cube']
    import bmesh
    mesh = bmesh.new()
    mesh.from_mesh(cube.data)
    bmesh.ops.subdivide_edges(mesh, edges=list(mesh.edges), cuts=5, use_grid_fill=True)
    mesh.to_mesh(cube.data)
    mesh.free()
    cube.data.update()
    with bpy.context.temp_override(window=window()):
        bpy.ops.mesh.primitive_cylinder_add(radius=0.5, depth=3, rotation=(math.pi / 2, 0, 0))
    cutter = bpy.context.active_object
    cutter.name = 'Hole Cutter'
    bpy.ops.ed.undo_push(message='Survey cutter')
    queries.invalidate()
    cube, cutter = window().scene.objects['Cube'], window().scene.objects['Hole Cutter']
    result, _job = run_program([
        op('modifier_add', handle(cube), {'type': 'BEVEL', 'name': 'Bevel', 'settings': {'segments': 3, 'width': 0.05}}),
        op('modifier_add', handle(cube), {'type': 'SUBSURF', 'name': 'Smooth', 'settings': {'levels': 2, 'render_levels': 3}}),
        op('modifier_add', handle(cube), {'type': 'BOOLEAN', 'name': 'Hole', 'settings': {'operand': handle(cutter)}})],
        writes=[cube], reads=[cutter])
    report['cases']['survey_stack'] = {'status': result.get('status'), 'error': result.get('error')}
    check('survey_stack_commits', result.get('success'))
    cube = window().scene.objects['Cube']
    estimate, actual = action_budget._modifier_bound(context, cube), evaluated(cube)
    report['cases']['survey_stack'].update(vertices=len(cube.data.vertices), estimate=estimate, actual=actual)
    check('survey_stack_estimate_bounds_its_real_result', len(cube.data.vertices) == 218 and actual <= estimate
          < action_budget.MAX_EVALUATED_VERTICES)
    with patch.object(action_budget, 'MAX_EVALUATED_VERTICES', evaluated(cube) - 1):
        try:
            action_budget.assert_evaluated(bpy.context.evaluated_depsgraph_get(), [cube])
            refused = False
        except ValueError as error:
            refused = 'Boolean result exceeds' in str(error)
    check('an_evaluated_boolean_over_the_budget_is_refused', refused)
    undo()
    bpy.data.objects.remove(window().scene.objects['Hole Cutter'])
    bpy.ops.ed.undo_push(message='Survey cutter removed')


def sculpt_tools_take_handles():
    """sculpt_rock: angelo_inspect_sculpt answered "Choose an existing polygon mesh" for Eval Cube's handle."""
    from angelo_agent.blender_tools import BlenderExecutor
    from angelo_agent.references import ReferenceStore
    current = window()
    area = next(a for a in current.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=current, area=area, region=region):
        executor = BlenderExecutor(current)
        executor.observation_store = ReferenceStore(OUT, '6a1d4f3e-6d55-4b0e-9a8e-2f6d0f7c3b21')
        by_handle = executor.execute('angelo_inspect_sculpt', {'object': handle(current.scene.objects['Cube']),
                                                               'probes': []})
        by_name = executor.execute('angelo_inspect_sculpt', {'object': 'Cube', 'probes': []})
    check('a_scene_handle_inspects_the_same_mesh_as_its_name',
          by_handle.get('mesh_revision') and by_handle.get('mesh_revision') == by_name.get('mesh_revision'))


def run():
    try:
        native.register()
        bpy.utils.register_class(Commit)
        bpy.context.preferences.edit.use_global_undo = True
        bpy.context.preferences.filepaths.file_preview_type = 'NONE'
        material_edits_in_place()
        material_names_are_exact()
        linear_spin_samples_between_keys()
        array_row_dimensions()
        typed_torus()
        observed_dependencies_bind()
        budget_is_sound_and_lets_the_survey_stack_through()
        sculpt_tools_take_handles()
        report['success'] = True
    except Exception:
        report.update(success=False, error=traceback.format_exc())
    finally:
        native.cleanup_all()
        (OUT/'survey-fixes.json').write_text(json.dumps(report, indent=2, default=str))
        print('ANGELO_SURVEY_FIXES', json.dumps({'success': report.get('success'), 'error': report.get('error')}),
              flush=True)
        bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(run, first_interval=1)
