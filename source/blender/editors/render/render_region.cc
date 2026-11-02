/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup edrend
 *
 * Angelo: the render operator's side of a region render over the last render
 * (RE_angelo_region.hh has the contract; doc/angelo/render_region.rst the feature).
 *
 * `render.render(region_over_last=True)` renders the region (its `region` property, or the
 * scene's render region) into the render result the Image Editor shows in its active render
 * slot: that slot's own render when it has one, else a copy of the last render, which keeps its
 * own slot, so a before/after compare across slots works. For the render's duration the scene
 * renders its render region padded by `region_padding` pixels with Crop off, which every engine
 * that supports a render border understands; the scene's own region settings come back when the
 * render ends, whatever way it ends.
 */

#include <algorithm>
#include <cmath>

#include "BLI_listbase.h"
#include "BLI_rect.h"

#include "DNA_image_types.h"
#include "DNA_node_types.h"
#include "DNA_scene_types.h"

#include "BKE_context.hh"
#include "BKE_global.hh"
#include "BKE_image.hh"
#include "BKE_main.hh"
#include "BKE_node_legacy_types.hh"
#include "BKE_node_runtime.hh"
#include "BKE_report.hh"
#include "BKE_scene.hh"

#include "DEG_depsgraph.hh"

#include "RE_angelo_region.hh"
#include "RE_pipeline.h"

#include "RNA_access.hh"
#include "RNA_define.hh"

#include "WM_api.hh"
#include "WM_types.hh"

#include "render_intern.hh"

namespace blender {

namespace region = render::angelo;

/** The scene's render settings a region render holds for its duration (and gives back). */
static constexpr int REGION_MODE_BITS = R_BORDER | R_CROP | R_PERSISTENT_DATA;

/** Default pixels rendered around the region; see the `region_padding` property. */
static constexpr int REGION_PADDING_DEFAULT = 32;

void render_region_operator_properties(wmOperatorType *ot)
{
  PropertyRNA *prop = RNA_def_boolean(
      ot->srna,
      "region_over_last",
      false,
      "Region Over Last Render",
      "Render only the region and keep the last render's passes everywhere else; compositing runs "
      "again on the whole frame");
  RNA_def_property_flag(prop, PropertyFlag(PROP_SKIP_SAVE | PROP_HIDDEN));
  prop = RNA_def_float_array(ot->srna,
                             "region",
                             4,
                             nullptr,
                             0.0f,
                             1.0f,
                             "Region",
                             "Left, bottom, right and top of the region as fractions of the frame "
                             "(the scene's render region when unset)",
                             0.0f,
                             1.0f);
  RNA_def_property_flag(prop, PropertyFlag(PROP_SKIP_SAVE | PROP_HIDDEN));
  prop = RNA_def_int(ot->srna,
                     "region_padding",
                     REGION_PADDING_DEFAULT,
                     0,
                     512,
                     "Region Padding",
                     "Pixels rendered around the region so denoising and screen-space effects see "
                     "past its edge; only the region itself is kept",
                     0,
                     512);
  RNA_def_property_flag(prop, PropertyFlag(PROP_SKIP_SAVE | PROP_HIDDEN));
}

/** The region as fractions of the frame: the operator's, else the scene's render region. */
static bool region_fractions(wmOperator *op, const Scene *scene, rctf *r_region)
{
  PropertyRNA *prop = RNA_struct_find_property(op->ptr, "region");
  if (RNA_property_is_set(op->ptr, prop)) {
    float values[4];
    RNA_property_float_get_array(op->ptr, prop, values);
    BLI_rctf_init(r_region, values[0], values[2], values[1], values[3]);
  }
  else {
    *r_region = scene->r.border;
  }
  return r_region->xmax > r_region->xmin && r_region->ymax > r_region->ymin;
}

/** A pixel edge as the fraction the render pipeline turns back into that same pixel. */
static float pixel_to_fraction(const int pixel, const int size)
{
  if (pixel <= 0) {
    return 0.0f;
  }
  if (pixel >= size) {
    return 1.0f;
  }
  /* `disprect = border * size` truncates, so aim at the pixel's middle. */
  return (float(pixel) + 0.5f) / float(size);
}

/**
 * Whether compositing renders other scenes (a Render Layers or Cryptomatte node of another
 * scene): those renders take the region as their border and are not rendered over their own last
 * render, so the composite outside the region would change.
 */
static bool compositor_renders_other_scenes(const Scene *scene)
{
  const bNodeTree *tree = scene->compositing_node_group;
  if (!(scene->r.scemode & R_DOCOMP) || tree == nullptr) {
    return false;
  }
  for (const bNode *node : tree->all_nodes()) {
    const bool renders = node->type_legacy == CMP_NODE_R_LAYERS ||
                         (node->type_legacy == CMP_NODE_CRYPTOMATTE &&
                          node->custom1 == CMP_NODE_CRYPTOMATTE_SOURCE_RENDER);
    if (renders && !node->is_muted() && node->id != nullptr && node->id != &scene->id) {
      return true;
    }
  }
  return false;
}

/** The render result to render over: what the Image Editor shows in the active render slot. */
static RenderResult *take_base(Render *re, Image *ima)
{
  if (region::region_render_shows_last(ima)) {
    RenderResult *base = nullptr;
    RE_SwapResult(re, &base);
    return base;
  }
  RenderSlot *slot = BKE_image_get_renderslot(ima, ima->render_slot);
  if (slot && slot->render) {
    RenderResult *base = slot->render;
    slot->render = nullptr;
    return base;
  }
  /* An empty slot: over a copy of the last render, which stays in its own slot. */
  RenderResult *base = RE_DuplicateRenderResult(RE_AcquireResultRead(re));
  RE_ReleaseResult(re);
  return base;
}

bool render_region_begin(
    wmOperator *op, Main *bmain, Scene *scene, const bool is_animation, RenderRegionJob *r_job)
{
  *r_job = RenderRegionJob();
  if (!RNA_boolean_get(op->ptr, "region_over_last")) {
    /* An ordinary render starts as Blender's always does: an engine a region render kept (its
     * Persistent Data) is freed first, unless the scene keeps Persistent Data itself. Without a
     * region render before it there is no engine to free. */
    if (!(scene->r.mode & R_PERSISTENT_DATA)) {
      RE_FreePersistentData(scene);
    }
    return true;
  }
  if (is_animation) {
    BKE_report(op->reports, RPT_ERROR, "A region renders over one frame, not an animation");
    return false;
  }
  if (RE_seq_render_active(scene, &scene->r)) {
    BKE_report(op->reports, RPT_ERROR, "A region cannot render over the sequencer's output");
    return false;
  }
  if (G.is_rendering) {
    BKE_report(op->reports, RPT_ERROR, "A render is running; render the region once it ends");
    return false;
  }
  /* Burned-in metadata is drawn into the whole frame by every render: the region would burn it
   * into the last render's pixels a second time. */
  if (scene->r.stamp & R_STAMP_DRAW) {
    BKE_report(
        op->reports,
        RPT_ERROR,
        "Turn off Burn Into Image (Output > Metadata) to render a region over the last render");
    return false;
  }
  if (compositor_renders_other_scenes(scene)) {
    BKE_report(op->reports,
               RPT_ERROR,
               "The compositor uses another scene's render, which would be black outside the "
               "region; render the full frame");
    return false;
  }
  rctf fractions;
  if (!region_fractions(op, scene, &fractions)) {
    BKE_report(op->reports, RPT_ERROR, "Draw a region first");
    return false;
  }

  int width, height;
  BKE_render_resolution(&scene->r, false, &width, &height);
  rcti pixels;
  BLI_rcti_init(&pixels,
                std::clamp(int(std::floor(fractions.xmin * width)), 0, width),
                std::clamp(int(std::ceil(fractions.xmax * width)), 0, width),
                std::clamp(int(std::floor(fractions.ymin * height)), 0, height),
                std::clamp(int(std::ceil(fractions.ymax * height)), 0, height));
  if (BLI_rcti_is_empty(&pixels)) {
    BKE_report(op->reports, RPT_ERROR, "The region is outside the frame");
    return false;
  }

  Render *re = RE_GetSceneRender(scene);
  Image *ima = BKE_image_ensure_viewer(bmain, IMA_TYPE_R_RESULT, "Render Result");
  if (!region::region_render_base_usable(
          re ? region::region_render_shown_result(re, ima) : nullptr, width, height, op->reports))
  {
    return false;
  }
  if (!region::region_render_arm(re, take_base(re, ima), pixels, width, height, op->reports)) {
    return false;
  }

  const int padding = RNA_int_get(op->ptr, "region_padding");
  rcti rendered = pixels;
  BLI_rcti_pad(&rendered, padding, padding);
  r_job->active = true;
  r_job->mode = scene->r.mode & REGION_MODE_BITS;
  r_job->border = scene->r.border;
  /* Persistent Data: the engine keeps its scene, BVH and device after a region render, so the next
   * region render skips rebuilding them (0.38 s of a 0.74 s Cycles region render, measured;
   * render_region.rst "Performance"). The next ordinary render frees them as Blender always does
   * (its own settings). */
  scene->r.mode = (scene->r.mode & ~R_CROP) | R_BORDER | R_PERSISTENT_DATA;
  BLI_rctf_init(&scene->r.border,
                pixel_to_fraction(rendered.xmin, width),
                pixel_to_fraction(rendered.xmax, width),
                pixel_to_fraction(rendered.ymin, height),
                pixel_to_fraction(rendered.ymax, height));
  /* An engine that keeps its evaluated scene between renders sees the region too. */
  DEG_id_tag_update(&scene->id, ID_RECALC_SYNC_TO_EVAL);
  return true;
}

void render_region_end(Scene *scene, Render *re, RenderRegionJob *job, ReportList *reports)
{
  if (!job->active) {
    return;
  }
  job->active = false;
  scene->r.mode = (scene->r.mode & ~REGION_MODE_BITS) | job->mode;
  scene->r.border = job->border;
  DEG_id_tag_update(&scene->id, ID_RECALC_SYNC_TO_EVAL);
  switch (region::region_render_finish(re)) {
    case region::RegionOutcome::Composited:
      BKE_report(reports, RPT_INFO, "Rendered the region over the last render");
      break;
    case region::RegionOutcome::Restored:
      BKE_report(reports, RPT_WARNING, "The region did not render; the last render is kept");
      break;
    case region::RegionOutcome::NotArmed:
      break;
  }
}

/* -------------------------------------------------------------------- */
/** \name Revert Region Operator
 * \{ */

static bool region_revert_poll(bContext *C)
{
  Scene *scene = CTX_data_scene(C);
  if (scene == nullptr || WM_jobs_test(CTX_wm_manager(C), scene, WM_JOB_TYPE_RENDER)) {
    return false;
  }
  Render *re = RE_GetSceneRender(scene);
  Image *ima = region::region_render_result_image(CTX_data_main(C));
  if (re == nullptr || ima == nullptr || !region::region_render_shows_last(ima)) {
    CTX_wm_operator_poll_msg_set(C, "Render a region over the last render first");
    return false;
  }
  if (!region::region_render_can_revert(re)) {
    CTX_wm_operator_poll_msg_set(C, "Render a region over the last render first");
    return false;
  }
  return true;
}

static wmOperatorStatus region_revert_exec(bContext *C, wmOperator *op)
{
  Main *bmain = CTX_data_main(C);
  Scene *scene = CTX_data_scene(C);
  Render *re = RE_GetSceneRender(scene);
  if (!region::region_render_toggle_revert(re)) {
    return OPERATOR_CANCELLED;
  }
  Image *ima = region::region_render_result_image(bmain);
  BKE_image_signal(bmain, ima, nullptr, IMA_SIGNAL_FREE);
  BKE_report(op->reports,
             RPT_INFO,
             region::region_render_is_reverted(re) ? "The region shows the last render again" :
                                                     "The region shows the region render again");
  WM_event_add_notifier(C, NC_SCENE | ND_RENDER_RESULT, scene);
  WM_event_add_notifier(C, NC_IMAGE | NA_EDITED, ima);
  return OPERATOR_FINISHED;
}

void RENDER_OT_region_revert(wmOperatorType *ot)
{
  ot->name = "Revert Region";
  ot->idname = "RENDER_OT_region_revert";
  ot->description =
      "Put back the last render's pixels in the region rendered over it, or the region render "
      "again";

  ot->exec = region_revert_exec;
  ot->poll = region_revert_poll;
}

/** \} */

}  // namespace blender
