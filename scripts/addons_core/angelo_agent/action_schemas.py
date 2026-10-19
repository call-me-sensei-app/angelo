# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Closed native-action registry and explicit defaults. No execution.

Keep schema/default metadata here; action_catalog owns cross-field validation
and action_kernels dispatches the corresponding staged-only implementations."""

import math
from .action_schema_types import (
    MAX_OPERATIONS, MAX_VERTICES, MAX_EDGES, MAX_FACES, MAX_EVALUATED_VERTICES,
    MAX_SUBDIVISION_LEVELS, MAX_SUBDIVISION_RENDER_LEVELS,
    MAX_INDEX_SELECTION, MAX_NODES, MAX_LINKS, HANDLE, ALIAS, NODE_ID, INDICES,
    COLOR, ANGLES, SCALE, NULLABLE_POINTER, POINTER, number, integer, enum, boolean, text, array, vector,
    nullable, obj, stated,
)
from .action_camera_light_spec import CAMERA_LIGHT_SCHEMAS, CAMERA_LIGHT_CREATE_KINDS, CAMERA_LIGHT_CAPABILITIES
from .action_properties import (PROPERTY_SET_SCHEMA, PROPERTY_PATHS, OBJECT as _OBJECT_OWNER, SCENE as _SCENE_OWNER,
                                values_schema, VALUE as PROPERTY_VALUE)
from .action_world_spec import WORLD_CONFIGURE_SCHEMA, IMAGE_ID
from .rna_rules import PATH_PATTERN
from .generic_settings import TYPE_PATTERN as GENERIC_TYPE_PATTERN
from .action_bones_spec import BONE_SET_SCHEMA
from .particle_rules import PARTICLE_SET_SCHEMA
from .action_operator_spec import OPERATOR_RUN_SCHEMA, OPERATOR_CAPABILITIES
from .anim_spec import KEY_SCHEMAS, INTERPOLATIONS
from .anim_structure_spec import STRUCTURE_SCHEMAS
from .node_edit_spec import NODE_EDIT_SCHEMA
from .batch_spec import BATCH_SCHEMA
from .material_rules import ALSO


_create_common = {'name': text(default='Angelo Primitive'), 'alias': ALIAS,
                  'location': vector([0, 0, 0]), 'rotation': ANGLES, 'scale': SCALE}


def _object_common(name):
    return dict(_create_common, name=text(default=name))
_primitive_fields = {
    'CUBE': {'size': number(0.000001, 1000, 2)},
    'PLANE': {'size': number(0.000001, 1000, 2)},
    'UV_SPHERE': {'radius': number(0.000001, 1000, 1), 'segments': integer(3, 128, 32),
                  'rings': integer(3, 128, 16)},
    'ICO_SPHERE': {'radius': number(0.000001, 1000, 1), 'subdivisions': integer(1, 5, 2)},
    'CYLINDER': {'radius': number(0.000001, 1000, 1), 'depth': number(0.000001, 1000, 2),
                 'segments': integer(3, 128, 32), 'cap_ends': boolean(True)},
    'CONE': {'radius': number(0.000001, 1000, 1), 'radius_top': number(0, 1000, 0),
             'depth': number(0.000001, 1000, 2), 'segments': integer(3, 128, 32), 'cap_ends': boolean(True)},
    'GRID': {'size': number(0.000001, 1000, 2), 'x_segments': integer(1, 128, 10),
             'y_segments': integer(1, 128, 10)},
    'CIRCLE': {'radius': number(0.000001, 1000, 1), 'segments': integer(3, 128, 32),
               'fill': boolean(False)},
    # Blender's Add > Torus defaults. Without it a donut went to operator_run and failed.
    'TORUS': {'major_radius': number(0.000001, 1000, 1), 'minor_radius': number(0.000001, 1000, .25),
              'major_segments': integer(3, 128, 48), 'minor_segments': integer(3, 64, 12)},
}

BOOL = {'type': 'boolean'}
# keyframe_insert's frame: left out, the current frame, as Blender keys (it was frame 1, which could replace a key).
KEY_FRAME = dict(integer(-10000, 100000), description="Left out: the current frame")
# A UV kind's map: left out, the mesh's active one (a new "UVMap" when it has none), as Blender's operators use.
UV_MAP = dict(text(), description="Left out: the active UV map")
# What a modifier, constraint or Principled material keeps of what a call leaves out, as camera and light configure:
# it stays as it is, and a new one keeps Blender's own default. Their settings take no schema default: _validate
# filled each into the canonical program and the kernel wrote it over the object's own value (so a Bevel's
# width 0.2 became 0.05 when a call set only its segments; a material edit that set only Roughness turned a red metal
# grey). The kinds' descriptions and lookups carry this note (plan_schema, action_catalog, codex_reference).
SETTINGS_NOTE = ("Settings and inputs you leave out stay as they are; a new modifier, constraint or material keeps "
                 "Blender's own defaults for them.")
# Each typed modifier's settings, with their ranges and no defaults (SETTINGS_NOTE).
MODIFIER_SETTINGS = {
    'BEVEL': obj({'width': number(0.000001, 100), 'segments': integer(1, 8), 'limit_method': enum(['ANGLE', 'NONE']),
                  'angle_limit': number(0, math.pi), 'profile': number(0, 1), 'clamp_overlap': BOOL}, default={}),
    # Viewport levels are what a stage evaluates; render levels and adaptive dicing only cost at render time
    # (action_budget). adaptive: null keeps the modifier's adaptive settings, and so does a null member.
    'SUBSURF': obj({'levels': integer(0, MAX_SUBDIVISION_LEVELS), 'render_levels': integer(0, MAX_SUBDIVISION_RENDER_LEVELS),
                    'subdivision_type': enum(['CATMULL_CLARK', 'SIMPLE']),
                    'adaptive': nullable(obj({'enabled': BOOL, 'space': nullable(enum(['PIXEL', 'OBJECT'])),
                                              'pixel_size': nullable(number(.5, 1000)),
                                              'object_edge_length': nullable(number(.001, 1000))}, ['enabled']))},
                   default={}),
    'SOLIDIFY': obj({'thickness': number(-100, 100), 'offset': number(-1, 1), 'use_even_offset': BOOL, 'use_rim': BOOL},
                    default={}),
    'MIRROR': obj({'axes': array(BOOL, 3, 3), 'use_clip': BOOL, 'use_merge_vertices': BOOL,
                   'merge_threshold': number(0.000001, 1),
                   # Null preserves the origin; clear is an explicit pointer clear, not a modifier reset.
                   'mirror_object': NULLABLE_POINTER, 'clear': BOOL}, default={}),
    'BOOLEAN': obj({'operand': POINTER, 'operation': enum(['DIFFERENCE', 'UNION', 'INTERSECT']),
                    'solver': enum(['EXACT', 'FLOAT', 'MANIFOLD']), 'use_self': BOOL, 'use_hole_tolerant': BOOL},
                   ['operand']),
    # Needs the remesh build feature (octree modes) or OpenVDB (VOXEL); refused where absent.
    'REMESH': obj({'mode': enum(['VOXEL', 'SMOOTH', 'SHARP', 'BLOCKS']), 'voxel_size': number(.0001, 100),
                   'octree_depth': integer(1, 8), 'scale': number(0, .99), 'use_remove_disconnected': BOOL,
                   'use_smooth_shade': BOOL}, default={}),
    # Fixed-count arrays only: fit-to-length/curve and caps have unbounded or object-dependent output.
    'ARRAY': obj({'count': integer(1, 64), 'use_relative_offset': BOOL, 'relative_offset_displace': vector(limit=100),
                  'use_constant_offset': BOOL, 'constant_offset_displace': vector(limit=1000), 'use_merge_vertices': BOOL,
                  'merge_threshold': number(0, 1)}, default={}),
    'DECIMATE': obj({'decimate_type': enum(['COLLAPSE', 'UNSUBDIV', 'DISSOLVE']), 'ratio': number(.0001, 1),
                     'iterations': integer(0, 32), 'angle_limit': number(0, math.pi), 'use_symmetry': BOOL,
                     'symmetry_axis': enum(['X', 'Y', 'Z'])}, default={}),
    'WEIGHTED_NORMAL': obj({'mode': enum(['FACE_AREA', 'CORNER_ANGLE', 'FACE_AREA_WITH_ANGLE']), 'weight': integer(1, 100),
                            'thresh': number(0, 10), 'keep_sharp': BOOL, 'use_face_influence': BOOL}, default={}),
    'TRIANGULATE': obj({'quad_method': enum(['BEAUTY', 'FIXED', 'FIXED_ALTERNATE', 'SHORTEST_DIAGONAL',
                                             'LONGEST_DIAGONAL']),
                        'ngon_method': enum(['BEAUTY', 'CLIP']), 'min_vertices': integer(4, 255),
                        'keep_custom_normals': BOOL}, default={}),
    'SHRINKWRAP': obj({'target': POINTER, 'wrap_method': enum(['NEAREST_SURFACEPOINT', 'PROJECT', 'NEAREST_VERTEX',
                                                               'TARGET_PROJECT']),
                       'wrap_mode': enum(['ON_SURFACE', 'INSIDE', 'OUTSIDE', 'OUTSIDE_SURFACE', 'ABOVE_SURFACE']),
                       'offset': number(-100, 100)}, ['target']),
    # Uniform displacement only: a texture is another datablock with its own settings.
    'DISPLACE': obj({'strength': number(-100, 100), 'mid_level': number(0, 1), 'direction': enum(['X', 'Y', 'Z', 'NORMAL']),
                     'space': enum(['LOCAL', 'GLOBAL'])}, default={}),
    'SCREW': obj({'angle': number(-4 * math.pi, 4 * math.pi), 'steps': integer(1, 256), 'render_steps': integer(1, 256),
                  'screw_offset': number(-100, 100), 'iterations': integer(1, 16), 'axis': enum(['X', 'Y', 'Z']),
                  'use_merge_vertices': BOOL, 'merge_threshold': number(0, 1), 'use_normal_calculate': BOOL,
                  'use_normal_flip': BOOL, 'use_smooth_shade': BOOL}, default={}),
    'CURVE': obj({'object': POINTER, 'deform_axis': enum(['POS_X', 'POS_Y', 'POS_Z', 'NEG_X', 'NEG_Y', 'NEG_Z'])},
                 ['object']),
}
MODIFIER_SETTINGS = {kind: stated(settings) for kind, settings in MODIFIER_SETTINGS.items()}  # Nulls too: none filled.
# Settings that name another staged object (declared read dependencies).
MODIFIER_OPERANDS = {'BOOLEAN': 'operand', 'SHRINKWRAP': 'target', 'CURVE': 'object'}
# The modifier properties a typed setting writes when RNA names them otherwise (action_modifiers._configure_modifier;
# None writes none of its own), and the properties a type also sets, with the stated setting that makes it (None:
# every time). Every other setting is its RNA name. test_native_typed_review pins these to the kernel's assignments.
MODIFIER_RNA_NAMES = {'BEVEL': {'clamp_overlap': 'use_clamp_overlap'},
                      'MIRROR': {'axes': 'use_axis', 'use_merge_vertices': 'use_mirror_merge', 'clear': None},
                      'BOOLEAN': {'operand': 'object'}, 'SUBSURF': {'adaptive': None}}
MODIFIER_ALSO_WRITES = {'BOOLEAN': ('operand', ('operand_type',)), 'ARRAY': (None, ('fit_type',))}


def also_writes(kind, settings):
    """The properties a typed modifier's canonical ``settings`` also write: a Boolean's operand type with its operand,
    an Array's fit type (fixed count) every time."""
    trigger, names = MODIFIER_ALSO_WRITES.get(kind, (None, ()))
    return names if trigger is None or trigger in settings else ()

