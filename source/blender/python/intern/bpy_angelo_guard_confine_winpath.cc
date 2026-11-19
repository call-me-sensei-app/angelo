/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Where a Windows path really leads (bpy_angelo_guard_confine_winpath.hh): its final spelling, and whether a link or
 * junction lies on the way. Every component is opened without following it (`FILE_FLAG_OPEN_REPARSE_POINT`) and
 * without asking for any access, so a file locked by another program, or a folder the person can only traverse,
 * still answers.
 */

#include "bpy_angelo_guard_confine_winpath.hh"

#include <cctype>

#ifdef _WIN32
#  ifndef NOMINMAX
#    define NOMINMAX
#  endif
#  include <windows.h>

#  include <deque>
#else
#  include <fcntl.h>
#  include <sys/stat.h>
#endif

namespace blender::angelo_guard {

namespace {

bool drive_absolute(const std::string &path)
{
  return path.size() >= 3 && isalpha((unsigned char)path[0]) && path[1] == ':' && path[2] == '\\';
}

/** `\\server\share` and what follows it: both named. */
bool share_absolute(const std::string &path)
{
  if (path.size() < 5 || path[0] != '\\' || path[1] != '\\') {
    return false;
  }
  const size_t server_end = path.find('\\', 2);
  if (server_end == std::string::npos || server_end == 2) {
    return false;
  }
  const size_t share_end = path.find('\\', server_end + 1);
  return (share_end == std::string::npos ? path.size() : share_end) > server_end + 1;
}

}  // namespace

std::string windows_plain_with(const std::string &path, const std::function<std::string(const std::string &)> &full)
{
  if (path.empty()) {
    return "";
  }
  if (path.rfind("\\\\?\\", 0) == 0) {
    const std::string rest = path.substr(4);
    if (rest.find('/') != std::string::npos) {
      return "";
    }
    std::string plain;
    if (rest.size() >= 4 && (rest[0] == 'U' || rest[0] == 'u') && (rest[1] == 'N' || rest[1] == 'n') &&
        (rest[2] == 'C' || rest[2] == 'c') && rest[3] == '\\')
    {
      plain = "\\\\" + rest.substr(4);
      if (!share_absolute(plain)) {
        return "";
      }
    }
    else if (drive_absolute(rest)) {
      plain = rest;
    }
    else {
      return ""; /* GLOBALROOT, a volume GUID, a device: not a path Angelo names. */
    }
    /* Literal here, normalized in the plain form: only one spelling both read the same way folds. */
    return full(plain) == plain ? plain : "";
  }
  std::string text = path;
  for (char &c : text) {
    c = (c == '/') ? '\\' : c;
  }
  if (text.rfind("\\\\", 0) == 0) {
    /* `\\.\` and `\\?\` spelled with `/` are device paths to Win32; a share needs both its names. */
    if (text.size() > 2 && (text[2] == '.' || text[2] == '?')) {
      return "";
    }
    return share_absolute(text) ? text : "";
  }
  if (text[0] == '\\') {
    return ""; /* `\??\` (an NT path) or root-relative: the current drive's, never what the judge reads. */
  }
  if (text.size() >= 2 && text[1] == ':' && !drive_absolute(text)) {
    return ""; /* Drive-relative `C:x`. */
  }
  return text;
}

#ifdef _WIN32

namespace {

std::wstring to_wide(const std::string &utf8, bool *r_ok)
{
  *r_ok = false;
  if (utf8.empty()) {
    return {};
  }
  const int size = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, utf8.data(), int(utf8.size()), nullptr, 0);
  if (size <= 0) {
    return {};
  }
  std::wstring wide(size_t(size), L'\0');
  if (MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, utf8.data(), int(utf8.size()), wide.data(), size) != size) {
    return {};
  }
  *r_ok = wide.find(L'\0') == std::wstring::npos; /* An embedded NUL names nothing the caller meant. */
  return wide;
}

std::string to_utf8(const std::wstring &wide, bool *r_ok)
{
  *r_ok = false;
  if (wide.empty()) {
    return {};
  }
  const int size = WideCharToMultiByte(
      CP_UTF8, WC_ERR_INVALID_CHARS, wide.data(), int(wide.size()), nullptr, 0, nullptr, nullptr);
  if (size <= 0) {
    return {};
  }
  std::string utf8(size_t(size), '\0');
  if (WideCharToMultiByte(CP_UTF8,
                          WC_ERR_INVALID_CHARS,
                          wide.data(),
                          int(wide.size()),
                          utf8.data(),
                          size,
                          nullptr,
                          nullptr) != size)
  {
    return {};
  }
  *r_ok = true;
  return utf8;
}

