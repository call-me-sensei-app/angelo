# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Staged animation kernels: delete keys, shape keys and NLA push-down (anim_keys configures keys).

Keys are read and written through the staged object's own Action slot and its
channelbag (Blender 5 layered Actions), never ``Action.fcurves``. Every Action
edited here is the program's private copy (``require_private``), so the
artist's Action is untouched until commit transfers the staged one. Drivers
are refused, as for ``keyframe_insert``: their expressions are not evaluated
natively. Shape keys live in the mesh's Key datablock and travel with the
staged mesh copy at commit.
"""

import bpy

from .refusal_codes import RefusalError
from .action_context import _mesh, _readback
from .action_schema_types import MAX_INDEX_SELECTION
from .program_names import name_new_slot

FRAME_EPSILON = .0001


def channelbag(obj, action=None):
    """The channelbag of ``obj``'s assigned Action slot, or None."""
    animation = obj.animation_data
    action = action or (animation.action if animation is not None else None)
    if action is None:
        return None
    slot = getattr(animation, "action_slot", None)
    for layer in getattr(action, "layers", ()):
        for strip in layer.strips:
            if slot is not None and hasattr(strip, "channelbag"):
                bag = strip.channelbag(slot)
                if bag is not None:
                    return bag
    return None


def curves(bag, data_path, index):
    """F-Curves of one property, all three channels for index -1."""
    if bag is None:
        return []
    found = [bag.fcurves.find(data_path, index=channel) for channel in (range(4) if index < 0 else [index])]
    return [curve for curve in found if curve is not None]


def key_at(curve, frame):
    return next((point for point in curve.keyframe_points if abs(point.co.x - frame) < FRAME_EPSILON), None)


def _private_bag(context, obj, target):
    animation = obj.animation_data
    if animation is None or animation.action is None:
        raise RefusalError('KEYS_NOT_FOUND: the object has no Action with keys', code='VALIDATION')
    if len(animation.drivers):
        raise RefusalError('Animation with drivers needs reviewed Python; driver expressions are not evaluated natively',
                           code='UNSUPPORTED')
    from .anim_channels import guard
    guard(context, target, 'object')  # A shared Action is refused, not split silently (anim_channels).
    context.require_private(animation.action)
    bag = channelbag(obj)
    if bag is None:
        raise RefusalError('KEYS_NOT_FOUND: the object\'s Action slot has no channels', code='VALIDATION')
    return bag


def delete_key(context, obj, operation):
    params = operation['parameters']
    bag = _private_bag(context, obj, operation['target'])
    removed = 0
    for curve in curves(bag, params['property'], params['index']):
        point = key_at(curve, params['frame'])
        if point is not None:
            curve.keyframe_points.remove(point)
            removed += 1
        if not len(curve.keyframe_points):
            bag.fcurves.remove(curve)
    if not removed:
        raise RefusalError('KEY_NOT_FOUND: no %s key at frame %d' % (params['property'], params['frame']), code='VALIDATION')
    return _readback(obj, operation, removed_keys=removed, frame=params['frame'])


def _shape_keys(context, obj):
    mesh = _mesh(context, obj)
    key = mesh.shape_keys
    if key is not None:
        context.require_private(key)
    return mesh, key


def add_shape_key(context, obj, operation):
    params = operation['parameters']
    mesh, key = _shape_keys(context, obj)
    if key is not None and key.key_blocks.get(params['name']) is not None:
        raise RefusalError('shape_key_add NAME_TAKEN: the mesh already has this shape key', code='VALIDATION')
    if len(mesh.vertices) * ((len(key.key_blocks) if key else 0) + 2) > MAX_INDEX_SELECTION * 1024:
        raise RefusalError('Shape keys on this mesh exceed the bounded native capability', code='LIMIT_EXCEEDED')
    if key is None:
        obj.shape_key_add(name='Basis', from_mix=False)
        context.track_id(mesh.shape_keys)
    block = obj.shape_key_add(name=params['name'], from_mix=False)
    if block.name != params['name']:
        raise RefusalError('shape_key_add NAME_TAKEN: the shape key name was suffixed', code='VALIDATION')
    block.slider_min, block.slider_max = params['slider_min'], params['slider_max']
    if not block.slider_min <= params['value'] <= block.slider_max:
        raise RefusalError('The shape key value lies outside its slider range', code='VALIDATION')
    block.value = params['value']
    if abs(block.value - params['value']) > 1e-6:
        raise RefusalError('The shape key value lies outside its slider range', code='INTERNAL_ERROR')
    return _readback(obj, operation, shape_keys=[item.name for item in mesh.shape_keys.key_blocks])


