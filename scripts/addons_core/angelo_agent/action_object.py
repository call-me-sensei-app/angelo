# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Typed object transform, hierarchy, keyframe and explicit rig kernels.

All dependencies resolve within the stage. Keyframes bind the correct private
scene/view-layer pair without changing either scene frame or the artist window."""

import math
import bpy
from mathutils import Euler, Matrix, Vector
from .refusal_codes import RefusalError, refusal_code, refusal_details
from .action_context import _mesh, _readback, _set_initial_transform
from .action_budget import _update_stage
from .action_edit_context import _edit_override
from .program_names import name_created, name_new_slot


def _transform(context, obj, operation):
    params = operation['parameters']
    for name, property_name in [('location', 'location'), ('rotation', 'rotation_euler'), ('scale', 'scale')]:
        value = params[name]
        if value is None:
            continue
        if name == 'rotation' and obj.rotation_mode != 'XYZ':
            obj.rotation_euler = obj.rotation_quaternion.to_euler('XYZ') if obj.rotation_mode == 'QUATERNION' else obj.matrix_basis.to_euler('XYZ')
            obj.rotation_mode = 'XYZ'
        if params['mode'] == 'SET':
            setattr(obj, property_name, value)  # property_name is this closed three-value local enum.
        elif name == 'scale':
            obj.scale = [a * b for a, b in zip(obj.scale, value)]
        elif name == 'rotation':
            obj.rotation_euler = (obj.rotation_euler.to_matrix() @ Euler(value, 'XYZ').to_matrix()).to_euler('XYZ')
        else:
            obj.location = obj.location + Vector(value)
    if any(not math.isfinite(value) or abs(value) > 100000 for value in obj.location) or any(
            not math.isfinite(value) or abs(value) > 1000 for value in obj.scale):
        raise RefusalError('Resulting transform exceeds finite bounds', code='VALIDATION')
    if any(abs(value) < .000001 for value in obj.scale):
        raise RefusalError('Resulting scale is singular', code='VALIDATION')
    return _readback(obj, operation, location=list(obj.location), rotation=list(obj.rotation_euler), scale=list(obj.scale))



def _dependency_reaches(context, start, goal):
    pending, seen = [start], set()
    while pending:
        current = pending.pop()
        if current == goal:
            return True
        if current in seen:
            continue
        context.assert_staged(current)
        seen.add(current)
        if len(seen) > 128:
            raise RefusalError('Object dependency graph exceeds the staged work budget', code='LIMIT_EXCEEDED')
        if current.parent is not None:
            pending.append(current.parent)
        for constraint in current.constraints:
            target = getattr(constraint, 'target', None)
            if target is not None:
                pending.append(target)
        for modifier in current.modifiers:
            target = getattr(modifier, 'object', None)
            if target is not None:
                pending.append(target)
    return False



def _parent(context, obj, operation):
    params = operation['parameters']
    parent = context.resolve(params['parent']) if params['parent'] is not None else None
    if parent is not None and _dependency_reaches(context, parent, obj):
        raise RefusalError('Native parenting cannot create a dependency cycle', code='VALIDATION')
    if params['keep_transform'] and any(not constraint.mute and constraint.influence != 0 for constraint in obj.constraints):
        # matrix_world includes constraint output. Feeding it into the basis
        # evaluates offset/local constraints twice. Do not disable or bake them.
        raise RefusalError('keep_transform parenting with active owner constraints is unsupported; use an unconstrained object or explicit local parenting',
                           code='UNSUPPORTED')
    _update_stage(context)
    world = obj.matrix_world.copy()
    inverse = None
    if params['keep_transform']:
        if obj.parent is not None and obj.parent_type != 'OBJECT':
            raise RefusalError('keep_transform supports existing Object parenting only, not bone or vertex parenting',
                               code='UNSUPPORTED')
        if parent is not None:
            effect = obj.parent.matrix_world @ obj.matrix_parent_inverse if obj.parent is not None else Matrix.Identity(4)
            try:
                inverse = parent.matrix_world.inverted() @ effect
            except ValueError as error:
                if refusal_code(error) == 'UNKNOWN' and _matrix_is_provably_singular(parent.matrix_world):
                    raise RefusalError('keep_transform cannot use a singular parent world matrix',
                                       code='VALIDATION') from error
                raise RefusalError('keep_transform cannot use a singular parent world matrix',
                                   code=refusal_code(error), details=refusal_details(error)) from error
        elif not _same_world_matrix(world, Matrix.LocRotScale(*world.decompose())):
            raise RefusalError('Unparenting cannot preserve a sheared world transform in local transform channels', code='UNSUPPORTED')
    obj.parent = parent
    obj.parent_type = 'OBJECT'
    if params['keep_transform']:
        if parent is not None:
            # Keep the pre-constraint basis (including delta channels) intact.
            # Changing the parent inverse preserves the old world effect even
            # with rotated/nonuniform parents, without decomposing a shear.
            obj.matrix_parent_inverse = inverse
        else:
            obj.matrix_parent_inverse = Matrix.Identity(4)
            obj.matrix_world = world
        _update_stage(context)
        if not _same_world_matrix(world, obj.matrix_world):
            raise RefusalError('Native parenting did not preserve the approved world transform', code='INTERNAL_ERROR')
    return _readback(obj, operation, parent=params['parent'], keep_transform=params['keep_transform'],
                     world_matrix=[list(row) for row in obj.matrix_world])


def _same_world_matrix(expected, actual):
    return all(math.isfinite(actual[i][j]) and abs(expected[i][j] - actual[i][j]) <= 1e-6 * max(1, abs(expected[i][j]))
               for i in range(4) for j in range(4))


def _matrix_is_provably_singular(matrix):
    """A failed inverse is correctable only when the matrix proves its cause.

    Called only on the failure path. A missing or failed diagnostic must not
    replace the original exception or grant correction authority.
    """
    try:
        determinant = matrix.determinant()
        return type(determinant) in (float, int) and math.isfinite(determinant) and determinant == 0
    except Exception:
        return False



# Typed constraint settings that write one RNA property per axis; every other typed setting is its RNA name.
CONSTRAINT_AXES = {'axes': 'use_%s', 'invert': 'invert_%s', 'minimum': 'min_%s', 'maximum': 'max_%s',
                   'use_min': 'use_min_%s', 'use_max': 'use_max_%s', 'use_limit': 'use_limit_%s',
                   'use_location': 'use_location_%s', 'use_rotation': 'use_rotation_%s', 'use_scale': 'use_scale_%s'}


def constraint_writes(params):
    """``{rna property: value}`` a typed ``constraint_add`` writes: what it states, and nothing else (SETTINGS_NOTE)."""
    written = {key: params[key] for key in ('influence', 'owner_space') if key in params}
    if params['target'] is not None and 'target_space' in params:
        written['target_space'] = params['target_space']
    for key, value in params['settings'].items():
        if key == 'set_inverse':
            continue
        if key in CONSTRAINT_AXES:
            written.update((CONSTRAINT_AXES[key] % axis, item) for axis, item in zip('xyz', value))
        else:
            written[key] = value
    return written


def _constraint(context, obj, operation):
    """A new typed constraint: Blender's own defaults, then the influence, spaces and settings the call states."""
    params, settings = operation['parameters'], operation['parameters']['settings']
    target = context.resolve(params['target']) if params['target'] is not None else None
    if target == obj:
        raise RefusalError('Constraint target resolves to the owner itself', code='VALIDATION')
    if target is not None and _dependency_reaches(context, target, obj):
        raise RefusalError('Native constraints cannot create a dependency cycle', code='VALIDATION')
    if len(obj.constraints) >= 16 or obj.constraints.get(params['name']) is not None:
        raise RefusalError('Constraints require a unique name and at most 16 constraints', code='VALIDATION')
    constraint = obj.constraints.new(params['type'])
    constraint.name = params['name']
    if target is not None:
        constraint.target = target
    for key, value in constraint_writes(params).items():
        setattr(constraint, key, value)
    if params['type'] == 'TRACK_TO' and constraint.track_axis[-1] == constraint.up_axis[-1]:
        raise RefusalError('Track To needs track_axis and up_axis on different axes (it is %s and %s)'
                           % (constraint.track_axis, constraint.up_axis), code='VALIDATION')
    if params['type'] in {'LIMIT_LOCATION', 'LIMIT_ROTATION', 'LIMIT_SCALE'}:
        for axis in 'xyz':
            lower = getattr(constraint, ('use_limit_' if params['type'] == 'LIMIT_ROTATION' else 'use_min_') + axis)
            upper = getattr(constraint, ('use_limit_' if params['type'] == 'LIMIT_ROTATION' else 'use_max_') + axis)
            if lower and upper and getattr(constraint, 'min_' + axis) > getattr(constraint, 'max_' + axis):
                raise RefusalError('Constraint minimum cannot exceed maximum (%s axis)' % axis.upper(),
                                   code='VALIDATION')
    if params['type'] == 'CHILD_OF' and settings.get('set_inverse', True):
        _update_stage(context)
        try:
            constraint.inverse_matrix = target.matrix_world.inverted()
        except ValueError as error:
            if refusal_code(error) == 'UNKNOWN' and _matrix_is_provably_singular(target.matrix_world):
                raise RefusalError('Child Of cannot set an inverse for a singular target matrix',
                                   code='VALIDATION') from error
            raise RefusalError('Child Of cannot set an inverse for a singular target matrix',
                               code=refusal_code(error), details=refusal_details(error)) from error
    return _readback(obj, operation, constraint=constraint.name)



