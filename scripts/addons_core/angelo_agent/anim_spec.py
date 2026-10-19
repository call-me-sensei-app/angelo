# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Key-editing kinds on an object's own Action slot: schemas and cross-field rules. No bpy.

Every kind here edits the F-Curves of the target object's assigned Action slot
(its channelbag, Blender 5 layered Actions; never ``Action.fcurves``), on the
program's private staged copy of that Action (``anim_channels``):

* ``keyframe_configure``: interpolation, easing, handle type, key type of the
  selected keys, and each selected channel's extrapolation and mute;
* ``keyframe_edit``: one structural operation on the selected keys
  (``DELETE``, ``OFFSET``, ``SCALE``, ``INSERT``, ``CLEAN``, ``DECIMATE``,
  ``SMOOTH``, ``BAKE``, ``EULER_FILTER``, ``SNAP``);
* ``fcurve_modifier``: add, configure or remove an F-Curve modifier of one
  channel (``Cycles``, ``Noise``, ``Limits``...), settings by RNA path;
* ``keyframe_copy``: keys of a declared source object's channels pasted onto
  the target's, offset in time (the copy/paste of the Graph Editor, as an
  explicit operation between two objects, or within one).

Channels: ``property`` is an F-Curve data path of the object's slot (``location``,
``pose.bones["Arm"].rotation_quaternion``, ``["prop"]``); ``data.<path>`` names a
channel of the object data's own slot (camera, light, curve settings); null
means every channel of the object's slot. ``index`` is the array index, -1 for
every index. Keys: ``frames`` (exact frames) or ``frame_range`` (inclusive
bounds); neither selects every key of the channels.

