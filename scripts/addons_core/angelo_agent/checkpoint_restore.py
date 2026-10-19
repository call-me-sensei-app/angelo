# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Anchored file restoration with confirmed partial-progress readbacks.

I/O callables are explicit dependencies so faults can be tested without replacing
process-global os functions. The store owns metadata and global effect order.
"""

import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from .checkpoint_filesystem import _file_info, _open_file, _private_directory, chmod_file, same_file, sync_directory
from .checkpoint_policy import journal_file_limit


@dataclass(frozen=True)
class _JournalOwner:
    session_id: str
    checkpoint_id: str
    version: tuple
    effect_sequence: int | None = None


@dataclass(frozen=True)
class _RestoreParticipants:
    """Private comparison data, not new fields/authority in journal DTOs."""
    selected: tuple
    backups: tuple
    effects: tuple
    recovery: object = None


class RestoredFiles(int):
    """The journal entries a file restore applied (an int, as before), and ``changed``: how many of them
    actually changed a file. Rewriting a file that already holds the restored bytes and mode, or a
    removal whose file is already absent, restores it without changing anything."""

    def __new__(cls, applied, changed):
        number = super().__new__(cls, applied)
        number.changed = changed
        return number

    def __getnewargs__(self):  # copy.deepcopy and pickle, as a saved chat state may hold it.
        return int(self), self.changed


def _holds(destination, name, source, backup_name, mode):
    """Whether the target already holds exactly the backup's bytes and mode (a bounded, anchored read).

    Only a count depends on it: any doubt (a missing file, a link, a read error) is a change.
    """
    try:
        return _same_bytes(destination, name, source, backup_name, mode)
    except (OSError, ValueError):
        return False


def _same_bytes(destination, name, source, backup_name, mode):
    target = _open_file(destination, name, os.O_RDONLY)
    try:
        info = os.fstat(target)
        backup = _open_file(source, backup_name, os.O_RDONLY)
        try:
            if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != stat.S_IMODE(mode)
                    or os.fstat(backup).st_size != info.st_size):
                return False
            while True:
                left, right = os.read(target, 1 << 20), os.read(backup, 1 << 20)
                if left != right:
                    return False
                if not left:
                    return True
        finally:
            os.close(backup)
    finally:
        os.close(target)


def apply_file_restore(store, plan, *, participants=None, copy_file, replace_file, unlink_file):
    store._validate_file_restore(plan, participants)
    # Validate every backup and target before applying any file changes.
    for target, backup, item in plan:
        store._target(item)
        if item["existed"]:
            with _private_directory(backup.parent) as folder:
                if _file_info(folder, backup.name).st_size > journal_file_limit(item, backup=True):
                    raise ValueError("A required file backup exceeds its bounded size limit")
    restored, unchanged = [], 0
    try:
        for (target, backup, item), effect_owner in zip(plan, participants.effects):
            store._target(item)
            if not item["existed"] and not target.parent.exists():
                continue  # The new file and its parent are already absent.
            with _private_directory(target.parent, create=item["existed"], within=Path(item["root"])) as destination:
                if not item["existed"]:
                    try:
                        before = _file_info(destination, target.name)
                    except FileNotFoundError:
                        continue
                    applied = False
                    try:
                        unlink_file(destination, target.name)
                        restored.append(str(target))
                        applied = True
                        store._record_restored_effect(item, owner=effect_owner, absent=True)
                        sync_directory(destination)
                    except (OSError, ValueError) as error:
                        if applied:
                            # Effect or sync failed after a confirmed unlink.
                            # Preserve that error; never replay our own now-stale
                            # effect binding or count this path a second time.
                            error.restore_progress_exact = True
                            raise
                        try:
                            after = _file_info(destination, target.name)
                        except FileNotFoundError:
                            restored.append(str(target))
                            store._record_restored_effect(item, owner=effect_owner, absent=True)
                            error.restore_progress_exact = True
                        except (OSError, ValueError):
                            error.restore_progress_exact = False
                        else:
                            error.restore_progress_exact = same_file(before, after)
                        raise
                    continue
                with _private_directory(backup.parent) as source:
                    unchanged += _holds(destination, target.name, source, backup.name, item["mode"])
                temporary = target.name + ".angelo-restore-" + str(uuid.uuid4())
                fd = _open_file(destination, temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, item["mode"])
                created = os.fstat(fd)
                try:
                    with os.fdopen(fd, "wb") as handle, _private_directory(backup.parent) as source:
                        copy_file(source, backup.name, handle, journal_file_limit(item, backup=True))
                        handle.flush()
                        os.fsync(handle.fileno())
                    chmod_file(destination, temporary, item["mode"])
                    if not same_file(created, _file_info(destination, temporary)):
                        raise ValueError("The staged restore file changed; no unknown output was published")
                    try:
                        replace_file(destination, temporary, target.name)
                    except (OSError, ValueError) as error:
                        try:
                            published = same_file(created, _file_info(destination, target.name))
                        except (OSError, ValueError):
                            published = False
                        if published:
                            restored.append(str(target))
                            store._record_restored_effect(item, owner=effect_owner)
                        error.restore_progress_exact = published
                        raise
                    restored.append(str(target))
                    store._record_restored_effect(item, owner=effect_owner)
                    sync_directory(destination)
                finally:
                    try:
                        if same_file(created, _file_info(destination, temporary)):
                            unlink_file(destination, temporary)
                    except FileNotFoundError:
                        pass
    except (OSError, ValueError) as error:
        error.restored_files = len(restored)
        error.restored_paths = restored
        error.partial_changes_possible = bool(restored) or not getattr(error, "restore_progress_exact", True)
        raise
    return RestoredFiles(len(plan), len(restored) - unchanged)
