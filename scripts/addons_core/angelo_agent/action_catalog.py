# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Closed, replayable native modeling actions (no bpy dependency).

Public entry point: schemas/defaults live in action_schemas; primitive schema
validation in action_schema_types. This module owns cross-field validation,
resource dependencies, discovery and conservative pure work estimates.
Object references are document handles, never Blender names; $alias refers
only to an earlier creation in the same staged program."""

import copy
import math
from .action_schema_types import (
    MAX_OPERATIONS, MAX_VERTICES, MAX_EDGES, MAX_FACES, MAX_EVALUATED_VERTICES,
    MAX_INDEX_SELECTION, MAX_NODES, MAX_LINKS, HANDLE, ALIAS, NODE_ID, INDICES,
    COLOR, UNIT, ANGLES, SCALE, number, integer, enum, boolean, text, array,
    vector, nullable, obj, _validate, float32_vector, without_nulls,
)
from .action_schemas import (
    MODIFIER_SETTINGS, MODIFIER_OPERANDS, SHADER_NODE_TYPES, GEOMETRY_NODE_TYPES, LINK,
    FACE_SELECTOR, EDGE_SELECTOR, CONSTRAINT_SETTINGS, TARGETLESS_CONSTRAINTS, PARAMETER_SCHEMAS,
    INDEXED_KINDS, TOPOLOGY_KINDS, CREATE_KINDS, ACTION_SCHEMAS,
    OPERATION_SCHEMAS, CAPABILITIES, BEND_NOTE, _extra_inputs, _mutations,
)
from .action_camera_light_spec import CAMERA_LIGHT_KINDS, canonical_camera_light, data_fields
from . import action_properties, particle_rules, action_bones_spec as _bones, action_operator_spec as operator_spec
from . import node_edit_spec, batch_spec, material_rules
from .action_world_spec import canonical as _canonical_world
from .node_graph_rules import validate_graph
from . import anim_spec, anim_structure_spec, open_values, scene_binding

# (canonical result, raw operation) -> None; bone settings paths and rest/pose rules, ParticleSettings by RNA path,
# operator_run's allow-list, exact property values and selection rules.
_SPEC_RULES = {'world_configure': lambda result, raw: _canonical_world(result['parameters']),
               'bone_set': _bones.canonical, 'particle_set': lambda result, raw: particle_rules.canonical(raw, result),
               'operator_run': lambda result, raw: operator_spec.canonical(result['parameters'], raw['parameters']),
               'node_edit': node_edit_spec.canonical, 'batch': batch_spec.canonical,
               **{kind: material_rules.canonical for kind in material_rules.KINDS},
               **{kind: anim_spec.canonical for kind in anim_spec.KEY_KINDS},
               **{kind: anim_structure_spec.canonical for kind in anim_structure_spec.STRUCTURE_KINDS}}
# Key, Action, NLA and marker kinds own their references and families.
_ANIMATION_SPECS = {**{kind: anim_spec for kind in anim_spec.KEY_KINDS},
                    **{kind: anim_structure_spec for kind in anim_structure_spec.STRUCTURE_KINDS}}

def validate_operation(operation):
    """Return an independent canonical operation, rejecting unknown keys/kinds."""
    if type(operation) is not dict or type(operation.get('kind')) is not str or operation['kind'] not in ACTION_SCHEMAS:
        raise ValueError('Choose a documented native action kind')
    operation = open_values.wrapped(operation, OPERATION_SCHEMAS[operation['kind']], open_values.long_handle)
    if operation['kind'] in {'property_set', 'property_keyframe'} and type(operation.get('parameters')) is dict:
        # A typed reason (file path, driver, custom property...) before the generic schema error.
        action_properties.screen_values(operation['parameters'].get('values'))
    scene_binding.check_target(operation)  # The scene's (filled in where observed: implicit_targets) or an object's.
    if 'target' not in operation and operation['kind'] in scene_binding.TARGETLESS_KINDS:
        operation = dict(operation, target=None)  # Nothing to choose, so nothing to write.
    result = _validate(operation, OPERATION_SCHEMAS[operation['kind']], 'operation')
    kind, params = result['kind'], result['parameters']
    if kind in _SPEC_RULES:  # Cross-field rules owned by the kind's spec module.
        _SPEC_RULES[kind](result, operation)
    if kind == 'transform_apply' and not (params['location'] or params['rotation'] or params['scale']):
        raise ValueError('Apply at least one of location, rotation or scale')
    if kind == 'origin_set' and (params['mode'] == 'POINT') != (params['point'] is not None):
        raise ValueError('POINT origins need a world point; other modes take none')
    if kind == 'shape_key_add' and params['slider_min'] >= params['slider_max']:
        raise ValueError('The shape key slider minimum must be below its maximum')
    if kind == 'visibility_set':
        flags = [key for key in ('exclude', 'hide_viewport', 'hide_render', 'hide_select') if params[key] is not None]
        if params['collection'] is None and (params['hidden'] is None or flags):
            raise ValueError('An object takes hidden only (its own disable flags are property_set paths); '
                             'name a collection, with the scene handle as target, for the others')
        if params['collection'] is not None and params['hidden'] is None and not flags:
            raise ValueError('Set at least one of exclude, hidden, hide_viewport, hide_render or hide_select')
    if kind == 'mesh_separate' and (params['mode'] == 'SELECTION') != bool(params['face_indices'] or params['face_selector']):
        raise ValueError('SELECTION separates the chosen faces (indexes or a selector); LOOSE and MATERIAL take none')
    if kind == 'object_convert' and params['source'] == '$' + params['alias']:
        raise ValueError('A converted object needs a different alias from its source')
    if kind == 'curve_points' and params['spline_type'] == 'NURBS' and params['cyclic'] and len(params['points']) < 3:
        raise ValueError('A cyclic NURBS spline needs at least three points')
    if kind == 'constraint_configure':
        from .generic_settings import screen_settings
        screen_settings(operation['parameters'].get('settings', {}))
        params['settings'] = copy.deepcopy(operation['parameters'].get('settings', {}))
        if not params['settings'] and params['influence'] is None:
            raise ValueError('Configure at least one setting or the influence')
        if result['target'] in _setting_references(params['settings']):
            raise ValueError('A constraint cannot point at its own object')
    if kind in {'modifier_add', 'modifier_configure'} and params['type'] not in MODIFIER_SETTINGS:
        from .generic_settings import screen_settings
        screen_settings(operation['parameters'].get('settings', {}))
        params['settings'] = copy.deepcopy(operation['parameters'].get('settings', {}))
        if result['target'] in _setting_references(params['settings']):
            raise ValueError('A modifier cannot point at its own object')
    if kind == 'object_join' and result['target'] in params['sources']:
        raise ValueError('object_join sources must differ from the receiving target')
    if kind == 'material_assign' and (params['source'] == result['target'] or params['copy_as'] == params['material']):
        raise ValueError('material_assign takes a source other than its target (null: the target itself or this '
                         'program) and a copy_as name other than the material')
    if kind == 'object_duplicate' and params['source'] == '$' + params['alias']:
        raise ValueError('A duplicate needs a different alias from its source')
    if kind in {'property_set', 'property_keyframe'}:
        # The schema only shapes an open map; the raw values keep integers integers.
        params['values'] = action_properties.canonical(copy.deepcopy(operation['parameters']['values']))
    if kind == 'property_keyframe':
        for path in params['values']:
            if not action_properties.animatable(path) or action_properties.data_family(path) not in (None, 'data'):
                raise action_properties.PropertyPathError(
                    'NOT_ANIMATABLE', path, 'property_keyframe keys object and object-data settings')
    if kind == 'property_set':
        if result['target'] in action_properties.references(params['values']):
            raise ValueError('A property_set target cannot point at itself')
        action_properties.check_shared(params['values'], params.get('shared'))
    if kind in INDEXED_KINDS and params['index_scope'] == 'INITIAL' and params['topology_step'] != 0:
        raise ValueError('INITIAL indexes require topology_step 0')
    if kind == 'mesh_recalculate_normals' and params['topology_digest'] is not None and (
            params['index_scope'] != 'CURRENT' or not params['face_indices']):
        raise ValueError('A planned topology digest requires explicit CURRENT face indexes')
    selector = params.get('face_selector')
    if selector is not None:
        if params.get('face_indices') or selector['area_min'] > selector['area_max']:
            raise ValueError('Choose indexes or one valid rule-based face selector, not both')
        if selector['normal'] is not None:
            length = math.sqrt(sum(value * value for value in selector['normal']))
            if length < .000001:
                raise ValueError('Face selector normal must be nonzero')
            selector['normal'] = [value / length for value in selector['normal']]
    edge_selector = params.get('edge_selector')
    if edge_selector is not None and (params.get('edge_indices') or edge_selector['angle_min'] > edge_selector['angle_max']):
        raise ValueError('Choose indexes or one valid rule-based edge selector, not both')
    if kind in {'mesh_extrude', 'mesh_inset', 'mesh_subdivide', 'mesh_delete'} and not params['face_indices'] and selector is None:
        raise ValueError('This mesh action requires explicit face indexes or a rule-based selector')
    if kind == 'mesh_bevel' and not params['edge_indices'] and edge_selector is None:
        raise ValueError('Bevel requires explicit edge indexes or a rule-based selector')
    if kind == 'mesh_extrude' and math.sqrt(sum(value * value for value in params['translation'])) < .000001:
        raise ValueError('Extrusion translation must be nonzero')
    if kind == 'mesh_bend' and (abs(params['rise']) < .000001 or params['axis'] == params['up']):
        raise ValueError('mesh_bend takes a nonzero rise and an axis other than up')
    scale = params.get('scale')
    if isinstance(scale, list) and any(abs(value) < .000001 for value in scale):
        raise ValueError('Scale components must be nonzero')
    if kind == 'transform' and all(params[field] is None for field in ('location', 'rotation', 'scale')):
        raise ValueError('Transform requires at least one explicit channel')
    if kind == 'modifier_add' or kind == 'modifier_configure':
        # What the call states (SETTINGS_NOTE; a typed null keeps a setting, as leaving it out does); kernels check the rest.
        settings = params['settings'] = (without_nulls(params['settings']) if params['type'] in MODIFIER_SETTINGS
                                         else params['settings'])
        if params['type'] == 'MIRROR' and 'axes' in settings and not any(settings['axes']):
            raise ValueError('Mirror requires at least one axis')
        if params['type'] == 'MIRROR':
            origin = settings.get('mirror_object')
            if origin is not None and settings.get('clear'):
                raise ValueError('MIRROR clear and mirror_object cannot be set together')
            if origin is not None and origin == result['target']:
                raise ValueError('MIRROR mirror_object cannot be the modified object itself')
        operand = MODIFIER_OPERANDS.get(params['type'])
        if operand and settings.get(operand) == result['target']:
            raise ValueError('A modifier operand (Boolean, Shrinkwrap target or Curve object) cannot be the target')
    if kind in {'shader_nodes', 'geometry_nodes'}:
        validate_graph(params, 'SHADER' if kind == 'shader_nodes' else 'GEOMETRY')
        raw = operation['parameters']['nodes']
        typed = SHADER_NODE_TYPES if kind == 'shader_nodes' else GEOMETRY_NODE_TYPES
        for node, given in zip(params['nodes'], raw):
            if node['type'] not in typed:  # Generic values keep their JSON types (integers stay integers).
                node['inputs'], node['settings'] = (copy.deepcopy(given.get(key, {})) for key in ('inputs', 'settings'))
        for node in params['nodes']:
            if isinstance(node['inputs'].get('Scale'), list) and any(abs(value) < .000001 for value in node['inputs']['Scale']):
                raise ValueError('Node scale components must be nonzero')
            if node['type'] == 'MESH_CUBE' and any(abs(value) < .000001 for value in node['inputs']['Size']):
                raise ValueError('Cube node size components must be nonzero')
    if kind == 'constraint_add' and params['type'] not in CONSTRAINT_SETTINGS:
        from .generic_settings import screen_settings, DENIED_CONSTRAINTS
        if params['type'] in DENIED_CONSTRAINTS:
            raise ValueError('CONSTRAINT_TYPE_DENIED: script constraints run Python')
        screen_settings(operation['parameters'].get('settings', {}))
        params['settings'] = copy.deepcopy(operation['parameters'].get('settings', {}))  # Integers stay integers.
        if params['target'] == result['target'] or result['target'] in _setting_references(params['settings']):
            raise ValueError('Constraint target cannot be the owner')
    elif kind == 'constraint_add':
        if params['type'] not in TARGETLESS_CONSTRAINTS and params['target'] is None:
            raise ValueError('This constraint requires a staged target reference')
        if params['type'] in TARGETLESS_CONSTRAINTS and params['target'] is not None:
            raise ValueError('Limit constraints have no target')
        if params['target'] == result['target']:
            raise ValueError('Constraint target cannot be the owner')
        settings = params['settings']  # What the call states; the kernel checks the constraint it makes.
        if params['type'] == 'TRACK_TO' and {'track_axis', 'up_axis'} <= set(settings) and settings['track_axis'][-1] == settings['up_axis'][-1]:
            raise ValueError('Track and up axes must differ')
        if params['type'] in TARGETLESS_CONSTRAINTS and {'minimum', 'maximum'} <= set(settings):
            lower = settings.get('use_limit', settings.get('use_min')) or [False] * 3
            upper = settings.get('use_limit', settings.get('use_max')) or [False] * 3
            if any(lower[i] and upper[i] and settings['minimum'][i] > settings['maximum'][i] for i in range(3)):
                raise ValueError('Constraint minimum cannot exceed maximum')
    if kind == 'parent_set' and params['parent'] == result['target']:
        raise ValueError('An object cannot parent itself')
    if kind == 'keyframe_insert' and params['property'] == 'scale' and any(abs(value) < .000001 for value in params['value']):
        raise ValueError('Keyframe scale components must be nonzero')
    # A key's value takes what its channel's schema states: a spin of several turns keys rotation past 2 pi.
    if kind == 'armature_create':
        bones = {}
        for bone in params['bones']:
            bone['head'], bone['tail'] = float32_vector(bone['head']), float32_vector(bone['tail'])
            # Blender lengthens bones at/below its native 1e-6 threshold,
            # choosing an arbitrary Z direction for zero-length input.
            if bone['name'] in bones or math.dist(bone['head'], bone['tail']) <= float32_vector([.000001, 0, 0])[0]:
                raise ValueError('Bones require unique names and float32-representable lengths greater than 1e-6')
            if bone['parent'] is not None and bone['parent'] not in bones:
                raise ValueError('Bone parents must be declared earlier in this armature')
            if bone['connected'] and (bone['parent'] is None or bone['head'] != bones[bone['parent']]['tail']):
                raise ValueError('Connected bone heads must exactly meet the float32 parent tail')
            bones[bone['name']] = bone
    if kind in CAMERA_LIGHT_KINDS:
        canonical_camera_light(kind, params, operation.get('parameters'))
    if kind == 'armature_bind':
        if params['armature'] == result['target']:
            raise ValueError('An armature cannot deform itself')
        names, totals, count = set(), {}, 0
        for group in params['groups']:
            if group['bone'] in names:
                raise ValueError('Each bone weight group must be unique')
            names.add(group['bone'])
            indexes = set()
            for weight in group['weights']:
                index = weight['vertex']
                if index in indexes:
                    raise ValueError('Weight indexes must be unique within a group')
                indexes.add(index)
                totals[index] = totals.get(index, 0) + weight['weight']
                count += 1
        if count > 8192 or any(total > 1.000001 for total in totals.values()):
            raise ValueError('Explicit rig weights require at most 8192 entries and normalized vertex totals')
    return result


def affected_references(operation):
    """All document/alias object dependencies, including read-only operands."""
    operation = validate_operation(operation)
    params = operation['parameters']
    refs = [] if operation['target'] is None else [operation['target']]
    if operation['kind'] in {'modifier_add', 'modifier_configure'} and params['type'] in MODIFIER_OPERANDS:
        refs.append(params['settings'][MODIFIER_OPERANDS[params['type']]])
    if operation['kind'] in {'modifier_add', 'modifier_configure'} and params['type'] == 'MIRROR':
        if params['settings'].get('mirror_object') is not None:
            refs.append(params['settings']['mirror_object'])
    if operation['kind'] == 'parent_set' and params['parent'] is not None:
        refs.append(params['parent'])
    if operation['kind'] == 'constraint_add' and params['target'] is not None:
        refs.append(params['target'])
    if operation['kind'] in {'constraint_add', 'constraint_configure', 'modifier_add', 'modifier_configure',
                             'particle_set'}:
        refs.extend(_setting_references(params.get('settings') or {}))  # Generic {"object": handle} settings.
    if operation['kind'] == 'armature_bind':
        refs.append(params['armature'])
    if operation['kind'] == 'property_set':
        refs.extend(action_properties.references(params['values']))
    if operation['kind'] == 'object_duplicate' or (operation['kind'] == 'material_assign' and params['source']):
        refs.append(params['source'])
    if operation['kind'] in {'object_join', 'batch'}:
        refs.extend(params['sources'])
    refs.extend(material_rules.also(operation))
    if operation['kind'] == 'object_convert':
        refs.append(params['source'])
    if operation['kind'] == 'bone_set':
        refs.extend(_bones.references(params))  # A pose bone's custom shape.
    if operation['kind'] in _ANIMATION_SPECS:
        refs.extend(_ANIMATION_SPECS[operation['kind']].references(operation))  # A copy source, a marker camera.
    return tuple(dict.fromkeys(refs))


def _setting_references(settings):
    from .rna_rules import object_reference
    return [object_reference(value) for value in settings.values() if object_reference(value) is not None] \
        if isinstance(settings, dict) else []


def written_references(operation):
    """The references an operation changes: its target, and object_join's sources (deleted)."""
    operation = validate_operation(operation)
    refs = [] if operation['target'] is None else [operation['target']]
    if operation['kind'] == 'object_join' or (operation['kind'] == 'batch' and batch_spec.moves_sources(
            operation['parameters'])):  # A batch that places or varies its unit moves the sources too.
        refs.extend(operation['parameters']['sources'])
    if operation['kind'] == 'object_convert' and not operation['parameters']['keep_original']:
        refs.append(operation['parameters']['source'])  # Deleted after conversion.
    return tuple(refs + material_rules.also(operation))


