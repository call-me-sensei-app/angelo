/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * \ingroup render
 *
 * Angelo: render a region over the last render.
 *
 * Blender's render border with Crop off renders the region and fills the rest of the frame with
 * black. A region render over the last render instead renders the region into the previous full
 * frame: every pass of every rendered layer changes only inside the region, and every pixel
 * outside it stays bit-identical to the previous render. While the region renders, the previous
 * frame stays on screen and the new tiles land on it.
 *
 * The caller (the render operator) takes the previous full-frame result away from the render or
 * its render slot, arms the render with it and with the region, starts an ordinary border render
 * of the region (optionally padded, so a denoiser or screen-space effect sees context past the
 * region's edge), and calls #region_render_finish once the render has ended, on the main thread.
 *
 * - Finished: the region is copied into the previous frame, which becomes the render's result.
 *   The previous pixels of the region (and the previous composite) are kept, so
 *   #region_render_toggle_revert can put them back, and put the new ones back again.
 * - Cancelled, failed or stopped before the engine wrote its result: the previous frame comes
 *   back untouched, live tiles included.
 * - Passes the previous render did not have are added, empty outside the region; passes this
 *   render did not produce keep their previous pixels. Both are reported as warnings.
 *
 * Contract: doc/angelo/render_region.rst.
 */

#include "DNA_vec_types.h"

namespace blender {

struct Image;
struct Main;
struct Render;
struct RenderResult;
struct ReportList;

namespace render::angelo {

enum class RegionOutcome {
  /** The render was not a region render. */
  NotArmed,
  /** The region was rendered over the previous frame. */
  Composited,
  /** The render was cancelled or failed; the previous frame is the render's result again. */
  Restored,
};

/** The Render Result image the Image Editor shows renders in, or null when there is none yet. */
Image *region_render_result_image(Main *bmain);

/**
 * The render a region renders over: what the Image Editor shows in `ima`'s active render slot,
 * that slot's own render when it has one, else `re`'s last render. A render into another slot (an
 * agent's look, which puts the person's slot back) never takes its place. Main thread, no render
 * running (the pointer is used without the result lock).
 */
RenderResult *region_render_shown_result(Render *re, Image *ima);

/**
 * The size of that render, safe while a render runs; false (and 0 x 0) when there is none. What
 * `RenderSettings.angelo_last_render_size` reads.
 */
bool region_render_shown_size(Render *re, Image *ima, int r_size[2]);

/** Whether `ima` shows the render's own last result, which a revert applies to. */
bool region_render_shows_last(const Image *ima);

/**
 * Whether `base` can be rendered over: a full-frame result of `width` x `height` (the size the
 * next render of the scene has, without border or crop) with pixels in at least one pass.
 * Reports why not to `reports`.
 */
bool region_render_base_usable(const RenderResult *base,
                               int width,
                               int height,
                               ReportList *reports);

/**
 * Arms `re` for a region render over `base`, which it takes ownership of in every case.
 *
 * `base` must pass #region_render_base_usable and `region` (in pixels) must overlap it. Returns
 * false (and frees `base`) when either does not; the render is then not armed.
 *
 * Main thread, before the render starts.
 */
bool region_render_arm(Render *re,
                       RenderResult *base,
                       const rcti &region,
                       int width,
                       int height,
                       ReportList *reports);

/** Whether `re` is armed for a region render that has not ended yet. */
bool region_render_is_armed(const Render *re);

/**
 * Ends the region render of `re`: when the render never composited the region (cancelled, failed
 * or stopped early) the previous frame becomes the result again. Main thread, after the render.
 */
RegionOutcome region_render_finish(Render *re);

/**
 * While a region render shows the previous frame, the offset of the rendered (cropped) result in
 * it, so a display can place the rendered tiles. False when no region render is displayed.
 */
bool region_render_display_offset(const Render *re, int r_offset[2]);

/** Whether the last render of `re` was a region render whose previous pixels are still kept. */
bool region_render_can_revert(const Render *re);

/** Whether the region currently shows its previous pixels (after #region_render_toggle_revert). */
bool region_render_is_reverted(const Render *re);

/** The region of the last region render of `re`, in pixels. False when there is none. */
bool region_render_last_region(const Render *re, rcti *r_region);

/**
 * Swaps the region's pixels (every pass, and the composite) with the kept previous ones: the
 * first call reverts the region render, the next one applies it again. Main thread, no render
 * running. Returns false when nothing can be reverted.
 */
bool region_render_toggle_revert(Render *re);

/** Frees the region render state of `re` (render freed). */
void region_render_free(Render *re);

}  // namespace render::angelo
}  // namespace blender