bool starts_with(const std::wstring &text, const wchar_t *prefix)
{
  return text.rfind(prefix, 0) == 0;
}

/** A path in the `\\?\` form every file call takes unchanged (and longer than MAX_PATH), with the length of its root
 * (`\\?\C:\`, `\\?\UNC\server\share\`, `\\?\Volume{...}\`), which is never taken apart. Empty for a device path or
 * a name Win32 can't make absolute. */
struct Extended {
  std::wstring path;
  size_t root = 0;
};

Extended extended(const std::string &utf8)
{
  bool ok = false;
  std::wstring wide = to_wide(utf8, &ok);
  if (!ok) {
    return {};
  }
  for (wchar_t &c : wide) {
    if (c == L'/') {
      c = L'\\';
    }
  }
  std::wstring absolute;
  if (starts_with(wide, L"\\\\?\\")) {
    absolute = wide; /* Already final: Win32 normalizes nothing in this form. */
  }
  else {
    const DWORD size = GetFullPathNameW(wide.c_str(), 0, nullptr, nullptr);
    if (size == 0) {
      return {};
    }
    absolute.assign(size_t(size), L'\0');
    const DWORD written = GetFullPathNameW(wide.c_str(), size, absolute.data(), nullptr);
    if (written == 0 || written >= size) {
      return {};
    }
    absolute.resize(written);
    if (starts_with(absolute, L"\\\\.\\")) {
      return {}; /* A device (`NUL`, `CON`, a pipe): not a file. */
    }
    if (starts_with(absolute, L"\\\\")) {
      absolute = L"\\\\?\\UNC\\" + absolute.substr(2);
    }
    else if (absolute.size() >= 3 && absolute[1] == L':' && absolute[2] == L'\\') {
      absolute = L"\\\\?\\" + absolute;
    }
    else {
      return {};
    }
  }
  /* The root: one component after `\\?\` (a drive or a volume), or `UNC\server\share`. */
  size_t at = 4;
  int parts = 1;
  if (absolute.compare(4, 4, L"UNC\\") == 0) {
    at = 8;
    parts = 2;
  }
  for (int i = 0; i < parts; i++) {
    const size_t separator = absolute.find(L'\\', at);
    if (separator == std::wstring::npos || separator == at) {
      if (i == parts - 1 && separator == std::wstring::npos && at < absolute.size()) {
        absolute += L'\\'; /* `C:` alone, or `\\server\share`: its root. */
        at = absolute.size();
        break;
      }
      return {};
    }
    at = separator + 1;
  }
  while (absolute.size() > at && absolute.back() == L'\\') {
    absolute.pop_back(); /* A trailing separator names the same file. */
  }
  return {absolute, at};
}

/** Opens a component without following it; `INVALID_HANDLE_VALUE` with the error kept on failure. */
HANDLE open_unfollowed(const std::wstring &path)
{
  return CreateFileW(path.c_str(),
                     0,
                     FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                     nullptr,
                     OPEN_EXISTING,
                     FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
                     nullptr);
}

bool missing(const DWORD error)
{
  return error == ERROR_FILE_NOT_FOUND || error == ERROR_PATH_NOT_FOUND;
}

enum class Link { No, Yes, Unknown };

/** Whether an open component names another place (a name surrogate reparse point). */
Link names_another_place(HANDLE handle)
{
  FILE_ATTRIBUTE_TAG_INFO info = {};
  if (!GetFileInformationByHandleEx(handle, FileAttributeTagInfo, &info, sizeof(info))) {
    return Link::Unknown;
  }
  return (info.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) && IsReparseTagNameSurrogate(info.ReparseTag) ?
             Link::Yes :
             Link::No;
}

/** The parent of a path in extended form, never above its root. */
std::wstring parent_of(const Extended &path, const std::wstring &of)
{
  const size_t separator = of.find_last_of(L'\\');
  if (separator == std::wstring::npos || separator + 1 <= path.root) {
    return of.substr(0, path.root);
  }
  return of.substr(0, separator);
}

/** The DOS name of the file `handle` has open (drive letter or `\\server\share`, no `\\?\` prefix), then closes
 * it. Empty when no drive letter or share names its volume. */
std::wstring named_by(HANDLE handle)
{
  std::wstring named(MAX_PATH, L'\0');
  DWORD length = 0;
  while (true) {
    length = GetFinalPathNameByHandleW(
        handle, named.data(), DWORD(named.size()), FILE_NAME_NORMALIZED | VOLUME_NAME_DOS);
    if (length == 0 || length < named.size()) {
      break;
    }
    named.assign(size_t(length) + 1, L'\0'); /* Too small: `length` counts the terminating NUL then. */
  }
  CloseHandle(handle);
  if (length == 0) {
    return {};
  }
  named.resize(length);
  if (starts_with(named, L"\\\\?\\UNC\\")) {
    named = L"\\\\" + named.substr(8);
  }
  else if (starts_with(named, L"\\\\?\\")) {
    named = named.substr(4);
  }
  return named;
}

}  // namespace

