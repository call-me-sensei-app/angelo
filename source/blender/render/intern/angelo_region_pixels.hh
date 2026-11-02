/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * \ingroup render
 *
 * Angelo: moving pixels between render results for a region render (RE_angelo_region.hh).
 *
 * Pixels move only between passes that match by layer name, full pass name (which carries the
 * view) and channel count, and only where both have CPU float data. Rectangles are in pixels of
 * the full frame, `xmax`/`ymax` exclusive, like `Render::disprect`.
 */

#include "DNA_vec_types.h"

namespace blender {

struct RenderPass;
struct RenderResult;

namespace render::angelo {

/** The pass of `rr` in layer `layer` with full name `fullname` (which carries the view). */
RenderPass *find_pass(RenderResult *rr, const char *layer, const char *fullname);

/** The CPU float pixels of `rpass`, or null. */
float *pass_pixels(RenderPass *rpass);

/** Copies `w` x `h` pixels of `channels` floats between two row-major images. */
void copy_rect(float *dst,
               int dst_width,
               int dst_x,
               int dst_y,
               const float *src,
               int src_width,
               int src_x,
               int src_y,
               int w,
               int h,
               int channels);

/**
 * Copies the pixels of `src` inside `rect` into `dst`. `src` covers the full-frame rectangle that
 * starts at `src_x`, `src_y`; `dst` is a full frame; `rect` lies inside both. A pass of `src`
 * that `dst` lacks is added to `dst` when `add_missing`, else skipped. Returns how many passes
 * were added.
 */
int copy_region(RenderResult *dst,
                RenderResult *src,
                int src_x,
                int src_y,
                const rcti &rect,
                bool add_missing);

}  // namespace render::angelo
}  // namespace blender
