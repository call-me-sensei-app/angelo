/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup wm
 *
 * Angelo's live-view input hold (`wm_angelo_input_hold.cc`): while a worker's live part covers a 3D view, no event
 * reaches that view's own handlers, and the window's key maps run only the commands the live view allows.
 */

#pragma once

#include <cstdint>
#include <string>

#include "BLI_span.hh"

namespace blender {

struct bContext;
struct ScrArea;
struct wmWindow;

/** Hold \a area of \a win for \a seconds from now (a lease, renewed while the live view shows); 0 releases the
 * window's hold. A window holds at most one area. */
void WM_angelo_input_hold_set(const wmWindow *win, const ScrArea *area, double seconds);
/** Release every window's hold. */
void WM_angelo_input_hold_clear();
/** The window-wide operators a held area lets the window's key maps run (Save, Quit, ...). */
void WM_angelo_input_hold_allow(Span<std::string> idnames);
/** Whether \a area of \a win is held now. */
bool WM_angelo_input_held(const wmWindow *win, const ScrArea *area);
/** Whether the window's held area is under \a xy (window pixels) now. */
bool WM_angelo_input_held_at(const wmWindow *win, const int xy[2]);
/** Whether a held area lets the window's key maps run \a idname. */
bool WM_angelo_input_hold_allows(const char *idname);

/** \a win drawn again now, each visible region into its own buffer, and composited into an offscreen that is read
 * (never the front buffer, which shows the last presented frame on macOS and Windows): RGBA bytes, bottom row first,
 * to free with #MEM_delete; null when no offscreen could be made. */
uint8_t *WM_angelo_window_pixels_redrawn(bContext *C, wmWindow *win, int r_size[2]);

}  // namespace blender
