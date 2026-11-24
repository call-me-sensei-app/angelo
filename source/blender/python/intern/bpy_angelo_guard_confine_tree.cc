/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * The moved-folder walk (bpy_angelo_guard_confine_tree.hh). No Python: callable from any thread.
 */

#include "bpy_angelo_guard_confine_tree.hh"
#include "bpy_angelo_guard_confine_links.hh"
#include "bpy_angelo_guard_confine_paths.hh"
#include "bpy_angelo_guard_confine_remote.hh"

#include <vector>

#ifdef _WIN32
#  ifndef NOMINMAX
#    define NOMINMAX
#  endif
#  include <windows.h>
#else
#  include <cerrno>
#  include <climits>
#  include <cstdlib>
#  include <dirent.h>
#  include <fcntl.h>
#  include <sys/stat.h>
#  include <unistd.h>
#  ifdef __APPLE__
#    include <strings.h>
#    include <sys/mount.h>
#  endif
#endif

namespace blender::angelo_guard {

const char *move_tree_refusal(const TreeVerdict verdict)
{
  switch (verdict) {
    case TreeVerdict::Plain:
      return nullptr;
    case TreeVerdict::Link:
      return "An agent script can't move a folder that holds a symbolic link, junction or mounted folder: a path "
             "checked before the move could then lead through it. Move the files inside it instead.";
    case TreeVerdict::TooLarge:
      return "An agent script can't move a folder this large (more than 100000 entries, or folders nested more than "
             "128 deep): it can't be checked for links. Move its contents in parts instead.";
    case TreeVerdict::Unreadable:
      return "An agent script can't move this folder: a folder inside it can't be read to check it for links.";
    case TreeVerdict::Network:
      return "An agent script can't move this folder: its path leads to the network.";
  }
  return "An agent script can't move this folder.";
}

#ifdef _WIN32

namespace {

/** `\\?\` form of a full path, so long names and trailing dots are read as they are. Empty when it fails. */
std::wstring extended(const std::wstring &path)
{
  const DWORD needed = GetFullPathNameW(path.c_str(), 0, nullptr, nullptr);
  if (needed == 0) {
    return {};
  }
  std::wstring full(size_t(needed), L'\0');
  const DWORD length = GetFullPathNameW(path.c_str(), needed, full.data(), nullptr);
  if (length == 0 || length >= needed) {
    return {};
  }
  full.resize(length);
  if (full.rfind(L"\\\\?\\", 0) == 0) {
    return full;
  }
  if (full.rfind(L"\\\\", 0) == 0) {
    return L"\\\\?\\UNC\\" + full.substr(2);
  }
  return L"\\\\?\\" + full;
}

/** A link (a name surrogate), or a folder whose reparse tag isn't one known to keep its data (DFS, ProjFS, a tag
 * yet unknown: links.hh `reparse_tag_keeps_data`). */
bool name_surrogate(const DWORD attributes, const DWORD tag)
{
  if (!(attributes & FILE_ATTRIBUTE_REPARSE_POINT)) {
    return false;
  }
  return IsReparseTagNameSurrogate(tag) ||
         ((attributes & FILE_ATTRIBUTE_DIRECTORY) && !links::reparse_tag_keeps_data(uint32_t(tag)));
}

}  // namespace

TreeVerdict move_tree_verdict(const std::string &path, const int dir_fd, const size_t limit, size_t *entries)
{
  (void)dir_fd;
  size_t walked = 0;
  if (entries) {
    *entries = 0;
  }
  if (path.empty()) {
    return TreeVerdict::Plain;
  }
  if (network_lookup(path, -1, false) != Refusal::None) {
    return TreeVerdict::Network; /* Nothing looked up through a link to a share on the way. */
  }
  std::wstring decoded;
  if (!utf8_to_wide(path, decoded)) {
    return TreeVerdict::Unreadable;
  }
  const std::wstring root = extended(decoded);
  if (root.empty()) {
    return TreeVerdict::Unreadable;
  }
  WIN32_FIND_DATAW found;
  {
    /* The path itself, not followed: its own attributes and tag. */
    HANDLE self = FindFirstFileExW(root.c_str(), FindExInfoBasic, &found, FindExSearchNameMatch, nullptr, 0);
    if (self == INVALID_HANDLE_VALUE) {
      const DWORD error = GetLastError();
      return (error == ERROR_FILE_NOT_FOUND || error == ERROR_PATH_NOT_FOUND) ? TreeVerdict::Plain :
                                                                               TreeVerdict::Unreadable;
    }
    FindClose(self);
    if (name_surrogate(found.dwFileAttributes, found.dwReserved0)) {
      return TreeVerdict::Link;
    }
    if (!(found.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)) {
      return TreeVerdict::Plain;
    }
  }
  std::vector<std::wstring> folders{root};
  while (!folders.empty()) {
    const std::wstring folder = std::move(folders.back());
    folders.pop_back();
    HANDLE list = FindFirstFileExW((folder + L"\\*").c_str(),
                                   FindExInfoBasic,
                                   &found,
                                   FindExSearchNameMatch,
                                   nullptr,
                                   FIND_FIRST_EX_LARGE_FETCH);
    if (list == INVALID_HANDLE_VALUE) {
      if (GetLastError() == ERROR_FILE_NOT_FOUND) {
        continue; /* Empty. */
      }
      return TreeVerdict::Unreadable;
    }
    TreeVerdict verdict = TreeVerdict::Plain;
    do {
      const wchar_t *name = found.cFileName;
      if (name[0] == L'.' && (name[1] == L'\0' || (name[1] == L'.' && name[2] == L'\0'))) {
        continue;
      }
      if (++walked > limit) {
        verdict = TreeVerdict::TooLarge;
        break;
      }
      if (name_surrogate(found.dwFileAttributes, found.dwReserved0)) {
        verdict = TreeVerdict::Link;
        break;
      }
      if (found.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
        folders.push_back(folder + L"\\" + name);
      }
    } while (FindNextFileW(list, &found));
    const DWORD error = verdict == TreeVerdict::Plain ? GetLastError() : ERROR_NO_MORE_FILES;
    FindClose(list);
    if (entries) {
      *entries = walked;
    }
    if (verdict != TreeVerdict::Plain) {
      return verdict;
    }
    if (error != ERROR_NO_MORE_FILES) {
      return TreeVerdict::Unreadable;
    }
  }
  return TreeVerdict::Plain;
}

#else

namespace {
/* A look that never mounts what it looks at: an autofs direct-map trigger is a folder on the automounter's own device
 * until something opens it (head-10/11 re-read SHOULD). Linux has the flag; elsewhere a stat doesn't mount. */
#  ifdef AT_NO_AUTOMOUNT
constexpr int no_mount = AT_SYMLINK_NOFOLLOW | AT_NO_AUTOMOUNT;
#  else
constexpr int no_mount = AT_SYMLINK_NOFOLLOW;
#  endif

#  ifdef __APPLE__
/** Whether `folder` (absolute, as written) is, or holds, an autofs trigger the mount table lists (a direct map's
 * point, `/net`, `/home`): macOS has no AT_NO_AUTOMOUNT, and a stat of a trigger mounts it (head-12 re-read
 * SHOULD), so the walk asks by name before it looks. macOS folds case. */
bool holds_an_autofs_trigger(const std::string &folder)
{
  struct statfs *mounts = nullptr;
  const int count = getmntinfo_r_np(&mounts, MNT_NOWAIT); /* Its own buffer: safe on any thread. */
  bool found = false;
  for (int i = 0; i < count && !found; i++) {
    if (strcmp(mounts[i].f_fstypename, "autofs") != 0) {
      continue;
    }
    const std::string point = mounts[i].f_mntonname;
    found = point.size() >= folder.size() && strncasecmp(point.c_str(), folder.c_str(), folder.size()) == 0 &&
            (point.size() == folder.size() || point[folder.size()] == '/' || folder == "/");
  }
  free(mounts);
  return found || count < 0; /* A table that can't be read: refused. */
}

/** `path` absolute, as written (relative to `dir_fd` or the working folder), trailing separators dropped; and the
 * same with its parent resolved (`/tmp` is `/private/tmp`). Empty when either can't be named. */
std::vector<std::string> names_of(const std::string &path, const int dir_fd)
{
  std::string absolute = path;
  if (path.empty() || path[0] != '/') {
    char base[PATH_MAX];
    if (dir_fd >= 0 ? fcntl(dir_fd, F_GETPATH, base) == -1 : getcwd(base, sizeof(base)) == nullptr) {
      return {};
    }
    absolute = std::string(base) + "/" + path;
  }
  while (absolute.size() > 1 && absolute.back() == '/') {
    absolute.pop_back();
  }
  std::vector<std::string> names{absolute};
  const size_t cut = absolute.find_last_of('/');
  char parent[PATH_MAX];
  if (cut != std::string::npos && cut > 0 && realpath(absolute.substr(0, cut).c_str(), parent) != nullptr) {
    names.push_back(std::string(parent) + absolute.substr(cut));
  }
  return names;
}
#  endif
}  // namespace

TreeVerdict move_tree_verdict(const std::string &path, const int dir_fd, const size_t limit, size_t *entries)
{
  size_t walked = 0;
  if (entries) {
    *entries = 0;
  }
  if (path.empty()) {
    return TreeVerdict::Plain;
  }
  if (network_lookup(path, dir_fd, false) != Refusal::None) {
    return TreeVerdict::Network; /* Nothing looked up through the automounter or a link to it on the way. */
  }
#  ifdef __APPLE__
  {
    const std::vector<std::string> names = names_of(path, dir_fd);
    if (names.empty()) {
      return TreeVerdict::Unreadable;
    }
    for (const std::string &name : names) {
      if (holds_an_autofs_trigger(name)) {
        return TreeVerdict::Link; /* A trigger inside: it moves with the folder, and a look would mount it. */
      }
    }
  }
#  endif
  const int base = dir_fd < 0 ? AT_FDCWD : dir_fd;
  struct stat self;
  if (fstatat(base, path.c_str(), &self, no_mount) != 0) {
    return (errno == ENOENT || errno == ENOTDIR) ? TreeVerdict::Plain : TreeVerdict::Unreadable;
  }
  if (S_ISLNK(self.st_mode)) {
    return TreeVerdict::Link;
  }
  if (!S_ISDIR(self.st_mode)) {
    return TreeVerdict::Plain;
  }
  const int root = openat(base, path.c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
  if (root < 0) {
    return errno == ELOOP ? TreeVerdict::Link : TreeVerdict::Unreadable; /* Swapped for a link since: a link. */
  }
  /* Depth first; each open folder holds a descriptor, so the depth is capped too (a deeper tree: refused). */
  constexpr size_t depth_limit = 128;
  std::vector<DIR *> open;
  TreeVerdict verdict = TreeVerdict::Plain;
  DIR *first = fdopendir(root);
  if (first == nullptr) {
    close(root);
    return TreeVerdict::Unreadable;
  }
  open.push_back(first);
  while (!open.empty() && verdict == TreeVerdict::Plain) {
    DIR *folder = open.back();
    errno = 0;
    const dirent *entry = readdir(folder);
    if (entry == nullptr) {
      if (errno != 0) {
        verdict = TreeVerdict::Unreadable;
      }
      closedir(folder);
      open.pop_back();
      continue;
    }
    const char *name = entry->d_name;
    if (name[0] == '.' && (name[1] == '\0' || (name[1] == '.' && name[2] == '\0'))) {
      continue;
    }
    if (++walked > limit) {
      verdict = TreeVerdict::TooLarge;
      break;
    }
    unsigned char type = entry->d_type;
    struct stat before;
    const bool looked = type == DT_UNKNOWN || type == DT_DIR;
    if (looked && fstatat(dirfd(folder), name, &before, no_mount) != 0) {
      verdict = TreeVerdict::Unreadable;
      break;
    }
    if (type == DT_UNKNOWN) {
      type = S_ISLNK(before.st_mode) ? DT_LNK : S_ISDIR(before.st_mode) ? DT_DIR : DT_REG;
    }
    if (type == DT_LNK) {
      verdict = TreeVerdict::Link;
      break;
    }
    if (type != DT_DIR) {
      continue;
    }
    if (before.st_dev != self.st_dev) {
      verdict = TreeVerdict::Link; /* Another file system, or an automount trigger: never opened. */
      break;
    }
    if (open.size() >= depth_limit) {
      verdict = TreeVerdict::TooLarge;
      break;
    }
    const int child = openat(dirfd(folder), name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (child < 0) {
      verdict = errno == ELOOP ? TreeVerdict::Link : TreeVerdict::Unreadable;
      break;
    }
    struct stat info;
    if (fstat(child, &info) != 0 || info.st_dev != self.st_dev) {
      close(child); /* Another file system mounted inside (an automounted share among them): it moves with it. */
      verdict = TreeVerdict::Link;
      break;
    }
    DIR *next = fdopendir(child);
    if (next == nullptr) {
      close(child);
      verdict = TreeVerdict::Unreadable;
      break;
    }
    open.push_back(next);
  }
  for (DIR *folder : open) {
    closedir(folder);
  }
  if (entries) {
    *entries = walked;
  }
  return verdict;
}

#endif

}  // namespace blender::angelo_guard
