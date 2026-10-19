# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Closed camera/light action schemas, explicit type defaults and aim math.

Pure Python (no bpy). action_schemas registers these parameter schemas,
action_catalog calls ``canonical_camera_light`` for cross-field validation,
action_camera_light owns the staged kernels and validation_camera_light the
read-only predicates. The active scene camera and World settings are scene- or
file-level state outside object staging and deliberately absent here; an
``orbit`` placement is measured from the scene camera's position, which the
host reads and program_orbits resolves into a location before approval.

Units: lens and sensor sizes in millimetres; clip distances, radius and size in
scene units; angles in radians; energy in watts (SUN: irradiance in W/m^2).
"""

import math
from .action_schema_types import ALIAS, ANGLES, number, enum, text, array, vector, obj

PROJECTIONS = ('PERSP', 'ORTHO')
SENSOR_FITS = ('AUTO', 'HORIZONTAL', 'VERTICAL')
LIGHT_TYPES = ('POINT', 'SUN', 'SPOT', 'AREA')
AREA_SHAPES = ('SQUARE', 'RECTANGLE', 'DISK', 'ELLIPSE')
RECTANGULAR_SHAPES = frozenset({'RECTANGLE', 'ELLIPSE'})
# look_at refuses points closer than this to the object's origin.
MIN_AIM_DISTANCE = .0001
# Staged look_at results must point their -Z axis within this angle (radians).
AIM_TOLERANCE = .00001

# Closed parameter -> RNA property maps; nothing else is read or written.
CAMERA_RNA = {'projection': 'type', 'lens': 'lens', 'ortho_scale': 'ortho_scale', 'sensor_fit': 'sensor_fit',
              'sensor_width': 'sensor_width', 'sensor_height': 'sensor_height',
              'clip_start': 'clip_start', 'clip_end': 'clip_end'}
LIGHT_RNA = {'light_type': 'type', 'energy': 'energy', 'color': 'color', 'use_shadow': 'use_shadow',
             'radius': 'shadow_soft_size', 'angle': 'angle', 'spot_size': 'spot_size',
             'spot_blend': 'spot_blend', 'shape': 'shape', 'size': 'size', 'size_y': 'size_y'}

CAMERA_FIELDS = {'projection': enum(PROJECTIONS), 'lens': number(1, 5000), 'ortho_scale': number(.001, 100000),
                 'sensor_fit': enum(SENSOR_FITS), 'sensor_width': number(1, 1000), 'sensor_height': number(1, 1000),
                 'clip_start': number(.000001, 100000), 'clip_end': number(.000001, 1000000)}
LIGHT_FIELDS = {'light_type': enum(LIGHT_TYPES), 'energy': number(0, 1000000), 'color': array(number(0, 1), 3, 3),
                'use_shadow': {'type': 'boolean'}, 'radius': number(0, 100), 'angle': number(0, math.pi),
                'spot_size': number(math.radians(1), math.pi), 'spot_blend': number(0, 1),
                'shape': enum(AREA_SHAPES), 'size': number(0, 1000), 'size_y': number(0, 1000)}

# Settings that exist only for one projection/light type. Everything else is common.
CAMERA_TYPE_FIELDS = {'PERSP': frozenset({'lens'}), 'ORTHO': frozenset({'ortho_scale'})}
LIGHT_TYPE_FIELDS = {'POINT': frozenset({'radius'}), 'SUN': frozenset({'angle'}),
                     'SPOT': frozenset({'radius', 'spot_size', 'spot_blend'}),
                     'AREA': frozenset({'shape', 'size', 'size_y'})}

# Explicit create defaults, matching this Blender build's Add Camera/Add Light
# operators: new lights use 10 W (sun 1 W/m^2), area lights 1 unit wide.
CAMERA_COMMON_DEFAULTS = {'projection': 'PERSP', 'sensor_fit': 'AUTO', 'sensor_width': 36., 'sensor_height': 24.,
                          'clip_start': .1, 'clip_end': 1000.}
CAMERA_TYPE_DEFAULTS = {'PERSP': {'lens': 50.}, 'ORTHO': {'ortho_scale': 6.}}
LIGHT_COMMON_DEFAULTS = {'light_type': 'POINT', 'color': [1., 1., 1.], 'use_shadow': True}
LIGHT_TYPE_DEFAULTS = {'POINT': {'energy': 10., 'radius': 0.}, 'SUN': {'energy': 1., 'angle': math.radians(.526)},
                       'SPOT': {'energy': 10., 'radius': 0., 'spot_size': math.pi / 4, 'spot_blend': .15},
                       'AREA': {'energy': 10., 'shape': 'SQUARE', 'size': 1.}}
RECTANGULAR_SIZE_Y_DEFAULT = 1.

FAMILIES = {'camera': ('projection', CAMERA_FIELDS, CAMERA_TYPE_FIELDS, CAMERA_COMMON_DEFAULTS, CAMERA_TYPE_DEFAULTS),
            'light': ('light_type', LIGHT_FIELDS, LIGHT_TYPE_FIELDS, LIGHT_COMMON_DEFAULTS, LIGHT_TYPE_DEFAULTS)}


def _settings_schema(fields):
    # One object per family, shared by create, configure and the matching
    # settings check, so schema compaction emits it once as a local $def.
    return dict(obj(fields), minProperties=1, maxProperties=len(fields))


CAMERA_SETTINGS = _settings_schema(CAMERA_FIELDS)
LIGHT_SETTINGS = _settings_schema(LIGHT_FIELDS)
_SETTINGS = {'camera': CAMERA_SETTINGS, 'light': LIGHT_SETTINGS}


# Camera-relative placement of a new camera or light: where it stands around look_at as the scene camera sees it.
# The host resolves it to an exact location before approval (program_orbits), so the approval, the derived
# transform and aim checks and the kernel all see an ordinary create. Why: in recorded one-shot lighting
# runs (Opus 5.5, factory scene) every plan put the rim light at world
# +Y, (0, 5, 4), about 102 degrees from the factory camera rather than behind the cube as it sees it, in 5 of 5
# trials, although the turn context stated the camera's position and view direction. Lighting is conceived in
# the camera's frame; this lets a plan say it in that frame and leaves the arithmetic to the host.
ORBIT_FRAME = ("radians around look_at as the scene camera sees it. azimuth 0 = camera side, pi/2 = its right, "
               "pi = behind look_at (rim light); elevation above look_at; distance from look_at, default the camera's.")
ORBIT_NOTE = "Instead of location: " + ORBIT_FRAME  # The field's description in Claude's plan and an exact lookup.
# The Codex program reference states it on a line of its own, far from the field (TypeScript drops descriptions),
# so the line says first when to use it. In a recorded lighting re-check with only the field's
# note there, every GPT-6 Astra and GPT-6.1 Sol trial that passed used orbit (3 of 6) and every one that failed put
# the rim light at world coordinates, (1-2, 4, 4), not behind the cube from the camera; Opus used it in 5 of 5.
ORBIT_REFERENCE_NOTE = ("camera_create, light_create: orbit with look_at places one relative to the view (a key, "
                        "fill or rim light, behind, camera-left); location is world coordinates, not "
                        "camera-relative. orbit is measured from the position of the camera the scene looks through "
                        "once the program has run, so a program that edits that camera, or an object it follows, "
                        "cannot also use orbit. orbit: " + ORBIT_FRAME)
ORBIT = dict(obj({'azimuth': number(-2 * math.pi, 2 * math.pi), 'elevation': number(-math.pi / 2, math.pi / 2),
                  'distance': number(.001, 100000)}, ['azimuth', 'elevation']), description=ORBIT_NOTE)


def _create_schema(family, name):
    # Absent settings take the documented type defaults (filled canonically);
    # absent look_at keeps the given rotation; orbit (with look_at) replaces location.
    return obj({'name': text(default=name), 'alias': ALIAS, 'location': vector([0, 0, 0]), 'rotation': ANGLES,
                'look_at': vector(), 'orbit': ORBIT, 'settings': _SETTINGS[family]})


def _configure_schema(family):
    # Absent settings stay unchanged and never appear in the canonical operation.
    return obj({'settings': _SETTINGS[family], 'look_at': vector()})


CAMERA_LIGHT_SCHEMAS = {'camera_create': _create_schema('camera', 'Angelo Camera'),
                        'camera_configure': _configure_schema('camera'),
                        'light_create': _create_schema('light', 'Angelo Light'),
                        'light_configure': _configure_schema('light')}
CAMERA_LIGHT_KINDS = frozenset(CAMERA_LIGHT_SCHEMAS)
CAMERA_LIGHT_CREATE_KINDS = frozenset({'camera_create', 'light_create'})


def family_of(kind):
    return 'camera' if kind.startswith('camera_') else 'light'


def data_fields(kind, params):
    """The object-data settings this canonical operation writes, in catalog order."""
    settings = params.get('settings', {})
    return [key for key in FAMILIES[family_of(kind)][1] if key in settings]


def check_applicable(family, kind_value, fields, shape=None):
    """Reject settings that do not exist for the resulting projection/light type."""
    type_fields = FAMILIES[family][2]
    specific = frozenset().union(*type_fields.values())
    allowed = type_fields.get(kind_value, frozenset())
    wrong = sorted(field for field in fields if field in specific and field not in allowed)
    if wrong:
        raise ValueError(', '.join(wrong) + ' do not apply to ' + family + ' type ' + str(kind_value))
    if 'size_y' in fields and shape is not None and shape not in RECTANGULAR_SHAPES:
        raise ValueError('size_y applies only to RECTANGLE or ELLIPSE area lights')


def check_clip_range(clip_start, clip_end):
    if clip_start >= clip_end:
        raise ValueError('Camera clip_start must be smaller than clip_end')


def canonical_camera_light(kind, params, given=None):
    """Cross-field checks and explicit type defaults on validated parameters.

    Creation fills every setting of its projection/light type, so approvals
    show exact values. Configuration keeps only explicitly supplied settings;
    the kernel checks those against the staged object's resulting type.
    ``given`` holds the parameters as submitted, before schema defaults: an
    orbit refuses a ``location`` written beside it, even the default one, and
    its canonical form has none until the host resolves it (program_orbits).
    """
    family = family_of(kind)
    type_key, _fields, _type_fields, common_defaults, type_defaults = FAMILIES[family]
    create = kind in CAMERA_LIGHT_CREATE_KINDS
    if 'settings' in params and not params['settings']:
        raise ValueError('Omit settings instead of sending an empty object')
    if not create and 'settings' not in params and 'look_at' not in params:
        raise ValueError(kind + ' requires settings or look_at')
    settings = params.setdefault('settings', {}) if create else params.get('settings', {})
    if create:
        for key, value in common_defaults.items():
            settings.setdefault(key, list(value) if isinstance(value, list) else value)
        for key, value in type_defaults[settings[type_key]].items():
            settings.setdefault(key, value)
        if settings.get('shape') in RECTANGULAR_SHAPES:
            settings.setdefault('size_y', RECTANGULAR_SIZE_Y_DEFAULT)
    fields = data_fields(kind, params)
    if type_key in settings:
        check_applicable(family, settings[type_key], fields, settings.get('shape'))
    elif 'size_y' in settings and 'shape' in settings:
        check_applicable(family, 'AREA', ['size_y'], settings['shape'])
    if 'clip_start' in settings and 'clip_end' in settings:
        check_clip_range(settings['clip_start'], settings['clip_end'])
    if create and 'orbit' in params:
        if 'look_at' not in params:
            raise ValueError('orbit places the object around look_at: give look_at, the point it faces')
        if 'location' in (given or ()) or any(value != 0 for value in params.get('location', ())):
            raise ValueError('Choose location or orbit, not both')
        params.pop('location', None)  # The schema's default; the resolved orbit gives the location.
    if create and 'look_at' in params:
        if any(value != 0 for value in params['rotation']):
            raise ValueError('Choose an explicit rotation or look_at, not both')
        if 'orbit' not in params:  # An orbit stands at least its distance (>= 0.001) from look_at once resolved.
            look_at_euler(params['location'], params['look_at'])  # Rejects a degenerate aim before approval.
    return params


def orbit_location(camera, point, orbit):
    """The world location ``orbit`` names: around ``point`` as a camera standing at ``camera`` sees it.

    The frame is the camera's horizontal bearing from ``point`` with world +Z
    up (ORBIT_NOTE), built from the camera's position alone, never its
    rotation: azimuth 0 is the camera's side, positive turns toward the
    camera's right as an unrolled camera looking at ``point`` sees it
    (counterclockwise seen from above; a rolled or tilted-away camera's screen
    right can differ) and pi is straight behind
    ``point``; elevation rises from ``point``'s height; distance defaults to the
    camera's own distance from ``point``, so lights that leave it out stand
    equally far and their energies alone rank them. Coordinates are rounded to
    six decimals, as the turn context states locations. A camera straight
    above or below ``point`` has no side to measure from and is refused.
    """
    offset = [float(c) - float(p) for c, p in zip(camera, point)]
    if not all(math.isfinite(value) for value in offset):
        raise ValueError('orbit is measured from the scene camera, whose position is not finite; give location instead')
    if math.sqrt(sum(value * value for value in offset)) < MIN_AIM_DISTANCE:
        raise ValueError('orbit is measured from the scene camera, which stands at look_at; give location instead')
    horizontal = math.hypot(offset[0], offset[1])
    if horizontal < MIN_AIM_DISTANCE:
        raise ValueError('orbit is measured from the scene camera, which stands straight above or below look_at; '
                         'give location instead')
    toward = (offset[0] / horizontal, offset[1] / horizontal)
    right = (-toward[1], toward[0])  # The camera's right: its view (-toward) crossed with world +Z.
    distance = float(orbit.get('distance') or math.sqrt(sum(value * value for value in offset)))
    azimuth, elevation = float(orbit['azimuth']), float(orbit['elevation'])
    level = distance * math.cos(elevation)
    across = [math.cos(azimuth) * toward[axis] + math.sin(azimuth) * right[axis] for axis in (0, 1)]
    location = [float(point[0]) + level * across[0], float(point[1]) + level * across[1],
                float(point[2]) + distance * math.sin(elevation)]
    return [round(value, 6) + 0. for value in location]  # + 0. turns a rounded -0.0 into 0.0.


def look_at_euler(origin, point):
    """XYZ Euler radians aiming local -Z at ``point``, local +Y toward world +Z.

    No roll is introduced. Straight up/down views use world +X as the right
    axis. Deterministic and independent of mathutils, so the approved rotation
    can be reproduced and tested without Blender. Blender's XYZ Euler matrix is
    Rz(c) @ Ry(b) @ Rx(a); the right axis is horizontal, so b is always zero.
    """
    direction = [float(p) - float(o) for p, o in zip(point, origin)]
    length = math.sqrt(sum(value * value for value in direction))
    if not math.isfinite(length) or length < MIN_AIM_DISTANCE:
        raise ValueError('look_at must be a finite point at least 0.0001 from the object origin')
    z = [-value / length for value in direction]  # Local +Z points away from the target.
    horizontal = math.hypot(z[0], z[1])
    x = [-z[1] / horizontal, z[0] / horizontal, 0.] if horizontal > 1e-9 else [1., 0., 0.]  # world_up x z
    y = [z[1] * x[2] - z[2] * x[1], z[2] * x[0] - z[0] * x[2], z[0] * x[1] - z[1] * x[0]]  # z x x
    return [math.atan2(y[2], z[2]), 0., math.atan2(x[1], x[0])]


def aim_error(matrix, point):
    """Angle (radians) between a 4x4 row-major world matrix's -Z axis and ``point``."""
    forward = [-float(matrix[row][2]) for row in range(3)]
    direction = [float(point[row]) - float(matrix[row][3]) for row in range(3)]
    lengths = [math.sqrt(sum(value * value for value in vector_)) for vector_ in (forward, direction)]
    if not all(math.isfinite(value) and value > 1e-12 for value in lengths):
        raise ValueError('Aim is undefined for a singular viewing axis or a point at the object origin')
    dot = sum(a * b for a, b in zip(forward, direction))
    cross = [forward[1] * direction[2] - forward[2] * direction[1], forward[2] * direction[0] - forward[0] * direction[2],
             forward[0] * direction[1] - forward[1] * direction[0]]
    # atan2 keeps small angles precise where acos(dot) would lose them.
    return math.atan2(math.sqrt(sum(value * value for value in cross)), dot)


