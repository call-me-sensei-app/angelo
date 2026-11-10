# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual bpy stock-import/asset-Scope fixture. AUTHORED, NOT RUN.

Register with run_native_suites.py only after the native build gate opens. Requires
factory-startup and an isolated output directory. No providers, mocks or manual
undo_push inside the transaction. Missing required APIs are failures, never skips.
This tests the native primitive and installed production append helper, not approval/download wiring.
"""
import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
from pathlib import Path
import struct
import shutil

import bpy
import _bpy

MODES = ('OBJECT', 'COLLECTION', 'MATERIAL', 'WORLD', 'GEOMETRY', 'SHADER', 'BRUSH', 'GLB')
DOMAINS = ('objects', 'collections', 'meshes', 'materials', 'worlds', 'node_groups',
           'brushes', 'images', 'cameras', 'lights', 'libraries')
RETAINED_SCOPES = []  # A non-settled scope must not be implicitly dropped/closed.
LIBRARY_GUARD_CHECKS = []
LIBRARY_GUARD_OBSERVATIONS = []
SOURCE_LIBRARY_CLEANUP_CHECKS = []


def require(value, message):
    if not value:
        raise AssertionError(message)


def plain(value):
    if hasattr(value, 'to_dict'):
        return {key: plain(item) for key, item in value.to_dict().items()}
    if hasattr(value, 'to_list'):
        return value.to_list()
    return value


def inventory():
    return {domain: sorted(item.name for item in getattr(bpy.data, domain)) for domain in DOMAINS}


def projection():
    """Reacquire by name after Undo; never retain RNA pointers across a read."""
    obj = bpy.data.objects['DirtyMesh']
    image = bpy.data.images['DirtyPixels']
    return {
        'inventory': inventory(),
        'vertices': [list(vertex.co) for vertex in obj.data.vertices],
        'properties': {key: plain(obj[key]) for key in obj.keys()},
        'pixels': list(image.pixels[:]), 'image_size': list(image.size),
        'image_source': image.source, 'colorspace': image.colorspace_settings.name,
        'selection': sorted(item.name for item in bpy.context.selected_objects),
        'active': bpy.context.view_layer.objects.active.name if bpy.context.view_layer.objects.active else None,
        'frame': bpy.context.scene.frame_current,
        'world': bpy.context.scene.world.name if bpy.context.scene.world else None,
        'camera': list(bpy.data.objects['DirtyCamera'].location),
        'light': bpy.data.lights['DirtyLightData'].energy,
        'materials': [item.name if item else None for item in obj.data.materials],
        'active_material_index': obj.active_material_index,
        'modifiers': [(item.name, item.type, item.node_group.name if item.type == 'NODES'
                       and item.node_group else None) for item in obj.modifiers],
        'edges': {collection.name: {'objects': sorted(item.name for item in collection.objects),
                                    'children': sorted(item.name for item in collection.children)}
                  for collection in bpy.data.collections},
        'scene_objects': sorted(item.name for item in bpy.context.scene.objects),
    }


def require_old_preserved(before, after, mode):
    for key in ('vertices', 'properties', 'pixels', 'image_size', 'image_source',
                'colorspace', 'frame', 'camera', 'light'):
        require(after[key] == before[key], mode + ' preserves artist ' + key)
    if mode != 'GLB':  # The stock glTF operator selects its imported result.
        for key in ('selection', 'active'):
            require(after[key] == before[key], mode + ' preserves artist ' + key)
    if mode != 'WORLD':
        require(after['world'] == before['world'], mode + ' preserves world')
    if mode != 'MATERIAL':
        require(after['materials'] == before['materials'], mode + ' preserves material slots')
    else:
        slot = before['active_material_index']
        require(len(after['materials']) == max(1, len(before['materials'])), 'material slot count')
        require(all(after['materials'][i] == name for i, name in enumerate(before['materials'])
                    if i != slot), 'material preserves every other slot')
    if mode != 'GEOMETRY':
        require(after['modifiers'] == before['modifiers'], mode + ' preserves modifiers')
    else:
        require(after['modifiers'][:-1] == before['modifiers'], 'geometry adds only one modifier')
    old_objects = set(before['inventory']['objects'])
    old_collections = set(before['inventory']['collections'])
    for domain in DOMAINS:
        require(set(before['inventory'][domain]) <= set(after['inventory'][domain]),
                mode + ' retains every old ' + domain)
    for parent, edges in after['edges'].items():
        old = before['edges'].get(parent, {'objects': [], 'children': []})
        require(sorted(set(edges['objects']) & old_objects) == old['objects'],
                mode + ' old object links unchanged under ' + parent)
        require(sorted(set(edges['children']) & old_collections) == old['children'],
                mode + ' old collection links unchanged under ' + parent)
    require(sorted(set(after['scene_objects']) & old_objects) == before['scene_objects'],
            mode + ' old scene projection unchanged')


def make_sources(root):
    mesh = bpy.data.meshes.new('AssetMeshData')
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    obj = bpy.data.objects.new('AssetObject', mesh)
    obj.location = (2, 3, 4)
    collection = bpy.data.collections.new('AssetCollection')
    collection.objects.link(obj)
    material = bpy.data.materials.new('AssetMaterial')
    material.use_nodes = True
    world = bpy.data.worlds.new('AssetWorld')
    geometry = bpy.data.node_groups.new('AssetGeometry', 'GeometryNodeTree')
    geometry.is_modifier = True
    geometry.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    geometry.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    source = geometry.nodes.new('NodeGroupInput')
    destination = geometry.nodes.new('NodeGroupOutput')
    geometry.links.new(source.outputs['Geometry'], destination.inputs['Geometry'])
    shader = bpy.data.node_groups.new('AssetShader', 'ShaderNodeTree')
    brush = bpy.data.brushes.new('AssetBrush', mode='SCULPT')
    roots = {obj, collection, material, world, geometry, shader, brush}
    for item in roots:
        item.asset_mark()
    path = root / 'stock-assets.blend'
    bpy.data.libraries.write(str(path), roots, fake_user=True)
    # A minimal real GLB with one triangle; no external resources or extension mocks.
    binary = struct.pack('<9f3H', 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 1, 2)
    binary += b'\0' * (-len(binary) % 4)
    document = {'asset': {'version': '2.0'}, 'scene': 0, 'scenes': [{'nodes': [0]}],
                'nodes': [{'name': 'GLBTriangle', 'mesh': 0}],
                'meshes': [{'primitives': [{'attributes': {'POSITION': 0}, 'indices': 1}]}],
                'buffers': [{'byteLength': len(binary)}],
                'bufferViews': [{'buffer': 0, 'byteOffset': 0, 'byteLength': 36},
                                {'buffer': 0, 'byteOffset': 36, 'byteLength': 6}],
                'accessors': [{'bufferView': 0, 'componentType': 5126, 'count': 3,
                               'type': 'VEC3', 'min': [0, 0, 0], 'max': [1, 1, 0]},
                              {'bufferView': 1, 'componentType': 5123, 'count': 3, 'type': 'SCALAR'}]}
    encoded = json.dumps(document).encode()
    encoded += b' ' * (-len(encoded) % 4)
    glb = root / 'triangle.glb'
    glb.write_bytes(struct.pack('<III', 0x46546C67, 2, 28 + len(encoded) + len(binary))
                    + struct.pack('<II', len(encoded), 0x4E4F534A) + encoded
                    + struct.pack('<II', len(binary), 0x004E4942) + binary)
    return path, glb


def dirty_document():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.preferences.edit.use_global_undo = True
    bpy.context.preferences.edit.undo_steps = 32
    require(_bpy._angelo_undo.keep_background_document() is True, 'native background history required')
    mesh = bpy.data.meshes.new('DirtyMeshData')
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    obj = bpy.data.objects.new('DirtyMesh', mesh)
    collection = bpy.data.collections.new('Destination')
    bpy.context.scene.collection.children.link(collection)
    collection.objects.link(obj)
    camera = bpy.data.objects.new('DirtyCamera', bpy.data.cameras.new('DirtyCameraData'))
    light = bpy.data.objects.new('DirtyLight', bpy.data.lights.new('DirtyLightData', 'POINT'))
    collection.objects.link(camera)
    collection.objects.link(light)
    bpy.context.scene.camera = camera
    bpy.context.scene.world = bpy.data.worlds.new('DirtyWorld')
    bpy.context.scene.world.use_fake_user = True
    material = bpy.data.materials.new('DirtyMaterial')
    material.use_fake_user = True
    mesh.materials.append(material)
    image = bpy.data.images.new('DirtyPixels', width=2, height=2)
    image.use_fake_user = True
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    # Establish an OLDER history entry, then make every tested edit without a push.
    require('FINISHED' in bpy.ops.ed.undo_push(message='Older artist state'), 'history initialization')
    mesh.vertices[0].co = (9, 8, 7)
    obj['artist'] = {'nested': {'value': 37}, 'array': [1.5, 2.5]}
    image.pixels[:] = [0.25, 0.5, 0.75, 1.0] * 4
    image.pack()  # Persist the actual edited bytes, not generated-color defaults.
    camera.select_set(True)
    bpy.context.view_layer.objects.active = camera
    camera.location = (7, 11, 13)
    light.data.energy = 123.0
    bpy.context.scene.frame_set(23)
    bpy.context.view_layer.update()


def stock_import(mode, blend, glb, source_paths):
    if mode == 'GLB':
        require('FINISHED' in bpy.ops.import_scene.gltf(filepath=str(glb)), 'stock GLB import')
        require(any(item.name.startswith('GLBTriangle') and item.type == 'MESH'
                    for item in bpy.context.scene.objects), 'GLB has actual mesh')
        return
    domain, name = {'OBJECT': ('objects', 'AssetObject'), 'COLLECTION': ('collections', 'AssetCollection'),
                    'MATERIAL': ('materials', 'AssetMaterial'), 'WORLD': ('worlds', 'AssetWorld'),
                    'GEOMETRY': ('node_groups', 'AssetGeometry'), 'SHADER': ('node_groups', 'AssetShader'),
                    'BRUSH': ('brushes', 'AssetBrush')}[mode]
    # Exercise the installed production append/cleanup path, not a fixture copy that could
    # suppress the protected RNA removal failure. Approval/download remain outside this suite.
    from angelo_agent import blender_asset_import
    installed = Path(bpy.utils.system_resource('SCRIPTS')) / 'addons_core' / 'angelo_agent'
    require(Path(blender_asset_import.__file__).resolve().parent == installed.resolve(),
            'actual installed production asset importer required')
    old_libraries = {item.session_uid for item in bpy.data.libraries}
    row = {'id_type': 'NODETREE' if mode in {'GEOMETRY', 'SHADER'} else mode,
           'name': name, 'origin': 'bundled', 'file': {'path': str(blend)},
           'files': ({'path': str(blend)},),
           '_source_paths': tuple(pair for pair in source_paths
                                  if Path(pair[1]).resolve() == blend.resolve())}
    primary = blender_asset_import._append(bpy.data, row)
    require(primary is not None, 'selected production append returned actual ID')
    if mode == 'BRUSH':
        require(primary.paint_curve is None and primary.texture is None
                and primary.mask_texture is None,
                'minimal Brush exercises real null USER-role slots across Undo/Redo')
    require(not any(library.session_uid not in old_libraries
                    and not any(item.library == library for item in bpy.data.all_ids)
                    for library in bpy.data.libraries),
            'production cleanup retires new unused source Libraries before publication')
    SOURCE_LIBRARY_CLEANUP_CHECKS.append(mode)
    if mode == 'OBJECT':
        bpy.data.collections['Destination'].objects.link(primary)
        primary.location += __import__('mathutils').Vector((5, 7, 11))
        require(tuple(primary.location) == (7, 10, 15), 'authored transform plus point')
    elif mode == 'COLLECTION':
        bpy.data.collections['Destination'].children.link(primary)
        for obj in primary.all_objects:
            if obj.parent is None:
                obj.location += __import__('mathutils').Vector((5, 7, 11))
    elif mode == 'MATERIAL':
        target = bpy.data.objects['DirtyMesh']
        if target.material_slots:
            target.material_slots[target.active_material_index].material = primary
        else:
            target.data.materials.append(primary)
    elif mode == 'WORLD':
        bpy.context.scene.world = primary
    elif mode == 'GEOMETRY':
        bpy.data.objects['DirtyMesh'].modifiers.new('Imported Geometry', 'NODES').node_group = primary
    else:
        # Shader and Brush load only; the product explicitly retains them with a fake user.
        primary.use_fake_user = True
        require(primary.use_fake_user, 'load-only asset retained')
    bpy.context.view_layer.update()


def save_projection():
    """Compare stock-persisted IDs; unused replaced assets may legitimately be omitted."""
    result = projection()
    for domain in DOMAINS:
        result['inventory'][domain] = sorted(item.name for item in getattr(bpy.data, domain)
                                             if item.users > 0 or item.use_fake_user)
    result['inventory']['libraries'] = sorted(library.name for library in bpy.data.libraries
        if any(item.library == library for item in bpy.data.all_ids))
    return result


def finish(scope, keep):
    api = _bpy._angelo_undo
    result = api.asset_finish(scope, keep=keep)
    require(type(result) is dict and type(result.get('schema')) is int
            and result['schema'] == 1, 'schema1 native receipt')
    require(result == api.asset_status(scope), 'status reads stored result without replay')
    expected = 'COMMITTED' if keep else 'RESTORED'
    require(result['state'] == expected, 'native terminal state: ' + repr(result))
    require(result['published'] is keep and result['restored'] is (not keep)
            and result['recovery_required'] is False and result['closed'] is False,
            'exact terminal flags')
    closed = api.asset_close(scope)
    require(type(closed) is dict and closed.get('closed') is True
            and closed.get('state') == expected and closed.get('recovery_required') is False
            and closed.get('published') is keep and closed.get('restored') is (not keep),
            'actual native close witness')
    require(closed == api.asset_status(scope), 'closed status remains observable')
    require(api.asset_pending() is None, 'no native pending owner after actual close')
    RETAINED_SCOPES.remove(scope)
    return closed


class FailureAfterCreation(RuntimeError):
    pass


def transact(mode, blend, glb, keep, source_paths):
    try:
        scope = _bpy._angelo_undo.asset_begin(source_paths=source_paths)
    except BaseException:
        pending = _bpy._angelo_undo.asset_pending()
        if pending is not None:
            RETAINED_SCOPES.append(pending)
        raise
    require(scope is not None, 'actual native scope returned')
    RETAINED_SCOPES.append(scope)
    try:
        stock_import(mode, blend, glb, source_paths)
        if not keep:
            raise FailureAfterCreation('fixture caller fails after actual import and target writes')
    except FailureAfterCreation:
        return finish(scope, False)
    except BaseException:
        # A failed restore remains retained and aborts this fixture; never replay import.
        finish(scope, False)
        raise
    return finish(scope, keep)


def library_removal_guards(blend, old_source, source_paths):
    """Actual denied RNA operations inside a real capture, followed by checked restore."""
    dirty_document()
    with bpy.data.libraries.load(str(old_source), link=True) as (available, requested):
        require('AssetMaterial' in available.materials, 'baseline external Library source')
        requested.materials = ['AssetMaterial']
    old_material = requested.materials[0]
    require(old_material is not None and old_material.library is not None,
            'baseline Library has actual linked data')
    bpy.data.objects['DirtyMesh'].data.materials.append(old_material)
    bpy.context.view_layer.update()
    old_library_uid = old_material.library.session_uid
    old_unused = bpy.data.meshes.new('ArtistUnusedMesh')
    old_unused_uid = old_unused.session_uid
    require(old_unused.users == 0, 'baseline unused artist data')
    baseline = projection()
    api = _bpy._angelo_undo
    try:
        scope = api.asset_begin(source_paths=source_paths)
    except BaseException:
        pending = api.asset_pending()
        if pending is not None:
            RETAINED_SCOPES.append(pending)
        raise
    RETAINED_SCOPES.append(scope)

    def expect_refused(collection, item, label, **options):
        uid = item.session_uid
        observation = {'case': label, 'users': item.users,
                       'fake_user': item.use_fake_user}
        if isinstance(item, bpy.types.Library):
            observation['id_library_references'] = sum(
                candidate.library == item for candidate in bpy.data.all_ids)
        try:
            collection.remove(item, **options)
        except RuntimeError as error:
            require('Sculpt recovery or native commit' in str(error),
                    label + ' refused by actual protected-owner guard')
        else:
            raise AssertionError(label + ' unexpectedly removed a protected ID')
        require(any(candidate.session_uid == uid for candidate in collection),
                label + ' retains the actual native ID')
        require(api.asset_status(scope)['state'] == 'ACTIVE' and api.asset_pending() is scope,
                label + ' does not abandon or replace the active capture')
        LIBRARY_GUARD_CHECKS.append(label)
        LIBRARY_GUARD_OBSERVATIONS.append(observation)

    try:
        old_library = next(item for item in bpy.data.libraries
                           if item.session_uid == old_library_uid)
        expect_refused(bpy.data.libraries, old_library, 'old-external-library')
        expect_refused(bpy.data.objects, bpy.data.objects['DirtyMesh'], 'other-protected-id')
        before_libraries = {item.session_uid for item in bpy.data.libraries}
        with bpy.data.libraries.load(str(blend), link=True, assets_only=True) as (available, requested):
            require('AssetMaterial' in available.materials, 'new linked Library source')
            requested.materials = ['AssetMaterial']
        linked = requested.materials[0]
        require(linked is not None and linked.library is not None
                and linked.library.session_uid not in before_libraries,
                'actual introduced Library with a real ID.library reference')
        expect_refused(bpy.data.libraries, linked.library, 'new-used-library')
        old_unused = next(item for item in bpy.data.meshes if item.session_uid == old_unused_uid)
        expect_refused(bpy.data.meshes, old_unused, 'old-unused-mesh')
        transient_mesh = bpy.data.meshes.new('ImportScratchMesh')
        transient = bpy.data.objects.new('ImportScratchObject', transient_mesh)
        require(transient.users == 0 and transient_mesh.users == 1,
                'new unlinked Object has only an outgoing Mesh user')
        for option in ('do_unlink', 'do_id_user', 'do_ui_user'):
            expect_refused(bpy.data.objects, transient, 'new-orphan-nondefault-' + option,
                           **{option: False})
        transient.use_fake_user = True
        expect_refused(bpy.data.objects, transient, 'new-fake-user-object')
        transient.use_fake_user = False
        bpy.context.collection.objects.link(transient)
        expect_refused(bpy.data.objects, transient, 'new-used-object')
        bpy.context.collection.objects.unlink(transient)
        # parent is a non-user pointer: the full native incoming-link scan must
        # reject it even though the target's total user count is still zero.
        incoming = bpy.data.objects.new('ImportScratchIncoming', None)
        incoming.parent = transient
        require(transient.users == 0, 'incoming non-user pointer control')
        expect_refused(bpy.data.objects, transient, 'new-incoming-nonuser-object')
        incoming.parent = None
        other_scene = bpy.data.scenes.new('ImportOtherScene')
        expect_refused(bpy.data.scenes, other_scene, 'new-document-scene')
        with bpy.context.temp_override(scene=other_scene,
                                       view_layer=other_scene.view_layers[0]):
            expect_refused(bpy.data.objects, transient, 'new-orphan-other-context')
        mesh_uid = transient_mesh.session_uid
        bpy.data.objects.remove(transient)
        require(any(item.session_uid == mesh_uid for item in bpy.data.meshes)
                and transient_mesh.users == 0,
                'ordinary Object cleanup preserves Mesh and decrements outgoing user once')
        bpy.data.objects.remove(incoming)
        bpy.data.meshes.remove(transient_mesh)
        material = bpy.data.materials.new('ImportScratchMaterial')
        image = bpy.data.images.new('ImportScratchImage', width=2, height=2)
        require(material.users == 0 and image.users == 0, 'held-out ordinary orphan types')
        require(material.node_tree is not None and material.node_tree.is_embedded_data,
                'Material positive includes its actual owned embedded NodeTree backlink')
        bpy.data.materials.remove(material)
        world = bpy.data.worlds.new('ImportScratchWorld')
        world.use_nodes = True
        require(world.users == 0 and world.node_tree is not None
                and world.node_tree.is_embedded_data,
                'held-out World positive includes the same native owned-backpointer role')
        bpy.data.worlds.remove(world)
        bpy.data.images.remove(image)

    except BaseException:
        finish(scope, False)
        raise
    finish(scope, False)
    require(projection() == baseline, 'guard controls restore without deleting baseline data')
    require(any(item.session_uid == old_unused_uid for item in bpy.data.meshes),
            'rollback retains the exact baseline unused Mesh')
    require('ImportOtherScene' not in bpy.data.scenes, 'rollback removes new document control')


def main():
    require('--factory-startup' in sys.argv and bpy.app.background, 'isolated factory background required')
    root = Path(os.environ['ANGELO_QA_DIR'])
    root.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    original_blend, original_glb = make_sources(root)
    private = root / 'private-source'
    private.mkdir()
    blend, glb = private / original_blend.name, private / original_glb.name
    shutil.copyfile(original_blend, blend)
    shutil.copyfile(original_glb, glb)
    source_paths = ((str(original_blend.resolve()), str(blend.resolve())),
                    (str(original_glb.resolve()), str(glb.resolve())))
    old_source = private / 'old-external-library.blend'
    shutil.copyfile(original_blend, old_source)
    api = _bpy._angelo_undo
    caps = api.asset_transaction_capabilities()
    require(type(caps) is dict and type(caps.get('schema')) is int and caps['schema'] == 1
            and all(caps.get(key) is True for key in ('native_undo', 'dirty_preimage',
                'checked_restore', 'nested_undo_suppressed')), 'focused native capabilities')
    checks = []
    for mode in MODES:
        dirty_document()
        before = projection()
        transact(mode, blend, glb, True, source_paths)
        after = projection()
        require(after != before, mode + ' actually changed document')
        require_old_preserved(before, after, mode)
        require('FINISHED' in bpy.ops.ed.undo(), mode + ' one Undo dispatch')
        require(projection() == before, mode + ' one Undo preserves dirty prehistory exactly')
        require('FINISHED' in bpy.ops.ed.redo(), mode + ' one Redo dispatch')
        require(projection() == after, mode + ' one Redo restores exact result')
        # A second append must have its own baseline, not reuse the first snapshot.
        transact(mode, blend, glb, True, source_paths)
        twice = projection()
        require_old_preserved(after, twice, mode)
        require(twice != after, mode + ' second import has actual separate result')
        require('FINISHED' in bpy.ops.ed.undo() and projection() == after, mode + ' sequential Undo')
        require('FINISHED' in bpy.ops.ed.redo() and projection() == twice, mode + ' sequential Redo')
        expected_saved = save_projection()
        saved = root / (mode.lower() + '-roundtrip.blend')
        require('FINISHED' in bpy.ops.wm.save_as_mainfile(filepath=str(saved)), 'save result')
        require('FINISHED' in bpy.ops.wm.open_mainfile(filepath=str(saved), load_ui=False,
                                                    use_scripts=False), 'reopen result')
        require(save_projection() == expected_saved, mode + ' save/reopen content')
        dirty_document()
        before = projection()
        transact(mode, blend, glb, False, source_paths)  # Actual created/linked IDs, then requested rollback.
        require(projection() == before, mode + ' failure after creation restores with no residue')
        checks.append(mode)
    library_removal_guards(blend, old_source, source_paths)
    require(LIBRARY_GUARD_CHECKS == ['old-external-library', 'other-protected-id',
                                     'new-used-library', 'old-unused-mesh',
                                     'new-orphan-nondefault-do_unlink',
                                     'new-orphan-nondefault-do_id_user',
                                     'new-orphan-nondefault-do_ui_user',
                                     'new-fake-user-object', 'new-used-object',
                                     'new-incoming-nonuser-object', 'new-document-scene',
                                     'new-orphan-other-context'], 'all actual guard controls executed')
    require(SOURCE_LIBRARY_CLEANUP_CHECKS == [mode for mode in MODES if mode != 'GLB'
                                              for _ in range(3)],
            'every blend import checked actual production source-Library cleanup')
    require(not RETAINED_SCOPES, 'all scopes actually settled')
    (root / 'stock-asset-undo.json').write_text(json.dumps({'success': True, 'modes': checks,
        'library_guard_checks': LIBRARY_GUARD_CHECKS,
        'library_guard_observations': LIBRARY_GUARD_OBSERVATIONS,
        'source_library_cleanup_checks': SOURCE_LIBRARY_CLEANUP_CHECKS,
        'phase': 'complete', 'binary': bpy.app.binary_path, 'version': bpy.app.version_string}, indent=2))
    print('ANGELO_STOCK_ASSET_UNDO_OK', flush=True)


if __name__ == '__main__':
    main()
