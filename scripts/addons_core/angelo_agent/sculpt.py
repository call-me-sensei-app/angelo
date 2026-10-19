# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Main-thread native sculpting and measured visual feedback. No UI automation."""

import hashlib
import json
import math
import os
import time
from collections import OrderedDict

import bpy
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

from .input_policy import interaction_active
from .legacy_names import SCULPT_REGIONS, read_property
from .sculpt_preflight import SculptPreflightError, before_an_edit, named
from .sculpt_spec import (MAX_VERTICES, SCULPT_MUTATIONS, ANCHORED, resample_paths,
                          multires_working_points, depth_match_tolerance)

_surfaces = OrderedDict()
VIEW_DIRECTIONS = {"FRONT": (0, -1, 0), "BACK": (0, 1, 0), "RIGHT": (1, 0, 0),
                   "TOP": (0, 0, 1), "THREE_QUARTER": (1, -1, .7)}


def target(window, name, mutate=False):
    if interaction_active(window):
        raise RuntimeError("Finish the current Blender tool before observing or editing the sculpt surface")
    obj = named(window, name)
    if obj is None or obj.type != 'MESH' or not len(obj.data.polygons):
        raise RuntimeError("Choose an existing polygon mesh in this Blender scene")
    if mutate:
        if obj.library or obj.data.library or not obj.is_editable or not obj.data.is_editable:
            raise RuntimeError("Sculpt a local editable object and mesh, not a linked asset")
        if obj.data.users != 1:
            raise RuntimeError("This mesh is shared by multiple objects. Make it single-user explicitly before sculpting")
        if obj.data.shape_keys:
            raise RuntimeError("Bounded sculpt passes don't edit shape keys. Use an explicitly reviewed shape-key workflow")
        if obj.hide_get(view_layer=window.view_layer) or not obj.visible_get(view_layer=window.view_layer):
            raise RuntimeError("Unhide the target in the bound view layer before sculpting")
        if obj.use_dynamic_topology_sculpting:
            raise RuntimeError("Disable dynamic topology first; Angelo preserves topology, UVs and vertex groups")
        if any(m.type != 'MULTIRES' and m.show_viewport for m in obj.modifiers):
            raise RuntimeError("Use a sculpt mesh with only Multires visible. Other modifiers need an explicit reviewed preparation, not automatic application")
        # Upstream brushes compensate using LOCAL scale, even if a parent's
        # inverse scale makes the world transform uniform. Negative components
        # can reverse Add strokes. Reject both at the host and native boundary;
        # never silently apply transforms to the user's mesh.
        local_scale = list(obj.scale)
        if min(local_scale) <= 0 or obj.matrix_world.to_3x3().determinant() <= 0:
            raise RuntimeError("Apply negative/reflected object or inherited scale explicitly before sculpting; Angelo won't alter transforms silently")
        if max(local_scale) - min(local_scale) > max(local_scale) * 1e-4:
            raise RuntimeError("Apply non-uniform local scale explicitly before sculpting, even when a parent compensates it")
        # Object-space brush spheres require a uniform, invertible WORLD transform.
        # Reject parent-induced shear too.
        columns = [obj.matrix_world.to_3x3().col[i] for i in range(3)]
        lengths = [c.length for c in columns]
        if min(lengths) < 1e-6 or max(lengths) - min(lengths) > max(lengths) * 1e-4 or any(
                abs(columns[i].dot(columns[j])) > lengths[i] * lengths[j] * 1e-4 for i in range(3) for j in range(i)):
            raise RuntimeError("Apply non-uniform scale/shear explicitly before sculpting; Angelo won't alter transforms silently")
        if interaction_active(window):
            raise RuntimeError("Finish the current Blender tool before starting an agent sculpt pass")
    return obj


def dense_controls(obj):
    """Observe level settings without exporting an over-budget live grid.

    This revision authorizes only preparation without subdivision / selecting
    an existing working level. It is never a geometry-edit revision.
    """
    visible = [m for m in obj.modifiers if m.show_viewport]
    if len(visible)!=1 or visible[0].type!='MULTIRES':
        return None
    modifier=visible[0]
    level=modifier.sculpt_levels if obj.mode=='SCULPT' else modifier.levels
    count=multires_working_points(len(obj.data.vertices),len(obj.data.loops),level)
    if count<=MAX_VERTICES:
        return None
    config={'scope':'MULTIRES_CONTROLS_ONLY','object':obj.session_uid,'mesh':obj.data.session_uid,
            'mode':obj.mode,'base_vertices':len(obj.data.vertices),'base_corners':len(obj.data.loops),
            'matrix':[v for row in obj.matrix_world for v in row],
            'levels':[modifier.levels,modifier.sculpt_levels,modifier.total_levels]}
    revision=hashlib.blake2b(json.dumps(config,sort_keys=True,allow_nan=False).encode(),digest_size=32).hexdigest()
    corners=np.asarray([obj.matrix_world @ Vector(p) for p in obj.bound_box],dtype=np.float32)
    return {'controls_only':True,'revision':revision,'vertices':count,'faces':len(obj.data.polygons),
            'bounds':[corners.min(axis=0).tolist(),corners.max(axis=0).tolist()]}


