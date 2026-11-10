# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Blender source of the existing LibraryService. Same pending-call, approval and document owners; no new engine."""

import logging
import threading
import time

from . import blender_asset_index as index, blender_assets as assets, live_target
from .refusal_codes import RefusalError


def _pending(poll, *, cancel=lambda: None, cleanup=lambda: None, **labels):
    """The controller may deliver Stop or poll completion twice. Neither can repeat an append."""
    from .library_agent import LibraryPending, _failure
    state = {'result': None}

    def once():
        if state['result'] is None:
            state['result'] = poll()
        return state['result']

    def stop():
        state['result'] = _failure('The library request was stopped; nothing was imported.', 'CANCELLED')
        cancel()
    return LibraryPending(once, cancel=stop, cleanup=cleanup, **labels)


def search(service, args):
    from .library_agent import _Worker, _failure
    bundled_root, remote_root, url, version, enabled, online = assets.configuration()
    state = {'bundled': list(assets._bundled or ()), 'files': None, 'remote': [], 'reader': None,
             'scan': _Worker(lambda stop: assets.bundled_files(bundled_root, stop)) if assets._bundled is None else None,
             'listing': None, 'notes': [], 'bundle_notes': list(assets._bundled_notes),
             'indexed': list(assets._bundled_files), 'previews': None}
    deadline = time.monotonic() + 60
    if enabled and online:
        try:
            state['listing'] = assets.Listing(remote_root, url)
        except Exception:
            logging.getLogger(__name__).warning('Essentials listing refresh could not start', exc_info=True)
            state['notes'].append('Online Essentials could not refresh; using its completed cached listing if available.')

    def poll():
        if state['previews'] is not None:
            return state['previews'].poll()
        scan = state['scan']
        if scan is not None:
            if not scan.done.is_set():
                return None
            if scan.error:
                return _failure('Bundled Essentials could not be indexed.', 'UNSUPPORTED')
            if state['files'] is None:
                state['files'] = iter(scan.value)
            file = next(state['files'], None)
            if file is not None:
                try:
                    state['bundled'].extend(assets.bundled_rows(file))
                    state['indexed'].append(file)
                except (ValueError, OSError) as error:
                    logging.getLogger(__name__).warning('Bundled Essentials file omitted: %s', error)
                    state['bundle_notes'].append('A bundled asset file has unreadable or external dependencies and was omitted.')
                except Exception:
                    logging.getLogger(__name__).warning('Bundled Essentials metadata could not be read', exc_info=True)
                    state['bundle_notes'].append('A bundled asset file has unreadable or external dependencies and was omitted.')
                return None
            state['scan'] = None
        now_enabled, now_online = assets.configuration()[4:]
        listing = state['listing']
        if listing and not listing.ready():
            if now_enabled and now_online and time.monotonic() < deadline:
                return None
            listing.cancel()
            state['notes'].append('Online Essentials refresh stopped; only a completed cached listing can be used.')
        if now_enabled and state['reader'] is None:
            state['reader'] = _Worker(lambda _stop: index.remote_rows(remote_root, url, version))
            return None
        reader = state['reader']
        if reader and not reader.done.is_set():
            return None
        if reader and not reader.error and now_enabled:
            state['remote'] = reader.value
        elif now_enabled:
            state['notes'].append('Online Essentials metadata is unavailable. Bundled assets remain searchable.')
        else:
            state['remote'] = []
            state['notes'].append('Online Essentials is disabled in Preferences > Asset Libraries.')
        if not now_online:
            state['notes'].append('Internet access is disabled. Only bundled or already cached files can be imported.')
        assets.publish(state['bundled'], state['remote'], files=state['indexed'], notes=state['bundle_notes'])
        state['notes'].extend(assets._bundled_notes)
        rows, total = index.search(assets._rows.values(), args['query'], args.get('kind'), args.get('limit', 8), args.get('offset', 0))
        result = {'query': args['query'], 'source': 'blender', 'total': total,
                  'items': [index.compact(row) for row in rows], 'scene_changed': False}
        end = args.get('offset', 0) + len(rows)
        result['next_offset'] = end if end < total else None
        if state['notes']:
            result['notes'] = list(dict.fromkeys(state['notes']))
        if args.get('preview'):
            from .blender_asset_previews import Previews
            try:
                state['previews'] = Previews(service, result, rows)
            except Exception:
                return dict(result, preview_note='Thumbnails are unavailable; the search results stand.')
            return None
        return result

    def cleanup():
        for key in ('scan', 'reader'):
            if state[key]:
                state[key].cancel.set()
        if state['listing']:
            state['listing'].cancel()
        if state['previews']:
            state['previews'].close()
    return _pending(poll, status_text='Searching Blender Essentials', activity_note='Reading asset metadata.',
                    timeout=120, cancel=cleanup, cleanup=cleanup)


def _targets(service, row, args):
    if row['kind'] == 'material' or (row['kind'] == 'node_group' and args.get('objects')):
        return service._targets({'kind': 'texture'}, args)
    if 'objects' in args:
        raise RefusalError('objects applies to materials and Geometry Nodes modifier assets only.', code='VALIDATION')
    return []


def precondition(service, args):
    assets.require_undo()
    from .blender_asset_commit import native_api
    native_api()  # Refuse an incomplete native chain before asking the person to approve.
    row = assets.get(args['item_id'])
    if 'resolution' in args:
        raise RefusalError('Blender Essentials assets use their authored resolution; omit resolution.', code='VALIDATION')
    if 'location' in args and row['kind'] != 'model':
        raise RefusalError('location places object and collection assets only.', code='VALIDATION')
    service._require_view()
    targets = _targets(service, row, args)
    return (row['id'], tuple(f['sha256'] for f in row.get('files', (row['file'],))),
            tuple(obj.session_uid for obj in targets), tuple(args.get('location') or ()))


