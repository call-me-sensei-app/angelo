/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup wm
 *
 * Angelo's startup cover: one opaque rectangle over every editor area of a window, drawn after
 * everything the areas draw (region overlays such as the timeline's playhead, the blend of
 * overlapping regions, action-zone arrows, area edges and the window's draw callbacks) and before
 * popups, so Blender's startup splash stays above it and the top and status bars stay visible.
 *
 * The startup module (`scripts/startup/angelo_default_layout.py`) sets the colour through
 * `WindowManager.angelo_startup_cover` while Angelo's layout is being put in place, so the stock
 * startup layout never shows, and clears it (alpha 0) once the layout has settled.
 */

#include "BLI_math_vector.h"
#include "BLI_rect.h"

#include "DNA_windowmanager_types.h"

#include "GPU_immediate.hh"
#include "GPU_state.hh"

#include "WM_api.hh"
#include "wm_draw.hh"

namespace blender {

static float g_startup_cover[4] = {0.0f, 0.0f, 0.0f, 0.0f};

void WM_angelo_startup_cover_set(const float color[4])
{
  copy_v4_v4(g_startup_cover, color);
}

void WM_angelo_startup_cover_get(float r_color[4])
{
  copy_v4_v4(r_color, g_startup_cover);
}

void wm_angelo_startup_cover_draw(wmWindow *win)
{
  if (g_startup_cover[3] <= 0.0f) {
    return;
  }
  rcti rect;
  WM_window_screen_rect_calc(win, &rect);
  GPU_blend(GPU_BLEND_ALPHA);
  const uint pos = GPU_vertformat_attr_add(
      immVertexFormat(), "pos", gpu::VertAttrType::SFLOAT_32_32);
  immBindBuiltinProgram(GPU_SHADER_3D_UNIFORM_COLOR);
  immUniformColor4fv(g_startup_cover);
  immRectf(pos, float(rect.xmin), float(rect.ymin), float(rect.xmax + 1), float(rect.ymax + 1));
  immUnbindProgram();
  GPU_blend(GPU_BLEND_NONE);
}

}  // namespace blender
