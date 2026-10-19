# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Windows backend of descriptor-anchored file I/O (``checkpoint_filesystem``).

POSIX anchors every effect to an open directory descriptor: ``openat`` with
``O_NOFOLLOW``, ``unlinkat``, ``renameat`` and ``linkat`` never resolve a path
string again. Windows has the same primitive one layer down: NtCreateFile opens
one name relative to an open directory handle, and rename, hard link and delete
are requests on an open handle (``windows_nt.py``). This module walks a path
from its volume root one component at a time with those calls, so:

* no step re-resolves a path string: each component is one name relative to the
  directory handle verified just before, as with ``openat``;
* every component and file is opened as itself (``FILE_OPEN_REPARSE_POINT``
  with ``OBJ_DONT_REPARSE``) and a name-surrogate reparse point (symbolic link,
  junction, mount point) is refused, never followed;
* every component must be its own long name (GetFinalPathNameByHandleW): an 8.3
  alias such as ``GIT~1`` for ``.git`` is refused, because lexical admission
  checked the name as given;
* ``within`` (the file tools' workspace) is checked against the opened handles'
  final paths, not the strings that were walked;
* delete, replace and no-overwrite publication act on the verified handle.

Data reparse points that are not name surrogates (OneDrive placeholders,
deduplicated or compressed files) are files: they are read through a reopened
handle that must be the same file, and are never written in place. Threat model
and limits: doc/angelo/checkpoint_policy.rst, "Windows file anchoring".

The API object is injected so host tests on any platform drive this logic with a
stand-in (``tests/python/angelo_agent/fake_nt_files.py``).
"""

import errno
import os
import stat
from contextlib import contextmanager
from pathlib import Path

# winnt.h / ntifs.h values, shared with the ctypes binding and the host tests' stand-in.
FILE_LIST_DIRECTORY = 0x1
FILE_TRAVERSE = 0x20
FILE_READ_ATTRIBUTES = 0x80
FILE_WRITE_ATTRIBUTES = 0x100
DELETE = 0x10000
SYNCHRONIZE = 0x100000
FILE_GENERIC_READ = 0x120089
FILE_GENERIC_WRITE = 0x120116
FILE_OPEN, FILE_CREATE, FILE_OPEN_IF, FILE_OVERWRITE, FILE_OVERWRITE_IF = 1, 2, 3, 4, 5   # never the last two
FILE_DIRECTORY_FILE = 0x1
FILE_NON_DIRECTORY_FILE = 0x40
FILE_ATTRIBUTE_READONLY = 0x1
FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
NAME_SURROGATE = 0x20000000   # IsReparseTagNameSurrogate: links, junctions, mount points
IO_REPARSE_TAG_SYMLINK = 0xA000000C

DIRECTORY_ACCESS = FILE_LIST_DIRECTORY | FILE_TRAVERSE | FILE_READ_ATTRIBUTES | SYNCHRONIZE
INFO_ACCESS = FILE_READ_ATTRIBUTES | SYNCHRONIZE
# Every handle is checked after it opens (attributes, then os.fstat on its descriptor), which needs
# FILE_READ_ATTRIBUTES; FILE_GENERIC_WRITE lacks it (POSIX's O_WRONLY still allows fstat).
_ACCESS = {os.O_RDONLY: FILE_GENERIC_READ, os.O_WRONLY: FILE_GENERIC_WRITE | FILE_READ_ATTRIBUTES,
           os.O_RDWR: FILE_GENERIC_READ | FILE_GENERIC_WRITE}
_INVALID = set('<>:"/\\|?*')
_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$", *("COM" + str(n) for n in range(10)),
             *("LPT" + str(n) for n in range(10)), "COM¹", "COM²", "COM³",
             "LPT¹", "LPT²", "LPT³"}
DIRECTORY_LINK = "Snapshot storage cannot follow directory links"
NOT_FOLLOWED = "Angelo does not follow links, junctions or mount points"

_default = None


def default_api():
    """The real binding on Windows (loaded once), otherwise None."""
    global _default
    if _default is None and os.name == "nt":
        from .windows_nt import NtFileApi
        _default = NtFileApi()
    return _default


class UnusableName(ValueError):
    """A name Windows can't store as given (too long, a device, a trailing dot ...)."""


def check_name(name):
    """One component Windows stores exactly as given: no streams, devices, separators or trailing dots."""
    if (not name or name in {".", ".."} or len(name) > 255 or name[-1] in ". "
            or any(character in _INVALID or ord(character) < 32 for character in name)
            or name.split(".", 1)[0].rstrip(" ").upper() in _RESERVED):
        raise UnusableName("Windows can't use this file or folder name: " + repr(name))
    return name


def _same_name(actual, requested):
    if actual.upper() != requested.upper() and actual.casefold() != requested.casefold():
        raise ValueError("Short (8.3) or aliased names aren't accepted; use the full name")


def _is_link(attributes, tag):
    return bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT and tag & NAME_SURROGATE)


