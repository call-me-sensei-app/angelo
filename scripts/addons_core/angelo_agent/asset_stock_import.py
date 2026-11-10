# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Stock importer effects/read-back within the native asset Undo scope; no admission engine."""
import bpy
from mathutils import Vector

from . import blender_asset_import as blend, library_import, team_library_import as base
from .team_library_files import NotImported, Refused


def _sync_created_materials(created):
    """Sync only this import's new materials, inside its existing native Undo scope."""
    from . import material_display_sync as display
    read = {}
    for block in created:
        if block.id_type == 'MATERIAL':
            display.sync(block, display.ADDED, images=display.LOAD, read=read)


def stamp_created(created, row, *, primary=None):
    """Only actual new IDs from this invocation; unrelated custom properties remain intact."""
    created = tuple(created)
    # Never recursively free or rewrite an existing source subtree merely to stamp it.
    # Inspect EVERY new ID first; a collision must precede ALL provenance writes.
    if any(key in block for block in created
           for key in ('angelo_blender_asset', library_import.PROPERTY)):
        raise Refused('This asset already uses reserved Angelo provenance fields. Its metadata '
                      'was left intact. Choose another asset, or remove those fields in a '
                      'separately saved source copy before importing it.')
    common = {key: row[key] for key in ('id', 'name', 'origin', 'license', 'author')}
    legacy = None
    if row['origin'] == 'fab':
        # The selected catalog already validates the canonical identifier/license.
        identity = row['id'].removeprefix('fab:')
        from uuid import UUID
        if str(UUID(identity)) != identity or row['id'] != 'fab:' + identity:
            raise NotImported('The selected Fab identifier is invalid.')
        legacy = library_import.provenance(dict(item=row['id'], id=row['id'], title=row['name'],
            source='fab', license=row['license'], attribution=row['author'],
            attribution_required=row['license'] == 'CC-BY-4.0',
            source_url='https://www.fab.com/listings/' + identity))
    _sync_created_materials(created)
    for block in created:
        if (block is not primary and
                block.id_type not in {'OBJECT', 'MESH', 'MATERIAL', 'IMAGE', 'COLLECTION', 'WORLD'}):
            continue
        block['angelo_blender_asset'] = common
        if legacy is not None:
            block[library_import.PROPERTY] = legacy
        if legacy is not None and block[library_import.PROPERTY].to_dict() != legacy:
            raise RuntimeError('The imported Fab credits were not retained.')
        if block['angelo_blender_asset'].to_dict() != common:
            raise RuntimeError('The imported asset provenance was not retained.')


def readback(row, block, objects, modifiers, usage):
    """Live stock read-back, not a native source/allocator or publication certificate."""
    return dict(schema=1, item_id=row['id'], source='fab' if row['origin'] == 'fab' else 'blender',
        title=row['name'], kind=row['kind'], id_type=block.id_type,
        datablock=block.name, usage=usage, license=row['license'], author=row['author'],
        objects=tuple(objects[:base.MAX_LISTED]), object_count=len(objects),
        modifiers=tuple(modifiers), loaded_only=usage == 'loaded_datablock')


def apply_glb(context, row, *, point=None, targets=()):
    if context.mode != 'OBJECT' or targets:
        raise NotImported('A model import requires Object Mode and no material targets.')
    from .gltf_checks import check_file
    check_file(row['file']['path'], self_contained=True)
    from .scene_signature import scene_signature, scene_diff
    before_scene = scene_signature(context.scene)
    before = blend.ids(bpy.data)
    scene_state = base._scene_state(context.scene)
    previous = context.view_layer.active_layer_collection
    collection = bpy.data.collections.new('Library · ' + row['name'][:50])
    previous.collection.children.link(collection)
    context.view_layer.active_layer_collection = next(child for child in previous.children
                                                     if child.collection == collection)
    try:
        from .gltf_morph import import_options
        result = bpy.ops.import_scene.gltf(filepath=row['file']['path'], import_pack_images=True,
            import_scene_extras=False, **import_options(row['file']['path']))
        if result != {'FINISHED'}:
            raise RuntimeError('Blender did not finish importing the selected GLB.')
        created = {p: block for p, block in blend.ids(bpy.data).items() if p not in before}
        objects = list(collection.all_objects)
        if not objects or any(obj.as_pointer() not in created for obj in objects):
            raise RuntimeError('Blender did not create the selected model objects.')
        # Validate actual output rather than accepting FINISHED alone. All images must be
        # packed; a malformed/unpacked dependency fails the WHOLE native transaction.
        for block in created.values():
            if block.id_type == 'IMAGE':
                blend._inspect_packed_image(block)
            animation = getattr(block, 'animation_data', None)
            if block.id_type == 'TEXT' or (animation and animation.drivers):
                raise RuntimeError('The imported model contains executable data.')
        base._restore_scene(context.scene, scene_state)
        cursor = context.scene.cursor.location.copy()
        stand = Vector(point) if point is not None else cursor
        library_import._stand(objects, stand)
        from . import scene_fit   # Fab files carry their publisher's units: sized like the scene, beside its objects.
        scaled = scene_fit.fit(objects, context.scene, stand)
        moved = (stand - cursor).length < 1e-6 and scene_fit.beside(objects, context.scene)
        stamp_created(created.values(), row)
        context.view_layer.update()
        result = readback(row, collection, objects, (), 'placed_model')
        if scaled != 1.0:
            result['scaled_to_scene'] = round(scaled, 4)
        if moved:
            result['placed_beside'] = True
        result['changes'] = scene_diff(before_scene, scene_signature(context.scene))
        return result
    finally:
        # This runs before AssetCommit requests any native restore, so no stale RNA is used
        # after a whole-Main restore invalidates the previous context/ID references.
        context.view_layer.active_layer_collection = previous
