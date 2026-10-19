/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup edundo
 *
 * A typed commit changes Main, even when the artist is using Sculpt's incremental Undo.
 * Save that incremental state before the commit. Hide the baseline only when the previous
 * incremental step remains an exact destination; otherwise retain a visible before-edit state.
 * Encoding both snapshots before changing the stack also preserves redo on a refused commit.
 */

#include "MEM_guardedalloc.h"

#include <cstring>
#include <limits>
#include <string>
#include <utility>

#include "BLI_listbase.h"
#include "BLI_set.hh"
#include "BLI_string.h"
#include "BLI_threads.h"
#include "BLI_vector.hh"

#include "DNA_object_types.h"
#include "DNA_scene_types.h"

#include "BKE_blender_undo.hh"
#include "BKE_context.hh"
#include "BKE_global.hh"
#include "BKE_layer.hh"
#include "BKE_lib_override.hh"
#include "BKE_lib_query.hh"
#include "BKE_main.hh"
#include "BKE_object.hh"
#include "BKE_object_types.hh"
#include "BKE_paint.hh"
#include "BKE_sculpt_undo.hh"
#include "BKE_scene.hh"
#include "BKE_undo_system.hh"

#include "BLO_undofile.hh"
#include "BLO_writefile.hh"

#include "../sculpt_paint/mesh/sculpt_main_undo_grids.hh"
#include "../sculpt_paint/mesh/sculpt_main_undo_snapshot_access.hh"
#include "../../windowmanager/intern/wm_checkpoint.hh"

#include "ED_sculpt.hh"
#include "ED_undo.hh"

#include "RNA_access.hh"
#include "WM_api.hh"
#include "WM_types.hh"

#include "undo_intern.hh"

namespace blender {

struct AngeloUndoCapture {
  bContext *context = nullptr;
  Main *main = nullptr;
  wmWindowManager *wm = nullptr;
  wmWindow *window = nullptr;
  UndoStack *stack = nullptr;
  UndoStep *active = nullptr;
  MemFileUndoStep *before = nullptr;
  UndoCaptureScope *guard = nullptr;
  uint scene_uid = 0;
  Set<uint> artist_scene_uids;
  bool operation_active = false;
  bool finished = false;
};

/** Restore writer bookkeeping on original and embedded IDs after detached capture. */
class RecalcRestore {
  struct Saved {
    ID *id;
    uint before;
    uint after;
  };
  Vector<Saved> saved_;
  Set<ID *> seen_;

  void remember(ID *id)
  {
    if (id && seen_.add(id)) {
      saved_.append({id, id->recalc_up_to_undo_push, id->recalc_after_undo_push});
    }
  }

