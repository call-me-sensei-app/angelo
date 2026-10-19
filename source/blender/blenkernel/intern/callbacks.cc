/* SPDX-FileCopyrightText: 2023 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup bke
 */

#include "BLI_listbase.h"
#include "BLI_threads.h"
#include <atomic>
#include <mutex>

#include "BKE_callbacks.hh"
#include "BKE_global.hh"
#include "BKE_undo_system.hh"

#include "MEM_guardedalloc.h"

#include "RNA_access.hh"
#include "RNA_prototypes.hh"

namespace blender {

struct CheckpointCallbackObservation {
  Main *target;
  void *user_data;
  void *(*before)(void *, Main *);
  bool (*after)(void *, void *, Main *);
  std::atomic<bool> valid{true};
  std::atomic<bool> safe{true};
};
static std::mutex checkpoint_callback_mutex;
static std::shared_ptr<CheckpointCallbackObservation> checkpoint_callback_observation;
static std::atomic<bool> checkpoint_callback_active{false};
static std::shared_ptr<CheckpointCallbackObservation> checkpoint_callback_get()
{
  if (!checkpoint_callback_active.load(std::memory_order_acquire)) { return nullptr; }
  std::lock_guard lock(checkpoint_callback_mutex);
  return checkpoint_callback_observation;
}

ScopedCheckpointCallbackObserver::ScopedCheckpointCallbackObserver(
    Main *target, void *user_data, void *(*before)(void *, Main *),
    bool (*after)(void *, void *, Main *))
{
  BLI_assert(BLI_thread_is_main());
  auto observation = std::make_shared<CheckpointCallbackObservation>();
  observation->target = target;
  observation->user_data = user_data;
  observation->before = before;
  observation->after = after;
  std::lock_guard lock(checkpoint_callback_mutex);
  if (checkpoint_callback_observation || !target || !before || !after) {
    observation->safe.store(false, std::memory_order_release);
    observation->valid.store(false, std::memory_order_release);
  }
  else {
    checkpoint_callback_observation = observation;
    checkpoint_callback_active.store(true, std::memory_order_release);
  }
  observation_ = std::move(observation);
}
ScopedCheckpointCallbackObserver::~ScopedCheckpointCallbackObserver()
{
  std::shared_ptr<CheckpointCallbackObservation> retired;
  {
    std::lock_guard lock(checkpoint_callback_mutex);
    if (checkpoint_callback_observation == observation_) {
      retired = std::move(checkpoint_callback_observation);
      checkpoint_callback_active.store(false, std::memory_order_release);
    }
  }
  /* Worker leases can release metadata after this scope; they never touch user_data/witnesses. */
}
bool ScopedCheckpointCallbackObserver::valid() const
{
  return observation_ && observation_->valid.load(std::memory_order_acquire);
}
bool BKE_checkpoint_callback_observation_valid(const Main *main)
{
  auto observation = checkpoint_callback_get();
  if (main && observation && observation->target != main) { return true; }
  return !observation || observation->valid.load(std::memory_order_acquire);
}
bool BKE_checkpoint_callback_observation_safe(const Main *main)
{
  auto observation = checkpoint_callback_get();
  if (main && observation && observation->target != main) { return true; }
  return !observation || observation->safe.load(std::memory_order_acquire);
}
void BKE_checkpoint_callback_observation_unavailable(const Main *main)
{
  auto observation = checkpoint_callback_get();
  if (!main || !observation || observation->target != main) { return; }
  observation->safe.store(false, std::memory_order_release);
  observation->valid.store(false, std::memory_order_release);
}
ScopedCheckpointCallback::ScopedCheckpointCallback(Main *main)
{
  auto observation = checkpoint_callback_get();
  if (!observation || main != observation->target) { return; }
  observation_ = std::move(observation);
  /* A witnessed conflict must not hide edits from later handlers. Unsafe access, however,
   * permanently forbids new witnesses, even if a subsequent callback runs on the main thread. */
  if (!observation_->safe.load(std::memory_order_acquire)) { return; }
  if (!BLI_thread_is_main() || G_MAIN != main ||
      BKE_undosys_restore_phase() != UndoRestorePhase::Checkpoint ||
      !BKE_undosys_restore_protects_main(main)) {
    observation_->safe.store(false, std::memory_order_release);
    observation_->valid.store(false, std::memory_order_release);
    return;
  }
  witness_ = observation_->before(observation_->user_data, main);
  if (!witness_) {
    observation_->safe.store(false, std::memory_order_release);
    observation_->valid.store(false, std::memory_order_release);
  }
}
ScopedCheckpointCallback::~ScopedCheckpointCallback()
{
  if (!observation_ || !witness_) { return; }
  /* If a foreign native operation replaced Main, cleanup receives null and must only dispose
   * its owned witness. Never hand a potentially freed cached Main to the transient verifier. */
  Main *live = nullptr;
  if (BLI_thread_is_main() && observation_->safe.load(std::memory_order_acquire) &&
      G_MAIN == observation_->target &&
      BKE_undosys_restore_phase() == UndoRestorePhase::Checkpoint &&
      BKE_undosys_restore_protects_main(observation_->target)) {
    live = G_MAIN;
  }
  else {
    observation_->safe.store(false, std::memory_order_release);
    observation_->valid.store(false, std::memory_order_release);
  }
  /* An already owned token still needs disposal after an unsafe nested callback. Null live
   * forbids observation/traversal while preserving the before/after ownership contract. */
  if (!observation_->after(observation_->user_data, witness_, live)) {
    observation_->valid.store(false, std::memory_order_release);
  }
}

static ListBaseT<bCallbackFuncStore> callback_slots[BKE_CB_EVT_TOT] = {{nullptr}};

static bool callbacks_initialized = false;

#define ASSERT_CALLBACKS_INITIALIZED() \
  BLI_assert_msg(callbacks_initialized, \
                 "Callbacks should be initialized with BKE_callback_global_init() before using " \
                 "the callback system.")

void BKE_callback_exec(Main *bmain, PointerRNA **pointers, const int pointers_num, eCbEvent evt)
{
  ASSERT_CALLBACKS_INITIALIZED();

  /* Use mutable iteration so handlers are able to remove themselves. */
  ListBaseT<bCallbackFuncStore> *lb = &callback_slots[evt];
  for (bCallbackFuncStore &funcstore : lb->items_mutable()) {
    ScopedCheckpointCallback callback(funcstore.checkpoint_python_dispatch ? nullptr : bmain);
    funcstore.func(bmain, pointers, pointers_num, funcstore.arg);
  }
}

void BKE_callback_exec_null(Main *bmain, eCbEvent evt)
{
  BKE_callback_exec(bmain, nullptr, 0, evt);
}

void BKE_callback_exec_id(Main *bmain, ID *id, eCbEvent evt)
{
  PointerRNA id_ptr = RNA_id_pointer_create(id);

  PointerRNA *pointers[1] = {&id_ptr};

  BKE_callback_exec(bmain, pointers, ARRAY_SIZE(pointers), evt);
}

void BKE_callback_exec_id_depsgraph(Main *bmain, ID *id, Depsgraph *depsgraph, eCbEvent evt)
{
  PointerRNA id_ptr = RNA_id_pointer_create(id);

  PointerRNA depsgraph_ptr = RNA_pointer_create_discrete(nullptr, RNA_Depsgraph, depsgraph);

  PointerRNA *pointers[2] = {&id_ptr, &depsgraph_ptr};

  BKE_callback_exec(bmain, pointers, ARRAY_SIZE(pointers), evt);
}

void BKE_callback_exec_boolean(Main *bmain, bool value, eCbEvent evt)
{
  PrimitiveBooleanRNA data = {};
  data.value = value;
  PointerRNA boolean_ptr = RNA_pointer_create_discrete(nullptr, RNA_PrimitiveBoolean, &data);

  PointerRNA *pointers[1] = {&boolean_ptr};

  BKE_callback_exec(bmain, pointers, ARRAY_SIZE(pointers), evt);
}

void BKE_callback_exec_string(Main *bmain, const char *str, eCbEvent evt)
{
  PrimitiveStringRNA data = {nullptr};
  data.value = str;
  PointerRNA str_ptr = RNA_pointer_create_discrete(nullptr, RNA_PrimitiveString, &data);

  PointerRNA *pointers[1] = {&str_ptr};

  BKE_callback_exec(bmain, pointers, ARRAY_SIZE(pointers), evt);
}

void BKE_callback_add(bCallbackFuncStore *funcstore, eCbEvent evt)
{
  ASSERT_CALLBACKS_INITIALIZED();
  ListBaseT<bCallbackFuncStore> *lb = &callback_slots[evt];
  BLI_addtail(lb, funcstore);
}

void BKE_callback_remove(bCallbackFuncStore *funcstore, eCbEvent evt)
{
  /* The callback may have already been removed by BKE_callback_global_finalize(), for
   * example when removing callbacks in response to a BKE_blender_atexit_register callback
   * function. `BKE_blender_atexit()` runs after `BKE_callback_global_finalize()`. */
  if (!callbacks_initialized) {
    return;
  }

  ListBaseT<bCallbackFuncStore> *lb = &callback_slots[evt];

  /* Be noisy about potential programming errors. */
  BLI_assert_msg(BLI_findindex(lb, funcstore) != -1, "To-be-removed callback not found");

  BLI_remlink(lb, funcstore);

  if (funcstore->alloc) {
    MEM_delete(funcstore);
  }
}

void BKE_callback_global_init()
{
  callbacks_initialized = true;
}

void BKE_callback_global_finalize()
{
  for (int evt_i = 0; evt_i < BKE_CB_EVT_TOT; evt_i++) {
    const eCbEvent evt = eCbEvent(evt_i);
    ListBaseT<bCallbackFuncStore> *lb = &callback_slots[evt];
    bCallbackFuncStore *funcstore;
    bCallbackFuncStore *funcstore_next;
    for (funcstore = static_cast<bCallbackFuncStore *>(lb->first); funcstore;
         funcstore = funcstore_next)
    {
      funcstore_next = funcstore->next;
      BKE_callback_remove(funcstore, evt);
    }
  }

  callbacks_initialized = false;
}

}  // namespace blender
