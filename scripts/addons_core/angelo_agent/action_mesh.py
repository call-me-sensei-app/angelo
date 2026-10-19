# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Typed primitive, indexed and whole-mesh (bend) BMesh and UV kernels on private staged meshes.

Selections and topology generations are bound by action_context. Growth is
guarded before base-mesh commit; operators are used only for isolated unwrap."""

import math
import numpy as np
import bpy
import bmesh
from mathutils import Vector
from .refusal_codes import RefusalError
from .action_catalog import estimated_work
from .action_schemas import TOPOLOGY_KINDS
from .action_context import (
    _mesh, _check_counts, _selected, _face_selection, _edge_selection,
    _readback, _set_initial_transform,
)
from .action_budget import _modifier_bound
from .action_edit_context import _edit_override
from .program_names import name_created


def _torus(bm, params):
    """Blender's Add > Torus ring (add_mesh_torus.add_torus): a minor circle swept about Z, all quads."""
    major, minor = params['major_segments'], params['minor_segments']
    rings = []
    for i in range(major):
        angle = 2 * math.pi * i / major
        ring = []
        for j in range(minor):
            tube = 2 * math.pi * j / minor
            radius = params['major_radius'] + math.cos(tube) * params['minor_radius']
            ring.append(bm.verts.new((radius * math.cos(angle), radius * math.sin(angle),
                                      math.sin(tube) * params['minor_radius'])))
        rings.append(ring)
    for i in range(major):
        for j in range(minor):
            a, b = rings[i], rings[(i + 1) % major]
            bm.faces.new((a[j], b[j], b[(j + 1) % minor], a[(j + 1) % minor]))


def _primitive(context, operation):
    params = operation['parameters']
    work = estimated_work(operation)
    _check_counts(work['vertices'], work['edges'], work['faces'])
    mesh = context.track_id(bpy.data.meshes.new(params['name'] + ' Mesh'))
    bm = bmesh.new()
    try:
        primitive = params['primitive']
        if primitive == 'CUBE':
            bmesh.ops.create_cube(bm, size=params['size'])
        elif primitive == 'UV_SPHERE':
            bmesh.ops.create_uvsphere(bm, u_segments=params['segments'], v_segments=params['rings'], radius=params['radius'])
        elif primitive == 'ICO_SPHERE':
            bmesh.ops.create_icosphere(bm, subdivisions=params['subdivisions'], radius=params['radius'])
        elif primitive in {'CYLINDER', 'CONE'}:
            bmesh.ops.create_cone(bm, cap_ends=params['cap_ends'], cap_tris=False, segments=params['segments'],
                                  radius1=params['radius'], radius2=params['radius'] if primitive == 'CYLINDER' else params['radius_top'],
                                  depth=params['depth'])
        elif primitive in {'PLANE', 'GRID'}:
            x, y = (1, 1) if primitive == 'PLANE' else (params['x_segments'], params['y_segments'])
            size = params['size']
            verts = [[bm.verts.new(((i / x - .5) * size, (j / y - .5) * size, 0)) for i in range(x + 1)]
                     for j in range(y + 1)]
            for j in range(y):
                for i in range(x):
                    bm.faces.new((verts[j][i], verts[j][i + 1], verts[j + 1][i + 1], verts[j + 1][i]))
        elif primitive == 'CIRCLE':
            bmesh.ops.create_circle(bm, cap_ends=params['fill'], cap_tris=False,
                                    segments=params['segments'], radius=params['radius'])
        elif primitive == 'TORUS':
            _torus(bm, params)
        bm.normal_update()
        bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
        _check_counts(len(bm.verts), len(bm.edges), len(bm.faces))
        bm.to_mesh(mesh)
        mesh.update()
    finally:
        bm.free()
    obj = context.track_id(bpy.data.objects.new(params['name'], mesh))
    _set_initial_transform(obj, params)
    context.add_object(obj, params['alias'])
    alias = ('$' + params['alias']) if params['alias'] else None
    name = name_created(context, obj, params['name'], alias, bpy.data.objects.get)  # In program order (program_names).
    return _readback(obj, operation, object=name, created=True, alias=alias)