std::string followed_path(const std::string &utf8)
{
  const Extended path = extended(utf8);
  if (path.path.empty()) {
    return {};
  }
  /* Opened as a program start opens it: every link followed, the last one included. */
  HANDLE handle = CreateFileW(path.path.c_str(),
                              0,
                              FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                              nullptr,
                              OPEN_EXISTING,
                              FILE_FLAG_BACKUP_SEMANTICS,
                              nullptr);
  if (handle == INVALID_HANDLE_VALUE) {
    return {};
  }
  const std::wstring named = named_by(handle);
  bool ok = false;
  std::string result = named.empty() ? std::string() : to_utf8(named, &ok);
  return ok ? result : std::string();
}

std::string final_path(const std::string &utf8)
{
  const Extended path = extended(utf8);
  if (path.path.empty()) {
    return {};
  }
  std::wstring prefix = path.path;
  std::deque<std::wstring> rest;
  HANDLE handle = INVALID_HANDLE_VALUE;
  while (true) {
    handle = open_unfollowed(prefix);
    if (handle != INVALID_HANDLE_VALUE) {
      break;
    }
    if (!missing(GetLastError()) || prefix.size() <= path.root) {
      return {}; /* It exists but can't be opened, or not even its volume does. */
    }
    const std::wstring parent = parent_of(path, prefix);
    rest.push_front(prefix.substr(parent.size() + (parent.size() > path.root ? 1 : 0)));
    prefix = parent;
  }
  std::wstring named = named_by(handle);
  if (named.empty()) {
    return {}; /* No drive letter or share names the volume. */
  }
  for (const std::wstring &component : rest) {
    if (named.empty() || named.back() != L'\\') {
      named += L'\\';
    }
    named += component;
  }
  bool ok = false;
  std::string result = to_utf8(named, &ok);
  return ok ? result : std::string();
}

bool reparse_in_any_component(const std::string &utf8)
{
  const Extended path = extended(utf8);
  if (path.path.empty()) {
    return true;
  }
  /* From the volume's root down: a component that is a link is judged itself, before anything is opened through
   * it. */
  size_t at = path.root;
  while (at < path.path.size()) {
    size_t end = path.path.find(L'\\', at);
    if (end == std::wstring::npos) {
      end = path.path.size();
    }
    HANDLE handle = open_unfollowed(path.path.substr(0, end));
    if (handle == INVALID_HANDLE_VALUE) {
      return !missing(GetLastError()); /* Nothing below a missing component exists yet. */
    }
    const Link link = names_another_place(handle);
    CloseHandle(handle);
    if (link != Link::No) {
      return true;
    }
    at = end + 1;
  }
  return false;
}

bool last_component_links(const std::string &utf8, int /*dir_fd*/)
{
  const Extended path = extended(utf8);
  if (path.path.empty()) {
    return true;
  }
  HANDLE handle = open_unfollowed(path.path);
  if (handle == INVALID_HANDLE_VALUE) {
    return !missing(GetLastError());
  }
  const Link link = names_another_place(handle);
  CloseHandle(handle);
  return link != Link::No;
}

#else

std::string followed_path(const std::string &utf8)
{
  return utf8;
}

std::string final_path(const std::string &utf8)
{
  return utf8;
}

bool reparse_in_any_component(const std::string & /*utf8*/)
{
  return false;
}

bool last_component_links(const std::string &utf8, int dir_fd)
{
  struct stat info;
  return fstatat(dir_fd >= 0 ? dir_fd : AT_FDCWD, utf8.c_str(), &info, AT_SYMLINK_NOFOLLOW) == 0 &&
         S_ISLNK(info.st_mode);
}

#endif

std::string windows_plain(const std::string &path)
{
#ifdef _WIN32
  return windows_plain_with(path, [](const std::string &plain) -> std::string {
    bool ok = false;
    const std::wstring wide = to_wide(plain, &ok);
    if (!ok) {
      return "";
    }
    const DWORD size = GetFullPathNameW(wide.c_str(), 0, nullptr, nullptr);
    if (size == 0) {
      return "";
    }
    std::wstring full(size_t(size), L'\0');
    const DWORD written = GetFullPathNameW(wide.c_str(), size, full.data(), nullptr);
    if (written == 0 || written >= size) {
      return "";
    }
    full.resize(written);
    const std::string utf8 = to_utf8(full, &ok);
    return ok ? utf8 : "";
  });
#else
  return path;
#endif
}

}  // namespace blender::angelo_guard
