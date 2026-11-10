# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A stopped preparation and the next one never share an update's folder.

Stopping a worker only asks it to stop: the download watches the request, but unpacking and verifying run to the
end. So whatever starts or removes a version's folder next (the same version accepted again, the session that
comes after an add-on reload, a newer version's clean-up, Stay's removal) must wait until that worker has settled.
Found by the 1.0.9 audit: Stay then Update at once let a second worker delete the first one's stage.

The real ``update_flow.Preparer`` and ``update_ready`` run here; only the download and the platform's staging are
stood in for, and the staging holds still where a slow disk image would.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import importlib
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_native_update_flow as flow_tests  # noqa: E402
import test_native_update_lines as lines  # noqa: E402
import test_native_update_ready as ready  # noqa: E402
from angelo_agent import update_feed, update_flow, update_hold  # noqa: E402
from host_test_policy import environment_skip  # noqa: E402

WAIT = 10
ADDONS = HERE.parents[2] / "scripts/addons_core"
OTHER_ANGELO = """
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from angelo_agent import update_hold
with update_hold.hold(Path(sys.argv[2])):
    Path(sys.argv[3]).write_text("held")
    while not Path(sys.argv[4]).exists():
        time.sleep(0.02)
"""


class SlowMac(flow_tests.FakeMac):
    """Staging that can be held still, and that notices two workers in one folder or its files going under it."""

    def __init__(self, root):
        super().__init__(root)
        self.lock, self.active, self.overlaps, self.lost, self.finished = threading.Lock(), {}, [], [], []
        self.hold, self.entered, self.holds = {}, {}, []

    def hold_next(self, version):
        self.hold[version], self.entered[version] = threading.Event(), threading.Event()
        self.holds.append(self.hold[version])

    def release(self):
        for event in self.holds:
            event.set()

    def from_disk_image(self, image, version, work, app):
        with self.lock:
            self.active[str(work)] = self.active.get(str(work), 0) + 1
            if self.active[str(work)] > 1:
                self.overlaps.append(version)
            hold = self.hold.pop(version, None)
        try:
            staged = self._stage(work)
            marker = staged / ("worker-%d" % threading.get_ident())
            marker.write_text("first half")
            if hold is not None:
                self.entered[version].set()
                hold.wait()   # Only a release ends it (every test's cleanup releases): no clock decides "it waits".
            if not marker.is_file():
                self.lost.append(version)
            marker.unlink(missing_ok=True)
            return staged
        finally:
            with self.lock:
                self.active[str(work)] -= 1
                self.finished.append(version)


