# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One private verified Essentials dependency closure, held through both native loads and rollback.

The shared native download cache is not an import lease. Copy and hash on a worker; native inspection and
append then read only this owner's paths. The existing 512 MiB file ceiling also caps this entire copy,
not each member independently. An oversized closure is refused, never partially imported.
"""

import hashlib
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import stat
import threading

from . import blender_asset_index as index, private_roots


class PayloadCopy:
    def __init__(self):
        self.folder = private_roots.allocate('angelo-library-')
        self.started, self.finished, self.closed = threading.Event(), threading.Event(), threading.Event()
        self.lock, self.users = threading.Lock(), 0
        self.removal_started = False
        self.row = None

    def prepare(self, row, cancel):
        """Worker only. Never publish a row until every copied byte has its selected digest."""
        with self.lock:
            self.started.set()
        try:
            files = row.get('files', (row['file'],))
            total = 0
            paths = set()
            for file in files:
                size = file['size']
                path = index.relative(file['relative']).as_posix()
                if type(size) is not int or size <= 0 or size > index.MAX_FILE - total or path in paths:
                    raise ValueError('The complete Essentials import exceeds its 512 MiB staging limit or repeats a path')
                total += size
                paths.add(path)
            copied = {}
            for file in files:
                if cancel.is_set() or self.closed.is_set():
                    raise InterruptedError('Essentials import stopped before its private copy was ready')
                target = index.inside(self.folder, file['relative'])
                target.parent.mkdir(parents=True, exist_ok=True)
                digest, size = hashlib.sha256(), 0
                flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
                with os.fdopen(os.open(file['path'], flags), 'rb') as source, target.open('xb') as destination:
                    info = os.fstat(source.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_size != file['size']:
                        raise ValueError('Essentials source changed before copying')
                    while data := source.read(min(1024 * 1024, file['size'] - size + 1)):
                        if cancel.is_set() or self.closed.is_set():
                            raise InterruptedError('Essentials import stopped while copying')
                        size += len(data)
                        if size > file['size']:
                            raise ValueError('Essentials source grew while copying')
                        digest.update(data)
                        destination.write(data)
                if size != file['size'] or digest.hexdigest() != file['sha256']:
                    raise ValueError('Essentials source no longer matches the selected content')
                copied[file['path']] = dict(file, path=str(target), root=str(self.folder))
            with self.lock:
                if cancel.is_set() or self.closed.is_set():
                    raise InterruptedError('Essentials import stopped before copy publication')
                self.row = dict(row, file=copied[row['file']['path']],
                                files=tuple(copied[file['path']] for file in files))
                return self.row
        finally:
            with self.lock:
                self.finished.set()
                remove = self._claim_cleanup()
            if remove:
                shutil.rmtree(self.folder, ignore_errors=True)

    def owns(self, row):
        return self.finished.is_set() and not self.closed.is_set() and self.row is row

    def _claim_cleanup(self):
        """Called with lock held. Exactly one closer/finalizer owns directory removal."""
        if (self.closed.is_set() and self.users == 0 and not self.removal_started
                and (not self.started.is_set() or self.finished.is_set())):
            self.removal_started = True
            return True
        return False

    @contextmanager
    def hold(self, row):
        """A reentrant Stop may close admission, but cannot remove files during native use or rollback."""
        with self.lock:
            if not self.owns(row):
                raise ValueError('The private Essentials payload is no longer available')
            self.users += 1
        try:
            yield
        finally:
            with self.lock:
                self.users -= 1
                remove = self._claim_cleanup()
            if remove:
                shutil.rmtree(self.folder, ignore_errors=True)

    def close(self):
        """Cancellation cannot unlink a copy still being written by its worker."""
        with self.lock:
            self.closed.set()
            remove = self._claim_cleanup()
        if remove:
            shutil.rmtree(self.folder, ignore_errors=True)