def deleted_references(operation):
    """The existing objects an operation deletes: object_delete's target, object_join's sources and, without
    keep_original, object_convert's source. Commit approves exactly these deletions (program_commit_scope)."""
    kind, params = operation['kind'], operation.get('parameters') or {}
    if kind == 'object_delete':
        return (operation['target'],)
    if kind == 'object_join':
        return tuple(params.get('sources', ()))
    return (params['source'],) if kind == 'object_convert' and not params.get('keep_original', True) else ()


# What each kind makes that did not exist before (static; resource_dependencies reports it).
CREATES = {'create_primitive': ['object', 'mesh'], 'armature_create': ['object', 'armature'],
           'object_duplicate': ['object', 'data_if_full'], 'batch': ['object', 'data_if_full'],
           'create_object': ['object', 'curve_if_text_or_curve'],
           'camera_create': ['object', 'camera'], 'light_create': ['object', 'light'],
           'mesh_separate': ['object', 'mesh'], 'object_convert': ['object', 'mesh'],
           'material_principled': ['material'], 'shader_nodes': ['material'],
           'geometry_nodes': ['node_group'], 'keyframe_insert': ['action_if_absent']}


def resource_dependencies(operation):
    """Executor staging requirements. Never mutate a shared source ID in place.

    Stage all references and remap intra-object modifier/constraint/parent IDs.
    Mesh edits/material-slot assignment require a private mesh copy; keyframes
    require a private Action copy. New materials/node groups need journaled ID
    cleanup on abort and ownership transfer on commit, not source-ID deletion.
    """
    operation = validate_operation(operation)
    kind = operation['kind']
    creates = CREATES.get(kind, [])
    mutates = list(_mutations[kind])
    if kind in {'camera_configure', 'light_configure'}:
        # Only written families transfer at commit: a look_at-only edit must
        # not replace (and so de-instance) shared camera/light data.
        params = operation['parameters']
        mutates = [family for family in mutates if family == 'object' or
                   (family == 'transforms' and 'look_at' in params) or
                   (family in {'camera', 'light'} and data_fields(kind, params))]
    properties = []
    if kind == 'bone_set':  # Rest edits swap the armature data; pose edits copy the pose.
        mutates = _bones.families(operation['parameters'])
    if kind == 'operator_run':  # Object-level state only for forcefield_toggle: never de-instance shared data.
        mutates = operator_spec.families(operation['parameters'])
    if kind in _ANIMATION_SPECS:  # Data channels swap the data; RENAME is an intent; markers are scene state.
        mutates = _ANIMATION_SPECS[kind].families(operation)
    if kind == 'node_edit':  # An object's material or Geometry Nodes trees, or the bound scene's World.
        mutates = node_edit_spec.families(operation['parameters'])
    if kind == 'visibility_set':
        # A collection lives in the bound scene, written like its settings.
        mutates = ['scene'] if operation['parameters']['collection'] is not None else ['object', 'visibility']
    if kind == 'property_keyframe':
        # Data paths key the data, which commit swaps whole. Blender keys data into the
        # object's Action (another slot) when it has one, so that Action always transfers.
        properties = list(operation['parameters']['values'])
        object_paths = any(action_properties.data_family(path) is None for path in properties)
        data_paths = any(action_properties.data_family(path) == 'data' for path in properties)
        mutates = ['object'] + ['properties'] * object_paths + ['animation'] + ['data'] * data_paths
    if kind == 'property_set':
        # Only the written owner transfers at commit: object settings are
        # copied path by path, written data is swapped, scene paths copied.
        properties = list(operation['parameters']['values'])
        if action_properties.owner_of(properties[0]) == action_properties.SCENE:
            mutates = ['scene']
        else:
            # Object and active-material settings transfer path by path (the material
            # in place, as Blender edits it); data paths swap in the private data copy.
            families = {action_properties.data_family(path) for path in properties}
            mutates = ['object'] + ['properties'] * bool(families & {None, 'material'}) + ['data'] * ('data' in families)
    return {'references': list(affected_references(operation)), 'mutates': mutates,
            'creates': list(creates), 'requires_object_mode': True, 'properties': properties,
            'private_mesh': 'mesh' in _mutations[kind], 'private_action': 'animation' in _mutations[kind]}


