/* SPDX-FileCopyrightText: 2023 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup edasset
 */

#pragma once

#include <memory>

namespace blender {

struct ARegion;
struct ARegionType;
struct AssetShelf;
struct AssetShelfSettings;
struct AssetShelfType;
struct BlendDataReader;
struct BlendWriter;
struct Main;
struct RegionPollParams;
struct ScrArea;
struct bContext;
struct bContextDataResult;
struct wmRegionListenerParams;
struct wmRegionMessageSubscribeParams;
struct wmWindowManager;

class StringRef;
class StringRefNull;
namespace asset_system {
class AssetRepresentation;
}

namespace ed::asset::shelf {

/* -------------------------------------------------------------------- */
/** \name Asset Shelf Regions
 *
 * Naming conventions:
 * - #regions_xxx(): Applies to both regions (#RGN_TYPE_ASSET_SHELF and
 *   #RGN_TYPE_ASSET_SHELF_HEADER).
 * - #region_xxx(): Applies to the main shelf region (#RGN_TYPE_ASSET_SHELF).
 * - #header_region_xxx(): Applies to the shelf header region
 *   (#RGN_TYPE_ASSET_SHELF_HEADER).
 *
 * \{ */

bool regions_poll(const RegionPollParams *params);

/** Only needed for #RGN_TYPE_ASSET_SHELF (not #RGN_TYPE_ASSET_SHELF_HEADER). */
void *region_duplicate(void *regiondata);
void region_free(ARegion *region);
void region_init(wmWindowManager *wm, ARegion *region);
int region_snap(const ARegion *region, int size, int axis);
void region_on_user_resize(const ARegion *region);
void region_listen(const wmRegionListenerParams *params);
void region_message_subscribe(const wmRegionMessageSubscribeParams *params);
void region_layout(const bContext *C, ARegion *region);
void region_draw(const bContext *C, ARegion *region);
void region_on_poll_success(const bContext *C, ARegion *region);
void region_blend_read_data(BlendDataReader *reader, ARegion *region);
void region_blend_write(BlendWriter *writer, ARegion *region);
int region_prefsizey();

void header_region_init(wmWindowManager *wm, ARegion *region);
void header_region(const bContext *C, ARegion *region);
void header_region_listen(const wmRegionListenerParams *params);
int header_region_size();
void types_register(ARegionType *region_type, const int space_type);

/** \} */

/* -------------------------------------------------------------------- */
/** \name Asset Shelf Type
 * \{ */

void type_register(std::unique_ptr<AssetShelfType> type);
void type_unregister(const AssetShelfType &shelf_type);
/**
 * Poll an asset shelf type for display as a popup. Doesn't check for space-type (the type's
 * #bl_space_type) since popups should ignore this to allow displaying in any space.
 *
 * Permanent/non-popup asset shelf regions should use #type_poll_for_space_type() instead.
 */
bool type_poll_for_popup(const bContext &C, const AssetShelfType *shelf_type);
bool type_asset_poll(const AssetShelfType &shelf_type,
                     const asset_system::AssetRepresentation &asset);
AssetShelfType *type_find_from_idname(StringRef idname);

/** \} */

/* -------------------------------------------------------------------- */
/** \name Asset Shelf Popup
 * \{ */

void type_popup_unlink(const AssetShelfType &shelf_type);
void ensure_asset_library_fetched(const bContext &C, const AssetShelfType &shelf_type);

/** \} */

/* -------------------------------------------------------------------- */

void type_unlink(const Main &bmain, const AssetShelfType &shelf_type);

int tile_width(const AssetShelfSettings &settings);
int tile_height(const AssetShelfSettings &settings);

AssetShelf *active_shelf_from_area(const ScrArea *area);

/**
 * Angelo: whether \a path is one of the shelf's catalog tabs, and adding it as one. Kept in the
 * Preferences or the shelf's own settings, as the shelf type stores enabled catalogs.
 */
bool catalog_path_enabled(const AssetShelf &shelf, StringRefNull path);
void enable_catalog_path(AssetShelf &shelf, StringRefNull path);
void disable_catalog_path(AssetShelf &shelf, StringRefNull path);
/** Show the tab of \a path, or the "All" tab for an empty path. */
void set_active_catalog_path(AssetShelf &shelf, StringRefNull path);
/**
 * Angelo: the assets the shelf listed at its last build (after its asset poll and catalog tab, before the name
 * search), -1 before its first build; and whether its asset library has finished loading.
 */
int shown_count(const AssetShelf &shelf);
bool list_ready(const AssetShelf &shelf);
/**
 * Angelo: whether the shelf shows only its tab strip (#RGN_FLAG_COLLAPSED), and collapsing or
 * expanding the shelf region of \a screen that shows \a shelf. False when no region shows it.
 */
bool is_collapsed(const AssetShelf &shelf);
bool set_collapsed(bContext &C, bScreen &screen, AssetShelf &shelf, bool collapsed);

/**
 * Enable catalog path in all shelves visible in all windows.
 */
void show_catalog_in_visible_shelves(const bContext &C, const StringRefNull catalog_path);

int context(const bContext *C, const char *member, bContextDataResult *result);

}  // namespace ed::asset::shelf
}  // namespace blender
