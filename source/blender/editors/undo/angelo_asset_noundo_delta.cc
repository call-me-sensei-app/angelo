/* SPDX-FileCopyrightText: 2026 Angelo Contributors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "angelo_asset_noundo_delta.hh"

#include <memory>
#include <optional>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "DNA_ID.h"
#include "DNA_asset_types.h"
#include "DNA_brush_types.h"
#include "DNA_scene_types.h"

#include "BLI_set.hh"
#include "BLI_threads.h"

#include "BKE_global.hh"
#include "BKE_idprop.hh"
#include "BKE_idtype.hh"
#include "BKE_lib_id.hh"
#include "BKE_lib_query.hh"
#include "BKE_main.hh"
#include "BKE_paint.hh"
#include "BKE_paint_types.hh"

namespace blender::ed::undo::asset {
namespace {
using UID = unsigned int;
using Index = std::unordered_map<UID, ID *>;
constexpr auto walk_flags = LibraryForeachIDFlag(IDWALK_INCLUDE_UI | IDWALK_DO_LIBRARY_POINTER);
constexpr PaintMode modes[] = {PaintMode::Sculpt, PaintMode::Vertex, PaintMode::Weight,
                              PaintMode::Texture3D, PaintMode::Texture2D, PaintMode::GPencil,
                              PaintMode::VertexGPencil, PaintMode::SculptGPencil,
                              PaintMode::WeightGPencil, PaintMode::SculptCurves};

bool fail(const char **error, const char *message)
{
  if (error) {
    *error = message;
  }
  return false;
}

bool current(Main &main, const char **error)
{
  return (BLI_thread_is_main() && G_MAIN == &main) ||
         fail(error, "Asset Brush Undo requires the current document on the main thread");
}

bool index_main(Main &main, Index &index, const char **error)
{
  ID *id;
  FOREACH_MAIN_ID_BEGIN (&main, id) {
    if (!id->session_uid || !index.emplace(id->session_uid, id).second) {
      return fail(error, "Asset Brush Undo found an invalid or duplicate native session UID");
    }
  }
  FOREACH_MAIN_ID_END;
  return true;
}

struct Reference {
  UID uid;
  short type;
  int flags;
  ID **template_slot;
};
struct Template {
  UID uid = 0;
  short type = 0;
  bool fake_user = false;
  short persistent_flags = 0;
  int linked_tags = 0;
  IDHash deep_hash = {};
  bool free_on_destroy = true;
  ID *id = nullptr;
  std::optional<LibraryWeakReference> weak_reference;
  std::vector<Reference> references;
  ~Template()
  {
    if (id && free_on_destroy) {
      /* Independent copies never own another top-level ID. Clear every external slot before
       * freeing a batch: another pending copy may already have been freed during unwind. */
      BKE_library_foreach_ID_link(
          nullptr, id,
          [](LibraryIDLinkCallbackData *data) {
            if (!(data->cb_flag & (IDWALK_CB_EMBEDDED | IDWALK_CB_LOOPBACK))) {
              *data->id_pointer = nullptr;
            }
            return IDWALK_RET_NOP;
          }, nullptr, walk_flags);
      BKE_id_free_ex(nullptr, id, LIB_ID_FREE_NO_MAIN | LIB_ID_FREE_NO_USER_REFCOUNT, false);
    }
  }
};
struct PaintReceipt {
  UID scene;
  PaintMode mode;
  UID brush;
};
Paint *existing_paint(Scene &scene, PaintMode mode)
{
  ToolSettings *ts = scene.toolsettings;
  if (!ts) {
    return nullptr;
  }
  /* The stock accessor assumes the mode's optional Paint has already been created. A receipt
   * must not create it, nor dereference an absent Sculpt/GPencil mode during an Object import. */
  switch (mode) {
    case PaintMode::Sculpt:
      if (!ts->sculpt) {
        return nullptr;
      }
      break;
    case PaintMode::Vertex:
      if (!ts->vpaint) {
        return nullptr;
      }
      break;
    case PaintMode::Weight:
      if (!ts->wpaint) {
        return nullptr;
      }
      break;
    case PaintMode::GPencil:
      if (!ts->gp_paint) {
        return nullptr;
      }
      break;
    case PaintMode::VertexGPencil:
      if (!ts->gp_vertexpaint) {
        return nullptr;
      }
      break;
    case PaintMode::SculptGPencil:
      if (!ts->gp_sculptpaint) {
        return nullptr;
      }
      break;
    case PaintMode::WeightGPencil:
      if (!ts->gp_weightpaint) {
        return nullptr;
      }
      break;
    case PaintMode::SculptCurves:
      if (!ts->curves_sculpt) {
        return nullptr;
      }
      break;
    default:
      break;
  }
  return BKE_paint_get_active_from_paintmode(&scene, mode);
}

