# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Real Object Mode/load-only Brush dependency and history fixture.

Run through run_native_suites.py with the matching native build, serially.
No providers, paint activation, mocks, native fault hooks, or pushes inside a scope.
"""
import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
from pathlib import Path
import shutil

import bpy
import _bpy

PREFIX = 'BrushDependencyQA_'
OLD_BRUSHES = (PREFIX + 'ArtistA', PREFIX + 'ArtistB')
OLD_IMAGE = PREFIX + 'ArtistImage'
OLD_TEXTURE = PREFIX + 'ArtistTexture'
OLD_MATERIAL = PREFIX + 'ArtistMaterial'
NEW_BRUSH = PREFIX + 'ImportedBrush'
NEW_IMAGE = PREFIX + 'ImportedImage'
NEW_TEXTURE = PREFIX + 'ImportedTexture'
NEW_MATERIAL = PREFIX + 'ImportedMaterial'
DOMAINS = ('brushes', 'images', 'textures', 'materials', 'objects', 'meshes',
           'collections', 'node_groups', 'libraries')
RETAINED_SCOPES = []


def require(value, message):
    if not value:
        raise AssertionError(message)


def plain(value):
    if isinstance(value, bpy.types.ID):
        return {'type': value.bl_rna.identifier, 'name': value.name_full}
    if hasattr(value, 'to_dict'):
        return {key: plain(item) for key, item in value.to_dict().items()}
    if hasattr(value, 'to_list'):
        return value.to_list()
    return value


def properties(item):
    return {key: plain(item[key]) for key in sorted(item.keys())}


def header(item, identity):
    result = {'name': item.name_full, 'fake_user': item.use_fake_user,
              'users': item.users, 'properties': properties(item)}
    if identity:
        result['uid'] = item.session_uid
    return result


def image_projection(image, identity):
    result = header(image, identity)
    require(image.packed_file is not None, image.name + ' has actual packed bytes')
    result.update(size=list(image.size), pixels=list(image.pixels[:]),
                  packed_bytes=list(image.packed_file.data),
                  colorspace=image.colorspace_settings.name, source=image.source)
    return result


def texture_projection(texture, identity):
    result = header(texture, identity)
    result.update(type=texture.type, image=plain(texture.image), filter_size=texture.filter_size)
    return result


def material_projection(material, identity):
    result = header(material, identity)
    result.update(diffuse=list(material.diffuse_color),
                  images=sorted((node.name, node.image.name_full if node.image else None)
                                for node in material.node_tree.nodes if node.type == 'TEX_IMAGE'))
    return result


def brush_projection(brush, identity):
    result = header(brush, identity)
    result.update(size=brush.size, strength=brush.strength, color=list(brush.color),
                  texture=plain(brush.texture), mask_texture=plain(brush.mask_texture))
    weak = brush.library_weak_reference
    result['weak_reference'] = None if weak is None else [
        str(Path(bpy.path.abspath(weak.filepath)).resolve()), weak.id_name]
    metadata = brush.asset_data
    result['asset'] = None if metadata is None else {
        'author': metadata.author, 'description': metadata.description,
        'license': metadata.license, 'copyright': metadata.copyright,
        'tags': sorted(tag.name for tag in metadata.tags), 'properties': properties(metadata)}
    return result


def inventory():
    return {domain: sorted(item.name_full for item in getattr(bpy.data, domain))
            for domain in DOMAINS}


def old_projection(identity=True):
    mesh = bpy.data.meshes[PREFIX + 'ArtistMesh']
    return {
        'brushes': [brush_projection(bpy.data.brushes[name], identity) for name in OLD_BRUSHES],
        'image': image_projection(bpy.data.images[OLD_IMAGE], identity),
        'texture': texture_projection(bpy.data.textures[OLD_TEXTURE], identity),
        'material': material_projection(bpy.data.materials[OLD_MATERIAL], identity),
        'mesh': {'vertices': [list(vertex.co) for vertex in mesh.vertices],
                 'properties': properties(mesh),
                 'materials': [plain(item) for item in mesh.materials]},
        'object_properties': properties(bpy.data.objects[PREFIX + 'ArtistObject']),
        'mode': bpy.context.mode,
        'active': bpy.context.view_layer.objects.active.name_full,
        'selected': sorted(item.name_full for item in bpy.context.selected_objects),
    }


def new_projection(identity=True):
    # The adjunct deliberately restores each new template's recorded session UID before
    # insertion and verifies it after decode. Ordinary memfile dependencies retain their
    # recorded UID too. This assertion applies to Undo/Redo, never a normal file reopen.
    return {
        'brush': brush_projection(bpy.data.brushes[NEW_BRUSH], identity),
        'image': image_projection(bpy.data.images[NEW_IMAGE], identity),
        'texture': texture_projection(bpy.data.textures[NEW_TEXTURE], identity),
        'material': material_projection(bpy.data.materials[NEW_MATERIAL], identity),
    }


def without_users(value):
    if isinstance(value, dict):
        return {key: without_users(item) for key, item in value.items() if key != 'users'}
    if isinstance(value, list):
        return [without_users(item) for item in value]
    return value


def assert_sharing(imported=False):
    image = bpy.data.images[OLD_IMAGE]
    texture = bpy.data.textures[OLD_TEXTURE]
    material = bpy.data.materials[OLD_MATERIAL]
    require(texture.image == image, 'old texture still references actual old image')
    require(material.node_tree.nodes[PREFIX + 'ImageNode'].image == image,
            'old material still references actual shared old image')
    for name in OLD_BRUSHES:
        brush = bpy.data.brushes[name]
        require(brush.texture == texture and brush.mask_texture == texture,
                'both old Brushes retain actual shared texture')
        require(brush['artist_material'] == material and brush['artist_image'] == image,
                'old Brush native ID properties retain shared dependencies')
    if imported:
        brush = bpy.data.brushes[NEW_BRUSH]
        require(brush.texture == texture and brush['shared_material'] == material
                and brush['shared_image'] == image, 'new Brush references existing shared IDs')
        require(brush.mask_texture == bpy.data.textures[NEW_TEXTURE]
                and brush.mask_texture.image == bpy.data.images[NEW_IMAGE],
                'new Brush also retains genuinely new texture/image')
        require(brush['asset_material'] == bpy.data.materials[NEW_MATERIAL],
                'new Brush retains its actual appended material')


def make_source(root):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    image = bpy.data.images.new(NEW_IMAGE, width=2, height=2)
    image.pixels[:] = [0.0, 1.0, 0.0, 1.0] * 4
    image.pack()
    texture = bpy.data.textures.new(NEW_TEXTURE, type='IMAGE')
    texture.image = image
    material = bpy.data.materials.new(NEW_MATERIAL)
    material.use_nodes = True
    node = material.node_tree.nodes.new('ShaderNodeTexImage')
    node.name = PREFIX + 'SourceImageNode'
    node.image = image
    brush = bpy.data.brushes.new(NEW_BRUSH, mode='SCULPT')
    brush.texture = texture
    brush.mask_texture = texture
    brush['asset_material'] = material
    brush['payload'] = {'nested': {'value': 73}, 'array': [1.25, 2.5]}
    brush.strength = 0.625
    brush.asset_mark()
    brush.asset_data.author = 'Brush dependency fixture'
    brush.asset_data.description = 'Metadata must survive real native Undo and Redo'
    brush.asset_data.license = 'Fixture-only'
    brush.asset_data.copyright = 'Angelo Contributors'
    brush.asset_data.tags.new('dependency-roundtrip')
    brush.asset_data['fixture_revision'] = 19
    original = root / 'brush-dependencies.blend'
    bpy.data.libraries.write(str(original), {brush}, fake_user=True)
    private_dir = root / 'private-source'
    private_dir.mkdir()
    private = private_dir / original.name
    shutil.copyfile(original, private)
    return private, ((str(original.resolve()), str(private.resolve())),)


def dirty_artist():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.preferences.edit.use_global_undo = True
    bpy.context.preferences.edit.undo_steps = 32
    require(_bpy._angelo_undo.keep_background_document() is True, 'native background history')
    image = bpy.data.images.new(OLD_IMAGE, width=2, height=2)
    image.use_fake_user = True
    texture = bpy.data.textures.new(OLD_TEXTURE, type='IMAGE')
    texture.image = image
    texture.use_fake_user = True
    material = bpy.data.materials.new(OLD_MATERIAL)
    material.use_fake_user = True
    material.use_nodes = True
    node = material.node_tree.nodes.new('ShaderNodeTexImage')
    node.name = PREFIX + 'ImageNode'
    node.image = image
    for name in OLD_BRUSHES:
        brush = bpy.data.brushes.new(name, mode='SCULPT')
        brush.use_fake_user = True
        brush.texture = texture
        brush.mask_texture = texture
        brush['artist_material'] = material
        brush['artist_image'] = image
    mesh = bpy.data.meshes.new(PREFIX + 'ArtistMesh')
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    mesh.materials.append(material)
    obj = bpy.data.objects.new(PREFIX + 'ArtistObject', mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    recorded_filter_size = texture.filter_size
    require('FINISHED' in bpy.ops.ed.undo_push(message='Older shared Brush state'), 'older history')
    # Every edit below is intentionally absent from the active history snapshot.
    for index, name in enumerate(OLD_BRUSHES):
        brush = bpy.data.brushes[name]
        brush.strength = 0.4375 + index * 0.125
        brush.size = 83 + index
        brush.color = (0.125, 0.5, 0.875)
        brush['dirty_artist'] = {'nested': {'value': 37 + index}, 'array': [1.5, 2.5]}
    image.pixels[:] = [1.0, 0.0, 1.0, 1.0] * 4
    image.pack()
    texture.filter_size = 0.3125
    require(texture.filter_size == 0.3125 and texture.filter_size != recorded_filter_size,
            'shared ImageTexture has an actual unrecorded writable-field edit')
    material.diffuse_color = (0.25, 0.5, 0.75, 1.0)
    material['dirty_artist'] = 'shared material changed after the older push'
    mesh.vertices[0].co = (9, 8, 7)
    mesh['dirty_artist'] = {'value': 29}
    obj['dirty_artist'] = [11, 13, 17]
    bpy.context.view_layer.update()
    require(bpy.context.mode == 'OBJECT', 'fixture stays in actual Object Mode')
    assert_sharing()


def append_brush(path):
    old_libraries = {library.as_pointer() for library in bpy.data.libraries}
    with bpy.data.libraries.load(str(path), link=False, assets_only=True, recursive=True) as (source, dest):
        require(NEW_BRUSH in source.brushes, 'real serialized Brush source exists')
        dest.brushes = [NEW_BRUSH]
    brush = dest.brushes[0]
    require(brush is not None and brush.name == NEW_BRUSH, 'actual selected Brush appended')
    # This is supported ID assignment, not activation or a fixture-only native hook.
    # Its untouched mask texture keeps a separate newly appended dependency closure alive.
    brush.texture = bpy.data.textures[OLD_TEXTURE]
    brush['shared_material'] = bpy.data.materials[OLD_MATERIAL]
    brush['shared_image'] = bpy.data.images[OLD_IMAGE]
    brush.use_fake_user = True  # Match the current product's load-only contract.
    for library in tuple(bpy.data.libraries):
        if (library.as_pointer() not in old_libraries
                and Path(library.filepath).resolve() == path.resolve()
                and not any(item.library == library for item in bpy.data.all_ids)):
            bpy.data.libraries.remove(library)
    bpy.context.view_layer.update()
    assert_sharing(imported=True)


def transact(path, source_paths, keep):
    api = _bpy._angelo_undo
    try:
        scope = api.asset_begin(source_paths=source_paths)
    except BaseException:
        pending = api.asset_pending()
        if pending is not None:
            RETAINED_SCOPES.append(pending)
        raise
    require(scope is not None, 'native scope returned')
    RETAINED_SCOPES.append(scope)
    try:
        append_brush(path)
    except BaseException:
        # Record actual restore facts, then preserve the original append/assertion failure.
        settle(scope, False)
        raise
    return settle(scope, keep)


def settle(scope, keep):
    api = _bpy._angelo_undo
    result = api.asset_finish(scope, keep=keep)
    expected = 'COMMITTED' if keep else 'RESTORED'
    require(type(result) is dict and type(result.get('schema')) is int and result['schema'] == 1,
            'native schema1 terminal receipt')
    require(result == api.asset_status(scope), 'stored status; no terminal replay')
    require(result.get('state') == expected and result.get('published') is keep
            and result.get('restored') is (not keep) and result.get('recovery_required') is False
            and result.get('closed') is False, 'actual native terminal state: ' + repr(result))
    closed = api.asset_close(scope)
    require(type(closed) is dict and closed.get('state') == expected
            and closed.get('closed') is True and closed.get('recovery_required') is False
            and closed.get('published') is keep and closed.get('restored') is (not keep),
            'actual settled native close')
    require(closed == api.asset_status(scope) and api.asset_pending() is None,
            'closed stored receipt and no pending native owner')
    RETAINED_SCOPES.remove(scope)
    return closed


def saved_roundtrip(root, label, expected_old, expected_inventory, expected_new=None):
    path = root / (label + '.blend')
    require('FINISHED' in bpy.ops.wm.save_as_mainfile(filepath=str(path)), 'save ' + label)
    require('FINISHED' in bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False,
                                                use_scripts=False), 'reopen ' + label)
    require(old_projection(identity=False) == expected_old, label + ' persisted old/shared content')
    require(inventory() == expected_inventory, label + ' persisted exact required inventories')
    assert_sharing(imported=expected_new is not None)
    if expected_new is not None:
        require(new_projection(identity=False) == expected_new,
                label + ' persisted new dependencies, metadata and fake-user retention')


def main():
    require('--factory-startup' in sys.argv and bpy.app.background, 'isolated factory background')
    root = Path(os.environ['ANGELO_QA_DIR'])
    root.mkdir(parents=True, exist_ok=True)
    path, source_paths = make_source(root)
    api = _bpy._angelo_undo
    caps = api.asset_transaction_capabilities()
    require(type(caps) is dict and type(caps.get('schema')) is int and caps['schema'] == 1
            and all(caps.get(key) is True for key in ('native_undo', 'dirty_preimage',
                'checked_restore', 'nested_undo_suppressed')), 'focused native capabilities required')
    checks = []

    dirty_artist()
    before, before_inventory = old_projection(), inventory()
    transact(path, source_paths, True)
    after, imported = old_projection(), new_projection()
    require(without_users(after) == without_users(before), 'commit preserves dirty old/shared data')
    require(after['texture']['users'] == before['texture']['users'] + 1,
            'new Brush adds exactly one real user of the old shared texture')
    require(after['material']['users'] == before['material']['users'] + 1
            and after['image']['users'] == before['image']['users'] + 1,
            'new Brush adds actual shared material/image ID users')
    after_inventory = inventory()
    require(after_inventory != before_inventory, 'commit actually adds new IDs')
    require('FINISHED' in bpy.ops.ed.undo(), 'one actual Brush Undo')
    require(old_projection() == before and inventory() == before_inventory,
            'Undo removes only imported closure and preserves dirty old/shared state')
    assert_sharing()
    require('FINISHED' in bpy.ops.ed.redo(), 'one actual Brush Redo')
    require(old_projection() == after and new_projection() == imported
            and inventory() == after_inventory, 'Redo restores UID/content/reference facts')
    assert_sharing(imported=True)
    saved_roundtrip(root, 'committed-brush', old_projection(False), after_inventory,
                    new_projection(False))
    checks.append('shared_dirty_commit_undo_redo_save_reopen')

    dirty_artist()
    before, before_inventory = old_projection(), inventory()
    transact(path, source_paths, True)
    require('FINISHED' in bpy.ops.ed.undo(), 'Undo before actual history branch')
    require(old_projection() == before and inventory() == before_inventory, 'branch starts at Before')
    # A real new history branch retires the native After step. Further bounded pushes prune
    # the original Before step too; no private refcount or fabricated destructor receipt.
    bpy.context.preferences.edit.undo_steps = 2
    for index in range(6):
        bpy.context.scene[PREFIX + 'branch'] = index
        require('FINISHED' in bpy.ops.ed.undo_push(message='Shared Brush branch ' + str(index)),
                'actual branch/pruning push')
    require(not bpy.ops.ed.redo.poll(), 'retired import branch is not redoable')
    require(old_projection() == before and inventory() == before_inventory,
            'branch retirement cannot free old Brushes/shared dependencies or resurrect import')
    require('FINISHED' in bpy.ops.ed.undo() and 'FINISHED' in bpy.ops.ed.redo(),
            'remaining ordinary branch Undo/Redo remains functional')
    require(old_projection() == before and inventory() == before_inventory,
            'ordinary branch Redo does not replay the retired Brush import')
    saved_roundtrip(root, 'retired-brush-branch', old_projection(False), before_inventory)
    checks.append('history_branch_retirement_pruning_save_reopen')

    dirty_artist()
    before, before_inventory = old_projection(), inventory()
    transact(path, source_paths, False)  # Actual append/ref writes occurred; caller requests rollback.
    require(old_projection() == before and inventory() == before_inventory,
            'checked rollback has no new Brush/dependency residue or old shared user-count drift')
    assert_sharing()
    saved_roundtrip(root, 'rolled-back-brush', old_projection(False), before_inventory)
    checks.append('actual_append_then_checked_rollback_save_reopen')

    require(not RETAINED_SCOPES and api.asset_pending() is None, 'all actual scopes settled')
    (root / 'stock-asset-brush-dependencies.json').write_text(json.dumps({
        'success': True, 'phase': 'complete', 'cases': checks, 'mode': 'OBJECT_LOAD_ONLY',
        'binary': bpy.app.binary_path, 'version': bpy.app.version_string}, indent=2))
    print('ANGELO_STOCK_ASSET_BRUSH_DEPENDENCIES_OK', flush=True)


if __name__ == '__main__':
    main()
