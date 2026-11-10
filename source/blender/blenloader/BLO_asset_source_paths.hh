/* SPDX-FileCopyrightText: 2026 Angelo Contributors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once

#include <memory>

#include "BLI_span.hh"
#include "BLI_string_ref.hh"

namespace blender {

struct BLOAssetSourcePathPair {
  StringRef original_absolute;
  StringRef private_absolute;
};

/** Immutable, copied routing facts; neither an import grant nor file identity evidence. */
class BLOAssetSourcePaths;
std::shared_ptr<const BLOAssetSourcePaths> BLO_asset_source_paths_create(
    Span<BLOAssetSourcePathPair> pairs, const char **error);

/** One actual asset scope on this thread. Empty maps still refuse all source opens. */
bool BLO_asset_source_paths_activate(std::shared_ptr<const BLOAssetSourcePaths> paths,
                                     const char **error);
void BLO_asset_source_paths_deactivate(const BLOAssetSourcePaths &paths) noexcept;
std::shared_ptr<const BLOAssetSourcePaths> BLO_asset_source_paths_current();

/** Resolve against the ORIGINAL parent of a private asset file, before library identity lookup.
 * Caller supplies a FILE_MAX buffer. A retained reader must still match the active asset scope.
 * No disk access, path fallback, or mutation of serialized Library paths is performed. */
bool BLO_asset_source_path_resolve(const std::shared_ptr<const BLOAssetSourcePaths> &paths,
                                   StringRef path,
                                   StringRef parent_private,
                                   char *resolved);

}  // namespace blender