void capture_paints(Main &main, std::vector<PaintReceipt> &receipts)
{
  for (Scene &scene : main.scenes) {
    for (PaintMode mode : modes) {
      if (Paint *paint = existing_paint(scene, mode)) {
        Brush *brush = BKE_paint_brush(paint);
        receipts.push_back({scene.id.session_uid, mode, brush ? brush->id.session_uid : 0});
      }
    }
  }
}

bool external_slot(const LibraryIDLinkCallbackData &data)
{
  return !(data.cb_flag & (IDWALK_CB_EMBEDDED | IDWALK_CB_LOOPBACK));
}

/* Retain exact native template slot addresses. The callback sequence is NOT assignment
 * authority: some stock walkers conditionally omit null slots. */
bool strip_template(Template &item, const Index &index, const char **error)
{
  BLI_assert(item.id->tag & ID_TAG_NO_MAIN);
  /* NO_USER_REFCOUNT is this private copy's cleanup policy, not the declared role of
   * its pointer slots. The native walker otherwise hides USER/USER_ONE, including
   * null slots, while Main insertion restores those roles before Redo validation.
   * Only this unpublished copy changes policy, and the callback never adjusts users. */
  struct RestoreRefcountPolicy {
    ID &id;
    int saved_no_user_refcount;
    ~RestoreRefcountPolicy() noexcept
    {
      id.tag = (id.tag & ~ID_TAG_NO_USER_REFCOUNT) | saved_no_user_refcount;
    }
  } restore{*item.id, item.id->tag & ID_TAG_NO_USER_REFCOUNT};
  item.id->tag &= ~ID_TAG_NO_USER_REFCOUNT;

  bool valid = true;
  BKE_library_foreach_ID_link(
      nullptr, item.id,
      [&](LibraryIDLinkCallbackData *data) {
        if (!external_slot(*data)) {
          return IDWALK_RET_NOP;
        }
        ID *target = *data->id_pointer;
        if (target && target != item.id && (!index.contains(target->session_uid) ||
                       index.at(target->session_uid) != target))
        {
          valid = false;
        }
        item.references.push_back(
            {target == item.id ? item.uid : target ? target->session_uid : 0,
             target ? short(GS(target->name)) : short(0),
             int(data->cb_flag & ~IDWALK_CB_INDIRECT_USAGE), data->id_pointer});
        *data->id_pointer = nullptr;
        return IDWALK_RET_NOP;
      }, nullptr, walk_flags);
  return valid || fail(error, "Asset Brush template has a reference outside the native Main");
}

/* A template is immutable outside this small stock-copy window. Its exact native slots remain
 * alive with the copied ID; no offset, hash, or callback ordinal locates an assignment. */
class TemplateBindings {
  Template &item_;
 public:
  explicit TemplateBindings(Template &item) : item_(item) {}
  bool bind(const Index &index, const char **error)
  {
    for (const Reference &ref : item_.references) {
      ID *target = ref.uid && index.contains(ref.uid) ? index.at(ref.uid) : nullptr;
      if (ref.uid && (!target || GS(target->name) != ref.type)) {
        return fail(error, "Asset Brush template dependency is absent from the target boundary");
      }
      *ref.template_slot = target;
    }
    return true;
  }
  ~TemplateBindings()
  {
    for (const Reference &ref : item_.references) {
      *ref.template_slot = nullptr;
    }
  }
};