AXES = 'XYZ'
BEND_EPSILON = .000001


def _bend(bm, obj, params):
    """mesh_bend on a BMesh in object space: cut even slices along the axis, then bow the mesh along ``up``.

    Every vertex moves straight along ``up`` by ``rise * (1 - t * t)``, ``t``
    running from -1 at one end of the mesh's bounds along the axis to 1 at the
    other: the ends stay, the length is unchanged and the middle moves by
    ``rise`` (a parabolic arch, or a sag when negative). ``rise`` is metres at
    the object's own scale along ``up`` (|scale|; parents and rotation do not
    enter), so a cube scaled to a 4 x 1.2 x 0.2 m deck rises by the stated
    metres. Sections stay upright: in recorded confirmation runs,
    sections square to a circular arc pushed a 4 m deck's top corners out to
    4.05 m, and GPT-6 Astra spent a round fitting it back to 4 m. Returns the
    facts the result reports.
    """
    up = AXES.index(params['up'])
    scale = abs(obj.scale[up])
    if scale < BEND_EPSILON:
        raise RefusalError('mesh_bend needs an object scale other than zero along up', code='VALIDATION')
    if not bm.verts:
        raise RefusalError('mesh_bend needs a mesh with vertices', code='VALIDATION')
    low = [min(vertex.co[index] for vertex in bm.verts) for index in range(3)]
    high = [max(vertex.co[index] for vertex in bm.verts) for index in range(3)]
    extents = [(high[index] - low[index]) * abs(obj.scale[index]) for index in range(3)]
    if params['axis'] is None:  # The longest other axis; ties go to the first (X before Y before Z).
        axis = max((index for index in range(3) if index != up), key=lambda index: (extents[index], -index))
    else:
        axis = AXES.index(params['axis'])
    if extents[axis] < BEND_EPSILON:
        raise RefusalError('mesh_bend: the mesh has no length along %s' % AXES[axis], code='VALIDATION')
    normal = [0.0, 0.0, 0.0]
    normal[axis] = 1.0
    for cut in range(1, params['segments']):
        point = [0.0, 0.0, 0.0]
        point[axis] = low[axis] + (high[axis] - low[axis]) * cut / params['segments']
        bmesh.ops.bisect_plane(bm, geom=list(bm.verts) + list(bm.edges) + list(bm.faces), dist=BEND_EPSILON,
                               plane_co=point, plane_no=normal, clear_outer=False, clear_inner=False)
        _check_counts(len(bm.verts), len(bm.edges), len(bm.faces))
    middle, half = (low[axis] + high[axis]) / 2, (high[axis] - low[axis]) / 2
    lift = params['rise'] / scale
    for vertex in bm.verts:
        t = max(-1.0, min(1.0, (vertex.co[axis] - middle) / half))
        co = vertex.co.copy()
        co[up] += lift * (1.0 - t * t)
        vertex.co = co
    return {'axis': AXES[axis], 'up': AXES[up], 'span': round(extents[axis], 6), 'rise': params['rise']}


