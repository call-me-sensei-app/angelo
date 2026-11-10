/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

#include <cstdint>
#include <string>
#include <utility>

#include "BLI_span.hh"
#include "BLI_sys_types.h"
#include "BLI_vector.hh"

namespace blender {

struct AngeloUndoCapture;
struct ID;
struct bContext;

/** The address is only an identity key until membership in the current Main is established. */
struct AngeloCommitIDIdentity {
  ID *pointer = nullptr;
  uint session_uid = 0;
  short id_type = 0;

  bool operator==(const AngeloCommitIDIdentity &other) const;
};

enum class AngeloCommitDeletePurpose {
  PrivateStage,
  ApprovedObject,
  CreatedCollection,
  /** Live data a commit's own swaps replaced (data, Key, Material, NodeTree, Action, ParticleSettings,
   * World) that nothing uses any more: authority is the scope's sealed replaceable closure. */
  ReplacedData,
  /** Prepared self-only roots; cascade IDs retain the ordinary unused-data policy. */
  SelfReplacedData,
};

struct AngeloCommitDeleteRight;
struct AngeloCommitEngineRight;

struct AngeloCommitDeleteResult {
  bool kernel_called = false;
  bool scope_valid_after = false;
  bool unexpected_scope = false;
  int64_t expected_main_ids = 0;
  size_t kernel_deleted_ids = 0;
  /** Main and embedded identities from the initial proof, resolved without freed-pointer reads. */
  Vector<AngeloCommitIDIdentity> remaining;
  Vector<AngeloCommitIDIdentity> removed;
  /** Kernel-expanded pointers absent from the admitted set. Never dereference these addresses. */
  Vector<uintptr_t> unexpected_kernel_pointers;
};

/**
 * Private native entry points, called only while the capture's operation guard is owned.
 * The bridge acquires that guard BEFORE callback-capable Python argument conversion. Neither
 * these helpers nor their callers suspend ordinary public lifetime/history admissions.
 *
 * `authorized` is copied from the native stage attach/loader ownership, sealed approved-object
 * targets, or native Collection creation record. Arbitrary Python ID lists, names and stage tags
 * are not provenance. `roots` selects from that authority; `keep` protects transferred ownership.
 * Embedded children and shape keys join the proof natively. Every Main cascade member, including
 * shape keys and never-null users, must independently belong to `authorized`. Preparation and
 * consumption both inspect fresh links. Already removed ledger entries remain inert.
 */
AngeloCommitDeleteRight *ED_angelo_commit_delete_prepare(
    bContext *C,
    AngeloUndoCapture *capture,
    AngeloCommitDeletePurpose purpose,
    Span<AngeloCommitIDIdentity> roots,
    Span<AngeloCommitIDIdentity> authorized,
    Span<AngeloCommitIDIdentity> keep,
    const char **r_error);

/** Prepare one all-proof-before-any-free batch of sealed plain roots held only by their own counted
 * self edges. Every surviving outside link still passes the ordinary deletion proof. A null result
 * without an error is a valid empty plan. This does not broaden the ordinary zero-user free loop. */
AngeloCommitDeleteRight *ED_angelo_commit_self_replaced_prepare(
    bContext *C,
    AngeloUndoCapture *capture,
    Span<AngeloCommitIDIdentity> roots,
    Span<AngeloCommitIDIdentity> authorized,
    Vector<AngeloCommitIDIdentity> &r_eligible,
    const char **r_error);

/** Fresh, non-consuming revalidation while the caller can still roll its transaction back. */
bool ED_angelo_commit_delete_validate(bContext *C,
                                      AngeloUndoCapture *capture,
                                      const AngeloCommitDeleteRight &right,
                                      const char **r_error);

/** Consumes the right on its first attempt, including a preflight refusal. No fallback deletion. */
bool ED_angelo_commit_delete(bContext *C,
                             AngeloUndoCapture *capture,
                             AngeloCommitDeleteRight &right,
                             AngeloCommitDeleteResult &r_result,
                             const char **r_error);
void ED_angelo_commit_delete_free(AngeloCommitDeleteRight *right);

/**
 * The replaceable closure of `holders` (the written Objects and the written Scene, before any commit
 * write): every local Main ID of a family a commit swaps (object data, Key, Material, NodeTree,
 * Action, ParticleSettings, World) the holders reach through counted (user) links, transitively
 * through those families only. This is the authority of #AngeloCommitDeletePurpose::ReplacedData:
 * data the written objects held before the commit, never another ID of the file.
 */
bool ED_angelo_commit_replaceable(bContext *C,
                                  AngeloUndoCapture *capture,
                                  Span<AngeloCommitIDIdentity> holders,
                                  Vector<AngeloCommitIDIdentity> &r_closure,
                                  const char **r_error);

struct AngeloCommitFreeResult {
  /** Identities the passes freed (Main and embedded), checked absent from Main afterwards. */
  Vector<AngeloCommitIDIdentity> removed;
  /** Roots (any family) still in Main with the reason their last proof refused them. */
  Vector<std::pair<AngeloCommitIDIdentity, std::string>> refused;
  /** Pointers the kernel freed outside the proven closures. Never dereference these addresses. */
  Vector<uintptr_t> unexpected;
  int passes = 0;
  bool unfinished = false;
};

/**
 * Free the replaced data `roots` that nothing uses, through the native deletion proof, in passes:
 * a root becomes eligible once nothing uses it (after another root's free). Never anything but a
 * root and its stock cascade (Key, embedded data); every root must be in `authorized` (the sealed
 * replaceable closure). One Main inventory per pass, read after every preview job is stopped. Roots refused
 * one by one are tried together once (roots that only point at each other). At most `max_passes`.
 * Returns false only when the scope changed or the kernel freed outside a proof (reported).
 */
bool ED_angelo_commit_free_replaced(bContext *C,
                                    AngeloUndoCapture *capture,
                                    Span<AngeloCommitIDIdentity> roots,
                                    Span<AngeloCommitIDIdentity> authorized,
                                    int max_passes,
                                    AngeloCommitFreeResult &r_result,
                                    const char **r_error);

struct AngeloCommitEngineResult {
  bool setter_called = false;
  bool update_called = false;
  bool scope_valid_after = false;
  bool scene_resolved_after = false;
  std::string actual_engine;
};

/**
 * Bind the exact admitted Scene and engine transition, including its inverse. The trusted caller
 * supplies the sealed action/transfer target, not a new arbitrary property request. Both names
 * must be installed RNA enum items; existing custom-engine targets/preimages remain supported.
 * There is deliberately no generic path, generic property setter, or built-in-name whitelist.
 */
AngeloCommitEngineRight *ED_angelo_commit_engine_prepare(bContext *C,
                                                         AngeloUndoCapture *capture,
                                                         AngeloCommitIDIdentity scene,
                                                         const char *expected,
                                                         const char *target,
                                                         const char **r_error);

/** One forward attempt and, only after its setter ran, one inverse attempt with exact preimage. */
bool ED_angelo_commit_engine_apply(bContext *C,
                                   AngeloUndoCapture *capture,
                                   AngeloCommitEngineRight &right,
                                   bool rollback,
                                   AngeloCommitEngineResult &r_result,
                                   const char **r_error);
void ED_angelo_commit_engine_free(AngeloCommitEngineRight *right);

}  // namespace blender