ANIMATED_PROPERTIES = ('location', 'rotation_euler', 'scale')

from .action_node_types import (_principled_inputs, SHADER_NODE_TYPES, GEOMETRY_NODE_TYPES,  # noqa: F401
                                _extra_inputs, _node_schema, LINK)

# A material kind naming a material its target already has edits it: for this object alone, or for every user.
MATERIAL_SHARED = enum(['REFUSE', 'ALL_USERS'], 'REFUSE')
FACE_SELECTOR = nullable(obj({'normal': nullable(vector(limit=1)), 'normal_angle': number(0, math.pi, math.pi / 6),
                               'material_index': nullable(integer(0, 31)), 'face_set': nullable(integer(0, 1000000)),
                               'area_min': number(0, 10000000000, 0), 'area_max': number(0, 10000000000, 10000000000)}))
EDGE_SELECTOR = nullable(obj({'angle_min': number(0, math.pi, 0), 'angle_max': number(0, math.pi, math.pi),
                               'boundary': enum(['ANY', 'ONLY', 'EXCLUDE'], 'ANY'),
                               'sharp': nullable({'type': 'boolean'})}))

CONSTRAINT_SETTINGS = {
    'COPY_LOCATION': obj({'axes': array(BOOL, 3, 3), 'invert': array(BOOL, 3, 3), 'use_offset': BOOL}, default={}),
    'COPY_ROTATION': obj({'axes': array(BOOL, 3, 3), 'invert': array(BOOL, 3, 3),
                          'mix_mode': enum(['REPLACE', 'ADD', 'BEFORE', 'AFTER'])}, default={}),
    'COPY_SCALE': obj({'axes': array(BOOL, 3, 3), 'use_offset': BOOL}, default={}),
    'TRACK_TO': obj({'track_axis': enum(['TRACK_X', 'TRACK_Y', 'TRACK_Z', 'TRACK_NEGATIVE_X', 'TRACK_NEGATIVE_Y',
                                         'TRACK_NEGATIVE_Z']), 'up_axis': enum(['UP_X', 'UP_Y', 'UP_Z'])}, default={}),
    'LIMIT_LOCATION': obj({'minimum': vector(), 'maximum': vector(), 'use_min': array(BOOL, 3, 3),
                           'use_max': array(BOOL, 3, 3)}, default={}),
    'LIMIT_ROTATION': obj({'minimum': vector(limit=2 * math.pi), 'maximum': vector(limit=2 * math.pi),
                           'use_limit': array(BOOL, 3, 3)}, default={}),
    'LIMIT_SCALE': obj({'minimum': vector(limit=1000), 'maximum': vector(limit=1000), 'use_min': array(BOOL, 3, 3),
                        'use_max': array(BOOL, 3, 3)}, default={}),
    'DAMPED_TRACK': obj({'track_axis': enum(['TRACK_X', 'TRACK_Y', 'TRACK_Z', 'TRACK_NEGATIVE_X', 'TRACK_NEGATIVE_Y',
                                             'TRACK_NEGATIVE_Z'])}, default={}),
    'COPY_TRANSFORMS': obj({'mix_mode': enum(['REPLACE', 'BEFORE_FULL', 'BEFORE', 'BEFORE_SPLIT', 'AFTER_FULL', 'AFTER',
                                              'AFTER_SPLIT']), 'remove_target_shear': BOOL}, default={}),
    # set_inverse is what the operation does, not a setting: it keeps the owner where it is, like Blender's Set Inverse.
    'CHILD_OF': obj({'use_location': array(BOOL, 3, 3), 'use_rotation': array(BOOL, 3, 3), 'use_scale': array(BOOL, 3, 3),
                     'set_inverse': boolean(True)}, default={}),
}
CONSTRAINT_SETTINGS = {kind: stated(settings, keep=('set_inverse',)) for kind, settings in CONSTRAINT_SETTINGS.items()}
TARGETLESS_CONSTRAINTS = frozenset({'LIMIT_LOCATION', 'LIMIT_ROTATION', 'LIMIT_SCALE'})


