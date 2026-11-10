# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A rename between two held folders that never replaces what is at the new name (POSIX). No Blender dependency.

``os.rename`` replaces an empty folder (and any file) at the new name, and ``mkdir`` returns no handle, so a folder
made where others can write is known only by a lookup of its name after it exists: another process can rename its
own empty folder onto that name first, and the lookup then names that folder (stat before read, release-1010 audit,
re-check 2). ``create_directory`` makes the folder privately, takes its identity from a handle
on it, and moves it into place here, where a taken name refuses (``FileExistsError``) instead.

macOS: ``renameatx_np(..., RENAME_EXCL)`` (10.12 and later). Linux: ``renameat2(..., RENAME_NOREPLACE)`` (glibc 2.28
and later; ext4, XFS, Btrfs, tmpfs and most local file systems). Where neither is there, or the file system refuses
the flag (exFAT on macOS: ``ENOTSUP``; NFS and SMB shares: ``EINVAL``, ``ENOTSUP``), ``rename`` raises ``Unsupported``
and ``create_directory`` makes the folder in place instead (``_made_in_place``): the person's job is never refused for
the volume it writes to (release-1010 audit re-check 3, P1). Residual there, stated: between the ``mkdir`` and the
open of the name, a process of the same user with write access to the output folder can rename its own EMPTY folder
onto it; the job then publishes into that folder. A folder of another owner, or one holding anything, is refused
(the next free name is taken). Windows never comes here: a folder made through a Windows handle is known from that handle.
"""

import ctypes
import errno
import os
import secrets
import sys

RENAME_EXCL = 0x00000004      # macOS <stdio.h>.
RENAME_NOREPLACE = 1          # Linux <linux/fs.h>.
UNSUPPORTED = "no exclusive rename on this system or file system"   # Logs only: ``create_directory`` goes on.
_REFUSED = {errno.ENOSYS, errno.EINVAL, getattr(errno, "ENOTSUP", 45), getattr(errno, "EOPNOTSUPP", 95)}
_call = None   # (function, flag) once looked up; False where the system has none.


def _function():
    global _call
    if _call is None:
        _call = False
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            name, flag = (("renameatx_np", RENAME_EXCL) if sys.platform == "darwin"
                          else ("renameat2", RENAME_NOREPLACE) if sys.platform.startswith("linux") else (None, 0))
            function = getattr(libc, name, None) if name else None
        except (OSError, AttributeError):
            function = None
        if function is not None:
            function.restype = ctypes.c_int
            function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            _call = (function, flag)
    return _call


class Unsupported(OSError):
    """The system or the file system can't rename without possibly replacing (``ENOTSUP``)."""

    def __init__(self):
        super().__init__(errno.ENOTSUP, UNSUPPORTED)


def available():
    """Whether this system has the exclusive rename (it may still refuse it on a file system that lacks it)."""
    return bool(_function())


def rename(source_folder, source, target_folder, target):
    """Move ``source`` in the held folder ``source_folder`` (a descriptor) to ``target`` in ``target_folder``, never
    replacing anything there: ``FileExistsError`` when the name is taken, ``Unsupported`` where the system or the file
    system can't promise that."""
    found = _function()
    if not found:
        raise Unsupported()
    function, flag = found
    if function(source_folder, os.fsencode(source), target_folder, os.fsencode(target), flag) == 0:
        return
    error = ctypes.get_errno()
    if error == errno.EEXIST:
        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), target)
    if error in _REFUSED:
        raise Unsupported()
    raise OSError(error, os.strerror(error), target)


def create_directory(folder, name, private):
    """Create the new child directory ``name`` of an anchored folder (``checkpoint_filesystem``; ``FileExistsError``
    when the name is taken) and return the (device, file ID) of the directory this call made, from a handle on it:
    never a lookup of ``name`` afterwards, which could find a folder another process renamed onto it meanwhile (POSIX
    lets a rename replace an empty folder). On Windows that is the handle that created it. On POSIX the folder is made
    in the held, owner-only folder ``private`` (on the same file system), opened there and moved into place by
    ``rename``; where that rename is unsupported, it is made in place (``_made_in_place``, the module docstring)."""
    from . import anchored_windows
    if isinstance(folder, anchored_windows.WindowsDirectory):
        info = folder.make_directory(name)
        return info.st_dev, info.st_ino
    if not isinstance(folder, int) or not isinstance(private, int):
        raise ValueError("Safe descriptor-relative publication is unavailable")
    temporary = ".angelo-new-" + secrets.token_hex(8)
    os.mkdir(temporary, 0o700, dir_fd=private)
    try:
        info = _held_info(private, temporary)
        rename(private, temporary, folder, name)
    except Unsupported:
        _remove(private, temporary)
        return _made_in_place(folder, name, os.fstat(private).st_uid)
    except BaseException:
        _remove(private, temporary)
        raise
    return info.st_dev, info.st_ino


def _made_in_place(folder, name, owner):
    """``create_directory`` without an exclusive rename: ``mkdir`` at ``name``, then the identity from a handle on
    what is there now, taken only when it is an empty folder of ``owner`` (the owner this volume reports for the
    staging folder this process made on it: a share or a FAT volume can map every owner to one); otherwise
    ``FileExistsError`` (another's folder: the caller takes the next free name and never touches it). The residual
    is the module docstring's."""
    os.mkdir(name, 0o700, dir_fd=folder)
    descriptor = _open_folder(folder, name)
    try:
        info = os.fstat(descriptor)
        theirs = info.st_uid != owner or bool(os.listdir(descriptor))
    finally:
        os.close(descriptor)
    if theirs:
        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), name)
    return info.st_dev, info.st_ino


def _open_folder(folder, name):
    return os.open(name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0), dir_fd=folder)


def _held_info(folder, name):
    descriptor = _open_folder(folder, name)
    try:
        return os.fstat(descriptor)
    finally:
        os.close(descriptor)


def _remove(folder, name):
    try:
        os.rmdir(name, dir_fd=folder)
    except OSError:
        pass