def _fcurves(action):
    if hasattr(action, 'layers'):
        for layer in action.layers:
            for strip in layer.strips:
                for channelbag in getattr(strip, 'channelbags', []):
                    yield from channelbag.fcurves
    elif hasattr(action, 'fcurves'):
        yield from action.fcurves



def _keyframe(context, obj, operation):
    params = dict(operation['parameters'])
    params.setdefault('frame', context.scene.frame_current)  # Left out: the current frame, as Blender keys.
    animation = obj.animation_data
    if animation is not None:
        if animation.drivers or animation.nla_tracks:
            raise RefusalError('Keyframes on driver/NLA objects require a separate supported workflow', code='UNSUPPORTED')
        if animation.action is not None:
            action = context.require_private(animation.action)
            if sum(len(curve.keyframe_points) for curve in _fcurves(action)) > 4096:
                raise RefusalError('Existing animation exceeds the bounded keyframe limit', code='LIMIT_EXCEEDED')
    # RNA keyframe_insert obtains its depsgraph from BPY_context_get(), even
    # with an explicit frame and an original staged object. In this Blender
    # build, an outer temp_override(window=..., scene=..., view_layer=...) calls
    # CTX_wm_window_set(), which clears its Python "scene" override but leaves
    # "view_layer". Rebind the pair *without setting window* so RNA cannot
    # combine the artist's scene with a view layer from the disposable stage.
    # No frame_set/window.scene assignment or host active/selection change.
    name_new_slot(context.extras, obj, operation['target'])  # A new slot takes the live name, as Blender's would.
    with bpy.context.temp_override(scene=context.scene, view_layer=context.view_layer):
        _transform(context, obj, {'kind': 'transform', 'target': operation['target'], 'parameters': {
            'mode': 'SET', 'location': params['value'] if params['property'] == 'location' else None,
            'rotation': params['value'] if params['property'] == 'rotation_euler' else None,
            'scale': params['value'] if params['property'] == 'scale' else None}})
        if not obj.keyframe_insert(data_path=params['property'], frame=params['frame']):
            raise RefusalError('Native keyframe insertion failed', code='INTERNAL_ERROR')
        action = obj.animation_data.action
        if action not in context.owned_ids:
            context.track_id(action)
            # Blender names a new Action after the private staged copy
            # (__angelo_copy_...); give it the live object's name instead.
            live = context.extras.get('live_names', {}).get(operation['target'])
            if live is not None:
                action.name = live + 'Action'
        for curve in _fcurves(action) if 'interpolation' in params else ():  # Left out: the key's own, or the preference.
            if curve.data_path == params['property']:
                for key in curve.keyframe_points:
                    if abs(key.co.x - params['frame']) < .00001:
                        key.interpolation = params['interpolation']
        # Its animation is evaluated again, as a written key point tags it: the stage's transform is then what the
        # committed curves give at the current frame, with or without an interpolation written.
        obj.update_tag(refresh={'TIME'})
    return _readback(obj, operation, action=action.name, frame=params['frame'], property=params['property'])



