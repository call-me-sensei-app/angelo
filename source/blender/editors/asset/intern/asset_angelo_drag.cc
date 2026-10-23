/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup edasset
 *
 * Angelo: #ASSET_OT_angelo_drag, Blender's own asset drag for items of the Angelo Media Studio library (Media
 * Studio's grid calls it). The item under the pointer as #WM_DRAG_ASSET and every selected item as
 * #WM_DRAG_ASSET_LIST, exactly what the Asset Browser starts, so every drop behaves as it does from there
 * (asset_angelo_media.cc has the drop boxes).
 */

#include <algorithm>

#include "AS_asset_library.hh"
#include "AS_asset_representation.hh"

#include "BKE_context.hh"
#include "BKE_preferences.h"
#include "BKE_report.hh"

#include "BLI_string_ref.hh"

#include "BLT_translation.hh"

#include "DNA_asset_types.h"
#include "DNA_userdef_types.h"

#include "ED_asset.hh"
#include "ED_asset_angelo_media.hh"
#include "ED_asset_list.hh"

#include "RNA_access.hh"
#include "RNA_define.hh"

#include "UI_resources.hh"

#include "WM_api.hh"
#include "WM_types.hh"

namespace blender::ed::asset::angelo_media {

/* -------------------------------------------------------------------- */
/** \name Drag: Blender's asset drag for Media Studio's items
 * \{ */

static std::optional<AssetLibraryReference> library_reference(const std::string &name)
{
  const bUserAssetLibrary *library = BKE_preferences_asset_library_find_by_name(&U, name.c_str());
  if (library == nullptr) {
    return std::nullopt;
  }
  AssetLibraryReference reference{};
  reference.type = ASSET_LIBRARY_CUSTOM;
  reference.custom_library_index = BKE_preferences_asset_library_get_index(&U, library);
  return reference;
}

static Vector<std::string> lines(const std::string &text)
{
  Vector<std::string> result;
  size_t start = 0;
  while (start < text.size()) {
    size_t end = text.find('\n', start);
    if (end == std::string::npos) {
      end = text.size();
    }
    if (end > start) {
      result.append(text.substr(start, end - start));
    }
    start = end + 1;
  }
  return result;
}

/**
 * The assets of each item, in order. An item is the prefix of its asset file (`models/<id>.blend/`): the writer
 * chooses what the file holds (an Object or a Collection; one Material per picture), so every asset under the prefix
 * is the item's. Empty when any item has none.
 */
static Vector<asset_system::AssetRepresentation *> find_assets(const bContext &C,
                                                               const AssetLibraryReference &reference,
                                                               const Vector<std::string> &prefixes)
{
  Vector<asset_system::AssetRepresentation *> found;
  for (const int attempt : {0, 1}) {
    if (attempt == 1) {
      /* An item written since the list loaded (Media Studio writes one at once before its drag): read again. */
      list::clear(&reference, &C);
    }
    if (!list::is_loaded(&reference)) {
      list::storage_fetch_blocking(reference, C);
    }
    Vector<std::pair<std::string, asset_system::AssetRepresentation *>> listed;
    list::iterate(reference, [&](asset_system::AssetRepresentation &asset) {
      listed.append({slashes(asset.library_relative_identifier().c_str()), &asset});
      return true;
    });
    /* An item's pictures in their order ("name 2" before "name 10"): shorter identifiers first. */
    std::sort(listed.begin(), listed.end(), [](const auto &a, const auto &b) {
      return a.first.size() != b.first.size() ? a.first.size() < b.first.size() : a.first < b.first;
    });
    found.clear();
    bool complete = true;
    for (const std::string &prefix : prefixes) {
      bool any = false;
      for (const auto &[identifier, asset] : listed) {
        if (StringRef(identifier).startswith(prefix)) {
          found.append(asset);
          any = true;
        }
      }
      complete &= any;
    }
    if (complete) {
      return found;
    }
  }
  return {};
}

static wmOperatorStatus angelo_drag_invoke(bContext *C, wmOperator *op, const wmEvent * /*event*/)
{
  const std::string library_name = RNA_string_get(op->ptr, "library");
  const std::optional<AssetLibraryReference> reference = library_reference(library_name);
  if (RNA_boolean_get(op->ptr, "prefetch")) {
    if (reference) {
      list::storage_fetch(&*reference, C);
    }
    return OPERATOR_FINISHED;
  }
  const Vector<std::string> identifiers = lines(RNA_string_get(op->ptr, "items"));
  const Vector<asset_system::AssetRepresentation *> assets =
      reference && !identifiers.is_empty() ? find_assets(*C, *reference, identifiers) :
                                             Vector<asset_system::AssetRepresentation *>();
  if (!assets.is_empty()) {
    asset_system::AssetRepresentation *first = assets.first();
    AssetImportSettings settings{};
    settings.method = first->get_import_method().value_or(ASSET_IMPORT_APPEND);
    if (U.experimental.no_data_block_packing && settings.method == ASSET_IMPORT_PACK) {
      settings.method = ASSET_IMPORT_APPEND_REUSE;
    }
    first->ensure_previewable(*C);
    wmDrag *drag = WM_drag_data_create(
        C, ICON_NONE, WM_DRAG_ASSET, WM_drag_create_asset_data(first, settings), WM_DRAG_FREE_DATA);
    WM_event_drag_preview_icon(drag, asset_preview_or_icon(*first));
    WM_event_start_prepared_drag(C, drag);
    /* As the Asset Browser does: the selected items as a list, beside the item under the pointer. */
    wmDrag *list_drag = WM_drag_data_create(C, ICON_NONE, WM_DRAG_ASSET_LIST, nullptr, WM_DRAG_NOP);
    for (const asset_system::AssetRepresentation *asset : assets) {
      WM_drag_add_asset_list_item(list_drag, asset);
    }
    WM_event_start_prepared_drag(C, list_drag);
    return OPERATOR_FINISHED;
  }
  /* Not in the Asset Browser library (it is off, or the item is not written yet): Blender's file drag of Media
   * Studio's own files, so a drop still does what dropping those files does. */
  const Vector<std::string> paths = lines(RNA_string_get(op->ptr, "paths"));
  if (!paths.is_empty()) {
    Vector<const char *> raw;
    for (const std::string &path : paths) {
      raw.append(path.c_str());
    }
    WM_event_start_drag(C, ICON_FILE_BLANK, WM_DRAG_PATH, WM_drag_create_path_data(raw), WM_DRAG_FREE_DATA);
    return OPERATOR_FINISHED;
  }
  BKE_report(op->reports, RPT_WARNING, RPT_("This Media Studio item is not in the Asset Browser library yet"));
  return OPERATOR_CANCELLED;
}

void ASSET_OT_angelo_drag(wmOperatorType *ot)
{
  ot->name = "Drag Media Studio Items";
  ot->description = "Drag Media Studio's items as the Asset Browser drags assets";
  ot->idname = "ASSET_OT_angelo_drag";

  ot->invoke = angelo_drag_invoke;

  ot->flag = OPTYPE_INTERNAL;

  for (const char *name : {"library", "items", "paths"}) {
    PropertyRNA *prop = RNA_def_string(ot->srna, name, nullptr, 0, name, "");
    RNA_def_property_flag(prop, PropertyFlag(PROP_HIDDEN | PROP_SKIP_SAVE));
  }
  PropertyRNA *prop = RNA_def_boolean(ot->srna, "prefetch", false, "Prefetch", "");
  RNA_def_property_flag(prop, PropertyFlag(PROP_HIDDEN | PROP_SKIP_SAVE));
}

}  // namespace blender::ed::asset::angelo_media
