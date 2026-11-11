# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Append a selected Essentials asset through Blender, with isolated inspection, rollback and one Undo step.

Only a hash-verified file selected by blender_assets reaches here. No arbitrary path or name is accepted from a
tool call. Objects keep their authored transforms; a root translation places them at the requested point.
Unapplied brushes/node groups are retained in the file and reported as loaded, never as activated/applied.
"""

import bpy
from pathlib import Path
from mathutils import Vector

from . import blender_asset_index as index, blender_assets, library_import, team_library_import as base
from .team_library_files import ImportFailed, NotImported, Refused


def ids(data):
    return {block.as_pointer(): block for prop in data.bl_rna.properties if prop.type == 'COLLECTION'
            for block in getattr(data, prop.identifier) if isinstance(block, bpy.types.ID)}


def _append(data, row):
    collection = index.TYPES[row['id_type']][0]
    previous_libraries = {lib.as_pointer() for lib in data.libraries}
    files = row.get('files', (row['file'],))
    allowed = {Path(file['path']).resolve() for file in files}
    routes = dict(row.get('_source_paths', ()))
    originals = {private: original for original, private in routes.items()}
    if not routes or len(originals) != len(routes) or set(originals) != {str(path) for path in allowed}:
        raise NotImported('This asset has no complete held private dependency route.')
    def dependency(file, name):
        # Relative tokens are relative to the ORIGINAL source parent. Absolute tokens
        # likewise identify that original member, never permission to reopen its path.
        original = dict(file, path=originals[str(Path(file['path']).resolve())])
        logical = str(blender_assets.dependency_path(original, name))
        private = routes.get(logical, logical if logical in originals else None)
        if private is None or Path(private) not in allowed:
            raise Refused('This asset depends on an unverified .blend file; it was not imported.')
        return private
    def approved_library(name):
        # The native route keeps serialized Library.filepath tokens intact while its
        # runtime absolute path names the private copy. Test each approved source parent
        # for a relative token; never reopen an original path to resolve that identity.
        for file in files:
            try:
                dependency(file, name)
                return True
            except Refused:
                continue
        return False
    # Check headers for every dependency before loading any of them. The online protocol is self-contained;
    # only the installed bundled catalog may have a transitive dependency closure.
    for file in files:
        with data.libraries.load(file['path'], link=False) as (source, _target):
            for library in source.libraries:
                if row['origin'] != 'bundled':
                    raise Refused('This asset depends on an unverified .blend file; it was not imported.')
                dependency(file, library.filepath)
    with data.libraries.load(row['file']['path'], assets_only=True, link=False, recursive=True) as (source, target):
        if source.libraries and row['origin'] != 'bundled':
            raise Refused('This asset depends on another .blend file; it was not imported.')
        name = row.get('datablock', row['name'])   # A Fab conversion names its datablock; Essentials use the title.
        if name not in getattr(source, collection):
            raise Refused('The selected asset is no longer in this file. Search again.')
        setattr(target, collection, [name])
    block = getattr(target, collection)[0]
    if block is None:
        raise Refused('Blender did not load the selected asset.')
    # libraries.load(link=False) leaves its source Library ID even though the appended IDs are local.
    # Library IDs have an internal user even with no linked data. Check actual linked IDs, not that counter.
    for library in list(data.libraries):
        if (library.as_pointer() not in previous_libraries
                and approved_library(library.filepath)
                and not any(block.library == library for block in ids(data).values())):
            data.libraries.remove(library)
    return block


def _inspect_packed_image(image):
    """Prove the actual stock packed slot profile; never rely on first packed_file truthiness.

    FILE and TILED single-view images are supported. Dynamic sources and multiview are
    explicit input-layout refusals: RNA does not expose the complete native Image.views
    inventory, and stock loading may discard a partial pack and fall back to the disk.
    """
    if image.source in {'MOVIE', 'SEQUENCE'}:
        raise Refused('This asset uses a movie or image sequence. Use packed still textures '
                      'in a separately saved source asset before importing it.')
    if image.source not in {'FILE', 'TILED'}:
        return  # Generated/viewer images do not use the file-loading fallback.
    if image.use_multiview or image.is_multiview:
        raise Refused('This asset uses multiview textures. Pack single-view textures in a '
                      'separately saved source asset before importing it.')
    tiles = tuple(tile.number for tile in image.tiles)
    expected = {(0, tile) for tile in tiles}
    packed = tuple(image.packed_files)
    actual = {(entry.view, entry.tile_number) for entry in packed}
    if (not tiles or len(expected) != len(tiles) or len(packed) != len(expected)
            or actual != expected
            or any(entry.packed_file is None or entry.packed_file.size <= 0 for entry in packed)):
        raise Refused('This asset has incomplete packed textures. Pack every image tile in '
                      'a separately saved source asset before importing it.')


def needs_python(driver):
    """A driver Blender evaluates with Python: a scripted expression outside its own simple-expression evaluator.
    Averaging, sum, minimum, maximum and simple expressions never call Python, with or without Auto Run."""
    return driver.type == 'SCRIPTED' and not driver.is_simple_expression


def _inspect(data):
    """Reject executable/external dependencies before any asset enters the artist's Main or dependency graph."""
    for block in ids(data).values():
        if block.id_type in {'TEXT', 'LIBRARY', 'CACHEFILE', 'MOVIECLIP', 'VOLUME'}:
            raise Refused('This asset contains an unsupported external or executable dependency.')
        if block.id_type == 'IMAGE':
            _inspect_packed_image(block)
        if block.id_type in {'SOUND', 'FONT'} and not getattr(block, 'packed_file', None) \
                and getattr(block, 'filepath', '') != '<builtin>':
            raise Refused('This asset contains unpacked media.')
        if block.id_type == 'OBJECT':
            for modifier in block.modifiers:
                # Mesh Cache reads MDD/PC2 directly from this string, not a CacheFile ID.
                # Such a file is not a member of the approved .blend-only closure.
                if modifier.type == 'MESH_CACHE' and modifier.filepath:
                    raise Refused('This asset uses an external Mesh Cache file. Bake the geometry '
                                  'into a separately saved source asset before importing it.')
        for owner in (block, getattr(block, 'node_tree', None), getattr(block, 'shape_keys', None)):
            animation = getattr(owner, 'animation_data', None)
            if animation and any(needs_python(curve.driver) for curve in animation.drivers):
                raise Refused('This asset contains drivers that run Python; it was not imported.')
            for node in getattr(owner, 'nodes', ()):
                if node.bl_idname == 'ShaderNodeScript':
                    raise Refused('This asset contains a script shader; it was not imported.')
                if (node.bl_idname == 'ShaderNodeTexIES' and getattr(node, 'mode', '') == 'EXTERNAL'
                        and getattr(node, 'filepath', '')):
                    raise Refused('This asset contains an external IES file; it was not imported.')