def check_observation_scope(state, name, args):
    if state.get('controls_only') and name not in {'angelo_inspect_sculpt','angelo_sculpt_resolution'} and not (
            name=='angelo_prepare_sculpt' and args['add_levels']==0):
        raise SculptPreflightError('Lower the existing Multires working level before surface observations or sculpt edits')


def snapshot(window, obj, build_surface=False):
    """Bulk read only the target, not every mesh in the scene after every dab.

    Live grid geometry includes Multires displacement. The digest also includes
    topology, masks, hidden faces, world transforms and subdivision settings, so
    an approval can't apply to a stale/different surface.
    """
    controls = dense_controls(obj)
    if controls:
        return controls
    depsgraph = window.view_layer.depsgraph
    has_modifiers = any(m.show_viewport for m in obj.modifiers)
    live_grids = has_modifiers and obj.mode == 'SCULPT' and any(m.type == 'MULTIRES' and m.show_viewport for m in obj.modifiers)
    if live_grids:
        if not hasattr(obj, 'angelo_sculpt_mesh'):
            raise SculptPreflightError("Multires observations need the compiled Angelo sculpt engine", code="UNSUPPORTED")
        mesh = obj.angelo_sculpt_mesh()
        evaluated = None
    else:
        evaluated = obj.evaluated_get(depsgraph) if has_modifiers else None
        # Base-mesh sculpting writes original vertices directly; the evaluated
        # mesh is intentionally not rebuilt by every upstream sculpt stroke.
        mesh = evaluated.to_mesh(preserve_all_data_layers=False, depsgraph=depsgraph) if evaluated else obj.data
    try:
        count = len(mesh.vertices)
        if count > MAX_VERTICES or not count:
            raise RuntimeError("Use a sculpt level with 1-1,000,000 evaluated points; higher levels need a lower working resolution")
        packed_positions = getattr(mesh,'angelo_sculpt_positions',None)
        if packed_positions is not None:
            positions = np.frombuffer(packed_positions,dtype=np.float32).reshape(count,3)
            topology = mesh.angelo_sculpt_topology
            expected_bytes = (len(mesh.loops)+len(mesh.polygons)+1)*4
            if len(topology) != expected_bytes:
                raise RuntimeError('The native topology observation is incomplete')
        else:
            positions = np.empty((count,3),dtype=np.float32)
            mesh.vertices.foreach_get('co',positions.reshape(-1))
            corners = np.empty(len(mesh.loops),dtype=np.int32)
            sizes = np.empty(len(mesh.polygons),dtype=np.int32)
            mesh.loops.foreach_get('vertex_index',corners)
            mesh.polygons.foreach_get('loop_total',sizes)
            offsets = np.empty(len(sizes)+1,dtype=np.int32)
            offsets[0]=0
            np.cumsum(sizes,out=offsets[1:])
            topology = corners.tobytes()+offsets.tobytes()
        if not np.isfinite(positions).all():
            raise RuntimeError('The target contains non-finite geometry; repair it before sculpting')
        digest = hashlib.blake2b(positions,digest_size=32)
        digest.update(topology)
        topology_digest = hashlib.blake2b(topology,digest_size=32)
        topology_digest.update(str(count).encode())
        topology_revision = topology_digest.hexdigest()
        masks = np.zeros(count, dtype=np.float32)
        hidden_faces = 0
        for name, dtype, prop in (('.sculpt_mask', np.float32, 'value'), ('.hide_vert', np.bool_, 'value'),
                                  ('.hide_poly', np.bool_, 'value'), ('.sculpt_face_set', np.int32, 'value')):
            attribute = mesh.attributes.get(name)
            if attribute:
                values = np.empty(len(attribute.data), dtype=dtype)
                attribute.data.foreach_get(prop, values)
                digest.update(name.encode())
                digest.update(values)
                if name == '.sculpt_mask' and len(values) == count:
                    masks = values
                elif name == '.hide_poly':
                    hidden_faces = int(np.count_nonzero(values))
        matrix = obj.matrix_world
        config = {"fingerprint":"blake2b-256-v1", "surface_counts":[count,len(mesh.loops),len(mesh.polygons)],
                  "object_uid": obj.session_uid, "mesh_uid": obj.data.session_uid,
                  "matrix": [v for row in matrix for v in row],
                  "modifiers": [(m.type, m.show_viewport, getattr(m, 'levels', None), getattr(m, 'sculpt_levels', None),
                                 getattr(m, 'total_levels', None)) for m in obj.modifiers]}
        config['regions'] = read_property(obj, SCULPT_REGIONS, '')
        digest.update(json.dumps(config, sort_keys=True, allow_nan=False).encode())
        world = positions @ np.asarray(matrix.to_3x3(), dtype=np.float32).T + np.asarray(matrix.translation, dtype=np.float32)
        low, high = world.min(axis=0), world.max(axis=0)
        revision = digest.hexdigest()
        key = (obj.session_uid, revision)
        if build_surface and not native_surface_available(obj) and key not in _surfaces:
            if not hasattr(BVHTree, 'FromMesh'):
                raise RuntimeError('This needs the compiled Angelo native surface-query bridge')
            _surfaces[key] = BVHTree.FromMesh(mesh)
        if key in _surfaces:
            _surfaces.move_to_end(key)
        while len(_surfaces) > 2:
            _surfaces.popitem(last=False)
        return {"revision": revision, "topology_revision": topology_revision, "positions": positions, "world": world,
                "vertices": count, "faces": len(mesh.polygons), "bounds": [low.tolist(), high.tolist()],
                "mask_mean": float(masks.mean()), "masked_points": int(np.count_nonzero(masks > .001)),
                "fully_masked_points": int(np.count_nonzero(masks > .999)), "hidden_faces": hidden_faces}
    finally:
        if evaluated:
            evaluated.to_mesh_clear()
        elif live_grids:
            obj.to_mesh_clear()


