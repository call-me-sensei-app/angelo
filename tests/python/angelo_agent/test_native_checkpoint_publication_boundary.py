# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A Sculpt pair's publication on a coarse clock, deterministically: what a stat can see once ``publish`` returns.

``test_native_checkpoint_capture_publication`` publishes real files, but APFS and Linux stamp change times finely, so
a write right after a link is caught there whether or not anything waited. Here the volume behaves as NTFS does
(measured; ``angelo_change_clock``'s docstring): a component's ChangeTime is the reading of a clock that moves in
ticks, stamped at every change of the file (a write, a link, an unlink), and that clock moves only when
``change_clock`` sleeps or the test lets a tick pass (another writer acting later). The real files, links, hashes and
checks run unchanged; only the change time a stat reports, and the clock it is judged against, are the volume's.

The boundary (``doc/angelo/checkpoint_policy.rst``, "Differences and limits"): from the moment ``publish`` returns,
any write to either component stamps another change time than its seal's. A write inside a component's own tick
before the pair's settle carries the seal's stamp and no stat sees it, but a pair is content-bound to native's own
write ("Content binding"): such a write before publish hashes it is refused by native's comparison (tested below,
with every safety capture and the coarse ordinary BASE), and one after it by Restore's re-hash against the record.
What stays stamp-bound in 1.0.10 is only an ordinary request BASE on a fine-clock volume (its identity seal).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

from contextlib import ExitStack
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import change_clock, checkpoint_capture as capture  # noqa: E402
import file_times  # noqa: E402
import test_native_checkpoint_capture_publication as publication  # noqa: E402

EPOCH = 116444736000000000   # 1970-01-01 in FILETIME (100 ns steps since 1601).
TICK = 156_250               # NTFS's default clock tick (15.625 ms) in FILETIME steps.
# The volume clock's first reading: 2100-01-01, past every real stamp a test file carries, so a real modification
# time is never in the volume clock's tick and only the volume's own change times are judged.
START = EPOCH + 4_102_444_800 * 10_000_000


class NtfsStat:
    """A real stat whose change time is the volume's: one ChangeTime, reported as ``st_change_time`` and as
    ``st_ctime_ns`` alike (CPython 3.13's ``st_ctime_ns`` is NTFS's ChangeTime)."""

    def __init__(self, info, change_time):
        self._info, self.st_change_time = info, change_time
        self.st_ctime_ns = (change_time - EPOCH) * 100

    def __getattr__(self, name):
        return getattr(self._info, name)


class CoarseVolume:
    """The components' change times as NTFS stamps them: the clock's reading at each change. ``now`` moves only by
    ``tick`` (``change_clock``'s sleep, or the test letting time pass)."""

    def __init__(self):
        self.now, self.stamps, self.sleeps = START, {}, 0

    def clock(self):
        return self.now

    def tick(self):
        self.now += TICK

    def sleep(self, _seconds):
        self.sleeps += 1
        self.tick()

    def stamp(self, descriptor):
        info = os.fstat(descriptor)
        self.stamps[(info.st_dev, info.st_ino)] = self.now

    def witness(self, info):
        stamped = self.stamps.get((info.st_dev, info.st_ino))
        return info if stamped is None else NtfsStat(info, stamped)


class PublicationBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.volume = volume = CoarseVolume()
        witness, named = capture.descriptor_witness, capture.file_witness
        link, unlink = capture.publish_new_file, capture._unlink_file

        def linked(folder, temporary, name):
            link(folder, temporary, name)
            self.restamp(temporary)

        def unlinked(folder, name):
            unlink(folder, name)
            self.restamp(name)
        stack = ExitStack()
        for target, name, value in (
                (change_clock, "COARSE", True), (change_clock, "system_time", volume.clock),
                (change_clock, "volume_step", lambda device: 0),   # A known NTFS volume: stamps never below the clock.
                (change_clock, "_sleep", volume.sleep),
                (capture, "descriptor_witness", lambda descriptor: volume.witness(witness(descriptor))),
                (capture, "file_witness", lambda folder, name: volume.witness(named(folder, name))),
                (capture, "publish_new_file", linked), (capture, "_unlink_file", unlinked)):
            stack.enter_context(patch.object(target, name, value))
        self.addCleanup(stack.close)   # Registered first, so it runs after the fixture's own tearDown.
        for name in ("native_facts", "prepare", "record", "mutate_component"):
            setattr(self, name, getattr(publication.CapturePublicationTests, name).__get__(self))
        publication.CapturePublicationTests.setUp(self)
        self.addCleanup(publication.CapturePublicationTests.tearDown, self)

    def descriptor(self, role):
        return self.base_fd if role == "BASE" else self.runtime_fd

    def restamp(self, name):
        """A link or an unlink changes the file's ChangeTime: stamp the component that name belongs to."""
        for role in ("BASE", "RUNTIME"):
            if name in capture.output_names(role) and hasattr(self, ("base" if role == "BASE" else "runtime") + "_fd"):
                self.volume.stamp(self.descriptor(role))

    def prepare_pair(self):
        self.prepare()
        for role in ("BASE", "RUNTIME"):
            # A modification time every host restores exactly (some keep only microseconds), as NTFS restores its own:
            # a writer that puts the time back leaves only the change time to tell.
            # file_times: os.utime takes no descriptor on Windows, the volume this boundary exists for.
            file_times.set_times(self.descriptor(role), ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
            self.volume.stamp(self.descriptor(role))   # The native writer's own writes, in the clock's current tick.

    def write(self, role):
        """Another writer: same size, new bytes, times put back; NTFS stamps its ChangeTime with the clock's reading."""
        self.mutate_component(role)
        self.volume.stamp(self.descriptor(role))

    def linked_at(self):
        """Each role's clock reading at the moment its ``_publish_output`` returned (its link and unlink done)."""
        readings, original = {}, capture.CaptureOwner._publish_output

        def observed(owner, resource, role):
            original(owner, resource, role)
            readings[role] = self.volume.now
        return readings, patch.object(capture.CaptureOwner, "_publish_output", observed)

    # The boundary: settled the moment publish returns.

    def test_both_sealed_components_are_settled_when_publish_returns(self):
        self.prepare_pair()
        readings, observing = self.linked_at()
        with observing:
            self.owner.publish(self.capability)
        sealed = self.owner._resource.sealed_outputs
        self.assertEqual(set(sealed), {"BASE", "RUNTIME"})
        for role in ("BASE", "RUNTIME"):
            with self.subTest(role=role):
                # The precondition that makes this a pin: the link stamped the seal in the clock's tick then.
                self.assertEqual(change_clock.verdict(sealed[role], readings[role])[0], change_clock.WAIT)
                self.assertTrue(change_clock.settled(sealed[role], self.volume.clock),
                                "from the moment publish returns, a write stamps another change time than the seal's")

    def write_the_moment_publish_returns(self, role):
        self.prepare_pair()
        self.owner.publish(self.capability)
        self.write(role)   # No tick passes: the clock is where publish left it.
        self.facts["post_state"] = "complete"
        with self.assertRaisesRegex(ValueError, "published capture component changed during POST"):
            self.owner.finish(self.capability)
        self.assertEqual(self.record()["capture_validation"], "PENDING", "never VALIDATED over changed bytes")

    def test_a_base_write_the_moment_publish_returns_is_caught(self):
        self.write_the_moment_publish_returns("BASE")

    def test_a_runtime_write_the_moment_publish_returns_is_caught(self):
        self.write_the_moment_publish_returns("RUNTIME")

    def test_an_untouched_pair_still_validates_after_two_waits(self):
        self.prepare_pair()
        self.owner.publish(self.capability)
        self.facts["post_state"] = "complete"
        self.assertEqual(self.owner.finish(self.capability)["capture_validation"], "VALIDATED")
        self.assertEqual(self.volume.sleeps, 2, "one wait before hashing, one for both seals as publish's last step")

    # Inside publish: a change after a component's link, a tick later, is refused before READY.

    def test_a_base_change_after_its_link_is_refused_before_runtime_is_linked(self):
        self.prepare_pair()
        original = capture.CaptureOwner._publish_output

        def after_link(owner, resource, role):
            original(owner, resource, role)
            if role == "BASE":
                self.volume.tick()   # Another writer, one tick after BASE's link.
                self.write("BASE")
        with patch.object(capture.CaptureOwner, "_publish_output", after_link), \
                self.assertRaisesRegex(ValueError, "acknowledged capture component changed after publication"):
            self.owner.publish(self.capability)
        self.assertEqual(self.owner._resource.published, {"BASE"}, "refused at once: RUNTIME is never linked")
        self.assertFalse((self.store.folder(self.owner.capture_id) / capture.output_names("RUNTIME")[1]).exists())
        self.assertEqual(self.record()["state"], "RESERVED")

    def runtime_change_before_ready(self, hook):
        self.prepare_pair()
        with hook, self.assertRaisesRegex(ValueError, "acknowledged capture component changed after publication"):
            self.owner.publish(self.capability)
        self.assertEqual(self.record()["state"], "RESERVED", "never READY over changed bytes")

    def test_a_runtime_change_right_after_its_link_is_refused_before_ready(self):
        original = capture.CaptureOwner._publish_output

        def after_link(owner, resource, role):
            original(owner, resource, role)
            if role == "RUNTIME":
                self.volume.tick()   # Another writer, one tick after RUNTIME's link.
                self.write("RUNTIME")
        self.runtime_change_before_ready(patch.object(capture.CaptureOwner, "_publish_output", after_link))

    def test_a_runtime_change_during_the_directory_sync_is_refused_before_ready(self):
        original = capture.sync_directory

        def during_sync(folder):
            original(folder)
            self.volume.tick()   # Another writer, a tick after the links, before the READY record.
            self.write("RUNTIME")
        self.runtime_change_before_ready(patch.object(capture, "sync_directory", side_effect=during_sync))

    # The late settle: READY may be durable before the tick passes, never VALIDATED.

    def test_a_settle_that_fails_after_ready_leaves_no_post_or_validated_path(self):
        self.prepare_pair()
        settle_all, calls = change_clock.settle_all, []

        def refused(witnesses, clock=None, **options):
            calls.append(list(witnesses))
            if len(calls) == 2:   # The pair's settle after the READY record (the first is before hashing).
                raise change_clock.Unsettled("The file's time stayed in its clock step past the wait's cap")
            return settle_all(witnesses, clock, **options)
        with patch.object(change_clock, "settle_all", side_effect=refused), \
                self.assertRaises(change_clock.Unsettled):
            self.owner.publish(self.capability)
        self.assertEqual(len(calls), 2)
        self.assertTrue(self.owner._resource.failed)
        self.assertEqual(self.record()["capture_validation"], "PENDING")
        with self.assertRaisesRegex(ValueError, "capture attempt failed"):
            self.owner._native_publication()   # Native POST admission.
        self.facts["post_state"] = "complete"
        with self.assertRaisesRegex(ValueError, "capture attempt failed"):
            self.owner.finish(self.capability)
        self.assertEqual(self.record()["capture_validation"], "PENDING", "never VALIDATED")

    # Content binding: a same-size write inside the tick of native's own last write, before publish. Its stat matches
    # native's stamp exactly, so only native's hash of what it wrote can tell; each is refused before any name is
    # published, with nothing READY and the attempt failed (so POST is refused and capture_abort follows).

    def refused_before_any_name(self):
        with self.assertRaisesRegex(ValueError, "native checkpoint output changed"):
            self.owner.publish(self.capability)
        self.assertTrue(self.owner._resource.failed)
        self.assertEqual(self.record()["state"], "RESERVED")
        folder = self.store.folder(self.owner.capture_id)
        self.assertFalse((folder / "scene.blend").exists())
        self.assertFalse((folder / "sculpt.runtime").exists())

    def same_stat_write(self, role):
        info = os.fstat(self.descriptor(role))
        stamp = self.volume.stamps[(info.st_dev, info.st_ino)]
        self.write(role)   # No tick passes: the change time is the native write's own.
        self.assertEqual(self.volume.stamps[(info.st_dev, info.st_ino)], stamp, "the precondition: no stat sees it")

    def prepare_base(self, purpose="request"):
        if purpose != "request":
            self.owner.abandon()
            self.owner = capture.CaptureOwner.reserve(self.store, purpose=purpose)
        self.prepare(runtime=False)
        file_times.set_times(self.base_fd, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
        self.volume.stamp(self.base_fd)

    def test_a_pair_base_written_in_its_tick_before_publish_is_refused(self):
        self.prepare_pair()
        self.same_stat_write("BASE")
        self.refused_before_any_name()

    def test_a_pair_runtime_written_in_its_tick_before_publish_is_refused(self):
        self.prepare_pair()
        self.same_stat_write("RUNTIME")
        self.refused_before_any_name()

    def test_a_safety_base_written_in_its_tick_before_publish_is_refused(self):
        self.prepare_base("safety")
        self.same_stat_write("BASE")
        self.refused_before_any_name()

    def test_a_coarse_ordinary_base_written_in_its_tick_before_publish_is_refused(self):
        from angelo_agent import checkpoint_seal
        with patch.object(checkpoint_seal, "coarse_timestamps", return_value=True):
            self.prepare_base()
            self.same_stat_write("BASE")
            self.refused_before_any_name()

    def bound_ordinary_is_content_sealed_version_3(self):
        from angelo_agent import checkpoint_seal
        self.owner.publish(self.capability)
        self.assertTrue(self.binding.bound)
        self.facts["post_state"] = "complete"
        record = self.owner.finish(self.capability)
        self.assertEqual((record["version"], record["state"], checkpoint_seal.binding(record)), (3, "READY", "content"))
        self.assertNotIn("scene_hash", record, "no new record field: the version 3 format is unchanged")

    def test_an_untouched_safety_base_is_content_sealed_version_3(self):
        self.prepare_base("safety")
        self.bound_ordinary_is_content_sealed_version_3()

    def test_an_untouched_coarse_ordinary_base_is_content_sealed_version_3(self):
        from angelo_agent import checkpoint_seal
        with patch.object(checkpoint_seal, "coarse_timestamps", return_value=True):
            self.prepare_base()
            self.bound_ordinary_is_content_sealed_version_3()


if __name__ == "__main__":
    unittest.main()
