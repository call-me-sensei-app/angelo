# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Typed shader and geometry node catalogs and the node item schema (pure Python, no bpy).

Typed keys (``TEX_NOISE``, ``MESH_CUBE``...) keep their closed sockets and
settings; any other registered node type is named by its ``bl_idname`` and
checked against RNA by ``node_generic``.
"""

import math

from .action_schema_types import (number, integer, enum, text, array, obj, vector, boolean, nullable,  # noqa: F401
                                  COLOR, ANGLES, SCALE, UNIT, NODE_ID, HANDLE, TYPED_NODE_ALTERNATIVES)
from .action_world_spec import IMAGE_ID
from .node_generic import DENIED_NODES, TYPE_PATTERN as NODE_TYPE_PATTERN, SOCKET_PATTERN
from .rna_rules import PATH_PATTERN


_principled_inputs = {
    'Base Color': COLOR, 'Metallic': number(0, 1, 0), 'Roughness': number(0, 1, .5),
    'IOR': number(1, 4, 1.5), 'Alpha': number(0, 1, 1),
    'Emission Color': array(number(0, 1), 4, 4, [0, 0, 0, 1]),
    'Emission Strength': number(0, 100, 0),
}

# Socket names are closed too; no filesystem/script/group nodes. An image
# texture names a loaded image datablock by its ID (angelo_scene_settings).
SHADER_NODE_TYPES = {
    'VALUE': ('ShaderNodeValue', {}, {}, ['Value']),
    'RGB': ('ShaderNodeRGB', {}, {}, ['Color']),
    'TEX_COORD': ('ShaderNodeTexCoord', {}, {}, ['Generated', 'Normal', 'UV', 'Object']),
    'TEX_NOISE': ('ShaderNodeTexNoise', {'Vector': vector([0, 0, 0]), 'Scale': number(.000001, 1000, 5),
                                      'Detail': number(0, 8, 2), 'Roughness': number(0, 1, .5)},
                  {'noise_dimensions': enum(['3D'], '3D')}, ['Fac', 'Color']),
    'MAPPING': ('ShaderNodeMapping', {'Vector': vector([0, 0, 0]), 'Location': vector([0, 0, 0]),
                                     'Rotation': ANGLES, 'Scale': SCALE},
                {'vector_type': enum(['POINT', 'TEXTURE', 'VECTOR', 'NORMAL'], 'POINT')}, ['Vector']),
    'MATH': ('ShaderNodeMath', {'Value': number(-10000, 10000, 0), 'Value_001': number(-10000, 10000, 0)},
             {'operation': enum(['ADD', 'SUBTRACT', 'MULTIPLY', 'DIVIDE', 'MINIMUM', 'MAXIMUM'], 'ADD'),
              'use_clamp': boolean(False)}, ['Value']),
    'MIX_RGB': ('ShaderNodeMixRGB', {'Fac': number(0, 1, .5), 'Color1': COLOR, 'Color2': COLOR},
                {'blend_type': enum(['MIX', 'MULTIPLY', 'ADD', 'SUBTRACT'], 'MIX'),
                 'use_clamp': boolean(False)}, ['Color']),
    'BUMP': ('ShaderNodeBump', {'Strength': number(0, 1, .1), 'Distance': number(0, 10, .1),
                               'Height': number(-1000, 1000, 0), 'Normal': vector([0, 0, 0], 1)},
             {'invert': boolean(False)}, ['Normal']),
    'TEX_IMAGE': ('ShaderNodeTexImage', {}, {'image': IMAGE_ID,
                                             'interpolation': enum(['Linear', 'Closest', 'Cubic', 'Smart'], 'Linear'),
                                             'extension': enum(['REPEAT', 'EXTEND', 'CLIP', 'MIRROR'], 'REPEAT'),
                                             'projection': enum(['FLAT', 'BOX'], 'FLAT')}, ['Color', 'Alpha']),
    'BSDF_PRINCIPLED': ('ShaderNodeBsdfPrincipled', _principled_inputs, {}, ['BSDF']),
    'OUTPUT_MATERIAL': ('ShaderNodeOutputMaterial', {}, {}, []),
}

GEOMETRY_NODE_TYPES = {
    'GROUP_INPUT': ('NodeGroupInput', {}, {}, ['Geometry']),
    'GROUP_OUTPUT': ('NodeGroupOutput', {}, {}, []),
    'TRANSFORM_GEOMETRY': ('GeometryNodeTransform', {'Translation': vector([0, 0, 0]), 'Rotation': ANGLES,
                                                   'Scale': SCALE}, {}, ['Geometry']),
    'SET_POSITION': ('GeometryNodeSetPosition', {'Offset': vector([0, 0, 0]), 'Selection': boolean(True)},
                     {}, ['Geometry']),
    'SUBDIVIDE_MESH': ('GeometryNodeSubdivideMesh', {'Level': integer(0, 2, 1)}, {}, ['Mesh']),
    'EXTRUDE_MESH': ('GeometryNodeExtrudeMesh', {'Selection': boolean(True), 'Offset': vector([0, 0, 1]),
                                              'Offset Scale': number(-100, 100, 1), 'Individual': boolean(False)},
                    {'mode': enum(['FACES'], 'FACES')}, ['Mesh', 'Top', 'Side']),
    'MESH_CUBE': ('GeometryNodeMeshCube', {'Size': array(number(.000001, 1000), 3, 3, [1, 1, 1]),
                                         'Vertices X': integer(2, 32, 2), 'Vertices Y': integer(2, 32, 2),
                                         'Vertices Z': integer(2, 32, 2)}, {}, ['Mesh']),
    'MESH_GRID': ('GeometryNodeMeshGrid', {'Size X': number(.000001, 1000, 1),
                                         'Size Y': number(.000001, 1000, 1),
                                         'Vertices X': integer(2, 128, 10), 'Vertices Y': integer(2, 128, 10)},
                  {}, ['Mesh']),
    'JOIN_GEOMETRY': ('GeometryNodeJoinGeometry', {}, {}, ['Geometry']),
    'SET_SHADE_SMOOTH': ('GeometryNodeSetShadeSmooth', {'Selection': boolean(True), 'Shade Smooth': boolean(True)},
                        {'domain': enum(['FACE'], 'FACE')}, ['Geometry']),
}

# Each typed key's own Blender node, named when one of its narrow settings or inputs is refused
# (action_schema_types.typed_node_alternative); a node the generic route denies is never offered.
TYPED_NODE_ALTERNATIVES.update({kind: spec[0] for types in (SHADER_NODE_TYPES, GEOMETRY_NODE_TYPES)
                                for kind, spec in types.items() if spec[0] not in DENIED_NODES})

_extra_inputs = {
    'SHADER': {'OUTPUT_MATERIAL': ['Surface'], 'BSDF_PRINCIPLED': ['Normal']},
    'GEOMETRY': {'GROUP_OUTPUT': ['Geometry'], 'TRANSFORM_GEOMETRY': ['Geometry'], 'SET_POSITION': ['Geometry'],
                 'SUBDIVIDE_MESH': ['Mesh'], 'EXTRUDE_MESH': ['Mesh'], 'JOIN_GEOMETRY': ['Geometry'],
                 'SET_SHADE_SMOOTH': ['Geometry']},
}


def _node_schema(types, family):
    options = []
    for kind, (_rna, inputs, settings, outputs) in types.items():
        config = dict(settings)
        if kind == 'VALUE':
            config['value'] = number(-10000, 10000, 0)
        elif kind == 'RGB':
            config['color'] = COLOR
        # A setting without a default (an image texture's image) makes the settings required.
        required = [name for name, schema in config.items() if 'default' not in schema]
        settings_schema = obj(config, required) if required else obj(config, default={})
        options.append(obj({'id': NODE_ID, 'type': {'const': kind}, 'inputs': obj(inputs, default={}),
                            'settings': settings_schema}, ['id', 'type'] + ['settings'] * bool(required)))
    # Any other registered node type by bl_idname (node_generic): RNA-checked settings and input defaults.
    from .action_properties import SCALAR_VALUE  # Node settings take no object (node_generic).
    open_map = {'type': 'object', 'properties': {}, 'additionalProperties': SCALAR_VALUE, 'maxProperties': 32, 'default': {}}
    options.append(obj({'id': NODE_ID, 'type': text(64, pattern=NODE_TYPE_PATTERN),
                        'inputs': dict(open_map, propertyNames={'pattern': SOCKET_PATTERN}),
                        'settings': dict(open_map, propertyNames={'pattern': PATH_PATTERN})}, ['id', 'type']))
    return {'oneOf': options}


LINK = obj({'from_node': NODE_ID, 'from_socket': text(), 'to_node': NODE_ID, 'to_socket': text()},
           ['from_node', 'from_socket', 'to_node', 'to_socket'])
