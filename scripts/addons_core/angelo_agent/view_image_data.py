# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_view`` with ``image: "IMAGE:<name>"``: an Image data-block's pixels as Blender holds them now.

Bakes, texture paint and generated images exist only in memory until they are
saved, so a model that baked or painted could not see its result. This source
reads the buffer Blender already holds (unsaved changes included) and makes
the picture file that ``view_finished.shown`` delivers (doc/angelo/
view_sources.rst, "Image data-blocks").

* **Never loads a file.** The native read helper says whether the image's
  default buffer is cached (``_bpy._angelo_read.image_metadata``, exact and
  cache-only, as ``angelo_read_data`` uses it). Only then is ``pixels`` read,
  which acquires that same cached buffer. An image not in memory is refused
  with what works; so is a build without the helper.
* **What the person sees by default.** 8-bit pixels as stored; float pixels
  (scene-linear, premultiplied) through the sRGB transform of Blender's
  Standard view; a non-colour image as its stored values, clipped values
  counted. What the Image Editor would show differently is reported.
* **Bounded.** ``pixels`` copies four-byte floats, so at most ``MAX_PIXELS``;
  conversion runs in row blocks.

Main thread. Nothing in the file, the image or the Image Editor changes.
Model-facing text is English.
"""

from .view_pixels import ROW_BLOCK, encode_rgba_png, srgb_encode, to_bytes

MAX_PIXELS = 4096 * 4096
LISTED = 8  # Image names a refusal lists.
SRGB_NAMES = ("sRGB", "sRGB IEC61966-2.1")
NOT_LOADED = ("Image '%s' is not in memory, and an automatic read never loads an image file. Its pixels can be shown "
              "once Blender holds them: when the person opens it in the Image Editor, or after an approved "
              "angelo_execute_python script loads it (for example image.reload()); EDITOR:IMAGE_EDITOR shows the "
              "Image Editor as the person sees it")
THEN = " It shows the pixels Blender holds now, unsaved paint and bakes included."


def cached_metadata(image):
    """The cached default buffer's metadata, or None when it is not in memory (RuntimeError without the helper)."""
    from .data_read_access import native_api
    api = native_api()
    if api is None or not callable(getattr(api, "image_metadata", None)):
        raise RuntimeError("This build of Angelo cannot tell whether an image is in memory without loading it; "
                           "update the application")
    record = api.image_metadata(image)
    return record if isinstance(record, dict) and "size" in record else None


def images_busy():
    """Whether a render, bake or job may be writing image buffers now (reading them would race it)."""
    import bpy
    from .view_finished import _busy
    return _busy() or bpy.app.is_job_running("OBJECT_BAKE")


def find_image(name):
    """The Image data-block called ``name`` (RuntimeError naming some images otherwise)."""
    import bpy
    image = bpy.data.images.get(name)
    if image is None:
        names = sorted(item.name for item in bpy.data.images if item.type not in ("RENDER_RESULT", "COMPOSITING"))
        listed = ", ".join(repr(item[:64]) for item in names[:LISTED]) or "none"
        more = " and %d more" % (len(names) - LISTED) if len(names) > LISTED else ""
        raise RuntimeError("No image named %r. Images: %s%s" % (name[:64], listed, more))
    if image.type == "RENDER_RESULT":
        raise RuntimeError("'%s' is Blender's Render Result: use image RENDER_RESULT" % image.name[:64])
    if image.type == "COMPOSITING":
        raise RuntimeError("'%s' is the compositor's Viewer image, which is not read here; EDITOR:IMAGE_EDITOR "
                           "shows an Image Editor that displays it" % image.name[:64])
    return image


def _alpha_used(image, channels):
    return channels == 4 and image.alpha_mode in ("STRAIGHT", "PREMUL")


def _block(values, channels, stored, is_float, alpha):
    """One block of stored rows (n, width, channels) as RGBA bytes; returns ``(bytes, pixels outside 0 to 1)``."""
    import numpy
    height, width = values.shape[:2]
    if channels >= 3:
        colour = values[..., :3]
    else:
        colour = numpy.repeat(values[..., :1], 3, axis=2)
    opacity = values[..., 3] if alpha else None
    outside = 0
    if stored:
        outside = int(((colour < 0.0) | (colour > 1.0) | ~numpy.isfinite(colour)).any(axis=-1).sum())
    elif is_float:
        if opacity is not None:  # Float buffers are premultiplied; colours are shown unpremultiplied over the checker.
            colour = numpy.divide(colour, opacity[..., None], out=numpy.zeros_like(colour),
                                  where=opacity[..., None] > 1e-6)
        colour = srgb_encode(colour)
    rows = numpy.empty((height, width, 4), dtype=numpy.uint8)
    rows[..., :3] = to_bytes(colour)
    rows[..., 3] = to_bytes(opacity) if opacity is not None else 255
    return rows, outside