bool remap_clone(ID &id, const Index &index, const char **error)
{
  bool valid = true;
  BKE_library_foreach_ID_link(
      nullptr, &id,
      [&](LibraryIDLinkCallbackData *data) {
        if (!external_slot(*data) || !*data->id_pointer) {
          return IDWALK_RET_NOP;
        }
        ID *old = *data->id_pointer;
        const auto found = index.find(old->session_uid);
        if (found == index.end() || GS(found->second->name) != GS(old->name)) {
          valid = false;
        }
        else {
          *data->id_pointer = found->second;
        }
        return IDWALK_RET_NOP;
      }, nullptr, walk_flags);
  return valid || fail(error, "Asset Brush clone could not resolve a native UID reference");
}

/* Validation only. Stock memfile read remaps retained no-undo references by native UID.
 * A changed callback layout refuses; it never assigns through a guessed ordinal. */
bool validate_references(ID &id, const Template &item, const char **error)
{
  size_t slot = 0;
  bool valid = true;
  BKE_library_foreach_ID_link(
      nullptr, &id,
      [&](LibraryIDLinkCallbackData *data) {
        if (!external_slot(*data)) {
          return IDWALK_RET_NOP;
        }
        if (slot >= item.references.size()) {
          valid = false;
          return IDWALK_RET_NOP;
        }
        const Reference &ref = item.references[slot++];
        ID *target = *data->id_pointer;
        if (ref.flags != int(data->cb_flag & ~IDWALK_CB_INDIRECT_USAGE) ||
            (target ? target->session_uid : 0) != ref.uid ||
            (target && GS(target->name) != ref.type))
        {
          valid = false;
        }
        return IDWALK_RET_NOP;
      }, nullptr, LibraryForeachIDFlag(walk_flags | IDWALK_READONLY));
  return (valid && slot == item.references.size()) ||
         fail(error, "Asset Brush Undo has incomplete native reference reconstruction");
}

}  // namespace

struct AssetNoUndoDelta {
  size_t references = 1;
  std::unordered_set<UID> baseline;
  std::unordered_set<UID> introduced_brushes;
  std::vector<std::unique_ptr<Template>> templates;
  std::vector<PaintReceipt> before_paints;
  std::vector<PaintReceipt> after_paints;
  bool after_complete = false;
  /* Published before the first fallible native insertion. An uncertain batch must outlive
   * every step/capture release: some boxes may already be linked or referenced from Main. */
  std::vector<std::unique_ptr<Template>> pending_clones;
  bool insertion_uncertain = false;
};

AssetNoUndoDelta *asset_noundo_delta_prepare(Main &main, const char **error)
try
{
  if (!current(main, error)) {
    return nullptr;
  }
  auto delta = std::make_unique<AssetNoUndoDelta>();
  Index index;
  if (!index_main(main, index, error)) {
    return nullptr;
  }
  for (const auto &[uid, id] : index) {
    delta->baseline.insert(uid);
  }
  capture_paints(main, delta->before_paints);
  return delta.release();
}
catch (...)
{
  fail(error, "Asset Brush Undo encountered a native allocation or callback failure");
  return nullptr;
}

AssetNoUndoDelta *asset_noundo_delta_retain(AssetNoUndoDelta *delta)
{
  if (delta) {
    BLI_assert(BLI_thread_is_main());
    ++delta->references;
  }
  return delta;
}
void asset_noundo_delta_free(AssetNoUndoDelta *delta)
{
  if (delta) {
    BLI_assert(BLI_thread_is_main());
    if (--delta->references == 0) {
      if (delta->insertion_uncertain) {
        /* Recovery owner, intentionally not released. No guessed free of possibly linked IDs.
         * There is no retry/automatic cleanup contract for this exceptional native outcome. */
        delta->references = 1;
        return;
      }
      delete delta;
    }
  }
}

