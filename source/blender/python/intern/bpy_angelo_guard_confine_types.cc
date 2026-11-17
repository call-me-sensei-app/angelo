/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * A class's attributes, journaled like a module's names (doc/angelo/script_confinement.rst, "Work that outlives
 * the script"). CPython raises no audit event and calls no dict watcher for `Cls.attr = value`, so
 * `type.__setattr__` itself is replaced before Python starts: every class and metaclass then inherits it, and the
 * `type.__setattr__` slot wrapper calls it too. While a thread journals, a change to a class the region did not
 * make is recorded with its old value. When the region ends it is put back: exactly for every class but Blender's
 * RNA types, where what a script registers (a property) stays and what replaces code goes back, as in a module.
 * A class the script made (`script_type`) is its own: what it set there is kept when the journal is undone. A change
 * an installed import makes (`in_installed_import`: a class statement's `__init_subclass__`, `enum`'s metaclass, a
 * decorator its top level applies) is that import's definition and stays, unless it put the script's code there; on
 * a class installed code made, what the script set afterwards goes back.
 */

#include <Python.h>

#include <cstring>
#include <vector>

#include "bpy_angelo_guard_confine_journal.hh"
#include "bpy_angelo_guard_confine_record.hh"

namespace blender::angelo_guard {

static setattrofunc original = nullptr;
static newfunc original_new = nullptr;

/* Classes made by the script's class statements in the running regions: held until the last region ends. */
static std::vector<PyObject *> statement_list;
static PointerSet statement_set;
thread_local int script_class_statement = 0;

bool script_type(PyTypeObject *type)
{
  return made_by_script((PyObject *)type) || (!statement_list.empty() && statement_set.contains(type));
}

void script_types_reset()
{
  std::vector<PyObject *> held;
  held.swap(statement_list);
  statement_set.clear();
  for (PyObject *type : held) {
    Py_DECREF(type);
  }
}

/** `type.__new__`: a class made while a class statement runs the script's body is the script's, whichever metaclass
 * (Python code, installed) made it. */
static PyObject *journaling_new(PyTypeObject *metatype, PyObject *args, PyObject *kwargs)
{
  PyObject *made = original_new(metatype, args, kwargs);
  if (made != nullptr && PyType_Check(made) && script_class_statement > 0 && journaling()) {
    statement_list.push_back(Py_NewRef(made));
    statement_set.insert(made);
  }
  return made;
}

static bool rna_type(PyTypeObject *type);

/** The module a class says it is from (`__module__` in its own dict; a static type's dotted name before the last
 * dot): borrowed from the class, or a reference held for the rest of the process for a static type. */
static PyObject *type_module(PyObject *type)
{
  PyObject *names = ((PyTypeObject *)type)->tp_dict;
  PyObject *module = names != nullptr ? PyDict_GetItemString(names, "__module__") : nullptr;
  if (module != nullptr && PyUnicode_Check(module)) {
    return module;
  }
  PyErr_Clear();
  static thread_local PyObject *last = nullptr;
  const char *name = ((PyTypeObject *)type)->tp_name;
  const char *dot = name ? strrchr(name, '.') : nullptr;
  Py_XSETREF(last, dot ? PyUnicode_FromStringAndSize(name, dot - name) : PyUnicode_FromString("builtins"));
  PyErr_Clear();
  return last;
}

static int journaling_setattro(PyObject *type, PyObject *name, PyObject *value)
{
  if ((journaling() || journaling_late()) && PyType_Check(type) && PyUnicode_Check(name)) {
    PyObject *raised = PyErr_GetRaisedException();
    PyObject *names = ((PyTypeObject *)type)->tp_dict;
    PyObject *old = names != nullptr ? PyDict_GetItemWithError(names, name) : nullptr; /* Borrowed. */
    PyErr_Clear();
    /* An installed import's own change: an extension module's native initialization (pybind11 setting its new
     * classes' methods: fixed native code, no Python frame to steer), or anything a module the import system is
     * loading afresh does while the region is unseeded (a class's `__init_subclass__`, `@dataclass`, a package
     * patching its C++ classes). Any other change (a body a script ran itself, or one after it seeded installed
     * code) is journaled and put back exactly. */
    bool definition = false;
    if (extension_initializing > 0) {
      PyFrameObject *innermost = PyEval_GetFrame(); /* Borrowed. */
      PyCodeObject *code = innermost ? PyFrame_GetCode(innermost) : nullptr;
      definition = code == nullptr || !agent_code(code);
      Py_XDECREF(code);
    }
    if (!definition) {
      /* A fresh, unseeded installed import (journal.cc) changing a class of its own package (or a Blender RNA
       * type: registering on them is its API); another package's class goes back. */
      PyObject *globals = installed_import_globals();
      definition = globals != nullptr && (rna_type((PyTypeObject *)type) ||
                                          package_of_import(globals, type_module(type)) ||
                                          writer_in_module(type_module(type)));
    }
    const bool tool = !definition && angelo_writes(type_module(type)); /* Angelo changing its own classes. */
    /* A callable that is not plain installed code put on an installed class outside an import's own definition
     * (not Blender's RNA types): the same seeding as a module's namespace (journal.cc). */
    if (!definition && !tool && !region_seeded && seeds(value) && !rna_type((PyTypeObject *)type) &&
        !script_type((PyTypeObject *)type) && !writer_in_module(type_module(type)))
    {
      PyObject *qualified = PyUnicode_FromString(((PyTypeObject *)type)->tp_name);
      note_seed(qualified, name);
      Py_XDECREF(qualified);
    }
    journal_record(type, name, old, definition, true, tool);
    PyErr_SetRaisedException(raised);
  }
  return original(type, name, value);
}

void types_install()
{
  if (original == nullptr) {
    original = PyType_Type.tp_setattro;
    PyType_Type.tp_setattro = journaling_setattro;
    original_new = PyType_Type.tp_new;
    PyType_Type.tp_new = journaling_new;
  }
}

/** Blender's RNA types (`bpy.types.*` and the classes registered on them): a script registering a property there
 * is Blender's own API. */
static bool rna_type(PyTypeObject *type)
{
  PyObject *mro = type->tp_mro;
  if (mro == nullptr || !PyTuple_Check(mro)) {
    return false;
  }
  for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(mro); i++) {
    if (strcmp(((PyTypeObject *)PyTuple_GET_ITEM(mro, i))->tp_name, "bpy_struct") == 0) {
      return true;
    }
  }
  return false;
}

bool undo_type_entry(const JournalEntry &entry, PyObject *findings, int *noted)
{
  PyTypeObject *type = (PyTypeObject *)entry.dict;
  PyObject *names = type->tp_dict;
  if (names == nullptr || entry.key == nullptr || (made_in_region((PyObject *)type) && script_type(type))) {
    return false; /* A class the script made keeps what it set on it. */
  }
  PyObject *now = PyDict_GetItemWithError(names, entry.key); /* Borrowed. */
  PyErr_Clear();
  if (entry.definition && !agent_callable(now) && !(entry.tool && made_by_script(now))) {
    return false; /* An installed import's definition (or Angelo's own tool state) stays, unless it put the script's
                   * code there (or, for Angelo's, a value the script made). */
  }
  const bool wanted = rna_type(type) ? undo_wanted(entry.key, entry.old, now) : now != entry.old;
  if (!wanted) {
    return false;
  }
  if (entry.old == nullptr && now == nullptr) {
    return false;
  }
  const int result = original((PyObject *)type, entry.key, entry.old); /* Old null: the attribute goes. */
  PyErr_Clear();
  if (result != 0) {
    return false;
  }
  PyObject *owner = PyType_GetQualName(type);
  note_finding(findings, owner, entry.key, noted);
  Py_XDECREF(owner);
  PyErr_Clear();
  return true;
}

}  // namespace blender::angelo_guard
