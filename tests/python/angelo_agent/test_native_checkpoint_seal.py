# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host tests: the keyed checkpoint seal (checkpoint_seal.py).

Every READY record this build publishes is sealed over its version, storage and
order, and its BASE by identity (no read on a request) or content; the key is
created atomically only when sealing, a damaged key is set aside without blocking
captures, and Restore says whether a key is unavailable or a record does not match. Synthetic bytes; Blender never opens them.
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import checkpoint_seal as seal_api, checkpoints  # noqa: E402
from angelo_agent.checkpoint_filesystem import anchored_files_supported  # noqa: E402
from angelo_agent.checkpoint_transactions import _transaction  # noqa: E402
from angelo_agent.checkpoints import CheckpointStore  # noqa: E402
from angelo_agent.snapshot_ingest import IngestError, ingest_snapshot  # noqa: E402

MISMATCH, UNAVAILABLE = "not bound to its own capture", "seal key is missing or damaged, so earlier checkpoints can't be verified"
FOREIGN = "sealed by another Angelo profile's key"


def digest(data):
    return hashlib.blake2b(data, digest_size=32).hexdigest()


@unittest.skipUnless(anchored_files_supported(), "descriptor-relative storage required")
class CheckpointSealTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="angelo-seal-")
        self.store = CheckpointStore(Path(self.temp.name).resolve() / "state", str(uuid.uuid4()))
        self.root = self.store.root
        self.key = self.root / seal_api.FOLDER / seal_api.KEY
        for cache in (seal_api._keys, seal_api._current, seal_api._notices, seal_api._derived, seal_api._owners):
            cache.clear()

    def tearDown(self):
        for cache in (seal_api._keys, seal_api._current, seal_api._notices, seal_api._derived, seal_api._owners):
            cache.clear()
        self.temp.cleanup()

    def capture(self, data=b"BASE bytes of one capture", sealed=True):
        """A READY version 3 full copy, sealed the way publication seals it (or a legacy one)."""
        record, path = self.store.reserve()
        path.write_bytes(data)
        record = self.store.complete(record["id"])
        if sealed:
            with _transaction(self.root):
                record["component_seal"] = seal_api.seal(self.root, record, digest(data))
            self.rewrite(record)
        return self.store.load(record["id"])

    def identity_capture(self, data=b"BASE bytes of an ordinary capture"):
        """A READY version 3 full copy sealed as publication seals it: by its BASE's identity, no read."""
        record, path = self.store.reserve()
        path.write_bytes(data)
        record = self.store.complete(record["id"])
        info = path.stat()
        with _transaction(self.root):
            record["component_seal"] = seal_api.seal_identity(self.root, record, info.st_size, info.st_mtime_ns,
                                                              info.st_ino)
        self.rewrite(record)
        return self.store.load(record["id"])

    def rewrite(self, record):
        (self.store.folder(record["id"]) / "checkpoint.json").write_text(json.dumps(record))

    def recovered(self, record):
        _, target = self.store.recovery_copy(record["id"])
        try:
            return target.read_bytes()
        finally:
            target.unlink()

    def no_working_copies(self):
        return not any((self.store.directory / "recoveries").glob("recovered-*"))

    def test_the_key_is_created_atomically_and_privately_only_by_sealing(self):
        record = dict(self.capture(sealed=False), component_seal="a" * 32 + "." + "b" * 64)
        with _transaction(self.root), self.assertRaisesRegex(ValueError, FOREIGN):  # No key of this profile.
            seal_api.verify(self.root, record, digest(b"x"))
        self.assertFalse((self.root / seal_api.FOLDER).exists(), "verification never creates a key")
        sealed = self.capture()
        self.assertEqual(len(self.key.read_bytes()), seal_api.KEY_BYTES)
        if os.name == "posix":
            self.assertEqual(self.key.stat().st_mode & 0o777, 0o600)
        self.assertEqual(sorted(path.name for path in self.key.parent.iterdir()), ["cutoff.json", "key", "keys.json"])
        self.assertIn(seal_api.FOLDER, checkpoints._PRIVATE_NAMES, "never a file-journal target")
        self.assertEqual(self.recovered(sealed), b"BASE bytes of one capture")

    def test_a_swapped_base_or_downgraded_record_is_refused_before_restore(self):
        first, second = self.capture(b"BASE of the first capture"), self.capture(b"BASE of capture two")
        swapped = self.store.folder(second["id"]) / "scene.blend"
        swapped.write_bytes(b"BASE of the first capture"[:len(b"BASE of capture two")])
        with self.assertRaisesRegex(ValueError, MISMATCH):
            self.recovered(second)
        self.assertTrue(self.no_working_copies())
        for change in ({"version": 2}, {"storage": "store", "scene_hash": digest(b"BASE of capture two")}):
            with self.subTest(change=change):
                self.rewrite(dict(first, **change))
                with self.assertRaises(ValueError):
                    self.recovered(self.store.load(first["id"]))
        self.rewrite({key: value for key, value in first.items() if key != "component_seal"})  # Seal stripped.
        with self.assertRaisesRegex(ValueError, MISMATCH):
            self.recovered(first)
        self.assertFalse(self.store.availability(first["id"])["available"])

    def test_legacy_captures_before_the_cutoff_are_admitted_later_unsealed_ones_are_not(self):
        legacy = self.capture(b"captured before sealing began", sealed=False)
        self.assertEqual(self.recovered(legacy), b"captured before sealing began", "no cutoff yet")
        sealed = self.capture(b"the first sealed capture")
        cutoff = json.loads((self.root / seal_api.FOLDER / seal_api.CUTOFF).read_text())["sealed_from"]
        self.assertEqual(cutoff, sealed["boundary"])
        self.assertLess(legacy["boundary"], cutoff)
        self.assertEqual(self.recovered(legacy), b"captured before sealing began")
        later = self.capture(b"an unsealed capture after the cutoff", sealed=False)
        with self.assertRaisesRegex(ValueError, MISMATCH):
            self.recovered(later)

    def test_a_missing_or_damaged_cutoff_is_derived_from_the_earliest_sealed_record(self):
        legacy = self.capture(b"captured before sealing began", sealed=False)
        sealed = self.capture(b"the first sealed capture")
        cutoff = self.root / seal_api.FOLDER / seal_api.CUTOFF
        for damage in (lambda: cutoff.write_text("{damaged"), cutoff.unlink):
            with self.subTest(damage=damage):
                damage()
                seal_api._derived.clear()
                stripped = self.capture(b"sealed, then stripped of its seal")
                self.rewrite({key: value for key, value in stripped.items() if key != "component_seal"})
                self.assertEqual(self.recovered(legacy), b"captured before sealing began", "still a legacy capture")
                with self.assertRaisesRegex(ValueError, MISMATCH):  # At or after the first sealed record.
                    self.recovered(stripped)
                seal_api._current.clear()
                self.capture(b"a seal rebuilds the cutoff file")
                self.assertEqual(json.loads(cutoff.read_text())["sealed_from"], sealed["boundary"])

    def test_a_deleted_seal_folder_replaces_the_key_with_a_notice_and_keeps_its_records_apart(self):
        lone = self.capture(b"the only capture sealed with the first key")
        shutil.rmtree(self.root / seal_api.FOLDER)
        seal_api._current.clear()
        self.capture(b"sealed with the replacement key")
        self.assertEqual(seal_api.take_notices(self.root), [seal_api.NOTICE_MISSING], "sealed records lost their key")
        seal_api._keys.clear()
        seal_api._owners.clear()
        with self.assertRaisesRegex(ValueError, UNAVAILABLE):  # Its own record's key, relisted with the new key.
            self.recovered(lone)
        kept = self.capture(b"sealed before the key file alone is lost")
        self.key.unlink()
        seal_api._keys.clear()
        seal_api._current.clear()
        seal_api._owners.clear()
        with self.assertRaisesRegex(ValueError, UNAVAILABLE):  # Listed as this profile's key.
            self.recovered(kept)

    def test_a_damaged_key_is_set_aside_with_a_notice_and_never_blocks_a_capture(self):
        old = self.capture(b"sealed with the first key")
        for damaged in (b"", b"x" * 31, b"x" * 33):
            with self.subTest(size=len(damaged)):
                self.key.write_bytes(damaged)
                seal_api._keys.clear()  # A new process.
                seal_api._current.clear()
                with self.assertRaisesRegex(ValueError, UNAVAILABLE):
                    self.recovered(old)
                fresh = self.capture(b"captured after the damage")
                self.assertEqual(seal_api.take_notices(self.root), [seal_api.NOTICE_DAMAGED])
                self.assertEqual(seal_api.take_notices(self.root), [], "each notice once")
                self.assertEqual(len(self.key.read_bytes()), seal_api.KEY_BYTES)
                self.assertTrue(any(path.name.startswith("damaged-") for path in self.key.parent.iterdir()))
                self.assertEqual(self.recovered(fresh), b"captured after the damage")
        with self.assertRaisesRegex(ValueError, UNAVAILABLE):
            self.recovered(old)

    def test_a_lost_key_is_replaced_for_new_captures_and_this_process_still_verifies_its_own(self):
        restored = self.capture(b"a Restore's safety capture")
        self.key.unlink()
        self.assertEqual(self.recovered(restored), b"a Restore's safety capture", "its Undo restore stays possible")
        self.assertFalse(self.key.exists(), "verification never creates the key")
        seal_api._keys.clear()  # A new process.
        seal_api._current.clear()
        with self.assertRaisesRegex(ValueError, UNAVAILABLE):
            self.recovered(restored)
        self.assertEqual(self.store.availability(restored["id"])["reason"][:60], seal_api.KEY_UNAVAILABLE[:60])
        fresh = self.capture(b"captured with the new key")
        self.assertEqual(seal_api.take_notices(self.root), [seal_api.NOTICE_MISSING])
        self.assertEqual(self.recovered(fresh), b"captured with the new key")

    def test_a_record_from_another_profile_is_refused(self):
        other = CheckpointStore(Path(self.temp.name).resolve() / "other", str(uuid.uuid4()))
        record, path = other.reserve()
        path.write_bytes(b"BASE of another profile")
        record = other.complete(record["id"])
        with _transaction(other.root):
            record["component_seal"] = seal_api.seal(other.root, record, digest(b"BASE of another profile"))
        self.capture()  # This profile's own key.
        with _transaction(self.root), self.assertRaisesRegex(ValueError, FOREIGN):
            seal_api.verify(self.root, record, digest(b"BASE of another profile"))
        mixed = dict(record, component_seal=self.capture()["component_seal"][:33] + record["component_seal"][33:])
        with _transaction(self.root), self.assertRaisesRegex(ValueError, MISMATCH):
            seal_api.verify(self.root, mixed, digest(b"BASE of another profile"))

    def test_an_identity_seal_binds_the_base_without_reading_it(self):
        first, second = self.identity_capture(b"BASE of the first capture"), self.identity_capture(b"BASE of capture two")
        self.assertEqual(seal_api.binding(second), "identity")
        self.assertEqual(self.recovered(second), b"BASE of capture two")
        base = self.store.folder(second["id"]) / "scene.blend"
        before = base.stat()
        base.write_bytes(b"BASE of capture 2!!")  # Rewritten in place, same size: a new modification time.
        with self.assertRaisesRegex(ValueError, MISMATCH):
            self.recovered(second)
        self.assertTrue(self.no_working_copies())
        base.write_bytes(b"BASE of capture two")
        os.utime(base, ns=(before.st_atime_ns, before.st_mtime_ns))  # A copy that keeps its time: a backup.
        self.assertEqual(self.recovered(second), b"BASE of capture two")
        (self.store.folder(first["id"]) / "scene.blend").write_bytes(base.read_bytes())  # Another capture's BASE.
        with self.assertRaisesRegex(ValueError, MISMATCH):
            self.recovered(first)
        third = self.identity_capture(b"BASE of capture three")
        own = self.store.folder(third["id"]) / "scene.blend"
        stamp = own.stat()
        swapped = own.with_name("swap.tmp")
        swapped.write_bytes(b"BASE of capture 3!!!!")  # Same size and timestamp, another file: coarse-tick swap.
        os.utime(swapped, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        os.replace(swapped, own)
        with self.assertRaisesRegex(ValueError, MISMATCH):  # Its inode differs.
            self.recovered(third)
        self.rewrite(dict(first, component_seal=first["component_seal"][:32] + "." + first["component_seal"][33:]))
        with self.assertRaisesRegex(ValueError, MISMATCH):  # An identity seal is never a content seal.
            self.recovered(self.store.load(first["id"]))

    def test_a_lost_key_file_is_written_again_from_the_session_key_so_nothing_is_lost(self):
        first = self.identity_capture(b"sealed with the loaded key")
        key = self.key.read_bytes()
        self.key.unlink()  # The single fault: the key file is lost mid-session (a Sculpt-only session too).
        second = self.identity_capture(b"sealed after the key file was lost")
        self.assertEqual(self.key.read_bytes(), key, "the request wrote the file again from this session's key")
        self.assertEqual(second["component_seal"][:32], first["component_seal"][:32])
        self.assertEqual(seal_api.take_notices(self.root), [seal_api.NOTICE_RESTORED])
        self.key.write_bytes(b"x" * 7)  # Damaged: the worker re-reads the file on its seal and writes it again too.
        ingest_snapshot(self.store, second["id"])
        self.assertEqual(self.key.read_bytes(), key)
        self.assertEqual(seal_api.take_notices(self.root), [seal_api.NOTICE_RESTORED])
        stored = self.store.load(second["id"])
        self.assertEqual((seal_api.binding(stored), stored["component_seal"][:32]), ("content", first["component_seal"][:32]))
        seal_api._keys.clear()  # A new process: everything this session sealed still verifies.
        seal_api._current.clear()
        self.assertEqual(self.recovered(first), b"sealed with the loaded key")
        self.assertEqual(self.recovered(stored), b"sealed after the key file was lost")

    def test_a_request_only_stats_its_key_file(self):
        self.identity_capture(b"loads the key once")
        opened = []
        original = seal_api._read_key
        seal_api._read_key = lambda *args: opened.append(args) or original(*args)
        try:
            self.identity_capture(b"seals with the loaded key")
        finally:
            seal_api._read_key = original
        self.assertEqual(opened, [], "no read of the key file on a request once it is loaded")

    def test_a_key_file_replaced_by_another_valid_key_is_adopted_and_strands_nothing(self):
        before = self.identity_capture(b"sealed with this session's key")
        held = before["component_seal"][:32]
        other = os.urandom(seal_api.KEY_BYTES)
        with open(self.key, "r+b") as handle:  # Rewritten in place: same inode and size (a synced profile).
            handle.write(other)
        after = self.identity_capture(b"sealed after the replacement")
        self.assertEqual(after["component_seal"][:32], seal_api._key_id(other), "the profile's file key seals new captures")
        self.assertEqual(self.key.read_bytes(), other, "another valid key is never overwritten")
        self.assertEqual(seal_api.take_notices(self.root), [seal_api.NOTICE_REPLACED])
        self.assertEqual((self.key.parent / seal_api._retired(held)).read_bytes(),
                         seal_api._keys[str(self.root)][held])
        self.identity_capture(b"no second notice")
        self.assertEqual(seal_api.take_notices(self.root), [])
        for cache in (seal_api._keys, seal_api._current, seal_api._owners):
            cache.clear()  # A new process: both keys' records verify.
        self.assertEqual(self.recovered(before), b"sealed with this session's key")
        self.assertEqual(self.recovered(after), b"sealed after the replacement")

    def test_two_processes_on_one_profile_strand_no_record(self):
        first = self.identity_capture(b"process A, before the key file is lost")
        self.key.unlink()
        script = (
            "import hashlib, json, sys\n"
            "from pathlib import Path\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "from angelo_agent import checkpoint_seal as seal_api\n"
            "from angelo_agent.checkpoint_transactions import _transaction\n"
            "from angelo_agent.checkpoints import CheckpointStore\n"
            "store = CheckpointStore(Path(sys.argv[2]), sys.argv[3])\n"
            "record, path = store.reserve()\n"
            "path.write_bytes(b'process B')\n"
            "record = store.complete(record['id'])\n"
            "with _transaction(store.root):\n"
            "    record['component_seal'] = seal_api.seal(store.root, record, hashlib.blake2b(b'process B', digest_size=32).hexdigest())\n"
            "(store.folder(record['id']) / 'checkpoint.json').write_text(json.dumps(record))\n"
            "print(json.dumps(record))\n")
        import subprocess
        result = subprocess.run([sys.executable, "-I", "-c", script, str(Path(seal_api.__file__).parents[1]),
                                 str(Path(self.temp.name).resolve() / "state"), str(uuid.uuid4())],
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        other = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertNotEqual(other["component_seal"][:32], first["component_seal"][:32], "process B started its own key")
        second = self.identity_capture(b"process A, after process B wrote the key file")
        self.assertEqual(second["component_seal"][:32], other["component_seal"][:32], "A adopts the file's key")
        self.assertEqual(seal_api.take_notices(self.root), [seal_api.NOTICE_REPLACED])
        for cache in (seal_api._keys, seal_api._current, seal_api._owners):
            cache.clear()  # Both processes quit: every record of both still verifies.
        self.assertEqual(self.recovered(first), b"process A, before the key file is lost")
        self.assertEqual(self.recovered(second), b"process A, after process B wrote the key file")
        with _transaction(self.root):
            seal_api.verify(self.root, other, digest(b"process B"))

    def test_deduplication_reseals_for_storage_and_refuses_a_mismatched_full_copy(self):
        data = b"deduplicated BASE " * 4096
        record = self.capture(data)
        ingest_snapshot(self.store, record["id"])
        stored = self.store.load(record["id"])
        self.assertEqual((stored["storage"], stored["scene_hash"]), ("store", digest(data)))
        self.assertNotEqual(stored["component_seal"], record["component_seal"], "the seal binds storage")
        self.assertEqual(self.recovered(stored), data)
        self.rewrite(dict(stored, scene_hash=digest(b"other")))
        with self.assertRaises(ValueError):
            self.recovered(self.store.load(record["id"]))
        identity = self.identity_capture(b"identity sealed, then deduplicated " * 4096)
        ingest_snapshot(self.store, identity["id"])
        upgraded = self.store.load(identity["id"])
        self.assertEqual((upgraded["storage"], seal_api.binding(upgraded)), ("store", "content"))
        self.assertEqual(self.recovered(upgraded), b"identity sealed, then deduplicated " * 4096)
        changed = self.identity_capture(b"changed before deduplication " * 4096)
        (self.store.folder(changed["id"]) / "scene.blend").write_bytes(b"changed before deduplicatioN " * 4096)
        with self.assertRaisesRegex(IngestError, "seal does not verify"):
            ingest_snapshot(self.store, changed["id"])
        tampered = self.capture(b"to be deduplicated " * 4096)
        (self.store.folder(tampered["id"]) / "scene.blend").write_bytes(b"another capture BASE" * 3891)
        self.rewrite(dict(tampered, scene_bytes=len(b"another capture BASE" * 3891)))
        with self.assertRaisesRegex(IngestError, "seal does not verify"):
            ingest_snapshot(self.store, tampered["id"])
        self.assertEqual(self.store.load(tampered["id"]).get("storage", "full"), "full", "kept as a full copy")


if __name__ == "__main__":
    unittest.main()
