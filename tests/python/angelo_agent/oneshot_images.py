# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Image work of the one-shot tests' background jobs (``oneshot_job.py``): statistics, crops and previews.

The array functions need only NumPy and are tested on the host; the functions
that take ``bpy`` read and write files through Blender's own image code, so
PNG, JPEG and OpenEXR stills of any bit depth are read alike and no other
image library is needed.

``statistics`` is what the pixel sanity checks (``oneshot_checks.pixel_checks``)
read: the mean and spread of the luminance, the share of near-black pixels
and a small luminance thumbnail for comparing a still with a render of the
factory scene. Values are display-referred (0 to 1): a scene-linear file
(OpenEXR) is given the sRGB curve first, so it is not taken for black.
"""

THUMBNAIL = (32, 18)   # Columns, rows.
DARK = 0.03
PREVIEW_SIDE = 1920
JPEG_QUALITIES = (92, 88, 84, 80, 76, 72, 68, 64, 60)


def luminance(rgb):
    return 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]


def thumbnail(values, size=THUMBNAIL):
    """Mean luminance of ``size`` (columns, rows) equal cells, row-major from the top, as a flat list."""
    import numpy
    height, width = values.shape
    columns, rows = min(size[0], width), min(size[1], height)
    row_edges = numpy.linspace(0, height, rows + 1).astype(int)
    column_edges = numpy.linspace(0, width, columns + 1).astype(int)
    cells = [float(values[row_edges[r]:row_edges[r + 1], column_edges[c]:column_edges[c + 1]].mean())
             for r in range(rows) for c in range(columns)]
    return [round(cell, 4) for cell in cells]


def statistics(rgb):
    """The measured facts of one image (``rgb``: height x width x 3, display-referred, top row first)."""
    import numpy
    values = numpy.clip(luminance(numpy.asarray(rgb, dtype=numpy.float64)), 0.0, 1.0)
    return {"pixels": int(values.size), "size": [int(values.shape[1]), int(values.shape[0])],
            "mean": round(float(values.mean()), 5), "spread": round(float(values.std()), 5),
            "dark_share": round(float((values < DARK).mean()), 5), "thumbnail": thumbnail(values)}


def to_display(linear):
    """The sRGB curve for scene-linear pixels (clipped to 0-1)."""
    import numpy
    values = numpy.clip(linear, 0.0, 1.0)
    return numpy.where(values <= 0.0031308, values * 12.92, 1.055 * numpy.power(values, 1.0 / 2.4) - 0.055)


def resized(rgb, width, height):
    """Bilinear resampling to ``width`` x ``height``."""
    import numpy
    rgb = numpy.asarray(rgb, dtype=numpy.float32)
    rows = numpy.clip((numpy.arange(height) + 0.5) * rgb.shape[0] / height - 0.5, 0, rgb.shape[0] - 1)
    columns = numpy.clip((numpy.arange(width) + 0.5) * rgb.shape[1] / width - 0.5, 0, rgb.shape[1] - 1)
    top, left = numpy.floor(rows).astype(int), numpy.floor(columns).astype(int)
    bottom, right = numpy.minimum(top + 1, rgb.shape[0] - 1), numpy.minimum(left + 1, rgb.shape[1] - 1)
    v, u = (rows - top)[:, None, None], (columns - left)[None, :, None]
    upper = rgb[top][:, left] * (1 - u) + rgb[top][:, right] * u
    lower = rgb[bottom][:, left] * (1 - u) + rgb[bottom][:, right] * u
    return upper * (1 - v) + lower * v


def cropped(rgb, box, scale=1):
    """The ``(left, top, right, bottom)`` pixel box of an image, enlarged ``scale`` times."""
    left, top, right, bottom = box
    part = rgb[top:bottom, left:right]
    if part.shape[0] != bottom - top or part.shape[1] != right - left or not part.size:
        raise ValueError("Crop box %s is outside the %d x %d image" % (list(box), rgb.shape[1], rgb.shape[0]))
    return part if scale == 1 else resized(part, part.shape[1] * scale, part.shape[0] * scale)


# ---------------------------------------------------------------- through Blender's image code

def read(bpy, path):
    """``(rgb, is_linear)``: an image file's pixels, top row first (height x width x 3 floats)."""
    import numpy
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        if not width or not height:
            raise ValueError("%s has no pixels" % path)
        buffer = numpy.empty(width * height * image.channels, dtype=numpy.float32)
        image.pixels.foreach_get(buffer)
        pixels = buffer.reshape(height, width, image.channels)[::-1]
        rgb = pixels[..., :3] if image.channels >= 3 else numpy.repeat(pixels[..., :1], 3, axis=2)
        return numpy.array(rgb), bool(image.is_float)
    finally:
        bpy.data.images.remove(image)


def read_display(bpy, path):
    rgb, linear = read(bpy, path)
    return to_display(rgb) if linear else rgb


def write(bpy, rgb, path, file_format="PNG", quality=90):
    """Save display-referred pixels as an 8-bit file, exactly as given (no view transform)."""
    import numpy
    height, width = rgb.shape[:2]
    image = bpy.data.images.new("One-shot image", width, height, alpha=False)
    try:
        rgba = numpy.ones((height, width, 4), dtype=numpy.float32)
        rgba[..., :3] = numpy.clip(rgb, 0.0, 1.0)[::-1]
        image.pixels.foreach_set(rgba.ravel())
        image.file_format, image.filepath_raw = file_format, str(path)
        image.save(quality=quality) if file_format == "JPEG" else image.save()
    finally:
        bpy.data.images.remove(image)
    return str(path)


def write_within(bpy, rgb, path, max_bytes):
    """A JPEG at the highest quality in ``JPEG_QUALITIES`` that fits ``max_bytes``; returns the quality."""
    import os
    for quality in JPEG_QUALITIES:
        write(bpy, rgb, path, "JPEG", quality)
        if os.path.getsize(path) <= max_bytes:
            return quality
    raise ValueError("%s does not fit %d bytes at JPEG quality %d" % (path, max_bytes, quality))


def file_statistics(bpy, path, preview=None):
    """``statistics`` of an image file; with ``preview`` also an 8-bit PNG no larger than ``PREVIEW_SIDE``."""
    try:
        rgb = read_display(bpy, path)
        facts = statistics(rgb)
        if preview:
            side = max(rgb.shape[:2])
            if side > PREVIEW_SIDE:
                width, height = (max(1, rgb.shape[axis] * PREVIEW_SIDE // side) for axis in (1, 0))
                rgb = resized(rgb, width, height)
            facts["preview"] = write(bpy, rgb, preview)
        return facts
    except Exception as error:   # An unreadable still fails its pixel checks; it never stops the job.
        return {"error": "%s: %s" % (type(error).__name__, str(error)[:300])}