# Settings of a generic type: RNA paths on the modifier or constraint, values typed by RNA.
GENERIC_SETTINGS = {'type': 'object', 'properties': {}, 'additionalProperties': PROPERTY_VALUE,
                    'propertyNames': {'pattern': PATH_PATTERN}, 'maxProperties': 64, 'default': {}}


def _generic_type(typed):
    return {'type': 'string', 'minLength': 2, 'maxLength': 32, 'pattern': GENERIC_TYPE_PATTERN,
            'not': {'enum': sorted(typed)}}


def _modifier_parameters(configure=False):
    variants = [obj({('modifier' if configure else 'name'): text(default=None if configure else 'Angelo ' + kind),
                     'type': {'const': kind}, 'settings': settings},
                    ['type'] + (['modifier'] if configure else []) + (['settings'] if settings['required'] else []))
                for kind, settings in MODIFIER_SETTINGS.items()]
    # Any other modifier type of this build, settings checked against its RNA (generic_settings).
    variants.append(obj({('modifier' if configure else 'name'): text() if configure else nullable(text()),
                         'type': _generic_type(MODIFIER_SETTINGS), 'settings': GENERIC_SETTINGS},
                        ['type'] + (['modifier'] if configure else [])))
    return {'oneOf': variants, 'description': SETTINGS_NOTE}


