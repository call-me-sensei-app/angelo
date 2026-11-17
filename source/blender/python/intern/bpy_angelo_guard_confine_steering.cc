/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * What the import system searches, and what the interpreter calls at exit, as each outermost region found them
 * (doc/angelo/script_confinement.rst, "Work that outlives the script"). The journal puts back a rebinding of
 * `sys.path` and its kin; this puts back their contents, for every kind of region: a run (whose Python sweep also
 * does it) and a step of a generator the script left, which has no sweep. A finder a script put on `sys.meta_path`
 * would otherwise serve Angelo's own later imports, outside any region.
 *
 * - `sys.path`, `sys.meta_path`, `sys.path_hooks`: what was there, in its order, then what was added that is
 *   installed (a folder Angelo installed, a finder of an installed kind).
 * - `sys.path_importer_cache`: a replaced entry goes back; a new one stays only if it is an installed kind.
 * - `threading._threading_atexits`: an entry added stays only if it is a trampoline or installed code.
 * - each loaded package's `__path__` (a list, or a namespace package's own `_path` list): its contents as they were,
 *   so a place a script added in place doesn't steer which file a later submodule import of Angelo's loads.
 * - `threading._trace_hook` / `_profile_hook`: Angelo's own values (trace_hooks.cc).
 * No Python runs here: this is a region's end, where nothing is journaled.
 */

#include <Python.h>

#include <string>
#include <utility>
#include <vector>

#include "bpy_angelo_guard_confine_intern.hh"
#include "bpy_angelo_guard_confine_journal.hh"
#include "bpy_angelo_guard_confine_paths.hh"

