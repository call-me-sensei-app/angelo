# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Real default Director layout + chat-only Studio tools, local API fixtures only."""

import base64
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get('ANGELO_QA_DIR') or Path(tempfile.gettempdir(), 'angelo-director-qa'))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / 'state'), ANGELO_CONNECTIONS_DIR=str(OUT / 'accounts'), ANGELO_LIBRARY_DIR=str(OUT / 'library'))
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
product_env.clear('DISABLE_DEFAULT_CHAT')  # Every spelling the app honours, deprecated ones included.
sys.path.insert(0, str(ROOT / 'scripts/addons_core'))
from angelo_agent import ui, console, studio, director, chat_view
from angelo_agent.api_connections import CheckResult
from angelo_agent.provider_registry import SPECS, GENAI
from angelo_agent.blender_tools import BlenderExecutor

ui._authentication.start = lambda *a, **k: None
ui.refresh_connected_models = lambda *a, **k: None
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
bpy.context.preferences.edit.use_global_undo = True
original_workspace = bpy.context.window.workspace.name
original_layout = [(a.as_pointer(), a.type, a.ui_type) for a in bpy.context.window.screen.areas]
original_objects = [(o.name, tuple(o.location)) for o in bpy.context.scene.objects]
started = time.monotonic()
state = {'phase': 'startup', 'checks': {}, 'success': False, 'fixture': 'Actual native layout and tools. Local fixture transport only; NO live providers, keys, paid generation or microphone.'}
answers, calls = [], []


def window():
    return bpy.context.window_manager.windows[0]


def chat():
    return next(a for a in window().screen.areas if console.is_chat(a))


def capture(name):
    area = chat()
    with bpy.context.temp_override(window=window(), area=area):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(OUT / name))


def request(name, args):
    answers.clear()
    controller = ui.get_controller()
    controller.busy = True
    controller.cancelled = False
    controller.state['options']['approval'] = 'AUTO_BOUNDED'
    controller.request_tool(str(time.monotonic()), name, args, answers.append)
    controller.pump_tools(BlenderExecutor(window()), ui.document_key())
    return next(iter(controller.pending.values()), None)


def approve(call):
    with bpy.context.temp_override(window=window(), area=chat()):
        result = bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=call.id)
    assert result == {'FINISHED'}, result


def transport(url, **kwargs):
    calls.append(url)
    body = json.loads(kwargs.get('body') or b'{}')
    if body.get('model') == 'gemini-3.8-flash':
        return b'{"steps":[{"type":"model_output","content":[{"type":"text","text":"Enhanced fixture prompt: a luminous blue sword"}]}]}'
    data = (ROOT / 'release/branding/angelo/angelo-raven-teal-v2.png').read_bytes()
    return json.dumps({'steps': [{'type': 'model_output', 'content': [
        {'type': 'image', 'data': base64.b64encode(data).decode()}]}]}).encode()


