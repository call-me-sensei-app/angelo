/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * The modules whose functions Angelo replaced (break-it-confine-reparse M7 read, MUST-2): a second module object made
 * from one of them (`del sys.modules["posix"]; import posix as raw`) would carry every original again, so a confined
 * thread never makes one, whether or not the first is still in `sys.modules`. The list is not written by hand: every
 * install site notes the module it imported (`put_in`, the replacement table, each type slot or dict entry changed in
 * place), and every replacement also notes its original's own module and, for a heap type, its defining module; so a
 * new wrapper adds its module by being installed.
 *
 * What `_imp` is asked to make is read once, into a spec of Angelo's own (`steady_spec`): a script's spec could answer
 * one name to the check and another to the import (a property, a `__getattr__`, a patched `ModuleSpec`).
 */

#include <Python.h>

#include <cstring>
#include <mutex>
#include <string>
#include <unordered_set>
#include <vector>

#include "bpy_angelo_guard_confine_intern.hh"

namespace blender::angelo_guard {

namespace {

std::mutex owners_lock;
std::unordered_set<std::string> owner_names; /* Full module names (`posix`, `_io`, `pxr.Usd._usd`). */
std::unordered_set<std::string> init_names;  /* Their last components: what an extension's `PyInit_` is named by. */
std::unordered_set<std::string> install_sites; /* Every module an install site imported to replace something in. */
PyObject *namespace_type = nullptr;          /* `types.SimpleNamespace`, taken before any script runs. */

std::string last_component(const std::string &name)
{
  const size_t dot = name.rfind('.');
  return dot == std::string::npos ? name : name.substr(dot + 1);
}

/** The name of the module `original` belongs to, new reference or null: a module function's module, a method's or
 * slot's class's module, or what `__module__` says. */
PyObject *owner_of(PyObject *original)
{
  if (PyCFunction_Check(original)) {
    PyObject *self = PyCFunction_GET_SELF(original);
    if (self != nullptr && PyModule_Check(self)) {
      if (PyObject *name = PyModule_GetNameObject(self)) {
        return name;
      }
      PyErr_Clear();
    }
  }
  PyObject *holder = PyType_Check(original) ? Py_NewRef(original) : PyObject_GetAttrString(original, "__objclass__");
  PyErr_Clear();
  PyObject *name = PyObject_GetAttrString(holder != nullptr ? holder : original, "__module__");
  Py_XDECREF(holder);
  if (name == nullptr || !PyUnicode_Check(name)) {
    Py_XDECREF(name);
    PyErr_Clear();
    PyObject *type_name = PyObject_GetAttrString((PyObject *)Py_TYPE(original), "__module__");
    if (type_name == nullptr || !PyUnicode_Check(type_name)) {
      Py_XDECREF(type_name);
      PyErr_Clear();
      return nullptr;
    }
    return type_name;
  }
  return name;
}

/** `value` as an exact `str` (a subclass could answer differently later), new reference or null. */
PyObject *exact_text(PyObject *value)
{
  if (value == nullptr || !PyUnicode_Check(value)) {
    return nullptr;
  }
  return PyUnicode_Substring(value, 0, PyUnicode_GET_LENGTH(value)); /* Itself when exact, else a copy. */
}

/** Note the module named `name` (a module of Python source, whose file is `.py`, is not: `_imp` never makes it;
 * one not loaded is noted, as it can't be told). */
static void note_named(PyObject *name)
{
  const char *text = name && PyUnicode_Check(name) ? PyUnicode_AsUTF8(name) : nullptr;
  if (text == nullptr || text[0] == '\0') {
    PyErr_Clear();
    return;
  }
  PyObject *module = PyDict_GetItemWithError(PyImport_GetModuleDict(), name); /* Borrowed. */
  PyObject *file = module && PyModule_Check(module) ? PyObject_GetAttrString(module, "__file__") : nullptr;
  const char *file_text = file && PyUnicode_Check(file) ? PyUnicode_AsUTF8(file) : nullptr;
  const size_t file_size = file_text ? strlen(file_text) : 0;
  const bool source = file_text != nullptr &&
                      ((file_size > 3 && strcmp(file_text + file_size - 3, ".py") == 0) ||
                       (file_size > 4 && strcmp(file_text + file_size - 4, ".pyc") == 0));
  Py_XDECREF(file);
  PyErr_Clear();
  if (source) {
    return;
  }
  std::lock_guard<std::mutex> hold(owners_lock);
  owner_names.insert(text);
  if (strcmp(text, "builtins") != 0 && strcmp(text, "sys") != 0) {
    init_names.insert(last_component(text)); /* Those two are made once per interpreter, by name only. */
  }
}

}  // namespace

void note_wrapped_owner(PyObject *original)
{
  if (original == nullptr) {
    return;
  }
  PyObject *name = owner_of(original);
  note_named(name);
  Py_XDECREF(name);
  /* A heap type's defining module (`PyType_GetModule`), which its `__module__` may not name: `_sqlite3.Connection`
   * says `sqlite3`, the package of Python source around it (break-it-confine-reparse head 14, MUST-2). */
  PyObject *holder = PyType_Check(original) ? Py_NewRef(original) : PyObject_GetAttrString(original, "__objclass__");
  PyErr_Clear();
  if (holder != nullptr && PyType_Check(holder) && PyType_HasFeature((PyTypeObject *)holder, Py_TPFLAGS_HEAPTYPE)) {
    if (PyObject *module = PyType_GetModule((PyTypeObject *)holder)) { /* Borrowed. */
      if (PyObject *defining = PyModule_GetNameObject(module)) {
        note_named(defining);
        Py_DECREF(defining);
      }
    }
  }
  Py_XDECREF(holder);
  PyErr_Clear();
}

void note_install_site(const char *module_name)
{
  if (module_name == nullptr) {
    return;
  }
  {
    std::lock_guard<std::mutex> hold(owners_lock);
    install_sites.insert(module_name);
  }
  PyObject *name = PyUnicode_FromString(module_name);
  note_named(name);
  Py_XDECREF(name);
  PyErr_Clear();
}

std::vector<std::string> install_site_modules()
{
  std::lock_guard<std::mutex> hold(owners_lock);
  return std::vector<std::string>(install_sites.begin(), install_sites.end());
}

bool names_a_wrapped_module(const char *name, const bool dynamic)
{
  if (name == nullptr) {
    return false;
  }
  std::lock_guard<std::mutex> hold(owners_lock);
  if (owner_names.count(name) != 0) {
    return true;
  }
  /* An extension module is found by its file and initialized by its last name (`PyInit_<last>`): the same file
   * under another package name is the same module. */
  return dynamic && init_names.count(last_component(name)) != 0;
}

std::vector<std::string> wrapped_module_names()
{
  std::lock_guard<std::mutex> hold(owners_lock);
  return std::vector<std::string>(owner_names.begin(), owner_names.end());
}

void steady_spec_prepare()
{
  if (namespace_type != nullptr) {
    return;
  }
  PyObject *types = PyImport_ImportModule("types");
  namespace_type = types ? PyObject_GetAttrString(types, "SimpleNamespace") : nullptr;
  Py_XDECREF(types);
  if (namespace_type != nullptr && !PyType_Check(namespace_type)) {
    Py_CLEAR(namespace_type);
  }
  PyErr_Clear();
}

PyObject *steady_spec(PyObject *spec, std::string &r_name)
{
  if (spec == nullptr || namespace_type == nullptr) {
    return nullptr;
  }
  PyObject *read = PyObject_GetAttrString(spec, "name");
  PyObject *name = exact_text(read);
  Py_XDECREF(read);
  const char *text = name ? PyUnicode_AsUTF8(name) : nullptr;
  if (text == nullptr) {
    Py_XDECREF(name);
    PyErr_Clear();
    return nullptr;
  }
  r_name = text;
  PyObject *fields = PyDict_New();
  if (fields == nullptr) {
    Py_DECREF(name);
    return nullptr;
  }
  PyDict_SetItemString(fields, "name", name);
  /* What `_imp` and an extension's own module creation read besides the name; each read once, here. */
  PyObject *locations = nullptr;
  for (const char *field : {"origin", "loader", "loader_state", "submodule_search_locations"}) {
    PyObject *value = PyObject_GetAttrString(spec, field);
    PyErr_Clear();
    if (strcmp(field, "origin") == 0 && value != nullptr && value != Py_None) {
      PyObject *steady = exact_text(value);
      Py_DECREF(value);
      value = steady; /* Not text: left out, and `_imp` says what it needs. */
    }
    if (value != nullptr) {
      PyDict_SetItemString(fields, field, value);
      if (strcmp(field, "submodule_search_locations") == 0) {
        locations = value; /* Borrowed through `fields`. */
      }
      Py_DECREF(value);
    }
  }
  /* `ModuleSpec.parent`, computed from the name the check read. */
  const std::string parent = (locations == nullptr || locations == Py_None) ?
                                 (r_name.rfind('.') == std::string::npos ? std::string() :
                                                                           r_name.substr(0, r_name.rfind('.'))) :
                                 r_name;
  if (PyObject *parent_text = PyUnicode_FromStringAndSize(parent.data(), Py_ssize_t(parent.size()))) {
    PyDict_SetItemString(fields, "parent", parent_text);
    Py_DECREF(parent_text);
  }
  PyObject *empty = PyTuple_New(0);
  PyObject *steady = empty ? PyObject_Call(namespace_type, empty, fields) : nullptr;
  Py_XDECREF(empty);
  Py_DECREF(fields);
  Py_DECREF(name);
  PyErr_Clear();
  return steady;
}

}  // namespace blender::angelo_guard
