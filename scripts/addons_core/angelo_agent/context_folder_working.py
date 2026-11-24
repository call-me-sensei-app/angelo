# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The disk side of writing into a chat's working folder (``context_folder_write``): ``WorkingFolder``.

Every write walks the folder from its root one opened folder at a time (``files.FileWorkspace``, as reads do: links
and junctions refused at every name, Angelo's state folders and the platform's private ones refused, another drive
refused), and refuses a folder that is, or lies inside, a reference folder: by each reference folder's identity as
attached and now, along the opened folder's whole chain up to the working folder, and by the path the system gives the
opened folder. That check comes before any folder is made.

**Atomic**: the text goes to a hidden temporary file in the target's own folder, which then takes the file's name: a
new file by an exclusive rename that never replaces one that appeared meanwhile (``exclusive_rename``: macOS
``RENAME_EXCL``, Linux ``RENAME_NOREPLACE``, Windows a rename without replace); where the file system has none, a
no-replace hard link; where it has neither (some shares), the write is refused, never done by a replacing rename
(break-it of 565f5df, S3). An update replaces the file by a rename, keeping its mode. The file must still be what it
was when the call was bound (``file_signature``: size, modification and change times, inode, device; S4) when the run
starts and again just before the rename.

**What is left behind**: nothing of a refused or failed write. Folders it made for the file are removed again when
they are still empty, and a temporary file another write left behind (Angelo stopped mid-write) is removed once
``STALE_TEMPORARY_SECONDS`` old. New files and folders get the person's own default permissions (their umask). No bpy.
"""

import errno
import os
import re
import stat
import time
import uuid
from pathlib import Path

from . import anchored_windows, change_clock, exclusive_rename, files
from .checkpoint_filesystem import (_open_file, _replace_file, _unlink_file, entry_info, entry_name, iter_names,
                                    make_directory, publish_new_file, same_file, sync_directory)
from .context_folder import NOT_GRANTED, ReadOnlyFolder
from .context_folder_grants import WORKING
from .refusal_codes import RefusalError

MISSING = ("missing",)
STALE_TEMPORARY_SECONDS = 600
_TEMPORARY = re.compile(r"^\.angelo-[0-9a-f]{32}\.tmp$")

# Its refusals, said for the model (never translated: model-facing text).
CHANGED = ("The file changed since this write was prepared; nothing was written. "   # i18n: model-facing
           "Read it again first.")
INSIDE_REFERENCE = ("That place is inside a reference folder, "   # i18n: model-facing
                    "which is read-only; nothing was written.")
NO_PUBLISH = ("This drive can't add a file without risking replacing one, "   # i18n: model-facing
              "so nothing was written there.")


class Refusal(RefusalError):
    """One of the working folder's own refusals, with its closed code: its sentence is said to the model as it is, and
    nothing was written (``partial_changes_possible`` False, so the request's other edits are not held)."""

    def __init__(self, message, *, code):
        super().__init__(message, code=code)
        self.partial_changes_possible = False


def file_signature(info):
    """What a file is now: (size, modification time, inode, device, change time); any edit, or another file at its
    name, changes it."""
    return info.st_size, info.st_mtime_ns, info.st_ino, info.st_dev, info.st_ctime_ns


def remove_empty(folder, name):
    """Remove the EMPTY child folder ``name`` of the anchored folder ``folder`` (a POSIX descriptor): one entry name,
    never a path through it, never a link followed; OSError when it holds anything. False where ``folder`` is no
    descriptor (Windows handles remove no folder here)."""
    if type(folder) is not int:
        return False
    os.rmdir(entry_name(name), dir_fd=folder)
    return True


def _within(path, root):
    path, root = os.path.normcase(path), os.path.normcase(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


class WorkingFolder(ReadOnlyFolder):
    """The read-only folder's anchored walk, which may also write one text file (``write_text``). ``references``: the
    real paths and identities of the chat's reference folders, never written into."""

    def __init__(self, root, excluded=(), references=(), grant=None):
        super().__init__(root, excluded=excluded, grant=grant)   # grant: "<chat>/working", judged at every walk.
        self.references = tuple(references)

    def _refuse_reference(self, folder, lexical):
        """The opened folder, and every folder holding it up to the working folder, is no reference folder."""
        opened = files.directory_info(folder)
        spelled = files._canonical_path(folder) or Path(os.path.realpath(lexical))
        identities = {(device, inode) for _real, device, inode in self.references}
        top = Path(os.path.realpath(self.root))
        chain = [(opened.st_dev, opened.st_ino)]
        for above in spelled.parents:
            if not _within(str(above), str(top)):
                break
            try:
                info = os.stat(above)
            except OSError:
                raise Refusal(CHANGED, code="STALE_CONTEXT") from None
            chain.append((info.st_dev, info.st_ino))
        if any(item in identities for item in chain) or any(_within(str(spelled), real)
                                                             for real, _d, _i in self.references):
            raise Refusal(INSIDE_REFERENCE, code="OWNERSHIP")

    def _open_root(self):
        """Only the chat's working-folder grant (``<chat>/working``) opens a folder to write in; then as the reader."""
        if not (isinstance(self.grant, str) and self.grant.endswith("/" + WORKING)):
            raise ValueError(NOT_GRANTED)
        return super()._open_root()

    def _same_folder(self, folder_path, folder):
        """The folder at ``folder_path`` is still the one held: walked again by this workspace's own walk, its root
        judged by the grant in C, never by the shared walk that asks ``may_write`` (the fourth read's MUST 2: that
        check refused every write after a script on Linux and Windows)."""
        with self._folder(folder_path) as again:
            first, second = files.directory_info(folder), files.directory_info(again)
        if (first.st_dev, first.st_ino) != (second.st_dev, second.st_ino):
            raise Refusal(CHANGED, code="STALE_CONTEXT")

    def signature(self, relative):
        """What the file at ``relative`` is now (``file_signature``), or ``MISSING``: the binding a write is approved
        for. Its folder need not exist yet."""
        def read():
            path = self.path(relative)
            try:
                with self._folder(path.parent) as folder:
                    info = self._admit(files.optional_info(folder, path.name))
            except FileNotFoundError:
                return MISSING
            if info is None:
                return MISSING
            change_clock.settle(info, change=False)   # An edit later in the same clock tick still shows.
            return file_signature(info)
        return self._scoped(read)

    def _existing(self, folder_path):
        existing = folder_path
        while existing != self.root and not os.path.lexists(existing):
            existing = existing.parent
        return existing

    def _make_parents(self, folder_path):
        """Each missing folder down to ``folder_path``, one anchored ``mkdir`` at a time, each in a folder checked
        first; returns those it made, outermost first."""
        made, missing, current = [], [], folder_path
        while current != self.root and not os.path.lexists(current):
            missing.append(current)
            current = current.parent
        for target in reversed(missing):
            with self._folder(target.parent) as parent:
                self._refuse_reference(parent, target.parent)
                try:
                    make_directory(parent, target.name, 0o777)   # The person's umask applies.
                except FileExistsError:
                    continue
                made.append(target)
        return made

    def _remove_made(self, made):
        """The folders a refused or failed write made, innermost first, while still empty (best effort)."""
        for target in reversed(made):
            try:
                with self._folder(target.parent) as parent:
                    if not remove_empty(parent, target.name):
                        return   # No anchored removal of a folder there (Windows): an empty folder stays.
            except (OSError, ValueError):
                return

    @staticmethod
    def _sweep(folder):
        """Temporary files of writes that never finished (Angelo stopped), once ``STALE_TEMPORARY_SECONDS`` old. Only
        a hidden regular file with one link named ``.angelo-<32 hex>.tmp``: the name carries 128 random bits Angelo
        chose, so no file of the person's or another program's has it by chance, and a write under way (its own or
        another chat's) is younger than the bound."""
        try:
            for name in list(iter_names(folder)):
                if _TEMPORARY.match(name):
                    info = entry_info(folder, name)
                    if (stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                            and time.time() - info.st_mtime > STALE_TEMPORARY_SECONDS):
                        _unlink_file(folder, name)
        except (OSError, ValueError):
            pass

    @staticmethod
    def _publish(folder, temporary, name, created):
        """The temporary file takes ``name``, never over a file there (FileExistsError)."""
        if isinstance(folder, anchored_windows.WindowsDirectory):
            folder.rename_new(temporary, name, lambda info: same_file(created, info))
            return
        try:
            exclusive_rename.rename(folder, temporary, folder, name)
        except exclusive_rename.Unsupported:
            try:
                publish_new_file(folder, temporary, name)   # A no-replace hard link, where the volume has links.
            except FileExistsError:
                raise
            except OSError as error:
                if error.errno == errno.EEXIST:
                    raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST)) from None
                raise Refusal(NO_PUBLISH, code="UNSUPPORTED") from None

    def write_note(self, relative, content, before):
        """Writes ``content`` at ``relative`` while the file is still ``before``; returns its new ``file_signature``,
        or None when it was written but could not be read back (then it is never taken as Angelo's own)."""
        data = content.encode("utf-8")

        def write():
            path = self.path(relative)
            existing = self._existing(path.parent)   # Before any folder is made: none of it is a reference folder.
            with self._folder(existing) as folder:
                self._refuse_reference(folder, existing)
            made, published = self._make_parents(path.parent), False
            try:
                with self._folder(path.parent) as folder:
                    self._same_folder(path.parent, folder)
                    self._refuse_reference(folder, path.parent)
                    self._sweep(folder)
                    info = self._admit(files.optional_info(folder, path.name))
                    if (file_signature(info) if info is not None else MISSING) != tuple(before):
                        raise Refusal(CHANGED, code="STALE_CONTEXT")
                    temporary = ".angelo-" + uuid.uuid4().hex + ".tmp"   # Hidden, in the same folder.
                    descriptor = _open_file(folder, temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                                            0o666 if info is None else 0o600)   # A new file: the person's umask.
                    created = os.fstat(descriptor)
                    try:
                        with os.fdopen(descriptor, "wb") as handle:
                            if info is not None and hasattr(os, "fchmod"):   # An update keeps the file's mode.
                                os.fchmod(handle.fileno(), stat.S_IMODE(info.st_mode) & 0o777)
                            handle.write(data)
                            handle.flush()
                            os.fsync(handle.fileno())
                        self._same_folder(path.parent, folder)
                        current = files.optional_info(folder, path.name)
                        if (file_signature(current) if current is not None else MISSING) != tuple(before):
                            raise Refusal(CHANGED, code="STALE_CONTEXT")
                        try:
                            if info is None:
                                self._publish(folder, temporary, path.name, created)
                            else:
                                _replace_file(folder, temporary, path.name)
                        except FileExistsError:   # A file appeared at its name meanwhile: never replaced.
                            raise Refusal(CHANGED, code="STALE_CONTEXT") from None
                        published = True
                    finally:
                        try:   # Gone once published; any left is swept later (_sweep).
                            if same_file(created, entry_info(folder, temporary)):
                                _unlink_file(folder, temporary)
                        except OSError:
                            pass
                    try:   # Written: what follows only lets Angelo know it as its own.
                        sync_directory(folder)
                        after = entry_info(folder, path.name)
                        change_clock.settle(after, change=False)
                        return file_signature(after)
                    except OSError:
                        return None
            except BaseException:
                if not published:
                    self._remove_made(made)
                raise
        return self._scoped(write)
