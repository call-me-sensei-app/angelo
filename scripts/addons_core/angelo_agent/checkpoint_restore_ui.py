# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Human snapshot recovery: durable safety link precedes irreversible opening.

The injected UI host owns Blender/main-thread calls. Controller owns authority
revocation and memory rebinding; CheckpointStore owns validated file journals.
After a real scene load, independent failures are partial recovery, not no-ops.
"""

from pathlib import Path

from . import checkpoint_images, ui_text
from .checkpoint_review_ui import checkpoint_review, mark_scene_history as _mark_scene_history, restore_outcome


def _error(controller, error):
    return controller.redact(str(error))


def _safety_notice(controller, selected, safety, unsaved):
    notice = {'role': 'system', **ui_text.notice('Snapshot recovery is starting. Its final outcome is not yet recorded. '
        'Inspect the current scene if recovery is interrupted; Undo restore opens the protected safety snapshot.'),
        'restore_checkpoint': safety['id'], 'restore_source_checkpoint': selected, 'restore_state': 'pending'}
    if unsaved:  # Their pixel edits are not in the safety snapshot: an Undo restore says so (checkpoint_images).
        notice['unsaved_images'] = list(unsaved)
    with controller.lock:
        controller.state['messages'].append(notice)
        controller.version += 1
        try:
            controller.save()  # Strict pre-open gate; never defer to an event queue.
        except Exception:
            notice.update(restore_state='not_applied', **ui_text.notice('The recovery scene was not opened because its safety link '
                'could not be saved. The protected safety snapshot is still available through Undo restore.'))
            raise
    return notice


class _SafetyReloaded(Exception):
    """Native Restore failed after replacing the document, and the exact safety state was reloaded.

    ``suspended``: the add-on modules whose load handlers were suspended for that reload, if any.
    """

    def __init__(self, message, suspended=()):
        super().__init__(message)
        self.suspended = tuple(suspended)


def _quiesce_jobs():
    """Stop window-manager jobs (icon and material previews, renders) GIL-safely before the safety
    capture: a preview written into Main afterwards would make the document differ from its safety
    capture, and reading the file stops every job anyway, as File > Open does."""
    try:
        import _bpy
    except ImportError:
        return
    quiesce = getattr(getattr(_bpy, '_angelo_checkpoint', None), 'quiesce_jobs', None)
    if callable(quiesce):
        quiesce()


def _defining_file(callback):
    """The file of the code a handler runs (its code object's, not its writable ``__module__``), or None."""
    target = getattr(callback, 'func', callback)  # functools.partial
    target = getattr(target, '__func__', target)  # A bound method.
    code = getattr(target, '__code__', None) or getattr(getattr(type(target), '__call__', None), '__code__', None)
    return getattr(code, 'co_filename', None) or None


def _bundled(ui, callback):
    """Whether a handler's code lives in Blender's own scripts folder or in Angelo's package folder.

    Ownership is decided by the file of the handler's code object, never by its ``__module__``, which
    any add-on can set: a handler without Python code, or defined anywhere else, is an add-on's.
    """
    path = _defining_file(callback)
    if not path:
        return False
    roots = [Path(__file__).resolve().parent]
    system = getattr(getattr(ui.bpy, 'utils', None), 'system_resource', None)
    try:
        if callable(system):
            roots.append(Path(system('SCRIPTS')).resolve())
        defined = Path(path).resolve()
    except (OSError, ValueError, TypeError):
        return False
    return any(defined.is_relative_to(root) for root in roots)


def _suspend_load_handlers(ui):
    """Take add-on load handlers (not Blender's bundled ones, not Angelo's) out for one reload."""
    saved = []
    for name in ('load_pre', 'load_post'):
        callbacks = getattr(ui.bpy.app.handlers, name)
        removed = [callback for callback in list(callbacks) if not _bundled(ui, callback)]
        for callback in removed:
            callbacks.remove(callback)
        saved.append((callbacks, removed))
    return saved


def _resume_load_handlers(saved):
    """Put suspended persistent handlers back, as a file load keeps persistent ones."""
    for callbacks, removed in saved:
        for callback in removed:
            if hasattr(callback, '_bpy_persistent') and callback not in callbacks:
                callbacks.append(callback)


def _handler_owners(saved):
    """The add-on names of suspended handlers, for the notice (data, never English prose).

    A handler's ``__module__`` names its add-on only when that module's file is the handler's own code
    file (an extension's ``bl_ext.<repository>.<name>`` gives ``<name>``); otherwise, as for a script,
    the code file's name stands in.
    """
    import sys
    names = []
    for _, removed in saved:
        for callback in removed:
            module, path = str(getattr(callback, '__module__', '') or ''), _defining_file(callback)
            loaded = getattr(sys.modules.get(module), '__file__', None) if module not in ('', '__main__') else None
            try:
                trusted = bool(loaded and path) and Path(loaded).resolve() == Path(path).resolve()
            except (OSError, ValueError, TypeError):
                trusted = False
            parts = module.split('.')
            name = (parts[2] if parts[0] == 'bl_ext' and len(parts) > 2 else parts[0]) if trusted else Path(path or '?').name
            if name not in names:
                names.append(name)
    return names


def _reload_safety(ui, controller, native, safety, warnings):
    """Reopen the protected pre-Restore document after a Restore failed past replacement.

    Native code admits this only for the safety capture of the Restore that just failed, into
    the failed document it left. Returns True once that exact state is open again.
    """
    from .checkpoint_recovery import CheckpointRecovery
    owner = prepared = None
    try:
        record = controller.checkpoints.load(safety['id'])
        with ui.bpy.context.temp_override(window=ui.main_window()):
            if record.get('version') == 4:
                owner = CheckpointRecovery.prepare(controller.checkpoints, safety['id'])
                prepared = native.prepare_restore(owner)
                reloaded = native.reload_failed_restore(prepared) is True
                record = owner.record
            else:  # Nothing was in Sculpt before Restore: its ordinary private copy is the document.
                record, recovery = controller.checkpoints.recovery_copy(safety['id'])
                reloaded = 'FINISHED' in ui.bpy.ops.wm.open_mainfile(filepath=str(recovery), load_ui=False,
                                                                     use_scripts=False)
        if reloaded:
            ui._recovery_original_path = record['original_path']
        return reloaded
    except Exception as error:
        warnings.append('Reloading the safety snapshot failed: ' + _error(controller, error))  # i18n: stored diagnostic
        return False
    finally:
        try:
            if prepared is not None:
                native.close_restore(prepared)
        except Exception as error:
            warnings.append('Safety reload resource release failed: ' + _error(controller, error))  # i18n: stored diagnostic
        finally:
            if owner is not None:
                try:
                    owner.close()
                except Exception as error:
                    warnings.append('Safety reload working-copy release failed: ' + _error(controller, error))  # i18n: stored diagnostic


def _note_pixels(controller, discarded, never_held):
    """Say in the chat which unsaved image pixel edits the replaced document took with it (checkpoint_images)."""
    with controller.lock:
        controller.state['messages'].extend(checkpoint_images.restore_notices(discarded, never_held))


def _refresh_views(ui, warnings):
    ui.console._versions.clear()
    ui.console._states.clear()
    ui.chat_view._modals.clear()
    for window in ui.bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if ui.console.is_chat(area):
                try:
                    ui.setup_chat_area(window, area)
                except Exception as error:
                    warnings.append('Chat layout refresh failed: ' + str(error))  # i18n: stored diagnostic


def _finish_chats(ui, controller, checkpoint_id, scopes, record, file_count, warnings):
    document = ui.document_key()
    targets = tuple(ui._controllers.values())
    for target in targets:
        try:
            result = target.rebind_after_restore(document, checkpoint_id, project_scope=scopes.get(target.state['id']))
            warnings.extend('Chat ' + target.state['id'] + ': ' + str(value) for value in result.get('warnings', []))
        except Exception as error:
            warnings.append('Chat ' + target.state['id'] + ' recovery lifecycle needs review: ' + _error(target, error))  # i18n: stored diagnostic
        try:
            _mark_scene_history(target, record, file_count)
        except Exception as error:
            warnings.append('Chat history annotation failed: ' + _error(target, error))  # i18n: stored diagnostic
    # Revoke/rebind EVERY chat before any individual transcript write.
    for target in targets:
        if target is controller:
            continue  # Its final notice is installed before its single save below.
        try:
            target.save()
        except Exception as error:
            warnings.append('Chat ' + target.state['id'] + ' history could not be saved: ' + _error(target, error))  # i18n: stored diagnostic


def execute_restore(ui, operator):
    controller = ui.get_controller()
    if any(target.busy for target in ui._controllers.values()):
        operator.report({'ERROR'}, ui_text.rpt_('Stop active requests before restoring'))
        return {'CANCELLED'}
    warnings, notice = [], None
    opened, file_count, file_progress, never_held = False, None, None, []
    native, prepared, recovery_owner, safety_capture = None, None, None, None
    native_ran, install_failed = False, False

    def release_safety():
        nonlocal safety_capture
        safety, safety_capture = safety_capture, None
        if safety is not None:
            try:
                native.capture_release(safety)
            except Exception as error:
                warnings.append('Safety checkpoint resource release failed: ' + _error(controller, error))  # i18n: stored diagnostic

    def release_recovery():
        nonlocal prepared, recovery_owner
        pending, prepared = prepared, None
        owner, recovery_owner = recovery_owner, None
        try:
            if pending is not None:
                try:
                    native.close_restore(pending)
                except Exception as error:
                    warnings.append('Native recovery resource release failed: ' + _error(controller, error))  # i18n: stored diagnostic
        finally:
            try:
                if owner is not None:
                    try:
                        owner.close()
                    except Exception as error:
                        warnings.append('Recovery working-copy release failed: ' + _error(controller, error))  # i18n: stored diagnostic
            finally:
                release_safety()

    try:
        # All strict preparation completes without replacing the user scene.
        selected = controller.checkpoints.load(operator.checkpoint_id)
        if selected.get('version') == 4:
            import _bpy
            from .checkpoint_recovery import CheckpointRecovery
            native = _bpy._angelo_checkpoint
            recovery_owner = CheckpointRecovery.prepare(controller.checkpoints, operator.checkpoint_id)
            prepared = native.prepare_restore(recovery_owner)
            record, recovery = recovery_owner.record, recovery_owner.path
            plan = controller.checkpoints.restore_plan(operator.checkpoint_id, recovery=recovery_owner)
        else:
            plan = controller.checkpoints.restore_plan(operator.checkpoint_id)
            record, recovery = controller.checkpoints.recovery_copy(operator.checkpoint_id)
        review = checkpoint_review(record, controller.state['messages'])
        never_held = (checkpoint_images.recorded_unsaved(controller.state['messages'], operator.checkpoint_id)
                      if review['scope'] == 'before_restore' else [])
        scopes = {controller.state['id']: controller.project_scope_for_restore(operator.checkpoint_id)}
        for target in ui._controllers.values():
            if not callable(getattr(target, 'rebind_after_restore', None)):
                raise RuntimeError('The host recovery lifecycle is unavailable; no scene was opened')
            if target is not controller:
                try:
                    scopes[target.state['id']] = target.project_scope_for_restore(operator.checkpoint_id, source_controller=controller)
                except (OSError, ValueError, TypeError) as error:
                    warnings.append('Chat ' + target.state['id'] + ' public notes remain isolated: ' + _error(target, error))  # i18n: stored diagnostic
        # Before the safety capture: what is unsaved now is what this restore discards.
        unsaved = checkpoint_images.unsaved_images(ui.bpy)
        _quiesce_jobs()
        if prepared is not None:
            safety, safety_capture = ui.capture_checkpoint(controller, purpose='safety', _retain_native=True)
            if (safety.get('state') != 'READY' or safety.get('protected') is not True
                    or safety.get('purpose') != 'safety'):
                raise RuntimeError('The complete protected safety checkpoint was not acknowledged')
        else:
            safety = ui.capture_checkpoint(controller, purpose='safety')
        for _, _, item in plan:
            controller.checkpoints.journal(safety['id'], Path(item['root']), item['path'], restore_entry=item)
        ui.remember_draft(controller)
        notice = _safety_notice(controller, operator.checkpoint_id, safety, unsaved)
        ui._restoring_snapshot = True
        try:
            with ui.bpy.context.temp_override(window=ui.main_window()):
                if prepared is not None:
                    native_ran = True
                    opened = native.restore(prepared, safety_capture) is True
                else:
                    result = ui.bpy.ops.wm.open_mainfile(filepath=str(recovery), load_ui=False, use_scripts=False)
                    opened = 'FINISHED' in result
            if not opened:
                raise RuntimeError('Blender did not finish opening the recovery scene')
        except Exception as error:
            if prepared is not None and native.failed_restore_safety() == safety['id']:
                # Native Restore freed the previous document but could not install the selected
                # state. Reload the exact pre-Restore document from the protected safety capture,
                # whose pin must close before it becomes a recovery source.
                release_safety()
                reload_failures = len(warnings)
                # Transient install faults are retried before anything else changes.
                for _attempt in range(2):
                    if _reload_safety(ui, controller, native, safety, warnings):
                        del warnings[reload_failures:]
                        raise _SafetyReloaded(_error(controller, error)) from error
                    if native.failed_restore_safety() != safety['id']:
                        break
                # Still refused: an add-on load handler edits the document on every load. Reload the
                # exact pre-Restore state with add-on load handlers suspended, so nothing is lost.
                if native.failed_restore_safety() == safety['id']:
                    suspended = _suspend_load_handlers(ui)
                    try:
                        if _reload_safety(ui, controller, native, safety, warnings):
                            del warnings[reload_failures:]
                            raise _SafetyReloaded(_error(controller, error), _handler_owners(suspended)) from error
                    finally:
                        _resume_load_handlers(suspended)
                install_failed = opened = True
            # A handler can fail after native file reading already replaced the
            # document. The unique working-copy path is additional real evidence.
            opened = opened or ui.bpy.data.filepath == str(recovery)
            if not opened:
                raise
            warnings.append('The recovery scene opened, but its load reported: ' + _error(controller, error))  # i18n: stored diagnostic
        # Native consumption has finished. The protected safety record remains,
        # but its capture pin must close before its journal can own file effects.
        release_safety()
        ui._recovery_original_path = record['original_path']
        try:
            if recovery_owner is not None:
                file_count = controller.checkpoints.restore_since(operator.checkpoint_id, plan, recovery=recovery_owner)
            else:
                file_count = controller.checkpoints.restore_since(operator.checkpoint_id, plan)
        except Exception as error:
            file_progress = getattr(error, 'restored_files', None)
            if type(file_progress) is not int or not 0 <= file_progress <= len(plan):
                file_progress = None
            warnings.append('File restoration failed: ' + _error(controller, error))  # i18n: stored diagnostic
        release_recovery()
        _finish_chats(ui, controller, operator.checkpoint_id, scopes, record, file_count, warnings)
        _refresh_views(ui, warnings)
        # Files that actually changed: rewriting a file that already holds its restored bytes changes nothing.
        changed = getattr(file_count, 'changed', file_count)
        if warnings:
            progress = ('The completed file count is unknown.' if file_progress is None else  # i18n: stored English outcome
                str(file_progress) + ' file changes were restored before the file error.') if file_count is None else (  # i18n: stored English outcome
                    str(changed) + ' file changes were restored.')  # i18n: stored English outcome
            text = ('The Blender scene was replaced, but the recorded Sculpt state could not be installed and the safety '  # i18n: stored English outcome
                    'snapshot could not be reloaded, so the scene lacks recorded Sculpt work. ' if install_failed else
                    'The Blender scene was replaced by ' + review['restored'] + ', but recovery is incomplete. ') + progress  # i18n: stored English outcome
            text += '\n' + '\n'.join(warnings) + '\nInspect the scene and workspace files. Undo restore opens safety snapshot ' + safety['id'] + '; no automatic rollback or retry was performed.'  # i18n: stored English outcome
        outcome = {'text': text} if warnings else restore_outcome(review, changed)  # The success notice also carries its template.
        with controller.lock:
            notice.pop('i18n', None)
            notice.update(outcome, restore_state='partial' if warnings else 'complete', scene_restored=True)
            if file_count is not None or file_progress is not None:
                notice['restored_files'] = changed if file_count is not None else file_progress
        _note_pixels(controller, unsaved, never_held)
        controller.version += 1
        try:
            controller.save()
        except Exception as error:
            warning = 'The current recovery outcome could not be saved: ' + _error(controller, error)  # i18n: stored diagnostic
            warnings.append(warning)
            with controller.lock:
                notice.update(restore_state='partial', text=notice['text'] + '\n' + warning +
                    '\nThe earlier saved safety link remains available. Inspect before retrying; safety snapshot ' + safety['id'] + '.')  # i18n: stored English outcome
            controller.version += 1
        if warnings:
            operator.report({'WARNING'}, ui_text.rpt_('The scene was restored, but recovery is incomplete. Inspect this chat and Undo restore; '
                                                       'safety snapshot {snapshot}', snapshot=safety['id']))
        elif unsaved:
            operator.report({'WARNING'}, ui_text.rpt_(checkpoint_images.DISCARDED, images=checkpoint_images.names(unsaved)))
        return {'FINISHED'}
    except _SafetyReloaded as error:
        release_recovery()
        # Every chat now addresses the reloaded document, exactly as after Undo restore.
        safety_id = notice['restore_checkpoint']
        reload_scopes = {}
        for target in ui._controllers.values():
            try:
                reload_scopes[target.state['id']] = target.project_scope_for_restore(
                    safety_id, **({} if target is controller else {'source_controller': controller}))
            except (OSError, ValueError, TypeError) as scope_error:
                warnings.append('Chat ' + target.state['id'] + ' public notes remain isolated: ' + _error(target, scope_error))  # i18n: stored diagnostic
        try:
            safety_record = controller.checkpoints.load(safety_id)
        except (OSError, ValueError, KeyError) as load_error:
            safety_record = {'created': float('inf')}
            warnings.append('Chat history annotation needs review: ' + _error(controller, load_error))  # i18n: stored diagnostic
        _finish_chats(ui, controller, safety_id, reload_scopes, safety_record, 0, warnings)
        _refresh_views(ui, warnings)
        # The native reason is an English diagnostic: kept as restore_reason, never inside the translated sentence.
        with controller.lock:  # The notice row changes under the chat's lock (session_writer).
            if error.suspended:  # One suspended add-on edits the document on every load. Which one is unknown: the
                # native check sees the change, not its author, so all are named as candidates, none as the culprit.
                notice.update(restore_state='not_applied', scene_reloaded=True, load_handlers_suspended=list(error.suspended),
                              restore_reason=str(error), **ui_text.notice(
                    'Restore was not applied: the restored scene could not be installed because one of these add-ons changes '
                    'the file when it loads: {addons}. The document was reloaded exactly from the protected safety snapshot '
                    'taken before Restore, with their load handlers suspended for that reload, so nothing was lost. Their '
                    'setup for a newly opened file was skipped for this document and runs again the next time a file is '
                    'opened. Disable the add-on that changes the file, then Restore again. No workspace files were changed. '
                    'Safety snapshot {snapshot} stays available.',
                    addons=', '.join(error.suspended), snapshot=notice['restore_checkpoint']))
            else:
                notice.update(restore_state='not_applied', scene_reloaded=True, restore_reason=str(error), **ui_text.notice(
                    'The recovery scene could not be installed, so the document was reloaded exactly from the protected '
                    'safety snapshot taken before Restore.\nNo workspace files were changed. Safety snapshot {snapshot} '
                    'stays available.', snapshot=notice['restore_checkpoint']))
            if warnings:  # Stored diagnostics are English; the stored text is then the display text.
                notice.pop('i18n', None)
                notice['text'] += '\n' + '\n'.join(warnings)
        _note_pixels(controller, notice.get('unsaved_images'), [])
        controller.version += 1
        try:
            controller.save()
        except Exception as save_error:
            with controller.lock:
                notice.pop('i18n', None)
                notice['text'] += '\nThe outcome could not be saved: ' + _error(controller, save_error)  # i18n: stored diagnostic
        if error.suspended:
            operator.report({'WARNING'}, ui_text.rpt_('Restore was not applied and nothing was lost: one of these add-ons '
                                                      'changes the file when it loads: {addons}. Disable that add-on, '
                                                      'then Restore again',
                                                      addons=', '.join(error.suspended)))
        else:
            operator.report({'WARNING'}, ui_text.rpt_('Restore failed; the document was reloaded from safety snapshot {snapshot}',
                                                      snapshot=notice['restore_checkpoint']))
        return {'FINISHED'}
    except Exception as error:
        release_recovery()
        if opened:
            # Unexpected post-open exceptions must not erase the durable safety
            # reference or misrepresent an already-replaced scene as a no-op.
            if notice is not None:
                with controller.lock:
                    notice.update(restore_state='partial', scene_restored=True, **ui_text.notice(
                        'The Blender recovery scene opened, but recovery finalization failed: {error}\nInspect before retrying. '
                        'Undo restore opens safety snapshot {snapshot}.', error=_error(controller, error), snapshot=notice['restore_checkpoint']))
                _note_pixels(controller, notice.get('unsaved_images'), never_held)
                controller.version += 1
            operator.report({'WARNING'}, ui_text.display_notice(notice) if notice else ui_text.rpt_('The scene opened but recovery is incomplete'))
            return {'FINISHED'}
        if notice is not None:
            with controller.lock:
                notice.update(restore_state='not_applied', **ui_text.notice(
                    'The recovery scene was not opened: {error}\nThe protected safety snapshot is still available through Undo restore.',
                    error=_error(controller, error)))
            controller.version += 1
            try:
                controller.save()
            except Exception:
                pass  # No scene was opened; the pre-open failure is reported below.
        operator.report({'ERROR'}, _error(controller, error))
        return {'CANCELLED'}
    finally:
        release_recovery()
        if native is not None:
            native.clear_failed_restore()  # A later document never inherits the reload admission.
        # Like File > Open, an add-on handler that raised is reported, never a Restore failure.
        if native_ran and getattr(native, 'restore_handler_errors', lambda: 0)():
            operator.report({'WARNING'}, ui_text.rpt_("An add-on's file handler raised an error during Restore; "
                                                      "see the system console for details"))
        ui._restoring_snapshot = False
