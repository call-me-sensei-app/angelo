/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Planted values (bpy_angelo_guard_confine_journal.cc): a module a confined script imported may copy, as it defines
 * itself, a value the script rebound elsewhere (`os.path.join` rebound, then `from os.path import join`). When the
 * journal is undone, such a copy gets the original too.
 */

#include <Python.h>

#include <utility>
#include <vector>

#include "bpy_angelo_guard_confine_journal.hh"

namespace blender::angelo_guard {

static bool same_key(PyObject *a, PyObject *b)
{
  return a == b || (PyUnicode_CheckExact(a) && PyUnicode_CheckExact(b) && PyUnicode_Compare(a, b) == 0);
}

/** Values the script planted: every value it bound, during the run, to a name the undo puts back, mapped to that
 * name's value before the run. A module the script imported may have copied one into its own namespace as it
 * defined itself (`os.path.join` rebound, then a module imported that runs `from os.path import join`): that copy
 * gets the original too. Pairs hold references. */
static std::vector<std::pair<PyObject *, PyObject *>> planted_values(size_t mark)
{
  std::vector<JournalEntry> &journal = journal_entries();
  std::vector<size_t> rebinds;
  for (size_t i = mark; i < journal.size(); i++) {
    if (journal[i].key != nullptr && !journal[i].definition && !journal[i].type_entry) {
      rebinds.push_back(i);
    }
  }
  std::vector<std::pair<PyObject *, PyObject *>> planted;
  for (size_t a = 0; a < rebinds.size(); a++) {
    const JournalEntry &first = journal[rebinds[a]];
    bool seen = false; /* Only the first entry of each name: it holds the value before the run. */
    for (size_t b = 0; b < a && !seen; b++) {
      seen = journal[rebinds[b]].dict == first.dict && same_key(journal[rebinds[b]].key, first.key);
    }
    if (seen || first.old == nullptr) {
      continue;
    }
    std::vector<PyObject *> bound; /* Borrowed: what later entries replaced, and what is there now. */
    for (size_t b = a + 1; b < rebinds.size(); b++) {
      const JournalEntry &later = journal[rebinds[b]];
      if (later.dict == first.dict && same_key(later.key, first.key) && later.old != nullptr) {
        bound.push_back(later.old);
      }
    }
    PyObject *now = PyDict_GetItemWithError(first.dict, first.key);
    PyErr_Clear();
    if (now != nullptr) {
      bound.push_back(now);
    }
    for (PyObject *value : bound) {
      if (value != first.old && undo_wanted(first.key, first.old, value)) {
        planted.emplace_back(Py_NewRef(value), Py_NewRef(first.old));
      }
    }
  }
  return planted;
}

/** A name a module bound to itself during the run (`definition`) that holds a planted value gets the original. */
void replace_planted(size_t mark, PyObject *findings, int *changed, int *noted)
{
  std::vector<JournalEntry> &journal = journal_entries();
  std::vector<std::pair<PyObject *, PyObject *>> planted = planted_values(mark);
  if (planted.empty()) {
    return;
  }
  for (size_t i = mark; i < journal.size(); i++) {
    const JournalEntry &entry = journal[i];
    if (!entry.definition || entry.key == nullptr) {
      continue;
    }
    PyObject *now = PyDict_GetItemWithError(entry.dict, entry.key);
    PyErr_Clear();
    for (const auto &[value, original] : planted) {
      if (now != nullptr && now == value) {
        PyDict_SetItem(entry.dict, entry.key, original);
        note(findings, entry.dict, entry.key, noted);
        (*changed)++;
        break;
      }
    }
  }
  PyErr_Clear();
  for (auto &[value, original] : planted) {
    Py_DECREF(value);
    Py_DECREF(original);
  }
}

}  // namespace blender::angelo_guard
