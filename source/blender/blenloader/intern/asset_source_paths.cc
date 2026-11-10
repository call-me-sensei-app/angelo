/* SPDX-FileCopyrightText: 2026 Angelo Contributors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */
#include "BLO_asset_source_paths.hh"

#include <cstring>
#include <string>
#include <vector>

#include "BLI_path_utils.hh"
#include "BLI_string.h"
#include "DNA_space_enums.h"

namespace blender {

class BLOAssetSourcePaths {
 public:
  struct Pair {
    std::string original;
    std::string private_path;
  };
  std::vector<Pair> pairs;
};

static thread_local std::shared_ptr<const BLOAssetSourcePaths> active_paths;

static bool canonical_absolute(const StringRef path)
{
  if (path.is_empty() || path.size() >= FILE_MAX ||
      std::memchr(path.data(), '\0', size_t(path.size())) != nullptr)
  {
    return false;
  }
  char normalized[FILE_MAX];
  memcpy(normalized, path.data(), size_t(path.size()));
  normalized[path.size()] = '\0';
  if (BLI_path_is_rel(normalized) || !BLI_path_is_abs_from_cwd(normalized)) {
    return false;
  }
  BLI_path_normalize(normalized);
  return path == normalized;
}

std::shared_ptr<const BLOAssetSourcePaths> BLO_asset_source_paths_create(
    const Span<BLOAssetSourcePathPair> pairs, const char **error)
{
  auto paths = std::make_shared<BLOAssetSourcePaths>();
  for (const BLOAssetSourcePathPair &pair : pairs) {
    if (!canonical_absolute(pair.original_absolute) || !canonical_absolute(pair.private_absolute)) {
      if (error) {
        *error = "Asset source paths must be canonical absolute paths within FILE_MAX";
      }
      return nullptr;
    }
    const std::string original = pair.original_absolute;
    const std::string private_path = pair.private_absolute;
    if (BLI_path_cmp(original.c_str(), private_path.c_str()) == 0) {
      if (error) {
        *error = "Asset source must have a distinct private copy";
      }
      return nullptr;
    }
    for (const BLOAssetSourcePaths::Pair &existing : paths->pairs) {
      if (BLI_path_cmp(existing.original.c_str(), original.c_str()) == 0 ||
          BLI_path_cmp(existing.private_path.c_str(), private_path.c_str()) == 0 ||
          BLI_path_cmp(existing.original.c_str(), private_path.c_str()) == 0 ||
          BLI_path_cmp(existing.private_path.c_str(), original.c_str()) == 0)
      {
        if (error) {
          *error = "Asset source paths contain an ambiguous original or private path";
        }
        return nullptr;
      }
    }
    paths->pairs.push_back({original, private_path});
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
    if (!canonical_absolute(parent_private)) {
      return false;
    }
    char parent_path[FILE_MAX];
    memcpy(parent_path, parent_private.data(), size_t(parent_private.size()));
    parent_path[parent_private.size()] = '\0';
    const BLOAssetSourcePaths::Pair *parent = nullptr;
    for (const BLOAssetSourcePaths::Pair &pair : paths->pairs) {
      if (BLI_path_cmp(parent_path, pair.private_path.c_str()) == 0) {
        parent = &pair;
        break;
      }
    }
    if (parent == nullptr) {
      return false;
    }
    BLI_path_abs(absolute, parent->original.c_str());
  }
  else if (!BLI_path_is_abs_from_cwd(absolute)) {
    return false;
  }
  BLI_path_normalize(absolute);
  for (const BLOAssetSourcePaths::Pair &pair : paths->pairs) {
    if (BLI_path_cmp(absolute, pair.original.c_str()) == 0 ||
        BLI_path_cmp(absolute, pair.private_path.c_str()) == 0)
    {
      BLI_strncpy(resolved, pair.private_path.c_str(), FILE_MAX);
      return true;
    }
  }
  return false;
}

}  // namespace blender
