/* SPDX-FileCopyrightText: 2008 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup edinterface
 *
 * General Interface Region Code
 *
 * \note Most logic is now in 'interface_region_*.c'
 */

#include "BLI_listbase.hh"

#include "BKE_context.hh"
#include "BKE_screen.hh"

#include "wm_draw.hh"

#include "ED_screen.hh"

#include "interface_intern.hh"
#include "interface_regions_intern.hh"

namespace blender::ui {

ARegion *region_temp_add(bScreen *screen)
{
  ARegion *region = BKE_area_region_new();
  BLI_addtail(&screen->regionbase, region);

  region->regiontype = RGN_TYPE_TEMPORARY;
  region->alignment = RGN_ALIGN_FLOAT;

  return region;
}

static void region_temp_remove_ex(bContext *C,
                                  bScreen *screen,
                                  ARegion *region,
                                  const AngeloTooltipOwn *own)
{
  wmWindow *win = CTX_wm_window(C);

  BLI_assert(region->regiontype == RGN_TYPE_TEMPORARY);
  BLI_assert(BLI_findindex(&screen->regionbase, region) != -1);
  if (win) {
    wm_draw_region_clear(win, region);
  }
  if (win) {
    /* Angelo: every control it covered (a menu over a guarded checkbox, a submenu over its parent,
     * a tooltip over a neighbour) is on screen again only from now, whatever closed it: a click,
     * Escape, a click outside, a timer, another popup taking its place (grant_operators.rst). A
     * tooltip included: a control placed or re-laid under one was never seen, and the cost is a
     * guarded control under a closing tooltip arming 0.3 s after its next frame. */
    ui_angelo_note_uncovered(win, UI_angelo_temporary_cover(region), region, true, own);
    ui_angelo_note_covering(win, region, false); /* The guard's consent regions under it too. */
  }

  ED_region_exit(C, region);
  BKE_area_region_free(nullptr, region); /* nullptr: no space-type. */
  BLI_freelinkN(&screen->regionbase, region);

  if (CTX_wm_region(C) == region) {
    CTX_wm_region_set(C, nullptr);
  }
  if (CTX_wm_region_popup(C) == region) {
    CTX_wm_region_popup_set(C, nullptr);
  }
}

void region_temp_remove(bContext *C, bScreen *screen, ARegion *region)
{
  region_temp_remove_ex(C, screen, region, nullptr);
}

void region_temp_remove_tooltip(bContext *C,
                                bScreen *screen,
                                ARegion *region,
                                const AngeloTooltipOwn &own)
{
  region_temp_remove_ex(C, screen, region, &own);
}

}  // namespace blender::ui
