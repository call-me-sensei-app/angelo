# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded Online Essentials thumbnails via Blender's native preview cache and the existing chat image owner.

No .blend download or scene mutation. Paths and hashes are admitted by blender_asset_index; only six previews
are requested, and each copied image is bounded and verified on a worker. Missing previews never hide results.
"""

import hashlib
from pathlib import Path
import shutil
import time

from . import blender_assets as assets, blender_asset_index as index, private_roots

MAX_BYTES = 2 * 1024 * 1024


def _copy(rows, folder, stop, wait):
    from .library_agent import _image_suffix
    pending, found = list(rows), []
    deadline = time.monotonic() + (15 if wait else 0)
    while not stop.is_set():
        for row in list(pending):
            thumb = row['thumbnail']
            try:
                path = Path(thumb['path'])
                if path.is_symlink() or path.stat().st_size > MAX_BYTES:
                    pending.remove(row)
                    continue
                with path.open('rb') as stream:
                    data = stream.read(MAX_BYTES + 1)
                suffix = _image_suffix(data[:12])
                if len(data) > MAX_BYTES or not suffix or hashlib.sha256(data).hexdigest() != thumb['hash']:
                    continue
                target = folder / (str(len(found)) + suffix)
                target.write_bytes(data)
                found.append((row['id'], target))
                pending.remove(row)
            except OSError:
                pass
        if not pending or time.monotonic() >= deadline:
            return found
        stop.wait(0.1)
    return []


class Previews:
    def __init__(self, service, result, rows):
        from .library_agent import _Worker, PREVIEW_FOLDER
        self.service, self.result = service, result
        self.folder = private_roots.allocate(PREVIEW_FOLDER)
        self.worker = None
        root, url, _version, enabled, online = assets.configuration()[1:]
        entries = [row for row in rows[:6] if row.get('thumbnail')]
        try:
            if enabled and online and entries:
                from _bpy_internal.assets.remote_library import asset_downloader as native
                for row in entries:
                    thumb = row['thumbnail']
                    assets.native_call(native.download_preview, asset_library_url=url, asset_library_local_path=root,
                                       preview_url=thumb['relative'], preview_hash='SHA256:' + thumb['hash'],
                                       dst_filepath=index.inside(root, thumb['relative']))
            self.worker = _Worker(lambda stop: _copy(entries, self.folder, stop, enabled and online))
        except Exception:
            self.close()
            raise

    def poll(self):
        if not assets.configuration()[4]:
            self.close()
            return dict(self.result, preview_note='Online Essentials was disabled; no thumbnails were attached.')
        if not self.worker.done.is_set():
            return None
        if not any(item.get('preview_available') for item in self.result['items']):
            return dict(self.result, preview_note='These assets have no online thumbnails. Browse bundled previews in the Asset Browser.')
        return self.service._attach_previews(self.result, self.worker)

    def close(self):
        if self.worker:
            self.worker.cancel.set()
        shutil.rmtree(self.folder, ignore_errors=True)
