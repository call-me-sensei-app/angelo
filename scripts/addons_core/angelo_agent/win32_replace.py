# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A replace on Windows that succeeds while a reader holds the target open: POSIX rename semantics. No bpy.

``MoveFileExW`` (``os.replace``) never replaces a name any handle has open, a delete-sharing one included
(``ERROR_ACCESS_DENIED``), so a reader of a chat's file (the lead's mirror of a worker chat) refused its writer's
compaction. ``replace`` renames with ``SetFileInformationByHandle(FileRenameInfoEx)`` and
``FILE_RENAME_FLAG_POSIX_SEMANTICS | FILE_RENAME_FLAG_REPLACE_IF_EXISTS`` (Windows 10 1607 and later, NTFS): the
name is replaced while readers that share delete (``session_journal.open_shared``, every reader of these files) keep
reading the file they opened. A handle that does not share delete still refuses it (a sharing violation), which
``session_journal._replace`` retries as before.

Where the call is unsupported (an older Windows, a file system without POSIX semantics: FAT, exFAT, some network
shares) it answers ``ERROR_INVALID_PARAMETER``, ``ERROR_NOT_SUPPORTED`` or ``ERROR_INVALID_FUNCTION``, and the
replace falls back to ``os.replace`` (logged once per process). Nothing is cached: one volume's lack never takes
POSIX semantics from another.

Standard library only; ``ctypes`` is bound on first use, on Windows only.
"""

import os
import sys

POSIX, CLASSIC = "posix", "classic"
_UNSUPPORTED = frozenset({1, 50, 87})   # ERROR_INVALID_FUNCTION, ERROR_NOT_SUPPORTED, ERROR_INVALID_PARAMETER
_DELETE, _SHARE_ALL, _OPEN_EXISTING = 0x00010000, 0x7, 3
_FILE_RENAME_INFO_EX = 22               # FILE_INFO_BY_HANDLE_CLASS.FileRenameInfoEx
_REPLACE_IF_EXISTS, _POSIX_SEMANTICS = 0x1, 0x2
last = None                             # Which path the last replace took (``POSIX`` or ``CLASSIC``): evidence.
_logged = False
_api = None


class Unsupported(OSError):
    """The rename could not use POSIX semantics here: the caller replaces classically."""


def replace(source, target):
    """Replace ``target`` with ``source`` (module docstring). Raises ``PermissionError`` as ``os.replace`` does when a
    handle without delete sharing holds either file."""
    global last, _logged
    try:
        _posix(os.fspath(source), os.fspath(target))
        last = POSIX
        return
    except Unsupported as error:
        if not _logged:
            _logged = True
            print("Angelo: a chat file is replaced without POSIX semantics here (%s); a reader holding it open "
                  "delays its compaction" % error, file=sys.stderr)
    os.replace(source, target)
    last = CLASSIC


def _posix(source, target):
    """The POSIX-semantics rename of ``source`` over ``target``; ``Unsupported`` where it cannot be used."""
    import ctypes
    api = _bind()
    if api is None:
        raise Unsupported("no SetFileInformationByHandle")
    handle = api.create(source, _DELETE, _SHARE_ALL, None, _OPEN_EXISTING, 0, None)
    if handle is None or handle == api.invalid:
        _raise(api.last_error(), source, target, opening=True)
    try:
        info = rename_info(os.path.abspath(target))
        if not api.set_info(handle, _FILE_RENAME_INFO_EX, ctypes.byref(info), ctypes.sizeof(info)):
            _raise(api.last_error(), source, target)
    finally:
        api.close(handle)


def rename_info(name):
    """``FILE_RENAME_INFO`` for ``FileRenameInfoEx``: ``Flags`` (the union with ``ReplaceIfExists``), ``RootDirectory``
    (none: ``name`` is absolute), ``FileNameLength`` in bytes without the terminator, then the name. Both the length
    and the buffer come from the name's UTF-16 encoding, never its count of Python characters: a character outside
    the Basic Multilingual Plane (an emoji) is two code units (external audit 3 #2)."""
    import ctypes
    import struct
    encoded = name.encode("utf-16-le", "surrogatepass")   # A Windows name's lone surrogate is its own code unit.
    units = struct.unpack("<%dH" % (len(encoded) // 2), encoded)
    wide = ctypes.c_uint16 * (len(units) + 1)   # WCHAR FileName[], then its terminator.

    class RenameInfo(ctypes.Structure):
        _fields_ = [("flags", ctypes.c_uint32), ("root", ctypes.c_void_p), ("length", ctypes.c_uint32),
                    ("name", wide)]
    return RenameInfo(_REPLACE_IF_EXISTS | _POSIX_SEMANTICS, None, len(encoded), wide(*units))


def _raise(code, source, target, opening=False):
    if code in _UNSUPPORTED and not opening:
        raise Unsupported("Windows error %d" % code)
    if code in (5, 32):   # Access denied, sharing violation: a handle that does not share delete. Retried.
        raise PermissionError(13, "[WinError %d] the file is in use" % code, source, None, target)
    if code in (2, 3):
        raise FileNotFoundError(2, "[WinError %d] not found" % code, source, None, target)
    raise OSError(5, "[WinError %d] the replace failed" % code, source, None, target)


def _bind():
    """``CreateFileW``, ``SetFileInformationByHandle``, ``CloseHandle`` and ``GetLastError``; None off Windows."""
    global _api
    if _api is None and os.name == "nt":
        import ctypes
        from ctypes import wintypes
        from types import SimpleNamespace
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel32.CreateFileW
        create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                           wintypes.DWORD, wintypes.HANDLE)
        create.restype = wintypes.HANDLE
        set_info = getattr(kernel32, "SetFileInformationByHandle", None)
        if set_info is None:
            return None
        set_info.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        set_info.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        _api = SimpleNamespace(create=create, set_info=set_info, close=kernel32.CloseHandle,
                               last_error=ctypes.get_last_error, invalid=ctypes.c_void_p(-1).value)
    return _api
