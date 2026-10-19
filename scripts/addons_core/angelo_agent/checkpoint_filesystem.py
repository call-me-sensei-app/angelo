# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Descriptor-anchored private I/O shared by file tools and recovery stores.

A "folder" yielded by ``_private_directory`` is a POSIX directory descriptor
(``int``), a verified Windows directory handle (``anchored_windows``) or, only
on a platform with neither, a ``Path`` checked before and after use. Callers use
the helpers below instead of ``os`` calls that need one particular kind.
"""

import errno
import json
import os
import stat
import sys
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from . import anchored_windows

MAX_METADATA_BYTES = 512 * 1024


def _windows_api():
    """The Windows handle API, or None off Windows (host tests substitute a stand-in)."""
    return anchored_windows.default_api()


def _posix_anchored():
    return (os.open in os.supports_dir_fd and os.unlink in os.supports_dir_fd and bool(getattr(os, "O_NOFOLLOW", 0))
            and (os.replace in os.supports_dir_fd or os.rename in os.supports_dir_fd))


def anchored_files_supported():
    """Whether untrusted workspace effects and snapshot deletion can be anchored race-free here."""
    return _windows_api() is not None or _posix_anchored()


@contextmanager
def _private_directory(path, create=False, within=None, lookup_only=False):
    """Anchor I/O to non-symlink directories, including every ancestor.

    Portable reads/writes validate ancestors before and after opening. Destructive
    pruning additionally requires descriptor-relative unlink support; it fails
    closed on platforms which cannot anchor deletion this way. ``within``: the
    workspace the directory must stay inside (Windows checks the opened handles'
    final paths; a POSIX descriptor cannot leave it once every component was
    opened without following a link). ``lookup_only``: the caller only opens or
    stats names in the folder (never lists or syncs it), so macOS may yield a
    search-only descriptor.
    """
    api = _windows_api()
    if api is not None:
        with anchored_windows.open_directory(path, create, api, within) as folder:
            yield folder
        return
    path = Path(path).absolute()
    if within is not None and not path.is_relative_to(Path(within).absolute()):
        raise ValueError("The directory is outside the selected workspace")
    descriptor = None
    anchored = os.open in os.supports_dir_fd and os.mkdir in os.supports_dir_fd
    try:
        if anchored and _NOFOLLOW_ANY:
            # One kernel lookup; a missing folder falls through to the creating walk.
            descriptor = _open_existing_directory(path, missing_ok=create,
                                                  access=_SEARCH if lookup_only and _SEARCH else os.O_RDONLY)
        if anchored and descriptor is None:
            descriptor = os.open(path.anchor, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            for part in path.parts[1:]:
                if create:
                    try:
                        os.mkdir(part, 0o700, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                child = _open_directory_at(descriptor, part)
                os.close(descriptor)
                descriptor = child
        elif not anchored:
            for parent in (path, *path.parents):
                if parent.exists() and (parent.is_symlink() or not parent.is_dir()):
                    raise ValueError("Snapshot storage cannot follow directory links")
            if create:
                path.mkdir(parents=True, exist_ok=True, mode=0o700)
            if path.resolve() != path or any(parent.is_symlink() for parent in (path, *path.parents)):
                raise ValueError("Snapshot storage cannot follow directory links")
            yield path  # Windows cannot open a directory with Python's os.open.
            if path.resolve() != path or any(parent.is_symlink() for parent in (path, *path.parents)):
                raise ValueError("Snapshot storage changed during access")
            return
        yield descriptor
    finally:
        if descriptor is not None:
            os.close(descriptor)


# macOS (11 and later; Angelo's minimum is far above) refuses a symbolic link in
# *any* component of one ``open`` with this flag (ELOOP): the guarantee the
# per-component walk in ``_private_directory`` gives with a stat, an open and an
# fstat per folder level. Each directory open costs about 130,000 instructions
# on macOS, so the walk cost about 1.2 million at an eight-level profile path and
# a request snapshot repeats it dozens of times; one open costs one. Other
# platforms keep the walk (Linux's equivalent, openat2, is not in Python).
_NOFOLLOW_ANY = getattr(os, "O_NOFOLLOW_ANY", 0) if sys.platform == "darwin" else 0
# A search-only directory descriptor (fstat and *at lookups, no listing or
# fsync) skips the read authorization: about 30,000 instructions on macOS.
_SEARCH = getattr(os, "O_SEARCH", 0) if _NOFOLLOW_ANY else 0


def _open_existing_directory(path, missing_ok=False, access=os.O_RDONLY):
    """Open an existing absolute directory path without following a link anywhere in it.

    The walk's outcomes for an existing path: a link or non-directory in any
    component raises its ValueError, and a missing component its
    FileNotFoundError (None with ``missing_ok``, so a creating caller falls back
    to the walk, which creates the missing folders). Only search permission is
    needed on ancestors, where the walk also opened (read) each one.
    """
    try:
        return os.open(path, access | os.O_DIRECTORY | _NOFOLLOW_ANY)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise
    except NotADirectoryError:
        raise ValueError("Snapshot storage cannot follow directory links") from None
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise ValueError("Snapshot storage cannot follow directory links") from None
        raise


def _open_directory_at(descriptor, name):
    """Open one non-symlink child directory relative to a held descriptor."""
    info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("Snapshot storage cannot follow directory links")
    child = os.open(name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
                    getattr(os, "O_NOFOLLOW", 0), dir_fd=descriptor)
    opened = os.fstat(child)
    if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
        os.close(child)
        raise ValueError("Snapshot storage changed while opening it")
    return child


@contextmanager
def _private_child(folder, name):
    """Anchor one child directory of a folder ``_private_directory`` already holds.

    Walking a tree (every session and snapshot folder of the profile) this way
    costs one relative open per level instead of re-validating every ancestor
    from the root for each folder, which made a profile-wide scan cost several
    system calls per path component per snapshot. Each backend applies to the
    child exactly the check its own walk applies to every component, relative
    to the held parent, which cannot be swapped underneath: a POSIX descriptor
    opens it without following a link; a Windows handle opens it as itself and
    refuses a link, junction or mount point and any 8.3 or aliased name
    (``anchored_windows.open_child``); the legacy path backend validates the
    child's whole chain as ``_private_directory`` does.
    """
    separators = {"/", os.sep, os.altsep} - {None}
    if type(name) is not str or name in {"", ".", ".."} or any(mark in name for mark in separators):
        raise ValueError("Snapshot storage names a single child folder")
    if isinstance(folder, anchored_windows.WindowsDirectory):
        with anchored_windows.open_child(folder, name) as child:
            yield child
        return
    if isinstance(folder, Path):
        with _private_directory(folder / name) as child:
            yield child
        return
    child = _open_directory_at(folder, name)
    try:
        yield child
    finally:
        os.close(child)


def entry_info(folder, name):
    """``lstat`` of one entry of an anchored folder; a link is reported, never followed."""
    if isinstance(folder, anchored_windows.WindowsDirectory):
        return folder.entry_info(name)
    if isinstance(folder, Path):
        return (folder / name).lstat()
    return os.stat(name, dir_fd=folder, follow_symlinks=False)


def directory_info(folder):
    """``fstat`` of the anchored folder itself."""
    if isinstance(folder, anchored_windows.WindowsDirectory):
        return folder.info()
    return folder.stat() if isinstance(folder, Path) else os.fstat(folder)


def iter_names(folder):
    """Entry names of an anchored folder, streamed (no ``.`` or ``..``)."""
    if isinstance(folder, anchored_windows.WindowsDirectory):
        yield from folder.names()
        return
    with os.scandir(folder) as entries:
        for entry in entries:
            yield entry.name


def list_names(folder):
    return list(iter_names(folder))


def sync_directory(folder):
    """Persist a POSIX directory's entries; NTFS journals its own (FlushFileBuffers needs write access there)."""
    if isinstance(folder, int) and os.name != "nt":
        os.fsync(folder)


