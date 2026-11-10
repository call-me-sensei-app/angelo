# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Approved Fab preparation and stock GLB import inside the native asset Undo transaction."""
import time

from . import live_target
from .blender_asset_commit import AssetCommit, native_api
from .fab_catalog import FabError
from .team_library_files import NotImported
from .refusal_codes import RefusalError
from .ui_text import n_


class UndoRequired(RefusalError):
    fab_reason = 'UNDO_REQUIRED'  # Local presentation only; the model keeps the existing UNSUPPORTED code.


def ready():
    from . import blender_assets
    try:
        blender_assets.require_undo()  # Same cheap first-party asset policy, before approval/download.
    except RefusalError as error:
        raise UndoRequired(str(error), code='UNSUPPORTED') from None
    native_api('glb')  # Missing real producers refuse before a download or a scene write.


def _row(prepared):
    item, proof = prepared.item, prepared.payload_proof
    return {'id': 'fab:' + item.identity, 'name': item.title, 'kind': 'model', 'origin': 'fab',
            'license': item.license, 'author': item.author,
            'file': {'relative': proof.relative, 'path': proof.path, 'size': proof.size, 'sha256': proof.sha256}}


def _result(facts, row):
    from .scene_queries import object_handle
    if (not isinstance(facts, dict) or type(facts.get('schema')) is not int or facts['schema'] != 1
            or facts.get('source') != 'fab'
            or facts.get('item_id') != row['id']):
        raise RuntimeError('Native Fab result is unavailable')
    return dict(success=True, item_id=row['id'], source='fab', title=row['name'], kind='model',
                license=row['license'], author=row['author'], blender_undoable=True,
                objects=[{'handle': object_handle(o), 'name': o.name, 'type': o.type} for o in facts['objects']],
                changes=facts['changes'], object_count=facts['object_count'])


class RetiredImport:
    """One exact canceled owner held until worker drain and the native close witness.

    Before native begin there is no native transaction owner. Once begin was attempted,
    only the exact stored COMMITTED/RESTORED closed receipt settles it; unknown or
    recovery-required owners retain the private copy and prepared Fab lease.
    """
    def __init__(self, job, state):
        self.job, self.state = job, state

    def poll(self):
        if not self.job.done.is_set():
            return False
        if self.job.value is not None and self.job.cleanup is not None:
            self.job.stop()  # Done-only in-memory result cleanup; failure retains this exact Job/value.
        payload = self.state['payload']
        if payload is not None:
            close_error = None
            try:
                payload.close()
            except Exception as error:
                close_error = error  # Never replay a close whose Python return was lost.
            status = payload.status()
            missing = object()
            pristine = (close_error is None and getattr(payload, 'scope', missing) is None
                        and getattr(payload, 'owner', missing) is None
                        and (getattr(payload, 'begin_attempted', True) is False
                             or getattr(payload, 'begin_failed_pristine', False) is True)
                        and getattr(payload, 'close_report', missing) is None and status is None)
            if not pristine and not payload.quiescent(status):
                if close_error is not None:
                    raise close_error
                raise RuntimeError('Canceled Fab native ownership is not proved quiescent')
        if self.state['prepared'] is not None:
            self.state['prepared'].close()
        return True


def start(host, window, item_id, variant_id, expected, point):
    from .library_agent import LibraryPending, _finish_failure
    ready()
    if host.active_import is not None or host.retired_import is not None:
        raise FabError('UNAVAILABLE')
    session = host.current_session()
    held_window, held_scene = live_target.window_ref(window), live_target.scene_ref(window.scene)
    deadline = time.monotonic() + 900
    state = {'prepared': None, 'payload': None, 'row': None, 'result': None, 'entered': False}
    job = host.submit_agent(lambda current, stop: current.prepare(item_id, variant_id, expected=expected, cancel=stop),
                            cleanup=lambda prepared: prepared.close())

    def poll():
        nonlocal job
        if state['result'] is not None:
            return state['result']
        if not job.done.is_set():
            return None
        try:
            if time.monotonic() >= deadline or host.current_session() is not session:
                raise FabError('EXPIRED')
            value = job.take()
            if state['prepared'] is None:
                state['prepared'] = value
                state['row'] = _row(value)
                state['payload'] = AssetCommit(state['row'], deadline=deadline, payload_format='glb')
                job = host.submit_agent(lambda current, stop: state['payload'].seal_payload(state['row'], stop), import_owner=pending)
                return None
            import bpy
            from . import live_windows
            window, scene = held_window(), held_scene()
            if window is None or scene is None or window.scene is not scene:
                raise NotImported('The approved document closed or changed before import.')
            area = next((a for a in live_windows.areas(window) if a.type == 'VIEW_3D'), None)
            region = next((r for r in area.regions if r.type == 'WINDOW'), None) if area else None
            if region is None:
                raise NotImported('The approved 3D Viewport is no longer available.')
            # Stock effects start only after AssetCommit's native dirty-before capture.
            with bpy.context.temp_override(window=window, area=area, region=region):
                state['entered'] = True
                state['result'] = state['prepared'].consume(lambda _item, _facts:
                    state['payload'].finish(bpy.context, state['row'], point=point,
                                           format_result=lambda facts: _result(facts, state['row'])))
        except Exception as error:
            if state['entered']:
                state['result'] = _finish_failure(error)
            else:
                from .library_agent import _failure
                message = str(error) if isinstance(error, (FabError, NotImported)) else 'The Fab asset could not be prepared.'
                state['result'] = _failure(message, 'UNSUPPORTED')
        return state['result']

    retired = None
    def cancel():
        nonlocal retired
        if state['result'] is not None and state['result'].get('success') is True:
            host.release_import(pending)
            return  # consume released the cache; the stored native transaction receipt already proved closure.
        job.cancel.set()  # Retirement owns result cleanup, including a failed worker cleanup.
        # A canceled closed StudioPending will never be polled by Controller.
        # Transfer exact ownership before returning; neither the lease nor the
        # native payload can close while its byte-sealing worker still runs.
        if state['result'] is None:
            from .library_agent import _failure
            state['result'] = _failure('The Fab request was stopped; nothing was imported.', 'CANCELLED')
        if retired is None:
            retired = RetiredImport(job, state)
        host.retire_import(retired)
        host.release_import(pending)
    pending = LibraryPending(poll, cancel=cancel, cleanup=cancel, timeout=900,
                             status_text=n_('Importing a model into the scene'), activity_note=n_('Getting ready'))
    host.claim_import(pending)
    return pending
