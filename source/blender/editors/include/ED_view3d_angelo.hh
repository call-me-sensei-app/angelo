/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup editors
 *
 * Angelo: where a 3D view region's own text info ended in its last draw, so overlays drawn over the view (the
 * session rail) start below whatever Blender drew there: the view's name or FPS, the active object, the grid unit,
 * the render engine's info, Performance and Statistics, each as the person's preferences and the view show them.
 */

#pragma once

#include <optional>

namespace blender {

struct ARegion;

/**
 * Region pixels, from the region's bottom, of the lowest point of the text `view3d_draw_region_info` drew in
 * \a region's last draw (descenders included), or the top of its visible rectangle when it drew none; empty before
 * its first draw. A region's POST_PIXEL callbacks run after it in the same draw, so they read that draw's value.
 * Main thread.
 */
std::optional<int> ED_view3d_angelo_text_info_end(const ARegion *region);

}  // namespace blender
