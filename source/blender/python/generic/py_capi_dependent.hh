/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pygen
 *
 * Angelo: a node linking a Python wrapper to the shared wrapper of the struct that owns its
 * memory, so freeing that struct invalidates the wrapper (`bpy_rna.cc`, "Dependent Wrappers").
 */

#pragma once

#include <Python.h>

#include "MEM_lifetime.hh"

#include <cstdint>

namespace blender {

struct ID;

/** Less common watches share one optional allocation and one hot-path pointer check. */
struct BPyRNA_ExtraWatches {
  mem_lifetime::Watch runtime_type{};
  mem_lifetime::Watch detail{};
};

/**
 * A node in a shared wrapper's list of dependents (#BPy_StructRNA::dependent). A head has a null
 * `prev`; a node in no list has both null; `head` is the list a node is in. `object` is the
 * wrapper or iterator holding the node. Every change holds the GIL. A new object must start with
 * the node zeroed (`tp_alloc` zeroes; `PyObject_New` does not).
 */
struct BPyRNA_DependentLink {
  BPyRNA_DependentLink *prev;
  BPyRNA_DependentLink *next;
  /** The shared wrapper (a `BPy_StructRNA`) heading this node's list, null when unlinked. */
  PyObject *head;
  PyObject *object;
  BPyRNA_ExtraWatches *extra_watches;
  mem_lifetime::Watch data_watch;
  mem_lifetime::Watch detail_watch; /* Descriptor, or a Screen-owner transfer watch. */
  /** What `object` is, so invalidation knows what to clear. */
  enum class Kind : uint8_t {
    /** A `bpy_struct`, `bpy_prop`, `bpy_prop_array` or `bpy_func`: its pointer is cleared. */
    Pointer,
    /** A `bpy_prop_collection_iter`: the iteration ends. */
    CollectionIterator,
    /** An `IDPropertyGroup` or `IDPropertyArray`: its property is cleared. */
    IDProperty,
    /** An `IDPropertyUIManager`: its property is cleared. */
    IDPropertyUIManager,
  } kind;
  /** Mesh attributes change their native layout across edit-mode transitions. */
  bool attribute_mode_sensitive;
};

/**
 * Link `object` (holding `node`) to the shared wrapper of `owner`, made if it has none yet, so it
 * is invalidated when `owner` is freed. A null `owner` leaves it unlinked. Requires the GIL.
 */
void pyrna_dependent_attach_to_id(PyObject *object,
                                  BPyRNA_DependentLink *node,
                                  ID *owner,
                                  BPyRNA_DependentLink::Kind kind);
/** Unlink `node` if it is linked. Requires the GIL. */
void pyrna_dependent_detach(BPyRNA_DependentLink *node);

/** Consume a worker's invalidation under the GIL before touching native data. */
void pyrna_dependent_check_slow(BPyRNA_DependentLink &node);
inline void pyrna_dependent_check(BPyRNA_DependentLink &node)
{
  /* All flags are safe to read together, avoiding a branch per common flag. */
  const int stale = int(mem_lifetime::expired(node.data_watch)) |
                    int(mem_lifetime::expired(node.detail_watch)) |
                    int(node.attribute_mode_sensitive);
  if (stale ||
      (node.extra_watches && (int(mem_lifetime::expired(node.extra_watches->runtime_type)) |
                              int(mem_lifetime::expired(node.extra_watches->detail)))))
  {
    pyrna_dependent_check_slow(node);
  }
}

/** Defined in `idprop_py_api.cc` / `idprop_py_ui_api.cc`: clear an invalidated wrapper. */
void BPy_IDProperty_owner_freed(PyObject *self);
void BPy_IDPropertyUIManager_owner_freed(PyObject *self);

}  // namespace blender
