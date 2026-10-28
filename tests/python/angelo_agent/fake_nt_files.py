# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A POSIX stand-in for ``windows_nt.NtFileApi``, so host tests drive ``anchored_windows`` on any OS.

Handles are POSIX descriptors. A name is opened relative to its directory
descriptor without following a link (a symbolic link is opened as itself, through
``O_PATH`` on Linux or ``O_SYMLINK`` on macOS), as NtCreateFile does with
FILE_OPEN_REPARSE_POINT. Access rights are kept per handle: querying a handle's
attributes needs FILE_READ_ATTRIBUTES, as GetFileInformationByHandleEx does.
``held`` plays files another program keeps open without delete sharing: opening
one for DELETE fails with a sharing violation, as NtCreateFile does.
Symbolic links play Windows' name-surrogate reparse
points, ``aliases`` plays 8.3 short names, ``data_reparse`` plays OneDrive-style
placeholders and ``redirect`` a placeholder whose reopen reaches another file.
By-handle rename, link and delete act on the path the handle was opened at.
This proves ``anchored_windows``' own logic, not Windows' semantics: the
Windows validation run does that (doc/angelo/checkpoint_policy.rst).
"""

import errno
import os
import stat
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import anchored_windows as aw

# The link itself (Linux O_PATH, macOS O_SYMLINK); Windows has neither, and runs the real backend instead.
_LINK_FLAGS = (os.O_PATH | os.O_NOFOLLOW) if hasattr(os, "O_PATH") else getattr(os, "O_SYMLINK", 0)
# Handles here are descriptors opened relative to a directory's (``dir_fd``), which Windows lacks. A test built on
# this stand-in skips there, whether or not its class says so: the real backend runs instead.
REAL_BACKEND_HOST = os.name == "nt"


class FakeNtApi:
    def __init__(self, aliases=None, data_reparse=(), redirect=None, final_paths=None):
        if REAL_BACKEND_HOST:
            raise unittest.SkipTest("The POSIX stand-in opens names relative to a directory descriptor; on Windows "
                                    "windows_ntfs_checks.py runs the real backend")
        self.aliases = dict(aliases or {})        # {(directory, alias): long name}
        self.data_reparse = {str(path) for path in data_reparse}
        self.redirect = {str(key): value for key, value in (redirect or {}).items()}
        self.final_paths = {str(key): value for key, value in (final_paths or {}).items()}
        self.paths, self.calls, self.access, self.held = {}, [], {}, set()

    def long_path(self, path):
        return path

    def open_anchor(self, anchor):
        fd = os.open(anchor, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        self.paths[fd] = Path(anchor)
        return fd

    def open_at(self, directory, name, access, disposition, options):
        self.calls.append(("open_at", name, access, disposition, options))
        parent = self.paths[directory]
        real = self.aliases.get((str(parent), name), name)
        if access & aw.DELETE and str(parent / real) in self.held:
            raise PermissionError(errno.EACCES, "The process cannot access the file because it is being used by "
                                  "another process", name)
        try:
            info = os.stat(real, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            if disposition in {aw.FILE_OPEN, aw.FILE_OVERWRITE}:
                raise
            if options & aw.FILE_DIRECTORY_FILE:
                os.mkdir(real, 0o700, dir_fd=directory)
            else:
                os.close(os.open(real, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory))
            info = os.stat(real, dir_fd=directory, follow_symlinks=False)
        else:
            if disposition == aw.FILE_CREATE:
                raise FileExistsError(errno.EEXIST, "File exists", name)
        if stat.S_ISLNK(info.st_mode):
            fd = os.open(real, _LINK_FLAGS, dir_fd=directory)
        elif stat.S_ISDIR(info.st_mode):
            if options & aw.FILE_NON_DIRECTORY_FILE:
                raise IsADirectoryError(errno.EISDIR, "Is a directory", name)
            fd = os.open(real, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_NOFOLLOW, dir_fd=directory)
        else:
            if options & aw.FILE_DIRECTORY_FILE:
                raise NotADirectoryError(errno.ENOTDIR, "Not a directory", name)
            writing, reading = access & 0x2, access & 0x1
            flags = os.O_RDWR if writing and reading else os.O_WRONLY if writing else os.O_RDONLY
            if disposition in {aw.FILE_OVERWRITE, aw.FILE_OVERWRITE_IF}:
                flags |= os.O_TRUNC
            fd = os.open(real, flags | os.O_NOFOLLOW, dir_fd=directory)
        self.paths[fd], self.access[fd] = parent / real, access
        return fd

    def attributes(self, fd):
        if not self.access.get(fd, aw.FILE_READ_ATTRIBUTES) & aw.FILE_READ_ATTRIBUTES:
            raise PermissionError(errno.EACCES, "Access is denied (no FILE_READ_ATTRIBUTES)")
        info, path = os.fstat(fd), str(self.paths[fd])
        if stat.S_ISLNK(info.st_mode):
            return aw.FILE_ATTRIBUTE_REPARSE_POINT, aw.IO_REPARSE_TAG_SYMLINK
        readonly = 0 if info.st_mode & stat.S_IWUSR else aw.FILE_ATTRIBUTE_READONLY
        if stat.S_ISDIR(info.st_mode):
            return aw.FILE_ATTRIBUTE_DIRECTORY | readonly, 0
        if path in self.data_reparse:
            return aw.FILE_ATTRIBUTE_REPARSE_POINT | readonly, 0x9000001A   # IO_REPARSE_TAG_CLOUD_1
        return 0x20 | readonly, 0

    def long_name(self, fd):
        return self.paths[fd].name

    def final_path(self, fd):
        path = str(self.paths[fd])
        return self.final_paths.get(path, "\\\\?\\" + path.replace("/", "\\"))

    def reopen(self, fd, access):
        path = self.redirect.get(str(self.paths[fd]), self.paths[fd])
        reopened = os.open(path, os.O_RDONLY)
        self.paths[reopened], self.access[reopened] = Path(path), access
        return reopened

    def list_names(self, fd):
        yield from (".", "..")
        yield from os.listdir(fd)

    def rename_at(self, fd, directory, name, replace):
        self.calls.append(("rename_at", name, replace))
        source, target = self.paths[fd], self.paths[directory] / name
        if not replace and os.path.lexists(target):
            raise FileExistsError(errno.EEXIST, "File exists", name)
        os.replace(source, target)
        self.paths[fd] = target

    def link_at(self, fd, directory, name):
        self.calls.append(("link_at", name))
        os.link(self.paths[fd], self.paths[directory] / name, follow_symlinks=False)

    def delete(self, fd):
        self.calls.append(("delete", self.paths[fd].name))
        path = self.paths[fd]
        os.rmdir(path) if stat.S_ISDIR(os.lstat(path).st_mode) else os.unlink(path)   # An empty directory, as NTFS

    def set_readonly(self, fd, readonly):
        mode = stat.S_IMODE(os.fstat(fd).st_mode)
        os.chmod(self.paths[fd], mode & ~0o222 if readonly else mode | stat.S_IWUSR)
