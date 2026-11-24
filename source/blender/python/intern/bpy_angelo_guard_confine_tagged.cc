/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * What an agent script set (doc/angelo/script_confinement.rst, "Output paths in data"; the re-read of f9f24cc6ffd,
 * round 3: the data decides, not the place).
 *
 * Blender's writers build an output path from data (a render's pattern and its views' suffixes, a bake's and a
 * cache's names, a node's folder and file name) and run unconfined, later. So the data is tagged where it is set:
 * - every property a confined thread sets, from Python or from C on its thread (`RNA_property_*_set`, any type, no
 *   list), by its data-block's session uid, the data's RNA type (and each base type: a writer names the most
 *   specific type its input has) and the property's identifier: a strip's `filepath` is not the render's, a node's
 *   `name` is not a File Output item's (the re-read of 0a0d4e95743, MUST-5);
 * - every data-block that gets its session uid on a confined thread when made or copied, or read (an open, append
 *   or link) from a .blend that is script-made (`BKE_lib_libblock_session_uid_ensure`): everything it holds is the
 *   script's. A person's own project a script opens stays the person's.
 * A writer judges a write when any input of its path is tagged (`BPY_angelo_guard_script_set`), and only then: a
 * person's job on the person's own data is never judged. Records live for the process. No Python: writers ask from
 * job threads.
 */

#include <cstdint>
#include <mutex>
#include <string>
#include <unordered_set>

#include "BLI_fileops.hh"

#include "DNA_ID.h"

#include "RNA_access.hh"

#include "bpy_angelo_guard_confine.hh"
#include "bpy_angelo_guard_confine_intern.hh"
#include "bpy_angelo_guard_confine_paths.hh"

namespace blender::angelo_guard {

namespace {

std::mutex tagged_lock;
std::unordered_set<uint32_t> originated;      /* Session uids of data-blocks a script made. */
std::unordered_set<std::string> properties_set; /* "<owner session uid>/<RNA type>/<identifier>". */

std::string property_key(const uint32_t owner, const char *type, const char *identifier)
{
  return std::to_string(owner) + "/" + type + "/" + identifier;
}

}  // namespace

/** Whether the .blend `source` is script-made: a confined region authored this very file (C's change record, by
 * identity and path, never by its bytes: a script's backup copy leaves the person's original theirs), or it lies
 * where Angelo's own writers may write (staging, downloads, Angelo's folders: `may_write`). A file elsewhere
 * is the person's: data a script opened, appended or linked from it is the person's, as if they had opened it (the
 * coordinator's ruling on the re-read of f9f24cc6ffd). Answered once per file a thread reads. */
bool source_is_scripts(const char *source)
{
  if (source == nullptr) {
    return true; /* Made or copied, not read: the script's own. */
  }
  /* Cached per file and its identity (a script can rewrite or move a file onto a name read before). */
  BLI_stat_t info = {};
  const bool exists = source[0] != '\0' && lookup_stat(source, &info) == 0;
  const std::string identity = std::string(source) + "|" + std::to_string(uint64_t(info.st_dev)) + "|" +
                               std::to_string(uint64_t(info.st_ino)) + "|" + std::to_string(int64_t(info.st_mtime)) +
                               "|" + std::to_string(int64_t(info.st_size));
  static thread_local std::string last_identity;
  static thread_local bool last_answer = false;
  if (exists && last_identity == identity) {
    return last_answer;
  }
  last_identity = exists ? identity : std::string();
  last_answer = source[0] != '\0' && (folders().script_wrote(source) || may_write(source));
  return last_answer;
}

void script_id_made(const unsigned int session_uid, const char *source_file)
{
  if (!source_is_scripts(source_file)) {
    return;
  }
  std::lock_guard<std::mutex> guard(tagged_lock);
  originated.insert(session_uid);
}

void script_property_set(const unsigned int owner_session_uid, const void *struct_type, const char *identifier)
{
  if (struct_type == nullptr || identifier == nullptr) {
    return;
  }
  /* The type and each base it refines (`CompositorNodeOutputFile`, ..., `Node`): a writer names the most specific
   * type its input is defined on, and a set through a less refined pointer still meets it at the shared base. */
  std::lock_guard<std::mutex> guard(tagged_lock);
  for (const StructRNA *type = static_cast<const StructRNA *>(struct_type); type != nullptr;
       type = RNA_struct_base(const_cast<StructRNA *>(type)))
  {
    properties_set.insert(property_key(owner_session_uid, RNA_struct_identifier(type), identifier));
  }
}

/** `script_set(owner_pointer, type, identifier) -> bool` and `script_originated(owner_pointer) -> bool` (an ID's
 * `as_pointer()`; `type` an RNA type's identifier): Angelo's code and its tests ask what a writer asks. */
PyObject *guard_script_set(PyObject * /*self*/, PyObject *args)
{
  unsigned long long pointer = 0;
  const char *type = nullptr;
  const char *identifier = nullptr;
  if (!PyArg_ParseTuple(args, "Kss:script_set", &pointer, &type, &identifier)) {
    return nullptr;
  }
  return PyBool_FromLong(
      BPY_angelo_guard_script_set(reinterpret_cast<const ID *>(uintptr_t(pointer)), type, identifier));
}

PyObject *guard_script_originated(PyObject * /*self*/, PyObject *arg)
{
  const unsigned long long pointer = PyLong_AsUnsignedLongLong(arg);
  if (PyErr_Occurred()) {
    return nullptr;
  }
  return PyBool_FromLong(BPY_angelo_guard_script_originated(reinterpret_cast<const ID *>(uintptr_t(pointer))));
}

}  // namespace blender::angelo_guard

namespace blender {

using namespace angelo_guard;

bool BPY_angelo_guard_script_originated(const ID *id)
{
  if (id == nullptr) {
    return false;
  }
  std::lock_guard<std::mutex> guard(tagged_lock);
  return originated.count(id->session_uid) != 0;
}

bool BPY_angelo_guard_script_set(const ID *owner, const char *type, const char *prop)
{
  if (owner == nullptr) {
    return false;
  }
  std::lock_guard<std::mutex> guard(tagged_lock);
  return originated.count(owner->session_uid) != 0 ||
         (type != nullptr && prop != nullptr &&
          properties_set.count(property_key(owner->session_uid, type, prop)) != 0);
}

}  // namespace blender
