# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the converter keeps of an imported Fab file, and how it makes the rest safe. Converter child only (bpy).

* **The asset is its objects.** Geometry, armatures, lattices and the empties that hold or instance them are kept,
  with every ancestor so transforms stay; cameras, light probes and speakers are presentation and are left out, as
  are lights that no kept object carries (a lamp model keeps its bulb). The world is never written.
* **Nothing can run.** Text blocks are removed, every driver whose expression needs Python is removed (Blender's
  own simple expressions and the averaging/sum/min/max kinds stay: they never call Python), and script (OSL) nodes
  are removed. External caches (Mesh Cache, Mesh Sequence Cache, Alembic/USD cache files) are removed.
* **Textures live in the file.** Every image a kept material uses is read from inside the unpacked folder and
  packed. A path that points elsewhere (an author's own disk) is relinked by its file name to the one image of that
  name in the download; an image with no such file is dropped and counted (the material keeps working without it).
  Movies and image sequences are dropped too.
"""

import os
from pathlib import Path

import bpy
import numpy as np

import fab_convert_guard as guard

ASSET_TYPES = frozenset({'MESH', 'CURVE', 'SURFACE', 'META', 'FONT', 'CURVES', 'POINTCLOUD', 'GREASEPENCIL',
                         'GPENCIL', 'ARMATURE', 'LATTICE', 'EMPTY'})   # Volumes read external grids: left out.
PRESENTATION = frozenset({'CAMERA', 'LIGHT_PROBE', 'SPEAKER'})
_EXTERNAL_MODIFIERS = frozenset({'MESH_CACHE', 'MESH_SEQUENCE_CACHE'})


def excluded_collections(scene):
    """Collections excluded from the scene's first view layer (an author's hidden alternatives)."""
    excluded = set()

    def walk(layer):
        if layer.exclude:
            excluded.add(layer.collection)
            for child in layer.collection.children_recursive:
                excluded.add(child)
            return
        for child in layer.children:
            walk(child)
    if scene.view_layers:
        walk(scene.view_layers[0].layer_collection)
    return excluded


def keep_asset_objects(objects):
    """``(kept, skipped)``: the asset's objects with all their ancestors, and counts of what was left out."""
    objects = list(objects)
    chosen = [o for o in objects if o.type in ASSET_TYPES]
    carriers = set(chosen)
    for obj in objects:
        if obj.type == 'LIGHT' and _ancestors(obj) & carriers:
            chosen.append(obj)
    kept = []
    for obj in chosen:
        for item in [*reversed(list(_chain(obj))), obj]:
            if item not in kept:
                kept.append(item)
    # An empty that holds nothing kept and instances nothing is a leftover helper (an exporter's root null).
    holders = {o.parent for o in kept if o.parent is not None}
    kept = [o for o in kept if o.type != 'EMPTY' or o in holders or o.instance_collection is not None]
    skipped = {}
    for obj in objects:
        if obj not in kept:
            key = 'cameras' if obj.type == 'CAMERA' else 'lights' if obj.type == 'LIGHT' else 'other'
            skipped[key] = skipped.get(key, 0) + 1
    return kept, skipped


def _chain(obj):
    parent = obj.parent
    while parent is not None:
        yield parent
        parent = parent.parent


def _ancestors(obj):
    return set(_chain(obj))


def scale_roots(objects, factor):
    """A source file's unit scale, applied so 1 unit is 1 metre in the result (a centimetre scene: 0.01)."""
    for obj in objects:
        if obj.parent is None:
            obj.scale *= factor
            obj.location *= factor


def _id_collections():
    return [getattr(bpy.data, prop.identifier) for prop in bpy.data.bl_rna.properties
            if prop.type == 'COLLECTION' and prop.identifier not in ('window_managers', 'workspaces', 'screens')]


def _owners():
    for data in _id_collections():
        for block in data:
            if not isinstance(block, bpy.types.ID):
                continue
            yield block
            tree = getattr(block, 'node_tree', None)
            if tree is not None:
                yield tree
            keys = getattr(block, 'shape_keys', None)
            if keys is not None:
                yield keys


