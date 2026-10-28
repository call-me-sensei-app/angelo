# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Looking around a live view: each worker session's own view, moved by the artist's own gestures.

A live view never passes its events to the 3D view it covers (break-it P1-2 H1: Blender's window and interface
keymaps match left and right presses, X, Delete, I, E and K first and then hand them to the hidden 3D view of the
artist's document). So the live view navigates itself, on a view of its own per session (``View``): the artist's
own 3D view, its matrices and its saved state are never touched (P1-2 M8), and a camera locked to that view never
moves (P1-2 M3).

The gestures are Blender's defaults, read with the artist's own preferences (turntable sensitivity, wheel and drag
zoom inversion, 3-button emulation): middle drag orbits, Shift+middle pans, Ctrl+middle zooms; the trackpad's two
fingers orbit (Shift pans, Ctrl zooms) and a pinch zooms; the wheel zooms; numpad 1/3/7 (Ctrl: the opposite side)
look along an axis, 2/4/6/8 turn 15 degrees, 5 switches perspective, +/- zoom, period and Home frame the part.
Turntable only, orbiting around the view's centre: a look, not Blender's full navigation (no orbit around the
selection, zoom to the mouse position or auto depth).
"""

import math
from dataclasses import dataclass, field

from . import scroll_input

STEP = math.radians(15.0)   # Numpad 2/4/6/8.
WHEEL = 1.2                 # Distance factor per wheel step (Blender's own zoom step).
PIXEL_ZOOM = 0.005          # Distance factor per pixel of a Ctrl+middle drag or a trackpad zoom.
MIN_DISTANCE, MAX_DISTANCE = 1e-3, 1e6
DEFAULT = ((0.0, 0.0, 0.0), 12.0)   # An empty part: Blender's own start-up view distance.


@dataclass
class View:
    location: tuple = (0.0, 0.0, 0.0)
    rotation: tuple = None      # A quaternion (w, x, y, z); None: Blender's default orientation.
    distance: float = DEFAULT[1]
    perspective: bool = True
    auto: bool = True           # Never moved by the artist: framed again when the part grows.


@dataclass
class Drag:
    mode: str = ""              # "orbit", "pan" or "zoom"; "" while no button is held.
    button: str = ""
    last: tuple = (0, 0)


@dataclass
class Settings:
    """The artist's preferences that change how gestures move the view (Preferences > Navigation, Input)."""
    rotate: float = 0.0069813   # Radians per pixel, turntable.
    invert_wheel: bool = False
    invert_drag: bool = False
    emulate: bool = False       # Alt+left drag stands for the middle button.
    lens: float = 50.0
    ends: tuple = (0.01, 1000.0)


def default_rotation():
    """Blender's start-up view: from the front right, above."""
    from mathutils import Euler
    return Euler((math.radians(63.6), 0.0, math.radians(46.7)), 'XYZ').to_quaternion()


def rotation_of(view):
    from mathutils import Quaternion
    return Quaternion(view.rotation) if view.rotation is not None else default_rotation()


def view_matrix(view):
    from mathutils import Matrix, Vector
    rotation = rotation_of(view).to_matrix().to_4x4()
    return (Matrix.Translation(Vector(view.location)) @ rotation @ Matrix.Translation((0.0, 0.0, view.distance))
            ).inverted()


def half_extent(lens):
    """Half the frame's longer side at distance 1 (a 72 mm wide frame: the viewport's own zoom of 2)."""
    return 36.0 / lens


def _fit(aspect, lens):
    """Half the frame's shorter side at distance 1: what framing must fit (break-it P1-3 M4)."""
    return half_extent(lens) * min(aspect, 1.0 / aspect)


def projection_matrix(view, settings, width, height):
    """Blender's viewport projection for ``settings.lens``: the longer side of the region spans the frame."""
    from mathutils import Matrix
    aspect = max(1, width) / max(1, height)
    near, far = settings.ends
    if view.perspective:
        half_width = half_extent(settings.lens)
        half_height = half_width / aspect
        if aspect < 1.0:
            half_height, half_width = half_width, half_width * aspect
        sx, sy = 1.0 / half_width, 1.0 / half_height
        return Matrix(((sx, 0, 0, 0), (0, sy, 0, 0), (0, 0, (far + near) / (near - far), 2 * far * near / (near - far)),
                       (0, 0, -1, 0)))
    half_width = view.distance * half_extent(settings.lens)
    half_height = half_width / aspect
    if aspect < 1.0:
        half_height, half_width = half_width, half_width * aspect
    # The clip range is ``far`` deep, centred on the view's centre rather than the eye: zooming in never clips the
    # part's front (break-it P1-3 L3). Blender centres its own on the eye of a view it keeps far back.
    near, far = view.distance - far / 2, view.distance + far / 2
    return Matrix(((1 / half_width, 0, 0, 0), (0, 1 / half_height, 0, 0), (0, 0, -2 / (far - near),
                                                                            -(far + near) / (far - near)),
                   (0, 0, 0, 1)))


def frame(view, bounds, aspect, lens=50.0):
    """Frame world-space ``bounds`` (``((min), (max))``) whole, from the view's own direction: its bounding sphere
    fits the frame's shorter side, in perspective and in ortho alike."""
    from mathutils import Vector
    low, high = Vector(bounds[0]), Vector(bounds[1])
    radius = max(0.05, (high - low).length / 2)
    view.location = tuple((low + high) / 2)
    fit = _fit(aspect, lens)
    if view.perspective:
        distance = radius / math.sin(math.atan(fit))
    else:
        distance = radius / fit
    view.distance = min(MAX_DISTANCE, distance * 1.05)


def orbit(view, dx, dy, settings):
    """Turntable: around world Z for ``dx``, around the view's own X for ``dy`` (pixels)."""
    from mathutils import Quaternion, Vector
    rotation = rotation_of(view)
    yaw = Quaternion(Vector((0.0, 0.0, 1.0)), -dx * settings.rotate)
    pitch = Quaternion(rotation @ Vector((1.0, 0.0, 0.0)), dy * settings.rotate)
    view.rotation = tuple((yaw @ pitch @ rotation).normalized())
    view.auto = False


def pan(view, dx, dy, settings, size):
    """Move the view's centre in its own plane, the pointer keeping its place on the part (at the view's centre
    depth): the region's longer side spans the frame, on both axes alike."""
    from mathutils import Vector
    world_per_pixel = 2.0 * view.distance * half_extent(settings.lens) / max(1, *size)
    offset = rotation_of(view) @ Vector((-dx * world_per_pixel, -dy * world_per_pixel, 0.0))
    view.location = tuple(Vector(view.location) + offset)
    view.auto = False


def zoom(view, factor):
    view.distance = min(MAX_DISTANCE, max(MIN_DISTANCE, view.distance * factor))
    view.auto = False


def look_along(view, axis, opposite):
    """Numpad 1 (front), 3 (right), 7 (top); ``opposite``: back, left, bottom."""
    from mathutils import Euler
    angles = {"FRONT": (90, 0, 0), "BACK": (90, 0, 180), "RIGHT": (90, 0, 90), "LEFT": (90, 0, -90),
              "TOP": (0, 0, 0), "BOTTOM": (180, 0, 0)}
    name = {"FRONT": "BACK", "RIGHT": "LEFT", "TOP": "BOTTOM"}[axis] if opposite else axis
    view.rotation = tuple(Euler(tuple(math.radians(a) for a in angles[name]), 'XYZ').to_quaternion())
    view.auto = False


NUMPAD_AXES = {"NUMPAD_1": "FRONT", "NUMPAD_3": "RIGHT", "NUMPAD_7": "TOP"}
NUMPAD_TURNS = {"NUMPAD_4": (1, 0), "NUMPAD_6": (-1, 0), "NUMPAD_8": (0, 1), "NUMPAD_2": (0, -1)}
FRAME_KEYS = frozenset({"NUMPAD_PERIOD", "HOME"})


BUTTONS = frozenset({'LEFTMOUSE', 'MIDDLEMOUSE', 'RIGHTMOUSE', 'BUTTON4MOUSE', 'BUTTON5MOUSE', 'BUTTON6MOUSE',
                     'BUTTON7MOUSE'})


def handle(event, view, drag, settings, size, frame_part):
    """Move ``view`` for one event; True when the event was a navigation (taken), False otherwise.

    ``event`` has Blender's event fields, ``size`` is the region's (width, height); ``frame_part()`` frames the part
    (Home, numpad period). A drag ends on any button's release, and a press of another button ends it too (its
    release went somewhere else, break-it P1-3 M2): the press is then handled as itself."""
    kind, value = event.type, event.value
    if drag.mode:
        if kind in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            dx, dy = event.mouse_x - drag.last[0], event.mouse_y - drag.last[1]
            drag.last = (event.mouse_x, event.mouse_y)
            _drag_step(view, drag.mode, dx, dy, settings, size)
            return True
        if kind in BUTTONS and value == 'RELEASE':
            drag.mode = drag.button = ""
            return True
        if not (kind in BUTTONS and value == 'PRESS'):
            return True   # Keys and wheel turns while dragging belong to the drag.
        drag.mode = drag.button = ""
    middle = kind == 'MIDDLEMOUSE' or (kind == 'LEFTMOUSE' and settings.emulate and event.alt)
    if middle and value == 'PRESS':
        drag.mode = "pan" if event.shift else "zoom" if event.ctrl else "orbit"
        drag.button, drag.last = kind, (event.mouse_x, event.mouse_y)
        return True
    if kind in {'WHEELUPMOUSE', 'WHEELINMOUSE', 'WHEELDOWNMOUSE', 'WHEELOUTMOUSE'}:
        closer = kind in {'WHEELUPMOUSE', 'WHEELINMOUSE'}
        zoom(view, (1 / WHEEL) if closer != settings.invert_wheel else WHEEL)
        return True
    travel = scroll_input.gesture_travel(event)
    if travel is not None and kind == 'TRACKPADPAN':
        _drag_step(view, "pan" if event.shift else "zoom" if event.ctrl else "orbit", *travel, settings, size)
        return True
    if travel is not None:   # A pinch.
        zoom(view, math.exp(-travel[0] * PIXEL_ZOOM))
        return True
    if value != 'PRESS':
        return kind.startswith("NUMPAD_") or kind == "HOME"   # Their releases too: never to the view below.
    if kind in NUMPAD_AXES:
        look_along(view, NUMPAD_AXES[kind], event.ctrl)
        return True
    if kind in NUMPAD_TURNS:
        sx, sy = NUMPAD_TURNS[kind]
        orbit(view, sx * STEP / settings.rotate, sy * STEP / settings.rotate, settings)
        return True
    if kind == 'NUMPAD_5':
        view.perspective = not view.perspective
        view.auto = False
        return True
    if kind in {'NUMPAD_PLUS', 'NUMPAD_MINUS'}:
        zoom(view, (1 / WHEEL) if kind == 'NUMPAD_PLUS' else WHEEL)
        return True
    if kind in FRAME_KEYS:
        frame_part()
        return True
    return False


def _drag_step(view, mode, dx, dy, settings, size):
    if mode == "orbit":
        orbit(view, dx, dy, settings)
    elif mode == "pan":
        pan(view, dx, dy, settings, size)
    else:
        zoom(view, math.exp((dy if not settings.invert_drag else -dy) * PIXEL_ZOOM))


def settings_from(preferences, space):
    """The artist's own navigation preferences and their view's lens and clip range."""
    inputs = preferences.inputs
    return Settings(rotate=float(getattr(inputs, "view_rotate_sensitivity_turntable", 0.0069813) or 0.0069813),
                    invert_wheel=bool(getattr(inputs, "invert_zoom_wheel", False)),
                    invert_drag=bool(getattr(inputs, "invert_mouse_zoom", False)),
                    emulate=bool(getattr(inputs, "use_mouse_emulate_3_button", False)),
                    lens=float(getattr(space, "lens", 50.0) or 50.0),
                    ends=(float(getattr(space, "clip_start", 0.01)), float(getattr(space, "clip_end", 1000.0))))


@dataclass
class Sessions:
    """Every worker session's own view and the drag in progress."""
    views: dict = field(default_factory=dict)
    drag: Drag = field(default_factory=Drag)
