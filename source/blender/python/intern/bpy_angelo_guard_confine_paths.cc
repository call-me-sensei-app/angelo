/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Where a path really is, for the confinement (see the header). No Python, no GIL.
 */

#include <algorithm>
#include <cctype>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <shared_mutex>

#include "BLI_fileops.hh"

#include "bpy_angelo_guard_confine_paths.hh"

#ifdef _WIN32
#  ifndef NOMINMAX
#    define NOMINMAX
#  endif
#  include <io.h>
#  include <windows.h>
#else
#  include <fcntl.h>
#  include <limits.h>
#  include <sys/stat.h>
#  include <unistd.h>
#endif

namespace blender::angelo_guard {

#ifdef _WIN32

static std::wstring wide(const std::string &text)
{
  if (text.empty()) {
    return {};
  }
  const int length = MultiByteToWideChar(CP_UTF8, 0, text.data(), int(text.size()), nullptr, 0);
  std::wstring result(size_t(std::max(length, 0)), L'\0');
  if (length > 0) {
    MultiByteToWideChar(CP_UTF8, 0, text.data(), int(text.size()), result.data(), length);
  }
  return result;
}

static std::string narrow(const std::wstring &text)
{
  if (text.empty()) {
    return {};
  }
  const int length = WideCharToMultiByte(
      CP_UTF8, 0, text.data(), int(text.size()), nullptr, 0, nullptr, nullptr);
  std::string result(size_t(std::max(length, 0)), '\0');
  if (length > 0) {
    WideCharToMultiByte(
        CP_UTF8, 0, text.data(), int(text.size()), result.data(), length, nullptr, nullptr);
  }
  return result;
}

static std::wstring strip_prefix(std::wstring path)
{
  if (path.rfind(L"\\\\?\\UNC\\", 0) == 0) {
    return L"\\\\" + path.substr(8);
  }
  if (path.rfind(L"\\\\?\\", 0) == 0) {
    return path.substr(4);
  }
  return path;
}

static std::optional<std::wstring> final_path(HANDLE handle)
{
  std::wstring buffer(32768, L'\0');
  const DWORD length = GetFinalPathNameByHandleW(handle, buffer.data(), DWORD(buffer.size()), 0);
  if (length == 0 || length >= buffer.size()) {
    return std::nullopt;
  }
  buffer.resize(length);
  return strip_prefix(buffer);
}

static HANDLE open_any(const std::wstring &path)
{
  /* Folders too (backup semantics), following links and junctions; no access to the contents is needed. */
  return CreateFileW(path.c_str(),
                     0,
                     FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                     nullptr,
                     OPEN_EXISTING,
                     FILE_FLAG_BACKUP_SEMANTICS,
                     nullptr);
}

std::optional<std::string> descriptor_path(int descriptor)
{
  if (!BLI_file_descriptor_is_open(descriptor)) {
    return std::nullopt; /* The C runtime ends the process on a closed or out-of-range descriptor. */
  }
  const intptr_t os_handle = _get_osfhandle(descriptor);
  if (os_handle == -1) {
    return std::nullopt;
  }
  if (GetFileType(HANDLE(os_handle)) != FILE_TYPE_DISK) {
    return std::nullopt;
  }
  const std::optional<std::wstring> found = final_path(HANDLE(os_handle));
  if (!found) {
    return std::nullopt;
  }
  return narrow(*found);
}

std::optional<std::string> resolve(const std::string &path, int /*dir_fd*/)
{
  /* Windows has no folder descriptors: `dir_fd` is never set there. */
  if (path.size() >= 4 && (path[0] == '\\' || path[0] == '/') && path[1] == '?' && path[2] == '?' &&
      (path[3] == '\\' || path[3] == '/'))
  {
    /* `\??\`: Win32 passes it to the system as an NT path (`\??\C:\x` is `C:\x`), while GetFullPathNameW reads it as
     * root-relative (`C:\??\C:\x`): never judged as a place it doesn't open (the re-read of piece 10). */
    return std::nullopt;
  }
  const std::wstring input = wide(path);
  std::wstring full(32768, L'\0');
  const DWORD length = GetFullPathNameW(input.c_str(), DWORD(full.size()), full.data(), nullptr);
  if (length == 0 || length >= full.size()) {
    return std::nullopt;
  }
  full.resize(length);
  /* The longest prefix that exists resolves through the system (links, junctions, 8.3 names, case); the rest is
   * appended as written: a new file's folder resolves. */
  std::wstring head = full, tail;
  while (true) {
    HANDLE handle = open_any(head);
    if (handle != INVALID_HANDLE_VALUE) {
      const std::optional<std::wstring> found = final_path(handle);
      CloseHandle(handle);
      if (!found) {
        return std::nullopt;
      }
      head = *found;
      break;
    }
    const size_t cut = head.find_last_of(L"\\/");
    if (cut == std::wstring::npos || cut <= 2) {
      break; /* A drive root or nothing that exists: as written. */
    }
    tail = head.substr(cut) + tail;
    head = head.substr(0, cut);
  }
  while (!head.empty() && (head.back() == L'\\' || head.back() == L'/') && head.size() > 3) {
    head.pop_back();
  }
  return narrow(head + tail);
}

static std::optional<Identity> identity_of(const std::string &path, bool folders_only)
{
  HANDLE handle = open_any(wide(path));
  if (handle == INVALID_HANDLE_VALUE) {
    return std::nullopt;
  }
  BY_HANDLE_FILE_INFORMATION info;
  const bool ok = GetFileInformationByHandle(handle, &info);
  CloseHandle(handle);
  if (!ok || (folders_only && !(info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY))) {
    return std::nullopt;
  }
  return Identity(info.dwVolumeSerialNumber,
                  (uint64_t(info.nFileIndexHigh) << 32) | uint64_t(info.nFileIndexLow));
}

static const char SEP = '\\';

#else /* POSIX */

std::optional<std::string> descriptor_path(int descriptor)
{
#  ifdef __APPLE__
  char buffer[PATH_MAX + 1] = {0};
  if (fcntl(descriptor, F_GETPATH, buffer) == -1 || buffer[0] != '/') {
    return std::nullopt;
  }
  return std::string(buffer);
#  else
  char link[64];
  snprintf(link, sizeof(link), "/proc/self/fd/%d", descriptor);
  char buffer[PATH_MAX + 1];
  const ssize_t length = readlink(link, buffer, PATH_MAX);
  if (length <= 0 || buffer[0] != '/') {
    return std::nullopt;
  }
  return std::string(buffer, size_t(length));
#  endif
}

static std::vector<std::string> split(const std::string &path)
{
  std::vector<std::string> parts;
  size_t start = 0;
  while (start <= path.size()) {
    const size_t end = path.find('/', start);
    const std::string part = path.substr(start, end == std::string::npos ? std::string::npos : end - start);
    if (!part.empty() && part != ".") {
      parts.push_back(part);
    }
    if (end == std::string::npos) {
      break;
    }
    start = end + 1;
  }
  return parts;
}

static std::string join(const std::vector<std::string> &parts)
{
  std::string result;
  for (const std::string &part : parts) {
    result += "/" + part;
  }
  return result.empty() ? "/" : result;
}

std::optional<std::string> resolve(const std::string &path, int dir_fd)
{
  std::string absolute = path;
  if (absolute.empty() || absolute[0] != '/') {
    std::string base;
    if (dir_fd >= 0) {
      const std::optional<std::string> folder = descriptor_path(dir_fd);
      if (!folder) {
        return std::nullopt;
      }
      base = *folder;
    }
    else {
      char cwd[PATH_MAX + 1];
      if (getcwd(cwd, sizeof(cwd)) == nullptr) {
        return std::nullopt;
      }
      base = cwd;
    }
    absolute = base + "/" + absolute;
  }
  std::vector<std::string> pending = split(absolute);
  std::reverse(pending.begin(), pending.end());
  std::vector<std::string> current;
  int links = 0;
  while (!pending.empty()) {
    const std::string name = pending.back();
    pending.pop_back();
    if (name == "..") {
      if (!current.empty()) {
        current.pop_back();
      }
      continue;
    }
    std::vector<std::string> candidate_parts = current;
    candidate_parts.push_back(name);
    const std::string candidate = join(candidate_parts);
    struct stat info;
    if (lstat(candidate.c_str(), &info) != 0) {
      current.push_back(name); /* Does not exist: the rest as written. */
      continue;
    }
    if (S_ISLNK(info.st_mode)) {
      if (++links > 64) {
        return std::nullopt;
      }
      char target[PATH_MAX + 1];
      const ssize_t length = readlink(candidate.c_str(), target, PATH_MAX);
      if (length <= 0) {
        return std::nullopt;
      }
      const std::string text(target, size_t(length));
      if (text[0] == '/') {
        current.clear();
      }
      std::vector<std::string> more = split(text);
      std::reverse(more.begin(), more.end());
      pending.insert(pending.end(), more.begin(), more.end());
      continue;
    }
    current.push_back(name);
  }
  std::string resolved = join(current);
  /* A descriptor's own path, as the system opens it (macOS /dev/fd/N; Linux /proc links resolved above). */
  for (const char *prefix : {"/dev/fd/", "/proc/self/fd/"}) {
    const size_t size = strlen(prefix);
    if (resolved.compare(0, size, prefix) == 0 && resolved.size() > size &&
        std::all_of(resolved.begin() + size, resolved.end(), [](char c) { return isdigit(c); }))
    {
      return descriptor_path(atoi(resolved.c_str() + size));
    }
  }
  return resolved;
}

static std::optional<Identity> identity_of(const std::string &path, bool folders_only)
{
  struct stat info;
  if (stat(path.c_str(), &info) != 0 || (folders_only && !S_ISDIR(info.st_mode))) {
    return std::nullopt;
  }
  return Identity(uint64_t(info.st_dev), uint64_t(info.st_ino));
}

static const char SEP = '/';

#endif

std::string key(const std::string &resolved)
{
#if defined(_WIN32) || defined(__APPLE__)
  /* These file systems ignore case; another Unicode spelling of an existing folder is caught by its identity. */
  std::string result = resolved;
  std::transform(result.begin(), result.end(), result.begin(), [](unsigned char c) {
    return char(std::tolower(c));
  });
#  ifdef _WIN32
  std::replace(result.begin(), result.end(), '/', '\\');
#  endif
  return result;
#else
  return resolved;
#endif
}

std::optional<Identity> folder_identity(const std::string &path)
{
  return identity_of(path, true);
}

std::vector<Identity> ancestors(const std::string &resolved)
{
  std::vector<Identity> found;
  std::string current = resolved;
  for (int guard = 0; guard < 512; guard++) {
    if (const std::optional<Identity> id = identity_of(current, false)) {
      found.push_back(*id);
    }
    const size_t cut = current.find_last_of("/\\");
    if (cut == std::string::npos) {
      break;
    }
    std::string parent = current.substr(0, cut);
#ifdef _WIN32
    if (parent.size() == 2 && parent[1] == ':') {
      parent += SEP;
    }
#endif
    if (parent.empty()) {
      parent = std::string(1, SEP);
    }
    if (parent == current) {
      break;
    }
    current = parent;
  }
  return found;
}

std::optional<Identity> entry_identity(const std::string &path)
{
  return identity_of(path, false);
}

FILE *open_ungated(const std::string &path, const char *mode)
{
#ifdef _WIN32
  return _wfopen(wide(path).c_str(), wide(mode).c_str());
#else
  return std::fopen(path.c_str(), mode);
#endif
}

bool replace_ungated(const std::string &from, const std::string &to)
{
#ifdef _WIN32
  return MoveFileExW(wide(from).c_str(), wide(to).c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH);
#else
  return std::rename(from.c_str(), to.c_str()) == 0;
#endif
}

}  // namespace blender::angelo_guard
