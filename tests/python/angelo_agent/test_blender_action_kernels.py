# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Factory-startup native regressions, no provider calls or project imports.

Run Blender --background --factory-startup --python this_file. Tests inspect
actual private meshes/modifiers/node graphs/Actions/weights, not just readbacks.
"""

import json
import math
import time
from pathlib import Path
import sys

import bpy
import bmesh

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
from angelo_agent.action_kernels import (StagedActionContext, apply_operation, assert_evaluation_budget,
                                         node_metadata, restore_node_metadata, runtime_capabilities)
from angelo_agent.action_catalog import validate_operation

assert '--factory-startup' in sys.argv and bpy.app.background
checks = []


def check(label, condition=True):
    assert condition, label
    checks.append(label)


def action(kind, parameters=None, target='$cube'):
    return {'kind': kind, 'target': target, 'parameters': parameters or {}}


def reject(context, operation, message=None):
    try:
        apply_operation(context, operation)
    except ValueError as error:
        if message is not None:
            assert message.lower() in str(error).lower(), str(error)
        return
    raise AssertionError('Expected native action refusal: ' + repr(operation))


def stage():
    scene = bpy.data.scenes.new('Kernel Regression Stage')
    collection = bpy.data.collections.new('Kernel Regression Collection')
    scene.collection.children.link(collection)
    return StagedActionContext(scene, collection)


def cube(context, alias='cube'):
    apply_operation(context, action('create_primitive', {'primitive': 'CUBE', 'alias': alias}, None))
    return context.resolve('$' + alias)


def fcurves(action_id):
    return [curve for layer in action_id.layers for strip in layer.strips
            for channelbag in strip.channelbags for curve in channelbag.fcurves]


def graph(levels=(1,)):
    nodes = [{'id': 'input', 'type': 'GROUP_INPUT'}, {'id': 'output', 'type': 'GROUP_OUTPUT'}]
    links, previous = [], 'input'
    for index, level in enumerate(levels):
        identifier = 'sub' + str(index)
        nodes.append({'id': identifier, 'type': 'SUBDIVIDE_MESH', 'inputs': {'Level': level}})
        links.append({'from_node': previous, 'from_socket': 'Geometry' if previous == 'input' else 'Mesh',
                      'to_node': identifier, 'to_socket': 'Mesh'})
        previous = identifier
    links.append({'from_node': previous, 'from_socket': 'Geometry' if previous == 'input' else 'Mesh',
                  'to_node': 'output', 'to_socket': 'Geometry'})
    return {'nodes': nodes, 'links': links}


def main():
    # A context imported by native_programs must remain the exact same class
    # accepted by the public dispatcher after internal module extraction.
    from angelo_agent.action_context import StagedActionContext as ContextImplementation
    from angelo_agent.action_budget import assert_evaluation_budget as BudgetImplementation
    from angelo_agent.action_node_graphs import node_metadata as MetadataImplementation
    assert StagedActionContext is ContextImplementation
    assert assert_evaluation_budget is BudgetImplementation
    assert node_metadata is MetadataImplementation
    live_scene, live_layer = bpy.context.scene, bpy.context.view_layer
    live_active = live_layer.objects.active
    live_selection = {item: item.select_get() for item in live_scene.objects}
    live_objects = tuple(live_scene.objects)
    live_cube = live_scene.objects['Cube']
    original_mesh = live_cube.data
    original_co = [tuple(vertex.co) for vertex in original_mesh.vertices]
    original_transform = live_cube.matrix_world.copy()

    context = stage()
    for index, primitive in enumerate(('CUBE', 'PLANE', 'UV_SPHERE', 'ICO_SPHERE', 'CYLINDER', 'CONE', 'GRID', 'CIRCLE',
                                       'TORUS')):
        params = {'primitive': primitive, 'alias': 'primitive' + str(index)}
        if primitive == 'CIRCLE':
            params['fill'] = True
        result = apply_operation(context, action('create_primitive', params, None))
        obj = context.resolve('$primitive' + str(index))
        check('primitive_' + primitive, result['vertices'] == len(obj.data.vertices) and result['vertices'] > 0)
        if primitive in {'CUBE', 'UV_SPHERE', 'ICO_SPHERE', 'CYLINDER', 'CONE'}:
            check('outward_' + primitive, all(face.normal.dot(face.center) > 0 for face in obj.data.polygons))
        if primitive == 'TORUS':  # Blender's Add > Torus: 48 x 12 quads, normals out of the tube.
            data = obj.data
            from mathutils import Vector
            tube = lambda center: center - Vector((center.x, center.y, 0)).normalized()
            check('torus_topology', (len(data.vertices), len(data.edges), len(data.polygons)) == (576, 1152, 576)
                  and all(len(face.vertices) == 4 for face in data.polygons))
            check('outward_TORUS', all(face.normal.dot(tube(face.center)) > 0 for face in data.polygons))
            check('torus_extent', math.isclose(max(abs(v.co.x) for v in data.vertices), 1.25, abs_tol=1e-5)
                  and math.isclose(max(abs(v.co.z) for v in data.vertices), .25, abs_tol=1e-5))
    cube(context)
    moved = context.resolve('$cube')
    apply_operation(context, action('transform', {'location': [1, 2, 3], 'rotation': [0, 0, .5], 'scale': [2, 2, 2]}))
    apply_operation(context, action('transform', {'mode': 'DELTA', 'location': [1, 0, 0], 'scale': [.5, 1, 1]}))
    check('transform_channels', tuple(moved.location) == (2, 2, 3) and tuple(moved.scale) == (1, 2, 2))
    reject(context, action('transform', {'location': [0, 0, 0]}, 'Cube'), 'outside')
    reject(context, action('transform', {'location': [0, 0, 0]}, 'primitive0'), 'outside')
    check('no_global_name_lookup')

    # assert_staged skips Object.users_collection for a single-user object:
    # every collection link adds a user, so a second link must be refused.
    link_context = stage()
    linked = cube(link_context, 'linked')
    check('single_stage_link_is_single_user', linked.users == 1 and link_context.assert_staged(linked) is linked)
    linked.use_fake_user = True
    check('fake_user_takes_the_exact_fallback', linked.users == 2 and link_context.assert_staged(linked) is linked)
    linked.use_fake_user = False
    live_collection = bpy.data.collections.new('Kernel Regression Live Link')
    live_scene.collection.children.link(live_collection)
    live_collection.objects.link(linked)
    try:
        check('second_collection_link_adds_a_user', linked.users == 2)
        try:
            link_context.assert_staged(linked)
        except ValueError as error:
            check('staged_object_also_linked_live_is_refused', 'live collection' in str(error))
        else:
            check('staged_object_also_linked_live_is_refused', False)
    finally:
        live_collection.objects.unlink(linked)
        live_scene.collection.children.unlink(live_collection)
        bpy.data.collections.remove(live_collection)

    edits = stage(); mesh_obj = cube(edits)
    apply_operation(edits, action('mesh_extrude', {'face_selector': {'normal': [0, 0, 1]}, 'translation': [0, 0, 1]}))
    check('bmesh_extrude_region_counts', len(mesh_obj.data.vertices) == 12 and len(mesh_obj.data.polygons) == 10)
    bm = bmesh.new(); bm.from_mesh(mesh_obj.data)
    check('extrude_stays_closed', all(edge.is_manifold for edge in bm.edges)); bm.free()
    before_counts = (len(mesh_obj.data.vertices), len(mesh_obj.data.polygons))
    reject(edits, action('mesh_inset', {'face_indices': [0]}), 'stale topology')
    check('stale_index_refusal_before_mutation', before_counts == (len(mesh_obj.data.vertices), len(mesh_obj.data.polygons)))
    apply_operation(edits, action('mesh_inset', {'face_selector': {'normal': [0, 0, 1]}, 'thickness': .2}))
    check('bmesh_inset', len(mesh_obj.data.vertices) == 16 and edits.topology_versions[mesh_obj] == 2)
    apply_operation(edits, action('mesh_recalculate_normals', {'face_indices': [0], 'index_scope': 'CURRENT', 'topology_step': 2}))
    check('explicit_current_topology_generation')
    reject(edits, action('mesh_recalculate_normals', {'face_indices': [0], 'index_scope': 'CURRENT', 'topology_step': 1}), 'stale')
    bevels = stage(); bevel_obj = cube(bevels)
    apply_operation(bevels, action('mesh_bevel', {'edge_selector': {'angle_min': 1, 'boundary': 'EXCLUDE'}, 'offset': .1, 'segments': 2}))
    check('rule_based_bevel', len(bevel_obj.data.vertices) > 8 and len(bevel_obj.data.polygons) > 6)
    reject(bevels, action('mesh_bevel', {'edge_indices': [999], 'index_scope': 'CURRENT', 'topology_step': 1}), 'outside')
    merge = stage()
    data = merge.track_id(bpy.data.meshes.new('Selected Merge Mesh'))
    data.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0), (.00001, 0, 0), (1, 1, 0), (0, 1.00001, 0)], [],
                     [(0, 1, 2), (3, 4, 5)])
    merged = merge.track_id(bpy.data.objects.new('Selected Merge', data)); merge.add_object(merged, 'cube')
    apply_operation(merge, action('mesh_merge_by_distance', {'vertex_indices': [0, 3], 'distance': .0001}))
    check('merge_only_explicit_vertices', len(data.vertices) == 5)

    modifiers = stage(); mod_obj = cube(modifiers)
    for name, kind in [('Bevel', 'BEVEL'), ('Solidify', 'SOLIDIFY')]:
        apply_operation(modifiers, action('modifier_add', {'type': kind, 'name': name}))
        check('modifier_' + kind, mod_obj.modifiers[name].type == kind)
    if bpy.app.build_options.opensubdiv:
        apply_operation(modifiers, action('modifier_add', {'type': 'SUBSURF', 'name': 'Subdivision'}))
        check('modifier_SUBSURF', mod_obj.modifiers['Subdivision'].type == 'SUBSURF')
        subdivision = stage(); subdiv_obj = cube(subdivision)
        apply_operation(subdivision, action('modifier_add', {'type': 'SUBSURF', 'name': 'Positive Subdivision',
                                                            'settings': {'levels': 1, 'render_levels': 1}}))
        subdivision.view_layer.update()
        evaluated_subdiv = subdiv_obj.evaluated_get(subdivision.view_layer.depsgraph)
        check('nonzero_SUBSURF_actual_geometry', len(evaluated_subdiv.data.vertices) == 26 and
              len(evaluated_subdiv.data.polygons) == 24 and len(subdiv_obj.data.vertices) == 8)
        apply_operation(subdivision, action('modifier_configure', {'type': 'SUBSURF', 'modifier': 'Positive Subdivision',
                                                                  'settings': {'levels': 2, 'render_levels': 2,
                                                                               'subdivision_type': 'SIMPLE'}}))
        subdivision.view_layer.update()
        evaluated_subdiv = subdiv_obj.evaluated_get(subdivision.view_layer.depsgraph)
        check('nonzero_SUBSURF_configure_actual_geometry', len(evaluated_subdiv.data.vertices) == 98 and
              len(evaluated_subdiv.data.polygons) == 96 and len(subdiv_obj.data.vertices) == 8)
    else:
        reject(modifiers, action('modifier_add', {'type': 'SUBSURF', 'name': 'Subdivision'}), 'unavailable')
        check('unsupported_subdivision_refused_not_silent_noop', mod_obj.modifiers.get('Subdivision') is None)
    apply_operation(modifiers, action('modifier_configure', {'type': 'BEVEL', 'modifier': 'Bevel', 'settings': {'width': .125, 'segments': 1}}))
    check('modifier_configure', abs(mod_obj.modifiers['Bevel'].width - .125) < 1e-6)
    mirror = stage(); mirror_obj = cube(mirror)
    apply_operation(mirror, action('modifier_add', {'type': 'MIRROR', 'settings': {'axes': [True, True, False]}}))
    check('modifier_MIRROR', list(mirror_obj.modifiers[0].use_axis) == [True, True, False])
    boolean = stage(); boolean_obj = cube(boolean); cutter = cube(boolean, 'cutter')
    apply_operation(boolean, action('transform', {'location': [1, 0, 0]}, '$cutter'))
    apply_operation(boolean, action('modifier_add', {'type': 'BOOLEAN', 'settings': {'operand': '$cutter'}}))
    check('boolean_operand_staged', boolean_obj.modifiers[0].object == cutter)
    boolean.aliases['$same'] = boolean_obj
    reject(boolean, action('modifier_add', {'type': 'BOOLEAN', 'name': 'Self Alias', 'settings': {'operand': '$same'}}))
    explosion = stage(); explosion_obj = cube(explosion)
    for index in range(4):
        apply_operation(explosion, action('modifier_add', {'type': 'MIRROR', 'name': 'Mirror ' + str(index),
                                                          'settings': {'axes': [True, True, True]}}))
    reject(explosion, action('modifier_add', {'type': 'MIRROR', 'name': 'Mirror Explosion',
                                            'settings': {'axes': [True, True, True]}}), 'budget')
    check('modifier_explosion_preflight', len(explosion_obj.modifiers) == 4)

    appearance = stage(); material_obj = cube(appearance)
    apply_operation(appearance, action('material_principled', {'inputs': {'Base Color': [.1, .2, .3, 1], 'Roughness': .3, 'Metallic': .7}}))
    principled = next(node for node in material_obj.data.materials[0].node_tree.nodes if node.type == 'BSDF_PRINCIPLED')
    check('principled_assignment', abs(principled.inputs['Metallic'].default_value - .7) < 1e-6 and all(face.material_index == 0 for face in material_obj.data.polygons))
    shader_graph = {'nodes': [{'id': 'coord', 'type': 'TEX_COORD'}, {'id': 'noise', 'type': 'TEX_NOISE'},
                              {'id': 'bump', 'type': 'BUMP'}, {'id': 'surface', 'type': 'BSDF_PRINCIPLED'},
                              {'id': 'output', 'type': 'OUTPUT_MATERIAL'}], 'links': [
        {'from_node': 'coord', 'from_socket': 'Generated', 'to_node': 'noise', 'to_socket': 'Vector'},
        {'from_node': 'noise', 'from_socket': 'Color', 'to_node': 'surface', 'to_socket': 'Base Color'},
        {'from_node': 'noise', 'from_socket': 'Fac', 'to_node': 'bump', 'to_socket': 'Height'},
        {'from_node': 'bump', 'from_socket': 'Normal', 'to_node': 'surface', 'to_socket': 'Normal'},
        {'from_node': 'surface', 'from_socket': 'BSDF', 'to_node': 'output', 'to_socket': 'Surface'}]}
    apply_operation(appearance, action('shader_nodes', shader_graph))
    check('shader_graph_typed_links', len(material_obj.data.materials[1].node_tree.links) == 5)
    geometry = stage(); geo_obj = cube(geometry)
    primitive_graph = {'nodes': [{'id': 'grid', 'type': 'MESH_GRID'},
                                 {'id': 'transform', 'type': 'TRANSFORM_GEOMETRY', 'inputs': {'Translation': [0, 0, 2]}},
                                 {'id': 'output', 'type': 'GROUP_OUTPUT'}], 'links': [
        {'from_node': 'grid', 'from_socket': 'Mesh', 'to_node': 'transform', 'to_socket': 'Geometry'},
        {'from_node': 'transform', 'from_socket': 'Geometry', 'to_node': 'output', 'to_socket': 'Geometry'}]}
    apply_operation(geometry, action('geometry_nodes', primitive_graph))
    geometry.view_layer.update()
    evaluated = geo_obj.evaluated_get(geometry.view_layer.depsgraph)
    check('geometry_nodes_actual_evaluation', len(evaluated.data.vertices) == 100 and
          all(abs(vertex.co.z - 2) < 1e-6 for vertex in evaluated.data.vertices))
    if bpy.app.build_options.opensubdiv:
        subdiv_nodes = stage(); subdiv_node_obj = cube(subdiv_nodes)
        apply_operation(subdiv_nodes, action('geometry_nodes', dict(graph((1,)), name='Positive Node Subdivision One')))
        subdiv_nodes.view_layer.update()
        evaluated_nodes = subdiv_node_obj.evaluated_get(subdiv_nodes.view_layer.depsgraph)
        check('nonzero_GN_subdivision_actual_geometry', len(evaluated_nodes.data.vertices) == 26 and
              len(evaluated_nodes.data.polygons) == 24 and len(subdiv_node_obj.data.vertices) == 8)
        apply_operation(subdiv_nodes, action('geometry_nodes', dict(graph((1,)), name='Positive Node Subdivision Two')))
        subdiv_nodes.view_layer.update()
        evaluated_nodes = subdiv_node_obj.evaluated_get(subdiv_nodes.view_layer.depsgraph)
        check('stacked_nonzero_GN_subdivision_actual_geometry', len(evaluated_nodes.data.vertices) == 98 and
              len(evaluated_nodes.data.polygons) == 96 and len(subdiv_node_obj.data.vertices) == 8)
        check('stacked_GN_subdivision_current_input_budget', assert_evaluation_budget(subdiv_nodes, subdiv_node_obj) == 384)
    else:
        reject(geometry, action('geometry_nodes', dict(graph(), name='Unsupported Subdivision')), 'unavailable')
        check('unsupported_node_refused_not_silent_noop')
    allocations = len(geometry.created_ids)
    reject(geometry, action('geometry_nodes', dict(graph((2, 2, 2, 2)), name='Explosion')), 'budget')
    check('node_explosion_before_allocation', len(geometry.created_ids) == allocations and len(geo_obj.modifiers) == 1)
    metadata = node_metadata(geometry)
    geometry.node_graphs.clear(); geometry.node_bounds.clear()
    restore_node_metadata(geometry, metadata)
    check('node_metadata_restore_and_recompute', assert_evaluation_budget(geometry, geo_obj) == 100)
    geometry.node_graphs.clear(); geometry.node_bounds.clear()
    check('persisted_node_provenance_audited', assert_evaluation_budget(geometry, geo_obj) == 100)
    geo_obj.modifiers[0].node_group.nodes['grid'].inputs['Vertices X'].default_value = 128
    reject(geometry, action('transform', {'location': [1, 0, 0]}), 'approved bounded value')
    check('changed_node_graph_refused_before_evaluation', tuple(geo_obj.location) == (0, 0, 0))
    geo_obj.modifiers[0].node_group.nodes['grid'].inputs['Vertices X'].default_value = 10
    growth = stage(); growth_obj = cube(growth)
    for index in range(4):
        apply_operation(growth, action('modifier_add', {'type': 'MIRROR', 'name': 'Growth Mirror ' + str(index),
                                                       'settings': {'axes': [True, True, True]}}))
    apply_operation(growth, action('geometry_nodes', graph(())))
    reject(growth, action('mesh_bevel', {'edge_indices': list(range(12)), 'segments': 2}), 'budget')
    check('topology_growth_rebinds_existing_graph_before_mesh_commit', len(growth_obj.data.vertices) == 8)
    unbounded = stage(); unbounded_obj = cube(unbounded)
    unbounded_obj.modifiers.new('Unknown Array', 'ARRAY').count = 100000
    reject(unbounded, action('transform', {'location': [1, 2, 3]}), 'outside')
    reject(unbounded, action('uv_project'), 'outside')
    check('unknown_existing_stack_refused_for_transform_and_uv', tuple(unbounded_obj.location) == (0, 0, 0) and not unbounded_obj.data.uv_layers)
    driver = stage(); driver_obj = cube(driver)
    driver_obj.driver_add('location', 0).driver.expression = '1.0'
    reject(driver, action('uv_project'), 'drivers')
    check('preexisting_driver_not_evaluated')
    image_count, allocation_count = len(bpy.data.images), len(appearance.created_ids)
    reject(appearance, action('shader_nodes', {'nodes': [{'id': 'external', 'type': 'TEX_IMAGE'},
                                                       {'id': 'output', 'type': 'OUTPUT_MATERIAL'}],
                                               'links': shader_graph['links']}))
    check('shader_file_node_refused_before_allocation', len(bpy.data.images) == image_count and len(appearance.created_ids) == allocation_count)
    check('runtime_capabilities_exact', runtime_capabilities()['runtime_features']['opensubdiv'] == bool(bpy.app.build_options.opensubdiv))

    uv = stage(); uv_obj = cube(uv)
    apply_operation(uv, action('uv_project', {'method': 'CUBE'}))
    check('uv_projection_finite', len(uv_obj.data.uv_layers['UVMap'].data) == 24 and
          all(math.isfinite(value) for loop in uv_obj.data.uv_layers['UVMap'].data for value in loop.uv))
    selection = [[element.select for element in sequence] for sequence in (uv_obj.data.vertices, uv_obj.data.edges, uv_obj.data.polygons)]
    apply_operation(uv, action('uv_unwrap', {'uv_map': 'Unwrapped', 'seam_edge_indices': list(range(12))}))
    check('native_uv_unwrap', uv_obj.mode == 'OBJECT' and len(uv_obj.data.uv_layers['Unwrapped'].data) == 24)
    check('unwrap_changes_actual_uv_values', max(loop.uv.x for loop in uv_obj.data.uv_layers['Unwrapped'].data) > .1)
    check('unwrap_selection_restored', selection == [[element.select for element in sequence] for sequence in
                                                   (uv_obj.data.vertices, uv_obj.data.edges, uv_obj.data.polygons)])
    check('stage_active_restored', uv.view_layer.objects.active is None)
    dense = stage()
    apply_operation(dense, action('create_primitive', {'primitive': 'GRID', 'x_segments': 120, 'y_segments': 120, 'alias': 'dense'}, None))
    started = time.perf_counter()
    reject(dense, action('uv_unwrap', {}, '$dense'), 'WORK_BOUND')  # 58,081 elements, all selected: 3.4e9 > 2.5e9.
    check('unwrap_work_bound_refuses_a_dense_mesh_at_once', time.perf_counter() - started < 2 and not dense.resolve('$dense').data.uv_layers)
    apply_operation(dense, action('uv_unwrap', {'face_indices': list(range(64))}, '$dense'))
    check('unwrap_work_bound_allows_a_small_selection_of_it', len(dense.resolve('$dense').data.uv_layers['UVMap'].data) == 4 * 14400)

    hierarchy = stage(); child = cube(hierarchy); parent = cube(hierarchy, 'parent'); leader = cube(hierarchy, 'leader')
    apply_operation(hierarchy, action('parent_set', {'parent': '$parent'}))
    check('staged_parenting', child.parent == parent)
    reject(hierarchy, action('parent_set', {'parent': '$cube'}, '$parent'), 'cycle')
    for kind in ('COPY_LOCATION', 'COPY_ROTATION', 'COPY_SCALE', 'TRACK_TO'):
        apply_operation(hierarchy, action('constraint_add', {'type': kind, 'target': '$leader', 'name': kind}))
        check('constraint_' + kind, child.constraints[kind].target == leader)
    apply_operation(hierarchy, action('constraint_add', {'type': 'LIMIT_LOCATION', 'settings': {
        'minimum': [-1, -1, -1], 'maximum': [1, 1, 1], 'use_min': [True] * 3, 'use_max': [True] * 3}}))
    check('constraint_LIMIT_LOCATION', child.constraints[-1].use_min_z)
    reject(hierarchy, action('constraint_add', {'type': 'COPY_LOCATION', 'target': '$cube', 'name': 'Constraint Cycle'}, '$leader'), 'cycle')
    check('transitive_constraint_cycle_refused')

    animation = stage(); animated = cube(animation)
    original_live_frame = (live_scene.frame_current, live_scene.frame_subframe)
    live_scene.frame_current, live_scene.frame_subframe = 37, .25
    animation.scene.frame_current, animation.scene.frame_subframe = 9, .5
    live_frame = (live_scene.frame_current, live_scene.frame_subframe)
    stage_frame = (animation.scene.frame_current, animation.scene.frame_subframe)
    try:
        # A window override inside the stage's scene and view layer (the
        # program executor nests them the other way, program_objects._override).
        # It clears scene but not the inherited view_layer, which Blender then
        # resolves from the artist's scene; the kernel must bind its private
        # scene/layer pair again for RNA keyframe insertion. Given in one call,
        # temp_override refuses the pair (test_blender_context_override_pair).
        with bpy.context.temp_override(scene=animation.scene, view_layer=animation.view_layer), \
                bpy.context.temp_override(window=bpy.context.window):
            for frame, value in [(1, [0, 0, 0]), (24, [1, 2, 3])]:
                apply_operation(animation, action('keyframe_insert', {'property': 'location', 'value': value,
                                                                       'frame': frame, 'interpolation': 'LINEAR'}))
        check('keyframes_preserve_host_and_stage_frame', live_frame == (live_scene.frame_current, live_scene.frame_subframe) and
              stage_frame == (animation.scene.frame_current, animation.scene.frame_subframe))
        check('keyframes_nested_window_override_restored', bpy.context.scene == live_scene and bpy.context.view_layer == live_layer and
              bpy.context.window.scene == live_scene and live_layer.objects.active == live_active and
              all(item.select_get() == selected for item, selected in live_selection.items()))
    finally:
        live_scene.frame_current, live_scene.frame_subframe = original_live_frame
    curves = fcurves(animated.animation_data.action)
    check('native_transform_keyframes', len(curves) == 3 and all(len(curve.keyframe_points) == 2 for curve in curves))
    check('keyframe_interpolation', all(key.interpolation == 'LINEAR' for curve in curves for key in curve.keyframe_points))
    check('new_action_journaled', animated.animation_data.action in animation.created_ids)

    rigging = stage(); rig_mesh = cube(rigging)
    apply_operation(rigging, action('armature_create', {'alias': 'rig', 'bones': [
        {'name': 'Root', 'head': [0, 0, 0], 'tail': [0, 0, 1]},
        {'name': 'Tip', 'head': [0, 0, 1], 'tail': [0, 0, 2], 'parent': 'Root', 'connected': True}]}, None))
    rig = rigging.resolve('$rig')
    check('bounded_armature_bones', len(rig.data.bones) == 2 and rig.data.bones['Tip'].parent == rig.data.bones['Root'])
    apply_operation(rigging, action('armature_bind', {'armature': '$rig', 'groups': [
        {'bone': 'Root', 'weights': [{'vertex': index, 'weight': 1} for index in range(4)]},
        {'bone': 'Tip', 'weights': [{'vertex': index, 'weight': 1} for index in range(4, 8)]}]}))
    check('explicit_rig_weights', abs(rig_mesh.vertex_groups['Tip'].weight(7) - 1) < 1e-6 and rig_mesh.modifiers[0].object == rig)

    isolation = stage()
    shared = live_cube.copy(); isolation.track_id(shared); isolation.owned_ids.add(shared.data)
    isolation.add_object(shared, 'shared')
    reject(isolation, action('mesh_bevel', {'edge_indices': [0]}, '$shared'), 'single-user')
    check('shared_mesh_refused')
    live_action = animated.animation_data.action
    shared_anim = animated.copy(); shared_anim.data = isolation.track_id(animated.data.copy())
    isolation.track_id(shared_anim); isolation.add_object(shared_anim, 'shared_animation')
    reject(isolation, action('keyframe_insert', {'property': 'location', 'value': [3, 2, 1]}, '$shared_animation'), 'single-user')
    check('shared_action_refused', sum(len(curve.keyframe_points) for curve in fcurves(live_action)) == 6)
    with bpy.context.temp_override(active_object=live_cube, object=live_cube):
        bpy.ops.object.mode_set(mode='EDIT')
    try:
        reject(uv, action('uv_unwrap', {'uv_map': 'Artist Mode Guard'}), 'artist')
        check('artist_edit_mode_not_interrupted', live_cube.mode == 'EDIT' and uv_obj.mode == 'OBJECT')
    finally:
        with bpy.context.temp_override(active_object=live_cube, object=live_cube):
            bpy.ops.object.mode_set(mode='OBJECT')
    check('live_scene_objects_unchanged', tuple(live_scene.objects) == live_objects)
    check('live_mesh_unchanged', live_cube.data == original_mesh and [tuple(vertex.co) for vertex in original_mesh.vertices] == original_co)
    check('live_transform_unchanged', live_cube.matrix_world == original_transform)
    check('live_context_unchanged', bpy.context.scene == live_scene and live_layer.objects.active == live_active and
          all(item.select_get() == selected for item, selected in live_selection.items()) and live_cube.mode == 'OBJECT')
    build_features = {name: getattr(bpy.app.build_options, name) for name in dir(bpy.app.build_options)
                      if not name.startswith('_') and isinstance(getattr(bpy.app.build_options, name), bool)}
    print('ANGELO_ACTION_KERNELS_TEST_OK', json.dumps({
        'checks': checks, 'count': len(checks), 'paid_requests': 0,
        'binary_path': bpy.app.binary_path, 'version': bpy.app.version_string,
        'build_hash': bpy.app.build_hash.decode(), 'build_features': build_features}))


if __name__ == '__main__':
    main()
