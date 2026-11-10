# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How ``angelo_blend_file`` names a referenced file, and whether it is there. No Blender dependency.

A .blend stores the paths of its images, sounds, fonts, caches and libraries
as the artist's machine wrote them, and an absolute path carries folder names
(a user, a client, a project) that must not reach a model provider. So a file
is named to the model in one of two ways only:

* a path relative to the selected file workspace (the form the file tools
  take) or, failing that, to the .blend's own folder, when the file lies
  inside one of them;
* otherwise its base name alone, marked ``outside``.

``..`` never appears: a path that climbs out of both folders is outside. The
decision is lexical (``os.path.normpath``); nothing is opened or resolved to
make it. ``state`` then asks the filesystem one question, whether the file
exists, for the status the model reads.
"""

import ntpath
import os
import posixpath
import re

PACKED, PRESENT, MISSING, UNKNOWN = "packed", "present", "missing", "unknown"
BLEND_RELATIVE = "//"
# Blender's built-in font and other placeholders are not files.
NOT_FILES = frozenset({"", "<builtin>"})
_DRIVE = re.compile(r"^[A-Za-z]:[\\/]|^\\\\")
MAX_NAME = 255
_LETTERS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")


def _printf(pattern, values):
    """``pattern`` formatted as C's printf formats it, for the ``%d`` and ``%%`` it can hold; None for anything else
    (undefined in C)."""
    out, values, index = [], list(values), 0
    while index < len(pattern):
        if pattern[index] != "%":
            out.append(pattern[index])
            index += 1
            continue
        spec = pattern[index + 1:index + 2]
        if spec == "%":
            out.append("%")
        elif spec == "d" and values:
            out.append(str(values.pop(0)))
        else:
            return None
        index += 2
    return "".join(out)


def _tile_place(number):
    """A tile's column and row, counted from 1 (1001 is ``u1_v1``, 1012 ``u2_v2``)."""
    return (number - 1001) % 10 + 1, (number - 1001) // 10 + 1


def tile_file_path(path, number):
    """The file tile ``number`` of a tiled image with stored path ``path`` lives in, or None when it names no tile.

    Blender's own rule (``BKE_image_get_tile_strformat``, ``BKE_image_set_filepath_from_tile_number``): ``<UDIM>``
    wins and becomes the tile number (1001); otherwise ``<UVTILE>`` becomes ``u<column>_v<row>``. Every occurrence of
    the chosen token is replaced. The path with its token replaced is a printf format there, so a ``%%`` reads as
    ``%``; a path with any other ``%`` (undefined in C) is named with the token replaced only, as Cycles names it.
    """
    if "<UDIM>" in path:
        pattern, values, plain = path.replace("<UDIM>", "%d"), (number,), path.replace("<UDIM>", str(number))
    elif "<UVTILE>" in path:
        pattern, values = path.replace("<UVTILE>", "u%d_v%d"), _tile_place(number)
        plain = path.replace("<UVTILE>", "u%d_v%d" % values)
    else:
        return None
    named = _printf(pattern, values) if "%" in path else None
    return plain if named is None else named


def cycles_tile_file_path(path, number):
    """The file of tile ``number`` as Cycles names it when it reads a tile's file itself (``ImageManager::add_image``):
    both tokens replaced, ``<UDIM>`` and then ``<UVTILE>``, no printf. None when the path names no tile."""
    if "<UDIM>" not in path and "<UVTILE>" not in path:
        return None
    return path.replace("<UDIM>", "%04d" % number).replace("<UVTILE>", "u%d_v%d" % _tile_place(number))


def frame_number(scene_frame, duration, start, offset, cyclic):
    """The frame of a sequence or movie an image user (a texture node's) reads at ``scene_frame``.

    Blender's own rule, ``BKE_image_user_frame_get`` (a render, and so a bake, computes it for every image user):
    the scene frame counted from ``start`` (``frame_start``) as 1, wrapped into ``1..duration`` when ``cyclic``,
    else held within ``0..duration``, then shifted by ``offset``. A ``duration`` of 0 reads frame 0, unshifted.
    """
    if duration <= 0:
        return 0
    frame = scene_frame - start + 1
    frame = (frame % duration or duration) if cyclic else min(max(frame, 0), duration)
    return frame + offset


def _name_end(path, slash):
    """Where the file name's extension starts (``BLI_path_extension_or_end``): its last dot that has a character other
    than a dot before it within the name, else the end."""
    end, named = len(path), False
    for index in range(slash + 1, len(path)):
        if path[index] != ".":
            named = True
        elif named:
            end = index
    return end


