# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Two native launches prove automatic saved-project brief continuity.

Run outside Blender: python3 this_file.py --binary /absolute/Angelo
--out <short temp root>/fresh-directory (``native_suite_paths.short_temp_root()``:
``/private/tmp`` on macOS, the temp folder elsewhere). Positive seed/resume use
the SAME saved .blend and profile. Fresh negative launches test changed bytes
and equal bytes at a different path. Only external auth/provider/catalog
surfaces are stubbed.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import native_suite_paths  # noqa: E402  (standard library only)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_host():
    import subprocess
    import gui_launch
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    root = native_suite_paths.fresh_fixture_root(args.out)
    binary = Path(args.binary).resolve(strict=True)
    root.mkdir(mode=0o700)
    reports = []
    def phase(name, project=None):
        log = root / (name + '.log')
        command = [str(binary), '--factory-startup', '--disable-autoexec',
            *gui_launch.window_flags(), '--window-geometry', '80', '80', '1400', '900']
        if project:
            command.append(str(project))
        command += ['--python', str(Path(__file__).resolve()), '--', '--native-phase', name, '--fixture-root', str(root)]
        with log.open('w') as output:
            result = subprocess.run(command, env=dict(os.environ, ANGELO_DISABLE_DEFAULT_CHAT='1'),
                stdout=output, stderr=subprocess.STDOUT, timeout=75)
        text = log.read_text(errors='replace')
        data = json.loads((root / (name + '.json')).read_text())
        data['exit_code'] = result.returncode
        data['fatal_log_markers'] = [value for value in ('BLI_assert failed', 'Segmentation fault', 'Traceback') if value in text]
        data['allocator_warning'] = next((line for line in text.splitlines() if 'Not freed memory blocks' in line), None)
        data['success'] = data['success'] and result.returncode == 0 and not data['fatal_log_markers'] and not data['allocator_warning']
        reports.append(data)
        if not data['success']:
            raise AssertionError('Native phase failed: ' + name + ' ' + str(data.get('error')))
    try:
        phase('seed')
        original = root / 'resume-project.blend'
        shutil.copy2(original, root / 'seed-immutable.blend')
        shutil.copy2(original, root / 'different-project.blend')
        phase('resume', original)
        shutil.copy2(root / 'changed-project.blend', original)
        phase('changed', original)
        shutil.copy2(root / 'seed-immutable.blend', original)
        phase('different', root / 'different-project.blend')
    finally:
        report = {'success': len(reports) == 4 and all(item['success'] for item in reports), 'phases': reports,
            'two_process_positive': len(reports) >= 2 and all(item['success'] for item in reports[:2]),
            'changed_and_different_require_confirmation': len(reports) == 4 and all(item['success'] for item in reports[2:]),
            'provider_starts': 0, 'paid_requests': 0, 'binary': str(binary)}
        (root / 'memory-project-resume.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))


if '--native-phase' not in sys.argv:
    if __name__ == '__main__':
        run_host()
    raise SystemExit


import traceback
import addon_utils
import bpy

assert '--factory-startup' in sys.argv and '--enable-event-simulate' in sys.argv and not bpy.app.background
parser = argparse.ArgumentParser()
parser.add_argument('--native-phase', required=True, choices=('seed', 'resume', 'changed', 'different'))
parser.add_argument('--fixture-root', required=True)
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
OUT, PHASE = Path(args.fixture_root).resolve(), args.native_phase
os.environ.update(ANGELO_STATE_DIR=str(OUT / 'profile'), ANGELO_CONNECTIONS_DIR=str(OUT / 'accounts'), ANGELO_DISABLE_DEFAULT_CHAT='1')
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
from angelo_agent import ui, console, chat_view, agent_workspace_ui, memory_project_ui
from angelo_agent.catalogs import ModelCatalogs
from angelo_agent.controller import Controller
from angelo_agent.blender_tools import BlenderExecutor
from angelo_agent.task_memory import read_memory, on_verified_result

# ONLY external discovery surfaces are stubbed. UI, persistence and receipts
# are production paths, including the real native main-thread pump and worker.
ui._authentication.start = lambda *args, **kwargs: None
type(ui._authentication).start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
Controller.refresh_models = lambda *args, **kwargs: None
ModelCatalogs.refresh = lambda *args, **kwargs: None
addon_utils.enable('angelo_agent', default_set=False, persistent=False)
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.filepaths.file_preview_type = 'NONE'
(OUT / ('runtime-temp-' + PHASE)).mkdir(exist_ok=True)
bpy.context.preferences.filepaths.temporary_directory = str(OUT / ('runtime-temp-' + PHASE))
report = {'phase': PHASE, 'checks': {}, 'success': False, 'paid_requests': 0, 'binary': bpy.app.binary_path}
state = {'step': 'boot', 'started': time.monotonic(), 'done': False}
window = bpy.context.window_manager.windows[0]
controller = None
OBJECTIVE = 'Resume this exact public project brief without granting edit authority.'


def check(name, condition):
    report['checks'][name] = bool(condition)
    assert condition, name


def parts():
    area = next(area for area in window.screen.areas if console.is_chat(area))
    return {'window': window, 'area': area, 'region': next(region for region in area.regions if region.type == 'WINDOW')}


def screenshot(name):
    with bpy.context.temp_override(**parts()):
        parts()['area'].tag_redraw()
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(OUT / name))
    check('native_chat_draw_has_no_errors', chat_view.last_error is None)


