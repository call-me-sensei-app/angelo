/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup spapi
 *
 * Angelo: Media Studio's items over the Properties editor (1.0.5). Blender's own drops take what makes sense there:
 * an image (its material asset) on a material slot (`UI_OT_drop_material`), an item on a matching data-block field
 * (`UI_OT_drop_name`). Anything else is refused with the reason as the drag's red tooltip, never a silent no-op,
 * through Media Studio's shared refusal (`ED_asset_angelo_media.hh`: `refuse` and its "can't drop" operator).
 * Appended after the "User Interface" drops, so it speaks only when none of Blender's took the drag.
 */

#include "BKE_context.hh"

#include "BLT_translation.hh"

#include "WM_api.hh"
#include "WM_types.hh"

#include "ED_asset_angelo_media.hh"
#include "ED_space_api.hh"

namespace blender {

namespace {

namespace media = ed::asset::angelo_media;

constexpr const char *REFUSE_OP = "ASSET_OT_angelo_media_cannot_drop";

bool properties_refuse_poll(bContext *C, wmDrag *drag, const wmEvent * /*event*/)
{
  if (CTX_wm_space_properties(C) == nullptr) {
    return false;
  }
  const std::optional<media::DragItems> items = media::drag_items(*drag);
  if (!items || (drag->type == WM_DRAG_ASSET_LIST ? items->assets.size() < 2 : media::is_absorbed(*C, *drag))) {
    return false;
  }
  switch (items->kind) {
    case media::Kind::Model:
      media::refuse(TIP_("A 3D model isn't added here: drop it in the 3D View or the Outliner to add it"));
      break;
    case media::Kind::Image:
      media::refuse(TIP_("Drop an image on a material slot to use it there"));
      break;
    case media::Kind::Mixed:
      media::refuse(TIP_("Drop models or images here, not both at once"));
      break;
  }
  return true;
}

}  // namespace

void ED_dropboxes_angelo_studio_drop()
{
  ListBaseT<wmDropBox> *ui = WM_dropboxmap_find("User Interface", SPACE_EMPTY, RGN_TYPE_WINDOW);
  WM_dropbox_add(ui, REFUSE_OP, properties_refuse_poll, nullptr, nullptr, nullptr);
}

}  // namespace blender
