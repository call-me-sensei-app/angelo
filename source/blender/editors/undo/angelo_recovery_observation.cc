/* SPDX-FileCopyrightText: 2026 Angelo Contributors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "angelo_recovery_observation.hh"

#include "BLI_threads.h"

namespace blender::ed::undo::recovery {

class Observation {
 public:
  const Binding binding;
  EffectState effect = EffectState::Prepared;
  Attachment attachment = Attachment::Attached;
  bool capture_open = true;
  bool history_bound = false;
  bool preimage_alive = false;
  bool afterimage_alive = false;
  SessionUID afterimage;

  explicit Observation(const Binding &binding) : binding(binding) {}
};

static bool generated(const SessionUID &uid)
{
  return BLI_session_uid_is_generated(&uid);
}

Registry::Registry() = default;
Registry::~Registry() = default;

Observation *Registry::find(const Handle &handle) const
{
  if (!BLI_thread_is_main() || !handle) {
    return nullptr;
  }
  for (const Handle &owned : observations_) {
    if (owned && owned == handle) {
      return owned.get();
    }
  }
  return nullptr;
}

Handle Registry::prepare(const Binding &binding, PrepareFailure *failure)
{
  auto refuse = [&](const PrepareFailure why) -> Handle {
    if (failure) {
      *failure = why;
    }
    return nullptr;
  };
  if (failure) {
    *failure = PrepareFailure::None;
  }
  if (!BLI_thread_is_main()) {
    return refuse(PrepareFailure::WrongThread);
  }
  if (!generated(binding.document) || !generated(binding.invocation) ||
      !generated(binding.preimage))
  {
    return refuse(PrepareFailure::InvalidIdentity);
  }
  for (const Handle &owned : observations_) {
    if (owned && owned->binding.document == binding.document)
    {
      return refuse(PrepareFailure::ExistingOwner);
    }
  }
  for (Handle &slot : observations_) {
    if (!slot) {
      slot = std::make_shared<Observation>(binding);
      return slot;
    }
  }
  return refuse(PrepareFailure::Capacity);
}

const char *prepare_failure_message(const PrepareFailure failure)
{
  switch (failure) {
    case PrepareFailure::None:
      return "";
    case PrepareFailure::WrongThread:
      return "Native recovery observations require the main thread; nothing was applied";
    case PrepareFailure::InvalidIdentity:
      return "Native recovery needs the exact document, invocation and preimage identities; "
             "nothing was applied";
    case PrepareFailure::ExistingOwner:
      return "This document already owns a native recovery observation; finish or cancel its "
             "preparation, "
             "or complete its owned recovery before another irreversible edit";
    case PrepareFailure::Capacity:
      return "Native recovery observation capacity is full; complete an associated owned preimage "
             "or checkpoint recovery before another irreversible edit. "
             "No unresolved owner was discarded";
  }
  return "Native recovery observation admission is unavailable; nothing was applied";
}

bool Registry::mark_effects_possible(const Handle &handle)
{
  Observation *owned = find(handle);
  if (!owned || !owned->capture_open || owned->attachment != Attachment::Attached ||
      owned->effect != EffectState::Prepared ||
      (owned->history_bound && !owned->preimage_alive))
  {
    return false;
  }
  owned->effect = EffectState::RecoveryRequired;
  return true;
}

bool Registry::note_capture_closed(const Handle &handle)
{
  Observation *owned = find(handle);
  if (!owned) {
    return false;
  }
  owned->capture_open = false;
  return true;
}

bool Registry::bind_history(const Handle &handle,
                           const SessionUID preimage,
                           const SessionUID afterimage)
{
  Observation *owned = find(handle);
  if (!owned || !owned->capture_open || owned->attachment != Attachment::Attached ||
      owned->history_bound || !generated(preimage) ||
      !generated(afterimage) || !(preimage == owned->binding.preimage) || preimage == afterimage)
  {
    return false;
  }
  owned->afterimage = afterimage;
  owned->history_bound = true;
  owned->preimage_alive = true;
  owned->afterimage_alive = true;
  return true;
}

void Registry::note_step_freed(const SessionUID step)
{
  if (!BLI_thread_is_main() || !generated(step)) {
    return;
  }
  for (const Handle &owned : observations_) {
    if (!owned || !owned->history_bound) {
      continue;
    }
    if (owned->binding.preimage == step) {
      owned->preimage_alive = false;
      if (owned->effect == EffectState::RecoveryRequired) {
        owned->effect = EffectState::TargetUnavailable;
      }
    }
    if (owned->afterimage == step) {
      owned->afterimage_alive = false;
    }
  }
}

void Registry::note_document_detached(const SessionUID document)
{
  if (!BLI_thread_is_main() || !generated(document)) {
    return;
  }
  for (const Handle &owned : observations_) {
    if (owned && owned->binding.document == document) {
      owned->attachment = Attachment::Detached;
    }
  }
}

bool Registry::cancel_before_effects(const Handle &handle)
{
  Observation *owned = find(handle);
  if (!owned || owned->effect != EffectState::Prepared) {
    return false;
  }
  for (Handle &slot : observations_) {
    if (slot.get() == owned) {
      slot.reset();
      return true;
    }
  }
  return false;
}

std::optional<Status> Registry::status(const Handle &handle) const
{
  const Observation *owned = find(handle);
  if (!owned) {
    return std::nullopt;
  }
  return Status{owned->effect,
                owned->attachment,
                owned->capture_open,
                owned->preimage_alive,
                owned->afterimage_alive};
}

std::optional<Status> Registry::status_for_document(const SessionUID document) const
{
  if (!BLI_thread_is_main() || !generated(document)) {
    return std::nullopt;
  }
  for (const Handle &owned : observations_) {
    if (owned && owned->binding.document == document) {
      return status(owned);
    }
  }
  return std::nullopt;
}

int Registry::size() const
{
  if (!BLI_thread_is_main()) {
    return 0;
  }
  int result = 0;
  for (const Handle &owned : observations_) {
    result += bool(owned);
  }
  return result;
}

}  // namespace blender::ed::undo::recovery