PARAMETER_SCHEMAS = {
    'create_primitive': {'oneOf': [obj(dict(_create_common, primitive={'const': kind}, **fields), ['primitive'])
                                  for kind, fields in _primitive_fields.items()]},
    'transform': obj({'mode': enum(['SET', 'DELTA'], 'SET'), 'location': nullable(vector()),
                       'rotation': nullable(vector(limit=2 * math.pi)), 'scale': nullable(vector(limit=1000))}),
    'mesh_extrude': obj({'face_indices': INDICES, 'translation': vector(),
                         'use_normal_flip': boolean(False)}, ['face_indices', 'translation']),
    'mesh_inset': obj({'face_indices': INDICES, 'thickness': number(.000001, 100, .1),
                       'depth': number(-100, 100, 0), 'individual': boolean(False),
                       'use_even_offset': boolean(True)}, ['face_indices']),
    'mesh_bevel': obj({'edge_indices': INDICES, 'offset': number(.000001, 100, .05),
                       'segments': integer(1, 8, 1), 'profile': number(0, 1, .5),
                       'clamp_overlap': boolean(True)}, ['edge_indices']),
    'mesh_merge_by_distance': obj({'vertex_indices': INDICES, 'distance': number(.000001, 1, .0001)}, ['vertex_indices']),
    'mesh_subdivide': obj({'face_indices': INDICES, 'cuts': integer(1, 6, 1), 'smoothness': number(0, 1, 0)},
                          ['face_indices']),
    'mesh_delete': obj({'face_indices': INDICES,
                        'mode': enum(['FACES', 'FACES_ONLY', 'FACES_KEEP_BOUNDARY'], 'FACES')}, ['face_indices']),
    'mesh_recalculate_normals': obj({'face_indices': dict(INDICES, minItems=0, default=[]),
                                     'topology_digest': nullable(text(64, pattern=r'^[a-f0-9]{64}$'))}),
    # The whole mesh bowed along one of its axes, ends and length kept (BEND_NOTE; action_mesh._bend). An arched deck
    # or rail, a sagging span or a bench curved in plan in one program: in recorded arched-footbridge one-shots
    # GPT-6.1-Sol wrote the arch as a reviewed script every time, five model requests against one for the flat
    # footbridge (doc/angelo/performance.rst, "One-shot rounds").
    'mesh_bend': obj({'rise': number(-1000, 1000), 'axis': nullable(enum(['X', 'Y', 'Z'])),
                      'up': enum(['X', 'Y', 'Z'], 'Z'), 'segments': integer(1, 64, 16)}, ['rise']),
    'modifier_add': _modifier_parameters(),
    'modifier_configure': _modifier_parameters(True),
    'material_principled': obj({'name': text(default='Angelo Material'), 'slot': integer(-1, 31, -1),
                                'face_indices': dict(INDICES, minItems=0, default=[]),
                                # Only the inputs a call states are written (SETTINGS_NOTE).
                                'inputs': stated(obj(_principled_inputs, default={})), 'shared': MATERIAL_SHARED,
                                'also': ALSO}, description=SETTINGS_NOTE),
    'shader_nodes': obj({'name': text(default='Angelo Shader'), 'slot': integer(-1, 31, -1),
                         'face_indices': dict(INDICES, minItems=0, default=[]),
                         'nodes': array(_node_schema(SHADER_NODE_TYPES, 'SHADER'), 2, MAX_NODES),
                         'links': array(LINK, 1, MAX_LINKS), 'shared': MATERIAL_SHARED, 'also': ALSO},
                        ['nodes', 'links']),
    # An existing material by its exact name: on `source` (an object that has it), else on the target or
    # stated earlier in this program. copy_as gives the target an independent copy under that name.
    # also (the three material kinds): more objects that get the same material (material_rules).
    'material_assign': obj({'material': text(), 'source': NULLABLE_POINTER, 'slot': integer(-1, 31, -1),
                            'face_indices': dict(INDICES, minItems=0, default=[]), 'copy_as': nullable(text()),
                            'also': ALSO}, ['material']),
    'geometry_nodes': obj({'name': text(default='Angelo Geometry'),
                           'nodes': array(_node_schema(GEOMETRY_NODE_TYPES, 'GEOMETRY'), 2, 16),
                           'links': array(LINK, 1, 32)}, ['nodes', 'links']),
    'uv_project': obj({'method': enum(['PLANAR', 'CUBE', 'SPHERICAL'], 'CUBE'),
                       'axis': enum(['X', 'Y', 'Z'], 'Z'), 'uv_map': UV_MAP,
                       'scale': number(.000001, 1000, 1),
                       'face_indices': dict(INDICES, minItems=0, default=[])}),
    'uv_unwrap': obj({'method': enum(['ANGLE_BASED', 'CONFORMAL'], 'ANGLE_BASED'),
                      'margin': number(0, .5, .001), 'uv_map': UV_MAP,
                      'margin_method': enum(['SCALED', 'ADD', 'FRACTION'], 'SCALED'),
                      'fill_holes': boolean(True), 'correct_aspect': boolean(False),
                      'use_subsurf': {'const': False, 'default': False},
                      'use_original_bounds': {'const': False, 'default': False},
                      'face_indices': dict(INDICES, minItems=0, default=[]),
                      'seam_edge_indices': dict(INDICES, minItems=0, default=[])}),
    'uv_smart_project': obj({'uv_map': UV_MAP, 'angle_limit': number(math.radians(1), math.radians(89),
                                                                                     math.radians(66)),
                             'island_margin': number(0, 1, 0), 'area_weight': number(0, 1, 0),
                             'correct_aspect': boolean(True), 'scale_to_bounds': boolean(False),
                             'face_indices': dict(INDICES, minItems=0, default=[])}),
    'uv_pack': obj({'uv_map': UV_MAP, 'margin': number(0, 1, .001), 'rotate': boolean(True),
                    'shape_method': enum(['CONCAVE', 'CONVEX', 'AABB'], 'CONCAVE'),
                    'margin_method': enum(['SCALED', 'ADD', 'FRACTION'], 'SCALED'),
                    'face_indices': dict(INDICES, minItems=0, default=[])}),
    'parent_set': obj({'parent': NULLABLE_POINTER, 'keep_transform': boolean(True)}, ['parent']),
    # Influence and spaces, like the settings, keep Blender's defaults for a new constraint unless stated (SETTINGS_NOTE).
    'constraint_add': {'oneOf': [obj({'type': {'const': kind}, 'name': text(default='Angelo ' + kind),
                                     'target': NULLABLE_POINTER, 'influence': number(0, 1),
                                     'owner_space': enum(['WORLD', 'LOCAL']),
                                     'target_space': enum(['WORLD', 'LOCAL']), 'settings': settings}, ['type'])
                                   for kind, settings in CONSTRAINT_SETTINGS.items()] + [
        # Any other constraint type: settings by RNA path (object pointers {"object": handle}).
        obj({'type': _generic_type(CONSTRAINT_SETTINGS), 'name': nullable(text()), 'target': NULLABLE_POINTER,
             'influence': number(0, 1), 'settings': GENERIC_SETTINGS}, ['type'])], 'description': SETTINGS_NOTE},
    # Settings of an existing constraint of any type, by its exact name.
    'constraint_configure': obj({'constraint': text(), 'influence': nullable(number(0, 1)),
                                 'settings': GENERIC_SETTINGS}, ['constraint']),
    # A key's value is bounded as its channel is written elsewhere (a scale as a transform's), but a rotation
    # key may spin past 2 pi (several turns).
    # interpolation left out: a key already at the frame keeps its own, a new key takes Blender's preference.
    'keyframe_insert': {'oneOf': [obj({'property': channel, 'value': value, 'frame': KEY_FRAME,
                                       'interpolation': enum(['BEZIER', 'LINEAR', 'CONSTANT'])},
                                      ['property', 'value'])
                                  for channel, value in ((enum(['location', 'rotation_euler']), vector()),
                                                         ({'const': 'scale'}, vector(limit=1000)))]},
    'armature_create': obj({'name': text(default='Angelo Armature'), 'alias': ALIAS,
                            'location': vector([0, 0, 0]), 'rotation': ANGLES, 'scale': SCALE,
                            'bones': array(obj({'name': text(), 'head': vector(), 'tail': vector(),
                                                 'roll': number(-2 * math.pi, 2 * math.pi, 0),
                                                 'parent': nullable(text()), 'connected': boolean(False)},
                                                ['name', 'head', 'tail']), 1, 32)}, ['bones']),
    'armature_bind': obj({'armature': POINTER, 'name': text(default='Angelo Armature Deform'),
                          'preserve_volume': boolean(False),
                          'groups': array(obj({'bone': text(), 'weights': array(obj({'vertex': integer(0, MAX_VERTICES - 1),
                                                                                    'weight': number(.000001, 1)},
                                                                                   ['vertex', 'weight']), 1, 4096)},
                                               ['bone', 'weights']), 1, 32)}, ['armature', 'groups']),
    **CAMERA_LIGHT_SCHEMAS,
    'property_set': PROPERTY_SET_SCHEMA,
    'world_configure': WORLD_CONFIGURE_SCHEMA,
    # Stack edits by modifier name; apply is first-in-stack only (Blender's own exact case).
    'modifier_remove': obj({'modifier': text()}, ['modifier']),
    'modifier_move': obj({'modifier': text(), 'index': integer(0, 31)}, ['modifier', 'index']),
    'modifier_apply': obj({'modifier': text()}, ['modifier']),
    # Object management. Names are bounded labels; Blender suffixes taken names, so a taken name fails.
    'object_rename': obj({'name': text()}, ['name']),
    'object_delete': obj({}, default={}),
    'object_duplicate': obj({'source': POINTER, 'alias': text(32, pattern=r'^[A-Za-z][A-Za-z0-9_]{0,31}$'),
                             'name': nullable(text()), 'linked': boolean(False), 'location': nullable(vector())},
                            ['source', 'alias']),
    # View-layer eye of an object, or (scene target + collection) a collection's view-layer and own visibility.
    'visibility_set': obj({'hidden': nullable({'type': 'boolean'}), 'collection': nullable(text()),
                           **{field: nullable({'type': 'boolean'})
                              for field in ('exclude', 'hide_viewport', 'hide_render', 'hide_select')}}),
    # One spline through explicit local points replaces a curve's splines.
    'curve_points': obj({'points': array(vector(), 2, 256), 'spline_type': enum(['POLY', 'BEZIER', 'NURBS'], 'BEZIER'),
                         'cyclic': boolean(False), 'handle_type': enum(['AUTO', 'VECTOR'], 'AUTO'),
                         'dimensions': nullable(enum(['2D', '3D']))}, ['points']),
    # A new mesh object from a curve or text object (Convert to Mesh, keeping or deleting the source).
    'object_convert': obj({'source': POINTER, 'alias': text(32, pattern=r'^[A-Za-z][A-Za-z0-9_]{0,31}$'),
                           'name': nullable(text()), 'keep_original': boolean(True)}, ['source', 'alias']),
    # Join merges declared source meshes into the target and deletes them; separate splits the target.
    'object_join': obj({'sources': dict(array(POINTER, 1, 16), uniqueItems=True)}, ['sources']),
    'mesh_separate': obj({'mode': enum(['LOOSE', 'MATERIAL', 'SELECTION'], 'LOOSE'),
                          'face_indices': dict(INDICES, minItems=0, default=[])}),
    'transform_apply': obj({'location': boolean(False), 'rotation': boolean(False), 'scale': boolean(False)}),
    'origin_set': obj({'mode': enum(['BOUNDS_CENTER', 'MEDIAN', 'POINT'], 'BOUNDS_CENTER'),
                       'point': nullable(vector())}),
    # Collections of the bound scene by exact name; MOVE leaves only the named collection.
    'collection_link': obj({'collection': text(), 'mode': enum(['LINK', 'MOVE', 'UNLINK'], 'LINK'),
                            'create': boolean(False)}, ['collection']),
    # Animation edits through the object's own Action slot (channelbag), never Action.fcurves.
    # property_set's allow-listed numeric/boolean object and data settings, set and keyed at one frame.
    'property_keyframe': obj({'values': values_schema(), 'frame': integer(-10000, 100000),
                              'interpolation': nullable(enum(INTERPOLATIONS))}, ['values', 'frame']),
    'keyframe_delete': obj({'property': enum(ANIMATED_PROPERTIES), 'frame': integer(-10000, 100000),
                            'index': integer(-1, 2, -1)}, ['property', 'frame']),
    # Keys of any channel of the object's slot (anim_spec), and the Action, NLA and marker kinds.
    **KEY_SCHEMAS, **STRUCTURE_SCHEMAS,
    'shape_key_add': obj({'name': text(), 'value': number(0, 1, 0), 'slider_min': number(-10, 10, 0),
                          'slider_max': number(-10, 10, 1)}, ['name']),
    'shape_key_value': obj({'name': text(), 'value': number(-10, 10), 'frame': nullable(integer(-10000, 100000))},
                           ['name', 'value']),
    'nla_push_down': obj({'track': nullable(text())}),
    # Non-mesh objects: empties, text and simple Bezier curves (shaped later with property_set).
    'create_object': {'oneOf': [
        obj(dict(_object_common('Angelo Empty'), type={'const': 'EMPTY'},
                 display=enum(['PLAIN_AXES', 'ARROWS', 'SINGLE_ARROW', 'CIRCLE', 'CUBE', 'SPHERE', 'CONE'], 'PLAIN_AXES'),
                 display_size=number(.0001, 1000, 1)), ['type']),
        obj(dict(_object_common('Angelo Text'), type={'const': 'TEXT'}, body=text(1024, default='Text'),
                 size=number(.0001, 10000, 1), extrude=number(0, 1000, 0),
                 align_x=enum(['LEFT', 'CENTER', 'RIGHT', 'JUSTIFY', 'FLUSH'], 'LEFT')), ['type']),
        obj(dict(_object_common('Angelo Curve'), type={'const': 'CURVE'}, shape=enum(['BEZIER', 'CIRCLE'], 'BEZIER'),
                 radius=number(.0001, 1000, 1), bevel_depth=number(0, 1000, 0)), ['type']),
        # A lattice cage for the LATTICE modifier; its other settings are property_set data paths.
        obj(dict(_object_common('Angelo Lattice'), type={'const': 'LATTICE'},
                 points=array(integer(1, 16), 3, 3, [2, 2, 2])), ['type']),
        # A Grease Pencil object with one layer and a blank frame; operator_run edits its layers.
        obj(dict(_object_common('Angelo Grease Pencil'), type={'const': 'GREASEPENCIL'}, layer=text(64, default='Layer')),
            ['type'])]},
    # One bone of an armature: rest geometry (staged Edit mode), Bone and PoseBone settings by RNA path, pose.
    'bone_set': BONE_SET_SCHEMA,
    # ParticleSettings of one PARTICLE_SYSTEM modifier by RNA path, on its private staged copy.
    'particle_set': PARTICLE_SET_SCHEMA,
    # One allow-listed Blender C operator on the staged target (action_operator_spec).
    'operator_run': OPERATOR_RUN_SCHEMA,
    # Targeted edits of one existing material, Geometry Nodes or World tree (node_edit_spec).
    'node_edit': NODE_EDIT_SCHEMA,
    # One unit of objects repeated as a pattern: one operation, bounded copies (batch_spec).
    'batch': BATCH_SCHEMA,
}

