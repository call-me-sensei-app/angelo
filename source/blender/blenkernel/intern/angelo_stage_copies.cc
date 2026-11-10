/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup bke
 *
 * Stand-ins, plain local copies, the link closure and the stage scene test
 * (BKE_angelo_stage_links.hh).
 */

#include "BLI_set.hh"
#include "BLI_threads.h"
#include "BLI_utildefines.h"

#include "DNA_ID.h"
#include "DNA_anim_types.h"
#include "DNA_scene_types.h"

#include "BKE_anim_data.hh"
#include "BKE_angelo_stage_links.hh"
#include "BKE_idprop.hh"
#include "BKE_idtype.hh"
#include "BKE_lib_id.hh"
#include "BKE_lib_query.hh"
#include "BKE_main.hh"

namespace blender {

bool BKE_angelo_is_stage_scene(const Scene &scene)
{
  const IDProperty *tag = IDP_GetPropertyFromGroup_null(scene.id.properties,
                                                         "_angelo_program_stage");
  return tag && ((tag->type == IDP_STRING && tag->len > 1) ||
                 (ELEM(tag->type, IDP_BOOLEAN, IDP_INT) && IDP_int_or_bool_get(tag)));
}

bool BKE_angelo_stage_held(const Scene &scene)
{
  if (!BKE_angelo_is_stage_scene(scene)) {
    return false;
  }
  const IDProperty *hold = IDP_GetPropertyFromGroup_null(scene.id.properties, "_angelo_stage_hold");
  return !(hold && ELEM(hold->type, IDP_BOOLEAN, IDP_INT) && IDP_int_or_bool_get(hold) == 0);
}

bool BKE_angelo_stage_take_hold(Scene &scene)
{
  if (!BKE_angelo_is_stage_scene(scene)) {
    return false;
  }
  if (!BLI_thread_is_main() || BKE_angelo_stage_held(scene)) {
    return true; /* Off the main thread no check ran for it: held, the release left for its pass. */
  }
  IDProperty *hold = IDP_GetPropertyFromGroup(scene.id.properties, "_angelo_stage_hold");
  hold->data.val = 1; /* Released (an integer or boolean 0): consumed by this pass. */
  return false;
}

ID *BKE_angelo_stage_stand_in(Main &bmain, const ID &like)
{
  const short type = GS(like.name);
  if ((like.flag & ID_FLAG_EMBEDDED_DATA) ||
      ELEM(type, ID_LI, ID_WM, ID_WS, ID_SCR, ID_KE, ID_SCE))
  {
    return nullptr;
  }
  ID *stand_in = static_cast<ID *>(BKE_id_new(&bmain, type, like.name + 2));
  if (stand_in != nullptr) {
    /* As ID.copy() leaves a copy: its users are the pointers relinked to it. */
    id_us_min(stand_in);
  }
  return stand_in;
}

ID *BKE_angelo_stage_copy_plain(Main &bmain, const ID &id)
{
  if ((id.flag & ID_FLAG_EMBEDDED_DATA) || !BKE_idtype_idcode_is_linkable(GS(id.name))) {
    return nullptr;
  }
  ID *copy = BKE_id_copy_in_lib(&bmain,
                                std::optional<Library *>(nullptr),
                                &id,
                                std::nullopt,
                                nullptr,
                                LIB_ID_COPY_DEFAULT | LIB_ID_COPY_NO_LIB_OVERRIDE);
  if (copy != nullptr) {
    id_us_min(copy); /* As ID.copy(). */
  }
  return copy;
}

static bool animated(ID &id)
{
  const AnimData *adt = BKE_animdata_from_id(&id);
  return adt != nullptr &&
         (adt->action != nullptr || !adt->nla_tracks.is_empty() || !adt->drivers.is_empty());
}

bool BKE_angelo_stage_reach(Main &bmain,
                            const Span<ID *> roots,
                            const Span<ID *> exclude,
                            const int64_t limit,
                            Vector<ID *> &r_reached,
                            Vector<ID *> &r_animated,
                            std::string &r_error)
{
  r_reached.clear();
  r_animated.clear();
  if (!BKE_angelo_stage_in_main(bmain, roots)) {
    r_error = "an ID to walk is not in Main";
    return false;
  }
  Set<const ID *> excluded;
  for (const ID *id : exclude) {
    excluded.add(id);
  }
  Set<ID *> seen;
  Vector<ID *> queue;
  for (ID *root : roots) {
    if (!excluded.contains(root) && seen.add(root)) {
      queue.append(root);
    }
  }
  /* Embedded IDs are walked with their owner (foreach recurses into them) and listed, so their
   * own animation is seen; loop-backs to an owner are not followed. */
  for (int64_t i = 0; i < queue.size(); i++) {
    ID *id = queue[i];
    r_reached.append(id);
    if (r_reached.size() > limit) {
      return false;
    }
    if (animated(*id)) {
      r_animated.append(id);
    }
    BKE_library_foreach_ID_link(
        &bmain,
        id,
        [&](LibraryIDLinkCallbackData *data) {
          ID *target = *data->id_pointer;
          if (target == nullptr || (data->cb_flag & IDWALK_CB_LOOPBACK) ||
              excluded.contains(target))
          {
            return IDWALK_RET_NOP;
          }
          if (data->cb_flag & IDWALK_CB_EMBEDDED) {
            /* Listed for its animation; its own links are reported on this walk already. */
            if (seen.add(target)) {
              r_reached.append(target);
              if (animated(*target)) {
                r_animated.append(target);
              }
            }
            return IDWALK_RET_NOP;
          }
          if (seen.add(target)) {
            queue.append(target);
          }
          return IDWALK_RET_NOP;
        },
        nullptr,
        IDWALK_NOP);
  }
  return true;
}

}  // namespace blender
