# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Draws the chat's marble wait (``marble_wait``) with the chat's GPU canvas: the hero mark and the live row's icon.

Two sizes of one drawing, both from the turn's ``Wait``:

* the **hero**, a block of its own under the turn's request (``with_hero``): ``HERO`` pixels while the turn
  plans, shrinking to ``HERO_COMPACT`` once its first step runs, gone when the turn ends;
* the **icon**, ``ICON`` pixels on the live row where its spinner was (``chat_rows``); a voice turn, and the
  first frame of a turn before the timer has seen it, keep the spinner.

Everything is triangles through Blender's built-in ``SMOOTH_COLOR`` shader, in mark units under one matrix:
the raven's parts are batched once per process; the standing stone once per fallen chip (``_solids``); the
chips in flight and the dust are a few dozen triangles built per frame. In a right-to-left interface the mark
is drawn upright where the mirrored layout puts it, as pictures are (``canvas_rtl``). While the chat's full draw
is being kept (``marble_frames``), a mark is only *placed* (``placing``: its centre, size, matrix and scissor)
and ``replay`` draws it over the kept picture, then and on every marble frame. Each draw records what it drew
in the chat's view state (``marble_drawn``) and its cost (``draw_seconds``) for the GUI fixtures.
"""

import math
import time

from . import canvas_rtl, marble_host, marble_mark
from .marble_carving import Mesh
from .marble_mark import SIZE
from .marble_wait import ICON, Pose, frame

ROLE = "marble_hero"
HERO_PAD = 20  # The hero block is its mark plus this much air.
STILL = Pose(0.0, 0.0, 1.0, 0.0)
_shader = None
_marks = {}  # part -> GPUBatch, for the process.
_solids = {}  # chip count -> (chips fallen, GPUBatch of the standing chips).
placing = []  # While marble_frames keeps the chat's picture: one list collecting the marks' places.
draw_seconds = 0.0  # Time spent in ``_draw`` (fixtures divide it by ``draw_calls``).
draw_calls = 0
paint_errors = 0  # Painters (the live row's status line) that raised: each loses only its frame (fixtures read it).


def clear():
    global _shader
    _shader = None
    _marks.clear()
    _solids.clear()


def _batch(mesh):
    from gpu_extras.batch import batch_for_shader
    return batch_for_shader(_shader, 'TRIS', {"pos": mesh.positions, "color": mesh.colours}, indices=mesh.indices)


def _mark(part):
    batch = _marks.get(part)
    if batch is None:
        positions, colours, triangles = marble_mark.mesh(part)
        batch = _marks[part] = _batch(Mesh([(x, y, 0.0) for x, y in positions], colours, triangles))
    return batch


def _solid(block, mesh):
    cached = _solids.get(block.total)
    if cached is None or cached[0] != block.removed:
        cached = _solids[block.total] = (block.removed, _batch(mesh))
    return cached[1]


def _turn(degrees, centre):
    import gpu
    from mathutils import Matrix
    gpu.matrix.translate(centre)
    gpu.matrix.multiply_matrix(Matrix.Rotation(math.radians(degrees), 4, 'Z'))
    gpu.matrix.translate((-centre[0], -centre[1]))


def _raven(pose):
    """The mark with its pose: tilted about its centre, the eyes raised and blinking about their own."""
    import gpu
    with gpu.matrix.push_pop():
        if pose.tilt:
            _turn(pose.tilt, (SIZE / 2, SIZE / 2))
        for part in ("helmet", "feather", "visor"):
            _mark(part).draw(_shader)
        for part in ("eye_left", "eye_right"):
            x, y = marble_mark.eye_centre(part)
            with gpu.matrix.push_pop():
                gpu.matrix.translate((x + pose.eye_dx, y + pose.eye_dy))
                gpu.matrix.scale((1.0, pose.eye_open))
                gpu.matrix.translate((-x, -y))
                _mark(part).draw(_shader)
        for part in ("beak", "beak_shade"):
            _mark(part).draw(_shader)


def _draw(wait, block, now, cx, cy, size, small):
    """The mark ``size`` pixels square centred on (``cx``, ``cy``), carved as ``block`` is at ``now``."""
    global _shader, draw_seconds, draw_calls
    import gpu
    started = time.perf_counter()
    if _shader is None:
        _shader = gpu.shader.from_builtin('SMOOTH_COLOR')
    gpu.state.blend_set('ALPHA')
    rect = _region_rect(cx, cy, size)
    with gpu.matrix.push_pop():
        gpu.matrix.translate((cx, cy))
        if canvas_rtl.mirrored():
            gpu.matrix.scale((-1.0, 1.0))  # Upright where the mirrored layout puts it, like a picture.
        gpu.matrix.scale((size / SIZE, size / SIZE))
        gpu.matrix.translate((-SIZE / 2, -SIZE / 2))
        drawn = frame(wait, block, now, small)
        if block.removed or block.flights:  # A whole block hides the raven: nothing to draw under it.
            _raven(drawn.pose)
        if drawn.solid is not None:
            with gpu.matrix.push_pop():
                gpu.matrix.translate(drawn.offset)
                _solid(block, drawn.solid).draw(_shader)
        for mesh in (drawn.flying, drawn.dust):
            if mesh.indices:
                _batch(mesh).draw(_shader)
    draw_seconds += time.perf_counter() - started
    draw_calls += 1
    return rect


def _region_rect(cx, cy, size):
    """The mark's square in region pixels (the canvas's scale and mirror applied), for the fixtures."""
    import gpu
    from mathutils import Vector
    matrix = gpu.matrix.get_model_view_matrix()
    half = size / 2
    corners = [matrix @ Vector((cx + d, cy + d, 0.0, 1.0)) for d in (-half, half)]
    left, right = sorted((corners[0].x, corners[1].x))
    return (round(left, 1), round(min(corners[0].y, corners[1].y), 1), round(right - left, 1),
            round(abs(corners[1].y - corners[0].y), 1))


def _note(state, kind, wait, block, size, now, rect):
    """What the wait drew, for the GUI fixtures; never read by the add-on."""
    pose = wait.pose(now, kind == "icon")
    state.setdefault("marble_drawn", {})[kind] = {
        "phase": wait.phase, "size": round(size, 2), "removed": block.removed, "total": block.total,
        "flying": sum(1 for flight in block.flights if now - flight.start >= 0), "taps": wait.taps,
        "reduced": wait.reduced, "eye_open": round(pose.eye_open, 3), "tilt": round(pose.tilt, 2),
        "look": round(pose.eye_dy, 2), "at": now, "rect": rect}
    state["marble_draws"] = state.get("marble_draws", 0) + 1


def with_hero(blocks, controller):
    """``blocks`` (chat_view's block tuples) with the hero after the running turn's request, when it has one."""
    wait = marble_host.current(controller)
    if wait is None or wait.request is None:
        return blocks
    for index, block in enumerate(blocks):
        if block[0] == "user" and block[1] is wait.request:
            size = wait.hero_size(time.monotonic())
            hero = (ROLE, {"wait": wait, "size": size}, (), size + HERO_PAD, ROLE, 0)
            return blocks[:index + 1] + [hero] + blocks[index + 1:]
    return blocks


def _failed(error):
    """A draw that raised: the animation stops for this session, and the chat draws on without it."""
    marble_host.last_error = "draw: %s" % error
    marble_host.tracker.waits.clear()


def _place(state, kind, wait, cx, cy, size):
    """Draw a mark now, or, inside ``marble_frames.keep``, note where it goes for drawing over the kept picture."""
    place = {"state": state, "kind": kind, "wait": wait, "centre": (cx, cy), "size": size}
    if placing:
        import gpu
        place.update(matrix=gpu.matrix.get_model_view_matrix().copy(), scissor=tuple(gpu.state.scissor_get()))
        placing[-1].append(place)
        return
    replay([place], time.monotonic())


def paint(state, painter):
    """Draw ``painter(now)`` now or, while the chat's picture is kept, over it on every marble frame.

    For live-row parts that change with time and nothing else (the clock chip, ``chat_rows._live_clock``): a frame
    then costs the painter, not a layout of the whole chat.
    """
    place = {"state": state, "kind": "paint", "paint": painter}
    if placing:
        import gpu
        place.update(matrix=gpu.matrix.get_model_view_matrix().copy(), scissor=tuple(gpu.state.scissor_get()))
        placing[-1].append(place)
        return
    replay([place], time.monotonic())


def replay(places, now):
    """Draw placed marks at ``now``: each under the matrix and scissor it was placed with."""
    global paint_errors
    import gpu
    for place in places:
        painter = place.get("paint")
        wait, small = place.get("wait"), place["kind"] == "icon"
        block = None if painter else wait.icon if small else wait.hero
        gpu.matrix.push()
        try:
            if "matrix" in place:
                gpu.matrix.load_matrix(place["matrix"])
                gpu.state.scissor_test_set(True)
                gpu.state.scissor_set(*place["scissor"])
            if painter:
                painter(now)
                continue
            rect = _draw(wait, block, now, *place["centre"], place["size"], small)
        except Exception as error:  # Never let decoration take the chat's own draw (and its hit areas) down.
            if painter:  # A live-row painter that failed misses this frame; the raven and its timer carry on.
                paint_errors += 1
                continue
            _failed(error)
            continue
        finally:
            gpu.matrix.pop()
            if "matrix" in place:
                gpu.state.scissor_test_set(False)
        _note(place["state"], place["kind"], wait, block, place["size"], now, rect)
    gpu.state.blend_set('NONE')


def draw_hero(message, top, bottom, width, state):
    _place(state, "hero", message["wait"], width / 2, (top + bottom) / 2, message["size"])


def draw_still(cx, cy, size, pose=STILL):
    """The raven alone at ``pose``, ``size`` pixels square centred on (``cx``, ``cy``): no stone, no chips, no wait.

    For Angelo in the 3D view's corner while the chat is hidden (``chat_launcher``): the same process-wide batches as
    the marble (``_mark``), so a draw is a handful of cached batch draws."""
    global _shader
    import gpu
    if _shader is None:
        _shader = gpu.shader.from_builtin('SMOOTH_COLOR')
    gpu.state.blend_set('ALPHA')
    with gpu.matrix.push_pop():
        gpu.matrix.translate((cx, cy))
        gpu.matrix.scale((size / SIZE, size / SIZE))
        gpu.matrix.translate((-SIZE / 2, -SIZE / 2))
        _raven(pose)


def draw_icon(x, y, state):
    """The live row's raven centred on (``x``, ``y``): True when drawn or placed, False to keep the spinner."""
    from . import ui
    controller = ui._controller
    wait = marble_host.current(controller) if controller is not None else None
    if wait is None:
        return False
    _place(state, "icon", wait, x, y, ICON)
    return True