def _open_component(api, parent, name, create):
    check_name(name)
    try:
        fd = api.open_at(parent, name, DIRECTORY_ACCESS, FILE_OPEN_IF if create else FILE_OPEN, FILE_DIRECTORY_FILE)
    except NotADirectoryError:
        raise ValueError(DIRECTORY_LINK) from None
    try:
        if _is_link(*api.attributes(fd)):
            raise ValueError(DIRECTORY_LINK)
        _same_name(api.long_name(fd), name)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _inside(final, root):
    final, root = final.casefold(), root.casefold().rstrip("\\")
    return final == root or final.startswith(root + "\\")


@contextmanager
def open_directory(path, create=False, api=None, within=None):
    """Yield the verified directory at ``path``, walking from its volume root.

    ``within``: a directory ``path`` must be inside; checked on the opened
    handles' final paths (GetFinalPathNameByHandleW), after the walk.
    """
    api = api or default_api()
    path = Path(api.long_path(os.fspath(Path(path).absolute())))
    if within is not None:
        within = Path(api.long_path(os.fspath(Path(within).absolute())))
        if not path.is_relative_to(within):
            raise ValueError("The directory is outside the selected workspace")
    fd, root_final = api.open_anchor(path.anchor), None
    try:
        if within is not None and len(within.parts) == 1:
            root_final = api.final_path(fd)
        for depth, part in enumerate(path.parts[1:], start=2):
            child = _open_component(api, fd, part, create)
            os.close(fd)
            fd = child
            if within is not None and depth == len(within.parts):
                root_final = api.final_path(fd)
        if within is not None and not _inside(api.final_path(fd), root_final):
            raise ValueError("The directory resolved outside the selected workspace")
        yield WindowsDirectory(fd, path, api)
    finally:
        os.close(fd)


@contextmanager
def open_child(parent, name):
    """Yield the verified existing child directory ``name`` of the open ``parent``.

    The same per-component checks as ``open_directory``'s walk (opened as
    itself, no link, junction or mount point, its own long name), relative to
    the parent's handle, so walking a tree costs one open per folder instead of
    one walk from the volume root. A child that is not a link, junction or mount
    point stays inside its verified parent, so ``within`` needs no new final-path
    check.
    """
    fd = _open_component(parent.api, parent.fd, name, False)
    try:
        yield WindowsDirectory(fd, parent.path / name, parent.api)
    finally:
        os.close(fd)


class LinkInfo:
    """``lstat`` of a name-surrogate reparse point: ``os.fstat`` reports its own type, not a link."""

    def __init__(self, status):
        for name in dir(status):
            if name.startswith("st_"):
                setattr(self, name, getattr(status, name))
        self.st_mode = stat.S_IFLNK | stat.S_IMODE(status.st_mode)


