/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

#include <cstdint>
#include <memory>

#include "RNA_types.hh"

namespace blender::angelo_read_traversal {

enum class Status { OK, INVALID_STORAGE, UNSUPPORTED };
using Family = uint16_t;

/* Internal source-bound families, never model-supplied transport values. */
inline constexpr Family NONE = 0;
inline constexpr Family VIEW_LAYER_ROOT = 1;
inline constexpr Family VIEW_LAYER_ACTIVE_COLLECTION = 2;
inline constexpr Family VIEW_LAYER_ACTIVE_OBJECT = 3;
inline constexpr Family POSE_BONE = 4;
inline constexpr Family LAYER_CHILDREN = 101;
inline constexpr Family VIEW_LAYER_OBJECTS = 102;
inline constexpr Family VIEW_LAYER_SELECTED_OBJECTS = 103;
inline constexpr Family MODIFIER_WARNINGS = 104;

Family pointer_family(const StructRNA &type, const PropertyRNA &property);
Family collection_family(const StructRNA &type, const PropertyRNA &property);

/** The view-layer families' built-in getters first resynchronize an out-of-date view layer
 * (BKE_view_layer_synced_ensure), which changes it. For a synchronized layer that call does
 * nothing, so the core runs the built-in getter only when every view layer of the owning scene
 * is synchronized, and otherwise withholds the value. They have no native replacement here. */
bool is_view_layer_family(Family family);
/** Whether any of this declaration's read callbacks is one this module catalogues. */
bool catalogued(const PropertyRNA &property);

struct Item {
  PointerRNA pointer;
  Family family = NONE;
  int64_t index = -1;
};

/**
 * Native replacements for PoseBone.bone (whose getter rebuilds the pose's bone indices) and
 * NodesModifier.node_warnings (whose iterator reduces the modifier's live evaluation log).
 * Borrows returned below live only through this operation's Scope. Core must consume the
 * selected descendants and copy their values, then destroy Scope before allocating Python output.
 */
class Scope {
 public:
  Scope();
  ~Scope();
  Scope(const Scope &) = delete;
  Scope &operator=(const Scope &) = delete;

 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
  friend struct ScopeAccess;
};

Status pointer(Scope &scope,
               Family family,
               const PointerRNA &owner,
               const PropertyRNA &property,
               PointerRNA &result);
Status length(Scope &scope,
              Family family,
              const PointerRNA &owner,
              const PropertyRNA &property,
              int64_t &result);
Status item(Scope &scope,
            Family family,
            const PointerRNA &owner,
            const PropertyRNA &property,
            int64_t index,
            Item &result);

}  // namespace blender::angelo_read_traversal