class Case(ready.ReadyCase):
    def setUp(self):
        super().setUp()
        self.mac = SlowMac(self.folder)
        self.real = update_flow.Preparer(self.folder / "updates", "1.0.9", lines.MAC, "/A/Angelo.app/Contents/MacOS/A",
                                         fetch=self.fetch, mac=self.mac, log=lambda *a: None)
        self.ready.preparer = lambda: self.real
        self.addCleanup(self.release_all)

    def fetch(self, expected, target, reserve=0, cancel=None, **options):
        if cancel is not None and cancel.is_set():
            raise update_flow.DownloadError("cancelled", "stopped")
        Path(target).write_bytes(b"x")
        return Path(target)

    def release_all(self):
        self.mac.release()

    def settle(self, count):
        """Wait until ``count`` stagings have ended (workers the card no longer follows included)."""
        deadline = time.monotonic() + WAIT
        while len(self.mac.finished) < count and time.monotonic() < deadline:
            time.sleep(0.01)
        while time.monotonic() < deadline:   # The worker writes its record and lets go after staging.
            job = self.ready._job
            if job is None or job["result"] is not None:
                break
            time.sleep(0.01)
        self.finish_worker()

    def started_again(self, version, seconds=1.0):
        """Give a second worker the time it would need to reach the staging (it must not)."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and self.mac.finished.count(version) == 0 and not self.mac.overlaps:
            time.sleep(0.01)


class StayThenUpdateTests(Case):
    def test_update_right_after_stay_waits_for_the_stopped_worker(self):
        offer = update_feed.offer_for(lines.feed_of(), "1.0.9", lines.MAC)
        self.mac.hold_next("1.1.0")
        self.ready.accept(offer)
        self.assertTrue(self.mac.entered["1.1.0"].wait(WAIT))
        self.n.update_ui.decide("skip", offer)   # Stay: the worker is asked to stop, mid-verification.
        self.ready.accept(offer)                 # Update again at once.
        self.started_again("1.1.0")
        self.release_all()
        self.settle(1)
        self.settle(2 if len(self.mac.finished) > 1 else 1)
        self.assertEqual(self.mac.overlaps, [], "two workers in one folder")
        self.assertEqual(self.mac.lost, [], "a worker's stage was deleted under it")
        self.assertTrue(self.ready.ready_for(offer), "the yes that came last is what is prepared")
        self.assertIsNotNone(self.real.recall("1.1.0"))

    def test_stay_removes_the_stage_only_once_its_worker_has_stopped(self):
        offer = update_feed.offer_for(lines.feed_of(), "1.0.9", lines.MAC)
        self.mac.hold_next("1.1.0")
        self.ready.accept(offer)
        self.assertTrue(self.mac.entered["1.1.0"].wait(WAIT))
        with mock.patch.object(threading.Thread, "join", lambda self, timeout=None: None):   # A worker that outlasts any wait.
            self.n.update_ui.decide("skip", offer)
            time.sleep(0.3)
        self.release_all()
        self.settle(1)
        self.assertEqual(self.mac.lost, [], "removed while its worker still wrote there")
        deadline = time.monotonic() + WAIT
        while self.real.work("1.1.0").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(self.real.work("1.1.0").exists(), "nothing of the line stays on disk")


class StayOnOneVersionTests(Case):
    def test_staying_on_a_version_removes_it_even_when_a_later_one_of_its_line_is_taken(self):
        first = update_feed.offer_for(lines.feed_of(), "1.0.9", lines.MAC)
        self.mac.hold_next("1.1.0")
        self.ready.accept(first)
        self.assertTrue(self.mac.entered["1.1.0"].wait(WAIT))
        self.n.update_ui.decide("skip", first)
        later = update_feed.offer_for(lines.feed_of(lines=("1.1.1",)), "1.0.9", lines.MAC)
        self.ready.accept(later)   # The same line, a later release: 1.1.0 itself is still not wanted.
        self.release_all()
        self.settle(2)
        deadline = time.monotonic() + WAIT
        while self.real.work("1.1.0").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(self.real.work("1.1.0").exists())
        self.assertTrue(self.ready.ready_for(later))


class SupersedeTests(Case):
    def own(self, version):
        return update_feed.offer_for(lines.feed_of(latest=version, lines=()), "1.0.9", lines.MAC)

    def test_a_newer_release_never_cleans_a_folder_its_stopped_worker_still_writes(self):
        self.mac.hold_next("1.0.10")
        self.ready.consider(self.own("1.0.10"), manual=True)
        self.assertTrue(self.mac.entered["1.0.10"].wait(WAIT))
        self.ready.consider(self.own("1.0.11"), manual=True)   # Stops 1.0.10, prepares 1.0.11, cleans older ones.
        self.settle(1)
        self.assertTrue(self.ready.ready_for(self.own("1.0.11")))
        self.release_all()
        self.settle(2)
        self.assertEqual(self.mac.lost, [], "1.0.10's folder was removed under its worker")
        deadline = time.monotonic() + WAIT
        while self.real.work("1.0.10").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(self.real.work("1.0.10").exists(), "removed once its worker settled")
        self.assertIsNotNone(self.real.recall("1.0.11"))


class ReloadTests(Case):
    def test_the_session_after_a_reload_waits_for_the_last_ones_worker(self):
        offer = update_feed.offer_for(lines.feed_of(latest="1.0.10", lines=()), "1.0.9", lines.MAC)
        self.mac.hold_next("1.0.10")
        self.ready.consider(offer, manual=True)
        self.assertTrue(self.mac.entered["1.0.10"].wait(WAIT))
        self.ready.unregister()
        self.ready.register()
        self.ready.consider(offer, manual=True)
        self.started_again("1.0.10")
        self.release_all()
        self.settle(1)
        self.settle(2 if len(self.mac.finished) > 1 else 1)
        self.assertEqual((self.mac.overlaps, self.mac.lost), ([], []))
        self.assertTrue(self.ready.ready_for(offer))


class FolderClaimTests(unittest.TestCase):
    """``Preparer`` on its own: one worker per folder, and removals wait for it."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.mac = SlowMac(self.folder)
        self.addCleanup(self.mac.release)

    def fetch(self, expected, target, reserve=0, cancel=None, **options):
        Path(target).write_bytes(b"x")
        return Path(target)

    def make(self):
        return update_flow.Preparer(self.folder / "updates", "1.0.0", "macos-arm64", "/A/Angelo.app/Contents/MacOS/A",
                                    fetch=self.fetch, mac=self.mac, log=lambda *a: None)

    def run_prepare(self, cancel=None):
        outcome = {}

        def work():
            try:
                outcome["ready"] = self.make().prepare(flow_tests.release(), flow_tests.download(), cancel=cancel)
            except update_flow.PrepareError as error:
                outcome["error"] = error.code
        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        return thread, outcome

    def test_a_second_preparation_of_one_version_waits_and_then_reuses_the_first(self):
        self.mac.hold_next("1.0.1")
        first, one = self.run_prepare()
        self.assertTrue(self.mac.entered["1.0.1"].wait(WAIT))
        second, two = self.run_prepare()
        second.join(0.5)
        self.assertTrue(second.is_alive(), "waits for the first")
        self.mac.release()
        first.join(WAIT)
        second.join(WAIT)
        self.assertEqual((self.mac.overlaps, self.mac.lost), ([], []))
        self.assertEqual(self.mac.finished, ["1.0.1"], "the second found the first's verified stage")
        self.assertEqual(one["ready"], two["ready"])

    def test_a_stopped_waiter_leaves_without_touching_the_folder(self):
        self.mac.hold_next("1.0.1")
        first, one = self.run_prepare()
        self.assertTrue(self.mac.entered["1.0.1"].wait(WAIT))
        cancel = threading.Event()
        second, two = self.run_prepare(cancel)
        cancel.set()
        second.join(WAIT)
        self.assertFalse(second.is_alive())
        self.assertEqual(two, {"error": "download:cancelled"})
        self.mac.release()
        first.join(WAIT)
        self.assertEqual(self.mac.lost, [])
        self.assertIn("ready", one)

    def test_removals_wait_for_the_worker_and_can_be_called_off(self):
        self.mac.hold_next("1.0.1")
        first, _one = self.run_prepare()
        self.assertTrue(self.mac.entered["1.0.1"].wait(WAIT))
        work = self.make().work("1.0.1")
        self.assertEqual(self.make().clean(keep=()), [], "not removed while its worker runs")
        self.assertEqual(self.make().forget("1.0.1"), [])
        self.assertTrue(work.is_dir())
        self.mac.release()
        first.join(WAIT)
        self.assertEqual(self.mac.lost, [])
        self.assertFalse(work.exists(), "removed as its worker settled")
        self.mac.hold_next("1.0.1")
        first, _one = self.run_prepare()
        self.assertTrue(self.mac.entered["1.0.1"].wait(WAIT))
        self.make().forget("1.0.1", unless=lambda: True)   # The person said yes again before it settled.
        self.mac.release()
        first.join(WAIT)
        self.assertIsNotNone(self.make().recall("1.0.1"))

    def test_a_folder_removed_as_its_worker_lets_go_is_not_returned_as_ready(self):
        self.mac.hold_next("1.0.1")
        first, one = self.run_prepare()
        self.assertTrue(self.mac.entered["1.0.1"].wait(WAIT))
        self.make().clean(keep=())
        self.mac.release()
        first.join(WAIT)
        self.assertEqual(one, {"error": "removed"})
        self.assertFalse(self.make().work("1.0.1").exists())
        self.assertFalse(update_hold.lock_path(self.make().work("1.0.1")).exists(), "its lock file went with it")

    def test_a_failing_removal_never_costs_the_worker_its_result_or_the_next_removal(self):
        self.mac.hold_next("1.0.1")
        first, one = self.run_prepare()
        self.assertTrue(self.mac.entered["1.0.1"].wait(WAIT))

        def broken():
            raise RuntimeError("unknown")
        self.make().forget("1.0.1", unless=broken)   # Unknown: the folder stays.
        self.mac.release()
        first.join(WAIT)
        self.assertIn("ready", one)
        self.mac.hold_next("1.0.1")
        (self.make().work("1.0.1") / "ready.json").unlink()
        first, one = self.run_prepare()
        self.assertTrue(self.mac.entered["1.0.1"].wait(WAIT))
        real_rmtree, calls = update_flow.shutil.rmtree, []

        def failing_rmtree(path, *args, **kwargs):
            calls.append(path)
            if len(calls) == 1:
                raise OSError("busy")
            return real_rmtree(path, *args, **kwargs)
        with mock.patch.object(update_flow.shutil, "rmtree", failing_rmtree):
            self.make().forget("1.0.1")
            self.mac.release()
            first.join(WAIT)
        self.assertIn("ready", one, "the worker's own result stands")
        self.assertEqual(self.make().forget("1.0.1"), ["1.0.1"], "the next removal works")

    def test_one_folder_spelled_through_a_link_is_one_folder(self):
        os.symlink(self.folder, self.folder.parent / (self.folder.name + "-link"))
        self.addCleanup(os.unlink, self.folder.parent / (self.folder.name + "-link"))
        self.mac.hold_next("1.0.1")
        first, one = self.run_prepare()
        self.assertTrue(self.mac.entered["1.0.1"].wait(WAIT))
        linked = update_flow.Preparer(self.folder.parent / (self.folder.name + "-link") / "updates", "1.0.0",
                                      "macos-arm64", "/A", fetch=self.fetch, mac=self.mac, log=lambda *a: None)
        self.assertEqual(linked.forget("1.0.1"), [])
        self.mac.release()
        first.join(WAIT)
        self.assertEqual(self.mac.lost, [])
        self.assertEqual(one, {"error": "removed"}, "removed as it let go, never under it")

    def test_another_angelo_holding_the_folder_is_waited_for_and_never_cleaned_under(self):
        work = self.make().work("1.0.1")
        work.mkdir(parents=True)
        (work / "theirs").write_text("x")
        held, go = self.folder / "held", self.folder / "go"
        other = subprocess.Popen([sys.executable, "-c", OTHER_ANGELO, str(ADDONS), str(work), str(held), str(go)])
        self.addCleanup(other.kill)
        deadline = time.monotonic() + WAIT
        while not held.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(held.exists())
        self.assertEqual(self.make().clean(keep=()), [], "the other Angelo's folder stays")
        self.assertTrue((work / "theirs").is_file())
        worker, outcome = self.run_prepare()
        worker.join(0.6)
        self.assertTrue(worker.is_alive(), "waits for the other Angelo")
        self.assertEqual(self.mac.finished, [])
        go.write_text("go")
        self.assertEqual(other.wait(WAIT), 0)
        worker.join(WAIT)
        self.assertIn("ready", outcome)
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(update_hold.Stopped):
            with update_hold.hold(work, cancel):
                pass

    def test_a_folder_being_removed_is_prepared_only_afterwards(self):
        self.make().work("1.0.1").mkdir(parents=True)
        (self.make().work("1.0.1") / "old").write_text("x")
        removing, started = threading.Event(), threading.Event()
        real_rmtree = update_flow.shutil.rmtree

        def slow_rmtree(path, *args, **kwargs):
            if not removing.is_set():
                removing.set()
                started.wait(WAIT)
            return real_rmtree(path, *args, **kwargs)
        with mock.patch.object(update_flow.shutil, "rmtree", slow_rmtree):
            cleaner = threading.Thread(target=lambda: self.make().clean(keep=()), daemon=True)
            cleaner.start()
            self.assertTrue(removing.wait(WAIT))
            worker, outcome = self.run_prepare()
            worker.join(0.3)
            self.assertEqual(self.mac.finished, [], "waits while the removal runs")
            started.set()
            cleaner.join(WAIT)
            worker.join(WAIT)
        self.assertIn("ready", outcome)
        self.assertIsNotNone(self.make().recall("1.0.1"), "the preparation ran after the removal, not under it")

    def test_a_waiter_stopped_while_another_angelo_holds_never_runs_removals_queued_on_it(self):
        work = self.make().work("1.1.0")
        work.mkdir(parents=True)
        (work / "theirs").write_text("x")
        held, go = self.folder / "held", self.folder / "go"
        other = subprocess.Popen([sys.executable, "-c", OTHER_ANGELO, str(ADDONS), str(work), str(held), str(go)])
        self.addCleanup(other.kill)
        deadline = time.monotonic() + WAIT
        while not held.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        cancel, outcome = threading.Event(), {}

        def wait():
            try:
                with update_hold.hold(work, cancel):
                    outcome["held"] = True
            except update_hold.Stopped:
                outcome["stopped"] = True
        waiter = threading.Thread(target=wait, daemon=True)
        waiter.start()
        deadline = time.monotonic() + WAIT
        while update_hold._key(work) not in update_hold._holders and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.make().forget("1.1.0"), [], "queued on the waiter")
        cancel.set()   # Stay: the waiting worker is stopped.
        waiter.join(WAIT)
        self.assertEqual(outcome, {"stopped": True})
        self.assertTrue((work / "theirs").is_file(), "the other Angelo's folder was removed under it")
        go.write_text("go")
        self.assertEqual(other.wait(WAIT), 0)
        self.assertEqual(self.make().forget("1.1.0"), ["1.1.0"], "a later removal takes it")


