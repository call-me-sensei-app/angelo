# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Legacy Windows birth-time stats with by-handle change witnesses; no NTFS claim."""

from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fake_nt_files import FakeNtApi
from angelo_agent import checkpoint_capture as capture, checkpoint_filesystem as filesystem
import test_native_checkpoint_capture_publication as publication
import test_native_checkpoint_recovery_resources as recovery
import test_native_checkpoint_bundle_flow as bundles

_FSTAT = os.fstat


class BirthTimeStat:
    def __init__(self, value):
        self.value = value
        self.st_ctime_ns = 123456789

    def __getattr__(self, name):
        return getattr(self.value, name)


class ChangeTimeApi(FakeNtApi):
    def change_time(self, descriptor):
        return _FSTAT(descriptor).st_ctime_ns


@contextmanager
def legacy_runtime():
    if os.name == "nt":
        raise unittest.SkipTest("The POSIX stand-in of the NT API cannot open a Windows drive; on Windows the real "
                                "backend runs in the capture and recovery suites")
    api = ChangeTimeApi()
    with (patch.object(filesystem, "_windows_api", return_value=api),
          patch.object(filesystem, "_needs_windows_change_time", return_value=True),
          patch.object(os, "fstat", side_effect=lambda fd: BirthTimeStat(_FSTAT(fd)))):
        yield api


class CheckpointChangeTimeTests(unittest.TestCase):
    def test_same_size_restored_mtime_retains_birthtime_but_invalidates_change_witness(self):
        with tempfile.TemporaryDirectory() as temp, legacy_runtime():
            root = Path(temp).resolve()
            with filesystem._private_directory(root) as folder:
                fd = filesystem._open_file(folder, "part", os.O_RDWR | os.O_CREAT | os.O_EXCL)
                try:
                    os.write(fd, b"before")
                    # Use a representable baseline: some hosts restore utime at microsecond precision.
                    os.utime(fd, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
                    before = filesystem.descriptor_witness(fd)
                    self.assertTrue(capture._stable_file(before, filesystem.file_witness(folder, "part")))
                    os.lseek(fd, 0, os.SEEK_SET)
                    os.write(fd, b"after!")
                    os.utime(fd, ns=(before.st_atime_ns, before.st_mtime_ns))
                    after = filesystem.descriptor_witness(fd)
                    self.assertEqual((before.st_size, before.st_mtime_ns, before.st_ctime_ns),
                                     (after.st_size, after.st_mtime_ns, after.st_ctime_ns))
                    self.assertNotEqual(before.st_change_time, after.st_change_time)
                    self.assertFalse(capture._stable_file(before, after))
                    self.assertTrue(capture._stable_file(after, filesystem.file_witness(folder, "part")))
                finally:
                    os.close(fd)

    def test_change_during_stamp_or_query_failure_does_not_fall_back_to_birthtime(self):
        with tempfile.TemporaryFile() as handle, legacy_runtime() as api:
            with patch.object(api, "change_time", side_effect=(1, 2)), self.assertRaises(ValueError):
                filesystem.descriptor_witness(handle.fileno())
            with patch.object(api, "change_time", side_effect=OSError("change query failed")), self.assertRaises(OSError):
                filesystem.descriptor_witness(handle.fileno())
            self.assertEqual(_FSTAT(handle.fileno()).st_size, 0)

    def test_current_runtime_keeps_existing_descriptor_cost(self):
        with tempfile.TemporaryFile() as handle:
            with (patch.object(filesystem, "_needs_windows_change_time", return_value=False),
                  patch.object(filesystem, "_windows_api", side_effect=AssertionError("extra query"))):
                self.assertEqual(filesystem.descriptor_witness(handle.fileno()), _FSTAT(handle.fileno()))

    def run_fixture(self, case, method):
        with legacy_runtime():
            fixture = case(method)
            fixture.setUp()
            try:
                getattr(fixture, method)()
            finally:
                fixture.tearDown()

    def test_legacy_capture_ack_tamper_and_positive_pair(self):
        for method in ("test_base_ack_change_cannot_be_adopted_by_later_pair_seal",
                       "test_runtime_ack_change_cannot_be_adopted_by_later_pair_seal",
                       "test_pair_is_hashed_published_then_finished_after_post"):
            with self.subTest(method=method):
                self.run_fixture(publication.CapturePublicationTests, method)

    def test_legacy_recovery_hash_return_tamper_and_positive_copy(self):
        for method in ("test_destination_hash_return_change_cannot_refresh_verified_seal",
                       "test_actual_pair_copied_and_exact_fds_retained_without_scene_authority"):
            with self.subTest(method=method):
                self.run_fixture(recovery.RecoveryResourceTests, method)

    def test_legacy_retention_rejects_hash_and_tombstone_tamper_but_accepts_exact_pair(self):
        for method in ("test_hash_return_mutation_cannot_become_a_new_verified_witness",
                       "test_in_place_content_change_after_tombstone_is_preserved",
                       "test_complete_pair_can_follow_existing_retention_policy"):
            with self.subTest(method=method):
                self.run_fixture(bundles.BundleConsumerTests, method)

    def test_legacy_retained_safety_checks_real_changes_and_allows_fresh_journals(self):
        for method in ("test_retained_safety_rechecks_metadata_and_each_component",
                       "test_retained_safety_accepts_fresh_journal_and_rejects_unprotected_record"):
            with self.subTest(method=method):
                self.run_fixture(publication.NativeCaptureLifecycleTests, method)


if __name__ == "__main__":
    unittest.main()
