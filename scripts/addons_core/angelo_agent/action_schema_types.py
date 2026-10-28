# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Finite native-action limits, JSON schema builders and strict value validation.

No Blender import; unknown fields and nonfinite numbers fail before approval."""

import copy
import math
import re
import struct


MAX_OPERATIONS = 32
MAX_VERTICES = 100000
MAX_EDGES = 200000
MAX_FACES = 200000
MAX_EVALUATED_VERTICES = 500000
# What a Subdivision stack may reach at render time: 4^3 times the staged budget, the most the earlier
# cap (render level 3 over viewport level 0 on a mesh at the staged budget) could leave behind.
MAX_RENDER_VERTICES = 64 * MAX_EVALUATED_VERTICES
MAX_SUBDIVISION_LEVELS, MAX_SUBDIVISION_RENDER_LEVELS = 3, 6
# A Subdivision modifier's typed ``adaptive`` members and the settings they write (Cycles dices by them at render).
ADAPTIVE_SETTINGS = {'enabled': 'use_adaptive_subdivision', 'space': 'adaptive_space', 'pixel_size': 'adaptive_pixel_size',
                     'object_edge_length': 'adaptive_object_edge_length'}
MAX_EVALUATED_BONES = 1024  # A staged armature, before and after every operation.
MAX_INDEX_SELECTION = 4096
MAX_NODES = 32
MAX_LINKS = 64


# Typed node keys (``MATH``) -> the Blender node they stand for (``ShaderNodeMath``), filled by action_node_types.
# Every registered node also goes by its bl_idname with RNA-checked settings (node_generic), so a typed key's
# closed enum is never the limit of what can be set: GPT trials read "MATH ... unsupported value" as no
# GREATER_THAN at all, though ShaderNodeMath took it at once.
TYPED_NODE_ALTERNATIVES = {}
MAX_ALLOWED_LISTED = 16


def allowed(values):
    """``allowed: A, B, C`` for an enum's values, at most ``MAX_ALLOWED_LISTED`` of them."""
    listed = ", ".join(str(value) for value in values[:MAX_ALLOWED_LISTED])
    more = len(values) - MAX_ALLOWED_LISTED
    return "allowed: " + listed + (" and %d more" % more if more > 0 else "")


def typed_node_alternative(value, error):
    """The route past a typed node key's narrow setting or input, or ``""``."""
    kind = value.get('type') if type(value) is dict and 'id' in value else None
    bl_idname = TYPED_NODE_ALTERNATIVES.get(kind) if isinstance(kind, str) else None
    if bl_idname is None or not ('.settings.' in error or '.inputs.' in error):
        return ""
    return ("; that list is the typed %s key's: type %s takes every setting and input Blender has, checked against "
            "its RNA" % (kind, bl_idname))


# What every note of a mesh over those limits states besides its counts. In recorded tree-scene trials models
# cut the grass mesh down to fit them in 5 of 6 runs, though nothing asked for it;
# the notes named the limits and the script route but not that the mesh may stay as it is.
OVER_LIMIT_NOTE = ("only typed programs have these limits: the mesh can stay as it is, and angelo_execute_python "
                   "edits a mesh of any size")


def mesh_over_limits(mesh, limits):
    """``"124,111 vertices; at most 100,000"``-style text for each count over ``limits``, or None.

    ``limits`` is (MAX_VERTICES, MAX_EDGES, MAX_FACES). Recorded GPT tree trials
    met a bare "exceeds bounded native program limits", then
    cut their own grass mesh to a guessed size; the counts and limits are the fact.
    """
    counts = (len(mesh.vertices), len(mesh.edges), len(mesh.polygons))
    over = ["%s %s; at most %s" % (format(count, ","), name, format(limit, ","))
            for count, limit, name in zip(counts, limits, ("vertices", "edges", "faces")) if count > limit]
    return ", ".join(over) or None


def without_nulls(settings):
    """Typed modifier ``settings`` without null members, at any depth: a null keeps a setting as leaving it out does
    (Mirror's origin, Subdivision's adaptive settings), so both are one canonical program."""
    return {key: without_nulls(value) if type(value) is dict else value for key, value in settings.items()
            if value is not None}


