# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Private Principled/shader material assignment and Geometry Nodes actions.

Graph provenance and construction live in action_node_graphs; evaluation work
is checked before allocating a new geometry group or adding its modifier."""

import copy
import json
import bpy
from .refusal_codes import RefusalError, refusal_code, refusal_details
from .action_catalog import geometry_node_work
from .action_context import _mesh, _face_selection, _readback, _counts, _require_subdivision
from .action_budget import _modifier_bound
from .action_node_graphs import _socket, _build_nodes
from .action_schema_types import MAX_EVALUATED_VERTICES
from .action_schemas import GEOMETRY_NODE_TYPES
from .legacy_names import NATIVE_GRAPH
from .material_rules import names_only, principled_inputs


def _existing(context, obj, operation):
    """The target's own material named ``name`` (staged copy or created in this program) and its slots, or None.

    Names are compared exactly, by the name commit gives the material
    (node_edit.logical_name), never by base name.
    """
    from .node_edit import logical_name
    from .node_edit_commit import edits_in_place
    if not edits_in_place(operation):
        return None
    name = operation['parameters']['name']
    slots = [index for index, slot in enumerate(obj.material_slots)
             if slot.material is not None and logical_name(context, operation['target'], slot.material) == name]
    return (obj.material_slots[slots[0]].material, slots) if slots else None


def _edit_in_place(context, obj, operation, slots):
    """The private copy of the target's material that this operation edits, keeping its ID and name.

    ``material_principled`` sets inputs of its one Principled BSDF; ``shader_nodes`` replaces the graph it states.

    node_edit's REMAP path: commit gives every use of the live material the
    edited copy, which takes its name. A material other users share is refused.
    """
    from .node_edit import NodeEditError, _material as staged_material
    params = operation['parameters']
    if params['slot'] != -1 and params['slot'] not in slots:
        raise RefusalError('%s: %r is already in slot %s of this object and is edited in place; use slot -1 or %s'
                         % (operation['kind'], params['name'][:64], slots[0], slots[0]), code='VALIDATION')
    try:
        return staged_material(context, operation['target'], obj,
                               {'material': params['name'], 'shared': params['shared']}, True)
    except NodeEditError as error:
        detail = str(error).split(': ', 1)[-1]
        if error.code == 'NODE_TREE_SHARED' and params['shared'] == 'REFUSE':
            detail = ('%r is also used elsewhere, and editing it in place would change those users too; pass shared '
                      'ALL_USERS to edit it for every object that uses it, or give this object a material with a new '
                      'name' % params['name'][:64])
        raise RefusalError('%s %s: %s' % (operation['kind'], error.code, detail),
                           code=refusal_code(error), details=refusal_details(error))


def _named_faces(mesh, params):
    """The faces a material kind names (indices or a selector) when its material is already in a slot; none named
    leaves every face where it is. Reading indices alone ignored a selector."""
    if params['face_indices'] or params.get('face_selector') is not None:
        return _face_selection(mesh, params)
    return []


def _keep(context, obj, mesh, operation, existing):
    """A name-only ``material_principled`` of a material its target holds: that material, unchanged.

    It names the material and states none of its values, so it assigns the
    material's slot to the faces it names (with no face field: every face,
    unless a base face already uses the material, ``material_faces.give_faces``,
    as its ``also`` objects get it); it never rebuilds the material at its default values (otherwise
    a held red material, live ones included, committed grey). ``program_material_obligations`` binds the same keep.
    """
    from . import action_materials as materials
    from .node_edit import logical_name
    params = operation['parameters']
    material, slots = existing
    if params['slot'] != -1 and params['slot'] not in slots:
        raise RefusalError('%s: %r is already in slot %s of this object; use slot -1 or %s'
                           % (operation['kind'], params['name'][:64], slots[0], slots[0]), code='VALIDATION')
    slot = slots[0] if params['slot'] == -1 else params['slot']
    selected = _named_faces(mesh, params)
    for polygon in selected:
        polygon.material_index = slot
    if not selected:   # No face field: the target renders what it is given, as its also objects do.
        from .material_faces import give_faces
        before = [polygon.material_index for polygon in mesh.polygons]
        give_faces(obj, mesh, [slot] + [held for held in slots if held != slot])
        selected = [polygon for polygon, index in zip(mesh.polygons, before) if polygon.material_index != index]
    result = _readback(obj, operation, material=logical_name(context, operation['target'], material), slot=slot,
                       slot_link=obj.material_slots[slot].link, assigned_faces=len(selected),
                       **materials.also(context, operation, material))
    result['kept'] = True  # The material as the object holds it.
    return result


def _new_principled(material):
    """A new material's graph: one Principled BSDF into the Material Output, at Blender's defaults."""
    material.node_tree.nodes.clear()
    shader = material.node_tree.nodes.new('ShaderNodeBsdfPrincipled')
    output = material.node_tree.nodes.new('ShaderNodeOutputMaterial')
    material.node_tree.links.new(shader.outputs['BSDF'], output.inputs['Surface'])
    return shader


def _principled_in_place(material, operation):
    """The one Principled BSDF of a held material whose stated inputs a ``material_principled`` sets.

    Every other node, link and value stays. Refused (material_rules.in_place_refusal, which the bound obligations
    share) when the material has none or several, when a link drives a stated input, or when it does not reach the
    active output's Surface (its values would not show).
    """
    from .material_rules import driven, in_place_refusal, tree_reaches
    tree = material.node_tree
    shaders = [node for node in tree.nodes if node.bl_idname == 'ShaderNodeBsdfPrincipled'] if tree is not None else []
    stated = principled_inputs(operation['parameters'])
    linked = sorted(name for name in stated if len(shaders) == 1 and driven(_socket(shaders[0], name)))
    refusal = in_place_refusal(operation, len(shaders), linked, len(shaders) == 1 and tree_reaches(tree, shaders[0]))
    if refusal is not None:
        raise refusal
    return shaders[0]


def _material(context, obj, operation):
    from .node_edit import logical_name
    from .node_edit_commit import edits_in_place
    from . import action_materials as materials, material_sharing
    mesh = _mesh(context, obj, slots=True)
    params = operation['parameters']
    existing = _existing(context, obj, operation)
    if existing is not None and names_only(operation):
        return _keep(context, obj, mesh, operation, existing)
    # A name an earlier operation of this program stated: the same material again, never a suffixed second one.
    stated = materials.defined(context, operation) if existing is None and edits_in_place(operation) else None
    if existing is not None:
        # Naming a material the target already has edits it (otherwise
        # a new 'Red Plastic.002' replaced the slot and orphaned 'Red Plastic').
        material = _edit_in_place(context, obj, operation, existing[1])
        slot = next(index for index, item in enumerate(obj.material_slots) if item.material == material)
        slot = slot if params['slot'] == -1 else params['slot']
        selected = _named_faces(mesh, params)
    else:
        selected = _face_selection(mesh, params)
        slot = len(mesh.materials) if params['slot'] == -1 else params['slot']
        if slot > len(mesh.materials) or slot >= 32:
            raise RefusalError('Material slot must be existing or the next append slot, with at most 32 slots', code='VALIDATION')
    if stated is not None:
        material = stated
    else:
        if existing is None:
            material = context.track_id(bpy.data.materials.new(params['name']))
            if edits_in_place(operation) and material.name != params['name']:
                raise RefusalError(str(materials.name_taken(operation['kind'], params['name'])), code='VALIDATION')
            if material.node_tree is None:  # Pre-5.2 compatibility, not a hidden shader toggle.
                material.use_nodes = True
            context.owned_ids.add(material.node_tree)  # Embedded tree is cleaned up with its material.
        if operation['kind'] == 'material_principled':
            # Only the stated inputs are written (SETTINGS_NOTE): a new material's fresh Principled BSDF keeps
            # Blender's defaults for the rest, and a held one keeps its graph and values (_principled_in_place).
            shader = _principled_in_place(material, operation) if existing is not None else _new_principled(material)
            inputs = principled_inputs(params)
            for name, value in inputs.items():
                _socket(shader, name).default_value = value
            if 'Base Color' in inputs:
                material.diffuse_color = inputs['Base Color']  # The viewport colour follows the stated one.
        else:  # shader_nodes states a whole graph.
            material.node_tree.nodes.clear()
            _build_nodes(material.node_tree, params, 'SHADER')
        if edits_in_place(operation):
            materials.remember(context, operation, material, held=existing is not None)
    if existing is None:
        materials.place(obj, mesh, material, slot, [])
    for polygon in selected:
        polygon.material_index = slot
    if obj.material_slots[slot].material != material:
        raise RefusalError('Native material assignment did not update the effective object slot', code='INTERNAL_ERROR')
    from .node_edit_spec import MODE
    isolated = material.get(MODE) == 'ASSIGN'  # An isolated copy commits under a name of its own.
    name = (materials.result_name(context, material) if stated is not None or isolated
            else logical_name(context, operation['target'], material))
    result = _readback(obj, operation, material=name, slot=slot, slot_link=obj.material_slots[slot].link,
                       assigned_faces=len(selected), **materials.also(context, operation, material, existing is not None))
    if existing is not None:
        result['edited_in_place'] = True  # program_objects.apply: commit transfers the edited copy.
        from .node_edit import library_copy
        copied = library_copy(material)
        if copied is not None:  # A linked material: Blender's local copy, stated (never claimed to edit the library).
            result['library_copy'] = copied
        users = material_sharing.users_of(material)
        if users is not None:  # Edited for all its users (named again, from the live scene, at commit).
            result.update(shared_policy='ALL_USERS', shared_users=users[:material_sharing.MAX_NAMED_USERS],
                          shared_user_count=len(users))
    elif stated is not None:
        result['reused'] = True  # The material an earlier operation of this program stated.
    return result



def _geometry(context, obj, operation):
    mesh = _mesh(context, obj)
    params = operation['parameters']
    if len(obj.modifiers) >= 16 or obj.modifiers.get(params['name']) is not None:
        raise RefusalError('Geometry Nodes require a unique modifier name and at most 16 modifiers', code='VALIDATION')
    prior_bound = _modifier_bound(context, obj)
    shape = _counts(mesh)
    # An upstream modifier changes the actual group input count; conservatively
    # treat all three domains as the preceding bound before traversing the DAG.
    if obj.modifiers:
        shape = {'vertices': prior_bound, 'edges': 0, 'faces': 0}
    bound = geometry_node_work(params, shape)
    if bound > MAX_EVALUATED_VERTICES:
        raise RefusalError('Geometry Nodes graph exceeds the conservative evaluated topology budget', code='LIMIT_EXCEEDED')
    for definition in params['nodes']:
        if definition['type'] == 'SUBDIVIDE_MESH':
            _require_subdivision(definition['inputs']['Level'])
    tree = context.track_id(bpy.data.node_groups.new(params['name'], 'GeometryNodeTree'))
    tree.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    tree.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    _build_nodes(tree, params, 'GEOMETRY')
    if any(definition['type'] not in GEOMETRY_NODE_TYPES for definition in params['nodes']):
        # Generic nodes: the bound is read from the built (never evaluated) nodes and their literal inputs.
        from .node_generic import mesh_area, tree_bound
        # The new modifier is first only on an object without modifiers: then its input is the base mesh.
        area = None if obj.modifiers else (lambda: mesh_area(mesh))
        bound = tree_bound(tree, max(shape['vertices'], shape['faces'] * 4, shape['edges'] * 2), area)
        if bound > MAX_EVALUATED_VERTICES:
            raise RefusalError('Geometry Nodes graph exceeds the conservative evaluated topology budget', code='LIMIT_EXCEEDED')
    tree[NATIVE_GRAPH[0]] = json.dumps(params, sort_keys=True, separators=(',', ':'), allow_nan=False)
    context.node_graphs[tree] = copy.deepcopy(params)
    context.node_bounds[tree] = bound
    modifier = obj.modifiers.new(params['name'], 'NODES')
    modifier.node_group = tree
    return _readback(obj, operation, modifier=modifier.name, node_group=tree.name,
                     nodes=len(tree.nodes), links=len(tree.links), evaluated_vertex_bound=bound)
