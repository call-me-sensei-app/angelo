# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Private deduplicated storage for request snapshots.

Layout under the snapshot profile (``<profile>/snapshot-store``, mode 0700):

* ``index.sqlite`` (WAL, synchronous FULL, 0600): pack sizes, one row per
  unique chunk (BLAKE2b-256 digest, location, codec, manifest reference count)
  and one manifest per snapshot (whole-file digest/size and ordered chunk IDs).
* ``packs/pack-<id>.dat`` (0600): append-only chunk data, about 64 MiB each.
* ``store.lock``: inter-process writer lock.

Ownership and locking:

* Only ``StoreWriter`` (snapshot_store_writer.py) mutates the store, while it
  holds the store lock. That lock is never taken by Blender's main thread.
* Packs are append-only. Readers see only committed index rows, so appending
  never disturbs a reader. Garbage collection deletes only packs without any
  referenced chunk. Compaction relocates referenced chunks and therefore also
  holds the profile transaction, which restore holds while it reads.
* Lock order is always profile transaction, then store lock.

Reads never create the store. A missing index simply has no manifests.
Python's sqlite3 opens paths, not descriptors: the directory chain is validated
as non-link private storage and the index files as single-link regular files
immediately before every connection.
"""

import array
import collections
import hashlib
import itertools
import os
import sqlite3
import stat
import sys
import threading
import zlib
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

from .checkpoint_filesystem import _file_info, _open_file, _private_directory, entry_info
from .checkpoint_transactions import lock_descriptor

STORE_NAME = "snapshot-store"
INDEX_NAME = "index.sqlite"
LOCK_NAME = "store.lock"
PACKS_NAME = "packs"
INDEX_FILES = (INDEX_NAME, INDEX_NAME + "-wal", INDEX_NAME + "-shm")
SCHEMA_VERSION = 1
CODEC_RAW = 0
CODEC_ZLIB = 1
DIGEST_BYTES = 32
LOOKUP_BATCH = 500
OPEN_PACK_LIMIT = 64
READ_THREADS = 4
READ_AHEAD_CHUNKS = 32

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS packs(id INTEGER PRIMARY KEY, size INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY, digest BLOB NOT NULL UNIQUE, "
    "pack INTEGER NOT NULL, offset INTEGER NOT NULL, stored INTEGER NOT NULL, raw INTEGER NOT NULL, "
    "codec INTEGER NOT NULL, refs INTEGER NOT NULL DEFAULT 0)",
    "CREATE INDEX IF NOT EXISTS chunks_by_pack ON chunks(pack, offset)",
    "CREATE TABLE IF NOT EXISTS manifests(ref TEXT PRIMARY KEY, file_digest BLOB NOT NULL, "
    "file_size INTEGER NOT NULL, chunk_count INTEGER NOT NULL, chunk_ids BLOB NOT NULL, created REAL NOT NULL)",
)
_THREAD_LOCKS = {}
_THREAD_LOCKS_GUARD = threading.Lock()


class StoreBusy(RuntimeError):
    """Another writer holds the store; the caller retries later."""


def digest(data):
    return hashlib.blake2b(data, digest_size=DIGEST_BYTES).digest()


def pack_name(pack_id):
    return "pack-%010d.dat" % pack_id


def encode_ids(ids):
    values = array.array("q", ids)
    if sys.byteorder != "little":
        values.byteswap()
    return zlib.compress(values.tobytes(), 6)


def decode_ids(blob):
    values = array.array("q")
    values.frombytes(zlib.decompress(blob))
    if sys.byteorder != "little":
        values.byteswap()
    return values


def read_at(descriptor, size, offset):
    """Read exactly ``size`` bytes at ``offset`` or raise ValueError."""
    parts, done = [], 0
    while done < size:
        if hasattr(os, "pread"):
            data = os.pread(descriptor, size - done, offset + done)
        else:  # Windows: descriptors are private to the calling reader.
            os.lseek(descriptor, offset + done, os.SEEK_SET)
            data = os.read(descriptor, size - done)
        if not data:
            raise ValueError("Snapshot pack data is truncated")
        parts.append(data)
        done += len(data)
    return b"".join(parts)


def decode_chunk(stored, codec, raw_size):
    if codec == CODEC_ZLIB:
        data = zlib.decompress(stored)
    elif codec == CODEC_RAW:
        data = stored
    else:
        raise ValueError("Unknown snapshot chunk encoding")
    if len(data) != raw_size:
        raise ValueError("Snapshot chunk has the wrong size")
    return data


class SnapshotStore:
    """Profile-wide chunk store shared by every chat's request snapshots."""

    def __init__(self, profile_root):
        supplied = Path(profile_root).absolute()
        if supplied.is_symlink():
            raise ValueError("Snapshot storage cannot be a directory link")
        self.root = supplied.resolve()
        self.directory = self.root / STORE_NAME
        self.packs = self.directory / PACKS_NAME

    # -- connections -----------------------------------------------------
    def _validate_index(self, create):
        with _private_directory(self.directory, create=create) as folder:
            for name in INDEX_FILES:
                try:
                    info = _index_file_info(folder, name)
                except FileNotFoundError:
                    if name == INDEX_NAME and not create:
                        raise
                    if name == INDEX_NAME:
                        os.close(_open_file(folder, name, os.O_WRONLY | os.O_CREAT | os.O_EXCL))
                    continue
                if os.name != "nt" and info.st_mode & 0o077:  # Windows has no POSIX mode bits.
                    raise ValueError("Snapshot index files must stay private")

    def exists(self):
        try:
            with _private_directory(self.directory) as folder:
                _file_info(folder, INDEX_NAME)
            return True
        except FileNotFoundError:
            return False

    def connect(self, *, create=False):
        """Open the index after validating its directory chain and files. A -wal or -shm that SQLite removes while
        it is checked (another connection closing: ``_index_file_info``) makes the whole check run again, where it is
        absent or valid (``_retried``); one that keeps vanishing is refused like any invalid file."""
        _retried(lambda: self._validate_index(create))
        try:
            connection = sqlite3.connect(str(self.directory / INDEX_NAME), timeout=10,
                                         isolation_level=None, check_same_thread=True)
        except sqlite3.Error as error:
            raise ValueError("The snapshot index is unreadable: " + str(error)) from None
        try:
            connection.execute("PRAGMA synchronous=FULL")
            if create:
                if connection.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() != "wal":
                    raise ValueError("Snapshot index could not enable write-ahead logging")
                for statement in _SCHEMA:
                    connection.execute(statement)
                connection.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('schema', ?)", (SCHEMA_VERSION,))
            row = connection.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
            if not row or row[0] != SCHEMA_VERSION:
                raise ValueError("Unsupported snapshot store version; it was written by a different application version")
        except sqlite3.Error as error:
            connection.close()
            raise ValueError("The snapshot index is unreadable: " + str(error)) from None
        except Exception:
            connection.close()
            raise
        return connection

    @contextmanager
    def reader(self):
        """Yield an index connection, or None when no store exists yet."""
        try:
            connection = self.connect()
        except FileNotFoundError:
            connection = None
        if connection is None:
            yield None
            return
        try:
            yield connection
        except sqlite3.Error as error:
            raise ValueError("The snapshot index is unreadable: " + str(error)) from None
        finally:
            connection.close()

    @contextmanager
    def writer(self, timeout=30):
        """Exclusive writer session (thread and inter-process lock)."""
        from .snapshot_store_writer import StoreWriter
        key = str(self.directory)
        with _THREAD_LOCKS_GUARD:
            thread_lock = _THREAD_LOCKS.setdefault(key, threading.Lock())
        if not thread_lock.acquire(timeout=timeout):
            raise StoreBusy("The snapshot store is busy")
        try:
            with _private_directory(self.packs, create=True):
                pass
            with _private_directory(self.directory) as folder:
                descriptor = _open_file(folder, LOCK_NAME, os.O_RDWR | os.O_CREAT)
                try:
                    _file_info(folder, LOCK_NAME)
                    release = lock_descriptor(descriptor, timeout, "Another app is updating the snapshot store")
                except (OSError, ValueError, RuntimeError) as error:
                    os.close(descriptor)
                    if isinstance(error, RuntimeError):
                        raise StoreBusy(str(error)) from None
                    raise
            try:
                connection = self.connect(create=True)
                try:
                    session = StoreWriter(self, connection)
                    try:
                        session.recover()
                        yield session
                    except sqlite3.Error as error:
                        raise ValueError("The snapshot index rejected an update: " + str(error)) from None
                    finally:
                        session.close()
                finally:
                    connection.close()
            finally:
                release()
                os.close(descriptor)
        finally:
            thread_lock.release()

    # -- reads -----------------------------------------------------------
    def manifest(self, ref):
        """Return (file digest, file size, ordered chunk IDs) or None."""
        with self.reader() as connection:
            if connection is None:
                return None
            row = connection.execute("SELECT file_digest, file_size, chunk_ids FROM manifests WHERE ref=?",
                                     (ref,)).fetchone()
        if row is None:
            return None
        return bytes(row[0]), row[1], decode_ids(row[2])

    def manifest_refs(self):
        with self.reader() as connection:
            if connection is None:
                return set()
            return {row[0] for row in connection.execute("SELECT ref FROM manifests")}

    def has_manifest(self, ref, file_digest=None):
        with self.reader() as connection:
            if connection is None:
                return False
            row = connection.execute("SELECT file_digest FROM manifests WHERE ref=?", (ref,)).fetchone()
        return row is not None and (file_digest is None or bytes(row[0]) == file_digest)

    def locations(self, connection, ids):
        """Map each chunk ID to (pack, offset, stored, raw, codec, digest)."""
        result, wanted = {}, sorted(set(ids))
        for start in range(0, len(wanted), LOOKUP_BATCH):
            batch = wanted[start:start + LOOKUP_BATCH]
            query = ("SELECT id, pack, offset, stored, raw, codec, digest FROM chunks WHERE id IN (%s)"
                     % ",".join("?" * len(batch)))
            for row in connection.execute(query, batch):
                result[row[0]] = (row[1], row[2], row[3], row[4], row[5], bytes(row[6]))
        if len(result) != len(wanted):
            raise ValueError("Snapshot data is incomplete: referenced chunks are missing from the store")
        return result

    def reassemble(self, ref, destination=None, *, expected_digest=None, expected_size=None, verify_chunks=False):
        """Stream a snapshot's exact original bytes; verify the whole-file digest.

        ``destination`` is a writable binary handle or None (verification only).
        Raises ValueError for missing, truncated or corrupted data; nothing is
        reported as restored unless the reconstructed bytes match exactly.
        """
        with self.reader() as connection:
            if connection is None:
                raise ValueError("The snapshot store is missing")
            row = connection.execute("SELECT file_digest, file_size, chunk_ids FROM manifests WHERE ref=?",
                                     (ref,)).fetchone()
            if row is None:
                raise ValueError("The snapshot manifest is missing from the store")
            file_digest, file_size, ids = bytes(row[0]), row[1], decode_ids(row[2])
            if expected_digest is not None and file_digest != expected_digest:
                raise ValueError("The snapshot manifest does not match its recorded file hash")
            if expected_size is not None and file_size != expected_size:
                raise ValueError("The snapshot manifest does not match its recorded size")
            locations = self.locations(connection, ids)
        hasher = hashlib.blake2b(digest_size=DIGEST_BYTES)
        total = 0
        try:
            with _private_directory(self.packs) as folder:
                for data in self._decoded_chunks(folder, ids, locations, verify_chunks):
                    hasher.update(data)
                    total += len(data)
                    if destination is not None:
                        destination.write(data)
        except zlib.error as error:
            raise ValueError("Snapshot chunk data is corrupted: " + str(error)) from None
        if total != file_size or hasher.digest() != file_digest:
            raise ValueError("Reassembled snapshot does not match its recorded hash; it was not used")
        return total, file_digest

    def _decoded_chunks(self, folder, ids, locations, verify):
        """Yield every chunk's original bytes in manifest order.

        Reads, decompression and per-chunk digests release the GIL, so they run
        on a few threads with a bounded look-ahead (at most READ_AHEAD_CHUNKS
        decoded chunks in memory) while the caller hashes and writes in order.
        Without pread, or when a manifest spans more than OPEN_PACK_LIMIT packs,
        chunks are read sequentially with a bounded descriptor cache.
        """
        packs = {locations[chunk_id][0] for chunk_id in locations}
        parallel = hasattr(os, "pread") and len(packs) <= OPEN_PACK_LIMIT and len(ids) > 1
        descriptors = {}

        def load(chunk_id):
            pack, offset, stored, raw, codec, chunk_digest = locations[chunk_id]
            descriptor = descriptors.get(pack)
            if descriptor is None:  # Sequential path only.
                if len(descriptors) >= OPEN_PACK_LIMIT:
                    os.close(descriptors.pop(next(iter(descriptors))))
                descriptor = descriptors[pack] = self._open_pack(folder, pack)
            data = decode_chunk(read_at(descriptor, stored, offset), codec, raw)
            if verify and digest(data) != chunk_digest:
                raise ValueError("Snapshot chunk %d failed verification" % chunk_id)
            return data

        try:
            if not parallel:
                for chunk_id in ids:
                    yield load(chunk_id)
                return
            for pack in sorted(packs):
                descriptors[pack] = self._open_pack(folder, pack)
            pool = ThreadPoolExecutor(max_workers=READ_THREADS, thread_name_prefix="angelo-snapshot-read")
            try:
                pending, remaining = collections.deque(), iter(ids)
                for chunk_id in itertools.islice(remaining, READ_AHEAD_CHUNKS):
                    pending.append(pool.submit(load, chunk_id))
                while pending:
                    data = pending.popleft().result()
                    for chunk_id in itertools.islice(remaining, 1):
                        pending.append(pool.submit(load, chunk_id))
                    yield data
            finally:
                pool.shutdown(wait=True, cancel_futures=True)
        finally:
            for descriptor in descriptors.values():
                os.close(descriptor)

    @staticmethod
    def _open_pack(folder, pack):
        name = pack_name(pack)
        info = _file_info(folder, name)
        descriptor = _open_file(folder, name, os.O_RDONLY)
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            os.close(descriptor)
            raise ValueError("Snapshot pack changed while opening it")
        return descriptor

    def usage(self):
        """Live and physical byte counts; zeros when the store does not exist."""
        result = {"live_bytes": 0, "chunks": 0, "manifests": 0, "pack_bytes": 0, "index_bytes": 0}
        with self.reader() as connection:
            if connection is None:
                return result
            live, chunks = connection.execute(
                "SELECT COALESCE(SUM(stored), 0), COUNT(*) FROM chunks WHERE refs > 0").fetchone()
            result.update(live_bytes=live, chunks=chunks,
                          manifests=connection.execute("SELECT COUNT(*) FROM manifests").fetchone()[0],
                          pack_bytes=connection.execute("SELECT COALESCE(SUM(size), 0) FROM packs").fetchone()[0])
        result["index_bytes"] = _retried(self._index_bytes)
        return result

    def _index_bytes(self):
        total = 0
        with _private_directory(self.directory) as folder:
            for name in INDEX_FILES:
                try:
                    total += _index_file_info(folder, name).st_size
                except FileNotFoundError:
                    pass
        return total


