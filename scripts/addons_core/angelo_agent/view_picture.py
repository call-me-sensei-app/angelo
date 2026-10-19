# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The pixels a model is shown, from any picture file: bounded, cropped, scaled and opaque.

Every picture source of ``angelo_view`` that is not a viewport draw ends here:
a look's own render, a finished job's file, the Render Result saved once
(``view_render``, ``view_finished``). A source hands over a file; ``scaled``
returns the PNG the model gets and the facts its result states. A further
source (an image data-block, a UV layout, a strip of frames) needs only to
produce its file.

* **Bounded before decoding.** ``header`` reads the size and depth from the
  file's first bytes (PNG, JPEG, OpenEXR); ``admit`` refuses a file that is too
  large to decode, so no unbounded allocation follows a model's request.
* **No data-block.** ``imbuf`` decodes, crops and box-filters outside Blender's
  Main: nothing is added to the file, the undo history or the Image Editor.
* **A part at full detail.** ``region`` is [left, top, right, bottom] in
  fractions of the picture from its top-left corner. The part is cropped from
  the source's own pixels before any scaling, so detail a downscaled frame
  loses is there.
* **Transparency is shown, not dropped.** A PNG handed to a model must be
  opaque: providers flatten alpha onto a colour of their own choosing. Pixels
  with alpha are composited over a grey checkerboard, as Blender's Image
  Editor shows them, and the fraction of such pixels is reported, so a
  transparent background or a hole cannot pass for black or for white.
* **Display values.** An 8-bit file is delivered as its pixels are. A 16-bit
  file is converted to 8 bits. A scene-linear file (OpenEXR) cannot be shown
  without a view transform: ``view_finished`` first has Blender save it through
  the scene's, and says so.
* **Stored values.** A file of data, not colours (a baked normal or position
  map), gets no transform at all: ``scaled(..., stored=True)`` shows an
  OpenEXR file's values from 0 to 1 as they are and reports the fraction of
  pixels outside that range, which are clipped.