def is_default_light_graph(tree):
    """Whether a light's node tree is Blender's default Emission -> Light Output.

    Every light in this build owns a node tree. Only this exact graph (white
    color, strength 1, nothing muted, linked, animated or retargeted) leaves
    emission fully described by energy and color. Duck-typed: no bpy import.
    """
    if tree is None:
        return True
    animation = getattr(tree, 'animation_data', None)
    if animation is not None and (animation.action is not None or len(animation.drivers) or len(animation.nla_tracks)):
        return False
    nodes = {node.bl_idname: node for node in tree.nodes}
    if len(tree.nodes) != 2 or set(nodes) != {'ShaderNodeEmission', 'ShaderNodeOutputLight'} or len(tree.links) != 1:
        return False
    emission, output = nodes['ShaderNodeEmission'], nodes['ShaderNodeOutputLight']
    link = tree.links[0]
    if (link.from_node.bl_idname, link.from_socket.identifier, link.to_node.bl_idname, link.to_socket.identifier) != (
            'ShaderNodeEmission', 'Emission', 'ShaderNodeOutputLight', 'Surface') or getattr(link, 'is_muted', False):
        return False
    if emission.mute or output.mute or not output.is_active_output or output.target != 'ALL':
        return False
    color, strength = emission.inputs.get('Color'), emission.inputs.get('Strength')
    return (color is not None and strength is not None and not color.is_linked and not strength.is_linked
            and [float(value) for value in color.default_value] == [1., 1., 1., 1.] and float(strength.default_value) == 1.)