def chmod_file(folder, name, mode):
    if isinstance(folder, anchored_windows.WindowsDirectory):
        folder.chmod(name, mode)
    elif isinstance(folder, Path):
        os.chmod(folder / name, mode)
    else:
        os.chmod(name, mode, dir_fd=folder)


def _file_info(folder, name):
    info = entry_info(folder, name)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("Snapshot storage contains an invalid file/link")
    return info


def _needs_windows_change_time():
    # CPython 3.13 fstat exposes NT ChangeTime; the supported 3.11 fallback
    # exposes creation time. Do not charge the current runtime an extra query.
    return os.name == "nt" and sys.version_info[:2] < (3, 13)


@dataclass(frozen=True, slots=True)
class _ChangeTimeInfo:
    info: object
    st_change_time: int

    def __getattr__(self, name):
        return getattr(self.info, name)


def descriptor_witness(descriptor):
    """Stat with an actual metadata-change witness on supported runtimes."""
    if not _needs_windows_change_time():
        return os.fstat(descriptor)
    api = _windows_api()
    if api is None:
        raise ValueError("Windows file change-time verification is unavailable")
    before = api.change_time(descriptor)
    info = os.fstat(descriptor)
    if api.change_time(descriptor) != before:
        raise ValueError("The file changed while observing its metadata")
    return _ChangeTimeInfo(info, before)