INDEXED_KINDS = frozenset({'mesh_extrude', 'mesh_inset', 'mesh_bevel', 'mesh_merge_by_distance', 'mesh_subdivide', 'mesh_delete',
                           'mesh_recalculate_normals', 'material_principled', 'shader_nodes', 'material_assign', 'uv_project',
                           'uv_unwrap', 'uv_smart_project', 'uv_pack', 'armature_bind', 'mesh_separate'})
TOPOLOGY_KINDS = frozenset({'mesh_extrude', 'mesh_inset', 'mesh_bevel', 'mesh_merge_by_distance', 'mesh_subdivide',
                            'mesh_delete', 'mesh_bend'})
# What mesh_bend's schema cannot say (TypeScript and JSON Schema drop descriptions): one line for the Codex program
# reference and the kind's lookup (action_catalog.catalog).
BEND_NOTE = ("mesh_bend: bows the whole mesh along axis (default its longest): each vertex moves along up by "
             "rise*(1-t*t), t from -1 to 1 end to end, so the ends and length stay and the middle moves rise metres "
             "(negative sags). Object axes and scale; it first cuts segments even slices.")
for _kind in INDEXED_KINDS:
    _parameters = PARAMETER_SCHEMAS[_kind]
    _parameters['properties']['index_scope'] = enum(['INITIAL', 'CURRENT'], 'INITIAL')
    _parameters['properties']['topology_step'] = integer(0, MAX_OPERATIONS, 0)
    if 'face_indices' in _parameters['properties']:
        _parameters['properties']['face_selector'] = FACE_SELECTOR
        if _kind in {'mesh_extrude', 'mesh_inset', 'mesh_subdivide', 'mesh_delete'}:
            _parameters['properties']['face_indices'] = dict(INDICES, minItems=0, default=[])
            _parameters['required'].remove('face_indices')
    if _kind == 'mesh_bevel':
        _parameters['properties']['edge_selector'] = EDGE_SELECTOR
        _parameters['properties']['edge_indices'] = dict(INDICES, minItems=0, default=[])
        _parameters['required'].remove('edge_indices')

