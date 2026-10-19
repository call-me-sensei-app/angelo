# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Persistent sculpt landmarks, working levels and explicit visual targets."""
import json
import math

import bpy
import numpy as np
from mathutils import Vector

from .sculpt_spec import MAX_VERTICES, choose_level
from .legacy_names import SCULPT_REGIONS, read_property, write_property

REGIONS_KEY = SCULPT_REGIONS[0]  # Read with its pre-rename fallback; written under this key.
REGION_ANCHOR_VERSION = 2


def regions(obj):
    try:
        value = json.loads(read_property(obj, SCULPT_REGIONS, '{}'))
        return value if isinstance(value, dict) and len(value) <= 64 else {}
    except (ValueError, TypeError, RecursionError):
        return {}


def _finite_number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _region_controls(region, state):
    """Validate saved landmark data before reading its native vertex index.

    Version 1 stored only a nearest vertex. Its distance from the requested
    surface point is unknowable, so reusing it could silently edit another area.
    Keep those records visible as stale rather than guessing a migration.
    """
    if not isinstance(region, dict) or region.get('topology') != state['topology_revision']:
        raise ValueError('Define this region again after a topology or working-level change')
    if type(region.get('anchor_version')) is not int or region['anchor_version'] != REGION_ANCHOR_VERSION:
        raise ValueError('This region has an unverified legacy anchor; define it again on the observed surface')
    index = region.get('vertex')
    if type(index) is not int or not 0 <= index < state['vertices']:
        raise ValueError('This region no longer has a valid surface anchor')
    radius, face_set = region.get('radius'), region.get('face_set')
    if (not _finite_number(radius) or not 1e-6 <= radius <= 1000 or
            type(face_set) is not int or not 0 <= face_set <= 2147483647):
        raise ValueError('This region has invalid controls; define it again')
    requested, accepted = region.get('requested_world'), region.get('accepted_world')
    for point in (requested, accepted):
        if (not isinstance(point, (list, tuple)) or len(point) != 3 or
                any(not _finite_number(v) for v in point)):
            raise ValueError('This region has invalid anchor validation data; define it again')
    if math.dist(requested, accepted) > radius * .5:
        raise ValueError('The saved vertex lies outside the accepted region footprint; define it again')
    return index, radius, face_set


def resolve_region(obj, state, name):
    from .sculpt import SculptPreflightError
    region = regions(obj).get(name)
    try:
        index, radius, face_set = _region_controls(region, state)
        position = (obj.matrix_world @ Vector(obj.angelo_sculpt_vertex(index))).to_tuple()
        if len(position) != 3 or not all(math.isfinite(v) for v in position):
            raise ValueError('This region no longer has a finite surface anchor')
    except (ValueError, TypeError, IndexError, AttributeError) as error:
        raise SculptPreflightError(str(error)) from error
    return {'name':name,'vertex':index,'topology':region['topology'],'radius':radius,'face_set':face_set,
            'position':position}


def list_regions(obj, state):
    output = []
    for name in regions(obj):
        try:
            item = resolve_region(obj, state, name)
            output.append({k:item[k] for k in ('name','position','radius','face_set')})
        except (RuntimeError, ValueError, AttributeError) as error:
            output.append({'name':name, 'stale':True, 'reason':str(error)})
    return output


