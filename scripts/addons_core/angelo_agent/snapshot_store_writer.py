# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The only mutator of the deduplicated snapshot store.

A ``StoreWriter`` exists only inside ``SnapshotStore.writer()``, which holds the
store lock. Crash safety follows one rule: pack bytes are written and fsynced
before the SQLite transaction that makes them reachable commits. Anything a
crash leaves behind (a pack tail past its committed size, or a pack file the
index never recorded) is unreachable and removed by ``recover()`` at the start
of the next writer session.

Reference counts are a cache for retention accounting. Garbage collection
recomputes them from the manifests before deleting anything, so a drifted count
can never delete a chunk that a manifest still needs.
"""

import os
import re
import sqlite3
from collections import Counter
import time
import zlib

from .checkpoint_filesystem import _file_info, _open_file, _private_directory, _unlink_file, list_names, sync_directory
from .snapshot_store import (CODEC_RAW, CODEC_ZLIB, LOOKUP_BATCH, decode_chunk, decode_ids, digest,
                             encode_ids, pack_name, read_at)

PACK_TARGET_BYTES = 64 * 1024 * 1024
# zlib level 1 is used only when it saves at least an eighth of a chunk.
COMPRESSION_LEVEL = 1
COMPRESSION_MIN_SAVING = 8
COMPACT_MIN_DEAD_BYTES = 1024 * 1024
COMPACT_DEAD_FRACTION = 0.25
_PACK_PATTERN = re.compile(r"pack-(\d{10})\.dat")


def _write_all(descriptor, data, offset):
    view = memoryview(data)
    while view:
        if hasattr(os, "pwrite"):
            written = os.pwrite(descriptor, view, offset)
        else:
            os.lseek(descriptor, offset, os.SEEK_SET)
            written = os.write(descriptor, view)
        view = view[written:]
        offset += written


# Stores whose garbage this process collected with no manifest dropped since (keyed by the store's folder).
# Only dropping a manifest (removal, or an ingestion replacing it) leaves chunks unreferenced, so the
# snapshot worker's per-capture cleanup skips the whole-store recount for these (snapshot_maintenance).
# A store not listed (another process's drops, or none collected yet in this process) is collected.
_COLLECTED = set()


def garbage_possible(store):
    """Whether a store may hold unreferenced chunks this process has not yet collected."""
    return str(store.directory) not in _COLLECTED


class StoreWriter:
    def __init__(self, store, connection):
        self.store = store
        self.db = connection
        self._pending = {}      # digest -> (pack, offset, stored, raw, codec)
        self._order = []        # pending digests in insertion order
        self._active = None     # [pack id, descriptor, size]
        self._sizes = {}        # pack id -> uncommitted size
        self._descriptors = {}  # pack id -> open descriptor written this session
        self._created = set()   # pack ids created this session

    # -- lifecycle -------------------------------------------------------
    def close(self):
        try:
            if self._pending or self._sizes:
                self.discard()  # A later recover() also removes these bytes.
        finally:
            for descriptor in self._descriptors.values():
                os.close(descriptor)
            self._descriptors.clear()
            self._active = None

    def recover(self):
        """Remove bytes a crashed writer left unreachable; never touch others."""
        committed = dict(self.db.execute("SELECT id, size FROM packs"))
        present = set()
        with _private_directory(self.store.packs) as folder:
            for name in sorted(list_names(folder)):
                match = _PACK_PATTERN.fullmatch(name)
                if not match:
                    raise ValueError("Unrecognized file in snapshot pack storage; nothing was removed")
                pack = int(match.group(1))
                info = _file_info(folder, name)
                present.add(pack)
                if pack not in committed:
                    _unlink_file(folder, name)
                elif info.st_size > committed[pack]:
                    descriptor = _open_file(folder, name, os.O_RDWR)
                    try:
                        os.ftruncate(descriptor, committed[pack])
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
                elif info.st_size < committed[pack]:
                    raise ValueError("Snapshot pack %d is shorter than its committed size" % pack)
        if set(committed) - present:
            raise ValueError("Snapshot pack data is missing for committed packs")

    # -- ingestion primitives ---------------------------------------------
    def lookup(self, digests):
        """Committed chunk IDs for the given digests."""
        found, wanted = {}, list(dict.fromkeys(digests))
        for start in range(0, len(wanted), LOOKUP_BATCH):
            batch = wanted[start:start + LOOKUP_BATCH]
            query = "SELECT digest, id FROM chunks WHERE digest IN (%s)" % ",".join("?" * len(batch))
            for value, chunk_id in self.db.execute(query, batch):
                found[bytes(value)] = chunk_id
        return found

    def add(self, chunk_digest, raw, *, compress=True):
        """Append one new chunk; returns stored bytes (0 if already pending)."""
        if chunk_digest in self._pending:
            return 0
        codec, stored = CODEC_RAW, raw
        if compress:
            packed = zlib.compress(raw, COMPRESSION_LEVEL)
            if len(packed) <= len(raw) - len(raw) // COMPRESSION_MIN_SAVING - 1:
                codec, stored = CODEC_ZLIB, packed
        pack, descriptor, offset = self._writable_pack()
        _write_all(descriptor, stored, offset)
        self._active[2] = self._sizes[pack] = offset + len(stored)
        self._pending[chunk_digest] = (pack, offset, len(stored), len(raw), codec)
        self._order.append(chunk_digest)
        if self._active[2] >= PACK_TARGET_BYTES:
            self._active = None  # Sealed; its descriptor stays open for fsync.
        return len(stored)

    def _writable_pack(self):
        if self._active is None:
            row = self.db.execute("SELECT id, size FROM packs ORDER BY id DESC LIMIT 1").fetchone()
            committed_newest = row[0] if row else 0
            newest = max([committed_newest, *self._sizes])
            reusable = (row is not None and newest == committed_newest and row[1] < PACK_TARGET_BYTES
                        and newest not in self._descriptors)
            committed_size = row[1] if reusable else None
            with _private_directory(self.store.packs) as folder:
                if reusable:
                    descriptor = _open_file(folder, pack_name(newest), os.O_RDWR)
                    if os.fstat(descriptor).st_size != committed_size:
                        os.close(descriptor)
                        raise ValueError("Snapshot pack size changed during writing")
                    self._descriptors[newest] = descriptor
                    self._active = [newest, descriptor, committed_size]
                else:
                    pack = newest + 1
                    descriptor = _open_file(folder, pack_name(pack), os.O_RDWR | os.O_CREAT | os.O_EXCL)
                    self._descriptors[pack] = descriptor
                    self._created.add(pack)
                    self._active = [pack, descriptor, 0]
        return self._active[0], self._active[1], self._active[2]

    def _sync_packs(self):
        for pack in self._sizes:
            os.fsync(self._descriptors[pack])
        if self._created:
            with _private_directory(self.store.packs) as folder:
                sync_directory(folder)

    def _commit_pending(self):
        """Inside an open transaction: publish synced packs and pending chunks."""
        for pack, size in self._sizes.items():
            self.db.execute("INSERT INTO packs(id, size) VALUES(?, ?) ON CONFLICT(id) DO UPDATE SET size=excluded.size",
                            (pack, size))
        self.db.executemany(
            "INSERT INTO chunks(digest, pack, offset, stored, raw, codec, refs) VALUES(?, ?, ?, ?, ?, ?, 0)",
            [(key, *self._pending[key]) for key in self._order])

    def _finish(self):
        self._pending.clear()
        self._order.clear()
        self._sizes.clear()
        self._created.clear()
        for descriptor in self._descriptors.values():
            os.close(descriptor)
        self._descriptors.clear()
        self._active = None

    def commit_manifest(self, ref, file_digest, file_size, ordered_digests, created=None):
        """Durably publish new chunks and (re)place ``ref``'s manifest."""
        self._sync_packs()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self._commit_pending()
            ids_by_digest = self.lookup(ordered_digests)
            if len(ids_by_digest) != len(set(ordered_digests)):
                raise ValueError("A snapshot chunk disappeared before its manifest was committed")
            ids = [ids_by_digest[value] for value in ordered_digests]
            self._drop(ref)
            self.db.executemany("UPDATE chunks SET refs = refs + 1 WHERE id = ?", [(value,) for value in set(ids)])
            self.db.execute("INSERT INTO manifests(ref, file_digest, file_size, chunk_count, chunk_ids, created) "
                            "VALUES(?, ?, ?, ?, ?, ?)", (ref, file_digest, file_size, len(ids), encode_ids(ids),
                                                          time.time() if created is None else created))
            self.db.execute("COMMIT")
        except BaseException:
            self._rollback()
            raise
        self._finish()
        self._checkpoint_wal()
        return ids

    def discard(self):
        """Forget uncommitted chunks; their bytes are truncated or unlinked."""
        committed = dict(self.db.execute("SELECT id, size FROM packs"))
        with _private_directory(self.store.packs) as folder:
            for pack in list(self._sizes):
                descriptor = self._descriptors.get(pack)
                if pack in self._created:
                    _unlink_file(folder, pack_name(pack))
                elif descriptor is not None:
                    os.ftruncate(descriptor, committed.get(pack, 0))
        self._finish()

    def _rollback(self):
        try:
            self.db.execute("ROLLBACK")
        except sqlite3.Error:
            pass  # No transaction was active; the original error is reported.

    def _checkpoint_wal(self):
        try:
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass  # A busy reader only delays log truncation; data is durable.

    # -- removal ----------------------------------------------------------
    def _drop(self, ref):
        row = self.db.execute("SELECT chunk_ids FROM manifests WHERE ref=?", (ref,)).fetchone()
        if row is None:
            return False
        self.db.executemany("UPDATE chunks SET refs = refs - 1 WHERE id = ?",
                            [(value,) for value in set(decode_ids(row[0]))])
        self.db.execute("DELETE FROM manifests WHERE ref=?", (ref,))
        _COLLECTED.discard(str(self.store.directory))  # Even if this rolls back: collecting then frees nothing.
        return True

    def drop_manifests(self, refs):
        """Remove manifests; their chunks become garbage once unreferenced."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            dropped = sum(self._drop(ref) for ref in refs)
            self.db.execute("COMMIT")
        except BaseException:
            self._rollback()
            raise
        return dropped

    def collect_garbage(self):
        """Recount references from manifests, then delete unreferenced data."""
        # Counted in C per manifest. No pause here: this holds the store's writer lock, which another app's
        # ingestion waits for, and it runs only after a dropped manifest (snapshot_maintenance).
        counts = Counter()
        for (blob,) in self.db.execute("SELECT chunk_ids FROM manifests"):
            counts.update(set(decode_ids(blob)))
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute("UPDATE chunks SET refs = 0")
            self.db.executemany("UPDATE chunks SET refs = ? WHERE id = ?", [(n, key) for key, n in counts.items()])
            freed, removed = self.db.execute(
                "SELECT COALESCE(SUM(stored), 0), COUNT(*) FROM chunks WHERE refs = 0").fetchone()
            self.db.execute("DELETE FROM chunks WHERE refs = 0")
            empty = [row[0] for row in self.db.execute(
                "SELECT id FROM packs WHERE id NOT IN (SELECT DISTINCT pack FROM chunks)")]
            self.db.executemany("DELETE FROM packs WHERE id = ?", [(pack,) for pack in empty])
            self.db.execute("COMMIT")
        except BaseException:
            self._rollback()
            raise
        with _private_directory(self.store.packs) as folder:
            for pack in empty:
                try:
                    _unlink_file(folder, pack_name(pack))
                except FileNotFoundError:
                    pass
        self._checkpoint_wal()
        _COLLECTED.add(str(self.store.directory))  # Under the store lock: no drop came in between.
        return {"chunks_removed": removed, "bytes_released": freed, "packs_removed": len(empty)}

    def compaction_candidates(self):
        """Sealed packs whose unreferenced space is worth rewriting."""
        newest = self.db.execute("SELECT MAX(id) FROM packs").fetchone()[0]
        rows = self.db.execute("SELECT packs.id, packs.size, COALESCE(SUM(chunks.stored), 0) FROM packs "
                               "LEFT JOIN chunks ON chunks.pack = packs.id GROUP BY packs.id")
        result = [(size - live, pack) for pack, size, live in rows if pack != newest and
                  size - live >= max(COMPACT_MIN_DEAD_BYTES, size * COMPACT_DEAD_FRACTION)]
        return [pack for _, pack in sorted(result, reverse=True)]

    def compact_pack(self, pack):
        """Relocate one pack's live chunks. Caller holds the profile transaction.

        Readers of chunk data (restore) hold that transaction, so no reader can
        be between an index lookup and a pack read while the pack is replaced.
        """
        rows = self.db.execute("SELECT id, offset, stored, raw, codec, digest FROM chunks WHERE pack = ? "
                               "ORDER BY offset", (pack,)).fetchall()
        size = self.db.execute("SELECT size FROM packs WHERE id = ?", (pack,)).fetchone()
        if size is None:
            return 0
        moved = []
        with _private_directory(self.store.packs) as folder:
            source = self.store._open_pack(folder, pack)
            try:
                for chunk_id, offset, stored, raw, codec, chunk_digest in rows:
                    data = read_at(source, stored, offset)
                    if digest(decode_chunk(data, codec, raw)) != bytes(chunk_digest):
                        raise ValueError("Snapshot chunk %d failed verification during compaction" % chunk_id)
                    target, descriptor, target_offset = self._writable_pack()
                    if target == pack:
                        raise ValueError("Compaction cannot write into its own source pack")
                    _write_all(descriptor, data, target_offset)
                    self._active[2] = self._sizes[target] = target_offset + stored
                    if self._active[2] >= PACK_TARGET_BYTES:
                        self._active = None
                    moved.append((target, target_offset, chunk_id, pack, offset))
            finally:
                os.close(source)
        self._sync_packs()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            for target, size_value in self._sizes.items():
                self.db.execute("INSERT INTO packs(id, size) VALUES(?, ?) ON CONFLICT(id) DO UPDATE SET size=excluded.size",
                                (target, size_value))
            self.db.executemany("UPDATE chunks SET pack = ?, offset = ? WHERE id = ? AND pack = ? AND offset = ?", moved)
            if self.db.execute("SELECT COUNT(*) FROM chunks WHERE pack = ?", (pack,)).fetchone()[0]:
                raise ValueError("Snapshot pack changed during compaction")
            self.db.execute("DELETE FROM packs WHERE id = ?", (pack,))
            self.db.execute("COMMIT")
        except BaseException:
            self._rollback()
            self.discard()
            raise
        self._finish()
        with _private_directory(self.store.packs) as folder:
            _unlink_file(folder, pack_name(pack))
        self._checkpoint_wal()
        return size[0] - sum(row[2] for row in rows)
