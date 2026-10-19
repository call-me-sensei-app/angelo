# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Typed graph construction, bounded provenance and preview metadata.

Metadata is untrusted until actual nodes, sockets and links match the closed
contract. This module does not evaluate geometry or assign materials/modifiers."""

import copy
import json
import math
import bpy
from .refusal_codes import RefusalError, refusal_code, refusal_details
from .action_catalog import validate_operation
from .legacy_names import NATIVE_GRAPH, read_property
from .action_context import _local, _require_subdivision
from .action_schemas import GEOMETRY_NODE_TYPES, SHADER_NODE_TYPES

IMAGE_MARKER = '_angelo_image'


def node_metadata(context):
    """Serializable graph parameters for preview journals (not pointer bounds), of the graphs the stage still uses.

    A suspended stage keeps what its scene reaches, so a group no staged
    modifier uses any more (one ``modifier_apply`` baked) is not in it and has
    nothing left to evaluate: its parameters are left out, and hydration
    finds every recorded graph (otherwise a Geometry Nodes apply beside a
    World edit failed at commit).
    """
    used = _used_trees(context.scene)
    return {tree.name: copy.deepcopy(parameters) for tree, parameters in context.node_graphs.items() if tree in used}


def _used_trees(scene, limit=4096):
    """The node groups the scene's objects' modifiers use, and the groups nested in them (bounded)."""
    found, frontier = set(), [modifier.node_group for obj in scene.objects for modifier in obj.modifiers
                              if getattr(modifier, 'node_group', None) is not None]
    while frontier and len(found) < limit:
        tree = frontier.pop()
        if tree in found:
            continue
        found.add(tree)
        frontier.extend(node.node_tree for node in tree.nodes if getattr(node, 'node_tree', None) is not None)
    return found



def restore_node_metadata(context, metadata):
    """Restore only owned staged node trees, verifying actual graph contents."""
    if type(metadata) is not dict or len(metadata) > 512:
        raise RefusalError('Invalid bounded staged node metadata', code='INTERNAL_ERROR')
    by_name = {block.name: block for block in context.owned_ids if isinstance(block, bpy.types.GeometryNodeTree)}
    for name, parameters in metadata.items():
        tree = by_name.get(name)
        if tree is None:
            raise RefusalError('Node metadata refers outside the staged IDs', code='INTERNAL_ERROR')
        parameters = validate_operation({'kind': 'geometry_nodes', 'target': 'metadata', 'parameters': parameters})['parameters']
        _audit_geometry_tree(tree, parameters)
        context.node_graphs[tree] = parameters



def _node_parameters(context, tree):
    _local(tree)
    if tree not in context.owned_ids:
        raise RefusalError('Geometry Nodes evaluation requires a private staged node group', code='INTERNAL_ERROR')
    if tree not in context.node_graphs:
        encoded = read_property(tree, NATIVE_GRAPH)
        if type(encoded) is not str or len(encoded) > 65536:
            raise RefusalError('Only catalog-authored bounded Geometry Nodes can be evaluated in this kernel', code='UNSUPPORTED')
        try:
            parameters = json.loads(encoded)
        except (ValueError, TypeError) as error:
            raise RefusalError('Invalid bounded Geometry Nodes provenance',
                               code=refusal_code(error), details=refusal_details(error)) from error
        parameters = validate_operation({'kind': 'geometry_nodes', 'target': 'metadata', 'parameters': parameters})['parameters']
        context.node_graphs[tree] = parameters
    parameters = context.node_graphs[tree]
    _audit_geometry_tree(tree, parameters)
    for definition in parameters['nodes']:
        if definition['type'] == 'SUBDIVIDE_MESH':
            _require_subdivision(definition['inputs']['Level'])
    return parameters



def _same_socket_value(actual, expected):
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(abs(float(a) - b) <= 1e-5 for a, b in zip(actual, expected))
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return math.isfinite(float(actual)) and abs(float(actual) - expected) <= 1e-5
    return actual == expected



def _audit_geometry_tree(tree, parameters):
    """Metadata is untrusted until the real closed graph matches it exactly."""
    if tree.animation_data is not None:
        raise RefusalError('Animated or driven node groups are outside the bounded graph contract', code='UNSUPPORTED')
    interface = [item for item in tree.interface.items_tree if item.item_type == 'SOCKET']
    if len(interface) != 2 or any(item.name != 'Geometry' or item.socket_type != 'NodeSocketGeometry' for item in interface):
        raise RefusalError('Geometry Nodes must have exactly the closed Geometry input/output interface', code='INTERNAL_ERROR')
    if {item.in_out for item in interface} != {'INPUT', 'OUTPUT'}:
        raise RefusalError('Geometry Nodes interface directions do not match the catalog', code='INTERNAL_ERROR')
    if len(tree.nodes) != len(parameters['nodes']) or len(tree.links) != len(parameters['links']):
        raise RefusalError('Geometry Nodes graph no longer matches its approved provenance', code='INTERNAL_ERROR')
    from . import node_generic
    for definition in parameters['nodes']:
        node = tree.nodes.get(definition['id'])
        if definition['type'] not in GEOMETRY_NODE_TYPES:  # A generic node: its type, inputs and settings.
            if node is None or node.bl_idname != definition['type'] or node.mute:
                raise RefusalError('Geometry Nodes contain an unsupported or changed node', code='INTERNAL_ERROR')
            for name, value in definition['inputs'].items():
                socket = node_generic.find_socket(node.inputs, name)
                if socket is None or socket.is_linked or not _same_socket_value(socket.default_value, value):
                    raise RefusalError('Geometry Nodes input no longer matches the approved bounded value', code='INTERNAL_ERROR')
            continue
        if node is None or node.bl_idname != GEOMETRY_NODE_TYPES[definition['type']][0] or node.mute:
            raise RefusalError('Geometry Nodes contain an unsupported or changed node', code='INTERNAL_ERROR')
        if definition['type'] == 'GROUP_OUTPUT' and not node.is_active_output:
            raise RefusalError('Geometry Nodes output must be the active approved output', code='INTERNAL_ERROR')
        for name, value in definition['inputs'].items():
            if not _same_socket_value(_socket(node, name).default_value, value):
                raise RefusalError('Geometry Nodes input no longer matches the approved bounded value', code='INTERNAL_ERROR')
        if definition['type'] == 'EXTRUDE_MESH' and node.mode != definition['settings']['mode']:
            raise RefusalError('Geometry Nodes extrusion mode is outside the closed contract', code='INTERNAL_ERROR')
        if definition['type'] == 'SET_SHADE_SMOOTH' and node.domain != definition['settings']['domain']:
            raise RefusalError('Geometry Nodes smooth domain is outside the closed contract', code='INTERNAL_ERROR')
    # FACE smooth sockets display "Mesh" but retain the typed "Geometry"
    # identifier. Compare resolved native identities, not translated labels.
    actual_links = sorted((link.from_socket.as_pointer(), link.to_socket.as_pointer())
                          for link in tree.links if link.is_valid and not link.is_muted)

    def socket(identifier, name, output):
        node = tree.nodes[identifier]
        if node.bl_idname in {spec[0] for spec in GEOMETRY_NODE_TYPES.values()} and \
                next(d['type'] for d in parameters['nodes'] if d['id'] == identifier) in GEOMETRY_NODE_TYPES:
            return _socket(node, name, output)
        found = node_generic.find_socket(node.outputs if output else node.inputs, name)
        if found is None:
            raise RefusalError('Geometry Nodes links no longer match their approved provenance', code='INTERNAL_ERROR')
        return found

    expected_links = sorted((socket(link['from_node'], link['from_socket'], True).as_pointer(),
                             socket(link['to_node'], link['to_socket'], False).as_pointer())
                            for link in parameters['links'])
    if actual_links != expected_links:
        raise RefusalError('Geometry Nodes links no longer match their approved provenance', code='INTERNAL_ERROR')



def _socket(node, name, output=False):
    sockets = node.outputs if output else node.inputs
    # Math exposes two sockets both named Value. The typed key is unambiguous.
    if not output and node.bl_idname == 'ShaderNodeMath' and name == 'Value_001':
        return sockets[1]
    socket = sockets.get(name)
    if socket is None:
        raise RefusalError('A catalog socket is unavailable in this Blender build: ' + name, code='UNSUPPORTED')
    return socket



def _build_nodes(tree, parameters, family):
    from . import node_generic
    types = SHADER_NODE_TYPES if family == 'SHADER' else GEOMETRY_NODE_TYPES
    nodes = {}
    for definition in parameters['nodes']:
        kind = definition['type']
        if kind not in types:
            node = node_generic.build(tree, definition, IMAGE_MARKER)
            node.name = node.label = definition['id']
            nodes[definition['id']] = node
            continue
        node = tree.nodes.new(types[kind][0])
        node.name = node.label = definition['id']
        nodes[definition['id']] = node
        settings = definition['settings']
        if kind == 'VALUE':
            node.outputs[0].default_value = settings['value']
        elif kind == 'RGB':
            node.outputs[0].default_value = settings['color']
        elif kind == 'TEX_NOISE':
            node.noise_dimensions = settings['noise_dimensions']
        elif kind == 'MAPPING':
            node.vector_type = settings['vector_type']
        elif kind == 'MATH':
            node.operation, node.use_clamp = settings['operation'], settings['use_clamp']
        elif kind == 'MIX_RGB':
            node.blend_type, node.use_clamp = settings['blend_type'], settings['use_clamp']
        elif kind == 'BUMP':
            node.invert = settings['invert']
        elif kind == 'TEX_IMAGE':
            from .action_world import resolve_image
            resolve_image(settings['image'])  # Fails early for a missing or expired image ID.
            node.interpolation, node.extension = settings['interpolation'], settings['extension']
            node.projection = settings['projection']
            # The stage never links the live image (a suspended stage would copy it into its
            # bundle); commit assigns it from this marker (program_identities._assign_images).
            node[IMAGE_MARKER] = settings['image']
        elif kind == 'EXTRUDE_MESH':
            node.mode = settings['mode']
        elif kind == 'SET_SHADE_SMOOTH':
            node.domain = settings['domain']
        for name, value in definition['inputs'].items():
            _socket(node, name).default_value = value
    kinds = {definition['id']: definition['type'] for definition in parameters['nodes']}
    for link in parameters['links']:
        if kinds[link['from_node']] not in types or kinds[link['to_node']] not in types:
            node_generic.link(tree, nodes[link['from_node']], link['from_socket'], nodes[link['to_node']], link['to_socket'])
            continue
        tree.links.new(_socket(nodes[link['from_node']], link['from_socket'], True),
                       _socket(nodes[link['to_node']], link['to_socket']))
    return nodes
