/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup render
 *
 * Angelo: render a region over the last render (contract in RE_angelo_region.hh).
 */

#include <algorithm>
#include <cstddef>
#include <cstring>
#include <string>

#include "MEM_guardedalloc.h"

#include "BLI_array.hh"
#include "BLI_listbase.h"
#include "BLI_rect.h"
#include "BLI_string.h"
#include "BLI_string_utils.hh"
#include "BLI_time.h"
#include "BLI_vector.hh"

#include "BKE_image.hh"
#include "BKE_report.hh"

#include "IMB_imbuf.hh"
#include "IMB_imbuf_types.hh"

#include "RE_angelo_region.hh"
#include "RE_pipeline.h"

#include "CLG_log.h"

#include "angelo_region_pixels.hh"
#include "angelo_region_render.hh"
#include "render_result.h"
#include "render_types.h"

namespace blender::render::angelo {

/* Its own costs, at debug level (`--log render.region --log-level debug`): what a region render
 * adds to the engine's own work (doc/angelo/render_region.rst, "Performance"). */
static CLG_LogRef LOG = {"render.region"};

/** The previous pixels of one pass inside the region. */
struct KeptPass {
  std::string layer;
  std::string pass;
  int channels = 0;
  Array<float> pixels;
};

/** The previous composite (or sequencer) image of one view, whole; null when there was none. */
struct KeptView {
  std::string view;
  ImBuf *ibuf = nullptr;
};

struct RegionRender {
  /** The previous frame. Owned until it becomes the render's result. */
  RenderResult *base = nullptr;
  /** The render's result after composing: what a revert applies to. Not owned. */
  RenderResult *composed = nullptr;
  rcti region = {};
  bool armed = true;
  bool reverted = false;
  RegionOutcome outcome = RegionOutcome::NotArmed;
  Vector<KeptPass> kept_passes;
  Vector<KeptView> kept_views;
  /** Seconds the live tiles took to land on the shown last render (debug log). */
  double tile_seconds = 0.0;
  /** Reports of the render (borrowed, only while armed). */
  ReportList *reports = nullptr;
};

/* -------------------------------------------------------------------- */
/** \name Pixels
 * \{ */

/** Puts the kept pixels back into `rr`, or swaps them with its current ones when `swap`. */
static void apply_kept_passes(RegionRender &state, RenderResult *rr, const bool swap)
{
  const rcti &region = state.region;
  const int w = BLI_rcti_size_x(&region);
  const int h = BLI_rcti_size_y(&region);
  for (KeptPass &kept : state.kept_passes) {
    RenderPass *rpass = find_pass(rr, kept.layer.c_str(), kept.pass.c_str());
    float *pixels = pass_pixels(rpass);
    if (pixels == nullptr || rpass->channels != kept.channels) {
      continue;
    }
    const size_t row = size_t(w) * kept.channels;
    for (int y = 0; y < h; y++) {
      float *frame_row = pixels +
                         (size_t(region.ymin + y) * rr->rectx + region.xmin) * kept.channels;
      float *kept_row = kept.pixels.data() + row * y;
      if (swap) {
        std::swap_ranges(frame_row, frame_row + row, kept_row);
      }
      else {
        memcpy(frame_row, kept_row, sizeof(float) * row);
      }
    }
  }
  render_result_free_gpu_texture_caches(rr);
}

static void keep_previous_pixels(RegionRender &state)
{
  RenderResult *base = state.base;
  const rcti &region = state.region;
  const int w = BLI_rcti_size_x(&region);
  const int h = BLI_rcti_size_y(&region);
  for (RenderLayer &layer : base->layers) {
    for (RenderPass &rpass : layer.passes) {
      const float *pixels = pass_pixels(&rpass);
      if (pixels == nullptr) {
        continue;
      }
      KeptPass kept;
      kept.layer = layer.name;
      kept.pass = rpass.fullname;
      kept.channels = rpass.channels;
      kept.pixels.reinitialize(size_t(w) * h * rpass.channels);
      copy_rect(kept.pixels.data(),
                w,
                0,
                0,
                pixels,
                base->rectx,
                region.xmin,
                region.ymin,
                w,
                h,
                kept.channels);
      state.kept_passes.append(std::move(kept));
    }
  }
  for (RenderView &rv : base->views) {
    state.kept_views.append({rv.name, rv.ibuf ? IMB_dupImBuf(rv.ibuf) : nullptr});
  }
}

static void free_kept(RegionRender &state)
{
  state.kept_passes.clear_and_shrink();
  for (KeptView &kept : state.kept_views) {
    IMB_freeImBuf(kept.ibuf);
  }
  state.kept_views.clear_and_shrink();
}

static void free_state(Render *re)
{
  RegionRender *state = re->angelo_region;
  if (state == nullptr) {
    return;
  }
  free_kept(*state);
  render_result_free(state->base);
  MEM_delete(state);
  re->angelo_region = nullptr;
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name Caller's side (RE_angelo_region.hh)
 * \{ */

bool region_render_base_usable(const RenderResult *base,
                               const int width,
                               const int height,
                               ReportList *reports)
{
  if (base == nullptr) {
    BKE_report(reports, RPT_ERROR, "Render the full frame first, then render a region over it");
    return false;
  }
  if (base->rectx != width || base->recty != height) {
    BKE_reportf(reports,
                RPT_ERROR,
                "The last render is %d x %d pixels and this one is %d x %d; render the full frame "
                "first",
                base->rectx,
                base->recty,
                width,
                height);
    return false;
  }
  for (const RenderLayer &layer : base->layers) {
    for (const RenderPass &rpass : layer.passes) {
      if (rpass.ibuf && rpass.ibuf->float_data()) {
        return true;
      }
    }
  }
  BKE_report(reports, RPT_ERROR, "The last render has no pixels to render over");
  return false;
}

bool region_render_arm(Render *re,
                       RenderResult *base,
                       const rcti &region,
                       const int width,
                       const int height,
                       ReportList *reports)
{
  free_state(re);
  if (!region_render_base_usable(base, width, height, reports)) {
    render_result_free(base);
    return false;
  }
  rcti frame, clipped;
  BLI_rcti_init(&frame, 0, width, 0, height);
  if (!BLI_rcti_isect(&frame, &region, &clipped) || BLI_rcti_is_empty(&clipped)) {
    BKE_report(reports, RPT_ERROR, "The region is outside the frame");
    render_result_free(base);
    return false;
  }

  RegionRender *state = MEM_new<RegionRender>(__func__);
  state->base = base;
  state->region = clipped;
  state->reports = reports;
  const double start = BLI_time_now_seconds();
  keep_previous_pixels(*state);
  CLOG_DEBUG(&LOG,
             "Kept the region's previous pixels in %.2f ms",
             (BLI_time_now_seconds() - start) * 1e3);
  /* The previous frame is displayed whole while the region renders. */
  base->tilerect = frame;
  re->angelo_region = state;
  return true;
}

bool region_render_is_armed(const Render *re)
{
  return re->angelo_region && re->angelo_region->armed;
}

RegionOutcome region_render_finish(Render *re)
{
  RegionRender *state = re->angelo_region;
  if (state == nullptr) {
    return RegionOutcome::NotArmed;
  }
  if (state->armed) {
    /* The engine never handed its result over: the previous frame comes back, without the live
     * tiles that already landed on it. */
    RE_FreeGPUTextureCaches();
    BLI_rw_mutex_lock(&re->resultmutex, THREAD_LOCK_WRITE);
    apply_kept_passes(*state, state->base, false);
    render_result_free(re->result);
    re->result = state->base;
    state->base = nullptr;
    BLI_rw_mutex_unlock(&re->resultmutex);
    free_state(re);
    return RegionOutcome::Restored;
  }
  const RegionOutcome outcome = state->outcome;
  state->reports = nullptr;
  if (outcome != RegionOutcome::Composited) {
    free_state(re);
  }
  return outcome;
}

bool region_render_display_offset(const Render *re, int r_offset[2])
{
  if (!region_render_is_armed(re)) {
    return false;
  }
  r_offset[0] = re->disprect.xmin;
  r_offset[1] = re->disprect.ymin;
  return true;
}

/** Unlocked: the caller holds the result lock, or is the main thread with no render running. */
static bool can_revert(const Render *re)
{
  const RegionRender *state = re->angelo_region;
  return state && !state->armed && state->outcome == RegionOutcome::Composited &&
         state->composed != nullptr && state->composed == re->result;
}

/* A new render frees a finished region render's state under the result write lock
 * (`RE_InitState`, render thread), so a reader from the main thread holds the read lock. */

bool region_render_can_revert(const Render *re)
{
  Render *mutable_re = const_cast<Render *>(re);
  BLI_rw_mutex_lock(&mutable_re->resultmutex, THREAD_LOCK_READ);
  const bool result = can_revert(re);
  BLI_rw_mutex_unlock(&mutable_re->resultmutex);
  return result;
}

bool region_render_is_reverted(const Render *re)
{
  Render *mutable_re = const_cast<Render *>(re);
  BLI_rw_mutex_lock(&mutable_re->resultmutex, THREAD_LOCK_READ);
  const bool result = can_revert(re) && re->angelo_region->reverted;
  BLI_rw_mutex_unlock(&mutable_re->resultmutex);
  return result;
}

bool region_render_last_region(const Render *re, rcti *r_region)
{
  Render *mutable_re = const_cast<Render *>(re);
  BLI_rw_mutex_lock(&mutable_re->resultmutex, THREAD_LOCK_READ);
  const bool found = can_revert(re);
  if (found) {
    *r_region = re->angelo_region->region;
  }
  BLI_rw_mutex_unlock(&mutable_re->resultmutex);
  return found;
}

bool region_render_toggle_revert(Render *re)
{
  if (!region_render_can_revert(re)) {
    return false;
  }
  /* A persistent compositor may hold the result's GPU textures, which the swap frees. */
  RE_FreeGPUTextureCaches();
  RegionRender &state = *re->angelo_region;
  BLI_rw_mutex_lock(&re->resultmutex, THREAD_LOCK_WRITE);
  apply_kept_passes(state, re->result, true);
  for (KeptView &kept : state.kept_views) {
    RenderView *rv = static_cast<RenderView *>(
        BLI_findstring(&re->result->views, kept.view.c_str(), offsetof(RenderView, name)));
    if (rv) {
      std::swap(rv->ibuf, kept.ibuf);
    }
  }
  state.reverted = !state.reverted;
  BLI_rw_mutex_unlock(&re->resultmutex);
  return true;
}

void region_render_free(Render *re)
{
  free_state(re);
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name Pipeline's side (angelo_region_render.hh)
 * \{ */

void region_render_merge_tile(Render *re, const RenderResult *tile)
{
  RegionRender *state = re->angelo_region;
  if (state == nullptr || !state->armed || tile == nullptr) {
    return;
  }
  /* The tile's rectangle is relative to the rendered (cropped) result. */
  rcti tile_rect = tile->tilerect;
  BLI_rcti_translate(&tile_rect, re->disprect.xmin, re->disprect.ymin);
  rcti rect;
  if (!BLI_rcti_isect(&tile_rect, &state->region, &rect) || BLI_rcti_is_empty(&rect)) {
    return;
  }
  const double start = BLI_time_now_seconds();
  copy_region(
      state->base, const_cast<RenderResult *>(tile), tile_rect.xmin, tile_rect.ymin, rect, false);
  state->tile_seconds += BLI_time_now_seconds() - start;
}

/** Warns about passes of the previous frame that this render did not produce. */
static void report_stale_passes(RegionRender &state, RenderResult *base, RenderResult *rendered)
{
  for (RenderLayer &layer : rendered->layers) {
    RenderLayer *base_layer = RE_GetRenderLayer(base, layer.name);
    if (base_layer == nullptr) {
      BKE_reportf(state.reports,
                  RPT_WARNING,
                  "View layer \"%s\" was not in the last render; render the full frame to see it",
                  layer.name);
      continue;
    }
    for (RenderPass &rpass : base_layer->passes) {
      if (find_pass(rendered, layer.name, rpass.fullname) == nullptr) {
        BKE_reportf(state.reports,
                    RPT_WARNING,
                    "Pass \"%s\" was not rendered this time and still shows the last render",
                    rpass.fullname);
      }
    }
  }
}

bool region_render_compose(Render *re, const bool cancelled)
{
  RegionRender *state = re->angelo_region;
  if (state == nullptr || !state->armed) {
    return false;
  }
  const double start = BLI_time_now_seconds();
  RenderResult *rendered = re->result;
  RenderResult *base = state->base;
  const bool rendered_matches = rendered && !BLI_listbase_is_empty(&rendered->layers) &&
                                rendered->rectx == BLI_rcti_size_x(&re->disprect) &&
                                rendered->recty == BLI_rcti_size_y(&re->disprect);
  rcti rect;
  const bool overlaps = BLI_rcti_isect(&re->disprect, &state->region, &rect) &&
                        !BLI_rcti_is_empty(&rect);

  if (cancelled || !rendered_matches || !overlaps) {
    if (rendered && rendered->error) { /* The engine's own reason (out of GPU memory...) stays. */
      BKE_report(state->reports, RPT_ERROR, rendered->error);
      MEM_delete(base->error);
      base->error = BLI_strdup(rendered->error);
    }
    apply_kept_passes(*state, base, false);
    free_kept(*state);
    state->outcome = RegionOutcome::Restored;
  }
  else {
    report_stale_passes(*state, base, rendered);
    const int added = copy_region(
        base, rendered, re->disprect.xmin, re->disprect.ymin, rect, true);
    if (added > 0) {
      BKE_report(state->reports,
                 RPT_WARNING,
                 "Some passes were not in the last render and are empty outside the region");
    }
    render_result_free_gpu_texture_caches(base);
    BKE_stamp_data_free(base->stamp_data);
    base->stamp_data = BKE_stamp_data_copy(rendered->stamp_data);
    /* The composite is made again from the new passes (kept whole for a revert). */
    for (RenderView &rv : base->views) {
      IMB_freeImBuf(rv.ibuf);
      rv.ibuf = nullptr;
    }
    state->composed = base;
    state->outcome = RegionOutcome::Composited;
  }

  render_result_free(rendered);
  re->result = base;
  state->base = nullptr;
  state->armed = false;
  CLOG_DEBUG(&LOG,
             "Composed the region over the last render in %.2f ms; live tiles took %.2f ms",
             (BLI_time_now_seconds() - start) * 1e3,
             state->tile_seconds * 1e3);
  return true;
}

RenderResult *region_render_display_result(const Render *re)
{
  return region_render_is_armed(re) ? re->angelo_region->base : nullptr;
}

void region_render_new_render(Render *re)
{
  if (re->angelo_region && !re->angelo_region->armed) {
    free_state(re);
  }
}

/** \} */

}  // namespace blender::render::angelo