class LockFileTests(unittest.TestCase):
    """``update_hold``'s OS lock: a deleted lock file is locked again, and Windows' branch."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp()) / "updates" / "1.0.1"
        self.addCleanup(shutil.rmtree, self.folder.parent.parent, True)

    @unittest.skipIf(update_hold.fcntl is None, environment_skip("Windows deletes no file another handle has open "
                                                                 "(its branch: the test below)"))
    def test_a_lock_on_a_file_deleted_meanwhile_is_taken_again_and_never_waited_on_forever(self):
        real_stat, calls = os.stat, []

        def moved(path, *args, **kwargs):
            found = real_stat(path, *args, **kwargs)
            if not str(path).endswith(".lock"):
                return found
            calls.append(path)
            return os.stat_result((found.st_mode, found.st_ino + 1) + tuple(found)[2:]) if len(calls) == 1 else found
        with mock.patch.object(update_hold.os, "stat", moved):
            handle = update_hold._os_lock(self.folder)
        self.assertNotIn(handle, (None, update_hold._HELD_ELSEWHERE))
        self.assertEqual(len(calls), 2, "taken again on the file at the path")
        update_hold._os_unlock(handle)
        with mock.patch.object(update_hold.os.path, "samestat", lambda a, b: False):
            self.assertIsNone(update_hold._os_lock(self.folder), "this process's hold only, not a wait")
            with update_hold.hold(self.folder, threading.Event()):
                pass

    def test_windows_locks_byte_zero_per_handle_and_unlocks(self):
        locked = {}

        class Msvcrt:
            LK_NBLCK, LK_UNLCK = 2, 0

            @staticmethod
            def locking(fd, mode, count):
                identity = os.fstat(fd).st_ino
                if mode == Msvcrt.LK_UNLCK:
                    locked.pop(identity, None)
                elif locked.get(identity, fd) != fd:
                    raise OSError(update_hold.errno.EACCES, "locked")
                else:
                    locked[identity] = fd
        with mock.patch.object(update_hold, "fcntl", None), mock.patch.object(update_hold, "msvcrt", Msvcrt), \
                mock.patch.object(update_hold.os.path, "samestat", lambda a, b: False):
            first = update_hold._os_lock(self.folder)
            self.assertNotIn(first, (None, update_hold._HELD_ELSEWHERE), "no identity check on Windows")
            self.assertIs(update_hold._os_lock(self.folder), update_hold._HELD_ELSEWHERE)
            update_hold._os_unlock(first)
            second = update_hold._os_lock(self.folder)
            self.assertNotIn(second, (None, update_hold._HELD_ELSEWHERE))
            update_hold._os_unlock(second)
            self.assertEqual(locked, {})
            with update_hold.hold(self.folder):
                self.assertTrue(locked)
            self.assertEqual(locked, {})
            real_unlink = os.unlink

            def windows_unlink(path, *args, **kwargs):   # Refused while a handle has the file open (locked here).
                if locked and str(path).endswith(".lock"):
                    raise PermissionError(13, "in use")
                return real_unlink(path, *args, **kwargs)
            self.folder.mkdir(parents=True, exist_ok=True)
            with mock.patch.object(update_hold.os, "unlink", windows_unlink):
                self.assertTrue(update_hold.remove(self.folder, lambda: os.rmdir(self.folder)))   # (samestat is stood in)
            self.assertFalse(update_hold.lock_path(self.folder).exists(), "deleted once let go")

    def test_a_removal_asked_for_while_the_os_lock_goes_still_happens(self):
        self.folder.mkdir(parents=True)
        real_unlock, asked = update_hold._os_unlock, []

        def unlock(handle):
            if not asked and hasattr(handle, "fileno"):
                asked.append(update_hold.remove(self.folder, lambda: shutil.rmtree(self.folder)))
            real_unlock(handle)
        with mock.patch.object(update_hold, "_os_unlock", unlock):
            with update_hold.hold(self.folder):
                pass
        self.assertEqual(asked, [False], "queued on the holder")
        self.assertFalse(self.folder.exists(), "and done, not refused by this process's own lock")

    def test_a_reload_keeps_what_a_waiting_worker_relies_on(self):
        self.folder.mkdir(parents=True)
        (self.folder / "theirs").write_text("x")
        held, go = self.folder.parent / "held", self.folder.parent / "go"
        other = subprocess.Popen([sys.executable, "-c", OTHER_ANGELO, str(ADDONS), str(self.folder), str(held),
                                  str(go)])
        self.addCleanup(other.kill)
        deadline = time.monotonic() + WAIT
        while not held.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        cancel, outcome, stopped = threading.Event(), {}, update_hold.Stopped

        def wait():
            try:
                with update_hold.hold(self.folder, cancel):
                    outcome["held"] = True
            except Exception as error:
                outcome["raised"] = error
        waiter = threading.Thread(target=wait, daemon=True)
        waiter.start()
        while update_hold._key(self.folder) not in update_hold._holders and time.monotonic() < deadline:
            time.sleep(0.01)
        importlib.reload(update_hold)   # The add-on is reloaded while the worker waits.
        self.assertIs(update_hold.Stopped, stopped)
        self.assertFalse(update_hold.remove(self.folder, lambda: shutil.rmtree(self.folder)))
        cancel.set()
        waiter.join(WAIT)
        self.assertIsInstance(outcome.get("raised"), update_hold.Stopped)
        self.assertTrue((self.folder / "theirs").is_file(), "the other Angelo's folder was removed under it")
        go.write_text("go")
        self.assertEqual(other.wait(WAIT), 0)


if __name__ == "__main__":
    unittest.main()