 public:
  explicit RecalcRestore(Main *bmain)
  {
    ID *id;
    FOREACH_MAIN_ID_BEGIN (bmain, id) {
      remember(id);
      BKE_library_foreach_ID_link(
          bmain,
          id,
          [this](LibraryIDLinkCallbackData *data) {
            if (data->cb_flag & IDWALK_CB_EMBEDDED) {
              remember(*data->id_pointer);
            }
            return IDWALK_RET_NOP;
          },
          nullptr,
          IDWALK_READONLY);
    }
    FOREACH_MAIN_ID_END;
  }
  void keep()
  {
    saved_.clear();
  }
  ~RecalcRestore()
  {
    for (const Saved &saved : saved_) {
      saved.id->recalc_up_to_undo_push = saved.before;
      saved.id->recalc_after_undo_push = saved.after;
    }
  }
};

static void detached_free(MemFileUndoStep *step)
{
  if (step) {
    ed::sculpt_paint::undo::main_undo_state_free(step->angelo_sculpt_state);
    BKE_memfile_undo_free(step->data);
    MEM_delete(step);
  }
}

/** Equality of encoded Main, not of editor runtime or the cost of owning these chunks. */
static bool memfile_contents_equal(const MemFile &a, const MemFile &b)
{
  const MemFileChunk *x = static_cast<const MemFileChunk *>(a.chunks.first);
  const MemFileChunk *y = static_cast<const MemFileChunk *>(b.chunks.first);
  while (x && y) {
    if (x->id_session_uid != y->id_session_uid || x->size != y->size ||
        (x->buf != y->buf && x->size != 0 && std::memcmp(x->buf, y->buf, x->size) != 0))
    {
      return false;
    }
    x = static_cast<const MemFileChunk *>(x->next);
    y = static_cast<const MemFileChunk *>(y->next);
  }
  if (x || y) {
    return false;
  }
  const int64_t a_shared = a.shared_storage ? a.shared_storage->sharing_info_by_address_id.size() :
                                              0;
  const int64_t b_shared = b.shared_storage ? b.shared_storage->sharing_info_by_address_id.size() :
                                              0;
  if (a_shared != b_shared) {
    return false;
  }
  if (a_shared) {
    for (const auto &item : a.shared_storage->sharing_info_by_address_id.items()) {
      const ImplicitSharingInfoAndData *other =
          b.shared_storage->sharing_info_by_address_id.lookup_ptr(item.key);
      /* Both MemFiles hold strong references. Matching immutable owners and data addresses
       * therefore proves equality without rereading shared geometry buffers. */
      if (!other || item.value.sharing_info != other->sharing_info ||
          item.value.data != other->data)
      {
        return false;
      }
    }
  }
  return true;
}

static bool captured_states_equal(const MemFileUndoStep &a, const MemFileUndoStep &b)
{
  /* An untagged MemFile has no provenance for live Sculpt geometry. Equal raw Main may hold
   * stale Mesh data while its BMesh has changed; never enrich or replace that old boundary. */
  return a.angelo_sculpt_state && b.angelo_sculpt_state &&
         memfile_contents_equal(a.data->memfile, b.data->memfile) &&
         ed::sculpt_paint::undo::main_undo_state_equals(*a.angelo_sculpt_state,
                                                        *b.angelo_sculpt_state);
}

static MemFileUndoStep *capture_memfile(Main *bmain,
                                        MemFileUndoStep *previous,
                                        const bool keep_bookkeeping,
                                        const char **r_error)
{
  ed::sculpt_paint::undo::MainUndoState *sculpt_state =
      ed::sculpt_paint::undo::main_undo_state_capture(*bmain, r_error);
  if (*r_error) {
    return nullptr;
  }
  /* Artist geometry and pending flush flags remain untouched. Each runtime adjunct owns the
   * state absent from Main; only the serializer's bookkeeping is restored on abandon/failure. */
  RecalcRestore recalc_restore(bmain);
  MemFile *reference = previous ? &previous->data->memfile : nullptr;
  Vector<bool> future;
  if (reference) {
    for (MemFileChunk &chunk : reference->chunks) {
      future.append(chunk.is_identical_future);
      chunk.is_identical_future = false;
    }
  }

  MemFileUndoStep *step = MEM_new_zeroed<MemFileUndoStep>(__func__);
  step->angelo_sculpt_state = sculpt_state;
  step->step.type = BKE_UNDOSYS_TYPE_MEMFILE;
  step->step.use_old_bmain_data = !bmain->use_memfile_full_barrier;
  STRNCPY(step->step.name, "Commit Validated Native Program");
  step->data = MEM_new_zeroed<MemFileUndoData>(__func__);
  const bool ok = BLO_write_file_mem(
      bmain, reference, &step->data->memfile, G.fileflags | G_FILE_RECOVER_WRITE);
  const size_t sculpt_bytes = sculpt_state ? ed::sculpt_paint::undo::main_undo_state_memory_size(
                                                 *sculpt_state) :
                                             0;
  if (!ok || sculpt_bytes == std::numeric_limits<size_t>::max() ||
      sculpt_bytes > std::numeric_limits<size_t>::max() - step->data->memfile.size)
  {
    if (reference) {
      int i = 0;
      for (MemFileChunk &chunk : reference->chunks) {
        chunk.is_identical_future = future[i++];
      }
    }
    detached_free(step);
    *r_error = "Could not encode the native commit Undo snapshot";
    return nullptr;
  }
  step->data->undo_size = step->data->memfile.size;
  step->step.data_size = step->data->undo_size + sculpt_bytes;
  if (keep_bookkeeping) {
    recalc_restore.keep();
  }
  return step;
}

class InitialCheckpointRecalcRestore {
  struct Saved { ID *id; uint before, after; };
  Vector<Saved> saved_;
  size_t charged_ = 0;
  int64_t expected_ = 0;
  bool valid_ = false;
  void remember(ID *id)
  {
    if (id) {
      if (saved_.size() >= expected_) { valid_ = false; return; }
      saved_.append({id, id->recalc_up_to_undo_push, id->recalc_after_undo_push});
    }
  }
  static void foreach_owner(Main &main, FunctionRef<void(ID *)> visit)
  {
    ID *id;
    FOREACH_MAIN_ID_BEGIN (&main, id) {
      visit(id);
      BKE_library_foreach_ID_link(&main, id, [&](LibraryIDLinkCallbackData *data) {
        if (data->cb_flag & IDWALK_CB_EMBEDDED) { visit(*data->id_pointer); }
        return IDWALK_RET_NOP;
      }, nullptr, IDWALK_READONLY);
    }
    FOREACH_MAIN_ID_END;
  }
 public:
  explicit InitialCheckpointRecalcRestore(Main &main, const size_t limit)
  {
    /* Duplicate embedded references need no Set: all values are recorded before the writer
     * changes bookkeeping, so restoring an ID repeatedly restores the same exact values.
     * Count first without growing owned containers; reserve the complete vector before append. */
    bool admitted = true;
    foreach_owner(main, [&](ID *id) {
      if (!id || !admitted) { return; }
      if (expected_ == std::numeric_limits<int64_t>::max() ||
          size_t(expected_) >= limit / sizeof(Saved)) { admitted = false; return; }
      expected_++;
    });
    if (!admitted) { return; }
    if (expected_ > saved_.capacity()) {
      /* BLI Vector reserve grows to max(requested, 2*capacity). Charge the whole fresh buffer,
       * including its overlap with inline storage; allocator overhead has a separate reserve. */
      const int64_t capacity = std::max(expected_, saved_.capacity() * 2);
      if (uint64_t(capacity) > limit / sizeof(Saved)) { return; }
      charged_ = size_t(capacity) * sizeof(Saved);
      saved_.reserve(expected_);
    }
    valid_ = true;
    foreach_owner(main, [&](ID *id) { remember(id); });
    valid_ = valid_ && saved_.size() == expected_;
  }
  bool valid() const { return valid_; }
  size_t charged_bytes() const { return charged_; }
  ~InitialCheckpointRecalcRestore()
  {
    for (const Saved &saved : saved_) {
      saved.id->recalc_up_to_undo_push = saved.before;
      saved.id->recalc_after_undo_push = saved.after;
    }
  }
};

static MemFileUndoStep *capture_checkpoint_initial(Main &main, const char **error)
{
  ed::sculpt_paint::checkpoint::CodecLimits limits;
  if (!WM_checkpoint_memory_limits(limits, 0, nullptr)) {
    *error = "Insufficient available memory for initial checkpoint Undo"; return nullptr;
  }
  size_t used = sizeof(MemFileUndoStep) + sizeof(MemFileUndoData);
  if (used > limits.max_backing_bytes) {
    *error = "Initial checkpoint Undo control exceeds finite admission"; return nullptr;
  }
  InitialCheckpointRecalcRestore recalc(main, limits.max_backing_bytes - used);
  if (!recalc.valid()) {
    *error = "Initial checkpoint bookkeeping exceeds finite admission"; return nullptr;
  }
  used += recalc.charged_bytes();
  auto *sculpt = ed::sculpt_paint::undo::main_undo_state_capture_initial_checkpoint(
      main, limits.max_backing_bytes, used, error);
  if (*error) { return nullptr; }
  MemFileUndoStep *step = MEM_new_zeroed<MemFileUndoStep>(__func__);
  step->angelo_sculpt_state = sculpt;
  step->step.type = BKE_UNDOSYS_TYPE_MEMFILE;
  step->data = MEM_new_zeroed<MemFileUndoData>(__func__);
  const BlendMemFileWriteResult result = BLO_write_file_mem_bounded(
      &main, &step->data->memfile, G.fileflags | G_FILE_RECOVER_WRITE,
      limits.max_backing_bytes - used);
  const size_t sculpt_bytes = sculpt ? ed::sculpt_paint::undo::main_undo_state_memory_size(*sculpt) : 0;
  if (result != BlendMemFileWriteResult::Success ||
      sculpt_bytes > SIZE_MAX - step->data->memfile.size) {
    detached_free(step);
    *error = "Could not encode initial checkpoint Undo within finite admission"; return nullptr;
  }
  step->data->undo_size = step->data->memfile.size;
  step->step.data_size = sculpt_bytes + step->data->undo_size;
  return step;
}

bool ED_angelo_undo_checkpoint_initial(bContext *C,
                                      CheckpointCaptureOwner *owner,
                                      const char **r_error)
{
  const char *local_error = nullptr;
  const char **error = r_error ? r_error : &local_error;
  *error = nullptr;
  /* Validate the lifecycle owner before dereferencing cached document/editor owners. */
  if (!C || !BKE_undosys_checkpoint_capture_validate(owner)) {
    *error = "Initial checkpoint Undo requires the live protected Restore owner";
    return false;
  }
  Main *main = CTX_data_main(C);
  if (!main || main != G_MAIN) {
    *error = "Initial checkpoint Undo has no protected live document";
    return false;
  }
  wmWindowManager *wm = CTX_wm_manager(C);
  /* A callback may change context independently of Main's live WM membership. */
  if (!wm || BLI_findindex(&main->wm, wm) < 0 || !wm->runtime || !wm->runtime->undo_stack) {
    *error = "Initial checkpoint Undo has no protected live document stack";
    return false;
  }
  UndoStack *stack = wm->runtime->undo_stack;
  /* The ordinary MemFile encoder flushes pending Sculpt geometry. Reuse the complete
   * Angelo encoder instead: it retains all graph owners, BMLog provenance and flush flags. */
  MemFileUndoStep *initial = capture_checkpoint_initial(*main, error);
  if (!initial) { return false; }
  STRNCPY(initial->step.name, "Checkpoint Restored");
  initial->step.use_old_bmain_data = false;
  if (!BKE_undosys_checkpoint_capture_validate(owner) ||
      CTX_data_main(C) != main || CTX_wm_manager(C) != wm ||
      !BKE_undosys_checkpoint_restore_publish(owner, stack, &initial->step))
  {
    detached_free(initial);
    *error = "Protected checkpoint initial Undo publication was refused";
    return false;
  }
  /* The stack now owns the complete snapshot. No context Sculpt dummy or callback is
   * needed; future normal sculpt pushes can use this explicit MemFile destination. */
  main->is_memfile_undo_written = true;
  main->use_memfile_full_barrier = false;
  return true;
}

static bool capture_jobs_idle(const Main &bmain, const char **r_error)
{
  for (const wmWindowManager &wm : bmain.wm) {
    if (WM_jobs_has_pending_or_running(&wm)) {
      *r_error = "Finish the pending editor work before capturing a native commit";
      return false;
    }
  }
  return true;
}

bool ED_angelo_undo_scope_validate(bContext *C,
                                    const AngeloUndoCapture *capture,
                                    Main **r_main,
                                    UndoStack **r_stack,
                                    const char **r_error)
{
  if (!capture || capture->finished || !BKE_undosys_capture_validate(capture->guard) ||
      BKE_undosys_restore_phase() != UndoRestorePhase::Preparing ||
      CTX_data_main(C) != capture->main || CTX_wm_manager(C) != capture->wm ||
      CTX_wm_window(C) != capture->window || capture->wm->runtime->undo_stack != capture->stack ||
      capture->wm->op_undo_depth != 1)
  {
    *r_error = "The exact native commit scope is no longer available";
    return false;
  }
  if (r_main) {
    *r_main = capture->main;
  }
  if (r_stack) {
    *r_stack = capture->stack;
  }
  return true;
}

bool ED_angelo_undo_scope_operation_begin(bContext *C,
                                           AngeloUndoCapture *capture,
                                           Main **r_main,
                                           UndoStack **r_stack,
                                           const char **r_error)
{
  if (!ED_angelo_undo_scope_validate(C, capture, r_main, r_stack, r_error)) {
    return false;
  }
  if (capture->operation_active) {
    *r_error = "A native commit capability is already being consumed";
    return false;
  }
  capture->operation_active = true;
  return true;
}

void ED_angelo_undo_scope_operation_end(AngeloUndoCapture *capture)
{
  BLI_assert(capture && capture->operation_active);
  capture->operation_active = false;
}

bool ED_angelo_undo_scope_operation_active(const AngeloUndoCapture *capture)
{
  return capture && capture->operation_active;
}

bool ED_angelo_undo_scope_id_is_protected(const AngeloUndoCapture *capture, const ID *id)
{
  /* The capability owner establishes current Main/embedded membership before calling. */
  if (!capture || !id) {
    return true;
  }
  const short type = GS(id->name);
  return ELEM(type, ID_WM, ID_SCR, ID_WS, ID_LI) ||
         (type == ID_SCE && capture->artist_scene_uids.contains(id->session_uid));
}

static bool synchronize_grid_owners(AngeloUndoCapture &capture, const char **r_error)
{
  using namespace ed::sculpt_paint::undo;
  if (!BKE_undosys_capture_validate(capture.guard)) {
    *r_error = "The native capture owner changed before synchronization";
    return false;
  }
  bool has_grids = false;
  for (const Object &object : capture.main->objects) {
    const SculptSession *ss = object.runtime->sculpt_session;
    has_grids |= (object.mode & OB_MODE_SCULPT) && ss && ss->subdiv_ccg;
  }
  if (!has_grids) {
    return true; /* No grid registry/evaluation work for ordinary Object Mode or Dyntopo. */
  }
  if (!capture_jobs_idle(*capture.main, r_error)) {
    return false;
  }
  Vector<MainUndoGridOwnerKey> owners;
  if (!main_undo_grids_collect_owner_keys(*capture.main, owners, r_error)) {
    return false;
  }
  for (const MainUndoGridOwnerKey &key : owners) {
    capture.artist_scene_uids.add(key.scene_uid);
  }
  /* Several Objects may share one canonical graph. Evaluate that graph once, in stable order. */
  Vector<std::pair<uint, std::string>> evaluated;
  for (const MainUndoGridOwnerKey &key : owners) {
    const std::pair<uint, std::string> identity{key.scene_uid, key.view_layer};
    if (evaluated.contains(identity)) {
      continue;
    }
    Scene *scene = nullptr;
    for (Scene &candidate : capture.main->scenes) {
      if (candidate.id.session_uid == key.scene_uid) {
        scene = &candidate;
        break;
      }
    }
    ViewLayer *view_layer = scene ? BKE_view_layer_find(scene, key.view_layer) : nullptr;
    if (!view_layer) {
      *r_error = "A Sculpt owner changed before native synchronization";
      return false;
    }
    Depsgraph *graph = BKE_scene_ensure_depsgraph(capture.main, scene, view_layer);
    BKE_scene_graph_evaluated_ensure(graph, capture.main);
    /* Do not use graph/Scene/ViewLayer pointers again after callback-bearing evaluation. */
    if (!BKE_undosys_capture_validate(capture.guard) ||
        !capture_jobs_idle(*capture.main, r_error))
    {
      if (!*r_error) {
        *r_error = "The native capture owner changed during synchronization";
      }
      return false;
    }
    evaluated.append(identity);
  }
  Vector<MainUndoGridOwnerKey> current;
  if (!main_undo_grids_collect_owner_keys(*capture.main, current, r_error)) {
    return false;
  }
  if (current.size() != owners.size()) {
    *r_error = "The Sculpt owner set changed during synchronization";
    return false;
  }
  for (const int i : owners.index_range()) {
    const MainUndoGridOwnerKey &a = owners[i], &b = current[i];
    if (a.object_uid != b.object_uid || a.mesh_uid != b.mesh_uid || a.scene_uid != b.scene_uid ||
        a.authority != b.authority || !STREQ(a.view_layer, b.view_layer))
    {
      *r_error = "The Sculpt owner set changed during synchronization";
      return false;
    }
  }
  /* Pure capture immediately follows and retains its complete freshness/profile checks. */
  return true;
}

/** Whether any sculpted Multires mesh has grids in more than one graph (one Object in several
 * Scenes or view layers, or Objects sharing a mesh). Decoding an ordinary MemFile does not
 * reproduce such grids exactly; unknown counts as shared, keeping the exact boundary visible. */
static bool grids_in_several_graphs(Main &bmain)
{
  Vector<ed::sculpt_paint::undo::MainUndoGridOwnerKey> owners;
  const char *error = nullptr;
  if (!ed::sculpt_paint::undo::main_undo_grids_collect_owner_keys(bmain, owners, &error)) {
    return true;
  }
  Set<uint32_t> meshes;
  for (const ed::sculpt_paint::undo::MainUndoGridOwnerKey &key : owners) {
    if (!meshes.add(key.mesh_uid)) {
      return true;
    }
  }
  return false;
}

AngeloUndoCapture *ED_angelo_undo_begin(bContext *C, const char **r_error, const bool synchronize)
{
  if (BKE_undosys_restore_phase() != UndoRestorePhase::Inactive) {
    *r_error = BKE_undosys_restore_refusal_message();
    return nullptr;
  }
  BLI_assert(BLI_thread_is_main());
  Main *bmain = CTX_data_main(C);
  wmWindowManager *wm = CTX_wm_manager(C);
  UndoStack *stack = wm ? wm->runtime->undo_stack : nullptr;
  if (BKE_undosys_recovery_required(stack, bmain)) {
    *r_error = "Undo recovery is required; open a known-valid saved file before applying changes";
    return nullptr;
  }
  if (!bmain || !wm || !stack || !stack->step_active || !(U.uiflag & USER_GLOBALUNDO) ||
      U.undosteps <= 0)
  {
    *r_error = "Enable Global Undo with at least one Undo step before applying a native commit";
    return nullptr;
  }
  for (const wmWindowManager &owner : bmain->wm) {
    if (owner.runtime && owner.runtime->is_interface_locked) {
      *r_error = "Finish the operation locking Blender's interface before applying the staged change";
      return nullptr;
    }
  }
  if (WM_operator_has_modal_undo(*wm)) {
    *r_error = "Finish or cancel the active modal edit before applying the staged change";
    return nullptr;
  }
  if (wm->op_undo_depth != 0 || stack->step_init || stack->group_level != 0 ||
      bmain->no_resync.load() != 0)
  {
    *r_error = "Finish the current stroke or modal edit before applying the staged change";
    return nullptr;
  }
  if (!BKE_sculpt_undo_runtime_preflight(*bmain, r_error)) {
    return nullptr;
  }
  bool has_sculpt_state = false;
  bool has_dyntopo_session = false;
  for (const Object &object : bmain->objects) {
    if (object.mode & OB_MODE_EDIT) {
      *r_error = "Finish the Edit Mode interaction before applying the staged change";
      return nullptr;
    }
    const bool sculpting = (object.mode & OB_MODE_SCULPT) && object.runtime->sculpt_session;
    has_sculpt_state |= sculpting;
    has_dyntopo_session |= sculpting && BKE_object_sculpt_use_dyntopo(&object);
  }
  /* The writer does this too. Resolve pending view-layer state before binding the scope. */
  if (!BKE_main_view_layers_synced_ensure(bmain)) {
    *r_error = "Finish the pending view-layer update before applying the staged change";
    return nullptr;
  }
  AngeloUndoCapture *capture = MEM_new<AngeloUndoCapture>(__func__);
  capture->context = C;
  capture->main = bmain;
  capture->wm = wm;
  capture->window = CTX_wm_window(C);
  capture->stack = stack;
  capture->active = stack->step_active;
  if (Scene *scene = CTX_data_scene(C)) {
    capture->scene_uid = scene->id.session_uid;
    capture->artist_scene_uids.add(capture->scene_uid);
  }
  for (const wmWindowManager &owner : bmain->wm) {
    for (const wmWindow &window : owner.windows) {
      if (window.scene) {
        capture->artist_scene_uids.add(window.scene->id.session_uid);
      }
    }
  }
  capture->guard = BKE_undosys_capture_begin(bmain, stack);
  if (!capture->guard) {
    MEM_delete(capture);
    *r_error = "Could not protect the native commit owners";
    return nullptr;
  }
  wm->op_undo_depth++;
  if (synchronize && has_sculpt_state && !synchronize_grid_owners(*capture, r_error)) {
    BKE_undosys_capture_finalize(capture->guard, C);
    ED_angelo_undo_discard(capture);
    return nullptr;
  }

  /* An ordinary Object Mode commit still writes just its usual post snapshot. Any live Sculpt
   * state needs an exact boundary independent of the active history type or pruned strokes. */
  if (has_sculpt_state) {
    capture->before = capture_memfile(bmain, nullptr, false, r_error);
    if (!capture->before) {
      BKE_undosys_capture_finalize(capture->guard, C);
      ED_angelo_undo_discard(capture);
      return nullptr;
    }
    if (capture->active->type == BKE_UNDOSYS_TYPE_MEMFILE &&
        captured_states_equal(*reinterpret_cast<MemFileUndoStep *>(capture->active),
                              *capture->before))
    {
      /* Drop only the new redundant capture. Existing recorded history is never replaced. The
       * post snapshot will reference that still-owned active MemFile, not this detached copy. */
      detached_free(capture->before);
      capture->before = nullptr;
    }
    else {
      /* One visible Undo step per commit. The exact boundary stays in history but is hidden
       * whenever landing on the active step reproduces the state before this commit:
       * - a Sculpt step of any object: its decoder recognizes this adjacent restored baseline
       *   (ED_angelo_undo_has_restored_baseline) and never forces another window's object
       *   into Sculpt mode;
       * - a MemFile step holding an exact Angelo Sculpt snapshot (a restored checkpoint or an
       *   earlier commit): decoding it restores that exact runtime state;
       * - an ordinary MemFile step (a property edit in Sculpt, Dyntopo disable, an Object Mode
       *   operator in another window) that is the latest step, while no Dyntopo session is live
       *   and every sculpted Multires mesh has its grids in one graph: decoding it reproduces
       *   this state.
       * Otherwise an ordinary MemFile keeps the exact boundary as a visible step of its own:
       * one reached by Undo (it has Redo steps) does not decode to the live state Undo left,
       * decoding it leaves a live Dyntopo session (stock behaviour), and grids shown by several
       * graphs come back from their displacement data, not bit for bit. */
      const bool hide_before =
          capture->active->type == BKE_UNDOSYS_TYPE_SCULPT ||
          (capture->active->type == BKE_UNDOSYS_TYPE_MEMFILE &&
           (reinterpret_cast<MemFileUndoStep *>(capture->active)->angelo_sculpt_state ||
            (!capture->active->next && !has_dyntopo_session && !grids_in_several_graphs(*bmain))));
      capture->before->step.skip = hide_before;
      if (hide_before) {
        STRNCPY(capture->before->step.name, capture->active->name);
      }
      else {
        STRNCPY(capture->before->step.name, "Before Validated Native Program");
      }
    }
  }
  return capture;
}

void ED_angelo_undo_discard(AngeloUndoCapture *capture)
{
  if (!capture) {
    return;
  }
  BLI_assert(!capture->operation_active);
  if (capture->operation_active) {
    return; /* A bridge-held reference keeps the owner alive until its private operation ends. */
  }
  detached_free(capture->before);
  capture->before = nullptr;
  if (BKE_undosys_restore_protects_main(capture->main) &&
      CTX_data_main(capture->context) == capture->main &&
      CTX_wm_manager(capture->context) == capture->wm)
  {
    BLI_assert(capture->wm->op_undo_depth > 0);
    if (capture->wm->op_undo_depth > 0) {
      capture->wm->op_undo_depth--;
    }
  }
  BKE_undosys_capture_end(capture->guard);
  MEM_delete(capture);
}

bool ED_angelo_undo_finish_prepare(bContext *C,
                                   AngeloUndoCapture *capture,
                                   const bool keep,
                                   const bool synchronize,
                                   const char **r_error)
{
  if (!ED_angelo_undo_scope_validate(C, capture, nullptr, nullptr, r_error) ||
      capture->operation_active)
  {
    if (!*r_error) {
      *r_error = "A native commit capability is still active";
    }
    return false;
  }
  capture->finished = true; /* No further data capabilities after the finish boundary. */
  if (!keep) {
    if (!BKE_undosys_capture_finalize(capture->guard, C)) {
      *r_error = "Native commit cleanup could not deliver final editor updates";
    }
    return false;
  }
  if (synchronize && !synchronize_grid_owners(*capture, r_error)) {
    BKE_undosys_capture_finalize(capture->guard, C);
    return false;
  }
  Main *bmain = capture->main;
  eRNAOverrideMatchResult override_flags = RNA_OVERRIDE_MATCH_RESULT_INIT;
  BKE_lib_override_library_main_operations_create(
      bmain, false, reinterpret_cast<int *>(&override_flags));
  auto *previous = reinterpret_cast<MemFileUndoStep *>(
      BKE_undosys_stack_active_with_type(capture->stack, BKE_UNDOSYS_TYPE_MEMFILE));
  MemFileUndoStep *after = capture_memfile(
      bmain, capture->before ? capture->before : previous, true, r_error);
  if (!after) {
    BKE_undosys_capture_finalize(capture->guard, C);
    return false;
  }
  if (capture->before && previous) {
    /* The independent baseline shares no serialized chunks with the older memfile. */
    BLO_memfile_clear_future(&previous->data->memfile);
  }
  if (!BKE_undosys_capture_publish(
          capture->guard, capture->before ? &capture->before->step : nullptr, &after->step))
  {
    detached_free(after);
    *r_error = "The protected native Undo publication was refused";
    BKE_undosys_capture_finalize(capture->guard, C);
    return false;
  }
  capture->before = nullptr; /* Stack owns both snapshots now. */
  bmain->is_memfile_undo_written = true;
  bmain->use_memfile_full_barrier = false;
  WM_file_tag_modified();
  if (override_flags & RNA_OVERRIDE_MATCH_RESULT_CREATED) {
    WM_main_add_notifier(NC_WM | ND_LIB_OVERRIDE_CHANGED, nullptr);
  }
  if (!BKE_undosys_capture_finalize(capture->guard, C)) {
    *r_error = "Native Undo was published, but final editor delivery could not be completed";
  }
  return true;
}

bool ED_angelo_undo_finish(bContext *C,
                           AngeloUndoCapture *capture,
                           const bool keep,
                           const char **r_error)
{
  if (capture && capture->operation_active) {
    *r_error = "A native commit capability is still active";
    return false;
  }
  const bool published = ED_angelo_undo_finish_prepare(C, capture, keep, false, r_error);
  ED_angelo_undo_discard(capture);
  return published;
}

bool ED_angelo_undo_has_restored_baseline(bContext *C, const UndoStep &sculpt_step)
{
  wmWindowManager *wm = CTX_wm_manager(C);
  UndoStack *stack = wm ? wm->runtime->undo_stack : nullptr;
  const UndoStep *baseline = sculpt_step.next;
  if (!stack || BKE_undosys_recovery_required(stack, CTX_data_main(C)) ||
      sculpt_step.type != BKE_UNDOSYS_TYPE_SCULPT || !sculpt_step.is_applied || !baseline ||
      !baseline->skip || baseline->type != BKE_UNDOSYS_TYPE_MEMFILE ||
      stack->step_active != baseline || stack->step_active_memfile != baseline)
  {
    return false;
  }
  return reinterpret_cast<const MemFileUndoStep *>(baseline)->angelo_sculpt_state != nullptr;
}

#ifdef WITH_GTESTS
namespace ed::sculpt_paint::undo {
std::shared_ptr<MainUndoTestFault> main_undo_test_fault_arm_index(bContext *C,
                                                                  const int step_index,
                                                                  const unsigned int object_index,
                                                                  const char **r_error,
                                                                  const int grid_owner_index)
{
  UndoStack *stack = ED_undo_stack_get();
  UndoStep *step = stack ? static_cast<UndoStep *>(BLI_findlink(&stack->steps, step_index)) :
                           nullptr;
  if (!step || step->type != BKE_UNDOSYS_TYPE_MEMFILE) {
    *r_error = "The fixture target is not a live MemFile boundary";
    return {};
  }
  MainUndoState *state = reinterpret_cast<MemFileUndoStep *>(step)->angelo_sculpt_state;
  if (!state) {
    *r_error = "The fixture target has no Sculpt runtime snapshot";
    return {};
  }
  return main_undo_state_test_fault_arm(
      *state, CTX_data_main(C), stack, object_index, r_error, grid_owner_index);
}
}  // namespace ed::sculpt_paint::undo
#endif

}  // namespace blender