def frame_file_path(path, frame):
    """The file of frame ``frame`` of an image sequence stored at ``path``, exactly as Blender names it.

    ``BLI_path_sequence_decode`` then ``_encode`` (``BKE_image_user_file_path_ex``): the file name's last run of
    digits before its extension is the frame, written at least as wide as that run, zero-padded (``seq.0001.png``,
    frame 12: ``seq.0012.png``; frame 12345: ``seq.12345.png``). A name without digits, or whose digits overflow a
    32-bit int, has the frame put before the extension, unpadded and left out for frame 0. A negative frame is 0.
    ``#`` is not a frame mark here (only output paths use it). Folder names never count, ``\\`` and ``/`` both
    separate.
    """
    frame = max(0, frame)
    slash = max(path.rfind("/"), path.rfind("\\"))
    name_end = _name_end(path, slash)
    last = name_end - 1
    while last > slash and not "0" <= path[last] <= "9":
        last -= 1
    if last > slash:
        first = last
        while first - 1 > slash and "0" <= path[first - 1] <= "9":
            first -= 1
        if int(path[first:last + 1]) <= 0x7FFFFFFF:
            return "%s%0*d%s" % (path[:first], last + 1 - first, frame, path[last + 1:])
    return "%s%s%s" % (path[:name_end], frame or "", path[name_end:])


STEREO_VIEWS = ("left", "right")   # Blender's two stereo views, by name (STEREO_LEFT_NAME, STEREO_RIGHT_NAME).


def view_suffixes(image, render):
    """The file suffixes of the views whose separate files Blender reads for ``image``, in the scene's view order;
    empty when it reads one file.

    ``image_update_views_format``: only an image using multi-view (``use_multiview``) with views in separate files
    (``views_format`` ``INDIVIDUAL``) in a scene rendering multi-view, and of the scene's views those
    ``BKE_scene_multiview_is_render_view_active`` allows: an enabled one, of any name with the Multi-View setup,
    else ``left`` or ``right`` only.
    """
    if not (render.use_multiview and image.use_multiview and image.views_format == "INDIVIDUAL"):
        return ()
    return tuple(view.file_suffix for view in render.views
                 if view.use and (render.views_format == "MULTIVIEW" or view.name in STEREO_VIEWS))


def view_file_paths(path, suffixes):
    """The file of each view of an image stored at ``path``, given its ``view_suffixes``; empty for one file.

    ``BKE_scene_multiview_view_prefix_get``: the stored path, split at its last dot (anywhere in it), must end in the
    first suffix (in order) it ends with; what comes before is the prefix, and each view's file is prefix, suffix and
    extension (``shot_L.png`` with ``_L`` and ``_R``: ``shot_L.png``, ``shot_R.png``). No dot, no matching suffix or
    an empty prefix: one file. Blender keeps the views whose files exist; all of them are named here.
    """
    dot = path.rfind(".")
    if dot < 0 or not suffixes:
        return []
    base, extension = path[:dot], path[dot:]
    for suffix in suffixes:
        if base.endswith(suffix):
            prefix = base[:len(base) - len(suffix)]
            return [prefix + each + extension for each in suffixes] if prefix else []
    return []


def read_files(path, source, *, tiles=(), frame=0, suffixes=()):
    """Every file Blender reads for an image stored at ``path`` (its raw path, before ``//`` is resolved).

    One name for each file, as ``BKE_image_user_file_path_ex`` builds it: each view's file (``view_file_paths``, else
    the path), then for a ``SEQUENCE`` the file of ``frame`` (``frame_number``, ``frame_file_path``) and for a
    ``TILED`` image the file of each tile number in ``tiles`` (``tile_file_path``); a ``MOVIE`` or ``FILE`` is the
    view's file itself. In order, without repeats.
    """
    bases = view_file_paths(path, suffixes) or [path]
    if source == "SEQUENCE":
        named = [frame_file_path(base, frame) for base in bases]
    elif source == "TILED":   # Blender's tile name, and Cycles' when it differs (a path with both tokens).
        named = [name(base, number) or base for base in bases for number in tiles
                 for name in (tile_file_path, cycles_tile_file_path)]
    else:
        named = bases
    return list(dict.fromkeys(named))


def image_files(image, user, scene_frame, abspath, *, suffixes=(), tiles=None):
    """Every file Blender reads for ``image`` as ``user`` (its node's image user, None for none) reads it at
    ``scene_frame``, absolute (``located``, through ``abspath``): ``read_files`` of the stored path, for a sequence the
    file of the frame the image user reads (``frame_number``; without an image user the stored file), for a tiled
    image each tile's but a generated tile's (or only ``tiles``), and each view's of ``suffixes``. A reader keyed on
    these files is keyed on what a render opens, not on the stored file alone (1.0.10 audit: a sequence's other
    frame, a later tile)."""
    source, frame = image.source, 0
    if source == "SEQUENCE":
        if user is None:
            source = "FILE"
        else:
            frame = frame_number(scene_frame, user.frame_duration, user.frame_start, user.frame_offset,
                                 user.use_cyclic)
    if source == "TILED" and tiles is None:
        tiles = [tile.number for tile in image.tiles if not tile.is_generated_tile]
    raw = getattr(image, "filepath_raw", None) or image.filepath
    found = (located(path, getattr(image, "library", None), abspath)
             for path in read_files(raw, source, tiles=tiles or (), frame=frame, suffixes=suffixes))
    return [os.path.normpath(path) if path else "" for path in found]   # One spelling per file; normpath("") is ".".


