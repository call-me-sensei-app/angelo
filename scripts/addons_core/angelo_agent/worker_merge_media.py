# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The files a merged part brings from a worker (``worker_merge_privacy``, break-it E2E-20 F-L-2, BREAKIT-C7A-5 H1 and
M1): never a worker file's own bytes.

A file is the worker's when it is packed in the part (the worker's bytes, whatever folder they came from) or when it
is in a private folder (``PrivateFolders.holds``). Its bytes can carry anything besides what Blender reads: a render's
PNG holds the open file's path in a text chunk, EXIF and EXR attributes, a sound's tags, or a file that is not media
at all and only loaded as a datablock (``bpy.data.sounds.load`` never reads the file). So what is kept is written
again from what Blender decoded:

- an image (one file or generated, one view) as its own pixels, packed as a new PNG, or an OpenEXR for a float
  buffer (which Blender holds in scene linear: tagged so, a data image as it was), in place, so its users and
  properties stay;
- a sound decoded and written again as PCM WAV, then packed.

A font cannot be written again (and any bytes after its tables are read by no one: BREAKIT-C7A-6), so a worker's font
is never kept: packed bytes are removed, and its private path cut. Blender's own font is the one named ``<builtin>``
with nothing packed (its data is Blender's, in memory); bytes packed under that name are a worker's too. What cannot
be written again this way (a sequence, a movie, tiles, views, what Blender cannot decode, any other datablock's file)
is never packed: a private file's path is cut (``detached``), and bytes already packed in the part that cannot be
removed refuse the merge (``unsafe``).
"""

import os

failures = []   # Why each file of the last merge was not kept (kind, error): read by the tests, never shown.



def packed(item):
    """``item``'s packed files (``PackedFile``): an image's, one per file it reads, or another datablock's one."""
    files = getattr(item, "packed_files", None)
    if files is not None:   # ``ImagePackedFile`` each, holding its ``PackedFile``.
        return [file.packed_file for file in files if file.packed_file is not None]
    single = getattr(item, "packed_file", None)
    return [single] if single is not None else []


def secure(bpy, kept, found, private, scratch):
    """Each kept datablock with a worker's file (``found``: a private path, by Blender's path walk) or packed bytes
    written again from what Blender decoded, or its packing removed. Returns (packed, detached, unsafe) names."""
    from .worker_merge_paths import type_name
    written, detached, unsafe = [], [], []
    failures.clear()
    candidates = [item for item in kept if packed(item) and item.library is None]
    candidates += [item for item in found if item not in candidates]
    for number, item in enumerate(candidates):
        kind, had = type_name(item), bool(packed(item))
        try:
            kept_it = (_image(bpy, item) if kind == "Image" else
                       _sound(bpy, item, os.path.join(scratch, str(number))) if kind == "Sound" else False)
        except Exception as error:   # noqa: BLE001 - Blender or audaspace (``aud.error``) could not decode or pack it.
            failures.append((kind, type(error).__name__, str(error)[:300]))
            kept_it = False
        if kept_it:
            written.append(item.name)
        elif had and not _unpacked(item):
            unsafe.append(item.name)
        else:
            detached.append(item.name)
    return sorted(written), sorted(detached), sorted(unsafe)


def _unpacked(item):
    """``item``'s packed bytes removed (its path stays, to be cut): whether none are left."""
    if not packed(item):
        return True
    try:
        item.unpack(method="REMOVE")
    except (RuntimeError, TypeError, AttributeError):
        return False
    return not packed(item)


def _image(bpy, item):
    """``item`` (an image of one file or a generated one, one view) packed as its own decoded pixels and nothing
    else: a bake never saved keeps its pixels as a file's do."""
    import numpy
    if (item.source not in ("FILE", "GENERATED") or item.type != "IMAGE" or item.use_multiview
            or len(packed(item)) > 1):
        return False
    width, height = item.size
    if width <= 0 or height <= 0:   # Not an image Blender can read (BREAKIT-C7A-4).
        return False
    pixels = numpy.empty(width * height * 4, dtype=numpy.float32)
    item.pixels.foreach_get(pixels)
    is_float, colorspace, alpha, data = (item.is_float, item.colorspace_settings.name, item.alpha_mode,
                                         item.colorspace_settings.is_data)
    stem = os.path.splitext(os.path.basename(item.filepath_raw.replace("\\", "/")))[0] or "image"
    # Its new name first, its buffer written whole: the new buffer copies the image's path buffer as its own name,
    # which packing and a later repack of an edit (Save Modified Images) write as the packed file's (BREAKIT-C7A-6).
    from .worker_merge_paths import overwrite
    overwrite(item, "filepath_raw", "//%s.%s" % (stem, "exr" if is_float else "png"))
    item.source = "GENERATED"   # A new buffer: none of the file's metadata, packed bytes dropped on packing.
    item.generated_type, item.use_generated_float = "BLANK", is_float
    item.generated_width, item.generated_height = width, height
    if not is_float or data:
        item.colorspace_settings.name = colorspace
    else:
        item.colorspace_settings.name = _scene_linear(bpy)
    item.alpha_mode = alpha
    item.pixels.foreach_set(pixels)
    item.pack()
    return item.source == "FILE" and len(packed(item)) == 1


_LINEAR = []


def _scene_linear(bpy):
    """The colour space Blender gives a new float image: scene linear, which a float buffer is held in."""
    if not _LINEAR:
        probe = bpy.data.images.new("angelo-linear-probe", 1, 1, float_buffer=True)
        _LINEAR.append(probe.colorspace_settings.name)
        bpy.data.images.remove(probe)
    return _LINEAR[0]


def _sound(bpy, item, folder):
    """``item`` decoded and packed again as PCM WAV, written in ``folder`` (a private scratch folder)."""
    import aud
    os.makedirs(folder, exist_ok=True)
    stem = os.path.splitext(os.path.basename(item.filepath.replace("\\", "/")))[0] or "sound"
    source = bpy.path.abspath(item.filepath, library=item.library)
    files = packed(item)
    if files:
        source = os.path.join(folder, "packed")
        with open(source, "wb") as out:
            out.write(bytes(files[0].data))
    target = os.path.join(folder, stem + ".wav")
    aud.Sound(source).write(target, container=aud.CONTAINER_WAV, codec=aud.CODEC_PCM, format=aud.FORMAT_FLOAT32)
    if files and not _unpacked(item):
        return False
    item.filepath = target
    item.pack()
    return len(packed(item)) == 1
