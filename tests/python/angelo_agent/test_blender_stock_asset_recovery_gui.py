# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Real context refusal and explicit production recovery UI.

GUI factory startup, isolated state, no providers/network. A real blend_data=None
context mismatch refuses BEFORE decode, leaving an actually recoverable native
owner. No native fault hooks or mocked recovery facts. Screenshots require visual
review; operator invocation is not a physical mouse click.
"""
import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
from pathlib import Path
import shutil
import time
import traceback

import bpy
from _bpy import _angelo_undo as api

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts/addons_core'))
from angelo_agent import blender_asset_commit as commit, library_ui  # noqa: E402
import qa_boot  # noqa: E402

assert '--factory-startup' in sys.argv and not bpy.app.background
OUT = Path(os.environ['ANGELO_QA_DIR'])
OUT.mkdir(parents=True, exist_ok=True)
OUT = OUT.resolve(strict=True)
REPORT = {'success': False, 'phase': 'setup', 'checks': {}, 'screenshots': [],
          'visual_review_required': True, 'physical_mouse_dispatch': False,
          'nonrecoverable_failure_coverage': False, 'binary': bpy.app.binary_path,
          'version': bpy.app.version_string}
STATE = {'phase': 'prepare', 'scope': None, 'before': None, 'ticks': 0,
         'deadline': time.monotonic() + 150, 'recover_dispatches': 0,
         'pending_unknown': False, 'pending_observation': 'not_queried'}
HELD_SCOPES = []


def check(name, value):
    REPORT['checks'][name] = bool(value)
    if not value:
        raise AssertionError(name)


def write_report():
    (OUT / 'stock-asset-recovery-gui.json').write_text(json.dumps(REPORT, indent=2))


def parts():
    window = bpy.context.window or bpy.context.window_manager.windows[0]   # No context window in a timer.
    area = next(area for area in window.screen.areas if area.type == 'VIEW_3D')
    region = next(region for region in area.regions if region.type == 'WINDOW')
    return dict(window=window, area=area, region=region)


def plain(value):
    if hasattr(value, 'to_dict'):
        return {key: plain(item) for key, item in value.to_dict().items()}
    if hasattr(value, 'to_list'):
        return value.to_list()
    return value


def projection():
    obj = bpy.data.objects['RecoveryDirty']
    image = bpy.data.images['RecoveryPixels']
    return {'ids': sorted((item.id_type, item.name, item.session_uid) for item in bpy.data.all_ids),
            'vertices': [list(vertex.co) for vertex in obj.data.vertices],
            'properties': plain(obj['artist']), 'pixels': list(image.pixels[:]),
            'selection': sorted(item.name for item in bpy.context.selected_objects),
            'active': bpy.context.view_layer.objects.active.name,
            'frame': bpy.context.scene.frame_current,
            'camera': list(bpy.data.objects['RecoveryCamera'].location),
            'light': bpy.data.lights['RecoveryLightData'].energy,
            'objects': sorted(item.name for item in bpy.context.scene.objects),
            'edges': {item.name: (sorted(obj.name for obj in item.objects),
                                  sorted(child.name for child in item.children))
                      for item in bpy.data.collections}}


def prepare_files_and_document():
    # Factory reads precede timer registration: no timer is silently removed by setup.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    mesh = bpy.data.meshes.new('RecoveryAssetMesh')
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    obj = bpy.data.objects.new('RecoveryAsset', mesh)
    obj.asset_mark()
    original = OUT / 'original.blend'
    private = OUT / 'private.blend'
    bpy.data.libraries.write(str(original), {obj}, fake_user=True)
    shutil.copyfile(original, private)
    STATE['private'] = private
    STATE['paths'] = ((str(original.resolve()), str(private.resolve())),)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.preferences.edit.use_global_undo = True
    bpy.context.preferences.edit.undo_steps = 32
    bpy.context.preferences.view.use_translate_interface = False
    window = bpy.context.window or bpy.context.window_manager.windows[0]   # A timer after the reset has no context window.
    area = max(window.screen.areas, key=lambda item: item.width * item.height)
    area.type = 'VIEW_3D'
    area.spaces.active.show_region_ui = True
    for cls in (library_ui.ANGELO_OT_library_recover, library_ui.ANGELO_PT_asset_recovery):
        if getattr(bpy.types, cls.__name__, None) is None:
            bpy.utils.register_class(cls)
    mesh = bpy.data.meshes.new('RecoveryDirtyMesh')
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    obj = bpy.data.objects.new('RecoveryDirty', mesh)
    bpy.context.scene.collection.objects.link(obj)
    camera = bpy.data.objects.new('RecoveryCamera', bpy.data.cameras.new('RecoveryCameraData'))
    light = bpy.data.objects.new('RecoveryLight', bpy.data.lights.new('RecoveryLightData', 'POINT'))
    bpy.context.scene.collection.objects.link(camera)
    bpy.context.scene.collection.objects.link(light)
    image = bpy.data.images.new('RecoveryPixels', width=2, height=2)
    image.use_fake_user = True
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    with bpy.context.temp_override(**parts()):
        check('older_history_created', bpy.ops.ed.undo_push(message='Earlier artist state') == {'FINISHED'})
    # All of these are unrecorded edits, made after that older history boundary.
    mesh.vertices[0].co = (9, 7, 5)
    obj['artist'] = {'nested': {'value': 47}, 'array': [2, 3, 5]}
    image.pixels[:] = [0.25, 0.5, 0.75, 1.0] * 4
    image.pack()
    camera.select_set(True)
    bpy.context.view_layer.objects.active = camera
    camera.location = (3, 5, 7)
    light.data.energy = 137
    bpy.context.scene.frame_set(29)
    bpy.context.view_layer.update()


def retain_scope(scope):
    STATE['scope'] = scope
    if not any(held is scope for held in HELD_SCOPES):
        HELD_SCOPES.append(scope)


def observe_pending():
    """Read/adopt native lifetime only; a failed query must never become absence."""
    try:
        pending = api.asset_pending()
    except BaseException as error:
        STATE['pending_unknown'] = True
        STATE['pending_observation'] = 'unknown'
        STATE['pending_query_error'] = type(error).__name__
        return None
    STATE['pending_unknown'] = False
    STATE['pending_observation'] = 'present' if pending is not None else 'absent'
    if pending is not None:
        retain_scope(pending)
    return pending


def begin_actual_scope():
    try:
        scope = api.asset_begin(source_paths=STATE['paths'])
    except BaseException:
        observe_pending()  # Retain a real lost-return owner, or preserve unknown state.
        raise
    if scope is None:
        observe_pending()
        raise RuntimeError('Native asset begin returned no scope')
    retain_scope(scope)  # Publish the returned owner before any later fixture assertion.
    return scope


def append_actual_object():
    before_libraries = {item.as_pointer() for item in bpy.data.libraries}
    with bpy.data.libraries.load(str(STATE['private']), assets_only=True,
                                 link=False, recursive=True) as (available, requested):
        check('source_object_exists', 'RecoveryAsset' in available.objects)
        requested.objects = ['RecoveryAsset']
    obj = requested.objects[0]
    check('actual_new_object', obj is not None and obj.name not in STATE['before']['objects'])
    bpy.context.scene.collection.objects.link(obj)
    obj.location = (11, 13, 17)
    for library in tuple(bpy.data.libraries):
        if (library.as_pointer() not in before_libraries
                and Path(library.filepath).resolve() == STATE['private'].resolve()
                and not any(item.library == library for item in bpy.data.all_ids)):
            bpy.data.libraries.remove(library)
    bpy.context.view_layer.update()


def show_sidebar():
    area = parts()['area']
    area.spaces.active.show_region_ui = True
    area.tag_redraw()
    region = next((item for item in area.regions if item.type == 'UI'), None)
    if region is None or region.width < 80:
        return False
    try:
        region.active_panel_category = 'Angelo'
    except (TypeError, ValueError):
        return False  # Categories are populated by the actual native draw pass.
    return region.active_panel_category == 'Angelo'


def panel_draw_ready():
    if not show_sidebar():
        STATE['draw_ticks'] = 0
        return False
    STATE['draw_ticks'] += 1
    return STATE['draw_ticks'] >= 3


def screenshot(name):
    path = OUT / name
    with bpy.context.temp_override(**parts()):
        check(name + '_capture', bpy.ops.screen.screenshot(filepath=str(path)) == {'FINISHED'})
    check(name + '_png', path.is_file() and path.stat().st_size > 64
          and path.read_bytes()[:8] == b'\x89PNG\r\n\x1a\n')
    REPORT['screenshots'].append(str(path))


def next_phase(name):
    STATE['phase'], STATE['ticks'], STATE['draw_ticks'] = name, 0, 0


def tick():
    try:
        if time.monotonic() > STATE['deadline']:
            raise TimeoutError('Recovery GUI fixture exceeded its 150-second logic bound')
        STATE['ticks'] += 1
        phase = STATE['phase']
        if phase == 'prepare':
            if STATE['ticks'] < 3:
                return 0.2
            with bpy.context.temp_override(**parts()):
                check('no_initial_pending', api.asset_pending() is None)
                check('no_initial_panel', not library_ui.ANGELO_PT_asset_recovery.poll(bpy.context))
                STATE['before'] = projection()
                scope = begin_actual_scope()
                append_actual_object()
                with bpy.context.temp_override(blend_data=None):
                    refused = api.asset_finish(scope, keep=True)
                check('actual_recovery_required', refused['state'] == 'RECOVERY_REQUIRED')
                check('prepublication_recoverable', refused['published'] is False
                      and refused['restored'] is False and refused['recoverable'] is True
                      and refused['recovery_required'] is True and refused['closed'] is False)
                check('stored_refusal_no_replay', api.asset_status(scope) == refused)
                check('native_owner_retained', api.asset_pending() is scope)
                check('pending_scene_still_changed', projection() != STATE['before'])
                check('panel_visible_poll', library_ui.ANGELO_PT_asset_recovery.poll(bpy.context))
                check('recover_action_enabled', bpy.ops.angelo.library_recover.poll())
            next_phase('recoverable_draw')
        elif phase == 'recoverable_draw':
            if not panel_draw_ready():
                return 0.2
            screenshot('recoverable-sidebar.png')
            with bpy.context.temp_override(**parts()):
                STATE['recover_dispatches'] += 1
                check('explicit_recover_operator', bpy.ops.angelo.library_recover('EXEC_DEFAULT') == {'FINISHED'})
                restored = api.asset_status(STATE['scope'])
                check('actual_restored_closed', restored['state'] == 'RESTORED'
                      and restored['published'] is False and restored['restored'] is True
                      and restored['recovery_required'] is False and restored['closed'] is True)
                check('exact_dirty_preimage', projection() == STATE['before'])
                check('recovery_owner_closed', api.asset_pending() is None and commit.recovery_status() is None)
                check('recovery_panel_hidden', not library_ui.ANGELO_PT_asset_recovery.poll(bpy.context))
            HELD_SCOPES.remove(STATE['scope'])
            next_phase('restored_draw')
        elif phase == 'restored_draw':
            parts()['area'].tag_redraw()
            if STATE['ticks'] < 4:
                return 0.2
            screenshot('restored-sidebar.png')
            with bpy.context.temp_override(**parts()):
                scope = begin_actual_scope()
                append_actual_object()
                committed = api.asset_finish(scope, keep=True)
                check('known_committed_not_closed', committed['state'] == 'COMMITTED'
                      and committed['published'] is True and committed['restored'] is False
                      and committed['recovery_required'] is False and committed['recoverable'] is False
                      and committed['closed'] is False)
                check('settled_panel_visible', library_ui.ANGELO_PT_asset_recovery.poll(bpy.context))
                check('settled_action_unavailable', not bpy.ops.angelo.library_recover.poll())
                STATE['committed'] = committed
            next_phase('unavailable_draw')
        elif phase == 'unavailable_draw':
            if not panel_draw_ready():
                return 0.2
            screenshot('committed-unavailable-sidebar.png')
            with bpy.context.temp_override(**parts()):
                check('unavailable_poll_stays_false', not bpy.ops.angelo.library_recover.poll())
                check('unavailable_status_unchanged', api.asset_status(STATE['scope']) == STATE['committed'])
                # Actual discovered owner closure, not a test-only module-state reset.
                commit._discover_pending().close()
                check('committed_close_known', api.asset_status(STATE['scope'])['closed'] is True)
                check('no_final_pending', api.asset_pending() is None and commit.recovery_status() is None)
                check('one_undo_after_known_commit', bpy.ops.ed.undo() == {'FINISHED'})
                check('final_dirty_preimage', projection() == STATE['before'])
            HELD_SCOPES.remove(STATE['scope'])
            check('exactly_one_recovery_dispatch', STATE['recover_dispatches'] == 1)
            check('no_held_scope', not HELD_SCOPES)
            REPORT.update(success=True, phase='complete', recover_operator_invocations=1)
            write_report()
            bpy.ops.wm.quit_blender()
            return None
        return 0.2
    except BaseException:
        error_trace = traceback.format_exc()
        pending = observe_pending()
        REPORT.update(success=False, phase=STATE['phase'], error=error_trace,
                      pending_owner_retained=None if STATE['pending_unknown'] else pending is not None,
                      pending_owner_observation=STATE['pending_observation'],
                      held_scope_count=len(HELD_SCOPES))
        if STATE['pending_unknown']:
            REPORT['pending_query_error'] = STATE.get('pending_query_error')
        write_report()
        print(error_trace, file=sys.stderr, flush=True)
        # An unresolved or unobservable owner is not forcibly closed or discharged.
        # A supervisor timeout is a failure, never a recovery/cleanup witness.
        if not HELD_SCOPES and not STATE['pending_unknown'] and pending is None:
            bpy.ops.wm.quit_blender()
        return None


prepare_files_and_document()
# Persistent only for ordinary GUI event delivery; all factory reads already completed.
bpy.app.timers.register(qa_boot.gated(tick), first_interval=0.2, persistent=True)