def action_capabilities():
    """Bounded JSON discovery, including every permitted field and default."""
    return {'summary': copy.deepcopy(CAPABILITIES), 'actions': [
        {'kind': kind, 'target': 'null' if kind in CREATE_KINDS else 'document_handle_or_alias',
         'parameters': copy.deepcopy(schema), 'mutates': list(_mutations[kind])}
        for kind, schema in ACTION_SCHEMAS.items()]}


def action_index():
    """Every kind with its target form and mutated families, without parameter schemas.

    The complete catalog passed 100 KB as kinds were added; discovery returns
    this index and exact schemas are read by name (plan_schema.lookup).
    """
    return {'summary': copy.deepcopy(CAPABILITIES),
            'actions': [{'kind': kind, 'target': 'null' if kind in CREATE_KINDS else 'document_handle_or_alias',
                         'mutates': list(_mutations[kind])} for kind in ACTION_SCHEMAS],
            'lookup': 'Read exact parameter schemas with family ACTIONS and up to 32 names.'}


def catalog(kind=None):
    if kind is None:
        return action_capabilities()
    if kind not in ACTION_SCHEMAS:
        raise ValueError('Unknown native action kind')
    result = {'kind': kind, 'parameters': copy.deepcopy(ACTION_SCHEMAS[kind]),
              'target': 'null' if kind in CREATE_KINDS else 'document_handle_or_alias',
              'mutates': list(_mutations[kind])}
    notes = {'batch': batch_spec.PLACEMENT_NOTE, 'mesh_bend': BEND_NOTE}  # What its schema can't say.
    return dict(result, note=notes[kind]) if kind in notes else result


