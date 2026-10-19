/* SPDX-FileCopyrightText: 2023 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

#include <cstddef>
#include <cstdint>

#include "BLI_span.hh"

/** \file
 * \ingroup bmesh
 */

struct RangeTreeUInt;
namespace blender {

struct BMFace;
struct BMVert;
struct BMesh;
struct BMLog;
struct BMLogEntry;
struct BMLogSnapshot;
struct BMLogSnapshotRestore;

/**
 * Allocate, initialize, and assign a new BMLog.
 */
BMLog *BM_log_create(BMesh *bm);

/**
 * Allocate and initialize a new #BMLog using existing #BMLogEntries
 *
 * The unused IDs field of the log will be initialized by taking all
 * keys from all Maps in the log entry.
 *
 * \param entry: The last entry of the prior BMLog, its `prev` pointer will be followed back to
 * reconstruct the log.
 */
BMLog *BM_log_from_existing_entries_create(BMesh *bm, BMLogEntry *entry);

/**
 * Immutable identity of the live elements and current history position. This does not copy the
 * mesh, log entries, or their Undo/Redo swap buffers. The BMesh iteration order must be preserved
 * when rebuilding the mesh for #BM_log_snapshot_restore_prepare.
 *
 * Only live vertices and faces are inspected; a log's maps can also contain dead element pointers.
 * Returns null and a static diagnostic on invalid live element identities or non-triangle faces.
 */
BMLogSnapshot *BM_log_snapshot_create(BMesh *bm, const BMLog *log, const char **r_error = nullptr);
void BM_log_snapshot_free(BMLogSnapshot *snapshot);
size_t BM_log_snapshot_memory_size(const BMLogSnapshot *snapshot);
bool BM_log_snapshot_equals(const BMLogSnapshot *a, const BMLogSnapshot *b);
uint64_t BM_log_snapshot_epoch(const BMLogSnapshot *snapshot);
/** Zero denotes the state before the first entry. */
uint64_t BM_log_snapshot_cursor_id(const BMLogSnapshot *snapshot);

/** Read-only live history identifiers. The caller protects the log's lifetime and history.
 * The epoch is retained before the first entry; a zero cursor identifies that initial state. */
uint64_t BM_log_epoch(const BMLog *log);
uint64_t BM_log_cursor_id(const BMLog *log);

/** Identities remain valid after the live BMLog is freed, while its owning Undo step survives. */
uint64_t BM_log_entry_epoch(const BMLogEntry *entry);
uint64_t BM_log_entry_identity(const BMLogEntry *entry);

/**
 * Validate a reconstructed mesh and prepare its log without modifying the retained entries.
 * `entries` must contain the entire surviving entry chain for this snapshot's epoch in
 * chronological order. The caller owns their lifetime and must hold the mesh and retained history
 * stable until commit or disposal, except for freeing the prior live log. All past and future IDs,
 * including face vertex references, remain reserved.
 * Every retained entry must be ownerless or owned by exactly `expected_old_log`.
 *
 * The cursor is the greatest surviving entry identity not newer than the saved cursor, or null
 * when there is no such entry. An empty chain is valid. No entry buffers are copied or swapped.
 * Returns null and a static diagnostic on failure. The opaque result owns all prepared storage;
 * #BM_log_snapshot_restore_discard disposes it without touching the old log or retained entries.
 */
BMLogSnapshotRestore *BM_log_snapshot_restore_prepare(BMesh *bm,
                                                      const BMLogSnapshot *snapshot,
                                                      Span<BMLogEntry *> entries,
                                                      const BMLog *expected_old_log,
                                                      const char **r_error = nullptr);
/** Private checkpoint replay: finite clone of all live retained-chain swap buffers before
 * native work resumes. Entry addresses/links/epoch stay owned by existing artist Undo steps.
 * Caller holds exclusive log/entry access and keeps their lifetime protected through commit. */
BMLogSnapshotRestore *BM_log_snapshot_restore_prepare_bounded(
    BMesh *bm, const BMLogSnapshot *snapshot, const BMLog *expected_old_log,
    size_t limit, size_t &used, const char **r_error = nullptr);
/** Allocation-free proof of the exact retained chain/owner before retiring its runtime. */
bool BM_log_snapshot_restore_replay_validate(const BMLogSnapshotRestore *restore,
                                              const BMLog *live_log);
bool BM_log_snapshot_restore_replay_equals(const BMLogSnapshotRestore *a,
                                            const BMLogSnapshotRestore *b);
void BM_log_snapshot_restore_discard(BMLogSnapshotRestore *restore);
/**
 * Consume a validated preparation and bind its log to the retained entries, without allocating,
 * invoking callbacks, or returning failure. The exact expected old owner must have been freed so
 * every entry is now ownerless. The caller must also exclude shared entry sets/owners when
 * preparing several objects. Mesh and history must remain unchanged since prepare.
 *
 * These are caller invariants, asserted before binding, not fallible checks after runtime
 * teardown.
 */
BMLog *BM_log_snapshot_restore_commit(BMLogSnapshotRestore *restore);

/**
 * Free all the data in a BMLog including the log itself.
 */
void BM_log_free(BMLog *log);

/**
 * Start a new log entry and update the log entry list.
 *
 * If the log entry list is empty, or if the current log entry is the
 * last entry, the new entry is simply appended to the end.
 *
 * Finally, the new entry is set as the current log entry.
 */
BMLogEntry *BM_log_entry_add(BMLog *log);

/** Mark all used ids as unused for this node */
void BM_log_cleanup_entry(BMLogEntry *entry);

/**
 * Remove an entry from the log.
 *
 * Uses entry->log as the log. If the log is NULL, the entry will be
 * freed but not removed from any list, nor shall its IDs be released.
 *
 * \warning This operation is only valid on the first and last entries in the log. Deleting from
 * the middle will assert.
 */
void BM_log_entry_drop(BMLogEntry *entry);

/**
 * Undo one #BMLogEntry.
 *
 * Has no effect if there's nothing left to undo.
 */
void BM_log_undo(BMesh *bm, BMLog *log);

/**
 * Redo one #BMLogEntry.
 *
 * Has no effect if there's nothing left to redo.
 */
void BM_log_redo(BMesh *bm, BMLog *log);

/**
 * Log a vertex before it is modified.
 *
 * Before modifying vertex coordinates, masks, or hflags, call this
 * function to log its current values. This is better than logging
 * after the coordinates have been modified, because only those
 * vertices that are modified need to have their original values
 * stored.
 *
 * Handles two separate cases:
 *
 * If the vertex was added in the current log entry, update the
 * vertex in the map of added vertices.
 *
 * If the vertex already existed prior to the current log entry, a
 * separate key/value map of modified vertices is used (using the
 * vertex's ID as the key). The values stored in that case are
 * the vertex's original state so that an undo can restore the
 * previous state.
 *
 * On undo, the current vertex state will be swapped with the stored
 * state so that a subsequent redo operation will restore the newer
 * vertex state.
 */
void BM_log_vert_before_modified(BMLog *log, BMVert *v, int cd_vert_mask_offset);

/**
 * Log a new vertex as added to the #BMesh.
 *
 * The new vertex gets a unique ID assigned. It is then added to a map
 * of added vertices, with the key being its ID and the value
 * containing everything needed to reconstruct that vertex.
 */
void BM_log_vert_added(BMLog *log, BMVert *v, int cd_vert_mask_offset);

/**
 * Log a face before it is modified.
 *
 * This is intended to handle only header flags and we always
 * assume face has been added before.
 */
void BM_log_face_modified(BMLog *log, BMFace *f);

/**
 * Log a new face as added to the #BMesh.
 *
 * The new face gets a unique ID assigned. It is then added to a map
 * of added faces, with the key being its ID and the value containing
 * everything needed to reconstruct that face.
 */
void BM_log_face_added(BMLog *log, BMFace *f);

/**
 * Log a vertex as removed from the #BMesh.
 *
 * A couple things can happen here:
 *
 * If the vertex was added as part of the current log entry, then it's
 * deleted and forgotten about entirely. Its unique ID is returned to
 * the unused pool.
 *
 * If the vertex was already part of the #BMesh before the current log
 * entry, it is added to a map of deleted vertices, with the key being
 * its ID and the value containing everything needed to reconstruct
 * that vertex.
 *
 * If there's a move record for the vertex, that's used as the
 * vertices original location, then the move record is deleted.
 */
void BM_log_vert_removed(BMLog *log, BMVert *v, int cd_vert_mask_offset);

/**
 * Log a face as removed from the #BMesh.
 *
 * A couple things can happen here:
 *
 * If the face was added as part of the current log entry, then it's
 * deleted and forgotten about entirely. Its unique ID is returned to
 * the unused pool.
 *
 * If the face was already part of the #BMesh before the current log
 * entry, it is added to a map of deleted faces, with the key being
 * its ID and the value containing everything needed to reconstruct
 * that face.
 */
void BM_log_face_removed(BMLog *log, BMFace *f);

/**
 * Log all vertices/faces in the #BMesh as added.
 */
void BM_log_all_added(BMesh *bm, BMLog *log);

/** Log all vertices/faces in the #BMesh as removed. */
void BM_log_before_all_removed(BMesh *bm, BMLog *log);

/**
 * Search the log for the original vertex coordinates.
 *
 * Does not modify the log or the vertex.
 *
 * \return the pointer or nullptr if the vertex isn't found.
 */
const float *BM_log_find_original_vert_co(BMLog *log, BMVert *v);

/**
 * Search the log for the original vertex mask.
 *
 * Does not modify the log or the vertex.
 *
 * \return the pointer or nullptr if the vertex isn't found.
 */
const float *BM_log_find_original_vert_mask(BMLog *log, BMVert *v);

/** Get the logged data of a vertex (avoid multiple lookups). */
void BM_log_original_vert_data(BMLog *log, BMVert *v, const float **r_co, const float **r_no);

#if !defined(NDEBUG) || defined(WITH_GTESTS)
/** For internal use only (unit testing). */
BMLogEntry *BM_log_current_entry(BMLog *log);
#endif

#ifndef NDEBUG
/** For internal use only (unit testing) */
struct RangeTreeUInt *BM_log_unused_ids(BMLog *log);

void BM_log_print(const BMLog *log, const char *description);
void BM_log_print_entry(BMesh *bm, BMLogEntry *entry);
#endif

#ifdef WITH_GTESTS
/** One-shot staging refusal after allocations, exercising the real cleanup owners. */
void BM_log_snapshot_restore_test_fail_stage(int stage);
#endif

}  // namespace blender
