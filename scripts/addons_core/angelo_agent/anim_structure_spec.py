# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Action, NLA and timeline-marker kinds: schemas, cross-field rules and families. No bpy.

* ``action_set`` manages which Action (and slot) the target object uses:
  ``NEW`` (a new empty Action), ``ASSIGN`` (an existing Action, by the
  ``dat:<epoch>:ACTION:<uid>`` ID an object read shows), ``SINGLE_USER`` (the
  object gets its own copy of a shared Action: explicit isolation), ``UNLINK``
  (no active Action), ``RENAME`` (the Action the object uses, renamed at commit
  for every user) and ``SLOT`` (assign an existing slot, create one, or none).
  An Action left without any user would be lost on save: ``NEW``, ``ASSIGN``
  and ``UNLINK`` refuse that unless ``stash_previous`` keeps it in a muted NLA
  track, as Blender's Stash does.
* ``nla_edit`` edits the target's NLA tracks and their action strips by exact
  (unique) name: add, remove and set tracks (name, mute, solo, lock, position),
  add strips of existing Actions, remove them, and set their timing, blending,
  extrapolation, repeat, scale, influence and mute, or move one to another track.
* ``marker_edit`` edits the bound scene's timeline markers (target: the scene
  handle), by exact unique name: add, remove, rename and move them, and bind a
  declared camera (or none) to one.