def native_surface_available(obj):
    return (obj.mode == 'SCULPT' and hasattr(obj, 'angelo_sculpt_query') and not obj.use_dynamic_topology_sculpting
            and not any(m.type != 'MULTIRES' and m.show_viewport for m in obj.modifiers))


class NativeSurface:
    """Live PBVH access, no owned/cached native pointers or secondary BVH."""
    def __init__(self, obj):
        self.obj = obj

    def find_nearest(self, position, distance=1e10):
        hit, co, normal, face, vertex = self.obj.angelo_sculpt_query(position, distance=distance)
        if not hit:
            return None, None, None, None
        return Vector(co), Vector(normal), face, (Vector(co)-Vector(position)).length

    def ray_cast(self, origin, direction, distance=1e10):
        hit, co, normal, face, vertex = self.obj.angelo_sculpt_query(origin, direction=direction, distance=distance)
        if not hit:
            return None, None, None, None
        return Vector(co), Vector(normal), face, (Vector(co)-Vector(origin)).length


def surface(window, obj, state):
    if native_surface_available(obj):
        return NativeSurface(obj)
    key = (obj.session_uid, state['revision'])
    if key not in _surfaces:
        # Recreate only from a live snapshot, never reevaluate a shallow sculpt
        # Object with FromObject (it can replace the real session's CCG pointer).
        fresh = snapshot(window, obj, build_surface=True)
        if fresh['revision'] != state['revision']:
            raise RuntimeError('The surface changed while preparing its spatial query')
    _surfaces.move_to_end(key)
    while len(_surfaces) > 2:
        _surfaces.popitem(last=False)
    return _surfaces[key]


def nearest(obj, bvh, position):
    inverse = obj.matrix_world.inverted()
    location, normal, face, distance = bvh.find_nearest(inverse @ Vector(position))
    if location is None:
        raise RuntimeError("No surface was found near this point")
    world = obj.matrix_world @ location
    world_normal = (inverse.to_3x3().transposed() @ normal).normalized()
    return {"position": list(world), "normal": list(world_normal), "face": int(face),
            "distance": (world - Vector(position)).length}, location


