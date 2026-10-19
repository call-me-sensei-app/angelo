# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Object management kernels: rename, delete, duplicate, apply transforms, origin, collections.

Geometry changes (apply transforms, set origin, a full duplicate's data) happen
on staged private copies like any mesh edit. Changes that only exist in the
live scene's structure are staged as intents in ``context.extras`` and carried
out by commit inside the Undo operator, each journaled for rollback:

* ``renames``: handle -> new name (a staged copy keeps its private name);
* ``deleted``: handles to unlink from the bound scene and remove;
* ``linked_duplicates`` and ``duplicate_sources``: alias -> source reference,
  so a linked duplicate shares the live data;
* ``collections``: reference -> ordered membership edits, with
  ``memberships`` holding each object's resulting collection names and
  ``live_memberships``/``scene_collections`` the bound scene's state when the
  stage was created. A new object's memberships start as its source's at that
  point of the program (``_membership``), so a duplicate made after its source
  moved joins the new collections; checks read them, and commit links each new
  object into exactly them (``program_memberships``).

Taken names, objects other objects still depend on, unknown collections and
children that would move fail closed rather than being suffixed, cleared or
moved silently. A name is taken or free in program order: one an earlier
rename or delete of this program released is free although its live holder
keeps it until commit (``program_names``).
"""

import bpy
from mathutils import Matrix, Vector

from . import transform_bake
from .refusal_codes import RefusalError
from .action_context import _mesh, _readback, _set_initial_transform
from .program_names import current_name, live_rename_taken, name_created, rename_new_object

MASTER_COLLECTION = "Scene Collection"
DUPLICABLE = frozenset({'MESH', 'CURVE', 'FONT', 'EMPTY', 'CAMERA', 'LIGHT'})


def _extras(context, key, default):
    return context.extras.setdefault(key, default)


def _live_name(context, reference):
    return context.extras.get('live_names', {}).get(reference)


def rename(context, obj, operation):
    """Names follow program order: one an earlier rename or delete released is free (program_names)."""
    name, reference = operation['parameters']['name'], operation['target']
    if reference.startswith('$'):
        if rename_new_object(context, obj, name, reference, bpy.data.objects.get) != name:
            raise RefusalError('object_rename NAME_TAKEN: another object is named ' + repr(name[:64]), code='VALIDATION')
        return _readback(obj, operation, object=name, name=name)
    if live_rename_taken(context.extras, name, reference, bpy.data.objects.get):
        raise RefusalError('object_rename NAME_TAKEN: another object is named ' + repr(name[:64]), code='VALIDATION')
    _extras(context, 'renames', {})[reference] = name
    return _readback(obj, operation, name_before=_live_name(context, reference), name=name)


def require_deletable(context, obj, kind):
    """Refuse removing ``obj`` while another declared object depends on it."""
    users = bpy.data.user_map(subset={obj}).get(obj, set())
    dependents = [user for user in users if isinstance(user, bpy.types.Object) and user != obj
                  and any(user == staged for staged in context.collection.objects)]
    if dependents:
        raise RefusalError(kind + ' OBJECT_IN_USE: another declared object depends on it (parent, modifier or constraint)',
                           code='VALIDATION')


def delete(context, obj, operation):
    reference = operation['target']
    if reference.startswith('$'):
        raise RefusalError('object_delete removes existing objects; do not create an object in the program you delete it in',
                           code='VALIDATION')
    require_deletable(context, obj, 'object_delete')
    deleted = _extras(context, 'deleted', [])
    if reference not in deleted:
        deleted.append(reference)
    return _readback(obj, operation, deleted=True)


def duplicate(context, operation):
    params = operation['parameters']
    source = context.resolve(params['source'])
    if source.type not in DUPLICABLE:
        raise RefusalError('object_duplicate supports mesh, curve, text, empty, camera and light objects', code='VALIDATION')
    animation = source.animation_data
    if animation is not None and (animation.action is not None or len(animation.drivers) or len(animation.nla_tracks)):
        raise RefusalError('object_duplicate ANIMATED_SOURCE: duplicating animation is reviewed Python', code='UNSUPPORTED')
    copy = context.track_id(source.copy())
    if source.data is not None:
        # Staged data stays private even for a linked duplicate (stage meshes are
        # single-user); commit points a linked duplicate at the live source data.
        data = context.track_id(source.data.copy())
        if getattr(data, 'shape_keys', None) is not None:
            context.track_id(data.shape_keys)
        copy.data = data
    alias = '$' + params['alias']
    # Unnamed, it is named after its source's current name, which Blender suffixes.
    wanted = params['name'] or current_name(context.extras, params['source'], source)
    name = rename_new_object(context, copy, wanted, alias, bpy.data.objects.get, requested=bool(params['name']))
    if params['name'] and name != params['name']:
        raise RefusalError('object_duplicate NAME_TAKEN: another object is named ' + repr(params['name'][:64]),
                           code='VALIDATION')
    if params['location'] is not None:
        copy.location = params['location']
    context.add_object(copy, params['alias'])
    _extras(context, 'duplicate_sources', {})[alias] = params['source']
    if params['linked']:
        _extras(context, 'linked_duplicates', {})[alias] = params['source']
    memberships = _extras(context, 'memberships', {})
    memberships[alias] = list(_membership(context, params['source']))
    return _readback(copy, operation, created=True, alias=alias, linked=params['linked'], object=name, name=name)


def _no_children_or_deltas(obj, kind):
    if obj.children:
        raise RefusalError(kind + ' CHILDREN: applying would move child objects; unparent them first', code='VALIDATION')
    deltas = (list(obj.delta_location), list(obj.delta_rotation_euler), list(obj.delta_scale), list(obj.delta_rotation_quaternion))
    if deltas != ([0, 0, 0], [0, 0, 0], [1, 1, 1], [1, 0, 0, 0]):
        raise RefusalError(kind + ' DELTA_TRANSFORMS: delta transforms are reviewed Python', code='UNSUPPORTED')


def apply_transform(context, obj, operation):
    """Bake the chosen local transform channels into the private mesh, as Blender's Apply does.

    The world result is unchanged and only the applied channels are reset: a scale applied alone keeps the
    rotation channels exactly, also for a mirrored scale (transform_bake); faces are flipped when the baked
    matrix mirrors, as Blender's Corrective Flip Normals does.
    """
    mesh = _mesh(context, obj)
    _no_children_or_deltas(obj, 'transform_apply')
    params = operation['parameters']
    basis = obj.matrix_basis
    try:
        applied, translation, mirrors = transform_bake.bake(
            [list(row) for row in basis.to_3x3()], list(obj.scale), list(basis.translation),
            apply_location=params['location'], apply_rotation=params['rotation'], apply_scale=params['scale'])
    except ValueError as error:
        raise RefusalError('transform_apply ' + str(error), code='VALIDATION')
    bake = Matrix([applied[row] + [translation[row]] for row in range(3)] + [[0, 0, 0, 1]])
    mesh.transform(bake, shape_keys=True)
    if mirrors:
        mesh.flip_normals()
    if params['location']:
        obj.location = (0, 0, 0)
    if params['scale']:
        obj.scale = (1, 1, 1)
    if params['rotation']:  # As Blender's apply: every rotation representation, not only the active mode's.
        obj.rotation_euler, obj.rotation_quaternion, obj.rotation_axis_angle = (0, 0, 0), (1, 0, 0, 0), (0, 0, 1, 0)
    _mesh(context, obj)  # Bounded, finite result.
    return _readback(obj, operation, location=list(obj.location), scale=list(obj.scale),
                     baked_determinant=transform_bake.determinant(applied))


def set_origin(context, obj, operation):
    """Move the object origin without moving its geometry in the world."""
    from .action_budget import _update_stage
    mesh = _mesh(context, obj)
    _no_children_or_deltas(obj, 'origin_set')
    if not len(mesh.vertices):
        raise RefusalError('origin_set needs a mesh with vertices', code='VALIDATION')
    params = operation['parameters']
    if params['mode'] == 'POINT':
        _update_stage(context)
        center = obj.matrix_world.inverted() @ Vector(params['point'])
    else:
        coordinates = [vertex.co for vertex in mesh.vertices]
        if params['mode'] == 'MEDIAN':
            center = sum(coordinates, Vector()) / len(coordinates)
        else:
            center = Vector([(min(co[axis] for co in coordinates) + max(co[axis] for co in coordinates)) / 2 for axis in range(3)])
    mesh.transform(Matrix.Translation(-center), shape_keys=True)
    obj.matrix_basis = obj.matrix_basis @ Matrix.Translation(center)
    _mesh(context, obj)
    return _readback(obj, operation, location=list(obj.location), local_offset=list(center))


def _membership(context, reference):
    memberships = context.extras.get('memberships', {})
    if reference in memberships:
        return list(memberships[reference])
    if reference.startswith('$'):
        source = context.extras.get('duplicate_sources', {}).get(reference)
        return _membership(context, source) if source else [MASTER_COLLECTION]
    return list(context.extras.get('live_memberships', {}).get(reference, [MASTER_COLLECTION]))


def link_collection(context, obj, operation):
    params, reference = operation['parameters'], operation['target']
    name = params['collection']
    known = set(context.extras.get('scene_collections', [])) | {MASTER_COLLECTION}
    created = _extras(context, 'created_collections', [])
    if params['create']:
        # Local names only: a library collection of the same name is another ID.
        local = known - set(context.extras.get('linked_collections', ()))
        if name in local or name in created or bpy.data.collections.get((name, None)) is not None:
            raise RefusalError('collection_link NAME_TAKEN: a collection is already named ' + repr(name[:64]), code='VALIDATION')
        created.append(name)
    elif params['mode'] != 'UNLINK' and name in context.extras.get('linked_collections', ()):
        raise RefusalError('collection_link LINKED: %r comes from a library and cannot hold objects of this file; '
                           'link into a local collection' % name[:64], code='VALIDATION')
    elif name not in known and name not in created:
        raise RefusalError('collection_link COLLECTION_NOT_FOUND: no collection of the bound scene is named ' + repr(name[:64]),
                           code='VALIDATION')
    if params['create'] and params['mode'] == 'UNLINK':
        raise RefusalError('collection_link cannot create a collection only to unlink from it', code='VALIDATION')
    members = _membership(context, reference)
    if params['mode'] == 'LINK':
        members = members + ([name] if name not in members else [])
    elif params['mode'] == 'MOVE':
        members = [name]
    else:
        if name not in members:
            raise RefusalError('collection_link NOT_LINKED: the object is not in ' + repr(name[:64]), code='VALIDATION')
        members = [member for member in members if member != name]
        if not members:
            raise RefusalError('collection_link LAST_COLLECTION: an object must stay in a collection of the scene', code='VALIDATION')
    hidden = context.extras.get('visibility', {}).get('objects', {})
    if reference in hidden:  # This program set its visibility: it must keep a Base (action_visibility).
        from .action_visibility import has_base, not_in_view_layer
        if has_base(context.extras, members) is False:
            raise not_in_view_layer('collection_link', current_name(context.extras, reference, obj), members,
                                    ', after this program set its visibility')
    _extras(context, 'memberships', {})[reference] = members
    _extras(context, 'collections', {}).setdefault(reference, []).append(dict(params))
    return _readback(obj, operation, collections=members)


def create_object(context, operation):
    """Create an empty, a text object, a simple Bezier curve, a lattice or a Grease Pencil object on the stage."""
    params = operation['parameters']
    data = None
    if params['type'] == 'TEXT':
        data = context.track_id(bpy.data.curves.new(params['name'], 'FONT'))
        data.body, data.size, data.extrude, data.align_x = params['body'], params['size'], params['extrude'], params['align_x']
    elif params['type'] == 'LATTICE':
        data = context.track_id(bpy.data.lattices.new(params['name']))
        data.points_u, data.points_v, data.points_w = params['points']
    elif params['type'] == 'GREASEPENCIL':
        # Blender's blank Grease Pencil: one active layer with a frame at the current frame.
        data = context.track_id(bpy.data.grease_pencils.new(params['name']))
        data.layers.new(params['layer'], set_active=True).frames.new(context.scene.frame_current)
    elif params['type'] == 'CURVE':
        data = context.track_id(bpy.data.curves.new(params['name'], 'CURVE'))
        data.dimensions, data.bevel_depth = '3D', params['bevel_depth']
        spline = data.splines.new('BEZIER')
        radius = params['radius']
        if params['shape'] == 'BEZIER':
            spline.bezier_points.add(1)
            for point, x in zip(spline.bezier_points, (-radius, radius)):
                point.co = (x, 0, 0)
                point.handle_left_type = point.handle_right_type = 'AUTO'
        else:
            spline.bezier_points.add(3)
            for point, (x, y) in zip(spline.bezier_points, ((radius, 0), (0, radius), (-radius, 0), (0, -radius))):
                point.co = (x, y, 0)
                point.handle_left_type = point.handle_right_type = 'AUTO'
            spline.use_cyclic_u = True
    obj = context.track_id(bpy.data.objects.new(params['name'], data))
    if params['type'] == 'EMPTY':
        obj.empty_display_type, obj.empty_display_size = params['display'], params['display_size']
    _set_initial_transform(obj, params)
    context.add_object(obj, params['alias'])
    alias = ('$' + params['alias']) if params['alias'] else None
    if name_created(context, obj, params['name'], alias, bpy.data.objects.get) != params['name']:
        raise RefusalError('create_object NAME_TAKEN: another object is named ' + repr(params['name'][:64]), code='VALIDATION')
    return _readback(obj, operation, created=True, alias=alias, object=params['name'], name=params['name'])