def _mesh_edit(context, obj, operation):
    mesh = _mesh(context, obj, topology=True)
    params, kind = operation['parameters'], operation['kind']
    bent = {}
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        for sequence in (bm.verts, bm.edges, bm.faces):
            sequence.ensure_lookup_table()
        bm.normal_update()
        if kind in {'mesh_extrude', 'mesh_inset'}:
            faces = [bm.faces[face.index] for face in _face_selection(mesh, params)]
            loops = sum(len(face.verts) for face in faces)
            _check_counts(len(bm.verts) + loops, len(bm.edges) + loops * 3, len(bm.faces) + loops)
            if kind == 'mesh_extrude':
                # Include edges, so the region kernel removes internal source
                # faces just like Edit Mode extrusion, rather than duplicating a cap.
                edges = list({edge for face in faces for edge in face.edges})
                result = bmesh.ops.extrude_face_region(bm, geom=faces + edges, use_keep_orig=False,
                                                      use_normal_flip=params['use_normal_flip'])
                verts = [element for element in result['geom'] if isinstance(element, bmesh.types.BMVert)]
                bmesh.ops.translate(bm, verts=verts, vec=Vector(params['translation']))
            elif params['individual']:
                bmesh.ops.inset_individual(bm, faces=faces, thickness=params['thickness'], depth=params['depth'],
                                           use_even_offset=params['use_even_offset'], use_interpolate=True)
            else:
                bmesh.ops.inset_region(bm, faces=faces, thickness=params['thickness'], depth=params['depth'],
                                       use_boundary=True, use_even_offset=params['use_even_offset'], use_interpolate=True)
        elif kind == 'mesh_bevel':
            edges = _edge_selection(bm, params)
            verts = {vertex for edge in edges for vertex in edge.verts}
            if any(len(vertex.link_edges) > 64 for vertex in verts):
                raise RefusalError('Bevel vertex valence exceeds the bounded kernel limit', code='LIMIT_EXCEEDED')
            growth = sum(len(vertex.link_edges) ** 2 for vertex in verts) * (params['segments'] + 1) ** 2
            _check_counts(len(bm.verts) + growth, len(bm.edges) + growth * 2, len(bm.faces) + growth)
            bmesh.ops.bevel(bm, geom=edges, offset=params['offset'], segments=params['segments'], profile=params['profile'],
                            affect='EDGES', clamp_overlap=params['clamp_overlap'], loop_slide=True)
        elif kind == 'mesh_subdivide':
            faces = [bm.faces[face.index] for face in _face_selection(mesh, params)]
            growth = sum(len(face.verts) for face in faces) * (params['cuts'] + 1) ** 2
            _check_counts(len(bm.verts) + growth, len(bm.edges) + growth * 2, len(bm.faces) + growth)
            edges = list({edge for face in faces for edge in face.edges})
            bmesh.ops.subdivide_edges(bm, edges=edges, cuts=params['cuts'], smooth=params['smoothness'],
                                      use_grid_fill=True, use_smooth_even=False)
        elif kind == 'mesh_delete':
            faces = [bm.faces[face.index] for face in _face_selection(mesh, params)]
            bmesh.ops.delete(bm, geom=faces, context=params['mode'])
        elif kind == 'mesh_merge_by_distance':
            bmesh.ops.remove_doubles(bm, verts=_selected(bm.verts, params['vertex_indices']), dist=params['distance'])
        elif kind == 'mesh_recalculate_normals':
            bmesh.ops.recalc_face_normals(bm, faces=[bm.faces[face.index] for face in _face_selection(mesh, params)])
        elif kind == 'mesh_bend':
            bent = {'bend': _bend(bm, obj, params)}
        _check_counts(len(bm.verts), len(bm.edges), len(bm.faces))
        if any(not math.isfinite(value) or abs(value) > 100000 for vertex in bm.verts for value in vertex.co):
            raise RefusalError('Mesh edit generated nonfinite or unbounded coordinates', code='VALIDATION')
        bm.normal_update()
        _modifier_bound(context, obj, shape_override={
            'vertices': len(bm.verts), 'edges': len(bm.edges), 'faces': len(bm.faces)})
        bm.to_mesh(mesh)
        mesh.update()
        if kind in TOPOLOGY_KINDS:
            context.topology_versions[obj] = context.topology_versions.get(obj, 0) + 1
    finally:
        bm.free()
    return _readback(obj, operation, topology_step=context.topology_versions.get(obj, 0), **bent)



def uv_map(mesh, params):
    """The UV map a UV kind names, else the mesh's active one (Blender's, as its operators use); None: none yet.
    Taking the literal "UVMap" when none was named added a new map beside a mesh's own."""
    name = params.get('uv_map')
    return mesh.uv_layers.get(name) if name is not None else mesh.uv_layers.active