namespace blender::angelo_guard {

namespace {

const char *const LISTS[] = {"path", "meta_path", "path_hooks"};
thread_local PyObject *lists_before[3] = {nullptr, nullptr, nullptr};
thread_local PyObject *cache_before = nullptr;
thread_local PyObject *exits_before = nullptr;
/* Each loaded package's search list and a copy of its contents (strong references to both). */
thread_local std::vector<std::pair<PyObject *, PyObject *>> package_paths;

/* The loaded modules whose search lists a region snapshots: the packages, and any module whose namespace the
 * journal does not watch (a change there sends no event). Borrowed: every removal from `sys.modules` sends an event
 * that marks this stale before the module can go. Rebuilt only when `sys.modules` or a `__path__` changed
 * (#steering_modules_changed), so a region reads a few dozen lists, not every loaded module. */
std::vector<PyObject *> package_modules;
bool package_modules_stale = true;

PyObject *path_key()
{
  static PyObject *key = PyUnicode_InternFromString("__path__");
  return key;
}

/** A package's search list: its `__path__` list, or a namespace path's own `_path` list (no descriptor runs). */
PyObject *package_path_list(PyObject *module)
{
  PyObject *names = PyModule_Check(module) ? PyModule_GetDict(module) : nullptr;
  PyObject *path = names != nullptr && path_key() != nullptr ? PyDict_GetItemWithError(names, path_key()) : nullptr;
  PyErr_Clear();
  if (path == nullptr || PyList_CheckExact(path)) {
    return path;
  }
  if (Py_TYPE(path)->tp_dictoffset == 0) {
    return nullptr;
  }
  PyObject *own = PyObject_GenericGetDict(path, nullptr);
  PyObject *places = own != nullptr && PyDict_CheckExact(own) ? PyDict_GetItemString(own, "_path") : nullptr;
  Py_XDECREF(own); /* `places` stays alive: `path` holds it. */
  PyErr_Clear();
  return places != nullptr && PyList_CheckExact(places) ? places : nullptr;
}

void package_paths_release()
{
  for (auto &[list, copy] : package_paths) {
    Py_DECREF(list);
    Py_DECREF(copy);
  }
  package_paths.clear();
}

PyObject *threading_exits()
{
  PyObject *modules = journal_modules(); /* Never aborts during finalization. */
  PyObject *threading = modules ? PyDict_GetItemString(modules, "threading") : nullptr; /* Borrowed. */
  PyObject *names = threading && PyModule_Check(threading) ? PyModule_GetDict(threading) : nullptr;
  PyObject *exits = names ? PyDict_GetItemString(names, "_threading_atexits") : nullptr;
  PyErr_Clear();
  return exits != nullptr && PyList_CheckExact(exits) ? exits : nullptr;
}

bool installed_entry(PyObject *entry)
{
  if (PyUnicode_Check(entry)) {
    const char *text = PyUnicode_AsUTF8(entry);
    PyErr_Clear();
    return text != nullptr && (text[0] == '\0' || folders().trusted(text));
  }
  return installed_kind(entry);
}

bool contains(PyObject *list, PyObject *item)
{
  for (Py_ssize_t i = 0; i < PyList_GET_SIZE(list); i++) {
    if (PyList_GET_ITEM(list, i) == item) {
      return true;
    }
  }
  return false;
}

/** `current` back to `before`'s entries in their order, then the ones added that `keep` accepts. */
template<typename Keep> bool settle_list(PyObject *current, PyObject *before, Keep keep)
{
  PyObject *ordered = PyList_GetSlice(before, 0, PY_SSIZE_T_MAX);
  for (Py_ssize_t i = 0; ordered != nullptr && i < PyList_GET_SIZE(current); i++) {
    PyObject *item = PyList_GET_ITEM(current, i);
    if (!contains(before, item) && keep(item)) {
      PyList_Append(ordered, item);
    }
  }
  bool same = ordered != nullptr && PyList_GET_SIZE(ordered) == PyList_GET_SIZE(current);
  for (Py_ssize_t i = 0; same && i < PyList_GET_SIZE(ordered); i++) {
    same = PyList_GET_ITEM(ordered, i) == PyList_GET_ITEM(current, i);
  }
  if (ordered != nullptr && !same) {
    PyList_SetSlice(current, 0, PyList_GET_SIZE(current), ordered);
  }
  Py_XDECREF(ordered);
  PyErr_Clear();
  return ordered != nullptr && !same;
}

void note(PyObject *findings, const char *what)
{
  if (findings != nullptr) {
    if (PyObject *line = PyUnicode_FromString(what)) {
      PyList_Append(findings, line);
      Py_DECREF(line);
    }
  }
  PyErr_Clear();
}

}  // namespace

void steering_snapshot()
{
  if (Py_IsFinalizing()) {
    return; /* The interpreter is going: nothing to keep as it was. */
  }
  steering_release();
  trace_hooks_know(); /* Once `threading` is loaded (trace_hooks.cc). */
  for (int i = 0; i < 3; i++) {
    Py_CLEAR(lists_before[i]);
    PyObject *list = PySys_GetObject(LISTS[i]); /* Borrowed. */
    lists_before[i] = list != nullptr && PyList_Check(list) ? PyList_GetSlice(list, 0, PY_SSIZE_T_MAX) : nullptr;
  }
  Py_CLEAR(cache_before);
  PyObject *cache = PySys_GetObject("path_importer_cache");
  cache_before = cache != nullptr && PyDict_Check(cache) ? PyDict_Copy(cache) : nullptr;
  Py_CLEAR(exits_before);
  PyObject *exits = threading_exits();
  exits_before = exits != nullptr ? PyList_GetSlice(exits, 0, PY_SSIZE_T_MAX) : nullptr;
  package_paths_release();
  PyObject *modules = journal_modules();
  if (modules == nullptr) {
    return;
  }
  if (package_modules_stale) {
    package_modules.clear();
    PyObject *key, *module;
    Py_ssize_t position = 0;
    while (PyDict_Next(modules, &position, &key, &module)) {
      if (PyModule_Check(module) &&
          (package_path_list(module) != nullptr || !journal_watches(PyModule_GetDict(module))))
      {
        package_modules.push_back(module);
      }
    }
    package_modules_stale = false;
  }
  for (PyObject *module : package_modules) {
    PyObject *list = package_path_list(module);
    PyObject *copy = list != nullptr ? PyList_GetSlice(list, 0, PY_SSIZE_T_MAX) : nullptr;
    if (copy != nullptr) {
      package_paths.emplace_back(Py_NewRef(list), copy);
    }
  }
  PyErr_Clear();
}

void steering_modules_changed(PyObject *dict, PyObject *key)
{
  if (package_modules_stale) {
    return;
  }
  const bool path_named = key != nullptr && (key == path_key() || (PyUnicode_Check(key) &&
                                                                     PyUnicode_GET_LENGTH(key) == 8 &&
                                                                     PyUnicode_CompareWithASCIIString(
                                                                         key, "__path__") == 0));
  if (dict == journal_modules() || key == nullptr || path_named) {
    package_modules_stale = true;
  }
}

void steering_settle(PyObject *findings)
{
  if (Py_IsFinalizing()) {
    return; /* The interpreter is going: nothing to keep as it was. */
  }
  for (int i = 0; i < 3; i++) {
    PyObject *list = PySys_GetObject(LISTS[i]);
    if (list != nullptr && PyList_Check(list) && lists_before[i] != nullptr &&
        settle_list(list, lists_before[i], installed_entry))
    {
      note(findings, i == 0 ? "sys.path" : (i == 1 ? "sys.meta_path" : "sys.path_hooks"));
    }
  }
  PyObject *cache = PySys_GetObject("path_importer_cache");
  if (cache != nullptr && PyDict_Check(cache) && cache_before != nullptr) {
    bool changed = false;
    PyObject *items = PyDict_Items(cache);
    for (Py_ssize_t i = 0; items != nullptr && i < PyList_GET_SIZE(items); i++) {
      PyObject *key = PyTuple_GET_ITEM(PyList_GET_ITEM(items, i), 0);
      PyObject *value = PyTuple_GET_ITEM(PyList_GET_ITEM(items, i), 1);
      PyObject *had = PyUnicode_CheckExact(key) ? PyDict_GetItemWithError(cache_before, key) : nullptr;
      PyErr_Clear();
      if (had == nullptr && !installed_kind(value)) {
        PyDict_DelItem(cache, key);
        changed = true;
      }
    }
    Py_XDECREF(items);
    PyObject *key, *value;
    Py_ssize_t position = 0;
    while (PyDict_Next(cache_before, &position, &key, &value)) {
      PyObject *now = PyUnicode_CheckExact(key) ? PyDict_GetItemWithError(cache, key) : nullptr;
      PyErr_Clear();
      if (now != value && PyUnicode_CheckExact(key)) {
        PyDict_SetItem(cache, key, value);
        changed = true;
      }
    }
    if (changed) {
      note(findings, "sys.path_importer_cache");
    }
  }
  PyObject *exits = threading_exits();
  if (exits != nullptr) {
    PyObject *before = exits_before != nullptr ? Py_NewRef(exits_before) : PyList_New(0);
    if (before != nullptr && settle_list(exits, before, [](PyObject *item) {
          return Py_TYPE(item) == &deferred_type || installed_function(item);
        }))
    {
      note(findings, "threading's exit list");
    }
    Py_XDECREF(before);
  }
  bool paths_changed = false;
  for (auto &[list, copy] : package_paths) {
    bool same = PyList_GET_SIZE(list) == PyList_GET_SIZE(copy);
    for (Py_ssize_t i = 0; same && i < PyList_GET_SIZE(copy); i++) {
      same = PyList_GET_ITEM(list, i) == PyList_GET_ITEM(copy, i);
    }
    if (!same) {
      PyList_SetSlice(list, 0, PyList_GET_SIZE(list), copy);
      paths_changed = true;
    }
  }
  if (paths_changed) {
    note(findings, "a package's __path__");
  }
  if (trace_hooks_put_back()) {
    note(findings, "threading's tracer for new threads");
  }
  PyErr_Clear();
}

void steering_release()
{
  for (int i = 0; i < 3; i++) {
    Py_CLEAR(lists_before[i]);
  }
  Py_CLEAR(cache_before);
  Py_CLEAR(exits_before);
  package_paths_release();
}

}  // namespace blender::angelo_guard