def sanitize(folder, images):
    """Remove everything that could run or read outside the download; returns the counts."""
    report = {'drivers_removed': 0, 'texts_removed': 0, 'script_nodes_removed': 0, 'external_removed': 0,
              'missing_textures': 0, 'relinked_textures': 0}
    for owner in list(_owners()):
        animation = getattr(owner, 'animation_data', None)
        if animation is not None:
            for curve in list(animation.drivers):
                driver = curve.driver
                if driver.type == 'SCRIPTED' and not driver.is_simple_expression:
                    animation.drivers.remove(curve)
                    report['drivers_removed'] += 1
        for node in list(getattr(owner, 'nodes', ())):
            if node.bl_idname == 'ShaderNodeScript':
                owner.nodes.remove(node)
                report['script_nodes_removed'] += 1
        for modifier in list(getattr(owner, 'modifiers', ())):
            if modifier.type in _EXTERNAL_MODIFIERS:
                owner.modifiers.remove(modifier)
                report['external_removed'] += 1
    for text in list(bpy.data.texts):
        bpy.data.texts.remove(text)
        report['texts_removed'] += 1
    for data in (bpy.data.cache_files, bpy.data.movieclips, bpy.data.sounds, bpy.data.volumes):
        for block in list(data):
            data.remove(block)
            report['external_removed'] += 1
    return report


def _index(folder, images):
    by_name = {}
    for relative in images:
        by_name.setdefault(Path(relative).name.casefold(), []).append((folder / relative).resolve())
    return by_name


def _clear_users(image):
    for material in bpy.data.materials:
        tree = material.node_tree
        for node in (tree.nodes if tree else ()):
            if getattr(node, 'image', None) == image:
                node.image = None
    for group in bpy.data.node_groups:
        for node in group.nodes:
            if getattr(node, 'image', None) == image:
                node.image = None
    bpy.data.images.remove(image)


def pack_images(folder, report, images=None, embedded=frozenset()):
    """Pack every image from inside ``folder``, relinking by name; drop what cannot be found or came from outside.

    A packed image may stay only when ``guard.source_allowed``: embedded data, or a source in the download or this
    child's own temporary folder (where the USD reader copies a USDZ package's textures), or it is in ``embedded``:
    data an importer packed from the downloaded file itself (an FBX's embedded textures). Security review
    2026-11-11, F1: an importer that packs by itself must never carry a file from elsewhere into the scene."""
    folder = Path(folder).resolve()
    roots = [str(folder)] + ([os.environ['TMPDIR']] if os.environ.get('TMPDIR') else [])
    index = _index(folder, images if images is not None else
                   [str(p.relative_to(folder)) for p in folder.rglob('*') if p.is_file() and not p.is_symlink()])
    for image in list(bpy.data.images):
        raw = image.filepath_raw or image.filepath
        if len(image.packed_files):   # First, before any skip: every packed image meets the check (re-review r7).
            allowed = image.session_uid in embedded or guard.source_allowed(raw, roots, bpy.path.abspath)
            layout = guard.packed_layout(image.source, allowed=allowed, tiles=len(image.tiles),
                                         packed=len(image.packed_files),
                                         multiview=bool(image.use_multiview or image.is_multiview),
                                         tile_numbers=_tile_numbers(image), pattern=raw)
            if layout == 'outside':
                _clear_users(image)
                report['outside_removed'] = report.get('outside_removed', 0) + 1
                print('ANGELO_FAB_DROPPED outside texture', flush=True)
            elif layout == 'remove_packed':
                # Frees the packed records without writing a file (packedFile.cc PF_REMOVE); the pattern stays.
                image.unpack(method='REMOVE')
                if len(image.packed_files):
                    _clear_users(image)
                    report['external_removed'] = report.get('external_removed', 0) + 1
            elif layout == 'drop':
                _clear_users(image)
                report['external_removed'] = report.get('external_removed', 0) + 1
            elif layout == 'single_view':
                if not _single_view(image, report):
                    report['external_removed'] = report.get('external_removed', 0) + 1
            elif layout == 'trim_tiles':
                packed = {entry.tile_number for entry in image.packed_files}
                for tile in [t for t in image.tiles if t.number not in packed]:
                    image.tiles.remove(tile)
                    report['trimmed_tiles'] = report.get('trimmed_tiles', 0) + 1
            continue
        if image.source in ('GENERATED', 'VIEWER'):
            continue
        if image.source in ('MOVIE', 'SEQUENCE'):
            _clear_users(image)
            report['external_removed'] = report.get('external_removed', 0) + 1
            continue
        if image.source == 'TILED' and (not guard.tiles_valid(_tile_numbers(image))
                                        or guard.tile_format(raw or '') == 'refused'):
            _clear_users(image)   # A tile set or pattern Blender would not handle (re-review r6 R4, R5).
            report['external_removed'] = report.get('external_removed', 0) + 1
            continue
        if image.source == 'TILED' and guard.tile_format(raw or '') != 'none':
            _pack_tiled(image, raw, folder, index, report)
            continue
        target = guard.resolved(bpy.path.abspath(raw)) if raw else None
        if not guard.inside_any(target, [str(folder)]) or not os.path.isfile(target):
            found = index.get(Path(raw.replace('\\', '/')).name.casefold(), []) if raw else []
            if len(found) != 1:
                _clear_users(image)
                report['missing_textures'] = report.get('missing_textures', 0) + 1
                continue
            image.filepath = str(found[0])
            report['relinked_textures'] = report.get('relinked_textures', 0) + 1
        try:
            image.pack()
        except RuntimeError:
            _clear_users(image)
            report['missing_textures'] = report.get('missing_textures', 0) + 1
    _pack_fonts(folder)