def file_witness(folder, name):
    """Observe a named regular file through the same by-handle witness."""
    named = _file_info(folder, name)
    if not _needs_windows_change_time():
        return named
    descriptor = _open_file(folder, name, os.O_RDONLY)
    try:
        current = descriptor_witness(descriptor)
        if not same_file(named, current) or current.st_nlink != 1:
            raise ValueError("The named file changed during witness acquisition")
        return current
    finally:
        os.close(descriptor)


def _open_file(folder, name, flags, mode=0o600):
    if isinstance(folder, anchored_windows.WindowsDirectory):
        return folder.open(name, flags, mode)
    if isinstance(folder, Path):
        if folder.resolve() != folder or (folder / name).is_symlink():
            raise ValueError("Snapshot storage cannot follow links")
        return os.open(folder / name, flags | getattr(os, "O_NOFOLLOW", 0), mode)
    return os.open(name, flags | getattr(os, "O_NOFOLLOW", 0), mode, dir_fd=folder)


def _replace_file(folder, source, destination):
    if isinstance(folder, anchored_windows.WindowsDirectory):
        folder.replace(source, destination)
    elif isinstance(folder, Path):
        if folder.resolve() != folder:
            raise ValueError("Snapshot storage changed during access")
        os.replace(folder / source, folder / destination)
    else:
        os.replace(source, destination, src_dir_fd=folder, dst_dir_fd=folder)


def _unlink_file(folder, name):
    if isinstance(folder, anchored_windows.WindowsDirectory):
        folder.unlink(name)
    elif isinstance(folder, Path):
        if folder.resolve() != folder:
            raise ValueError("Snapshot storage changed during access")
        os.unlink(folder / name)
    else:
        os.unlink(name, dir_fd=folder)


@contextmanager
def held_for_removal(folder, names):
    """Yield ``remove(name)`` for deleting ``names`` together after the caller's own preparation.

    On Windows a file another program holds open without delete sharing cannot be deleted, which POSIX
    ``unlinkat`` never refuses. Each name is therefore opened for deletion first: such a file refuses the
    whole removal (PermissionError) before the caller changes anything, and ``remove`` deletes through the
    held handle. Elsewhere ``remove`` unlinks by name.
    """
    if not isinstance(folder, anchored_windows.WindowsDirectory):
        yield lambda name: _unlink_file(folder, name)
        return
    held = {}
    try:
        for name in names:
            try:
                held[name] = folder.open_for_removal(name)
            except PermissionError as error:   # A sharing violation: open elsewhere without delete sharing.
                raise PermissionError(error.errno, "Another program has this file open; nothing was removed",
                                      name) from error
        yield lambda name: folder.remove_opened(held[name])
    finally:
        for descriptor in held.values():
            os.close(descriptor)


def _read_json(folder, name):
    info = _file_info(folder, name)
    if info.st_size > MAX_METADATA_BYTES:
        raise ValueError("Snapshot metadata exceeds its size limit")
    descriptor = _open_file(folder, name, os.O_RDONLY)
    with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
        opened = os.fstat(handle.fileno())
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError("Snapshot metadata changed while reading it")
        payload = handle.read(MAX_METADATA_BYTES + 1)
        if len(payload.encode("utf-8")) > MAX_METADATA_BYTES:
            raise ValueError("Snapshot metadata exceeds its size limit")
        return json.loads(payload)


def _read_metadata_bytes(folder, name):
    """The exact bytes of a metadata file, admitted like ``_read_json`` (regular, one link, bounded, same file)."""
    info = _file_info(folder, name)
    if info.st_size > MAX_METADATA_BYTES:
        raise ValueError("Snapshot metadata exceeds its size limit")
    descriptor = _open_file(folder, name, os.O_RDONLY)
    with os.fdopen(descriptor, "rb") as handle:
        opened = os.fstat(handle.fileno())
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError("Snapshot metadata changed while reading it")
        payload = handle.read(MAX_METADATA_BYTES + 1)
    if len(payload) > MAX_METADATA_BYTES:
        raise ValueError("Snapshot metadata exceeds its size limit")
    return payload


