# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Handle-relative NTFS primitives for anchored file I/O (ctypes; import on Windows only).

The binding ``anchored_windows`` drives. Each call is the Windows counterpart of a
POSIX ``*at`` call, the recipe Rust's standard library adopted for CVE-2022-21658:

* ``open_at``: NtCreateFile with ``OBJECT_ATTRIBUTES.RootDirectory`` (one name
  relative to an open directory), ``FILE_OPEN_REPARSE_POINT`` (a link is opened
  as itself) and ``OBJ_DONT_REPARSE`` (Windows 10 1709 and later; an older
  system rejects the flag with STATUS_INVALID_PARAMETER and is retried without
  it, which changes nothing for one name opened with FILE_OPEN_REPARSE_POINT);
* ``rename_at``, ``link_at``, ``delete``: NtSetInformationFile on the verified
  handle (FileRenameInformationEx with POSIX semantics, FileLinkInformation,
  FileDispositionInformationEx; the legacy classes when a file system lacks
  the extended ones), so the object changed is the object checked;
* ``long_name``, ``final_path``: GetFinalPathNameByHandleW (normalized long names);
* ``attributes``: FileAttributeTagInfo, or FileBasicInfo where FAT/exFAT lack it;
* ``list_names``: GetFileInformationByHandleEx(FileFullDirectoryInfo) on the
  open directory, the counterpart of ``fdopendir``.

