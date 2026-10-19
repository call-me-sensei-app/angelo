/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

#include <cstdint>
#include <string>

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

/** Consumes the right on its first attempt, including a preflight refusal. No fallback deletion. */
bool ED_angelo_commit_delete(bContext *C,
                             AngeloUndoCapture *capture,
                             AngeloCommitDeleteRight &right,
                             AngeloCommitDeleteResult &r_result,
                             const char **r_error);
void ED_angelo_commit_delete_free(AngeloCommitDeleteRight *right);

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