def adaptive_values(settings):
    """The modifier settings a canonical SUBSURF ``settings`` writes for adaptive subdivision: only those stated."""
    return {ADAPTIVE_SETTINGS[key]: value for key, value in (settings.get('adaptive') or {}).items() if value is not None}


def number(low, high, default=None):
    result = {'type': 'number', 'minimum': low, 'maximum': high}
    if default is not None:
        result['default'] = default
    return result


def integer(low, high, default=None):
    result = {'type': 'integer', 'minimum': low, 'maximum': high}
    if default is not None:
        result['default'] = default
    return result


def enum(values, default=None):
    result = {'type': 'string', 'enum': list(values)}
    if default is not None:
        result['default'] = default
    return result


def boolean(default):
    return {'type': 'boolean', 'default': default}


def text(limit=64, default=None, pattern=None):
    result = {'type': 'string', 'minLength': 1, 'maxLength': limit}
    if default is not None:
        result['default'] = default
    if pattern:
        result['pattern'] = pattern
    return result


def array(items, minimum, maximum, default=None):
    result = {'type': 'array', 'items': items, 'minItems': minimum, 'maxItems': maximum}
    if default is not None:
        result['default'] = default
    return result


def vector(default=None, limit=100000):
    return array(number(-limit, limit), 3, 3, default)


def nullable(schema, default=None):
    return {'oneOf': [schema, {'type': 'null'}], 'default': default}


def obj(properties, required=(), default=None, description=None):
    result = {'type': 'object', 'properties': properties, 'required': list(required),
              'additionalProperties': False}
    if default is not None:
        result['default'] = default
    if description is not None:
        result['description'] = description
    return result


