# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host tests: the deduplicated snapshot chunk store on its own.

Synthetic block layouts (snapshot_fixtures.py); no Blender process. Covers
dedup and sub-chunking, exact reassembly with corruption detection, garbage
collection and compaction, crash leftovers, private permissions, link refusal
and writer exclusion.
"""

import hashlib
import io
import os
import sqlite3
from contextlib import closing
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from snapshot_fixtures import blend_v1, scene_blocks  # noqa: E402
from angelo_agent.checkpoint_filesystem import anchored_files_supported  # noqa: E402
from angelo_agent import snapshot_store_writer  # noqa: E402
from angelo_agent.snapshot_blocks import BlendSegmenter  # noqa: E402
from angelo_agent.snapshot_store import SnapshotStore, StoreBusy, digest, pack_name  # noqa: E402

REF_A = "00000000-0000-4000-8000-00000000000a/00000000-0000-4000-8000-0000000000a1"
REF_B = "00000000-0000-4000-8000-00000000000a/00000000-0000-4000-8000-0000000000b1"


def whole(data):
    return hashlib.blake2b(data, digest_size=32).digest()


def put(store, ref, data, compress=True):
    segments = list(BlendSegmenter(io.BytesIO(data), len(data)))
    keys = [digest(piece) for piece in segments]
    with store.writer() as writer:
        found = writer.lookup(keys)
        written = sum(writer.add(key, piece, compress=compress) for key, piece in zip(keys, segments) if key not in found)
        writer.commit_manifest(ref, whole(data), len(data), keys)
    return written


def restored(store, ref, data=None):
    output = io.BytesIO()
    store.reassemble(ref, output, expected_digest=whole(data) if data is not None else None)
    return output.getvalue()


@unittest.skipUnless(anchored_files_supported(), "descriptor-relative storage required")
class SnapshotStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="angelo-store-")
        self.root = Path(self.temp.name).resolve() / "profile"
        self.root.mkdir()
        self.store = SnapshotStore(self.root)
        self.base = blend_v1(scene_blocks())

    def tearDown(self):
        self.temp.cleanup()

    def pack_files(self):
        return sorted(self.store.packs.iterdir())

    def test_reads_never_create_storage(self):
        self.assertIsNone(self.store.manifest(REF_A))
        self.assertFalse(self.store.has_manifest(REF_A))
        self.assertEqual(self.store.usage()["live_bytes"], 0)
        self.assertEqual(self.store.manifest_refs(), set())
        with self.assertRaisesRegex(ValueError, "missing"):
            self.store.reassemble(REF_A)
        self.assertFalse(self.store.directory.exists())

    def test_unchanged_and_edited_documents_store_only_new_blocks(self):
        first = put(self.store, REF_A, self.base, compress=False)
        self.assertGreaterEqual(first, len(self.base) * 0.9)
        moved = blend_v1(scene_blocks(moved={3}))
        self.assertLess(put(self.store, REF_B, moved, compress=False), 3000)
        self.assertEqual(restored(self.store, REF_A, self.base), self.base)
        self.assertEqual(restored(self.store, REF_B, moved), moved)
        self.assertEqual(put(self.store, REF_B, moved), 0, "re-ingesting replaces the manifest without new data")
        self.assertEqual(self.store.usage()["manifests"], 2)

    def test_partial_edit_of_a_large_array_stores_one_sub_chunk(self):
        put(self.store, REF_A, self.base, compress=False)
        edited = blend_v1(scene_blocks(mesh_offset=2 * 1024 * 1024 + 100))
        written = put(self.store, REF_B, edited, compress=False)
        self.assertLessEqual(written, 1024 * 1024)
        self.assertGreater(written, 512 * 1024)
        self.assertEqual(restored(self.store, REF_B, edited), edited)

    def test_compression_is_used_only_when_it_pays(self):
        compressible = blend_v1([(b"OB\0\0", b"x" * 40000, 1), (b"DATA", bytes(2 * 1024 * 1024), 2)])
        written = put(self.store, REF_A, compressible)
        self.assertLess(written, len(compressible) // 20)
        random_only = blend_v1([(b"DATA", os.urandom(1024 * 1024), 3)])
        self.assertGreaterEqual(put(self.store, REF_B, random_only), 1024 * 1024)
        with closing(sqlite3.connect(str(self.store.directory / "index.sqlite"))) as db, db:
            self.assertEqual({row[0] for row in db.execute("SELECT DISTINCT codec FROM chunks")}, {0, 1})
        self.assertEqual(restored(self.store, REF_A, compressible), compressible)
        self.assertEqual(restored(self.store, REF_B, random_only), random_only)

    def test_any_corruption_is_detected_and_nothing_partial_is_reported(self):
        put(self.store, REF_A, self.base)
        pack = self.pack_files()[0]
        original = pack.read_bytes()
        for offset in (40, len(original) // 2, len(original) - 3):
            damaged = bytearray(original)
            damaged[offset] ^= 0xFF
            pack.write_bytes(bytes(damaged))
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                restored(self.store, REF_A, self.base)
        pack.write_bytes(original[:-10])
        with self.assertRaises(ValueError):
            restored(self.store, REF_A)
        pack.write_bytes(original)
        self.assertEqual(restored(self.store, REF_A, self.base), self.base)
        with self.assertRaisesRegex(ValueError, "recorded file hash"):
            self.store.reassemble(REF_A, expected_digest=whole(b"other"))
        with closing(sqlite3.connect(str(self.store.directory / "index.sqlite"))) as db, db:
            db.execute("DELETE FROM chunks WHERE id = (SELECT MIN(id) FROM chunks)")
        with self.assertRaisesRegex(ValueError, "incomplete"):
            restored(self.store, REF_A)

    def test_chunk_verification_names_the_damaged_chunk(self):
        put(self.store, REF_A, self.base, compress=False)
        pack = self.pack_files()[0]
        damaged = bytearray(pack.read_bytes())
        damaged[100] ^= 1
        pack.write_bytes(bytes(damaged))
        with self.assertRaisesRegex(ValueError, "chunk .* failed verification"):
            self.store.reassemble(REF_A, verify_chunks=True)

    def test_garbage_collection_recounts_and_keeps_every_referenced_chunk(self):
        put(self.store, REF_A, self.base)
        moved = blend_v1(scene_blocks(moved={1, 2}, mesh_offset=0))
        put(self.store, REF_B, moved)
        with closing(sqlite3.connect(str(self.store.directory / "index.sqlite"))) as db, db:
            db.execute("UPDATE chunks SET refs = 0")  # Simulated drift must not delete live data.
        with self.store.writer() as writer:
            self.assertEqual(writer.collect_garbage()["chunks_removed"], 0)
            writer.drop_manifests([REF_A])
            result = writer.collect_garbage()
        self.assertGreater(result["chunks_removed"], 0)
        self.assertEqual(restored(self.store, REF_B, moved), moved)
        with self.store.writer() as writer:
            writer.drop_manifests([REF_B])
            writer.collect_garbage()
        self.assertEqual(self.pack_files(), [])
        self.assertEqual(self.store.usage()["live_bytes"], 0)

    def test_compaction_relocates_live_chunks_and_reclaims_dead_space(self):
        with patch.object(snapshot_store_writer, "PACK_TARGET_BYTES", 512 * 1024), \
                patch.object(snapshot_store_writer, "COMPACT_MIN_DEAD_BYTES", 64 * 1024):
            documents = {}
            for index in range(4):
                ref = REF_A[:-1] + str(index)
                documents[ref] = blend_v1(scene_blocks(objects=10, seed=index, array_bytes=900 * 1024))
                put(self.store, ref, documents[ref], compress=False)
            with self.store.writer() as writer:
                writer.drop_manifests([REF_A[:-1] + "0", REF_A[:-1] + "2"])
                writer.collect_garbage()
                before = self.store.usage()["pack_bytes"]
                candidates = writer.compaction_candidates()
                self.assertTrue(candidates)
                reclaimed = sum(writer.compact_pack(pack) for pack in candidates)
            self.assertGreater(reclaimed, 0)
            self.assertLess(self.store.usage()["pack_bytes"], before)
            for ref in (REF_A[:-1] + "1", REF_A[:-1] + "3"):
                self.assertEqual(restored(self.store, ref, documents[ref]), documents[ref])
                self.store.reassemble(ref, verify_chunks=True)

    def test_crash_leftovers_are_removed_and_committed_data_survives(self):
        put(self.store, REF_A, self.base)
        newest = self.pack_files()[-1]
        committed = newest.stat().st_size
        with open(newest, "ab") as handle:
            handle.write(b"uncommitted tail from a crashed writer")
        orphan = self.store.packs / pack_name(999)
        orphan.write_bytes(b"pack never recorded in the index")
        os.chmod(orphan, 0o600)
        with self.store.writer():
            pass
        self.assertEqual(newest.stat().st_size, committed)
        self.assertFalse(orphan.exists())
        self.assertEqual(restored(self.store, REF_A, self.base), self.base)

    def test_failed_session_discards_uncommitted_chunks(self):
        put(self.store, REF_A, self.base)
        sizes = {path.name: path.stat().st_size for path in self.pack_files()}
        with self.assertRaises(RuntimeError):
            with self.store.writer() as writer:
                writer.add(digest(b"never committed"), os.urandom(300000))
                raise RuntimeError("simulated failure before commit")
        self.assertEqual({path.name: path.stat().st_size for path in self.pack_files()}, sizes)
        with self.store.writer() as writer:
            self.assertEqual(writer.lookup([digest(b"never committed")]), {})

    def test_unknown_or_missing_pack_files_fail_closed(self):
        put(self.store, REF_A, self.base)
        stranger = self.store.packs / "notes.txt"
        stranger.write_text("not ours")
        with self.assertRaisesRegex(ValueError, "Unrecognized"):
            with self.store.writer():
                pass
        self.assertEqual(stranger.read_text(), "not ours")
        stranger.unlink()
        self.pack_files()[0].unlink()
        with self.assertRaisesRegex(ValueError, "missing"):
            with self.store.writer():
                pass

    def test_private_permissions(self):
        put(self.store, REF_A, self.base)
        for path in (self.store.directory, self.store.packs):
            if os.name != "nt":  # POSIX mode bits; on Windows the per-user profile's access list applies
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
        files = [*self.pack_files(), self.store.directory / "index.sqlite", self.store.directory / "store.lock"]
        files += [path for path in self.store.directory.glob("index.sqlite-*")]
        for path in files:
            with self.subTest(path=path.name):
                if os.name != "nt":  # POSIX mode bits; on Windows the per-user profile's access list applies
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_links_are_refused_and_never_followed(self):
        put(self.store, REF_A, self.base)
        outside = self.root.parent / "outside"
        outside.mkdir()
        pack = self.pack_files()[0]
        moved = outside / "real.dat"
        pack.rename(moved)
        pack.symlink_to(moved)
        with self.assertRaises(ValueError):
            restored(self.store, REF_A)
        pack.unlink()
        moved.rename(pack)
        index = self.store.directory / "index.sqlite"
        index.rename(outside / "index.sqlite")
        index.symlink_to(outside / "index.sqlite")
        with self.assertRaises(ValueError):
            self.store.manifest(REF_A)
        with self.assertRaises(ValueError):
            with self.store.writer():
                pass
        linked_root = self.root.parent / "linked-profile"
        linked_root.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            SnapshotStore(linked_root)
        other = SnapshotStore(self.root.parent / "second")
        (self.root.parent / "second").mkdir()
        (self.root.parent / "second" / "snapshot-store").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            put(other, REF_A, self.base)
        self.assertEqual(sorted(path.name for path in outside.iterdir()), ["index.sqlite"])

    def test_only_one_writer_at_a_time(self):
        entered, release = threading.Event(), threading.Event()

        def hold():
            with self.store.writer():
                entered.set()
                release.wait(5)

        worker = threading.Thread(target=hold)
        worker.start()
        try:
            self.assertTrue(entered.wait(5))
            with self.assertRaises(StoreBusy):
                with self.store.writer(timeout=0.1):
                    pass
        finally:
            release.set()
            worker.join(5)
        with self.store.writer():
            pass

    def test_unsupported_schema_is_refused(self):
        put(self.store, REF_A, self.base)
        with closing(sqlite3.connect(str(self.store.directory / "index.sqlite"))) as db, db:
            db.execute("UPDATE meta SET value = 99 WHERE key = 'schema'")
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            self.store.manifest(REF_A)


@unittest.skipUnless(anchored_files_supported(), "descriptor-relative storage required")
class IndexSideFileTests(unittest.TestCase):
    """SQLite deletes the index's -wal and -shm when the last connection closes; a reader validating the index
    files then can see one with no link left (2026-10-19: a stored restore point read as unavailable, and an
    ingestion failed its verification, about once in 5,000 checks against a busy snapshot worker)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="angelo-index-")
        self.root = Path(self.temp.name).resolve() / "profile"
        self.root.mkdir()
        self.store = SnapshotStore(self.root)
        self.data = blend_v1(scene_blocks())
        put(self.store, REF_A, self.data)
        self.addCleanup(self.temp.cleanup)

    def seen(self, name, looks=None, **changes):
        """The store's next ``looks`` looks at ``name`` (every look when None) return its real stat with ``changes``."""
        from contextlib import ExitStack
        import angelo_agent.checkpoint_filesystem as filesystem
        import angelo_agent.snapshot_store as module
        real, left = filesystem.entry_info, [looks]

        def look(folder, entry):
            info = real(folder, entry)
            if entry != name or left[0] == 0:
                return info
            if left[0] is not None:
                left[0] -= 1
            values = list(info[:10])
            values[3] = changes.get("nlink", values[3])
            values[0] = changes.get("mode", values[0])
            return os.stat_result(values)
        stack = ExitStack()
        for owner in (filesystem, module):  # Wherever the store takes its look from.
            if hasattr(owner, "entry_info"):
                stack.enter_context(patch.object(owner, "entry_info", look))
        return stack

    def present(self):
        """Keep a connection open so SQLite's -wal and -shm exist."""
        with self.store.writer():
            pass
        connection = sqlite3.connect(str(self.store.directory / "index.sqlite"))
        self.addCleanup(connection.close)
        connection.execute("SELECT COUNT(*) FROM manifests").fetchone()
        return all((self.store.directory / name).exists() for name in ("index.sqlite-wal", "index.sqlite-shm"))

    def test_a_side_file_removed_while_it_is_checked_is_checked_again(self):
        self.assertTrue(self.present())
        for name in ("index.sqlite-wal", "index.sqlite-shm"):
            with self.subTest(name), self.seen(name, looks=1, nlink=0):
                self.assertTrue(self.store.has_manifest(REF_A, whole(self.data)))
            with self.subTest(name, usage=True), self.seen(name, looks=1, nlink=0):
                self.assertGreater(self.store.usage()["index_bytes"], 0)

    def test_a_side_file_vanishing_on_several_checks_in_a_row_is_checked_again(self):
        self.assertTrue(self.present())
        from angelo_agent.snapshot_store import MAX_INDEX_CHECKS
        with self.seen("index.sqlite-shm", looks=MAX_INDEX_CHECKS - 1, nlink=0):
            self.assertTrue(self.store.has_manifest(REF_A, whole(self.data)))

    def test_a_side_file_that_keeps_vanishing_is_refused(self):
        self.assertTrue(self.present())
        for name in ("index.sqlite-wal", "index.sqlite-shm"):
            with self.subTest(name), self.seen(name, nlink=0):
                with self.assertRaisesRegex(ValueError, "invalid file/link"):
                    self.store.has_manifest(REF_A)

    def test_every_attack_shape_stays_refused(self):
        self.assertTrue(self.present())
        for name, changes in (("index.sqlite", {"nlink": 0}), ("index.sqlite", {"nlink": 2}),
                              ("index.sqlite-shm", {"nlink": 2}), ("index.sqlite-wal", {"nlink": 2}),
                              ("index.sqlite-shm", {"mode": stat.S_IFIFO | 0o600}),
                              ("index.sqlite-wal", {"mode": stat.S_IFLNK | 0o600, "nlink": 0})):
            with self.subTest(name=name, **changes), self.seen(name, **changes):
                with self.assertRaisesRegex(ValueError, "invalid file/link"):
                    self.store.has_manifest(REF_A)

    def test_real_links_fifos_and_open_modes_in_place_of_a_side_file_are_refused(self):
        outside = Path(self.temp.name) / "outside"
        outside.write_bytes(b"x" * 64)
        os.chmod(outside, 0o600)
        for suffix in ("-wal", "-shm"):
            self.refused_shapes(self.store.directory / ("index.sqlite" + suffix), outside)

    def refused_shapes(self, side, outside):
        shapes = {"symbolic link": lambda: side.symlink_to(outside),
                  "hard link": lambda: os.link(outside, side),
                  "fifo": lambda: os.mkfifo(side, 0o600),
                  "open mode": lambda: (side.write_bytes(b""), os.chmod(side, 0o644))}
        for shape, make in shapes.items():
            with self.subTest(shape, name=side.name):
                if side.exists() or side.is_symlink():
                    side.unlink()
                make()
                with self.assertRaisesRegex(ValueError, "invalid file/link|must stay private"):
                    self.store.has_manifest(REF_A)
                side.unlink()

    def test_connections_closing_never_make_a_stored_snapshot_unreadable(self):
        stop, errors = threading.Event(), []

        def churn():  # Each close of the last connection removes the side files.
            while not stop.is_set():
                try:
                    with self.store.reader() as connection:
                        connection.execute("SELECT COUNT(*) FROM manifests").fetchone()
                except ValueError as error:
                    errors.append(str(error))
        thread = threading.Thread(target=churn)
        thread.start()
        try:
            deadline = time.monotonic() + 1.5
            while time.monotonic() < deadline:
                try:
                    self.assertTrue(self.store.has_manifest(REF_A, whole(self.data)))
                except ValueError as error:
                    errors.append(str(error))
        finally:
            stop.set()
            thread.join()
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
