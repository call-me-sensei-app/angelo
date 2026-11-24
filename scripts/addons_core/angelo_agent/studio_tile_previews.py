# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio's attachment tiles get their pictures here, prepared away from drawing (1.0.8 audit P2).

A draw callback only asks: ``texture(path)`` returns a ready ``Preview`` or None, and on None the path is queued.
Nothing in a draw loads, decodes, resizes or uploads. A main-thread timer (``pump``) works through the queue
within ``BUDGET_S`` per tick, the Angelo Library's thumbnail pattern (``library_host``): it loads the file as a
private image (``check_existing=False``, removed at once, never one of the person's), reads its stored pixels
(Non-Color, so the texture holds what the screen shows and is drawn with the plain ``IMAGE`` shader), shrinks them
to at most ``SIDE`` pixels a side with an area filter (``bounded``), and uploads that. Images and movie posters
(a movie's first frame) take the same path, so a poster is never bigger than a picture. Ready textures are kept
least recently drawn first within ``GPU_BYTES`` of GPU memory; a file that can't be shown is remembered and drawn
as the empty tile. The cache is released with Media Studio (``clear``).
"""

from . import edit_tool_hold
from .ui_text import n_
import time
from array import array
from collections import OrderedDict, deque
from dataclasses import dataclass

SIDE = 384                      # The longest side of a tile's picture, in pixels (a tile is far smaller on screen).
TEXEL_BYTES = 8                 # RGBA16F.
GPU_BYTES = 24 * 1024 * 1024    # Kept textures' GPU memory: about 40 full-size 16:9 tiles.
BUDGET_S = 0.010                # Main-thread time a timer tick may spend preparing pictures.
TICK_S = 0.05
PIXEL_LIMIT = 8192 * 8192       # A file larger than this is not read at all: its header says so first (markup S1).
# Why a picture can't be read: English for the log and the agent; the viewer shows it translated (markup S2).
TOO_MANY_PIXELS = n_("This picture has more pixels than Angelo reads (8,192 \u00d7 8,192 at most)")
NO_PIXELS = n_("This picture has no pixels Angelo can read")
UNREADABLE = n_("Angelo can't read this picture")


@dataclass
class Preview:
    texture: object
    width: int
    height: int

    @property
    def bytes(self):
        return self.width * self.height * TEXEL_BYTES


def bounded(pixels, width, height, side=SIDE):
    """``(pixels, width, height)`` of RGBA float ``pixels`` (flat, rows bottom first) shrunk to at most ``side``
    pixels a side by averaging whole ``step`` x ``step`` blocks (an area filter: no aliasing on fine detail).
    Pictures already within ``side`` are returned as they are."""
    import numpy as np
    step = -(-max(width, height) // side)   # ceil
    if step <= 1:
        return pixels, width, height
    grid = np.asarray(pixels, dtype=np.float32).reshape(height, width, 4)
    rows, cols = height // step, width // step
    if rows == 0 or cols == 0:   # A sliver (1 x 5000): keep every step-th pixel instead.
        grid = grid[::step, ::step]
        return grid.reshape(-1), grid.shape[1], grid.shape[0]
    grid = grid[:rows * step, :cols * step].reshape(rows, step, cols, step, 4).mean(axis=(1, 3))
    return grid.astype(np.float32).reshape(-1), cols, rows


def decode(path, side=SIDE):
    """The picture (or a movie's first frame) at ``path`` as bounded stored pixels, turned upright as a camera's EXIF
    orientation says (``picture_size.orientation``): ``(pixels, width, height)``, at most ``side`` pixels a side (the
    picture viewer, ``markup_overlay``, asks for larger ones). A PNG, JPEG or WebP over ``PIXEL_LIMIT`` pixels is
    refused by its header before Blender decodes a pixel (re-review of markup 4e712847d3b, S1: a 0.8 MiB flat 16K
    PNG cost 1 GiB and seconds on the main thread, at every open). Main thread only: it loads a private image and
    removes it before returning."""
    import bpy
    import numpy as np
    from . import picture_size
    try:
        stored = picture_size.header_size(path)
    except OSError:   # Blender's own read says why.
        stored = None
    if stored is None and picture_size.signed(path):   # A header that can't be read fails closed (re-review S7).
        raise ValueError(UNREADABLE)
    if stored is not None and not all(stored):   # A zero side: no pixels, not too many (N6).
        raise ValueError(NO_PIXELS)
    if stored is not None and stored[0] * stored[1] > PIXEL_LIMIT:
        raise ValueError(TOO_MANY_PIXELS)
    try:
        image = bpy.data.images.load(str(path), check_existing=False)
    except RuntimeError:   # Its text names the file's full path: never shown (re-review of markup 4e712847d3b, S2).
        raise ValueError(UNREADABLE) from None
    try:
        from . import color_spaces
        color_spaces.mark_stored(image.colorspace_settings)   # Stored values: what the screen shows, drawn with IMAGE.
        width, height = image.size
        if not 0 < width * height <= PIXEL_LIMIT:
            raise ValueError(TOO_MANY_PIXELS if width * height else NO_PIXELS)
        step = -(-max(width, height) // side)   # ceil
        if step > 1 and width // step and height // step:   # Scaled in Blender's own buffer first: never more than
            image.scale(width // step, height // step)     # ``side`` a side expanded to floats (markup S4: an 8K
            width, height = image.size                     # picture was 1 GiB of floats on the main thread).
        pixels = np.empty(width * height * 4, dtype=np.float32)
        image.pixels.foreach_get(pixels)
    finally:
        bpy.data.images.remove(image)
    return upright(*bounded(pixels, width, height, side), picture_size.orientation(path))


def upright(pixels, width, height, turn):
    """``(pixels, width, height)`` of flat RGBA rows (bottom first) turned as EXIF orientation ``turn`` says: 2 to 4
    mirror or half-turn, 5 to 8 swap the sides (transpose, quarter turns, transverse). 1 returns them as they are."""
    if turn == 1:
        return pixels, width, height
    import numpy as np
    top = np.asarray(pixels, dtype=np.float32).reshape(height, width, 4)[::-1]   # Rows top first, as EXIF counts.
    if turn in (2, 3, 7):   # Mirrored left to right first (2, 3), or turned about the other diagonal (7).
        top = top[:, ::-1]
    if turn in (3, 4, 7):   # Upside down (3 with 2's mirror: a half turn).
        top = top[::-1]
    if turn in (5, 7):   # Transposed: rows become columns.
        top = top.transpose(1, 0, 2)
    elif turn == 6:
        top = np.rot90(top, -1)   # A quarter turn clockwise.
    elif turn == 8:
        top = np.rot90(top, 1)   # A quarter turn anticlockwise.
    seen = top   # Branches, never a callable taken from a table (test_native_guard_work).
    seen = np.ascontiguousarray(seen[::-1])
    return seen.reshape(-1), seen.shape[1], seen.shape[0]


def upload(pixels, width, height):
    """A GPU texture of bounded stored pixels (main thread, from a timer: never inside a draw)."""
    import gpu
    data = array('f')
    data.frombytes(memoryview(pixels).cast("B") if not isinstance(pixels, array) else pixels.tobytes())
    return gpu.types.GPUTexture((width, height), format='RGBA16F',
                                data=gpu.types.Buffer('FLOAT', [width * height * 4], data))


class Previews:
    """The prepared pictures and the queue of those asked for. ``decoder`` and ``uploader`` stand in for Blender
    in tests; ``wake`` starts the timer (``on_ready`` redraws Media Studio)."""

    def __init__(self, decoder=decode, uploader=upload, wake=None, on_ready=None, budget=GPU_BYTES):
        self._decode, self._upload, self._wake, self._on_ready = decoder, uploader, wake, on_ready
        self.budget, self.used = budget, 0
        self._ready = OrderedDict()   # path -> Preview, least recently drawn first
        self._failed, self._queue, self._queued = set(), deque(), set()

    def texture(self, path):
        """Draw-safe: the ready Preview for ``path``, or None (it is queued; nothing is read here)."""
        path = str(path)
        found = self._ready.get(path)
        if found is not None:
            self._ready.move_to_end(path)
            return found
        if path not in self._failed and path not in self._queued:
            self._queue.append(path)
            self._queued.add(path)
            if self._wake is not None:
                self._wake()
        return None

    def pending(self):
        return bool(self._queue)

    def pump(self, deadline):
        """Prepare queued pictures until ``deadline`` (``time.perf_counter``); True when one became ready."""
        ready = False
        while self._queue and time.perf_counter() < deadline:
            path = self._queue.popleft()
            self._queued.discard(path)
            try:
                pixels, width, height = self._decode(path)
                self._keep(path, Preview(self._upload(pixels, width, height), width, height))
                ready = True
            except (OSError, RuntimeError, ValueError, TypeError):
                self._failed.add(path)
        if ready and self._on_ready is not None:
            self._on_ready()
        return ready

    def _keep(self, path, preview):
        old = self._ready.pop(path, None)
        self.used += preview.bytes - (old.bytes if old else 0)
        self._ready[path] = preview
        while len(self._ready) > 1 and self.used > self.budget:
            _path, evicted = self._ready.popitem(last=False)
            self.used -= evicted.bytes

    def clear(self):
        self._ready.clear()
        self._failed.clear()
        self._queue.clear()
        self._queued.clear()
        self.used = 0


@edit_tool_hold.guarded(waiting=lambda: _previews is not None and _previews.pending())   # Pictures wait.
def _tick():
    if _previews is None:
        return None
    _previews.pump(time.perf_counter() + BUDGET_S)
    return TICK_S if _previews.pending() else None


def _wake():
    import bpy
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=0.0)


def _redraw():
    from . import studio
    studio.redraw()


_previews = None


def previews():
    """The process's attachment pictures (created on first use)."""
    global _previews
    if _previews is None:
        _previews = Previews(wake=_wake, on_ready=_redraw)
    return _previews


def clear():
    """Release every prepared picture and stop the timer (Media Studio's unregister)."""
    global _previews
    try:
        import bpy
        if bpy.app.timers.is_registered(_tick):
            bpy.app.timers.unregister(_tick)
    except (ImportError, AttributeError):
        pass
    if _previews is not None:
        _previews.clear()
    _previews = None
