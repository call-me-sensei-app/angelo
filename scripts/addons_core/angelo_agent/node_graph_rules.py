# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Structural rules of a typed node graph (shader_nodes, geometry_nodes), before anything is built.

Unique node IDs, exactly one typed output, links between nodes of this graph on
sockets the capability map allows (generic endpoints are checked against the
built nodes instead), single links per input except Join Geometry, no cycles.
Moved from ``action_catalog`` unchanged.
"""

from .action_schemas import GEOMETRY_NODE_TYPES, SHADER_NODE_TYPES, _extra_inputs


def validate_graph(params, family):
    from . import node_generic
    types = SHADER_NODE_TYPES if family == 'SHADER' else GEOMETRY_NODE_TYPES
    nodes = {node['id']: node for node in params['nodes']}
    if len(nodes) != len(params['nodes']):
        raise ValueError('Node IDs must be unique')
    for node in nodes.values():
        if node['type'] not in types:
            node_generic.screen_definition(node, family)
    output_type = {'OUTPUT_MATERIAL', 'ShaderNodeOutputMaterial'} if family == 'SHADER' else {'GROUP_OUTPUT', 'NodeGroupOutput'}
    outputs = [node for node in nodes.values() if node['type'] in output_type]
    if len(outputs) != 1:
        raise ValueError('A graph requires exactly one typed output node')
    used = set()
    adjacency = {identifier: [] for identifier in nodes}
    for link in params['links']:
        source, destination = nodes.get(link['from_node']), nodes.get(link['to_node'])
        if source is None or destination is None:
            raise ValueError('Links must reference nodes in this graph')
        if source['type'] not in types or destination['type'] not in types:
            # A generic endpoint: socket names and types are checked against the built nodes.
            used.add((link['to_node'], link['to_socket']))
            adjacency[link['from_node']].append(link['to_node'])
            continue
        out_names = types[source['type']][3]
        in_names = list(types[destination['type']][1]) + _extra_inputs[family].get(destination['type'], [])
        if link['from_socket'] not in out_names or link['to_socket'] not in in_names:
            raise ValueError('Link socket is outside the typed capability map')
        source_geometry = link['from_socket'] in {'Mesh', 'Geometry'}
        destination_geometry = link['to_socket'] in {'Mesh', 'Geometry'}
        if family == 'GEOMETRY' and source_geometry != destination_geometry:
            raise ValueError('Geometry sockets require geometry links')
        if family == 'SHADER' and ((link['from_socket'] == 'BSDF') != (link['to_socket'] == 'Surface')):
            raise ValueError('Shader Surface requires a BSDF link')
        key = (link['to_node'], link['to_socket'])
        multi = family == 'GEOMETRY' and destination['type'] == 'JOIN_GEOMETRY'
        if key in used and not multi:
            raise ValueError('Only Join Geometry permits multiple links to one socket')
        used.add(key)
        adjacency[link['from_node']].append(link['to_node'])
    if (outputs[0]['id'], 'Surface' if family == 'SHADER' else 'Geometry') not in used:
        raise ValueError('Graph output must be connected')
    visiting, done = set(), set()

    def visit(identifier):
        if identifier in visiting:
            raise ValueError('Node graphs must be acyclic')
        if identifier in done:
            return
        visiting.add(identifier)
        for destination in adjacency[identifier]:
            visit(destination)
        visiting.remove(identifier)
        done.add(identifier)

    for identifier in nodes:
        visit(identifier)
