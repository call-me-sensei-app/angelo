/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup edasset
 *
 * Angelo: Media Studio's items as assets everywhere. One drag for every source (Media Studio's grid starts Blender's
 * own asset drag, as the Asset Browser and the Asset Shelf do) and Angelo's drop boxes for the targets Blender has
 * none for. A Media Studio asset is known by its catalogue: the two fixed ids `studio_assets.CATALOGS` writes.
 */

#pragma once

#include <optional>
#include <string>

#include "BLI_vector.hh"

namespace blender {
struct bContext;
struct PointerRNA;
struct wmDrag;
struct wmDropBox;
struct wmEvent;
struct wmOperatorType;
}  // namespace blender

namespace blender::asset_system {
class AssetRepresentation;
}

namespace blender::ed::asset::angelo_media {

enum class Kind { Model, Image, Mixed };

/** The Media Studio assets a drag holds (one asset, or a list), or none when any of them is not Media Studio's. */
struct DragItems {
  Vector<const asset_system::AssetRepresentation *> assets;
  Kind kind = Kind::Mixed;
};

std::optional<DragItems> drag_items(const wmDrag &drag);

/**
 * Whether \a drag is the single-asset half of a multi-asset drag (Blender starts both from the Asset Browser, and
 * Media Studio's grid does the same): the list's drop places every item, so this one adds nothing, keeping one undo
 * step per drop.
 */
bool is_absorbed(const bContext &C, const wmDrag &drag);

/** Fill a drop box's #ASSET_OT_angelo_media_drop properties. */
void drop_props_set(PointerRNA &ptr, const DragItems &items, const char *target);

/** For a drop box that refuses: the reason the "can't drop here" tooltip shows (translated, static). */
void refuse(const char *reason);
/** The Outliner's reason for refusing \a kind where it was dropped (outliner_dragdrop.cc): a model on a linked
 * collection's row, or an image off an object. */
void refuse_outliner(Kind kind, bool linked_collection);

/* Drop box callbacks the other editors' drop boxes share (Outliner). */
bool absorb_poll(bContext *C, wmDrag *drag, const wmEvent *event);
void absorb_copy(bContext *C, wmDrag *drag, wmDropBox *drop);
std::string absorb_tooltip(bContext *C, wmDrag *drag, const int xy[2], wmDropBox *drop);
std::string drop_tooltip(bContext *C, wmDrag *drag, const int xy[2], wmDropBox *drop);

/** A relative asset identifier with `/` separators on every platform. */
std::string slashes(const char *text);

void ASSET_OT_angelo_drag(wmOperatorType *ot);
void operatortypes();
/** Add Angelo's drop boxes (after every editor's own: some are put first, to come before them). */
void dropboxes();

}  // namespace blender::ed::asset::angelo_media
