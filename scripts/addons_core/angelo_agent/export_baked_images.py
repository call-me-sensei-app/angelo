# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What an image's pixels are now, every tile of it, as a bake reads them: ``export_baked_reads.Reader.image``.

Contract: doc/angelo/long_jobs.rst, "Baked materials at export". A bake reads an image from one of two places, as
Cycles decides for a bake (``image_is_builtin``, intern/cycles/blender/util.h; ``reads_memory``), and the digest
describes that place:

* **Blender's memory**: an image that is packed, generated, a movie, has unsaved edits (image painting), or is a UDIM
  image with a generated tile (one Add Tile filled and nobody saved). Cycles copies the pixels Blender holds, every
  tile of them, and those can differ from the image's packed bytes or files: a file saved over while the image holds
  the old pixels, then packed (measured: the bake read the held pixels). So the digest holds the pixels themselves,
  every tile's, read natively as Cycles' loader reads them (``_bpy._angelo_digest.pixels``: the node's image user at
  the frame a render computes, loading what is not held and freeing it again, one XXH3 per buffer). Read this way,
  an image loaded or not, and reloaded or reopened from the same source, gives the same digest, and different
  pixels give a different one. A generated image without edits is its settings, which make its pixels (any change
  to them frees the pixels). Without the native reader (a host test's stand-in, a build that lacks it) only a still
  image's single buffer is readable (``Image.pixels``); any other reads as unknown, and unknown is stale.
* **Its files on disk** (OpenImageIO), for every other image, whatever pixels Blender holds (measured: a file saved
  over while Blender held the old pixels baked the file's): each file Blender reads, by size and time. The files are
  named by ``blend_file_paths.read_files`` as Blender names them: each tile's, the file of the frame a sequence's
  image user reads at the scene frame (``frame_number``), and each view's of an image whose views are separate
  files. A multi-view image's views are the list Blender saved with the image (set up from the scene of that time),
  which Python cannot read: the views the scene names now are used only when Blender's own name for the image
  user's view (``Image.filepath_from_user``) is one of them, else the image reads as unknown (stale). An image no
  image user reads (a Geometry Nodes Image input) is not shading the bake reads; its stored file stands for it, and
  what it does reaches the bake through the evaluated mesh.

Main thread.
"""

import hashlib
import os
from collections import OrderedDict

import bpy
import numpy

from . import change_clock, change_keys
from .blend_file_paths import frame_number, image_files, located, read_files, view_suffixes

try:
    import _bpy
    NATIVE = getattr(_bpy, "_angelo_digest", None)
except ImportError:   # Outside Blender (a host test's stand-in bpy).
    NATIVE = None

ANIMATED = frozenset({"SEQUENCE", "MOVIE"})   # Sources whose pixels follow the frame.
FILED = frozenset({"FILE", "SEQUENCE", "TILED"})   # Sources a bake reads from files when nothing makes it copy.
HELD = frozenset({"GENERATED", "MOVIE"})   # Sources Cycles always reads from Blender's memory.


def _text(value):
    return repr(value).encode("utf-8", "surrogatepass")


KEYS = change_keys.ChangeKeys()   # The bake digests' files: one key while a file is unchanged (``change_keys``).
READ_KEYS = change_keys.ChangeKeys()   # ``export_baked_reads``' own: its keys have another shape.


def _file(hasher, path):
    try:
        status = os.stat(path)   # Settled, else its bytes: the digest is saved with the bake (change_keys).
        hasher.update(_text(change_keys.key(status, path, (path, status.st_size, status.st_mtime_ns), keys=KEYS,
                                            change=False)))
    except OSError:
        hasher.update(_text((path, "missing")))


def _cached_file(hasher, path):
    """A file's part of a cache's key (``_source``): its stat once settled, else a key used once
    (``change_clock.cache_key``: the decode is redone, never remembered). A cache's miss recomputes the same value,
    so it never reads a file's bytes to key it, as a detector's form does (``_file``)."""
    try:
        status = os.stat(path)
        hasher.update(_text(change_clock.cache_key(status, (path, status.st_size, status.st_mtime_ns))))
    except OSError:
        hasher.update(_text((path, "missing")))


def reads_memory(image):
    """Whether a bake reads ``image`` from Blender's memory rather than its files: Cycles' ``image_is_builtin`` for a
    bake (no preview render), condition for condition (``test_native_baked_digest_buffers`` holds the two the
    same)."""
    if image.source == "TILED" and any(tile.is_generated_tile for tile in image.tiles):
        return True
    return len(image.packed_files) > 0 or image.source in HELD or image.is_dirty


SIMPLE = frozenset({"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"})
# Editable Image values that are no decode setting and whose getters load the pixels (rna_image.cc): the pixels, the
# resolution and the save format, read from the buffer.
FROM_PIXELS = frozenset({"pixels", "resolution", "file_format"})
# The stored path, as spelled: the files it names are keyed by their resolved paths and stamps (``_source``), and Make
# Paths Relative rewrites the spelling with nothing to decode again.
STORED = frozenset({"filepath", "filepath_raw"})
DECODED_LIMIT = 512   # Remembered decodes (``_DECODED``): a few hundred images' worth of 16-byte digests.
_DECODED = OrderedDict()   # Source and settings -> the pixels' digest bytes a read decoded from that source.


_ID_FIELDS = []


def _id_fields():
    """A datablock's own ID values (name, users, tags, its preview and asset data): never how it decodes, and its
    preview's pixel arrays are costly to read and change while a thumbnail renders."""
    if not _ID_FIELDS:
        rna = getattr(getattr(bpy.types, "ID", None), "bl_rna", None)
        _ID_FIELDS.append(frozenset(rna.properties.keys()) if rna is not None else frozenset())
    return _ID_FIELDS[0]


def _settings(block, depth=1):
    """``block``'s own editable values (and one level of the structs it holds, such as its colour space): how
    Blender decodes an image from its source. Without RNA (a host test's stand-in) there are none."""
    rna = getattr(block, "bl_rna", None)
    if rna is None:
        return ()
    found = []
    for prop in rna.properties:
        name = prop.identifier
        if name == "rna_type" or name in FROM_PIXELS or name in STORED or name in _id_fields():
            continue
        if prop.type in SIMPLE and not prop.is_readonly:
            value = getattr(block, name, None)
            if isinstance(value, (set, frozenset)):   # An enum flag: its members in a stable order.
                value = tuple(sorted(value))
            elif hasattr(value, "__len__") and not isinstance(value, str):
                value = tuple(value)
            found.append((name, value))
        elif prop.type == "POINTER" and depth > 0:
            value = getattr(block, name, None)
            if value is not None and not isinstance(value, bpy.types.ID):
                found.append((name, _settings(value, depth - 1)))
    return tuple(found)


def _source(image, user, scene_frame, tiles, render):
    """What a read of ``image`` that holds nothing decodes, as a key: its settings, the image user and frame, each
    tile's generator settings, and its source's identity (each packed file's XXH3, else each file's settled size and
    time: ``_cached_file``).
    None when that source cannot be named (a view list unknown, no native packed reader)."""
    key = [_settings(image), getattr(image, "type", None), _settings(user) if user is not None else None,
           scene_frame if image.source in ANIMATED else 0,
           getattr(getattr(getattr(bpy, "data", None), "colorspace", None), "working_space", None)]
    for tile in getattr(image, "tiles", ()):
        if image.source == "TILED":
            key.append((tile.number, tile.is_generated_tile, tile.generated_type, tuple(tile.generated_color),
                        tile.generated_width, tile.generated_height, tile.use_generated_float))
    if len(image.packed_files) > 0:
        packed = getattr(NATIVE, "packed", None)
        if packed is None:
            return None
        key.append(tuple(packed(image)))
    elif image.source in FILED or image.source in ANIMATED:
        suffixes = _views(image, render, user, tiles if image.source == "TILED" else ())
        if suffixes is None:
            return None
        stamps = hashlib.blake2b(digest_size=16)
        for path in image_files(image, user, scene_frame, bpy.path.abspath, suffixes=suffixes,
                                tiles=tiles if image.source == "TILED" else None):
            _cached_file(stamps, path)
        key.append(stamps.digest())
    return tuple(key)


def _held(image, user, scene_frame, hasher, render=None):
    """The pixels Blender holds for ``image`` as a bake reads them, every tile (``_bpy._angelo_digest.pixels``).

    An image that holds nothing is decoded from its source by the read, as the bake would; that decode is remembered
    by its source and settings (``_source``), so the next export reads the same source without decoding it again,
    and any change to the source or how it is decoded is a new key. Only a read whose source stayed the same while it
    decoded is remembered."""
    tiles = [tile.number for tile in image.tiles] if image.source == "TILED" else [0]
    read = getattr(NATIVE, "pixels", None)
    if read is not None:
        # Remembered only for an image holding nothing a load would read instead of its source (a cached buffer, an
        # open movie decoder, a multilayer file's result: ``holds``), whose read decodes the source as it is now.
        holds = getattr(NATIVE, "holds", None)
        nothing = not image.has_data and holds is not None and not holds(image)
        source = _source(image, user, scene_frame, tiles, render) if nothing else None
        found = _DECODED.get(source) if source is not None else None
        if found is None:
            found = b"".join(_text(("held", tile, width, height, channels, is_float)) + (digest or b"none")
                             for tile, width, height, channels, is_float, digest
                             in read(image, user, scene_frame, tiles))
            if source is not None and _source(image, user, scene_frame, tiles, render) == source:
                _DECODED[source] = found
                while len(_DECODED) > DECODED_LIMIT:
                    _DECODED.popitem(last=False)
        else:
            _DECODED.move_to_end(source)
        hasher.update(found)
        return
    # Without the native reader only a still image's one buffer is readable (``Image.pixels``, which Blender reads
    # without an image user); any other buffer is unknown, and unknown is stale.
    if (len(tiles) > 1 or image.source in ANIMATED or getattr(image, "is_multiview", False)
            or getattr(image, "type", None) == "MULTILAYER"):
        hasher.update(os.urandom(16))
        return
    pixels = numpy.empty(len(image.pixels), dtype=numpy.float32)
    image.pixels.foreach_get(pixels)
    hasher.update(b"pixels" + pixels.tobytes())


def image_key(image, user, scene_frame):
    """``(image, frame, view, layer)``: what ``image_digest`` depends on beyond the image, as ``user`` (its node's image
    user) reads it at ``scene_frame``. ``frame``, for a sequence or a movie, is the frame a render computes
    (``frame_number``), None without an image user; ``view`` is the image user's view and stored frame, which name
    Blender's file for a multi-view image (``_views``); ``layer`` is the layer and pass the image user reads, each
    its own buffer of a multilayer image."""
    frame = view = layer = None
    if user is not None:
        if image.source in ANIMATED:
            frame = frame_number(scene_frame, user.frame_duration, user.frame_start, user.frame_offset,
                                 user.use_cyclic)
        if getattr(image, "is_multiview", False):
            view = (user.multilayer_view, user.frame_current)
        # A multilayer file's layer and pass, each its own buffer (a file reads as multilayer only once decoded).
        layer = (getattr(user, "multilayer_layer", 0), getattr(user, "multilayer_pass", 0))
    return image.session_uid, frame, view, layer


def image_path(image):
    """The file a bake opens for ``image``, absolute (``located``), or "" when the bake opens none (it copies the
    pixels Blender holds: ``reads_memory``, whose pixels the digest hashes). Never the stored spelling, which Make
    Paths Relative or a save's relative remap rewrites with no change to what is read; and no path where none is read,
    so a packed image's binding survives its project folder moving."""
    if image.source not in FILED or reads_memory(image) or not image.filepath_raw:
        return ""
    return _path(image.filepath_raw, image)


def _path(raw, image):
    path = located(raw, image.library, bpy.path.abspath)
    return os.path.normpath(path) if path else ""   # normpath("") is ".", the working folder.


def _views(image, render, user, tiles):
    """The view suffixes naming a multi-view image's files, () for one file, or None when Blender's own list of
    views cannot be confirmed (unknown)."""
    if user is None or not getattr(image, "is_multiview", False):
        return ()   # The stored path: no views (or none Blender set up), or not shading a bake reads.
    suffixes = view_suffixes(image, render) if render is not None else ()
    if user.multilayer_view != 0:
        return None   # Only the first view's name is checked (another index may be past Blender's list).
    named = {_path(path, image) for path in read_files(image.filepath_raw, image.source, tiles=tiles,
                                                        frame=user.frame_current, suffixes=suffixes)}
    return suffixes if os.path.normpath(image.filepath_from_user(image_user=user)) in named else None


def image_digest(image, key, render=None, user=None, scene_frame=0):
    """A hex digest of what ``image`` holds now, as ``image_key`` (``key``) and ``user`` read it at ``scene_frame``.

    ``render``: the scene's render settings, whose views name a multi-view image's files (``view_suffixes``).
    """
    frame = key[1]
    hasher = hashlib.blake2b(digest_size=16)
    if image.source == "GENERATED" and not image.is_dirty:   # Its settings make its pixels.
        hasher.update(_text((image.generated_type, tuple(image.generated_color), image.generated_width,
                             image.generated_height, image.use_generated_float)))
    elif reads_memory(image):   # Cycles copies what Blender holds: those pixels, whatever the files say.
        _held(image, user, scene_frame, hasher, render)
        if image.source in ANIMATED:
            hasher.update(_text(frame))
    elif image.source in FILED:
        tiles = [tile.number for tile in image.tiles] if image.source == "TILED" else ()
        suffixes = _views(image, render, user, tiles)
        if suffixes is None:   # Which views' files Blender reads is unknown, and unknown is stale.
            hasher.update(os.urandom(16))
            suffixes = ()

        # Each file as Blender names it (``image_files``): a tile's (1002, u2_v1), the frame's, a view's.
        for part in ([(number,) for number in tiles] if image.source == "TILED" else [None]):
            for path in image_files(image, user, scene_frame, bpy.path.abspath, suffixes=suffixes, tiles=part):
                _file(hasher, path)
        if image.source in ANIMATED:
            hasher.update(_text(frame))
    else:   # A render result or viewer: what it was generated with.
        hasher.update(_text((image.generated_type, tuple(image.generated_color), image.generated_width,
                             image.generated_height, image.use_generated_float)))
    return hasher.hexdigest()