def geometry_node_work(parameters, shape):
    """Conservative acyclic graph bound, before Blender evaluates any nodes."""
    nodes = {node['id']: node for node in parameters['nodes']}
    incoming = {identifier: [] for identifier in nodes}
    for link in parameters['links']:
        if link['to_socket'] in {'Mesh', 'Geometry'}:
            incoming[link['to_node']].append(link['from_node'])
    memo = {}

    def bound(identifier):
        if identifier in memo:
            return memo[identifier]
        node = nodes[identifier]
        kind, inputs = node['type'], node['inputs']
        parents = [bound(parent) for parent in incoming[identifier]]
        if kind not in GEOMETRY_NODE_TYPES:  # Generic: the kernel bounds the built nodes (node_generic.tree_bound).
            memo[identifier] = max(parents, default=0)
            return memo[identifier]
        count = sum(parents) if kind == 'JOIN_GEOMETRY' else (max(parents) if parents else 0)
        if kind == 'GROUP_INPUT':
            count = max(shape['vertices'], shape['faces'] * 4, shape['edges'] * 2)
        elif kind == 'MESH_CUBE':
            x, y, z = (inputs['Vertices ' + axis] for axis in 'XYZ')
            count = 2 * (x * y + y * z + z * x)
        elif kind == 'MESH_GRID':
            count = inputs['Vertices X'] * inputs['Vertices Y']
        elif kind == 'SUBDIVIDE_MESH':
            count *= 4 ** inputs['Level']
        elif kind == 'EXTRUDE_MESH':
            count *= 6
        memo[identifier] = count
        return count

    # Bound disconnected nodes as well: lazy evaluation is not a safety promise.
    values = [bound(identifier) for identifier in nodes]
    return max(values, default=0)


