/* SPDX-FileCopyrightText: 2023 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup spconsole
 */

#include <cstdio>
#include <cstring>
#include <utility>

#include "DNA_space_types.h"

#include "MEM_guardedalloc.h"

#include "BLI_listbase.h"
#include "BLI_map.hh"
#include "BLI_string.h"
#include "BLI_string_ref.hh"
#include "BLI_string_utf8.h"

#include "BKE_angelo_legacy_names.hh"
#include "BKE_context.hh"
#include "BKE_screen.hh"

#include "ED_screen.hh"
#include "ED_space_api.hh"
#include "ED_space_console.hh"

#include "RNA_access.hh"
#include "RNA_define.hh"
#include "RNA_path.hh"
#include "RNA_types.hh"

#include "WM_api.hh"
#include "WM_types.hh"

#include "UI_resources.hh"
#include "UI_view2d.hh"

#include "BLO_read_write.hh"

#include "BLT_translation.hh"

#include "console_intern.hh" /* own include */

namespace blender {

/* Alternate text buffers are runtime-only, like the native Python namespace.
 * Keep Python history and agent input separate without changing .blend DNA. */
static Map<SpaceConsole *, Map<int, SpaceConsole *>> console_alternate_buffers;

ConsoleLine *ED_console_coding_agent_input(SpaceConsole *console)
{
  SpaceConsole *buffer = console;
  if (!STREQ(console->language, "angelo")) {
    const Map<int, SpaceConsole *> *buffers = console_alternate_buffers.lookup_ptr(console);
    buffer = buffers ? buffers->lookup_default(1, nullptr) : nullptr;
  }
  return buffer ? static_cast<ConsoleLine *>(buffer->history.last) : nullptr;
}

/* ******************** default callbacks for console space ***************** */

static SpaceLink *console_create(const ScrArea * /*area*/, const Scene * /*scene*/)
{
  ARegion *region;
  SpaceConsole *sconsole;

  sconsole = MEM_new<SpaceConsole>("initconsole");
  sconsole->spacetype = SPACE_CONSOLE;

  sconsole->lheight = 14;

  /* header */
  region = BKE_area_region_new();

  BLI_addtail(&sconsole->regionbase, region);
  region->regiontype = RGN_TYPE_HEADER;
  region->alignment = (U.uiflag & USER_HEADER_BOTTOM) ? RGN_ALIGN_BOTTOM : RGN_ALIGN_TOP;

  /* main region */
  region = BKE_area_region_new();

  BLI_addtail(&sconsole->regionbase, region);
  region->regiontype = RGN_TYPE_WINDOW;

  /* keep in sync with info */
  region->v2d.scroll |= V2D_SCROLL_RIGHT | V2D_SCROLL_VERTICAL_HIDE;
  region->v2d.align |= V2D_ALIGN_NO_NEG_X | V2D_ALIGN_NO_NEG_Y; /* align bottom left */
  region->v2d.keepofs |= V2D_LOCKOFS_X;
  region->v2d.keepzoom = (V2D_LOCKZOOM_X | V2D_LOCKZOOM_Y | V2D_LIMITZOOM | V2D_KEEPASPECT);
  region->v2d.keeptot = V2D_KEEPTOT_BOUNDS;
  region->v2d.minzoom = region->v2d.maxzoom = 1.0f;

  /* for now, aspect ratio should be maintained, and zoom is clamped within sane default limits */
  // region->v2d.keepzoom = (V2D_KEEPASPECT|V2D_LIMITZOOM);

  return reinterpret_cast<SpaceLink *>(sconsole);
}

/* Doesn't free the space-link itself. */
static void console_free(SpaceLink *sl)
{
  SpaceConsole *sc = reinterpret_cast<SpaceConsole *>(sl);

  while (sc->scrollback.first) {
    console_scrollback_free(sc, static_cast<ConsoleLine *>(sc->scrollback.first));
  }

  while (sc->history.first) {
    console_history_free(sc, static_cast<ConsoleLine *>(sc->history.first));
  }
  if (Map<int, SpaceConsole *> *buffers = console_alternate_buffers.lookup_ptr(sc)) {
    for (SpaceConsole *buffer : buffers->values()) {
      console_free(reinterpret_cast<SpaceLink *>(buffer));
      MEM_delete(buffer);
    }
    console_alternate_buffers.remove(sc);
    if (console_alternate_buffers.is_empty()) {
      /* Release allocator-backed table storage before Blender's allocator
       * shuts down, not from this static map's process-exit destructor. */
      console_alternate_buffers.clear();
    }
  }
}

/* spacetype; init callback */
static void console_init(wmWindowManager * /*wm*/, ScrArea * /*area*/) {}

/* The native text surface is shared, not the user-visible editor choice.
 * CONSOLE remains Python; CODING_AGENT is a distinct Area.ui_type subtype,
 * listed in its own AI column by rna_Area_ui_type_itemf(). */
static int console_space_subtype_get(ScrArea *area)
{
  const SpaceConsole *console = static_cast<SpaceConsole *>(area->spacedata.first);
  return STREQ(console->language, "angelo_genai") ? 2 :
         STREQ(console->language, "angelo") ? 1 : 0;
}

static StringRefNull console_space_name_get(const ScrArea *area)
{
  const SpaceConsole *console = static_cast<const SpaceConsole *>(area->spacedata.first);
  return STREQ(console->language, "angelo_genai") ? IFACE_("Generative AI") :
         STREQ(console->language, "angelo") ? IFACE_("Coding Agent") : IFACE_("Python Console");
}

static int console_space_icon_get(const ScrArea *area)
{
  const SpaceConsole *console = static_cast<const SpaceConsole *>(area->spacedata.first);
  return STREQ(console->language, "angelo_genai") ? ICON_IMAGE :
         STREQ(console->language, "angelo") ? ICON_COMMUNITY : ICON_CONSOLE;
}

static void console_space_subtype_set(ScrArea *area, int value)
{
  SpaceConsole *console = static_cast<SpaceConsole *>(area->spacedata.first);
  const char *language = value == 2 ? "angelo_genai" : value == 1 ? "angelo" : "python";
  if (console->language[0] == '\0') {
    STRNCPY_UTF8(console->language, language);
    STRNCPY_UTF8(console->prompt, value == 1 ? "Ask > " : ">>> ");
    console->lheight = value == 1 ? 18 : 14;
    return;
  }
  if (!STREQ(console->language, language)) {
    /* A chat draft must never become an executable Python command. Swap whole
     * text buffers; the original console's region/Python namespace stays alive. */
    Map<int, SpaceConsole *> &buffers = console_alternate_buffers.lookup_or_add(console, {});
    /* A keyed empty buffer holds the current mode while another mode is active.
     * Three editors cannot share the old two-way alternate-buffer swap. */
    const int previous = console_space_subtype_get(area);
    SpaceConsole *saved = buffers.lookup_or_add_cb(previous, []() {
      return MEM_new<SpaceConsole>("console saved editor buffer");
    });
    SpaceConsole *alternate = buffers.lookup_or_add_cb(value, [value]() {
      SpaceConsole *buffer = MEM_new<SpaceConsole>("console alternate editor buffer");
      buffer->lheight = value == 1 ? 18 : 14;
      STRNCPY_UTF8(buffer->prompt, value == 1 ? "Ask > " : ">>> ");
      return buffer;
    });
    std::swap(console->scrollback, saved->scrollback);
    std::swap(console->history, saved->history);
    std::swap(console->prompt, saved->prompt);
    std::swap(console->lheight, saved->lheight);
    std::swap(console->history_index, saved->history_index);
    std::swap(console->sel_start, saved->sel_start);
    std::swap(console->sel_end, saved->sel_end);
    std::swap(console->scrollback, alternate->scrollback);
    std::swap(console->history, alternate->history);
    std::swap(console->prompt, alternate->prompt);
    std::swap(console->lheight, alternate->lheight);
    std::swap(console->history_index, alternate->history_index);
    std::swap(console->sel_start, alternate->sel_start);
    std::swap(console->sel_end, alternate->sel_end);
    STRNCPY_UTF8(console->language, language);
  }
}

static void console_space_subtype_item_extend(bContext * /*C*/,
                                              EnumPropertyItem **item,
                                              int *totitem)
{
  /* Coding Agent is added separately under AI, not mixed into Scripting. */
  static const EnumPropertyItem console_items[] = {
      {0, "CONSOLE", ICON_CONSOLE, "Python Console", "Interactive Python console"},
      {0, nullptr, 0, nullptr, nullptr},
  };
  RNA_enum_items_add(item, totitem, console_items);
}

static SpaceConsole *console_duplicate_input(SpaceConsole *source, const bool copy_input)
{
  SpaceConsole *duplicate = MEM_dupalloc(source);
  duplicate->scrollback.clear_no_delete();
  duplicate->history.clear_no_delete();
  if (copy_input) {
    /* Workspace changes may clone a floating editor. Preserve its unsent AI
     * input, not its transcript or sent history, and never share line storage. */
    if (ConsoleLine *line = static_cast<ConsoleLine *>(source->history.last)) {
      ConsoleLine *input = console_history_add_str(duplicate, line->line, false);
      input->cursor = line->cursor;
      input->type = line->type;
    }
    duplicate->history_index = 0;
    duplicate->sel_start = duplicate->sel_end = 0;
  }
  return duplicate;
}

static SpaceLink *console_duplicate(SpaceLink *sl)
{
  SpaceConsole *source = reinterpret_cast<SpaceConsole *>(sl);
  /* Python keeps the upstream behavior: its history and interpreter namespace
   * aren't duplicated. AI inputs are runtime-only and safe to copy separately. */
  SpaceConsole *duplicate = console_duplicate_input(
      source, STREQ(source->language, "angelo") || STREQ(source->language, "angelo_genai"));
  if (const Map<int, SpaceConsole *> *buffers = console_alternate_buffers.lookup_ptr(source)) {
    Map<int, SpaceConsole *> copied;
    for (const int subtype : {1, 2}) {
      if (SpaceConsole *buffer = buffers->lookup_default(subtype, nullptr)) {
        copied.add(subtype, console_duplicate_input(buffer, true));
      }
    }
    if (!copied.is_empty()) {
      console_alternate_buffers.add(duplicate, std::move(copied));
    }
  }
  return reinterpret_cast<SpaceLink *>(duplicate);
}

/* add handlers, stuff you only do once or on area/region changes */
static void console_main_region_init(wmWindowManager *wm, ARegion *region)
{
  wmKeyMap *keymap;
  ListBaseT<wmDropBox> *lb;

  const float prev_y_min = region->v2d.cur.ymin; /* so re-sizing keeps the cursor visible */

  view2d_region_reinit(&region->v2d, ui::V2D_COMMONVIEW_CUSTOM, region->winx, region->winy);

  /* always keep the bottom part of the view aligned, less annoying */
  if (prev_y_min != region->v2d.cur.ymin) {
    const float cur_y_range = BLI_rctf_size_y(&region->v2d.cur);
    region->v2d.cur.ymin = prev_y_min;
    region->v2d.cur.ymax = prev_y_min + cur_y_range;
  }

  /* own keymap */
  keymap = WM_keymap_ensure(wm->runtime->defaultconf, "Console", SPACE_CONSOLE, RGN_TYPE_WINDOW);
  WM_event_add_keymap_handler_v2d_mask(&region->runtime->handlers, keymap);

  /* Include after "Console" so cursor motion keys such as "Home" isn't overridden. */
  keymap = WM_keymap_ensure(
      wm->runtime->defaultconf, "View2D Buttons List", SPACE_EMPTY, RGN_TYPE_WINDOW);
  WM_event_add_keymap_handler(&region->runtime->handlers, keymap);

  /* add drop boxes */
  lb = WM_dropboxmap_find("Console", SPACE_CONSOLE, RGN_TYPE_WINDOW);

  WM_event_add_dropbox_handler(&region->runtime->handlers, lb);
}

/* same as 'text_cursor' */
static void console_cursor(wmWindow *win, ScrArea * /*area*/, ARegion *region)
{
  int wmcursor = WM_CURSOR_TEXT_EDIT;
  const wmEvent *event = win->runtime->eventstate;
  if (ui::view2d_mouse_in_scrollers(region, &region->v2d, event->xy)) {
    wmcursor = WM_CURSOR_DEFAULT;
  }

  WM_cursor_set(win, wmcursor);
}

/* ************* dropboxes ************* */

static bool console_drop_id_poll(bContext * /*C*/, wmDrag *drag, const wmEvent * /*event*/)
{
  return WM_drag_get_local_ID(drag, 0) != nullptr;
}

static void console_drop_id_copy(bContext * /*C*/, wmDrag *drag, wmDropBox *drop)
{
  ID *id = WM_drag_get_local_ID(drag, 0);

  /* copy drag path to properties */
  std::string text = RNA_path_full_ID_py(id);
  RNA_string_set(drop->ptr, "text", text.c_str());
}

static bool console_drop_path_poll(bContext *C, wmDrag *drag, const wmEvent * /*event*/)
{
  /* AI editors use the generic native FileHandler drop route, including multiple
   * files. Never insert a quoted path into its composer as if it were a prompt. */
  const SpaceConsole *console = CTX_wm_space_console(C);
  if (console && (STREQ(console->language, "angelo") || STREQ(console->language, "angelo_genai"))) {
    return false;
  }
  return (drag->type == WM_DRAG_PATH);
}

static void console_drop_path_copy(bContext * /*C*/, wmDrag *drag, wmDropBox *drop)
{
  char pathname[FILE_MAX + 2];
  SNPRINTF(pathname, "\"%s\"", WM_drag_get_single_path(drag));
  RNA_string_set(drop->ptr, "text", pathname);
}

static bool console_drop_string_poll(bContext * /*C*/, wmDrag *drag, const wmEvent * /*event*/)
{
  return (drag->type == WM_DRAG_STRING);
}

static void console_drop_string_copy(bContext * /*C*/, wmDrag *drag, wmDropBox *drop)
{
  /* NOTE(@ideasman42): Only a single line is supported, multiple lines could be supported
   * but this implies executing all lines except for the last. While we could consider that,
   * there are some security implications for this, so just drop one line for now. */
  std::string str = WM_drag_get_string_firstline(drag);
  RNA_string_set(drop->ptr, "text", str.c_str());
}

/* this region dropbox definition */
static void console_dropboxes()
{
  ListBaseT<wmDropBox> *lb = WM_dropboxmap_find("Console", SPACE_CONSOLE, RGN_TYPE_WINDOW);

  WM_dropbox_add(
      lb, "CONSOLE_OT_insert", console_drop_id_poll, console_drop_id_copy, nullptr, nullptr);
  WM_dropbox_add(
      lb, "CONSOLE_OT_insert", console_drop_path_poll, console_drop_path_copy, nullptr, nullptr);
  WM_dropbox_add(lb,
                 "CONSOLE_OT_insert",
                 console_drop_string_poll,
                 console_drop_string_copy,
                 nullptr,
                 nullptr);
}

/* ************* end drop *********** */

static void console_main_region_draw(const bContext *C, ARegion *region)
{
  /* draw entirely, view changes should be handled here */
  SpaceConsole *sc = CTX_wm_space_console(C);
  View2D *v2d = &region->v2d;

  if (sc->scrollback.is_empty()) {
    WM_operator_name_call(const_cast<bContext *>(C),
                          "CONSOLE_OT_banner",
                          wm::OpCallContext::ExecDefault,
                          nullptr,
                          nullptr);
  }

  /* clear and setup matrix */
  ui::theme::frame_buffer_clear(TH_BACK);

  /* Works best with no view2d matrix set. */
  ui::view2d_view_ortho(v2d);

  /* data... */

  console_history_verify(C); /* make sure we have some command line */
  console_textview_main(sc, region);

  /* reset view matrix */
  ui::view2d_view_restore(C);

  /* scrollers */
  ui::view2d_scrollers_draw(v2d, nullptr);
}

static void console_operatortypes()
{
  /* `console_ops.cc` */
  WM_operatortype_append(CONSOLE_OT_move);
  WM_operatortype_append(CONSOLE_OT_delete);
  WM_operatortype_append(CONSOLE_OT_insert);

  WM_operatortype_append(CONSOLE_OT_indent);
  WM_operatortype_append(CONSOLE_OT_indent_or_autocomplete);
  WM_operatortype_append(CONSOLE_OT_unindent);

  /* for use by python only */
  WM_operatortype_append(CONSOLE_OT_history_append);
  WM_operatortype_append(CONSOLE_OT_scrollback_append);

  WM_operatortype_append(CONSOLE_OT_clear);
  WM_operatortype_append(CONSOLE_OT_clear_line);
  WM_operatortype_append(CONSOLE_OT_history_cycle);
  WM_operatortype_append(CONSOLE_OT_copy);
  WM_operatortype_append(CONSOLE_OT_paste);
  WM_operatortype_append(CONSOLE_OT_select_set);
  WM_operatortype_append(CONSOLE_OT_select_all);
  WM_operatortype_append(CONSOLE_OT_select_word);
}

static void console_keymap(wmKeyConfig *keyconf)
{
  WM_keymap_ensure(keyconf, "Console", SPACE_CONSOLE, RGN_TYPE_WINDOW);
}

/****************** header region ******************/

/* add handlers, stuff you only do once or on area/region changes */
static void console_header_region_init(wmWindowManager * /*wm*/, ARegion *region)
{
  ED_region_header_init(region);
}

static void console_header_region_draw(const bContext *C, ARegion *region)
{
  ED_region_header(C, region);
}

static void console_main_region_listener(const wmRegionListenerParams *params)
{
  ScrArea *area = params->area;
  ARegion *region = params->region;
  const wmNotifier *wmn = params->notifier;

  /* context changes */
  switch (wmn->category) {
    case NC_SPACE: {
      if (wmn->data == ND_SPACE_CONSOLE) {
        if (wmn->action == NA_EDITED) {
          if ((wmn->reference && area) && (wmn->reference == area->spacedata.first)) {
            /* we've modified the geometry (font size), re-calculate rect */
            console_textview_update_rect(static_cast<SpaceConsole *>(wmn->reference), region);
            ED_region_tag_redraw(region);
          }
        }
        else {
          /* generic redraw request */
          ED_region_tag_redraw(region);
        }
      }
      break;
    }
  }
}

static void console_blend_read_data(BlendDataReader *reader, SpaceLink *sl)
{
  SpaceConsole *sconsole = reinterpret_cast<SpaceConsole *>(sl);

  /* AI editors saved before the product rename use their pre-rename language. */
  if (const char *language = BKE_angelo_legacy_console_language(sconsole->language)) {
    STRNCPY_UTF8(sconsole->language, language);
  }

  BLO_read_struct_list(reader, ConsoleLine, &sconsole->scrollback);
  BLO_read_struct_list(reader, ConsoleLine, &sconsole->history);

  /* Comma expressions, (e.g. expr1, expr2, expr3) evaluate each expression,
   * from left to right.  the right-most expression sets the result of the comma
   * expression as a whole. */
  for (ConsoleLine &cl : sconsole->history.items_mutable()) {
    if (BLO_read_array(reader, &cl.line, size_t(cl.len) + 1) && cl.line) {
      /* The allocated length is not written, so reset here. */
      cl.len_alloc = cl.len + 1;
    }
    else {
      BLI_remlink(&sconsole->history, &cl);
      MEM_delete(&cl);
    }
  }
}

static void console_space_blend_write(BlendWriter *writer, SpaceLink *sl)
{
  SpaceConsole *con = reinterpret_cast<SpaceConsole *>(sl);

  if (STREQ(con->language, "angelo") || STREQ(con->language, "angelo_genai")) {
    /* Save editor identity/layout, not private prompts or transcript backing
     * buffers. Their owner is the local host, not the shared project file. */
    SpaceConsole saved = *con;
    saved.history.clear_no_delete();
    saved.scrollback.clear_no_delete();
    saved.history_index = saved.sel_start = saved.sel_end = 0;
    writer->write_struct_at_address(con, &saved);
    return;
  }

  for (ConsoleLine &cl : con->history) {
    /* 'len_alloc' is invalid on write, set from 'len' on read */
    writer->write_struct(&cl);
    writer->write_char_array(size_t(cl.len) + 1, cl.line);
  }
  writer->write_struct_cast<SpaceConsole>(sl);
}

void ED_spacetype_console()
{
  std::unique_ptr<SpaceType> st = std::make_unique<SpaceType>();
  ARegionType *art;

  st->spaceid = SPACE_CONSOLE;
  STRNCPY_UTF8(st->name, "Console");

  st->create = console_create;
  st->free = console_free;
  st->init = console_init;
  st->duplicate = console_duplicate;
  st->operatortypes = console_operatortypes;
  st->keymap = console_keymap;
  st->dropboxes = console_dropboxes;
  st->blend_read_data = console_blend_read_data;
  st->blend_write = console_space_blend_write;
  st->space_subtype_item_extend = console_space_subtype_item_extend;
  st->space_subtype_get = console_space_subtype_get;
  st->space_subtype_set = console_space_subtype_set;
  st->space_name_get = console_space_name_get;
  st->space_icon_get = console_space_icon_get;

  /* regions: main window */
  art = MEM_new_zeroed<ARegionType>("spacetype console region");
  art->regionid = RGN_TYPE_WINDOW;
  art->keymapflag = ED_KEYMAP_UI | ED_KEYMAP_VIEW2D;

  art->init = console_main_region_init;
  art->draw = console_main_region_draw;
  art->cursor = console_cursor;
  art->event_cursor = true;
  art->listener = console_main_region_listener;

  BLI_addhead(&st->regiontypes, art);

  /* regions: header */
  art = MEM_new_zeroed<ARegionType>("spacetype console region");
  art->regionid = RGN_TYPE_HEADER;
  art->prefsizey = HEADERY;
  art->keymapflag = ED_KEYMAP_UI | ED_KEYMAP_VIEW2D | ED_KEYMAP_HEADER;

  art->init = console_header_region_init;
  art->draw = console_header_region_draw;

  BLI_addhead(&st->regiontypes, art);

  BKE_spacetype_register(std::move(st));
}

}  // namespace blender
