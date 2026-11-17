/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Angelo (doc/angelo/script_confinement.rst, "Python in the project"): data an agent script brings in by linking or
 * appending carries no Python that would run later. Whatever the route (`bpy.data.libraries.load`, the Append and
 * Link operators, an asset drop, a paste) it passes through #BKE_blendfile_link and #BKE_blendfile_append; when a
 * confined thread runs them, every data-block that is new afterwards is disarmed on entry, by the same rule as at
 * set time:
 *
 * - a driver whose expression needs Python keeps its expression but not its Python
 *   (#DRIVER_FLAG_ANGELO_UNTRUSTED, "Python restricted for security" until the person edits it), in the
 *   data-block's embedded IDs too (#BKE_angelo_foreach_animdata);
 * - an appended text set to run at load stops running at load, and an appended style module is switched off;
 * - a linked text set to run at load, or a linked scene with a style module on, is removed and its link reported as
 *   not loaded: its code would come back from the library file at every open, where no flag could hold.
 *
 * Code the script left and resumed after its run (a generator) is confined by its frames, so this holds for it too.
 */

#include "DNA_anim_types.h"
#include "DNA_freestyle_types.h"
#include "DNA_layer_types.h"
#include "DNA_scene_types.h"
#include "DNA_space_types.h"
#include "DNA_text_types.h"

#include "BLI_fileops.hh"
#include "BLI_listbase_iterator.hh"
#include "BLI_set.hh"
#include "BLI_vector.hh"

#include "BKE_anim_data.hh"
#include "BKE_blendfile_link_append.hh"
#include "BKE_fcurve_driver.h"
#include "BKE_lib_id.hh"
#include "BKE_lib_query.hh"
#include "BKE_main.hh"

namespace blender {

void BKE_angelo_foreach_animdata(Main *bmain, ID *id, FunctionRef<void(ID *self, AnimData *animation)> fn)
{
  if (AnimData *animation = BKE_animdata_from_id(id)) {
    fn(id, animation);
  }
  /* The ID walk reaches every embedded ID the data-block owns, however deep (a material's node tree, a scene's
   * compositing tree, a world's, a light's, a texture's, a line style's): no list of where they live. */
  BKE_library_foreach_ID_link(
      bmain,
      id,
      [&](LibraryIDLinkCallbackData *cb_data) {
        ID *embedded = *cb_data->id_pointer;
        if ((cb_data->cb_flag & IDWALK_CB_EMBEDDED) && embedded != nullptr) {
          if (AnimData *animation = BKE_animdata_from_id(embedded)) {
            fn(embedded, animation);
          }
        }
        return IDWALK_RET_NOP;
      },
      nullptr,
      IDWALK_READONLY);
}

static Main *main_of(BlendfileLinkAppendContext *lapp_context)
{
  return lapp_context->params->bmain;
}

void BKE_blendfile_link_append_angelo_begin(BlendfileLinkAppendContext *lapp_context)
{
  const BLI_FileGate *gate = BLI_fileops_gate_get();
  lapp_context->angelo_before.reset();
  if (!(gate && gate->confined && gate->confined())) {
    return;
  }
  Set<uint32_t> before;
  ID *id;
  FOREACH_MAIN_ID_BEGIN (main_of(lapp_context), id) {
    before.add(id->session_uid);
  }
  FOREACH_MAIN_ID_END;
  lapp_context->angelo_before = std::move(before);
}

static bool runs_style_module(const Scene *scene)
{
  for (const ViewLayer &view_layer : scene->view_layers) {
    for (const FreestyleModuleConfig &module : view_layer.freestyle_config.modules) {
      if (module.script != nullptr && module.is_displayed) {
        return true;
      }
    }
  }
  return false;
}

void BKE_blendfile_link_append_angelo_end(BlendfileLinkAppendContext *lapp_context, const bool appending)
{
  /* The link step of an append: the append that follows makes its data local, and disarms at its own end. */
  const bool appends_next = !appending && (lapp_context->params->flag & FILE_LINK) == 0;
  if (!lapp_context->angelo_before || appends_next) {
    return;
  }
  const std::optional<Set<uint32_t>> before = std::move(lapp_context->angelo_before);
  lapp_context->angelo_before.reset();
  Main *bmain = main_of(lapp_context);
  Vector<ID *> removed;
  ID *id;
  FOREACH_MAIN_ID_BEGIN (bmain, id) {
    if (before->contains(id->session_uid)) {
      continue;
    }
    BKE_angelo_foreach_animdata(bmain, id, [](ID * /*self*/, AnimData *animation) {
      for (FCurve &fcurve : animation->drivers) {
        ChannelDriver *driver = fcurve.driver;
        if (driver && driver->type == DRIVER_TYPE_PYTHON && driver->expression[0] != '\0' &&
            !BKE_driver_has_simple_expression(driver))
        {
          driver->flag |= DRIVER_FLAG_ANGELO_UNTRUSTED;
        }
      }
    });
    if (GS(id->name) == ID_TXT && (reinterpret_cast<Text *>(id)->flags & TXT_ISSCRIPT)) {
      if (ID_IS_LINKED(id)) {
        removed.append(id);
      }
      else {
        reinterpret_cast<Text *>(id)->flags &= ~TXT_ISSCRIPT;
      }
    }
    if (GS(id->name) == ID_SCE && runs_style_module(reinterpret_cast<Scene *>(id))) {
      if (ID_IS_LINKED(id)) {
        removed.append(id);
      }
      else {
        for (ViewLayer &view_layer : reinterpret_cast<Scene *>(id)->view_layers) {
          for (FreestyleModuleConfig &module : view_layer.freestyle_config.modules) {
            module.is_displayed = 0;
          }
        }
      }
    }
  }
  FOREACH_MAIN_ID_END;
  if (removed.is_empty()) {
    return;
  }
  /* The caller hands its items' new data-blocks back (to Python's lists): a removed one reads as not loaded. */
  for (BlendfileLinkAppendContextItem &item : lapp_context->items) {
    if (item.new_id != nullptr && removed.contains(item.new_id)) {
      lapp_context->new_id_to_item.remove(item.new_id);
      item.new_id = nullptr;
    }
  }
  BKE_main_id_tag_all(bmain, ID_TAG_DOIT, false);
  for (ID *gone : removed) {
    gone->tag |= ID_TAG_DOIT;
  }
  BKE_id_multi_tagged_delete(bmain);
}

}  // namespace blender
