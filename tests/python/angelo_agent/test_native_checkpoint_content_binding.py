# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The capture owner's half of checkpoint content binding, with real files and a stand-in native binding.

Native hashes what it writes (``bpy_angelo_checkpoint_binding.cc``); ``publish`` feeds the chunks of its one read to
native hashers and ``capture_bind`` compares inside native before any name is published (``fake_native_binding``
keeps that contract here). A same-size rewrite that puts the modification time back (``utime``, as APFS allows; no
fake clock) between native's last write and ``publish`` matches every stamp check, so only the binding can refuse it:
for a Sculpt pair and for every safety capture, including one taken with nothing in Sculpt mode (owner_count 0).
The built-app halves (POST and Restore refusal, hasher misuse, the gate) are ``test_blender_checkpoint_content_binding``.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import hashlib
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import checkpoint_capture as capture  # noqa: E402
from angelo_agent import checkpoint_seal  # noqa: E402
from angelo_agent.checkpoint_publication import CheckpointPublicationMixin  # noqa: E402
import file_times  # noqa: E402
import test_native_checkpoint_capture_publication as publication  # noqa: E402

MIB = 1024 * 1024


def available(store, identity):
    """The store's availability with a native that has the Restore reader (as in the app): only the record decides."""
    reader = types.SimpleNamespace(prepare_restore=lambda _: None, restore=lambda *_: None, close_restore=lambda _: None)
    with patch.dict(sys.modules, {"_bpy": types.SimpleNamespace(_angelo_checkpoint=reader)}):
        return store.availability(identity)["available"]


def bind_skipped(owner, native_capture, resource, roles, facts):
    """``_bind_content`` with native's comparison skipped, as a Python regression would: hashes, never binds."""
    return {role: capture._hash_descriptor(resource.outputs[role]._resource.descriptor,
                                           facts["scene_bytes" if role == "BASE" else "runtime_bytes"])
            for role in roles}