bool asset_noundo_delta_capture_after(AssetNoUndoDelta &delta, Main &main, const char **error)
try
{
  if (!current(main, error) || delta.after_complete) {
    return fail(error, "Asset Brush after-state was already captured or is not current");
  }
  Index index;
  if (!index_main(main, index, error)) {
    return false;
  }
  std::vector<ID *> queue;
  std::unordered_set<UID> visited;
  for (Brush &brush : main.brushes) {
    if (!delta.baseline.contains(brush.id.session_uid)) {
      delta.introduced_brushes.insert(brush.id.session_uid);
      queue.push_back(&brush.id);
    }
  }
  /* Follow stock no-undo dependencies. New ordinary dependencies also need independent templates
   * so the Brush can be recreated with valid references before the After memfile is read. The
   * normal decoder subsequently replaces ordinary IDs; baseline dependencies are never copied. */
  for (size_t i = 0; i < queue.size(); ++i) {
    ID *id = queue[i];
    if (!visited.insert(id->session_uid).second) {
      continue;
    }
    BKE_library_foreach_ID_link(
        &main, id,
        [&](LibraryIDLinkCallbackData *data) {
          ID *target = *data->id_pointer;
          if (target && external_slot(*data)) {
            queue.push_back(target);
          }
          return IDWALK_RET_NOP;
        }, nullptr, LibraryForeachIDFlag(walk_flags | IDWALK_READONLY));
  }
  std::vector<std::unique_ptr<Template>> templates;
  for (UID uid : visited) {
    ID *id = index.at(uid);
    if (delta.baseline.contains(uid)) {
      continue;
    }
    /* Match AssetMetaData's actual RNA contract (STRUCT_NO_DATABLOCK_IDPROPERTIES).
     * Its stock copy/free do user-count ID properties, but the global ID walker deliberately
     * does not visit them. Refuse this unsupported native state before copying touches users. */
    bool metadata_has_id = false;
    if (id->asset_data && id->asset_data->properties) {
      IDP_foreach_property(id->asset_data->properties, 0, [&](IDProperty *property) {
        metadata_has_id |= property->type == IDP_ID;
      });
    }
    if (metadata_has_id) {
      return fail(error, "Asset metadata contains a datablock pointer forbidden by its native contract");
    }
    auto item = std::make_unique<Template>();
    item->uid = uid;
    item->type = GS(id->name);
    item->fake_user = (id->flag & ID_FLAG_FAKEUSER) != 0;
    item->persistent_flags = id->flag;
    item->linked_tags = id->tag & (ID_TAG_EXTERN | ID_TAG_INDIRECT | ID_TAG_MISSING);
    item->deep_hash = id->deep_hash;
    if (id->library_weak_reference) {
      item->weak_reference = *id->library_weak_reference;
    }
    item->id = BKE_id_copy_ex(nullptr, id, nullptr,
                             LIB_ID_CREATE_NO_MAIN | LIB_ID_CREATE_NO_USER_REFCOUNT |
                                 LIB_ID_CREATE_NO_DEG_TAG | LIB_ID_COPY_ASSET_METADATA);
    if (!item->id || !strip_template(*item, index, error)) {
      return fail(error, "Asset Brush Undo could not create an independent native template");
    }
    item->id->session_uid = item->uid;
    item->id->flag = short((item->id->flag & ID_FLAG_FAKEUSER) |
                          (item->persistent_flags & ~ID_FLAG_FAKEUSER));
    item->id->tag |= item->linked_tags;
    item->id->deep_hash = item->deep_hash;
    templates.push_back(std::move(item));
  }
  std::vector<PaintReceipt> paints;
  capture_paints(main, paints);
  delta.templates = std::move(templates);
  delta.after_paints = std::move(paints);
  delta.after_complete = true;
  return true;
}
catch (...)
{
  fail(error, "Asset Brush Undo encountered a native allocation or callback failure");
  return false;
}