class WindowsDirectory:
    """An open, verified directory: the Windows counterpart of a POSIX directory descriptor."""

    def __init__(self, fd, path, api):
        self.fd, self.path, self.api = fd, path, api

    def _open(self, name, access, disposition, options=0, *, allow_link=False):
        fd = self.api.open_at(self.fd, check_name(name), access, disposition, options)
        try:
            attributes, tag = self.api.attributes(fd)
            if _is_link(attributes, tag) and not allow_link:
                raise OSError(errno.ELOOP, NOT_FOLLOWED, name)
            _same_name(self.api.long_name(fd), name)
            return fd, attributes, tag
        except BaseException:
            os.close(fd)
            raise

    def info(self):
        return os.fstat(self.fd)

    def final_path(self):
        return self.api.final_path(self.fd)

    def entry_info(self, name):
        """``lstat``: never follows; a link reports ``S_IFLNK``."""
        fd, attributes, tag = self._open(name, INFO_ACCESS, FILE_OPEN, allow_link=True)
        try:
            status = os.fstat(fd)
        finally:
            os.close(fd)
        return LinkInfo(status) if _is_link(attributes, tag) else status

    def open(self, name, flags, mode=0o600):
        """``openat(O_NOFOLLOW)`` returning a C runtime descriptor. ``mode`` applies through ``chmod``.

        ``O_TRUNC`` truncates only after the checks. NtCreateFile's overwrite dispositions would act first:
        on real NTFS, FILE_OVERWRITE with FILE_OPEN_REPARSE_POINT turned a symbolic link into an empty file
        before the link could be refused (it would also empty a provider placeholder).
        """
        access = _ACCESS[flags & 3]
        disposition = (FILE_CREATE if flags & os.O_EXCL else FILE_OPEN_IF) if flags & os.O_CREAT else FILE_OPEN
        fd, attributes, _ = self._open(name, access, disposition, FILE_NON_DIRECTORY_FILE)
        if attributes & FILE_ATTRIBUTE_REPARSE_POINT:
            fd = self._reopen_data_file(fd, access, name)
        if flags & os.O_TRUNC:
            try:
                os.ftruncate(fd, 0)
            except BaseException:
                os.close(fd)
                raise
        return fd

    def _reopen_data_file(self, fd, access, name):
        """A data reparse point (not a link) is read through a reopened handle that must be the same file."""
        try:
            if access != FILE_GENERIC_READ:
                raise OSError(errno.EPERM, "Angelo only reads files a storage provider manages", name)
            before = os.fstat(fd)
            reopened = self.api.reopen(fd, access)
        finally:
            os.close(fd)
        after = os.fstat(reopened)
        if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
            os.close(reopened)
            raise OSError(errno.ELOOP, NOT_FOLLOWED, name)
        return reopened

    def replace(self, source, destination):
        """``renameat``: moves the verified ``source`` over ``destination`` (a link there is replaced, not followed)."""
        check_name(destination)
        fd, _, _ = self._open(source, DELETE | FILE_READ_ATTRIBUTES | SYNCHRONIZE, FILE_OPEN, FILE_NON_DIRECTORY_FILE)
        try:
            self.api.rename_at(fd, self.fd, destination, replace=True)
        finally:
            os.close(fd)

    def rename_new(self, source, destination, expected):
        """Rename the entry ``source`` (a file or a folder, never a link) to ``destination``, never over an existing
        name (FileExistsError), and only when ``expected(info)`` accepts the very entry opened (ValueError)."""
        check_name(destination)
        fd, _, _ = self._open(source, DELETE | FILE_READ_ATTRIBUTES | SYNCHRONIZE, FILE_OPEN)
        try:
            if not expected(os.fstat(fd)):
                raise ValueError("The entry changed before it was renamed")
            self.api.rename_at(fd, self.fd, destination, replace=False)
        finally:
            os.close(fd)

    def link(self, source, destination):
        """``linkat``: atomic, never overwrites (FileExistsError); unsupported file systems raise ENOTSUP."""
        check_name(destination)
        fd, _, _ = self._open(source, FILE_READ_ATTRIBUTES | FILE_WRITE_ATTRIBUTES | SYNCHRONIZE, FILE_OPEN,
                              FILE_NON_DIRECTORY_FILE)
        try:
            self.api.link_at(fd, self.fd, destination)
        finally:
            os.close(fd)

    def unlink(self, name):
        """``unlinkat``: deletes the opened entry itself (a link, never its target); refuses directories."""
        fd = self.open_for_removal(name)
        try:
            self.remove_opened(fd)
        finally:
            os.close(fd)

    def make_directory(self, name):
        """``mkdirat``: a new child directory; an existing name of any kind raises FileExistsError."""
        os.close(self.api.open_at(self.fd, check_name(name), DIRECTORY_ACCESS, FILE_CREATE, FILE_DIRECTORY_FILE))

    def link_into(self, source, target, destination):
        """``linkat`` into another verified directory: never overwrites (FileExistsError)."""
        check_name(destination)
        fd, _, _ = self._open(source, FILE_READ_ATTRIBUTES | FILE_WRITE_ATTRIBUTES | SYNCHRONIZE, FILE_OPEN,
                              FILE_NON_DIRECTORY_FILE)
        try:
            self.api.link_at(fd, target.fd, destination)
        finally:
            os.close(fd)

    def remove_entry(self, name):
        """Delete the entry itself: a file, an empty directory, or a link, junction or mount point (never its target)."""
        fd, _, _ = self._open(name, DELETE | FILE_READ_ATTRIBUTES | SYNCHRONIZE, FILE_OPEN, allow_link=True)
        try:
            self.api.delete(fd)
        finally:
            os.close(fd)

    def open_for_removal(self, name):
        """A handle with DELETE access to the entry itself. Windows refuses it (sharing violation) while another
        program holds the file open without delete sharing; ``remove_opened`` then cannot fail that way."""
        fd, _, _ = self._open(name, DELETE | FILE_READ_ATTRIBUTES | SYNCHRONIZE, FILE_OPEN, FILE_NON_DIRECTORY_FILE,
                              allow_link=True)
        return fd

    def remove_opened(self, fd):
        self.api.delete(fd)

    def chmod(self, name, mode):
        """Windows keeps only the owner-write bit, as the read-only attribute."""
        fd, _, _ = self._open(name, FILE_READ_ATTRIBUTES | FILE_WRITE_ATTRIBUTES | SYNCHRONIZE, FILE_OPEN,
                              FILE_NON_DIRECTORY_FILE)
        try:
            self.api.set_readonly(fd, not mode & stat.S_IWUSR)
        finally:
            os.close(fd)

    def names(self):
        """Entry names, streamed from the open handle (``.`` and ``..`` excluded)."""
        for name in self.api.list_names(self.fd):
            if name not in {".", ".."}:
                yield name
