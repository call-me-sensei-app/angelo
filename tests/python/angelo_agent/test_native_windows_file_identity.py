# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Windows branch of Angelo's file identity compiles, checked on any host.

Evidence (break-it review round 3 of the restored-document change): the first Windows version declared its file handle
inside ``UTF16_ENCODE``'s scope (utfconv.hh opens a block) and used it after the block closed, which no macOS or Linux
build sees. ``wm_angelo_file_identity.cc`` is kept free of Blender headers so this test compiles the real file with
``-fsyntax-only``, ``WIN32`` defined, the real ``utfconv.hh`` and stand-in Win32 declarations whose signatures follow
the Windows SDK (types, constants and the four calls the file makes). It proves the code compiles as written for
Windows, not its behaviour on NTFS or ReFS. The file's native branch is compiled the same way.
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from host_test_policy import environment_skip  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "source/blender/windowmanager/intern/wm_angelo_file_identity.cc"
INCLUDES = (ROOT / "source/blender/windowmanager/intern", ROOT / "intern/utfconv")

# Declarations as in the Windows SDK (minwindef.h, fileapi.h, winbase.h, stringapiset.h, winnls.h), reduced to what the
# file uses. A wrong type, argument count or a name used outside its scope fails the compile, as on Windows.
WINDOWS_H = r"""
#pragma once
#include <cstdint>
typedef int BOOL;
typedef unsigned long DWORD;
typedef unsigned char BYTE;
typedef unsigned long long ULONGLONG;
typedef void *HANDLE;
typedef void *LPVOID;
typedef const wchar_t *LPCWSTR;
typedef const wchar_t *LPCWCH;
typedef struct _SECURITY_ATTRIBUTES SECURITY_ATTRIBUTES, *LPSECURITY_ATTRIBUTES;
#define TRUE 1
#define FALSE 0
#define INVALID_HANDLE_VALUE ((HANDLE)(intptr_t)-1)
#define FILE_READ_ATTRIBUTES 0x0080
#define FILE_SHARE_READ 0x00000001
#define FILE_SHARE_WRITE 0x00000002
#define FILE_SHARE_DELETE 0x00000004
#define OPEN_EXISTING 3
#define FILE_FLAG_BACKUP_SEMANTICS 0x02000000
#define CSTR_EQUAL 2
typedef struct _FILETIME { DWORD dwLowDateTime, dwHighDateTime; } FILETIME;
typedef struct _BY_HANDLE_FILE_INFORMATION {
  DWORD dwFileAttributes;
  FILETIME ftCreationTime, ftLastAccessTime, ftLastWriteTime;
  DWORD dwVolumeSerialNumber, nFileSizeHigh, nFileSizeLow, nNumberOfLinks, nFileIndexHigh, nFileIndexLow;
} BY_HANDLE_FILE_INFORMATION, *LPBY_HANDLE_FILE_INFORMATION;
typedef struct _FILE_ID_128 { BYTE Identifier[16]; } FILE_ID_128;
typedef struct _FILE_ID_INFO { ULONGLONG VolumeSerialNumber; FILE_ID_128 FileId; } FILE_ID_INFO;
typedef enum _FILE_INFO_BY_HANDLE_CLASS { FileBasicInfo = 0, FileIdInfo = 18 } FILE_INFO_BY_HANDLE_CLASS;
HANDLE CreateFileW(LPCWSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES, DWORD, DWORD, HANDLE);
BOOL CloseHandle(HANDLE);
BOOL GetFileInformationByHandle(HANDLE, LPBY_HANDLE_FILE_INFORMATION);
BOOL GetFileInformationByHandleEx(HANDLE, FILE_INFO_BY_HANDLE_CLASS, LPVOID, DWORD);
int CompareStringOrdinal(LPCWCH, int, LPCWCH, int, BOOL);
"""


def compiler():
    return shutil.which("clang++") or shutil.which("g++")


class WindowsFileIdentityCompileTests(unittest.TestCase):
    def compile(self, *defines, stand_in=None):
        found = compiler()
        if found is None:
            self.skipTest(environment_skip("no C++ compiler (clang++ or g++) to syntax-check the Windows branch"))
        command = [found, "-std=c++17", "-fsyntax-only", "-Werror=return-type", *defines,
                   *(f"-I{path}" for path in INCLUDES)]
        if stand_in is not None:
            command.append(f"-I{stand_in}")
        result = subprocess.run(command + [str(SOURCE)], capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_windows_branch_compiles(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "windows.h").write_text(WINDOWS_H, encoding="utf-8")
            self.compile("-DWIN32", stand_in=directory)   # WIN32 is tested first in the file.

    def test_the_native_branch_compiles(self):
        self.compile()

    def test_the_file_stays_free_of_blender_headers(self):
        includes = [line for line in SOURCE.read_text(encoding="utf-8").splitlines() if line.lstrip("# ").startswith("include")]
        allowed = ('"wm_angelo_file_identity.hh"', '"utfconv.hh"', "<windows.h>", "<cstdlib>", "<cstring>",
                   "<CoreFoundation/CoreFoundation.h>", "<sys/stat.h>")
        for line in includes:
            with self.subTest(line=line):
                self.assertTrue(line.strip().endswith(allowed), line)


if __name__ == "__main__":
    unittest.main()
