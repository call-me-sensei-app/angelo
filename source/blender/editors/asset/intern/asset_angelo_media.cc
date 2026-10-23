/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup edasset
 *
 * Angelo: Media Studio's items dragged like any asset, from every source, onto every editor that can take them.
 *
 * - #ASSET_OT_angelo_drag starts Blender's own asset drag for items of the Angelo Media Studio library (Media
 *   Studio's grid calls it): the item under the pointer as #WM_DRAG_ASSET and every selected item as
 *   #WM_DRAG_ASSET_LIST, exactly what the Asset Browser starts. Blender's own drops (a model under the pointer, a
 *   material on an object or a material slot) therefore behave the same from every source.
 * - Angelo's drop boxes take Media Studio items where Blender has no drop for them: an image on empty 3D space, in
 *   the Image Editor, in the chat and in Media Studio; several items at once in each of those; models and images in
 *   the Outliner (outliner_dragdrop.cc). Node editors are space_node's (node_add.cc). Each
 *   runs #ASSET_OT_angelo_media_drop, one undo step, which hands the drop to the add-on's `angelo.media_drop`.
 * - Where a kind makes no sense (a model in the Image Editor), #ASSET_OT_angelo_media_cannot_drop refuses with the
 *   reason, which Blender shows as the drop's tooltip: never a silent no-op.
 */

#include <algorithm>

#include <fmt/format.h>

#include "AS_asset_library.hh"
#include "AS_asset_representation.hh"

#include "BKE_context.hh"
#include "BKE_library.hh"
#include "BKE_preferences.h"
#include "BKE_report.hh"
#include "BKE_wm_runtime.hh"

#include "BLI_listbase.h"
#include "BLI_math_vector_types.hh"
#include "BLI_string.h"
#include "BLI_uuid.h"

#include "BLT_translation.hh"

#include "DNA_asset_types.h"
#include "DNA_object_types.h"
#include "DNA_space_types.h"
#include "DNA_userdef_types.h"

#include "ED_asset.hh"
#include "ED_asset_angelo_media.hh"
#include "ED_asset_list.hh"
#include "ED_view3d.hh"

#include "RNA_access.hh"
#include "RNA_define.hh"

#include "WM_api.hh"
#include "WM_types.hh"

