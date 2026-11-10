# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
#
# SPDX-License-Identifier: Apache-2.0
"""Collision shapes: fitting, axis conversion, transforms and the OMI_physics_shape dictionaries. No ``bpy``.

Units are the scene's (glTF metres). A shape is fitted in its object's own (Blender) axes from the object's
evaluated bounds, the way Blender's own Rigid Body fits a shape (``rigidbody.cc``): a box is the bounds, a sphere's
radius is half the largest extent, and a capsule or cylinder runs along one axis with the radius of the larger of the
other two extents. A capsule or cylinder axis of ``AUTO`` is the longest extent (Z on a tie, then Y).

**Capsule height.** OMI's 2025-01 revision made a capsule's ``height`` the distance between its hemisphere centres
and split ``radius`` into ``radiusTop``/``radiusBottom``. Godot 4.4 to 4.7.2 (``GLTFPhysicsShape::from_dictionary``)
reads ``radius`` and takes ``height`` as the full height, as the earlier revision defined it. Angelo writes what Godot
reads (``radius``, full ``height``) and also ``radiusTop``/``radiusBottom``, so a newer reader gets the radius. Reading
back, a capsule with ``radius`` is the earlier form and one without it is the 2025 form (``height`` + both radii).
A cylinder's ``height`` is its full height in both revisions.
"""

import math

BODIES = ("NONE", "STATIC", "RIGID", "KINEMATIC", "TRIGGER")
SHAPES = ("NONE", "BOX", "SPHERE", "CAPSULE", "CYLINDER", "CONVEX", "TRIMESH")
PRIMITIVES = ("BOX", "SPHERE", "CAPSULE", "CYLINDER")
MESH_SHAPES = ("CONVEX", "TRIMESH")
AXES = ("AUTO", "X", "Y", "Z")
MOTION_TYPES = {"STATIC": "static", "RIGID": "dynamic", "KINEMATIC": "kinematic"}
BODY_FROM_MOTION = {value: key for key, value in MOTION_TYPES.items()}
OMI_TYPES = {"BOX": "box", "SPHERE": "sphere", "CAPSULE": "capsule", "CYLINDER": "cylinder", "CONVEX": "convex",
             "TRIMESH": "trimesh"}
SHAPE_FROM_OMI = {value: key for key, value in OMI_TYPES.items()}
SHAPE_FROM_OMI["hull"] = "CONVEX"   # OMI_collider's name, which Godot still reads.
IDENTITY_ROTATION = (0.0, 0.0, 0.0, 1.0)   # glTF order: x, y, z, w.
EPSILON = 1e-6


class Fit:
    """A primitive shape in one node's axes: its ``center``, a box's ``size``, a ``radius`` and full ``height``, and
    the ``axis`` index (0, 1, 2) a capsule or cylinder runs along (None for a box or sphere)."""
    __slots__ = ("kind", "center", "size", "radius", "height", "axis")

    def __init__(self, kind, center=(0.0, 0.0, 0.0), size=(1.0, 1.0, 1.0), radius=0.5, height=2.0, axis=None):
        self.kind, self.center, self.size = kind, tuple(center), tuple(size)
        self.radius, self.height, self.axis = radius, height, axis

    def __repr__(self):
        return "Fit(%s, center=%s, size=%s, radius=%s, height=%s, axis=%s)" % (
            self.kind, self.center, self.size, self.radius, self.height, self.axis)


def bounds(corners):
    """(minimum, maximum) of an object's bound box corners (eight points, or any number)."""
    corners = [tuple(corner) for corner in corners]
    return (tuple(min(point[i] for point in corners) for i in range(3)),
            tuple(max(point[i] for point in corners) for i in range(3)))


def longest_axis(extents):
    """The index of the longest extent; a tie goes to Z, then Y (Blender's up axis first)."""
    return max((2, 1, 0), key=lambda index: extents[index])


def fit_bounds(kind, minimum, maximum, axis="AUTO"):
    """The ``kind`` primitive fitted to the bounds ``minimum``..``maximum`` (Blender local axes)."""
    center = tuple((low + high) * 0.5 for low, high in zip(minimum, maximum))
    extents = tuple(high - low for low, high in zip(minimum, maximum))
    if kind == "BOX":
        return Fit(kind, center, size=extents)
    if kind == "SPHERE":
        return Fit(kind, center, radius=max(extents) * 0.5)
    index = longest_axis(extents) if axis == "AUTO" else "XYZ".index(axis)
    radius = max(extent for i, extent in enumerate(extents) if i != index) * 0.5
    height = extents[index]
    if kind == "CAPSULE":
        height = max(height, 2.0 * radius)   # A capsule is at least its two hemispheres.
    return Fit(kind, center, radius=radius, height=height, axis=index)


