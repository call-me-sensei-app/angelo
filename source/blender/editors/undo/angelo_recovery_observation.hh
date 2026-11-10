/* SPDX-FileCopyrightText: 2026 Angelo Contributors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

#include <array>
#include <memory>
#include <optional>

#include "BLI_session_uid.h"

namespace blender::ed::undo::recovery {

/** Native-only lifecycle foundation. These identities do not certify snapshot contents or effects.
 * Actual capture binding, checked restoration and terminal verification remain unready. */
struct Binding {
  SessionUID document;
  SessionUID invocation;
  SessionUID preimage;
};

enum class EffectState { Prepared, RecoveryRequired, TargetUnavailable };
enum class Attachment { Attached, Detached };
enum class PrepareFailure { None, WrongThread, InvalidIdentity, ExistingOwner, Capacity };

struct Status {
  EffectState effect;
  Attachment attachment;
  bool capture_open;
  bool preimage_history_alive;
  bool afterimage_history_alive;
  /* Lifecycle observations are never an effect/recovery certificate. */
  bool effect_certified = false;
  bool recovery_proven = false;
};

class Observation;
using Handle = std::shared_ptr<Observation>;

/** Bounded native ownership, independent of request handles and capture close.
 *
 * One unresolved observation per document; no eviction of armed/detached observations.
 * Capacity failure must refuse before effects, never discard prior unresolved ownership.
 * No API in this foundation certifies effects, restores a preimage or clears armed uncertainty.
 * Methods require the main thread. Foreign/stale handles are refused without reading their data.
 */
class Registry {
 public:
  static constexpr int capacity = 16;

  Registry();
  ~Registry();
  Registry(const Registry &) = delete;
  Registry &operator=(const Registry &) = delete;

  Handle prepare(const Binding &binding, PrepareFailure *failure = nullptr);
  bool mark_effects_possible(const Handle &handle);
  bool note_capture_closed(const Handle &handle);
  bool bind_history(const Handle &handle, SessionUID preimage, SessionUID afterimage);
  void note_step_freed(SessionUID step);
  void note_document_detached(SessionUID document);
  bool cancel_before_effects(const Handle &handle);
  std::optional<Status> status(const Handle &handle) const;
  std::optional<Status> status_for_document(SessionUID document) const;
  int size() const;

 private:
  std::array<Handle, capacity> observations_{};
  Observation *find(const Handle &handle) const;
};

const char *prepare_failure_message(PrepareFailure failure);

/** Readiness is separate from schema/lifecycle support. This scaffold grants no admission. */
struct Capabilities {
  static constexpr int schema_version = 1;
  static constexpr bool identity_binding_ready = false;
  static constexpr bool exact_preimage_ready = false;
  static constexpr bool checked_restore_ready = false;
  static constexpr bool terminal_effect_seal_ready = false;
};

}  // namespace blender::ed::undo::recovery