def define_region(window, obj, state, args):
    from .sculpt import SculptPreflightError, snapshot, public_state, change_report, partial_edit
    if obj.mode != 'SCULPT' or not hasattr(obj,'angelo_sculpt_query'):
        raise SculptPreflightError('Prepare the native sculpt surface before defining landmarks')
    position = obj.matrix_world.inverted() @ Vector(args['position'])
    hit, co, normal, face, vertex = obj.angelo_sculpt_query(position)
    if not hit or math.dist(args['position'], obj.matrix_world @ Vector(co)) > args['radius']*.5:
        raise SculptPreflightError('Place the region landmark on the observed surface')
    if type(vertex) is not int or not 0 <= vertex < state['vertices']:
        raise SculptPreflightError('The observed surface did not return a valid vertex landmark')
    anchor = list(obj.matrix_world @ Vector(obj.angelo_sculpt_vertex(vertex)))
    distance = math.dist(args['position'], anchor)
    if not math.isfinite(distance) or distance > args['radius'] * .5:
        raise SculptPreflightError(
            f'The nearest vertex landmark is {distance:.6g} world units from the requested point; '
            f'this region allows at most {args["radius"] * .5:.6g} (half its radius). '
            'Increase the sculpt working resolution or place the landmark on an observed vertex. '
            'No region was changed')
    if args['face_set']:
        sets = obj.data.attributes.get('.sculpt_face_set')
        if (not sets or type(face) is not int or not 0 <= face < len(sets.data) or
                sets.data[face].value != args['face_set']):
            raise SculptPreflightError('The landmark must lie on the requested existing face set')
    values = regions(obj)
    if len(values) >= 64 and args['name'] not in values:
        raise SculptPreflightError('This mesh already has 64 region landmarks')
    values[args['name']] = {'vertex':vertex,'topology':state['topology_revision'],
                           'radius':args['radius'],'face_set':args['face_set'],
                           'anchor_version':REGION_ANCHOR_VERSION,
                           'requested_world':list(args['position']), 'accepted_world':anchor}
    try:
        write_property(obj, SCULPT_REGIONS, json.dumps(values,allow_nan=False))
        return {'success':True,'object':obj.name,'region':resolve_region(obj,state,args['name']),
                'regions':list_regions(obj,state),'undoable':True,'verified':public_state(obj,snapshot(window,obj)),
                'changes':change_report(window,obj,['sculpt_regions'])}
    except Exception as error:
        return partial_edit(window,obj,state,error,'Store sculpt region',['sculpt_regions'])


def working_resolution(window, obj, state, args):
    from .sculpt import SculptPreflightError, snapshot, public_state, change_report, partial_edit
    if obj.mode != 'SCULPT' or bpy.context.active_object != obj:
        raise SculptPreflightError('Prepare this mesh in Sculpt Mode before changing its working level')
    modifier = next((m for m in obj.modifiers if m.type=='MULTIRES'),None)
    if not modifier:
        raise SculptPreflightError('Add non-destructive Multires levels with Prepare Sculpt first')
    counts = [len(obj.data.vertices)] + [len(obj.data.loops)*(2**(level-1)+1)**2 for level in range(1,modifier.total_levels+1)]
    sufficient = True
    if args['mode']=='AUTO':
        level,sufficient = choose_level(modifier.total_levels,args['stage'],args['radius'],public_state(obj,state)['diagonal_world'],counts)
    else:
        level = args['level']
        if level > modifier.total_levels or counts[level] > MAX_VERTICES:
            raise SculptPreflightError('Select an existing level within the working-point budget')
    old_level = modifier.sculpt_levels
    try:
        modifier.sculpt_levels = level
        window.view_layer.update()
        after = snapshot(window,obj)
        if modifier.sculpt_levels != level:
            raise RuntimeError('Blender did not select the requested working level')
        result = {'success':True,'object':obj.name,'stage':args['stage'],'old_level':old_level,'working_level':level,
                  'density_sufficient_estimate':sufficient,'undoable':True,'verified':public_state(obj,after),
                  'changes':change_report(window,obj,['multires_working_level'] if old_level!=level else []),
                  'regions':list_regions(obj,after)}
        if not sufficient:
            result['warning']='Existing bounded levels may be too coarse for this footprint; inspect detail before adding geometry'
        return result
    except Exception as error:
        return partial_edit(window,obj,state,error,'Select Multires working level',['multires_working_level'])