class ContentBindingPublicationTests(unittest.TestCase):
    def setUp(self):
        for name in ("native_facts", "prepare", "record", "mutate_component"):
            setattr(self, name, getattr(publication.CapturePublicationTests, name).__get__(self))
        publication.CapturePublicationTests.setUp(self)
        self.addCleanup(publication.CapturePublicationTests.tearDown, self)

    def reserve(self, purpose):
        self.owner.abandon()
        self.owner = capture.CaptureOwner.reserve(self.store, purpose=purpose)

    def rewrite_keeping_stamp(self, role):
        """Another writer after native's last write: same size, new bytes, times put back exactly."""
        fd = self.base_fd if role == "BASE" else self.runtime_fd
        before = os.fstat(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, b"X" * before.st_size)
        file_times.set_times_exact(fd, ns=(before.st_atime_ns, before.st_mtime_ns))
        after = os.fstat(fd)
        self.assertEqual((after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
                         (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns),
                         "the precondition: every stamp native admits by is unchanged")

    def refused(self, *names):
        with self.assertRaisesRegex(ValueError, "native checkpoint output changed"):
            self.owner.publish(self.capability)
        self.assertTrue(self.owner._resource.failed, "failed: native POST is refused, capture_abort follows")
        self.assertEqual(self.record()["state"], "RESERVED", "no READY record")
        folder = self.store.folder(self.owner.capture_id)
        for name in names:
            self.assertFalse((folder / name).exists(), name + " was never published")
        with self.assertRaisesRegex(ValueError, "capture attempt failed"):
            self.owner._native_publication()

    # The rewrite native's stamps cannot see.

    def test_a_pair_base_rewritten_keeping_its_stamp_is_refused(self):
        self.prepare()
        self.rewrite_keeping_stamp("BASE")
        self.refused("scene.blend", "sculpt.runtime")

    def test_a_pair_runtime_rewritten_keeping_its_stamp_is_refused(self):
        self.prepare()
        self.rewrite_keeping_stamp("RUNTIME")
        self.refused("scene.blend", "sculpt.runtime")

    def test_a_safety_capture_with_no_sculpt_owner_rewritten_keeping_its_stamp_is_refused(self):
        self.reserve("safety")
        self.prepare(runtime=False)
        self.rewrite_keeping_stamp("BASE")
        self.refused("scene.blend")

    def test_a_safety_pair_rewritten_keeping_its_stamp_is_refused(self):
        self.reserve("safety")
        self.prepare()
        self.rewrite_keeping_stamp("BASE")
        self.refused("scene.blend", "sculpt.runtime")

    # What is bound, and with what.

    def test_the_native_hasher_receives_exactly_the_chunks_blake2b_hashed(self):
        self.prepare()
        payload = os.urandom(8 * MIB * 2 + 12345)   # Three chunks of the one read: 8 MiB, 8 MiB and the rest.
        os.lseek(self.base_fd, 0, os.SEEK_SET)
        os.write(self.base_fd, payload)
        self.binding.wrote(self.capability, "BASE", payload)
        self.facts["scene_bytes"] = len(payload)
        blake = []
        real = hashlib.blake2b

        class Recorded:
            def __init__(self, **options):
                self._hash = real(**options)

            def update(self, data):
                blake.append(len(data))
                self._hash.update(data)

            def hexdigest(self):
                return self._hash.hexdigest()
        with patch.object(capture.hashlib, "blake2b", Recorded):
            receipt = self.owner.publish(self.capability)
        base, runtime = (next(h for h in self.binding.hashers if h.role == role) for role in ("BASE", "RUNTIME"))
        self.assertEqual(base.chunks + runtime.chunks, blake, "the same chunks, in the same order, as BLAKE2b's")
        self.assertEqual(base.chunks, [8 * MIB, 8 * MIB, 12345])
        self.assertEqual(receipt["scene_hash"], real(payload, digest_size=32).hexdigest())
        self.assertTrue(self.binding.bound)

    def test_a_bound_ordinary_safety_capture_is_content_sealed_without_a_new_field(self):
        self.reserve("safety")
        self.prepare(runtime=False)
        self.owner.publish(self.capability)
        self.assertEqual(self.binding.bind_calls, 1)
        self.facts["post_state"] = "complete"
        with patch.object(capture, "_hash_descriptor", side_effect=AssertionError("finish reads nothing back")):
            record = self.owner.finish(self.capability)
        self.assertEqual((record["version"], checkpoint_seal.binding(record)), (3, "content"))
        self.assertNotIn("scene_hash", record)
        checkpoint_seal.verify(self.root, record, hashlib.blake2b(self.base, digest_size=32).hexdigest())
        with self.assertRaisesRegex(ValueError, "not bound to its own capture"):
            checkpoint_seal.verify(self.root, record, hashlib.blake2b(b"X" * len(self.base), digest_size=32).hexdigest())

    def test_an_ordinary_request_on_a_fine_clock_binds_nothing(self):
        """The one residual of 1.0.10 (checkpoint_policy.rst): stamp-bound, never read back on a request."""
        with patch.object(checkpoint_seal, "coarse_timestamps", return_value=False):
            self.prepare(runtime=False)
            with patch.object(capture, "_hash_descriptor", side_effect=AssertionError("a request read its BASE")):
                self.owner.publish(self.capability)
            self.facts["post_state"] = "complete"
            record = self.owner.finish(self.capability)
        self.assertEqual((self.binding.hashers, self.binding.bind_calls), ([], 0))
        self.assertEqual(checkpoint_seal.binding(record), "identity")

    # Nothing unbound becomes valid, whatever skipped the binding.

    def test_publish_refuses_a_pair_or_safety_capture_whose_bind_was_skipped(self):
        for purpose, runtime in (("request", True), ("safety", False), ("safety", True)):
            with self.subTest(purpose=purpose, runtime=runtime):
                if self.owner._resource is not None and self.owner._resource.native_active:
                    self.owner._native_end()
                self.reserve(purpose)
                self.binding = type(self.binding)()
                self.binding.install(self.native)
                self.prepare(runtime=runtime)
                with patch.object(CheckpointPublicationMixin, "_bind_content", bind_skipped), \
                        self.assertRaisesRegex(ValueError, "not bound to native's write before publication"):
                    self.owner.publish(self.capability)
                self.assertEqual(self.record()["state"], "RESERVED")
                self.assertFalse((self.store.folder(self.owner.capture_id) / "scene.blend").exists())

    def test_finish_records_an_unbound_pair_invalid_whatever_post_said(self):
        for post_state in ("complete", "failed"):
            with self.subTest(post_state=post_state):
                if self.owner._resource is not None and self.owner._resource.native_active:
                    self.owner._native_end()
                self.reserve("request")
                self.binding = type(self.binding)()
                self.binding.install(self.native)
                self.prepare()
                self.owner.publish(self.capability)
                self.owner._resource.content_bound = False   # A regression that published without the flag.
                self.facts["post_state"] = post_state
                with self.assertRaisesRegex(ValueError, "recorded INVALID"):
                    self.owner.finish(self.capability)
                self.assertEqual((self.record()["state"], self.record()["capture_validation"]), ("READY", "INVALID"))
                self.assertFalse(available(self.store, self.owner.capture_id))
                self.assertIsNone(self.owner._native_terminal())

    # Fail closed: an older native, or a capture whose binding was never decided.

    def test_a_native_without_the_hasher_or_the_bind_is_refused(self):
        for missing in ("capture_hasher", "capture_bind"):
            with self.subTest(missing=missing):
                if self.owner._resource is not None and self.owner._resource.native_active:
                    self.owner._native_end()
                self.reserve("request")
                self.binding = type(self.binding)()
                self.binding.install(self.native)
                self.prepare()
                delattr(self.native, missing)
                with self.assertRaises(AttributeError):
                    self.owner.publish(self.capability)
                self.assertTrue(self.owner._resource.failed)
                self.assertEqual(self.record()["state"], "RESERVED")
                self.assertFalse((self.store.folder(self.owner.capture_id) / "scene.blend").exists())

    def test_a_native_that_never_asked_for_the_binding_is_refused(self):
        identity, _path = self.owner._native_begin()
        fd = self.owner._native_output("BASE")
        written = b"an older native writes without asking"
        os.write(fd, written)
        self.facts = {"capture_id": identity, "scene_bytes": len(written), "runtime_bytes": 0, "owner_count": 0,
                      "format_version": 0, "post_state": "pending"}
        with self.assertRaisesRegex(ValueError, "did not decide its content binding"):
            self.owner.publish(self.capability)
        self.assertEqual(self.record()["state"], "RESERVED")

    @patch.object(capture.change_clock, "volume", lambda _witness: capture.change_clock._posix.FINE)
    def test_the_binding_is_decided_once_before_the_base_write(self):
        self.owner._native_begin()
        with self.assertRaisesRegex(ValueError, "decided once"):
            self.owner._native_binding()   # No BASE destination yet.
        fd = self.owner._native_output("BASE")
        self.assertFalse(self.owner._native_binding())   # A request on this (fine) volume.
        with self.assertRaisesRegex(ValueError, "decided once"):
            self.owner._native_binding()
        os.write(fd, b"bytes")

    @patch.object(capture.change_clock, "volume", lambda _witness: capture.change_clock._posix.FINE)
    def test_a_base_whose_stamp_contradicts_a_fine_prediction_is_refused(self):
        self.prepare(runtime=False)   # Predicted fine (bind_required False) ...
        with patch.object(checkpoint_seal, "coarse_timestamps", return_value=True), \
                self.assertRaisesRegex(ValueError, "coarse clock but not content-bound"):
            self.owner.publish(self.capability)   # ... but its final stamp reads as coarse.
        self.assertEqual(self.record()["state"], "RESERVED")
        self.assertFalse((self.store.folder(self.owner.capture_id) / "scene.blend").exists())


class ContentBindingLifecycleTests(unittest.TestCase):
    """capture_checkpoint end to end with the stand-in native: a refused bind aborts, never POSTs."""

    def setUp(self):
        for name in ("begin", "facts", "post", "abort", "close", "release"):
            setattr(self, name, getattr(publication.NativeCaptureLifecycleTests, name).__get__(self))
        publication.NativeCaptureLifecycleTests.setUp(self)
        self.addCleanup(publication.NativeCaptureLifecycleTests.tearDown, self)

    def test_a_safety_capture_rewritten_before_publish_aborts_without_post_or_record(self):
        def rewrite_after_native(owner):
            token = self.begin(owner)
            fd = owner._resource.outputs["BASE"]._resource.descriptor
            before = os.fstat(fd)
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, b"X")   # Same size: one byte of native's "fixture bytes" replaced.
            file_times.set_times_exact(fd, ns=(before.st_atime_ns, before.st_mtime_ns))
            self.assertEqual(os.fstat(fd).st_mtime_ns, before.st_mtime_ns, "the precondition: the stamp is kept")
            return token
        self.native.capture = rewrite_after_native
        with self.assertRaisesRegex(ValueError, "native checkpoint output changed"):
            capture.capture_checkpoint(self.store, purpose="safety", retain_native=True)
        self.assertEqual(self.events, ["capture", "abort", "close"], "SAVE_POST_FAIL, never POST")
        self.assertIsNone(self.owner._resource, "no retained safety resources")
        self.assertNotEqual(self.store.load(self.owner.capture_id)["state"], "READY")

    def test_a_pair_whose_bind_was_skipped_never_becomes_valid_or_restorable(self):
        """Native refuses its POST (a failed POST); before the Python-side flag, finish still wrote
        INTERRUPTED_VALID over bytes nobody compared, which Restore accepts."""
        with patch.object(CheckpointPublicationMixin, "_bind_content", bind_skipped), self.assertRaises(ValueError):
            capture.capture_checkpoint(self.store)
        record = self.store.load(self.owner.capture_id)
        self.assertNotIn(record.get("capture_validation"), {"VALIDATED", "INTERRUPTED_VALID"}, record.get("state"))
        self.assertFalse(available(self.store, self.owner.capture_id))
        self.assertNotIn("post", self.events, "native never admitted the POST")


if __name__ == "__main__":
    unittest.main()