def tick():
    try:
        assert time.monotonic() - started < 65, 'Director acceptance timed out'
        if director.last_error:
            raise AssertionError(director.last_error)
        if state['phase'] == 'startup':
            if not director.is_director(window()) or director._timer is not None:
                return .2
            state['checks']['director_is_default'] = director.preferred_mode() == 'DIRECTOR'
            state['checks']['only_scene_preview_and_chat'] = len(window().screen.areas) == 2 and any(a.type == 'VIEW_3D' for a in window().screen.areas) and len([a for a in window().screen.areas if console.is_chat(a)]) == 1
            preview = next(a for a in window().screen.areas if a.type == 'VIEW_3D').spaces.active
            # Director never takes input: every native viewport event reaches Blender
            # (pinned event by event in test_blender_director_navigation.py).
            state['checks']['director_adds_no_input_handler'] = not any('director' in str(getattr(op, 'bl_idname', '')).lower()
                                                                        for op in window().modal_operators)
            state['checks']['all_manual_chrome_hidden'] = not any([preview.show_region_header, preview.show_region_tool_header, preview.show_region_toolbar, preview.show_region_ui, preview.show_gizmo, window().screen.show_statusbar])
            manual = bpy.data.workspaces[original_workspace]
            original_screen = next(s for s in manual.screens if any(a.as_pointer() == original_layout[0][0] for a in s.areas))
            state['checks']['original_manual_editors_preserved'] = [(a.as_pointer(), a.type, a.ui_type) for a in original_screen.areas] == original_layout
            chat().spaces.active.history[-1].body = 'Keep my unsent direction'
            capture('director-default.png')
            with bpy.context.temp_override(window=window(), area=chat()):
                bpy.ops.angelo.workspace_mode(mode='MANUAL')
            state['phase'] = 'manual'
            return .2
        if state['phase'] == 'manual':
            if director._timer is not None or ui._layout_timer is not None:
                return .2
            state['checks']['show_controls_restores_properties_and_outliner'] = not director.is_director(window()) and {'PROPERTIES', 'OUTLINER'} <= {a.type for a in window().screen.areas}
            state['checks']['manual_preference_persisted'] = director.preferred_mode() == 'MANUAL'
            state['checks']['draft_transferred_to_manual'] = chat().spaces.active.history[-1].body == 'Keep my unsent direction'
            chat().spaces.active.history[-1].body = 'Updated direction in Manual mode'
            capture('manual-controls.png')
            with bpy.context.temp_override(window=window(), area=chat()):
                bpy.ops.angelo.workspace_mode(mode='DIRECTOR')
            state['phase'] = 'studio'
            return .2
        if state['phase'] == 'studio':
            if director._timer is not None:
                return .2
            state['checks']['director_return_keeps_latest_draft'] = chat().spaces.active.history[-1].body == 'Updated direction in Manual mode'
            assert director.is_director(window())
            studio.library()
            studio._jobs.transport = transport
            record = ui.connections().add('GOOGLE', 'API_KEY', 'Director fixture account', group=GENAI)
            ui._memory_keys[record['id']] = 'fixture-key-no-live-network'
            ui._api_connections.results[record['id']] = CheckResult('verified', 'Fixture only', True, SPECS['gemini_images_api']['models'])
            state['account'] = record['id']
            request('angelo_studio_catalog', {})
            state['checks']['catalog_from_chat_no_studio_editor'] = answers and any(m['connection'] == record['id'] and m['ready'] for m in answers[0]['models']) and not any(studio.is_studio(a) for a in window().screen.areas)
            call = request('angelo_studio_generate', {'connection_id': record['id'], 'model': 'gemini-3.1-flash-image', 'prompt': '[Local UI fixture] A luminous blue sword', 'options': {'aspect': '1:1'}})
            state['checks']['auto_mode_does_not_bypass_billing_approval'] = call and call.approval and not calls and not studio._jobs.active
            state['checks']['approval_discloses_exact_account'] = 'Director fixture account' in call.usage_label
            capture('director-chat-billing-approval.png')
            approve(call)
            state['phase'] = 'generation'
            return .2
        if state['phase'] == 'generation':
            ui.get_controller().pump_events()
            if not answers:
                return .1
            asset = answers[0]
            assert asset.get('status') == 'ready', asset
            state['asset'] = asset['id']
            state['checks']['generated_output_delivered_in_chat'] = bool(asset.get('views')) and studio.library().checked_file(studio.library().assets[asset['id']], asset['files'][0]).is_file()
            state['checks']['generation_did_not_change_scene'] = original_objects == [(o.name, tuple(o.location)) for o in window().scene.objects]
            capture('director-chat-generated-preview.png')
            call = request('angelo_studio_enhance', {'prompt': 'A sword', 'kind': 'IMAGE', 'generation_model': 'Nano Banana 2'})
            state['checks']['enhance_requires_usage_approval'] = bool(call and call.approval)
            approve(call)
            state['phase'] = 'enhance'
            return .2
        if state['phase'] == 'enhance':
            ui.get_controller().pump_events()
            if not answers:
                return .1
            state['checks']['enhancement_original_preserved_and_returned'] = answers[0].get('original_prompt') == 'A sword' and answers[0].get('enhanced_prompt') == 'Enhanced fixture prompt: a luminous blue sword' and not answers[0]['applied']
            call = request('angelo_studio_create_folder', {'name': 'Directed props'})
            approve(call)
            folder = answers[0]['folder']['id']
            call = request('angelo_studio_move_asset', {'asset_id': state['asset'], 'folder': folder})
            approve(call)
            request('angelo_studio_list', {'folder': folder})
            state['checks']['library_organization_in_chat'] = answers[0]['total'] == 1 and answers[0]['assets'][0]['id'] == state['asset']
            call = request('angelo_studio_export', {'asset_id': state['asset'], 'path': 'exports/director-fixture.png'})
            approve(call)
            # Large saved outputs must still deliver bounded pixels without
            # opening Studio or changing the full-size source. Local noise PNG.
            import struct, zlib
            edge = 1536
            def chunk(kind, body):
                return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body))
            png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', edge, edge, 8, 6, 0, 0, 0))
            png += chunk(b'IDAT', zlib.compress(b''.join(b'\0' + os.urandom(edge*4) for _ in range(edge)))) + chunk(b'IEND', b'')
            large = studio.library().add('IMAGE', '[Local large-preview fixture]', 'Fixture', status='ready')
            source = studio.library().result_directory(large['id']) / 'large.png'
            source.write_bytes(png)
            large.update(files=['large.png'], thumbnail='large.png', progress=100)
            studio.library().update(large)
            request('angelo_studio_asset', {'asset_id': large['id']})
            state['checks']['large_output_gets_bounded_chat_preview'] = len(png) > ui.get_controller().references.image_limit and bool(answers[0].get('views')) and answers[0]['views'][0]['bytes'] <= ui.get_controller().references.image_limit
            state['checks']['large_output_preserved_at_original_size'] = source.read_bytes() == png
            state['checks']['export_in_chat_no_file_picker'] = (ui.get_controller().files.root / 'exports/director-fixture.png').is_file()
            state['checks']['never_opened_media_studio'] = studio.draw_count == 0 and not any(studio.is_studio(a) for a in window().screen.areas)
            state['checks']['exactly_two_fixture_requests_no_live_usage'] = len(calls) == 2
            # Create a self-contained local GLB from this scratch scene, then
            # test the exact approved import tool and native Undo. No provider.
            model = studio.library().add('3D', '[Local import fixture]', 'Native GLB', status='ready')
            path = studio.library().result_directory(model['id']) / 'model.glb'
            viewport = next(a for a in window().screen.areas if a.type == 'VIEW_3D')
            with bpy.context.temp_override(window=window(), area=viewport):
                result = bpy.ops.export_scene.gltf(filepath=str(path), export_format='GLB', use_selection=False, use_active_scene=True)
            assert result == {'FINISHED'}
            model.update(files=['model.glb'], progress=100)
            studio.library().update(model)
            before = {o.name for o in window().scene.objects}
            # Establish the pre-import state in this isolated script's native
            # Undo history; addon import has its own UNDO operator boundary.
            with bpy.context.temp_override(window=window(), area=viewport):
                bpy.ops.ed.undo_push(message='Before Director GLB fixture import')
            call = request('angelo_studio_import_3d', {'asset_id': model['id']})
            state['checks']['3d_import_has_separate_approval'] = bool(call and call.approval)
            approve(call)
            state['checks']['3d_import_actual_readback'] = bool(answers[0].get('objects')) and len(window().scene.objects) > len(before)
            with bpy.context.temp_override(window=window(), area=viewport):
                bpy.ops.ed.undo()
            state['checks']['3d_import_native_undo'] = {o.name for o in window().scene.objects} == before
            capture('director-preview-after-undo.png')
            state['checks']['native_chat_draw_is_healthy'] = chat_view.draw_count > 0 and chat_view.last_error is None
            ui.get_controller().busy = False
            state['phase'] = 'complete'
            state['success'] = all(state['checks'].values())
            (OUT / 'director-qa.json').write_text(json.dumps(state, indent=2))
            print('ANGELO_DIRECTOR_QA', json.dumps(state), flush=True)
            bpy.ops.wm.quit_blender()
            return None
    except Exception as error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
        (OUT / 'director-qa.json').write_text(json.dumps(state, indent=2))
        print(state['traceback'], flush=True)
        bpy.ops.wm.quit_blender()
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.3)
