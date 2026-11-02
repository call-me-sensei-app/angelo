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

import time
from array import array
from collections import OrderedDict, deque
from dataclasses import dataclass

SIDE = 384                      # The longest side of a tile's picture, in pixels (a tile is far smaller on screen).
TEXEL_BYTES = 8                 # RGBA16F.
GPU_BYTES = 24 * 1024 * 1024    # Kept textures' GPU memory: about 40 full-size 16:9 tiles.
BUDGET_S = 0.010                # Main-thread time a timer tick may spend preparing pictures.
TICK_S = 0.05
PIXEL_LIMIT = 8192 * 8192       # A file larger than this is not read at all.


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


def decode(path):
    """The picture (or a movie's first frame) at ``path`` as bounded stored pixels: ``(pixels, width, height)``.
    Main thread only: it loads a private image and removes it before returning."""
    import bpy
    import numpy as np
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        image.colorspace_settings.name = 'Non-Color'   # Stored values: what the screen shows, drawn with IMAGE.
        width, height = image.size
        if not 0 < width * height <= PIXEL_LIMIT:
            raise ValueError("no usable pixels")
        pixels = np.empty(width * height * 4, dtype=np.float32)
        image.pixels.foreach_get(pixels)
    finally:
        bpy.data.images.remove(image)
    return bounded(pixels, width, height)


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