class _Vanished(Exception):
    """A -wal or -shm with no link left: SQLite removed it while it was looked at."""


MAX_INDEX_CHECKS = 8  # Each vanish shows another connection closed; a busy store sees two in a row (review 2026-10-19).


def _retried(check):
    """``check()``, run again while an index side file vanishes during it, at most ``MAX_INDEX_CHECKS`` times; one
    that keeps vanishing past that is refused like any invalid file."""
    for _ in range(MAX_INDEX_CHECKS - 1):
        try:
            return check()
        except _Vanished:
            continue
    try:
        return check()
    except _Vanished:
        raise ValueError("Snapshot storage contains an invalid file/link") from None


def _index_file_info(folder, name):
    """``_file_info`` of an index file, from one look. SQLite itself deletes the write-ahead log and shared-memory
    files (``-wal``, ``-shm``) when the last connection to the index closes, so one seen as a regular file with no
    link left was being removed while it was looked at (2026-10-19: a stored restore point read as unavailable, and
    an ingestion failed its verification, whenever another connection closed during the check; about 1 in 5,000
    checks against a busy worker): ``_Vanished``, and the caller checks once more (``_retried_once``). Everything
    else stays refused as before: the index with no link, a hard link, a symbolic link or another file type."""
    info = entry_info(folder, name)  # One look: a second could catch the removal the first missed.
    if name != INDEX_NAME and stat.S_ISREG(info.st_mode) and info.st_nlink == 0:
        raise _Vanished(name)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("Snapshot storage contains an invalid file/link")  # As _file_info says it.
    return info
