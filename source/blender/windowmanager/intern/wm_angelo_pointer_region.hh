/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * \ingroup wm
 *
 * Angelo: which region a pointer event reaches, for the grant guard (doc/angelo/grant_operators.rst; the review of
 * f6b01a9..da9ba66, SHOULD 1; the maintainer: "Fix properly in 2.1.0"). Blender's own handler loop
 * (`wm_event_do_handlers`) asks the same two steps, so the guard's answer and the region that takes the event
 * cannot differ: the area whose rect holds the point and that takes input (`wm_event_area_takes_xy`), then the
 * region of it visually under the point (`wm_event_area_region_xy`: overlapping regions first where they are opaque,
 * a transparent header only on its buttons; `ED_area_find_region_xy_visual`).
 */

namespace blender {

struct ARegion;
struct ScrArea;
struct bScreen;
struct wmWindow;

/** Whether Blender's handlers give `area` of `win` a pointer event at `xy` (window pixels): inside its rect, and the
 * area takes input (no worker's live view holds it, `WM_angelo_input_held`). */
bool wm_event_area_takes_xy(const wmWindow *win, const ScrArea *area, const int xy[2]);
/** The region of `area` Blender's handlers give a pointer event at `xy`, or null. */
ARegion *wm_event_area_region_xy(const ScrArea *area, const int xy[2]);
/**
 * The region a pointer event at `xy` in `win` (showing `screen`, its active one) reaches: the topmost temporary region drawn there (a menu, popover,
 * dialog, pie, search box; a tooltip too, which Blender passes clicks through: the guard fails closed under one),
 * else the region Blender's handlers give it in the area under it (the two steps above), else null (between areas,
 * outside the window, an area that takes no input).
 */
const ARegion *wm_event_pointer_region(const wmWindow *win, const bScreen *screen, const int xy[2]);

}  // namespace blender