Main thread (``imbuf`` and NumPy are Blender's); model-facing text is English.
"""

import struct

from .view_framing import output_size
from .view_png import encode_png

MAX_BYTES = 256 * 1024 * 1024
MAX_PIXELS = 68_000_000        # 8 bits a channel: four bytes a pixel decoded. An 8K frame is 33 million.
MAX_FLOAT_PIXELS = 36_000_000  # 16 bits or float: sixteen bytes a pixel decoded.
MIN_REGION_PIXELS = 8          # A region smaller than this in the source's pixels shows nothing.
CHECKER = (204, 153, 16)       # Light grey, dark grey, square size in delivered pixels.
LINEAR = "LINEAR"              # A header depth: scene-linear float values (OpenEXR), not display values.
_JPEG_SIZE_MARKERS = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}


def _exr_size(handle):
    """The display window of an OpenEXR file's first part (what Blender shows), or None."""
    data = handle.read(64 * 1024)
    offset = 8
    while offset < len(data) and data[offset]:
        name_end = data.find(b"\0", offset)
        type_end = data.find(b"\0", name_end + 1)
        if name_end < 0 or type_end < 0 or type_end + 5 > len(data):
            return None
        size = struct.unpack_from("<i", data, type_end + 1)[0]
        if data[offset:name_end] == b"displayWindow" and data[name_end + 1:type_end] == b"box2i" and size == 16:
            left, top, right, bottom = struct.unpack_from("<4i", data, type_end + 5)
            return right - left + 1, bottom - top + 1
        if size < 0:
            return None
        offset = type_end + 5 + size
    return None


def header(path):
    """``(width, height, depth)`` read from a file's first bytes; depth is 8, 16 or LINEAR. None for another file."""
    with open(path, "rb") as handle:
        head = handle.read(26)
        if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR" and len(head) >= 25:
            return struct.unpack(">II", head[16:24]) + (head[24],) if head[24] in (8, 16) else None
        if head[:4] == b"\x76\x2f\x31\x01":
            handle.seek(0)
            size = _exr_size(handle)
            return size + (LINEAR,) if size else None
        if head[:2] != b"\xff\xd8":
            return None
        handle.seek(2)
        for _segment in range(256):
            marker = handle.read(4)
            if len(marker) < 4 or marker[0] != 0xFF:
                return None
            length = struct.unpack(">H", marker[2:4])[0]
            if marker[1] in _JPEG_SIZE_MARKERS:
                frame = handle.read(5)
                if len(frame) < 5 or frame[0] != 8:
                    return None
                height, width = struct.unpack(">HH", frame[1:5])
                return width, height, 8
            handle.seek(length - 2, 1)
    return None


def admit(path, label):
    """``(width, height, depth)`` of a picture file that may be decoded; RuntimeError says why another may not."""
    try:
        found = header(path) if path.stat().st_size <= MAX_BYTES else None
    except OSError:
        found = None
    if found is None or found[0] < 1 or found[1] < 1:
        raise RuntimeError("%s is not a PNG, JPEG or OpenEXR picture of at most %d MB that can be shown"
                           % (label, MAX_BYTES // (1024 * 1024)))
    limit = MAX_PIXELS if found[2] == 8 else MAX_FLOAT_PIXELS
    if found[0] * found[1] > limit:
        raise RuntimeError("%s is %dx%d: more than the %d million pixels of this kind that can be shown"
                           % (label, found[0], found[1], limit // 1_000_000))
    return found


def pixel_box(region, width, height):
    """The region as whole pixels ``(left, top, right, bottom)`` (right and bottom exclusive) of a width x height picture."""
    left, top = int(region[0] * width), int(region[1] * height)
    right, bottom = max(left + 1, round(region[2] * width)), max(top + 1, round(region[3] * height))
    return left, top, min(right, width), min(bottom, height)


def flatten(pixels, premultiplied=False):
    """Opaque RGBA rows for RGBA rows with alpha: ``(pixels, fraction of pixels that had transparency)``.

    Transparent and half-transparent pixels are composited over a grey
    checkerboard. Fully opaque input is returned as it is.
    """
    import numpy
    alpha = pixels[..., 3]
    if int(alpha.min()) == 255:
        return pixels, 0.0
    height, width = alpha.shape
    light, dark, square = CHECKER
    cells = (numpy.arange(height)[:, None] // square + numpy.arange(width)[None, :] // square) & 1
    board = numpy.where(cells, dark, light).astype(numpy.float32)[..., None]
    cover = alpha.astype(numpy.float32)[..., None] / 255.0
    colour = pixels[..., :3].astype(numpy.float32)
    mixed = (colour if premultiplied else colour * cover) + board * (1.0 - cover)
    result = numpy.empty_like(pixels)
    result[..., :3] = numpy.clip(mixed + 0.5, 0.0, 255.0).astype(numpy.uint8)
    result[..., 3] = 255
    return result, round(float((alpha < 255).mean()), 4)


def stored_bytes(values):
    """Float RGBA rows as 8-bit rows with no colour transform: ``(rows, fraction of pixels outside 0 to 1)``."""
    import numpy
    colour = numpy.nan_to_num(values[..., :3], nan=0.0, posinf=2.0, neginf=-1.0)
    outside = ((colour < 0.0) | (colour > 1.0)).any(axis=-1)
    rows = numpy.empty(values.shape[:2] + (4,), dtype=numpy.uint8)
    rows[..., :3] = numpy.clip(colour * 255.0 + 0.5, 0.0, 255.0).astype(numpy.uint8)
    rows[..., 3] = (numpy.clip(numpy.nan_to_num(values[..., 3], nan=1.0) * 255.0 + 0.5, 0.0, 255.0).astype(numpy.uint8)
                    if values.shape[-1] > 3 else 255)
    return rows, round(float(outside.mean()), 4)


def scaled(path, region=None, long_side=None, *, stored=False):
    """The picture file at ``path`` as the PNG a model gets: ``(png bytes, facts)``.

    ``region`` crops first; ``long_side`` then scales the longer side down to
    at most that many pixels (never up). The file is an 8- or 16-bit PNG or a
    JPEG whose size ``admit`` accepted; with ``stored`` also an OpenEXR file
    of data, whose values are shown without a colour transform. Facts:
    ``source_size``, ``size``, ``transparent`` (fraction of delivered pixels
    that had alpha), with a region ``region_pixels`` (the part's size in the
    source) and with ``stored`` float values ``clipped`` (the fraction of
    delivered pixels outside 0 to 1).
    """
    import imbuf
    import numpy
    buffer = imbuf.load(str(path))
    try:
        source = tuple(buffer.size)
        facts = {"source_size": list(source)}
        if region is not None:
            left, top, right, bottom = pixel_box(region, *source)
            if min(right - left, bottom - top) < MIN_REGION_PIXELS:
                raise RuntimeError("That region is only %dx%d pixels of the %dx%d picture; choose a larger one"
                                   % ((right - left, bottom - top) + source))
            # Rows are stored bottom first; ``crop`` takes inclusive corners.
            buffer.crop((left, source[1] - bottom), (right - 1, source[1] - top - 1))
            facts["region_pixels"] = [right - left, bottom - top]
        width, height = buffer.size
        if long_side is not None and max(width, height) > long_side:
            width, height = output_size(long_side, width / height)
            buffer.resize((width, height), method="BILINEAR")  # A box filter when scaling down.
        raw = stored and buffer.buffer_type != "BYTE"
        if buffer.buffer_type != "BYTE" and not raw:
            buffer.convert_buffer_type("BYTE")
        with buffer.with_buffer() as view:
            rows = numpy.asarray(view)  # (height, width, 4) bytes, bottom row first, straight alpha.
            if raw:  # Float values of data: no display conversion, which would bend them.
                rows, facts["clipped"] = stored_bytes(rows)
            pixels, transparent = flatten(rows)
            png = encode_png(pixels.tobytes(), width, height)
            del rows, pixels  # Nothing may still export the buffer when it is freed.
    finally:
        buffer.free()
    facts.update(size=[width, height], transparent=transparent)
    return png, facts


def colour(scene):
    """The display transform a rendered picture is shown in: the scene's view transform, look and exposure."""
    view = scene.view_settings
    facts = {"view_transform": view.view_transform}
    if view.look != "None":
        facts["look"] = view.look
    if abs(view.exposure) > 1e-6:
        facts["exposure"] = round(view.exposure, 3)
    if abs(view.gamma - 1.0) > 1e-6:
        facts["gamma"] = round(view.gamma, 3)
    return facts


def clipped_note(fraction):
    """What a result says when stored values lay outside what eight bits can show (empty otherwise)."""
    if not fraction:
        return ""
    return (" %s of its pixels hold values outside 0 to 1, which are clipped here."
            % ("%d%%" % round(fraction * 100) if fraction >= 0.005 else "Under 1%"))


def transparency_note(fraction):
    """What a result says when its picture had transparent pixels (empty otherwise)."""
    if not fraction:
        return ""
    return (" %s of the picture is transparent or half transparent; those pixels are shown over a grey checkerboard, "
            "which is not in the render." % ("%d%%" % round(fraction * 100) if fraction >= 0.005 else "Under 1%"))
