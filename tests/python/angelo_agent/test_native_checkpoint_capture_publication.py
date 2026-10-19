# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Real temporary-file publication with a fake native witness; no Sculpt fidelity claim."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import checkpoint_capture as capture
from angelo_agent import checkpoint_filesystem as filesystem
from angelo_agent.checkpoints import CheckpointStore
from fake_nt_files import FakeNtApi
import file_times  # noqa: E402

SESSION = "00000000-0000-4000-8000-000000000001"


class CapturePublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="angelo-capture-publication-")
        self.root = Path(self.temp.name).resolve() / "profile"
        self.store = CheckpointStore(self.root, SESSION)
        self.owner = capture.CaptureOwner.reserve(self.store)
        self.capability = object()
        self.facts = None
        self.native = types.SimpleNamespace(capture_facts=self.native_facts)
        self.module = patch.dict(sys.modules, {"_bpy": types.SimpleNamespace(_angelo_checkpoint=self.native)})
        self.module.start()

    def tearDown(self):
        try:
            if self.owner._resource is not None and self.owner._resource.native_active:
                self.owner._native_end()
            self.owner.abandon()
        finally:
            self.module.stop()
            self.temp.cleanup()

    def native_facts(self, capability, owner):
        if capability is not self.capability or owner is not self.owner:
            raise ValueError("Not the bound native witness")
        return dict(self.facts)

    def prepare(self, *, runtime=True):
        identity, path = self.owner._native_begin()
        self.assertEqual(identity, self.owner.capture_id)
        self.assertEqual(Path(path).name, "scene.blend")
        self.base = b"BASE storage fixture, not a native blend"
        self.runtime = b"RUNTIME storage fixture, not a native companion" if runtime else b""
        self.base_fd = self.owner._native_output("BASE")
        os.write(self.base_fd, self.base)
        if runtime:
            self.runtime_fd = self.owner._native_output("RUNTIME")
            os.write(self.runtime_fd, self.runtime)
        self.facts = {"capture_id": identity, "scene_bytes": len(self.base),
            "runtime_bytes": len(self.runtime), "owner_count": 2 if runtime else 0,
            "format_version": 1 if runtime else 0, "post_state": "pending"}

    def record(self):
        return self.store.load(self.owner.capture_id)

    def rewrite(self, change):
        path = self.store.folder(self.owner.capture_id) / "checkpoint.json"
        record = json.loads(path.read_text())
        change(record)
        path.write_text(json.dumps(record))

    def test_pair_is_hashed_published_then_finished_after_post(self):
        self.prepare()
        offset = os.lseek(self.base_fd, 0, os.SEEK_CUR)
        receipt = self.owner.publish(self.capability)
        self.assertEqual(receipt["scene_hash"], hashlib.blake2b(self.base, digest_size=32).hexdigest())
        self.assertEqual(receipt["sculpt"]["hash"], hashlib.blake2b(self.runtime, digest_size=32).hexdigest())
        self.assertEqual(os.lseek(self.base_fd, 0, os.SEEK_CUR), offset)
        self.assertEqual((self.record()["state"], self.record()["capture_validation"]), ("READY", "PENDING"))
        from angelo_agent import checkpoint_seal
        published = self.record()
        self.assertEqual(published.pop("component_seal"), checkpoint_seal.seal(self.root, published, receipt["scene_hash"]),
                         "the READY declaration is bound to this capture with the profile key")
        checkpoint_seal.verify(self.root, self.record(), receipt["scene_hash"])
        folder = self.store.folder(self.owner.capture_id)
        self.assertEqual((folder / "scene.blend").read_bytes(), self.base)
        self.assertEqual((folder / "sculpt.runtime").read_bytes(), self.runtime)
        self.assertFalse((folder / "scene.blend@").exists())
        self.assertFalse((folder / "sculpt.runtime@").exists())
        self.assertFalse(self.store.availability(self.owner.capture_id)["available"])
        self.facts["post_state"] = "complete"
        self.assertEqual(self.owner.finish(self.capability)["capture_validation"], "VALIDATED")

    def mutate_component(self, role):
        fd = self.base_fd if role == "BASE" else self.runtime_fd
        before = os.fstat(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, b"X" * before.st_size)
        file_times.set_times(fd, ns=(before.st_atime_ns, before.st_mtime_ns))

    def publication_ack_change(self, role):
        self.prepare()
        original = capture.CaptureOwner._publish_output
        def after_ack(owner, resource, current_role):
            original(owner, resource, current_role)
            if current_role == role:
                self.mutate_component(role)
        with patch.object(capture.CaptureOwner, "_publish_output", after_ack), self.assertRaises(ValueError):
            self.owner.publish(self.capability)
        self.assertEqual(self.record()["state"], "RESERVED")
        self.assertIsNone(self.owner._native_terminal())

    def test_base_ack_change_cannot_be_adopted_by_later_pair_seal(self):
        self.publication_ack_change("BASE")

    def test_runtime_ack_change_cannot_be_adopted_by_later_pair_seal(self):
        self.publication_ack_change("RUNTIME")

    def test_directory_sync_change_preserves_original_component_seal(self):
        self.prepare()
        original = capture.sync_directory
        def after_sync(folder):
            original(folder)
            self.mutate_component("BASE")
        with patch.object(capture, "sync_directory", side_effect=after_sync), self.assertRaises(ValueError):
            self.owner.publish(self.capability)
        self.assertEqual(self.record()["state"], "RESERVED")

    def test_metadata_publication_change_cannot_authorize_post(self):
        self.prepare()
        original = capture._write_json
        def after_write(folder, name, record):
            original(folder, name, record)
            self.mutate_component("RUNTIME")
        with patch.object(capture, "_write_json", side_effect=after_write), self.assertRaises(ValueError):
            self.owner.publish(self.capability)
        self.assertEqual(self.record()["capture_validation"], "PENDING")
        with self.assertRaises(ValueError):
            self.owner._native_publication()

    def test_terminal_metadata_io_change_cannot_return_success(self):
        self.prepare()
        self.owner.publish(self.capability)
        self.facts["post_state"] = "complete"
        original = capture._write_json
        def after_write(folder, name, record):
            original(folder, name, record)
            self.mutate_component("BASE")
        with patch.object(capture, "_write_json", side_effect=after_write), self.assertRaises(ValueError):
            self.owner.finish(self.capability)
        self.assertIsNone(self.owner._native_terminal())

    def test_failed_post_is_interrupted_valid_not_request_success(self):
        self.prepare()
        self.owner.publish(self.capability)
        self.facts["post_state"] = "failed"
        self.assertEqual(self.owner.finish(self.capability)["capture_validation"], "INTERRUPTED_VALID")

    def test_plain_dictionary_cannot_authorize_publication(self):
        self.prepare()
        with self.assertRaises(ValueError):
            self.owner.publish(dict(self.facts))
        self.assertEqual(self.record()["state"], "RESERVED")
        self.assertFalse((self.store.folder(self.owner.capture_id) / "scene.blend").exists())

    def test_native_owner_prevents_abandonment_and_duplicate_begin(self):
        self.prepare()
        with self.assertRaises(ValueError):
            self.owner.abandon()
        with self.assertRaises(ValueError):
            self.owner._native_begin()
        self.assertEqual(os.fstat(self.base_fd).st_size, len(self.base))

    def test_no_runtime_pays_no_hash_and_discharges_only_base(self):
        self.prepare(runtime=False)
        with patch.object(capture, "_hash_descriptor", side_effect=AssertionError("extra BASE hash")):
            self.owner.publish(self.capability)
            self.assertEqual(self.record()["state"], "RESERVED")
            self.facts["post_state"] = "complete"
            record = self.owner.finish(self.capability)
        self.assertEqual((record["version"], record["state"]), (3, "READY"))
        # Sealed in the same record write by the BASE's identity, never by reading it back.
        from angelo_agent import checkpoint_seal
        self.assertEqual(checkpoint_seal.binding(record), "identity")
        base = self.store.folder(self.owner.capture_id) / "scene.blend"
        info = base.stat()
        checkpoint_seal.verify(self.root, record, identity=(info.st_size, info.st_mtime_ns, info.st_ino))
        for changed in ((info.st_size, info.st_mtime_ns + 1000, info.st_ino), (info.st_size, info.st_mtime_ns, info.st_ino + 1)):
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, "not bound to its own capture"):
                checkpoint_seal.verify(self.root, record, identity=changed)

    def test_coarse_timestamps_bind_content_at_publication(self):
        self.prepare(runtime=False)
        hashed = []
        original = capture._hash_descriptor
        def counted(descriptor, size):
            hashed.append(size)
            return original(descriptor, size)
        base = self.store.folder(self.owner.capture_id) / "scene.blend@"
        from angelo_agent import checkpoint_seal
        with patch.object(checkpoint_seal, "coarse_timestamps", return_value=True), \
             patch.object(capture, "_hash_descriptor", side_effect=counted):
            self.owner.publish(self.capability)
            self.facts["post_state"] = "complete"
            record = self.owner.finish(self.capability)
        self.assertEqual(hashed, [len(self.base)], "one BASE hash, only where timestamps are coarse")
        self.assertEqual(checkpoint_seal.binding(record), "content")
        checkpoint_seal.verify(self.root, record, hashlib.blake2b(self.base, digest_size=32).hexdigest())
        self.assertTrue(checkpoint_seal.coarse_timestamps(1_700_000_000 * 10**9))
        self.assertFalse(checkpoint_seal.coarse_timestamps(1_700_000_000 * 10**9 + 123_456))
        self.assertNotIn("capture_outputs", record)
        self.assertNotIn("capture_validation", record)
        self.assertFalse((self.store.folder(self.owner.capture_id) / "sculpt.runtime@").exists())
        self.owner._native_end()
        self.owner.abandon()
        self.assertTrue(self.store.availability(record["id"])["available"])

    def test_no_runtime_cannot_discard_a_runtime_obligation(self):
        self.prepare()
        self.facts.update(runtime_bytes=0, owner_count=0, format_version=0)
        with self.assertRaises(ValueError):
            self.owner.publish(self.capability)
        self.assertEqual(self.record()["state"], "RESERVED")

    def test_no_runtime_post_failure_stays_uncompleted(self):
        self.prepare(runtime=False)
        self.owner.publish(self.capability)
        self.facts["post_state"] = "failed"
        with self.assertRaises(ValueError):
            self.owner.finish(self.capability)
        self.assertEqual(self.record()["state"], "RESERVED")

    def test_post_must_happen_between_publication_and_finish(self):
        self.prepare()
        self.owner.publish(self.capability)
        with self.assertRaises(ValueError):
            self.owner.finish(self.capability)
        self.facts["post_state"] = "complete"
        with self.assertRaises(ValueError):
            self.owner.finish(self.capability)
        self.assertEqual(self.record()["capture_validation"], "PENDING")

    def test_post_mutated_bytes_cannot_be_certified(self):
        self.prepare()
        self.owner.publish(self.capability)
        info = os.fstat(self.base_fd)
        os.lseek(self.base_fd, 0, os.SEEK_SET)
        os.write(self.base_fd, b"X")
        file_times.set_times(self.base_fd, ns=(info.st_atime_ns, info.st_mtime_ns + 1000000))
        self.facts["post_state"] = "complete"
        with self.assertRaises(ValueError):
            self.owner.finish(self.capability)
        self.assertEqual(self.record()["capture_validation"], "PENDING")

    def test_output_collision_preserves_foreign_file_and_no_retry(self):
        self.prepare()
        path = self.store.folder(self.owner.capture_id) / "scene.blend"
        path.write_bytes(b"foreign")
        with self.assertRaises(FileExistsError):
            self.owner.publish(self.capability)
        self.assertEqual(path.read_bytes(), b"foreign")
        self.assertEqual(self.record()["state"], "RESERVED")
        with self.assertRaises(ValueError):
            self.owner.publish(self.capability)

    def test_second_component_failure_preserves_observable_partial_pair(self):
        self.prepare()
        original = capture.publish_new_file
        def fail_second(folder, temporary, final):
            if final == "sculpt.runtime":
                raise OSError("second publication failed")
            return original(folder, temporary, final)
        with patch.object(capture, "publish_new_file", side_effect=fail_second), self.assertRaises(OSError):
            self.owner.publish(self.capability)
        folder = self.store.folder(self.owner.capture_id)
        self.assertEqual((folder / "scene.blend").read_bytes(), self.base)
        self.assertEqual((folder / "sculpt.runtime@").read_bytes(), self.runtime)
        self.assertEqual(self.record()["state"], "RESERVED")

    def test_directory_sync_failure_does_not_claim_ready_or_retry(self):
        self.prepare()
        with patch.object(capture, "sync_directory", side_effect=OSError("sync failed")), self.assertRaises(OSError):
            self.owner.publish(self.capability)
        self.assertEqual(self.record()["state"], "RESERVED")
        with self.assertRaises(ValueError):
            self.owner.publish(self.capability)

    def test_metadata_replace_then_error_remains_pending_and_cannot_finish(self):
        self.prepare()
        original = capture._write_json
        def ambiguous(*args):
            original(*args)
            raise OSError("directory sync outcome ambiguous")
        with patch.object(capture, "_write_json", side_effect=ambiguous), self.assertRaises(OSError):
            self.owner.publish(self.capability)
        self.assertEqual((self.record()["state"], self.record()["capture_validation"]), ("READY", "PENDING"))
        self.facts["post_state"] = "complete"
        with self.assertRaises(ValueError):
            self.owner.finish(self.capability)

    def test_native_size_mismatch_precedes_names_and_metadata(self):
        self.prepare()
        self.facts["scene_bytes"] += 1
        with self.assertRaises(ValueError):
            self.owner.publish(self.capability)
        self.assertEqual(self.record()["state"], "RESERVED")
        self.assertFalse((self.store.folder(self.owner.capture_id) / "scene.blend").exists())

    def test_native_facts_changed_after_hash_prevents_publication(self):
        self.prepare()
        original = self.native_facts
        calls = 0
        def changed(capability, owner):
            nonlocal calls
            calls += 1
            facts = original(capability, owner)
            if calls == 2:
                facts["owner_count"] += 1
            return facts
        self.native.capture_facts = changed
        with self.assertRaises(ValueError):
            self.owner.publish(self.capability)
        self.assertFalse((self.store.folder(self.owner.capture_id) / "scene.blend").exists())

    def test_fresh_protection_and_file_journal_survive_publish_and_post(self):
        self.prepare()
        target = self.root.parent / "workspace"
        target.mkdir()
        (target / "asset.txt").write_text("before")
        self.store.journal(self.owner.capture_id, target, "asset.txt")
        self.rewrite(lambda record: record.update(protected=True))
        expected = self.record()["files"]
        self.owner.publish(self.capability)
        (target / "later.txt").write_text("later")
        self.store.journal(self.owner.capture_id, target, "later.txt")
        self.facts["post_state"] = "complete"
        result = self.owner.finish(self.capability)
        self.assertTrue(result["protected"])
        self.assertEqual(result["files"][:len(expected)], expected)
        self.assertEqual(len(result["files"]), len(expected) + 1)

    def test_changed_base_hash_cannot_admit_post(self):
        self.prepare()
        self.owner.publish(self.capability)
        self.rewrite(lambda record: record.update(scene_hash="0" * 64))
        with self.assertRaisesRegex(ValueError, "declarations changed"):
            self.owner._native_publication()

    def test_changed_runtime_hash_cannot_finish(self):
        self.prepare()
        self.owner.publish(self.capability)
        self.rewrite(lambda record: record["sculpt"].update(hash="0" * 64))
        self.facts["post_state"] = "complete"
        with self.assertRaisesRegex(ValueError, "declarations changed"):
            self.owner.finish(self.capability)
        self.assertEqual(self.record()["capture_validation"], "PENDING")

    def test_changed_storage_cannot_admit_post(self):
        self.prepare()
        self.owner.publish(self.capability)
        self.rewrite(lambda record: record.update(storage="store"))
        with self.assertRaises(ValueError):
            self.owner._native_publication()

    def test_changed_request_purpose_cannot_become_safety_at_finish(self):
        self.prepare()
        self.owner.publish(self.capability)
        self.rewrite(lambda record: record.update(purpose="safety", protected=True))
        self.facts["post_state"] = "complete"
        with self.assertRaisesRegex(ValueError, "binding changed"):
            self.owner.finish(self.capability)
        self.assertIsNone(self.owner._native_terminal())

    def test_changed_base_hash_after_finish_cannot_seal_terminal_evidence(self):
        self.prepare()
        self.owner.publish(self.capability)
        self.facts["post_state"] = "complete"
        self.owner.finish(self.capability)
        self.rewrite(lambda record: record.update(scene_hash="0" * 64))
        self.assertIsNone(self.owner._native_terminal())

    def test_changed_runtime_hash_after_finish_cannot_seal_terminal_evidence(self):
        self.prepare()
        self.owner.publish(self.capability)
        self.facts["post_state"] = "complete"
        self.owner.finish(self.capability)
        self.rewrite(lambda record: record["sculpt"].update(hash="0" * 64))
        self.assertIsNone(self.owner._native_terminal())

    def test_changed_request_purpose_after_finish_cannot_seal_safety(self):
        self.prepare()
        self.owner.publish(self.capability)
        self.facts["post_state"] = "complete"
        self.owner.finish(self.capability)
        self.rewrite(lambda record: record.update(purpose="safety", protected=True))
        self.assertIsNone(self.owner._native_terminal())

    def test_returned_receipt_cannot_change_the_retained_component_binding(self):
        self.prepare()
        receipt = self.owner.publish(self.capability)
        original = self.record()["sculpt"]["hash"]
        receipt["sculpt"]["hash"] = "0" * 64
        receipt["scene_hash"] = "0" * 64
        self.facts["post_state"] = "complete"
        record = self.owner.finish(self.capability)
        self.assertEqual(record["sculpt"]["hash"], original)
        self.assertIsNotNone(self.owner._native_terminal())

    def test_no_runtime_terminal_size_is_bound_to_verified_base(self):
        self.prepare(runtime=False)
        self.owner.publish(self.capability)
        self.facts["post_state"] = "complete"
        self.owner.finish(self.capability)
        self.rewrite(lambda record: record.update(scene_bytes=len(self.base) + 1))
        self.assertIsNone(self.owner._native_terminal())

    def hash_return_change(self, role):
        self.prepare()
        descriptor = self.base_fd if role == "BASE" else self.runtime_fd
        original = capture._hash_descriptor
        changed = False
        def changed_after_hash(fd, size):
            nonlocal changed
            digest = original(fd, size)
            if fd == descriptor and not changed:
                changed = True
                info = os.fstat(fd)
                os.lseek(fd, 0, os.SEEK_SET)
                os.write(fd, b"X" * size)
                file_times.set_times(fd, ns=(info.st_atime_ns, info.st_mtime_ns))
            return digest
        with patch.object(capture, "_hash_descriptor", side_effect=changed_after_hash), self.assertRaises(ValueError):
            self.owner.publish(self.capability)
        self.assertTrue(changed)
        self.assertEqual(self.record()["state"], "RESERVED")
        self.assertFalse((self.store.folder(self.owner.capture_id) / "scene.blend").exists())

    def test_base_hash_return_change_cannot_become_verified_publication(self):
        self.hash_return_change("BASE")

    def test_runtime_hash_return_change_cannot_become_verified_publication(self):
        self.hash_return_change("RUNTIME")

    def test_final_native_facts_cannot_refresh_changed_byte_proof(self):
        self.prepare()
        original = self.native_facts
        calls = 0
        def changed_before_return(token, owner):
            nonlocal calls
            calls += 1
            if calls == 2:
                stamp = os.fstat(self.base_fd)
                os.lseek(self.base_fd, 0, os.SEEK_SET)
                os.write(self.base_fd, b"X" * len(self.base))
                file_times.set_times(self.base_fd, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            return original(token, owner)
        self.native.capture_facts = changed_before_return
        with self.assertRaises(ValueError):
            self.owner.publish(self.capability)
        self.assertEqual(self.record()["state"], "RESERVED")

    def test_native_end_releases_once_and_keeps_descriptors_until_abandon(self):
        self.prepare()
        self.owner._native_end()
        with self.assertRaises(ValueError):
            self.owner._native_end()
        self.assertGreater(os.fstat(self.base_fd).st_size, 0)
        self.owner.abandon()
        with self.assertRaises(OSError):
            os.fstat(self.base_fd)


class NativeCaptureLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="angelo-capture-lifecycle-")
        self.root = Path(self.temp.name).resolve() / "profile"
        self.store = CheckpointStore(self.root, SESSION)
        self.owner, self.token = None, object()
        self.events = []
        self.post_state = "pending"
        self.native = types.SimpleNamespace(capture=self.begin, capture_facts=self.facts,
            capture_post=self.post, capture_abort=self.abort, capture_close=self.close,
            capture_release=self.release)
        self.module = patch.dict(sys.modules, {"_bpy": types.SimpleNamespace(_angelo_checkpoint=self.native)})
        self.module.start()

    def tearDown(self):
        try:
            if getattr(self, "owner", None) is not None:
                self.owner.abandon()
        finally:
            self.module.stop()
            self.temp.cleanup()

    def begin(self, owner):
        self.owner = owner
        self.events.append("capture")
        owner._native_begin()
        for role in ("BASE", "RUNTIME"):
            fd = owner._native_output(role)
            os.write(fd, b"fixture bytes")
        return self.token

    def facts(self, token, owner):
        if token is not self.token or owner is not self.owner:
            raise ValueError("foreign capability")
        return {"capture_id":owner.capture_id, "scene_bytes":13, "runtime_bytes":13,
                "owner_count":2, "format_version":1, "post_state":self.post_state}

    def post(self, token, owner):
        owner._native_publication()
        self.events.append("post")
        self.post_state = "complete"

    def abort(self, token, owner):
        self.events.append("abort")
        self.post_state = "failed"

    def close(self, token):
        self.events.append("close")
        self.owner._native_end()

    def release(self, token):
        self.assertIs(token, self.token)
        self.owner.abandon()

    def test_success_records_terminal_before_native_close_and_resource_release(self):
        real_close = self.native.capture_close
        def observed(token):
            self.assertEqual(self.store.load(self.owner.capture_id)["capture_validation"], "VALIDATED")
            self.assertIsNotNone(self.owner._resource)
            real_close(token)
        self.native.capture_close = observed
        record = capture.capture_checkpoint(self.store)
        self.assertEqual(record["capture_validation"], "VALIDATED")
        self.assertEqual(self.events, ["capture", "post", "close"])
        self.assertIsNone(self.owner._resource)

    def test_post_error_records_interrupted_pair_but_raises_to_request(self):
        def failed(token, owner):
            owner._native_publication()
            self.events.append("post")
            self.post_state = "failed"
            raise RuntimeError("POST failure")
        self.native.capture_post = failed
        with self.assertRaisesRegex(RuntimeError, "POST failure"):
            capture.capture_checkpoint(self.store)
        self.assertEqual(self.store.load(self.owner.capture_id)["capture_validation"], "INTERRUPTED_VALID")
        self.assertEqual(self.events, ["capture", "post", "close"])
        self.assertIsNone(self.owner._resource)

    def test_publication_failure_dispatches_only_fail_then_closes(self):
        with patch.object(capture, "publish_new_file", side_effect=OSError("publication failure")):
            with self.assertRaisesRegex(OSError, "publication failure"):
                capture.capture_checkpoint(self.store)
        self.assertEqual(self.events, ["capture", "abort", "close"])
        self.assertEqual(self.store.load(self.owner.capture_id)["state"], "RESERVED")
        self.assertIsNone(self.owner._resource)

    def test_abort_error_does_not_skip_native_or_python_release(self):
        def failed(token, owner):
            self.events.append("abort")
            raise RuntimeError("FAIL handler failure")
        self.native.capture_abort = failed
        with patch.object(capture, "publish_new_file", side_effect=OSError("publication failure")):
            with self.assertRaises(BaseExceptionGroup) as caught:
                capture.capture_checkpoint(self.store)
        self.assertEqual(len(caught.exception.exceptions), 2)
        self.assertEqual(self.events, ["capture", "abort", "close"])
        self.assertIsNone(self.owner._resource)

    def test_terminal_metadata_error_never_replays_post(self):
        original = capture._write_json
        def terminal(folder, name, record):
            if record.get("capture_validation") == "VALIDATED":
                raise OSError("terminal failure")
            return original(folder, name, record)
        with patch.object(capture, "_write_json", side_effect=terminal):
            with self.assertRaisesRegex(OSError, "terminal failure"):
                capture.capture_checkpoint(self.store)
        self.assertEqual(self.events, ["capture", "post", "close"])
        self.assertEqual(self.store.load(self.owner.capture_id)["capture_validation"], "PENDING")
        self.assertIsNone(self.owner._resource)

    def test_safety_capsule_is_retained_only_after_durable_terminal_completion(self):
        real_close = self.native.capture_close
        evidence = []
        def observed(token):
            evidence.append(self.owner._native_terminal())
            real_close(token)
        self.native.capture_close = observed
        record, token = capture.capture_checkpoint(self.store, purpose="safety", retain_native=True)
        self.assertIs(token, self.token)
        self.assertEqual(evidence, [record])
        self.assertTrue(record["protected"])
        self.assertEqual(record["purpose"], "safety")
        self.assertIsNone(self.owner._native_terminal())
        self.assertIsNotNone(self.owner._resource)
        self.assertEqual(self.owner._native_safety(), record)
        self.native.capture_release(token)
        self.assertIsNone(self.owner._resource)
        with self.assertRaises(ValueError):
            self.owner._native_safety()

    def test_request_cannot_retain_a_safety_capability(self):
        with self.assertRaisesRegex(ValueError, "Only mandatory safety"):
            capture.capture_checkpoint(self.store, retain_native=True)
        self.assertEqual(self.events, [])

    def test_purpose_is_original_and_only_available_during_native_interval(self):
        original = self.native.capture_post
        def post(token, owner):
            self.assertEqual(owner._native_purpose(), "safety")
            original(token, owner)
        self.native.capture_post = post
        capture.capture_checkpoint(self.store, purpose="safety", retain_native=True)
        with self.assertRaises(ValueError):
            self.owner._native_purpose()

    def test_retained_safety_accepts_fresh_journal_and_rejects_unprotected_record(self):
        record, token = capture.capture_checkpoint(self.store, purpose="safety", retain_native=True)
        workspace = Path(self.temp.name).resolve() / "workspace"
        workspace.mkdir()
        (workspace / "a.txt").write_text("artist data")
        self.store.journal(record["id"], workspace, "a.txt")
        fresh = self.owner._native_safety()
        self.assertEqual(len(fresh["files"]), 1)
        self.assertEqual(fresh["scene_hash"], record["scene_hash"])
        path = self.store.folder(record["id"]) / "checkpoint.json"
        fresh["protected"] = False
        path.write_text(json.dumps(fresh))
        with self.assertRaises(ValueError):
            self.owner._native_safety()

    def test_retained_safety_rechecks_metadata_and_each_component(self):
        for change in ("purpose", "scene_hash", "runtime_hash", "BASE", "RUNTIME"):
            with self.subTest(change=change):
                if getattr(self, "owner", None) is not None:
                    self.owner.abandon()
                self.post_state = "pending"
                record, token = capture.capture_checkpoint(self.store, purpose="safety", retain_native=True)
                if change in {"BASE", "RUNTIME"}:
                    fd = self.owner._resource.outputs[change]._resource.descriptor
                    before = os.fstat(fd)
                    os.lseek(fd, 0, os.SEEK_SET)
                    os.write(fd, b"X" * before.st_size)
                    file_times.set_times(fd, ns=(before.st_atime_ns, before.st_mtime_ns))
                else:
                    path = self.store.folder(record["id"]) / "checkpoint.json"
                    if change == "purpose":
                        record["purpose"] = "request"
                    elif change == "scene_hash":
                        record["scene_hash"] = "0" * 64
                    else:
                        record["sculpt"]["hash"] = "0" * 64
                    path.write_text(json.dumps(record))
                with self.assertRaises(ValueError):
                    self.owner._native_safety()

    def test_failed_post_does_not_retain_safety_resources(self):
        def failed(token, owner):
            self.post_state = "failed"
            raise RuntimeError("POST failed")
        self.native.capture_post = failed
        with self.assertRaisesRegex(RuntimeError, "POST failed"):
            capture.capture_checkpoint(self.store, purpose="safety", retain_native=True)
        self.assertIsNone(self.owner._resource)

    def test_consumed_safety_pin_releases_before_actual_file_restore(self):
        from angelo_agent.checkpoint_recovery import CheckpointRecovery
        from angelo_agent.files import FileWorkspace
        selected = capture.capture_checkpoint(self.store)
        workspace = Path(self.temp.name).resolve() / "workspace"
        workspace.mkdir()
        target = workspace / "a.txt"
        target.write_text("before")
        FileWorkspace(workspace).execute("angelo_write_file", {"path": "a.txt", "content": "after"},
                                         self.store, selected["id"])
        pair = CheckpointRecovery.prepare(self.store, selected["id"])
        try:
            plan = self.store.restore_plan(selected["id"], recovery=pair)
            self.post_state = "pending"
            safety, token = capture.capture_checkpoint(self.store, purpose="safety", retain_native=True)
            for _, _, item in plan:
                self.store.journal(safety["id"], Path(item["root"]), item["path"], restore_entry=item)
            self.owner._native_safety()
            with self.assertRaisesRegex(ValueError, "capture is still active"):
                self.store.restore_since(selected["id"], plan, recovery=pair)
            self.assertEqual(target.read_text(), "after")
            self.native.capture_release(token)
            self.assertEqual(self.store.restore_since(selected["id"], plan, recovery=pair), 1)
            self.assertEqual(target.read_text(), "before")
            self.assertTrue(self.store.load(safety["id"])["protected"])
        finally:
            pair.close()

    def test_failed_terminal_write_cannot_seal_safety_evidence(self):
        original_write, original_close = capture._write_json, self.native.capture_close
        evidence = []
        def failed_write(folder, name, record):
            if record.get("capture_validation") == "VALIDATED":
                raise OSError("terminal failed")
            return original_write(folder, name, record)
        def observed(token):
            evidence.append(self.owner._native_terminal())
            original_close(token)
        self.native.capture_close = observed
        with patch.object(capture, "_write_json", side_effect=failed_write), self.assertRaises(OSError):
            capture.capture_checkpoint(self.store, purpose="safety", retain_native=True)
        self.assertEqual(evidence, [None])


@unittest.skipIf(os.name == "nt", "POSIX stand-in; native Windows uses base tests")
class CapturePublicationFakeNtTests(CapturePublicationTests):
    def setUp(self):
        self.api = FakeNtApi()
        self.backend = patch.object(filesystem, "_windows_api", return_value=self.api)
        self.backend.start()
        self.addCleanup(self.backend.stop)
        super().setUp()


if __name__ == "__main__":
    unittest.main()