def use(service, args):
    from .library_agent import _Worker, _failure, _finish_failure
    precondition(service, args)
    row = assets.get(args['item_id'])
    held_scene = live_target.scene_ref(service.window.scene)
    # This is a downward bound from the same started-job lifetime returned below.
    deadline = time.monotonic() + 900
    targets = [obj.session_uid for obj in _targets(service, row, args)]
    state = {'worker': _Worker(lambda stop: all(index.verified(f, stop) for f in row.get('files', (row['file'],)))),
             'download': None, 'after': False, 'queue_done': threading.Event(), 'payload': None}

    def poll():
        try:
            assets.get(row['id'])  # Preference/catalog changes invalidate an outstanding selection.
            if state['download'] is not None:
                from _bpy_internal.assets.remote_library import asset_downloader as native
                if native.downloader_status(assets.configuration()[2]) != native.DownloadStatus.DOWNLOADING:
                    state['queue_done'].set()
            worker = state['worker']
            if not worker.done.is_set():
                return None
            if worker.error:
                return _failure('The Essentials asset could not be verified.', 'UNSUPPORTED')
            if not worker.value:
                if row['origin'] != 'online' or state['after']:
                    return _failure('The Essentials asset is missing or its checksum does not match.', 'UNSUPPORTED')
                if not assets.configuration()[5]:
                    return _failure('Downloading this asset requires Allow Online Access in Preferences.', 'UNSUPPORTED')
                from _bpy_internal.assets.remote_library import asset_downloader as native
                root, url = assets.configuration()[1:3]
                if state['download'] is None:
                    state['download'] = assets.native_call(native.download_asset_file,
                        asset_library_url=url, asset_library_local_path=root, asset_url=row['file']['url'],
                        asset_hash='SHA256:' + row['file']['sha256'],
                        save_to=index.inside(root, row['file']['relative']))
                    state['after'] = True
                    state['worker'] = _Worker(lambda stop: index.wait_for_file(row['file'], stop, state['queue_done']))
                    return None
            if state['payload'] is None:
                from .blender_asset_commit import AssetCommit
                # Prepare the private source copy before the main-thread native Undo transaction.
                state['payload'] = AssetCommit(row, deadline=deadline)
                state['worker'] = _Worker(lambda stop: state['payload'].seal_payload(row, stop))
                return None
        except RefusalError as error:
            return _failure(str(error), error.code)
        except Exception:
            return _failure('The Essentials asset could not be prepared; nothing was imported.', 'UNSUPPORTED')
        try:
            return _finish(held_scene(), row, args.get('location'), targets, state['payload'])
        except Exception as error:
            return _finish_failure(error)

    def cancel():
        state['worker'].cancel.set()
        if state['payload']:
            state['payload'].close()
        # Blender may share this transfer with its Asset Browser. Stop ends this import, not another user's transfer.
    return _pending(poll, status_text='Importing Blender Essentials',
                    activity_note='Preparing the selected Blender asset.', timeout=900, cancel=cancel, cleanup=cancel)


def _finish(scene, row, point, target_ids, payload):
    assets.require_undo()  # Preferences may have changed while the approved import waited for disk/network work.
    return _finish_held(scene, row, point, target_ids, payload)


def _finish_held(scene, row, point, target_ids, payload):
    import bpy
    from .library_agent import _window_for
    from . import live_windows
    from .team_library_files import NotImported
    window = _window_for(scene) if scene is not None else None
    if window is None:
        raise NotImported('The document closed or changed; nothing was imported.')
    by_uid = {obj.session_uid: obj for obj in scene.objects}
    targets = [by_uid.get(uid) for uid in target_ids]
    if (len(set(target_ids)) != len(target_ids)
            or any(obj is None or obj.type != 'MESH' or obj.library or obj.data.library for obj in targets)):
        raise NotImported('A chosen mesh changed or was deleted; nothing was imported.')
    area = next(a for a in live_windows.areas(window) if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=window, area=area, region=region):
        return payload.finish(bpy.context, row, point=point, targets=targets,
                              format_result=lambda facts: _native_result(facts, row))


def _native_result(facts, row):
    """Format actual stock-import read-back before native Undo publication; no source-proof flags."""
    from .scene_queries import object_handle
    if (not isinstance(facts, dict) or type(facts.get('schema')) is not int or facts['schema'] != 1
            or facts.get('item_id') != row['id'] or facts.get('source') != 'blender'):
        raise RuntimeError('Essentials import read-back is unavailable')
    result = {key: facts[key] for key in ('item_id', 'source', 'title', 'kind', 'id_type',
              'datablock', 'usage', 'license', 'author', 'object_count')}
    result.update(success=True, blender_undoable=True, changes=facts['changes'],
                  objects=[{'handle': object_handle(obj), 'name': obj.name, 'type': obj.type}
                           for obj in facts['objects']])
    if facts['modifiers']:
        result['modifiers'] = [{'object': object_handle(obj), 'name': modifier.name}
                               for obj, modifier in facts['modifiers']]
    if facts['loaded_only']:
        result['note'] = ('Loaded into this file, not activated or connected. Connecting this node group or '
                          'activating this brush requires separately reviewed angelo_execute_python or manual '
                          'editing. Inspect the installed API with angelo_blender_api before proposing code.')
    return result
