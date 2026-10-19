/* SPDX-FileCopyrightText: 2023 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup bke
 *
 * Used by ED_undo.hh, internal implementation.
 */

#include <atomic>
#include <cstdio>
#include <cstring>
#include <memory>
#include <mutex>
#include <utility>

#include "CLG_log.h"

#include "BLI_listbase.h"
#include "BLI_map.hh"
#include "BLI_set.hh"
#include "BLI_string.h"
#include "BLI_sys_types.h"
#include "BLI_threads.h"
#include "BLI_utildefines.h"
#include "BLI_vector.hh"

#include "BLT_translation.hh"

#include "DNA_listBase.h"
#include "DNA_scene_types.h"
#include "DNA_windowmanager_types.h"

#include "BKE_context.hh"
#include "BKE_global.hh"
#include "BKE_lib_id.hh"
#include "BKE_lib_override.hh"
#include "BKE_library.hh"
#include "BKE_main.hh"
#include "BKE_report.hh"
#include "BKE_scene.hh"
#include "BKE_sculpt_undo.hh"
#include "BKE_undo_system.hh"
#include "BKE_wm_runtime.hh"

#include "DEG_depsgraph.hh"
#include "DEG_depsgraph_query.hh"

#include "RNA_access.hh"

#include "MEM_guardedalloc.h"

/* Header to pull symbols from the file which otherwise might get stripped away. */
#include "BKE_blender_undo.hh"

namespace blender {

/* This ownership check inspects only the explicitly supplied Main. Keep the prohibition below
 * around the actual history operations, which must continue receiving their stack as an argument. */
static bool restore_main_owns_stack(const Main &bmain, const UndoStack *stack)
{
  for (const wmWindowManager &wm : bmain.wm) {
    if (wm.runtime && wm.runtime->undo_stack == stack) {
      return true;
    }
  }
  return false;
}

static bool checkpoint_main_history_is_idle(const Main &bmain)
{
  for (const wmWindowManager &wm : bmain.wm) {
    const UndoStack *stack = wm.runtime->undo_stack;
    if (stack && (stack->step_init || stack->group_level)) {
      return false;
    }
  }
  return true;
}

namespace {
struct CheckpointWindowManager {
  uint session_uid;
  const bke::WindowManagerRuntime *runtime;
  const UndoStack *stack;
};

/* Only identity values and container storage may outlive the main-thread owner. In particular,
 * an evaluation worker can release the last reference without destroying any Blender owner. */
struct CheckpointObservation {
  const Main *main;
  const Main *restore_target = nullptr;
  bool restore_transaction = false;
  Map<const wmWindowManager *, CheckpointWindowManager> window_managers;
  Set<const UndoStack *> stacks;

  explicit CheckpointObservation(const Main *bmain) : main(bmain) {}
};

static std::shared_ptr<const CheckpointObservation> checkpoint_observation_create(
    const Main &bmain)
{
  auto observation = std::make_shared<CheckpointObservation>(&bmain);
  for (const wmWindowManager &wm : bmain.wm) {
    if (!wm.runtime) {
      return nullptr;
    }
    observation->window_managers.add_new(&wm,
                                         {wm.id.session_uid, wm.runtime, wm.runtime->undo_stack});
    if (wm.runtime->undo_stack) {
      observation->stacks.add(wm.runtime->undo_stack);
    }
  }
  return observation;
}

static bool checkpoint_observation_validate(const CheckpointObservation &observation,
                                            const Main &bmain)
{
  int64_t matched = 0;
  for (const wmWindowManager &wm : bmain.wm) {
    const CheckpointWindowManager *saved = observation.window_managers.lookup_ptr(&wm);
    /* Resolve each current live member before inspecting its runtime. Saved pointers need never
     * be dereferenced, including after a callback removed or replaced an old member. */
    if (!saved || saved->session_uid != wm.id.session_uid) {
      return false;
    }
    if (!wm.runtime || saved->runtime != wm.runtime || saved->stack != wm.runtime->undo_stack) {
      return false;
    }
    matched++;
  }
  return matched == observation.window_managers.size();
}

/* The inactive hint avoids locking on ordinary admission checks. Access to the shared pointer
 * itself is always locked; a reader keeps exactly one immutable record after unlocking. */
std::atomic<bool> checkpoint_observation_active{false};
std::mutex checkpoint_observation_mutex;
std::shared_ptr<const CheckpointObservation> checkpoint_observation;

static std::shared_ptr<const CheckpointObservation> checkpoint_observation_get()
{
  if (!checkpoint_observation_active.load(std::memory_order_acquire)) {
    return nullptr;
  }
  std::lock_guard lock(checkpoint_observation_mutex);
  return checkpoint_observation;
}
}  // namespace

struct CheckpointCaptureOwner {
  std::shared_ptr<const CheckpointObservation> observation;
  Main *restore_target = nullptr;
  bool restore_history_retired = false, restore_rebased = false, restore_published = false;

  explicit CheckpointCaptureOwner(std::shared_ptr<const CheckpointObservation> observation)
      : observation(std::move(observation))
  {
  }
};

/* Only the main thread accesses the opaque owner. Workers retain metadata, never this pointer. */
static CheckpointCaptureOwner *checkpoint_owner = nullptr;

#define undo_stack _wm_undo_stack_disallow /* pass in as a variable always. */

/** Odd requirement of Blender that we always keep a memfile undo in the stack. */
#define WITH_GLOBAL_UNDO_KEEP_ONE

/** Make sure all ID's created at the point we add an undo step that uses ID's. */
#define WITH_GLOBAL_UNDO_ENSURE_UPDATED

/**
 * Make sure we don't apply edits on top of a newer memfile state, see: #56163.
 * \note Keep an eye on this, could solve differently.
 */
#define WITH_GLOBAL_UNDO_CORRECT_ORDER

/** We only need this locally. */
static CLG_LogRef LOG = {"undo"};

/* The observer storage outlives all evaluation workers. Never publish a pointer to the stack-local
 * scope: final-state editor delivery may start a job whose worker outlives that scope. */
static std::atomic<UndoRestorePhase> restore_phase{UndoRestorePhase::Inactive};
static std::atomic<const Main *> restore_main{nullptr};
static std::atomic<const UndoStack *> restore_stack{nullptr};

UndoRestorePhase BKE_undosys_restore_phase()
{
  if (checkpoint_observation_get()) {
    return UndoRestorePhase::Checkpoint;
  }
  return restore_phase.load(std::memory_order_acquire);
}

bool BKE_undosys_restore_protects_main(const Main *bmain)
{
  if (const auto observation = checkpoint_observation_get()) {
    return !bmain || observation->main == bmain || observation->restore_target == bmain;
  }
  return restore_phase.load(std::memory_order_acquire) != UndoRestorePhase::Inactive &&
         (!bmain || restore_main.load(std::memory_order_acquire) == bmain);
}

bool BKE_undosys_restore_protects_stack(const UndoStack *ustack)
{
  if (const auto observation = checkpoint_observation_get()) {
    return observation->restore_transaction || observation->stacks.contains(ustack);
  }
  return restore_phase.load(std::memory_order_acquire) != UndoRestorePhase::Inactive &&
         restore_stack.load(std::memory_order_acquire) == ustack;
}

const char *BKE_undosys_restore_refusal_message()
{
  if (checkpoint_observation_get()) {
    return "Finish the current checkpoint preparation before changing history, document owners, "
           "or starting work";
  }
  return "Finish the current Sculpt recovery or native commit before changing history, document "
         "owners, or starting work";
}

namespace {
struct DeferredEditorUpdate {
  Depsgraph *graph;
  uint scene_uid;
  const ViewLayer *view_layer;
  bool is_time_update;
  bool clear_recalc;
};

class SculptProtectedScope;
class SculptRestoreTraversal;
/* Only the main thread accesses scope pointers or mutable notification records. */
SculptProtectedScope *protected_scope = nullptr;
SculptRestoreTraversal *restore_traversal = nullptr;

/** Shared owner protection and editor delivery; capture is not an Undo traversal. */
class SculptProtectedScope {
 public:
  UndoStack *stack;
  bool active = false;
  bool completed = false;
  bool finalization_started = false;
  bool invalid_notification_owner = false;
  Vector<DeferredEditorUpdate> editor_updates;

