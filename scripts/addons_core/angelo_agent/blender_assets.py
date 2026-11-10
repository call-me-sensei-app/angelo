# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Essentials metadata in the running app. bpy stays on the main thread; downloads use Blender's own owners.

The bundled catalog is built once, one file per controller poll, in temporary Main data (never the artist's file).
Remote listings and payloads share Blender's native cache. Disabling Online Essentials hides its rows immediately;
disabling internet access prevents transfers, including when an import is waiting. No preference is changed here.
"""

import hashlib
import inspect
from pathlib import Path

from . import blender_asset_index as index
from .refusal_codes import RefusalError

_bundled = None
_bundled_files = ()
_bundled_notes = ()
_rows = {}


def native_call(function, **arguments):
    """Blender 5.3 adds token arguments to the 5.2 native API. Essentials is public: always pass an empty token.

    Bind once by signature, never retry a partially submitted transfer after TypeError.
    """
    parameters = inspect.signature(function).parameters
    for name in ('auth_token', 'asset_library_auth_token'):
        if name in parameters:
            arguments[name] = ''
    return function(**arguments)


def require_undo():
    """undo_push returns FINISHED even when native preferences silently suppress its snapshot."""
    import bpy
    edit = bpy.context.preferences.edit
    if not edit.use_global_undo or edit.undo_steps <= 0:
        raise RefusalError('Enable Global Undo and set Undo Steps above zero before importing an asset.',
                           code='UNSUPPORTED')


def configuration():
    import bpy
    prefs = bpy.context.preferences
    return (Path(bpy.utils.system_resource('DATAFILES', path='assets')).resolve(),
            Path(bpy.types.AssetLibrary.online_assets_cache_path()).resolve(),
            bpy.types.AssetLibrary.online_assets_url(), tuple(bpy.app.version[:2]),
            bool(prefs.asset_libraries.use_online_essentials
                 and prefs.experimental.use_remote_asset_libraries), bool(bpy.app.online_access))


def bundled_files(root, cancel):
    """Disk traversal/hashing worker. The installed Essentials directory is the only bundled root."""
    found = []
    for path in sorted(root.rglob('*.blend')):
        if cancel.is_set():
            break
        path = index.inside(root, path.relative_to(root).as_posix())
        size = path.stat().st_size
        if not 1 <= size <= index.MAX_FILE or len(found) >= 128:
            raise ValueError('Bundled Essentials exceeds its supported bounds')
        sha = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                if cancel.is_set():
                    return []
                sha.update(chunk)
        found.append({'path': str(path), 'relative': path.relative_to(root).as_posix(),
                      'size': size, 'sha256': sha.hexdigest(), 'url': '', 'root': str(root)})
    return found


def bundled_rows(file):
    """One installed file's metadata; temporary data is released before returning plain records."""
    import bpy
    rows = []
    with bpy.data.temp_data() as data:
        # assets_only also filters Library IDs on some Blender versions. Read dependency headers without it.
        with data.libraries.load(file['path'], link=False) as (source, _target):
            file['dependencies'] = []
            root = Path(file.get('root') or Path(file['path']).parent)
            for library in source.libraries:
                path = dependency_path(file, library.filepath)
                index.inside(root, path.relative_to(root).as_posix())
                file['dependencies'].append(str(path))
        with data.libraries.load(file['path'], assets_only=True, link=False) as (source, target):
            for collection, _kind in index.TYPES.values():
                setattr(target, collection, getattr(source, collection))
        for collection, _kind in index.TYPES.values():
            for block in getattr(target, collection):
                meta = block.asset_data
                if meta is None:
                    continue
                raw = {'name': block.name, 'id_type': block.id_type,
                       'meta': {'description': meta.description, 'license': meta.license, 'author': meta.author,
                                'tags': [tag.name for tag in meta.tags]}}
                row = index.record(raw, file, 'bundled')
                row['node_type'] = getattr(block, 'bl_idname', '')
                row['is_modifier'] = bool(getattr(block, 'is_modifier', False))
                rows.append(row)
    return rows


def dependency_path(file, name):
    path = Path(file['path']).parent / name[2:] if name.startswith('//') else Path(name)
    return path.resolve()


def bind_dependencies(rows, files=None):
    """Bind each valid closure independently; a rejected dependency never hides unrelated catalog entries.

    files includes successfully inspected files without asset-marked IDs, which may still supply dependencies.
    """
    files = {file['path']: file for file in files} if files is not None else {r['file']['path']: r['file'] for r in rows}
    accepted, omitted = [], 0
    for row in rows:
        pending, found = [row['file']], {}
        try:
            while pending:
                file = pending.pop()
                if file['path'] in found:
                    continue
                found[file['path']] = file
                if len(found) > 128:
                    raise ValueError('Too many bundled dependencies')
                for path in file.get('dependencies', ()):
                    if path not in files:
                        raise ValueError('A bundled asset depends on an unindexed file')
                    pending.append(files[path])
        except ValueError:
            omitted += 1
            continue
        row['files'] = tuple(found[key] for key in sorted(found))
        revision = json_revision(row['files'])
        row['id'] = index.identity(row['origin'], row['file']['relative'], row['id_type'], row['name'], revision)
        accepted.append(row)
    return accepted, omitted


def json_revision(files):
    return hashlib.sha256(''.join(f['relative'] + ':' + f['sha256'] + '\n' for f in files).encode()).hexdigest()


class Listing:
    """One native listing sync. Returns completed metadata or a bounded error, never asset payloads."""
    def __init__(self, root, url):
        from _bpy_internal.assets.remote_library import listing_downloader, sync_mutex
        self.owner, self.done, self.error = None, False, False
        root.mkdir(parents=True, exist_ok=True)
        if listing_downloader.is_more_recent_than(root, 86400):
            self.done = True
            return
        # The native Asset Browser may already be syncing. Its completed listing can be read after its lock ends.
        if root in sync_mutex._mutex_locks or not sync_mutex.mutex_lock(root):
            self.waiting = root
            return
        self.waiting = None

        def finish(owner):
            self.error = owner.status != listing_downloader.DownloadStatus.FINISHED_SUCCESSFULLY
            self.done = True
            sync_mutex.mutex_unlock(root)

        try:
            self.owner = native_call(listing_downloader.RemoteAssetListingDownloader,
                                     remote_url=url, local_path=root,
                                     on_update_callback=lambda _owner: None, on_done_callback=finish)
            self.owner.download_and_process()
        except Exception:
            sync_mutex.mutex_unlock(root)
            raise

    def ready(self):
        if self.done:
            return True
        if self.owner is not None:
            return False
        from _bpy_internal.assets.remote_library import sync_mutex
        root = self.waiting
        if root in sync_mutex._mutex_locks or not sync_mutex.mutex_lock(root):
            return False
        sync_mutex.mutex_unlock(root)
        self.done = True
        return True

    def cancel(self):
        if self.owner is not None and not self.done:
            self.owner.cancel_and_shutdown()


def get(identity):
    row = _rows.get(identity)
    if row is None:
        raise RefusalError('This Blender asset selection expired. Search Blender Essentials again.', code='EXPIRED')
    if row['origin'] == 'online' and not configuration()[4]:
        raise RefusalError('Online Essentials is disabled in Preferences > Asset Libraries.', code='UNSUPPORTED')
    return row


def publish(bundled, remote, *, files=None, notes=()):
    global _bundled, _bundled_files, _bundled_notes
    files = tuple(files) if files is not None else tuple(row['file'] for row in bundled)
    accepted, omitted = bind_dependencies(bundled, files)
    _bundled, _bundled_files = tuple(accepted), files
    _bundled_notes = tuple(dict.fromkeys(notes))
    if omitted:
        _bundled_notes += ('Some bundled assets have unavailable dependencies and were omitted.',)
    _rows.clear()
    _rows.update((row['id'], row) for row in (*accepted, *remote))


def reset():
    global _bundled, _bundled_files, _bundled_notes
    _bundled, _bundled_files, _bundled_notes = None, (), ()
    _rows.clear()
