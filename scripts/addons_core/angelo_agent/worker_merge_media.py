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
- a sound decoded (audaspace) and written again as 24-bit FLAC, streamed, so memory stays flat however long it is
  (BREAKIT-C7A-9 M2: a whole decode took 39 GB for three hours; its WAV writer failed on ten minutes, BREAKIT-C7A-7
  L3), then packed; one whose file would be larger than Blender packs (2 GB) or than the free disk holds is not kept.

A font cannot be written again (and any bytes after its tables are read by no one: BREAKIT-C7A-6), so a worker's font
is never kept: packed bytes are removed, and its private path cut. Blender's own font is the one named ``<builtin>``
with nothing packed (its data is Blender's, in memory); bytes packed under that name are a worker's too. What cannot
be written again this way (a sequence, a movie, tiles, views, what Blender cannot decode, any other datablock's file)
is never packed: a private file's path is cut (``detached``), and bytes already packed in the part that cannot be
removed refuse the merge (``unsafe``): a packed Volume always does, since Blender has no way to remove a Volume's
packed bytes (BREAKIT-C7A-7 L5). An image larger than Blender's generated images can be (65536 pixels a side), or whose
decoded pixels would be larger than Blender packs (``PIXELS``: their copy is held three times while it is written
again; BREAKIT-C7A-9 M2), is not written again (BREAKIT-C7A-7 L4).
"""

import os
import re
import shutil

from . import owned_paths

PACK_LIMIT = (1 << 31) - 1   # The largest file Blender packs (``BKE_packedfile_new``).
PIXELS = PACK_LIMIT // 16   # The most pixels written again: their float RGBA copy no larger than a packed file.
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
    written again from what Blender decoded, or its packing removed. Returns (packed: the datablocks written again,
    detached names, unsafe names)."""
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
            written.append(item)
        elif had and not _unpacked(item):
            unsafe.append(item.name)
        else:
            detached.append(item.name)
    return written, sorted(detached), sorted(unsafe)


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
    sizes = item.bl_rna.properties
    if width > sizes["generated_width"].hard_max or height > sizes["generated_height"].hard_max:
        return False   # A buffer Blender cannot make: refused before anything of the image changes.
    if width * height > PIXELS:
        return False   # Checked before its pixels are read (BREAKIT-C7A-9 M2).
    pixels = numpy.empty(width * height * 4, dtype=numpy.float32)
    item.pixels.foreach_get(pixels)
    is_float, colorspace, alpha, data = (item.is_float, item.colorspace_settings.name, item.alpha_mode,
                                         item.colorspace_settings.is_data)
    stem = os.path.splitext(os.path.basename(item.filepath_raw.replace("\\", "/")))[0] or "image"
    # Its new name first, its buffer written whole: the new buffer copies the image's path buffer as its own name,
    # which packing and a later repack of an edit (Save Modified Images) write as the packed file's (BREAKIT-C7A-6).
    from .worker_merge_paths import overwrite
    name = "//%s.%s" % (stem, "exr" if is_float else "png")
    overwrite(item, "filepath_raw", name)
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
    overwrite(item, "filepath_raw", name)   # Making it generated cleared its path (BREAKIT-C7A-7 L4).
    return item.source == "FILE" and len(packed(item)) == 1


_LINEAR = []


def _scene_linear(bpy):
    """The colour space Blender gives a new float image: scene linear, which a float buffer is held in."""
    if not _LINEAR:
        probe = bpy.data.images.new("angelo-linear-probe", 1, 1, float_buffer=True)
        _LINEAR.append(probe.colorspace_settings.name)
        bpy.data.images.remove(probe)
    return _LINEAR[0]


def _flac_stem(path):
    """The sound's own last name without its extension (``path``: the part's data), in word characters and ``-``
    only, at most 64 bytes of UTF-8 (whole characters): after the fixed ``sound-`` its FLAC is one plain file of the
    folder it is joined to on every platform, never a separator, ``..``, a drive, a stream (``a:b``) or a Windows
    device name (``COM1``, ``NUL``), whatever a script set the path to."""
    stem = os.path.splitext(path.replace("\\", "/").rsplit("/", 1)[-1])[0]
    return re.sub(r"[^\w-]", "_", stem).encode("utf-8")[:64].decode("utf-8", "ignore")


def _sound(bpy, item, folder):
    """``item`` decoded and packed again as 24-bit FLAC, streamed by audaspace into ``folder`` (a private scratch
    folder): memory stays flat; a file Blender cannot pack (``PACK_LIMIT``) or the disk cannot hold is not kept."""
    import aud
    os.makedirs(owned_paths.require_writable(folder), exist_ok=True)
    source = bpy.path.abspath(item.filepath, library=item.library)
    files = packed(item)
    if files:
        source = os.path.join(folder, "packed")
        owned_paths.require_writable(source)
        with open(source, "wb") as out:
            out.write(bytes(files[0].data))
    sound = aud.Sound(source)
    rate, channels = sound.specs
    frames = sound.length
    if frames > 0 and frames * channels * 3 > shutil.disk_usage(folder).free:   # Its samples, before FLAC packs them.
        return False
    target = os.path.join(folder, "sound-%s.flac" % _flac_stem(item.filepath))   # A plain file of the folder above.
    sound.write(target, rate=int(rate), channels=int(channels), format=aud.FORMAT_S24, container=aud.CONTAINER_FLAC,
                codec=aud.CODEC_FLAC)
    if not 0 < os.path.getsize(target) <= PACK_LIMIT:
        return False
    if files and not _unpacked(item):
        return False
    item.filepath = target
    item.pack()
    return len(packed(item)) == 1