Handles travel as C runtime descriptors (``msvcrt.open_osfhandle``), so
``os.fstat``, ``os.fdopen``, ``os.fsync``, ``msvcrt.locking`` and ``os.close``
work on them unchanged. They are opened sharing read, write and delete (POSIX
behaviour: an open file can be renamed or unlinked) and are not inheritable.
ctypes releases the GIL during each call. The libraries and C runtime calls are
injectable so host tests on any OS drive this exact marshalling through fakes
(test_native_windows_nt.py); only a Windows run proves Windows' behaviour.
"""

import ctypes
import errno
import ntpath
import struct
from ctypes import wintypes

from .anchored_windows import (FILE_ATTRIBUTE_READONLY, FILE_ATTRIBUTE_REPARSE_POINT, FILE_LIST_DIRECTORY,
    FILE_READ_ATTRIBUTES, FILE_TRAVERSE, SYNCHRONIZE)

FILE_SHARE_ALL = 0x7
FILE_SYNCHRONOUS_IO_NONALERT = 0x20
FILE_OPEN_REPARSE_POINT = 0x200000
OBJ_CASE_INSENSITIVE = 0x40
OBJ_DONT_REPARSE = 0x1000
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
FILE_NAME_NORMALIZED, VOLUME_NAME_DOS, VOLUME_NAME_NONE = 0x0, 0x0, 0x4
# FILE_INFORMATION_CLASS (NtSetInformationFile) and FILE_INFO_BY_HANDLE_CLASS (GetFileInformationByHandleEx).
FILE_RENAME_INFORMATION, FILE_LINK_INFORMATION, FILE_DISPOSITION_INFORMATION = 10, 11, 13
FILE_DISPOSITION_INFORMATION_EX, FILE_RENAME_INFORMATION_EX = 64, 65
FILE_BASIC_INFO, FILE_ATTRIBUTE_TAG_INFO, FILE_FULL_DIRECTORY_INFO, FILE_FULL_DIRECTORY_RESTART_INFO = 0, 9, 14, 15
RENAME_REPLACE_IF_EXISTS, RENAME_POSIX_SEMANTICS, RENAME_IGNORE_READONLY = 0x1, 0x2, 0x40
DISPOSITION_DELETE, DISPOSITION_POSIX_SEMANTICS, DISPOSITION_IGNORE_READONLY = 0x1, 0x2, 0x10
SETTABLE_ATTRIBUTES = 0x1 | 0x2 | 0x4 | 0x20 | 0x100 | 0x1000 | 0x2000
FILE_ATTRIBUTE_NORMAL = 0x80
ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND, ERROR_NO_MORE_FILES = 2, 3, 18
ERROR_INVALID_FUNCTION, ERROR_NOT_SUPPORTED, ERROR_INVALID_PARAMETER = 1, 50, 87
_NO_TAG_INFO = {ERROR_INVALID_FUNCTION, ERROR_NOT_SUPPORTED, ERROR_INVALID_PARAMETER}   # FAT, exFAT
UNKNOWN_SURROGATE = 0x20000000   # A reparse point whose tag could not be read: treated as a link.

STATUS_INVALID_INFO_CLASS = 0xC0000003
STATUS_INVALID_PARAMETER = 0xC000000D
STATUS_INVALID_DEVICE_REQUEST = 0xC0000010
STATUS_FILE_IS_A_DIRECTORY = 0xC00000BA
STATUS_NOT_SUPPORTED = 0xC00000BB
STATUS_NOT_SAME_DEVICE = 0xC00000D4
STATUS_NOT_A_DIRECTORY = 0xC0000103
STATUS_REPARSE_POINT_ENCOUNTERED = 0xC000050B
# A file system without the extended class (FAT, some network redirectors) answers one of these.
_NO_EXTENDED_CLASS = {STATUS_INVALID_INFO_CLASS, STATUS_INVALID_PARAMETER, STATUS_NOT_SUPPORTED,
                      STATUS_INVALID_DEVICE_REQUEST}


class UNICODE_STRING(ctypes.Structure):
    _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT), ("Buffer", ctypes.c_void_p)]


class OBJECT_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Length", wintypes.ULONG), ("RootDirectory", wintypes.HANDLE),
                ("ObjectName", ctypes.POINTER(UNICODE_STRING)), ("Attributes", wintypes.ULONG),
                ("SecurityDescriptor", ctypes.c_void_p), ("SecurityQualityOfService", ctypes.c_void_p)]


class IO_STATUS_BLOCK(ctypes.Structure):
    _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t)]


class NAME_INFORMATION(ctypes.Structure):
    """FILE_RENAME_INFORMATION and FILE_LINK_INFORMATION share this header; FileName follows FileNameLength."""
    _fields_ = [("Flags", wintypes.ULONG), ("RootDirectory", wintypes.HANDLE), ("FileNameLength", wintypes.ULONG)]


_NAME_OFFSET = NAME_INFORMATION.FileNameLength.offset + ctypes.sizeof(wintypes.ULONG)
_NTSTATUS = ctypes.c_long
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
NTDLL_FUNCTIONS = (
    ("NtCreateFile", _NTSTATUS, [ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, ctypes.POINTER(OBJECT_ATTRIBUTES),
     ctypes.POINTER(IO_STATUS_BLOCK), ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG,
     ctypes.c_void_p, wintypes.ULONG]),
    ("NtSetInformationFile", _NTSTATUS, [wintypes.HANDLE, ctypes.POINTER(IO_STATUS_BLOCK), ctypes.c_void_p,
     wintypes.ULONG, ctypes.c_int]),
    ("RtlNtStatusToDosError", wintypes.ULONG, [_NTSTATUS]))
KERNEL32_FUNCTIONS = (
    ("CreateFileW", wintypes.HANDLE, [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
     wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]),
    ("ReOpenFile", wintypes.HANDLE, [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]),
    ("CloseHandle", wintypes.BOOL, [wintypes.HANDLE]),
    ("GetFinalPathNameByHandleW", wintypes.DWORD, [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]),
    ("GetLongPathNameW", wintypes.DWORD, [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]),
    ("GetFileInformationByHandleEx", wintypes.BOOL, [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]),
    ("SetFileInformationByHandle", wintypes.BOOL, [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]))


class NtFileApi:
    """By default ntdll, kernel32 and the C runtime's handle/descriptor calls (``msvcrt``).

    ``ntdll``, ``kernel32``, ``last_error``, ``format_error``, ``os_handle`` and
    ``descriptor`` exist for host tests.
    """

    def __init__(self, ntdll=None, kernel32=None, last_error=None, format_error=None, os_handle=None, descriptor=None):
        if ntdll is None:
            import msvcrt   # Windows only, like the libraries below.
            ntdll, kernel32 = ctypes.WinDLL("ntdll"), ctypes.WinDLL("kernel32", use_last_error=True)
            last_error, format_error = ctypes.get_last_error, ctypes.FormatError
            os_handle, descriptor = msvcrt.get_osfhandle, msvcrt.open_osfhandle
        for library, functions in ((ntdll, NTDLL_FUNCTIONS), (kernel32, KERNEL32_FUNCTIONS)):
            for name, restype, argtypes in functions:
                function = getattr(library, name)
                function.restype, function.argtypes = restype, argtypes
        self.ntdll, self.kernel32, self.last_error, self.format_error = ntdll, kernel32, last_error, format_error
        self.os_handle, self.open_descriptor = os_handle, descriptor
        self.dont_reparse = OBJ_DONT_REPARSE

    # -- errors -------------------------------------------------------------
    def _raise(self, status, name=None):
        status &= 0xFFFFFFFF
        special = {STATUS_REPARSE_POINT_ENCOUNTERED: (OSError, errno.ELOOP, "A link was not followed"),
                   STATUS_NOT_A_DIRECTORY: (NotADirectoryError, errno.ENOTDIR, "Not a directory"),
                   STATUS_FILE_IS_A_DIRECTORY: (IsADirectoryError, errno.EISDIR, "Is a directory"),
                   STATUS_NOT_SUPPORTED: (OSError, errno.ENOTSUP, "Not supported by this file system"),
                   STATUS_INVALID_DEVICE_REQUEST: (OSError, errno.ENOTSUP, "Not supported by this file system"),
                   STATUS_NOT_SAME_DEVICE: (OSError, errno.EXDEV, "Not on the same volume")}
        if status in special:
            kind, code, message = special[status]
            raise kind(code, message, name)
        code = self.ntdll.RtlNtStatusToDosError(status)
        raise OSError(0, self.format_error(code), name, code)  # On Windows winerror selects errno and the subclass.

    def _win32_error(self, name=None):
        code = self.last_error()
        raise OSError(0, self.format_error(code), name, code)

    def _handle(self, fd):
        return self.os_handle(fd)

    def _descriptor(self, handle):
        try:
            return self.open_descriptor(handle, 0)
        except OSError:
            self.kernel32.CloseHandle(handle)
            raise

    # -- opening ------------------------------------------------------------
    def open_anchor(self, anchor):
        """The volume root (``C:\\`` or ``\\\\server\\share\\``), opened as a directory, never through a link."""
        if anchor.startswith("\\\\?\\"):
            device = anchor
        elif anchor.startswith("\\\\"):
            device = "\\\\?\\UNC\\" + anchor[2:]
        else:
            device = "\\\\?\\" + anchor
        handle = self.kernel32.CreateFileW(device, FILE_LIST_DIRECTORY | FILE_TRAVERSE | FILE_READ_ATTRIBUTES |
                                           SYNCHRONIZE, FILE_SHARE_ALL, None, OPEN_EXISTING,
                                           FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT, None)
        if handle is None or handle == INVALID_HANDLE_VALUE:
            self._win32_error(anchor)
        return self._descriptor(handle)

    def _create(self, directory, name, access, disposition, options, attributes):
        encoded = name.encode("utf-16-le")
        buffer = ctypes.create_string_buffer(encoded, len(encoded) + 2)
        text = UNICODE_STRING(len(encoded), len(encoded) + 2, ctypes.addressof(buffer))
        objects = OBJECT_ATTRIBUTES(ctypes.sizeof(OBJECT_ATTRIBUTES), self._handle(directory), ctypes.pointer(text),
                                    OBJ_CASE_INSENSITIVE | attributes, None, None)
        handle, block = wintypes.HANDLE(), IO_STATUS_BLOCK()
        status = self.ntdll.NtCreateFile(ctypes.byref(handle), access, ctypes.byref(objects), ctypes.byref(block),
                                         None, FILE_ATTRIBUTE_NORMAL, FILE_SHARE_ALL, disposition,
                                         options | FILE_SYNCHRONOUS_IO_NONALERT | FILE_OPEN_REPARSE_POINT, None, 0)
        return status, handle.value

    def open_at(self, directory, name, access, disposition, options):
        """One name relative to an open directory; a reparse point is opened as itself."""
        status, handle = self._create(directory, name, access, disposition, options, self.dont_reparse)
        if (status & 0xFFFFFFFF) == STATUS_INVALID_PARAMETER and self.dont_reparse:
            retry, retry_handle = self._create(directory, name, access, disposition, options, 0)
            if (retry & 0xFFFFFFFF) != STATUS_INVALID_PARAMETER:
                self.dont_reparse = 0   # This Windows predates OBJ_DONT_REPARSE.
                status, handle = retry, retry_handle
        if status < 0:
            self._raise(status, name)
        return self._descriptor(handle)

    def reopen(self, fd, access):
        """The same file opened again without FILE_OPEN_REPARSE_POINT (for data reparse points)."""
        handle = self.kernel32.ReOpenFile(self._handle(fd), access, FILE_SHARE_ALL, 0)
        if handle is None or handle == INVALID_HANDLE_VALUE:
            self._win32_error()
        return self._descriptor(handle)

    # -- queries ------------------------------------------------------------
    def change_time(self, fd):
        """Raw signed ChangeTime ticks (100 ns since 1601), not file creation time."""
        basic = ctypes.create_string_buffer(40)   # FILE_BASIC_INFO: ChangeTime is the fourth LARGE_INTEGER.
        if not self.kernel32.GetFileInformationByHandleEx(self._handle(fd), FILE_BASIC_INFO, basic, 40):
            self._win32_error()
        return struct.unpack_from("<q", basic, 24)[0]

    def attributes(self, fd):
        """``(FileAttributes, ReparseTag)``.

        FAT and exFAT have no FileAttributeTagInfo (ERROR_INVALID_PARAMETER) and no
        reparse points; their attributes come from FileBasicInfo. Should such a file
        system ever report a reparse point, its unknown tag counts as a link.
        """
        value = (wintypes.DWORD * 2)()
        if self.kernel32.GetFileInformationByHandleEx(self._handle(fd), FILE_ATTRIBUTE_TAG_INFO, value,
                                                      ctypes.sizeof(value)):
            return value[0], value[1]
        if self.last_error() not in _NO_TAG_INFO:
            self._win32_error()
        basic = ctypes.create_string_buffer(40)   # FILE_BASIC_INFO: four times, then FileAttributes at 32.
        if not self.kernel32.GetFileInformationByHandleEx(self._handle(fd), FILE_BASIC_INFO, basic, 40):
            self._win32_error()
        attributes = struct.unpack_from("<I", basic, 32)[0]
        return attributes, (UNKNOWN_SURROGATE if attributes & FILE_ATTRIBUTE_REPARSE_POINT else 0)

    def _final(self, fd, flags):
        size = 1024
        while True:
            buffer = ctypes.create_unicode_buffer(size)
            length = self.kernel32.GetFinalPathNameByHandleW(self._handle(fd), buffer, size, flags)
            if not length:
                self._win32_error()
            if length < size:
                return buffer.value
            size = length + 1

    def final_path(self, fd):
        return self._final(fd, FILE_NAME_NORMALIZED | VOLUME_NAME_DOS)

    def long_name(self, fd):
        """The opened object's own long name (an 8.3 alias that was used to open it is expanded)."""
        return self._final(fd, FILE_NAME_NORMALIZED | VOLUME_NAME_NONE).rstrip("\\").rpartition("\\")[2]

    def long_path(self, path):
        """``path`` with 8.3 components expanded (links are not resolved); a missing tail is kept as given."""
        head, tail = path, []
        while True:
            buffer = ctypes.create_unicode_buffer(32768)
            if self.kernel32.GetLongPathNameW(head, buffer, 32768):
                return ntpath.join(buffer.value, *reversed(tail))
            parent, last = ntpath.split(head)
            if self.last_error() not in {ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND} or not last or parent == head:
                return path
            head = parent
            tail.append(last)

    def list_names(self, fd):
        """Entry names of an open directory, 64 KiB of records per call."""
        buffer, info_class = ctypes.create_string_buffer(65536), FILE_FULL_DIRECTORY_RESTART_INFO
        while True:
            if not self.kernel32.GetFileInformationByHandleEx(self._handle(fd), info_class, buffer, len(buffer)):
                if self.last_error() == ERROR_NO_MORE_FILES:
                    return
                self._win32_error()
            info_class, offset, records = FILE_FULL_DIRECTORY_INFO, 0, buffer.raw
            while True:   # FILE_FULL_DIR_INFO: NextEntryOffset at 0, FileNameLength at 60, FileName at 68.
                following, length = struct.unpack_from("<I", records, offset)[0], struct.unpack_from("<I", records, offset + 60)[0]
                yield records[offset + 68:offset + 68 + length].decode("utf-16-le")
                if not following:
                    break
                offset += following

    # -- changes ------------------------------------------------------------
    def _set_information(self, fd, info_class, data):
        block = IO_STATUS_BLOCK()
        return self.ntdll.NtSetInformationFile(self._handle(fd), ctypes.byref(block), data, len(data), info_class)

    def _name_request(self, fd, directory, name, info_class, flags):
        encoded = name.encode("utf-16-le")
        data = ctypes.create_string_buffer(max(_NAME_OFFSET + len(encoded), ctypes.sizeof(NAME_INFORMATION)))
        header = NAME_INFORMATION(flags, self._handle(directory), len(encoded))
        ctypes.memmove(data, ctypes.byref(header), _NAME_OFFSET)
        ctypes.memmove(ctypes.addressof(data) + _NAME_OFFSET, encoded, len(encoded))
        return self._set_information(fd, info_class, data)

    def rename_at(self, fd, directory, name, replace):
        """Rename the opened file to ``name`` in ``directory``; never over an existing name unless ``replace``."""
        flags = RENAME_POSIX_SEMANTICS | RENAME_IGNORE_READONLY | (RENAME_REPLACE_IF_EXISTS if replace else 0)
        status = self._name_request(fd, directory, name, FILE_RENAME_INFORMATION_EX, flags)
        if (status & 0xFFFFFFFF) in _NO_EXTENDED_CLASS:
            status = self._name_request(fd, directory, name, FILE_RENAME_INFORMATION, 1 if replace else 0)
        if status < 0:
            self._raise(status, name)

    def link_at(self, fd, directory, name):
        """A new hard link ``name`` in ``directory`` to the opened file; fails if ``name`` exists."""
        status = self._name_request(fd, directory, name, FILE_LINK_INFORMATION, 0)
        if status < 0:
            self._raise(status, name)

    def delete(self, fd):
        """Delete the opened entry now (POSIX semantics where the file system has them)."""
        flags = struct.pack("<I", DISPOSITION_DELETE | DISPOSITION_POSIX_SEMANTICS | DISPOSITION_IGNORE_READONLY)
        status = self._set_information(fd, FILE_DISPOSITION_INFORMATION_EX, ctypes.create_string_buffer(flags, 4))
        if (status & 0xFFFFFFFF) in _NO_EXTENDED_CLASS:
            status = self._set_information(fd, FILE_DISPOSITION_INFORMATION, ctypes.create_string_buffer(b"\x01", 1))
        if status < 0:
            self._raise(status)

    def set_readonly(self, fd, readonly):
        basic = ctypes.create_string_buffer(40)   # FILE_BASIC_INFO: four times, then FileAttributes at 32.
        if not self.kernel32.GetFileInformationByHandleEx(self._handle(fd), FILE_BASIC_INFO, basic, 40):
            self._win32_error()
        current = struct.unpack_from("<I", basic, 32)[0]
        wanted = (current | FILE_ATTRIBUTE_READONLY) if readonly else (current & ~FILE_ATTRIBUTE_READONLY)
        wanted = (wanted & SETTABLE_ATTRIBUTES) or FILE_ATTRIBUTE_NORMAL
        request = ctypes.create_string_buffer(40)   # Zero times leave them unchanged.
        struct.pack_into("<I", request, 32, wanted)
        if not self.kernel32.SetFileInformationByHandle(self._handle(fd), FILE_BASIC_INFO, request, 40):
            self._win32_error()
