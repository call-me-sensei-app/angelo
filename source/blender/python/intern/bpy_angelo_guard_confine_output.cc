/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * What a confined script may set in a string on data (doc/angelo/script_confinement.rst, "Output paths in data";
 * the re-read of f9f24cc6ffd, round 3: the data decides, not the place).
 *
 * Every string a confined thread sets is tagged as the script's where it is set (`RNA_property_string_set`,
 * tagged.cc), and a writer that builds an output path from tagged data judges what it writes
 * (`BPY_angelo_guard_output_allowed`). So a path property is set as asked (relinking a texture, pointing a render at
 * a folder) and nothing is refused here. Any other string (a name, a view's suffix, a cache's name) a writer may
 * join into a path, so it may only lead down from where it is joined: never a `..` part, a leading separator, a
 * drive (`C:`) or a share (`\\server`). A `/` inside a name stays allowed: joined after a folder it can only go
 * down, and the write is judged anyway, its name being the script's.
 */

#include <cctype>
#include <string>

#include "BLI_utildefines.hh"

#include "RNA_access.hh"
#include "RNA_prototypes.hh"

#include "bpy_angelo_guard_confine.hh"
#include "bpy_angelo_guard_confine_intern.hh"

namespace blender {

using namespace angelo_guard;

namespace {

/** Whether `text`, joined after a folder, could name a place outside it. */
bool leaves_its_folder(const std::string &text)
{
  if (text.empty()) {
    return false;
  }
  if (text[0] == '/' || text[0] == '\\' || (text.size() >= 2 && isalpha((unsigned char)text[0]) && text[1] == ':')) {
    return true; /* Absolute, root-relative, a share, a drive (`C:` alone is that drive's own current folder). */
  }
  size_t start = 0;
  while (start <= text.size()) {
    size_t end = text.find_first_of("/\\", start);
    end = end == std::string::npos ? text.size() : end;
    if (text.compare(start, end - start, "..") == 0) {
      return true;
    }
    start = end + 1;
  }
  return false;
}

}  // namespace

const char *BPY_angelo_guard_output_path_refusal(const PointerRNA *ptr, PropertyRNA *prop, const char *value)
{
  /* No thread confined (an atomic load) first, the type tests next, the thread last: every string an RNA call is given
   * asks here (the Head 21 read's IC-1, Ruling 17). */
  if (!BLI_fileops_confine_possible() || ptr == nullptr || value == nullptr || value[0] == '\0') {
    return nullptr;
  }
  if (ptr->type != nullptr &&
      (RNA_struct_is_a(ptr->type, RNA_OperatorProperties) || RNA_struct_is_a(ptr->type, RNA_Operator) ||
       RNA_struct_is_a(ptr->type, RNA_GizmoProperties)))
  {
    return nullptr; /* An operator's own arguments: judged by what the operator does (an import reads them). */
  }
  if (ELEM(RNA_property_subtype(prop), PROP_FILEPATH, PROP_DIRPATH)) {
    return nullptr; /* A path: tagged as the script's; writers judge what they write from it. */
  }
  return leaves_its_folder(value) && confined_now() ? WRITE : nullptr;
}

}  // namespace blender