def inspect(row):
    with bpy.data.temp_data() as data:
        block = _append(data, row)
        _inspect(data)
        return {'node_type': getattr(block, 'bl_idname', ''),
                'is_modifier': bool(getattr(block, 'is_modifier', False))}


def import_item(context, row, *, payload, point=None, targets=()):
    """Public entry requires the actual prepared transaction owner, never a bare verified path."""
    return payload.finish(context, row, point=point, targets=targets)


def apply_item(context, row, *, point=None, targets=()):
    """Stock .blend effects INSIDE AssetCommit's live native Undo scope.

    This helper neither publishes Undo nor claims rollback. Exceptions propagate to the
    native before-image owner; no Python ID-removal or slot reconstruction earns unchanged.
    """
    if context.mode != 'OBJECT':
        raise NotImported('Leave the current editing or sculpt mode before importing an Essentials asset.')
    facts = inspect(row)
    kind = row['kind']
    if kind == 'node_group' and targets and not (facts['node_type'] == 'GeometryNodeTree' and facts['is_modifier']):
        raise NotImported('Only a Geometry Nodes modifier asset can be applied to objects. Omit objects to load it.')
    if kind == 'material' and not targets and row['origin'] != 'fab':
        raise NotImported('Choose at least one editable mesh for this material.')
    if any(obj.type != 'MESH' or obj.library or obj.data.library for obj in targets):
        raise NotImported('A chosen editable mesh changed before import.')
    from .scene_signature import scene_signature, scene_diff
    before_scene = scene_signature(context.scene)
    before = ids(bpy.data)
    block = _append(bpy.data, row)
    created = {p: b for p, b in ids(bpy.data).items() if p not in before}
    if block.as_pointer() not in created:
        raise RuntimeError('Blender did not create a fresh selected asset.')
    objects, modifiers, usage = [], [], 'loaded_datablock'
    if kind == 'model':
        if row['id_type'] == 'COLLECTION':
            context.collection.children.link(block)
            objects = list(block.all_objects)
        else:
            objects = [b for b in created.values() if b.id_type == 'OBJECT']
            for obj in objects:
                context.collection.objects.link(obj)
        if not objects or any(obj.as_pointer() not in created for obj in objects):
            raise RuntimeError('Blender did not create the selected model objects.')
        offset = Vector(point) if point is not None else context.scene.cursor.location.copy()
        if row['origin'] == 'fab':
            # A Fab file carries its publisher's placement and units: stand it at the point, sized like the scene.
            library_import._stand(objects, offset)
            from . import scene_fit
            fitted = scene_fit.fit(objects, context.scene, offset)
            moved = point is None and scene_fit.beside(objects, context.scene)
        else:
            for obj in objects:
                if obj.parent not in objects:
                    obj.location += offset
        usage = 'placed_model'
    elif kind == 'material' and targets:
        if library_import.assign(targets, block) != len(targets):
            raise RuntimeError('Not all chosen meshes accepted the material.')
        if any(not obj.material_slots or obj.material_slots[obj.active_material_index].material != block
               for obj in targets):
            raise RuntimeError('A chosen mesh did not retain the assigned material.')
        usage = 'applied_material'
    elif kind == 'world':
        context.scene.world = block
        if context.scene.world != block:
            raise RuntimeError('The scene did not retain the selected World.')
        usage = 'replaced_world'
    elif kind == 'node_group' and targets:
        for obj in targets:
            modifier = obj.modifiers.new(block.name, 'NODES')
            modifier.node_group = block
            if modifier.node_group != block:
                raise RuntimeError('The selected Geometry Nodes group was not retained.')
            modifiers.append((obj, modifier))
        usage = 'applied_geometry_nodes'
    else:
        block.use_fake_user = True
        if not block.use_fake_user:
            raise RuntimeError('The loaded asset was not retained in this file.')
        if kind == 'material':
            # A Fab texture set with nothing selected joins this file's asset library (Asset Browser, Current File).
            block.asset_mark()
            usage = 'material_in_library'
    if row['origin'] == 'fab' and usage != 'material_in_library':
        block.asset_clear()   # Marked only so the converted file could be appended: placed or applied, not a library item.
    from .asset_stock_import import stamp_created, readback
    stamp_created(created.values(), row, primary=block)
    context.view_layer.update()
    result = readback(row, block, objects, modifiers, usage)
    result['changes'] = scene_diff(before_scene, scene_signature(context.scene))
    if row['origin'] == 'fab' and kind == 'model':
        if fitted != 1.0:
            result['scaled_to_scene'] = round(fitted, 4)
        if moved:
            result['placed_beside'] = True
    return result