CREATE_KINDS = frozenset({'create_primitive', 'armature_create', 'object_duplicate', 'create_object',
                          'object_convert', 'batch'}) | CAMERA_LIGHT_CREATE_KINDS
# Public values are parameter schemas: program_spec wraps these in operations.
ACTION_SCHEMAS = PARAMETER_SCHEMAS
OPERATION_SCHEMAS = {
    kind: obj({'kind': {'const': kind}, 'target': {'type': 'null'} if kind in CREATE_KINDS else HANDLE,
               'parameters': schema}, ['kind', 'target', 'parameters'])
    for kind, schema in PARAMETER_SCHEMAS.items()
}

_mutations = {
    'create_primitive': ('object', 'mesh'), 'armature_create': ('object', 'armature'),
    'transform': ('object',), 'parent_set': ('object',), 'constraint_add': ('object',),
    'constraint_configure': ('object',),
    'keyframe_insert': ('object', 'animation'), 'modifier_add': ('object', 'modifiers'),
    'modifier_configure': ('object', 'modifiers'), 'material_principled': ('object', 'mesh', 'materials'),
    'material_assign': ('object', 'mesh', 'materials'),
    'shader_nodes': ('object', 'mesh', 'materials', 'node_groups'),
    'geometry_nodes': ('object', 'modifiers', 'node_groups'),
    'armature_bind': ('object', 'mesh', 'modifiers', 'vertex_groups'),
    # Configure kinds list every family they may change; resource_dependencies
    # narrows each canonical operation to the families it actually writes.
    'camera_create': ('object', 'camera'), 'camera_configure': ('object', 'transforms', 'camera'),
    'light_create': ('object', 'light'), 'light_configure': ('object', 'transforms', 'light'),
    # Object settings, camera or curve data, or scene settings; narrowed per operation.
    'property_set': ('object', 'properties', 'data', 'scene'),
    'world_configure': ('world',),
    'modifier_remove': ('object', 'modifiers'), 'modifier_move': ('object', 'modifiers'),
    'modifier_apply': ('object', 'mesh', 'modifiers'),
    'object_rename': ('object',), 'object_delete': ('object',), 'object_duplicate': ('object',),
    'curve_points': ('object', 'curve'), 'object_convert': ('object',),
    'object_join': ('object', 'mesh'), 'visibility_set': ('object', 'visibility', 'scene'),
    'transform_apply': ('object', 'mesh', 'transforms'), 'origin_set': ('object', 'mesh', 'transforms'),
    'collection_link': ('object', 'collections'),
    'keyframe_delete': ('object', 'animation'), 'keyframe_configure': ('object', 'animation', 'data'),
    'keyframe_edit': ('object', 'animation', 'data'), 'fcurve_modifier': ('object', 'animation', 'data'),
    'keyframe_copy': ('object', 'animation'), 'action_set': ('object', 'animation', 'nla_stack'),
    'nla_edit': ('object', 'nla_stack'), 'marker_edit': ('scene', 'markers'),
    'property_keyframe': ('object', 'properties', 'animation', 'data'),
    'shape_key_add': ('object', 'mesh'), 'shape_key_value': ('object', 'mesh'),
    'nla_push_down': ('object', 'animation', 'nla'),
    'create_object': ('object', 'curve'),
    'bone_set': ('object', 'armature', 'pose'),
    'particle_set': ('object', 'particles'),
    'operator_run': ('object', 'data'),
    'node_edit': ('object', 'node_trees', 'world_nodes'),
    'batch': ('object', 'transforms'),
}
for _kind in PARAMETER_SCHEMAS:
    if _kind.startswith('mesh_') or _kind.startswith('uv_'):
        _mutations[_kind] = ('object', 'mesh')

