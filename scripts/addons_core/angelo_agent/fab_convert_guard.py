# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The converter child's own guards, free of bpy so the host tests prove them: where a texture may come from, which
USD dependencies leave the download, and a deadline the child keeps even when the app is gone.

* ``source_allowed``: a packed image is carried into the scene only when it holds embedded data (no recorded path)
  or its recorded path, resolved with links followed, lies in one of the allowed roots: the unpacked download, or
  the child's own temporary folder, where Blender's USD reader copies a USDZ package's textures before packing them.
  Anything else was read from outside the download (an author's disk, ``..`` escapes) and is dropped.
* ``usd_escapes``: every layer and asset a USD file depends on (``UsdUtils.ComputeAllDependencies``: sublayers,
  references, payloads, value clips, shader asset paths and UDIM sets) must resolve inside the download; the ones
  that do not are listed, and the child refuses the file before Blender's importer reads any of them.
* ``watchdog``: the child ends itself at its deadline or when its parent process is gone, so an app that quits or
  crashes mid-conversion never leaves a converter running.

Standard library only. Imported as a plain sibling module by the converter child.
"""

import ntpath
import os
import threading
import time

try:
    from . import untrusted_paths
except ImportError:   # The converter child imports its sibling modules plainly.
    import untrusted_paths


def network_or_device(raw):
    """Whether ``raw`` is not a local file path's shape (``untrusted_paths.local_form``): another computer, a device,
    a stream, a URL or a reserved character. Decided from the text alone, before anything opens or resolves it."""
    return not untrusted_paths.local_form(raw)


def resolved(path, base=None):
    """``path`` made absolute (against ``base`` when relative) with links followed; None when unusable, or when it
    names another computer or a device (never opened to find out)."""
    if not path or '\x00' in path or network_or_device(path):
        return None
    try:
        if not os.path.isabs(path):
            path = os.path.join(base or os.getcwd(), path)
        return os.path.realpath(path)
    except (OSError, ValueError):
        return None


def inside_any(path, roots):
    """Whether resolved ``path`` lies in one of ``roots`` (themselves resolved)."""
    if path is None:
        return False
    for root in roots:
        root = os.path.realpath(root)
        try:
            if os.path.commonpath([root, path]) == root:
                return True
        except ValueError:   # Another drive on Windows.
            continue
    return False


def source_allowed(raw, roots, abspath=lambda value: value):
    """Whether a packed image whose recorded path is ``raw`` may stay. ``abspath`` expands Blender's ``//``."""
    if not raw:
        return True   # Embedded data (a glTF buffer image): no file was read.
    source = raw.split('[', 1)[0] if raw.endswith(']') and '[' in raw else raw   # "pkg.usdz[0/tex.jpg]"
    return inside_any(resolved(abspath(source)), roots)


def names_a_root(text):
    """Whether ``text`` names a root or a drive in either system's form (``/x``, ``\\x``, ``C:\\x``, ``C:x``):
    a place outside any download, whichever system wrote the file. ``os.path.isabs`` alone misses ``/x`` on Windows
    (Python 3.13), where it is the current drive's root."""
    return text.startswith(('/', '\\')) or bool(ntpath.splitdrive(text)[0])


def usd_escapes(dependencies, folder):
    """The resolved paths of ``(layers, assets, unresolved)`` that leave ``folder`` (unresolved ones by their text)."""
    layers, assets, unresolved = dependencies
    escaping = []
    for path in [*layers, *assets]:
        text = str(path)
        package = text.split('[', 1)[0] if '[' in text else text
        if not inside_any(resolved(package), [folder]):
            escaping.append(text)
    for path in unresolved:
        text = str(path)
        # A path the resolver could not find reads nothing, but one that names a place outside is refused too.
        if '://' in text or names_a_root(text) or '..' in text.replace('\\', '/').split('/'):
            escaping.append(text)
    return escaping


# Blender's own limits (BKE_image.hh IMA_UDIM_MAX; image.cc only reads tiles 1001..IMA_UDIM_MAX).
UDIM_FIRST, UDIM_LAST = 1001, 2000
MAX_TILES = UDIM_LAST - UDIM_FIRST + 1
# The Team Library's image caps (team_library_files.MAX_SIDE, MAX_PIXELS; a host test keeps them equal): a packed
# image inside a downloaded .blend is not capped by the archive, so whatever the child copies pixel by pixel is.
MAX_SIDE = 16384
MAX_PIXELS = 8192 * 8192


def packed_layout(source, *, allowed, tiles, packed, multiview, tile_numbers=None, pattern=''):
    """How a packed image is made one the app can carry; one texture never refuses a download (re-review r5).

    'outside' first, whatever the layout or source (re-review r6 M1, r7): ``allowed`` is the packed-source check
    (its own embedded data, or a source inside the download), the backstop for EVERY packed image. Then
    'remove_packed' (a generated image still holding bytes from before its Source was set back to Generated: it keeps
    its pattern and loses only the bytes, which Blender never shows); 'drop' (a packed movie or image sequence, like
    an unpacked one; packed data on a viewer image; a tiled image whose tile set or path pattern Blender would not
    handle, ``tiles_valid`` and ``tile_format``); 'single_view' (a multiview image keeps one packed view);
    'trim_tiles' (a tiled image packed in part: its unpacked tiles are removed, they name missing or outside files
    anyway); 'keep'."""
    if not allowed:
        return 'outside'
    if source == 'GENERATED':
        return 'remove_packed'   # Packed, then Source set back to Generated: the bytes are never shown (r7, r8).
    if source in ('MOVIE', 'SEQUENCE', 'VIEWER'):
        return 'drop'
    if source == 'TILED' and (not tiles_valid(tile_numbers) or tile_format(pattern) == 'refused'):
        return 'drop'
    if multiview:
        return 'single_view'
    if source == 'TILED' and packed != tiles:
        return 'trim_tiles'
    return 'keep'


def tiles_valid(numbers):
    """Whether a tiled image's tile numbers (from the downloaded file) are a set Blender makes: 1..MAX_TILES distinct
    numbers in UDIM_FIRST..UDIM_LAST (re-review r6 R5). Callers check the count before listing the numbers."""
    if not numbers or len(numbers) > MAX_TILES or len(set(numbers)) != len(numbers):
        return False
    return all(isinstance(n, int) and UDIM_FIRST <= n <= UDIM_LAST for n in numbers)


def size_allowed(width, height):
    """Whether an image of this size may be copied pixel by pixel (the Team Library's caps)."""
    return 0 < width <= MAX_SIDE and 0 < height <= MAX_SIDE and width * height <= MAX_PIXELS


def tile_format(pattern):
    """'udim', 'uvtile', 'none' (no token), or 'refused' (re-review r6 R4). Blender's BKE_image_get_tile_strformat
    turns EVERY ``<UDIM>`` (or, when there is none, every ``<UVTILE>``) into a printf directive and formats the path
    with one tile number; a second token, or any other '%', is undefined behaviour there, so it is refused rather
    than guessed. Tokens match case exactly, as Blender's strstr does."""
    count = pattern.count('<UDIM>') + pattern.count('<UVTILE>')
    if not count:
        return 'none'
    if count > 1 or '%' in pattern:
        return 'refused'
    return 'udim' if '<UDIM>' in pattern else 'uvtile'


def tile_paths(pattern, tiles):
    """``[(tile, path)]`` for a tiled image's one-token path and its tiles in Blender's range; None for a path
    without a token or a refused one (``tile_format``)."""
    kind = tile_format(pattern)
    if kind in ('none', 'refused'):
        return None
    found = []
    for tile in tiles:
        if not UDIM_FIRST <= tile <= UDIM_LAST:
            continue
        offset = tile - UDIM_FIRST
        token = '%d' % tile if kind == 'udim' else 'u%d_v%d' % (offset % 10 + 1, offset // 10 + 1)
        found.append((tile, pattern.replace('<UDIM>' if kind == 'udim' else '<UVTILE>', token)))
    return found


def tile_relink(pattern, tiles, index):
    """Relink a tiled image by name: the download folder holding most of its tile files (``index``: casefolded file
    name -> paths inside the download). ``(new pattern, tiles found)``, or ``(None, [])`` when none is there."""
    if tile_format(pattern) == 'refused':
        return None, []   # A refused pattern is never expanded, not even its file name (re-review r6 R4).
    name = pattern.replace('\\', '/').rpartition('/')[2]
    by_folder = {}
    for tile, expected in tile_paths(name, tiles) or ():
        for path in index.get(expected.casefold(), ()):
            by_folder.setdefault(os.path.dirname(path), []).append(tile)
    if not by_folder:
        return None, []
    folder = max(sorted(by_folder), key=lambda key: len(by_folder[key]))
    return os.path.join(folder, name), sorted(by_folder[folder])


def watchdog(seconds, parent, *, exit=os._exit, parent_now=os.getppid, clock=time.monotonic, sleep=time.sleep,
             start=True):
    """End this process ``seconds`` from now, or as soon as ``parent`` is no longer its parent (it exited)."""
    deadline = clock() + seconds

    def watch():
        while True:
            if clock() >= deadline:
                exit(124)
                return
            if parent is not None and parent_now() != parent:
                exit(125)
                return
            sleep(0.5)
    thread = threading.Thread(target=watch, name='fab-convert-watchdog', daemon=True)
    if start:
        thread.start()
    return thread
