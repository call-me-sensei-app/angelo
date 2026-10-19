# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Curve and text kernels: explicit curve points, and conversion to a mesh object.

``curve_points`` replaces a staged curve's splines with one spline through
explicit local points (poly, Bezier or NURBS); commit swaps the curve data like
any curve edit. ``object_convert`` evaluates a staged curve or text object on
the stage and creates a new mesh object from it (as Blender's Convert to Mesh
with Keep Original), placed and parented like its source and linked into the
source's collections at commit; without ``keep_original`` the source is
deleted through ``object_delete``'s commit path, and an unnamed result takes
its name, as Blender's conversion in place keeps it (``program_names``). The
evaluated mesh is bounded before evaluation by an estimate from the curve
settings and after it by the native mesh limits.
"""

import bpy

from .refusal_codes import RefusalError
from .action_context import _readback, _check_counts
from .action_schema_types import MAX_EVALUATED_VERTICES
from .program_names import current_name, name_created

CONVERTIBLE = frozenset({'CURVE', 'FONT'})
# Vertices per character outline at resolution 1 (a generous bound for common fonts).
_CHARACTER_POINTS = 48


def set_points(context, obj, operation):
    params = operation['parameters']
    if obj.type != 'CURVE':
        raise RefusalError('curve_points edits curve objects (text uses property_set data.body)', code='VALIDATION')
    curve = context.require_private(obj.data)
    if curve.bevel_object is not None or curve.taper_object is not None:
        raise RefusalError('Curves with bevel or taper objects are reviewed Python', code='UNSUPPORTED')
    points = params['points']
    curve.splines.clear()
    spline = curve.splines.new(params['spline_type'])
    if params['spline_type'] == 'BEZIER':
        spline.bezier_points.add(len(points) - 1)
        for point, co in zip(spline.bezier_points, points):
            point.co = co
            point.handle_left_type = point.handle_right_type = params['handle_type']
    else:
        spline.points.add(len(points) - 1)
        for point, co in zip(spline.points, points):
            point.co = (*co, 1.0)
        if params['spline_type'] == 'NURBS':
            spline.order_u = min(4, len(points))
            spline.use_endpoint_u = not params['cyclic']
    spline.use_cyclic_u = params['cyclic']
    if params['dimensions'] is not None:
        curve.dimensions = params['dimensions']
    return _readback(obj, operation, points=len(points), spline_type=params['spline_type'], cyclic=params['cyclic'])


def _estimated_vertices(obj):
    """An upper estimate of the evaluated mesh, read from settings only (no evaluation)."""
    curve = obj.data
    ring = max(1, 2 * (curve.bevel_resolution + 2)) if curve.bevel_depth > 0 else 1
    ring *= 2 if curve.extrude > 0 else 1
    resolution = max(1, curve.resolution_u)
    if obj.type == 'FONT':
        return len(curve.body) * _CHARACTER_POINTS * resolution * ring * 2
    points = sum(len(spline.bezier_points) + len(spline.points) for spline in curve.splines)
    return points * resolution * ring * 2


def convert(context, operation):
    from .action_budget import _update_stage
    params = operation['parameters']
    source = context.resolve(params['source'])
    if source.type not in CONVERTIBLE:
        raise RefusalError('object_convert converts curve and text objects to meshes', code='VALIDATION')
    if params['source'].startswith('$') and not params['keep_original']:
        raise RefusalError('Convert a new object with keep_original; do not create and delete it in one program', code='VALIDATION')
    if source.data.bevel_object is not None or source.data.taper_object is not None:
        raise RefusalError('Curves with bevel or taper objects are reviewed Python', code='UNSUPPORTED')
    if source.modifiers:
        raise RefusalError('object_convert CURVE_MODIFIERS: curve modifiers are reviewed Python', code='UNSUPPORTED')
    if _estimated_vertices(source) > MAX_EVALUATED_VERTICES:
        raise RefusalError('The converted mesh would exceed the bounded native capability', code='LIMIT_EXCEEDED')
    if not params['keep_original']:
        from .action_object_management import require_deletable
        require_deletable(context, source, 'object_convert')
    _update_stage(context)
    depsgraph = context.view_layer.depsgraph
    evaluated = source.evaluated_get(depsgraph)
    mesh = context.track_id(bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=False,
                                                            depsgraph=depsgraph))
    _check_counts(len(mesh.vertices), len(mesh.edges), len(mesh.polygons))
    wanted = params['name'] or current_name(context.extras, params['source'], source)
    obj = context.track_id(bpy.data.objects.new(wanted, mesh))
    obj.parent, obj.matrix_parent_inverse = source.parent, source.matrix_parent_inverse.copy()
    obj.rotation_mode = source.rotation_mode
    obj.location, obj.scale = source.location, source.scale
    obj.rotation_euler, obj.rotation_quaternion = source.rotation_euler, source.rotation_quaternion
    obj.rotation_axis_angle = source.rotation_axis_angle
    alias = '$' + params['alias']
    context.add_object(obj, params['alias'])
    if not params['keep_original']:  # First: the replaced source releases its name to this object (program_names).
        deleted = context.extras.setdefault('deleted', [])
        if params['source'] not in deleted:
            deleted.append(params['source'])
    name = name_created(context, obj, wanted, alias, bpy.data.objects.get, requested=bool(params['name']))
    if params['name'] and name != params['name']:
        raise RefusalError('object_convert NAME_TAKEN: another object is named ' + repr(params['name'][:64]), code='VALIDATION')
    from .action_object_management import _membership
    context.extras.setdefault('duplicate_sources', {})[alias] = params['source']
    context.extras.setdefault('memberships', {})[alias] = _membership(context, params['source'])
    return _readback(obj, operation, object=name, created=True, alias=alias, name=name, source_type=source.type)