def _uv_project(context, obj, operation):
    mesh = _mesh(context, obj)
    params = operation['parameters']
    polygons = _face_selection(mesh, params)
    layer = uv_map(mesh, params)
    if layer is None:
        if len(mesh.uv_layers) >= 8:
            raise RefusalError('Native UV actions are limited to eight maps', code='LIMIT_EXCEEDED')
        layer = mesh.uv_layers.new(name=params.get('uv_map') or 'UVMap')
    planar_axes = {'X': (1, 2), 'Y': (0, 2), 'Z': (0, 1)}
    for polygon in polygons:
        normal = polygon.normal
        axis = max(range(3), key=lambda i: abs(normal[i]))
        cube_axes = [(1, 2), (0, 2), (0, 1)][axis]
        for loop_index in polygon.loop_indices:
            co = mesh.vertices[mesh.loops[loop_index].vertex_index].co
            if params['method'] == 'SPHERICAL':
                radius = co.length
                if radius < .000001:
                    uv = (.5, .5)
                else:
                    uv = (.5 + math.atan2(co.y, co.x) / (2 * math.pi),
                          .5 + math.asin(max(-1, min(1, co.z / radius))) / math.pi)
                uv = [(value - .5) * params['scale'] + .5 for value in uv]
            else:
                axes = cube_axes if params['method'] == 'CUBE' else planar_axes[params['axis']]
                uv = [co[i] * params['scale'] + .5 for i in axes]
            layer.data[loop_index].uv = uv
    mesh.update()
    return _readback(obj, operation, uv_map=layer.name, projected_faces=len(polygons))



def _uv_operator(context, obj, operation, create_layer, call):
    """Run one UV operator on the selected staged faces in isolated Edit mode, restoring selection."""
    mesh = _mesh(context, obj)
    params = operation['parameters']
    selected = _face_selection(mesh, params)
    if not selected:
        raise RefusalError('This UV action requires at least one staged face', code='VALIDATION')
    layer = uv_map(mesh, params)
    if layer is None:
        if not create_layer:
            raise RefusalError('UV_MAP_NOT_FOUND: pack an existing UV map', code='VALIDATION')
        if len(mesh.uv_layers) >= 8:
            raise RefusalError('Native UV actions are limited to eight maps', code='LIMIT_EXCEEDED')
        layer = mesh.uv_layers.new(name=params.get('uv_map') or 'UVMap')
    mesh.uv_layers.active = layer
    uv_map_name = layer.name
    selected_ids = {polygon.index for polygon in selected}
    original_selection = ([vertex.select for vertex in mesh.vertices], [edge.select for edge in mesh.edges],
                          [polygon.select for polygon in mesh.polygons])
    for vertex in mesh.vertices:
        vertex.select = False
    for edge in mesh.edges:
        edge.select = False
    for polygon in mesh.polygons:
        polygon.select = polygon.index in selected_ids
    settings = context.scene.tool_settings
    sync = settings.use_uv_select_sync
    settings.use_uv_select_sync = True  # Private stage scene: mesh selection is the UV selection.
    try:
        with _edit_override(context, obj):
            bm = bmesh.from_edit_mesh(mesh)
            for face in bm.faces:
                if face.select:
                    face.select_set(True)
            bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
            result = call()
            if 'FINISHED' not in result:
                raise RefusalError('The native UV operator did not complete', code='INTERNAL_ERROR')
    finally:
        settings.use_uv_select_sync = sync
        for sequence, flags in zip((mesh.vertices, mesh.edges, mesh.polygons), original_selection):
            for element, flag in zip(sequence, flags):
                element.select = flag
    return _readback(obj, operation, uv_map=uv_map_name, faces=len(selected))


def _uv_smart_project(context, obj, operation):
    params = operation['parameters']
    return _uv_operator(context, obj, operation, True, lambda: bpy.ops.uv.smart_project(
        angle_limit=params['angle_limit'], island_margin=params['island_margin'], area_weight=params['area_weight'],
        correct_aspect=params['correct_aspect'], scale_to_bounds=params['scale_to_bounds']))


def _uv_pack(context, obj, operation):
    params = operation['parameters']
    return _uv_operator(context, obj, operation, False, lambda: bpy.ops.uv.pack_islands(
        rotate=params['rotate'], margin=params['margin'], margin_method=params['margin_method'],
        shape_method=params['shape_method']))


