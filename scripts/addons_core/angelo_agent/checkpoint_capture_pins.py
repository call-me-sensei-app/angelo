# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Private per-capture OS resource owned across the native Save lifecycle.

Only this independent file lock survives outside the caller's profile
transaction. Probes always use a fresh open and never wait. A stale pin is not
completed capture evidence. Record lifetime, native verification and consumer
admission belong to the capture owner and its exact native result.

Each pin has one synchronous resource owner. A cross-thread handoff must be
quiescent: acquisition and all previous use have finished, and release/abandon
never overlap. Detaching the resource protects synchronous reentry after that
point; its separate read/check/clear operations are not a cross-thread atomic
exchange or a free-threaded/trace-concurrent ownership guarantee.
"""

import os
from contextlib import ExitStack
from pathlib import Path

from .checkpoint_filesystem import (
    _file_info, _open_file, _private_directory, _unlink_file,
    assert_directory_identity, require_anchored_files, same_file, sync_directory,
)
from .checkpoint_policy import _identity
from .checkpoint_transactions import holds_transaction, lock_descriptor

_NAMESPACE = "checkpoint-captures"


class CapturePin:
    """Single-owner descriptor; transfer only quiescently, never use concurrently.

    This is not checkpoint publication or verification authority.
    """

    __slots__ = ("_root", "_path", "_name", "_resource")

    def __init__(self):
        raise TypeError("Capture pins are created only by acquire_capture_pin")


class _PinResource:
    """Allocated at acquisition; detached once before any release-side I/O."""

    __slots__ = ("folder", "descriptor", "opened", "unlock", "stack", "error_phase", "error",
                 "cleanup_error", "seek_error", "unlock_error", "close_error", "stack_error")

    def __init__(self):
        self.folder = self.descriptor = self.opened = self.unlock = None
        self.stack = ExitStack()
        self.error_phase = self.error = self.cleanup_error = None
        self.seek_error = self.unlock_error = self.close_error = self.stack_error = None


def _transaction_root(root):
    root = Path(root)
    if not root.is_absolute() or not holds_transaction(root):
        raise RuntimeError("Capture pin operations require the matching profile transaction")
    require_anchored_files()
    return root


def _location(root, session_id, capture_id):
    return root / _NAMESPACE / _identity(session_id), _identity(capture_id) + ".lock"


def _matching(folder, name, descriptor, opened):
    named, current = _file_info(folder, name), os.fstat(descriptor)
    if (not same_file(opened, named) or not same_file(opened, current)
            or current.st_nlink != 1):
        raise ValueError("The capture pin no longer names its exact owned file")


def _unlock(resource):
    release, resource.unlock = resource.unlock, None  # Never retry an ambiguous unlock.
    if release is None:
        return True
    try:
        os.lseek(resource.descriptor, 0, os.SEEK_SET)
    except BaseException as error:
        resource.seek_error = error
        return False  # Windows must not unlock a range at an unconfirmed offset.
    try:
        release()
    except BaseException as error:
        resource.unlock_error = error
        return False
    return True


def _close(resource):
    """Attempt every owned cleanup; preserve errors without invoking their hooks."""
    try:
        _unlock(resource)
    finally:
        try:
            descriptor, resource.descriptor = resource.descriptor, None
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except BaseException as error:
                    resource.close_error = error  # No retry of a possibly reused fd.
        finally:
            stack, resource.stack = resource.stack, None
            try:
                stack.close()
            except BaseException as error:
                resource.stack_error = error


def _raise_errors(resource):
    # Collect/allocate diagnostic containers only after all resource cleanup.
    errors = [(phase, error) for phase, error in (
        (resource.error_phase, resource.error),
        ("pin acquisition cleanup", resource.cleanup_error),
        ("pin seek", resource.seek_error),
        ("pin unlock", resource.unlock_error),
        ("pin close", resource.close_error),
        ("pin directory cleanup", resource.stack_error),
    ) if error is not None]
    if len(errors) == 1:
        raise errors[0][1]
    if errors:
        raise BaseExceptionGroup("Capture pin operation and cleanup failed", [
            BaseExceptionGroup(phase, [error]) for phase, error in errors])


def acquire_capture_pin(root, session_id, capture_id):
    """Create a new named pin under profile transaction; never reuse an old file.

    O_EXCL rejects a stale existing pin. After deletion, only the later record
    owner can enforce that a completed capture ID is not given another lifetime.
    """
    root = _transaction_root(root)
    path, name = _location(root, session_id, capture_id)
    resource = _PinResource()
    pin = object.__new__(CapturePin)
    pin._root, pin._path, pin._name, pin._resource = root, path, name, resource
    try:
        resource.folder = folder = resource.stack.enter_context(_private_directory(path, create=True))
        resource.descriptor = descriptor = _open_file(folder, name, os.O_RDWR | os.O_CREAT | os.O_EXCL)
        resource.opened = os.fstat(descriptor)
        os.set_inheritable(descriptor, False)
        assert_directory_identity(path, folder)
        _matching(folder, name, descriptor, resource.opened)
        resource.unlock = lock_descriptor(descriptor, 0, "This capture pin is already owned")
        _matching(folder, name, descriptor, resource.opened)
        sync_directory(folder)
        return pin
    except BaseException as error:
        pin._resource = None
        resource.error_phase, resource.error = "pin acquisition", error
        try:
            # Failure cannot authorize a replacement's removal. Make a bounded
            # cleanup attempt only when the newly opened identity is known.
            if resource.opened is not None:
                assert_directory_identity(path, resource.folder)
                _matching(resource.folder, name, resource.descriptor, resource.opened)
                _unlink_file(resource.folder, name)
                sync_directory(resource.folder)
        except BaseException as cleanup_error:
            resource.cleanup_error = cleanup_error
        finally:
            _close(resource)
        _raise_errors(resource)


def capture_is_active(root, session_id, capture_id):
    """Probe an existing pin on an independent descriptor, including same thread.

    False means absent or currently unlocked, never valid/completed metadata.
    All non-contention I/O/identity errors remain errors rather than False.
    """
    root = _transaction_root(root)
    path, name = _location(root, session_id, capture_id)
    with ExitStack() as stack:
        try:
            folder = stack.enter_context(_private_directory(path))
            descriptor = _open_file(folder, name, os.O_RDWR)
        except FileNotFoundError:
            return False  # Only initial absence, never disappearance after a successful open.
        try:
            opened = os.fstat(descriptor)
            assert_directory_identity(path, folder)
            _matching(folder, name, descriptor, opened)
            try:
                release = lock_descriptor(descriptor, 0, "The capture is still active")
            except RuntimeError:
                return True  # lock_descriptor raises RuntimeError only for contention.
            try:
                _matching(folder, name, descriptor, opened)
                return False
            finally:
                os.lseek(descriptor, 0, os.SEEK_SET)
                release()
        finally:
            os.close(descriptor)


def release_capture_pin(pin):
    """Remove only this owned pin. No checkpoint payload or metadata is touched.

    Outside the required transaction, refuse before touching the resource so
    its caller can retry. After admission consume the owner before I/O, so a
    reentrant release cannot close it twice. Never delete a replacement.
    """
    if type(pin) is not CapturePin:
        raise TypeError("An exact capture pin owner is required")
    _transaction_root(pin._root)  # Must precede even the closed-state fast path.
    resource = pin._resource
    if resource is None:
        return
    pin._resource = None
    try:
        assert_directory_identity(pin._path, resource.folder)
        _matching(resource.folder, pin._name, resource.descriptor, resource.opened)
        if _unlock(resource):
            # Still under profile transaction. Recheck after unlock; a changed
            # pathname must never become deletion authority.
            assert_directory_identity(pin._path, resource.folder)
            _matching(resource.folder, pin._name, resource.descriptor, resource.opened)
            _unlink_file(resource.folder, pin._name)
            sync_directory(resource.folder)
    except BaseException as error:
        resource.error_phase, resource.error = "pin release", error
    finally:
        _close(resource)
    _raise_errors(resource)


def abandon_capture_pin(pin):
    """Close this exact resource without a transaction or any pathname effects.

    The stale name and existing metadata survive. This is not normal release,
    terminal capture evidence, payload authority or permission to reuse its ID.
    """
    if type(pin) is not CapturePin:
        raise TypeError("An exact capture pin owner is required")
    resource = pin._resource
    if resource is None:
        return
    pin._resource = None
    _close(resource)
    _raise_errors(resource)
