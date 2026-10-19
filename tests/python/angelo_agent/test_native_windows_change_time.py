# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exact by-handle ChangeTime marshalling; real Windows filesystem behavior is separate."""

import ctypes
import struct
import unittest

from test_native_windows_nt import FakeLibraries, nt
from fake_c_libraries import CAbiLibrary


class ChangeTimeLibraries(FakeLibraries):
    def __init__(self):
        super().__init__()
        self.ticks = 0
        self.query_succeeds = True

    def _GetFileInformationByHandleEx(self, handle, info_class, buffer, size):
        self.calls.append(("GetFileInformationByHandleEx", handle, info_class, size))
        if not self.query_succeeds:
            self.error = 5
            return 0
        # Distinct values detect accidentally reading creation/access/write time or attributes.
        data = struct.pack("<qqqqI4x", 101, 202, 303, self.ticks, 0x20)
        if info_class != nt.FILE_BASIC_INFO or size != len(data):
            self.error = nt.ERROR_INVALID_PARAMETER
            return 0
        ctypes.memmove(buffer, data, len(data))
        return 1


class WindowsChangeTimeTests(unittest.TestCase):
    def api(self, *, abi=False, os_handle=None):
        fake = ChangeTimeLibraries()
        library = CAbiLibrary(fake, nt.NTDLL_FUNCTIONS + nt.KERNEL32_FUNCTIONS) if abi else fake
        api = nt.NtFileApi(
            ntdll=library, kernel32=library, last_error=lambda: fake.error,
            format_error=lambda code: "Windows error %d" % code,
            os_handle=os_handle if os_handle is not None else lambda fd: fd * 16,
            descriptor=lambda handle, flags: self.fail("Query must not create a descriptor"))
        return api, fake, library

    def test_signed_ticks_remain_exact_through_direct_and_ctypes_calls(self):
        for abi in (False, True):
            for ticks in (0, 1, (1 << 53) + 1, 134000000000000001, (1 << 63) - 1, -1, -(1 << 63)):
                with self.subTest(abi=abi, ticks=ticks):
                    api, fake, library = self.api(abi=abi)
                    fake.ticks = ticks
                    result = api.change_time(7)
                    self.assertIs(type(result), int)
                    self.assertEqual(result, ticks)
                    self.assertEqual(fake.calls, [("GetFileInformationByHandleEx", 0x70, nt.FILE_BASIC_INFO, 40)])
                    if abi:
                        self.assertEqual(library.errors, [])

    def test_changed_metadata_is_queried_again_without_cached_time(self):
        api, fake, _ = self.api()
        fake.ticks = 134000000000000001
        first = api.change_time(7)
        fake.ticks += 1
        self.assertEqual(api.change_time(7), first + 1)
        self.assertEqual(fake.calls, [("GetFileInformationByHandleEx", 0x70, nt.FILE_BASIC_INFO, 40)] * 2)

    def test_failed_kernel_query_raises_without_fallback_or_handle_effects(self):
        for abi in (False, True):
            with self.subTest(abi=abi):
                api, fake, library = self.api(abi=abi)
                fake.query_succeeds = False
                with self.assertRaisesRegex(OSError, "Windows error 5"):
                    api.change_time(7)
                self.assertEqual(fake.calls, [("GetFileInformationByHandleEx", 0x70, nt.FILE_BASIC_INFO, 40)])
                if abi:
                    self.assertEqual(library.errors, [])

    def test_handle_conversion_error_prevents_kernel_call(self):
        error = OSError("closed descriptor")

        def fail(fd):
            self.assertEqual(fd, 7)
            raise error

        api, fake, _ = self.api(os_handle=fail)
        with self.assertRaises(OSError) as raised:
            api.change_time(7)
        self.assertIs(raised.exception, error)
        self.assertEqual(fake.calls, [])


if __name__ == "__main__":
    unittest.main()