def manual(kind, offset, size, radius, height, axis):
    """The person's own dimensions; a capsule or cylinder axis of AUTO is Z (there is nothing to measure)."""
    index = None if kind in ("BOX", "SPHERE") else "XYZ".index("Z" if axis == "AUTO" else axis)
    if kind == "CAPSULE":
        height = max(height, 2.0 * radius)
    return Fit(kind, offset, size=size, radius=radius, height=height, axis=index)


# --- Blender axes to glTF axes (the exporter's +Y up conversion: x, y, z -> x, z, -y).

def to_gltf_vector(vector, yup):
    x, y, z = vector
    return (x, z, -y) if yup else (x, y, z)


def from_gltf_vector(vector, yup):
    x, y, z = vector
    return (x, -z, y) if yup else (x, y, z)


def bounds_from_gltf(minimum, maximum, yup):
    """(minimum, maximum) in Blender axes of glTF-axes bounds (+Y up turns Blender's Y into glTF's -Z)."""
    low, high = from_gltf_vector(minimum, yup), from_gltf_vector(maximum, yup)
    return tuple(map(min, low, high)), tuple(map(max, low, high))


def swap_size(size, yup):
    """A box's extents between Blender and glTF axes (the same swap both ways: Y and Z trade places)."""
    return (size[0], size[2], size[1]) if yup else tuple(size)


def to_gltf_axis(index, yup):
    """The glTF axis a Blender axis becomes (X stays X; Y becomes -Z and Z becomes Y under +Y up)."""
    return (0, 2, 1)[index] if yup else index


def to_gltf_fit(fit, yup):
    """``fit`` in glTF node axes: the centre converted, a box's extents permuted, the axis renamed."""
    axis = None if fit.axis is None else to_gltf_axis(fit.axis, yup)
    return Fit(fit.kind, to_gltf_vector(fit.center, yup), size=swap_size(fit.size, yup), radius=fit.radius,
               height=fit.height, axis=axis)


# OMI capsules and cylinders run along their node's +Y: the rotation that lays +Y along glTF axis 0, 1 or 2.
_HALF = math.sqrt(0.5)
AXIS_ROTATIONS = {0: (0.0, 0.0, -_HALF, _HALF), 1: IDENTITY_ROTATION, 2: (_HALF, 0.0, 0.0, _HALF)}


# --- Transforms: 4x4 row-major matrices and (x, y, z, w) quaternions, as glTF stores them.

def quaternion_matrix(rotation):
    x, y, z, w = rotation
    return ((1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)),
            (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)),
            (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)))


def matrix_quaternion(m):
    """The unit quaternion (w >= 0) of a 3x3 rotation matrix (Shepperd's method)."""
    trace = m[0][0] + m[1][1] + m[2][2]
    if trace > 0:
        s = math.sqrt(trace + 1.0) * 2
        result = ((m[2][1] - m[1][2]) / s, (m[0][2] - m[2][0]) / s, (m[1][0] - m[0][1]) / s, 0.25 * s)
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2
        result = (0.25 * s, (m[0][1] + m[1][0]) / s, (m[0][2] + m[2][0]) / s, (m[2][1] - m[1][2]) / s)
    elif m[1][1] > m[2][2]:
        s = math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2
        result = ((m[0][1] + m[1][0]) / s, 0.25 * s, (m[1][2] + m[2][1]) / s, (m[0][2] - m[2][0]) / s)
    else:
        s = math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2
        result = ((m[0][2] + m[2][0]) / s, (m[1][2] + m[2][1]) / s, 0.25 * s, (m[1][0] - m[0][1]) / s)
    length = math.sqrt(sum(value * value for value in result)) or 1.0
    result = tuple(value / length for value in result)
    return result if result[3] >= 0 else tuple(-value for value in result)


def compose(translation, rotation, scale):
    """The 4x4 matrix of a node's TRS (None parts are identity)."""
    t = translation or (0.0, 0.0, 0.0)
    r = quaternion_matrix(rotation or IDENTITY_ROTATION)
    s = scale or (1.0, 1.0, 1.0)
    return tuple(tuple(r[row][column] * s[column] for column in range(3)) + (t[row],) for row in range(3)) + (
        (0.0, 0.0, 0.0, 1.0),)


def multiply(a, b):
    return tuple(tuple(sum(a[row][k] * b[k][column] for k in range(4)) for column in range(4)) for row in range(4))


