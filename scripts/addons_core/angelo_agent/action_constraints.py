# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Generic constraints: any constraint type of this build, and configuring an existing one.

A type without a typed schema (``constraint_add``'s generic variant) is
created on the staged object and its settings written through RNA
(``generic_settings``); ``constraint_configure`` writes the influence and
settings of an existing constraint by its exact name. Every object a
constraint points at must be a declared, staged dependency that does not
depend on the owner, so no cycle and no undeclared live object is reached.
Constraints change no topology, so they need no evaluation bound.
"""

from .refusal_codes import RefusalError, refusal_code, refusal_details
from .action_context import _readback
from . import generic_settings


def _no_cycles(context, obj, constraint):
    from .action_object import _dependency_reaches
    for target in generic_settings.object_pointers(constraint):
        context.assert_staged(target)
        if target == obj or _dependency_reaches(context, target, obj):
            raise RefusalError('Native constraints cannot create a dependency cycle', code='VALIDATION')


def add(context, obj, operation):
    params = operation['parameters']
    if len(obj.constraints) >= 16:
        raise RefusalError('Constraints require a unique name and at most 16 constraints', code='LIMIT_EXCEEDED')
    name = params['name'] or 'Angelo ' + params['type'].replace('_', ' ').title()
    if obj.constraints.get(name) is not None:
        raise RefusalError('Constraints require a unique name and at most 16 constraints', code='VALIDATION')
    try:
        constraint = obj.constraints.new(params['type'])
    except (TypeError, RuntimeError) as error:
        raise RefusalError('CONSTRAINT_TYPE_UNAVAILABLE: %s (%s)' % (params['type'], str(error)[:120]),
                           code=refusal_code(error), details=refusal_details(error)) from error
    constraint.name = name
    if 'influence' in params:  # Left out: Blender's own (SETTINGS_NOTE).
        constraint.influence = params['influence']
    if params['target'] is not None:
        if not hasattr(constraint, 'target'):
            raise RefusalError('A %s constraint has no target; name its objects in settings' % params['type'], code='VALIDATION')
        constraint.target = context.resolve(params['target'])
    generic_settings.apply(context, obj, constraint, params['settings'], 'constraint')
    _no_cycles(context, obj, constraint)
    return _readback(obj, operation, constraint=constraint.name, type=constraint.type)


def configure(context, obj, operation):
    params = operation['parameters']
    constraint = obj.constraints.get(params['constraint'])
    if constraint is None:
        names = [repr(item.name[:64]) for item in obj.constraints][:8]
        raise RefusalError('No staged constraint has this exact name; its constraints: ' + (', '.join(names) or 'none'),
                           code='VALIDATION')
    if params['influence'] is not None:
        constraint.influence = params['influence']
    generic_settings.apply(context, obj, constraint, params['settings'], 'constraint')
    _no_cycles(context, obj, constraint)
    return _readback(obj, operation, constraint=constraint.name, type=constraint.type)