  explicit SculptProtectedScope(UndoStack *stack) : stack(stack) {}
  ~SculptProtectedScope()
  {
    if (active) {
      BLI_assert(protected_scope == this);
      /* Release performs no evaluation, callbacks, pruning or deferred destruction. */
      restore_phase.store(UndoRestorePhase::Inactive, std::memory_order_release);
      restore_main.store(nullptr, std::memory_order_release);
      restore_stack.store(nullptr, std::memory_order_release);
      protected_scope = nullptr;
    }
  }

  void activate(Main *bmain)
  {
    BLI_assert(!protected_scope && !active);
    restore_main.store(bmain, std::memory_order_release);
    restore_stack.store(stack, std::memory_order_release);
    protected_scope = this;
    active = true;
    restore_phase.store(UndoRestorePhase::Preparing, std::memory_order_release);
  }

  bool finalize(bContext *C)
  {
    if (!active) {
      return true;
    }
    if (finalization_started) {
      return completed;
    }
    finalization_started = true;
    Main *bmain = C ? CTX_data_main(C) : G_MAIN;
    if (invalid_notification_owner || !BKE_undosys_restore_protects_main(bmain)) {
      return false;
    }
    /* Only canonical viewport graphs deliver editor updates in production. Resolve their
     * Scene-owned slots without inspecting unrelated job graphs or dereferencing stale records. */
    Vector<DeferredEditorUpdate> resolved_updates;
    for (const DeferredEditorUpdate &update : editor_updates) {
      for (Scene &scene : bmain->scenes) {
        if (scene.id.session_uid != update.scene_uid) {
          continue;
        }
        for (ViewLayer &view_layer : scene.view_layers) {
          if (&view_layer == update.view_layer &&
              BKE_scene_get_depsgraph(&scene, &view_layer) == update.graph)
          {
            resolved_updates.append(update);
            break;
          }
        }
        break;
      }
    }
    /* Final history markers and exact runtime are now installed. No decoder runs after this
     * transition. Jobs started by editor delivery therefore own only the final document. */
    restore_phase.store(UndoRestorePhase::Finalizing, std::memory_order_release);
    for (const DeferredEditorUpdate &update : resolved_updates) {
      BKE_scene_graph_finalize_editors(update.graph, update.is_time_update, update.clear_recalc);
      if ((C ? CTX_data_main(C) : G_MAIN) != bmain) {
        return false;
      }
    }
    completed = true;
    return true;
  }
};

class SculptRestoreTraversal : public SculptProtectedScope {
  SculptRestoreTraversal *previous_;

 public:
  UndoStep *decoding_step = nullptr;
  bool intermediate_work_started = false;
  void (*cancel_intermediate_work)(Main *) = nullptr;

  explicit SculptRestoreTraversal(UndoStack *stack)
      : SculptProtectedScope(stack), previous_(restore_traversal)
  {
    restore_traversal = this;
  }
  ~SculptRestoreTraversal()
  {
    if (active && !completed && intermediate_work_started && cancel_intermediate_work) {
      cancel_intermediate_work(G_MAIN);
    }
    restore_traversal = previous_;
  }
};
}  // namespace

CheckpointCaptureOwner *BKE_undosys_checkpoint_capture_begin(Main *bmain)
{
  if (!BLI_thread_is_main() || checkpoint_owner || protected_scope || restore_traversal ||
      BKE_undosys_restore_phase() != UndoRestorePhase::Inactive || !bmain || bmain != G_MAIN)
  {
    return nullptr;
  }
  std::shared_ptr<const CheckpointObservation> observation = checkpoint_observation_create(*bmain);
  if (!observation) {
    return nullptr;
  }
  /* Allocation and all Blender access finish before publication. No Undo history is needed. */
  auto *owner = MEM_new<CheckpointCaptureOwner>(__func__, observation);
  checkpoint_owner = owner;
  {
    std::lock_guard lock(checkpoint_observation_mutex);
    BLI_assert(!checkpoint_observation);
    checkpoint_observation.swap(observation);
    checkpoint_observation_active.store(true, std::memory_order_release);
  }
  return owner;
}

bool BKE_undosys_checkpoint_capture_validate(const CheckpointCaptureOwner *owner)
{
  /* Check address identity before dereferencing. The caller must discard consumed handles;
   * a later owner can reuse the address. */
  if (!BLI_thread_is_main() || !owner || owner != checkpoint_owner) {
    return false;
  }
  const CheckpointObservation &observation = *owner->observation;
  return G_MAIN == observation.main &&
         checkpoint_observation_validate(observation, *observation.main);
}

bool BKE_undosys_checkpoint_capture_end(CheckpointCaptureOwner *&owner)
{
  if (!owner) {
    return true;
  }
  if (!BLI_thread_is_main() || owner != checkpoint_owner) {
    return false;
  }
  /* Consume first. Releasing this metadata remains safe after document validation has failed. */
  CheckpointCaptureOwner *retired_owner = owner;
  owner = nullptr;
  checkpoint_owner = nullptr;
  std::shared_ptr<const CheckpointObservation> retired_observation;
  {
    std::lock_guard lock(checkpoint_observation_mutex);
    retired_observation = std::move(checkpoint_observation);
    checkpoint_observation_active.store(false, std::memory_order_release);
  }
  /* Neither metadata destruction nor any Blender access happens under the publication lock.
   * A worker can still own the immutable record and finish its query independently. */
  MEM_delete(retired_owner);
  return true;
}

CheckpointCaptureOwner *BKE_undosys_checkpoint_restore_begin(Main *old_main, Main *prepared_main)
{
  if (!prepared_main || prepared_main == old_main) { return nullptr; }
  CheckpointCaptureOwner *owner = BKE_undosys_checkpoint_capture_begin(old_main);
  if (!owner) { return nullptr; }
  if (!checkpoint_main_history_is_idle(*old_main)) {
    BKE_undosys_checkpoint_capture_end(owner);
    return nullptr;
  }
  auto observation = std::make_shared<CheckpointObservation>(*owner->observation);
  observation->restore_target = prepared_main;
  observation->restore_transaction = true;
  owner->restore_target = prepared_main;
  owner->observation = observation;
  {
    std::lock_guard lock(checkpoint_observation_mutex);
    checkpoint_observation = std::move(observation);
  }
  return owner;
}

bool BKE_undosys_checkpoint_restore_rebase(CheckpointCaptureOwner *owner, Main *installed_main)
{
  /* Old Main may already be freed by our controlled setup. Never inspect it here; the declared
   * target and current global identity are checked before taking any new live-member snapshot. */
  if (!BLI_thread_is_main() || !owner || owner != checkpoint_owner || !owner->restore_target ||
      owner->restore_rebased || installed_main != owner->restore_target || installed_main != G_MAIN)
  {
    return false;
  }
  if (!owner->restore_history_retired) { return false; }
  auto observation = checkpoint_observation_create(*installed_main);
  if (!observation) { return false; }
  auto rebased = std::make_shared<CheckpointObservation>(*observation);
  rebased->restore_target = installed_main;
  rebased->restore_transaction = true;
  owner->observation = rebased;
  owner->restore_rebased = true;
  {
    std::lock_guard lock(checkpoint_observation_mutex);
    /* Replace one immutable observer record without ever publishing an inactive hint. Workers
     * may finish an old identity-only query independently; no record dereferences old Main. */
    checkpoint_observation = std::move(rebased);
  }
  return BKE_undosys_checkpoint_capture_validate(owner);
}

bool BKE_undosys_checkpoint_restore_complete(const CheckpointCaptureOwner *owner)
{
  return BKE_undosys_checkpoint_capture_validate(owner) && owner->restore_rebased && owner->restore_published;
}

struct UndoCaptureScope : SculptProtectedScope {
  Main *main;
  UndoStep *expected_active;
  bool published = false;

