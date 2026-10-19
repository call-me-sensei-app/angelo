# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Isolated real OS capture-pin controls, only in owned temporary profiles.

No live producer, Blender/native execution, metadata completion or payload
verification. Windows runtime behavior is established only when run on Windows.
Cross-thread pin transfer is tested only after prior use has finished; concurrent
release/abandon of one owner is outside this contract.
"""

import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ADDONS = Path(__file__).resolve().parents[3] / "scripts/addons_core"
sys.path.insert(0, str(ADDONS))
from angelo_agent import checkpoint_capture_pins as pins
from angelo_agent.checkpoint_filesystem import anchored_files_supported
from angelo_agent.checkpoint_transactions import _transaction, holds_transaction

SESSION = "00000000-0000-4000-8000-000000000001"
CAPTURE = "00000000-0000-4000-8000-000000000002"
CHILD = r'''
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from angelo_agent.checkpoint_capture_pins import acquire_capture_pin, capture_is_active, release_capture_pin
from angelo_agent.checkpoint_transactions import _transaction, holds_transaction
root, session, capture, mode, signal = Path(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5], Path(sys.argv[6])
def await_signal():
    deadline = time.monotonic() + 10
    while not signal.exists():
        if time.monotonic() >= deadline:
            raise RuntimeError("Owned child handshake timed out")
        time.sleep(.01)
if mode == "probe":
    with _transaction(root):
        print(json.dumps({"active": capture_is_active(root, session, capture), "profile": holds_transaction(root)}), flush=True)
elif mode == "profile":
    with _transaction(root):
        print("READY", flush=True)
        await_signal()
    print("DONE", flush=True)
else:
    with _transaction(root):
        pin = acquire_capture_pin(root, session, capture)
    print("READY", flush=True)
    try:
        await_signal()
        if mode == "crash":
            os._exit(23)
    finally:
        with _transaction(root):
            release_capture_pin(pin)
    print("DONE", flush=True)
'''


class HostileError(Exception):
    def __init__(self, hooks):
        super().__init__("owned hostile fixture error")
        self.hooks = hooks

    def __str__(self):
        self.hooks.append("str")
        raise AssertionError("Exception formatting must not run")

    def __repr__(self):
        self.hooks.append("repr")
        raise AssertionError("Exception formatting must not run")

    def __bool__(self):
        self.hooks.append("bool")
        raise AssertionError("Exception truthiness must not run")


@unittest.skipUnless(anchored_files_supported(), "descriptor-anchored storage required")
class CapturePinTests(unittest.TestCase):
    def move_held_folder(self, folder, moved):
        """Move the folder of a held pin: True when it moved. Windows refuses, since the pin's open file keeps its
        folder from being renamed, so the replacement these tests stage cannot happen there."""
        if os.name == "nt":
            with self.assertRaises(PermissionError):
                folder.rename(moved)
            return False
        folder.rename(moved)
        return True

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="angelo-capture-pin-")
        self.root = Path(self.temp.name).resolve() / "profile"
        self.root.mkdir()
        self.owned = []
        self.children = []
        self.threads = []

    def tearDown(self):
        try:
            for child in self.children:
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=2)
            for thread in self.threads:
                thread.join(timeout=3)
                self.assertFalse(thread.is_alive(), "Owned probe thread did not stop")
            for pin in self.owned:
                if pin._resource is not None:
                    with _transaction(self.root):
                        pins.release_capture_pin(pin)
        finally:
            # If a test assertion failed, close only its owned descriptors;
            # do not reacquire a profile lock a failed probe might hold.
            try:
                for pin in self.owned:
                    pins.abandon_capture_pin(pin)
                for thread in self.threads:
                    thread.join(timeout=3)
            finally:
                self.temp.cleanup()

    @property
    def pin_path(self):
        return self.root / "checkpoint-captures" / SESSION / (CAPTURE + ".lock")

    def acquire(self):
        with _transaction(self.root):
            pin = pins.acquire_capture_pin(self.root, SESSION, CAPTURE)
        self.owned.append(pin)
        return pin

    def probe(self):
        with _transaction(self.root):
            return pins.capture_is_active(self.root, SESSION, CAPTURE)

    def release(self, pin):
        with _transaction(self.root):
            pins.release_capture_pin(pin)

    def assert_closed(self, descriptor):
        with self.assertRaises(OSError) as caught:
            os.fstat(descriptor)
        self.assertEqual(caught.exception.errno, errno.EBADF)

    def start_child(self, mode):
        index = len(self.children)
        output = Path(self.temp.name) / ("child-%d.log" % index)
        signal = Path(self.temp.name) / ("release-%d" % index)
        with output.open("wb") as destination:
            child = subprocess.Popen([sys.executable, "-B", "-c", CHILD, str(ADDONS), str(self.root), SESSION,
                                      CAPTURE, mode, str(signal)], stdout=destination, stderr=subprocess.STDOUT)
        self.children.append(child)
        return child, output, signal

    def await_ready(self, child, output):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            text = output.read_text()
            if text.startswith("READY\n"):
                return
            if child.poll() is not None:
                self.fail("Owned child exited before readiness: " + text[:2000])
            time.sleep(.01)
        self.fail("Owned child readiness timed out: " + output.read_text()[:2000])

    def test_absent_probe_does_not_create_namespace(self):
        self.assertFalse(self.probe())
        self.assertFalse((self.root / "checkpoint-captures").exists())

    def test_transaction_required_before_resource_mutation(self):
        with self.assertRaises(RuntimeError):
            pins.acquire_capture_pin(self.root, SESSION, CAPTURE)
        with self.assertRaises(RuntimeError):
            pins.capture_is_active(self.root, SESSION, CAPTURE)
        self.assertFalse(self.pin_path.exists())
        pin = self.acquire()
        descriptor = pin._resource.descriptor
        with self.assertRaises(RuntimeError):
            pins.release_capture_pin(pin)
        self.assertIsNotNone(pin._resource)
        self.assertTrue(self.probe())
        os.fstat(descriptor)  # Caller can retry release in the proper scope.
        with _transaction(self.root.parent):
            with self.assertRaises(RuntimeError):
                pins.release_capture_pin(pin)
        self.release(pin)
        self.assert_closed(descriptor)

    def test_exact_names_owner_and_noninheritable_descriptor(self):
        with _transaction(self.root):
            for session, capture in (("../bad", CAPTURE), (SESSION, "AAAAAAAA-0000-4000-8000-000000000002"),
                                     (SESSION, True)):
                with self.subTest(session=session, capture=capture), self.assertRaises(ValueError):
                    pins.acquire_capture_pin(self.root, session, capture)
        with self.assertRaises(TypeError):
            pins.CapturePin()
        with self.assertRaises(TypeError):
            pins.release_capture_pin(object())
        with self.assertRaises(TypeError):
            pins.abandon_capture_pin(object())
        pin = self.acquire()
        self.assertFalse(os.get_inheritable(pin._resource.descriptor))

    def test_same_thread_fresh_probe_does_not_unlock_outer_owner(self):
        pin = self.acquire()
        for _ in range(2):
            self.assertTrue(self.probe())
        with _transaction(self.root), self.assertRaises(FileExistsError):
            pins.acquire_capture_pin(self.root, SESSION, CAPTURE)
        self.assertTrue(self.probe())
        self.release(pin)
        self.assertFalse(self.probe())

    def test_second_thread_enters_profile_and_observes_active_pin(self):
        self.acquire()
        result, done = {}, threading.Event()
        def observe():
            try:
                with _transaction(self.root):
                    result.update(profile=holds_transaction(self.root), active=pins.capture_is_active(self.root, SESSION, CAPTURE))
            except BaseException as error:
                result["error"] = repr(error)
            finally:
                done.set()
        thread = threading.Thread(target=observe, daemon=True)
        self.threads.append(thread)
        thread.start()
        self.assertTrue(done.wait(3), "Capture retained the profile mutex or a probe waited")
        thread.join(timeout=3)
        self.assertEqual(result, {"profile": True, "active": True})

    def test_real_second_process_observes_parent_pin(self):
        self.acquire()
        child, output, _ = self.start_child("probe")
        self.assertEqual(child.wait(timeout=5), 0, output.read_text())
        self.assertEqual(json.loads(output.read_text()), {"active": True, "profile": True})
        self.assertTrue(self.probe())

    def test_quiescent_thread_handoff_abandons_actual_descriptor(self):
        pin = self.acquire()  # Acquisition and its profile transaction are finished.
        descriptor = pin._resource.descriptor
        errors = []
        def recipient():
            try:
                pins.abandon_capture_pin(pin)
            except BaseException as error:
                errors.append(error)
        thread = threading.Thread(target=recipient, daemon=True)
        self.threads.append(thread)
        thread.start()
        # The previous owner performs no pin operation until the recipient exits.
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive(), "Quiescent recipient did not finish")
        self.assertEqual(errors, [])
        self.assertIsNone(pin._resource)
        self.assert_closed(descriptor)
        before = self.pin_path.read_bytes()
        self.assertFalse(self.probe())  # Independent descriptor observes the stale pin unlocked.
        pins.abandon_capture_pin(pin)
        self.release(pin)
        self.assert_closed(descriptor)
        self.assertEqual(self.pin_path.read_bytes(), before)

    def test_real_child_owner_releases_and_removes_its_pin(self):
        child, output, signal = self.start_child("hold")
        self.await_ready(child, output)
        self.assertTrue(self.probe())
        signal.write_text("release")
        self.assertEqual(child.wait(timeout=5), 0, output.read_text())
        self.assertIn("DONE", output.read_text())
        self.assertFalse(self.probe())
        self.assertFalse(self.pin_path.exists())

    def test_real_crash_unlocks_but_stale_name_is_not_new_lifetime(self):
        child, output, signal = self.start_child("crash")
        self.await_ready(child, output)
        self.assertTrue(self.probe())
        signal.write_text("crash")
        self.assertEqual(child.wait(timeout=5), 23, output.read_text())
        self.assertTrue(self.pin_path.is_file())
        # OS release may be briefly delayed on Windows; never age-delete the pin.
        deadline = time.monotonic() + 3
        while self.probe() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(self.probe())
        with _transaction(self.root), self.assertRaises(FileExistsError):
            pins.acquire_capture_pin(self.root, SESSION, CAPTURE)
        self.assertTrue(self.pin_path.exists())  # Owned TemporaryDirectory removes this test residue.

    def test_release_restores_lock_offset_and_is_inert_when_repeated(self):
        offsets = []
        original = pins.lock_descriptor
        def checked_lock(fd, timeout, message):
            release = original(fd, timeout, message)
            def checked_release():
                offsets.append(os.lseek(fd, 0, os.SEEK_CUR))
                release()
            return checked_release
        with patch.object(pins, "lock_descriptor", checked_lock):
            pin = self.acquire()
        os.lseek(pin._resource.descriptor, 7, os.SEEK_SET)
        self.release(pin)
        self.assertEqual(offsets, [0])
        self.pin_path.write_bytes(b"replacement")
        self.release(pin)
        self.assertEqual(self.pin_path.read_bytes(), b"replacement")
        self.assertEqual(offsets, [0])

    def test_replaced_pin_is_preserved_while_owned_descriptor_closes(self):
        pin = self.acquire()
        descriptor = pin._resource.descriptor
        moved = self.pin_path.with_suffix(".old")
        self.pin_path.rename(moved)
        self.pin_path.write_bytes(b"replacement")
        with self.assertRaises(ValueError):
            self.release(pin)
        self.assertIsNone(pin._resource)
        self.assert_closed(descriptor)
        self.assertEqual(self.pin_path.read_bytes(), b"replacement")
        self.assertTrue(moved.exists())

    def test_replaced_directory_is_preserved_while_owned_descriptor_closes(self):
        pin = self.acquire()
        descriptor = pin._resource.descriptor
        parent = self.pin_path.parent
        if not self.move_held_folder(parent, parent.with_name(SESSION + ".old")):
            self.release(pin)
            return
        parent.mkdir()
        self.pin_path.write_bytes(b"replacement")
        with self.assertRaises(ValueError):
            self.release(pin)
        self.assert_closed(descriptor)
        self.assertEqual(self.pin_path.read_bytes(), b"replacement")

    def test_links_are_not_probe_or_cleanup_authority(self):
        self.pin_path.parent.mkdir(parents=True)
        other = self.root / "owned-other"
        other.write_bytes(b"keep")
        try:
            self.pin_path.symlink_to(other)
        except (NotImplementedError, PermissionError):
            self.skipTest("Host cannot create a symlink control")
        with self.assertRaises((OSError, ValueError)):
            self.probe()
        self.pin_path.unlink()
        os.link(other, self.pin_path)
        with self.assertRaises(ValueError):
            self.probe()
        self.assertEqual(other.read_bytes(), b"keep")

    def test_disappearance_after_open_is_error_not_inactive(self):
        self.pin_path.parent.mkdir(parents=True)
        self.pin_path.write_bytes(b"stale")
        original, opened = pins._open_file, []
        def disappear(folder, name, flags, *args):
            fd = original(folder, name, flags, *args)
            opened.append(fd)
            self.pin_path.unlink()
            return fd
        with patch.object(pins, "_open_file", disappear), self.assertRaises(FileNotFoundError):
            self.probe()
        self.assert_closed(opened[0])

    def test_unexpected_lock_io_error_is_not_inactive(self):
        self.pin_path.parent.mkdir(parents=True)
        self.pin_path.write_bytes(b"stale")
        original, opened = pins._open_file, []
        def record_open(*args):
            fd = original(*args)
            opened.append(fd)
            return fd
        with (patch.object(pins, "_open_file", record_open),
              patch.object(pins, "lock_descriptor", side_effect=OSError(errno.EIO, "owned fixture I/O failure")),
              self.assertRaises(OSError) as caught):
            self.probe()
        self.assertEqual(caught.exception.errno, errno.EIO)
        self.assert_closed(opened[0])
        self.assertEqual(self.pin_path.read_bytes(), b"stale")

    def test_namespace_link_cannot_redirect_pin_creation(self):
        other = self.root / "owned-other-directory"
        other.mkdir()
        try:
            (self.root / "checkpoint-captures").symlink_to(other, target_is_directory=True)
        except (NotImplementedError, PermissionError):
            self.skipTest("Host cannot create a directory symlink control")
        with _transaction(self.root), self.assertRaises((OSError, ValueError)):
            pins.acquire_capture_pin(self.root, SESSION, CAPTURE)
        self.assertEqual(list(other.iterdir()), [])

    def test_acquire_failure_cleans_only_created_pin_and_closes_descriptor(self):
        original, opened = pins._open_file, []
        original_sync, sync_calls = pins.sync_directory, []
        def record_open(*args):
            fd = original(*args)
            opened.append(fd)
            return fd
        def fail_first_sync(folder):
            sync_calls.append(folder)
            if len(sync_calls) == 1:
                raise OSError("owned fixture fsync failure")
            original_sync(folder)
        with (patch.object(pins, "_open_file", record_open),
              patch.object(pins, "sync_directory", fail_first_sync),
              _transaction(self.root), self.assertRaises(OSError)):
            pins.acquire_capture_pin(self.root, SESSION, CAPTURE)
        self.assertFalse(self.pin_path.exists())
        self.assert_closed(opened[0])

    def test_unlock_error_still_closes_without_removing_unconfirmed_path(self):
        pin = self.acquire()
        descriptor = pin._resource.descriptor
        pin._resource.unlock = lambda: (_ for _ in ()).throw(OSError("owned unlock failure"))
        with self.assertRaises(OSError):
            self.release(pin)
        self.assert_closed(descriptor)
        self.assertTrue(self.pin_path.exists())
        self.assertFalse(self.probe())


    def test_abandon_outside_transaction_keeps_metadata_and_stale_name(self):
        pin = self.acquire()
        descriptor = pin._resource.descriptor
        metadata = self.root / "owned-checkpoint.json"
        metadata.write_bytes(b'{"state":"READY","capture_validation":"PENDING"}')
        # Windows' byte-range lock makes the held pin unreadable through another handle until it is released.
        before_pin = None if os.name == "nt" else self.pin_path.read_bytes()
        before_metadata = metadata.read_bytes()
        with (patch.object(pins, "_transaction_root", side_effect=AssertionError("No profile acquisition")),
              patch.object(pins, "_file_info", side_effect=AssertionError("No pathname inspection")),
              patch.object(pins, "_unlink_file", side_effect=AssertionError("No pathname deletion")),
              patch.object(pins, "assert_directory_identity", side_effect=AssertionError("No pathname walk"))):
            pins.abandon_capture_pin(pin)
        self.assertIsNone(pin._resource)
        self.assert_closed(descriptor)
        if before_pin is not None:
            self.assertEqual(self.pin_path.read_bytes(), before_pin)
        self.assertTrue(self.pin_path.exists())
        self.assertEqual(metadata.read_bytes(), before_metadata)
        child, output, _ = self.start_child("probe")
        self.assertEqual(child.wait(timeout=5), 0, output.read_text())
        self.assertEqual(json.loads(output.read_text()), {"active": False, "profile": True})

    def test_abandon_keeps_replaced_pin_and_directory(self):
        pin = self.acquire()
        descriptor = pin._resource.descriptor
        parent = self.pin_path.parent
        moved = parent.with_name(SESSION + ".old")
        if not self.move_held_folder(parent, moved):
            pins.abandon_capture_pin(pin)
            self.assert_closed(descriptor)
            return
        parent.mkdir()
        self.pin_path.write_bytes(b"replacement")
        pins.abandon_capture_pin(pin)
        self.assert_closed(descriptor)
        self.assertEqual(self.pin_path.read_bytes(), b"replacement")
        self.assertTrue((moved / self.pin_path.name).exists())

    def test_repeated_abandon_and_release_do_not_close_reused_descriptor(self):
        pin = self.acquire()
        descriptor = pin._resource.descriptor
        pins.abandon_capture_pin(pin)
        other = self.root / "owned-reused-fd"
        opened = os.open(other, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            if opened != descriptor:
                os.dup2(opened, descriptor)  # Reuse only the just-closed fixture-owned slot.
            pins.abandon_capture_pin(pin)
            self.release(pin)
            os.write(descriptor, b"still open")
            self.assertEqual(other.read_bytes(), b"still open")
            self.assertTrue(self.pin_path.exists())
        finally:
            os.close(descriptor)
            if opened != descriptor:
                os.close(opened)

    def test_normal_release_consumes_before_first_fallible_check(self):
        pin = self.acquire()
        resource = pin._resource
        descriptor = resource.descriptor
        original_check, original_unlink = pins.assert_directory_identity, pins._unlink_file
        checks, removed = [], []
        def reenter(path, folder):
            checks.append(path)
            self.assertIsNone(pin._resource)
            pins.release_capture_pin(pin)
            pins.abandon_capture_pin(pin)
            os.fstat(descriptor)  # Nested calls did not close the outer local owner.
            original_check(path, folder)
        def record_unlink(folder, name):
            removed.append(name)
            original_unlink(folder, name)
        with _transaction(self.root), patch.object(pins, "assert_directory_identity", reenter), \
                patch.object(pins, "_unlink_file", record_unlink):
            pins.release_capture_pin(pin)
        self.assertEqual(len(checks), 2)
        self.assertEqual(removed, [self.pin_path.name])
        self.assert_closed(descriptor)
        self.assertFalse(self.pin_path.exists())

    def test_abandon_consumes_before_unlock_reentry_and_restores_offset(self):
        pin = self.acquire()
        resource = pin._resource
        descriptor, original_unlock = resource.descriptor, resource.unlock
        observed = []
        def reenter():
            self.assertIsNone(pin._resource)
            pins.abandon_capture_pin(pin)
            observed.append(os.lseek(descriptor, 0, os.SEEK_CUR))
            os.fstat(descriptor)
            original_unlock()
        resource.unlock = reenter
        os.lseek(descriptor, 9, os.SEEK_SET)
        pins.abandon_capture_pin(pin)
        self.assertEqual(observed, [0])
        self.assert_closed(descriptor)
        self.assertTrue(self.pin_path.exists())

    def test_real_profile_timeout_finally_abandons_without_claiming_completion(self):
        pin = self.acquire()
        descriptor = pin._resource.descriptor
        metadata = self.root / "owned-checkpoint.json"
        metadata.write_bytes(b'{"state":"READY","capture_validation":"PENDING"}')
        child, output, signal = self.start_child("profile")
        self.await_ready(child, output)
        started = time.monotonic()
        try:
            with self.assertRaises(RuntimeError):
                try:
                    with _transaction(self.root):
                        self.fail("The child still owns the profile transaction")
                finally:
                    pins.abandon_capture_pin(pin)
            self.assertLess(time.monotonic() - started, 4)
            self.assert_closed(descriptor)
            self.assertTrue(self.pin_path.exists())
            self.assertEqual(metadata.read_bytes(), b'{"state":"READY","capture_validation":"PENDING"}')
        finally:
            signal.write_text("release")
            child.wait(timeout=5)
        self.assertEqual(child.returncode, 0, output.read_text())
        self.assertFalse(self.probe())

    def test_single_hostile_primary_is_preserved_without_hooks(self):
        pin = self.acquire()
        descriptor = pin._resource.descriptor
        hooks = []
        primary = HostileError(hooks)
        def refuse(*_args):
            raise primary
        with _transaction(self.root), patch.object(pins, "assert_directory_identity", refuse):
            with self.assertRaises(HostileError) as caught:
                pins.release_capture_pin(pin)
        self.assertIs(caught.exception, primary)
        self.assertEqual(hooks, [])
        self.assert_closed(descriptor)
        self.assertTrue(self.pin_path.exists())

    def test_primary_and_each_cleanup_phase_survive_without_hooks(self):
        # A failed seek intentionally skips unlock, so exercise the two mutually
        # exclusive failure paths separately while still closing real resources.
        for unlock_phase in ("seek", "unlock"):
            with self.subTest(phase=unlock_phase):
                pin = self.acquire()
                resource = pin._resource
                descriptor = resource.descriptor
                folder_descriptor = resource.folder if type(resource.folder) is int else resource.folder.fd
                hooks, calls = [], []
                primary, lock_error, close_error, stack_error = [HostileError(hooks) for _ in range(4)]
                original_seek, original_close, original_unlock = os.lseek, os.close, resource.unlock
                def refuse(*_args):
                    raise primary
                def seek(fd, offset, whence):
                    if fd == descriptor and unlock_phase == "seek":
                        calls.append("seek")
                        raise lock_error
                    return original_seek(fd, offset, whence)
                def unlock():
                    calls.append("unlock")
                    original_unlock()
                    if unlock_phase == "unlock":
                        raise lock_error
                def close(fd):
                    original_close(fd)
                    if fd == descriptor:
                        calls.append("close")
                        raise close_error
                def stack_failure():
                    calls.append("stack")
                    raise stack_error
                resource.unlock = unlock
                resource.stack.callback(stack_failure)
                # Patch only within an already held transaction, leaving its
                # own descriptor acquisition/release outside the fault scope.
                with _transaction(self.root):
                    with (patch.object(pins, "assert_directory_identity", refuse),
                          patch.object(os, "lseek", seek), patch.object(os, "close", close),
                          self.assertRaises(BaseExceptionGroup) as caught):
                        pins.release_capture_pin(pin)
                phases = caught.exception.exceptions
                self.assertEqual([item.message for item in phases],
                                 ["pin release", "pin " + unlock_phase, "pin close", "pin directory cleanup"])
                for item, expected in zip(phases, (primary, lock_error, close_error, stack_error)):
                    self.assertIs(item.exceptions[0], expected)
                self.assertEqual(calls, [unlock_phase, "close", "stack"])
                self.assertEqual(hooks, [])
                self.assert_closed(descriptor)
                self.assert_closed(folder_descriptor)
                self.assertIsNone(pin._resource)
                self.assertTrue(self.pin_path.exists())
                self.assertFalse(self.probe())
                self.pin_path.unlink()  # Only this fixture's observed stale pin, for the second case.

    def test_abandon_close_diagnostic_never_retries_reused_fd(self):
        pin = self.acquire()
        descriptor = pin._resource.descriptor
        original_close, replacement, calls = os.close, [], []
        error = OSError(errno.EIO, "owned close returned an uncertain status")
        other = self.root / "owned-close-replacement"
        def close(fd):
            if fd != descriptor:
                return original_close(fd)
            calls.append(fd)
            original_close(fd)
            opened = os.open(other, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
            replacement.append(opened)
            if opened != descriptor:
                os.dup2(opened, descriptor)
            raise error  # Real close occurred; the helper may not assume that or retry.
        try:
            with patch.object(os, "close", close), self.assertRaises(OSError) as caught:
                pins.abandon_capture_pin(pin)
            self.assertIs(caught.exception, error)
            pins.abandon_capture_pin(pin)
            self.release(pin)
            os.write(descriptor, b"not retried")
            self.assertEqual(other.read_bytes(), b"not retried")
            self.assertEqual(calls, [descriptor])
            self.assertTrue(self.pin_path.exists())
        finally:
            if replacement:
                original_close(descriptor)
                if replacement[0] != descriptor:
                    original_close(replacement[0])

    def test_acquisition_primary_and_cleanup_error_are_both_preserved(self):
        primary = OSError(errno.EIO, "owned acquisition sync failure")
        cleanup = OSError(errno.EACCES, "owned cleanup unlink failure")
        original, opened = pins._open_file, []
        def record_open(*args):
            fd = original(*args)
            opened.append(fd)
            return fd
        with _transaction(self.root):
            with (patch.object(pins, "_open_file", record_open),
                  patch.object(pins, "sync_directory", side_effect=primary),
                  patch.object(pins, "_unlink_file", side_effect=cleanup),
                  self.assertRaises(BaseExceptionGroup) as caught):
                pins.acquire_capture_pin(self.root, SESSION, CAPTURE)
        groups = caught.exception.exceptions
        self.assertEqual([item.message for item in groups], ["pin acquisition", "pin acquisition cleanup"])
        self.assertIs(groups[0].exceptions[0], primary)
        self.assertIs(groups[1].exceptions[0], cleanup)
        self.assert_closed(opened[0])
        self.assertTrue(self.pin_path.exists())
        self.assertFalse(self.probe())


if __name__ == "__main__":
    unittest.main()
