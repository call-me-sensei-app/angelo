/* SPDX-FileCopyrightText: 2007 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup wm
 */

#pragma once

namespace blender {

struct ARegion;
struct GPUOffScreen;
namespace gpu {
class Texture;
}
struct GPUViewport;
struct ScrArea;
struct bContext;
struct wmWindow;

struct wmDrawBuffer {
  GPUOffScreen *offscreen;
  GPUViewport *viewport;
  bool stereo;
  int bound_view;
};

/* `wm_draw.cc` */

void wm_draw_update(bContext *C);
void wm_draw_region_clear(wmWindow *win, ARegion *region);
void wm_draw_region_blend(ARegion *region, int view, bool blend);
void wm_draw_region_test(bContext *C, ScrArea *area, ARegion *region);
/** Angelo: every region of \a win laid out and drawn again into its own buffer, as a window redraw draws it,
 * without presenting the window. */
void wm_draw_window_regions_again(bContext *C, wmWindow *win);
/** Angelo's startup cover over the window's editor areas, when set (`wm_angelo_cover.cc`). */
void wm_angelo_startup_cover_draw(wmWindow *win);

gpu::Texture *wm_draw_region_texture(ARegion *region, int view);

}  // namespace blender