Families: ``animation`` (commit assigns the edited staged Action copy, as for
``keyframe_insert``), plus ``data`` for ``data.`` channels (the data, whose
Action copy is edited, is swapped whole).
"""

import copy

from .refusal_codes import RefusalError
from .action_schema_types import POINTER, text, number, integer, enum, boolean, array, nullable, obj
from .rna_rules import PATH_PATTERN, screen_path, json_value
from .action_properties import SCALAR_VALUE

# A data path through identifiers, ["quoted keys"] and [integer] subscripts; no evaluation.
CHANNEL_PATTERN = (r'^(?:[A-Za-z_][A-Za-z0-9_]*|\["[^"\\]{1,64}"\])'
                   r'(?:\[(?:[0-9]{1,6}|"[^"\\]{1,64}")\]|\.[A-Za-z_][A-Za-z0-9_]*){0,12}$')
PATH = text(256, pattern=CHANNEL_PATTERN)
FRAME = integer(-100000, 1000000)
TIME = number(-100000, 1000000)
FRAMES = dict(array(FRAME, 0, 64), uniqueItems=True, default=[])
RANGE = nullable(array(TIME, 2, 2))
INTERPOLATIONS = ('CONSTANT', 'LINEAR', 'BEZIER', 'SINE', 'QUAD', 'CUBIC', 'QUART', 'QUINT', 'EXPO', 'CIRC', 'BACK',
                  'BOUNCE', 'ELASTIC')
EASINGS = ('AUTO', 'EASE_IN', 'EASE_OUT', 'EASE_IN_OUT')
HANDLE_TYPES = ('FREE', 'ALIGNED', 'VECTOR', 'AUTO', 'AUTO_CLAMPED')
KEY_TYPES = ('KEYFRAME', 'BREAKDOWN', 'MOVING_HOLD', 'EXTREME', 'JITTER', 'GENERATED')
EXTRAPOLATIONS = ('CONSTANT', 'LINEAR')
# Settings-only modifier types: Envelope control points are a collection no settings path creates.
FMODIFIER_TYPES = ('GENERATOR', 'FNGENERATOR', 'CYCLES', 'NOISE', 'LIMITS', 'STEPPED', 'SMOOTH')
# A Generator's polynomial order bounds its coefficient array.
MAX_POLY_ORDER = 16
MAX_SETTINGS = 32

CHANNEL = {'property': nullable(PATH), 'index': integer(-1, 63, -1)}
CONFIGURED = ('interpolation', 'easing', 'handle_type', 'key_type', 'extrapolation', 'mute')
KEYFRAME_CONFIGURE_SCHEMA = obj(dict(
    CHANNEL, frames=FRAMES, frame_range=RANGE,
    interpolation=nullable(enum(INTERPOLATIONS)), easing=nullable(enum(EASINGS)),
    handle_type=nullable(enum(HANDLE_TYPES)), key_type=nullable(enum(KEY_TYPES)),
    extrapolation=nullable(enum(EXTRAPOLATIONS)), mute=nullable({'type': 'boolean'})))


# The fields each keyframe_edit operation takes besides the channel and frame_range; a field another
# operation owns is refused (anim_spec.canonical), so the flat schema stays closed per operation.
EDIT_FIELDS = {
    'DELETE': ('frames',),
    # Keys move in time and value; moved keys replace unmoved keys they land on, as a transform does.
    'OFFSET': ('offset', 'value_offset'),
    # Time about pivot (a negative factor mirrors) and value about value_pivot.
    'SCALE': ('factor', 'pivot', 'value_factor', 'value_pivot'),
    # At each frame on existing channels: the curve's own value, or an explicit one.
    'INSERT': ('frames', 'value', 'interpolation', 'key_type'),
    # Blender's Clean Keyframes; channels also removes a single key left at its default.
    'CLEAN': ('threshold', 'channels'),
    # Remove the keys that least change the curve: a ratio of the interior keys, or while within an error.
    'DECIMATE': ('ratio', 'error'),
    # Blender's Smooth Keys (5-point weighted means), passes times.
    'SMOOTH': ('passes',),
    # A key every step frames on the curve, Blender's Bake Channels; bake_modifiers bakes the channel's
    # F-Curve modifiers in and removes them (otherwise they are muted meanwhile).
    'BAKE': ('step', 'remove', 'bake_modifiers', 'interpolation'),
    # Rotation curves made continuous (whole channels, no frame_range).
    'EULER_FILTER': (),
    'SNAP': ('mode',),
}
EDIT_OPERATIONS = tuple(EDIT_FIELDS)
KEYFRAME_EDIT_SCHEMA = obj(dict(
    CHANNEL, operation=enum(EDIT_OPERATIONS), frames=FRAMES, frame_range=RANGE,
    offset=number(-100000, 100000, 0), value_offset=number(-1000000, 1000000, 0),
    factor=number(-1000, 1000, 1), pivot=number(-100000, 1000000, 0), value_factor=number(-1000, 1000, 1),
    value_pivot=number(-1000000, 1000000, 0), value=nullable(number(-1000000, 1000000)),
    interpolation=nullable(enum(INTERPOLATIONS)), key_type=nullable(enum(KEY_TYPES)),
    threshold=number(0, 1000, .001), channels=boolean(False), ratio=nullable(number(0, 1)),
    error=nullable(number(0, 1000000)), passes=integer(1, 16, 1), step=number(.1, 1000, 1),
    remove=enum(['IN_RANGE', 'OUT_RANGE', 'ALL', 'NONE'], 'IN_RANGE'), bake_modifiers=boolean(True),
    mode=enum(['NEAREST_FRAME', 'FLATTEN_HANDLES'], 'NEAREST_FRAME')), ['operation'])

FMODIFIER_SETTINGS = {'type': 'object', 'properties': {}, 'additionalProperties': SCALAR_VALUE,  # No object setting.
                      'propertyNames': {'pattern': PATH_PATTERN}, 'maxProperties': MAX_SETTINGS, 'default': {}}
FCURVE_MODIFIER_SCHEMA = obj({
    'property': PATH, 'index': integer(-1, 63, -1), 'mode': enum(['ADD', 'CONFIGURE', 'REMOVE'], 'ADD'),
    'type': nullable(enum(FMODIFIER_TYPES)), 'name': nullable(text(64)), 'settings': FMODIFIER_SETTINGS},
    ['property'])

KEYFRAME_COPY_SCHEMA = obj(dict(
    CHANNEL, source=POINTER, frame_range=RANGE, offset=number(-100000, 100000, 0),
    mode=enum(['MERGE', 'REPLACE_RANGE', 'REPLACE'], 'MERGE')), ['source'])

KEY_SCHEMAS = {'keyframe_configure': KEYFRAME_CONFIGURE_SCHEMA, 'keyframe_edit': KEYFRAME_EDIT_SCHEMA,
               'fcurve_modifier': FCURVE_MODIFIER_SCHEMA, 'keyframe_copy': KEYFRAME_COPY_SCHEMA}
KEY_KINDS = frozenset(KEY_SCHEMAS)


def given_fields(values, schema):
    """Fields of a flat schema set to something other than their default (canonical operations carry every field)."""
    properties = schema['properties']

    def unset(value, default):  # A settings object canonicalises to every field null.
        return value == default or (default == {} and isinstance(value, dict) and all(v is None for v in value.values()))
    return [key for key, value in values.items() if key in properties and not unset(value, properties[key].get('default'))]


def is_data_path(path):
    return isinstance(path, str) and path.startswith('data.')


def canonical(result, raw):
    """Cross-field rules of the key kinds on a schema-validated operation (raises ValueError)."""
    kind, params = result['kind'], result['parameters']
    if params.get('property') is None and params.get('index', -1) != -1:
        raise RefusalError('An array index needs a property; every channel takes index -1', code="VALIDATION")
    selection = params.get('frames'), params.get('frame_range')
    if selection[1] is not None and selection[1][0] > selection[1][1]:
        raise RefusalError('frame_range runs from its first frame to its last', code="VALIDATION")
    if kind == 'keyframe_configure':
        if all(params[key] is None for key in CONFIGURED):
            raise RefusalError('Configure at least one of interpolation, easing, handle_type, key_type, '
                               'extrapolation or mute', code="VALIDATION")
        if selection[0] and selection[1] is not None:
            raise RefusalError('Select keys by frames or by frame_range, not both', code="VALIDATION")
        if (selection[0] or selection[1] is not None) and all(params[key] is None for key in CONFIGURED[:4]):
            raise RefusalError('extrapolation and mute apply to whole channels; frames select keys', code="VALIDATION")
    elif kind == 'keyframe_edit':
        _edit_rules(params, raw)
    elif kind == 'fcurve_modifier':
        _modifier_rules(params, raw)
    elif kind == 'keyframe_copy':
        if result['target'] == params['source'] and params['offset'] == 0:
            raise RefusalError('Copying keys onto their own channels needs a nonzero offset', code="VALIDATION")
        if is_data_path(params['property']):
            raise RefusalError('keyframe_copy copies object channels; data channels need reviewed Python', code="UNSUPPORTED")


def _edit_rules(params, raw):
    operation = params['operation']
    given = set(given_fields(raw['parameters'], KEYFRAME_EDIT_SCHEMA)) - {'operation', 'property', 'index', 'frame_range'}
    foreign = sorted(given - set(EDIT_FIELDS[operation]))
    if foreign:
        raise RefusalError('%s takes no %s' % (operation, ', '.join(foreign)), code="VALIDATION")
    if operation == 'INSERT' and not params['frames']:
        raise RefusalError('INSERT needs the frames to key', code="VALIDATION")
    if operation == 'BAKE' and params['frame_range'] is None:
        raise RefusalError('BAKE needs a frame_range', code="VALIDATION")
    if operation == 'DELETE' and params['frames'] and params['frame_range'] is not None:
        raise RefusalError('Delete keys at frames or in a frame_range, not both', code="VALIDATION")
    if operation == 'OFFSET' and params['offset'] == 0 and params['value_offset'] == 0:
        raise RefusalError('An offset moves keys in time or value', code="VALIDATION")
    if operation == 'SCALE' and (abs(params['factor']) < 1e-6 or abs(params['value_factor']) < 1e-6):
        raise RefusalError('Scale factors must be nonzero (a negative factor mirrors)', code="VALIDATION")
    if operation == 'SCALE' and params['factor'] == 1 and params['value_factor'] == 1:
        raise RefusalError('Scale by a factor other than 1 in time or value', code="VALIDATION")
    if operation == 'DECIMATE' and (params['ratio'] is None) == (params['error'] is None):
        raise RefusalError('Decimate by a ratio of keys to remove or by an error, one of them', code="VALIDATION")
    if operation == 'BAKE' and params['frame_range'][0] == params['frame_range'][1]:
        raise RefusalError('Bake over a frame range longer than one frame', code="VALIDATION")
    if operation == 'EULER_FILTER' and params['frame_range'] is not None:
        raise RefusalError('The Euler filter runs over whole channels; it takes no frame_range', code="VALIDATION")
    if operation == 'EULER_FILTER' and params['property'] is not None and \
            not params['property'].rsplit('.', 1)[-1] == 'rotation_euler':
        raise RefusalError('The Euler filter applies to rotation_euler channels', code="VALIDATION")


def _modifier_rules(params, raw):
    if params['mode'] == 'ADD' and params['type'] is None:
        raise RefusalError('Name the F-Curve modifier type to add', code="VALIDATION")
    if params['mode'] != 'ADD' and params['name'] is None:
        raise RefusalError('Name the existing F-Curve modifier to configure or remove', code="VALIDATION")
    if params['mode'] == 'REMOVE' and params['settings']:
        raise RefusalError('Removing a modifier takes no settings', code="VALIDATION")
    if params['mode'] == 'CONFIGURE' and not params['settings']:
        raise RefusalError('Configure at least one setting', code="VALIDATION")
    settings = copy.deepcopy(raw['parameters'].get('settings', {}))  # Integers stay integers.
    for path, value in settings.items():
        screen_path(path, struct=True)
        json_value(value, path)
        if path in {'type', 'name', 'active', 'show_expanded', 'is_valid'}:
            raise RefusalError('FMODIFIER_SETTING_DENIED: %s is set by the operation itself or is editor state' % path,
                               code="UNSUPPORTED")
        if path == 'poly_order' and not (isinstance(value, int) and 1 <= value <= MAX_POLY_ORDER):
            raise RefusalError('A Generator polynomial order is 1-%d' % MAX_POLY_ORDER, code="LIMIT_EXCEEDED")
        if path == 'coefficients' and not (isinstance(value, list) and len(value) <= MAX_POLY_ORDER + 1):
            raise RefusalError('A Generator has at most %d coefficients' % (MAX_POLY_ORDER + 1), code="LIMIT_EXCEEDED")
    params['settings'] = settings


def references(operation):
    """Objects a key kind reads besides its target: keyframe_copy's source."""
    return [operation['parameters']['source']] if operation['kind'] == 'keyframe_copy' else []