def track_dialog(name):
    cls = next(cls for cls in agent_workspace_ui._classes if cls.__name__ == name)
    original = cls.invoke
    def invoke(operator, context, event):
        state['dialog'] = operator
        return original(operator, context, event)
    cls.invoke = invoke


def close_dialog():
    window.event_simulate(type='RET', value='PRESS')
    window.event_simulate(type='RET', value='RELEASE')


def finish(error=None):
    state['done'] = True
    report.update(success=error is None, error=error, step=state['step'], native_document=ui.document_key(),
        provider_started=any(item.provider is not None for item in ui._controllers.values()))
    if controller is not None:
        report.update(session_id=controller.state['id'], memory_scope=controller.state['task_memory']['scope'])
    (OUT / (PHASE + '.json')).write_text(json.dumps(report, indent=2))
    print('ANGELO_MEMORY_PROJECT_RESUME', json.dumps(report), flush=True)
    addon_utils.disable('angelo_agent', default_set=False)
    bpy.ops.wm.quit_blender()
    return None


def tick():
    global controller
    if state['done']:
        return None
    try:
        if time.monotonic() - state['started'] > 55:
            raise RuntimeError('Saved-project continuity fixture timed out: ' + state['step'])
        if state['step'] == 'boot':
            if PHASE == 'seed':
                bpy.data.objects['Cube'].location.x = 2
                bpy.ops.wm.save_as_mainfile(filepath=str(OUT / 'changed-project.blend'), check_existing=False)
                bpy.data.objects['Cube'].location.x = 1
                bpy.ops.wm.save_as_mainfile(filepath=str(OUT / 'resume-project.blend'), check_existing=False)
                bpy.ops.angelo.open_chat(side='RIGHT')
            else:
                check('actual_saved_project_loaded_before_controller_creation', Path(bpy.data.filepath).resolve() ==
                    (OUT / ('different-project.blend' if PHASE == 'different' else 'resume-project.blend')))
                if not any(console.is_chat(area) for area in window.screen.areas):
                    bpy.ops.angelo.open_chat(side='RIGHT')
            state['step'] = 'layout'
            return .5
        if ui._layout_timer is not None:
            return .05
        if state['step'] == 'layout':
            controller = ui.get_controller()
            if PHASE == 'seed':
                with bpy.context.temp_override(**parts()):
                    bpy.ops.angelo.set_approval(mode='READ_ONLY')
                    track_dialog('ANGELO_OT_project_brief')
                    bpy.ops.angelo.project_brief('INVOKE_DEFAULT')
                state['step'] = 'edit_brief'
                return .3
            seed = json.loads((OUT / 'seed.json').read_text())
            check('same_temporary_profile_and_conversation_resumed', controller.state['id'] == seed['session_id'])
            check('new_native_process_has_new_document_serial', ui.document_key()[0] != seed['native_document'][0])
            check('persisted_notes_exist_before_automatic_worker_bind', controller.state['task_memory']['brief']['objective'] == OBJECTIVE)
            state['step'] = 'await_receipt'
        elif state['step'] == 'edit_brief':
            state['dialog'].objective = OBJECTIVE
            state['dialog'].constraints = 'Retain exact scale\nDo not auto-approve any scene change'
            state['dialog'].planned_steps = 'Reobserve native state\nReview every edit'
            close_dialog()
            state['step'] = 'save_project'
            return .3
        elif state['step'] == 'save_project':
            check('real_public_brief_editor_persisted_goal', controller.state['task_memory']['brief']['objective'] == OBJECTIVE)
            inspection = BlenderExecutor(window).execute('angelo_inspect_scene', {})
            controller.state = on_verified_result(controller.state, controller.document, call_id='fixture/native-observation',
                tool='angelo_inspect_scene', result=inspection, object_handles=[item['handle'] for item in inspection['objects']],
                profile_id=str(controller.store.directory.resolve()))
            entry = controller.state['task_memory']['verified_results'][-1]
            check('real_native_readback_has_fresh_verified_evidence_before_restart', entry['status'] == 'verified'
                and not entry['stale'] and bool(entry['object_handles']) and not controller.state['task_memory']['stale'])
            controller.save()
            bpy.ops.wm.save_as_mainfile(filepath=str(OUT / 'resume-project.blend'), check_existing=False)
            state['step'] = 'await_receipt'
        elif state['step'] == 'await_receipt':
            busy = memory_project_ui._pending or memory_project_ui._ready.qsize() or (
                memory_project_ui._worker is not None and memory_project_ui._worker.is_alive())
            if busy:
                return .05
            memory = read_memory(controller.state, controller.document)
            if PHASE in {'seed', 'resume'}:
                if memory['scope_mismatch'] or not memory['scope'].get('project_revision'):
                    return .05
                check('automatic_real_ui_worker_bound_saved_project', memory['brief']['objective'] == OBJECTIVE and
                    memory['scope']['project_revision'].startswith('sha256:'))
                check('actual_saved_revision_matches_project_bytes', memory['scope']['project_revision'] == 'sha256:' + digest(Path(bpy.data.filepath)))
                check('all_edited_public_constraints_and_plans_survive_saved_continuity', memory['brief']['constraints'] ==
                    ['Retain exact scale', 'Do not auto-approve any scene change'] and memory['planned_steps'] ==
                    ['Reobserve native state', 'Review every edit'])
                if PHASE == 'resume':
                    old = next(entry for entry in memory['verified_results'] if entry['id'] == 'fixture/native-observation')
                    check('old_scene_evidence_stays_stale_after_new_process', memory['stale'] and old['status'] == 'verified'
                        and old['stale'] and not old['object_handles'] and old['document'] != memory['scope']['document'])
                    check('automatic_resume_has_no_human_confirmation_intent', not any(message.get('request_kind') == 'task_memory_rebind'
                        for message in controller.state['messages']))
                check('read_only_policy_and_no_scene_edit_authority_invented', controller.state['options']['approval'] == 'READ_ONLY'
                    and not controller.busy and not controller.pending and not controller.native_jobs and bpy.data.objects['Cube'].location.x == 1)
                controller.save()
                ui._sessions.update(controller.state)
                ui._sessions.save()
                track_dialog('ANGELO_OT_project_brief')
                with bpy.context.temp_override(**parts()):
                    bpy.ops.angelo.project_brief('INVOKE_DEFAULT')
                state['step'] = 'verified_dialog'
                return .3
            check('changed_or_different_project_does_not_auto_bind_public_notes', memory['scope_mismatch'] and not memory['brief']['objective'])
            check('original_notes_remain_preserved_but_isolated', controller.state['task_memory']['brief']['objective'] == OBJECTIVE)
            check('read_only_policy_unchanged_for_unverified_scope', controller.state['options']['approval'] == 'READ_ONLY' and not controller.busy)
            track_dialog('ANGELO_OT_use_project_brief')
            with bpy.context.temp_override(**parts()):
                bpy.ops.angelo.project_brief('INVOKE_DEFAULT')
            state['step'] = 'explicit_dialog'
            return .3
        elif state['step'] == 'verified_dialog':
            check('native_brief_viewer_reads_automatically_resumed_goal', state['dialog'].objective == OBJECTIVE)
            screenshot(PHASE + '-automatic-brief.png')
            close_dialog()
            state['step'] = 'verified_finish'
            return .3
        elif state['step'] == 'verified_finish':
            report['project_hash'] = digest(Path(bpy.data.filepath))
            return finish()
        elif state['step'] == 'explicit_dialog':
            check('real_unchecked_confirmation_is_required_for_unverified_project', not state['dialog'].review_confirmed)
            check('exact_prior_public_notes_are_visible_in_confirmation', OBJECTIVE in '\n'.join(line.body for line in state['dialog'].review_lines))
            screenshot(PHASE + '-requires-explicit-confirmation.png')
            close_dialog()
            state['step'] = 'explicit_finish'
            return .3
        elif state['step'] == 'explicit_finish':
            check('unchecked_confirmation_never_rebinds_different_or_changed_file', read_memory(controller.state, controller.document)['scope_mismatch'])
            return finish()
        return .05
    except Exception:
        return finish(traceback.format_exc())


bpy.app.timers.register(tick, first_interval=.3, persistent=True)