CAMERA_LIGHT_CAPABILITIES = {
    'camera_projections': list(PROJECTIONS), 'light_types': list(LIGHT_TYPES), 'area_shapes': list(AREA_SHAPES),
    'camera_create_defaults': dict(CAMERA_COMMON_DEFAULTS, by_projection=CAMERA_TYPE_DEFAULTS),
    'light_create_defaults': dict(LIGHT_COMMON_DEFAULTS, by_type=LIGHT_TYPE_DEFAULTS,
                                  rectangular_size_y=RECTANGULAR_SIZE_Y_DEFAULT),
    'type_specific_fields': {'camera': {key: sorted(value) for key, value in CAMERA_TYPE_FIELDS.items()},
                             'light': {key: sorted(value) for key, value in LIGHT_TYPE_FIELDS.items()},
                             'size_y_shapes': sorted(RECTANGULAR_SHAPES)},
    'units': 'lens/sensor mm; clip, radius and size scene units; angles radians; energy W (SUN W/m^2)',
    'settings': 'create: absent settings take these defaults; configure: absent settings stay unchanged. '
                'Type-specific settings must match the resulting projection/light type.',
    'orbit': 'create only, with look_at: resolved against the scene camera into an exact location before '
             'approval. Refused without a scene camera, with one straight above or below look_at, or in a program '
             'that sets, edits or moves the scene camera.',
    'look_at': 'Aims local -Z at a world point with local +Y toward world +Z and no roll. Requires an '
               'unparented, unconstrained, unanimated object with positive scale and no delta rotation; '
               'use transform for location first.',
    'shared_object_data': 'Staging edits a private copy. Commit gives only the written object that copy; '
                          'other objects keep the original camera/light/mesh data (reported as isolated_shared_data).',
    'animated_data': 'Camera/light data with actions, drivers or NLA is refused for configuration.',
    'light_node_tree': "Only Blender's default Emission -> Light Output light graph is supported.",
    'preview': 'Solid previews neither draw cameras/lights nor shade with scene lights; review settings and checks.',
}
