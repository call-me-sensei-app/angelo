/* SPDX-FileCopyrightText: 2026 Angelo Contributors
 * SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once

#include <cstdint>

namespace blender {
struct Main;

namespace ed::undo::asset {

/** Brushes deliberately survive ordinary memfile Undo. This adjunct owns only the creation
 * delta of an asset import: native IDs absent at begin, never pre-existing Brushes/dependencies.
 * Templates use stock ID copy/free and UID references, not pointers into a replaced Main.
 * Main-thread only; no provider, Python references, file loading, or allocation-proof authority. */
struct AssetNoUndoDelta;
enum class AssetNoUndoSide : uint8_t { Before = 0, After = 1 };

/** One owned reference; capture before the importer runs. Failure has no Main effects. */
AssetNoUndoDelta *asset_noundo_delta_prepare(Main &, const char **r_error);
AssetNoUndoDelta *asset_noundo_delta_retain(AssetNoUndoDelta *);
/** Release a known owner reference. An uncertain native Main insertion retains its entire
 * prepublished recovery batch even at the last release; this is not a cleanup-success signal. */
void asset_noundo_delta_free(AssetNoUndoDelta *);

/** Capture actual newly introduced Brushes and their new retained dependencies. Build complete
 * independent templates before replacing the prior plan; failed capture remains usable for
 * an actual Before rollback, or explicitly refuses if it cannot establish that deletion set. */
bool asset_noundo_delta_capture_after(AssetNoUndoDelta &, Main &, const char **r_error);

/** Called by the checked memfile decoder after preview/pool cancellation and before decoding.
 * Before removes only introduced no-undo IDs; After recreates the stock-copy templates so
 * Blender's existing UID remapping can resolve the target snapshot. False is not a rollback. */
bool asset_noundo_delta_before_decode(AssetNoUndoDelta &,
                                      Main &,
                                      AssetNoUndoSide,
                                      const char **r_error);

/** Rebind/validate actual current target facts before final editor delivery. Missing required
 * IDs or incomplete adjunct effects are a checked decode failure, never a global-Undo claim. */
bool asset_noundo_delta_after_decode(AssetNoUndoDelta &,
                                     Main &,
                                     AssetNoUndoSide,
                                     const char **r_error);

}  // namespace ed::undo::asset
}  // namespace blender
