# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Anchored copy lifetime/integrity only; native decode and Sculpt Restore are separate."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import checkpoint_filesystem as filesystem
from angelo_agent import checkpoint_recovery as recovery
from angelo_agent import checkpoint_seal
from angelo_agent.checkpoints import CheckpointStore
from angelo_agent.checkpoint_capture import CaptureOwner
from fake_nt_files import FakeNtApi
import file_times  # noqa: E402

SESSION = "00000000-0000-4000-8000-000000000001"


class RecoveryResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="angelo-recovery-resource-")
        self.root = Path(self.temp.name).resolve() / "profile"
        self.store = CheckpointStore(self.root, SESSION)
        self.record, path = self.store.reserve()
        self.base, self.runtime = b"BASE recovery fixture", b"COMPANION recovery fixture"
        path.write_bytes(self.base)
        (path.parent / "sculpt.runtime").write_bytes(self.runtime)
        self.record.update(version=4, state="READY", capture_validation="VALIDATED", storage="full",
            scene_bytes=len(self.base), scene_hash=hashlib.blake2b(self.base, digest_size=32).hexdigest(),
            sculpt={"filename":"sculpt.runtime", "format_version":1, "owner_count":2,
                    "bytes":len(self.runtime), "hash":hashlib.blake2b(self.runtime,digest_size=32).hexdigest()})
        # Publication binds the declared pair to this capture with the profile key (checkpoint_seal).
        self.record["component_seal"] = checkpoint_seal.seal(self.root, self.record, self.record["scene_hash"])
        self.metadata = path.parent / "checkpoint.json"
        self.metadata.write_text(json.dumps(self.record))
        self.resources = []

    def tearDown(self):
        try:
            for owner in self.resources:
                if owner._resource is not None and owner._resource.active:
                    owner._native_end()
                owner.close()
        finally:
            self.temp.cleanup()

    def prepare(self):
        owner = recovery.CheckpointRecovery.prepare(self.store, self.record["id"])
        self.resources.append(owner)
        return owner

    def test_actual_pair_copied_and_exact_fds_retained_without_scene_authority(self):
        owner = self.prepare()
        self.assertEqual(owner.path.read_bytes(), self.base)
        returned = owner.record
        returned["sculpt"]["owner_count"] = 99
        self.assertEqual(owner.record["sculpt"]["owner_count"], 2)
        identity, path, base, runtime = owner._native_begin()
        self.assertEqual(identity, self.record["id"])
        self.assertEqual(path, str(owner.path))
        self.assertEqual(os.read(base, len(self.base)), self.base)
        self.assertEqual(os.read(runtime, len(self.runtime)), self.runtime)
        self.assertFalse(os.get_inheritable(base))
        self.assertFalse(os.get_inheritable(runtime))
        with self.assertRaises(ValueError):
            owner.close()
        owner._native_end()
        owner.close()
        self.assertTrue(owner.path.is_file())
        with self.assertRaises(OSError):
            os.fstat(base)

    def rewrite_record(self, **fields):
        record = dict(json.loads(self.metadata.read_text()), **fields)
        self.metadata.write_text(json.dumps(record))
        return record

    def test_unsealed_or_wrongly_sealed_pair_is_refused_before_any_copy(self):
        record = json.loads(self.metadata.read_text())
        record.pop("component_seal")
        self.metadata.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, "not bound to its own capture"):
            self.prepare()
        with self.assertRaisesRegex(ValueError, "Invalid component seal"):  # The record shape refuses it.
            self.rewrite_record(component_seal="0" * 64)
            self.prepare()
        self.rewrite_record(component_seal=self.record["component_seal"][:33] + "0" * 64)
        with self.assertRaisesRegex(ValueError, "not bound to its own capture"):
            self.prepare()
        self.assertFalse(any((self.root / "checkpoints" / SESSION / "recoveries").glob("recovered-*")),
                         "no working copy of an unbound pair is made")

    def test_a_base_from_another_capture_with_rewritten_declarations_is_refused(self):
        other_base = b"BASE of another capture of this document"
        (self.metadata.parent / "scene.blend").write_bytes(other_base)
        self.rewrite_record(scene_bytes=len(other_base),
                            scene_hash=hashlib.blake2b(other_base, digest_size=32).hexdigest())
        with self.assertRaisesRegex(ValueError, "not bound to its own capture"):
            self.prepare()

    def test_a_seal_from_another_profile_key_is_refused(self):
        other = Path(self.temp.name).resolve() / "other-profile"
        self.rewrite_record(component_seal=checkpoint_seal.seal(other, self.record, self.record["scene_hash"]))
        with self.assertRaisesRegex(ValueError, "can't be verified"):  # This profile has no such key.
            self.prepare()
        foreign = checkpoint_seal.seal(other, self.record, self.record["scene_hash"])
        self.rewrite_record(component_seal=self.record["component_seal"][:33] + foreign[33:])
        with self.assertRaisesRegex(ValueError, "not bound to its own capture"):  # This key, another key's HMAC.
            self.prepare()

    def test_the_profile_key_is_private_and_a_damaged_key_refuses_after_restart(self):
        key = self.root / checkpoint_seal.FOLDER / checkpoint_seal.KEY
        if not isinstance(self, RecoveryResourceFakeNtTests):
            if os.name != "nt":  # Windows has no mode bits; the key is private by its profile folder there.
                self.assertEqual(key.stat().st_mode & 0o777, 0o600)
            self.assertEqual(len(key.read_bytes()), checkpoint_seal.KEY_BYTES)
        self.prepare()
        with filesystem._private_directory(self.root / checkpoint_seal.FOLDER) as folder:
            descriptor = filesystem._open_file(folder, checkpoint_seal.KEY, os.O_WRONLY | os.O_TRUNC)
            os.write(descriptor, b"short")
            os.close(descriptor)
        self.prepare()  # This process still holds the key it sealed with (a Restore's Undo restore).
        checkpoint_seal._keys.clear()  # A new process.
        with self.assertRaisesRegex(ValueError, "seal key is missing or damaged, so earlier checkpoints can't be verified"):
            self.prepare()
        self.assertEqual(key.read_bytes(), b"short", "verification never creates or replaces the key")

    def test_each_prepare_has_fresh_working_names(self):
        first, second = self.prepare(), self.prepare()
        self.assertNotEqual(first.path, second.path)
        self.assertEqual(first.path.read_bytes(), second.path.read_bytes())

    def test_destination_hash_return_change_cannot_refresh_verified_seal(self):
        original = recovery._hash_descriptor
        for target_size in (len(self.base), len(self.runtime)):
            with self.subTest(target_size=target_size):
                changed = []
                def after_hash(fd, size):
                    digest = original(fd, size)
                    if size == target_size:
                        before = os.fstat(fd)
                        os.lseek(fd, 0, os.SEEK_SET)
                        os.write(fd, b"X" * size)
                        file_times.set_times(fd, ns=(before.st_atime_ns, before.st_mtime_ns))
                        changed.append(True)
                    return digest
                with patch.object(recovery, "_hash_descriptor", side_effect=after_hash), self.assertRaises(ValueError):
                    self.prepare()
                self.assertEqual(changed, [True])

    def test_plain_or_legacy_record_is_not_admitted(self):
        self.record.update(version=3)
        self.record.pop("capture_validation")
        self.record.pop("sculpt")
        self.metadata.write_text(json.dumps(self.record))
        with self.assertRaises(ValueError):
            self.prepare()

    def test_pending_and_invalid_native_declarations_refuse_before_copy(self):
        for state in ("PENDING", "INVALID"):
            with self.subTest(state=state):
                self.record["capture_validation"] = state
                self.metadata.write_text(json.dumps(self.record))
                with self.assertRaises(ValueError):
                    self.prepare()
        self.assertFalse((self.store.directory / "recoveries").exists())

    def test_interrupted_valid_pair_is_recoverable_input(self):
        self.record["capture_validation"] = "INTERRUPTED_VALID"
        self.metadata.write_text(json.dumps(self.record))
        self.assertEqual(self.prepare().record["capture_validation"], "INTERRUPTED_VALID")

    def test_corrupt_companion_hash_refuses_before_native(self):
        (self.metadata.parent / "sculpt.runtime").write_bytes(b"X" * len(self.runtime))
        with self.assertRaisesRegex(ValueError, "hash"):
            self.prepare()

    def test_truncated_base_refuses_before_native(self):
        (self.metadata.parent / "scene.blend").write_bytes(self.base[:-1])
        with self.assertRaises(ValueError):
            self.prepare()

    def test_short_writes_are_completed(self):
        original = recovery.os.write
        def partial(fd, data):
            return original(fd, data[:max(1,len(data)//2)])
        with patch.object(recovery.os, "write", side_effect=partial):
            owner = self.prepare()
        self.assertEqual(owner.path.read_bytes(), self.base)

    def test_successful_but_corrupt_write_is_caught_by_actual_copy_readback(self):
        original = recovery.os.write
        def corrupt(fd, data):
            return original(fd, b"X" * len(data))
        with patch.object(recovery.os, "write", side_effect=corrupt), self.assertRaisesRegex(ValueError, "verification"):
            self.prepare()

    def test_zero_write_refuses_without_native_borrow(self):
        with patch.object(recovery.os, "write", return_value=0), self.assertRaises(OSError):
            self.prepare()

    def test_working_copy_changes_before_borrow_are_rejected_once(self):
        owner = self.prepare()
        owner.path.write_bytes(b"X" * len(self.base))
        with self.assertRaises(ValueError):
            owner._native_begin()
        owner.path.write_bytes(self.base)
        with self.assertRaises(ValueError):
            owner._native_begin()

    def test_working_copy_name_replacement_is_not_lent(self):
        owner = self.prepare()
        owner.path.unlink()
        owner.path.write_bytes(self.base)
        with self.assertRaises(ValueError):
            owner._native_begin()

    def test_metadata_cannot_retarget_live_record_binding(self):
        owner = self.prepare()
        self.record["original_path"] = "changed"
        self.metadata.write_text(json.dumps(self.record))
        self.assertNotEqual(owner.record["original_path"], "changed")
        self.store.directory = self.root / "foreign"
        self.assertEqual(owner._native_begin()[1], str(owner.path))

    def test_native_borrow_and_release_are_once(self):
        owner = self.prepare()
        owner._native_begin()
        owner._native_end()
        with self.assertRaises(ValueError):
            owner._native_end()
        with self.assertRaises(ValueError):
            owner._native_begin()

    def test_active_capture_cannot_be_prepared_even_with_forged_terminal_metadata(self):
        active = CaptureOwner.reserve(self.store)
        try:
            forged = dict(self.record, id=active.capture_id)
            (self.store.folder(active.capture_id) / "checkpoint.json").write_text(json.dumps(forged))
            with self.assertRaisesRegex(ValueError, "still active"):
                recovery.CheckpointRecovery.prepare(self.store, active.capture_id)
        finally:
            active.abandon()


@unittest.skipIf(os.name == "nt", "POSIX stand-in; native Windows uses base tests")
class RecoveryResourceFakeNtTests(RecoveryResourceTests):
    def setUp(self):
        self.api = FakeNtApi()
        self.backend = patch.object(filesystem, "_windows_api", return_value=self.api)
        self.backend.start()
        self.addCleanup(self.backend.stop)
        super().setUp()


if __name__ == "__main__":
    unittest.main()