def display_rows(image, metadata, scene):
    """The image as RGBA bytes, top row first, as the person sees it by default: ``(rows, facts, uncertainty)``."""
    import numpy
    width, height = metadata["size"]
    channels = int(metadata["channels"])
    if width * height > MAX_PIXELS:
        raise RuntimeError("Image '%s' is %dx%d: more than the %d x %d pixels a view reads from an image; "
                           "EDITOR:IMAGE_EDITOR shows it as the Image Editor displays it" %
                           (image.name[:64], width, height, 4096, 4096))
    if channels not in (1, 3, 4) or width < 1 or height < 1:
        raise RuntimeError("Image '%s' has %d channels a pixel, which cannot be shown" % (image.name[:64], channels))
    values = numpy.empty(width * height * channels, dtype=numpy.float32)
    image.pixels.foreach_get(values)  # The cached buffer metadata just reported: no load, no generation.
    values = values.reshape(height, width, channels)
    settings = image.colorspace_settings
    stored, is_float = bool(settings.is_data), bool(metadata.get("is_float"))
    alpha = _alpha_used(image, channels)
    rows = numpy.empty((height, width, 4), dtype=numpy.uint8)
    outside = 0
    for start in range(0, height, ROW_BLOCK):
        block, clipped = _block(values[start:start + ROW_BLOCK], channels, stored, is_float, alpha)
        rows[start:start + ROW_BLOCK] = block
        outside += clipped
    del values
    facts = {"image": image.name[:128], "size": [width, height], "channels": channels, "float": is_float,
             "color_space": settings.name[:64], "values": "STORED" if stored else "DISPLAY",
             "unsaved_changes": bool(image.is_dirty), "kind": image.source}
    if stored and outside:
        facts["clipped"] = round(outside / float(width * height), 4)
    if image.alpha_mode == "CHANNEL_PACKED" and channels == 4:
        facts["alpha"] = "CHANNEL_PACKED"
    uncertainty = _approximations(image, settings, stored, is_float, scene)
    if image.source == "TILED":
        facts["tiles"] = len(image.tiles)
    if image.source in ("SEQUENCE", "MOVIE"):
        facts["frame_duration"] = int(metadata.get("frame_duration", 1))
    return rows[::-1], facts, uncertainty


def _approximations(image, settings, stored, is_float, scene):
    """What the Image Editor would show differently from this picture, as uncertainty entries."""
    reasons = []
    if not stored:
        device = scene.display_settings.display_device
        if device != "sRGB":
            reasons.append("the display device is %s, not sRGB" % device[:32])
        if not is_float and settings.name not in SRGB_NAMES:
            reasons.append("its 8-bit pixels are in %s, shown as stored" % settings.name[:32])
        if getattr(image, "use_view_as_render", False):
            reasons.append("View as Render is on, so the Image Editor applies the scene's view transform")
    entries = []
    if reasons:
        entries.append({"code": "image_color_approximate", "detail": "Shown in Blender's Standard sRGB colours; "
                        + "; ".join(reasons)})
    if image.source == "TILED" and len(image.tiles) > 1:
        entries.append({"code": "image_first_tile_only", "detail": "A UDIM image: only its first tile is shown"})
    if image.source in ("SEQUENCE", "MOVIE"):
        entries.append({"code": "image_current_frame_only", "detail": "Only the frame Blender holds now is shown"})
    return entries


def source(window, checked, references, controller):
    """The ``view_finished.Source`` of ``IMAGE:<name>``: a private RGBA PNG of the image as shown."""
    from .view_finished import Source
    from .view_picture import clipped_note
    image = find_image(checked["item"])
    if images_busy():
        raise RuntimeError("A render, bake or job is running and may be writing images; '%s' can be shown once it "
                           "ends" % image.name[:64])
    metadata = cached_metadata(image)
    if metadata is None:
        raise RuntimeError(NOT_LOADED % image.name[:64])
    rows, facts, uncertainty = display_rows(image, metadata, window.scene)
    label = "Image %s" % image.name[:64]
    reference = references.new_observation(label + " · pixels")
    try:
        with open(reference["path"], "wb") as handle:
            handle.write(encode_rgba_png(rows))
    except Exception:
        references.discard_observation(reference)
        raise
    kind = "non-colour data, its stored values" if facts["values"] == "STORED" else (
        "float pixels through the sRGB transform" if facts["float"] else "8-bit pixels as stored")
    note = " It is %s." % kind + clipped_note(facts.get("clipped"))
    if facts.get("alpha") == "CHANNEL_PACKED":
        note += " Its alpha channel holds packed data and is not shown."
    return Source(reference["path"], label, "Image '%s'" % image.name[:64], facts, reference, then=THEN,
                  note=note, framing="IMAGE_DATA", uncertainty=uncertainty)