namespace {
bool remove_created(AssetNoUndoDelta &delta, Main &main, bool brushes_only,
                    const char **error)
{
  Index index;
  if (!index_main(main, index, error)) {
    return false;
  }
  Set<ID *> selected;
  /* Also works when capture_after failed before making any templates. */
  for (Brush &brush : main.brushes) {
    if ((delta.after_complete ? delta.introduced_brushes.contains(brush.id.session_uid) :
                                !delta.baseline.contains(brush.id.session_uid))) {
      delta.introduced_brushes.insert(brush.id.session_uid);
      selected.add(&brush.id);
    }
  }
  if (!brushes_only) {
    for (const auto &item : delta.templates) {
      if (!delta.baseline.contains(item->uid) && index.contains(item->uid)) {
        if (GS(index.at(item->uid)->name) != item->type) {
          return fail(error, "Asset dependency UID no longer identifies its captured native type");
        }
        selected.add(index.at(item->uid));
      }
    }
  }
  /* Protect baseline IDs and all outside users. Deletion must not cascade into a preserved
   * never-null owner. Dependencies still used outside the creation closure remain alive. */
  bool changed;
  do {
    changed = false;
    for (const auto &[uid, id] : index) {
      if (selected.contains(id)) {
        continue;
      }
      BKE_library_foreach_ID_link(
          &main, id,
          [&](LibraryIDLinkCallbackData *data) {
            ID *target = *data->id_pointer;
            if (!target || !external_slot(*data) || !selected.contains(target)) {
              return IDWALK_RET_NOP;
            }
            if (GS(target->name) != ID_BR || (data->cb_flag & IDWALK_CB_NEVER_NULL)) {
              selected.remove(target);
              changed = true;
            }
            return IDWALK_RET_NOP;
          }, nullptr, LibraryForeachIDFlag(walk_flags | IDWALK_READONLY));
    }
  } while (changed);
  for (Brush &brush : main.brushes) {
    if (delta.introduced_brushes.contains(brush.id.session_uid) && !selected.contains(&brush.id)) {
      return fail(error, "A new Brush has a preserved never-null owner; Undo requires recovery");
    }
  }
  if (!selected.is_empty()) {
    BKE_id_multi_delete(&main, selected);
  }
  return true;
}

bool recreate(AssetNoUndoDelta &delta, Main &main, const char **error)
{
  if (!delta.after_complete) {
    return fail(error, "Asset Brush After templates are incomplete");
  }
  Index index;
  if (!index_main(main, index, error)) {
    return false;
  }
  Index source_index = index;
  for (const auto &item : delta.templates) {
    if (!source_index.contains(item->uid)) {
      source_index.emplace(item->uid, item->id);
    }
  }
  std::vector<std::unique_ptr<Template>> pending;
  for (const auto &item : delta.templates) {
    if (index.contains(item->uid)) {
      if (GS(index.at(item->uid)->name) != item->type) {
        return fail(error, "Asset Brush UID was reused by a different ID type");
      }
      continue;
    }
    TemplateBindings bindings(*item);
    if (!bindings.bind(source_index, error)) {
      return false;
    }
    auto clone = std::make_unique<Template>();
    clone->id = BKE_id_copy_ex(nullptr, item->id, nullptr,
                              LIB_ID_CREATE_NO_MAIN | LIB_ID_CREATE_NO_USER_REFCOUNT |
                                  LIB_ID_CREATE_NO_DEG_TAG | LIB_ID_COPY_ASSET_METADATA);
    if (!clone->id) {
      return fail(error, "Asset Brush Undo could not recreate a native template");
    }
    clone->uid = item->uid;
    clone->type = item->type;
    clone->id->session_uid = item->uid;
    clone->id->flag = short((clone->id->flag & ID_FLAG_FAKEUSER) |
                           (item->persistent_flags & ~ID_FLAG_FAKEUSER));
    clone->id->tag |= item->linked_tags;
    clone->id->deep_hash = item->deep_hash;
    if (item->fake_user) {
      id_fake_user_set(clone->id);
    }
    else {
      id_fake_user_clear(clone->id);
    }
    if (item->weak_reference) {
      BKE_main_library_weak_reference_add(clone->id,
                                        item->weak_reference->library_filepath,
                                        item->weak_reference->library_id_name);
    }
    index.emplace(item->uid, clone->id);
    pending.push_back(std::move(clone));
  }
  /* All new IDs now have real independent native boxes. Rebind using their actual UID,
   * not a callback ordinal or a pointer into an old Main. */
  for (const auto &clone : pending) {
    if (!remap_clone(*clone->id, index, error)) {
      return false;
    }
  }
  /* Prepublish the entire batch in the step/capture-owned delta before any native Main link.
   * Insertion can fail after linking; no local destructor may then free an unknown-linked box. */
  delta.pending_clones = std::move(pending);
  delta.insertion_uncertain = !delta.pending_clones.empty();
  for (auto &clone : delta.pending_clones) {
    clone->free_on_destroy = false;
  }
  /* Library boxes must exist in Main before adding their linked IDs. */
  for (const bool libraries : {true, false}) {
    for (auto &clone : delta.pending_clones) {
      if (clone->id && (clone->type == ID_LI) == libraries) {
        BKE_libblock_management_main_add(&main, clone->id);
        clone->id = nullptr;
      }
    }
  }
  delta.pending_clones.clear();
  delta.insertion_uncertain = false;
  return true;
}

bool restore_paints(AssetNoUndoDelta &delta, Main &main, AssetNoUndoSide side,
                    const Index &index, const char **error)
{
  const auto &receipts = side == AssetNoUndoSide::Before ? delta.before_paints : delta.after_paints;
  for (const PaintReceipt &receipt : receipts) {
    bool affected = delta.introduced_brushes.contains(receipt.brush);
    if (side == AssetNoUndoSide::Before) {
      for (const PaintReceipt &after : delta.after_paints) {
        affected |= after.scene == receipt.scene && after.mode == receipt.mode &&
                    delta.introduced_brushes.contains(after.brush);
      }
    }
    if (!affected || !index.contains(receipt.scene)) {
      continue;
    }
    ID *scene_id = index.at(receipt.scene);
    ID *brush_id = receipt.brush && index.contains(receipt.brush) ? index.at(receipt.brush) : nullptr;
    if (GS(scene_id->name) != ID_SCE || (receipt.brush && !brush_id) ||
        (brush_id && GS(brush_id->name) != ID_BR))
    {
      return fail(error, "Asset Brush Undo cannot resolve the target paint selection");
    }
    Paint *paint = existing_paint(*reinterpret_cast<Scene *>(scene_id), receipt.mode);
    Brush *brush = reinterpret_cast<Brush *>(brush_id);
    if (!paint || (brush && !paint->runtime) || !BKE_paint_can_use_brush(paint, brush) ||
        !BKE_paint_brush_set(paint, brush))
    {
      return fail(error, "Asset Brush Undo cannot restore a usable paint-mode Brush");
    }
  }
  return true;
}
}  // namespace