def families(operation):
    """The families commit transfers: the Action, plus the swapped data for a data channel."""
    data = is_data_path(operation['parameters'].get('property'))
    return ['object', 'animation'] + ['data'] * data


def derived_checks(target, operation):
    """(check, keeps-kinds or None for every kind) pairs a key kind states exactly; none for ranges."""
    kind, params = operation['kind'], operation['parameters']
    if params.get('property') is None:
        return []
    base = {'kind': 'keyframes', 'target': target, 'property': params['property'], 'index': params['index']}
    if kind == 'keyframe_configure' and params['frames']:
        settings = {key: params[key] for key in ('interpolation', 'easing', 'handle_type', 'key_type')
                    if params[key] is not None}
        if params['extrapolation'] is not None:
            settings['extrapolation'] = params['extrapolation']
        return [dict(base, frames=list(params['frames']), **settings)] if settings else []
    if kind == 'keyframe_edit' and params['operation'] == 'INSERT':
        return [dict(base, frames=list(params['frames']))]
    if kind == 'keyframe_edit' and params['operation'] == 'DELETE' and params['frames']:
        return [dict(base, absent=list(params['frames']))]
    if kind == 'fcurve_modifier' and params['name'] is not None:
        check = {'kind': 'fcurve_modifier', 'target': target, 'property': params['property'],
                 'index': max(params['index'], 0), 'name': params['name']}
        if params['mode'] == 'REMOVE':
            return [dict(check, exists=False)]
        return [dict(check, values=copy.deepcopy(params['settings']))] if params['settings'] else [check]
    return []


def keeps(check, operation):
    """Whether a later keyframe_configure leaves an earlier keyframes check true: only on another channel."""
    path = operation['parameters']['property']
    return path is not None and path != check['property']