def _armature(context, operation):
    params = operation['parameters']
    data = context.track_id(bpy.data.armatures.new(params['name'] + ' Rig'))
    obj = context.track_id(bpy.data.objects.new(params['name'], data))
    _set_initial_transform(obj, params)
    context.add_object(obj, params['alias'])
    alias = ('$' + params['alias']) if params['alias'] else None
    name = name_created(context, obj, params['name'], alias, bpy.data.objects.get)  # In program order (program_names).
    with _edit_override(context, obj):
        bones = {}
        for definition in params['bones']:
            bone = data.edit_bones.new(definition['name'])
            bone.head, bone.tail, bone.roll = definition['head'], definition['tail'], definition['roll']
            if definition['parent'] is not None:
                bone.parent = bones[definition['parent']]
                bone.use_connect = definition['connected']
            bones[definition['name']] = bone
        for definition in params['bones']:
            bone = bones[definition['name']]
            if list(bone.head) != definition['head'] or list(bone.tail) != definition['tail']:
                raise RefusalError('Native edit bone endpoints do not match the approved float32 coordinates', code='INTERNAL_ERROR')
    endpoints = []
    for definition in params['bones']:
        bone = data.bones.get(definition['name'])
        if bone is None or list(bone.head_local) != definition['head'] or list(bone.tail_local) != definition['tail']:
            raise RefusalError('Native rest bone endpoints changed from the approved float32 coordinates', code='INTERNAL_ERROR')
        actual_parent = bone.parent.name if bone.parent else None
        if actual_parent != definition['parent'] or bool(bone.use_connect) != definition['connected']:
            raise RefusalError('Native bone hierarchy changed from the approved definition', code='INTERNAL_ERROR')
        endpoints.append({'name': bone.name, 'head': list(bone.head_local), 'tail': list(bone.tail_local),
                          'parent': actual_parent, 'connected': bool(bone.use_connect)})
    return _readback(obj, operation, object=name, created=True, alias=alias, bones=len(data.bones),
                     bone_endpoints=endpoints)