# The most mesh elements x selected elements (V+E+F each, after the selection pass) one unwrap runs on: about 2 s
# for its worst measured input, a quad or triangle grid of one island (native_coverage.rst, "operator_run work
# bounds"). Above it the solve grows faster than linearly: 25 s at 198,000 elements, 45 s at 393,000.
UNWRAP_BOUND = 2.5e9


def _unwrap_work(mesh, faces):
    """(elements, selected): the mesh's V+E+F and those of ``faces`` with the vertices and edges they touch."""
    count = len(mesh.polygons)
    totals, loop_vertex, loop_edge = (np.empty(size, np.int32) for size in (count, len(mesh.loops), len(mesh.loops)))
    mesh.polygons.foreach_get('loop_total', totals)
    mesh.loops.foreach_get('vertex_index', loop_vertex)
    mesh.loops.foreach_get('edge_index', loop_edge)
    chosen = np.zeros(count, bool)
    chosen[[face.index for face in faces]] = True
    in_face = np.repeat(chosen, totals)  # Loops are stored face after face.
    touched = []
    for loops, size in ((loop_vertex, len(mesh.vertices)), (loop_edge, len(mesh.edges))):
        flags = np.zeros(size, bool)
        flags[loops[in_face]] = True
        touched.append(int(flags.sum()))
    return len(mesh.vertices) + len(mesh.edges) + count, len(faces) + sum(touched)


def _uv_unwrap(context, obj, operation):
    mesh = _mesh(context, obj)
    params = operation['parameters']
    selected = _face_selection(mesh, params)
    seams = _selected(mesh.edges, params['seam_edge_indices'])
    if not selected:
        raise RefusalError('Unwrap requires at least one staged face', code='VALIDATION')
    elements, touched = _unwrap_work(mesh, selected)
    if elements * touched > UNWRAP_BOUND:
        raise RefusalError('WORK_BOUND: uv_unwrap takes time growing faster than linearly with its input: %d elements with '
                         '%d selected exceed its native bound; use uv_smart_project for a dense mesh, or unwrap at most '
                         '4,096 faces (face_indices) at a time' % (elements, touched), code='LIMIT_EXCEEDED')
    layer = uv_map(mesh, params)
    if layer is None:
        if len(mesh.uv_layers) >= 8:
            raise RefusalError('Native UV actions are limited to eight maps', code='LIMIT_EXCEEDED')
        layer = mesh.uv_layers.new(name=params.get('uv_map') or 'UVMap')
    mesh.uv_layers.active = layer
    # Entering/exiting Edit mode replaces custom-data attribute storage. Never
    # keep a MeshUVLoopLayer RNA pointer for readback across that transition.
    uv_map_name = layer.name
    selected_ids = {polygon.index for polygon in selected}
    original_selection = ([vertex.select for vertex in mesh.vertices], [edge.select for edge in mesh.edges],
                          [polygon.select for polygon in mesh.polygons])
    for vertex in mesh.vertices:
        vertex.select = False
    for edge in mesh.edges:
        edge.select = False
    for polygon in mesh.polygons:
        polygon.select = polygon.index in selected_ids
    for edge in seams:
        edge.use_seam = True
    try:
        with _edit_override(context, obj):
            bm = bmesh.from_edit_mesh(mesh)
            for face in bm.faces:
                if face.select:
                    face.select_set(True)
            bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
            if not bpy.ops.uv.unwrap.poll():
                raise RefusalError('Native unwrap is unavailable in this staged context', code='UNSUPPORTED')
            result = bpy.ops.uv.unwrap(method=params['method'], margin=params['margin'],
                                       margin_method=params['margin_method'], fill_holes=params['fill_holes'],
                                       correct_aspect=params['correct_aspect'], use_subsurf_data=False, use_original_bounds=False)
            if 'FINISHED' not in result:
                raise RefusalError('Native UV unwrap did not complete', code='INTERNAL_ERROR')
    finally:
        for sequence, flags in zip((mesh.vertices, mesh.edges, mesh.polygons), original_selection):
            for element, flag in zip(sequence, flags):
                element.select = flag
    return _readback(obj, operation, uv_map=uv_map_name, unwrapped_faces=len(selected))