def silhouette(window, area, region, obj, offscreen, view, projection, size):
    """Target-only flat mask. Restore every display/visibility setting even on failure."""
    space = area.spaces.active
    shading = space.shading
    settings = {'type':'SOLID', 'light':'FLAT', 'color_type':'SINGLE', 'single_color':(1,1,1),
                'background_type':'VIEWPORT', 'background_color':(0,0,0), 'show_cavity':False,
                'show_shadows':False, 'show_backface_culling':False, 'show_xray':False,
                'show_object_outline':False, 'show_specular_highlight':False, 'use_dof':False}
    old = {key:getattr(shading,key) for key in settings}
    old['single_color'],old['background_color'] = tuple(old['single_color']),tuple(old['background_color'])
    display, in_front = obj.display_type, obj.show_in_front
    overlays = space.overlay.show_overlays
    view_regions = tuple(space.region_quadviews) or (space.region_3d,)
    clipping = [(view_region, view_region.use_clip_planes) for view_region in view_regions]
    hidden = []
    old_active = window.view_layer.objects.active
    try:
        for neighbor in window.view_layer.objects:
            if neighbor != obj and not neighbor.hide_get(view_layer=window.view_layer):
                hidden.append((neighbor,neighbor.select_get(view_layer=window.view_layer)))
                neighbor.hide_set(True,view_layer=window.view_layer)
        obj.display_type = 'SOLID'
        obj.show_in_front = False
        space.overlay.show_overlays = False
        for view_region, enabled in clipping:
            view_region.use_clip_planes = False
        for key, value in settings.items():
            setattr(shading, key, value)
        window.view_layer.update()
        offscreen.draw_view3d(window.scene,window.view_layer,area.spaces.active,region,view,projection,do_color_management=False)
        with offscreen.bind():
            buffer = offscreen.texture_color.read()
            buffer.dimensions = size*size*4
            mask = np.frombuffer(bytes(buffer),dtype=np.uint8).reshape(size,size,4)[:,:,:3].max(axis=2)>127
        return np.flipud(mask).copy()
    finally:
        for key,value in old.items():
            setattr(shading,key,value)
        obj.display_type = display
        obj.show_in_front = in_front
        space.overlay.show_overlays = overlays
        for view_region, enabled in clipping:
            view_region.use_clip_planes = enabled
        for neighbor,selected in hidden:
            neighbor.hide_set(False,view_layer=window.view_layer)
        window.view_layer.update()
        for neighbor,selected in hidden:
            neighbor.select_set(selected,view_layer=window.view_layer)
        window.view_layer.objects.active = old_active


def polygon_mask(points, size):
    """Exact pixel-center even/odd fill, bounded to the polygon's pixel ROI.

    Compute only scanline/edge crossings, then prefix their parity across each
    row. This avoids evaluating every edge against a full-resolution image.
    searchsorted uses the original normalized pixel centers directly: rounding
    crossings to raster coordinates would change pixels exactly on an edge.
    Duplicate vertices, horizontal edges and self intersections need no special
    raster approximation and retain the same half-open boundary convention.
    """
    if type(size) is not int or not 1 <= size <= 768:
        raise ValueError('Silhouette masks require a size from 1 to 768')
    if len(points) > 128:
        raise ValueError('A silhouette outline needs at most 128 finite two-dimensional points')
    if len(points) == 0:
        return np.zeros((size,size), dtype=bool)
    vertices = np.asarray(points, dtype=np.float64)
    if (vertices.ndim != 2 or vertices.shape[1] != 2 or
            not np.isfinite(vertices).all()):
        raise ValueError('A silhouette outline needs at most 128 finite two-dimensional points')
    mask = np.zeros((size,size),dtype=bool)
    if len(vertices) < 3:
        return mask
    centers = (np.arange(size, dtype=np.float64) + .5) / size
    x0, y0 = np.searchsorted(centers, vertices.min(axis=0), side='left')
    x1, y1 = np.searchsorted(centers, vertices.max(axis=0), side='left')
    if y0 == y1:
        return mask
    # Preserve the ray formula's floating-point boundary behavior too. An
    # intersection at an extremal vertex can round one ULP past that vertex;
    # a one-pixel guard keeps a coincident center inside the evaluated ROI.
    x0, x1 = max(0, x0-1), min(size, x1+1)
    ax, ay = np.roll(vertices, 1, axis=0).T
    bx, by = vertices.T
    ys = centers[y0:y1]
    rows, edges = np.nonzero((ay > ys[:,None]) != (by > ys[:,None]))
    # The strict crossing test excludes horizontal edges, so division is safe.
    crossings = (bx[edges]-ax[edges]) * (ys[rows]-ay[edges]) / (by[edges]-ay[edges]) + ax[edges]
    cuts = np.searchsorted(centers[x0:x1], crossings, side='left')
    changes = np.zeros((y1-y0, x1-x0+1), dtype=bool)
    changes[:,0] = (np.bincount(rows, minlength=y1-y0) & 1).astype(bool)
    np.bitwise_xor.at(changes, (rows, cuts), True)
    np.logical_xor.accumulate(changes[:,:-1], axis=1, out=mask[y0:y1,x0:x1])
    return mask