def _bind(context, obj, operation):
    mesh = _mesh(context, obj)
    params = operation['parameters']
    armature = context.resolve(params['armature'])
    if armature == obj or armature.type != 'ARMATURE':
        raise RefusalError('Explicit rig binding requires a different staged armature', code='VALIDATION')
    context.require_private(armature.data)
    if _dependency_reaches(context, armature, obj):
        raise RefusalError('Armature binding cannot create a dependency cycle', code='VALIDATION')
    if len(obj.modifiers) >= 16 or obj.modifiers.get(params['name']) is not None:
        raise RefusalError('Armature binding requires a unique modifier name', code='VALIDATION')
    if len(obj.vertex_groups) + len(params['groups']) > 64:
        raise RefusalError('Native rig binding is limited to 64 vertex groups', code='LIMIT_EXCEEDED')
    for group in params['groups']:
        if armature.data.bones.get(group['bone']) is None or obj.vertex_groups.get(group['bone']) is not None:
            raise RefusalError('Weights must address existing bones and new vertex group names', code='VALIDATION')
        if any(weight['vertex'] >= len(mesh.vertices) for weight in group['weights']):
            raise RefusalError('A rig weight vertex is outside the staged mesh', code='VALIDATION')
    for definition in params['groups']:
        group = obj.vertex_groups.new(name=definition['bone'])
        for weight in definition['weights']:
            group.add([weight['vertex']], weight['weight'], 'REPLACE')
    modifier = obj.modifiers.new(params['name'], 'ARMATURE')
    modifier.object, modifier.use_vertex_groups, modifier.use_bone_envelopes = armature, True, False
    modifier.use_deform_preserve_volume = params['preserve_volume']
    return _readback(obj, operation, modifier=modifier.name, armature=params['armature'],
                     groups=len(params['groups']))
