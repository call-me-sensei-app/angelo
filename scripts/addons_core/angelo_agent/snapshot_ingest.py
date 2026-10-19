# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Move a completed full-copy request snapshot into the deduplicated store.

Runs on the snapshot worker thread and never touches ``bpy``. Capture itself
is unchanged: Blender's main thread saves ``scene.blend`` and the record becomes
READY. Until this module finishes, that full file IS the checkpoint, so restore
works in every intermediate state:

1. Stream ``scene.blend`` (no-follow, single link, unchanged while read), cut it
   at block headers, hash every segment and the whole file, append new chunks
   and commit a manifest (store lock only).
2. Reassemble the manifest from the store and require the exact whole-file
   BLAKE2b-256 digest and size, verifying every chunk digest too.
3. Under the profile transaction, re-check the record and file identity, record
   ``storage="store"`` and ``scene_hash``, then delete the full copy.

A crash before step 3 leaves a READY full copy (re-ingested at next start; the
manifest is replaced). A crash between the metadata write and the deletion
leaves an extra ``scene.blend`` that ``remove_leftover_copies`` deletes once
the manifest is confirmed. A verification failure keeps the full copy.
Legacy version 1/2 records are never rewritten and stay full copies. Version 4
bundles remain unavailable to ingestion and full-copy cleanup.
"""

import hashlib
import os
import time

from .checkpoint_filesystem import _file_info, _open_file, _private_directory, _unlink_file, same_file, sync_directory
from .checkpoint_capture_state import require_capture_idle
from .checkpoint_policy import checkpoint_ref
from .checkpoint_transactions import _transaction
from .snapshot_blocks import BlendSegmenter
from .snapshot_store import DIGEST_BYTES, SnapshotStore, digest

SCENE = "scene.blend"
READ_BUFFER_BYTES = 8 * 1024 * 1024
LOOKUP_BATCH_BYTES = 8 * 1024 * 1024
LOOKUP_BATCH_SEGMENTS = 256


class IngestError(ValueError):
    """Ingestion did not complete; the full-copy snapshot remains authoritative."""


class IngestCancelled(Exception):
    """The worker is stopping; uncommitted chunk data is discarded."""


def eligible(record):
    """Only ordered (version 3) READY full copies are converted."""
    return (record.get("version", 1) == 3 and record["state"] == "READY"
            and record.get("storage", "full") == "full" and bool(record.get("scene_bytes")))


def _stable(before, after):
    fields = ("st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink")
    return same_file(before, after) and all(getattr(before, name) == getattr(after, name) for name in fields)


def _write_segments(writer, handle, size, cancelled, compress):
    segmenter = BlendSegmenter(handle, size)
    whole = hashlib.blake2b(digest_size=DIGEST_BYTES)
    ordered, batch = [], []
    stats = {"segments": 0, "new_chunks": 0, "new_bytes": 0, "stored_bytes": 0, "read_bytes": 0}
    batch_bytes = 0

    def flush():
        found = writer.lookup([key for key, _ in batch])
        for key, data in batch:
            if key not in found:
                stored = writer.add(key, data, compress=compress)
                if stored:
                    stats["new_chunks"] += 1
                    stats["new_bytes"] += len(data)
                    stats["stored_bytes"] += stored

    for piece in segmenter:
        whole.update(piece)
        key = digest(piece)
        ordered.append(key)
        batch.append((key, piece))
        batch_bytes += len(piece)
        stats["read_bytes"] += len(piece)
        if batch_bytes >= LOOKUP_BATCH_BYTES or len(batch) >= LOOKUP_BATCH_SEGMENTS:
            flush()
            batch, batch_bytes = [], 0
            if cancelled():
                raise IngestCancelled()
    flush()
    stats.update(segments=len(ordered), format=segmenter.format, blocks=segmenter.blocks,
                 fallback=segmenter.fallback)
    return ordered, whole.digest(), stats


def ingest_snapshot(checkpoints, identity, *, store=None, cancelled=None, compress=True):
    """Convert one snapshot; returns measured stats or {"skipped": reason}."""
    try:
        return _ingest(checkpoints, identity, store or SnapshotStore(checkpoints.root), cancelled, compress)
    except IngestError:
        # Retention may remove a point while it is being stored (its full copy
        # is unlinked under the reader). That is not a storage failure.
        if not eligible(checkpoints.load(identity)):
            return {"ref": checkpoint_ref(checkpoints.session_id, identity),
                    "skipped": "the snapshot was removed while it was being stored"}
        raise


def _ingest(checkpoints, identity, store, cancelled, compress):
    cancelled = cancelled or (lambda: False)
    ref = checkpoint_ref(checkpoints.session_id, identity)
    started = time.monotonic()
    descriptor = None
    try:
        # Capture publication and the initial reader admission share the
        # profile transaction; the long hash/store work must not hold it.
        with _transaction(checkpoints.root):
            record = checkpoints.load(identity)
            if not eligible(record):
                return {"ref": ref, "skipped": "not a ready ordered full-copy snapshot"}
            require_capture_idle(checkpoints, identity)
            with _private_directory(checkpoints.folder(identity)) as folder:
                info = _file_info(folder, SCENE)
                descriptor = _open_file(folder, SCENE, os.O_RDONLY)
        if not _stable(info, os.fstat(descriptor)) or info.st_size != record["scene_bytes"]:
            raise IngestError("The snapshot file changed after capture; it was kept as a full copy")
        with os.fdopen(descriptor, "rb", buffering=READ_BUFFER_BYTES, closefd=False) as handle, store.writer() as writer:
            ordered, file_digest, stats = _write_segments(writer, handle, info.st_size, cancelled, compress)
            if stats["read_bytes"] != info.st_size or not _stable(info, os.fstat(descriptor)):
                raise IngestError("The snapshot file changed while it was stored; it was kept as a full copy")
            writer.commit_manifest(ref, file_digest, info.st_size, ordered, record["created"])
    finally:
        if descriptor is not None:
            os.close(descriptor)
    stored_at = time.monotonic()
    try:
        store.reassemble(ref, None, expected_digest=file_digest, expected_size=info.st_size, verify_chunks=True)
    except ValueError as error:
        with store.writer() as writer:
            writer.drop_manifests([ref])
        raise IngestError("Stored snapshot failed verification; the full copy was kept: " + str(error)) from None
    verified_at = time.monotonic()
    result = _adopt(checkpoints, identity, ref, info, file_digest, store)
    stats.update(ref=ref, scene_bytes=info.st_size, store_ms=round((stored_at - started) * 1000, 1),
                 verify_ms=round((verified_at - stored_at) * 1000, 1),
                 total_ms=round((time.monotonic() - started) * 1000, 1), **result)
    return stats


def _adopt(checkpoints, identity, ref, info, file_digest, store):
    """Switch the record to the verified manifest, then delete the full copy."""
    with _transaction(checkpoints.root):
        current = checkpoints.load(identity)
        if not eligible(current):
            # Pruned or changed meanwhile: the manifest is an orphan for GC.
            return {"adopted": False, "reason": "the snapshot changed state before adoption"}
        with _private_directory(checkpoints.folder(identity)) as folder:
            if not _stable(info, _file_info(folder, SCENE)):
                raise IngestError("The snapshot file changed before adoption; it was kept as a full copy")
            if not store.has_manifest(ref, file_digest):
                raise IngestError("The verified manifest disappeared before adoption; the full copy was kept")
            require_capture_idle(checkpoints, identity)
            from .checkpoint_seal import FIELD, seal, verify
            try:  # The stored bytes are the sealed ones (identity seals: the file read was this stable stat).
                verify(checkpoints.root, current, file_digest.hex(), identity=(info.st_size, info.st_mtime_ns, info.st_ino))
            except ValueError as error:
                raise IngestError("The snapshot's seal does not verify; it was kept as a full copy: " + str(error)) from None
            current.update(storage="store", scene_hash=file_digest.hex())
            if FIELD in current:  # The seal binds storage too; a legacy capture stays unsealed.
                current[FIELD] = seal(checkpoints.root, current, file_digest.hex())
            checkpoints.save(current)
            removed = _remove_copy(folder, info)
    return {"adopted": True, "full_copy_removed": removed}


def _remove_copy(folder, info):
    try:
        if not same_file(info, _file_info(folder, SCENE)):
            return False
        _unlink_file(folder, SCENE)
        sync_directory(folder)
        return True
    except OSError:
        return False  # remove_leftover_copies retries once the store is confirmed


def pending_snapshots(checkpoints):
    """Refs of READY full copies awaiting ingestion, oldest first."""
    with _transaction(checkpoints.root):
        records = checkpoints._inventory(validate_targets=False)["checkpoints"]
    ready = [item for item in records if eligible(item) and item.get("scene_usable", True)
             and item.get("capture_active") is not True
             and item.get("boundary")]
    return [(item["session_id"], item["id"]) for item in sorted(ready, key=lambda item: (item["created"], item["ref"]))]


def remove_leftover_copies(checkpoints, store=None):
    """Delete full copies whose store manifest is already authoritative."""
    store = store or SnapshotStore(checkpoints.root)
    removed = 0
    with _transaction(checkpoints.root):
        records = checkpoints._inventory(validate_targets=False)["checkpoints"]
        for item in records:
            if item.get("version") != 3 or item.get("storage") != "store" or not item.get("scene_file"):
                continue
            owner = checkpoints._session_store(item["session_id"])
            try:
                record = owner.load(item["id"])
                if (record.get("version", 1) != 3 or record["state"] != "READY"
                        or record.get("storage", "full") != "store"):
                    continue
                if not store.has_manifest(item["ref"], bytes.fromhex(record["scene_hash"])):
                    continue
                require_capture_idle(owner, item["id"])
            except (OSError, ValueError, TypeError, RecursionError):
                # A busy pin or unreadable current record cannot authorize
                # deletion, but need not prevent cleanup of another record.
                continue
            with _private_directory(owner.folder(item["id"])) as folder:
                try:
                    _file_info(folder, SCENE)  # A regular single-link file only.
                    _unlink_file(folder, SCENE)
                    removed += 1
                except (OSError, ValueError):
                    continue
    return removed
