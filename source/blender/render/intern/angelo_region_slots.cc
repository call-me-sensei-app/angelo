/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup render
 *
 * Angelo: which render a region renders over (RE_angelo_region.hh). One rule for the operator,
 * its poll and the RNA the add-on and the agent read, so they never disagree after a render into
 * another slot, such as an agent's look.
 */

#include "BLI_listbase.h"
#include "BLI_threads.h"

#include "BKE_image.hh"
#include "BKE_main.hh"

#include "DNA_image_types.h"

#include "RE_angelo_region.hh"
#include "RE_pipeline.h"

#include "render_types.h"

namespace blender::render::angelo {

Image *region_render_result_image(Main *bmain)
{
  for (Image &ima : bmain->images) {
    if (ima.type == IMA_TYPE_R_RESULT) {
      return &ima;
    }
  }
  return nullptr;
}

bool region_render_shows_last(const Image *ima)
{
  return ima == nullptr || ima->render_slot == ima->last_render_slot;
}

/** The active slot's own render, when the Image Editor shows a slot other than the last render's.
 */
static RenderResult *shown_slot_render(Image *ima)
{
  if (region_render_shows_last(ima)) {
    return nullptr;
  }
  RenderSlot *slot = BKE_image_get_renderslot(ima, ima->render_slot);
  return slot ? slot->render : nullptr;
}

RenderResult *region_render_shown_result(Render *re, Image *ima)
{
  if (RenderResult *own = shown_slot_render(ima)) {
    return own;
  }
  return re ? re->result : nullptr;
}

bool region_render_shown_size(Render *re, Image *ima, int r_size[2])
{
  r_size[0] = r_size[1] = 0;
  /* A slot's own render is only changed on the main thread (render slots,
   * `BKE_image_backup_render`). */
  if (const RenderResult *own = shown_slot_render(ima)) {
    if (BLI_listbase_is_empty(&own->layers)) {
      return false;
    }
    r_size[0] = own->rectx;
    r_size[1] = own->recty;
  }
  else if (re != nullptr) {
    BLI_rw_mutex_lock(&re->resultmutex, THREAD_LOCK_READ);
    if (re->result && !BLI_listbase_is_empty(&re->result->layers)) {
      r_size[0] = re->result->rectx;
      r_size[1] = re->result->recty;
    }
    BLI_rw_mutex_unlock(&re->resultmutex);
  }
  return r_size[0] > 0 && r_size[1] > 0;
}

}  // namespace blender::render::angelo