bool asset_noundo_delta_before_decode(AssetNoUndoDelta &delta, Main &main,
                                     AssetNoUndoSide side, const char **error)
try
{
  if (!current(main, error) || delta.insertion_uncertain) {
    return fail(error, "Asset Brush native insertion remains uncertain; recovery is required");
  }
  if (side == AssetNoUndoSide::Before && !delta.after_complete) {
    delta.after_paints.clear();
    capture_paints(main, delta.after_paints);
  }
  return side == AssetNoUndoSide::Before ? remove_created(delta, main, true, error) :
                                         recreate(delta, main, error);
}
catch (...)
{
  fail(error, "Asset Brush Undo encountered a native allocation or callback failure");
  return false;
}

bool asset_noundo_delta_after_decode(AssetNoUndoDelta &delta, Main &main,
                                    AssetNoUndoSide side, const char **error)
try
{
  if (!current(main, error) || delta.insertion_uncertain) {
    return fail(error, "Asset Brush native insertion remains uncertain; recovery is required");
  }
  if (side == AssetNoUndoSide::Before && !remove_created(delta, main, false, error)) {
    return false;
  }
  Index index;
  if (!index_main(main, index, error)) {
    return false;
  }
  if (side == AssetNoUndoSide::Before) {
    for (const auto &[uid, id] : index) {
      if (!delta.baseline.contains(uid) &&
          (BKE_idtype_get_info_from_id(id)->flags & IDTYPE_FLAGS_NO_MEMFILE_UNDO))
      {
        return fail(error, "A newly created non-memfile ID remains; native recovery is required");
      }
    }
  }
  if (side == AssetNoUndoSide::After) {
    for (const auto &item : delta.templates) {
      if (!index.contains(item->uid)) {
        return fail(error, "Asset Brush After-state is missing a captured ID");
      }
      if (GS(index.at(item->uid)->name) != item->type) {
        return fail(error, "Asset Brush After-state ID type changed");
      }
      if (!validate_references(*index.at(item->uid), *item, error)) {
        return false; /* Preserve the actual reference-reconstruction refusal. */
      }
    }
  }
  return restore_paints(delta, main, side, index, error);
}
catch (...)
{
  fail(error, "Asset Brush Undo encountered a native allocation or callback failure");
  return false;
}


}  // namespace blender::ed::undo::asset
