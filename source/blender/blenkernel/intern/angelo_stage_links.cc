/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup bke
 *
 * Relinking, its journal and the read-only link walk (BKE_angelo_stage_links.hh).
 */

#include "BLI_listbase_iterator.hh"
#include "BLI_map.hh"
#include "BLI_set.hh"

#include "DNA_ID.h"

#include "BKE_angelo_stage_links.hh"
#include "BKE_lib_id.hh"
#include "BKE_lib_query.hh"
#include "BKE_lib_remap.hh"
#include "BKE_main.hh"

#include "DEG_depsgraph.hh"
#include "DEG_depsgraph_build.hh"

namespace blender {

/** The IDs of \a ids that are in \a bmain: one pass over the lists of the types they have. */
static Set<const ID *> present(Main &bmain, const Span<ID *> ids)
{
  Set<const ID *> wanted;
  Set<short> types;
  for (const ID *id : ids) {
    if (id != nullptr) {
      wanted.add(id);
      types.add(GS(id->name));
    }
  }
  Set<const ID *> found;
  for (const short type : types) {
    for (ID &id : *which_libbase(&bmain, type)) {
      if (wanted.contains(&id)) {
        found.add(&id);
      }
    }
  }
  return found;
}

bool BKE_angelo_stage_in_main(Main &bmain, const Span<ID *> ids)
{
  const Set<const ID *> found = present(bmain, ids);
  for (const ID *id : ids) {
    if (id == nullptr || !found.contains(id)) {
      return false;
    }
  }
  return true;
}

static const char *invalid_holder(const ID &id)
{
  /* A library override is local data: its pointers may be relinked like any other. */
  if (ID_IS_LINKED(&id)) {
    return "a linked ID cannot be relinked";
  }
  if (id.flag & ID_FLAG_EMBEDDED_DATA) {
    return "an embedded ID is relinked with its owner";
  }
  if (id.tag & ID_TAG_NO_MAIN) {
    return "an ID outside Main cannot be relinked";
  }
  return nullptr;
}

static const char *invalid_pair(const ID *old_id, const ID *new_id)
{
  if (old_id == nullptr || new_id == nullptr || old_id == new_id) {
    return "a relink pair needs two different IDs";
  }
  if (GS(old_id->name) != GS(new_id->name)) {
    return "a relink pair's IDs differ in type";
  }
  if ((old_id->flag & ID_FLAG_EMBEDDED_DATA) || (new_id->flag & ID_FLAG_EMBEDDED_DATA)) {
    return "an embedded ID is never relinked";
  }
  return nullptr;
}

/** Whether the remap callback (lib_remap.cc foreach_libblock_remap_callback) leaves this slot. */
static bool remap_skips(const LibraryIDLinkCallbackData &data)
{
  if (data.cb_flag & IDWALK_CB_EMBEDDED) {
    return true;
  }
  const bool self_embedded = (data.self_id->flag & ID_FLAG_EMBEDDED_DATA) != 0;
  return self_embedded && (data.cb_flag & IDWALK_CB_LOOPBACK) && *data.id_pointer == data.owner_id;
}

bool BKE_angelo_stage_links(
    Main &bmain,
    const Span<ID *> ids,
    const FunctionRef<void(ID *owner, ID *holder, ID *target, int flags)> fn,
    std::string &r_error)
{
  if (!BKE_angelo_stage_in_main(bmain, ids)) {
    r_error = "an ID to walk is not in Main";
    return false;
  }
  for (ID *id : ids) {
    BKE_library_foreach_ID_link(
        &bmain,
        id,
        [&](LibraryIDLinkCallbackData *data) {
          if (*data->id_pointer != nullptr) {
            fn(data->owner_id, data->self_id, *data->id_pointer, int(data->cb_flag));
          }
          return IDWALK_RET_NOP;
        },
        nullptr,
        IDWALK_NOP);
  }
  return true;
}

bool BKE_angelo_stage_relink(Main &bmain,
                             const Span<ID *> ids,
                             const Span<std::pair<ID *, ID *>> pairs,
                             Vector<ID *> &r_outside,
                             Vector<AngeloStageRelinkSlot> &r_journal,
                             std::string &r_error)
{
  r_outside.clear();
  r_journal.clear();
  Set<const ID *> holders;
  Vector<ID *> unique;
  for (ID *id : ids) {
    if (id == nullptr) {
      r_error = "a relinked ID is missing";
      return false;
    }
    if (const char *error = invalid_holder(*id)) {
      r_error = error;
      return false;
    }
    if (holders.add(id)) {
      unique.append(id);
    }
  }
  Map<ID *, ID *> mapping;
  Vector<ID *> paired;
  for (const std::pair<ID *, ID *> &pair : pairs) {
    if (const char *error = invalid_pair(pair.first, pair.second)) {
      r_error = error;
      return false;
    }
    if (!mapping.add(pair.first, pair.second)) {
      r_error = "an ID is named first by two relink pairs";
      return false;
    }
    paired.extend({pair.first, pair.second});
  }
  if (!BKE_angelo_stage_in_main(bmain, unique) || !BKE_angelo_stage_in_main(bmain, paired)) {
    r_error = "a relinked ID is not in Main";
    return false;
  }
  /* Validated before anything is written: a refusal leaves every ID as it was. */
  if (!mapping.is_empty()) {
    /* The journal: exactly the slots the remap below changes (its own skip rules). */
    for (ID *id : unique) {
      BKE_library_foreach_ID_link(
          &bmain,
          id,
          [&](LibraryIDLinkCallbackData *data) {
            ID *old_id = *data->id_pointer;
            ID *const *new_id = old_id ? mapping.lookup_ptr(old_id) : nullptr;
            if (new_id == nullptr || remap_skips(*data)) {
              return IDWALK_RET_NOP;
            }
            /* A never-self pointer remapped to its own holder is cleared (unassigned). */
            ID *target = (data->cb_flag & IDWALK_CB_NEVER_SELF) && *new_id == data->self_id ?
                             nullptr :
                             *new_id;
            r_journal.append({data->owner_id,
                              data->owner_id->session_uid,
                              data->id_pointer,
                              old_id,
                              target,
                              int(data->cb_flag)});
            return IDWALK_RET_NOP;
          },
          nullptr,
          IDWALK_NOP);
    }
    bke::id::IDRemapper remapper;
    for (const auto item : mapping.items()) {
      remapper.add(item.key, item.value);
    }
    /* ID_REMAP_SKIP_USER_CLEAR, as upstream relinks copies to their new IDs: a plain remap moves
     * an old ID's fake user to the new one and clears its extra user. The old IDs here are the
     * person's own (a stage only stops pointing at them), so their users stay theirs; without it
     * staging a mesh with a fake user cleared that flag on the person's mesh. */
    BKE_libblock_relink_multiple(
        &bmain, unique, ID_REMAP_TYPE_REMAP, remapper, ID_REMAP_SKIP_USER_CLEAR);
  }

  /* What the IDs still name outside themselves: the same pointers the remap visited. */
  Set<ID *> seen;
  std::string unused;
  BKE_angelo_stage_links(
      bmain,
      unique,
      [&](ID * /*owner*/, ID * /*holder*/, ID *target, int flags) {
        if (flags & (IDWALK_CB_EMBEDDED | IDWALK_CB_LOOPBACK)) {
          return;
        }
        if (holders.contains(target)) {
          return;
        }
        if ((target->flag & ID_FLAG_EMBEDDED_DATA) &&
            holders.contains(BKE_id_owner_get(target, false)))
        {
          return;
        }
        if (seen.add(target)) {
          r_outside.append(target);
        }
      },
      unused);
  return true;
}

int BKE_angelo_stage_unrelink(Main &bmain,
                              const Span<AngeloStageRelinkSlot> journal,
                              int &r_skipped)
{
  r_skipped = 0;
  /* Newest first, so a slot relinked twice ends at its first value. */
  Map<ID **, int64_t> newest;
  Vector<ID *> owners;
  Set<ID *> owner_set;
  for (const int64_t i : journal.index_range()) {
    newest.add_overwrite(journal[i].slot, i);
    if (owner_set.add(journal[i].owner)) {
      owners.append(journal[i].owner);
    }
  }
  /* Owners still in Main: only those are walked (their session UIDs are compared per slot). */
  const Set<const ID *> alive = present(bmain, owners);
  int restored = 0;
  Set<int64_t> done;
  for (ID *owner : owners) {
    if (!alive.contains(owner)) {
      continue;
    }
    BKE_library_foreach_ID_link(
        &bmain,
        owner,
        [&](LibraryIDLinkCallbackData *data) {
          const int64_t *index = newest.lookup_ptr(data->id_pointer);
          if (index == nullptr || done.contains(*index)) {
            return IDWALK_RET_NOP;
          }
          const AngeloStageRelinkSlot &entry = journal[*index];
          if (entry.owner != data->owner_id || entry.owner_uid != data->owner_id->session_uid ||
              *data->id_pointer != entry.new_id)
          {
            return IDWALK_RET_NOP;
          }
          *data->id_pointer = entry.old_id;
          if (entry.flags & IDWALK_CB_USER) {
            if (entry.new_id) {
              id_us_min(entry.new_id);
            }
            if (entry.old_id) {
              id_us_plus(entry.old_id);
            }
          }
          done.add(*index);
          restored++;
          return IDWALK_RET_NOP;
        },
        nullptr,
        IDWALK_NOP);
    DEG_id_tag_update(owner, ID_RECALC_SYNC_TO_EVAL);
  }
  r_skipped = int(newest.size()) - restored;
  if (restored) {
    DEG_relations_tag_update(&bmain);
  }
  return restored;
}

}  // namespace blender