CAPABILITIES = {
    'version': 1, 'max_operations': MAX_OPERATIONS, 'kinds': list(ACTION_SCHEMAS),
    'primitives': list(_primitive_fields), 'modifiers': list(MODIFIER_SETTINGS),
    'constraints': list(CONSTRAINT_SETTINGS), 'shader_nodes': list(SHADER_NODE_TYPES),
    'geometry_nodes': list(GEOMETRY_NODE_TYPES), 'animation_properties': ['location', 'rotation_euler', 'scale'],
    'base_mesh_limits': {'vertices': MAX_VERTICES, 'edges': MAX_EDGES, 'faces': MAX_FACES},
    'evaluated_vertex_limit': MAX_EVALUATED_VERTICES,
    'index_semantics': 'INITIAL binds to the declared starting object revision and topology generation 0; '
                       'CURRENT must name the exact topology_step after preceding base topology edits. '
                       'Each extrude/inset/bevel/merge increments the generation. Selectors recompute on the current base mesh. '
                       'A planned post-merge normal selection also binds exact base topology_digest before using CURRENT IDs.',
    'face_selectors': ['normal_and_angle', 'material_index', 'face_set', 'area_range'],
    'edge_selectors': ['dihedral_angle_range', 'boundary', 'sharp'],
    'runtime_requirements': {'SUBSURF_nonzero_levels': 'Blender OpenSubdiv build feature',
                             'SUBDIVIDE_MESH_nonzero_level': 'Blender OpenSubdiv build feature'},
    'camera_light': CAMERA_LIGHT_CAPABILITIES,
    'property_set': {'owners': [_OBJECT_OWNER, _SCENE_OWNER], 'paths': 'generic RNA settings paths; '
                     'angelo_blender_api domain WRITABLE lists a type\'s writable settings',
                     'scene_binding': 'Bind the scene handle and revision from angelo_scene_settings'},
    'world': {'target': 'scene handle', 'graph': 'World Output <- Background <- optional Environment Texture',
              'images': 'already loaded image datablocks by angelo_scene_settings ID; no file paths',
              'shared_world': 'only the bound scene receives the edited copy'},
    'modifier_stack': {'removable': 'types a program can stage, except MULTIRES and bound deform caches',
                       'apply': 'first modifier of a mesh without shape keys'},
    'object_management': {'delete': 'refused while other objects, the scene camera or a non-scene user depend on it',
                          'duplicate': 'into the source collections; linked shares live data; animated sources refused',
                          'apply_and_origin': 'meshes without children', 'collections': 'by exact name in the bound scene'},
    'bones': {'bone_set': 'one existing bone of an armature target, by exact name; head/tail in armature space, '
                          'pose location in bone space, rotation XYZ Euler kept in the bone\'s rotation mode',
              'settings': 'Bone (settings) and PoseBone (pose_settings) paths: angelo_blender_api WRITABLE'},
    'operator_run': OPERATOR_CAPABILITIES,
    'animation': {'channels': 'F-Curve data paths of the object\'s own Action slot; data.<path> for its data\'s; '
                              'null: every channel', 'actions': 'existing Actions by the dat:...:ACTION:... ID an '
                              'object read shows; a shared Action is refused until action_set SINGLE_USER',
                  'markers': 'marker_edit targets the scene handle; markers by exact unique name'},
    'node_edit': {'trees': 'material (live name on the target), modifier (Geometry Nodes catalog group) or world true '
                           '(scene handle); group edits inside a group node of a material',
                  'nodes': 'by exact name, as observed (angelo_scene_object MATERIALS or MODIFIERS, angelo_scene_settings '
                           'world_graph)', 'shared': 'a tree with other users is refused unless shared ISOLATE'},
    'unsupported': ['arbitrary_python', 'arbitrary_rna', 'join_separate', 'export',
                    'external_assets', 'drivers', 'simulation', 'automatic_weights', 'instance_nodes',
                    'custom_world_node_graphs', 'panoramic_or_custom_cameras', 'custom_light_node_graphs'],
}