def decompose(m):
    """(translation, rotation, scale) of an affine matrix; a negative determinant flips the X scale. A sheared
    matrix (a non-uniform scale above a rotated child) has no exact TRS: the nearest is returned."""
    translation = (m[0][3], m[1][3], m[2][3])
    columns = [tuple(m[row][column] for row in range(3)) for column in range(3)]
    scale = [math.sqrt(sum(value * value for value in column)) for column in columns]
    determinant = (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
                   + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))
    if determinant < 0:
        scale[0] = -scale[0]
    rotation_matrix = tuple(tuple(m[row][column] / (scale[column] or 1.0) for column in range(3)) for row in range(3))
    return translation, matrix_quaternion(rotation_matrix), tuple(scale)


def multiply_quaternions(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz)


def is_identity(translation, rotation, scale, tolerance=EPSILON):
    return (all(abs(value) <= tolerance for value in translation or (0.0, 0.0, 0.0))
            and all(abs(value - expected) <= tolerance
                    for value, expected in zip(rotation or IDENTITY_ROTATION, IDENTITY_ROTATION))
            and all(abs(value - 1.0) <= tolerance for value in scale or (1.0, 1.0, 1.0)))


def scaled(fit, scale):
    """``fit`` (glTF node axes) with a node scale baked into its dimensions."""
    scale = tuple(abs(value) for value in scale)
    if fit.kind == "BOX":
        return Fit(fit.kind, fit.center, size=tuple(a * b for a, b in zip(fit.size, scale)))
    if fit.kind == "SPHERE":
        return Fit(fit.kind, fit.center, radius=fit.radius * max(scale))
    radial = max(value for index, value in enumerate(scale) if index != fit.axis)
    return Fit(fit.kind, fit.center, radius=fit.radius * radial, height=fit.height * scale[fit.axis], axis=fit.axis)


def placement(fit, relative=None):
    """(translation, rotation, fit) of an unscaled node holding the primitive ``fit`` (glTF axes of the fitted node),
    below a parent whose frame reaches the fitted node through ``relative`` (a 4x4 matrix; None: the fitted node
    itself). Any scale on the way is baked into the dimensions: OMI discourages scaled shape nodes and Godot warns
    about them. A capsule or cylinder is turned so its +Y runs along its axis."""
    matrix = compose(fit.center, None, None)
    if relative is not None:
        matrix = multiply(relative, matrix)
    translation, rotation, scale = decompose(matrix)
    baked = scaled(fit, scale)
    if fit.axis is not None:
        rotation = multiply_quaternions(rotation, AXIS_ROTATIONS[fit.axis])
    return translation, rotation, baked


# --- OMI dictionaries.

def _number(value):
    return round(float(value), 6)


def omi_shape(fit=None, kind=None, mesh=None):
    """The OMI_physics_shape entry for a primitive ``fit`` (glTF axes, scale baked) or a mesh shape ``kind``."""
    if kind in MESH_SHAPES:
        name = OMI_TYPES[kind]
        return {"type": name, name: {"mesh": mesh}}
    name = OMI_TYPES[fit.kind]
    if fit.kind == "BOX":
        return {"type": name, name: {"size": [_number(value) for value in fit.size]}}
    if fit.kind == "SPHERE":
        return {"type": name, name: {"radius": _number(fit.radius)}}
    radius = _number(fit.radius)
    return {"type": name, name: {"radius": radius, "radiusTop": radius, "radiusBottom": radius,
                                 "height": _number(fit.height)}}


def read_shape(entry):
    """(kind, size, radius, full height, mesh index or None) of an OMI_physics_shape or OMI_collider entry; None when
    its type is unknown. Missing values take the specification's defaults."""
    if not isinstance(entry, dict):
        return None
    kind = SHAPE_FROM_OMI.get(entry.get("type"))
    if kind is None:
        return None
    properties = entry.get(entry.get("type"))
    properties = properties if isinstance(properties, dict) else entry   # OMI_collider kept them flat.
    size = properties.get("size", (1.0, 1.0, 1.0))
    size = tuple(float(value) for value in size) if isinstance(size, (list, tuple)) and len(size) == 3 else (
        1.0, 1.0, 1.0)
    if "radius" in properties:
        radius = float(properties["radius"])
        height = float(properties.get("height", 2.0))
    else:
        top, bottom = float(properties.get("radiusTop", 0.5)), float(properties.get("radiusBottom", 0.5))
        radius = max(top, bottom)
        height = float(properties.get("height", 1.0 if kind == "CAPSULE" else 2.0))
        if kind == "CAPSULE":
            height += top + bottom   # The 2025 form's height runs between the hemisphere centres.
    mesh = properties.get("mesh")
    return kind, size, radius, height, mesh if isinstance(mesh, int) and mesh >= 0 else None
