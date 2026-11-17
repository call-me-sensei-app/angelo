/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * The confinement's side of the gate at Blender's writers (`BLI_angelo_writers.hh`; doc/angelo/script_confinement.rst,
 * "Writers that read their path from data"; the re-read of f9f24cc6ffd, "the data decides, not the place").
 *
 * A writer declares the inputs its output path is built from (a property of data in a data-block) and asks before
 * each write. The write is judged when a confined script set one of those inputs, or its data-block was made,
 * appended or linked in a script region (`BPY_angelo_guard_script_set`: one lookup, no file system call), or when its
 * job began inside a script region (on a thread that ran agent code, or while a region ran: the region count here).
 * A judged write must be one Angelo's output may take for the property the path is read from
 * (`BPY_angelo_guard_output_allowed`). Nothing else is asked: a person's job on their own data writes as before.
 */

#include <Python.h>

#include <atomic>
#include <cstdio>
#include <mutex>
#include <string>
#include <unordered_map>

#include "BLI_angelo_writers.hh"

#include "DNA_ID.h"

#include "DEG_depsgraph_query.hh"

#include "RNA_access.hh"

#include "bpy_angelo_guard_confine.hh"
#include "bpy_angelo_guard_confine_intern.hh"

namespace blender {

namespace {

std::atomic<int> regions_running{0};

int writers_regions()
{
  return regions_running.load(std::memory_order_seq_cst);
}

bool writers_inside()
{
  return angelo_guard::confined_now();
}

/** The data-block a writer names, as a script set it: the original (writers on evaluated copies, a bake's
 * modifiers, a render's compositor, name the copy, which has its own session uid). */
const ID *original_of(const void *owner)
{
  return owner ? DEG_get_original_id(static_cast<const ID *>(owner)) : nullptr;
}

/** Whether the RNA type `type` has the property `prop` (its own or a base type's): a writer's declared input names
 * something a set can tag. Answered once per pair (types are registered at startup; the cache keeps the answer). */
bool declared_input_known(const char *type, const char *prop)
{
  static std::mutex lock;
  static std::unordered_map<std::string, bool> known;
  const std::string pair = std::string(type) + "." + prop;
  std::lock_guard<std::mutex> guard(lock);
  const auto found = known.find(pair);
  if (found != known.end()) {
    return found->second;
  }
  StructRNA *srna = RNA_struct_find(type);
  const bool exists = srna != nullptr && RNA_struct_type_find_property(srna, prop) != nullptr;
  if (!exists) {
    fprintf(stderr, "Angelo: a writer's input %s names no RNA property: every write built from it is judged\n",
            pair.c_str());
  }
  known.emplace(pair, exists);
  return exists;
}

bool writers_script_set(const void *owner, const char *type, const char *prop)
{
  if ((type == nullptr) != (prop == nullptr) || (type != nullptr && !declared_input_known(type, prop))) {
    return true; /* A wrong declaration over-judges, loudly; it never lets a script's set through unjudged. */
  }
  return BPY_angelo_guard_script_set(original_of(owner), type, prop);
}

bool writers_output_allowed(const char *path, const angelo_writers::Input &tag)
{
  return BPY_angelo_guard_output_allowed(path, original_of(tag.owner), tag.data, tag.prop);
}

/** A folder a writer makes, fills or removes without asking for each file. */
bool writers_folder_allowed(const char *path, const angelo_writers::Input &tag)
{
  return BPY_angelo_guard_folder_allowed(path, original_of(tag.owner), tag.data, tag.prop);
}

}  // namespace

/* Round 3's writer API (`BPY_angelo_guard_script_set`, `_script_originated`, `_output_allowed`, `_folder_allowed`)
 * is defined in bpy_angelo_guard_confine_tagged.cc and _beside.cc (the v200-confinement lane's; this branch's
 * stand-ins were removed at the merge, so writers_stubbed() is False). */

namespace angelo_guard {

void writers_region_entered()
{
  regions_running.fetch_add(1, std::memory_order_seq_cst);
}

void writers_region_left()
{
  regions_running.fetch_sub(1, std::memory_order_seq_cst);
}

/** `writers_stubbed() -> bool`: whether this build carries stand-ins for the v200 lane's judgement (the suites leave
 * the checks that need it). */
static PyObject *guard_writers_stubbed(PyObject * /*self*/, PyObject * /*args*/)
{
#ifdef WRITERS_V200_STUBS
  Py_RETURN_TRUE;
#else
  Py_RETURN_FALSE;
#endif
}

PyMethodDef writers_methods[] = {
    {"writers_stubbed", guard_writers_stubbed, METH_NOARGS,
     "writers_stubbed() -> bool: whether the writers' judgement is this branch's stand-in (v200's pending)."},
    {nullptr, nullptr, 0, nullptr},
};

void writers_install()
{
  static const angelo_writers::Gate gate = {writers_regions,
                                            writers_inside,
                                            writers_script_set,
                                            writers_output_allowed,
                                            writers_folder_allowed,
                                            WRITE};
  angelo_writers::gate_set(&gate);
}

}  // namespace angelo_guard

}  // namespace blender
