# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Structural capture reads, active-resource admission and legacy scene gates.

V4 journals remain readable independently of the complete native scene pair.
Neither a declared terminal state nor a successful decode grants scene access.
"""

import stat

from .checkpoint_capture_pins import _NAMESPACE, capture_is_active
from .checkpoint_capture_records import validate_record
from .checkpoint_filesystem import anchored_files_supported, _private_directory, directory_info, entry_info
from .checkpoint_policy import _identity, scene_usable
from .checkpoint_records import _record
from .checkpoint_transactions import holds_transaction

V4_SCENE_UNAVAILABLE = "Sculpt checkpoint scene verification and recovery are not available"


def decode_record(value, identity):
    """Dispatch exact metadata containers; both routes validate declarations only."""
    if (type(value) is not dict or any(type(key) is not str for key in value)
            or type(value.get("version", 1)) is not int):
        raise ValueError("Invalid snapshot metadata")
    return validate_record(value, identity) if value.get("version") == 4 else _record(value, identity)


def is_bundle(record):
    """For already decoded records only; a bundle declaration is not authority."""
    return record.get("version", 1) == 4


def capture_activity(root, session_id, identity):
    """Probe real pins, or establish whole-namespace absence for legacy reads."""
    if not holds_transaction(root):
        raise RuntimeError("Capture admission requires the matching profile transaction")
    session_id, identity = _identity(session_id), _identity(identity)
    if anchored_files_supported():
        return capture_is_active(root, session_id, identity)
    with _private_directory(root) as folder:
        before = directory_info(folder)
        if not stat.S_ISDIR(before.st_mode):
            raise ValueError("Capture admission requires an existing profile directory")
        try:
            entry_info(folder, _NAMESPACE)
        except FileNotFoundError:
            pass  # Only the initial namespace lookup may establish absence.
        else:
            raise ValueError("Existing capture pins require descriptor-relative storage")
        after = directory_info(folder)
        if (not stat.S_ISDIR(after.st_mode)
                or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)):
            raise ValueError("The profile directory changed during capture admission")
        return False


def require_capture_idle(store, identity):
    """Require an authoritative transaction-bound observation; never wait on a pin."""
    if capture_activity(store.root, store.session_id, identity):
        raise ValueError("Snapshot capture is still active; retry after it finishes")


def require_legacy_scene(record):
    if is_bundle(record):
        raise ValueError(V4_SCENE_UNAVAILABLE)
