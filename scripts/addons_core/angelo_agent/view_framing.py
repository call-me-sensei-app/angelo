# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Orthographic camera framing for visual observations. Pure Python, no bpy.

Matrices are row-major 4x4 nested lists in world units. ``forward`` points from
the framed content toward the camera (the camera's +Z axis), so the camera
looks along -forward, exactly like a Blender camera object.
"""

import math

MARGIN = 1.15
MIN_HALF_WIDTH = 0.001
AXIS_DIRECTIONS = {
    "FRONT": (0.0, -1.0, 0.0), "BACK": (0.0, 1.0, 0.0),
    "LEFT": (-1.0, 0.0, 0.0), "RIGHT": (1.0, 0.0, 0.0),
    "TOP": (0.0, 0.0, 1.0), "BOTTOM": (0.0, 0.0, -1.0),
    "ISO": (1.0, -1.0, 0.7),
}


def _normalized(vector):
    length = math.sqrt(sum(value * value for value in vector))
    if not math.isfinite(length) or length < 1e-12:
        raise ValueError("Cannot frame along a zero or invalid direction")
    return tuple(value / length for value in vector)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def axis_basis(direction):
    """(right, up, forward) matching Blender's numpad views for each axis.

    World +Z is up unless looking straight down/up; there the image keeps +X to
    the right with +Y up (TOP) or -Y up (BOTTOM), as Blender's own views do.
    """
    forward = _normalized(direction)
    if abs(forward[2]) > 0.999999:
        hint = (0.0, 1.0, 0.0) if forward[2] > 0 else (0.0, -1.0, 0.0)
    else:
        hint = (0.0, 0.0, 1.0)
    right = _normalized(_cross(hint, forward))
    return right, _cross(forward, right), forward


def basis_from_view_matrix(view_matrix):
    """Orthonormal (right, up, forward) from a world-to-view matrix's rows."""
    right = _normalized(view_matrix[0][:3])
    forward = _normalized(view_matrix[2][:3])
    # Remove skew before deriving up, so the result is a rigid camera.
    right = _normalized(tuple(r - _dot(right, forward) * f for r, f in zip(right, forward)))
    return right, _cross(forward, right), forward


def orthographic_fit(points, basis, margin=MARGIN):
    """Square orthographic frame of world points seen along ``basis``.

    Returns center, half_width, camera distance from center and clip depth.
    The near plane is at the camera, at least one half-width in front of the
    nearest point, so geometry nearer than that is cut away instead of hiding
    the framed content. The far plane keeps a generous background range for
    visible neighbors behind it.
    """
    right, up, forward = basis
    points = [tuple(float(v) for v in point) for point in points]
    if not points or any(len(point) != 3 or not all(math.isfinite(v) for v in point) for point in points):
        raise ValueError("Framing needs finite world-space points")
    xs = [_dot(point, right) for point in points]
    ys = [_dot(point, up) for point in points]
    zs = [_dot(point, forward) for point in points]
    cx, cy = (min(xs) + max(xs)) * 0.5, (min(ys) + max(ys)) * 0.5
    mid_z = (min(zs) + max(zs)) * 0.5
    center = tuple(cx * r + cy * u + mid_z * f for r, u, f in zip(right, up, forward))
    half_width = max((max(xs) - min(xs)) * 0.5, (max(ys) - min(ys)) * 0.5) * margin
    half_width = max(half_width, MIN_HALF_WIDTH)
    near_extent, far_extent = max(zs) - mid_z, mid_z - min(zs)
    distance = max(half_width * 4.0, near_extent + half_width)
    depth = max(half_width * 12.0, distance + far_extent + half_width)
    if not all(math.isfinite(v) for v in (*center, half_width, distance, depth)):
        raise ValueError("Framing produced non-finite camera values")
    return center, half_width, distance, depth


def camera_matrix(center, basis, distance):
    """Camera-to-world matrix: columns are right, up, forward and position."""
    right, up, forward = basis
    position = tuple(c + f * distance for c, f in zip(center, forward))
    return [[right[i], up[i], forward[i], position[i]] for i in range(3)] + [[0.0, 0.0, 0.0, 1.0]]


def view_matrix(camera):
    """Inverse of a rigid camera-to-world matrix (world-to-view)."""
    rotation = [[camera[row][col] for col in range(3)] for row in range(3)]
    position = [camera[row][3] for row in range(3)]
    inverse = [[rotation[col][row] for col in range(3)] for row in range(3)]
    translation = [-_dot(inverse[row], position) for row in range(3)]
    return [inverse[row] + [translation[row]] for row in range(3)] + [[0.0, 0.0, 0.0, 1.0]]


def orthographic_projection(half_width, half_height, depth):
    """Near plane at the camera, far plane ``depth`` units in front of it."""
    return [[1.0 / half_width, 0.0, 0.0, 0.0], [0.0, 1.0 / half_height, 0.0, 0.0],
            [0.0, 0.0, -2.0 / depth, -1.0], [0.0, 0.0, 0.0, 1.0]]


def render_aspect(render):
    """The output shape of a scene's render settings, render pixel aspect included (SCENE_CAMERA, free cameras)."""
    return (render.resolution_x * render.pixel_aspect_x) / (render.resolution_y * render.pixel_aspect_y)


def output_size(resolution, aspect):
    """Pixel size whose longer side is ``resolution`` and whose ratio is ``aspect``."""
    if not math.isfinite(aspect) or aspect <= 0:
        raise ValueError("Camera aspect must be positive and finite")
    if aspect >= 1:
        return resolution, max(1, round(resolution / aspect))
    return max(1, round(resolution * aspect)), resolution


# A free camera (view_spec: a views item with location and look_at) is the camera Blender would make for those
# values: a 36 mm sensor fitted to the picture's longer side (DEFAULT_SENSOR_WIDTH, sensor fit AUTO), aimed by
# action_camera_light_spec.look_at_euler's convention, no shift. Distances are world units.
SENSOR_WIDTH_MM = 36.0
# Clip planes come from the visible scene's bounding boxes, never from look_at, which only aims (free_clip). A
# perspective depth buffer resolves depth z to about z * z / (clip_start * 2**24): precision is set by clip_start
# alone, so clip_end is generous (Workbench's shadows need it well past their casters) and clip_start is chosen
# between keeping near geometry and resolving far geometry:
# * no box reaches the camera's plane: half the nearest box's depth, nothing being nearer;
# * a box reaches it (a ground, a valley, a room, a sky dome) and its geometry may be anywhere in front: Blender's
#   0.1, less for a scene shallower than 100 units and for a box wholly in front that is nearer;
# * never nearer than the scene's depth / DEPTH_RESOLUTION (Blender's viewport ratio, 0.01 to 1000), so the
#   farthest geometry stays distinct; a box this cuts is stated.
DEFAULT_CLIP_START, DEFAULT_CLIP_END = 0.1, 1000.0
DEPTH_RESOLUTION = 1.0e5   # Most depth / clip_start: Blender's viewport default range, 0.01 to 1000.
SMALL_SCENE_RATIO = 1.0e3  # A scene shallower than 100 units starts at a thousandth of its depth.
FAR_RATIO = 1.0e4          # clip_end is at least clip_start x this (Blender's new camera, 0.1 to 1000).
FAR_MARGIN = 1.05
PARTIAL_FAR_MARGIN = 2.0   # A truncated scan saw only part of the scene: its farthest box is a lower bound.
FREE_FIELDS = ("location", "look_at", "lens", "ortho_scale")


def look_at_basis(location, look_at):
    """(right, up, forward) of a camera at ``location`` aimed at ``look_at``: forward is its +Z, away from it.

    The convention of ``action_camera_light_spec.look_at_euler`` (what camera_create's look_at makes): no
    roll, right horizontal and up toward world +Z; looking straight down or up, right is world +X, so the
    picture is oriented as the TOP or BOTTOM view.
    """
    forward = tuple(float(c) - float(t) for c, t in zip(location, look_at))
    length = math.sqrt(sum(value * value for value in forward))
    if not math.isfinite(length) or length < 1e-4:
        raise ValueError("look_at must be a finite point at least 0.0001 from location")
    forward = tuple(value / length for value in forward)
    horizontal = math.hypot(forward[0], forward[1])
    right = (-forward[1] / horizontal, forward[0] / horizontal, 0.0) if horizontal > 1e-9 else (1.0, 0.0, 0.0)
    return right, _cross(forward, right), forward


def _beside(points, location, side, depths):
    """Whether a box (eight corners, their depths) lies wholly outside one of the frame's four side planes.

    ``side`` is ``(right, up, half_width, half_height, perspective)``: half sizes at depth 1 for a perspective
    camera, in world units for an orthographic one.
    """
    right, up, half_width, half_height, perspective = side
    across = [sum((float(p) - float(c)) * r for p, c, r in zip(point, location, right)) for point in points]
    along = [sum((float(p) - float(c)) * u for p, c, u in zip(point, location, up)) for point in points]
    for offsets, half in ((across, half_width), (along, half_height)):
        limits = [half * depth for depth in depths] if perspective else [half] * len(depths)
        if all(o > limit for o, limit in zip(offsets, limits)) or all(o < -limit for o, limit in zip(offsets, limits)):
            return True
    return False


def box_depths(points, location, forward, side=None):
    """``(nearest_front, farthest, straddles)`` of bounding boxes given as eight corners each.

    Depth along the viewing direction is linear, so over a box its least and greatest values are at corners.
    ``nearest_front``: the least depth of the boxes wholly in front of the camera's plane (None without one);
    ``farthest``: the greatest depth of any corner (None for no points); ``straddles``: some box reaches the
    camera's plane, so its geometry may lie at any depth in front. With ``side`` (``_beside``), a box wholly
    outside the frame sets neither: nothing of it is drawn, so it must not pull the near plane in.
    """
    depths = [-sum((float(p) - float(c)) * f for p, c, f in zip(point, location, forward)) for point in points]
    nearest_front, straddles = None, False
    for start in range(0, len(depths), 8):
        box = depths[start:start + 8]
        if side is not None and _beside(points[start:start + 8], location, side, box):
            continue
        low, high = min(box), max(box)
        if low > 0:
            nearest_front = low if nearest_front is None else min(nearest_front, low)
        elif high > 0:
            straddles = True
    return nearest_front, (max(depths) if depths else None), straddles


def free_clip(nearest_front, farthest, straddles, orthographic, complete=True):
    """``(clip_start, clip_end)`` of a free camera from its scene's box depths (box_depths).

    With nothing visible in front, Blender's new camera's range. ``complete`` is False when the scan was
    truncated (its boxes are a part: the far reach doubles and an unseen box may straddle).
    """
    if farthest is None or farthest <= 0:
        return (0.0 if orthographic else DEFAULT_CLIP_START), DEFAULT_CLIP_END
    reach = farthest * (FAR_MARGIN if complete else PARTIAL_FAR_MARGIN)
    if orthographic:  # Its precision is spread evenly over the range: it starts at its own plane.
        return 0.0, max(2.0 * reach, DEFAULT_CLIP_END)
    if complete and not straddles and nearest_front is not None:
        start = nearest_front * 0.5
    else:
        start = min(DEFAULT_CLIP_START, reach / SMALL_SCENE_RATIO)
        if nearest_front is not None:
            start = min(start, nearest_front * 0.5)
    start = max(start, reach / DEPTH_RESOLUTION)
    return start, max(2.0 * reach, start * FAR_RATIO)


def perspective_projection(half_width, half_height, near, far):
    """Blender's ``perspective_m4`` for a centred frustum with this half size at the near plane (row-major)."""
    return [[near / half_width, 0.0, 0.0, 0.0], [0.0, near / half_height, 0.0, 0.0],
            [0.0, 0.0, -(far + near) / (far - near), -2.0 * far * near / (far - near)], [0.0, 0.0, -1.0, 0.0]]


def orthographic_clip_projection(half_width, half_height, near, far):
    """Blender's ``orthographic_m4`` for a centred box between the near and far planes (row-major)."""
    return [[1.0 / half_width, 0.0, 0.0, 0.0], [0.0, 1.0 / half_height, 0.0, 0.0],
            [0.0, 0.0, -2.0 / (far - near), -(far + near) / (far - near)], [0.0, 0.0, 0.0, 1.0]]


def free_camera(item, aspect, nearest_front=None, farthest=None, straddles=False, complete=True):
    """World, view and projection matrices and details of one free camera item at a picture ``aspect``.

    ``item`` is validated (view_spec): location, look_at and lens (mm) or ortho_scale. The sensor or the
    orthographic scale spans the picture's longer side, as Blender's sensor fit AUTO does.
    """
    location, look_at = item["location"], item["look_at"]
    basis = look_at_basis(location, look_at)
    orthographic = "ortho_scale" in item
    near, far = free_clip(nearest_front, farthest, straddles, orthographic, complete)
    major = item["ortho_scale"] / 2.0 if orthographic else SENSOR_WIDTH_MM / 2.0 * near / item["lens"]
    half_width, half_height = (major, major / aspect) if aspect >= 1 else (major * aspect, major)
    world = camera_matrix(tuple(float(v) for v in location), basis, 0.0)
    projection = (orthographic_clip_projection(half_width, half_height, near, far) if orthographic
                  else perspective_projection(half_width, half_height, near, far))
    details = {"location": [float(v) for v in location], "look_at": [float(v) for v in look_at],
               "clip_start": near, "clip_end": far}
    if orthographic:
        details["ortho_scale"] = float(item["ortho_scale"])
    else:
        details.update(lens_mm=float(item["lens"]), sensor_width_mm=SENSOR_WIDTH_MM, sensor_fit="AUTO")
    # ``free``: the item's own values, from which a later REUSE of this camera clips its current scene again.
    return {"projection": "ORTHOGRAPHIC" if orthographic else "PERSPECTIVE", "matrix_world": world,
            "view_matrix": view_matrix(world), "projection_matrix": projection, "aspect": aspect,
            "details": details, "free": {key: item[key] for key in FREE_FIELDS if key in item}}
