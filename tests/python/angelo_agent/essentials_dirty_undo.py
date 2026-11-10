# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual-bpy seven-effect diagnostics using explicitly SYNTHETIC .blend assets.

This is not official Essentials catalog/acquisition proof. The caller supplies the real
Executor/approval path; native capture, append, history and Undo are never replaced.
Post-import copies have a distinct final readback phase so reload cannot manufacture Undo history.
"""

import hashlib
import json
from pathlib import Path

import bpy


CASES = (
    ('object', 'OBJECT', 'Fixture model', 'placed_model'),
    ('collection', 'COLLECTION', 'Fixture collection', 'placed_model'),
    ('material', 'MATERIAL', 'Fixture material', 'applied_material'),
    ('world', 'WORLD', 'Fixture world', 'replaced_world'),
    ('geometry_modifier', 'NODETREE', 'Fixture geometry modifier', 'applied_geometry_nodes'),
    ('shader_load', 'NODETREE', 'Fixture shader group', 'loaded_datablock'),
    ('brush_load', 'BRUSH', 'Fixture brush', 'loaded_datablock'),
)
UI_IDS = {'SCREEN', 'WORKSPACE', 'WINDOWMANAGER'}
ARTIST_IMAGE = 'Fixture unsaved artist pixels'
PACKED_PIXELS = [0.0, 0.0, 0.0, 1.0] * 4
DIRTY_PIXELS = [0.0, 1.0, 1.0, 1.0] * 4


def plain(value):
    if isinstance(value, bpy.types.ID):
        return [value.id_type, value.name_full]
    if isinstance(value, (str, bool, int, float)) or value is None:
        return value
    if hasattr(value, 'to_dict'):
        return {key: plain(item) for key, item in sorted(value.to_dict().items())}
    if hasattr(value, 'items'):
        return {key: plain(item) for key, item in sorted(value.items())}
    return [plain(item) for item in value]


def properties(block):
    return {key: plain(value) for key, value in sorted(block.items())}


def ids(data):
    """Independently enumerate each native Main ID once, excluding only the UI policy.

    all_ids already aggregates the per-type collections. Combining both doubles
    every ID and makes the fixture's created-object count disagree with reality.
    Reject duplicate or incomplete native iteration rather than silently deduping.
    """
    aggregate = data.all_ids
    expected_count = len(aggregate)
    uids, pointers, result = set(), set(), []
    count = 0
    for block in aggregate:
        count += 1
        assert count <= expected_count, 'Native Main ID iteration exceeded its finite count'
        assert isinstance(block, bpy.types.ID), 'Native Main iterator returned a non-ID'
        uid, pointer = block.session_uid, block.as_pointer()
        assert uid > 0 and pointer > 0, 'Native Main ID has no current UID/pointer'
        assert uid not in uids and pointer not in pointers, 'Native Main ID appeared more than once'
        uids.add(uid)
        pointers.add(pointer)
        if block.id_type not in UI_IDS:
            result.append(block)
    assert count == expected_count, 'Native Main ID iteration omitted an entry'
    return result


def node_tree(tree):
    if tree is None:
        return None
    return {
        'name': tree.name_full,
        'nodes': [{'name': node.name, 'type': node.bl_idname,
                   'image': plain(getattr(node, 'image', None)),
                   'node_group': plain(getattr(node, 'node_tree', None)),
                   'inputs': [[socket.identifier, plain(socket.default_value)]
                              for socket in node.inputs if hasattr(socket, 'default_value')]}
                  for node in tree.nodes],
        'links': [[link.from_node.name, link.from_socket.identifier,
                   link.to_node.name, link.to_socket.identifier] for link in tree.links],
    }


def artist_state(*, uids):
    """Typed readback of the fixture's actual Main, not a native recovery certificate.

    All non-UI ID membership, artist object/mesh/camera/light state and assignment graphs are
    compared. UI/cursor/toolsettings keep ordinary Undo policy; no full-UI rollback is asserted.
    """
    scene = bpy.context.scene
    inventory = [[block.id_type, block.name_full, *([block.session_uid] if uids else [])]
                 for block in ids(bpy.data)]
    return {
        'ids': sorted(inventory),
        'fake_users': sorted([[block.id_type, block.name_full, block.use_fake_user]
                              for block in ids(bpy.data)]),
        'properties': sorted([[block.id_type, block.name_full, properties(block)]
                              for block in ids(bpy.data)]),
        'scene': {'name': scene.name_full, 'frame': scene.frame_current,
                  'world': plain(scene.world), 'fps': scene.render.fps,
                  'root_objects': sorted(obj.name_full for obj in scene.collection.objects),
                  'root_collections': sorted(child.name_full for child in scene.collection.children),
                  'fps_base': scene.render.fps_base,
                  'unit_system': scene.unit_settings.system,
                  'unit_scale': scene.unit_settings.scale_length},
        'objects': [{'name': obj.name_full, 'type': obj.type, 'mode': obj.mode, 'data': plain(obj.data),
                     'parent': plain(obj.parent), 'location': list(obj.location),
                     'rotation': list(obj.rotation_euler), 'scale': list(obj.scale),
                     'hide_viewport': obj.hide_viewport, 'hide_render': obj.hide_render,
                     'slots': [[slot.link, plain(slot.material)] for slot in obj.material_slots],
                     'active_slot': obj.active_material_index,
                     'modifiers': [[mod.name, mod.type, plain(getattr(mod, 'node_group', None))]
                                   for mod in obj.modifiers]}
                    for obj in sorted(bpy.data.objects, key=lambda obj: obj.name_full)],
        'meshes': [{'name': mesh.name_full, 'vertices': [list(v.co) for v in mesh.vertices],
                    'edges': [list(edge.vertices) for edge in mesh.edges],
                    'polygons': [[list(poly.vertices), poly.material_index] for poly in mesh.polygons],
                    'materials': [plain(mat) for mat in mesh.materials]}
                   for mesh in sorted(bpy.data.meshes, key=lambda mesh: mesh.name_full)],
        'cameras': [[cam.name_full, cam.type, cam.lens, cam.clip_start, cam.clip_end,
                     cam.shift_x, cam.shift_y] for cam in sorted(bpy.data.cameras, key=lambda x: x.name_full)],
        'lights': [[light.name_full, light.type, light.energy, list(light.color)]
                   for light in sorted(bpy.data.lights, key=lambda x: x.name_full)],
        'collections': [[collection.name_full, sorted(obj.name_full for obj in collection.objects),
                         sorted(child.name_full for child in collection.children)]
                        for collection in sorted(bpy.data.collections, key=lambda x: x.name_full)],
        'worlds': [[world.name_full, list(world.color), world.use_nodes, node_tree(world.node_tree)]
                   for world in sorted(bpy.data.worlds, key=lambda x: x.name_full)],
        'materials': [[mat.name_full, list(mat.diffuse_color), mat.use_nodes, node_tree(mat.node_tree)]
                      for mat in sorted(bpy.data.materials, key=lambda x: x.name_full)],
        'node_groups': [node_tree(tree) for tree in sorted(bpy.data.node_groups, key=lambda x: x.name_full)],
        'images': [[image.name_full, image.source, list(image.size), bool(image.packed_file),
                    list(image.pixels)] for image in sorted(bpy.data.images, key=lambda x: x.name_full)
                   if image.type == 'IMAGE'],
    }


def dirty_artist(case, *, image_pixels=False):
    cube = bpy.data.objects['Cube']
    cube.data.vertices[0].co.x = 2.25
    cube.data.update()
    cube['generic_import_unsaved_marker'] = case
    if image_pixels:
        image = bpy.data.images[ARTIST_IMAGE]
        image.pixels[:] = DIRTY_PIXELS
        image.update()
        assert image.is_dirty, 'Fixture must exercise an unsaved pixel cache, not freshly packed pixels'
    bpy.data.objects['Camera'].data.lens = 55.5
    bpy.data.objects['Light'].data.energy = 512.25
    # Explicit dependency update is part of the artist fixture, before native import admission.
    bpy.context.view_layer.update()


def assert_dirty_artist(case, *, image_pixels=False):
    # Always reacquire; Undo/publication may have replaced every old RNA wrapper.
    if image_pixels:
        assert list(bpy.data.images[ARTIST_IMAGE].pixels) == DIRTY_PIXELS, 'Unsaved artist pixels were lost'
    assert bpy.data.objects['Cube'].get('generic_import_unsaved_marker') == case
    assert bpy.data.objects['Cube'].data.vertices[0].co.x == 2.25
    assert bpy.data.objects['Camera'].data.lens == 55.5
    assert bpy.data.objects['Light'].data.energy == 512.25


def check_effect(case, row, result, before, destination, target_names):
    from angelo_agent import blender_asset_index as index
    assert result.get('success') is True and result.get('blender_undoable') is True, result
    expected_usage = next(entry[3] for entry in CASES if entry[0] == case)
    assert result['usage'] == expected_usage and result['id_type'] == row['id_type'], result
    primary = getattr(bpy.data, index.TYPES[row['id_type']][0]).get(result['datablock'])
    assert primary is not None, result
    stamp = {key: row[key] for key in ('id', 'name', 'origin', 'license', 'author')}
    assert properties(primary).get('angelo_blender_asset') == stamp
    old_ids = {tuple(entry) for entry in before['ids']}
    current_ids = {(block.id_type, block.name_full, block.session_uid) for block in ids(bpy.data)}
    assert old_ids <= current_ids, 'Import removed/replaced an existing artist ID'
    created = [block for block in ids(bpy.data)
               if (block.id_type, block.name_full, block.session_uid) not in old_ids]
    assert primary in created
    if case in {'object', 'collection'}:
        collection = (bpy.context.scene.collection if destination['scene_master'] else
                      bpy.data.collections[destination['name']])
        if case == 'object':
            assert primary in collection.objects.values()
        else:
            assert primary in collection.children.values()
        objects = [block for block in created if block.id_type == 'OBJECT']
        assert objects and result['object_count'] == len(objects)
        for obj in objects:
            assert obj in (collection.objects.values() if case == 'object' else primary.all_objects.values())
            assert properties(obj).get('angelo_blender_asset') == stamp
            if obj.parent not in objects:
                assert tuple(obj.location) == (4.0, 5.0, 6.0)
    elif case == 'material':
        targets = [bpy.data.objects[name] for name in target_names]
        assert len(targets) == 2 and targets[0].data == targets[1].data
        assert targets[0].material_slots[0].link == 'DATA'
        assert targets[1].material_slots[0].link == 'OBJECT'
        assert all(obj.active_material == primary for obj in targets)
    elif case == 'world':
        assert bpy.context.scene.world == primary
    elif case == 'geometry_modifier':
        obj = bpy.data.objects[target_names[0]]
        assert len(obj.modifiers) == 1
        assert obj.modifiers[0].type == 'NODES' and obj.modifiers[0].node_group == primary
    else:
        assert primary.use_fake_user
        # The declared load-only contract must not assign a modifier, material or World.
        after = artist_state(uids=True)
        assert after['objects'] == before['objects'] and after['scene'] == before['scene']
    return {'created_ids': [[block.id_type, block.name_full] for block in created],
            'primary': [primary.id_type, primary.name_full], 'provenance': stamp}


def assert_existing_artist_fields(case, before, after, target_names):
    """The four sentinel edits are necessary but do not excuse unrelated existing-ID changes."""
    after_objects = {obj['name']: obj for obj in after['objects']}
    target_meshes = set()
    for obj in before['objects']:
        found = dict(after_objects[obj['name']])
        expected = dict(obj)
        if case == 'material' and obj['name'] in target_names:
            target_meshes.add(obj['data'][1])
            found.pop('slots')
            expected.pop('slots')
        if case == 'geometry_modifier' and obj['name'] in target_names:
            found.pop('modifiers')
            expected.pop('modifiers')
        assert found == expected, f'{case}: unrelated existing Object fields changed'
    after_meshes = {mesh['name']: mesh for mesh in after['meshes']}
    for mesh in before['meshes']:
        found, expected = dict(after_meshes[mesh['name']]), dict(mesh)
        if mesh['name'] in target_meshes:
            found.pop('materials')
            expected.pop('materials')
        assert found == expected, f'{case}: unrelated existing Mesh fields changed'
    for category in ('cameras', 'lights'):
        assert after[category] == before[category], f'{case}: existing {category} changed'
    after_properties = {(kind, name): value for kind, name, value in after['properties']}
    for kind, name, value in before['properties']:
        assert after_properties[kind, name] == value, f'{case}: existing custom properties changed'


def window_boundary(executor, expected_path=None):
    """Rebind only from fresh native context after this fixture's document change.

    A GUI file read clears Context.window and expires the old manager identity,
    even with load_ui=False. Return to the actual event loop before consulting
    the new context; do not reuse a saved window or select a replacement from a
    window list. Keep only immutable post-operation document facts across yield.
    """
    manager_uid = bpy.context.window_manager.session_uid
    document = bpy.data.filepath
    if expected_path is not None:
        assert Path(document).resolve() == Path(expected_path).resolve(), 'Unexpected loaded fixture file'
    yield None
    assert bpy.context.window_manager.session_uid == manager_uid, 'Document changed during fixture boundary'
    assert bpy.data.filepath == document, 'Fixture document changed before context rebinding'
    window = bpy.context.window
    assert window is not None, 'Native window context did not return after the fixture document change'
    assert window.scene == bpy.context.scene and window.view_layer == bpy.context.view_layer, \
        'Fresh native window does not own the fixture context Scene/ViewLayer'
    executor.window = window
    assert executor.window == window, 'Fresh native window is not live in the current manager'


def unretained_material_memory_case(out, rows, execute_import, executor, object_handle, report):
    """Default-false, zero-user artist IDs must survive memory Undo, independently of disk policy."""
    cube = bpy.data.objects['Cube']
    assert len(cube.material_slots) == 1 and cube.active_material is not None
    original_uid, original_name = cube.active_material.session_uid, cube.active_material.name_full
    assert not cube.active_material.use_fake_user, 'Memory variant must retain the default-false case'
    shared = bpy.data.objects.new('Fixture artist shared mesh', cube.data)
    bpy.context.collection.objects.link(shared)
    cube.material_slots[0].link = 'OBJECT'
    targets = [shared.name_full, cube.name_full]
    args = {'objects': [object_handle(bpy.data.objects[name]) for name in targets]}
    destination = {'name': bpy.context.collection.name_full,
                   'scene_master': bpy.context.collection == bpy.context.scene.collection}
    del cube, shared
    dirty_artist('unretained_material_memory', image_pixels=True)
    before = artist_state(uids=True)
    row = next(row for row in rows if row['id_type'] == 'MATERIAL')
    result = yield execute_import(row, **args)
    check_effect('material', row, result, before, destination, targets)
    after = artist_state(uids=True)
    assert_existing_artist_fields('material', before, after, targets)
    assert_dirty_artist('unretained_material_memory', image_pixels=True)
    original = next(material for material in bpy.data.materials if material.session_uid == original_uid)
    retention = [original.id_type, original.name_full, original.users, original.use_fake_user]
    assert original.name_full == original_name and original.users == 0 and not original.use_fake_user
    assert next(entry for entry in after['materials'] if entry[0] == original_name) == \
        next(entry for entry in before['materials'] if entry[0] == original_name)
    del original
    assert bpy.ops.ed.undo() == {'FINISHED'}
    yield from window_boundary(executor)
    assert artist_state(uids=True) == before, 'Default-false orphan Material Undo lost the dirty preimage'
    assert bpy.ops.ed.redo() == {'FINISHED'}
    yield from window_boundary(executor)
    assert artist_state(uids=True) == after, 'Redo lost the zero-user default-false artist Material'
    assert next([block.id_type, block.name_full, block.users, block.use_fake_user]
                for block in bpy.data.materials if block.session_uid == original_uid) == retention, \
        'Redo changed the original zero-user Material retention state'
    assert bpy.ops.ed.undo() == {'FINISHED'}
    yield from window_boundary(executor)
    assert artist_state(uids=True) == before, 'Second Undo lost the default-false artist state'
    proof = {'complete': True, 'original_material_uid': original_uid,
             'post_import_retention': retention, 'before': before, 'after': after,
             'dirty_undo_redo_undo': True}
    (out / 'unretained-material-memory.json').write_text(json.dumps(proof, indent=2) + '\n')
    report['unretained_material_memory'] = proof
    report['checks']['unretained_material_dirty_undo_redo'] = True


def unretained_world_memory_case(out, rows, execute_import, executor, report):
    """A replaced default-false World stays in Main and in native memory history."""
    original_uid = bpy.context.scene.world.session_uid
    original_name = bpy.context.scene.world.name_full
    assert not bpy.context.scene.world.use_fake_user, 'World memory variant must be default-false'
    destination = {'name': bpy.context.collection.name_full,
                   'scene_master': bpy.context.collection == bpy.context.scene.collection}
    dirty_artist('unretained_world_memory', image_pixels=True)
    before = artist_state(uids=True)
    row = next(row for row in rows if row['id_type'] == 'WORLD')
    result = yield execute_import(row)
    check_effect('world', row, result, before, destination, [])
    after = artist_state(uids=True)
    assert_existing_artist_fields('world', before, after, [])
    assert_dirty_artist('unretained_world_memory', image_pixels=True)
    original = next(world for world in bpy.data.worlds if world.session_uid == original_uid)
    retention = [original.id_type, original.name_full, original.users, original.use_fake_user]
    assert original.name_full == original_name and original.users == 0 and not original.use_fake_user
    assert next(entry for entry in after['worlds'] if entry[0] == original_name) == \
        next(entry for entry in before['worlds'] if entry[0] == original_name)
    del original
    assert bpy.ops.ed.undo() == {'FINISHED'}
    yield from window_boundary(executor)
    assert artist_state(uids=True) == before, 'Default-false orphan World Undo lost the dirty preimage'
    assert bpy.ops.ed.redo() == {'FINISHED'}
    yield from window_boundary(executor)
    assert artist_state(uids=True) == after, 'Redo lost the zero-user default-false artist World'
    assert next([block.id_type, block.name_full, block.users, block.use_fake_user]
                for block in bpy.data.worlds if block.session_uid == original_uid) == retention, \
        'Redo changed the original zero-user World retention state'
    assert bpy.ops.ed.undo() == {'FINISHED'}
    yield from window_boundary(executor)
    assert artist_state(uids=True) == before, 'Second Undo lost the default-false World artist state'
    proof = {'complete': True, 'original_world_uid': original_uid,
             'post_import_retention': retention, 'before': before, 'after': after,
             'dirty_undo_redo_undo': True}
    (out / 'unretained-world-memory.json').write_text(json.dumps(proof, indent=2) + '\n')
    report['unretained_world_memory'] = proof
    report['checks']['unretained_world_dirty_undo_redo'] = True


def cases(out, rows, execute_import, executor, object_handle, report):
    """Generator consumed by the existing suite timer. Every case starts from owned fresh data."""
    root = out / 'generic-dirty-undo'
    root.mkdir(exist_ok=True)
    baseline = root / 'factory-baseline.blend'
    image = bpy.data.images.new(ARTIST_IMAGE, width=2, height=2)
    image.use_fake_user = True  # Keep the zero-reference artist image in the saved baseline.
    image.pixels[:] = PACKED_PIXELS
    image.pack()
    assert image.source == 'FILE' and image.packed_file
    # First exercise normal default-false memory preservation. Then explicitly
    # declare keep intent for the separate seven-case full saved-copy contract.
    memory_baseline = root / 'unretained-memory-baseline.blend'
    assert bpy.ops.wm.save_as_mainfile(filepath=str(memory_baseline)) == {'FINISHED'}
    del image
    assert bpy.ops.wm.open_mainfile(filepath=str(memory_baseline), load_ui=False,
                                    use_scripts=False) == {'FINISHED'}
    yield from window_boundary(executor, memory_baseline)
    yield from unretained_material_memory_case(root, rows, execute_import, executor, object_handle, report)
    assert bpy.ops.wm.open_mainfile(filepath=str(memory_baseline), load_ui=False,
                                    use_scripts=False) == {'FINISHED'}
    yield from window_boundary(executor, memory_baseline)
    yield from unretained_world_memory_case(root, rows, execute_import, executor, report)
    assert bpy.ops.wm.open_mainfile(filepath=str(memory_baseline), load_ui=False,
                                    use_scripts=False) == {'FINISHED'}
    yield from window_boundary(executor, memory_baseline)
    material = bpy.data.objects['Cube'].active_material
    assert material is not None and not material.use_fake_user
    material.use_fake_user = True
    assert material.use_fake_user, 'Saved-copy baseline explicitly keeps the artist Material'
    del material
    world = bpy.context.scene.world
    assert world is not None and not world.use_fake_user
    world.use_fake_user = True
    assert world.use_fake_user, 'Saved-copy baseline explicitly keeps the artist World'
    del world
    assert bpy.ops.wm.save_as_mainfile(filepath=str(baseline)) == {'FINISHED'}
    ledger = {'dataset': 'SYNTHETIC .blend assets; NOT official Essentials catalog/acquisition',
              'native_owner': 'actual registered B1 adapter; no replacement', 'cases': [],
              'readback_complete': False}
    for case, id_type, name, _usage in CASES:
        assert bpy.ops.wm.open_mainfile(filepath=str(baseline), load_ui=False,
                                        use_scripts=False) == {'FINISHED'}
        yield from window_boundary(executor, baseline)
        image = bpy.data.images.get(ARTIST_IMAGE)
        assert image is not None and image.use_fake_user, 'Saved artist image did not survive baseline reopen'
        assert image.source == 'FILE' and image.packed_file, 'Artist image lost its packed baseline backing'
        assert list(image.pixels) == PACKED_PIXELS, 'Baseline backing differs before the unsaved pixel edit'
        folder = root / case
        folder.mkdir(exist_ok=True)
        cube = bpy.data.objects['Cube']
        target_names = []
        if case == 'material':
            assert len(cube.material_slots) == 1, 'The owned factory baseline must have its material slot'
            shared = bpy.data.objects.new('Fixture artist shared mesh', cube.data)
            bpy.context.collection.objects.link(shared)
            cube.material_slots[0].link = 'OBJECT'
            target_names = [shared.name_full, cube.name_full]  # Deliberately reverse Scene order.
        elif case == 'geometry_modifier':
            target_names = [cube.name_full]
        args = {'objects': [object_handle(bpy.data.objects[name]) for name in target_names]} if target_names else {}
        if case in {'object', 'collection'}:
            args['location'] = [4.0, 5.0, 6.0]
        destination = {'name': bpy.context.collection.name_full,
                       'scene_master': bpy.context.collection == bpy.context.scene.collection}
        case_baseline = folder / 'saved-before-dirty.blend'
        assert bpy.ops.wm.save_as_mainfile(filepath=str(case_baseline)) == {'FINISHED'}
        clean = artist_state(uids=True)
        dirty_artist(case, image_pixels=True)
        before = artist_state(uids=True)
        assert before != clean
        assert before['meshes'] != clean['meshes'] and before['properties'] != clean['properties']
        assert before['images'] != clean['images']
        assert before['cameras'] != clean['cameras'] and before['lights'] != clean['lights']
        saved_before = artist_state(uids=False)
        row = next(row for row in rows if row['id_type'] == id_type and row['name'] == name)
        case_receipt = folder / 'receipt.json'
        facts = {'case': case, 'phase': 'dirty_before_import',
                 'row': {'id': row['id'], 'name': row['name'], 'id_type': id_type},
                 'destination': destination, 'target_order': target_names,
                 'saved_baseline_state': clean, 'before': before,
                 'before_without_uids': saved_before, 'single_undo_dirty_preimage': False,
                 'saved_copy_readback': False}
        case_receipt.write_text(json.dumps(facts, indent=2) + '\n')
        result = yield execute_import(row, **args)
        facts['result'] = {key: result.get(key) for key in
                           ('success', 'usage', 'id_type', 'datablock', 'blender_undoable', 'error_code')}
        facts['phase'] = 'import_returned'
        case_receipt.write_text(json.dumps(facts, indent=2) + '\n')
        effects = check_effect(case, row, result, before, destination, target_names)
        assert_dirty_artist(case, image_pixels=True)
        assert_existing_artist_fields(case, before, artist_state(uids=True), target_names)
        after = artist_state(uids=False)
        postcopy = folder / 'post-import-copy.blend'
        facts['post_import_retention'] = [[block.id_type, block.name_full, block.users,
                                           block.use_fake_user] for block in ids(bpy.data)]
        assert bpy.ops.wm.save_as_mainfile(filepath=str(postcopy), copy=True) == {'FINISHED'}
        # Saving a .blend stores the encoded packed backing, not a modified pixel cache.
        # The full dirty projection remains mandatory for import/Undo/Redo below.
        saved_after = json.loads(json.dumps(after))
        for entry in saved_after['images']:
            if entry[0] == ARTIST_IMAGE:
                entry[4] = PACKED_PIXELS
        facts.update(effects=effects, after=after, saved_after=saved_after,
                     post_import_copy=str(postcopy), phase='before_undo')
        facts['post_import_sha256'] = hashlib.sha256(postcopy.read_bytes()).hexdigest()
        case_receipt.write_text(json.dumps(facts, indent=2) + '\n')
        assert bpy.ops.ed.undo() == {'FINISHED'}, 'Import must be one real Undo, without a baseline push'
        yield from window_boundary(executor)
        # Re-read all IDs after Undo; no retained RNA pointer is consulted for acceptance.
        rebound = artist_state(uids=True)
        assert rebound == before, f'{case}: Undo did not preserve the complete typed dirty preimage'
        assert_dirty_artist(case, image_pixels=True)
        assert bpy.ops.ed.redo() == {'FINISHED'}, f'{case}: actual Redo failed'
        yield from window_boundary(executor)
        assert artist_state(uids=False) == after, f'{case}: Redo did not restore the imported effect and dirty pixels'
        assert_dirty_artist(case, image_pixels=True)
        assert bpy.ops.ed.undo() == {'FINISHED'}
        yield from window_boundary(executor)
        assert artist_state(uids=True) == before, f'{case}: second Undo lost the dirty preimage'
        facts.update(single_undo_dirty_preimage=True, redo_dirty_effect=True, phase='single_undo_redo_verified')
        case_receipt.write_text(json.dumps(facts, indent=2) + '\n')
        ledger['cases'].append(facts)
        (root / 'manifest.json').write_text(json.dumps(ledger, indent=2) + '\n')
    report['generic_seven_mode_dirty_undo'] = ledger
    # Leave the old suite's separate real bundled GN path at its original factory state.
    assert bpy.ops.wm.open_mainfile(filepath=str(baseline), load_ui=False,
                                    use_scripts=False) == {'FINISHED'}
    yield from window_boundary(executor, baseline)



def failure_case(out, execute_import, executor, report):
    """Actual created-ID provenance refusal, not a monkeypatched native fault seam."""
    from angelo_agent import blender_assets as assets, blender_asset_commit
    path = out / 'reserved-provenance-collision.blend'
    with bpy.data.temp_data() as data:
        block = data.objects.new('Fixture reserved provenance collision', None)
        block['angelo_blender_asset'] = {'artist_note': 'preserve this source metadata'}
        block.asset_mark()
        data.libraries.write(str(path), {block})
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    file = dict(path=str(path), relative=path.name, size=path.stat().st_size, sha256=digest, url='')
    row = assets.bundled_rows(file)[0]
    assets._rows[row['id']] = row
    dirty_artist('reserved_collision', image_pixels=True)
    before = artist_state(uids=True)
    result = yield execute_import(row)
    assert result.get('success') is not True and result.get('scene_changed') is False, result
    assert 'already uses reserved Angelo provenance fields' in result.get('error', ''), result
    assert 'The prior state was restored.' in result.get('error', ''), result
    assert artist_state(uids=True) == before, 'Failed product import changed the dirty artist preimage'
    assert_dirty_artist('reserved_collision', image_pixels=True)
    assert blender_asset_commit.recovery_status() is None, 'Checked restore did not close its retained owner'
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, 'Source custom properties were rewritten'
    with bpy.data.temp_data() as data:
        with data.libraries.load(str(path)) as (_source, destination):
            destination.objects = ['Fixture reserved provenance collision']
        assert properties(destination.objects[0])['angelo_blender_asset'] == {
            'artist_note': 'preserve this source metadata'}
    report['product_created_id_failure_restore'] = {
        'success': True, 'failure': 'reserved provenance collision after append',
        'dirty_mesh_properties_pixels_preserved': True, 'source_unchanged': True,
        'result': {key: result.get(key) for key in ('success', 'scene_changed', 'error_code')}}


def sequential_cases(out, rows, execute_import, executor, object_handle, report):
    """No fresh-Main shortcut: each preparation must support the prior imported ID closure."""
    root = out / 'sequential-dirty-undo'
    root.mkdir(exist_ok=True)
    baseline = out / 'generic-dirty-undo/factory-baseline.blend'
    assert bpy.ops.wm.open_mainfile(filepath=str(baseline), load_ui=False,
                                    use_scripts=False) == {'FINISHED'}
    yield from window_boundary(executor, baseline)
    cube = bpy.data.objects['Cube']
    shared = bpy.data.objects.new('Fixture artist shared mesh', cube.data)
    bpy.context.collection.objects.link(shared)
    assert len(cube.material_slots) == 1
    cube.material_slots[0].link = 'OBJECT'
    assert bpy.ops.wm.save_as_mainfile(filepath=str(root / 'saved-before-dirty.blend')) == {'FINISHED'}
    ledger = {'dataset': 'SYNTHETIC sequential already-imported Main; not official acquisition',
              'sequence': [], 'final_single_undo': False, 'readback_complete': False}
    prior_created = set()
    for case in ('object', 'material', 'geometry_modifier', 'brush_load'):
        _, id_type, name, _usage = next(entry for entry in CASES if entry[0] == case)
        row = next(row for row in rows if row['id_type'] == id_type and row['name'] == name)
        targets = (['Fixture artist shared mesh', 'Cube'] if case == 'material' else
                   ['Cube'] if case == 'geometry_modifier' else [])
        args = {'objects': [object_handle(bpy.data.objects[name]) for name in targets]} if targets else {}
        if case == 'object':
            args['location'] = [4.0, 5.0, 6.0]
        destination = {'name': bpy.context.collection.name_full,
                       'scene_master': bpy.context.collection == bpy.context.scene.collection}
        marker = 'sequential_' + case
        dirty_artist(marker, image_pixels=True)
        before = artist_state(uids=True)
        assert prior_created <= {tuple(entry) for entry in before['ids']}
        entry = {'case': case, 'before': before, 'prior_imported_ids': sorted(prior_created),
                 'phase': 'dirty_before_import'}
        ledger['sequence'].append(entry)
        (root / 'manifest.json').write_text(json.dumps(ledger, indent=2) + '\n')
        result = yield execute_import(row, **args)
        entry['result'] = {key: result.get(key) for key in
                           ('success', 'usage', 'id_type', 'datablock', 'blender_undoable', 'error_code')}
        entry['phase'] = 'import_returned'
        (root / 'manifest.json').write_text(json.dumps(ledger, indent=2) + '\n')
        entry['effects'] = check_effect(case, row, result, before, destination, targets)
        assert_dirty_artist(marker, image_pixels=True)
        after = artist_state(uids=True)
        assert_existing_artist_fields(case, before, after, targets)
        prior_created |= {tuple(value) for value in after['ids']} - {tuple(value) for value in before['ids']}
        entry.update(phase='import_verified', after=after)
        (root / 'manifest.json').write_text(json.dumps(ledger, indent=2) + '\n')
        # Deliberately NO Undo, reload or Save here. Next capture sees the imported Image/NodeTree
        # storage and later modifier/Brush data instead of the pristine factory Main.
    postcopy = root / 'post-import-copy.blend'
    ledger['after_without_uids'] = artist_state(uids=False)
    ledger['post_import_retention'] = [[block.id_type, block.name_full, block.users,
                                        block.use_fake_user] for block in ids(bpy.data)]
    assert bpy.ops.wm.save_as_mainfile(filepath=str(postcopy), copy=True) == {'FINISHED'}
    ledger['post_import_copy'] = str(postcopy)
    ledger['post_import_sha256'] = hashlib.sha256(postcopy.read_bytes()).hexdigest()
    ledger['phase'] = 'before_final_undo'
    (root / 'manifest.json').write_text(json.dumps(ledger, indent=2) + '\n')
    assert bpy.ops.ed.undo() == {'FINISHED'}
    yield from window_boundary(executor)
    assert artist_state(uids=True) == before, 'Brush Undo lost earlier imports or dirty artist state'
    assert_dirty_artist('sequential_brush_load', image_pixels=True)
    ledger['final_single_undo'] = True
    ledger['phase'] = 'final_undo_verified'
    (root / 'manifest.json').write_text(json.dumps(ledger, indent=2) + '\n')
    report['same_main_sequence'] = ledger
    assert bpy.ops.wm.open_mainfile(filepath=str(baseline), load_ui=False,
                                    use_scripts=False) == {'FINISHED'}
    yield from window_boundary(executor, baseline)


def record_readback_diagnostic(path, case, expected):
    """Persist exact synthetic saved-state evidence without changing the acceptance oracle."""
    actual = artist_state(uids=False)
    differences = {
        key: {'expected_present': key in expected, 'actual_present': key in actual,
              'expected': expected.get(key), 'actual': actual.get(key)}
        for key in sorted(expected.keys() | actual.keys())
        if (key in expected) != (key in actual) or expected.get(key) != actual.get(key)}
    path.write_text(json.dumps({'case': case, 'document': bpy.data.filepath,
                               'expected': expected, 'actual': actual,
                               'field_differences': differences}, indent=2) + '\n')


def readback(out, executor):
    """Distinct final phase, after every import/Undo case and the existing checks completed.

    This does no acquisition, import, Undo or Redo: only owned saved-copy load and readback.
    """
    path = out / 'generic-dirty-undo/manifest.json'
    ledger = json.loads(path.read_text())
    assert [entry['case'] for entry in ledger['cases']] == [entry[0] for entry in CASES]
    assert all(entry['single_undo_dirty_preimage'] is True and entry['redo_dirty_effect'] is True
               for entry in ledger['cases'])
    assert not (out / 'fixture.blend').exists(), 'Readback must not retain the synthetic source payload'
    for entry in ledger['cases']:
        copy = Path(entry['post_import_copy']).resolve()
        assert copy.is_relative_to((out / 'generic-dirty-undo').resolve())
        assert hashlib.sha256(copy.read_bytes()).hexdigest() == entry['post_import_sha256']
        assert bpy.ops.wm.open_mainfile(filepath=str(copy), load_ui=False,
                                        use_scripts=False) == {'FINISHED'}
        yield from window_boundary(executor, copy)
        record_readback_diagnostic(copy.parent / 'readback-diagnostic.json',
                                   entry['case'], entry['saved_after'])
        assert artist_state(uids=False) == entry['saved_after'], entry['case']
        assert not bpy.data.libraries
        for image in bpy.data.images:
            if image.source in {'FILE', 'TILED', 'MOVIE', 'SEQUENCE'}:
                assert image.packed_file, image.name_full
        entry['saved_copy_readback'] = True
        (copy.parent / 'readback.json').write_text(json.dumps(entry, indent=2) + '\n')
    ledger['readback_complete'] = True
    (path.parent / 'readback-manifest.json').write_text(json.dumps(ledger, indent=2) + '\n')
    sequential_path = out / 'sequential-dirty-undo/manifest.json'
    sequential = json.loads(sequential_path.read_text())
    assert sequential['final_single_undo'] is True
    assert [entry['case'] for entry in sequential['sequence']] == [
        'object', 'material', 'geometry_modifier', 'brush_load']
    copy = Path(sequential['post_import_copy']).resolve()
    assert copy.is_relative_to((out / 'sequential-dirty-undo').resolve())
    assert hashlib.sha256(copy.read_bytes()).hexdigest() == sequential['post_import_sha256']
    assert bpy.ops.wm.open_mainfile(filepath=str(copy), load_ui=False,
                                    use_scripts=False) == {'FINISHED'}
    yield from window_boundary(executor, copy)
    expected = json.loads(json.dumps(sequential['after_without_uids']))
    for entry in expected['images']:
        if entry[0] == ARTIST_IMAGE:
            entry[4] = PACKED_PIXELS
    record_readback_diagnostic(copy.parent / 'readback-diagnostic.json', 'sequential', expected)
    assert artist_state(uids=False) == expected
    assert not bpy.data.libraries
    for image in bpy.data.images:
        if image.source in {'FILE', 'TILED', 'MOVIE', 'SEQUENCE'}:
            assert image.packed_file, image.name_full
    sequential['readback_complete'] = True
    (sequential_path.parent / 'readback-manifest.json').write_text(json.dumps(sequential, indent=2) + '\n')
    ledger['same_main_sequence'] = sequential
    return ledger
