# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Temporary profiles only; payloads test resource lifetime, never native recovery."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import checkpoint_capture as capture
from angelo_agent import checkpoint_capture_records as records
from angelo_agent import checkpoint_filesystem as filesystem
from angelo_agent import checkpoints
from angelo_agent.checkpoint_capture_pins import capture_is_active
from angelo_agent.checkpoint_transactions import _transaction
from fake_nt_files import FakeNtApi

SESSION = "00000000-0000-4000-8000-000000000001"
OTHER = "00000000-0000-4000-8000-000000000002"
BACKUP = "00000000-0000-4000-8000-000000000003.bin"


class CaptureOutputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="angelo-capture-output-")
        self.root = Path(self.temp.name).resolve() / "profile"
        self.store = checkpoints.CheckpointStore(self.root, SESSION)
        self.owners = []

    def tearDown(self):
        try:
            for owner in reversed(self.owners):
                owner.abandon()
        finally:
            self.temp.cleanup()

    def reserve(self):
        owner = capture.CaptureOwner.reserve(self.store)
        self.owners.append(owner)
        return owner

    def output(self, owner, role="BASE"):
        owner.declare_output(role)
        return owner.create_output(role)

    def test_reserve_is_pending_pinned_and_never_usable(self):
        owner = self.reserve()
        record = self.store.load(owner.capture_id)
        self.assertEqual((record["version"], record["state"], record["capture_validation"]), (4, "RESERVED", "PENDING"))
        self.assertNotIn("capture_outputs", record)
        with _transaction(self.root):
            self.assertTrue(capture_is_active(self.root, SESSION, owner.capture_id))
        self.assertFalse(self.store.availability(owner.capture_id)["available"])
        with self.assertRaises(ValueError):
            self.store.complete(owner.capture_id)
        with self.assertRaises(ValueError):
            self.store.save(record)

    def test_empty_existing_directory_cannot_be_adopted_under_trusted_timeline(self):
        self.store.reserve()
        identity = "00000000-0000-4000-8000-000000000099"
        folder = self.store.folder(identity)
        folder.mkdir()
        before = folder.stat()
        with (patch.object(checkpoints.uuid, "uuid4", return_value=uuid.UUID(identity)),
              patch.object(capture, "acquire_capture_pin", wraps=capture.acquire_capture_pin) as pin,
              patch.object(capture, "_write_json", wraps=capture._write_json) as metadata,
              self.assertRaises(FileExistsError)):
            self.reserve()
        after = folder.stat()
        self.assertEqual((before.st_dev, before.st_ino), (after.st_dev, after.st_ino))
        self.assertEqual(list(folder.iterdir()), [])
        self.assertEqual(pin.call_count, 0)
        self.assertEqual(metadata.call_count, 0)

    def test_two_acknowledged_outputs_borrow_once_without_ready(self):
        owner = self.reserve()
        for role in ("BASE", "RUNTIME"):
            output = self.output(owner, role)
            fd = output.borrow_descriptor()
            self.assertFalse(os.get_inheritable(fd))
            info = os.fstat(fd)
            created = self.store.load(owner.capture_id)["capture_outputs"]["roles"][role]["created"]
            self.assertEqual(created, {"device": str(info.st_dev), "inode": str(info.st_ino)})
            os.write(fd, b"resource lifetime only")
            with self.assertRaises(ValueError):
                output.borrow_descriptor()
            self.assertGreater(os.fstat(fd).st_size, 0)
        self.assertEqual(self.store.load(owner.capture_id)["state"], "RESERVED")
        observed = owner.observe_outputs()["outputs"]
        self.assertTrue(all(row["creation_observed"] and row["writer_acknowledged"] for row in observed.values()))
        self.assertFalse(self.store.availability(owner.capture_id)["available"])

    def test_intent_does_not_infer_creation_and_existing_bytes_are_not_overwritten(self):
        owner = self.reserve()
        owner.declare_output("BASE")
        path = self.store.folder(owner.capture_id) / "scene.blend@"
        path.write_bytes(b"unproved residue")
        result = owner.observe_outputs()["outputs"]["BASE"]
        self.assertFalse(result["creation_observed"])
        self.assertFalse(result["writer_acknowledged"])
        self.assertFalse(result["names"]["scene.blend@"]["matches_live_output"])
        with self.assertRaises(FileExistsError):
            owner.create_output("BASE")
        self.assertEqual(path.read_bytes(), b"unproved residue")
        path.unlink()
        with self.assertRaises(ValueError):
            owner.create_output("BASE")
        self.assertIsNone(self.store.load(owner.capture_id)["capture_outputs"]["roles"]["BASE"]["created"])

    def test_mutated_store_cannot_redirect_owner(self):
        owner = self.reserve()
        original = checkpoints.CheckpointStore(self.root, SESSION)
        self.store.root = self.root.parent / "other-profile"
        self.store.session_id = OTHER
        self.store.directory = self.store.root / "checkpoints" / OTHER
        self.output(owner)
        self.assertIn("BASE", original.load(owner.capture_id)["capture_outputs"]["roles"])
        self.assertFalse(self.store.root.exists())

    def test_fresh_journal_and_protection_survive_creation(self):
        owner = self.reserve()
        owner.declare_output("BASE")
        before = self.store.load(owner.capture_id)
        workspace = self.root.parent / "workspace"
        workspace.mkdir()
        entry = {"root": str(workspace), "path": "asset.txt", "existed": True, "backup": BACKUP,
                 "mode": 0o600, "sequence": before["boundary"] + 1, "window": before["boundary"],
                 "phase": "ready", "effect_sequence": before["boundary"] + 1, "effect_kind": "file_tool"}
        self.store._save_journal(before, dict(before, files=[entry]))
        # Protection is mutable, but the purpose of this active capture is not.
        path = self.store.folder(owner.capture_id) / "checkpoint.json"
        protected = json.loads(path.read_text())
        protected["protected"] = True
        path.write_text(json.dumps(protected))
        owner.create_output("BASE")
        after = self.store.load(owner.capture_id)
        self.assertEqual(after["files"], [entry])
        self.assertTrue(after["protected"])
        self.assertEqual(after["purpose"], "request")

    def test_folder_replacement_cannot_refresh_authority(self):
        owner = self.reserve()
        folder = self.store.folder(owner.capture_id)
        moved = folder.with_name(folder.name + "-moved")
        folder.rename(moved)
        folder.mkdir()
        (folder / "checkpoint.json").write_bytes((moved / "checkpoint.json").read_bytes())
        with self.assertRaises(ValueError):
            owner.declare_output("BASE")
        self.assertFalse((folder / "scene.blend@").exists())
        self.assertFalse((moved / "scene.blend@").exists())

    def test_record_rebinding_refuses_before_creation(self):
        owner = self.reserve()
        owner.declare_output("BASE")
        path = self.store.folder(owner.capture_id) / "checkpoint.json"
        value = json.loads(path.read_text())
        value["boundary"] += 1
        path.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            owner.create_output("BASE")
        self.assertFalse(path.with_name("scene.blend@").exists())

    def test_declaration_failure_before_replace_has_no_handoff_or_retry(self):
        owner = self.reserve()
        with patch.object(capture, "_write_json", side_effect=OSError("before replace")), self.assertRaises(OSError):
            owner.declare_output("BASE")
        self.assertNotIn("capture_outputs", self.store.load(owner.capture_id))
        with self.assertRaises(ValueError):
            owner.declare_output("BASE")
        with self.assertRaises(ValueError):
            owner.create_output("BASE")

    def test_directory_sync_failure_after_replace_remains_ambiguous(self):
        owner = self.reserve()
        folder = owner._resource.folder
        original = filesystem.sync_directory
        def fail_after_sync(target):
            original(target)
            if target is folder or target == folder:
                raise OSError("visible metadata, failed acknowledgment")
        with patch.object(filesystem, "sync_directory", side_effect=fail_after_sync), self.assertRaises(OSError):
            owner.declare_output("BASE")
        self.assertIn("BASE", self.store.load(owner.capture_id)["capture_outputs"]["roles"])
        with self.assertRaises(ValueError):
            owner.declare_output("BASE")
        with self.assertRaises(ValueError):
            owner.create_output("BASE")
        self.assertTrue(owner.observe_outputs()["ledger_changed"])

    def test_creation_ack_failure_retains_fd_but_never_hands_it_out(self):
        owner = self.reserve()
        owner.declare_output("BASE")
        original = capture._write_json
        def replace_then_fail(*args):
            original(*args)
            raise OSError("ambiguous metadata acknowledgment")
        with patch.object(capture, "_write_json", side_effect=replace_then_fail), self.assertRaises(OSError):
            owner.create_output("BASE")
        output = owner._resource.outputs["BASE"]
        fd = output._resource.descriptor
        self.assertEqual(os.fstat(fd).st_size, 0)
        self.assertIsNotNone(self.store.load(owner.capture_id)["capture_outputs"]["roles"]["BASE"]["created"])
        with self.assertRaises(ValueError):
            output.borrow_descriptor()
        with self.assertRaises(ValueError):
            owner.create_output("BASE")
        owner.abandon()
        with self.assertRaises(OSError):
            os.fstat(fd)
        self.assertTrue((self.store.folder(owner.capture_id) / "scene.blend@").exists())

    def test_output_sync_failure_leaves_only_declared_intent(self):
        owner = self.reserve()
        owner.declare_output("BASE")
        with patch.object(capture, "sync_directory", side_effect=OSError("output sync failure")), self.assertRaises(OSError):
            owner.create_output("BASE")
        self.assertIsNone(self.store.load(owner.capture_id)["capture_outputs"]["roles"]["BASE"]["created"])
        self.assertTrue(owner.observe_outputs()["failed"])
        self.assertFalse(owner.observe_outputs()["outputs"]["BASE"]["writer_acknowledged"])

    def test_unrelated_reused_descriptor_is_not_handed_out_or_closed(self):
        owner = self.reserve()
        output = self.output(owner)
        fd = output._resource.descriptor
        os.close(fd)
        outside = self.root.parent / "unrelated"
        other = os.open(outside, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        if other != fd:
            os.dup2(other, fd)
            os.close(other)
        try:
            with self.assertRaises((OSError, ValueError)):
                output.borrow_descriptor()
            with self.assertRaises(BaseExceptionGroup):
                owner.abandon()
            os.write(fd, b"still mine")
            self.assertEqual(outside.read_bytes(), b"still mine")
            owner.abandon()
            self.assertEqual(os.fstat(fd).st_size, 10)
        finally:
            os.close(fd)

    def test_closed_descriptor_is_not_retried(self):
        owner = self.reserve()
        output = self.output(owner)
        fd = output._resource.descriptor
        os.close(fd)
        with self.assertRaises((OSError, ValueError)):
            output.borrow_descriptor()
        with self.assertRaises(BaseExceptionGroup):
            owner.abandon()
        owner.abandon()

    def test_cleanup_continues_after_ambiguous_close(self):
        owner = self.reserve()
        first = self.output(owner, "BASE")._resource.descriptor
        second = self.output(owner, "RUNTIME")._resource.descriptor
        original = os.close
        calls = []
        def close_then_fail(fd):
            calls.append(fd)
            original(fd)
            if fd == first:
                raise OSError("closed but raised")
        with patch.object(capture.os, "close", side_effect=close_then_fail), self.assertRaises(BaseExceptionGroup):
            owner.abandon()
        self.assertEqual(calls.count(first), 1)
        self.assertIn(second, calls)
        for fd in (first, second):
            with self.assertRaises(OSError):
                os.fstat(fd)
        with _transaction(self.root):
            self.assertFalse(capture_is_active(self.root, SESSION, owner.capture_id))
        self.assertEqual(len(owner.cleanup_errors), 1)
        owner.abandon()

    def test_abandon_avoids_profile_transaction_and_preserves_metadata(self):
        owner = self.reserve()
        fd = self.output(owner)._resource.descriptor
        folder = self.store.folder(owner.capture_id)
        before = (folder / "checkpoint.json").read_bytes()
        with patch.object(capture, "_transaction", side_effect=RuntimeError("profile unavailable")):
            owner.abandon()
        self.assertEqual((folder / "checkpoint.json").read_bytes(), before)
        self.assertTrue((folder / "scene.blend@").exists())
        with self.assertRaises(OSError):
            os.fstat(fd)

    def test_existing_link_fifo_or_directory_is_not_opened_or_overwritten(self):
        # Windows has no named pipes in the file system namespace (os.mkfifo), so it checks the other three kinds.
        for kind in ("symlink", "hardlink", "fifo", "directory") if hasattr(os, "mkfifo") else ("symlink", "hardlink",
                                                                                                  "directory"):
            with self.subTest(kind=kind):
                owner = self.reserve()
                owner.declare_output("BASE")
                path = self.store.folder(owner.capture_id) / "scene.blend@"
                outside = self.root.parent / (owner.capture_id + "-outside")
                outside.write_bytes(b"keep")
                if kind == "symlink":
                    path.symlink_to(outside)
                elif kind == "hardlink":
                    os.link(outside, path)
                elif kind == "fifo":
                    os.mkfifo(path)
                else:
                    path.mkdir()
                with self.assertRaises(OSError):
                    owner.create_output("BASE")
                self.assertEqual(outside.read_bytes(), b"keep")

    def test_inventory_measures_declared_residue_without_granting_cleanup(self):
        owner = self.reserve()
        owner.declare_output("BASE")
        folder = self.store.folder(owner.capture_id)
        (folder / "scene.blend@").write_bytes(b"intent only")
        result = self.store.inspect()
        self.assertEqual(result["issues"], [])
        item = next(x for x in result["checkpoints"] if x["id"] == owner.capture_id)
        self.assertEqual(item["bytes"], (folder / "checkpoint.json").stat().st_size + 11)
        self.assertFalse(item["scene_usable"])
        self.assertEqual(self.store.incomplete_plan(older_than_seconds=300)["candidates"], [])
        owner.abandon()
        self.assertFalse(self.store.availability(owner.capture_id)["available"])
        with self.assertRaises(ValueError):
            self.store.recovery_copy(owner.capture_id)
        (folder / "unrecognized.tmp").write_bytes(b"unknown")
        self.assertTrue(self.store.inspect()["issues"])

    def test_role_attempts_are_single_use(self):
        owner = self.reserve()
        owner.declare_output("BASE")
        with self.assertRaises(ValueError):
            owner.declare_output("BASE")
        owner.create_output("BASE")
        with self.assertRaises(ValueError):
            owner.create_output("BASE")
        for role in ("OTHER", "../BASE", True):
            with self.assertRaises(ValueError):
                owner.declare_output(role)

    def test_wrong_output_wrapper_cannot_borrow_another_resource(self):
        owner = self.reserve()
        original = self.output(owner)
        forged = object.__new__(capture.OwnedOutput)
        forged._owner, forged._role, forged._resource = owner, "BASE", original._resource
        with self.assertRaises(ValueError):
            forged.borrow_descriptor()
        self.assertIsInstance(original.borrow_descriptor(), int)
        with self.assertRaises(TypeError):
            capture.OwnedOutput()
        with self.assertRaises(TypeError):
            capture.CaptureOwner()

    def test_failed_initial_fstat_closes_the_unexposed_acquisition(self):
        owner = self.reserve()
        owner.declare_output("BASE")
        created = []
        original_open, original_stat = capture._open_file, os.fstat
        def remember(*args):
            descriptor = original_open(*args)
            created.append(descriptor)
            return descriptor
        def fail_output(descriptor):
            if created and descriptor == created[0]:
                raise OSError("initial output fstat failed")
            return original_stat(descriptor)
        with (patch.object(capture, "_open_file", side_effect=remember),
              patch.object(capture.os, "fstat", side_effect=fail_output), self.assertRaises(OSError)):
            owner.create_output("BASE")
        self.assertEqual(len(created), 1)
        self.assertIsNone(self.store.load(owner.capture_id)["capture_outputs"]["roles"]["BASE"]["created"])
        owner.abandon()
        with self.assertRaises(OSError):
            original_stat(created[0])

    def test_replaced_output_name_before_ack_never_grants_writer(self):
        owner = self.reserve()
        owner.declare_output("BASE")
        path = self.store.folder(owner.capture_id) / "scene.blend@"
        moved = path.with_name("original-owned-output")
        original = os.set_inheritable
        def replace(descriptor, value):
            original(descriptor, value)
            path.rename(moved)
            path.write_bytes(b"foreign replacement")
        with patch.object(capture.os, "set_inheritable", side_effect=replace), self.assertRaises(ValueError):
            owner.create_output("BASE")
        self.assertEqual(path.read_bytes(), b"foreign replacement")
        self.assertEqual(moved.read_bytes(), b"")
        owner.abandon()
        self.assertEqual(path.read_bytes(), b"foreign replacement")

    def test_file_sync_error_never_records_creation(self):
        owner = self.reserve()
        owner.declare_output("BASE")
        original = os.fsync
        def fail_output(descriptor):
            output = owner._resource.outputs.get("BASE")
            if output is not None and descriptor == output._resource.descriptor:
                raise OSError("output file sync failed")
            return original(descriptor)
        with patch.object(capture.os, "fsync", side_effect=fail_output), self.assertRaises(OSError):
            owner.create_output("BASE")
        self.assertIsNone(self.store.load(owner.capture_id)["capture_outputs"]["roles"]["BASE"]["created"])
        self.assertFalse(owner.observe_outputs()["outputs"]["BASE"]["writer_acknowledged"])

    def test_legacy_reserve_keeps_single_metadata_write_and_shape(self):
        with patch.object(checkpoints, "_write_json", wraps=checkpoints._write_json) as write:
            record, path = self.store.reserve()
        self.assertEqual(write.call_count, 1)
        self.assertEqual(record["version"], 3)
        self.assertNotIn("capture_validation", record)
        self.assertNotIn("capture_outputs", record)
        path.write_bytes(b"legacy bytes")
        self.assertEqual(self.store.complete(record["id"])["state"], "READY")

    def test_fresh_rereads_and_revalidates_every_changed_record_byte(self):
        owner = self.reserve()
        path = self.store.folder(owner.capture_id) / "checkpoint.json"

        def fresh():
            with owner._operation() as resource, _transaction(self.root):
                return owner._fresh(resource)
        with patch.object(records, "validate_record", wraps=records.validate_record) as validate:
            first = fresh()
            first["state"] = "READY"  # A caller's copy never reaches the next observation.
            first.setdefault("files", []).append("caller edit")
            second = fresh()
            unchanged = validate.call_count
            self.assertEqual((second["state"], second["files"]), ("RESERVED", []))
            self.assertIsNot(second, fresh())
            self.assertEqual(validate.call_count, unchanged)  # Exactly the validated bytes: no second parse.
            value = json.loads(path.read_bytes())
            path.write_text(json.dumps(value, indent=1))  # Same record, other bytes: validated again.
            self.assertEqual(fresh(), second)
            self.assertEqual(validate.call_count, unchanged + 1)
            path.write_text(json.dumps(dict(value, protected=0)))  # Equal in Python, not the exact JSON type.
            with self.assertRaises(ValueError):
                fresh()

    def test_reservation_failure_closes_pin_without_deleting_its_name(self):
        seen = []
        original = capture.acquire_capture_pin
        def remember(*args):
            pin = original(*args)
            seen.append((args[1], args[2]))
            return pin
        with (patch.object(capture, "acquire_capture_pin", side_effect=remember),
              patch.object(capture, "_write_json", side_effect=OSError("reservation failure")),
              self.assertRaises(OSError)):
            self.reserve()
        self.assertEqual(len(seen), 1)
        with _transaction(self.root):
            self.assertFalse(capture_is_active(self.root, *seen[0]))


@unittest.skipIf(os.name == "nt", "POSIX stand-in; native Windows uses the base tests")
class CaptureOutputFakeNtTests(CaptureOutputTests):
    def setUp(self):
        self.api = FakeNtApi()
        self.backend = patch.object(filesystem, "_windows_api", return_value=self.api)
        self.backend.start()
        self.addCleanup(self.backend.stop)
        super().setUp()


if __name__ == "__main__":
    unittest.main()