Families: ``animation`` (the object's Action and slot), ``nla_stack`` (commit
rebuilds the live NLA stack from the staged one, with a snapshot to roll back)
and, for the scene, ``markers`` (the marker list, rebuilt the same way).
"""

from .refusal_codes import RefusalError
from .action_schema_types import NULLABLE_POINTER, text, number, integer, enum, boolean, nullable, obj

ACTION_ID_PATTERN = r'^dat:[a-f0-9]{32}:ACTION:[1-9][0-9]{0,19}$'
ACTION_ID = text(80, pattern=ACTION_ID_PATTERN)
NAME = text(64)
SLOT = text(66)  # A slot identifier ("OBCube") or its display name.
FRAME = number(-100000, 1000000)
MARKER_FRAME = integer(-1048574, 1048574)
STASH = boolean(False)


# Fields each mode takes (required first, then optional); other fields are refused (canonical).
ACTION_FIELDS = {'NEW': ((), ('name', 'stash_previous')), 'ASSIGN': (('action',), ('slot', 'stash_previous')),
                 'SINGLE_USER': ((), ()), 'UNLINK': ((), ('stash_previous',)), 'RENAME': (('name',), ()),
                 # slot: an existing slot of the Action; new_slot: a new one named so; neither: no slot.
                 'SLOT': ((), ('slot', 'new_slot'))}
ACTION_SET_SCHEMA = obj({'mode': enum(tuple(ACTION_FIELDS)), 'name': nullable(NAME), 'action': nullable(ACTION_ID),
                         'slot': nullable(SLOT), 'new_slot': nullable(NAME), 'stash_previous': STASH}, ['mode'])

BLEND_TYPES = ('REPLACE', 'COMBINE', 'ADD', 'SUBTRACT', 'MULTIPLY')
STRIP_EXTRAPOLATIONS = ('NOTHING', 'HOLD', 'HOLD_FORWARD')
# Typed strip settings, written in this order (timing first); frame_start moves the strip keeping its length.
STRIP_SETTINGS = obj({
    'action_frame_start': nullable(FRAME), 'action_frame_end': nullable(FRAME),
    'scale': nullable(number(.001, 1000)), 'repeat': nullable(number(.01, 1000)),
    'frame_start': nullable(FRAME), 'frame_end': nullable(FRAME),
    'blend_type': nullable(enum(BLEND_TYPES)), 'extrapolation': nullable(enum(STRIP_EXTRAPOLATIONS)),
    'blend_in': nullable(number(0, 100000)), 'blend_out': nullable(number(0, 100000)),
    'use_auto_blend': nullable({'type': 'boolean'}), 'influence': nullable(number(0, 1)),
    'mute': nullable({'type': 'boolean'}), 'use_reverse': nullable({'type': 'boolean'}),
    'use_sync_length': nullable({'type': 'boolean'})}, default={})
STRIP_FIELDS = tuple(STRIP_SETTINGS['properties'])


# Fields each operation takes (required, optional). TRACK_SET index: the track's new position from the
# bottom (0) of the stack. STRIP_ADD plays an existing Action (by ID) from frame.
NLA_FIELDS = {'TRACK_ADD': (('track',), ('position',)), 'TRACK_REMOVE': (('track',), ()),
              'TRACK_SET': (('track',), ('name', 'mute', 'solo', 'lock', 'index')),
              'STRIP_ADD': (('track', 'action', 'frame'), ('name', 'slot', 'settings')),
              'STRIP_REMOVE': (('track', 'strip'), ()),
              'STRIP_SET': (('track', 'strip'), ('name', 'move_to_track', 'settings'))}
NLA_OPERATIONS = tuple(NLA_FIELDS)
NLA_EDIT_SCHEMA = obj({
    'operation': enum(NLA_OPERATIONS), 'track': nullable(NAME), 'strip': nullable(NAME), 'name': nullable(NAME),
    'position': enum(['TOP', 'BOTTOM'], 'TOP'), 'mute': nullable({'type': 'boolean'}), 'solo': nullable({'type': 'boolean'}),
    'lock': nullable({'type': 'boolean'}), 'index': nullable(integer(0, 63)), 'action': nullable(ACTION_ID),
    'frame': nullable(FRAME), 'slot': nullable(SLOT), 'move_to_track': nullable(NAME), 'settings': STRIP_SETTINGS},
    ['operation'])

# Fields each operation takes; BIND_CAMERA binds a declared camera object (Bind Camera to Markers), null unbinds.
MARKER_FIELDS = {'ADD': (('name', 'frame'), ('camera',)), 'REMOVE': (('marker',), ()),
                 'SET': (('marker',), ('name', 'frame')), 'BIND_CAMERA': (('marker', 'camera'), ())}
MARKER_EDIT_SCHEMA = obj({'operation': enum(tuple(MARKER_FIELDS)), 'marker': nullable(NAME), 'name': nullable(NAME),
                          'frame': nullable(MARKER_FRAME), 'camera': NULLABLE_POINTER}, ['operation'])

STRUCTURE_SCHEMAS = {'action_set': ACTION_SET_SCHEMA, 'nla_edit': NLA_EDIT_SCHEMA, 'marker_edit': MARKER_EDIT_SCHEMA}
STRUCTURE_KINDS = frozenset(STRUCTURE_SCHEMAS)


_TABLES = {'action_set': ('mode', ACTION_FIELDS), 'nla_edit': ('operation', NLA_FIELDS),
           'marker_edit': ('operation', MARKER_FIELDS)}


def _fields(kind, params, raw):
    """Refuse a missing required field or a field another mode or operation owns."""
    key, table = _TABLES[kind]
    required, optional = table[params[key]]
    from .anim_spec import given_fields
    schema = {'action_set': ACTION_SET_SCHEMA, 'nla_edit': NLA_EDIT_SCHEMA, 'marker_edit': MARKER_EDIT_SCHEMA}[kind]
    given = set(given_fields(raw['parameters'], schema)) - {key}
    foreign = sorted(given - set(required) - set(optional))
    if foreign:
        raise RefusalError('%s %s takes no %s' % (kind, params[key], ', '.join(foreign)), code="VALIDATION")
    # Required fields are non-null; only BIND_CAMERA's camera may be null (unbinding it).
    missing = [name for name in required if name not in raw['parameters']
               or (params[name] is None and (kind, params[key], name) != ('marker_edit', 'BIND_CAMERA', 'camera'))]
    if missing:
        raise RefusalError('%s %s needs %s' % (kind, params[key], ', '.join(missing)), code="VALIDATION")


def canonical(result, raw):
    """Cross-field rules of the structure kinds on a schema-validated operation (raises ValueError)."""
    kind, params = result['kind'], result['parameters']
    _fields(kind, params, raw)
    if kind == 'action_set':
        if params['mode'] == 'SLOT' and params['slot'] is not None and params['new_slot'] is not None:
            raise RefusalError('Assign an existing slot or create a new one, not both', code="VALIDATION")
    elif kind == 'nla_edit':
        operation = params['operation']
        if operation == 'TRACK_SET' and all(params[key] is None for key in ('name', 'mute', 'solo', 'lock', 'index')):
            raise RefusalError('Set at least one of name, mute, solo, lock or index', code="VALIDATION")
        if operation == 'STRIP_SET' and params['name'] is None and params['move_to_track'] is None and \
                not any(value is not None for value in params['settings'].values()):
            raise RefusalError('Set at least one strip setting, a name or a track to move to', code="VALIDATION")
        settings = params.get('settings') or {}
        if (settings.get('frame_end') is not None and settings.get('frame_start') is not None
                and settings['frame_end'] <= settings['frame_start']):
            raise RefusalError('A strip ends after it starts', code="VALIDATION")
        if (settings.get('action_frame_end') is not None and settings.get('action_frame_start') is not None
                and settings['action_frame_end'] <= settings['action_frame_start']):
            raise RefusalError('A strip\'s action range ends after it starts', code="VALIDATION")
    elif kind == 'marker_edit':
        if params['operation'] == 'SET' and params['name'] is None and params['frame'] is None:
            raise RefusalError('Set the marker\'s name, frame or both', code="VALIDATION")
        if params.get('camera') is not None and params['camera'] == result['target']:
            raise RefusalError('A marker binds a camera object, not the scene', code="VALIDATION")


def references(operation):
    """Objects a structure kind reads besides its target: a marker's camera."""
    camera = operation['parameters'].get('camera') if operation['kind'] == 'marker_edit' else None
    return [camera] if camera is not None else []


