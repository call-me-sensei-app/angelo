# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Verified private payload + ordinary native asset Undo transaction.

This is not the former full allocator/source-graph capability. Stock importers and
native dirty-before/restore/history own their established behavior. Python never
reports unchanged from removing new IDs or reconstructing selected fields.
"""
import time
from pathlib import Path

from .team_library_files import ImportFailed, NotImported, Refused

MODES = ('import_objects', 'assign_material', 'use_world', 'apply_geometry_nodes', 'load_datablock')
TRANSACTION_FLAGS = ('native_undo', 'dirty_preimage', 'checked_restore', 'nested_undo_suppressed')
METHODS = ('asset_begin', 'asset_finish', 'asset_status', 'asset_close', 'asset_recover', 'asset_pending')
_NO_CLOSE_RETURN = object()
_recovery_owner = None  # Strong lifetime only; the native scope owns Main/history authority.


def recovery_status():
    """Read-only diagnosis of the retained attempt; never replay a native mutation."""
    owner = _discover_pending()
    return None if owner is None else owner.status()


def recover_pending():
    """Explicit manual recovery action; never called by polling, Stop or automatic cleanup.

    The native owner decides whether exact restoration is still possible. A published or
    unknown-partial history cannot be repaired by replaying a Python import/finish call.
    """
    owner = _discover_pending()
    if owner is None:
        return None
    if owner.applying:
        raise RuntimeError('Wait for the current Blender importer to unwind before recovery.')
    status = owner.status()
    if status.get('recoverable') is not True:
        raise RuntimeError('Recovery is unavailable. Keep this document open and do not import another asset.')
    try:
        status = _receipt(owner.api.asset_recover(owner.scope))
    except Exception:
        status = owner.status()  # No automatic replay of a manual recovery with a lost return.
    if status['state'] == 'RESTORED' and status['restored'] and not status['recovery_required']:
        owner.close()
        return owner.status()
    raise RuntimeError('Recovery did not complete. Keep this document open and do not import another asset.')



class _DiscoveredOwner:
    """Rediscovered native lifetime after reload; no invented private-copy ownership."""
    applying = False

    def __init__(self, api, scope):
        self.api, self.scope = api, scope

    def status(self):
        return _receipt(self.api.asset_status(self.scope))

    def close(self):
        global _recovery_owner
        try:
            status = _receipt(self.api.asset_close(self.scope))
        except Exception:
            status = self.status()  # Observe a lost return, never replay cleanup.
        if not AssetCommit.quiescent(status):
            raise RuntimeError('The retained native asset transaction did not close.')
        if _recovery_owner is self:
            _recovery_owner = None


def _discover_pending(api=None):
    global _recovery_owner
    if _recovery_owner is not None:
        return _recovery_owner
    api = native_api() if api is None else api
    scope = api.asset_pending()  # Query failure is uncertainty, never evidence of absence.
    if scope is not None:
        _recovery_owner = _DiscoveredOwner(api, scope)
    return _recovery_owner


def _context_witness(context):
    from . import live_target
    # Native begin absence is only a pristine certificate in the original document/context.
    names = ('window_manager', 'window', 'scene', 'view_layer')
    values = tuple(getattr(context, name, None) for name in names)
    if any(value is None for value in values):
        return lambda: False
    manager, window, scene, layer = values
    uid = getattr(manager, 'session_uid', None)
    refs = (live_target.window_ref(window), live_target.scene_ref(scene), live_target.view_layer_ref(layer))
    def unchanged():
        try:
            return (uid is not None and context.window_manager.session_uid == uid
                    and all(ref() is not None and ref() == getattr(context, name)
                            for ref, name in zip(refs, names[1:])))
        except Exception:
            return False
    return unchanged


def native_api(payload_format='blend'):
    from .refusal_codes import coded
    if payload_format not in ('blend', 'glb'):
        raise coded(NotImported('Unsupported asset payload format'), 'UNSUPPORTED')
    try:
        from _bpy import _angelo_undo as api
        caps = api.asset_transaction_capabilities()
        if (not isinstance(caps, dict) or type(caps.get('schema')) is not int or caps['schema'] != 1
                or set(caps) != {'schema', *TRANSACTION_FLAGS}
                or any(caps.get(flag) is not True for flag in TRANSACTION_FLAGS)
                or any(not callable(getattr(api, method, None)) for method in METHODS)):
            raise ValueError('Incomplete native asset Undo transaction')
        return api
    except Exception as error:
        raise coded(NotImported('This app does not provide the native asset Undo transaction.'),
                    'UNSUPPORTED') from error


def mode_for(row, targets):
    return {'model': 'import_objects', 'material': 'assign_material', 'world': 'use_world',
            'node_group': 'apply_geometry_nodes' if targets else 'load_datablock',
            'brush': 'load_datablock'}[row['kind']]


def _receipt(value):
    if (not isinstance(value, dict) or type(value.get('schema')) is not int or value['schema'] != 1
            or value.get('state') not in {'ACTIVE', 'COMMITTED', 'RESTORED', 'RECOVERY_REQUIRED'}
            or (value.get('published') is not None and type(value.get('published')) is not bool)
            or type(value.get('restored')) is not bool or type(value.get('closed')) is not bool
            or type(value.get('recovery_required')) is not bool
            or ('recoverable' in value and type(value['recoverable']) is not bool)
            or (value.get('error') is not None and type(value['error']) is not str)):
        raise RuntimeError('Native asset transaction status is unavailable')
    if ((value['state'] == 'RESTORED' and
         (value['restored'] is not True or value['published'] is not False or value['recovery_required']))
            or (value['state'] == 'COMMITTED' and
                (value['published'] is not True or value['restored'] or value['recovery_required']))
            or (value['state'] == 'ACTIVE' and value['closed'])):
        raise RuntimeError('Native asset transaction status is inconsistent')
    return value


class AssetCommit:
    """One prepared copy and one native transaction; unknown returns are never replayed."""

    def __init__(self, row, *, deadline, api=None, payload_format='blend'):
        if _recovery_owner is not None:
            raise NotImported('A previous asset transaction needs recovery before another import. '
                              'Keep this document open and use Asset Recovery in the 3D View sidebar’s Angelo tab.')
        if payload_format not in ('blend', 'glb'):
            raise NotImported('Unsupported asset payload format')
        self.api = native_api(payload_format) if api is None else api
        if _discover_pending(self.api) is not None:
            raise NotImported('A retained native asset transaction needs recovery before another import.')
        self.deadline, self.payload_format = deadline, payload_format
        files = tuple(dict(file) for file in row.get('files', (row['file'],)))
        if payload_format == 'glb' and (row['kind'] != 'model' or len(files) != 1
                                       or not 0 < files[0]['size'] <= 256 * 1024 * 1024):
            raise NotImported('A Fab import requires one self-contained GLB within its size limit.')
        self.selected = dict(row, file=dict(row['file']), files=files)
        from .blender_asset_payload import PayloadCopy
        self.payload = PayloadCopy()
        # Canonicalize only our freshly allocated/registered output directory. On macOS,
        # the system /var alias must not look like an untrusted link in index.inside().
        # Untrusted source/member paths still go through the unchanged no-link checks.
        self.payload.folder = self.payload.folder.resolve(strict=True)
        self.row = self.scope = self.owner = None
        self.begin_attempted = self.finish_attempted = self.close_attempted = False
        self.begin_failed_pristine = False
        self.applying = self.closed = False
        self.close_report = _NO_CLOSE_RETURN

    def remaining(self):
        duration = int((self.deadline - time.monotonic()) * 1_000_000_000)
        if duration <= 0:
            raise NotImported('The asset import expired before its next operation.')
        return duration

    def seal_payload(self, row, cancel):
        """Worker-only verified private copy. It does not grant native source-graph authority."""
        if cancel.is_set() or self.closed:
            raise InterruptedError('Asset import stopped before its private copy was ready')
        self.remaining()
        self.row = self.payload.prepare(self.selected, cancel)
        if cancel.is_set() or self.closed:
            raise InterruptedError('Asset import stopped after preparation')
        return self.row

    def status(self):
        if self.scope is None:
            if self.begin_attempted and not self.begin_failed_pristine:
                raise RuntimeError('Native asset begin did not return an observable scope')
            return None
        return _receipt(self.api.asset_status(self.scope))

    def source_paths(self):
        """Exact approved original/private pairs; native read routing adds private identity pairs.

        Original names are only logical dependency identities. They must never be reopened
        as a fallback after verification; every real source read uses the held private copy.
        """
        originals = {file['relative']: file for file in self.selected['files']}
        copies = {file['relative']: file for file in self.row['files']}
        if len(originals) != len(self.selected['files']) or originals.keys() != copies.keys():
            raise NotImported('The private dependency copy no longer matches its selected files.')
        pairs = tuple((str(Path(originals[key]['path']).resolve()), str(Path(copies[key]['path']).resolve()))
                      for key in sorted(originals))
        if len({old for old, _new in pairs}) != len(pairs) or len({new for _old, new in pairs}) != len(pairs):
            raise NotImported('The selected dependency closure repeats a file identity.')
        return pairs

    @staticmethod
    def quiescent(status):
        return (isinstance(status, dict) and status.get('closed') is True
                and status.get('recovery_required') is False
                and ((status.get('state') == 'COMMITTED' and status.get('published') is True)
                     or (status.get('state') == 'RESTORED' and status.get('restored') is True
                         and status.get('published') is False)))

    def _finish_native(self, keep):
        if self.finish_attempted:
            return self.status()
        self.finish_attempted = True
        try:
            return _receipt(self.api.asset_finish(self.scope, keep=keep))
        except Exception:
            # A lost Python return can only be recovered by the exact stored native receipt.
            return self.status()

    def _close_native(self):
        if not self.close_attempted:
            self.close_attempted = True
            try:
                self.close_report = _receipt(self.api.asset_close(self.scope))
            except Exception:
                pass  # Never replay a close with an unknown return.
        return self.status()

    def close(self):
        global _recovery_owner
        self.closed = True
        if self.applying:
            return  # Reentrant Stop must not restore/unlink while the stock operator is on-stack.
        if self.scope is None:
            if self.begin_attempted and not self.begin_failed_pristine:
                raise RuntimeError('The native asset begin owner needs recovery')
            self.payload.close()
            self.close_report = None
            if _recovery_owner is self:
                _recovery_owner = None
            return
        status = self.status()
        if status['state'] == 'ACTIVE' and not self.finish_attempted:
            status = self._finish_native(False)
        if status['state'] not in {'COMMITTED', 'RESTORED'} or status['recovery_required']:
            raise RuntimeError('The native asset transaction needs recovery')
        status = self._close_native()
        if not self.quiescent(status):
            raise RuntimeError('The native asset transaction did not close')
        self.payload.close()
        if _recovery_owner is self:
            _recovery_owner = None

    def finish(self, context, row, *, point=None, targets=(), format_result=lambda facts: facts):
        global _recovery_owner
        from . import blender_assets
        blender_assets.require_undo()
        if self.closed or self.scope is not None or self.begin_attempted:
            raise NotImported('This asset import was stopped or already entered Blender.')
        if context.mode != 'OBJECT' or not self.payload.owns(self.row):
            raise NotImported('The prepared asset or Object Mode context is no longer available.')
        if any(row.get(key) != self.selected.get(key) for key in
               ('id', 'name', 'kind', 'origin', 'license', 'author', 'id_type')):
            raise NotImported('The selected asset changed before import.')
        self.remaining()
        source_paths = self.source_paths()
        self.row['_source_paths'] = source_paths
        if _discover_pending(self.api) is not None:
            raise NotImported('A retained native asset transaction needs recovery before import.')
        with self.payload.hold(self.row):
            self.applying = True
            try:
                if _recovery_owner is not None and _recovery_owner is not self:
                    raise RuntimeError('Another native asset transaction is retained')
                _recovery_owner = self  # Publish lifetime BEFORE the first fallible native begin.
                unchanged_context = _context_witness(context)
                self.begin_attempted = True
                try:
                    self.scope = self.api.asset_begin(source_paths=source_paths)
                except Exception:
                    # Native owns its capsule before activation. A raised begin can still
                    # retain that exact owner; adopt it before any cleanup or status query.
                    self.scope = self.api.asset_pending()
                    if self.scope is not None:
                        self.owner = self.scope
                        self.status()  # Validate stored facts; failure retains this owner/copy.
                    else:
                        self.begin_failed_pristine = unchanged_context()
                    raise
                if self.scope is None:
                    raise RuntimeError('Native asset begin returned no scope')
                self.owner = self.scope
                if self.payload_format == 'glb':
                    from .asset_stock_import import apply_glb
                    facts = apply_glb(context, self.row, point=point, targets=targets)
                else:
                    from .blender_asset_import import apply_item
                    facts = apply_item(context, self.row, point=point, targets=targets)
                # Build all RNA-dependent handles/read-back while the new Main is live.
                # Only the later stored COMMITTED receipt makes this answer successful.
                result = format_result(facts)
                if self.closed:
                    raise InterruptedError('The asset import was stopped before publication')
                self.applying = False
                status = self._finish_native(True)
                if status['state'] != 'COMMITTED' or status['published'] is not True or status['recovery_required']:
                    raise RuntimeError('Native asset Undo publication did not complete')
                self.close()
                return result
            except BaseException as error:
                self.applying = False
                if self.begin_failed_pristine:
                    self.close()
                    raise NotImported('Blender could not prepare the asset transaction. Nothing was imported.') from error
                unchanged = False
                try:
                    if self.scope is not None:
                        status = self.status()
                        if status['state'] == 'ACTIVE':
                            status = self._finish_native(False)
                        unchanged = (status['state'] == 'RESTORED' and status['restored'] is True
                                     and status['recovery_required'] is False)
                        self.close()
                        unchanged = unchanged and self.quiescent(self.status())
                except Exception:
                    unchanged = False
                detail = str(error) if isinstance(error, (NotImported, Refused)) else 'Blender could not complete this asset import.'
                raise ImportFailed(detail + ' ' +
                    ('The prior state was restored.' if unchanged else
                     'Changes may remain. Keep this document open. Use Asset Recovery in the '
                     '3D View sidebar’s Angelo tab. Do not import another asset until recovery completes.'),
                    scene_unchanged=unchanged) from error
            finally:
                self.applying = False