def overlap(a,b):
    union = int(np.count_nonzero(a|b))
    return float(np.count_nonzero(a&b)/union) if union else 1.0


def projected_pixel(position, camera):
    delta = Vector(position)-Vector(camera['center'])
    return [.5+delta.dot(Vector(camera['right']))/(2*camera['radius']),
            .5-delta.dot(Vector(camera['up']))/(2*camera['radius'])]


def review(window, area, region, obj, state, args, references):
    from .sculpt import capture_views
    if references is None:
        raise RuntimeError('Visual review requires this chat’s admitted native view')
    baseline = references.observation_camera(args['baseline'])
    if baseline['object_uid'] != obj.session_uid or 'silhouette' not in baseline:
        raise RuntimeError('Capture a new native baseline for this target')
    result = capture_views(window,area,region,obj,state,{'views':[baseline['label']],
                           'resolution':baseline['depths'].shape[0], 'framing_reference':args['baseline']},references)
    camera = references.observation_camera(result['views'][0]['id'])
    old,new = baseline['silhouette'],camera['silhouette']
    metrics = {'baseline_iou':overlap(old,new),'baseline_area_pixels':int(old.sum()),'current_area_pixels':int(new.sum()),
               'touches_frame_edge':bool(new[0].any() or new[-1].any() or new[:,0].any() or new[:,-1].any()),
               'scope':'Target-only silhouette in identical framing; baseline overlap measures change, not artistic quality'}
    if args['outline']:
        goal = polygon_mask(args['outline'],new.shape[0])
        if not goal.any():
            raise RuntimeError('The reference outline covers no pixels in this framing')
        metrics.update(reference_iou_before=overlap(old,goal),reference_iou_after=overlap(new,goal))
        metrics['reference_iou_delta'] = metrics['reference_iou_after']-metrics['reference_iou_before']
    errors = []
    for target in args['landmarks']:
        live = resolve_region(obj,state,target['region'])
        pixel = projected_pixel(live['position'],camera)
        item = {'region':target['region'],'observed_pixel':pixel,'target_pixel':target['pixel'],
                'error_pixels':math.dist(pixel,target['pixel'])*new.shape[0]}
        old_region = baseline.get('landmarks',{}).get(target['region'])
        if old_region:
            item['previous_error_pixels'] = math.dist(projected_pixel(old_region['position'],baseline),target['pixel'])*new.shape[0]
            item['error_reduction_pixels'] = item['previous_error_pixels']-item['error_pixels']
            item['anchor_movement_world'] = math.dist(old_region['position'],live['position'])
        errors.append(item)
    result['review'] = dict(metrics,landmarks=errors,has_explicit_targets=bool(args['outline'] or args['landmarks']))
    return result


def execute(window,area,region,obj,state,name,args,references):
    if name=='angelo_sculpt_region':
        return define_region(window,obj,state,args)
    if name=='angelo_sculpt_resolution':
        return working_resolution(window,obj,state,args)
    return review(window,area,region,obj,state,args,references)