def families(operation):
    """Families commit transfers for one canonical structure operation."""
    kind, params = operation['kind'], operation['parameters']
    if kind == 'marker_edit':
        return ['scene', 'markers']
    if kind == 'nla_edit':
        return ['object', 'nla_stack']
    if params['mode'] == 'RENAME':
        return ['object']  # An intent on the live Action at commit; nothing staged transfers.
    stash = params.get('stash_previous', False)
    return ['object', 'animation'] + ['nla_stack'] * stash


def derived_checks(target, operation):
    """Checks a structure operation states exactly: track names, a strip's settings, markers, the Action name."""
    kind, params = operation['kind'], operation['parameters']
    if kind == 'marker_edit':
        operation_name = params['operation']
        if operation_name == 'ADD':
            return [{'kind': 'markers', 'target': target, 'present': [{'name': params['name'], 'frame': params['frame']}]}]
        if operation_name == 'REMOVE':
            return [{'kind': 'markers', 'target': target, 'absent': [params['marker']]}]
        if operation_name == 'SET':
            name = params['name'] or params['marker']
            row = {'name': name, **({'frame': params['frame']} if params['frame'] is not None else {})}
            absent = [params['marker']] if params['name'] not in (None, params['marker']) else []
            return [{'kind': 'markers', 'target': target, 'present': [row], 'absent': absent}]
        return [{'kind': 'markers', 'target': target, 'present': [{'name': params['marker'], 'camera': params['camera']}]}]
    if kind == 'nla_edit':
        operation_name = params['operation']
        if operation_name in {'TRACK_ADD', 'TRACK_SET'}:
            name = params.get('name') or params['track']
            return [{'kind': 'nla', 'target': target, 'track_names': [name]}]
        if operation_name == 'TRACK_REMOVE':
            return [{'kind': 'nla', 'target': target, 'absent_tracks': [params['track']]}]
        if operation_name in {'STRIP_ADD', 'STRIP_SET'}:
            values = {key: value for key, value in params['settings'].items() if value is not None}
            track = params.get('move_to_track') or params['track']
            strip = params.get('name') or params.get('strip')
            if strip is None:
                return []
            return [{'kind': 'nla', 'target': target, 'strip': {'track': track, 'name': strip, 'values': values}}]
        return [{'kind': 'nla', 'target': target, 'absent_strips': [params['strip']]}]
    if params['mode'] in {'NEW', 'RENAME'} and params.get('name') is not None:
        return [{'kind': 'nla', 'target': target, 'action': params['name']}]
    if params['mode'] == 'UNLINK':
        return [{'kind': 'nla', 'target': target, 'active_action': False}]
    return []


def merge_markers(previous, new):
    """The marker state after a later marker_edit: later rows win by name; absent names exclude present ones."""
    present = {row['name']: row for row in previous.get('present', [])}
    for name in new.get('absent', []):
        present.pop(name, None)
    for row in new.get('present', []):
        present[row['name']] = dict(present.get(row['name'], {}), **row)
    absent = sorted((set(previous.get('absent', [])) | set(new.get('absent', []))) - set(present))
    return {'kind': 'markers', 'target': new['target'], 'present': list(present.values()), 'absent': absent}
