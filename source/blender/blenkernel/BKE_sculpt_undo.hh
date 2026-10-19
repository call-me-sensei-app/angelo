/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once

#include <memory>

#include "BLI_array.hh"
#include "BLI_bit_group_vector.hh"

namespace blender {

struct Mesh;
struct Main;
struct Object;
struct SubdivCCG;

/** Validate runtime ownership before any Undo decoder or original-ID release can run. */
bool BKE_sculpt_undo_runtime_preflight(const Main &bmain, const char **r_error);

/**
 * Fine Multires visibility is absent from Main's coarse mesh. Only Angelo's explicit MemFile
 * snapshots install this runtime payload. It survives repeated evaluation passes until an
 * actual hide edit or a topology/coarse-visibility change invalidates it.
 */
struct SculptUndoGridVisibility {
  uint mesh_uid;
  int modifier_uid;
  int verts_num;
  int grids_num;
  int grid_size;
  Array<int> face_offsets;
  Array<int> corner_verts;
  BitVector<> face_hidden;
  BitGroupVector<> grid_hidden;

  bool matches_mesh(const Mesh &mesh) const;
  bool matches_modifier(const Object &object) const;
  bool matches_grids(const SubdivCCG &grids) const;
  size_t memory_size() const;
};

std::unique_ptr<SculptUndoGridVisibility> BKE_sculpt_undo_grid_visibility_capture(
    const Object &object, const char **r_error);
void BKE_sculpt_undo_grid_visibility_restore(Object &object,
                                             const SculptUndoGridVisibility &visibility);

}  // namespace blender