  UndoCaptureScope(Main *bmain, UndoStack *stack)
      : SculptProtectedScope(stack), main(bmain), expected_active(stack->step_active)
  {
    activate(bmain);
  }
};

UndoCaptureScope *BKE_undosys_capture_begin(Main *bmain, UndoStack *stack)
{
  if (!BLI_thread_is_main() || restore_traversal ||
      BKE_undosys_restore_phase() != UndoRestorePhase::Inactive ||
      !bmain || bmain != G_MAIN || !stack || !restore_main_owns_stack(*bmain, stack) ||
      !stack->step_active || stack->step_init || stack->group_level != 0)
  {
    return nullptr;
  }
  return MEM_new<UndoCaptureScope>(__func__, bmain, stack);
}

bool BKE_undosys_capture_validate(const UndoCaptureScope *scope)
{
  return BLI_thread_is_main() && scope && protected_scope == scope && scope->active &&
         BKE_undosys_restore_protects_main(scope->main) && G_MAIN == scope->main &&
         restore_main_owns_stack(*scope->main, scope->stack) &&
         scope->stack->step_active == scope->expected_active && !scope->stack->step_init &&
         scope->stack->group_level == 0 && !scope->invalid_notification_owner;
}

bool BKE_undosys_capture_finalize(UndoCaptureScope *scope, bContext *C)
{
  return BKE_undosys_capture_validate(scope) && scope->finalize(C);
}

void BKE_undosys_capture_end(UndoCaptureScope *scope)
{
  BLI_assert(BLI_thread_is_main());
  MEM_delete(scope);
}

bool BKE_undosys_restore_activate(UndoStack *ustack,
                                  Main *bmain,
                                  void (*cancel_intermediate_work)(Main *))
{
  if (!BLI_thread_is_main() || !restore_traversal || restore_traversal->stack != ustack ||
      BKE_undosys_restore_phase() == UndoRestorePhase::Finalizing)
  {
    return false;
  }
  if (restore_traversal->active) {
    return BKE_undosys_restore_protects_main(bmain);
  }
  restore_traversal->cancel_intermediate_work = cancel_intermediate_work;
  restore_traversal->activate(bmain);
  return true;
}

bool BKE_undosys_restore_rebind_main(UndoStack *ustack, Main *bmain)
{
  if (!BLI_thread_is_main() || !restore_traversal || restore_traversal->stack != ustack ||
      BKE_undosys_restore_phase() != UndoRestorePhase::Preparing)
  {
    return false;
  }
  restore_traversal->editor_updates.clear();
  restore_traversal->invalid_notification_owner = false;
  restore_main.store(bmain, std::memory_order_release);
  return true;
}

bool BKE_undosys_restore_validate(Main *bmain)
{
  if (!BLI_thread_is_main() || !restore_traversal || !BKE_undosys_restore_protects_main(bmain) ||
      G_MAIN != bmain || restore_traversal->invalid_notification_owner)
  {
    return false;
  }
  const UndoStack *stack = restore_traversal->stack;
  if (!restore_main_owns_stack(*bmain, stack)) {
    return false;
  }
  for (const UndoStep &step : stack->steps) {
    if (&step == restore_traversal->decoding_step) {
      return true;
    }
  }
  return false;
}

void BKE_undosys_restore_before_memfile_read(UndoStack *ustack)
{
  if (BLI_thread_is_main() && restore_traversal && restore_traversal->stack == ustack &&
      restore_traversal->active)
  {
    /* Pure admission is complete. A later failure must cancel only work belonging to a
     * traversal that actually started restoring, never an initial read-only refusal. */
    restore_traversal->intermediate_work_started = true;
    restore_traversal->editor_updates.clear();
    restore_traversal->invalid_notification_owner = false;
  }
}

bool BKE_undosys_restore_defer_editor_update(Main *bmain,
                                             Depsgraph *depsgraph,
                                             const bool is_time_update,
                                             const bool clear_recalc)
{
  if (BKE_undosys_restore_phase() != UndoRestorePhase::Preparing ||
      !BKE_undosys_restore_protects_main(bmain))
  {
    return false;
  }
  /* This receiving graph is owned by the current scene-update call. Non-editor graphs keep
   * stock immediate finalization (the editor call is a no-op, recalc policy still applies). */
  if (!DEG_editors_update_is_enabled(depsgraph)) {
    return false;
  }
  BLI_assert(BLI_thread_is_main());
  if (!BLI_thread_is_main()) {
    /* Public worker-side evaluation entry points refuse before evaluation. Do not deliver G0
     * from an unsupported native reentry either, or touch the main-thread record container. */
    return true;
  }
  Scene *input_scene = DEG_get_input_scene(depsgraph);
  ViewLayer *input_layer = DEG_get_input_view_layer(depsgraph);
  uint scene_uid = 0;
  const ViewLayer *view_layer = nullptr;
  if (DEG_get_bmain(depsgraph) == bmain) {
    for (Scene &scene : bmain->scenes) {
      if (&scene != input_scene) {
        continue;
      }
      for (ViewLayer &layer : scene.view_layers) {
        if (&layer == input_layer && BKE_scene_get_depsgraph(&scene, &layer) == depsgraph) {
          scene_uid = scene.id.session_uid;
          view_layer = &layer;
          break;
        }
      }
      break;
    }
  }
  if (!scene_uid || !view_layer) {
    /* A future native editor-enabled noncanonical caller needs an explicit lifetime contract. */
    protected_scope->invalid_notification_owner = true;
    return true;
  }
  for (DeferredEditorUpdate &existing : protected_scope->editor_updates) {
    if (existing.graph == depsgraph && existing.scene_uid == scene_uid &&
        existing.view_layer == view_layer)
    {
      existing.is_time_update |= is_time_update;
      /* Preserve any caller that requested retaining recalc (e.g. a renderer). */
      existing.clear_recalc &= clear_recalc;
      return true;
    }
  }
  protected_scope->editor_updates.append(
      {depsgraph, scene_uid, view_layer, is_time_update, clear_recalc});
  return true;
}

/* -------------------------------------------------------------------- */
/** \name Undo Types
 * \{ */

const UndoType *BKE_UNDOSYS_TYPE_IMAGE = nullptr;
const UndoType *BKE_UNDOSYS_TYPE_MEMFILE = nullptr;
const UndoType *BKE_UNDOSYS_TYPE_PAINTCURVE = nullptr;
const UndoType *BKE_UNDOSYS_TYPE_PARTICLE = nullptr;
const UndoType *BKE_UNDOSYS_TYPE_SCULPT = nullptr;
const UndoType *BKE_UNDOSYS_TYPE_TEXT = nullptr;

static ListBaseT<UndoType> g_undo_types = {nullptr, nullptr};

/* An unused function with public linkage just to ensure symbols from the blender_undo.cc are not
 * stripped. */
void bke_undo_system_linker_workaround();
void bke_undo_system_linker_workaround()
{
  BLI_assert_unreachable();
  BKE_memfile_undo_free(nullptr);
}

static const UndoType *BKE_undosys_type_from_context(bContext *C)
{
  for (const UndoType &ut : g_undo_types) {
    /* No poll means we don't check context. */
    if (ut.poll && ut.poll(C)) {
      return &ut;
    }
  }
  return nullptr;
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name Internal Nested Undo Checks
 *
 * Make sure we're not running undo operations from 'step_encode', 'step_decode' callbacks.
 * bugs caused by this situation aren't _that_ hard to spot but aren't always so obvious.
 * Best we have a check which shows the problem immediately.
 * \{ */

#define WITH_NESTED_UNDO_CHECK

#ifdef WITH_NESTED_UNDO_CHECK
static bool g_undo_callback_running = false;
#  define UNDO_NESTED_ASSERT(state) BLI_assert(g_undo_callback_running == state)
#  define UNDO_NESTED_CHECK_BEGIN \
    { \
      UNDO_NESTED_ASSERT(false); \
      g_undo_callback_running = true; \
    } \
    ((void)0)
#  define UNDO_NESTED_CHECK_END \
    { \
      UNDO_NESTED_ASSERT(true); \
      g_undo_callback_running = false; \
    } \
    ((void)0)
#else
#  define UNDO_NESTED_ASSERT(state) ((void)0)
#  define UNDO_NESTED_CHECK_BEGIN ((void)0)
#  define UNDO_NESTED_CHECK_END ((void)0)
#endif

/** \} */

/* -------------------------------------------------------------------- */
/** \name Internal Callback Wrappers
 *
 * #UndoRefID is simply a way to avoid in-lining name copy and lookups,
 * since it's easy to forget a single case when done inline (crashing in some cases).
 *
 * \{ */

static void undosys_id_ref_store(void * /*user_data*/, UndoRefID *id_ref)
{
  BLI_assert(id_ref->name[0] == '\0');
  if (id_ref->ptr) {
    STRNCPY(id_ref->name, id_ref->ptr->name);
    if (id_ref->ptr->lib) {
      STRNCPY(id_ref->library_filepath_abs, id_ref->ptr->lib->runtime->filepath_abs);
    }
    else {
      id_ref->library_filepath_abs[0] = '\0';
    }
    /* Not needed, just prevents stale data access. */
    id_ref->ptr = nullptr;
  }
}

static void undosys_id_ref_resolve(void *user_data, UndoRefID *id_ref)
{
  /* NOTE: we could optimize this,
   * for now it's not too bad since it only runs when we access undo! */
  Main *bmain = static_cast<Main *>(user_data);
  id_ref->ptr = BKE_libblock_find_name_and_library_filepath(
      bmain,
      GS(id_ref->name),
      id_ref->name + 2,
      (id_ref->library_filepath_abs[0] ? id_ref->library_filepath_abs : nullptr));
}

static bool undosys_step_encode(bContext *C, Main *bmain, UndoStack *ustack, UndoStep *us)
{
  CLOG_DEBUG(&LOG, "addr=%p, name='%s', type='%s'", us, us->name, us->type->name);
  UNDO_NESTED_CHECK_BEGIN;
  bool ok = us->type->step_encode(C, bmain, us);
  UNDO_NESTED_CHECK_END;
  if (ok) {
    if (us->type->step_foreach_ID_ref != nullptr) {
      /* Don't use from context yet because sometimes context is fake and
       * not all members are filled in. */
      us->type->step_foreach_ID_ref(us, undosys_id_ref_store, bmain);
    }

#ifdef WITH_GLOBAL_UNDO_CORRECT_ORDER
    if (us->type == BKE_UNDOSYS_TYPE_MEMFILE) {
      ustack->step_active_memfile = us;
    }
#endif
  }
  if (ok == false) {
    CLOG_DEBUG(&LOG, "encode callback didn't create undo step");
  }
  return ok;
}

static bool undosys_step_decode(bContext *C,
                                Main *bmain,
                                UndoStack *ustack,
                                UndoStep *us,
                                const eUndoStepDir dir,
                                bool is_final)
{
  CLOG_DEBUG(&LOG, "addr=%p, name='%s', type='%s'", us, us->name, us->type->name);

  if (us->type->step_foreach_ID_ref) {
#ifdef WITH_GLOBAL_UNDO_CORRECT_ORDER
    if (us->type != BKE_UNDOSYS_TYPE_MEMFILE) {
      for (UndoStep *us_iter = us->prev; us_iter; us_iter = us_iter->prev) {
        if (us_iter->type == BKE_UNDOSYS_TYPE_MEMFILE) {
          if (us_iter == ustack->step_active_memfile) {
            /* Common case, we're already using the last memfile state. */
          }
          else {
            /* Load the previous memfile state so any ID's referenced in this
             * undo step will be correctly resolved, see: #56163. */
            if (!undosys_step_decode(C, bmain, ustack, us_iter, dir, false)) {
              return false;
            }
            /* May have been freed on memfile read. */
            bmain = G_MAIN;
          }
          break;
        }
      }
    }
#endif
    /* Don't use from context yet because sometimes context is fake and
     * not all members are filled in. */
    us->type->step_foreach_ID_ref(us, undosys_id_ref_resolve, bmain);
  }

  if (restore_traversal && restore_traversal->stack == ustack) {
    restore_traversal->decoding_step = us;
  }
  UNDO_NESTED_CHECK_BEGIN;
  UndoDecodeResult result = UndoDecodeResult::Success;
  if (us->type->step_decode_checked) {
    result = us->type->step_decode_checked(C, bmain, us, dir, is_final);
  }
  else {
    us->type->step_decode(C, bmain, us, dir, is_final);
  }
  UNDO_NESTED_CHECK_END;

  if (result == UndoDecodeResult::Refused) {
    return false;
  }
  if (result != UndoDecodeResult::Success) {
    ustack->recovery_required = true;
    if (Main *current_main = C ? CTX_data_main(C) : G_MAIN) {
      current_main->is_undo_recovery_required = true;
    }
    if (result == UndoDecodeResult::FailedStateInstalled) {
      /* The installed Main belongs to this boundary, even if its runtime adjunct failed.
       * Do not pretend that the previous or requested incremental step was restored. */
      ustack->step_active = us;
      if (us->type == BKE_UNDOSYS_TYPE_MEMFILE) {
        ustack->step_active_memfile = us;
      }
    }
    return false;
  }

#ifdef WITH_GLOBAL_UNDO_CORRECT_ORDER
  if (us->type == BKE_UNDOSYS_TYPE_MEMFILE) {
    ustack->step_active_memfile = us;
  }
#endif
  return true;
}

static void undosys_step_free_and_unlink(UndoStack *ustack, UndoStep *us)
{
  CLOG_DEBUG(&LOG, "addr=%p, name='%s', type='%s'", us, us->name, us->type->name);
  UNDO_NESTED_CHECK_BEGIN;
  us->type->step_free(us);
  UNDO_NESTED_CHECK_END;

  BLI_remlink(&ustack->steps, us);
  MEM_delete(us);

#ifdef WITH_GLOBAL_UNDO_CORRECT_ORDER
  if (ustack->step_active_memfile == us) {
    ustack->step_active_memfile = nullptr;
  }
#endif
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name Undo Stack
 * \{ */

#ifndef NDEBUG
static void undosys_stack_validate(UndoStack *ustack, bool expect_non_empty)
{
  if (ustack->step_active != nullptr) {
    BLI_assert(!ustack->steps.is_empty());
    BLI_assert(BLI_findindex(&ustack->steps, ustack->step_active) != -1);
  }
  if (expect_non_empty) {
    BLI_assert(!ustack->steps.is_empty());
  }
}
#else
static void undosys_stack_validate(UndoStack * /*ustack*/, bool /*expect_non_empty*/) {}
#endif

bool BKE_undosys_checkpoint_restore_retire_history(CheckpointCaptureOwner *owner)
{
  if (!BKE_undosys_checkpoint_capture_validate(owner) || !owner->restore_target ||
      owner->restore_rebased || owner->restore_history_retired) { return false; }
  UNDO_NESTED_ASSERT(false);
  const auto observation = owner->observation;
  for (const UndoStack *observed : observation->stacks) {
    if (!BKE_undosys_checkpoint_capture_validate(owner)) { return false; }
    UndoStack *stack = const_cast<UndoStack *>(observed);
    if (stack->group_level || stack->step_init) { return false; }
    /* Sculpt Undo free routines must run while their original Main/runtime is still alive,
     * not after controlled setup has freed it. Generic history remains closed throughout. */
    for (UndoStep *step = static_cast<UndoStep *>(stack->steps.last), *previous; step; step = previous) {
      previous = step->prev;
      undosys_step_free_and_unlink(stack, step);
    }
    stack->steps.clear_no_delete();
    stack->step_active = stack->step_active_memfile = nullptr;
  }
  if (!BKE_undosys_checkpoint_capture_validate(owner)) { return false; }
  owner->restore_history_retired = true;
  return true;
}

UndoStack *BKE_undosys_stack_create()
{
  UndoStack *ustack = MEM_new_zeroed<UndoStack>(__func__);
  return ustack;
}

bool BKE_undosys_recovery_required(const UndoStack *ustack, const Main *bmain)
{
  return (ustack && ustack->recovery_required) || (bmain && bmain->is_undo_recovery_required);
}

void BKE_undosys_stack_destroy(UndoStack *ustack)
{
  if (BKE_undosys_restore_protects_stack(ustack)) {
    return;
  }
  BKE_undosys_stack_clear(ustack);
  MEM_delete(ustack);
}

void BKE_undosys_stack_clear(UndoStack *ustack)
{
  if (BKE_undosys_restore_protects_stack(ustack)) {
    return;
  }
  UNDO_NESTED_ASSERT(false);
  CLOG_DEBUG(&LOG, "steps=%d", ustack->steps.count());
  for (UndoStep *us = static_cast<UndoStep *>(ustack->steps.last), *us_prev; us; us = us_prev) {
    us_prev = us->prev;
    undosys_step_free_and_unlink(ustack, us);
  }
  if (UndoStep *us = ustack->step_init) {
    undosys_step_free_and_unlink(ustack, us);
    ustack->step_init = nullptr;
  }
  ustack->steps.clear_no_delete();
  ustack->step_active = nullptr;
}

void BKE_undosys_stack_clear_active(UndoStack *ustack)
{
  if (BKE_undosys_restore_protects_stack(ustack)) {
    return;
  }
  /* Remove active and all following undo-steps. */
  UndoStep *us = ustack->step_active;

  if (us) {
    ustack->step_active = us->prev;
    bool is_not_empty = ustack->step_active != nullptr;

    while (ustack->steps.last != ustack->step_active) {
      UndoStep *us_iter = static_cast<UndoStep *>(ustack->steps.last);
      undosys_step_free_and_unlink(ustack, us_iter);
      undosys_stack_validate(ustack, is_not_empty);
    }
  }
}

/* Caller is responsible for handling active. */
static void undosys_stack_clear_all_last(UndoStack *ustack, UndoStep *us)
{
  if (us) {
    bool is_not_empty = true;
    UndoStep *us_iter;
    do {
      us_iter = static_cast<UndoStep *>(ustack->steps.last);
      BLI_assert(us_iter != ustack->step_active);
      undosys_step_free_and_unlink(ustack, us_iter);
      undosys_stack_validate(ustack, is_not_empty);
    } while (us != us_iter);
  }
}

static void undosys_stack_clear_all_first(UndoStack *ustack, UndoStep *us, UndoStep *us_exclude)
{
  if (us && us == us_exclude) {
    us = us->prev;
  }

  if (us) {
    bool is_not_empty = true;
    UndoStep *us_iter;
    do {
      us_iter = static_cast<UndoStep *>(ustack->steps.first);
      if (us_iter == us_exclude) {
        us_iter = us_iter->next;
      }
      BLI_assert(us_iter != ustack->step_active);
      undosys_step_free_and_unlink(ustack, us_iter);
      undosys_stack_validate(ustack, is_not_empty);
    } while (us != us_iter);
  }
}

static bool undosys_stack_push_main(UndoStack *ustack, const char *name, Main *bmain)
{
  UNDO_NESTED_ASSERT(false);
  BLI_assert(ustack->step_init == nullptr);
  CLOG_DEBUG(&LOG, "Push main '%s'", name);
  bContext *C_temp = CTX_create();
  CTX_data_main_set(C_temp, bmain);
  eUndoPushReturn ret = BKE_undosys_step_push_with_type(
      ustack, C_temp, name, BKE_UNDOSYS_TYPE_MEMFILE);
  CTX_free(C_temp);
  return (ret & UNDO_PUSH_RET_SUCCESS);
}

void BKE_undosys_stack_init_from_main(UndoStack *ustack, Main *bmain)
{
  if (BKE_undosys_restore_protects_stack(ustack)) {
    return;
  }
  UNDO_NESTED_ASSERT(false);
  /* A newly loaded, valid Main clears quarantine. Reinitializing the same Main does not. */
  ustack->recovery_required = bmain->is_undo_recovery_required;
  undosys_stack_push_main(ustack, IFACE_("Original"), bmain);
}

void BKE_undosys_stack_init_from_context(UndoStack *ustack, bContext *C)
{
  if (BKE_undosys_restore_protects_stack(ustack)) {
    return;
  }
  const UndoType *ut = BKE_undosys_type_from_context(C);
  if (!ELEM(ut, nullptr, BKE_UNDOSYS_TYPE_MEMFILE)) {
    BKE_undosys_step_push_with_type(ustack, C, IFACE_("Original Mode"), ut);
  }
}

bool BKE_undosys_stack_has_undo(const UndoStack *ustack, const char *name)
{
  if (ustack->recovery_required) {
    return false;
  }
  if (name) {
    const UndoStep *us = static_cast<UndoStep *>(
        BLI_rfindstring(&ustack->steps, name, offsetof(UndoStep, name)));
    return us && us->prev;
  }

  return !ustack->steps.is_empty();
}

bool BKE_undosys_stack_has_redo(const UndoStack *ustack)
{
  if (ustack->recovery_required || !ustack->step_active) {
    return false;
  }
  return ustack->step_active->next != nullptr;
}

UndoStep *BKE_undosys_stack_active_with_type(UndoStack *ustack, const UndoType *ut)
{
  UndoStep *us = ustack->step_active;
  while (us && (us->type != ut)) {
    us = us->prev;
  }
  return us;
}

UndoStep *BKE_undosys_stack_init_or_active_with_type(UndoStack *ustack, const UndoType *ut)
{
  UNDO_NESTED_ASSERT(false);
  if (ustack->step_init && (ustack->step_init->type == ut)) {
    return ustack->step_init;
  }
  return BKE_undosys_stack_active_with_type(ustack, ut);
}

static void undosys_stack_limit_steps_and_memory(UndoStack *ustack,
                                                 int steps,
                                                 size_t memory_limit,
                                                 UndoStep *minimum_keep)
{
  UNDO_NESTED_ASSERT(false);
  if ((steps == -1) && (memory_limit == 0)) {
    return;
  }

  CLOG_DEBUG(&LOG, "Limit steps=%d, memory_limit=%zu", steps, memory_limit);
  UndoStep *us;
  UndoStep *us_exclude = nullptr;
  /* keep at least two (original + other) */
  size_t data_size_all = 0;
  size_t us_count = 0;
  for (us = static_cast<UndoStep *>(ustack->steps.last); us && us->prev; us = us->prev) {
    if (memory_limit) {
      data_size_all += us->data_size;
      if (data_size_all > memory_limit) {
        CLOG_DEBUG(&LOG,
                   "At step %zu: data_size_all=%zu >= memory_limit=%zu",
                   us_count,
                   data_size_all,
                   memory_limit);
        break;
      }
    }
    if (steps != -1) {
      if (us_count == steps) {
        break;
      }
      if (us->skip == false) {
        us_count += 1;
      }
    }
  }

  CLOG_DEBUG(&LOG, "Total steps %zu: data_size_all=%zu", us_count, data_size_all);

  if (us) {
    /* A transactional publisher must retain at least its exact before state, even when one
     * snapshot exceeds the memory preference. Other publishers retain their existing policy. */
    if (minimum_keep) {
      for (UndoStep *iter = us->prev; iter; iter = iter->prev) {
        if (iter == minimum_keep) {
          us = minimum_keep;
          break;
        }
      }
    }
#ifdef WITH_GLOBAL_UNDO_KEEP_ONE
    /* Hack, we need to keep at least one BKE_UNDOSYS_TYPE_MEMFILE. */
    if (us->type != BKE_UNDOSYS_TYPE_MEMFILE) {
      us_exclude = us->prev;
      while (us_exclude && us_exclude->type != BKE_UNDOSYS_TYPE_MEMFILE) {
        us_exclude = us_exclude->prev;
      }
      /* Once this is outside the given number of 'steps', undoing onto this state
       * may skip past many undo steps which is confusing, instead,
       * disallow stepping onto this state entirely. */
      if (us_exclude) {
        us_exclude->skip = true;
      }
    }
#endif
    /* Free from first to last, free functions may update de-duplication info
     * (see #MemFileUndoStep). */
    undosys_stack_clear_all_first(ustack, us->prev, us_exclude);
  }
}

void BKE_undosys_stack_limit_steps_and_memory(UndoStack *ustack, int steps, size_t memory_limit)
{
  if (BKE_undosys_restore_protects_stack(ustack)) {
    return;
  }
  undosys_stack_limit_steps_and_memory(ustack, steps, memory_limit, nullptr);
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name Undo Step
 * \{ */

UndoStep *BKE_undosys_step_push_init_with_type(UndoStack *ustack,
                                               bContext *C,
                                               const char *name,
                                               const UndoType *ut)
{
  if (BKE_undosys_restore_protects_stack(ustack)) {
    return nullptr;
  }
  UNDO_NESTED_ASSERT(false);
  if (ut->step_encode_init) {
    undosys_stack_validate(ustack, false);

    if (UndoStep *us = ustack->step_init) {
      undosys_step_free_and_unlink(ustack, us);
      ustack->step_init = nullptr;
    }
    if (ustack->step_active) {
      undosys_stack_clear_all_last(ustack, ustack->step_active->next);
    }

    UndoStep *us = static_cast<UndoStep *>(MEM_new_zeroed(ut->step_size, __func__));
    if (name != nullptr) {
      STRNCPY(us->name, name);
    }
    us->type = ut;
    ustack->step_init = us;
    CLOG_INFO(&LOG, "Initialize type='%s'", ut->name);
    CLOG_DEBUG(&LOG, "addr=%p, name='%s', type='%s'", us, us->name, us->type->name);
    ut->step_encode_init(C, us);
    undosys_stack_validate(ustack, false);
    return us;
  }

  return nullptr;
}

UndoStep *BKE_undosys_step_push_init(UndoStack *ustack, bContext *C, const char *name)
{
  if (BKE_undosys_restore_protects_stack(ustack)) {
    return nullptr;
  }
  UNDO_NESTED_ASSERT(false);
  const UndoType *ut = BKE_undosys_type_from_context(C);
  if (ut == nullptr) {
    return nullptr;
  }
  return BKE_undosys_step_push_init_with_type(ustack, C, name, ut);
}

eUndoPushReturn BKE_undosys_step_push_with_type(UndoStack *ustack,
                                                bContext *C,
                                                const char *name,
                                                const UndoType *ut)
{
  if (BKE_undosys_restore_protects_stack(ustack)) {
    return UNDO_PUSH_RET_FAILURE;
  }
  BLI_assert((ut->flags & UNDOTYPE_FLAG_NEED_CONTEXT_FOR_ENCODE) == 0 || C != nullptr);

  UNDO_NESTED_ASSERT(false);
  undosys_stack_validate(ustack, false);
  bool is_not_empty = ustack->step_active != nullptr;
  eUndoPushReturn retval = UNDO_PUSH_RET_FAILURE;

  /* Might not be final place for this to be called - probably only want to call it from some
   * undo handlers, not all of them? */
  eRNAOverrideMatchResult report_flags = RNA_OVERRIDE_MATCH_RESULT_INIT;
  BKE_lib_override_library_main_operations_create(
      G_MAIN, false, reinterpret_cast<int *>(&report_flags));
  if (report_flags & RNA_OVERRIDE_MATCH_RESULT_CREATED) {
    retval |= UNDO_PUSH_RET_OVERRIDE_CHANGED;
  }

  /* Remove all undo-steps after (also when 'ustack->step_active == nullptr'). */
  while (ustack->steps.last != ustack->step_active) {
    UndoStep *us_iter = static_cast<UndoStep *>(ustack->steps.last);
    undosys_step_free_and_unlink(ustack, us_iter);
    undosys_stack_validate(ustack, is_not_empty);
  }

  if (ustack->step_active) {
    BLI_assert(BLI_findindex(&ustack->steps, ustack->step_active) != -1);
  }

#ifdef WITH_GLOBAL_UNDO_ENSURE_UPDATED
  if (ut->step_foreach_ID_ref != nullptr) {
    if (G_MAIN->is_memfile_undo_written == false) {
      const char *name_internal = "MemFile Internal (pre)";
      /* Don't let 'step_init' cause issues when adding memfile undo step. */
      void *step_init = ustack->step_init;
      ustack->step_init = nullptr;
      const bool ok = undosys_stack_push_main(ustack, name_internal, G_MAIN);
      /* Restore 'step_init'. */
      ustack->step_init = static_cast<UndoStep *>(step_init);
      if (ok) {
        UndoStep *us = static_cast<UndoStep *>(ustack->steps.last);
        BLI_assert(STREQ(us->name, name_internal));
        us->skip = true;
#  ifdef WITH_GLOBAL_UNDO_CORRECT_ORDER
        ustack->step_active_memfile = us;
#  endif
      }
    }
  }
#endif

  bool use_memfile_step = false;
  {
    UndoStep *us = ustack->step_init ?
                       ustack->step_init :
                       static_cast<UndoStep *>(MEM_new_zeroed(ut->step_size, __func__));
    ustack->step_init = nullptr;
    if (us->name[0] == '\0') {
      STRNCPY(us->name, name);
    }
    us->type = ut;
    /* True by default, code needs to explicitly set it to false if necessary. */
    us->use_old_bmain_data = true;
    /* Initialized, not added yet. */

    CLOG_DEBUG(&LOG, "addr=%p, name='%s', type='%s'", us, us->name, us->type->name);

    if (!undosys_step_encode(C, G_MAIN, ustack, us)) {
      MEM_delete(us);
      undosys_stack_validate(ustack, true);
      return retval;
    }
    ustack->step_active = us;
    BLI_addtail(&ustack->steps, us);
    use_memfile_step = us->use_memfile_step;
  }

  if (use_memfile_step) {
    /* Make this the user visible undo state, so redo always applies
     * on top of the mem-file undo instead of skipping it. see: #67256. */
    UndoStep *us_prev = ustack->step_active;
    const char *name_internal = us_prev->name;
    const bool ok = undosys_stack_push_main(ustack, name_internal, G_MAIN);
    if (ok) {
      UndoStep *us = static_cast<UndoStep *>(ustack->steps.last);
      BLI_assert(STREQ(us->name, name_internal));
      us_prev->skip = true;
#ifdef WITH_GLOBAL_UNDO_CORRECT_ORDER
      ustack->step_active_memfile = us;
#endif
      ustack->step_active = us;
    }
  }

  if (ustack->group_level > 0) {
    /* Temporarily set skip for the active step.
     * This is an invalid state which must be corrected once the last group ends. */
    ustack->step_active->skip = true;
  }

  undosys_stack_validate(ustack, true);
  return (retval | UNDO_PUSH_RET_SUCCESS);
}

eUndoPushReturn BKE_undosys_step_push(UndoStack *ustack, bContext *C, const char *name)
{
  if (BKE_undosys_restore_protects_stack(ustack)) {
    return UNDO_PUSH_RET_FAILURE;
  }
  UNDO_NESTED_ASSERT(false);
  const UndoType *ut = ustack->step_init ? ustack->step_init->type :
                                           BKE_undosys_type_from_context(C);
  if (ut == nullptr) {
    return UNDO_PUSH_RET_FAILURE;
  }
  return BKE_undosys_step_push_with_type(ustack, C, name, ut);
}

static void undosys_step_publish_memfiles(UndoStack *ustack, UndoStep *before, UndoStep *after)
{
  UNDO_NESTED_ASSERT(false);
  BLI_assert(ustack->step_init == nullptr && ustack->group_level == 0);
  BLI_assert(after && after->type == BKE_UNDOSYS_TYPE_MEMFILE);
  BLI_assert(after->prev == nullptr && after->next == nullptr);
  BLI_assert(!before || (before->type == BKE_UNDOSYS_TYPE_MEMFILE && before->prev == nullptr &&
                         before->next == nullptr));

  while (ustack->steps.last != ustack->step_active) {
    undosys_step_free_and_unlink(ustack, static_cast<UndoStep *>(ustack->steps.last));
  }
  UndoStep *minimum_keep = before ? before : ustack->step_active;
  if (before) {
    BLI_addtail(&ustack->steps, before);
  }
  after->skip = false;
  BLI_addtail(&ustack->steps, after);
  ustack->step_active = after;
  ustack->step_active_memfile = after;
  undosys_stack_limit_steps_and_memory(
      ustack, U.undosteps, size_t(U.undomemory) * 1024 * 1024, minimum_keep);
  if (before && before->skip) {
    /* If limits discarded all earlier visible history, the exact baseline becomes the initial
     * state. It adds no empty Undo action, and the newest commit still has one usable Undo. */
    bool has_visible_predecessor = false;
    for (UndoStep *iter = before->prev; iter; iter = iter->prev) {
      has_visible_predecessor |= !iter->skip;
    }
    before->skip = has_visible_predecessor;
  }
  undosys_stack_validate(ustack, true);
}

void BKE_undosys_step_publish_memfiles(UndoStack *ustack, UndoStep *before, UndoStep *after)
{
  if (!BKE_undosys_restore_protects_stack(ustack)) {
    undosys_step_publish_memfiles(ustack, before, after);
  }
}

bool BKE_undosys_capture_publish(UndoCaptureScope *scope, UndoStep *before, UndoStep *after)
{
  if (!BKE_undosys_capture_validate(scope) || scope->published ||
      BKE_undosys_restore_phase() != UndoRestorePhase::Preparing || !after)
  {
    return false;
  }
  undosys_step_publish_memfiles(scope->stack, before, after);
  scope->expected_active = after;
  scope->published = true;
  return true;
}

bool BKE_undosys_checkpoint_restore_publish(CheckpointCaptureOwner *owner,
                                           UndoStack *stack, UndoStep *initial_memfile)
{
  if (!BKE_undosys_checkpoint_capture_validate(owner) || !owner->restore_rebased ||
      owner->restore_published || !stack || !initial_memfile ||
      initial_memfile->type != BKE_UNDOSYS_TYPE_MEMFILE || initial_memfile->next || initial_memfile->prev ||
      !restore_main_owns_stack(*G_MAIN, stack) || stack->group_level || stack->step_init ||
      !stack->steps.is_empty() || stack->step_active || stack->step_active_memfile ||
      BLI_findindex(&stack->steps, initial_memfile) >= 0)
  {
    return false;
  }
  UNDO_NESTED_ASSERT(false);
  /* Source history was retired while source Main was live. The private caller has now built
   * complete target Main + Sculpt payload; no flush/encoder/free callback runs at publication. */
  if (!BKE_undosys_checkpoint_capture_validate(owner)) { return false; }
  initial_memfile->skip = false;
  BLI_addtail(&stack->steps, initial_memfile);
  stack->step_active = stack->step_active_memfile = initial_memfile;
  stack->recovery_required = G_MAIN->is_undo_recovery_required;
  owner->restore_published = true;
  undosys_stack_validate(stack, true);
  return true;
}

UndoStep *BKE_undosys_step_same_type_next(UndoStep *us)
{
  if (us) {
    const UndoType *ut = us->type;
    while ((us = us->next)) {
      if (us->type == ut) {
        return us;
      }
    }
  }
  return us;
}

UndoStep *BKE_undosys_step_same_type_prev(UndoStep *us)
{
  if (us) {
    const UndoType *ut = us->type;
    while ((us = us->prev)) {
      if (us->type == ut) {
        return us;
      }
    }
  }
  return us;
}

UndoStep *BKE_undosys_step_find_by_name_with_type(UndoStack *ustack,
                                                  const char *name,
                                                  const UndoType *ut)
{
  for (UndoStep &us : ustack->steps.items_reversed()) {
    if (us.type == ut) {
      if (STREQ(name, us.name)) {
        return &us;
      }
    }
  }
  return nullptr;
}

UndoStep *BKE_undosys_step_find_by_name(UndoStack *ustack, const char *name)
{
  return static_cast<UndoStep *>(BLI_rfindstring(&ustack->steps, name, offsetof(UndoStep, name)));
}

UndoStep *BKE_undosys_step_find_by_type(UndoStack *ustack, const UndoType *ut)
{
  for (UndoStep &us : ustack->steps.items_reversed()) {
    if (us.type == ut) {
      return &us;
    }
  }
  return nullptr;
}

eUndoStepDir BKE_undosys_step_calc_direction(const UndoStack *ustack,
                                             const UndoStep *us_target,
                                             const UndoStep *us_reference)
{
  if (us_reference == nullptr) {
    us_reference = ustack->step_active;
  }

  BLI_assert(us_reference != nullptr);

  /* Note that we use heuristics to make this lookup as fast as possible in most common cases,
   * assuming that:
   *  - Most cases are just undo or redo of one step from active one.
   *  - Otherwise, it is typically faster to check future steps since active one is usually close
   *    to the end of the list, rather than its start. */
  /* NOTE: in case target step is the active one, we assume we are in an undo case... */
  if (ELEM(us_target, us_reference, us_reference->prev)) {
    return STEP_UNDO;
  }
  if (us_target == us_reference->next) {
    return STEP_REDO;
  }

  /* Search forward, and then backward. */
  for (UndoStep *us_iter = us_reference->next; us_iter != nullptr; us_iter = us_iter->next) {
    if (us_iter == us_target) {
      return STEP_REDO;
    }
  }
  for (UndoStep *us_iter = us_reference->prev; us_iter != nullptr; us_iter = us_iter->prev) {
    if (us_iter == us_target) {
      return STEP_UNDO;
    }
  }

  BLI_assert_msg(0,
                 "Target undo step not found, this should not happen and may indicate an undo "
                 "stack corruption");
  return STEP_INVALID;
}

/**
 * When reading undo steps for undo/redo,
 * some extra checks are needed when so the correct undo step is decoded.
 */
static UndoStep *undosys_step_iter_first(UndoStep *us_reference, const eUndoStepDir undo_dir)
{
  if (us_reference->type->flags & UNDOTYPE_FLAG_DECODE_ACTIVE_STEP) {
    /* Reading this step means an undo action reads undo twice.
     * This should be avoided where possible, however some undo systems require it.
     *
     * Redo skips the current state as this represents the currently loaded state. */
    return (undo_dir == -1) ? us_reference : us_reference->next;
  }

  /* Typical case, skip reading the current undo step. */
  return (undo_dir == -1) ? us_reference->prev : us_reference->next;
}

bool BKE_undosys_step_load_data_ex(UndoStack *ustack,
                                   bContext *C,
                                   UndoStep *us_target,
                                   UndoStep *us_reference,
                                   const bool use_skip)
{
  if (BKE_undosys_restore_phase() != UndoRestorePhase::Inactive) {
    if (C && CTX_wm_manager(C)) {
      BKE_report(CTX_wm_reports(C), RPT_WARNING, BKE_undosys_restore_refusal_message());
    }
    return false;
  }
  SculptRestoreTraversal restore_scope(ustack);
  UNDO_NESTED_ASSERT(false);
  if (BKE_undosys_recovery_required(ustack, C ? CTX_data_main(C) : G_MAIN)) {
    return false;
  }
  const Main *bmain = C ? CTX_data_main(C) : G_MAIN;
  const char *refusal = nullptr;
  if (ustack->step_init || ustack->group_level != 0) {
    refusal = "Finish the active edit before restoring Undo history";
  }
  else if (bmain) {
    BKE_sculpt_undo_runtime_preflight(*bmain, &refusal);
  }
  if (refusal) {
    if (C && CTX_wm_manager(C)) {
      BKE_report(CTX_wm_reports(C), RPT_WARNING, refusal);
    }
    return false;
  }
  if (us_target == nullptr) {
    CLOG_ERROR(&LOG, "called with a nullptr target step");
    return false;
  }
  undosys_stack_validate(ustack, true);

  if (us_reference == nullptr) {
    us_reference = ustack->step_active;
  }
  if (us_reference == nullptr) {
    CLOG_ERROR(&LOG, "could not find a valid initial active target step as reference");
    return false;
  }

  /* This considers we are in undo case if both `us_target` and `us_reference` are the same. */
  const eUndoStepDir undo_dir = BKE_undosys_step_calc_direction(ustack, us_target, us_reference);
  BLI_assert(undo_dir != STEP_INVALID);

  /* This will be the active step once the undo process is complete.
   *
   * In case we do skip 'skipped' steps, the final active step may be several steps backward from
   * the one passed as parameter. */
  UndoStep *us_target_active = us_target;
  if (use_skip) {
    while (us_target_active != nullptr && us_target_active->skip) {
      us_target_active = (undo_dir == -1) ? us_target_active->prev : us_target_active->next;
    }
    if (us_target_active == nullptr) {
      CLOG_DEBUG(&LOG,
                 "undo/redo did not find a step after stepping over skip-steps "
                 "(undo limit exceeded)");
      return false;
    }
  }

  CLOG_DEBUG(&LOG,
             "addr=%p, name='%s', type='%s', undo_dir=%d",
             us_target,
             us_target->name,
             us_target->type->name,
             undo_dir);

  /* Undo/Redo steps until we reach given target step (or beyond if it has to be skipped),
   * from given reference step. */
  bool is_processing_extra_skipped_steps = false;
  for (UndoStep *us_iter = undosys_step_iter_first(us_reference, undo_dir); us_iter != nullptr;
       us_iter = (undo_dir == -1) ? us_iter->prev : us_iter->next)
  {
    BLI_assert(us_iter != nullptr);

    const bool is_final = (us_iter == us_target_active);

    if (!is_final && is_processing_extra_skipped_steps) {
      BLI_assert(us_iter->skip == true);
      CLOG_DEBUG(&LOG,
                 "undo/redo continue with skip addr=%p, name='%s', type='%s'",
                 us_iter,
                 us_iter->name,
                 us_iter->type->name);
    }

    if (!undosys_step_decode(C, G_MAIN, ustack, us_iter, undo_dir, is_final)) {
      return false;
    }
    ustack->step_active = us_iter;

    if (us_iter == us_target) {
      is_processing_extra_skipped_steps = true;
    }

    if (is_final) {
      /* No later decoder may run once final-state editor delivery starts. */
      if (!restore_scope.finalize(C)) {
        ustack->recovery_required = true;
        if (Main *current_main = C ? CTX_data_main(C) : G_MAIN) {
          current_main->is_undo_recovery_required = true;
        }
        if (C && CTX_wm_manager(C)) {
          BKE_report(
              CTX_wm_reports(C),
              RPT_ERROR,
              "Sculpt Undo editor ownership changed during restoration; recover a saved file");
        }
        return false;
      }
      return true;
    }
  }

  BLI_assert_msg(
      false,
      "This should never be reached, either undo stack is corrupted, or code above is buggy");
  return false;
}

bool BKE_undosys_step_load_data(UndoStack *ustack, bContext *C, UndoStep *us_target)
{
  /* Note that here we do not skip 'skipped' steps by default. */
  return BKE_undosys_step_load_data_ex(ustack, C, us_target, nullptr, false);
}

bool BKE_undosys_step_load_from_index(UndoStack *ustack, bContext *C, const int index)
{
  UndoStep *us_target = static_cast<UndoStep *>(BLI_findlink(&ustack->steps, index));
  if (!us_target || BKE_undosys_recovery_required(ustack, C ? CTX_data_main(C) : G_MAIN)) {
    return false;
  }
  BLI_assert(us_target->skip == false);
  if (us_target == ustack->step_active) {
    return true;
  }
  return BKE_undosys_step_load_data(ustack, C, us_target);
}

bool BKE_undosys_step_undo_with_data_ex(UndoStack *ustack,
                                        bContext *C,
                                        UndoStep *us_target,
                                        bool use_skip)
{
  /* In case there is no active step, we consider we just load given step, so reference must be
   * itself (due to weird 'load current active step in undo case' thing, see comments in
   * #BKE_undosys_step_load_data_ex). */
  UndoStep *us_reference = ustack->step_active != nullptr ? ustack->step_active : us_target;

  BLI_assert(BKE_undosys_step_calc_direction(ustack, us_target, us_reference) == -1);

  return BKE_undosys_step_load_data_ex(ustack, C, us_target, us_reference, use_skip);
}

bool BKE_undosys_step_undo_with_data(UndoStack *ustack, bContext *C, UndoStep *us_target)
{
  return BKE_undosys_step_undo_with_data_ex(ustack, C, us_target, true);
}

bool BKE_undosys_step_undo(UndoStack *ustack, bContext *C)
{
  if (ustack->step_active != nullptr) {
    return BKE_undosys_step_undo_with_data(ustack, C, ustack->step_active->prev);
  }
  return false;
}

bool BKE_undosys_step_redo_with_data_ex(UndoStack *ustack,
                                        bContext *C,
                                        UndoStep *us_target,
                                        bool use_skip)
{
  /* In case there is no active step, we consider we just load given step, so reference must be
   * the previous one. */
  UndoStep *us_reference = ustack->step_active != nullptr ? ustack->step_active : us_target->prev;

  BLI_assert(BKE_undosys_step_calc_direction(ustack, us_target, us_reference) == 1);

  return BKE_undosys_step_load_data_ex(ustack, C, us_target, us_reference, use_skip);
}

bool BKE_undosys_step_redo_with_data(UndoStack *ustack, bContext *C, UndoStep *us_target)
{
  return BKE_undosys_step_redo_with_data_ex(ustack, C, us_target, true);
}

bool BKE_undosys_step_redo(UndoStack *ustack, bContext *C)
{
  if (ustack->step_active != nullptr) {
    return BKE_undosys_step_redo_with_data(ustack, C, ustack->step_active->next);
  }
  return false;
}

UndoType *BKE_undosys_type_append(void (*undosys_fn)(UndoType *))
{
  UndoType *ut = MEM_new_zeroed<UndoType>(__func__);

  undosys_fn(ut);

  BLI_addtail(&g_undo_types, ut);

  return ut;
}

void BKE_undosys_type_free_all()
{
  while (UndoType *ut = static_cast<UndoType *>(BLI_pophead(&g_undo_types))) {
    MEM_delete(ut);
  }
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name Undo Stack Grouping
 *
 * This enables skip while group-level is set.
 * In general it's not allowed that #UndoStack.step_active have 'skip' enabled.
 *
 * This rule is relaxed for grouping, however it's important each call to
 * #BKE_undosys_stack_group_begin has a matching #BKE_undosys_stack_group_end.
 *
 * - Levels are used so nesting is supported, where the last call to #BKE_undosys_stack_group_end
 *   will set the active undo step that should not be skipped.
 *
 * - Correct begin/end is checked by an assert since any errors here will cause undo
 *   to consider all steps part of one large group.
 *
 * - Calls to begin/end with no undo steps being pushed is supported and does nothing.
 *
 * \{ */

void BKE_undosys_stack_group_begin(UndoStack *ustack)
{
  if (BKE_undosys_restore_protects_stack(ustack)) {
    return;
  }
  BLI_assert(ustack->group_level >= 0);
  ustack->group_level += 1;
}

void BKE_undosys_stack_group_end(UndoStack *ustack)
{
  if (BKE_undosys_restore_protects_stack(ustack)) {
    return;
  }
  ustack->group_level -= 1;
  BLI_assert(ustack->group_level >= 0);

  if (ustack->group_level == 0) {
    if (LIKELY(ustack->step_active != nullptr)) {
      ustack->step_active->skip = false;
    }
  }
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name ID Reference Utilities
 *
 * Unfortunately we need this for a handful of places.
 * \{ */

static void UNUSED_FUNCTION(BKE_undosys_foreach_ID_ref(UndoStack *ustack,
                                                       UndoTypeForEachIDRefFn foreach_ID_ref_fn,
                                                       void *user_data))
{
  for (UndoStep &us : ustack->steps) {
    const UndoType *ut = us.type;
    if (ut->step_foreach_ID_ref != nullptr) {
      ut->step_foreach_ID_ref(&us, foreach_ID_ref_fn, user_data);
    }
  }
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name Debug Helpers
 * \{ */

void BKE_undosys_print(UndoStack *ustack)
{
  if (!ustack) {
    printf("No undo steps recorded yet.\n");
    return;
  }
  printf("Undo %d Steps (*: active, #=applied, M=memfile-active, S=skip)\n",
         ustack->steps.count());
  int index = 0;
  for (UndoStep &us : ustack->steps) {
    printf("[%c%c%c%c] %3d {%p} type='%s', name='%s'\n",
           (&us == ustack->step_active) ? '*' : ' ',
           us.is_applied ? '#' : ' ',
           (&us == ustack->step_active_memfile) ? 'M' : ' ',
           us.skip ? 'S' : ' ',
           index,
           static_cast<void *>(&us),
           us.type->name,
           us.name);
    index++;
  }
}

/** \} */

}  // namespace blender
