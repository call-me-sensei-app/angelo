/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup render
 *
 * Angelo: moving pixels between render results for a region render (RE_angelo_region.hh).
 */

#include <cstring>

#include "BLI_listbase.h"
#include "BLI_rect.h"
#include "BLI_string.h"

#include "IMB_imbuf_types.hh"

#include "RE_pipeline.h"

#include "angelo_region_pixels.hh"

namespace blender::render::angelo {

RenderPass *find_pass(RenderResult *rr, const char *layer, const char *fullname)
{
  RenderLayer *rl = RE_GetRenderLayer(rr, layer);
  if (rl == nullptr) {
    return nullptr;
  }
  for (RenderPass &rpass : rl->passes) {
    if (STREQ(rpass.fullname, fullname)) {
      return &rpass;
    }
  }
  return nullptr;
}

float *pass_pixels(RenderPass *rpass)
{
  return (rpass && rpass->ibuf) ? rpass->ibuf->float_data_for_write() : nullptr;
}

void copy_rect(float *dst,
               const int dst_width,
               const int dst_x,
               const int dst_y,
               const float *src,
               const int src_width,
               const int src_x,
               const int src_y,
               const int w,
               const int h,
               const int channels)
{
  for (int y = 0; y < h; y++) {
    memcpy(dst + (size_t(dst_y + y) * dst_width + dst_x) * channels,
           src + (size_t(src_y + y) * src_width + src_x) * channels,
           sizeof(float) * size_t(w) * channels);
  }
}

int copy_region(RenderResult *dst,
                RenderResult *src,
                const int src_x,
                const int src_y,
                const rcti &rect,
                const bool add_missing)
{
  const int w = BLI_rcti_size_x(&rect);
  const int h = BLI_rcti_size_y(&rect);
  int added = 0;
  for (RenderLayer &src_layer : src->layers) {
    if (RE_GetRenderLayer(dst, src_layer.name) == nullptr) {
      continue;
    }
    for (RenderPass &src_pass : src_layer.passes) {
      const float *src_pixels = pass_pixels(&src_pass);
      if (src_pixels == nullptr) {
        continue;
      }
      RenderPass *dst_pass = find_pass(dst, src_layer.name, src_pass.fullname);
      if (dst_pass == nullptr && add_missing) {
        RE_create_render_pass(dst,
                              src_pass.name,
                              src_pass.channels,
                              src_pass.chan_id,
                              src_layer.name,
                              src_pass.view,
                              true);
        dst_pass = find_pass(dst, src_layer.name, src_pass.fullname);
        added += dst_pass != nullptr;
      }
      float *dst_pixels = pass_pixels(dst_pass);
      if (dst_pixels == nullptr || dst_pass->channels != src_pass.channels) {
        continue;
      }
      copy_rect(dst_pixels,
                dst->rectx,
                rect.xmin,
                rect.ymin,
                src_pixels,
                src->rectx,
                rect.xmin - src_x,
                rect.ymin - src_y,
                w,
                h,
                src_pass.channels);
    }
  }
  return added;
}

}  // namespace blender::render::angelo