def public_state(obj, state):
    multires = next((m for m in obj.modifiers if m.type == 'MULTIRES'), None)
    low, high = state['bounds']
    diagonal = math.dist(low, high)
    if state.get('controls_only'):
        return {'object':obj.name,'mesh_revision':state['revision'],'revision_scope':'MULTIRES_CONTROLS_ONLY',
                'requires_lower_working_level':True,'estimated_working_points':state['vertices'],
                'bounds_world':{'min':low,'max':high},'diagonal_world':diagonal,'max_dabs_this_mesh':0,
                'mode':obj.mode,'multires':{'levels':multires.levels,'sculpt_levels':multires.sculpt_levels,'total_levels':multires.total_levels},
                'note':'Settings-only observation: prepare with zero new levels if needed, then select a bounded existing working level. No geometry or masks were sampled.'}
    return {"object": obj.name, "mesh_revision": state['revision'], "vertices": state['vertices'],
            "faces": state['faces'], "bounds_world": {"min": low, "max": high},
            "center_world": [(a+b)/2 for a, b in zip(low, high)], "diagonal_world": diagonal,
            "suggested_form_radius": diagonal * .12, "suggested_detail_radius": diagonal * .025,
            "mask_mean": round(state['mask_mean'], 6), "masked_points": state['masked_points'],
            "fully_masked_points": state['fully_masked_points'], "hidden_faces": state['hidden_faces'],
            "multires": {"levels": multires.levels, "sculpt_levels": multires.sculpt_levels, "total_levels": multires.total_levels} if multires else None,
            "mode": obj.mode, "dyntopo": obj.use_dynamic_topology_sculpting,
            "modifiers": [{"name": m.name, "type": m.type, "visible": m.show_viewport} for m in obj.modifiers],
            "coordinate_space": "WORLD; symmetry axes are LOCAL to the object",
            "max_dabs_this_mesh": min(512, max(16, 32000000 // state['vertices']))}


def change_report(window, obj, fields):
    return {"modified": [{"name": obj.name, "fields": fields}] if fields else [], "added": [], "removed": [],
            "object_count_before": len(window.scene.objects), "object_count_after": len(window.scene.objects),
            "truncated": False, "scope": "Target sculpt surface, mode and Multires read-back; external effects are not performed"}


def partial_edit(window, obj, before, error, stage, fields):
    """Do not auto-Undo (that could revert unrelated user work). Retain evidence
    and recovery instructions even if the failed read-back still cannot run.
    These fields are POSSIBLE changes, not a fabricated successful measurement.
    """
    result = {"error": str(error), "success": False, "object": obj.name,
              "partial_changes_possible": True, "mutation_started": True,
              "mutation_stage": stage, "mesh_revision_before": before['revision'],
              "changes": change_report(window, obj, fields),
              "recovery": "Stop and inspect the target. Restore the pre-request snapshot, or check Blender's Undo history before undoing the last operation. Do not automatically retry; partial scene changes may remain."}
    result['changes'].update(uncertain=True, scope="Possible changes to this sculpt target and active selection; final verification failed")
    try:
        result['current_state'] = {"mode": obj.mode,
            "active_object": window.view_layer.objects.active.name if window.view_layer.objects.active else None,
            "multires": [{"name": m.name, "levels": m.levels, "sculpt_levels": m.sculpt_levels,
                          "total_levels": m.total_levels} for m in obj.modifiers if m.type == 'MULTIRES']}
        result['verified'] = public_state(obj, snapshot(window, obj))
    except Exception as readback_error:
        result['readback_error'] = str(readback_error)
    return result


def precondition(window, name, args):
    """What a sculpt edit's approval binds. Nothing has run: any refusal here says so (partial_changes_possible
    False), so it never holds the request's further edits as a failure inside an edit would."""
    with before_an_edit():
        obj = target(window, args['object'], mutate=True)
        state = snapshot(window, obj)
        check_observation_scope(state,name,args)
        if state['revision'] != args['mesh_revision']:
            raise SculptPreflightError("The sculpt surface changed. Inspect it again; never reuse a stale mesh_revision",
                                       code="STALE_REVISION")
    return (obj.session_uid, state['revision'], obj.mode, window.view_layer.objects.active.session_uid if window.view_layer.objects.active else None)


def prepare(window, obj, args, before):
    if bpy.context.mode not in {'OBJECT', 'SCULPT'} or (bpy.context.mode == 'SCULPT' and bpy.context.active_object != obj):
        raise SculptPreflightError("Finish editing the other object before preparing this sculpt target")
    multires = next((m for m in obj.modifiers if m.type == 'MULTIRES'), None)
    if sum(m.type == 'MULTIRES' for m in obj.modifiers) > 1:
        raise SculptPreflightError("Use only one Multires modifier on a sculpt target")
    add_levels = args['add_levels']
    if add_levels and not bpy.app.build_options.opensubdiv:
        raise SculptPreflightError("This build lacks OpenSubdiv; rebuild Angelo with WITH_OPENSUBDIV=ON before adding sculpt detail",
                                  code="UNSUPPORTED")
    # Subdivision appends to TOTAL levels, not the low-resolution working view.
    # Check the destination CCG grids before selection/mode/modifier changes.
    destination = (multires.total_levels if multires else 0) + add_levels
    points = multires_working_points(len(obj.data.vertices), len(obj.data.loops), destination)
    if add_levels and points > MAX_VERTICES:
        raise SculptPreflightError("These subdivisions exceed the one-million-point working budget; add fewer levels")
    working_level = (multires.sculpt_levels if obj.mode == 'SCULPT' else multires.levels) if multires else 0
    if multires and add_levels and multires.total_levels > working_level:
        raise SculptPreflightError("Higher Multires levels already exist. Select them explicitly rather than adding more")
    fields, stage = ['sculpt_mode', 'active_selection'], 'Select sculpt target'
    try:
        was_sculpt = obj.mode == 'SCULPT'
        if was_sculpt and add_levels:
            stage = 'Leave Sculpt Mode for subdivision'
            bpy.ops.object.mode_set(mode='OBJECT')
        if not was_sculpt:
            for selected in list(window.view_layer.objects):
                if selected.select_get(view_layer=window.view_layer):
                    selected.select_set(False, view_layer=window.view_layer)
            obj.select_set(True, view_layer=window.view_layer)
            window.view_layer.objects.active = obj
        if add_levels:
            stage = 'Add Multires detail'
            fields.append('multires_levels')
            multires = multires or obj.modifiers.new('Angelo Sculpt Detail', 'MULTIRES')
            for _ in range(add_levels):
                if 'FINISHED' not in bpy.ops.object.multires_subdivide(modifier=multires.name, mode='CATMULL_CLARK'):
                    raise RuntimeError("Native Multires subdivision failed")
            multires.levels = multires.total_levels
            multires.sculpt_levels = multires.total_levels
        stage = 'Enter Sculpt Mode'
        if obj.mode != 'SCULPT':
            if 'FINISHED' not in bpy.ops.object.mode_set(mode='SCULPT'):
                raise RuntimeError("Native Sculpt Mode preparation was cancelled")
        stage = 'Verify sculpt preparation'
        window.view_layer.update()
        after = snapshot(window, obj)
    except Exception as error:
        return partial_edit(window, obj, before, error, stage, fields)
    return {"success": True, "undoable": True, "added_levels": add_levels, "preserves_base_topology": True,
            "verified": public_state(obj, after), "changes": change_report(window, obj, fields)}


def compile_pass(window, obj, args, before):
    if obj.mode != 'SCULPT' or bpy.context.active_object != obj:
        raise SculptPreflightError("Prepare this object for sculpting first")
    if not native_surface_available(obj):
        raise SculptPreflightError('Restart the rebuilt Angelo app to load the live native sculpt bridge', code="UNSUPPORTED")
    public = public_state(obj, before)
    largest = max(p.get('radius_scale',1) for path in args['paths'] for p in path)
    if args['radius'] * largest > public['diagonal_world'] * .75:
        raise SculptPreflightError("Keep the largest brush footprint below 75% of the target diagonal")
    paths = resample_paths(args['paths'], args['radius'] * args['spacing'] * min(p.get('radius_scale',1) for path in args['paths'] for p in path), grab=args['brush'] in ANCHORED)
    dabs = sum(map(len, paths))
    if dabs > public['max_dabs_this_mesh']:
        raise SculptPreflightError(f"Split work into at most {public['max_dabs_this_mesh']} dabs at this resolution")
    bvh = surface(window, obj, before)
    inverse = obj.matrix_world.inverted()
    scale = obj.matrix_world.to_3x3().col[0].length
    if not 1e-6 <= args['radius'] / scale <= 10000:
        raise SculptPreflightError("Use an object-space radius from 0.000001 to 10000")
    anchor = None
    automasking = set(args.get('automasking', []))
    if args.get('region'):
        from .sculpt_workflow import resolve_region
        anchor = resolve_region(obj, before, args['region'])
        if anchor['face_set']:
            automasking.add('FACE_SETS')
    points = []
    for path in paths:
        for index, point in enumerate(path):
            world = Vector(point['position'])
            if anchor:
                if world.length > anchor['radius']:
                    raise SculptPreflightError("Regional stroke centers must stay within the named region radius")
                world += Vector(anchor['position'])
            if args['brush'] in ANCHORED and index > 0:
                location = inverse @ world
                if math.dist(point['position'], path[0]['position']) > args['radius'] * 2:
                    raise SculptPreflightError("Limit anchored displacement to two brush radii")
            else:
                hit, location = nearest(obj, bvh, world)
                if hit['distance'] > args['radius'] * .5:
                    raise SculptPreflightError("A control point is too far from the observed surface")
                if anchor and anchor['face_set']:
                    values = obj.data.attributes.get('.sculpt_face_set')
                    if not values or values.data[hit['face']].value != anchor['face_set']:
                        raise SculptPreflightError("A regional stroke starts outside its bound face set")
            tilt = point.get('tilt',[0,0])
            points.append({'name':str(len(points)), 'mouse':(0,0), 'mouse_event':(0,0), 'time':0.0, 'location': [float(x) for x in location], 'pressure': float(point['pressure']), 'is_start': index == 0,
                           'x_tilt': float(tilt[0]), 'y_tilt': float(tilt[1]), 'size': float(point.get('radius_scale',1))})
    normal = (obj.matrix_world.to_3x3().transposed() @ Vector(args['normal'])).normalized()
    payload = {'stroke':points, 'brush_type':args['brush'], 'radius':float(args['radius']/scale),
               'strength':float(args['strength']), 'symmetry':args['symmetry'], 'surface_normal':list(normal),
               'front_faces_only':args['front_faces_only'], 'mode':'NORMAL' if args['direction']=='ADD' else 'INVERT',
               'falloff':args.get('falloff','SMOOTH'), 'automasking':sorted(automasking)}
    if anchor:
        payload.update(anchor_vertex=anchor['vertex'], anchor_initial=list(inverse @ Vector(anchor['position'])))
    return payload


def pass_result(window, obj, before, after, engine_ms, payloads):
    if before['world'].shape != after['world'].shape:
        raise RuntimeError("Unexpected topology change")
    displacement = np.linalg.norm(after['world']-before['world'],axis=1)
    diagonal = public_state(obj,before)['diagonal_world']
    changed = displacement > max(diagonal*1e-7,1e-8)
    masks_changed = any(p['brush_type']=='MASK' for p in payloads)
    result = {'success':True, 'object':obj.name, 'brush':payloads[0]['brush_type'] if len(payloads)==1 else 'PROGRAM',
              'passes':len(payloads), 'paths':sum(sum(p['is_start'] for p in op['stroke']) for op in payloads),
              'dabs':sum(len(op['stroke']) for op in payloads), 'undoable':True, 'topology_preserved':True,
              'native_brush_ms':round(engine_ms,3), 'changed_points':int(changed.sum()),
              'max_displacement_world':float(displacement.max()), 'rms_displacement_world':float(np.sqrt(np.mean(displacement**2))),
              'verified':public_state(obj,after), 'surface_queries':'LIVE_PBVH',
              'visual_verification':'Compare fresh views/reference landmarks before judging the sculpt',
              'changes':change_report(window,obj, (['geometry','sculpt_mask'] if masks_changed else ['geometry']) if before['revision']!=after['revision'] else [])}
    if changed.any():
        affected = np.concatenate((before['world'][changed],after['world'][changed]))
        result['changed_bounds_world'] = {'min':affected.min(axis=0).tolist(),'max':affected.max(axis=0).tolist()}
    elif not masks_changed:
        result['warning'] = 'No measurable displacement; check mask, density, pressure and targeting'
    return result


def sculpt_pass(window, obj, args, before):
    return sculpt_program(window, obj, {'steps':[args]}, before, single=True)


def sculpt_program(window, obj, args, before, single=False):
    with before_an_edit():
        native = bpy.ops.sculpt.angelo_sculpt_pass if single else bpy.ops.sculpt.angelo_sculpt_program
        native.get_rna_type()
        payloads = [compile_pass(window,obj,step,before) for step in args['steps']]
        if sum(len(p['stroke']) for p in payloads) > public_state(obj,before)['max_dabs_this_mesh']:
            raise SculptPreflightError('The whole program exceeds the work budget; split at visual checkpoints')
    stage = 'Apply native sculpt program' if not single else 'Apply native sculpt brush'
    fields = ['geometry','sculpt_mask'] if any(p['brush_type']=='MASK' for p in payloads) else ['geometry']
    try:
        started = time.perf_counter()
        if single:
            payload = {k:v for k,v in payloads[0].items() if not k.startswith('anchor_')}
            payload['automasking'] = set(payload['automasking'])
            outcome = bpy.ops.sculpt.angelo_sculpt_pass('EXEC_DEFAULT',True,**payload)
        else:
            outcome = bpy.ops.sculpt.angelo_sculpt_program('EXEC_DEFAULT',True,program=json.dumps(payloads,allow_nan=False))
        engine_ms = (time.perf_counter()-started)*1000
        if 'FINISHED' not in outcome:
            raise RuntimeError('Native sculpt program stopped')
        stage = 'Verify native sculpt brush'
        window.view_layer.update()
        after = snapshot(window,obj)
        return pass_result(window,obj,before,after,engine_ms,payloads)
    except Exception as error:
        return partial_edit(window,obj,before,error,stage,fields)



def camera_depth_range(window, obj, projected, center, basis, radius):
    """Fit depth independently of the image crop, including possible occluders.

    Live target positions are authoritative in Sculpt Mode. Other displayed
    objects/instances need only evaluated bounding boxes; no neighbor geometry
    export or scene-wide BVH is necessary.
    """
    low, high = float(projected[:, 2].min()), float(projected[:, 2].max())
    for instance in window.view_layer.depsgraph.object_instances:
        neighbor = instance.object
        if not instance.show_self or (not instance.is_instance and neighbor.original == obj):
            continue
        if not instance.is_instance and not neighbor.original.visible_get(view_layer=window.view_layer):
            continue
        bounds = np.asarray(neighbor.bound_box, dtype=np.float64)
        if np.all(bounds == -1):
            continue  # Non-geometric objects have no drawable bounding box.
        transform = np.asarray(instance.matrix_world, dtype=np.float64)
        bounds = (bounds @ transform[:3, :3].T + transform[:3, 3] - np.asarray(center)) @ basis
        if not np.isfinite(bounds).all():
            raise RuntimeError('A visible object has invalid bounds; cannot prove image-space visibility')
        if (bounds[:, :2].min(axis=0) > radius).any() or (bounds[:, :2].max(axis=0) < -radius).any():
            continue
        low, high = min(low, float(bounds[:, 2].min())), max(high, float(bounds[:, 2].max()))
    return low, high


def in_front_sources(window):
    """Include instanced sources outside the scene, never keep iterator wrappers."""
    sources = {neighbor.session_uid:neighbor for neighbor in window.view_layer.objects if neighbor.show_in_front}
    for instance in window.view_layer.depsgraph.object_instances:
        original = instance.object.original
        if original.show_in_front:
            sources[original.session_uid] = original
    return list(sources.values())


def capture_views(window, area, region, obj, state, args, references):
    import gpu
    if references is None:
        raise RuntimeError("Sculpt views need a bound chat reference store")
    size = args['resolution']
    from .sculpt_workflow import silhouette, list_regions, resolve_region
    framing = references.observation_camera(args['framing_reference']) if args.get('framing_reference') else None
    if framing and (framing['object_uid'] != obj.session_uid or args['views'] != [framing['label']]):
        raise RuntimeError('Reuse one admitted camera belonging to this target')
    # Refresh base evaluated geometry once per checkpoint, not per dab. Native
    # Workbench viewport images draw live sculpt PBVH/CCG, not the Multires cage;
    # do not rebuild that live session or reevaluate a shallow object copy.
    prior_revision = state['revision']
    if not any(m.show_viewport for m in obj.modifiers):
        obj.update_tag(refresh={'DATA'})
        window.view_layer.update()
    state = snapshot(window,obj)
    low, high = (Vector(v) for v in state['bounds'])
    center = (low + high) * .5
    region_frame = resolve_region(obj,state,args['region']) if args.get('region') else None
    if region_frame:
        center = Vector(region_frame['position'])
    if framing:
        center = Vector(framing['center'])
    space = area.spaces.active
    shading = space.shading
    old = {key: getattr(shading, key) for key in ('type', 'light', 'color_type', 'single_color', 'show_shadows', 'show_cavity', 'background_type', 'background_color', 'show_xray', 'show_backface_culling', 'show_object_outline', 'show_specular_highlight', 'use_dof')}
    old['single_color'], old['background_color'] = tuple(old['single_color']), tuple(old['background_color'])
    overlay = space.overlay.show_overlays
    view_regions = tuple(space.region_quadviews) or (space.region_3d,)
    clipping = [(view_region, view_region.use_clip_planes) for view_region in view_regions]
    in_front = in_front_sources(window)
    offscreen = None
    views = []
    started = time.perf_counter()
    try:
        shading.type = 'SOLID'
        shading.light = 'STUDIO'
        shading.color_type = 'SINGLE'
        shading.single_color = (.58, .61, .67)
        shading.show_shadows = True
        shading.show_cavity = True
        # Targeting must correspond to opaque visible surfaces, not an artist's
        # temporary X-ray display of geometry behind another object.
        shading.show_xray = False
        shading.show_backface_culling = False
        shading.show_object_outline = False
        shading.show_specular_highlight = False
        shading.use_dof = False
        shading.background_type = 'VIEWPORT'
        shading.background_color = (.025, .025, .03)
        space.overlay.show_overlays = False
        for view_region, enabled in clipping:
            view_region.use_clip_planes = False
        # In Front is an artist display override, not real visibility. Its
        # separate Workbench depth buffer is zeroed in the final composite.
        for neighbor in in_front:
            neighbor.show_in_front = False
        if in_front:
            window.view_layer.update()
        offscreen = gpu.types.GPUOffScreen(size, size)
        for label in args['views']:
            direction = Vector(framing['direction'] if framing else VIEW_DIRECTIONS[label]).normalized()
            rotation = direction.to_track_quat('Z', 'Y').to_matrix().to_4x4()
            basis = np.asarray(rotation.to_3x3(), dtype=np.float64)
            projected = (state['world'] - np.asarray(center)) @ basis
            radius = max(float(np.abs(projected[:, :2]).max()) * 1.12, .001)
            if region_frame:
                radius = region_frame['radius']*1.2
            if framing:
                radius = framing['radius']
            low_depth, high_depth = camera_depth_range(window, obj, projected, center, basis, radius)
            if framing:
                distance, depth = framing['camera_distance'], framing['clip_depth']
                # Distant background neighbors outside the old far plane cannot
                # occlude this target. The target itself must remain unclipped.
                if high_depth >= distance or float(projected[:, 2].min()) <= distance-depth:
                    raise RuntimeError('Visible geometry has left the saved camera depth range. Take a fresh sculpt view before targeting or starting a new comparison')
            else:
                margin = max(radius, .001)
                distance = max(radius * 4, high_depth + margin)
                depth = max(radius * 12, distance - low_depth + margin)
            projection = Matrix(((1/radius, 0, 0, 0), (0, 1/radius, 0, 0), (0, 0, -2/depth, -1), (0, 0, 0, 1)))
            camera = Matrix.Translation(center + direction * distance) @ rotation
            view = camera.inverted()
            offscreen.draw_view3d(window.scene, window.view_layer, space, region, view, projection, do_color_management=True)
            with offscreen.bind():
                pixels = offscreen.texture_color.read()
                depth_pixels = gpu.state.active_framebuffer_get().read_depth(0, 0, size, size)
                depths = np.frombuffer(depth_pixels, dtype=np.float32).copy().reshape((size, size))
            if not np.isfinite(depths).all():
                raise RuntimeError('Native view depth is unavailable; take a fresh solid view before pixel targeting')
            pixels.dimensions = size * size * 4
            reference = references.new_observation(label + ' sculpt view')
            image = None
            try:
                image = bpy.data.images.new('Angelo sculpt observation', width=size, height=size, alpha=True)
                image.colorspace_settings.name = 'Non-Color'
                image.pixels.foreach_set(np.frombuffer(bytes(pixels), dtype=np.uint8).astype(np.float32) / 255)
                image.filepath_raw = reference['path']
                image.file_format = 'PNG'
                image.save()
                os.chmod(reference['path'], 0o600)
                reference['bytes'] = os.path.getsize(reference['path'])
            except Exception:
                references.discard_observation(reference)
                raise
            finally:
                if image:
                    bpy.data.images.remove(image)
            views.append(reference | {"view": label, "view_direction_world": list(direction), "framing_center_world": list(center),
                                      "half_width_world": radius, "mesh_revision": state['revision']})
            mask = silhouette(window,area,region,obj,offscreen,view,projection,size)
            landmarks = {v['name']:v for v in list_regions(obj,state) if not v.get('stale')}
            references.remember_observation(reference, {'label':label,'silhouette':mask,'landmarks':landmarks,'camera_distance':distance,'object_uid': obj.session_uid, 'mesh_revision': state['revision'],
                'center': list(center), 'radius': radius, 'direction': list(direction),
                'right': list(rotation.to_3x3().col[0]), 'up': list(rotation.to_3x3().col[1]),
                # GPU depth includes visible neighbors, instances and modifiers.
                # Runtime-only and bounded; never expose an array in tool JSON.
                'depths': depths, 'clip_depth': depth})
        # Native drawing can finalize CCG boundary stitching. Bind every camera
        # to the final live revision, not a pre-draw hash of those shared points.
        live = snapshot(window, obj)
        for reference in views:
            reference['mesh_revision'] = live['revision']
            camera = references.observation_camera(reference['id'])
            camera['mesh_revision'] = live['revision']
            references.remember_observation(reference, camera)
    except Exception:
        for reference in views:
            references.discard_observation(reference)
        raise
    finally:
        if offscreen:
            offscreen.free()
        for key, value in old.items():
            setattr(shading, key, value)
        space.overlay.show_overlays = overlay
        for view_region, enabled in clipping:
            view_region.use_clip_planes = enabled
        for neighbor in in_front:
            neighbor.show_in_front = True
        if in_front:
            window.view_layer.update()
    return {"success": True, "object": obj.name, "mesh_revision": live['revision'], "views": views,
            "capture_ms": round((time.perf_counter()-started)*1000, 3), "resolution": size,
            "native_cache_synchronized": prior_revision != live['revision'],
            "scene_changed": False, "projection": "orthographic", "note": "Fresh native solid views; visible neighboring objects may also appear"}


def pixel_targets(window, obj, state, args, references):
    if references is None:
        raise RuntimeError('Image-space targets need a bound chat reference store')
    camera = references.observation_camera(args['view_reference'])
    if camera['object_uid'] != obj.session_uid or state['revision'] != args['mesh_revision'] or camera['mesh_revision'] != state['revision']:
        raise RuntimeError('The observed surface changed or belongs to another object. Take a fresh sculpt view before targeting its pixels')
    bvh = surface(window, obj, state)
    inverse = obj.matrix_world.inverted()
    center, right, up, direction = (Vector(camera[key]) for key in ('center','right','up','direction'))
    radius = camera['radius']
    depths = camera.get('depths')
    if depths is None:
        raise RuntimeError('Take a fresh native sculpt view with visible-surface depth before targeting its pixels')
    size = depths.shape[0]
    coordinate_scale = max(abs(v) for v in center) + camera['camera_distance'] + 2 * radius
    tolerance = depth_match_tolerance(camera['clip_depth'], 2 * radius / size, coordinate_scale)
    ray = (inverse.to_3x3() @ -direction).normalized()
    hits = []
    for x, y in args['pixels']:
        # Sample the same pixel center as the depth buffer (bottom-left GPU,
        # top-left public image), including clamped coordinates on the edges.
        ix, iy = min(size-1, int(x*size)), min(size-1, int((1-y)*size))
        sx, sy = (ix+.5)/size, 1-(iy+.5)/size
        origin = center + right * ((sx-.5)*2*radius) + up * ((.5-sy)*2*radius) + direction * camera.get('camera_distance',radius*4)
        position, normal, face, distance = bvh.ray_cast(inverse @ origin, ray)
        if position is None:
            hits.append({'pixel':[x,y], 'hit':False})
        else:
            world = obj.matrix_world @ position
            visible_depth = float(depths[iy,ix])
            target_distance = (origin-world).dot(direction)
            visible_distance = visible_depth * camera['clip_depth']
            if tolerance is None:
                hits.append({'pixel':[x,y], 'sampled_pixel':[sx,sy], 'hit':False,
                             'reason':'insufficient_depth_precision'})
                continue
            # Bound projection roundoff in WORLD units relative to this crop's
            # pixel footprint, never a fixed fraction of the scene's depth.
            if visible_depth >= 1 or not 0 <= target_distance < camera['clip_depth'] or abs(target_distance-visible_distance) > tolerance:
                hits.append({'pixel':[x,y], 'sampled_pixel':[sx,sy], 'hit':False,
                    'reason':'occluded' if visible_distance < target_distance-tolerance else 'not_visible_in_captured_view'})
                continue
            hits.append({'pixel':[x,y], 'sampled_pixel':[sx,sy], 'hit':True, 'position':list(world),
                         'normal':list((inverse.to_3x3().transposed() @ normal).normalized()), 'face':int(face)})
    return {'object':obj.name, 'mesh_revision':state['revision'], 'hits':hits, 'coordinate_space':'WORLD', 'scene_changed':False}


def execute(window, area, region, name, args, references=None):
    mutation = name in SCULPT_MUTATIONS
    with before_an_edit(mutation):
        obj = target(window, args['object'], mutate=mutation)
        state = snapshot(window, obj, build_surface=name in {'angelo_sculpt_batch', 'angelo_sculpt_target'} or
                         (name == 'angelo_inspect_sculpt' and bool(args['probes'])))
        check_observation_scope(state,name,args)
        if mutation and state['revision'] != args['mesh_revision']:
            raise SculptPreflightError("The sculpt surface changed since inspection; obtain a fresh mesh_revision",
                                       code="STALE_REVISION")
        if not bpy.context.preferences.edit.use_global_undo and mutation:
            raise SculptPreflightError("Enable Global Undo before agent sculpting", code="UNSUPPORTED")
    if name == 'angelo_inspect_sculpt':
        report = public_state(obj, state)
        if state.get('controls_only'):
            report.update(probes=[],regions=[],edit_blockers=['Lower the existing Multires working level first'])
            return report
        report['probes'] = [nearest(obj, surface(window, obj, state), p)[0] for p in args['probes']]
        report['edit_blockers'] = []
        try:
            target(window, args['object'], mutate=True)
        except RuntimeError as error:
            report['edit_blockers'].append(str(error))
        from .sculpt_workflow import list_regions
        report['regions'] = list_regions(obj,state)
        return report
    if name == 'angelo_sculpt_target':
        return pixel_targets(window, obj, state, args, references)
    if name == 'angelo_prepare_sculpt':
        return prepare(window, obj, args, state)
    if name == 'angelo_sculpt_batch':
        return sculpt_pass(window, obj, args, state)
    if name == 'angelo_sculpt_program':
        return sculpt_program(window, obj, args, state)
    if name in {'angelo_sculpt_region','angelo_sculpt_resolution','angelo_sculpt_review'}:
        from . import sculpt_workflow
        return sculpt_workflow.execute(window,area,region,obj,state,name,args,references)
    return capture_views(window, area, region, obj, state, args, references)
