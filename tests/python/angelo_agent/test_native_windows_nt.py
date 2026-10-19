# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The ctypes binding of handle-relative NTFS calls, on any host through fake ntdll/kernel32.

The fakes receive exactly the ctypes arguments ``windows_nt.NtFileApi`` passes
(OBJECT_ATTRIBUTES with a counted UTF-16 name, rename/link buffers, directory
records), so this checks the marshalling: relative opens never following a
reparse point, the OBJ_DONT_REPARSE fallback, by-handle rename/link/delete
layouts with their legacy fallbacks, directory parsing and error mapping. Only a
Windows run proves Windows' behaviour (doc/angelo/checkpoint_policy.rst).
"""

import ctypes
import errno
import struct
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
from angelo_agent import anchored_windows as aw
from angelo_agent import windows_nt as nt
from fake_c_libraries import CFunction

DIRECTORY_FD, DIRECTORY_HANDLE = 7, 0x70


def _struct(reference):
    return reference._obj


class FakeLibraries:
    """ntdll and kernel32 functions recording their ctypes arguments."""

    def __init__(self):
        self.calls, self.create_status, self.set_status, self.next_handle = [], [], [], 0x100
        self.directory_pages, self.final, self.long_paths, self.error = [], "\\\\?\\C:\\work\\notes.md", {}, 0
        self.tag_info, self.basic_attributes = True, 0x20   # False: a FAT volume (no FileAttributeTagInfo)
        for name in ("NtCreateFile", "NtSetInformationFile", "RtlNtStatusToDosError", "CreateFileW", "ReOpenFile",
                     "CloseHandle", "GetFinalPathNameByHandleW", "GetLongPathNameW", "GetFileInformationByHandleEx",
                     "SetFileInformationByHandle"):
            setattr(self, name, CFunction(getattr(self, "_" + name)))

    def _NtCreateFile(self, handle, access, objects, block, size, attributes, share, disposition, options, ea, length):
        objects = _struct(objects)
        text = objects.ObjectName.contents
        name = ctypes.string_at(text.Buffer, text.Length).decode("utf-16-le")
        self.calls.append(("NtCreateFile", objects.RootDirectory, name, objects.Attributes, access, share,
                           disposition, options))
        status = self.create_status.pop(0) if self.create_status else 0
        if status == 0:
            self.next_handle += 1
            _struct(handle).value = self.next_handle
        return status

    def _NtSetInformationFile(self, handle, block, data, length, info_class):
        self.calls.append(("NtSetInformationFile", handle, bytes(data)[:length], info_class))
        return self.set_status.pop(0) if self.set_status else 0

    def _RtlNtStatusToDosError(self, status):
        return 2

    def _CreateFileW(self, path, access, share, security, disposition, flags, template):
        self.calls.append(("CreateFileW", path, access, share, disposition, flags))
        return 0x55

    def _ReOpenFile(self, handle, access, share, flags):
        self.calls.append(("ReOpenFile", handle, access, share, flags))
        return 0x56

    def _CloseHandle(self, handle):
        self.calls.append(("CloseHandle", handle))
        return 1

    def _GetFinalPathNameByHandleW(self, handle, buffer, size, flags):
        value = self.final if flags == nt.VOLUME_NAME_DOS else self.final.split(":", 1)[1]
        if len(value) >= size:
            return len(value) + 1
        buffer.value = value
        return len(value)

    def _GetLongPathNameW(self, path, buffer, size):
        if path not in self.long_paths:
            self.error = nt.ERROR_FILE_NOT_FOUND
            return 0
        buffer.value = self.long_paths[path]
        return len(buffer.value)

    def _GetFileInformationByHandleEx(self, handle, info_class, buffer, size):
        self.calls.append(("GetFileInformationByHandleEx", handle, info_class))
        if info_class == nt.FILE_ATTRIBUTE_TAG_INFO:
            if not self.tag_info:
                self.error = nt.ERROR_INVALID_PARAMETER
                return 0
            buffer[0], buffer[1] = 0x400, 0xA000000C
            return 1
        if info_class == nt.FILE_BASIC_INFO:
            struct.pack_into("<I", buffer, 32, self.basic_attributes)
            return 1
        if not self.directory_pages:
            self.error = nt.ERROR_NO_MORE_FILES
            return 0
        page = self.directory_pages.pop(0)
        ctypes.memmove(buffer, page, len(page))
        return 1

    def _SetFileInformationByHandle(self, handle, info_class, buffer, size):
        self.calls.append(("SetFileInformationByHandle", handle, info_class, bytes(buffer)[:size]))
        return 1


def directory_page(*names):
    """FILE_FULL_DIR_INFO records: NextEntryOffset at 0, FileNameLength at 60, FileName at 68."""
    records = []
    for index, name in enumerate(names):
        encoded = name.encode("utf-16-le")
        size = (68 + len(encoded) + 7) // 8 * 8
        record = bytearray(size)
        struct.pack_into("<I", record, 0, size if index < len(names) - 1 else 0)
        struct.pack_into("<I", record, 60, len(encoded))
        record[68:68 + len(encoded)] = encoded
        records.append(bytes(record))
    return b"".join(records)


class NtBindingTests(unittest.TestCase):
    def setUp(self):
        self.libraries = FakeLibraries()
        self.descriptors = []
        self.api = nt.NtFileApi(ntdll=self.libraries, kernel32=self.libraries, last_error=lambda: self.libraries.error,
                                format_error=lambda code: "Windows error %d" % code,
                                os_handle=lambda fd: DIRECTORY_HANDLE if fd == DIRECTORY_FD else fd * 16,
                                descriptor=self.descriptor)

    def descriptor(self, handle, flags):
        self.descriptors.append((handle, flags))
        return handle // 16

    def test_signatures_are_declared_on_both_libraries(self):
        for name, restype, argtypes in nt.NTDLL_FUNCTIONS + nt.KERNEL32_FUNCTIONS:
            function = getattr(self.libraries, name)
            self.assertEqual((function.restype, function.argtypes), (restype, argtypes))

    def test_relative_open_names_one_component_and_never_follows(self):
        fd = self.api.open_at(DIRECTORY_FD, "notes \u00e9\U0001f600.md", 0x120089,
                              1, 0x40)
        _, root, name, attributes, access, share, disposition, options = self.libraries.calls[0]
        self.assertEqual((root, name, disposition), (DIRECTORY_HANDLE, "notes \u00e9\U0001f600.md", 1))
        self.assertEqual(attributes, nt.OBJ_CASE_INSENSITIVE | nt.OBJ_DONT_REPARSE)
        self.assertEqual(share, nt.FILE_SHARE_ALL)
        self.assertEqual(options, 0x40 | nt.FILE_OPEN_REPARSE_POINT | nt.FILE_SYNCHRONOUS_IO_NONALERT)
        self.assertEqual((fd, self.descriptors), (0x101 // 16, [(0x101, 0)]))

    def test_windows_without_obj_dont_reparse_is_retried_once_then_remembered(self):
        self.libraries.create_status = [nt.STATUS_INVALID_PARAMETER - 2**32, 0]
        self.api.open_at(DIRECTORY_FD, "a", 1, 1, 0)
        self.api.open_at(DIRECTORY_FD, "b", 1, 1, 0)
        attributes = [call[3] for call in self.libraries.calls if call[0] == "NtCreateFile"]
        self.assertEqual(attributes, [nt.OBJ_CASE_INSENSITIVE | nt.OBJ_DONT_REPARSE, nt.OBJ_CASE_INSENSITIVE,
                                      nt.OBJ_CASE_INSENSITIVE])

    def test_a_genuinely_invalid_request_keeps_obj_dont_reparse(self):
        self.libraries.create_status = [nt.STATUS_INVALID_PARAMETER - 2**32] * 2
        with self.assertRaises(OSError):
            self.api.open_at(DIRECTORY_FD, "a", 1, 1, 0)
        self.assertEqual(self.api.dont_reparse, nt.OBJ_DONT_REPARSE)

    def test_status_mapping(self):
        for status, kind, code in ((nt.STATUS_REPARSE_POINT_ENCOUNTERED, OSError, errno.ELOOP),
                                   (nt.STATUS_NOT_A_DIRECTORY, NotADirectoryError, errno.ENOTDIR),
                                   (nt.STATUS_FILE_IS_A_DIRECTORY, IsADirectoryError, errno.EISDIR),
                                   (nt.STATUS_NOT_SUPPORTED, OSError, errno.ENOTSUP),
                                   (nt.STATUS_NOT_SAME_DEVICE, OSError, errno.EXDEV)):
            self.libraries.create_status = [status - 2**32]
            with self.subTest(status=hex(status)), self.assertRaises(kind) as raised:
                self.api.open_at(DIRECTORY_FD, "name", 1, 1, 0)
            self.assertEqual(raised.exception.errno, code)
        self.libraries.create_status = [0xC0000034 - 2**32]   # STATUS_OBJECT_NAME_NOT_FOUND -> winerror 2
        with self.assertRaisesRegex(OSError, "Windows error 2"):
            self.api.open_at(DIRECTORY_FD, "missing", 1, 1, 0)

    def test_rename_and_link_carry_the_target_directory_handle_and_name(self):
        self.api.rename_at(3, DIRECTORY_FD, "out.txt", replace=True)
        self.api.link_at(3, DIRECTORY_FD, "new.txt")
        rename, link = [call for call in self.libraries.calls if call[0] == "NtSetInformationFile"]
        offset = nt._NAME_OFFSET
        for call, info_class, flags, name in ((rename, nt.FILE_RENAME_INFORMATION_EX, 0x43, "out.txt"),
                                              (link, nt.FILE_LINK_INFORMATION, 0, "new.txt")):
            _, handle, data, used_class = call
            self.assertEqual((handle, used_class), (3 * 16, info_class))
            self.assertEqual(struct.unpack_from("<I", data, 0)[0], flags)
            self.assertEqual(struct.unpack_from("<Q", data, nt.NAME_INFORMATION.RootDirectory.offset)[0], DIRECTORY_HANDLE)
            self.assertEqual(struct.unpack_from("<I", data, nt.NAME_INFORMATION.FileNameLength.offset)[0], len(name) * 2)
            self.assertEqual(data[offset:offset + len(name) * 2].decode("utf-16-le"), name)

    def test_file_systems_without_extended_classes_use_the_legacy_ones(self):
        self.libraries.set_status = [nt.STATUS_INVALID_INFO_CLASS - 2**32, 0, nt.STATUS_NOT_SUPPORTED - 2**32, 0]
        self.api.rename_at(3, DIRECTORY_FD, "out.txt", replace=False)
        self.api.delete(3)
        calls = [(call[3], call[2][:4]) for call in self.libraries.calls if call[0] == "NtSetInformationFile"]
        self.assertEqual([info_class for info_class, _ in calls],
                         [nt.FILE_RENAME_INFORMATION_EX, nt.FILE_RENAME_INFORMATION,
                          nt.FILE_DISPOSITION_INFORMATION_EX, nt.FILE_DISPOSITION_INFORMATION])
        self.assertEqual(calls[1][1][0], 0)                       # ReplaceIfExists FALSE
        self.assertEqual(struct.unpack("<I", calls[2][1])[0], 0x13)  # delete | POSIX | ignore read-only
        self.assertEqual(calls[3][1][:1], b"\x01")                # DeleteFile TRUE

    def test_directory_listing_parses_every_record_across_pages(self):
        self.libraries.directory_pages = [directory_page(".", "..", "a.txt"), directory_page("\u00e9t\u00e9 \U0001f600")]
        self.assertEqual(list(self.api.list_names(DIRECTORY_FD)), [".", "..", "a.txt", "\u00e9t\u00e9 \U0001f600"])
        classes = [call[2] for call in self.libraries.calls if call[0] == "GetFileInformationByHandleEx"]
        self.assertEqual(classes, [nt.FILE_FULL_DIRECTORY_RESTART_INFO, nt.FILE_FULL_DIRECTORY_INFO,
                                   nt.FILE_FULL_DIRECTORY_INFO])

    def test_names_paths_and_anchors(self):
        self.assertEqual(self.api.final_path(3), "\\\\?\\C:\\work\\notes.md")
        self.assertEqual(self.api.long_name(3), "notes.md")
        self.libraries.final = "\\\\?\\C:\\" + "d" * 1500   # Longer than the first buffer.
        self.assertEqual(self.api.long_name(3), "d" * 1500)
        self.assertEqual(self.api.attributes(3), (0x400, 0xA000000C))
        self.libraries.long_paths = {"C:\\Users\\RUNNER~1\\x": "C:\\Users\\runneradmin\\x"}
        self.assertEqual(self.api.long_path("C:\\Users\\RUNNER~1\\x\\new\\file"),
                         "C:\\Users\\runneradmin\\x\\new\\file")
        self.api.open_anchor("C:\\")
        self.api.open_anchor("\\\\server\\share\\")
        devices = [call[1] for call in self.libraries.calls if call[0] == "CreateFileW"]
        self.assertEqual(devices, ["\\\\?\\C:\\", "\\\\?\\UNC\\server\\share\\"])
        flags = [call[5] for call in self.libraries.calls if call[0] == "CreateFileW"]
        self.assertTrue(all(value == nt.FILE_FLAG_BACKUP_SEMANTICS | nt.FILE_FLAG_OPEN_REPARSE_POINT for value in flags))

    def test_attributes_on_a_volume_without_tag_information(self):
        # FAT and exFAT answer FileAttributeTagInfo with ERROR_INVALID_PARAMETER (seen on a FAT32 volume).
        self.libraries.tag_info = False
        self.assertEqual(self.api.attributes(3), (0x20, 0))
        self.libraries.basic_attributes = 0x400   # A reparse point whose tag cannot be read is a link.
        self.assertTrue(aw._is_link(*self.api.attributes(3)))
        classes = [call[2] for call in self.libraries.calls if call[0] == "GetFileInformationByHandleEx"]
        self.assertEqual(classes, [nt.FILE_ATTRIBUTE_TAG_INFO, nt.FILE_BASIC_INFO] * 2)
        self.libraries.tag_info, self.libraries.error = True, 0

    def test_read_only_attribute_round_trip_keeps_other_attributes(self):
        self.api.set_readonly(3, True)
        _, _, info_class, data = [call for call in self.libraries.calls if call[0] == "SetFileInformationByHandle"][0]
        self.assertEqual((info_class, struct.unpack_from("<I", data, 32)[0], data[:32]), (nt.FILE_BASIC_INFO, 0x21, bytes(32)))



class WindowsValidationScriptTests(unittest.TestCase):
    """The Windows-only validation scripts never run in CI; at least keep them importable-shaped and licensed."""

    def test_scripts_compile_and_carry_the_license(self):
        here = Path(__file__).resolve().parent
        for name in ("windows_agent_checks.py", "windows_ntfs_checks.py", "windows_cli_checks.py"):
            with self.subTest(script=name):
                text = (here / name).read_text(encoding="utf-8")
                compile(text, name, "exec")
                self.assertIn("SPDX-License-Identifier: GPL-2.0-or-later", text)


if __name__ == "__main__":
    unittest.main()
