/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Angelo: file identity and file-name equality as the file system decides them. See
 * wm_angelo_file_identity.hh. Kept free of Blender headers: test_native_windows_file_identity.py
 * compiles the Windows branch with stand-in Win32 declarations on any host.
 */

#include <cstdlib>
#include <cstring>

#include "wm_angelo_file_identity.hh"

#ifdef WIN32
#  include <windows.h>

#  include "utfconv.hh"
#elif defined(__APPLE__)
#  include <CoreFoundation/CoreFoundation.h>
#  include <sys/stat.h>
#else
#  include <sys/stat.h>
#endif

namespace blender {

enum : uint32_t {
  ANGELO_IDENTITY_POSIX = 1,
  ANGELO_IDENTITY_WIN_FILE_ID = 2,
  ANGELO_IDENTITY_WIN_FILE_INDEX = 3,
};

#ifdef WIN32
/** The 128-bit file ID first: on ReFS (Dev Drive) the 64-bit index is not unique. Then the 64-bit
 * index. A zero volume serial number or file number (some SMB/NAS servers) is unknown. */
static std::optional<WMAngeloFileIdentity> file_identity_from_handle(HANDLE handle)
{
  FILE_ID_INFO id_info;
  if (GetFileInformationByHandleEx(handle, FileIdInfo, &id_info, sizeof(id_info))) {
    uint64_t low = 0, high = 0;
    memcpy(&low, id_info.FileId.Identifier, sizeof(low));
    memcpy(&high, id_info.FileId.Identifier + sizeof(low), sizeof(high));
    if (id_info.VolumeSerialNumber != 0 && (low | high) != 0) {
      return WMAngeloFileIdentity{ANGELO_IDENTITY_WIN_FILE_ID, id_info.VolumeSerialNumber, low, high};
    }
  }
  BY_HANDLE_FILE_INFORMATION info;
  if (GetFileInformationByHandle(handle, &info)) {
    const uint64_t index = (uint64_t(info.nFileIndexHigh) << 32) | info.nFileIndexLow;
    if (info.dwVolumeSerialNumber != 0 && index != 0) {
      return WMAngeloFileIdentity{ANGELO_IDENTITY_WIN_FILE_INDEX, info.dwVolumeSerialNumber, index, 0};
    }
  }
  return std::nullopt;
}
#endif

std::optional<WMAngeloFileIdentity> WM_angelo_file_identity(const char *filepath)
{
  if (filepath == nullptr || filepath[0] == '\0') {
    return std::nullopt;
  }
#ifdef WIN32
  /* As BLI_stat: the handle lives outside the UTF16_ENCODE block, which opens a scope. */
  HANDLE handle = INVALID_HANDLE_VALUE;
  UTF16_ENCODE(filepath);
  handle = CreateFileW(filepath_16,
                       FILE_READ_ATTRIBUTES,
                       FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                       nullptr,
                       OPEN_EXISTING,
                       FILE_FLAG_BACKUP_SEMANTICS,
                       nullptr);
  UTF16_UN_ENCODE(filepath);
  if (handle == INVALID_HANDLE_VALUE) {
    return std::nullopt;
  }
  const std::optional<WMAngeloFileIdentity> identity = file_identity_from_handle(handle);
  CloseHandle(handle);
  return identity;
#else
  struct stat info;
  if (stat(filepath, &info) != 0 || info.st_ino == 0) {
    return std::nullopt;
  }
  return WMAngeloFileIdentity{ANGELO_IDENTITY_POSIX, uint64_t(info.st_dev), uint64_t(info.st_ino), 0};
#endif
}

bool WM_angelo_same_file_name(const char *a, const char *b)
{
  if (a == nullptr || b == nullptr) {
    return false;
  }
#ifdef WIN32
  /* NTFS and ReFS compare names ordinally, ignoring case (their upcase table); no normalization. */
  bool same = false;
  UTF16_ENCODE(a);
  UTF16_ENCODE(b);
  same = a_16 && b_16 && CompareStringOrdinal(a_16, -1, b_16, -1, TRUE) == CSTR_EQUAL;
  UTF16_UN_ENCODE(b);
  UTF16_UN_ENCODE(a);
  return same;
#elif defined(__APPLE__)
  /* APFS and HFS+ (by default): canonically equivalent (NFC/NFD) and case-insensitive names are one. */
  CFStringRef string_a = CFStringCreateWithBytes(
      kCFAllocatorDefault, reinterpret_cast<const UInt8 *>(a), CFIndex(strlen(a)), kCFStringEncodingUTF8, false);
  CFStringRef string_b = CFStringCreateWithBytes(
      kCFAllocatorDefault, reinterpret_cast<const UInt8 *>(b), CFIndex(strlen(b)), kCFStringEncodingUTF8, false);
  bool same = false;
  if (string_a && string_b) {
    same = CFStringCompare(string_a, string_b, kCFCompareCaseInsensitive | kCFCompareNonliteral) ==
           kCFCompareEqualTo;
  }
  else {
    same = strcmp(a, b) == 0;
  }
  if (string_a) {
    CFRelease(string_a);
  }
  if (string_b) {
    CFRelease(string_b);
  }
  return same;
#else
  return strcmp(a, b) == 0;
#endif
}

}  // namespace blender
