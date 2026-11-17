/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * `sys.modules` as a confined script found it (doc/angelo/script_confinement.rst, "Work that outlives the script"):
 * nothing Angelo imports after the script resolves to a module object the script made or put there.
 *
 * - When a region ends, every name it changed is settled from what it held when the region began (the journal's
 *   earliest entry): what was there goes back; a name it gained stays only if it is an installed module under its own
 *   name (an import of installed code, whoever asked for it).
 * - Code the script left that runs outside a region (a generator resumed later) changes `sys.modules` with no region
 *   end to settle it: the same rule runs the next time the main thread runs Python (`Py_AddPendingCall`), against
 *   what each name held before its first such change.
 */

#include <Python.h>

#include "bpy_angelo_guard_confine_journal.hh"
#include "bpy_angelo_guard_confine_paths.hh"
#include "bpy_angelo_guard_confine_record.hh"

namespace blender::angelo_guard {

/** A module with no file that is installed: built into the interpreter, frozen, or a namespace package whose
 * folders are all installed. (A script's `types.ModuleType` has none of these.) */
/** An object's own instance attribute, read from its dict: no descriptor, `__getattr__` or iterator of anyone's
 * runs (this is judged at a region's end, where nothing is journaled). New reference, or null. */
static PyObject *own_attribute(PyObject *object, const char *name)
{
  PyObject *own = object != nullptr && object != Py_None ? PyObject_GenericGetDict(object, nullptr) : nullptr;
  PyObject *value = own ? Py_XNewRef(PyDict_GetItemString(own, name)) : nullptr;
  Py_XDECREF(own);
  PyErr_Clear();
  return value;
}

static bool fileless_installed(PyObject *module)
{
  PyObject *names = PyModule_GetDict(module); /* Borrowed. */
  PyObject *spec = names ? PyDict_GetItemString(names, "__spec__") : nullptr;
  PyObject *origin = own_attribute(spec, "origin");
  bool installed = origin && PyUnicode_Check(origin) &&
                   (PyUnicode_CompareWithASCIIString(origin, "built-in") == 0 ||
                    PyUnicode_CompareWithASCIIString(origin, "frozen") == 0);
  Py_XDECREF(origin);
  PyObject *path = !installed && names ? Py_XNewRef(PyDict_GetItemString(names, "__path__")) : nullptr;
  if (path != nullptr && !PyList_CheckExact(path)) {
    PyObject *places = own_attribute(path, "_path"); /* importlib's `_NamespacePath`: its own list. */
    Py_SETREF(path, places);
  }
  bool any = false, all = true;
  for (Py_ssize_t i = 0; path != nullptr && PyList_CheckExact(path) && i < PyList_GET_SIZE(path); i++) {
    PyObject *item = PyList_GET_ITEM(path, i);
    const char *text = PyUnicode_Check(item) ? PyUnicode_AsUTF8(item) : nullptr;
    any = true;
    all = all && text != nullptr && folders().trusted(text);
  }
  Py_XDECREF(path);
  PyErr_Clear();
  return installed || (any && all);
}

/** Whether a module is the agent's own: from a file outside the installed folders, or made by a script (no file):
 * its namespace is the script's, not journaled (what it leaves there is confined by provenance instead). */
bool agent_module(PyObject *module)
{
  PyObject *file = PyModule_GetFilenameObject(module);
  if (file == nullptr) {
    PyErr_Clear();
    return !fileless_installed(module);
  }
  const char *text = PyUnicode_AsUTF8(file);
  const bool agent = text == nullptr || !folders().trusted(text);
  Py_DECREF(file);
  PyErr_Clear();
  return agent;
}

/** A `sys.modules` entry a region added that may stay: a module the import system loaded from installed parts
 * (never a module object the script built or registered itself, nor one its own loader made), under its own name,
 * from the file (or built-in or frozen module) that name resolves to through installed finders. Anything else is
 * removed at the region's end, so nothing Angelo imports later resolves to what the script planted (a stand-in
 * `tarfile` whose `is_tarfile` is `shutil.rmtree`, a `None` that blocks an import, an installed module under
 * another's name, or another installed file under this one's). */
static bool module_entry_kept(PyObject *key, PyObject *value)
{
  if (value == nullptr || !PyModule_Check(value) || made_by_script(value) || module_assembled(value) ||
      agent_module(value))
  {
    return false;
  }
  PyObject *name = PyModule_GetNameObject(value);
  const bool own_name = name != nullptr && PyUnicode_Check(key) && PyUnicode_Compare(name, key) == 0;
  Py_XDECREF(name);
  PyErr_Clear();
  return own_name && origin_honest(key, value); /* And the file its name resolves to (not another's file). */
}

/** A submodule dropped from `sys.modules` leaves its package too: the attribute the import system set for it (the
 * same module object) goes, so nothing reaches it through `package.name` either. */
static void drop_from_parent(PyObject *key, PyObject *module)
{
  if (key == nullptr || module == nullptr || !PyUnicode_Check(key)) {
    return;
  }
  const Py_ssize_t dot = PyUnicode_FindChar(key, '.', 0, PyUnicode_GET_LENGTH(key), -1);
  if (dot <= 0) {
    return;
  }
  PyObject *parent_name = PyUnicode_Substring(key, 0, dot);
  PyObject *tail = PyUnicode_Substring(key, dot + 1, PyUnicode_GET_LENGTH(key));
  PyObject *modules = journal_modules();
  PyObject *parent = parent_name && modules ? PyDict_GetItemWithError(modules, parent_name) : nullptr;
  PyObject *names = parent && PyModule_Check(parent) ? PyModule_GetDict(parent) : nullptr;
  if (names != nullptr && tail != nullptr && PyDict_GetItemWithError(names, tail) == module) {
    PyDict_DelItem(names, tail);
  }
  Py_XDECREF(parent_name);
  Py_XDECREF(tail);
  PyErr_Clear();
}

/** Stands for "no entry" among what `sys.modules` held when a region began. */
PyObject *const modules_absent = Py_Ellipsis;

/** `sys.modules` back as the region found it, name by name: what it held goes back (replaced or removed); a name
 * it gained stays only if #module_entry_kept (an import of installed code), so nothing imported later resolves to a
 * module object the script made or put there. */
void modules_settle(PyObject *before, PyObject *findings, int *changed, int *noted)
{
  PyObject *modules = journal_modules();
  if (modules == nullptr) {
    return;
  }
  PyObject *key, *old;
  Py_ssize_t position = 0;
  while (PyDict_Next(before, &position, &key, &old)) {
    PyObject *now = PyDict_GetItemWithError(modules, key); /* Borrowed. */
    PyErr_Clear();
    if (now == old || (old == modules_absent && now == nullptr)) {
      continue;
    }
    if (old != modules_absent) {
      PyDict_SetItem(modules, key, old);
    }
    else if (module_entry_kept(key, now)) {
      continue;
    }
    else {
      drop_from_parent(key, now);
      PyDict_DelItem(modules, key);
    }
    PyErr_Clear();
    note(findings, modules, key, noted);
    (*changed)++;
  }
}

static PyObject *outside_before = nullptr; /* name -> what it held before code outside a region changed it. */
static bool outside_queued = false;

static int settle_outside(void * /*unused*/)
{
  outside_queued = false;
  PyObject *before = outside_before;
  outside_before = nullptr;
  if (before != nullptr && !Py_IsFinalizing()) {
    PyObject *raised = PyErr_GetRaisedException();
    int changed = 0, noted = 0;
    const bool was = outside_settling;
    outside_settling = true; /* Its own puts are not changes to note again. */
    modules_settle(before, nullptr, &changed, &noted);
    outside_settling = was;
    assembled_release();
    Py_DECREF(before);
    PyErr_Clear();
    PyErr_SetRaisedException(raised);
  }
  return 0;
}

void modules_changed_outside(PyObject *key)
{
  if (outside_before == nullptr && (outside_before = PyDict_New()) == nullptr) {
    PyErr_Clear();
    return;
  }
  if (PyDict_Contains(outside_before, key) == 0) {
    PyObject *now = PyDict_GetItemWithError(journal_modules(), key); /* Borrowed; not changed yet. */
    PyErr_Clear();
    PyDict_SetItem(outside_before, key, now ? now : modules_absent);
  }
  PyErr_Clear();
  if (!outside_queued) {
    outside_queued = Py_AddPendingCall(settle_outside, nullptr) == 0;
  }
}

}  // namespace blender::angelo_guard
