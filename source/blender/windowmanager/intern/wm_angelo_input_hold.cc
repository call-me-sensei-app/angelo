/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup wm
 *
 * Angelo's live-view input hold. A worker's live part is drawn over one 3D view of the artist's document
 * (`live_view_draw.py`); that view is still there, unseen, and nothing may edit it from the live view. Angelo's
 * input handler in front decides what the live view takes (its own navigation, a click that names an object); this
 * is the backstop for whatever it hands on, so no exception it makes can reach the document no one sees (break-it
 * P1-3 H1, H2, M3):
 *
 * - the held area's region and area handlers never see an event (#wm_event_do_handlers): no key map, gizmo,
 *   button, drop box or tool of that 3D view, its header, toolbar or sidebar;
 * - the window's own key maps (Window, Screen) run only the allowed operators while the pointer is over it, and its
 *   drop boxes take no drop there.
 *
 * Window modal handlers (Angelo's own, Blender's area-edge editing, popups) and events that always pass (timers,
 * window events) are unaffected. A hold is a lease, renewed by Angelo twice a second while the live view shows: when
 * Angelo stops renewing it (an error, the add-on disabled) it lapses on its own, so the 3D view is never left
 * without input.
 */

#include <algorithm>
#include <set>
#include <string>
#include <vector>

#include "BLI_rect.h"
#include "BLI_threads.h"
#include "BLI_time.h"

#include "BKE_context.hh"
#include "BKE_global.hh"
#include "BKE_main.hh"
#include "BKE_screen.hh"

#include "ED_screen.hh"

#include "DNA_screen_types.h"
#include "DNA_windowmanager_types.h"

#include "WM_angelo_input_hold.hh"
#include "WM_api.hh"

#include "wm_draw.hh"
#include "wm_window.hh"

namespace blender {

struct InputHold {
  const wmWindow *win;
  const ScrArea *area;
  double until;
};

/* Standard containers, never the guarded allocator: they outlive Blender's leak detector at exit (break-it P1-4 L5). */
static std::vector<InputHold> g_holds;
static std::set<std::string, std::less<>> g_allowed;

void WM_angelo_input_hold_set(const wmWindow *win, const ScrArea *area, const double seconds)
{
  BLI_assert(BLI_thread_is_main());
  std::erase_if(g_holds, [&](const InputHold &hold) { return hold.win == win; });
  if (area != nullptr && seconds > 0.0) {
    g_holds.push_back({win, area, BLI_time_now_seconds() + seconds});
  }
}

void WM_angelo_input_hold_clear()
{
  BLI_assert(BLI_thread_is_main());
  g_holds.clear();
}

void WM_angelo_input_hold_allow(const Span<std::string> idnames)
{
  BLI_assert(BLI_thread_is_main());
  g_allowed.clear();
  for (const std::string &idname : idnames) {
    g_allowed.insert(idname);
  }
}

/** Whether \a area is one of the areas \a win shows now, and \a win one of the open windows: a hold never reads an
 * area Blender freed (leaving a maximized area, a join or close, a closed window) or one of a screen no longer
 * shown (another workspace), whatever the lease says (break-it P1-4 M1). Only the pointers are compared. */
static bool shown_now(const wmWindow *win, const ScrArea *area)
{
  wmWindowManager *wm = G_MAIN != nullptr ? static_cast<wmWindowManager *>(G_MAIN->wm.first) : nullptr;
  if (wm == nullptr) {
    return false;
  }
  for (wmWindow &window : wm->windows) {
    if (&window != win) {
      continue;
    }
    bScreen *screen = WM_window_get_active_screen(&window);
    if (screen == nullptr) {
      return false;
    }
    for (ScrArea &item : screen->areabase) {
      if (&item == area) {
        return true;
      }
    }
    return false;
  }
  return false;
}

static const InputHold *hold_of(const wmWindow *win)
{
  if (g_holds.empty()) {
    return nullptr;
  }
  const double now = BLI_time_now_seconds();
  for (const InputHold &hold : g_holds) {
    if (hold.win == win && hold.until > now) {
      return shown_now(hold.win, hold.area) ? &hold : nullptr;
    }
  }
  return nullptr;
}

bool WM_angelo_input_held(const wmWindow *win, const ScrArea *area)
{
  const InputHold *hold = hold_of(win);
  return hold != nullptr && hold->area == area;
}

bool WM_angelo_input_held_at(const wmWindow *win, const int xy[2])
{
  const InputHold *hold = hold_of(win);
  return hold != nullptr && BLI_rcti_isect_pt_v(&hold->area->totrct, xy);
}

bool WM_angelo_input_hold_allows(const char *idname)
{
  return idname != nullptr && g_allowed.find(std::string_view(idname)) != g_allowed.end();
}

/* The lead's own picture: Angelo hides the live view and the session rail while it draws (`live_view_draw.hidden`),
 * so a capture of the window shows the lead's document (break-it P1-3 M1). The regions are drawn the way
 * `wm.redraw_timer` draws a window, without its report or wait cursor. */
uint8_t *WM_angelo_window_pixels_redrawn(bContext *C, wmWindow *win, int r_size[2])
{
  BLI_assert(BLI_thread_is_main());
  wmWindowManager *wm = CTX_wm_manager(C);
  wmWindow *win_prev = CTX_wm_window(C);
  ScrArea *area_prev = CTX_wm_area(C);
  ARegion *region_prev = CTX_wm_region(C);
  ARegion *popup_prev = CTX_wm_region_popup(C);
  wm_window_make_drawable(wm, win);
  CTX_wm_window_set(C, win);
  CTX_wm_region_popup_set(C, nullptr);
  bScreen *screen = WM_window_get_active_screen(win);
  if (screen != nullptr) {
    /* Every area the composite blits, the top bar and status bar too: each buffer drawn in this pass
     * (break-it P1-4 M3). */
    ED_screen_areas_iter (win, screen, area) {
      CTX_wm_area_set(C, area);
      for (ARegion &region : area->regionbase) {
        if (!region.runtime->visible) {
          continue;
        }
        CTX_wm_region_set(C, &region);
        wm_draw_region_test(C, area, &region);
      }
    }
  }
  CTX_wm_area_set(C, nullptr);
  CTX_wm_region_set(C, nullptr);
  uint8_t *pixels = WM_window_pixels_read_from_offscreen(C, win, r_size);
  if (win_prev != nullptr && win_prev != win) {
    wm_window_make_drawable(wm, win_prev);
  }
  CTX_wm_window_set(C, win_prev);
  CTX_wm_area_set(C, area_prev);
  CTX_wm_region_set(C, region_prev);
  CTX_wm_region_popup_set(C, popup_prev);
  return pixels;
}

}  // namespace blender
