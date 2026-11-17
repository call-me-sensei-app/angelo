/* SPDX-FileCopyrightText: 2026 Angelo Contributors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */
#include "BLO_asset_source_paths.hh"

#include <cstring>
#include <string>
#include <vector>

#include "BLI_path_utils.hh"
#include "BLI_string.hh"
#include "BLI_string_utils.hh"
#include "DNA_space_enums.h"

namespace blender {

class BLOAssetSourcePaths {
 public:
  struct Pair {
    /** The private copy as the caller spelled it: what a resolved read opens. */
    std::string private_path;
    /** The original and the private copy in the one spelling paths are compared in
     * (#one_spelling). Relative references are joined to `original_key`. */
    std::string original_key;
    std::string private_key;
  };
  std::vector<Pair> pairs;
};

static thread_local std::shared_ptr<const BLOAssetSourcePaths> active_paths;

/**
 * Spell an absolute \a path the one way paths are compared in.
 *
 * On Windows either separator names the same file, while #BLI_path_normalize and #BLI_path_abs
 * read and write only the native one: `C:/a/../b.blend` has no separator the first recognises,
 * and the second answers `C:\a\b.blend` for a base spelled `C:/a/...`. Compared as written,
 * a path spelled with `/` was never checked for `..`, never matched what a relative reference
 * resolved to, and could be registered beside its own other spelling. So the native separator is
 * the one spelling there. Elsewhere a backslash in an absolute path is a character of a file name:
 * nothing changes.
 *
 * Separators are all this unifies. Other names Windows gives one file (a trailing dot or space,
 * a short 8.3 name, a `\\?\` prefix, a mapped drive and its share, a `::$DATA` stream) stay
 * different spellings: knowing in general that two of them name one file means asking the file
 * system, which this code never does. A lookup spelled one of those ways matches nothing and is
 * refused; that a file is registered once only is the caller's to ensure, by passing paths the
 * file system resolved.
 */
static void one_spelling(char *path)
{
#ifdef WIN32
  BLI_string_replace_char(path, ALTSEP, SEP);
#else
  UNUSED_VARS(path);
#endif
}

/**
 * Whether \a path names one place whatever the working directory is. On Windows that is a share
 * or a drive followed by a separator: `C:name` and a bare `C:` are places under the drive's
 * current folder, though #BLI_path_is_abs_from_cwd counts them.
 */
static bool absolute_anywhere(const char *path)
{
  return BLI_path_is_abs_from_cwd(path) &&
         (!BLI_path_is_win32_drive(path) || BLI_path_is_win32_drive_with_slash(path));
}

/**
 * Whether \a path is absolute and canonical in the one spelling paths are compared in, which
 * \a r_key then holds.
 */
static bool canonical_absolute(const StringRef path, std::string &r_key)
{
  if (path.is_empty() || path.size() >= FILE_MAX ||
      std::memchr(path.data(), '\0', size_t(path.size())) != nullptr)
  {
    return false;
  }
  char normalized[FILE_MAX];
  memcpy(normalized, path.data(), size_t(path.size()));
  normalized[path.size()] = '\0';
  /* Before the spelling: `//` is Blender's relative prefix, never a share spelled with `/`. */
  if (BLI_path_is_rel(normalized) || !absolute_anywhere(normalized)) {
    return false;
  }
  one_spelling(normalized);
  r_key = normalized;
  BLI_path_normalize(normalized);
  return r_key == normalized;
}

std::shared_ptr<const BLOAssetSourcePaths> BLO_asset_source_paths_create(
    const Span<BLOAssetSourcePathPair> pairs, const char **error)
{
  auto paths = std::make_shared<BLOAssetSourcePaths>();
  for (const BLOAssetSourcePathPair &pair : pairs) {
    std::string original_key, private_key;
    if (!canonical_absolute(pair.original_absolute, original_key) ||
        !canonical_absolute(pair.private_absolute, private_key))
    {
      if (error) {
        *error = "Asset source paths must be canonical absolute paths within FILE_MAX";
      }
      return nullptr;
    }
    if (BLI_path_cmp(original_key.c_str(), private_key.c_str()) == 0) {
      if (error) {
        *error = "Asset source must have a distinct private copy";
      }
      return nullptr;
    }
    for (const BLOAssetSourcePaths::Pair &existing : paths->pairs) {
      if (BLI_path_cmp(existing.original_key.c_str(), original_key.c_str()) == 0 ||
          BLI_path_cmp(existing.private_key.c_str(), private_key.c_str()) == 0 ||
          BLI_path_cmp(existing.original_key.c_str(), private_key.c_str()) == 0 ||
          BLI_path_cmp(existing.private_key.c_str(), original_key.c_str()) == 0)
      {
        if (error) {
          *error = "Asset source paths contain an ambiguous original or private path";
        }
        return nullptr;
      }
    }
    paths->pairs.push_back(
        {std::string(pair.private_absolute), std::move(original_key), std::move(private_key)});
  }
  return paths;
}

bool BLO_asset_source_paths_activate(std::shared_ptr<const BLOAssetSourcePaths> paths,
                                     const char **error)
{
  if (!paths || active_paths) {
    if (error) {
      *error = "Asset source route is missing or another route is already active";
    }
    return false;
  }
  active_paths = std::move(paths);
  return true;
}

void BLO_asset_source_paths_deactivate(const BLOAssetSourcePaths &paths) noexcept
{
  if (active_paths.get() == &paths) {
    active_paths.reset();
  }
}

std::shared_ptr<const BLOAssetSourcePaths> BLO_asset_source_paths_current()
{
  return active_paths;
}

bool BLO_asset_source_path_resolve(const std::shared_ptr<const BLOAssetSourcePaths> &paths,
                                   const StringRef path,
                                   const StringRef parent_private,
                                   char *resolved)
{
  if (!paths || active_paths != paths || path.is_empty() || path.size() >= FILE_MAX ||
      std::memchr(path.data(), '\0', size_t(path.size())) != nullptr)
  {
    return false;
  }
  char absolute[FILE_MAX];
  memcpy(absolute, path.data(), size_t(path.size()));
  absolute[path.size()] = '\0';
  /* Blender's // prefix is relative even though POSIX also accepts a leading slash. */
  if (BLI_path_is_rel(absolute)) {
    std::string parent_key;
    if (!canonical_absolute(parent_private, parent_key)) {
      return false;
    }
    const BLOAssetSourcePaths::Pair *parent = nullptr;
    for (const BLOAssetSourcePaths::Pair &pair : paths->pairs) {
      if (BLI_path_cmp(parent_key.c_str(), pair.private_key.c_str()) == 0) {
        parent = &pair;
        break;
      }
    }
    if (parent == nullptr) {
      return false;
    }
    /* #BLI_path_abs joins what follows `//` to the parent's folder and cuts the result short at
     * FILE_MAX without saying so. A reference cut short has lost the end of its last name: it is
     * another name, and must not become a registered one. */
    const char *base = parent->original_key.c_str();
    const char *last_slash = BLI_path_slash_rfind(base);
    const size_t folder_len = last_slash ? size_t(last_slash - base) + 1 : 0;
    if (folder_len + size_t(path.size()) - 2 >= FILE_MAX) {
      return false;
    }
    /* Reads a reference written with either separator, on every platform. */
    BLI_path_abs(absolute, base);
  }
  else if (!absolute_anywhere(absolute)) {
    return false;
  }
  one_spelling(absolute);
  BLI_path_normalize(absolute);
  for (const BLOAssetSourcePaths::Pair &pair : paths->pairs) {
    if (BLI_path_cmp(absolute, pair.original_key.c_str()) == 0 ||
        BLI_path_cmp(absolute, pair.private_key.c_str()) == 0)
    {
      BLI_strncpy(resolved, pair.private_path.c_str(), FILE_MAX);
      return true;
    }
  }
  return false;
}

}  // namespace blender
