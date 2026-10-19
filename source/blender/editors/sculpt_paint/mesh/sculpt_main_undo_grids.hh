/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once

#include <cstddef>
#include <cstdint>

#include "BLI_vector.hh"

#ifdef WITH_GTESTS
#  include "BLI_array.hh"
#  include "BLI_bit_group_vector.hh"
#  include "BLI_math_vector_types.hh"
#  include <string>
#endif

namespace blender {
struct Main;

namespace ed::sculpt_paint::undo {

struct MainUndoGridState;
struct MainUndoGridRestore;

/**
 * Copy existing authoritative Sculpt grids without evaluation, writeback, normalization or source
 * mutation. Null with no error means no live grids. Every materialized owner must be a current
 * canonical viewport graph, with exactly one original SculptSession authority per Object.
 * Requires main-thread exclusive access to Main and its graph registry throughout.
 */
MainUndoGridState *main_undo_grids_capture(Main &bmain, const char **r_error);
MainUndoGridState *main_undo_grids_observe_checkpoint(Main &bmain, Span<uint32_t> selected,
                                                     size_t limit, size_t &used,
                                                     const char **r_error);
MainUndoGridState *main_undo_grids_capture_checkpoint(Main &bmain, size_t limit, size_t &used,
                                                     const char **r_error);
/** Prepared transient replay; validates all live graph destinations before allocation-free moves. */
bool main_undo_grids_replay_checkpoint(Main &bmain, MainUndoGridState &state,
                                      bool publish, const char **r_error);
void main_undo_grids_free(MainUndoGridState *state);
size_t main_undo_grids_memory_size(const MainUndoGridState &state);
bool main_undo_grids_equals(const MainUndoGridState &a, const MainUndoGridState &b);
bool main_undo_grids_has_object(const MainUndoGridState &state, uint32_t object_uid);

/** Stable owner identity and its observed session authority; contains no borrowed pointers. */
struct MainUndoGridOwnerKey {
  uint32_t object_uid;
  uint32_t mesh_uid;
  uint32_t scene_uid;
  char view_layer[64];
  /** Discovery metadata, not part of the identity or sort order. */
  bool authority;
};

/**
 * Read-only discovery for protected synchronization. Validates canonical live ownership and the
 * unique session authority, while allowing pending/time-stale graphs that must be evaluated by
 * the caller. Final capture remains strict. Requires the same exclusive access as capture.
 */
bool main_undo_grids_collect_owner_keys(Main &bmain,
                                      Vector<MainUndoGridOwnerKey> &r_owners,
                                      const char **r_error);

#ifdef WITH_GTESTS
/** The parent binds this selector to one decode; no process-global failure switch. */
struct MainUndoGridPrepareTestFault {
  uint32_t object_uid;
  uint32_t owner_index;
  bool consumed = false;
  uint32_t consumed_scene_uid = 0;
};
uint32_t main_undo_grids_test_owner_count(const MainUndoGridState &state, uint32_t object_uid);
#endif

/**
 * Validate every registered graph and its owned CCG first, then suppress old cache writeback.
 * Call only after all other read admission succeeds, immediately before a replacing MemFile read.
 * False is nonmutating. After true, any reader failure is recovery failure, never a pure refusal.
 * Also call when the target snapshot has no grid payload, and before no-flush failure cleanup.
 * Retirement follows owned evaluated Object caches only; missing original identities do not
 * prevent suppressing writeback from an otherwise proven owned cache.
 */
bool main_undo_grids_suppress_old_writeback(Main &bmain, const char **r_error);

/**
 * Requires safe empty temporary Sculpt sessions for ALL objects (no BM/log/PBVH or borrowed CCG).
 * Evaluates all required canonical owner graphs before preparing exact grid arrays and PBVH trees.
 * Evaluation/callback failures occur after Main replacement and require no-flush runtime cleanup.
 * No prepared pointer may survive another graph evaluation or Main mutation.
 */
MainUndoGridRestore *main_undo_grids_prepare(Main &bmain,
                                             const MainUndoGridState &state,
                                             const char **r_error
#ifdef WITH_GTESTS
                                             , MainUndoGridPrepareTestFault *test_fault = nullptr
#endif
);
bool main_undo_grids_restore_has_object(const MainUndoGridRestore &restore, uint32_t object_uid);
/** No evaluation, allocation or callbacks. Consumes the token; leaves Main's flush flag to caller.
 */
void main_undo_grids_commit(MainUndoGridRestore *restore);
void main_undo_grids_discard(MainUndoGridRestore *restore);

#ifdef WITH_GTESTS
/** Detached facts from the same ownership inventory as production capture; no lazy updates. */
struct MainUndoGridObservation {
  uint32_t object_uid;
  uint32_t mesh_uid;
  uint32_t scene_uid;
  uint32_t scene_original_session_uid;
  std::string view_layer;
  uintptr_t graph_address;
  uintptr_t ccg_address;
  float frame;
  float ctime;
  bool canonical;
  bool viewport;
  bool active;
  bool visibility_optimization;
  bool render_pipeline;
  bool editors_update;
  int sync_writeback;
  int debug_flags;
  bool pending_updates;
  bool authority;
  bool dirty_coords;
  bool dirty_hidden;
  bool session_needs_flush;
  int level;
  int grid_size;
  int grids_num;
  Array<int, 0> face_offsets;
  Array<int, 0> grid_to_face;
  Array<float3, 0> positions;
  Array<float3, 0> normals;
  Array<float, 0> masks;
  BitGroupVector<0> hidden;
  Array<int, 0> pending_normal_faces;
};
bool main_undo_grids_observe(Main &bmain,
                             Vector<MainUndoGridObservation> &r_observations,
                             const char **r_error);
#endif

}  // namespace ed::sculpt_paint::undo
}  // namespace blender
