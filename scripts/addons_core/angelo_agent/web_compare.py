# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The preview page's Compare view: Angelo's viewport drawn from the page's own camera. Main thread.

The person presses Compare on the live web preview; the page sends its camera (``web_preview_server`` ``compare``)
and Angelo draws its 3D viewport from exactly that camera with ``angelo_view``'s LIT look: EEVEE with the scene's
own lights and World, in the scene's view transform, look and exposure (``view_capture``: the drawing viewport's
display is set for the one draw and put back). The page lays the picture over its own canvas for a wipe or a
difference. Nothing in the scene is written. Only the person starts it, from their page, so no agent approval
applies (``view_policy`` governs the agent's views).

The page's camera is three.js's: Y up, looking down its own -Z with +Y up, as Blender's cameras do. The glTF is
exported Y up, so a three.js point (x, y, z) is Blender's (x, -z, y): the camera's world matrix is that change of
axes applied on the left, and its view matrix the inverse. The projection is OpenGL's, as both use.
"""

import concurrent.futures
import math

MAX_SIDE = 1280      # The longest side of the picture, as the page's own capture for the agent.
MIN_SIDE = 16
# three.js world (Y up) -> Blender world (Z up), row-major.
Y_UP_TO_Z_UP = ((1.0, 0.0, 0.0, 0.0), (0.0, 0.0, -1.0, 0.0), (0.0, 1.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0))


class CompareError(ValueError):
    """The page's camera can't be drawn."""


def _number(value, name, low=-1e9, high=1e9):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) \
            or not low <= value <= high:
        raise CompareError("bad " + name)
    return float(value)


def size(request):
    """(width, height) of the picture: the page's canvas aspect, the longer side at most ``MAX_SIDE``."""
    width = _number(request.get("width"), "width", 1, 16384)
    height = _number(request.get("height"), "height", 1, 16384)
    scale = min(1.0, MAX_SIDE / max(width, height))
    return max(MIN_SIDE, round(width * scale)), max(MIN_SIDE, round(height * scale))


def camera(request):
    """{"view_matrix", "projection_matrix"} (row-major, Blender's world) of the page's camera ``request``:
    ``matrix`` (three.js ``matrixWorld.elements``, column-major), vertical ``fov`` in degrees, ``near``, ``far``."""
    elements = request.get("matrix")
    if not isinstance(elements, list) or len(elements) != 16:
        raise CompareError("bad matrix")
    columns = [_number(value, "matrix") for value in elements]
    three = [[columns[col * 4 + row] for col in range(4)] for row in range(4)]
    world = [[sum(Y_UP_TO_Z_UP[row][k] * three[k][col] for k in range(4)) for col in range(4)] for row in range(4)]
    fov = math.radians(_number(request.get("fov"), "fov", 1.0, 179.0))
    near = _number(request.get("near"), "near", 1e-6, 1e9)
    far = _number(request.get("far"), "far", near * 1.0001, 1e12)
    width, height = size(request)
    half_height = near * math.tan(fov / 2.0)
    half_width = half_height * width / height
    from .view_framing import perspective_projection, view_matrix
    return {"view_matrix": view_matrix(world), "projection_matrix": perspective_projection(half_width, half_height,
                                                                                           near, far)}


def capture(window, request):
    """A PNG of the window's 3D viewport drawn LIT from the page's camera ``request``. Raises CompareError or
    RuntimeError (no 3D viewport, an interaction under way)."""
    import bpy
    from .input_policy import tool_running
    from .view_capture import _draw_all, _draw_area, _view3d_areas
    from .view_png import encode_png
    if tool_running(window):
        raise RuntimeError("Finish the current Blender interaction first")
    seen = camera(request)
    width, height = size(request)
    area = _draw_area(_view3d_areas(window))
    region = next(item for item in area.regions if item.type == "WINDOW")
    with bpy.context.temp_override(window=window, area=area, region=region):
        window.view_layer.update()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="AngeloComparePNG") as pool:
            futures, _unavailable = _draw_all(window, area, "LIT", [(0, seen, width, height, encode_png)], pool)
            return futures[0].result()
