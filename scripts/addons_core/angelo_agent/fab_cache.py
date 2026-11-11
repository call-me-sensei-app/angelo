# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Account-scoped, bounded Fab download cache. Blocking worker code; no network client and no bpy.

The account adapter supplies a context-managed byte stream only AFTER current authorization. A local SHA-256
receipt detects later corruption; it is not a publisher signature or evidence of ownership. All file names are
ours, never Fab text. Use the public library's leases/trim but never its public-host download allowlist.

A self-contained GLB is the payload itself (``asset.glb``). Every other format of ``fab_formats`` is unpacked
(``fab_archive``) and converted by a sandboxed background Blender (``fab_convert``) into ``asset.blend``: the only
file the app then reads. The conversion is cached beside the download under its own receipt (download digest and
converter version), so adding the same file again starts no converter; the unpacked files are removed at once.
"""

import hashlib
from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
import tempfile
import threading

from . import fab_formats, library_fetch, shared_replace, team_library_files
from .fab_catalog import FabError

CHUNK = 1024 * 1024
CACHE_BYTES = 4 * 1024 ** 3
# Serialize this first implementation's cache transfers, including across sessions sharing a cache root.
# Bounded network waits belong to the transport; waiting for this lock remains cancellable.
_transfer_lock = threading.Lock()


@dataclass(frozen=True, repr=False, slots=True)
class PayloadProof:
    """Private cache-worker result, not an import certificate or publisher signature.

    The existing lease must remain live through native FD sealing and consume. The native owner must compare
    the actual opened bytes to this frozen size/digest; a retained path/hash alone grants no native authority.
    """
    path: Path
    size: int
    sha256: str
    relative: str = 'asset.glb'


def _regular(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise FabError('CACHE_UNAVAILABLE')


def _digest(path, size, check):
    _regular(path)
    if path.stat().st_size != size:
        return None
    digest, total = hashlib.sha256(), 0
    with shared_replace.open_file(path) as handle:
        while chunk := handle.read(CHUNK):
            check()
            total += len(chunk)
            if total > size:
                return None
            digest.update(chunk)
    return digest.hexdigest() if total == size else None


def _cached(folder, variant, check):
    path, record = folder / 'download.bin', folder / 'receipt.json'
    try:
        _regular(record)
        if record.stat().st_size > 1024:
            return None
        with shared_replace.open_file(record) as handle:
            saved = json.loads(handle.read(1025).decode('utf8'))
        if not isinstance(saved, dict) or saved.get('bytes') != variant.size:
            return None
        digest = _digest(path, variant.size, check)
        if digest is None or digest != saved.get('sha256'):
            return None
        return path, digest
    except (FileNotFoundError, ValueError, UnicodeError):
        return None


def _directory(path):
    # Check ancestors BEFORE creating descendants, so an existing link cannot redirect mkdir outside the cache.
    for part in (*reversed(path.parents), path):
        try:
            info = part.lstat()
        except FileNotFoundError:
            part.mkdir(mode=0o700)
            info = part.lstat()
        if not stat.S_ISDIR(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise FabError('CACHE_UNAVAILABLE')


def _inspect(path, fmt):
    """Reject content that is not the chosen format before it is published to the cache."""
    with shared_replace.open_file(path) as handle:
        head = handle.read(65536)
    try:
        kind = fab_formats.sniff(head, path.stat().st_size)
    except fab_formats.Unsupported:
        raise FabError('UNSUPPORTED') from None
    if fmt.direct:
        facts = team_library_files.inspect(str(path), 'model')
        if facts['format'] != 'glb':
            raise FabError('UNSUPPORTED')
        return {'model': path, 'format': 'glb', 'maps': {}}
    if not ((fmt.archive and kind == 'zip') or (fmt.bare and kind in fmt.members)
            or (fmt.code == 'usd' and kind == 'usdz')):
        raise FabError('UNSUPPORTED')
    return {'format': fmt.code, 'container': 'zip' if kind == 'zip' else 'file', 'content': kind}


def prepare(root, account_scope, item, variant, stream, check, progress=None):
    """Return (facts, lease, cache_hit, payload_proof), with a lease held through native sealing and import.

    ``stream()`` returns a context manager yielding an iterator of bytes (max CHUNK per chunk), closed on all
    outcomes. ``check`` raises when stopped/session invalidated/deadline exceeded. Caller already authorized this
    exact variant and revision; even cache hits must be authorized before entering this function.
    """
    key = json.dumps([account_scope, item.identity, variant.identity, variant.revision, variant.size])
    folder = Path(root).resolve() / 'fab' / 'files' / hashlib.sha256(key.encode()).hexdigest()
    lease = library_fetch.Lease(folder)
    locked = False
    try:
        check()
        while not _transfer_lock.acquire(timeout=0.1):
            check()
        locked = True
        check()
        _directory(folder.parent)
        _directory(folder)
        fmt = fab_formats.BY_CODE.get(variant.format)
        if fmt is None:
            raise FabError('UNSUPPORTED')
        cached = _cached(folder, variant, check)
        hit = cached is not None
        path, digest = cached if hit else _download(folder, variant, fmt, stream, check, progress)
        facts = _inspect(path, fmt)
        check()
        if fmt.direct:
            proof = PayloadProof(path, variant.size, digest)
        else:
            from . import fab_convert
            converted, size, sealed, result = fab_convert.cached(folder, path, digest, fmt, facts, item, check, progress)
            facts = dict(facts, model=converted, conversion=result)
            proof = PayloadProof(converted, size, sealed, 'asset.blend')
        os.utime(folder, None)
        library_fetch.trim(folder.parent, CACHE_BYTES)
        return facts, lease, hit, proof
    except BaseException:
        lease.release()
        raise
    finally:
        if locked:
            _transfer_lock.release()


def _download(folder, variant, fmt, stream, check, progress):
    fd, temporary = tempfile.mkstemp(prefix='.download-', suffix='.part', dir=folder)
    temp = Path(temporary)
    receipt = None
    try:
        digest, done = hashlib.sha256(), 0
        with os.fdopen(fd, 'wb') as handle, stream() as chunks:
            for chunk in chunks:
                check()
                if not isinstance(chunk, bytes) or not 0 < len(chunk) <= CHUNK:
                    raise FabError('CORRUPT')
                done += len(chunk)
                if done > variant.size:
                    raise FabError('LIMIT_EXCEEDED')
                handle.write(chunk)
                digest.update(chunk)
                if progress is not None:
                    progress(done, variant.size)
            if done != variant.size:
                raise FabError('CORRUPT')
        check()
        _inspect(temp, fmt)  # Reject malformed, external-reference, oversized or other-format content first.
        check()
        fd, name = tempfile.mkstemp(prefix='.receipt-', suffix='.part', dir=folder)
        receipt = Path(name)
        with os.fdopen(fd, 'w', encoding='utf8') as handle:
            json.dump({'bytes': done, 'sha256': digest.hexdigest()}, handle)
        path = folder / 'download.bin'
        shared_replace.replace(temp, path, told=False)
        shared_replace.replace(receipt, folder / 'receipt.json', told=False)
        return path, digest.hexdigest()
    finally:
        temp.unlink(missing_ok=True)
        if receipt is not None:
            receipt.unlink(missing_ok=True)