def set_shape_key(context, obj, operation):
    params = operation['parameters']
    mesh, key = _shape_keys(context, obj)
    block = key.key_blocks.get(params['name']) if key is not None else None
    if block is None or block == key.reference_key:
        raise RefusalError('SHAPE_KEY_NOT_FOUND: name an existing non-basis shape key', code='VALIDATION')
    animation = key.animation_data
    if animation is not None and len(animation.drivers):
        raise RefusalError('Driven shape keys need reviewed Python', code='UNSUPPORTED')
    if not block.slider_min <= params['value'] <= block.slider_max:
        raise RefusalError('The shape key value lies outside its slider range', code='VALIDATION')
    block.value = params['value']
    if abs(block.value - params['value']) > 1e-6:
        raise RefusalError('The shape key value lies outside its slider range', code='INTERNAL_ERROR')
    if params['frame'] is not None:
        if animation is not None and animation.action is not None:
            context.require_private(animation.action)
        # Same pairing as keyframe_insert: the stage scene and view layer, no window.
        with bpy.context.temp_override(scene=context.scene, view_layer=context.view_layer):
            if not block.keyframe_insert('value', frame=params['frame']):
                raise RefusalError('Native shape-key keyframe insertion failed', code='INTERNAL_ERROR')
        action = key.animation_data.action
        if action not in context.owned_ids:
            context.track_id(action)
            live = context.extras.get('live_names', {}).get(operation['target'])
            if live is not None:
                action.name = live + 'ShapeKeys'  # Not the private staged copy's name.
    return _readback(obj, operation, shape_key=block.name, value=block.value, frame=params['frame'])


def push_down(context, obj, operation):
    animation = obj.animation_data
    if animation is None or animation.action is None:
        raise RefusalError('NO_ACTION: the object has no active Action to push down', code='VALIDATION')
    if len(animation.drivers):
        raise RefusalError('Animation with drivers needs reviewed Python', code='UNSUPPORTED')
    action = context.require_private(animation.action)
    slot = getattr(animation, 'action_slot', None)
    # Named after the Action as commit names it ("CubeAction"), never its private staged copy ("CubeAction.001").
    from .anim_stage import find_action
    uid = context.extras.get('action_sources', {}).get(action.name)
    live = find_action(uid) if uid is not None else None
    # A shared Action stays with its other users: the copy commits under a name of its own, reserved now.
    from .stage_names import reserve
    name = live.name if live is not None and live.users <= 1 else reserve(
        context.extras, action, lambda wanted: bpy.data.actions.get((wanted, None)) is not None)
    track = animation.nla_tracks.new()
    track.name = operation['parameters']['track'] or name
    strip = track.strips.new(name, int(action.frame_range[0]), action)
    if slot is not None and hasattr(strip, 'action_slot'):
        strip.action_slot = slot
    animation.action = None
    return _readback(obj, operation, track=track.name, strip=strip.name,
                     frame_range=[strip.frame_start, strip.frame_end], tracks=len(animation.nla_tracks))


def key_properties(context, operation):
    """``property_keyframe``: property_set's checked writes, then a key per path at one frame.

    Object paths key the object's Action, data paths the data's (a private
    staged copy that commit swaps in); a new Action is named after the live
    object, never the private staged copy.
    """
    from .action_property import apply_property_set
    from .action_properties import holder_and_name, PropertyPathError
    params = operation['parameters']
    result = apply_property_set(context, operation)  # Resolves and type-checks the owner, writes and reads back.
    obj = context.resolve(operation['target'])
    keyed = []
    for path in params['values']:
        holder, name = holder_and_name(obj, path)
        if not getattr(holder.bl_rna.properties[name], 'is_animatable', False) or \
                holder.bl_rna.properties[name].type not in {'BOOLEAN', 'INT', 'FLOAT'}:
            raise PropertyPathError('NOT_ANIMATABLE', path, 'RNA marks this setting as not animatable')
        owner = holder.id_data
        animation = owner.animation_data
        if animation is not None and len(animation.drivers):
            raise RefusalError('Driven settings need reviewed Python', code='UNSUPPORTED')
        if animation is not None and animation.action is not None:
            context.require_private(animation.action)
        if owner == obj:
            name_new_slot(context.extras, obj, operation['target'])  # A new slot takes the live name (program_names).
        with bpy.context.temp_override(scene=context.scene, view_layer=context.view_layer):
            if not holder.keyframe_insert(name, frame=params['frame']):
                raise RefusalError('Native keyframe insertion failed for ' + path, code='INTERNAL_ERROR')
        action = owner.animation_data.action
        if action not in context.owned_ids:
            context.track_id(action)
            live = context.extras.get('live_names', {}).get(operation['target'])
            if live is not None:
                action.name = live + ('Action' if owner == obj else 'DataAction')
        data_path = holder.path_from_id(name)
        if params['interpolation'] is not None:
            for curve in curves(channelbag(owner), data_path, -1 if isinstance(params['values'][path], list) else 0):
                point = key_at(curve, params['frame'])
                if point is not None:
                    point.interpolation = params['interpolation']
        keyed.append(path)
    return dict(result, kind=operation['kind'], frame=params['frame'], keyed=keyed)