def stated(schema, keep=()):
    """A copy of an object ``schema`` whose properties take no default, at any depth, except the ``keep`` names.

    For the settings of something that already exists or that Blender makes with
    its own defaults (a modifier, a constraint, a Principled material's inputs):
    ``_validate`` fills a property's default into the canonical value, so the
    kernel would write it over the value the object has. Without one, a setting
    the call leaves out is absent and stays as it is. The object's own default
    (``{}`` for absent settings) is kept.
    """
    if isinstance(schema, list):
        return [stated(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    result = {key: stated(value) for key, value in schema.items() if key not in ('properties', 'enum', 'const', 'default')}
    for key in ('enum', 'const', 'default'):
        if key in schema:
            result[key] = copy.deepcopy(schema[key])
    if 'properties' in schema:
        result['properties'] = {}
        for name, child in schema['properties'].items():
            child = stated(child)
            if name not in keep:
                child.pop('default', None)
            result['properties'][name] = child
    return result


HANDLE = text(128, pattern=r'^(?:\$[A-Za-z][A-Za-z0-9_]{0,31}|[A-Za-z0-9][A-Za-z0-9_.:\-]{0,127})$')
# How models refer to objects (doc/angelo/agent_execution.rst, "Referring to objects, data and values").
# An open settings map (property_set values, generic modifier, constraint and node settings) types each value
# by RNA at run time, so a plain string there is text and an object is written {"object": handle}; read-backs
# show every pointer that way. A typed pointer field (parent, mirror_object, a constraint target, the scene
# camera...) takes the handle or $alias alone and, like the maps, {"object": handle}: _validate stores the
# handle alone. Models wrote the camera like the maps' pointers and Claude Code's schema check refused it in
# four of five recorded camera one-shots, each time a round and a 26 KB error.
OBJECT_REFERENCE = {'type': 'object', 'properties': {'object': HANDLE}, 'required': ['object'],
                    'additionalProperties': False}
POINTER = {'oneOf': [HANDLE, OBJECT_REFERENCE]}
NULLABLE_POINTER = {'oneOf': [HANDLE, OBJECT_REFERENCE, {'type': 'null'}], 'default': None}
ALIAS = nullable(text(32, pattern=r'^[A-Za-z][A-Za-z0-9_]{0,31}$'))
NODE_ID = text(32, pattern=r'^[A-Za-z][A-Za-z0-9_]{0,31}$')
INDICES = dict(array(integer(0, MAX_EDGES - 1), 1, MAX_INDEX_SELECTION), uniqueItems=True)
COLOR = array(number(0, 1), 4, 4, [0.8, 0.8, 0.8, 1])
UNIT = number(0, 1)
ANGLES = vector([0, 0, 0], 2 * math.pi)
SCALE = vector([1, 1, 1], 1000)


def float32_vector(values):
    """Canonical native coordinates, so approval precedes precision loss."""
    return list(struct.unpack('<3f', struct.pack('<3f', *values)))


def pointer_union(members):
    """Whether union ``members`` are an object pointer: a reference string type and its ``{"object": ...}`` form.

    Structural, so the action validator, the check validator and the schema lint
    recognize the same fields. An open map's value union (``action_properties.VALUE``)
    holds the wrapper but no reference string, and keeps it.
    """
    strings = [member for member in members if isinstance(member, dict) and member.get('type') == 'string']
    return any(isinstance(member, dict) and member.get('type') == 'object' and member.get('required') == ['object']
               and set(member.get('properties') or ()) == {'object'} and member['properties']['object'] in strings
               for member in members)


def bounds(low, high, noun, given):
    """A refusal's bound and what was given ("1 to 8 items; 9 given"): a dev-check worker told only "requires a bounded
    array" spent a whole round finding the limit (2026-10-25: 9 sources where 8 are allowed)."""
    low, high = low or 0, None if high in (None, math.inf) else high
    if high is None:
        span = "at least %g" % low
    else:
        span = "exactly %g" % high if low == high else "%g to %g" % (low, high) if low else "at most %g" % high
    one = (low if high is None else high) == 1
    return "%s %s; %s given" % (span, noun[:-1] if one and noun.endswith("s") else noun, given)


def stored_pointer(value, members):
    """The one stored form of a value that matched a union: a pointer's handle alone, anything else unchanged."""
    if type(value) is dict and len(value) == 1 and 'object' in value and pointer_union(members):
        return value['object']
    return value


def stored_forms(schema):
    """A copy of ``schema`` whose pointers admit only their stored form, the handle (or null).

    For a strict output grammar (``codex_plan.output_schema``): constrained decoding
    can write nothing the grammar lacks, so no form is ever refused there, and the
    ``{"object": ...}`` branch would only add bytes to every Codex plan turn.
    Every schema a validator checks keeps both forms.
    """
    if isinstance(schema, list):
        return [stored_forms(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    result = {}
    for key, value in schema.items():
        if key in ('oneOf', 'anyOf') and isinstance(value, list) and pointer_union(value):
            value = [member for member in value if not (isinstance(member, dict) and member.get('type') == 'object')]
        result[key] = (value if key in ('enum', 'const', 'default') else
                       {name: stored_forms(child) for name, child in value.items()} if key == 'properties'
                       else stored_forms(value))
    for key in ('oneOf', 'anyOf'):
        if isinstance(result.get(key), list) and len(result[key]) == 1:
            result = dict(result.pop(key)[0], **result)  # A pointer that cannot be null is its handle type.
    return result


def tagged_member(value, members):
    """The one union member whose constant and enumerated fields admit ``value``'s, else None.

    A union told apart by such a field (a key's ``property``, a primitive) then
    reports its chosen member's own error (a range) instead of no match at all.
    """
    if type(value) is not dict:
        return None
    fits = []
    for member in members:
        fields = member.get('properties') if isinstance(member, dict) else None
        tags = {name: field for name, field in (fields or {}).items() if name in value and isinstance(field, dict)
                and ('const' in field or 'enum' in field)}
        if tags and all(value[name] == field['const'] if 'const' in field else value[name] in field['enum']
                        for name, field in tags.items()):
            fits.append(member)
    return fits[0] if len(fits) == 1 else None


def _validate(value, schema, path):
    if 'oneOf' in schema:
        successes = []
        for candidate in schema['oneOf']:
            try:
                successes.append(_validate(value, candidate, path))
            except ValueError:
                pass
        if len(successes) != 1:
            if type(value) is dict and set(value) == {'object'} and not any(
                    candidate.get('type') == 'object' for candidate in schema['oneOf']):
                raise ValueError(f'{path} NO_POINTERS: takes no object')  # A map of plain values (SCALAR_VALUE).
            if not successes and (member := tagged_member(value, schema['oneOf'])) is not None:
                try:
                    _validate(value, member, path)  # Raises the error of the member the value's tags chose.
                except ValueError as error:  # A typed node key's narrow setting: Blender's own node takes any.
                    raise ValueError(str(error) + typed_node_alternative(value, str(error))) from None
            raise ValueError(f'{path} does not match one closed schema')
        return stored_pointer(successes[0], schema['oneOf'])
    if 'not' in schema:
        # Only the closed exclusion the generic variants use: a value outside a named set.
        if value in schema['not'].get('enum', ()):
            raise ValueError(f'{path} has a typed schema elsewhere')
        schema = {key: item for key, item in schema.items() if key != 'not'}
    if 'const' in schema:
        if type(value) is not type(schema['const']) or value != schema['const']:
            raise ValueError(f'{path} has an unsupported value')
        return value
    kind = schema['type']
    if kind == 'null':
        if value is not None:
            raise ValueError(f'{path} must be null')
        return None
    if kind == 'object':
        extra = schema.get('additionalProperties')
        if type(value) is not dict or (set(value) - set(schema['properties']) and not isinstance(extra, dict)):
            raise ValueError(f'{path} must contain only documented fields')
        if set(schema.get('required', ())) - set(value):  # Named: a model told only "fields" re-sent four times.
            raise ValueError(f'{path} is missing required fields: {", ".join(sorted(set(schema["required"]) - set(value)))}')
        if not schema.get('minProperties', 0) <= len(value) <= schema.get('maxProperties', len(value)):
            raise ValueError(f"{path} takes {bounds(schema.get('minProperties'), schema.get('maxProperties'), 'fields', len(value))}")
        result = {}
        names = schema.get('propertyNames', {}).get('pattern')
        for key in [key for key in value if key not in schema['properties']]:
            # An open map (settings by RNA path): names by pattern, values by one schema.
            if type(key) is not str or (names and not re.fullmatch(names, key)):
                raise ValueError(f'{path} has an unsupported field name')
            result[key] = _validate(value[key], extra, path + '.' + key)
        for key, spec in schema['properties'].items():
            if key in value:
                result[key] = _validate(value[key], spec, path + '.' + key)
            elif 'default' in spec:
                result[key] = _validate(copy.deepcopy(spec['default']), spec, path + '.' + key)
        return result
    if kind == 'array':
        if type(value) is not list or not schema['minItems'] <= len(value) <= schema['maxItems']:
            given = len(value) if type(value) is list else 'no list'
            raise ValueError(f"{path} requires a list of {bounds(schema['minItems'], schema['maxItems'], 'items', given)}")
        result = [_validate(item, schema['items'], f'{path}[{index}]') for index, item in enumerate(value)]
        if schema.get('uniqueItems') and len(set(result)) != len(result):
            raise ValueError(f'{path} indexes must be unique')
        return result
    if kind == 'string':
        if type(value) is not str or not value.strip():
            raise ValueError(f'{path} requires text that is not blank')
        if not schema.get('minLength', 1) <= len(value) <= schema.get('maxLength', 128):
            raise ValueError(f"{path} requires {bounds(schema.get('minLength', 1), schema.get('maxLength', 128), 'characters', len(value))}")
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError(f'{path} contains control characters')
        if 'enum' in schema and value not in schema['enum']:
            raise ValueError(f'{path} has an unsupported value {value[:64]!r}; {allowed(schema["enum"])}')
        if 'pattern' in schema and not re.fullmatch(schema['pattern'], value):
            raise ValueError(f'{path} is not a document handle or typed identifier')
        return value
    if kind == 'boolean':
        if type(value) is not bool:
            raise ValueError(f'{path} requires a boolean')
        return value
    if kind in {'number', 'integer'}:
        if type(value) not in (int, float) or (kind == 'integer' and type(value) is not int):
            # A JSON integer is written 2: 2.0, true and "2" are refused, as in every validator of the host.
            wanted = ('an integer' + (' (2, not 2.0)' if type(value) is float else '')) if kind == 'integer' else 'a number'
            raise ValueError(f'{path} requires {wanted}')
        try:
            finite = math.isfinite(value)
        except (OverflowError, TypeError):
            finite = False
        if not finite or not schema['minimum'] <= value <= schema['maximum']:
            raise ValueError(f"{path} requires a finite number from {schema['minimum']:g} to {schema['maximum']:g}; "
                             f"{value if finite else 'a non-finite number'} given")
        return int(value) if kind == 'integer' else float(value)
    raise ValueError('Unsupported catalog schema')