def base_name(raw):
    """The last component of a stored path, whichever platform wrote it."""
    return re.split(r"[\\/]", raw.rstrip("\\/"))[-1][:MAX_NAME]


def absolute(raw, blend_folder):
    """The absolute path a stored path means, or None when it cannot be known.

    ``//`` is relative to the .blend's folder, unknown while the file is
    unsaved. A path written on Windows cannot be located from another platform.
    """
    if raw.startswith(BLEND_RELATIVE):
        if not blend_folder:
            return None
        return os.path.normpath(os.path.join(blend_folder, raw[2:].replace("\\", os.sep)))
    if _DRIVE.match(raw) and os.name != "nt":
        return None
    if not os.path.isabs(raw):
        return None  # Blender stores absolute or //-relative paths; anything else has no known base.
    return os.path.normpath(raw)


def _inside(path, folder):
    """``path`` relative to ``folder`` when it lies inside it, else None (lexical; case-insensitive on Windows)."""
    if not folder:
        return None
    module = ntpath if os.name == "nt" else posixpath
    folder = module.normpath(folder)
    try:
        relative = module.relpath(path, folder)
    except ValueError:  # Another drive.
        return None
    if relative == module.curdir or relative == module.pardir or relative.startswith(module.pardir + module.sep):
        return None
    return relative.replace("\\", "/")


def public_name(raw, blend_folder, workspace_root=None):
    """``{"file": name}`` for the model, with ``outside`` true when only the base name can be given."""
    located = absolute(raw, blend_folder)
    if located is not None:
        for folder in (workspace_root, blend_folder):
            relative = _inside(located, folder)
            if relative:
                return {"file": relative[:1024]}
    return {"file": base_name(raw), "outside": True}


def native_spelling(path):
    """A stored path spelled as Blender reads it on this platform (``BLI_path_abs``): on macOS and Linux a Windows
    drive (``C:\\`` or ``C:/``) becomes ``/c/``, and every backslash after a UNC prefix (``\\\\``, ``\\\\?\\``)
    becomes ``/``, so a .blend saved on Windows names the file Blender opens. Windows reads either separator."""
    if os.name == "nt":
        return path
    if len(path) > 2 and path[0] in _LETTERS and path[1] == ":" and path[2] in "\\/":
        path = "/" + path[0].lower() + path[2:]
    unc = 4 if path.startswith("\\\\?\\") else 2 if path.startswith("\\\\") else 0
    return path[:unc] + path[unc:].replace("\\", "/")


def located(raw, library, abspath):
    """The absolute path Blender opens for a stored path ``raw`` of a datablock linked from ``library`` (None: the
    open file's own). ``abspath`` is ``bpy.path.abspath``.

    Both the path and the library's own path are spelled as Blender reads them (``native_spelling``): ``//`` is the
    library's folder (``ID_BLEND_PATH``), whose absolute path ``BKE_library_filepath_set`` builds with
    ``BLI_path_abs`` against the open file, whatever library links it.
    """
    path = native_spelling(raw)
    if library is None:
        return abspath(path)
    return abspath(path, start=os.path.dirname(abspath(native_spelling(library.filepath))))


def node_file_places(raw, library, abspath):
    """Each absolute path Cycles may open for a node's external file (an IES or script file) stored as ``raw`` in a
    tree from ``library`` (None: the open file's own). Cycles 5.2 inlines every material tree, groups included, into
    a copy with no library before it resolves paths (``add_nodes``, ``blender_absolute_path``), so ``//`` is the open
    file's folder even in a linked tree; the library's folder, where Blender itself resolves the tree's paths, is named
    too, so neither reading can leave a changed file unstamped."""
    places = {os.path.normpath(located(raw, None, abspath))}
    if library is not None:
        places.add(os.path.normpath(located(raw, library, abspath)))
    return sorted(places)


def first_tile(block):
    """The number of a tiled image's first tile, which stands for its set of files; None for anything else."""
    tiles = getattr(block, "tiles", None) if getattr(block, "source", None) == "TILED" else None
    return tiles[0].number if tiles else None


def state(raw, blend_folder, *, packed=False, exists=None, tile=None):
    """``packed``, ``present``, ``missing`` or ``unknown`` (unsaved file, foreign platform path).

    ``exists``: the one filesystem question asked, ``os.path.exists`` unless a test supplies its own. ``tile``: a tiled
    image's ``first_tile``. Its path names its tiles with a token, and the file asked about is that tile's, as in
    Blender's own missing-file check (``BKE_bpath_missing_files_check`` resolves the token with the image's first
    tile, whatever its number: a set of 1002 to 1005 is there when 1002 is). Any other path is asked about as written.
    """
    exists = exists or os.path.exists
    if packed:
        return PACKED
    located = absolute(raw, blend_folder)
    if located is None:
        return UNKNOWN
    if tile is not None:
        located = tile_file_path(located, tile) or located
    try:
        return PRESENT if exists(located) else MISSING
    except OSError:
        return UNKNOWN