namespace blender::ed::asset::angelo_media {

/* Media Studio's two catalogues: `studio_assets.CATALOGS` in the Angelo add-on writes them, and their ids are fixed
 * for good. A Media Studio asset is recognized by them, whatever the person renamed the library to. */
static const char *const MODELS_CATALOG = "5c0e3e1a-7d0b-4c8e-9a51-0f6a2d3b7e11";
static const char *const IMAGES_CATALOG = "9b2f6c47-1e83-4d5a-b7c2-3a8e4f1d6c22";
static const char *const PYTHON_DROP = "ANGELO_OT_media_drop";
/* Drops that change only the interface (the chat's attachments, Media Studio's references): no undo step. */
static const char *const PYTHON_DROP_UI = "ANGELO_OT_media_drop_ui";
static const char *const DROP_OP = "ASSET_OT_angelo_media_drop";
static const char *const REFUSE_OP = "ASSET_OT_angelo_media_cannot_drop";

static bool catalog_is(const AssetMetaData &meta, const char *text)
{
  bUUID uuid;
  return BLI_uuid_parse_string(&uuid, text) && BLI_uuid_equal(meta.catalog_id, uuid);
}

static std::optional<Kind> kind_of(const asset_system::AssetRepresentation *asset)
{
  if (asset == nullptr || asset->is_local_id()) {
    return std::nullopt;
  }
  const AssetMetaData &meta = asset->get_metadata();
  if (catalog_is(meta, MODELS_CATALOG) && ELEM(asset->get_id_type(), ID_OB, ID_GR)) {
    return Kind::Model;
  }
  if (catalog_is(meta, IMAGES_CATALOG) && asset->get_id_type() == ID_MA) {
    return Kind::Image;
  }
  return std::nullopt;
}

std::optional<DragItems> drag_items(const wmDrag &drag)
{
  DragItems items;
  bool models = false, images = false;
  auto add = [&](const asset_system::AssetRepresentation *asset) {
    const std::optional<Kind> kind = kind_of(asset);
    if (!kind) {
      return false;
    }
    items.assets.append(asset);
    models |= *kind == Kind::Model;
    images |= *kind == Kind::Image;
    return true;
  };
  if (drag.type == WM_DRAG_ASSET) {
    if (!add(static_cast<const wmDragAsset *>(drag.poin)->asset)) {
      return std::nullopt;
    }
  }
  else if (drag.type == WM_DRAG_ASSET_LIST) {
    for (const wmDragAssetListItem &item : *WM_drag_asset_list_get(&drag)) {
      if (!item.is_external || !add(item.asset_data.external_info->asset)) {
        return std::nullopt;
      }
    }
  }
  if (items.assets.is_empty()) {
    return std::nullopt;
  }
  items.kind = models && images ? Kind::Mixed : (models ? Kind::Model : Kind::Image);
  return items;
}

bool is_absorbed(const bContext &C, const wmDrag &drag)
{
  if (drag.type != WM_DRAG_ASSET || !drag_items(drag)) {
    return false;
  }
  const asset_system::AssetRepresentation *asset = static_cast<const wmDragAsset *>(drag.poin)->asset;
  for (const wmDrag &other : CTX_wm_manager(&C)->runtime->drags) {
    if (other.type != WM_DRAG_ASSET_LIST) {
      continue;
    }
    const std::optional<DragItems> list = drag_items(other);
    if (list && list->assets.size() >= 2 && list->assets.contains(asset)) {
      return true;
    }
  }
  return false;
}

/** A single item not absorbed by its list, or a list of two or more. */
static std::optional<DragItems> droppable(const bContext &C, const wmDrag &drag)
{
  std::optional<DragItems> items = drag_items(drag);
  if (!items) {
    return std::nullopt;
  }
  if (drag.type == WM_DRAG_ASSET_LIST ? items->assets.size() < 2 : is_absorbed(C, drag)) {
    return std::nullopt;
  }
  return items;
}

std::string slashes(const char *text)
{
  std::string result = text ? text : "";
  std::replace(result.begin(), result.end(), '\\', '/');
  return result;
}

void drop_props_set(PointerRNA &ptr, const DragItems &items, const char *target)
{
  std::string identifiers;
  for (const asset_system::AssetRepresentation *asset : items.assets) {
    const AssetWeakReference weak_ref = asset->make_weak_reference();
    identifiers += slashes(weak_ref.relative_asset_identifier) + "\n";
  }
  const AssetWeakReference first = items.assets.first()->make_weak_reference();
  RNA_string_set(&ptr, "library", first.asset_library_identifier ? first.asset_library_identifier : "");
  RNA_string_set(&ptr, "items", identifiers.c_str());
  RNA_string_set(&ptr, "target", target);
  /* A drop box's properties outlive its drop: what the last drop was over must never reach the next one. */
  for (const char *name : {"object", "collection", "into"}) {
    RNA_string_set(&ptr, name, "");
  }
}

static const char *refusal = "";

void refuse(const char *reason)
{
  refusal = reason;
}

void refuse_outliner(const Kind kind, const bool linked_collection)
{
  if (linked_collection) {
    refuse(TIP_("A linked collection can't take new objects"));
    return;
  }
  refuse(kind == Kind::Mixed ? TIP_("Drop models or images here, not both at once") :
                               TIP_("Drop an image on an object to give it the material"));
}

/* -------------------------------------------------------------------- */
/** \name Drop: hand the drop to the add-on, as one undo step
 * \{ */

static bool media_drop_poll(bContext *C)
{
  if (WM_operatortype_find(PYTHON_DROP, true) == nullptr) {
    CTX_wm_operator_poll_msg_set(C, TIP_("Turn on the Angelo add-on to drop Media Studio items"));
    return false;
  }
  return true;
}

static wmOperatorStatus media_drop_call(bContext *C, wmOperator *op, const wmEvent *event)
{
  if (RNA_boolean_get(op->ptr, "absorbed")) {
    return OPERATOR_CANCELLED; /* The list drop of the same drag places it. */
  }
  const std::string target = RNA_string_get(op->ptr, "target");
  wmOperatorType *ot = WM_operatortype_find(ELEM(target, "CHAT", "STUDIO") ? PYTHON_DROP_UI : PYTHON_DROP, true);
  if (ot == nullptr) {
    return OPERATOR_CANCELLED;
  }
  PointerRNA ptr = WM_operator_properties_create_ptr(ot);
  for (const char *name : {"target", "library", "items", "object", "collection", "into"}) {
    const std::string value = RNA_string_get(op->ptr, name);
    RNA_string_set(&ptr, name, value.c_str());
  }
  /* Nested: the add-on's operator and everything it calls are this operator's one undo step. */
  const wmOperatorStatus result = WM_operator_name_call_ptr(
      C, ot, event ? wm::OpCallContext::InvokeDefault : wm::OpCallContext::ExecDefault, &ptr, event);
  WM_operator_properties_free(&ptr);
  return (result & OPERATOR_FINISHED) ? OPERATOR_FINISHED : OPERATOR_CANCELLED;
}

static wmOperatorStatus media_drop_invoke(bContext *C, wmOperator *op, const wmEvent *event)
{
  return media_drop_call(C, op, event);
}

static wmOperatorStatus media_drop_exec(bContext *C, wmOperator *op)
{
  return media_drop_call(C, op, nullptr);
}

static void ASSET_OT_angelo_media_drop(wmOperatorType *ot)
{
  ot->name = "Drop Media Studio Items";
  ot->description = "Add Media Studio's items where they were dropped";
  ot->idname = DROP_OP;

  ot->invoke = media_drop_invoke;
  ot->exec = media_drop_exec;
  ot->poll = media_drop_poll;

  /* No undo step of its own: `angelo.media_drop` is the drop's one step, and a drop that only changes the interface
   * (the chat, Media Studio's references) pushes none. */
  ot->flag = OPTYPE_INTERNAL;

  for (const char *name : {"target", "library", "items", "object", "collection", "into"}) {
    PropertyRNA *prop = RNA_def_string(ot->srna, name, nullptr, 0, name, "");
    RNA_def_property_flag(prop, PropertyFlag(PROP_HIDDEN | PROP_SKIP_SAVE));
  }
  PropertyRNA *prop = RNA_def_boolean(ot->srna, "absorbed", false, "Absorbed", "");
  RNA_def_property_flag(prop, PropertyFlag(PROP_HIDDEN | PROP_SKIP_SAVE));
}

static bool cannot_drop_poll(bContext *C)
{
  CTX_wm_operator_poll_msg_set(C, refusal);
  return false;
}

static wmOperatorStatus cannot_drop_exec(bContext * /*C*/, wmOperator * /*op*/)
{
  return OPERATOR_CANCELLED;
}

static void ASSET_OT_angelo_media_cannot_drop(wmOperatorType *ot)
{
  ot->name = "Can't Drop Media Studio Items Here";
  ot->description = "Media Studio's items of this kind can't be dropped here";
  ot->idname = REFUSE_OP;

  ot->exec = cannot_drop_exec;
  ot->poll = cannot_drop_poll;

  ot->flag = OPTYPE_INTERNAL;
}

/** \} */

void operatortypes()
{
  WM_operatortype_append(ASSET_OT_angelo_drag);   /* asset_angelo_drag.cc */
  WM_operatortype_append(ASSET_OT_angelo_media_drop);
  WM_operatortype_append(ASSET_OT_angelo_media_cannot_drop);
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name Drop boxes
 * \{ */

bool absorb_poll(bContext *C, wmDrag *drag, const wmEvent * /*event*/)
{
  return is_absorbed(*C, *drag);
}

void absorb_copy(bContext * /*C*/, wmDrag * /*drag*/, wmDropBox *drop)
{
  RNA_boolean_set(drop->ptr, "absorbed", true);
}

std::string absorb_tooltip(bContext * /*C*/, wmDrag * /*drag*/, const int /*xy*/[2], wmDropBox * /*drop*/)
{
  return ""; /* The list's tooltip says what the drop adds. */
}

std::string drop_tooltip(bContext * /*C*/, wmDrag *drag, const int /*xy*/[2], wmDropBox * /*drop*/)
{
  const std::optional<DragItems> items = drag_items(*drag);
  if (!items || items->assets.size() < 2) {
    return fmt::format(fmt::runtime(TIP_("Add \"{}\"")), WM_drag_get_item_name(drag));
  }
  return fmt::format(fmt::runtime(TIP_("Add {} Media Studio items")), items->assets.size());
}

static wmDropBox *add(ListBaseT<wmDropBox> *lb,
                      const char *idname,
                      bool (*poll)(bContext *, wmDrag *, const wmEvent *),
                      void (*copy)(bContext *, wmDrag *, wmDropBox *),
                      WMDropboxTooltipFunc tooltip,
                      const bool first)
{
  wmDropBox *drop = WM_dropbox_add(lb, idname, poll, copy, nullptr, tooltip);
  if (drop && first) {
    BLI_remlink(lb, drop);
    BLI_addhead(lb, drop);
  }
  return drop;
}

static void copy_target(wmDropBox *drop, wmDrag *drag, const char *target)
{
  if (const std::optional<DragItems> items = drag_items(*drag)) {
    drop_props_set(*drop->ptr, *items, target);
  }
}

/** The pointer in \a C's region, from the window's last event (drop box copy callbacks get no event). */
static int2 region_mval(bContext *C)
{
  const wmWindow *win = CTX_wm_window(C);
  const ARegion *region = CTX_wm_region(C);
  if (win == nullptr || region == nullptr || win->runtime->eventstate == nullptr) {
    return int2(0);
  }
  return int2(win->runtime->eventstate->xy[0] - region->winrct.xmin, win->runtime->eventstate->xy[1] - region->winrct.ymin);
}

/* 3D View: an image on empty space (a reference image), and several items at once. */

/** The object under the pointer that an image's material can go on, or null (empty space, an Empty, a camera, a
 * light, linked data): Blender's own material drop takes such an object, Angelo's reference image the rest. */
static const Object *material_target(bContext *C, const int mval[2])
{
  const Object *ob = ED_view3d_give_object_under_cursor(C, mval);
  if (ob && OB_TYPE_SUPPORT_MATERIAL(ob->type) && ID_IS_EDITABLE(&ob->id) && !ID_IS_OVERRIDE_LIBRARY(&ob->id)) {
    return ob;
  }
  return nullptr;
}

static bool view3d_image_poll(bContext *C, wmDrag *drag, const wmEvent *event)
{
  if (drag->type != WM_DRAG_ASSET) {
    return false;
  }
  const std::optional<DragItems> items = droppable(*C, *drag);
  return items && items->kind == Kind::Image && material_target(C, event->mval) == nullptr;
}

static bool view3d_list_poll(bContext *C, wmDrag *drag, const wmEvent * /*event*/)
{
  return drag->type == WM_DRAG_ASSET_LIST && droppable(*C, *drag);
}

static void view3d_copy(bContext *C, wmDrag *drag, wmDropBox *drop)
{
  copy_target(drop, drag, "VIEW3D");
  const int2 mval = region_mval(C);
  if (const Object *ob = material_target(C, mval)) {
    RNA_string_set(drop->ptr, "object", ob->id.name + 2);
  }
}

/* Image Editor: images open. */

static bool image_poll(bContext *C, wmDrag *drag, const wmEvent * /*event*/)
{
  const std::optional<DragItems> items = droppable(*C, *drag);
  return items && items->kind == Kind::Image;
}

static void image_copy(bContext * /*C*/, wmDrag *drag, wmDropBox *drop)
{
  copy_target(drop, drag, "IMAGE");
}

static bool image_refuse_poll(bContext *C, wmDrag *drag, const wmEvent * /*event*/)
{
  const std::optional<DragItems> items = droppable(*C, *drag);
  if (!items || items->kind == Kind::Image) {
    return false;
  }
  refuse(TIP_("Only images open in the Image Editor"));
  return true;
}

/* Angelo's console editors: the chat attaches, Media Studio takes images as references. */

static StringRefNull console_language(bContext *C)
{
  const SpaceConsole *console = CTX_wm_space_console(C);
  return console ? StringRefNull(console->language) : StringRefNull("");
}

static bool console_absorb_poll(bContext *C, wmDrag *drag, const wmEvent *event)
{
  return ELEM(console_language(C), "angelo", "angelo_genai") && absorb_poll(C, drag, event);
}

static bool console_poll(bContext *C, wmDrag *drag, const wmEvent * /*event*/)
{
  const std::optional<DragItems> items = droppable(*C, *drag);
  const StringRefNull language = console_language(C);
  return items && (language == "angelo" || (language == "angelo_genai" && items->kind == Kind::Image));
}

static void console_copy(bContext *C, wmDrag *drag, wmDropBox *drop)
{
  copy_target(drop, drag, console_language(C) == "angelo" ? "CHAT" : "STUDIO");
}

static bool console_refuse_poll(bContext *C, wmDrag *drag, const wmEvent * /*event*/)
{
  const std::optional<DragItems> items = droppable(*C, *drag);
  if (!items || items->kind == Kind::Image || console_language(C) != "angelo_genai") {
    return false;
  }
  refuse(TIP_("Media Studio takes images as references, not models"));
  return true;
}

/**
 * On release Blender runs only the first drop box, in list order, that takes any of the drags, then frees every drag.
 * So in each editor Angelo's boxes come in this order: the list (two or more items, from Media Studio's selection or
 * the Asset Browser's), then the single item, then the refusals, then #absorb_poll (it only hides the single half's
 * hover tooltip while the list's shows: at release the list's box has already run), then the editor's own boxes.
 */
void dropboxes()
{
  ListBaseT<wmDropBox> *lb = WM_dropboxmap_find("View3D", SPACE_VIEW3D, RGN_TYPE_WINDOW);
  /* Added first, in reverse: the list, the single image (before Blender's material drop, which takes any object but
   * gives an Empty, a camera or a light nothing; on an object that takes materials it is Blender's own drop), then
   * the single half's tooltip-only box. */
  add(lb, DROP_OP, absorb_poll, absorb_copy, absorb_tooltip, true);
  add(lb, DROP_OP, view3d_image_poll, view3d_copy, drop_tooltip, true);
  add(lb, DROP_OP, view3d_list_poll, view3d_copy, drop_tooltip, true);

  /* Node editors are space_node's own (node_add.cc): Flow Image steps and image nodes from Media Studio's drags. */

  lb = WM_dropboxmap_find("Image", SPACE_IMAGE, RGN_TYPE_WINDOW);
  add(lb, DROP_OP, image_poll, image_copy, drop_tooltip, false);
  add(lb, REFUSE_OP, image_refuse_poll, nullptr, nullptr, false);
  add(lb, DROP_OP, absorb_poll, absorb_copy, absorb_tooltip, false);

  /* First, so they come before the Console's own drops (which insert the item's name as text); added in reverse. */
  lb = WM_dropboxmap_find("Console", SPACE_CONSOLE, RGN_TYPE_WINDOW);
  add(lb, DROP_OP, console_absorb_poll, absorb_copy, absorb_tooltip, true);
  add(lb, REFUSE_OP, console_refuse_poll, nullptr, nullptr, true);
  add(lb, DROP_OP, console_poll, console_copy, drop_tooltip, true);
}

/** \} */

}  // namespace blender::ed::asset::angelo_media