def estimated_work(operation, shape=None):
    """JSON topology/work bound for preflight; shape counts are observed, not guessed.

    Returns base upper counts plus a conservative evaluated-vertex estimate.
    Executor also enforces cumulative program work; kernels refine selection
    valence bounds and guard existing modifier stacks before allocating.
    """
    operation = validate_operation(operation)
    if shape is None:
        shape = {'vertices': 0, 'edges': 0, 'faces': 0}
    counts = {}
    for name, limit in [('vertices', MAX_VERTICES), ('edges', MAX_EDGES), ('faces', MAX_FACES)]:
        value = shape.get(name, 0)
        if type(value) is not int or not 0 <= value <= limit:
            raise ValueError('Preflight shape requires bounded integer mesh counts')
        counts[name] = value
    params, kind = operation['parameters'], operation['kind']
    vertices, edges, faces = (counts[name] for name in ('vertices', 'edges', 'faces'))
    if kind == 'create_primitive':
        primitive = params['primitive']
        if primitive == 'CUBE':
            vertices, edges, faces = 8, 12, 6
        elif primitive == 'PLANE':
            vertices, edges, faces = 4, 4, 1
        elif primitive == 'GRID':
            x, y = params['x_segments'], params['y_segments']
            vertices, edges, faces = (x + 1) * (y + 1), x * (y + 1) + y * (x + 1), x * y
        elif primitive == 'ICO_SPHERE':
            multiplier = 4 ** (params['subdivisions'] - 1)
            vertices, edges, faces = 10 * multiplier + 2, 30 * multiplier, 20 * multiplier
        elif primitive == 'UV_SPHERE':
            vertices = params['segments'] * (params['rings'] + 1) + 2
            edges, faces = vertices * 3, params['segments'] * params['rings']
        elif primitive == 'CIRCLE':
            vertices = edges = params['segments']
            faces = int(params['fill'])
        elif primitive == 'TORUS':
            faces = vertices = params['major_segments'] * params['minor_segments']
            edges = 2 * faces
        else:
            vertices, edges = 2 * params['segments'] + 2, 3 * params['segments']
            faces = params['segments'] + (2 if params['cap_ends'] else 0)
    elif kind in {'mesh_extrude', 'mesh_inset'}:
        # Without an observed valence array, all edges may touch the selection.
        growth = edges * 2
        vertices += growth
        edges += growth * 2
        faces += growth
    elif kind in {'mesh_subdivide', 'mesh_bend', 'mesh_bevel'}:
        # Every face may be subdivided, each growing by at most (cuts + 1)^2. A bend's parallel cuts split each
        # edge at most once per cut, and the edges a cut adds lie in its plane, so no later cut crosses them.
        growth = (faces * ((params['cuts'] + 1) ** 2) if kind == 'mesh_subdivide'
                  else edges * (params['segments'] - 1) if kind == 'mesh_bend'
                  else len(params['edge_indices']) * 64 * (params['segments'] + 1) ** 2)
        vertices += growth
        edges += growth * 2
        faces += growth
    evaluated = max(vertices, faces * 4, edges * 2)
    if kind == 'geometry_nodes':
        evaluated = max(evaluated, geometry_node_work(params, counts))
    if kind in {'modifier_add', 'modifier_configure'}:
        settings = params['settings']  # Left out: a new modifier's Blender default (the kernel bounds the result).
        multiplier = {'SUBSURF': 4 ** settings.get('levels', 1),  # Viewport levels, as action_budget.
                      'MIRROR': 2 ** sum(settings.get('axes', [True])), 'SOLIDIFY': 3,
                      'BEVEL': (settings.get('segments', 1) + 1) ** 2 + 4, 'ARRAY': settings.get('count', 2),
                      'SCREW': (max(settings.get('steps', 16), settings.get('render_steps', 16)) + 1)
                               * settings.get('iterations', 1)}.get(params['type'], 1)
        evaluated *= multiplier
    return {'vertices': vertices, 'edges': edges, 'faces': faces, 'evaluated_vertices': evaluated,
            'work_units': max(1, vertices + edges + faces + evaluated)}
