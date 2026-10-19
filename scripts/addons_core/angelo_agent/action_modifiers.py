# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Non-destructive modifier add/configure, remove, move and apply kernels.

Typed types write the settings a call states and keep every other (a new
modifier: Blender's defaults; ``action_schemas.SETTINGS_NOTE``); any other type
of this build writes RNA-checked settings through ``generic_settings``. All
operands resolve through the staged context; topology is budgeted before any
evaluation, and a refused typed step leaves the stack as it was."""

from .action_budget import _modifier_bound, render_factor
from .action_context import _mesh, _readback
from .action_schema_types import adaptive_values
from .refusal_codes import RefusalError, refusal_code, refusal_details


def _configure_modifier(context, obj, modifier, kind, settings):
    """Write the settings the call states, and only those (action_schemas.SETTINGS_NOTE).

    Every other setting keeps its value: on a new modifier, Blender's own default.
    A typed setting writes the RNA property ``MODIFIER_RNA_NAMES`` names (its own
    name otherwise), and ``also_writes`` the properties it implies. Each written
    value is read back.
    """
    from .action_schemas import MODIFIER_RNA_NAMES, also_writes
    renamed, implied, written = MODIFIER_RNA_NAMES.get(kind, {}), also_writes(kind, settings), {}
    if kind == 'MIRROR' and (settings.get('mirror_object') is not None or settings.get('clear')):
        origin = None if settings.get('clear') else context.resolve(settings['mirror_object'])
        if origin == obj:
            raise RefusalError('MIRROR mirror_object resolves to the modified object itself', code='VALIDATION')
        modifier.mirror_object = origin
        if modifier.mirror_object != origin:
            raise RefusalError('MIRROR mirror_object did not retain its approved object identity', code='INTERNAL_ERROR')
    if 'operand_type' in implied:  # A Boolean's stated operand: an object, never a collection.
        operand = context.resolve(settings['operand'])
        if operand == obj:
            raise RefusalError('Boolean operand resolves to the target itself', code='VALIDATION')
        _mesh(context, operand)
        modifier.operand_type = 'OBJECT'
        modifier.object = operand
    if 'fit_type' in implied:
        modifier.fit_type = 'FIXED_COUNT'
    for key, value in settings.items():
        name = renamed.get(key, key)
        if name is None or key in ('mirror_object', 'operand'):
            continue  # Written above, or (clear, adaptive) not a property of its own.
        if key in OPERAND_SETTINGS:
            value = _operand(context, obj, kind, value)
        setattr(modifier, name, value)
        written[name] = value
    adaptive = adaptive_values(settings) if kind == 'SUBSURF' else {}
    for name, value in adaptive.items():
        setattr(modifier, name, value)
    written.update(adaptive)
    if kind == 'REMESH':
        require_remesh(modifier.mode)  # The mode it now has, stated or kept.
    if kind == 'MIRROR' and not any(modifier.use_axis):
        raise RefusalError('Mirror requires at least one axis', code='VALIDATION')
    _verify_settings(modifier, written)


def require_remesh(mode):
    """Refuse a Remesh mode this build cannot evaluate (it would silently pass geometry through)."""
    import bpy
    options = bpy.app.build_options
    available = getattr(options, 'openvdb', False) if mode == 'VOXEL' else getattr(options, 'mod_remesh', False)
    if not available:
        raise RefusalError('REMESH_UNAVAILABLE: this Blender build lacks %s, so a %s Remesh modifier would do nothing'
                         % ('OpenVDB' if mode == 'VOXEL' else 'the remesh feature', mode), code='UNSUPPORTED')


# A Shrinkwrap's target and a Curve modifier's object: staged objects, checked by _operand.
OPERAND_SETTINGS = frozenset({'target', 'object'})


def _operand(context, obj, kind, reference):
    operand = context.resolve(reference)
    if operand == obj:
        raise RefusalError('A modifier operand resolves to the target itself', code='VALIDATION')
    if kind == 'SHRINKWRAP':
        _mesh(context, operand)
    elif operand.type != 'CURVE':
        raise RefusalError('A Curve modifier needs a staged curve object', code='VALIDATION')
    return operand


def _settings_of(modifier):
    """Every writable stored setting of ``modifier`` (pointers included), to restore after a refused configure."""
    values = {}
    for prop in modifier.bl_rna.properties:
        if prop.is_readonly or prop.type == 'COLLECTION' or prop.identifier == 'rna_type':
            continue
        value = getattr(modifier, prop.identifier)
        values[prop.identifier] = tuple(value) if getattr(prop, 'array_length', 0) else value
    return values


def _restore(modifier, values):
    """Write back each setting that differs from ``values`` (Blender couples some, so only those still apart)."""
    for name, value in values.items():
        current = getattr(modifier, name)
        if (tuple(current) if isinstance(value, tuple) else current) != value:
            try:
                setattr(modifier, name, value)
            except (AttributeError, TypeError, ValueError, RuntimeError):
                pass  # Never in place of the refusal being raised; the program fails and its stage goes.


def _verify_settings(modifier, settings):
    for key, expected in settings.items():
        actual = getattr(modifier, key)
        if key in OPERAND_SETTINGS:
            continue
        if isinstance(expected, list):
            actual = list(actual)
            matched = len(actual) == len(expected) and all(abs(a - e) <= max(1e-6, 4e-7 * abs(e)) for a, e in zip(actual, expected))
        elif isinstance(expected, float):
            matched = abs(actual - expected) <= max(1e-6, 4e-7 * abs(expected))
        else:
            matched = actual == expected
        if not matched:
            raise RefusalError('Native modifier setting %s did not retain its approved value' % key, code='INTERNAL_ERROR')



def _generic_modifier(context, obj, operation):
    """Any other modifier type: created or found, its settings written through RNA, then bounded."""
    from .action_schemas import MODIFIER_SETTINGS
    from . import generic_settings
    params = operation['parameters']
    kind = params['type']
    generic_settings.check_type(kind, MODIFIER_SETTINGS)
    if operation['kind'] == 'modifier_configure':
        modifier = obj.modifiers.get(params['modifier'])
        if modifier is None or modifier.type != kind:
            raise RefusalError('Choose an existing staged %s modifier by its exact name' % kind,
                               code='VALIDATION')
    else:
        name = params['name'] or 'Angelo ' + kind.replace('_', ' ').title()
        if len(obj.modifiers) >= 16 or obj.modifiers.get(name) is not None:
            raise RefusalError('Modifier additions require a unique name and at most 16 staged modifiers', code='VALIDATION')
        try:
            modifier = obj.modifiers.new(name, kind)
        except (TypeError, RuntimeError) as error:
            raise RefusalError('MODIFIER_TYPE_UNAVAILABLE: %s (%s)' % (kind, str(error)[:120]),
                               code=refusal_code(error), details=refusal_details(error)) from error
        if modifier is None:
            raise RefusalError('MODIFIER_TYPE_UNAVAILABLE: %s cannot be added to this object' % kind, code='UNSUPPORTED')
        from .action_particles import adopt_new
        adopt_new(context, modifier)  # A particle system's new settings belong to the stage.
    generic_settings.apply(context, obj, modifier, params['settings'], 'modifier')
    bound = _modifier_bound(context, obj, prospective=True)  # Settings only; nothing has been evaluated yet.
    return _readback(obj, operation, modifier=modifier.name, evaluated_vertex_bound=bound,
                     show_viewport=bool(modifier.show_viewport), show_render=bool(modifier.show_render))


def _modifier(context, obj, operation):
    """A typed modifier added (Blender's defaults) or found, the settings the call states written, then bounded."""
    params = operation['parameters']
    _mesh(context, obj)
    from .action_schemas import MODIFIER_SETTINGS
    if params['type'] not in MODIFIER_SETTINGS:
        return _generic_modifier(context, obj, operation)
    if operation['kind'] == 'modifier_configure':
        modifier = obj.modifiers.get(params['modifier'])
        if modifier is None or modifier.type != params['type']:
            # Name the existing candidates so one corrected plan can succeed.
            names = [repr(item.name[:64]) for item in obj.modifiers if item.type == params['type']][:8]
            raise RefusalError('Choose an existing staged modifier of the exact declared type: no %s modifier is named '
                             '%r on this object; its %s modifiers: %s' % (params['type'], params['modifier'][:64],
                                                                         params['type'], ', '.join(names) or 'none'),
                               code='VALIDATION')
        kept = _settings_of(modifier)
    else:
        if len(obj.modifiers) >= 16 or obj.modifiers.get(params['name']) is not None:
            raise RefusalError('Modifier additions require a unique name and at most 16 staged modifiers', code='VALIDATION')
        active = obj.modifiers.active  # modifiers.new activates the one it adds.
        modifier, kept = obj.modifiers.new(params['name'], params['type']), None
    try:
        _configure_modifier(context, obj, modifier, params['type'], params['settings'])
        # The settings it now has, written or kept, bounded before anything evaluates them.
        bound = _modifier_bound(context, obj, prospective=True)
    except Exception:
        # Refused: the stack as it was, as when the bound preceded the modifier (nothing evaluated it meanwhile).
        if kept is None:
            obj.modifiers.remove(modifier)
            obj.modifiers.active = active
        else:
            _restore(modifier, kept)
        raise
    result = _readback(obj, operation, modifier=modifier.name, evaluated_vertex_bound=bound,
                       show_viewport=bool(modifier.show_viewport), show_render=bool(modifier.show_render))
    if params['type'] == 'SUBSURF':
        # The render-time size the stage never evaluates, and whether Cycles will dice adaptively.
        result.update(render_vertex_bound=bound * render_factor(obj),
                      adaptive=bool(modifier.use_adaptive_subdivision) and obj.modifiers[-1] == modifier)
    return result


# Removing or applying these loses data a rollback or Undo-free retry cannot
# rebuild (Multires displacement, bound deform caches, hook vertex lists, a
# particle system and its settings link).
UNREMOVABLE = frozenset({'MULTIRES', 'MESH_DEFORM', 'SURFACE_DEFORM', 'HOOK', 'LAPLACIANDEFORM', 'PARTICLE_SYSTEM'})


def _named(obj, name):
    modifier = obj.modifiers.get(name)
    if modifier is None:
        raise RefusalError('No staged modifier has this exact name', code='VALIDATION')
    return modifier


def _stack(obj):
    return [[modifier.name, modifier.type] for modifier in obj.modifiers]


def _remove_modifier(context, obj, operation):
    modifier = _named(obj, operation['parameters']['modifier'])
    if modifier.type in UNREMOVABLE:
        raise RefusalError('Removing this modifier type would discard data the native commit cannot restore', code='UNSUPPORTED')
    before = _stack(obj)
    obj.modifiers.remove(modifier)
    return _readback(obj, operation, modifiers_before=before, modifiers_after=_stack(obj))


def _move_modifier(context, obj, operation):
    params = operation['parameters']
    modifier = _named(obj, params['modifier'])
    if params['index'] >= len(obj.modifiers):
        raise RefusalError('The target index is outside the staged modifier stack', code='VALIDATION')
    before = _stack(obj)
    obj.modifiers.move(list(obj.modifiers).index(modifier), params['index'])
    if obj.modifiers[params['index']].name != params['modifier']:
        raise RefusalError('The staged modifier did not move to the approved index', code='INTERNAL_ERROR')
    return _readback(obj, operation, modifiers_before=before, modifiers_after=_stack(obj))


def _apply_modifier(context, obj, operation):
    """Bake the first modifier into the private mesh copy with Blender's own operator."""
    import bpy
    from .action_budget import _update_stage
    mesh = _mesh(context, obj, topology=True)
    modifier = _named(obj, operation['parameters']['modifier'])
    if obj.modifiers[0] != modifier:
        raise RefusalError('Only the first modifier applies exactly; move it to index 0 first', code='VALIDATION')
    if modifier.type in UNREMOVABLE:
        raise RefusalError('Applying this modifier type is outside the native capability', code='UNSUPPORTED')
    if not modifier.show_viewport:
        raise RefusalError('A disabled modifier has no evaluated result to apply', code='VALIDATION')
    bound = _modifier_bound(context, obj)
    before = {'stack': _stack(obj), 'vertices': len(mesh.vertices), 'faces': len(mesh.polygons)}
    _update_stage(context)
    layer = context.view_layer
    active = layer.objects.active
    try:
        layer.objects.active = obj
        with bpy.context.temp_override(scene=context.scene, view_layer=layer, object=obj, active_object=obj,
                                       selected_objects=[obj], selected_editable_objects=[obj]):
            result = bpy.ops.object.modifier_apply(modifier=modifier.name)
    finally:
        layer.objects.active = active
    if 'FINISHED' not in result or obj.modifiers.get(operation['parameters']['modifier']) is not None:
        raise RefusalError('Blender did not apply the staged modifier', code='INTERNAL_ERROR')
    if obj.data != mesh:
        raise RefusalError('Applying replaced the private mesh datablock unexpectedly', code='INTERNAL_ERROR')
    _mesh(context, obj)  # Bounded, finite result.
    context.topology_versions[obj] = context.topology_versions.get(obj, 0) + 1
    return _readback(obj, operation, modifiers_before=before['stack'], modifiers_after=_stack(obj),
                     counts_before={'vertices': before['vertices'], 'faces': before['faces']},
                     evaluated_vertex_bound=bound, topology_step=context.topology_versions[obj])
