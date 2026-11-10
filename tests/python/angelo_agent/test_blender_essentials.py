# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unbilled native Essentials tool fixture, isolated profile only. No account, inference or user files.

Runs the actual BlenderExecutor precondition/execute paths and approval-card builder. Offline by default;
ANGELO_QA_ESSENTIALS_ONLINE=1 additionally reads the official catalog and imports a small model/material/world.
Checks its receipt, not just process exit. Online mode uses Blender's real download owner and cache.
This is functional import/Undo/readback acceptance, not the pending native partial-assignment fault gate.
"""

import os, sys  # noqa: E401  The suite folder must precede the isolation import.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: never expose real provider logins.


import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import time
import traceback
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts/addons_core'))
from angelo_agent import blender_assets as assets, blender_asset_import, blender_asset_index as index
from angelo_agent import scene_queries
from angelo_agent.references import ReferenceStore
from angelo_agent.blender_tools import BlenderExecutor
from angelo_agent.library_spec import SEARCH, IMPORT
from angelo_agent.approvals import approval_message
import essentials_dirty_undo

OUT = Path(os.environ['ANGELO_QA_DIR']).resolve()
OUT.mkdir(parents=True, exist_ok=True)
ONLINE = os.environ.get('ANGELO_QA_ESSENTIALS_ONLINE') == '1'
report = {'success': False, 'phase': 'starting', 'checks': {}, 'online': ONLINE,
          'fixture_phases': {'synthetic_seven_mode_import_undo': False,
                             'same_main_sequence': False, 'saved_copy_readback': False},
          'native_fault_readiness': False,
          'pending_native_fault_checks': ['arbitrary native partial-publication fault injection remains separate; '
                                          'product reserved-provenance refusal/restore is exercised below'],
          'binary_hash': bpy.app.build_hash.decode(), 'blender_version': bpy.app.version_string,
          'source': str(ROOT), 'measurements': {}, 'imports': {}, 'downloads': {}}
assert Path(assets.__file__).is_relative_to(ROOT), 'Fixture did not load the source under test'
assert Path(essentials_dirty_undo.__file__).resolve().parent == Path(__file__).resolve().parent
report['source_sha256'] = {name: hashlib.sha256((ROOT / 'scripts/addons_core/angelo_agent' / (name + '.py')).read_bytes()).hexdigest()
                         for name in ('blender_assets', 'blender_asset_index', 'blender_asset_import',
                                      'blender_assets_agent', 'blender_asset_previews', 'blender_asset_payload',
                                      'blender_asset_commit',
                                      'library_spec', 'library_agent', 'library_approvals')}
for name in report['source_sha256']:
    module = importlib.import_module('angelo_agent.' + name)
    assert Path(module.__file__).resolve().is_relative_to(ROOT), f'{name} was not loaded from this checkout'
checks = report['checks']
report['fixture_sha256'] = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                           for path in (Path(__file__).resolve(), Path(essentials_dirty_undo.__file__).resolve())}
started = time.monotonic()
prefs = bpy.context.preferences
prefs.use_preferences_save = False
prefs.view.show_splash = False
prefs.asset_libraries.use_online_essentials = False
prefs.system.use_online_access = False
executor = BlenderExecutor(bpy.context.window)
executor.studio_context = SimpleNamespace(references=ReferenceStore(OUT, '00000000-0000-0000-0000-000000000001'))


class NativeDependencyUnavailable(RuntimeError):
    pass


def search(query='', **args):
    return executor.execute(SEARCH, dict(query=query, source='blender', **args))


def use(row, **args):
    call = dict(item_id=row['id'], **args)
    assert executor.precondition(IMPORT, call)
    card = approval_message(IMPORT, call)
    assert 'Blender Essentials' in card['body']
    return executor.execute(IMPORT, call)


def target_proof(obj):
    """Direct native identity/content; slots/modifiers are the declared mutable effects below."""
    assert obj.type == 'MESH' and obj.data is not None
    return {'uid': obj.session_uid, 'name': obj.name_full, 'mode': obj.mode,
            'mesh_uid': obj.data.session_uid, 'mesh_name': obj.data.name_full,
            'parent': [obj.parent.session_uid, obj.parent.name_full] if obj.parent else None,
            'location': list(obj.location), 'rotation': list(obj.rotation_euler),
            'scale': list(obj.scale), 'hide_viewport': obj.hide_viewport, 'hide_render': obj.hide_render,
            'properties': essentials_dirty_undo.properties(obj),
            'mesh_properties': essentials_dirty_undo.properties(obj.data),
            'vertices': [list(vertex.co) for vertex in obj.data.vertices],
            'edges': [list(edge.vertices) for edge in obj.data.edges],
            'polygons': [[list(poly.vertices), poly.material_index] for poly in obj.data.polygons]}


def observed_object_handle(obj):
    """One public full-scope observation, never a stale-handle import retry or name fallback."""
    expected = target_proof(obj)
    window = executor.window
    assert window is not None
    # A first read may discover a prior ID replacement and explicitly request a
    # fresh observation. At most one read-only settling pass is allowed; no import
    # has been submitted, and native target identity/content must remain exact.
    for attempt in range(2):
        current = [candidate for candidate in window.scene.objects
                   if candidate.session_uid == expected['uid']]
        assert len(current) == 1 and target_proof(current[0]) == expected
        try:
            result = scene_queries.execute(window, 'angelo_scene_search',
                {'query': expected['name'], 'coverage': 'SCENE', 'limit': 100})
        except scene_queries.SceneQueryError as error:
            if attempt != 0 or str(error) != 'Document IDs were replaced during observation; search again':
                raise
            continue
        break
    assert result.get('read_only') is True and result.get('observation_coverage') == 'SCENE'
    matches = [row['object'] for row in result['objects']
               if row['object'].rsplit(':', 1)[-1] == str(expected['uid'])]
    assert len(matches) == 1, 'Public scene query did not observe the unique original target UID'
    resolved = scene_queries.resolve_object(window, matches[0])
    assert target_proof(resolved) == expected
    return matches[0]


def observe_artist_cube(expected):
    current = [candidate for candidate in executor.window.scene.objects
               if candidate.session_uid == expected['uid']]
    assert len(current) == 1 and target_proof(current[0]) == expected
    handle = observed_object_handle(current[0])
    resolved = scene_queries.resolve_object(executor.window, handle)
    assert target_proof(resolved) == expected
    return resolved, handle


def bundled_import_record(row, result):
    """Separate real installed Essentials selection facts from synthetic generic-effect data."""
    row = assets.get(row['id'])
    assert row['origin'] == 'bundled' and result.get('success') is True, result
    return {'id': row['id'], 'name': row['name'], 'id_type': row['id_type'],
            'origin': row['origin'], 'usage': result['usage'],
            'expected_payload_members': [{key: member[key] for key in ('relative', 'size', 'sha256')}
                                         for member in row.get('files', (row['file'],))]}


def active_brushes():
    # Existing ToolSettings Paint subclasses and read-only Paint.brush RNA, not brush activation.
    settings = bpy.context.scene.tool_settings
    result = {}
    for name in ('sculpt', 'curves_sculpt', 'vertex_paint', 'weight_paint', 'image_paint',
                 'gpencil_paint', 'gpencil_vertex_paint', 'gpencil_sculpt_paint', 'gpencil_weight_paint'):
        paint = getattr(settings, name)
        brush = paint.brush if paint else None
        result[name] = [brush.name_full, brush.session_uid] if brush else None
    return result


def fixture_rows():
    """Synthetic generic-effect data, NOT official Essentials catalog/acquisition proof."""
    path = OUT / 'fixture.blend'
    with bpy.data.temp_data() as data:
        mesh = data.meshes.new('Fixture mesh')
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        obj = data.objects.new('Fixture model', mesh)
        collection = data.collections.new('Fixture collection')
        child = data.objects.new('Collection child', mesh)
        collection.objects.link(child)
        material = data.materials.new('Fixture material')
        material.use_nodes = True
        texture = data.images.new('Fixture packed image', width=2, height=2)
        texture.pixels = [1.0, 0.0, 1.0, 1.0] * 4  # Exact through the packed PNG round trip.
        texture.pack()
        image_node = material.node_tree.nodes.new('ShaderNodeTexImage')
        image_node.image = texture
        bsdf = next(node for node in material.node_tree.nodes if node.type == 'BSDF_PRINCIPLED')
        material.node_tree.links.new(image_node.outputs['Color'], bsdf.inputs['Base Color'])
        mesh.materials.append(material)
        world = data.worlds.new('Fixture world')
        world.use_nodes = True
        group = data.node_groups.new('Fixture shader group', 'ShaderNodeTree')
        geometry = data.node_groups.new('Fixture geometry modifier', 'GeometryNodeTree')
        geometry.is_modifier = True
        geometry.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
        geometry.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
        group_input = geometry.nodes.new('NodeGroupInput')
        group_output = geometry.nodes.new('NodeGroupOutput')
        geometry.links.new(group_input.outputs['Geometry'], group_output.inputs['Geometry'])
        brush = data.brushes.new('Fixture brush', mode='SCULPT')
        blocks = {obj, collection, material, world, group, geometry, brush}
        for block in blocks:
            block.asset_mark()
            block.asset_data.license = 'CC0'
            block.asset_data.author = 'Synthetic native acceptance fixture'
        data.libraries.write(str(path), blocks)
    file = {'path': str(path), 'relative': path.name, 'size': path.stat().st_size,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'url': ''}
    return assets.bundled_rows(file)


def scenario():
    cold = time.monotonic()
    result = yield search('smooth by angle', kind='node_group')
    report['measurements']['cold_search_seconds'] = time.monotonic() - cold
    assert result.get('total') == 1, result
    assert not bpy.data.node_groups and not bpy.data.brushes
    checks['discovery_leaves_main_unchanged'] = True
    smooth = result['items'][0]
    assert smooth['can_apply_as_modifier'] and smooth['downloaded']
    report['bundled_count'] = len(assets._bundled)
    warm = time.monotonic()
    yield search('smooth by angle')
    report['measurements']['warm_search_seconds'] = time.monotonic() - warm
    from angelo_agent.blender_asset_commit import native_api
    from angelo_agent.team_library_files import NotImported
    try:
        native_api()  # The real capability gate; never replaced by a test owner or fake flags.
    except NotImported as error:
        report['native_dependency'] = {'ready': False, 'reason': str(error)}
        raise NativeDependencyUnavailable('Seven native import/Undo cases await the actual owner') from error
    report['native_dependency'] = {'ready': True, 'boundary': 'existing capability validation; not effect proof'}
    # Object is the FIRST successful import in this diagnostic, before the separate real GN path.
    # No fake owner/capability, baseline Undo push, or synthetic official-catalog claim is used.
    rows = fixture_rows()
    assets.publish(assets._bundled, rows)
    yield from essentials_dirty_undo.cases(OUT, rows, use, executor,
                                           observed_object_handle, report)
    checks['synthetic_seven_mode_dirty_single_undo'] = True
    report['fixture_phases']['synthetic_seven_mode_import_undo'] = True
    yield from essentials_dirty_undo.failure_case(OUT, use, executor, report)
    checks['product_created_id_failure_preserves_dirty_preimage'] = True
    yield from essentials_dirty_undo.sequential_cases(OUT, rows, use, executor,
                                                      observed_object_handle, report)
    checks['same_main_sequential_imports_dirty_single_undo'] = True
    report['fixture_phases']['same_main_sequence'] = True
    report['synthetic_seven_mode_saved_copy_readback'] = 'PENDING_SEPARATE_READBACK_PHASE'
    obj = bpy.data.objects['Cube']
    cube_expected = target_proof(obj)
    obj, handle = observe_artist_cube(cube_expected)
    # Actual preference refusal, including a change after approval while the import is pending.
    edit = bpy.context.preferences.edit
    saved_global, saved_steps = edit.use_global_undo, edit.undo_steps
    try:
        from angelo_agent.refusal_codes import RefusalError
        for global_undo, undo_steps in ((False, 32), (True, 0)):
            edit.use_global_undo, edit.undo_steps = global_undo, undo_steps
            try:
                executor.precondition(IMPORT, dict(item_id=smooth['id'], objects=[handle]))
            except RefusalError:
                pass
            else:
                raise AssertionError('Disabled/zero Undo admitted an asset approval')
        edit.use_global_undo, edit.undo_steps = True, 32
        obj, handle = observe_artist_cube(cube_expected)
        pending = use(smooth, objects=[handle])
        edit.undo_steps = 0
        refused = yield pending
        # Library preflight refusals use error/code/unchanged-scene, without a
        # mandatory success key. Require that exact no-effect refusal contract.
        assert (type(refused) is dict and refused.get('error_code') == 'UNSUPPORTED'
                and isinstance(refused.get('error'), str) and refused['error'].strip()
                and refused.get('scene_changed') is False
                and ('success' not in refused or refused['success'] is False)
                and ('partial_changes_possible' not in refused
                     or refused['partial_changes_possible'] is False)), refused
        obj, handle = observe_artist_cube(cube_expected)
        assert not obj.modifiers
        checks['undo_preferences_refuse_before_mutation'] = True
    finally:
        edit.use_global_undo, edit.undo_steps = saved_global, saved_steps
    # No manual baseline: Undo must preserve a real unsaved edit absent from current history.
    # Required diagnostic; the existing after-only push has not proved this contract.
    obj['essentials_dirty_preimage'] = 'keep this earlier artist edit'
    cube_expected['properties']['essentials_dirty_preimage'] = 'keep this earlier artist edit'
    obj, handle = observe_artist_cube(cube_expected)
    dirty_result = yield use(smooth, objects=[handle])
    assert dirty_result.get('success'), dirty_result
    assert bpy.ops.ed.undo() == {'FINISHED'}
    yield from essentials_dirty_undo.window_boundary(executor)
    obj = bpy.data.objects['Cube']
    assert obj.get('essentials_dirty_preimage') == 'keep this earlier artist edit', 'Import Undo lost earlier dirty state'
    assert not obj.modifiers
    checks['dirty_artist_preimage_survives_import_undo'] = True
    bpy.ops.ed.undo_push(message='Essentials fixture baseline')
    obj, handle = observe_artist_cube(cube_expected)
    pending = use(smooth, objects=[handle])
    result = yield pending
    assert result['success'] and result['usage'] == 'applied_geometry_nodes', result
    obj, handle = observe_artist_cube(cube_expected)
    report.setdefault('real_bundled_imports', {})['geometry_modifier'] = bundled_import_record(smooth, result)
    assert len(obj.modifiers) == 1 and obj.modifiers[0].node_group is not None
    assert pending.poll() == result and len(obj.modifiers) == 1
    checks['native_modifier_import_once'] = True
    assert bpy.ops.ed.undo() == {'FINISHED'}
    yield from essentials_dirty_undo.window_boundary(executor)
    assert not bpy.data.objects['Cube'].modifiers
    assert bpy.ops.ed.redo() == {'FINISHED'}
    yield from essentials_dirty_undo.window_boundary(executor)
    assert len(bpy.data.objects['Cube'].modifiers) == 1
    checks['undo_redo'] = True
    # Undo can replace RNA instances. Rebind just as a controller does after an intervening document change.
    obj = bpy.data.objects['Cube']
    obj, handle = observe_artist_cube(cube_expected)
    dependent = next(r for r in assets._bundled if len(r.get('files', ())) > 1 and r['kind'] == 'node_group')
    result = yield use(dependent)
    assert result.get('success') and not bpy.data.libraries, result
    report['real_bundled_imports']['dependency_node_group'] = bundled_import_record(dependent, result)
    checks['bundled_dependency_closure'] = True
    # Real bundled Brush data, not fixture_rows()'s synthetic Brush. Loading is not activation.
    brush_row = min((row for row in assets._bundled if row['kind'] == 'brush'),
                    key=lambda row: sum(member['size'] for member in row.get('files', (row['file'],))))
    modes_before_brush = {obj.name_full: obj.mode for obj in bpy.context.scene.objects}
    brushes_before = active_brushes()
    result = yield use(brush_row)  # No objects, paint-mode change, active-brush change or fake owner.
    assert result.get('success') and result['usage'] == 'loaded_datablock', result
    loaded_brush = bpy.data.brushes.get(result['datablock'])
    assert loaded_brush is not None and loaded_brush.use_fake_user
    expected_stamp = {key: brush_row[key] for key in ('id', 'name', 'origin', 'license', 'author')}
    assert essentials_dirty_undo.properties(loaded_brush).get('angelo_blender_asset') == expected_stamp
    assert {obj.name_full: obj.mode for obj in bpy.context.scene.objects} == modes_before_brush
    assert active_brushes() == brushes_before
    report['real_bundled_imports']['brush_load_only'] = bundled_import_record(brush_row, result)
    checks['real_bundled_brush_loaded_only'] = True
    assets.publish(assets._bundled, rows)
    for row in rows:
        if row['kind'] == 'material':
            obj, handle = observe_artist_cube(cube_expected)
        args = {'objects': [handle]} if row['kind'] == 'material' else {}
        result = yield use(row, **args)
        assert result.get('success'), result
        expected = {'model': 'placed_model', 'material': 'applied_material', 'world': 'replaced_world'}
        assert result['usage'] == expected.get(row['kind'], 'loaded_datablock'), result
        report['imports'][row['id_type']] = result['usage']
        checks['import_' + row['id_type']] = True
        if row['kind'] == 'material':
            obj, handle = observe_artist_cube(cube_expected)
            assert obj.active_material.name.startswith('Fixture material')
        if row['kind'] == 'world':
            assert bpy.context.scene.world.name.startswith('Fixture world')
    obj, handle = observe_artist_cube(cube_expected)
    cancelled = use(smooth, objects=[handle])
    cancelled.cancel()
    assert cancelled.poll()['error_code'] == 'CANCELLED'
    obj, handle = observe_artist_cube(cube_expected)
    assert len(obj.modifiers) == 1
    checks['cancel_no_append'] = True
    # The bound target is deleted after request preparation, before the pending append.
    obj, handle = observe_artist_cube(cube_expected)
    target = obj.copy()
    target.data = obj.data.copy()
    bpy.context.scene.collection.objects.link(target)
    target_handle = scene_queries.object_handle(target)
    pending = use(next(r for r in rows if r['kind'] == 'material'), objects=[target_handle])
    bpy.data.objects.remove(target, do_unlink=True)
    result = yield pending
    assert result['scene_changed'] is False and result['error_code'] == 'UNSUPPORTED', result
    checks['deleted_target_refused'] = True
    # Current product failure/restore is exercised through a real reserved-property collision
    # above. No monkeypatch of library_import.assign or native fault engine is credited.
    unsafe = OUT / 'unsafe.blend'
    with bpy.data.temp_data() as data:
        block = data.objects.new('Driver fixture', None)
        block.asset_mark()
        block.driver_add('location', 0).driver.expression = '1.0'
        data.libraries.write(str(unsafe), {block})
    file = {'path': str(unsafe), 'relative': unsafe.name, 'size': unsafe.stat().st_size,
            'sha256': hashlib.sha256(unsafe.read_bytes()).hexdigest(), 'url': ''}
    bad = assets.bundled_rows(file)[0]
    assets._rows[bad['id']] = bad
    # Native memfile restore preserves ID UIDs/content, not RNA allocation addresses.
    # Keep an independent inventory and full typed artist preimage across this refusal.
    before = {(block.id_type, block.name_full, block.session_uid)
              for block in essentials_dirty_undo.ids(bpy.data)}
    driver_before = essentials_dirty_undo.artist_state(uids=True)
    result = yield use(bad)
    report['unsafe_driver_refusal_result'] = result
    (OUT / 'unsafe-driver-refusal.json').write_text(json.dumps(result, indent=2) + '\n')
    # Inspection is inside the native scope. Its Refused exception is wrapped as
    # verified-restored ImportFailed; that response intentionally has no code.
    assert (type(result) is dict
            and result.get('error') == ('This asset contains drivers; it was not imported. '
                                       'The prior state was restored.')
            and result.get('scene_changed') is False and 'error_code' not in result
            and ('success' not in result or result['success'] is False)
            and ('partial_changes_possible' not in result
                 or result['partial_changes_possible'] is False)), result
    assert {(block.id_type, block.name_full, block.session_uid)
            for block in essentials_dirty_undo.ids(bpy.data)} == before
    assert essentials_dirty_undo.artist_state(uids=True) == driver_before, \
        'Driver refusal did not preserve the complete typed artist preimage'
    from angelo_agent.blender_asset_commit import recovery_status
    assert recovery_status() is None, 'Driver refusal left a retained native recovery owner'
    checks['drivers_refused_before_mutation'] = True
    # The intentional restore expires previously observed handles and RNA wrappers.
    # Reobserve only the original artist target, never retry an expired handle.
    cube_rows = [entry for entry in driver_before['ids']
                 if entry[0] == 'OBJECT' and entry[1] == 'Cube']
    assert len(cube_rows) == 1, 'The saved artist preimage has no unique original Cube'
    cube_uid = cube_rows[0][2]
    yield from essentials_dirty_undo.window_boundary(executor)
    assert essentials_dirty_undo.artist_state(uids=True) == driver_before, \
        'Artist identity/content changed before target reobservation'
    current = [candidate for candidate in executor.window.scene.objects
               if candidate.session_uid == cube_uid]
    assert len(current) == 1 and current[0].name_full == cube_rows[0][1], \
        'The original artist Cube is no longer in the current native scene'
    # This is a required negative check, not a stale-handle import retry.
    try:
        scene_queries.resolve_object(executor.window, handle)
    except RefusalError as error:
        assert error.code == 'STALE_REVISION', error
    else:
        raise AssertionError('A pre-restore observed target handle unexpectedly remained valid')
    obj = current[0]
    obj, handle = observe_artist_cube(cube_expected)
    assert scene_queries.resolve_object(executor.window, handle) == obj
    if ONLINE:
        prefs.system.use_online_access = True
        assert prefs.experimental.use_remote_asset_libraries, 'This build disables remote asset libraries'
        prefs.asset_libraries.use_online_essentials = True
        cache = assets.configuration()[1]
        assert not list(cache.rglob('*.blend')), 'Live proof needs an empty payload cache'
        result = yield search('', limit=20)
        assert not list(cache.rglob('*.blend')), 'Search downloaded asset payloads'
        online_rows = [r for r in assets._rows.values() if r['origin'] == 'online']
        assert online_rows, result
        report['online_count'] = len(online_rows)
        checks['online_search_zero_payloads'] = True
        seen, offset = set(), 0
        while offset is not None:
            page = yield search('', limit=20, offset=offset)
            ids = {r['id'] for r in page['items']}
            assert not seen & ids, 'Pagination duplicated assets'
            seen |= ids
            offset = page['next_offset']
        assert len(seen) == page['total']
        checks['all_pages_reachable'] = True
        preview_row = next(r for r in online_rows if r.get('thumbnail'))
        result = yield search(preview_row['name'], preview=True)
        assert result.get('views') and preview_row['id'] in result['previews'], result
        for view in result['views']:
            picture = bpy.data.images.load(view['path'], check_existing=False)
            assert min(picture.size) > 0
            bpy.data.images.remove(picture)
        assert not list(cache.rglob('*.blend')), 'Preview downloaded asset payloads'
        checks['online_thumbnail_images_without_payloads'] = True
        selected_files = set()
        for kind in ('model', 'material', 'world'):
            row = min((r for r in online_rows if r['kind'] == kind), key=lambda r: r['file']['size'])
            if kind == 'material':
                obj, handle = observe_artist_cube(cube_expected)
            args = {'objects': [handle]} if kind == 'material' else {}
            result = yield use(row, **args)
            assert result.get('success'), result
            assert result['usage'] == {'model': 'placed_model', 'material': 'applied_material', 'world': 'replaced_world'}[kind]
            report['downloads'][kind] = {'title': row['name'], 'bytes': row['file']['size'], 'usage': result['usage']}
            checks['live_' + kind] = True
            file = Path(row['file']['path'])
            selected_files.add(file)
            assert set(cache.rglob('*.blend')) == selected_files, 'An unselected asset payload was downloaded'
            stamp = file.stat().st_mtime_ns
            if kind == 'material':
                obj, handle = observe_artist_cube(cube_expected)
                args = {'objects': [handle]}
            result = yield use(row, **args)
            assert result.get('success') and file.stat().st_mtime_ns == stamp
        checks['live_cache_reuse'] = True
        prefs.system.use_online_access = False
        result = yield use(row)
        assert result.get('success'), result
        checks['cached_offline_import'] = True
        prefs.asset_libraries.use_online_essentials = False
        try:
            use(row)
        except Exception:
            checks['online_disabled_selection_refused'] = True
        else:
            raise AssertionError('Disabled online selection imported')
    saved = OUT / 'essentials-proof.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(saved))
    (OUT / 'fixture.blend').unlink()
    if ONLINE:
        for path in cache.rglob('*.blend'):
            path.unlink()  # This fixture's isolated cache only, after saving the appended project.
    bpy.ops.wm.open_mainfile(filepath=str(saved), load_ui=False, use_scripts=False)
    yield from essentials_dirty_undo.window_boundary(executor, saved)
    assert bpy.data.objects['Cube'].modifiers[0].node_group is not None
    assert bpy.context.scene.world is not None
    assert bpy.data.materials.get('Fixture material') and bpy.data.brushes.get('Fixture brush')
    assert not bpy.data.libraries
    checks['reopen_without_source_files'] = True
    # All seven single-Undo assertions are finished. Only now enter the distinct saved-copy
    # phase; opening these files cannot supply history to an earlier Undo assertion.
    report['phase'] = 'saved_copy_readback'
    report['generic_seven_mode_dirty_undo'] = yield from essentials_dirty_undo.readback(OUT, executor)
    checks['synthetic_seven_mode_saved_copy_readback'] = True
    report['fixture_phases']['saved_copy_readback'] = True
    report['synthetic_seven_mode_saved_copy_readback'] = 'COMPLETE'
    assert all(report['fixture_phases'].values()), 'Both native diagnostic phases are required'


steps = scenario()
state = {'pending': None, 'send': None}


def tick():
    try:
        assert time.monotonic() - started < 240, 'Native Essentials fixture timed out'
        if state['pending'] is not None:
            value = state['pending'].poll()
            if value is None:
                return .01
            state['send'], state['pending'] = value, None
        state['pending'] = steps.send(state['send'])
        state['send'] = None
        return .01
    except StopIteration:
        report.update(success=True, phase='complete')
    except NativeDependencyUnavailable:
        report.update(phase='blocked_native_owner', error=traceback.format_exc(),
                      blocked_cases=[case[0] for case in essentials_dirty_undo.CASES])
    except Exception:
        report.update(phase='failed', error=traceback.format_exc())
        traceback.print_exc()
    (OUT / 'essentials-import-and-readback.json').write_text(json.dumps(report, indent=2))
    (OUT / 'essentials.json').write_text(json.dumps(report, indent=2))
    print('ESSENTIALS_RESULT', json.dumps(report), flush=True)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(tick, first_interval=1, persistent=True)