def _write_json(folder, name, value):
    payload = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(payload) > MAX_METADATA_BYTES:
        raise ValueError("Snapshot metadata exceeds its size limit")
    try:
        _file_info(folder, name)
    except FileNotFoundError:
        pass
    temporary = "." + str(uuid.uuid4()) + ".tmp"
    descriptor = _open_file(folder, temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_file(folder, temporary, name)
        sync_directory(folder)
    finally:
        try:
            _unlink_file(folder, temporary)
        except FileNotFoundError:
            pass


def require_anchored_files():
    """Untrusted workspace effects have no race-prone portable path fallback."""
    if not anchored_files_supported():
        raise ValueError("Safe descriptor-relative file tools are unavailable on this platform")


def require_safe_deletion():
    """Snapshot removal deletes only through an anchored folder (a descriptor or a Windows handle)."""
    if not anchored_files_supported():
        raise RuntimeError("Safe descriptor-relative snapshot deletion is unavailable on this platform")


def assert_directory_identity(path, folder):
    """Detect a changed lexical destination before publishing to its held fd."""
    if _SEARCH and isinstance(folder, int) and _windows_api() is None:
        # The same no-link lookup as _private_directory; only the identity is needed.
        current = _open_existing_directory(Path(path).absolute(), access=_SEARCH)
        try:
            second = os.fstat(current)
        finally:
            os.close(current)
        first = os.fstat(folder)
        if (first.st_dev, first.st_ino) != (second.st_dev, second.st_ino):
            raise ValueError("The workspace directory changed during access")
        return
    with _private_directory(path) as current:
        if isinstance(folder, Path) or isinstance(current, Path):
            raise ValueError("Safe descriptor-relative workspace access is unavailable")
        first, second = directory_info(folder), directory_info(current)
        if (first.st_dev, first.st_ino) != (second.st_dev, second.st_ino):
            raise ValueError("The workspace directory changed during access")


def same_file(first, second):
    return stat.S_ISREG(second.st_mode) and (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino)


def publish_new_file(folder, temporary, name):
    """Atomic no-overwrite publication (a new hard link), refusing unsupported filesystems."""
    if isinstance(folder, anchored_windows.WindowsDirectory):
        folder.link(temporary, name)
    elif isinstance(folder, Path):
        raise ValueError("Safe descriptor-relative publication is unavailable")
    else:
        os.link(temporary, name, src_dir_fd=folder, dst_dir_fd=folder, follow_symlinks=False)


def make_directory(folder, name):
    """Create one new child directory of an anchored folder; an existing name of any kind raises FileExistsError."""
    if isinstance(folder, anchored_windows.WindowsDirectory):
        folder.make_directory(name)
    elif isinstance(folder, Path):
        if folder.resolve() != folder:
            raise ValueError("Snapshot storage changed during access")
        (folder / name).mkdir(mode=0o700)
    else:
        os.mkdir(name, 0o700, dir_fd=folder)


def link_into(folder, name, target, new_name):
    """A no-overwrite hard link of a regular file into another anchored folder (FileExistsError if taken)."""
    if isinstance(folder, anchored_windows.WindowsDirectory):
        folder.link_into(name, target, new_name)
    elif isinstance(folder, Path) or isinstance(target, Path):
        raise ValueError("Safe descriptor-relative publication is unavailable")
    else:
        os.link(name, new_name, src_dir_fd=folder, dst_dir_fd=target, follow_symlinks=False)


def remove_entry(folder, name):
    """Remove one entry of an anchored folder itself: a file, a link (never its target) or an empty directory."""
    if isinstance(folder, anchored_windows.WindowsDirectory):
        folder.remove_entry(name)
    elif isinstance(folder, Path):
        if folder.resolve() != folder:
            raise ValueError("Snapshot storage changed during access")
        path = folder / name
        path.rmdir() if stat.S_ISDIR(path.lstat().st_mode) else path.unlink()
    elif stat.S_ISDIR(os.stat(name, dir_fd=folder, follow_symlinks=False).st_mode):
        os.rmdir(name, dir_fd=folder)
    else:
        os.unlink(name, dir_fd=folder)


def copy_stable_file(folder, name, destination, maximum=None):
    """Stream a fresh exact before-image, bounding bytes even if the source grows; return its opened stat."""
    info = _file_info(folder, name)
    if maximum is not None and info.st_size > maximum:
        raise ValueError("A file journal backup exceeds its bounded size limit")
    descriptor = _open_file(folder, name, os.O_RDONLY)
    with os.fdopen(descriptor, "rb") as source:
        opened = os.fstat(source.fileno())
        if not same_file(info, opened) or opened.st_nlink != 1:
            raise ValueError("The snapshot changed while opening it")
        copied = 0
        while chunk := source.read(min(1024 * 1024, info.st_size - copied + 1)):
            copied += len(chunk)
            if copied > info.st_size or (maximum is not None and copied > maximum):
                raise ValueError("The file grew while copying its bounded before-image")
            destination.write(chunk)
        finished = os.fstat(source.fileno())
        fields = ("st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink")
        if copied != info.st_size or any(getattr(opened, field) != getattr(finished, field) for field in fields):
            raise ValueError("The file changed while copying its before-image; no mutation was applied")
        return opened
