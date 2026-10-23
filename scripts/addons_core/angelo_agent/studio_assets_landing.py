# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where a Media Studio image lands in the person's file: its picture is packed, once.

An image asset's material references Studio's own picture by its absolute path (the library stores no copy). When the
material is appended into the person's file (a drop, File > Append, a script in an interactive session),
``blend_import_post`` packs the pictures that import brought in (``blend_import_pre`` notes what was there before),
so the file keeps them through every save, move, rename and share. A placement that loads the picture itself (a
reference, the Image Editor) lands it through ``land`` the same way. Each is packed once: its marker
(``STUDIO_IMAGE``) is then replaced by ``STUDIO_PACKED``, so a later Unpack, repack or paint is the person's and
never undone. Saving packs only what an import could not (Studio's file was missing then), never in the library's
own files. A linked material stays the library's, as any linked asset. Model assets are ordinary assets with their own
data: nothing here touches them. A background (``-b``) Blender registers none of this (``studio_assets_host``).
"""

import hashlib
import os

import bpy
from bpy.app.handlers import persistent

from . import pump_guard

STUDIO_IMAGE = "angelo_studio_asset"   # Set by studio_assets_convert on each picture of an image asset: not yet packed.
STUDIO_PACKED = "angelo_studio_packed"   # The item id, once packed here: only finds an unchanged copy to share.
_to_free = []   # Duplicate pictures, freed later from a timer and only if nothing uses them.
_before = set()   # Image pointers present when the current import started (blend_import_pre).


def unmodified(packed, source):
    """Whether ``packed`` (an image packed here) still holds exactly Studio's picture at ``source``: same bytes,
    nothing painted since. A picture the person changed is never shared with a new drop."""
    data = getattr(getattr(packed, "packed_file", None), "data", None)
    if data is None or getattr(packed, "is_dirty", False):
        return False
    try:
        with open(source, "rb") as handle:
            original = handle.read()
    except OSError:
        return False
    return len(original) == len(data) and hashlib.sha256(original).digest() == hashlib.sha256(bytes(data)).digest()


def same_settings(packed, landed):
    """Whether ``packed`` still has Studio's picture settings as ``landed`` brings them: colour space, alpha mode,
    source and Studio's own name (the file's name). A person who set Non-Color, changed the alpha or renamed it to
    their own use keeps it to themselves (release-105's review 6)."""
    def colour_space(image):
        return getattr(getattr(image, "colorspace_settings", None), "name", None)
    return (colour_space(packed) == colour_space(landed) and getattr(packed, "alpha_mode", None) == getattr(
        landed, "alpha_mode", None) and getattr(packed, "source", None) == getattr(landed, "source", None)
            and packed.name == os.path.basename(packed.filepath))


def pack_landed_images(arrived=None):
    """Land each Media Studio picture still waiting to be packed (``arrived(image)``, when given, narrows it to the
    current import's), once (``land``); returns how many were packed.

    The library references Studio's file, so nothing is stored twice on disk; the person's file holds only the
    pictures it uses."""
    kept, landed = _packed_here(), []
    for image in bpy.data.images:
        waiting = image.get(STUDIO_IMAGE) if image.library is None else None
        if isinstance(waiting, str) and image.packed_file is None and (arrived is None or arrived(image)):
            landed.append(image)
    packed = 0
    for image in landed:
        packed += _land(image, kept) is image and image.packed_file is not None
    return packed


def land(image):
    """The image to use for ``image``, a Studio picture just brought into the person's file and marked
    ``STUDIO_IMAGE`` with its item id (an append, or a placement that loaded the picture: a reference, the Image
    Editor): a copy of the same picture already packed here while that one is unchanged (``unmodified``: the bytes;
    ``same_settings``: colour space, alpha, source, name), which ``image`` is remapped to and freed later; otherwise
    ``image`` itself, packed and marked ``STUDIO_PACKED``, as stock Blender keeps a second picture. Every path that
    lands a Studio picture comes through here, so one picture is packed once whichever way it arrived; no other
    module reads or writes the two markers (a placement that packed and marked its own copy was a second copy for
    the next material drop: test_blender_media_drop on 6ceaef365ff)."""
    return _land(image, _packed_here())


def _packed_here():
    """(item id, file name) -> the images packed here as that Studio picture."""
    kept = {}
    for image in bpy.data.images:
        packed_id = image.get(STUDIO_PACKED) if image.library is None else None
        if isinstance(packed_id, str) and image.packed_file is not None:
            kept.setdefault((packed_id, os.path.basename(image.filepath)), []).append(image)
    return kept


def _land(image, kept):
    key = (image.get(STUDIO_IMAGE), os.path.basename(image.filepath))
    source = bpy.path.abspath(image.filepath)
    keep = next((k for k in kept.get(key, ()) if same_settings(k, image) and unmodified(k, source)), None)
    if keep is not None:
        image.user_remap(keep)
        _free_later([image])
        return keep
    try:
        image.pack()
    except RuntimeError:
        return image   # Studio's file is gone: the picture shows as missing, as any missing texture does.
    image[STUDIO_PACKED] = key[0]
    del image[STUDIO_IMAGE]   # Packed once: what the person does with it from now on stays.
    kept.setdefault(key, []).append(image)
    return image


def _free_later(ids):
    """Never free inside blend_import_post: ``bpy.data.libraries.load`` hands the import's items to the script after
    the handler, and a freed one would crash Blender (use after free). Freed from a timer, only if still unused."""
    _to_free.extend(ids)
    if not bpy.app.timers.is_registered(_free_unused):
        bpy.app.timers.register(_free_unused, first_interval=0.5)


def _free_unused():
    unused = []
    for item in _to_free:
        try:
            if item.users == 0:
                unused.append(item)
        except ReferenceError:
            continue   # Already gone (an undo, a file load).
    _to_free.clear()
    if unused:
        bpy.data.batch_remove(unused)
    return None


@persistent
def _importing(*_args):
    """Right before Blender appends or links: what is already here is not this import's."""
    _before.clear()
    _before.update(image.as_pointer() for image in bpy.data.images)


@persistent
def _imported(*_args):
    """Right after Blender appends (a drop, File > Append, a script): pack the pictures this import brought, inside
    the same step, so the drop's undo takes them back with it."""
    try:
        pack_landed_images(arrived=lambda image: image.as_pointer() not in _before)
    except Exception as error:  # noqa: BLE001 - an append never fails on this; saving tries again.
        pump_guard.report("studio_assets_landing.imported", error)
    finally:
        _before.clear()


def library_file(path, folder=None):
    """Whether ``path`` (the file being saved) is one of the library's own files (opened with Open Blend File);
    ``folder`` is the library's asset folder (default: this Angelo's)."""
    if not path:
        return False
    try:
        if folder is None:
            from . import studio_assets
            from .studio_state import library_directory
            folder = studio_assets.asset_directory(library_directory())
        folder = os.path.realpath(folder)
        return os.path.commonpath([os.path.realpath(path), folder]) == folder
    except (ImportError, OSError, ValueError):
        return False


@persistent
def _before_save(*_args):
    """What an import could not pack (Studio's file was missing then): a saved file never depends on Studio's folder
    for its pictures. Never inside the library's own files."""
    try:
        if not library_file(bpy.data.filepath):
            pack_landed_images()
    except Exception as error:  # noqa: BLE001 - a save never fails on this.
        pump_guard.report("studio_assets_landing.before_save", error)


LANDING_HANDLERS = (("blend_import_pre", _importing), ("blend_import_post", _imported), ("save_pre", _before_save))


def add_landing_handlers():
    for name, handler in LANDING_HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if handler not in handlers:
            handlers.append(handler)


def remove_landing_handlers():
    for name, handler in LANDING_HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if handler in handlers:
            handlers.remove(handler)
    if bpy.app.timers.is_registered(_free_unused):
        bpy.app.timers.unregister(_free_unused)
