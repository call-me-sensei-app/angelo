/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup wm
 *
 * Angelo: the region a pointer event reaches, shared by Blender's handler loop and the grant guard's input feed
 * (`wm_angelo_pointer_region.hh`).
 */

#include "BLI_listbase_iterator.hh"
#include "BLI_rect.hh"

#include "BKE_screen.hh"

#include "DNA_screen_types.h"
#include "DNA_windowmanager_types.h"

#include "ED_screen.hh"

#include "WM_angelo_input_hold.hh"
#include "WM_api.hh"

#include "wm_angelo_pointer_region.hh"

namespace blender {

bool wm_event_area_takes_xy(const wmWindow *win, const ScrArea *area, const int xy[2])
{
  return BLI_rcti_isect_pt_v(&area->totrct, xy) && !WM_angelo_input_held(win, area);
}

ARegion *wm_event_area_region_xy(const ScrArea *area, const int xy[2])
{
  return ED_area_find_region_xy_visual(area, RGN_TYPE_ANY, xy);
}

const ARegion *wm_event_pointer_region(const wmWindow *win, const bScreen *screen, const int xy[2])
{
  if (screen == nullptr) {
    return nullptr;
  }
  const ARegion *top = nullptr; /* Drawn in order: the last one there is on top. */
  for (const ARegion &region : screen->regionbase) {
    if (region.runtime->visible && BLI_rcti_isect_pt_v(&region.winrct, xy)) {
      top = &region;
    }
  }
  if (top != nullptr) {
    return top;
  }
  ED_screen_areas_iter (win, screen, area) {
    if (wm_event_area_takes_xy(win, area, xy)) {
      return wm_event_area_region_xy(area, xy);
    }
  }
  return nullptr;
}

const ARegion *WM_angelo_pointer_region(const wmWindow *win, const int xy[2])
{
  return wm_event_pointer_region(win, WM_window_get_active_screen(win), xy);
}

}  // namespace blender