def _tile_numbers(image):
    """A tiled image's tile numbers, or None when it holds more than Blender's range can (never listed then)."""
    if len(image.tiles) > guard.MAX_TILES:
        return None
    return [tile.number for tile in image.tiles]


def _pack_tiled(image, raw, folder, index, report):
    """An unpacked tiled (UDIM) image: each tile's own file must be inside the download (else relinked by name into
    the folder holding most tiles); tiles with no file are removed; then every tile is packed (re-review r5)."""
    tiles = [tile.number for tile in image.tiles]
    present = [tile for tile, path in guard.tile_paths(bpy.path.abspath(raw), tiles)
               if guard.inside_any(guard.resolved(path), [str(folder)]) and os.path.isfile(guard.resolved(path))]
    if not present:
        relinked, present = guard.tile_relink(raw, tiles, index)
        if relinked is None:
            _clear_users(image)
            report['missing_textures'] = report.get('missing_textures', 0) + 1
            return
        image.filepath = relinked
        report['relinked_textures'] = report.get('relinked_textures', 0) + 1
    for tile in [t for t in image.tiles if t.number not in present]:
        image.tiles.remove(tile)
        report['trimmed_tiles'] = report.get('trimmed_tiles', 0) + 1
    try:
        image.pack()
    except RuntimeError:
        _clear_users(image)
        report['missing_textures'] = report.get('missing_textures', 0) + 1


def _single_view(image, report):
    """A multiview image's first view as a plain packed image in its place; False when it has no pixels to keep or
    is larger than the caps (a packed .blend image is not capped by the archive: re-review r6 R2)."""
    width, height = image.size
    if not guard.size_allowed(width, height):
        _clear_users(image)
        return False
    pixels = np.empty(width * height * image.channels, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    single = bpy.data.images.new(image.name, width, height, alpha=image.channels == 4, float_buffer=image.is_float)
    # Colour space and alpha mode first: changing either frees the image buffer, so set after the pixels they would
    # leave nothing dirty for pack() to write, and the image would stay an empty generated one.
    single.colorspace_settings.name = image.colorspace_settings.name
    single.alpha_mode = image.alpha_mode
    single.pixels.foreach_set(pixels)
    del pixels
    single.pack()
    if single.packed_file is None:
        bpy.data.images.remove(single)
        _clear_users(image)
        return False
    image.user_remap(single)
    bpy.data.images.remove(image)
    report['single_view_images'] = report.get('single_view_images', 0) + 1
    return True


def _pack_fonts(folder):
    """A text object's font from inside the download is packed; any other font falls back to Blender's own."""
    builtin = None
    for font in list(bpy.data.fonts):
        if font.packed_file is not None or font.filepath == '<builtin>':
            continue
        target = guard.resolved(bpy.path.abspath(font.filepath))   # Never opens a network or device path.
        if guard.inside_any(target, [str(folder)]) and os.path.isfile(target):
            try:
                font.pack()
                continue
            except RuntimeError:
                pass
        builtin = builtin or bpy.data.fonts.load('<builtin>')
        for curve in bpy.data.curves:
            for slot in ('font', 'font_bold', 'font_italic', 'font_bold_italic'):
                if getattr(curve, slot, None) == font:
                    setattr(curve, slot, builtin)
        bpy.data.fonts.remove(font)
