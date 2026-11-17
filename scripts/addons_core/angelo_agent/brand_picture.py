# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A picture Angelo's own surfaces draw (the welcome banner, the showcase still): on the GPU while a surface shows
it, never held as a float copy beside its texture.

* ``prepare`` reads the file outside drawing with ``imbuf``, which adds and removes no image datablock (a datablock
  added while Preferences is open closes a dialog over it). Its pixels are kept only until the first draw.
* ``texture`` (in a draw) makes the GPU texture from them on first use and drops the pixels: 16 bytes a pixel, 11.7
  MB for the banner and 23 MB for the showcase, held for the whole session before 2.0.0.
* ``show`` / ``hide``: a surface names itself while it shows the picture; when the last one hides, ``release`` drops
  the texture too, and the next ``prepare`` reads the file again.
* ``ready`` (the size is known: the picture can be drawn) is kept apart from the pixels, which go at the first draw.

Both pictures are 8-bit and drawn as stored: the stored bytes over 255, as ``Image.pixels`` gives them for a data
colour space, never a colour-managed conversion.
"""

from pathlib import Path


class Picture:
    def __init__(self, path):
        self.path = Path(path)
        self.size = None
        self.failed = False
        self._pixels = None
        self._texture = None
        self._shown = set()

    @property
    def ready(self):
        """The picture can be drawn: its size is known and its pixels or texture are held."""
        return self.size is not None

    def holds_pixels(self):
        """A float copy of the picture is in memory (only between ``prepare`` and the first draw)."""
        return self._pixels is not None

    def prepare(self):
        """Read the file once, outside drawing. A missing or undecodable file leaves the surface's plain fallback,
        never an error."""
        if self.size is not None or self.failed:
            return
        try:
            self._pixels, self.size = read(self.path)
        except (OSError, RuntimeError, ValueError, ImportError):
            self._pixels, self.size, self.failed = None, None, True

    def show(self, surface):
        """``surface`` shows the picture from now on (read now, outside drawing, if it is not held)."""
        self._shown.add(surface)
        self.prepare()

    def hide(self, surface):
        """``surface`` no longer shows it; with no surface left, the texture goes too."""
        self._shown.discard(surface)
        if not self._shown:
            self.release()

    def shown_by(self):
        return frozenset(self._shown)

    def texture(self, gpu):
        """The GPU texture, made on the first draw from the pixels ``prepare`` read, which are then dropped. None
        when the picture is not held (``prepare`` first)."""
        if self._texture is None and self._pixels is not None:
            width, height = self.size
            self._texture = gpu.types.GPUTexture((width, height), format='RGBA16F', data=gpu.types.Buffer(
                'FLOAT', [width * height * 4], self._pixels))
            self._pixels = None
        return self._texture

    def release(self):
        """Drop the pixels and the texture, and forget which surfaces showed it (the add-on unloading); the next
        ``prepare`` reads the file again."""
        self._pixels = self._texture = self.size = None
        self.failed = False
        self._shown.clear()


def read(path):
    """``(pixels, (width, height))``: the file's stored 8-bit values over 255, RGBA, rows from the bottom (as
    ``Image.pixels``), as one contiguous float32 array. No image datablock is added."""
    import imbuf
    import numpy
    image = imbuf.load(str(path))
    try:
        width, height = image.size
        if not width or not height:
            raise ValueError("%s did not decode" % Path(path).name)
        if image.buffer_type != 'BYTE':
            raise ValueError("%s is not an 8-bit picture" % Path(path).name)
        with image.with_buffer() as stored:
            values = numpy.asarray(stored)   # (height, width, channels), bytes.
            channels = min(4, values.shape[2])
            pixels = numpy.ones((height, width, 4), dtype=numpy.float32)
            pixels[..., :channels] = values[..., :channels]
    finally:
        image.free()
    pixels[..., :channels] *= numpy.float32(1.0 / 255.0)
    return pixels.reshape(-1), (width, height)
