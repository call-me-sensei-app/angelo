# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Typed staged camera/light kernels on private object data only.

Settings use the closed parameter -> RNA maps of action_camera_light_spec.
Every written value is read back from RNA and compared with its approved
value. look_at is solved analytically, then proven on the evaluated staged
world matrix. Animated data and custom light node graphs are refused rather
than configured into values an evaluation would override or reinterpret."""

import bpy
from .refusal_codes import RefusalError
from .action_camera_light_spec import (
    CAMERA_RNA, LIGHT_RNA, AIM_TOLERANCE, aim_error, check_applicable, check_clip_range,
    data_fields, family_of, is_default_light_graph, look_at_euler,
)
from .action_context import _readback
from .action_budget import _update_stage
from .program_names import name_created

_MAPS = {'camera': ('CAMERA', 'projection', CAMERA_RNA), 'light': ('LIGHT', 'light_type', LIGHT_RNA)}


def settings_readback(data, mapping):
    """Catalog settings present on this RNA subtype (spot_size only on spot lights)."""
    return {name: (list(getattr(data, prop)) if name == 'color' else getattr(data, prop))
            for name, prop in mapping.items() if hasattr(data, prop)}


def object_settings(obj):
    """Bounded staged/live read-back for deltas: {'camera'|'light': settings} or {}."""
    if obj.type == 'CAMERA':
        return {'camera': settings_readback(obj.data, CAMERA_RNA)}
    if obj.type == 'LIGHT':
        return {'light': settings_readback(obj.data, LIGHT_RNA)}
    return {}


def _same(actual, expected):
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(_same(a, e) for a, e in zip(actual, expected))
    if isinstance(expected, float):
        # float32 storage: compare relative to the approved magnitude.
        return abs(actual - expected) <= 1e-6 * max(1., abs(expected))
    return actual == expected


def _assert_unanimated(data, names):
    """Refuse writing data settings an F-curve, driver or NLA track holds (the RNA ``names`` written).

    Only those: setting clip_end on a camera whose lens is keyed holds, as the
    pre-approval check (program_frames.unkeyed_writes) and the staged property
    checks see it (otherwise any data animation was refused after
    approval)."""
    from .validation_properties import _curves
    curves = _curves(data)
    held = sorted(name for name in names if '*' in curves or name in curves)
    if held:
        raise RefusalError('Animated or driven camera/light data would override configured settings (%s); '
                           'use a reviewed keyframe workflow' % ', '.join(held[:8]), code='UNSUPPORTED')


def _configure_data(context, obj, kind, params):
    """Write the canonical settings; validate the resulting type before writing."""
    family = family_of(kind)
    object_type, type_key, mapping = _MAPS[family]
    if obj.type != object_type:
        raise RefusalError(kind + ' requires a staged ' + object_type + ' object', code='VALIDATION')
    data = context.require_private(obj.data)
    fields = data_fields(kind, params)
    if not fields:
        return settings_readback(data, mapping)
    settings = params['settings']
    _assert_unanimated(data, [mapping[name] for name in fields])
    if family == 'light' and not is_default_light_graph(data.node_tree):
        raise RefusalError('Custom light node graphs are outside the closed light catalog', code='UNSUPPORTED')
    resulting_type = settings.get(type_key, data.type)
    check_applicable(family, resulting_type, fields)
    if family == 'camera':
        check_clip_range(settings.get('clip_start', data.clip_start), settings.get('clip_end', data.clip_end))
    if type_key in settings:
        previous = data
        pointer = data.as_pointer()
        data.type = settings[type_key]
        # The RNA wrapper's subtype (PointLight, SpotLight...) is fixed when it
        # is created, and wrappers of different subtypes compare unequal. Re-read
        # the same, already private, ID so type-specific properties resolve.
        data = obj.data
        if data.as_pointer() != pointer:
            raise RefusalError('Changing the ' + family + ' type replaced its data unexpectedly', code='INTERNAL_ERROR')
        context.refresh_id(previous, data)
    if family == 'light' and resulting_type == 'AREA':
        check_applicable(family, resulting_type, fields, settings.get('shape', data.shape))
    for name in fields:
        if name != type_key:
            setattr(data, mapping[name], settings[name])  # mapping is the closed catalog allowlist.
    actual = settings_readback(data, mapping)
    for name in fields:
        if name not in actual or not _same(actual[name], settings[name]):
            raise RefusalError('Native ' + family + ' setting did not retain its approved value: ' + name, code='INTERNAL_ERROR')
    return actual


def _aim(context, obj, point):
    """Set an exact XYZ rotation aiming -Z at a world point, then prove it."""
    if point is None:
        return {}
    if obj.parent is not None or any(not constraint.mute and constraint.influence != 0 for constraint in obj.constraints):
        raise RefusalError('look_at requires an unparented object without active constraints', code='VALIDATION')
    animation = obj.animation_data
    if animation is not None and (animation.action is not None or len(animation.drivers) or len(animation.nla_tracks)):
        raise RefusalError('look_at on animated objects requires a reviewed keyframe workflow', code='UNSUPPORTED')
    if any(value != 0 for value in obj.delta_rotation_euler) or list(obj.delta_rotation_quaternion) != [1, 0, 0, 0]:
        raise RefusalError('look_at requires an identity delta rotation', code='VALIDATION')
    if any(value <= 0 for value in list(obj.scale) + list(obj.delta_scale)):
        raise RefusalError('look_at requires positive object and delta scale', code='VALIDATION')
    # Unparented and unconstrained: the world origin is location + delta_location,
    # read back from the stored float32 channels rather than the request.
    origin = [a + b for a, b in zip(obj.location, obj.delta_location)]
    obj.rotation_mode = 'XYZ'
    obj.rotation_euler = look_at_euler(origin, point)
    _update_stage(context)
    error = aim_error([list(row) for row in obj.matrix_world], point)
    if error > AIM_TOLERANCE:
        raise RefusalError('Native look_at did not aim the evaluated -Z axis at the approved point', code='INTERNAL_ERROR')
    return {'look_at': list(point), 'rotation': list(obj.rotation_euler), 'aim_error_radians': error}


def create(context, operation):
    params = operation['parameters']
    if 'orbit' in params:  # The controller resolves orbits before approval (program_orbits); never guess one here.
        raise RefusalError('An orbit placement reached staging unresolved; give location', code='VALIDATION')
    family = family_of(operation['kind'])
    if family == 'camera':
        data = context.track_id(bpy.data.cameras.new(params['name']))
    else:
        data = context.track_id(bpy.data.lights.new(params['name'], params['settings']['light_type']))
    obj = context.track_id(bpy.data.objects.new(params['name'], data))
    obj.location = params['location']
    obj.rotation_mode = 'XYZ'
    obj.rotation_euler = params['rotation']
    context.add_object(obj, params['alias'])
    alias = ('$' + params['alias']) if params['alias'] else None
    # The name in program order (program_names): one an earlier rename or delete released is free, one a pending
    # rename gives is taken. A suffixed name fails the derived object_name check.
    name = name_created(context, obj, params['name'], alias, bpy.data.objects.get)
    settings = _configure_data(context, obj, operation['kind'], params)
    aim = _aim(context, obj, params.get('look_at'))
    return _readback(obj, operation, object=name, created=True, alias=alias, **{family + '_settings': settings}, **aim)


def configure(context, obj, operation):
    params = operation['parameters']
    settings = _configure_data(context, obj, operation['kind'], params)
    aim = _aim(context, obj, params.get('look_at'))
    return _readback(obj, operation, **{family_of(operation['kind']) + '_settings': settings}, **aim)
